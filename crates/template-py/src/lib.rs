//! PyO3 bindings exposing `template-core` as the `_native` extension module.
//!
//! This is the only crate permitted to depend on Python. The seam traits
//! defined in `template-core` are implemented here over `PyObject`.

use pyo3::create_exception;
use pyo3::exceptions::{
    PyAttributeError, PyException, PyIndexError, PyKeyError, PyNotImplementedError, PyRuntimeError,
    PyStopIteration, PyTypeError, PyValueError, PyZeroDivisionError,
};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};
use std::cell::{Cell, RefCell};
use std::rc::Rc;

// The filter seam: the real `FilterHost`, the value interner, and
// `FilterExpression.resolve`'s loop. A module rather than a section of this file
// because it is a self-contained subsystem with one entry point, and because
// `lib.rs` is already the parser's, the resolver's and the renderer's.
mod filters;

// Aliased to keep the call site readable: `CoreLexer` names the crate's lexer,
// as opposed to anything Python-side that might later be called a lexer.
use template_core::lexer::Lexer as CoreLexer;
use template_core::parser::desc::{
    AttrValue, FilterExprDesc, FilterVar, NodeDesc, NodeListDesc, TokenDesc, VariableDesc,
};
// Aliased because this crate already exports a `NodeRef` — the Python-facing
// handle a *descriptor* carries. The resolver's handles are `template-core`'s
// and are never the same type.
use template_core::parser::desc::NodeRef as CoreNodeRef;
use template_core::parser::resolve::{resolve_lookup, CallArm, LookupOutcome, LookupTarget, Step};
use template_core::parser::{Error, ErrorKind, Host, Parser as CoreParser};
use template_core::pynum::IntValue;
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

/// The registered names whose compile function is **still a milestone stub**,
/// ascending — the registry's own answer, derived there by asking each compile
/// function rather than from a second list.
///
/// Exposed for the same reason as `builtin_tag_names`, and with more at stake:
/// a milestone's *scope* is a prose phrase ("the remaining built-in tags") and
/// its *gate* is a set difference, so `tests/test_tag_inventory.py` needs the set
/// the engine believes is unfinished in order to subtract it from the tags that
/// are done. Derived in `template_core::parser::registry::builtin_tag_stubs`,
/// where the doc records why a hand-maintained list is not good enough here.
#[pyfunction]
fn builtin_tag_stubs() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_stubs()
}

/// Engine version, so the Python shell can assert it is talking to the build it
/// expects rather than a stale `.so` left in the tree.
#[pyfunction]
fn engine_version() -> &'static str {
    template_core::version()
}

/// Every built-in filter this engine implements, ascending — the Rust registry's
/// names.
///
/// The cross-check lives where both languages exist, for `builtin_tag_names`'
/// reason: Django's registry is only readable from Python, so
/// `tests/test_filter_inventory.py` is the test that compares the two, and this
/// is the half it cannot read any other way.
#[pyfunction]
fn filter_names() -> Vec<&'static str> {
    template_core::filters::filter_names()
}

/// The built-in filters whose body carries Django's `@stringfilter`, ascending.
///
/// Not one of the three measured flags and deliberately not folded into
/// `filter_flags`: `@stringfilter` is a *structural* fact about the body, not an
/// attribute `Library.register` sets. It exists as an FFI because it must be
/// compared against Django's live registry — `tests/test_filter_inventory.py`
/// derives the 29 from the pinned `defaultfilters.py`'s AST, and a transcription
/// into a Rust literal with no reader on the other side is a list nothing can
/// disagree with.
///
/// The corpus pins what the flag *does*; this pins which filters have it. Both
/// are needed, because the second can be wrong in the direction the first cannot
/// see: a filter wrongly marked `stringfilter` only misbehaves on a lazy
/// `__proxy__` input, which no ordinary corpus row supplies.
#[pyfunction]
fn stringfilter_filters() -> Vec<&'static str> {
    template_core::filters::stringfilter_names()
}

/// `(is_safe, needs_autoescape, expects_localtime)` for one built-in filter.
///
/// A tuple rather than three functions because the three flags are one row of
/// §10.3's milestone table and are compared together; and because the absence
/// Django distinguishes — `cut` and `escapejs` have **no** `is_safe` attribute at
/// all — is already invisible here: this returns the `bool` that
/// `getattr(f, "is_safe", False)` yields, which is what every reader of the
/// attribute does. `tests/test_filter_inventory.py` asserts both halves, so the
/// absence stays visible where it is real.
///
/// A name that is not a built-in filter is a `KeyError` rather than a default row:
/// three `False`s would be a plausible-looking answer for a typo.
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

/// Run one built-in filter over one value: `FilterExpression.resolve`'s loop.
///
/// The **only** entry point the corpus and the shell use, and deliberately the
/// only one: `filter_flags` reports flags and this applies them, so a test that
/// called a filter's body directly would be testing a path no template reaches.
/// `autoescape` and `use_tz` are the context's, and exist because seven filters
/// branch on the first and two on the second (§D1).
///
/// **`args` is the filter's argument list and not an `Option`, which is the one
/// shape this parameter must not have.** Django's loop builds `arg_vals`
/// (`base.py:804-809`) and calls `func(obj, *arg_vals)` (`:815`), so the list is
/// **empty for `{{ x|f }}` and one `None` for `{{ x|f:None }}`** — two different
/// calls, and `{{ x|floatformat:price }}` with `price = None` is a template a user
/// writes by accident. An `Option` cannot hold that distinction: `None` would
/// have to mean both "no argument" and "the argument `None`", and the second
/// would silently take the first's branch.
///
/// It was an `Option<&PyAny>` defaulting to `None` until this, and the collapse
/// was measured rather than argued: of the 23 landed filters that take an
/// argument, `floatformat`, `pluralize` and `unordered_list` answered where Django
/// raises — `floatformat:None` is `int(None)`'s `TypeError`, `pluralize:None` is
/// `TypeError: argument of type 'NoneType' is not a container or iterable` —
/// and the other 20 agreed only because their bodies happened to treat "no
/// argument" as `Value::None`. Requiring the length makes the distinction
/// structural: a caller cannot express "no argument" with a value. The third of
/// those is no longer an example of the collapse, because an argument to it now
/// refuses outright for the `base.py:813` reason below; that refusal is a better
/// answer than the one the body gave then.
///
/// **One divergence at `base.py:813` is the last of the split's, and the engine
/// refuses rather than diverging.** For a `needs_autoescape` filter Django calls
/// `func(obj, autoescape=context.autoescape, *arg_vals)` — the keyword goes in
/// **before** the positional arguments — so a filter whose own second parameter
/// is named `autoescape` cannot be given an argument at all: Python binds the
/// positional one to the same name and raises. Measured, five filters are in that
/// shape, and all five bodies refuse with this message:
///
/// ```text
/// {{ x|linebreaks:5 }}     -> TypeError: linebreaks_filter() got multiple values for argument 'autoescape'
/// {{ x|linebreaksbr:5 }}   -> TypeError: linebreaksbr() got multiple values for argument 'autoescape'
/// {{ x|linenumbers:5 }}    -> TypeError: linenumbers() got multiple values for argument 'autoescape'
/// {{ x|unordered_list:5 }} -> TypeError: unordered_list() got multiple values for argument 'autoescape'
/// {{ x|urlize:5 }}         -> TypeError: urlize() got multiple values for argument 'autoescape'
/// ```
///
/// `join` and `urlizetrunc` take an argument of their own, so their second
/// parameter is not `autoescape` and neither collides. Note what does not catch
/// the rest: `args_check` (`base.py:822`) counts `autoescape` among the filter's
/// own parameters, so the **parse accepts** `{{ x|linebreaks:5 }}` and the
/// refusal is at call time — a `TypeError`, not a `TemplateSyntaxError` — which is
/// why an implementation that rejected the token while parsing would be wrong.
///
/// The refusal is each **body's** and not this seam's, which is why the message
/// is a constant inside the five filter modules rather than a string composed
/// here. This seam takes `autoescape` as a *value*, so there is no keyword to
/// collide with, and the message needs the *callable's* Python name, which a
/// name-keyed seam does not have — `linebreaks_filter` is the pinned tree's `def`
/// name for the registered `linebreaks`, and a `{% load %}`-ed `linebreaks`
/// carries whatever name its library gave it. So the module that owns the filter
/// owns its message; the five `ERROR_CASES` rows that assert them are what fail
/// if a pin bump renames a callable, and §10.4 records that as a checklist item.
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
    filters::run_filter(py, name, value, args, autoescape, use_tz)
}

/// A filter failure (or a filter stub) as a Python exception — **for the errors
/// that never were one**.
///
/// Lives in `lib.rs` beside `parse_error` because it is the same job — carrying a
/// core `Error` back across the FFI — and because the exception *classes* it names
/// are this crate's.
///
/// **A raise inside a host call does not come through here.** The host keeps the
/// `PyErr` it caught and `run_filter` returns that object, so a caller's `except
/// SuspiciousOperation` — and its `except <a class of its own>`, raised by a
/// `__str__` the `@stringfilter` coercion called — sees exactly what Python
/// raised. See `PyFilterHost::host_error` for why keeping it is legitimate at
/// this seam and what the alternative measured.
///
/// What is left here is what `template-core` **synthesised**: a body's own
/// refusal (`wordwrap`'s `invalid width 0 (must be > 0)`), an unimplemented
/// filter, a parser error — errors with no exception object behind them, where
/// the class name is all there is to rebuild from.
///
/// `FilterHost`'s payload carries the class **and** the message separately, which
/// is why a body's `except IndexError` sees an `IndexError` rather than a generic
/// error. Reconstruction is by name in two steps: the six classes Django's filters
/// **catch** get a named `PyXError`, because those are the ones a body branches
/// on; everything else is looked up in `builtins` and instantiated with the
/// message. The second step is what makes `int(inf)`'s `OverflowError` arrive as
/// an `OverflowError` rather than as an `Exception` whose text begins with the
/// word — measured, and the two `filesizeformat` rows in `ERROR_CASES` are the
/// instrument that found it, since they compare `type(e).__name__` against
/// Django's. A name whose class refuses a single message argument
/// (`UnicodeDecodeError` needs five) or that is not in `builtins` at all falls
/// back to `Exception` with the name prefixed; `decimal.InvalidOperation` lands
/// there, because [`PyErrorKind`] records `__name__` and not the module it came
/// from, so the name alone cannot find it.
///
/// **A name this cannot rebuild is now reachable only from Rust**, which is what
/// keeps the fallback from being a hole: a body names its own kind, so a body
/// choosing a name `builtins` cannot instantiate is a bug in that body, and the
/// corpus row for it shows the wrong class immediately. The measured case that
/// used to sit here was the *host* half — `InvalidOperation`, which `builtins`
/// does not hold — and the host half no longer passes through this function at
/// all. For the record, since it was the argument for calling that gap
/// unreachable: on the pinned tree the name occurs at exactly three sites outside
/// tests (`defaultfilters.py:6`, `:155`, `:181`), every one inside `floatformat`
/// and all three inside an `except` clause that catches it (`:155`, `:158`,
/// `:181`), so no filter let one escape even then.
fn filter_py_error(py: Python<'_>, error: &Error) -> PyErr {
    let ErrorKind::FilterHost(inner) = &error.kind else {
        // An `Unimplemented` is a filter this engine has not built. Anything else
        // is a parser error, which a filter body cannot produce.
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

/// `builtins.<name>(message)` as a `PyErr`, or `None` if that is not a thing.
///
/// `None` for a name that `builtins` does not hold, and for a class whose
/// constructor will not take one string — the caller then prefixes the name onto
/// the message, which loses the class but not the word. Nothing here panics: the
/// input is a name Python produced, arriving on an error path, and an error path
/// that raises while reporting an error is worse than one that degrades.
fn rebuilt_builtin(py: Python<'_>, name: &str, message: &str) -> Option<PyErr> {
    let class = py.import("builtins").ok()?.getattr(name).ok()?;
    let instance = class.call1((message,)).ok()?;
    Some(PyErr::from_value(instance))
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

/// Python's `str.upper()` for a one-code-point string.
///
/// Exposed for the same reason `whitespace_codepoints` is: Rust's and CPython's
/// case tables are built from different Unicode releases, and nothing in
/// `cargo test` can compare the two. `tests/test_pyunicode_case_parity.py`
/// compares this against `chr(cp).upper()` for every code point.
///
/// Takes `&str` rather than a `u32` because the comparison on the Python side is
/// `_native.python_upper(ch) == ch.upper()` with `ch = chr(cp)`. A lone surrogate
/// cannot be passed — a Rust `&str` cannot hold one, so PyO3 raises — which is
/// why the test skips the surrogate range explicitly instead of relying on it.
#[pyfunction]
fn python_upper(s: &str) -> String {
    template_core::pyunicode::upper_str(s)
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

/// A position in the enclosing node's own nodelists — `AttrValue::NodeListRef`.
///
/// Its own type rather than a bare `int`, and the reason is an ambiguity that
/// would be silent: `AttrValue::Int` arrives in Python as an `int` too, so a
/// materialiser dispatching on `isinstance(value, int)` could not tell an
/// integer attribute from a nodelist reference. The failure mode is a node
/// handed a *wrong* nodelist, which no shallow test notices. `NodeRef` and
/// `SafeStr` exist for the same reason.
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
/// Filled by the **materialiser**, not by the host: the value that has to be
/// shared is a materialised Python object, so it does not exist during the parse
/// (`{% cycle %}`'s `CycleNode` is the producer). The descriptor carries the
/// index in two positions, and they are different things:
///
/// * as an attribute **value** — `ResetCycleNode.node` is the cycle's object;
/// * as a node descriptor's own fifth element — the descriptor *is* that object,
///   which is how a second `{% cycle c %}` resolves to the first one's node.
///
/// Its own type rather than a bare `int`, for `NodeListRef`'s reason: an integer
/// attribute arrives as an `int` too, and a materialiser dispatching on
/// `isinstance(value, int)` could not tell them apart.
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
        AttrValue::Variable(v) => variable_desc_to_py(py, v)?,
        AttrValue::FilterExpr(expr) => filter_expr_desc_to_py(py, expr)?,
        AttrValue::List(items) => {
            let items: Vec<Py<PyAny>> = items
                .iter()
                .map(|item| attr_value_to_py(py, item))
                .collect::<PyResult<_>>()?;
            PyList::new(py, items)?.into_any().unbind()
        }
        AttrValue::Map(entries) => {
            // A real `dict`, not a list of pairs. Python's dict has kept
            // insertion order since 3.7, so a list would buy nothing and lose
            // something: measured against `token_kwargs` (`base.py:1196`), which
            // assigns `kwargs[key] = …`, a repeated key **keeps its first
            // position and takes the last value** — which `set_item` reproduces
            // exactly and `dict(pairs)` would too, while a list handed to
            // `context.new()` would not. Built here rather than in the shell so
            // the shell's decoder stays a decoder.
            let mapping = PyDict::new(py);
            for (name, value) in entries {
                mapping.set_item(name, attr_value_to_py(py, value)?)?;
            }
            mapping.into_any().unbind()
        }
        AttrValue::NodeListRef(index) => Py::new(py, NodeListRef::new(*index))?.into_any(),
        AttrValue::NodeSlot(index) => Py::new(py, NodeSlot::new(*index))?.into_any(),
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

/// A `FilterVar` as a 2-tuple — Django's own `(lookup, value)`.
///
/// The `bool` is `lookup`, and the shell reads it when it decides whether to
/// `resolve` the `Variable` now or keep it for the context; see `FilterVar`'s
/// doc for why the value is a `Variable` on both sides of the flag.
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

/// A `FilterExpression` as it crosses the wire.
///
/// Three readable attributes, and the shapes are Django's with the filter
/// *function* replaced by its name: `filters` is a list of `(name, args)` pairs,
/// each `args` a list of `(lookup, Variable)` pairs — Django's
/// `filters.append((filter_func, args))` (`base.py:773`) with the lookup left to
/// the shell, which is the only side that can do it.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct FilterExpr {
    /// `FilterExpression.token`.
    #[pyo3(get)]
    pub token: String,
    /// `FilterExpression.var` **before materialisation**, as `(lookup, Variable)`.
    #[pyo3(get)]
    pub var: Py<PyAny>,
    /// `FilterExpression.filters`, as `[(name, [(lookup, Variable), …]), …]`.
    #[pyo3(get)]
    pub filters: Py<PyAny>,
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
pub fn filter_expr_desc_to_py(py: Python<'_>, desc: &FilterExprDesc) -> PyResult<Py<PyAny>> {
    let filters = desc
        .filters
        .iter()
        .map(|call| {
            let args: Vec<Py<PyAny>> = call
                .args
                .iter()
                .map(|arg| filter_var_to_py(py, arg))
                .collect::<PyResult<_>>()?;
            pair_to_py(py, &call.name, PyList::new(py, args)?.into_any().unbind())
        })
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    Ok(Py::new(
        py,
        FilterExpr {
            token: desc.token.clone(),
            var: filter_var_to_py(py, &desc.var)?,
            filters: PyList::new(py, filters)?.into_any().unbind(),
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

    // The fifth element, and it is not an attribute: `NodeDesc::slot` says this
    // node *is* the object in a reference-table slot, which the materialiser
    // reads before it looks at `attrs` — a `{% cycle c %}` descriptor has no
    // attributes at all. `None`, never `0`, when there is no slot: index 0 is a
    // real slot.
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
        // Django's `parse` catches `Exception`, not `TemplateSyntaxError`, so a
        // `ValueError` raised inside a tag body leaves `parse` as the *same
        // object* with `.token` attached below — `do_filter`'s unguarded
        // `_, rest = token.contents.split(None, 1)` is the one producer
        // (`defaulttags.py:744`). A `TemplateSyntaxError` with the same text
        // would satisfy every comparison in `tests/test_parser_errors.py`, so
        // without this arm the divergent class would be invisible.
        ErrorKind::ValueError(_) => PyValueError::new_err(error.message()),
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

/// The argument part of a Python exception, and nothing else.
///
/// `str(e)`, not `repr(e)` and not `PyErr`'s own `Display` — which pyo3 defines
/// as `"TemplateSyntaxError: Invalid filter: 'nope'"`, type name included. The
/// shell re-raises `TemplateSyntaxError` with exactly this text, so a prefix
/// would be visible in `str(exception)` and in every `assertRaisesMessage`.
///
/// The exception **object** is dropped here and only its message survives, which
/// is the loss `ErrorKind::Host`'s doc records: `_culprit_node` and
/// `__traceback__` live on the object (§5.2), and M4 replaces this function with
/// one that carries identity instead of text.
fn python_error_message(py: Python<'_>, error: &PyErr) -> String {
    match error.value(py).str() {
        Ok(text) => text.to_string_lossy().into_owned(),
        // Unreachable for a `TemplateSyntaxError`, whose `str()` is the message
        // it was built with. A host callable that broke this would fail in code
        // we do not own, so the failure is reported rather than panicked on.
        Err(_) => "<exception str() failed>".to_string(),
    }
}

/// The [`Host`] that puts Django's filter checks where Django calls them.
///
/// `has_tag` is `false` and `call_tag` panics, exactly as in [`NoHost`], and for
/// the same reason: `_native.parse` does not dispatch block tags yet, and this
/// host is built only for `parse_filter_expression`, whose token is a `{{ … }}`
/// body and so is never a block tag.
struct FilterCheckHost<'py> {
    /// The shell's `(name, arg_count) -> None` callable — Task 6's
    /// `parser.find_filter(name)` followed by
    /// `FilterExpression.args_check(name, func, [None] * arg_count)`, which is
    /// the pair Django calls at `base.py:771-772`. `None` accepts every name,
    /// which is what the Rust-only tests and the parity corpus's "no host"
    /// rows use.
    check_filter: Option<Bound<'py, PyAny>>,
    /// The per-parse reference table — the one side collection two tags share.
    ///
    /// One entry per `Host::alloc_slot`, a `None` placeholder until something
    /// fills it. `{% if %}`'s handle is filled during the parse; `{% cycle %}`'s
    /// node is filled by the *materialiser*, which is why the table outlives the
    /// parse and is handed to `materialise` as a third element of the descriptor
    /// tuple. Its lifetime is the parse and the materialisation that follows it,
    /// exactly as `PyTarget.arena`'s is the `resolve` call — a handle is
    /// meaningless outside the pair.
    refs: RefCell<Vec<Option<Py<PyAny>>>>,
}

impl FilterCheckHost<'_> {
    /// The table as the Python list the wire and the two callbacks use.
    ///
    /// A `list`, not a tuple: `materialise`'s `register_ref` is
    /// `list.__setitem__`, and reserving an index without filling it is a
    /// normal state rather than an error — a `None` placeholder is what the
    /// materialiser reads as "nothing is here yet, so build the node".
    fn ref_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let refs: Vec<Py<PyAny>> = self
            .refs
            .borrow()
            .iter()
            .map(|entry| match entry {
                Some(value) => value.clone_ref(py),
                None => py.None(),
            })
            .collect();
        Ok(PyList::new(py, refs)?.into_any().unbind())
    }
}

impl Host for FilterCheckHost<'_> {
    fn has_tag(&self, _command: &str) -> bool {
        false
    }

    fn call_tag(&self, _command: &str, _token: &Token) -> Result<NodeDesc, Error> {
        unreachable!("FilterCheckHost::call_tag is never reached: has_tag always returns false")
    }

    fn check_filter(&self, name: &str, arg_count: usize) -> Result<(), Error> {
        let Some(check) = &self.check_filter else {
            return Ok(());
        };
        match check.call1((name, arg_count)) {
            Ok(_) => Ok(()),
            // The callable raises Django's own exception, so its message is
            // already Django's; flattening it is what `ErrorKind::Host` asks for
            // at M2/M3.
            Err(error) => Err(Error::host(python_error_message(check.py(), &error))),
        }
    }

    /// Python's own `repr()`, which is why this override exists at all.
    ///
    /// [`Host::repr`]'s default (`py_repr_str`) is exact on ASCII only, and the
    /// M5 tag that reads it interpolates the **offending token** — arbitrary
    /// template text — through `%r`. Measured on the pinned tree:
    /// `{% with a=1 foo<U+00AD>bar %}` reports
    /// `'with' received an invalid token: 'foo\xadbar'`, which the Rust default
    /// would emit with the soft hyphen raw.
    ///
    /// The `expect` is a `repr()` of a `str` — a C slot with no failure mode but
    /// memory exhaustion, where the process is already lost. Falling back to the
    /// Rust approximation instead would put a *silently wrong* string in a
    /// message the parity suite compares exactly, which this project treats as
    /// strictly worse than a loud failure.
    fn repr(&self, s: &str) -> String {
        // `Python::attach` is the pinned PyO3's spelling — it has no
        // `Python::with_gil` (`pyo3-0.29.3/src/marker.rs`) — and it is cheap
        // here because the caller is already attached.
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
        // The placeholder is a Rust `None` rather than `py.None()`, because this
        // is called from `template-core` with no `Python<'_>` to hand — and it is
        // the right shape anyway: `ref_list` is the only reader, and "reserved
        // but not filled" is a state this table has rather than an error.
        let mut refs = self.refs.borrow_mut();
        refs.push(None);
        refs.len() - 1
    }
}

/// `FilterExpression.__init__` for a token the caller already knows is one.
///
/// `check_filter` is the host half of the parse: Django calls `find_filter` and
/// `args_check` *inside* the match loop (`base.py:771-772`), at a point no
/// batched shell-side pass can reproduce — see `Host::check_filter`. `None`
/// means "accept every filter name", which is what the Rust-side and
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
        refs: RefCell::new(Vec::new()),
    };
    match template_core::parser::filter_expr::parse_filter_expression(token, &host) {
        Ok(desc) => filter_expr_desc_to_py(py, &desc),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

/// Parse `source` into a node descriptor tree, with `host` at the seam.
///
/// Shared by `parse` and `render` rather than written twice, because the two
/// differ only in which host they build — and the host is the whole reason
/// Task 6's `check_filter` exists. A second copy would be the place a later
/// caller's host silently went missing.
fn parse_source(
    py: Python<'_>,
    source: &str,
    debug: bool,
    origin_template_repr: Option<String>,
    host: &dyn Host,
) -> PyResult<Py<PyAny>> {
    let tokens = if debug {
        CoreLexer::debug(source).tokenize()
    } else {
        CoreLexer::new(source).tokenize()
    };

    let mut parser = CoreParser::new(tokens, host);
    if let Some(repr) = origin_template_repr {
        parser = parser.with_origin_template_repr(repr);
    }

    match parser.parse(&[]) {
        Ok(nodelist) => nodelist_desc_to_py(py, &nodelist),
        Err(error) => Err(parse_error(py, &error)?),
    }
}

/// Attach the parse's reference table to its descriptor tuple.
///
/// `parse_source` returns `(nodes, contains_nontext)` — the nodelist alone — and
/// this appends the table, which lives on the host and so cannot be built from
/// the nodelist. Two callers, one place: `parse` hands the result to Python and
/// `render` hands it straight to `materialise`, and both need the third element.
///
/// The elements are re-read out of the tuple rather than returned separately, so
/// that the nodelist's wire shape has exactly one producer.
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

/// Parse `source` into a node descriptor tree, **with the reference table it
/// allocated**.
///
/// `debug` selects Django's `DebugLexer`, so token positions are recorded.
/// `origin_template_repr` is Python's `repr()` of `Origin.template_name`, or
/// `None` when the origin has no template name; it arrives pre-rendered because
/// a Python `repr` is Python's to produce — the rule §5.2 already records for
/// `LookupError::Missing::current_repr`.
///
/// The result is `(nodes, contains_nontext, refs)`. The third element is the
/// per-parse table `Host::alloc_slot` reserves into; a caller that materialises
/// passes it straight through (`materialise` reads the two callbacks off it), and
/// a caller that only inspects the tree can ignore it. It is part of the parse
/// result rather than a second call because it is a property *of this parse*:
/// `{% cycle %}`'s first mention reserves a slot that its second mention names,
/// so a table fetched separately could belong to a different parse.
///
/// `check_filter` is **Task 6's prerequisite, discharged here**: `None` means
/// "accept every filter name", which is what the descriptor-shape tests want,
/// but any caller that has a `Parser` in hand must pass
/// `materialiser.check_filter(parser)`. Without it a filter error defers to
/// materialisation and the parse-time ordering Django gives it (`find_filter`
/// then `args_check`, *inside* the match loop, `base.py:771-772`) is lost —
/// measured, `x|nope:1:2` reports `Invalid filter: 'nope'` with the host and
/// `Could not parse the remainder: ':2'` without it, and Django reports the
/// former. `render` passes one; see its comment.
#[pyfunction]
#[pyo3(signature = (source, debug = false, origin_template_repr = None, check_filter = None))]
fn parse(
    py: Python<'_>,
    source: &str,
    debug: bool,
    origin_template_repr: Option<String>,
    check_filter: Option<Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let host = FilterCheckHost {
        check_filter,
        refs: RefCell::new(Vec::new()),
    };
    let nodelist = parse_source(py, source, debug, origin_template_repr, &host)?;
    with_ref_table(py, nodelist, &host)
}

/// Which of Django's three `except` clauses a failed arm is being judged by, so
/// each swallowed set is read off `base.py` in one place instead of three.
enum Arm {
    /// `base.py:964` — `TypeError, AttributeError, KeyError, ValueError,
    /// IndexError`.
    Item,
    /// `base.py:966` — `TypeError, AttributeError`. This is the arm whose
    /// swallowed exception can still be re-raised, because Django's `dir()` line
    /// (`:970-977`) sits inside this `except`.
    Attr,
    /// `base.py:975` — `IndexError, ValueError, KeyError, TypeError`.
    Index,
}

/// Whether `error` is one this arm swallows — the only thing that decides
/// between falling through to the next arm and leaving the inner `try`.
///
/// Django's arms are `except` clauses, so a *subclass* matches and
/// `PyErr::is_instance_of` (which is `PyErr_GivenExceptionMatches`) is the right
/// test; comparing type *names* would not be, and would misjudge the two most
/// common rows — a `KeyError` from a dict-like and a `TypeError` from an
/// unsubscriptable object.
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
/// **Holds `Py<PyAny>`, not `Bound<'py, PyAny>`, and is therefore `'static`.**
/// That is forced, not stylistic: `Step::Found` boxes its child, and a boxed
/// child that borrowed from the container it came from would make
/// `current = found` in `resolve_lookup` a borrow error — the new `current`
/// would outlive the old one it borrows from. So each method re-acquires the
/// token itself with `Python::attach` — the pinned PyO3's spelling
/// (`pyo3-0.29.3/src/marker.rs:414`; that crate has no `Python::with_gil`), and
/// acquisition is reentrant, so being called under `_native.resolve`'s own token
/// is fine).
struct PyTarget {
    value: Py<PyAny>,
    /// Fresh per `resolve` call and shared by every container in it, so a handle
    /// is dereferenceable within the call that produced it and meaningless
    /// outside it. Deliberately **not** M2's `resolve_ref` table: that one is
    /// the shell's and resolves *descriptor* references, while these are values
    /// the caller asked for. A second persistent table would make `NodeRef`
    /// ambiguous, which is why this one is per-call and owns nothing on exit.
    arena: Rc<RefCell<Vec<Py<PyAny>>>>,
    /// The most recent exception an arm raised, held **by identity** until the
    /// port decides whether to re-raise it (§5.2 forbids flattening it to a
    /// string: that would lose `_culprit_node` and `__traceback__`).
    /// `Rc<Cell<..>>` rather than `Cell<..>` because `get_*` takes `&self` and
    /// the decision to raise happens in `outcome_to_py`, outside this object.
    ///
    /// Stashed on **every** failed arm, including the swallowed ones, because
    /// `LookupOutcome::Reraise` is returned in two different ways: an arm that
    /// raised something Django does not swallow, *and* an attribute arm whose
    /// swallowed `AttributeError` turned out to be one Django re-raises (its
    /// `dir()` line). The read always follows the failing arm immediately — the
    /// port never returns `Reraise` twice or later — so the most recent write is
    /// always the right one.
    pending: Rc<Cell<Option<PyErr>>>,
}

/// Hand-written rather than derived, because `Py<PyAny>` is not `Clone`: an
/// owned Python reference is copied with `clone_ref`, which needs the token.
/// A derived `Clone` is the one thing about this type that would compile if
/// `Py` were a plain `Rc`, and it is not — dropping the copy without the token
/// would be a refcount edit outside the GIL.
impl Clone for PyTarget {
    fn clone(&self) -> Self {
        Python::attach(|py| Self {
            value: self.value.clone_ref(py),
            arena: Rc::clone(&self.arena),
            pending: Rc::clone(&self.pending),
        })
    }
}

impl PyTarget {
    fn new(
        value: Py<PyAny>,
        arena: &Rc<RefCell<Vec<Py<PyAny>>>>,
        pending: &Rc<Cell<Option<PyErr>>>,
    ) -> Self {
        Self {
            value,
            arena: Rc::clone(arena),
            pending: Rc::clone(pending),
        }
    }

    /// Classify one arm's result: a swallowed exception is a miss the next arm
    /// covers, anything else is reported as `Raised`. Both are stashed; see
    /// `pending`.
    ///
    /// Written so the token never leaves its closure — a `Python<'_>` cannot be
    /// returned out of `Python::attach`, so the classification happens inside.
    fn step(&self, result: PyResult<Py<PyAny>>, arm: Arm) -> Step {
        match result {
            Ok(found) => Step::Found(Box::new(Self::new(found, &self.arena, &self.pending))),
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

    /// `bit in dir(current)`, **not** `hasattr`: `dir()` lists names statically,
    /// while `hasattr` invokes `getattr` — which would re-raise the very
    /// exception this predicate is being asked about. Measured: conflating the
    /// two turns the `@property` guard into a re-raise in *both* fixtures, which
    /// is exactly the distinction `resolve.rs`'s pair exists to hold.
    fn dir(&self, py: Python<'_>, bit: &str) -> bool {
        let Ok(names) = py
            .import("builtins")
            .and_then(|builtins| builtins.getattr("dir"))
            .and_then(|dir| dir.call1((self.value.bind(py),)))
        else {
            // A `dir()` that itself failed cannot be a real answer, and Django
            // would be raising out of the `except` handler — so "not in dir" is
            // the reading that keeps the loop's shape.
            return false;
        };
        names.contains(bit).unwrap_or(false)
    }
}

impl LookupTarget for PyTarget {
    /// `hasattr(type(current), "__getitem__")` — the **type**, not the instance.
    /// A `__getitem__` defined only on the instance is invisible to Django's
    /// guard (`base.py:963`), which is the classgetitem-versus-getitem rule the
    /// comment there cites.
    fn has_getitem(&self) -> bool {
        Python::attach(|py| {
            self.value
                .bind(py)
                .get_type()
                .hasattr("__getitem__")
                .unwrap_or(false)
        })
    }

    fn get_item(&self, bit: &str) -> Step {
        let result = Python::attach(|py| {
            self.value
                .bind(py)
                .get_item(bit)
                .map(|found| found.unbind())
        });
        self.step(result, Arm::Item)
    }

    fn get_attr(&self, bit: &str) -> Step {
        let result =
            Python::attach(|py| self.value.bind(py).getattr(bit).map(|found| found.unbind()));
        self.step(result, Arm::Attr)
    }

    fn get_index(&self, index: &IntValue) -> Step {
        let result = Python::attach(|py| -> PyResult<Py<PyAny>> {
            let key = match index {
                IntValue::Small(n) => (*n).into_pyobject(py)?.into_any().unbind(),
                // `IntValue::Big` exists because Python's `int()` is unbounded,
                // and its digits are canonical ASCII — the same route
                // `attr_value_to_py` takes for `AttrValue::BigInt`. Never
                // `eval`, never a formatted source string. Letting Python's own
                // `int()` do the conversion is what keeps the arm faithful for
                // an out-of-range index.
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
        // `getattr(type(current), bit)` raises for an *absent* attribute, and
        // that AttributeError is swallowed by Django's inner `except`
        // (`base.py:977`), so absent and truthy reach the same arm: the instance
        // attribute is not read. Only a *present and falsy* class attribute does
        // (`base.py:972-976`). Measured both ways — `{{ c.template }}` on a
        // `Context` renders `''` even though the instance holds `template=None`,
        // and a subclass with `zero = 0` renders the instance's `7`.
        //
        // `getattr` **walks the MRO**, and that is load-bearing rather than
        // incidental: `BaseContext` defines `push`, `pop`, `update`, `flatten`
        // and `__getitem__`, so for a `RequestContext` the class attribute
        // Django finds is two classes up. A `type(obj).__dict__` probe would
        // miss every inherited one, the condition would go false, and
        // `{{ ctx.push }}` would render a bound method instead of the `''` the
        // guard exists to produce. So this must stay `getattr`; the cheap check
        // once it exists is `{{ c.push }}` against a `RequestContext`, which
        // renders `''` here and a `<bound method ...>` repr if the MRO is
        // skipped.
        Python::attach(|py| {
            self.value
                .bind(py)
                .get_type()
                .getattr(bit)
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

    /// Django's three tests, folded in because only the host can read the two
    /// flags: `callable(current)`, then `do_not_call_in_templates`, then
    /// `alters_data` (`base.py:999-1003`).
    ///
    /// `None` — not a `CallArm` — when `callable(current)` is false, because
    /// that is when Django's arm does not run at all; folding it into
    /// `DoNotCall` would be right for one row and wrong for the reason.
    fn call_arm(&self) -> Option<CallArm> {
        Python::attach(|py| {
            let value = self.value.bind(py);
            if !value.is_callable() {
                return None;
            }
            let flag = |name: &str| {
                value
                    .getattr(name)
                    .ok()
                    .and_then(|flag| flag.is_truthy().ok())
                    .unwrap_or(false)
            };
            // Django's order, and it matters: a value with both flags set takes
            // the `pass` branch, so it is `DoNotCall`.
            if flag("do_not_call_in_templates") {
                Some(CallArm::DoNotCall)
            } else if flag("alters_data") {
                Some(CallArm::AltersData)
            } else {
                Some(CallArm::Call)
            }
        })
    }

    fn node(&self) -> CoreNodeRef {
        // Register in this call's arena and hand back the index. `template-core`
        // stores it and never reads it; `outcome_to_py` dereferences it below,
        // in the same call. The token stays inside the closure, as in `step`.
        //
        // A second `node()` on the same object pushes a second handle rather
        // than reusing the first. That is deliberate: the arena is not a map
        // keyed by identity, and a lookup that wanted identity would need
        // `Py::is`, which is the sort of cleverness that makes a per-call arena
        // into a cache with a lifetime question.
        Python::attach(|py| {
            let mut arena = self.arena.borrow_mut();
            arena.push(self.value.clone_ref(py));
            CoreNodeRef(arena.len() as u64 - 1)
        })
    }
}

/// Django's `BaseContext`, cached once so `is_context` is not an import per
/// lookup.
static BASE_CONTEXT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn base_context(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    BASE_CONTEXT.import(py, "django.template.base", "BaseContext")
}

/// Django's `VariableDoesNotExist`, cached for the same reason.
static VARIABLE_DOES_NOT_EXIST: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Django's own `VariableDoesNotExist`, constructed the way Django constructs
/// it — `(msg, params)`, with the message **left unformatted**.
///
/// `VariableDoesNotExist.__str__` does the `%` (`base.py:109-110`), so this
/// does not call `repr(container)`; measured on the pinned tree, the exception
/// constructs fine around a parameter whose `__repr__` raises, and raises only
/// at `str()`. Formatting here would reach back across the seam once per failed
/// lookup — the hottest failure path in the engine, since `{{ missing }}`
/// inside a `{% for %}` fails once per iteration — and would produce a message
/// Django never produces.
///
/// The class comes from `django.template.base` through a `GILOnceCell` rather
/// than from a Rust type, for the same reason `base_context` does: the class is
/// Python's, and raising a *different* exception class would make a
/// differential comparison fail on the class rather than on the message.
fn variable_does_not_exist(py: Python<'_>, bit: &str, container: Py<PyAny>) -> PyResult<PyErr> {
    let class =
        VARIABLE_DOES_NOT_EXIST.import(py, "django.template.base", "VariableDoesNotExist")?;
    // A Rust 2-tuple becomes a Python tuple, which is the shape `params` needs:
    // `self.msg % self.params`.
    Ok(PyErr::from_value(class.call1((
        "Failed lookup for key [%s] in %r",
        (bit, container),
    ))?))
}

/// Whether Django's outer handler substitutes for this exception instead of
/// re-raising it — `getattr(e, "silent_variable_failure", False)` (`base.py:1023`).
///
/// Read off the *instance*, and defaulted to `False` rather than to "missing is
/// an error": an exception class without the attribute is the common case, and
/// `getattr` with a default is exactly Django's own expression. A `getattr` that
/// raises for some other reason is a real failure and propagates rather than
/// being read as `False`.
fn silent_variable_failure(py: Python<'_>, error: &PyErr) -> PyResult<bool> {
    match error.value(py).getattr("silent_variable_failure") {
        Ok(flag) => Ok(flag.is_truthy()?),
        // `getattr(e, name, False)` catches `AttributeError` from the lookup —
        // including one raised by a property — and nothing else, and so does
        // this arm. A truthiness that raises is Django's `if` raising too, which
        // is why it is `?` rather than a default.
        Err(err) if err.is_instance_of::<PyAttributeError>(py) => Ok(false),
        Err(err) => Err(err),
    }
}

/// `context.template.engine.string_if_invalid` — Django's substitution for both
/// `alters_data` and a silent variable failure (`base.py:1024`).
///
/// Three plain attribute reads, deliberately not a `getattr` chain with
/// defaults: stock Django fails with `AttributeError: 'NoneType' object has no
/// attribute 'engine'` when `context.template` is unbound and
/// `AttributeError: 'dict' object has no attribute 'template'` when the caller
/// passed a mapping instead of a `Context`, and a port that substituted `""`
/// there would be quiet where Django is loud.
///
/// Not cached against the context: the engine's `string_if_invalid` is
/// per-engine, and a cache keyed on the context object would be a second source
/// of truth for it.
///
/// Takes no `Python<'_>`: a `Bound` already carries its interpreter, and a token
/// the body never reads is a parameter the next reader has to prove is unused.
fn string_if_invalid(context: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    Ok(context
        .getattr("template")?
        .getattr("engine")?
        .getattr("string_if_invalid")?
        .unbind())
}

/// Turn the port's verdict into Python's: hand back the value behind the
/// handle, raise Django's `VariableDoesNotExist`, or apply the host half of
/// Django's outer `except Exception` to the exception it stashed.
///
/// `context` is needed only by the [`LookupOutcome::Escaped`] arm, which is the
/// one place Django's handler reaches for `context.template.engine`.
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
        // The container's handle, which is what Django puts in `params`. It is
        // already in the arena: `node()` put it there when the loop gave up.
        LookupOutcome::Missing { bit, container } => {
            Err(variable_does_not_exist(py, &bit, value_behind(container)?)?)
        }
        // `pending` is the reason `LookupOutcome` does not carry the exception:
        // the enum derives `PartialEq` and is compared in `template-core`'s
        // tests, where no Python exists.
        //
        // Both branches of Django's handler are here, and **the substitution is
        // the one that was missing**: measured before this arm existed, a
        // `@property` raising an exception with `silent_variable_failure = True`
        // re-raised from here where stock Django substituted. Django logs first
        // (`base.py:1016-1021`); that is deliberately not reproduced — no test
        // reads it, and a `logger.debug` on the hottest lookup path would be
        // noise this port has no way to make useful.
        LookupOutcome::Escaped => {
            let error = pending
                .take()
                .expect("Escaped is only returned after an arm stashed an exception");
            if silent_variable_failure(py, &error)? {
                string_if_invalid(context)
            } else {
                Err(error)
            }
        }
        LookupOutcome::Unimplemented(message) => Err(PyNotImplementedError::new_err(message)),
    }
}

/// The Python object `Variable.literal` holds, for a descriptor that is a
/// literal rather than a path.
///
/// Everything but the quoted string is `attr_value_to_py`'s answer unchanged:
/// `Int`/`BigInt` are Python `int`s and `Float` is a Python `float`, which is
/// what `Variable.__init__` stores.
///
/// `SafeStr` is the exception, and the difference is a type that rendering
/// reads: Django stores `mark_safe(unescape_string_literal(var))`
/// (`base.py:912`), a `SafeString`, and returns it from `resolve` unchanged — so
/// a `SafeStr` wire object (not a `str` subclass, and not `SafeData`) would make
/// the value escape-able on a path Django never escapes. `mark_safe` is called
/// here rather than in the shell because `_native.resolve` returns the value
/// itself; the shell's `_decode_value` does the same conversion for descriptor
/// attributes.
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

/// Resolve a dotted path against a context, for the differential test.
///
/// A separate entry from `render` so the resolver can be compared against
/// `Variable._resolve_lookup` on paths that no template reaches — a nested
/// missing key, a `@property` that raises, a `BaseContext` attribute — which is
/// what makes the resolver *measured* rather than merely unit-tested.
///
/// **No `parser` argument**, unlike `render`: this mirrors `_resolve_lookup`,
/// which needs no filter registry. It raises the two exceptions `_resolve_lookup`
/// raises — `VariableDoesNotExist` from the arms, and whatever an object's own
/// code raised — and substitutes `string_if_invalid` in the two places
/// `_resolve_lookup` itself substitutes it, `alters_data` (M8) and a
/// `silent_variable_failure` (`base.py:1024`). It does **not** do `resolve`'s
/// substitution, the one for a missing key, which is one frame out and the
/// shell's. An unused parameter kept "for symmetry" would be a parameter the next
/// reader has to prove is unused.
///
/// The path is split by Task 3's `parse_variable`, so `a.b`, `5` and `_("x")`
/// are classified exactly as `Variable.__init__` classifies them, and then:
///
/// * `translate` → `Unimplemented`, naming M12 (D4). Checked **before** the
///   literal, because `_("x")` sets both: `resolve` applies translation to the
///   value, and returning the untranslated literal is the quiet wrong answer D4
///   exists to avoid.
/// * `literal` → the literal, without touching the context. This is
///   `Variable.resolve`'s first branch, and skipping it would make
///   `_native.resolve("5", {})` a lookup of the key `5`.
/// * `lookups` → the port, with one arena and one `pending` shared across the
///   call.
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

    if let Some(literal) = &desc.literal {
        return literal_to_py(py, literal);
    }

    let Some(lookups) = desc.lookups.as_ref() else {
        // `Variable.__init__` guarantees one of the two is set: every branch of
        // its `except ValueError` assigns a literal or the lookups. This is an
        // invariant of *our* parse, so it is a runtime error and not a template
        // error.
        return Err(PyRuntimeError::new_err(
            "a parsed variable has neither a literal nor lookups",
        ));
    };

    let arena = Rc::new(RefCell::new(Vec::new()));
    let pending = Rc::new(Cell::new(None));
    // Bound first, because `resolve_lookup` takes the context by value into the
    // root target and `outcome_to_py` needs it again for the one arm Django's
    // outer handler reads `context.template.engine` from. `Bound::clone` is a
    // refcount bump, not a conversion.
    let context = context.bind(py);
    let root = PyTarget::new(context.clone().unbind(), &arena, &pending);
    outcome_to_py(
        py,
        resolve_lookup(lookups, Box::new(root)),
        &arena,
        &pending,
        context,
    )
}

/// Parse, materialise and render — M3's end-to-end entry.
///
/// **Two Python arguments, not one, and the second is a defect the plan did not
/// anticipate.** D5 settled that the `Parser` comes from the caller, because
/// materialising a `FilterExpression` calls `parser.find_filter`. It did not
/// settle how Rust reaches the *materialiser*, and the answer the plan assumed
/// is impossible: the shell lives in `django_rust_templates/_impl`, whose
/// `__init__.py` is the shim's `django.template` and imports its own siblings
/// (`from .engine import Engine`), so `import
/// django_rust_templates._impl.parser` raises
/// `ModuleNotFoundError: No module named 'django_rust_templates._impl.engine'`
/// — measured. Under the shim the name `django.template.parser` would resolve,
/// and M3 runs unshimmed. So the shell hands its materialiser over, as one
/// module rather than three callables: `tree.check_filter(parser)`,
/// `tree.materialise(desc, None, None, parser)` and
/// `tree.template_for(nodelist)` are the functions this needs, and all three are
/// already named in that module. Handing over one object also keeps the seam to
/// a single argument, which is what makes it cheap to widen later.
///
/// The parse is driven **with the host**, which is Task 6's prerequisite: the
/// filter check must run inside the match loop or the error ordering Django
/// gives it is lost (see `parse`). `check_filter(parser)` is the shell's
/// `find_filter` + `args_check` pair, built here rather than in the shell so
/// that `render` cannot be called with a parser whose checks were forgotten.
///
/// **The render goes through `tree.template_for(nodelist, engine)`, not
/// `nodelist.render(context)`, and that is a third thing the plan did not
/// anticipate.** Rendering a nodelist needs a *bound* context, and binding is a
/// `Template`-level act: the render path reads `context.template.engine` for
/// `string_if_invalid` (`base.py:793`, `:997`, `:1005`, `:1011`, `:1024`) and
/// for `engine.debug` (`:1054`). With `context.template` left `None`, `{{ nope }}`
/// raises `AttributeError: 'NoneType' object has no attribute 'engine'` —
/// measured, on this very call. So the shell builds a real `Template` around the
/// tree (`__new__` plus assignment, because the constructor would parse the
/// source a second time in Python) and `Template.render` binds it. See
/// `template_for` in `_impl/parser.py`, which owns the attribute list.
///
/// **`engine` is a fifth argument, and it is the caller's for the same reason
/// the parser is.** The render path reads the engine in six places, so a shell
/// that handed over a parser built from one engine and let Rust build another
/// would render a *different answer* than the caller's engine — measured before
/// this argument existed:
/// `Engine(string_if_invalid="<<invalid>>")` renders `{{ nope }}` as
/// `'&lt;&lt;invalid&gt;&gt;'` through stock Django and rendered `''` through the
/// shell. Passing it keeps one engine on both halves of the call, and it is the
/// same argument D5 makes for the parser: Python knows which engine, and Rust
/// cannot construct one.
///
/// Nothing is caught. A `TemplateSyntaxError` from the parse and an exception
/// from the render must both propagate as themselves, because
/// `tests/test_render_parity.py` compares exception *types and messages* as well
/// as output.
#[pyfunction]
fn render(
    py: Python<'_>,
    source: &str,
    context: Py<PyAny>,
    parser: Py<PyAny>,
    tree: Py<PyAny>,
    engine: Py<PyAny>,
) -> PyResult<String> {
    let tree = tree.bind(py);
    let check = tree.getattr("check_filter")?.call1((parser.bind(py),))?;
    let host = FilterCheckHost {
        check_filter: Some(check),
        refs: RefCell::new(Vec::new()),
    };

    let desc = parse_source(py, source, false, None, &host)?;
    // The table is the render's own, and it is handed to `materialise` **with**
    // the descriptor rather than being left for the shell to find: this is the
    // hole §Review Focus 3 names. Before this, a `Ref` in the tree reached
    // `_decode_value` with no resolver and raised
    // `LookupError: … needs a resolve_ref callback`, because nothing produced a
    // table here. The two callbacks are the bound methods of the same list the
    // descriptor carries, so there is one table and no second place to lose it.
    let refs = host.ref_list(py)?;
    let resolve_ref = refs.bind(py).getattr("__getitem__")?;
    let register_ref = refs.bind(py).getattr("__setitem__")?;
    let desc = with_ref_table(py, desc, &host)?;
    let nodelist = tree.getattr("materialise")?.call1((
        desc,
        py.None(),
        resolve_ref,
        register_ref,
        parser.bind(py),
    ))?;
    let template = tree
        .getattr("template_for")?
        .call1((nodelist, engine.bind(py)))?;
    template
        .call_method1("render", (context,))?
        .extract::<String>()
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(builtin_tag_names, m)?)?;
    m.add_function(wrap_pyfunction!(builtin_tag_stubs, m)?)?;
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(filter_names, m)?)?;
    m.add_function(wrap_pyfunction!(stringfilter_filters, m)?)?;
    m.add_function(wrap_pyfunction!(filter_flags, m)?)?;
    m.add_function(wrap_pyfunction!(filter, m)?)?;
    m.add_function(wrap_pyfunction!(whitespace_codepoints, m)?)?;
    m.add_function(wrap_pyfunction!(decimal_digit_codepoints, m)?)?;
    m.add_function(wrap_pyfunction!(python_upper, m)?)?;
    m.add_function(wrap_pyfunction!(split_contents, m)?)?;
    m.add_function(wrap_pyfunction!(lex, m)?)?;
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(parse_variable, m)?)?;
    m.add_function(wrap_pyfunction!(parse_filter_expression, m)?)?;
    m.add_function(wrap_pyfunction!(resolve, m)?)?;
    m.add_function(wrap_pyfunction!(render, m)?)?;
    m.add_class::<NodeRef>()?;
    m.add_class::<NodeListRef>()?;
    m.add_class::<NodeSlot>()?;
    m.add_class::<SafeStr>()?;
    m.add_class::<Variable>()?;
    m.add_class::<FilterExpr>()?;
    m.add("ParseError", m.py().get_type::<ParseError>())?;
    m.add("UnimplementedTag", m.py().get_type::<UnimplementedTag>())?;
    Ok(())
}
