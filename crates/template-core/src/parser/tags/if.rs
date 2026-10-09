//! `{% if %}` — `defaulttags.do_if` (`defaulttags.py:932-1020`).
//!
//! The tag is small; what makes it M5's largest is that its condition is a
//! **parse tree Python has to hold**. `do_if` runs
//! `TemplateIfParser(parser, bits).parse()` (`defaulttags.py:993`) and `IfNode.render` calls
//! `condition.eval(context)` on the result (`defaulttags.py:326`), so the arena
//! `smartif.rs` builds has to cross as shape, and the materialiser rebuilds
//! real `smartif` nodes from it — see [`IfConditionDesc`].
//!
//! # The four-part body, and the one line that is not smartif's
//!
//! 1. `TemplateIfParser(parser, bits).parse()` for the first condition
//!    (`defaulttags.py:993-995`) — errors here are smartif's, and they are the *only* errors a
//!    malformed condition can produce.
//! 2. `parser.parse(("elif", "else", "endif"))`, then `next_token()` (`defaulttags.py:996`).
//! 3. A `while token.contents.startswith("elif")` loop (`defaulttags.py:999-1006`) — note
//!    **`startswith`**, not `==`: `Parser.parse` stops on
//!    `token.contents.split()[0] in parse_until`, so `{% elif foo %}` terminates
//!    the body and its contents are `"elif foo"` (`base.py:561-564`).
//! 4. An optional `else`, then the `endif` check (`defaulttags.py:1007-1018`).
//!
//! The `endif` check is the only message this file owns, and it is reached by a
//! construct the plan's draft got wrong. `{% if a %}{% endif %}{% endif %}` does
//! **not** reach it — the first `endif` terminates the body and the *second* is
//! then an ordinary unknown top-level tag, reporting `Invalid block tag on line
//! 1: 'endif'`. What reaches it is a terminator whose contents is *not* exactly
//! `endif`: measured, `{% if a %}x{% endif foo %}` gives
//! `Malformed template tag at line 1: "endif foo"`, and so does
//! `{% if a %}x{% else foo %}y{% endif %}` — which also **silently drops** the
//! else body, because `token.contents == "else"` is false and the `parse` that
//! would have read it never runs.
//!
//! # The condition is built during the parse, and that is Django's order
//!
//! `TemplateIfParser.__init__` compiles every operand *before* `parse` runs the
//! operator algorithm, so a bad filter in a later operand beats an operator
//! error. Rust reproduces that by construction: [`IfParser::template`] walks
//! the merged bits in order and calls [`CompileFilter::compile_filter`] for each
//! non-operator one, and only then does `parse` touch the tree. Measured, the
//! two agree: `{% if a and and b|nope %}` reports the filter error on both.
//!
//! # `{% if %}` is the only tag whose node has *positional-only* nodelists
//!
//! `IfNode.__init__(self, conditions_nodelists)` (`defaulttags.py:308-309`) takes one
//! argument and holds the branch bodies *inside* it, so there is no parameter a
//! branch nodelist could be a keyword for. The descriptor therefore carries N
//! unnamed nodelists and the branches name them by position
//! ([`AttrValue::NodeListRef`]), which is the shape `desc.rs` reserved for it.

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

    // `parse` puts its terminator back, so this reads it rather than the body's
    // last token — the same contract `{% for %}` relies on.
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
        // `conditions_nodelists.append((None, nodelist))` (`defaulttags.py:1011`): the `None`
        // condition is what `IfNode.render` reads as "this is the else", and it
        // is emitted rather than left out because the tuple is positional.
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
        // The empty name is `desc.rs`'s "positional only": `materialise_node`
        // builds every nodelist and passes only the *named* ones to `cls`, so an
        // unnamed one is reachable through its `NodeListRef` and nowhere else —
        // which is exactly `IfNode`'s contract.
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
    // Named rather than inferred: this is the seam where the arena becomes the
    // wire, and the type is the whole contract of [`IfConditionDesc`].
    let tree: IfConditionDesc = if_parser.condition(root);
    Ok(AttrValue::IfCondition(Box::new(tree)))
}

/// The `smartif` seam: `TemplateIfParser.create_var`'s
/// `self.template_parser.compile_filter(value)`.
///
/// An adapter rather than an argument because `IfParser::template` takes
/// `&dyn CompileFilter` while the seam belongs to the *host* — see
/// [`Parser::compile_condition_filter`], which is this in one line.
struct HostCompiler<'a, 'h>(&'a Parser<'h>);

impl CompileFilter for HostCompiler<'_, '_> {
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error> {
        self.0.compile_condition_filter(token)
    }
}

/// `IfError` as the engine's error.
///
/// The class decides, and `TemplateSyntax` is the only one `do_if` can produce:
/// it builds `TemplateIfParser`, whose `error_class` is `TemplateSyntaxError`
/// (`defaulttags.py:922`). `Value` is the base `smartif.IfParser`'s and no tag
/// constructs one, so it is mapped rather than left `unreachable!` — the mapping
/// is one line and a panic here would be a trap for a future caller.
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
    ///
    /// Neither [`CountingHost`] nor [`NoHost`] can answer the two questions the
    /// ordering tests ask — *which* tokens reached the host, and *when* the walk
    /// stopped — because both accept everything silently.
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

    /// One branch's condition, with the container asserted on the way past.
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

    /// The same, from the tree directly — the borrow a clone would avoid.
    fn literal_of(tree: &IfConditionDesc) -> (&str, NodeRef) {
        let IfConditionDesc::Literal { text, expression } = tree else {
            panic!("expected a literal, got {tree:?}");
        };
        (text, *expression)
    }

    /// `(key, first, second)` of an operator node, with `second` `None` for a
    /// prefix operator.
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
        // The tuple, not a list, is the whole point: `IfNode.render` unpacks
        // `(condition, nodelist)` (`defaulttags.py:323`) and `describe`'s tuple
        // arm carries a marker the list arm does not, so a list here would be a
        // detectable diff on every parity row.
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
        // The order is load-bearing twice over: `IfNode.render` takes the *first*
        // truthy condition (`defaulttags.py:323-333`), and the `NodeListRef` index only means
        // anything if the nodelists were pushed in the same order as the branches.
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
        // `{% if a and b or not c %}` is `(a and b) or (not c)`: `or` binds
        // loosest, `not` is the only prefix operator and its `second` is None.
        // The shape is asserted structurally rather than through a stringify, so
        // a port that rebuilt the tree from a re-parse could not pass it by
        // producing the same rendering.
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
        // `merge_tokens` folds `not in` and `is not` into a single operator
        // before `translate_token` looks them up (`smartif.py:159-173`), so the
        // key is the two-word one and no operand named `in` exists.
        let node = compile("{% if a not in b %}x{% endif %}");
        let (key, first, second) = operator(tree_of(&node));
        assert_eq!(key, "not in");
        assert_eq!(literal_of(first).0, "a");
        assert_eq!(literal_of(second.expect("`not in` is infix")).0, "b");
    }

    #[test]
    fn the_condition_carries_the_operands_handle_and_not_a_filter_expression() {
        // The seam: `TemplateIfParser.create_var` calls the *host's*
        // `compile_filter` (`defaulttags.py:929`), so each operand is a handle
        // and not an `AttrValue::FilterExpr`. Two operands must be two handles —
        // which is what makes `CountingHost` and not `NoHost` the right host
        // here: under `NoHost` every handle is `NodeRef(0)` and "two operands, two
        // host objects" would be indistinguishable from a single shared one.
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
        // (`defaulttags.py:995-996`, then `defaulttags.py:1002-1003`), so the first body's unknown tag is
        // reported before the `elif`'s empty condition is ever looked at. The
        // tempting opposite reading — "conditions are compiled first" — is true
        // *within* one condition (see the two tests below) and false across
        // branches, and it is the per-branch loop that decides.
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
        // `create_var` only for the rest (`smartif.py:179-183`), so the host sees
        // operands alone, left to right — never an operator. `TemplateIfParser.__init__`
        // compiles every token before `parse` runs, so the whole sequence is
        // complete before the tree is built.
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
        // Two errors are reachable from `{% if a and and b|nope %}`: the doubled
        // `and` is one the algorithm raises, and `b|nope` is one `create_var`
        // raises. Django reports the operand. This is the ordering the design
        // spec's `compile_filter` seam exists to reproduce, and it is why the
        // condition cannot be a re-parse of the whole expression in Python.
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

        // The control: with nothing refused, the operator error is what surfaces.
        let error = failure("{% if a and and b %}x{% endif %}");
        assert_eq!(
            error.message(),
            "Not expecting 'and' in this position in if tag.",
        );
    }

    #[test]
    fn malformed_is_the_terminators_own_contents() {
        // The one message this file owns. Note how it is reached: `Parser.parse`
        // stops on `token.contents.split()[0] in parse_until`, so `{% endif foo %}`
        // terminates the body and its contents are `"endif foo"` — the `!=` check
        // then fires. This is the plan's intended row with the source corrected;
        // see the module docs for what `{% if a %}{% endif %}{% endif %}` actually
        // does.
        let error = failure("{% if a %}x{% endif foo %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Malformed template tag at line 1: \"endif foo\"".to_string()),
        );
    }

    #[test]
    fn an_else_with_trailing_bits_is_malformed_and_its_body_is_dropped() {
        // `token.contents == "else"` is false for `"else foo"`, so the `parse`
        // that would have read the else body never runs and the body is silently
        // left in the token stream — where the `endif` check then fails on the
        // *terminator*, not on the body. Measured on the pinned tree.
        let error = failure("{% if a %}x{% else foo %}y{% endif %}");
        assert_eq!(
            error.message(),
            "Malformed template tag at line 1: \"else foo\"",
        );
    }

    #[test]
    fn a_second_endif_is_an_ordinary_unknown_tag() {
        // The row the plan's draft got wrong, pinned so the correction cannot be
        // quietly reverted: the first `endif` ends the `{% if %}` and the second
        // is an unknown top-level tag, so the message is `Parser.parse`'s and not
        // this file's.
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
        // Not this file's message either: `Parser.parse` reaches EOF with its
        // `parse_until` unsatisfied, and it is the one that knows the set.
        let error = failure("{% if a %}");
        assert_eq!(
            error.message(),
            "Unclosed tag on line 1: 'if'. Looking for one of: elif, else, endif.",
        );
    }
}
