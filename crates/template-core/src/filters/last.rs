//! `last` — `defaultfilters.py:624-630`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def last(value):
//!     """Return the last item in a list."""
//!     try:
//!         return value[-1]
//!     except IndexError:
//!         return ""
//! ```
//!
//! `first`'s mirror image with two differences, both measured on the pinned tree:
//!
//! * the index is **`-1`**, so the host performs the negative-index lookup
//!   (`get_index(value, -1)`), not a Rust `items[items.len() - 1]`;
//! * `is_safe=True`, so the result is marked safe by the loop when the input was
//!   a `SafeString` — the body still returns the item unchanged and never calls
//!   `mark_safe` itself.
//!
//! The same single-class `except`, measured:
//!
//! ```text
//! last([0, 1, 2])  -> 2        (int)
//! last("test")     -> "t"      (str)
//! last([])         -> ""       (IndexError -> "")
//! last("")         -> ""       (IndexError -> "")
//! last(None)       -> TypeError: 'NoneType' object is not subscriptable (escapes)
//! last(5)          -> TypeError: 'int' object is not subscriptable     (escapes)
//! last({"a": 1})   -> KeyError: -1                                     (escapes)
//! ```

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except IndexError:` — and *only* `IndexError`.
fn is_index_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::IndexError)
}

/// `last`'s body: `value[-1]`, with `IndexError` → `""` and everything else
/// escaping.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    match call.host.get_index(&value, -1) {
        Ok(item) => Ok(item),
        Err(error) if is_index_error(&error) => Ok(Value::Str("".into())),
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

    fn host(item: Value) -> TestFilterHost {
        TestFilterHost {
            get_index: Some(item),
            ..TestFilterHost::default()
        }
    }

    /// `last([0, 1, 2])` is `2`, measured, returned through the host.
    #[test]
    fn it_returns_the_last_item_through_the_host() {
        let h = host(Value::Int(2));
        assert_eq!(
            run(
                Value::List(vec![Value::Int(0), Value::Int(1), Value::Int(2)]),
                None,
                &call(&h)
            )
            .unwrap(),
            Value::Int(2)
        );
        h.assert_called("get_index");
    }

    /// The forgiving arm: `IndexError` on an empty sequence is `""`.
    #[test]
    fn an_index_error_is_the_empty_string() {
        let h = host(Value::Int(0));
        h.raise_on("get_index", PyErrorKind::IndexError);
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        h.assert_called("get_index");
    }

    /// **The escaping exception**, the same one `first` has: `last(5)` is
    /// `TypeError: 'int' object is not subscriptable`, which the `except
    /// IndexError` does not catch.
    #[test]
    fn a_type_error_escapes_the_filter() {
        let h = host(Value::Int(0));
        h.raise_on("get_index", PyErrorKind::TypeError);
        let error = run(Value::Int(5), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "last(5) must raise TypeError: {error:?}"
        );
    }

    /// The negative index is the host's, so it is the host's `KeyError` for a
    /// dict that escapes (`{"a": 1}[-1]` is `KeyError: -1`).
    #[test]
    fn a_key_error_escapes_the_filter() {
        let h = host(Value::Int(0));
        h.raise_on("get_index", PyErrorKind::KeyError);
        let error = run(Value::None, None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::KeyError),
            "last({{'a': 1}}) must raise KeyError: {error:?}"
        );
    }
}
