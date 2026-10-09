//! `floatformat` — `defaultfilters.py:94-220`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def floatformat(text, arg=-1):
//!     """
//!     Display a float to a specified number of decimal places.
//!
//!     If called without an argument, display the floating point number with one
//!     decimal place -- but only if there's a decimal place to be displayed:
//!
//!     * num1 = 34.23234
//!     * num2 = 34.00000
//!     * num3 = 34.26000
//!     * {{ num1|floatformat }} displays "34.2"
//!     * {{ num2|floatformat }} displays "34"
//!     * {{ num3|floatformat }} displays "34.3"
//!
//!     If arg is positive, always display exactly arg number of decimal places:
//!
//!     * {{ num1|floatformat:3 }} displays "34.232"
//!     * {{ num2|floatformat:3 }} displays "34.000"
//!     * {{ num3|floatformat:3 }} displays "34.260"
//!
//!     If arg is negative, display arg number of decimal places -- but only if
//!     there are places to be displayed:
//!
//!     * {{ num1|floatformat:"-3" }} displays "34.232"
//!     * {{ num2|floatformat:"-3" }} displays "34"
//!     * {{ num3|floatformat:"-3" }} displays "34.260"
//!
//!     If arg has the 'g' suffix, force the result to be grouped by the
//!     THOUSAND_SEPARATOR for the active locale. When the active locale is
//!     en (English):
//!
//!     * {{ 6666.6666|floatformat:"2g" }} displays "6,666.67"
//!     * {{ 10000|floatformat:"g" }} displays "10,000"
//!
//!     If arg has the 'u' suffix, force the result to be unlocalized. When the
//!     active locale is pl (Polish):
//!
//!     * {{ 66666.6666|floatformat:"2" }} displays "66666,67"
//!     * {{ 66666.6666|floatformat:"2u" }} displays "66666.67"
//!
//!     If the input float is infinity or NaN, display the string representation
//!     of that value.
//!     """
//!     force_grouping = False
//!     use_l10n = True
//!     if isinstance(arg, str):
//!         last_char = arg[-1]
//!         if arg[-2:] in {"gu", "ug"}:
//!             force_grouping = True
//!             use_l10n = False
//!             arg = arg[:-2] or -1
//!         elif last_char == "g":
//!             force_grouping = True
//!             arg = arg[:-1] or -1
//!         elif last_char == "u":
//!             use_l10n = False
//!             arg = arg[:-1] or -1
//!     try:
//!         input_val = str(text)
//!         d = Decimal(input_val)
//!     except InvalidOperation:
//!         try:
//!             d = Decimal(str(float(text)))
//!         except (ValueError, InvalidOperation, TypeError):
//!             return ""
//!     try:
//!         p = int(arg)
//!     except ValueError:
//!         return input_val
//!
//!     _, digits, exponent = d.as_tuple()
//!     try:
//!         number_of_digits_and_exponent_sum = len(digits) + abs(exponent)
//!     except TypeError:
//!         # Exponent values can be "F", "n", "N".
//!         number_of_digits_and_exponent_sum = 0
//!
//!     # Values with more than 200 digits, or with a large exponent, are returned
//!     # "as is" to avoid high memory consumption and potential denial-of-service
//!     # attacks. The cut-off of 200 is consistent with
//!     # django.utils.numberformat.floatformat().
//!     if number_of_digits_and_exponent_sum > 200:
//!         return input_val
//!
//!     try:
//!         m = int(d) - d
//!     except (ValueError, OverflowError, InvalidOperation):
//!         return input_val
//!
//!     if not m and p <= 0:
//!         return mark_safe(
//!             formats.number_format(
//!                 "%d" % (int(d)),
//!                 0,
//!                 use_l10n=use_l10n,
//!                 force_grouping=force_grouping,
//!             )
//!         )
//!
//!     exp = Decimal(1).scaleb(-abs(p))
//!     # Set the precision high enough to avoid an exception (#15789).
//!     tupl = d.as_tuple()
//!     units = len(tupl[1])
//!     units += -tupl[2] if m else tupl[2]
//!     prec = abs(p) + units + 1
//!     prec = max(getcontext().prec, prec)
//!
//!     # Avoid conversion to scientific notation by accessing `sign`, `digits`,
//!     # and `exponent` from Decimal.as_tuple() directly.
//!     rounded_d = d.quantize(exp, ROUND_HALF_UP, Context(prec=prec))
//!     sign, digits, exponent = rounded_d.as_tuple()
//!     digits = [str(digit) for digit in reversed(digits)]
//!     while len(digits) <= abs(exponent):
//!         digits.append("0")
//!     digits.insert(-exponent, ".")
//!     if sign and rounded_d:
//!         digits.append("-")
//!     number = "".join(reversed(digits))
//!     return mark_safe(
//!         formats.number_format(
//!             number,
//!             abs(p),
//!             use_l10n=use_l10n,
//!             force_grouping=force_grouping,
//!         )
//!     )
//! ```
//!
//! **The seam runs between the control flow and the decimal engine, and that
//! line is §D2's rule read literally:** "what a `decimal.Context` is set to" is
//! not a function of the filter's arguments, and `defaultfilters.py:200` reads it —
//! `prec = max(getcontext().prec, prec)` — three lines after `defaultfilters.py:194`'s
//! `Decimal(1).scaleb(-abs(p))`. The context is *observable* through the answer,
//! not merely consulted: below `2 * (Emax + prec)` the `scaleb` silently clamps
//! to `Decimal("0E-999999")`, so the rounding position is `999999` whatever `p`
//! is, and above it the same call raises `InvalidOperation` out of a `try` that
//! does not exist. Measured end to end, `floatformat(1.5, 2000054)` is 2000056
//! characters at the default `prec=28` and raises `InvalidOperation` at
//! `prec=1`. So the parse, the `scaleb`, the `prec` and the `quantize` cross the
//! seam whole ([`FilterHost::decimal_parse`], [`FilterHost::decimal_quantize`]);
//! what stays here is the `arg`-suffix dispatch, the 200 refusal, the
//! `not m and p <= 0` branch, the digit assembly and the `.` insertion.
//!
//! Three measurements that fall out of that split, one of them a correction to
//! the plan:
//!
//! * **`arg[-1]` runs before any parse, on a `str` argument, and an empty one is
//!   a CPython `IndexError` that escapes.** Measured `floatformat(6666.6666,
//!   "")` raises `IndexError: string index out of range` — so a body guarding
//!   with `is_empty()` would answer where Django raises. The dispatch is Rust's
//!   because it is a function of the argument, exactly as `str(value)[-arg]` is
//!   Rust's in `get_digit.rs:108`, and [`index_out_of_range`] synthesises the
//!   error for the same reason `get_digit`'s `invalid_literal` synthesises
//!   `int("-")`: no seam method is asked. **A correction to the plan:** the
//!   claim that 199 zeros "formats to a `SafeString` 203 chars long" is
//!   arithmetically off — measured, `floatformat(10**199)` is a `SafeString` of
//!   **200** characters (`len(digits) + abs(exponent)` is `1 + 199 = 200`), and
//!   `floatformat(10**200)` is the refusal, a plain `str` of 201 characters. The
//!   boundary itself is exactly 200, as the plan says.
//! * **`int(arg)`'s `ValueError` is `return input_val`, and it is a plain
//!   `str`.** Measured `floatformat(6666.6666, "x") -> '6666.6666'` and
//!   `floatformat(6666.6666, "2.5") -> '6666.6666'`, both `type() is str`. The
//!   suffix rows: `"2g" -> '6,666.67'`, `"2u" -> '6666.67'`, `"g" -> '6,666.7'`,
//!   `"gu"`/`"ug"`/`"u" -> '6666.7'`, `"0" -> '6667'`, `"20" ->
//!   '6666.66660000000000000000'`.
//! * **`inf`/`-inf`/`nan` do not raise here; they take the `int(d)` arm and come
//!   back as `input_val`.** Measured `floatformat(inf) -> 'inf'`,
//!   `floatformat(-inf) -> '-inf'`, `floatformat(nan) -> 'nan'`, all plain
//!   `str`. `defaultfilters.py:181` catches `OverflowError`, so `int(Decimal("Infinity"))`'s
//!   `OverflowError: cannot convert float infinity to integer` never escapes —
//!   which is why both non-finites and the sum-`0` non-finite exponent reach the
//!   same `return input_val` arm. `"abc"` and `None` are the other arm:
//!   `Unparseable`, and `""`.
//!
//! The remaining measured subtleties, one bullet per source line that would
//! otherwise be guessed:
//!
//! * **The integer branch is the *integral-value* test, not `exponent < 0`**
//!   (`defaultfilters.py:184`). `not m and p <= 0` asks "is the value a whole number", and
//!   `Decimal("1.0")` has a falsy `m` with `exponent == -1`: measured
//!   `floatformat(1.0, -1) -> '1'` and `floatformat("1.0", -1) -> '1'` where an
//!   exponent test takes the quantize path. `floatformat(34.0) -> '34'` is the
//!   same arm with the default `arg=-1`, and `floatformat("34.26000", "-3") ->
//!   '34.260'` is the quantize path taken because the value is *not* whole.
//! * **Rounding is `ROUND_HALF_UP` on the exact decimal** (`defaultfilters.py:204`), not
//!   banker's: measured `floatformat(0.5, 0) -> '1'` and
//!   `floatformat(2.5, 0) -> '3'`, where `round(2.5)` is `2`.
//! * **The sign is decided on the value *after* the quantization** (`defaultfilters.py:210`,
//!   `if sign and rounded_d`), on a different question from `m`'s. Measured
//!   `floatformat("-0.0001", 2) -> '0.00'` with the minus **dropped** (the
//!   rounded `Decimal("-0.00")` is falsy) and `floatformat("-0.5", 0) -> '-1'`
//!   with it **kept** — which is why [`DecimalTuple`] carries `truthy` beside
//!   the parse's `integral`.
//! * **`mark_safe` is in the body, on the two normal returns only** (`defaultfilters.py:185`,
//!   `defaultfilters.py:213`). Measured `floatformat(34.0) -> '34'` is a `SafeString` while
//!   `floatformat(nan) -> 'nan'` and `floatformat(1.5, "abc") -> '1.5'` are
//!   plain `str`. The three `return input_val` arms build a new
//!   `Value::Str` and never reach the seam's `mark_safe`.
//! * **The assembled text is built by repetition, never by a Rust format
//!   spec.** `format!("{:.*}", p, x)` panics for a runtime precision past
//!   `u16` (65535) where Django answers — `floatformat(6666.6666, "20")` is 25
//!   characters and `{{ x|floatformat:70000 }}` is a legal template whose
//!   precision is a `u64` here, refused only by the host's `scaleb` clamp. So
//!   [`integer_text`] and [`assemble`] append digits.

use crate::filters::host::{DecimalParse, DecimalTuple, FilterCall, NumberFormatArgs};
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `except ValueError:` on `int(arg)` (`defaultfilters.py:162`) — the one class that arm names.
/// A `TypeError` (`int(None)`, measured as `floatformat(34.0, None)`'s
/// `int() argument must be … not 'NoneType'`) is not caught and escapes.
fn is_value_error(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host) if host.kind == PyErrorKind::ValueError
    )
}

/// CPython's `arg[-1]` on an empty `str` — the read `defaultfilters.py:141` performs before any
/// parse, which the dispatch has no `try` around.
///
/// Synthesised here because no [`FilterHost`] method is asked for it: the
/// dispatch is a function of the argument, so it stays Rust — the same line
/// `get_digit.rs:108` draws for `str(value)[-arg]`, and the same reason
/// `get_digit.rs:153` synthesises `int("-")`. `operation` names the CPython
/// operation this stands in for, not a method that ran.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn index_out_of_range() -> Error {
    Error::filter_host(
        PyErrorKind::IndexError,
        "str.__getitem__",
        "string index out of range",
    )
}

/// `defaultfilters.py:138-151`'s whole output: the `arg` the rest of the body sees, and the two
/// flags both `number_format` calls take.
struct Dispatch {
    /// `arg` with its suffix cut — `arg[:-2] or -1`, `arg[:-1] or -1` — or the
    /// argument unchanged when it is not a `str`.
    arg: Value,
    /// `force_grouping` (`defaultfilters.py:143`, `defaultfilters.py:147`).
    force_grouping: bool,
    /// `use_l10n` (`defaultfilters.py:139`, `defaultfilters.py:144`, `defaultfilters.py:150`),
    /// which starts `True` and is passed to `number_format` as-is — `Some(false)` for the
    /// `u`/`gu`/`ug` suffixes.
    use_l10n: bool,
}

/// `arg[:-n] or -1` (`defaultfilters.py:145`, `defaultfilters.py:148`, `defaultfilters.py:151`):
/// the prefix, or the `-1` the `or` supplies when cutting the suffix leaves nothing.
///
/// The result is a plain `Value::Str` even when the argument was a `SafeString`;
/// it is only ever fed to `int()`, which cannot see the marker.
fn strip_suffix(text: &str, n: usize) -> Value {
    let keep = text.chars().count().saturating_sub(n);
    let kept: String = text.chars().take(keep).collect();
    if kept.is_empty() {
        Value::Int(-1)
    } else {
        Value::Str(kept.into())
    }
}

/// `defaultfilters.py:140-151`. `arg` is read as a `Value` and not through `to_str` because its
/// *type* is the branch: a non-`str` argument skips the whole block, so
/// `floatformat(1.5, 2)` reaches `int(2)` where `floatformat(1.5, "2.5")`
/// reaches `input_val`.
fn dispatch_suffix(arg: Option<Value>) -> Result<Dispatch, Error> {
    let mut dispatch = Dispatch {
        arg: arg.unwrap_or(Value::Int(-1)),
        force_grouping: false,
        use_l10n: true,
    };
    if let Value::Str(text) | Value::Safe(text) = &dispatch.arg {
        // `arg[-1]`, then `arg[-2:]`, both **code points**: `str.__getitem__`
        // and `str.__getslice__` count characters, which Rust's `chars()` does.
        let mut tail = text.chars().rev();
        let last_char = tail.next().ok_or_else(index_out_of_range)?;
        let second_last = tail.next();
        match (last_char, second_last) {
            // `arg[-2:] in {"gu", "ug"}` — read from the right, so `"gu"` arrives
            // as `('u', Some('g'))`. The `or -1` is inside [`strip_suffix`].
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

/// `int(arg)` (`defaultfilters.py:161`) reduced to the two questions the rest of the body asks of
/// it — `p <= 0` (`defaultfilters.py:184`) and `abs(p)` (`defaultfilters.py:194`).
///
/// It is not an `i64` because Python's `int` is arbitrary-precision and
/// `p <= 0` is answerable for a value that does not fit — measured
/// `floatformat(1.0, "-99999999999999999999999")` takes the integer branch and
/// answers `'1'`. A `Big` never reaches `i64::try_from(...).unwrap()` and so
/// cannot panic; only its *magnitude* saturates, and only for a quantize, where
/// the host's `scaleb` refuses first.
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
/// Only reached when [`ParsedDecimal::integral`] holds, i.e. `int(d) - d` is
/// zero, so `int(d)` truncating toward zero is `d` itself and the
/// digits/exponent pair spells it exactly. `digits` is most-significant first.
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
/// The source's `digits.insert(-exponent, ".")` is `insert(abs(exponent), ".")`
/// here because the value it indexes is the *quantized* tuple's exponent, which
/// is the target `exp`'s: `-abs(p)`, never positive. The sign is [`sign`] **and
/// [`truthy`]** — the `if sign and rounded_d` of `defaultfilters.py:210`, whose second half is
/// the value after rounding, not the parse's `m`.
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

/// The `return input_val` arms' value: `str(text)` (`defaultfilters.py:153`), a plain `str` and
/// never `mark_safe`d — measured `floatformat("x"-argument)` is `type() is str`
/// while `floatformat(34.0)` is a `SafeString`.
fn input_value(input_val: &str) -> Value {
    Value::Str(input_val.into())
}

/// `mark_safe(formats.number_format(number, decimal_pos, use_l10n=…, force_grouping=…))` — the
/// shape both normal returns have (`defaultfilters.py:185` and `defaultfilters.py:213`).
///
/// The operand is a **string**, not an `f64`: `defaultfilters.py:187` hands over `"%d" % int(d)`
/// and `defaultfilters.py:215` hands over the assembled `number`, which is [`NumberFormatArgs`]'s
/// documented str case.
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
    call.host.mark_safe(Value::Str(text.into()))
}

/// `floatformat`'s body: the Rust dispatch, the host's decimal parse, the three
/// `return input_val` arms in source order, and the two normal branches.
///
/// `arg.unwrap_or(Value::Int(-1))` is `defaultfilters.py:95`'s default, and it applies only to a
/// **missing** argument: `{{ x|floatformat:None }}` is `int(None)`'s `TypeError`,
/// measured. **The FFI cannot currently tell the two apart** — it maps an
/// explicit `None` argument to the same `Option::None` it uses for an absent one
/// (`template-py/src/filters.rs:1364-1367`) — so that one row answers the default
/// here instead of raising. A body cannot see the difference; the fix is the
/// FFI's, and `Some(Value::None)` already reaches `to_int(Value::None)` → the
/// escaping `TypeError` the moment it is sent. Reported, not worked around.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `defaultfilters.py:140-151` first, before any parse: an empty-string argument raises here.
    let dispatch = dispatch_suffix(arg)?;

    // `try: input_val = str(text); d = Decimal(input_val)` — `defaultfilters.py:152-159`, whose
    // double failure is `return ""`.
    let parsed = match call.host.decimal_parse(&value)? {
        DecimalParse::Parsed(parsed) => parsed,
        DecimalParse::Unparseable => return Ok(Value::Str("".into())),
    };

    // `p = int(arg)` (`defaultfilters.py:161`), with `except ValueError: return input_val`. A
    // `TypeError` `int(None)` is not caught and escapes.
    let p = match call.host.to_int(&dispatch.arg) {
        Ok(int) => Argument::new(int),
        Err(error) if is_value_error(&error) => return Ok(input_value(&parsed.input_val)),
        Err(error) => return Err(error),
    };

    // `_, digits, exponent = d.as_tuple()` and the 200 refusal (`defaultfilters.py:165-177`).
    // **`exponent == None` is two source arms at once**: the `TypeError` the sum
    // catches, and the `int(d)` that raises at `defaultfilters.py:180` (`OverflowError` for an
    // infinity, `ValueError` for a NaN) — both `return input_val`. So it is
    // tested once, here, and `integral` is never read beside it.
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

    // `d.quantize(...)` (`defaultfilters.py:194-204`), which is the host's whole, and then the
    // assembly (`defaultfilters.py:205-212`) and the marked return (`defaultfilters.py:213-220`).
    let rounded = call
        .host
        .decimal_quantize(&parsed.value, p.magnitude, parsed.integral)?;
    let decimal_pos = i64::try_from(p.magnitude).unwrap_or(i64::MAX);
    Ok(formatted(assemble(&rounded), decimal_pos, &dispatch, call))
}

#[cfg(test)]
mod tests {
    //! **These pin the branch structure and the seam's call order, not the
    //! digits.** `TestFilterHost`'s `number_format` and `mark_safe` return
    //! scripted answers and ignore their arguments, so neither the assembled
    //! text, the `decimal_pos` nor the `use_l10n` flag can be observed from a
    //! body test — the end-to-end rows in `tests/filter_cases.py` are where the
    //! digits are checked, and this is where "which arm produced them" is.
    //!
    //! **Every error path here is host-passed-through**, so each asserts the
    //! class and that it escapes, and no message text: the message belongs to
    //! CPython and the corpus's `ERROR_CASES` pins it. The empty-string
    //! argument's `IndexError` is the one exception in kind — it is synthesised
    //! in Rust (`index_out_of_range`) — and it is asserted for its **class and
    //! the fact that no host call was made**, which is the branch fact that the
    //! dispatch precedes the parse. Its text is `ERROR_CASES`'s alone.
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
            mark_safe: Some(Value::Safe("MARKED".into())),
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
    /// then `number_format` then `mark_safe`, and **no** `decimal_quantize` —
    /// which is what the plan's "a body testing the exponent would take the
    /// quantize path here" would show up as.
    #[test]
    fn a_whole_number_takes_the_integer_branch() {
        let h = args(parsed(true, Some(0), &[6, 6, 6, 7]), IntValue::Small(-1));
        assert_eq!(
            run(Value::Float(6666.6666), None, &call(&h)).unwrap(),
            Value::Safe("MARKED".into()),
            "the marked answer comes back, not the assembled text"
        );
        assert_eq!(
            h.calls(),
            vec!["decimal_parse", "to_int", "number_format", "mark_safe"]
        );
    }

    /// A fractional value takes the quantize path, and the recorded order is the
    /// source's: parse, `int(arg)`, quantize, `number_format`, `mark_safe`.
    #[test]
    fn a_fractional_value_takes_the_quantize_branch() {
        let h = args(
            parsed(false, Some(-4), &[6, 6, 6, 6, 6, 6, 6, 6]),
            IntValue::Small(-1),
        );
        assert_eq!(
            run(Value::Float(6666.6666), None, &call(&h)).unwrap(),
            Value::Safe("MARKED".into())
        );
        assert_eq!(
            h.calls(),
            vec![
                "decimal_parse",
                "to_int",
                "decimal_quantize",
                "number_format",
                "mark_safe"
            ]
        );
    }

    /// The **200 boundary is `> 200`**, so a sum of exactly 200 formats and 201
    /// refuses — measured `floatformat(10**199)` is a `SafeString` of 200
    /// characters and `floatformat(10**200)` is `input_val`, a plain `str`.
    #[test]
    fn the_two_hundred_boundary_is_exactly_two_hundred() {
        let digits = vec![1u8];
        // `len(digits) + abs(exponent)` == 200: through the integer branch.
        let h = args(parsed(true, Some(199), &digits), IntValue::Small(-1));
        assert_eq!(
            run(Value::Int(1), None, &call(&h)).unwrap(),
            Value::Safe("MARKED".into())
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
    /// `mark_safe` is not reached, which is what separates `floatformat(nan)`'s
    /// `str` from `floatformat(34.0)`'s `SafeString`.
    #[test]
    fn a_non_finite_answers_the_input_text_unmarked() {
        let h = args(parsed(false, None, &[]), IntValue::Small(-1));
        let result = run(Value::Str("nan".into()), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("6666.6666".into()));
        assert_eq!(result.type_name(), "str");
        assert_eq!(h.calls(), vec!["decimal_parse", "to_int"]);
    }

    /// `Unparseable` is `return ""` at `defaultfilters.py:159`, before `int(arg)` ever runs — so
    /// the argument is not examined and no `to_int` is recorded.
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
    /// `floatformat(6666.6666, "x") -> '6666.6666'`, a plain `str`. A different
    /// class is **not** caught: `int(None)` is a `TypeError` and escapes.
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
    /// two classes `decimal_parse` itself turns into `Unparseable`, so anything
    /// else is the host's to raise.
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

    /// `quantize` is not inside a `try` in the source (`defaultfilters.py:204`), so its
    /// `InvalidOperation` at the `scaleb` boundary escapes the filter.
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

    /// An empty-string argument is CPython's `arg[-1]` on an empty `str` and
    /// escapes — measured `floatformat(6666.6666, "")` raises `IndexError:
    /// string index out of range`. A body guarding with `is_empty()` answers
    /// where Django raises, and the **empty call list** is what says the
    /// dispatch runs before the parse.
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

    /// The suffix dispatch is Rust's, so every suffix row reaches the parse with
    /// the suffix already cut — asserted here through the *absence* of a
    /// refusal for a string argument that `int()` of the whole text would
    /// reject, `"2g"`.
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
    /// seam: `{{ x|floatformat }}` is measured `floatformat(34.0) -> '34'`.
    #[test]
    fn a_missing_argument_still_reaches_the_seam() {
        let h = args(parsed(true, Some(0), &[3, 4]), IntValue::Small(-1));
        assert_eq!(
            run(Value::Float(34.0), None, &call(&h)).unwrap(),
            Value::Safe("MARKED".into())
        );
        h.assert_called("decimal_parse");
        h.assert_called("to_int");
    }

    /// The body needs the seam twice over — the parse and the quantize — so it
    /// cannot compute an answer in Rust and pass a bare shell by luck.
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
