//! `{% firstof %}` — `defaulttags.firstof` (`defaulttags.py:758-804`).
//!
//! # `as` is read from the end, before anything is compiled
//!
//! `if len(bits) >= 2 and bits[-2] == "as"` (`defaulttags.py:801`). Two
//! consequences that a port written from the documentation gets wrong, both
//! measured on the pinned tree:
//!
//! - `{% firstof a as b c %}` has no `asvar`, because `bits[-2]` is `b`. Only a
//!   *trailing* `as` counts, and `as` and `c` are compiled as ordinary
//!   variables — `{% firstof a as v w %}` is four variables named `a`, `as`,
//!   `v`, `w`.
//! - `{% firstof as v %}` is valid and has `asvar == "v"` with *no* variables:
//!   the argument-count check at `defaulttags.py:798` runs on `bits` **before**
//!   the `as` is stripped, so `['as', 'v']` is two arguments and then becomes
//!   none.
//!
//! # The attribute is `variables`, the Python attribute is `vars`
//!
//! `FirstOfNode.__init__(self, variables, asvar=None)` sets
//! `self.vars = variables` (`defaulttags.py:149-150`). The descriptor carries
//! the **parameter** name, because the materialiser constructs `cls(**kwargs)`;
//! a descriptor keyed `"vars"` would raise
//! `TypeError: unexpected keyword argument` rather than build a node with a
//! wrong attribute, which is at least loud.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Django's `token.split_contents()[1:]`. The tag name is always present:
    // `Parser::parse` rejects an empty block tag before it dispatches.
    let mut bits = split_contents(token)?;
    bits.remove(0);

    if bits.is_empty() {
        return Err(Error::syntax(
            "'firstof' statement requires at least one argument",
        ));
    }

    // `asvar` is emitted even when it is absent, as `AttrValue::None`: Django
    // always passes the second positional (`defaulttags.py:804`), so `__init__`
    // always sets the attribute, and `describe` compares `vars(node)`.
    let asvar = if bits.len() >= 2 && bits[bits.len() - 2] == "as" {
        let name = bits[bits.len() - 1].clone();
        bits.truncate(bits.len() - 2);
        AttrValue::Str(name)
    } else {
        AttrValue::None
    };

    let mut variables = Vec::with_capacity(bits.len());
    for bit in &bits {
        variables.push(parser.compile_filter(bit)?);
    }

    Ok(NodeDesc::new("FirstOfNode")
        .attr("variables", AttrValue::List(variables))
        .attr("asvar", asvar))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    fn variables(node: &NodeDesc) -> &Vec<AttrValue> {
        let AttrValue::List(values) = attr(node, "variables") else {
            panic!("variables is a List");
        };
        values
    }

    fn names(node: &NodeDesc) -> Vec<&str> {
        variables(node)
            .iter()
            .map(|value| {
                let AttrValue::FilterExpr(expression) = value else {
                    panic!("each variable is a FilterExpr");
                };
                expression.token.as_str()
            })
            .collect()
    }

    #[test]
    fn firstof_compiles_every_bit_after_the_tag_name() {
        let node = compile("{% firstof a b c %}");
        assert_eq!(node.class_name, "FirstOfNode");
        assert_eq!(names(&node), vec!["a", "b", "c"]);
        assert_eq!(attr(&node, "asvar"), &AttrValue::None);
    }

    #[test]
    fn firstof_reads_as_from_the_end_only() {
        // The documented form takes the last two bits.
        let trailing = compile("{% firstof a b as v %}");
        assert_eq!(names(&trailing), vec!["a", "b"]);
        assert_eq!(attr(&trailing, "asvar"), &AttrValue::Str("v".to_string()));

        // `as` in any other position is just another variable — including in
        // the *second-to-last* slot, which is the case the check tests.
        let middle = compile("{% firstof a as b c %}");
        assert_eq!(names(&middle), vec!["a", "as", "b", "c"]);
        assert_eq!(attr(&middle, "asvar"), &AttrValue::None);
    }

    #[test]
    fn firstof_accepts_a_lone_as_pair_because_the_count_check_runs_first() {
        // Measured: `{% firstof as v %}` parses, with no variables at all.
        let node = compile("{% firstof as v %}");
        assert!(names(&node).is_empty());
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("v".to_string()));
    }

    #[test]
    fn firstof_requires_at_least_one_bit() {
        assert_eq!(
            failure("{% firstof %}").kind,
            ErrorKind::Syntax("'firstof' statement requires at least one argument".to_string()),
        );
    }

    #[test]
    fn firstof_keeps_a_quoted_fallback_whole() {
        // `split_contents`, not `split`: the phrase is one bit, and its quotes
        // survive into the compiled source.
        let node = compile("{% firstof a \"fall back\" %}");
        assert_eq!(names(&node), vec!["a", "\"fall back\""]);
    }
}
