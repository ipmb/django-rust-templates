//! `default_if_none` — `defaultfilters.py:846-851`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def default_if_none(value, arg):
//!     """If value is None, use given default."""
//!     if value is None:
//!         return arg
//!     return value
//! ```
//!
//! **This body is pure and needs no host at all.** The test is `value is None`,
//! and `classify` maps Python `None` to `Value::None` unconditionally, so the
//! question is a Rust `matches!` rather than a `bool(value)`. That matters,
//! because the two questions have different answers: measured on the pinned tree,
//! `default_if_none("", "x") -> ""`, `(0, "x") -> 0`, `(False, "x") -> False` and
//! `([], "x") -> []` — none of them is `None`, so each comes back **unchanged**,
//! where a body written as `is_true` would return the argument. Calling
//! [`FilterHost::is_true`] here would be exactly the `default` filter's behaviour
//! under the wrong name.
//!
//! **The operands come back untouched** — no `str()` coercion (the registry's
//! `stringfilter` is `false`), no `mark_safe`, no rebuild. Measured,
//! `default_if_none(None, "x")` is the *argument* `SafeString('x')` when `"x"` is
//! a quoted constant, and `default_if_none("", "x")` is a plain `str`. The body
//! returns whichever `Value` it was handed; the safety mark travels with it.
//!
//! `arg.unwrap_or(Value::None)` is the established shape for a missing argument
//! (`crates/template-core/src/filters/center.rs:86`); the filter's arity is enforced above this body, so the
//! substitution is a shape rather than a value Django can observe.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `default_if_none`'s body.
///
/// `value is None` → the argument (untouched); otherwise the value (untouched).
/// The `_call` is unread: this filter's whole question is answerable in Rust,
/// and the seam is never consulted.
pub fn run(value: Value, arg: Option<Value>, _call: &FilterCall<'_>) -> Result<Value, Error> {
    if matches!(value, Value::None) {
        Ok(arg.unwrap_or(Value::None))
    } else {
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::{FilterHost, NoFilterHost, TestFilterHost};

    fn call(host: &dyn FilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// Every measured row of the table. `"x"` is a quoted template constant and
    /// so is a `SafeString` on both sides; the value is otherwise the real Python
    /// object. The rows a body written as `is_true` would get wrong are the ones
    /// with a falsy-but-not-`None` value: `""`, `0`, `False`, `[]` and `0.0`
    /// all come back **unchanged**.
    #[test]
    fn the_measured_rows() {
        let rows = [
            (
                Value::None,
                Value::Safe("x".into()),
                Value::Safe("x".into()),
                "SafeString",
            ),
            (
                Value::Str("".into()),
                Value::Safe("x".into()),
                Value::Str("".into()),
                "str",
            ),
            (Value::Int(0), Value::Safe("x".into()), Value::Int(0), "int"),
            (
                Value::Bool(false),
                Value::Safe("x".into()),
                Value::Bool(false),
                "bool",
            ),
            (
                Value::List(vec![]),
                Value::Safe("x".into()),
                Value::List(vec![]),
                "list",
            ),
            (
                Value::Str("a".into()),
                Value::Safe("x".into()),
                Value::Str("a".into()),
                "str",
            ),
            (
                Value::Float(0.0),
                Value::Safe("x".into()),
                Value::Float(0.0),
                "float",
            ),
        ];
        for (value, arg, expected, type_name) in rows {
            let h = TestFilterHost::default();
            let result = run(value.clone(), Some(arg.clone()), &call(&h)).unwrap();
            assert_eq!(
                result, expected,
                "default_if_none({value:?}, {arg:?}) must return {expected:?}"
            );
            assert_eq!(result.type_name(), type_name, "default_if_none({value:?})");
            assert!(
                h.calls().is_empty(),
                "a pure body touches no seam: {:?}",
                h.calls()
            );
        }
    }

    /// A body that rebuilt whichever operand it returned — re-`str`-ing it or
    /// dropping a safety marker — passes every text assertion above and fails
    /// here. The argument's `Safe` marker must survive, and an `Opaque` value
    /// that is not `None` must come back as the same handle rather than a copy
    /// Rust cannot even see.
    #[test]
    fn the_returned_operand_is_verbatim() {
        let h = TestFilterHost::default();

        let arg = Value::Safe("x".into());
        let result = run(Value::None, Some(arg.clone()), &call(&h)).unwrap();
        assert_eq!(result, arg);
        assert_eq!(result.type_name(), "SafeString");

        let value = Value::Opaque(7);
        let result = run(value.clone(), Some(Value::Int(1)), &call(&h)).unwrap();
        assert_eq!(result, value, "a non-None value comes back by identity");
    }

    /// The proof of purity: the body answers correctly with **no host at all**,
    /// and its first `is_true`-shaped reading is not made. Under [`NoFilterHost`]
    /// every operation refuses, so a body that consulted the seam would error
    /// here rather than panic-free return.
    #[test]
    fn it_is_pure_and_works_under_no_filter_host() {
        let call = call(&NoFilterHost);
        assert_eq!(
            run(Value::None, Some(Value::Safe("x".into())), &call).unwrap(),
            Value::Safe("x".into())
        );
        assert_eq!(
            run(Value::Str("".into()), Some(Value::Int(7)), &call).unwrap(),
            Value::Str("".into()),
            "an empty string is not None"
        );
        assert_eq!(
            run(Value::Int(0), Some(Value::Int(7)), &call).unwrap(),
            Value::Int(0)
        );
    }

    /// `{{ x|default_if_none }}` is the parser's arity error, but the `None` this
    /// body substitutes is still the shape of an absent argument: a `None` value
    /// returns it and a non-`None` value is returned instead.
    #[test]
    fn a_missing_argument_substitutes_none() {
        let h = TestFilterHost::default();
        assert_eq!(run(Value::None, None, &call(&h)).unwrap(), Value::None);
        assert_eq!(run(Value::Int(0), None, &call(&h)).unwrap(), Value::Int(0));
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }
}
