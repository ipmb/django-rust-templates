//! Parsing, following `django.template.base.Parser`.

pub mod desc;

pub use desc::{AttrValue, NodeDesc, NodeListDesc, NodeRef, TokenDesc, TEXT_NODE};
