//! `filesizeformat` — `defaultfilters.py:899-939`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def filesizeformat(bytes_):
//!     """
//!     Format the value like a 'human-readable' file size (i.e. 13 KB, 4.1 MB,
//!     102 bytes, etc.).
//!     """
//!     try:
//!         bytes_ = int(bytes_)
//!     except (TypeError, ValueError, UnicodeDecodeError):
//!         value = ngettext("%(size)d byte", "%(size)d bytes", 0) % {"size": 0}
//!         return avoid_wrapping(value)
//!
//!     def filesize_number_format(value):
//!         return formats.number_format(round(value, 1), 1)
//!
//!     KB = 1 << 10
//!     MB = 1 << 20
//!     GB = 1 << 30
//!     TB = 1 << 40
//!     PB = 1 << 50
//!
//!     negative = bytes_ < 0
//!     if negative:
//!         bytes_ = -bytes_  # Allow formatting of negative numbers.
//!
//!     if bytes_ < KB:
//!         value = ngettext("%(size)d byte", "%(size)d bytes", bytes_) % {"size": bytes_}
//!     elif bytes_ < MB:
//!         value = gettext("%s KB") % filesize_number_format(bytes_ / KB)
//!     elif bytes_ < GB:
//!         value = gettext("%s MB") % filesize_number_format(bytes_ / MB)
//!     elif bytes_ < TB:
//!         value = gettext("%s GB") % filesize_number_format(bytes_ / GB)
//!     elif bytes_ < PB:
//!         value = gettext("%s TB") % filesize_number_format(bytes_ / TB)
//!     else:
//!         value = gettext("%s PB") % filesize_number_format(bytes_ / PB)
//!
//!     if negative:
//!         value = "-%s" % value
//!     return avoid_wrapping(value)
//! ```
//!
//! `avoid_wrapping` is `django/utils/html.py:502-507`, one line:
//! `return value.replace(" ", "\xa0")` — **every** ASCII space, not only the one
//! between the number and the unit.
//!
//! **The body holds no arithmetic.** `int()` is called **once** (`defaultfilters.py:906`), and the
//! quotient `bytes_ / (1 << shift)` crosses the seam whole
//! ([`FilterHost::int_divide_by_power_of_two`]) because CPython's
//! `int.__truediv__` is a correctly-rounded long division that does not convert
//! its operands: a Rust `f64` division of a >308-digit decimal gives `inf` and
//! would answer `'inf.0\xa0PB'` where the source raises, and
//! [`FilterHost::to_float`] raises with a *different message* than the division.
//! The `int()` is inside the `try` at `defaultfilters.py:905-907` and the division is **outside**
//! it, so the two failure modes separate — which is why the seam is two methods
//! and not one.
//!
//! The measured rows that fix this file, all on the pinned tree (`.django/` @
//! 6.0.9, CPython 3.14.4), with the NBSP written as `\xa0`:
//!
//! * **The byte branch is `ngettext`, so the singular is chosen by the
//!   magnitude**: `filesizeformat(1) -> '1\xa0byte'` and `filesizeformat(2) ->
//!   '2\xa0bytes'`, both through `ngettext(...) % {"size": n}`. It is `1023 ->
//!   '1023\xa0bytes'` and `1024 -> '1.0\xa0KB'` at the `1 << 10` boundary, and the
//!   branch selection is *strict* `bytes_ < KB`: 1024 belongs to KB.
//! * **`defaultfilters.py:922` negates before the branch, so every comparison is on the absolute
//!   value** — the step that is easy to omit and agrees on small negatives.
//!   Measured: `-500 -> '-500\xa0bytes'` but `-1024 -> '-1.0\xa0KB'`,
//!   `-2048 -> '-2.0\xa0KB'` and `-1048576 -> '-1.0\xa0MB'`; a signed
//!   `bytes_ < KB` would answer `'-2048\xa0bytes'`. The negation feeds the
//!   branch, the division **and** the byte arm's `ngettext`/`%(size)d`, so
//!   `-1 -> '-1\xa0byte'` keeps the singular. The sign is reattached last
//!   (`defaultfilters.py:937-938`), after the unit: `-100 -> '-100\xa0bytes'`.
//! * **`-(2**63)` is `i64::MIN`, whose negation is one past `i64::MAX`**, so
//!   [`negate`] promotes it to an [`IntValue::Big`] rather than overflowing
//!   `i64` — measured `filesizeformat(-(2**63)) -> '-8192.0\xa0PB'` and
//!   `filesizeformat(-(2**100)) -> '-1125899906842624.0\xa0PB'`. Reachable from
//!   context (`{"x": -(2**63)}`), not as a template literal.
//! * **The five thresholds, measured**: `10**6 -> '976.6\xa0KB'`,
//!   `10**9 -> '953.7\xa0MB'`, `10**12 -> '931.3\xa0GB'`, `10**15 ->
//!   '909.5\xa0TB'`, `10**18 -> '888.2\xa0PB'` (`10**18` is past `PB = 1 << 50`,
//!   `10**15` is not).
//! * **`int()`'s three caught classes all answer `'0\xa0bytes'`** (`defaultfilters.py:907-909`):
//!   measured `filesizeformat(None)` (`TypeError`), `filesizeformat("abc")`
//!   (`ValueError`), `filesizeformat(b"\xff")` (`UnicodeDecodeError`) and
//!   `filesizeformat(float("nan"))` (`ValueError`) are each `'0\xa0bytes'`.
//! * **A class *not* in the tuple escapes**: `filesizeformat(float("inf"))`
//!   raises `OverflowError: cannot convert float infinity to integer` — from
//!   `int()`, and the `except` does not name `OverflowError`. `filesizeformat(10**400)`
//!   and `filesizeformat(-(10**400))` raise `OverflowError: integer division
//!   result too large for a float`, the *division's* message, from outside the
//!   `try`. Both escape the filter and are corpus `ERROR_CASES` rows; the body
//!   propagates them and never synthesises one.
//!
//! `arg` is ignored: `filesizeformat` takes none, and a supplied one
//! (`{{ x|filesizeformat:y }}`) is an arity `TemplateSyntaxError` the parser
//! raises before the body runs.
//!
//! [`FilterHost::int_divide_by_power_of_two`]: crate::filters::host::FilterHost::int_divide_by_power_of_two
//! [`FilterHost::to_float`]: crate::filters::host::FilterHost::to_float

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `KB = 1 << 10` … `PB = 1 << 50` (`defaultfilters.py:914-918`).
const KB: u64 = 1 << 10;
const MB: u64 = 1 << 20;
const GB: u64 = 1 << 30;
const TB: u64 = 1 << 40;
const PB: u64 = 1 << 50;

/// `except (TypeError, ValueError, UnicodeDecodeError)` (`defaultfilters.py:907`) — the three
/// classes that answer `'0 bytes'`.
///
/// `UnicodeDecodeError` is a subclass of `ValueError`, so the source's third
/// name is redundant there; it is **not** redundant here, because the seam
/// reports it as an [`PyErrorKind::Other`] by `__name__` and the two spellings
/// are separate variants. Every other class — an `OverflowError` from `inf`, an
/// `AttributeError` — is not caught and escapes.
fn is_int_refusal(error: &Error) -> bool {
    let ErrorKind::FilterHost(host) = &error.kind else {
        return false;
    };
    match &host.kind {
        PyErrorKind::TypeError | PyErrorKind::ValueError => true,
        PyErrorKind::Other(name) => &**name == "UnicodeDecodeError",
        _ => false,
    }
}

/// `-bytes_` (`defaultfilters.py:922`) for an [`IntValue`].
///
/// `IntValue::Small(i64::MIN)` cannot be negated in `i64` — CPython's answer is
/// `9223372036854775808`, one past `i64::MAX` — so it becomes a
/// [`IntValue::Big`], the same set Python's arbitrary-precision `int` names.
/// `IntValue::Big`'s text carries the sign, so negating one flips its first
/// character.
fn negate(value: &IntValue) -> IntValue {
    match value {
        IntValue::Small(number) => match number.checked_neg() {
            Some(negated) => IntValue::Small(negated),
            None => IntValue::Big("9223372036854775808".to_string()),
        },
        IntValue::Big(text) => match text.strip_prefix('-') {
            Some(positive) => IntValue::Big(positive.to_string()),
            None => IntValue::Big(format!("-{text}")),
        },
    }
}

/// Which of the six arms at `defaultfilters.py:924-935` the **non-negative** `bytes_` takes.
///
/// This is the branch selection the coordinator pins corpus rows against. The
/// cuts are strict `bytes_ < T`, measured: `1023` is [`Branch::Bytes`] and
/// `1024` is `KB`. An [`IntValue::Big`] is past `i64::MAX` and so past every
/// `T` — `PB` is `1 << 50` — which is why it lands on the `else` at `defaultfilters.py:934`.
enum Branch {
    /// `bytes_ < KB` — the `ngettext` arm, carrying the value itself.
    Bytes(u64),
    /// One of the five divided arms: `1 << shift` and the untranslated format.
    Divided { shift: u32, format: &'static str },
}

/// `defaultfilters.py:924-935`. The `u64` cast is exact because the caller has already negated,
/// so a `Small` here is `>= 0` and fits.
fn branch(value: &IntValue) -> Branch {
    match value {
        IntValue::Big(_) => Branch::Divided {
            shift: 50,
            format: "%s PB",
        },
        IntValue::Small(number) => {
            let size = *number as u64;
            if size < KB {
                Branch::Bytes(size)
            } else if size < MB {
                Branch::Divided {
                    shift: 10,
                    format: "%s KB",
                }
            } else if size < GB {
                Branch::Divided {
                    shift: 20,
                    format: "%s MB",
                }
            } else if size < TB {
                Branch::Divided {
                    shift: 30,
                    format: "%s GB",
                }
            } else if size < PB {
                Branch::Divided {
                    shift: 40,
                    format: "%s TB",
                }
            } else {
                Branch::Divided {
                    shift: 50,
                    format: "%s PB",
                }
            }
        }
    }
}

/// `avoid_wrapping` (`html.py:502-507`): `value.replace(" ", "\xa0")`.
///
/// A pure `str` method, so Rust's — every ASCII space becomes a no-break space
/// and nothing else moves. The answer is a **plain `str`**, not `SafeString`:
/// `is_safe=True` on the filter (`defaultfilters.py:899`) is the registry's flag and the loop's
/// job, not the body's, which is why this file never calls `mark_safe`.
fn avoid_wrapping(text: &str) -> Value {
    Value::Str(text.replace(' ', "\u{a0}").into())
}

/// `filesizeformat`'s body: one `int()`, the sign split, the six-way branch, and
/// `avoid_wrapping`.
///
/// `_arg` is unused — the filter takes none; the parser enforces the arity.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `try: bytes_ = int(bytes_)` (`defaultfilters.py:905-907`). The three named classes are the
    // `'0 bytes'` arm; anything else is Python's and escapes the filter.
    let bytes = match call.host.to_int(&value) {
        Ok(int) => int,
        Err(error) if is_int_refusal(&error) => {
            let template = call.host.ngettext("%(size)d byte", "%(size)d bytes", 0);
            let zero = call
                .host
                .percent_mapping(&template, "size", &Value::Int(0))?;
            return Ok(avoid_wrapping(&call.host.to_str(&zero)?));
        }
        Err(error) => return Err(error),
    };

    // `negative = bytes_ < 0` (`defaultfilters.py:920`) — read without a host call, since
    // `IntValue::Big`'s text carries the sign.
    let negative = match &bytes {
        IntValue::Small(number) => *number < 0,
        IntValue::Big(text) => text.starts_with('-'),
    };
    // `if negative: bytes_ = -bytes_` (`defaultfilters.py:921-922`). Negated **here**, before the
    // branch and the division, because every arm works on the magnitude and the
    // sign is reattached once at `defaultfilters.py:937-938`.
    let magnitude = if negative { negate(&bytes) } else { bytes };

    let text = match branch(&magnitude) {
        // `ngettext("%(size)d byte", "%(size)d bytes", bytes_) % {"size": bytes_}`
        // (`defaultfilters.py:925`) — both calls, in that order, with the magnitude for both.
        Branch::Bytes(size) => {
            let template = call.host.ngettext("%(size)d byte", "%(size)d bytes", size);
            let mapped = call
                .host
                .percent_mapping(&template, "size", &Value::Int(size as i64))?;
            call.host.to_str(&mapped)?
        }
        // `gettext("%s U") % filesize_number_format(bytes_ / (1 << shift))`. The
        // left operand is evaluated first, so `gettext` precedes the division;
        // the right operand's division precedes its `number_format` (`defaultfilters.py:911-912`).
        Branch::Divided { shift, format } => {
            let template = call.host.gettext(format);
            let quotient = call.host.int_divide_by_power_of_two(&magnitude, shift)?;
            let formatted = call.host.filesize_number_format(quotient);
            let unit = call
                .host
                .percent_format(&template, &Value::Str(formatted.into()))?;
            call.host.to_str(&unit)?
        }
    };

    // `if negative: value = "-%s" % value` (`defaultfilters.py:937-938`). `value` is a `str` by
    // here and `%s` of a `str` is itself, so the operator is exactly the
    // concatenation — Rust's, not the seam's.
    let text = if negative { format!("-{text}") } else { text };
    Ok(avoid_wrapping(&text))
}

#[cfg(test)]
mod tests {
    //! **Two shapes of test, and the split is a fact about the scripted host.**
    //!
    //! `TestFilterHost`'s answers ignore their arguments, so nothing the body
    //! *sends* the seam is observable: which `shift`, which format string, or
    //! whether the int it divided was negated. What **is** observable is the
    //! branch actually taken — through which operations ran, and in what order —
    //! so the body-level tests assert that, and the pure helpers
    //! [`branch`]/[`negate`] carry the argument-level facts (`bytes_ < KB` and
    //! the sign) with measured rows. Those helper tests assert *which arm*, not a
    //! rendered string, so they are not corpus rows restated.
    //!
    //! **Every error path here is host-passed-through**, so each asserts the
    //! class and that it escapes, and no message: the `OverflowError` texts
    //! belong to CPython and `ERROR_CASES` pins them.
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// A host that reaches the **byte** branch for a non-negative magnitude:
    /// `to_int` small, the `ngettext` pair scripted, and — deliberately — no
    /// `int_divide_by_power_of_two`, so a body that took a divided arm would
    /// fail with a refusal rather than pass by accident.
    fn byte_host(size: i64, mapped: &str) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(IntValue::Small(size)),
            ngettext: Some("N BYTES".to_string()),
            percent_mapping: Some(Value::Str(mapped.into())),
            to_str: Some(mapped.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// A host that reaches a **divided** branch: the division and the formatter
    /// scripted, and `filesize_number_format`'s answer flowing into the `%`.
    fn divided_host(size: i64, quotient: f64, formatted: &str) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(IntValue::Small(size)),
            gettext: Some("UNIT".to_string()),
            int_divide_by_power_of_two: Some(quotient),
            filesize_number_format: Some(formatted.to_string()),
            percent_format: Some(Value::Str("FORMATTED".to_string().into())),
            to_str: Some("FORMATTED".to_string()),
            // Scripted so that a body taking the byte arm **fails on the call
            // list** rather than panicking on an unscripted operation.
            ngettext: Some("N BYTES".to_string()),
            percent_mapping: Some(Value::Str("N BYTES".to_string().into())),
            ..TestFilterHost::default()
        }
    }

    /// The byte branch is `ngettext` then `percent_mapping` then a text read, and
    /// **no division** — the call order is the source's.
    #[test]
    fn a_small_value_takes_the_byte_branch() {
        let h = byte_host(23, "23 bytes");
        let result = run(Value::Int(23), None, &call(&h)).unwrap();
        assert_eq!(
            h.calls(),
            vec!["to_int", "ngettext", "percent_mapping", "to_str"]
        );
        // `avoid_wrapping` ran on the extracted text: the space is the NBSP.
        assert_eq!(result, Value::Str("23\u{a0}bytes".into()));
    }

    /// `1024` is the `1 << 10` cut and belongs to KB — the division runs where
    /// `1023`'s does not. The pair is the body-level statement of the strict
    /// `<`; [`branch`] pins it exactly.
    #[test]
    fn the_kilobyte_boundary_is_strict() {
        let h = divided_host(1024, 1.0, "1.0");
        run(Value::Int(1024), None, &call(&h)).unwrap();
        assert_eq!(
            h.calls(),
            vec![
                "to_int",
                "gettext",
                "int_divide_by_power_of_two",
                "filesize_number_format",
                "percent_format",
                "to_str",
            ]
        );

        let h = byte_host(1023, "1023 bytes");
        run(Value::Int(1023), None, &call(&h)).unwrap();
        assert!(!h.calls().contains(&"int_divide_by_power_of_two"));
    }

    /// A negative value is negated **before** the branch: `-5` is `Small(5)`, so
    /// it is the byte arm and needs no division. A body that skipped `negate`
    /// would cast `-5` to a `u64` past `PB` and take the divided arm, failing on
    /// the unscripted division. This is the negation, made observable.
    #[test]
    fn a_negative_value_is_negated_before_the_branch() {
        let h = byte_host(-5, "5 bytes");
        let result = run(Value::Int(-5), None, &call(&h)).unwrap();
        assert_eq!(
            h.calls(),
            vec!["to_int", "ngettext", "percent_mapping", "to_str"]
        );
        // The sign is reattached after formatting (`defaultfilters.py:937-938`), on the plain text.
        assert_eq!(result, Value::Str("-5\u{a0}bytes".into()));
    }

    /// The negation is on **every** branch, not only the division: `-2048` has a
    /// magnitude of 2048 and must reach the KB arm, where a signed
    /// `bytes_ < KB` would take the byte arm. Measured `-2048 -> '-2.0\xa0KB'`;
    /// here the division being reached at all is the assertion.
    #[test]
    fn a_negative_magnitude_selects_its_branch_by_absolute_value() {
        let h = divided_host(-2048, 2.0, "2.0");
        run(Value::Int(-2048), None, &call(&h)).unwrap();
        h.assert_called("int_divide_by_power_of_two");
        assert!(
            !h.calls().contains(&"ngettext"),
            "a signed comparison would take the byte arm: {:?}",
            h.calls()
        );
    }

    /// A `Big` magnitude is past every threshold and takes `defaultfilters.py:934`'s `else`,
    /// through the division. The value is never converted to a Rust `f64`.
    #[test]
    fn a_big_magnitude_takes_the_petabyte_else() {
        let h = TestFilterHost {
            to_int: Some(IntValue::Big(format!("1{}", "0".repeat(400)))),
            gettext: Some("UNIT".to_string()),
            int_divide_by_power_of_two: Some(8.3e291),
            filesize_number_format: Some("8.3e+291".to_string()),
            percent_format: Some(Value::Str("FORMATTED".to_string().into())),
            to_str: Some("FORMATTED".to_string()),
            ..TestFilterHost::default()
        };
        run(
            Value::BigInt(format!("1{}", "0".repeat(400)).into_boxed_str()),
            None,
            &call(&h),
        )
        .unwrap();
        h.assert_called("int_divide_by_power_of_two");
        h.assert_called("gettext");
    }

    /// Each of `int()`'s three caught classes is the `'0 bytes'` arm:
    /// `ngettext(…, 0)` then `percent_mapping` with the literal `0`.
    #[test]
    fn the_three_caught_classes_answer_zero_bytes() {
        for kind in [
            PyErrorKind::TypeError,
            PyErrorKind::ValueError,
            PyErrorKind::Other("UnicodeDecodeError".into()),
        ] {
            let h = TestFilterHost {
                ngettext: Some("0 bytes".to_string()),
                percent_mapping: Some(Value::Str("0 bytes".into())),
                to_str: Some("0 bytes".to_string()),
                ..TestFilterHost::default()
            };
            h.raise_on("to_int", kind);
            let result = run(Value::Str("abc".into()), None, &call(&h)).unwrap();
            assert_eq!(result, Value::Str("0\u{a0}bytes".into()));
            assert_eq!(
                h.calls(),
                vec!["to_int", "ngettext", "percent_mapping", "to_str"]
            );
        }
    }

    /// An `OverflowError` from `int()` is **not** caught — measured
    /// `filesizeformat(inf)`. It escapes and the `'0 bytes'` arm is not entered.
    #[test]
    fn an_overflow_from_the_int_escapes() {
        let h = TestFilterHost {
            ngettext: Some("0 bytes".to_string()),
            ..TestFilterHost::default()
        };
        h.raise_on("to_int", PyErrorKind::Other("OverflowError".into()));
        let error = run(Value::Float(f64::INFINITY), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::Other("OverflowError".into())),
            "{error:?}"
        );
        assert_eq!(
            h.calls(),
            vec!["to_int"],
            "the byte arm must not be entered"
        );
    }

    /// The division's own `OverflowError` escapes from **outside** any `try` —
    /// measured `filesizeformat(10**400)` — and the formatter is never reached.
    #[test]
    fn an_overflow_from_the_division_escapes() {
        let h = TestFilterHost {
            to_int: Some(IntValue::Big(format!("1{}", "0".repeat(400)))),
            gettext: Some("UNIT".to_string()),
            filesize_number_format: Some("inf.0".to_string()),
            ..TestFilterHost::default()
        };
        h.raise_on(
            "int_divide_by_power_of_two",
            PyErrorKind::Other("OverflowError".into()),
        );
        let error = run(
            Value::BigInt(format!("1{}", "0".repeat(400)).into_boxed_str()),
            None,
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::Other("OverflowError".into())),
            "{error:?}"
        );
        assert!(
            !h.calls().contains(&"filesize_number_format"),
            "the formatter must not run after a failed division: {:?}",
            h.calls()
        );
    }

    /// The body needs the host for its first step, so it cannot answer in Rust.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &crate::filters::host::NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        assert!(run(Value::Int(1024), None, &call).is_err());
        assert!(run(Value::Int(5), None, &call).is_err());
    }

    /// `defaultfilters.py:924-935`'s cuts, both sides of every one of them, plus the `Big` case.
    /// This is the branch predicate the coordinator pins corpus rows against; it
    /// asserts **which arm**, never a rendered string.
    #[test]
    fn branch_selects_the_measured_cuts() {
        let divided = |value: &IntValue| match branch(value) {
            Branch::Divided { shift, .. } => shift,
            Branch::Bytes(_) => panic!("expected a divided branch for {value:?}"),
        };
        let bytes = |value: &IntValue| match branch(value) {
            Branch::Bytes(size) => size,
            Branch::Divided { .. } => panic!("expected the byte branch for {value:?}"),
        };

        assert_eq!(bytes(&IntValue::Small(0)), 0);
        assert_eq!(bytes(&IntValue::Small(1023)), 1023);
        assert_eq!(divided(&IntValue::Small(1024)), 10);
        assert_eq!(divided(&IntValue::Small((1 << 20) - 1)), 10);
        assert_eq!(divided(&IntValue::Small(1 << 20)), 20);
        assert_eq!(divided(&IntValue::Small((1 << 30) - 1)), 20);
        assert_eq!(divided(&IntValue::Small(1 << 30)), 30);
        assert_eq!(divided(&IntValue::Small((1 << 40) - 1)), 30);
        assert_eq!(divided(&IntValue::Small(1 << 40)), 40);
        assert_eq!(divided(&IntValue::Small((1 << 50) - 1)), 40);
        assert_eq!(divided(&IntValue::Small(1 << 50)), 50);
        assert_eq!(divided(&IntValue::Big("1".repeat(30))), 50);
    }

    /// `negative` reads both shapes without a host call, and `negate` inverts
    /// both — including `i64::MIN`, whose negation is one past `i64::MAX` and so
    /// must become a `Big`, as Python's `int` does.
    #[test]
    fn the_sign_and_its_negation_cover_both_shapes() {
        assert!(!matches!(
            IntValue::Small(0),
            IntValue::Small(n) if n < 0
        ));
        assert!(matches!(IntValue::Small(-1), IntValue::Small(n) if n < 0));
        assert!(matches!(
            &IntValue::Big("-5".to_string()),
            IntValue::Big(text) if text.starts_with('-')
        ));
        assert!(matches!(
            &IntValue::Big("5".to_string()),
            IntValue::Big(text) if !text.starts_with('-')
        ));

        assert_eq!(negate(&IntValue::Small(5)), IntValue::Small(-5));
        assert_eq!(negate(&IntValue::Small(-5)), IntValue::Small(5));
        assert_eq!(negate(&IntValue::Small(0)), IntValue::Small(0));
        assert_eq!(
            negate(&IntValue::Small(i64::MIN)),
            IntValue::Big("9223372036854775808".to_string())
        );
        assert_eq!(
            negate(&IntValue::Big("-123".to_string())),
            IntValue::Big("123".to_string())
        );
        assert_eq!(
            negate(&IntValue::Big("123".to_string())),
            IntValue::Big("-123".to_string())
        );
    }
}
