//! Text utilities ported from `django/utils/text.py`.
//!
//! These live outside `django.template`, so shadowing the template namespace
//! does not replace them — Django's originals stay live for other callers. The
//! Rust versions must therefore match them exactly rather than inherit them.

use std::sync::OnceLock;

use regex::Regex;

/// Splits a string on whitespace, keeping quoted phrases together, where the
/// quotes are kept in the output.
///
/// Mirrors `smart_split` (`django/utils/text.py:423`), which is
/// `smart_split_re.finditer(str(text))`.
pub fn smart_split(text: &str) -> Vec<&str> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        // Django writes this pattern with `\s` and `\S`. Python's `\s` is built
        // on `str.isspace()`, which also matches U+001C-U+001F; the `regex`
        // crate's is Unicode `White_Space`, which does not. Both are therefore
        // spelled out as `crate::pyunicode::WS_CLASS` here. Substituting the
        // class for `\s` and its negation for `\S` is the whole change — the
        // rest of the pattern is Django's verbatim.
        //
        // A caution for anyone re-verifying this later: comparing the two
        // spellings *in Python* proves nothing about the substitution, because
        // there they denote the same 29 code points and the substitution is a
        // no-op. It is exercised only by the Rust-side parity run in
        // `tests/test_lexer_parity.py` — which is why those corpora carry the
        // separators at all.
        let pattern = format!(
            r#"((?:[^{ws}'"]*(?:(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')[^{ws}'"]*)+)|[^{ws}]+)"#,
            ws = crate::pyunicode::WS_CLASS,
        );
        Regex::new(&pattern).expect("smart_split_re is a valid pattern")
    });
    re.find_iter(text).map(|m| m.as_str()).collect()
}

/// Raised where Django's `Token.split_contents` lets `next(bits)` raise
/// `StopIteration` — an unterminated `_("...` marker at the end of the token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SplitError;

/// `get_text_list(items, last_word)` (`django/utils/text.py:282`).
///
/// Joins with `", "` and the final item with `last_word`: `"a, b or c"`, and
/// notably `"a or b"` for two items — with no comma. The parser's
/// `invalid_block_tag` message is built with it and is pinned by the suite.
///
/// Django's version passes the separator through `gettext`; the message is
/// already assembled in the host's language by then, and M2 has no i18n layer,
/// so the untranslated separator is what is used here. Noted because it is a
/// real difference, not an oversight.
pub fn get_text_list(items: &[String], last_word: &str) -> String {
    if items.is_empty() {
        return String::new();
    }
    if items.len() == 1 {
        return items[0].clone();
    }
    format!(
        "{} {} {}",
        items[..items.len() - 1].join(", "),
        last_word,
        items[items.len() - 1],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn get_text_list_joins_with_the_last_word() {
        fn items(names: &[&str]) -> Vec<String> {
            names.iter().map(|name| name.to_string()).collect()
        }
        assert_eq!(get_text_list(&items(&[]), "or"), "");
        assert_eq!(get_text_list(&items(&["a"]), "or"), "a");
        // Two items take no comma: this is the case a hand-rolled join gets
        // wrong, and it is the one `_("{% endautoescape %}{% endspaceless %}")`
        // -style messages hit.
        assert_eq!(get_text_list(&items(&["a", "b"]), "or"), "a or b");
        assert_eq!(get_text_list(&items(&["a", "b", "c"]), "or"), "a, b or c");
        assert_eq!(get_text_list(&items(&["a", "b", "c"]), "and"), "a, b and c");
    }

    #[test]
    fn splits_on_whitespace() {
        assert_eq!(smart_split("a b c"), vec!["a", "b", "c"]);
        assert_eq!(smart_split("  leading"), vec!["leading"]);
        assert_eq!(smart_split("trailing  "), vec!["trailing"]);
    }

    #[test]
    fn keeps_quoted_phrases_together_and_keeps_the_quotes() {
        assert_eq!(smart_split(r#"a "b c" d"#), vec!["a", "\"b c\"", "d"]);
        assert_eq!(smart_split("a 'b c' d"), vec!["a", "'b c'", "d"]);
    }

    #[test]
    fn escaped_quotes_do_not_close_the_phrase() {
        // The `\\.` alternative means a backslash-escaped quote is consumed
        // rather than ending the quoted run.
        assert_eq!(smart_split(r#"a "b\"c" d"#), vec!["a", r#""b\"c""#, "d"]);
    }

    #[test]
    fn empty_and_whitespace_only_inputs_yield_nothing() {
        assert!(smart_split("").is_empty());
        assert!(smart_split("   ").is_empty());
    }

    #[test]
    fn unquoted_quotes_are_kept_in_the_word() {
        // `a"b` has no closing quote, so the quoted alternative cannot match and
        // `\S+` takes the whole run.
        assert_eq!(smart_split(r#"a"b"#), vec![r#"a"b"#]);
        assert_eq!(smart_split("a'b"), vec!["a'b"]);
    }
}
