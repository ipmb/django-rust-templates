//! Rust's compile functions for built-in tags.

use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc, TEXT_NODE};
// `NodeRef` is named only by the `#[cfg(test)]` tests below — the non-test code
// holds one through `AttrValue::Ref` and the parser's cycle registry, never as a
// written type — so an ungated import would be an `unused_imports` warning under
// `-D warnings`. The plan listed all four descriptor imports ungated, which does
// not compile for that reason.
#[cfg(test)]
use crate::parser::desc::NodeRef;
use crate::parser::error::Error;
use crate::parser::Parser;
// Django splits a tag's arguments with `str.split()` (`autoescape`,
// `templatetag`) or with `smart_split` (`split_contents`), and both follow
// Python's `str.isspace()` table, which includes U+001C-U+001F. Rust's
// `str::split_whitespace` does not, so it must not be used for either.
use crate::pyunicode;
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
        "autoescape" => Some(tag_autoescape),
        "comment" => Some(tag_comment),
        "csrf_token" => Some(tag_csrf_token),
        "debug" => Some(tag_debug),
        "now" => Some(tag_now),
        "resetcycle" => Some(tag_resetcycle),
        "spaceless" => Some(tag_spaceless),
        "templatetag" => Some(tag_templatetag),
        "verbatim" => Some(tag_verbatim),
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

// ---------------------------------------------------------------------------
// Shared helpers for the M2 built-in tags.
// ---------------------------------------------------------------------------

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

/// Django renders `{% verbatim %}`'s body at **parse** time —
/// `nodelist.render(Context())` — and keeps the result as a `SafeString`, which
/// is why the descriptor carries `AttrValue::SafeStr` rather than `Str`
/// (Review Focus item 8).
///
/// At M2 the body can only be text: the lexer turns everything between
/// `{% verbatim %}` and `{% endverbatim %}` into text tokens, and a variable
/// would have failed the parse before reaching here for want of M3's
/// `FilterExpression`. So the non-text arm is unreachable today, and it is an
/// error rather than an `unreachable!()` so that the milestone which *does*
/// reach it gets a message instead of a panic through the FFI.
fn render_text_only(nodelist: &NodeListDesc) -> Result<String, Error> {
    let mut out = String::new();
    for node in &nodelist.nodes {
        if node.class_name != TEXT_NODE {
            return Err(Error::unimplemented(format!(
                "a {{% verbatim %}} body contains a {} node, which needs a real render",
                node.class_name,
            )));
        }
        match node.attrs.as_slice() {
            [(name, AttrValue::Str(s))] if name.as_str() == "s" => out.push_str(s),
            other => {
                return Err(Error::internal(format!(
                    "a TextNode descriptor is not [(\"s\", Str)]: {other:?}",
                )))
            }
        }
    }
    Ok(out)
}

/// The eight bits `{% templatetag %}` accepts, in Django's mapping order — which
/// is also the order the error message lists them in, because Python's `list()`
/// of a dict literal preserves insertion order. The *values* are the bits
/// themselves and are render-time, so they live on the Python class.
const TEMPLATETAG_ARGUMENTS: [&str; 8] = [
    "openblock",
    "closeblock",
    "openvariable",
    "closevariable",
    "openbrace",
    "closebrace",
    "opencomment",
    "closecomment",
];

// ---------------------------------------------------------------------------
// The nine M2 built-in tags.
// ---------------------------------------------------------------------------

/// `{% autoescape on|off %}…{% endautoescape %}`.
///
/// Django splits with `token.contents.split()`, not `split_contents()`, "because
/// this tag doesn't accept variable as arguments". The two are observable here:
/// for `{% autoescape "on off" %}` the raw split sees three arguments and
/// rejects on the count, while `split_contents` keeps the quoted phrase whole,
/// sees two, and rejects on the value. Both fail, with different messages, and
/// only one of them is Django's. (`{% autoescape a"b c %}` does *not*
/// distinguish them: the trailing quote is unmatched, so `smart_split` splits it
/// exactly as `str.split` does — verified against Django 6.0.9.)
fn tag_autoescape(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 2 {
        return Err(Error::syntax(
            "'autoescape' tag requires exactly one argument.",
        ));
    }
    let arg = args[1];
    if arg != "on" && arg != "off" {
        return Err(Error::syntax(
            "'autoescape' argument should be 'on' or 'off'",
        ));
    }

    let nodelist = parser.parse(&["endautoescape"])?;
    parser.delete_first_token();
    Ok(
        NodeDesc::with_nodelist("AutoEscapeControlNode", "nodelist", nodelist)
            .attr("setting", AttrValue::Bool(arg == "on")),
    )
}

/// `{% comment %}…{% endcomment %}`.
///
/// `skip_past`, not `parse`: the body is never compiled, so a template with a
/// syntax error inside `{% comment %}` still parses (Review Focus item 9). An
/// unclosed comment is reported by `skip_past` through `unclosed_block_tag`,
/// which pops the `comment` frame that `parse` pushed.
fn tag_comment(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    parser.skip_past("endcomment")?;
    // `CommentNode` has `child_nodelists = ()` and no `__init__`, so the body is
    // discarded rather than attached: there is nothing to render.
    Ok(NodeDesc::new("CommentNode"))
}

/// `{% csrf_token %}`.
fn tag_csrf_token(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    // Django reads no arguments at all, so `{% csrf_token junk %}` is accepted
    // rather than rejected.
    Ok(NodeDesc::new("CsrfTokenNode"))
}

/// `{% debug %}`.
fn tag_debug(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Ok(NodeDesc::new("DebugNode"))
}

/// `{% now "format" [as var] %}`.
fn tag_now(_parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let mut bits = split_contents(token)?;

    let mut asvar = None;
    if bits.len() == 4 && bits[bits.len() - 2] == "as" {
        asvar = Some(bits[bits.len() - 1].clone());
        bits.truncate(bits.len() - 2);
    }

    if bits.len() != 2 {
        return Err(Error::syntax("'now' statement takes one argument"));
    }

    let mut node = NodeDesc::new("NowNode").attr(
        "format_string",
        AttrValue::Str(strip_first_and_last(&bits[1])),
    );
    // Django passes `asvar=None` when absent, and `NowNode` stores it, so omitting
    // the keyword here produces the same node.
    if let Some(asvar) = asvar {
        node = node.attr("asvar", AttrValue::Str(asvar));
    }
    Ok(node)
}

/// `{% resetcycle [name] %}`.
///
/// Both paths fail at M2, because nothing populates the registry until
/// `{% cycle %}` (M5) — so `ResetCycleNode` is not reachable from a template
/// yet, and its descriptor shape is pinned by Task 7's corpus only through the
/// materialiser's hand-built fixtures.
fn tag_resetcycle(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = split_contents(token)?;
    if args.len() > 2 {
        // `args[0]` is safe to `%r` because it is the tag name dispatch matched
        // against the registry, not text from the template — see `py_repr_str`'s
        // bound. Anything reaching here with a non-ASCII byte trips that
        // function's assertion rather than producing Django-incompatible output.
        return Err(Error::syntax(format!(
            "{} tag accepts at most one argument.",
            py_repr_str(&args[0]),
        )));
    }

    if args.len() == 2 {
        let name = &args[1];
        // Django catches `(AttributeError, KeyError)` here: the attribute does
        // not exist until `{% cycle %}` has run, and the name may not be in it.
        // Both mean "no such cycle", so a miss in the map is the whole story —
        // the absent-attribute case is the map being empty.
        return match parser.named_cycle_nodes.get(name) {
            Some(node) => Ok(NodeDesc::new("ResetCycleNode").attr("node", AttrValue::Ref(*node))),
            None => Err(Error::syntax(format!(
                "Named cycle '{name}' does not exist."
            ))),
        };
    }

    match parser.last_cycle_node {
        Some(node) => Ok(NodeDesc::new("ResetCycleNode").attr("node", AttrValue::Ref(node))),
        None => Err(Error::syntax("No cycles in template.")),
    }
}

/// `{% spaceless %}…{% endspaceless %}`.
fn tag_spaceless(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    let nodelist = parser.parse(&["endspaceless"])?;
    parser.delete_first_token();
    Ok(NodeDesc::with_nodelist(
        "SpacelessNode",
        "nodelist",
        nodelist,
    ))
}

/// `{% templatetag openblock %}` and the other seven.
fn tag_templatetag(_parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Raw split, as in Django: the tag takes no variables, so quoting is not
    // meaningful and `{% templatetag "openblock" %}` is an invalid argument
    // rather than a quoted one. The distinguishing case is a quoted phrase with
    // a space — `{% templatetag "open block" %}` — where the raw split sees
    // three arguments (a count error) and `split_contents` sees two (an
    // invalid-argument error).
    let bits = pyunicode::split(&token.contents);
    if bits.len() != 2 {
        return Err(Error::syntax("'templatetag' statement takes one argument"));
    }

    let tag = bits[1];
    if !TEMPLATETAG_ARGUMENTS.contains(&tag) {
        // `tag` — arbitrary template text — is *not* passed through
        // `py_repr_str`: Django's own message interpolates it with `%s`, so this
        // uses `{}`. The only `%r` here is `py_list_repr(&TEMPLATETAG_ARGUMENTS)`
        // over the constant above, which is ASCII by construction and is why
        // that function's assertion is not tripped from this path.
        return Err(Error::syntax(format!(
            "Invalid templatetag argument: '{}'. Must be one of: {}",
            tag,
            py_list_repr(&TEMPLATETAG_ARGUMENTS),
        )));
    }

    Ok(NodeDesc::new("TemplateTagNode").attr("tagtype", AttrValue::Str(tag.to_string())))
}

/// `{% verbatim %}…{% endverbatim %}`.
fn tag_verbatim(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    let nodelist = parser.parse(&["endverbatim"])?;
    parser.delete_first_token();
    Ok(NodeDesc::new("VerbatimNode")
        .attr("content", AttrValue::SafeStr(render_text_only(&nodelist)?)))
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

    #[test]
    fn the_registry_holds_exactly_the_nine_m2_tags() {
        const NINE: [&str; 9] = [
            "autoescape",
            "comment",
            "csrf_token",
            "debug",
            "now",
            "resetcycle",
            "spaceless",
            "templatetag",
            "verbatim",
        ];
        for name in NINE {
            assert!(builtin_tag(name).is_some(), "{name} is not registered");
        }
        // The eighteen that wait on other milestones must NOT be registered. A
        // half-registered tag is worse than an absent one: the error message
        // stops naming the real problem, and `{% if %}` reaching a
        // half-implementation is how a wrong tree gets committed.
        //
        // The list is Django 6.0.9's registry minus the nine above, and it is
        // easy to get wrong by hand: the pinned checkout has **27** built-ins
        // (24 in `defaulttags.py`, 3 in `loader_tags.py`), not 26 — `querystring`
        // is registered with `@register.simple_tag` and is absent from earlier
        // enumerations in this plan. `lorem` is likewise a plain `@register.tag`
        // that reads `split_contents`. Both belong here.
        for name in [
            "if",
            "for",
            "block",
            "extends",
            "include",
            "load",
            "with",
            "filter",
            "cycle",
            "firstof",
            "ifchanged",
            "regroup",
            "url",
            "widthratio",
            "partialdef",
            "partial",
            "lorem",
            "querystring",
        ] {
            assert!(
                builtin_tag(name).is_none(),
                "{name} must not be registered yet"
            );
        }
        assert!(builtin_tag("nope").is_none());
    }

    #[test]
    fn autoescape_reads_its_body_and_records_the_setting() {
        let on = compile("{% autoescape on %}x{% endautoescape %}");
        assert_eq!(on.class_name, "AutoEscapeControlNode");
        assert_eq!(attr(&on, "setting"), &AttrValue::Bool(true));
        assert_eq!(on.nodelists[0].0, "nodelist");
        assert_eq!(texts(&on.nodelists[0].1), vec!["x"]);

        let off = compile("{% autoescape off %}{% endautoescape %}");
        assert_eq!(attr(&off, "setting"), &AttrValue::Bool(false));
        assert!(texts(&off.nodelists[0].1).is_empty());
    }

    #[test]
    fn autoescape_nests() {
        let outer = compile(
            "{% autoescape off %}{% autoescape on %}x{% endautoescape %}{% endautoescape %}",
        );
        assert_eq!(attr(&outer, "setting"), &AttrValue::Bool(false));
        let inner = &outer.nodelists[0].1;
        assert_eq!(inner.nodes.len(), 1);
        assert_eq!(inner.nodes[0].class_name, "AutoEscapeControlNode");
        assert_eq!(attr(&inner.nodes[0], "setting"), &AttrValue::Bool(true));
        assert_eq!(texts(&inner.nodes[0].nodelists[0].1), vec!["x"]);
    }

    #[test]
    fn autoescape_uses_the_raw_split_not_split_contents() {
        // `"on off"` is one bit to `split_contents` (which keeps a quoted phrase
        // whole) and two to the raw split, so this count error is reachable only
        // through the raw split: a `split_contents` implementation would see two
        // arguments and fail on the *value* instead. The plan's original input,
        // `{% autoescape a"b c %}`, does not distinguish them — `smart_split`
        // splits the unmatched trailing quote exactly as `str.split` does — so it
        // passed under either implementation (verified against Django 6.0.9).
        assert_eq!(
            failure("{% autoescape \"on off\" %}").kind,
            ErrorKind::Syntax("'autoescape' tag requires exactly one argument.".to_string()),
        );
    }

    #[test]
    fn autoescape_rejects_the_value_django_rejects() {
        assert_eq!(
            failure("{% autoescape maybe %}").kind,
            ErrorKind::Syntax("'autoescape' argument should be 'on' or 'off'".to_string()),
        );
    }

    #[test]
    fn comment_skips_its_body_without_compiling_it() {
        // A syntax error inside the body must not fail the parse: the body is
        // never compiled (Review Focus item 9).
        let node = compile("{% comment %}{% nope %}{{ x }}a{% endcomment %}");
        assert_eq!(node.class_name, "CommentNode");
        assert!(node.attrs.is_empty(), "CommentNode takes no arguments");
        assert!(
            node.nodelists.is_empty(),
            "the body must be discarded, not attached",
        );
    }

    #[test]
    fn an_empty_comment_is_fine() {
        assert_eq!(
            compile("{% comment %}{% endcomment %}").class_name,
            "CommentNode"
        );
    }

    #[test]
    fn csrf_token_and_debug_read_no_arguments() {
        assert_eq!(compile("{% csrf_token %}").class_name, "CsrfTokenNode");
        assert_eq!(compile("{% csrf_token junk %}").class_name, "CsrfTokenNode");
        assert_eq!(compile("{% debug %}").class_name, "DebugNode");
        assert_eq!(compile("{% debug junk more %}").class_name, "DebugNode");
    }

    #[test]
    fn now_strips_the_surrounding_characters() {
        let node = compile(r#"{% now "jS F Y H:i" %}"#);
        assert_eq!(node.class_name, "NowNode");
        assert_eq!(
            attr(&node, "format_string"),
            &AttrValue::Str("jS F Y H:i".to_string()),
        );
        assert!(
            node.attrs.iter().all(|(name, _)| name != "asvar"),
            "no `as` means the keyword is absent, not None-valued",
        );
    }

    #[test]
    fn now_with_as_drops_the_last_two_bits() {
        let node = compile(r#"{% now "Y" as year %}"#);
        assert_eq!(
            attr(&node, "format_string"),
            &AttrValue::Str("Y".to_string())
        );
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("year".to_string()));
    }

    #[test]
    fn now_clamps_like_python_when_the_argument_is_short() {
        // `s[1:-1]` clamps; a Rust `[1..len - 1]` would panic on `""` and `"5"`.
        assert_eq!(strip_first_and_last(""), "");
        assert_eq!(strip_first_and_last("a"), "");
        assert_eq!(strip_first_and_last("ab"), "");
        assert_eq!(strip_first_and_last("abc"), "b");
        assert_eq!(strip_first_and_last("héllo"), "éll");
        assert_eq!(
            attr(&compile("{% now 5 %}"), "format_string"),
            &AttrValue::Str(String::new()),
        );
    }

    #[test]
    fn now_does_not_check_that_the_argument_was_quoted() {
        // Django slices regardless, so `abc` yields `b`.
        assert_eq!(
            attr(&compile("{% now abc %}"), "format_string"),
            &AttrValue::Str("b".to_string()),
        );
    }

    #[test]
    fn now_rejects_the_forms_django_rejects() {
        for source in [
            "{% now %}",
            "{% now a b %}",
            "{% now a b c %}",
            "{% now a b c d %}",
            "{% now \"Y\" as %}",
        ] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax("'now' statement takes one argument".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn resetcycle_reports_the_two_empty_registry_cases() {
        assert_eq!(
            failure("{% resetcycle %}").kind,
            ErrorKind::Syntax("No cycles in template.".to_string()),
        );
        assert_eq!(
            failure("{% resetcycle x %}").kind,
            ErrorKind::Syntax("Named cycle 'x' does not exist.".to_string()),
        );
    }

    #[test]
    fn resetcycle_counts_arguments_before_looking_anything_up() {
        // The count check is first, so its message wins even with an empty
        // registry — and it is the only one of the three that quotes the command
        // with `%r`.
        assert_eq!(
            failure("{% resetcycle a b %}").kind,
            ErrorKind::Syntax("'resetcycle' tag accepts at most one argument.".to_string()),
        );
    }

    #[test]
    fn resetcycle_finds_a_named_cycle_the_parser_already_holds() {
        // M2 cannot reach this through a template, so the registry is populated
        // by hand. It is the only check that the *success* shape — and the
        // `AttrValue::Ref` it carries — is what M5 will need.
        let mut parser = Parser::new(Lexer::new("{% resetcycle named %}").tokenize(), &NoHost);
        let cycle = NodeRef(11);
        parser.named_cycle_nodes.insert("named".to_string(), cycle);
        let nodelist = parser.parse(&[]).unwrap();

        assert_eq!(nodelist.nodes[0].class_name, "ResetCycleNode");
        assert_eq!(attr(&nodelist.nodes[0], "node"), &AttrValue::Ref(cycle));
    }

    #[test]
    fn resetcycle_falls_back_to_the_last_cycle_node() {
        let mut parser = Parser::new(Lexer::new("{% resetcycle %}").tokenize(), &NoHost);
        let cycle = NodeRef(12);
        parser.last_cycle_node = Some(cycle);
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(attr(&nodelist.nodes[0], "node"), &AttrValue::Ref(cycle));
    }

    #[test]
    fn spaceless_reads_its_body() {
        let node = compile("{% spaceless %}  a  {% endspaceless %}");
        assert_eq!(node.class_name, "SpacelessNode");
        assert_eq!(node.nodelists[0].0, "nodelist");
        assert_eq!(texts(&node.nodelists[0].1), vec!["  a  "]);
    }

    #[test]
    fn templatetag_accepts_every_mapped_argument() {
        for name in TEMPLATETAG_ARGUMENTS {
            let node = compile(&format!("{{% templatetag {name} %}}"));
            assert_eq!(node.class_name, "TemplateTagNode");
            assert_eq!(attr(&node, "tagtype"), &AttrValue::Str(name.to_string()));
        }
    }

    #[test]
    fn templatetag_rejects_anything_else_with_the_full_ordered_list() {
        // The list must be in Django's mapping order, because Python's `list()`
        // of a dict literal is insertion-ordered and the message is compared
        // exactly.
        assert_eq!(
            failure("{% templatetag nope %}").kind,
            ErrorKind::Syntax(
                "Invalid templatetag argument: 'nope'. Must be one of: \
                 ['openblock', 'closeblock', 'openvariable', 'closevariable', \
                 'openbrace', 'closebrace', 'opencomment', 'closecomment']"
                    .to_string(),
            ),
        );
    }

    #[test]
    fn templatetag_does_not_strip_quotes() {
        // The quotes are part of the argument, so it is invalid. This pins that
        // *neither* splitter strips them — `smart_split` keeps the quotes too —
        // so it is not a raw-vs-smart discriminator; the test below is.
        match failure(r#"{% templatetag "openblock" %}"#).kind {
            ErrorKind::Syntax(message) => {
                assert!(message.contains("'\"openblock\"'"), "{message}");
            }
            other => panic!("expected a syntax error, got {other:?}"),
        }
    }

    #[test]
    fn templatetag_uses_the_raw_split_not_split_contents() {
        // `"open block"` is two bits to `split_contents` and three to the raw
        // split, so this count error is reachable only through the raw split: a
        // `split_contents` implementation would reach the invalid-argument
        // error for `"open block"` instead. `{% templatetag "openblock" %}`
        // cannot distinguish them (verified against Django 6.0.9).
        assert_eq!(
            failure(r#"{% templatetag "open block" %}"#).kind,
            ErrorKind::Syntax("'templatetag' statement takes one argument".to_string()),
        );
    }

    #[test]
    fn templatetag_requires_exactly_one_argument() {
        for source in ["{% templatetag %}", "{% templatetag a b %}"] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax("'templatetag' statement takes one argument".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn verbatim_content_is_computed_at_parse_time_and_kept_safe() {
        let node = compile("{% verbatim %}{{ x }}{% endverbatim %}");
        assert_eq!(node.class_name, "VerbatimNode");
        assert!(
            node.nodelists.is_empty(),
            "the body is rendered at parse time, not kept as a nodelist",
        );
        match attr(&node, "content") {
            AttrValue::SafeStr(content) => assert_eq!(content, "{{ x }}"),
            other => panic!("verbatim content must be a SafeStr, got {other:?}"),
        }
    }

    #[test]
    fn verbatim_bodies_are_not_shared_between_instances() {
        let mut parser = Parser::new(
            Lexer::new("{% verbatim %}a{% endverbatim %}{% verbatim %}b{% endverbatim %}")
                .tokenize(),
            &NoHost,
        );
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(
            attr(&nodelist.nodes[0], "content"),
            &AttrValue::SafeStr("a".to_string()),
        );
        assert_eq!(
            attr(&nodelist.nodes[1], "content"),
            &AttrValue::SafeStr("b".to_string()),
        );
    }

    #[test]
    fn an_empty_verbatim_body_is_the_empty_string() {
        assert_eq!(
            attr(&compile("{% verbatim %}{% endverbatim %}"), "content"),
            &AttrValue::SafeStr(String::new()),
        );
    }

    #[test]
    fn render_text_only_refuses_a_non_text_body() {
        // Unreachable through a real parse at M2, which is the point: a milestone
        // that makes it reachable must get a message, not a wrong answer.
        let nodelist = NodeListDesc {
            nodes: vec![NodeDesc::new("DebugNode")],
            contains_nontext: true,
        };
        assert!(matches!(
            render_text_only(&nodelist).unwrap_err().kind,
            ErrorKind::Unimplemented(_),
        ));
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
