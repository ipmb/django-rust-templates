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
    AttrValue, FilterExprDesc, FilterVar, IfConditionDesc, NodeDesc, NodeListDesc, TokenDesc,
    VariableDesc,
};
// Aliased because this crate already exports a `NodeRef` — the Python-facing
// handle a *descriptor* carries. The resolver's handles are `template-core`'s
// and are never the same type.
use template_core::parser::desc::NodeRef as CoreNodeRef;
use template_core::parser::resolve::{
    resolve_lookup, CallArm, CallFailure, LookupOutcome, LookupTarget, PendingFailure, Step,
};
use template_core::parser::{Error, ErrorKind, Host, Parser as CoreParser};
use template_core::pynum::IntValue;
use template_core::token::{Token, TokenType};

/// Every command name in Rust's tag registry, ascending, **stubs included**.
///
/// Exposed because the cross-check lives where both languages exist: Django's
/// registry is only readable from Python, and a Rust test cannot see `.django`.
/// The list is the `REGISTRY` table in `template_core::parser::registry`, which
/// is written once and in full (§10.4) — a name missing here is a built-in tag
/// this engine would report as unknown.
///
/// The name carries "including stubs" because it did not used to, and the
/// misreading cost three separate afternoons: this is the registry's keys, not
/// the tags Rust implements. Subtract `builtin_tag_stub_names()` for the latter.
#[pyfunction]
fn builtin_tag_names_including_stubs() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_names_including_stubs()
}

/// The registry's names whose compile function is **still a milestone stub**,
/// ascending — the registry's own answer, derived there by asking each compile
/// function rather than from a second list.
///
/// Exposed for the same reason as `builtin_tag_names_including_stubs`, and with
/// more at stake: a milestone's *scope* is a prose phrase ("the remaining
/// built-in tags") and its *gate* is a set difference, so
/// `tests/test_tag_inventory.py` needs the set the engine believes is unfinished
/// in order to subtract it from the tags that are done. Derived in
/// `template_core::parser::registry::builtin_tag_stub_names`, where the doc
/// records why a hand-maintained list is not good enough here.
#[pyfunction]
fn builtin_tag_stub_names() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_stub_names()
}

/// Every **library** tag this engine compiles, ascending — the Rust library
/// registry's names.
///
/// Exposed because the shell cannot derive it. `ShellHost._rust_owned` has to
/// name the library tags Rust owns in order to answer `has_tag` for them the way
/// it answers for a built-in, and the natural-looking source — the tag tables of
/// the five `django.templatetags` modules — holds **all 18** library tags, the
/// same eighteen this registry carries as of M12 Task 6b (0 of the 18 are
/// Python's). It was not always the same set: while a library was only partly
/// ported, claiming its unported names would have sent `trans`, `timezone` and
/// the rest to `Invalid block tag` instead of to the host that compiles them.
/// That is the same shape as the `245 failures of the 1547` measurement recorded
/// on `builtin_tag_names_including_stubs`, one scope over.
///
/// The doc on `template_core::parser::registry::library_tag_names` carries the
/// argument; this is only the wire.
#[pyfunction]
fn library_tag_names() -> Vec<&'static str> {
    template_core::parser::registry::library_tag_names()
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
/// The cross-check lives where both languages exist, for
/// `builtin_tag_names_including_stubs`' reason: Django's registry is only
/// readable from Python, so
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
/// (`base.py:804-809`) and calls `func(obj, *arg_vals)` (`base.py:815`), so the
/// list is **empty for `{{ x|f }}` and one `None` for `{{ x|f:None }}`** — two
/// different calls, and `{{ x|floatformat:price }}` with `price = None` is a
/// template a user writes by accident. An `Option` cannot hold that
/// distinction: `None` would have to mean both "no argument" and "the argument
/// `None`", and the second would silently take the first's branch.
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
/// unreachable: outside the test suite the name occurs on nine lines, six of
/// them in filter modules — two imports (`defaultfilters.py:6`,
/// `humanize.py:3`) and four uses, each in an `except` clause that catches it.
/// Three of the uses are `floatformat`'s (`defaultfilters.py:155`,
/// `defaultfilters.py:158`, `defaultfilters.py:181`) and the fourth is
/// `intcomma`'s (`humanize.py:80`), so no filter let one escape even then. The
/// other three are outside the template engine (`layermapping.py:12` and
/// `layermapping.py:419`, through its `DecimalInvalidOperation` alias, and
/// `db/models/fields/__init__.py:1818`), none of them a filter.
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

/// `AttrValue::Tokens` — a `BlockTranslateNode`'s `singular`/`.plural`.
///
/// The one attribute on the wire that is a slice of the **token stream** rather
/// than a subtree: `do_block_translate` scans its body token by token
/// (`i18n.py:560-578`) and hands the node the `Token` objects themselves, which
/// `render_token_list` (`i18n.py:134-146`) walks by `token.token_type` and
/// `token.contents`. See `AttrValue::Tokens` for why no existing variant can
/// carry that.
///
/// **Its own class rather than a bare `list`**, and the reason is
/// `NodeListRef`'s turned around: a bare list of 4-tuples would decode through
/// `_decode_value`'s *list* arm into a list of plain tuples, and
/// `render_token_list`'s `token.token_type` would then be an `AttributeError`
/// at **render** time — after a parse that looked fine. The payload is a Python
/// `list` of the same `(token_type, contents, lineno, position)` tuples
/// `_native.lex` and `NodeDesc.token` emit, so the shell rebuilds each element
/// with the one `_decode_token` it already has and the token wire form stays
/// single-spelt.
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

/// `AttrValue::IfCondition` — one node of a `{% if %}` condition.
///
/// The one attribute on the wire whose value is a parse **tree**, and the
/// reason is that its leaves are Python objects: `IfNode.render` calls
/// `condition.eval(context)` on each branch (`defaulttags.py:326`), an
/// `Operator`'s `eval` recurses through its own `first`/`second`, and a
/// `TemplateLiteral`'s calls
/// `self.value.resolve(context, ignore_failures=True)` (`defaulttags.py:917-918`).
/// So the shell rebuilds real `smartif` objects and this is the shape it
/// rebuilds them from.
///
/// **One class with two kind-specific halves rather than two classes**, because
/// the materialiser dispatches on `kind` and both halves are needed at once by
/// nothing. `key`/`first`/`second` are the operator's — `key` is one of
/// `smartif.OPERATORS`' thirteen, and `second` is `None` for the prefix `not`,
/// which `nud` sets (`smartif.py:81-83`). `text`/`expression` are the literal's:
/// `TemplateLiteral.text` is what `display()` returns and `expression` is the
/// **host's** `FilterExpression`, by handle into the same table `NodeRef`
/// indexes.
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
    /// The operator's right operand — `None` on a literal, and also `None` on a
    /// **prefix** operator, which is a different fact with the same spelling and
    /// is why the materialiser branches on `kind` first.
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
        AttrValue::FilterExpr(expr) => filter_expr_desc_to_py(py, expr, None)?,
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
            // something: measured against `token_kwargs` (`base.py:1202`), which
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
        AttrValue::Tuple(items) => {
            // A real `tuple`, and the container type is the contract:
            // `IfNode.conditions_nodelists` holds `(condition, nodelist)` pairs
            // that `IfNode.render` *unpacks* (`defaulttags.py:323`), and the
            // parity suite can tell a list from a tuple because `describe`'s
            // tuple arm carries a marker. Recursed, for the `List` arm's reason.
            let items: Vec<Py<PyAny>> = items
                .iter()
                .map(|item| attr_value_to_py(py, item))
                .collect::<PyResult<_>>()?;
            PyTuple::new(py, items)?.into_any().unbind()
        }
        AttrValue::IfCondition(tree) => if_condition_to_py(py, tree)?,
        AttrValue::Tokens(tokens) => {
            // The elements are tokens, so each goes through `token_to_py` — the
            // one spelling of a token's wire form, shared with `_native.lex` and
            // `NodeDesc.token`. The list is built here, inside the pyclass, so
            // the shell's decoder stays a decoder; `_decode_value` recognises the
            // class and rebuilds each element with `_decode_token`.
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
///
/// Recursive, and the recursion is the shape: an `Operator` is two more
/// conditions and a `TemplateLiteral` is a leaf. `first` is `Some` on every
/// operator — `nud` and `led` both set it (`smartif.py:54-56`,
/// `smartif.py:81-83`) — and the `expect` says so rather than emitting a `None`
/// the shell would turn into an `AttributeError` three frames away.
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
/// *function* replaced by its name: `filters` is a list of `((name, args),
/// handle)` pairs, each `args` a list of `(lookup, Variable)` pairs — Django's
/// `filters.append((filter_func, args))` (`base.py:773`), with the lookup left
/// to the shell and the function itself carried beside the name as a handle.
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
    /// The handle is a `NodeRef` into [`Self::refs`], or `None` when the host ran
    /// no filter check. See `FilterCall::handle` for why the name on its own is
    /// not enough.
    #[pyo3(get)]
    pub filters: Py<PyAny>,
    /// The reference table this descriptor's handles index into, or `None` for a
    /// descriptor built outside a parse that owns one.
    ///
    /// **Carried on the descriptor and not only in `_native.parse`'s tuple,
    /// because `parse_filter_expression` is a parse with a table of its own.**
    /// That entry point builds a `FilterCheckHost`, asks it for handles and used
    /// to drop the table on return, which would leave every handle it produced
    /// dangling — an index into nothing. A handle is meaningless outside the
    /// pair (the table is the parse's, the handle is an index into it), so the
    /// pair travels together. The whole-template path is unaffected:
    /// `materialise` derives `resolve_ref` from the parse's table, which is the
    /// same list, so a nested `FilterExpr` resolves against one table either way.
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
/// `refs` is the table the descriptor's handles index into — the parse's own, or
/// `None` for a descriptor that carries no handles (every nested one: a `{{ … }}`
/// inside a node's attributes resolves through `materialise`'s `resolve_ref`,
/// which is the same table).
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
            // Django's `(filter_func, args)` pair, with the name in the
            // function's place and the function itself beside it: the pair
            // stays one thing, because it is one thing that renders.
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
///
/// `class_name` rides the same way, for a host exception, and is the same trick
/// for the same reason: design decision 2 gives the exception *object* to
/// Python, so a `ParseError` cannot be the class Django would have raised — what
/// the shell needs from here is **which** class to raise instead. See
/// [`python_host_error`] for the measurement that made the class worth carrying,
/// and note that nothing reads this attribute yet: the shell's conversion is
/// M9's, and this is the information it will need.
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

/// The token Django attached to a host exception, as a [`TokenDesc`].
///
/// Read from `token_wire`, the attribute `ShellHost.call_tag` sets on its way
/// out (`python/django_rust_templates/host.py`) — not from Django's own
/// `.token`, which is a `Token` object and would mean this crate naming its
/// fields. The wire form is the same four-field tuple `_native.lex` emits, so
/// the mapping back is one `TokenType::from_wire` and no new shape.
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
/// [`python_error_message`], plus the two things the tag seam was dropping: the
/// token Django attached and the class Python raised. Both are why this exists,
/// and both are measured on the pinned tree.
///
/// A tag's compile function can re-enter Django's parse, so when its body fails
/// the exception arrives already carrying the **innermost** token, set by the
/// nested frame. Flattening it to a message loses that, and Rust's own
/// `Parser::error` — whose `token.or_else(...)` guard exists precisely to honour
/// an already-set token — then attaches the **outer** tag's token instead:
///
/// ```text
/// {% load custom %}{% div %}{% now a b %}{% enddiv %}
/// → Django: "'now' statement takes one argument", token (2, 'now a b', 1, None)
/// → ours without the token: the same message, token (2, 'div', 1, None)
/// ```
///
/// The class diverges for a plainer reason: Django's `Parser.parse` re-raises
/// the *same object* (`base.py:583-585`), so a tag body that raises `TypeError`
/// leaves the parse as one, and every comparison in `tests/test_parser_errors.py`
/// compares text and would call the two equal:
///
/// ```text
/// {% load cls %}{% boom %}     # boom raises TypeError("not a syntax error")
/// → Django: TypeError('not a syntax error')
/// → ours without the class: ParseError('not a syntax error')
/// ```
///
/// The token comes from `token_wire`, the attribute `ShellHost.call_tag` sets on
/// its way out (`python/django_rust_templates/host.py`), and the class from the
/// exception object itself — [`py_error_kind`](filters::py_error_kind), the same
/// mapping the filter seam has always used, so there is one Python→seam class
/// table in this crate and not two. Neither can be missing for an exception that
/// came through `call_tag`: the shell always attaches the first, and Python
/// always has a class for the second.
fn python_host_error(py: Python<'_>, error: &PyErr) -> Error {
    Error::host_exception(
        python_error_message(py, error),
        filters::py_error_kind(py, error),
        python_host_token(py, error),
    )
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
    /// The shell's `token -> FilterExpression` callable — that is, Django's own
    /// `Parser.compile_filter` (`base.py:676`), which is
    /// `TemplateIfParser.create_var`'s
    /// `self.template_parser.compile_filter(value)` (`defaulttags.py:929`).
    ///
    /// **A second callable rather than "the parser", because the first one is
    /// already composed in Python** — `check_filter(parser)` is `find_filter`
    /// followed by `args_check`, which is the pair Django calls at
    /// `base.py:771-772`, and moving that composition into Rust would put a
    /// shape into the seam that the seam does not have. This one is the shell's
    /// `parser.compile_filter`, one call and no composition.
    ///
    /// `None` means the caller gave us none, which is an **error and not a
    /// default**: unlike `check_filter`, there is no permissive answer. The
    /// object this returns is a real `FilterExpression` that
    /// `TemplateLiteral.eval` resolves at render time, so "accept every token"
    /// cannot be spelled. A parse that reaches a condition without one fails
    /// loudly — see `Host::compile_filter` below.
    compile_filter: Option<Bound<'py, PyAny>>,
    /// The per-parse reference table — the one side collection two tags share.
    ///
    /// One entry per `Host::alloc_slot`, a `None` placeholder until something
    /// fills it. `{% if %}`'s handle is filled during the parse; `{% cycle %}`'s
    /// node is filled by the *materialiser*, which is why the table outlives the
    /// parse and is handed to `materialise` as a third element of the descriptor
    /// tuple. Its lifetime is the parse and the materialisation that follows it,
    /// exactly as `PyTarget.arena`'s is the `resolve` call — a handle is
    /// meaningless outside the pair.
    ///
    /// **It is the Python `list` itself, not a Rust `Vec` copied out on read,
    /// and M13's Task 4 is why.** A handle is an *index*, so the index is
    /// meaningful only against one table; a copy made per read therefore makes
    /// the same index mean two things. `{% partialdef %}` materialises its body
    /// *during* the parse (`FilterCheckHost::register_partial_def`), so with a
    /// per-read copy the body's materialisation writes into a list that is then
    /// discarded and the outer materialisation reads a second one — a
    /// `{% cycle %}` slot reserved outside the body and filled inside it would
    /// come back empty and build a second node for one slot. The failure that
    /// forced the change is blunter than that and was measured on the pinned
    /// tree: a body containing *any* filter expression arrives as a handle, so
    /// the body could not be materialised at all without a resolver
    /// (`LookupError: the filter 'default' arrived as <NodeRef 0> but no
    /// resolve_ref was passed to materialise …`), and 27 of the 1,547 went red.
    /// The list is created **before** the parse and lives here, on the host —
    /// the object the parser borrows for the parse's whole life.
    refs: Py<PyAny>,
    /// The shell's tag seam, one object per parse — `ShellHost` in
    /// `python/django_rust_templates/host.py`.
    ///
    /// **One object rather than two callables**, and that is a correction to the
    /// plan, which had `has_tag` and `call_tag` travel separately. They are a
    /// single thing in both directions: `has_tag` is consulted first and
    /// `call_tag` is called only for a name it accepted, so the two must be
    /// answering about the *same* registry at the *same* moment, and the
    /// registry is one `Parser`'s dict. Two callables would let a caller pair
    /// them across parses — a `has_tag` from one and a `call_tag` from another —
    /// with no error anywhere, which is the same silent-wrong-source shape the
    /// filter handle's table exists to prevent.
    ///
    /// `None` is [`NoHost`]'s behaviour exactly: `has_tag` answers `false` and
    /// `call_tag` is unreachable. That is what keeps
    /// `_native.parse(source, check_filter=None)` — every Rust-only test and the
    /// parity corpus's "no host" rows — working unchanged.
    ///
    /// [`NoHost`]: template_core::parser::host::NoHost
    host: Option<Bound<'py, PyAny>>,
}

impl FilterCheckHost<'_> {
    /// The table as the Python list the wire and the two callbacks use.
    ///
    /// A `list`, not a tuple: `materialise`'s `register_ref` is
    /// `list.__setitem__`, and reserving an index without filling it is a
    /// normal state rather than an error — a `None` placeholder is what the
    /// materialiser reads as "nothing is here yet, so build the node".
    ///
    /// **The same object every time, and that is the contract.** This used to
    /// build a fresh `PyList` from the `Vec` on each call, which is the snapshot
    /// the field's own comment rules out: two readers of one index must
    /// subscript one table. It is a handle *out* of the host, so it is a
    /// `clone_ref` and not a copy — `FilterCheckHost::register_partial_def`
    /// hands this list to the shell mid-parse and `compile_template` hands it to
    /// `materialise` afterwards, and both must see the same entries.
    fn ref_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.refs.clone_ref(py))
    }

    /// Reserve the next slot and fill it in one step, returning its handle.
    ///
    /// Every producer that *has* the object at the moment it reserves goes
    /// through this — a host-built node (`call_tag`, `partial_node`), a
    /// host-bound filter function (`check_filter`), and the `PartialDefNode`
    /// the shell returns from `register_partial_def`. Only
    /// [`Host::alloc_slot`] reserves without filling, and it is the only caller
    /// that leaves a `None` placeholder behind.
    ///
    /// It appends to the same list `alloc_slot` does, which is the contract the
    /// field's comment states: one table per parse, so an index reserved here
    /// is readable by a materialisation that starts later — and by one that
    /// started earlier and is still running, which is `{% partialdef %}`'s case.
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
        // A raise here is an integration bug in the shell, not a verdict on the
        // template, and the trait returns a plain `bool` with nowhere to put it.
        // A panic is therefore the loud option rather than swallowing it as
        // `false`, which would silently route a host tag to Rust's own registry
        // and produce `Invalid block tag` for a tag the host was ready to
        // compile.
        host.call_method1("has_tag", (command,))
            .and_then(|answer| answer.extract::<bool>())
            .expect("ShellHost.has_tag raised or did not return a bool")
    }

    fn library_tag_loaded(&self, command: &str) -> bool {
        let Some(host) = &self.host else {
            // No host means no parser and no dict of loaded tags, which is
            // `NoHost`'s answer and this method's default. `false` rather than a
            // panic, unlike `call_tag`: nothing is *expected* to be here, and a
            // bare membership question with nothing to be a member of is `false`.
            return false;
        };
        // A raise here is a shell integration bug for `has_tag`'s reason, and the
        // trait has nowhere to put it, so it panics loudly rather than answering
        // `false` — which would report `Invalid block tag` for a tag the host was
        // ready to hand to Rust's registry.
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
        // The dispatched token travels as its **wire tuple**, not as a Python
        // `Token`: building one here would mean this crate naming a Django
        // class, and the shell already builds `Token`s for the stream. The
        // shell turns this into `Token(TokenType(t), contents, position, lineno)`
        // — Django's own signature — so the compile function receives a real
        // token, which is what `token.split_contents()` needs.
        let answer = host
            .call_method1("call_tag", (command, wire(token), stream))
            .map_err(|error| python_host_error(py, &error))?;
        let (node, left) = match answer.extract::<(Bound<'_, PyAny>, Bound<'_, PyAny>)>() {
            Ok(pair) => pair,
            // Not `Error::host`: no Python exception was raised, so claiming one
            // would send the reader looking for a traceback that does not exist.
            // This is the seam's own contract failing, which is `Internal`.
            Err(error) => {
                return Err(Error::internal(format!(
                    "ShellHost.call_tag({command:?}) did not return a (node, tokens) pair: {error}"
                )))
            }
        };
        // A4: the flag crosses **inbound**. This is the Python node the host's
        // compile function just returned, and `must_be_first` is a class
        // attribute every Django `Node` carries (`base.py:1031-1035`) which
        // `extend_nodelist` reads unconditionally (`base.py:602`). M6 carried
        // every other node field but this one, so a host tag returning a node
        // class that sets it was *accepted* where Django refuses.
        //
        // **Raise rather than default `false`.** An object without the attribute
        // is not a node, and defaulting would turn "the host returned the wrong
        // thing" into "no ordering check needed" — silently accepting exactly
        // the shape this read exists to refuse. The outbound 5-tuple
        // (`node_desc_to_py`) is untouched: it is a descriptor-to-shell wire,
        // and this flag never travels that way.
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
        // The node the host built goes into the same table `{% if %}`'s
        // `FilterExpression` and `{% cycle %}`'s slot live in: it is a Python
        // object this side must not read, and a `NodeRef` is how a descriptor
        // names one (§5.2). The materialiser's `HOST_NODE` arm returns it.
        //
        // `fill_slot` takes ownership of `node`, which is why `must_be_first`
        // was read off it above; it was set here rather than there so the
        // descriptor carries the host's own answer and not this side's default.
        let handle = self.fill_slot(node);
        let mut desc = NodeDesc::host(handle);
        desc.must_be_first = must_be_first;
        Ok((desc, left))
    }

    fn check_filter(&self, name: &str, arg_count: usize) -> Result<Option<CoreNodeRef>, Error> {
        let Some(check) = &self.check_filter else {
            return Ok(None);
        };
        // The shell's callable *returns* the function it found — it is
        // `parser.find_filter(name)` with its result handed back rather than
        // discarded — and the `args_check` still runs inside it, at Django's
        // line, so the error ordering the M5 measurements pinned is unchanged.
        match check.call1((name, arg_count)) {
            Ok(func) => {
                // The same table `alloc_slot` reserves into, for `{% cycle %}`'s
                // reason: a handle is an index into the parse's table, and this
                // is the parse. Reserved-and-filled in one step, so no `None`
                // placeholder is ever visible for this producer.
                Ok(Some(self.fill_slot(func)))
            }
            // The callable raises Django's own exception, so its message is
            // already Django's; flattening it is what `ErrorKind::Host` asks for
            // at M2/M3.
            Err(error) => Err(Error::host(python_error_message(check.py(), &error))),
        }
    }

    fn partial_node(&self, name: &str) -> Result<CoreNodeRef, Error> {
        let Some(host) = &self.host else {
            // Reachable, unlike `call_tag`'s `unreachable!`: `has_tag` answering
            // `false` with no host routes `{% partial %}` *towards* Rust's
            // registry rather than away from it, and the registry has a body as
            // of M13's Task 3 — so `_native.parse("{% partial p %}")` with no
            // `ShellHost` lands here. `Internal` for `compile_filter`'s reason:
            // nothing raised a Python exception, so this is the caller's
            // integration bug and not a `Host` error flattened.
            return Err(Error::internal(
                "this parse reached {% partial %} with no host; pass a \
                 `ShellHost` to `_native.parse`",
            ));
        };
        // The object is `ShellHost.partial_node`'s — a real `PartialNode`
        // holding a live `DeferredSubDict` — and it goes into the same table
        // `call_tag`'s nodes and `{% cycle %}`'s slots live in: a `NodeRef` is
        // how a descriptor names an object this side must not read (§5.2), and
        // the materialiser's `HOST_NODE` arm returns it.
        //
        // The name crosses **unresolved**, and that is the seam's contract
        // rather than an omission: resolving it here would snapshot parser
        // state at compile time and break `{% partial p %}{% partialdef p %}`,
        // a template Django renders. `test_partial_node_does_not_resolve_the_name`
        // is the row that pins it on the Python side.
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
            // Reachable for `partial_node`'s reason, and reached the same way:
            // `ShellHost.has_tag` answers `False` for `partialdef` — the
            // registered callable is still `defaulttags`' — so the tag routes
            // *to* Rust's registry with no host in play.
            return Err(Error::internal(
                "this parse reached {% partialdef %} with no host; pass a \
                 `ShellHost` to `_native.parse`",
            ));
        };
        let py = host.py();
        // The dispatched token travels as its wire tuple, not as a Python
        // `Token`, for `call_tag`'s reason: this crate does not name a Django
        // class, and the shell rebuilds `Token(TokenType(t), contents,
        // position, lineno)` — the signature its own `register_partial_def`
        // takes. It needs a real token because `Template.get_exception_info`
        // reads the one attached to the duplicate-definition
        // `TemplateSyntaxError`.
        let wire = (
            token.token_type as u8,
            token.contents.clone(),
            token.lineno,
            token.position,
        );
        // The body crosses as the **descriptor** tuple `parse_source` hands out
        // (`(nodes, contains_nontext)`) — plus the parse's **reference table**
        // as its third element, which is what makes the body materialisable at
        // all. A handle in the body is an index into the parse's one table, and
        // `materialise` derives `resolve_ref`/`register_ref` from the third
        // element when the caller passes none (which `ShellHost` does): without
        // it, a body containing any filter expression arrives as a `NodeRef`
        // with no resolver and dies with
        // `LookupError: the filter 'default' arrived as <NodeRef 0> but no
        // resolve_ref was passed to materialise …`. Measured on the pinned tree
        // — 27 of the 1,547 went red before this line existed. The two callbacks
        // are bound methods of the *same* list `compile_template` later hands to
        // the outer `materialise`, so a slot the body fills is filled for the
        // whole tree (`{% cycle %}`'s slot reserved outside a body and named
        // inside it is the case that notices).
        //
        // The shell is still what materialises it: `materialise` builds one
        // `NodeList` that the stored `PartialTemplate` and the returned
        // `PartialDefNode` share *by identity* (`defaulttags.py:1258`,
        // `defaulttags.py:1265`). Materialising here would need the shell's
        // materialiser, and building it twice would give the inline and deferred
        // render paths different trees.
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
        // The same table `partial_node`'s node and `{% cycle %}`'s slots live
        // in, reserved-and-filled in one step so no `None` placeholder is ever
        // visible for this producer.
        Ok(self.fill_slot(node))
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
        // `list.append(None)` on the parse's **own** list, which is what lets a
        // slot reserved here be filled by a materialisation that has not started
        // yet and read by one that started mid-parse: the index and the table
        // travel together because there is only one table.
        //
        // The placeholder is `py.None()` rather than a Rust `None` now that a
        // `Python<'_>` is what this holds: "reserved but not filled" is the
        // state the materialiser reads as "nothing is here yet, so build the
        // node", and a Rust `None` in the `Vec` used to be translated to exactly
        // this on the way out. `Python::attach` is available because storing the
        // list is what removed the need for a `Python<'_>` at the call site.
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
            // Reachable, and reachable *only* through a caller that forgot: a
            // template with no `{% if %}` in it never gets here, which is why
            // `_native.parse(source)` without a compiler works for every other
            // tag. So the message names the argument rather than the template —
            // this is an integration bug in the caller, and a `TemplateSyntaxError`
            // shaped like a template error would send the reader to the wrong file.
            //
            // `Internal` rather than `Host`, because nothing raised it: `Host` is
            // this project's spelling for "a Python exception, flattened", and
            // there is no Python exception here.
            return Err(Error::internal(
                "this parse reached an {% if %} condition with no compile_filter \
                 callable; pass the shell's `compile_filter(parser)` to `_native.parse`",
            ));
        };
        // `TemplateIfParser.create_var`'s line: build the operand now, at this
        // point in the token walk, so a bad filter raises before the operator
        // algorithm runs and carries Django's own message (`Host::compile_filter`
        // in `template-core` has the measurements).
        match compile.call1((token,)) {
            Ok(expression) => {
                // Stored in the same table `alloc_slot` reserves into, because
                // the descriptor names it by a `NodeRef` and the materialiser
                // resolves that through the same two callbacks `{% resetcycle %}`
                // uses. Reserved-and-filled in one step here, which is why no
                // `None` placeholder is ever visible for this producer.
                Ok(self.fill_slot(expression))
            }
            // The callable raises Django's own `TemplateSyntaxError`, from
            // `find_filter` or from `FilterExpression.__init__`'s variable
            // parsing — both must read as themselves, which is why the message is
            // flattened rather than replaced.
            Err(error) => Err(Error::host(python_error_message(compile.py(), &error))),
        }
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
        // A `{{ … }}` body is not a `{% if %}` condition, so no token here can
        // reach `Host::compile_filter`; `None` is the honest spelling of "this
        // entry point has no such seam".
        compile_filter: None,
        // This entry point's own table, created here and handed out with the
        // descriptor below. It is one object and not a copy of one, for the
        // field's reason — `filter_expr_desc_to_py` carries it as
        // `FilterExpr::refs` and `_materialise_filter_expression` subscript it.
        refs: PyList::empty(py).into_any().unbind(),
        // A `{{ … }}` body has no block tags in it, so `has_tag` is never
        // consulted and there is nothing for a tag seam to do.
        host: None,
    };
    match template_core::parser::filter_expr::parse_filter_expression(token, &host) {
        // The table goes out **with** the descriptor, because this entry point's
        // handles index into it and nothing else will ever see it: returning the
        // descriptor alone would leave every handle pointing into a dropped
        // list. See `FilterExpr::refs`.
        Ok(desc) => filter_expr_desc_to_py(py, &desc, Some(host.ref_list(py)?)),
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
    origin_template_name: Option<String>,
    host: &dyn Host,
) -> PyResult<Py<PyAny>> {
    let tokens = if debug {
        CoreLexer::debug(source).tokenize()
    } else {
        CoreLexer::new(source).tokenize()
    };

    let mut parser = CoreParser::new(tokens, host);
    if let Some(name) = origin_template_name {
        parser = parser.with_origin_template_name(name);
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
/// `origin_template_name` is `Origin.template_name`, or `None` when the origin
/// has none — the **name**, not a rendered `repr`: `{% extends %}` and
/// `{% include %}` read it directly, and [`Parser::extend_nodelist`] derives the
/// `repr` its must-be-first message needs (M7 Task 2, A3).
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
///
/// `compile_filter` is `{% if %}`'s half of the same discharge: the operand of a
/// condition is a **host-built `FilterExpression`**, not a wire descriptor,
/// because `TemplateLiteral.value` has to answer
/// `value.resolve(context, ignore_failures=True)` (`defaulttags.py:917-918`).
/// Unlike `check_filter` there is no permissive default — the object has to
/// exist — so a source containing `{% if %}` parsed without one fails at the
/// condition with `ErrorKind::Internal` naming this argument. Callers with a
/// `Parser` in hand pass `materialiser.compile_filter(parser)`; `render` builds
/// its own, so the shell's end-to-end path cannot forget it.
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

/// Which of Django's three `except` clauses a failed arm is being judged by, so
/// each swallowed set is read off `base.py` in one place instead of three.
enum Arm {
    /// `base.py:968` — `TypeError, AttributeError, KeyError, ValueError,
    /// IndexError`.
    Item,
    /// `base.py:977` — `TypeError, AttributeError`. This is the arm whose
    /// swallowed exception can still be re-raised, because Django's `dir()` line
    /// (`base.py:979`) sits inside this `except`.
    Attr,
    /// `base.py:983-988` — `IndexError, ValueError, KeyError, TypeError`.
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
/// (`pyo3-0.29.3/src/marker.rs`; that crate has no `Python::with_gil`), and
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
    /// The context this lookup runs against, shared with every target the loop
    /// produces. `string_if_invalid` is read from a target the loop has already
    /// walked into, so the root's copy is not enough.
    ///
    /// `Rc<Py<PyAny>>` rather than a cache filled on first use: Django re-reads
    /// the attribute at **every** substitution, and a cache would be an
    /// optimisation this port has to justify rather than a faithful default.
    /// Nothing measured needs one, and YAGNI is the rule that says so.
    context: Rc<Py<PyAny>>,
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
            arena: Rc::clone(arena),
            pending: Rc::clone(pending),
            context: Rc::clone(context),
        }
    }

    /// A target over `value`, sharing this call's arena, `pending` and context.
    ///
    /// A method rather than an inline `Self::new` because `call` and
    /// `string_if_invalid` both produce a fresh value mid-loop, and sharing the
    /// four `Rc`s is the whole contract.
    fn target_for(&self, value: Py<PyAny>) -> Box<dyn LookupTarget> {
        Box::new(Self::new(value, &self.arena, &self.pending, &self.context))
    }

    /// Classify one arm's result: a swallowed exception is a miss the next arm
    /// covers, anything else is reported as `Raised`. Both are stashed; see
    /// `pending`.
    ///
    /// Written so the token never leaves its closure — a `Python<'_>` cannot be
    /// returned out of `Python::attach`, so the classification happens inside.
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
    /// `alters_data` (`base.py:993-996`).
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

    /// `current()` with Django's `signature()`/`bind()` fallback
    /// (`base.py:1000-1013`).
    ///
    /// The three `CallFailure` outcomes are Django's three, and the distinction
    /// between the last two is the one worth keeping: a `TypeError` from
    /// `signature`/`bind` means arguments were required, while a `TypeError`
    /// from the call itself — with both checks succeeding — is the bare `raise`
    /// at `base.py:1013`, which leaves this arm and reaches the outer handler at
    /// `base.py:1014`. Measured: a zero-arg callable whose body raises `TypeError`
    /// propagates rather than substituting, and the original error must be the
    /// one stashed, not the `bind` error, or the message is Django's-never.
    fn call(&self) -> Result<Box<dyn LookupTarget>, CallFailure> {
        Python::attach(|py| {
            let value = self.value.bind(py);
            match value.call0() {
                Ok(called) => Ok(self.target_for(called.unbind())),
                Err(err) if err.is_instance_of::<PyTypeError>(py) => {
                    // Django's fallback: signature() then bind() (`base.py:1002-1013`).
                    //
                    // **`django.utils.inspect.signature`, not `inspect.signature`**
                    // — and the difference is measured, not stylistic. Django
                    // imports its own wrapper (`base.py:63`), which on Python
                    // 3.14+ passes `annotation_format=Format.FORWARDREF` so a
                    // *deferred* annotation is never evaluated. The bare
                    // `inspect.signature` evaluates them and raises whatever the
                    // annotation evaluates to: measured, `{{ obj.method }}` on a
                    // callable annotated `value: Undefined` raises `NameError`
                    // through this path and renders `''` through Django's.
                    // `django/utils/inspect.py:126-134` is the wrapper;
                    // `tests/template_tests/tests.py:231-243` is the row.
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
                        Ok(sig) => match sig.call_method0("bind") {
                            Err(e) if e.is_instance_of::<PyTypeError>(py) => {
                                Err(CallFailure::ArgsRequired)
                            }
                            Err(error) => {
                                self.pending.set(Some(error));
                                Err(CallFailure::Raised)
                            }
                            // Neither check failed, so the `TypeError` came from
                            // the body: Django re-raises the **original** error,
                            // so the host must stash `err`, not the bind error.
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
            // The read happens here, at the substitution site, and not in
            // `PyTarget::new` — Django re-reads the attribute at each of its
            // three assignments, and a plain value on an unbound context must
            // never reach it (measured; `m8_measure_invalid.py` §2).
            match string_if_invalid(self.context.bind(py)) {
                Ok(value) => Ok(self.target_for(value)),
                // `PendingFailure` means "the host holds the exception", so it
                // must actually be holding it; `outcome_to_py` reads `pending`.
                Err(error) => {
                    self.pending.set(Some(error));
                    Err(PendingFailure)
                }
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
/// `alters_data` and a silent variable failure (`base.py:1023-1024`).
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

/// The logger Django's outer handler writes to.
///
/// It is `logging.getLogger("django.template")`, reached by that call rather
/// than through `django.template.base.logger`: the two are the same object
/// upstream (`base.py:94`) and the call is what `assertLogs("django.template")`
/// itself does, so a row that captures the record finds the logger the handler
/// wrote to whatever the shim did to the module. It also removes a dependency
/// on our transcription re-exporting the name.
///
/// Cached, and safely: `assertLogs` replaces the *handlers* on the logger the
/// call returns, not the logger object, so a cached reference still sees the
/// capture.
static TEMPLATE_LOGGER: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The three lines of Django's outer `except` that are not a decision about the
/// value: read `template_name`, log, and let the caller decide.
///
/// This is `base.py:1015-1020` — `template_name`, then the `logger.debug`
/// call with its message left as a `%`-template — so
/// `record.args` carries the bit and the template name and `getMessage()`
/// formats it — the two attributes `test_logging` reads.
fn log_resolution_failure(
    py: Python<'_>,
    context: &Bound<'_, PyAny>,
    bit: &str,
    error: &PyErr,
) -> PyResult<()> {
    // `getattr(context, "template_name", None) or "unknown"` (`base.py:1015`).
    // A `getattr` with a default swallows exactly `AttributeError` and nothing
    // else, so a `template_name` property that raises `RuntimeError` propagates
    // from here — out of the *handler*, replacing the exception it was about to
    // report. That is what upstream's identical line does, one frame out.
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

    // The record is built from the exception **instance**, not from
    // `exc_info=True`. `logging` accepts either — `Logger._log` converts an
    // instance to `(type(e), e, e.__traceback__)` and calls `sys.exc_info()`
    // only for a truthy non-tuple — and the instance is the only one of the two
    // this frame has: the failure was stashed by an arm and handed back, never
    // raised, so Python's exception state here is whatever the caller's was.
    //
    // Passing `True` would therefore have produced `(None, None, None)` — a
    // record whose `exc_info` is not `None`, so `assertIsNotNone` passes and the
    // `str(log_record.exc_info[1])` two lines below it fails with `'None'`.
    // Measured on the pinned tree before this was written.
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

/// Turn the port's verdict into Python's: hand back the value behind the
/// handle, raise Django's `VariableDoesNotExist`, or apply the host half of
/// Django's outer `except Exception` to the exception it stashed.
///
/// `context` is needed by the [`LookupOutcome::Escaped`] arm, which is the one
/// place Django's handler reaches for `context.template.engine`, and by both
/// arms that log, which read `context.template_name`.
///
/// # The log lives here, and that is a deviation from the M9 plan
///
/// Task 5 of `docs/superpowers/plans/2026-10-09-m9-interpose-base-and-smartif.md`
/// puts the log in Python, in the shell's `Variable._resolve_lookup`, with the
/// `bit` carried across by [`LookupOutcome::Escaped`]'s new payload.
///
/// **The first version of this paragraph said it *cannot* go there, and that
/// was false.** The reviewer falsified it, and the falsification is worth
/// keeping because the mistake was choosing a premise and then arguing from it:
/// a Python handler is available the moment the function stops being obliged to
/// raise. Return the outcome instead — `(value, bit, error)` — and the shell can
/// log `exc_info=error`, an *instance*, which `logging` accepts and which is
/// precisely why `record.exc_info[1]` comes back as the caller's object by
/// identity; then substitute or `raise error`. No mutated exception, no
/// out-parameter, no thread-local, and no live `except` block anywhere. The
/// "Rust must raise" of that paragraph is a property of
/// `fn resolve(…) -> PyResult<Py<PyAny>>`, which has one channel — the
/// signature was chosen, the constraint was not found.
///
/// It stays here for a different reason, and the reason is what `_native.resolve`
/// *is*: the Rust mirror of `_resolve_lookup`, built so that
/// `tests/differential/cases.py`, `tests/test_render_parity.py`,
/// `tests/test_resolve_invalid.py`, `tests/test_resolve_callarm.py` and
/// `tests/test_m8_side_effects.py` can compare a lookup against Django's **at
/// the method**, with the arm that failed named in the report. Those five
/// harnesses exercise `silent_variable_failure` — `test_render_parity.py:533`
/// and `test_resolve_callarm.py:86` are named for it — so an `_native.resolve`
/// that returned the raw failure would diverge from the pinned method on exactly
/// the rows they were built for.
///
/// **A second version of this paragraph added that moving the handler to Python
/// would be refused by `tests/parity_target.py:62`, and that was false too.**
/// That guard's subject is the *target* — its own docstring at that line
/// describes `target` as a class or function whose source file is the
/// comparison's Python side, Django's — and routing the five harnesses through
/// the shell's `_resolve_lookup` changes **our** side, not the target, which
/// stays the ast-extraction out of the pinned checkout
/// (`tests/parity_target.py:170`). That is the whole job of
/// `pinned_resolve_lookup()`, so the guard would be satisfied and unmoved. The
/// sentence conflated the side under test with the oracle.
///
/// So the trade is **one comparison point against two**, not a complete mirror
/// against a verbatim handler. Comparing at `Variable._resolve_lookup` rather
/// than at `_native.resolve` costs the arm-named report, and that is
/// recoverable rather than lost: an arm-level row calling `_native.resolve`
/// directly still gets the arm name with no handler living in Rust, because it
/// needs the *outcome*, which a `(value, bit, error)` return carries more
/// explicitly than an applied handler does, and a behaviour row at the method
/// covers the `silent_variable_failure` branch. Two granularities, which is what
/// every other seam in this project already has.
///
/// The reason this file keeps the handler is therefore about **when**, not
/// **whether**: the five harnesses' teeth were established by mutation, and
/// reworking them inside a commit whose job is to correct a false reason is the
/// "redesign bundled into a fix" shape that has made a premise false twice
/// already — the new teeth would be asserted rather than measured, in a commit
/// nobody is reading for that. Task 7 owns the design question, and now owns it
/// with the corrected cost.
///
/// What this choice costs today is written down rather than hidden: the shell's
/// `_resolve_lookup` is no longer upstream's text, and — the sharper half — the
/// *handler* moved while the transcription diff shows only a hunk of the loop.
/// The declared region in `scripts/check_transcriptions.py` says so in prose,
/// and prose is the only place that can: a body replaced by a call is one hunk
/// however much body went with it. That is a limit of a line-diff guard rather
/// than a defect in this declaration — seeing a moved handler would take
/// comparing semantics, which is not obviously achievable. What *is* achievable
/// is the asymmetry: a region where upstream has forty lines and ours has two is
/// a relocation **by construction**, and the guard already holds both spans.
/// Reporting that ratio, and requiring a declared note above some threshold, is
/// mechanical, and it would have flagged this hunk. Task 7 inherits that.
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
        //
        // **This arm logs too, and that was the second of two defects the recon
        // collapsed into one.** Django's `raise VariableDoesNotExist(...)`
        // (`base.py:989`) is *inside* the outer `try`, so a missing key reaches
        // the handler at `base.py:1014` and logs before it raises. A port that
        // treated "missing" as "already decided, nothing to do" produces the
        // right exception, the right message, and *no log record* —
        // `test_log_on_variable_does_not_exist_silent` reports "no logs of level
        // DEBUG or higher triggered on django.template", which is what a
        // silently-dropped log line looks like from the outside.
        LookupOutcome::Missing { bit, container } => {
            let error = variable_does_not_exist(py, &bit, value_behind(container)?)?;
            log_resolution_failure(py, context, &bit, &error)?;
            Err(error)
        }
        // `pending` is the reason `LookupOutcome` does not carry the exception:
        // the enum derives `PartialEq` and is compared in `template-core`'s
        // tests, where no Python exists. The bit *is* carried — see
        // [`LookupOutcome::Escaped`] — because the handler logs it and nothing
        // below Rust can recover it.
        //
        // Both branches of Django's handler are here, in Django's order: log,
        // then substitute for a `silent_variable_failure`, else re-raise. The
        // substitution is the one that was missing until M8; the log is the one
        // M9's recon mistook for the only failure.
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

/// The Python object `Variable.literal` holds, for a descriptor that is a
/// literal rather than a path.
///
/// Everything but the quoted string is `attr_value_to_py`'s answer unchanged:
/// `Int`/`BigInt` are Python `int`s and `Float` is a Python `float`, which is
/// what `Variable.__init__` stores.
///
/// `SafeStr` is the exception, and the difference is a type that rendering
/// reads: Django stores `mark_safe(unescape_string_literal(var))`
/// (`base.py:906`), a `SafeString`, and returns it from `resolve` unchanged — so
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
/// `silent_variable_failure` (`base.py:1023-1024`). It does **not** do `resolve`'s
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

/// Parse, materialise, and build the `Template` — M3's end-to-end entry.
///
/// **This returns the `Template` rather than rendering it, and that is M6 Task
/// 4's change.** Django's `Template.__init__` sets `self.extra_data =
/// parser.extra_data` inside `compile_nodelist`'s `try`, right after
/// `parser.parse()` (`base.py:198-200`), and a shell that builds the `Template`
/// by `__new__` skips that line. Handing the object back lets
/// `_impl/parser.py::template_for` own the whole attribute list in one place —
/// which is what its own docstring has always claimed — instead of a second
/// source-to-`Template` path existing for the shell to keep in step. The caller
/// renders it, which is also what `Engine.from_string` needs at M9.
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
/// `string_if_invalid` (`base.py:793`, `base.py:997`, `base.py:1005`,
/// `base.py:1011`, `base.py:1024`) and for `engine.debug` (`base.py:1054`).
/// With `context.template` left `None`, `{{ nope }}` raises
/// `AttributeError: 'NoneType' object has no attribute 'engine'` — measured, on
/// this very call. So the shell builds a real `Template` around the tree
/// (`__new__` plus assignment, because the constructor would parse the source a
/// second time in Python) and `Template.render` binds it. See `template_for` in
/// `_impl/parser.py`, which owns the attribute list.
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
/// Nothing is caught. A `TemplateSyntaxError` from the parse propagates as
/// itself, and the caller's `Template.render` propagates the render's own
/// exceptions the same way, because `tests/test_render_parity.py` compares
/// exception *types and messages* as well as output.
///
/// `origin` is Django's `Origin` for this template, and it reaches **two**
/// readers, which is why it is one object rather than two values (A3):
///
/// - the parse, as `origin.template_name` — the *attribute*, never the object,
///   which is what `{% extends %}` compares against a path;
/// - every materialised node's `node.origin`, and the `Template.origin` of the
///   tree `template_for` builds, both of which Django's own
///   `Parser.extend_nodelist` (`base.py:616`) and `Template.__init__`
///   (`base.py:154`) set to the parser's origin.
///
/// They have to be the *same* object: `Node.render_annotated` decides whether to
/// attach the debug traceback by comparing `context.render_context.template.origin
/// == e._culprit_node.origin` (`base.py:1058-1062`), so the node's origin and the
/// template's must be equal for `engine.debug` templates to keep annotating.
/// Before M7 Task 2 both were `None`, which was equal; threading one and not the
/// other would be the silent half-fix.
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
    // `{% if %}`'s other half, from the same module and for the same reason:
    // `TemplateIfParser.create_var` builds each operand during the parse, so the
    // callable has to be here rather than in the shell. Built from the *same*
    // parser, so the filters a condition resolves are the caller's engine's.
    let compile = tree.getattr("compile_filter")?.call1((parser.bind(py),))?;
    let host = FilterCheckHost {
        check_filter: Some(check),
        compile_filter: Some(compile),
        refs: PyList::empty(py).into_any().unbind(),
        host,
    };

    // The attribute, never the object (A3): `{% extends %}` compares this
    // against a path. `None` is Django's `Origin(UNKNOWN_SOURCE).template_name`
    // and is not special-cased here — `Parser::extend_nodelist` is where
    // Django's truthiness test lives.
    let origin_name: Option<String> = match &origin {
        Some(origin) => origin.getattr("template_name")?.extract()?,
        None => None,
    };
    // `self.engine.debug`, which upstream reads at the top of `compile_nodelist`
    // to choose `DebugLexer` over `Lexer` (`base.py:183-186`). It has to be read
    // here rather than defaulted, because the *positions* it turns on are what
    // every downstream debug path needs and none of them can recover them:
    // measured, with this hardcoded `false`, `Engine(debug=True).from_string(
    // "{% badtag %}")` reaches `Template.get_exception_info`, which does
    // `start, end = token.position`, and dies with
    // `TypeError: cannot unpack non-iterable NoneType object`.
    let debug: bool = engine.bind(py).getattr("debug")?.extract()?;
    let desc = parse_source(py, source, debug, origin_name, &host)?;
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
    // Bound once, because it is handed to two calls below: `materialise` puts it
    // on every node, and `template_for` puts it on the `Template`. **They must
    // be the same object**, which is what the paragraph above says and what this
    // line exists to make true — the annotation comparison in
    // `Node.render_annotated` is between the two.
    let origin = origin.unwrap_or_else(|| py.None().into_bound(py));
    let nodelist = tree.getattr("materialise")?.call1((
        desc,
        &origin,
        resolve_ref,
        register_ref,
        parser.bind(py),
    ))?;
    // `parser` is the third argument because `template_for` reads
    // `parser.extra_data` there (`base.py:198-200`). Passing the parser rather
    // than the dict keeps the two in step: there is no way to hand over one
    // parse's state with another parse's tree.
    //
    // `source` is the fifth, and it is the render-annotation protocol's other
    // half (`Template.get_exception_info` slices it). **This call used to pass
    // neither `origin` nor `source`**, which meant the `Template` this builds
    // carried `origin=None` and `source=""` while every node carried the real
    // origin — so the equality that decides whether to annotate never held, and
    // `parse_template`/`render_string` produced no `template_debug` at all where
    // Django produces one. Invisible to the Django suite: `Template.__init__`
    // builds the object that renders and this one is discarded by
    // `compile_nodelist`. Measured, both halves.
    let template = tree.getattr("template_for")?.call1((
        nodelist,
        engine.bind(py),
        parser.bind(py),
        &origin,
        source,
    ))?;
    Ok(template.unbind())
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
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
    m.add_function(wrap_pyfunction!(lex, m)?)?;
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(parse_variable, m)?)?;
    m.add_function(wrap_pyfunction!(parse_filter_expression, m)?)?;
    m.add_function(wrap_pyfunction!(resolve, m)?)?;
    m.add_function(wrap_pyfunction!(compile_template, m)?)?;
    m.add_class::<NodeRef>()?;
    m.add_class::<NodeListRef>()?;
    m.add_class::<NodeSlot>()?;
    m.add_class::<Tokens>()?;
    m.add_class::<IfCondition>()?;
    m.add_class::<SafeStr>()?;
    m.add_class::<Variable>()?;
    m.add_class::<FilterExpr>()?;
    m.add("ParseError", m.py().get_type::<ParseError>())?;
    m.add("UnimplementedTag", m.py().get_type::<UnimplementedTag>())?;
    Ok(())
}
