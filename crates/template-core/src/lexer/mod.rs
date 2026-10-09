//! The lexer, following `django.template.base.Lexer` and `DebugLexer`.
//!
//! `Lexer` and `DebugLexer` differ only in whether token positions are
//! recorded, so one implementation with a `debug` flag serves both. Django
//! keeps them as separate classes for speed; that distinction is **not worth
//! reproducing in Rust**, but it is **mandatory in the Python shell at M9**,
//! which must expose both as distinct classes. See "M9 obligations" in the
//! plan for the three things the shell must reconstruct.

pub mod segments;

// Also a `use`, so `segments` and `Segment` are in scope below.
pub use segments::{segments, Segment};

use crate::pyunicode;
use crate::token::{Token, TokenType};

// Template syntax constants, from django/template/base.py.
const BLOCK_TAG_START: &str = "{%";
const VARIABLE_TAG_START: &str = "{{";
const COMMENT_TAG_START: &str = "{#";

pub struct Lexer<'a> {
    source: &'a str,
    /// Set while inside a `{% verbatim %}` block; holds the exact contents the
    /// closing tag must have, e.g. `"endverbatim"`.
    verbatim: Option<String>,
    debug: bool,
}

impl<'a> Lexer<'a> {
    pub fn new(source: &'a str) -> Self {
        Self {
            source,
            verbatim: None,
            debug: false,
        }
    }

    pub fn debug(source: &'a str) -> Self {
        Self {
            source,
            verbatim: None,
            debug: true,
        }
    }

    /// Verbatim state, exposed so tests can assert it is left clean.
    pub fn verbatim(&self) -> Option<&str> {
        self.verbatim.as_deref()
    }

    pub fn tokenize(&mut self) -> Vec<Token> {
        let mut out = Vec::new();
        let mut in_tag = false;
        let mut lineno = 1usize;

        for segment in segments(self.source) {
            if !segment.text.is_empty() {
                out.push(self.create_token(&segment, lineno, in_tag));
                lineno += segment.text.matches('\n').count();
            }
            // The toggle happens for every segment, including empty ones —
            // that is what keeps adjacent tags aligned.
            in_tag = !in_tag;
        }

        out
    }

    fn create_token(&mut self, segment: &Segment<'_>, lineno: usize, in_tag: bool) -> Token {
        let position = if self.debug { Some(segment.span) } else { None };
        let text = segment.text;

        if in_tag {
            let token_start = &text[..2];

            if token_start == BLOCK_TAG_START {
                // `[2..len - 2]` strips `{%` and `%}`. Safe on byte indices
                // because both delimiters are ASCII, so both edges are
                // character boundaries.
                // `pyunicode::strip`, not `str::trim`: Django writes
                // `.strip()`, whose table is Python's `str.isspace()` and so
                // includes U+001C-U+001F. See the `pyunicode` module.
                let content = pyunicode::strip(&text[2..text.len() - 2]);

                if let Some(expected) = self.verbatim.clone() {
                    if content != expected {
                        return Token {
                            token_type: TokenType::Text,
                            contents: text.to_string(),
                            lineno,
                            position,
                        };
                    }
                    self.verbatim = None;
                } else if is_verbatim_start(content) {
                    self.verbatim = Some(format!("end{content}"));
                }

                return Token {
                    token_type: TokenType::Block,
                    contents: content.to_string(),
                    lineno,
                    position,
                };
            }

            if self.verbatim.is_none() {
                let content = pyunicode::strip(&text[2..text.len() - 2]).to_string();
                let token_type = if token_start == VARIABLE_TAG_START {
                    TokenType::Var
                } else {
                    // Django writes this as a bare `assert`, which is live unless
                    // the interpreter runs under `-O`. `debug_assert!` would
                    // compile out under `--release`, which is how this extension
                    // is actually built, so a real assert is the faithful port.
                    assert_eq!(token_start, COMMENT_TAG_START);
                    TokenType::Comment
                };
                return Token {
                    token_type,
                    contents: content,
                    lineno,
                    position,
                };
            }
        }

        Token {
            token_type: TokenType::Text,
            contents: text.to_string(),
            lineno,
            position,
        }
    }
}

/// Whether a block's contents open a verbatim block.
///
/// Django tests `content[:9] in ("verbatim", "verbatim ")`, i.e. the contents
/// are exactly `verbatim` or begin with `verbatim `.
fn is_verbatim_start(content: &str) -> bool {
    match content.strip_prefix("verbatim") {
        Some(rest) => rest.is_empty() || rest.starts_with(' '),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One token's observable fields, for compact assertions.
    type Lexed = (TokenType, String, usize);
    type LexedDebug = (TokenType, String, usize, Option<(usize, usize)>);

    fn lex(source: &str) -> Vec<Lexed> {
        Lexer::new(source)
            .tokenize()
            .into_iter()
            .map(|t| (t.token_type, t.contents, t.lineno))
            .collect()
    }

    fn lex_debug(source: &str) -> Vec<LexedDebug> {
        Lexer::debug(source)
            .tokenize()
            .into_iter()
            .map(|t| (t.token_type, t.contents, t.lineno, t.position))
            .collect()
    }

    #[test]
    fn plain_text() {
        assert_eq!(
            lex("hello"),
            vec![(TokenType::Text, "hello".to_string(), 1)]
        );
    }

    #[test]
    fn variable_trims_whitespace() {
        assert_eq!(
            lex("{{   spaced   }}"),
            vec![(TokenType::Var, "spaced".to_string(), 1)],
        );
    }

    #[test]
    fn block_and_comment() {
        assert_eq!(
            lex("{% if x %}{# note #}"),
            vec![
                (TokenType::Block, "if x".to_string(), 1),
                (TokenType::Comment, "note".to_string(), 1),
            ],
        );
    }

    #[test]
    fn lineno_counts_newlines_in_text() {
        assert_eq!(
            lex("a\nb\n{{ x }}"),
            vec![
                (TokenType::Text, "a\nb\n".to_string(), 1),
                (TokenType::Var, "x".to_string(), 3),
            ],
        );
    }

    #[test]
    fn lineno_counts_newlines_inside_a_text_run_only() {
        // Two newlines in the leading text; the tag itself may not span lines.
        assert_eq!(
            lex("\n\n{% if x %}\n{{ y }}"),
            vec![
                (TokenType::Text, "\n\n".to_string(), 1),
                (TokenType::Block, "if x".to_string(), 3),
                (TokenType::Text, "\n".to_string(), 3),
                (TokenType::Var, "y".to_string(), 4),
            ],
        );
    }

    #[test]
    fn non_debug_lexer_records_no_positions() {
        assert!(Lexer::new("{{ x }}")
            .tokenize()
            .iter()
            .all(|t| t.position.is_none()));
    }

    #[test]
    fn debug_lexer_records_positions() {
        assert_eq!(
            lex_debug("a{{ x }}b"),
            vec![
                (TokenType::Text, "a".to_string(), 1, Some((0, 1))),
                (TokenType::Var, "x".to_string(), 1, Some((1, 8))),
                (TokenType::Text, "b".to_string(), 1, Some((8, 9))),
            ],
        );
    }

    #[test]
    fn empty_segments_still_toggle_tag_state() {
        // Adjacent tags produce empty text segments between them, and the
        // in_tag toggle must flip on those too. A lexer that toggles only on
        // non-empty segments reads `{% a %}` here as text.
        assert_eq!(
            lex("{{ a }}{% b %}{# c #}"),
            vec![
                (TokenType::Var, "a".to_string(), 1),
                (TokenType::Block, "b".to_string(), 1),
                (TokenType::Comment, "c".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_contents_are_text_including_delimiters() {
        assert_eq!(
            lex("{% verbatim %}{{ x }}{% endverbatim %}"),
            vec![
                (TokenType::Block, "verbatim".to_string(), 1),
                (TokenType::Text, "{{ x }}".to_string(), 1),
                (TokenType::Block, "endverbatim".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_makes_other_blocks_text() {
        assert_eq!(
            lex("{% verbatim %}{% if x %}{% endverbatim %}"),
            vec![
                (TokenType::Block, "verbatim".to_string(), 1),
                (TokenType::Text, "{% if x %}".to_string(), 1),
                (TokenType::Block, "endverbatim".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_named_block_requires_exact_match() {
        assert_eq!(
            lex("{% verbatim foo %}{% endverbatim bar %}{% endverbatim foo %}"),
            vec![
                (TokenType::Block, "verbatim foo".to_string(), 1),
                (TokenType::Text, "{% endverbatim bar %}".to_string(), 1),
                (TokenType::Block, "endverbatim foo".to_string(), 1),
            ],
        );
    }

    #[test]
    fn verbatim_state_is_cleared() {
        let mut lexer = Lexer::new("{% verbatim %}{% endverbatim %}");
        lexer.tokenize();
        assert_eq!(lexer.verbatim(), None);
    }

    #[test]
    fn verbatim_prefix_that_is_not_a_verbatim_tag() {
        // `verbatimx` must not open a verbatim block.
        assert_eq!(
            lex("{% verbatimx %}"),
            vec![(TokenType::Block, "verbatimx".to_string(), 1)],
        );
    }

    #[test]
    fn unterminated_verbatim_leaves_state_set() {
        let mut lexer = Lexer::new("{% verbatim %}never closed");
        let tokens = lexer.tokenize();
        assert_eq!(tokens.len(), 2);
        assert_eq!(lexer.verbatim(), Some("endverbatim"));
    }
}
