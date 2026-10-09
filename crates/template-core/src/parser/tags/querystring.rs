//! `{% querystring %}` — `defaulttags.querystring` (`defaulttags.py:1286`).
//!
//! It is registered by `simple_tag` rather than `tag`
//! (`defaulttags.py:1286`), and `simple_tag`'s generated compile function
//! passes `parser.compile_filter` into `parse_bits` (`library.py:454`) — so
//! it is in the FilterExpression bucket, with M5, not with M2's nine.
//! M5. The body is a stub on purpose: the registry is written once and
//! in full (§10.4), so M5 replaces this file's body and edits no shared
//! file.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::registry::unimplemented;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Err(unimplemented("querystring", "M5"))
}
