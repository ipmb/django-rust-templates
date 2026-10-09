//! `get_digit` — `defaultfilters.py:757-776`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def get_digit(value, arg):
//!     try:
//!         arg = int(arg)
//!         value = int(value)
//!     except ValueError:
//!         return value  # Fail silently for an invalid argument
//!     if arg < 1:
//!         return value
//!     try:
//!         return int(str(value)[-arg])
//!     except IndexError:
//!         return 0
//! ```
//!
//! **The first `try` catches `ValueError` only, and that is a branch, not
//! decoration.** `int(None)` is a `TypeError`, so `get_digit(None, 1)` raises
//! (measured) where the `except ValueError` arm would have returned the value.
//! The seam's class taxonomy is what makes the distinction expressible: an error
//! is caught exactly when `matches!(&error.kind, ErrorKind::FilterHost(e) if
//! e.kind == PyErrorKind::ValueError)`.
//!
//! **`int(arg)` runs first, and both conversions share one `try`.** So the
//! `except` arm returns the **original** `value` parameter, never a
//! half-converted one — measured `get_digit("abc", 1) -> "abc"` and
//! `get_digit("abc", 0) -> "abc"` (a `0` argument would otherwise reach the
//! `arg < 1` arm and return the *converted* value, which is the next point).
//!
//! **The two early returns return different things, and both are measured.**
//! `arg < 1` returns the **converted** value, so `get_digit("0123", 0) -> 123`
//! and not `"0123"`; the `except ValueError` arm returns the **original**, so
//! `get_digit(123, "abc") -> 123` (an int, which it already was) while
//! `get_digit("abc", 0) -> "abc"` (a str).
//!
//! **`str(value)[-arg]` indexes code points from the right, and then `int()` of
//! that character is what can raise.** Measured rows: `(123, 1) -> 3`,
//! `(123, 4) -> 0`, `(123, 0) -> 123`, `(123, -1) -> 123`, `(-123, 1) -> 3`,
//! `("0123", 5) -> 0`, `(10**30, 1) -> 0`, `(1, 10**30) -> 0`.
//!
//! **The trap is `get_digit(-1, 2)`.** `str(-1)` is `"-1"`, `[-2]` is `"-"`, and
//! `int("-")` raises `ValueError: invalid literal for int() with base 10: '-'`
//! (measured) — which the **second** `try` does *not* catch, because it names
//! `IndexError` only, so it escapes the filter. A Rust body that reduced the
//! digit numerically instead of going through the character would answer `1`
//! here and be silently wrong; this body reads the character first and only
//! then decides, which is what the `Some(c)` match below is for.
//!
//! **An argument at or beyond the length is the `IndexError` arm.** That covers
//! `arg == len(str(value))` and beyond (`(123, 9) -> 0`, `("0123", 5) -> 0`) and
//! every positive `Big` argument, which is past any string's length
//! (`(1, 10**30) -> 0`). An [`IntValue::Big`] is never zero and its text carries
//! the sign, so `arg < 1` for a `Big` is exactly `text.starts_with('-')` — the
//! same test `center.rs:90` uses.
//!
//! `str(value)` is [`IntValue`]'s canonical text (`Small(n) -> n.to_string()`,
//! `Big(t) -> t.clone()`), which is always ASCII digits with at most one leading
//! `-`. That is why the character's `int()` can be decided in Rust: its only
//! non-digit possibility is `-`, handled as the `ValueError` above.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `except ValueError: return value` — the only class the first `try` names.
fn is_value_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError
    )
}

/// One `int()` of the shared `try`, classified as the source sees it:
/// `Ok(Some(n))` for a value, `Ok(None)` for the `ValueError` the `except`
/// catches — the caller then returns the **original** `value` — and `Err` for
/// anything else, which escapes.
///
/// Takes the already-computed `Result` rather than the operand so that **both**
/// arms are testable directly. `TestFilterHost` holds a single scripted `to_int`
/// answer and raises for every call to an operation it is told to fail, so it
/// can make the first conversion fail but never the second alone — and the
/// second-conversion `ValueError` is exactly the `get_digit("abc", 1)` row.
fn converted(result: Result<IntValue, Error>) -> Result<Option<IntValue>, Error> {
    match result {
        Ok(int) => Ok(Some(int)),
        Err(error) if is_value_error(&error) => Ok(None),
        Err(error) => Err(error),
    }
}

/// The arithmetic once both conversions have succeeded: `if arg < 1: return
/// value`, then `int(str(value)[-arg])` with `IndexError -> 0`.
fn digit(value: &IntValue, arg: &IntValue) -> Result<IntValue, Error> {
    if below_one(arg) {
        return Ok(value.clone());
    }
    let text = int_text(value);
    let index = match arg {
        IntValue::Small(number) => (*number - 1) as usize,
        // A `Big` is canonical decimal text and never zero; a positive one is
        // larger than any string's length, so `[-arg]` is the `IndexError` arm
        // (measured `get_digit(1, 10**30) -> 0`).
        IntValue::Big(_) => return Ok(IntValue::Small(0)),
    };
    match text.chars().rev().nth(index) {
        None => Ok(IntValue::Small(0)),
        Some(c) => match c.to_digit(10) {
            Some(digit) => Ok(IntValue::Small(i64::from(digit))),
            // The only non-digit `str(value)` can hold is the leading `-`
            // (measured `get_digit(-1, 2)` -> `ValueError: invalid literal for
            // int() with base 10: '-'`). `int("-")` is a `ValueError`, and the
            // second `try` does **not** catch it, so it escapes.
            None => Err(invalid_literal(c)),
        },
    }
}

/// `arg < 1`, for both representations. A `Big`'s text carries the sign, and a
/// `Big` is never `"0"`.
fn below_one(arg: &IntValue) -> bool {
    match arg {
        IntValue::Small(number) => *number < 1,
        IntValue::Big(text) => text.starts_with('-'),
    }
}

/// `str(value)` for a converted Python int.
fn int_text(value: &IntValue) -> String {
    match value {
        IntValue::Small(number) => number.to_string(),
        IntValue::Big(text) => text.clone(),
    }
}

/// `IntValue -> Value`, the two-line conversion both early returns and the
/// digit result need.
fn to_value(int: IntValue) -> Value {
    match int {
        IntValue::Small(number) => Value::Int(number),
        IntValue::Big(text) => Value::BigInt(text.into_boxed_str()),
    }
}

/// `int("-")` — a `ValueError` the body raises for the one non-digit character
/// `str(value)` can contain. The operation is named `"int"` because no
/// [`FilterHost`] method performs it here (the same shape as `cut` naming
/// `str.replace` in `error.rs:160-165`).
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn invalid_literal(c: char) -> Error {
    Error::filter_host(
        PyErrorKind::ValueError,
        "int",
        format!("invalid literal for int() with base 10: '{c}'"),
    )
}

/// `get_digit`'s body.
///
/// `int(arg)` then `int(value)`, each classified by [`converted`]: a
/// `ValueError` returns the **original** `value` (before the second conversion
/// runs, so a `TypeError` can never be masked), anything else escapes. Then the
/// pure [`digit`] decides, and its own `ValueError` (`int("-")`) escapes via the
/// `?`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let arg_value = arg.unwrap_or(Value::None);
    let arg = match converted(call.host.to_int(&arg_value))? {
        Some(arg) => arg,
        None => return Ok(value),
    };
    let value_int = match converted(call.host.to_int(&value))? {
        Some(value) => value,
        None => return Ok(value),
    };
    Ok(to_value(digit(&value_int, &arg)?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::{NoFilterHost, TestFilterHost};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn scripted(int: IntValue) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(int),
            ..TestFilterHost::default()
        }
    }

    /// `10**30` as `IntValue::Big`.
    fn ten_to_the(n: usize) -> IntValue {
        IntValue::Big(format!("1{}", "0".repeat(n)))
    }

    /// Every arithmetic row of the measured table, driven through the pure
    /// helper with the **converted** ints the source's two `int()` calls
    /// produce. The conversions themselves are the host's and are covered by the
    /// `run` tests below; what this pins is `str(value)[-arg]`, `IndexError -> 0`
    /// and the two early returns.
    #[test]
    fn the_measured_digit_rows() {
        let rows = [
            (IntValue::Small(123), IntValue::Small(1), IntValue::Small(3)),
            (IntValue::Small(123), IntValue::Small(2), IntValue::Small(2)),
            (IntValue::Small(123), IntValue::Small(3), IntValue::Small(1)),
            (IntValue::Small(123), IntValue::Small(4), IntValue::Small(0)),
            (IntValue::Small(123), IntValue::Small(9), IntValue::Small(0)),
            // `arg < 1` returns the converted value, not the original text.
            (
                IntValue::Small(123),
                IntValue::Small(0),
                IntValue::Small(123),
            ),
            (
                IntValue::Small(123),
                IntValue::Small(-1),
                IntValue::Small(123),
            ),
            (
                IntValue::Small(-123),
                IntValue::Small(1),
                IntValue::Small(3),
            ),
            (
                IntValue::Small(-123),
                IntValue::Small(2),
                IntValue::Small(2),
            ),
            (IntValue::Small(0), IntValue::Small(1), IntValue::Small(0)),
            (IntValue::Small(0), IntValue::Small(2), IntValue::Small(0)),
            // `"0123"`, `123.9` and `True` all convert to 123/1; `1.9` to 1.
            (IntValue::Small(123), IntValue::Small(1), IntValue::Small(3)),
            (IntValue::Small(1), IntValue::Small(1), IntValue::Small(1)),
            // `"0123"` with 5: past the length -> the IndexError arm.
            (IntValue::Small(123), IntValue::Small(5), IntValue::Small(0)),
            // `10**30` is a Big whose last digit is 0; 40 is past its length.
            (ten_to_the(30), IntValue::Small(1), IntValue::Small(0)),
            (ten_to_the(30), IntValue::Small(40), IntValue::Small(0)),
            // A positive Big argument is past any length.
            (IntValue::Small(1), ten_to_the(30), IntValue::Small(0)),
            // A negative Big argument is `arg < 1`, so the value comes back.
            (
                IntValue::Small(123),
                IntValue::Big(format!("-{}", "1".repeat(30))),
                IntValue::Small(123),
            ),
        ];
        for (value, arg, expected) in rows {
            assert_eq!(
                digit(&value, &arg).unwrap(),
                expected,
                "digit({value:?}, {arg:?})"
            );
        }
    }

    /// **The trap.** `str(-1)` is `"-1"`, `[-2]` is `"-"`, and `int("-")` raises
    /// `ValueError`, which the second `try` does **not** catch — so it escapes
    /// the filter. A body that reduced the digit numerically instead of going
    /// through the character answers `1` here, and the message is pinned so the
    /// class cannot be right while the text is fabricated.
    #[test]
    fn a_minus_sign_indexed_as_the_digit_escapes() {
        let error = digit(&IntValue::Small(-1), &IntValue::Small(2)).unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host) => {
                assert_eq!(host.kind, PyErrorKind::ValueError);
                assert_eq!(host.message, "invalid literal for int() with base 10: '-'");
            }
            other => panic!("expected a ValueError, got {other:?}"),
        }
    }

    /// `str(value)` is the converted int's canonical text, not the argument's:
    /// a `Big` keeps its digits and a `Small` is spelled by `i64`.
    #[test]
    fn the_text_is_the_converted_ints_canonical_digits() {
        assert_eq!(int_text(&IntValue::Small(-123)), "-123");
        assert_eq!(int_text(&IntValue::Small(0)), "0");
        assert_eq!(
            int_text(&IntValue::Big("99999999999999999999".into())),
            "99999999999999999999"
        );
    }

    /// The classification of one `int()`: a `ValueError` is the `None` that
    /// makes the caller return the original value, and every other class
    /// escapes. Asserted directly for **both** positions, because
    /// `TestFilterHost` cannot make the second conversion fail while the first
    /// succeeds.
    #[test]
    fn a_value_error_is_caught_and_the_rest_escape() {
        assert_eq!(
            converted(Ok(IntValue::Small(7))).unwrap(),
            Some(IntValue::Small(7))
        );
        assert_eq!(
            converted(Err(Error::filter_host(
                PyErrorKind::ValueError,
                "to_int",
                "invalid literal"
            )))
            .unwrap(),
            None,
            "a ValueError is the `except` arm, which returns the original value"
        );
        for kind in [
            PyErrorKind::TypeError,
            PyErrorKind::IndexError,
            PyErrorKind::Other("OverflowError".into()),
        ] {
            let error =
                converted(Err(Error::filter_host(kind.clone(), "to_int", "x"))).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
        }
    }

    /// The seam is reached twice — `int(arg)` then `int(value)` — in that order,
    /// and its answers drive the arithmetic. The scripted host returns `Small(1)`
    /// for both calls, so the measured answer here is `get_digit(1, 1) -> 1`.
    #[test]
    fn it_converts_both_operands_in_source_order() {
        let h = scripted(IntValue::Small(1));
        assert_eq!(
            run(Value::Int(1), Some(Value::Int(1)), &call(&h)).unwrap(),
            Value::Int(1)
        );
        assert_eq!(
            h.calls(),
            vec!["to_int", "to_int"],
            "int(arg) then int(value)"
        );
    }

    /// The `except ValueError` arm returns the **original** value — measured
    /// `get_digit("abc", 1) -> "abc"` and `get_digit("abc", 0) -> "abc"` — not a
    /// converted one. A body that returned the converted `Small(0)` would answer
    /// `Value::Int(0)` here and fail on the type alone.
    #[test]
    fn a_value_error_returns_the_original_value() {
        for arg in [Value::Int(1), Value::Int(0)] {
            let h = scripted(IntValue::Small(0));
            h.raise_on("to_int", PyErrorKind::ValueError);
            let value = Value::Str("abc".into());
            assert_eq!(
                run(value.clone(), Some(arg), &call(&h)).unwrap(),
                value,
                "the original value comes back unchanged"
            );
            h.assert_called("to_int");
        }
    }

    /// `except ValueError` names one class: `int(None)` is a `TypeError`
    /// (measured `get_digit(None, 1)` raises) and it **escapes** the filter.
    #[test]
    fn a_type_error_escapes() {
        let h = scripted(IntValue::Small(1));
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::None, Some(Value::Int(1)), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "int(None) must escape, not return the value: {error:?}"
        );
    }

    /// A class that is neither `ValueError` nor `TypeError` escapes too, so the
    /// `converted` guard is not accidentally `catch-all`.
    #[test]
    fn another_class_escapes() {
        let h = scripted(IntValue::Small(1));
        h.raise_on("to_int", PyErrorKind::Other("OverflowError".into()));
        let error = run(Value::Int(1), Some(Value::Int(1)), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::Other("OverflowError".into())),
            "{error:?}"
        );
    }

    /// `{{ x|get_digit }}` is the parser's error, but the `None` this body
    /// substitutes must still reach the seam — `int(None)` is the `TypeError`
    /// row. A body that early-returned on a missing argument would answer where
    /// Django raises.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = scripted(IntValue::Small(1));
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Int(1), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError)
        );
        h.assert_called("to_int");
    }

    /// `IntValue -> Value` keeps a `Big`'s digits rather than saturating, and a
    /// `Small` is a plain `Value::Int`.
    #[test]
    fn a_big_result_keeps_its_digits() {
        assert_eq!(to_value(IntValue::Small(7)), Value::Int(7));
        assert_eq!(
            to_value(IntValue::Big("99999999999999999999".into())),
            Value::BigInt("99999999999999999999".into())
        );
    }

    /// The body needs the seam, so it cannot compute the answer in Rust and pass
    /// a bare-shell test by luck.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        assert!(run(Value::Int(123), Some(Value::Int(1)), &call).is_err());
    }
}
