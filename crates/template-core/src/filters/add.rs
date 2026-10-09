//! `add` — `defaultfilters.py:745-754`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def add(value, arg):
//!     """Add the arg to the value."""
//!     try:
//!         return int(value) + int(arg)
//!     except (ValueError, TypeError):
//!         try:
//!             return value + arg
//!         except Exception:
//!             return ""
//! ```
//!
//! **The whole body is one seam call.** [`FilterHost::add_values`] encodes all
//! three arms — `int(value) + int(arg)`, then `value + arg`, then a bare
//! `except Exception` → `""` — and the reason it must is that the second arm is
//! Python's `__add__`, which no [`Value`] variant can answer on Rust's side of
//! the seam (a list, a tuple and an `Opaque` all reach it). So there is no
//! integer arithmetic here and no `IntValue -> Value` conversion: the body
//! passes both operands through and returns the host's answer.
//!
//! Measured rows on the pinned tree, one or more per arm:
//!
//! ```text
//! (1, 2) -> 3      (1, "2") -> 3      (True, 1) -> 2     arm 1: int + int
//! (1.5, 1) -> 2    (2.5, 2.5) -> 4    ("2", "3") -> 5
//! ("a", "b") -> "ab"                                    arm 2: value + arg
//! (1, "2.5") -> "" ("a", 1) -> ""     ([1], 2) -> ""     arm 3: bare except
//! (None, 1) -> ""
//! ```
//!
//! **A missing argument never reaches this body.** `{{ x|add }}` is Django's
//! `add requires 2 arguments, 1 provided` — a `TemplateSyntaxError` from the
//! parser, measured — so the `None` this body substitutes for a missing
//! argument is only reachable through the explicit `{{ x|add:None }}`, which
//! the `int(None)` arm turns into `""`. Substituting `Value::None` (rather than
//! returning early) keeps that path on the seam where Django's is.
//!
//! **The result is never `SafeData`.** The registry flag is `is_safe=False`, so
//! the loop's `is_safe and isinstance(obj, SafeData)` test is false however the
//! operands were marked; this body returns the host's answer unchanged and does
//! not call `mark_safe`.
//!
//! The only thing this body can get wrong is *not delegating*: a body that
//! computed `int(value) + int(arg)` in Rust would answer arm 1's rows and fail
//! every string and refusal row, which is what the tests pin.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `add`'s body.
///
/// Both operands go to [`FilterHost::add_values`] **unconverted** — the int
/// arm's `int()` is inside the host's own `try`, alongside the `value + arg`
/// retry — and the host's answer is returned as-is. `arg.unwrap_or(Value::None)`
/// is the established shape for a missing argument (`center.rs:86`): Django's
/// body sees `None` for `{{ x|add:None }}`, and this makes the substituted case
/// reach the same seam.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let arg = arg.unwrap_or(Value::None);
    call.host.add_values(&value, &arg)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::NoFilterHost;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::{ErrorKind, PyErrorKind};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(answer: Value) -> TestFilterHost {
        TestFilterHost {
            add_values: Some(answer),
            ..TestFilterHost::default()
        }
    }

    /// The measured rows of the three arms. `add_values` is scripted with the
    /// answer Django gives, so the row pins that the body delegates **and** that
    /// it returns the host's answer verbatim: a body that computed
    /// `int(value) + int(arg)` itself would answer arm 1's rows and fail every
    /// string and refusal row below.
    #[test]
    fn the_measured_rows_come_back_from_the_host() {
        let rows = [
            (Value::Int(1), Value::Int(2), Value::Int(3)),
            (Value::Float(1.5), Value::Int(1), Value::Int(2)),
            (Value::Str("1".into()), Value::Int(2), Value::Int(3)),
            (
                Value::Str("1".into()),
                Value::Str("2".into()),
                Value::Int(3),
            ),
            (Value::Bool(true), Value::Int(1), Value::Int(2)),
            (
                Value::Int(1),
                Value::Str("2.5".into()),
                Value::Str("".into()),
            ),
            (
                Value::Str("a".into()),
                Value::Str("b".into()),
                Value::Str("ab".into()),
            ),
            (Value::Str("a".into()), Value::Int(1), Value::Str("".into())),
            (
                Value::List(vec![Value::Int(1)]),
                Value::Int(2),
                Value::Str("".into()),
            ),
            (Value::None, Value::Int(1), Value::Str("".into())),
            (Value::Float(2.5), Value::Float(2.5), Value::Int(4)),
            (
                Value::Str("2".into()),
                Value::Str("3".into()),
                Value::Int(5),
            ),
        ];
        for (value, arg, answer) in rows {
            let h = host(answer.clone());
            assert_eq!(
                run(value.clone(), Some(arg.clone()), &call(&h)).unwrap(),
                answer,
                "add({value:?}, {arg:?})"
            );
            h.assert_called("add_values");
        }
    }

    /// A sentinel no `add` answer can be. `int(1) + int(2)` is `3`, so a body
    /// that computed even that first arm in Rust answers `3` here where the host
    /// answered `"sentinel"` — the value assertion fails, and it fails *because*
    /// the body never reached [`FilterHost::add_values`], which the
    /// `assert_called` reports.
    #[test]
    fn the_hosts_answer_is_returned_verbatim() {
        let h = host(Value::Str("sentinel".into()));
        assert_eq!(
            run(Value::Int(1), Some(Value::Int(2)), &call(&h)).unwrap(),
            Value::Str("sentinel".into())
        );
        h.assert_called("add_values");
    }

    /// `{{ x|add }}` is the parser's `TemplateSyntaxError`, but an explicit
    /// `{{ x|add:None }}` and the `None` this body substitutes for a missing
    /// argument both reach `int(None)`, whose arm answers `""`. The body must
    /// still reach the seam: a body that early-returned on a missing argument
    /// would answer where Django's arm does.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = host(Value::Str("".into()));
        assert_eq!(
            run(Value::Int(1), None, &call(&h)).unwrap(),
            Value::Str("".into()),
            "add(1, None) is int(None) -> value + None -> \"\""
        );
        h.assert_called("add_values");
    }

    /// The body has no `try` of its own — the bare `except Exception` lives
    /// inside `add_values` — so a host failure escapes. A body that caught it
    /// and answered `""` would pass every row above, because `""` is reachable.
    #[test]
    fn a_host_failure_escapes() {
        let h = host(Value::Int(3));
        h.raise_on("add_values", PyErrorKind::ValueError);
        let error = run(Value::Int(1), Some(Value::Int(2)), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError),
            "the host's failure must not be swallowed: {error:?}"
        );
        h.assert_called("add_values");
    }

    /// `is_safe=False`, so the loop never marks the result — and the body must
    /// not invent a marker either, which would be a `SafeString` the flag
    /// denies. The scripted `mark_safe` would also change the returned value, so
    /// a body that called it fails both halves.
    #[test]
    fn the_body_never_marks_safe() {
        let h = TestFilterHost {
            add_values: Some(Value::Int(3)),
            mark_safe: Some(Value::Safe("marked".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(1), Some(Value::Int(2)), &call(&h)).unwrap(),
            Value::Int(3)
        );
        assert!(!h.calls().contains(&"mark_safe"), "{:?}", h.calls());
    }

    /// The body genuinely needs the seam: with no host it cannot answer, so a
    /// body that computed the sum itself cannot pass a bare-shell test by luck.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        assert!(run(Value::Int(1), Some(Value::Int(2)), &call).is_err());
    }
}
