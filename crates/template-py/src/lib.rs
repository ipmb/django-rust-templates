//! PyO3 bindings exposing `template-core` as the `_native` extension module.
//! The only crate that may depend on Python; `template-core`'s seam traits are
//! implemented here over `PyObject`.

use pyo3::basic::CompareOp;
use pyo3::create_exception;
use pyo3::exceptions::{
    PyAttributeError, PyException, PyIndexError, PyKeyError, PyNotImplementedError, PyRuntimeError,
    PyStopIteration, PyTypeError, PyUnicodeDecodeError, PyValueError, PyZeroDivisionError,
};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple, PyType};
use std::cell::{Cell, RefCell};
use std::rc::Rc;

// The filter seam: `FilterHost`, the value interner, and
// `FilterExpression.resolve`'s loop — a self-contained subsystem with one
// entry point.
mod filters;

// `CoreLexer` names the crate's lexer, not anything Python-side.
use template_core::lexer::Lexer as CoreLexer;
use template_core::parser::desc::{
    AttrValue, FilterExprDesc, FilterVar, IfConditionDesc, NodeDesc, NodeListDesc, TokenDesc,
    VariableDesc, HOST_NODE, TEXT_NODE,
};
// This crate's own `NodeRef` is the Python-facing handle a descriptor carries;
// the resolver's handles are `template-core`'s and are never the same type.
use template_core::parser::desc::NodeRef as CoreNodeRef;
use template_core::parser::resolve::{
    resolve_lookup, CallArm, CallFailure, LookupOutcome, LookupTarget, PendingFailure, Step,
};
use template_core::parser::{Error, ErrorKind, Host, Parser as CoreParser};
use template_core::pynum::IntValue;
use template_core::token::{Token, TokenType};

/// Every command name in Rust's tag registry, ascending, **stubs included**.
///
/// The `REGISTRY` keys, not the tags Rust implements — subtract `builtin_tag_stub_names()` for those. A
/// name missing here is a built-in this engine reports as unknown. Exposed because Django's registry is
/// only readable from Python, so the cross-check test must call in.
#[pyfunction]
fn builtin_tag_names_including_stubs() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_names_including_stubs()
}

/// The registry's names whose compile function is **still a milestone stub**, ascending — derived by asking
/// each compile function, not from a second list. `tests/test_tag_inventory.py` subtracts this unfinished set
/// from the done tags.
#[pyfunction]
fn builtin_tag_stub_names() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_stub_names()
}

/// Every **library** tag this engine compiles, ascending — the Rust library registry's names, all 18.
///
/// Exposed because the shell cannot derive it: `ShellHost._rust_owned` must name the library tags Rust owns
/// to answer `has_tag` for them, and the `django.templatetags` tag tables hold all 18.
#[pyfunction]
fn library_tag_names() -> Vec<&'static str> {
    template_core::parser::registry::library_tag_names()
}

/// Engine version, so the shell can assert it is talking to the build it expects rather than a stale `.so`
/// left in the tree.
#[pyfunction]
fn engine_version() -> &'static str {
    template_core::version()
}

/// Every built-in filter this engine implements, ascending. `tests/test_filter_inventory.py`
/// compares this against Django's registry, which is only readable from Python.
#[pyfunction]
fn filter_names() -> Vec<&'static str> {
    template_core::filters::filter_names()
}

/// The built-in filters whose body carries Django's `@stringfilter`, ascending.
///
/// A *structural* fact about the body, not a `Library.register` attribute, so it is not folded into
/// `filter_flags`. `tests/test_filter_inventory.py` derives the 29 from the pinned `defaultfilters.py` AST
/// and compares. A filter wrongly marked `stringfilter` only misbehaves on a lazy `__proxy__` input, which
/// no corpus row supplies.
#[pyfunction]
fn stringfilter_filters() -> Vec<&'static str> {
    template_core::filters::stringfilter_names()
}

/// `(is_safe, needs_autoescape, expects_localtime)` for one built-in filter.
///
/// One tuple because the three flags are compared together. The absence Django distinguishes — `cut` and
/// `escapejs` have no `is_safe` attribute at all — is invisible here: this returns the `bool`
/// `getattr(f, "is_safe", False)` yields.
///
/// A name that is not a built-in filter is a `KeyError`, not three `False`s, which would be a
/// plausible-looking answer for a typo.
#[pyfunction]
fn filter_flags(name: &str) -> PyResult<(bool, bool, bool)> {
    let entry = template_core::filters::filter(name)
        .ok_or_else(|| PyKeyError::new_err(format!("`{name}` is not a built-in filter")))?;
    Ok((
        entry.flags.is_safe,
        entry.flags.needs_autoescape,
        entry.flags.expects_localtime,
    ))
}

/// Run one built-in filter over one value — `FilterExpression.resolve`'s loop, and the only entry point the
/// corpus and the shell use. `autoescape` and `use_tz` come from the context (§D1).
/// `args` is the positional argument list, **not** an `Option`: Django calls `func(obj, *arg_vals)`
/// (`base.py:815`), so `{{ x|f }}` passes `()` and `{{ x|f:None }}` passes `(None,)`. At `base.py:813`
/// Django inserts `autoescape=context.autoescape` **before** the positionals, so a filter whose own second
/// parameter is `autoescape` cannot take an argument: five bodies refuse at call time with a `TypeError`.
/// `args_check` (`base.py:822`) counts `autoescape` among the parameters, so the parse accepts and the
/// refusal must not move into the parser.
/// [`CannotHold`] is the one return that is not an answer: a Python `str` may hold a lone surrogate a Rust
/// `&str` cannot, so the seam returns the singleton and the shell runs Django's own callable.
#[pyfunction]
#[pyo3(signature = (name, value, args, autoescape = true, use_tz = true))]
fn filter(
    py: Python<'_>,
    name: &str,
    value: &Bound<'_, PyAny>,
    args: &Bound<'_, PyTuple>,
    autoescape: bool,
    use_tz: bool,
) -> PyResult<Py<PyAny>> {
    filters::run_filter(py, name, value, args, autoescape, use_tz, false)
}

/// A filter failure (or a filter stub) as a Python exception — **for the errors that never were one**.
/// A raise inside a host call does not come through here: the host keeps the `PyErr` it caught, so a
/// caller's `except` sees exactly what Python raised. What is left are the errors `template-core`
/// **synthesised** (a body's own refusal, an unimplemented filter, a parser error).
/// Reconstruction is by name: the six classes Django's filters **catch** get a named `PyXError`; everything
/// else is looked up in `builtins` and instantiated with the message, so `int(inf)`'s `OverflowError`
/// arrives as an `OverflowError`. A name whose class refuses a single message argument
/// (`UnicodeDecodeError` needs five) or that is not in `builtins` falls back to `Exception` with the name
/// prefixed.
fn filter_py_error(py: Python<'_>, error: &Error) -> PyErr {
    let ErrorKind::FilterHost(inner) = &error.kind else {
        // An `Unimplemented` is a filter this engine has not built; anything else is a parser error, which a
        // filter body cannot produce.
        return match error.kind {
            ErrorKind::Unimplemented(_) => UnimplementedTag::new_err(error.message()),
            _ => ParseError::new_err(error.message()),
        };
    };
    match inner.kind.name() {
        "AttributeError" => PyAttributeError::new_err(inner.message.clone()),
        "IndexError" => PyIndexError::new_err(inner.message.clone()),
        "KeyError" => PyKeyError::new_err(inner.message.clone()),
        "TypeError" => PyTypeError::new_err(inner.message.clone()),
        "ValueError" => PyValueError::new_err(inner.message.clone()),
        "ZeroDivisionError" => PyZeroDivisionError::new_err(inner.message.clone()),
        other => rebuilt_builtin(py, other, &inner.message)
            .unwrap_or_else(|| PyException::new_err(format!("{other}: {}", inner.message))),
    }
}

/// `builtins.<name>(message)` as a `PyErr`, or `None` if that is not a thing — a name
/// `builtins` does not hold, or a class whose constructor will not take one string. The
/// caller then prefixes the name onto the message, losing the class but not the word.
/// Nothing here panics: the input arrives on an error path, and an error path that raises
/// while reporting an error is worse than one that degrades.
fn rebuilt_builtin(py: Python<'_>, name: &str, message: &str) -> Option<PyErr> {
    let class = py.import("builtins").ok()?.getattr(name).ok()?;
    let instance = class.call1((message,)).ok()?;
    Some(PyErr::from_value(instance))
}

/// Every code point `pyunicode::is_space` accepts, ascending.
///
/// `is_space` inherits Rust's `char::is_whitespace` and CPython's `str.isspace()` follows a
/// different Unicode release, so only a Python test can compare them. A table rather than a
/// per-code-point predicate: a Python `str` may hold a lone surrogate, which answers `False`
/// but cannot be a Rust `&str`, and `char::from_u32` drops it without a special case.
#[pyfunction]
fn whitespace_codepoints() -> Vec<u32> {
    (0..=0x10FFFF)
        .filter(|&code| char::from_u32(code).is_some_and(template_core::pyunicode::is_space))
        .collect()
}

/// Python's `str.upper()` for a one-code-point string.
///
/// Exposed because Rust's and CPython's case tables are different Unicode releases, so
/// `tests/test_pyunicode_case_parity.py` must compare against `chr(cp).upper()` for every
/// code point. Takes `&str` for that comparison; lone surrogates cannot be passed, so the
/// test skips them explicitly.
#[pyfunction]
fn python_upper(s: &str) -> String {
    template_core::pyunicode::upper_str(s)
}

/// Split template-tag contents into arguments.
///
/// Raises `StopIteration` where Django's inner `next(bits)` on an exhausted `smart_split`
/// generator would, so the shell's error behaviour matches without translating a Rust error.
#[pyfunction]
fn split_contents(contents: &str) -> PyResult<Vec<String>> {
    // Only `contents` is read; token type and position are irrelevant. Constructing a
    // `Token` keeps the splitting logic in `Token` rather than duplicating it here.
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

/// `django.utils.html.escape` for an exact `str` — the table, nothing else. The caller is the shell's
/// `render_value_in_context` fast path: the autoescape branch for a value whose **exact** type is `str`,
/// where `localize` short-circuits, `template_localtime` has nothing to convert, and `conditional_escape`
/// reaches this table wrapped in `SafeString`. The wrapper is the shell's.
/// **`None` means "refuse"**: PyO3 extracts a Python `str` as `&str` only when it is UTF-8-encodable, which a
/// lone surrogate is not — and `'\ud800'` is a value stock Django renders. A `&str` signature would turn a
/// value that renders today into a raise, so the refusal sends the caller down the slow path.
#[pyfunction]
fn html_escape(text: &Bound<'_, PyString>) -> PyResult<Option<String>> {
    match text.to_str() {
        Ok(s) => Ok(Some(template_core::filters::html_escape(s))),
        Err(_) => Ok(None),
    }
}

/// One lexed token as it crosses into Python: token type discriminant, contents,
/// line number, and character-index span (`None` when lexed without `debug`).
///
/// Named because `clippy::type_complexity` rejects the anonymous tuple.
type LexedToken = (u8, String, usize, Option<(usize, usize)>);

/// Lex `source` into `(token_type, contents, lineno, position)` tuples.
///
/// The token type is an `int` matching `django.template.base.TokenType`'s discriminants;
/// `position` holds **character** indices.
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
/// `template-core` stores the index and never reads it; the shell resolves it through the
/// `resolve_ref` callback passed to `materialise`. Constructible from Python so a test can
/// hand-build a descriptor, which is the only reason it is a `#[pyclass]` with a `#[new]`.
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
/// Rides the wire rather than a plain `str` because `{% verbatim %}`'s content is a
/// `SafeString` computed at **parse** time, and a plain `str` would be escaped at render.
/// The shell converts it with `mark_safe`; Rust does not import Django to do so.
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

/// A position in the enclosing node's own nodelists — `AttrValue::NodeListRef`.
///
/// Its own type rather than a bare `int`, because `AttrValue::Int` arrives as an `int` too,
/// so a materialiser dispatching on `isinstance(value, int)` could not tell an integer
/// attribute from a nodelist reference and would hand a node a *wrong* nodelist, which no
/// shallow test notices.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct NodeListRef {
    /// The index into the enclosing `NodeDesc.nodelists`, in descriptor order.
    #[pyo3(get)]
    pub index: usize,
}

#[pymethods]
impl NodeListRef {
    #[new]
    fn new(index: usize) -> Self {
        Self { index }
    }

    fn __repr__(&self) -> String {
        format!("<NodeListRef {}>", self.index)
    }
}

/// `AttrValue::NodeSlot` — an index into the per-parse reference table.
///
/// Filled by the **materialiser**, not the host: the shared value is a materialised Python
/// object, so it does not exist during the parse (`{% cycle %}`'s `CycleNode` produces it).
/// Two positions, different things: as an attribute **value** it is the cycle's object; as a
/// descriptor's own fifth element the descriptor *is* that object, so a second `{% cycle c %}`
/// resolves to the first one's node. Its own type rather than a bare `int`, for
/// `NodeListRef`'s reason.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct NodeSlot {
    #[pyo3(get)]
    pub index: usize,
}

#[pymethods]
impl NodeSlot {
    #[new]
    fn new(index: usize) -> Self {
        Self { index }
    }

    fn __repr__(&self) -> String {
        format!("<NodeSlot {}>", self.index)
    }
}

/// `AttrValue::Tokens` — a `BlockTranslateNode`'s `singular`/`.plural`, the one wire attribute that is a slice
/// of the **token stream**, not a subtree: `do_block_translate` scans its body token by token
/// (`i18n.py:560-578`) and `render_token_list` (`i18n.py:134-146`) then walks the `Token`s by
/// `token_type`/`contents`.
/// Its own class rather than a bare `list` because a bare list of 4-tuples decodes into plain tuples, and
/// `token.token_type` would be an `AttributeError` at **render** time, after a parse that looked fine.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct Tokens {
    /// A Python `list` of `(token_type, contents, lineno, position)` tuples.
    #[pyo3(get)]
    pub tokens: Py<PyAny>,
}

#[pymethods]
impl Tokens {
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        Ok(format!("<Tokens {}>", self.tokens.bind(py).repr()?))
    }
}

/// `AttrValue::IfCondition` — one node of a `{% if %}` condition, the one attribute whose value is a parse
/// **tree** of Python objects: `IfNode.render` calls `condition.eval(context)` per branch
/// (`defaulttags.py:326`), an `Operator`'s `eval` recurses through `first`/`second`, and a
/// `TemplateLiteral`'s calls `self.value.resolve(context, ignore_failures=True)` (`defaulttags.py:917-918`).
/// Two kind-specific halves, dispatched on `kind` and never both needed: `key` is one of `smartif.OPERATORS`'
/// thirteen (`second` is `None` for the prefix `not`, `smartif.py:81-83`); `text`/`expression` are the
/// literal's, `expression` a host `FilterExpression` by handle.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct IfCondition {
    /// `"operator"` or `"literal"`.
    #[pyo3(get)]
    pub kind: String,
    /// The `OPERATORS` key; `None` on a literal.
    #[pyo3(get)]
    pub key: Option<String>,
    /// The operator's left operand; `None` on a literal.
    #[pyo3(get)]
    pub first: Option<Py<PyAny>>,
    /// The operator's right operand — `None` on a literal, and also `None` on a **prefix** operator,
    /// a different fact with the same spelling, which is why the materialiser branches on `kind` first.
    #[pyo3(get)]
    pub second: Option<Py<PyAny>>,
    /// `TemplateLiteral.text`; `None` on an operator.
    #[pyo3(get)]
    pub text: Option<String>,
    /// The literal's `FilterExpression`, as a `NodeRef`; `None` on an operator.
    #[pyo3(get)]
    pub expression: Option<Py<PyAny>>,
}

#[pymethods]
impl IfCondition {
    fn __repr__(&self) -> String {
        match self.kind.as_str() {
            "literal" => format!("<IfCondition literal {:?}>", self.text),
            _ => format!("<IfCondition operator {:?}>", self.key),
        }
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

create_exception!(
    _native,
    CannotHoldValue,
    PyException,
    "The seam cannot hold a Python value, so it declines to answer for it.\n\
     \n\
     A Python `str` is a sequence of code points and a Rust `&str` is UTF-8, so a\n\
     lone surrogate cannot be represented; the conversion raises rather than\n\
     losing the character. **This type is the Rust side's signal and never\n\
     reaches Python**: `run_filter` spends it and reports `CANNOT_HOLD` instead,\n\
     the way `?` carries any other internal failure. It is a distinct type rather\n\
     than a message because the test for it must be structural: `urlencode`\n\
     raises `UnicodeEncodeError` on this very input in Django too, and that one is\n\
     a real error the caller must see."
);

/// The value `_native.filter` returns for a Python value the seam cannot hold.
/// **A singleton, identity-compared, because `None` cannot carry this message.** A filter may legitimately
/// *answer* `None` — `{{ x|default:None }}` with a falsy `x` is `None` — so a `None` return would mean both
/// "here is your answer" and "I have none", and the shell would run Django's own body a second time over the
/// first, on an input nobody would think to test.
/// Not an exception: a refusal is **not an error** — Django renders this value — and a returned object cannot
/// be caught by a handler that has never heard of it.
#[pyclass(frozen, module = "django_rust_templates._native")]
pub struct CannotHold;

#[pymethods]
impl CannotHold {
    fn __repr__(&self) -> &'static str {
        "<the filter seam cannot hold this value>"
    }
}

/// The one [`CannotHold`] instance, shared by the module attribute and every refusal `run_filter`
/// reports.
///
/// One `PyOnceLock` behind both, because the shell's test is `is not` and two instances would make it
/// false for the refusal it exists to catch.
pub(crate) fn cannot_hold(py: Python<'_>) -> Py<PyAny> {
    static SINGLETON: pyo3::sync::PyOnceLock<Py<PyAny>> = pyo3::sync::PyOnceLock::new();
    SINGLETON
        .get_or_init(py, || {
            Py::new(py, CannotHold)
                .expect("allocating the CannotHold singleton")
                .into_any()
        })
        .clone_ref(py)
}

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
        // `bool`'s `IntoPyObject` yields a `Borrowed` (Python's `True`/`False` are singletons), which
        // must be owned before `into_any` can consume it.
        AttrValue::Bool(b) => (*b).into_pyobject(py)?.to_owned().into_any().unbind(),
        AttrValue::Int(i) => (*i).into_pyobject(py)?.into_any().unbind(),
        // A Python `float`, which is what Django's `Variable.literal` holds for the
        // `float(var)` branch — `1e400` included, because `f64` inf is Python's inf.
        AttrValue::Float(f) => (*f).into_pyobject(py)?.into_any().unbind(),
        AttrValue::BigInt(text) => {
            // `builtins.int(text)`, **never** `py.eval`: the text is a validated decimal
            // (optional ASCII sign then ASCII digits), so `int()` is exact and
            // arbitrary-precision, and an `eval` spelling would accept code.
            py.import("builtins")?
                .getattr("int")?
                .call1((text.as_str(),))?
                .unbind()
        }
        AttrValue::None => py.None(),
        AttrValue::Ref(r) => Py::new(py, NodeRef::new(r.0))?.into_any(),
        AttrValue::Variable(v) => variable_desc_to_py(py, v)?,
        AttrValue::FilterExpr(expr) => filter_expr_desc_to_py(py, expr, None)?,
        AttrValue::List(items) => {
            let items: Vec<Py<PyAny>> = items
                .iter()
                .map(|item| attr_value_to_py(py, item))
                .collect::<PyResult<_>>()?;
            PyList::new(py, items)?.into_any().unbind()
        }
        AttrValue::Map(entries) => {
            // A real `dict`, not a list of pairs. Python's dict keeps insertion order, so a
            // list buys nothing and loses something: measured against `token_kwargs`
            // (`base.py:1202`), a repeated key **keeps its first position and takes the last
            // value**, which `set_item` reproduces and a list handed to `context.new()` would
            // not.
            let mapping = PyDict::new(py);
            for (name, value) in entries {
                mapping.set_item(name, attr_value_to_py(py, value)?)?;
            }
            mapping.into_any().unbind()
        }
        AttrValue::NodeListRef(index) => Py::new(py, NodeListRef::new(*index))?.into_any(),
        AttrValue::NodeSlot(index) => Py::new(py, NodeSlot::new(*index))?.into_any(),
        AttrValue::Tuple(items) => {
            // A real `tuple`, and the container type is the contract: `IfNode.conditions_nodelists`
            // holds `(condition, nodelist)` pairs that `IfNode.render` *unpacks*
            // (`defaulttags.py:323`), and the parity suite can tell a list from a tuple.
            let items: Vec<Py<PyAny>> = items
                .iter()
                .map(|item| attr_value_to_py(py, item))
                .collect::<PyResult<_>>()?;
            PyTuple::new(py, items)?.into_any().unbind()
        }
        AttrValue::IfCondition(tree) => if_condition_to_py(py, tree)?,
        AttrValue::Tokens(tokens) => {
            // Each element goes through `token_to_py`, the one spelling of a token's wire form shared
            // with `_native.lex` and `NodeDesc.token`; `_decode_value` recognises the class and
            // rebuilds each element with `_decode_token`.
            let items: Vec<Py<PyAny>> = tokens
                .iter()
                .map(|token| token_to_py(py, token))
                .collect::<PyResult<_>>()?;
            let list = PyList::new(py, items)?.into_any().unbind();
            Py::new(py, Tokens { tokens: list })?.into_any()
        }
    })
}

/// One `{% if %}` condition node, as the tree the shell rebuilds `smartif` from.
/// Recursive, and the recursion is the shape: an `Operator` is two more conditions and a `TemplateLiteral` is
/// a leaf. `first` is `Some` on every operator — `nud` and `led` both set it (`smartif.py:54-56`, `smartif.py:81-83`) —
/// so this states that rather than emitting a `None` the shell would turn into an `AttributeError` three frames
/// away.
fn if_condition_to_py(py: Python<'_>, tree: &IfConditionDesc) -> PyResult<Py<PyAny>> {
    let condition = match tree {
        IfConditionDesc::Operator { key, first, second } => IfCondition {
            kind: "operator".to_string(),
            key: Some(key.clone()),
            first: Some(if_condition_to_py(py, first)?),
            second: match second {
                Some(second) => Some(if_condition_to_py(py, second)?),
                None => None,
            },
            text: None,
            expression: None,
        },
        IfConditionDesc::Literal { text, expression } => IfCondition {
            kind: "literal".to_string(),
            key: None,
            first: None,
            second: None,
            text: Some(text.clone()),
            expression: Some(attr_value_to_py(py, &AttrValue::Ref(*expression))?),
        },
    };
    Ok(Py::new(py, condition)?.into_any())
}

/// A `Variable` as it crosses the wire — `VariableDesc` as Python sees it, and what the materialiser
/// dispatches on (`isinstance(value, _native.Variable)`). The four readable attributes are Django's four
/// parse-time slots; the fifth, `message_context`, is set to `None` by `Variable.__init__` and never assigned
/// during a parse, so it is not on the wire — the materialiser sets it, which is where Django does too.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct Variable {
    #[pyo3(get)]
    pub var: String,
    /// Already converted to the Python object Django would hold: an `int` (of any size), a `float`, or
    /// a `SafeString`-to-be (`SafeStr`, turned into `mark_safe` by the shell). Converting here keeps
    /// `_decode_value` from knowing which wire value is which type.
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
/// `pub` because it and `attr_value_to_py` are mutually recursive: a `Variable`'s literal is itself an
/// `AttrValue`, and `AttrValue::Variable` is the other caller.
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

/// A `FilterVar` as a 2-tuple — Django's own `(lookup, value)`.
///
/// The `bool` is `lookup`; the shell reads it to decide whether to `resolve` the `Variable` now or keep
/// it for the context. See `FilterVar`'s doc for why the value is a `Variable` on both sides of the flag.
fn filter_var_to_py(py: Python<'_>, var: &FilterVar) -> PyResult<Py<PyAny>> {
    let (lookup, value) = var;
    Ok(PyTuple::new(
        py,
        [
            (*lookup).into_pyobject(py)?.to_owned().into_any().unbind(),
            attr_value_to_py(py, value)?,
        ],
    )?
    .into_any()
    .unbind())
}

/// A `FilterExpression` as it crosses the wire: three readable attributes, shaped Django's with the filter
/// *function* replaced by its name — `filters` is a list of `((name, args), handle)` pairs, each `args` a list
/// of `(lookup, Variable)` pairs, Django's `filters.append((filter_func, args))` (`base.py:773`) with the
/// function carried beside the name as a handle.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct FilterExpr {
    /// `FilterExpression.token`.
    #[pyo3(get)]
    pub token: String,
    /// `FilterExpression.var` **before materialisation**, as `(lookup, Variable)`.
    #[pyo3(get)]
    pub var: Py<PyAny>,
    /// `FilterExpression.filters`, as `[((name, [(lookup, Variable), …]), handle), …]`.
    ///
    /// The handle is a `NodeRef` into [`Self::refs`], or `None` when the host ran no filter check. See
    /// `FilterCall::handle` for why the name on its own is not enough.
    #[pyo3(get)]
    pub filters: Py<PyAny>,
    /// The reference table this descriptor's handles index into, or `None` for a descriptor built outside a
    /// parse that owns one. Carried on the descriptor because `parse_filter_expression` is a parse with a table
    /// of its own: a handle is meaningless outside the (table, index) pair, so the pair travels together or
    /// every handle it produced would dangle. The whole-template path is unaffected — `materialise` derives
    /// `resolve_ref` from the same table.
    #[pyo3(get)]
    pub refs: Option<Py<PyAny>>,
}

#[pymethods]
impl FilterExpr {
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        Ok(format!(
            "<FilterExpr {:?} var={} filters={}>",
            self.token,
            self.var.bind(py).repr()?,
            self.filters.bind(py).repr()?,
        ))
    }
}

/// A [`FilterExprDesc`] as Python sees it.
///
/// `refs` is the table the descriptor's handles index into — the parse's own, or `None` for a descriptor
/// that carries no handles (every nested one resolves through `materialise`'s `resolve_ref`, the same
/// table).
pub fn filter_expr_desc_to_py(
    py: Python<'_>,
    desc: &FilterExprDesc,
    refs: Option<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    let filters = desc
        .filters
        .iter()
        .map(|call| {
            let args: Vec<Py<PyAny>> = call
                .args
                .iter()
                .map(|arg| filter_var_to_py(py, arg))
                .collect::<PyResult<_>>()?;
            // Django's `(filter_func, args)` pair, with the name in the function's place and the
            // function itself beside it: the pair stays one thing, because it is one thing that renders.
            let named = pair_to_py(py, &call.name, PyList::new(py, args)?.into_any().unbind())?;
            let handle = match call.handle {
                Some(handle) => Py::new(py, NodeRef::new(handle.0))?.into_any(),
                None => py.None(),
            };
            Ok(PyTuple::new(py, [named, handle])?.into_any().unbind())
        })
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    Ok(Py::new(
        py,
        FilterExpr {
            token: desc.token.clone(),
            var: filter_var_to_py(py, &desc.var)?,
            filters: PyList::new(py, filters)?.into_any().unbind(),
            refs,
        },
    )?
    .into_any())
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

    // The fifth element, and it is not an attribute: `NodeDesc::slot` says this node *is* the object
    // in a reference-table slot, which the materialiser reads before it looks at `attrs` — a
    // `{% cycle c %}` descriptor has no attributes at all. `None`, never `0`, when there is no slot:
    // index 0 is a real slot.
    let slot = match desc.slot {
        Some(index) => Py::new(py, NodeSlot::new(index))?.into_any(),
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
            slot,
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
/// The token rides as an attribute on the exception **instance** rather than through a
/// `#[pyclass(extends = PyException)]` with a custom `__new__`: every `BaseException` has a `__dict__`, so
/// `e.token` is readable exactly as it will be on the shell's `TemplateSyntaxError`. `class_name` rides the same
/// way: the exception *object* goes to Python, so a `ParseError` cannot be the class Django would have raised —
/// the shell needs **which** class to raise instead. See [`python_host_error`].
fn parse_error(py: Python<'_>, error: &Error) -> PyResult<PyErr> {
    let err = match &error.kind {
        // Django reaches `next(bits)` on an exhausted iterator and lets `StopIteration` out of `parse`
        // (only `Exception` is caught, to attach the token). A bare `StopIteration` has no arguments,
        // hence an empty `str()`, which is what `new_err(())` produces.
        ErrorKind::UnterminatedMarker => PyStopIteration::new_err(()),
        // Django's `parse` catches `Exception`, not `TemplateSyntaxError`, so a `ValueError` from a tag body
        // leaves `parse` as the *same object* with `.token` attached below — `do_filter`'s unguarded
        // `token.contents.split(None, 1)` (`defaulttags.py:744`). A `TemplateSyntaxError` with the same text
        // satisfies every comparison in `tests/test_parser_errors.py`, making the divergent class invisible.
        ErrorKind::ValueError(_) => PyValueError::new_err(error.message()),
        // Its own class, so the "not built yet" bucket is visible at the FFI.
        ErrorKind::Unimplemented(_) => UnimplementedTag::new_err(error.message()),
        _ => ParseError::new_err(error.message()),
    };
    let token = match &error.token {
        Some(token) => token_to_py(py, token)?,
        None => py.None(),
    };
    err.value(py).setattr("token", token)?;
    let class_name = match &error.kind {
        ErrorKind::Host(host) => match &host.class {
            Some(class) => class
                .name()
                .to_string()
                .into_pyobject(py)?
                .into_any()
                .unbind(),
            None => py.None(),
        },
        _ => py.None(),
    };
    err.value(py).setattr("class_name", class_name)?;
    Ok(err)
}

/// Every code point Python's `unicodedata` reports as category `Nd`, ascending.
/// The comparison can run only where both languages exist — a Rust `char` cannot hold a surrogate, and
/// CPython's table is not readable from Rust. Unlike the whitespace table, which *inherits*
/// `char::is_whitespace`'s Unicode version, this is a literal table inside `pynum.rs`, so the two move
/// independently and nothing here pins either.
#[pyfunction]
fn decimal_digit_codepoints() -> Vec<u32> {
    template_core::pynum::decimal_digit_codepoints()
}

/// `Variable.__init__` for a token the caller already knows is a variable.
/// Separate from `parse` because the materialiser tests and `parse_filter_expression` drive it directly; the
/// shell never calls it on a template's behalf — it materialises what Rust parsed, it does not re-parse (§5.5).
#[pyfunction]
fn parse_variable(py: Python<'_>, raw: &str) -> PyResult<Py<PyAny>> {
    match template_core::parser::variable::parse_variable(raw) {
        Ok(desc) => variable_desc_to_py(py, &desc),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

/// The argument of a Python exception, and nothing else: `str(e)`, not `repr(e)` and not `PyErr`'s own
/// `Display` — which pyo3 defines as `"TemplateSyntaxError: Invalid filter: 'nope'"`, type name included. The
/// shell re-raises `TemplateSyntaxError` with exactly this text, so a prefix would be visible in `str(e)` and
/// in every `assertRaisesMessage`.
/// The exception **object** is dropped and only its message survives: `_culprit_node` and `__traceback__` live
/// on the object (§5.2).
fn python_error_message(py: Python<'_>, error: &PyErr) -> String {
    match error.value(py).str() {
        Ok(text) => text.to_string_lossy().into_owned(),
        // Unreachable for a `TemplateSyntaxError`, whose `str()` is its message. A host callable that
        // broke this fails in code we do not own, so report rather than panic.
        Err(_) => "<exception str() failed>".to_string(),
    }
}

/// The token Django attached to a host exception, as a [`TokenDesc`].
///
/// Read from `token_wire`, the attribute `ShellHost.call_tag` sets on its way out
/// (`python/django_rust_templates/host.py`) — not Django's own `.token`, a `Token` object that would
/// mean this crate naming its fields.
fn python_host_token(py: Python<'_>, error: &PyErr) -> Option<TokenDesc> {
    let wire = error.value(py).getattr("token_wire").ok()?;
    let (kind, contents, lineno, position) = wire.extract::<WireToken>().ok()?;
    Some(TokenDesc {
        token_type: TokenType::from_wire(kind)?,
        contents,
        lineno,
        position,
    })
}

/// The [`Error`] a Python exception raised by `ShellHost.call_tag` becomes.
///
/// [`python_error_message`] plus the token Django attached and the class Python raised. A tag's compile
/// function can re-enter Django's parse, so its exception arrives already carrying the **innermost** token;
/// flattening it to a message loses that and Rust's own `Parser::error` then attaches the **outer** tag's
/// token instead. The class diverges too: `Parser.parse` re-raises the *same object* (`base.py:583-585`),
/// so a tag body that raises `TypeError` leaves the parse as one, and every comparison in
/// `tests/test_parser_errors.py` compares text and would call the two equal.
fn python_host_error(py: Python<'_>, error: &PyErr) -> Error {
    Error::host_exception(
        python_error_message(py, error),
        filters::py_error_kind(py, error),
        python_host_token(py, error),
    )
}

/// The [`Host`] that puts Django's filter checks where Django calls them.
/// `has_tag` is `false` and `call_tag` panics exactly as in [`NoHost`]: `_native.parse` does not dispatch block
/// tags yet, and this host is built only for `parse_filter_expression`, whose token is a `{{ … }}` body and so is
/// never a block tag.
struct FilterCheckHost<'py> {
    /// The shell's `(name, arg_count) -> None` callable — `parser.find_filter(name)` followed by
    /// `FilterExpression.args_check(name, func, [None] * arg_count)`, the pair Django calls at
    /// `base.py:771-772`. `None` accepts every name.
    check_filter: Option<Bound<'py, PyAny>>,
    /// The shell's `token -> FilterExpression` callable — Django's own `Parser.compile_filter` (`base.py:676`),
    /// which `TemplateIfParser.create_var` calls (`defaulttags.py:929`). A second callable because the first is
    /// already composed in Python — `check_filter(parser)` is `find_filter` followed by `args_check` — and moving
    /// that composition into Rust would put a shape into the seam it does not have.
    /// `None` is an **error and not a default**: unlike `check_filter` there is no permissive answer, since the
    /// object is a real `FilterExpression` that `TemplateLiteral.eval` resolves at render time.
    compile_filter: Option<Bound<'py, PyAny>>,
    /// The per-parse reference table — the one side collection two tags share, one entry per
    /// `Host::alloc_slot`, a `None` placeholder until something fills it. `{% if %}`'s handle is filled during
    /// the parse; `{% cycle %}`'s node is filled by the *materialiser*, which is why the table outlives the parse
    /// and is handed to `materialise` as a third element.
    /// **It is the Python `list` itself, not a Rust `Vec` copied out on read.** A handle is an *index*,
    /// meaningful only against one table, so a copy per read makes the same index mean two things:
    /// `{% partialdef %}` materialises its body *during* the parse, so a slot reserved outside the body and
    /// filled inside it would come back empty and build a second node for one slot.
    refs: Py<PyAny>,
    /// The shell's tag seam, one object per parse — `ShellHost` in `python/django_rust_templates/host.py`.
    /// **One object rather than two callables**, because `has_tag` and `call_tag` must answer about the *same*
    /// registry at the *same* moment: `has_tag` is consulted first and `call_tag` called only for a name it
    /// accepted. Two callables would let a caller pair them across parses with no error anywhere. `None` is
    /// [`NoHost`]'s behaviour exactly.
    /// [`NoHost`]: template_core::parser::host::NoHost
    host: Option<Bound<'py, PyAny>>,
}

impl FilterCheckHost<'_> {
    /// The table as the Python list the wire and the two callbacks use.
    ///
    /// A `list`, not a tuple: `materialise`'s `register_ref` is `list.__setitem__`, and reserving an
    /// index without filling it is a normal state — a `None` placeholder is what the materialiser reads
    /// as "build the node".
    ///
    /// The same object every time, and that is the contract: two readers of one index must subscript
    /// one table, so this is a `clone_ref` and not a copy — `register_partial_def` hands the list to
    /// the shell mid-parse and `compile_template` hands it to `materialise` afterwards.
    fn ref_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.refs.clone_ref(py))
    }

    /// Reserve the next slot and fill it in one step, returning its handle.
    ///
    /// Every producer that *has* the object when it reserves goes through this — a host-built node
    /// (`call_tag`, `partial_node`), a host-bound filter function (`check_filter`), and the
    /// `PartialDefNode` the shell returns from `register_partial_def`. Only [`Host::alloc_slot`]
    /// reserves without filling, leaving a `None` placeholder.
    ///
    /// It appends to the same list `alloc_slot` does, so an index reserved here is readable by a
    /// materialisation that starts later — and by one that started earlier and is still running, which
    /// is `{% partialdef %}`'s case.
    fn fill_slot(&self, value: Bound<'_, PyAny>) -> CoreNodeRef {
        let refs = self.refs.bind(value.py());
        let index: usize = refs.len().expect("the reference table is a list");
        refs.call_method1("append", (value,))
            .expect("the reference table is a list");
        CoreNodeRef(index as u64)
    }
}

impl Host for FilterCheckHost<'_> {
    fn has_tag(&self, command: &str) -> bool {
        let Some(host) = &self.host else {
            return false;
        };
        // A raise here is an integration bug in the shell, not a verdict on the template, and the trait
        // returns a plain `bool`. A panic is the loud option rather than swallowing it as `false`, which
        // would route a host tag to Rust's registry and report `Invalid block tag` for a tag the host
        // was ready to compile.
        host.call_method1("has_tag", (command,))
            .and_then(|answer| answer.extract::<bool>())
            .expect("ShellHost.has_tag raised or did not return a bool")
    }

    // **`FilterCheckHost` must override these, and that is not a detail.** It is the
    // production adapter: `_native.compile_template` builds this very struct and hands it
    // to `parse_nodelist`, so there is no other PyO3 host for an implementation to live
    // in. Inheriting the trait's `false` default would leave the optimisation inert —
    // passing every test except the zero-transfer assertion.
    fn tag_is_stream_free(&self, command: &str) -> bool {
        let Some(host) = &self.host else {
            // No Python host, no classifier: the ordinary path, which is also what a
            // caller driving `_native.parse` directly gets.
            return false;
        };
        // **Absence is not failure.** `_native.compile_template` takes a caller-supplied
        // host, and one written against the seam as it was before this optimisation has no
        // such method. That host must still compile — reading a missing attribute as
        // `false` puts it on the ordinary path, which is exactly what it had. An attribute
        // that *exists* and misbehaves is still a loud integration bug, like `has_tag`'s.
        let py = host.py();
        let method = match host.getattr("tag_is_stream_free") {
            Ok(method) => method,
            // **Absence declines; a lookup that raises stays loud.** Returning
            // `false` for any error would silently swallow a property or custom
            // `__getattr__` that failed, which is an integration bug and not a
            // host that predates this optimisation.
            Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => {
                return false
            }
            Err(error) => panic!("ShellHost.tag_is_stream_free lookup raised: {error}"),
        };
        method
            .call1((command,))
            .and_then(|answer| answer.extract::<bool>())
            .expect("ShellHost.tag_is_stream_free raised or did not return a bool")
    }

    fn call_tag_stream_free(&self, command: &str, token: &Token) -> Result<NodeDesc, Error> {
        let Some(host) = &self.host else {
            unreachable!(
                "FilterCheckHost::call_tag_stream_free is never reached without a host: \
                 tag_is_stream_free returns false when there is none"
            )
        };
        let py = host.py();
        let wire = (
            token.token_type as u8,
            token.contents.clone(),
            token.lineno,
            token.position,
        );
        let node = host
            .call_method1("call_tag_stream_free", (command, wire))
            .map_err(|error| python_host_error(py, &error))?;
        // The same node handling `call_tag` performs, because the node is the same kind
        // of object: only the token vector is absent.
        let must_be_first = node
            .getattr("must_be_first")
            .and_then(|flag| flag.extract::<bool>())
            .map_err(|error| {
                Error::internal(format!(
                    "ShellHost.call_tag_stream_free({command:?}) returned a node without a \
                     bool `must_be_first`: {error}"
                ))
            })?;
        // The same reference-table handling as `call_tag`: the node is a Python object
        // this side must not read, named by a `NodeRef` (§5.2). `fill_slot` takes
        // ownership, so `must_be_first` is read off it above.
        let handle = self.fill_slot(node);
        let mut desc = NodeDesc::host(handle);
        desc.must_be_first = must_be_first;
        Ok(desc)
    }

    fn library_tag_loaded(&self, command: &str) -> bool {
        let Some(host) = &self.host else {
            // No host means no parser and no dict of loaded tags — `NoHost`'s answer. `false` rather
            // than a panic, unlike `call_tag`: nothing is *expected* here, and a bare membership
            // question with nothing to be a member of is `false`.
            return false;
        };
        // A shell integration bug, for `has_tag`'s reason; panics rather than answering `false`,
        // which would report `Invalid block tag` for a tag the host was ready to register.
        host.call_method1("library_tag_loaded", (command,))
            .and_then(|answer| answer.extract::<bool>())
            .expect("ShellHost.library_tag_loaded raised or did not return a bool")
    }

    fn call_tag(
        &self,
        command: &str,
        token: &Token,
        tokens: &[Token],
    ) -> Result<(NodeDesc, Vec<Token>), Error> {
        let Some(host) = &self.host else {
            unreachable!(
                "FilterCheckHost::call_tag is never reached without a host: \
                 has_tag returns false when there is none"
            )
        };
        let py = host.py();
        let wire = |token: &Token| -> WireToken {
            (
                token.token_type as u8,
                token.contents.clone(),
                token.lineno,
                token.position,
            )
        };
        let stream = match PyList::new(py, tokens.iter().map(wire)) {
            Ok(list) => list,
            Err(error) => {
                return Err(Error::internal(format!(
                    "could not hand the token stream to the host: {error}"
                )))
            }
        };
        // The dispatched token travels as its **wire tuple**, not a Python `Token`: building one would
        // mean this crate naming a Django class. The shell turns it into
        // `Token(TokenType(t), contents, position, lineno)` — Django's own signature — so the compile
        // function receives a real token, which `token.split_contents()` needs.
        let answer = host
            .call_method1("call_tag", (command, wire(token), stream))
            .map_err(|error| python_host_error(py, &error))?;
        let (node, left) = match answer.extract::<(Bound<'_, PyAny>, Bound<'_, PyAny>)>() {
            Ok(pair) => pair,
            // Not `Error::host`: no Python exception was raised, so claiming one would send the reader
            // looking for a traceback that does not exist. This is the seam's own contract failing,
            // which is `Internal`.
            Err(error) => {
                return Err(Error::internal(format!(
                    "ShellHost.call_tag({command:?}) did not return a (node, tokens) pair: {error}"
                )))
            }
        };
        // The flag crosses **inbound**. `must_be_first` is a class attribute every Django `Node` carries
        // (`base.py:1031-1035`) that `extend_nodelist` reads unconditionally (`base.py:602`).
        //
        // Raise rather than default `false`: an object without the attribute is not a node, and
        // defaulting would silently accept exactly the shape this read exists to refuse.
        let must_be_first = node
            .getattr("must_be_first")
            .and_then(|flag| flag.extract::<bool>())
            .map_err(|error| {
                Error::internal(format!(
                    "ShellHost.call_tag({command:?}) returned a node without a \
                     bool `must_be_first`: {error}"
                ))
            })?;
        let left = match left.extract::<Vec<WireToken>>() {
            Ok(left) => left,
            Err(error) => {
                return Err(Error::internal(format!(
                    "ShellHost.call_tag({command:?}) returned tokens this side cannot read: {error}"
                )))
            }
        };
        let left = left
            .into_iter()
            .map(|(kind, contents, lineno, position)| {
                TokenType::from_wire(kind).map(|token_type| Token {
                    token_type,
                    contents,
                    lineno,
                    position,
                })
            })
            .collect::<Option<Vec<Token>>>()
            .ok_or_else(|| {
                Error::internal(format!(
                    "ShellHost.call_tag({command:?}) returned a token with no known type"
                ))
            })?;
        // The node the host built goes into the same table `{% if %}`'s `FilterExpression` and
        // `{% cycle %}`'s slot live in: a Python object this side must not read, named by a `NodeRef`
        // (§5.2). `fill_slot` takes ownership, so `must_be_first` was read off it above.
        let handle = self.fill_slot(node);
        let mut desc = NodeDesc::host(handle);
        desc.must_be_first = must_be_first;
        Ok((desc, left))
    }

    fn check_filter(&self, name: &str, arg_count: usize) -> Result<Option<CoreNodeRef>, Error> {
        let Some(check) = &self.check_filter else {
            return Ok(None);
        };
        // The callable *returns* the function it found, with `args_check` still run inside it at
        // Django's line, so the error ordering is unchanged.
        match check.call1((name, arg_count)) {
            Ok(func) => {
                // The same table `alloc_slot` reserves into — a handle is an index into the parse's
                // table and this is the parse. Reserved-and-filled in one step, so no `None` placeholder
                // is ever visible here.
                Ok(Some(self.fill_slot(func)))
            }
            // The callable raises Django's own exception, so its message is already Django's.
            Err(error) => Err(Error::host(python_error_message(check.py(), &error))),
        }
    }

    fn partial_node(&self, name: &str) -> Result<CoreNodeRef, Error> {
        let Some(host) = &self.host else {
            // Reachable, unlike `call_tag`'s `unreachable!`: `has_tag` answering `false` with no host
            // routes `{% partial %}` *towards* Rust's registry, which has a body. `Internal` for
            // `compile_filter`'s reason: nothing raised a Python exception, so this is the caller's
            // integration bug, not a `Host` error flattened.
            return Err(Error::internal(
                "this parse reached {% partial %} with no host; pass a \
                 `ShellHost` to `_native.parse`",
            ));
        };
        // The object is `ShellHost.partial_node`'s — a real `PartialNode` holding a live
        // `DeferredSubDict` — and it goes into the same table `call_tag`'s nodes and `{% cycle %}`'s
        // slots live in: a descriptor names it by `NodeRef` and this side must not read it (§5.2).
        //
        // The name crosses **unresolved**: resolving it here would snapshot parser state at compile
        // time and break `{% partial p %}{% partialdef p %}`.
        // `test_partial_node_does_not_resolve_the_name` pins it.
        let node = host
            .call_method1("partial_node", (name,))
            .map_err(|error| python_host_error(host.py(), &error))?;
        Ok(self.fill_slot(node))
    }

    fn register_partial_def(
        &self,
        token: &Token,
        name: &str,
        inline: bool,
        body: &NodeListDesc,
        source_start: Option<usize>,
        source_end: Option<usize>,
    ) -> Result<CoreNodeRef, Error> {
        let Some(host) = &self.host else {
            // Reachable for `partial_node`'s reason, and reached the same way: `ShellHost.has_tag`
            // answers `False` for `partialdef` — the registered callable is still `defaulttags`' — so
            // the tag routes *to* Rust's registry with no host in play.
            return Err(Error::internal(
                "this parse reached {% partialdef %} with no host; pass a \
                 `ShellHost` to `_native.parse`",
            ));
        };
        let py = host.py();
        // The dispatched token travels as its wire tuple, not a Python `Token`, for `call_tag`'s
        // reason. The shell needs a real token because `Template.get_exception_info` reads the one
        // attached to the duplicate-definition `TemplateSyntaxError`.
        let wire = (
            token.token_type as u8,
            token.contents.clone(),
            token.lineno,
            token.position,
        );
        // The body crosses as the **descriptor** tuple `parse_source` hands out plus the parse's
        // **reference table** as a third element, which is what makes the body materialisable: a handle is
        // an index into the parse's one table, and `materialise` derives `resolve_ref`/`register_ref` from
        // the third element when none is passed. These are the *same* list `compile_template` later hands to
        // the outer `materialise`, so a slot the body fills is filled for the whole tree (`{% cycle %}`'s
        // slot reserved outside a body and named inside it is the case that notices).
        //
        // The shell still materialises it: `materialise` builds one `NodeList` that the stored
        // `PartialTemplate` and the returned `PartialDefNode` share *by identity* (`defaulttags.py:1258`,
        // `defaulttags.py:1265`); building it twice would give the inline and deferred paths different trees.
        let body = nodelist_desc_to_py(py, body).map_err(|error| {
            Error::internal(format!(
                "could not hand {{% partialdef {name} %}}'s body to the host: {error}"
            ))
        })?;
        let body = with_ref_table(py, body, self).map_err(|error| {
            Error::internal(format!(
                "could not attach the parse's reference table to {{% partialdef \
                 {name} %}}'s body: {error}"
            ))
        })?;
        let node = host
            .call_method1(
                "register_partial_def",
                (wire, name, inline, body, source_start, source_end),
            )
            .map_err(|error| python_host_error(host.py(), &error))?;
        // The same table `partial_node`'s node and `{% cycle %}`'s slots live in, reserved-and-filled in
        // one step so no `None` placeholder is visible for this producer.
        Ok(self.fill_slot(node))
    }

    /// Python's own `repr()`, which is why this override exists.
    /// [`Host::repr`]'s default (`py_repr_str`) is exact on ASCII only, and the tag that reads it interpolates
    /// the **offending token** — arbitrary template text — through `%r`. Measured:
    /// `{% with a=1 foo<U+00AD>bar %}` reports `'with' received an invalid token: 'foo\xadbar'`, which the Rust
    /// default would emit with the soft hyphen raw.
    /// The `expect` is a `repr()` of a `str` — a C slot with no failure mode but memory exhaustion. Falling back
    /// to the Rust approximation would put a *silently wrong* string in a message the parity suite compares
    /// exactly.
    fn repr(&self, s: &str) -> String {
        // `Python::attach` is the pinned PyO3's spelling — it has no `Python::with_gil`
        // (`pyo3-0.29.3/src/marker.rs`) — and is cheap here because the caller is already attached.
        Python::attach(|py| {
            PyString::new(py, s)
                .as_any()
                .repr()
                .expect("repr() of a str cannot raise")
                .to_string_lossy()
                .into_owned()
        })
    }

    fn alloc_slot(&self) -> usize {
        // `list.append(None)` on the parse's **own** list, which lets a slot reserved here be filled by
        // a materialisation that has not started yet and read by one that started mid-parse: the index
        // and the table travel together because there is only one table.
        //
        // The placeholder is `py.None()`, which the materialiser reads as "reserved but not filled, so
        // build the node".
        Python::attach(|py| {
            let refs = self.refs.bind(py);
            let index: usize = refs.len().expect("the reference table is a list");
            refs.call_method1("append", (py.None(),))
                .expect("the reference table is a list");
            index
        })
    }

    fn compile_filter(&self, token: &str) -> Result<CoreNodeRef, Error> {
        let Some(compile) = &self.compile_filter else {
            // Reachable *only* through a caller that forgot: a template with no `{% if %}` never gets here. So
            // the message names the argument rather than the template — this is an integration bug in the caller,
            // and a `TemplateSyntaxError` shaped like a template error would send the reader to the wrong file.
            // `Internal` rather than `Host`, because nothing raised it: `Host` is the spelling for "a Python
            // exception, flattened", and there is none here.
            return Err(Error::internal(
                "this parse reached an {% if %} condition with no compile_filter \
                 callable; pass the shell's `compile_filter(parser)` to `_native.parse`",
            ));
        };
        // `TemplateIfParser.create_var`'s line: build the operand now, at this point in the token walk,
        // so a bad filter raises before the operator algorithm runs and carries Django's own message
        // (`Host::compile_filter` in `template-core` has the measurements).
        match compile.call1((token,)) {
            Ok(expression) => {
                // Stored in the same table `alloc_slot` reserves into, because the descriptor names it by
                // `NodeRef` and the materialiser resolves that through the same two callbacks
                // `{% resetcycle %}` uses. Reserved-and-filled in one step, so no `None` placeholder is
                // ever visible here.
                Ok(self.fill_slot(expression))
            }
            // The callable raises Django's own `TemplateSyntaxError`, from `find_filter` or from
            // `FilterExpression.__init__`'s variable parsing; the message is flattened rather than
            // replaced so both read as themselves.
            Err(error) => Err(Error::host(python_error_message(compile.py(), &error))),
        }
    }
}

/// `FilterExpression.__init__` for a token the caller already knows is one.
///
/// `check_filter` is the host half of the parse: Django calls `find_filter` and `args_check` *inside*
/// the match loop (`base.py:771-772`), at a point no batched shell-side pass can reproduce — see
/// `Host::check_filter`. `None` means "accept every filter name", which the Rust-side and
/// descriptor-shape tests want.
#[pyfunction]
#[pyo3(signature = (token, check_filter = None))]
fn parse_filter_expression(
    py: Python<'_>,
    token: &str,
    check_filter: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let host = FilterCheckHost {
        check_filter,
        // A `{{ … }}` body is not a `{% if %}` condition, so no token here can reach
        // `Host::compile_filter`; `None` is the honest spelling of "no such seam".
        compile_filter: None,
        // This entry point's own table, handed out with the descriptor below. One object and not a copy,
        // so `FilterExpr::refs` and `_materialise_filter_expression` subscript the same list.
        refs: PyList::empty(py).into_any().unbind(),
        // A `{{ … }}` body has no block tags, so `has_tag` is never consulted.
        host: None,
    };
    match template_core::parser::filter_expr::parse_filter_expression(token, &host) {
        // The table goes out **with** the descriptor, because this entry point's handles index into it
        // and nothing else will ever see it; returning the descriptor alone would leave every handle
        // pointing into a dropped list. See `FilterExpr::refs`.
        Ok(desc) => filter_expr_desc_to_py(py, &desc, Some(host.ref_list(py)?)),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

/// Parse `source` into the Rust node descriptor tree, with `host` at the seam.
///
/// Returns `NodeListDesc` — the type `template-core` owns — rather than a Python object. Shared by
/// `parse_source` (which converts it to the wire tuple) and `compile_template` (which keeps it in Rust
/// for the render walk), so a second copy cannot be where a later caller's host silently went missing.
fn parse_nodelist(
    source: &str,
    debug: bool,
    origin_template_name: Option<String>,
    host: &dyn Host,
) -> Result<NodeListDesc, Error> {
    let tokens = if debug {
        CoreLexer::debug(source).tokenize()
    } else {
        CoreLexer::new(source).tokenize()
    };

    let mut parser = CoreParser::new(tokens, host);
    if let Some(name) = origin_template_name {
        parser = parser.with_origin_template_name(name);
    }

    parser.parse(&[])
}

/// `parse_nodelist`, as the descriptor tuple `_native.parse` hands to Python.
fn parse_source(
    py: Python<'_>,
    source: &str,
    debug: bool,
    origin_template_name: Option<String>,
    host: &dyn Host,
) -> PyResult<Py<PyAny>> {
    match parse_nodelist(source, debug, origin_template_name, host) {
        Ok(nodelist) => nodelist_desc_to_py(py, &nodelist),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

/// Attach the parse's reference table to its descriptor tuple.
///
/// `parse_source` returns `(nodes, contains_nontext)` — the nodelist alone — and this appends the table, which
/// lives on the host and so cannot be built from the nodelist. Two callers, one place: `parse` hands the result
/// to Python and `render` hands it straight to `materialise`. The elements are re-read out of the tuple rather
/// than returned separately, so the nodelist's wire shape has exactly one producer.
fn with_ref_table(
    py: Python<'_>,
    nodelist: Py<PyAny>,
    host: &FilterCheckHost<'_>,
) -> PyResult<Py<PyAny>> {
    let nodelist = nodelist.bind(py);
    let nodes = nodelist.get_item(0)?;
    let contains_nontext = nodelist.get_item(1)?;
    Ok(PyTuple::new(
        py,
        [
            nodes.unbind(),
            contains_nontext.unbind(),
            host.ref_list(py)?,
        ],
    )?
    .into_any()
    .unbind())
}

/// Parse `source` into a node descriptor tree, **with the reference table it allocated**.
/// `debug` selects Django's `DebugLexer`, so token positions are recorded. `origin_template_name` is
/// `Origin.template_name` or `None` — the **name**, not a rendered `repr`: `{% extends %}` and `{% include %}`
/// read it directly.
/// The result is `(nodes, contains_nontext, refs)`; `refs` is the per-parse table `Host::alloc_slot` reserves
/// into, a property *of this parse* — `{% cycle %}`'s first mention reserves a slot its second names.
/// `check_filter` `None` means "accept every filter name"; a caller with a `Parser` must pass
/// `materialiser.check_filter(parser)`, or the parse-time ordering Django gives the check (`find_filter` then
/// `args_check`, *inside* the match loop, `base.py:771-772`) is lost — measured, `x|nope:1:2` reports
/// `Invalid filter: 'nope'` with the host and `Could not parse the remainder: ':2'` without it. `compile_filter`
/// is `{% if %}`'s half: a condition's operand is a **host-built `FilterExpression`**, not a wire descriptor,
/// because `TemplateLiteral.value` answers `value.resolve(context, ignore_failures=True)`
/// (`defaulttags.py:917-918`). Unlike `check_filter` there is no permissive default, so `{% if %}` parsed
/// without one fails at the condition with `ErrorKind::Internal`.
#[pyfunction]
#[pyo3(signature = (source, debug = false, origin_template_name = None, check_filter = None, compile_filter = None, host = None))]
fn parse(
    py: Python<'_>,
    source: &str,
    debug: bool,
    origin_template_name: Option<String>,
    check_filter: Option<Bound<'_, PyAny>>,
    compile_filter: Option<Bound<'_, PyAny>>,
    host: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let host = FilterCheckHost {
        check_filter,
        compile_filter,
        refs: PyList::empty(py).into_any().unbind(),
        host,
    };
    let nodelist = parse_source(py, source, debug, origin_template_name, &host)?;
    with_ref_table(py, nodelist, &host)
}

/// Which of Django's three `except` clauses a failed arm is judged by, so each swallowed set
/// is read off `base.py` in one place.
enum Arm {
    /// `base.py:968` — `TypeError, AttributeError, KeyError, ValueError, IndexError`.
    Item,
    /// `base.py:977` — `TypeError, AttributeError`. The one arm whose swallowed exception can
    /// still be re-raised, because Django's `dir()` line (`base.py:979`) sits inside it.
    Attr,
    /// `base.py:983-988` — `IndexError, ValueError, KeyError, TypeError`.
    Index,
}

/// Whether `error` is one this arm swallows — the only thing that decides between falling through to
/// the next arm and leaving the inner `try`.
///
/// Django's arms are `except` clauses, so a *subclass* matches and `PyErr::is_instance_of` (which is
/// `PyErr_GivenExceptionMatches`) is the right test; comparing type *names* would misjudge the two most
/// common rows — a `KeyError` from a dict-like and a `TypeError` from an unsubscriptable object.
fn swallowed(py: Python<'_>, error: &PyErr, arm: Arm) -> bool {
    match arm {
        Arm::Item => {
            error.is_instance_of::<PyTypeError>(py)
                || error.is_instance_of::<PyAttributeError>(py)
                || error.is_instance_of::<PyKeyError>(py)
                || error.is_instance_of::<PyValueError>(py)
                || error.is_instance_of::<PyIndexError>(py)
        }
        Arm::Attr => {
            error.is_instance_of::<PyTypeError>(py) || error.is_instance_of::<PyAttributeError>(py)
        }
        Arm::Index => {
            error.is_instance_of::<PyIndexError>(py)
                || error.is_instance_of::<PyValueError>(py)
                || error.is_instance_of::<PyKeyError>(py)
                || error.is_instance_of::<PyTypeError>(py)
        }
    }
}

/// A Python object, seen through `LookupTarget`.
///
/// **Holds `Py<PyAny>`, not `Bound<'py, PyAny>`, and is therefore `'static`.** Forced, not stylistic:
/// `Step::Found` boxes its child, and a boxed child borrowing from the container it came from would make
/// `current = found` in `resolve_lookup` a borrow error. Each method re-acquires the token with
/// `Python::attach`, which is reentrant.
struct PyTarget {
    value: Py<PyAny>,
    /// Immutable names owned by the compiled head; absent on the Python resolver
    /// seam, which receives fresh components rather than a render plan.
    names: Option<Arc<HashMap<String, Py<PyString>>>>,
    /// Fresh per `resolve` call and shared by every container in it, so a handle is dereferenceable
    /// within the call that produced it and meaningless outside it. Deliberately **not** the shell's
    /// `resolve_ref` table: those resolve *descriptor* references, these are values the caller asked for,
    /// and a second persistent table would make `NodeRef` ambiguous.
    arena: Rc<RefCell<Vec<Py<PyAny>>>>,
    /// The most recent exception an arm raised, held **by identity** until the port decides whether to
    /// re-raise it (§5.2 forbids flattening it to a string: that loses `_culprit_node` and
    /// `__traceback__`). `Rc<Cell<..>>` rather than `Cell<..>` because `get_*` takes `&self` and the
    /// decision to raise happens in `outcome_to_py`.
    ///
    /// Stashed on **every** failed arm, including swallowed ones: `Reraise` is returned both for an arm
    /// that raised something Django does not swallow *and* for an attribute arm whose swallowed
    /// `AttributeError` turned out to be Django's `dir()` re-raise. The read always follows the failing
    /// arm immediately, so the most recent write is the right one.
    pending: Rc<Cell<Option<PyErr>>>,
    /// The context this lookup runs against, shared with every target the loop produces.
    /// `string_if_invalid` is read from a target the loop has already walked into, so the root's copy is
    /// not enough.
    ///
    /// `Rc<Py<PyAny>>` rather than a cache filled on first use: Django re-reads the attribute at **every**
    /// substitution, and nothing measured needs a cache.
    context: Rc<Py<PyAny>>,
}

/// Hand-written because `Py<PyAny>` is not `Clone`: an owned Python reference is copied with
/// `clone_ref`, which needs the token. Dropping a copy without the token would be a refcount edit outside
/// the GIL.
impl Clone for PyTarget {
    fn clone(&self) -> Self {
        Python::attach(|py| Self {
            value: self.value.clone_ref(py),
            names: self.names.clone(),
            arena: Rc::clone(&self.arena),
            pending: Rc::clone(&self.pending),
            // No token needed: this is an `Rc` bump, not a `Py` clone.
            context: Rc::clone(&self.context),
        })
    }
}

impl PyTarget {
    fn new(
        value: Py<PyAny>,
        arena: &Rc<RefCell<Vec<Py<PyAny>>>>,
        pending: &Rc<Cell<Option<PyErr>>>,
        context: &Rc<Py<PyAny>>,
    ) -> Self {
        Self {
            value,
            names: None,
            arena: Rc::clone(arena),
            pending: Rc::clone(pending),
            context: Rc::clone(context),
        }
    }

    /// A target over `value`, sharing this call's arena, `pending` and context.
    ///
    /// A method rather than an inline `Self::new` because `call` and `string_if_invalid` both produce a
    /// fresh value mid-loop, and sharing the four `Rc`s is the whole contract.
    fn target_for(&self, value: Py<PyAny>) -> Box<dyn LookupTarget> {
        let mut target = Self::new(value, &self.arena, &self.pending, &self.context);
        target.names = self.names.clone();
        Box::new(target)
    }

    fn key<'py>(&self, py: Python<'py>, bit: &str) -> Bound<'py, PyString> {
        match self.names.as_ref().and_then(|names| names.get(bit)) {
            Some(key) => key.bind(py).clone(),
            None => PyString::new(py, bit),
        }
    }

    /// Classify one arm's result: a swallowed exception is a miss the next arm covers, anything else is
    /// `Raised`. Both are stashed; see `pending`.
    ///
    /// The token never leaves its closure, since a `Python<'_>` cannot be returned out of
    /// `Python::attach`, so the classification happens inside.
    fn step(&self, result: PyResult<Py<PyAny>>, arm: Arm) -> Step {
        match result {
            Ok(found) => Step::Found(self.target_for(found)),
            Err(error) => {
                let is_swallowed = Python::attach(|py| swallowed(py, &error, arm));
                self.pending.set(Some(error));
                if is_swallowed {
                    Step::Miss
                } else {
                    Step::Raised
                }
            }
        }
    }

    /// `bit in dir(current)`, **not** `hasattr`: `dir()` lists names statically, while `hasattr` invokes
    /// `getattr` — which would re-raise the very exception this predicate is being asked about.
    fn dir(&self, py: Python<'_>, bit: &str) -> bool {
        let Ok(names) = py
            .import("builtins")
            .and_then(|builtins| builtins.getattr("dir"))
            .and_then(|dir| dir.call1((self.value.bind(py),)))
        else {
            // A `dir()` that itself failed cannot be a real answer: "not in dir" keeps the loop's shape.
            return false;
        };
        names.contains(bit).unwrap_or(false)
    }

    /// `BaseContext.__getitem__` (`context.py:88-91`) — `for d in reversed(self.dicts)`, then
    /// `if key in d: return d[key]`, else `raise KeyError(key)`. Transcribed for the root target only;
    /// `Ok(None)` means *"use the real thing"*, so every uncertain path is the slow path.
    /// Fires only when this target **is** the context object, the class's subscript entry is
    /// `BASE_CONTEXT_GETITEM` (looked up every use, never cached — `Context.__getitem__` can be rebound while
    /// a body runs), `context.dicts` is **exactly** a `list`, and every layer is a `dict` subclass.
    /// Membership before subscription, both through the *object protocol* rather than raw `PyDict` calls: a
    /// `dict` subclass overriding `__contains__` to claim a key it lacks makes the two disagree, and
    /// `PyDict_GetItemWithError` would skip the `__missing__` that `d[key]` reaches. `key` is always a `str`.
    fn root_item(&self, py: Python<'_>, bit: &str) -> PyResult<Option<Py<PyAny>>> {
        let value = self.value.bind(py);
        if !value.is(self.context.bind(py)) {
            return Ok(None);
        }
        if !subscript_entry_is_pin(&value.get_type(), base_context_getitem(py)?) {
            return Ok(None);
        }
        // Read-then-bail: `dicts` is asked for before the bail is decided, and Django's own
        // `__getitem__` asks for it again, so a bail costs 2 reads of `dicts` to Django's 1 (measured
        // with a counting instance `__getattribute__`). A side-effecting `__getattribute__` can tell
        // the engines apart on this route; deciding without inspecting is not available.
        let Ok(dicts) = value.getattr(intern!(py, "dicts")) else {
            return Ok(None);
        };
        // **Exactly** a `list`: `reversed(self.dicts)` is `list.__reversed__`, and a subclass may
        // override it. Measured — a `class Rev(list)` returning `iter([{"x": ...}])` from `__reversed__`
        // makes Django answer that where positional indexing answers differently. A subclass
        // overriding only `__len__`/`__getitem__` does not diverge.
        if !dicts.is_exact_instance_of::<PyList>() {
            return Ok(None);
        }
        let Ok(dicts) = dicts.cast::<PyList>() else {
            return Ok(None);
        };
        let key = self.key(py, bit);
        // Every layer must be a `dict` before any is probed: a bail runs Django's method over the whole
        // stack, so probing first would run the layers above the non-dict twice. Measured — Django logs
        // one `__contains__` call, probing-then-bailing logs two.
        for layer in dicts.iter() {
            if !layer.is_instance_of::<PyDict>() {
                return Ok(None);
            }
        }
        // `reversed(self.dicts)` re-reads the length on every step, so a shortened list **ends the
        // search** — `break`, not a bail; bailing would re-walk a partly-consumed stack.
        let mut index = dicts.len();
        while index > 0 {
            index -= 1;
            if index >= dicts.len() {
                break;
            }
            let layer = dicts.get_item(index)?;
            if layer.contains(&key)? {
                return Ok(Some(layer.get_item(&key)?.unbind()));
            }
        }
        Err(PyKeyError::new_err(bit.to_owned()))
    }
}

impl LookupTarget for PyTarget {
    /// `hasattr(type(current), "__getitem__")` — the **type**, not the instance: a `__getitem__` defined
    /// only on the instance is invisible to Django's guard (`base.py:963`).
    fn has_getitem(&self) -> bool {
        Python::attach(|py| {
            let class = self.value.bind(py).get_type();
            let name = intern!(py, "__getitem__");
            // Most model attribute lookups have no subscript arm. Prove absence
            // without constructing and clearing AttributeError on every bit.
            // A custom metaclass can fabricate the attribute, so it must use
            // type's attribute reader and have neither relevant hook nor entry.
            if type_entry_is_absent(&class, name) {
                let meta = class.get_type();
                let plain_meta = meta.is(py.get_type::<PyType>())
                    || (type_getattribute(py).is_ok_and(|pin| {
                        type_entry_is_pin(&meta, intern!(py, "__getattribute__"), pin)
                    }) && type_entry_is_absent(&meta, intern!(py, "__getattr__"))
                        && type_entry_is_absent(&meta, name));
                if plain_meta {
                    return false;
                }
            }
            class.hasattr(name).unwrap_or(false)
        })
    }

    fn get_item(&self, bit: &str) -> Step {
        let result = Python::attach(|py| -> PyResult<Py<PyAny>> {
            match self.root_item(py, bit)? {
                Some(found) => Ok(found),
                None => self
                    .value
                    .bind(py)
                    .get_item(self.key(py, bit))
                    .map(|found| found.unbind()),
            }
        });
        self.step(result, Arm::Item)
    }

    fn get_attr(&self, bit: &str) -> Step {
        let result = Python::attach(|py| {
            self.value
                .bind(py)
                .getattr(self.key(py, bit))
                .map(|found| found.unbind())
        });
        self.step(result, Arm::Attr)
    }

    fn get_index(&self, index: &IntValue) -> Step {
        let result = Python::attach(|py| -> PyResult<Py<PyAny>> {
            let key = match index {
                IntValue::Small(n) => (*n).into_pyobject(py)?.into_any().unbind(),
                // Python's `int()` is unbounded and its digits are canonical ASCII — the same route
                // `attr_value_to_py` takes for `AttrValue::BigInt`, and it keeps the arm faithful for an
                // out-of-range index. Never `eval`.
                IntValue::Big(digits) => py
                    .import("builtins")?
                    .getattr("int")?
                    .call1((digits.as_str(),))?
                    .unbind(),
            };
            Ok(self.value.bind(py).get_item(key)?.unbind())
        });
        self.step(result, Arm::Index)
    }

    fn in_dir(&self, bit: &str) -> bool {
        Python::attach(|py| self.dir(py, bit))
    }

    fn context_probes_attribute(&self, bit: &str) -> bool {
        // Absent and truthy reach the same arm: `getattr(type(current), bit)` raises for an absent
        // attribute, and Django's inner `except` swallows it (`base.py:977`), so only a *present and
        // falsy* class attribute diverts (`base.py:972-976`). Measured: `{{ c.template }}` renders `''`
        // even though the instance holds `template=None`.
        //
        // `getattr` **walks the MRO**, which is load-bearing: for a `RequestContext` the class attribute
        // Django finds is two classes up, and a `type(obj).__dict__` probe would render a bound method
        // instead of the `''` the guard exists to produce.
        Python::attach(|py| {
            self.value
                .bind(py)
                .get_type()
                .getattr(self.key(py, bit))
                .map(|value| !value.is_truthy().unwrap_or(true))
                .unwrap_or(false)
        })
    }

    fn is_context(&self) -> bool {
        Python::attach(|py| match base_context(py) {
            Ok(class) => self.value.bind(py).is_instance(class).unwrap_or(false),
            Err(_) => false,
        })
    }

    /// Django's three tests, folded in because only the host can read the two flags:
    /// `callable(current)`, then `do_not_call_in_templates`, then `alters_data` (`base.py:993-996`).
    /// `None` when `callable(current)` is false, because then Django's arm does not run at all.
    fn call_arm(&self) -> Option<CallArm> {
        Python::attach(|py| {
            let value = self.value.bind(py);
            if !value.is_callable() {
                return None;
            }
            let flag = |name: &Bound<'_, PyString>| {
                value
                    .getattr(name)
                    .ok()
                    .and_then(|flag| flag.is_truthy().ok())
                    .unwrap_or(false)
            };
            // Order matters: both flags set takes `pass`, so it is `DoNotCall`.
            if flag(intern!(py, "do_not_call_in_templates")) {
                Some(CallArm::DoNotCall)
            } else if flag(intern!(py, "alters_data")) {
                Some(CallArm::AltersData)
            } else {
                Some(CallArm::Call)
            }
        })
    }

    /// `current()` with Django's `signature()`/`bind()` fallback (`base.py:1000-1013`).
    ///
    /// A `TypeError` from `signature`/`bind` means arguments were required; one from the call itself,
    /// with both checks succeeding, is the bare `raise` at `base.py:1013`. Measured: a zero-arg callable
    /// whose body raises `TypeError` propagates, so the *original* error must be stashed, not the `bind`
    /// error.
    fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure> {
        Python::attach(|py| {
            let value = self.value.bind(py);
            match value.call0() {
                Ok(called) => Ok(self.target_for(called.unbind())),
                Err(err) if err.is_instance_of::<PyTypeError>(py) => {
                    // Django's fallback: signature() then bind() (`base.py:1002-1013`).
                    //
                    // **`django.utils.inspect.signature`, not `inspect.signature`** — measured, not
                    // stylistic. Django's wrapper (`base.py:63`) on Python 3.14+ passes
                    // `annotation_format=Format.FORWARDREF` so a *deferred* annotation is never
                    // evaluated; the bare `inspect.signature` evaluates them and raises whatever the
                    // annotation evaluates to. Measured: `{{ obj.method }}` on a callable annotated
                    // `value: Undefined` raises `NameError` through this path and renders `''`
                    // through Django's (`django/utils/inspect.py:126-134`;
                    // `tests/template_tests/tests.py:231-243`).
                    let inspect = match py.import("django.utils.inspect") {
                        Ok(inspect) => inspect,
                        Err(error) => {
                            self.pending.set(Some(error));
                            return Err(CallFailure::Raised);
                        }
                    };
                    match inspect.getattr("signature").and_then(|f| f.call1((value,))) {
                        Err(e) if e.is_instance_of::<PyValueError>(py) => {
                            Err(CallFailure::NoSignature)
                        }
                        Err(error) => {
                            self.pending.set(Some(error));
                            Err(CallFailure::Raised)
                        }
                        Ok(sig) => match sig.call_method0(intern!(py, "bind")) {
                            Err(e) if e.is_instance_of::<PyTypeError>(py) => {
                                Err(CallFailure::ArgsRequired)
                            }
                            Err(error) => {
                                self.pending.set(Some(error));
                                Err(CallFailure::Raised)
                            }
                            // Neither check failed, so the `TypeError` came from the body and Django
                            // re-raises the **original** error: stash `err`, not the bind error.
                            Ok(_) => {
                                self.pending.set(Some(err));
                                Err(CallFailure::Raised)
                            }
                        },
                    }
                }
                Err(err) => {
                    self.pending.set(Some(err));
                    Err(CallFailure::Raised)
                }
            }
        })
    }

    fn string_if_invalid(&self) -> Result<Box<dyn LookupTarget>, PendingFailure> {
        Python::attach(|py| {
            // Read at the substitution site, not in `PyTarget::new`: Django re-reads the attribute at
            // each of its three assignments, and a plain value on an unbound context must never reach it
            // (measured; `m8_measure_invalid.py` §2).
            match string_if_invalid(self.context.bind(py)) {
                Ok(value) => Ok(self.target_for(value)),
                // `PendingFailure` means "the host holds the exception", so it must be holding it —
                // `outcome_to_py` reads `pending`.
                Err(error) => {
                    self.pending.set(Some(error));
                    Err(PendingFailure)
                }
            }
        })
    }

    fn node(&self) -> CoreNodeRef {
        // Register in this call's arena and hand back the index; `outcome_to_py` dereferences it in the
        // same call. A second `node()` on the same object pushes a second handle rather than reusing
        // the first: identity would need `Py::is` and turn a per-call arena into a cache.
        Python::attach(|py| {
            let mut arena = self.arena.borrow_mut();
            arena.push(self.value.clone_ref(py));
            CoreNodeRef(arena.len() as u64 - 1)
        })
    }
}

/// Django's `BaseContext`, cached once so `is_context` is not an import per lookup.
static BASE_CONTEXT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn base_context(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    BASE_CONTEXT.import(py, "django_rust_templates.engine.base", "BaseContext")
}

/// `BaseContext.__getitem__`, as `engine/base.py` bound it **during its own import**.
/// Read from that module rather than captured from the class here, and that is the whole of this guard's
/// soundness: `PyTarget::root_item` licences its transcription by comparing against this object, so it must be
/// the function as it stood before any project could rebind it — `type(context).__getitem__` taken on first use
/// could be taken after a rebind, licensing the transcribed body against a class that no longer runs it.
/// `tests/test_loop_read_guard.py` pins the ordering and is the control.
static BASE_CONTEXT_GETITEM: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn base_context_getitem(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    BASE_CONTEXT_GETITEM.import(
        py,
        "django_rust_templates.engine.base",
        "BASE_CONTEXT_GETITEM",
    )
}

// `_PyType_Lookup`: the walk of a type's MRO dictionaries that CPython's own subscript slot performs, and the
// question this guard is entitled to ask.
// Declared here rather than through `pyo3::ffi` because `pyo3-ffi` 0.29.3 skips it as private, while CPython
// declares it `PyAPI_FUNC` on the non-limited API (`Include/cpython/object.h:292`). This crate is not built for
// the stable ABI (no `abi3` feature). Windows does not export this private symbol,
// so it uses the public dictionary lookup below instead.
// The name is **interned** (the lookup cache keys on its identity), and the
// *borrowed* result is compared by pointer and dropped in the next statement, so nothing can free it in
// between. It sets no exception (`typeobject.c:5964`, comment at 5962-5963).
#[cfg(not(windows))]
extern "C" {
    fn _PyType_Lookup(
        type_: *mut pyo3::ffi::PyTypeObject,
        name: *mut pyo3::ffi::PyObject,
    ) -> *mut pyo3::ffi::PyObject;
}

/// Is the function `obj[key]` would run the one `pin` names?
/// `PyObject_GetItem` on a class with a Python-level `__getitem__` dispatches through the `mp_subscript`
/// slot, whose generic wrapper `slot_mp_subscript` (`typeobject.c:10096`) resolves the name with
/// `_PyType_Lookup` (`typeobject.c:3016` → 2939 → 2901) and calls a plain function found there **unbound** —
/// so this *is* the dispatch's own lookup.
/// Reading the name as an attribute instead breaks three ways: a metaclass `__getattribute__` can answer the
/// pin while the MRO entry is a subclass override; a metaclass `__getattr__` can fabricate the name so
/// `obj[key]` is a `TypeError` Django catches and renders `''` for; a custom descriptor answers
/// `__get__(None, cls)` for the attribute read while the slot binds `__get__(obj, cls)`. No MRO entry answers
/// `NULL` — where Django's own method lands too. `tests/test_loop_read_guard.py` is the control.
fn subscript_entry_is_pin(class: &Bound<'_, PyType>, pin: &Bound<'_, PyAny>) -> bool {
    type_entry_is_pin(class, intern!(class.py(), "__getitem__"), pin)
}

fn type_entry_is_pin(
    class: &Bound<'_, PyType>,
    name: &Bound<'_, PyString>,
    pin: &Bound<'_, PyAny>,
) -> bool {
    #[cfg(windows)]
    {
        mro_entry_is_pin(class, name, pin).unwrap_or(false)
    }
    #[cfg(not(windows))]
    {
        // SAFETY: `class` is a live type pointer, the name is interned,
        // and the *borrowed* result is compared on the next line with no Python code
        // running in between and no exception set on either answer.
        let entry = unsafe { _PyType_Lookup(class.as_type_ptr(), name.as_ptr()) };
        !entry.is_null() && entry == pin.as_ptr()
    }
}

static TYPE_GETATTRIBUTE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn type_getattribute(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    TYPE_GETATTRIBUTE
        .get_or_try_init(py, || {
            py.get_type::<PyType>()
                .getattr("__getattribute__")
                .map(Bound::unbind)
        })
        .map(|pin| pin.bind(py))
}

fn type_entry_is_absent(class: &Bound<'_, PyType>, name: &Bound<'_, PyString>) -> bool {
    #[cfg(not(windows))]
    {
        // SAFETY: live type and interned name, with the GIL held; the lookup
        // neither executes Python nor sets an exception.
        unsafe { _PyType_Lookup(class.as_type_ptr(), name.as_ptr()).is_null() }
    }
    #[cfg(windows)]
    {
        mro_entry_is_absent(class, name).unwrap_or(false)
    }
}

#[cfg(any(windows, all(test, not(feature = "extension-module"))))]
fn mro_entry_is_absent(class: &Bound<'_, PyType>, name: &Bound<'_, PyString>) -> PyResult<bool> {
    // SAFETY: the live type owns its MRO; dictionaries are borrowed
    // from live bases while the GIL is held.
    let ptr = unsafe { (*class.as_type_ptr()).tp_mro };
    if ptr.is_null() {
        return Ok(false);
    }
    let mro = unsafe { Bound::<PyAny>::from_borrowed_ptr(class.py(), ptr) };
    for base in mro.cast::<PyTuple>()?.iter() {
        let base = base.cast::<PyType>()?;
        let ptr = unsafe { pyo3::ffi::PyType_GetDict(base.as_type_ptr()) };
        let dict = unsafe { Bound::<PyAny>::from_borrowed_ptr(class.py(), ptr) };
        if dict.cast::<PyDict>()?.get_item(name)?.is_some() {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Read the actual MRO dictionaries without invoking metaclass hooks or descriptors.
#[cfg(all(test, not(feature = "extension-module")))]
fn mro_subscript_entry_is_pin(class: &Bound<'_, PyType>, pin: &Bound<'_, PyAny>) -> PyResult<bool> {
    mro_entry_is_pin(class, intern!(class.py(), "__getitem__"), pin)
}

#[cfg(any(windows, all(test, not(feature = "extension-module"))))]
fn mro_entry_is_pin(
    class: &Bound<'_, PyType>,
    name: &Bound<'_, PyString>,
    pin: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    // SAFETY: the live type holds its ready MRO tuple. The GIL is held and the
    // owned Bound keeps that tuple alive throughout the dictionary walk.
    let mro_ptr = unsafe { (*class.as_type_ptr()).tp_mro };
    if mro_ptr.is_null() {
        return Ok(false);
    }
    let mro = unsafe { Bound::<PyAny>::from_borrowed_ptr(class.py(), mro_ptr) };
    for base in mro.cast::<PyTuple>()?.iter() {
        let base = base.cast::<PyType>()?;
        // SAFETY: PyType_GetDict returns the live type's borrowed dictionary.
        // This public API is available on every supported Python (3.12+).
        let dict_ptr = unsafe { pyo3::ffi::PyType_GetDict(base.as_type_ptr()) };
        let dict = unsafe { Bound::<PyAny>::from_borrowed_ptr(class.py(), dict_ptr) };
        if let Some(entry) = dict.cast::<PyDict>()?.get_item(name)? {
            return Ok(entry.is(pin));
        }
    }
    Ok(false)
}

/// Django's `VariableDoesNotExist`, cached for the same reason.
static VARIABLE_DOES_NOT_EXIST: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Django's own `VariableDoesNotExist`, constructed the way Django does — `(msg, params)`, with the
/// message **left unformatted**: `VariableDoesNotExist.__str__` does the `%` (`base.py:109-110`), so this
/// does not call `repr(container)`. Formatting here would cross the seam once per failed lookup — the
/// hottest failure path in the engine, since `{{ missing }}` inside a `{% for %}` fails once per iteration —
/// and would produce a message Django never produces.
/// The class comes from `django_rust_templates.engine.base`, not a Rust type: a *different* exception class
/// would fail a differential comparison on the class rather than on the message.
fn variable_does_not_exist(py: Python<'_>, bit: &str, container: Py<PyAny>) -> PyResult<PyErr> {
    let class = VARIABLE_DOES_NOT_EXIST.import(
        py,
        "django_rust_templates.engine.base",
        "VariableDoesNotExist",
    )?;
    // A Rust 2-tuple becomes a Python tuple, the shape `params` needs: `self.msg % self.params`.
    Ok(PyErr::from_value(class.call1((
        "Failed lookup for key [%s] in %r",
        (bit, container),
    ))?))
}

/// Whether Django's outer handler substitutes for this exception instead of re-raising it —
/// `getattr(e, "silent_variable_failure", False)` (`base.py:1023`).
///
/// Read off the *instance* and defaulted to `False`: an exception class without the attribute is the
/// common case. A `getattr` that raises for any other reason propagates.
fn silent_variable_failure(py: Python<'_>, error: &PyErr) -> PyResult<bool> {
    match error.value(py).getattr("silent_variable_failure") {
        Ok(flag) => Ok(flag.is_truthy()?),
        // `getattr(e, name, False)` catches `AttributeError` from the lookup — including one raised by a
        // property — and nothing else. A truthiness that raises is Django's `if` raising too, hence `?`.
        Err(err) if err.is_instance_of::<PyAttributeError>(py) => Ok(false),
        Err(err) => Err(err),
    }
}

/// `context.template.engine.string_if_invalid` — Django's substitution for both `alters_data` and a
/// silent variable failure (`base.py:1023-1024`).
///
/// Three plain attribute reads, not a `getattr` chain with defaults: stock Django fails with
/// `AttributeError` when `context.template` is unbound and when the caller passed a mapping instead of a
/// `Context`, and a port substituting `""` there would be quiet where Django is loud.
///
/// Not cached against the context: `string_if_invalid` is per-engine, and a cache keyed on the context
/// would be a second source of truth.
fn string_if_invalid(context: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    Ok(context
        .getattr("template")?
        .getattr("engine")?
        .getattr("string_if_invalid")?
        .unbind())
}

/// The logger Django's outer handler writes to — reached by `logging.getLogger("django.template")`
/// rather than through `django.template.base.logger`: the two are the same object upstream (`base.py:94`),
/// and the call is what `assertLogs("django.template")` does.
///
/// Safe to cache: `assertLogs` replaces the *handlers* on the returned logger, not the logger object.
static TEMPLATE_LOGGER: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The three lines of Django's outer `except` that are not a decision about the value: read
/// `template_name`, log, and let the caller decide — `base.py:1015-1020`. The `logger.debug` message is
/// left as a `%`-template so `record.args` carries the bit and the template name.
fn log_resolution_failure(
    py: Python<'_>,
    context: &Bound<'_, PyAny>,
    bit: &str,
    error: &PyErr,
) -> PyResult<()> {
    // `getattr(context, "template_name", None) or "unknown"` (`base.py:1015`). A `getattr` with a default
    // swallows exactly `AttributeError`, so a `template_name` property raising `RuntimeError` propagates
    // out of the *handler*, as upstream's identical line does, one frame out.
    let name = match context.getattr("template_name") {
        Ok(name) => name,
        Err(err) if err.is_instance_of::<PyAttributeError>(py) => py.None().into_bound(py),
        Err(err) => return Err(err),
    };
    let template_name: Bound<'_, PyAny> = if name.is_truthy()? {
        name
    } else {
        "unknown".into_pyobject(py)?.into_any()
    };

    // Built from the exception **instance**, not `exc_info=True`: the failure was stashed by an arm and
    // handed back, never raised, so Python's exception state is the caller's. `True` would produce
    // `(None, None, None)`.
    let kwargs = PyDict::new(py);
    kwargs.set_item("exc_info", error.value(py))?;

    let logger = TEMPLATE_LOGGER.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        Ok(py
            .import("logging")?
            .call_method1("getLogger", ("django.template",))?
            .unbind())
    })?;
    logger.bind(py).call_method(
        "debug",
        (
            "Exception while resolving variable '%s' in template '%s'.",
            bit,
            template_name,
        ),
        Some(&kwargs),
    )?;
    Ok(())
}

/// Turn the port's verdict into Python's: hand back the value behind the handle, raise Django's
/// `VariableDoesNotExist`, or apply the host half of Django's outer `except Exception` to the exception it
/// stashed.
/// `context` is needed by the [`LookupOutcome::Escaped`] arm, the one place Django's handler reaches for
/// `context.template.engine`, and by both logging arms, which read `context.template_name`.
/// The log lives here rather than in the shell's `_resolve_lookup` because `_native.resolve` is the Rust mirror
/// of `_resolve_lookup`, and the differential harnesses exercise `silent_variable_failure` at the method
/// (`test_render_parity.py:616`, `test_resolve_callarm.py:86`) — a `_native.resolve` returning the raw failure
/// would diverge from the pinned method on exactly those rows.
fn outcome_to_py(
    py: Python<'_>,
    outcome: LookupOutcome,
    arena: &Rc<RefCell<Vec<Py<PyAny>>>>,
    pending: &Rc<Cell<Option<PyErr>>>,
    context: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let value_behind = |handle: CoreNodeRef| -> PyResult<Py<PyAny>> {
        arena
            .borrow()
            .get(handle.0 as usize)
            .ok_or_else(|| PyRuntimeError::new_err("handle escaped its resolve call"))
            .map(|value| value.clone_ref(py))
    };

    match outcome {
        LookupOutcome::Found(node) => value_behind(node),
        // The container's handle, which is what Django puts in `params`, already in the arena.
        //
        // **This arm logs too:** Django's `raise VariableDoesNotExist(...)` (`base.py:989`) is *inside*
        // the outer `try`, so a missing key reaches the handler at `base.py:1014` and logs before it
        // raises. Treating "missing" as "already decided" produces the right exception and *no log*.
        LookupOutcome::Missing { bit, container } => {
            let error = variable_does_not_exist(py, &bit, value_behind(container)?)?;
            log_resolution_failure(py, context, &bit, &error)?;
            Err(error)
        }
        // `pending` carries the exception because `LookupOutcome` derives `PartialEq` and has no room
        // for a `PyErr`; the bit is carried because the handler logs it and nothing below Rust can
        // recover it. Both branches of Django's handler, in Django's order: log, substitute for a
        // `silent_variable_failure`, else re-raise.
        LookupOutcome::Escaped { bit } => {
            let error = pending
                .take()
                .expect("Escaped is only returned after an arm stashed an exception");
            log_resolution_failure(py, context, &bit, &error)?;
            if silent_variable_failure(py, &error)? {
                string_if_invalid(context)
            } else {
                Err(error)
            }
        }
        LookupOutcome::Unimplemented(message) => Err(PyNotImplementedError::new_err(message)),
    }
}

/// The Python object `Variable.literal` holds, for a descriptor that is a literal rather than a path.
///
/// Everything but the quoted string is `attr_value_to_py`'s answer unchanged: `Int`/`BigInt` are Python
/// `int`s and `Float` a Python `float`, which is what `Variable.__init__` stores.
///
/// `SafeStr` is the exception: Django stores `mark_safe(unescape_string_literal(var))` (`base.py:906`),
/// and returns it from `resolve` unchanged — a plain `str` would be escape-able on a path Django never
/// escapes.
fn literal_to_py(py: Python<'_>, value: &AttrValue) -> PyResult<Py<PyAny>> {
    match value {
        AttrValue::SafeStr(text) => Ok(py
            .import("django.utils.safestring")?
            .getattr("mark_safe")?
            .call1((text.as_str(),))?
            .unbind()),
        other => attr_value_to_py(py, other),
    }
}

/// Resolve a dotted path against a context, for the differential test — separate from `render` so the
/// resolver can be compared against `Variable._resolve_lookup` on paths no template reaches, which is what
/// makes it *measured*. **No `parser` argument**, mirroring `_resolve_lookup`, which needs no filter registry.
/// It raises what `_resolve_lookup` raises and substitutes `string_if_invalid` where `_resolve_lookup` does:
/// `alters_data` and a `silent_variable_failure` (`base.py:1023-1024`).
/// Classified by `parse_variable`, as `Variable.__init__` classifies: `translate` → `Unimplemented`, checked
/// **before** the literal because `_("x")` sets both and returning the untranslated literal is the quiet wrong
/// answer; `lookups` → the port, the branch `Variable.resolve` reads first (`base.py:926-928`); `literal` →
/// the literal, without touching the context, taken only when there are no lookups.
/// **The two are not exclusive:** the trailing-dot numerals assign the float *and* take the string branch
/// (`template-core/src/parser/variable.rs:42-45`), so `2.` carries `literal = 2.0` and `lookups = ("2", "")`.
/// Reading the literal first prints `2.0` where Django prints `string_if_invalid`. The shipped path calls
/// [`resolve_lookups`]; this is kept for harnesses that compare a *path*.
#[pyfunction]
fn resolve(py: Python<'_>, path: &str, context: Py<PyAny>) -> PyResult<Py<PyAny>> {
    let desc = match template_core::parser::variable::parse_variable(path) {
        Ok(desc) => desc,
        Err(error) => return Err(parse_error(py, &error)?),
    };

    if desc.translate {
        return Err(PyNotImplementedError::new_err(
            "resolving a translated variable is M12's",
        ));
    }

    let Some(lookups) = desc.lookups.as_ref() else {
        return match &desc.literal {
            Some(literal) => literal_to_py(py, literal),
            // `Variable.__init__` guarantees one of the two is set: every branch of its `except
            // ValueError` assigns a literal or the lookups. An invariant of *our* parse, so a runtime
            // error and not a template error.
            None => Err(PyRuntimeError::new_err(
                "a parsed variable has neither a literal nor lookups",
            )),
        };
    };

    resolve_components(py, lookups, context)
}

/// `_resolve_lookup` with the path already **split**, which is the entry point the shipped render path uses.
/// `_resolve_lookup`'s whole input is the component tuple `Variable.__init__` produced, so this is the
/// *closer* mirror of the method and [`resolve`] is this plus a classification; both exist because the
/// classification is a different thing to test.
/// **No `translate` arm**, and its absence is the point: `_("x y")` sets `translate` *and* `lookups`, but
/// `Variable.resolve` applies the translation to the *value* one frame out and after `_resolve_lookup`
/// returns, so a translation is never this function's to apply.
/// The shell passes components rather than re-joining them into a path, because `".".join` re-parses `"2."`
/// as a number and renders `2.0` where Django renders `string_if_invalid`.
#[pyfunction]
fn resolve_lookups(
    py: Python<'_>,
    lookups: Vec<String>,
    context: Py<PyAny>,
) -> PyResult<Py<PyAny>> {
    resolve_components(py, &lookups, context)
}

/// The tail [`resolve`] and [`resolve_lookups`] share: one arena, one `pending`, one `PyTarget` root,
/// one `outcome_to_py`.
///
/// Factored because the two entry points differ **only** in how the components were obtained, and a
/// second copy would be a second place for the `PyTarget` contract to drift from
/// `LookupScope::resolve`, the third caller of the same shape.
fn resolve_components(
    py: Python<'_>,
    lookups: &[String],
    context: Py<PyAny>,
) -> PyResult<Py<PyAny>> {
    let arena = Rc::new(RefCell::new(Vec::new()));
    let pending = Rc::new(Cell::new(None));
    // Bound first, because `resolve_lookup` takes the context by value into the root target and
    // `outcome_to_py` needs it again for the one arm Django's handler reads context.template.engine
    // from. `Bound::clone` is a refcount bump, not a conversion.
    let context = context.bind(py);
    let context_rc = Rc::new(context.clone().unbind());
    let root = PyTarget::new(context.clone().unbind(), &arena, &pending, &context_rc);
    outcome_to_py(
        py,
        resolve_lookup(lookups, Box::new(root)),
        &arena,
        &pending,
        context,
    )
}

// ---------------------------------------------------------------------------
// M19 step 2 — the native render walk.
// ---------------------------------------------------------------------------
// A plan is built once at compile time by walking the parser's descriptor tree in parallel with the
// `NodeList` `materialise` built from it, carrying the materialised node at every step. That pairing is the
// invariant, asserted rather than assumed: a `class_name` that does not match the object at that index
// produces no plan at all and the template renders on the Python path unchanged, so a declined plan is a set
// difference the census shows, never a render that quietly differs.
// Four removals it makes against Django's render path, each measured: one sink (one `String`, one
// `SafeString` wrapper, as `NodeList.render` does at `engine/base.py:1204-1205`); a native loop frame
// (`ForNode.render`'s per-iteration `forloop` dict and loop-variable store measure ~0.11 and ~0.06 µs against
// a 0.222 fitted slope, and a body this walk renders cannot observe either); hoisted render-invariant work
// (`autoescape` and the escape function read once per render); and the escape fast path
// (`render_value_in_context`'s own `type(value) is str` arm) inline, so `{{ name }}` pays no Python frame.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

/// The node classes the walk renders itself, by the name `type(node).__name__` answers.
/// [`TEXT_NODE`] is the fourth and comes from `template-core`, because the descriptor names it too.
const VARIABLE_NODE: &str = "VariableNode";
const FOR_NODE: &str = "ForNode";
const IF_NODE: &str = "IfNode";
const INCLUDE_NODE: &str = "IncludeNode";
/// The one class whose body the walk reaches on its own hook rather than as a planned slot:
/// `BlockNode.render` renders the body nodelist through `NodeList._render_with_plan`, so the plan goes
/// on the nodelist — see [`plan_block_bodies`].
const BLOCK_NODE: &str = "BlockNode";
const WITH_NODE: &str = "WithNode";

/// The nesting depth past which the walk raises, rather than letting the Rust stack run out.
///
/// Stock Django raises `RecursionError` first, because the tree is built by Python's recursive parser
/// and CPython's limit is 1000 frames — measured on the pin: 200 levels of `{% for %}` parse, 500 raise.
/// A recursive Rust walk would instead run out of *stack*, which is not catchable.
const MAX_WALK_DEPTH: usize = 500;

/// The head of a `{{ … }}`: the value the filters, if any, are applied to. The two arms are
/// `Variable`'s own two shapes, exclusive exactly as Django's `(literal, lookups)` pair is.
enum Head {
    /// A lookup, resolved against the context through the same `Variable.resolve` the Python engine
    /// runs.
    Lookups {
        lookups: Vec<String>,
        keys: Arc<HashMap<String, Py<PyString>>>,
    },
    /// A literal, as the Python object Django's `Variable.literal` holds — `literal_to_py`'s answer,
    /// computed once at plan time. Django reuses its own `literal` object across renders the same way,
    /// and every value a literal can be is immutable.
    Value(Py<PyAny>),
}

/// Keep the Python lookup names with the compiled plan, including their cached
/// hashes. A render reuses them for subscripting and attribute lookup rather
/// than allocating the same Python strings for each node and loop iteration.
fn lookup_keys(py: Python<'_>, lookups: &[String]) -> Arc<HashMap<String, Py<PyString>>> {
    Arc::new(
        lookups
            .iter()
            .map(|bit| (bit.clone(), PyString::new(py, bit).unbind()))
            .collect(),
    )
}

/// One filter argument, in Django's three shapes.
///
/// `FilterExpression` stores each argument as `(lookup, value)` and `resolve` reads the flag
/// (`base.py:806-809`): `lookup=False` is a constant, `lookup=True` is `Variable(var_arg)`, resolved
/// against the context on every render.
enum FilterArg {
    /// `mark_safe(arg)`, folded to plan time. `arg` is a constant — `var_obj =
    /// Variable(constant).resolve({})` at parse time (`base.py:755`) — `mark_safe` is pure, and its
    /// answer cannot reach the output by identity because the render's last step builds a new string.
    Constant(Py<PyAny>),
    /// The `Variable` itself, resolved per render — Django's own `arg.resolve(context)`, on the object
    /// the parser built. The lookup path is deliberately not kept: a variable argument resolves against
    /// the context, which holds the enclosing loop's state on every iteration.
    Variable { variable: Py<PyAny> },
    /// A `Variable(var_arg)` that is a number, so `resolve` returns the literal rather than a context
    /// lookup — `value = self.literal` (`base.py:929-931`). It arrives as a `var_arg` because `var_chars` is
    /// `\w\.\+-` (`base.py:714`), digits included, so `|floatformat:2` parses `(True, Variable("2"))` and only
    /// the flag says otherwise (`base.py:686-687`). The value here is the **`int` 2**, not `"2"`.
    Literal(Py<PyAny>),
}

/// One `|name` or `|name:arg` the walk applies itself.
struct FilterStep {
    /// The name Rust's registry answers to. `run_filter` resolves the body by name — the *function* was
    /// already checked by handle when the plan was built, so this is not a second routing decision.
    name: String,
    /// Django's `arg_vals` element, or `None` for a filter with no argument.
    arg: Option<FilterArg>,
}

/// A `{{ … }}`: a head and the filters applied to it.
///
/// Also an `{% if %}` condition, which is a `FilterExpression` evaluated rather than emitted —
/// `TemplateLiteral.eval` is `self.value.resolve(context, ignore_failures=True)`
/// (`defaulttags.py:917-918`) — so the two share one shape and one resolution.
struct Emit {
    head: Head,
    /// In source order; empty for an unfiltered `{{ … }}`.
    steps: Vec<FilterStep>,
}

/// One `{% if %}`/`{% elif %}`/`{% else %}` the walk owns.
///
/// Every branch is an `{% else %}` or a `smartif` tree the walk evaluates itself; a branch whose
/// leaves it cannot plan declines the whole node.
struct IfPlan {
    /// `(condition, body)` in source order, `None` for the `{% else %}` — Django's own list, in Django's
    /// own order, with Django's own `None`.
    branches: Vec<(Option<Cond>, Vec<Slot>)>,
}

/// One node of a `{% if %}` condition — `smartif`'s tree (`engine/smartif.py`).
///
/// `do_if` builds this from thirteen operators and one leaf class, and `IfNode.render` asks the root
/// for a value it tests for truth. The leaf is always a `TemplateLiteral`: `TemplateIfParser` overrides
/// `create_var`, so `smartif`'s own `Literal` is unreachable through `{% if %}`.
enum Cond {
    /// A `TemplateLiteral` — `self.value.resolve(context, ignore_failures=True)`.
    Leaf(Emit),
    /// `not x`, the one prefix operator (`smartif.py`'s `prefix(8, …)`).
    Not(Box<Cond>),
    /// One of the twelve infix operators.
    Infix {
        op: Infix,
        first: Box<Cond>,
        second: Box<Cond>,
    },
}

/// `smartif.OPERATORS`' twelve infix keys.
///
/// `Or` and `And` are **not** comparisons and are kept apart from the rest at evaluation: their
/// lambdas are Python's own `or`/`and`, which short-circuit and yield the *operand*, not a bool.
#[derive(Clone, Copy)]
enum Infix {
    Or,
    And,
    In,
    NotIn,
    Is,
    IsNot,
    Eq,
    Ne,
    Gt,
    Ge,
    Lt,
    Le,
}

/// One node's native action.
///
/// `Act` and not `Step`, because `Step` is already `template-core`'s answer to one arm of a lookup and
/// this file implements that trait.
enum Act {
    /// A `TextNode`'s `s`, appended verbatim. Django never escapes a text node.
    Text(String),
    /// A `{{ … }}`.
    Emit(Emit),
    /// An `{% if %}`.
    If(Box<IfPlan>),
    /// A `{% for %}` the walk owns end to end.
    For(Box<ForPlan>),
    /// A scoped body, with Django's context manager and binding resolution.
    With(Box<WithPlan>),
    /// An `{% include %}` the walk resolves and renders itself.
    Include(Box<IncludePlan>),
    /// A third-party `simple_tag`, whose scaffolding the walk owns.
    SimpleTag(Box<SimpleTagPlan>),
    /// A node the walk does not render. `render_annotated` is called on it, which keeps Django's culprit
    /// protocol in one place.
    Delegate,
}

/// One planned node, with the materialised node it was planned from.
struct Slot {
    act: Act,
    /// The materialised Python node — two jobs, both needing the object rather than a name:
    /// `render_annotated` is the delegate target, and Django's exception protocol reads
    /// `e._culprit_node.token` off whatever node was rendering.
    node: Py<PyAny>,
    /// `type(node).__name__`, read once here so neither the render-time census nor the pairing assertion
    /// costs a Python attribute read per node.
    class_name: String,
}

/// A `{% for %}` this walk renders *itself* — the walk's own Rust loop, in `ForNode.render`'s order.
///
/// Built whenever both of the loop's nodelists plan and the loop has exactly one variable: an unpacking
/// loop writes one context entry per name and pops them per iteration, and the write order between them
/// is observable, so it is declined rather than half-done.
struct ForPlan {
    body: Vec<Slot>,
    /// The `{% empty %}` branch, planned normally. It is rendered **before** Django builds the dict
    /// (`engine/defaulttags.py:205-214`) and sees the enclosing context's `forloop` untouched.
    empty: Vec<Slot>,
    /// `self.sequence` — a `FilterExpression`, so the filters and the `ignore_failures` semantics are the
    /// transcribed Python ones.
    sequence: Py<PyAny>,
    /// `self.is_reversed`.
    is_reversed: bool,
    /// `self.loopvars[0]` — the name the loop writes into the context on every iteration, after the
    /// counters (`engine/defaulttags.py:248`).
    ///
    /// A `Py<PyString>` and not a `String`, because this is a dict key on a per-iteration path.
    /// `PyString::new` and not `PyString::intern` — the name comes from the template, and the
    /// interpreter's intern table is permanent.
    loopvar: Py<PyString>,
}

struct WithPlan {
    body: Vec<Slot>,
    nodelist: Py<PyAny>,
}

/// A third-party `{% simple_tag %}` the walk calls itself.
///
/// `SimpleNode.render` is scaffolding around one call — resolve each argument, call, escape,
/// optionally assign `as var` — and it is **Django's** code, not the tag author's: a library
/// registers with `django.template.Library`, so the node class is Django's even though the function
/// is not. Measured on bakerydemo, the scaffolding is ~2.7 µs a call against a body that is 0.42 µs
/// for `cell_classname` (89% scaffolding) and 21.6 µs for `pageurl` (12%).
struct SimpleTagPlan {
    /// The third-party function, called once per render with everything already resolved.
    func: Py<PyAny>,
    /// `takes_context`, which prepends the context to the positional arguments.
    takes_context: bool,
    /// One per positional argument, each a `FilterExpression` the walk resolves natively. This is
    /// where the win is: on the delegated path every argument is resolved in Python and our own
    /// transcription sends each lookup through `_native.resolve`, so each one pays a crossing that
    /// Django does not — which is why the delegated penalty grows with arity (1.17x at zero
    /// arguments, 1.62x at three).
    args: Vec<Emit>,
    /// `as var`: the output is assigned and the node renders `""`.
    target_var: Option<Py<PyAny>>,
}

/// A `{% include %}` with a constant name, no `with` bindings and no `only`.
///
/// **The name is all it holds.** Where the *template* is remembered is Django's business and stays
/// Django's: `IncludeNode.render` caches it in `render_context.dicts[0]` keyed by the node
/// (`loader_tags.py:197-201`), so its lifetime is the `Context`'s. A copy here would outlive a loader
/// reset and answer with a template the engine no longer serves.
struct IncludePlan {
    /// Read off the descriptor, not the node: `tags/include.rs` ran `construct_relative_path` before
    /// compiling the filter, so a relative name is already its final target.
    name: String,
}

/// The template name of an `{% include %}` the slot may render itself, or `None`.
///
/// `None` for every shape whose resolution touches the context, which is every shape the slot does
/// not reproduce exactly.
fn constant_include_name(desc: &NodeDesc) -> Option<&str> {
    if !matches!(
        find_attr(desc, "isolated_context"),
        Some(AttrValue::Bool(false))
    ) {
        return None;
    }
    if !matches!(find_attr(desc, "extra_context"), Some(AttrValue::Map(m)) if m.is_empty()) {
        return None;
    }
    let Some(AttrValue::FilterExpr(expr)) = find_attr(desc, "template") else {
        return None;
    };
    if !expr.filters.is_empty() {
        return None;
    }
    // `is_var` false is Django's `isinstance(var_obj, Variable)` being false.
    let (lookup, value) = &expr.var;
    if *lookup {
        return None;
    }
    let AttrValue::Variable(var) = value else {
        return None;
    };
    // `_("x")` translates at render time, and Django's `is_var` is false only when `lookups` is
    // falsy — `is_none()` and not `is_empty()`, so a hand-built descriptor with an empty list is
    // refused rather than trusted.
    if var.translate || var.lookups.is_some() {
        return None;
    }
    match var.literal.as_ref()? {
        // `SafeStr` and not `Str`: `parse_variable` wraps `unescape_string_literal`'s output in
        // `AttrValue::SafeStr` because Django wraps it in `mark_safe`
        // (`template-core/src/parser/variable.rs:70`). Matching on `Str` here compiles and never
        // fires.
        //
        // An empty literal is `"" or ()` (`loader_tags.py:187`), i.e. `select_template(())` and not
        // `("",)`.
        AttrValue::SafeStr(name) if !name.is_empty() => Some(name),
        _ => None,
    }
}

/// `IncludeNode.render`'s lookup, run against the `Context`'s own cache and nothing else.
///
/// The `setdefault` / `get` / store are Django's four lines. Writing the *store* is what makes
/// declining cheap: a target with no plan is found by the next render's `get` instead of being
/// selected a second time on the Python path.
///
/// A `TemplateDoesNotExist` out of `select_template` propagates: that is the exception Django raises
/// there, and the node's own `render_annotated` would have raised the same one.
fn resolve_include<'py>(
    py: Python<'py>,
    context: &Bound<'py, PyAny>,
    node: &Bound<'py, PyAny>,
    plan: &IncludePlan,
) -> PyResult<Bound<'py, PyAny>> {
    let name = PyTuple::new(py, [plan.name.as_str()])?;
    let render_context = context.getattr("render_context")?;
    let cache = render_context
        .getattr("dicts")?
        .get_item(0)?
        .call_method1("setdefault", (node, PyDict::new(py)))?;
    // `cache.get(name)`, not `cache[name]`: `cache` is a `Bound<PyAny>`, whose `get_item` is
    // `__getitem__` and raises `KeyError` on the first render of any include.
    let cached = cache.call_method1("get", (&name,))?;
    if !cached.is_none() {
        return Ok(cached);
    }
    let template = context
        .getattr("template")?
        .getattr("engine")?
        .call_method1("select_template", (&name,))?;
    cache.set_item(&name, &template)?;
    Ok(template)
}

/// The three `with` blocks an `{% include %}` puts a context through, entered here and left in
/// Python's order.
///
/// `IncludeNode.render` pushes a layer and *then* calls `template.render(context)`
/// (`loader_tags.py:210-211`), which is where `Template.render` enters `push_state` (`base.py:170`)
/// and, when the context is unbound, `bind_template` (`base.py:171-174`). So the layers nest
/// `push` → `push_state` → `bind_template`, and every one of them is a manager Python handed back.
///
/// **The managers, not their effects.** Each of the three is driven with `__enter__` and `__exit__`
/// rather than by transcribing the body behind it: `Context.push` returns a `ContextDict`, and
/// `push_state` and `bind_template` are generators, so what a `with` runs is whatever those return
/// and a subclass is free to return something else. Measured, a `RenderContext.push_state` override
/// that raises on entry, a `Context.push` whose manager raises on entry, and a `bind_template`
/// manager returning `True` each render one way through Python and another through a slot that
/// called the underlying methods instead.
///
/// **A manager is recorded only once its `__enter__` returned.** A `with` whose expression or
/// `__enter__` raises runs no `__exit__`, and neither does this — so `self` stays as it was and
/// whatever the manager already did to the context stands. That is the residue Python leaves too:
/// `ContextDict.__init__` appends before it returns, and `push_state`'s body runs up to its `yield`.
///
/// **The ordinary path is [`finish`](Self::finish), which is fallible, and `Drop` is the emergency.**
/// An `__exit__` in Python can raise anything, and a `Drop` cannot return that error — leaving it to
/// `Drop` would swallow it and render this include as though the template had returned.
struct ContextLayers<'py> {
    context: Bound<'py, PyAny>,
    /// `context.push()`'s `__exit__`, resolved and bound before the enter.
    pushed_exit: Option<Bound<'py, PyAny>>,
    /// `context.render_context.push_state(target)`'s, likewise.
    pushed_state_exit: Option<Bound<'py, PyAny>>,
    /// `context.bind_template(target)`'s, likewise.
    bound_template_exit: Option<Bound<'py, PyAny>>,
    /// Whether anything was ever travelling through the unwind — the body's failure, or one an
    /// `__exit__` raised. A failure that leaves cancels the body's *value*, and a manager outside it
    /// that later swallows the failure does not bring the value back: see [`finish`](Self::finish).
    tripped: bool,
    /// Whether [`finish`](Self::finish) already unwound — `Drop` then has nothing to do.
    done: bool,
}

impl<'py> ContextLayers<'py> {
    /// `with context.push(**values)` (`loader_tags.py:210`) — the expression, then the enter.
    ///
    /// The layer is pushed even though `values` is empty: [`constant_include_name`] refused every
    /// `with`, and `Context.__setitem__` writes to `dicts[-1]`, so the loop variable of a surrounding
    /// `{% for %}` would otherwise be visible inside the included template.
    ///
    /// **The two pushes are two calls and not one.** `IncludeNode.render` pushes this layer and
    /// *then* calls `template.render(context)`, which is where `push_state` pushes the second.
    /// Everything the slot reads to decide whether it can render natively is read by Python in one
    /// of those two gaps, so the slot has to be able to refuse in one of them — and what it refuses
    /// with is a call to the target that belongs in that gap, which is only possible while the gap
    /// is still open.
    fn push_context(context: &Bound<'py, PyAny>) -> PyResult<Self> {
        Self::push_values(context, None)
    }

    fn push_values(
        context: &Bound<'py, PyAny>,
        values: Option<&Bound<'py, PyDict>>,
    ) -> PyResult<Self> {
        let mut layers = Self {
            context: context.clone(),
            pushed_exit: None,
            pushed_state_exit: None,
            bound_template_exit: None,
            tripped: false,
            done: false,
        };
        layers.pushed_exit = Some(enter(context.call_method("push", (), values)?)?);
        Ok(layers)
    }

    /// `with context.render_context.push_state(self)` (`base.py:170`).
    ///
    /// **The render context is read here and not earlier**, because this is where Python reads it:
    /// `IncludeNode.render` has already pushed its own layer, so a `Context.push` override that
    /// rebinds `context.render_context` is seen.
    ///
    /// **The object's own `push_state` is called, and what it returns is entered.** `push_state` is
    /// `@contextmanager`-decorated, so the block is a `_GeneratorContextManager` and the body is a
    /// generator: the manager path is the only one that reproduces the body's `finally` running
    /// with the failure raised *at the `yield`* — which is what `sys.exception()` reads inside it,
    /// and what turns a `StopIteration` out of the body into `RuntimeError`. A subclass's `raise`,
    /// too, happens at the `__enter__`. Measured, this costs **546 ns** an include over calling the
    /// body directly; the difference is the manager and the generator, and there is no cheaper
    /// shape that keeps any of those three.
    fn enter_render_context(&mut self, template: &Bound<'py, PyAny>) -> PyResult<()> {
        let render_context = self.context.getattr("render_context")?;
        let manager = render_context.call_method1("push_state", (template,))?;
        self.pushed_state_exit = Some(enter(manager)?);
        Ok(())
    }

    /// `with context.bind_template(self)` (`base.py:172`), the branch `base.py:171` picks.
    ///
    /// **The context's own `bind_template`**, which `RequestContext` overrides to install its
    /// context processors (`context.py:254`), so the manager is entered rather than reproduced.
    /// `context.template_name = self.name` is the `with` body's line (`base.py:173`) and is written
    /// here, after the enter and before anything reads further.
    fn enter_bind_template(&mut self, template: &Bound<'py, PyAny>) -> PyResult<()> {
        let manager = self.context.call_method1("bind_template", (template,))?;
        self.bound_template_exit = Some(enter(manager)?);
        self.context
            .setattr("template_name", template.getattr("name")?)?;
        Ok(())
    }

    /// Leave the layers with `outcome` — what the body produced — in flight, and answer with what
    /// the render actually yields.
    ///
    /// **One exit, and it is the one Python would take.** The body's value and the body's failure
    /// travel through the same `__exit__`s; only the failure can be acted on, which is what
    /// [`exit`] does with it.
    ///
    /// **A manager that swallows the failure makes the frame return `None`.** A truthy `__exit__` is
    /// a `with` that does not propagate: execution continues after the block and the frame that
    /// block belongs to falls off its end. Measured, a `bind_template` manager returning `True`
    /// leaves stock Django rendering the include as `None` and raising `TypeError` in the parent's
    /// `''.join`; reporting the swallowed `TemplateDoesNotExist` instead is a different answer.
    ///
    /// **And a failure that is *raised* during the unwind cancels the value even when a manager
    /// outside it swallows that failure.** `self.tripped`, not `outcome.is_err()`, is the question:
    /// the body returning `"x"` and then an `__exit__` raising leaves stock Django with no value at
    /// all, so the include is `None` and the parent's join raises — measured against a
    /// `render_context.pop` that raises into an outer `context.push` that suppresses.
    fn finish(mut self, outcome: PyResult<Py<PyAny>>) -> PyResult<Py<PyAny>> {
        let py = self.context.py();
        self.done = true;
        let in_flight = match &outcome {
            Ok(_) => None,
            Err(error) => Some(error.clone_ref(py)),
        };
        self.tripped = in_flight.is_some();
        match self.pop_all(in_flight) {
            // An `__exit__` that raised replaces whatever was travelling through it, value or not.
            Some(error) => Err(error),
            // Nothing left in flight, but something was: the body never reached its end with a value.
            None if self.tripped => Ok(py.None()),
            None => outcome,
        }
    }

    /// Run the three `__exit__`s innermost first, and answer with the failure still in flight —
    /// `None` when one of them swallowed it.
    ///
    /// **Innermost first**, because that is how the `with` blocks nest: the binding block sits
    /// inside `push_state`, which sits inside `IncludeNode.render`'s own push.
    ///
    /// **A failure in one does not stop the others.** Each `with` has its own `__exit__` and Python
    /// runs the next one outwards whatever the previous did; one that raises leaves the failure it
    /// replaced as the new one's `__context__` — chained by Python, see [`exit`] — and the
    /// unwinding carries on.
    fn pop_all(&mut self, last: Option<PyErr>) -> Option<PyErr> {
        let py = self.context.py();
        let mut last = last;
        for exit_method in [
            self.bound_template_exit.take(),
            self.pushed_state_exit.take(),
            self.pushed_exit.take(),
        ]
        .into_iter()
        .flatten()
        {
            exit(&exit_method, &mut last, py);
            self.tripped |= last.is_some();
        }
        last
    }
}

/// `with manager:` — resolve the pair CPython's `BEFORE_WITH` resolves, then call the enter.
///
/// **Both names, off the type, bound, and *before* the enter.** `BEFORE_WITH` looks each up with
/// `_PyObject_LookupSpecial`, which skips the instance `__dict__` and binds through the descriptor,
/// and it resolves `__exit__` while `__enter__` has not run yet. Measured, a manager whose
/// `__enter__` replaces its class's `__exit__` is exited by the replacement under delegation and by
/// the original through a slot that looked the name up afterwards; one carrying the name only on
/// the instance is the mirror image.
///
/// The exit is what is kept — it holds the manager alive on its own — and what [`exit`] calls.
///
/// Resolve from the actual MRO dictionaries and bind through `tp_descr_get`, as
/// CPython does. This skips metaclass and instance hooks and avoids the Python
/// reflection calls that the old `class.getattr(...).__get__(...)` spelling made.
fn enter<'py>(manager: Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = manager.py();
    let class = manager.get_type();
    let bind = |name: &str| -> PyResult<Bound<'py, PyAny>> {
        let key = if name == "__enter__" {
            intern!(py, "__enter__")
        } else {
            intern!(py, "__exit__")
        };
        let Some(descriptor) = special_entry(&class, key)? else {
            let type_name = if py.version_info().minor >= 14 {
                class.fully_qualified_name()?.extract::<String>()?
            } else {
                // SAFETY: the live type owns this NUL-terminated name, under the GIL.
                unsafe { std::ffi::CStr::from_ptr((*class.as_type_ptr()).tp_name) }
                    .to_string_lossy()
                    .into_owned()
            };
            let missing = if py.version_info().minor >= 14 || name == "__exit__" {
                format!(" (missed {name} method)")
            } else {
                String::new()
            };
            return Err(PyTypeError::new_err(format!(
                "'{type_name}' object does not support the context manager protocol{missing}"
            )));
        };
        // Special lookup binds through the descriptor's type slot, ignoring
        // both metaclass hooks and a __get__ placed on the descriptor instance.
        // SAFETY: all three objects are live under the GIL; tp_descr_get returns
        // a new reference, or NULL with an exception set.
        let getter = unsafe { (*pyo3::ffi::Py_TYPE(descriptor.as_ptr())).tp_descr_get };
        match getter {
            Some(get) => unsafe {
                Bound::from_owned_ptr_or_err(
                    py,
                    get(descriptor.as_ptr(), manager.as_ptr(), class.as_ptr()),
                )
            },
            None => Ok(descriptor),
        }
    };
    // 3.14's LOAD_SPECIAL binds exit first; BEFORE_WITH on 3.12/3.13 binds enter first.
    let (enter_method, exit_method) = if py.version_info().minor >= 14 {
        let exit_method = bind("__exit__")?;
        (bind("__enter__")?, exit_method)
    } else {
        let enter_method = bind("__enter__")?;
        (enter_method, bind("__exit__")?)
    };
    enter_method.call0()?;
    Ok(exit_method)
}

fn special_entry<'py>(
    class: &Bound<'py, PyType>,
    name: &Bound<'py, PyString>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    #[cfg(not(windows))]
    {
        // SAFETY: live type and interned name under the GIL. The borrowed
        // entry is promoted to an owned reference before any descriptor runs.
        let ptr = unsafe { _PyType_Lookup(class.as_type_ptr(), name.as_ptr()) };
        Ok(if ptr.is_null() {
            None
        } else {
            Some(unsafe { Bound::from_borrowed_ptr(class.py(), ptr) })
        })
    }
    #[cfg(windows)]
    {
        // SAFETY: the live type owns its ready MRO, and each live base owns its dictionary.
        let ptr = unsafe { (*class.as_type_ptr()).tp_mro };
        if ptr.is_null() {
            return Ok(None);
        }
        let mro = unsafe { Bound::<PyAny>::from_borrowed_ptr(class.py(), ptr) };
        for base in mro.cast::<PyTuple>()?.iter() {
            let base = base.cast::<PyType>()?;
            let ptr = unsafe { pyo3::ffi::PyType_GetDict(base.as_type_ptr()) };
            let dict = unsafe { Bound::<PyAny>::from_borrowed_ptr(class.py(), ptr) };
            if let Some(entry) = dict.cast::<PyDict>()?.get_item(name)? {
                return Ok(Some(entry));
            }
        }
        Ok(None)
    }
}

/// Exit one layer with the failure in flight, if there is one, and **swallow** it if the manager
/// says to.
///
/// **Two paths, and they are not the same call.**
///
/// *Ordinary* — nothing in flight. `with` calls `__exit__(None, None, None)` for its side effect and
/// drops the result unread; CPython only reaches the truthiness test on the path an exception took.
/// Made here, so a manager whose return is an object with a raising `__bool__` is measured to render
/// fine on the happy path and to fail on the unhappy one. Nothing about the handled-exception state
/// changes on this path in CPython either, so there is nothing to stand in for.
///
/// *Exceptional* — through [`Stock::unwind`], which is Python. CPython reaches this `__exit__`
/// through `WITH_EXCEPT_START`, which runs the call with the travelling failure as the *handled*
/// exception; that state is what `sys.exception()` reads and what a newly raised exception's
/// `__context__` is chained onto, and PyO3 exposes no way to establish it — `PyErr::restore` sets
/// the *raised* exception, a different slot. So the call is made from Python and the chaining is
/// Python's, which is also why nothing here writes `__context__`. Measured, this file used to chain
/// `RuntimeError → KeyError → TemplateDoesNotExist` where delegation chains
/// `RuntimeError → TemplateDoesNotExist` — and `sys.exception()` answers `NoneType` here where
/// delegation answers the failure.
fn exit(exit_method: &Bound<'_, PyAny>, last: &mut Option<PyErr>, py: Python<'_>) {
    let Some(error) = last.as_ref().map(|error| error.clone_ref(py)) else {
        let none = py.None();
        if let Err(error) = exit_method.call1((none.clone_ref(py), none.clone_ref(py), none)) {
            *last = Some(error);
        }
        return;
    };
    // `stock` answered when the slot was taken, and a slot without it declines; see
    // [`Walk::renders_through`].
    let Some(unwind) = stock(py).map(|stock| stock.unwind.bind(py)) else {
        return;
    };
    let value = error.value(py);
    let args = (
        exit_method,
        error.get_type(py).into_any(),
        value.clone().into_any(),
        value
            .getattr("__traceback__")
            .unwrap_or_else(|_| py.None().into_bound(py)),
    );
    match unwind.call1(args) {
        // `UNWIND` answers with the truthiness of the return, tested where CPython tests it.
        Ok(returned) => match returned.is_truthy() {
            Ok(true) => *last = None,
            Ok(false) => {}
            Err(error) => *last = Some(error),
        },
        Err(error) => *last = Some(error),
    }
}

impl Drop for ContextLayers<'_> {
    /// The net, not the path: [`push_context`](ContextLayers::push_context) is the only way to get
    /// one of these and its caller unwinds through [`finish`](ContextLayers::finish) before it
    /// returns, so nothing reachable today leaves a layer for this to find.
    ///
    /// It stays because the alternative to a net is leaking a layer into a `Context` a later render
    /// reads, and because a `?` added between the two would find this rather than a compiler error.
    /// It cannot report what it finds — the error is already leaving by another route — so anything
    /// that *must* report goes through [`finish`](ContextLayers::finish).
    fn drop(&mut self) {
        if !self.done {
            let _ = self.pop_all(None);
        }
    }
}

/// How deep this crate may recurse into itself — native includes and walks together, *across
/// re-entrant walks*, on one thread.
///
/// **Not a limit on the template, and not a limit Django has.** It is there because Rust stack is not
/// something CPython's recursion limit protects: on 3.12 that limit counts Python frames and knows
/// nothing of the C stack underneath them, so deep Rust recursion is a `SIGBUS` and the process is
/// gone. Django, which recurses only through Python, raises `RecursionError` instead. Every frame
/// this counter refuses is a frame that runs in Python instead, where Django's own limit applies and
/// a runaway template raises exactly what it raises under Django.
///
/// **Refusing is per-frame and never per-render, so the ceiling costs no compatibility.** A finite
/// chain deeper than the budget renders in full — the head natively, the tail on Python — and the
/// census in
/// `IncludeSlotTests.test_the_shared_include_budget_runs_out_and_the_tail_delegates` is the row that
/// shows both halves. Nothing about the output depends on which side of the budget a level landed.
///
/// **The number is what fits the smallest thread this walk can meet, and it was bisected rather than
/// reasoned.** The Rust frames are the whole cost that is ours to bound; the Python frames are
/// CPython's, and they are the half Django runs alone. A self-include given a worker thread of
/// **96 KiB on CPython 3.12.9** and **128 KiB on 3.14.4** reaches CPython's recursion limit and
/// raises `RecursionError` — what Django raises — rather than dying. Below those the process dies on
/// the *pure-Python* path too, with no `WalkFrame` in the frame, so that is the floor beneath this
/// crate's rather than a floor of it. The bisection is `IncludeRecursionTests`' own shape and is
/// good to a few KiB: a thread's usable stack is page-rounded and carries a guard page, and
/// `stack_size` rounds again.
///
/// The main thread's 8 MiB is sixty times the larger floor, and every render in this repo's corpus
/// runs on it. The suite holds 512 KiB — four times that floor — as the smallest a caller is
/// promised.
const MAX_WALK_FRAMES: usize = 64;

thread_local! {
    static LIVE_FRAMES: Cell<usize> = const { Cell::new(0) };
}

/// One live frame of Rust recursion — a walk ([`render_plan`]) or a native include
/// ([`Walk::run_include`]) — released on every path out including an unwind.
///
/// **One counter for both, because both are the same stack.** A native include recurses inside the
/// walk that took it; a *declined* include hands back to Python, which re-enters `render_plan` and so
/// nests a whole new walk inside the frame of the old one's `delegate`. Counting only the includes
/// bounds the first recursion and leaves the second unbounded — a crash, not a wrong answer.
/// Measured on CPython 3.12.9, where the recursion limit counts Python frames and knows nothing of
/// the C stack underneath: with the include cap alone, a self-include died with `SIGBUS` on worker
/// threads of 128 KiB through 2 MiB, and survived only at 8 MiB. With both counted it raises
/// `RecursionError` from 96 KiB up. Counting both leaves two responses — decline the include,
/// decline the walk — as the only two things that can happen at the budget, and each one unwinds
/// Rust and puts Python back in charge of the depth.
///
/// See [`MAX_WALK_FRAMES`] for the number and what it is measured against.
struct WalkFrame;

impl WalkFrame {
    fn enter() -> Option<Self> {
        LIVE_FRAMES.with(|n| {
            if n.get() >= MAX_WALK_FRAMES {
                None
            } else {
                n.set(n.get() + 1);
                Some(WalkFrame)
            }
        })
    }
}

impl Drop for WalkFrame {
    fn drop(&mut self) {
        LIVE_FRAMES.with(|n| n.set(n.get() - 1));
    }
}

/// Which nodes the walk rendered and which it handed back, over every render since [`census_start`].
///
/// A process-global rather than a per-render return value, because the question is asked of a whole suite
/// run ("how many of the 1547 rendered without a delegate") and the answer is a set. Off by default, and
/// the walk's only reference to it is an `Option`, so the happy-path cost is one branch per node.
#[derive(Default, Clone)]
struct Census {
    handled: Vec<(String, usize)>,
    delegated: Vec<(String, usize)>,
    /// The characters that came back from `render_annotated`, summed at the call site. See
    /// [`census_take`] for why this is not a partition.
    delegated_len: usize,
    /// The filters the walk applied **itself**, by the name Rust's registry answers to, in
    /// first-sighting order.
    ///
    /// Nothing else can see this: once a filtered `{{ … }}` is [`Act::Emit`], the filter body is reached
    /// inside Rust with no seam crossing, so `tests/test_interposition.py`'s counter on the Python-facing
    /// `_native.filter` reads zero both on the fast route *and* when routing is broken. A count of what
    /// the walk applied is the only instrument that tells the two apart.
    filters: Vec<(String, usize)>,
}

/// `(name, count)`, ascending by first sighting. Named because `census_take` returns three of them and
/// clippy reads a tuple of tuples as a type nobody meant to write.
type CensusRows = Vec<(String, usize)>;

static CENSUS_ON: AtomicBool = AtomicBool::new(false);
static CENSUS: Mutex<Census> = Mutex::new(Census {
    handled: Vec::new(),
    delegated: Vec::new(),
    delegated_len: 0,
    filters: Vec::new(),
});

pub(crate) fn census_on() -> bool {
    CENSUS_ON.load(Ordering::Relaxed)
}

/// Python work executed **inside a filter body** since [`census_start`], over every render.
/// The meter [`Census::filters`] cannot replace: that one counts the filters the walk applied itself, a
/// statement about **routing**, and a Rust body is free to call back into Python — `escape`'s body is
/// `conditional_escape(value)`, `truncate`/`urlize`/`wrap` call Django's own implementations — so
/// `applied == [("escape", 1)]` with `crossed == []` is true while the escaping ran in Python.
/// A crossing is a Python object touched or Python executed: a call, an import, an attribute or item access,
/// a `str()`/`len()`/`isinstance`/truth test on a Python-owned object, and the `object(...)` conversion that
/// hands a value over at all. A `Value` that is a `str`/`int`/`float`/`bool`/`None`/`list` is answered by Rust
/// and reads zero (`upper` is a `to_str` plus a Rust table).
/// The window is the body, not the filter application: `run_filter_inner` brackets `(entry.run)(...)`, so the
/// seam's own work — `classify`, the `@stringfilter` coercion, `localtime`, `is_safe`, the loop's `mark_safe`
/// — is outside it. Only the walk's own applications are counted; the seam's two callers are [`filter`]
/// (Python asking) and `Walk::apply_step`, and only the second has a [`Census::filters`] entry to check against.
static BODY_CROSSINGS: AtomicUsize = AtomicUsize::new(0);

/// Add `count` crossings to the census's third meter.
///
/// Called once per **walk-applied** `run_filter` with the body's own count rather than once per crossing,
/// so the counter lives in the host (created per call and dropped with it) and this is the one place that
/// reaches the process-global. The Python arm does not call this: see [`BODY_CROSSINGS`].
pub(crate) fn census_body_crossings(count: usize) {
    if count > 0 && census_on() {
        BODY_CROSSINGS.fetch_add(count, Ordering::Relaxed);
    }
}

fn census_add(counts: &mut Vec<(String, usize)>, class_name: &str, count: usize) {
    match counts.iter_mut().find(|(name, _)| name == class_name) {
        Some(entry) => entry.1 += count,
        None => counts.push((class_name.to_string(), count)),
    }
}

/// `counts[name] += 1`, keeping first-seen order.
fn census_bump(census: &mut Option<Census>, class_name: &str, delegated: bool) {
    let Some(census) = census else { return };
    let counts = if delegated {
        &mut census.delegated
    } else {
        &mut census.handled
    };
    census_add(counts, class_name, 1);
}

/// `filters[name] += 1` for a filter the walk applied itself.
///
/// Called only on an **answer**: a refusal is the walk declining, and the application that follows happens
/// on the Python path, so counting it here would report coverage the walk does not have.
fn census_filter(census: &mut Option<Census>, name: &str) {
    let Some(census) = census else { return };
    census_add(&mut census.filters, name, 1);
}

fn census_merge(into: &mut Census, from: Census) {
    for (class_name, count) in from.handled {
        census_add(&mut into.handled, &class_name, count);
    }
    for (class_name, count) in from.delegated {
        census_add(&mut into.delegated, &class_name, count);
    }
    for (name, count) in from.filters {
        census_add(&mut into.filters, &name, count);
    }
    into.delegated_len += from.delegated_len;
}

/// Start (or clear) the census.
#[pyfunction]
fn census_start() {
    // Cleared before the flag is set, so a render starting between the two cannot write into the previous
    // run's counts.
    *CENSUS.lock().unwrap() = Census::default();
    BODY_CROSSINGS.store(0, Ordering::Relaxed);
    CENSUS_ON.store(true, Ordering::Relaxed);
}

/// Stop the census and return `(handled, delegated, delegated_len, filters, body_crossings)`.
/// `delegated_len` matters most on a page with inheritance. Counts alone are **containment-blind**: a
/// delegated `BlockNode` is one node and carries the whole page body into Python with it, so
/// `real-world/normal` reads 7 handled against 4 delegated while essentially *all* of its output came back
/// from Python. It is **not a partition and can exceed 100%** — a delegate nested inside another contributes
/// its own length *and* is part of its parent's, deliberately unclamped, so a row over 100% says the delegates
/// are nested.
/// `filters` **is** a partition of one thing: every filter application in a walked template is either counted
/// there or made on the Python path. `BODY_CROSSINGS` is the Python work done **inside** the bodies — zero is
/// a claim about a particular filter and input, not a property of the filter: `upper` on a `str` reads zero,
/// `upper` on an `int` reads one (the coercion is outside the window, so it is the body's `to_str` that
/// crosses), and `escape` reads one on any input.
#[pyfunction]
fn census_take() -> (CensusRows, CensusRows, usize, CensusRows, usize) {
    CENSUS_ON.store(false, Ordering::Relaxed);
    let census = CENSUS.lock().unwrap();
    (
        census.handled.clone(),
        census.delegated.clone(),
        census.delegated_len,
        census.filters.clone(),
        BODY_CROSSINGS.load(Ordering::Relaxed),
    )
}

/// The first attribute named `name`, or `None`.
fn find_attr<'a>(node: &'a NodeDesc, name: &str) -> Option<&'a AttrValue> {
    node.attrs.iter().find(|(n, _)| n == name).map(|(_, v)| v)
}

/// `django.utils.safestring.mark_safe`, cached once.
///
/// `FilterExpression.resolve` calls this on every render for every constant argument
/// (`base.py:807`), and the argument is compile-time (`base.py:768`), so the answer is reached once.
static MARK_SAFE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn mark_safe_fn(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    MARK_SAFE.import(py, "django.utils.safestring", "mark_safe")
}

/// `django_rust_templates.host.django_own_filter_names`, cached once.
///
/// The identity-based router the Python arm already uses — `{func: name}` for the handles in one
/// expression, `None` where the handle is not a Rust filter — read at **plan** time rather than per
/// application.
static DJANGO_OWN_FILTER_NAMES: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn django_own_filter_names_fn(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    DJANGO_OWN_FILTER_NAMES.import(py, "django_rust_templates.host", "django_own_filter_names")
}

/// `django.conf.settings`, cached once — the **object**, not `settings.USE_TZ`.
///
/// `override_settings` swaps attribute values on one `Settings` instance and installs a fresh object in
/// `django.conf.settings` for the duration, so a cached `bool`, or a cached module-level lookup, would
/// survive an override. The object plus a per-walk `getattr` sees both. See [`Walk::use_tz`].
static DJANGO_SETTINGS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The head of a `{{ … }}` from its descriptor, or `None` if this walk declines.
///
/// Declines a translated variable (`_("x")` resolves to a `gettext_lazy` proxy Python must build) and any
/// expression whose variable part is neither a lookup nor a literal.
///
/// Read from the **descriptor** rather than the materialised `FilterExpression`: the unfiltered case is
/// the common one — `real-world/large` is 320 variable nodes and every one is unfiltered — and four
/// Python attribute reads per node would be a per-*parse* cost, since `parse` mode compiles a fresh
/// template every iteration.
fn plan_head(py: Python<'_>, expr: &FilterExprDesc) -> PyResult<Option<Head>> {
    let (lookup, value) = &expr.var;
    let AttrValue::Variable(variable) = value else {
        return Ok(None);
    };
    if variable.translate {
        return Ok(None);
    }
    if *lookup {
        Ok(variable.lookups.as_ref().map(|lookups| Head::Lookups {
            lookups: lookups.clone(),
            keys: lookup_keys(py, lookups),
        }))
    } else {
        match variable.literal.as_ref() {
            Some(value) => Ok(Some(Head::Value(literal_to_py(py, value)?))),
            None => Ok(None),
        }
    }
}

/// The filter chain of a materialised `FilterExpression`, or `None` if this walk declines it.
///
/// Read from **the object Django renders from**, not the descriptor, because the descriptor carries
/// neither the *function* each name was bound to (`FilterCall::handle` is a reference-table index) nor
/// the already-resolved constant arguments (`base.py:768`) — materialisation is where Django puts both.
///
/// Declines the whole expression if any handle is not ours: a `{% load %}`ed filter has no Rust body, and
/// running Django's callable from here would be a second implementation of the `FilterExpression.resolve`
/// loop this file keeps to one. Handing the node back keeps Django's Python arm, and the census counts it.
fn plan_steps(py: Python<'_>, expression: &Bound<'_, PyAny>) -> PyResult<Option<Vec<FilterStep>>> {
    let filters = expression.getattr("filters")?;
    let count = filters.len()?;
    if count == 0 {
        return Ok(Some(Vec::new()));
    }
    let names = django_own_filter_names_fn(py)?.call1((&filters,))?;
    let names = names.cast::<PyDict>()?;
    let mut steps = Vec::with_capacity(count);
    for pair in filters.try_iter()? {
        let pair = pair?;
        let func = pair.get_item(0)?;
        // The answer is a *value* per handle, and Python's `None` is "not ours".
        // `django_own_filter_names` maps every handle in the expression at once, so an absent key is a
        // pairing failure rather than a filter that is not ours.
        let Some(name) = names.get_item(&func)? else {
            return Ok(None);
        };
        if name.is_none() {
            return Ok(None);
        }
        let args = pair.get_item(1)?;
        let arg = match args.len()? {
            0 => None,
            1 => {
                let operand = args.get_item(0)?;
                let lookup: bool = operand.get_item(0)?.extract()?;
                let value = operand.get_item(1)?;
                if lookup {
                    // `args.append((True, Variable(var_arg)))` (`base.py:770`) — the `var_arg` is either
                    // a real variable (lookups set) or a **number** (lookups `None`).
                    let lookups = value.getattr("lookups")?;
                    if lookups.is_none() {
                        // A `lookups`-less `Variable` with a literal has two shapes: a number
                        // (`base.py:887-895`, handed back by `resolve` without touching the context,
                        // `base.py:929-931`) and the quoted string at `base.py:906` — the latter ruled out
                        // because `var_chars` (`base.py:714`) cannot spell a leading quote. The test is
                        // *the numeric branch completed*, not "looks like a number": `Variable("2.")` sets
                        // `literal=2.0` **and** `lookups=('2','')`, so it resolves as a variable. A
                        // `literal` of `None` would be a `Variable` neither arm explains, so the node goes
                        // back rather than a `None` argument reaching a filter.
                        let literal = value.getattr("literal")?;
                        if literal.is_none() {
                            return Ok(None);
                        }
                        Some(FilterArg::Literal(literal.unbind()))
                    } else {
                        Some(FilterArg::Variable {
                            variable: value.unbind(),
                        })
                    }
                } else {
                    // `args.append((False, Variable(constant_arg).resolve({})))` (`base.py:768`) hands it
                    // already resolved, and `arg_vals.append(mark_safe(arg))` (`base.py:807`) makes the
                    // callable's argument the safe one.
                    Some(FilterArg::Constant(
                        mark_safe_fn(py)?.call1((value,))?.unbind(),
                    ))
                }
            }
            // Unreachable: `filter_re`'s argument group allows zero or one (`base.py:706-711`). Declined
            // rather than asserted, matching every other arm here.
            _ => return Ok(None),
        };
        steps.push(FilterStep {
            name: name.extract()?,
            arg,
        });
    }
    Ok(Some(steps))
}

/// A `{{ … }}`, or `None` if this walk declines it.
///
/// An unfiltered expression never touches Python; a filtered one must, because the handles are Python
/// objects (see [`plan_steps`]) and the descriptor alone cannot say whether they are ours. The head is
/// read from the descriptor either way — see [`plan_head`].
fn plan_emit(
    py: Python<'_>,
    desc: &FilterExprDesc,
    node: &Bound<'_, PyAny>,
) -> PyResult<Option<Emit>> {
    let Some(head) = plan_head(py, desc)? else {
        return Ok(None);
    };
    if desc.filters.is_empty() {
        return Ok(Some(Emit {
            head,
            steps: Vec::new(),
        }));
    }
    let expression = node.getattr("filter_expression")?;
    match plan_steps(py, &expression)? {
        Some(steps) => Ok(Some(Emit { head, steps })),
        None => Ok(None),
    }
}

/// An `{% if %}` condition, from its materialised `FilterExpression`.
///
/// The head is read from the object here rather than a descriptor, because a condition's descriptor is a
/// `NodeRef` into the reference table and nothing else — `IfConditionDesc::Literal` carries the
/// expression's *handle*, not its shape. A condition is one per branch rather than per node, so the
/// Python reads are paid on the rows that write `{% if %}`.
///
/// `ignore_failures=True` is not spelled here: it is a property of the caller — `TemplateLiteral.eval`
/// (`defaulttags.py:917-918`) — and changes only what a failed head means, which is [`Walk::run_if`]'s
/// question.
fn plan_condition(py: Python<'_>, expression: &Bound<'_, PyAny>) -> PyResult<Option<Emit>> {
    let head = if expression.getattr("is_var")?.is_truthy()? {
        let variable = expression.getattr("var")?;
        if variable.getattr("translate")?.is_truthy()? {
            return Ok(None);
        }
        let lookups = variable.getattr("lookups")?;
        if lookups.is_none() {
            // `Variable.resolve`'s own else: "We're dealing with a literal, so it's already been
            // resolved" — `value = self.literal` (`engine/base.py:1050-1052`). A `{% if %}` operand
            // written as a number or a quoted string is one of these, and declining it here was
            // sending every `{% if n > 1 %}` and `{% if s == 'x' %}` back to Python over an operand
            // the parse had already finished with.
            //
            // `translate` is refused above, which is what leaves this branch with nothing to do but
            // hand the value over. A `literal` of `None` is a `Variable` neither arm explains, so it
            // declines rather than emitting a `None` the parse never produced.
            let literal = variable.getattr("literal")?;
            if literal.is_none() {
                return Ok(None);
            }
            return Ok(plan_steps(py, expression)?.map(|steps| Emit {
                head: Head::Value(literal.unbind()),
                steps,
            }));
        }
        let lookups: Vec<String> = lookups.extract()?;
        Head::Lookups {
            keys: lookup_keys(py, &lookups),
            lookups,
        }
    } else {
        // `obj = self.var` — `FilterExpression.resolve`'s non-var arm (`base.py:801-802`).
        Head::Value(expression.getattr("var")?.unbind())
    };
    match plan_steps(py, expression)? {
        Some(steps) => Ok(Some(Emit { head, steps })),
        None => Ok(None),
    }
}

/// `{% if %}` as an [`IfPlan`], or `None` for a node `IfNode.render` must run.
///
/// Whether a branch is an `{% else %}` or a `TemplateLiteral` rather than a `smartif` operator is a
/// question about the parse tree, so it is asked of the descriptor; the conditions are read from the
/// object. A branch body that declines hands the whole node back — [`plan_slot`]'s convention, so a
/// half-planned `{% if %}` cannot render.
/// One condition, from the descriptor's tree paired with the object `do_if` built.
///
/// Both sides are walked together and asserted to agree, the rule `plan_slot` applies to nodes: the
/// descriptor says the shape, the materialised object supplies the `FilterExpression` at each leaf,
/// and a disagreement plans nothing rather than guessing. `id` is `smartif`'s own operator key —
/// `op.id = key`, assigned on each generated class at import.
fn plan_cond(
    py: Python<'_>,
    desc: &IfConditionDesc,
    node: &Bound<'_, PyAny>,
) -> PyResult<Option<Cond>> {
    match desc {
        IfConditionDesc::Literal { .. } => {
            let value = match node.getattr("value") {
                Ok(value) => value,
                // Not a `TemplateLiteral`: the pairing is wrong, so nothing here is planned.
                Err(_) => return Ok(None),
            };
            Ok(plan_condition(py, &value)?.map(Cond::Leaf))
        }
        IfConditionDesc::Operator { key, first, second } => {
            // The object must be the operator the descriptor names.
            match node.getattr("id") {
                Ok(id) if id.extract::<String>().is_ok_and(|id| &id == key) => {}
                _ => return Ok(None),
            }
            let Some(first_node) = node.getattr("first").ok() else {
                return Ok(None);
            };
            let Some(first) = plan_cond(py, first, &first_node)? else {
                return Ok(None);
            };
            if key == "not" {
                // `nud` sets `self.second = None` (`smartif.py`), and the descriptor carries the
                // same absence; a `not` with a second operand is not the tree `do_if` builds.
                if second.is_some() {
                    return Ok(None);
                }
                return Ok(Some(Cond::Not(Box::new(first))));
            }
            let op = match key.as_str() {
                "or" => Infix::Or,
                "and" => Infix::And,
                "in" => Infix::In,
                "not in" => Infix::NotIn,
                "is" => Infix::Is,
                "is not" => Infix::IsNot,
                "==" => Infix::Eq,
                "!=" => Infix::Ne,
                ">" => Infix::Gt,
                ">=" => Infix::Ge,
                "<" => Infix::Lt,
                "<=" => Infix::Le,
                // An operator this walk does not know. `smartif.OPERATORS` has thirteen keys and the
                // twelve above plus `not` are all of them, so this is a table that grew.
                _ => return Ok(None),
            };
            let (Some(second_desc), Some(second_node)) = (second, node.getattr("second").ok())
            else {
                return Ok(None);
            };
            let Some(second) = plan_cond(py, second_desc, &second_node)? else {
                return Ok(None);
            };
            Ok(Some(Cond::Infix {
                op,
                first: Box::new(first),
                second: Box::new(second),
            }))
        }
    }
}

fn plan_if(py: Python<'_>, desc: &NodeDesc, node: &Bound<'_, PyAny>) -> PyResult<Option<IfPlan>> {
    let Some(AttrValue::List(branches)) = find_attr(desc, "conditions_nodelists") else {
        return Ok(None);
    };
    let materialised = node.getattr("conditions_nodelists")?;
    // The parse wrote the branches and the bodies in step, so a disagreement is a pairing failure rather
    // than a template this walk cannot plan.
    if branches.len() != desc.nodelists.len() || materialised.len()? != branches.len() {
        return Ok(None);
    }
    let mut planned = Vec::with_capacity(branches.len());
    for (index, branch) in branches.iter().enumerate() {
        let AttrValue::Tuple(items) = branch else {
            return Ok(None);
        };
        let [condition_desc, body_ref] = items.as_slice() else {
            return Ok(None);
        };
        let pair = materialised.get_item(index)?;
        let condition = match condition_desc {
            // `conditions_nodelists.append((None, nodelist))` — `do_if`'s own `{% else %}`
            // (`defaulttags.py:1009`).
            AttrValue::None => None,
            AttrValue::IfCondition(tree) => {
                let condition = pair.get_item(0)?;
                if condition.is_none() {
                    return Ok(None);
                }
                match plan_cond(py, tree.as_ref(), &condition)? {
                    Some(cond) => Some(cond),
                    None => return Ok(None),
                }
            }
            _ => return Ok(None),
        };
        let AttrValue::NodeListRef(body_index) = body_ref else {
            return Ok(None);
        };
        // `{% if %}` is the one tag with **positional-only** nodelists: `IfNode` holds its branch bodies
        // inside `conditions_nodelists`, so the descriptor carries N unnamed nodelists and the branch
        // names its own by position.
        let Some((_, body_desc)) = desc.nodelists.get(*body_index) else {
            return Ok(None);
        };
        let Some(body) = plan_list(py, body_desc, &pair.get_item(1)?)? else {
            return Ok(None);
        };
        planned.push((condition, body));
    }
    Ok(Some(IfPlan { branches: planned }))
}

/// `{% for %}` as a [`ForPlan`], or `None` for a loop `ForNode.render` must run.
fn plan_for(py: Python<'_>, desc: &NodeDesc, node: &Bound<'_, PyAny>) -> PyResult<Option<ForPlan>> {
    let Some((_, body_desc)) = desc
        .nodelists
        .iter()
        .find(|(name, _)| name == "nodelist_loop")
    else {
        return Ok(None);
    };
    // Always present on the descriptor, even when the tag has no `{% empty %}`: Django's
    // `ForNode.__init__` substitutes an empty `NodeList` and the shell materialises one.
    let Some((_, empty_desc)) = desc
        .nodelists
        .iter()
        .find(|(name, _)| name == "nodelist_empty")
    else {
        return Ok(None);
    };
    let python_body = node.getattr("nodelist_loop")?;
    let python_empty = node.getattr("nodelist_empty")?;
    let Some(body) = plan_list(py, body_desc, &python_body)? else {
        return Ok(None);
    };
    let Some(empty) = plan_list(py, empty_desc, &python_empty)? else {
        return Ok(None);
    };
    let loopvars: Vec<String> = node.getattr("loopvars")?.extract()?;
    // An unpacking loop writes one context entry per name, each a frame of its own. Declined rather than
    // half-done.
    if loopvars.len() != 1 {
        return Ok(None);
    }
    Ok(Some(ForPlan {
        body,
        empty,
        sequence: node.getattr("sequence")?.unbind(),
        is_reversed: node.getattr("is_reversed")?.extract()?,
        loopvar: PyString::new(py, &loopvars[0]).unbind(),
    }))
}

/// One materialised node, planned against the descriptor it was built from.
///
/// `None` means the pairing does not hold, which aborts the whole plan: a plan right about most nodes and
/// wrong about one is a render that differs from Django's in a way no test would name.
/// `engine/_stock.py`'s `constant_text`, cached.
static CONSTANT_TEXT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The text a node will always render, or `None` for everything else.
///
/// Four built-ins answer this: `{% comment %}` and `{% load %}` render `""`,
/// `{% verbatim %}` the content it was compiled with, `{% templatetag %}` one of eight
/// constants. Delegating them spends a Python frame per render to return a value the compile
/// already knew — measured against stock Django, `{% verbatim %}` 2.20x, `{% comment %}` 2.02x,
/// `{% templatetag %}` 1.80x, and `{% load %}` is 119 of the 709 delegated nodes over eleven
/// real pages.
///
/// **The decision is Python's, not a class-name match here.** `engine/_stock.py` holds the four
/// classes and their stock `render` functions, read at import before anything can rebind them,
/// and answers `None` for a subclass or a patched body. A name comparison in Rust would accept
/// both. Called once per node at plan time, never per render.
fn constant_text(py: Python<'_>, node: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let func = CONSTANT_TEXT.import(py, "django_rust_templates.engine._stock", "constant_text")?;
    let answer = func.call1((node,))?;
    if answer.is_none() {
        return Ok(None);
    }
    Ok(Some(answer.extract::<String>()?))
}

/// `engine/_stock.py`'s `simple_tag_parts`, cached.
static SIMPLE_TAG_PARTS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// A third-party `simple_tag` the walk can call itself, or `None`.
///
/// **The gate is Python's**, in `engine/_stock.py`: it answers `None` for anything that is not
/// exactly Django's `SimpleNode` with its own `render` — a subclass such as `SimpleBlockNode`, a
/// rebound body, or a shape with keyword arguments. Rust then plans the arguments, and a single
/// argument it cannot plan declines the whole node, because a half-planned call would resolve some
/// operands here and some in Python.
fn plan_simple_tag(py: Python<'_>, node: &Bound<'_, PyAny>) -> PyResult<Option<SimpleTagPlan>> {
    let parts = SIMPLE_TAG_PARTS
        .import(
            py,
            "django_rust_templates.engine._stock",
            "simple_tag_parts",
        )?
        .call1((node,))?;
    if parts.is_none() {
        return Ok(None);
    }
    let func = parts.get_item(0)?.unbind();
    let takes_context: bool = parts.get_item(1)?.extract()?;
    let raw_args = parts.get_item(2)?;
    let target = parts.get_item(3)?;
    let mut args = Vec::with_capacity(raw_args.len()?);
    for expression in raw_args.try_iter()? {
        match plan_condition(py, &expression?)? {
            Some(emit) => args.push(emit),
            None => return Ok(None),
        }
    }
    Ok(Some(SimpleTagPlan {
        func,
        takes_context,
        args,
        target_var: if target.is_none() {
            None
        } else {
            Some(target.unbind())
        },
    }))
}

static WITH_NODE_IS_STOCK: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static WITH_VALUES: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static WITH_LOOP_IS_STOCK: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn plan_with(
    py: Python<'_>,
    desc: &NodeDesc,
    node: &Bound<'_, PyAny>,
) -> PyResult<Option<WithPlan>> {
    if !WITH_NODE_IS_STOCK
        .import(
            py,
            "django_rust_templates.engine._stock",
            "with_node_is_stock",
        )?
        .call1((node,))?
        .is_truthy()?
    {
        return Ok(None);
    }
    let Some((_, body_desc)) = desc.nodelists.iter().find(|(name, _)| name == "nodelist") else {
        return Ok(None);
    };
    let nodelist = node.getattr("nodelist")?;
    let Some(slots) = plan_list(py, body_desc, &nodelist)? else {
        return Ok(None);
    };
    Ok(Some(WithPlan {
        body: slots,
        nodelist: nodelist.unbind(),
    }))
}

fn plan_slot(py: Python<'_>, desc: &NodeDesc, node: &Bound<'_, PyAny>) -> PyResult<Option<Slot>> {
    let class_name: String = node.get_type().name()?.to_string();
    // `HOST_NODE` is the one descriptor whose class name is not the object's: a user tag's compile
    // function returned that object, so the descriptor could only name the slot (see `desc.rs`).
    if desc.class_name != class_name && desc.class_name != HOST_NODE {
        return Ok(None);
    }
    let act = if desc.class_name == TEXT_NODE {
        match find_attr(desc, "s") {
            Some(AttrValue::Str(text)) => Act::Text(text.clone()),
            _ => Act::Delegate,
        }
    } else if class_name == VARIABLE_NODE {
        match find_attr(desc, "filter_expression") {
            Some(AttrValue::FilterExpr(expr)) => match plan_emit(py, expr, node)? {
                Some(emit) => Act::Emit(emit),
                None => Act::Delegate,
            },
            _ => Act::Delegate,
        }
    } else if class_name == FOR_NODE {
        match plan_for(py, desc, node)? {
            Some(plan) => Act::For(Box::new(plan)),
            None => Act::Delegate,
        }
    } else if class_name == WITH_NODE {
        match plan_with(py, desc, node)? {
            Some(plan) => Act::With(Box::new(plan)),
            None => Act::Delegate,
        }
    } else if class_name == IF_NODE {
        match plan_if(py, desc, node)? {
            Some(plan) => Act::If(Box::new(plan)),
            None => Act::Delegate,
        }
    } else if class_name == INCLUDE_NODE {
        match constant_include_name(desc) {
            Some(name) => Act::Include(Box::new(IncludePlan {
                name: name.to_owned(),
            })),
            None => Act::Delegate,
        }
    } else if let Some(plan) = plan_simple_tag(py, node)? {
        Act::SimpleTag(Box::new(plan))
    } else if let Some(text) = constant_text(py, node)? {
        // The node's whole output, known before any context exists. `Act::Text` and not a
        // removed slot: the slot carries the node, which the culprit protocol reads when a
        // *later* node raises.
        Act::Text(text)
    } else {
        Act::Delegate
    };
    Ok(Some(Slot {
        act,
        node: node.clone().unbind(),
        class_name,
    }))
}

/// A whole nodelist in document order, or `None` if any node declines.
fn plan_list(
    py: Python<'_>,
    desc: &NodeListDesc,
    nodelist: &Bound<'_, PyAny>,
) -> PyResult<Option<Vec<Slot>>> {
    if nodelist.len()? != desc.nodes.len() {
        return Ok(None);
    }
    let mut slots = Vec::with_capacity(desc.nodes.len());
    for (index, node_desc) in desc.nodes.iter().enumerate() {
        let node = nodelist.get_item(index)?;
        let Some(slot) = plan_slot(py, node_desc, &node)? else {
            return Ok(None);
        };
        slots.push(slot);
    }
    Ok(Some(slots))
}

/// A parsed template's render plan, held in Rust.
///
/// Built by `compile_template` and attached to the `Template` the shell builds; `Template._render` hands
/// it back to [`render_plan`]. A template with no plan renders on the Python path exactly as before, so a
/// plan this builder declines is a lost optimisation and never a behaviour change.
#[pyclass]
struct NativePlan {
    slots: Vec<Slot>,
}

/// A plan for one nodelist, already boxed as the Python object the body's render reads, or `None` if
/// the pairing does not hold.
fn make_plan(
    py: Python<'_>,
    desc: &NodeListDesc,
    nodelist: &Bound<'_, PyAny>,
) -> PyResult<Option<Py<PyAny>>> {
    match plan_list(py, desc, nodelist)? {
        Some(slots) => Ok(Some(
            NativePlan { slots }.into_pyobject(py)?.into_any().unbind(),
        )),
        None => Ok(None),
    }
}

/// Attach a plan to every `{% block %}` body in the tree.
/// A `{% block %}` body is reached through `BlockNode.render`, a plain Python `NodeList.render` the walk never
/// enters, and on a page with inheritance that is where the whole render lives: measured,
/// `real-world/normal` returns **198.8%** of its output from delegates and **99.0%** of its wall time is
/// inside the one delegated `ExtendsNode` frame.
/// This pass attaches; `NodeList._render_with_plan` reads — that method rather than `NodeList.render` because
/// the latter is on the parity harness's compared path, where an arm inside it would make the comparison Rust
/// against Rust (clause (4) of `scripts/check_transcriptions.py`).
/// Descent pairs the descriptor tree with the `NodeList` materialised from it by the attribute name each
/// descriptor's nodelist was declared with. `IfNode` is the one class whose branches cannot be reached by name:
/// it holds them inside `conditions_nodelists` at `conditions_nodelists[i][1]`, and a `{% block %}` inside an
/// `{% if %}` branch is reachable only that way, since `plan_if` cannot plan a branch containing a
/// `BlockNode`. The pairing is by position, so a wrong pair is not possible; a missing attribute or a short
/// list is skipped rather than raised, because an optimisation must not turn a shape it does not understand
/// into a compile error. `HOST_NODE` descriptors carry no nodelists, so a host tag's body is not descended
/// into.
fn plan_block_bodies(
    py: Python<'_>,
    desc: &NodeListDesc,
    nodelist: &Bound<'_, PyAny>,
) -> PyResult<()> {
    if nodelist.len()? != desc.nodes.len() {
        return Ok(());
    }
    for (index, node_desc) in desc.nodes.iter().enumerate() {
        let node = nodelist.get_item(index)?;
        let class_name: String = node.get_type().name()?.to_string();
        // The same pairing rule `plan_slot` applies: a descriptor and a node that disagree are not about
        // each other, so neither this node's body nor its children may be planned from it.
        if node_desc.class_name != class_name && node_desc.class_name != HOST_NODE {
            continue;
        }
        let mut positional = 0usize;
        for (name, child_desc) in &node_desc.nodelists {
            let child = if name.is_empty() {
                let branch = match if_branch(&node, positional) {
                    Some(branch) => branch,
                    None => {
                        positional += 1;
                        continue;
                    }
                };
                positional += 1;
                branch
            } else if node.hasattr(name.as_str())? {
                node.getattr(name.as_str())?
            } else {
                continue;
            };
            if class_name == BLOCK_NODE {
                if let Some(plan) = make_plan(py, child_desc, &child)? {
                    child.setattr("_native_plan", plan)?;
                }
            }
            plan_block_bodies(py, child_desc, &child)?;
        }
    }
    Ok(())
}

/// One `IfNode` branch nodelist, by position, or `None` for a node that is not an `IfNode` — or one
/// whose shape is not what `tags/if.rs` builds.
fn if_branch<'py>(node: &Bound<'py, PyAny>, index: usize) -> Option<Bound<'py, PyAny>> {
    let branches = node.getattr("conditions_nodelists").ok()?;
    let branch = branches.get_item(index).ok()?;
    branch.get_item(1).ok()
}

/// `Node.render_annotated`'s `except` arm (`engine/base.py:1135-1164`), for a node this walk rendered
/// itself.
///
/// Django's two guards are both `hasattr` — first-writer-wins — so the innermost failing node names the
/// culprit and every enclosing one leaves it alone. The walk reproduces that by *being* the nesting:
/// each native step's error arm runs in a Rust frame nested inside its caller's.
///
/// Everything is read at error time and nothing on the happy path, Django's shape too.
fn annotate_culprit(py: Python<'_>, context: &Bound<'_, PyAny>, slot: &Slot, error: &PyErr) {
    // The walk is only reachable through `Template._render` with a bound template, so the debug/culprit
    // reads cannot fail the way they would for a hand-called `render_annotated`. If one does, the original
    // exception propagates unannotated — a smaller failure than replacing it with the describe error.
    let error_value = error.value(py);
    let annotate = || -> PyResult<()> {
        let debug: bool = context
            .getattr("template")?
            .getattr("engine")?
            .getattr("debug")?
            .extract()?;
        if !debug {
            return Ok(());
        }
        if !error_value.hasattr("_culprit_node")? {
            error_value.setattr("_culprit_node", slot.node.bind(py))?;
        }
        if error_value.hasattr("template_debug")? {
            return Ok(());
        }
        let template = context.getattr("render_context")?.getattr("template")?;
        let culprit = error_value.getattr("_culprit_node")?;
        if !template.getattr("origin")?.eq(culprit.getattr("origin")?)? {
            return Ok(());
        }
        let info = template.call_method1(
            "get_exception_info",
            (error_value, culprit.getattr("token")?),
        )?;
        error_value.setattr("template_debug", info)?;
        Ok(())
    };
    let _ = annotate();
}

/// Whether a planned node rendered itself or handed back to Python.
///
/// `Refused` has exactly one route and is not an error: a filter whose answer is a lone surrogate answers
/// the seam's sentinel ([`cannot_hold`]) rather than raising, because the value is one CPython holds and
/// this walk's accumulator is a Rust `String`. The node is then rendered by `render_annotated`, as Django
/// would have done had the walk never owned it.
enum Rendered {
    Done,
    Refused,
}

/// What one filter application did.
enum StepOutcome {
    Value(Py<PyAny>),
    Refused,
}

/// What `FilterExpression.resolve` did with a **failed head** — three answers, three places to go.
///
/// The distinction is not cosmetic: the truthy `string_if_invalid` returns *early*, so the filter chain
/// never runs, while the falsy one assigns and **falls through**. `""` is the default setting, so the
/// second arm is the common one — it is what makes `{{ missing|default:"x" }}` render `"x"` rather than
/// `""`.
enum Absorbed {
    /// `string_if_invalid` is truthy, so `resolve` returned it — `return string_if_invalid`
    /// (`base.py:793-798`).
    Final(Py<PyAny>),
    /// Falsy: `obj = string_if_invalid` and the chain runs on it (`base.py:800`).
    Continue(Py<PyAny>),
    /// `VariableNode.render`'s `UnicodeDecodeError` arm: the node renders `""` and
    /// `render_value_in_context` never runs (`engine/base.py:1360-1363`).
    Nothing,
}

/// The render-invariant state of one walk.
struct Walk<'py> {
    py: Python<'py>,
    context: Bound<'py, PyAny>,
    /// `django_rust_templates.engine.base.render_value_in_context`, fetched once. Django's function, not a
    /// Rust reimplementation: it is the transcription the transcription guard pins, so the fallback path
    /// is byte-identical by construction rather than by a test that keeps pace.
    escape: Bound<'py, PyAny>,
    scope: LookupScope,
    /// `context.autoescape`, hoisted — and refreshed after every delegate, because
    /// `AutoEscapeControlNode` writes that attribute **in place** on the context object rather than
    /// restoring it later.
    autoescape: bool,
    /// `template_localtime`'s `use_tz` argument (`django/utils/timezone.py:146-161`), filled on first
    /// use.
    ///
    /// **Lazy, and that is the design.** `Context.use_tz` is `None` unless `{% localtime %}` or
    /// `{% timezone %}` set it, and the rule for `None` is `settings.USE_TZ` — an eager value would put a
    /// `django.conf` import on every walked render, including ones with no date filter. At 6.0.9, 2 of the
    /// 57 builtins declare `expects_localtime`, and `run_filter` reads this for those two only. A delegate
    /// clears it, because a delegate is the one thing that can change `context.use_tz`.
    use_tz: Option<bool>,
    /// The output, as a Rust `String` — the whole point of the walk, and Python's `str` is not a superset
    /// of it. See [`Walk::push_python`].
    out: String,
    /// Set once a piece will not fit `out`; `Some` means the accumulator is Python's from then on and the
    /// pieces are here.
    spill: Option<Vec<Py<PyAny>>>,
    /// How many nodes have been pushed. Read by [`Walk::push_python`] as the **node index** of the piece it
    /// is holding, the ordinal `NodeList.render`'s `"".join` would name for it.
    nodes: usize,
    /// Scoped nodelists preserve their own join boundaries, including the
    /// loop-wide join spanning all iterations.
    scoped_joins: usize,
    census: Option<Census>,
    /// The implementations an included target must still answer to, read once per process by
    /// [`stock`]. `None` when they could not be read, in which case every include declines. See
    /// [`Walk::renders_through`], which is the only thing standing between an include and
    /// rendering a target whose `render` is not what the slot is standing in for.
    bypassed: Option<&'static Stock>,
}

impl<'py> Walk<'py> {
    /// Render one node list, in `NodeList.render`'s order, into this walk's accumulator.
    ///
    /// `depth` is the walk's own nesting count, checked against [`MAX_WALK_DEPTH`] — the one piece of state
    /// a body inherits from its parent.
    fn run(&mut self, slots: &[Slot], depth: usize) -> PyResult<()> {
        let py = self.py;
        if depth > MAX_WALK_DEPTH {
            return Err(pyo3::exceptions::PyRecursionError::new_err(
                "maximum recursion depth exceeded",
            ));
        }
        for slot in slots {
            match &slot.act {
                Act::Text(text) => {
                    self.push_text(text)?;
                    census_bump(&mut self.census, &slot.class_name, false);
                }
                // No culprit annotation here: `render_annotated` *is* the culprit protocol, so annotating
                // on this side would be a second writer of a first-writer-wins attribute.
                Act::Delegate => self.delegate(slot)?,
                Act::Emit(emit) => match self.run_emit(slot, emit) {
                    Ok(Rendered::Done) => census_bump(&mut self.census, &slot.class_name, false),
                    // A refusal is a *value* the filter returned, so the node goes back to Python whole —
                    // and the context already holds the enclosing loop's state, because the loop wrote it.
                    Ok(Rendered::Refused) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::If(plan) => match self.run_if(plan, depth + 1) {
                    Ok(Rendered::Done) => census_bump(&mut self.census, &slot.class_name, false),
                    Ok(Rendered::Refused) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::For(plan) if self.scoped_joins > 0 => {
                    if WITH_LOOP_IS_STOCK
                        .import(
                            py,
                            "django_rust_templates.engine._stock",
                            "with_loop_is_stock",
                        )?
                        .call1((&self.context, slot.node.bind(py)))?
                        .is_truthy()?
                    {
                        match self.run_scoped_for(plan, depth + 1) {
                            Ok(output) => {
                                self.push_python(output)?;
                                census_bump(&mut self.census, &slot.class_name, false);
                            }
                            Err(error) => return Err(self.culprit(slot, error)),
                        }
                    } else {
                        self.delegate(slot)?;
                    }
                }
                Act::For(plan) => match self.run_for(plan, depth + 1) {
                    Ok(()) => census_bump(&mut self.census, &slot.class_name, false),
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::With(plan) => match self.run_with(slot, plan, depth + 1) {
                    Ok(Rendered::Done) => census_bump(&mut self.census, &slot.class_name, false),
                    Ok(Rendered::Refused) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::SimpleTag(plan) => match self.run_simple_tag(slot, plan) {
                    Ok(Rendered::Done) => census_bump(&mut self.census, &slot.class_name, false),
                    Ok(Rendered::Refused) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::Include(plan) => match self.run_include(slot, plan, depth) {
                    Ok(true) => census_bump(&mut self.census, &slot.class_name, false),
                    Ok(false) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
            }
        }
        Ok(())
    }

    /// Attach the culprit annotation and hand the original exception back.
    fn culprit(&self, slot: &Slot, error: PyErr) -> PyErr {
        annotate_culprit(self.py, &self.context, slot, &error);
        error
    }

    /// Render one node with Python — the arm an unplanned node takes, and the arm a refused one takes once
    /// its loop state is in place.
    fn delegate(&mut self, slot: &Slot) -> PyResult<()> {
        let rendered = slot
            .node
            .bind(self.py)
            .call_method1(intern!(self.py, "render_annotated"), (&self.context,))?;
        // Before the piece is moved into the sink: what this node contributed to the output.
        if let Some(census) = self.census.as_mut() {
            census.delegated_len += rendered.len().unwrap_or(0);
        }
        self.push_python(rendered.unbind())?;
        // The two context attributes a delegate can have changed, both written **in place** on the context
        // rather than restored later: `autoescape` by `AutoEscapeControlNode`, `use_tz` by `{% localtime %}`
        // and `{% timezone %}`.
        self.autoescape = self
            .context
            .getattr(intern!(self.py, "autoescape"))?
            .extract()?;
        self.use_tz = None;
        census_bump(&mut self.census, &slot.class_name, true);
        Ok(())
    }

    /// `if "forloop" in context: parentloop = context["forloop"] else: {}`
    /// (`engine/defaulttags.py:194-198`).
    ///
    /// Read **before** the push by [`Walk::run_for`], which is Django's order and not a convenience: after
    /// a push into this loop's own layer, a read would find the `forloop` *this* loop wrote on an earlier
    /// iteration and name it its own parent.
    fn parentloop(&self) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        if self.context.contains("forloop")? {
            self.context.get_item("forloop")
        } else {
            Ok(PyDict::new(py).into_any())
        }
    }

    /// Render one `{{ … }}` — `VariableNode.render`'s shape (`engine/base.py:1288-1296`) with the
    /// resolution done in Rust.
    ///
    /// The two absorbing arms are `render_resolved`'s, and the third route out is a refusal, which is a
    /// *value* the filter returned rather than an error.
    fn run_emit(&mut self, slot: &Slot, emit: &Emit) -> PyResult<Rendered> {
        let mut value = match self.resolve_head(&emit.head) {
            Ok(value) => value,
            Err(error) => match absorb_emit(self.py, slot, &self.context, error)? {
                // Truthy `string_if_invalid`: `resolve` returned it and the filters never ran.
                Absorbed::Final(value) => {
                    self.emit(value)?;
                    return Ok(Rendered::Done);
                }
                Absorbed::Continue(value) => value,
                // The `UnicodeDecodeError` arm: nothing emitted, and the node still counts as a node.
                Absorbed::Nothing => {
                    self.push_text("")?;
                    return Ok(Rendered::Done);
                }
            },
        };
        for step in &emit.steps {
            match self.apply_step(step, &value)? {
                StepOutcome::Value(next) => value = next,
                StepOutcome::Refused => return Ok(Rendered::Refused),
            }
        }
        self.emit(value)?;
        Ok(Rendered::Done)
    }

    /// Resolve bindings before entering the scope, then render its body in a
    /// separate accumulator. The body's join and the manager's unwind complete
    /// before its result is appended to the parent nodelist.
    fn run_with(&mut self, slot: &Slot, plan: &WithPlan, depth: usize) -> PyResult<Rendered> {
        let py = self.py;
        let Some(stock) = self.bypassed else {
            return Ok(Rendered::Refused);
        };
        let Some(_frame) = WalkFrame::enter() else {
            return Ok(Rendered::Refused);
        };
        let values = WITH_VALUES
            .import(py, "django_rust_templates.engine._stock", "with_values")?
            .call1((slot.node.bind(py), &self.context))?;
        if values.is_none() {
            return Ok(Rendered::Refused);
        }
        let layers = ContextLayers::push_values(&self.context, Some(values.cast::<PyDict>()?))?;
        // Read the body and its render method after entering: a custom push can
        // replace either. A refusal here calls that already-resolved method,
        // without repeating bindings or pushing the scope again.
        let outcome = (|| -> PyResult<Py<PyAny>> {
            let body = slot.node.bind(py).getattr("nodelist")?;
            let render = body.getattr("render")?;
            if body.is(plan.nodelist.bind(py))
                && body.get_type().is(stock.nodelist.bind(py))
                && render.get_type().is(stock.method_type.bind(py))
                && render.getattr("__self__")?.is(&body)
                && render
                    .getattr("__func__")?
                    .is(stock.nodelist_render.bind(py))
            {
                self.autoescape = self.context.getattr(intern!(py, "autoescape"))?.extract()?;
                self.use_tz = None;
                return self.render_scoped_body(&plan.body, depth);
            }
            Ok(render.call1((&self.context,))?.unbind())
        })();
        self.push_python(layers.finish(outcome)?)?;
        self.autoescape = self.context.getattr(intern!(py, "autoescape"))?.extract()?;
        self.use_tz = None;
        Ok(Rendered::Done)
    }

    fn render_scoped_body(&mut self, body: &[Slot], depth: usize) -> PyResult<Py<PyAny>> {
        let outer_out = std::mem::take(&mut self.out);
        let outer_spill = self.spill.take();
        let outer_nodes = std::mem::replace(&mut self.nodes, 0);
        self.scoped_joins += 1;
        let outcome = self.run(body, depth).and_then(|()| self.finish_output());
        // Restore the parent even when rendering or the body join raises.
        self.out = outer_out;
        self.spill = outer_spill;
        self.nodes = outer_nodes;
        self.scoped_joins -= 1;
        outcome
    }

    fn run_scoped_for(&mut self, plan: &ForPlan, depth: usize) -> PyResult<Py<PyAny>> {
        let py = self.py;
        let parentloop = self.parentloop()?;
        let layers = ContextLayers::push_context(&self.context)?;
        let outer_out = std::mem::take(&mut self.out);
        let outer_spill = self.spill.take();
        let outer_nodes = std::mem::replace(&mut self.nodes, 0);
        let result = self.run_loop_body(plan, &parentloop, depth);
        let empty = matches!(result, Ok(true));
        let outcome = result.and_then(|_| {
            if empty {
                self.finish_output()
            } else {
                Ok(py.None())
            }
        });
        let outcome = layers.finish(outcome).and_then(|output| {
            if empty {
                Ok(output)
            } else {
                self.finish_output()
            }
        });
        self.out = outer_out;
        self.spill = outer_spill;
        self.nodes = outer_nodes;
        outcome
    }

    fn finish_output(&mut self) -> PyResult<Py<PyAny>> {
        let py = self.py;
        let out: Py<PyAny> = match self.spill.take() {
            None => PyString::new(py, &self.out).into_any().unbind(),
            Some(pieces) => PyString::new(py, "")
                .call_method1("join", (PyList::new(py, pieces)?,))?
                .unbind(),
        };
        Ok(SAFE_STRING
            .import(py, "django.utils.safestring", "SafeString")?
            .call1((out,))?
            .unbind())
    }

    /// Run one third-party `simple_tag` — `SimpleNode.render` (`django/template/library.py`).
    ///
    /// Everything but the call is reproduced here: the arguments resolve natively, the function is
    /// called once, and the result is escaped or assigned. The function itself is untouched third-party
    /// code, which is the whole point — the scaffolding is Django's and the body is not.
    ///
    /// Two refusals, both handing the node back whole before anything is emitted: an argument whose
    /// filter the seam cannot hold, and an output that is not a `str`. The second is faithfulness
    /// rather than caution — `SimpleNode.render` returns the value and `NodeList.render` joins it, so
    /// a non-string output is a `TypeError` raised by Python's `join`, and letting Python render the
    /// node is what produces that error where Django produces it.
    fn run_simple_tag(&mut self, slot: &Slot, plan: &SimpleTagPlan) -> PyResult<Rendered> {
        let py = self.py;
        let mut args: Vec<Py<PyAny>> = Vec::with_capacity(plan.args.len() + 1);
        if plan.takes_context {
            args.push(self.context.clone().unbind());
        }
        for expression in &plan.args {
            match self.resolve_expression(slot, expression)? {
                Some(value) => args.push(value),
                None => return Ok(Rendered::Refused),
            }
        }
        let output = plan.func.bind(py).call1(PyTuple::new(py, args)?)?;

        // `context[self.target_var] = output; return ""` — the assignment goes to the top frame,
        // which is `Context.__setitem__`'s own `self.dicts[-1]`.
        if let Some(target) = &plan.target_var {
            self.context.set_item(target.bind(py), &output)?;
            self.push_text("")?;
            return Ok(Rendered::Done);
        }

        // `if context.autoescape: output = conditional_escape(output)`. The exact-`str` arm is the
        // one the walk already takes for `{{ … }}`: a `str` *subclass* is where `SafeString` lives
        // and carries `__html__`, so it is left to Python.
        if self.autoescape && self.spill.is_none() && output.is_exact_instance_of::<PyString>() {
            if let Ok(text) = output.cast::<PyString>() {
                if let Some(escaped) = html_escape(text)? {
                    self.push_text(&escaped)?;
                    return Ok(Rendered::Done);
                }
            }
        }
        let output = if self.autoescape {
            conditional_escape_fn(py)?.call1((&output,))?
        } else {
            output
        };
        match output.extract::<String>() {
            Ok(text) => {
                self.push_text(&text)?;
                Ok(Rendered::Done)
            }
            // Not a `str`: Django's `"".join` is where that fails, so Python renders it.
            Err(_) => Ok(Rendered::Refused),
        }
    }

    /// One `FilterExpression`'s value — `FilterExpression.resolve(context)`, the argument form.
    ///
    /// The same absorption an emitted `{{ … }}` takes, because it is the same method: a failed head
    /// becomes `string_if_invalid` and the filters either stop or run on it. The one arm that does
    /// **not** carry over is `Absorbed::Nothing`, which belongs to `VariableNode.render` rather than
    /// to `resolve`; an argument has no such arm, so the node is handed back instead of inventing one.
    fn resolve_expression(&mut self, slot: &Slot, emit: &Emit) -> PyResult<Option<Py<PyAny>>> {
        let mut value = match self.resolve_head(&emit.head) {
            Ok(value) => value,
            Err(error) => match absorb_emit(self.py, slot, &self.context, error)? {
                Absorbed::Final(value) => return Ok(Some(value)),
                Absorbed::Continue(value) => value,
                Absorbed::Nothing => return Ok(None),
            },
        };
        for step in &emit.steps {
            match self.apply_step(step, &value)? {
                StepOutcome::Value(next) => value = next,
                StepOutcome::Refused => return Ok(None),
            }
        }
        Ok(Some(value))
    }

    /// Render one `{% if %}` this walk owns — `IfNode.render`'s loop (`engine/defaulttags.py:324-337`).
    ///
    /// The one way out without rendering a branch is a refusal, and only a condition can refuse: a body
    /// either runs to completion or returns its own error. A condition is evaluated before anything is
    /// emitted, so handing the node back at that point hands back a node that has written a byte.
    fn run_if(&mut self, plan: &IfPlan, depth: usize) -> PyResult<Rendered> {
        for (condition, body) in &plan.branches {
            let matched = match condition {
                None => true,
                Some(condition) => match self.eval_cond(condition) {
                    // A filter the seam cannot hold: the node has emitted nothing yet, so it can
                    // still be handed back whole.
                    Ok(None) => return Ok(Rendered::Refused),
                    // `if match:` (`engine/defaulttags.py:334`), which is **not** inside the
                    // `except`, so a `__bool__` that raises here travels as Django's does.
                    Ok(Some(value)) => value.bind(self.py).is_truthy()?,
                    // `except VariableDoesNotExist: match = None` (`engine/defaulttags.py:329-330`)
                    // — the branch misses and the next one is tried. Only a *bare* leaf reaches
                    // here: inside an operator the same exception is swallowed to `False` by that
                    // operator's own `except Exception`.
                    Err(error) if is_variable_does_not_exist(self.py, &error)? => false,
                    Err(error) => return Err(error),
                },
            };
            if matched {
                if self.scoped_joins > 0 {
                    let output = self.render_scoped_body(body, depth)?;
                    self.push_python(output)?;
                } else {
                    self.run(body, depth)?;
                }
                return Ok(Rendered::Done);
            }
        }
        // `IfNode.render`'s `return ""` (`engine/defaulttags.py:337`): a node that matched no branch
        // contributes nothing, and is still a node.
        self.push_text("")?;
        Ok(Rendered::Done)
    }

    /// One condition's value — `smartif`'s `eval(context)`, which `IfNode.render` tests for truth.
    ///
    /// `Ok(None)` is a refusal, not a value: a filter the seam cannot hold has to hand the whole node
    /// back, so it travels through the operators untouched rather than being swallowed as `False`.
    ///
    /// **Every operator catches `Exception` and answers `False`** (`smartif.py`'s `infix`/`prefix`),
    /// and it catches it *at its own node*, so a failure deep in a subtree is `False` for the operator
    /// that contains it rather than for the whole condition. That is why the wrapper is here and not
    /// around the root: a bare leaf has no operator above it and its exception reaches
    /// `IfNode.render`, which is what makes `{% if a|nope:b %}` and `{% if x and a|nope:b %}` differ.
    fn eval_cond(&mut self, cond: &Cond) -> PyResult<Option<Py<PyAny>>> {
        match cond {
            Cond::Leaf(emit) => self.eval_leaf(emit),
            operator => match self.eval_operator(operator) {
                Ok(answer) => Ok(answer),
                Err(_) => Ok(Some(self.py_bool(false))),
            },
        }
    }

    /// `TemplateLiteral.eval` (`engine/defaulttags.py:919-920`) —
    /// `self.value.resolve(context, ignore_failures=True)`.
    ///
    /// `ignore_failures=True` is the whole difference from an emitted `{{ … }}`: a failed *head* is
    /// `None` and the filters still run on it, where a `{{ … }}` would have reached for
    /// `string_if_invalid`. A failed filter *argument* has no handler in `resolve`, so it travels.
    fn eval_leaf(&mut self, emit: &Emit) -> PyResult<Option<Py<PyAny>>> {
        let mut value = match self.resolve_head(&emit.head) {
            Ok(value) => value,
            Err(error) if is_variable_does_not_exist(self.py, &error)? => self.py.None(),
            Err(error) => return Err(error),
        };
        for step in &emit.steps {
            match self.apply_step(step, &value)? {
                StepOutcome::Value(next) => value = next,
                StepOutcome::Refused => return Ok(None),
            }
        }
        Ok(Some(value))
    }

    /// The body of one operator, with its `except Exception` supplied by [`eval_cond`].
    ///
    /// The lambdas are `smartif.OPERATORS`', in their own terms: `or` and `and` are Python's, so they
    /// short-circuit and yield the **operand** rather than a bool — `{% if a or b %}` evaluates to
    /// `b`'s value when `a` is falsy, which a nested comparison can then see. The ten comparisons use
    /// Python's own protocols, so `==` on an object with an exotic `__eq__` answers whatever that
    /// answers, exactly as the lambda would.
    fn eval_operator(&mut self, cond: &Cond) -> PyResult<Option<Py<PyAny>>> {
        let py = self.py;
        match cond {
            // `eval_cond` routes a leaf to `eval_leaf`; nothing else reaches here.
            Cond::Leaf(_) => Ok(None),
            Cond::Not(inner) => {
                let Some(value) = self.eval_cond(inner)? else {
                    return Ok(None);
                };
                Ok(Some(self.py_bool(!value.bind(py).is_truthy()?)))
            }
            Cond::Infix { op, first, second } => {
                let Some(left) = self.eval_cond(first)? else {
                    return Ok(None);
                };
                // `x.eval(context) or y.eval(context)` / `and`: the right operand is not evaluated
                // at all when the left decides it.
                match op {
                    Infix::Or if left.bind(py).is_truthy()? => return Ok(Some(left)),
                    Infix::And if !left.bind(py).is_truthy()? => return Ok(Some(left)),
                    Infix::Or | Infix::And => return self.eval_cond(second),
                    _ => {}
                }
                let Some(right) = self.eval_cond(second)? else {
                    return Ok(None);
                };
                let (left, right) = (left.bind(py), right.bind(py));
                let answer = match op {
                    Infix::In => self.py_bool(right.contains(left)?),
                    Infix::NotIn => self.py_bool(!right.contains(left)?),
                    Infix::Is => self.py_bool(left.is(right)),
                    Infix::IsNot => self.py_bool(!left.is(right)),
                    Infix::Eq => left.rich_compare(right, CompareOp::Eq)?.unbind(),
                    Infix::Ne => left.rich_compare(right, CompareOp::Ne)?.unbind(),
                    Infix::Gt => left.rich_compare(right, CompareOp::Gt)?.unbind(),
                    Infix::Ge => left.rich_compare(right, CompareOp::Ge)?.unbind(),
                    Infix::Lt => left.rich_compare(right, CompareOp::Lt)?.unbind(),
                    Infix::Le => left.rich_compare(right, CompareOp::Le)?.unbind(),
                    // Handled above; `or`/`and` never reach the comparison arm.
                    Infix::Or | Infix::And => return Ok(Some(left.clone().unbind())),
                };
                Ok(Some(answer))
            }
        }
    }

    /// Python's `True`/`False`, which an operator's refusal and `not` both answer with.
    fn py_bool(&self, value: bool) -> Py<PyAny> {
        PyBool::new(self.py, value).to_owned().into_any().unbind()
    }

    /// The head's value, by whichever of [`Head`]'s two shapes it is.
    ///
    /// One lookup arm, resolved against the context, because that is where Django's `Variable.resolve`
    /// resolves it and the context holds the enclosing loop's state on every iteration.
    fn resolve_head(&self, head: &Head) -> PyResult<Py<PyAny>> {
        match head {
            Head::Value(value) => Ok(value.clone_ref(self.py)),
            Head::Lookups { lookups, keys } => {
                self.scope.resolve(self.py, lookups, keys, &self.context)
            }
        }
    }

    /// Apply one filter: the body of `resolve`'s loop (`for func, args in self.filters:`,
    /// `engine/base.py:868-874`).
    fn apply_step(&mut self, step: &FilterStep, value: &Py<PyAny>) -> PyResult<StepOutcome> {
        let py = self.py;
        // `arg_vals`: a constant was `mark_safe`d at plan time, a variable is `arg.resolve(context)` with
        // **no handler**, so a failed argument propagates out of `resolve` and each caller decides. A
        // literal is neither resolved nor wrapped — it is the object `resolve` would have returned.
        let mut args: Vec<Py<PyAny>> = Vec::with_capacity(1);
        match &step.arg {
            None => {}
            Some(FilterArg::Constant(constant)) => args.push(constant.clone_ref(py)),
            Some(FilterArg::Literal(literal)) => args.push(literal.clone_ref(py)),
            Some(FilterArg::Variable { variable, .. }) => args.push(
                variable
                    .bind(py)
                    .call_method1("resolve", (&self.context,))?
                    .unbind(),
            ),
        }
        let args = PyTuple::new(py, args)?;
        let (autoescape, use_tz) = (self.autoescape, self.use_tz()?);
        let filtered = filters::run_filter(
            py,
            &step.name,
            value.bind(py),
            &args,
            autoescape,
            use_tz,
            true,
        )?;
        // A refusal is an answer, not an error — the seam's sentinel, in place of a result this walk
        // cannot hold. The application that follows is Python's, so the census counts only the
        // applications the walk itself made.
        if filtered.bind(py).is(cannot_hold(py).bind(py)) {
            return Ok(StepOutcome::Refused);
        }
        census_filter(&mut self.census, &step.name);
        Ok(StepOutcome::Value(filtered))
    }

    /// `template_localtime`'s `use_tz` argument, filled on first use.
    fn use_tz(&mut self) -> PyResult<bool> {
        if let Some(use_tz) = self.use_tz {
            return Ok(use_tz);
        }
        let context = self.context.getattr("use_tz")?;
        let use_tz: bool = if context.is_none() {
            // The **object** is cached and the attribute read per walk, so an `override_settings` between
            // two renders is still seen.
            DJANGO_SETTINGS
                .import(self.py, "django.conf", "settings")?
                .getattr("USE_TZ")?
                .extract()?
        } else {
            context.extract()?
        };
        self.use_tz = Some(use_tz);
        Ok(use_tz)
    }

    /// Append one resolved value, taking `render_value_in_context`'s first arm here when it applies.
    ///
    /// Exact strings are escaped inline, stock SafeStrings are appended, and
    /// ungrouped exact integers use their decimal spelling. Subclasses and
    /// rebound SafeString hooks reach Python. Strings with lone surrogates spill
    /// into Python's accumulator rather than being forced into a Rust String.
    fn emit(&mut self, value: Py<PyAny>) -> PyResult<()> {
        let py = self.py;
        if self
            .bypassed
            .is_some_and(|stock| stock.is_safe_string(py, value.bind(py)))
        {
            return self.push_python(value);
        }
        // The same exact-int arm as engine/base.py's render_value_in_context:
        // with grouping disabled, localizing an integer only produces its decimal
        // spelling. Those characters need no HTML escaping, and the final safe
        // nodelist constructor supplies the output's safety.
        if value.bind(py).is_exact_instance_of::<PyInt>()
            && (self
                .context
                .getattr(intern!(py, "use_l10n"))?
                .is(false.into_pyobject(py)?)
                || !DJANGO_SETTINGS
                    .import(py, "django.conf", "settings")?
                    .getattr(intern!(py, "USE_THOUSAND_SEPARATOR"))?
                    .is_truthy()?)
        {
            return self.push_python(value.bind(py).str()?.into_any().unbind());
        }
        // Gated on the spill because the fast path writes `out` directly: once the accumulator is
        // Python's, `out` is an empty remnant and a piece written to it would be lost.
        if self.autoescape && self.spill.is_none() {
            let value = value.bind(py);
            if value.is_exact_instance_of::<PyString>() {
                if let Ok(text) = value.cast::<PyString>() {
                    if let Some(escaped) = html_escape(text)? {
                        return self.push_text(&escaped);
                    }
                }
            }
        }
        let rendered = self.escape.call1((value, &self.context))?;
        self.push_python(rendered.unbind())
    }

    /// Append a piece this walk produced itself.
    fn push_text(&mut self, text: &str) -> PyResult<()> {
        match &mut self.spill {
            None => self.out.push_str(text),
            Some(pieces) => pieces.push(PyString::new(self.py, text).into_any().unbind()),
        }
        self.nodes += 1;
        Ok(())
    }

    /// Append one piece that came back from Python, which may not be a piece a Rust `String` can hold.
    /// **The accumulator is a Rust `String`, and Python's `str` is not a subset of it.** A lone surrogate —
    /// `'\ud800'`, which
    /// `FilterSeamRefusalTests.test_a_string_the_seam_cannot_hold_renders_as_django_renders_it` renders
    /// through `{{ x|upper }}` — is a `str` CPython carries and no `String` can. So the first piece that will
    /// not fit **spills** the accumulator into Python's; the slow path ends in `"".join`, as `NodeList.render`
    /// does (`engine/base.py:1204-1205`).
    /// A **`str` that will not fit** goes into the spill; a piece that is **not a `str` at all** is a value
    /// Django dies on: keep it until the final `join` raises `TypeError`, after all
    /// sibling nodes have rendered. Empty padding keeps the offending piece at
    /// its **node** index (`self.nodes`) despite an aggregated string prefix.
    fn push_python(&mut self, piece: Py<PyAny>) -> PyResult<()> {
        let py = self.py;
        let fits = match piece.bind(py).cast::<PyString>() {
            Ok(text) => text.to_str().is_ok(),
            Err(_) => false,
        };
        if fits && self.spill.is_none() {
            let text = piece.bind(py).cast::<PyString>()?.to_str()?;
            self.out.push_str(text);
        } else if piece.bind(py).is_instance_of::<PyString>() {
            if self.spill.is_none() {
                // The prefix has merged `self.nodes` nodes and becomes one element; the ordinal above is
                // computed from the *count*, not from this list, so merging does not lose it.
                self.spill = Some(vec![PyString::new(py, &self.out).into_any().unbind()]);
                self.out.clear();
            }
            self.spill.as_mut().unwrap().push(piece);
        } else {
            // NodeList first renders every child and then joins. Keep a bad
            // piece until that join so later nodes still run. Empty padding
            // preserves its node ordinal after an aggregated string prefix.
            if self.spill.is_none() {
                self.spill = Some(if self.nodes == 0 {
                    Vec::new()
                } else {
                    vec![PyString::new(py, &self.out).into_any().unbind()]
                });
                self.out.clear();
            }
            let pieces = self.spill.as_mut().unwrap();
            while pieces.len() < self.nodes {
                pieces.push(PyString::new(py, "").into_any().unbind());
            }
            pieces.push(piece);
        }
        self.nodes += 1;
        Ok(())
    }

    /// Render one `{% include %}` natively, or answer `false` so the caller delegates.
    ///
    /// **The refusal is only free before the first push.** Answering `false` hands the node back to
    /// Python, which runs `IncludeNode.render` from the top and pushes these layers a second time.
    /// So the order below is Django's order and a refusal that sits after the first push renders the
    /// target *here* instead, calling what Python would have called from that point.
    ///
    /// **One exit.** [`include_inside`](Self::include_inside) is the whole of `IncludeNode.render`
    /// and the `Template.render` it calls, and it has no exit of its own: the value it returns and
    /// the failure it returns both travel out through [`ContextLayers::finish`], which is the
    /// `__exit__`s Python would have run. That is also what lets a manager swallow the failure —
    /// the include's output is then `None`, and joining it in the parent is what raises.
    fn run_include(&mut self, slot: &Slot, plan: &IncludePlan, depth: usize) -> PyResult<bool> {
        let py = self.py;
        let target = resolve_include(py, &self.context, slot.node.bind(py), plan)?;
        // The frame is taken before any layer is pushed, so a refusal here still repeats nothing.
        let Some(_frame) = WalkFrame::enter() else {
            return Ok(false);
        };

        // `with context.push(**values)` (`loader_tags.py:210-211`).
        let mut layers = ContextLayers::push_context(&self.context)?;
        let outcome = self.include_inside(&target, depth, &mut layers);
        self.push_python(layers.finish(outcome)?)?;
        Ok(true)
    }

    /// `IncludeNode.render` from its first read to the string it returns, and the `Template.render`
    /// it calls — with no exit of its own. The caller unwinds, once, after this returns.
    ///
    /// **What is read, and where.** `render` is read after `context.push` and before `push_state`,
    /// which is where Python reads it; `context.template`, `_render` and `_native_plan` are read
    /// after `push_state`, which is where Python reads those. Measured, a `Context.push` override
    /// that rebinds a name on the target renders `OVERRIDE` through stock Django and the plan
    /// through a slot that read the target before pushing.
    ///
    /// **Every read after the first push is made once and kept.** `target.render` and
    /// `target._render` are attributes like any other, and the call a refusal makes is the one
    /// Python would have made from there — never a second lookup that an object answering
    /// differently each time could see.
    fn include_inside(
        &mut self,
        target: &Bound<'py, PyAny>,
        depth: usize,
        layers: &mut ContextLayers<'py>,
    ) -> PyResult<Py<PyAny>> {
        let py = self.py;

        // `template.render(context)`: the attribute read at `loader_tags.py:211`, after
        // `context.push` and before `Template.render` enters `push_state` — and `push()` is arbitrary
        // Python, so a target it rebound must be seen.
        let render = target.getattr(Renders::Render.name())?;
        if !self.renders_through(&render, target, Renders::Render) {
            return Ok(render.call1((&self.context,))?.unbind());
        }

        // `with context.render_context.push_state(self)` (`base.py:170`).
        layers.enter_render_context(target)?;

        // `if context.template is None:` (`base.py:171`), asked here because this is where Python
        // asks it — after `push_state`, whose `render_context.push` may have cleared it.
        if self.context.getattr("template")?.is_none() {
            layers.enter_bind_template(target)?;
        }

        // `self._render(context)` (`base.py:174`, `base.py:176`), the second name read once.
        let plan_read = target.getattr(Renders::Plan.name())?;
        if !self.renders_through(&plan_read, target, Renders::Plan) {
            return Ok(plan_read.call1((&self.context,))?.unbind());
        }

        // `_native_plan` is the first line of `_render` (`engine/base.py:177-194`). A template with no
        // plan is any template compiled while the walk was off, and an attribute that is not a
        // `NativePlan` is a template another engine built; both render on the Python path.
        // `AttributeError` is the only lookup failure that means one of those two things; anything
        // else is the object's own exception and belongs to the caller.
        let child = match target.getattr("_native_plan") {
            Ok(held) => match held.extract::<Py<NativePlan>>() {
                Ok(child) => child,
                Err(_) => return Ok(plan_read.call1((&self.context,))?.unbind()),
            },
            Err(error) if error.is_instance_of::<PyAttributeError>(py) => {
                return Ok(plan_read.call1((&self.context,))?.unbind())
            }
            Err(error) => return Err(error),
        };

        // The included template's output is `SafeString("".join(...))` of *its own* nodelist, so it is
        // one element of the parent's list and the ordinal a non-`str` piece is reported at is its
        // index in that inner list. Hence a frame of its own, swapped in and out.
        let outer_out = std::mem::take(&mut self.out);
        let outer_spill = self.spill.take();
        let outer_nodes = std::mem::replace(&mut self.nodes, 0);

        let child = child.bind(py);
        let child = child.borrow();
        let rendered = self.run(&child.slots, depth + 1);

        let inner_out = std::mem::replace(&mut self.out, outer_out);
        let inner_spill = self.spill.take();
        self.spill = outer_spill;
        self.nodes = outer_nodes;

        // A plain `str`, not a `SafeString`: `push_python` reads the piece's *characters*, and an
        // include's output is already escaped by the time it is joined. Wrapping it here would only
        // ask `SafeString` to re-wrap a string it did not produce.
        rendered.and_then(|()| match inner_spill {
            None => Ok(PyString::new(py, &inner_out).into_any().unbind()),
            Some(pieces) => Ok(PyString::new(py, "")
                .call_method1("join", (PyList::new(py, pieces)?,))?
                .unbind()),
        })
    }

    /// Whether `target` still renders through the `render` and `_render` this slot stands in for.
    ///
    /// `_native_plan` says a plan was built for the target's class. It does not say the object still
    /// renders through it: `IncludeNode.render` ends in `template.render(context)`
    /// (`loader_tags.py:210-211`), and a subclass overriding either name — or an instance that patched
    /// one — has an answer of its own that rendering the plan here would skip. Measured: a cached
    /// target with `render` replaced renders the replacement through stock Django and its own
    /// template through this slot.
    ///
    /// **Ask the object, not its class.** `target.render` *is* Python's lookup, so it is the only
    /// question that sees everything a look at the class cannot: a class-level `__getattribute__`, a
    /// data descriptor somewhere in the MRO, an instance that patched the name, a `functools.partial`
    /// or a plain callable. A method answers with a bound method, whose `__func__` is the function
    /// behind it and whose `__self__` is the object it was bound to; anything else answers without
    /// one and declines here — as does a bound method of the right function bound to the *wrong*
    /// object. That pair of reads replaces the type resolution and the `__dict__` check between
    /// them, because it asks about both at once.
    ///
    /// **A genuine bound method, not something shaped like one.** `__func__` and `__self__` are
    /// ordinary attributes: an object carrying the right function and the right receiver without
    /// being that method answers this test and renders something else — measured, one whose call
    /// returned `"FAKE"` where stock Django returned the template's own `"x"`. The type of the
    /// lookup's result is therefore checked first, against `types.MethodType`, which cannot be
    /// subclassed, so an exact test is the whole question.
    ///
    /// **Against [`Stock`]'s pair, not the walk's own template's class.** A walk entered through a
    /// subclass's `render` wrapper is not standing in for that wrapper, and neither is one that
    /// took the pair from whatever `render_context.template` happens to be — measured, one pair of
    /// brackets where Django writes two. And against the pair as *[`stock`] captured it* rather
    /// than as the class spells it now, so that a rebind of either name declines instead of being
    /// followed.
    ///
    /// **Refusal is the answer when it cannot be given.** No pair read at entry, or a name that
    /// does not resolve to a bound method of the right function on this object, declines.
    /// Declining is a lost optimisation, never a different string.
    ///
    /// **Asked one name at a time, because the two are read in different gaps.** Python reads
    /// `render` in `IncludeNode.render`, after `context.push` and before `Template.render` enters
    /// `push_state`; it reads `_render` inside `Template.render`, after both. The caller asks
    /// [`Renders::Render`] at the first of those points and [`Renders::Plan`] at the second, so
    /// that the call each one refuses with is the call Python would have made from there.
    fn renders_through(
        &self,
        bound: &Bound<'py, PyAny>,
        target: &Bound<'py, PyAny>,
        which: Renders,
    ) -> bool {
        let Some(stock) = self.bypassed else {
            return false;
        };
        let implementation = match which {
            Renders::Render => &stock.render,
            Renders::Plan => &stock.plan,
        };
        if !bound.get_type().is(stock.method_type.bind(self.py)) {
            return false;
        }
        let (Ok(func), Ok(this)) = (bound.getattr("__func__"), bound.getattr("__self__")) else {
            return false;
        };
        if !func.is(implementation.bind(self.py)) || !this.is(target) {
            return false;
        }
        true
    }

    /// A `{% for %}` the walk runs itself, in `ForNode.render`'s order (`engine/defaulttags.py:193-258`).
    ///
    /// **The layer is Django's, it opens once per loop, and it opens before the sequence resolves.** The
    /// sequence is a `FilterExpression` — arbitrary Python handed the **context** — and it is resolved as
    /// the first statement inside the `with` (`engine/defaulttags.py:198-199`), so it can read the layer stack and write into it:
    /// resolving one layer out gets both wrong, and a write would land in a layer Django pops with the
    /// `{% endfor %}`. `parentloop = context["forloop"]` is read *above* the `with` (`engine/defaulttags.py:194-197`) for every
    /// loop, and here too, because the loop writes a `forloop` into its own layer before the first
    /// iteration, so reading it inside would name *this* loop its own parent from the second iteration on.
    fn run_for(&mut self, plan: &ForPlan, depth: usize) -> PyResult<()> {
        let py = self.py;
        let parentloop = self.parentloop()?;
        let pushed = self.context.call_method0("push")?;
        let result = self.run_loop_body(plan, &parentloop, depth);
        // On both paths, as `with` does: an exception must not leave the context a layer deeper than the
        // loop found it. `__exit__` is called explicitly because the exception is *travelling* here.
        let popped = pushed.call_method1("__exit__", (py.None(), py.None(), py.None()));
        result?;
        popped?;
        Ok(())
    }

    /// [`Walk::run_for`]'s body, inside the loop's layer: the sequence, the empty test, the reversal, and the
    /// iterations with Django's per-iteration state. Split out so the layer is closed on every way out of the
    /// loop, error included, from one place.
    /// **The order inside the layer is the argument, and every step is observable.** `values` is resolved and
    /// normalised to something with a length *before* the empty test; the length is read *before* the reversal,
    /// because `reversed(values)` has no length; the empty branch is rendered *before* any loop state exists;
    /// the `forloop` dict is written *before* the first iteration and mutated in place from inside it; and
    /// within an iteration the six counters go in *before* the loop variable
    /// (`engine/defaulttags.py:220-227` then `engine/defaulttags.py:248`).
    fn run_loop_body(
        &mut self,
        plan: &ForPlan,
        parentloop: &Bound<'py, PyAny>,
        depth: usize,
    ) -> PyResult<bool> {
        let py = self.py;
        let mut values = plan
            .sequence
            .bind(py)
            .call_method1("resolve", (&self.context, true))?;
        if values.is_none() {
            values = PyList::empty(py).into_any();
        }
        if !values.hasattr("__len__")? {
            values = py.import("builtins")?.getattr("list")?.call1((values,))?;
        }
        // Read *before* the reversal, both Django's order (`len_values = len(values)`,
        // `engine/defaulttags.py:204`) and the only order that works: `reversed(values)` has no length.
        let length = values.len()?;
        if length < 1 {
            // Inside the layer, because Django's is: the `with` opens *before* the empty test and the empty
            // arm returns from inside it (`engine/defaulttags.py:198-206`), so a write an `{% empty %}` body
            // makes is discarded with the loop's layer. Rendering the arm outside any push keeps the write,
            // which is observable: `{% for r in none %}{% empty %}{% writes "foo" %}{% endfor %}{{ foo }}`
            // renders `[]` in stock and `[W]` without this.
            if self.scoped_joins > 0 {
                let output = self.render_scoped_body(&plan.empty, depth)?;
                self.push_python(output)?;
            } else {
                self.run(&plan.empty, depth)?;
            }
            return Ok(true);
        }
        if plan.is_reversed {
            values = py
                .import("builtins")?
                .getattr("reversed")?
                .call1((values,))?;
        }
        // `loop_dict = context["forloop"] = {...}` — built once, mutated in place per iteration
        // (`engine/defaulttags.py:214-229`). Django's shape, not an optimisation: a fresh dict per iteration
        // would give a nested loop inside a delegate a different `parentloop` *object* than Django gives it
        // — the same numbers, and the wrong identity.
        //
        // `parentloop` is an argument rather than a read here for the reason [`Walk::parentloop`] gives, and
        // `length` is one of the two keys Django writes **once**: a nested loop inside a delegate reads
        // `forloop.parentloop.length` off this dict, and a dict missing it is a `KeyError` where Django has a
        // number.
        let forloop = PyDict::new(py);
        forloop.set_item(intern!(py, "parentloop"), parentloop)?;
        forloop.set_item(intern!(py, "length"), length)?;
        self.context.set_item(intern!(py, "forloop"), &forloop)?;
        // The state is written on every iteration, for every loop. Nothing about a body shape decides
        // whether a body can run Python on a value holding the context and write a name the walk would then
        // answer stale (`.review/m19-measurements/ctx_write_channels.py` rows 15-17), so nothing is elided
        // and no shape is refused.
        //
        // The writes are Django's, in Django's order: the six counters into the dict first
        // (`engine/defaulttags.py:220-227`), then `context[loopvars[0]] = item` (`engine/defaulttags.py:248`). The order is
        // observable — a loop named `forloop` has the item written *over* the dict at `engine/defaulttags.py:248`.
        // The reverse counters are signed, and it is not a style choice: Django's are Python integers and go
        // *negative* when the sequence yields more items than `len()` reported, ordinary behaviour for a list
        // that grows while it renders or any object whose `__iter__` and `__len__` disagree
        // (`engine/defaulttags.py:223-224`). Computing them in `usize` wraps instead: a one-element list whose
        // element appends to itself renders `b:0/18446744073709551615` where Django gives `b:0/-1`. `length`
        // comes from Python's `len()`, so it fits `i64` by construction.
        let signed_length = length as i64;
        // The keys are hoisted out of the iteration: `forloop.set_item(&str, …)` builds a `PyString` from the
        // `&str` on *every* call — six counters, six builds per iteration, for names that are literals here.
        // `intern!` answers with the same `PyString` each time, as CPython does with the literal keys of
        // Django's own `loop_dict = {"counter0": …}`: no build, a hash cached per key object, and from the
        // second iteration a lookup that hits on *identity*. `plan.loopvar` is the seventh, pre-built on the
        // plan ([`ForPlan::loopvar`]).
        let key_counter0 = intern!(py, "counter0");
        let key_counter = intern!(py, "counter");
        let key_revcounter = intern!(py, "revcounter");
        let key_revcounter0 = intern!(py, "revcounter0");
        let key_first = intern!(py, "first");
        let key_last = intern!(py, "last");
        for (index, item) in values.try_iter()?.enumerate() {
            let item = item?;
            let counter = index + 1;
            let revcounter = signed_length - index as i64;
            forloop.set_item(key_counter0, index)?;
            forloop.set_item(key_counter, counter)?;
            forloop.set_item(key_revcounter, revcounter)?;
            forloop.set_item(key_revcounter0, revcounter - 1)?;
            forloop.set_item(key_first, index == 0)?;
            forloop.set_item(key_last, index == length - 1)?;
            self.context.set_item(plan.loopvar.bind(py), &item)?;
            self.run(&plan.body, depth)?;
        }
        Ok(false)
    }
}

/// `except VariableDoesNotExist` — the class test all three callers need.
///
/// Nothing else is caught anywhere: `Variable.resolve` has no handler of its own, so a lookup raising
/// anything else reaches the node uncaught, where Django's outer handler decides.
fn is_variable_does_not_exist(py: Python<'_>, error: &PyErr) -> PyResult<bool> {
    let class = VARIABLE_DOES_NOT_EXIST
        .import(
            py,
            "django_rust_templates.engine.base",
            "VariableDoesNotExist",
        )?
        .cast::<PyType>()?;
    Ok(error.is_instance(py, class))
}

/// `FilterExpression.resolve`'s failure arm (`engine/base.py:819-832`), as the **three-way** decision it
/// is.
///
/// A truthy `string_if_invalid` **returns**, so the filter chain never runs, while a falsy one assigns
/// `obj = string_if_invalid` and falls through to the chain. `""` is the default setting, so the
/// falling-through arm is the common one — and it is why `{{ missing|default:"x" }}` is `"x"`. Collapsing
/// the two would make the default `""` skip `default`.
///
/// The `UnicodeDecodeError` arm is not `resolve`'s but `VariableNode.render`'s (`engine/base.py:1360-1363`):
/// resolving a variable directly resolves *past* both handlers, so both are taken here.
fn absorb_emit(
    py: Python<'_>,
    slot: &Slot,
    context: &Bound<'_, PyAny>,
    error: PyErr,
) -> PyResult<Absorbed> {
    if error.is_instance_of::<PyUnicodeDecodeError>(py) {
        return Ok(Absorbed::Nothing);
    }
    if !is_variable_does_not_exist(py, &error)? {
        return Err(error);
    }
    let invalid = string_if_invalid(context)?;
    let invalid = invalid.bind(py);
    if !invalid.is_truthy()? {
        return Ok(Absorbed::Continue(invalid.clone().unbind()));
    }
    if invalid.contains("%s")? {
        // `string_if_invalid % self.var`, and `self.var` is the `Variable` object — `%s` on it calls
        // `Variable.__str__`, the lookup text. Read off the node rather than re-derived by joining
        // `lookups`: it is the operand Django uses.
        let var = slot
            .node
            .bind(py)
            .getattr("filter_expression")?
            .getattr("var")?;
        return Ok(Absorbed::Final(
            invalid.call_method1("__mod__", (var,))?.unbind(),
        ));
    }
    Ok(Absorbed::Final(invalid.clone().unbind()))
}

/// Walk a plan and return the rendered `SafeString`, or `None` if the walk would nest Rust deeper
/// than [`MAX_WALK_FRAMES`] allows.
///
/// The `None` is the caller's cue to render the nodelist the ordinary way. A caller that ignores it
/// is rendering nothing at all, so `Template._render` and `NodeList._render_with_plan` both read it,
/// and every other reason a walk cannot run is already folded in here: a walk that declines is a
/// render that never started.
#[pyfunction]
fn render_plan(
    py: Python<'_>,
    plan: &NativePlan,
    context: Py<PyAny>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(_frame) = WalkFrame::enter() else {
        return Ok(None);
    };
    let context = context.bind(py);
    let mut walk = Walk {
        py,
        context: context.clone(),
        escape: render_value_in_context(py)?.clone(),
        scope: LookupScope::new(context),
        autoescape: context.getattr("autoescape")?.extract()?,
        use_tz: None,
        out: String::new(),
        spill: None,
        nodes: 0,
        scoped_joins: 0,
        census: if census_on() {
            Some(Census::default())
        } else {
            None
        },
        // The two functions an included target is claimed to still render through, from
        // [`Stock`] — read once per process, so a rebind of either name is declined rather than
        // followed. `None` if `_stock.py` could not be read, which declines every include for this
        // render and nothing else.
        bypassed: stock(py),
    };
    walk.run(&plan.slots, 0)?;
    if let Some(census) = walk.census.take() {
        census_merge(&mut CENSUS.lock().unwrap(), census);
    }
    // `SafeString` of a whole `str`, or of `"".join(pieces)` where the walk had to spill —
    // `NodeList.render`'s own expression (`engine/base.py:1204-1205`). `SafeString` and not `mark_safe`,
    // for the reasons on [`SAFE_STRING`].
    Ok(Some(walk.finish_output()?))
}

/// `django_rust_templates.engine.base.render_value_in_context`, cached once.
///
/// The function itself, not a Rust reimplementation: it is Django's — `template_localtime`, `localize`,
/// `conditional_escape` — transcribed into this package, and the transcription is byte-identical to
/// stock's by construction rather than by a test that has to keep pace. The walk takes the same
/// function's first arm inline ([`Walk::emit`]) and calls this for everything that arm declines.
///
/// Cached because a per-render fetch cost ~1.2 µs of fixed cost — most of the `micro/text` row, which is
/// the render call. `PyOnceLock::import` resolves it once per process.
static RENDER_VALUE_IN_CONTEXT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// `django.utils.html.conditional_escape`, cached — `SimpleNode.render`'s own escape.
static CONDITIONAL_ESCAPE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn conditional_escape_fn(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    CONDITIONAL_ESCAPE.import(py, "django.utils.html", "conditional_escape")
}

fn render_value_in_context(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    RENDER_VALUE_IN_CONTEXT.import(
        py,
        "django_rust_templates.engine.base",
        "render_value_in_context",
    )
}

/// `django.utils.safestring.SafeString`, cached once — `SafeString` rather than `mark_safe`
/// deliberately, for two reasons that agree.
///
/// **It is the faithful one:** the expression this replaces is `NodeList.render`'s, and that expression is
/// `SafeString("".join([...]))` — `mark_safe` is Django's *public* helper, not the constructor the node
/// list uses. **And it is 0.48 µs against 0.07:** measured, 7×300 000, `mark_safe("")` 0.484 µs against
/// `SafeString("")` 0.072 µs, and on a walked template it is paid once per render.
static SAFE_STRING: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// What an include slot reproduces, read from Python **once per process**.
///
/// `render` and `_render` are the implementations the walk stands in for: an include target may be
/// claimed only while those two names still resolve to these two objects. See
/// [`Walk::renders_through`].
///
/// **Captured in Python, at import, because only Python can answer "what did the class say
/// first".** Reading `Template.render` again per walk follows a rebind rather than noticing it — a
/// template whose class has had `render` replaced by a wrapper that adds brackets rendered `[x]`
/// through this slot where stock Django renders `[[x]]`, and a per-walk read of the class attribute
/// agreed with the wrapper. `engine/_stock.py` holds the import-time pair; `rust.py` imports it, so
/// it is captured when the backend is, which is the earliest moment the engine exists at all.
///
/// `method_type` is `types.MethodType`, checked by identity because it cannot be subclassed: see
/// [`Walk::renders_through`].
struct Stock {
    render: Py<PyAny>,
    plan: Py<PyAny>,
    method_type: Py<PyAny>,
    /// `engine/_unwind.py`'s `UNWIND` — the exceptional `__exit__`. See [`exit`].
    unwind: Py<PyAny>,
    nodelist: Py<PyAny>,
    nodelist_render: Py<PyAny>,
    safe_string: Py<PyAny>,
    safe_string_str: Py<PyAny>,
    safe_string_html: Py<PyAny>,
    safe_string_getattribute: Py<PyAny>,
    safe_string_class: Py<PyAny>,
}

impl Stock {
    fn is_safe_string(&self, py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
        let class = value.get_type();
        class.is(self.safe_string.bind(py))
            && type_entry_is_pin(
                &class,
                intern!(py, "__str__"),
                self.safe_string_str.bind(py),
            )
            && type_entry_is_pin(
                &class,
                intern!(py, "__html__"),
                self.safe_string_html.bind(py),
            )
            && type_entry_is_pin(
                &class,
                intern!(py, "__getattribute__"),
                self.safe_string_getattribute.bind(py),
            )
            && type_entry_is_pin(
                &class,
                intern!(py, "__class__"),
                self.safe_string_class.bind(py),
            )
    }
}

/// Which of the two names on [`Stock`] a check is about. They are read by Python at different
/// points in `IncludeNode.render`'s call into the target, and each refusal has to be made from the
/// point the name it refuses on is read at. See [`Walk::renders_through`].
#[derive(Clone, Copy)]
enum Renders {
    /// `Template.render`, read in `IncludeNode.render` before `push_state`.
    Render,
    /// `Template._render`, read inside `Template.render` after `push_state`.
    Plan,
}

impl Renders {
    /// The name itself — the one the caller reads off the target, once, and hands to
    /// [`Walk::renders_through`].
    fn name(self) -> &'static str {
        match self {
            Renders::Render => "render",
            Renders::Plan => "_render",
        }
    }
}

/// [`Stock`], or `None` if the two modules behind it cannot be read — which leaves every include
/// of this process declining, an optimisation lost and nothing else.
static STOCK: PyOnceLock<Stock> = PyOnceLock::new();

fn stock(py: Python<'_>) -> Option<&'static Stock> {
    STOCK
        .get_or_try_init(py, || -> PyResult<Stock> {
            let module = py.import("django_rust_templates.engine._stock")?;
            let unwind = py.import("django_rust_templates.engine._unwind")?;
            Ok(Stock {
                render: module.getattr("RENDER")?.unbind(),
                plan: module.getattr("PLAN")?.unbind(),
                method_type: module.getattr("METHOD_TYPE")?.unbind(),
                unwind: unwind.getattr("UNWIND")?.unbind(),
                nodelist: module.getattr("NODELIST")?.unbind(),
                nodelist_render: module.getattr("NODELIST_RENDER")?.unbind(),
                safe_string: module.getattr("SAFE_STRING")?.unbind(),
                safe_string_str: module.getattr("SAFE_STRING_STR")?.unbind(),
                safe_string_html: module.getattr("SAFE_STRING_HTML")?.unbind(),
                safe_string_getattribute: module.getattr("SAFE_STRING_GETATTRIBUTE")?.unbind(),
                safe_string_class: module.getattr("SAFE_STRING_CLASS")?.unbind(),
            })
        })
        .ok()
}

/// The four `Rc`s `PyTarget` shares, hoisted out of the render loop.
///
/// The arena and `pending` stay per-*call* in the port's contract, so [`LookupScope::resolve`] resets both
/// rather than reusing them. Nothing observes the reset: `outcome_to_py` returns `clone_ref`'d values, not
/// handles into the arena, and reads `pending` only for an outcome that raised.
struct LookupScope {
    arena: Rc<RefCell<Vec<Py<PyAny>>>>,
    pending: Rc<Cell<Option<PyErr>>>,
    context: Rc<Py<PyAny>>,
}

impl LookupScope {
    fn new(context: &Bound<'_, PyAny>) -> Self {
        Self {
            arena: Rc::new(RefCell::new(Vec::new())),
            pending: Rc::new(Cell::new(None)),
            context: Rc::new(context.clone().unbind()),
        }
    }

    fn reset(&self) {
        self.arena.borrow_mut().clear();
        self.pending.set(None);
    }

    fn resolve(
        &self,
        py: Python<'_>,
        lookups: &[String],
        keys: &Arc<HashMap<String, Py<PyString>>>,
        context: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        self.reset();
        let mut root = PyTarget::new(
            context.clone().unbind(),
            &self.arena,
            &self.pending,
            &self.context,
        );
        root.names = Some(Arc::clone(keys));
        outcome_to_py(
            py,
            resolve_lookup(lookups, Box::new(root)),
            &self.arena,
            &self.pending,
            context,
        )
    }
}

/// Parse, materialise, and build the `Template`.
/// Returns the `Template` rather than rendering it, because Django sets `self.extra_data = parser.extra_data`
/// inside `compile_nodelist`'s `try` (`base.py:198-200`) and a shell building the `Template` by `__new__` skips
/// it. Handing the object back lets `engine/parser.py::template_for` own the whole attribute list in one place.
/// **The parser and the engine are both caller-supplied.** An import of `django_rust_templates.engine.parser`
/// from inside the extension re-enters a package that is still initialising — measured `ModuleNotFoundError:
/// No module named 'django_rust_templates.engine.engine'`. Materialising a `FilterExpression` needs the parser,
/// and the render path reads `context.template.engine` in six places (`base.py:793`, `997`, `1005`, `1011`,
/// `1024`, `1054`), so a parser and an engine from different engines render a *different answer*: measured,
/// `Engine(string_if_invalid="<<invalid>>")` renders `{{ nope }}` as `'&lt;&lt;invalid&gt;&gt;'` through stock
/// and `''` through the shell. The render goes through `tree.template_for(nodelist, engine)`, not
/// `nodelist.render(context)`: with `context.template` left `None` `{{ nope }}` raises
/// `AttributeError: 'NoneType' object has no attribute 'engine'`.
/// `origin` reaches two readers, so it is one object rather than two values: every materialised node's
/// `node.origin` and the `Template.origin` (`base.py:616`, `base.py:154`). They must be the *same* object:
/// `Node.render_annotated` compares `context.render_context.template.origin == e._culprit_node.origin` to
/// attach the debug traceback (`base.py:1058-1062`).
#[pyfunction]
#[pyo3(signature = (source, parser, tree, engine, host = None, origin = None))]
fn compile_template(
    py: Python<'_>,
    source: &str,
    parser: Py<PyAny>,
    tree: Py<PyAny>,
    engine: Py<PyAny>,
    host: Option<Bound<'_, PyAny>>,
    origin: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let tree = tree.bind(py);
    let check = tree.getattr("check_filter")?.call1((parser.bind(py),))?;
    // `{% if %}`'s other half, from the same module and for the same reason: `TemplateIfParser.create_var`
    // builds each operand during the parse, so the callable has to be here rather than in the shell. Built
    // from the *same* parser, so the filters a condition resolves are the caller's engine's.
    let compile = tree.getattr("compile_filter")?.call1((parser.bind(py),))?;
    let host = FilterCheckHost {
        check_filter: Some(check),
        compile_filter: Some(compile),
        refs: PyList::empty(py).into_any().unbind(),
        host,
    };

    // The attribute, never the object: `{% extends %}` compares this against a path. `None` is Django's
    // `Origin(UNKNOWN_SOURCE).template_name`, and `Parser::extend_nodelist` is where Django's truthiness
    // test lives.
    let origin_name: Option<String> = match &origin {
        Some(origin) => origin.getattr("template_name")?.extract()?,
        None => None,
    };
    // `self.engine.debug`, which upstream reads at the top of `compile_nodelist` to choose `DebugLexer`
    // over `Lexer` (`base.py:183-186`). It must be read rather than defaulted: measured, with this
    // hardcoded `false`, `Engine(debug=True).from_string("{% badtag %}")` reaches
    // `Template.get_exception_info`, which does `start, end = token.position`, and dies with
    // `TypeError: cannot unpack non-iterable NoneType object`.
    let debug: bool = engine.bind(py).getattr("debug")?.extract()?;
    // The descriptor tree is **kept**, not consumed: the render walk plans against the Rust tree paired
    // with the `NodeList` materialised from it, which is the only way to know, per node, which class the
    // materialiser built and which attributes the parser gave it. `nodelist_desc_to_py` is the same
    // producer `parse_source` uses, so the wire shape still has one spelling.
    let desc_tree = match parse_nodelist(source, debug, origin_name, &host) {
        Ok(desc_tree) => desc_tree,
        Err(error) => return Err(parse_error(py, &error)?),
    };
    // A **bool or nothing**: a missing attribute reads `False`, so a Rust tree newer than the Python module
    // beside it does not fail a compile. A value that is present and not a bool raises, because that is a
    // bug in the Python half rather than a version skew — `unwrap_or(false)` would have swallowed it.
    let native: bool = match tree.getattr("USE_NATIVE_MATERIALISER") {
        Ok(flag) => flag.extract()?,
        Err(error) if error.is_instance_of::<PyAttributeError>(py) => false,
        Err(error) => return Err(error),
    };
    // The table is the render's own, handed to `materialise` **with** the descriptor rather than left for
    // the shell to find: without it a `Ref` in the tree reaches `_decode_value` with no resolver and raises
    // `LookupError: … needs a resolve_ref callback`. The two callbacks are the bound methods of the same
    // list the descriptor carries, so there is one table and no second place to lose it.
    let refs = host.ref_list(py)?;
    let resolve_ref = refs.bind(py).getattr("__getitem__")?;
    let register_ref = refs.bind(py).getattr("__setitem__")?;
    // The wire and its wrapper tuple are built **only** on the Python path: a line that built them "just in
    // case" would leave them measurable while the walk they feed never runs.
    let desc = if native {
        // Unread on this path — the native walk takes `desc_tree` itself — and `None` rather than a wire,
        // so that a line which *did* build one would show up as work the native branch does.
        py.None()
    } else {
        with_ref_table(py, nodelist_desc_to_py(py, &desc_tree)?, &host)?
    };
    // Bound once, because it is handed to two calls below: `materialise` puts it on every node, and
    // `template_for` puts it on the `Template`. **They must be the same object** — the annotation comparison
    // in `Node.render_annotated` is between the two.
    let origin = origin.unwrap_or_else(|| py.None().into_bound(py));
    let nodelist = if native {
        materialise::materialise_native(
            py,
            tree,
            &desc_tree,
            &origin,
            &resolve_ref,
            &register_ref,
            parser.bind(py),
        )?
        .into_bound(py)
    } else {
        tree.getattr("materialise")?.call1((
            desc,
            &origin,
            resolve_ref,
            register_ref,
            parser.bind(py),
        ))?
    };
    // The render plan, built here because `compile_template` is the one place that holds the descriptor tree
    // *and* the `NodeList` built from it. It is **optional by construction**: `plan_list` returns `None` the
    // moment the two disagree about a node's class, and `render_plan` is only reached from `Template._render`
    // when the template carries a plan — so a declined plan falls back to the Python path with no branch in
    // the render.
    let plan = match plan_list(py, &desc_tree, &nodelist)? {
        Some(slots) => Some(NativePlan { slots }.into_pyobject(py)?.into_any().unbind()),
        None => None,
    };
    // And the same hook one level down, for the bodies the walk cannot reach as slots (see
    // `plan_block_bodies`). Independent of the plan above: a template whose *root* declines can still have a
    // plannable block body.
    plan_block_bodies(py, &desc_tree, &nodelist)?;
    // `parser` is the third argument because `template_for` reads `parser.extra_data` there
    // (`base.py:198-200`); passing the parser rather than the dict keeps the two in step. `source` is the
    // fifth, the render-annotation protocol's other half (`Template.get_exception_info` slices it). Passing
    // neither would leave the `Template` with `origin=None` and `source=""` while every node carried the real
    // origin, so the equality that decides whether to annotate never holds — invisible to the Django suite,
    // which discards this object.
    let template = tree.getattr("template_for")?.call1((
        nodelist.clone(),
        engine.bind(py),
        parser.bind(py),
        &origin,
        source,
        plan,
    ))?;
    Ok(template.unbind())
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let unicode_version: String = m
        .py()
        .import("unicodedata")?
        .getattr("unidata_version")?
        .extract()?;
    template_core::pyunicode::profile::configure(&unicode_version)
        .map_err(pyo3::exceptions::PyImportError::new_err)?;
    m.add_function(wrap_pyfunction!(builtin_tag_names_including_stubs, m)?)?;
    m.add_function(wrap_pyfunction!(builtin_tag_stub_names, m)?)?;
    m.add_function(wrap_pyfunction!(library_tag_names, m)?)?;
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(filter_names, m)?)?;
    m.add_function(wrap_pyfunction!(stringfilter_filters, m)?)?;
    m.add_function(wrap_pyfunction!(filter_flags, m)?)?;
    m.add_function(wrap_pyfunction!(filter, m)?)?;
    m.add_function(wrap_pyfunction!(whitespace_codepoints, m)?)?;
    m.add_function(wrap_pyfunction!(decimal_digit_codepoints, m)?)?;
    m.add_function(wrap_pyfunction!(python_upper, m)?)?;
    m.add_function(wrap_pyfunction!(split_contents, m)?)?;
    m.add_function(wrap_pyfunction!(html_escape, m)?)?;
    m.add_function(wrap_pyfunction!(lex, m)?)?;
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(parse_variable, m)?)?;
    m.add_function(wrap_pyfunction!(parse_filter_expression, m)?)?;
    m.add_function(wrap_pyfunction!(resolve, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_lookups, m)?)?;
    m.add_function(wrap_pyfunction!(compile_template, m)?)?;
    m.add_function(wrap_pyfunction!(render_plan, m)?)?;
    m.add_function(wrap_pyfunction!(census_start, m)?)?;
    m.add_function(wrap_pyfunction!(census_take, m)?)?;
    m.add_class::<NodeRef>()?;
    m.add_class::<NodeListRef>()?;
    m.add_class::<NodeSlot>()?;
    m.add_class::<Tokens>()?;
    m.add_class::<IfCondition>()?;
    m.add_class::<SafeStr>()?;
    m.add_class::<Variable>()?;
    m.add_class::<FilterExpr>()?;
    m.add_class::<NativePlan>()?;
    m.add("ParseError", m.py().get_type::<ParseError>())?;
    m.add("UnimplementedTag", m.py().get_type::<UnimplementedTag>())?;
    m.add("CANNOT_HOLD", cannot_hold(m.py()))?;
    Ok(())
}

/// The Rust side of the walk, tested against plans built here rather than parsed.
/// Gated on `not(extension-module)` because that is the switch that decides whether libpython is linked: an
/// extension module is loaded by an interpreter that already has one, and a test binary has to start its own.
/// The walk needs its own harness because **`MAX_WALK_DEPTH` has no route to it through template text**: a plan
/// is built by `plan_list`, which recurses over the same nesting the parser did, and the parser recurses in
/// Python — CPython's own limit raises `RecursionError` at about 200 levels of `{% for %}` nesting long before a
/// plan 501 deep exists. The plan here is built by hand; the two rows are the same construction at
/// `MAX_WALK_DEPTH` and `MAX_WALK_DEPTH + 1`, so the row that passes is a control over the population the row
/// that raises is drawn from.
///
/// 500 levels need 4–8 KiB of stack each in a debug build and 0.5–1 KiB in a release one (probed in
/// `scripts/run_rust_unit_tests.sh`, which sets the 8 MiB of thread stack this test needs — the default
/// 2 MiB **stalls** rather than failing). So `MAX_WALK_DEPTH` is a cap on *stack*, and 500 is a number
/// inherited from CPython's parser limit rather than derived from the walk's frame size.
#[cfg(all(test, not(feature = "extension-module")))]
mod walk_depth {
    use super::*;

    #[test]
    fn public_mro_lookup_does_not_call_metaclass_hooks_or_descriptors() {
        Python::initialize();
        Python::attach(|py| {
            let namespace = PyDict::new(py);
            let code = std::ffi::CString::new(
                r#"
def pin(self, key):
    return key
class Base:
    __getitem__ = pin
class Inherited(Base):
    pass
class Liar(type):
    def __getattribute__(cls, name):
        if name in ('__mro__', '__dict__'):
            raise AssertionError('metaclass lookup')
        if name == '__getitem__':
            return pin
        return super().__getattribute__(name)
class Overridden(Base, metaclass=Liar):
    def __getitem__(self, key):
        return None
class Missing(metaclass=Liar):
    pass
class Descriptor:
    def __get__(self, instance, owner):
        raise AssertionError('descriptor lookup')
class Described(Base):
    __getitem__ = Descriptor()
"#,
            )
            .unwrap();
            py.run(&code, Some(&namespace), None).unwrap();
            let pin = namespace.get_item("pin").unwrap().unwrap();
            for (name, expected) in [
                ("Base", true),
                ("Inherited", true),
                ("Overridden", false),
                ("Missing", false),
                ("Described", false),
            ] {
                let class = namespace.get_item(name).unwrap().unwrap();
                assert_eq!(
                    mro_subscript_entry_is_pin(class.cast::<PyType>().unwrap(), &pin).unwrap(),
                    expected,
                    "{name}"
                );
                assert_eq!(
                    mro_entry_is_absent(
                        class.cast::<PyType>().unwrap(),
                        intern!(py, "__getitem__")
                    )
                    .unwrap(),
                    name == "Missing",
                    "{name}"
                );
            }
        });
    }

    /// `ForPlan.sequence`, which `run_for` calls `.resolve(context, true)` on — the walk's only Python call
    /// on the path under test, and what makes the synthetic plan descend instead of taking the `{% empty %}`
    /// branch. It answers with one item so every level is entered exactly once.
    #[pyclass]
    struct OneItem;

    #[pymethods]
    impl OneItem {
        fn resolve(&self, py: Python<'_>, _context: &Bound<'_, PyAny>, _ignore: bool) -> Py<PyAny> {
            PyList::new(py, [1i64]).unwrap().into_any().unbind()
        }
    }

    /// `levels` nested `{% for %}` slots, the innermost with an empty body.
    fn nested_for(py: Python<'_>, sequence: &Py<PyAny>, levels: usize) -> Slot {
        let mut body = Vec::new();
        if levels > 1 {
            body.push(nested_for(py, sequence, levels - 1));
        }
        Slot {
            act: Act::For(Box::new(ForPlan {
                body,
                empty: Vec::new(),
                sequence: sequence.clone_ref(py),
                is_reversed: false,
                loopvar: PyString::new(py, "item").unbind(),
            })),
            node: py.None(),
            class_name: FOR_NODE.to_string(),
        }
    }

    /// Every slot in this plan is an `Act::For`, so the walk never reaches `emit`/`push_python` and the
    /// `escape` field below is never read.
    ///
    /// **The context, though, is read:** `run_for` asks the context whether a `forloop` is already there
    /// (the parent loop) and pushes the loop's own layer before resolving its sequence, the order
    /// `ForNode.render` uses (`engine/defaulttags.py:198-199`). So the fixture answers exactly those calls,
    /// which keeps the engine's own imports out of the row while letting the recursion reach the guard on a
    /// path a real loop also walks.
    #[pyclass]
    struct DepthContext;

    #[pymethods]
    impl DepthContext {
        fn __contains__(&self, _key: &Bound<'_, PyAny>) -> bool {
            false
        }

        fn push(&self, py: Python<'_>) -> Py<PyAny> {
            Py::new(py, DepthLayer).unwrap().into_any()
        }

        /// **The fourth call, and the deletion is what makes it necessary.** The walk writes the loop
        /// variable into the context once per iteration, unconditionally, in `ForNode.render`'s order
        /// (`engine/defaulttags.py:248`). It is a no-op because the plan has no `Emit`; what it buys is that
        /// the walk's own contract with the context is not narrower in the test than in the engine.
        fn __setitem__(&self, _key: &Bound<'_, PyAny>, _value: &Bound<'_, PyAny>) {}
    }

    /// What `push` hands back. `with` calls `__exit__` on it, and so does `run_for`, on both the success and
    /// the error path; nothing else of it is ever touched.
    #[pyclass]
    struct DepthLayer;

    #[pymethods]
    impl DepthLayer {
        fn __exit__(&self, _t: &Bound<'_, PyAny>, _v: &Bound<'_, PyAny>, _tb: &Bound<'_, PyAny>) {}
    }

    fn walk_depth_of(py: Python<'_>, slots: &[Slot]) -> PyResult<()> {
        let none = py.None().into_bound(py);
        let mut walk = Walk {
            py,
            context: Bound::new(py, DepthContext)?.into_any(),
            escape: none.clone(),
            scope: LookupScope::new(&none),
            autoescape: false,
            use_tz: None,
            out: String::new(),
            spill: None,
            nodes: 0,
            scoped_joins: 0,
            census: None,
            // This walk has no template, so it has no `render` to stand in for and every include
            // declines — which is what `render_plan` does for the same reason.
            bypassed: None,
        };
        walk.run(slots, 0)
    }

    #[test]
    fn the_depth_guard_raises_cpythons_error_at_the_cap_and_not_below_it() {
        Python::initialize();
        Python::attach(|py| {
            let sequence: Py<PyAny> = Py::new(py, OneItem).unwrap().into_any();
            // A plan exactly `MAX_WALK_DEPTH` loops deep is walked: the body of loop *n* is run at depth
            // *n*, so this is the deepest plan that reaches the innermost `run` at the cap without passing it.
            let at_cap = [nested_for(py, &sequence, MAX_WALK_DEPTH)];
            walk_depth_of(py, &at_cap).expect("a plan nested exactly MAX_WALK_DEPTH deep walks");
            let past = [nested_for(py, &sequence, MAX_WALK_DEPTH + 1)];
            let error = walk_depth_of(py, &past).expect_err("one loop past the cap raises");
            assert!(
                error.is_instance_of::<pyo3::exceptions::PyRecursionError>(py),
                "the guard must raise stock's error class, not a Rust abort: {error}"
            );
            assert_eq!(
                error.value(py).to_string(),
                "maximum recursion depth exceeded",
                "and stock's message, so a caller catching RecursionError sees one failure"
            );
        });
    }
}

// Declared here rather than at the top of the file **on purpose**: every anchor
// into `lib.rs` in `scripts/anchors.txt` is a line number, and a `mod` above them
// moves all of them. Rust does not care where a `mod` is declared.
mod materialise;
