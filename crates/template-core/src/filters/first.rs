//! `first` — `defaultfilters.py:602-608`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def first(value):
//!     """Return the first item in a list."""
//!     try:
//!         return value[0]
//!     except IndexError:
//!         return ""
//! ```
//!
//! **The trap is the single class in the `except`, measured rather than
//! inferred.** `first` is `value[0]`, and the only failure it forgives is
//! `IndexError`; every other class escapes the filter. Measured on the pinned
//! tree:
//!
//! ```text
//! first([0, 1, 2])  -> 0        (int)
//! first("test")     -> "t"      (str)
//! first([None, 1])  -> None     (NoneType)
//! first("")         -> ""       (IndexError -> "")
//! first([])         -> ""       (IndexError -> "")
//! first(5)          -> TypeError: 'int' object is not subscriptable  (escapes)
//! first(None)       -> TypeError: 'NoneType' object is not subscriptable (escapes)
//! first({"a": 1})   -> KeyError: 0                                    (escapes)
//! ```
//!
//! So the body is `value[0]` through [`FilterHost::get_index`] — **not** an
//! `iter_values` walk, which would answer `""` for a generator where Django
//! raises `TypeError`. A dict is the second escape: `{"a": 1}[0]` is `KeyError`,
//! which the `except IndexError` does not catch.
//!
//! `is_safe=False` and no `@stringfilter`, so nothing is coerced and nothing is
//! marked: the first item comes back with its own type (`first([0, 1, 2])` is the
//! `int` `0`, and the corpus's §D5 comparison depends on that).

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except IndexError:` — and *only* `IndexError`.
fn is_index_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::IndexError)
}

/// `first`'s body: `value[0]`, with `IndexError` → `""` and everything else
/// escaping.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    match call.host.get_index(&value, 0) {
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

    /// `first([0, 1, 2])` is the `int` `0`, measured — the item is returned
    /// unchanged, with its own type, and the host is what read it.
    #[test]
    fn it_returns_the_first_item_through_the_host() {
        let h = host(Value::Int(0));
        assert_eq!(
            run(
                Value::List(vec![Value::Int(0), Value::Int(1), Value::Int(2)]),
                None,
                &call(&h)
            )
            .unwrap(),
            Value::Int(0)
        );
        h.assert_called("get_index");
    }

    /// `first("test")` is `"t"` and `first([None, 1])` is `None` — the item's own
    /// type survives, which is what `is_safe=False` means here.
    #[test]
    fn the_item_keeps_its_own_type() {
        let h = host(Value::None);
        assert_eq!(
            run(
                Value::List(vec![Value::None, Value::Int(1)]),
                None,
                &call(&h)
            )
            .unwrap(),
            Value::None
        );
    }

    /// The forgiving arm: `IndexError` (an empty list or string) becomes `""`.
    /// A body that walked the list in Rust and answered `""` without reaching the
    /// host would still pass *this* row, which is why the call is asserted too.
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

    /// **The escaping exception.** `first(5)` is `TypeError: 'int' object is not
    /// subscriptable` — the `except IndexError` does not catch it, so it leaves
    /// the filter. A body that caught every host failure and returned `""` fails
    /// here.
    #[test]
    fn a_type_error_escapes_the_filter() {
        let h = host(Value::Int(0));
        h.raise_on("get_index", PyErrorKind::TypeError);
        let error = run(Value::Int(5), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "first(5) must raise TypeError: {error:?}"
        );
    }

    /// A `dict`'s `KeyError` escapes as well: `{"a": 1}[0]` is `KeyError: 0`.
    #[test]
    fn a_key_error_escapes_the_filter() {
        let h = host(Value::Int(0));
        h.raise_on("get_index", PyErrorKind::KeyError);
        let error = run(Value::None, None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::KeyError),
            "first({{'a': 1}}) must raise KeyError: {error:?}"
        );
    }
}
