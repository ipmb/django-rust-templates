//! The filter seam: what a filter body may ask the host (design spec §D2, §D7).
//!
//! **Rust owns the filter and its algorithm; the host owns the engines whose
//! answer lives outside the template** — `settings`, the active language, the
//! clock, and CPython/Python-library behaviours Rust has no equivalent of
//! (`str.title`'s `Cased` property, `HTMLParser`, `textwrap.TextWrapper`,
//! `json.dumps`, `unicodedata`). A body needing one calls a method here and gets
//! an answer or a Python exception's *class* back.
//!
//! Three implementations, three names deliberately: `template-py`'s real one,
//! [`NoFilterHost`] (refuses everything) and [`TestFilterHost`] (answers what a
//! test scripts). "No host" and "a host that answers" fail a test in *opposite*
//! directions, so collapsing them would let a wrong filter pass.

#[cfg(test)]
use std::cell::RefCell;

use crate::filters::value::Value;
use crate::parser::error::Error;
// Only the test-only `TestFilterHost` names an exception class; a real host reports one it
// caught rather than one it was handed.
#[cfg(test)]
use crate::parser::error::PyErrorKind;
use crate::pynum::IntValue;

/// What a filter body is handed besides its value and its argument.
///
/// Deliberately not the context: a filter that could read it would have a behaviour depending on
/// it, and Django's 57 do not. The two that look like exceptions take `context.use_tz` as a
/// *field* instead (§D1), one bool rather than an escape hatch.
pub struct FilterCall<'a> {
    pub host: &'a dyn FilterHost,
    /// The `autoescape` in force, which the seven `needs_autoescape` filters
    /// branch on. Rust owns the branch; the host owns only the escaping it calls.
    pub autoescape: bool,
    /// The context's `use_tz`, for the two `expects_localtime` filters.
    pub use_tz: bool,
}

/// The arguments of `django.utils.formats.number_format` (`formats.py:172`),
/// keyword-called from exactly two filters in three shapes:
/// `defaultfilters.py:186`, `defaultfilters.py:214`, `defaultfilters.py:912`.
///
/// A struct rather than four positional parameters: the call sites differ only
/// in `use_l10n` and `force_grouping`, and those are worth naming at a call site.
pub struct NumberFormatArgs<'a> {
    /// The value to format: an `int`, a `float`, or a **string** of digits.
    /// `floatformat`'s second call passes its own assembled decimal text
    /// (`defaultfilters.py:214` passes `number`, not `rounded_d`), so this cannot be an `f64`.
    pub value: &'a Value,
    /// Django's `decimal_pos`; `None` is its default.
    pub decimal_pos: Option<i64>,
    /// Django's `use_l10n`, **three-valued**: `None` means "localize"
    /// (`formats.py:179-180`), `Some(false)` means the template asked for
    /// `floatformat:…g`. A host may collapse `None` and `Some(true)`; the caller
    /// must not, since that would hide which branch `floatformat` took.
    pub use_l10n: Option<bool>,
    /// Django's `force_grouping`, `False` on every built-in call path.
    pub force_grouping: bool,
}

/// A nested container as `walk_items` needs it: the materialised items **and**
/// Django's truthiness of the *original object*.
///
/// Django's `list_formatter` tests `if children:` on the **container object**: a
/// generator object is always truthy, even one that yields nothing, where a list
/// is truthy exactly when non-empty. Once materialised to a `Vec<Value>` the two
/// are indistinguishable, so `truthy` carries the object's own answer to mirror
/// `if children:` rather than approximating it with `!items.is_empty()`.
#[derive(Clone, PartialEq, Debug)]
pub struct NestedSequence {
    /// The container's items, materialised — `list(item)`, which is also
    /// Django's `iter(item)` half of the nesting test.
    pub items: Vec<Value>,
    /// Django's `if children:`, evaluated on the **original object**.
    pub truthy: bool,
}

impl NestedSequence {
    /// A **sized** container: truthy exactly when it holds something (`list`,
    /// `tuple`). A generator's answer does not follow its items, so that case is
    /// written out in full instead.
    pub fn sized(items: Vec<Value>) -> Self {
        let truthy = !items.is_empty();
        Self { items, truthy }
    }
}

/// A `Decimal` as [`FilterHost::decimal_parse`] returns it: the parts Rust needs
/// to decide and assemble, plus a handle for the call that rounds it.
///
/// The handle is a [`Value::Opaque`] rather than a second `str(value)` through the
/// seam, because the source parses `str(text)` **once** (`defaultfilters.py:153-154`);
/// a re-parse would be a second evaluation of a source-level expression.
#[derive(Clone, PartialEq, Debug)]
pub struct ParsedDecimal {
    /// The `Decimal` itself, as an `Opaque`, for
    /// [`FilterHost::decimal_quantize`].
    pub value: Value,
    /// `str(text)` (`defaultfilters.py:153`) — the payload of all three
    /// `return input_val` arms (`defaultfilters.py:163`, `defaultfilters.py:177`, `defaultfilters.py:182`). A plain
    /// `str`, **not** safe.
    pub input_val: String,
    /// `d.as_tuple()`'s sign.
    pub sign: bool,
    /// `d.as_tuple()`'s digits, most-significant first; `len` is the source's
    /// `len(tupl[1])` at `defaultfilters.py:197`.
    pub digits: Vec<u8>,
    /// `d.as_tuple()`'s exponent, or `None` for the `"F"`/`"n"`/`"N"` a non-finite
    /// reports — which is **two source arms at once, both meaning `return input_val`**:
    /// the `TypeError` the 200-digit sum catches (`defaultfilters.py:168-170`) *and* the
    /// `int(d)` raise at `defaultfilters.py:180`. A body tests this once and never reads
    /// [`ParsedDecimal::integral`], meaningless for a non-finite.
    pub exponent: Option<i64>,
    /// `not (int(d) - d)` — the source's `m` at `defaultfilters.py:180` — and `false`
    /// when that raised, i.e. exactly when `exponent` is `None`.
    ///
    /// **The *integral-value* test, not `exponent < 0`**: `Decimal("1.0")` has a falsy
    /// `m`, which is how `floatformat(1.0, -1)` reaches the integer branch (measured
    /// `"1"`) where an exponent test would take the quantize path.
    pub integral: bool,
}

/// [`FilterHost::decimal_parse`]'s answer: the parse, or the `return ""` its double
/// failure produces (`defaultfilters.py:158-159`).
#[derive(Clone, PartialEq, Debug)]
pub enum DecimalParse {
    Parsed(ParsedDecimal),
    /// `Decimal(input_val)` and the `Decimal(str(float(text)))` retry both raised —
    /// measured, `floatformat("abc")` and `floatformat(None)` are both `""`.
    Unparseable,
}

/// `Decimal.as_tuple()` **after** the quantization, for the Rust-side assembly
/// (`defaultfilters.py:205-212`).
#[derive(Clone, PartialEq, Debug)]
pub struct DecimalTuple {
    pub sign: bool,
    /// The **digit values**, not a count: `defaultfilters.py:206` builds
    /// `[str(digit) for digit in reversed(digits)]`, so the assembly needs the digits
    /// themselves where the parse side needs only `len`.
    pub digits: Vec<u8>,
    /// Always an integer — `quantize` cannot produce a non-finite — and the source
    /// reads `abs(exponent)` to pad (`defaultfilters.py:207`).
    pub exponent: i64,
    /// `bool(rounded_d)` — the second half of `defaultfilters.py:210`'s
    /// `if sign and rounded_d`, and a separate field from [`ParsedDecimal::integral`]:
    /// that is the value *before* rounding, this is the value *after*. The two
    /// disagree exactly where the sign is decided — measured `floatformat("-0.0001", 2)`
    /// is `"0.00"` where a port reading `integral` here would emit `"-0.00"`.
    pub truthy: bool,
}

/// `django.utils.translation.get_language_info(lang_code)`'s three read fields.
///
/// The four `language_*` filters read `["name"]`, `["name_local"]` and `["bidi"]` off
/// **one** call each (`i18n.py:292-310`), so the seam returns the three fields rather
/// than the dict — every index operation on this seam takes an `i64`, not a string.
/// Reading all three eagerly is the same answer as reading one: measured,
/// `get_language_info` resolves the six fallback-only `LANG_INFO` entries
/// (`zh-cn`…`zh-tw`) into their fallback's info first
/// (`django/utils/translation/__init__.py:275`), so every dict carries all three.
#[derive(Clone, PartialEq, Debug)]
pub struct LanguageInfo {
    /// The English name: `return translation.get_language_info(lang_code)["name"]` (`i18n.py:294`).
    pub name: String,
    /// The local name: `return translation.get_language_info(lang_code)["name_local"]` (`i18n.py:305`).
    pub name_local: String,
    /// `["bidi"]` — a `bool`, and `language_bidi` returns it **as the bool**, not
    /// as a string (`i18n.py:310`).
    pub bidi: bool,
}

/// Which timezone the `tz` filters' one body is handed, as the source spells it.
///
/// `do_timezone(value, arg)` (`tz.py:38-84`) is **one** Python function under three
/// registered names: `localtime` passes `timezone.get_current_timezone()` (`tz.py:26`),
/// `utc` passes `datetime.UTC` (`tz.py:34`), and `timezone` *is* `do_timezone` under
/// `@register.filter("timezone")` (`tz.py:37-38`), passing the template's argument. They
/// differ only there, so the seam is one method and this enum is the argument.
pub enum TimezoneSource<'a> {
    /// `timezone.get_current_timezone()` — the `localtime` filter (`tz.py:26`).
    Current,
    /// `datetime.UTC` — the `utc` filter (`tz.py:34`).
    Utc,
    /// The template's argument — the `timezone` filter. `None` is Django's own
    /// missing-argument call `do_timezone(value)`, kept so the source's `TypeError` is
    /// what escapes if the parse-time guard is ever bypassed.
    Given(Option<&'a Value>),
}

/// Everything a filter body can ask of Python.
///
/// A method returns `Result<_, Error>` where Django's call can raise and a bare value
/// where it cannot. A body matches on `ErrorKind::FilterHost`'s `kind` — the Python
/// **class**, not a message — because that is what Django catches: `first` catches
/// `IndexError` and lets `TypeError` escape, `add` catches bare `Exception`,
/// `divisibleby` has no `try`. Flattening those to one error type would make three
/// filters unimplementable.
pub trait FilterHost {
    // --- the Python value protocol -----------------------------------------
    /// `str(v)`. `@stringfilter`'s wrapper does this once per filter
    /// (`defaultfilters.py:46`), and `unordered_list` does it again per item.
    fn to_str(&self, value: &Value) -> Result<String, Error>;
    /// `int(v)` — `get_digit`, `divisibleby` and the three padding filters. Python's
    /// `int` accepts `" 5 "`, `"5_0"` and `"٠٥"`, and is arbitrary-precision, so this is
    /// not a parse.
    fn to_int(&self, value: &Value) -> Result<IntValue, Error>;
    /// `float(v)` — `pluralize` (`defaultfilters.py:942-981`) and `floatformat`
    /// (`defaultfilters.py:94-220`).
    fn to_float(&self, value: &Value) -> Result<f64, Error>;
    /// `bool(v)` — `default` and `yesno`. For an `Opaque` this is `__bool__` or
    /// `__len__`, which Rust cannot see.
    fn is_true(&self, value: &Value) -> Result<bool, Error>;
    /// `len(v)` — `length` (`defaultfilters.py:633-639`), `pluralize`, `wordcount`'s fallback.
    fn len(&self, value: &Value) -> Result<usize, Error>;
    /// `list(v)`, **not** an index walk: `unordered_list`'s whole-value case is a
    /// generator (`test_unordered_list.py:143`), which has no `[]`. `first`/`last` must
    /// go through [`FilterHost::get_index`] instead, because Django's `except IndexError`
    /// lets a generator's `TypeError` escape.
    fn iter_values(&self, value: &Value) -> Result<Vec<Value>, Error>;
    /// `value[index]` — `first` and `last`, including the negative index and the
    /// `IndexError`/`TypeError` split.
    fn get_index(&self, value: &Value, index: i64) -> Result<Value, Error>;
    /// `value[start:stop:step]` — `slice`. Not [`FilterHost::get_index`] plus Rust
    /// slicing: `"abcdef"|slice:":3"` is the *string* `"abc"`, where the index route
    /// yields a list. Every component is `Option` because `"::2"` and `":3"` are both
    /// legal and Django passes `None` straight through to `slice(*bits)`.
    fn get_slice(
        &self,
        value: &Value,
        start: Option<i64>,
        stop: Option<i64>,
        step: Option<i64>,
    ) -> Result<Value, Error>;
    /// `a + b` — `add`: int-plus-int, then sequence concatenation, then a bare
    /// `except Exception` → `""`; all three arms are `__add__`, which no `Value` variant
    /// can answer for.
    fn add_values(&self, a: &Value, b: &Value) -> Result<Value, Error>;
    /// `format % value` — the `%` operator with the format string **already built**:
    /// `stringformat` passes `format!("%{}", to_str(arg))`, and `filesizeformat`'s five
    /// unit branches (`defaultfilters.py:927-935`) pass `gettext("%s KB")`'s
    /// **translation**. That second caller is why this takes the whole format rather than
    /// a suffix — a catalog may **move** the `%s`, and reassembling `"%s " + unit` would
    /// format the untranslated literal, an English-only divergence no corpus row can see.
    fn percent_format(&self, format: &str, value: &Value) -> Result<Value, Error>;
    /// `format % {name: size}` — the same operator with the **mapping** operand
    /// `filesizeformat`'s two byte branches use (`defaultfilters.py:908`, `defaultfilters.py:925`).
    ///
    /// A second method because the operand is not a `Value` at all: `{"size": n}` is a
    /// **dict**, interned as an `Opaque`. Measured, `"%(size)d bytes" % {"size": 1023}` is
    /// `"1023 bytes"` while the same expression with a list, tuple, int or str raises
    /// `TypeError: format requires a mapping`.
    fn percent_mapping(&self, format: &str, name: &str, size: &Value) -> Result<Value, Error>;
    /// `isinstance(value, SafeData)` — what `escape`, `force_escape`, `safeseq`, `cut`
    /// and `linebreaksbr` branch on, and what a Rust `Value::Safe` cannot answer for an
    /// `Opaque` carrying `__html__`.
    fn is_safe(&self, value: &Value) -> bool;
    /// `mark_safe(value)` (`safestring.py:64-78`). Measured: a value with `__html__` is
    /// returned **unchanged and not necessarily `SafeData`**; a non-str becomes
    /// `SafeString(str(v))`. No `SafeText` in 6.0.9, so a caller must not assume a
    /// `SafeString`.
    fn mark_safe(&self, value: Value) -> Value;

    // --- the escaping protocol: one call, four branches ---------------------
    /// `conditional_escape` (`django/utils/html.py:115-127`) — see §D4. One method,
    /// because two of its three branches are not `is_safe` questions:
    /// `isinstance(text, Promise)` and `hasattr(text, "__html__")`. Its docstring shares
    /// the convention with `markupsafe`, which implements `__html__` **without being
    /// `SafeData`**, so an `is_safe`-then-escape would get those objects wrong.
    fn conditional_escape(&self, value: &Value) -> Result<Value, Error>;

    // --- the one ordering operation Rust cannot express ---------------------
    /// `dictsort`/`dictsortreversed` (`defaultfilters.py:578-599`) with `_property_resolver`
    /// (`defaultfilters.py:540-575`). One method, not two: the float-vs-string branch, the private-name
    /// `AttributeError`, the five-exception `__getitem__`→`getattr` fallback and
    /// `dictsort`'s own `(AttributeError, TypeError)` → `""` are one semantic unit, and
    /// splitting them would put Python's exception classification on the Rust side — the
    /// thing this seam exists to avoid. Sorting `Opaque` objects by an extracted key is
    /// not expressible in Rust at all.
    ///
    /// `prop` is `&Value`, not `&str` (§D7.1): it is *not* `@stringfilter`-coerced, and
    /// `float(arg)` sends `1` and `'1'` down different branches — `dictsort(x, 1)` is the
    /// list, `dictsort(x, '1')` is `""`.
    fn sort_by_property(&self, value: &Value, prop: &Value, reverse: bool) -> Result<Value, Error>;

    // --- the type questions Rust cannot answer ------------------------------
    /// `stringformat`'s `isinstance(value, tuple)` (`defaultfilters.py:289`) — whether the operand
    /// is stringified before `%` sees it.
    ///
    /// Load-bearing, measured: `stringformat((1, 2, 3), "s")` is `"(1, 2, 3)"` *through this test*,
    /// where `"%s" % (1, 2, 3)` alone raises `TypeError` — which the filter catches, so its answer
    /// without the test is `""`. A **list** is the opposite case: CPython unpacks a **tuple**
    /// operand and treats every other sequence as a single value, so a Rust
    /// `matches!(value, Value::List(_))` would be exactly backwards.
    ///
    /// Rust cannot ask: a tuple crosses the FFI as `Value::Opaque`, and `isinstance` against an
    /// `Opaque` is a `__class__` walk the host owns (§D2).
    fn is_tuple(&self, value: &Value) -> bool;
    /// `unordered_list`'s nested-sequence test: the `isinstance` triple and the `iter()` probe
    /// (`defaultfilters.py:708-714`). `Some(items)` when **both** halves hold —
    /// `isinstance(next_item, (list, tuple, GeneratorType))` *and* `iter(next_item)` does not raise
    /// — else `None`, "a plain item".
    ///
    /// One method, not a predicate plus an iteration (§D7.1): Rust cannot answer the `isinstance`
    /// for an `Opaque` element, and an `iter_values`-shaped call could not represent a generator
    /// without *consuming* it — the very branch being consulted.
    ///
    /// Measured: `test_unordered_list.py:143`'s **whole value** is a generator, so
    /// [`FilterHost::iter_values`] must be `list(v)` and must not index, and `test_unordered_list.py:148`'s element is a
    /// generator, which must nest rather than render flat. The returned [`NestedSequence`] carries
    /// Django's `if children:` for the **object**, not the items it materialises.
    fn as_nested_sequence(&self, value: &Value) -> Result<Option<NestedSequence>, Error>;
    /// The comma-separated bits of a filter's argument — `pluralize`
    /// (`defaultfilters.py:965-966`) and `yesno` (`defaultfilters.py:879`) — as one call.
    ///
    /// `prepend_comma` is the whole of the difference: `pluralize` prefixes a comma to an argument
    /// that has none, so `pluralize(1)` has the bits `["", "s"]`, while `yesno` splits the argument
    /// as it stands, so `yesno(True, "yes")` has the single bit `["yes"]` and returns the **value**
    /// rather than a word.
    ///
    /// **One method rather than `contains` + concatenation + `split`** (§D7.1): the three are one
    /// question whose answer depends on the argument's type. Measured on the pinned tree, all
    /// reachable from template syntax:
    ///
    /// ```text
    /// {{ x|yesno:5 }}      -> AttributeError: 'int' object has no attribute 'split'
    /// {{ x|pluralize:5 }}  -> TypeError: argument of type 'int' is not a container or iterable
    /// pluralize(1, [1])    -> TypeError: can only concatenate str (not "list") to str
    /// pluralize(1, [","])  -> AttributeError: 'list' object has no attribute 'split'
    /// ```
    ///
    /// The last two settle it: `"," in [","]` is a **membership** test answering `True`, so that
    /// argument skips the concatenation and dies at the split. Which operation raises is the
    /// argument's type's decision.
    fn comma_bits(&self, value: &Value, prepend_comma: bool) -> Result<Vec<String>, Error>;

    // --- CPython string-method semantics ------------------------------------
    /// `str.lower()`. **Not a per-code-point table**, and that is the finding: measured,
    /// `"ΟΣ".lower()` is `"ος"`, not `"οσ"` — Unicode's context-dependent **Final_Sigma**
    /// rule, which `pyunicode::case`'s exhaustive per-code-point diff cannot see. Rust's
    /// `str::to_lowercase` implements the rule but disagrees with Python at the 28
    /// suppressed code points, and the two halves cannot be combined without either
    /// replicating Final_Sigma (guardable only by sampling) or leaving a silent divergence.
    fn str_lower(&self, text: &str) -> String;
    /// `str.title()`. Needs three things Rust has no equivalent of: Unicode's `Cased`
    /// property (4,578 code points, not exposed by `unicodedata`), a **135-entry**
    /// titlecase table distinct from uppercase, and the same Final_Sigma rule. Measured: a
    /// from-first-principles rule reproduced all 1,112,064 single code points and still got
    /// 444 of 400,000 random strings wrong, every one a final sigma.
    fn str_title(&self, text: &str) -> String;

    // --- engines whose state is not the filter's argument -------------------
    /// `django.utils.html.strip_tags` — `HTMLParser`-based (`html.py:211-229`), so the tag
    /// grammar, the entity handling and the malformed-input behaviour are all Python's.
    ///
    /// **Fallible, and the measurement is one call**: `strip_tags("<a" + "<" * 49 + "x" *
    /// 1000)` raises `SuspiciousOperation` (empty message) out of `HTMLParser`'s depth limit
    /// (`MAX_STRIP_TAGS_DEPTH = 50`, `html.py:44`). The input is ASCII, so nothing at the FFI
    /// boundary stops it, and Django's own `striptags` body has no `try` around the call —
    /// the raise **is** that filter's answer on such a value. A bare `String` would have to
    /// swallow it or panic.
    fn strip_tags(&self, text: &str) -> Result<String, Error>;
    /// `django.utils.text._slugify` (`text.py:473-476`), **whole**: NFKD, the
    /// combining-mark strip, the two `re.sub`s and the `.lower()`.
    ///
    /// Whole rather than a `normalize_nfkd` the body would wrap, because the lowercasing
    /// inside it is Final_Sigma — the reason [`FilterHost::str_lower`] exists — and because a
    /// split would leave `unicode_normalize_nfkd` with `slugify` as its only caller.
    fn slugify(&self, text: &str) -> String;
    /// `django.utils.text.phone2numeric` (`text.py:313-344`), **whole** — the 26-entry table and
    /// the `.lower()` both.
    ///
    /// **The one helper here reached through a decorator with no `@stringfilter` in between**,
    /// which fixes the argument's shape: `phone2numeric` carries `@keep_lazy_text` (`text.py:313`),
    /// so the registered function is not the `def` body. A `Promise` argument yields a *lazy*
    /// result (`__proxy__` out, measured); any other non-`str` is refused by `phone.lower()`, **its
    /// own class named** (`'dict' object has no attribute 'lower'`) where Rust can only say
    /// `'object'`. Neither is expressible through a `&str`, so this takes and returns a [`Value`];
    /// a `SafeString` marker does **not** survive (measured).
    ///
    /// Per §D2's third clause, **a body must call the host for a helper whose `def` carries a
    /// result-changing decorator rather than reproduce it**; `tests/test_filter_inventory.py`
    /// asserts a verdict for each of the five such `(filter, helper)` pairs.
    fn phone2numeric(&self, value: &Value) -> Result<Value, Error>;
    /// `django.utils.html.urlize` — a regex over `settings.URLIZE_ASSUME_HTTPS`, so the setting and
    /// the regex engine are both the host's.
    ///
    /// **`limit` is `Option<&IntValue>`, not an `Option<usize>`, because that type cannot express
    /// two of the answers the corpus reaches.** `Urlizer.trim_url` (`html.py:407-410`) compares the
    /// limit against a length and slices (`x[: max(0, limit - 1)]`), both taking any Python int:
    /// measured through `{{ x|urlizetrunc:… }}`, `0`, `-1` and `-5` all answer `'…'`, and a 30-digit
    /// literal is untruncated. §D2's sentinel rule — *a signature is the wrapped function's
    /// signature, defaults and sentinels included*.
    ///
    /// **The `int()` is not this method's**: `urlizetrunc`'s body does it before the call
    /// (`defaultfilters.py:399`), so `int("abc")`'s `ValueError` and `int(None)`'s `TypeError` come
    /// from [`FilterHost::to_int`]. `None` here is `urlize`'s own call site
    /// (`defaultfilters.py:386`). `nofollow=True` is not a parameter because both call sites
    /// hardcode it.
    fn urlize(&self, text: &str, limit: Option<&IntValue>, autoescape: bool) -> String;
    /// `Truncator.chars` / `.words` (`text.py:193-261`), **whole**. Delegated rather than
    /// split because even the non-html path needs `unicodedata.normalize("NFC")`,
    /// `unicodedata.combining` per character (`text.py:205`, `text.py:220`) and the translatable
    /// marker `add_truncation_text` falls back to — so a Rust `chars` would need a **third**
    /// Unicode table diverging from CPython's. `truncate_text` is Django's `truncate`
    /// argument; `None` is its default.
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
    /// `django.utils.encoding.iri_to_uri`; `iriencode`'s whole body is this one call
    /// (`defaultfilters.py:225-227`).
    ///
    /// **Infallible by an audit rather than by assumption**: the one input that makes it
    /// raise is a lone surrogate — `iri_to_uri("\ud800")` is a `UnicodeEncodeError` from the
    /// UTF-8 encoding `quote` does — and a `&str` argument cannot carry one, so no body can
    /// reach that raise. Measured: `{{ x|iriencode }}` with `x = "\ud800"` raises at the FFI
    /// in both engines.
    fn iri_to_uri(&self, text: &str) -> String;
    /// `urllib.parse.quote` called the way `urlencode`'s body calls it
    /// (`defaultfilters.py:365-379`): with the `safe` keyword **omitted** when `safe` is `None`,
    /// otherwise with that object passed through as it is.
    ///
    /// `safe` is `Option<&Value>`, not `Option<&str>` (§D7.1): `quote` observes the argument's
    /// *type*, not its text. `isinstance(safe, str)` selects the `safe.encode('ascii', 'ignore')`
    /// branch; every other object goes to `bytes([c for c in safe if c < 128])`, which **answers**
    /// for an all-`int` container (measured `safe=[1]` is `'a%20b%2Fc'`), for `bytes`
    /// (`safe=b'/'` is `'a%20b/c'`) and for `[]`, and refuses with a *different* class or message
    /// for `[-1]`, `[1.0]` and `['/']`. A `&str` parameter would force the body to run that
    /// dispatch in Rust.
    ///
    /// **Fallible**, unlike `iri_to_uri`, on the non-`str` branch. `None` covers two Django
    /// tokens — the absent argument and an explicit `None` — since the body's `if safe is not
    /// None` leaves `kwargs` empty for both.
    fn quote(&self, text: &str, safe: Option<&Value>) -> Result<String, Error>;
    /// The `json.dumps` inside `django.utils.html.json_script` (`html.py:103`):
    /// **`json.dumps(value, cls=DjangoJSONEncoder)`**, whole, with `json`'s own default separators
    /// `(", ", ": ")` and not `(",", ":")`.
    ///
    /// Both halves are load-bearing: the encoder is what makes a `date`, `Decimal` or `UUID`
    /// serialisable rather than a `TypeError` (measured `json_script({"d": date(2026, 10, 8)})` is
    /// `'{"d": "2026-10-08"}'`). The escaping and the `<script>` wrapping are the caller's.
    fn json_dumps(&self, value: &Value) -> Result<String, Error>;
    /// `pprint.pformat` — `pprint` (`defaultfilters.py:990-996`).
    fn pformat(&self, value: &Value) -> Result<String, Error>;
    /// `random.choice` — `random` (`defaultfilters.py:642-648`), which is stateful and not a
    /// function of its argument.
    fn random_choice(&self, value: &Value) -> Result<Value, Error>;

    // --- locale, language and the clock -------------------------------------
    /// `django.utils.timezone.template_localtime(value, use_tz=…)` — `date` and `time`.
    ///
    /// **`use_tz` is `Context.use_tz`, not `settings.USE_TZ`** (`base.py:811`): `{% localtime off %}`
    /// flips the former, the field's whole reason for existing, so a host reading the setting would
    /// ignore the tag. `Context.use_tz` **defaults to `None`**, read as "consult the setting"
    /// (`timezone.py:155-158`), which a `bool` cannot spell — the caller resolves it, safe **only
    /// because the substitution is exactly the callee's own expression**.
    fn localtime(&self, value: Value, use_tz: bool) -> Result<Value, Error>;
    /// `do_timezone(value, …)` (`tz.py:38-84`), under the three `tz` filters' three spellings — see
    /// [`TimezoneSource`].
    ///
    /// **The body stays Python-side, under the seam**: measured, the three `tz` tag bodies call
    /// `do_timezone` **zero** times; its only callers are the `localtime` and `utc` *filters*
    /// (`tz.py:26`, `tz.py:34`), so there is no shared-helper case to port. Each Rust body's whole
    /// content is this call.
    fn do_timezone(&self, value: Value, source: TimezoneSource<'_>) -> Result<Value, Error>;
    /// `django.utils.formats.date_format` — the active language's formats.
    ///
    /// **`format` is an `Option` because Django's default is spelled `None`, and `None` and the
    /// literal `"DATE_FORMAT"` are the *same call*.** The body is
    /// `dateformat.format(value, get_format(format or "DATE_FORMAT", use_l10n))`
    /// (`formats.py:155-157`). Measured on `date(2026, 10, 8)` under `LANGUAGE_CODE="de"`:
    ///
    /// ```text
    /// get_format("DATE_FORMAT")            = 'j. F Y'
    /// date_format(d, None)                 = '8. Oktober 2026'
    /// date_format(d, settings.DATE_FORMAT) = 'Okt. 8, 2026'   <- diverges
    /// ```
    ///
    /// So **`settings.DATE_FORMAT` is not the default**: it is `en-us`'s, and a caller resolving the
    /// default itself would be right in `en-us` and wrong in every language that has a format module.
    fn date_format(&self, value: &Value, format: Option<&str>) -> Result<String, Error>;
    /// `django.utils.formats.time_format`, the same shape and the same reason: its body is
    /// `get_format(format or "TIME_FORMAT", use_l10n)`.
    fn time_format(&self, value: &Value, format: Option<&str>) -> Result<String, Error>;
    /// `django.utils.dateformat.format` (`dateformat.py:326-329`) — the **second** branch of
    /// `date`, where the argument is used literally rather than resolved as a format setting.
    ///
    /// **`format` is a `&Value` where [`Self::date_format`]'s is an `Option<&str>` — the wrapped
    /// signature, not an inconsistency.** `formats.date_format` hands its argument to
    /// `get_format(arg or "DATE_FORMAT")`, a **lookup key** with `None` as the sentinel;
    /// `dateformat.format` instead `str()`s any object and splits the text (`dateformat.py:42`), so
    /// an absent argument is the text `"None"` — four live format characters. Measured on
    /// `datetime(2026, 10, 8, 14, 30, 45)`:
    ///
    /// ```text
    /// dateformat.format(dt, None)  = 'Oct.202610'
    /// dateformat.format(dt, True)  = 'UTCThu, 08 Oct 2026 14:30:45 +0000000000'
    /// dateformat.format(dt, 5)     = '5'
    /// ```
    ///
    /// A `&str` seam would make the Rust body compute `str(None)` itself.
    fn dateformat_format(&self, value: &Value, format: &Value) -> Result<String, Error>;
    /// `django.utils.dateformat.time_format` (`dateformat.py:332-335`) — the second branch of
    /// `time`, the same shape and the same reason as [`Self::dateformat_format`].
    ///
    /// It differs from the `date` twin on the absent argument, and that is a second reason
    /// the return is a `Result`: `TimeFormat` has no `N`, so
    /// `dateformat.time_format(t, None)` raises where the `date` twin answers —
    /// `AttributeError: 'TimeFormat' object has no attribute 'N'`.
    fn dateformat_time_format(&self, value: &Value, format: &Value) -> Result<String, Error>;
    /// `django.utils.timesince.timesince`, over the **reachable** parameter set.
    ///
    /// Django's signature is five — `(d, now=None, reversed=False, time_strings=None, depth=2)`
    /// (`timesince.py:28`); this seam expresses three, because `time_strings` and `depth` have
    /// exactly two Rust callers (`timesince.rs`, `timeuntil.rs`), both passing the three that
    /// remain — the template reaches this helper only through `timesince_filter` and
    /// `timeuntil_filter` (`defaultfilters.py:812`, `defaultfilters.py:825`). A caller that ever needs a custom
    /// `depth` must widen this first.
    ///
    /// `timeuntil` is not `timesince` with the arguments swapped: Django's
    /// `timesince(d, now, reversed=False)` swaps `d` and `now` *after* normalising the timezones
    /// (`timesince.py:67-70`), so `now.astimezone(d.tzinfo)` expresses one operand in the other's
    /// zone and the calendar fields `total_months` reads (`timesince.py:83`) are zone-dependent. A caller-side
    /// swap normalises the opposite way and reports different months for two aware datetimes in
    /// different zones. `now` is an `Option` for the same reason: it is filled from `d.tzinfo`
    /// *before* the swap, so a caller-side swap would pass `d = None` and die at `timesince.py:62`
    /// on `d.year`.
    ///
    /// **It returns a `Result`, for the same reason `strip_tags` does.** The helper raises
    /// `AttributeError` on a value with no date protocol (`timesince("x")` is `'str' object has no
    /// attribute 'year'`) and `timesince_filter` does **not** catch it: its
    /// `except (ValueError, TypeError)` lets that class through while catching the mixed-awareness
    /// `TypeError` and answering `''`.
    fn timesince(&self, d: &Value, now: Option<&Value>, reversed: bool) -> Result<String, Error>;
    /// `django.utils.formats.number_format`, in the three shapes the two callers
    /// use. The struct is [`NumberFormatArgs`].
    fn number_format(&self, args: NumberFormatArgs<'_>) -> String;
    /// `gettext` — the active language, and `settings.USE_I18N`.
    fn gettext(&self, text: &str) -> String;
    /// `ngettext` — `gettext`'s plural form.
    ///
    /// `n` is a `u64` where Python's is arbitrary-precision. Both call sites are
    /// `filesizeformat`'s (`defaultfilters.py:908`, `defaultfilters.py:925`) and the only non-literal one is
    /// the `bytes_ < KB` arm, so **`n < 1024`** holds; the narrowness is bounded by the *call
    /// site*, and a wider caller must widen this first.
    fn ngettext(&self, singular: &str, plural: &str, n: u64) -> String;
    /// `django.utils.translation.get_language_info(lang_code)`, the four `language_*`
    /// filters' single lookup each (`i18n.py:292-310`).
    ///
    /// The language table is `django.conf.locale.LANG_INFO` — 107 entries of
    /// `name`/`name_local`/`bidi` data — so the lookup is the host's for §D2's reason: it is
    /// locale data, not a function of the filter's argument. `Result` because the call raises
    /// `KeyError("Unknown language code …")` for a code that is neither a key nor resolvable
    /// through its first subtag, and **none of the four filters catches it**.
    fn language_info(&self, lang_code: &Value) -> Result<LanguageInfo, Error>;
    /// `str(formats.localize(value, use_l10n=…))` — `localize` and `unlocalize`
    /// (`l10n.py:7-21`), which differ only in the `use_l10n` they force.
    ///
    /// **The whole source expression, `str()` included, because that `str()` is not
    /// [`FilterHost::to_str`].** Measured, `str(formats.localize(mark_safe("123"), use_l10n=True))`
    /// is a **`SafeString`** — `localize` returns a `str` unchanged — while `to_str` hands back a
    /// plain `String` the FFI turns into `Value::Str`. `localize` is `is_safe=False` (`l10n.py:7`),
    /// so the loop does not re-mark and the two would *render* differently: ours would escape where
    /// Django does not.
    fn localize(&self, value: &Value, use_l10n: bool) -> Result<Value, Error>;
    /// `formats.number_format(round(value, 1), 1)` whole (`defaultfilters.py:911-912`).
    ///
    /// A host call rather than [`FilterHost::number_format`] over a Rust-rounded argument,
    /// because Python's two-argument `round` on a **float** rounds against the exact binary
    /// value: measured `round(0.35, 1)` is `0.3` (the exact value is `0.34999999999999997780…`)
    /// where the decimal tie-break gives `0.4`.
    fn filesize_number_format(&self, value: f64) -> String;
    // --- operand conversions at the source's own statement boundary --------
    // A body that calls one has not thereby handed its *algorithm* over. See `.review/m4-seams.md`.
    /// `Decimal(str(value))`, then Django's `Decimal(str(float(value)))` retry
    /// (`defaultfilters.py:152-159`) — `floatformat`.
    ///
    /// **The decimal engine is host-side** (§D2): "what a `decimal.Context` is set to" is not a
    /// function of the filter's arguments, so this method and the next are the two halves it
    /// crosses the seam as.
    fn decimal_parse(&self, value: &Value) -> Result<DecimalParse, Error>;

    /// `defaultfilters.py:194-205`: `exp = Decimal(1).scaleb(-abs(p))`, the `prec` computation
    /// and `d.quantize(exp, ROUND_HALF_UP, Context(prec=prec))`, as **one** call.
    ///
    /// Not three, because `prec = max(getcontext().prec, prec)` (`defaultfilters.py:200`) is a read
    /// of thread-local state that *bounds the quantize at `defaultfilters.py:204`*: splitting the method would invite
    /// a Rust caller to compute its own `prec`. The context does reach the answer, measurably —
    /// below `2*(Emax + prec)` the `scaleb` clamps to `Decimal('0E-' + Etiny)` (the rounding
    /// *position* is 999999 whatever `p` is), and above it the same call raises `InvalidOperation`
    /// out of a `try` that does not exist.
    ///
    /// `places` is `abs(p)`; `integral` is [`ParsedDecimal::integral`], passed rather than recomputed
    /// so the source's single `int(d) - d` stays single.
    fn decimal_quantize(
        &self,
        decimal: &Value,
        places: u64,
        integral: bool,
    ) -> Result<DecimalTuple, Error>;

    /// `bytes_ / (1 << shift)` — the division half of `filesizeformat`
    /// (`defaultfilters.py:914-925`), where `bytes_` is the already-converted `int`, `KB =
    /// 1 << 10` and the branches `MB`, `GB`, `TB`, `PB` are `1 << 20`, `1 << 30`, `1 << 40`,
    /// `1 << 50`.
    ///
    /// **It takes the `int`, not the `Value`, and that is the whole reason it is a seam.**
    /// `filesizeformat` calls `int()` **once** (`defaultfilters.py:906`), and the body needs the
    /// result before this call to choose its branch (`bytes_ < KB`) and its sign (`negative =
    /// bytes_ < 0`). A `&Value` signature would convert twice — two answers for an object whose
    /// `__int__` has side effects. Taking the `IntValue` puts the method at the division alone, the
    /// source's own boundary: the `int()` sits inside the `try` at `defaultfilters.py:905-907` (its
    /// `ValueError`/`TypeError` become `'0 bytes'`) and the division sits **outside** it, so its
    /// `OverflowError` escapes.
    ///
    /// The division must run in Python, not over two `f64`s: CPython's `int.__truediv__` is a
    /// correctly-rounded long division that does not convert its operands, which is what makes the
    /// messages below differ. **Measured on the pinned interpreter** — five shapes fixing the
    /// boundary, none reachable from the suite:
    ///
    /// | expression | outcome |
    /// |---|---|
    /// | `filesizeformat(inf)` | `OverflowError: cannot convert float infinity to integer` — from `int()`, inside the `try`, so **not** caught (the `except` names `(TypeError, ValueError, UnicodeDecodeError)`) |
    /// | `filesizeformat(nan)` | `'0\xa0bytes'` — `int(nan)` is a `ValueError`, which **is** caught |
    /// | `10**400 / 2**50` | `OverflowError: integer division result too large for a float` |
    /// | `float(10**400)` | `OverflowError: int too large to convert to float` |
    /// | `number_format(inf, 1)` | `'inf.0'` |
    ///
    /// The last three are why an `f64` cannot answer this (a >308-digit decimal parses to `inf`, and
    /// `inf.0` formats to a plausible, silently wrong `'inf.0\xa0PB'` where Django raises); the last
    /// two are why [`FilterHost::to_float`] cannot either (both raise `OverflowError` and the
    /// **messages differ**). Not folded into [`FilterHost::filesize_number_format`]: the fusion rule
    /// is for operations with non-separable failures, and here they separate.
    fn int_divide_by_power_of_two(&self, value: &IntValue, shift: u32) -> Result<f64, Error>;

    /// `int(a) % int(b)` — `divisibleby` (`defaultfilters.py:854-857`), which has **no** `try`,
    /// so `b == 0` is a `ZeroDivisionError` and a non-numeric operand a `ValueError` or
    /// `TypeError`; all three escape the filter.
    ///
    /// Rust compares the answer to zero, a `Small(0)` test: an `IntValue::Big` is canonical
    /// decimal text and never zero.
    fn int_modulo(&self, a: &Value, b: &Value) -> Result<IntValue, Error>;
}

/// The refusal [`NoFilterHost`] gives, and the one [`TestFilterHost`] gives for an operation
/// it was not scripted for.
///
/// One string produced in one place, because the two hosts differ only in *which* operations
/// they can answer: a test must not be able to tell which one refused from the message.
fn refused(operation: &'static str) -> Error {
    Error::host(format!("no host answers `{operation}`"))
}

/// The panic the 14 non-`Result` operations give when nobody answers them.
///
/// They cannot return the refusal as a value, so the refusal is the panic's message — and it
/// is the same text [`refused`] produces.
fn refused_panic(operation: &'static str) -> ! {
    panic!("{}", refused(operation).message())
}

/// A host that answers nothing: the one a filter module's bare-shell unit test uses when its
/// filter must be pure.
///
/// Every operation refuses, so **a filter that needs the host cannot pass a `cargo test -p
/// template-core` test by accident** — the failure names the operation, which is the signal
/// either that the body belongs on the other side of §D2's line or that the test needs a
/// [`TestFilterHost`].
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

    fn do_timezone(&self, _value: Value, _source: TimezoneSource<'_>) -> Result<Value, Error> {
        Err(refused("do_timezone"))
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

    fn language_info(&self, _lang_code: &Value) -> Result<LanguageInfo, Error> {
        Err(refused("language_info"))
    }

    fn localize(&self, _value: &Value, _use_l10n: bool) -> Result<Value, Error> {
        Err(refused("localize"))
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
}

/// A scripted host for a filter module's own `#[cfg(test)] mod tests`.
///
/// Every operation answers from its own `Option` field when set, otherwise refuses exactly as
/// [`NoFilterHost`] does — so **a test cannot pass by leaving a field unset** — and every call
/// is recorded, so a test can assert the body *reached* the seam. That second half is what §11
/// needs: a delegating filter whose test never checks [`TestFilterHost::calls`] can be
/// reimplemented in Rust and still pass.
///
/// The field order is the trait's order. Non-`Result` operations (the 14 that return a bare
/// `String`, `bool` or `Value`) panic when unset rather than defaulting, for the same reason:
/// a sentinel could silently satisfy an assertion.
///
/// `#[cfg(test)]`, because only a test needs it: this crate's `cargo test` compiles every
/// `#[cfg(test)] mod tests` in `filters/<name>.rs` against the same crate, so it is visible
/// everywhere an agent's filter tests live. Shipping it in the library would put 45 `Option`
/// fields and two `RefCell`s in the real build, reachable from a non-test path. The one cost is
/// that a *doc* test cannot demonstrate it — doc tests link against the crate compiled normally
/// — so its demonstrations live in the `mod tests` below.
#[cfg(test)]
#[derive(Default)]
pub struct TestFilterHost {
    // Crate-visible rather than private: functional-update syntax
    // (`..TestFilterHost::default()`) requires *every* field to be visible at the construction
    // site, so two private fields would stop a filter module's test from scripting any of the
    // 45 answers — the struct's whole purpose. Reading a recorded call goes through
    // [`TestFilterHost::calls`] and [`TestFilterHost::assert_called`]; scripting a failure
    // through [`TestFilterHost::raise_on`]. Reaching past those to these `RefCell`s is possible
    // here where it was not before — the trade this struct's first real users forced, whose
    // worst case is a test that fabricates the evidence it then asserts on, which a reviewer
    // sees in the diff.
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
    /// `Some(None)` is a **scripted** "plain item"; `None` is "not scripted" — the outer
    /// layer is the scripted/unscripted question, not a third meaning.
    pub as_nested_sequence: Option<Option<NestedSequence>>,
    pub comma_bits: Option<Vec<String>>,
    pub str_lower: Option<String>,
    pub str_title: Option<String>,
    pub strip_tags: Option<String>,
    pub slugify: Option<String>,
    /// A `Value` rather than a `String`: the helper's answer can be a lazy proxy.
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
    /// The four `language_*` filters' one lookup, in the trait's return shape.
    pub language_info: Option<LanguageInfo>,
    /// `str(formats.localize(value, use_l10n=…))`, whose safety the host owns.
    pub localize: Option<Value>,
    /// `do_timezone(value, …)` — the three `tz` filters' one seam.
    pub do_timezone: Option<Value>,
}

#[cfg(test)]
impl TestFilterHost {
    /// The operation names this host was asked for, in order.
    pub fn calls(&self) -> Vec<&'static str> {
        self.calls.borrow().clone()
    }

    /// Panics naming `name` if it is absent, printing the calls it did see.
    ///
    /// The half of a delegating filter's test a Rust reimplementation cannot satisfy, so a
    /// body that stops calling the host fails here even when its output is still right.
    pub fn assert_called(&self, name: &str) {
        let calls = self.calls();
        assert!(
            calls.contains(&name),
            "the body never reached `{name}`; it called {calls:?}"
        );
    }

    /// Make `operation` raise `kind` for this host's remaining life.
    ///
    /// The taxonomy is the point of `ErrorKind::FilterHost`, and the filters that depend on it
    /// — `first`'s `IndexError` → `""` against its escaping `TypeError`, `add`'s bare `except
    /// Exception`, `get_digit`'s `ValueError` → the original value, `divisibleby`'s escaping
    /// `ZeroDivisionError` — are branches no host-free test can reach. Scripting the class is
    /// what makes them testable in a bare shell.
    pub fn raise_on(&self, operation: &'static str, kind: PyErrorKind) {
        self.raises.borrow_mut().push((operation, kind));
    }

    /// Records the call and returns the scripted failure, if any.
    ///
    /// **The push is first, deliberately**: recording after the read would skip the record on
    /// the path that refuses, so "the body reached the seam" would pass or fail depending on
    /// what the body did with the answer.
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

    fn do_timezone(&self, _value: Value, _source: TimezoneSource<'_>) -> Result<Value, Error> {
        self.record("do_timezone")?;
        self.do_timezone
            .clone()
            .ok_or_else(|| refused("do_timezone"))
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

    fn language_info(&self, _lang_code: &Value) -> Result<LanguageInfo, Error> {
        self.record("language_info")?;
        self.language_info
            .clone()
            .ok_or_else(|| refused("language_info"))
    }

    fn localize(&self, _value: &Value, _use_l10n: bool) -> Result<Value, Error> {
        self.record("localize")?;
        self.localize.clone().ok_or_else(|| refused("localize"))
    }

    fn decimal_parse(&self, _value: &Value) -> Result<DecimalParse, Error> {
        self.record("decimal_parse")?;
        // `Unparseable` is a *value*, not an error: only the outer `Option` is "not scripted",
        // so collapsing the two would make the unparseable arm untestable in a bare shell — and
        // that arm is the one the `float`-retry exists for.
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
                Error::host(format!("no host answers `{operation}`")).kind,
                "an unset `{operation}` must refuse with `Error::host`, not \
                 default to a value"
            );
        }
    }

    /// The same property on the non-`Result` half, where the refusal is a panic. Observed by
    /// catching it: the message names the operation, which makes a bare-shell failure
    /// diagnosable.
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

    /// Every `Result` operation of `NoFilterHost`, as `(name, refusal)`.
    ///
    /// A free function so the completeness test below can walk the same list against the trait
    /// that declares the methods; the name/refusal pairing is explicit so a check has something
    /// to compare with.
    fn refused_result_operations() -> Vec<(&'static str, Error)> {
        let host = &NoFilterHost;
        let v = Value::None;
        vec![
            ("to_str", host.to_str(&v).unwrap_err()),
            ("to_int", host.to_int(&v).unwrap_err()),
            ("to_float", host.to_float(&v).unwrap_err()),
            ("is_true", host.is_true(&v).unwrap_err()),
            ("len", host.len(&v).unwrap_err()),
            ("iter_values", host.iter_values(&v).unwrap_err()),
            ("get_index", host.get_index(&v, 0).unwrap_err()),
            (
                "get_slice",
                host.get_slice(&v, None, None, None).unwrap_err(),
            ),
            ("add_values", host.add_values(&v, &v).unwrap_err()),
            ("percent_format", host.percent_format("%s", &v).unwrap_err()),
            (
                "percent_mapping",
                host.percent_mapping("%(size)d", "size", &v).unwrap_err(),
            ),
            (
                "conditional_escape",
                host.conditional_escape(&v).unwrap_err(),
            ),
            (
                "sort_by_property",
                host.sort_by_property(&v, &v, false).unwrap_err(),
            ),
            (
                "as_nested_sequence",
                host.as_nested_sequence(&v).unwrap_err(),
            ),
            ("comma_bits", host.comma_bits(&v, true).unwrap_err()),
            ("strip_tags", host.strip_tags("x").unwrap_err()),
            ("phone2numeric", host.phone2numeric(&v).unwrap_err()),
            ("quote", host.quote("x", None).unwrap_err()),
            ("json_dumps", host.json_dumps(&v).unwrap_err()),
            ("pformat", host.pformat(&v).unwrap_err()),
            ("random_choice", host.random_choice(&v).unwrap_err()),
            ("localtime", host.localtime(v.clone(), false).unwrap_err()),
            (
                "do_timezone",
                host.do_timezone(v.clone(), TimezoneSource::Current)
                    .unwrap_err(),
            ),
            ("date_format", host.date_format(&v, Some("Y")).unwrap_err()),
            ("time_format", host.time_format(&v, Some("H")).unwrap_err()),
            (
                "dateformat_format",
                host.dateformat_format(&v, &v).unwrap_err(),
            ),
            (
                "dateformat_time_format",
                host.dateformat_time_format(&v, &v).unwrap_err(),
            ),
            ("timesince", host.timesince(&v, None, false).unwrap_err()),
            ("language_info", host.language_info(&v).unwrap_err()),
            ("localize", host.localize(&v, true).unwrap_err()),
            ("decimal_parse", host.decimal_parse(&v).unwrap_err()),
            (
                "decimal_quantize",
                host.decimal_quantize(&v, 2, false).unwrap_err(),
            ),
            (
                "int_divide_by_power_of_two",
                host.int_divide_by_power_of_two(&IntValue::Small(1), 1)
                    .unwrap_err(),
            ),
            ("int_modulo", host.int_modulo(&v, &v).unwrap_err()),
        ]
    }

    /// Every `Result` operation of `NoFilterHost` refuses, under its own name.
    ///
    /// The whole list rather than a sample: one method accidentally returning `Ok` would be a
    /// filter body silently reaching a default, and the per-method name makes a wrong dispatch
    /// visible.
    #[test]
    fn no_filter_host_refuses_every_result_operation() {
        let names = refused_result_operations();
        for (name, error) in names {
            match error.kind {
                ErrorKind::Host(host) => {
                    assert!(
                        host.message.starts_with("no host answers `"),
                        "{name}: {}",
                        host.message
                    );
                }
                other => {
                    panic!("NoFilterHost must refuse {name} with ErrorKind::Host, got {other:?}")
                }
            }
        }
    }

    /// The list above **is** the trait's `Result` half — derived from the trait, not counted.
    ///
    /// A length check against the list itself could never see an omission, and this one's
    /// predecessor did not: it was seven entries short of the trait's 31 `Result` methods while
    /// the docstring above it read "the whole 25 rather than a sample". This asks `host.rs`
    /// itself.
    #[test]
    fn the_refusal_list_covers_every_result_operation() {
        let mut declared: Vec<String> = Vec::new();
        let mut signature = String::new();
        let mut in_trait = false;
        for line in include_str!("host.rs").lines() {
            if line.starts_with("pub trait FilterHost") {
                in_trait = true;
                continue;
            }
            if !in_trait {
                continue;
            }
            if line == "}" {
                break;
            }
            let trimmed = line.trim();
            if let Some(rest) = trimmed.strip_prefix("fn ") {
                signature = rest.to_string();
            } else if signature.is_empty() {
                continue;
            } else {
                signature.push_str(trimmed);
            }
            if trimmed.ends_with(';') {
                if signature.contains("Result<") {
                    declared.push(
                        signature
                            .split('(')
                            .next()
                            .expect("a signature always has a name")
                            .to_string(),
                    );
                }
                signature.clear();
            }
        }
        let listed: Vec<String> = refused_result_operations()
            .into_iter()
            .map(|(name, _)| name.to_string())
            .collect();
        assert_eq!(
            listed, declared,
            "the refusal list and the trait's `Result` methods must be the same \
             names in the same order"
        );
        assert_eq!(declared.len(), 34, "the `Result` half of the trait is 34");
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

        // A value *is* scripted, so this asserts the failure wins: a host that returned the
        // answer would make `get_digit`'s `except ValueError` branch unreachable from a test.
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

    /// **The `calls()` property.** Move the `push` in `record` below the `ok_or_else` and this
    /// fails: the refusal path takes the `?` before anything is recorded, so `calls()` is
    /// empty.
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

    /// Order is part of the record: a body calling the seam twice in the wrong order is a
    /// different bug from one calling it once.
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

    /// `assert_called` is the guard a Rust reimplementation cannot satisfy, so it has to fail
    /// when it should — observed, not assumed.
    #[test]
    fn assert_called_names_what_it_saw() {
        let host = TestFilterHost::default();
        // Recorded through a *refusing* operation, which also exercises the recording path a
        // Rust reimplementation would silently drop.
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

    /// Both hosts describe themselves the same way, so a test cannot tell which one refused
    /// from the message alone.
    #[test]
    fn the_two_hosts_refuse_in_the_same_words() {
        let scripted = TestFilterHost::default().to_str(&Value::None).unwrap_err();
        let absent = NoFilterHost.to_str(&Value::None).unwrap_err();
        assert_eq!(scripted.kind, absent.kind);
        assert_eq!(scripted.kind, Error::host("no host answers `to_str`").kind);
    }

    /// `FilterCall` is the only thing a body is handed, and §D1 says it is one bool rather than
    /// the context. Asserted so a later milestone adding the context here must delete a test
    /// that says why it should not.
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
