//! `divisibleby` — `defaultfilters.py:854-858`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def divisibleby(value, arg):
//!     """Return True if the value is divisible by the argument."""
//!     return int(value) % int(arg) == 0
//! ```
//!
//! **There is no `try` anywhere, so every failure escapes the filter.** That is
//! the whole behavioural point, and it is measured: `divisibleby(4, 0)` raises
//! `ZeroDivisionError: division by zero`, `divisibleby("4.0", 2)` raises
//! `ValueError: invalid literal for int() with base 10: '4.0'`, and
//! `divisibleby(None, 2)` raises `TypeError: int() argument must be a string, a
//! bytes-like object or a real number, not 'NoneType'`. The operand in the
//! middle row is the **string** `"4.0"`: the *float* `4.0` is `True`, because
//! `int(4.0)` is `4` — the quoted message is `int()`'s of a `str`, and an
//! earlier revision of this paragraph wrote the float and so contradicted this
//! module's own `(4.9, 2) -> True` row. A body that swallowed
//! any of those and answered `false` would pass every value row below — `false`
//! is the value most rows produce — which is why the refusal rows are tests of
//! their own.
//!
//! **The body is `to_int` twice and then `int_modulo`.** The conversions are
//! deliberate even though [`FilterHost::int_modulo`] takes the *original*
//! operands: Python's `%` evaluates `int(value)` and then `int(arg)`, so the
//! class a bad operand raises is the **left** operand's, and `to_int` in source
//! order is what pins that. Measured, the two `int()`s and the operator produce
//! the three escaping classes above.
//!
//! **`int_modulo` takes the `Value`s, not the converted ints**, because it must
//! reproduce Python's own ordering and exception classes — including
//! `ZeroDivisionError`, which no Rust `%` on `i64` can produce as that class.
//! The `?` on it is not defensive: it is the filter's answer for `arg == 0`.
//!
//! The result is a Python `bool` (`Value::Bool`), never `SafeData`, and the
//! comparison to zero is against `IntValue::Small(0)`: an [`IntValue::Big`] is
//! canonical decimal text and never zero, so it is never divisible-by-zero. The
//! measured rows are `(4, 2) -> True`, `(5, 2) -> False`, `(0, 2) -> True`,
//! `(4.9, 2) -> True`, `("4", 2) -> True`, `(2**70, 2) -> True`,
//! `(-4, 2) -> True`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;
use crate::pynum::IntValue;

/// `divisibleby`'s body.
///
/// `int(value)` then `int(arg)` in source order (both `?`), then Python's
/// `%` through [`FilterHost::int_modulo`] over the **original** operands, and
/// the answer compared to `Small(0)`. No `try`: every one of the three `?`s is
/// an escaping failure in Django too.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let arg = arg.unwrap_or(Value::None);
    // Order matters and is observable: `int(value)` runs first, so a bad *left*
    // operand's class wins (measured `divisibleby(4, "a")` is the `ValueError`
    // of `int("a")`, while `divisibleby("a", None)` is `int("a")`'s, not
    // `int(None)`'s). The converted values themselves are discarded — the
    // modulo is `int_modulo`'s over the originals.
    call.host.to_int(&value)?;
    call.host.to_int(&arg)?;
    let remainder = call.host.int_modulo(&value, &arg)?;
    Ok(Value::Bool(remainder == IntValue::Small(0)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::{NoFilterHost, TestFilterHost};
    use crate::parser::error::{ErrorKind, PyErrorKind};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(operand: IntValue, remainder: IntValue) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(operand),
            int_modulo: Some(remainder),
            ..TestFilterHost::default()
        }
    }

    /// The remainder decides the answer, and `Small(0)` is the only truthy
    /// remainder. The `Big` rows are the ones a naive `== 0` on an `i64` would
    /// get wrong: an [`IntValue::Big`] is never zero, so it must answer `false`
    /// whatever its digits.
    #[test]
    fn the_remainder_is_compared_to_small_zero() {
        let rows = [
            (IntValue::Small(0), true),
            (IntValue::Small(1), false),
            (IntValue::Small(-1), false),
            (IntValue::Big("2".to_string()), false),
            (IntValue::Big("-2".to_string()), false),
        ];
        for (remainder, expected) in rows {
            let h = host(IntValue::Small(4), remainder.clone());
            assert_eq!(
                run(Value::Int(4), Some(Value::Int(2)), &call(&h)).unwrap(),
                Value::Bool(expected),
                "remainder {remainder:?} must be {}",
                if expected { "zero" } else { "non-zero" }
            );
            h.assert_called("to_int");
            h.assert_called("int_modulo");
        }
    }

    /// §D5's type half: the answer is a Python `bool`, not the `int` `1`/`0`.
    /// `Value::Bool` and `Value::Int` are structurally unequal, so a body that
    /// returned `Value::Int(1)` after the comparison fails here while passing
    /// every numeric assertion written with `1`.
    #[test]
    fn the_answer_is_a_bool_not_an_int() {
        let h = host(IntValue::Small(4), IntValue::Small(0));
        let result = run(Value::Int(4), Some(Value::Int(2)), &call(&h)).unwrap();
        assert_eq!(result, Value::Bool(true));
        assert_eq!(result.type_name(), "bool");
    }

    /// **The guard the `?` on `int_modulo` exists for.** Django has no `try`, so
    /// `4 % 0` raises `ZeroDivisionError` (measured) and it escapes. A body that
    /// caught the host error and answered `false` would pass every value row
    /// above.
    #[test]
    fn a_zero_divisor_escapes_as_zero_division_error() {
        let h = host(IntValue::Small(4), IntValue::Small(0));
        h.raise_on("int_modulo", PyErrorKind::ZeroDivisionError);
        let error = run(Value::Int(4), Some(Value::Int(0)), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ZeroDivisionError),
            "a zero divisor must escape: {error:?}"
        );
        h.assert_called("int_modulo");
    }

    /// A non-numeric operand's `ValueError` and a `None` operand's `TypeError`
    /// are `to_int`'s and both escape — nothing is caught.
    #[test]
    fn a_non_numeric_operand_escapes() {
        for kind in [PyErrorKind::ValueError, PyErrorKind::TypeError] {
            let h = host(IntValue::Small(4), IntValue::Small(0));
            h.raise_on("to_int", kind.clone());
            let error = run(Value::Int(4), Some(Value::Str("a".into())), &call(&h)).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
            h.assert_called("to_int");
            assert!(
                !h.calls().contains(&"int_modulo"),
                "the modulo must not be reached once an operand fails: {:?}",
                h.calls()
            );
        }
    }

    /// `int_modulo` may itself refuse with a `ValueError` (a `%` whose operands
    /// are not both numeric), and that escapes too — the `?` is not only for the
    /// zero divisor.
    #[test]
    fn a_value_error_from_the_modulo_escapes() {
        let h = host(IntValue::Small(4), IntValue::Small(0));
        h.raise_on("int_modulo", PyErrorKind::ValueError);
        let error = run(Value::Int(4), Some(Value::Str("a".into())), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError),
            "{error:?}"
        );
        h.assert_called("int_modulo");
    }

    /// Both operands are converted, in source order, **before** the modulo —
    /// Python evaluates `int(value)` then `int(arg)`. The recorded order is the
    /// only place a test host can see it (`TestFilterHost` records names, not
    /// arguments), and a body that swapped them would report the right classes
    /// for a single bad operand but the wrong one when both are bad.
    #[test]
    fn both_operands_are_converted_before_the_modulo() {
        let h = host(IntValue::Small(4), IntValue::Small(0));
        run(Value::Int(4), Some(Value::Int(2)), &call(&h)).unwrap();
        assert_eq!(
            h.calls(),
            vec!["to_int", "to_int", "int_modulo"],
            "int(value), int(arg), then %"
        );
    }

    /// `{{ x|divisibleby }}` is the parser's error, but the `None` this body
    /// substitutes must still reach the seam: `int(None)` is the `TypeError`
    /// row, and a body that early-returned on a missing argument would answer
    /// where Django raises. Both conversions are still made.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = host(IntValue::Small(4), IntValue::Small(0));
        run(Value::Int(4), None, &call(&h)).unwrap();
        assert_eq!(
            h.calls().iter().filter(|name| **name == "to_int").count(),
            2,
            "both operands are converted even with a missing argument: {:?}",
            h.calls()
        );
        h.assert_called("int_modulo");
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
        assert!(run(Value::Int(4), Some(Value::Int(2)), &call).is_err());
    }
}
