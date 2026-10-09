//! `{% ifchanged %}` — `defaulttags.ifchanged` (`defaulttags.py:1023-1073`).
//!
//! # Two *named* nodelists, and the false one is an empty `NodeList`
//!
//! `IfChangedNode(nodelist_true, nodelist_false, *varlist)` (`:262-265`) keeps
//! both as attributes. Without an `{% else %}`, Django assigns a fresh
//! `NodeList()` (`:1057-1058`) rather than leaving it absent, so the descriptor
//! must emit `nodelist_false` as an **empty nodelist** — omitting it would build
//! a node whose `nodelist_false` is missing, and the parity harness compares
//! `vars(node)`.
//!
//! # The values are variadic, and that is a wire problem, not a naming one
//!
//! `values = [parser.compile_filter(bit) for bit in bits[1:]]` (`:1059`) is
//! splatted into the constructor's `*varlist`, which Django stores as the tuple
//! `self._varlist` (`:265`). There is no keyword for it, so `cls(**kwargs)`
//! cannot build this node — the descriptor names the attribute `_varlist` and
//! `NODE_CLASSES["IfChangedNode"]` is a factory that splices the list back into
//! position. `IfChangedNode` is the **only** variadic node among M5's twelve,
//! measured by scanning each `__init__`.
//!
//! # `parser.next_token()` reads the terminator
//!
//! `parser.parse(("else", "endifchanged"))` stops *before* the terminator and
//! prepends it back (`base.py:600-601`), so the `next_token()` at `:1054` is what
//! consumes it — the same shape `{% for %}` uses for its `empty` branch. Reading
//! the terminator here rather than inside `parse` is what lets the tag tell
//! `else` from `endifchanged`.
//!
//! No message of its own: every failure comes from `parse`/`next_token`, which
//! M3 already pins.

use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Django computes `bits` first (`:1052`), before the body is parsed, so a
    // malformed tag argument would report before any nested error. There is no
    // argument check here — `bits[1:]` may be empty.
    let bits = split_contents(token)?;

    let nodelist_true = parser.parse(&["else", "endifchanged"])?;
    let terminator = parser
        .next_token()
        .expect("parse returns only after prepending its terminator");
    let nodelist_false = if terminator.contents == "else" {
        let nodelist = parser.parse(&["endifchanged"])?;
        parser.delete_first_token();
        nodelist
    } else {
        // Django's `NodeList()` (`:1058`), not `None`.
        NodeListDesc::default()
    };

    let mut values = Vec::with_capacity(bits.len().saturating_sub(1));
    for bit in &bits[1..] {
        values.push(parser.compile_filter(bit)?);
    }

    Ok(NodeDesc::new("IfChangedNode")
        // Django's own attribute name for the splatted tuple, so the factory and
        // the descriptor agree on one spelling.
        .attr("_varlist", AttrValue::List(values))
        .nodelist("nodelist_true", nodelist_true)
        .nodelist("nodelist_false", nodelist_false))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::tags::support::{compile, texts};

    fn nodelist(node: &NodeDesc, name: &str) -> Vec<String> {
        let (_, nodelist) = node
            .nodelists
            .iter()
            .find(|(key, _)| key == name)
            .unwrap_or_else(|| panic!("{name} is a named nodelist"));
        texts(nodelist)
    }

    fn values(node: &NodeDesc) -> Vec<String> {
        let AttrValue::List(values) = node
            .attrs
            .iter()
            .find(|(key, _)| key == "_varlist")
            .map(|(_, value)| value)
            .expect("_varlist is emitted")
        else {
            panic!("_varlist is a List");
        };
        values
            .iter()
            .map(|value| {
                let AttrValue::FilterExpr(expression) = value else {
                    panic!("each value is a FilterExpr");
                };
                expression.token.clone()
            })
            .collect()
    }

    #[test]
    fn ifchanged_has_no_values_and_an_empty_false_branch() {
        let node = compile("{% ifchanged %}x{% endifchanged %}");
        assert_eq!(node.class_name, "IfChangedNode");
        assert_eq!(nodelist(&node, "nodelist_true"), vec!["x"]);
        // Emitted, not omitted: Django assigns an empty `NodeList()` and the
        // parity harness compares the attribute set.
        assert_eq!(nodelist(&node, "nodelist_false"), Vec::<String>::new());
        assert!(values(&node).is_empty());
    }

    #[test]
    fn ifchanged_keeps_both_branches_apart() {
        let node = compile("{% ifchanged %}x{% else %}y{% endifchanged %}");
        assert_eq!(nodelist(&node, "nodelist_true"), vec!["x"]);
        assert_eq!(nodelist(&node, "nodelist_false"), vec!["y"]);
    }

    #[test]
    fn ifchanged_compiles_every_value_after_the_tag_name() {
        let node = compile("{% ifchanged a b %}x{% else %}y{% endifchanged %}");
        assert_eq!(values(&node), vec!["a", "b"]);
        // The `else` split does not disturb the values, which come from the
        // tag's own bits.
        assert_eq!(nodelist(&node, "nodelist_false"), vec!["y"]);
    }

    #[test]
    fn the_second_else_is_an_invalid_block_tag() {
        // Django's `parse(("else", "endifchanged"))` stops at the first `else`,
        // and the body then expects `endifchanged`, so the second `else` is
        // rejected by the *parser* rather than by any check in this tag.
        // Measured wording on the pinned tree.
        let error = crate::parser::tags::support::failure(
            "{% ifchanged %}{% else %}{% else %}{% endifchanged %}",
        );
        assert_eq!(
            error.kind,
            crate::parser::error::ErrorKind::Syntax(
                "Invalid block tag on line 1: 'else', expected 'endifchanged'. \
                 Did you forget to register or load this tag?"
                    .to_string()
            ),
        );
    }
}
