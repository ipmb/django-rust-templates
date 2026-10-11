//! `{% extends %}` — `django.template.loader_tags.do_extends` (`loader_tags.py:292`).
//!
//! Reaches both halves of the origin seam: `parser.origin.template_name`
//! (`loader_tags.py:305`, via [`Parser::origin_template_name`]) and
//! [`Parser::compile_filter`], which is how the parent name may be a variable as
//! readily as a literal. Its `CompileFn` also **consumes the rest of the template**
//! — `nodelist = parser.parse()` with no `parse_until` (`loader_tags.py:307`).

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::relative::construct_relative_path;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // `split_contents()`, not `contents.split()`: Django takes a *variable* here as
    // readily as a literal (`loader_tags.py:296-299`); quoting is `compile_filter`'s job.
    let mut bits = split_contents(token)?;
    if bits.len() != 2 {
        // `bits.len() != 2` admits 0, where Django's `% bits[0]` would raise
        // `IndexError`; `Parser::parse` raises `Empty block tag on line %d` first
        // (`base.py:560-563`), so nothing with zero bits reaches a compile function.
        let command = bits
            .first()
            .expect("parse rejects an empty block tag before a compile function runs");
        return Err(Error::syntax(format!("'{command}' takes one argument")));
    }

    // The **raw** origin attribute, never a rendered repr: the messages interpolate
    // it with `%s` and the recursion check compares it. `None` is Django's real
    // "unknown origin" case, not a missing argument — see `relative.rs`.
    bits[1] = construct_relative_path(parser.origin_template_name(), &bits[1], false)?;

    // `ExtendsNode.get_parent` calls `self.parent_name.resolve(context)`
    // (`loader_tags.py:118`) and tests `parent_name.filters` and `parent_name.var`
    // against `Variable` for its error message (`loader_tags.py:121`) — all `FilterExpression`
    // internals, so only a real host expression answers them; this is the seam.
    // The name reaches it **after** `construct_relative_path`, Django's order.
    let parent_name = parser.compile_filter(&bits[1])?;

    // Unbounded: `{% extends %}` ends its template, so everything after it —
    // including any second `{% extends %}` — becomes this node's body, not a sibling.
    let nodelist = parser.parse(&[])?;

    // The "more than once" check scans the nodelist just parsed, not the one this node
    // joins: the second one is *inside* the first, so no sibling scan sees the pair.
    if !nodelist.get_nodes_by_type("ExtendsNode").is_empty() {
        return Err(Error::syntax(format!(
            "'{}' cannot appear more than once in the same template",
            bits[0]
        )));
    }

    // `.first()` is `ExtendsNode.must_be_first = True` (`loader_tags.py:87`), the only
    // built-in that sets it; `extend_nodelist` reads it when this node is appended, not
    // here. It bites on the shapes this tag's unbounded parse leaves reachable:
    // `{{ x }}{% extends "a" %}` raises, while `x{% extends "a" %}` does not — a leading
    // `TextNode` is not `contains_nontext`.
    Ok(NodeDesc::new("ExtendsNode")
        .attr("parent_name", parent_name)
        .nodelist("nodelist", nodelist)
        .first())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::desc::AttrValue;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, nodelist};

    #[test]
    fn extends_carries_the_parent_as_a_compiled_filter_expression() {
        let node = compile("{% extends \"base.html\" %}");
        assert_eq!(node.class_name, "ExtendsNode");
        let AttrValue::FilterExpr(expression) = attr(&node, "parent_name") else {
            panic!("parent_name is a FilterExpr");
        };
        // The token is the *quoted* literal: `compile_filter` sees what
        // `construct_relative_path` returned, which keeps the original quoting.
        assert_eq!(expression.token, "\"base.html\"");
        let (is_var, _) = &expression.var;
        assert!(!is_var, "a literal, not a lookup");
        assert_eq!(node.nodelists[0].0, "nodelist");
        // The body is a real, empty nodelist — `{% extends %}` swallows to EOF and there
        // is nothing after it; `ExtendsNode.__init__` reads `nodelist.get_nodes_by_type`.
        assert_eq!(node.nodelists[0].1.nodes.len(), 0);
    }

    #[test]
    fn extends_takes_a_variable_as_readily_as_a_literal() {
        // The parent is a variable, not a literal (`loader_tags.py:296-299`) — the reason
        // for `split_contents()`.
        let node = compile("{% extends parent %}");
        let AttrValue::FilterExpr(expression) = attr(&node, "parent_name") else {
            panic!("parent_name is a FilterExpr");
        };
        assert_eq!(expression.token, "parent");
        let (is_var, _) = &expression.var;
        assert!(is_var, "`parent` is a lookup");
    }

    #[test]
    fn extends_swallows_the_rest_of_the_template() {
        // The unbounded `parser.parse()`: the trailing text and the block are both inside
        // the `ExtendsNode`'s body, so the top level holds one node.
        let node = compile("{% extends \"b.html\" %}tail{% block a %}x{% endblock %}");
        assert_eq!(node.class_name, "ExtendsNode");
        let body = &node.nodelists[0].1;
        assert_eq!(
            body.nodes.len(),
            2,
            "a TextNode and the BlockNode, both this node's children",
        );
        assert_eq!(body.nodes[0].class_name, "TextNode");
        assert_eq!(body.nodes[1].class_name, "BlockNode");
    }

    #[test]
    fn a_second_extends_is_rejected_from_inside_the_first() {
        // The pair is never two siblings — the unbounded parse makes the second a child
        // of the first, and the check scans that child nodelist. The attached **token** is
        // the *first* `{% extends %}`, the frame the check ran in, not the second one.
        let error = failure("{% extends \"a\" %}{% extends \"b\" %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "'extends' cannot appear more than once in the same template".to_string()
            ),
        );
        let token = error.token.expect("the tag's own token is attached");
        assert_eq!(
            token.contents, "extends \"a\"",
            "the token is the *first* extends — the frame the check ran in",
        );
    }

    #[test]
    fn extends_takes_exactly_one_argument() {
        assert_eq!(
            failure("{% extends %}").kind,
            ErrorKind::Syntax("'extends' takes one argument".to_string()),
        );
        assert_eq!(
            failure("{% extends \"a\" \"b\" %}").kind,
            ErrorKind::Syntax("'extends' takes one argument".to_string()),
        );
    }

    #[test]
    fn extends_must_be_the_first_non_text_node() {
        // `must_be_first`: both sides of the `contains_nontext` test, since only one raises.
        assert_eq!(
            failure("{{ x }}{% extends \"a\" %}").kind,
            ErrorKind::Syntax(
                "{% extends \"a\" %} must be the first tag in the template.".to_string()
            ),
        );
        assert_eq!(
            failure("{% block b %}y{% endblock %}{% extends \"a\" %}").kind,
            ErrorKind::Syntax(
                "{% extends \"a\" %} must be the first tag in the template.".to_string()
            ),
        );
        // The control: `extend_nodelist` flips `contains_nontext` only for a node that
        // is not a `TextNode`, so a leading `TextNode` compiles — "is the nodelist empty"
        // would wrongly raise. Two top-level nodes, hence reading the nodelist here.
        let tree = nodelist("x{% extends \"a\" %}");
        assert_eq!(
            tree.nodes
                .iter()
                .map(|node| node.class_name.as_str())
                .collect::<Vec<_>>(),
            vec!["TextNode", "ExtendsNode"],
            "a leading TextNode does not make the extends non-first",
        );
    }

    #[test]
    fn extends_resolves_a_relative_name_against_the_origin() {
        // The one non-error path into `construct_relative_path`: the resolved name is
        // what `compile_filter` sees, and so the FilterExpression's token.
        let mut parser = Parser::new(
            crate::lexer::Lexer::new("{% extends './base.html' %}").tokenize(),
            &crate::parser::host::NoHost,
        )
        .with_origin_template_name("sub/index.html".to_string());
        let nodelist = parser.parse(&[]).expect("expected a successful parse");
        let AttrValue::FilterExpr(expression) = attr(&nodelist.nodes[0], "parent_name") else {
            panic!("parent_name is a FilterExpr");
        };
        // `dirname("sub/index.html")` is `sub`, so the resolved name keeps the directory:
        // expecting `base.html` would pass while resolving against the wrong thing.
        assert_eq!(expression.token, "\"sub/base.html\"");
    }

    #[test]
    fn extends_refuses_a_relative_name_with_no_origin() {
        // No origin, so this is what a bare `from_string` sees. The message names the
        // *original* argument unquoted (`loader_tags.py:264-265`), unlike the tag's
        // other two messages.
        assert_eq!(
            failure("{% extends './base.html' %}").kind,
            ErrorKind::Syntax(
                "The relative path './base.html' cannot be evaluated due to an \
                 unknown template origin."
                    .to_string()
            ),
        );
    }

    #[test]
    fn get_nodes_by_type_reaches_a_node_inside_an_if_branch() {
        // `IfNode` emits its branch lists as **empty-named** nodelists (`desc.rs`,
        // "positional only"), and Django reaches them through `nodelist`, a property over
        // `conditions_nodelists` (`defaulttags.py:314-320`). A walk that descended only
        // into *named* nodelists would silently miss every `{% block %}` in an `{% if %}`.
        //
        // `{% block %}`, not `{% extends %}`, in the branch: an `{% extends %}` inside any
        // tag with an end tag eats the end tag and raises `Invalid block tag`, measured.
        let tree = nodelist("{% if x %}{% block a %}{% endblock %}{% endif %}");
        assert_eq!(tree.get_nodes_by_type("BlockNode").len(), 1);
        assert!(tree.get_nodes_by_type("ExtendsNode").is_empty());
        // The IfNode itself is a sibling of nothing, and its two branch lists are both
        // unnamed, which is the shape being walked.
        assert_eq!(tree.get_nodes_by_type("IfNode").len(), 1);
    }

    #[test]
    fn get_nodes_by_type_is_pre_order_and_in_document_order() {
        // Order is the contract: `ExtendsNode.__init__` folds the result into
        // `{n.name: n …}` (`loader_tags.py:94`), and `describe` descends that dict with
        // `.items()` unsorted, so a children-first or sorted walk would materialise
        // differently-ordered `blocks`. Names are non-alphabetical and distinct: `{% block %}`'s
        // duplicate-name check refuses a repeat within one parse, so only insertion order is
        // observable from a Rust-built tree.
        let tree = nodelist(
            "{% block z %}1{% endblock %}{% block a %}2{% endblock %}{% block m %}3{% endblock %}",
        );
        let names: Vec<&str> = tree
            .get_nodes_by_type("BlockNode")
            .iter()
            .map(|node| match attr(node, "name") {
                AttrValue::Str(name) => name.as_str(),
                other => panic!("BlockNode.name is not a Str: {other:?}"),
            })
            .collect();
        assert_eq!(names, vec!["z", "a", "m"]);
        // `ExtendsNode.__init__` folds this list into an insertion-ordered dict
        // (`loader_tags.py:94`), so the assertion above is the assertion about
        // `self.blocks` too.
    }

    #[test]
    fn get_nodes_by_type_walks_a_nested_nodelist_depth_first() {
        // Pre-order across depth: the outer block precedes the inner one, which a
        // breadth-first or children-first walk gets wrong.
        let tree = nodelist("{% block a %}{% block b %}x{% endblock %}{% endblock %}");
        let names: Vec<&str> = tree
            .get_nodes_by_type("BlockNode")
            .iter()
            .map(|node| match attr(node, "name") {
                AttrValue::Str(name) => name.as_str(),
                other => panic!("BlockNode.name is not a Str: {other:?}"),
            })
            .collect();
        assert_eq!(names, vec!["a", "b"]);
    }
}
