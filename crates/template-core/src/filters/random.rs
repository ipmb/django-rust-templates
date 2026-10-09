//! `random` — `defaultfilters.py:642-648`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def random(value):
//!     """Return a random item from the list."""
//!     try:
//!         return random_module.choice(value)
//!     except IndexError:
//!         return ""
//! ```
//!
//! The choice is `random.choice` — stateful, so it is the host's and not a
//! function of the argument ([`FilterHost::random_choice`]). The single caught
//! class is `IndexError` → `""`, measured:
//!
//! ```text
//! random([])        -> ""      (IndexError -> "")
//! random("a&b")     -> "a"     (str)      <- a string is a sequence
//! random([7, 8, 9]) -> 8       (int)
//! random(5)         -> TypeError: object of type 'int' has no len()      (escapes)
//! random(None)      -> TypeError: object of type 'NoneType' has no len() (escapes)
//! random({"a": 1})  -> KeyError: 0                                       (escapes)
//! ```
//!
//! The `dict` row is the one worth naming: `random.choice` indexes the container,
//! and a `dict` is not a sequence, so it raises `KeyError`, not `IndexError` — it
//! escapes the filter. `is_safe=True`, so a `SafeString` input is marked by the
//! loop; the body returns the item unchanged and never calls `mark_safe` itself.
//!
//! **The answer is chosen at random**, so the central corpus asserts
//! *membership* and not equality (Review Focus 4): an equality row for an
//! n-element list would be green one time in n. The unit test below scripts one
//! answer to prove the pass-through and reaches the host; it deliberately does
//! **not** assert what `random.choice` would pick.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except IndexError:` — and *only* `IndexError` (`KeyError` from a `dict`
/// escapes).
fn is_index_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::IndexError)
}

/// `random`'s body: `random.choice(value)` through the host, with `IndexError` →
/// `""`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    match call.host.random_choice(&value) {
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
            random_choice: Some(item),
            ..TestFilterHost::default()
        }
    }

    /// The choice is the host's, and the item it chose is returned unchanged —
    /// the scripted answer stands in for whichever element `random.choice` picked,
    /// because the body's job is the pass-through, not the pick.
    #[test]
    fn it_returns_the_hosts_choice_unchanged() {
        let h = host(Value::Int(8));
        assert_eq!(
            run(
                Value::List(vec![Value::Int(7), Value::Int(8), Value::Int(9)]),
                None,
                &call(&h)
            )
            .unwrap(),
            Value::Int(8)
        );
        h.assert_called("random_choice");
    }

    /// The forgiving arm: an empty sequence is `IndexError` → `""`.
    #[test]
    fn an_index_error_is_the_empty_string() {
        let h = host(Value::Int(0));
        h.raise_on("random_choice", PyErrorKind::IndexError);
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        h.assert_called("random_choice");
    }

    /// **The escaping class the brief's briefest gloss would miss.** `random` on
    /// a `dict` is `KeyError: 0` (measured), which the `except IndexError` does
    /// not catch — a body that treated every failure as "empty" would answer `""`
    /// here.
    #[test]
    fn a_key_error_from_a_dict_escapes() {
        let h = host(Value::Int(0));
        h.raise_on("random_choice", PyErrorKind::KeyError);
        let error = run(Value::None, None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::KeyError),
            "random({{'a': 1}}) must raise KeyError: {error:?}"
        );
    }

    /// A `TypeError` (a number, `None`) escapes as well.
    #[test]
    fn a_type_error_escapes() {
        let h = host(Value::Int(0));
        h.raise_on("random_choice", PyErrorKind::TypeError);
        let error = run(Value::Int(5), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "random(5) must raise TypeError: {error:?}"
        );
    }
}
