//! PyO3 bindings exposing `template-core` as the `_native` extension module.
//!
//! This is the only crate permitted to depend on Python. The seam traits
//! defined in `template-core` are implemented here over `PyObject`.

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyStopIteration};
use pyo3::prelude::*;
use pyo3::types::PyTuple;

// Aliased to keep the call site readable: `CoreLexer` names the crate's lexer,
// as opposed to anything Python-side that might later be called a lexer.
use template_core::lexer::Lexer as CoreLexer;
use template_core::parser::desc::{AttrValue, NodeDesc, NodeListDesc, TokenDesc, VariableDesc};
use template_core::parser::{Error, ErrorKind, NoHost, Parser as CoreParser};
use template_core::token::{Token, TokenType};

/// Every built-in tag command Rust owns a compile function for, ascending.
///
/// Exposed because the cross-check lives where both languages exist: Django's
/// registry is only readable from Python, and a Rust test cannot see `.django`.
/// The list is the `REGISTRY` table in `template_core::parser::registry`, which
/// is written once and in full (§10.4) — a name missing here is a built-in tag
/// this engine would report as unknown.
#[pyfunction]
fn builtin_tag_names() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_names()
}

/// Engine version, so the Python shell can assert it is talking to the build it
/// expects rather than a stale `.so` left in the tree.
#[pyfunction]
fn engine_version() -> &'static str {
    template_core::version()
}

/// Every code point `pyunicode::is_space` accepts, ascending.
///
/// Exposed because the parity test lives where both languages exist.
/// `is_space` inherits Rust's `char::is_whitespace`, whose Unicode version is
/// the toolchain's, while Python's `str.isspace()` follows CPython's; nothing
/// in `cargo test` can compare the two, so the test that does has to call in.
///
/// A table rather than a per-code-point `is_space(c: &str)` predicate on
/// purpose. A Python `str` may hold a lone surrogate, which answers `False` but
/// which a Rust `&str` cannot represent, so a predicate would raise at all 2048
/// of them instead of returning; `char::from_u32` returns `None` for them, so
/// they drop out here without a special case.
#[pyfunction]
fn whitespace_codepoints() -> Vec<u32> {
    (0..=0x10FFFF)
        .filter(|&code| char::from_u32(code).is_some_and(template_core::pyunicode::is_space))
        .collect()
}

/// Split template-tag contents into arguments.
///
/// Raises `StopIteration` where Django's implementation would — its inner
/// `next(bits)` on an exhausted `smart_split` generator — so the shell's
/// `Token.split_contents` has identical error behaviour rather than needing to
/// translate a Rust error type.
#[pyfunction]
fn split_contents(contents: &str) -> PyResult<Vec<String>> {
    // The token type and position are irrelevant to splitting; only `contents`
    // is read. Constructing one keeps the logic in `Token` rather than
    // duplicating it here.
    let token = Token {
        token_type: TokenType::Block,
        contents: contents.to_string(),
        lineno: 1,
        position: None,
    };

    token
        .split_contents()
        .map_err(|_| PyStopIteration::new_err("unterminated translation marker"))
}

/// One lexed token as it crosses into Python: token type discriminant,
/// contents, line number, and character-index span (or `None` when lexed
/// without `debug`).
///
/// Named rather than written inline because `clippy::type_complexity` rejects
/// the anonymous tuple — the same lint that caught the debug test helper in
/// `template-core`'s lexer module.
type LexedToken = (u8, String, usize, Option<(usize, usize)>);

/// Lex `source`, returning `(token_type, contents, lineno, position)` tuples.
///
/// The token type is an `int` matching `django.template.base.TokenType`'s
/// discriminants, and `position` holds **character** indices (or `None` for the
/// non-debug lexer).
#[pyfunction]
#[pyo3(signature = (source, debug = false))]
fn lex(source: &str, debug: bool) -> Vec<LexedToken> {
    let mut lexer = if debug {
        CoreLexer::debug(source)
    } else {
        CoreLexer::new(source)
    };

    lexer
        .tokenize()
        .into_iter()
        .map(|token| {
            (
                token.token_type as u8,
                token.contents,
                token.lineno,
                token.position,
            )
        })
        .collect()
}

/// An opaque handle to a host-owned value carried inside a descriptor.
///
/// `template-core` stores the index and never reads it. The shell resolves it
/// through the `resolve_ref` callback passed to `materialise`; M3's
/// `FilterExpression` is the first real producer. Constructible from Python so
/// that a test can hand-build a descriptor, which is the only reason this is a
/// `#[pyclass]` with a `#[new]` rather than an opaque capsule.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct NodeRef {
    #[pyo3(get)]
    pub index: u64,
}

#[pymethods]
impl NodeRef {
    #[new]
    fn new(index: u64) -> Self {
        Self { index }
    }

    fn __repr__(&self) -> String {
        format!("<NodeRef {}>", self.index)
    }
}

/// A string that is already safe (`django.utils.safestring.SafeData`).
///
/// It rides on the wire rather than a plain `str` because `{% verbatim %}`'s
/// content is a `SafeString` computed at **parse** time (Review Focus item 8),
/// and a plain `str` would be escaped when the node finally renders. The shell
/// converts it with `mark_safe`; Rust does not import Django to do so.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct SafeStr {
    #[pyo3(get)]
    pub text: String,
}

#[pymethods]
impl SafeStr {
    #[new]
    fn new(text: String) -> Self {
        Self { text }
    }

    fn __repr__(&self) -> String {
        format!("<SafeStr {:?}>", self.text)
    }
}

create_exception!(
    _native,
    ParseError,
    PyException,
    "A template parse failure raised by `_native.parse`.\n\
     \n\
     Carries `message` and `token`; the Python shell catches it and raises\n\
     `django.template.exceptions.TemplateSyntaxError` with the same message and\n\
     a real `Token` object, which is design decision 2: Rust owns the message,\n\
     Python owns the exception object that `assertRaisesMessage` and\n\
     `Template.get_exception_info` inspect."
);

create_exception!(
    _native,
    UnimplementedTag,
    ParseError,
    "A built-in tag this engine does not implement yet.\n\
     \n\
     A **subclass** of `ParseError`, so every existing `except ParseError` and\n\
     the shell's conversion to `TemplateSyntaxError` are unchanged; the subclass\n\
     exists so a parity harness can bucket an outcome as \"not implemented here\"\n\
     rather than \"errored differently\". That distinction cannot be recovered\n\
     from the message, and it must not be: Django would compile `{% for %}`\n\
     successfully, so our `Unimplemented` is a statement about the engine, not a\n\
     verdict on the template, and a harness comparing messages would score it as\n\
     agreeing with Django's \"Did you forget to register or load this tag?\" — the\n\
     opposite of the truth."
);

/// A token's wire shape, shared with `_native.lex`.
type WireToken = (u8, String, usize, Option<(usize, usize)>);

fn token_to_py(py: Python<'_>, token: &TokenDesc) -> PyResult<Py<PyAny>> {
    let wire: WireToken = (
        token.token_type as u8,
        token.contents.clone(),
        token.lineno,
        token.position,
    );
    Ok(wire.into_pyobject(py)?.into_any().unbind())
}

fn attr_value_to_py(py: Python<'_>, value: &AttrValue) -> PyResult<Py<PyAny>> {
    Ok(match value {
        AttrValue::Str(s) => s.as_str().into_pyobject(py)?.into_any().unbind(),
        AttrValue::SafeStr(s) => Py::new(py, SafeStr::new(s.clone()))?.into_any(),
        // `bool`'s `IntoPyObject` yields a `Borrowed` (Python's `True`/`False`
        // are singletons), which must be owned before `into_any` can consume it.
        AttrValue::Bool(b) => (*b).into_pyobject(py)?.to_owned().into_any().unbind(),
        AttrValue::Int(i) => (*i).into_pyobject(py)?.into_any().unbind(),
        // A Python `float`, which is what Django's `Variable.literal` holds for
        // the `float(var)` branch — `1e400` included, because `f64` inf is
        // Python's inf.
        AttrValue::Float(f) => (*f).into_pyobject(py)?.into_any().unbind(),
        AttrValue::BigInt(text) => {
            // `builtins.int(text)`, **never** `py.eval`. The text is a validated
            // decimal produced by `pynum::python_int` — an optional ASCII sign
            // then ASCII digits — so the only thing it can denote is a number,
            // and Python's own `int()` is exact and arbitrary-precision, which
            // is the whole reason the variant exists. The tempting
            // `py.eval("int(%s)" % text)` would accept the text as *code*, which
            // no lint catches; this form cannot, whatever the text says.
            py.import("builtins")?
                .getattr("int")?
                .call1((text.as_str(),))?
                .unbind()
        }
        AttrValue::None => py.None(),
        AttrValue::Ref(r) => Py::new(py, NodeRef::new(r.0))?.into_any(),
    })
}

/// A `Variable` as it crosses the wire.
///
/// This is `VariableDesc` as Python sees it, and it is what the materialiser
/// dispatches on: `isinstance(value, _native.Variable)` is how a descriptor
/// attribute becomes a real `django.template.base.Variable`. The four readable
/// attributes are Django's four parse-time slots. The fifth, `message_context`,
/// is set to `None` by `Variable.__init__` and never assigned during a parse, so
/// it is not on the wire — the materialiser sets it, which is where Django sets
/// it too.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct Variable {
    #[pyo3(get)]
    pub var: String,
    /// Already converted to the Python object Django would hold: an `int` (of
    /// any size), a `float`, or a `SafeString`-to-be (`SafeStr`, which the shell
    /// turns into `mark_safe`). Converting here keeps `_decode_value` from
    /// needing to know which wire value means which Django type.
    #[pyo3(get)]
    pub literal: Py<PyAny>,
    #[pyo3(get)]
    pub lookups: Option<Vec<String>>,
    #[pyo3(get)]
    pub translate: bool,
}

#[pymethods]
impl Variable {
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let literal = self.literal.bind(py).repr()?.extract::<String>()?;
        Ok(format!(
            "<Variable {:?} literal={literal} lookups={:?} translate={}>",
            self.var, self.lookups, self.translate,
        ))
    }
}

/// A [`VariableDesc`] as Python sees it.
///
/// `pub` rather than private for the same reason `attr_value_to_py` is: the two
/// are mutually recursive, because a `Variable`'s literal is itself an
/// `AttrValue`. Task 4's `AttrValue::Variable` arm is the other caller.
pub fn variable_desc_to_py(py: Python<'_>, desc: &VariableDesc) -> PyResult<Py<PyAny>> {
    let literal = match &desc.literal {
        Some(value) => attr_value_to_py(py, value)?,
        None => py.None(),
    };
    Ok(Py::new(
        py,
        Variable {
            var: desc.var.clone(),
            literal,
            lookups: desc.lookups.clone(),
            translate: desc.translate,
        },
    )?
    .into_any())
}

/// A `(name, value)` pair as a 2-tuple.
fn pair_to_py(py: Python<'_>, name: &str, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
    Ok(
        PyTuple::new(py, [name.into_pyobject(py)?.into_any().unbind(), value])?
            .into_any()
            .unbind(),
    )
}

fn node_desc_to_py(py: Python<'_>, desc: &NodeDesc) -> PyResult<Py<PyAny>> {
    let attrs = desc
        .attrs
        .iter()
        .map(|(name, value)| pair_to_py(py, name, attr_value_to_py(py, value)?))
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    let nodelists = desc
        .nodelists
        .iter()
        .map(|(name, nodelist)| pair_to_py(py, name, nodelist_desc_to_py(py, nodelist)?))
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    let token = match &desc.token {
        Some(token) => token_to_py(py, token)?,
        None => py.None(),
    };

    Ok(PyTuple::new(
        py,
        [
            desc.class_name
                .as_str()
                .into_pyobject(py)?
                .into_any()
                .unbind(),
            PyTuple::new(py, attrs)?.into_any().unbind(),
            PyTuple::new(py, nodelists)?.into_any().unbind(),
            token,
        ],
    )?
    .into_any()
    .unbind())
}

fn nodelist_desc_to_py(py: Python<'_>, nodelist: &NodeListDesc) -> PyResult<Py<PyAny>> {
    let nodes = nodelist
        .nodes
        .iter()
        .map(|node| node_desc_to_py(py, node))
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    Ok(PyTuple::new(
        py,
        [
            PyTuple::new(py, nodes)?.into_any().unbind(),
            nodelist
                .contains_nontext
                .into_pyobject(py)?
                .to_owned()
                .into_any()
                .unbind(),
        ],
    )?
    .into_any()
    .unbind())
}

/// Translate a parse failure into the Python exception the shell will re-raise.
///
/// The token rides as an attribute on the exception **instance** rather than
/// through a `#[pyclass(extends = PyException)]` with a custom `__new__`: every
/// `BaseException` instance has a `__dict__`, so this needs no extra class and
/// `e.token` is readable exactly as it will be on the shell's
/// `TemplateSyntaxError`.
fn parse_error(py: Python<'_>, error: &Error) -> PyResult<PyErr> {
    let err = match &error.kind {
        // Django reaches `next(bits)` on an exhausted iterator and lets
        // `StopIteration` out of `parse` — only `Exception` is caught, to attach
        // the token. A bare `StopIteration` has no arguments and so an empty
        // `str()`, which is what `PyStopIteration::new_err(())` produces; giving
        // it a message would be a gratuitous difference from Django at the one
        // place `assertRaises` is used instead of `assertRaisesMessage`.
        ErrorKind::UnterminatedMarker => PyStopIteration::new_err(()),
        // Its own class, so the "this engine has not built it yet" bucket is
        // visible at the FFI. See the exception's docstring for why the message
        // cannot carry that.
        ErrorKind::Unimplemented(_) => UnimplementedTag::new_err(error.message()),
        _ => ParseError::new_err(error.message()),
    };
    let token = match &error.token {
        Some(token) => token_to_py(py, token)?,
        None => py.None(),
    };
    err.value(py).setattr("token", token)?;
    Ok(err)
}

/// Every code point Python's `unicodedata` reports as category `Nd`, ascending.
///
/// Exposed for the same reason `whitespace_codepoints` is: the comparison can
/// only run where both languages exist, because a Rust `char` cannot hold a
/// surrogate and CPython's table is not readable from Rust. Unlike the
/// whitespace table — which *inherits* `char::is_whitespace`'s Unicode version —
/// this one is a literal table inside `pynum.rs`, so the two can only move
/// independently: a Python upgrade changes CPython's `unicodedata`, and nothing
/// in this repository pins either.
#[pyfunction]
fn decimal_digit_codepoints() -> Vec<u32> {
    template_core::pynum::decimal_digit_codepoints()
}

/// `Variable.__init__` for a token the caller already knows is a variable.
///
/// A separate entry point from `parse` rather than a flag on it: this is the
/// function Task 5's `parse_filter_expression` and Task 6's materialiser tests
/// drive directly, and the Python shell never calls it on a template's behalf
/// (§5.5 — the shell materialises what Rust parsed, it does not re-parse).
#[pyfunction]
fn parse_variable(py: Python<'_>, raw: &str) -> PyResult<Py<PyAny>> {
    match template_core::parser::variable::parse_variable(raw) {
        Ok(desc) => variable_desc_to_py(py, &desc),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

/// Parse `source` into a node descriptor tree.
///
/// `debug` selects Django's `DebugLexer`, so token positions are recorded.
/// `origin_template_repr` is Python's `repr()` of `Origin.template_name`, or
/// `None` when the origin has no template name; it arrives pre-rendered because
/// a Python `repr` is Python's to produce — the rule §5.2 already records for
/// `LookupError::Missing::current_repr`.
#[pyfunction]
#[pyo3(signature = (source, debug = false, origin_template_repr = None))]
fn parse(
    py: Python<'_>,
    source: &str,
    debug: bool,
    origin_template_repr: Option<String>,
) -> PyResult<Py<PyAny>> {
    let tokens = if debug {
        CoreLexer::debug(source).tokenize()
    } else {
        CoreLexer::new(source).tokenize()
    };

    let mut parser = CoreParser::new(tokens, &NoHost);
    if let Some(repr) = origin_template_repr {
        parser = parser.with_origin_template_repr(repr);
    }

    match parser.parse(&[]) {
        Ok(nodelist) => nodelist_desc_to_py(py, &nodelist),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(builtin_tag_names, m)?)?;
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(whitespace_codepoints, m)?)?;
    m.add_function(wrap_pyfunction!(decimal_digit_codepoints, m)?)?;
    m.add_function(wrap_pyfunction!(split_contents, m)?)?;
    m.add_function(wrap_pyfunction!(lex, m)?)?;
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(parse_variable, m)?)?;
    m.add_class::<NodeRef>()?;
    m.add_class::<SafeStr>()?;
    m.add_class::<Variable>()?;
    m.add("ParseError", m.py().get_type::<ParseError>())?;
    m.add("UnimplementedTag", m.py().get_type::<UnimplementedTag>())?;
    Ok(())
}
