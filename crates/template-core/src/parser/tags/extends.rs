//! `{% extends %}` — `django.template.loader_tags.do_extends` (`loader_tags.py:292`).
//!
//! The first built-in Rust compiles that reaches **both** halves of the origin
//! seam: `parser.origin.template_name` (`loader_tags.py:305`, read through
//! [`Parser::origin_template_name`]) and [`Parser::compile_filter`], which is
//! how the parent name may be a variable as readily as a literal.
//!
//! It is also the only built-in whose `CompileFn` ends by **consuming the rest
//! of the template** — `nodelist = parser.parse()` with no `parse_until`
//! (`loader_tags.py:307`) — and most of what looks surprising about this tag
//! follows from that one line.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::relative::construct_relative_path;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // `split_contents()`, not `contents.split()`: Django takes a *variable* as
    // readily as a literal here (`loader_tags.py:296-299`), and the quoting is
    // `compile_filter`'s business below. `{% block %}` is the opposite case —
    // its own comment says the tag "doesn't accept variable as arguments".
    let mut bits = split_contents(token)?;
    if bits.len() != 2 {
        // `bits.len() != 2` admits 0, and Django's `% bits[0]` would raise
        // `IndexError` there. `Parser::parse` splits the same contents and
        // raises `Empty block tag on line %d` first (`base.py:560-563`), so
        // nothing with zero bits reaches a compile function — the same
        // unreachable arm `do_block`'s port states and for the same reason.
        let command = bits
            .first()
            .expect("parse rejects an empty block tag before a compile function runs");
        return Err(Error::syntax(format!("'{command}' takes one argument")));
    }

    // The **raw** origin attribute, never a rendered repr: the three messages
    // interpolate it with `%s` and the recursion check compares it, and `None`
    // is Django's real "unknown origin" case rather than a missing argument —
    // see `relative.rs`.
    bits[1] = construct_relative_path(parser.origin_template_name(), &bits[1], false)?;

    // `ExtendsNode.get_parent` calls `self.parent_name.resolve(context)`
    // (`loader_tags.py:118`) and then tests `self.parent_name.filters` and
    // `self.parent_name.var` against `Variable` to build its error message
    // (`loader_tags.py:121`). All three are `FilterExpression` internals — the
    // `var` is a `Variable` object, not a string — so only a real host
    // expression answers them, and this is the seam rather than a local parse.
    //
    // **Corrected at Task 3's execution: there is no `ignore_failures=True`
    // here.** An earlier draft of this comment asserted one; `grep
    // ignore_failures` over the pinned tree finds seven call sites, all in
    // `defaulttags.py`, and none in `loader_tags.py`. The claim was invented
    // rather than measured, and it is the reason this paragraph cites a line
    // and quotes what is on it.
    // The name reaches it **after** `construct_relative_path`, which is
    // Django's order and is observable: a relative name is compiled as the
    // resolved one, re-quoted exactly when the original was.
    let parent_name = parser.compile_filter(&bits[1])?;

    // Unbounded, and that is the whole shape of this tag: `{% extends %}` ends
    // the template it appears in, so everything after it — including any second
    // `{% extends %}` — becomes this node's body rather than a sibling of it.
    let nodelist = parser.parse(&[])?;

    // So the "more than once" check is a scan of the nodelist just parsed, and
    // not of the nodelist this node is about to be pushed into: the second one
    // is *inside* the first, and nothing that looks at siblings will ever see
    // the pair. Django's own text, with `bits[0]` — the command as spelled.
    if !nodelist.get_nodes_by_type("ExtendsNode").is_empty() {
        return Err(Error::syntax(format!(
            "'{}' cannot appear more than once in the same template",
            bits[0]
        )));
    }

    // `.first()` is `ExtendsNode.must_be_first = True` (`loader_tags.py:87`),
    // the only built-in that sets it, and it is read by `extend_nodelist` when
    // this node is appended — **not** by anything here. It bites on the shapes
    // this tag's own unbounded parse leaves reachable: `{{ x }}{% extends "a" %}`
    // and `{% block b %}{% endblock %}{% extends "a" %}` both raise
    // `{% extends "a" %} must be the first tag in the template.` on the pin,
    // while `x{% extends "a" %}` does not — `TextNode` is what
    // `contains_nontext` is defined against.
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
        // The token is the *quoted* literal, because `compile_filter` sees what
        // `construct_relative_path` returned and that keeps the original
        // quoting. Django's `FilterExpression.token` is the same string.
        assert_eq!(expression.token, "\"base.html\"");
        let (is_var, _) = &expression.var;
        assert!(!is_var, "a literal, not a lookup");
        assert_eq!(node.nodelists[0].0, "nodelist");
        // The body is empty, and it is a real nodelist rather than a missing
        // one: `{% extends %}` swallows to EOF, and here there is nothing after
        // it. `ExtendsNode.__init__` reads `nodelist.get_nodes_by_type`.
        assert_eq!(node.nodelists[0].1.nodes.len(), 0);
    }

    #[test]
    fn extends_takes_a_variable_as_readily_as_a_literal() {
        // The reason this tag splits with `split_contents()` where `{% block %}`
        // uses `contents.split()`: Django's docstring makes the variable form
        // explicit (`loader_tags.py:296-299`).
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
        // The unbounded `parser.parse()`. Both nodes are inside the
        // `ExtendsNode`'s body — the trailing text and the block — so the top
        // level holds exactly one node.
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
        // The pair is never two siblings: `do_extends`'s unbounded parse is
        // what makes the second one a child of the first, and the check is a
        // scan of that child nodelist. The **token** is what makes the shape
        // observable — it is the *first* `{% extends %}`, the one whose compile
        // function raised, and not the second one this message is about.
        // Measured on the pin: Django reports `(2, "extends 'a'", 1, None)`.
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
        // `must_be_first`, and the two sides of the `contains_nontext` test are
        // both here because only one of them raises.
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
        // Text before it is the *control*: `extend_nodelist` flips
        // `contains_nontext` only for a node that is not a `TextNode`, so this
        // one compiles — and a port that used "is the nodelist empty" instead
        // would raise here too. Two top-level nodes, which is why this reads the
        // nodelist rather than `compile`'s single node: measured on the pin,
        // `x{% extends 'a' %}` is `['TextNode', 'ExtendsNode']`.
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
        // The one path from this tag into `construct_relative_path` that is not
        // an error: the resolved name is what `compile_filter` sees, so it is
        // what the FilterExpression's token says.
        let mut parser = Parser::new(
            crate::lexer::Lexer::new("{% extends './base.html' %}").tokenize(),
            &crate::parser::host::NoHost,
        )
        .with_origin_template_name("sub/index.html".to_string());
        let nodelist = parser.parse(&[]).expect("expected a successful parse");
        let AttrValue::FilterExpr(expression) = attr(&nodelist.nodes[0], "parent_name") else {
            panic!("parent_name is a FilterExpr");
        };
        // `dirname("sub/index.html")` is `sub`, so the resolved name keeps the
        // directory: this is the branch a test that expected `base.html` — the
        // `"./x"`-against-a-bare-name case — would pass while resolving
        // relative to the wrong thing.
        assert_eq!(expression.token, "\"sub/base.html\"");
    }

    #[test]
    fn extends_refuses_a_relative_name_with_no_origin() {
        // `parse`'s default origin is the unknown one, so this is what a bare
        // `from_string` sees. The message names the *original* argument and does
        // not quote it (`loader_tags.py:264-265`) — the f-string form, unlike
        // the tag's other two messages.
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
        // The walk, through the one node shape that would break a
        // name-driven traversal. `IfNode` emits its branch lists as
        // **empty-named** nodelists (`desc.rs`, "positional only"), and Django
        // reaches the same nodes through `nodelist`, a property over
        // `conditions_nodelists` (`defaulttags.py:314-320`) — so a walk that
        // descended only into *named* nodelists would silently miss every
        // `{% block %}` in an `{% if %}`, and `ExtendsNode.blocks` would be
        // short by exactly those.
        //
        // `{% block %}` and not `{% extends %}` in the branch: `{% extends %}`
        // inside any tag that has an end tag makes its own unbounded parse eat
        // the end tag and raise `Invalid block tag`, measured on the pin.
        let tree = nodelist("{% if x %}{% block a %}{% endblock %}{% endif %}");
        assert_eq!(tree.get_nodes_by_type("BlockNode").len(), 1);
        assert!(tree.get_nodes_by_type("ExtendsNode").is_empty());
        // The IfNode itself is a sibling of nothing, and its two branch lists
        // are both unnamed, which is the shape being walked.
        assert_eq!(tree.get_nodes_by_type("IfNode").len(), 1);
    }

    #[test]
    fn get_nodes_by_type_is_pre_order_and_in_document_order() {
        // The order is the contract, not an incidental: `ExtendsNode.__init__`
        // folds the result into `{n.name: n …}` (`loader_tags.py:94`), and
        // `test_parser_parity`'s `describe` descends that dict with `.items()`
        // without sorting it, so a walk that visited children first, or that
        // sorted, would materialise a differently-ordered `blocks` and the
        // parity comparison would say so.
        //
        // Names deliberately not in alphabetical order, and all distinct:
        // `{% block %}`'s own duplicate-name check (`parser.loaded_blocks`, A5)
        // refuses a repeated name within one parse, so "the fold keeps the last
        // of two equal names" is **not** observable from a tree Rust builds —
        // the duplicate can only arrive through a host-compiled block, which is
        // exactly the fork A5 records. What is observable, and is what this
        // asserts, is insertion order.
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
        // `ExtendsNode.__init__` then folds that list into
        // `{n.name: n …}` (`loader_tags.py:94`), whose **key order is this
        // list's** — Python dicts are insertion-ordered, so the assertion above
        // is the assertion about `self.blocks` too, and `describe` compares the
        // dict unsorted.
    }

    #[test]
    fn get_nodes_by_type_walks_a_nested_nodelist_depth_first() {
        // Pre-order across depth: the outer block is reported before the inner
        // one, which is the half a breadth-first or children-first walk gets
        // wrong.
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
