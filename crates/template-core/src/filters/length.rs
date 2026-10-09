//! `length` — `defaultfilters.py:633-639`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def length(value):
//!     """Return the length of the value - useful for lists."""
//!     try:
//!         return len(value)
//!     except (ValueError, TypeError):
//!         return 0
//! ```
//!
//! `len(value)` is the host's, and the caught pair is measured: `(ValueError,
//! TypeError)` → `0`. A `dict` has a length (`{"a": 1, "b": 2}` is `2`), a string
//! counts code points (`"1234"` is `4`), and both a number and `None` are the
//! `TypeError` arm:
//!
//! ```text
//! length("1234")          -> 4  (int)
//! length([1, 2, 3, 4])    -> 4  (int)
//! length({"a": 1, "b": 2})-> 2  (int)   <- a dict has a length
//! length(7)               -> 0  (TypeError -> 0)
//! length(None)            -> 0  (TypeError -> 0)
//! ```
//!
//! The result is an `int`, and `is_safe=False` means it is never marked safe —
//! `length(mark_safe("django"))` is still the plain `int` `6` (measured).
//!
//! The `ValueError` half of the pair is defensive on the pinned tree (no builtin
//! `__len__` raises it), but it is caught in the source and so is caught here: a
//! host that reports one must get `0`, not an escape.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except (ValueError, TypeError):` → `0`.
fn is_caught(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if matches!(host.kind, PyErrorKind::ValueError | PyErrorKind::TypeError)
    )
}

/// `length`'s body: `len(value)` through the host, with `(ValueError, TypeError)`
/// → `0`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    match call.host.len(&value) {
        Ok(length) => Ok(Value::Int(length as i64)),
        Err(error) if is_caught(&error) => Ok(Value::Int(0)),
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

    fn host(length: usize) -> TestFilterHost {
        TestFilterHost {
            len: Some(length),
            ..TestFilterHost::default()
        }
    }

    /// `length("django")` is `6` and `length([1, 2, 3, 4])` is `4`, measured —
    /// the length comes through the host as an `int`.
    #[test]
    fn it_returns_the_length_through_the_host() {
        for (length, value) in [
            (6usize, Value::Str("django".into())),
            (4, Value::List(vec![Value::Int(1); 4])),
            (2, Value::None),
        ] {
            let h = host(length);
            assert_eq!(
                run(value, None, &call(&h)).unwrap(),
                Value::Int(length as i64)
            );
            h.assert_called("len");
        }
    }

    /// A `dict` has a length — `length({"a": 1, "b": 2})` is `2` (measured) — so
    /// the body must not treat "not a list or string" as zero.
    #[test]
    fn a_mapping_is_measured_not_refused() {
        let h = host(2);
        assert_eq!(run(Value::None, None, &call(&h)).unwrap(), Value::Int(2));
        h.assert_called("len");
    }

    /// The forgiving arm, both halves: `TypeError` (a number, `None`) and
    /// `ValueError` are `0`.
    #[test]
    fn a_length_error_is_zero() {
        for kind in [PyErrorKind::TypeError, PyErrorKind::ValueError] {
            let h = host(0);
            h.raise_on("len", kind.clone());
            assert_eq!(
                run(Value::Int(7), None, &call(&h)).unwrap(),
                Value::Int(0),
                "len failure {kind:?} must answer 0"
            );
            h.assert_called("len");
        }
    }

    /// A class *outside* the caught pair escapes — the pair is not `except
    /// Exception`.
    #[test]
    fn an_uncaught_class_escapes() {
        let h = host(0);
        h.raise_on("len", PyErrorKind::KeyError);
        let error = run(Value::None, None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::KeyError),
            "{error:?}"
        );
    }
}
