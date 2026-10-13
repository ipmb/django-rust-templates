//! `floatformat` — `defaultfilters.py:94-220`.
//!
//! **The seam runs between the control flow and the decimal engine.** "What a
//! `decimal.Context` is set to" is not a function of the filter's arguments, and
//! `defaultfilters.py:200` reads it (`prec = max(getcontext().prec, prec)`, past
//! `defaultfilters.py:194`'s `Decimal(1).scaleb(-abs(p))`). The context is
//! observable through the answer: below `2 * (Emax + prec)` the `scaleb` clamps to
//! `Decimal("0E-999999")`, so the rounding position is `999999` whatever `p` is, and
//! above it the same call raises `InvalidOperation` out of a `try` that does not
//! exist. Measured end to end, `floatformat(1.5, 2000054)` is 2000056 characters at
//! the default `prec=28` and raises `InvalidOperation` at `prec=1`. So the parse,
//! the `scaleb`, the `prec` and the `quantize` cross the seam whole
//! ([`FilterHost::decimal_parse`], [`FilterHost::decimal_quantize`]); Rust keeps the
//! `arg`-suffix dispatch, the 200 refusal, the `not m and p <= 0` branch, the digit
//! assembly and the `.` insertion.
//!
//! **`arg[-1]` runs before any parse, on a `str` argument, and an empty one is a
//! CPython `IndexError` that escapes** — measured `floatformat(6666.6666, "")`
//! raises `IndexError: string index out of range`, so a body guarding with
//! `is_empty()` would answer where Django raises. The dispatch is Rust's because it
//! is a function of the argument, as `str(value)[-arg]` is in
//! `crates/template-core/src/filters/get_digit.rs`, and [`index_out_of_range`]
//! synthesises the error for the same reason no seam method is asked.
//!
//! **`int(arg)`'s `ValueError` is `return input_val`, a plain `str`.** Measured
//! `floatformat(6666.6666, "x") -> '6666.6666'` and `floatformat(6666.6666, "2.5")
//! -> '6666.6666'`, both `type() is str`. Suffix rows: `"2g" -> '6,666.67'`, `"2u"
//! -> '6666.67'`, `"g" -> '6,666.7'`, `"gu"`/`"ug"`/`"u" -> '6666.7'`, `"0" ->
//! '6667'`, `"20" -> '6666.66660000000000000000'`.
//!
//! **`inf`/`-inf`/`nan` do not raise here; they take the `int(d)` arm and come back
//! as `input_val`.** Measured `floatformat(inf) -> 'inf'`, `-inf -> '-inf'`, `nan ->
//! 'nan'`, all plain `str`. `defaultfilters.py:181` catches `OverflowError`, so the
//! `TypeError` the 200-sum catches, `int(Decimal("Infinity"))`'s `OverflowError` and
//! the NaN's `ValueError` all reach the same `return input_val` arm. `"abc"` and
//! `None` are the other arm: `Unparseable`, and `""`.
//!
//! The remaining measured subtleties:
//!
//! * **The integer branch is the *integral-value* test, not `exponent < 0`**
//!   (`defaultfilters.py:184`): `not m and p <= 0` asks "is the value whole", and
//!   `Decimal("1.0")` has a falsy `m` with `exponent == -1`, so `floatformat(1.0, -1)
//!   -> '1'` where an exponent test would take the quantize path.
//! * **Rounding is `ROUND_HALF_UP` on the exact decimal** (`defaultfilters.py:204`),
//!   not banker's: `floatformat(2.5, 0) -> '3'` where `round(2.5)` is `2`.
//! * **The sign is decided on the value *after* the quantization**
//!   (`defaultfilters.py:210`, `if sign and rounded_d`), a different question from
//!   `m`'s — `floatformat("-0.0001", 2) -> '0.00'` with the minus dropped (the rounded
//!   `Decimal("-0.00")` is falsy) and `floatformat("-0.5", 0) -> '-1'` with it kept;
//!   hence [`DecimalTuple`] carries `truthy` beside the parse's `integral`.
//! * **`mark_safe` is in the body, on the two normal returns only**
//!   (`defaultfilters.py:185`, `defaultfilters.py:213`): `floatformat(34.0) -> '34'` is a
//!   `SafeString` while `floatformat(nan) -> 'nan'` and `floatformat(1.5, "abc")` are
//!   plain `str`. The three `return input_val` arms build a `Value::Str` and never
//!   reach the seam's `mark_safe`.
//! * **The assembled text is built by repetition, never by a Rust format spec.**
//!   `format!("{:.*}", p, x)` panics for a runtime precision past `u16` (65535) where
//!   Django answers; `{{ x|floatformat:70000 }}` is legal, its precision a `u64` here,
//!   refused only by the host's `scaleb` clamp. So [`integer_text`] and [`assemble`]
//!   append digits.

use crate::filters::host::{DecimalParse, DecimalTuple, FilterCall, NumberFormatArgs};
use crate::filters::safety;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `except ValueError:` on `int(arg)` (`defaultfilters.py:162`) — the one class that arm names.
/// `int(None)`'s `TypeError` is not caught and escapes.
fn is_value_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError
    )
}

/// CPython's `arg[-1]` on an empty `str` — the read `defaultfilters.py:141` performs before any
/// parse, which the dispatch has no `try` around.
///
/// Synthesised because no [`FilterHost`] method is asked for it: the dispatch is a
/// function of the argument, so it stays Rust, as `str(value)[-arg]` and
/// `int("-")` do in `crates/template-core/src/filters/get_digit.rs`.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn index_out_of_range() -> Error {
    Error::filter_host(
        PyErrorKind::IndexError,
        "str.__getitem__",
        "string index out of range",
    )
}

/// `defaultfilters.py:138-151`'s whole output: the `arg` the rest of the body sees,
/// and the two flags both `number_format` calls take.
struct Dispatch {
    /// `arg` with its suffix cut (`arg[:-2] or -1`, `arg[:-1] or -1`), or unchanged
    /// when it is not a `str`.
    arg: Value,
    /// `force_grouping` (`defaultfilters.py:143`, `defaultfilters.py:147`).
    force_grouping: bool,
    /// `use_l10n`, `True` at the start and passed to `number_format` as-is —
    /// `false` for the `u`/`gu`/`ug` suffixes.
    use_l10n: bool,
}

/// `arg[:-n] or -1` (`defaultfilters.py:145`, `defaultfilters.py:148`, `defaultfilters.py:151`):
/// the prefix, or the `-1` the `or` supplies when cutting the suffix leaves nothing.
/// The result is a plain `Value::Str` even from a `SafeString`; it is only ever fed
/// to `int()`, which cannot see the marker.
fn strip_suffix(text: &str, n: usize) -> Value {
    let keep = text.chars().count().saturating_sub(n);
    let kept: String = text.chars().take(keep).collect();
    if kept.is_empty() {
        Value::Int(-1)
    } else {
        Value::Str(kept.into())
    }
}

/// `defaultfilters.py:140-151`. `arg` is read as a `Value` and not through `to_str`
/// because its *type* is the branch: a non-`str` argument skips the block, so
/// `floatformat(1.5, 2)` reaches `int(2)` where `floatformat(1.5, "2.5")` reaches
/// `input_val`.
fn dispatch_suffix(arg: Option<Value>) -> Result<Dispatch, Error> {
    let mut dispatch = Dispatch {
        arg: arg.unwrap_or(Value::Int(-1)),
        force_grouping: false,
        use_l10n: true,
    };
    if let Value::Str(text) | Value::Safe(text) = &dispatch.arg {
        // `arg[-1]`, then `arg[-2:]`, both **code points**.
        let mut tail = text.chars().rev();
        let last_char = tail.next().ok_or_else(index_out_of_range)?;
        let second_last = tail.next();
        match (last_char, second_last) {
            // `arg[-2:] in {"gu", "ug"}` — read from the right, so `"gu"` arrives
            // as `('u', Some('g'))`; the `or -1` is inside [`strip_suffix`].
            ('u', Some('g')) | ('g', Some('u')) => {
                dispatch.force_grouping = true;
                dispatch.use_l10n = false;
                dispatch.arg = strip_suffix(text, 2);
            }
            ('g', _) => {
                dispatch.force_grouping = true;
                dispatch.arg = strip_suffix(text, 1);
            }
            ('u', _) => {
                dispatch.use_l10n = false;
                dispatch.arg = strip_suffix(text, 1);
            }
            // Every other last character, `-` and a digit alike: the argument
            // passes through unchanged and `int()` sees the whole text.
            _ => {}
        }
    }
    Ok(dispatch)
}

/// `int(arg)` (`defaultfilters.py:161`) reduced to the two questions the body asks of
/// it — `p <= 0` (`defaultfilters.py:184`) and `abs(p)` (`defaultfilters.py:194`).
///
/// Not an `i64` because Python's `int` is arbitrary-precision and `p <= 0` is
/// answerable for a value that does not fit — measured
/// `floatformat(1.0, "-99999999999999999999999")` takes the integer branch and answers
/// `'1'`. A `Big` never reaches `i64::try_from(...).unwrap()`, so it cannot panic; only
/// its *magnitude* saturates, and only for a quantize, where the host's `scaleb`
/// refuses first.
struct Argument {
    /// `p < 0`, read from a `Small`'s sign or a `Big`'s text.
    negative: bool,
    /// `abs(p)`, saturating past `u64`.
    magnitude: u64,
}

impl Argument {
    fn new(int: IntValue) -> Self {
        match int {
            IntValue::Small(number) => Self {
                negative: number < 0,
                magnitude: number.unsigned_abs(),
            },
            // `IntValue::Big`'s text carries the sign and is never zero.
            IntValue::Big(text) => Self {
                negative: text.starts_with('-'),
                magnitude: text.trim_start_matches('-').parse().unwrap_or(u64::MAX),
            },
        }
    }

    /// `p <= 0` (`defaultfilters.py:184`).
    fn is_nonpositive(&self) -> bool {
        self.negative || self.magnitude == 0
    }
}

/// `"%d" % (int(d))` (`defaultfilters.py:187`) — the integer branch's operand, built by
/// repetition rather than through a Rust format spec.
///
/// Only reached when [`ParsedDecimal::integral`] holds, so `int(d)` truncating toward
/// zero is `d` itself and the digits/exponent pair spells it exactly. `digits` is
/// most-significant first.
fn integer_text(digits: &[u8], exponent: i64, sign: bool) -> String {
    // `digits.len() + abs(exponent) <= 200` has already been established by the
    // caller, so neither repetition below is unbounded.
    let mut digits: Vec<u8> = digits.to_vec();
    if exponent >= 0 {
        digits.extend(std::iter::repeat_n(0, exponent as usize));
    } else {
        let dropped = exponent.unsigned_abs() as usize;
        if dropped >= digits.len() {
            digits.clear();
        } else {
            digits.truncate(digits.len() - dropped);
        }
    }
    let first = digits
        .iter()
        .position(|digit| *digit != 0)
        .unwrap_or(digits.len());
    let mut text: String = digits[first..]
        .iter()
        .map(|digit| (b'0' + digit) as char)
        .collect();
    if text.is_empty() {
        // `"%d" % 0` is `"0"`, and `int(Decimal("0.00"))` is a zero with no
        // significant digit left after the leading zeros are stripped.
        text.push('0');
    } else if sign {
        // Python writes no `-0`: the sign is only prepended to a nonzero text.
        text.insert(0, '-');
    }
    text
}

/// `defaultfilters.py:205-212` — the digit assembly, the `.` insertion and the sign, verbatim.
///
/// The source's `digits.insert(-exponent, ".")` is `insert(abs(exponent), ".")` here
/// because the indexed value is the *quantized* tuple's exponent, the target `exp`'s:
/// `-abs(p)`, never positive. The sign is [`sign`] **and [`truthy`]** — the
/// `if sign and rounded_d` of `defaultfilters.py:210`, whose second half is the value after
/// rounding, not the parse's `m`.
///
/// [`sign`]: DecimalTuple::sign
/// [`truthy`]: DecimalTuple::truthy
fn assemble(rounded: &DecimalTuple) -> String {
    let mut digits: Vec<char> = rounded
        .digits
        .iter()
        .rev()
        .map(|digit| (b'0' + digit) as char)
        .collect();
    let exponent = rounded.exponent.unsigned_abs() as usize;
    while digits.len() <= exponent {
        digits.push('0');
    }
    digits.insert(exponent, '.');
    if rounded.sign && rounded.truthy {
        digits.push('-');
    }
    digits.iter().rev().collect()
}

/// The `return input_val` arms' value: `str(text)` (`defaultfilters.py:153`), a plain `str`
/// and never `mark_safe`d.
fn input_value(input_val: &str) -> Value {
    Value::Str(input_val.into())
}

/// `mark_safe(formats.number_format(number, decimal_pos, use_l10n=…, force_grouping=…))` — the
/// shape both normal returns have (`defaultfilters.py:185` and `defaultfilters.py:213`).
///
/// The operand is a **string**, not an `f64`: `defaultfilters.py:187` hands over `"%d" % int(d)`
/// and `defaultfilters.py:215` the assembled `number` ([`NumberFormatArgs`]'s str case).
fn formatted(
    number: String,
    decimal_pos: i64,
    dispatch: &Dispatch,
    call: &FilterCall<'_>,
) -> Value {
    let text = call.host.number_format(NumberFormatArgs {
        value: &Value::Str(number.into()),
        decimal_pos: Some(decimal_pos),
        use_l10n: Some(dispatch.use_l10n),
        force_grouping: dispatch.force_grouping,
    });
    safety::mark_safe(Value::Str(text.into()), call)
}

/// `floatformat`'s body: the Rust dispatch, the host's decimal parse, the three
/// `return input_val` arms in source order, and the two normal branches.
///
/// `arg.unwrap_or(Value::Int(-1))` is `defaultfilters.py:95`'s default and applies only to a
/// **missing** argument: `{{ x|floatformat:None }}` is `int(None)`'s `TypeError` — the
/// FFI distinguishes them by length (`crates/template-py/src/filters.rs:1601-1609`), so
/// the literal `None` reaches `to_int(Value::None)` and escapes where the absent
/// argument answers the default.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `defaultfilters.py:140-151` first, before any parse: an empty-string argument raises here.
    let dispatch = dispatch_suffix(arg)?;

    // `try: input_val = str(text); d = Decimal(input_val)` — `defaultfilters.py:152-159`, whose
    // double failure is `return ""`.
    let parsed = match call.host.decimal_parse(&value)? {
        DecimalParse::Parsed(parsed) => parsed,
        DecimalParse::Unparseable => return Ok(Value::Str("".into())),
    };

    // `p = int(arg)` (`defaultfilters.py:161`), with `except ValueError: return input_val`; a
    // `TypeError` `int(None)` is not caught and escapes.
    let p = match call.host.to_int(&dispatch.arg) {
        Ok(int) => Argument::new(int),
        Err(error) if is_value_error(&error) => return Ok(input_value(&parsed.input_val)),
        Err(error) => return Err(error),
    };

    // `_, digits, exponent = d.as_tuple()` and the 200 refusal (`defaultfilters.py:165-177`).
    // **`exponent == None` is two source arms at once** — the sum's `TypeError` and the
    // `int(d)` that raises at `defaultfilters.py:180` (`OverflowError` for an infinity,
    // `ValueError` for a NaN) — both `return input_val`, so it is tested once here and
    // `integral` is never read beside it.
    let exponent = match parsed.exponent {
        Some(exponent) => exponent,
        None => return Ok(input_value(&parsed.input_val)),
    };
    let digit_and_exponent_sum =
        (parsed.digits.len() as u64).saturating_add(exponent.unsigned_abs());
    if digit_and_exponent_sum > 200 {
        return Ok(input_value(&parsed.input_val));
    }

    // `if not m and p <= 0:` (`defaultfilters.py:184-192`) — the integral-value test.
    if parsed.integral && p.is_nonpositive() {
        let number = integer_text(&parsed.digits, exponent, parsed.sign);
        return Ok(formatted(number, 0, &dispatch, call));
    }

    // `d.quantize(...)` (`defaultfilters.py:194-204`), the host's whole, then the assembly
    // (`defaultfilters.py:205-212`) and the marked return (`defaultfilters.py:213-220`).
    let rounded = call
        .host
        .decimal_quantize(&parsed.value, p.magnitude, parsed.integral)?;
    let decimal_pos = i64::try_from(p.magnitude).unwrap_or(i64::MAX);
    Ok(formatted(assemble(&rounded), decimal_pos, &dispatch, call))
}

#[cfg(test)]
mod tests {
    //! **These pin the branch structure and the seam's call order, not the
    //! digits.** `TestFilterHost`'s `number_format` and `mark_safe` ignore their
    //! arguments, so the assembled text, the `decimal_pos` and the `use_l10n` flag are
    //! the corpus's; this is where "which arm produced them" is. Every error path is
    //! host-passed-through, so each asserts the class and that it escapes, not the
    //! message text — except the empty-string argument's Rust-synthesised `IndexError`,
    //! asserted for its class and that no host call was made (the branch fact that the
    //! dispatch precedes the parse).
    use super::*;
    use crate::filters::host::{NoFilterHost, ParsedDecimal, TestFilterHost};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// A parsed decimal with the parts a test needs; `input_val` is the text all
    /// three `return input_val` arms hand back.
    fn parsed(integral: bool, exponent: Option<i64>, digits: &[u8]) -> DecimalParse {
        DecimalParse::Parsed(ParsedDecimal {
            value: Value::Opaque(7),
            input_val: "6666.6666".to_string(),
            sign: false,
            digits: digits.to_vec(),
            exponent,
            integral,
        })
    }

    fn args(decimal: DecimalParse, int: IntValue) -> TestFilterHost {
        TestFilterHost {
            decimal_parse: Some(decimal),
            to_int: Some(int),
            number_format: Some("FORMATTED".to_string()),
            mark_safe: Some(Value::Safe("FORMATTED".into())),
            decimal_quantize: Some(DecimalTuple {
                sign: false,
                digits: vec![7],
                exponent: 0,
                truthy: true,
            }),
            ..TestFilterHost::default()
        }
    }

    /// A whole number at or below zero places takes the integer branch: `to_int`
    /// then `number_format` then `mark_safe`, and **no** `decimal_quantize`.
    #[test]
    fn a_whole_number_takes_the_integer_branch() {
        let h = args(parsed(true, Some(0), &[6, 6, 6, 7]), IntValue::Small(-1));
        assert_eq!(
            run(Value::Float(6666.6666), None, &call(&h)).unwrap(),
            Value::Safe("FORMATTED".into()),
            "the marked answer comes back, not the assembled text"
        );
        assert_eq!(h.calls(), vec!["decimal_parse", "to_int", "number_format"]);
    }

    /// A fractional value takes the quantize path, in the source's order: parse,
    /// `int(arg)`, quantize, `number_format`, `mark_safe`.
    #[test]
    fn a_fractional_value_takes_the_quantize_branch() {
        let h = args(
            parsed(false, Some(-4), &[6, 6, 6, 6, 6, 6, 6, 6]),
            IntValue::Small(-1),
        );
        assert_eq!(
            run(Value::Float(6666.6666), None, &call(&h)).unwrap(),
            Value::Safe("FORMATTED".into())
        );
        assert_eq!(
            h.calls(),
            vec![
                // `mark_safe` of the formatted native string is Rust's
                // (`filters::safety`), so it is no longer a host call.
                "decimal_parse",
                "to_int",
                "decimal_quantize",
                "number_format"
            ]
        );
    }

    /// The **200 boundary is `> 200`**: a sum of exactly 200 formats and 201 refuses
    /// — measured `floatformat(10**199)` is a 200-character `SafeString` and
    /// `floatformat(10**200)` is a plain-`str` `input_val`.
    #[test]
    fn the_two_hundred_boundary_is_exactly_two_hundred() {
        let digits = vec![1u8];
        // `len(digits) + abs(exponent)` == 200: through the integer branch.
        let h = args(parsed(true, Some(199), &digits), IntValue::Small(-1));
        assert_eq!(
            run(Value::Int(1), None, &call(&h)).unwrap(),
            Value::Safe("FORMATTED".into())
        );
        h.assert_called("number_format");

        // 201: `input_val`, and the seam is not asked for anything after `to_int`.
        let h = args(parsed(true, Some(200), &digits), IntValue::Small(-1));
        assert_eq!(
            run(Value::Int(1), None, &call(&h)).unwrap(),
            Value::Str("6666.6666".into())
        );
        assert_eq!(h.calls(), vec!["decimal_parse", "to_int"]);
    }

    /// A non-finite has `exponent == None` and answers `input_val`, **plain**:
    /// `mark_safe` is not reached, which separates `nan`'s `str` from `34.0`'s
    /// `SafeString`.
    #[test]
    fn a_non_finite_answers_the_input_text_unmarked() {
        let h = args(parsed(false, None, &[]), IntValue::Small(-1));
        let result = run(Value::Str("nan".into()), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("6666.6666".into()));
        assert_eq!(result.type_name(), "str");
        assert_eq!(h.calls(), vec!["decimal_parse", "to_int"]);
    }

    /// `Unparseable` is `return ""` at `defaultfilters.py:159`, before `int(arg)` runs, so no
    /// `to_int` is recorded.
    #[test]
    fn an_unparseable_input_answers_the_empty_string() {
        let h = TestFilterHost {
            decimal_parse: Some(DecimalParse::Unparseable),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("abc".into()), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
        assert_eq!(h.calls(), vec!["decimal_parse"]);
    }

    /// `except ValueError: return input_val` — measured
    /// `floatformat(6666.6666, "x") -> '6666.6666'`, a plain `str`. `int(None)`'s
    /// `TypeError` is **not** caught and escapes.
    #[test]
    fn a_value_error_from_the_argument_returns_the_input_text() {
        let h = args(parsed(false, Some(-4), &[1]), IntValue::Small(0));
        h.raise_on("to_int", PyErrorKind::ValueError);
        assert_eq!(
            run(Value::Float(1.5), Some(Value::Str("x".into())), &call(&h)).unwrap(),
            Value::Str("6666.6666".into())
        );
        assert_eq!(h.calls(), vec!["decimal_parse", "to_int"]);
    }

    #[test]
    fn another_class_from_the_argument_escapes() {
        let h = args(parsed(false, Some(-4), &[1]), IntValue::Small(0));
        h.raise_on("to_int", PyErrorKind::TypeError);
        let error = run(Value::Float(1.5), Some(Value::None), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "int(None) must escape: {error:?}"
        );
    }

    /// A refusal from the decimal parse escapes: `defaultfilters.py:155-159` catches exactly the
    /// two classes `decimal_parse` turns into `Unparseable`.
    #[test]
    fn a_parse_refusal_escapes() {
        let h = args(parsed(false, Some(-4), &[1]), IntValue::Small(0));
        h.raise_on("decimal_parse", PyErrorKind::TypeError);
        let error = run(Value::Float(1.5), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "{error:?}"
        );
        assert_eq!(h.calls(), vec!["decimal_parse"]);
    }

    /// `quantize` has no `try` in the source (`defaultfilters.py:204`), so its `InvalidOperation`
    /// at the `scaleb` boundary escapes the filter.
    #[test]
    fn a_quantize_refusal_escapes() {
        let h = args(parsed(false, Some(-4), &[1]), IntValue::Small(2000055));
        h.raise_on(
            "decimal_quantize",
            PyErrorKind::Other("InvalidOperation".into()),
        );
        let error = run(Value::Float(1.5), Some(Value::Int(2000055)), &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::Other("InvalidOperation".into())),
            "{error:?}"
        );
        h.assert_called("decimal_quantize");
        assert!(!h.calls().contains(&"number_format"), "{:?}", h.calls());
    }

    /// An empty-string argument is CPython's `arg[-1]` on an empty `str` and escapes
    /// — measured `floatformat(6666.6666, "")` raises `IndexError: string index out
    /// of range`. The **empty call list** says the dispatch runs before the parse.
    #[test]
    fn an_empty_argument_raises_before_any_host_call() {
        let h = args(parsed(true, Some(0), &[1]), IntValue::Small(-1));
        let error = run(
            Value::Float(6666.6666),
            Some(Value::Str("".into())),
            &call(&h),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::IndexError),
            "{error:?}"
        );
        assert_eq!(h.calls(), Vec::<&str>::new(), "the dispatch is Rust's");
    }

    /// The suffix dispatch is Rust's, so every suffix row reaches the parse with the
    /// suffix already cut — asserted through the *absence* of a refusal for `"2g"`,
    /// which `int()` of the whole text would reject.
    #[test]
    fn a_suffixed_argument_reaches_the_seam_after_the_cut() {
        for arg in ["2g", "2u", "g", "gu", "ug", "u", "-3"] {
            let h = args(parsed(false, Some(-4), &[1]), IntValue::Small(2));
            assert!(
                run(
                    Value::Float(6666.6666),
                    Some(Value::Str(arg.into())),
                    &call(&h)
                )
                .is_ok(),
                "`{arg}` is dispatched in Rust and reaches the seam"
            );
            h.assert_called("decimal_parse");
            h.assert_called("to_int");
        }
    }

    /// A missing argument is the default `-1` (`defaultfilters.py:95`) and still reaches the
    /// seam: measured `floatformat(34.0) -> '34'`.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = args(parsed(true, Some(0), &[3, 4]), IntValue::Small(-1));
        assert_eq!(
            run(Value::Float(34.0), None, &call(&h)).unwrap(),
            Value::Safe("FORMATTED".into())
        );
        h.assert_called("decimal_parse");
        h.assert_called("to_int");
    }

    /// The body needs the seam twice over — the parse and the quantize — so it cannot
    /// compute an answer in Rust and pass a bare shell by luck.
    #[test]
    fn it_needs_a_host() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: true,
        };
        assert!(run(Value::Float(1.5), None, &call).is_err());
        assert!(run(Value::Float(1.5), Some(Value::Int(2)), &call).is_err());
    }
}
