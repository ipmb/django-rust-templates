//! `rjust` — `defaultfilters.py:424-428`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def rjust(value, arg):
//!     """Right-align the value in a field of a given width."""
//!     return value.rjust(int(arg))
//! ```
//!
//! `rjust` is `ljust`'s mirror with the same three measured facts, stated here
//! rather than cross-referenced so the file reads on its own:
//!
//! * `int(arg)` is Python's `int` (`" 5 "` → 5, `"5_0"` → 50, `5.0` → 5,
//!   measured), so the conversion is [`FilterHost::to_int`]'s.
//! * `str.rjust` pads by **code points**: `rjust("e\u{301}", 3)` is
//!   `" e\u{301}"` (measured), so the length is `chars().count()`.
//! * The `ValueError` from `int(arg)` **escapes** — there is no `try`.
//!
//! A width past `Py_ssize_t` is `OverflowError: Python int too large to convert
//! to C ssize_t` for either sign (measured on `'ab'.rjust(±10**30)`), which is
//! the set `IntValue::Big` names.
//!
//! `@stringfilter` coerces the value, `is_safe=True` marks the result in the
//! loop, and this body calls neither `mark_safe` nor anything but `to_int` and
//! `to_str`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};
use crate::pynum::IntValue;

/// The `OverflowError` CPython raises when the width does not fit `Py_ssize_t`,
/// for either sign (measured on `'ab'.rjust(±10**30)`); `IntValue::Big` is
/// exactly that set. No [`FilterHost`] method performs the padding, so
/// `operation` names the CPython method this body stands in for.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn too_large() -> Error {
    Error::filter_host(
        PyErrorKind::Other("OverflowError".into()),
        "str.rjust",
        "Python int too large to convert to C ssize_t",
    )
}

/// `rjust`'s body.
///
/// `int(arg)` is the host's and there is no `try`, so its error escapes; the
/// padding is Rust's and counts **code points**.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let width = match call.host.to_int(&arg.unwrap_or(Value::None))? {
        IntValue::Small(width) => width,
        IntValue::Big(_) => return Err(too_large()),
    };
    let text = call.host.to_str(&value)?;
    let length = text.chars().count() as i64;
    if width <= length {
        return Ok(Value::Str(text.into()));
    }
    let mut padded = " ".repeat((width - length) as usize);
    padded.push_str(&text);
    Ok(Value::Str(padded.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::{ErrorKind, PyErrorKind};
    use crate::pynum::IntValue;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(width: IntValue, text: &str) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(width),
            to_str: Some(text.to_string()),
            ..TestFilterHost::default()
        }
    }

    #[test]
    fn it_pads_on_the_left_by_code_points() {
        let rows = [
            (5_i64, "ab", "   ab"),
            (2, "ab", "ab"),
            (1, "ab", "ab"),
            (0, "ab", "ab"),
            (-3, "ab", "ab"),
            (3, "", "   "),
            (3, "\u{e9}", "  \u{e9}"),
            // `e` + U+0301 is two code points, so one pad.
            (3, "e\u{301}", " e\u{301}"),
        ];
        for (width, text, expected) in rows {
            let h = host(IntValue::Small(width), text);
            assert_eq!(
                run(
                    Value::Str(text.into()),
                    Some(Value::Str("w".into())),
                    &call(&h)
                )
                .unwrap(),
                Value::Str(expected.into()),
                "rjust({text:?}, {width})"
            );
        }
    }

    /// The padding must not go through Rust's format spec: `format!("{:>width$}")`
    /// **panics** (`Formatting argument out of range`) past `u16::MAX`, while
    /// Django pads happily — measured `rjust("ab", 65536)` is a 65536-wide string
    /// with no error. Pinning the length keeps a later refactor from
    /// reintroducing the format spec, where a legal `{{ x|rjust:70000 }}` would
    /// panic.
    #[test]
    fn a_width_past_u16_pads_rather_than_panicking() {
        let h = host(IntValue::Small(65536), "ab");
        let result = run(
            Value::Str("ab".into()),
            Some(Value::Str("65536".into())),
            &call(&h),
        )
        .unwrap();
        match result {
            Value::Str(text) => assert_eq!(text.len(), 65536, "no u16 cap on the format width"),
            other => panic!("expected a Str, got {other:?}"),
        }
    }

    /// The width is Python's `int`, reached through the seam.
    #[test]
    fn the_width_comes_from_the_hosts_int() {
        let h = host(IntValue::Small(5), "ab");
        assert_eq!(
            run(
                Value::Str("ab".into()),
                Some(Value::Str(" 5 ".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("   ab".into())
        );
        h.assert_called("to_int");
    }

    /// `int(5.0)` is `5`, so `rjust("abc", 5.0)` is `"  abc"` (measured).
    #[test]
    fn a_float_argument_is_truncated_to_an_int() {
        let h = host(IntValue::Small(5), "abc");
        assert_eq!(
            run(Value::Str("abc".into()), Some(Value::Float(5.0)), &call(&h)).unwrap(),
            Value::Str("  abc".into())
        );
        h.assert_called("to_int");
    }

    /// The text comes back through the seam, so a coerced non-string still pads.
    #[test]
    fn the_text_is_read_through_the_host() {
        let h = host(IntValue::Small(3), "5");
        assert_eq!(
            run(Value::Int(5), Some(Value::Str("3".into())), &call(&h)).unwrap(),
            Value::Str("  5".into())
        );
        h.assert_called("to_str");
    }

    /// No `try`, so `int(arg)`'s `ValueError` is the answer.
    #[test]
    fn a_non_integer_argument_escapes_as_value_error() {
        let h = host(IntValue::Small(5), "ab");
        h.raise_on("to_int", PyErrorKind::ValueError);
        let error = run(
            Value::Str("ab".into()),
            Some(Value::Str("x".into())),
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError),
            "int('x') must escape: {error:?}"
        );
    }

    /// `int(None)` is a `TypeError` and `rjust` does not catch it.
    #[test]
    fn a_none_argument_escapes_as_type_error() {
        let h = host(IntValue::Small(5), "ab");
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Str("ab".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "int(None) is a TypeError: {error:?}"
        );
    }

    /// A width past `Py_ssize_t` raises, for either sign.
    #[test]
    fn a_width_past_i64_is_an_overflow_error() {
        for big in [
            "1000000000000000000000000000000",
            "-1000000000000000000000000000000",
        ] {
            let h = host(IntValue::Big(big.to_string()), "ab");
            let error = run(
                Value::Str("ab".into()),
                Some(Value::BigInt(big.into())),
                &call(&h),
            )
            .unwrap_err();
            match &error.kind {
                ErrorKind::FilterHost(host_error) => {
                    assert_eq!(host_error.kind, PyErrorKind::Other("OverflowError".into()));
                    assert_eq!(
                        host_error.message,
                        "Python int too large to convert to C ssize_t"
                    );
                }
                other => panic!("a Big width must raise OverflowError, got {other:?}"),
            }
        }
    }
}
