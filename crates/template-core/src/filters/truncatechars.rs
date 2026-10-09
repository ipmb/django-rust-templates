//! `truncatechars` — `defaultfilters.py:305-313`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def truncatechars(value, arg):
//!     """Truncate a string after `arg` number of characters."""
//!     try:
//!         length = int(arg)
//!     except ValueError:  # Invalid literal for int().
//!         return value  # Fail silently.
//!     return Truncator(value).chars(length)
//! ```
//!
//! **Rust owns the `int(arg)` and its silent failure; the host owns the
//! truncation.** The split is the plan's, and the reason is that
//! `Truncator.chars` reaches `unicodedata.normalize("NFC")` and
//! `unicodedata.combining` per character (`text.py:205`, `:220`) and the
//! translatable `add_truncation_text` marker — a Rust `chars` would need a third
//! Unicode table. Delegating it whole is also what makes the combining-mark rule
//! testable in one place: `truncatechars("e\u{301}x", 2)` is `"\u{e9}x"`
//! (measured) because the pair is one code point for the length and NFC folds it.
//!
//! Three measured answers on the Rust side of the split:
//!
//! * **`ValueError` is swallowed, and only `ValueError`.** `int("x")` returns the
//!   value unchanged (`truncatechars("abcdefghij", "x")` is `"abcdefghij"`,
//!   measured); `int(None)` is a `TypeError` and **escapes** (measured), because
//!   Django catches only `ValueError`. `int(1.5)` truncates to `1`, and `1` is a
//!   valid length.
//! * **A length `<= 0` is `""`**, decided inside `Truncator.chars`
//!   (`if length <= 0: return ""`, `text.py:207`). The host's `length` is a
//!   `usize`, so this branch cannot cross the seam and is Rust's.
//! * **A length past `i64` is `usize::MAX`.** The length is only ever compared
//!   with the text's own length, so any value with more digits than the text is
//!   "longer than the text"; `usize::MAX` makes the same comparison, while a
//!   **negative** `Big` — `IntValue::Big` carries its sign — is `<= 0` and is the
//!   empty string (`truncatechars("abcdefghij", -10**30)` is `""`, measured).
//!
//! `@stringfilter` coerces the value and `is_safe=True` marks the result in the
//! loop; the body calls `to_int`, `to_str` and `truncate`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `except ValueError: return value  # Fail silently.` — the silent branch,
/// matched on the class the seam carries rather than on a message.
fn is_value_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError)
}

/// `truncatechars`'s body.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let length = match call.host.to_int(&arg.unwrap_or(Value::None)) {
        Ok(length) => length,
        // `except ValueError: return value` — and *only* `ValueError`; the
        // `TypeError` from `int(None)` falls through and escapes.
        Err(error) if is_value_error(&error) => return Ok(value),
        Err(error) => return Err(error),
    };
    let length = match length {
        IntValue::Small(length) if length > 0 => length as usize,
        // `if length <= 0: return ""` (text.py:207) — Rust's, because the seam's
        // length is a `usize` and cannot carry this branch.
        IntValue::Small(_) => return Ok(Value::Str("".into())),
        // `IntValue::Big`'s canonical text carries the sign: a negative Big is
        // `<= 0` (the empty string), a positive one is longer than any text.
        IntValue::Big(text) if text.starts_with('-') => return Ok(Value::Str("".into())),
        IntValue::Big(_) => usize::MAX,
    };
    let text = call.host.to_str(&value)?;
    Ok(Value::Str(
        call.host.truncate(&text, length, false, false, None).into(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(length: IntValue, text: &str, truncated: &str) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(length),
            to_str: Some(text.to_string()),
            truncate: Some(truncated.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The truncation is delegated, not reimplemented: the body reaches
    /// `to_int`, `to_str` and `truncate`. The scripted answer is the measured
    /// `truncatechars("abcdefghij", 5)` = `"abcd…"`.
    #[test]
    fn it_delegates_the_truncation_to_the_host() {
        let h = host(IntValue::Small(5), "abcdefghij", "abcd\u{2026}");
        assert_eq!(
            run(
                Value::Str("abcdefghij".into()),
                Some(Value::Str("5".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abcd\u{2026}".into())
        );
        h.assert_called("to_int");
        h.assert_called("to_str");
        h.assert_called("truncate");
    }

    /// `Truncator.chars`'s `length <= 0` is Rust's, and it must not reach the
    /// host: the result is `""` and `truncate` is never called.
    #[test]
    fn a_non_positive_length_is_the_empty_string() {
        for length in [0_i64, -1, -100] {
            let h = host(IntValue::Small(length), "abcdefghij", "unused");
            assert_eq!(
                run(
                    Value::Str("abcdefghij".into()),
                    Some(Value::Str("0".into())),
                    &call(&h)
                )
                .unwrap(),
                Value::Str("".into()),
                "truncatechars(_, {length})"
            );
            assert!(
                !h.calls().contains(&"truncate"),
                "a <= 0 length must not reach the host, saw {:?}",
                h.calls()
            );
        }
    }

    /// A negative `Big` is `<= 0`, so it is the empty string — not `usize::MAX`.
    /// `IntValue::Big` carries the sign in its canonical text.
    #[test]
    fn a_negative_big_length_is_the_empty_string() {
        let h = host(
            IntValue::Big("-1000000000000000000000000000000".to_string()),
            "abcdefghij",
            "unused",
        );
        assert_eq!(
            run(
                Value::Str("abcdefghij".into()),
                Some(Value::BigInt("-1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("".into())
        );
        assert!(!h.calls().contains(&"truncate"));
    }

    /// A positive `Big` is longer than any text, so it still delegates rather
    /// than taking the `<= 0` branch.
    #[test]
    fn a_positive_big_length_still_delegates() {
        let h = host(
            IntValue::Big("1000000000000000000000000000000".to_string()),
            "abcdefghij",
            "abcdefghij",
        );
        assert_eq!(
            run(
                Value::Str("abcdefghij".into()),
                Some(Value::BigInt("1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abcdefghij".into())
        );
        h.assert_called("truncate");
    }

    /// **The silent failure.** `int("x")` is a `ValueError`, and Django returns
    /// the value **unchanged** without reaching `Truncator` — so neither
    /// `to_str` nor `truncate` is called.
    #[test]
    fn a_non_integer_argument_returns_the_value_unchanged() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::ValueError);
        assert_eq!(
            run(
                Value::Str("abc".into()),
                Some(Value::Str("x".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abc".into()),
            "truncatechars(\"abc\", \"x\") is \"abc\""
        );
        assert!(
            !h.calls().contains(&"truncate") && !h.calls().contains(&"to_str"),
            "the silent branch returns before reading or truncating, saw {:?}",
            h.calls()
        );
    }

    /// `int(None)` is a `TypeError`, which Django's `except ValueError` does
    /// **not** catch — so it escapes the filter.
    #[test]
    fn a_none_argument_escapes_as_type_error() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Str("abc".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "int(None) is a TypeError and is not caught: {error:?}"
        );
    }
}
