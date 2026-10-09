//! `truncatechars_html` — `defaultfilters.py:316-327`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def truncatechars_html(value, arg):
//!     """
//!     Truncate HTML after `arg` number of chars.
//!     Preserve newlines in the HTML.
//!     """
//!     try:
//!         length = int(arg)
//!     except ValueError:  # invalid literal for int()
//!         return value  # Fail silently.
//!     return Truncator(value).chars(length, html=True)
//! ```
//!
//! The `html=True` selects `Truncator`'s `TruncateCharsHTMLParser` branch, which
//! is why the whole call stays on the host's side of the seam — the tag grammar,
//! the entity handling and the malformed-input behaviour are `HTMLParser`'s.
//! Measured: `truncatechars_html("<p>abcdefghij</p>", 5)` is `"<p>abcd…</p>"`,
//! the closing tag is preserved, and an **unbalanced** tag in the text is closed
//! or dropped by the parser (`"<p>abc", 4` is `"<p>abc"`; `"a<b", 2` is `"a"`).
//!
//! The Rust side is `truncatechars`'s exactly: `int(arg)` (Python's, with
//! `int(1.5)` → 1), the silent `ValueError` return, the `TypeError` from
//! `int(None)` escaping, `length <= 0` → `""`, and a positive `Big` length as
//! `usize::MAX` against a negative `Big` as `""`. The only difference from
//! `truncatechars` is the `html: true` argument.
//!
//! `@stringfilter` coerces the value and `is_safe=True` marks the result in the
//! loop; the body calls `to_int`, `to_str` and `truncate`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `except ValueError: return value  # Fail silently.`
fn is_value_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError)
}

/// `truncatechars_html`'s body.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let length = match call.host.to_int(&arg.unwrap_or(Value::None)) {
        Ok(length) => length,
        // `except ValueError: return value` — and *only* `ValueError`.
        Err(error) if is_value_error(&error) => return Ok(value),
        Err(error) => return Err(error),
    };
    let length = match length {
        IntValue::Small(length) if length > 0 => length as usize,
        IntValue::Small(_) => return Ok(Value::Str("".into())),
        IntValue::Big(text) if text.starts_with('-') => return Ok(Value::Str("".into())),
        IntValue::Big(_) => usize::MAX,
    };
    let text = call.host.to_str(&value)?;
    // `Truncator(value).chars(length, html=True)` — the parser owns the tags.
    Ok(Value::Str(
        call.host.truncate(&text, length, false, true, None).into(),
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

    /// The whole truncation is delegated — including the HTML parse — so
    /// `to_int`, `to_str` and `truncate` are all reached. The scripted answer is
    /// the measured `truncatechars_html("<p>abcdefghij</p>", 5)`.
    #[test]
    fn it_delegates_the_html_truncation_to_the_host() {
        let h = host(
            IntValue::Small(5),
            "<p>abcdefghij</p>",
            "<p>abcd\u{2026}</p>",
        );
        assert_eq!(
            run(
                Value::Str("<p>abcdefghij</p>".into()),
                Some(Value::Str("5".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>abcd\u{2026}</p>".into())
        );
        h.assert_called("to_int");
        h.assert_called("to_str");
        h.assert_called("truncate");
    }

    /// An unbalanced `<` in the text is the parser's problem, not Rust's: the
    /// measured answer for `"a<b"` at length 2 is `"a"`.
    #[test]
    fn an_unbalanced_tag_is_the_hosts_parse() {
        let h = host(IntValue::Small(2), "a<b", "a");
        assert_eq!(
            run(
                Value::Str("a<b".into()),
                Some(Value::Str("2".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("a".into())
        );
        h.assert_called("truncate");
    }

    /// A length `<= 0` is `""` and does not reach the host.
    #[test]
    fn a_non_positive_length_is_the_empty_string() {
        let h = host(IntValue::Small(-1), "<p>abc</p>", "unused");
        assert_eq!(
            run(
                Value::Str("<p>abc</p>".into()),
                Some(Value::Str("-1".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("".into())
        );
        assert!(!h.calls().contains(&"truncate"), "{:?}", h.calls());
    }

    /// A negative `Big` is `<= 0` and is `""`; a positive `Big` still delegates.
    #[test]
    fn a_big_length_is_sign_dependent() {
        let h = host(
            IntValue::Big("-1000000000000000000000000000000".to_string()),
            "<p>abc</p>",
            "unused",
        );
        assert_eq!(
            run(
                Value::Str("<p>abc</p>".into()),
                Some(Value::BigInt("-1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("".into())
        );
        assert!(!h.calls().contains(&"truncate"));

        let h = host(
            IntValue::Big("1000000000000000000000000000000".to_string()),
            "<p>abc</p>",
            "<p>abc</p>",
        );
        assert_eq!(
            run(
                Value::Str("<p>abc</p>".into()),
                Some(Value::BigInt("1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>abc</p>".into())
        );
        h.assert_called("truncate");
    }

    /// The silent `ValueError` returns the value unchanged without delegating.
    #[test]
    fn a_non_integer_argument_returns_the_value_unchanged() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::ValueError);
        assert_eq!(
            run(
                Value::Str("<p>abc</p>".into()),
                Some(Value::Str("x".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>abc</p>".into())
        );
        assert!(
            !h.calls().contains(&"truncate") && !h.calls().contains(&"to_str"),
            "{:?}",
            h.calls()
        );
    }

    /// `int(None)` is a `TypeError` and escapes.
    #[test]
    fn a_none_argument_escapes_as_type_error() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Str("<p>abc</p>".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "{error:?}"
        );
    }
}
