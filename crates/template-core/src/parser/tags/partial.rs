//! `{% partial %}` — `defaulttags.partial_func` (`defaulttags.py:1268`).
//!
//! Shared with `partialdef`: the two tags use one parser-side registry of
//! partial definitions, so they land in the same milestone.
//!
//! **The node is host-side, and that is the tag.** `partial_func`'s productive
//! arm builds `DeferredSubDict(extra_data, "partials")` — a *live view over the
//! parser's dict*, not a copy (`defaulttags.py:1279-1280`) — and hands it to
//! `PartialNode`, so the compiled node closes over parse state a `NodeDesc`
//! cannot represent at all. The argument check stays here because it raises
//! *before* that call; everything after it crosses the seam
//! (`Host::partial_node`), and the registry's own note (`crates/template-core/src/parser/registry.rs:117-134`)
//! is where that rule and its reason are written down. That note read "flagged,
//! not resolved" when this file landed, because it predates the task that
//! placed the pair and described the choice rather than its outcome; the task
//! that emptied the stub list is where it was rewritten, and it now records the
//! choice as taken. What this file is: the first half of that choice, made.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Django's match has one productive arm and a wildcard whose body is
    // `raise TemplateSyntaxError("'partial' tag requires a single argument")`
    // (`defaulttags.py:1277-1283`), so the *one* condition is "exactly two
    // bits" and the same message covers both failures. Slicing rather than a
    // length check keeps that shape visible: a reader sees one arm, not a
    // comparison that has to be read twice to see it is the wildcard's.
    let bits = split_contents(token)?;
    match bits.as_slice() {
        [_, name] => Ok(NodeDesc::host(parser.partial_node(name)?)),
        _ => Err(Error::syntax("'partial' tag requires a single argument")),
    }
}

#[cfg(test)]
mod tests {
    use crate::parser::desc::HOST_NODE;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{self, PartialHost};

    #[test]
    fn one_argument_builds_a_host_node() {
        // The whole tag. `NodeDesc::host` and not a `NODE_CLASSES` row: the
        // mapping is live host state, so the object can only be built host-side
        // — and `PartialHost`'s `partial_node` returns a fixed handle, which is
        // all this row needs to see the *route*.
        let host = PartialHost::default();
        let node = support::parse_with("{% partial p %}", &host);
        assert_eq!(node.nodes[0].class_name, HOST_NODE);
    }

    #[test]
    fn no_argument_is_djangos_message() {
        // `ErrorKind::Syntax` and the message together, not `.message()` alone:
        // the kind is what the shell converts to `TemplateSyntaxError`, and a
        // row asserting only the text would pass on an `Internal`.
        assert_eq!(
            support::failure("{% partial %}").kind,
            ErrorKind::Syntax("'partial' tag requires a single argument".to_string()),
        );
    }

    #[test]
    fn two_arguments_are_the_same_message() {
        // Django's `case _` catches both, so the same string covers "too few"
        // and "too many" — a length check spelled `!= 2` would agree on both of
        // these and differ on neither, which is why the row exists: it says the
        // *reason* is the wildcard arm, not an arity rule that happens to give
        // the same text twice.
        assert_eq!(
            support::failure("{% partial p q %}").kind,
            ErrorKind::Syntax("'partial' tag requires a single argument".to_string()),
        );
    }
}
