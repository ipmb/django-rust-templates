//! `dictsort` — `defaultfilters.py:578-587`.
//!
//! ```python
//! def _property_resolver(arg):
//!     try:
//!         float(arg)
//!     except ValueError:
//!         if VARIABLE_ATTRIBUTE_SEPARATOR + "_" in arg or arg[0] == "_":
//!             raise AttributeError("Access to private variables is forbidden.")
//!         parts = arg.split(VARIABLE_ATTRIBUTE_SEPARATOR)
//!
//!         def resolve(value):
//!             for part in parts:
//!                 try:
//!                     value = value[part]
//!                 except (AttributeError, IndexError, KeyError, TypeError, ValueError):
//!                     value = getattr(value, part)
//!             return value
//!
//!         return resolve
//!     else:
//!         return itemgetter(arg)
//!
//!
//! @register.filter(is_safe=False)
//! def dictsort(value, arg):
//!     """
//!     Given a list of dicts, return that list sorted by the property given in
//!     the argument.
//!     """
//!     try:
//!         return sorted(value, key=_property_resolver(arg))
//!     except (AttributeError, TypeError):
//!         return ""
//! ```
//!
//! **One host call, and only one.** [`FilterHost::sort_by_property`] is
//! `sorted(value, key=_property_resolver(arg))` whole: the float-vs-string
//! branch, the private-name `AttributeError`, the five-exception
//! `__getitem__`→`getattr` fallback, and the sort over `Opaque` elements are a
//! single semantic unit that cannot be split across the FFI without Rust
//! reimplementing Python's exception classification — and `sorted` over `Opaque`
//! values is not expressible in Rust at all.
//!
//! **What Rust owns is the filter's own `(AttributeError, TypeError)` catch**,
//! and it is a match on [`ErrorKind::FilterHost`]'s class rather than a string —
//! the taxonomy exists for exactly this. Measured on the pinned tree:
//!
//! ```text
//! dictsort([{"age": 23}, {"age": 63}, {"age": 18}], "age") -> the list, by age
//! dictsort([("a", "42"), ("c", "string"), ("b", "foo")], 0) -> sorted by item 0
//! dictsort([1, 2, 3], "age") -> ""           (AttributeError -> "")
//! dictsort("Hello!", "age")  -> ""           (AttributeError -> "")
//! dictsort({"a": 1}, "age")  -> ""           (AttributeError -> "")
//! dictsort(1, "age")         -> ""           (TypeError -> "")
//! dictsort([{}], "._private")-> ""           (AttributeError -> "")
//! ```
//!
//! **The argument is a `&Value` and not a `&str`, and the difference is
//! observable** (§D7.1): `dictsort(['abc', 'xyz'], 1)` is `['abc', 'xyz']` — the
//! float-convertible `1` takes the `itemgetter` branch — while
//! `dictsort(['abc', 'xyz'], '1')` is `""`, because the *string* `'1'` takes the
//! subscript branch and `['abc', 'xyz']['1']` raises `TypeError`. Same digits,
//! different type, different answer, and Django's own test uses the int literal
//! (`filter_tests/test_dictsort.py:93`), so a corpus that only passes strings
//! never sees the string branch. `is_safe=False`: the result is never marked.

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

/// `dictsort`'s body: `sorted(value, key=…)` through the host, with
/// `(AttributeError, TypeError)` → `""`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // A missing argument is Python's `None`; `_property_resolver(None)` is the
    // `float(None)` `TypeError`, caught by the filter, so `dictsort(x)` is `""`.
    match call
        .host
        .sort_by_property(&value, &arg.unwrap_or(Value::None), false)
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

    /// The sort is wholly the host's: the answer is returned unchanged and the
    /// host is the one that produced it. A Rust body that re-sorted would not
    /// reach the seam at all.
    #[test]
    fn it_delegates_the_sort_to_the_host() {
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

    /// **The `&Value` argument, and why it cannot be a `&str`.** Two calls with
    /// the same digits and different types take different branches; the body must
    /// forward the `Value` untouched rather than coercing it. `TestFilterHost`
    /// does not record arguments, so the branch itself is the corpus's job — what
    /// this pins is that an `int` argument flows through as a `Value::Int` and not
    /// as text.
    #[test]
    fn an_integer_argument_is_forwarded_as_a_value() {
        let h = host(Value::List(vec![Value::None]));
        assert_eq!(
            run(
                Value::List(vec![Value::None]),
                Some(Value::Int(1)),
                &call(&h)
            )
            .unwrap(),
            Value::List(vec![Value::None])
        );
        h.assert_called("sort_by_property");
    }

    /// The forgiving arm, both halves: an invalid lookup (`AttributeError`) and a
    /// non-iterable value (`TypeError`) are `""`.
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

    /// A class outside the caught pair escapes — the pair is not `except
    /// Exception`.
    #[test]
    fn an_uncaught_class_escapes() {
        let h = host(Value::List(vec![]));
        h.raise_on("sort_by_property", PyErrorKind::ValueError);
        let error = run(
            Value::List(vec![Value::Int(1)]),
            Some(Value::Str("age".into())),
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError),
            "{error:?}"
        );
    }
}
