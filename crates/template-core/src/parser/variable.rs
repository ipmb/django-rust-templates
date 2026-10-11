//! `Variable.__init__` — `base.py:850-925`.
//!
//! The float branch **assigns before it raises**, so `Variable("2.")` keeps
//! `literal=2.0` *and* `lookups=("2", "")`: observable through the slot even
//! though `resolve` branches on `lookups`. Do not raise before assigning.
//!
//! Measured details a tidy port gets wrong:
//! - `unescape_string_literal` accepts a **lone quote** — `s[0]` and `s[-1]` are
//!   the same character on a one-character string, so `s[1:-1]` is empty and
//!   `Variable('"').literal` is `SafeString('')`. Rust needs an explicit length
//!   guard: `&s[1..s.len() - 1]` **panics** at `len == 1`.
//! - the underscore rule is tested *before* the `+`/`-` rules, so `_.` is an
//!   underscore error, not an "Invalid character ('.')" one.
//! - `3.e5` is a number: the trailing-dot rejection fires only on a **final**
//!   `.`, independently of the `e` branch.
//!
//! Django's fall-through indexes `var[0]`, so an empty name raises `IndexError`;
//! unreachable from a template (the parser rejects empty `{{ }}`, and
//! `FilterExpression`'s `var` group `[\w\.\+-]+` cannot match `_()`), so this
//! reports `ErrorKind::Internal` instead.

use crate::parser::desc::{AttrValue, VariableDesc};
use crate::parser::error::Error;
use crate::pynum::{python_float, python_int, IntValue};

/// Django's `VARIABLE_ATTRIBUTE_SEPARATOR` (`base.py:60`).
const ATTRIBUTE_SEPARATOR: char = '.';

/// `Variable.__init__`, as a descriptor. Takes the raw token rather than a
/// `Token`: the caller has already decided this is a variable.
pub fn parse_variable(raw: &str) -> Result<VariableDesc, Error> {
    // The working copy the marker strip rewrites; the field keeps the original.
    let mut text = raw.to_string();
    let mut literal = None;
    let mut lookups = None;
    let mut translate = false;

    // `"." in var or "e" in var.lower()`: the code points whose lowercase is `e`
    // are exactly `{'e', 'E'}` (measured), so two `contains` calls match the fold.
    let numeric = if text.contains('.') || text.contains('e') || text.contains('E') {
        // Assign, then check the trailing dot: Django converts first and raises
        // second, so `2.` keeps the `2.0` literal *and* gets its lookups. Do not
        // merge these into one condition.
        let parsed = python_float(&text);
        literal = parsed.map(AttrValue::Float);
        parsed.is_some() && !text.ends_with('.')
    } else {
        match python_int(&text) {
            Some(IntValue::Small(value)) => {
                literal = Some(AttrValue::Int(value));
                true
            }
            Some(IntValue::Big(digits)) => {
                literal = Some(AttrValue::BigInt(digits));
                true
            }
            None => false,
        }
    };

    if !numeric {
        // `var[0:2] == "_(" and var[-1] == ")"`; both are one byte, so the slice
        // is exact and `_(` alone is simply not a marker rather than an error.
        if text.starts_with("_(") && text.ends_with(')') {
            translate = true;
            text = text[2..text.len() - 1].to_string();
        }

        match unescape_string_literal(&text) {
            Some(value) => literal = Some(AttrValue::SafeStr(value)),
            None => {
                // Django reads `var[0]` (`base.py:910`), raising `IndexError`;
                // unreachable from a template (module doc), so `Internal`, not a
                // template claim.
                if text.is_empty() {
                    return Err(Error::internal(
                        "an empty variable name reached parse_variable".to_string(),
                    ));
                }
                // `sep + "_" in var or var[0] == "_"`, tested *before* the
                // character rules: `_.` is an underscore error, not "Invalid character".
                if text.contains("._") || text.starts_with('_') {
                    return Err(Error::syntax(format!(
                        "Variables and attributes may not begin with underscores: '{text}'"
                    )));
                }
                // `for c in ["+", "-"]`: `+` wins whenever present anywhere.
                for c in ['+', '-'] {
                    if text.contains(c) {
                        return Err(Error::syntax(format!(
                            "Invalid character ('{c}') in variable name: '{text}'"
                        )));
                    }
                }
                // Python's `str.split(".")` keeps empty fields (`"."` is `("", "")`);
                // Rust's agrees on a non-empty string, hence the emptiness check above.
                lookups = Some(
                    text.split(ATTRIBUTE_SEPARATOR)
                        .map(str::to_string)
                        .collect(),
                );
            }
        }
    }

    Ok(VariableDesc {
        var: raw.to_string(),
        literal,
        lookups,
        translate,
    })
}

/// `django.utils.text.unescape_string_literal` — `Some` where Python would not
/// raise `ValueError`.
///
/// The replacements are ordered — escaped quote first, doubled backslash second
/// — hence `"a \"bc\""` is `a "bc"`. A lone quote is a valid empty literal:
/// first and last **character** are compared, not that there are two.
pub fn unescape_string_literal(s: &str) -> Option<String> {
    let quote = s.chars().next()?;
    if quote != '"' && quote != '\'' {
        return None;
    }
    if !s.ends_with(quote) {
        return None;
    }
    // Django's `s[1:-1]` is empty on a one-character string, but `&s[1..s.len()-1]`
    // **panics** at `len == 1`; both delimiters are one byte, so the arithmetic is exact.
    let inner = if s.len() >= 2 { &s[1..s.len() - 1] } else { "" };
    let unescaped_quote = inner.replace(&format!("\\{quote}"), &quote.to_string());
    Some(unescaped_quote.replace("\\\\", "\\"))
}

#[cfg(test)]
mod tests {
    use super::*;
    // Imported here, not in the module header: the implementation only ever
    // *constructs* an `Error`, so the kind would be unused under `-D warnings`.
    use crate::parser::error::ErrorKind;

    /// Parse a token that must succeed.
    fn var(raw: &str) -> VariableDesc {
        parse_variable(raw).unwrap_or_else(|error| panic!("{raw:?} failed: {:?}", error.kind))
    }

    /// The kind of the failure `raw` must produce.
    fn failure(raw: &str) -> ErrorKind {
        parse_variable(raw)
            .err()
            .unwrap_or_else(|| panic!("{raw:?} unexpectedly parsed as {raw}"))
            .kind
    }

    fn syntax(message: String) -> ErrorKind {
        ErrorKind::Syntax(message)
    }

    /// `Variable.lookups` for a token that is a real variable.
    fn lookups(names: &[&str]) -> Option<Vec<String>> {
        Some(names.iter().map(|name| (*name).to_string()).collect())
    }

    fn safe(text: &str) -> Option<AttrValue> {
        Some(AttrValue::SafeStr(text.to_string()))
    }

    /// One row of [`djangos_measured_table_of_literal_lookups_and_translate`]; a
    /// named alias for `clippy::type_complexity`, as `pyregex.rs`'s `WordRange` is.
    type Row = (&'static str, Option<AttrValue>, Option<Vec<String>>, bool);

    #[test]
    fn djangos_measured_table_of_literal_lookups_and_translate() {
        // Every row was produced by Django 6.0.9's own `Variable` on the pinned
        // checkout; the dull-looking rows are the ones intuition gets wrong.
        let cases: Vec<Row> = vec![
            ("abc", None, lookups(&["abc"]), false),
            ("a.b.c", None, lookups(&["a", "b", "c"]), false),
            ("0x10", None, lookups(&["0x10"]), false),
            (".", None, lookups(&["", ""]), false),
            ("..", None, lookups(&["", "", ""]), false),
            ("5.5.5", None, lookups(&["5", "5", "5"]), false),
            ("5_", None, lookups(&["5_"]), false),
            ("1__0", None, lookups(&["1__0"]), false),
            (
                r#"a.b|default:"x""#,
                None,
                lookups(&["a", r#"b|default:"x""#]),
                false,
            ),
            (r#""""#, safe(""), None, false),
            (r#""quoted""#, safe("quoted"), None, false),
            ("'q'", safe("q"), None, false),
            (r#"_("x")"#, safe("x"), None, true),
            (r#"_( "x" )"#, None, lookups(&[" \"x\" "]), true),
            (r#"_('x')"#, safe("x"), None, true),
            ("5", Some(AttrValue::Int(5)), None, false),
            (" 5 ", Some(AttrValue::Int(5)), None, false),
            ("+5", Some(AttrValue::Int(5)), None, false),
            ("5\n", Some(AttrValue::Int(5)), None, false),
            ("-5", Some(AttrValue::Int(-5)), None, false),
            (
                "\u{0661}\u{0662}\u{0663}",
                Some(AttrValue::Int(123)),
                None,
                false,
            ),
            ("1_000", Some(AttrValue::Int(1000)), None, false),
            (
                "9223372036854775807",
                Some(AttrValue::Int(i64::MAX)),
                None,
                false,
            ),
            (
                "9223372036854775808",
                Some(AttrValue::BigInt("9223372036854775808".to_string())),
                None,
                false,
            ),
            (
                "99999999999999999999",
                Some(AttrValue::BigInt("99999999999999999999".to_string())),
                None,
                false,
            ),
            ("1.5", Some(AttrValue::Float(1.5)), None, false),
            (".5", Some(AttrValue::Float(0.5)), None, false),
            ("1e5", Some(AttrValue::Float(100_000.0)), None, false),
            ("1E5", Some(AttrValue::Float(100_000.0)), None, false),
            ("1.5e3", Some(AttrValue::Float(1500.0)), None, false),
            ("1e400", Some(AttrValue::Float(f64::INFINITY)), None, false),
            // `float` accepts `1e400` (infinity); `nan`/`inf` never reach the float
            // branch (no `.`, no `e`/`E`), so they are plain variables (`pynum::python_float`).
            ("inf", None, lookups(&["inf"]), false),
            ("nan", None, lookups(&["nan"]), false),
            ("Infinity", None, lookups(&["Infinity"]), false),
            // Two `No`/`Mn` code points: Python word characters, neither a number.
            ("\u{00b2}", None, lookups(&["\u{00b2}"]), false),
            ("a\u{0301}", None, lookups(&["a\u{0301}"]), false),
        ];

        for (raw, literal, expected_lookups, translate) in cases {
            // The row is named in every message: a bare `assert_eq!` failure would
            // not say which row produced it.
            let parsed = var(raw);
            assert_eq!(parsed.literal, literal, "literal for {raw:?}");
            assert_eq!(parsed.lookups, expected_lookups, "lookups for {raw:?}");
            assert_eq!(parsed.translate, translate, "translate for {raw:?}");
            assert_eq!(parsed.var, raw, "`var` must keep the original token");
        }
    }

    #[test]
    fn a_trailing_dot_keeps_both_the_literal_and_the_lookups() {
        // Django assigns the literal and *then* raises on a final `.`, so the
        // assignment survives; checking the trailing dot first would leave it `None`.
        let parsed = var("2.");
        assert_eq!(parsed.literal, Some(AttrValue::Float(2.0)));
        assert_eq!(parsed.lookups, lookups(&["2", ""]));
        assert!(!parsed.translate);

        // The literal is the exception, not the trailing dot: a dot elsewhere
        // leaves a plain variable with no literal.
        let parsed = var("5.5.5");
        assert_eq!(parsed.literal, None);
    }

    #[test]
    fn an_exponent_after_a_trailing_dot_is_still_a_number() {
        // `3.e5` separates "ends in a dot" from "is not a float": it converts
        // *and* does not end in `.`. Measured: `300000.0`, no lookups.
        let parsed = var("3.e5");
        assert_eq!(parsed.literal, Some(AttrValue::Float(300_000.0)));
        assert_eq!(parsed.lookups, None);
    }

    #[test]
    fn the_original_token_survives_the_translation_marker_strip() {
        // The constructor reassigns its *local* `var` and never writes the slot,
        // so the marker survives — and `__str__` returns it, which
        // `{% blocktranslate %}` renders.
        let parsed = var(r#"_("x")"#);
        assert_eq!(parsed.var, r#"_("x")"#);
        assert!(parsed.translate);
        assert_eq!(parsed.literal, safe("x"));
    }

    #[test]
    fn the_marker_flags_translate_even_when_it_wraps_something_that_is_not_a_literal() {
        // `translate` and "is a literal" are independent: `_( "x" )`'s inner text
        // fails the quoted-string rule, so it is a variable named ` "x" ` — still
        // marked for translation.
        let parsed = var(r#"_( "x" )"#);
        assert!(parsed.translate);
        assert_eq!(parsed.literal, None);
        assert_eq!(parsed.lookups, lookups(&[" \"x\" "]));
    }

    #[test]
    fn a_lone_quote_is_the_empty_literal() {
        // First and last character are compared, not open-quote/content/close-quote,
        // so a one-character token passes with an empty inner slice.
        for raw in ["\"", "'"] {
            assert_eq!(var(raw).literal, safe(""), "literal for {raw:?}");
            assert_eq!(var(raw).lookups, None, "lookups for {raw:?}");
        }
    }

    #[test]
    fn an_empty_variable_name_is_an_invariant_violation() {
        // Django raises `IndexError` from `var[0]`; unreachable from a template
        // (module doc), so `Internal`, not a `Syntax` blaming the template.
        for raw in ["", "_()"] {
            assert!(
                matches!(failure(raw), ErrorKind::Internal(_)),
                "{raw:?} produced {:?}",
                failure(raw),
            );
        }
    }

    #[test]
    fn the_underscore_rule_precedes_the_character_rules() {
        // Three orderings: `_.` is the underscore rule (`.` is never rejected), and
        // `a.b|default:"x"` passes because `"._" in var` needs the separator first.
        assert_eq!(
            failure("_x"),
            syntax("Variables and attributes may not begin with underscores: '_x'".to_string()),
        );
        assert_eq!(
            failure("_.x"),
            syntax("Variables and attributes may not begin with underscores: '_.x'".to_string()),
        );
        assert_eq!(
            failure("a._b"),
            syntax("Variables and attributes may not begin with underscores: 'a._b'".to_string()),
        );
        assert_eq!(
            failure("_"),
            syntax("Variables and attributes may not begin with underscores: '_'".to_string()),
        );
        // A trailing underscore is not a leading one: `a_` and `a_.b` are ordinary variables.
        assert_eq!(var("a_").lookups, lookups(&["a_"]));
        assert_eq!(var("a_.b").lookups, lookups(&["a_", "b"]));
    }

    #[test]
    fn the_character_rule_reports_plus_before_minus() {
        // Django iterates `["+", "-"]`, so the message names `+` whenever one is present at all.
        assert_eq!(
            failure("+"),
            syntax("Invalid character ('+') in variable name: '+'".to_string()),
        );
        assert_eq!(
            failure("-"),
            syntax("Invalid character ('-') in variable name: '-'".to_string()),
        );
        assert_eq!(
            failure("a+b"),
            syntax("Invalid character ('+') in variable name: 'a+b'".to_string()),
        );
        assert_eq!(
            failure("--5"),
            syntax("Invalid character ('-') in variable name: '--5'".to_string()),
        );
        assert_eq!(
            failure("a+b-c"),
            syntax("Invalid character ('+') in variable name: 'a+b-c'".to_string()),
        );
        // `-5` never reaches this branch: a leading `-` on a number is a sign.
        // Only a `-` that survived the number branch is rejected.
        assert_eq!(
            failure("-a"),
            syntax("Invalid character ('-') in variable name: '-a'".to_string()),
        );
        assert_eq!(var("-5").literal, Some(AttrValue::Int(-5)));
    }

    #[test]
    fn a_negative_integer_past_i64_keeps_its_sign() {
        // The sign survives into the `Big` text, which the materialiser feeds to
        // `int()`; a lost sign is a wrong number no type check catches.
        assert_eq!(
            var("-99999999999999999999").literal,
            Some(AttrValue::BigInt("-99999999999999999999".to_string())),
        );
    }

    #[test]
    fn unescape_string_literal_matches_python_including_the_lone_quote() {
        assert_eq!(unescape_string_literal(r#""abc""#), Some("abc".to_string()));
        assert_eq!(unescape_string_literal("'abc'"), Some("abc".to_string()));
        // The doc examples from `django/utils/text.py:443-460`.
        assert_eq!(
            unescape_string_literal(r#""a \"bc\"""#),
            Some(r#"a "bc""#.to_string()),
        );
        assert_eq!(
            unescape_string_literal(r"'\'ab\' c'"),
            Some("'ab' c".to_string()),
        );
        // The lone quote: empty rather than an error.
        assert_eq!(unescape_string_literal("\""), Some(String::new()));
        assert_eq!(unescape_string_literal("'"), Some(String::new()));
        assert_eq!(unescape_string_literal(r#""""#), Some(String::new()));
    }

    #[test]
    fn the_two_replacements_are_ordered() {
        // Escaped quote first, doubled backslash second; reversing them agrees on
        // simple cases and differs here, where a backslash precedes the closing quote.
        assert_eq!(unescape_string_literal(r#""\""#), Some("\\".to_string()),);
        assert_eq!(unescape_string_literal(r#""\\""#), Some("\\".to_string()),);
        assert_eq!(
            unescape_string_literal(r#""a\"b""#),
            Some(r#"a"b"#.to_string())
        );
        // Two backslashes *not* before a quote collapse to one.
        assert_eq!(
            unescape_string_literal(r#""a\\b""#),
            Some(r"a\b".to_string()),
        );
    }

    #[test]
    fn unescape_string_literal_rejects_what_python_rejects() {
        for bad in ["", "abc", r#""abc"#, r#"abc""#, r#"'abc""#, "a"] {
            assert_eq!(unescape_string_literal(bad), None, "{bad:?}");
        }
        // The two quotes must be the *same* one.
        assert_eq!(unescape_string_literal(r#""abc'"#), None);
        assert_eq!(unescape_string_literal("'abc\""), None);
    }

    #[test]
    fn the_translation_marker_strip_is_what_djangos_slice_is() {
        // A slice comparison, so `_(` is simply false (too short for both), not an
        // error — it falls through and is reported as an underscore name.
        assert_eq!(
            failure("_("),
            syntax("Variables and attributes may not begin with underscores: '_('".to_string()),
        );
        assert_eq!(
            failure("_()x"),
            syntax("Variables and attributes may not begin with underscores: '_()x'".to_string()),
        );
        // A marker that is not closed is not a marker.
        assert_eq!(
            failure("_(\"x\""),
            syntax(
                "Variables and attributes may not begin with underscores: '_(\"x\"'".to_string()
            ),
        );
    }
}
