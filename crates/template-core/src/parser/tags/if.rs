//! `{% if %}` — `defaulttags.do_if` (`defaulttags.py:932-1020`).
//!
//! `do_if` runs `TemplateIfParser(parser, bits).parse()` (`defaulttags.py:993`)
//! and `IfNode.render` calls `condition.eval(context)` (`defaulttags.py:326`),
//! so the arena `smartif.rs` builds has to cross as shape and the materialiser
//! rebuilds real `smartif` nodes from it — see [`IfConditionDesc`].
//!
//! The body: the first condition (`defaulttags.py:993-995`, the only source of
//! smartif errors), `parser.parse(("elif", "else", "endif"))` + `next_token()`
//! (`defaulttags.py:996`), a `token.contents.startswith("elif")` loop
//! (`defaulttags.py:999-1006`), then an optional `else` and the `endif` check
//! (`defaulttags.py:1007-1018`). **`startswith`**, not `==`: `Parser.parse` stops
//! on `contents.split()[0] in parse_until`, so `{% elif foo %}` ends the body
//! with contents `"elif foo"` (`base.py:561-564`).
//!
//! Only a terminator whose contents is *not* exactly `endif` reaches this file's
//! sole message (`{% if a %}x{% endif foo %}`, "Malformed template tag"); a
//! second `{% endif %}` is an ordinary unknown top-level tag instead.
//!
//! `TemplateIfParser.__init__` compiles every operand *before* `parse` runs the
//! operator algorithm, so a later operand's bad filter beats an operator error;
//! [`IfParser::template`] reproduces that by compiling the merged bits in order.
//!
//! `IfNode.__init__` takes one argument holding the bodies
//! (`defaulttags.py:308-309`), so the descriptor carries N unnamed nodelists
//! located by position ([`AttrValue::NodeListRef`]).

use crate::parser::desc::{AttrValue, IfConditionDesc, NodeDesc, NodeListDesc, NodeRef};
use crate::parser::error::Error;
use crate::parser::smartif::{CompileFilter, ErrorClass, IfError, IfParser};
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let mut branches: Vec<AttrValue> = Vec::new();
    let mut nodelists: Vec<NodeListDesc> = Vec::new();

    let first = condition(parser, &bits_after_tag(token)?)?;
    nodelists.push(parser.parse(&["elif", "else", "endif"])?);
    branches.push(branch(first, 0));

    // `parse` puts its terminator back; this reads it, not the body's last token.
    let mut terminator = parser
        .next_token()
        .expect("parse returns only after prepending its terminator");

    while terminator.contents.starts_with("elif") {
        let condition = condition(parser, &bits_after_tag(&terminator)?)?;
        let index = nodelists.len();
        nodelists.push(parser.parse(&["elif", "else", "endif"])?);
        branches.push(branch(condition, index));
        terminator = parser
            .next_token()
            .expect("parse returns only after prepending its terminator");
    }

    if terminator.contents == "else" {
        let index = nodelists.len();
        nodelists.push(parser.parse(&["endif"])?);
        // `conditions_nodelists.append((None, nodelist))` (`defaulttags.py:1009`): the
        // `None` is what `IfNode.render` reads as "the else", and the tuple is positional.
        branches.push(AttrValue::Tuple(vec![
            AttrValue::None,
            AttrValue::NodeListRef(index),
        ]));
        terminator = parser
            .next_token()
            .expect("parse returns only after prepending its terminator");
    }

    if terminator.contents != "endif" {
        return Err(Error::syntax(format!(
            "Malformed template tag at line {}: \"{}\"",
            terminator.lineno, terminator.contents,
        )));
    }

    let mut node = NodeDesc::new("IfNode").attr("conditions_nodelists", AttrValue::List(branches));
    for nodelist in nodelists {
        // The empty name is `desc.rs`'s "positional only": `materialise_node` passes
        // only *named* nodelists to `cls`, so an unnamed one is reachable solely by `NodeListRef`.
        node = node.nodelist("", nodelist);
    }
    Ok(node)
}

/// `token.split_contents()[1:]` — the bits after the tag name.
fn bits_after_tag(token: &Token) -> Result<Vec<String>, Error> {
    Ok(split_contents(token)?.split_off(1))
}

/// One `(condition, nodelist)` **tuple**, as `do_if` builds them.
fn branch(condition: AttrValue, nodelist: usize) -> AttrValue {
    AttrValue::Tuple(vec![condition, AttrValue::NodeListRef(nodelist)])
}

/// `TemplateIfParser(parser, bits).parse()` — one branch's condition.
fn condition(parser: &Parser<'_>, bits: &[String]) -> Result<AttrValue, Error> {
    let compiler = HostCompiler(parser);
    let mut if_parser = IfParser::template(&compiler, bits.to_vec())?;
    let root = if_parser.parse().map_err(|error| condition_error(&error))?;
    // Named rather than inferred: the seam where the arena becomes the wire.
    let tree: IfConditionDesc = if_parser.condition(root);
    Ok(AttrValue::IfCondition(Box::new(tree)))
}

/// The `smartif` seam: `TemplateIfParser.create_var`'s `compile_filter`. An
/// adapter because `IfParser::template` takes `&dyn CompileFilter` while the seam
/// belongs to the host ([`Parser::compile_condition_filter`]).
struct HostCompiler<'a, 'h>(&'a Parser<'h>);

impl CompileFilter for HostCompiler<'_, '_> {
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error> {
        self.0.compile_condition_filter(token)
    }
}

/// `IfError` as the engine's error. `do_if` builds `TemplateIfParser`, whose
/// `error_class` is `TemplateSyntaxError` (`defaulttags.py:922`), so only
/// `TemplateSyntax` can occur; `Value` is mapped rather than `unreachable!`.
fn condition_error(error: &IfError) -> Error {
    match error.class {
        ErrorClass::TemplateSyntax => Error::syntax(error.message.clone()),
        ErrorClass::Value => Error::value_error(error.message.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::desc::NodeListDesc;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::Host;
    use crate::parser::tags::support::{attr, compile, failure, parse_with, texts, CountingHost};

    /// A host that records every operand it is asked for, and can refuse one.
    /// [`CountingHost`] and [`NoHost`] accept everything silently, so neither can
    /// say *which* tokens reached the host or *when* the walk stopped.
    #[derive(Default)]
    struct FilterHost {
        unknown: Option<&'static str>,
        seen: std::cell::RefCell<Vec<String>>,
    }

    impl Host for FilterHost {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(NodeDesc, Vec<Token>), Error> {
            unreachable!("FilterHost compiles no tags")
        }

        fn alloc_slot(&self) -> usize {
            0
        }

        fn compile_filter(&self, token: &str) -> Result<NodeRef, Error> {
            let mut seen = self.seen.borrow_mut();
            seen.push(token.to_string());
            if Some(token) == self.unknown {
                // Django's own message, from `find_filter` (`base.py:682`).
                return Err(Error::host(format!("Invalid filter: '{token}'")));
            }
            Ok(NodeRef(seen.len() as u64 - 1))
        }
    }

    /// The `conditions_nodelists` list, as the tuples `do_if` builds.
    fn branches(node: &NodeDesc) -> &Vec<AttrValue> {
        let AttrValue::List(branches) = attr(node, "conditions_nodelists") else {
            panic!("conditions_nodelists is a List");
        };
        branches
    }

    /// One branch's condition, asserting the container on the way past.
    fn condition_of(branch: &AttrValue) -> &AttrValue {
        let AttrValue::Tuple(pair) = branch else {
            panic!("a branch is a Tuple, not a list: {branch:?}");
        };
        assert_eq!(pair.len(), 2, "a branch is exactly (condition, nodelist)");
        &pair[0]
    }

    /// One branch's nodelist index, asserted to be the *second* half.
    fn nodelist_of(branch: &AttrValue) -> usize {
        let AttrValue::Tuple(pair) = branch else {
            panic!("a branch is a Tuple: {branch:?}");
        };
        let AttrValue::NodeListRef(index) = pair[1] else {
            panic!(
                "the second half of a branch is a NodeListRef: {:?}",
                pair[1]
            );
        };
        index
    }

    /// A literal condition's `(text, handle)`.
    fn literal(condition: &AttrValue) -> (&str, NodeRef) {
        let AttrValue::IfCondition(tree) = condition else {
            panic!("a condition crosses as an IfCondition: {condition:?}");
        };
        literal_of(tree)
    }

    /// The same, from the tree directly.
    fn literal_of(tree: &IfConditionDesc) -> (&str, NodeRef) {
        let IfConditionDesc::Literal { text, expression } = tree else {
            panic!("expected a literal, got {tree:?}");
        };
        (text, *expression)
    }

    /// `(key, first, second)` of an operator node; `second` is `None` for a prefix operator.
    fn operator(tree: &IfConditionDesc) -> (&str, &IfConditionDesc, Option<&IfConditionDesc>) {
        let IfConditionDesc::Operator { key, first, second } = tree else {
            panic!("expected an operator, got {tree:?}");
        };
        (key, first, second.as_deref())
    }

    /// The single branch's condition tree of a one-branch `{% if %}`.
    fn tree_of(node: &NodeDesc) -> &IfConditionDesc {
        let AttrValue::IfCondition(tree) = condition_of(&branches(node)[0]) else {
            panic!("the condition is an IfCondition");
        };
        tree
    }

    /// The nodelists an `IfNode` descriptor carries, asserted *unnamed*.
    fn nodelists(node: &NodeDesc) -> Vec<&NodeListDesc> {
        for (name, _) in &node.nodelists {
            assert_eq!(
                name, "",
                "every IfNode nodelist is positional-only: IfNode.__init__ takes \
                 one argument and holds the bodies inside it",
            );
        }
        node.nodelists.iter().map(|(_, list)| list).collect()
    }

    #[test]
    fn a_branch_is_a_tuple_and_the_else_condition_is_none() {
        // A tuple, not a list: `IfNode.render` unpacks `(condition, nodelist)`
        // (`defaulttags.py:323`), and `describe`'s tuple arm carries a marker the
        // list arm does not, so a list would be a detectable diff on every parity row.
        let node = compile("{% if a %}x{% else %}y{% endif %}");
        assert_eq!(node.class_name, "IfNode");

        let branches = branches(&node);
        assert_eq!(branches.len(), 2);
        assert_eq!(literal(condition_of(&branches[0])).0, "a");
        assert_eq!(
            condition_of(&branches[1]),
            &AttrValue::None,
            "the else branch's condition is None, the value materialise_node maps to `None`",
        );

        assert_eq!(nodelist_of(&branches[0]), 0);
        assert_eq!(nodelist_of(&branches[1]), 1);

        let bodies = nodelists(&node);
        assert_eq!(bodies.len(), 2, "one nodelist per branch");
        assert_eq!(texts(bodies[0]), vec!["x"]);
        assert_eq!(texts(bodies[1]), vec!["y"]);
    }

    #[test]
    fn an_if_with_no_else_has_one_branch_and_one_nodelist() {
        let node = compile("{% if a %}x{% endif %}");
        assert_eq!(branches(&node).len(), 1);
        assert_eq!(node.nodelists.len(), 1);
        assert_eq!(nodelist_of(&branches(&node)[0]), 0);
    }

    #[test]
    fn elif_branches_come_back_in_source_order_and_each_names_its_own_body() {
        // Order is load-bearing twice: `IfNode.render` takes the *first* truthy
        // condition (`defaulttags.py:323-333`), and the `NodeListRef` index only
        // means anything if nodelists were pushed in branch order.
        let node = compile("{% if a %}1{% elif b %}2{% elif c %}3{% else %}4{% endif %}");
        let branches = branches(&node);
        assert_eq!(branches.len(), 4);
        assert_eq!(literal(condition_of(&branches[0])).0, "a");
        assert_eq!(literal(condition_of(&branches[1])).0, "b");
        assert_eq!(literal(condition_of(&branches[2])).0, "c");
        assert_eq!(condition_of(&branches[3]), &AttrValue::None);

        let bodies = nodelists(&node);
        assert_eq!(bodies.len(), 4);
        for (index, body) in bodies.iter().enumerate() {
            assert_eq!(texts(body), vec![(index + 1).to_string()]);
        }
        for (index, branch) in branches.iter().enumerate() {
            assert_eq!(nodelist_of(branch), index);
        }
    }

    #[test]
    fn the_operators_are_named_and_their_children_are_ordered() {
        // `or` binds loosest and `not` is the only prefix operator, so its `second`
        // is None. The shape is asserted structurally, so a tree rebuilt from a
        // re-parse cannot pass by producing the same rendering.
        let node = compile("{% if a and b or not c %}x{% endif %}");
        let root = tree_of(&node);

        let (key, left, right) = operator(root);
        assert_eq!(key, "or");

        let (key, and_left, and_right) = operator(left);
        assert_eq!(key, "and");
        assert_eq!(literal_of(and_left).0, "a");
        assert_eq!(literal_of(and_right.expect("`and` is infix")).0, "b");

        let right = right.expect("`or` is infix, so both operands are set");
        let (key, not_operand, not_second) = operator(right);
        assert_eq!(key, "not");
        assert!(
            not_second.is_none(),
            "`not` is prefix: nud sets second to None",
        );
        assert_eq!(literal_of(not_operand).0, "c");
    }

    #[test]
    fn a_merged_operator_is_one_key_rather_than_two_tokens() {
        // `IfParser.__init__` folds `not in`/`is not` into one operator
        // (`smartif.py:159-173`), which `translate_token` looks up (`smartif.py:179-185`).
        let node = compile("{% if a not in b %}x{% endif %}");
        let (key, first, second) = operator(tree_of(&node));
        assert_eq!(key, "not in");
        assert_eq!(literal_of(first).0, "a");
        assert_eq!(literal_of(second.expect("`not in` is infix")).0, "b");
    }

    #[test]
    fn the_condition_carries_the_operands_handle_and_not_a_filter_expression() {
        // `TemplateIfParser.create_var` calls the *host's* `compile_filter`
        // (`defaulttags.py:929`), so each operand is a handle, not a `FilterExpr`.
        // `NoHost` gives every handle `NodeRef(0)`, so it cannot tell two operands apart.
        let node = parse_with("{% if a and b %}x{% endif %}", &CountingHost::default())
            .nodes
            .into_iter()
            .next()
            .expect("one node");
        let (key, first, second) = operator(tree_of(&node));
        assert_eq!(key, "and");
        let (left_text, left) = literal_of(first);
        let (right_text, right) = literal_of(second.expect("`and` is infix"));
        assert_eq!((left_text, right_text), ("a", "b"));
        assert_ne!(left, right, "each operand is its own host object");
    }

    #[test]
    fn a_body_error_precedes_the_next_branchs_condition() {
        // `do_if` builds a branch's condition *then* parses its body, per branch
        // (`defaulttags.py:993-996`, `defaulttags.py:1001-1004`), so the first body's unknown
        // tag beats the `elif`'s empty condition — "conditions first" holds only within one.
        let error = failure("{% if a %}{% nope %}{% elif %}y{% endif %}");
        assert_eq!(
            error.message(),
            "Invalid block tag on line 1: 'nope', expected 'elif', 'else' or 'endif'. \
             Did you forget to register or load this tag?",
        );
    }

    #[test]
    fn every_operand_is_compiled_before_the_operator_algorithm_runs() {
        // `translate_token` tries `OPERATORS[token]` first and falls through to
        // `create_var` (`smartif.py:179-183`), so the host sees operands alone, left to
        // right, and all of them before the tree is built.
        let host = FilterHost::default();
        parse_with("{% if a and b or not c %}x{% endif %}", &host);
        assert_eq!(*host.seen.borrow(), vec!["a", "b", "c"]);

        let host = FilterHost::default();
        parse_with("{% if a|lower == 'x' %}x{% endif %}", &host);
        assert_eq!(
            *host.seen.borrow(),
            vec!["a|lower", "'x'"],
            "the operands are the *merged* bits, filters and all",
        );
    }

    #[test]
    fn a_bad_operand_beats_the_operator_error_that_would_have_followed() {
        // Both the doubled `and` and `b|nope` are errors, and Django reports the
        // operand — which is why the condition cannot be a re-parse of the expression.
        let host = FilterHost {
            unknown: Some("b|nope"),
            ..FilterHost::default()
        };
        let mut parser = Parser::new(
            Lexer::new("{% if a and and b|nope %}x{% endif %}").tokenize(),
            &host,
        );
        let error = parser.parse(&[]).expect_err("the operand is refused");
        assert_eq!(error.kind, Error::host("Invalid filter: 'b|nope'").kind);
        assert_eq!(
            *host.seen.borrow(),
            vec!["a", "b|nope"],
            "and the refusal stopped the walk at the offending operand",
        );

        // The control: with nothing refused, the operator error surfaces.
        let error = failure("{% if a and and b %}x{% endif %}");
        assert_eq!(
            error.message(),
            "Not expecting 'and' in this position in if tag.",
        );
    }

    #[test]
    fn malformed_is_the_terminators_own_contents() {
        // This file's only own message: `Parser.parse` stops on
        // `contents.split()[0] in parse_until`, so `{% endif foo %}` terminates the body
        // with contents `"endif foo"` and the `!=` check fires.
        let error = failure("{% if a %}x{% endif foo %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Malformed template tag at line 1: \"endif foo\"".to_string()),
        );
    }

    #[test]
    fn an_else_with_trailing_bits_is_malformed_and_its_body_is_dropped() {
        // `contents == "else"` is false for `"else foo"`, so the else body is never
        // parsed and is silently dropped; the `endif` check then fails on the terminator.
        let error = failure("{% if a %}x{% else foo %}y{% endif %}");
        assert_eq!(
            error.message(),
            "Malformed template tag at line 1: \"else foo\"",
        );
    }

    #[test]
    fn a_second_endif_is_an_ordinary_unknown_tag() {
        // The first `endif` ends the `{% if %}` and the second is an unknown
        // top-level tag, so the message is `Parser.parse`'s and not this file's.
        let error = failure("{% if a %}{% endif %}{% endif %}");
        assert_eq!(
            error.message(),
            "Invalid block tag on line 1: 'endif'. Did you forget to register or load this tag?",
        );
    }

    #[test]
    fn an_empty_condition_is_smartifs_error_not_this_files() {
        let error = failure("{% if %}{% endif %}");
        assert_eq!(error.message(), "Unexpected end of expression in if tag.");
        assert_eq!(
            error.token.as_ref().map(|token| token.contents.as_str()),
            Some("if"),
            "the token smartif is handed is the tag's own",
        );
    }

    #[test]
    fn an_unclosed_if_names_the_three_terminators_it_wanted() {
        // Not this file's message: `Parser.parse` hits EOF with `parse_until` unsatisfied.
        let error = failure("{% if a %}");
        assert_eq!(
            error.message(),
            "Unclosed tag on line 1: 'if'. Looking for one of: elif, else, endif.",
        );
    }
}
