//! The filter seam: what a filter body may ask the host (design spec §D2, §D7).
//!
//! §D2's rule is the whole content of this file. **Rust owns the filter and its
//! algorithm; the host owns the engines whose answer lives outside the
//! template** — `settings`, the active language, the clock, and the CPython and
//! Python-library behaviours Rust has no equivalent of (`str.title`'s `Cased`
//! property, `HTMLParser`, `textwrap.TextWrapper`, `json.dumps`, `unicodedata`).
//! A body that needs one of those calls a method here and gets an answer or a
//! Python exception's *class* back.
//!
//! **The trait is settled before the fan-out and this file is not edited
//! afterwards** (§10.4): the seven group tasks each own one filter module and
//! none of them owns this one, so an agent that finds an operation missing stops
//! and reports rather than adding it. That is what makes the 35 methods below a
//! contract rather than a suggestion.
//!
//! Three implementations exist, and they are three names rather than two
//! deliberately: `template-py`'s real one, [`NoFilterHost`] (refuses everything),
//! and [`TestFilterHost`] (answers what a test scripts). "No host at all" and "a
//! host that answers" fail a test in *opposite* directions — a body that wrongly
//! needs the host must fail a bare-shell unit test, and a body that is supposed
//! to need it must be caught by a test rather than by luck — so collapsing them
//! would let a wrong filter pass.

#[cfg(test)]
use std::cell::RefCell;

use crate::filters::value::Value;
use crate::parser::error::Error;
// Only the test-only `TestFilterHost` names an exception class; a real host
// reports one it caught rather than one it was handed.
#[cfg(test)]
use crate::parser::error::PyErrorKind;
use crate::pynum::IntValue;

/// What a filter body is handed besides its value and its argument.
///
/// Deliberately not the whole context: a filter that could read the context
/// would be a filter whose behaviour depends on it, and Django's 57 do not —
/// the two that look like exceptions take `context.use_tz` as a *field* here
/// instead (§D1), which is one bool rather than an escape hatch.
pub struct FilterCall<'a> {
    pub host: &'a dyn FilterHost,
    /// The `autoescape` in force, which the seven `needs_autoescape` filters
    /// branch on. Rust owns the branch; the host owns only the escaping it calls.
    pub autoescape: bool,
    /// The context's `use_tz`, for the two `expects_localtime` filters.
    pub use_tz: bool,
}

/// The arguments of `django.utils.formats.number_format`
/// (`formats.py:172`), which is keyword-called from exactly two filters in three
/// shapes: `defaultfilters.py:186`, `:214` and `:912`.
///
/// A struct rather than four positional parameters because
/// `clippy::type_complexity` has already bitten this project, and because the
/// call sites differ only in `use_l10n` and `force_grouping` — the two things
/// worth being able to name at a call site.
pub struct NumberFormatArgs<'a> {
    /// The value to format: an `int`, a `float`, or a **string** of digits.
    /// `floatformat`'s second call passes its own assembled decimal text
    /// (`:214` passes `number`, not `rounded_d`), so this cannot be an `f64`.
    pub value: &'a Value,
    /// Django's `decimal_pos`; `None` is its default.
    pub decimal_pos: Option<i64>,
    /// Django's `use_l10n`, which is **three-valued**: `None` means "localize",
    /// because `number_format` maps it to `True` itself (`formats.py:179-180`),
    /// while `Some(false)` means the template asked for `floatformat:…g`.
    /// `None` and `Some(true)` are behaviourally identical, so a host may
    /// collapse them — but the caller must not, since that would hide which
    /// branch `floatformat` took.
    pub use_l10n: Option<bool>,
    /// Django's `force_grouping`, `False` on every built-in call path.
    pub force_grouping: bool,
}

/// Everything a filter body can ask of Python.
///
/// The methods return `Result<_, Error>` where Django's call can raise, and a
/// bare value where it cannot. A body matches on `ErrorKind::FilterHost`'s
/// `kind` — the Python **class**, not a message — because that is what Django
/// catches: `first` catches `IndexError` and lets `TypeError` escape, `add`
/// catches bare `Exception`, `divisibleby` has no `try` at all. A seam that
/// flattened those to one error type would make three filters unimplementable.
pub trait FilterHost {
    // --- the Python value protocol -----------------------------------------
    /// `str(v)`. `@stringfilter`'s wrapper does this once per filter
    /// (`defaultfilters.py:44-48`), and `unordered_list` does it again per item.
    fn to_str(&self, value: &Value) -> Result<String, Error>;
    /// `int(v)` — `get_digit` (`:757-776`), `divisibleby` (`:854-858`), and the
    /// three padding filters (`:417-430`). Python's `int` accepts `" 5 "`, `"5_0"`
    /// and `"٠٥"`, and is arbitrary-precision, so this is not a parse.
    fn to_int(&self, value: &Value) -> Result<IntValue, Error>;
    /// `float(v)` — `pluralize` (`:942-982`) and `floatformat` (`:94-220`).
    fn to_float(&self, value: &Value) -> Result<f64, Error>;
    /// `bool(v)` — `default` (`:840-843`) and `yesno` (`:860-891`). For an
    /// `Opaque` this is `__bool__` or `__len__`, which Rust cannot see.
    fn is_true(&self, value: &Value) -> Result<bool, Error>;
    /// `len(v)` — `length` (`:633-639`), `pluralize`, `wordcount`'s fallback.
    fn len(&self, value: &Value) -> Result<usize, Error>;
    /// `list(v)`, **not** an index walk: `unordered_list`'s whole-value case is a
    /// generator (`test_unordered_list.py:141`), which has no `[]`. `first`/`last`
    /// are the mirror image — they are `value[0]`/`value[-1]` and must go through
    /// [`FilterHost::get_index`], because Django's `except IndexError` lets a
    /// generator's `TypeError` escape, and an `iter_values`-based implementation
    /// would return a value where Django raises.
    fn iter_values(&self, value: &Value) -> Result<Vec<Value>, Error>;
    /// `value[index]` — `first` (`:602-608`) and `last` (`:624-630`), including
    /// the negative index and the `IndexError`/`TypeError` split.
    fn get_index(&self, value: &Value, index: i64) -> Result<Value, Error>;
    /// `value[start:stop:step]` — `slice` (`:651-666`). Not expressible as
    /// [`FilterHost::get_index`] plus Rust slicing: `"abcdef"|slice:":3"` is
    /// `"abc"`, a *string*, where the index route yields a list. Every component
    /// is `Option` because `"::2"` and `":3"` are both legal and Django passes
    /// `None` straight through to `slice(*bits)`.
    fn get_slice(
        &self,
        value: &Value,
        start: Option<i64>,
        stop: Option<i64>,
        step: Option<i64>,
    ) -> Result<Value, Error>;
    /// `a + b` — `add` (`:745-754`). Int-plus-int, then sequence concatenation,
    /// then a bare `except Exception` → `""`; all three arms are reachable
    /// because `+` is `__add__`, which no `Value` variant can answer for.
    fn add_values(&self, a: &Value, b: &Value) -> Result<Value, Error>;
    /// `("%" + spec) % value` — `stringformat` (`:277-294`), inside a
    /// `try/except (ValueError, TypeError)` → `""`. The format string is Rust's
    /// to build; the operator is Python's.
    fn percent_format(&self, value: &Value, spec: &str) -> Result<Value, Error>;
    /// `isinstance(value, SafeData)` — the question `escape`, `force_escape`,
    /// `safeseq`, `cut` and `linebreaksbr` branch on, and the one a Rust
    /// `Value::Safe` cannot answer for an `Opaque` that carries `__html__`.
    fn is_safe(&self, value: &Value) -> bool;
    /// `mark_safe(value)` (`safestring.py:64-80`). Measured on the pinned tree:
    /// a value with `__html__` is returned **unchanged and not necessarily
    /// `SafeData`**; a non-str becomes `SafeString(str(v))`. There is no
    /// `SafeText` in 6.0.9 — it was removed in Django 4.0 — so a caller must not
    /// assume the answer is a `SafeString`.
    fn mark_safe(&self, value: Value) -> Value;

    // --- the escaping protocol: one call, four branches ---------------------
    /// `conditional_escape` (`django/utils/html.py:115-127`) — see §D4. One
    /// method, because two of its three branches are not `is_safe` questions:
    /// `isinstance(text, Promise)` and `hasattr(text, "__html__")`. Its own
    /// docstring says the convention is shared with `markupsafe`, which
    /// implements `__html__` **without being `SafeData`**, so a Rust
    /// `is_safe`-then-escape would get those objects wrong.
    fn conditional_escape(&self, value: &Value) -> Result<Value, Error>;

    // --- the one ordering operation Rust cannot express ---------------------
    /// `dictsort`/`dictsortreversed` (`:578-599`) together with
    /// `_property_resolver` (`:540-575`). One method, not two: the
    /// float-vs-string branch, the private-name `AttributeError`, the
    /// five-exception `__getitem__`→`getattr` fallback and `dictsort`'s own
    /// `(AttributeError, TypeError)` → `""` catch are a single semantic unit, and
    /// splitting them across the FFI would mean Rust reimplementing Python's
    /// exception classification — the thing this seam exists to avoid. Sorting
    /// arbitrary `Opaque` objects by an extracted key is not expressible in Rust
    /// at all.
    ///
    /// `prop` is `&Value` and not `&str` (§D7.1): `arg` is *not*
    /// `@stringfilter`-coerced here, and `float(arg)` sends `1` and `'1'` down
    /// different branches — `dictsort(x, 1)` is the list, `dictsort(x, '1')` is
    /// `""`. Coercing at the seam would silently pick one branch for both.
    fn sort_by_property(&self, value: &Value, prop: &Value, reverse: bool) -> Result<Value, Error>;

    // --- the one type question Rust cannot answer ---------------------------
    /// `unordered_list`'s nested-sequence test (`:695-706`). `Some(items)` when
    /// **both** halves hold — `isinstance(next_item, (list, tuple,
    /// types.GeneratorType))` *and* `iter(next_item)` does not raise — and `None`
    /// otherwise, meaning "a plain item".
    ///
    /// One method, not a predicate plus an iteration, by §D7.1's argument: Rust
    /// cannot answer the `isinstance` for an `Opaque` element (a tuple and a
    /// generator both arrive as `Opaque`; neither is a `Value::List`), and an
    /// `iter_values`-shaped call could not represent a generator without
    /// *consuming* it — which is the very branch being consulted. Splitting them
    /// would put Python's `TypeError` classification on the Rust side of the
    /// seam.
    ///
    /// Measured, not inferred: an implementation without this method gets one of
    /// the two suite rows wrong.
    /// `filter_tests/test_unordered_list.py:141` is
    /// `unordered_list(item_generator())` — the **whole value** is a generator,
    /// so [`FilterHost::iter_values`] must be `list(v)` and must not index — and
    /// `:150`'s `test_nested_generators` is a list whose **element** is a
    /// generator, which must nest rather than render flat.
    fn as_nested_sequence(&self, value: &Value) -> Result<Option<Vec<Value>>, Error>;

    // --- CPython string-method semantics ------------------------------------
    /// `str.lower()`. **Not a per-code-point table**, and that is the finding:
    /// this is the project's fifth Unicode surprise after `\w`, whitespace,
    /// `combining` and the case fixup, and the first that is not a table at all.
    /// Measured: `"ΟΣ".lower()` is `"ος"`, not `"οσ"` — Unicode's **Final_Sigma**
    /// rule, which is *context*-dependent, so `pyunicode::case`'s exhaustive
    /// per-code-point diff provably cannot see it. Rust's `str::to_lowercase`
    /// does implement the rule, but it would then disagree with Python at the 28
    /// suppressed code points, and the two halves cannot be combined without
    /// either replicating Final_Sigma (guardable only by sampling) or leaving a
    /// silent divergence on a string holding both a suppressed code point and a
    /// context-sensitive `Σ`. Delegating is complete by construction.
    fn str_lower(&self, text: &str) -> String;
    /// `str.title()`. Needs three things Rust has no equivalent of: Unicode's
    /// `Cased` property (4,578 code points, and `unicodedata` does not expose
    /// it), a **135-entry** titlecase mapping table distinct from uppercase, and
    /// the same Final_Sigma rule when lowercasing the rest of each run.
    /// Measured: a from-first-principles rule reproduced all 1,112,064 single
    /// code points and still got 444 of 400,000 random strings wrong, every one
    /// of them final sigma.
    fn str_title(&self, text: &str) -> String;

    // --- engines whose state is not the filter's argument -------------------
    /// `django.utils.html.strip_tags` — `HTMLParser`-based (`html.py:238-272`),
    /// so the tag grammar, the entity handling and the malformed-input behaviour
    /// are all Python's.
    fn strip_tags(&self, text: &str) -> String;
    /// `django.utils.text._slugify` (`text.py:473-476`), **whole**: NFKD, the
    /// combining-mark strip, the two `re.sub`s and the `.lower()`.
    ///
    /// Whole rather than a `normalize_nfkd` the body would wrap, because the
    /// lowercasing inside it is Final_Sigma — the same reason
    /// [`FilterHost::str_lower`] exists — and because a whole method is what
    /// leaves no uncalled sibling: with the body owning the regexes,
    /// `unicode_normalize_nfkd` would have had `slugify` as its only caller,
    /// which is the `repr` mistake one method along.
    fn slugify(&self, text: &str) -> String;
    /// `django.utils.html.urlize` — a regex over `settings.URLIZE_ASSUME_HTTPS`,
    /// so the setting and the regex engine are both the host's.
    fn urlize(&self, text: &str, limit: Option<usize>, autoescape: bool) -> String;
    /// `Truncator.chars` / `.words` (`text.py:193-261`), **whole**. Delegated
    /// rather than split because even the non-html path needs
    /// `unicodedata.normalize("NFC")`, `unicodedata.combining` per character
    /// (`:205`, `:220`) and the translatable marker `add_truncation_text` falls
    /// back to — so a Rust `chars` would need a **third** Unicode table
    /// diverging from CPython's, which is the M1 `\w` problem reintroduced.
    /// `truncate_text` is Django's `truncate` argument; `None` is its default.
    fn truncate(
        &self,
        text: &str,
        length: usize,
        words: bool,
        html: bool,
        truncate_text: Option<&str>,
    ) -> String;
    /// `django.utils.text.wrap` — `textwrap.TextWrapper`, whose break rules and
    /// whitespace handling are not Rust's.
    fn wrap(&self, text: &str, width: usize) -> String;
    /// `django.utils.encoding.iri_to_uri`. `iriencode`'s entire body is this one
    /// call (`:225-227`).
    fn iri_to_uri(&self, text: &str) -> String;
    /// `urllib.parse.quote` — `urlencode`'s body (`:365-379`), with the `safe`
    /// set Rust selects from `arg`/the default.
    fn quote(&self, text: &str, safe: &str) -> String;
    /// `json.dumps` — `json_script` (`:85-91`), whose separators and
    /// `ensure_ascii` are Django's `_json_script` choice.
    fn json_dumps(&self, value: &Value) -> Result<String, Error>;
    /// `pprint.pformat` — `pprint` (`:990-996`).
    fn pformat(&self, value: &Value) -> Result<String, Error>;
    /// `random.choice` — `random` (`:642-648`), which is stateful and not a
    /// function of its argument.
    fn random_choice(&self, value: &Value) -> Result<Value, Error>;

    // --- locale, language and the clock -------------------------------------
    /// `django.utils.timezone.template_localtime(value, use_tz=…)` — `date` and
    /// `time` (`expects_localtime`), so this is `settings.USE_TZ` and the active
    /// timezone.
    fn localtime(&self, value: Value, use_tz: bool) -> Result<Value, Error>;
    /// `django.utils.formats.date_format` — the active language's formats.
    fn date_format(&self, value: &Value, format: &str) -> Result<String, Error>;
    /// `django.utils.formats.time_format`, for the same reason.
    fn time_format(&self, value: &Value, format: &str) -> Result<String, Error>;
    /// `django.utils.timesince.timesince`, **with Django's own signature**.
    ///
    /// `timeuntil` is not `timesince` with the arguments swapped: Django's
    /// `timesince(d, now, reversed=False)` swaps `d` and `now` *after*
    /// normalising the timezones (`timesince.py:60-70`), so
    /// `now.astimezone(d.tzinfo)` expresses one operand in the other's zone and
    /// the calendar fields `total_months` reads (`:83`) are zone-dependent. A
    /// caller-side swap normalises the opposite way and reports different months
    /// for two aware datetimes in different zones.
    ///
    /// `now` is an `Option` for the same reason: `timeuntil_filter` passes
    /// `arg=None` straight through, and `now` is filled from `d.tzinfo` *before*
    /// the swap, so a swap performed by the caller would have to pass `d = None`
    /// and die at `:61` on `d.year`.
    ///
    /// `reversed` is therefore a parameter here rather than a property of the
    /// call site, because it is only expressible on Python's side of the seam.
    fn timesince(&self, d: &Value, now: Option<&Value>, reversed: bool) -> String;
    /// `django.utils.formats.number_format`, in the three shapes the two callers
    /// use. The struct is [`NumberFormatArgs`].
    fn number_format(&self, args: NumberFormatArgs<'_>) -> String;
    /// `gettext` — the active language, and `settings.USE_I18N`.
    fn gettext(&self, text: &str) -> String;
    /// `ngettext` — the same, with the plural form.
    fn ngettext(&self, singular: &str, plural: &str, n: u64) -> String;
    /// `django.utils.timezone.now`, for `timeuntil`'s default argument.
    fn now(&self) -> Result<Value, Error>;
}

/// The refusal [`NoFilterHost`] gives, and the one [`TestFilterHost`] gives for an
/// operation it was not scripted for.
///
/// One string produced in one place, because the two hosts differ only in *which*
/// operations they can answer: a test that swapped one for the other must not be
/// able to tell the difference from the message, or the message is being read as
/// a signal it does not carry.
fn refused(operation: &'static str) -> Error {
    Error::host(format!("no host answers `{operation}`"))
}

/// The panic the 15 non-`Result` operations give when nobody answers them.
///
/// Those methods cannot return the refusal as a value, so the refusal is the
/// panic's message — and it is the same text [`refused`] produces, so "the host
/// refused" reads the same whichever shape the operation has.
fn refused_panic(operation: &'static str) -> ! {
    panic!("{}", refused(operation).message())
}

/// A host that answers nothing: the one a filter module's bare-shell unit test
/// uses when its filter must be pure.
///
/// Every operation refuses, so **a filter that needs the host cannot pass a
/// `cargo test -p template-core` test by accident** — the test fails with the
/// operation's name in it, which is the signal that the body belongs on the other
/// side of §D2's line or that the test needs a [`TestFilterHost`].
pub struct NoFilterHost;

impl FilterHost for NoFilterHost {
    fn to_str(&self, _value: &Value) -> Result<String, Error> {
        Err(refused("to_str"))
    }

    fn to_int(&self, _value: &Value) -> Result<IntValue, Error> {
        Err(refused("to_int"))
    }

    fn to_float(&self, _value: &Value) -> Result<f64, Error> {
        Err(refused("to_float"))
    }

    fn is_true(&self, _value: &Value) -> Result<bool, Error> {
        Err(refused("is_true"))
    }

    fn len(&self, _value: &Value) -> Result<usize, Error> {
        Err(refused("len"))
    }

    fn iter_values(&self, _value: &Value) -> Result<Vec<Value>, Error> {
        Err(refused("iter_values"))
    }

    fn get_index(&self, _value: &Value, _index: i64) -> Result<Value, Error> {
        Err(refused("get_index"))
    }

    fn get_slice(
        &self,
        _value: &Value,
        _start: Option<i64>,
        _stop: Option<i64>,
        _step: Option<i64>,
    ) -> Result<Value, Error> {
        Err(refused("get_slice"))
    }

    fn add_values(&self, _a: &Value, _b: &Value) -> Result<Value, Error> {
        Err(refused("add_values"))
    }

    fn percent_format(&self, _value: &Value, _spec: &str) -> Result<Value, Error> {
        Err(refused("percent_format"))
    }

    fn is_safe(&self, _value: &Value) -> bool {
        refused_panic("is_safe")
    }

    fn mark_safe(&self, _value: Value) -> Value {
        refused_panic("mark_safe")
    }

    fn conditional_escape(&self, _value: &Value) -> Result<Value, Error> {
        Err(refused("conditional_escape"))
    }

    fn sort_by_property(
        &self,
        _value: &Value,
        _prop: &Value,
        _reverse: bool,
    ) -> Result<Value, Error> {
        Err(refused("sort_by_property"))
    }

    fn as_nested_sequence(&self, _value: &Value) -> Result<Option<Vec<Value>>, Error> {
        Err(refused("as_nested_sequence"))
    }

    fn str_lower(&self, _text: &str) -> String {
        refused_panic("str_lower")
    }

    fn str_title(&self, _text: &str) -> String {
        refused_panic("str_title")
    }

    fn strip_tags(&self, _text: &str) -> String {
        refused_panic("strip_tags")
    }

    fn slugify(&self, _text: &str) -> String {
        refused_panic("slugify")
    }

    fn urlize(&self, _text: &str, _limit: Option<usize>, _autoescape: bool) -> String {
        refused_panic("urlize")
    }

    fn truncate(
        &self,
        _text: &str,
        _length: usize,
        _words: bool,
        _html: bool,
        _truncate_text: Option<&str>,
    ) -> String {
        refused_panic("truncate")
    }

    fn wrap(&self, _text: &str, _width: usize) -> String {
        refused_panic("wrap")
    }

    fn iri_to_uri(&self, _text: &str) -> String {
        refused_panic("iri_to_uri")
    }

    fn quote(&self, _text: &str, _safe: &str) -> String {
        refused_panic("quote")
    }

    fn json_dumps(&self, _value: &Value) -> Result<String, Error> {
        Err(refused("json_dumps"))
    }

    fn pformat(&self, _value: &Value) -> Result<String, Error> {
        Err(refused("pformat"))
    }

    fn random_choice(&self, _value: &Value) -> Result<Value, Error> {
        Err(refused("random_choice"))
    }

    fn localtime(&self, _value: Value, _use_tz: bool) -> Result<Value, Error> {
        Err(refused("localtime"))
    }

    fn date_format(&self, _value: &Value, _format: &str) -> Result<String, Error> {
        Err(refused("date_format"))
    }

    fn time_format(&self, _value: &Value, _format: &str) -> Result<String, Error> {
        Err(refused("time_format"))
    }

    fn timesince(&self, _d: &Value, _now: Option<&Value>, _reversed: bool) -> String {
        refused_panic("timesince")
    }

    fn number_format(&self, _args: NumberFormatArgs<'_>) -> String {
        refused_panic("number_format")
    }

    fn gettext(&self, _text: &str) -> String {
        refused_panic("gettext")
    }

    fn ngettext(&self, _singular: &str, _plural: &str, _n: u64) -> String {
        refused_panic("ngettext")
    }

    fn now(&self) -> Result<Value, Error> {
        Err(refused("now"))
    }
}

/// A scripted host for a filter module's own `#[cfg(test)] mod tests`.
///
/// Every operation answers from its own `Option` field when set, and otherwise
/// refuses exactly as [`NoFilterHost`] does — so **a test cannot pass by leaving
/// a field unset**, which is the failure a host with permissive defaults would
/// hide. Every call is recorded, so a test can assert the body *reached* the seam
/// rather than asserting a value the body could have produced on its own. That
/// second half is the half §11 needs: a delegating filter whose test never checks
/// [`TestFilterHost::calls`] can be reimplemented in Rust and still pass.
///
/// The field order is the trait's order, so a reader can walk the two together.
/// Non-`Result` operations (the 15 that return a bare `String`, `bool` or
/// `Value`) panic when unset rather than defaulting, for the same reason: a
/// sentinel could silently satisfy an assertion.
///
/// `#[cfg(test)]`, because the only thing that needs it is a test: this crate's
/// `cargo test` compiles every `#[cfg(test)] mod tests` in `filters/<name>.rs`
/// against the same crate, so it is visible everywhere an agent's filter tests
/// live. Shipping it in the library would put 35 `Option` fields and two
/// `RefCell`s in the real build, reachable from a non-test path with nothing to
/// stop it. The one cost is that a *doc* test cannot demonstrate it — doc tests
/// link against the crate compiled normally — so its demonstrations live in the
/// `mod tests` below rather than in a fenced example.
#[cfg(test)]
#[derive(Default)]
pub struct TestFilterHost {
    // Crate-visible rather than private, and the reason is a Rust rule worth
    // stating: functional-update syntax (`..TestFilterHost::default()`) requires
    // *every* field to be visible at the construction site, so two private fields
    // here would stop a filter module's test from scripting any of the 35 answers
    // — the struct's whole purpose. `pub(crate)` is the narrowest visibility that
    // restores it, and it costs nothing in this struct's case, since the struct is
    // itself `#[cfg(test)]` and so unconstructible from outside the crate anyway.
    //
    // Reading a recorded call goes through [`TestFilterHost::calls`] and
    // [`TestFilterHost::assert_called`]; scripting a failure, through
    // [`TestFilterHost::raise_on`]. Those three are the API a filter test wants,
    // and reaching past them to these `RefCell`s is possible here where it was
    // not before — the trade this struct's first real users forced, and one whose
    // worst case is a test that fabricates the evidence it then asserts on, which
    // a reviewer sees in the diff.
    pub(crate) calls: RefCell<Vec<&'static str>>,
    pub(crate) raises: RefCell<Vec<(&'static str, PyErrorKind)>>,

    pub to_str: Option<String>,
    pub to_int: Option<IntValue>,
    pub to_float: Option<f64>,
    pub is_true: Option<bool>,
    pub len: Option<usize>,
    pub iter_values: Option<Vec<Value>>,
    pub get_index: Option<Value>,
    pub get_slice: Option<Value>,
    pub add_values: Option<Value>,
    pub percent_format: Option<Value>,
    pub is_safe: Option<bool>,
    pub mark_safe: Option<Value>,
    pub conditional_escape: Option<Value>,
    pub sort_by_property: Option<Value>,
    /// `Some(None)` is a **scripted** "plain item"; `None` is "not scripted".
    /// The double `Option` is the trait's own shape —
    /// `Result<Option<Vec<Value>>, Error>` — and the outer layer here is the
    /// scripted/unscripted question, not a third meaning.
    pub as_nested_sequence: Option<Option<Vec<Value>>>,
    pub str_lower: Option<String>,
    pub str_title: Option<String>,
    pub strip_tags: Option<String>,
    pub slugify: Option<String>,
    pub urlize: Option<String>,
    pub truncate: Option<String>,
    pub wrap: Option<String>,
    pub iri_to_uri: Option<String>,
    pub quote: Option<String>,
    pub json_dumps: Option<String>,
    pub pformat: Option<String>,
    pub random_choice: Option<Value>,
    pub localtime: Option<Value>,
    pub date_format: Option<String>,
    pub time_format: Option<String>,
    pub timesince: Option<String>,
    pub number_format: Option<String>,
    pub gettext: Option<String>,
    pub ngettext: Option<String>,
    pub now: Option<Value>,
}

#[cfg(test)]
impl TestFilterHost {
    /// The operation names this host was asked for, in order.
    pub fn calls(&self) -> Vec<&'static str> {
        self.calls.borrow().clone()
    }

    /// Panics naming `name` if it is absent, and prints the calls it did see.
    ///
    /// This is the half of a delegating filter's test that a Rust
    /// reimplementation cannot satisfy, so a body that stops calling the host
    /// fails here even when its output is still right.
    pub fn assert_called(&self, name: &str) {
        let calls = self.calls();
        assert!(
            calls.contains(&name),
            "the body never reached `{name}`; it called {calls:?}"
        );
    }

    /// Make `operation` raise `kind` for this host's remaining life.
    ///
    /// The taxonomy is the point of `ErrorKind::FilterHost`, and the filters that
    /// depend on it — `first`'s `IndexError` → `""` against its escaping
    /// `TypeError`, `add`'s bare `except Exception`, `get_digit`'s `ValueError` →
    /// the original value, `divisibleby`'s escaping `ZeroDivisionError` — are
    /// Rust-side branches that no host-free test can reach. Scripting the class
    /// is what makes them testable in a bare shell.
    pub fn raise_on(&self, operation: &'static str, kind: PyErrorKind) {
        self.raises.borrow_mut().push((operation, kind));
    }

    /// Records the call and returns the scripted failure, if any.
    ///
    /// **The push is first, deliberately.** A recording written the other way
    /// round — read the answer, then record — would skip the record on the path
    /// that refuses, so a test asserting "the body reached the seam" would pass
    /// or fail depending on what the body did with the answer. Step 2 of Task 2
    /// observes this by moving the push below the read.
    fn record(&self, operation: &'static str) -> Result<(), Error> {
        self.calls.borrow_mut().push(operation);
        match self
            .raises
            .borrow()
            .iter()
            .find(|(name, _)| *name == operation)
        {
            Some((_, kind)) => Err(Error::filter_host(
                kind.clone(),
                operation,
                format!("scripted {}", kind.name()),
            )),
            None => Ok(()),
        }
    }

    /// [`TestFilterHost::record`] for the operations that cannot return it.
    fn record_plain(&self, operation: &'static str) {
        if let Err(error) = self.record(operation) {
            panic!(
                "TestFilterHost::{} was scripted to raise: {}",
                operation,
                error.message()
            );
        }
    }
}

#[cfg(test)]
impl FilterHost for TestFilterHost {
    fn to_str(&self, _value: &Value) -> Result<String, Error> {
        self.record("to_str")?;
        self.to_str.clone().ok_or_else(|| refused("to_str"))
    }

    fn to_int(&self, _value: &Value) -> Result<IntValue, Error> {
        self.record("to_int")?;
        self.to_int.clone().ok_or_else(|| refused("to_int"))
    }

    fn to_float(&self, _value: &Value) -> Result<f64, Error> {
        self.record("to_float")?;
        self.to_float.ok_or_else(|| refused("to_float"))
    }

    fn is_true(&self, _value: &Value) -> Result<bool, Error> {
        self.record("is_true")?;
        self.is_true.ok_or_else(|| refused("is_true"))
    }

    fn len(&self, _value: &Value) -> Result<usize, Error> {
        self.record("len")?;
        self.len.ok_or_else(|| refused("len"))
    }

    fn iter_values(&self, _value: &Value) -> Result<Vec<Value>, Error> {
        self.record("iter_values")?;
        self.iter_values
            .clone()
            .ok_or_else(|| refused("iter_values"))
    }

    fn get_index(&self, _value: &Value, _index: i64) -> Result<Value, Error> {
        self.record("get_index")?;
        self.get_index.clone().ok_or_else(|| refused("get_index"))
    }

    fn get_slice(
        &self,
        _value: &Value,
        _start: Option<i64>,
        _stop: Option<i64>,
        _step: Option<i64>,
    ) -> Result<Value, Error> {
        self.record("get_slice")?;
        self.get_slice.clone().ok_or_else(|| refused("get_slice"))
    }

    fn add_values(&self, _a: &Value, _b: &Value) -> Result<Value, Error> {
        self.record("add_values")?;
        self.add_values.clone().ok_or_else(|| refused("add_values"))
    }

    fn percent_format(&self, _value: &Value, _spec: &str) -> Result<Value, Error> {
        self.record("percent_format")?;
        self.percent_format
            .clone()
            .ok_or_else(|| refused("percent_format"))
    }

    fn is_safe(&self, _value: &Value) -> bool {
        self.record_plain("is_safe");
        self.is_safe.unwrap_or_else(|| refused_panic("is_safe"))
    }

    fn mark_safe(&self, _value: Value) -> Value {
        self.record_plain("mark_safe");
        self.mark_safe
            .clone()
            .unwrap_or_else(|| refused_panic("mark_safe"))
    }

    fn conditional_escape(&self, _value: &Value) -> Result<Value, Error> {
        self.record("conditional_escape")?;
        self.conditional_escape
            .clone()
            .ok_or_else(|| refused("conditional_escape"))
    }

    fn sort_by_property(
        &self,
        _value: &Value,
        _prop: &Value,
        _reverse: bool,
    ) -> Result<Value, Error> {
        self.record("sort_by_property")?;
        self.sort_by_property
            .clone()
            .ok_or_else(|| refused("sort_by_property"))
    }

    fn as_nested_sequence(&self, _value: &Value) -> Result<Option<Vec<Value>>, Error> {
        self.record("as_nested_sequence")?;
        self.as_nested_sequence
            .clone()
            .ok_or_else(|| refused("as_nested_sequence"))
    }

    fn str_lower(&self, _text: &str) -> String {
        self.record_plain("str_lower");
        self.str_lower
            .clone()
            .unwrap_or_else(|| refused_panic("str_lower"))
    }

    fn str_title(&self, _text: &str) -> String {
        self.record_plain("str_title");
        self.str_title
            .clone()
            .unwrap_or_else(|| refused_panic("str_title"))
    }

    fn strip_tags(&self, _text: &str) -> String {
        self.record_plain("strip_tags");
        self.strip_tags
            .clone()
            .unwrap_or_else(|| refused_panic("strip_tags"))
    }

    fn slugify(&self, _text: &str) -> String {
        self.record_plain("slugify");
        self.slugify
            .clone()
            .unwrap_or_else(|| refused_panic("slugify"))
    }

    fn urlize(&self, _text: &str, _limit: Option<usize>, _autoescape: bool) -> String {
        self.record_plain("urlize");
        self.urlize
            .clone()
            .unwrap_or_else(|| refused_panic("urlize"))
    }

    fn truncate(
        &self,
        _text: &str,
        _length: usize,
        _words: bool,
        _html: bool,
        _truncate_text: Option<&str>,
    ) -> String {
        self.record_plain("truncate");
        self.truncate
            .clone()
            .unwrap_or_else(|| refused_panic("truncate"))
    }

    fn wrap(&self, _text: &str, _width: usize) -> String {
        self.record_plain("wrap");
        self.wrap.clone().unwrap_or_else(|| refused_panic("wrap"))
    }

    fn iri_to_uri(&self, _text: &str) -> String {
        self.record_plain("iri_to_uri");
        self.iri_to_uri
            .clone()
            .unwrap_or_else(|| refused_panic("iri_to_uri"))
    }

    fn quote(&self, _text: &str, _safe: &str) -> String {
        self.record_plain("quote");
        self.quote.clone().unwrap_or_else(|| refused_panic("quote"))
    }

    fn json_dumps(&self, _value: &Value) -> Result<String, Error> {
        self.record("json_dumps")?;
        self.json_dumps.clone().ok_or_else(|| refused("json_dumps"))
    }

    fn pformat(&self, _value: &Value) -> Result<String, Error> {
        self.record("pformat")?;
        self.pformat.clone().ok_or_else(|| refused("pformat"))
    }

    fn random_choice(&self, _value: &Value) -> Result<Value, Error> {
        self.record("random_choice")?;
        self.random_choice
            .clone()
            .ok_or_else(|| refused("random_choice"))
    }

    fn localtime(&self, _value: Value, _use_tz: bool) -> Result<Value, Error> {
        self.record("localtime")?;
        self.localtime.clone().ok_or_else(|| refused("localtime"))
    }

    fn date_format(&self, _value: &Value, _format: &str) -> Result<String, Error> {
        self.record("date_format")?;
        self.date_format
            .clone()
            .ok_or_else(|| refused("date_format"))
    }

    fn time_format(&self, _value: &Value, _format: &str) -> Result<String, Error> {
        self.record("time_format")?;
        self.time_format
            .clone()
            .ok_or_else(|| refused("time_format"))
    }

    fn timesince(&self, _d: &Value, _now: Option<&Value>, _reversed: bool) -> String {
        self.record_plain("timesince");
        self.timesince
            .clone()
            .unwrap_or_else(|| refused_panic("timesince"))
    }

    fn number_format(&self, _args: NumberFormatArgs<'_>) -> String {
        self.record_plain("number_format");
        self.number_format
            .clone()
            .unwrap_or_else(|| refused_panic("number_format"))
    }

    fn gettext(&self, _text: &str) -> String {
        self.record_plain("gettext");
        self.gettext
            .clone()
            .unwrap_or_else(|| refused_panic("gettext"))
    }

    fn ngettext(&self, _singular: &str, _plural: &str, _n: u64) -> String {
        self.record_plain("ngettext");
        self.ngettext
            .clone()
            .unwrap_or_else(|| refused_panic("ngettext"))
    }

    fn now(&self) -> Result<Value, Error> {
        self.record("now")?;
        self.now.clone().ok_or_else(|| refused("now"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::{ErrorKind, FilterHostError};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    /// The guard for the "unset means refuse" property, on the `Result` half.
    #[test]
    fn unset_result_operations_refuse_rather_than_default() {
        let host = TestFilterHost::default();
        let host = &host;
        let v = Value::Int(1);

        for (operation, error) in [
            ("to_str", host.to_str(&v).unwrap_err()),
            ("to_int", host.to_int(&v).unwrap_err()),
            ("to_float", host.to_float(&v).unwrap_err()),
            ("is_true", host.is_true(&v).unwrap_err()),
            ("len", host.len(&v).unwrap_err()),
            ("iter_values", host.iter_values(&v).unwrap_err()),
            ("get_index", host.get_index(&v, 0).unwrap_err()),
        ] {
            assert_eq!(
                error.kind,
                ErrorKind::Host(format!("no host answers `{operation}`")),
                "an unset `{operation}` must refuse with `Error::host`, not \
                 default to a value"
            );
        }
    }

    /// The same property on the non-`Result` half, where the refusal cannot be a
    /// value and so is a panic. Observed by catching it: the message names the
    /// operation, which is what makes a bare-shell failure diagnosable.
    #[test]
    fn unset_plain_operations_refuse_loudly() {
        let host = TestFilterHost::default();
        let text = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| host.str_lower("x")))
            .unwrap_err();
        let message = text
            .downcast_ref::<String>()
            .expect("panic payload is a formatted string")
            .clone();
        assert!(
            message.contains("str_lower"),
            "the panic must name the operation: {message}"
        );
        assert!(
            message.contains("no host answers"),
            "the panic must be the same refusal `NoFilterHost` gives: {message}"
        );
    }

    /// Every `Result` operation of `NoFilterHost` refuses, and refuses under its
    /// own name.
    ///
    /// The whole 20 rather than a sample: a single method that accidentally
    /// returned `Ok` would be a filter body silently reaching a default, and the
    /// per-method name is what makes a wrong dispatch visible.
    #[test]
    fn no_filter_host_refuses_every_result_operation() {
        let host = &NoFilterHost;
        let v = Value::None;
        let names = [
            host.to_str(&v).map(|_| "to_str").unwrap_err(),
            host.to_int(&v).map(|_| "to_int").unwrap_err(),
            host.to_float(&v).map(|_| "to_float").unwrap_err(),
            host.is_true(&v).map(|_| "is_true").unwrap_err(),
            host.len(&v).map(|_| "len").unwrap_err(),
            host.iter_values(&v).map(|_| "iter_values").unwrap_err(),
            host.get_index(&v, 0).map(|_| "get_index").unwrap_err(),
            host.get_slice(&v, None, None, None)
                .map(|_| "get_slice")
                .unwrap_err(),
            host.add_values(&v, &v).map(|_| "add_values").unwrap_err(),
            host.percent_format(&v, "s")
                .map(|_| "percent_format")
                .unwrap_err(),
            host.conditional_escape(&v)
                .map(|_| "conditional_escape")
                .unwrap_err(),
            host.sort_by_property(&v, &v, false)
                .map(|_| "sort_by_property")
                .unwrap_err(),
            host.as_nested_sequence(&v)
                .map(|_| "as_nested_sequence")
                .unwrap_err(),
            host.json_dumps(&v).map(|_| "json_dumps").unwrap_err(),
            host.pformat(&v).map(|_| "pformat").unwrap_err(),
            host.random_choice(&v).map(|_| "random_choice").unwrap_err(),
            host.localtime(v.clone(), false)
                .map(|_| "localtime")
                .unwrap_err(),
            host.date_format(&v, "Y")
                .map(|_| "date_format")
                .unwrap_err(),
            host.time_format(&v, "H")
                .map(|_| "time_format")
                .unwrap_err(),
            host.now().map(|_| "now").unwrap_err(),
        ];
        assert_eq!(
            names.len(),
            20,
            "the Result half of the trait is 20 methods"
        );
        for error in names {
            match error.kind {
                ErrorKind::Host(message) => {
                    assert!(message.starts_with("no host answers `"), "{message}");
                }
                other => panic!("NoFilterHost must refuse with ErrorKind::Host, got {other:?}"),
            }
        }
    }

    /// The other two property: a scripted answer comes back, and a scripted
    /// *failure* comes back as its Python class rather than as a message.
    #[test]
    fn a_scripted_answer_and_a_scripted_failure_both_come_back() {
        let host = TestFilterHost {
            str_lower: Some("ος".to_string()),
            ..Default::default()
        };
        assert_eq!(host.str_lower("ΟΣ"), "ος");
        host.assert_called("str_lower");

        // A value *is* scripted, so this asserts the failure wins over it: a
        // host that returned the answer would make `get_digit`'s
        // `except ValueError` branch unreachable from a test.
        let host2 = TestFilterHost {
            to_int: Some(IntValue::Small(3)),
            ..Default::default()
        };
        host2.raise_on("to_int", PyErrorKind::ValueError);
        let error = host2.to_int(&Value::Str("x".into())).unwrap_err();
        assert_eq!(
            error.kind,
            ErrorKind::FilterHost(Box::new(FilterHostError {
                kind: PyErrorKind::ValueError,
                message: "scripted ValueError".to_string(),
                operation: "to_int",
            }))
        );
    }

    /// **The `calls()` property, and the test that fails if the push moves.**
    ///
    /// Move the `push` in `record` below the `ok_or_else` and this fails: the
    /// refusal path takes the `?` before anything is recorded, so `calls()` is
    /// empty and a test asserting the body reached the seam would be asserting
    /// nothing.
    #[test]
    fn a_refused_call_is_still_recorded() {
        let host = TestFilterHost::default();
        assert!(host.to_str(&Value::None).is_err());
        assert_eq!(
            host.calls(),
            vec!["to_str"],
            "the call must be recorded on the refusing path too"
        );
    }

    /// Order is part of the record: a body that calls the seam twice in the wrong
    /// order is a different bug from one that calls it once.
    #[test]
    fn calls_are_recorded_in_order() {
        let host = TestFilterHost {
            to_str: Some("7".to_string()),
            iter_values: Some(vec![Value::Int(1)]),
            ..Default::default()
        };
        host.to_str(&Value::Int(7)).unwrap();
        host.iter_values(&Value::Int(7)).unwrap();
        host.to_str(&Value::Int(7)).unwrap();
        assert_eq!(host.calls(), vec!["to_str", "iter_values", "to_str"]);
    }

    /// `assert_called` is the guard a Rust reimplementation cannot satisfy, so it
    /// has to fail when it should — observed, not assumed.
    #[test]
    fn assert_called_names_what_it_saw() {
        let host = TestFilterHost::default();
        // Recorded through a *refusing* operation, which also exercises the
        // recording path a Rust reimplementation would silently drop.
        assert!(host.to_int(&Value::None).is_err());
        let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            host.assert_called("truncate")
        }))
        .unwrap_err();
        let message = panic.downcast_ref::<String>().unwrap().clone();
        assert!(message.contains("never reached `truncate`"), "{message}");
        assert!(
            message.contains("to_int"),
            "it must print the calls it did see: {message}"
        );
    }

    /// Both hosts describe themselves the same way, so a test cannot tell which
    /// one refused from the message alone.
    #[test]
    fn the_two_hosts_refuse_in_the_same_words() {
        let scripted = TestFilterHost::default().to_str(&Value::None).unwrap_err();
        let absent = NoFilterHost.to_str(&Value::None).unwrap_err();
        assert_eq!(scripted.kind, absent.kind);
        assert_eq!(
            scripted.kind,
            ErrorKind::Host("no host answers `to_str`".to_string())
        );
    }

    /// `FilterCall` is the only thing a body is handed, and §D1 says it is one
    /// bool rather than the context. Asserted so that a later milestone adding
    /// the context here has to delete a test that says why it should not.
    #[test]
    fn filter_call_carries_two_bools_and_the_host() {
        let host = TestFilterHost::default();
        let call = call(&host);
        assert!(call.autoescape);
        assert!(!call.use_tz);
        // The host is reachable through the call, which is how a body uses it.
        assert!(call.host.to_str(&Value::None).is_err());
    }
}
