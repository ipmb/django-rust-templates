//! `{% lorem %}` — `defaulttags.lorem` (`defaulttags.py:1134-1179`).
//!
//! # Three optional bits, consumed from the back, and a default that is text
//!
//! `{% lorem [count] [method] [random] %}` is parsed by popping
//! (`:1160-1175`): `random` if it is last, then a method if the new last bit is
//! one of `w`/`p`/`b`, then a count if more than the tag name is left. Two
//! consequences, both measured on the pinned tree:
//!
//! - the **count defaults to the string `"1"`**, compiled as a
//!   `FilterExpression` like any other count (`:1171-1175`) — so
//!   `LoremNode.count` is never absent and the default goes through the same
//!   compiler as a written argument.
//! - `{% lorem w %}` reads `w` as the **method**, not as the count, because the
//!   method pop happens first: a variable named `w` is not reachable here. What
//!   is left is the tag name alone, so the count is `"1"`.
//!
//! The `len(bits) != 1` check at the end (`:1176-1177`) is what remains after
//! the three pops, and it is reachable — `{% lorem 3 p nope %}` and
//! `{% lorem 1 2 %}` both leave two bits and both raise.
//!
//! # `common` is a bool, not a flag on the presence of `random`
//!
//! `common = bits[-1] != "random"` (`:1160`), so `{% lorem %}` — whose last bit
//! is the tag name — is `True` without any pop happening. The negation is easy
//! to invert by accident and the field feeds `words`/`paragraphs` at render
//! time.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Includes the tag name, which is `bits[0]` and is never popped — the
    // `len(bits) != 1` check below is exactly "nothing but the tag name is
    // left".
    let mut bits = split_contents(token)?;
    let tagname = bits[0].clone();

    let common = bits[bits.len() - 1] != "random";
    if !common {
        bits.pop();
    }

    // Cloned before the match so the arm can pop: a borrow of `bits` as the
    // scrutinee would outlive the mutation.
    let last = bits[bits.len() - 1].clone();
    let method = match last.as_str() {
        "w" | "p" | "b" => {
            bits.pop();
            last
        }
        _ => "b".to_string(),
    };

    // `"1"` and not `1`: the default is compiled like a written argument, and
    // the descriptor carries the resulting `FilterExpression`.
    let count_source = if bits.len() > 1 {
        bits.pop().expect("checked by the length test")
    } else {
        "1".to_string()
    };
    let count = parser.compile_filter(&count_source)?;

    if bits.len() != 1 {
        return Err(Error::syntax(format!(
            "Incorrect format for {} tag",
            parser.py_repr(&tagname),
        )));
    }

    Ok(NodeDesc::new("LoremNode")
        .attr("count", count)
        .attr("method", AttrValue::Str(method))
        .attr("common", AttrValue::Bool(common)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    fn count_source(node: &NodeDesc) -> &str {
        let AttrValue::FilterExpr(expression) = attr(node, "count") else {
            panic!("count is a FilterExpr");
        };
        expression.token.as_str()
    }

    fn method(node: &NodeDesc) -> &str {
        let AttrValue::Str(method) = attr(node, "method") else {
            panic!("method is a Str");
        };
        method.as_str()
    }

    #[test]
    fn lorem_defaults_every_bit() {
        let node = compile("{% lorem %}");
        assert_eq!(node.class_name, "LoremNode");
        // The default count is the *string* "1", compiled like an argument.
        assert_eq!(count_source(&node), "1");
        assert_eq!(method(&node), "b");
        assert_eq!(attr(&node, "common"), &AttrValue::Bool(true));
    }

    #[test]
    fn lorem_takes_the_three_bits_in_order() {
        let node = compile("{% lorem 2 w random %}");
        assert_eq!(count_source(&node), "2");
        assert_eq!(method(&node), "w");
        assert_eq!(attr(&node, "common"), &AttrValue::Bool(false));
    }

    #[test]
    fn a_lone_w_is_the_method_not_the_count() {
        // The method pop runs first, so `w` is never reachable as a variable
        // name here and the count falls back to "1".
        let node = compile("{% lorem w %}");
        assert_eq!(count_source(&node), "1");
        assert_eq!(method(&node), "w");
        assert_eq!(attr(&node, "common"), &AttrValue::Bool(true));
    }

    #[test]
    fn random_alone_leaves_the_other_two_defaults() {
        let node = compile("{% lorem random %}");
        assert_eq!(count_source(&node), "1");
        assert_eq!(method(&node), "b");
        assert_eq!(attr(&node, "common"), &AttrValue::Bool(false));
    }

    #[test]
    fn lorem_rejects_the_shapes_that_leave_too_much() {
        for source in ["{% lorem 3 p nope %}", "{% lorem 1 2 %}"] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax("Incorrect format for 'lorem' tag".to_string()),
                "{source}",
            );
        }
    }
}
