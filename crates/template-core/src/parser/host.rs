//! The host seam: the tags the parser must ask Python to compile.
//!
//! Ports the tag half of §5.2's `ExtensionHost`. It is two methods rather than
//! one because `Parser.parse` tests membership *before* calling — a single
//! `Option`-returning method would have to distinguish "no such tag" from "the
//! tag failed", which the error type does not.
//!
//! `template-core` never sees a Python object here: the `NodeDesc` that comes
//! back is already plain data, and the host is the only thing that touches
//! Python.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::token::Token;

pub trait Host {
    /// Whether `command` is a tag the host can compile.
    ///
    /// Called for every block tag that Rust has no compile function for, which
    /// at M2 is every block tag until `builtin_tag` answers.
    fn has_tag(&self, command: &str) -> bool;

    /// Compile `command`, given the token dispatch matched on.
    ///
    /// Called only after `has_tag` returned true for the same `command`.
    fn call_tag(&self, command: &str, token: &Token) -> Result<NodeDesc, Error>;
}

/// A host with no tags at all.
///
/// Used by `_native.parse` until M6 supplies a real registry, and by every Rust
/// unit test that is not specifically testing dispatch order.
pub struct NoHost;

impl Host for NoHost {
    fn has_tag(&self, _command: &str) -> bool {
        false
    }

    fn call_tag(&self, _command: &str, _token: &Token) -> Result<NodeDesc, Error> {
        // Unreachable through `Parser::parse`, which consults `has_tag` first.
        // A panic here rather than a silent `Err` so that a future dispatch bug
        // is loud; `parse` never lets it out of a Rust test.
        unreachable!("NoHost::call_tag is never reached: has_tag always returns false")
    }
}
