//! PyO3 bindings exposing `template-core` as the `_native` extension module.
//!
//! This is the only crate permitted to depend on Python. The seam traits
//! defined in `template-core` are implemented here over `PyObject`.

use pyo3::create_exception;
use pyo3::exceptions::{
    PyAttributeError, PyException, PyIndexError, PyKeyError, PyNotImplementedError, PyRuntimeError,
    PyStopIteration, PyTypeError, PyUnicodeDecodeError, PyValueError, PyZeroDivisionError,
};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyList, PyString, PyTuple, PyType};
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
    VariableDesc, HOST_NODE, TEXT_NODE,
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
/// **`_native.CANNOT_HOLD` is the one return that is not an answer.** A Python
/// `str` may hold a lone surrogate, which a Rust `&str` cannot, so there are
/// values this seam cannot hold. Where Django's answer for such a value would be
/// an exception of our own invention — `{{ x|upper }}` with `x = '\ud800'` is
/// `'\ud800'` upstream and was `UnicodeEncodeError` here — the seam returns the
/// [`CannotHold`] singleton instead, and the shell reads that as "run Django's
/// own callable". It is the same refusal `_native.html_escape` sends with its
/// `None`, and it is deliberately not an exception: an exception would turn a
/// value Django renders into a 500. Measured over the seam: all 57 built-in
/// names refuse on `'\ud800'`, where Django's own body answers 33 of the 37 that
/// take only a value (`.review/m19-measurements/surrogate_boundary.txt`).
///
/// The sentinel is an object rather than `None` because `None` **is** an answer
/// a filter may give — see [`CannotHold`] — and a caller that read it as the
/// refusal would run the filter a second time.
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
    filters::run_filter(py, name, value, args, autoescape, use_tz, false)
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

/// `django.utils.html.escape` for an exact `str` — the table, and nothing else.
///
/// The caller is `engine/base.py::render_value_in_context`'s fast path: the
/// autoescape branch for a value whose **exact** type is `str`, where `localize`
/// short-circuits on `str` before reading anything, `template_localtime` has
/// nothing to convert, and `conditional_escape` finds no `__html__` and reaches
/// this table wrapped in `SafeString`. **The `SafeString` wrapper is the shell's**
/// — this crate does not name Django classes.
///
/// **`None` means "refuse", and it is a correctness contract rather than a
/// convenience.** PyO3 extracts a Python `str` as `&str` only when it is
/// UTF-8-encodable, which a string holding a lone surrogate is not — and
/// `'\ud800'` is a value stock Django renders, measured at 6.0.9 (`{{ x }}` and
/// `{{ x|force_escape }}` both render `'\ud800'`). A `&str` signature would turn
/// a value that renders today into a raise, so the refusal sends the caller down
/// the slow path, which is correct for that input.
///
/// That divergence is **already present for every builtin filter** — measured,
/// `{{ x|upper }}`, `{{ x|length }}` and `{{ x|escape }}` all raise
/// `UnicodeEncodeError` where stock renders — and it is *not* fixed here: this
/// fast path must not add the plain-variable path to that set. The filter
/// layer's is its own defect and its own change.
#[pyfunction]
fn html_escape(text: &Bound<'_, PyString>) -> PyResult<Option<String>> {
    match text.to_str() {
        Ok(s) => Ok(Some(template_core::filters::html_escape(s))),
        Err(_) => Ok(None),
    }
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
///
/// **A singleton, identity-compared, because `None` cannot carry this message.**
/// A filter may legitimately *answer* `None` — `default` returns its argument,
/// and `{{ x|default:None }}` with a falsy `x` is `None` — so a `None` return
/// would mean both "here is your answer" and "I have none", and the shell would
/// run Django's own body a second time over the first, on an input nobody would
/// think to test. Measured at the seam, both spellings of "no value" arrive as
/// Python `None`: `_native.filter("default", None, (None,), True, True)` is a
/// *result* and `_native.filter("upper", "\ud800", (), True, True)` was the
/// refusal, indistinguishable.
///
/// **Not an exception, and the first reason written for that was false.** The
/// draft said a control-flow exception would be eaten by the render path's broad
/// handlers — `Variable._resolve_lookup`'s `except Exception` is Django's own —
/// and measured at 6.0.9 that is wrong: `VariableNode.render` catches
/// `UnicodeDecodeError` and nothing else, and a filter body's exception reaches
/// the caller from all six render shapes tried, with `debug` on and off
/// (`.review/m19-measurements/surrogate_boundary.txt` §8). The reasons that hold
/// are that a refusal is **not an error** — Django renders this value — and that
/// the sibling refusal one function away, `_native.html_escape`, already
/// declines by *returning*. Two seams, one protocol; and a returned object
/// cannot be caught by a handler that has never heard of it.
#[pyclass(frozen, module = "django_rust_templates._native")]
pub struct CannotHold;

#[pymethods]
impl CannotHold {
    fn __repr__(&self) -> &'static str {
        "<the filter seam cannot hold this value>"
    }
}

/// The one [`CannotHold`] instance, shared by the module attribute and every
/// refusal `run_filter` reports.
///
/// One `PyOnceLock` behind both, because the shell's test is `is not` and two
/// instances would make it false for the refusal it is there to catch.
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

/// Parse `source` into the Rust node descriptor tree, with `host` at the seam.
///
/// The lex-and-parse half, returning `NodeListDesc` — the type `template-core`
/// owns — rather than a Python object. Shared by `parse_source` (which converts
/// it to the wire tuple) and `compile_template` (which keeps it in Rust for the
/// render walk), so a second copy cannot be the place a later caller's host
/// silently went missing.
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

    /// `BaseContext.__getitem__` (`context.py:88-91`) — `for d in reversed(self.dicts):`,
    /// then `if key in d: return d[key]`, else `raise KeyError(key)`. The upward
    /// search, transcribed for the root target only.
    ///
    /// `Ok(None)` means *"use the real thing"*, and that is the whole safety
    /// argument: every path that is not certain is the slow path, and the slow
    /// path is Django's own code, run by the caller. The transcription below is
    /// reached only when all of
    ///
    /// 1. this target **is** the context object (`is`: the root of a `resolve` is
    ///    the object the walk was handed, so this is exactly "we are at the root"),
    /// 2. `type(context).__getitem__` **is** `BASE_CONTEXT_GETITEM`, read here on
    ///    every use and never cached — `Context.__getitem__` can be rebound while
    ///    a body runs, and `engine/base.py` binds that name at import time and
    ///    argues why that is the only moment it can be trusted,
    /// 3. `context.dicts` is **exactly** a `list` — `PyList_CheckExact`, because
    ///    `reversed()` dispatches to `list.__reversed__` and a subclass may
    ///    override it, and
    /// 4. every layer in it is a `dict` or a `dict` subclass.
    ///
    /// Condition 4 is about `ContextDict` — `class ContextDict(dict):`
    /// (`context.py:14`) — the subclass `push()` appends for `{% with %}` and
    /// `{% include %}`: a subclass is still Django's `d[key]`, so it is admitted. A
    /// layer that is not a dict at all is not, and that is decided **before** the
    /// walk probes anything, so a bail does not run a layer's `__contains__` twice.
    ///
    /// A truncated `dicts` is **not** a bail. Django's `for` is driven by a
    /// `list_reverseiterator`, which reads the list's length on every step, so a
    /// layer whose `__contains__` shortens the list ends the loop and the
    /// `raise KeyError(key)` below it runs; the transcription does the same
    /// (`break`, then the same `KeyError`). Treating it as "not certain" and
    /// falling back re-walks a stack that has already been partly consumed, and
    /// can answer a key Django never sees.
    ///
    /// **Membership before subscription**, and both through the *object protocol*
    /// rather than the raw `PyDict` calls — because for a `dict` subclass the two
    /// readings can disagree, and the way to see which is to try to build the
    /// disagreement.
    ///
    /// A subclass defining `__missing__` and nothing else does **not** produce one:
    /// `if key in d:` is tested before `return d[key]` (`context.py:89-90`), so a miss
    /// is never subscribed to, and `__missing__` is only reached from a subscription.
    /// Measured, on such a subclass: both this walk and Django raise `KeyError`. What
    /// does produce one is a subclass that also overrides `__contains__` to claim a key
    /// it does not hold — membership then passes, the subscription misses, and `d[key]`
    /// reaches `__missing__` through CPython's `dict_subscript` (`dict.__getitem__` *is*
    /// that function) while `PyDict_GetItemWithError` would not. Measured, on
    /// `class C(dict): __contains__ -> True, __missing__ -> "MISSING-" + key`: Django
    /// answers `'MISSING-x'`, and so must this.
    ///
    /// `PyObject_Contains`/`PyObject_GetItem` are what `in` and `[]` compile to, so
    /// routing both operations through them leaves that distinction nowhere to
    /// arise. `key` is always a `str`, because `LookupTarget::get_item` takes `&str`.
    fn root_item(&self, py: Python<'_>, bit: &str) -> PyResult<Option<Py<PyAny>>> {
        let value = self.value.bind(py);
        if !value.is(self.context.bind(py)) {
            return Ok(None);
        }
        let Ok(current) = value.get_type().getattr(intern!(py, "__getitem__")) else {
            return Ok(None);
        };
        if !current.is(base_context_getitem(py)?) {
            return Ok(None);
        }
        let Ok(dicts) = value.getattr(intern!(py, "dicts")) else {
            return Ok(None);
        };
        // **Exactly** a `list`, not a subclass of one, because `reversed(self.dicts)`
        // is `list.__reversed__` and a subclass may override it: measured, a
        // `class Rev(list)` returning `iter([{"x": "FROM_REVERSED"}])` from
        // `__reversed__` makes Django answer `FROM_REVERSED` where positional
        // indexing from the list's own length answers `FROM_SECOND`. A subclass
        // overriding only `__len__` or `__getitem__` does *not* diverge — the
        // iterator reads the list through `PyList_GET_SIZE`/`PyList_GET_ITEM`, and
        // so does `PyList::len`/`get_item` — but the exact check costs nothing
        // (`push`, `update` and `_reset_dicts` only ever build a plain list) and it
        // makes the condition true as written rather than nearly true.
        if !dicts.is_exact_instance_of::<PyList>() {
            return Ok(None);
        }
        let Ok(dicts) = dicts.cast::<PyList>() else {
            return Ok(None);
        };
        let key = PyString::new(py, bit);
        // Every layer must be a `dict` **before** any of them is probed. A layer
        // that is not one is a bail, and a bail runs Django's own method over the
        // whole stack — so probing first would run the layers *above* the non-dict
        // one twice, and `__contains__` is a user method whose calls are
        // observable. Measured, with a logging `__contains__` on the top layer and
        // a non-dict below it: Django logs one call, probing-then-bailing logs two.
        for layer in dicts.iter() {
            if !layer.is_instance_of::<PyDict>() {
                return Ok(None);
            }
        }
        // `reversed(self.dicts)` is a `list_reverseiterator`, and that object
        // re-reads the list's length on every step: a layer whose `__contains__`
        // shortens the list ends the loop early. The length is therefore read here
        // per step too, and a shortened list **ends the search** — `break`, not a
        // bail: the iterator's stop is a certain answer, and Django's `for` falling
        // through to `raise KeyError(key)` is what that answer is. Bailing here
        // instead re-walked a stack the transcription had already partly consumed:
        // measured, an already-examined layer gaining the key while the stack
        // truncates makes Django answer `''` where a bail answers `HIT`.
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
        let result = Python::attach(|py| -> PyResult<Py<PyAny>> {
            match self.root_item(py, bit)? {
                Some(found) => Ok(found),
                None => self
                    .value
                    .bind(py)
                    .get_item(bit)
                    .map(|found| found.unbind()),
            }
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
    BASE_CONTEXT.import(py, "django_rust_templates.engine.base", "BaseContext")
}

/// `BaseContext.__getitem__`, as `engine/base.py` bound it **during its own
/// import**.
///
/// **Read from that module rather than captured from the class here, and the
/// difference is the whole of this guard's soundness.** `PyTarget::root_item`
/// licences its Rust transcription of the upward search by comparing against this
/// object, so it has to be the function as it stood *before any code outside the
/// package could rebind it*. Both spellings resolve once per process, and the one
/// this is not — `type(context).__getitem__` taken on first use — can be taken
/// *after* a project rebinds the attribute, and would then license the transcribed
/// body against a class that no longer runs it. What is cached here is a name that
/// was already correct when it was bound, and the region in `engine/base.py` argues
/// why no project can reach the class before that binding happens — the argument
/// rests on `django_rust_templates/engine/__init__.py`'s *first* import
/// (`from .engine import Engine`, line 26) reaching `engine.py:7`'s
/// `from .base import Template`, so this module's body runs before any route to
/// `BaseContext` can complete. That ordering, not `base.py`'s own import of
/// `context`, is the invariant; `test_loop_read_guard.py` pins it.
/// `tests/test_loop_read_guard.py` is the control: it moves the class attribute and
/// asserts that the walk stops taking this path.
static BASE_CONTEXT_GETITEM: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn base_context_getitem(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    BASE_CONTEXT_GETITEM.import(
        py,
        "django_rust_templates.engine.base",
        "BASE_CONTEXT_GETITEM",
    )
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
/// The class comes from `django_rust_templates.engine.base` through a
/// `GILOnceCell` rather than from a Rust type, for the same reason
/// `base_context` does: the class is Python's, and raising a *different*
/// exception class would make a differential comparison fail on the class
/// rather than on the message.
fn variable_does_not_exist(py: Python<'_>, bit: &str, container: Py<PyAny>) -> PyResult<PyErr> {
    let class = VARIABLE_DOES_NOT_EXIST.import(
        py,
        "django_rust_templates.engine.base",
        "VariableDoesNotExist",
    )?;
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
/// harnesses exercise `silent_variable_failure` — `test_render_parity.py:615`
/// and `test_resolve_callarm.py:86` are named for it — so an `_native.resolve`
/// that returned the raw failure would diverge from the pinned method on exactly
/// the rows they were built for.
///
/// **A second version of this paragraph added that moving the handler to Python
/// would be refused by `tests/parity_target.py:63`, and that was false too.**
/// That guard's subject is the *target* — its own docstring at that line
/// describes `target` as a class or function whose source file is the
/// comparison's Python side, Django's — and routing the five harnesses through
/// the shell's `_resolve_lookup` changes **our** side, not the target, which
/// stays the ast-extraction out of the pinned checkout
/// (`tests/parity_target.py:177`). That is the whole job of
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
/// * `lookups` → the port, with one arena and one `pending` shared across the
///   call. **This is the branch `Variable.resolve` reads first** — `base.py:926-928`
///   is `if self.lookups is not None` — so it is read first here too.
/// * `literal` → the literal, without touching the context. Taken only when
///   there are no lookups, which is nearly always: skipping it would make
///   `_native.resolve("5", {})` a lookup of the key `5`.
///
/// **The two are not exclusive, and the order between them is a render
/// divergence rather than a style.** The trailing-dot numerals assign the float
/// *and* take the string branch as well (`template-core/src/parser/variable.rs:
/// 66-73`), so `2.` carries `literal = 2.0` and `lookups = ("2", "")` at once —
/// exactly as Django's own `Variable("2.")` does. Reading the literal first
/// prints `2.0` where Django prints `string_if_invalid`. The shipped path used to
/// reach that state through the shell's `".".join(self.lookups)`, which re-parses
/// `"2."` as a number; **it no longer does** — the shell calls
/// [`resolve_lookups`] with the components, and this function is kept for the
/// harnesses that compare a *path* and therefore the classification too, which is
/// what `ResolverParityTests`' trailing-dot row is.
/// `.review/m19-measurements/lookup_roundtrip.txt`, sections 3 and 4.
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
            // `Variable.__init__` guarantees one of the two is set: every branch of
            // its `except ValueError` assigns a literal or the lookups. This is an
            // invariant of *our* parse, so it is a runtime error and not a template
            // error.
            None => Err(PyRuntimeError::new_err(
                "a parsed variable has neither a literal nor lookups",
            )),
        };
    };

    resolve_components(py, lookups, context)
}

/// `_resolve_lookup` with the path already **split**, which is the entry point the
/// shipped render path uses.
///
/// `crates/template-core/src/lookup.rs`'s `resolve_lookup` takes `&[String]`, and
/// `Variable._resolve_lookup`'s whole input is the component tuple
/// `Variable.__init__` produced — so this is the *closer* mirror of the method,
/// and [`resolve`] is this plus a classification. Two entry points rather than
/// one because the classification is a different thing to test:
/// `ResolverParityTests` compares `resolve`'s classify-then-resolve composition
/// against `pinned_resolve_lookup()(Variable(path), …)` and its
/// `test_a_trailing_dot_numeral_is_a_path_not_a_number` row is the one that found
/// the `2.` divergence, so the path form has to stay reachable.
///
/// **No `translate` arm**, and its absence is the point rather than an
/// oversight. `_("x y")` sets `translate` *and* `lookups` (`unescape_string_literal`
/// rejects the unquoted body, so `__init__` falls through to the string branch),
/// which is why [`resolve`] refuses it — but `Variable.resolve` applies the
/// translation to the *value* one frame out and after `_resolve_lookup` returns,
/// and the shell reaches here only from `if self.lookups is not None`. So a
/// translation is never this function's to apply, and refusing it here would
/// refuse a render Django performs.
///
/// Why it exists: until M19 this took a **path** and the shell re-joined the
/// components into one (`".".join(self.lookups)`), which is exact componentwise
/// and not faithful in *classification* — `"2."` re-parses as a number, and that
/// is `{{ 2. }}` rendering `2.0` where Django renders `string_if_invalid`. Passing
/// the components removes the input class rather than ordering the dispatch
/// around it. Measured: `.review/m19-measurements/production_render_path.txt` §3d.
#[pyfunction]
fn resolve_lookups(
    py: Python<'_>,
    lookups: Vec<String>,
    context: Py<PyAny>,
) -> PyResult<Py<PyAny>> {
    resolve_components(py, &lookups, context)
}

/// The tail [`resolve`] and [`resolve_lookups`] share: one arena, one `pending`,
/// one `PyTarget` root, one `outcome_to_py`.
///
/// Factored rather than duplicated because the two entry points differ **only**
/// in how the components were obtained, and a second copy of this would be a
/// second place for the `PyTarget` contract to drift out of step with
/// `LookupScope::resolve`, which is the third caller of the same shape.
fn resolve_components(
    py: Python<'_>,
    lookups: &[String],
    context: Py<PyAny>,
) -> PyResult<Py<PyAny>> {
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

// ---------------------------------------------------------------------------
// M19 step 2 — the native render walk.
// ---------------------------------------------------------------------------
//
// A plan is built once, at compile time, by walking the parser's descriptor
// tree in parallel with the `NodeList` `materialise` built from it, and it
// **carries the materialised node at every step**. That pairing is the design's
// load-bearing invariant, and it is asserted rather than assumed: a
// `class_name` that does not match the object at that index produces no plan at
// all, and the template renders on the Python path unchanged. A declined plan
// is therefore a set difference somebody can see (the census below), never a
// render that quietly differs.
//
// Four lessons, each with the measurement that justifies it:
//
// * **One sink.** `NodeList.render` builds a list and `"".join`s it, and so does
//   `ForNode.render` — a list, an append and a join per nodelist per render.
//   This walk owns one `String` and applies the `SafeString` wrapper **once**,
//   at the outer boundary, which is where Django applies it too
//   (`NodeList.render`, `engine/base.py:1199-1200`): the type is the same and the characters
//   are the same, so nothing between the two can observe the difference.
// * **A native loop frame.** `ForNode.render` writes a six-key `forloop` dict
//   and the loop variable into the context on **every** iteration —
//   `loop_bookkeeping.py` measures ~0.11 and ~0.06 µs per iteration for the two,
//   against a fitted slope of 0.222 for the row. A body this walk renders end to
//   end cannot observe either, so both are elided and the loop variable lives in
//   a frame target instead of in the context.
// * **Hoisted render-invariant work.** `autoescape` and the escape function are
//   read once per render rather than once per variable.
// * **The escape fast path inside the walk** — `render_value_in_context`'s own
//   first arm (`type(value) is str` under autoescaping) taken here, so an
//   ordinary `{{ name }}` pays no Python frame at all.
//
// The plan is **not** sold as the win: the M18.0 floor already measured that
// dispatch is not where the time is, and the minijinja study falsified the
// jump-table mechanism outright. The plan is what makes the removals above
// reachable and checkable — minijinja's own `pc`/`Jump` exists for an
// instruction-swap re-entry this design does not need.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;

/// The node classes the walk renders itself, by the name `type(node).__name__`
/// answers. [`TEXT_NODE`] is the fourth and comes from `template-core`, because
/// the descriptor names it too.
const VARIABLE_NODE: &str = "VariableNode";
const FOR_NODE: &str = "ForNode";
const IF_NODE: &str = "IfNode";
/// The one class whose body the walk reaches on its own hook rather than as a
/// planned slot (M19 step 5): `BlockNode.render` renders the body nodelist
/// through `NodeList._render_with_plan`, so the plan goes on the nodelist — see
/// [`plan_block_bodies`].
const BLOCK_NODE: &str = "BlockNode";

/// The nesting depth past which the walk raises, rather than letting the Rust
/// stack run out.
///
/// Stock Django raises `RecursionError` first, because the tree is built by
/// Python's recursive parser and CPython's limit is 1000 frames — measured on
/// the pin: 200 levels of `{% for %}` parse, 500 raise. A recursive Rust walk
/// would instead run out of *stack*, which is **not** catchable: the process
/// dies where stock raises a 500. So the cap is explicit rather than inherited,
/// and the failure *mode* stays what stock's is.
const MAX_WALK_DEPTH: usize = 500;

/// The head of a `{{ … }}`: the value the filters, if any, are applied to.
///
/// The two arms are `Variable`'s own two shapes, and they are exclusive for the
/// same reason Django's `(literal, lookups)` pair is: `lookups` is `Some`
/// exactly when the token was a real variable. See [`Head::Value`].
enum Head {
    /// A lookup: `Variable.lookups`, resolved against the context — always, and
    /// from the same `Variable.resolve` the Python engine runs. A sealed loop
    /// used to point the head of its body's first node at a `Frame` instead
    /// (a `local: bool` beside this field); that route is gone, because a loop
    /// now writes Django's per-iteration state unconditionally
    /// ([`Walk::run_loop_body`]), so there is no read the context cannot answer
    /// correctly. See §7 of `.review/m19-measurements/frame-rewrite-fix.md`.
    Lookups { lookups: Vec<String> },
    /// A literal, as the Python object Django's `Variable.literal` holds —
    /// `literal_to_py`'s answer, computed once at plan time rather than once per
    /// render. Django's `Variable` reuses its own `literal` object across
    /// renders in exactly the same way, and every value a literal can be is
    /// immutable, so no render can tell the two apart.
    Value(Py<PyAny>),
}

/// One filter argument, in Django's three shapes.
///
/// `FilterExpression` stores each argument as `(lookup, value)` and
/// `resolve` reads the flag (`base.py:806-809`), so the same pair decides here:
/// `lookup=False` is a constant — `arg_vals.append(mark_safe(arg))` — and
/// `lookup=True` is `Variable(var_arg)`, resolved against the context on every
/// render. The third shape hides inside the second: a `var_arg` may be a number,
/// which `Variable` turns into a literal rather than a lookup, so `lookup=True`
/// carries two cases and `lookups` is what tells them apart. [`FilterArg::Literal`]
/// is that one.
enum FilterArg {
    /// `mark_safe(arg)`, folded to plan time.
    ///
    /// Django marks it on every render, and the fold is safe for the reason the
    /// call is cheap to describe: `arg` is a *constant* — `var_obj =
    /// Variable(constant).resolve({})` at parse time (`base.py:755`) — `mark_safe` is a pure
    /// function of it, and its answer cannot reach the output by identity,
    /// because the render's last step builds a new string.
    Constant(Py<PyAny>),
    /// The `Variable` itself, resolved per render. Django's own line is
    /// `arg.resolve(context)` and the object it resolves is the one the parser
    /// built, so this holds that object rather than a re-derived lookup path.
    /// The lookup path is deliberately **not** kept: a body's plan asks nothing
    /// of it, because a variable argument resolves against the context, which
    /// holds the enclosing loop's state on every iteration.
    Variable { variable: Py<PyAny> },
    /// A `Variable(var_arg)` that is a **number**, so `resolve` returns the
    /// literal rather than a context lookup — `value = self.literal`
    /// (`base.py:929-931`), where `literal` is the `int`/`float` the parser
    /// computed (`base.py:887-895`).
    ///
    /// This is Django's *third* argument shape and it is easy to miss because it
    /// arrives as a `var_arg`: `var_chars` is `\w\.\+-` (`base.py:714`), digits
    /// included, so `|floatformat:2` is parsed `(True, Variable("2"))` and only
    /// the `lookup=True` flag says otherwise. The comment that explains the
    /// design is `base.py:686-687` — *"Numbers are treated as variables for
    /// implementation reasons (so that they retain their type when passed to
    /// filters)"* — and `retain their type` is the load-bearing half: the value
    /// here is the **`int` 2**, not the string `"2"`.
    ///
    /// So it is **not** `Constant`: Django's `lookup=True` arm of `arg_vals` does
    /// not `mark_safe` (`base.py:806-809`), and `mark_safe(2)` would not merely
    /// skip a wrapper, it would *stringify* — `SafeString(2)` is `"2"`. That
    /// difference is observable: `{{ x|floatformat:2.5 }}` on `1.2345` is
    /// `1.23` with the literal (`int(2.5)` is `2`) and `1.2345` with the string
    /// (`int("2.5")` raises `ValueError`, and `floatformat` returns the input).
    Literal(Py<PyAny>),
}

/// One `|name` or `|name:arg` the walk applies itself.
struct FilterStep {
    /// The name Rust's registry answers to. `run_filter` resolves the body by
    /// name — the *function* was already checked, by handle, when the plan was
    /// built, so this is not a second, name-based routing decision.
    name: String,
    /// Django's `arg_vals` element, or `None` for a filter with no argument.
    arg: Option<FilterArg>,
}

/// A `{{ … }}`: a head and the filters applied to it.
///
/// Also an `{% if %}` condition, which is a `FilterExpression` evaluated rather
/// than emitted — `TemplateLiteral.eval` is `self.value.resolve(context,
/// ignore_failures=True)` (`defaulttags.py:917-918`) — so the two share one shape and
/// one resolution.
struct Emit {
    head: Head,
    /// In source order; empty for an unfiltered `{{ … }}`.
    steps: Vec<FilterStep>,
}

/// One `{% if %}`/`{% elif %}`/`{% else %}` the walk owns.
///
/// Owned only when **every** branch's condition is an `{% else %}` or a
/// `TemplateLiteral` that is not an operator — the shape `{% if rows %}` is. A
/// condition built from an operator (`and`, `==`, `in`, …) is one of
/// `smartif`'s thirteen, evaluated by a `smartif` tree this walk does not walk,
/// so the whole node delegates.
struct IfPlan {
    /// `(condition, body)` in source order, `None` for the `{% else %}` —
    /// Django's own list, in Django's own order, with Django's own `None`.
    branches: Vec<(Option<Emit>, Vec<Slot>)>,
}

/// One node's native action.
///
/// `Act` and not `Step`, because `Step` is already `template-core`'s answer to
/// one arm of a lookup and this file implements that trait.
enum Act {
    /// A `TextNode`'s `s`, appended verbatim. Django never escapes a text node.
    Text(String),
    /// A `{{ … }}`.
    Emit(Emit),
    /// An `{% if %}`.
    If(Box<IfPlan>),
    /// A `{% for %}` the walk owns end to end.
    For(Box<ForPlan>),
    /// A node the walk does not render. `render_annotated` is called on it,
    /// which is also what keeps Django's culprit protocol in one place.
    Delegate,
}

/// One planned node, with the materialised node it was planned from.
struct Slot {
    act: Act,
    /// The materialised Python node — two jobs, and both need the object rather
    /// than a name: `render_annotated` is the delegate target, and Django's
    /// exception protocol reads `e._culprit_node.token` and
    /// `e._culprit_node.origin` off whatever node was rendering.
    node: Py<PyAny>,
    /// `type(node).__name__`, read once here so neither the render-time census
    /// nor the pairing assertion costs a Python attribute read per node.
    class_name: String,
}

/// A `{% for %}` this walk renders *itself* — the walk's own Rust loop, in
/// `ForNode.render`'s order, rather than that node's `render` handed to Python.
///
/// Built whenever both of the loop's nodelists plan and the loop has exactly one
/// variable — an unpacking loop writes one context entry per name and pops them
/// again per iteration, and the write order between them is observable, so it is
/// declined rather than half-done.
///
/// **That is the whole gate, and it is worth saying why nothing else is asked.**
/// A `{% for %}` used to be *sealed* only if a read pass over its body found no
/// node that would have to read the context, because the walk used to *elide*
/// Django's per-iteration state: the body read its names out of a Rust frame, so
/// `{{ forloop.counter }}` — or a filter argument naming the loop variable —
/// would have resolved against a context `ForNode.render` never leaves without
/// them, a miss where Django has a value. **The elision was unsound and is
/// gone.** The walk now writes Django's state on every iteration, exactly as
/// `ForNode.render` writes it ([`Walk::run_loop_body`]), and every name in the
/// body resolves the way Django resolves it — so a body *shape* cannot violate
/// anything, there is no read pass, and no body is refused.
///
/// What the walk still saves on a loop is real but narrower than it was, and the
/// record states it as such: the plan, the Rust-side resolution of every head,
/// and `ForNode.render`'s own dispatch are still gone. See §7 of
/// `.review/m19-measurements/frame-rewrite-fix.md`, §5.5 for what the write costs.
struct ForPlan {
    body: Vec<Slot>,
    /// The `{% empty %}` branch, planned normally. It is rendered **before**
    /// Django builds the dict (`engine/defaulttags.py:205-214`) and sees the
    /// enclosing context's `forloop` untouched.
    empty: Vec<Slot>,
    /// `self.sequence` — a `FilterExpression`, so the filters and the
    /// `ignore_failures` semantics are the transcribed Python ones.
    sequence: Py<PyAny>,
    /// `self.is_reversed`.
    is_reversed: bool,
    /// `self.loopvars[0]` — the name the loop writes into the context on every
    /// iteration, after the counters, as `ForNode.render` writes it
    /// (`engine/defaulttags.py:248`).
    ///
    /// **A `Py<PyString>` and not a `String`, because this is a dict key on a
    /// per-iteration path.** Passing the `&str` to `Context.__setitem__` builds a
    /// fresh `PyString` on every iteration of every loop — one build per item,
    /// forever, for a name the plan knew before the first render. The name is
    /// fixed at plan time, so the string is built once, here, and reused.
    /// `PyString::new` and not `PyString::intern`: the name comes from the
    /// template, and the interpreter's intern table is permanent, so interning a
    /// template-supplied name would let any template grow it without bound. See
    /// §5.5's first lever in `.review/m19-measurements/frame-rewrite-fix.md`.
    loopvar: Py<PyString>,
}

/// Which nodes the walk rendered and which it handed back, over every render
/// since [`census_start`].
///
/// A process-global rather than a per-render return value, because the question
/// it answers is asked of a whole suite run ("how many of the 1547 rendered
/// without a delegate") and the answer is a set. Off by default, and the walk's
/// only reference to it is an `Option` it carries, so the happy-path cost is
/// one branch per node.
#[derive(Default, Clone)]
struct Census {
    handled: Vec<(String, usize)>,
    delegated: Vec<(String, usize)>,
    /// The characters that came back from `render_annotated`, summed at the
    /// call site. See [`census_take`] for why this is not a partition.
    delegated_len: usize,
    /// The filters the walk applied **itself**, by the name Rust's registry
    /// answers to, in first-sighting order.
    ///
    /// **This is the node census's question one level down, and it exists
    /// because nothing else can see it.** Once a filtered `{{ … }}` is
    /// [`Act::Emit`], the filter body is reached inside Rust with *no* seam
    /// crossing at all — so `tests/test_interposition.py`'s counter on the
    /// Python-facing `_native.filter` reads zero on the fast route *and* reads
    /// zero when the routing is broken (a handle missing from
    /// `django_own_filters()` declines the node, and the Python arm then answers
    /// from the *transcription* rather than from Rust — the same silently-green
    /// failure M11 built that row for). A count of what the walk applied is the
    /// only instrument that tells the two apart.
    filters: Vec<(String, usize)>,
}

/// `(name, count)`, ascending by first sighting. Named because `census_take`
/// returns three of them and clippy reads a tuple of tuples as a type nobody
/// meant to write.
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

/// Python work executed **inside a filter body** since [`census_start`], over
/// every render.
///
/// The third meter, and the one the other two cannot replace.  [`Census::filters`]
/// counts the filters the walk applied *itself*, which is a statement about
/// **routing** — and a Rust body is free to call back into Python: `escape`'s body
/// is `conditional_escape(value)` (`filters/escape.rs`), `lower`'s is Python's
/// `str.lower` (Final_Sigma), `truncate`/`urlize`/`wrap` call Django's own
/// implementations.  So `applied == [("escape", 1)]` with `crossed == []` is true
/// while the escaping ran in Python — the same silently-green shape as M11's
/// finding, one level further in.  Counting the host's crossings is the only
/// instrument that separates "the body is Rust's" from "the body is Rust's and
/// then asks Python".
///
/// **What is counted is a crossing and not a host-method call.**  A `Value` that
/// is a `str`/`int`/`float`/`bool`/`None`/`list` is answered by Rust, and a host
/// method that answers it never touches Python — `upper` is `to_str` (pure for
/// `Value::Str`) plus a Rust table, so it reads **zero**.  What counts is a
/// Python object being touched or Python being executed: a call, an import, an
/// attribute or item access, a `str()`/`len()`/`isinstance`/truth test on an
/// object Python owns, and the `object(...)` conversion that hands a value over
/// at all.
///
/// **The window is the body and not the filter application.**  `run_filter_inner`
/// brackets `(entry.run)(...)` and reports the crossings made between the two
/// reads, so the seam's own work — `classify`, the `@stringfilter` coercion,
/// `localtime`, `is_safe`, the loop's `mark_safe` — is outside it by
/// construction.  A filter that crosses only in its wrapper reads zero, which is
/// the honest answer to "did this body do Python's work".
///
/// **And the applications counted are the walk's own, which is the whole point of
/// the meter.**  The seam has two callers — [`filter`], which is Python asking
/// (`_native.filter`, the fallback arm), and `Walk::apply_step`, which is the
/// walk applying one itself — and only the second has a [`Census::filters`] entry
/// to be checked against.  Counting both would put a number beside an `applied:
/// -` row that reads as if the walk's (empty) set of bodies had crossed: measured
/// before the flag, `django-only/filter-arg` printed `applied: -` with `body
/// xings: 200`, the 200 being the *Python* arm's `{{ …|title }}` inside the
/// delegated `ForNode`.  With the flag the two lines are about the same set of
/// bodies by construction, so "applied, and read zero" means what it says: Rust
/// computed it.  How much Python the fallback does is the delegated rows' own
/// business and is already reported by `delegated`.
static BODY_CROSSINGS: AtomicUsize = AtomicUsize::new(0);

/// Add `count` crossings to the census's third meter.
///
/// Called once per **walk-applied** `run_filter` with the body's own count rather
/// than once per crossing, so the counter lives in the host (which is created per
/// call and dropped with it) and this is the one place that reaches the
/// process-global.  The Python arm does not call this: see [`BODY_CROSSINGS`].
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
/// Called only on an **answer**: a refusal is the walk declining, and the
/// application that follows happens on the Python path, so counting it here
/// would report coverage the walk does not have.
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
    // Cleared before the flag is set, so a render that starts between the two
    // cannot write into the previous run's counts.
    *CENSUS.lock().unwrap() = Census::default();
    BODY_CROSSINGS.store(0, Ordering::Relaxed);
    CENSUS_ON.store(true, Ordering::Relaxed);
}

/// Stop the census and return `(handled, delegated, delegated_len, filters)`.
///
/// The third value is the reviewer's correction to this instrument, and it is
/// the one that matters on a page with inheritance. Counts alone are
/// **containment-blind**: a delegated `BlockNode` is one node and carries the
/// whole page body into Python with it, so `real-world/normal` reads 7 handled
/// against 4 delegated — 36% delegated — while essentially *all* of its output
/// came back from Python. `delegated_len` sums, at each delegate call site, the
/// length of the string `render_annotated` returned, so `delegated_len / len(the
/// render's output)` is a share of the output rather than a share of the nodes.
///
/// **It is not a partition and the number can exceed 100%.** A delegate nested
/// inside another delegate (an `{% include %}` inside a block inside an
/// `{% extends %}`) contributes its own length *and* is part of its parent's, so
/// the same character is counted once per nested delegate above it. That is the
/// honest shape for the question the number answers — "how much of what I print
/// came back from Python" — and it is deliberately not clamped: a row over 100%
/// says the delegates are nested, which is a fact about the template and not an
/// error in the meter.
///
/// The fourth is [`Census::filters`], and unlike the first two it *is* a
/// partition of one thing: every filter application in a walked template is
/// either counted here or made on the Python path.
///
/// The fifth is [`BODY_CROSSINGS`], the Python work done **inside** the bodies
/// the fourth counts — the meter that says whether "the walk applied it" also
/// means "Rust computed it".  Zero is the strong reading and it is a claim about
/// a particular filter and input, not a property of the filter: `upper` on a
/// `str` reads zero, `upper` on an `int` reads one (the `@stringfilter` coercion
/// is outside the window, so it is the body's `to_str` that crosses), and
/// `escape` reads one on any input because its body is `conditional_escape`.
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
/// The fold [`FilterArg::Constant`] argues for: `FilterExpression.resolve` calls
/// this on every render for every constant argument —
/// `arg_vals.append(mark_safe(arg))`, `base.py:807` — and the
/// argument is compile-time — `Variable(constant).resolve({})`, `base.py:768` —
/// so the answer is reached once, here.
static MARK_SAFE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn mark_safe_fn(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    MARK_SAFE.import(py, "django.utils.safestring", "mark_safe")
}

/// `django_rust_templates.host.django_own_filter_names`, cached once.
///
/// The identity-based router the Python arm already uses — `{func: name}` for
/// the handles in one expression, `None` where the handle is not a Rust filter —
/// and the second thing this file reads from Python at **plan** time rather than
/// per application (see `FilterRoutingTests` for the first).
static DJANGO_OWN_FILTER_NAMES: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn django_own_filter_names_fn(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    DJANGO_OWN_FILTER_NAMES.import(py, "django_rust_templates.host", "django_own_filter_names")
}

/// `django.conf.settings`, cached once — the **object**, not `settings.USE_TZ`.
///
/// Caching the object is the whole point: `override_settings` swaps attribute
/// values on one `Settings` instance and installs a fresh object in
/// `django.conf.settings` for the duration, so a cached `bool` would survive an
/// override and a cached module-level lookup would too. The object plus a
/// per-walk `getattr` sees both. See [`Walk::use_tz`] for when the read happens.
static DJANGO_SETTINGS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The head of a `{{ … }}` from its descriptor, or `None` if this walk declines.
///
/// Declines a translated variable (`_("x")` resolves to a `gettext_lazy` proxy
/// Python must build, and one a Rust literal cannot hold) and any expression
/// whose variable part is neither a lookup nor a literal.
///
/// Read from the **descriptor** rather than from the materialised
/// `FilterExpression`, and that is a cost decision with a measurement behind it:
/// the unfiltered case is the common one — `real-world/large` is 320 variable
/// nodes and every one of them is unfiltered — and four Python attribute reads
/// per node would be a per-*parse* cost, because `parse` mode compiles a fresh
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
        }))
    } else {
        match variable.literal.as_ref() {
            Some(value) => Ok(Some(Head::Value(literal_to_py(py, value)?))),
            None => Ok(None),
        }
    }
}

/// The filter chain of a materialised `FilterExpression`, or `None` if this walk
/// declines it.
///
/// **The chain is read from the object Django renders from**, not from the
/// descriptor, and the reason is what the descriptor does not carry: the
/// *function* each name was bound to (`FilterCall::handle` is a reference-table
/// index, and the table is not reachable from a plan) together with the
/// already-resolved constant arguments (`Variable(constant).resolve({})`,
/// `base.py:768`). Both are on the materialised object, because materialisation
/// is where Django puts them.
///
/// **Declines the whole expression if any handle is not ours.** A `{% load %}`ed
/// filter has no Rust body, and the alternative — running Django's callable from
/// here — would be a second implementation of the `FilterExpression.resolve`
/// loop this file is careful to keep to one. Handing the node back keeps Django's
/// Python arm for it, and the census counts it.
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
        // The answer is a *value* per handle, and `None` — Python's — is the
        // answer "not ours". `django_own_filter_names` maps every handle in the
        // expression at once, so the key is always present and an absent one is a
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
                    // `args.append((True, Variable(var_arg)))` (`base.py:770`) —
                    // a `var_arg`, which is either a real variable (lookups set)
                    // or a **number** (lookups `None`), and `base.py:686-687` is
                    // why the second is spelled the same as the first.
                    let lookups = value.getattr("lookups")?;
                    if lookups.is_none() {
                        // `Variable.__init` leaves `lookups` unset for exactly one
                        // shape in this arm — a number (`self.literal = int(var)`,
                        // `base.py:887-895`) — and `resolve` hands that literal
                        // back without touching the context (`base.py:929-931`).
                        // "In this arm" is doing work: `Variable` reaches
                        // `lookups=None` with a literal set by a second route, the
                        // quoted string at `base.py:906`, and what rules that route
                        // out is the regex above it rather than the class —
                        // `var_chars` (`base.py:714`) cannot spell a leading quote
                        // and `unescape_string_literal` raises unless `s[0]` is one
                        // (`utils/text.py:457`). Note also that the test is *the
                        // numeric branch completed*, not "the argument looks like a
                        // number": `Variable("2.")` sets `literal=2.0` **and**
                        // `lookups=('2','')`, so it resolves as a variable.
                        // A `literal` of `None` here would be a `Variable` neither
                        // arm explains, so the node goes back rather than a `None`
                        // argument reaching a filter.
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
                    // `args.append((False, Variable(constant_arg).resolve({})))`
                    // (`base.py:768`) hands already resolved, and
                    // `arg_vals.append(mark_safe(arg))` (`base.py:807`) is what
                    // makes the callable's argument the safe one.
                    Some(FilterArg::Constant(
                        mark_safe_fn(py)?.call1((value,))?.unbind(),
                    ))
                }
            }
            // Unreachable: `filter_re`'s argument group allows zero or one
            // (`(?:%(arg_sep)s`, `base.py:706-711`). Declined rather than
            // asserted, because a
            // descriptor and a materialised object disagreeing is the failure
            // every other arm here answers by handing the node back.
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
/// An unfiltered expression never touches Python; a filtered one must, because
/// the handles are Python objects (see [`plan_steps`]) and the descriptor alone
/// cannot say whether they are ours. The head is read from the descriptor either
/// way — see [`plan_head`].
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
/// The head is read from the object here rather than from a descriptor, because
/// a condition's descriptor is a `NodeRef` into the reference table and nothing
/// else — `IfConditionDesc::Literal` carries the expression's *handle*, not its
/// shape. A condition is one per branch rather than one per node, so the Python
/// reads are paid on the rows that write `{% if %}` and not on the rows that are
/// mostly variables.
///
/// **`ignore_failures=True` is not spelled here**: it is a property of the
/// caller — `TemplateLiteral.eval` (`defaulttags.py:917-918`) — and it changes
/// only what a failed head means, which is [`Walk::run_if`]'s question.
fn plan_condition(py: Python<'_>, expression: &Bound<'_, PyAny>) -> PyResult<Option<Emit>> {
    let head = if expression.getattr("is_var")?.is_truthy()? {
        let variable = expression.getattr("var")?;
        if variable.getattr("translate")?.is_truthy()? {
            return Ok(None);
        }
        let lookups = variable.getattr("lookups")?;
        if lookups.is_none() {
            return Ok(None);
        }
        Head::Lookups {
            lookups: lookups.extract()?,
        }
    } else {
        // `obj = self.var` — `FilterExpression.resolve`'s non-var arm
        // (`base.py:801-802`), which is the object itself.
        Head::Value(expression.getattr("var")?.unbind())
    };
    match plan_steps(py, expression)? {
        Some(steps) => Ok(Some(Emit { head, steps })),
        None => Ok(None),
    }
}

/// `{% if %}` as an [`IfPlan`], or `None` for a node `IfNode.render` must run.
///
/// The **shape** check is on the descriptor and the conditions are read from the
/// object: whether a branch is an `{% else %}` or a `TemplateLiteral` rather
/// than a `smartif` operator is a question about the parse tree, which is what
/// the descriptor is. A branch body that declines hands the whole node back —
/// [`plan_slot`]'s convention, so a half-planned `{% if %}` cannot render — and
/// so does an `{% elif %}` written as an operator, which is one of `smartif`'s
/// thirteen and is not evaluated here at all.
fn plan_if(py: Python<'_>, desc: &NodeDesc, node: &Bound<'_, PyAny>) -> PyResult<Option<IfPlan>> {
    let Some(AttrValue::List(branches)) = find_attr(desc, "conditions_nodelists") else {
        return Ok(None);
    };
    let materialised = node.getattr("conditions_nodelists")?;
    // The branches and the bodies are two lists the parse wrote in step
    // (`tags/if.rs` pushes a branch and a nodelist per clause, and `do_if` does
    // the same in `defaulttags.py`), so a disagreement is a pairing failure and
    // not a template this walk cannot plan.
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
            // `conditions_nodelists.append((None, nodelist))` — `do_if`'s own
            // `{% else %}` (`defaulttags.py:1009`).
            AttrValue::None => None,
            AttrValue::IfCondition(tree) => match tree.as_ref() {
                // `nud` and `led` build this arm, and a `smartif` operator tree
                // is thirteen operators deep. Declined as a whole.
                IfConditionDesc::Operator { .. } => return Ok(None),
                IfConditionDesc::Literal { .. } => {
                    let literal = pair.get_item(0)?;
                    if literal.is_none() {
                        return Ok(None);
                    }
                    match plan_condition(py, &literal.getattr("value")?)? {
                        Some(emit) => Some(emit),
                        None => return Ok(None),
                    }
                }
            },
            _ => return Ok(None),
        };
        let AttrValue::NodeListRef(body_index) = body_ref else {
            return Ok(None);
        };
        // `{% if %}` is the one tag with **positional-only** nodelists
        // (`tags/if.rs`): `IfNode` holds its branch bodies inside
        // `conditions_nodelists`, so the descriptor carries N unnamed nodelists
        // and the branch names its own by position.
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
    // Always present on the descriptor (`tags/for.rs:163-164`), even when the
    // tag has no `{% empty %}`: Django's `ForNode.__init__` substitutes an empty
    // `NodeList` and the shell materialises one, so the pairing holds either way.
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
    // An unpacking loop writes one context entry per name and pops them back
    // per iteration, and each name is a frame of its own. Declined rather than
    // half-done.
    if loopvars.len() != 1 {
        return Ok(None);
    }
    // **Nothing is asked of the body, because nothing about the state is
    // elided.** The state write used to depend on a read pass and a write pass
    // agreeing that the body could not write the context; the write is now
    // unconditional ([`Walk::run_loop_body`]) and the reads are Django's own, so
    // there is no question left to ask of a body shape.
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
/// `None` means the pairing does not hold, which aborts the whole plan: a plan
/// that is right about most nodes and wrong about one is a render that differs
/// from Django's in a way no test would name.
fn plan_slot(py: Python<'_>, desc: &NodeDesc, node: &Bound<'_, PyAny>) -> PyResult<Option<Slot>> {
    let class_name: String = node.get_type().name()?.to_string();
    // `HOST_NODE` is the one descriptor whose class name is not the object's: a
    // user tag's compile function returned that object, and the descriptor
    // could only name the slot (see `desc.rs::HOST_NODE`).
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
    } else if class_name == IF_NODE {
        match plan_if(py, desc, node)? {
            Some(plan) => Act::If(Box::new(plan)),
            None => Act::Delegate,
        }
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
/// Built by `compile_template` and attached to the `Template` the shell builds;
/// `Template._render` hands it back to [`render_plan`]. A template with no plan
/// renders on the Python path exactly as before this slice, so a plan this
/// builder declines is a lost optimisation and never a behaviour change.
#[pyclass]
struct NativePlan {
    slots: Vec<Slot>,
}

/// A plan for one nodelist, already boxed as the Python object the body's
/// render reads, or `None` if the pairing does not hold.
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

/// Attach a plan to every `{% block %}` body in the tree (M19 step 5).
///
/// **Why this pass exists at all.**  A `{% block %}`'s body is reached through
/// `BlockNode.render`, which calls `block.nodelist.render(context)` — a plain
/// Python `NodeList.render`.  The walk therefore never enters a block body, and
/// on a page with inheritance that is where the whole render lives: measured,
/// `real-world/normal` returns **198.8%** of its output from delegates and
/// **99.0%** of its wall time is inside the one delegated `ExtendsNode` frame
/// (`.review/m19-measurements/inheritance_share.py`).  `Template._render` is the
/// same hook one level up; this is it one level down.
///
/// **This pass attaches; `NodeList._render_with_plan` reads.**  That method, not
/// `NodeList.render`, and the pair is a pair because of the transcription guard:
/// `NodeList.render` is on the parity harness's compared path, so an arm inside
/// it would make the comparison Rust against Rust.  Nothing else about the design
/// changes — the attach is on the body `NodeList` either way, and clause (4) of
/// `scripts/check_transcriptions.py` is the reason.  The caller is
/// `loader_tags.py::BlockNode.render`, whose two body renders are upstream's own
/// lines rewritten by the generator's `TRANSFORMS` table: this pass and base.py
/// are hand-written, `loader_tags.py` is generated, and a generated transcription
/// may differ from the pin only by whole-line substitutions.
///
/// **The tree it descends is the descriptor tree, paired with the `NodeList`
/// materialised from it**, because `compile_template` is the only place that
/// holds both — the same reason the root plan is built there.  Descent is by the
/// attribute name each descriptor's nodelist was declared with, which is the
/// constructor parameter name and therefore the attribute name the materialiser
/// set; `NodeListDesc::get_nodes_by_type` descends by position instead, and the
/// two agree for every node Rust builds except `IfNode` (see `desc.rs`).
///
/// **`IfNode` is the one class whose branches cannot be reached by name**: its
/// `__init__` takes `conditions_nodelists` and holds its child nodelists inside
/// that list, so Rust emits them as *empty-named* descriptors — "positional
/// only", the marker `materialise_node` documents — and the materialised node
/// stores them at `conditions_nodelists[i][1]`.  A `{% block %}` inside an
/// `{% if %}` branch is reachable exactly that way and nowhere else: `plan_if`
/// cannot plan a branch containing a `BlockNode` (that is a node `plan_slot`
/// declines), so the `IfNode` is delegated and Python reaches the block body —
/// which is the case this arm is for.  The pairing is by position, which is the
/// order `tags/if.rs` emitted them, so a wrong pair is not possible; a missing
/// attribute or a short list is skipped rather than raised, because an
/// optimisation must not turn a shape it does not understand into a compile
/// error.
///
/// **Nothing here can change an answer.**  A body whose nodes and descriptors
/// disagree gets no plan (`plan_list` returns `None` on the first class
/// mismatch), and the body's render runs exactly as it did before when the plan
/// is absent.  So a `{% block %}` this pass misreads is a lost
/// optimisation, which is the contract `plan_list` already has.
///
/// `HOST_NODE` descriptors carry no nodelists at all (`NodeDesc::host`), so a
/// host tag's body is not descended into — the shape `desc.rs` records as the
/// one place a host-owned body could differ, and the divergence is bounded there
/// to a tag that returns a real `ExtendsNode`/`BlockNode` from Python.
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
        // The same pairing rule `plan_slot` applies, and the same reason: a
        // descriptor and a node that disagree are not about each other, so
        // neither this node's body nor its children may be planned from it.
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

/// One `IfNode` branch nodelist, by position, or `None` for a node that is not
/// an `IfNode` — or one whose shape is not what `tags/if.rs` builds.
fn if_branch<'py>(node: &Bound<'py, PyAny>, index: usize) -> Option<Bound<'py, PyAny>> {
    let branches = node.getattr("conditions_nodelists").ok()?;
    let branch = branches.get_item(index).ok()?;
    branch.get_item(1).ok()
}

/// `Node.render_annotated`'s `except` arm (`engine/base.py:1135-1164`), for a node this
/// walk rendered itself.
///
/// Django's two guards are both `hasattr` — first-writer-wins — so the innermost
/// failing node names the culprit and every enclosing one leaves it alone. The
/// walk reproduces that by *being* the nesting: each native step's error arm runs
/// in a Rust frame nested inside its caller's, so the innermost sets the
/// attribute and the outer frames find it already there. That is the property a
/// flat plan with jumps would have had to reconstruct.
///
/// Everything is read at error time and nothing on the happy path, which is
/// Django's shape too: `context.template.engine.debug` is read inside the
/// handler, so a template rendered without debug pays nothing for the protocol.
fn annotate_culprit(py: Python<'_>, context: &Bound<'_, PyAny>, slot: &Slot, error: &PyErr) {
    // The walk is only reachable through `Template._render`, which is only
    // reachable with a bound template, so none of these reads can fail the way
    // they would for a hand-called `render_annotated`. If one does anyway the
    // original exception propagates unannotated: losing an annotation is a
    // smaller failure than replacing the exception with whatever was raised
    // while describing it.
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
/// There is exactly one route to `Refused`, and it is not an error: a filter
/// whose answer is a lone surrogate answers the seam's sentinel
/// ([`cannot_hold`]) rather than raising, because the value is one CPython holds
/// and this walk's accumulator is a Rust `String`. The node is then rendered by
/// `render_annotated` — which is what Django would have done had the walk never
/// owned it, and which absorbs the sentinel the way the transcribed `resolve`
/// does.
enum Rendered {
    Done,
    Refused,
}

/// What one filter application did.
enum StepOutcome {
    Value(Py<PyAny>),
    Refused,
}

/// What `FilterExpression.resolve` did with a **failed head** — three answers,
/// and three different places to go next.
///
/// The distinction is not cosmetic and it is not a detail of this slice: the
/// truthy `string_if_invalid` returns *early*, so the filter chain never runs,
/// while the falsy one assigns and **falls through**. `""` is the default
/// setting, so the second arm is the common one — it is what makes
/// `{{ missing|default:"x" }}` render `"x"` rather than `""`.
enum Absorbed {
    /// `string_if_invalid` is truthy, so `resolve` returned it —
    /// `return string_if_invalid` (`base.py:793-798`).
    Final(Py<PyAny>),
    /// Falsy: `obj = string_if_invalid` and the chain runs on it
    /// (`base.py:800`).
    Continue(Py<PyAny>),
    /// `VariableNode.render`'s `UnicodeDecodeError` arm: the node renders `""`
    /// and `render_value_in_context` never runs (`engine/base.py:1351-1354`).
    Nothing,
}

/// What one `{% if %}` condition decided.
enum Branch {
    Match,
    /// Falsy — and the `VariableDoesNotExist` that `IfNode.render` catches and
    /// turns into the same falsy `None` (`except VariableDoesNotExist:`,
    /// `engine/defaulttags.py:329-330`).
    NoMatch,
    Refused,
}

/// The render-invariant state of one walk.
struct Walk<'py> {
    py: Python<'py>,
    context: Bound<'py, PyAny>,
    /// `django_rust_templates.engine.base.render_value_in_context`, fetched once.
    /// Django's function, not a Rust reimplementation of it: it is the
    /// transcription the transcription guard pins, so the fallback path is
    /// byte-identical by construction rather than by a test that keeps pace.
    escape: Bound<'py, PyAny>,
    scope: LookupScope,
    /// `context.autoescape`, hoisted — and refreshed after every delegate,
    /// because `AutoEscapeControlNode` writes that attribute **in place** on the
    /// context object rather than restoring it later.
    autoescape: bool,
    /// `template_localtime`'s `use_tz` argument
    /// (`django/utils/timezone.py:146-161`), filled on first use.
    ///
    /// **Lazy, and that is the design.** `Context.use_tz` is `None` unless
    /// `{% localtime %}` or `{% timezone %}` set it, and the rule for `None` is
    /// `settings.USE_TZ` — so a value resolved eagerly would put a
    /// `django.conf` import on every walked render, including the ones whose
    /// filters are no date filter at all. At 6.0.9, 2 of the 57 builtins declare
    /// `expects_localtime` and `run_filter` reads this for those two only, so the
    /// read belongs where the first filter is applied and nowhere earlier. A
    /// delegate clears it, because a delegate is the one thing that can change
    /// `context.use_tz`.
    use_tz: Option<bool>,
    /// The output, as a Rust `String` — which is the whole point of the walk,
    /// and which Python's `str` is not a superset of. See [`Walk::push_python`].
    out: String,
    /// Set once a piece will not fit `out`; `Some` means the accumulator is
    /// Python's from then on and the pieces are here.
    spill: Option<Vec<Py<PyAny>>>,
    /// How many nodes have been pushed. Read by [`Walk::push_python`] as the
    /// **node index** of the piece it is holding, which is the ordinal
    /// `NodeList.render`'s `"".join` would name for it.
    nodes: usize,
    census: Option<Census>,
}

impl<'py> Walk<'py> {
    /// Render one node list, in `NodeList.render`'s order, into this walk's
    /// accumulator.
    ///
    /// `depth` is the walk's own nesting count, checked against
    /// [`MAX_WALK_DEPTH`] — the one piece of state a body inherits from its
    /// parent, and the only reason this is not a plain slice walk.
    fn run(&mut self, slots: &[Slot], depth: usize) -> PyResult<()> {
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
                // No `culprit` here: `render_annotated` *is* the culprit
                // protocol, so annotating on this side would be a second writer
                // of an attribute whose whole contract is first-writer-wins.
                Act::Delegate => self.delegate(slot)?,
                Act::Emit(emit) => match self.run_emit(slot, emit) {
                    Ok(Rendered::Done) => census_bump(&mut self.census, &slot.class_name, false),
                    // A refusal is a *value* the filter returned, so the node
                    // goes back to Python whole — and the context it goes back
                    // into already holds the enclosing loop's state, because the
                    // loop wrote it ([`Walk::run_loop_body`]).
                    Ok(Rendered::Refused) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::If(plan) => match self.run_if(plan, depth + 1) {
                    Ok(Rendered::Done) => census_bump(&mut self.census, &slot.class_name, false),
                    Ok(Rendered::Refused) => self.delegate(slot)?,
                    Err(error) => return Err(self.culprit(slot, error)),
                },
                Act::For(plan) => match self.run_for(plan, depth + 1) {
                    Ok(()) => census_bump(&mut self.census, &slot.class_name, false),
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

    /// Render one node with Python — the arm an unplanned node takes, and the arm
    /// a refused one takes once its loop state is in place.
    fn delegate(&mut self, slot: &Slot) -> PyResult<()> {
        let rendered = slot
            .node
            .bind(self.py)
            .call_method1("render_annotated", (&self.context,))?;
        // Before the piece is moved into the sink: what this node contributed
        // to the output. A `str` that has no length is not a thing, and a piece
        // that is not a `str` raises inside `push_python` and so contributes
        // nothing to count.
        if let Some(census) = self.census.as_mut() {
            census.delegated_len += rendered.len().unwrap_or(0);
        }
        self.push_python(rendered.unbind())?;
        // The two context attributes a delegate can have changed, both of them
        // written **in place** on the context rather than restored later:
        // `autoescape` by `AutoEscapeControlNode`, `use_tz` by `{% localtime %}`
        // and `{% timezone %}`.
        self.autoescape = self.context.getattr("autoescape")?.extract()?;
        self.use_tz = None;
        census_bump(&mut self.census, &slot.class_name, true);
        Ok(())
    }

    /// `if "forloop" in context: parentloop = context["forloop"] else: {}`
    /// (`engine/defaulttags.py:194-198`).
    ///
    /// Read **before** the push by [`Walk::run_for`], which is Django's order and
    /// not a convenience: after a push into this loop's own layer, a read would
    /// find the `forloop` *this* loop wrote on an earlier iteration and name it
    /// its own parent.
    fn parentloop(&self) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        if self.context.contains("forloop")? {
            self.context.get_item("forloop")
        } else {
            Ok(PyDict::new(py).into_any())
        }
    }

    /// Render one `{{ … }}` — `VariableNode.render`'s shape
    /// (`engine/base.py:1288-1296`) with the resolution done in Rust.
    ///
    /// The two absorbing arms are `render_resolved`'s, and the third route out is
    /// a refusal, which is a *value* the filter returned rather than an error.
    fn run_emit(&mut self, slot: &Slot, emit: &Emit) -> PyResult<Rendered> {
        let mut value = match self.resolve_head(&emit.head) {
            Ok(value) => value,
            Err(error) => match absorb_emit(self.py, slot, &self.context, error)? {
                // Truthy `string_if_invalid`: `resolve` returned it and the
                // filters never ran.
                Absorbed::Final(value) => {
                    self.emit(value)?;
                    return Ok(Rendered::Done);
                }
                Absorbed::Continue(value) => value,
                // The `UnicodeDecodeError` arm: nothing emitted, and the node
                // still counts as a node.
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

    /// Render one `{% if %}` this walk owns — `IfNode.render`'s loop
    /// (`engine/defaulttags.py:324-337`).
    ///
    /// The one way out without rendering a branch is a refusal, and a refusal can
    /// only come from a condition: the body either runs to completion or returns
    /// its own error. A condition is evaluated before anything is emitted, so
    /// handing the whole node back at that point is handing back a node that has
    /// not written a byte — which is what makes it safe to do.
    fn run_if(&mut self, plan: &IfPlan, depth: usize) -> PyResult<Rendered> {
        for (condition, body) in &plan.branches {
            let matched = match condition {
                None => true,
                Some(condition) => match self.condition_matches(condition)? {
                    Branch::Match => true,
                    Branch::NoMatch => false,
                    Branch::Refused => return Ok(Rendered::Refused),
                },
            };
            if matched {
                self.run(body, depth)?;
                return Ok(Rendered::Done);
            }
        }
        // `IfNode.render`'s `return ""` (`engine/defaulttags.py:337`): a node
        // that matched no branch contributes nothing, and is still a node.
        self.push_text("")?;
        Ok(Rendered::Done)
    }

    /// `TemplateLiteral.eval` (`engine/defaulttags.py:919-920`) —
    /// `self.value.resolve(context, ignore_failures=True)`.
    ///
    /// `ignore_failures=True` is the whole difference from an emitted `{{ … }}`,
    /// and the flag's name is the rule: a failed head is `None` and the filters
    /// still run on it, where a `{{ … }}` would have reached for
    /// `string_if_invalid`. `IfNode.render` then catches `VariableDoesNotExist`
    /// from anywhere in the evaluation and calls the branch a miss, which is why
    /// the filter loop below has a `VariableDoesNotExist` arm of its own.
    fn condition_matches(&mut self, condition: &Emit) -> PyResult<Branch> {
        let mut value = match self.resolve_head(&condition.head) {
            Ok(value) => value,
            Err(error) if is_variable_does_not_exist(self.py, &error)? => self.py.None(),
            Err(error) => return Err(error),
        };
        for step in &condition.steps {
            match self.apply_step(step, &value) {
                Ok(StepOutcome::Value(next)) => value = next,
                Ok(StepOutcome::Refused) => return Ok(Branch::Refused),
                // The failed *argument* case: `resolve` has no handler there, so
                // it travels, and `IfNode.render` is what decides it is a miss.
                Err(error) if is_variable_does_not_exist(self.py, &error)? => {
                    return Ok(Branch::NoMatch);
                }
                Err(error) => return Err(error),
            }
        }
        Ok(if value.bind(self.py).is_truthy()? {
            Branch::Match
        } else {
            Branch::NoMatch
        })
    }

    /// The head's value, by whichever of [`Head`]'s two shapes it is.
    ///
    /// There is one lookup arm, resolved against the context, because that is
    /// where Django's `Variable.resolve` resolves it and the context now holds
    /// the enclosing loop's state on every iteration. See [`Head::Lookups`].
    fn resolve_head(&self, head: &Head) -> PyResult<Py<PyAny>> {
        match head {
            Head::Value(value) => Ok(value.clone_ref(self.py)),
            Head::Lookups { lookups } => self.scope.resolve(self.py, lookups, &self.context),
        }
    }

    /// Apply one filter: the body of `resolve`'s loop
    /// (`for func, args in self.filters:`, `engine/base.py:868-874`).
    fn apply_step(&mut self, step: &FilterStep, value: &Py<PyAny>) -> PyResult<StepOutcome> {
        let py = self.py;
        // `arg_vals`: a constant was `mark_safe`d at plan time, a variable is
        // `arg.resolve(context)` with **no handler**, so a failed argument
        // propagates out of `resolve` and each caller decides what it means. A
        // literal is neither resolved nor wrapped — it is the object `resolve`
        // would have returned (`value = self.literal`), which for a numeric
        // argument is a number and not its text.
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
        // **A refusal is an answer, not an error** — the seam's sentinel, in
        // place of a result this walk cannot hold. Handing the node back is what
        // the Python `resolve` does with the same sentinel, one level up, and the
        // application that follows is Python's: the census counts only the
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
            // The **object** is cached and the attribute is read per walk, so an
            // `override_settings` between two renders is still seen.
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

    /// Append one resolved value, taking `render_value_in_context`'s first arm
    /// here when it applies.
    ///
    /// The arm is **exact `str` under autoescaping**, and the exactness is
    /// load-bearing rather than tidy: a `str` subclass is where `SafeString`
    /// lives, it carries `__html__`, and escaping one is the bug the predicate
    /// declines. `html_escape` returning `None` (a lone surrogate, which the
    /// binding refuses rather than raises on) falls through to the Python
    /// function, which takes the slow path with the same answer.
    fn emit(&mut self, value: Py<PyAny>) -> PyResult<()> {
        let py = self.py;
        // Gated on the spill because the fast path writes `out` directly: once
        // the accumulator is Python's, `out` is an empty remnant and a piece
        // written to it would be lost.
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

    /// Append one piece that came back from Python, which may not be a piece a
    /// Rust `String` can hold.
    ///
    /// **The accumulator is a Rust `String`, and Python's `str` is not a subset
    /// of it.** A lone surrogate — `'\ud800'`, which
    /// `FilterSeamRefusalTests.test_a_string_the_seam_cannot_hold_renders_as_django_renders_it`
    /// renders through `{{ x|upper }}` — is a `str` CPython carries and one no
    /// `String` can. So the first piece that will not fit **spills** the
    /// accumulator into Python's: the fast path stays one `push_str` per node,
    /// and the slow path ends in `"".join`, which is what `NodeList.render`
    /// does (`engine/base.py:1199-1200`) and therefore what the walk owes it.
    ///
    /// Two failures are not the same failure here, and the branch below is the
    /// difference. A **`str` that will not fit** is a value Django renders, and
    /// it goes into the spill. A piece that is **not a `str` at all** is a value
    /// Django dies on: `NodeList.render` is
    /// `SafeString("".join([node.render_annotated(context) for node in self]))`,
    /// so a node returning anything else raises `TypeError` from the `join`, and
    /// this raises from the same call rather than re-writing its message — the
    /// list is padded so the offending piece sits at its **node** index
    /// (`self.nodes`), because that ordinal and the type name in the text are
    /// CPython's to phrase. The padding is empty strings: the `join` raises
    /// before it concatenates anything, so only the position is observed.
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
                // The prefix has merged `self.nodes` nodes and becomes one
                // element. The ordinal above is computed from the *count*, not
                // from this list, so merging does not lose it.
                self.spill = Some(vec![PyString::new(py, &self.out).into_any().unbind()]);
                self.out.clear();
            }
            self.spill.as_mut().unwrap().push(piece);
        } else {
            let list = PyList::empty(py);
            for _ in 0..self.nodes {
                list.append("")?;
            }
            list.append(&piece)?;
            PyString::new(py, "").call_method1("join", (list,))?;
            unreachable!("`''.join` over a non-str piece did not raise");
        }
        self.nodes += 1;
        Ok(())
    }

    /// A `{% for %}` the walk runs itself, in `ForNode.render`'s order
    /// (`engine/defaulttags.py:193-258`).
    ///
    /// **The layer is Django's, it is opened once per loop, and it opens before
    /// the sequence resolves.** Both halves of that sentence were wrong until the
    /// second adversarial review, and neither is a detail:
    ///
    /// * `with context.push():` is entered at `engine/defaulttags.py:198` and
    ///   `values = self.sequence.resolve(context, ignore_failures=True)` is the
    ///   first statement inside it (`engine/defaulttags.py:199`). A sequence is a
    ///   `FilterExpression` — arbitrary Python this walk handed the **context** —
    ///   so it can both read the layer stack and write into it, and resolving one
    ///   layer out gets both wrong: a filter reading `len(context.dicts)` sees one
    ///   too few, and a filter that assigns `context[name]` writes a layer where
    ///   Django's write is popped with the `{% endfor %}`.
    /// * `parentloop = context["forloop"]` is read *above* the `with`
    ///   (`engine/defaulttags.py:194-197`) for **every** loop, and it has to be read
    ///   here too: the loop writes a `forloop` into its own layer before the first
    ///   iteration ([`Walk::run_loop_body`]), so reading it inside the body would
    ///   name *this* loop its own parent from the second iteration on.
    ///
    /// The push is per loop and not per iteration: the layer is opened once and
    /// closed once, and only the state writes inside it are per iteration.
    fn run_for(&mut self, plan: &ForPlan, depth: usize) -> PyResult<()> {
        let py = self.py;
        let parentloop = self.parentloop()?;
        let pushed = self.context.call_method0("push")?;
        let result = self.run_loop_body(plan, &parentloop, depth);
        // On both paths, as `with` does: an exception must not leave the
        // context a layer deeper than the loop found it. `__exit__` is called
        // explicitly because the exception is *travelling* here, not unwinding
        // through the `with`.
        let popped = pushed.call_method1("__exit__", (py.None(), py.None(), py.None()));
        result?;
        popped?;
        Ok(())
    }

    /// [`Walk::run_for`]'s body, inside the loop's layer: the sequence, the empty
    /// test, the reversal, and the iterations with Django's per-iteration state.
    ///
    /// **The order inside the layer is the argument, and every step of it is
    /// observable.** `values` is resolved and normalised to something with a
    /// length *before* the empty test; the length is read *before* the reversal,
    /// because `reversed(values)` has no length; the empty branch is rendered
    /// *before* any loop state exists; the `forloop` dict is written *before* the
    /// first iteration and mutated in place from inside it; and within an
    /// iteration the six counters go in *before* the loop variable, which is
    /// Django's order at `engine/defaulttags.py:220-227` then
    /// `engine/defaulttags.py:248`.
    ///
    /// Split out from [`Walk::run_for`] rather than written inline so that the
    /// layer is closed on every way out of the loop, error included, from one
    /// place.
    fn run_loop_body(
        &mut self,
        plan: &ForPlan,
        parentloop: &Bound<'py, PyAny>,
        depth: usize,
    ) -> PyResult<()> {
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
        // Read *before* the reversal, which is both Django's order
        // (`len_values = len(values)`, `engine/defaulttags.py:204`) and the only
        // order that works: `reversed(values)` is an iterator and has no length.
        let length = values.len()?;
        if length < 1 {
            // Inside the layer, because Django's is. The `with` opens *before*
            // the empty test, and the empty arm is returned from inside it
            // (`engine/defaulttags.py:198-206`), so a write an `{% empty %}` body
            // makes is discarded with the loop's layer. Rendering the arm outside
            // any push keeps the write — and the write is observable, which is
            // not a thing that can be argued away: `{% for r in none %}{% empty
            // %}{% writes "foo" %}{% endfor %}{{ foo }}` renders `[]` in stock and
            // `[W]` without this.
            return self.run(&plan.empty, depth);
        }
        if plan.is_reversed {
            values = py
                .import("builtins")?
                .getattr("reversed")?
                .call1((values,))?;
        }
        // **`loop_dict = context["forloop"] = {...}` — built once, mutated in
        // place per iteration** (`engine/defaulttags.py:214-229`). That shape is
        // Django's and not an optimisation of ours: a fresh dict per iteration
        // would give a nested loop inside a delegate a different `parentloop`
        // object than Django gives it — the same numbers, which is why it would
        // pass a test that only reads `.counter`, and the wrong identity.
        //
        // `parentloop` is an argument rather than a read here for the reason
        // [`Walk::parentloop`] gives, and `length` is one of the two keys Django
        // writes **once**, before the iterations — so a nested loop inside a
        // delegate reads `forloop.parentloop.length` off this dict, and a dict
        // missing it is a `KeyError` where Django has a number.
        let forloop = PyDict::new(py);
        forloop.set_item(intern!(py, "parentloop"), parentloop)?;
        forloop.set_item(intern!(py, "length"), length)?;
        self.context.set_item(intern!(py, "forloop"), &forloop)?;
        // **The state is written on every iteration, for every loop, and that is
        // the whole of this fix.** A body used to be able to *seal* — the walk
        // elided these writes and served the body's reads from a Rust frame — and
        // the elision was unsound: any node in a body can run Python on a value
        // that holds the context and writes a name the frame then answers stale
        // (`.review/m19-measurements/ctx_write_channels.py` rows 15-17, and the
        // third round's two templates). Nothing about a body shape can decide
        // whether that happened, so nothing is elided and no shape is refused.
        //
        // The writes are Django's, in Django's order: the six counters into the
        // dict first (`engine/defaulttags.py:220-227`), then
        // `context[loopvars[0]] = item` (`engine/defaulttags.py:248`).
        // The order is observable — a loop named `forloop` has the item written
        // *over* the dict at `engine/defaulttags.py:248` — so it is transcribed
        // rather than reordered.
        // **The reverse counters are signed, and it is not a style choice.**
        // Django's are Python integers and go *negative* when the sequence yields
        // more items than `len()` reported — which is ordinary behaviour for a list
        // that grows while it is rendered, and for any object whose `__iter__` and
        // `__len__` disagree (`engine/defaulttags.py:223-224`; `engine/defaulttags.py:226-227` does not police it). Computing them in
        // `usize` wraps instead: the adversarial reviewer's fourth round rendered
        // `{% for s in xs %}{{ s }}:{{ forloop.revcounter }}/{{ forloop.revcounter0
        // }};{% endfor %}` over a one-element list whose element appends to it, and
        // got `b:0/18446744073709551615` where Django gives `b:0/-1`. `length` comes
        // from Python's `len()`, so it fits `i64` by construction; `index` passing
        // `i64::MAX` would take 9.2e18 iterations.
        let signed_length = length as i64;
        // **The keys are hoisted out of the iteration, and that is the whole of
        // this change.** `forloop.set_item(&str, …)` builds a `PyString` from the
        // `&str` on *every* call: six counters, six builds per iteration, 300 for
        // a 50-item loop, for names that are literals here and identical on every
        // pass. `intern!` answers with the same `PyString` each time, as CPython
        // does with the literal keys of Django's own `loop_dict = {"counter0":
        // …}`. On the **write** — the only path this loop pays — that is three
        // things: no build at all, a hash computed once per key object and cached
        // instead of 350 times, and from the second iteration a lookup that hits
        // on *identity*. `plan.loopvar` is the seventh, pre-built on the plan ([`ForPlan::loopvar`]).
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
        Ok(())
    }
}

/// `except VariableDoesNotExist` — the class test all three callers need.
///
/// Nothing else is caught anywhere: `Variable.resolve` has no handler of its
/// own, so a lookup that raises anything else reaches the node uncaught, which is
/// where Django's outer handler decides whether to substitute or to propagate.
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

/// `FilterExpression.resolve`'s failure arm (`except VariableDoesNotExist:`,
/// `engine/base.py:819-832`), as the
/// **three-way** decision it is.
///
/// A free function rather than a method because it holds no state: the two things
/// it reads are the context and the node, and everything it returns is owned.
///
/// `FilterExpression.resolve`'s `VariableDoesNotExist` handler has two arms and
/// only one of them is a substitution: a truthy `string_if_invalid` **returns**
/// (`if string_if_invalid: if "%s" in string_if_invalid: return ... % self.var;
/// return string_if_invalid`), so the filter chain never runs, while a falsy one
/// assigns `obj = string_if_invalid` and falls through to the chain. `""` is the
/// default setting, so the falling-through arm is the common one — and it is why
/// `{{ missing|default:"x" }}` is `"x"`. Collapsing the two would make the
/// default `string_if_invalid=""` skip `default`, which is the bug this arm
/// exists to not have.
///
/// The `UnicodeDecodeError` arm is not `resolve`'s at all but
/// `VariableNode.render`'s (`engine/base.py:1351-1354`): resolving a variable
/// directly is resolving *past* both handlers, so both are taken here.
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
        // `string_if_invalid % self.var`, and `self.var` is the `Variable`
        // object — `%s` on it calls `Variable.__str__`, which is the lookup text.
        // Read off the node rather than re-derived by joining `lookups`: it is
        // the operand Django uses, and it is the same object the expression
        // would have used had it not been planned natively.
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

/// Walk a plan and return the rendered `SafeString`.
#[pyfunction]
fn render_plan(py: Python<'_>, plan: &NativePlan, context: Py<PyAny>) -> PyResult<Py<PyAny>> {
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
        census: if census_on() {
            Some(Census::default())
        } else {
            None
        },
    };
    walk.run(&plan.slots, 0)?;
    if let Some(census) = walk.census.take() {
        census_merge(&mut CENSUS.lock().unwrap(), census);
    }
    // `SafeString` of a whole `str`, or of `"".join(pieces)` where the walk had
    // to spill — `NodeList.render`'s own expression (`engine/base.py:1199-1200`),
    // which is the thing this returns in place of. `SafeString` and not
    // `mark_safe`, for the two reasons on [`SAFE_STRING`].
    let out: Py<PyAny> = match walk.spill.take() {
        None => PyString::new(py, &walk.out).into_any().unbind(),
        Some(pieces) => PyString::new(py, "")
            .call_method1("join", (PyList::new(py, pieces)?,))?
            .unbind(),
    };
    Ok(SAFE_STRING
        .import(py, "django.utils.safestring", "SafeString")?
        .call1((out,))?
        .unbind())
}

/// `django_rust_templates.engine.base.render_value_in_context`, cached once.
///
/// The function itself, not a Rust reimplementation of it: it is Django's —
/// `template_localtime`, `localize`, `conditional_escape` — transcribed into
/// this package, and the transcription is byte-identical to stock's by
/// construction rather than by a test that has to keep pace with it. The walk
/// takes the same function's first arm inline ([`Walk::emit`]) and calls this
/// for everything that arm declines.
///
/// **Cached, and it is a per-render fetch that made it worth caching.** This was
/// a function, `escape_fn`, that did the import and the `getattr` on every call;
/// the walk then called it once per render, so the two together were ~1.2 µs of
/// fixed cost on every walked template — invisible in the loop rows, where a
/// per-iteration win dominates, and the whole of the `micro/text` row, which is
/// the render call. `PyOnceLock::import` resolves both once per process.
static RENDER_VALUE_IN_CONTEXT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn render_value_in_context(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    RENDER_VALUE_IN_CONTEXT.import(
        py,
        "django_rust_templates.engine.base",
        "render_value_in_context",
    )
}

/// `django.utils.safestring.SafeString`, cached once — and it is `SafeString`
/// rather than `mark_safe` deliberately, for two reasons that agree.
///
/// **It is the faithful one.** The expression this replaces is
/// `NodeList.render`'s, and that expression is `SafeString("".join([...]))` —
/// `mark_safe` is Django's *public* helper, not the constructor the node list
/// uses. `mark_safe` wraps `SafeString` in `keep_lazy_text`, and for a `str`
/// input the two return the same object.
///
/// **And it is 0.48 µs against 0.07.** Measured, 7×300 000: `mark_safe("")`
/// 0.484 µs, `SafeString("")` 0.072 µs. The wrapper is the whole of it, and on
/// a walked template it is paid once per render — which is why the empty-plan
/// `render_plan` was 0.656 µs, most of it this call.
static SAFE_STRING: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The four `Rc`s `PyTarget` shares, hoisted out of the render loop.
///
/// The arena and `pending` stay per-*call* in the port's contract (`PyTarget`
/// says so), so [`LookupScope::resolve`] resets both rather than reusing them.
/// Nothing observes the reset: `outcome_to_py` returns `clone_ref`'d values, not
/// handles into the arena, and it reads `pending` only for an outcome that
/// raised.
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
        context: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        self.reset();
        let root = PyTarget::new(
            context.clone().unbind(),
            &self.arena,
            &self.pending,
            &self.context,
        );
        outcome_to_py(
            py,
            resolve_lookup(lookups, Box::new(root)),
            &self.arena,
            &self.pending,
            context,
        )
    }
}

/// Parse, materialise, and build the `Template` — M3's end-to-end entry.
///
/// **This returns the `Template` rather than rendering it, and that is M6 Task
/// 4's change.** Django's `Template.__init__` sets `self.extra_data =
/// parser.extra_data` inside `compile_nodelist`'s `try`, right after
/// `parser.parse()` (`base.py:198-200`), and a shell that builds the `Template`
/// by `__new__` skips that line. Handing the object back lets
/// `engine/parser.py::template_for` own the whole attribute list in one place —
/// which is what its own docstring has always claimed — instead of a second
/// source-to-`Template` path existing for the shell to keep in step. The caller
/// renders it, which is also what `Engine.from_string` needs at M9.
///
/// **Two Python arguments, not one, and the second is a defect the plan did not
/// anticipate.** D5 settled that the `Parser` comes from the caller, because
/// materialising a `FilterExpression` calls `parser.find_filter`. It did not
/// settle how Rust reaches the *materialiser*, and the answer the plan assumed
/// is impossible: the shell lives in `django_rust_templates/engine`, whose
/// `__init__.py` imports its own siblings (`from .engine import Engine`), so an
/// `import django_rust_templates.engine.parser` issued from inside the extension
/// re-enters a package that is still initialising. Measured before M18, against a
/// package whose `__init__` did more work on the way, this failed as
/// `ModuleNotFoundError: No module named 'django_rust_templates.engine.engine'`.
/// The layout has changed since; the reason to avoid the import has not, and the
/// import is a hazard rather than an impossibility, which is worse to rely on.
/// So the shell hands its materialiser over, as one
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
/// `engine/parser.py`, which owns the attribute list.
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
    // The descriptor tree is **kept**, not consumed: `parse_source` returns the
    // wire tuple and drops the Rust tree, and the render walk plans against the
    // Rust tree paired with the `NodeList` materialised from it — which is the
    // only way to know, for each node, which class the materialiser built and
    // which attributes the parser gave it. `nodelist_desc_to_py` is the same
    // producer `parse_source` uses, so the wire shape still has one spelling.
    let desc_tree = match parse_nodelist(source, debug, origin_name, &host) {
        Ok(desc_tree) => desc_tree,
        Err(error) => return Err(parse_error(py, &error)?),
    };
    let desc = with_ref_table(py, nodelist_desc_to_py(py, &desc_tree)?, &host)?;
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
    // The render plan, built here because `compile_template` is the one place
    // that holds the descriptor tree *and* the `NodeList` built from it. It is
    // **optional by construction**: `plan_list` returns `None` the moment the
    // two disagree about a node's class, and `render_plan` is only reached from
    // `Template._render` when the template carries a plan — so a declined plan
    // falls back to the Python path with no branch in the render and no way for
    // a half-planned tree to render.
    let plan = match plan_list(py, &desc_tree, &nodelist)? {
        Some(slots) => Some(NativePlan { slots }.into_pyobject(py)?.into_any().unbind()),
        None => None,
    };
    // And the same hook one level down, for the bodies the walk cannot reach as
    // slots (see `plan_block_bodies`). Independent of the plan above: a template
    // whose *root* declines can still have a plannable block body, and one whose
    // root is planned usually has several. It is attached to the `NodeList`
    // objects themselves rather than returned, because that is the object
    // `BlockNode.render` calls and the object `{{ block.super }}` re-enters
    // through.
    plan_block_bodies(py, &desc_tree, &nodelist)?;
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
///
/// Gated on `not(extension-module)` because that is the switch that decides
/// whether libpython is linked: an extension module is loaded by an interpreter
/// that already has one, and a test binary has to start its own. Under the
/// default feature set this module does not exist and the crate's test target
/// stays empty, which is what keeps `cargo test --workspace` — and the gate that
/// runs it — unchanged.
///
/// Why the walk needs its own harness at all: **`MAX_WALK_DEPTH` has no route to
/// it through template text.** The plan is built by `plan_list`, which recurses
/// over the same nesting the parser did, and the parser recurses in Python — so
/// CPython's own limit raises `RecursionError` at about 200 levels of `{% for %}`
/// nesting (measured on the pin) long before a plan 501 deep exists. Every
/// Python-side test of the guard therefore tests the *parser's* limit and passes
/// whatever the guard does. The plan here is built by hand, which is the only way
/// to arrive at the boundary from the other side; the two rows are the same
/// construction at `MAX_WALK_DEPTH` and `MAX_WALK_DEPTH + 1`, so the row that
/// passes is a control over the population the row that raises is drawn from.
///
/// That the walk's recursion is *real* recursion is the other half, and it is
/// what the boundary row measures on the way past: 500 levels need 4–8 KiB of
/// stack each in a debug build and 0.5–1 KiB in a release one (probed in
/// `scripts/run_rust_unit_tests.sh`, which is where the 8 MiB of thread stack
/// this test needs is set — the default 2 MiB **stalls** rather than failing).
/// So `MAX_WALK_DEPTH` is a cap on *stack*, and 500 of them is a number inherited
/// from CPython's parser limit rather than derived from the walk's frame size.
/// It is reachable only once plans stop being built by the recursive Python
/// parser; that is the milestone at which the cap should be re-derived from the
/// smallest stack this might run on, and this test is the thing that will hold
/// the boundary while it is.
#[cfg(all(test, not(feature = "extension-module")))]
mod walk_depth {
    use super::*;

    /// `ForPlan.sequence`, which `run_for` calls `.resolve(context, true)` on.
    /// The walk's only Python call on the path under test — it is what makes the
    /// synthetic plan descend instead of taking the `{% empty %}` branch — and it
    /// answers with one item so every level is entered exactly once.
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

    /// Every slot in this plan is an `Act::For`, so the walk never reaches
    /// `emit`/`push_python` and the `escape` field below is never read.
    ///
    /// **The context, though, is read, and the first version of this fixture was
    /// wrong about that.** It passed `None`, on the argument that the plan holds
    /// no `Emit` and therefore nothing the walk would resolve *against* the
    /// context — true of the reads, and false of the loop: `run_for` asks the
    /// context whether a `forloop` is already there (the parent loop) and pushes
    /// the loop's own layer before resolving its sequence, because that is the
    /// order `ForNode.render` uses (`engine/defaulttags.py:198-199`). So the
    /// fixture answers exactly those calls and the write below, and nothing else,
    /// which keeps
    /// the engine's own imports out of the row while letting the recursion reach
    /// the guard on a path a real loop also walks.
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

        /// **The fourth call, and the deletion is what makes it necessary.** The
        /// walk writes the loop variable into the context once per iteration,
        /// unconditionally, in `ForNode.render`'s order
        /// (`engine/defaulttags.py:248`) — on the ordered pass a body this plan
        /// would have sealed wrote nothing at all, so the fixture could omit this
        /// and still let the recursion reach the guard. It is a no-op because the
        /// plan has no `Emit` and nothing reads the value back; what it buys is
        /// that the walk's own contract with the context is not narrower in the
        /// test than in the engine, which is exactly how this row failed
        /// (`TypeError: 'builtins.DepthContext' object does not support item
        /// assignment`) the first time the gate compiled it after the deletion.
        fn __setitem__(&self, _key: &Bound<'_, PyAny>, _value: &Bound<'_, PyAny>) {}
    }

    /// What `push` hands back. `with` calls `__exit__` on it, and so does
    /// `run_for`, on both the success and the error path; nothing else of it is
    /// ever touched.
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
            census: None,
        };
        walk.run(slots, 0)
    }

    #[test]
    fn the_depth_guard_raises_cpythons_error_at_the_cap_and_not_below_it() {
        Python::initialize();
        Python::attach(|py| {
            let sequence: Py<PyAny> = Py::new(py, OneItem).unwrap().into_any();
            // A plan exactly `MAX_WALK_DEPTH` loops deep is walked: the body of
            // loop *n* is run at depth *n*, so this is the deepest plan that
            // reaches the innermost `run` at the cap without passing it.
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
