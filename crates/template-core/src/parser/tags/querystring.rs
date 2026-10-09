//! `{% querystring %}` — `defaulttags.querystring` (`defaulttags.py:1286`).
//!
//! It is registered by `simple_tag` rather than `tag`
//! (`defaulttags.py:1286`), and `simple_tag`'s generated compile function
//! passes `parser.compile_filter` into `parse_bits` (`library.py:454`) — so
//! it is in the FilterExpression bucket, with M5's tags and not with M2's nine.
//!
//! **M6, not M5, and the reason is the decorator.** Being the only `simple_tag`
//! in `defaulttags.py` (the other 25 registrations are `@register.tag`), parsing
//! it needs three things all of which belong to M6's `Library` machinery:
//! `SimpleNode` in `NODE_CLASSES`, a host handle to the Python callable, and
//! `parse_bits` driven by that callable's signature. A bespoke node would fail
//! parity, because Django's own tree holds a `SimpleNode` and the comparison is
//! by class name. So M5's scope subtracts it — `tests/test_tag_inventory.py`'s
//! `M5_TAGS` is derived as `NEEDS_FILTER_EXPRESSION - {extends, include,
//! querystring}` — and this stub is M6's to replace.
//!
//! The body is a stub on purpose: the registry is written once and in full
//! (§10.4), so a milestone replaces this file's body and edits no shared file.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::registry::unimplemented;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Err(unimplemented("querystring", "M6"))
}
