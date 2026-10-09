//! `{% regroup %}` — `defaulttags.regroup` (`defaulttags.py:1352`).
//!
//! M5. The body is a stub on purpose: the registry is written once and
//! in full (§10.4), so M5 replaces this file's body and edits no shared
//! file.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::registry::unimplemented;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Err(unimplemented("regroup", "M5"))
}
