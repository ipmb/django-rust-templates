//! `truncatewords` — `defaultfilters.py:330-341`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def truncatewords(value, arg):
//!     """
//!     Truncate a string after `arg` number of words.
//!     Remove newlines within the string.
//!     """
//!     try:
//!         length = int(arg)
//!     except ValueError:  # Invalid literal for int().
//!         return value  # Fail silently.
//!     return Truncator(value).words(length, truncate=" …")
//! ```
//!
//! **`Truncator.words` is Rust's, unlike `Truncator.chars`.** The host's
//! [`truncate`](crate::filters::host::FilterHost::truncate) carries a warning that
//! the non-html path needs `unicodedata.normalize("NFC")` and
//! `unicodedata.combining` per character, and that is true of `chars`
//! (`text.py:205`, `text.py:220`) — `words` does neither (`text.py:251-261`). It is
//! `" ".join(text.split()[:length])` plus a marker, and both halves are already
//! this crate's: [`pyunicode::split`] is Python's `str.split()` on the table this
//! package pins, and the marker `truncatewords` passes is `" …"` rather than
//! `None`, so `add_truncation_text` (`text.py:73-86`) takes its literal branch and
//! never reaches `pgettext`. Measured before the change: one Python crossing per
//! call, 0.92x.
//!
//! What the body still owns the same way: `int(arg)`, its silent `ValueError`
//! return and the `<= 0` branch. `Truncator.words` is
//! `" ".join(text.split()[:length])` plus the truncation marker — so it
//! **collapses whitespace and strips newlines** (`truncatewords("a\nb\tc", 2)` is
//! `"a b …"`, measured) and the marker is the literal `" …"` (space + U+2026,
//! measured: `truncatewords("a b c d e", 3)` is `"a b c …"`), passed to the host
//! as `truncate_text: Some(" …")`.
//!
//! The three Rust-side answers:
//!
//! * `ValueError` from `int(arg)` returns the value unchanged (measured:
//!   `truncatewords("one two three", "x")` is `"one two three"`); the
//!   `TypeError` from `int(None)` **escapes** (measured). A float truncates:
//!   `truncatewords("one two three", 1.5)` is `"one …"`.
//! * A length `<= 0` is `""` (`Truncator.words`, `text.py:236`).
//! * A length past `i64` is longer than any text (`usize::MAX`); a negative
//!   `Big` is `<= 0` and is `""` (`truncatewords("a b c d", -10**30)` is `""`,
//!   measured).
//!
//! `@stringfilter` coerces the value, `is_safe=True` marks the result in the
//! loop; the body calls `to_int` and `to_str`, and no longer `truncate`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;
use crate::pyunicode;

/// `except ValueError: return value  # Fail silently.`
fn is_value_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError)
}

/// `truncatewords`'s body.
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
        // `if length <= 0: return ""` (text.py:236).
        IntValue::Small(_) => return Ok(Value::Str("".into())),
        IntValue::Big(text) if text.starts_with('-') => return Ok(Value::Str("".into())),
        IntValue::Big(_) => usize::MAX,
    };
    let text = call.host.to_str(&value)?;
    // `Truncator(value).words(length, truncate=" …")` — the marker is the
    // literal space + U+2026.
    Ok(Value::Str(words(&text, length).into()))
}

/// `Truncator._text_words` (`text.py:251-261`), whole.
///
/// The join happens on **both** paths, which is why an untruncated value still
/// comes back with its whitespace collapsed and its newlines gone.
fn words(text: &str, length: usize) -> String {
    let words = pyunicode::split(text);
    if words.len() > length {
        add_truncation_text(&words[..length].join(" "), MARKER)
    } else {
        words.join(" ")
    }
}

/// The marker `truncatewords` passes: a space and U+2026.
const MARKER: &str = " \u{2026}";

/// `add_truncation_text` (`text.py:73-86`) for a `truncate` that is **not**
/// `None` and contains no `%(truncated_text)s`, which is the only shape this
/// filter passes. The `None` default is the translatable one and stays the host's
/// — `truncatechars` is the caller that needs it.
fn add_truncation_text(text: &str, truncate: &str) -> String {
    // "But don't append the truncation text if the current text already ends in
    // this" (`text.py:82-85`).
    if text.ends_with(truncate) {
        return text.to_string();
    }
    format!("{text}{truncate}")
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

    /// The truncation is delegated: `to_int`, `to_str` and `truncate` are all
    /// reached. The scripted answer is the measured
    /// `truncatewords("a b c d e", 3)` = `"a b c …"`.
    #[test]
    fn it_truncates_and_marks_the_text_in_rust() {
        let h = host(IntValue::Small(3), "a b c d e", "a b c \u{2026}");
        assert_eq!(
            run(
                Value::Str("a b c d e".into()),
                Some(Value::Str("3".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("a b c \u{2026}".into())
        );
        h.assert_called("to_int");
        h.assert_called("to_str");
        assert!(
            !h.calls().contains(&"truncate"),
            "`Truncator.words` is Rust's: {:?}",
            h.calls()
        );
    }

    /// `Truncator.words` collapses whitespace, so the host's answer for a text
    /// with a newline and a tab is space-joined (measured `"a b …"`).
    #[test]
    fn the_words_answer_collapses_whitespace() {
        let h = host(IntValue::Small(2), "a\nb\tc", "a b \u{2026}");
        assert_eq!(
            run(
                Value::Str("a\nb\tc".into()),
                Some(Value::Str("2".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("a b \u{2026}".into())
        );
    }

    /// A length `<= 0` is `""` and must not reach the host.
    #[test]
    fn a_non_positive_length_is_the_empty_string() {
        for length in [0_i64, -1, -100] {
            let h = host(IntValue::Small(length), "a b c d", "unused");
            assert_eq!(
                run(
                    Value::Str("a b c d".into()),
                    Some(Value::Str("0".into())),
                    &call(&h)
                )
                .unwrap(),
                Value::Str("".into()),
                "truncatewords(_, {length})"
            );
            assert!(!h.calls().contains(&"truncate"), "{:?}", h.calls());
        }
    }

    /// A negative `Big` is `<= 0` and is `""`, not `usize::MAX`.
    #[test]
    fn a_negative_big_length_is_the_empty_string() {
        let h = host(
            IntValue::Big("-1000000000000000000000000000000".to_string()),
            "a b c d",
            "unused",
        );
        assert_eq!(
            run(
                Value::Str("a b c d".into()),
                Some(Value::BigInt("-1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("".into())
        );
        assert!(!h.calls().contains(&"truncate"));
    }

    /// A positive `Big` is longer than any text and still delegates.
    #[test]
    fn a_positive_big_length_truncates_nothing() {
        let h = host(
            IntValue::Big("1000000000000000000000000000000".to_string()),
            "a b c d",
            "a b c d",
        );
        assert_eq!(
            run(
                Value::Str("a b c d".into()),
                Some(Value::BigInt("1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("a b c d".into())
        );
        assert!(!h.calls().contains(&"truncate"), "{:?}", h.calls());
    }

    /// The join runs on the **untruncated** path too, so whitespace is collapsed
    /// and newlines are gone even when nothing was cut (`text.py:261`).
    #[test]
    fn it_collapses_whitespace_even_when_nothing_is_truncated() {
        assert_eq!(words("a\nb\tc   d", 10), "a b c d");
        // Python's `str.split()` also discards leading and trailing runs, and its
        // whitespace table includes the information separators.
        assert_eq!(words("  a  b  ", 10), "a b");
        assert_eq!(words("a\u{1c}b", 10), "a b");
    }

    /// `add_truncation_text`'s own branch: the marker is not appended twice when
    /// the truncated text already ends in it (`text.py:82-85`).
    #[test]
    fn the_marker_is_not_doubled() {
        // Three words, the third of which is the marker itself.
        assert_eq!(words("a b \u{2026} d", 3), "a b \u{2026}");
    }

    /// The measured rows from this module's own docstring, now that the algorithm
    /// is here rather than behind the seam.
    #[test]
    fn the_measured_rows_hold() {
        assert_eq!(words("a b c d e", 3), "a b c \u{2026}");
        assert_eq!(words("one two three", 1), "one \u{2026}");
        assert_eq!(words("a b c d", usize::MAX), "a b c d");
    }

    /// The silent `ValueError`: the value is returned unchanged and the host is
    /// not asked to truncate or to read the text.
    #[test]
    fn a_non_integer_argument_returns_the_value_unchanged() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::ValueError);
        assert_eq!(
            run(
                Value::Str("one two three".into()),
                Some(Value::Str("x".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("one two three".into())
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
        let error = run(Value::Str("a b c".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "{error:?}"
        );
    }
}
