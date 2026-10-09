//! Python's `int()` and `float()` grammar for base-10 text.
//!
//! Rust's `str::parse::<i64>()` is not the same function. Python accepts
//! surrounding whitespace (`int(" 5 ")`), `_` between digits (`int("1_000")`),
//! and — the part no Rust std call can do — **non-ASCII decimal digits**:
//! `int("١٢٣")` is `123` on the pinned tree, and `char::to_digit` is ASCII-only,
//! so `\p{Nd}` needs a table.
//!
//! The table is 760 code points in 71 contiguous ranges, extracted from the
//! pinned CPython's `unicodedata` and pinned by a Python-side guard that
//! compares it exhaustively (Step 8) — the same instrument `pyunicode.rs` uses
//! for the whitespace table, and for the same reason: neither language's table
//! is visible from the other, so only a comparison that runs in both can see one
//! of them move. There are now two such tables and a `cargo update` or a Python
//! upgrade moves either.

/// An integer that may not fit Rust's `i64`.
///
/// Python's `int` is arbitrary-precision, so `{{ 99999999999999999999 }}` is a
/// valid template that renders its own digits. `Big` carries the canonical
/// decimal text and the shell calls `int()` on it; saturating to `i64::MAX`
/// would render a wrong number, which is the failure mode this type exists to
/// prevent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IntValue {
    Small(i64),
    Big(String),
}

/// `\p{Nd}` as `(first, last)` inclusive ranges, ascending and disjoint.
///
/// Generated, not transcribed: the 71 literals below came from
/// `unicodedata.category(chr(c)) == "Nd"` over U+0000..=U+10FFFF on the pinned
/// interpreter (CPython 3.14.4, `unidata_version` 16.0.0), splitting on
/// **contiguity only**. Splitting on "the value also increments by one" as well
/// looks equivalent and is not: it cuts the two fused runs into their constituent
/// alphabets and reports 76 ranges where there are 71.
///
/// The last four lines of that run, which the table cannot show and a reader
/// needs: `ranges: 71`, `codepoints: 760`, `all ranges start at decimal 0: True`,
/// `code points where (cp - first) % 10 != decimal: 0 []`. The last is the
/// licence for [`decimal_digit_value`]'s `% 10`, measured over all 760.
static DECIMAL_DIGIT_RANGES: &[(u32, u32)] = &[
    (0x0030, 0x0039),
    (0x0660, 0x0669),
    (0x06F0, 0x06F9),
    (0x07C0, 0x07C9),
    (0x0966, 0x096F),
    (0x09E6, 0x09EF),
    (0x0A66, 0x0A6F),
    (0x0AE6, 0x0AEF),
    (0x0B66, 0x0B6F),
    (0x0BE6, 0x0BEF),
    (0x0C66, 0x0C6F),
    (0x0CE6, 0x0CEF),
    (0x0D66, 0x0D6F),
    (0x0DE6, 0x0DEF),
    (0x0E50, 0x0E59),
    (0x0ED0, 0x0ED9),
    (0x0F20, 0x0F29),
    (0x1040, 0x1049),
    (0x1090, 0x1099),
    (0x17E0, 0x17E9),
    (0x1810, 0x1819),
    (0x1946, 0x194F),
    (0x19D0, 0x19D9),
    (0x1A80, 0x1A89),
    (0x1A90, 0x1A99),
    (0x1B50, 0x1B59),
    (0x1BB0, 0x1BB9),
    (0x1C40, 0x1C49),
    (0x1C50, 0x1C59),
    (0xA620, 0xA629),
    (0xA8D0, 0xA8D9),
    (0xA900, 0xA909),
    (0xA9D0, 0xA9D9),
    (0xA9F0, 0xA9F9),
    (0xAA50, 0xAA59),
    (0xABF0, 0xABF9),
    (0xFF10, 0xFF19),
    (0x104A0, 0x104A9),
    (0x10D30, 0x10D39),
    (0x10D40, 0x10D49),
    (0x11066, 0x1106F),
    (0x110F0, 0x110F9),
    (0x11136, 0x1113F),
    (0x111D0, 0x111D9),
    (0x112F0, 0x112F9),
    (0x11450, 0x11459),
    (0x114D0, 0x114D9),
    (0x11650, 0x11659),
    (0x116C0, 0x116C9),
    (0x116D0, 0x116E3),
    (0x11730, 0x11739),
    (0x118E0, 0x118E9),
    (0x11950, 0x11959),
    (0x11BF0, 0x11BF9),
    (0x11C50, 0x11C59),
    (0x11D50, 0x11D59),
    (0x11DA0, 0x11DA9),
    (0x11F50, 0x11F59),
    (0x16130, 0x16139),
    (0x16A60, 0x16A69),
    (0x16AC0, 0x16AC9),
    (0x16B50, 0x16B59),
    (0x16D70, 0x16D79),
    (0x1CCF0, 0x1CCF9),
    (0x1D7CE, 0x1D7FF),
    (0x1E140, 0x1E149),
    (0x1E2F0, 0x1E2F9),
    (0x1E4F0, 0x1E4F9),
    (0x1E5F1, 0x1E5FA),
    (0x1E950, 0x1E959),
    (0x1FBF0, 0x1FBF9),
];

/// The value of `c` as a decimal digit, if Python's `int()` would treat it as one.
///
/// `% 10` and not `code - first`: a range is a contiguous run of `Nd` code
/// points, and two of the 71 runs are several digit alphabets fused by
/// adjacency — `U+1D7CE..U+1D7FF` is five of them, and `U+116D0..U+116E3` is two.
/// Measured for all 760 code points; see the table's doc comment.
///
/// `char::to_digit` is not usable here for the reason the table exists: it
/// knows only ASCII (and, at 10 and above, letters).
pub fn decimal_digit_value(c: char) -> Option<u32> {
    let code = c as u32;
    DECIMAL_DIGIT_RANGES
        .iter()
        .find(|(first, last)| (*first..=*last).contains(&code))
        .map(|(first, _)| (code - first) % 10)
}

/// Python's `int(s)` for base 10, as `Some(value)` or `None` where Python
/// raises `ValueError`.
///
/// The grammar, measured rather than recalled: optional surrounding whitespace
/// (Python's `str.isspace()` table — `pyunicode::is_space`, which is *not*
/// Rust's), optional `+`/`-`, then digits separated by at most one `_` with no
/// leading, trailing or doubled `_`.
///
/// Two value forms, because the value may not fit: Python integers are unbounded
/// and `Variable('99999999999999999999')` is a valid literal on the pinned tree.
/// `Small` is the common case and allocates nothing; `Big` carries canonical
/// ASCII digits so the materialiser can hand them to `int()` — never to `eval`,
/// and never by formatting a source string and evaluating it.
pub fn python_int(s: &str) -> Option<IntValue> {
    let core = strip_python_whitespace(s);
    let (negative, digits) = split_sign(core);
    let digits = drop_underscores(digits)?;
    if digits.is_empty() {
        return None;
    }

    // Accumulate in `i128` and fall to the text form on overflow. The length is
    // deliberately not tested first: leading zeros make it a poor predictor
    // (`'000000000000000000005'` is `5`), and the boundary this code has is the
    // one Task 3's parity corpus measures.
    let mut magnitude: i128 = 0;
    let mut too_big = false;
    for c in digits.chars() {
        let digit = decimal_digit_value(c)? as i128;
        match magnitude.checked_mul(10).and_then(|v| v.checked_add(digit)) {
            Some(v) => magnitude = v,
            None => {
                too_big = true;
                break;
            }
        }
    }
    if !too_big {
        let signed = if negative { -magnitude } else { magnitude };
        if let Ok(small) = i64::try_from(signed) {
            return Some(IntValue::Small(small));
        }
    }

    // Canonical form: an ASCII `-` if negative, then ASCII digits, no
    // underscores. Non-ASCII digits are transliterated, which is safe because
    // this string is only ever fed to `int()`: what the materialiser builds is
    // the *value*, not the literal's spelling.
    let mut text = String::with_capacity(digits.len() + 1);
    if negative {
        text.push('-');
    }
    for c in digits.chars() {
        text.push(char::from_digit(decimal_digit_value(c)?, 10).expect("0..=9"));
    }
    Some(IntValue::Big(text))
}

/// Python's `float(s)`, as `Some(value)` or `None` where Python raises
/// `ValueError`.
///
/// Reached only when the token contains `.` or `e`/`E` (`Variable.__init__`'s
/// own condition), which is why `float("inf")` and `float("nan")` are
/// unreachable in a template and are not special-cased here: `"inf"`, `"nan"`
/// and `"Infinity"` all take the *integer* branch and fail there, measured.
pub fn python_float(s: &str) -> Option<f64> {
    let core = strip_python_whitespace(s);
    let (negative, body) = split_sign(core);
    let canonical = canonical_float_digits(body)?;
    let signed = if negative {
        format!("-{canonical}")
    } else {
        canonical
    };
    // Rust's parser converts an already-validated ASCII decimal string. It never
    // makes the *decision*: `f64::from_str` accepts `"inf"`/`"NaN"` spellings,
    // and accepting them here would be accepting something the caller's `e`/`.`
    // precondition already excluded.
    signed.parse::<f64>().ok()
}

/// Python's surrounding whitespace: `str.isspace()`, which is **not** Rust's
/// `char::is_whitespace()` — 29 code points against 25, the M1 divergence.
/// `pyunicode::is_space` is the table that closed it and is what must be used.
fn strip_python_whitespace(s: &str) -> &str {
    s.trim_matches(crate::pyunicode::is_space)
}

/// A leading `+`/`-`, if any, and the rest. A second sign is left in the body,
/// where the digit walk rejects it — which is how `int('+-5')` and
/// `float('--5')` come out as `ValueError` without a special case.
fn split_sign(s: &str) -> (bool, &str) {
    match s.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    }
}

/// Drop `_` separators, rejecting the placements Python rejects: none leading,
/// trailing or doubled.
fn drop_underscores(s: &str) -> Option<String> {
    if s.is_empty() || s.starts_with('_') || s.ends_with('_') || s.contains("__") {
        return None;
    }
    Some(s.chars().filter(|c| *c != '_').collect())
}

/// The ASCII decimal spelling of a validated Python float body, sign already
/// stripped, or `None` where Python raises `ValueError`.
///
/// Underscores are permitted only *between* digits. Measured: `float('1_0.5')`
/// is `10.5`, while `float('1_.5')`, `float('1._5')`, `float('1e_5')` and
/// `float('1_e5')` are all `ValueError`. `.5` and `5.` are both legal, and an
/// exponent must have digits of its own (`float('1e')` is a `ValueError`).
fn canonical_float_digits(s: &str) -> Option<String> {
    let mut out = String::with_capacity(s.len());
    let mut digits = 0usize;
    let mut seen_dot = false;
    let mut seen_exp = false;
    let mut prev_was_digit = false;
    let mut chars = s.chars().peekable();

    while let Some(c) = chars.next() {
        if c == '_' {
            let followed_by_digit = chars
                .peek()
                .is_some_and(|n| decimal_digit_value(*n).is_some());
            if !prev_was_digit || !followed_by_digit {
                return None;
            }
            continue;
        }
        if c == '.' {
            if seen_dot || seen_exp {
                return None;
            }
            seen_dot = true;
            prev_was_digit = false;
            out.push('.');
            continue;
        }
        if c == 'e' || c == 'E' {
            if seen_exp || digits == 0 {
                return None;
            }
            seen_exp = true;
            prev_was_digit = false;
            out.push('e');
            if matches!(chars.peek(), Some('+' | '-')) {
                out.push(chars.next().expect("peeked"));
            }
            continue;
        }
        let digit = decimal_digit_value(c)?;
        out.push(char::from_digit(digit, 10).expect("0..=9"));
        digits += 1;
        prev_was_digit = true;
    }

    // `digits` counts both sides of the point and the exponent, so `'.'` and
    // `'e5'` are both rejected, as Python rejects them.
    (digits > 0).then_some(out)
}

/// Every code point Python's `unicodedata` reports as category `Nd`, ascending.
///
/// Derived from [`DECIMAL_DIGIT_RANGES`] rather than from `char::is_numeric` or
/// any Rust-side Unicode query: the point of exposing it is for a Python test to
/// compare it against `unicodedata`, and a table derived from a *second* source
/// would compare the second source instead of this table. Surrogates drop out
/// because a Rust `char` cannot hold one.
pub fn decimal_digit_codepoints() -> Vec<u32> {
    DECIMAL_DIGIT_RANGES
        .iter()
        .flat_map(|(first, last)| *first..=*last)
        .collect()
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_decimal_digit_table_is_contiguous_ranges_not_a_flat_list() {
        // 760 code points in 71 ranges. The shape is asserted because the guard
        // that compares it against Python compares ranges, and a table that
        // silently became a flat list would still pass that comparison.
        assert_eq!(DECIMAL_DIGIT_RANGES.len(), 71);
        let total: u32 = DECIMAL_DIGIT_RANGES.iter().map(|(a, b)| b - a + 1).sum();
        assert_eq!(total, 760);
        assert!(
            DECIMAL_DIGIT_RANGES.windows(2).all(|w| w[0].1 < w[1].0),
            "ranges overlap or are unsorted"
        );
        // A range is a *contiguous run of Nd code points*, not one digit
        // alphabet. Two of the 71 hold several cycles; see the test below.
        assert_eq!(
            DECIMAL_DIGIT_RANGES
                .iter()
                .filter(|(a, b)| b - a > 9)
                .count(),
            2,
            "the two fused runs are what make `code - first` wrong on its own"
        );
    }

    #[test]
    fn ascii_digits_and_the_non_ascii_ones_python_calls_digits() {
        for c in '0'..='9' {
            assert_eq!(decimal_digit_value(c), Some(c as u32 - '0' as u32));
        }
        // U+0661 ARABIC-INDIC DIGIT ONE — `int('١٢٣') == 123` on the pinned tree.
        assert_eq!(decimal_digit_value('\u{0661}'), Some(1));
        // U+00B2 SUPERSCRIPT TWO is `No`, not `Nd`: `int('²')` is a ValueError
        // in Python and must therefore not be a digit here.
        assert_eq!(decimal_digit_value('\u{00b2}'), None);
        // U+0301 COMBINING ACUTE ACCENT is `Mn`. It is not a digit, and it is
        // also not rejected by the *variable* grammar — see Task 2's class.
        assert_eq!(decimal_digit_value('\u{0301}'), None);
    }

    #[test]
    fn python_int_accepts_what_rust_parse_does_not() {
        assert_eq!(python_int(" 5 "), Some(IntValue::Small(5)));
        assert_eq!(python_int("5\n"), Some(IntValue::Small(5)));
        assert_eq!(python_int("+5"), Some(IntValue::Small(5)));
        assert_eq!(python_int("-5"), Some(IntValue::Small(-5)));
        assert_eq!(python_int("1_000"), Some(IntValue::Small(1000)));
        assert_eq!(
            python_int("\u{0661}\u{0662}\u{0663}"),
            Some(IntValue::Small(123))
        );
        assert_eq!(python_int("\u{0661}_\u{0662}"), Some(IntValue::Small(12)));
    }

    #[test]
    fn python_int_rejects_what_python_rejects() {
        for bad in [
            "_5",
            "5_",
            "1__0",
            "0x10",
            "",
            "5.5",
            "\u{0665}.\u{0665}",
            "1e5",
        ] {
            assert_eq!(python_int(bad), None, "python_int({bad:?})");
        }
    }

    #[test]
    fn ints_past_i64_keep_the_digits_rather_than_saturating() {
        assert_eq!(
            python_int("9223372036854775807"),
            Some(IntValue::Small(9223372036854775807)),
        );
        assert_eq!(
            python_int("9223372036854775808"),
            Some(IntValue::Big("9223372036854775808".to_string())),
        );
        assert_eq!(
            python_int("99999999999999999999"),
            Some(IntValue::Big("99999999999999999999".to_string())),
        );
        // Whitespace and underscores are normalised away, because the shell
        // hands the text to Python's `int()` and Python accepts either — but a
        // canonical form is what the wire carries, so the test says which.
        assert_eq!(python_int(" 1_000 "), Some(IntValue::Small(1000)));
    }

    #[test]
    fn python_float_matches_python_including_infinity_and_the_underscore_rules() {
        assert_eq!(python_float("1.5"), Some(1.5));
        assert_eq!(python_float(".5"), Some(0.5));
        assert_eq!(python_float("1e5"), Some(100000.0));
        assert_eq!(python_float("1.5e3"), Some(1500.0));
        assert_eq!(python_float("1e400"), Some(f64::INFINITY));
        assert_eq!(python_float("\u{0665}.\u{0665}"), Some(5.5));
        assert_eq!(python_float("\u{0661}e\u{0662}"), Some(100.0));
        // The two underscore forms Python rejects even though the digits are
        // fine on both sides: `float('1_e5')` and `float('1e_5')`.
        assert_eq!(python_float("1_e5"), None);
        assert_eq!(python_float("1e_5"), None);
        assert_eq!(python_float("abc"), None);
    }

    #[test]
    fn a_range_may_hold_several_digit_cycles() {
        // The only inputs that distinguish `(code - first) % 10` from
        // `code - first`. Both long ranges are named, and every code point in
        // each is asserted against `unicodedata.decimal`, not against this
        // module.
        //
        // Two of the labels here were wrong when this test was written from the
        // plan's text, and the measurement is what caught them: `U+1D7D8` is the
        // second alphabet's **zero** (`MATHEMATICAL DOUBLE-STRUCK DIGIT ZERO`,
        // `unicodedata.decimal` = 0), not its one — which is the entire point,
        // since `code - first` reads it as 10. Likewise `U+116DA` is
        // `MYANMAR EASTERN PWO KAREN DIGIT ZERO`, not a second Modi cycle (Modi
        // is `U+116C0..U+116C9`, a range of its own).
        let expected_zero = [
            (0x116d0u32, "MYANMAR PAO DIGIT ZERO"),
            (0x116da, "MYANMAR EASTERN PWO KAREN DIGIT ZERO"),
            (0x1d7ce, "MATHEMATICAL BOLD DIGIT ZERO"),
            (0x1d7d8, "MATHEMATICAL DOUBLE-STRUCK DIGIT ZERO"),
            (0x1d7e2, "MATHEMATICAL SANS-SERIF DIGIT ZERO"),
        ];
        for (code, name) in expected_zero {
            let c = char::from_u32(code).unwrap();
            assert_eq!(
                decimal_digit_value(c),
                Some(0),
                "U+{code:04X} ({name}) should read 0; `code - first` reads it as {}",
                code - 0x1d7ce,
            );
        }
        // The last code point of the five-alphabet run: `(cp - first) % 10`
        // reads 9, and `cp - first` reads 49.
        assert_eq!(decimal_digit_value('\u{1d7ff}'), Some(9));

        let all: String = (0x1d7ceu32..0x1d800)
            .map(|c| char::from_u32(c).unwrap())
            .map(|c| char::from_digit(decimal_digit_value(c).unwrap(), 10).unwrap())
            .collect();
        assert_eq!(all, "0123456789".repeat(5));
    }
}
