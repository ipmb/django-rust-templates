//! `token_kwargs`: the keyword arguments at the front of a tag's bits.
//!
//! A direct port of `django.template.base.token_kwargs` (`base.py:1163-1207`)
//! and the module-level `kwarg_re` (`base.py:1160`). Two of Django's behaviours
//! are load-bearing and are therefore in the public doc comments rather than
//! only in tests: `bits` is mutated **in place**, and the function **returns
//! early** on the first argument whose format is invalid instead of raising.
//!
//! Nothing calls this at M3. Its three call sites arrive later — `{% with %}`
//! (`defaulttags.py:1676`, `support_legacy=True`) and `{% include %}`'s `with`
//! option (`loader_tags.py:347`, `False`) at M5/M7, and `parse_bits`
//! (`library.py:421`, `False`) for every `simple_tag`/`inclusion_tag` at M6 —
//! so the tests below drive it directly and that is stated rather than
//! discovered.
//!
//! `kwarg_re` is `pub` and not private to this module because
//! `{% url %}` (`defaulttags.py:1575`) calls `kwarg_re.match` on each bit
//! directly instead of going through `token_kwargs`, and is the tree's only
//! other consumer.

use std::sync::OnceLock;

use regex::{Captures, Regex};

use crate::parser::desc::AttrValue;
use crate::parser::error::Error;
use crate::pyregex::PYTHON_WORD_STANDALONE;

/// Django's `kwarg_re`, `_lazy_re_compile(r"(?:(\w+)=)?(.+)")` (`base.py:1160`).
///
/// `\w` is the one token that does not port: Python's is 142,940 code points
/// (`isalnum() or '_'`), Rust's is 144,667, and the two disagree in both
/// directions. `PYTHON_WORD_STANDALONE` is the *standalone* spelling of Python's set
/// (`[\p{L}\p{N}_]`, also 142,940), so the pattern is assembled with `format!`
/// rather than written literally. Measured against the pinned checkout:
/// `["\u{00b2}=1"]` keys on `\u{00b2}` and `["a\u{0301}=1"]` does not key at
/// all; neither follows from Rust's `\w`.
///
/// Public because `{% url %}` (`defaulttags.py:1575`) calls `kwarg_re.match`
/// directly instead of `token_kwargs`, and is the tree's only other consumer.
pub fn kwarg_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(r"(?:({PYTHON_WORD_STANDALONE}+)=)?(.+)"))
            .expect("kwarg_re is a valid pattern")
    })
}

/// Python's `kwarg_re.match(s)`.
///
/// Python's `.match` anchors at the start of the string; Rust's
/// [`Regex::captures`] returns the leftmost match *anywhere*, and
/// `Regex::captures_at(s, 0)` is not an anchor either — both find a match at
/// offset 1 in `"\nb=c"` where Python finds none (measured against `regex`
/// 1.13.1). Every reachable `bit` starts with a non-whitespace character, so
/// the two agree on all real input; the guard is here because the divergence is
/// silent, and `token_kwargs(["\nb=c"])` would otherwise key `b` rather than
/// returning no arguments.
fn match_start(s: &str) -> Option<Captures<'_>> {
    kwarg_re()
        .captures(s)
        .filter(|caps| caps.get(0).is_some_and(|m| m.start() == 0))
}

/// The kwargs pulled from the front of `bits`, in Python dict insertion order.
///
/// An alias rather than an inline tuple for two reasons: `clippy::type_complexity`
/// has bitten this project twice, and the aliased type is what a caller wraps in
/// `AttrValue::Map` — the same `Vec<(String, AttrValue)>` — so the emptiness
/// check (`{% with %}`'s "expected at least one variable assignment") stays on
/// the caller, where Django has it.
pub type Kwargs = Vec<(String, AttrValue)>;

/// Django's `token_kwargs(bits, parser, support_legacy=False)` (`base.py:1163`).
///
/// Parse token keyword arguments and return them in insertion order. `bits` is
/// the remainder of a tag's `split_contents`, and valid arguments are **removed
/// from the front of it in place**, so the caller reads what is left afterwards
/// — `{% with %}` and `{% include %}` both do, and both raise on what remains.
///
/// `compile_filter` is Django's `parser.compile_filter` (`base.py:1202`), the
/// one thing `token_kwargs` uses its `parser` argument for. It receives the
/// whole value string, filter chain and all: `["a=1|add:2"]` hands `"1|add:2"`
/// over. At M3 the caller passes
/// `|s| parse_filter_expression(s, host).map(|e| AttrValue::FilterExpr(Box::new(e)))`;
/// at M6 a loaded custom tag passes the host's compiler. Its error is **not**
/// caught: because Django deletes the bit before calling, the offending bit is
/// already gone when the error returns, and the port preserves that order.
///
/// `support_legacy` selects Django's legacy `1 as foo` form, which names the
/// variable in the *third* bit and compiles the *first*. There is no
/// requirement that all remaining bits are keyword arguments: the function
/// returns as soon as an argument's format is invalid.
pub fn token_kwargs(
    bits: &mut Vec<String>,
    compile_filter: &mut dyn FnMut(&str) -> Result<AttrValue, Error>,
    support_legacy: bool,
) -> Result<Kwargs, Error> {
    if bits.is_empty() {
        return Ok(Kwargs::new());
    }

    // `kwarg_format = match and match[1]`: an argument is in kwarg form iff the
    // optional `key=` group participated. Note the two negatives this collapses:
    // `["a="]` matches but group 1 does not participate, so it is not kwarg
    // form, and it is not legacy form either — which is why it returns empty and
    // changes nothing rather than keying `a` on an empty value.
    let kwarg_format = match_start(&bits[0]).and_then(|caps| caps.get(1)).is_some();
    if !kwarg_format && (!support_legacy || bits.len() < 3 || bits[1] != "as") {
        return Ok(Kwargs::new());
    }

    let mut kwargs = Kwargs::new();
    while !bits.is_empty() {
        let (key, value) = if kwarg_format {
            // Django re-matches every iteration: a later bit may not be a kwarg,
            // and that is how the run stops.
            let Some(caps) = match_start(&bits[0]) else {
                return Ok(kwargs);
            };
            let Some(key) = caps.get(1) else {
                return Ok(kwargs);
            };
            let key = key.as_str().to_string();
            // `(.+)` is mandatory and matched, so group 2 is present.
            let value = caps.get(2).map_or("", |m| m.as_str()).to_string();
            // Django's `del bits[:1]`, before `compile_filter` below.
            bits.remove(0);
            (key, value)
        } else {
            // Django's `key, value = bits[2], bits[0]`: the name is the THIRD
            // bit and the value is the FIRST.
            if bits.len() < 3 || bits[1] != "as" {
                return Ok(kwargs);
            }
            let key = bits[2].clone();
            let value = bits[0].clone();
            // Django's `del bits[:3]`, before `compile_filter` below.
            bits.drain(..3);
            (key, value)
        };

        kwargs.push((key, compile_filter(&value)?));

        // Only the legacy form consumes the `and` between pairs; in kwarg form a
        // stray `and` terminates the run like any other non-kwarg bit. That is
        // the only asymmetry between the two branches.
        if !kwarg_format && !bits.is_empty() {
            if bits[0] != "and" {
                return Ok(kwargs);
            }
            bits.remove(0);
        }
    }

    Ok(kwargs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::filter_expr::parse_filter_expression;
    use crate::parser::host::NoHost;

    /// A `compile_filter` that records the exact strings it is handed, so the
    /// contract "the whole value string reaches the compiler" is asserted rather
    /// than assumed, and returns a placeholder value.
    #[derive(Default)]
    struct Recorder {
        seen: Vec<String>,
    }

    impl Recorder {
        fn compile(&mut self) -> impl FnMut(&str) -> Result<AttrValue, Error> + '_ {
            |value: &str| {
                self.seen.push(value.to_string());
                Ok(AttrValue::Str(value.to_string()))
            }
        }
    }

    /// Django's `token_kwargs` on `bits`, returning keys, the remaining bits, and
    /// the strings the compiler saw.
    fn run(bits: &[&str], support_legacy: bool) -> (Vec<String>, Vec<String>, Vec<String>) {
        let mut bits: Vec<String> = bits.iter().map(|b| (*b).to_string()).collect();
        let mut rec = Recorder::default();
        let kwargs = {
            let mut compile = rec.compile();
            token_kwargs(&mut bits, &mut compile, support_legacy).expect("valid in this table")
        };
        (
            kwargs.into_iter().map(|(key, _)| key).collect(),
            bits,
            rec.seen,
        )
    }

    #[test]
    fn the_behaviour_table_matches_django() {
        // Every row was measured against Django 6.0.9 at `.django/` by calling
        // `django.template.base.token_kwargs` with a `Parser` built as
        // `Parser(Lexer("x").tokenize(), builtins=Engine().template_builtins)`,
        // and re-measured when this task was implemented.
        // The four rows carrying the risk are `["a="]`, `["=1"]`,
        // `["a=1", "and", "b=2"]` and `["a=1", "junk"]`; keep them if trimming.
        // The row type is an alias, not inline: the four-tuple written inline is
        // a `clippy::type_complexity` failure under `-D warnings`, verified.
        type Case<'a> = (&'a [&'a str], bool, &'a [&'a str], &'a [&'a str]);
        let cases: &[Case] = &[
            (&[], false, &[], &[]),
            (&["a=1"], false, &["a"], &[]),
            (&["a=1", "b=2"], false, &["a", "b"], &[]),
            (&["a=1", "junk"], false, &["a"], &["junk"]),
            (&["junk"], false, &[], &["junk"]),
            (&["a="], false, &[], &["a="]),
            (&["=1"], false, &[], &["=1"]),
            (&["a=1", "and", "b=2"], false, &["a"], &["and", "b=2"]),
            (
                &["a=1", "b=2", "rest", "more"],
                false,
                &["a", "b"],
                &["rest", "more"],
            ),
            (&["1", "as", "foo"], true, &["foo"], &[]),
            (&["1", "as", "foo"], false, &[], &["1", "as", "foo"]),
            (&["a=1"], true, &["a"], &[]),
            (&["a=1", "x", "as", "y"], true, &["a"], &["x", "as", "y"]),
            (&["1", "as", "foo", "junk"], true, &["foo"], &["junk"]),
            (&["1", "AS", "foo"], true, &[], &["1", "AS", "foo"]),
            (&["1", "as"], true, &[], &["1", "as"]),
            (
                &["1", "as", "a", "and", "2", "as", "b"],
                true,
                &["a", "b"],
                &[],
            ),
            (
                &["1", "as", "foo", "and", "junk"],
                true,
                &["foo"],
                &["junk"],
            ),
            (
                &["person.some_sql_method", "as", "total"],
                true,
                &["total"],
                &[],
            ),
            (&["\u{00b2}=1"], false, &["\u{00b2}"], &[]),
            (&["a\u{00b2}=1"], false, &["a\u{00b2}"], &[]),
            (&["a\u{0301}=1"], false, &[], &["a\u{0301}=1"]),
            (&["\u{0301}=1"], false, &[], &["\u{0301}=1"]),
            (&["xyz=bad_var"], false, &["xyz"], &[]),
            (&["a=1|add:2"], false, &["a"], &[]),
            (&["a=x|default:y"], false, &["a"], &[]),
        ];
        for &(bits, legacy, keys, remaining) in cases {
            let (got_keys, got_remaining, _) = run(bits, legacy);
            assert_eq!(got_keys, keys, "keys for {bits:?} legacy={legacy}");
            assert_eq!(
                got_remaining, remaining,
                "remaining for {bits:?} legacy={legacy}",
            );
        }
    }

    #[test]
    fn the_legacy_form_names_the_third_bit_and_compiles_the_first() {
        // `key, value = bits[2], bits[0]` — the reversed order is the whole of
        // the legacy form, and swapping the two would produce a kwarg named
        // after the value. The table above cannot see it, because every legacy
        // row there would key the same string either way only if value == key.
        let mut bits: Vec<String> = ["1", "as", "foo"].iter().map(|s| s.to_string()).collect();
        let mut rec = Recorder::default();
        let kwargs = {
            let mut compile = rec.compile();
            token_kwargs(&mut bits, &mut compile, true).expect("legacy form")
        };
        assert_eq!(kwargs.len(), 1);
        assert_eq!(kwargs[0].0, "foo");
        assert_eq!(kwargs[0].1, AttrValue::Str("1".to_string()));
        assert_eq!(rec.seen, vec!["1".to_string()], "the FIRST bit is compiled");
    }

    #[test]
    fn the_value_reaches_compile_filter_whole() {
        // Not the key, not the first filter term — the entire value string, so
        // that the same `FilterExpression` parser sees it as anywhere else.
        let (_, _, seen) = run(&["a=1|add:2"], false);
        assert_eq!(seen, vec!["1|add:2".to_string()]);
        let (_, _, seen) = run(&["a=xyz"], false);
        assert_eq!(seen, vec!["xyz".to_string()]);
        // `["a="]` never reaches the compiler at all.
        let (_, _, seen) = run(&["a="], false);
        assert!(seen.is_empty());
    }

    #[test]
    fn kwarg_re_is_djangos_pattern_with_the_word_class_expanded() {
        // The guard Task 2's two spellings rest on: the bracketed form must be
        // spliced standalone. With the contents form the source would be
        // `(?:(\p{L}\p{N}_+)=)?(.+)` and `upper` would stop matching; this
        // assertion is what makes that unspellable rather than merely untested.
        assert_eq!(kwarg_re().as_str(), r"(?:([\p{L}\p{N}_]+)=)?(.+)");
    }

    #[test]
    fn kwarg_re_matches_djangos_word_class_not_rusts() {
        let group1 = |s: &str| {
            kwarg_re()
                .captures(s)
                .and_then(|c| c.get(1))
                .map(|m| m.as_str().to_string())
        };
        // Python's `\w` admits U+00B2 (`No`) and rejects U+0301 (`Mn`).
        assert_eq!(group1("\u{00b2}=1"), Some("\u{00b2}".to_string()));
        assert_eq!(group1("a\u{0301}=1"), None);
        // Rust's `\w` would invert both, and `[a-z]`-style classes would reject
        // a non-ASCII letter entirely.
        assert_eq!(group1("\u{03b1}=1"), Some("\u{03b1}".to_string()));
    }

    #[test]
    fn kwarg_re_matches_like_python_match_not_like_rust_captures() {
        // Python's `.match` anchors at the start; Rust's `captures` (and
        // `captures_at(_, 0)`) do not. `"\nb=c"` is the distinguishing input:
        // Python keys nothing, a leftmost search keys `b`.
        assert!(
            kwarg_re().captures("\nb=c").is_some(),
            "leftmost search does match"
        );
        assert!(
            match_start("\nb=c").is_none(),
            "the port must anchor at the start"
        );
        assert!(match_start("a=1").is_some());
    }

    #[test]
    fn a_compiler_error_propagates_and_leaves_the_bit_consumed() {
        // Django deletes the bit *before* `compile_filter` (`base.py:1194` then
        // :1202), so the offending bit is gone when the error returns. Measured:
        // `["a=|", "rest"]` leaves `["rest"]`; `["a=1", "b=|", "rest"]` too.
        let mut bits: Vec<String> = ["a=|", "rest"].iter().map(|s| s.to_string()).collect();
        let mut compile = |value: &str| -> Result<AttrValue, Error> {
            if value == "|" {
                return Err(Error::syntax("Could not parse the remainder: '|' from '|'"));
            }
            Ok(AttrValue::Str(value.to_string()))
        };
        let err = token_kwargs(&mut bits, &mut compile, false).expect_err("the error propagates");
        assert_eq!(
            err.kind,
            ErrorKind::Syntax("Could not parse the remainder: '|' from '|'".to_string()),
        );
        assert_eq!(
            bits,
            vec!["rest".to_string()],
            "the consumed bit stays consumed"
        );
    }

    #[test]
    fn values_compile_through_the_real_filter_parser() {
        // The M3 wiring: the closure every M5 caller passes is
        // `|s| parse_filter_expression(s, host).map(|e| AttrValue::FilterExpr(Box::new(e)))`.
        // Asserted through the filter *name*, so the test does not reach into
        // Task 3's or Task 5's descriptor internals; full parity is Task 5's
        // corpus test.
        let mut bits: Vec<String> = ["a=1|add:2", "b=x"].iter().map(|s| s.to_string()).collect();
        let mut compile = |value: &str| {
            parse_filter_expression(value, &NoHost).map(|e| AttrValue::FilterExpr(Box::new(e)))
        };
        let kwargs = token_kwargs(&mut bits, &mut compile, false).expect("compiles");
        assert_eq!(kwargs.len(), 2);
        let AttrValue::FilterExpr(expr) = &kwargs[0].1 else {
            panic!("expected a filter expression")
        };
        assert_eq!(expr.filters.len(), 1);
        assert_eq!(expr.filters[0].name, "add");
        assert!(bits.is_empty());
    }
}
