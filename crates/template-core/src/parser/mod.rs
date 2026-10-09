//! Parsing, following `django.template.base.Parser`.
//!
//! `parse` is a direct port of Django's loop (`base.py:529-591`). Four details
//! are easy to lose and each has a test below:
//!
//! - tags are dispatched on `token.contents.split()[0]`, the **raw** first word
//!   — `split_contents` is for a tag's arguments afterwards, and the two
//!   disagree whenever the first word is quoted (`{% 'a b' %}`, where the raw
//!   split yields `'a`) or contains U+001C-U+001F;
//! - reaching `parse_until` **prepends** the terminator and returns, leaving the
//!   caller to consume it;
//! - `tokens` is reversed, so `delete_first_token` deletes `tokens[-1]`;
//! - an error keeps the **innermost** token, because `error()` only sets one
//!   when there is none.

pub mod desc;
pub mod error;
pub mod host;
pub mod registry;
pub mod tags;
pub mod variable;

pub use desc::{AttrValue, NodeDesc, NodeListDesc, NodeRef, TokenDesc, VariableDesc, TEXT_NODE};
pub use error::{Error, ErrorKind};
pub use host::{Host, NoHost};
pub use registry::CompileFn;

use std::collections::HashMap;

use crate::pyunicode;
use crate::text::get_text_list;
use crate::token::{Token, TokenType};

/// The parser, ported from `django.template.base.Parser`.
pub struct Parser<'a> {
    /// Reversed, so the *front* of the stream is the *end* of the vec:
    /// `next_token` is `pop`, `prepend_token` is `push`, `delete_first_token`
    /// is `pop`. That is Django's `list(reversed(tokens))`, and it is the reason
    /// `delete_first_token` deletes `tokens[-1]` (Review Focus item 4).
    tokens: Vec<Token>,
    /// `(command, token)` for every tag currently being compiled, innermost
    /// last. `unclosed_block_tag` pops it to name the tag that was left open.
    command_stack: Vec<(String, Token)>,
    host: &'a dyn Host,
    /// Python's `repr()` of `Origin.template_name`, or `None` when the origin
    /// has no template name.
    ///
    /// Only `extend_nodelist`'s must-be-first message reads it, and it arrives
    /// pre-rendered because a Python `repr` is Python's to produce — the same
    /// rule §5.2 already records for `LookupError::Missing::current_repr`.
    /// Python's `repr` escapes by Unicode category and Rust has no categories
    /// without a new dependency; guessing would put a subtly wrong string in a
    /// message the suite compares exactly.
    origin_template_repr: Option<String>,
    /// `parser._last_cycle_node` and `parser._named_cycle_nodes`, which
    /// `{% cycle %}` sets and `{% resetcycle %}` reads by name or by default.
    ///
    /// Both hold host-owned `CycleNode`s, so they are handles here. M2 only ever
    /// reads them and always finds them empty, because `{% cycle %}` is M5; the
    /// fields exist now so that `{% resetcycle %}`'s two error paths are the
    /// real ones rather than a hard-coded "no cycles".
    last_cycle_node: Option<NodeRef>,
    named_cycle_nodes: HashMap<String, NodeRef>,
}

impl<'a> Parser<'a> {
    /// `Parser.__init__(tokens, libraries=None, builtins=None, origin=None)`.
    ///
    /// `libraries`, `builtins`, `tags`, `filters` and `extra_data` are absent on
    /// purpose:
    ///
    /// - `tags`/`filters` are the host's registry. §5.2's re-entrancy contract
    ///   has the host own it — `{% load %}` (M6) mutates it mid-parse — and a
    ///   Rust copy would be a second source of truth.
    /// - `extra_data` must be a real Python `dict` holding Python objects
    ///   (`{% load %}` stores a library, `{% partialdef %}` a
    ///   `PartialTemplate` proxy), so a Rust `HashMap` would need converting
    ///   back on every read. Python's field, arriving with the host.
    pub fn new(tokens: Vec<Token>, host: &'a dyn Host) -> Self {
        let mut tokens = tokens;
        tokens.reverse();
        Self {
            tokens,
            command_stack: Vec::new(),
            host,
            origin_template_repr: None,
            last_cycle_node: None,
            named_cycle_nodes: HashMap::new(),
        }
    }

    /// Set the origin's `repr`, as `Parser(…, origin=…)` plus Python's `repr`
    /// would have produced it.
    #[must_use]
    pub fn with_origin_template_repr(mut self, repr: String) -> Self {
        self.origin_template_repr = Some(repr);
        self
    }

    /// `Parser.next_token` — `self.tokens.pop()`.
    pub fn next_token(&mut self) -> Option<Token> {
        self.tokens.pop()
    }

    /// `Parser.prepend_token` — `self.tokens.append(token)`.
    pub fn prepend_token(&mut self, token: Token) {
        self.tokens.push(token);
    }

    /// `Parser.delete_first_token` — `del self.tokens[-1]`.
    ///
    /// Django would raise `IndexError` on an empty list; that cannot happen
    /// here, because every caller reaches this after `parse(until)` returned,
    /// which only happens when the terminator was prepended. A real `assert!`
    /// rather than `debug_assert!`, because `debug_assert!` is compiled out in
    /// the build this ships in.
    pub fn delete_first_token(&mut self) {
        assert!(
            self.tokens.pop().is_some(),
            "delete_first_token with an empty token list"
        );
    }

    /// `Parser.skip_past(endtag)`.
    ///
    /// Loops until a **block** token whose contents *equal* `endtag` — a
    /// variable or comment token with the same contents does not terminate it —
    /// and otherwise reports an unclosed block. `{% comment %}` uses this rather
    /// than `parse`, so its body is never compiled at all (Review Focus item 9).
    pub fn skip_past(&mut self, endtag: &str) -> Result<(), Error> {
        while let Some(token) = self.next_token() {
            if token.token_type == TokenType::Block && token.contents == endtag {
                return Ok(());
            }
        }
        Err(self.unclosed_block_tag(&[endtag]))
    }

    /// `Parser.parse(parse_until=None)`.
    pub fn parse(&mut self, parse_until: &[&str]) -> Result<NodeListDesc, Error> {
        let mut nodelist = NodeListDesc::default();

        while let Some(token) = self.next_token() {
            match token.token_type {
                TokenType::Text => {
                    let node = NodeDesc::text(&token.contents);
                    self.extend_nodelist(&mut nodelist, node, &token)?;
                }
                TokenType::Comment => {
                    // Django's `parse` has no branch for COMMENT, so a comment
                    // token contributes neither a node nor a
                    // `contains_nontext` flip. Easy to "improve" by accident.
                }
                TokenType::Var => {
                    if token.contents.is_empty() {
                        return Err(self.error(
                            &token,
                            Error::syntax(format!("Empty variable tag on line {}", token.lineno)),
                        ));
                    }
                    // `self.compile_filter(token.contents)` is M3's. Reaching a
                    // variable before then is a real gap, and it is reported
                    // rather than papered over: a placeholder descriptor would
                    // materialise into a node that renders nothing, which is
                    // exactly the "passes a shallow test, wrong forever"
                    // outcome the ownership split exists to avoid.
                    return Err(self.error(
                        &token,
                        Error::unimplemented(
                            "variable compilation needs FilterExpression, which arrives in M3",
                        ),
                    ));
                }
                TokenType::Block => {
                    // The raw first word, not `split_contents()`: `{% 'a b' %}`
                    // dispatches on `'a`, and using M1's splitter here would
                    // dispatch on `'a b'` because smart_split keeps quoted
                    // phrases whole (Review Focus item 2). Django indexes
                    // `token.contents.split()[0]` and catches `IndexError` for
                    // an empty `contents`, which is the `Empty block tag` case
                    // below — that is `[0]` on an empty list, not the `next(bits)`
                    // exhaustion `split_contents` is subject to.
                    //
                    // It is Python's `str.split` — so `pyunicode::split`, whose
                    // table is `str.isspace()` and so includes U+001C-U+001F —
                    // never `str::split_whitespace`, which would disagree on
                    // those four code points and so on which tag was found.
                    let Some(command) = pyunicode::split(&token.contents).first().copied() else {
                        return Err(self.error(
                            &token,
                            Error::syntax(format!("Empty block tag on line {}", token.lineno)),
                        ));
                    };
                    let command = command.to_string();

                    if parse_until.contains(&command.as_str()) {
                        // Put it back and return: the caller consumes it with
                        // `next_token()`. Consuming it here produces
                        // `endfor`-shaped nodes or silently drops a tag
                        // (Review Focus item 3).
                        self.prepend_token(token);
                        return Ok(nodelist);
                    }

                    self.command_stack.push((command.clone(), token.clone()));

                    // The host is consulted first. Django's registry is one dict
                    // that `add_library` updates in place, so a *later*
                    // registration shadows an earlier one, and `{% load %}` runs
                    // after the engine's builtins — a custom library that
                    // redefines a built-in name wins. The shell must therefore
                    // **not** register the built-ins Rust implements, or
                    // host-first would route them to Python and these compile
                    // functions would never run.
                    let compiled = if self.host.has_tag(&command) {
                        self.host.call_tag(&command, &token)
                    } else if let Some(compile) = registry::builtin_tag(&command) {
                        compile(self, &token)
                    } else {
                        return Err(self.invalid_block_tag(&token, &command, parse_until));
                    };
                    let node = compiled.map_err(|error| self.error(&token, error))?;

                    self.extend_nodelist(&mut nodelist, node, &token)?;
                    // Compile success: remove the token from the command stack.
                    self.command_stack.pop();
                }
            }
        }

        if parse_until.is_empty() {
            Ok(nodelist)
        } else {
            Err(self.unclosed_block_tag(parse_until))
        }
    }

    /// `Parser.error(token, e)` — **returns** the error with the token attached,
    /// and only if it does not already carry one.
    ///
    /// Rust has no `hasattr`, so the "already carries one" test is
    /// `token.is_none()`. That is exactly equivalent: Django's exceptions never
    /// define `token` as a class attribute, so before the first `error()` call
    /// `hasattr` is false and afterwards it is true
    /// (`TemplateSyntaxError.__init__` sets only `self.msg`).
    ///
    /// Django's `if not isinstance(e, Exception)` branch has no counterpart: a
    /// Rust compile function can only *return* an `Error`, so "a tag raised a
    /// string" is unrepresentable here.
    pub fn error(&self, token: &Token, error: Error) -> Error {
        Error {
            kind: error.kind,
            token: error.token.or_else(|| Some(TokenDesc::of(token))),
        }
    }

    /// `Parser.invalid_block_tag(token, command, parse_until=None)`.
    pub fn invalid_block_tag(&self, token: &Token, command: &str, parse_until: &[&str]) -> Error {
        let message = if parse_until.is_empty() {
            format!(
                "Invalid block tag on line {}: '{}'. Did you forget to register or load this tag?",
                token.lineno, command,
            )
        } else {
            let expected: Vec<String> =
                parse_until.iter().map(|name| format!("'{name}'")).collect();
            format!(
                "Invalid block tag on line {}: '{}', expected {}. \
                 Did you forget to register or load this tag?",
                token.lineno,
                command,
                get_text_list(&expected, "or"),
            )
        };
        self.error(token, Error::syntax(message))
    }

    /// `Parser.unclosed_block_tag(parse_until)`.
    ///
    /// Reports the tag that was **left open** — from `command_stack`, not the
    /// terminator that was being looked for (Review Focus item 6) — and the list
    /// it was looking for, joined with `", "` rather than `get_text_list`'s
    /// `" or "`.
    pub fn unclosed_block_tag(&mut self, parse_until: &[&str]) -> Error {
        let Some((command, token)) = self.command_stack.pop() else {
            // Unreachable from `parse` and `skip_past`: both are only reached
            // from inside a tag's compile function, and `parse` pushes the
            // frame before calling it. Django would raise IndexError here.
            return Error::internal("unclosed_block_tag with an empty command stack");
        };
        let message = format!(
            "Unclosed tag on line {}: '{}'. Looking for one of: {}.",
            token.lineno,
            command,
            parse_until.join(", "),
        );
        self.error(&token, Error::syntax(message))
    }

    /// `Parser.extend_nodelist(nodelist, node, token)`.
    ///
    /// This — not a node's `__init__` — is where a node acquires its position,
    /// and it runs **after** the node is constructed, so a node whose
    /// constructor reads its own subtree (`ExtendsNode.__init__` builds
    /// `self.blocks` via `get_nodes_by_type`) sees children and no token
    /// (Review Focus item 10).
    ///
    /// Returns `Result` because Django's must-be-first and command-stack rules
    /// make an append able to fail; in M2 the failure is unreachable for the
    /// nine tags (none sets `must_be_first`) and is reached only by Task 3's
    /// stand-in.
    pub fn extend_nodelist(
        &self,
        nodelist: &mut NodeListDesc,
        mut node: NodeDesc,
        token: &Token,
    ) -> Result<(), Error> {
        if node.must_be_first && nodelist.contains_nontext {
            let origin = match &self.origin_template_repr {
                Some(repr) => repr.clone(),
                None => "the template".to_string(),
            };
            return Err(self.error(
                token,
                Error::syntax(format!(
                    "{{% {} %}} must be the first tag in {}.",
                    token.contents, origin,
                )),
            ));
        }
        // Django asks `isinstance(node, TextNode)`; the descriptor has the class
        // *name*, and no class subclasses TextNode (see `TEXT_NODE`), so this is
        // the same test. Task 7 pins the assumption.
        if node.class_name != TEXT_NODE {
            nodelist.contains_nontext = true;
        }
        node.token = Some(TokenDesc::of(token));
        nodelist.nodes.push(node);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;

    /// Parse `source` with no host and no registered tags.
    fn parse(source: &str) -> Result<NodeListDesc, Error> {
        Parser::new(Lexer::new(source).tokenize(), &NoHost).parse(&[])
    }

    fn failure(source: &str) -> Error {
        parse(source).expect_err("expected a parse failure")
    }

    /// The `s` of every `TextNode`, in order.
    fn texts(nodelist: &NodeListDesc) -> Vec<String> {
        let mut out = Vec::new();
        for node in &nodelist.nodes {
            if node.class_name != TEXT_NODE {
                continue;
            }
            match node.attrs.as_slice() {
                [(name, AttrValue::Str(s))] if name.as_str() == "s" => out.push(s.clone()),
                other => panic!("TextNode attributes are not [(\"s\", Str)]: {other:?}"),
            }
        }
        out
    }

    fn stream(parser: &mut Parser<'_>) -> Vec<String> {
        std::iter::from_fn(|| parser.next_token())
            .map(|token| token.contents)
            .collect()
    }

    #[test]
    fn tokens_are_consumed_in_source_order() {
        // `Parser::new` reverses and `next_token` pops, so the two reversals
        // must cancel. A single reversal shows up here as the *last* token first.
        let mut parser = Parser::new(Lexer::new("a{{ x }}b").tokenize(), &NoHost);
        assert_eq!(stream(&mut parser), vec!["a", "x", "b"]);
    }

    #[test]
    fn prepend_puts_a_token_back_at_the_front() {
        let mut parser = Parser::new(Lexer::new("a{{ x }}b").tokenize(), &NoHost);
        let first = parser.next_token().expect("a first token");
        assert_eq!(first.contents, "a");
        parser.prepend_token(first);
        assert_eq!(stream(&mut parser), vec!["a", "x", "b"]);
    }

    #[test]
    fn delete_first_token_deletes_from_the_front_of_the_stream() {
        // `del self.tokens[-1]` on a reversed vec. The tempting implementation —
        // `remove(0)` or `drain(..1)` on a forward vec — deletes the wrong end,
        // and the symptom is a block tag's *second* parse, i.e. the `{% empty %}`
        // branch of `{% for %}` (Review Focus item 4).
        let mut parser = Parser::new(Lexer::new("a{{ x }}b").tokenize(), &NoHost);
        parser.delete_first_token();
        assert_eq!(stream(&mut parser), vec!["x", "b"]);
    }

    #[test]
    fn unclosed_block_tag_reports_an_internal_error_when_the_stack_is_empty() {
        // The `Internal` path, reachable only by calling the method directly.
        let mut parser = Parser::new(Vec::new(), &NoHost);
        assert_eq!(
            parser.unclosed_block_tag(&["endautoescape"]).kind,
            ErrorKind::Internal("unclosed_block_tag with an empty command stack".to_string()),
        );
    }

    #[test]
    fn text_becomes_text_nodes() {
        assert_eq!(texts(&parse("a\nb").unwrap()), vec!["a\nb"]);
        assert_eq!(parse("").unwrap(), NodeListDesc::default());
        assert_eq!(texts(&parse("a{{").unwrap()), vec!["a{{"]);
    }

    #[test]
    fn comment_tokens_are_dropped_entirely() {
        // No node, and no `contains_nontext` flip either.
        let nodelist = parse("a{# c #}b").unwrap();
        assert_eq!(texts(&nodelist), vec!["a", "b"]);
        assert!(!nodelist.contains_nontext);
    }

    #[test]
    fn empty_variable_tag_reports_its_line() {
        let error = failure("\n{{}}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Empty variable tag on line 2".to_string()),
        );
        assert_eq!(error.token.unwrap().lineno, 2);
    }

    #[test]
    fn empty_block_tag_reports_its_line() {
        let error = failure("\n{%  %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Empty block tag on line 2".to_string()),
        );
        assert_eq!(error.token.unwrap().lineno, 2);
    }

    #[test]
    fn an_unregistered_tag_is_reported_with_its_line() {
        let error = failure("\n{% nope %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 2: 'nope'. \
                 Did you forget to register or load this tag?"
                    .to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().contents, "nope");
    }

    #[test]
    fn an_unimplemented_builtin_says_so_rather_than_denying_the_tag_exists() {
        // The behaviour change M3 introduces. Before it, `{% for %}` fell through
        // to `invalid_block_tag` and reported Django's "Did you forget to
        // register or load this tag?" — a message that is *false* for a built-in
        // tag, since the template is not missing anything. Now it is an
        // `Unimplemented` naming the milestone that supplies it.
        let error = failure("{% for x in y %}{% endfor %}");
        assert_eq!(
            error.kind,
            ErrorKind::Unimplemented(
                "'for' is a built-in tag this engine does not implement yet (M5)".to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().contents, "for x in y");
    }

    #[test]
    fn a_variable_reports_the_m3_gap_rather_than_dropping_it() {
        let error = failure("{{ x }}");
        assert!(
            matches!(error.kind, ErrorKind::Unimplemented(_)),
            "expected Unimplemented, got {:?}",
            error.kind,
        );
        assert_eq!(error.token.unwrap().lineno, 1);
    }

    #[test]
    fn the_token_is_attached_once_and_never_replaced() {
        // The innermost-token rule at its smallest: `error()` called twice keeps
        // the first token (Review Focus item 5).
        let parser = Parser::new(Vec::new(), &NoHost);
        let outer = Token {
            token_type: TokenType::Block,
            contents: "outer".to_string(),
            lineno: 1,
            position: None,
        };
        let inner = Token {
            token_type: TokenType::Block,
            contents: "inner".to_string(),
            lineno: 2,
            position: None,
        };

        let once = parser.error(&inner, Error::syntax("boom"));
        let twice = parser.error(&outer, once);
        assert_eq!(twice.token.unwrap().contents, "inner");
    }

    #[test]
    fn skip_past_stops_only_on_a_block_token_with_the_exact_contents() {
        // A variable token whose contents happen to match must not terminate the
        // skip, or `{% comment %}{{ endcomment }}{% endcomment %}` would end one
        // token early.
        let mut parser = Parser::new(
            Lexer::new("a{{ endcomment }}b{% endcomment %}c").tokenize(),
            &NoHost,
        );
        parser.skip_past("endcomment").unwrap();
        assert_eq!(stream(&mut parser), vec!["c"]);
    }

    #[test]
    fn skip_past_reports_an_unclosed_block_through_the_command_stack() {
        let mut parser = Parser::new(Lexer::new("a").tokenize(), &NoHost);
        // `skip_past` is only ever called from inside a compile function, so the
        // stack has the frame the real parser would have pushed.
        parser.command_stack.push((
            "comment".to_string(),
            Token {
                token_type: TokenType::Block,
                contents: "comment".to_string(),
                lineno: 4,
                position: None,
            },
        ));
        let error = parser.skip_past("endcomment").unwrap_err();
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 4: 'comment'. Looking for one of: endcomment.".to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().lineno, 4);
    }

    #[test]
    fn dispatch_uses_the_raw_first_word_not_split_contents() {
        // `token.contents.split()[0]` is the *raw* split, not `split_contents`:
        // smart_split keeps quoted phrases whole, so dispatching through it
        // would name `'a b'` here rather than `'a` (Review Focus item 2). The
        // command is observable in the invalid-tag message, which quotes it.
        let error = failure("{% 'a b' %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 1: ''a'. \
                 Did you forget to register or load this tag?"
                    .to_string(),
            ),
        );
    }

    #[test]
    fn dispatch_splits_on_pythons_whitespace_table_not_rusts() {
        // Python's `str.split()` uses `str.isspace()`, which is true for
        // U+001C-U+001F; Rust's `str::split_whitespace` is not. So the first word
        // of `ab\u{1c}cd` is `ab` in Python and `ab\u{1c}cd` in Rust, and a
        // dispatch that used Rust's splitter would name the wrong tag
        // (Review Focus item 2).
        let error = failure("{% ab\u{1c}cd %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 1: 'ab'. \
                 Did you forget to register or load this tag?"
                    .to_string(),
            ),
        );
    }
}
