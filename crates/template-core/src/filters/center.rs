//! `center` — `defaultfilters.py:431-438`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def center(value, arg):
//!     """Center the value in a field of a given width."""
//!     width = int(arg)
//!     if width <= 0:
//!         return value
//!     return f"{value:^{width}}"
//! ```
//!
//! **This is not `str.center`, and the difference is the trap.** The padding
//! comes from CPython's format-spec `^`, whose extra space goes on the
//! **right** — `left = margin // 2` — where `str.center`'s rule carries an
//! `marg & width & 1` term that puts it on the left. The two agree on every even
//! margin and diverge exactly when the margin is odd:
//!
//! ```text
//! "ab"|center:5  -> " ab  "   left=1 right=2   <- the format spec (this filter)
//! "ab".center(5) -> "  ab "   left=2 right=1   <- str.center, NOT this filter
//! ```
//!
//! So the both-odd row (`"ab"`, width 5) is the only one that can tell a correct
//! body from one written to `str.center`, and it is pinned below.
//!
//! **The early return is `width <= 0`, and it runs after `int(arg)`.** `int(arg)`
//! is computed first, so `center("ab", "x")` raises `ValueError` (measured) while
//! `center("ab", 0)` and `center("ab", -1)` return the value **unchanged** —
//! `"ab"`, not the empty string the truncation filters give for a non-positive
//! length. A **float** argument truncates: `center("abc", 5.0)` is `" abc "`
//! (measured).
//!
//! The padding counts **code points** (`center("e\u{301}", 4)` is `" e\u{301} "`,
//! measured). A width past `Py_ssize_t` is asymmetric because it takes a
//! different path for each sign: a **negative** `Big` is `<= 0` and returns the
//! value, while a **positive** one reaches the format spec and raises
//! `ValueError: Too many decimal digits in format string` (both measured).
//!
//! **The `<= 0` guard is load-bearing for the error *type*, not just the value.**
//! It is what keeps a negative huge width off the format path, and the filter's
//! answer there is not the builtin's: `'ab'.center(10**30)` raises
//! `OverflowError: Python int too large to convert to C ssize_t`, but the
//! **filter** — which is the format spec, not `str.center` — raises
//! `ValueError: Too many decimal digits in format string` (measured). Quote the
//! filter's class.
//!
//! **A third regime sits between those and neither guard covers it**: a width
//! that fits `Py_ssize_t` and is too large to *allocate*. `center("ab", 10**18)`
//! is a `MemoryError` in Python (measured, empty message), so the padding
//! allocates **fallibly** — `" ".repeat(margin)` would abort the process rather
//! than raise. See [`pad_spaces`](crate::filters::padding::pad_spaces).
//!
//! `@stringfilter` coerces the value and `is_safe=True` marks the result in the
//! loop; this body calls only `to_int` and `to_str`.

use crate::filters::host::FilterCall;
use crate::filters::padding::pad_spaces;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};
use crate::pynum::IntValue;

/// The `ValueError` CPython's format spec raises for a positive width too large
/// to spell (measured: `f"{'ab':^{10**30}}"` is `ValueError: Too many decimal
/// digits in format string`). No [`FilterHost`] method formats the value, so
/// `operation` names the CPython operation this body stands in for.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn too_many_digits() -> Error {
    Error::filter_host(
        PyErrorKind::ValueError,
        "str.format",
        "Too many decimal digits in format string",
    )
}

/// `center`'s body.
///
/// `int(arg)` is the host's and there is no `try`, so its error escapes — and it
/// runs before the `<= 0` test, which therefore returns the **value unchanged**
/// (with the value's own text unread) for a non-positive width. Otherwise the
/// padding is the format spec's: `left = margin / 2`, the extra space on the
/// **right**, counting code points.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let width = match call.host.to_int(&arg.unwrap_or(Value::None))? {
        IntValue::Small(width) => width,
        // A negative `Big` is `<= 0` and takes the early return; a positive one
        // reaches the format spec. `IntValue::Big`'s text carries the sign.
        IntValue::Big(text) if text.starts_with('-') => return Ok(value),
        IntValue::Big(_) => return Err(too_many_digits()),
    };
    if width <= 0 {
        return Ok(value);
    }
    let text = call.host.to_str(&value)?;
    let length = text.chars().count() as i64;
    if width <= length {
        return Ok(Value::Str(text.into()));
    }
    let margin = (width - length) as usize;
    let left = margin / 2;
    // Fallible: `" ".repeat(margin)` aborts the process on a margin the
    // allocator refuses, where Django raises `MemoryError`. See `padding`.
    Ok(Value::Str(
        pad_spaces("str.format", &text, left, margin - left)?.into(),
    ))
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

    /// The measured rows. Both parities of width are present, and the code-point
    /// count is exercised by the `e` + U+0301 pair.
    #[test]
    fn it_centers_by_the_format_spec_rule() {
        let rows = [
            (5_i64, "ab", " ab  "),
            (4, "ab", " ab "),
            (6, "ab", "  ab  "),
            (1, "ab", "ab"),
            (3, "", "   "),
            (4, "a", " a  "),
            (4, "abc", "abc "),
            (6, "abc", " abc  "),
            (7, "abc", "  abc  "),
            (4, "e\u{301}", " e\u{301} "),
            (3, "e\u{301}", "e\u{301} "),
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
                "center({text:?}, {width})"
            );
        }
    }

    /// **The guard.** A body written to `str.center`'s rule —
    /// `left = margin // 2 + (margin & width & 1)` — agrees with every even-margin
    /// row above and fails here, where the margin and the width are both odd.
    #[test]
    fn the_extra_space_goes_on_the_right_at_a_both_odd_width() {
        let h = host(IntValue::Small(5), "ab");
        assert_eq!(
            run(
                Value::Str("ab".into()),
                Some(Value::Str("5".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str(" ab  ".into()),
            "the format spec puts the odd space on the RIGHT; str.center puts it on the left"
        );
    }

    /// The padding must not go through Rust's format spec: `format!("{:^width$}")`
    /// **panics** (`Formatting argument out of range`) past `u16::MAX`, while
    /// Django centers happily — measured `center("ab", 65536)` is a 65536-wide
    /// string with no error. Pinning the length keeps a later refactor from
    /// reintroducing the format spec, where a legal `{{ x|center:70000 }}` would
    /// panic. (`result.len()` on ASCII is the code-point count.)
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

    /// **The allocation guard**, and the one this body could not see before:
    /// past `u16::MAX` is fine (above), but a margin the allocator *refuses* is a
    /// different animal. `" ".repeat(10**18)` does not panic — it calls
    /// `handle_alloc_error` and **aborts**, which no `catch_unwind` sees and which
    /// takes the interpreter with it, where Django's `center("ab", 10**18)` raises
    /// a catchable `MemoryError` (measured, empty message). The margin must go
    /// through the fallible path in `filters::padding`.
    ///
    /// The failure mode is worth stating because it is unusual: this is a
    /// *behaviour* assertion, and if the body regresses to an infallible
    /// allocation the **abort is the failure** — the test binary dies by signal 6
    /// instead of reporting a failed assertion. There is no way to assert on an
    /// allocation that must not happen, so the abort is the signal.
    #[test]
    fn a_width_the_allocator_refuses_is_a_memory_error_not_an_abort() {
        let h = host(IntValue::Small(10_i64.pow(18)), "ab");
        let error = run(
            Value::Str("ab".into()),
            Some(Value::Str("1000000000000000000".into())),
            &call(&h),
        )
        .unwrap_err();
        match error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::Other("MemoryError".into()));
                assert_eq!(
                    host_error.message, "",
                    "CPython's MemoryError carries no message"
                );
                assert_eq!(
                    host_error.operation, "str.format",
                    "the error is the format spec's, not the shared helper's"
                );
            }
            other => panic!("expected a filter-host MemoryError, got {other:?}"),
        }
    }

    /// `width <= 0` returns the value **unchanged**, unlike the truncation
    /// filters' `""`, and `int(arg)` is evaluated before that test.
    #[test]
    fn a_non_positive_width_returns_the_value_unchanged() {
        for width in [0_i64, -1, -100] {
            let h = host(IntValue::Small(width), "ab");
            assert_eq!(
                run(
                    Value::Str("ab".into()),
                    Some(Value::Str("0".into())),
                    &call(&h)
                )
                .unwrap(),
                Value::Str("ab".into()),
                "center(_, {width})"
            );
        }
    }

    /// `int(5.0)` is `5`, so `center("abc", 5.0)` is `" abc "` (measured).
    #[test]
    fn a_float_argument_is_truncated_to_an_int() {
        let h = host(IntValue::Small(5), "abc");
        assert_eq!(
            run(Value::Str("abc".into()), Some(Value::Float(5.0)), &call(&h)).unwrap(),
            Value::Str(" abc ".into())
        );
        h.assert_called("to_int");
    }

    /// No `try`, so `int(arg)`'s `ValueError` escapes — and it escapes *before*
    /// the `<= 0` test, so `center("ab", "x")` raises rather than returning `ab`.
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

    /// `int(None)` is a `TypeError`.
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

    /// The two `Big` signs take different paths, so both are pinned: a negative
    /// width is the early return, a positive one is the format spec's
    /// `ValueError` (measured).
    #[test]
    fn a_big_width_is_sign_dependent() {
        let h = host(
            IntValue::Big("-1000000000000000000000000000000".to_string()),
            "ab",
        );
        assert_eq!(
            run(
                Value::Str("ab".into()),
                Some(Value::BigInt("-1000000000000000000000000000000".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("ab".into())
        );

        let h = host(
            IntValue::Big("1000000000000000000000000000000".to_string()),
            "ab",
        );
        let error = run(
            Value::Str("ab".into()),
            Some(Value::BigInt("1000000000000000000000000000000".into())),
            &call(&h),
        )
        .unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::ValueError);
                assert_eq!(
                    host_error.message,
                    "Too many decimal digits in format string"
                );
            }
            other => panic!("a positive Big width must raise, got {other:?}"),
        }
    }
}
