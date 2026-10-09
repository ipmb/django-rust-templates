//! `dictsortreversed` — `defaultfilters.py:590-599`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def dictsortreversed(value, arg):
//!     """
//!     Given a list of dicts, return that list sorted in reverse order by the
//!     property given in the argument.
//!     """
//!     try:
//!         return sorted(value, key=_property_resolver(arg), reverse=True)
//!     except (AttributeError, TypeError):
//!         return ""
//! ```
//!
//! `dictsort` with `reverse=True` — the **same** host call and the **same**
//! `(AttributeError, TypeError)` → `""` catch, and only the boolean differs. Both
//! filters are one [`FilterHost::sort_by_property`] call because
//! `_property_resolver` (`:540-575`) and the sort are one semantic unit; see
//! [`crate::filters::dictsort`] for why splitting them across the FFI would mean
//! reimplementing Python's exception classification in Rust.
//!
//! The argument's **type** is as load-bearing here as it is for `dictsort`
//! (§D7.1): `float(arg)` sends an int `1` to `itemgetter` and a string `'1'` to
//! the subscript chain, so `dictsortreversed(x, 1)` sorts and
//! `dictsortreversed(x, '1')` is `""`. The body forwards the `Value` unchanged.
//! `is_safe=False`: the result is never marked.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except (AttributeError, TypeError): return ""`.
fn is_caught(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if matches!(host.kind, PyErrorKind::AttributeError | PyErrorKind::TypeError)
    )
}

/// `dictsortreversed`'s body: `sorted(value, key=…, reverse=True)` through the
/// host, with `(AttributeError, TypeError)` → `""`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    match call
        .host
        .sort_by_property(&value, &arg.unwrap_or(Value::None), true)
    {
        Ok(sorted) => Ok(sorted),
        Err(error) if is_caught(&error) => Ok(Value::Str("".into())),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    fn host(sorted: Value) -> TestFilterHost {
        TestFilterHost {
            sort_by_property: Some(sorted),
            ..TestFilterHost::default()
        }
    }

    /// The sort and its reversal are the host's: the answer is returned unchanged
    /// and the host produced it. The `reverse=true` half of the call is what the
    /// corpus pins, since `TestFilterHost` does not record arguments.
    #[test]
    fn it_delegates_the_reversed_sort_to_the_host() {
        let sorted = Value::List(vec![Value::None, Value::None]);
        let h = host(sorted.clone());
        assert_eq!(
            run(
                Value::List(vec![Value::None, Value::None]),
                Some(Value::Str("age".into())),
                &call(&h)
            )
            .unwrap(),
            sorted
        );
        h.assert_called("sort_by_property");
    }

    /// The forgiving arm, both halves of the pair.
    #[test]
    fn an_attribute_or_type_error_is_the_empty_string() {
        for kind in [PyErrorKind::AttributeError, PyErrorKind::TypeError] {
            let h = host(Value::List(vec![]));
            h.raise_on("sort_by_property", kind.clone());
            assert_eq!(
                run(
                    Value::List(vec![Value::Int(1), Value::Int(2)]),
                    Some(Value::Str("age".into())),
                    &call(&h)
                )
                .unwrap(),
                Value::Str("".into()),
                "failure {kind:?} must answer ''"
            );
            h.assert_called("sort_by_property");
        }
    }

    /// A class outside the caught pair escapes.
    #[test]
    fn an_uncaught_class_escapes() {
        let h = host(Value::List(vec![]));
        h.raise_on("sort_by_property", PyErrorKind::KeyError);
        let error = run(
            Value::List(vec![Value::Int(1)]),
            Some(Value::Str("age".into())),
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::KeyError),
            "{error:?}"
        );
    }
}
