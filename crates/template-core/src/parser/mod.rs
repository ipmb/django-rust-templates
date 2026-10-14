//! Parsing, following `django.template.base.Parser`.
//!
//! `parse` is a direct port of Django's loop (`base.py:529-591`). The easy-to-lose details,
//! each with a test below: tags dispatch on `token.contents.split()[0]`, the **raw** first
//! word (`split_contents` is for a tag's arguments, and the two disagree on a quoted first
//! word or on U+001C-U+001F); reaching `parse_until` **prepends** the terminator and returns,
//! leaving the caller to consume it; `tokens` is reversed, so `delete_first_token` deletes
//! `tokens[-1]`; an error keeps the **innermost** token, because `error()` only sets one when
//! there is none.

pub mod desc;
pub mod error;
pub mod filter_expr;
pub mod host;
pub mod kwargs;
pub mod registry;
pub mod relative;
pub mod resolve;
pub mod smartif;
pub mod tags;
pub mod variable;

pub use desc::{
    AttrValue, FilterCall, FilterExprDesc, FilterVar, NodeDesc, NodeListDesc, NodeRef, TokenDesc,
    VariableDesc, TEXT_NODE,
};
pub use error::{Error, ErrorKind};
pub use host::{Host, NoHost};
pub use kwargs::{kwarg_re, token_kwargs, Kwargs};
pub use registry::CompileFn;

use std::collections::HashMap;

use crate::parser::filter_expr::parse_filter_expression;
use crate::pyunicode;
use crate::text::get_text_list;
use crate::token::{Token, TokenType};

/// The parser, ported from `django.template.base.Parser`.
/// The shortest suffix worth a classifier crossing. See the gate in `parse`.
///
/// The crossing and its guard cost roughly what two or three tokens cost to
/// ship, so the break-even is low; this sits comfortably above it rather than
/// at it, because a template this small cannot recover a misjudgement.
const STREAM_FREE_MIN_TOKENS: usize = 8;

pub struct Parser<'a> {
    /// Reversed, so the *front* of the stream is the *end* of the vec: `next_token` is `pop`,
    /// `prepend_token` is `push`, `delete_first_token` is `pop`. That is Django's
    /// `list(reversed(tokens))`, and why `delete_first_token` deletes `tokens[-1]`.
    tokens: Vec<Token>,
    /// `(command, token)` for every tag currently being compiled, innermost
    /// last. `unclosed_block_tag` pops it to name the tag that was left open.
    command_stack: Vec<(String, Token)>,
    /// Commands the host has already declined to compile stream-free, so the
    /// classifier is not re-asked across the seam for every repetition.
    ///
    /// **Negative answers only, and the asymmetry is the safety argument.** A
    /// command can only move from ineligible to eligible by a `{% load %}`
    /// rebinding the name (`base.py:707-709`), and a stale "no" costs a lost
    /// optimisation. A stale "yes" would be a *wrong answer* — a body-consuming
    /// compile function handed no stream — which is why eligibility is never
    /// cached and the classifier is re-asked on every hit.
    ///
    /// Measured: §8's all-streamful control paid ~3% for one crossing per host
    /// tag, which this reduces to one per distinct command per parse.
    stream_free_declined: std::collections::HashSet<String>,
    host: &'a dyn Host,
    /// `parser.origin.template_name` — the **raw** name Django's `Origin` holds, not a
    /// rendered `repr`.
    ///
    /// `{% extends %}` and `{% include %}` read it directly, and `extend_nodelist` renders a
    /// `repr` on demand through [`Parser::py_repr`], because a Python `repr` is Python's to
    /// produce (Python escapes by Unicode category, and Rust has no categories without a new
    /// dependency; guessing would put a subtly wrong string in a message the suite compares
    /// exactly).
    ///
    /// `None` is the origin a `Template` built without a loader has —
    /// `Origin(UNKNOWN_SOURCE).template_name` is `None` (A3).
    origin_template_name: Option<String>,
    /// `parser._last_cycle_node` and `parser._named_cycle_nodes`, which `{% cycle %}` sets and
    /// `{% resetcycle %}` reads by name or by default.
    ///
    /// **Indices into the per-parse reference table, not node objects**: Django holds a
    /// materialised `CycleNode`, which does not exist during the parse, so what is held is the
    /// slot the materialiser will fill — the same slot a second `{% cycle c %}` names so it
    /// resolves to the first one's node ([`NodeDesc::slot`]).
    ///
    /// `_named_cycle_nodes` holds the definition's **descriptor**, not the bare slot, because a
    /// reference carrying only a slot can be resolved only if the definition was materialised
    /// **first** — and `{% partialdef %}` materialises a body mid-parse, so a body whose
    /// `{% cycle c %}` names an outside definition would reach an empty slot with no
    /// constructor arguments (a `TypeError` Django does not raise). Echoing the definition's
    /// arguments makes the slot's filler an arbitrary one of its namers; `CycleNode`'s
    /// render-context key is one object either way.
    last_cycle_node: Option<usize>,
    named_cycle_nodes: HashMap<String, NodeDesc>,
    /// `parser.__loaded_blocks` — the names of every `{% block %}` compiled in this template,
    /// in order.
    ///
    /// Django reaches it with `try/except AttributeError` so the first `do_block` initialises it
    /// (`loader_tags.py:233-234`); an empty `Vec` is that collapse. `do_block` is its only reader
    /// and writer, which is why it is Rust's under Design B, and why `block` is not in
    /// `registry.rs`'s subtracted list: that rule subtracts tags whose compile touches
    /// parse-lifetime state.
    ///
    /// A `{% block %}` compiled by a **host** tag's nested `parser.parse` accumulates in Django's
    /// own attribute instead; with the pinned suite that is one template
    /// (`syntax_tests/i18n/test_blocktranslate.py`'s `test_with_block`, where `{% blocktranslate %}`
    /// refuses the nested block anyway). See A5: recorded, not fixed.
    loaded_blocks: Vec<String>,
}

impl<'a> Parser<'a> {
    /// `Parser.__init__(tokens, libraries=None, builtins=None, origin=None)`.
    ///
    /// `libraries`, `builtins`, `tags`, `filters` and `extra_data` are absent on purpose:
    /// `tags`/`filters` are the host's registry (a Rust copy would be a second source of truth
    /// against §5.2's re-entrancy contract, since `{% load %}` mutates it mid-parse), and
    /// `extra_data` must be a real Python `dict` holding Python objects, so a Rust `HashMap`
    /// would need converting back on every read.
    pub fn new(tokens: Vec<Token>, host: &'a dyn Host) -> Self {
        let mut tokens = tokens;
        tokens.reverse();
        Self {
            tokens,
            command_stack: Vec::new(),
            stream_free_declined: std::collections::HashSet::new(),
            host,
            origin_template_name: None,
            last_cycle_node: None,
            named_cycle_nodes: HashMap::new(),
            loaded_blocks: Vec::new(),
        }
    }

    /// Set the origin's template name, as `Parser(…, origin=…)` plus
    /// `origin.template_name` would have produced it.
    #[must_use]
    pub fn with_origin_template_name(mut self, name: String) -> Self {
        self.origin_template_name = Some(name);
        self
    }

    /// `parser.origin.template_name` (`loader_tags.py:305`).
    ///
    /// The field holds the name rather than a rendered `repr` because `{% extends %}` hands
    /// this straight to `construct_relative_path`, whose messages interpolate it with `%s` and
    /// whose recursion check compares it. Only `extend_nodelist` wants a `repr`, and it asks.
    pub fn origin_template_name(&self) -> Option<&str> {
        self.origin_template_name.as_deref()
    }

    /// The `{% block %}` names seen so far, in the order `do_block` recorded them.
    ///
    /// A method rather than a public field so a tag cannot assign to — and reset — it mid-parse.
    pub fn loaded_blocks(&self) -> &[String] {
        &self.loaded_blocks
    }

    /// `parser.__loaded_blocks.append(block_name)` (`loader_tags.py:232`).
    ///
    /// `do_block` calls it **after** the duplicate check — Django's ordering, and observable:
    /// the occurrence that raises is never recorded, so the list holds only names whose block
    /// compiled.
    pub fn register_block_name(&mut self, name: String) {
        self.loaded_blocks.push(name);
    }

    /// Reserve a reference-table slot for the node being compiled.
    ///
    /// `{% cycle %}` is the caller: it reserves once for a definition form and records the index
    /// so a later `{% cycle c %}` and a `{% resetcycle %}` name the same slot. The node is built
    /// and stored by the materialiser, not here — see [`NodeDesc::slot`].
    ///
    /// A thin forward to [`Host::alloc_slot`], because the table is the host's while the *tag* is
    /// the allocator: `Parser::host` is private so a tag cannot reach around the seam.
    pub fn alloc_slot(&mut self) -> usize {
        self.host.alloc_slot()
    }

    /// `Parser.compile_filter(token)` — `FilterExpression(token, self)` (`base.py:676`).
    ///
    /// One argument, exactly as in the pinned source: there is no `ignore_invalid` flag in
    /// 6.0.9. The descriptor is the one the `{{ … }}` arm builds, so a tag's `filter_expr`
    /// attribute and a `VariableNode`'s are the same shape on the wire.
    ///
    /// **The error comes back bare, carrying no token.** Django wraps the whole
    /// `compile_func(self, token)` call in `except Exception as e: raise self.error(token, e)`
    /// (`base.py:585`), so the token attached to a tag's filter failure is the *tag's*;
    /// attaching one here would pre-empt that and make `error()`'s innermost rule unreachable.
    /// The `{{ … }}` arm calls `self.error` itself only because it is not inside that `try`.
    pub fn compile_filter(&self, source: &str) -> Result<AttrValue, Error> {
        parse_filter_expression(source, self.host)
            .map(|expression| AttrValue::FilterExpr(Box::new(expression)))
    }

    /// One `{% if %}` operand's `FilterExpression`, by handle — a thin forward to
    /// [`Host::compile_filter`] for `alloc_slot`'s reason.
    ///
    /// **Not the same as [`Parser::compile_filter`] above**, which returns the `{{ … }}`
    /// descriptor: this returns the *object*, because that is what `smartif`'s
    /// `LiteralValue::Compiled` holds and what `TemplateLiteral.value` must be.
    pub fn compile_condition_filter(&self, token: &str) -> Result<NodeRef, Error> {
        self.host.compile_filter(token)
    }

    /// `partial_func`'s `PartialNode` construction, by handle — a thin forward to
    /// [`Host::partial_node`], for `alloc_slot`'s reason: the mapping is a live view over a dict
    /// the *host* owns.
    pub fn partial_node(&self, name: &str) -> Result<NodeRef, Error> {
        self.host.partial_node(name)
    }

    /// `partialdef_func`'s `extra_data` write and its duplicate check — a thin forward to
    /// [`Host::register_partial_def`], for the same reason: the body is materialised once, by the
    /// side that owns the materialiser, and the nodelist must be one object shared with the stored
    /// `PartialTemplate`.
    #[allow(clippy::too_many_arguments)]
    pub fn register_partial_def(
        &self,
        token: &Token,
        name: &str,
        inline: bool,
        body: &NodeListDesc,
        source_start: Option<usize>,
        source_end: Option<usize>,
    ) -> Result<NodeRef, Error> {
        self.host
            .register_partial_def(token, name, inline, body, source_start, source_end)
    }

    /// Python's `repr()` of `s`, through the host — see [`Host::repr`].
    ///
    /// A forward rather than a call to `py_repr_str`, because the shell's implementation is
    /// Python's own `repr` and `py_repr_str` is only exact on ASCII. `{% with %}` is the caller
    /// whose argument makes that matter.
    pub fn py_repr(&self, s: &str) -> String {
        self.host.repr(s)
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
    /// Django would raise `IndexError` on an empty list; that cannot happen here, because every
    /// caller reaches this after `parse(until)` returned, which only happens when the terminator
    /// was prepended. A real `assert!` rather than `debug_assert!`, which is compiled out in the
    /// build this ships in.
    pub fn delete_first_token(&mut self) {
        assert!(
            self.tokens.pop().is_some(),
            "delete_first_token with an empty token list"
        );
    }

    /// The tokens from the cursor on, in **parse order**.
    ///
    /// The internal stack is reversed (`base.py:504-505`, for `pop` from the end), so this
    /// reverses a copy. Exists for [`Host::call_tag`], which hands a host compile function the
    /// stream it may consume.
    pub fn remaining_tokens(&self) -> Vec<Token> {
        self.tokens.iter().rev().cloned().collect()
    }

    /// Replace the cursor with `tokens`, in parse order.
    ///
    /// Replacing rather than advancing by a count is what lets a host callback's `prepend_token`
    /// work without a special case: whatever the host leaves behind *is* the stream.
    pub fn replace_tokens(&mut self, tokens: Vec<Token>) {
        self.tokens = tokens.into_iter().rev().collect();
    }

    /// `Parser.skip_past(endtag)`.
    ///
    /// Loops until a **block** token whose contents *equal* `endtag` — a variable or comment
    /// token with the same contents does not terminate it — and otherwise reports an unclosed
    /// block. `{% comment %}` uses this rather than `parse`, so its body is never compiled.
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
                    // `self.compile_filter(token.contents)`, i.e. `FilterExpression(contents, self)`.
                    // The filter *names* are validated through the host where Django validates
                    // them; `VariableNode.__init__`'s parameter is named `filter_expression`
                    // (`base.py:1139`) and the materialiser constructs by keyword, so the name is
                    // contract.
                    //
                    // The error goes through `self.error` because Django wraps this call in
                    // `except TemplateSyntaxError as e: raise self.error(token, e)`
                    // (`base.py:549-551`), and a filter-check failure is one of those. `error()`
                    // sets the token only when there is none, so the innermost one still wins.
                    let expression = parse_filter_expression(&token.contents, self.host)
                        .map_err(|error| self.error(&token, error))?;
                    let node = NodeDesc::new("VariableNode").attr(
                        "filter_expression",
                        AttrValue::FilterExpr(Box::new(expression)),
                    );
                    self.extend_nodelist(&mut nodelist, node, &token)?;
                }
                TokenType::Block => {
                    // The raw first word, not `split_contents()`: `{% 'a b' %}` dispatches on
                    // `'a`, and the splitter would dispatch on `'a b'` because smart_split keeps
                    // quoted phrases whole. Django indexes `token.contents.split()[0]` and catches
                    // `IndexError` for empty `contents` — that is `[0]` on an empty list, not the
                    // `next(bits)` exhaustion `split_contents` is subject to.
                    //
                    // It is Python's `str.split`, so `pyunicode::split`, whose table is
                    // `str.isspace()` and so includes U+001C-U+001F — never `str::split_whitespace`,
                    // which would disagree on those four code points and so on which tag was found.
                    let Some(command) = pyunicode::split(&token.contents).first().copied() else {
                        return Err(self.error(
                            &token,
                            Error::syntax(format!("Empty block tag on line {}", token.lineno)),
                        ));
                    };
                    let command = command.to_string();

                    if parse_until.contains(&command.as_str()) {
                        // Put it back and return: the caller consumes it with `next_token()`.
                        // Consuming it here produces `endfor`-shaped nodes or silently drops a tag.
                        self.prepend_token(token);
                        return Ok(nodelist);
                    }

                    self.command_stack.push((command.clone(), token.clone()));

                    // The host is consulted first. Django's registry is one dict that
                    // `add_library` updates in place, so a *later* registration shadows an earlier
                    // one, and `{% load %}` runs after the engine's builtins — a custom library
                    // redefining a built-in name wins. The shell must therefore **not** register the
                    // built-ins Rust implements, or host-first would route them to Python and these
                    // compile functions would never run. `block`, `extends` and `include` are
                    // `loader_tags`' entries, merged into the same dict by `Parser.__init__`; the
                    // host compares the entry against the modules it ports, and this side only sees
                    // `true`/`false`.
                    let compiled = if self.host.has_tag(&command) {
                        // The host compiles it and may consume the rest of the stream. Both halves
                        // cross by value: the host is Python's own `Parser` and holds no borrow of
                        // this one (§5.2), which is what lets `{% load %}` and a
                        // `simple_block_tag`'s nested parse re-enter at all.
                        // Below this, there is nothing worth classifying: the saving
                        // scales with the suffix that would have crossed, while the
                        // classifier crossing costs the same whatever the length.
                        // Measured against main, a one-host template of a few tokens
                        // was +4.9% slower before this gate — a real cost, outside the
                        // noise, on a template with nothing to gain. Taking the
                        // ordinary path without asking is always correct; the
                        // classifier is an optimisation gate, never a correctness one.
                        let stream_free = self.tokens.len() >= STREAM_FREE_MIN_TOKENS
                            && !self.stream_free_declined.contains(&command)
                            && {
                                let answer = self.host.tag_is_stream_free(&command);
                                if !answer {
                                    self.stream_free_declined.insert(command.clone());
                                }
                                answer
                            };
                        if stream_free {
                            // The whole of the optimisation: no `remaining_tokens()`
                            // clone, no inbound `PyList`, no Python `Token` per token,
                            // no outbound wire tuples, and no `replace_tokens` — the
                            // cursor is simply never touched. Skipping both halves is
                            // value-preserving because `remaining_tokens` reverses a
                            // clone and `replace_tokens` reverses it back.
                            //
                            // **Not `?`**, for the reason the ordinary arm below is not
                            // `?` either: the result must reach the shared
                            // `map_err(|error| self.error(&token, error))` so a boundary
                            // error that carries no token still gets one.
                            self.host.call_tag_stream_free(&command, &token)
                        } else {
                            let remaining = self.remaining_tokens();
                            // Not `?`: a host error must reach the `map_err` below like any other
                            // compile error, because Django attaches the token in `Parser.parse`'s
                            // `except` and the host's exception carries none. `?` would return before
                            // `self.error(&token, …)` ran and the message would lose its line.
                            match self.host.call_tag(&command, &token, &remaining) {
                                Ok((desc, left)) => {
                                    self.replace_tokens(left);
                                    Ok(desc)
                                }
                                Err(error) => Err(error),
                            }
                        }
                    } else if let Some(compile) = registry::builtin_tag(&command) {
                        compile(self, &token)
                    } else if let Some(compile) = loaded_library_tag(self.host, &command) {
                        // A **library** tag — `{% load static %}` then `{% static %}`. The host
                        // said `false` to `has_tag` because the registered function is still
                        // Django's own, so the remaining question is whether Rust has a body for
                        // it. Both questions are inside `loaded_library_tag`, whose doc says why the
                        // conjunction is the shape.
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

    /// `Parser.error(token, e)` — **returns** the error with the token attached, and only if it
    /// does not already carry one.
    ///
    /// Rust has no `hasattr`, so the "already carries one" test is `token.is_none()`. That is
    /// exactly equivalent: Django's exceptions never define `token` as a class attribute
    /// (`TemplateSyntaxError.__init__` sets only `self.msg`), so before the first `error()` call
    /// `hasattr` is false and afterwards true. Django's `if not isinstance(e, Exception)` branch
    /// has no counterpart: a Rust compile function can only *return* an `Error`, so "a tag raised
    /// a string" is unrepresentable here.
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
    /// Reports the tag **left open** — from `command_stack`, not the terminator being looked for
    /// — and the list it was looking for, joined with `", "` rather than `get_text_list`'s
    /// `" or "`.
    pub fn unclosed_block_tag(&mut self, parse_until: &[&str]) -> Error {
        let Some((command, token)) = self.command_stack.pop() else {
            // Unreachable from `parse` and `skip_past`: both are only reached from inside a tag's
            // compile function, and `parse` pushes the frame before calling it. Django would raise
            // IndexError here.
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
    /// This — not a node's `__init__` — is where a node acquires its position, and it runs
    /// **after** the node is constructed, so a node whose constructor reads its own subtree
    /// (`ExtendsNode.__init__` builds `self.blocks` via `get_nodes_by_type`) sees children and
    /// no token.
    ///
    /// Returns `Result` because Django's must-be-first and command-stack rules make an append
    /// able to fail.
    pub fn extend_nodelist(
        &self,
        nodelist: &mut NodeListDesc,
        mut node: NodeDesc,
        token: &Token,
    ) -> Result<(), Error> {
        if node.must_be_first && nodelist.contains_nontext {
            // Django's test is **truthiness**, not `is None` (`base.py:602-606`): an empty name
            // picks "the template" as well as `None`. Rust holds `Some("")` for the empty one, so
            // the arm is explicit — a `match` on `Option` alone would answer `''`.
            let origin = match self.origin_template_name() {
                Some(name) if !name.is_empty() => self.py_repr(name),
                _ => "the template".to_string(),
            };
            return Err(self.error(
                token,
                Error::syntax(format!(
                    "{{% {} %}} must be the first tag in {}.",
                    token.contents, origin,
                )),
            ));
        }
        // Django asks `isinstance(node, TextNode)`; the descriptor has the class *name*, and no
        // class subclasses TextNode (see `TEXT_NODE`), so this is the same test.
        if node.class_name != TEXT_NODE {
            nodelist.contains_nontext = true;
        }
        node.token = Some(TokenDesc::of(token));
        nodelist.nodes.push(node);
        Ok(())
    }
}

/// The compile function for `command` **as a loaded library tag**, if it is one.
///
/// Two questions, in this order, and the conjunction is why this exists: the host answers
/// whether the template loaded a library providing `command` (`ShellHost.library_tag_loaded`
/// is `command in parser.tags`, which `{% load %}` mutates), and the registry answers whether
/// Rust has a body for it ([`registry`]'s `LIBRARY_REGISTRY`). Either alone routes wrongly: the
/// host's alone compiles any loaded name the registry happens to spell too, whether or not Rust
/// has a body; the registry's alone compiles `{% static %}` in a template that never wrote
/// `{% load static %}`, which Django refuses.
///
/// The conjunction's two distinguishing inputs are rows in this module's tests, one per
/// direction.
fn loaded_library_tag(host: &dyn Host, command: &str) -> Option<CompileFn> {
    if !host.library_tag_loaded(command) {
        return None;
    }
    registry::library_tag(command)
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

    /// Parse `source` which must succeed.
    fn parse_ok(source: &str) -> NodeListDesc {
        parse(source).unwrap_or_else(|error| panic!("{source:?} failed: {error:?}"))
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
        // `Parser::new` reverses and `next_token` pops, so the two reversals must cancel; a
        // single reversal shows up here as the *last* token first.
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
        // `del self.tokens[-1]` on a reversed vec. The tempting `remove(0)`/`drain(..1)` on a
        // forward vec deletes the wrong end, and the symptom is a block tag's *second* parse,
        // i.e. the `{% empty %}` branch of `{% for %}`.
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

    /// Parse one tag under a host that reports exactly `loaded` as loaded — the shape
    /// `ShellHost`'s `library_tag_loaded` has, and the only shape under which
    /// [`loaded_library_tag`]'s two questions can disagree.
    fn parse_with_loaded(loaded: Vec<&'static str>, source: &str) -> Result<NodeListDesc, Error> {
        let host = crate::parser::tags::support::LoadedLibrariesHost(loaded);
        Parser::new(Lexer::new(source).tokenize(), &host).parse(&[])
    }

    #[test]
    fn a_library_tag_that_was_never_loaded_is_refused() {
        // One direction of the disagreement, on a production path: `{% static %}` with no
        // `{% load static %}`, which Django refuses. The registry has a body for the name and the
        // host says the library was not loaded — the input that tells the conjunction from the
        // registry lookup alone, which would compile a `StaticNode` Django rejects.
        let error = parse_with_loaded(vec![], r#"{% static "a/b.css" %}"#)
            .expect_err("the library was never loaded");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 1: 'static'. \
                 Did you forget to register or load this tag?"
                    .to_string(),
            ),
        );
    }

    #[test]
    fn the_same_tag_compiles_once_the_host_says_the_library_was_loaded() {
        // The row above is only a row if this one passes: same source, one answer different, and
        // the outcome flips. Together they make the conjunction observable rather than asserted.
        let nodelist = parse_with_loaded(vec!["static"], r#"{% static "a/b.css" %}"#)
            .expect("loaded, and Rust has a body");
        assert_eq!(nodelist.nodes[0].class_name, "StaticNode");
    }

    #[test]
    fn a_loaded_library_tag_rust_has_no_body_for_is_refused_rather_than_panicking() {
        // The other direction, and the input no production path reaches: the real instance is
        // `trans` after `{% load i18n %}`, where the real `ShellHost` answers `has_tag` `true`
        // first, so [`loaded_library_tag`] is never consulted. The fixture is a **fabricated** name
        // rather than `trans` so that porting `trans` cannot turn this row into a compile where it
        // expects a refusal; the host's set is a literal, so it can claim the name was loaded.
        //
        // It holds that the answer, if reached, is the same refusal an unregistered tag gets — not
        // a panic, and not an `Unimplemented`, which a `library_tag_loaded`-alone gate would have to
        // invent for a name it claimed and no body answered.
        let error = parse_with_loaded(vec!["unregistered_tag"], "{% unregistered_tag %}")
            .expect_err("Rust has no body for it");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 1: 'unregistered_tag'. \
                 Did you forget to register or load this tag?"
                    .to_string(),
            ),
        );
    }

    #[test]
    fn a_variable_now_parses_and_the_m3_gap_is_closed() {
        // No `Unimplemented` reaches a `{{ … }}` any more, so a harness scoring our agreement with
        // Django never sees a variable bucketed as "not built yet".
        let nodelist = parse_ok("{{ x }}");
        assert_eq!(nodelist.nodes[0].class_name, "VariableNode");
        // `VariableNode.__init__(self, filter_expression)` — the parameter name is the
        // materialiser's keyword, so it is pinned here rather than left to the FFI test.
        assert!(matches!(
            nodelist.nodes[0].attrs.as_slice(),
            [(name, AttrValue::FilterExpr(_))] if name == "filter_expression",
        ));
    }

    #[test]
    fn a_variable_that_django_rejects_is_rejected_here_too() {
        // The error travels out of `Parser::parse` rather than only out of
        // `parse_filter_expression`, and it keeps the token — `error()` attaches it once,
        // innermost first.
        let error = failure("{{ a+b }}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Invalid character ('+') in variable name: 'a+b'".to_string()),
        );
        assert_eq!(error.token.unwrap().contents, "a+b");
    }

    #[test]
    fn the_token_is_attached_once_and_never_replaced() {
        // The innermost-token rule at its smallest: `error()` called twice keeps the first token.
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
        // A variable token whose contents happen to match must not terminate the skip, or
        // `{% comment %}{{ endcomment }}{% endcomment %}` would end one token early.
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
        // `skip_past` is only ever called from inside a compile function, so the stack has the
        // frame the real parser would have pushed.
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
        // `token.contents.split()[0]` is the *raw* split, not `split_contents`: smart_split keeps
        // quoted phrases whole, so dispatching through it would name `'a b'` here rather than `'a`.
        // The command is observable in the invalid-tag message, which quotes it.
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
        // Python's `str.split()` uses `str.isspace()`, which is true for U+001C-U+001F; Rust's
        // `str::split_whitespace` is not, so the first word of `ab\u{1c}cd` is `ab` in Python and
        // `ab\u{1c}cd` in Rust — a Rust splitter would name the wrong tag.
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
