//! `time` — `defaultfilters.py:797-808`.
//!
//! ```python
//! @register.filter(expects_localtime=True, is_safe=False)
//! def time(value, arg=None):
//!     """Format a time according to the given format."""
//!     if value in (None, ""):
//!         return ""
//!     try:
//!         return formats.time_format(value, arg)
//!     except (AttributeError, TypeError):
//!         try:
//!             return time_format(value, arg)
//!         except (AttributeError, TypeError):
//!             return ""
//! ```
//!
//! Same skeleton as [`date`](crate::filters::date) with **three** differences,
//! and each is a measured row:
//!
//! 1. the resolved default is `TIME_FORMAT` (`formats.time_format`,
//!    `formats.py:160-169`) — `'P'` in `en-us`, so the bare `x|time` is
//!    `'2:30 p.m.'`;
//! 2. **both branches catch `TypeError` as well as `AttributeError`** — this is
//!    why `x|time:"H"` over a `date` answers `''` where `x|date:"H"` propagates;
//! 3. the fallback is `dateformat.time_format`, hence
//!    [`FilterHost::dateformat_time_format`], the raw twin of `date`'s branch 2.
//!
//! **The two branches call two different functions**, the same as `date`'s: the
//! resolved [`FilterHost::time_format`] and the literal
//! [`FilterHost::dateformat_time_format`]. The raw method's `&Value` is the
//! wrapped signature — `dateformat.time_format` `str()`s any object
//! (`dateformat.py:332-335`) — and it is a `Result` for a measured second
//! reason: `TimeFormat` has no `N`, so the absent argument raises
//! `AttributeError: 'TimeFormat' object has no attribute 'N'` where the `date`
//! twin answers `'Oct.202610'`.
//!
//! **`expects_localtime=True` is not this body's.** `run_filter` applies
//! `localtime(value, use_tz)` before the body, so the corpus's `time/localtime-*`
//! rows are the evidence the flag is carried, and a body that called it again
//! would answer twice.
//!
//! **The guard is `value in (None, "")` — equality against two sentinels, not
//! truthiness**, exactly as in `date` (see that module for the measured
//! divergence and the `__eq__` trigger that would reopen the narrowing).
//!
//! Measured rows through `FilterExpression.resolve` on the pinned 6.0.9:
//!
//! ```text
//! x|time  with time(14, 30, 45)          -> '2:30 p.m.'
//! x|time:"H:i"                           -> '14:30'
//! x|time:"H:i:s"                         -> '14:30:45'
//! x|time:"P"                             -> '2:30 p.m.'
//! x|time:"H:i" with datetime(2026,10,8,14,30,45) -> '14:30'
//! x|time:"H"  with date(2026, 10, 8)     -> ''      branch 1 TypeError caught, branch 2 same
//! x|time:"H:i" with date(2026, 10, 8)    -> ''
//! x|time      with date(2026, 10, 8)     -> ''
//! x|time      with "x" / None / ""       -> ''
//! x|time:5    with 0 / False / [] / {}   -> '5'     the equality guard, not truthiness
//! ```
//!
//! The `date` row is the sharp one: branch 1 raises the *same* `TypeError` that
//! `date`'s `'H'` row propagates, `time` catches it, branch 2 raises it again (a
//! `date` has no `.hour`), and the second catch answers `''`. A body that caught
//! only `AttributeError` raises here.
//!
//! `is_safe=False` and no `@stringfilter`, so every return is a plain `str`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `value in (None, "")` — Python equality against the two sentinels, not
/// `bool(value)`. `date.rs`'s twin with the same measured divergence.
fn is_none_or_empty(value: &Value) -> bool {
    matches!(value, Value::None) || matches!(value, Value::Str(s) | Value::Safe(s) if s.is_empty())
}

/// `except (AttributeError, TypeError):` — **both** classes, in both branches.
/// `date` names only the first, and that asymmetry is the pair the corpus pins.
fn is_attribute_or_type_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if host.kind == PyErrorKind::AttributeError || host.kind == PyErrorKind::TypeError
    )
}

/// Branch 1's format argument, `get_format(arg or "TIME_FORMAT")`: the
/// argument's truthiness first, then its text.
fn resolved_format(arg: Option<&Value>, call: &FilterCall<'_>) -> Result<Option<String>, Error> {
    match arg {
        Some(arg) if call.host.is_true(arg)? => Ok(Some(call.host.to_str(arg)?)),
        _ => Ok(None),
    }
}

/// `time`'s body: guard → branch 1 with the resolved format → on
/// `AttributeError`/`TypeError` branch 2 with the argument's literal text → on
/// either class again `""`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if is_none_or_empty(&value) {
        return Ok(Value::Str("".into()));
    }
    let format = resolved_format(arg.as_ref(), call)?;
    match call.host.time_format(&value, format.as_deref()) {
        Ok(text) => Ok(Value::Str(text.into())),
        Err(error) if is_attribute_or_type_error(&error) => {
            // `time_format(value, arg)` — the raw formatter, with the original
            // argument. An absent argument is Python's `None`, whose text is
            // `"None"` and whose first character has no `TimeFormat` method.
            let literal = arg.unwrap_or(Value::None);
            match call.host.dateformat_time_format(&value, &literal) {
                Ok(text) => Ok(Value::Str(text.into())),
                Err(error) if is_attribute_or_type_error(&error) => Ok(Value::Str("".into())),
                Err(error) => Err(error),
            }
        }
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

    fn branch_one(text: &str) -> TestFilterHost {
        TestFilterHost {
            time_format: Some(text.into()),
            is_true: Some(true),
            to_str: Some("H:i".into()),
            ..TestFilterHost::default()
        }
    }

    /// The ordinary path: `formats.time_format` with the resolved format, and a
    /// plain `str` back.
    #[test]
    fn it_delegates_branch_one_with_the_resolved_format() {
        let h = branch_one("14:30");
        assert_eq!(
            run(Value::Opaque(0), Some(Value::Str("H:i".into())), &call(&h)).unwrap(),
            Value::Str("14:30".into())
        );
        assert_eq!(h.calls(), vec!["is_true", "to_str", "time_format"]);
    }

    /// A missing argument skips the format lookup and leaves the callee's own
    /// `TIME_FORMAT` default in place.
    #[test]
    fn a_missing_argument_skips_the_format_lookup() {
        let h = branch_one("2:30 p.m.");
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("2:30 p.m.".into())
        );
        assert_eq!(h.calls(), vec!["time_format"]);
    }

    /// **The guard is equality and not truthiness** — the same decision as
    /// `date`'s, and the `x|time:5` over a falsy value is `'5'`.
    #[test]
    fn the_sentinel_guard_is_equality_not_truthiness() {
        for sentinel in [Value::None, Value::Str("".into()), Value::Safe("".into())] {
            let h = branch_one("unreachable");
            assert_eq!(
                run(sentinel, None, &call(&h)).unwrap(),
                Value::Str("".into())
            );
            assert!(h.calls().is_empty(), "the guard must not reach the host");
        }
        for falsy in [Value::Int(0), Value::Bool(false), Value::List(vec![])] {
            let h = branch_one("5");
            assert_eq!(
                run(falsy, Some(Value::Int(5)), &call(&h)).unwrap(),
                Value::Str("5".into())
            );
            h.assert_called("time_format");
        }
    }

    /// The argument's truthiness precedes its text.
    #[test]
    fn the_argument_truthiness_is_read_before_its_text() {
        let h = TestFilterHost {
            is_true: Some(false),
            time_format: Some("default".into()),
            to_str: Some("H".into()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Opaque(0), Some(Value::Str("".into())), &call(&h)).unwrap(),
            Value::Str("default".into())
        );
        assert_eq!(h.calls(), vec!["is_true", "time_format"]);
    }

    /// **`TypeError` reaches the fallback**, which is the whole difference from
    /// `date`: the corpus's `time:"H"` over a `date` row is branch 1's
    /// `TypeError`, caught, then branch 2's, caught again, answering `''`.
    #[test]
    fn a_type_error_reaches_the_raw_fallback() {
        let h = TestFilterHost {
            dateformat_time_format: Some("raw".into()),
            ..TestFilterHost::default()
        };
        h.raise_on("time_format", PyErrorKind::TypeError);
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("raw".into())
        );
        assert_eq!(h.calls(), vec!["time_format", "dateformat_time_format"]);
    }

    /// An `AttributeError` reaches the fallback too, as in `date`.
    #[test]
    fn an_attribute_error_reaches_the_raw_fallback() {
        let h = TestFilterHost {
            dateformat_time_format: Some("raw".into()),
            ..TestFilterHost::default()
        };
        h.raise_on("time_format", PyErrorKind::AttributeError);
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("raw".into())
        );
        h.assert_called("dateformat_time_format");
    }

    /// **Both branches catch `TypeError`**, so a second one is the `""` return —
    /// where `date`'s second `TypeError` would have escaped.
    #[test]
    fn a_second_type_error_is_the_empty_string() {
        let h = TestFilterHost::default();
        h.raise_on("time_format", PyErrorKind::TypeError);
        h.raise_on("dateformat_time_format", PyErrorKind::TypeError);
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        h.assert_called("dateformat_time_format");
    }

    /// A class neither branch names escapes, so the pair is not a catch-all.
    #[test]
    fn another_class_escapes() {
        let h = TestFilterHost {
            dateformat_time_format: Some("unreachable".into()),
            ..TestFilterHost::default()
        };
        h.raise_on("time_format", PyErrorKind::ValueError);
        let error = run(Value::Opaque(0), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError),
            "{error:?}"
        );
        assert_eq!(h.calls(), vec!["time_format"]);
    }

    /// The body needs the seam.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &crate::filters::host::NoFilterHost,
            autoescape: true,
            use_tz: false,
        };
        assert!(run(Value::Opaque(0), None, &call).is_err());
    }
}
