//! Rust's compile functions for built-in tags.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

/// A tag's compile function: Django's `Parser.compile_func(self, token)`.
///
/// A plain `fn` pointer rather than a `Box<dyn Fn>`, so a table of them is a
/// `static` if it ever needs to be. The `'_` in `Parser<'_>` is a higher-ranked
/// lifetime, so one `CompileFn` value is usable with a parser of any lifetime.
pub type CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>;

/// The compile function Rust has for `command`, if it has one.
///
/// A `match` rather than a lazy map: the table is small, it is read once per
/// block tag, and a `HashMap` behind a `OnceLock` would add a synchronisation
/// point to the hot path for no benefit.
pub fn builtin_tag(_command: &str) -> Option<CompileFn> {
    // Filled in by Task 5. Until then every block tag is either a host tag or
    // an invalid one.
    None
}
