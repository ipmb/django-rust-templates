//! The parser's error protocol, ported from `Parser.error` and its callers.

use crate::parser::desc::TokenDesc;

/// What the parser failed with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ErrorKind {
    /// A syntax error. Rust owns the message (§6); the exception *object* is
    /// Python's (design decision 2).
    Syntax(String),
    /// An exception raised by a host tag.
    ///
    /// M2 has no host tag that can fail, so the payload is the host's message.
    /// M4 must replace it with the host's exception object held **by identity**:
    /// flattening a Python exception to a string loses `_culprit_node` and its
    /// `__traceback__`, which the annotation protocol depends on (§5.2). An
    /// enum variant that must change is preferable to a `String` that looks
    /// settled.
    Host(String),
    /// `Token::split_contents` reached Django's `next(bits)` on an exhausted
    /// iterator — an unterminated `_("…` marker.
    ///
    /// Django lets `StopIteration` propagate out of the parse (only `Exception`
    /// is caught, to attach `.token`), so this is **not** a `Syntax`: the shell
    /// raises `StopIteration`, matching what M1's `_native.split_contents`
    /// already does at the same boundary.
    UnterminatedMarker,
    /// Something M2 deliberately does not do yet. Removed by the milestone that
    /// supplies it, so the list of these is the milestone's remaining work.
    Unimplemented(String),
    /// An invariant the parser relies on was violated.
    ///
    /// Every use documents why it is believed unreachable, and it exists so a
    /// broken invariant becomes an `Error` rather than a panic unwinding
    /// through the FFI. Django's equivalent is an `IndexError`.
    Internal(String),
}

/// An error, plus the token Django's `Parser.error` attached to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    /// Set by the **first** `error()` call to see this error, never replaced.
    ///
    /// That is Django's `if not hasattr(e, "token")`, and it is what makes the
    /// **innermost** token win when a compile error unwinds through nested
    /// `parse` frames (Review Focus item 5).
    pub token: Option<TokenDesc>,
}

impl Error {
    pub fn syntax(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Syntax(message.into()),
            token: None,
        }
    }

    pub fn host(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Host(message.into()),
            token: None,
        }
    }

    pub fn unterminated_marker() -> Self {
        Self {
            kind: ErrorKind::UnterminatedMarker,
            token: None,
        }
    }

    pub fn unimplemented(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Unimplemented(message.into()),
            token: None,
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Internal(message.into()),
            token: None,
        }
    }

    /// The message, whatever kind this is.
    ///
    /// `UnterminatedMarker` has no message of its own — Python raises a bare
    /// `StopIteration` with no arguments — so it is named after its cause. Only
    /// diagnostic paths read it.
    pub fn message(&self) -> String {
        match &self.kind {
            ErrorKind::Syntax(message)
            | ErrorKind::Host(message)
            | ErrorKind::Unimplemented(message)
            | ErrorKind::Internal(message) => message.clone(),
            ErrorKind::UnterminatedMarker => "unterminated translation marker".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::token::TokenType;

    fn marker(contents: &str, lineno: usize) -> TokenDesc {
        TokenDesc {
            token_type: TokenType::Block,
            contents: contents.to_string(),
            lineno,
            position: None,
        }
    }

    #[test]
    fn constructors_start_without_a_token() {
        // Load-bearing: `error()` uses `token.is_none()` as its `hasattr` test,
        // so a constructor that pre-filled the token would make the innermost
        // rule silently never apply.
        assert_eq!(Error::syntax("a").token, None);
        assert_eq!(Error::host("a").token, None);
        assert_eq!(Error::unterminated_marker().token, None);
        assert_eq!(Error::unimplemented("a").token, None);
        assert_eq!(Error::internal("a").token, None);
    }

    #[test]
    fn message_preserves_every_kinds_text() {
        assert_eq!(Error::syntax("s").message(), "s");
        assert_eq!(Error::host("h").message(), "h");
        assert_eq!(Error::unimplemented("u").message(), "u");
        assert_eq!(Error::internal("i").message(), "i");
        assert_eq!(
            Error::unterminated_marker().message(),
            "unterminated translation marker",
        );
    }

    #[test]
    fn a_token_can_be_attached_to_any_kind() {
        // `parse` attaches the token to whatever a compile function returned,
        // including `Host` — the branch that matters at M4.
        let host_error = Error::host("refused");
        let attached = Error {
            kind: host_error.kind,
            token: Some(marker("hosty", 1)),
        };
        assert_eq!(attached.token.unwrap().contents, "hosty");
    }
}
