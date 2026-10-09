# M2 — Rust parser and node AST: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Rust parser that turns an M1 token stream into a node tree,
expose it as `_native.parse`, and materialise that tree in Python as real
`Node`/`NodeList` objects.

**Architecture:** `template-core` owns the parser loop, tag dispatch and node
AST, and returns a **node descriptor** — a plain-data tree naming each node's
class and attributes. The Python shell materialises descriptors into genuine
`Node` subclasses so that `get_nodes_by_type`, `isinstance` and `repr` behave as
Django's do (§5.5, §10.4). Rust never dereferences a Python node.

**Tech Stack:** Rust (edition 2021, `regex`, no Python dependency in
`template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`
— read §5.2 (the seam), §5.5 (the shadow tree), §6 (ownership, incl. the
`Parser` contract block), §10.3, §10.4. This plan argues from the spec; where
they disagree the spec wins and the plan is wrong.

## Global Constraints

Copied verbatim from the spec; every task's requirements implicitly include them.

- **Django 6.0.9**, pinned at `.django/`. Never modified. It is the
  specification, not a dependency.
- **`cargo test` does not run clippy.** `scripts/check.sh` gates on
  `cargo clippy --workspace --all-targets -- -D warnings`. Every task runs
  clippy explicitly before committing. Two lints have already bitten this
  project's plan-supplied code: `clippy::module_inception` and
  `clippy::type_complexity`. Name tuples with a `type` alias before writing
  them; never nest a module `x/x.rs`.
- **No indented non-Rust code in doc comments.** rustdoc compiles indented
  blocks as doctests and `cargo test` will run them; this already broke a build
  once. Quote code inline or fence it with ```` ```text ````.
- **`source scripts/env.sh` before any cargo or venv command.** `cargo` is not
  on `PATH` otherwise.
- **`maturin develop` after every Rust change** or the Python tests silently
  exercise a stale `.so`.
- **Python is never a dependency of `template-core`.** If the engine needs
  something from Python it goes behind a trait implemented in `template-py`
  (§5.2).
- **Django's Python source is not vendored.** Error strings, positions and reprs
  are ported verbatim; algorithms are written against Django as a spec (§6).
- **A change is not committed until its review round returns APPROVE or all
  blocker/major findings are resolved** (§11).

## Scope of M2 — what "every built-in tag" can mean here

§10.3's M2 row says "Parse tree descriptor for every built-in tag; syntax-error
parity". Read literally that is not reachable at M2, and the reason is
structural rather than a matter of effort: **14 of the 27 built-in tags reach
`parser.compile_filter(...)` at compile time**, and `FilterExpression`/`Variable`
are M3's (`smartif` too, which `{% if %}` needs). A tag's compile function cannot
be written before the thing it compiles.

Both numbers are counted, not recalled, and the counting is worth stating
because the obvious method gets it wrong. There are **27** built-in tags: 24 in
`defaulttags.py` plus 3 in `loader_tags.py` (`django.template.defaultfilters`
registers no tags). The 14 are `cycle`, `extends`, `filter`, `firstof`, `for`,
`if`, `ifchanged`, `include`, `lorem`, `querystring`, `regroup`, `url`,
`widthratio`, `with`.
Two of those contain no `compile_filter` call of their own — `{% with %}`
reaches it through `token_kwargs`, and `{% if %}` through
`TemplateIfParser.create_var` — so a per-function scan of the tag functions
under-reports by exactly those two, and a script that misses plain-name calls
(`token_kwargs(...)`, `TemplateIfParser(...)`) under-reports further. Counting
by reading the registry is the only reliable way.

So M2 delivers:

- the parser machinery itself — `Parser`, `NodeList`, `Node`, `extend_nodelist`,
  the error protocol, `command_stack`, `extra_data`, and tag dispatch;
- the descriptor format and the materialisation that turns it into real Python
  nodes;
- `_native.parse`;
- the compile functions for the built-in tags that need **no** filter or
  expression machinery.

**M2's tag set is nine tags**: `autoescape`, `comment`, `csrf_token`, `debug`,
`now`, `resetcycle`, `spaceless`, `templatetag`, `verbatim`.

**First the count, stated so it can be checked rather than trusted.** The metric
is the *registry*, not decorated functions, because the registry is what a
template can actually invoke:

| Count | Meaning |
|---|---|
| **27** | registry entries — `defaulttags.register.tags` (24) + `loader_tags.register.tags` (3) |
| **26** | functions carrying an `@register.tag` decorator — 23 + 3 |

They differ by exactly one name: **`querystring`**
(`@register.simple_tag(name="querystring", takes_context=True)`,
`defaulttags.py:1286`), which is in the registry but is not `tag`-decorated. An
AST walk for `@register.tag` therefore under-reports by one — which is how an
earlier draft reached 26, and how a review of that draft "confirmed" it. Both
counts were taken correctly; the metric was never named. 27 is the one this plan
needs.

**The arithmetic is 27 = 14 + 13, and 13 = 4 + 9:**

- **14** reach `parser.compile_filter`: 11 directly (`cycle`, `extends`,
  `filter`, `firstof`, `for`, `ifchanged`, `include`, `lorem`, `regroup`, `url`,
  `widthratio`), plus `with` through `token_kwargs`, `if` through
  `TemplateIfParser`, and `querystring` through `simple_tag`'s generated compile
  function, which hands `parser.compile_filter` to `parse_bits`
  (`library.py:454`).
- **13** touch no `FilterExpression` at all — the four deferred below plus M2's
  nine.

The other eighteen arrive with their dependencies, and the assignment is not
arbitrary — each waits on something the spec schedules elsewhere:

| Tags | Wait for |
|---|---|
| `filter`, `firstof`, `cycle`, `regroup`, `url`, `widthratio`, `with`, `lorem`, `querystring` | M3 (`FilterExpression`) |
| `if`, `ifchanged`, `for` | M3 (`smartif`, `FilterExpression`) |
| `load` | M6 (`Library`, `import_library`) |
| `block`, `extends`, `include` | M7 — and these are **Python-owned** per §6, so they are not Rust compile functions at all; the shell materialises them from Rust's compiled *structure* |
| `partialdef`, `partial` | M13 (`PartialTemplate`) |

The §10.3 M2 row is amended by this plan to say so. This is a *narrowing of the
row to what it can mean*, not a reduction in the milestone's intent: the
descriptor must be shown general enough for all 27, which is why Task 3 pins a
descriptor shape for a filter-bearing tag using a hand-written stand-in node,
before M3 supplies the real `FilterExpression`.

## Design decision 1 — the descriptor format

This is M2's highest-risk decision, because everything from M3 to M15 crosses it
and a wrong shape is expensive to change later. It is stated here so the
reviewer can reject it before any code exists.

Rust returns a **nested plain-data tree**, one variant per attribute kind:

```rust
pub struct NodeDesc {
    /// The Python class to materialise, e.g. "TextNode", "IfNode".
    pub class_name: String,
    pub attrs: Vec<(String, AttrValue)>,
    /// Child nodelists in the order the class's __init__ takes them.
    pub nodelists: Vec<(String, NodeListDesc)>,
    pub token: Option<TokenDesc>,
    /// Read by `extend_nodelist` to enforce the `{% extends %}`-must-be-first
    /// rule. It is a *class* attribute in Django, so it is constant per class
    /// and cannot be read through a descriptor-only seam — the materialiser has
    /// the class, but `extend_nodelist` runs in Rust at a point where Rust is
    /// deciding whether to raise. Carried on the descriptor for that reason.
    /// True in exactly one place in Django 6.0.9: `ExtendsNode`.
    pub must_be_first: bool,
}

pub struct NodeListDesc {
    pub nodes: Vec<NodeDesc>,
    pub contains_nontext: bool,
}

pub enum AttrValue {
    Str(String),
    /// A `SafeString`, not a `str`. Needed because `{% verbatim %}` renders at
    /// parse time and its result is marked safe; losing that would start
    /// escaping verbatim output. Distinct from `Str` rather than a flag on it,
    /// so that a forgotten case is a compile error rather than silent escaping.
    SafeStr(String),
    Bool(bool),
    Int(i64),
    None,
    /// An opaque handle to a host-built value (M3's FilterExpression).
    Ref(NodeRef),
}
```

Why this shape and not the alternatives:

- **Not a flat array with child indices.** It would avoid deep recursion, but the
  descriptor is built from a recursive parser and read by a recursive
  materialiser; a flat form buys nothing here and makes every task harder to
  read. `Parser.parse` recursion depth is bounded by template nesting, not by
  template size.
- **`class_name` as a string, not an enum.** The shell has to construct a
  *Python* class by name anyway (it uses a registry), and string names let a
  later milestone add a class without changing the Rust enum — which matters
  because M7's `BlockNode`/`ExtendsNode` are Python-owned and must still appear
  in the tree.
- **`Ref(NodeRef)` for opaque host values.** This is the seam's existing
  `NodeRef` (§5.2): Rust stores it and hands it back, never inspecting it.
- **AttrValue does *not* carry a nodelist.** Nodelists are separate so that
  `contains_nontext` travels with them and the shell can construct a real
  `NodeList` rather than a `list`.

**Known gap, to be resolved in M3:** `FilterExpression` attributes. M2 proves the
shape with `Ref`, but `Ref` in M2 can only be produced by the host. If M3 finds
it needs Rust to *describe* a filter expression structurally (rather than hold a
host object), `AttrValue` grows a variant. That is an additive change and the
reason `AttrValue` is an enum rather than a struct.

That prediction has already been exercised once, which is weak evidence it is
the right shape: `SafeStr` was added for `{% verbatim %}`'s parse-time
`SafeString` while writing this plan, and the change touched only the enum and
its two conversion sites.

## Design decision 2 — who raises the syntax error

Django's `Parser.error(token, e)` **returns** an exception and only sets
`e.token` if absent, so the innermost token wins (verified, `base.py:619-630`;
recorded in §6). The shell must be able to raise a real `TemplateSyntaxError`
carrying `.token`.

Decision: **Rust builds the message string and the token; the shell raises.** The
`Error::Syntax` variant carries `(message, Option<TokenDesc>)`, and
`_native.parse` returns it as a Python exception of the shell's choosing. This
keeps §6's "Rust owns the message" true while the exception *object* — which
carries `.token` and is inspected by `assertRaisesMessage` — stays Python's,
exactly as `ParserHandle`'s contract already requires for parse failures (§5.2).

## Review Focus

Each line names a failure mode this design implies but that no task's happy-path
test would catch. Each has a test in the task that owns the code.

1. **`contains_nontext` is per-nodelist and survives nesting.** It is a *class*
   attribute on Django's `NodeList` (`base.py:1088`) shadowed per instance by
   `extend_nodelist`. A Rust implementation that puts it on the parser, or on the
   node, passes every single-tag test and fails `{% extends %}` ordering. (Task 2)
2. **`token.contents.split()[0]` is not `split_contents()`.** Tag *dispatch* uses
   the raw `str.split` first word; `split_contents` is for a tag's arguments
   afterwards. Using the M1 splitter for dispatch changes which tags are found —
   but **not** on `{% if(x) %}`, an earlier version of this plan's example and
   the wrong one to test: `smart_split` yields `if(x)` as a single bit, so both
   splitters agree. The real discriminators are a **quoted first word**
   (`{% 'a b' %}` → `'a` raw, `'a b'` through smart_split) and the
   U+001C–U+001F table difference. Task 2's two tests use exactly those, after
   the wrong example was found by checking it against Django rather than by
   reasoning about it. (Task 2)
   Being Python's `str.split`, it splits on `str.isspace()` — use
   `pyunicode::split`, never `str::split_whitespace`; M1 shipped that
   divergence until commit `1a013ae`. (Task 2)
3. **`prepend_token`, not a consumed token.** On reaching `parse_until` the
   terminator is pushed *back*; the caller then consumes it with `next_token()`.
   A parser that consumes it produces `endfor`-shaped nodes or silently drops a
   tag. (Task 2)
4. **`delete_first_token` deletes `tokens[-1]`, and `tokens` is reversed.**
   Off-by-one here is invisible until a block tag's *second* parse, i.e. the
   `{% empty %}` branch of `{% for %}`. (Task 2)
5. **The innermost token wins on a nested error.** `error()` only sets `.token`
   when absent, so a compile error inside an `{% if %}` body must keep the inner
   token. Raising with the outer token passes any single-level test. (Task 2,
   Task 6)
6. **`unclosed_block_tag` reports the *command*, from `command_stack`.** The
   message names the tag that was left open and the list it was looking for.
   Popping the wrong stack frame, or reporting the terminator instead of the
   opener, is a message-only difference that the suite pins exactly. (Task 2)
7. **Materialised nodes must be real instances, not handles.** `ExtendsNode.render`
   isinstance-tests `TextNode` and `ExtendsNode` and calls
   `get_nodes_by_type(BlockNode)` (§10.4). A generic node class passes §5.5's
   stated tests and fails at M7. (Task 4)
8. **`{% verbatim %}` renders at compile time.** Its content is
   `SafeString("".join(text))`, and losing safeness starts escaping verbatim
   output. (Task 5)
9. **`{% comment %}` uses `skip_past`, not `parse`.** Different mechanism,
   different failure path: `skip_past` on an unclosed comment pops
   `command_stack` and reports via `unclosed_block_tag([endtag])`. (Task 5)
10. **A node's `__init__` runs before `extend_nodelist` attaches `token` and
    `origin`, and some `__init__`s read the subtree.** `ExtendsNode.__init__`
    (`loader_tags.py:90-94`) computes
    `self.blocks = {n.name: n for n in nodelist.get_nodes_by_type(BlockNode)}`
    during construction, and `ExtendsNode.__repr__` reads
    `self.parent_name.token`. So the materialiser must build a node's children
    *completely* — a real `NodeList` of real nodes — before constructing the
    parent, and must attach `token`/`origin` afterwards. A materialiser that
    hands the constructor a lazily-populated list, or that sets attributes in
    the other order, fails at M7 with an empty `blocks` mapping. (Task 4)
    (TextNode's `child_nodelists = ()` looks load-bearing but is not:
    `get_nodes_by_type` uses `getattr(self, attr, None)` and skips falsy values,
    so the inherited default would behave identically. It is an optimisation.)

---

## Wire form of the descriptor (elaborating Design decision 1)

Design decision 1 fixes the Rust types but not what crosses the FFI, and the
shell cannot be written without it, so it is fixed here — before any code — for
the same reason design decision 1 is: everything from M3 to M15 crosses it.

`_native.parse` returns **nested Python tuples**, and an attribute value is a
**native Python value**:

```text
nodelist := (nodes, contains_nontext)
nodes    := tuple[desc, ...]
desc     := (class_name, attrs, nodelists, token)
class_name  : str
attrs       := tuple[tuple[str, value], ...]
nodelists   := tuple[tuple[str, nodelist], ...]
token       : None | (token_type: int, contents: str, lineno: int, position: (int, int) | None)
value       : str | bool | int | None | NodeRef | SafeStr
```

Four consequences, each a decision rather than an accident:

1. **`AttrValue` has no discriminant on the wire.** Python's own types carry it,
   so the materialiser decodes with `isinstance` — testing `bool` before `int`,
   because `bool` is a subclass of `int` and `setting=None` would otherwise never
   be distinguishable from `setting=False`. A tagged union would be one line per
   variant to build and unreadable in a hand-written test fixture.
2. **`token` has the same shape `_native.lex` already returns**, so the shell
   builds a real `Token` from it with the same four values it will use at M9.
3. **`NodeRef` and `SafeStr` are `_native` classes**, not strings or bare
   indices. `NodeRef` is design decision 1's opaque handle; `SafeStr` exists
   because `{% verbatim %}`'s content is a *`SafeString`* computed at parse time
   (Review Focus item 8), and a plain `str` on the wire would silently lose that
   and start escaping verbatim output.
4. **`must_be_first` is not on the wire.** Rust reads it during the parse
   (Task 2); the materialised Python class carries its own copy, exactly as
   Django's does. `NodeDesc` therefore has one field the wire form drops.

## What M2 deliberately does not do

Stated so that the gaps are reviewable rather than discovered:

- **No `VariableNode`.** A `{{ … }}` tag reaches `parse` (M2 has tokenised it
  correctly since M1) and fails there with a message naming M3, rather than
  becoming a placeholder node that renders nothing. A placeholder would pass a
  shallow "the tree has the right shape" test while being wrong forever.
- **No `{% resetcycle %}` node can be built.** Both of its paths are errors at
  M2 — `"No cycles in template."` and `"Named cycle 'x' does not exist."` — so
  the tag's *success* path, and with it the only built-in producer of
  `AttrValue::Ref`, waits for `{% cycle %}` in M5. That is what makes the
  stand-ins in Task 3 necessary rather than decorative.
- **No host tag registry.** `Host`/`NoHost` exist (the seam is §5.2's, and
  dispatch order is observable), but `_native.parse` always passes `NoHost`.
- **`extra_data` is not on the Rust parser.** It must be a real Python `dict`
  holding Python objects — `{% load %}` puts a library in it and
  `{% partialdef %}` a `PartialTemplate` proxy — so it belongs to the host.
  Absent here rather than modelled wrongly.

### Task 1: The descriptor — Rust's `Node`, `NodeList`, and the wire vocabulary

**Files:**
- Delete: `crates/template-core/src/parser.rs` (the 3-line M2 placeholder)
- Create: `crates/template-core/src/parser/mod.rs`
- Create: `crates/template-core/src/parser/desc.rs`

**Interfaces:**
- Consumes: `crate::token::{Token, TokenType}`.
- Produces: `TEXT_NODE: &str`; `NodeRef(u64)`; `TokenDesc { token_type, contents, lineno, position }` + `TokenDesc::of(&Token)`; `AttrValue::{Str, SafeStr, Bool, Int, None, Ref}`; `NodeListDesc { nodes, contains_nontext }` (`Default`); `NodeDesc { class_name, attrs, nodelists, must_be_first, token }` + `NodeDesc::{new, with_nodelist, attr, nodelist, first, text}`.

- [ ] **Step 1: Delete the placeholder**

The placeholder is a *file* `parser.rs`. `parser/mod.rs` cannot coexist with it,
and `parser/parser.rs` is banned by `clippy::module_inception` — which is why
the plan's path is `parser/mod.rs`.

```bash
git rm crates/template-core/src/parser.rs
```

Run: `ls crates/template-core/src/parser.rs`
Expected: `ls: … No such file or directory`

- [ ] **Step 2: Write `crates/template-core/src/parser/desc.rs`**

```rust
//! The node descriptor: the plain-data tree Rust hands to the Python shell.
//!
//! Rust owns the parse and Python owns the tree that renders (design spec §5.5),
//! so these types are the only thing that crosses between them. They are
//! deliberately plain: no trait objects, no Python, no lifetimes.
//!
//! **The descriptor *is* Rust's `Node`/`NodeList`, not a parallel model of
//! them.** Django's `Node` carries `must_be_first`, `child_nodelists` and a
//! `render`; only `must_be_first` is read during a parse, and `render` is
//! Python's (§5.5). Django's `NodeList` is a `list` subclass whose one piece of
//! parse-time state is `contains_nontext`, which [`NodeListDesc`] carries.
//! Inventing a second Rust type for either would mean a conversion layer with no
//! reader.

use crate::token::{Token, TokenType};

/// The Python class name of a text node, which `extend_nodelist` special-cases.
///
/// Django tests `isinstance(node, TextNode)`. The descriptor has only the name,
/// and no class in Django's template package subclasses `TextNode` (verified
/// against the pinned checkout), so comparing names is exact. Task 7 pins the
/// assumption, so a future Django that subclasses it fails loudly instead of
/// silently treating a text node as non-text.
pub const TEXT_NODE: &str = "TextNode";

/// An opaque handle to a host-owned value.
///
/// `template-core` stores it and hands it back; it never reads it. The first
/// real producer is M3's `FilterExpression`; M2 produces one only from
/// `#[cfg(test)]` stand-ins, because `{% resetcycle %}`'s operand — the other
/// intended producer — is unreachable until `{% cycle %}` exists (M5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct NodeRef(pub u64);

/// A token, as it appears inside a descriptor.
///
/// The same four fields in the same order as `_native.lex` returns them, so the
/// shell builds a real `Token` from either without a second adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenDesc {
    pub token_type: TokenType,
    pub contents: String,
    /// Always present: a parse only ever sees lexed tokens.
    pub lineno: usize,
    /// Character indices, or `None` for the non-debug lexer.
    pub position: Option<(usize, usize)>,
}

impl TokenDesc {
    pub fn of(token: &Token) -> Self {
        Self {
            token_type: token.token_type,
            contents: token.contents.clone(),
            lineno: token.lineno,
            position: token.position,
        }
    }
}

/// One constructor argument of a node.
#[derive(Debug, Clone, PartialEq)]
pub enum AttrValue {
    Str(String),
    /// A string that is already safe. Materialises through `mark_safe`.
    ///
    /// `{% verbatim %}` renders its body at *parse* time and keeps a
    /// `SafeString`; a plain `str` here would be escaped when the node finally
    /// renders, which is Review Focus item 8.
    SafeStr(String),
    Bool(bool),
    Int(i64),
    None,
    /// A host-owned value, held by handle. See [`NodeRef`].
    Ref(NodeRef),
}

/// A compiled nodelist: Django's `NodeList` as far as a parse can observe it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct NodeListDesc {
    pub nodes: Vec<NodeDesc>,
    /// Django's `NodeList.contains_nontext`, a *class* attribute shadowed per
    /// instance by `extend_nodelist`. It is per-nodelist — not per-parser and
    /// not per-node — and `{% extends %}`'s ordering rule depends on it being
    /// reset for every new list while still surviving nesting
    /// (Review Focus item 1).
    pub contains_nontext: bool,
}

/// A compiled node.
#[derive(Debug, Clone, PartialEq)]
pub struct NodeDesc {
    /// The Python class to materialise, e.g. `"TextNode"`, `"AutoEscapeControlNode"`.
    pub class_name: String,
    /// Constructor arguments **by parameter name**, so the materialiser can
    /// build `kwargs` and call `cls(**kwargs)`; see `materialise_node`.
    pub attrs: Vec<(String, AttrValue)>,
    /// Child nodelists, by the attribute name the class's `__init__` takes.
    pub nodelists: Vec<(String, NodeListDesc)>,
    /// Read by `extend_nodelist` during the parse. Not carried on the wire.
    pub must_be_first: bool,
    /// Attached by `extend_nodelist` **after** construction, which is why it is
    /// not one of the `attrs` (Review Focus item 10).
    pub token: Option<TokenDesc>,
}

impl NodeDesc {
    /// A node with no arguments and no child nodelists.
    pub fn new(class_name: impl Into<String>) -> Self {
        Self {
            class_name: class_name.into(),
            attrs: Vec::new(),
            nodelists: Vec::new(),
            must_be_first: false,
            token: None,
        }
    }

    /// A node whose only child nodelist is `name`.
    pub fn with_nodelist(
        class_name: impl Into<String>,
        name: impl Into<String>,
        nodelist: NodeListDesc,
    ) -> Self {
        Self::new(class_name).nodelist(name, nodelist)
    }

    #[must_use]
    pub fn attr(mut self, name: impl Into<String>, value: AttrValue) -> Self {
        self.attrs.push((name.into(), value));
        self
    }

    #[must_use]
    pub fn nodelist(mut self, name: impl Into<String>, nodelist: NodeListDesc) -> Self {
        self.nodelists.push((name.into(), nodelist));
        self
    }

    /// Django's `Node.must_be_first`, which exactly one built-in sets:
    /// `ExtendsNode` (`loader_tags.py:87`). Nothing in M2 sets it; the
    /// `#[cfg(test)]` stand-in in Task 3 does, so the rule is tested before M7
    /// depends on it.
    #[must_use]
    pub fn first(mut self) -> Self {
        self.must_be_first = true;
        self
    }

    /// Django's `TextNode(token.contents)`, whose parameter is named `s`.
    pub fn text(contents: &str) -> Self {
        Self::new(TEXT_NODE).attr("s", AttrValue::Str(contents.to_string()))
    }
}
```

- [ ] **Step 3: Add the descriptor's unit tests**

Append to `crates/template-core/src/parser/desc.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_node_uses_djangos_parameter_name() {
        // `TextNode.__init__(self, s)`, and the materialiser constructs by
        // keyword, so the name is part of the contract rather than an internal
        // choice. Renaming it here fails at M4's materialisation test.
        let node = NodeDesc::text("hi");
        assert_eq!(node.class_name, "TextNode");
        assert_eq!(
            node.attrs,
            vec![("s".to_string(), AttrValue::Str("hi".to_string()))],
        );
    }

    #[test]
    fn builders_keep_insertion_order() {
        let node = NodeDesc::new("X")
            .attr("a", AttrValue::Int(1))
            .attr("b", AttrValue::Bool(true));
        assert_eq!(
            node.attrs,
            vec![
                ("a".to_string(), AttrValue::Int(1)),
                ("b".to_string(), AttrValue::Bool(true)),
            ],
        );
    }

    #[test]
    fn a_new_node_is_not_required_to_be_first_and_has_no_token() {
        let node = NodeDesc::new("X");
        assert!(!node.must_be_first);
        assert_eq!(node.token, None);
        assert!(node.nodelists.is_empty());
    }

    #[test]
    fn the_field_and_the_builder_are_independent() {
        // `NodeDesc` has both a `must_be_first` field and a `first()` builder,
        // so a builder that forgot to set the field would be invisible to every
        // other test here.
        assert!(NodeDesc::new("X").first().must_be_first);
    }

    #[test]
    fn contains_nontext_defaults_to_false_per_nodelist() {
        assert!(!NodeListDesc::default().contains_nontext);
        assert!(NodeListDesc::default().nodes.is_empty());
    }

    #[test]
    fn token_desc_round_trips_every_field() {
        let token = Token {
            token_type: TokenType::Block,
            contents: "if x".to_string(),
            lineno: 3,
            position: Some((4, 9)),
        };
        assert_eq!(
            TokenDesc::of(&token),
            TokenDesc {
                token_type: TokenType::Block,
                contents: "if x".to_string(),
                lineno: 3,
                position: Some((4, 9)),
            },
        );
    }

    #[test]
    fn with_nodelist_is_one_named_nodelist_and_nothing_else() {
        let node = NodeDesc::with_nodelist("SpacelessNode", "nodelist", NodeListDesc::default());
        assert_eq!(node.class_name, "SpacelessNode");
        assert_eq!(
            node.nodelists,
            vec![("nodelist".to_string(), NodeListDesc::default())],
        );
        assert!(node.attrs.is_empty());
    }
}
```

- [ ] **Step 4: Write `crates/template-core/src/parser/mod.rs`**

Task 2 fills in the parser proper; this step only makes the module exist and
re-export the descriptor, so that the crate compiles between the two steps.

```rust
//! Parsing, following `django.template.base.Parser`.

pub mod desc;

pub use desc::{AttrValue, NodeDesc, NodeListDesc, NodeRef, TokenDesc, TEXT_NODE};
```

- [ ] **Step 5: Build, test, lint**

```bash
source scripts/env.sh
cargo test -p template-core parser 2>&1 | tail -20
echo "test exit=${PIPESTATUS[0]}"
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m2t1.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m2t1.txt
```

Expected: `test result: ok. 7 passed` (the descriptor's tests), and
`clippy exit=0` with no output.

`cargo test` does **not** run clippy, and `tail`'s exit code is not clippy's —
which is why the lint is run separately, unpiped, and its own `$?` echoed. Both
mistakes have already happened in this project.

- [ ] **Step 6: Commit**

Step 1's `git rm` already staged the placeholder's deletion, so it must not be
named here — `git add -A <removed path>` exits 128 with "pathspec did not match
any files". Only the new directory is added; the deletion is already staged.

```bash
git add -A crates/template-core/src/parser/
git commit -m "M2 Task 1: the node descriptor

Rust's Node/NodeList: NodeDesc, NodeListDesc, AttrValue, TokenDesc and the
NodeRef handle, plus the wire vocabulary the materialiser reads. Deletes the
parser.rs placeholder; the module is parser/mod.rs because
clippy::module_inception bans parser/parser.rs."
```

---

### Task 2: The parser core

**Files:**
- Create: `crates/template-core/src/parser/error.rs`
- Create: `crates/template-core/src/parser/host.rs`
- Create: `crates/template-core/src/parser/tags.rs`
- Modify: `crates/template-core/src/parser/mod.rs`
- Modify: `crates/template-core/src/text.rs` (add `get_text_list`)

**Interfaces:**
- Consumes: `desc::{AttrValue, NodeDesc, NodeListDesc, NodeRef, TokenDesc, TEXT_NODE}`, `crate::token::{Token, TokenType}`, `crate::text::get_text_list`.
- Produces:
  - `error`: `Error { kind, token }`, `ErrorKind::{Syntax, Host, UnterminatedMarker, Unimplemented, Internal}`, `Error::{syntax, host, unterminated_marker, unimplemented, internal, message}`.
  - `host`: `trait Host { fn has_tag(&self, &str) -> bool; fn call_tag(&self, &str, &Token) -> Result<NodeDesc, Error>; }`, `struct NoHost`.
  - `tags`: `type CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>`, `fn builtin_tag(&str) -> Option<CompileFn>`.
  - `mod`: `Parser<'a>`, `Parser::{new, with_origin_template_repr, parse, skip_past, next_token, prepend_token, delete_first_token, error, invalid_block_tag, unclosed_block_tag, extend_nodelist}`.
  - `text`: `fn get_text_list(&[String], &str) -> String`.

- [ ] **Step 1: Add `get_text_list` to `crates/template-core/src/text.rs`**

It is needed because `invalid_block_tag`'s message ends
`expected 'endautoescape' or 'endspaceless'` and the suite pins the text
exactly. The two-item case has no comma, which is the part hand-rolling gets
wrong.

Add above the `#[cfg(test)]` block in `crates/template-core/src/text.rs`:

```rust
/// `get_text_list(items, last_word)` (`django/utils/text.py:282`).
///
/// Joins with `", "` and the final item with `last_word`: `"a, b or c"`, and
/// notably `"a or b"` for two items — with no comma. The parser's
/// `invalid_block_tag` message is built with it and is pinned by the suite.
///
/// Django's version passes the separator through `gettext`; the message is
/// already assembled in the host's language by then, and M2 has no i18n layer,
/// so the untranslated separator is what is used here. Noted because it is a
/// real difference, not an oversight.
pub fn get_text_list(items: &[String], last_word: &str) -> String {
    if items.is_empty() {
        return String::new();
    }
    if items.len() == 1 {
        return items[0].clone();
    }
    format!(
        "{} {} {}",
        items[..items.len() - 1].join(", "),
        last_word,
        items[items.len() - 1],
    )
}
```

And add to its `#[cfg(test)] mod tests`:

```rust
    #[test]
    fn get_text_list_joins_with_the_last_word() {
        fn items(names: &[&str]) -> Vec<String> {
            names.iter().map(|name| name.to_string()).collect()
        }
        assert_eq!(get_text_list(&items(&[]), "or"), "");
        assert_eq!(get_text_list(&items(&["a"]), "or"), "a");
        // Two items take no comma: this is the case a hand-rolled join gets
        // wrong, and it is the one `_("{% endautoescape %}{% endspaceless %}")`
        // -style messages hit.
        assert_eq!(get_text_list(&items(&["a", "b"]), "or"), "a or b");
        assert_eq!(get_text_list(&items(&["a", "b", "c"]), "or"), "a, b or c");
        assert_eq!(get_text_list(&items(&["a", "b", "c"]), "and"), "a, b and c");
    }
```

- [ ] **Step 2: Write `crates/template-core/src/parser/error.rs`**

```rust
//! The parser's error protocol, ported from `Parser.error` and its callers.

use crate::parser::desc::TokenDesc;

/// What the parser failed with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ErrorKind {
    /// A syntax error. Rust owns the message (§6); the exception *object* is
    /// Python's (design decision 2).
    Syntax(String),
    /// An exception raised by a host tag.
    ///
    /// M2 has no host tag that can fail, so the payload is the host's message.
    /// M4 must replace it with the host's exception object held **by identity**:
    /// flattening a Python exception to a string loses `_culprit_node` and its
    /// `__traceback__`, which the annotation protocol depends on (§5.2). An
    /// enum variant that must change is preferable to a `String` that looks
    /// settled.
    Host(String),
    /// `Token::split_contents` reached Django's `next(bits)` on an exhausted
    /// iterator — an unterminated `_("…` marker.
    ///
    /// Django lets `StopIteration` propagate out of the parse (only `Exception`
    /// is caught, to attach `.token`), so this is **not** a `Syntax`: the shell
    /// raises `StopIteration`, matching what M1's `_native.split_contents`
    /// already does at the same boundary.
    UnterminatedMarker,
    /// Something M2 deliberately does not do yet. Removed by the milestone that
    /// supplies it, so the list of these is the milestone's remaining work.
    Unimplemented(String),
    /// An invariant the parser relies on was violated.
    ///
    /// Every use documents why it is believed unreachable, and it exists so a
    /// broken invariant becomes an `Error` rather than a panic unwinding
    /// through the FFI. Django's equivalent is an `IndexError`.
    Internal(String),
}

/// An error, plus the token Django's `Parser.error` attached to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    /// Set by the **first** `error()` call to see this error, never replaced.
    ///
    /// That is Django's `if not hasattr(e, "token")`, and it is what makes the
    /// **innermost** token win when a compile error unwinds through nested
    /// `parse` frames (Review Focus item 5).
    pub token: Option<TokenDesc>,
}

impl Error {
    pub fn syntax(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Syntax(message.into()),
            token: None,
        }
    }

    pub fn host(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Host(message.into()),
            token: None,
        }
    }

    pub fn unterminated_marker() -> Self {
        Self {
            kind: ErrorKind::UnterminatedMarker,
            token: None,
        }
    }

    pub fn unimplemented(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Unimplemented(message.into()),
            token: None,
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Internal(message.into()),
            token: None,
        }
    }

    /// The message, whatever kind this is.
    ///
    /// `UnterminatedMarker` has no message of its own — Python raises a bare
    /// `StopIteration` with no arguments — so it is named after its cause. Only
    /// diagnostic paths read it.
    pub fn message(&self) -> String {
        match &self.kind {
            ErrorKind::Syntax(message)
            | ErrorKind::Host(message)
            | ErrorKind::Unimplemented(message)
            | ErrorKind::Internal(message) => message.clone(),
            ErrorKind::UnterminatedMarker => "unterminated translation marker".to_string(),
        }
    }
}
```

Add its tests:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::token::TokenType;

    fn marker(contents: &str, lineno: usize) -> TokenDesc {
        TokenDesc {
            token_type: TokenType::Block,
            contents: contents.to_string(),
            lineno,
            position: None,
        }
    }

    #[test]
    fn constructors_start_without_a_token() {
        // Load-bearing: `error()` uses `token.is_none()` as its `hasattr` test,
        // so a constructor that pre-filled the token would make the innermost
        // rule silently never apply.
        assert_eq!(Error::syntax("a").token, None);
        assert_eq!(Error::host("a").token, None);
        assert_eq!(Error::unterminated_marker().token, None);
        assert_eq!(Error::unimplemented("a").token, None);
        assert_eq!(Error::internal("a").token, None);
    }

    #[test]
    fn message_preserves_every_kinds_text() {
        assert_eq!(Error::syntax("s").message(), "s");
        assert_eq!(Error::host("h").message(), "h");
        assert_eq!(Error::unimplemented("u").message(), "u");
        assert_eq!(Error::internal("i").message(), "i");
        assert_eq!(
            Error::unterminated_marker().message(),
            "unterminated translation marker",
        );
    }

    #[test]
    fn a_token_can_be_attached_to_any_kind() {
        // `parse` attaches the token to whatever a compile function returned,
        // including `Host` — the branch that matters at M4.
        let host_error = Error::host("refused");
        let attached = Error {
            kind: host_error.kind,
            token: Some(marker("hosty", 1)),
        };
        assert_eq!(attached.token.unwrap().contents, "hosty");
    }
}
```

- [ ] **Step 3: Write `crates/template-core/src/parser/host.rs`**

```rust
//! The host seam: the tags the parser must ask Python to compile.
//!
//! Ports the tag half of §5.2's `ExtensionHost`. It is two methods rather than
//! one because `Parser.parse` tests membership *before* calling — a single
//! `Option`-returning method would have to distinguish "no such tag" from "the
//! tag failed", which the error type does not.
//!
//! `template-core` never sees a Python object here: the `NodeDesc` that comes
//! back is already plain data, and the host is the only thing that touches
//! Python.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::token::Token;

pub trait Host {
    /// Whether `command` is a tag the host can compile.
    ///
    /// Called for every block tag that Rust has no compile function for, which
    /// at M2 is every block tag until `builtin_tag` answers.
    fn has_tag(&self, command: &str) -> bool;

    /// Compile `command`, given the token dispatch matched on.
    ///
    /// Called only after `has_tag` returned true for the same `command`.
    fn call_tag(&self, command: &str, token: &Token) -> Result<NodeDesc, Error>;
}

/// A host with no tags at all.
///
/// Used by `_native.parse` until M6 supplies a real registry, and by every Rust
/// unit test that is not specifically testing dispatch order.
pub struct NoHost;

impl Host for NoHost {
    fn has_tag(&self, _command: &str) -> bool {
        false
    }

    fn call_tag(&self, _command: &str, _token: &Token) -> Result<NodeDesc, Error> {
        // Unreachable through `Parser::parse`, which consults `has_tag` first.
        // A panic here rather than a silent `Err` so that a future dispatch bug
        // is loud; `parse` never lets it out of a Rust test.
        unreachable!("NoHost::call_tag is never reached: has_tag always returns false")
    }
}
```

- [ ] **Step 4: Write `crates/template-core/src/parser/tags.rs`**

Only the registry and the seam-facing type exist in this task; Task 5 adds the
nine compile functions.

```rust
//! Rust's compile functions for built-in tags.

use crate::parser::desc::NodeDesc;
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
pub fn builtin_tag(_command: &str) -> Option<CompileFn> {
    // Filled in by Task 5. Until then every block tag is either a host tag or
    // an invalid one.
    None
}
```

- [ ] **Step 5: Write `crates/template-core/src/parser/mod.rs`**

```rust
//! Parsing, following `django.template.base.Parser`.
//!
//! `parse` is a direct port of Django's loop (`base.py:529-591`). Four details
//! are easy to lose and each has a test below:
//!
//! - tags are dispatched on `token.contents.split()[0]`, the **raw** first word
//!   — `split_contents` is for a tag's arguments afterwards, and the two
//!   disagree on a quoted first word, `{% 'a b' %}` giving `'a` raw and
//!   `'a b'` through `smart_split` (they do *not* disagree on `{% if(x) %}`,
//!   which is one bit either way, so that is not the case to test);
//! - reaching `parse_until` **prepends** the terminator and returns, leaving the
//!   caller to consume it;
//! - `tokens` is reversed, so `delete_first_token` deletes `tokens[-1]`;
//! - an error keeps the **innermost** token, because `error()` only sets one
//!   when there is none.

pub mod desc;
pub mod error;
pub mod host;
pub mod tags;

pub use desc::{AttrValue, NodeDesc, NodeListDesc, NodeRef, TokenDesc, TEXT_NODE};
pub use error::{Error, ErrorKind};
pub use host::{Host, NoHost};
pub use tags::CompileFn;

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
    // The cycle registry (`parser._last_cycle_node`, `parser._named_cycle_nodes`)
    // arrives in Task 5 with `{% resetcycle %}`, which is its first reader. It is
    // not declared here because a field nothing reads is a `dead_code` warning
    // under `-D warnings`, and a milestone that adds state before its reader is
    // how that happens.
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
                    // The raw first word — `token.contents.split()[0]`
                    // (`base.py:561`), not `split_contents()` (Review Focus
                    // item 2). The two part company on a *quoted* first word:
                    // `{% 'a b' %}` dispatches on `'a` raw and on `'a b'` via
                    // smart_split, so routing dispatch through M1's splitter
                    // would look up a different tag. They also part company on
                    // U+001C-U+001F, which is why this is `pyunicode::split`
                    // and not `split_whitespace`.
                    //
                    // Django wraps the index in `try/except IndexError`
                    // (`base.py:561-562`), which is how empty contents is
                    // handled — not `next(bits)`, whose exhaustion is
                    // `split_contents`' failure mode on a different code path.
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
                    } else if let Some(compile) = tags::builtin_tag(&command) {
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
            let expected: Vec<String> = parse_until
                .iter()
                .map(|name| format!("'{name}'"))
                .collect();
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
```

- [ ] **Step 6: Add the parser core's unit tests**

Append to `crates/template-core/src/parser/mod.rs`:

```rust
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
        let mut parser = Parser::new(Lexer::new("a{{ endcomment }}b{% endcomment %}c").tokenize(), &NoHost);
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
}
```

- [ ] **Step 7: Build, test, lint**

```bash
source scripts/env.sh
cargo test -p template-core parser 2>&1 | tail -30
echo "test exit=${PIPESTATUS[0]}"
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m2t2.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m2t2.txt
```

Expected: `test result: ok` with no failures, and `clippy exit=0`.

- [ ] **Step 8: Commit**

```bash
git add -A crates/template-core/src/parser/ crates/template-core/src/text.rs
git commit -m "M2 Task 2: the parser core

Parser::parse as a port of Django's loop, the error protocol with the
innermost-token rule, the Host seam, command_stack, extend_nodelist and
contains_nontext, plus get_text_list for the invalid-block-tag message."
```

---

### Task 3: Tag dispatch, and the stand-ins that prove the machinery

**Files:**
- Modify: `crates/template-core/src/parser/tags.rs` (test-only stand-in compile functions and their dispatch arms, plus the registry's tests)

**Interfaces:**
- Consumes: Task 2's `Parser`, `Host`, `NoHost`, `Error`, `desc::*`.
- Produces: nothing public. This task's deliverable is *evidence*: that dispatch, `contains_nontext`, the must-be-first rule, the innermost token, `Ref` and the host seam all work, before any of the nine real tags exists to be confused with them.
- Adds to `builtin_tag`, under `#[cfg(test)]`: `"standin"`, `"standin_ref"`, `"standin_first"`.

- [ ] **Step 1: Add the stand-in compile functions to `crates/template-core/src/parser/tags.rs`**

They live in `tags.rs` rather than in the test module because `builtin_tag`'s
dispatch arms must reference them; a `#[cfg(test)]` item and a `#[cfg(test)]`
match arm compile together in both test and non-test builds (verified).

Append to `crates/template-core/src/parser/tags.rs` (after `builtin_tag`, before
any test module):

```rust
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
```

Add the imports that makes necessary at the top of the file:

```rust
use crate::parser::desc::NodeDesc;
// `AttrValue` and `NodeRef` are used only by the `#[cfg(test)]` stand-ins
// below. Importing them unconditionally fails the non-test build under
// `-D warnings` with `unused_imports`, which is how this was found — the
// task's own note about the stand-in arms being gated covers dead *code*, and
// this is the import half of the same gate.
#[cfg(test)]
use crate::parser::desc::{AttrValue, NodeRef};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;
```

- [ ] **Step 2: Add the dispatch arms**

Replace `builtin_tag`'s body with:

```rust
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
```

- [ ] **Step 3: Add the registry's tests**

Append to `crates/template-core/src/parser/tags.rs`:

```rust
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
        let mut parser =
            Parser::new(Lexer::new("a{% standin %}b{% endstandin %}c").tokenize(), &NoHost);
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
        assert!(nodelist.nodes[0].nodelists[0].1.contains_nontext, "inner list has a non-text node");
        assert!(!nodelist.nodes[1].nodelists[0].1.contains_nontext, "second body is text only");
        assert!(nodelist.contains_nontext, "the outer list holds block nodes");
    }

    #[test]
    fn text_alone_never_sets_contains_nontext() {
        let mut parser = Parser::new(Lexer::new("{% standin %}a{% endstandin %}").tokenize(), &NoHost);
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
        let error = failure_with_origin(
            "{% standin_first %}{% standin_first %}",
            None,
        );
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
        let error = parser.parse(&[]).expect_err("the host shadows the built-in");
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
```

- [ ] **Step 4: Build, test, lint**

```bash
source scripts/env.sh
cargo test -p template-core parser 2>&1 | tail -30
echo "test exit=${PIPESTATUS[0]}"
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m2t3.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m2t3.txt
```

Expected: `test result: ok`, and `clippy exit=0`. The three `#[cfg(test)]` arms
must not produce dead-code warnings in the non-test build — they do not, because
the items and their only references are gated together.

- [ ] **Step 5: Confirm the guards can fail**

Per the project's rule that a guard which has never failed is not known to work,
break each of the three properties Task 3 exists to protect and confirm the
failure, then revert:

The three predictions below were **wrong twice**. They are recorded here as
observed rather than as planned, because a prediction that disagrees with the
measurement is the useful part:

1. In `parse`, drop the token instead of `self.prepend_token(token);` →
   **4** failures, not the 2 predicted:
   `the_terminator_is_prepended_not_consumed`,
   `a_block_tag_reads_its_body_and_terminates` (`left: ["a"]`,
   `right: ["a", "c"]`), `contains_nontext_starts_false_for_every_nodelist`,
   `text_alone_never_sets_contains_nontext`.
   The predicted `an_unclosed_block_names_the_tag_that_was_left_open` does
   **not** fail — it exercises the `command_stack` path on token exhaustion,
   which never reaches the prepend.
2. In `Parser::error` — **not** `extend_nodelist`, where this plan first put
   it; that function does not contain this expression — replace
   `token: error.token.or_else(|| Some(TokenDesc::of(token)))` with
   `token: Some(TokenDesc::of(token))` → 3 failures:
   `the_innermost_token_wins_across_a_nested_parse` at line 3 (as predicted),
   `an_invalid_tag_inside_a_block_lists_what_was_expected`, and
   `the_token_is_attached_once_and_never_replaced`.
3. In `extend_nodelist`, move `nodelist.contains_nontext = true` outside the
   `if node.class_name != TEXT_NODE` guard → 5 failures, not the 1 predicted:
   `text_alone_never_sets_contains_nontext` (as predicted), plus
   `contains_nontext_starts_false_for_every_nodelist`,
   `a_first_node_may_lead_any_nodelist`, `comment_tokens_are_dropped_entirely`,
   and `the_innermost_token_wins_across_a_nested_parse`.

Record the three observed failures in the task's commit message; a revert that
is not confirmed green afterwards is how a broken guard gets committed.

The lesson worth carrying: each prediction named the test its author expected
to be sensitive to that property, and two were wrong — both because the
predicted test was insensitive for a structural reason the plan had not
traced. A guard-break step is a measurement, not a formality. Writing the
expected result down first is what makes it informative, and it is also what
makes the disagreement visible when it comes.

- [ ] **Step 6: Commit**

```bash
git add -A crates/template-core/src/parser/tags.rs
git commit -m "M2 Task 3: tag dispatch and the stand-ins

Registry plus compile-function dispatch, and three test-only tags that prove
what no M2 built-in can reach: a body-reading block, an AttrValue::Ref carrier,
and a must_be_first node. Guards broken and confirmed failing, as observed
rather than as predicted — two of the three predictions were wrong, and the
counts and failing tests here are the measured ones:
 - dropping the prepended terminator -> 4 failures (predicted 2, and named a
   test that turns out not to reach this code path at all)
 - overwriting an attached token, in Parser::error -> 3 failures, including
   the innermost-token test at line 3 as predicted
 - always setting contains_nontext -> 5 failures (predicted 1)

Co-Authored-By: Claude Code <noreply@anthropic.com>
"
```

---

### Task 4: `_native.parse`, and materialisation into real Python nodes

**Files:**
- Modify: `crates/template-py/src/lib.rs`
- Create: `python/django_rust_templates/_impl/parser.py`
- Create: `tests/test_materialise.py`

**Interfaces:**
- Consumes: Task 2's `Parser`, `NoHost`, `Error`, `ErrorKind`; Task 1's `desc::*`; the existing `CoreLexer` binding.
- Produces:
  - Rust: `_native.NodeRef(index)`, `_native.SafeStr(text)`, `_native.ParseError`, `_native.parse(source, debug=False, origin_template_repr=None) -> tuple`.
  - Python: `django_rust_templates._impl.parser.NODE_CLASSES`, `materialise(nodelist, origin=None, resolve_ref=None) -> NodeList`, `materialise_node(desc, origin=None, resolve_ref=None) -> Node`.

- [ ] **Step 1: Add the wire classes and the exception to `crates/template-py/src/lib.rs`**

Add to the `use` block at the top:

```rust
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::types::PyTuple;

use template_core::parser::desc::{AttrValue, NodeDesc, NodeListDesc, TokenDesc};
use template_core::parser::{Error, ErrorKind, NoHost, Parser as CoreParser};
```

Then add, after `lex`:

```rust
/// An opaque handle to a host-owned value carried inside a descriptor.
///
/// `template-core` stores the index and never reads it. The shell resolves it
/// through the `resolve_ref` callback passed to `materialise`; M3's
/// `FilterExpression` is the first real producer. Constructible from Python so
/// that a test can hand-build a descriptor, which is the only reason this is a
/// `#[pyclass]` with a `#[new]` rather than an opaque capsule.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct NodeRef {
    #[pyo3(get)]
    pub index: u64,
}

#[pymethods]
impl NodeRef {
    #[new]
    fn new(index: u64) -> Self {
        Self { index }
    }

    fn __repr__(&self) -> String {
        format!("<NodeRef {}>", self.index)
    }
}

/// A string that is already safe (`django.utils.safestring.SafeData`).
///
/// It rides on the wire rather than a plain `str` because `{% verbatim %}`'s
/// content is `nodelist.render(Context())` — a `SafeString` — computed at
/// **parse** time (Review Focus item 8), and a plain `str` would be escaped when
/// the node finally renders. The shell converts it with `mark_safe`; Rust does
/// not import Django to do so.
#[pyclass(frozen)]
#[derive(Debug)]
pub struct SafeStr {
    #[pyo3(get)]
    pub text: String,
}

#[pymethods]
impl SafeStr {
    #[new]
    fn new(text: String) -> Self {
        Self { text }
    }

    fn __repr__(&self) -> String {
        format!("<SafeStr {:?}>", self.text)
    }
}

create_exception!(
    _native,
    ParseError,
    PyException,
    "A template parse failure raised by `_native.parse`.\n\
     \n\
     Carries `message` and `token`; the Python shell catches it and raises\n\
     `django.template.exceptions.TemplateSyntaxError` with the same message and\n\
     a real `Token` object, which is design decision 2: Rust owns the message,\n\
     Python owns the exception object that `assertRaisesMessage` and\n\
     `Template.get_exception_info` inspect."
);
```

- [ ] **Step 2: Add the descriptor-to-Python conversion**

`PyTuple::new` takes an `ExactSizeIterator` of `IntoPyObject`, and each element
here is an already-built `Py<PyAny>`, so the nested tuples are built
bottom-up. Add after the classes above:

```rust
/// A token's wire shape, shared with `_native.lex`.
type WireToken = (u8, String, usize, Option<(usize, usize)>);

fn token_to_py(py: Python<'_>, token: &TokenDesc) -> PyResult<Py<PyAny>> {
    let wire: WireToken = (
        token.token_type as u8,
        token.contents.clone(),
        token.lineno,
        token.position,
    );
    Ok(wire.into_pyobject(py)?.into_any().unbind())
}

fn attr_value_to_py(py: Python<'_>, value: &AttrValue) -> PyResult<Py<PyAny>> {
    Ok(match value {
        AttrValue::Str(s) => s.as_str().into_pyobject(py)?.into_any().unbind(),
        AttrValue::SafeStr(s) => Py::new(py, SafeStr::new(s.clone()))?.into_any(),
        // `bool`'s `IntoPyObject` yields a `Borrowed` (Python's `True`/`False`
        // are singletons), so it must be owned before `into_any` can consume it:
        // `(*b).into_pyobject(py)?.into_any()` is a borrowck error (E0507).
        AttrValue::Bool(b) => (*b).into_pyobject(py)?.to_owned().into_any().unbind(),
        AttrValue::Int(i) => (*i).into_pyobject(py)?.into_any().unbind(),
        AttrValue::None => py.None(),
        AttrValue::Ref(r) => Py::new(py, NodeRef::new(r.0))?.into_any(),
    })
}

/// A `(name, value)` pair as a 2-tuple.
fn pair_to_py(py: Python<'_>, name: &str, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
    Ok(
        PyTuple::new(py, [name.into_pyobject(py)?.into_any().unbind(), value])?
            .into_any()
            .unbind(),
    )
}

fn node_desc_to_py(py: Python<'_>, desc: &NodeDesc) -> PyResult<Py<PyAny>> {
    let attrs = desc
        .attrs
        .iter()
        .map(|(name, value)| pair_to_py(py, name, attr_value_to_py(py, value)?))
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    let nodelists = desc
        .nodelists
        .iter()
        .map(|(name, nodelist)| pair_to_py(py, name, nodelist_desc_to_py(py, nodelist)?))
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    let token = match &desc.token {
        Some(token) => token_to_py(py, token)?,
        None => py.None(),
    };

    Ok(PyTuple::new(
        py,
        [
            desc.class_name.as_str().into_pyobject(py)?.into_any().unbind(),
            PyTuple::new(py, attrs)?.into_any().unbind(),
            PyTuple::new(py, nodelists)?.into_any().unbind(),
            token,
        ],
    )?
    .into_any()
    .unbind())
}

fn nodelist_desc_to_py(py: Python<'_>, nodelist: &NodeListDesc) -> PyResult<Py<PyAny>> {
    let nodes = nodelist
        .nodes
        .iter()
        .map(|node| node_desc_to_py(py, node))
        .collect::<PyResult<Vec<Py<PyAny>>>>()?;

    Ok(PyTuple::new(
        py,
        [
            PyTuple::new(py, nodes)?.into_any().unbind(),
            // Same `Borrowed`-needs-owning fix as `attr_value_to_py`'s bool arm.
            nodelist
                .contains_nontext
                .into_pyobject(py)?
                .to_owned()
                .into_any()
                .unbind(),
        ],
    )?
    .into_any()
    .unbind())
}

/// Translate a parse failure into the Python exception the shell will re-raise.
///
/// The token rides as an attribute on the exception **instance** rather than
/// through a `#[pyclass(extends = PyException)]` with a custom `__new__`: every
/// `BaseException` instance has a `__dict__`, so this needs no extra class and
/// `e.token` is readable exactly as it will be on the shell's
/// `TemplateSyntaxError`.
fn parse_error(py: Python<'_>, error: &Error) -> PyResult<PyErr> {
    let err = match &error.kind {
        // Django reaches `next(bits)` on an exhausted iterator and lets
        // `StopIteration` out of `parse` — only `Exception` is caught, to attach
        // the token. A bare `StopIteration` has no arguments and so an empty
        // `str()`, which is what `PyStopIteration::new_err(())` produces; giving
        // it a message would be a gratuitous difference from Django at the one
        // place `assertRaises` is used instead of `assertRaisesMessage`.
        ErrorKind::UnterminatedMarker => PyStopIteration::new_err(()),
        _ => ParseError::new_err(error.message()),
    };
    let token = match &error.token {
        Some(token) => token_to_py(py, token)?,
        None => py.None(),
    };
    err.value(py).setattr("token", token)?;
    Ok(err)
}
```

- [ ] **Step 3: Add the `parse` function and register everything**

Add after `parse_error`:

```rust
/// Parse `source` into a node descriptor tree.
///
/// `debug` selects Django's `DebugLexer`, so token positions are recorded.
/// `origin_template_repr` is Python's `repr()` of `Origin.template_name`, or
/// `None` when the origin has no template name; it arrives pre-rendered because
/// a Python `repr` is Python's to produce — the rule §5.2 already records for
/// `LookupError::Missing::current_repr`.
#[pyfunction]
#[pyo3(signature = (source, debug = false, origin_template_repr = None))]
fn parse(
    py: Python<'_>,
    source: &str,
    debug: bool,
    origin_template_repr: Option<String>,
) -> PyResult<Py<PyAny>> {
    let tokens = if debug {
        CoreLexer::debug(source).tokenize()
    } else {
        CoreLexer::new(source).tokenize()
    };

    let mut parser = CoreParser::new(tokens, &NoHost);
    if let Some(repr) = origin_template_repr {
        parser = parser.with_origin_template_repr(repr);
    }

    match parser.parse(&[]) {
        Ok(nodelist) => nodelist_desc_to_py(py, &nodelist),
        Err(error) => Err(parse_error(py, &error)?),
    }
}
```

And replace the `#[pymodule]` with:

```rust
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(split_contents, m)?)?;
    m.add_function(wrap_pyfunction!(lex, m)?)?;
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_class::<NodeRef>()?;
    m.add_class::<SafeStr>()?;
    m.add("ParseError", m.py().get_type::<ParseError>())?;
    Ok(())
}
```

`CoreParser::new(tokens, &NoHost)` relies on temporary lifetime extension for
the `&NoHost` — it lives to the end of the function body.

- [ ] **Step 4: Rebuild the extension**

```bash
source scripts/env.sh
maturin develop 2>&1 | tail -5
echo "maturin exit=${PIPESTATUS[0]}"
.venv/bin/python -c "
from django_rust_templates import _native
print(_native.parse('a\nb'))
print(_native.NodeRef(3), _native.SafeStr('x'), _native.ParseError)
"
```

Expected: a nested 2-tuple whose `nodes` element holds a `('TextNode', (('s','a\nb'),),
(), (...))` entry, then `<NodeRef 3> <SafeStr "x"> <class '_native.ParseError'>`.

A tag-based smoke test (`{% csrf_token %}`, `{% spaceless %}` …) does **not**
work here: `builtin_tag` returns `None` for every name until Task 5 registers the
nine M2 built-ins (`crates/template-core/src/parser/tags.rs`), so any of them
fails with "Invalid block tag" at Step 4. Plain text is the only source that
parses at this point.

`create_exception!(_native, …)` names the module `_native`, not the fully
qualified `django_rust_templates._native`, so `repr(ParseError)` is
`<class '_native.ParseError'>`. The class object is still the same one
`django_rust_templates._native.ParseError` resolves to.

- [ ] **Step 5: Write `python/django_rust_templates/_impl/parser.py`**

```python
"""Materialise a Rust node descriptor into Django's real node classes.

Rust owns the parse; Python owns the tree that renders (design spec §5.5). This
module is the only place the two meet, and it is deliberately mechanical: it
looks up a class by name, calls it with keyword arguments, and attaches
``token``/``origin`` afterwards.

The path is ``_impl/parser.py`` as the milestone plan specifies. Note the
consequence: ``_impl`` is on the shim's search path for ``django.template``, so
this file also makes ``django.template.parser`` importable — a name Django does
not have. Nothing imports it (the shell reaches it as
``django_rust_templates._impl.parser``), and ``_shim.unshimmed()`` compares
source trees, so it neither shadows anything nor perturbs the progress meter.
Flagged rather than silently relocated, because if Django ever grows a
``template/parser.py`` this name would shadow it.
"""

from __future__ import annotations

from django.template.base import Node, NodeList, TextNode, Token, TokenType
from django.template.defaulttags import (
    AutoEscapeControlNode,
    CommentNode,
    CsrfTokenNode,
    DebugNode,
    NowNode,
    ResetCycleNode,
    SpacelessNode,
    TemplateTagNode,
    VerbatimNode,
)
from django.utils.safestring import mark_safe

from django_rust_templates import _native

__all__ = ("NODE_CLASSES", "materialise", "materialise_node")

# An explicit registry rather than `getattr(base, class_name)`: a
# `getattr`-based lookup would resolve anything that happens to be in Django's
# modules, turning a Rust typo into a wrong node class instead of a loud
# failure, and it would silently pick up a class M7 must *not* materialise from
# Rust (block/extends/include are Python-owned).
NODE_CLASSES = {
    "TextNode": TextNode,
    "AutoEscapeControlNode": AutoEscapeControlNode,
    "CommentNode": CommentNode,
    "CsrfTokenNode": CsrfTokenNode,
    "DebugNode": DebugNode,
    "NowNode": NowNode,
    "ResetCycleNode": ResetCycleNode,
    "SpacelessNode": SpacelessNode,
    "TemplateTagNode": TemplateTagNode,
    "VerbatimNode": VerbatimNode,
}


def materialise(nodelist_desc, origin=None, resolve_ref=None):
    """Build a real :class:`~django.template.base.NodeList` from a descriptor."""
    nodes, contains_nontext = nodelist_desc

    nodelist = NodeList()
    # Django's `contains_nontext` is a *class* attribute, so leaving it alone is
    # how it reads False. Only the True transition is ever written, which is what
    # makes a second, text-only nodelist read False again.
    if contains_nontext:
        nodelist.contains_nontext = True

    for desc in nodes:
        nodelist.append(materialise_node(desc, origin, resolve_ref))

    return nodelist


def materialise_node(desc, origin=None, resolve_ref=None):
    """Build one real node, children first.

    The ordering is load-bearing (Review Focus item 10): a node's ``__init__``
    runs **before** ``token``/``origin`` are attached, and some ``__init__``s read
    their own subtree — ``ExtendsNode.__init__`` builds ``self.blocks`` by walking
    the nodelist it is given. So children are materialised completely, then the
    parent is constructed, then the attributes are set.
    """
    class_name, attrs, nodelists, token = desc

    try:
        cls = NODE_CLASSES[class_name]
    except KeyError:
        raise LookupError(
            f"no materialiser for node class {class_name!r}; add it to NODE_CLASSES"
        ) from None

    kwargs = {name: _decode_value(value, resolve_ref) for name, value in attrs}
    for name, child in nodelists:
        kwargs[name] = materialise(child, origin, resolve_ref)

    node = cls(**kwargs)

    node.token = _decode_token(token) if token is not None else None
    node.origin = origin
    return node


def _decode_token(wire):
    """A real :class:`Token` from the four-field wire form.

    Same order as ``_native.lex``'s tuples: Django's signature is
    ``Token(token_type, contents, position=None, lineno=None)``.
    """
    token_type, contents, lineno, position = wire
    return Token(TokenType(token_type), contents, position, lineno)


def _decode_value(value, resolve_ref):
    """A descriptor attribute as a Python object.

    ``bool`` is tested first because ``bool`` subclasses ``int``: an
    ``isinstance(value, int)`` arm ahead of it would turn every boolean
    attribute — ``{% autoescape on %}``'s ``setting`` — into ``0``/``1``, which
    compares equal and passes any ``assertEqual`` while being the wrong type.
    """
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, _native.NodeRef):
        if resolve_ref is None:
            raise LookupError(
                f"{value!r} needs a resolve_ref callback; none was passed to materialise"
            )
        return resolve_ref(value.index)
    if isinstance(value, _native.SafeStr):
        return mark_safe(value.text)
    raise LookupError(
        f"cannot decode descriptor value {value!r} of type {type(value).__name__}"
    )
```

- [ ] **Step 6: Write `tests/test_materialise.py`**

```python
"""Materialisation: descriptors become Django's own node objects.

These run against Django's real modules (``.django`` on ``sys.path``, no shim),
because the whole point is that the objects produced are Django's — the same
classes ``isinstance``, ``get_nodes_by_type`` and ``ExtendsNode.render`` test for.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import (  # noqa: E402
    Node,
    NodeList,
    TextNode,
    Token,
    TokenType,
)
from django.template.defaulttags import (  # noqa: E402
    AutoEscapeControlNode,
    SpacelessNode,
    VerbatimNode,
)
from django.utils.safestring import SafeString  # noqa: E402

from django_rust_templates import _native  # noqa: E402

# Loaded from its file, not as `django_rust_templates._impl.parser`:
# `_impl/__init__.py` is the shim's `django.template` and imports its own
# siblings (`from .engine import Engine` …), so importing the `_impl` *package*
# by its ordinary name executes that file and fails for want of `engine`. This
# is how the materialiser is reached without installing the shim.
_MATERIALISER_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates._impl.parser",
    PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "parser.py",
)
materialiser = importlib.util.module_from_spec(_MATERIALISER_SPEC)
sys.modules[_MATERIALISER_SPEC.name] = materialiser
_MATERIALISER_SPEC.loader.exec_module(materialiser)

# token_type 0 is `TokenType.TEXT`: a TextNode's token is a TEXT token, and
# `test_the_token_is_a_real_token` asserts exactly that.
TEXT = ("TextNode", (("s", "hi"),), (), (0, "hi", 1, (0, 2)))


def text_desc(contents, token=None):
    return ("TextNode", (("s", contents),), (), token)


class MaterialiseTests(unittest.TestCase):
    def test_a_text_node_is_a_real_text_node(self):
        nodelist = materialiser.materialise(([TEXT], False))
        self.assertIsInstance(nodelist, NodeList)
        self.assertEqual(len(nodelist), 1)
        node = nodelist[0]
        self.assertIsInstance(node, TextNode)
        self.assertEqual(node.s, "hi")

    def test_the_token_is_a_real_token(self):
        node = materialiser.materialise_node(TEXT)
        self.assertIsInstance(node.token, Token)
        self.assertEqual(node.token.token_type, TokenType.TEXT)
        self.assertEqual(node.token.contents, "hi")
        self.assertEqual(node.token.lineno, 1)
        self.assertEqual(node.token.position, (0, 2))
        self.assertIsNone(node.origin)

    def test_attrs_are_passed_by_keyword_and_kept_by_type(self):
        desc = ("AutoEscapeControlNode", (("setting", True),), (("nodelist", ([], False)),), None)
        node = materialiser.materialise_node(desc)
        self.assertIsInstance(node, AutoEscapeControlNode)
        self.assertIs(node.setting, True)
        self.assertIsInstance(node.setting, bool)
        self.assertIsInstance(node.nodelist, NodeList)

    def test_a_false_setting_is_not_confused_with_none(self):
        # The wire has `bool` and `None` and no discriminant: this is the case
        # an `isinstance(value, int)` test ordered before `bool` would flatten,
        # and it is the one `{% autoescape off %}` produces.
        desc = ("AutoEscapeControlNode", (("setting", False),), (("nodelist", ([], False)),), None)
        self.assertIs(materialiser.materialise_node(desc).setting, False)

    def test_children_are_built_before_the_parent_and_token_comes_after(self):
        class SpyNode(Node):
            def __init__(self, nodelist):
                self.received = nodelist
                self.seen = [type(child).__name__ for child in nodelist]
                # `Node.token` is a *class* attribute defaulting to None
                # (`base.py:1036`), so "not attached yet" reads as None, not as
                # a missing attribute — `getattr(self, "token", "absent")` would
                # return None and compare unequal to "absent" on every run,
                # passing or failing for the wrong reason.
                self.token_at_construction = getattr(self, "token", None)

        materialiser.NODE_CLASSES["SpyNode"] = SpyNode
        self.addCleanup(materialiser.NODE_CLASSES.pop, "SpyNode")

        desc = ("SpyNode", (), (("nodelist", ([text_desc("in")], False)),), (2, "spy", 1, None))
        node = materialiser.materialise_node(desc)

        # A real `NodeList`, not a generator or a plain list, and populated
        # before construction: a lazily-materialised child list fails one of
        # these, exactly as `ExtendsNode.__init__`'s
        # `nodelist.get_nodes_by_type(BlockNode)` would at M7.
        self.assertIsInstance(
            node.received,
            NodeList,
            "the constructor must receive a real NodeList, not a lazy list",
        )
        self.assertEqual(
            node.seen, ["TextNode"], "the constructor must receive a complete child list"
        )
        self.assertIsNone(
            node.token_at_construction,
            "token must be attached after construction, not before",
        )
        self.assertEqual(node.token.contents, "spy")

    def test_contains_nontext_maps_both_ways(self):
        self.assertFalse(materialiser.materialise(([], False)).contains_nontext)
        self.assertTrue(materialiser.materialise(([], True)).contains_nontext)

    def test_a_second_nodelist_does_not_inherit_the_flag(self):
        # `contains_nontext` is a class attribute on NodeList, so an
        # implementation that set it on the class rather than the instance would
        # make every later nodelist read True.
        materialiser.materialise(([text_desc("x")], True))
        self.assertFalse(materialiser.materialise(([text_desc("y")], False)).contains_nontext)

    def _register(self, name, cls):
        materialiser.NODE_CLASSES[name] = cls
        self.addCleanup(materialiser.NODE_CLASSES.pop, name)

    def test_a_ref_is_resolved_through_the_callback(self):
        class RefNode(Node):
            def __init__(self, thing):
                self.thing = thing

        self._register("RefNode", RefNode)

        desc = ("RefNode", (("thing", _native.NodeRef(7)),), (), None)
        seen = []

        def resolve(index):
            seen.append(index)
            return index

        # Not `resolve_ref=seen.append`: that returns None, so `node.thing`
        # would be None and the `assertEqual(node.thing, 7)` below could never
        # pass — the test would fail on a correct implementation.
        node = materialiser.materialise_node(desc, resolve_ref=resolve)
        self.assertEqual(seen, [7], "the index must reach the resolver unchanged")
        self.assertEqual(node.thing, 7)

    def test_a_ref_without_a_resolver_is_loud(self):
        class RefNode(Node):
            def __init__(self, thing):
                self.thing = thing

        self._register("RefNode", RefNode)

        desc = ("RefNode", (("thing", _native.NodeRef(7)),), (), None)
        with self.assertRaises(LookupError) as caught:
            materialiser.materialise_node(desc)
        self.assertIn("resolve_ref", str(caught.exception))

    def test_a_safe_str_becomes_a_safe_string(self):
        desc = ("VerbatimNode", (("content", _native.SafeStr("{{ raw }}")),), (), None)
        node = materialiser.materialise_node(desc)
        self.assertIsInstance(node, VerbatimNode)
        self.assertIsInstance(node.content, SafeString)
        self.assertEqual(node.content, "{{ raw }}")

    def test_an_unknown_class_name_names_itself(self):
        with self.assertRaises(LookupError) as caught:
            materialiser.materialise_node(("NoSuchNode", (), (), None))
        self.assertIn("NoSuchNode", str(caught.exception))

    def test_an_unknown_value_type_is_loud(self):
        desc = ("TextNode", (("s", object()),), (), None)
        with self.assertRaises(LookupError):
            materialiser.materialise_node(desc)


class ParseToMaterialiseTests(unittest.TestCase):
    """`_native.parse` and the materialiser together, on real M2 templates."""

    def test_text_round_trips(self):
        nodelist = materialiser.materialise(_native.parse("a\nb"))
        self.assertEqual([node.s for node in nodelist], ["a\nb"])

    def test_a_real_tag_produces_its_real_class(self):
        nodelist = materialiser.materialise(_native.parse("{% spaceless %} x {% endspaceless %}"))
        self.assertEqual(len(nodelist), 1)
        node = nodelist[0]
        self.assertIsInstance(node, SpacelessNode)
        self.assertIsInstance(node.nodelist, NodeList)
        self.assertEqual([child.s for child in node.nodelist], [" x "])
        self.assertEqual(node.token.contents, "spaceless")

    def test_verbatim_content_is_safe(self):
        nodelist = materialiser.materialise(_native.parse("{% verbatim %}{{ x }}{% endverbatim %}"))
        self.assertIsInstance(nodelist[0].content, SafeString)
        self.assertEqual(nodelist[0].content, "{{ x }}")

    def test_a_parse_error_carries_a_token(self):
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse("\n{% nope %}")
        self.assertEqual(
            str(caught.exception),
            "Invalid block tag on line 2: 'nope'. "
            "Did you forget to register or load this tag?",
        )
        self.assertEqual(caught.exception.token[2], 2)
        self.assertEqual(caught.exception.token[1], "nope")

    def test_an_unterminated_marker_is_a_stop_iteration(self):
        # Django's own behaviour at this point: `next(bits)` on an exhausted
        # generator, caught by `except Exception` only to attach the token.
        with self.assertRaises(StopIteration) as caught:
            _native.parse('{% now _("unterminated %}')
        self.assertEqual(str(caught.exception), "")
        self.assertEqual(caught.exception.token[2], 1)

    def test_the_wire_form_is_plain_data(self):
        """Nothing Python reaches Rust, and nothing Rust-shaped reaches Python."""
        def walk(value):
            if isinstance(value, tuple):
                for item in value:
                    walk(item)
                return
            if isinstance(value, (_native.NodeRef, _native.SafeStr)):
                return
            self.assertIsInstance(value, (str, int, bool, type(None)), f"odd {value!r}")

        walk(_native.parse("{% templatetag openblock %}", debug=True))

    def test_debug_positions_cross_the_wire(self):
        # `Lexer` gives no positions, `DebugLexer` does; the shell must be able
        # to reconstruct both, because M9 exposes the two as distinct classes.
        # `{% debug %}`'s tag token spans characters 0..11 — `DebugLexer` and the
        # Rust lexer both report `(0, 11)` for it (verified against `.django`).
        debug_node = materialiser.materialise(_native.parse("{% debug %}", debug=True))[0]
        self.assertEqual(debug_node.token.position, (0, 11))
        plain_node = materialiser.materialise(_native.parse("{% debug %}"))[0]
        self.assertIsNone(plain_node.token.position)


if __name__ == "__main__":
    unittest.main()
```

`test_debug_positions_cross_the_wire` unpacks the single node from the nodelist
via tuple-unpacking of the `NodeList` — a `NodeList` is a `list`, so
`(node,) = nodelist` works; write it as `node = nodelist[0]` if the unpacking
form reads awkwardly.

- [ ] **Step 7: Run the new tests**

```bash
source scripts/env.sh
.venv/bin/python -m unittest tests.test_materialise -v 2>&1 | tail -30
echo "exit=${PIPESTATUS[0]}"
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m2t4.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m2t4.txt
```

Expected: `clippy exit=0`, and **14 of the 19 tests pass**. The other five
error with "Invalid block tag … Did you forget to register or load this tag?",
and that is correct at Task 4 — Task 5 is where the nine M2 built-in tags get
registered in Rust. Those five, all of which parse a tag through `_native.parse`
directly, are:

- `test_a_real_tag_produces_its_real_class` (`{% spaceless %}`)
- `test_verbatim_content_is_safe` (`{% verbatim %}`)
- `test_an_unterminated_marker_is_a_stop_iteration` (`{% now … %}`)
- `test_the_wire_form_is_plain_data` (`{% templatetag openblock %}`)
- `test_debug_positions_cross_the_wire` (`{% debug %}`)

They are written here in full because they belong to this task's materialiser;
they go green when Task 5 lands the tags, and Step 7's gate is "no *unexpected*
failure" — the twelve `MaterialiseTests` and the two parse-independent
`ParseToMaterialiseTests` (`test_text_round_trips`, `test_a_parse_error_carries_a_token`)
must pass.

Two of these tests are self-checks worth keeping: `test_the_wire_form_is_plain_data`
walks the whole descriptor and fails on any type outside the documented set, and
`SpyNode` pins Review Focus item 10 by inspecting what a constructor actually
receives rather than trusting the order of two lines.

- [ ] **Step 8: Commit**

```bash
git add -A crates/template-py/src/lib.rs python/django_rust_templates/_impl/parser.py tests/test_materialise.py
git commit -m "M2 Task 4: _native.parse and materialisation

The FFI wire vocabulary (nested tuples, NodeRef, SafeStr, ParseError) and the
shell that turns descriptors into Django's own Node/NodeList objects, children
first and token after construction."
```

---

### Task 5: The nine M2 built-in tags

**Files:**
- Modify: `crates/template-core/src/parser/mod.rs` (the cycle registry, for `{% resetcycle %}`)
- Modify: `crates/template-core/src/parser/tags.rs` (the nine compile functions, their helpers, and the dispatch arms)

**Interfaces:**
- Consumes: Task 2's `Parser`, `Error`, `extend_nodelist`; Task 1's `desc::*`.
- Produces: `builtin_tag` answers for `autoescape`, `comment`, `csrf_token`, `debug`, `now`, `resetcycle`, `spaceless`, `templatetag`, `verbatim`; descriptors for `AutoEscapeControlNode`, `CommentNode`, `CsrfTokenNode`, `DebugNode`, `NowNode`, `SpacelessNode`, `TemplateTagNode`, `VerbatimNode`. `ResetCycleNode` has **no** reachable producer at M2 — both of its paths are errors until `{% cycle %}` exists (M5) — which is recorded rather than worked around.

- [ ] **Step 1: Add the cycle registry to `Parser`**

`{% resetcycle %}` reads `parser._last_cycle_node` and
`parser._named_cycle_nodes`, which `{% cycle %}` (M5) writes. Declaring them here
rather than in Task 2 keeps each field next to its first reader, which is also
what keeps `dead_code` quiet under `-D warnings`.

In `crates/template-core/src/parser/mod.rs`, add to the `use` block:

```rust
use std::collections::HashMap;
```

Add to the `Parser` struct, replacing the placeholder comment Task 2 left:

```rust
    /// `parser._last_cycle_node` and `parser._named_cycle_nodes`, which
    /// `{% cycle %}` sets and `{% resetcycle %}` reads by name or by default.
    ///
    /// Both hold host-owned `CycleNode`s, so they are handles here. M2 only ever
    /// reads them and always finds them empty, because `{% cycle %}` is M5; the
    /// fields exist now so that `{% resetcycle %}`'s two error paths are the
    /// real ones rather than a hard-coded "no cycles".
    last_cycle_node: Option<NodeRef>,
    named_cycle_nodes: HashMap<String, NodeRef>,
```

And in `Parser::new`'s struct literal:

```rust
            last_cycle_node: None,
            named_cycle_nodes: HashMap::new(),
```

Fields are private and `tags.rs` is a child module of `parser`, so the compile
functions read them directly; no accessor is needed because a child module sees
its ancestors' private items.

- [ ] **Step 2: Add the shared helpers to `crates/template-core/src/parser/tags.rs`**

Update the `use` block:

```rust
use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc, TEXT_NODE};
// `NodeRef` is used only by the `#[cfg(test)]` registry tests below (non-test
// code holds one through `AttrValue::Ref` and the parser's fields, never as a
// written type), so it is gated with them: an ungated import is an
// `unused_imports` error under `-D warnings`. `pyunicode::split` replaces the
// `str::split_whitespace` the compile functions would otherwise reach for —
// Python's `str.split()`, which is what Django uses, is not Rust's table.
#[cfg(test)]
use crate::parser::desc::NodeRef;
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;
```

Then add:

```rust
/// `Token.split_contents`, as a compile function sees it.
///
/// Django reaches `next(bits)` on an exhausted iterator, so that failure leaves
/// `parse` as a bare `StopIteration` — with `.token` attached by the
/// `except Exception` arm — rather than a `TemplateSyntaxError`. M1's
/// `_native.split_contents` binding already fixed that type at the FFI boundary;
/// this keeps the two consistent instead of inventing a second behaviour for the
/// same condition.
fn split_contents(token: &Token) -> Result<Vec<String>, Error> {
    token.split_contents().map_err(|_| Error::unterminated_marker())
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
/// Two Django messages interpolate with `%r`: `{% resetcycle %}`'s command name
/// and `{% templatetag %}`'s list of arguments. Both are ASCII identifiers, and
/// for those this is exact — Python chooses `'` unless the string contains `'`
/// and no `"`, then escapes backslashes, the chosen quote, and the standard
/// control escapes as `\xNN` / `\uNNNN` / `\UNNNNNNNN`. Printable non-ASCII is
/// passed through, which is also what Python 3 does.
///
/// Where it is *not* exact is any character Python classifies as non-printable
/// by Unicode category, or a surrogate; nothing on this path can produce one,
/// and the alternative — a Python `repr` — would mean a call back into the host
/// from inside a compile function.
fn py_repr_str(s: &str) -> String {
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
```

- [ ] **Step 3: Write the nine compile functions**

Add to `crates/template-core/src/parser/tags.rs`:

```rust
/// `{% autoescape on|off %}…{% endautoescape %}`.
///
/// Django splits with `token.contents.split()`, not `split_contents()`, "because
/// this tag doesn't accept variable as arguments". The two are observable here:
/// for `{% autoescape "on off" %}` the raw split sees three arguments and
/// rejects on the count, while `split_contents` keeps the quoted phrase whole,
/// sees two, and rejects on the value. Both fail, with different messages, and
/// only one of them is Django's.
///
/// (The original example, `{% autoescape a"b c %}`, does **not** distinguish
/// them: the trailing quote is unmatched, so `smart_split` splits it exactly as
/// `str.split` does — verified against Django 6.0.9 — and a `split_contents`
/// implementation passed the test too. `pyunicode::split`, not
/// `str::split_whitespace`, is Django's table and is what makes
/// `{% autoescape<U+001C>on %}` two arguments.)
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
            None => Err(Error::syntax(format!("Named cycle '{name}' does not exist."))),
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
    // invalid-argument error). `pyunicode::split` is Django's `str.split()`.
    let bits = pyunicode::split(&token.contents);
    if bits.len() != 2 {
        return Err(Error::syntax("'templatetag' statement takes one argument"));
    }

    let tag = bits[1];
    if !TEMPLATETAG_ARGUMENTS.contains(&tag) {
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
    Ok(NodeDesc::new("VerbatimNode").attr(
        "content",
        AttrValue::SafeStr(render_text_only(&nodelist)?),
    ))
}
```

- [ ] **Step 4: Register them**

Replace `builtin_tag`'s body with:

```rust
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
```

- [ ] **Step 5: Add the tags' tests**

Add to the existing `#[cfg(test)] mod tests` in
`crates/template-core/src/parser/tags.rs` (the helpers `compile`, `failure`,
`attr` and `texts` from Task 3 are already there):

```rust
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
        // This is Django 6.0.9's registry minus the nine above, and the list is
        // easy to get wrong: the pinned checkout has **27** built-ins, not 26 —
        // `querystring` (`@register.simple_tag`, so it reaches `compile_filter`
        // at compile time) is absent from the milestone table below. `lorem` is a
        // plain `@register.tag` that reads `split_contents`.
        for name in [
            "if", "for", "block", "extends", "include", "load", "with", "filter", "cycle",
            "firstof", "ifchanged", "regroup", "url", "widthratio", "partialdef", "partial",
            "lorem", "querystring",
        ] {
            assert!(builtin_tag(name).is_none(), "{name} must not be registered yet");
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
        // arguments and fail on the *value* instead. The original input,
        // `{% autoescape a"b c %}`, does not distinguish them — `smart_split`
        // splits the unmatched trailing quote exactly as `str.split` does — so
        // it passed under either implementation.
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
        assert_eq!(compile("{% comment %}{% endcomment %}").class_name, "CommentNode");
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
        assert_eq!(attr(&node, "format_string"), &AttrValue::Str("Y".to_string()));
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
        // *neither* splitter strips them — `smart_split` keeps the quotes too,
        // so the original comment's "a `split_contents` implementation would
        // accept `"openblock"`" was false and the test did not pin the raw
        // split. The test below does.
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
        // error for `"open block"` instead.
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
        assert_eq!(py_repr_str("héllo"), "'héllo'");
        assert_eq!(
            py_list_repr(&["openblock", "closeblock"]),
            "['openblock', 'closeblock']",
        );
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
```

- [ ] **Step 6: Build, test, lint**

```bash
source scripts/env.sh
cargo test -p template-core parser 2>&1 | tail -30
echo "test exit=${PIPESTATUS[0]}"
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m2t5.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m2t5.txt
```

Expected: `test result: ok`, `clippy exit=0`.

The `python_repr_matches_repr_for_the_reachable_inputs` expectations were read
off CPython rather than assumed:

```bash
.venv/bin/python -c "
for s in ['resetcycle', \"don't\", 'both\'\\\"quotes', 'a\\\\b', 'a\nb\tc\rd', '\x00', 'héllo']:
    print(repr(s))
print(repr(['openblock', 'closeblock']))
"
```

- [ ] **Step 7: Commit**

```bash
git add -A crates/template-core/src/parser/
git commit -m "M2 Task 5: the nine M2 built-in tag compile functions

autoescape, comment, csrf_token, debug, now, resetcycle, spaceless,
templatetag, verbatim — the built-ins that need no FilterExpression. Adds
Parser's cycle registry, which resetcycle reads and cycle will populate.
Splitting goes through pyunicode::split, so the parser inherits M1's
corrected whitespace table (1a013ae) rather than re-deriving it."
```

---

### Task 6: Syntax-error parity against Django's own parser

**Files:**
- Create: `tests/parser_error_corpus.py`
- Create: `tests/test_parser_errors.py`

**Interfaces:**
- Consumes: `_native.parse`, and Django's real `Lexer`/`DebugLexer`/`Parser` from `.django/` with no shim in the path.
- Produces: `ERRORS: list[tuple[str, str]]` — `(source, expected message)` — and the test module that checks each expectation against Django *and* against us.

Why this is a milestone deliverable and not a nicety: the suite pins
`assertRaisesMessage` text, and `Template.get_exception_info` reads `e.token`.
An implementation whose messages are 95% right fails the drop-in test at M9 in
a way that looks like an unrelated breakage.

- [ ] **Step 1: Write `tests/parser_error_corpus.py`**

Every entry is verified against Django by the test module, so the expected
message here is a *claim the test checks*, not a recollection the test trusts —
which is the only thing that makes a hand-written corpus trustworthy.

```python
"""Template sources that must fail to parse, with the message Django gives.

Each entry is `(source, expected message)`. The expected message is verified
against Django's real parser by
`test_parser_errors.ErrorParityTests.test_the_corpus_matches_django`, so a wrong
expectation fails loudly rather than silently agreeing with a wrong
implementation.

Error text is pinned exactly: Django's own test suite compares it with
`assertRaisesMessage`, and `Template.get_exception_info` exposes `e.token`.
"""

TEMPLATETAG_ARGUMENTS = [
    "openblock",
    "closeblock",
    "openvariable",
    "closevariable",
    "openbrace",
    "closebrace",
    "opencomment",
    "closecomment",
]

# Built with `repr` rather than written out, because Python's `list()` repr of a
# list literal is insertion-ordered and exactly what `"%s" % list(mapping)`
# produces in Django. The test still checks it against Django.
TEMPLATETAG_INVALID = (
    "Invalid templatetag argument: 'nope'. Must be one of: " + repr(TEMPLATETAG_ARGUMENTS)
)

ERRORS = [
    # --- empty tags ---------------------------------------------------------
    ("{{}}", "Empty variable tag on line 1"),
    ("{%  %}", "Empty block tag on line 1"),
    ("{% %}", "Empty block tag on line 1"),
    ("\n\n{{}}", "Empty variable tag on line 3"),
    # --- unknown tags ------------------------------------------------------
    (
        "{% nope %}",
        "Invalid block tag on line 1: 'nope'. "
        "Did you forget to register or load this tag?",
    ),
    (
        "x\n{% nope %}",
        "Invalid block tag on line 2: 'nope'. "
        "Did you forget to register or load this tag?",
    ),
    # The `parse_until` form, with the two-item `get_text_list` join.
    (
        "{% spaceless %}\n{% nope %}",
        "Invalid block tag on line 2: 'nope', expected 'endspaceless'. "
        "Did you forget to register or load this tag?",
    ),
    # --- autoescape --------------------------------------------------------
    ("{% autoescape %}", "'autoescape' tag requires exactly one argument."),
    ("{% autoescape on off %}", "'autoescape' tag requires exactly one argument."),
    # A *matched* quoted phrase containing a space: the raw split counts three
    # arguments here, `split_contents` two. Only the raw split reaches the count
    # error, so this is the raw-vs-smart discriminator — NOT
    # `{% autoescape "on" %}` below, which an earlier draft of this plan called
    # "an *unmatched* quote ... the raw split counts three arguments here". That
    # was wrong against Django 6.0.9: `'autoescape "on"'.split()` is
    # `['autoescape', '"on"']`, two arguments exactly as `smart_split` gives,
    # and the template fails on the *value*, not the count.
    ('{% autoescape "on off" %}', "'autoescape' tag requires exactly one argument."),
    # An unmatched quote: `smart_split` does not treat it as opening a phrase, so
    # both splitters split it identically and it is rejected on the count.
    ('{% autoescape a"b c %}', "'autoescape' tag requires exactly one argument."),
    # A matched quoted single word is two arguments to both splitters, so this
    # one fails on the value: `'"on"'` is not `'on'`.
    ('{% autoescape "on" %}', "'autoescape' argument should be 'on' or 'off'"),
    ("{% autoescape maybe %}", "'autoescape' argument should be 'on' or 'off'"),
    (
        "{% autoescape on %}",
        "Unclosed tag on line 1: 'autoescape'. Looking for one of: endautoescape.",
    ),
    (
        "x\n\n{% autoescape on %}y",
        "Unclosed tag on line 3: 'autoescape'. Looking for one of: endautoescape.",
    ),
    # Nested: the *innermost* command frame is popped, so the line is the inner
    # tag's, not the outer one's.
    (
        "{% autoescape on %}\n{% autoescape off %}",
        "Unclosed tag on line 2: 'autoescape'. Looking for one of: endautoescape.",
    ),
    # --- comment, through skip_past rather than parse -----------------------
    (
        "{% comment %}",
        "Unclosed tag on line 1: 'comment'. Looking for one of: endcomment.",
    ),
    (
        "{% comment %}a\nb",
        "Unclosed tag on line 1: 'comment'. Looking for one of: endcomment.",
    ),
    # --- the other body-reading tags ---------------------------------------
    (
        "{% spaceless %}",
        "Unclosed tag on line 1: 'spaceless'. Looking for one of: endspaceless.",
    ),
    (
        "{% verbatim %}",
        "Unclosed tag on line 1: 'verbatim'. Looking for one of: endverbatim.",
    ),
    # --- now ---------------------------------------------------------------
    ("{% now %}", "'now' statement takes one argument"),
    ("{% now a b %}", "'now' statement takes one argument"),
    ("{% now a b c %}", "'now' statement takes one argument"),
    ("{% now a b c d %}", "'now' statement takes one argument"),
    # --- templatetag -------------------------------------------------------
    ("{% templatetag %}", "'templatetag' statement takes one argument"),
    ("{% templatetag a b %}", "'templatetag' statement takes one argument"),
    ("{% templatetag nope %}", TEMPLATETAG_INVALID),
    # Raw split, so the quotes are part of the argument and it is invalid.
    (
        '{% templatetag "openblock" %}',
        "Invalid templatetag argument: '\"openblock\"'. Must be one of: "
        + repr(TEMPLATETAG_ARGUMENTS),
    ),
    # --- resetcycle: both of its paths are errors at M2 ---------------------
    ("{% resetcycle %}", "No cycles in template."),
    ("{% resetcycle x %}", "Named cycle 'x' does not exist."),
    ("{% resetcycle a b %}", "'resetcycle' tag accepts at most one argument."),
    # --- the innermost token, across a nested parse -------------------------
    (
        "{% autoescape on %}\n{% now a b %}\n{% endautoescape %}",
        "'now' statement takes one argument",
    ),
]
```

- [ ] **Step 2: Write `tests/test_parser_errors.py`**

```python
"""Syntax-error parity with Django's own parser.

Three things are compared per case: the message, the token the error is
attributed to (including its debug positions), and the fact that Django agrees
with the corpus's expectation.

The comparison target is Django's real `Parser` imported from `.django/` with no
shim in the path, and `test_the_comparison_target_is_genuinely_djangos_parser`
keeps it that way — including by checking that no other test module has left the
shim installed in this process.
"""

import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import DebugLexer, Lexer, Parser  # noqa: E402
from django.template.engine import Engine  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.parser_error_corpus import ERRORS  # noqa: E402

# A bare `Parser(tokens)` registers **no** tags — Django's built-ins live in
# `defaulttags`/`loader_tags` libraries, which `Engine` passes to `Parser` as
# `template_builtins` (`base.py:522-523`; `builtins=None` defaults to `[]` at
# `base.py:518-519`). The plan's original helper omitted them, so every built-in
# tag reported `Invalid block tag` and the comparison was against the wrong
# Django. This is the path `Engine.compile_template` itself takes.
_ENGINE = Engine()


def lexer(source, debug):
    return DebugLexer(source) if debug else Lexer(source)


def django_failure(source, debug=False):
    """The exception Django's own parser raises, or None."""
    parser = Parser(
        lexer(source, debug).tokenize(),
        _ENGINE.template_libraries,
        _ENGINE.template_builtins,
    )
    try:
        parser.parse()
    except Exception as exc:  # noqa: BLE001  (StopIteration is deliberate)
        return exc
    return None


def ours_failure(source, debug=False):
    """The exception `_native.parse` raises, or None."""
    try:
        _native.parse(source, debug)
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


def token_shape(token):
    """The comparable fields of a token, from either implementation.

    Ours crosses the FFI as the same 4-tuple `_native.lex` returns; Django's is
    a real `Token`, whose type is a plain `Enum` rather than an `IntEnum` (so
    `.value` is the comparable part).
    """
    if token is None:
        return None
    if isinstance(token, tuple):
        return token
    return (token.token_type.value, token.contents, token.lineno, token.position)


class ErrorParityTests(unittest.TestCase):
    def test_the_corpus_matches_django(self):
        """The corpus's expectations are Django's, not the author's."""
        for source, expected in ERRORS:
            with self.subTest(source=source):
                failure = django_failure(source)
                self.assertIsNotNone(failure, "Django accepted this template")
                self.assertEqual(str(failure), expected)

    def test_every_case_raises_the_same_message(self):
        for source, expected in ERRORS:
            with self.subTest(source=source):
                ours = ours_failure(source)
                self.assertIsNotNone(ours, "we accepted this template")
                self.assertEqual(str(ours), expected)

    def test_every_case_carries_the_same_token(self):
        for source, _ in ERRORS:
            with self.subTest(source=source):
                self.assertEqual(
                    token_shape(ours_failure(source).token),
                    token_shape(django_failure(source).token),
                )

    def test_every_case_carries_the_same_token_in_debug_mode(self):
        # Positions only exist in debug mode, which is what makes this a
        # different test rather than a slower one.
        for source, _ in ERRORS:
            with self.subTest(source=source):
                ours = token_shape(ours_failure(source, debug=True).token)
                theirs = token_shape(django_failure(source, debug=True).token)
                self.assertEqual(ours, theirs)
                self.assertIsNotNone(ours, "debug parsing must still fail")

    def test_the_exception_types_differ_by_design(self):
        # Design decision 2: Rust owns the message and the token; Python owns the
        # exception object. `_native` raises its own `ParseError` and the shell
        # converts it to `TemplateSyntaxError` at M9, so the *type names* differ
        # on purpose while everything a test reads about them agrees.
        self.assertEqual(type(django_failure("{% nope %}")).__name__, "TemplateSyntaxError")
        self.assertIs(ours_failure("{% nope %}").__class__, _native.ParseError)

    def test_the_innermost_token_wins(self):
        # The outer token is on line 1 and the message alone cannot tell the two
        # apart, so this is the case a naive `error()` passes (Review Focus
        # item 5).
        source = "{% autoescape on %}\n{% now a b %}\n{% endautoescape %}"
        self.assertEqual(django_failure(source).token.lineno, 2)
        self.assertEqual(ours_failure(source).token[2], 2)

    def test_an_unterminated_marker_is_a_stop_iteration_for_both(self):
        # Django reaches `next(bits)` on an exhausted generator and lets
        # `StopIteration` out of `parse`, caught by `except Exception` only so the
        # token is attached. Ours raises it from the FFI boundary with no
        # arguments, so `str()` is empty on both sides.
        source = '{% now _("unterminated %}'
        theirs = django_failure(source)
        ours = ours_failure(source)
        self.assertIsInstance(theirs, StopIteration)
        self.assertIsInstance(ours, StopIteration)
        self.assertEqual(str(theirs), str(ours))
        self.assertEqual(token_shape(theirs.token), token_shape(ours.token))

    def test_successful_templates_raise_nothing(self):
        # The control: a corpus that every parser rejects proves nothing about
        # messages, so this pins that these four do parse.
        for source in ["", "hello", "{% csrf_token %}", "{% verbatim %}x{% endverbatim %}"]:
            with self.subTest(source=source):
                self.assertIsNone(django_failure(source))
                self.assertIsNone(ours_failure(source))

    def test_the_comparison_target_is_genuinely_djangos_parser(self):
        from django_rust_templates import _shim

        source = inspect.getsourcefile(Parser)
        self.assertNotIn("_impl", source)
        self.assertTrue(Path(source).resolve().is_relative_to(DJANGO_CHECKOUT.resolve()))
        # A leaked install from another test module would make this comparison
        # circular the moment we provide a `base` module, which M3 does.
        self.assertFalse(_shim.installed(), "the shim is installed in this process")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run them**

```bash
source scripts/env.sh
.venv/bin/python -m unittest tests.test_parser_errors -v 2>&1 | tail -30
echo "exit=${PIPESTATUS[0]}"
```

Expected: all pass. A failure on `test_the_corpus_matches_django` means the
corpus's expectation is wrong (Django is the oracle); a failure on
`test_every_case_raises_the_same_message` means the implementation is.

- [ ] **Step 4: Confirm the corpus is not vacuous**

Temporarily change one implementation message — `unclosed_block_tag`'s, say —
and confirm both `test_the_corpus_matches_django` still passes (Django is
unaffected) and `test_every_case_raises_the_same_message` fails on the
unclosed-tag cases and nothing else. It is **seven** cases, not the four an
earlier draft claimed, across four distinct tags (`autoescape` ×3, `comment` ×2,
`spaceless`, `verbatim`) — verified by running it. Revert.

This is the check that the corpus tests the implementation rather than testing
itself, and it is the reason the two assertions are separate tests.

- [ ] **Step 5: Commit**

```bash
git add -A tests/parser_error_corpus.py tests/test_parser_errors.py
git commit -m "M2 Task 6: syntax-error parity with Django's parser

A 32-case corpus of failing templates compared against Django's real Parser for
message, token and debug position, plus the StopIteration path and the
innermost-token case. The corpus's own expectations are verified against
Django, so the oracle is Django rather than this file's author."
```

---

### Task 7: Descriptor parity — `_native.parse` against Django's `Parser`, over a corpus

**Files:**
- Create: `tests/parser_corpus.py`
- Create: `tests/test_parser_parity.py`

**Interfaces:**
- Consumes: `_native.parse`, `_impl.parser.materialise`, Django's real `Parser` from `.django/`.
- Produces: `TEMPLATES: list[str]`, a seeded random generator, and the parity test. This is M2's acceptance test: it is the thing that makes "the Rust parser builds the same tree Django's does" a checked claim rather than a description.

The comparison is over the **materialised** tree, not the descriptor — that is
the point of the milestone. Comparing descriptors would leave the materialiser
unchecked, and the materialiser is what §5.5's identity-stability and M7's
`ExtendsNode`/`get_nodes_by_type` requirements land on.

- [ ] **Step 1: Write `tests/parser_corpus.py`**

```python
"""Template sources for comparing our parse tree against Django's.

Every source here must *parse successfully*: errors are Task 6's corpus. There
are therefore no `{{ variable }}` tags anywhere in this file — variable
compilation is M3's, and at M2 such a template reaches `_native.parse` and is
reported as unimplemented.

Each entry below the first block pins one behaviour, named in its comment.
"""

TEMPLATES = [
    # --- text, and the things that are not tags ----------------------------
    "",
    "hello",
    "a\nb\n",
    "  leading and trailing  ",
    "line one\nline two\nline three",
    "a{{",  # an unmatched opener is text, not a tag
    "{#",  # as is an unmatched comment opener
    "{# c #}",
    "a{# c #}b",
    "{#}{#}",  # empty comments, adjacent
    "{# {% if %} #}",  # a block tag inside a comment is not a tag
    "{# {{ x }} #}",
    "héllo 🎉",  # contents and positions are counted in characters
    # --- csrf_token and debug: no arguments are read -----------------------
    "{% csrf_token %}",
    "{% csrf_token junk %}",
    "x{% csrf_token %}y",
    "{% debug %}",
    "{% debug junk more %}",
    # --- templatetag: all eight bits, then two in sequence -----------------
    "{% templatetag openblock %}",
    "{% templatetag closeblock %}",
    "{% templatetag openvariable %}",
    "{% templatetag closevariable %}",
    "{% templatetag openbrace %}",
    "{% templatetag closebrace %}",
    "{% templatetag opencomment %}",
    "{% templatetag closecomment %}",
    "{% templatetag openblock %}{% templatetag closeblock %}",
    # --- now: quoted, unquoted, empty, and the `as` form -------------------
    '{% now "jS F Y H:i" %}',
    '{% now "Y" %}',
    '{% now "Y" as year %}',
    '{% now "Y" as year %}x',
    '{% now "a b" %}',  # one quoted phrase, so `split_contents`, not `split`
    "{% now 5 %}",  # unquoted: first and last characters are dropped
    "{% now abc %}",
    '{% now "" %}',
    # --- autoescape: both settings, empty, nested, and around a non-text node
    "{% autoescape on %}x{% endautoescape %}",
    "{% autoescape off %}x{% endautoescape %}",
    "{% autoescape on %}{% endautoescape %}",
    "{% autoescape off %}{% autoescape on %}x{% endautoescape %}{% endautoescape %}",
    "a{% autoescape on %}{% csrf_token %}{% endautoescape %}b",
    "{% autoescape on %}{% comment %}c{% endcomment %}x{% endautoescape %}",
    # --- comment: the body is skipped, never parsed -----------------------
    "{% comment %}{% endcomment %}",
    "{% comment %}anything{% endcomment %}",
    # `{% nope %}` inside a comment must not be reported: `skip_past`, not
    # `parse` (Review Focus item 9).
    "{% comment %}{% if %}{{ x }}{% nope %}{% endcomment %}x",
    "a{% comment %}b{% endcomment %}c",
    # --- spaceless: text, empty, nested, and around a non-text node --------
    "{% spaceless %}  a  {% endspaceless %}",
    "{% spaceless %}{% endspaceless %}",
    "{% spaceless %}{% spaceless %}a{% endspaceless %}{% endspaceless %}",
    "{% spaceless %}{% csrf_token %}{% endspaceless %}",
    # --- verbatim: the body is rendered at parse time, and kept safe -------
    "{% verbatim %}a{% endverbatim %}",
    "{% verbatim %}{% endverbatim %}",
    "{% verbatim %}{{ x }}{% endverbatim %}",
    # Inside verbatim, `{% if %}` is text: a *lexer* behaviour the tree depends
    # on, and one Django's own lexer also has.
    "{% verbatim %}{% if %}x{% endif %}{% endverbatim %}",
    "{% verbatim %}  spaces & {braces}  {% endverbatim %}",
    "{% verbatim %}a{% endverbatim %}{% verbatim %}b{% endverbatim %}",
    # --- combinations, to reach nesting and dispatch together --------------
    "{% spaceless %}{% verbatim %}{{ x }}{% endverbatim %}{% endspaceless %}",
    "{% autoescape off %}{% spaceless %}{% templatetag openbrace %}"
    "{% endspaceless %}{% endautoescape %}",
    "{% comment %}{% spaceless %}{% endcomment %}{% spaceless %}x{% endspaceless %}",
    "text{% csrf_token %}more{% debug %}end",
]


# --- the randomised pass ---------------------------------------------------
#
# The corpus and the implementation share an author, which is the standing
# weakness of a parity test written this way. The answer is a second, generated
# corpus: it reaches combinations nobody thought to write down, and it is seeded
# so any failure reproduces exactly.

RANDOM_SEED = 20261008
RANDOM_CASES = 400

# No `{`, `}`, `%` or `#`, so no random text accidentally forms a tag. What the
# generator is for is *shape*: nesting depth, sibling order, and which nodelists
# end up holding non-text nodes.
RANDOM_ALPHABET = "abc \n\t'\"é🎉"

INLINE_TAGS = [
    "{% csrf_token %}",
    "{% debug %}",
    "{% templatetag openblock %}",
    "{% templatetag closebrace %}",
    '{% now "Y" %}',
    '{% now "jS F Y H:i" as when %}',
]

BLOCK_TAGS = [
    ("{% autoescape on %}", "{% endautoescape %}"),
    ("{% autoescape off %}", "{% endautoescape %}"),
    ("{% spaceless %}", "{% endspaceless %}"),
    ("{% verbatim %}", "{% endverbatim %}"),
    ("{% comment %}", "{% endcomment %}"),
]


def random_text(rng):
    return "".join(rng.choice(RANDOM_ALPHABET) for _ in range(rng.randint(0, 8)))


def random_piece(rng, depth):
    if depth >= 3:
        return random_text(rng)

    roll = rng.random()
    if roll < 0.45:
        return random_text(rng)
    if roll < 0.7:
        return rng.choice(INLINE_TAGS)

    opening, closing = rng.choice(BLOCK_TAGS)
    return opening + random_piece(rng, depth + 1) + closing


def random_template(rng):
    return "".join(random_piece(rng, 0) for _ in range(rng.randint(0, 5)))
```

`{% verbatim %}` inside `{% verbatim %}` is generated on purpose: the lexer
resolves it in a way a naive reader does not predict, and the randomised pass
compares *outcomes* rather than only successes, so a source that fails is
compared as a failure. That is why Task 7's comparison has the shape it does.

- [ ] **Step 2: Write `tests/test_parser_parity.py`**

```python
"""M2's acceptance test: our parse tree is Django's parse tree.

Both sides are materialised into real Django nodes and compared structurally.
The target is Django's own `Parser` imported from `.django/` with no shim in the
path; `test_the_comparison_target_is_genuinely_djangos_parser` keeps it that way.

Two independent traversals are compared for every node: one reading `vars()`
directly (which catches a wrong attribute *name*), and one through
`get_nodes_by_type` (which walks `child_nodelists` — the same path Django's
rendering and `ExtendsNode.__init__` use, and the one that catches children
attached under a name nothing walks).
"""

import inspect
import random
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import (  # noqa: E402
    DebugLexer,
    Lexer,
    Node,
    NodeList,
    Parser,
)

from django_rust_templates import _native  # noqa: E402
from django_rust_templates._impl.parser import materialise  # noqa: E402
from tests.parser_corpus import (  # noqa: E402
    RANDOM_CASES,
    RANDOM_SEED,
    TEMPLATES,
    random_template,
)

# Kept for symmetry with Task 6's module, where the same tuple describes a wire
# token from either side.
IGNORED_ATTRIBUTES = ("token", "origin")


def django_parse(source, debug=False):
    lexer = DebugLexer(source) if debug else Lexer(source)
    return Parser(lexer.tokenize()).parse()


def ours_parse(source, debug=False):
    return materialise(_native.parse(source, debug))


def token_shape(token):
    if token is None:
        return None
    if isinstance(token, tuple):
        return token
    return (token.token_type.value, token.contents, token.lineno, token.position)


def describe(value):
    """A comparable description of a node tree, without node identity.

    `token` and `origin` are pulled out to their own key rather than dropped, so
    a node whose token is missing is distinguishable from one whose token differs.
    """
    if isinstance(value, NodeList):
        return {
            "type": "NodeList",
            "contains_nontext": value.contains_nontext,
            "nodes": [describe(node) for node in value],
        }
    if isinstance(value, Node):
        attributes = vars(value)
        return {
            "type": type(value).__name__,
            "attrs": {
                key: describe(item)
                for key, item in sorted(attributes.items())
                if key not in IGNORED_ATTRIBUTES
                and not isinstance(item, (Node, NodeList))
            },
            "nodelists": {
                key: describe(item)
                for key, item in sorted(attributes.items())
                if isinstance(item, (Node, NodeList))
            },
            "token": token_shape(getattr(value, "token", None)),
        }
    if isinstance(value, list):
        return [describe(item) for item in value]
    return value


def walked_subtree(node):
    """Type names of a node's subtree, reached through `child_nodelists`.

    The second traversal. `describe` reads the attributes it is given; this asks
    the node itself, which is what Django's renderer does.
    """
    return [type(child).__name__ for child in node.get_nodes_by_type(Node)]


def fingerprint(nodelist):
    return {
        "tree": describe(nodelist),
        "subtrees": [walked_subtree(node) for node in nodelist],
    }


def outcome(parse, source, debug):
    """(kind, payload) for either implementation, so failures compare too."""
    try:
        return ("ok", fingerprint(parse(source, debug)))
    except Exception as exc:  # noqa: BLE001  (StopIteration is deliberate)
        return ("error", str(exc), token_shape(getattr(exc, "token", None)))


class CorpusParityTests(unittest.TestCase):
    def assert_trees_match(self, source, debug):
        theirs = django_parse(source, debug)
        ours = ours_parse(source, debug)
        self.assertEqual(
            describe(ours),
            describe(theirs),
            f"tree differs for {source!r} (debug={debug})",
        )
        self.assertEqual(
            [walked_subtree(node) for node in ours],
            [walked_subtree(node) for node in theirs],
            f"subtree walk differs for {source!r} (debug={debug})",
        )

    def test_corpus_matches_without_debug_positions(self):
        for source in TEMPLATES:
            with self.subTest(source=source):
                self.assert_trees_match(source, debug=False)

    def test_corpus_matches_with_debug_positions(self):
        for source in TEMPLATES:
            with self.subTest(source=source):
                self.assert_trees_match(source, debug=True)

    def test_the_corpus_is_not_trivially_small(self):
        self.assertGreaterEqual(len(TEMPLATES), 40)
        self.assertGreaterEqual(len(set(TEMPLATES)), 40, "duplicate corpus entries")

    def test_the_corpus_covers_every_tag_with_a_success_path(self):
        joined = "\n".join(TEMPLATES)
        for tag in [
            "autoescape",
            "comment",
            "csrf_token",
            "debug",
            "now",
            "spaceless",
            "templatetag",
            "verbatim",
        ]:
            self.assertIn(f"{{% {tag}", joined, f"{tag} is not in the corpus")
        # `resetcycle` is absent on purpose: both of its paths are errors at M2,
        # so it belongs to Task 6's corpus and has no success tree to compare.
        # Its descriptor shape is pinned by a unit test in Task 5 instead.


class RandomisedParityTests(unittest.TestCase):
    def test_randomised_templates_match(self):
        rng = random.Random(RANDOM_SEED)
        outcomes = {"ok": 0, "error": 0}

        for index in range(RANDOM_CASES):
            source = random_template(rng)
            for debug in (False, True):
                with self.subTest(case=index, source=source, debug=debug):
                    theirs = outcome(django_parse, source, debug)
                    ours = outcome(ours_parse, source, debug)
                    self.assertEqual(ours, theirs, f"outcome differs for {source!r}")
                    outcomes[theirs[0]] += 1

        # A generator that only ever produced failures would compare error
        # messages and never a tree, so pin that most cases parse.
        self.assertGreater(
            outcomes["ok"],
            RANDOM_CASES,
            f"the generator barely produced successful parses: {outcomes}",
        )


class ShapeTests(unittest.TestCase):
    """Claims about the tree that a diff against Django would not isolate."""

    def test_text_nodes_are_real_text_nodes(self):
        # Django's `extend_nodelist` and `ExtendsNode.render` both isinstance-test
        # TextNode (Review Focus item 7), so the materialiser must not produce a
        # generic node for text — and it must not produce a TextNode for anything
        # else either, since `ExtendsNode.render` skips TextNode children.
        from django.template.base import TextNode

        nodes = ours_parse("a{% csrf_token %}b")
        self.assertEqual([type(node).__name__ for node in nodes],
                         ["TextNode", "CsrfTokenNode", "TextNode"])
        self.assertIsInstance(nodes[0], TextNode)
        self.assertNotIsInstance(nodes[1], TextNode)
        self.assertEqual(nodes[2].s, "b")

    def test_every_node_carries_a_real_token_and_an_origin(self):
        for node in ours_parse("a{% spaceless %}b{% endspaceless %}{% debug %}"):
            self.assertIsNotNone(node.token, f"{type(node).__name__} has no token")
            self.assertTrue(hasattr(node, "origin"))

    def test_verbatim_content_is_safe_and_rendered_at_parse_time(self):
        from django.utils.safestring import SafeString

        node = ours_parse("{% verbatim %}{{ x }}{% endverbatim %}")[0]
        self.assertIsInstance(node.content, SafeString)
        self.assertEqual(node.content, "{{ x }}")
        self.assertEqual(
            node.content,
            django_parse("{% verbatim %}{{ x }}{% endverbatim %}")[0].content,
        )

    def test_contains_nontext_is_per_nodelist(self):
        ours = ours_parse(
            "{% autoescape on %}{% csrf_token %}{% endautoescape %}"
            "{% autoescape on %}text{% endautoescape %}"
        )
        theirs = django_parse(
            "{% autoescape on %}{% csrf_token %}{% endautoescape %}"
            "{% autoescape on %}text{% endautoescape %}"
        )
        for index in (0, 1):
            self.assertEqual(
                ours[index].nodelist.contains_nontext,
                theirs[index].nodelist.contains_nontext,
                f"body {index}",
            )
        self.assertTrue(ours[0].nodelist.contains_nontext)
        self.assertFalse(ours[1].nodelist.contains_nontext)

    def test_a_variable_is_reported_rather_than_dropped(self):
        # The M3 gap, asserted from the shell's side: a variable must be loud,
        # because a silently-empty node would render wrong output rather than
        # fail.
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse("a{{ x }}b")
        self.assertIn("M3", str(caught.exception))
        self.assertEqual(caught.exception.token[2], 1)

    def test_the_comparison_target_is_genuinely_djangos_parser(self):
        from django_rust_templates import _shim

        source = inspect.getsourcefile(Parser)
        self.assertNotIn("_impl", source)
        self.assertTrue(Path(source).resolve().is_relative_to(DJANGO_CHECKOUT.resolve()))
        self.assertFalse(_shim.installed(), "the shim is installed in this process")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it**

```bash
source scripts/env.sh
.venv/bin/python -m unittest tests.test_parser_parity -v 2>&1 | tail -40
echo "exit=${PIPESTATUS[0]}"
```

Expected: all pass. `test_corpus_matches_*` runs 60-odd subTests each; a diff
prints both trees, which is the point of `describe` returning sorted dicts.

- [ ] **Step 4: Confirm the acceptance test can fail**

The corpus test is the milestone's gate, so confirm it fails for a *small* wrong
tree rather than only for a crash. Temporarily make `NodeDesc::text` emit
`AttrValue::Str` under the name `"content"` instead of `"s"`, rebuild, and
confirm `test_corpus_matches_without_debug_positions` fails on the first
text-bearing source while `test_the_corpus_covers_every_m2_tag` still passes.
Revert and rebuild.

Also confirm the second traversal earns its place: change
`materialise_node` to put children under `"body"` rather than under the name the
descriptor gives, and confirm `subtrees` differs while `tree` may not.

- [ ] **Step 5: Commit**

```bash
git add -A tests/parser_corpus.py tests/test_parser_parity.py
git commit -m "M2 Task 7: descriptor parity over a corpus

M2's acceptance test. Compares the materialised tree node for node, by vars()
and by a second traversal through get_nodes_by_type, over a 60-source corpus and
400 seeded random templates, in both lexer modes. Failures are compared as
failures, so the generator may produce illegal templates."
```

---

### Task 8: Milestone close-out

Same shape as M1's Task 7.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.3's M2 row)
- Create: `.review/m2-summary.md`

**Interfaces:** none. This task produces evidence.

- [ ] **Step 1: Full check**

```bash
source scripts/env.sh
scripts/check.sh 2>&1 | tail -30
echo "check exit=${PIPESTATUS[0]}"
```

Expected:
- `cargo test --workspace` green;
- `cargo fmt --all -- --check` clean;
- `cargo clippy --workspace --all-targets -- -D warnings` exit 0;
- the Python test modules pass, including `tests.test_materialise`,
  `tests.test_parser_errors` and `tests.test_parser_parity`;
- `scripts/differential.py` unchanged;
- the Django suite still reports `Ran 1547 tests … OK (skipped=6)`.

That last line is the *expected* result, not a disappointment: M2 adds a
`_native` entry point and a shell module that nothing imports yet, so the engine
is not in the suite's path (§10.1 — engine progress and integration progress
move independently). The suite's job here is to prove M2 broke nothing.

- [ ] **Step 2: The progress meter**

```bash
source scripts/env.sh
.venv/bin/python - <<'PY'
import sys
from pathlib import Path

root = Path(".").resolve()
sys.path.insert(0, str(root / ".django"))

from django_rust_templates import _shim

_shim.install()
remaining = _shim.unshimmed()
print(f"{len(remaining)} modules still Django's")
for name in remaining:
    print("  ", name)
PY
```

Expected: **25**, unchanged from M0 and M1.

Unchanged is correct, and worth stating why rather than leaving it looking like
no work: `_shim.unshimmed()` is `theirs - ours` over the module names *Django*
has, and M2 adds `_impl/parser.py` — a name Django does not have — while
replacing no Django module. The meter first moves at M3 (which brings a real
`base`/`defaulttags` slice) and M9 (the shell swap). `test_shim.py` already pins
that an `_impl` module Django *does* have wins; `parser.py` is not one.

- [ ] **Step 3: Amend §10.3's M2 row**

The row currently reads "Parse tree descriptor for every built-in tag;
syntax-error parity". Replace it with the narrowed claim this plan delivers, in
the same voice as the neighbouring rows:

```markdown
| M2 | Rust parser, node AST and descriptor format — `Parser`, `NodeList`,
`extend_nodelist`, the error protocol, tag dispatch, `_native.parse`, and the
materialisation of descriptors into real Django nodes. Compile functions for the
**nine** built-in tags that need no `FilterExpression`; syntax-error parity with
Django's parser. The other eighteen built-in tags are scheduled with their
dependencies (M3, M6, M7, M13) — see the M2 plan's scope note for the mapping. |
```

- [ ] **Step 4: Write `.review/m2-summary.md`**

The summary is what the reviewer reads instead of the diff, so it leads with the
claims and the evidence for each, and names what was *not* verified.

```markdown
# M2 — Rust parser and node AST

## What M2 claims

1. `_native.parse` builds the same tree Django's `Parser` does, for every
   template in a 60-source corpus and 400 seeded random templates, in both lexer
   modes — compared node for node after materialisation, by `vars()` and by a
   second traversal through `get_nodes_by_type`.
2. Syntax errors match Django's **message and token**, including which token wins
   when a compile error unwinds through nested `parse` frames.
3. The materialised tree is made of Django's real classes, children built before
   their parent and `token`/`origin` attached after, so M7's `ExtendsNode` will
   find a populated `blocks` mapping.
4. The descriptor format is general enough for the tags that arrive later — in
   particular `AttrValue::Ref`, which is proven by a stand-in and by
   `{% resetcycle %}`'s hand-populated registry path.

## Commits

| Commit | Task |
|---|---|
| … | Task 1 — the descriptor |
| … | Task 2 — the parser core |
| … | Task 3 — dispatch and the stand-ins |
| … | Task 4 — `_native.parse` and materialisation |
| … | Task 5 — the nine tag compile functions |
| … | Task 6 — syntax-error parity |
| … | Task 7 — descriptor parity |

## Evidence

| Check | Result |
|---|---|
| `cargo test -p template-core` | … passed |
| `cargo clippy --workspace --all-targets -- -D warnings` | exit 0 |
| `tests.test_parser_errors` | … |
| `tests.test_parser_parity` | … corpus sources × 2 modes, 0 mismatches |
| Randomised parity | 400 sources × 2 modes, 0 mismatches |
| `scripts/check.sh` (full) | `Ran 1547 tests … OK (skipped=6)` |
| Progress meter | 25 modules still Django's — unchanged, and expected to be |

### Guards made to fail, not just read

(Record the observed failure of each, as in M1's round-2 dispatch: the
prepended-terminator guard, the innermost-token guard, the per-nodelist
`contains_nontext` guard, the corpus-vs-implementation split, and the
`child_nodelists` traversal.)

## What I did not verify

- `{% resetcycle %}`'s **success** path is exercised only with a hand-populated
  registry, because `{% cycle %}` is M5. Its descriptor shape is therefore
  pinned by a unit test, not by a template.
- `render_text_only`'s non-text arm is unreachable at M2 and is tested directly
  instead.
- No timing. The parser is recursive and unbounded in nesting depth for a
  hostile template, exactly as Django's is; that is a deliberate match, not a
  measurement.

## Carried findings

**M1, fixed before M2 began: Python's whitespace table is not Rust's.** Python's
`str.isspace()` accepts U+001C–U+001F as well as Unicode `White_Space`; Rust's
`char::is_whitespace()` does not. The tables differ by exactly those four code
points (Python 29, Rust 25, checked exhaustively over U+0000..U+10FFFF), and M1
had shipped both a lexer that trimmed tag contents with the Rust table and a
`smart_split` whose pattern used Rust's `\s`. Django therefore accepted
`{% autoescape<U+001C>on %}` and this implementation rejected it.

Discovered while writing this plan. **Fixed** in M1 as commit `1a013ae`
(`pyunicode`, with the separators added to both parity corpora and to the
randomised alphabet; 13 parity failures before the fix, green after), rather
than pinned, because it is precisely the mechanism the differential tests
exist to check. Task 5's `python_whitespace_separators_split_arguments` pins
that the parser inherits the corrected table.

**M2: `_impl/parser.py` makes `django.template.parser` importable** — a name
Django does not have. Nothing imports it, and `_shim.unshimmed()` compares
source trees so the meter is unaffected, but if Django ever grows
`template/parser.py` this name would shadow it.

## What I want attacked

1. The wire form (this plan's "Wire form of the descriptor" section): nested
   tuples with native values and no discriminant. `bool`-before-`int` decoding
   is the hazard; is there a third case that needs its own wire class?
2. `py_repr_str` — a hand-rolled Python `repr` for two messages. Is the
   reachable-input argument airtight, and should the `{% resetcycle %}` message
   route through the host's `repr` instead?
3. Host-first dispatch order. It is argued from `add_library`'s
   last-registration-wins semantics; is that the right reading of Django's
   registry at M6?
4. The `Unimplemented` error for `{{ variable }}`. It makes M2 loud rather than
   wrong; does it make any M2 test vacuous?
5. Task 7's randomised pass compares *outcomes*, so illegal templates compare as
   failures. Is the alphabet too narrow to reach a real class of divergence?
```

- [ ] **Step 5: Dispatch to the reviewer**

`rusty-reviewer` is a peer session, not a subagent, so it is reached with
`SendMessage`. The dispatch must name the pinned tree, per the standing rule:

```
SendMessage(to="django-rust-templates-a8", message=…)
```

The message points at `.review/m2-summary.md`, the seven commits, and the four
"what I want attacked" items; it states that the review target is `.django/` at
**6.0.9** (an unpinned Django also exists on this machine, so an unqualified
"Django" is ambiguous), and it asks for executable findings — a command and its
expected output — rather than prose.

- [ ] **Step 6: Commit the close-out**

```bash
git add -A docs/superpowers/specs/ .review/ docs/superpowers/plans/
git commit -m "M2 close-out: amend §10.3's M2 row, add the milestone summary

Nine built-in tags at M2, with the other eighteen mapped to the milestones that
bring their dependencies. The summary carries M2's evidence, the guards that
were made to fail, and the two carried findings (the M1 whitespace divergence
and the _impl/parser.py namespace note)."
```

Per §11, the milestone is not *approved* until the review round returns APPROVE
or every blocker/major finding is resolved; this commit records the work and the
request, it does not close the round.

---

## Pre-execution validation

Everything a plan supplies must have been run before it is committed, because a
plan's snippets are only as checked as the crate they were extracted from — the
lesson recorded in M1, whose Task 3 and Task 5 shipped code that failed clippy
and whose Task 7 shipped a command that could not run.

Checked while writing this plan, in the pinned tree and this workspace:

1. **`#[cfg(test)]` on match arms compiles.** `builtin_tag`'s stand-in arms rely
   on it, and nothing else in this project does. Verified with a throwaway crate
   (`cargo test` green, and a non-test build with the arms absent).
2. **PyO3 0.29.3's conversion impls.** `IntoPyObject` is implemented for
   `&String` and for `bool`, `PyTuple::new` takes an `ExactSizeIterator`, and
   `PyErr::value(&self, py)` returns `&Bound<PyBaseException>` — so the
   `err.value(py).setattr(...)` idiom in `parse_error` is correct as written.
   Read from the vendored crate, not from memory.
3. **The nine node classes' constructor signatures**, read from
   `.django/django/template/defaulttags.py`: `AutoEscapeControlNode(setting,
   nodelist)`, `NowNode(format_string, asvar=None)`, `ResetCycleNode(node)`,
   `SpacelessNode(nodelist)`, `TemplateTagNode(tagtype)`,
   `VerbatimNode(content)`, and `CommentNode`/`CsrfTokenNode`/`DebugNode` with no
   `__init__`. `TemplateTagNode.mapping`'s eight keys are in the order the error
   message lists them.
4. **Django defines no `TextNode` subclass**, so `class_name != "TextNode"` in
   `extend_nodelist` is the same test as `isinstance(node, TextNode)`. Task 7
   pins the assumption so a Django that changes it fails loudly.
5. **Python's whitespace table differs from Rust's by exactly U+001C..U+001F**,
   checked exhaustively over U+0000..U+10FFFF in both directions. A finding
   about shipped M1 code: fixed there as commit `1a013ae` before M2 began, so
   this plan depends on the corrected table rather than documenting a
   divergence.
6. **`get_text_list`'s algorithm**, read from `django/utils/text.py:282` rather
   than from a running import: calling it requires `settings.USE_I18N`, so it
   raises `ImproperlyConfigured` on a bare interpreter. The two-item case has no
   comma.
7. **`_shim.unshimmed()` does not import anything** — it compares the two source
   trees — so `_impl/parser.py` cannot perturb the meter or trigger an import
   cycle from inside the shim.
