//! `truncatewords_html` — `defaultfilters.py:344-355`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def truncatewords_html(value, arg):
//!     """
//!     Truncate HTML after `arg` number of words.
//!     Preserve newlines in the HTML.
//!     """
//!     try:
//!         length = int(arg)
//!     except ValueError:  # invalid literal for int()
//!         return value  # Fail silently.
//!     return Truncator(value).words(length, html=True, truncate=" …")
//! ```
//!
//! The `html=True` selects `Truncator`'s `TruncateWordsHTMLParser`, and the
//! marker is the literal `" …"` (space + U+2026) — the same one `truncatewords`
//! passes without `html`. Delegated whole for the same reason as its three
//! siblings: the tag grammar is `HTMLParser`'s, so an unbalanced `<` in the text
//! is handled by the parser (measured: `truncatewords_html("a<b c d", 2)` is
//! `"a"`) and the closing tags around a cut are the parser's
//! (`truncatewords_html("<p>one <b>two</b> three</p>", 2)` is
//! `"<p>one <b>two</b> …</p>"`, measured).
//!
//! The Rust side is `truncatewords`'s: `int(arg)` (Python's, `int(1.5)` → 1), the
//! silent `ValueError` return, the `TypeError` from `int(None)` escaping,
//! `length <= 0` → `""`, and a positive `Big` as `usize::MAX` against a negative
//! `Big` as `""`. The differences from `truncatewords` are `html: true` and the
//! same `truncate_text: Some(" …")`.
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

/// `truncatewords_html`'s body.
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
    // `Truncator(value).words(length, html=True, truncate=" …")`.
    Ok(Value::Str(
        call.host
            .truncate(&text, length, true, true, Some(" \u{2026}"))
            .into(),
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

    /// The whole truncation — the HTML parse included — is delegated, so all
    /// three seam calls are reached. The scripted answer is the measured
    /// `truncatewords_html("<p>one two three four</p>", 2)`.
    #[test]
    fn it_delegates_the_html_truncation_to_the_host() {
        let h = host(
            IntValue::Small(2),
            "<p>one two three four</p>",
            "<p>one two \u{2026}</p>",
        );
        assert_eq!(
            run(
                Value::Str("<p>one two three four</p>".into()),
                Some(Value::Str("2".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>one two \u{2026}</p>".into())
        );
        h.assert_called("to_int");
        h.assert_called("to_str");
        h.assert_called("truncate");
    }

    /// A cut inside markup closes the tags, which is the parser's work, not
    /// Rust's: measured `truncatewords_html("<p>one <b>two</b> three</p>", 2)`.
    #[test]
    fn a_cut_inside_markup_is_the_hosts_parse() {
        let h = host(
            IntValue::Small(2),
            "<p>one <b>two</b> three</p>",
            "<p>one <b>two</b> \u{2026}</p>",
        );
        assert_eq!(
            run(
                Value::Str("<p>one <b>two</b> three</p>".into()),
                Some(Value::Str("2".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>one <b>two</b> \u{2026}</p>".into())
        );
        h.assert_called("truncate");
    }

    /// A length `<= 0` is `""` and does not reach the host.
    #[test]
    fn a_non_positive_length_is_the_empty_string() {
        let h = host(IntValue::Small(0), "<p>one two</p>", "unused");
        assert_eq!(
            run(
                Value::Str("<p>one two</p>".into()),
                Some(Value::Str("0".into())),
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
            "<p>one two</p>",
            "unused",
        );
        assert_eq!(
            run(
                Value::Str("<p>one two</p>".into()),
                Some(Value::BigInt("-1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("".into())
        );
        assert!(!h.calls().contains(&"truncate"));

        let h = host(
            IntValue::Big("1000000000000000000000000000000".to_string()),
            "<p>one two</p>",
            "<p>one two</p>",
        );
        assert_eq!(
            run(
                Value::Str("<p>one two</p>".into()),
                Some(Value::BigInt("1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>one two</p>".into())
        );
        h.assert_called("truncate");
    }

    /// The silent `ValueError` returns the value unchanged without delegating.
    #[test]
    fn a_non_integer_argument_returns_the_value_unchanged() {
        let h = host(IntValue::Small(2), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::ValueError);
        assert_eq!(
            run(
                Value::Str("<p>one two</p>".into()),
                Some(Value::Str("x".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("<p>one two</p>".into())
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
        let h = host(IntValue::Small(2), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Str("<p>one two</p>".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "{error:?}"
        );
    }
}
