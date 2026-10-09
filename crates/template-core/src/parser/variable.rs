//! `Variable.__init__` — `base.py:850-925`.
//!
//! The order of operations is load-bearing in one place and the module is
//! written to preserve it: the float branch **assigns before it raises**, so
//! `Variable("2.")` ends up with `literal=2.0` *and* `lookups=("2", "")`. The
//! value is unreachable through `resolve` (which branches on `lookups`), but the
//! slot is observable and the materialiser sets slots directly, so a port that
//! raised before assigning would produce an object Django never produces.
//!
//! **Three more measured details the source does not advertise**, each of which
//! a "tidy" port gets wrong:
//!
//! - `unescape_string_literal` accepts a **lone quote**. Django tests
//!   `s[0] in "\"'"` and `s[-1] == s[0]`, and on a one-character string both are
//!   that character, so `s[1:-1]` is the empty string rather than an error:
//!   `Variable('"').literal` is `SafeString('')`. A port that required a closing
//!   quote *after* an opening one would reject it. The same slice is why the
//!   Rust side needs an explicit length guard — `&s[1..s.len() - 1]` is a
//!   **panic** at `len == 1`, not an empty string.
//! - the underscore rule and the `+`/`-` rule are **ordered**, and the underscore
//!   rule's message quotes the token *after* the `_("…")` strip while the rule
//!   itself is tested before either character rule. `_.` is an underscore error,
//!   not an "Invalid character ('.')" one, because `.` is never checked.
//! - `3.e5` is a number. The `e`-branch is chosen by `"e" in var.lower()`, and
//!   the trailing-dot rejection only fires on a **final** `.`, so the trailing
//!   dot and the exponent are independent conditions: `2.` is not a number and
//!   `3.e5` is. Both were measured.
//!
//! **One divergence, measured, and deliberate.** Django indexes `var[0]` in the
//! fall-through, so `Variable("")` and `Variable("_()")` (whose marker strip
//! leaves `""`) raise **`IndexError`** — not `TemplateSyntaxError`. That is
//! unreachable from a template: the parser rejects an empty `{{ }}` before this
//! is called, and `FilterExpression`'s `var` group is `[\w\.\+-]+`, which cannot
//! match `_()` as a whole (measured: `FilterExpression("_()")` hands `Variable`
//! the token `_`, and reports the underscore error instead). So this returns
//! `ErrorKind::Internal` rather than reproducing an `IndexError` the wire has no
//! variant for — the same choice `unclosed_block_tag` makes, and for the same
//! reason: an invariant that turns out to be broken should be an `Error` rather
//! than a panic unwinding through the FFI.

use crate::parser::desc::{AttrValue, VariableDesc};
use crate::parser::error::Error;
use crate::pynum::{python_float, python_int, IntValue};

/// Django's `VARIABLE_ATTRIBUTE_SEPARATOR` (`base.py:60`).
const ATTRIBUTE_SEPARATOR: char = '.';

/// `Variable.__init__`, as a descriptor.
///
/// Takes the raw token rather than a `Token`: the caller has already decided
/// this is a variable, and Django's constructor does too.
pub fn parse_variable(raw: &str) -> Result<VariableDesc, Error> {
    // The working copy, which the marker strip rewrites. `var` is the field and
    // keeps the original — see `VariableDesc::var`.
    let mut text = raw.to_string();
    let mut literal = None;
    let mut lookups = None;
    let mut translate = false;

    // `if "." in var or "e" in var.lower()`. The `lower()` is spelled out as
    // two `contains` calls because the set of code points whose lowercase
    // contains an `e` is exactly `{'e', 'E'}` — measured over
    // U+0000..=U+10FFFF, two code points and both of them — so case folding
    // cannot move the decision, and not folding cannot either.
    let numeric = if text.contains('.') || text.contains('e') || text.contains('E') {
        // **Assign, then check the trailing dot.** Django converts first and
        // raises second, and the raise is caught by the same handler that goes
        // on to compute the lookups — so `2.` keeps a `2.0` literal it also
        // gets `("2", "")` alongside. These two statements must not be merged
        // into one condition.
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
        // `var[0:2] == "_(" and var[-1] == ")"`. Both are one byte, so the
        // slice is exact and `_(` alone is simply not a marker rather than an
        // error.
        if text.starts_with("_(") && text.ends_with(')') {
            translate = true;
            text = text[2..text.len() - 1].to_string();
        }

        match unescape_string_literal(&text) {
            Some(value) => literal = Some(AttrValue::SafeStr(value)),
            None => {
                // Django reads `var[0]` (`base.py:910`) and raises `IndexError`
                // for an empty name. That is unreachable from a template (see
                // the module doc), so it is an `Internal` invariant violation
                // rather than a claim about the template.
                if text.is_empty() {
                    return Err(Error::internal(
                        "an empty variable name reached parse_variable".to_string(),
                    ));
                }
                // `VARIABLE_ATTRIBUTE_SEPARATOR + "_" in var or var[0] == "_"`,
                // tested *before* the character rules — so `_.` is an underscore
                // error and never an "Invalid character ('.')" one.
                if text.contains("._") || text.starts_with('_') {
                    return Err(Error::syntax(format!(
                        "Variables and attributes may not begin with underscores: '{text}'"
                    )));
                }
                // `for c in ["+", "-"]`: `+` is reported whenever one is present
                // anywhere, whatever else the token holds.
                for c in ['+', '-'] {
                    if text.contains(c) {
                        return Err(Error::syntax(format!(
                            "Invalid character ('{c}') in variable name: '{text}'"
                        )));
                    }
                }
                // Python's `str.split(".")`, which keeps empty fields: `"."` is
                // `("", "")`. Rust's `str::split` on a non-empty string agrees
                // exactly, which is why the emptiness above had to be handled
                // first.
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
/// The two replacements are ordered: the escaped quote first, then a doubled
/// backslash, which is why `"a \"bc\""` is `a "bc"` and not `a \"bc\"`.
///
/// A lone quote (`"` or `'`) is a valid empty literal, because Django tests the
/// first and last **character** rather than that there are at least two.
pub fn unescape_string_literal(s: &str) -> Option<String> {
    let quote = s.chars().next()?;
    if quote != '"' && quote != '\'' {
        return None;
    }
    if !s.ends_with(quote) {
        return None;
    }
    // Django's `s[1:-1]`, which on a one-character string is the empty string
    // rather than an error. `&s[1..s.len() - 1]` **panics** at `len == 1`, so
    // the length guard is the port's, not Django's — and both delimiters are
    // one byte, so the byte arithmetic is exact.
    let inner = if s.len() >= 2 { &s[1..s.len() - 1] } else { "" };
    let unescaped_quote = inner.replace(&format!("\\{quote}"), &quote.to_string());
    Some(unescaped_quote.replace("\\\\", "\\"))
}

#[cfg(test)]
mod tests {
    use super::*;
    // Not in the module header: the implementation only ever *constructs* an
    // `Error` through `Error::syntax`/`Error::internal`, so importing the kind
    // there would be an unused import under `-D warnings`.
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

    /// One row of [`djangos_measured_table_of_literal_lookups_and_translate`].
    ///
    /// A named alias rather than an inline tuple, for the reason
    /// `pyregex.rs`'s `WordRange` is one: `clippy::type_complexity` has already
    /// bitten this project twice.
    type Row = (&'static str, Option<AttrValue>, Option<Vec<String>>, bool);

    #[test]
    fn djangos_measured_table_of_literal_lookups_and_translate() {
        // Every row was produced by running Django 6.0.9's own `Variable` on the
        // pinned checkout. This is the whole contract of the function, so it is
        // a table rather than a story: the rows that are *not* interesting are
        // here because they are the ones an implementation by intuition gets
        // wrong, and deleting a row silently narrows the port.
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
            // `float` is fine with `1e400`; `float` is also fine with `nan` and
            // `inf`, but neither token reaches the float branch — no `.`, no
            // `e`/`E` — so they are plain variables. See `pynum::python_float`.
            ("inf", None, lookups(&["inf"]), false),
            ("nan", None, lookups(&["nan"]), false),
            ("Infinity", None, lookups(&["Infinity"]), false),
            // The two `No`/`Mn` code points Task 2 exists for: both are Python
            // word characters, and neither is a number.
            ("\u{00b2}", None, lookups(&["\u{00b2}"]), false),
            ("a\u{0301}", None, lookups(&["a\u{0301}"]), false),
        ];

        for (raw, literal, expected_lookups, translate) in cases {
            // The row is named in every message, because a bare `assert_eq!`
            // failure prints two values and not which of the 36 rows produced
            // them — the difference between a failure and a bisect.
            let parsed = var(raw);
            assert_eq!(parsed.literal, literal, "literal for {raw:?}");
            assert_eq!(parsed.lookups, expected_lookups, "lookups for {raw:?}");
            assert_eq!(parsed.translate, translate, "translate for {raw:?}");
            assert_eq!(parsed.var, raw, "`var` must keep the original token");
        }
    }

    #[test]
    fn a_trailing_dot_keeps_both_the_literal_and_the_lookups() {
        // Django assigns `self.literal = float(var)` and *then* raises on a
        // final `.`, so the assignment survives into the `ValueError` handler's
        // path. `resolve` never reads the literal, because it branches on
        // `lookups is not None` — but the slot is observable, and a port that
        // checked the trailing dot before converting would leave it `None`.
        let parsed = var("2.");
        assert_eq!(parsed.literal, Some(AttrValue::Float(2.0)));
        assert_eq!(parsed.lookups, lookups(&["2", ""]));
        assert!(!parsed.translate);

        // And it is the *literal* that is the exception, not the trailing dot:
        // a dot anywhere else leaves it a plain variable with no literal.
        let parsed = var("5.5.5");
        assert_eq!(parsed.literal, None);
    }

    #[test]
    fn an_exponent_after_a_trailing_dot_is_still_a_number() {
        // The counterexample that separates "contains a final dot" from "is not
        // a float": `3.e5` converts *and* does not end in `.`, so it is a
        // number. Measured on the pinned tree: `300000.0`, no lookups.
        let parsed = var("3.e5");
        assert_eq!(parsed.literal, Some(AttrValue::Float(300_000.0)));
        assert_eq!(parsed.lookups, None);
    }

    #[test]
    fn the_original_token_survives_the_translation_marker_strip() {
        // `Variable.__init__` reassigns its *local* `var` and never writes the
        // slot again, so the slot holds the marker. `Variable.__str__` returns
        // `self.var`, and `{% blocktranslate %}` renders that string into the
        // template it emits, so getting this wrong is visible output.
        let parsed = var(r#"_("x")"#);
        assert_eq!(parsed.var, r#"_("x")"#);
        assert!(parsed.translate);
        assert_eq!(parsed.literal, safe("x"));
    }

    #[test]
    fn the_marker_flags_translate_even_when_it_wraps_something_that_is_not_a_literal() {
        // `translate` and "is a literal" are independent: `_( "x" )` has spaces
        // inside the marker, so the quoted-string rule rejects it and the token
        // becomes a *variable* — one whose name is ` "x" ` — and it is still
        // marked for translation.
        let parsed = var(r#"_( "x" )"#);
        assert!(parsed.translate);
        assert_eq!(parsed.literal, None);
        assert_eq!(parsed.lookups, lookups(&[" \"x\" "]));
    }

    #[test]
    fn a_lone_quote_is_the_empty_literal() {
        // Django compares the first and last character, not "an opening quote
        // followed by at least one character followed by a closing quote", so a
        // one-character token passes and `s[1:-1]` is empty.
        for raw in ["\"", "'"] {
            assert_eq!(var(raw).literal, safe(""), "literal for {raw:?}");
            assert_eq!(var(raw).lookups, None, "lookups for {raw:?}");
        }
    }

    #[test]
    fn an_empty_variable_name_is_an_invariant_violation() {
        // Django raises `IndexError` here, from `var[0]`. Unreachable from a
        // template — see the module doc — so it is `Internal` rather than a
        // `Syntax` claiming the template is wrong. Both spellings are pinned:
        // the empty token, and the one whose marker strip produces it.
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
        // Three orderings at once. `_.` contains no `+`/`-`, so it is the
        // underscore rule that fires — and `.` is never a rejected character.
        // `a.b|default:"x"` is fine because the rule is `"._" in var`, which
        // needs the separator *before* the underscore.
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
        // A trailing underscore is not a leading one, and no separator precedes
        // it, so `a_` and `a_.b` are ordinary variables.
        assert_eq!(var("a_").lookups, lookups(&["a_"]));
        assert_eq!(var("a_.b").lookups, lookups(&["a_", "b"]));
    }

    #[test]
    fn the_character_rule_reports_plus_before_minus() {
        // Django iterates `["+", "-"]`, so the message names `+` whenever a `+`
        // is present at all, wherever the `-` is.
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
        // A leading `-` is a *sign*, not a rejected character, when the token is
        // a number: `-5` never reaches this branch. Only a `-` that survived the
        // number branch is rejected, which is why `-a` is an error and `-5` is
        // not.
        assert_eq!(
            failure("-a"),
            syntax("Invalid character ('-') in variable name: '-a'".to_string()),
        );
        assert_eq!(var("-5").literal, Some(AttrValue::Int(-5)));
    }

    #[test]
    fn a_negative_integer_past_i64_keeps_its_sign() {
        // The sign survives into the `Big` text, because the text is what the
        // materialiser feeds to `int()` and a lost sign is a wrong number that
        // no type check catches.
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
        // The lone quote, which is empty rather than an error.
        assert_eq!(unescape_string_literal("\""), Some(String::new()));
        assert_eq!(unescape_string_literal("'"), Some(String::new()));
        assert_eq!(unescape_string_literal(r#""""#), Some(String::new()));
    }

    #[test]
    fn the_two_replacements_are_ordered() {
        // Escaped quote first, doubled backslash second. Measured:
        // `unescape_string_literal('"\\"')` is `\`, because the two-backslash
        // pass only sees what the quote pass left. Reversing them turns a
        // `\"`  into `\"`-then-`"`, which agrees on the simple case and differs
        // here — an escaped backslash immediately before the closing quote.
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
        // `var[0:2] == "_(" and var[-1] == ")"`, which is a slice comparison and
        // so is false for `_(` (too short to have both) rather than an error.
        // That matters because `_(` then falls through to the underscore rule
        // and is reported as an underscore name, *not* as a broken marker.
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
