//! `ljust` — `defaultfilters.py:417-421`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def ljust(value, arg):
//!     """Left-align the value in a field of a given width."""
//!     return value.ljust(int(arg))
//! ```
//!
//! **Three measured facts, each of which a plausible body gets wrong.**
//!
//! * **`int(arg)` is Python's `int`, not a parse.** `int(" 5 ")` is `5` and
//!   `int("5_0")` is `50` (measured), which Rust's `str::parse` rejects, so the
//!   conversion is [`FilterHost::to_int`]'s and never a Rust `parse`. A **float**
//!   argument is truncated, not rounded: `ljust("abc", 5.0)` is `"abc  "`
//!   (measured).
//! * **`str.ljust` counts code points**, not bytes or grapheme clusters:
//!   `ljust("e\u{301}", 3)` is `"e\u{301} "` (measured — the two-code-point pair
//!   takes a single space). So the length is `chars().count()`, never `len()`.
//! * **The `ValueError` escapes.** There is no `try` here, so `ljust("ab", "x")`
//!   is CPython's `ValueError: invalid literal for int() with base 10: 'x'`
//!   out of the filter (measured). Catching it — the shape `truncatechars` has —
//!   would turn Django's raise into an answer.
//!
//! A width that does not fit `Py_ssize_t` is an `OverflowError` **for either
//! sign**: `'ab'.ljust(10**30)` and `'ab'.ljust(-10**30)` both raise
//! `OverflowError: Python int too large to convert to C ssize_t` (measured),
//! because CPython converts the width before it compares it with the length. That
//! is exactly the set `IntValue::Big` names, so the class is reproduced rather
//! than the padding attempted.
//!
//! **A width that fits `Py_ssize_t` and is still too large to allocate is a
//! `MemoryError`**, not an `OverflowError` — `ljust("ab", 10**18)` raises one in
//! Python (measured) — so the padding allocates **fallibly**:
//! `" ".repeat(margin)` would abort the process rather than raise. See
//! [`pad_spaces`](crate::filters::padding::pad_spaces).
//!
//! `@stringfilter` coerces the **value** (never `arg`), so the text comes back
//! through [`FilterHost::to_str`]; `is_safe=True`, so the loop marks the result
//! safe when it should and this body never calls `mark_safe`.

use crate::filters::host::FilterCall;
use crate::filters::padding::pad_spaces;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};
use crate::pynum::IntValue;

/// The `OverflowError` CPython raises when the width does not fit `Py_ssize_t` —
/// for **either** sign, because it converts the width before comparing it with
/// the length (measured on `'ab'.ljust(±10**30)`). `IntValue::Big` is exactly
/// that set. No [`FilterHost`] method performs the padding, so `operation` names
/// the CPython method this body stands in for.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn too_large() -> Error {
    Error::filter_host(
        PyErrorKind::Other("OverflowError".into()),
        "str.ljust",
        "Python int too large to convert to C ssize_t",
    )
}

/// `ljust`'s body.
///
/// `int(arg)` is the host's (Python's `int`, not a parse) and there is no `try`,
/// so its error escapes; the padding is Rust's and counts **code points**.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // The literal `None` arrives as `Some(Value::None)` and is `int(None)`, a
    // `TypeError` the real host raises (`crates/template-py/src/filters.rs:1601-1609`);
    // a missing argument never reaches a body because the parser's arity check
    // fires first, measured `ljust requires 2 arguments, 1 provided`
    // (`base.py:819`).
    let width = match call.host.to_int(&arg.unwrap_or(Value::None))? {
        IntValue::Small(width) => width,
        IntValue::Big(_) => return Err(too_large()),
    };
    let text = call.host.to_str(&value)?;
    let length = text.chars().count() as i64;
    if width <= length {
        return Ok(Value::Str(text.into()));
    }
    let padded = pad_spaces("str.ljust", &text, 0, (width - length) as usize)?;
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

    /// The measured rows, including the code-point and no-padding edges: a width
    /// at or below the length is the text unchanged (Python returns early), and a
    /// combining pair counts as **two** code points.
    #[test]
    fn it_pads_on_the_right_by_code_points() {
        let rows = [
            (5_i64, "ab", "ab   "),
            (2, "ab", "ab"),
            (1, "ab", "ab"),
            (0, "ab", "ab"),
            (-3, "ab", "ab"),
            (3, "", "   "),
            (3, "\u{e9}", "\u{e9}  "),
            // Two code points (`e` + U+0301), so one pad — a body counting bytes
            // would find length 3 and pad nothing.
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
                "ljust({text:?}, {width})"
            );
        }
    }

    /// The padding must not go through Rust's format spec: `format!("{:<width$}")`
    /// **panics** (`Formatting argument out of range`) past `u16::MAX`, while
    /// Django pads happily — measured `ljust("ab", 65536)` is a 65536-wide string
    /// with no error. Pinning the length keeps a later refactor from
    /// reintroducing the format spec, where a legal `{{ x|ljust:70000 }}` would
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

    /// **The allocation guard**, and it is a *different* huge-width regime from
    /// `too_large` above: `IntValue::Big` (past `Py_ssize_t`) is `OverflowError`
    /// before any padding is attempted, but a width that *fits* `Py_ssize_t` and
    /// is still too large to allocate reaches the padding — and there
    /// `" ".repeat(n)` calls `handle_alloc_error` and **aborts**, where Django's
    /// `ljust("ab", 10**18)` raises a catchable `MemoryError` (measured). So the
    /// two are not one guard split in two: one is a width Python cannot convert,
    /// the other a buffer Python cannot allocate. See `filters::padding`.
    ///
    /// The failure mode is the abort: if the body regresses to an infallible
    /// allocation this test kills the test binary by signal 6 rather than failing.
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
                    host_error.operation, "str.ljust",
                    "the error is this method's, not the shared helper's"
                );
            }
            other => panic!("expected a filter-host MemoryError, got {other:?}"),
        }
    }

    /// The width is Python's `int(arg)`, reached through the seam — so `" 5 "`
    /// (width 5) and `"5_0"` (width 50) are the host's grammar, not Rust's.
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
            Value::Str("ab   ".into())
        );
        h.assert_called("to_int");

        let h = host(IntValue::Small(50), "ab");
        assert_eq!(
            run(
                Value::Str("ab".into()),
                Some(Value::Str("5_0".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str(format!("ab{}", " ".repeat(48)).into())
        );
        h.assert_called("to_int");
    }

    /// A **float** argument is truncated by `int`: `int(5.0)` is `5` (measured
    /// `ljust("abc", 5.0)` is `"abc  "`). Scripted here, because the truncation is
    /// the host's — the point of the row is that a float argument is routed to
    /// `to_int` rather than rejected.
    #[test]
    fn a_float_argument_is_truncated_to_an_int() {
        let h = host(IntValue::Small(5), "abc");
        assert_eq!(
            run(Value::Str("abc".into()), Some(Value::Float(5.0)), &call(&h)).unwrap(),
            Value::Str("abc  ".into())
        );
        h.assert_called("to_int");
    }

    /// The text is read through the seam, so a value the loop coerced from a
    /// non-string still pads its text.
    #[test]
    fn the_text_is_read_through_the_host() {
        let h = host(IntValue::Small(3), "5");
        assert_eq!(
            run(Value::Int(5), Some(Value::Str("3".into())), &call(&h)).unwrap(),
            Value::Str("5  ".into())
        );
        h.assert_called("to_str");
    }

    /// `ljust` has no `try`, so `int(arg)`'s `ValueError` is the filter's answer
    /// — it must **escape**, not be swallowed into an unchanged value.
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
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::ValueError);
                assert_eq!(host_error.operation, "to_int");
            }
            other => panic!("`int(arg)`'s ValueError must reach the caller, got {other:?}"),
        }
    }

    /// A `None` argument is `int(None)`, a `TypeError` (measured), and `ljust`
    /// does not catch it. The FFI hands the literal `None` as `Some(Value::None)`
    /// (`crates/template-py/src/filters.rs:1601-1609`), which this body routes to the
    /// seam as `Value::None`, so the real host raises the class Django's does.
    #[test]
    fn a_none_argument_escapes_as_type_error() {
        let h = host(IntValue::Small(5), "ab");
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Str("ab".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "int(None) is a TypeError: {error:?}"
        );
        h.assert_called("to_int");
    }

    /// A width past `Py_ssize_t` is CPython's `OverflowError`, for either sign
    /// (measured). `IntValue::Big` is exactly that set, so the class and message
    /// are reproduced instead of a padding nobody can allocate.
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
                    assert_eq!(
                        host_error.kind,
                        PyErrorKind::Other("OverflowError".into()),
                        "{big}"
                    );
                    assert_eq!(
                        host_error.message, "Python int too large to convert to C ssize_t",
                        "{big}"
                    );
                }
                other => panic!("a Big width must raise OverflowError, got {other:?}"),
            }
        }
    }
}
