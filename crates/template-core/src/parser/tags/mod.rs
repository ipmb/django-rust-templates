//! One module per built-in tag (design spec §10.4). **A helper needed by two
//! tags lives here, not in either tag's file**, added by the central commit
//! rather than by a fan-out agent. Each tag owns `tags/<name>.rs` and exports
//! `pub fn compile`; `crate::parser::registry` maps the command name to it.

pub mod autoescape;
pub mod block;
pub mod cache;
pub mod comment;
pub mod csrf_token;
pub mod cycle;
pub mod debug;
pub mod extends;
pub mod filter;
pub mod firstof;
// `for` and `if` are Rust keywords: the module name is `for_tag`/`if_tag` while
// `#[path]` keeps the file named after the command.
#[path = "for.rs"]
pub mod for_tag;
pub mod i18n;
#[path = "if.rs"]
pub mod if_tag;
pub mod ifchanged;
pub mod include;
pub mod l10n;
pub mod lorem;
pub mod now;
pub mod partial;
pub mod partialdef;
pub mod regroup;
pub mod resetcycle;
pub mod spaceless;
// `static` is a Rust keyword in edition 2018+ (`pub mod static`), so the same
// `#[path]` idiom: the file keeps the command's name.
#[path = "static.rs"]
pub mod static_tag;
pub mod templatetag;
pub mod tz;
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

/// `Token.split_contents`, as a compile function sees it. Django's `next(bits)`
/// on an exhausted iterator is a bare `StopIteration` with `.token` attached,
/// not a `TemplateSyntaxError` — the type M1's FFI binding already fixes.
fn split_contents(token: &Token) -> Result<Vec<String>, Error> {
    token
        .split_contents()
        .map_err(|_| Error::unterminated_marker())
}

/// Python's `s[1:-1]`: first and last *characters* dropped, clamping where
/// Python clamps. Django doesn't check `{% now %}`'s argument is quoted, so
/// `{% now 5 %}` yields `""`; `[1..len - 1]` on a `&str` panics on a
/// one-character argument.
fn strip_first_and_last(s: &str) -> String {
    let mut chars = s.chars();
    chars.next();
    chars.next_back();
    chars.as_str().to_string()
}

/// Python's `repr()` for a `str`, as `"%r"` interpolation applies it: quote `'`
/// unless the string contains `'` and no `"`, then escape backslash, the chosen
/// quote, and control characters as `\xNN`/`\uNNNN`/`\UNNNNNNNN`.
///
/// **Exact on ASCII only, enforced as such.** Python also escapes non-printable
/// non-ASCII (Cf/Cs/Co/Cn/Zl/Zp/Zs), which `char::is_control()` — Cc alone — does
/// not cover. Both M2 call sites are ASCII by construction (`args[0]` is the
/// dispatched tag name, `TEMPLATETAG_ARGUMENTS` a constant); user text must route
/// through the host's `repr` (design spec §10.4), never widen this.
pub(crate) fn py_repr_str(s: &str) -> String {
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
/// ASCII-only like [`py_repr_str`], for the same reason: the input is a constant.
fn py_list_repr(items: &[&str]) -> String {
    let rendered: Vec<String> = items.iter().map(|item| py_repr_str(item)).collect();
    format!("[{}]", rendered.join(", "))
}

/// Python's `repr()` of a list of **arbitrary** strings, as `"%r" % [str, …]`
/// renders it: the user-text counterpart of [`py_list_repr`].
///
/// Each element goes through [`Parser::py_repr`] — the host's real `repr` —
/// because the elements are template text, exactly what `py_repr_str`'s
/// ASCII-only assertion refuses. Shared by `tz.rs` (`tz.py:197-199`) and `i18n.rs`
/// (`i18n.py:237,260,287,330,353`); generic over the element type so a
/// `Vec<String>` and a `Vec<&str>` both pass through without a conversion.
///
/// [`Parser::py_repr`]: crate::parser::Parser::py_repr
pub(crate) fn python_list_repr<T: AsRef<str>>(
    parser: &crate::parser::Parser<'_>,
    items: &[T],
) -> String {
    let rendered: Vec<String> = items
        .iter()
        .map(|item| parser.py_repr(item.as_ref()))
        .collect();
    format!("[{}]", rendered.join(", "))
}

// ---------------------------------------------------------------------------
// Test-only stand-ins, each for one property no M2 built-in can reach:
//
//   standin        reads its own body, exercising `parse(until)`,
//                  `prepend_token`, `delete_first_token`, nesting and
//                  `contains_nontext`
//   standin_ref    carries `AttrValue::Ref`, the shape `FilterExpression` uses
//   standin_first  a `must_be_first` node, testing the ordering rule and the
//                  host-supplied origin repr
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
/// A `match` rather than `REGISTRY` rows: a row would name a tag Django's
/// registry lacks, which `tests/test_tag_inventory.py` cross-checks against the
/// real set. `registry::builtin_tag` consults this before the table.
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
/// test module — one copy, so a tag's tests cannot pass against a harness no
/// other tag used. `pub(crate)` under `#[cfg(test)]`, so nothing ships it.
#[cfg(test)]
pub(crate) mod support {
    use crate::lexer::Lexer;
    use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc, NodeRef};
    use crate::parser::error::Error;
    use crate::parser::host::NoHost;
    use crate::parser::Parser;
    use crate::parser::TEXT_NODE;
    use crate::token::Token;
    use std::cell::{Cell, RefCell};

    /// Parse `source` with no host and return the whole top-level nodelist — for
    /// tests whose subject is the *list*: `get_nodes_by_type` walks one, so a
    /// helper returning a single node could not express the multi-sibling case.
    pub(crate) fn nodelist(source: &str) -> NodeListDesc {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        parser.parse(&[]).expect("expected a successful parse")
    }

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

    /// The error `source` fails with, with an origin template name attached. The
    /// parameter is the origin's **name**, not a rendered `repr`: the field holds
    /// `Origin.template_name` and `extend_nodelist` derives the quoted form, so a
    /// caller passing `"tpl.html"` is what exercises that derivation.
    pub(crate) fn failure_with_origin(source: &str, name: Option<&str>) -> Error {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        if let Some(name) = name {
            parser = parser.with_origin_template_name(name.to_string());
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

    /// A host that hands out a **fresh slot index per call**. [`NoHost`] answers
    /// `0` to every `alloc_slot`, which is wrong for any test that has to see the
    /// table's *sequence*: under `NoHost`, "two definitions reserve two slots" and
    /// "two definitions collide on one slot" produce the same descriptors. `Cell`
    /// rather than `&mut self` because `alloc_slot` takes `&self`, as the real
    /// host's interior mutability does.
    #[derive(Default)]
    pub(crate) struct CountingHost {
        next: std::cell::Cell<usize>,
    }

    impl crate::parser::host::Host for CountingHost {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(NodeDesc, Vec<Token>), Error> {
            unreachable!("CountingHost::call_tag is never reached: has_tag returns false")
        }

        fn alloc_slot(&self) -> usize {
            let slot = self.next.get();
            self.next.set(slot + 1);
            slot
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            // The counter, not the null handle: a condition's operand *is* a
            // reservation, so returning `0` every time would make
            // `{% if a and b %}`'s two `NodeRef`s indistinguishable. `NodeRef` is
            // a `u64` handle into the same table `alloc_slot` indexes, so the
            // `as u64` is its width, not a narrowing. Nothing is built: the handle
            // names a table entry no test fills.
            Ok(NodeRef(self.alloc_slot() as u64))
        }
    }

    /// Parse `source` against `host`, returning the whole top-level nodelist.
    /// [`compile`] asserts a single node; this one does not, because its tests are
    /// about a relationship between two nodes.
    pub(crate) fn parse_with(source: &str, host: &dyn crate::parser::host::Host) -> NodeListDesc {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), host);
        parser.parse(&[]).expect("expected a successful parse")
    }

    /// One `register_partial_def` call, as the tag made it.
    #[derive(Debug, PartialEq, Eq)]
    pub(crate) struct PartialDefCall {
        pub(crate) name: String,
        pub(crate) inline: bool,
        /// The body's text nodes, in order — `texts` rather than the descriptor,
        /// so the host does not need `NodeListDesc: Clone` to keep one.
        pub(crate) body: Vec<String>,
        pub(crate) source_start: Option<usize>,
        pub(crate) source_end: Option<usize>,
    }

    /// The host `{% partial %}` and `{% partialdef %}` compile under.
    ///
    /// Both answers are deliberately uninformative about *materialisation* —
    /// `partial_node` returns a fixed handle and `register_partial_def` records
    /// the call and returns another, because nothing parsed here is ever
    /// materialised and `NodeRef`s index a table the real host owns. The rows test
    /// what the **tag** passes across the seam; what the shell does with it is
    /// `tests/test_partials_rust.py`'s subject.
    #[derive(Default)]
    pub(crate) struct PartialHost {
        pub(crate) defs: RefCell<Vec<PartialDefCall>>,
        pub(crate) slots: Cell<usize>,
    }

    impl crate::parser::host::Host for PartialHost {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(NodeDesc, Vec<Token>), Error> {
            unreachable!("PartialHost::call_tag is never reached: has_tag returns false")
        }

        fn alloc_slot(&self) -> usize {
            let slot = self.slots.get();
            self.slots.set(slot + 1);
            slot
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(0))
        }

        fn partial_node(&self, _name: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(0))
        }

        fn register_partial_def(
            &self,
            _token: &Token,
            name: &str,
            inline: bool,
            body: &NodeListDesc,
            source_start: Option<usize>,
            source_end: Option<usize>,
        ) -> Result<NodeRef, Error> {
            self.defs.borrow_mut().push(PartialDefCall {
                name: name.to_string(),
                inline,
                body: texts(body),
                source_start,
                source_end,
            });
            Ok(NodeRef(0))
        }
    }

    /// A host that reports an **explicit set** of library tags as loaded.
    ///
    /// Library tags are reachable in Rust only behind [`Host::library_tag_loaded`],
    /// and [`NoHost`] answers the trait default `false`, so `{% static %}` under it
    /// is the *correct* unloaded case; these tests need the loaded one.
    ///
    /// The set is a parameter rather than `library_tag_names()` because
    /// `registry::library_tag` reads the same table: `library_tag_loaded(name) &&
    /// library_tag(name).is_none()` could then never be true, and the arm handling
    /// that case was dead code. A caller-supplied set also matches `ShellHost`'s
    /// shape, and lets a row load a name Rust has **no** body for in order to pin
    /// the refusal.
    ///
    /// [`Host::library_tag_loaded`]: crate::parser::host::Host::library_tag_loaded
    #[cfg(test)]
    pub(crate) struct LoadedLibrariesHost(pub Vec<&'static str>);

    #[cfg(test)]
    impl crate::parser::host::Host for LoadedLibrariesHost {
        fn has_tag(&self, _command: &str) -> bool {
            // The library tags Rust compiles are *not* host tags —
            // `ShellHost.has_tag`'s identity clause decides that for the real
            // host — which is why these names needed a second registry table.
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(NodeDesc, Vec<Token>), Error> {
            unreachable!(
                "LoadedLibrariesHost::call_tag is never reached: has_tag returns false \
                 for every command"
            )
        }

        fn library_tag_loaded(&self, command: &str) -> bool {
            self.0.contains(&command)
        }

        fn alloc_slot(&self) -> usize {
            // `NoHost`'s answer, honest here for the same reason: no library tag
            // allocates a slot and nothing parsed under this host is materialised.
            0
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            // `NoHost`'s answer, and not a gap for these four tags: each compiles
            // its operands to `AttrValue::FilterExpr` via `Parser::compile_filter`,
            // which does not cross this seam. The seam method is `{% if %}`'s
            // operand handle.
            Ok(NodeRef(0))
        }
    }

    /// The loaded set the library modules' own rows run under: exactly the names
    /// Rust has a body for, so those rows exercise the tag and not the routing.
    /// (The routing rows pass literals, so the two questions can disagree.)
    fn rust_libraries_loaded() -> Vec<&'static str> {
        crate::parser::registry::library_tag_names()
    }

    /// [`compile`], against the libraries Rust has bodies for — the
    /// loaded-library case.
    pub(crate) fn compile_loaded(source: &str) -> NodeDesc {
        let host = LoadedLibrariesHost(rust_libraries_loaded());
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &host);
        let nodelist = parser.parse(&[]).expect("expected a successful parse");
        assert_eq!(
            nodelist.nodes.len(),
            1,
            "expected exactly one top-level node in {source:?}",
        );
        nodelist.nodes.into_iter().next().expect("checked above")
    }

    /// [`failure`], against the same host — the loaded-library case.
    pub(crate) fn failure_loaded(source: &str) -> Error {
        let host = LoadedLibrariesHost(rust_libraries_loaded());
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &host);
        parser.parse(&[]).expect_err("expected a parse failure")
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
        // `delete_first_token`: a parser that consumed it here would report the
        // *second* block as unclosed.
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
        // non-text, which makes `{% extends %}` refuse a legal template.
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
            Some("tpl.html"),
        );
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "{% standin_first %} must be the first tag in 'tpl.html'.".to_string(),
            ),
        );
        // The outer token is on line 1 and the message alone cannot tell the two
        // apart; an `error()` that overwrote the token would report line 1 here.
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

    /// Django's test is **truthiness**, not `is None` (`base.py:602-606`), so an
    /// empty origin name says "the template" too. An `Option`-shaped port answers
    /// `''` instead, and the `None` row above cannot tell the two apart — this row
    /// does.
    #[test]
    fn an_empty_origin_name_says_the_template_too() {
        let error = failure_with_origin("{% standin_first %}{% standin_first %}", Some(""));
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
        // `must_be_first` tag is legal — text nodes do not set the flag.
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

        fn call_tag(
            &self,
            command: &str,
            _token: &Token,
            tokens: &[Token],
        ) -> Result<(NodeDesc, Vec<Token>), Error> {
            if command == "hosty" {
                // Consumes nothing and pushes nothing back, so the stream is
                // returned unchanged — the identity case of the cursor handoff.
                Ok((NodeDesc::new("StandinHostNode"), tokens.to_vec()))
            } else {
                Err(Error::host("the host refused"))
            }
        }

        fn alloc_slot(&self) -> usize {
            // Neither of this host's tags names a slot, and zero keeps `&TestHost`
            // a promotable unit struct.
            0
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            // Accepted, like `NoHost`: this host exists to make dispatch order
            // observable, and `{% if %}` is not one of the tags it dispatches.
            Ok(NodeRef(0))
        }
    }

    #[test]
    fn a_host_tag_is_dispatched_to_the_host() {
        let mut parser = Parser::new(Lexer::new("{% hosty %}").tokenize(), &TestHost);
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(nodelist.nodes[0].class_name, "StandinHostNode");
        assert_eq!(nodelist.nodes[0].token.as_ref().unwrap().contents, "hosty");
    }

    #[test]
    fn the_host_wins_over_a_builtin_of_the_same_name() {
        // Django's registry is one dict `add_library` updates in place, so a later
        // registration shadows an earlier one and `{% load %}` runs after the
        // builtins: host-first is faithful. The converse obligation — the shell
        // must not register a tag Rust implements — is a note on `Parser::parse`.
        let mut parser = Parser::new(Lexer::new("{% standin %}").tokenize(), &TestHost);
        let error = parser
            .parse(&[])
            .expect_err("the host shadows the built-in");
        assert_eq!(error.kind, Error::host("the host refused").kind);
    }

    #[test]
    fn a_host_error_gets_the_token_attached_by_parse() {
        // `error()` is kind-agnostic; the `Host` branch is exercised here rather
        // than assumed.
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
        // Not `héllo`: printable non-ASCII passes through correctly in Python 3,
        // so that input would pass while the function stayed wrong for every
        // non-printable non-ASCII point. The `debug_assert` fails loudly instead.
        py_repr_str("héllo");
    }

    #[test]
    fn python_whitespace_separators_split_arguments() {
        // Python's `str.isspace()` is true for U+001C..U+001F as well as Unicode
        // `White_Space`, and every split Django performs uses that table, so
        // `{% autoescape<U+001C>on %}` is two arguments to Django and is accepted.
        // This pins that the parser *inherits* `pyunicode`'s corrected table
        // (commit `1a013ae`) rather than re-deriving splitting for itself.
        let node = compile("{% autoescape\u{1c}on %}x{% endautoescape %}");
        assert_eq!(attr(&node, "setting"), &AttrValue::Bool(true));
    }
}
