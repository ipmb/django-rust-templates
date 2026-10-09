//! `yesno` — `defaultfilters.py:860-891`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def yesno(value, arg=None):
//!     if arg is None:
//!         # Translators: Please do not add spaces around commas.
//!         arg = gettext("yes,no,maybe")
//!     bits = arg.split(",")
//!     if len(bits) < 2:
//!         return value  # Invalid arg.
//!     try:
//!         yes, no, maybe = bits
//!     except ValueError:
//!         # Unpack list of wrong size (no "maybe" value provided).
//!         yes, no, maybe = bits[0], bits[1], bits[1]
//!     if value is None:
//!         return maybe
//!     if value:
//!         return yes
//!     return no
//! ```
//!
//! **`arg is None` covers both of our cases, and they must not be separated.**
//! An absent argument (`arg: None`) and the literal `None`
//! (`Some(Value::None)`) are both `arg is None` in Python, so both take
//! [`FilterHost::gettext`]; every *other* `Some(v)` goes to `comma_bits`
//! unchanged. `gettext` is not a `Result` — it cannot raise — so it needs no `?`,
//! and with the test settings' `USE_I18N=False` it returns its input unchanged.
//!
//! **[`FilterHost::comma_bits`] is the whole of `if "," not in arg: …` plus
//! `.split(",")`**, and the `false` it is given is what makes `yesno`'s order
//! right: an argument that is not a container reaches the split untouched and
//! dies there — measured, `{{ x|yesno:5 }}` is
//! `AttributeError: 'int' object has no attribute 'split'`, where `pluralize`'s
//! `in` would have raised `TypeError` first. Nothing is caught, so that class
//! escapes the filter.
//!
//! **The `try`/`except ValueError` is the unpack, and it succeeds exactly when
//! `len(bits) == 3`.** The fallback is `bits[0], bits[1], bits[1]` — it also
//! covers **more** than three bits, measured `yesno(None, "a,b,c,d") -> "b"`.
//! The `len(bits) < 2` guard above returns **the value itself**, not a string and
//! not a copy: measured `yesno(True, "y") -> True`, `yesno(None, "y") -> None`,
//! `yesno(7, "y") -> 7`.
//!
//! **`value is None` is checked before truthiness, and when it holds
//! [`FilterHost::is_true`] must not be called at all** — measured
//! `yesno(None, "y,n,m") -> "m"` where a truthiness-first body would answer the
//! `no` bit. Only the remaining values reach `is_true`.
//!
//! The three answers come from the `bits` the host returned, as plain `Value::Str`
//! — the body never calls `mark_safe` (`is_safe=False` is the registry's flag,
//! applied in `template-py` from the pre-filter value), and the `len(bits) < 2`
//! answer is whatever `Value` was passed in.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `yesno`'s body.
///
/// The `gettext` branch for an absent or explicit-`None` argument, then
/// [`FilterHost::comma_bits`] (`prepend_comma=false`), the `len(bits) < 2` early
/// return of the value, the three-way unpack, and finally `value is None` before
/// `bool(value)`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `if arg is None`. An absent argument and the literal `None` are the same
    // Python input, so the two `Option` spellings collapse here.
    let arg = match arg {
        None | Some(Value::None) => Value::Str(call.host.gettext("yes,no,maybe").into()),
        Some(other) => other,
    };
    let bits = call.host.comma_bits(&arg, false)?;
    if bits.len() < 2 {
        // Invalid arg: the value itself, unexamined.
        return Ok(value);
    }
    // `yes, no, maybe = bits` succeeds exactly when `len(bits) == 3`; every other
    // length >= 2 is the `except ValueError` fallback. `comma_bits` is free to
    // return any number of bits, so the length is asked rather than assumed.
    let (yes, no, maybe) = if bits.len() == 3 {
        (bits[0].clone(), bits[1].clone(), bits[2].clone())
    } else {
        (bits[0].clone(), bits[1].clone(), bits[1].clone())
    };
    if matches!(value, Value::None) {
        return Ok(Value::Str(maybe.into()));
    }
    if call.host.is_true(&value)? {
        Ok(Value::Str(yes.into()))
    } else {
        Ok(Value::Str(no.into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::{NoFilterHost, TestFilterHost};
    use crate::parser::error::{ErrorKind, PyErrorKind};

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
        /// `None` is an **absent** argument; `Some(Value::None)` is the literal
        /// `None` — both take the `gettext` branch, and both are represented here
        /// by an absent argument plus a scripted `gettext`.
        arg: Option<Value>,
        /// The `gettext` answer, scripted only on the rows that take that branch.
        /// An unscripted `gettext` panics, so a body that asked for it on the
        /// wrong row fails rather than passing by luck.
        gettext: Option<&'static str>,
        bits: &'static [&'static str],
        /// The scripted `bool(value)`. `None` means `is_true` must **not** be
        /// called — the `value is None` rows and the invalid-argument rows.
        is_true: Option<bool>,
        expected: Value,
    }

    fn case(
        value: Value,
        arg: Option<Value>,
        gettext: Option<&'static str>,
        bits: &'static [&'static str],
        is_true: Option<bool>,
        expected: Value,
    ) -> Case {
        Case {
            value,
            arg,
            gettext,
            bits,
            is_true,
            expected,
        }
    }

    fn host(case: &Case) -> TestFilterHost {
        TestFilterHost {
            gettext: case.gettext.map(str::to_string),
            comma_bits: Some(case.bits.iter().map(|bit| (*bit).to_string()).collect()),
            is_true: case.is_true,
            ..TestFilterHost::default()
        }
    }

    /// Every measured row a scripted host can express. The argument-omitted rows
    /// take `gettext("yes,no,maybe")`; the invalid-argument rows (`"y"`, `""`)
    /// return the value; the `value is None` rows never consult `is_true`.
    #[test]
    fn the_measured_rows() {
        let cases = [
            // The `gettext` branch.
            case(
                Value::Bool(true),
                None,
                Some("yes,no,maybe"),
                &["yes", "no", "maybe"],
                Some(true),
                Value::Str("yes".into()),
            ),
            case(
                Value::Bool(false),
                None,
                Some("yes,no,maybe"),
                &["yes", "no", "maybe"],
                Some(false),
                Value::Str("no".into()),
            ),
            case(
                Value::None,
                None,
                Some("yes,no,maybe"),
                &["yes", "no", "maybe"],
                None,
                Value::Str("maybe".into()),
            ),
            // Three bits.
            case(
                Value::Bool(true),
                Some(Value::Str("y,n,m".into())),
                None,
                &["y", "n", "m"],
                Some(true),
                Value::Str("y".into()),
            ),
            case(
                Value::Bool(false),
                Some(Value::Str("y,n,m".into())),
                None,
                &["y", "n", "m"],
                Some(false),
                Value::Str("n".into()),
            ),
            case(
                Value::None,
                Some(Value::Str("y,n,m".into())),
                None,
                &["y", "n", "m"],
                None,
                Value::Str("m".into()),
            ),
            // Two bits: no mapping for None, so `None` answers the `no` bit.
            case(
                Value::Bool(true),
                Some(Value::Str("y,n".into())),
                None,
                &["y", "n"],
                Some(true),
                Value::Str("y".into()),
            ),
            case(
                Value::Bool(false),
                Some(Value::Str("y,n".into())),
                None,
                &["y", "n"],
                Some(false),
                Value::Str("n".into()),
            ),
            case(
                Value::None,
                Some(Value::Str("y,n".into())),
                None,
                &["y", "n"],
                None,
                Value::Str("n".into()),
            ),
            // Four bits: the unpack fails and the fallback uses bits[0], bits[1].
            case(
                Value::Bool(true),
                Some(Value::Str("a,b,c,d".into())),
                None,
                &["a", "b", "c", "d"],
                Some(true),
                Value::Str("a".into()),
            ),
            case(
                Value::None,
                Some(Value::Str("a,b,c,d".into())),
                None,
                &["a", "b", "c", "d"],
                None,
                Value::Str("b".into()),
            ),
            // One bit: invalid, the value comes back untouched.
            case(
                Value::Bool(true),
                Some(Value::Str("y".into())),
                None,
                &["y"],
                None,
                Value::Bool(true),
            ),
            case(
                Value::Bool(false),
                Some(Value::Str("y".into())),
                None,
                &["y"],
                None,
                Value::Bool(false),
            ),
            case(
                Value::None,
                Some(Value::Str("y".into())),
                None,
                &["y"],
                None,
                Value::None,
            ),
            case(
                Value::Int(7),
                Some(Value::Str("y".into())),
                None,
                &["y"],
                None,
                Value::Int(7),
            ),
            case(
                Value::Bool(true),
                Some(Value::Str("".into())),
                None,
                &[""],
                None,
                Value::Bool(true),
            ),
            case(
                Value::None,
                Some(Value::Str("".into())),
                None,
                &[""],
                None,
                Value::None,
            ),
            // Truthiness of non-bool values.
            case(
                Value::Int(1),
                Some(Value::Str("y,n,m".into())),
                None,
                &["y", "n", "m"],
                Some(true),
                Value::Str("y".into()),
            ),
            case(
                Value::Str("".into()),
                Some(Value::Str("y,n,m".into())),
                None,
                &["y", "n", "m"],
                Some(false),
                Value::Str("n".into()),
            ),
        ];
        for case in &cases {
            let h = host(case);
            let value = case.value.clone();
            let result = run(value.clone(), case.arg.clone(), &call(&h)).unwrap();
            assert_eq!(result, case.expected, "yesno({value:?}, {:?})", case.arg);
            h.assert_called("comma_bits");
            if case.gettext.is_some() {
                h.assert_called("gettext");
            }
            if case.is_true.is_some() {
                h.assert_called("is_true");
            } else {
                assert!(
                    !h.calls().contains(&"is_true"),
                    "is_true must not be consulted for {value:?}: {:?}",
                    h.calls()
                );
            }
        }
    }

    /// The literal `None` argument is the same Python input as an absent one, so
    /// it too takes the `gettext` branch rather than reaching `comma_bits` with
    /// `None` — which would raise, measured
    /// `TypeError: argument of type 'NoneType' is not a container or iterable`
    /// through `pluralize`.
    #[test]
    fn an_explicit_none_argument_takes_the_gettext_branch() {
        let h = TestFilterHost {
            gettext: Some("yes,no,maybe".to_string()),
            comma_bits: Some(vec![
                "yes".to_string(),
                "no".to_string(),
                "maybe".to_string(),
            ]),
            is_true: Some(true),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Bool(true), Some(Value::None), &call(&h)).unwrap(),
            Value::Str("yes".into())
        );
        h.assert_called("gettext");
        h.assert_called("comma_bits");
    }

    /// The one-bit answer is **the value itself**, by identity — measured
    /// `yesno(7, "y") -> 7`. A body that stringified the value, or returned a
    /// fresh copy, fails here.
    #[test]
    fn an_invalid_argument_returns_the_value_itself() {
        let values = [
            Value::Bool(true),
            Value::None,
            Value::Int(7),
            Value::Opaque(9),
            Value::List(vec![Value::Int(1)]),
        ];
        for value in values {
            let h = TestFilterHost {
                comma_bits: Some(vec!["y".to_string()]),
                ..TestFilterHost::default()
            };
            let result = run(value.clone(), Some(Value::Str("y".into())), &call(&h)).unwrap();
            assert_eq!(result, value, "the value comes back untouched");
            assert_eq!(result.type_name(), value.type_name());
            h.assert_called("comma_bits");
            assert!(!h.calls().contains(&"is_true"), "{:?}", h.calls());
        }
    }

    /// The words are the `bits` the host returned, verbatim: a body that
    /// hard-coded the defaults, or rebuilt the answer, fails here.
    #[test]
    fn the_words_come_back_verbatim() {
        let h = TestFilterHost {
            comma_bits: Some(vec!["SI".to_string(), "NO".to_string(), "MOLY".to_string()]),
            is_true: Some(true),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(1), Some(Value::Str("ignored".into())), &call(&h)).unwrap(),
            Value::Str("SI".into())
        );

        let h = TestFilterHost {
            comma_bits: Some(vec!["SI".to_string(), "NO".to_string(), "MOLY".to_string()]),
            is_true: Some(false),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(0), Some(Value::Str("ignored".into())), &call(&h)).unwrap(),
            Value::Str("NO".into())
        );
    }

    /// Every `comma_bits` refusal escapes: the source has no `try` around the
    /// split, and the two classes it can raise are the argument's type's decision
    /// — `AttributeError` for an int argument, `TypeError` for the `in`'s
    /// "not a container". Both are measured and both are reachable from template
    /// syntax.
    #[test]
    fn a_comma_bits_refusal_escapes() {
        for kind in [PyErrorKind::AttributeError, PyErrorKind::TypeError] {
            let h = TestFilterHost {
                comma_bits: Some(vec![]),
                ..TestFilterHost::default()
            };
            h.raise_on("comma_bits", kind.clone());
            let error = run(Value::Bool(true), Some(Value::Int(5)), &call(&h)).unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
            h.assert_called("comma_bits");
            assert!(
                !h.calls().contains(&"is_true"),
                "the value is not examined once the argument refuses: {:?}",
                h.calls()
            );
        }
    }

    /// `bool(value)` may refuse, and nothing catches it.
    #[test]
    fn a_truthiness_refusal_escapes() {
        for kind in [
            PyErrorKind::ValueError,
            PyErrorKind::TypeError,
            PyErrorKind::Other("RuntimeError".into()),
        ] {
            let h = TestFilterHost {
                comma_bits: Some(vec!["y".to_string(), "n".to_string(), "m".to_string()]),
                ..TestFilterHost::default()
            };
            h.raise_on("is_true", kind.clone());
            let error = run(
                Value::Opaque(0),
                Some(Value::Str("y,n,m".into())),
                &call(&h),
            )
            .unwrap_err();
            assert!(
                matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == kind),
                "expected {kind:?}: {error:?}"
            );
            h.assert_called("is_true");
        }
    }

    /// `{{ x|yesno }}` is legal: an absent argument means `gettext`, whose result
    /// must still reach `comma_bits`. A body that early-returned on a missing
    /// argument would answer where Django consults the catalog and the split.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = TestFilterHost {
            gettext: Some("yes,no,maybe".to_string()),
            comma_bits: Some(vec![
                "yes".to_string(),
                "no".to_string(),
                "maybe".to_string(),
            ]),
            is_true: Some(true),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Bool(true), None, &call(&h)).unwrap(),
            Value::Str("yes".into())
        );
        h.assert_called("gettext");
        h.assert_called("comma_bits");
        h.assert_called("is_true");
    }

    /// `is_safe=False` is the registry's flag, applied in `template-py`; this
    /// body returns a plain `Value::Str` and never calls `mark_safe`. A body that
    /// did would return the scripted `Safe` and fail both assertions.
    #[test]
    fn the_body_returns_a_plain_str_and_never_marks_safe() {
        let h = TestFilterHost {
            gettext: Some("yes,no,maybe".to_string()),
            comma_bits: Some(vec![
                "yes".to_string(),
                "no".to_string(),
                "maybe".to_string(),
            ]),
            is_true: Some(true),
            mark_safe: Some(Value::Safe("marked".into())),
            ..TestFilterHost::default()
        };
        let result = run(Value::Bool(true), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("yes".into()));
        assert_eq!(result.type_name(), "str", "the loop marks, not the body");
        assert!(!h.calls().contains(&"mark_safe"), "{:?}", h.calls());
    }

    /// The body's **first** host call for a missing argument is `gettext`, one of
    /// the 17 non-`Result` operations that [`NoFilterHost`] refuses by
    /// **panicking** rather than returning `Err` (`filters/host.rs:576-739`), so
    /// the bare-shell test observes the panic the way `stringformat.rs`'s does.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        let reached = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            run(Value::Bool(true), None, &call)
        }));
        assert!(
            reached.is_err(),
            "the body must reach the host, whose gettext refusal here is a panic"
        );
    }
}
