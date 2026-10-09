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
    /// `parser.origin.template_name` — the **raw** name Django's `Origin` holds,
    /// not a rendered `repr`.
    ///
    /// `{% extends %}` and `{% include %}` read it directly (that is the field's
    /// shape after M7 Task 2), and `extend_nodelist`'s must-be-first message
    /// renders a `repr` on demand through [`Parser::py_repr`] because a Python
    /// `repr` is Python's to produce — the same rule §5.2 already records for
    /// `LookupError::Missing::current_repr`. Python's `repr` escapes by Unicode
    /// category and Rust has no categories without a new dependency; guessing
    /// would put a subtly wrong string in a message the suite compares exactly.
    ///
    /// `None` stands for the origin a `Template` built without a loader has —
    /// `Origin(UNKNOWN_SOURCE).template_name` is `None` (A3) — and **not** for
    /// "no origin object": the Python side always passes a real `Origin`, and
    /// what reaches Rust is the attribute read from it.
    origin_template_name: Option<String>,
    /// `parser._last_cycle_node` and `parser._named_cycle_nodes`, which
    /// `{% cycle %}` sets and `{% resetcycle %}` reads by name or by default.
    ///
    /// **Indices into the per-parse reference table, not node objects.** The
    /// object Django stores here is a materialised `CycleNode`, which does not
    /// exist during the parse, so what is held is the slot the materialiser will
    /// fill — the same slot a second `{% cycle c %}` names so that it resolves
    /// to the first one's node ([`NodeDesc::slot`]).
    ///
    /// M5 only ever reads them and finds them empty until `{% cycle %}` lands;
    /// the fields exist now so that `{% resetcycle %}`'s two error paths are the
    /// real ones rather than a hard-coded "no cycles".
    last_cycle_node: Option<usize>,
    named_cycle_nodes: HashMap<String, usize>,
    /// `parser.__loaded_blocks` — the names of every `{% block %}` compiled in
    /// this template, in order.
    ///
    /// Django reaches it with `try/except AttributeError` so that the first
    /// `do_block` initialises it (`loader_tags.py:233-234`); an empty `Vec` is
    /// that collapse. Its only reader and only writer are `do_block`, which is
    /// why it is Rust's under Design B — and it is the field that moved `block`
    /// out of `registry.rs`'s subtracted list, because the rule there subtracts
    /// tags whose compile touches parse-lifetime state, and this is that state
    /// living somewhere a compile function can reach.
    ///
    /// A `{% block %}` compiled by a **host** tag's nested `parser.parse`
    /// accumulates in Django's own attribute instead; with the pinned suite that
    /// is one template (`syntax_tests/i18n/test_blocktranslate.py`'s
    /// `test_with_block`, where `{% blocktranslate %}` refuses the nested block
    /// anyway). See A5: recorded, not fixed.
    loaded_blocks: Vec<String>,
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
    /// The read half, and the reason the field holds the name rather than a
    /// rendered `repr`: `{% extends %}` hands this straight to
    /// `construct_relative_path`, whose messages interpolate it with `%s` and
    /// whose recursion check compares it — none of which is a `repr`. Only
    /// `extend_nodelist` wants one, and it asks for one.
    pub fn origin_template_name(&self) -> Option<&str> {
        self.origin_template_name.as_deref()
    }

    /// The `{% block %}` names seen so far, in the order `do_block` recorded them.
    ///
    /// A method rather than a public field for `alloc_slot`'s reason: the field
    /// belongs to the parse, and a tag that could assign to it directly could
    /// also reset it mid-parse.
    pub fn loaded_blocks(&self) -> &[String] {
        &self.loaded_blocks
    }

    /// `parser.__loaded_blocks.append(block_name)` (`loader_tags.py:232`).
    ///
    /// The write half. `do_block` calls it **after** the duplicate check, which
    /// is Django's ordering and is observable: the occurrence that raises is
    /// never recorded, so the list holds only names whose block compiled.
    pub fn register_block_name(&mut self, name: String) {
        self.loaded_blocks.push(name);
    }

    /// Reserve a reference-table slot for the node being compiled.
    ///
    /// `{% cycle %}` is the caller: it reserves once for a definition form and
    /// records the index in `named_cycle_nodes`/`last_cycle_node`, so that a
    /// later `{% cycle c %}` and a `{% resetcycle %}` name the same slot. The
    /// node itself is built and stored by the materialiser, not here — see
    /// [`NodeDesc::slot`].
    ///
    /// A thin forward to [`Host::alloc_slot`], and it exists because the table is
    /// the host's while the *tag* is the allocator: the tag holds a `Parser`, and
    /// `Parser::host` is private so that a tag cannot reach around the seam.
    pub fn alloc_slot(&mut self) -> usize {
        self.host.alloc_slot()
    }

    /// `Parser.compile_filter(token)` — `FilterExpression(token, self)`
    /// (`base.py:676`).
    ///
    /// One argument, exactly as in the pinned source: there is no
    /// `ignore_invalid` flag in 6.0.9, so every call site passes a bare string.
    /// The descriptor is the one the `{{ … }}` arm builds, so a tag's
    /// `filter_expr` attribute and a `VariableNode`'s attribute are the same
    /// shape on the wire.
    ///
    /// **The error comes back bare, carrying no token.** Django wraps the whole
    /// `compile_func(self, token)` call in `except Exception as e: raise
    /// self.error(token, e)` (`base.py:585`), so the token attached to a
    /// tag's filter failure is the *tag's* — attaching one here would pre-empt
    /// that and make `error()`'s innermost rule unreachable. The `{{ … }}` arm
    /// has to call `self.error` itself only because it is not inside that `try`.
    pub fn compile_filter(&self, source: &str) -> Result<AttrValue, Error> {
        parse_filter_expression(source, self.host)
            .map(|expression| AttrValue::FilterExpr(Box::new(expression)))
    }

    /// One `{% if %}` operand's `FilterExpression`, by handle — a thin forward
    /// to [`Host::compile_filter`], for `alloc_slot`'s reason: the seam is the
    /// host's while the *tag* is the caller, and `Parser::host` is private so a
    /// tag cannot reach around it.
    ///
    /// **Not the same thing as [`Parser::compile_filter`] above**, which returns
    /// the `{{ … }}` descriptor. This one returns the *object*, because that is
    /// what `smartif`'s `LiteralValue::Compiled` holds and what
    /// `TemplateLiteral.value` must be.
    pub fn compile_condition_filter(&self, token: &str) -> Result<NodeRef, Error> {
        self.host.compile_filter(token)
    }

    /// Python's `repr()` of `s`, through the host — see [`Host::repr`].
    ///
    /// A forward rather than a call to `py_repr_str`, because the shell's
    /// implementation is Python's own `repr` and `py_repr_str` is only exact on
    /// ASCII. `{% with %}` is the caller whose argument makes that matter.
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

    /// The tokens from the cursor on, in **parse order**.
    ///
    /// The internal stack is reversed (`base.py:504-505`, for `pop` from the
    /// end), so this reverses a copy. It exists for [`Host::call_tag`], which
    /// hands a host compile function the stream it may consume.
    pub fn remaining_tokens(&self) -> Vec<Token> {
        self.tokens.iter().rev().cloned().collect()
    }

    /// Replace the cursor with `tokens`, in parse order.
    ///
    /// Replacing rather than advancing by a count is what makes a host
    /// callback's `prepend_token` work without a special case: whatever the host
    /// leaves behind *is* the stream, and the host is Django's own `Parser`,
    /// whose stack semantics the reversal already mirrors.
    pub fn replace_tokens(&mut self, tokens: Vec<Token>) {
        self.tokens = tokens.into_iter().rev().collect();
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
                    // `self.compile_filter(token.contents)`, which is
                    // `FilterExpression(contents, self)`. The filter *names* are
                    // validated through the host at the point in
                    // `FilterExpression.__init__`'s loop where Django validates
                    // them; `VariableNode.__init__`'s parameter is named
                    // `filter_expression` (`base.py:1139`) and the materialiser
                    // constructs by keyword, so the name is contract.
                    //
                    // The error goes through `self.error` because Django wraps
                    // this call in `except TemplateSyntaxError as e: raise
                    // self.error(token, e)` (`base.py:549-551`), and a
                    // filter-check failure is one of those — it is a Python
                    // exception from `find_filter`. `error()` sets the token only
                    // when there is none, so the innermost one still wins.
                    let expression = parse_filter_expression(&token.contents, self.host)
                        .map_err(|error| self.error(&token, error))?;
                    let node = NodeDesc::new("VariableNode").attr(
                        "filter_expression",
                        AttrValue::FilterExpr(Box::new(expression)),
                    );
                    self.extend_nodelist(&mut nodelist, node, &token)?;
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
                    //
                    // Since M7 that "built-in" is not always `defaulttags`':
                    // `block`, `extends` and `include` are `loader_tags`', merged
                    // into the same dict by `Parser.__init__` rather than
                    // registered there. The host's decision is the same either
                    // way — it compares the entry against the module table it
                    // ports, from both modules — and the only thing this side
                    // sees is `true`/`false`. What the *previous* shape got
                    // wrong is worth naming here because it lived in Python: an
                    // exception list for those three names was consulted before
                    // the comparison, so a `{% load %}`ed library redefining
                    // `block` lost. Nothing on this side changes for that; the
                    // host is still asked first, and still has the last word.
                    let compiled = if self.host.has_tag(&command) {
                        // The host compiles it, and it may consume the rest of
                        // the stream. Both halves cross by value: the host is
                        // Python's own `Parser` and holds no borrow of this one
                        // (§5.2), which is what lets `{% load %}` and a
                        // `simple_block_tag`'s nested parse re-enter at all.
                        let remaining = self.remaining_tokens();
                        // Not `?`: a host error must reach the `map_err` below
                        // like any other compile error, because Django attaches
                        // the token in `Parser.parse`'s `except` and the host's
                        // exception does not carry one. `?` here would return
                        // before `self.error(&token, …)` ran and the message
                        // would lose its line and position.
                        match self.host.call_tag(&command, &token, &remaining) {
                            Ok((desc, left)) => {
                                self.replace_tokens(left);
                                Ok(desc)
                            }
                            Err(error) => Err(error),
                        }
                    } else if let Some(compile) = registry::builtin_tag(&command) {
                        compile(self, &token)
                    } else if let Some(compile) = loaded_library_tag(self.host, &command) {
                        // A **library** tag — `{% load static %}` then
                        // `{% static %}`. The host said `false` to `has_tag`
                        // because the registered function is still Django's own
                        // and the name is one Rust compiles, so the remaining
                        // question is whether Rust has a body for it, and that
                        // one is the registry's (M12). Both questions are inside
                        // `loaded_library_tag`, whose doc says why the
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
            // Django's test is **truthiness**, not `is None` (`base.py:602-606`):
            // `if self.origin.template_name:` picks "the template" for an empty
            // name as well as for `None`. Rust holds `Some("")` for the empty
            // one, so the arm is explicit — the two encodings differ on exactly
            // that input, and a `match` on `Option` alone would answer `''`.
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

/// The compile function for `command` **as a loaded library tag**, if it is one.
///
/// Two questions, asked in this order, and the conjunction is this function's
/// reason to exist rather than a convenience:
///
/// * the host answers whether the template loaded a library providing
///   `command` — `ShellHost.library_tag_loaded` is `command in parser.tags`,
///   which `{% load %}` mutates, and it is `true` for **all 18**
///   `django.templatetags` names, 0 of which are Python's as of M12 Task 6b;
/// * the registry answers whether Rust has a body for it ([`registry`]'s
///   `LIBRARY_REGISTRY` — eighteen names as of M12 Task 6b).
///
/// Either question alone routes wrongly. The host's alone compiles any loaded
/// name whose spelling the registry also carries, whether or not Rust has a
/// body for it — the shape `blocktranslate` had until M12 Task 6b, when a
/// loaded `{% blocktranslate %}` had to reach Django's body and the registry
/// had no arm for it; the
/// registry's alone compiles `{% static %}` in a template that never wrote
/// `{% load static %}`, which Django refuses.
///
/// **This replaced a `match` with a third arm, and deleting that arm is a
/// no-op by construction.** The arm
/// (`library_tag_loaded(name) && library_tag(name).is_none()` →
/// `invalid_block_tag`) returned *the same call with the same three arguments*
/// as the `else` directly below it: `git show b83ec01^` shows
/// `return Err(self.invalid_block_tag(&token, &command, parse_until))` twice,
/// differing only by a trailing `;`. Two branches no input can distinguish are
/// not defence in depth; they are a claim of coverage, and removing one cannot
/// change what any template does. **Identity is the load-bearing reason here,
/// and unlike the two below it cannot expire.**
///
/// The *measurements* that also bear on the arm are supporting context, and
/// each is a claim about today's two tables rather than about the deletion:
///
/// * replacing the arm's body with `unreachable!()` left the whole gate green —
///   `cargo test --workspace`, the Python unit suite (408 rows then; this
///   batch's own two rows are counted after it), the 48-case differential
///   harness, and Django's 1547;
/// * the routing that makes it unreachable in production: the shell's
///   `_rust_owned` takes its library half from `library_tag_names()`, the
///   *same* table [`registry::library_tag`] reads, so a loaded name Rust owns
///   always has a body here, while a loaded name Rust does not own makes
///   `has_tag` return `true` in its second clause
///   (`command not in self._rust_owned`,
///   `python/django_rust_templates/host.py`) at the top of the chain, so this
///   line is never consulted for it. The reviewer confirmed the same routing
///   from outside: a third-party library registering `if`/`static` sends all
///   four cases (with and without `OPTIONS["builtins"]`) to the host, and
///   compiling all **30** names in `_rust_owned` produces **zero**
///   `Invalid block tag` refusals.
///
/// A later reader who falsifies either table should still not conclude the
/// deletion was unsafe; identity is why. The two inputs that *do* distinguish
/// the conjunction are rows in this module's tests, one per direction.
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

    /// Parse one tag under a host that reports exactly `loaded` as loaded —
    /// the shape `ShellHost`'s `library_tag_loaded` has, `command in
    /// parser.tags`, and the only shape under which the two questions
    /// [`loaded_library_tag`] conjoins can disagree.
    fn parse_with_loaded(loaded: Vec<&'static str>, source: &str) -> Result<NodeListDesc, Error> {
        let host = crate::parser::tags::support::LoadedLibrariesHost(loaded);
        Parser::new(Lexer::new(source).tokenize(), &host).parse(&[])
    }

    #[test]
    fn a_library_tag_that_was_never_loaded_is_refused() {
        // One direction of the disagreement, on a production path: `{% static %}`
        // with no `{% load static %}`, which is the template Django refuses
        // (`Invalid block tag … Did you forget to register or load this tag?`,
        // `static.py`'s tag being unreachable without the load). The registry
        // has a body for the name and the host says the library was not loaded —
        // the input that tells the conjunction from the registry lookup alone,
        // which would compile a `StaticNode` for a template stock Django rejects.
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
        // The row above is only a row if this one passes: same source, one
        // answer different, and the outcome flips. Together they are what makes
        // the conjunction observable instead of asserted — and this is also the
        // production path M12 Task 3 opened, `{% load static %}{% static %}`.
        let nodelist = parse_with_loaded(vec!["static"], r#"{% static "a/b.css" %}"#)
            .expect("loaded, and Rust has a body");
        assert_eq!(nodelist.nodes[0].class_name, "StaticNode");
    }

    #[test]
    fn a_loaded_library_tag_rust_has_no_body_for_is_refused_rather_than_panicking() {
        // The other direction, and the input no production path reaches. The
        // real instance is `trans` after `{% load i18n %}`: Django compiles it
        // (`i18n.py`'s `do_trans`) and the real `ShellHost` answers `has_tag`
        // first — `true`, by its `command not in self._rust_owned` clause — so
        // [`loaded_library_tag`] is never consulted for it. The fixture is a
        // **fabricated** name rather than `trans` because `trans` stops being an
        // instance of this case the moment M12 Task 6 ports it: the row would
        // then read a compile where it expects a refusal. `unregistered_tag` is
        // a name no registry holds and no milestone can overtake, and the host's
        // set is a literal, so the host can claim it was loaded.
        //
        // This row exists because the dispatch's *previous* shape had a branch
        // for exactly this case and nothing could reach it — see
        // [`loaded_library_tag`] for the measurement. What it holds is that the
        // answer, if reached, is the same refusal an unregistered tag gets: not
        // a panic, and not an `Unimplemented` — which is what a
        // `library_tag_loaded`-alone gate would have to invent for a name it
        // claimed and no body answered.
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
    fn an_unimplemented_builtin_says_so_rather_than_denying_the_tag_exists() {
        // The behaviour change M3 introduces. Before it, an unimplemented
        // built-in fell through to `invalid_block_tag` and reported Django's
        // "Did you forget to register or load this tag?" — a message that is
        // *false* for a built-in tag, since the template is not missing
        // anything. Now it is an `Unimplemented` naming the milestone that
        // supplies it.
        //
        // The example is `{% partialdef %}`, one of the two stubs M13 will
        // replace, and it moved *to* `partialdef` in M6: this test is one of the
        // few that names a *specific* unimplemented tag, so each milestone's
        // tasks move it as they land. It named `{% if %}` until M5 implemented
        // that tag, then `{% load %}` — which M6 stopped being a stub at all,
        // because it left the table for the host. `partialdef` is the first
        // still-stubbed entry now and is M13's, so this line outlives M6.
        let error = failure("{% partialdef x %}");
        assert_eq!(
            error.kind,
            ErrorKind::Unimplemented(
                "'partialdef' is a built-in tag this engine does not implement yet (M13)"
                    .to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().contents, "partialdef x");
    }

    #[test]
    fn a_variable_now_parses_and_the_m3_gap_is_closed() {
        // M2's arm returned `Unimplemented` here, which the shell turned into a
        // fallback to Django's own parse. This is the test that had to change
        // when it stopped: no `Unimplemented` reaches a `{{ … }}` any more, so a
        // harness scoring our agreement with Django never sees a variable
        // bucketed as "not built yet".
        let nodelist = parse_ok("{{ x }}");
        assert_eq!(nodelist.nodes[0].class_name, "VariableNode");
        // `VariableNode.__init__(self, filter_expression)` — the parameter name
        // is the materialiser's keyword, so it is pinned here rather than left to
        // the FFI test.
        assert!(matches!(
            nodelist.nodes[0].attrs.as_slice(),
            [(name, AttrValue::FilterExpr(_))] if name == "filter_expression",
        ));
    }

    #[test]
    fn a_variable_that_django_rejects_is_rejected_here_too() {
        // The error travels out of `Parser::parse` rather than only out of
        // `parse_filter_expression`, and it keeps the token — `error()` attaches
        // it once, innermost first.
        let error = failure("{{ a+b }}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Invalid character ('+') in variable name: 'a+b'".to_string()),
        );
        assert_eq!(error.token.unwrap().contents, "a+b");
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
