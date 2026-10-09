//! `pprint` — `defaultfilters.py:990-996`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def pprint(value):
//!     """A wrapper around pprint.pprint -- for debugging, really."""
//!     try:
//!         return pformat(value)
//!     except Exception as e:
//!         return "Error in formatting: %s: %s" % (e.__class__.__name__, e)
//! ```
//!
//! The dump is [`FilterHost::pformat`], whole — `pprint.pformat`, whose width,
//! sorting, recursion detection and `_safe_repr` are all CPython's, not Rust's.
//! The `try`/`except` is the body's, and it is the one branch: a failure is not
//! propagated but **rendered**, as `e.__class__.__name__` and `str(e)` — which is
//! why the seam carries a class *name* ([`PyErrorKind::name`]) beside its
//! message, and this is the only body that needs it.
//!
//! **No argument** — measured, `{{ x|pprint:1 }}` is `TemplateSyntaxError:
//! pprint requires 1 arguments, 2 provided` out of `args_check`, so `_arg` is
//! unreachable rather than ignored.
//!
//! Measured rows (pinned 6.0.9, through `FilterExpression.resolve`):
//!
//! ```text
//! [1, 2, 3]        -> '[1, 2, 3]'
//! {'a': 1, 'b': 2} -> "{'a': 1, 'b': 2}"
//! 'abc'            -> "'abc'"
//! 5                -> '5'
//! None             -> 'None'
//! True             -> 'True'
//! (1, 2)           -> '(1, 2)'          (a tuple, so `Opaque` here)
//! mark_safe('abc') -> (SafeString, "'abc'") safe=True
//! ```
//!
//! The `except` arm is **reachable**, and its text is reproducible: an object
//! whose `__repr__` raises gives
//!
//! ```text
//! ValueError('boom repr')     -> 'Error in formatting: ValueError: boom repr'
//! ValueError()                -> 'Error in formatting: ValueError: '
//! RecursionError('deep repr') -> 'Error in formatting: RecursionError: deep repr'
//! KeyError('key repr')        -> "Error in formatting: KeyError: 'key repr'"
//! ```
//!
//! — on the object itself and on it nested in a list or a dict, and the class
//! name is Python's `__class__.__name__` for **any** class, `RecursionError`
//! included (which is why the seam's name-carrying variant is not one of the
//! six). `KeyError`'s row is `str(e)`, which for a `KeyError` is the *`repr`* of
//! its argument, so the quotes are Django's and not this body's.
//!
//! **One residual, measured and stated rather than hidden.** Django's `except
//! Exception` does **not** catch a `BaseException` that is not an `Exception`, so
//! a `__repr__` raising `KeyboardInterrupt`, `SystemExit`, `GeneratorExit` or a
//! custom `BaseException` subclass **propagates** out of the filter (measured,
//! both directly and out of `FilterExpression.resolve`), where this body would
//! render it. The seam cannot tell the two apart: it carries the class *name*,
//! not the hierarchy, and a hardcoded four-name list would still misfire on a
//! custom `BaseException` subclass — while *looking* correct, which is worse than
//! the uniform gap. Closing it needs a host operation that answers "is this a
//! Python `Exception`", which the trait does not have; reported rather than
//! approximated.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind};

/// Django's `"Error in formatting: %s: %s" % (e.__class__.__name__, e)` —
/// `defaultfilters.py:996`.
///
/// `None` when the failure is **not** a Python exception: a host that refused
/// (no [`ErrorKind::FilterHost`] payload) has nothing for Django's `except
/// Exception` to have caught, so there is no class name and no `str(e)` to
/// render, and the caller propagates it. The real host always answers with a
/// `FilterHost` error, so this arm is the refuse-everything host's and the
/// shape a body must not mistake for Django's.
fn formatting_failure(error: &Error) -> Option<String> {
    match &error.kind {
        ErrorKind::FilterHost(host) => Some(format!(
            "Error in formatting: {}: {}",
            // Python's `__class__.__name__` for *any* class.
            host.kind.name(),
            // `str(e)` — the seam's kept exception's own text.
            host.message,
        )),
        _ => None,
    }
}

/// `pprint`'s body: `pformat(value)`, with the failure rendered.
///
/// The whole answer comes from [`FilterHost::pformat`]; the body owns only the
/// `"Error in formatting: …"` formatting of an exception the host reports.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    match call.host.pformat(&value) {
        Ok(dump) => Ok(Value::Str(dump.into())),
        // `except Exception as e: return "Error in formatting: %s: %s" % …`.
        Err(error) => match formatting_failure(&error) {
            Some(text) => Ok(Value::Str(text.into())),
            None => Err(error),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::PyErrorKind;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(dump: &str) -> TestFilterHost {
        TestFilterHost {
            pformat: Some(dump.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The whole body is the one seam call, returned as a plain `str` — the loop
    /// marks a `SafeData` input, not the body.
    #[test]
    fn it_returns_the_hosts_pformat() {
        let h = host("[1, 2, 3]");
        assert_eq!(
            run(
                Value::List(vec![Value::Int(1), Value::Int(2), Value::Int(3)]),
                None,
                &call(&h)
            )
            .unwrap(),
            Value::Str("[1, 2, 3]".into())
        );
        h.assert_called("pformat");
    }

    /// A plain input is not marked by the body. `mark_safe` is not scripted, so
    /// a body that wrongly marked would panic rather than pass.
    #[test]
    fn the_body_does_not_mark_the_result_safe() {
        let h = host("'abc'");
        let result = run(Value::Str("abc".into()), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("'abc'".into()));
        assert_ne!(result, Value::Safe("'abc'".into()));
    }

    /// **The `except` arm, and the measurement is `ValueError: boom repr`.** The
    /// scripted host's message is `"scripted ValueError"`, so the rendered text
    /// pins both halves the seam supplies: the class name and `str(e)`.
    #[test]
    fn a_host_failure_becomes_djangos_error_string() {
        let h = TestFilterHost::default();
        h.raise_on("pformat", PyErrorKind::ValueError);
        assert_eq!(
            run(Value::Str("abc".into()), None, &call(&h)).unwrap(),
            Value::Str("Error in formatting: ValueError: scripted ValueError".into()),
            "measured: `Error in formatting: ValueError: boom repr`"
        );
        h.assert_called("pformat");
    }

    /// An `Other` kind renders **its Python name**, not its `Debug` form — the
    /// whole reason `PyErrorKind::Other` carries `__name__`. Measured:
    /// `RecursionError('deep repr')` gives
    /// `'Error in formatting: RecursionError: deep repr'`.
    #[test]
    fn any_class_name_is_rendered_from_the_seam() {
        let h = TestFilterHost::default();
        h.raise_on("pformat", PyErrorKind::Other("RecursionError".into()));
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("Error in formatting: RecursionError: scripted RecursionError".into())
        );
    }

    /// A **refusal** is not a Python exception, so Django's `except Exception`
    /// has no `e` to render and the error propagates. `TestFilterHost` with
    /// `pformat` unscripted is exactly that: `refused("pformat")`, an
    /// `ErrorKind::Host`, not a `FilterHost`.
    #[test]
    fn a_refusal_that_is_not_a_python_exception_propagates() {
        let h = TestFilterHost::default();
        let error = run(Value::Int(5), None, &call(&h)).expect_err("must propagate");
        assert!(
            matches!(error.kind, ErrorKind::Host(_)),
            "a refused host has no exception to render: {:?}",
            error.kind
        );
    }
}
