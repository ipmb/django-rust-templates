//! `title` — `defaultfilters.py:297-302`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def title(value):
//!     """Convert a string into titlecase."""
//!     t = re.sub("([a-z])'([A-Z])", lambda m: m[0].lower(), value.title())
//!     return re.sub(r"\d([A-Z])", lambda m: m[0].lower(), t)
//! ```
//!
//! The body is **the host's `str.title()`** followed by two Rust regexes, and the
//! split is a measurement rather than a preference (§D2's second metric).
//! `str.title()` needs three things Rust has no equivalent of: Unicode's `Cased`
//! property (4,578 code points, which `unicodedata` does not even expose), a
//! 135-entry titlecase mapping table distinct from uppercase, and the same
//! Final_Sigma rule when lowercasing each run's remainder. A from-first-principles
//! Rust rule reproduced all 1,112,064 single code points and still failed **444 of
//! 400,000** random strings, every one a final sigma — so `str.title()` is
//! [`FilterHost::str_title`], and `"ΟΣ".title()` is `"Ος"`, which no per-code-point
//! table can produce.
//!
//! The two regexes are the one part that *is* Rust's, and the reason is worth
//! recording so nobody "fixes" it later: enumerating every code point in both
//! patterns shows the only cased characters a match can hold are ASCII `a-z` and
//! `A-Z` (`\d` matches 760 code points and **none** of them is cased), so
//! `.lower()` on a match has no Final_Sigma question to answer — unlike
//! `str.title()`'s own run-remainder lowercasing, inside the host call, which is
//! where the 444 failures came from. Both patterns are Django's verbatim; `\d` is
//! Unicode `\p{Nd}` in both languages.
//!
//! [`FilterHost::str_title`]: crate::filters::host::FilterHost::str_title

use std::sync::OnceLock;

use regex::{Captures, Regex};

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `([a-z])'([A-Z])` — a letter, an apostrophe and an uppercase letter, which is
/// the `"They'Re"` shape `str.title()` produces for `"they're"`.
fn apostrophe_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"([a-z])'([A-Z])").expect("title's first pattern is fixed"))
}

/// `\d([A-Z])` — a digit followed by an uppercase letter, the `"1D"` in
/// `"Abc1Def"`. `\d` is Unicode `\p{Nd}` in Rust's `regex` as it is in Python's
/// `re`, so the two select the same code points.
fn digit_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\d([A-Z])").expect("title's second pattern is fixed"))
}

/// `title`'s body.
///
/// The `@stringfilter` coercion already happened (§D3), so `title(5)` is `"5"`.
/// `str.title()` is the host's; the two `re.sub`s run here, each replacing the
/// **whole match** with its lowercase, exactly as Django's `lambda m:
/// m[0].lower()` does.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    let titled = call.host.str_title(&coerced);
    // `m[0].lower()` lowercases the whole match. The match is ASCII-cased at
    // most, so this is `str.lower` with no Final_Sigma question — see the module
    // doc.
    let after_apostrophe = apostrophe_re()
        .replace_all(&titled, |caps: &Captures| caps[0].to_lowercase())
        .into_owned();
    let after_digit = digit_re()
        .replace_all(&after_apostrophe, |caps: &Captures| caps[0].to_lowercase())
        .into_owned();
    Ok(Value::Str(after_digit.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// A host whose `to_str` and `str_title` answers the test scripts. `to_str`
    /// is what the body receives; `str_title` is the *intermediate* `value.title()`
    /// the two regexes then run over, so the regex tests can name it directly.
    fn host(input: &str, titled: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(input.to_string()),
            str_title: Some(titled.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The delegation, asserted through the *Final_Sigma* answer: `"ΟΣ".title()`
    /// is `"Ος"`, which a Rust per-code-point implementation cannot produce. A
    /// body that reimplemented `str.title` in Rust fails here while passing every
    /// ASCII case anyone would write.
    #[test]
    fn it_titles_through_the_host() {
        let h = host("ΟΣ", "Ος");
        assert_eq!(
            run(Value::Str("ΟΣ".into()), None, &call(&h)).unwrap(),
            Value::Str("Ος".into()),
            "the host's Final_Sigma answer is the filter's answer"
        );
        h.assert_called("str_title");
    }

    /// The first regex: `str.title()` gives `"They'Re"`, and `([a-z])'([A-Z])`
    /// lowercases the whole three-character match back to `"e'r"`.
    #[test]
    fn it_lowercases_an_upper_after_an_apostrophe() {
        let h = host("they're", "They'Re");
        assert_eq!(
            run(Value::Str("they're".into()), None, &call(&h)).unwrap(),
            Value::Str("They're".into())
        );
    }

    /// The second regex: a digit followed by an uppercase letter is lowercased
    /// across the whole two-character match.
    #[test]
    fn it_lowercases_an_upper_after_a_digit() {
        let h = host("abc1Def", "Abc1Def");
        assert_eq!(
            run(Value::Str("abc1Def".into()), None, &call(&h)).unwrap(),
            Value::Str("Abc1def".into())
        );
    }

    /// Both regexes at once, in order — the pair the body must apply, on one
    /// input, so a body that ran only one of them is caught by whichever
    /// assertion it did not satisfy.
    #[test]
    fn both_regexes_run_in_order() {
        let h = host("o'brien 3rd", "O'Brien 3Rd");
        assert_eq!(
            run(Value::Str("o'brien 3rd".into()), None, &call(&h)).unwrap(),
            Value::Str("O'Brien 3rd".into())
        );
    }

    /// **The digit-class guard.** The module doc's argument that `.lower()` on a
    /// match never meets a Final_Sigma problem rests on `\d` matching only
    /// **uncased** code points. Python 3.14.4's `\d` matches exactly 760 code
    /// points, all of category `Nd`, none of them cased (measured); Rust's
    /// `regex` spells `\d` as the same `\p{Nd}`, and this pins Rust's side of
    /// that claim, so a `regex`-crate Unicode bump that changed the class would
    /// fail here rather than only in a corpus run.
    #[test]
    fn rusts_digit_class_is_the_same_uncased_nd_python_matches() {
        let digit_only = Regex::new(r"\d").expect("the digit class compiles");
        let mut matched = 0usize;
        let mut cased: Vec<char> = Vec::new();
        for cp in 0..=0x10FFFF {
            let Some(ch) = char::from_u32(cp) else {
                continue; // surrogates are not `char`s
            };
            let text = ch.to_string();
            if !digit_only.is_match(&text) {
                continue;
            }
            matched += 1;
            if ch.to_lowercase().ne(std::iter::once(ch))
                || ch.to_uppercase().ne(std::iter::once(ch))
            {
                cased.push(ch);
            }
        }
        assert_eq!(
            matched, 760,
            "Python 3.14.4's `\\d` matches 760 code points"
        );
        assert!(
            cased.is_empty(),
            "no code point `\\d` matches is cased, but these are: {cased:?}"
        );
    }

    /// The coercion is reached: `title(5)` is `"5"`.
    #[test]
    fn it_coerces_through_the_host() {
        let h = host("5", "5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        h.assert_called("to_str");
    }
}
