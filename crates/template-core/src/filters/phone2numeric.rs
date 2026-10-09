//! `phone2numeric` — `defaultfilters.py:984-987`.
//!
//! ```python
//! @register.filter("phone2numeric", is_safe=True)
//! def phone2numeric_filter(value):
//!     """Take a phone number and converts it in to its numerical equivalent."""
//!     return phone2numeric(value)
//! ```
//!
//! and the helper it calls, `django.utils.text.phone2numeric`
//! (`.django/django/utils/text.py:313-344`), is a 26-entry a–z table and one
//! expression — **not** a `str.translate`:
//!
//! ```python
//! @keep_lazy_text
//! def phone2numeric(phone):
//!     """Convert a phone number with letters into its numeric equivalent."""
//!     char2number = { "a": "2", … "z": "9" }
//!     return "".join(char2number.get(c, c) for c in phone.lower())
//! ```
//!
//! **No `@stringfilter`** — it is one of the four the 29 exclude (see
//! `tests/test_filter_inventory.py`'s `STRINGFILTER`), so the argument is the raw
//! [`Value`] and its *type* is observed rather than coerced. `phone.lower()`
//! (`text.py:344`) is a `str` method, so `phone2numeric(5)` is an
//! `AttributeError` and not `"5"`; the body reads `Value::Str`/`Value::Safe`
//! directly and refuses everything else, the same shape `cut`'s argument takes
//! (§D7.1's `&Value` case: the Python function observes the argument's type).
//!
//! **The lowercasing is the host's, and the plan's "pure, so it is Rust's" is
//! wrong.** `phone.lower()` is the same Final_Sigma / 28-suppressed-code-point
//! operation [`FilterHost::str_lower`] exists for — measured, `phone2numeric("ΟΣ")`
//! is `"ος"`, not `"οσ"` — so the body delegates it and owns only the table. A
//! Rust `str::to_lowercase` would agree with Python on this input and disagree at
//! the 28 code points, which is exactly why that method's doc says delegating is
//! "complete by construction". The plan's "`str.translate` over a fixed
//! 26-letter table" is wrong twice: the pinned helper is a generator over
//! `.lower()`, and `str.translate` would not lowercase at all.
//!
//! Measured rows (pinned 6.0.9, through `FilterExpression.resolve`):
//!
//! ```text
//! '800-COLLECT'    -> '800-2655328'
//! '1-800-FLOWERS'  -> '1-800-3569377'
//! 'aBc'            -> '222'
//! 'abcXYZ'         -> '222999'
//! 'a-b_c'          -> '2-2_2'      (punctuation is not in the table)
//! '123-456'        -> '123-456'
//! ''               -> ''
//! 'éè'             -> 'éè'         (not a–z, so not mapped)
//! mark_safe('800-COLLECT') -> (SafeString, '800-2655328') safe=True
//! ```
//!
//! The non-`str` rows, verbatim from the pinned tree — the class name is the
//! argument's `type(value).__name__`:
//!
//! ```text
//! 5     -> AttributeError: 'int' object has no attribute 'lower'
//! True  -> AttributeError: 'bool' object has no attribute 'lower'
//! 5.0   -> AttributeError: 'float' object has no attribute 'lower'
//! None  -> AttributeError: 'NoneType' object has no attribute 'lower'
//! [1]   -> AttributeError: 'list' object has no attribute 'lower'
//! {'a': 1} -> AttributeError: 'dict' object has no attribute 'lower'   (Opaque — see below)
//! (1, 2)   -> AttributeError: 'tuple' object has no attribute 'lower'  (Opaque — see below)
//! ```
//!
//! **One gap the seam cannot close, and it is reported rather than papered
//! over.** `@keep_lazy_text` (`text.py:313`) makes a lazy argument produce a
//! lazy *result* in Django (measured: a `lazy(...)` proxy in gives a
//! `__proxy__` out), and a `Markup`/`Promise`/`dict`/`tuple` argument is an
//! `Opaque` whose Python class this crate cannot name — `Value::type_name`
//! returns `"object"` for every `Opaque` and its own doc says that is not an
//! answer. So for a `tuple`/`dict`/arbitrary object this body writes `'object'
//! object has no attribute 'lower'` where Django writes `'dict'`/`'tuple'`/…,
//! and for a `Markup` it refuses where Django *answers*. Both halves are one
//! missing operation: a host method that performs `phone.lower()` on a `&Value`
//! (or names an `Opaque`'s class). The trait has neither — `to_str` would
//! `str()` a `dict` and *answer* where Django raises — so the body takes the
//! `&Value`-shaped half it can and the residue is stated here, in the same shape
//! as `cut`'s argument message.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;
use crate::parser::error::PyErrorKind;

/// The `AttributeError` CPython's `str.lower` raises for an object without the
/// method. Django's own text is `'<type(value).__name__>' object has no
/// attribute 'lower'` — measured as `'int'`, `'bool'`, `'float'`, `'NoneType'`,
/// `'list'`, `'dict'`, `'tuple'`. Synthesised here rather than raised across the
/// seam because no [`FilterHost`] method performs `str.lower` on a whole value:
/// [`FilterHost::str_lower`] takes a `&str` that only a `str` can supply, and
/// [`FilterHost::to_str`] is `str(v)`, which *answers* where this filter must
/// refuse.
///
/// `name` is the caller's `Value::type_name()`, which is exact for every
/// variant of [`Value`] except `Opaque` — `"object"` there, the one gap the
/// module doc reports.
///
/// `operation` is `"str.lower"` — the CPython operation this stands in for —
/// and **not** a `FilterHost` method's name, because none ran: naming `"to_str"`
/// would put a call in the diagnostic that the body never made.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn not_a_string(name: &str) -> Error {
    Error::filter_host(
        PyErrorKind::AttributeError,
        "str.lower",
        format!("'{name}' object has no attribute 'lower'"),
    )
}

/// `char2number.get(c, c)` — the 26-entry table at `text.py:316-343`, in the
/// source's own order. Only ASCII a–z are mapped, so `'123'`, `'_'` and any
/// non-ASCII code point come back unchanged, which is measured above.
fn digit_or_self(c: char) -> char {
    match c {
        'a' | 'b' | 'c' => '2',
        'd' | 'e' | 'f' => '3',
        'g' | 'h' | 'i' => '4',
        'j' | 'k' | 'l' => '5',
        'm' | 'n' | 'o' => '6',
        'p' | 'q' | 'r' | 's' => '7',
        't' | 'u' | 'v' => '8',
        'w' | 'x' | 'y' | 'z' => '9',
        other => other,
    }
}

/// `phone2numeric`'s body: `"".join(char2number.get(c, c) for c in phone.lower())`.
///
/// The table is Rust's; the `.lower()` is the host's. The value is **not**
/// coerced, so it is matched as a [`Value`] — a `str` (or `SafeString`) supplies
/// the text, anything else gets the `AttributeError` CPython would raise.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `phone.lower()` (`text.py:344`) requires a `str`, and this filter has no
    // `@stringfilter`, so `phone2numeric(5)` raises instead of becoming `"5"`.
    // Read as a `Value` rather than through `to_str`, which would coerce — the
    // `&Value` shape §D7.1 asks for wherever the argument's *type* is observed.
    let text: &str = match &value {
        Value::Str(text) | Value::Safe(text) => text,
        other => return Err(not_a_string(other.type_name())),
    };
    // `str.lower()`, not Rust's `str::to_lowercase`: measured, `"ΟΣ".lower()` is
    // `"ος"` (Final_Sigma), and the two halves cannot be combined without a
    // silent divergence at the 28 suppressed code points — see
    // `FilterHost::str_lower`. The body has no lowercasing of its own.
    let lowered = call.host.str_lower(text);
    let digits: String = lowered.chars().map(digit_or_self).collect();
    // `is_safe=True` and a `SafeData` input are the loop's marking, not the
    // body's — measured, `mark_safe('800-COLLECT')|phone2numeric` is a
    // `SafeString` while a plain input is a plain `str`.
    Ok(Value::Str(digits.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::ErrorKind;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// The host's `str.lower` answer, scripted. The body's own input is the
    /// first argument, so a test that wants a mapping pins which characters the
    /// table saw.
    fn lowering(lowered: &str) -> TestFilterHost {
        TestFilterHost {
            str_lower: Some(lowered.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// Every letter of the table, and the letters-only input keeps the row
    /// unambiguous: `'abcXYZ'` → `'222999'` is measured on the pinned tree.
    #[test]
    fn it_maps_the_26_letters_through_the_table() {
        let h = lowering("abcxyz");
        assert_eq!(
            run(Value::Str("abcXYZ".into()), None, &call(&h)).unwrap(),
            Value::Str("222999".into())
        );
        h.assert_called("str_lower");
    }

    /// Punctuation and digits are not in the table, so `char2number.get(c, c)`
    /// returns them unchanged — measured, `'a-b_c'` → `'2-2_2'`.
    #[test]
    fn a_character_outside_the_table_is_left_alone() {
        let h = lowering("a-b_c");
        assert_eq!(
            run(Value::Str("a-b_c".into()), None, &call(&h)).unwrap(),
            Value::Str("2-2_2".into())
        );
    }

    /// **The lowercasing is the host's**, and this is the test a Rust
    /// `to_lowercase` fails: the scripted answer is Python's Final_Sigma answer
    /// `"ος"`, which the body must return verbatim (`ο` and `ς` are not in the
    /// table). The body never lowercases on its own — `str_lower` is the only
    /// seam call, and `to_str` is not scripted, so reaching it would panic.
    #[test]
    fn the_lowercase_is_the_hosts() {
        let h = lowering("ος");
        assert_eq!(
            run(Value::Str("ΟΣ".into()), None, &call(&h)).unwrap(),
            Value::Str("ος".into())
        );
        assert_eq!(h.calls(), vec!["str_lower"], "no other seam call");
    }

    /// A `SafeString` is a `str`, so it is answered — and the result is a plain
    /// `str`, because the marking is the loop's. `Value::Safe` and `Value::Str`
    /// are structurally unequal, so a body that kept the marker fails here.
    #[test]
    fn a_safe_string_is_answered_and_not_marked() {
        let h = lowering("abc");
        let result = run(Value::Safe("aBc".into()), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("222".into()));
        assert_ne!(result, Value::Safe("222".into()));
    }

    /// The five measured refusals whose class name `Value::type_name` gets
    /// exactly right. Each is the class Django's own `type(value).__name__`
    /// produces, and the body must not have touched the host first.
    #[test]
    fn a_non_string_is_the_measured_attribute_error() {
        for (value, name, expected) in [
            (
                Value::Int(5),
                "int",
                "'int' object has no attribute 'lower'",
            ),
            (
                Value::Bool(true),
                "bool",
                "'bool' object has no attribute 'lower'",
            ),
            (
                Value::Float(5.0),
                "float",
                "'float' object has no attribute 'lower'",
            ),
            (
                Value::None,
                "NoneType",
                "'NoneType' object has no attribute 'lower'",
            ),
            (
                Value::List(vec![Value::Int(1)]),
                "list",
                "'list' object has no attribute 'lower'",
            ),
        ] {
            let h = TestFilterHost::default();
            let error = run(value, None, &call(&h)).expect_err("must refuse");
            match &error.kind {
                ErrorKind::FilterHost(host) => {
                    assert_eq!(host.kind, PyErrorKind::AttributeError, "{name}");
                    assert_eq!(host.operation, "str.lower", "{name}");
                    assert_eq!(host.message, expected, "{name}");
                }
                other => panic!("{name}: not a FilterHost error: {other:?}"),
            }
            assert_eq!(
                h.calls(),
                Vec::<&str>::new(),
                "{name}: the host must not be reached"
            );
        }
    }

    /// `BigInt` reports `"int"`, which is the class Python would name for an
    /// `int` outside `i64` — the same fact `Value::type_name`'s doc records.
    #[test]
    fn a_big_int_is_named_int() {
        let h = TestFilterHost::default();
        let error = run(
            Value::BigInt("123456789012345678901234567890".into()),
            None,
            &call(&h),
        )
        .expect_err("must refuse");
        match &error.kind {
            ErrorKind::FilterHost(host) => {
                assert_eq!(host.message, "'int' object has no attribute 'lower'");
            }
            other => panic!("not a FilterHost error: {other:?}"),
        }
    }
}
