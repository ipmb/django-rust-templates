//! `timeuntil` — `defaultfilters.py:824-832`.
//!
//! ```python
//! @register.filter("timeuntil", is_safe=False)
//! def timeuntil_filter(value, arg=None):
//!     """Format a date as the time until that date (i.e. "4 days, 6 hours")."""
//!     if not value:
//!         return ""
//!     try:
//!         return timeuntil(value, arg)
//!     except (ValueError, TypeError):
//!         return ""
//! ```
//!
//! `timeuntil(value, arg)` is `timesince(value, arg, reversed=True)`
//! (`timesince.py:138-143`), a one-line delegation to the same host helper
//! ([`FilterHost::timesince`]) with the flag set. **The flag is a flag and not
//! an operand swap**: Django swaps `d` and `now` *after* normalising the
//! timezones (`timesince.py:60-70`), so a caller-side swap normalises the other
//! way and reports different calendar months for two aware datetimes in
//! different zones — a difference the corpus's `timeuntil B o=A` /
//! `timesince B o=A` pair reads out loud (`'1\xa0hour, 21\xa0minutes'` against
//! `'0\xa0minutes'`).
//!
//! Two differences from `timesince_filter`, both deliberate in the source:
//!
//! * **`arg` is passed unconditionally** — no `if arg` guard. The two spellings
//!   are behaviourally identical for every falsy argument (both end at
//!   `now()` inside the helper) and the source is the tiebreak: `Some(arg)` or
//!   `None` crosses as the helper's own `now`, with no truthiness question. So
//!   this body reads the argument's truthiness **zero** times where `timesince`
//!   reads it once.
//! * the caught pair is `(ValueError, TypeError)`, as in `timesince`, and
//!   `AttributeError` escapes again — `{{ "x"|timeuntil }}` is
//!   `'str' object has no attribute 'year'` out of the filter.
//!
//! **`if not value` is truthiness**, the same as `timesince`.
//!
//! Measured rows, `A = datetime(2026, 10, 8, 22, 30)`, `B = datetime(2026, 10,
//! 8, 23, 51)`, through `FilterExpression.resolve`:
//!
//! ```text
//! x|timeuntil:o  with (A, o=B)                -> '0\xa0minutes'
//! x|timeuntil:o  with (B, o=A)                -> '1\xa0hour, 21\xa0minutes'
//! x|timeuntil:o  with (date(2026,10,8), o=A)  -> '0\xa0minutes'
//! x|timeuntil:o  with (A_aware, o=A)          -> ''   the caught TypeError
//! x|timeuntil    with None / 0 / "" / []      -> ''
//! x|timeuntil    with "x"                     -> AttributeError: 'str' object has no attribute 'year'
//! ```
//!
//! The first two rows are the swap read out loud: `timeuntil(A, o=B)` is
//! `timesince(B, o=A)`. `timeuntil` has no `now()`-free deterministic
//! `'0\xa0minutes'` row of its own terms — the "in the future, stop processing"
//! arm is on the *reversed* side of the swap — so every row above carries a
//! fixed `o`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except (ValueError, TypeError):`, the same pair as `timesince`'s.
fn is_value_or_type_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if host.kind == PyErrorKind::ValueError || host.kind == PyErrorKind::TypeError
    )
}

/// `timeuntil`'s body: the truthiness guard → the helper with `reversed=true`
/// and the argument passed through **unconditionally** → the caught pair as
/// `""`, everything else escaping.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if !call.host.is_true(&value)? {
        return Ok(Value::Str("".into()));
    }
    match call.host.timesince(&value, arg.as_ref(), true) {
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

    /// The helper is the host's, whole, and `reversed` reaches it as a flag.
    ///
    /// **What a bare-shell host can and cannot see here is worth stating.**
    /// `TestFilterHost` records the *operation names* it was asked for, not the
    /// operands, so this test proves the body makes one `timesince` call — an
    /// operand-swapping body makes one too, and is **not** distinguishable from
    /// this side of the seam. The flag-versus-swap choice is pinned where the
    /// operands are observable: the corpus's `timeuntil B o=A` row
    /// (`'1\xa0hour, 21\xa0minutes'`) against `timesince B o=A`'s
    /// `'0\xa0minutes'`, which no swap reproduces.
    #[test]
    fn it_returns_the_hosts_answer_from_one_call() {
        let h = answering("1\u{a0}hour, 21\u{a0}minutes");
        assert_eq!(
            run(Value::Opaque(0), Some(Value::Opaque(1)), &call(&h)).unwrap(),
            Value::Str("1\u{a0}hour, 21\u{a0}minutes".into())
        );
        assert_eq!(h.calls(), vec!["is_true", "timesince"]);
    }

    /// **The `arg` guard is absent**, which is the one structural difference
    /// from `timesince`: with an argument present this body still reads the
    /// value's truthiness only — one `is_true`, not two — because `arg` is
    /// passed straight through.
    #[test]
    fn the_argument_guard_is_absent() {
        let h = answering("x");
        run(Value::Opaque(0), Some(Value::Opaque(1)), &call(&h)).unwrap();
        assert_eq!(
            h.calls(),
            vec!["is_true", "timesince"],
            "`timeuntil` must not read the argument's truthiness"
        );
    }

    /// A missing argument passes as `None`, the helper's own default.
    #[test]
    fn a_missing_argument_crosses_as_none() {
        let h = answering("0\u{a0}minutes");
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("0\u{a0}minutes".into())
        );
        assert_eq!(h.calls(), vec!["is_true", "timesince"]);
    }

    /// `if not value` is truthiness: a falsy value answers `''` at the guard.
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
            assert_eq!(h.calls(), vec!["is_true"]);
        }
    }

    /// **`(ValueError, TypeError)` is caught.**
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

    /// **`AttributeError` escapes**, the row `{{ "x"|timeuntil }}` is measured
    /// on.
    #[test]
    fn an_attribute_error_escapes() {
        let h = answering("unreachable");
        h.raise_on("timesince", PyErrorKind::AttributeError);
        let error = run(Value::Str("x".into()), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::AttributeError),
            "{error:?}"
        );
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
