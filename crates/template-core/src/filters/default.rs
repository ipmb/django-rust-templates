//! `default` — `defaultfilters.py:840-843`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def default(value, arg):
//!     """If value is unavailable, use given default."""
//!     return value or arg
//! ```
//!
//! **The body is `bool(value)`, and that is the whole of it.** `value or arg`
//! evaluates `bool(value)` and returns `value` when it is truthy, `arg` when it
//! is not, so this is [`FilterHost::is_true`] followed by a choice between the
//! two operands. `is_true` is the host's because Python truthiness is not a Rust
//! `match`: it is `__bool__`, or `__len__` in its absence, on anything the crate
//! carries as a [`Value::Opaque`] — measured, a truthy object and a falsy one
//! cross the seam as the same variant.
//!
//! **Both operands come back untouched.** There is no `str()` coercion (the
//! registry's `stringfilter` is `false`), no `mark_safe`, and no rebuild of the
//! returned operand: measured on the pinned tree with `"x"` a quoted constant,
//! `default(0, "x")` is the argument, a `SafeString('x')`, while `default("a",
//! "x")` is the value, a plain `str`. A body that re-`str`-ed its answer would
//! drop that marker, which is invisible to a text comparison and exactly what
//! §D1 exists to catch. `default(0, 7)` is the plain `int` `7`.
//!
//! **The falsy rows are the point of the filter.** `0`, `0.0`, `""`, `[]`,
//! `False` and `None` all take the argument, while `"0"` is a *non-empty string*
//! and comes back — a body that tested for zero, or that read `is_none`, gets at
//! least one of those wrong. `True` is truthy and comes back unchanged.
//!
//! `arg.unwrap_or(Value::None)` is the established shape for a missing argument
//! (`center.rs:86`); the filter's arity is enforced above this body.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `default`'s body.
///
/// `bool(value)` — the host's — then the truthy operand: `value` when truthy,
/// the argument otherwise. Both are returned by identity.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let arg = arg.unwrap_or(Value::None);
    if call.host.is_true(&value)? {
        Ok(value)
    } else {
        Ok(arg)
    }
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

    fn host(truthy: bool) -> TestFilterHost {
        TestFilterHost {
            is_true: Some(truthy),
            ..TestFilterHost::default()
        }
    }

    /// Every measured row of the table, with `"x"` a quoted constant (a
    /// `SafeString`) and the value the real Python object. `is_true` is scripted
    /// so the row pins that the body *asks* rather than reimplements truthiness;
    /// the answers it returns are the operands themselves, checked by type name
    /// as well as by value.
    #[test]
    fn the_measured_rows() {
        let rows = [
            (Value::Int(0), false, Value::Safe("x".into()), "SafeString"),
            (Value::Int(1), true, Value::Int(1), "int"),
            (
                Value::Str("".into()),
                false,
                Value::Safe("x".into()),
                "SafeString",
            ),
            (Value::Str("a".into()), true, Value::Str("a".into()), "str"),
            (Value::None, false, Value::Safe("x".into()), "SafeString"),
            (
                Value::Bool(false),
                false,
                Value::Safe("x".into()),
                "SafeString",
            ),
            (Value::Bool(true), true, Value::Bool(true), "bool"),
            (
                Value::List(vec![]),
                false,
                Value::Safe("x".into()),
                "SafeString",
            ),
            (
                Value::List(vec![Value::Int(1)]),
                true,
                Value::List(vec![Value::Int(1)]),
                "list",
            ),
            (
                Value::Float(0.0),
                false,
                Value::Safe("x".into()),
                "SafeString",
            ),
            (Value::Str("0".into()), true, Value::Str("0".into()), "str"),
        ];
        for (value, truthy, expected, type_name) in rows {
            let h = host(truthy);
            let arg = Value::Safe("x".into());
            let result = run(value.clone(), Some(arg), &call(&h)).unwrap();
            assert_eq!(
                result, expected,
                "default({value:?}, Safe(\"x\")) with bool(value)={truthy}"
            );
            assert_eq!(result.type_name(), type_name, "default({value:?})");
            h.assert_called("is_true");
        }
    }

    /// The integer-argument rows, measured separately: the answer is the plain
    /// `int` `7`, unmarked and unrebuilt.
    #[test]
    fn an_int_argument_comes_back_as_an_int() {
        let rows = [
            (Value::Int(0), Value::Int(7)),
            (Value::None, Value::Int(7)),
            (Value::Str("".into()), Value::Int(7)),
        ];
        for (value, expected) in rows {
            let h = host(false);
            let result = run(value.clone(), Some(Value::Int(7)), &call(&h)).unwrap();
            assert_eq!(result, expected, "default({value:?}, 7)");
            assert_eq!(result.type_name(), "int");
            h.assert_called("is_true");
        }
    }

    /// Both operands are returned by identity, so a body that rebuilt its answer
    /// — re-`str`-ing it or dropping a marker — fails here while passing every
    /// text assertion. The falsy row returns the argument's `Safe`; the truthy
    /// row returns the value's `Opaque` handle untouched.
    #[test]
    fn the_returned_operand_is_verbatim() {
        let h = host(false);
        let arg = Value::Safe("x".into());
        let result = run(Value::Int(0), Some(arg.clone()), &call(&h)).unwrap();
        assert_eq!(result, arg);
        assert_eq!(result.type_name(), "SafeString");

        let value = Value::Opaque(4);
        let h = host(true);
        let result = run(value.clone(), Some(Value::Int(1)), &call(&h)).unwrap();
        assert_eq!(result, value, "the truthy value comes back by identity");
        assert_eq!(result.type_name(), "object");
    }

    /// `bool(value)` may raise — a `Value::Opaque` whose `__bool__` refuses — and
    /// the source has no `try`, so the class escapes with its identity intact.
    #[test]
    fn a_truthiness_refusal_escapes() {
        for kind in [
            PyErrorKind::ValueError,
            PyErrorKind::TypeError,
            PyErrorKind::Other("RuntimeError".into()),
        ] {
            let h = host(true);
            h.raise_on("is_true", kind.clone());
            let error = run(Value::Opaque(0), Some(Value::Int(1)), &call(&h)).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
            h.assert_called("is_true");
        }
    }

    /// `{{ x|default }}` is the parser's arity error, but the `None` this body
    /// substitutes must still reach the seam: a falsy value returns it, and a
    /// body that early-returned on a missing argument would not ask `is_true`.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = host(false);
        assert_eq!(
            run(Value::Str("".into()), None, &call(&h)).unwrap(),
            Value::None
        );
        h.assert_called("is_true");
    }

    /// `is_safe=False` is the registry's flag and the loop applies it in
    /// `template-py`; this body returns a plain `Value::Str` and never calls
    /// `mark_safe`. A body that did would return the scripted `Safe` and fail both
    /// the value and the call assertion.
    #[test]
    fn the_body_returns_a_plain_str_and_never_marks_safe() {
        let h = TestFilterHost {
            is_true: Some(false),
            mark_safe: Some(Value::Safe("marked".into())),
            ..TestFilterHost::default()
        };
        let result = run(
            Value::Str("".into()),
            Some(Value::Str("x".into())),
            &call(&h),
        )
        .unwrap();
        assert_eq!(result, Value::Str("x".into()));
        assert_eq!(result.type_name(), "str", "the loop marks, not the body");
        assert!(!h.calls().contains(&"mark_safe"), "{:?}", h.calls());
    }

    /// The body needs the seam, so it cannot reimplement truthiness in Rust and
    /// pass a bare-shell test by luck. `is_true` is a `Result` operation, so the
    /// bare-shell refusal is an ordinary `Err`.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        assert!(run(Value::Int(1), Some(Value::Int(2)), &call).is_err());
    }
}
