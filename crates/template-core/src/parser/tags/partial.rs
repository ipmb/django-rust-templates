//! `{% partial %}` — `defaulttags.partial_func` (`defaulttags.py:1268`).
//!
//! Shared with `partialdef`: the two tags use one parser-side registry of
//! partial definitions, so they land in the same milestone.
//! M13. The body is a stub on purpose: the registry is written once and
//! in full (§10.4), so M13 replaces this file's body and edits no shared
//! file.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::registry::unimplemented;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Err(unimplemented("partial", "M13"))
}
