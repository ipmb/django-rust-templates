//! Token types, mirroring `django.template.base.TokenType` and `Token`.

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

#[cfg(test)]
mod tests {
    use super::*;

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
}
