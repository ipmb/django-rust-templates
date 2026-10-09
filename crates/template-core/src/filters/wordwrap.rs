//! `wordwrap` — `defaultfilters.py:410-414`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def wordwrap(value, arg):
//!     """Wrap words at `arg` line length."""
//!     return wrap(value, int(arg))
//! ```
//!
//! `wrap` is `django.utils.text.wrap`, i.e. `textwrap.TextWrapper(width=…,
//! break_long_words=False, break_on_hyphens=False,
//! replace_whitespace=False).wrap(…)` per line plus a `"\n".join`. The wrapping
//! itself is the host's for the usual reason — the break rules and the whitespace
//! handling are CPython's — and the two measured traps the plan names are both in
//! it:
//!
//! * **`break_long_words=False`**: a word longer than the width is emitted whole
//!   (`wordwrap("aaaa bbb", 3)` is `"aaaa\nbbb"`, measured).
//! * **Whitespace is collapsed at a break and stripped at the edges**, even with
//!   `replace_whitespace=False`: `wordwrap("a   b", 3)` is `"a\nb"`,
//!   `wordwrap("  lead", 3)` is `"lead"` (measured). A run that does **not** sit
//!   at a break survives (`wordwrap("a  b", 10)` is `"a  b"`).
//!
//! Rust owns `int(arg)` (Python's: `int(" 5 ")` → 5), the join is the host's, and
//! there is **no `try`**, so both of the raises escape:
//!
//! * `int`'s `ValueError` for a non-numeric argument (measured:
//!   `wordwrap("a b c", "x")` raises).
//! * `textwrap`'s own `ValueError: invalid width N (must be > 0)` for a width
//!   `<= 0` (measured). But Django calls the wrapper **once per line**, and an
//!   empty text has no lines, so `wordwrap("", 0)` is `""` while
//!   `wordwrap("\n", 0)` raises (measured).
//!
//! A width past `i64` follows the same rule by sign: a **negative** `Big` is a
//! width `<= 0` and raises `invalid width -1000… (must be > 0)`, while a
//! **positive** one is longer than any line and leaves the text unchanged
//! (`wordwrap("a b c d", 10**30)` is `"a b c d"`, measured) — expressed as
//! `usize::MAX`, which is the same comparison.
//!
//! `@stringfilter` coerces the value and `is_safe=True` marks the result in the
//! loop; the body calls `to_int`, `to_str` and `wrap`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};
use crate::pynum::IntValue;

/// The width `<= 0` path.
///
/// `textwrap.TextWrapper.wrap` raises this `ValueError` from its first line, but
/// Django only reaches it inside `for line in text.splitlines()` — so a text with
/// no lines is the one input that returns instead of raising. `operation` names
/// the CPython operation the body stands in for, not a [`FilterHost`] method,
/// because the width never crosses the seam.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn non_positive_width(text: &str, width: &str) -> Result<Value, Error> {
    if text.is_empty() {
        return Ok(Value::Str("".into()));
    }
    Err(Error::filter_host(
        PyErrorKind::ValueError,
        "textwrap.TextWrapper.wrap",
        format!("invalid width {width} (must be > 0)"),
    ))
}

/// `wordwrap`'s body.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `int(arg)` first and with no `try`, so its `ValueError`/`TypeError` escapes
    // before the text is read.
    let width = call.host.to_int(&arg.unwrap_or(Value::None))?;
    let text = call.host.to_str(&value)?;
    match width {
        IntValue::Small(width) if width > 0 => {
            Ok(Value::Str(call.host.wrap(&text, width as usize).into()))
        }
        // A positive `Big` is longer than any line, so the wrapper leaves the
        // text alone; `usize::MAX` makes the same comparison.
        IntValue::Big(digits) if !digits.starts_with('-') => {
            Ok(Value::Str(call.host.wrap(&text, usize::MAX).into()))
        }
        // `<= 0`, spelled with the argument's own digits in the message.
        IntValue::Small(width) => non_positive_width(&text, &width.to_string()),
        IntValue::Big(digits) => non_positive_width(&text, &digits),
    }
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

    fn host(width: IntValue, text: &str, wrapped: &str) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(width),
            to_str: Some(text.to_string()),
            wrap: Some(wrapped.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The wrapping is delegated: `to_int`, `to_str` and `wrap` are all reached.
    ///
    /// The scripted answer is the measured `wordwrap("aaa bbb ccc", 5)`, but the
    /// break rules themselves are the host's — the corpus is what pins
    /// `break_long_words=False` and the whitespace collapsing. What this test
    /// pins is that the body hands the text to the host instead of wrapping it in
    /// Rust.
    #[test]
    fn it_delegates_the_wrap_to_the_host() {
        let h = host(IntValue::Small(5), "aaa bbb ccc", "aaa\nbbb\nccc");
        assert_eq!(
            run(
                Value::Str("aaa bbb ccc".into()),
                Some(Value::Str("5".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("aaa\nbbb\nccc".into())
        );
        h.assert_called("to_int");
        h.assert_called("to_str");
        h.assert_called("wrap");
    }

    /// The scripted answer for a long word is the measured one, so the body's
    /// plumbing is exercised on the `break_long_words=False` shape too.
    #[test]
    fn a_long_word_is_a_host_answer() {
        let h = host(IntValue::Small(3), "aaaa bbb", "aaaa\nbbb");
        assert_eq!(
            run(
                Value::Str("aaaa bbb".into()),
                Some(Value::Str("3".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("aaaa\nbbb".into())
        );
        h.assert_called("wrap");
    }

    /// A width of `0` is `textwrap`'s `ValueError`, and it must not reach the
    /// host's infallible `wrap`.
    #[test]
    fn a_zero_width_raises_invalid_width() {
        let h = host(IntValue::Small(0), "ab cd", "unused");
        let error = run(
            Value::Str("ab cd".into()),
            Some(Value::Str("0".into())),
            &call(&h),
        )
        .unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::ValueError);
                assert_eq!(host_error.message, "invalid width 0 (must be > 0)");
            }
            other => panic!("a zero width must raise, got {other:?}"),
        }
        assert!(!h.calls().contains(&"wrap"), "{:?}", h.calls());
    }

    /// The empty text is the one input that escapes the `<= 0` raise: Django's
    /// `wrap` never reaches the wrapper for a text with no lines.
    #[test]
    fn the_empty_text_at_a_zero_width_returns_empty() {
        let h = host(IntValue::Small(0), "", "unused");
        assert_eq!(
            run(
                Value::Str("".into()),
                Some(Value::Str("0".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("".into())
        );
        assert!(!h.calls().contains(&"wrap"), "{:?}", h.calls());
    }

    /// A negative `Big` is a width `<= 0`, spelled with its digits in the
    /// message (measured: `invalid width -1000000000000000000000000000000`).
    #[test]
    fn a_negative_big_width_raises_with_its_digits() {
        let h = host(
            IntValue::Big("-1000000000000000000000000000000".to_string()),
            "ab cd",
            "unused",
        );
        let error = run(
            Value::Str("ab cd".into()),
            Some(Value::BigInt("-1000000000000000000000000000000".into())),
            &call(&h),
        )
        .unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::ValueError);
                assert_eq!(
                    host_error.message,
                    "invalid width -1000000000000000000000000000000 (must be > 0)"
                );
            }
            other => panic!("a negative Big width must raise, got {other:?}"),
        }
    }

    /// A positive `Big` is longer than any line and still delegates.
    #[test]
    fn a_positive_big_width_still_delegates() {
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
        h.assert_called("wrap");
    }

    /// `int(arg)`'s `ValueError` escapes — there is no `try` in `wordwrap` — and
    /// it escapes before the text is read or `wrap` is reached.
    #[test]
    fn a_non_integer_argument_escapes_as_value_error() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::ValueError);
        let error = run(
            Value::Str("a b c".into()),
            Some(Value::Str("x".into())),
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError && e.operation == "to_int"),
            "int('x') must escape: {error:?}"
        );
        assert!(
            !h.calls().contains(&"wrap") && !h.calls().contains(&"to_str"),
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
