//! `date` — `defaultfilters.py:783-794`.
//!
//! **The two branches call two different functions**, which is why the seam
//! carries two methods. Branch 1 is `formats.date_format(value, arg)`, whose body
//! is `dateformat.format(value, get_format(format or "DATE_FORMAT"))`
//! (`formats.py:147-157`); [`FilterHost::date_format`] mirrors the `get_format`
//! resolution with its `Option<&str>`. Branch 2 is `format(value, arg)` —
//! `django.utils.dateformat.format` imported at `defaultfilters.py:14`, the same
//! formatter **without `get_format`**, so the argument's text is used literally.
//! That is [`FilterHost::dateformat_format`], and its `&Value` is the wrapped
//! signature: `dateformat.format` `str()`s any object (`dateformat.py:42`), so an
//! absent argument is the text `"None"` — four live format characters — and a body
//! holding a `&str` would have to compute `str(None)` itself.
//!
//! **The two differ exactly when `str(arg)` is falsy or names a format setting,
//! *and* branch 1 raised.** A body that spelled branch 2 as
//! `date_format(value, Some(text))` would resolve `get_format("DATE_FORMAT")` to
//! `'N j, Y'` and answer `''` where the source answers the literal. The corpus's
//! `date/branch-two-absent-argument` row is that class, and is the only row pinning
//! branch 2 receiving `None` rather than the resolved default.
//!
//! **`expects_localtime=True` is not this body's.** `run_filter` applies
//! `localtime(value, use_tz)` before the body (the flag and `FilterCall::use_tz`),
//! so calling it here would be two calls and two answers.
//!
//! **The one narrowing is the guard, and it is `==` and not truthiness.**
//! `value in (None, "")` is Python equality against two sentinels, so Rust
//! expresses exactly those two shapes. It is **not** `bool(value)`: measured, `0`,
//! `False`, `[]`, `{}`, `()` and `0.0` are all falsy and all proceed past the guard,
//! diverging from a truthiness test as soon as the argument is non-empty
//! (`x|date:5` over one of them is `'5'`).
//!
//! The narrowing is exact unless a value's `__eq__` answers `True` for `None` or
//! `""` *and* it carries the date-format protocol. No fixture in
//! `tests/filter_cases.py` is that object today (`DOT_ATTR`, the class defining
//! `__eq__`, scopes it to `isinstance`), so **gaining an object fixture that
//! defines `__eq__` is the condition that would reopen this guard**.
//!
//! Measured rows through `FilterExpression.resolve` on 6.0.9, `en-us` unless said
//! otherwise, `d = date(2026, 10, 8)`:
//!
//! ```text
//! x|date / :"DATE_FORMAT" / :"" / :0   -> 'Oct. 8, 2026'
//! x|date:5                             -> '5'   a bare 5 is a var_arg; get_format str()s it
//! x|date:"x"                           -> 'x'
//! x|date:"Y-m-d" / "jS"                -> '2026-10-08' / '8th'
//! x|date:"\Y"                         -> 'Y'   the backslash escapes the format char
//! x|date:"H" / "TIME_FORMAT" / True    -> TypeError, found 'H' / 'P' / 'T'  (escapes)
//! x|date with "x"/5/None/""/mark_safe("")/0/False/[]/{}  -> ''
//! x|date:"Y"  with datetime(2026, 10, 8, 14, 30, 45)      -> '2026'
//! x|date:"Y-m-d" with mark_safe("2026-10-08")            -> ''   a SafeString has no .year
//! ```
//!
//! `date` catches **only `AttributeError`**, so the three `TypeError` rows
//! propagate where `time`'s identical `'H'` row answers `''`. The `x|date:5` `'5'`
//! is `get_format`'s `str(format_type)` (`formats.py:113`).
//!
//! `is_safe=False` and no `@stringfilter`, so nothing coerces the value and
//! nothing marks the result: every return is a plain `str`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `value in (None, "")` — Python equality against the two sentinels, **not**
/// `bool(value)`; see the module docs for the divergence and the reopen trigger.
fn is_none_or_empty(value: &Value) -> bool {
    matches!(value, Value::None) || matches!(value, Value::Str(s) | Value::Safe(s) if s.is_empty())
}

/// `except AttributeError:` — the only class either branch names.
fn is_attribute_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host) if host.kind == PyErrorKind::AttributeError
    )
}

/// Branch 1's format argument: `get_format(arg or "DATE_FORMAT")` reads the
/// argument's **truthiness**, not its presence, so `date:0` and `date:""` select
/// the default key and `date:5` does not. `is_true` precedes `to_str`.
fn resolved_format(arg: Option<&Value>, call: &FilterCall<'_>) -> Result<Option<String>, Error> {
    match arg {
        Some(arg) if call.host.is_true(arg)? => Ok(Some(call.host.to_str(arg)?)),
        _ => Ok(None),
    }
}

/// `date`'s body: guard → branch 1 with the resolved format → on
/// `AttributeError` branch 2 with the argument's **literal** text → on
/// `AttributeError` `""`. Any other class escapes, so the `TypeError` rows
/// propagate.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if is_none_or_empty(&value) {
        return Ok(Value::Str("".into()));
    }
    let format = resolved_format(arg.as_ref(), call)?;
    match call.host.date_format(&value, format.as_deref()) {
        Ok(text) => Ok(Value::Str(text.into())),
        Err(error) if is_attribute_error(&error) => {
            // `format(value, arg)` — the **raw** formatter, fed the original `arg`
            // rather than the resolved one; an absent argument is Python's `None`,
            // whose text is `"None"`.
            let literal = arg.unwrap_or(Value::None);
            match call.host.dateformat_format(&value, &literal) {
                Ok(text) => Ok(Value::Str(text.into())),
                Err(error) if is_attribute_error(&error) => Ok(Value::Str("".into())),
                Err(error) => Err(error),
            }
        }
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

    /// A host that answers branch 1 with `text`, plus the argument plumbing the
    /// truthy-argument tests need.
    fn branch_one(text: &str) -> TestFilterHost {
        TestFilterHost {
            date_format: Some(text.into()),
            is_true: Some(true),
            to_str: Some("Y".into()),
            ..TestFilterHost::default()
        }
    }

    /// The ordinary path: the resolved format reaches `date_format`, and the
    /// host's answer comes back as a plain `str`.
    #[test]
    fn it_delegates_branch_one_with_the_resolved_format() {
        let h = branch_one("2026-10-08");
        assert_eq!(
            run(
                Value::Opaque(0),
                Some(Value::Str("Y-m-d".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("2026-10-08".into())
        );
        assert_eq!(h.calls(), vec!["is_true", "to_str", "date_format"]);
    }

    /// A missing argument is Django's `arg=None`: no truthiness question, no
    /// `str()` of a text, and `None` crosses to the callee's own default.
    #[test]
    fn a_missing_argument_skips_the_format_lookup() {
        let h = branch_one("Oct. 8, 2026");
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("Oct. 8, 2026".into())
        );
        assert_eq!(h.calls(), vec!["date_format"]);
    }

    /// **The guard is equality and not truthiness.** The two sentinels answer
    /// `""` **without touching the host**; every other falsy value proceeds.
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
        for falsy in [
            Value::Int(0),
            Value::Bool(false),
            Value::Float(0.0),
            Value::List(vec![]),
        ] {
            let h = branch_one("5");
            assert_eq!(
                run(falsy, Some(Value::Int(5)), &call(&h)).unwrap(),
                Value::Str("5".into()),
                "a falsy value that is not a sentinel must proceed"
            );
            h.assert_called("date_format");
        }
    }

    /// The argument's **truthiness** is read before its text: `date:0` and
    /// `date:""` select the default (no `to_str`), and a truthy argument is `str()`d.
    #[test]
    fn the_argument_truthiness_is_read_before_its_text() {
        let h = TestFilterHost {
            is_true: Some(false),
            date_format: Some("default".into()),
            to_str: Some("5".into()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Opaque(0), Some(Value::Int(0)), &call(&h)).unwrap(),
            Value::Str("default".into())
        );
        assert_eq!(
            h.calls(),
            vec!["is_true", "date_format"],
            "a falsy argument must not be str()d"
        );

        let h = TestFilterHost {
            is_true: Some(true),
            date_format: Some("5".into()),
            to_str: Some("5".into()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Opaque(0), Some(Value::Int(5)), &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        assert_eq!(h.calls(), vec!["is_true", "to_str", "date_format"]);
    }

    /// **The second branch calls the raw formatter, not `date_format`.** The
    /// corpus's `date/branch-two-absent-argument` row separates them; this pins
    /// that the body reaches the *other* seam method.
    #[test]
    fn the_fallback_is_the_raw_formatter_not_date_format() {
        let h = TestFilterHost {
            dateformat_format: Some("Oct.202610".into()),
            ..TestFilterHost::default()
        };
        h.raise_on("date_format", PyErrorKind::AttributeError);
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("Oct.202610".into())
        );
        assert_eq!(
            h.calls(),
            vec!["date_format", "dateformat_format"],
            "date_format once, then the raw formatter — not date_format again"
        );
    }

    /// **Only `AttributeError` reaches the fallback.** The corpus's `date:"H"` row
    /// raises `TypeError` out of the filter.
    #[test]
    fn only_attribute_error_reaches_the_fallback() {
        for escaping in [PyErrorKind::TypeError, PyErrorKind::ValueError] {
            let h = TestFilterHost {
                dateformat_format: Some("unreachable".into()),
                is_true: Some(true),
                to_str: Some("H".into()),
                ..TestFilterHost::default()
            };
            h.raise_on("date_format", escaping.clone());
            let error = run(Value::Opaque(0), Some(Value::Str("H".into())), &call(&h)).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == escaping),
                "expected {escaping:?} to escape: {error:?}"
            );
            assert_eq!(
                h.calls(),
                vec!["is_true", "to_str", "date_format"],
                "no fallback on this class"
            );
        }
    }

    /// A second `AttributeError` — branch 2 also failing — is the `""` return.
    #[test]
    fn a_second_attribute_error_is_the_empty_string() {
        let h = TestFilterHost::default();
        h.raise_on("date_format", PyErrorKind::AttributeError);
        h.raise_on("dateformat_format", PyErrorKind::AttributeError);
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        h.assert_called("dateformat_format");
    }

    /// The body needs the seam, so a bare-shell host refuses rather than letting a
    /// Rust reimplementation pass by luck.
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
