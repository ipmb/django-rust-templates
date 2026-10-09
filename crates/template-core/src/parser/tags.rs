//! Rust's compile functions for built-in tags.

use crate::parser::desc::NodeDesc;
// `AttrValue` and `NodeRef` are used only by the `#[cfg(test)]` stand-ins below,
// so they are gated with them: an ungated import would be an `unused_imports`
// warning under `-D warnings` in the non-test build. The plan listed all four
// imports ungated, which does not compile for that reason.
#[cfg(test)]
use crate::parser::desc::{AttrValue, NodeRef};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

/// A tag's compile function: Django's `Parser.compile_func(self, token)`.
///
/// A plain `fn` pointer rather than a `Box<dyn Fn>`, so a table of them is a
/// `static` if it ever needs to be. The `'_` in `Parser<'_>` is a higher-ranked
/// lifetime, so one `CompileFn` value is usable with a parser of any lifetime.
pub type CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>;

/// The compile function Rust has for `command`, if it has one.
///
/// A `match` rather than a lazy map: the table is small, it is read once per
/// block tag, and a `HashMap` behind a `OnceLock` would add a synchronisation
/// point to the hot path for no benefit.
pub fn builtin_tag(command: &str) -> Option<CompileFn> {
    match command {
        // The nine built-in tags arrive in Task 5.
        #[cfg(test)]
        "standin" => Some(standin_block),
        #[cfg(test)]
        "standin_ref" => Some(standin_ref),
        #[cfg(test)]
        "standin_first" => Some(standin_first),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Test-only stand-ins.
//
// The parser's machinery has to be provable before the tags that will exercise
// it exist, and each of these exists for one property that no M2 built-in can
// reach:
//
//   standin        a tag that reads its own body, so `parse(until)`,
//                  `prepend_token`, `delete_first_token`, nesting and
//                  `contains_nontext` are all exercised
//   standin_ref    a node carrying `AttrValue::Ref`, the shape M3's
//                  `FilterExpression` will use — nothing in M2 produces a `Ref`
//                  from Rust, because both of `{% resetcycle %}`'s paths are
//                  errors until `{% cycle %}` arrives in M5
//   standin_first  a `must_be_first` node, so the ordering rule and the
//                  host-supplied origin repr are tested before `{% extends %}`
//                  arrives at M7
// ---------------------------------------------------------------------------

/// A block tag that reads its own body, exactly as `{% spaceless %}` will.
#[cfg(test)]
fn standin_block(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    let nodelist = parser.parse(&["endstandin"])?;
    parser.delete_first_token();
    Ok(NodeDesc::with_nodelist(
        "StandinBlockNode",
        "nodelist",
        nodelist,
    ))
}

/// An inline tag carrying `AttrValue::Ref`.
#[cfg(test)]
fn standin_ref(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Ok(NodeDesc::new("StandinRefNode").attr("expr", AttrValue::Ref(NodeRef(7))))
}

/// A `must_be_first` node.
#[cfg(test)]
fn standin_first(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Ok(NodeDesc::new("StandinFirstNode").first())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::desc::NodeListDesc;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::{Host, NoHost};
    use crate::parser::TEXT_NODE;

    /// Parse `source` with no host, and return its single top-level node.
    fn compile(source: &str) -> NodeDesc {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        let nodelist = parser.parse(&[]).expect("expected a successful parse");
        assert_eq!(
            nodelist.nodes.len(),
            1,
            "expected exactly one top-level node in {source:?}",
        );
        nodelist.nodes.into_iter().next().expect("checked above")
    }

    /// The error `source` fails with, parsing with no host.
    fn failure(source: &str) -> Error {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        parser.parse(&[]).expect_err("expected a parse failure")
    }

    /// The error `source` fails with, with an origin repr attached.
    fn failure_with_origin(source: &str, repr: Option<&str>) -> Error {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        if let Some(repr) = repr {
            parser = parser.with_origin_template_repr(repr.to_string());
        }
        parser.parse(&[]).expect_err("expected a parse failure")
    }

    /// The `AttrValue` of `name`.
    fn attr<'a>(node: &'a NodeDesc, name: &str) -> &'a AttrValue {
        node.attrs
            .iter()
            .find(|(key, _)| key.as_str() == name)
            .map(|(_, value)| value)
            .unwrap_or_else(|| panic!("{} has no attribute {name:?}", node.class_name))
    }

    /// The contents of every `TextNode` in a nodelist, in order.
    fn texts(nodelist: &NodeListDesc) -> Vec<String> {
        let mut out = Vec::new();
        for node in &nodelist.nodes {
            if node.class_name != TEXT_NODE {
                continue;
            }
            match attr(node, "s") {
                AttrValue::Str(s) => out.push(s.clone()),
                other => panic!("TextNode.s is not a Str: {other:?}"),
            }
        }
        out
    }

    #[test]
    fn a_block_tag_reads_its_body_and_terminates() {
        let mut parser = Parser::new(
            Lexer::new("a{% standin %}b{% endstandin %}c").tokenize(),
            &NoHost,
        );
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(texts(&nodelist), vec!["a", "c"]);
        assert_eq!(nodelist.nodes.len(), 3, "text, block, text");
        assert_eq!(nodelist.nodes[1].class_name, "StandinBlockNode");
        assert_eq!(
            nodelist.nodes[1].token.as_ref().unwrap().contents,
            "standin",
            "extend_nodelist attaches the opening token to the block node",
        );
        assert_eq!(nodelist.nodes[1].nodelists[0].0, "nodelist");
        assert_eq!(texts(&nodelist.nodes[1].nodelists[0].1), vec!["b"]);
    }

    #[test]
    fn the_terminator_is_prepended_not_consumed() {
        // `{% endstandin %}` is pushed back for the caller to consume with
        // `delete_first_token`. A parser that consumed it here would report the
        // *second* block as unclosed, or silently swallow a node
        // (Review Focus item 3).
        let mut parser = Parser::new(
            Lexer::new("{% standin %}a{% endstandin %}{% standin %}b{% endstandin %}").tokenize(),
            &NoHost,
        );
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(nodelist.nodes.len(), 2, "two blocks, each one node");
        assert_eq!(texts(&nodelist.nodes[1].nodelists[0].1), vec!["b"]);
        assert_eq!(nodelist.nodes[1].nodelists[0].0, "nodelist");
    }

    #[test]
    fn an_unclosed_block_names_the_tag_that_was_left_open() {
        let error = failure("x\n{% standin %}y");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 2: 'standin'. Looking for one of: endstandin.".to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().lineno, 2);
    }

    #[test]
    fn an_invalid_tag_inside_a_block_lists_what_was_expected() {
        // The `parse_until` form of the message, and the two-item `get_text_list`
        // case in a *single*-item list.
        let error = failure("{% standin %}\n{% nope %}\n{% endstandin %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 2: 'nope', expected 'endstandin'. \
                 Did you forget to register or load this tag?"
                    .to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().lineno, 2);
    }

    #[test]
    fn contains_nontext_starts_false_for_every_nodelist() {
        // A parser that keeps the flag on `self` — or on the node — passes every
        // single-tag test, and reports the *second* block's body as containing
        // non-text, which is what makes `{% extends %}` refuse a legal template
        // (Review Focus item 1).
        let mut parser = Parser::new(
            Lexer::new(
                "{% standin %}{% standin_ref %}{% endstandin %}{% standin %}b{% endstandin %}",
            )
            .tokenize(),
            &NoHost,
        );
        let nodelist = parser.parse(&[]).unwrap();
        assert!(
            nodelist.nodes[0].nodelists[0].1.contains_nontext,
            "inner list has a non-text node"
        );
        assert!(
            !nodelist.nodes[1].nodelists[0].1.contains_nontext,
            "second body is text only"
        );
        assert!(
            nodelist.contains_nontext,
            "the outer list holds block nodes"
        );
    }

    #[test]
    fn text_alone_never_sets_contains_nontext() {
        let mut parser = Parser::new(
            Lexer::new("{% standin %}a{% endstandin %}").tokenize(),
            &NoHost,
        );
        let nodelist = parser.parse(&[]).unwrap();
        assert!(!nodelist.nodes[0].nodelists[0].1.contains_nontext);
    }

    #[test]
    fn the_innermost_token_wins_across_a_nested_parse() {
        let error = failure_with_origin(
            "{% standin %}\n{% standin_first %}\n{% standin_first %}\n{% endstandin %}",
            Some("'tpl.html'"),
        );
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "{% standin_first %} must be the first tag in 'tpl.html'.".to_string(),
            ),
        );
        // The outer token is on line 1 and the message alone cannot tell the two
        // apart; an `error()` that overwrote the token would report line 1 here
        // (Review Focus item 5).
        assert_eq!(error.token.unwrap().lineno, 3);
    }

    #[test]
    fn without_an_origin_the_message_says_the_template() {
        let error = failure_with_origin("{% standin_first %}{% standin_first %}", None);
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "{% standin_first %} must be the first tag in the template.".to_string(),
            ),
        );
    }

    #[test]
    fn a_first_node_may_lead_any_nodelist() {
        // The rule permits it when `contains_nontext` is false, so a lone
        // `must_be_first` tag is legal — including when it is preceded by text,
        // because text nodes do not set the flag.
        let mut parser = Parser::new(Lexer::new("  {% standin_first %}").tokenize(), &NoHost);
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(nodelist.nodes[1].class_name, "StandinFirstNode");
        assert!(nodelist.contains_nontext);
    }

    #[test]
    fn a_ref_survives_the_descriptor_unchanged() {
        let node = compile("{% standin_ref %}");
        assert_eq!(node.class_name, "StandinRefNode");
        assert_eq!(attr(&node, "expr"), &AttrValue::Ref(NodeRef(7)));
    }

    /// A host with one tag of its own and one shadowing a Rust built-in, so
    /// dispatch order is observable.
    struct TestHost;

    impl Host for TestHost {
        fn has_tag(&self, command: &str) -> bool {
            matches!(command, "hosty" | "standin")
        }

        fn call_tag(&self, command: &str, _token: &Token) -> Result<NodeDesc, Error> {
            if command == "hosty" {
                Ok(NodeDesc::new("HostNode"))
            } else {
                Err(Error::host("the host refused"))
            }
        }
    }

    #[test]
    fn a_host_tag_is_dispatched_to_the_host() {
        let mut parser = Parser::new(Lexer::new("{% hosty %}").tokenize(), &TestHost);
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(nodelist.nodes[0].class_name, "HostNode");
        assert_eq!(nodelist.nodes[0].token.as_ref().unwrap().contents, "hosty");
    }

    #[test]
    fn the_host_wins_over_a_builtin_of_the_same_name() {
        // Django's registry is one dict that `add_library` updates in place, so a
        // later registration shadows an earlier one and `{% load %}` runs after
        // the builtins. Host-first is therefore the faithful order, and the
        // converse obligation — the shell must not register a tag Rust
        // implements — is a note on `Parser::parse`.
        let mut parser = Parser::new(Lexer::new("{% standin %}").tokenize(), &TestHost);
        let error = parser
            .parse(&[])
            .expect_err("the host shadows the built-in");
        assert_eq!(error.kind, ErrorKind::Host("the host refused".to_string()));
    }

    #[test]
    fn a_host_error_gets_the_token_attached_by_parse() {
        // `error()` is kind-agnostic; the `Host` branch is the one M4 will lean
        // on, and it is exercised here rather than assumed.
        let mut parser = Parser::new(Lexer::new("\n{% standin %}").tokenize(), &TestHost);
        let error = parser.parse(&[]).expect_err("the host refused");
        assert_eq!(error.token.unwrap().lineno, 2);
    }
}
