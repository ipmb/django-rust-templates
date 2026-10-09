//! `stringformat` — `defaultfilters.py:277-294`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def stringformat(value, arg):
//!     if isinstance(value, tuple):
//!         value = str(value)
//!     try:
//!         return ("%" + str(arg)) % value
//!     except (ValueError, TypeError):
//!         return ""
//! ```
//!
//! **The tuple test is the point of the body, and it is load-bearing rather
//! than defensive.** A tuple is the one operand `%` *unpacks*, so
//! `"%s" % (1, 2)` alone raises `TypeError: not all arguments converted during
//! string formatting` (measured), which the `try` would catch and answer `""`.
//! Stringifying it first gives the measured rows `((1, 2), "s") -> "(1, 2)"`,
//! `((), "s") -> "()"`, `((1,), "s") -> "(1,)"` and
//! `((1, 2), "10s") -> "    (1, 2)"`. [`FilterHost::is_tuple`] is the only way
//! Rust can ask, because a tuple crosses the seam as `Value::Opaque`.
//!
//! **A `list` is the opposite case and needs no special case at all.** `%`
//! treats every non-tuple as a single operand, so `([1], "s") -> "[1]"` and
//! `([1, None], "s") -> "[1, None]"` (measured) — a body written to
//! `matches!(value, Value::List(_))` would be exactly backwards. This body never
//! tests for a list.
//!
//! **`"%\" + str(arg)` is Rust's; the `%` operator is Python's.** The format
//! string is built as `format!("%{}", str(arg))` — the source's own literal
//! `"%" + str(arg)` — and handed whole to [`FilterHost::percent_format`], so the
//! spec never reaches a Rust `format!`. That closes Task 5's width cap on the
//! precision axis: measured, `{{ x|stringformat:"70000d" }}` is a legal template
//! Django answers with 70000 characters, where Rust's `Formatter` caps precision
//! at 65535.
//!
//! **`str(arg)` is inside the `try`, but `str(value)` is outside it.** So a
//! refusal from `to_str(arg)` is caught (`""`) while one from the tuple's
//! `to_str(value)` escapes. A body that built the format string outside the
//! `try` would let the first escape where Django answers `""`.
//!
//! **The result is a plain `str`, and the body must not call `mark_safe`.**
//! `is_safe=True` is the registry's flag, and the loop that applies it lives in
//! `template-py` (`crates/template-py/src/filters.rs`); its
//! `is_safe and isinstance(obj, SafeData)` test reads the **pre-filter** value,
//! which is why measured `stringformat("AbC", "10s")` is *not* safe even though
//! the flag is set. Django's own `test_stringformat.py` sees a `SafeString` only
//! because its *input* was `mark_safe("a<b")`. This body returns `Value::Str`
//! and leaves the marking to the loop.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except (ValueError, TypeError): return ""` — the two classes the `%` and
/// `str(arg)` may raise and the filter catches.
fn is_caught(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if matches!(
                host.kind,
                PyErrorKind::ValueError | PyErrorKind::TypeError
            )
    )
}

/// `stringformat`'s body.
///
/// `is_tuple` first (its `str(value)` outside the `try`, via `?`), then
/// `str(arg)` and the `%` inside the `try` — both caught as `""` — and the
/// result returned as a plain `Value::Str`/host answer. `arg.unwrap_or(None)`
/// is the established shape for a missing argument (`crates/template-core/src/filters/center.rs:86`).
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `if isinstance(value, tuple): value = str(value)` — outside the `try`, so
    // a refusal here escapes rather than becoming `""`.
    let value = if call.host.is_tuple(&value) {
        Value::Str(call.host.to_str(&value)?.into())
    } else {
        value
    };

    // `str(arg)` is inside the `try`.
    let spec = match call.host.to_str(&arg.unwrap_or(Value::None)) {
        Ok(text) => text,
        Err(error) if is_caught(&error) => return Ok(Value::Str("".into())),
        Err(error) => return Err(error),
    };

    // `("%" + str(arg)) % value` — the format string is Rust's, the operator is
    // Python's.
    match call.host.percent_format(&format!("%{spec}"), &value) {
        Ok(result) => Ok(result),
        Err(error) if is_caught(&error) => Ok(Value::Str("".into())),
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
            use_tz: true,
        }
    }

    fn host(is_tuple: bool, text: &str, formatted: Value) -> TestFilterHost {
        TestFilterHost {
            is_tuple: Some(is_tuple),
            to_str: Some(text.to_string()),
            percent_format: Some(formatted),
            ..TestFilterHost::default()
        }
    }

    fn to_str_calls(h: &TestFilterHost) -> usize {
        h.calls().iter().filter(|name| **name == "to_str").count()
    }

    /// The float of the measured `stringformat(1.23456, ".2f") -> "1.23"` row.
    ///
    /// A `const` rather than a literal in the table, and the value is chosen so
    /// the row needs **no** suppression. An earlier revision used `3.14159` and
    /// carried a scoped `#[allow(clippy::approx_constant)]` with the note that
    /// the constant was Django's measured input rather than an approximation of
    /// π — true, but a *reason* rather than a measurement: the row exists to show
    /// a float carrying a precision spec, which any float does, so the lint is
    /// avoided rather than silenced. Re-measured: `1.23456` is `"1.23"`.
    const MEASURED_PRECISION_FLOAT: f64 = 1.23456;

    /// The non-tuple measured rows. `percent_format` is scripted with Django's
    /// answer, so the row pins that the body delegates the `%` and returns its
    /// answer verbatim. `TestFilterHost` records the operation **name** and not
    /// the format string (see `slice.rs`), so the `"%" + str(arg)` half cannot
    /// be pinned here — the real host consumes it in the parity corpus. What
    /// *is* pinned is that a non-tuple value is **not** stringified: exactly one
    /// `to_str` call, the argument's.
    #[test]
    fn the_measured_non_tuple_rows() {
        let rows = [
            (Value::Str("AbC".into()), "10s", "       AbC"),
            (Value::Str("AbC".into()), "s", "AbC"),
            (Value::Int(42), "d", "42"),
            (Value::Int(42), "5d", "   42"),
            (Value::Float(MEASURED_PRECISION_FLOAT), ".2f", "1.23"),
            (Value::Int(42), "x", "2a"),
            (Value::None, "s", "None"),
            (Value::Str("abc".into()), "d", ""),
            (Value::Int(42), "z", ""),
            // A list is a single `%` operand, so no tuple test applies.
            (Value::List(vec![Value::Int(1)]), "s", "[1]"),
            (
                Value::List(vec![Value::Int(1), Value::None]),
                "s",
                "[1, None]",
            ),
            (Value::Int(42), "%", ""),
            (Value::Int(42), "%d", ""),
            (Value::Int(65), "c", "A"),
            (Value::Int(42), "o", "52"),
            (Value::Int(42), "05.1f", "042.0"),
            (Value::Str("a".into()), ".2f", ""),
        ];
        for (value, spec, answer) in rows {
            let h = host(false, spec, Value::Str(answer.into()));
            assert_eq!(
                run(value.clone(), Some(Value::Str(spec.into())), &call(&h)).unwrap(),
                Value::Str(answer.into()),
                "stringformat({value:?}, {spec:?})"
            );
            h.assert_called("is_tuple");
            h.assert_called("to_str");
            h.assert_called("percent_format");
            assert_eq!(
                to_str_calls(&h),
                1,
                "a non-tuple value is never stringified; only `str(arg)` is called: {:?}",
                h.calls()
            );
        }
    }

    /// The tuple rows — the whole reason [`FilterHost::is_tuple`] exists. A real
    /// tuple crosses the seam as a `Value::Opaque` (the host interns one
    /// deliberately), which is why the predicate is the only way to ask; the
    /// variant here is irrelevant because the host scripts both answers.
    ///
    /// A body that omitted the `is_tuple` call fails on `assert_called` **and**
    /// on the `to_str` count (one, not two); a body that tested for a list
    /// instead is caught by the non-tuple rows above.
    #[test]
    fn a_tuple_is_stringified_before_the_percent_operator() {
        let rows = [
            (Value::Opaque(0), "s", "(1, 2)"),
            (Value::Opaque(0), "s", "()"),
            (Value::Opaque(0), "s", "(1,)"),
            (Value::Opaque(0), "d", ""),
            (Value::Opaque(0), "10s", "    (1, 2)"),
        ];
        for (value, spec, answer) in rows {
            let h = host(true, "(1, 2)", Value::Str(answer.into()));
            assert_eq!(
                run(value.clone(), Some(Value::Str(spec.into())), &call(&h)).unwrap(),
                Value::Str(answer.into()),
                "stringformat(tuple, {spec:?})"
            );
            h.assert_called("is_tuple");
            h.assert_called("percent_format");
            assert_eq!(
                to_str_calls(&h),
                2,
                "a tuple is stringified (`str(value)`) and so is the argument: {:?}",
                h.calls()
            );
        }
    }

    /// `is_safe=True` is the registry's, and the loop marks the result there —
    /// but measured `stringformat("AbC", "10s")` is **not** safe, because the
    /// loop's test is on the pre-filter value. So the body returns a plain
    /// `Value::Str` and never calls `mark_safe`; a body that did would return
    /// the scripted `SafeString` and fail the value assertion as well as the
    /// call assertion.
    #[test]
    fn the_body_returns_a_plain_str_and_never_marks_safe() {
        let h = TestFilterHost {
            is_tuple: Some(false),
            to_str: Some("10s".to_string()),
            percent_format: Some(Value::Str("       AbC".into())),
            mark_safe: Some(Value::Safe("marked".into())),
            ..TestFilterHost::default()
        };
        let result = run(
            Value::Str("AbC".into()),
            Some(Value::Str("10s".into())),
            &call(&h),
        )
        .unwrap();
        assert_eq!(result, Value::Str("       AbC".into()));
        assert_eq!(result.type_name(), "str", "the loop marks, not the body");
        assert!(!h.calls().contains(&"mark_safe"), "{:?}", h.calls());
    }

    /// The `%`'s refusals: `ValueError` and `TypeError` answer `""` (measured
    /// `stringformat("abc", "d") -> ""`, `(42, "z") -> ""`), anything else
    /// escapes. A body that caught nothing would answer where Django's `try`
    /// does.
    #[test]
    fn the_percent_operator_refusals_are_caught() {
        for kind in [PyErrorKind::ValueError, PyErrorKind::TypeError] {
            let h = host(false, "d", Value::Str("unused".into()));
            h.raise_on("percent_format", kind.clone());
            assert_eq!(
                run(
                    Value::Str("abc".into()),
                    Some(Value::Str("d".into())),
                    &call(&h)
                )
                .unwrap(),
                Value::Str("".into()),
                "a {kind:?} from the % is the `except` arm"
            );
            h.assert_called("percent_format");
        }

        let h = host(false, "d", Value::Str("unused".into()));
        h.raise_on("percent_format", PyErrorKind::IndexError);
        let error = run(
            Value::Str("abc".into()),
            Some(Value::Str("d".into())),
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::IndexError),
            "only ValueError and TypeError are caught: {error:?}"
        );
    }

    /// `str(arg)` is inside the `try`, so its refusal is caught **before** the
    /// `%` is ever reached. A body that built the format string outside the
    /// `try` would let it escape.
    #[test]
    fn a_str_of_the_argument_refusal_is_caught() {
        for kind in [PyErrorKind::ValueError, PyErrorKind::TypeError] {
            let h = host(false, "unused", Value::Str("unused".into()));
            h.raise_on("to_str", kind);
            assert_eq!(
                run(Value::Int(42), Some(Value::Int(1)), &call(&h)).unwrap(),
                Value::Str("".into())
            );
            h.assert_called("to_str");
            assert!(
                !h.calls().contains(&"percent_format"),
                "the % must not be reached once str(arg) refuses: {:?}",
                h.calls()
            );
        }
    }

    /// A `stringformat` with no argument is the parser's `TemplateSyntaxError`,
    /// but the `None` this body substitutes must still reach the seam:
    /// `str(None)` is `"None"`, so the format string becomes `"%None"`, whose
    /// refusal is the `%`'s `""`. The body must not early-return.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = host(false, "None", Value::Str("".into()));
        assert_eq!(
            run(Value::Str("x".into()), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        h.assert_called("to_str");
        h.assert_called("percent_format");
    }

    /// The body needs the seam, so it cannot be reimplemented in Rust and pass a
    /// bare-shell test by luck. Under [`NoFilterHost`] the refusal is a **panic**
    /// rather than an `Err`, because `is_tuple` is one of the 14 non-`Result`
    /// operations (`crates/template-core/src/filters/host.rs:519-526`), so this observes it the way
    /// `filters/host.rs`'s own tests observe those.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        let reached = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            run(Value::Int(42), Some(Value::Str("d".into())), &call)
        }));
        assert!(
            reached.is_err(),
            "the body must reach the host, whose refusal here is a panic"
        );
    }
}
