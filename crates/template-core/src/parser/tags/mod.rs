//! One module per built-in tag (design spec §10.4).
//!
//! Written once, in full, and never edited by a fan-out agent: it is the file
//! every new tag would otherwise have to touch, and the layout exists so that no
//! two agents share one. The consequence is a real constraint worth stating —
//! **a helper needed by two tags lives here, not in either tag's file**, and it
//! is added by the central commit rather than by an agent.
//!
//! The compile *functions* do not live here. Each tag owns `tags/<name>.rs` and
//! exports `pub fn compile`; `crate::parser::registry` maps the command name to
//! it. The four helpers below are the ones M2's nine tags share, moved out of
//! the single `tags.rs` unchanged.

pub mod autoescape;
pub mod comment;
pub mod csrf_token;
pub mod cycle;
pub mod debug;
pub mod filter;
pub mod firstof;
// `for` and `if` are Rust keywords, so the module name is `for_tag`/`if_tag`
// while the *file* keeps the command's name. `#[path]` is what lets the
// module name differ from the file name.
#[path = "for.rs"]
pub mod for_tag;
#[path = "if.rs"]
pub mod if_tag;
pub mod ifchanged;
pub mod load;
pub mod lorem;
pub mod now;
pub mod partial;
pub mod partialdef;
pub mod querystring;
pub mod regroup;
pub mod resetcycle;
pub mod spaceless;
pub mod templatetag;
pub mod url;
pub mod verbatim;
pub mod widthratio;
pub mod with;

#[cfg(test)]
pub(crate) use crate::parser::registry::CompileFn;

use crate::parser::error::Error;
use crate::token::Token;

#[cfg(test)]
use crate::parser::desc::{AttrValue, NodeDesc, NodeRef};
#[cfg(test)]
use crate::parser::Parser;

/// `Token.split_contents`, as a compile function sees it.
///
/// Django reaches `next(bits)` on an exhausted iterator, so that failure leaves
/// `parse` as a bare `StopIteration` — with `.token` attached by the
/// `except Exception` arm — rather than a `TemplateSyntaxError`. M1's
/// `_native.split_contents` binding already fixed that type at the FFI boundary;
/// this keeps the two consistent instead of inventing a second behaviour for the
/// same condition.
fn split_contents(token: &Token) -> Result<Vec<String>, Error> {
    token
        .split_contents()
        .map_err(|_| Error::unterminated_marker())
}

/// Python's `s[1:-1]`: the first and last *characters* dropped, clamping where
/// Python clamps.
///
/// `{% now %}`'s format string is extracted this way, and Django does not check
/// that the argument is quoted — so `{% now 5 %}` yields `""` and
/// `{% now abc %}` yields `"b"`. `[1..len - 1]` on a `&str` panics on a
/// one-character argument and is not the same operation.
fn strip_first_and_last(s: &str) -> String {
    let mut chars = s.chars();
    chars.next();
    chars.next_back();
    chars.as_str().to_string()
}

/// Python's `repr()` for a `str`, as a `"%r"` interpolation applies it.
///
/// Python's `repr()` of a string, for the two Django messages that interpolate
/// with `%r`: `{% resetcycle %}`'s tag name and `{% templatetag %}`'s argument
/// list. Python chooses `'` unless the string contains `'` and no `"`, then
/// escapes backslashes, the chosen quote, and the standard control escapes as
/// `\xNN` / `\uNNNN` / `\UNNNNNNNN`.
///
/// **Exact on ASCII, and enforced as such.** The boundary is *not* "printable
/// non-ASCII passes through": Python escapes a character when
/// `Py_UNICODE_ISPRINTABLE` is false, which covers every non-ASCII category-C
/// code point (Cf, Cs, Co, Cn) plus Zl, Zp and Zs. `char::is_control()` is Cc
/// only, so this function emits those raw where Django escapes them. Measured
/// on the pinned tree — `U+00AD` is Cf, and `option` is a live argument slot:
///
/// ```text
/// >>> Template('{% include "x" ­option %}')
/// TemplateSyntaxError: Unknown argument for 'include' tag: '\xadoption'.
/// ```
///
/// The same slot exists at M5 in `{% with %}`'s `remaining_bits[0]`
/// (`defaulttags.py:1683`): `{% with a=1 foo­bar %}` reports
/// `'with' received an invalid token: 'foo\xadbar'`.
///
/// Both M2 call sites are ASCII *by construction*, which is why the assertion
/// below holds and not by accident: `args[0]` is the tag name dispatch matched
/// against the registry, and `TEMPLATETAG_ARGUMENTS` is a constant. When M5 and
/// M7 wire user text into these messages they must route them through the
/// host's `repr` rather than widen this function (design spec §10.4) — and this
/// assertion is what makes that a decision someone has to make rather than a
/// silently wrong message.
fn py_repr_str(s: &str) -> String {
    debug_assert!(
        s.is_ascii(),
        "py_repr_str is exact for ASCII only: Python's repr also escapes \
         non-printable non-ASCII (Cf/Cs/Co/Cn/Zl/Zp/Zs), which `is_control` \
         does not cover. Route {s:?} through the host's repr; do not widen here.",
    );

    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };

    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ if c == quote => {
                out.push('\\');
                out.push(c);
            }
            _ if c.is_control() => match u32::from(c) {
                n if n < 0x100 => out.push_str(&format!("\\x{n:02x}")),
                n if n < 0x10000 => out.push_str(&format!("\\u{n:04x}")),
                n => out.push_str(&format!("\\U{n:08x}")),
            },
            _ => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `repr()` of a list of strings, as `"%s" % [str, ...]` renders it.
fn py_list_repr(items: &[&str]) -> String {
    let rendered: Vec<String> = items.iter().map(|item| py_repr_str(item)).collect();
    format!("[{}]", rendered.join(", "))
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

/// The stand-in compile function for `command`, if `command` names one.
///
/// A `match` here rather than three `REGISTRY` rows: a row would put a name in
/// `builtin_tag_names` that Django's registry does not have, and the Python
/// cross-check in `tests/test_tag_inventory.py` asserts against Django's real
/// set. `registry::builtin_tag` consults this before the table.
#[cfg(test)]
pub(crate) fn standin_tag(command: &str) -> Option<CompileFn> {
    match command {
        "standin" => Some(standin_block),
        "standin_ref" => Some(standin_ref),
        "standin_first" => Some(standin_first),
        _ => None,
    }
}

/// The parser harness shared by `tags/mod.rs`'s tests and every `tags/<name>.rs`
/// test module.
///
/// One copy rather than one per tag: each of these builds a `Parser` the same
/// way, and a per-tag copy would be a second spelling of the same helper —
/// meaning a tag's tests could pass against a harness no other tag used. It is
/// `pub(crate)` and lives under `#[cfg(test)]` so nothing ships it.
#[cfg(test)]
pub(crate) mod support {
    use crate::lexer::Lexer;
    use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc};
    use crate::parser::error::Error;
    use crate::parser::host::NoHost;
    use crate::parser::Parser;
    use crate::parser::TEXT_NODE;

    /// Parse `source` with no host, and return its single top-level node.
    pub(crate) fn compile(source: &str) -> NodeDesc {
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
    pub(crate) fn failure(source: &str) -> Error {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        parser.parse(&[]).expect_err("expected a parse failure")
    }

    /// The error `source` fails with, with an origin repr attached.
    pub(crate) fn failure_with_origin(source: &str, repr: Option<&str>) -> Error {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        if let Some(repr) = repr {
            parser = parser.with_origin_template_repr(repr.to_string());
        }
        parser.parse(&[]).expect_err("expected a parse failure")
    }

    /// The `AttrValue` of `name`.
    pub(crate) fn attr<'a>(node: &'a NodeDesc, name: &str) -> &'a AttrValue {
        node.attrs
            .iter()
            .find(|(key, _)| key.as_str() == name)
            .map(|(_, value)| value)
            .unwrap_or_else(|| panic!("{} has no attribute {name:?}", node.class_name))
    }

    /// The contents of every `TextNode` in a nodelist, in order.
    pub(crate) fn texts(nodelist: &NodeListDesc) -> Vec<String> {
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
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::desc::NodeRef;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::Host;
    use crate::parser::host::NoHost;
    use crate::parser::tags::support::{attr, compile, failure, failure_with_origin, texts};

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

    #[test]
    fn python_repr_matches_repr_for_the_reachable_inputs() {
        // Cross-checked against `repr()` in CPython 3.14 for each case.
        assert_eq!(py_repr_str("resetcycle"), "'resetcycle'");
        assert_eq!(py_repr_str("openbrace"), "'openbrace'");
        assert_eq!(py_repr_str("don't"), "\"don't\"");
        assert_eq!(py_repr_str("both'\"quotes"), "'both\\'\"quotes'");
        assert_eq!(py_repr_str("a\\b"), "'a\\\\b'");
        assert_eq!(py_repr_str("a\nb\tc\rd"), "'a\\nb\\tc\\rd'");
        assert_eq!(py_repr_str("\u{0}"), "'\\x00'");
        assert_eq!(py_repr_str("\u{7f}"), "'\\x7f'");
        assert_eq!(
            py_list_repr(&["openblock", "closeblock"]),
            "['openblock', 'closeblock']",
        );
    }

    #[test]
    #[cfg_attr(not(debug_assertions), ignore = "the guard is a debug_assert")]
    #[should_panic(expected = "py_repr_str is exact for ASCII only")]
    fn python_repr_refuses_the_inputs_it_is_not_exact_for() {
        // This replaced an `assert_eq!(py_repr_str("héllo"), "'héllo'")` test,
        // which pinned behaviour nothing can reach and read as "this function is
        // a general repr" to the next caller. `héllo` is *not* the divergence —
        // printable non-ASCII passes through correctly in Python 3 too — so the
        // old test would have passed while the function stayed wrong for every
        // non-printable non-ASCII code point. The constraint now fails loudly
        // instead: M5's `{% with a=1 foo\u{ad}bar %}` and M7's
        // `{% include "x" \u{ad}option %}` trip this the moment they are wired
        // in, rather than emitting a message Django does not emit.
        py_repr_str("héllo");
    }

    #[test]
    fn python_whitespace_separators_split_arguments() {
        // Python's `str.isspace()` is true for U+001C..U+001F as well as for
        // Unicode `White_Space`, and every split Django performs uses that
        // table: `.split()` for dispatch and `smart_split` for a tag's
        // arguments. So `{% autoescape<U+001C>on %}` is two arguments to
        // Django, and is accepted.
        //
        // This was a genuine divergence until M1 fixed it: the lexer trimmed
        // and `smart_split` split on Rust's table, and the case above raised
        // "'autoescape' tag requires exactly one argument." The fix is
        // `pyunicode` (commit `1a013ae`), with the separators added to both
        // parity corpora and to the randomised alphabet — 13 parity failures
        // against the unfixed code. This test pins that the parser *inherits*
        // the corrected table rather than re-deriving splitting for itself.
        let node = compile("{% autoescape\u{1c}on %}x{% endautoescape %}");
        assert_eq!(attr(&node, "setting"), &AttrValue::Bool(true));
    }
}
