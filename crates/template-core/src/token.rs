//! Token types, mirroring `django.template.base.TokenType` and `Token`.

use crate::text::{smart_split, SplitError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum TokenType {
    Text = 0,
    Var = 1,
    Block = 2,
    Comment = 3,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub token_type: TokenType,
    pub contents: String,
    pub lineno: usize,
    /// Character indices into the source, or `None` for the non-debug lexer.
    pub position: Option<(usize, usize)>,
}

impl Token {
    /// Split the token's contents into arguments, keeping quoted phrases and
    /// translation-marked `_("...")` runs together.
    ///
    /// Ports `Token.split_contents` (`django/template/base.py:390-403`), which
    /// consumes a `smart_split` generator shared between the outer `for` and
    /// the inner `while`. This walks one index across both, which is the same
    /// thing: the inner loop advances the cursor the outer loop then resumes
    /// from.
    ///
    /// The inner `while` is where Django calls `next(bits)` on an exhausted
    /// generator; that surfaces here as `Err(SplitError)` rather than a panic,
    /// because a Rust iterator's exhaustion is silent.
    pub fn split_contents(&self) -> Result<Vec<String>, SplitError> {
        let bits = smart_split(&self.contents);
        let mut split = Vec::with_capacity(bits.len());
        let mut i = 0;

        while i < bits.len() {
            let mut bit = bits[i].to_string();

            if bit.starts_with("_(\"") || bit.starts_with("_('") {
                // Byte 2 is a char boundary: the guard above proved the first
                // three bytes are ASCII `_`, `(`, and a quote.
                let sentinel = format!("{})", &bit[2..3]);
                let mut trans_bit = vec![bit.clone()];

                while !bit.ends_with(&sentinel) {
                    i += 1;
                    if i >= bits.len() {
                        return Err(SplitError);
                    }
                    bit = bits[i].to_string();
                    trans_bit.push(bit.clone());
                }

                bit = trans_bit.join(" ");
            }

            split.push(bit);
            i += 1;
        }

        Ok(split)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn block(contents: &str) -> Token {
        Token {
            token_type: TokenType::Block,
            contents: contents.to_string(),
            lineno: 1,
            position: None,
        }
    }

    #[test]
    fn token_type_discriminants_match_djangos_enum() {
        // django.template.base.TokenType is TEXT=0, VAR=1, BLOCK=2, COMMENT=3.
        // These values cross the FFI boundary, so they are part of the contract.
        assert_eq!(TokenType::Text as u8, 0);
        assert_eq!(TokenType::Var as u8, 1);
        assert_eq!(TokenType::Block as u8, 2);
        assert_eq!(TokenType::Comment as u8, 3);
    }

    #[test]
    fn tokens_compare_by_value() {
        let a = Token {
            token_type: TokenType::Text,
            contents: "hi".to_string(),
            lineno: 1,
            position: None,
        };
        assert_eq!(a.clone(), a);
    }

    #[test]
    fn split_contents_keeps_translation_markers_whole() {
        // From tests/template_tests/test_parser.py:24-33.
        let token = block(r#"sometag _("Page not found") value|yesno:_("yes,no")"#);
        assert_eq!(
            token.split_contents().unwrap(),
            vec![
                "sometag".to_string(),
                r#"_("Page not found")"#.to_string(),
                r#"value|yesno:_("yes,no")"#.to_string(),
            ],
        );
    }

    #[test]
    fn split_contents_plain_token() {
        let token = block("if x > 1");
        assert_eq!(
            token.split_contents().unwrap(),
            vec![
                "if".to_string(),
                "x".to_string(),
                ">".to_string(),
                "1".to_string()
            ],
        );
    }

    #[test]
    fn split_contents_reports_an_unterminated_translation_marker() {
        // Django reaches `next(bits)` on an exhausted iterator here, which
        // raises StopIteration out of `split_contents`. Rust cannot, so the
        // condition is explicit and the shell raises StopIteration for parity.
        let token = block(r#"tag _("unterminated"#);
        assert_eq!(token.split_contents(), Err(SplitError));
    }

    #[test]
    fn split_contents_rejoins_a_multi_word_translation_marker() {
        // The inner loop must consume every bit up to the one ending in `")`,
        // then join them with single spaces.
        let token = block(r#"t _("a b c") after"#);
        assert_eq!(
            token.split_contents().unwrap(),
            vec![
                "t".to_string(),
                r#"_("a b c")"#.to_string(),
                "after".to_string()
            ],
        );
    }
}
