//! `{% load %}` — `defaulttags.load` (`defaulttags.py:1102`).
//!
//! Stubbed rather than absent because at M3 it *is* a built-in tag Rust has
//! no compile function for. M6 decides whether `{% load %}` is a Rust compile
//! function calling a host `add_library`, or a host tag outright — it mutates
//! the registry the host owns (§5.2), so host-owned is the likelier answer. If
//! M6 concludes that, it removes the entry from `REGISTRY` and this file: a
//! one-file edit by the agent who owns `load`, not a fan-out conflict.
//! M6. The body is a stub on purpose: the registry is written once and
//! in full (§10.4), so M6 replaces this file's body and edits no shared
//! file.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::registry::unimplemented;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Err(unimplemented("load", "M6"))
}
