//! `timesince` — `defaultfilters.py:811-821`.
//!
//! ```python
//! @register.filter("timesince", is_safe=False)
//! def timesince_filter(value, arg=None):
//!     """Format a date as the time since that date (i.e. "4 days, 6 hours")."""
//!     if not value:
//!         return ""
//!     try:
//!         if arg:
//!             return timesince(value, arg)
//!         return timesince(value)
//!     except (ValueError, TypeError):
//!         return ""
//! ```
//!
//! The helper, `django.utils.timesince.timesince`, is the host's whole
//! ([`FilterHost::timesince`]): the calendar arithmetic, the
//! `TIMESINCE_CHUNKS` walk and the `\xa0` separator inside the unit strings all
//! live in Python. Nothing here builds a time string.
//!
//! **`if not value` is truthiness, not `date`'s two sentinels.** Measured:
//! `None`, `0`, `""` and `[]` all answer `''`; the guard is
//! `call.host.is_true(&value)?`.
//!
//! **`if arg` is truthiness too, and the seam still mirrors the source's two
//! calls with one.** A falsy argument falls through to `timesince(value)`, and
//! the helper then fills `now` from `d.tzinfo` itself (`timesince.py:67-68`) —
//! `timesince:0` and `timesince:""` are the same answer as no argument. `now` is
//! an `Option<&Value>` and **`None` is the right value**; passing
//! `timezone.now()` instead is a measured `TypeError` — it is aware where a naive
//! `d` is not, so it takes the `elif is_aware(now) and is_aware(d)` branch — and
//! it turns a real answer into `''` through this filter's own `except`.
//!
//! **It catches `(ValueError, TypeError)` and lets `AttributeError` escape**,
//! and the two classes leave the *same* filter in opposite directions:
//!
//! ```text
//! timesince(A, A_aware)   TypeError: can't subtract offset-naive and offset-aware datetimes  -> ''
//! timesince("x")          AttributeError: 'str' object has no attribute 'year'               -> escapes
//! ```
//!
//! So the corpus's mixed-awareness rows belong in `CASES` with the empty answer
//! and the `"x"` rows in `ERROR_CASES`, which compares the message as a literal
//! — the escaping message is CPython's, and a body that swallowed it would
//! answer `''` where Django raises.
//!
//! **The helper's `reversed` flag is `false` here**; `timeuntil` is the same
//! call with `true` (`timesince.py:138-143`), because a caller-side operand swap
//! normalises the timezones the other way and reports different months for two
//! aware datetimes in different zones.
//!
//! Measured rows, `A = datetime(2026, 10, 8, 22, 30)`, `B = datetime(2026, 10,
//! 8, 23, 51)`, both naive, through `FilterExpression.resolve`:
//!
//! ```text
//! x|timesince:o  with (A, o=B)                    -> '1\xa0hour, 21\xa0minutes'
//! x|timesince:o  with (B, o=A)                    -> '0\xa0minutes'
//! x|timesince:o  with (date(2026,10,8), o=A)      -> '22\xa0hours, 30\xa0minutes'
//! x|timesince:o  with (A_aware, o=A)              -> ''   the caught TypeError
//! x|timesince:o  with (A, o=A_aware)              -> ''   the same, other way round
//! x|timesince    with None / 0 / "" / []          -> ''
//! x|timesince    with now() + 3650 days           -> '0\xa0minutes'
//! x|timesince    with "x"                         -> AttributeError: 'str' object has no attribute 'year'
//! x|timesince:o  with ("x", o=A)                  -> the same
//! ```
//!
//! The separator is NBSP (`\xa0`) and comes from Python's own `ngettext`; a
//! hand-built `'1 hour'` fails the corpus by one byte. Every row above carries a
//! fixed `o` except the far-future one, because without one the helper calls
//! `datetime.now()` **inside each engine's call**, microseconds apart, and any
//! row without an explicit `o` is a race at a unit boundary.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except (ValueError, TypeError):` — the two classes the filter forgives.
/// `AttributeError` is **not** here, and letting it escape is the whole reason
/// [`FilterHost::timesince`] returns a `Result`.
fn is_value_or_type_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if host.kind == PyErrorKind::ValueError || host.kind == PyErrorKind::TypeError
    )
}

/// `timesince`'s body: the truthiness guard → the two-call shape the source
/// writes as one seam call → the caught pair as `""`, everything else escaping.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if !call.host.is_true(&value)? {
        return Ok(Value::Str("".into()));
    }
    // `if arg: timesince(value, arg) else: timesince(value)`. The seam's `now`
    // is `None` for the second spelling, which is what lets the helper fill it
    // from `d.tzinfo`.
    let now = match arg.as_ref() {
        Some(arg) if call.host.is_true(arg)? => Some(arg),
        _ => None,
    };
    match call.host.timesince(&value, now, false) {
        Ok(text) => Ok(Value::Str(text.into())),
        Err(error) if is_value_or_type_error(&error) => Ok(Value::Str("".into())),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::{NoFilterHost, TestFilterHost};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    fn answering(text: &str) -> TestFilterHost {
        TestFilterHost {
            timesince: Some(text.into()),
            is_true: Some(true),
            ..TestFilterHost::default()
        }
    }

    /// The helper is the host's, whole: its answer comes back unmodified.
    #[test]
    fn it_returns_the_hosts_answer() {
        let h = answering("1\u{a0}hour, 21\u{a0}minutes");
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("1\u{a0}hour, 21\u{a0}minutes".into())
        );
        h.assert_called("timesince");
    }

    /// **`if not value` is truthiness**: a falsy value answers `''` before the
    /// helper is called at all. A body that tested only `None`/`""` would reach
    /// the host here.
    #[test]
    fn a_falsy_value_is_the_empty_string() {
        for falsy in [
            Value::None,
            Value::Int(0),
            Value::Str("".into()),
            Value::List(vec![]),
        ] {
            let h = TestFilterHost {
                is_true: Some(false),
                timesince: Some("unreachable".into()),
                ..TestFilterHost::default()
            };
            assert_eq!(run(falsy, None, &call(&h)).unwrap(), Value::Str("".into()));
            assert_eq!(
                h.calls(),
                vec!["is_true"],
                "the guard is the only host call"
            );
        }
    }

    /// **The `arg` guard is present**: a truthy argument is tested (`is_true`)
    /// before the helper is called, so `timesince` with an argument asks the
    /// host **twice**. `timeuntil` — which passes `arg` unconditionally — asks
    /// once, and the test beside it in `timeuntil.rs` is that difference.
    ///
    /// A *falsy* argument's own branch is not observable here: `TestFilterHost`
    /// answers one scripted `is_true` for both questions, so it cannot make the
    /// value truthy and the argument falsy in one call, and `now` is not among
    /// the things it records. What the body does with the two answers is the
    /// source's own `if arg`, and the corpus is where the argument shapes are
    /// compared.
    #[test]
    fn the_argument_truthiness_guard_is_present() {
        let h = answering("x");
        run(Value::Opaque(0), Some(Value::Opaque(1)), &call(&h)).unwrap();
        assert_eq!(h.calls(), vec!["is_true", "is_true", "timesince"]);
    }

    /// **`(ValueError, TypeError)` is caught** — the mixed-awareness rows answer
    /// `''`.
    #[test]
    fn the_caught_pair_is_the_empty_string() {
        for caught in [PyErrorKind::ValueError, PyErrorKind::TypeError] {
            let h = answering("unreachable");
            h.raise_on("timesince", caught.clone());
            assert_eq!(
                run(Value::Opaque(0), None, &call(&h)).unwrap(),
                Value::Str("".into()),
                "expected {caught:?} to be caught"
            );
        }
    }

    /// **`AttributeError` escapes**, and its message is CPython's — the row
    /// `{{ "x"|timesince }}` is measured on. A body that caught it would answer
    /// `''` where Django raises `'str' object has no attribute 'year'`.
    #[test]
    fn an_attribute_error_escapes() {
        let h = answering("unreachable");
        h.raise_on("timesince", PyErrorKind::AttributeError);
        let error = run(Value::Str("x".into()), None, &call(&h)).unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host) => {
                assert_eq!(host.kind, PyErrorKind::AttributeError);
                assert_eq!(host.operation, "timesince");
            }
            other => panic!("expected an escaping AttributeError, got {other:?}"),
        }
    }

    /// The body needs the seam.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: false,
        };
        assert!(run(Value::Opaque(0), None, &call).is_err());
    }
}
