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
//! and reports rather than adding it. That is what makes the 41 methods below a
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

/// A nested container, as `walk_items` needs it: the materialised items **and**
/// Django's truthiness of the *original object*.
///
/// The two are different questions, and the difference is the whole reason this
/// is a struct rather than a bare `Vec`. Django's `list_formatter` tests
/// `if children:` on the **container object** — a generator object is always
/// truthy, even one that yields nothing, where a list or tuple is truthy exactly
/// when it is non-empty. So `unordered_list(["a", empty_generator()])` builds a
/// `<ul>` around nothing (measured `"\t<li>a\n\t<ul>\n\n\t</ul>\n\t</li>"`)
/// while `["a", []]` gets no `<ul>` at all — and once the generator has been
/// materialised to a `Vec<Value>` the two are indistinguishable. `truthy`
/// carries the answer the object gave, so the Rust body can mirror `if children:`
/// instead of approximating it with `!items.is_empty()`.
#[derive(Clone, PartialEq, Debug)]
pub struct NestedSequence {
    /// The container's items, materialised — `list(item)`, which is also
    /// Django's `iter(item)` half of the nesting test.
    pub items: Vec<Value>,
    /// Django's `if children:`, evaluated on the **original object**.
    pub truthy: bool,
}

impl NestedSequence {
    /// A **sized** container: truthy exactly when it holds something, which is
    /// how a `list` or `tuple` answers `if children:`. A generator's answer does
    /// not follow its items, so that case is written out in full instead.
    pub fn sized(items: Vec<Value>) -> Self {
        let truthy = !items.is_empty();
        Self { items, truthy }
    }
}

/// A `Decimal`, as [`FilterHost::decimal_parse`] hands it back: the parts Rust
/// needs in order to decide and to assemble, plus a handle for the call that
/// rounds it.
///
/// The handle is a [`Value::Opaque`] rather than a second `str(value)` through
/// the seam, because the source parses `str(text)` **once** (`:153-154`) and
/// every later use is the same object — a re-parse would be a second evaluation
/// of a source-level expression.
#[derive(Clone, PartialEq, Debug)]
pub struct ParsedDecimal {
    /// The `Decimal` itself, as an `Opaque`, for
    /// [`FilterHost::decimal_quantize`].
    pub value: Value,
    /// `str(text)` (`:153`) — the payload of all three `return input_val` arms
    /// (`:163`, `:177`, `:182`). A plain `str`, and **not** safe.
    pub input_val: String,
    /// `d.as_tuple()`'s sign.
    pub sign: bool,
    /// `d.as_tuple()`'s digits, most-significant first, so `digits.len()` is the
    /// source's `len(tupl[1])` at `:197`.
    pub digits: Vec<u8>,
    /// `d.as_tuple()`'s exponent, or `None` for the `"F"`/`"n"`/`"N"` a
    /// non-finite `Decimal` reports.
    ///
    /// **`None` is two source arms at once, and both mean `return input_val`**:
    /// it is the `TypeError` the 200-digit sum catches (`:168-170`) *and* the
    /// reason `int(d)` raises at `:180` (`OverflowError` for an infinity,
    /// `ValueError` or `InvalidOperation` for a NaN). A body tests this once and
    /// never reads [`ParsedDecimal::integral`], which is meaningless for a
    /// non-finite.
    pub exponent: Option<i64>,
    /// `not (int(d) - d)` — the source's `m` at `:180` — and `false` when that
    /// raised, which is exactly when `exponent` is `None`.
    ///
    /// **This is the *integral-value* test, not `exponent < 0`.** A whole number
    /// with a negative exponent (`Decimal("1.0")`) has a falsy `m`, which is how
    /// `floatformat(1.0, -1)` reaches the `not m and p <= 0` integer branch
    /// (measured `"1"`) where an exponent test would take the quantize path.
    pub integral: bool,
}

/// [`FilterHost::decimal_parse`]'s answer: the parse, or the `return ""` its
/// double failure produces (`:158-159`).
#[derive(Clone, PartialEq, Debug)]
pub enum DecimalParse {
    Parsed(ParsedDecimal),
    /// `Decimal(input_val)` and the `Decimal(str(float(text)))` retry both
    /// raised — measured, `floatformat("abc")` and `floatformat(None)` are both
    /// `""`.
    Unparseable,
}

/// `Decimal.as_tuple()` **after** the quantization, for the Rust-side assembly
/// (`:205-212`).
#[derive(Clone, PartialEq, Debug)]
pub struct DecimalTuple {
    pub sign: bool,
    /// The **digit values**, not a count: `:206` is
    /// `[str(digit) for digit in reversed(digits)]`, so the assembly needs the
    /// digits themselves where the parse side needs only `len`.
    pub digits: Vec<u8>,
    /// Always an integer: `quantize` cannot produce a non-finite, and the source
    /// reads `abs(exponent)` to pad (`:207`).
    pub exponent: i64,
    /// `bool(rounded_d)` — the second half of `:210`'s `if sign and rounded_d`.
    ///
    /// **A separate question from [`ParsedDecimal::integral`], and a separate
    /// field because of it.** `m` is the truthiness of `int(d) - d`, i.e. of the
    /// value *before* rounding; this is the truthiness of the value *after* it,
    /// and the two disagree exactly where the sign is decided — measured
    /// `floatformat("-0.0001", 2)` is `"0.00"` with `m` truthy and the rounded
    /// value `Decimal("-0.00")` falsy, so a port that read `integral` here would
    /// emit `"-0.00"`. Same for `"-0.004"` at 2 against `"-0.5"` at 0, which
    /// keeps its minus.
    pub truthy: bool,
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
    /// `format % value` — the `%` operator with the format string **already
    /// built**. Two callers, and they are the two shapes a Python `%` has:
    /// `stringformat` (`:292`, whose own docstring makes the leading `%` part of
    /// the *filter's* interface) and `filesizeformat`'s five unit branches
    /// (`:927-935`, where the format is `gettext("%s KB")`'s **translation**).
    ///
    /// The format string is Rust's to build and the operator is Python's, so
    /// `stringformat` passes `format!("%{}", to_str(arg))` and `filesizeformat`
    /// passes what `gettext` returned. The second is why this takes the whole
    /// format rather than `stringformat`'s `"%" + spec` suffix: a catalog may
    /// **move** the `%s`, so a Rust body that reassembled `"%s " + unit` would
    /// apply the operator to the untranslated literal and answer the English
    /// string under every locale — a divergence no English-only corpus row can
    /// see, which is the same blindness §11's rule is about.
    fn percent_format(&self, format: &str, value: &Value) -> Result<Value, Error>;
    /// `format % {name: size}` — the same operator with the **mapping** operand
    /// `filesizeformat`'s two byte branches use (`:908`, `:925`).
    ///
    /// A second method rather than a flag on the first, because the operand is
    /// not a `Value` at all: `{"size": n}` is a **dict**, and `classify` interns
    /// one as an `Opaque` — Rust cannot build it. Measured on the pinned tree,
    /// `"%(size)d bytes" % {"size": 1023}` is `"1023 bytes"` while the same
    /// expression with a list, a tuple, an int or a str raises
    /// `TypeError: format requires a mapping`, so no `Value` can stand in for the
    /// one-entry dict the source writes.
    fn percent_mapping(&self, format: &str, name: &str, size: &Value) -> Result<Value, Error>;
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

    // --- the type questions Rust cannot answer ------------------------------
    /// `stringformat`'s `isinstance(value, tuple)` (`:289`) — the test that
    /// decides whether the operand is stringified before `%` sees it.
    ///
    /// The branch is load-bearing rather than defensive, and measured on the
    /// pinned tree: `stringformat((1, 2, 3), "s")` is `"(1, 2, 3)"` *through this
    /// test*, where `"%s" % (1, 2, 3)` alone raises `TypeError: not all arguments
    /// converted during string formatting` — which the filter's own
    /// `except (ValueError, TypeError)` catches, so its answer without the test
    /// is `""`. A **list** is the opposite case and needs no test:
    /// `stringformat([1, None], "s")` is `"[1, None]"`, because CPython unpacks a
    /// **tuple** operand and treats every other sequence as a single value. That
    /// asymmetry is why the source tests for `tuple` and not for `Sequence`, and
    /// why a Rust `matches!(value, Value::List(_))` would be exactly backwards.
    ///
    /// Rust cannot ask: a tuple crosses the FFI as `Value::Opaque` — the host's
    /// `classify` interns it as one deliberately, *because* flattening it into
    /// `Value::List` would leave [`FilterHost::as_nested_sequence`] and
    /// [`FilterHost::get_index`] answering with a list's semantics — and
    /// `isinstance` against an `Opaque` is a `__class__` walk the host owns (§D2).
    ///
    /// A bare predicate, unlike [`FilterHost::as_nested_sequence`] below, and
    /// §D7.1's "one method, not a predicate plus an iteration" is no objection:
    /// that argument is against *splitting* a semantic unit, and this question
    /// has nothing to be fused with.
    fn is_tuple(&self, value: &Value) -> bool;
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
    ///
    /// The returned [`NestedSequence`] carries Django's `if children:` for the
    /// **object**, not for the items it materialises — the two differ for a
    /// generator, which is truthy even when it yields nothing. See that type.
    fn as_nested_sequence(&self, value: &Value) -> Result<Option<NestedSequence>, Error>;
    /// The comma-separated bits of a filter's argument — `pluralize`
    /// (`defaultfilters.py:950-952`) and `yesno` (`:863`) — as one call.
    ///
    /// `prepend_comma` is the whole of the difference between the two callers,
    /// and it is load-bearing in both directions rather than convenient:
    /// `pluralize` prefixes a comma to an argument that has none (`if "," not in
    /// arg: arg = "," + arg`) so that `pluralize(1)` has the bits `["", "s"]`,
    /// while `yesno` splits the argument as it stands so that
    /// `yesno(True, "yes")` has the single bit `["yes"]` and returns the
    /// **value** rather than a word. A caller that shared one setting would get
    /// one of those two rows wrong.
    ///
    /// **One method rather than `contains` plus a concatenation plus `split`,
    /// because the three are one question whose answer depends on the argument's
    /// type** — §D7.1's fusion test. All four measured on the pinned tree, all
    /// reachable from template syntax:
    ///
    /// ```text
    /// {{ x|yesno:5 }}      -> AttributeError: 'int' object has no attribute 'split'
    /// {{ x|pluralize:5 }}  -> TypeError: argument of type 'int' is not a container or iterable
    /// pluralize(1, [1])    -> TypeError: can only concatenate str (not "list") to str
    /// pluralize(1, [","])  -> AttributeError: 'list' object has no attribute 'split'
    /// ```
    ///
    /// The last two are the pair that settles it: `"," in [","]` is a
    /// **membership** test answering `True`, so that argument skips the
    /// concatenation and dies at the split instead. Which of the three operations
    /// raises, and with which class, is therefore the argument's type's decision —
    /// and `str` is not even the question, since a `str` subclass that answers
    /// `__html__` (markupsafe's `Markup`) passes all three. Split back out,
    /// `contains` and the concatenation would each have exactly one caller, which
    /// is the `repr` mistake one method along that [`FilterHost::slugify`]'s doc
    /// names.
    ///
    /// The filters' own logic stays in the bodies: `len(bits) > 2 -> ""` is
    /// `pluralize`'s and `len(bits) < 2` returning the value is `yesno`'s.
    ///
    /// Measured but **not** reachable from the corpus's ordinary rows — the
    /// comparison is on an answer, so a row can only pin these arms through its
    /// `raises` key. See `tests/filter_cases.py`.
    fn comma_bits(&self, value: &Value, prepend_comma: bool) -> Result<Vec<String>, Error>;

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
    ///
    /// **Fallible where its neighbours here are not, and the measurement is one
    /// call**: `strip_tags("<a" + "<" * 49 + "x" * 1000)` raises
    /// `SuspiciousOperation` (empty message) out of `HTMLParser`'s depth limit
    /// (`MAX_STRIP_TAGS_DEPTH = 50`, `html.py:246`). The input is ASCII, so
    /// nothing at the FFI boundary stops it arriving, and Django's own
    /// `striptags` body has no `try` around this call — the raise **is** that
    /// filter's answer on such a value. A bare `String` here would have to
    /// swallow it or panic, and the corpus row that pins the behaviour compares
    /// the exception's class.
    fn strip_tags(&self, text: &str) -> Result<String, Error>;
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
    /// `django.utils.text.phone2numeric` (`text.py:313-344`), **whole** — the
    /// 26-entry table and the `.lower()` both.
    ///
    /// **The one helper in this trait reached through a decorator with no
    /// `@stringfilter` in between**, and the argument's shape follows from that
    /// rather than from the table: `phone2numeric` carries `@keep_lazy_text`
    /// (`text.py:313`), so the function the registry holds is not the `def`
    /// body. A `Promise` argument yields a *lazy* result (`__proxy__` out,
    /// measured), and any other non-`str` is refused by `phone.lower()` **with
    /// its own class named** — `'dict' object has no attribute 'lower'`, where a
    /// Rust-side classification can only say `'object'`. Neither is expressible
    /// through a `&str`, so this takes a [`Value`] and returns one: a `str` in
    /// gives a plain `str` out, a `SafeString` marker does **not** survive
    /// (measured), and no `Opaque` class name is ever needed because Python does
    /// the naming.
    ///
    /// Whole rather than a [`FilterHost::str_lower`] the body would drive, per
    /// §D2's third clause: **a filter's body must call the host for a helper
    /// whose `def` carries a result-changing decorator, rather than reproduce
    /// it.** The two axes that classified `phone2numeric` as pure — no stateful
    /// reference, no divergent string method — are both blind to a decorator
    /// that changes the *result*, which is why a classification that asked only
    /// those two questions said this filter's Rust body was safe to write.
    /// Swept over the pinned tree: **15** helpers in `django/utils/*.py` carry
    /// `@keep_lazy`/`@keep_lazy_text`, **10** are referenced by a filter body in
    /// **23** `(filter, helper)` pairs, and **5** pairs sit in bodies with no
    /// `@stringfilter`. Of those five, four call `mark_safe` — a host method, so
    /// `keep_lazy` is inherited rather than reproduced — which leaves this one,
    /// the only body that owned its helper's algorithm. `tests/
    /// test_filter_inventory.py` derives the five and asserts a verdict each, so
    /// the class is closed at one by measurement.
    fn phone2numeric(&self, value: &Value) -> Result<Value, Error>;
    /// `django.utils.html.urlize` — a regex over `settings.URLIZE_ASSUME_HTTPS`,
    /// so the setting and the regex engine are both the host's.
    ///
    /// **`limit` is `Option<&IntValue>` rather than the `Option<usize>` this
    /// signature carried until wave B, because that type cannot express two of the
    /// answers the corpus reaches.** `Urlizer.trim_url` (`html.py:412-415`) is
    /// `if limit is None or len(x) <= limit: return x` and then
    /// `"%s…" % x[: max(0, limit - 1)]`, so `trim_url_limit` is compared against a
    /// length and sliced — both of which take any Python int. Measured on the
    /// pinned tree through `{{ x|urlizetrunc:… }}` on a URL long enough to
    /// truncate: `0`, `-1` and `-5` all answer `'…'`, and a 30-digit literal is
    /// untruncated. A `usize` cannot hold the negatives, and the untruncating
    /// literal would have to saturate rather than arrive. This is §D2's sentinel
    /// rule — *a seam's signature is the wrapped function's signature, defaults
    /// and sentinels included* — in the shape `date_format`/`time_format` gave it
    /// (`c4027d0`): `trim_url_limit`'s ints are a sentinel range, not a length.
    ///
    /// **The `int()` is not this method's.** `urlizetrunc`'s body does it —
    /// `int(limit)` before the call (`defaultfilters.py:395`) — so
    /// [`FilterHost::to_int`] is where `int("abc")`'s `ValueError` and
    /// `int(None)`'s `TypeError` come from, and this seam is handed an int or
    /// `None` and nothing else. `None` is `urlize`'s own call site
    /// (`:383`, `trim_url_limit` defaulted), not a spelling of 0.
    ///
    /// `nofollow=True` is not a parameter for the same reason `trim_url_limit`
    /// has no default here: both call sites hardcode it (`:383`, `:395`), so
    /// carrying it would be carrying a constant.
    fn urlize(&self, text: &str, limit: Option<&IntValue>, autoescape: bool) -> String;
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
    ///
    /// **Infallible on the strength of an audit rather than by assumption.** The
    /// one input that makes it raise is a lone surrogate — `iri_to_uri("\ud800")`
    /// is `UnicodeEncodeError` from the UTF-8 encoding `quote` does — and a `&str`
    /// argument cannot carry one, so no body can reach that raise any more than it
    /// can reach `iri_to_uri` with it. Measured on the pinned tree, with the
    /// boundary in the way: `{{ x|iriencode }}` with `x = "\ud800"` raises at the
    /// FFI in both engines (`test_lone_surrogate_raises_at_the_ffi_boundary`).
    fn iri_to_uri(&self, text: &str) -> String;
    /// `urllib.parse.quote` called the way `urlencode`'s body calls it
    /// (`:365-379`): with the `safe` keyword **omitted** when `safe` is `None`,
    /// and otherwise with that object passed through as it is.
    ///
    /// `safe` is `Option<&Value>` rather than `Option<&str>` per **§D7.1**:
    /// `quote` observes the argument's *type*, not its text. `isinstance(safe,
    /// str)` selects the `safe.encode('ascii', 'ignore')` branch and every other
    /// object goes to `bytes([c for c in safe if c < 128])` (`urllib/parse.py`,
    /// `quote_from_bytes`), which **answers** for an all-`int` container
    /// (measured: `safe=[1]` is `'a%20b%2Fc'`), for `bytes` (`safe=b'/'` is
    /// `'a%20b/c'`) and for `[]`, and refuses with a *different* class or message
    /// for `[-1]`, `[1.0]` and `['/']`. A `&str` parameter would force a body to
    /// run that dispatch in Rust and synthesise the exceptions of a branch it
    /// cannot see — which is what an earlier `(&str, &str)` signature did, and
    /// why `urlencode`'s rows were divergence rows instead of parity rows.
    ///
    /// `None` covers **two** Django tokens — the absent argument and an explicit
    /// `None` — because the body's `if safe is not None` leaves `kwargs` empty
    /// for both, so they are one call.
    ///
    /// **Fallible**, unlike `iri_to_uri`: the non-`str` branch raises, and
    /// `host_error` keeps Python's own exception object for it. The lone
    /// surrogate that makes `quote("\ud800", safe="")` raise
    /// `UnicodeEncodeError` stays unreachable for `iri_to_uri`'s reason — a
    /// `&str` cannot hold one.
    fn quote(&self, text: &str, safe: Option<&Value>) -> Result<String, Error>;
    /// The `json.dumps` inside `django.utils.html.json_script` (`html.py:103`):
    /// **`json.dumps(value, cls=DjangoJSONEncoder)`**, whole, with `json`'s own
    /// default separators.
    ///
    /// Both halves of that are load-bearing and both were got wrong once, in
    /// `PyFilterHost`: the separators are `(", ", ": ")` and not `(",", ":")`,
    /// and the encoder is what makes a `date`, `Decimal` or `UUID` serialisable
    /// at all rather than a `TypeError`. Measured on the pinned tree,
    /// `json_script({"a": 1})` is `'{"a": 1}'` and
    /// `json_script({"d": date(2026, 10, 8)})` is `'{"d": "2026-10-08"}'`.
    /// `ensure_ascii` needs no mention: `True` is `json.dumps`' default.
    ///
    /// **The escaping and the `<script>` wrapping are the caller's**, not this
    /// method's — `json_script`'s Rust body owns `_json_script_escapes`
    /// (`html.py:88-92`, three entries) and the two templates.
    fn json_dumps(&self, value: &Value) -> Result<String, Error>;
    /// `pprint.pformat` — `pprint` (`:990-996`).
    fn pformat(&self, value: &Value) -> Result<String, Error>;
    /// `random.choice` — `random` (`:642-648`), which is stateful and not a
    /// function of its argument.
    fn random_choice(&self, value: &Value) -> Result<Value, Error>;

    // --- locale, language and the clock -------------------------------------
    /// `django.utils.timezone.template_localtime(value, use_tz=…)` — `date` and
    /// `time` (`expects_localtime`).
    ///
    /// **`use_tz` is `Context.use_tz`, not `settings.USE_TZ`**, which is what the
    /// engine passes (`base.py:811`). The two differ on purpose: `{% localtime
    /// off %}` flips `context.use_tz`, and that tag is the field's whole reason
    /// for existing. A host reading `settings.USE_TZ` itself would ignore the tag.
    ///
    /// `Context.use_tz` **defaults to `None`**, which `template_localtime` reads
    /// as "consult the setting" (`timezone.py:155-158`: `settings.USE_TZ if
    /// use_tz is None else use_tz`). A `bool` cannot spell that, so the caller
    /// resolves it — safe here **only because the substitution is exactly the
    /// callee's own expression**, which puts this in the class with
    /// [`FilterHost::comma_bits`] rather than the `date_format` one below:
    /// performing the same decision the callee would have performed is not
    /// reproducing it. It would stop being safe if `None` ever meant anything
    /// else here.
    fn localtime(&self, value: Value, use_tz: bool) -> Result<Value, Error>;
    /// `django.utils.formats.date_format` — the active language's formats.
    ///
    /// **`format` is an `Option` because Django's default is spelled `None`, and
    /// `None` and the literal `"DATE_FORMAT"` are the *same call*.** The body is
    /// `dateformat.format(value, get_format(format or "DATE_FORMAT", use_l10n))`
    /// (`formats.py:155-158`). Measured on `date(2026, 10, 8)` under
    /// `LANGUAGE_CODE="de"`:
    ///
    /// ```text
    /// settings.DATE_FORMAT                 = 'N j, Y'
    /// get_format("DATE_FORMAT")            = 'j. F Y'
    /// date_format(d, None)                 = '8. Oktober 2026'
    /// date_format(d, "DATE_FORMAT")        = '8. Oktober 2026'
    /// date_format(d, settings.DATE_FORMAT) = 'Okt. 8, 2026'   <- diverges
    /// ```
    ///
    /// So **`settings.DATE_FORMAT` is not the default**: it is `en-us`'s, and a
    /// caller that resolved the default itself would be right in `en-us` and
    /// wrong in every language that has a format module. The sentinel crosses the
    /// seam unread, which is why this is an `Option` and not a `&str`.
    fn date_format(&self, value: &Value, format: Option<&str>) -> Result<String, Error>;
    /// `django.utils.formats.time_format`, the same shape and the same reason:
    /// its body is `get_format(format or "TIME_FORMAT", use_l10n)`.
    fn time_format(&self, value: &Value, format: Option<&str>) -> Result<String, Error>;
    /// `django.utils.dateformat.format` (`dateformat.py:326-329`) — the **second**
    /// branch of `date`, where the argument is used literally rather than resolved
    /// as a format setting.
    ///
    /// **`format` is a `&Value` where [`Self::date_format`]'s is an `Option<&str>`,
    /// and that is the wrapped signature rather than an inconsistency.**
    /// `formats.date_format` hands its argument to `get_format(arg or
    /// "DATE_FORMAT")`, where it is a **lookup key** and `None` is the sentinel
    /// selecting the default key. `dateformat.format` instead `str()`s any object
    /// and splits the text (`dateformat.py:42`), so an absent argument is the text
    /// `"None"` — four live format characters, not an empty string. Measured on
    /// `datetime(2026, 10, 8, 14, 30, 45)`:
    ///
    /// ```text
    /// dateformat.format(dt, None)  = 'Oct.202610'
    /// dateformat.format(dt, True)  = 'UTCThu, 08 Oct 2026 14:30:45 +0000000000'
    /// dateformat.format(dt, 5)     = '5'
    /// ```
    ///
    /// So a `&str` seam would make the Rust body compute `str(None)` itself, which
    /// is the pattern this trait exists to avoid. The two branches of `date` call
    /// two different functions; this is the second one's own signature.
    fn dateformat_format(&self, value: &Value, format: &Value) -> Result<String, Error>;
    /// `django.utils.dateformat.time_format` (`dateformat.py:332-335`) — the second
    /// branch of `time`, the same shape and the same reason as
    /// [`Self::dateformat_format`].
    ///
    /// It differs from the `date` twin on the absent argument, which is worth
    /// knowing because it is a second reason the return is a `Result`: `TimeFormat`
    /// has no `N`, so `dateformat.time_format(t, None)` raises where the `date` twin
    /// answers — `AttributeError: 'TimeFormat' object has no attribute 'N'`.
    fn dateformat_time_format(&self, value: &Value, format: &Value) -> Result<String, Error>;
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
    ///
    /// **It returns a `Result`, for the same reason `strip_tags` does.** The
    /// helper raises `AttributeError` on a value with no date protocol —
    /// `timesince("x")` is `'str' object has no attribute 'year'` — and
    /// `timesince_filter` does **not** catch it: its `except (ValueError,
    /// TypeError)` lets that class through while catching the mixed-awareness
    /// `TypeError` and answering `''`. The two classes leave the *same* filter in
    /// opposite directions, so a bare `String` could express neither. This is the
    /// audit row in the Python host's `infallible_failure` table leaving the list,
    /// by the route that table's own closing paragraph describes.
    fn timesince(&self, d: &Value, now: Option<&Value>, reversed: bool) -> Result<String, Error>;
    /// `django.utils.formats.number_format`, in the three shapes the two callers
    /// use. The struct is [`NumberFormatArgs`].
    fn number_format(&self, args: NumberFormatArgs<'_>) -> String;
    /// `gettext` — the active language, and `settings.USE_I18N`.
    fn gettext(&self, text: &str) -> String;
    /// `ngettext` — the same, with the plural form.
    ///
    /// `n` is a `u64` where Python's is an arbitrary-precision int. Both call
    /// sites are `filesizeformat`'s (`:908`, `:925`) and the only non-literal one
    /// is the `bytes_ < KB` arm, so **`n < 1024`** holds; the other is a literal
    /// `0`. So the narrowness is bounded by the *call site* rather than by the
    /// signature, and the bound is written here rather than left to be
    /// re-derived — a wider caller would have to widen this first.
    fn ngettext(&self, singular: &str, plural: &str, n: u64) -> String;
    /// `Decimal(str(value))`, then Django's `Decimal(str(float(value)))` retry
    /// (`defaultfilters.py:152-159`) — `floatformat`.
    ///
    /// **The decimal engine is host-side**, which is §D2's own rule read
    /// literally: "what a `decimal.Context` is set to" is not a function of the
    /// filter's arguments. The plan's earlier claim that `floatformat`'s
    /// "exponent arithmetic" was Rust was refuted by measurement — see the
    /// Task 7 amendment — and this method and the next are the two halves it
    /// crosses the seam as.
    fn decimal_parse(&self, value: &Value) -> Result<DecimalParse, Error>;
    /// `:194-205`: `exp = Decimal(1).scaleb(-abs(p))`, the `prec` computation and
    /// `d.quantize(exp, ROUND_HALF_UP, Context(prec=prec))`, as **one** call.
    ///
    /// Not three, because `prec = max(getcontext().prec, abs(p) + units + 1)`
    /// (`:200`) is a read of thread-local state that *bounds the quantize three
    /// lines below it*: splitting the method would invite a Rust caller to
    /// compute its own `prec`, which is the divergence this seam exists to
    /// prevent. The context does reach the answer, measurably — below
    /// `2*(Emax + prec)` the `scaleb` clamps to `Decimal('0E-' + Etiny)` (so the
    /// rounding *position* is 999999 whatever `p` is), and above it the same call
    /// raises `InvalidOperation` out of a `try` that does not exist.
    ///
    /// `places` is `abs(p)`; `integral` is [`ParsedDecimal::integral`], passed
    /// rather than recomputed so the source's single `int(d) - d` stays single.
    fn decimal_quantize(
        &self,
        decimal: &Value,
        places: u64,
        integral: bool,
    ) -> Result<DecimalTuple, Error>;
    /// `formats.number_format(round(value, 1), 1)` whole (`:911-912`).
    ///
    /// A host call rather than [`FilterHost::number_format`] over a Rust-rounded
    /// argument, because Python's two-argument `round` on a **float** rounds
    /// against the exact binary value: measured `round(0.35, 1)` is `0.3` (the
    /// exact value is `0.34999999999999997780…`) where the decimal tie-break
    /// gives `0.4`. A Rust `round` over the decimal text — what the rest of this
    /// crate does — would answer `0.4`.
    fn filesize_number_format(&self, value: f64) -> String;
    /// `bytes_ / (1 << shift)` — the division half of `filesizeformat`
    /// (`defaultfilters.py:914-925`), where `bytes_` is the already-converted
    /// `int`, `KB = 1 << 10` and the branches `MB`, `GB`, `TB`, `PB` are
    /// `1 << 20`, `1 << 30`, `1 << 40`, `1 << 50`.
    ///
    /// **It takes the `int`, not the `Value`, and that is the whole reason it is
    /// a seam rather than a Rust expression.** `filesizeformat` calls `int()`
    /// **once** at `:905`; the body needs the result before this call to choose
    /// its branch (`bytes_ < KB`) and to compute the sign (`negative = bytes_ < 0`).
    /// A `&Value` signature would make the body convert, then the method convert
    /// again — two calls, which for an object whose `__int__` has side effects is
    /// two answers. Taking the `IntValue` puts the method at the division alone,
    /// which is exactly the source's own boundary: the `int()` sits inside the
    /// `try` at `:905-907` (its `ValueError`/`TypeError` become `'0 bytes'`), and
    /// the division sits **outside** it, so its `OverflowError` escapes the
    /// filter. One method, one statement of the source, one failure mode.
    ///
    /// The division must run in Python rather than over two `f64`s. CPython's
    /// `int.__truediv__` is a correctly-rounded long division that does not
    /// convert its operands, which is what makes the two failure messages below
    /// differ; `float(a) / float(b)` would answer for inputs in range and change
    /// the text out of range.
    ///
    /// **Measured on the pinned interpreter** (`.django/` @ 6.0.9, CPython
    /// 3.14.4) — the five shapes that fix the boundary, and none of them is
    /// reachable from the suite:
    ///
    /// | expression | outcome |
    /// |---|---|
    /// | `filesizeformat(inf)` | `OverflowError: cannot convert float infinity to integer` — from `int()`, inside the `try`, so it is **not** caught: the `except` names `(TypeError, ValueError, UnicodeDecodeError)` |
    /// | `filesizeformat(nan)` | `'0\xa0bytes'` — `int(nan)` is a `ValueError`, which **is** caught |
    /// | `10**400 / 2**50` | `OverflowError: integer division result too large for a float` |
    /// | `float(10**400)` | `OverflowError: int too large to convert to float` |
    /// | `number_format(inf, 1)` | `'inf.0'` |
    ///
    /// The last three are why this cannot be answered by a Rust `f64`: a
    /// \>308-digit decimal parses to `inf`, and `inf.0` formats to `'inf.0\xa0PB'`
    /// — a plausible string, silently wrong, where Django raises. The last two
    /// are why it cannot be answered by [`FilterHost::to_float`] either: both
    /// raise `OverflowError` and the **messages differ**, so a body reaching for
    /// the float seam would produce the right class and the wrong text.
    ///
    /// Not folded into [`FilterHost::filesize_number_format`] as one
    /// `int_and_divide`-style call, and the reason is the fusion rule rather than
    /// convenience: fusion is for operations that are one semantic unit with
    /// non-separable failures. Here they separate cleanly — the division's
    /// `OverflowError` escapes and the formatter on a finite float raises at all —
    /// and the source itself keeps them apart, inline in each branch against a
    /// nested `def`. Folding them would merge what Python separates.
    fn int_divide_by_power_of_two(&self, value: &IntValue, shift: u32) -> Result<f64, Error>;
    /// `int(a) % int(b)` — `divisibleby` (`:854-858`), which has **no** `try`, so
    /// `b == 0` is a `ZeroDivisionError` and a non-numeric operand a `ValueError`
    /// or `TypeError`; all three escape the filter.
    ///
    /// Rust compares the answer to zero, which is a `Small(0)` test: an
    /// `IntValue::Big` is canonical decimal text and never zero.
    fn int_modulo(&self, a: &Value, b: &Value) -> Result<IntValue, Error>;
    /// `django.utils.timezone.now`, and **no filter or tag in this engine may
    /// call it**.
    ///
    /// `timeuntil` is the caller the previous doc named, and following it is
    /// wrong: `timeuntil_filter` is `timesince(d, now, reversed=True)` with `now`
    /// filled **inside** `timesince` (`timesince.py:67-68`) from `d.tzinfo`. A
    /// caller supplying this value instead takes the `elif is_aware(now) and
    /// is_aware(d)` branch and raises, and the filter catches `(ValueError,
    /// TypeError)` and answers `""` — so the documented caller turns a real
    /// answer into an empty string. Measured under `USE_TZ=True` with a naive `d`:
    ///
    /// ```text
    /// timesince(naive, None,           reversed=True) = '2 hours, 59 minutes'
    /// timesince(naive, timezone.now(), reversed=True) = TypeError: can't subtract
    ///                                                   offset-naive and offset-aware datetimes
    /// {{ naive_dt|timeuntil }}                        = '2 hours, 59 minutes'
    /// ```
    ///
    /// **`{% now %}` must not call it either**, which is the half that is easy to
    /// miss: `NowNode.render` (`defaulttags.py:402-404`) is
    /// `datetime.now(tz=get_current_timezone() if settings.USE_TZ else None)`, and
    /// `timezone.now()` is UTC-based. Measured with `TIME_ZONE="Europe/Paris"`,
    /// `USE_TZ=True`: `{% now "H:i:s" %}` answers `07:28:58` where
    /// `time_format(timezone.now(), "H:i:s")` answers `05:28:58`. So the tag's
    /// task needs a *different* method — the engine's current time in the current
    /// timezone — and this one stays the faithful wrapper of `timezone.now()` it
    /// is, with no caller in the filter layer and none owed to it.
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

/// The panic the 17 non-`Result` operations give when nobody answers them.
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

    fn percent_format(&self, _format: &str, _value: &Value) -> Result<Value, Error> {
        Err(refused("percent_format"))
    }

    fn percent_mapping(&self, _format: &str, _name: &str, _size: &Value) -> Result<Value, Error> {
        Err(refused("percent_mapping"))
    }

    fn is_safe(&self, _value: &Value) -> bool {
        refused_panic("is_safe")
    }

    fn is_tuple(&self, _value: &Value) -> bool {
        refused_panic("is_tuple")
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

    fn as_nested_sequence(&self, _value: &Value) -> Result<Option<NestedSequence>, Error> {
        Err(refused("as_nested_sequence"))
    }

    fn comma_bits(&self, _value: &Value, _prepend_comma: bool) -> Result<Vec<String>, Error> {
        Err(refused("comma_bits"))
    }

    fn str_lower(&self, _text: &str) -> String {
        refused_panic("str_lower")
    }

    fn str_title(&self, _text: &str) -> String {
        refused_panic("str_title")
    }

    fn strip_tags(&self, _text: &str) -> Result<String, Error> {
        Err(refused("strip_tags"))
    }

    fn slugify(&self, _text: &str) -> String {
        refused_panic("slugify")
    }

    fn phone2numeric(&self, _value: &Value) -> Result<Value, Error> {
        Err(refused("phone2numeric"))
    }

    fn urlize(&self, _text: &str, _limit: Option<&IntValue>, _autoescape: bool) -> String {
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

    fn quote(&self, _text: &str, _safe: Option<&Value>) -> Result<String, Error> {
        Err(refused("quote"))
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

    fn date_format(&self, _value: &Value, _format: Option<&str>) -> Result<String, Error> {
        Err(refused("date_format"))
    }

    fn time_format(&self, _value: &Value, _format: Option<&str>) -> Result<String, Error> {
        Err(refused("time_format"))
    }

    fn dateformat_format(&self, _value: &Value, _format: &Value) -> Result<String, Error> {
        Err(refused("dateformat_format"))
    }

    fn dateformat_time_format(&self, _value: &Value, _format: &Value) -> Result<String, Error> {
        Err(refused("dateformat_time_format"))
    }

    fn timesince(
        &self,
        _d: &Value,
        _now: Option<&Value>,
        _reversed: bool,
    ) -> Result<String, Error> {
        Err(refused("timesince"))
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

    fn decimal_parse(&self, _value: &Value) -> Result<DecimalParse, Error> {
        Err(refused("decimal_parse"))
    }

    fn decimal_quantize(
        &self,
        _decimal: &Value,
        _places: u64,
        _integral: bool,
    ) -> Result<DecimalTuple, Error> {
        Err(refused("decimal_quantize"))
    }

    fn filesize_number_format(&self, _value: f64) -> String {
        refused_panic("filesize_number_format")
    }

    fn int_divide_by_power_of_two(&self, _value: &IntValue, _shift: u32) -> Result<f64, Error> {
        Err(refused("int_divide_by_power_of_two"))
    }

    fn int_modulo(&self, _a: &Value, _b: &Value) -> Result<IntValue, Error> {
        Err(refused("int_modulo"))
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
/// Non-`Result` operations (the 17 that return a bare `String`, `bool` or
/// `Value`) panic when unset rather than defaulting, for the same reason: a
/// sentinel could silently satisfy an assertion.
///
/// `#[cfg(test)]`, because the only thing that needs it is a test: this crate's
/// `cargo test` compiles every `#[cfg(test)] mod tests` in `filters/<name>.rs`
/// against the same crate, so it is visible everywhere an agent's filter tests
/// live. Shipping it in the library would put 43 `Option` fields and two
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
    // here would stop a filter module's test from scripting any of the 43 answers
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
    pub percent_mapping: Option<Value>,
    pub is_safe: Option<bool>,
    pub is_tuple: Option<bool>,
    pub mark_safe: Option<Value>,
    pub conditional_escape: Option<Value>,
    pub sort_by_property: Option<Value>,
    /// `Some(None)` is a **scripted** "plain item"; `None` is "not scripted".
    /// The double `Option` is the trait's own shape —
    /// `Result<Option<NestedSequence>, Error>` — and the outer layer here is the
    /// scripted/unscripted question, not a third meaning.
    pub as_nested_sequence: Option<Option<NestedSequence>>,
    pub comma_bits: Option<Vec<String>>,
    pub str_lower: Option<String>,
    pub str_title: Option<String>,
    pub strip_tags: Option<String>,
    pub slugify: Option<String>,
    /// A `Value` rather than a `String`, because the helper's answer can be a
    /// lazy proxy — the one place a filter's host answer is not already text.
    pub phone2numeric: Option<Value>,
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
    pub dateformat_format: Option<String>,
    pub dateformat_time_format: Option<String>,
    pub timesince: Option<String>,
    pub number_format: Option<String>,
    pub gettext: Option<String>,
    pub ngettext: Option<String>,
    pub decimal_parse: Option<DecimalParse>,
    pub decimal_quantize: Option<DecimalTuple>,
    pub filesize_number_format: Option<String>,
    pub int_divide_by_power_of_two: Option<f64>,
    pub int_modulo: Option<IntValue>,
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

    fn percent_format(&self, _format: &str, _value: &Value) -> Result<Value, Error> {
        self.record("percent_format")?;
        self.percent_format
            .clone()
            .ok_or_else(|| refused("percent_format"))
    }

    fn percent_mapping(&self, _format: &str, _name: &str, _size: &Value) -> Result<Value, Error> {
        self.record("percent_mapping")?;
        self.percent_mapping
            .clone()
            .ok_or_else(|| refused("percent_mapping"))
    }

    fn is_safe(&self, _value: &Value) -> bool {
        self.record_plain("is_safe");
        self.is_safe.unwrap_or_else(|| refused_panic("is_safe"))
    }

    fn is_tuple(&self, _value: &Value) -> bool {
        self.record_plain("is_tuple");
        self.is_tuple.unwrap_or_else(|| refused_panic("is_tuple"))
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

    fn as_nested_sequence(&self, _value: &Value) -> Result<Option<NestedSequence>, Error> {
        self.record("as_nested_sequence")?;
        self.as_nested_sequence
            .clone()
            .ok_or_else(|| refused("as_nested_sequence"))
    }

    fn comma_bits(&self, _value: &Value, _prepend_comma: bool) -> Result<Vec<String>, Error> {
        self.record("comma_bits")?;
        self.comma_bits.clone().ok_or_else(|| refused("comma_bits"))
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

    fn strip_tags(&self, _text: &str) -> Result<String, Error> {
        self.record("strip_tags")?;
        self.strip_tags.clone().ok_or_else(|| refused("strip_tags"))
    }

    fn slugify(&self, _text: &str) -> String {
        self.record_plain("slugify");
        self.slugify
            .clone()
            .unwrap_or_else(|| refused_panic("slugify"))
    }

    fn phone2numeric(&self, _value: &Value) -> Result<Value, Error> {
        self.record("phone2numeric")?;
        self.phone2numeric
            .clone()
            .ok_or_else(|| refused("phone2numeric"))
    }

    fn urlize(&self, _text: &str, _limit: Option<&IntValue>, _autoescape: bool) -> String {
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

    fn quote(&self, _text: &str, _safe: Option<&Value>) -> Result<String, Error> {
        self.record("quote")?;
        self.quote.clone().ok_or_else(|| refused("quote"))
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

    fn date_format(&self, _value: &Value, _format: Option<&str>) -> Result<String, Error> {
        self.record("date_format")?;
        self.date_format
            .clone()
            .ok_or_else(|| refused("date_format"))
    }

    fn time_format(&self, _value: &Value, _format: Option<&str>) -> Result<String, Error> {
        self.record("time_format")?;
        self.time_format
            .clone()
            .ok_or_else(|| refused("time_format"))
    }

    fn dateformat_format(&self, _value: &Value, _format: &Value) -> Result<String, Error> {
        self.record("dateformat_format")?;
        self.dateformat_format
            .clone()
            .ok_or_else(|| refused("dateformat_format"))
    }

    fn dateformat_time_format(&self, _value: &Value, _format: &Value) -> Result<String, Error> {
        self.record("dateformat_time_format")?;
        self.dateformat_time_format
            .clone()
            .ok_or_else(|| refused("dateformat_time_format"))
    }

    fn timesince(
        &self,
        _d: &Value,
        _now: Option<&Value>,
        _reversed: bool,
    ) -> Result<String, Error> {
        self.record("timesince")?;
        self.timesince.clone().ok_or_else(|| refused("timesince"))
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

    fn decimal_parse(&self, _value: &Value) -> Result<DecimalParse, Error> {
        self.record("decimal_parse")?;
        // `DecimalParse::Unparseable` is a *value* here, not an error, which is
        // why a scripted `Unparseable` and an unscripted field must not be
        // confused: only the outer `Option` is "not scripted", and a scripted
        // refusal is the inner variant. Collapsing the two — `ok_or` over a
        // flat `Option<Decimal>` — would make the unparseable arm untestable in
        // a bare shell, and that arm is the one the `float`-retry exists for.
        self.decimal_parse
            .clone()
            .ok_or_else(|| refused("decimal_parse"))
    }

    fn decimal_quantize(
        &self,
        _decimal: &Value,
        _places: u64,
        _integral: bool,
    ) -> Result<DecimalTuple, Error> {
        self.record("decimal_quantize")?;
        self.decimal_quantize
            .clone()
            .ok_or_else(|| refused("decimal_quantize"))
    }

    fn filesize_number_format(&self, _value: f64) -> String {
        self.record_plain("filesize_number_format");
        self.filesize_number_format
            .clone()
            .unwrap_or_else(|| refused_panic("filesize_number_format"))
    }

    fn int_divide_by_power_of_two(&self, _value: &IntValue, _shift: u32) -> Result<f64, Error> {
        self.record("int_divide_by_power_of_two")?;
        self.int_divide_by_power_of_two
            .ok_or_else(|| refused("int_divide_by_power_of_two"))
    }

    fn int_modulo(&self, _a: &Value, _b: &Value) -> Result<IntValue, Error> {
        self.record("int_modulo")?;
        self.int_modulo.clone().ok_or_else(|| refused("int_modulo"))
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
    /// The whole 25 rather than a sample: a single method that accidentally
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
            host.percent_format("%s", &v)
                .map(|_| "percent_format")
                .unwrap_err(),
            host.percent_mapping("%(size)d", "size", &v)
                .map(|_| "percent_mapping")
                .unwrap_err(),
            host.decimal_parse(&v).map(|_| "decimal_parse").unwrap_err(),
            host.decimal_quantize(&v, 2, false)
                .map(|_| "decimal_quantize")
                .unwrap_err(),
            host.int_modulo(&v, &v).map(|_| "int_modulo").unwrap_err(),
            host.conditional_escape(&v)
                .map(|_| "conditional_escape")
                .unwrap_err(),
            host.sort_by_property(&v, &v, false)
                .map(|_| "sort_by_property")
                .unwrap_err(),
            host.as_nested_sequence(&v)
                .map(|_| "as_nested_sequence")
                .unwrap_err(),
            host.comma_bits(&v, true).map(|_| "comma_bits").unwrap_err(),
            host.json_dumps(&v).map(|_| "json_dumps").unwrap_err(),
            host.pformat(&v).map(|_| "pformat").unwrap_err(),
            host.random_choice(&v).map(|_| "random_choice").unwrap_err(),
            host.localtime(v.clone(), false)
                .map(|_| "localtime")
                .unwrap_err(),
            host.date_format(&v, Some("Y"))
                .map(|_| "date_format")
                .unwrap_err(),
            host.time_format(&v, Some("H"))
                .map(|_| "time_format")
                .unwrap_err(),
            host.now().map(|_| "now").unwrap_err(),
        ];
        assert_eq!(
            names.len(),
            25,
            "the Result half of the trait is 25 methods"
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
