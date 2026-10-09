//! `pluralize` — `defaultfilters.py:942-981`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def pluralize(value, arg="s"):
//!     if "," not in arg:
//!         arg = "," + arg
//!     bits = arg.split(",")
//!     if len(bits) > 2:
//!         return ""
//!     singular_suffix, plural_suffix = bits[:2]
//!
//!     try:
//!         return singular_suffix if float(value) == 1 else plural_suffix
//!     except ValueError:  # Invalid string that's not a number.
//!         pass
//!     except TypeError:  # Value isn't a string or a number; maybe it's a list?
//!         try:
//!             return singular_suffix if len(value) == 1 else plural_suffix
//!         except TypeError:  # len() of unsized object.
//!             pass
//!     return ""
//! ```
//!
//! **Only an *absent* argument gets the default `"s"`.** `{{ x|pluralize }}`
//! passes `Option::None`, which becomes `"s"`; an explicit `None`
//! (`{{ x|pluralize:None }}`) is `Some(Value::None)` and must instead reach
//! [`FilterHost::comma_bits`], where the `in` raises — measured
//! `TypeError: argument of type 'NoneType' is not a container or iterable`. The
//! `Option` distinction is load-bearing here in a way it is not for `yesno`,
//! whose `arg is None` test collapses the two.
//!
//! **`if "," not in arg: arg = "," + arg` and `arg.split(",")` are inside
//! [`FilterHost::comma_bits`]** with `prepend_comma=true` — the `in`, the
//! concatenation and the split are one question whose answer, and whose exception
//! class, is the argument's type's decision (measured: an int argument raises
//! `TypeError` naming the `in`, a list argument gets past it and dies at the
//! concatenation or the split). Nothing is caught, so each escapes the filter.
//! A successful call always yields **at least two** bits — prepending `,` to a
//! comma-less argument guarantees it — which is exactly why the source's
//! `bits[:2]` unpack cannot fail and why indexing `bits[0]`/`bits[1]` below cannot
//! panic.
//!
//! **`len(bits) > 2` returning `""` is the body's, not the seam's.**
//!
//! **The two exception arms return different things, so they must not be
//! merged.** `ValueError` means `float(value)` refused a non-numeric *string*;
//! its body is `pass`, and control falls to the final `return ""` — it can never
//! reach the `len` fallback. `TypeError` means the value is not a number at all
//! (a list, a `None`, an unsized object) and it retries with `len(value)`. A body
//! that treated the two alike, or that read `len` for every non-numeric value,
//! gets `pluralize("abc") -> ""` (measured) wrong — a `len`-based body would
//! answer `"s"` — where `pluralize([1]) -> ""` *is* the `len` answer.
//!
//! The suffix is a plain `Value::Str` (or the literal `""`), never marked safe;
//! `is_safe=False` is the registry's flag, applied in `template-py`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except ValueError:` — `float(value)` refused a non-numeric string, which
/// falls through to the final `return ""`.
fn is_value_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError
    )
}

/// `except TypeError:` — the value is not a number, which retries with
/// `len(value)`.
fn is_type_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host) if host.kind == PyErrorKind::TypeError
    )
}

/// `pluralize`'s body.
///
/// The default argument, [`FilterHost::comma_bits`] (`prepend_comma=true`), the
/// `len(bits) > 2` early return, then `float(value) == 1` with the two
/// distinguished exception arms and the nested `len(value)` retry.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // The default is the string `"s"`, and it applies to an *absent* argument
    // only — a literal `None` is `Some(Value::None)` and belongs to the seam.
    let arg = arg.unwrap_or(Value::Str("s".into()));
    let bits = call.host.comma_bits(&arg, true)?;
    // `prepend_comma=true` guarantees at least two bits, so the two indices below
    // cannot panic and the source's `bits[:2]` cannot fail.
    if bits.len() > 2 {
        return Ok(Value::Str("".into()));
    }
    let singular = &bits[0];
    let plural = &bits[1];
    match call.host.to_float(&value) {
        Ok(number) => {
            let suffix = if number == 1.0 { singular } else { plural };
            Ok(Value::Str(suffix.as_str().into()))
        }
        // `except TypeError`: not a number — maybe a list. Retry with `len`.
        Err(error) if is_type_error(&error) => match call.host.len(&value) {
            Ok(length) => {
                let suffix = if length == 1 { singular } else { plural };
                Ok(Value::Str(suffix.as_str().into()))
            }
            // `except TypeError: pass` — `len()` of an unsized object.
            Err(error) if is_type_error(&error) => Ok(Value::Str("".into())),
            Err(error) => Err(error),
        },
        // `except ValueError: pass`, then the final `return ""`. This arm can
        // never reach the `len` fallback above.
        Err(error) if is_value_error(&error) => Ok(Value::Str("".into())),
        // Any other class is uncaught and escapes.
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

    /// One measured row.
    struct Case {
        value: Value,
        /// The bits `comma_bits` answers with — the argument's split, after the
        /// prepend `pluralize` performs. The default-argument rows are `["", "s"]`.
        bits: &'static [&'static str],
        /// `float(value)`: `Ok` is the float, `Err` the class it raises.
        float: Result<f64, PyErrorKind>,
        /// `len(value)`, consulted only when `float` raises `TypeError`.
        length: Result<usize, PyErrorKind>,
        expected: Value,
    }

    fn host(case: &Case) -> TestFilterHost {
        let h = TestFilterHost {
            comma_bits: Some(case.bits.iter().map(|bit| (*bit).to_string()).collect()),
            to_float: case.float.as_ref().ok().copied(),
            len: case.length.as_ref().ok().copied(),
            ..TestFilterHost::default()
        };
        if let Err(kind) = &case.float {
            h.raise_on("to_float", kind.clone());
        }
        if let Err(kind) = &case.length {
            h.raise_on("len", kind.clone());
        }
        h
    }

    const DEFAULT: &[&str] = &["", "s"];

    /// Every measured row a scripted host can express — the `raises` rows are
    /// the dedicated tests below. The two arms of the value are checked, not just
    /// the answer: `to_float` is consulted only when `len(bits) <= 2`, and `len`
    /// only when `float` raised `TypeError`.
    #[test]
    fn the_measured_rows() {
        let cases = [
            (Value::Int(1), DEFAULT, Ok(1.0), Ok(0), ""),
            (Value::Int(0), DEFAULT, Ok(0.0), Ok(0), "s"),
            (Value::Int(2), DEFAULT, Ok(2.0), Ok(0), "s"),
            (Value::Float(1.0), DEFAULT, Ok(1.0), Ok(0), ""),
            (Value::Float(0.5), DEFAULT, Ok(0.5), Ok(0), "s"),
            (Value::Str("1".into()), DEFAULT, Ok(1.0), Ok(0), ""),
            (Value::Str("1.0".into()), DEFAULT, Ok(1.0), Ok(0), ""),
            (Value::Str(" 1 ".into()), DEFAULT, Ok(1.0), Ok(0), ""),
            // A list is not a number: `TypeError`, then `len`.
            (
                Value::List(vec![Value::Int(1)]),
                DEFAULT,
                Err(PyErrorKind::TypeError),
                Ok(1),
                "",
            ),
            (
                Value::List(vec![Value::Int(1), Value::Int(2)]),
                DEFAULT,
                Err(PyErrorKind::TypeError),
                Ok(2),
                "s",
            ),
            (
                Value::List(vec![]),
                DEFAULT,
                Err(PyErrorKind::TypeError),
                Ok(0),
                "s",
            ),
            // An unsized non-number: both refusals.
            (
                Value::None,
                DEFAULT,
                Err(PyErrorKind::TypeError),
                Err(PyErrorKind::TypeError),
                "",
            ),
            // A non-numeric string is `ValueError`: the `len` fallback is never
            // reached, so `pluralize("abc")` is `""` and not `"s"`.
            (
                Value::Str("abc".into()),
                DEFAULT,
                Err(PyErrorKind::ValueError),
                Ok(3),
                "",
            ),
            (
                Value::Str("".into()),
                DEFAULT,
                Err(PyErrorKind::ValueError),
                Ok(0),
                "",
            ),
            (Value::Bool(true), DEFAULT, Ok(1.0), Ok(0), ""),
            (Value::Bool(false), DEFAULT, Ok(0.0), Ok(0), "s"),
            (Value::Int(3), DEFAULT, Ok(3.0), Ok(0), "s"),
            // Argument shapes.
            (Value::Int(0), &["", "es"], Ok(0.0), Ok(0), "es"),
            (Value::Int(1), &["", "es"], Ok(1.0), Ok(0), ""),
            (Value::Int(1), &["y", "ies"], Ok(1.0), Ok(0), "y"),
            (Value::Int(2), &["y", "ies"], Ok(2.0), Ok(0), "ies"),
            // More than two bits: the whole filter answers `""` before the value
            // is examined.
            (Value::Int(3), &["", "a", "b", "c"], Ok(3.0), Ok(0), ""),
            (Value::Int(0), &["", "a", "b", "c"], Ok(0.0), Ok(0), ""),
            // Both suffixes empty.
            (Value::Int(1), &["", ""], Ok(1.0), Ok(0), ""),
            (Value::Int(2), &["", ""], Ok(2.0), Ok(0), ""),
            (Value::Bool(true), &["y", "ies"], Ok(1.0), Ok(0), "y"),
            (Value::Str("1".into()), &["y", "ies"], Ok(1.0), Ok(0), "y"),
        ];
        for (value, bits, float, length, expected) in cases {
            let row = Case {
                value,
                bits,
                float,
                length,
                expected: Value::Str(expected.into()),
            };
            let h = host(&row);
            let value = row.value.clone();
            let result = run(value.clone(), None, &call(&h)).unwrap();
            assert_eq!(result, row.expected, "pluralize({value:?})");

            h.assert_called("comma_bits");
            let examined = row.bits.len() <= 2;
            assert_eq!(
                h.calls().contains(&"to_float"),
                examined,
                "float(value) is asked only when the argument is valid: {:?}",
                h.calls()
            );
            let length_examined = examined && matches!(&row.float, Err(PyErrorKind::TypeError));
            assert_eq!(
                h.calls().contains(&"len"),
                length_examined,
                "len(value) is the TypeError arm only: {:?}",
                h.calls()
            );
        }
    }

    /// **The distinction the two arms exist for.** A `ValueError` from
    /// `float(value)` is a non-numeric string; its body is `pass` and the answer
    /// is the final `""`, so `len` must **not** be asked. Measured
    /// `pluralize("abc") -> ""` where a `len`-based body answers `"s"`.
    #[test]
    fn a_value_error_never_reaches_the_length_fallback() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["".to_string(), "s".to_string()]),
            to_float: Some(0.0),
            len: Some(3),
            ..TestFilterHost::default()
        };
        h.raise_on("to_float", PyErrorKind::ValueError);
        assert_eq!(
            run(Value::Str("abc".into()), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        h.assert_called("to_float");
        assert!(
            !h.calls().contains(&"len"),
            "a ValueError must not reach the len fallback: {:?}",
            h.calls()
        );
    }

    /// A `TypeError` from `float(value)` **does** retry with `len(value)`. The
    /// recorded order pins it: `comma_bits`, `to_float`, `len`.
    #[test]
    fn a_type_error_retries_with_the_length() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["".to_string(), "s".to_string()]),
            to_float: Some(0.0),
            len: Some(1),
            ..TestFilterHost::default()
        };
        h.raise_on("to_float", PyErrorKind::TypeError);
        assert_eq!(
            run(Value::List(vec![Value::Int(1)]), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        assert_eq!(h.calls(), vec!["comma_bits", "to_float", "len"]);
    }

    /// `len()` of an unsized object is the inner `except TypeError: pass`, and
    /// the answer is the final `""`. Measured `pluralize(None) -> ""`.
    #[test]
    fn an_unsized_value_answers_the_empty_suffix() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["".to_string(), "s".to_string()]),
            to_float: Some(0.0),
            ..TestFilterHost::default()
        };
        h.raise_on("to_float", PyErrorKind::TypeError);
        h.raise_on("len", PyErrorKind::TypeError);
        assert_eq!(
            run(Value::None, None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        assert_eq!(h.calls(), vec!["comma_bits", "to_float", "len"]);
    }

    /// The classes the source does not name escape from `float` — including
    /// `OverflowError`, which `float(10**400)` raises in CPython and which Python's
    /// `except TypeError` therefore does not catch.
    #[test]
    fn a_class_the_source_does_not_catch_escapes_from_float() {
        for kind in [
            PyErrorKind::Other("OverflowError".into()),
            PyErrorKind::AttributeError,
        ] {
            let h = TestFilterHost {
                comma_bits: Some(vec!["".to_string(), "s".to_string()]),
                ..TestFilterHost::default()
            };
            h.raise_on("to_float", kind.clone());
            let error = run(Value::Int(1), None, &call(&h)).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
            h.assert_called("to_float");
            assert!(!h.calls().contains(&"len"), "{:?}", h.calls());
        }
    }

    /// A non-`TypeError` class from `len` escapes too: the inner `except` names
    /// `TypeError` only.
    #[test]
    fn a_class_the_source_does_not_catch_escapes_from_len() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["".to_string(), "s".to_string()]),
            to_float: Some(0.0),
            ..TestFilterHost::default()
        };
        h.raise_on("to_float", PyErrorKind::TypeError);
        h.raise_on("len", PyErrorKind::Other("RecursionError".into()));
        let error = run(Value::None, None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::Other("RecursionError".into())),
            "{error:?}"
        );
        h.assert_called("len");
    }

    /// Every `comma_bits` refusal escapes — there is no `try` around the argument.
    /// Measured, the class is the argument's type's decision: `TypeError` for an
    /// int argument (`{{ x|pluralize:5 }}`), `AttributeError` for a list that got
    /// past the `in` and died at the split.
    #[test]
    fn a_comma_bits_refusal_escapes() {
        for kind in [PyErrorKind::TypeError, PyErrorKind::AttributeError] {
            let h = TestFilterHost {
                comma_bits: Some(vec![]),
                ..TestFilterHost::default()
            };
            h.raise_on("comma_bits", kind.clone());
            let error = run(Value::Int(1), Some(Value::Int(5)), &call(&h)).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
            h.assert_called("comma_bits");
            assert!(
                !h.calls().contains(&"to_float"),
                "the value is not examined once the argument refuses: {:?}",
                h.calls()
            );
        }
    }

    /// The suffixes are the `bits` the host returned, verbatim: a body that
    /// hard-coded `"s"`, or rebuilt the answer, fails here. Both arms are shown.
    #[test]
    fn the_suffixes_come_back_verbatim() {
        for (number, expected) in [(1.0_f64, "SINGULAR"), (2.0, "PLURAL")] {
            let h = TestFilterHost {
                comma_bits: Some(vec!["SINGULAR".to_string(), "PLURAL".to_string()]),
                to_float: Some(number),
                ..TestFilterHost::default()
            };
            assert_eq!(
                run(Value::Int(1), Some(Value::Str("ignored".into())), &call(&h)).unwrap(),
                Value::Str(expected.into()),
                "float(value) == {number}"
            );
        }

        let h = TestFilterHost {
            comma_bits: Some(vec!["ONE".to_string(), "MANY".to_string()]),
            to_float: Some(0.0),
            len: Some(1),
            ..TestFilterHost::default()
        };
        h.raise_on("to_float", PyErrorKind::TypeError);
        assert_eq!(
            run(Value::List(vec![Value::Int(1)]), None, &call(&h)).unwrap(),
            Value::Str("ONE".into())
        );
    }

    /// `{{ x|pluralize }}` is legal: an absent argument means the default `"s"`,
    /// whose `comma_bits(&arg, true)` must still be made. A body that
    /// early-returned on a missing argument would answer where Django splits.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["".to_string(), "s".to_string()]),
            to_float: Some(0.0),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(0), None, &call(&h)).unwrap(),
            Value::Str("s".into())
        );
        h.assert_called("comma_bits");
        h.assert_called("to_float");
    }

    /// An explicit `None` argument is **not** the default: it reaches
    /// `comma_bits`, whose `in` raises `TypeError` (measured). A body that folded
    /// the two `Option` spellings together would answer `"s"` here.
    #[test]
    fn an_explicit_none_argument_is_not_the_default() {
        let h = TestFilterHost {
            comma_bits: Some(vec![]),
            ..TestFilterHost::default()
        };
        h.raise_on("comma_bits", PyErrorKind::TypeError);
        let error = run(Value::Int(1), Some(Value::None), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "{error:?}"
        );
        h.assert_called("comma_bits");
    }

    /// `is_safe=False` is the registry's flag, applied in `template-py`; this
    /// body returns a plain `Value::Str` and never calls `mark_safe`.
    #[test]
    fn the_body_returns_a_plain_str_and_never_marks_safe() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["".to_string(), "s".to_string()]),
            to_float: Some(0.0),
            mark_safe: Some(Value::Safe("marked".into())),
            ..TestFilterHost::default()
        };
        let result = run(Value::Int(0), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("s".into()));
        assert_eq!(result.type_name(), "str", "the loop marks, not the body");
        assert!(!h.calls().contains(&"mark_safe"), "{:?}", h.calls());
    }

    /// The body needs the seam, so it cannot compute the answer in Rust and pass
    /// a bare-shell test by luck. `comma_bits` is a `Result` operation, so the
    /// bare-shell refusal is an ordinary `Err`.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        assert!(run(Value::Int(1), None, &call).is_err());
    }
}
