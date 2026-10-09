//! Lexing, following `django.template.base.Lexer` and `DebugLexer`.

pub mod segments;

pub use segments::{segments, Segment};
