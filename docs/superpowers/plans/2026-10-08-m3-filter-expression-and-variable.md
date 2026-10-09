# M3 — `FilterExpression`, `Variable`, `smartif` and the first `_native.render`: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the engine the expression layer it has been missing — `FilterExpression`, `Variable`, the `smartif` parser and a primitive resolver — expose the first `_native.render`, and split the tag modules so the tag and filter fan-out can start.

**Architecture:** Rust keeps the parse and Python keeps the tree that renders (§5.5). M3 extends the descriptor so a `FilterExpression` can cross the wire: Rust parses `a.b|c:"x"` into a *filter chain* descriptor, and the Python shell materialises it into objects that hold the **real Python filter functions**, because `args_check` inspects a function's signature and only the host can see one. Resolving a filter therefore returns a handle (`AttrValue::Ref`), the mechanism §5.5 already reserves for host-owned values.

**Tech Stack:** Rust (edition 2021, `regex`, no Python dependency in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.2 (the seam), §5.5 (the shadow tree), §6 (ownership), §10.3, §10.4. This plan argues from the spec; where they disagree the spec wins and this plan is wrong.

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
- **Every snippet in this plan is executed against the pinned tree before the
  plan is committed, and a plan is not reviewable until it is** (§11). Where a
  step's evidence is a mutation, the mutation is *run* — M2 lost three plan
  steps to predictions about what an edit produces that nobody had made.
- **A task states its declared environment.** M2's defect rate was dominated by
  correct knowledge of Django combined with wrong assumptions about the harness.
  Each task below names what is constructible at that point, what imports
  resolve, and what the shim does and does not shadow.

## Review Focus

The five inputs and conditions the spec implies but no single task's tests
exercise. Each is pinned by a test in the task that owns the code.

1. **`\w` is not the same set in Python and Rust, and `filter_re` is built on
   it.** Rust's `\w` matches 2,642 code points Python's does not and misses 915
   Python's matches — 3,557 in all. Measured against the pinned tree, the
   divergence is user-visible in *both* directions: `{{ ² }}` renders `''` on
   Django (U+00B2 is `No`, a word character to Python) while `{{ á }}` built
   from `a`+U+0301 **raises** `Could not parse the remainder: '́' from 'á'`
   (U+0301 is `Mn`, a Rust word character but not a Python one). Python's `\w` is
   142,940 code points in 771 ranges; `[\p{L}\p{N}_]` reproduces it exactly.
   **Owned by Task 2**, which guards the class out of the *compiled* regex rather
   than out of a table the guard and the engine both read.
2. **`FilterExpression.args_check` inspects a Python function's signature.** A
   Rust-side check cannot reproduce it without reimplementing
   `inspect.getfullargspec`, decorator unwrapping and `lazy_annotations`. The
   check must run where the function is. **Owned by Task 6**, which calls the real
   `args_check` and pins its message (`cut requires 2 arguments, 1 provided`).
3. **`Variable._resolve_lookup`'s callable arm is M8's, and dropping it changes
   what resolves.** Django calls `current()` for any callable that is not
   `do_not_call_in_templates` or `alters_data`, and falls back to
   `string_if_invalid` on a signature mismatch. M3 must decide which arm it
   implements and make the unimplemented one *loud* rather than silently
   resolving to the object. **Owned by Task 7**, whose `LookupOutcome::Unimplemented`
   names M8. Also Task 7: the `@property` re-raise, which is the one arm whose
   omission turns a raised exception into a rendered `''` (measured).
4. **`string_if_invalid` is read off `context.template.engine`, so a resolver
   test that passes a bare dict tests the wrong thing.** Every M3 resolution
   test goes through a real `Context`, and rendering a *number* additionally
   needs `settings.configure()` — measured `ImproperlyConfigured` otherwise.
   **Owned by Tasks 6, 7 and 10.**
5. **`{% if %}` is M5; `smartif` is M3.** The machinery lands here and nothing
   calls it yet, so its tests must drive `IfParser` directly rather than through
   a template — and its `error_class` is `ValueError` for `IfParser` and
   `TemplateSyntaxError` for `TemplateIfParser`, which is the difference between
   a raised error and a rendered one. **Owned by Task 8.**

---

## Scope of M3

§10.3's M3 row: *"`FilterExpression`, `Variable`, `smartif`, primitive resolver
— First `_native.render`; text, variables, dotted/index lookup, filters"*. Read
literally that is still not "the tags", and the boundary is worth stating
because it is what makes M4 and M5 fan-out-able.

**M3 adds no new tags.** `{% if %}` is M5's (`smartif` is the machinery, not the
tag) and the 14 `compile_filter` tags §10.3's M2 note lists are M5's. What M3
adds is the thing those 14 tags were waiting for.

**M3 adds no new filters either, and that is deliberate.** §10.3's M4 row owns
"all 57 built-in filters" and §10.4 assigns `FilterFlags` to M4, so M3 proves
the filter *pipeline* with filters the host supplies, not with `defaultfilters`.
This is a change from the M2 plan's assumption that M3 would carry a few
filters, and it is the right one: implementing `default` here would mean
implementing `is_safe` here, which is precisely the flag round-trip §10.3
assigns to M4.

**The deliverable that matters is the layout**, because §10.4 makes fan-out
conditional on it: `parser/tags.rs` is 1,091 lines and nine tags share it, so
parallel agents would clobber each other. M3 delivers one file per tag and per
filter behind a registry written once, in full.

**M3's one deliberate deviation from the spec is recorded in Task 10**, with the
measurement that forces it: §10.4's `{% extends %}` ordering gate cannot be met
at M3 because `{% extends %}` is M7's, so M3 takes the half it can hold
(variable-versus-text and variable-versus-tag `contains_nontext`) and the
`{% extends %}`-specific case moves to M7.

## Design decisions this plan makes, and why

**D1 — A filter chain crosses the wire as a list, so `AttrValue` gains
containers.** §10.4 deferred this decision to M3 with the counterexample
recorded: a plain `AttrValue::List` cannot express `IfNode.conditions_nodelists`,
which is a list of `(condition, nodelist)` tuples whose second element is a
*nodelist*. There are three independent reasons it cannot, all measured:

1. the nodelist lives in `NodeDesc.nodelists`, not in an attribute value;
2. `IfNode` does **not** override `child_nodelists` — it inherits
   `('nodelist',)` from `Node` — while `nodelist` is a *computed property*
   (`return NodeList(self)`) that flattens every branch through `__iter__`, so
   the materialised `IfNode` must hold real Python `NodeList` objects *inside*
   the tuples or `get_nodes_by_type` cannot reach its children at all;
3. the other half of each tuple is a `smartif` AST node, not a
   `FilterExpression`.

§10.3 puts `if` in **M5**, so this lands in the same milestone as the container
decision and cannot be dodged by deferring the container. The resolution
separates the cases rather than widening the list:

- `AttrValue::List(Vec<AttrValue>)` for values that are genuinely lists of
  values — a filter chain's `(name, args)` entries, `ForNode.loopvars`,
  `CycleNode.cyclevars`.
- `AttrValue::Map(Vec<(String, AttrValue)>)` for `token_kwargs` results —
  `WithNode.extra_context`, `IncludeNode.extra_context`. Materialised as a real
  `dict` (Task 4), because every consumer wants a mapping and `dict` both
  preserves insertion order and reproduces `kwargs[key] = …`'s
  keep-first-position-take-last-value behaviour on a repeated key.
- **Nodelists inside a container stay out of `attrs`.** `AttrValue::NodeListRef(usize)`
  is an index into that `NodeDesc`'s existing `nodelists`, so
  `IfNode.conditions_nodelists` becomes
  `List[Tuple[Ref | None, NodeListRef(i)]]`. It resolves because
  `materialise_node` already builds children before the parent, so the index is
  populated by the time attributes decode — Task 4 adds the arm and a test that
  proves the ordering rather than assuming it.

`NodeListRef` is *needed* at M3 rather than speculated: a filter argument is a
`Variable` (a `Ref` or a literal) and the chain is a `List`, so `List` is load
bearing here; `Map` and `NodeListRef` exist for the M5 nodes named above. All
three get a materialiser arm and a test in the same commit, so none is a dead
decode arm — the failure mode this project has flagged twice.

**D2 — `FilterExpression` is materialised in Python, parsed in Rust.** Rust
cannot hold a Python function and `args_check` needs one. So Rust emits a
`FilterExpression` descriptor — the variable part plus an ordered list of
`(filter_name, args)` — and the shell's materialiser calls
`parser.find_filter(name)` and `FilterExpression.args_check` for real. This keeps
§5.5's ownership (Rust parses, Python owns the tree) and reuses the existing
`NODE_CLASSES` materialiser rather than building a second seam.

**D3 — the resolver is primitive, and its gaps are `Unimplemented`, not
silence.** `_resolve_lookup`'s callable arm (`alters_data`,
`do_not_call_in_templates`, `signature` probing) is M8's; M3 resolves
dictionary, attribute and list-index lookups. When a lookup lands on a callable,
M3 returns an `Unimplemented` error rather than returning the callable, because
returning it would render a `<function …>` — wrong and quiet — where Django
renders a value.

**D4 — `translate` is parsed and deferred.** `Variable.__init__` sets
`translate=True` for `_("…")`; `resolve` then calls `gettext_lazy`. i18n is M12's,
so M3 parses `_("…")` into the right descriptor and raises `Unimplemented` on
resolution, so a translated variable is a loud failure rather than an
untranslated one.

**D5 — `_native.render(source, context, parser)` takes the `Parser` from its
caller.** §10.3 names "first `_native.render`" as M3's done-when. That entry
cannot be a self-contained Rust function, and the reason is worth stating rather
than working around:

- Rust cannot materialise the tree. §6 and §5.5 give the node tree to Python,
  and materialising a `FilterExpression` calls `parser.find_filter(name)` and
  the real `FilterExpression.args_check` (D2) — the first needs a
  `django.template.base.Parser`, the second a Python function object.
- Building that `Parser` inside Rust would need to import Django and call
  `Engine().template_builtins` — which is a *host callback*, i.e. M6's `Host`
  trait. Reaching for it at M3 would mean inventing half of M6 to satisfy a
  milestone boundary, and the invented half would then be rewritten at M6.

So `_native.render` is a Rust `#[pyfunction]` that parses in Rust and then calls
into the shell's materialiser, and it takes the `Parser` as an argument. The
shell entry that supplies one is
`django_rust_templates._impl.engine.render_string(source, context)`, added in
Task 7, which constructs `Parser(tokens, builtins=Engine().template_builtins)`
and passes it down. Two consequences worth naming:

- **The parser argument is not a seam that outlives M3.** At M9 the shim
  interposes `base`, `Template.render` owns its parser, and `render_string`
  becomes a test-facing convenience rather than an entry point. Recording that
  here so the next milestone does not treat the argument list as designed.
- **It makes the honest statement about what M3's render test proves.** The
  *lookup* in a render case still runs through Django's `Variable.resolve`,
  because M9 has not interposed `base`. So a green `_native.render` is evidence
  about the Rust **parse**, not about Task 7's resolver. The resolver's own
  evidence is `_native.resolve`, driven directly. Task 7's Step 5 puts this in
  the test's docstring, where the next reader will actually meet it.

## File Structure

| File | Responsibility |
|---|---|
| `crates/template-core/src/parser/registry.rs` | **New.** Every built-in tag name → compile function, written once and complete. The one file no fan-out agent edits. |
| `crates/template-core/src/parser/tags/mod.rs` | **New.** Declares one submodule per built-in tag. Also written once, in full, and never edited by a fan-out agent. |
| `crates/template-core/src/parser/tags/<name>.rs` | **New.** One module per built-in tag; M2's nine move here unchanged. This is what a fan-out agent owns. |
| `crates/template-core/src/filters/mod.rs` | **New.** The filter registry. Empty of filters at M3; the directory exists so M4 adds files, not structure. |
| `crates/template-core/src/filters/<name>.rs` | **New.** One module per filter. None at M3. |
| `crates/template-core/src/parser/desc.rs` | **Modify.** `AttrValue::Float`, `BigInt`, `List`, `Map`, `NodeListRef`, `Variable` (Task 3), `FilterExpr` (Task 5). |
| `crates/template-core/src/pyregex.rs` | **New.** Python's `\w` as **two** position-named constants, guarded out of the *compiled* regex. |
| `crates/template-core/src/pyregex_word_ranges.txt` | **New.** The 771 inclusive ranges / 142,940 code points, `include_str!`-ed by the Rust guard and read by the Python parity test. |
| `scripts/gen_python_word_ranges.py` | **New.** Regenerates the table from the interpreter, with a `--check` mode and two pinned counts. |
| `crates/template-core/src/pynum.rs` | **New.** Python's `int()`/`float()` decimal grammar — whitespace, signs, `_` separators, non-ASCII `Nd` digits — which Rust's `parse` does not share. |
| `crates/template-core/src/parser/variable.rs` | **New.** `Variable.__init__`'s parsing: numbers, quoted literals, `_("…")`, the `lookups` split and its two error messages. |
| `crates/template-core/src/parser/filter_expr.rs` | **New.** `filter_re` and `FilterExpression.__init__`'s match loop, including its three error messages. |
| `crates/template-core/src/parser/smartif.rs` | **New.** `TokenBase`, `infix`, `prefix`, `Literal`, `EndToken`, `IfParser`. |
| `crates/template-core/src/parser/kwargs.rs` | **New.** `kwarg_re` and `token_kwargs`. |
| `crates/template-core/src/parser/resolve.rs` | **New.** The primitive resolver and its `LookupTarget` trait. |
| `crates/template-py/src/lib.rs` | **Modify.** `_native.builtin_tag_names` (Task 1); `_native.render` and `_native.resolve` (Task 7); the new `AttrValue` arms (Tasks 3, 4, 5); the `Variable` and `FilterExpr` pyclasses with their `_desc_to_py` helpers and `_native.parse_variable` / `_native.parse_filter_expression` (Tasks 3 and 5 — each ships with the parser that produces it). |
| `python/django_rust_templates/_impl/parser.py` | **Modify.** Materialisers for the new descriptors and the `NodeRef` callback. |
| `python/django_rust_templates/_impl/engine.py` | **New.** `render_string(source, context)` — the shell entry that constructs the `Parser` `_native.render` needs (D5). |
| `tests/pyregex_splices.txt`, `tests/test_pyregex_parity.py` | **New.** The class is compared against Django's *assembled* patterns, not against itself. |
| `tests/filter_expr_corpus.py`, `tests/test_filter_expr_parity.py` | **New.** |
| `tests/test_render_parity.py` | **New.** |
| `tests/parser_corpus.py` | **Modify.** The variable cases of §10.4's ordering gate, in the form M3 can hold — see Task 10. |

---

### Task 1: One module per tag, behind a registry written once

**Files:**
- Create: `crates/template-core/src/parser/registry.rs`
- Create: `crates/template-core/src/parser/tags/mod.rs`
- Create: `crates/template-core/src/parser/tags/autoescape.rs`, `comment.rs`, `csrf_token.rs`, `debug.rs`, `now.rs`, `resetcycle.rs`, `spaceless.rs`, `templatetag.rs`, `verbatim.rs` — M2's nine, **bodies moved unchanged**
- Create: 15 stubs: `tags/cycle.rs`, `filter.rs`, `firstof.rs`, `for.rs`, `if.rs`, `ifchanged.rs`, `lorem.rs`, `querystring.rs`, `regroup.rs`, `url.rs`, `widthratio.rs`, `with.rs` (M5), `tags/load.rs` (M6), `tags/partial.rs`, `tags/partialdef.rs` (M13)
- Create: `crates/template-core/src/filters/mod.rs`
- Delete: `crates/template-core/src/parser/tags.rs`
- Modify: `crates/template-core/src/parser/mod.rs` (module list and re-exports)
- Modify: `crates/template-py/src/lib.rs` (add `builtin_tag_names`)
- Test: `tests/test_tag_inventory.py` (extend)

**Interfaces:**
- Consumes: `crate::parser::{Parser, Error}`, `crate::parser::desc::NodeDesc`, `crate::token::Token`.
- Produces:
  - `template_core::parser::registry::CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>` (moved here from `tags.rs`)
  - `template_core::parser::registry::builtin_tag(&str) -> Option<CompileFn>`
  - `template_core::parser::registry::builtin_tag_names() -> Vec<&'static str>`
  - `template_core::parser::registry::unimplemented(name: &str, milestone: &str) -> Error`
  - `_native.builtin_tag_names() -> list[str]`
  - Each `parser::tags::<name>::compile(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>`

**Declared environment.** No new dependency and nothing Python-side is needed to build or test this task: `template-core` has no PyO3, so `cargo test -p template-core` compiles and runs the stubs, the registry and the tests below in a bare shell. The `_native.builtin_tag_names` binding added here needs `maturin develop` before the Python test in Step 10 runs — that is the one step that steps outside `cargo test`. `.django/` is readable and is where the Python test's Django side comes from. `parser::tags::py_repr_str`, `split_contents` and `strip_first_and_last` are **not** public and become `pub(crate)` in `tags/mod.rs`; nothing outside the crate reads them.

- [ ] **Step 1: Write the failing test for the registry's shape**

`crates/template-core/src/parser/registry.rs` is not written yet, so this compiles only after Step 3 — that is deliberate, and the first thing the registry must satisfy is its own invariant test.

```rust
// In `crates/template-core/src/parser/registry.rs`, module `tests`.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    #[test]
    fn the_registry_is_sorted_and_has_no_duplicates() {
        // The table is documented as ascending so that `builtin_tag`'s linear
        // scan is a scan over a known order, and so that a reader can find an
        // entry without reading all 24. A table that drifts out of order
        // silently breaks the second promise, so it is asserted.
        let names = builtin_tag_names();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted, "REGISTRY is not ascending by command");
        sorted.dedup();
        assert_eq!(sorted.len(), names.len(), "REGISTRY has a duplicate command");
    }

    #[test]
    fn the_registry_has_the_twenty_four_names_rust_owns() {
        // 27 built-in names minus `block`, `extends`, `include`. See the module
        // doc for why those three are absent and for the counting trap.
        assert_eq!(builtin_tag_names().len(), 24);
    }

    #[test]
    fn the_three_loader_tags_are_not_rust_compile_functions() {
        for absent in ["block", "extends", "include"] {
            assert!(
                builtin_tag(absent).is_none(),
                "{absent} must not be a Rust compile function (§6: Python-owned, M7)",
            );
        }
    }

    #[test]
    fn a_stub_names_its_milestone_and_says_it_is_the_engine_not_the_template() {
        assert_eq!(
            unimplemented("for", "M5").kind,
            ErrorKind::Unimplemented(
                "'for' is a built-in tag this engine does not implement yet (M5)".to_string(),
            ),
        );
    }
}
```

That second test needs a `Parser`, which a `registry.rs` unit test has no convenient way to build. Put it where the harness already exists — `crates/template-core/src/parser/mod.rs`'s `tests` module already has a `failure(source)` helper (used by `an_unregistered_tag_is_reported_with_its_line`), so the routing half of the claim goes there:

```rust
    #[test]
    fn an_unimplemented_builtin_says_so_rather_than_denying_the_tag_exists() {
        // The behaviour change this task makes. Before it, `{% for %}` fell
        // through to `invalid_block_tag` and reported Django's
        // "Did you forget to register or load this tag?" — a message that is
        // *false* for a built-in tag. Now it is an `Unimplemented` naming the
        // milestone that supplies it.
        let error = failure("{% for x in y %}{% endfor %}");
        assert_eq!(
            error.kind,
            ErrorKind::Unimplemented(
                "'for' is a built-in tag this engine does not implement yet (M5)".to_string(),
            ),
        );
        assert_eq!(error.token.unwrap().contents, "for x in y");
    }
```

- [ ] **Step 2: Run it and watch it fail**

Run: `source scripts/env.sh && cargo test -p template-core registry`
Expected: FAIL — `error[E0432]: unresolved import` / `cannot find module 'registry'`. Step 3 creates it.

- [ ] **Step 3: Write `registry.rs`**

```rust
//! The built-in tag registry: command name → compile function.
//!
//! Written **once, in full**, and never edited by a fan-out agent (§10.4). The
//! reason is mechanical rather than stylistic: under fan-out each agent owns one
//! `tags/<name>.rs`, and a registry that grew as they landed would be the single
//! shared file they all had to edit — exactly the clobbering this layout exists
//! to prevent. So from M3 onward every built-in tag Rust owns has an entry here,
//! and a tag that is not implemented yet is a stub in its own file rather than a
//! missing entry.
//!
//! **Three built-in names are deliberately absent**, and a test asserts their
//! absence: `block`, `extends` and `include`. §6 assigns their render-time logic
//! to Python, and M7 supplies them through the host seam; they are not Rust
//! compile functions at any milestone, so an entry here would be a promise this
//! project has already decided not to keep.
//!
//! The count is 24 = 27 built-ins minus those three, and the metric matters:
//! `Engine().template_builtins[0].tags` is 24 (`defaulttags` alone), while the
//! union across all three builtin libraries is 27 (`defaulttags` 24 +
//! `loader_tags` 3). Both are right answers to different questions, and this
//! project has lost time to that shape four times. `builtin_tag_names` is
//! cross-checked against Django's real registry by `tests/test_tag_inventory.py`
//! so the number cannot drift unnoticed.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::tags;
use crate::parser::Parser;
use crate::token::Token;

/// A tag's compile function: Django's `Parser.compile_func(self, token)`.
pub type CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>;

/// `(command, compile function)`, ascending by command.
///
/// One table rather than a `match` plus a separate name list: two spellings of
/// the same 24 names is a drift risk, and `builtin_tag_names` exists precisely
/// so a test can compare the set against Django's.
type Entry = (&'static str, CompileFn);

const REGISTRY: &[Entry] = &[
    ("autoescape", tags::autoescape::compile),
    ("comment", tags::comment::compile),
    ("csrf_token", tags::csrf_token::compile),
    ("cycle", tags::cycle::compile),
    ("debug", tags::debug::compile),
    ("filter", tags::filter::compile),
    ("firstof", tags::firstof::compile),
    ("for", tags::for_tag::compile),
    ("if", tags::if_tag::compile),
    ("ifchanged", tags::ifchanged::compile),
    ("load", tags::load::compile),
    ("lorem", tags::lorem::compile),
    ("now", tags::now::compile),
    ("partial", tags::partial::compile),
    ("partialdef", tags::partialdef::compile),
    ("querystring", tags::querystring::compile),
    ("regroup", tags::regroup::compile),
    ("resetcycle", tags::resetcycle::compile),
    ("spaceless", tags::spaceless::compile),
    ("templatetag", tags::templatetag::compile),
    ("url", tags::url::compile),
    ("verbatim", tags::verbatim::compile),
    ("widthratio", tags::widthratio::compile),
    ("with", tags::with::compile),
];

/// The compile function Rust has for `command`, if it has one.
///
/// A linear scan of 24 entries. M2's plan chose a `match` over a lazy `HashMap`
/// for the same reason — the table is small and read once per block tag, and a
/// `HashMap` behind a `OnceLock` would add a synchronisation point to the hot
/// path for no benefit.
pub fn builtin_tag(command: &str) -> Option<CompileFn> {
    REGISTRY
        .iter()
        .find(|(name, _)| *name == command)
        .map(|(_, compile)| *compile)
}

/// Every command name Rust owns a compile function for, ascending.
pub fn builtin_tag_names() -> Vec<&'static str> {
    REGISTRY.iter().map(|(name, _)| *name).collect()
}

/// The error a stub compile function returns.
///
/// An `Unimplemented` rather than a `Syntax`: the tag is real and Django would
/// compile it, so this is a statement about the engine, not a verdict on the
/// template. The message names the milestone so the failure is actionable.
pub fn unimplemented(name: &str, milestone: &str) -> Error {
    Error::unimplemented(format!(
        "'{name}' is a built-in tag this engine does not implement yet ({milestone})"
    ))
}
```

Two names in that table are **not** the command name: `for` and `if` are Rust keywords, so their modules are `tags::for_tag` and `tags::if_tag` (the module files are still `for.rs` and `if.rs`, declared as `pub mod for_tag;` in Step 5 — see the note there). Every other module is named after its command.

- [ ] **Step 4: Run it and watch it pass**

Run: `source scripts/env.sh && cargo test -p template-core registry`
Expected: PASS, 4 tests. Until Step 5 creates `tags/mod.rs` it does not compile, so run this after Step 5.

- [ ] **Step 5: Create `tags/mod.rs` and move the nine bodies**

Move `parser/tags.rs` to `parser/tags/mod.rs`, then split its nine compile functions into one file each, **bodies unchanged**. Three things move into `tags/mod.rs` and become `pub(crate)`: the shared helpers `split_contents`, `strip_first_and_last`, `py_repr_str`, `py_list_repr`, and the three `#[cfg(test)]` stand-ins plus the `#[cfg(test)]` `standin*` registry arm (Step 6 says where that arm goes).

`tags/mod.rs`:

```rust
//! One module per built-in tag (design spec §10.4).
//!
//! Written once, in full, and never edited by a fan-out agent: it is the file
//! every new tag would otherwise have to touch, and the layout exists so that no
//! two agents share one. The consequence is a real constraint worth stating —
//! **a helper needed by two tags lives here, not in either tag's file**, and it
//! is added by the central commit rather than by an agent.

pub mod autoescape;
pub mod comment;
pub mod csrf_token;
pub mod cycle;
pub mod debug;
pub mod filter;
pub mod firstof;
// `for` and `if` are Rust keywords, so the module name is `for_tag`/`if_tag`
// while the *file* keeps the command's name. `#[path]` is what lets the module
// name differ from the file name.
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

pub(crate) use crate::parser::registry::CompileFn;

// M2's helpers (`py_repr_str`, `py_list_repr`, `split_contents`, the `Error`
// plumbing) and the `#[cfg(test)]` stand-in nodes live below these
// declarations, moved from `tags.rs` unchanged — read them out of git, do not
// rewrite them. The nine compile functions do **not** live here: one per file,
// as `tags/<name>.rs`.
```

- [ ] **Step 6: Move the `#[cfg(test)]` stand-in arms into the registry**

M2's `builtin_tag` carried three `#[cfg(test)]` arms (`standin`, `standin_ref`, `standin_first`) that the parser's own tests dispatch through. They are not tags and must not become 24-entry table rows, so they stay a `match` in front of the table:

```rust
pub fn builtin_tag(command: &str) -> Option<CompileFn> {
    // Test-only stand-ins, still an arm rather than table rows: they are parser
    // machinery, not Django tags, and adding them to REGISTRY would put three
    // names in `builtin_tag_names` that have no counterpart in Django's
    // registry — which is exactly what the Python cross-check asserts against.
    #[cfg(test)]
    if let Some(compile) = tags::standin_tag(command) {
        return Some(compile);
    }
    REGISTRY
        .iter()
        .find(|(name, _)| *name == command)
        .map(|(_, compile)| *compile)
}
```

- [ ] **Step 7: Create the 15 stubs, and one file per stub**

Each stub file is **exactly** this shape, with the two substitutions in the trailing table. Shown in full for `cycle`:

`crates/template-core/src/parser/tags/cycle.rs`:

```rust
//! `{% cycle %}` — `defaulttags.do_cycle` (`defaulttags.py:1058`).
//!
//! M5. The body is a stub on purpose: the registry is written once and in full
//! (§10.4), so M5 replaces this file's body and edits no shared file.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::registry::unimplemented;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    Err(unimplemented("cycle", "M5"))
}
```

The substitutions, exhaustively — file stem, the command string passed to `unimplemented`, and the milestone:

| File | Command | Milestone |
|---|---|---|
| `tags/cycle.rs` | `"cycle"` | `"M5"` |
| `tags/filter.rs` | `"filter"` | `"M5"` |
| `tags/firstof.rs` | `"firstof"` | `"M5"` |
| `tags/for.rs` | `"for"` | `"M5"` |
| `tags/if.rs` | `"if"` | `"M5"` |
| `tags/ifchanged.rs` | `"ifchanged"` | `"M5"` |
| `tags/lorem.rs` | `"lorem"` | `"M5"` |
| `tags/querystring.rs` | `"querystring"` | `"M5"` |
| `tags/regroup.rs` | `"regroup"` | `"M5"` |
| `tags/url.rs` | `"url"` | `"M5"` |
| `tags/widthratio.rs` | `"widthratio"` | `"M5"` |
| `tags/with.rs` | `"with"` | `"M5"` |
| `tags/load.rs` | `"load"` | `"M6"` |
| `tags/partial.rs` | `"partial"` | `"M13"` |
| `tags/partialdef.rs` | `"partialdef"` | `"M13"` |

Note on `load`: it is a stub here because at M3 it *is* a built-in tag Rust has no compile function for. M6's design decision is whether `{% load %}` is a Rust compile function that calls a host `add_library`, or a host tag outright — since it mutates the registry the host owns (§5.2), host-owned is the likelier answer. If M6 concludes that, it **removes the entry** from `REGISTRY` and the file, which is a one-file edit by the agent who owns `load` rather than a fan-out conflict. Stubbing it now and deleting it later is cheaper than guessing now.

- [ ] **Step 8: Create `filters/mod.rs`**

```rust
//! One module per filter (design spec §10.4).
//!
//! M3 writes the directory and the registry and adds **no filters**: §10.3's M4
//! row owns all 57 built-in filters and §10.4 assigns `FilterFlags` to M4, so
//! M3 proves the filter *pipeline* with filters the host supplies. The point of
//! creating this now is that M4 adds files rather than structure.

/// Filter name → compile-time metadata, in the shape M4 fills in.
///
/// Empty at M3 and asserted empty, so that a filter added at M3 — which would
/// mean implementing `is_safe` outside its milestone — fails a test.
pub fn filter_names() -> Vec<&'static str> {
    Vec::new()
}
```

- [ ] **Step 9: Add `_native.builtin_tag_names`**

In `crates/template-py/src/lib.rs`:

```rust
/// Every built-in tag command Rust owns a compile function for, ascending.
///
/// Exposed because the cross-check lives where both languages exist: Django's
/// registry is only readable from Python, and a Rust test cannot see `.django`.
#[pyfunction]
fn builtin_tag_names() -> Vec<&'static str> {
    template_core::parser::registry::builtin_tag_names()
}
```

and register it in `#[pymodule]`:

```rust
    m.add_function(wrap_pyfunction!(builtin_tag_names, m)?)?;
```

Then rebuild: `source scripts/env.sh && maturin develop`.

- [ ] **Step 10: Extend `tests/test_tag_inventory.py` with the Rust-side cross-check**

```python
class RustRegistryTests(unittest.TestCase):
    """The Rust registry is exactly Django's built-ins minus the three §6 assigns to Python."""

    def test_the_rust_registry_matches_the_loader_tags_subtraction(self):
        from django_rust_templates import _native

        self.assertEqual(
            sorted(_native.builtin_tag_names()),
            sorted(REGISTERED - {"block", "extends", "include"}),
        )

    def test_the_three_loader_tags_are_absent_on_purpose(self):
        from django_rust_templates import _native

        for name in ("block", "extends", "include"):
            with self.subTest(name=name):
                self.assertNotIn(name, _native.builtin_tag_names())
```

`REGISTERED` is the 27-name set already defined in that module, so the assertion is against Django's real registry rather than a second hand-written list.

- [ ] **Step 11: Run everything**

Run: `source scripts/env.sh && cargo test -p template-core; echo "exit=$?"`
Expected: exit 0, 113 + new tests, 0 failed.

Run: `source scripts/env.sh && python -m unittest tests.test_tag_inventory -v`
Expected: PASS, including the two new tests.

Run: `source scripts/env.sh && cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"`
Expected: exit 0. `parser/tags/mod.rs` is a module directory, not a nested same-name module, so `clippy::module_inception` does not fire; check the output rather than assuming.

Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"`
Expected: exit 0, `Ran 1547 tests … OK (skipped=6)`. **This must be unchanged**: no corpus entry dispatches a stub tag (measured — the four `{% if %}` occurrences in `tests/parser_corpus.py` are all inside `{# #}`, `{% comment %}` or `{% verbatim %}`, where the lexer never produces a block token), and the randomised generator's `INLINE_TAGS`/`BLOCK_TAGS` contain only M2's nine.

- [ ] **Step 12: Commit**

```bash
git add crates/template-core/src/parser crates/template-py/src/lib.rs tests/test_tag_inventory.py
git commit -m "M3 task 1: one module per tag behind a registry written once"
```

---

### Task 2: Python's `\w`, as two spellings, guarded from the compiled class

**Files:**
- Create: `crates/template-core/src/pyregex.rs`
- Create: `crates/template-core/src/pyregex_word_ranges.txt` — generated data, 771 lines, 8,033 bytes
- Create: `scripts/gen_python_word_ranges.py`
- Create: `tests/pyregex_splices.txt` — generated fixture, 2 lines
- Create: `tests/test_pyregex_parity.py`
- Modify: `crates/template-core/src/lib.rs` — register `pub mod pyregex;`
- Test: the Rust tests live in `pyregex.rs`'s own `#[cfg(test)] mod tests`; the Python-side ones in `tests/test_pyregex_parity.py`

**Interfaces:**
- Consumes: nothing but the `regex` dependency already in `Cargo.toml` — resolved by `Cargo.lock` to `regex 1.13.1` / `regex-automata 0.4.18` / `regex-syntax 0.8.11`. No PyO3, no Python, no `.django` at build or `cargo test` time.
- Produces:
  - `template_core::pyregex::PYTHON_WORD_STANDALONE: &str` = `r"[\p{L}\p{N}_]"` — Python's `\w` as a **complete standalone atom**; the drop-in for a bare `\w` (`base.py:705`, `:1160`).
  - `template_core::pyregex::PYTHON_WORD_IN_CLASS: &str` = `r"\p{L}\p{N}_"` — Python's `\w` as **class contents**, for splicing inside a `[...]` the caller writes (`base.py:714`).

**This supersedes the single-constant sketch** (`pub const PYTHON_WORD: &str = r"\p{L}\p{N}_"`). One constant cannot serve both positions — Django uses `\w` both standalone and spliced inside a class, and the two mistakes are not symmetric — so there are two, each named for its position, and no `PYTHON_WORD` is defined at all: a stale reference to the sketch is a compile error rather than a silent pick of the dangerous form. Nothing in the repository referenced it (checked: the only hit is this plan's own file table), so the change is free. Task 3's `filter_expr.rs` uses `PYTHON_WORD_STANDALONE` for `(?P<filter_name>\w+)` and `PYTHON_WORD_IN_CLASS` for `var_chars`; Task 9's `kwargs.rs` uses `PYTHON_WORD_STANDALONE` for `kwarg_re`.

**Declared environment.**
- **Constructible now:** `template-core` builds, and `cargo test -p template-core pyregex` compiles and runs everything in `pyregex.rs` in a bare shell — that crate has no PyO3, so nothing here needs `maturin develop`. The `.txt` data file is `include_str!`-ed, so the Rust tests read it with no Python present. Django's own source is where the Python-side test gets its right-hand side; that test runs under `.venv/bin/python -I` (the system interpreter has no `asgiref`, so `import django.template.base` fails there — measured).
- **Not available yet:** `parser::filter_expr::filter_re` and `FilterExpression` are **Task 3's**, so there is no variable path: the two divergences cannot be observed through a template at this task, and `tests/parser_corpus.py` deliberately holds no `{{ … }}` at all. They are asserted on the class itself and on a `filter_re`-shaped leading-run match. `_native` exists (Task 1 added it) but this task adds **no** binding to it, so the Rust constants are mirrored as strings in the Python test and `pyregex.rs`'s own tests pin that those strings are the constants' values.

**Measured background — re-derived independently for this task, not inherited.**

Reproduced with a scratch crate at `/tmp/pyregex-verify/` depending on `regex = "=1.13.1"`, walking every code point U+0000..=U+10FFFF except the surrogates a `char` cannot hold, anchored `\A…\z` (a naive `^…$` would match a single `\n`, because `$` matches before a trailing newline — the code point whose answer matters). Python's side is `re.fullmatch(r"\w", chr(c))`, cross-checked against `chr(c).isalnum() or c == "_"`.

| Measurement | Value |
|---|---|
| `python -V` | 3.14.4; `unicodedata.unidata_version` = 16.0.0 |
| `regex` / `regex-syntax` (`Cargo.lock`) | 1.13.1 / 0.8.11; regex-syntax ships UCD 16.0.0 (its `unicode_tables/*.rs` headers say `ucd-16.0.0`) |
| Python `\w` (str) | **142,940** code points = **771** inclusive ranges; `re.fullmatch` vs `isalnum()` mismatches: **0** |
| Rust `\w` | **144,667**; byte-for-byte equal to `[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]` (measured equal) |
| Rust `\w` vs Python | Python-only **915** (all `No`); Rust-only **2,642** (`Mn` 2,020, `Mc` 468, `So` 130, `Me` 13, `Pc` 9, `Cf` 2) — 3,557 total |
| **Candidate `[\p{L}\p{N}_]`** | **142,940**; Python-only **0**, candidate-only **0** |
| `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]` (the earlier rejection) | 144,435; Python-only 0, class-only **1,495** (`Mn` 927, `Mc` 438, `So` 130) — **not** equal |

The user-visible divergences, both re-run against the pinned tree 6.0.9 with `Engine().from_string(…).render(Context({}))`:

```text
source: '{{ ² }}'                              render repr: ''
source: '{{ á }}'  (a + U+0301)
TemplateSyntaxError: "Could not parse the remainder: '́' from 'á'"
```

(`base.py:776-779`: `"Could not parse the remainder: '%s' from '%s'" % (token[upto:], token)`.) Read the class the way `filter_re` reads a variable — a leading run — and the two cases are exactly the two directions:

```text
U+00B2 alone        candidate consumes 1 char, remainder ""      Rust \w consumes 0, remainder "²"
"a" + U+0301        candidate consumes 1 char, remainder U+0301  Rust \w consumes 2, remainder ""
```

**Why two spellings.** Django's `\w` appears in three places in `base.py` (`filter_raw_string`, built at `:701-717`, contains all three): `(?P<var>[%(var_chars)s]+)` and `(?P<var_arg>[%(var_chars)s]+)` with `var_chars = r"\w\.\+-"` spliced **inside** a class, and `(?P<filter_name>\w+)` **standalone**. `kwarg_re` (`:1160`, Task 9's) is the third standalone use. Measured failure modes of getting the position wrong:

- Contents form used standalone is `\A\p{L}\p{N}_+\z` — "one letter, one number, then underscores" — which matches neither `upper` nor `a_`. Loud, and the first filtered expression fails.
- **The bracketed form spliced inside a class is the dangerous one.** `regex` supports nested character classes, so `[[\p{L}\p{N}_]\.\+-]+` **compiles** and matches *exactly the same code points* as `[\p{L}\p{N}_\.\+-]+` (measured, on `upper`, `a.b`, `x-y`, `²`, `á`). It is correct by accident, and the pattern is no longer Django's pattern. Nothing but a comparison of the pattern *source* can see it, which is why Step 3 asserts on the source and Step 3's last test proves the set-comparison cannot.

**Why the guard must compare *ranges of the compiled class* against a *generated table*, and why nothing else can.**
`char::is_whitespace` tracks the Rust toolchain, which moves only on a deliberate rustc upgrade. `\p{L}`/`\p{N}` track `regex-syntax`, which ships **its own copy of the UCD** — and `regex = "1"` lets a routine `cargo update` move it. Nothing in `scripts/check.sh` would notice. The guard is the only instrument that can: it derives the class from the same compiled `regex` the engine will compile `filter_re` from and compares it against Python's table, which is the one thing that can disagree. The differential corpus **cannot** be the backstop here — a UCD skew is visible only on code points whose assignment differs between two UCD versions, i.e. recently assigned characters, which no corpus contains.

**Why ranges and not a code-point list.** A list of the 142,940 code points is **1,805,848 bytes** of source; the complement is 1,114,296 and worse. The 771 inclusive ranges are **8,033 bytes — 225× smaller** and human-diffable, and a divergence reports `U+XXXX–U+YYYY` instead of one needle in 143k. It is a `.txt` data file `include_str!`-ed by the test, not a `const` in the module, so it stays data and both languages can read it by one path.

---

- [ ] **Step 1: Write the generator, and generate the table**

`scripts/gen_python_word_ranges.py`, in full:

```python
#!/usr/bin/env python3
"""Generate `crates/template-core/src/pyregex_word_ranges.txt`: Python's `\\w`.

Django builds `filter_re` on `\\w` (`django/template/base.py:705` and `:714`),
and Python's `\\w` for `str` patterns is exactly `str.isalnum() or '_'`. Both
spellings are checked against each other over every code point a `str` can
hold, so the generator's own correctness is two independent predicates agreeing
plus two pinned counts.

Why ranges and not code points: the set is 142,940 code points, which as a Rust
list is 1,805,848 bytes of source; the 771 inclusive ranges are 8,033 bytes
(225x smaller), and a divergence reads `U+XXXX-U+YYYY` rather than one needle
in 143k.

Usage:
    python -I scripts/gen_python_word_ranges.py           # write the file
    python -I scripts/gen_python_word_ranges.py --check   # verify it is current

`--check` exits 1 when the committed file and CPython disagree, which is what
notices a CPython `unicodedata` revision moving the table.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "crates" / "template-core" / "src" / "pyregex_word_ranges.txt"

EXPECTED_CODEPOINTS = 142_940
EXPECTED_RANGES = 771


def python_word_codepoints():
    """Python's `\\w` for `str`, with `re` and `isalnum()` cross-checked.

    A disagreement raises rather than being silently resolved: `\\w` is
    documented as `isalnum() or '_'`, so if CPython ever stops agreeing, the
    table the guard is built on is the question and the generator must say so.
    """
    word = re.compile(r"\w")
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        by_re = word.fullmatch(ch) is not None
        by_pred = ch.isalnum() or ch == "_"
        if by_re != by_pred:
            raise SystemExit(f"U+{cp:04X}: re.fullmatch disagrees with isalnum()")
        if by_re:
            yield cp


def to_ranges(codepoints):
    ranges = []
    for cp in codepoints:
        if ranges and ranges[-1][1] + 1 == cp:
            ranges[-1][1] = cp
        else:
            ranges.append([cp, cp])
    return ranges


def render(ranges):
    return "".join(f"{lo:X} {hi:X}\n" for lo, hi in ranges)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    ranges = to_ranges(python_word_codepoints())
    total = sum(hi - lo + 1 for lo, hi in ranges)
    if len(ranges) != EXPECTED_RANGES or total != EXPECTED_CODEPOINTS:
        raise SystemExit(
            f"the table moved: {len(ranges)} ranges (expected {EXPECTED_RANGES}), "
            f"{total} code points (expected {EXPECTED_CODEPOINTS})"
        )

    text = render(ranges)
    if args.check:
        if OUT.read_text() != text:
            raise SystemExit(f"{OUT} is stale; re-run without --check")
        print(f"{OUT.name} is current: {len(ranges)} ranges, {total} code points")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT}: {len(ranges)} ranges, {len(text)} bytes")


if __name__ == "__main__":
    main()
```

Run: `source scripts/env.sh && python -I scripts/gen_python_word_ranges.py`
Expected:

```text
wrote /…/crates/template-core/src/pyregex_word_ranges.txt: 771 ranges, 8033 bytes
```

The generator's own correctness is checked three ways: `re.fullmatch(r"\w", …)` and `str.isalnum() or '_'` agree over the whole range (0 disagreements — the generator raises otherwise); the two pinned counts are asserted before anything is written; and `--check` compares the committed file against a fresh derivation, so the Python-side test's "table is current" is the same code path. The file's first four lines are `30 39`, `41 5A`, `5F 5F`, `61 7A`.

- [ ] **Step 2: Write `pyregex.rs` with its tests, and register the module**

**The two constants are the naive `\w` port in this step, on purpose:** they are what a reader would write before reading this task, and Step 3 shows the tests fail against them. Step 4 corrects them.

`crates/template-core/src/pyregex.rs`, in full:

```rust
//! Python's `\w`, which is not Rust's.
//!
//! Django builds `filter_re` on `\w` (`django/template/base.py:705` `\w+` for a
//! filter name, `:714` `var_chars = r"\w\.\+-"` spliced into `[\w\.\+-]`, and
//! `:1160` `kwarg_re`), so the engine's `\w` must be Python's, not the `regex`
//! crate's.
//!
//! Python's `\w` for `str` patterns is exactly `str.isalnum() or '_'` — 142,940
//! code points, measured equal to `re.fullmatch(r"\w", c)` over the whole range
//! with zero disagreement. The `regex` crate's `\w` is
//! `[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]`, 144,667 code points:
//! 2,642 Rust-only (`Mn` 2,020, `Mc` 468, `So` 130, `Me` 13, `Pc` 9, `Cf` 2)
//! and 915 Python-only (all `No`). The candidate `\p{L}\p{N}_` is measured
//! byte-for-byte equal to Python's over U+0000..=U+10FFFF — zero in both
//! directions — while `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]` is not (1,495 too
//! many), which is why it is `\p{L}\p{N}` and not the `Alphabetic` spelling.
//!
//! The divergence is user-visible in both directions, measured on the pinned
//! checkout: `{{ ² }}` (U+00B2, `No`) renders `''` on Django but is not a Rust
//! `\w` character, and `{{ á }}` built from `a` + U+0301 (`Mn`) parses on Rust
//! but raises `Could not parse the remainder: '́' from 'á'` on Django.
//!
//! **Two spellings, because `\w` is used in two syntactic positions.** Django
//! writes a bare `\w` (`(?P<filter_name>\w+)`) and also splices `\w` *inside* a
//! class (`var_chars = r"\w\.\+-"` into `[%(var_chars)s]+`). The two mistakes
//! have opposite failure modes, and the bracketed one is the dangerous one:
//! the `regex` crate supports nested character classes, so `[[\p{L}\p{N}_]\.\+-]`
//! compiles and matches the same code points as `[\p{L}\p{N}_\.\+-]` — correct
//! by accident, and no longer Django's pattern. So each constant is named for
//! its position, and the guard asserts on the pattern *source*, because no
//! set-comparison can see the difference.
//!
//! This is the discipline of `crate::pyunicode::WS_CLASS`, which is likewise
//! class *contents* spliced as `[^{ws}'"]`, rather than a whole class.
//!
//! **The guard is the only thing that can see these tables move.** `\p{L}` and
//! `\p{N}` come from `regex-syntax`, which ships its own copy of the Unicode
//! Character Database (0.8.11 ships UCD 16.0.0, which is what CPython 3.14.4
//! reports too — so they agree *today*), versioned independently of CPython's
//! `unicodedata`. `regex = "1"` lets a routine `cargo update` move `regex-syntax`
//! and nothing in `scripts/check.sh` would notice; the differential corpus
//! cannot be the backstop, because a UCD skew is visible only on code points
//! whose assignment differs between two UCD versions — recently assigned
//! characters — which no corpus contains. The guard in this file walks the
//! *compiled* class and compares it to Python's table, which is the one thing
//! that can disagree.

/// Python's `\w` as a **standalone regular-expression atom** — a complete
/// character class, brackets included.
///
/// Use where Django writes a bare `\w`: `(?P<filter_name>\w+)`
/// (`base.py:705`) and `kwarg_re`'s `(\w+)=` (`base.py:1160`, Task 9's).
///
/// Do **not** splice this inside a `[...]`: `regex` accepts nested classes, so
/// `[[\p{L}\p{N}_]\.\+-]` compiles and matches the right set anyway, and the
/// pattern silently stops being Django's. Use [`PYTHON_WORD_IN_CLASS`] there.
pub const PYTHON_WORD_STANDALONE: &str = r"[\w]"; // Step 4: r"[\p{L}\p{N}_]"

/// Python's `\w` as the **contents of a character class**, for splicing inside a
/// `[...]` the caller writes.
///
/// This is the form for `var_chars = r"\w\.\+-"` (`base.py:714`); build it as
/// `format!(r"[{PYTHON_WORD_IN_CLASS}\.\+-]+")`. Used standalone it means "one
/// letter, one number, then underscores" and rejects `upper`, which is loud —
/// the safe direction to get wrong.
pub const PYTHON_WORD_IN_CLASS: &str = r"\w"; // Step 4: r"\p{L}\p{N}_"

#[cfg(test)]
mod tests {
    use super::*;
    use regex::Regex;

    /// An inclusive code-point range.
    ///
    /// A named alias rather than a bare `(u32, u32)`: `clippy::type_complexity`
    /// has already bitten this project once, and the alias is what makes the
    /// guard's signature readable.
    type WordRange = (u32, u32);

    /// Python's `\w`, as inclusive ranges, generated by
    /// `scripts/gen_python_word_ranges.py` from CPython 3.14.4 (UCD 16.0.0).
    ///
    /// A data file rather than a `const` in this module: 771 lines of `lo hi`
    /// hex, 8,033 bytes, versus 1,805,848 for the 142,940 code points.
    const PYTHON_WORD_RANGES_TEXT: &str = include_str!("pyregex_word_ranges.txt");

    /// The two Django `\w` positions, spliced with our forms, written by
    /// `tests/test_pyregex_parity.py` from the pinned tree's own
    /// `filter_raw_string`. A `cargo test` cannot read `.django`, so the
    /// right-hand side is derived on the Python side and read here.
    const SPLICES: &str = include_str!("../../../tests/pyregex_splices.txt");

    /// The table above, parsed.
    fn python_word_ranges() -> Vec<WordRange> {
        PYTHON_WORD_RANGES_TEXT
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let mut parts = line.split_whitespace();
                let lo = u32::from_str_radix(parts.next().expect("lo"), 16).expect("hex lo");
                let hi = u32::from_str_radix(parts.next().expect("hi"), 16).expect("hex hi");
                assert!(parts.next().is_none(), "trailing field on {line:?}");
                (lo, hi)
            })
            .collect()
    }

    /// The class as the `regex` crate actually compiles `pattern`, walked to
    /// canonical ascending, non-adjacent inclusive ranges.
    ///
    /// This is the point of the guard: `pattern` is built from the exported
    /// constants, so the code points come from the same `regex-syntax` tables
    /// the engine's `filter_re` will compile against.
    fn compiled_ranges(pattern: &str) -> Vec<WordRange> {
        let re = Regex::new(pattern).unwrap_or_else(|e| panic!("{pattern}: {e}"));
        let mut out: Vec<WordRange> = Vec::new();
        for cp in 0u32..=0x10FFFF {
            if (0xD800..=0xDFFF).contains(&cp) {
                continue;
            }
            let c = char::from_u32(cp).unwrap();
            let mut buf = [0u8; 4];
            if re.is_match(c.encode_utf8(&mut buf)) {
                match out.last_mut() {
                    Some(last) if last.1 + 1 == cp => last.1 = cp,
                    _ => out.push((cp, cp)),
                }
            }
        }
        out
    }

    /// The first difference between two range lists, or how they ended.
    fn first_diff(got: &[WordRange], expected: &[WordRange]) -> String {
        for (index, (g, e)) in got.iter().zip(expected).enumerate() {
            if g != e {
                return format!("index {index}: got {g:?}, expected {e:?}");
            }
        }
        format!(
            "equal over the common prefix; got {} ranges, expected {}",
            got.len(),
            expected.len()
        )
    }

    #[test]
    fn the_embedded_table_is_pythons_measured_word_set() {
        let ranges = python_word_ranges();
        assert_eq!(ranges.len(), 771, "range count");
        let total: u64 = ranges
            .iter()
            .map(|(lo, hi)| u64::from(hi - lo + 1))
            .sum();
        assert_eq!(total, 142_940, "code points");
        for pair in ranges.windows(2) {
            assert!(pair[0].1 + 1 < pair[1].0, "not canonical at {pair:?}");
        }
    }

    #[test]
    fn the_compiled_class_is_exactly_pythons_word_set() {
        // The one guard that can see `regex-syntax`'s UCD move under us. Both
        // spellings are walked, each wrapped into a complete one-code-point
        // pattern, so neither can drift from the table unnoticed.
        let expected = python_word_ranges();
        for (label, pattern) in [
            ("standalone", format!(r"\A{PYTHON_WORD_STANDALONE}\z")),
            ("in-class", format!(r"\A[{PYTHON_WORD_IN_CLASS}]\z")),
        ] {
            let got = compiled_ranges(&pattern);
            // `assert!` and not `assert_eq!`: on failure `assert_eq!` prints
            // both ~771-range vectors, which buries the one useful line.
            assert!(
                got == expected,
                "the regex crate's `{label}` class has diverged from Python's \
                 \\w: {}",
                first_diff(&got, &expected),
            );
        }
    }

    #[test]
    fn superscript_two_is_a_word_character() {
        // Django: `Template("{{ ² }}").render(Context({}))` == "". U+00B2 is
        // `No`, Python's `\w` matches it, Rust's does not.
        let ours = Regex::new(&format!(r"\A{PYTHON_WORD_STANDALONE}\z")).unwrap();
        assert!(ours.is_match("\u{00b2}"));
        assert!(
            !Regex::new(r"\A\w\z").unwrap().is_match("\u{00b2}"),
            "Rust's `\\w` does not match, which is why this is the failing-first case"
        );
    }

    #[test]
    fn combining_acute_is_not_a_word_character() {
        // Django: `Engine().from_string("{{ a\u{0301} }}")` raises
        // "Could not parse the remainder: '\u{0301}' from 'a\u{0301}'".
        // U+0301 is `Mn`, a Rust `\w` character but not a Python one.
        let ours = Regex::new(&format!(r"\A{PYTHON_WORD_STANDALONE}\z")).unwrap();
        assert!(!ours.is_match("\u{0301}"));
        assert!(
            Regex::new(r"\A\w\z").unwrap().is_match("\u{0301}"),
            "Rust's `\\w` matches, which is why this is the failing-first case"
        );
    }

    #[test]
    fn the_class_leaves_djangos_remainder() {
        // The same two characters, read the way `filter_re` reads a variable:
        // a leading run of the class. This is the observable consequence —
        // what is left over — which is what Django turns into the error.
        let re = Regex::new(&format!(r"\A[{PYTHON_WORD_IN_CLASS}]+")).unwrap();
        let m = re.find("\u{00b2}").expect("U+00B2 must match");
        assert_eq!(m.end(), "\u{00b2}".len());
        // `m.end()` is a byte offset, so slice on bytes; U+0301 is two bytes.
        let source = "a\u{0301}";
        let m = re.find(source).unwrap();
        assert_eq!(&source[m.end()..], "\u{0301}");
    }

    #[test]
    fn both_forms_splice_reproducing_djangos_pattern_sources() {
        let lines: Vec<&str> = SPLICES
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(str::trim)
            .collect();
        assert_eq!(lines.len(), 2, "fixture must hold exactly two atoms:\n{SPLICES}");
        let (label, atom) = lines[0].split_once(' ').expect("labelled fixture line");
        assert_eq!(label, "filter_name");
        assert_eq!(atom, format!("(?P<filter_name>{PYTHON_WORD_STANDALONE}+)"));
        let (label, atom) = lines[1].split_once(' ').expect("labelled fixture line");
        assert_eq!(label, "var_chars");
        assert_eq!(atom, format!(r"[{PYTHON_WORD_IN_CLASS}\.\+-]+"));
    }

    #[test]
    fn the_other_splice_would_be_silent() {
        // Why the assertion above is on the *source*. Splicing the standalone
        // form inside a class is accepted by `regex` and matches the same code
        // points, so only the string comparison can see it.
        let wrong_source = format!(r"\A[{PYTHON_WORD_STANDALONE}\.\+-]+\z");
        let right_source = format!(r"\A[{PYTHON_WORD_IN_CLASS}\.\+-]+\z");
        assert_ne!(wrong_source, right_source);
        let wrong = Regex::new(&wrong_source).expect("regex accepts nested classes");
        let right = Regex::new(&right_source).unwrap();
        for probe in ["upper", "a.b", "x-y", "\u{00b2}", "a\u{0301}"] {
            assert_eq!(
                wrong.is_match(probe),
                right.is_match(probe),
                "the wrong splice differs on {probe:?}",
            );
        }
    }
}
```

Register the module in `crates/template-core/src/lib.rs`, keeping the alphabetical order (`lexer`, `parser`, `pyregex`, `pyunicode`, `text`, `token`):

```rust
pub mod lexer;
pub mod parser;
pub mod pyregex;
pub mod pyunicode;
pub mod text;
pub mod token;
```

Before Step 4 the `include_str!("../../../tests/pyregex_splices.txt")` does not resolve, so run Step 3 *after* Step 6 creates that file — or, to see the red step sooner, temporarily point `SPLICES` at a file containing the two expected lines by hand. The measured run below was taken with the fixture present.

- [ ] **Step 3: Run it and watch it fail**

Run: `source scripts/env.sh && cargo test -p template-core pyregex`

Expected — five failures, all of them the divergences (the two that pass are the table's own sanity check and the nested-class demonstration, neither of which depends on the constants' value):

```text
---- tests::both_forms_splice_reproducing_djangos_pattern_sources stdout ----
assertion `left == right` failed
  left: "(?P<filter_name>[\\p{L}\\p{N}_]+)"
 right: "(?P<filter_name>[\\w]+)"
---- tests::superscript_two_is_a_word_character stdout ----
assertion failed: ours.is_match("\u{00b2}")
---- tests::the_class_leaves_djangos_remainder stdout ----
U+00B2 must match
---- tests::combining_acute_is_not_a_word_character stdout ----
assertion failed: !ours.is_match("\u{0301}")
---- tests::the_compiled_class_is_exactly_pythons_word_set stdout ----
the regex crate's `standalone` class has diverged from Python's \w: index 5: got (181, 181), expected (178, 179)
failures:
    tests::both_forms_splice_reproducing_djangos_pattern_sources
    tests::combining_acute_is_not_a_word_character
    tests::superscript_two_is_a_word_character
    tests::the_class_leaves_djangos_remainder
    tests::the_compiled_class_is_exactly_pythons_word_set
test result: FAILED. 2 passed; 5 failed; 0 ignored; 0 measured; 0 filtered out
```

The guard's first divergence, `got (181, 181)` vs `expected (178, 179)`, is U+00B2/U+00B3 — the superscripts, `No`, which Python has and Rust's `\w` lacks. That is divergence 1 named by the exhaustive check rather than by a hand-picked case.

- [ ] **Step 4: Correct the two constants**

The only change the tests ask for, and the whole point of the task:

```rust
pub const PYTHON_WORD_STANDALONE: &str = r"[\p{L}\p{N}_]";

pub const PYTHON_WORD_IN_CLASS: &str = r"\p{L}\p{N}_";
```

- [ ] **Step 5: Run it and watch it pass**

Run: `source scripts/env.sh && cargo test -p template-core pyregex`

Expected:

```text
running 7 tests
.......
test result: ok. 7 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.70s
```

The guard walks 1,100,000-odd code points twice; measured at **0.70 s** in a debug `cargo test` (no `opt-level` override), so it is not a reason to make it non-exhaustive.

- [ ] **Step 6: Write the Python side**

`tests/pyregex_splices.txt` — two labelled atoms, committed; the Rust test `include_str!`s it and the Python test proves it is what Django produces:

```text
filter_name (?P<filter_name>[\p{L}\p{N}_]+)
var_chars [\p{L}\p{N}_\.\+-]+
```

`tests/test_pyregex_parity.py`, in full:

```python
"""Python's `\\w`, pinned where both languages exist.

The Rust guard in `crates/template-core/src/pyregex.rs` compares the *compiled*
`regex` class against the range table, which is what catches a `regex-syntax`
UCD move — `regex = "1"` lets a routine `cargo update` change it, and nothing in
`scripts/check.sh` would notice. That guard cannot see CPython, and this file
cannot see `regex-syntax`, so each covers the side the other cannot.

This file re-derives Python's `\\w` from `str.isalnum()`, checks the committed
table against it (catching a CPython `unicodedata` revision moving the table),
and builds the two splice strings from Django's own `filter_raw_string` rather
than a transcription.
"""

import sys
import unicodedata
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
RANGES_PATH = (
    PROJECT_ROOT / "crates" / "template-core" / "src" / "pyregex_word_ranges.txt"
)
SPLICES_PATH = PROJECT_ROOT / "tests" / "pyregex_splices.txt"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import filter_raw_string  # noqa: E402

# The Rust spellings, as published by `template_core::pyregex`. Written here as
# strings because at this point in the plan there is no `_native` binding for
# them; `pyregex.rs`'s own tests pin that these are the constants' values.
PYTHON_WORD_STANDALONE = r"[\p{L}\p{N}_]"
PYTHON_WORD_IN_CLASS = r"\p{L}\p{N}_"


def measured_word_codepoints():
    """CPython's `\\w`, independently of the committed table."""
    return {
        cp
        for cp in range(0x110000)
        if not (0xD800 <= cp <= 0xDFFF) and (chr(cp).isalnum() or chr(cp) == "_")
    }


def committed_ranges():
    ranges = []
    for line in RANGES_PATH.read_text().splitlines():
        if line.strip():
            lo, hi = (int(part, 16) for part in line.split())
            ranges.append((lo, hi))
    return ranges


class WordTableTests(unittest.TestCase):
    def test_the_committed_table_is_cpythons_word_set(self):
        committed = set()
        for lo, hi in committed_ranges():
            committed.update(range(lo, hi + 1))
        self.assertEqual(
            sorted(committed ^ measured_word_codepoints()),
            [],
            "code points where the committed table and CPython disagree",
        )

    def test_the_measured_counts(self):
        ranges = committed_ranges()
        self.assertEqual(len(ranges), 771, "range count")
        self.assertEqual(
            sum(hi - lo + 1 for lo, hi in ranges), 142_940, "code point count"
        )
        # Pin what was compared, not just that something was. This fails when
        # CPython moves UCD, which is a deliberate re-measure, not a bug.
        self.assertEqual(unicodedata.unidata_version, "16.0.0")

    def test_the_table_is_canonical(self):
        ranges = committed_ranges()
        for (lo1, hi1), (lo2, _hi2) in zip(ranges, ranges[1:]):
            self.assertLess(hi1 + 1, lo2, f"not canonical near {(lo1, hi1)}")


class SpliceTests(unittest.TestCase):
    """Django's two `\\w` positions, spliced with our spellings.

    A `cargo test` cannot read `.django`, so the right-hand side is built here,
    from Django's own `filter_raw_string`, and written to `pyregex_splices.txt`
    for `pyregex.rs`'s splice test to read. That is what makes the Rust
    assertion compare against Django rather than against a transcription.
    """

    FILTER_NAME_ATOM = f"(?P<filter_name>{PYTHON_WORD_STANDALONE}+)"
    VAR_CHARS_ATOM = f"[{PYTHON_WORD_IN_CLASS}\\.\\+-]+"

    def test_each_form_splices_where_django_writes_a_bare_w(self):
        ours = filter_raw_string
        ours = ours.replace(r"(?P<filter_name>\w+)", self.FILTER_NAME_ATOM)
        ours = ours.replace(r"[\w\.\+-]+", self.VAR_CHARS_ATOM)

        # No bare `\w` survives: every one of Django's is now one of ours.
        self.assertNotIn(r"\w", ours)
        # Ours occur exactly as often as Django's did — 1 and 2, measured.
        self.assertEqual(ours.count(self.FILTER_NAME_ATOM), 1)
        self.assertEqual(ours.count(self.VAR_CHARS_ATOM), 2)

    def test_the_committed_fixture_is_what_django_produces(self):
        expected = (
            f"filter_name {self.FILTER_NAME_ATOM}\n"
            f"var_chars {self.VAR_CHARS_ATOM}\n"
        )
        self.assertEqual(SPLICES_PATH.read_text(), expected)


if __name__ == "__main__":
    unittest.main()
```

Run: `source scripts/env.sh && python -I scripts/gen_python_word_ranges.py --check`

Expected:

```text
pyregex_word_ranges.txt is current: 771 ranges, 142940 code points
```

Run: `source scripts/env.sh && .venv/bin/python -I -m unittest tests.test_pyregex_parity -v`

Expected:

```text
test_each_form_splices_where_django_writes_a_bare_w ... ok
test_the_committed_fixture_is_what_django_produces ... ok
test_the_committed_table_is_cpythons_word_set ... ok
test_the_measured_counts ... ok
test_the_table_is_canonical ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.099s

OK
```

The two sides of the splice assertion now meet: the Python test binds `FILTER_NAME_ATOM`/`VAR_CHARS_ATOM` to Django's real `filter_raw_string` and writes the fixture; the Rust test reads the fixture and checks that splicing the exported constants reproduces it character for character. Substituting our forms into Django's string leaves **no bare `\w`**, and the atoms occur exactly 1 and 2 times — the counts Django's own string has.

- [ ] **Step 7: Lint and format**

```bash
source scripts/env.sh
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m3t2.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m3t2.txt
```

Expected: `clippy exit=0` with no output. The lint is run unpiped so `$?` is clippy's, not `tail`'s — a `| tail` here reports the pager's status and has already hidden a failure once in this project. `WordRange` is a named alias rather than a bare `(u32, u32)` for `clippy::type_complexity`; the module is a single file `pyregex.rs` (not `pyregex/pyregex.rs`), so `clippy::module_inception` cannot fire.

- [ ] **Step 8: Commit**

```bash
git add crates/template-core/src/pyregex.rs \
        crates/template-core/src/pyregex_word_ranges.txt \
        crates/template-core/src/lib.rs \
        scripts/gen_python_word_ranges.py \
        tests/pyregex_splices.txt \
        tests/test_pyregex_parity.py
git commit -m "M3 task 2: Python's \\w, as two spellings, guarded from the compiled class

Python's \\w is isalnum() or '_' (142,940 code points); Rust's is 144,667 and
differs in both directions. Two constants, named for their syntactic position:
PYTHON_WORD_STANDALONE (a complete atom) and PYTHON_WORD_IN_CLASS (contents for
splicing). An exhaustive cargo guard walks both compiled spellings against a
generated 771-range table, and a Python test builds the two splice RHS strings
from Django's own filter_raw_string."
```

**Evidence for this task.** Every snippet above was executed before it was written here: the scratch crate `/tmp/pyregex-verify/` (regex 1.13.1) reproduced the counts and the zero-divergence result; a mirror of `pyregex.rs` produced the exact red output in Step 3 and the exact green output in Step 5, and passed `clippy --all-targets -- -D warnings` with exit 0; the generator and `tests/test_pyregex_parity.py` were run in a mirror of the repo layout under `.venv/bin/python -I` and both printed the outputs shown. The only difference between the mirror and the real tree is `include_str!`'s fixture path — the crate root sits one level shallower there, so the mirror used `"../tests/…"` where the plan uses `"../../../tests/…"`; that string is not exercised by any test output.

**What this task does not do.** It does not build `filter_re` (Task 3), add a `_native` binding, or touch `kwarg_re` (Task 9). It adds no corpus entry, because at this point there is no variable path for a corpus entry to reach — the divergences are pinned at the class, which is where they are decidable now and where `filter_re` will read them later. If a `regex` upgrade moves `regex-syntax`'s UCD, the guard fails and the fix is a re-measure, not an edit to `PYTHON_WORD_*`: `\p{L}\p{N}_` is Python's definition, and a crate whose tables disagree with CPython is the thing that is wrong.

---

### Task 3: Python's `int()`/`float()` grammar, and `Variable.__init__`'s parsing

**Files:**
- Create: `crates/template-core/src/pynum.rs`
- Create: `crates/template-core/src/parser/variable.rs`
- Modify: `crates/template-core/src/lib.rs` (`pub mod pynum;`)
- Modify: `crates/template-core/src/parser/mod.rs` (`pub mod variable;`)
- Modify: `crates/template-core/src/parser/desc.rs` (`AttrValue::Float`, `AttrValue::BigInt`, `VariableDesc`)
- Modify: `crates/template-py/src/lib.rs` (`AttrValue` arms, `decimal_digit_codepoints`, the `Variable` pyclass, `variable_desc_to_py`, `_native.parse_variable`)
- Modify: `crates/template-core/src/parser/mod.rs` (replace the `{{ x }}` gap test)
- Test: `tests/test_variable_parity.py` (**new**), `tests/test_pyunicode.py` or wherever the whitespace table guard lives (extend by analogy)

**Interfaces:**
- Consumes: `crate::pyunicode::is_space`, `crate::parser::error::{Error, ErrorKind}`.
- Produces:
  - `template_core::pynum::python_int(&str) -> Option<IntValue>` where `pub enum IntValue { Small(i64), Big(String) }`
  - `template_core::pynum::python_float(&str) -> Option<f64>`
  - `template_core::pynum::decimal_digit_value(char) -> Option<u32>`
  - `template_core::pynum::decimal_digit_codepoints() -> Vec<u32>`
  - `template_core::parser::desc::VariableDesc { var: String, literal: Option<AttrValue>, lookups: Option<Vec<String>>, translate: bool }`
  - `template_core::parser::variable::parse_variable(&str) -> Result<VariableDesc, Error>`
  - `template_core::parser::variable::unescape_string_literal(&str) -> Option<String>`
  - `AttrValue::Float(f64)`, `AttrValue::BigInt(String)`
  - `_native.decimal_digit_codepoints() -> list[int]`
  - `#[pyclass] Variable` — a `VariableDesc` as Python sees it, with `var`, `literal`, `lookups`, `translate` readable, plus `variable_desc_to_py(py, &VariableDesc) -> Py<PyAny>`
  - `_native.parse_variable(raw: str) -> Variable`

**Declared environment.** `template-core` still has one dependency (`regex`) and no PyO3, so everything but the two Python tests builds in a bare shell. `Variable.__init__`'s target is `.django/` (Django 6.0.9). The Python parity test in Step 8 compares against **Django's real `Variable`**, which exists and is importable — this task adds the *Rust* side, so the comparison has a real right-hand side from the first run. `FilterExpression` does **not** exist on the Rust side yet (Task 5), so nothing in this task may construct one; `Variable` is reachable on its own.

**This task owns the Python binding and the pyclass, not Task 7.** `#[pyclass] Variable`, `variable_desc_to_py` and `_native.parse_variable` are created here, because two later tasks consume them: Task 4's `attr_value_to_py` has an `AttrValue::Variable(v) => variable_desc_to_py(py, v)?` arm, and Task 6's materialiser dispatches on `isinstance(value, _native.Variable)`. Neither may declare it — a `#[pyclass]` declared twice does not link — and Task 7 only *re-exports* nothing: it adds `render` and `resolve`, which are its own. An earlier draft of this plan listed all four bindings under Task 7, which would have left Task 6's materialiser tests calling a name that no task creates.

#### The specification: `Variable.__init__`, measured

Every row below was produced by running Django 6.0.9's own `Variable` on the pinned tree. The right-hand columns are `Variable.__dict__`-equivalent slot values, and they *are* the contract:

| input | `literal` | `lookups` | `translate` |
|---|---|---|---|
| `abc` | `None` | `('abc',)` | `False` |
| `a.b.c` | `None` | `('a', 'b', 'c')` | `False` |
| `0x10` | `None` | `('0x10',)` | `False` |
| `.` | `None` | `('', '')` | `False` |
| `..` | `None` | `('', '', '')` | `False` |
| `5.5.5` | `None` | `('5', '5', '5')` | `False` |
| `5_` | `None` | `('5_',)` | `False` |
| `1__0` | `None` | `('1__0',)` | `False` |
| `a.b\|default:"x"` | `None` | `('a', 'b\|default:"x"')` | `False` |
| `""` | `SafeString('')` | `None` | `False` |
| `"quoted"` | `SafeString('quoted')` | `None` | `False` |
| `'q'` | `SafeString('q')` | `None` | `False` |
| `_("x")` | `SafeString('x')` | `None` | `True` |
| `_( "x" )` | `None` | `(' "x" ',)` | `True` |
| `5` / ` 5 ` / `+5` / `5\n` | `5` | `None` | `False` |
| `-5` | `-5` | `None` | `False` |
| `١٢٣` (U+0661…) | `123` | `None` | `False` |
| `1_000` | `1000` | `None` | `False` |
| `9223372036854775807` | `9223372036854775807` | `None` | `False` |
| `9223372036854775808` | `9223372036854775808` | `None` | `False` |
| `99999999999999999999` | `99999999999999999999` | `None` | `False` |
| `1.5` / `.5` / `1e5` / `1E5` / `1.5e3` | `1.5` / `0.5` / `100000.0` / `100000.0` / `1500.0` | `None` | `False` |
| `1e400` | `inf` | `None` | `False` |
| **`2.`** | **`2.0`** | **`('2', '')`** | `False` |

Three rows carry a claim that is not obvious and each is a hazard:

- **`2.` has a literal *and* lookups.** `Variable.__init__` assigns `self.literal = float(var)` and *then* finds `var[-1] == "."` and raises `ValueError`, which the outer handler catches. The assignment is already done, so the literal survives alongside the lookups it then computes. `resolve` branches on `lookups is not None`, so the value is unreachable — but `Variable.literal` is an observable slot, and a materialiser that built the object from scratch would produce a different object from Django's. The Rust port must assign in the same order, and one test pins it.
- **`9223372036854775808` and `99999999999999999999` are valid ints.** Python's `int` is arbitrary-precision; Rust's `i64` is not. `AttrValue::BigInt` carries the canonical decimal text for values that do not fit, and the materialiser calls `int()` on it.
- **` 5 `, `1_000` and `١٢٣` are all valid ints, and none of them is what Rust's `str::parse::<i64>()` accepts.** Rust's parse rejects surrounding whitespace, rejects `_` separators, and `char::to_digit` is ASCII-only, so no Rust std call can classify `١` as a digit at all. This is why `pynum.rs` exists rather than a `.parse()` call: it is Python's grammar, ported.

Measured grammar facts for the port, all against the pinned interpreter:

| expression | result |
|---|---|
| `int('١_٢')` | `12` |
| `int('+١٢')`, `int(' ١٢ ')` | `12`, `12` |
| `int('٥.٥')` | `ValueError` (but `float('٥.٥')` is `5.5`) |
| `int('١e٢')` | `ValueError` (but `float('١e٢')` is `100.0`) |
| `float('1_e5')`, `float('1e_5')` | `ValueError` both |
| `unicodedata.unidata_version` | `16.0.0` |
| `Nd` code points | `760` |
| `Nd` contiguous ranges | `71` |

- [ ] **Step 1: Write the failing test for the Nd table**

`crates/template-core/src/pynum.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_decimal_digit_table_is_contiguous_ranges_not_a_flat_list() {
        // 760 code points in 71 ranges. The shape is asserted because the guard
        // that compares it against Python compares ranges, and a table that
        // silently became a flat list would still pass that comparison.
        assert_eq!(DECIMAL_DIGIT_RANGES.len(), 71);
        let total: u32 = DECIMAL_DIGIT_RANGES.iter().map(|(a, b)| b - a + 1).sum();
        assert_eq!(total, 760);
        assert!(DECIMAL_DIGIT_RANGES.windows(2).all(|w| w[0].1 < w[1].0), "ranges overlap or are unsorted");
        // A range is a *contiguous run of Nd code points*, not one digit
        // alphabet. Two of the 71 hold several cycles; see the test below.
        assert_eq!(
            DECIMAL_DIGIT_RANGES.iter().filter(|(a, b)| b - a > 9).count(),
            2,
            "the two fused runs are what make `code - first` wrong on its own"
        );
    }

    #[test]
    fn ascii_digits_and_the_non_ascii_ones_python_calls_digits() {
        for c in '0'..='9' {
            assert_eq!(decimal_digit_value(c), Some(c as u32 - '0' as u32));
        }
        // U+0661 ARABIC-INDIC DIGIT ONE — `int('١٢٣') == 123` on the pinned tree.
        assert_eq!(decimal_digit_value('\u{0661}'), Some(1));
        // U+00B2 SUPERSCRIPT TWO is `No`, not `Nd`: `int('²')` is a ValueError
        // in Python and must therefore not be a digit here.
        assert_eq!(decimal_digit_value('\u{00b2}'), None);
        // U+0301 COMBINING ACUTE ACCENT is `Mn`. It is not a digit, and it is
        // also not rejected by the *variable* grammar — see Task 2's class.
        assert_eq!(decimal_digit_value('\u{0301}'), None);
    }

    #[test]
    fn python_int_accepts_what_rust_parse_does_not() {
        assert_eq!(python_int(" 5 "), Some(IntValue::Small(5)));
        assert_eq!(python_int("5\n"), Some(IntValue::Small(5)));
        assert_eq!(python_int("+5"), Some(IntValue::Small(5)));
        assert_eq!(python_int("-5"), Some(IntValue::Small(-5)));
        assert_eq!(python_int("1_000"), Some(IntValue::Small(1000)));
        assert_eq!(python_int("\u{0661}\u{0662}\u{0663}"), Some(IntValue::Small(123)));
        assert_eq!(python_int("\u{0661}_\u{0662}"), Some(IntValue::Small(12)));
    }

    #[test]
    fn python_int_rejects_what_python_rejects() {
        for bad in ["_5", "5_", "1__0", "0x10", "", "5.5", "\u{0665}.\u{0665}", "1e5"] {
            assert_eq!(python_int(bad), None, "python_int({bad:?})");
        }
    }

    #[test]
    fn ints_past_i64_keep_the_digits_rather_than_saturating() {
        assert_eq!(
            python_int("9223372036854775807"),
            Some(IntValue::Small(9223372036854775807)),
        );
        assert_eq!(
            python_int("9223372036854775808"),
            Some(IntValue::Big("9223372036854775808".to_string())),
        );
        assert_eq!(
            python_int("99999999999999999999"),
            Some(IntValue::Big("99999999999999999999".to_string())),
        );
        // Whitespace and underscores are normalised away, because the shell
        // hands the text to Python's `int()` and Python accepts either — but a
        // canonical form is what the wire carries, so the test says which.
        assert_eq!(python_int(" 1_000 "), Some(IntValue::Small(1000)));
    }

    #[test]
    fn python_float_matches_python_including_infinity_and_the_underscore_rules() {
        assert_eq!(python_float("1.5"), Some(1.5));
        assert_eq!(python_float(".5"), Some(0.5));
        assert_eq!(python_float("1e5"), Some(100000.0));
        assert_eq!(python_float("1.5e3"), Some(1500.0));
        assert_eq!(python_float("1e400"), Some(f64::INFINITY));
        assert_eq!(python_float("\u{0665}.\u{0665}"), Some(5.5));
        assert_eq!(python_float("\u{0661}e\u{0662}"), Some(100.0));
        // The two underscore forms Python rejects even though the digits are
        // fine on both sides: `float('1_e5')` and `float('1e_5')`.
        assert_eq!(python_float("1_e5"), None);
        assert_eq!(python_float("1e_5"), None);
        assert_eq!(python_float("abc"), None);
    }
}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `source scripts/env.sh && cargo test -p template-core pynum`
Expected: FAIL to compile — `cannot find module 'pynum'` until Step 3, and `DECIMAL_DIGIT_RANGES` is undeclared. This step is the evidence that the test is not passing by accident; if it compiles and passes before Step 3, the module is already there and something is wrong.

- [ ] **Step 3: Write `pynum.rs`**

```rust
//! Python's `int()` and `float()` grammar for base-10 text.
//!
//! Rust's `str::parse::<i64>()` is not the same function. Python accepts
//! surrounding whitespace (`int(" 5 ")`), `_` between digits (`int("1_000")`),
//! and — the part no Rust std call can do — **non-ASCII decimal digits**:
//! `int("١٢٣")` is `123` on the pinned tree, and `char::to_digit` is ASCII-only,
//! so `\p{Nd}` needs a table.
//!
//! The table is 760 code points in 71 contiguous ranges, extracted from the
//! pinned CPython's `unicodedata` and pinned by a Python-side guard that
//! compares it exhaustively (Step 8) — the same instrument `pyunicode.rs` uses
//! for the whitespace table, and for the same reason: neither language's table
//! is visible from the other, so only a comparison that runs in both can see one
//! of them move. There are now two such tables and a `cargo update` or a Python
//! upgrade moves either.

/// An integer that may not fit Rust's `i64`.
///
/// Python's `int` is arbitrary-precision, so `{{ 99999999999999999999 }}` is a
/// valid template that renders its own digits. `Big` carries the canonical
/// decimal text and the shell calls `int()` on it; saturating to `i64::MAX`
/// would render a wrong number, which is the failure mode this type exists to
/// prevent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IntValue {
    Small(i64),
    Big(String),
}

/// `\p{Nd}` as `(first, last)` inclusive ranges, ascending and disjoint.
static DECIMAL_DIGIT_RANGES: &[(u32, u32)] = &[
    (0x0030, 0x0039), // ASCII 0-9
    // ... 70 more, generated as in Step 3a ...
];

/// The value of `c` as a decimal digit, if Python's `int()` would treat it as one.
///
/// `% 10` and not `code - first`: a range is a contiguous run of `Nd` code
/// points, and two of the 71 runs are several digit alphabets fused by
/// adjacency — `U+1D7CE..U+1D7FF` is five of them. Measured for all 760 code
/// points; see Step 3a.
///
/// `char::to_digit` is not usable here for the reason the table exists: it
/// knows only ASCII (and, at 10 and above, letters).
pub fn decimal_digit_value(c: char) -> Option<u32> {
    let code = c as u32;
    DECIMAL_DIGIT_RANGES
        .iter()
        .find(|(first, last)| (*first..=*last).contains(&code))
        .map(|(first, _)| (code - first) % 10)
}
```

- [ ] **Step 3a: Generate the table from the pinned interpreter, and re-measure the value rule**

The body above is written from a measurement already taken, so this step is not
where the rule gets decided — it is where the **71 literals get produced** and
where the rule is re-derived, so that a table copied from a plan is not trusted
over the interpreter.

Run this with `python -I`; it prints a Rust source fragment to a scratch file,
not into the repository:

```python
import unicodedata

# Split on *contiguity only*. Splitting on "the value also increments by one"
# as well looks equivalent and is not: it cuts the two fused runs below into
# their constituent alphabets and reports 76 ranges where the table has 71.
ranges, start, prev = [], None, None
for c in range(0x110000):
    if unicodedata.category(chr(c)) == "Nd":
        if prev is None or c != prev + 1:
            if start is not None:
                ranges.append((start, prev))
            start = c
        prev = c
if start is not None:
    ranges.append((start, prev))

# The value rule, measured rather than assumed — and asserted for every one of
# the 760 code points, which is the whole point of the exercise.
wrong = [
    (c, unicodedata.decimal(chr(c)), (c - first) % 10)
    for first, last in ranges
    for c in range(first, last + 1)
    if unicodedata.decimal(chr(c)) != (c - first) % 10
]

for first, last in ranges:
    print(f"    (0x{first:04X}, 0x{last:04X}),")
print("ranges:", len(ranges))
print("codepoints:", sum(last - first + 1 for first, last in ranges))
print("all ranges start at decimal 0:",
      all(unicodedata.decimal(chr(first)) == 0 for first, _ in ranges))
print("ranges longer than ten:",
      [(hex(a), hex(b), b - a + 1) for a, b in ranges if b - a > 9])
print("code points where (cp - first) % 10 != decimal:", len(wrong), wrong[:5])
```

**Expected output, and the measurement this plan was written against:**

```
ranges: 71
codepoints: 760
all ranges start at decimal 0: True
ranges longer than ten: [('0x116d0', '0x116e3', 20), ('0x1d7ce', '0x1d7ff', 50)]
code points where (cp - first) % 10 != decimal: 0 []
```

`unidata_version` is **16.0.0** on the pinned interpreter. If any line differs, the
table is not the table above and the `% 10` is not licensed — record what was
measured, do not adjust the assertion to fit.


The measured basis for the `% 10`, since the plan asserts the rule and the
generator above is what checks it. The obvious formula — `value = code - first`,
which is what a table of "digit blocks" invites — is wrong, and the sweep is what
says so:

| Measurement | Result |
|---|---|
| `Nd` code points | **760** |
| contiguous ranges | **71** |
| ranges whose first code point is decimal `0` | all **71** |
| code points where `decimal(cp) == (cp - range_start) % 10` | all **760**, no exceptions |
| ranges longer than 10 | **exactly two** |

So the offset-from-start shorthand is true *only modulo ten*. The two long ranges
are the trap, and neither is exotic:

- `U+116D0..U+116E3` — **20** code points: two ten-digit cycles fused because
  their blocks are adjacent.
- `U+1D7CE..U+1D7FF` — **50** code points: *five*, the five mathematical digit
  alphabets (bold, double-struck, sans-serif, sans-serif-bold, monospace).

A table of `(first, last)` pairs with `value = code - first` therefore produces
values `10..19` from `U+116DA` onward and `10..49` from `U+1D7D8` onward — a
**wrong digit from a wrong-but-plausible table**, silently, for **50** of the 760
code points (the first ten of each long run are right by luck). `int('𝟘')` is
`0`, not `10`. The body is therefore:

```rust
    DECIMAL_DIGIT_RANGES
        .iter()
        .find(|(first, last)| (*first..=*last).contains(&code))
        .map(|(first, _)| (code - first) % 10)
```

and a test that pins the two long ranges by name, because they are the only
inputs that distinguish it from `code - first`:

```rust
    #[test]
    fn a_range_may_hold_several_digit_cycles() {
        // Measured, not recalled: 71 ranges, and exactly two of them are longer
        // than ten. `U+1D7CE..U+1D7FF` is five mathematical digit alphabets
        // fused by adjacency, so `code - first` alone reads `U+1D7D8` as 10.
        assert_eq!(decimal_digit_value('\u{116d0}'), Some(0));
        assert_eq!(decimal_digit_value('\u{116da}'), Some(0), "second cycle of the Modi run");
        assert_eq!(decimal_digit_value('\u{1d7ce}'), Some(0));
        assert_eq!(decimal_digit_value('\u{1d7d8}'), Some(1), "second alphabet's '1'");
        assert_eq!(decimal_digit_value('\u{1d7ff}'), Some(9), "fifth alphabet's '9'");

        let all: String = (0x1d7ceu32..0x1d800)
            .map(|c| char::from_u32(c).unwrap())
            .map(|c| char::from_digit(decimal_digit_value(c).unwrap(), 10).unwrap())
            .collect();
        assert_eq!(all, "0123456789".repeat(5));
    }
```

- [ ] **Step 4: Implement `python_int` and `python_float`**

```rust
/// Python's `int(s)` for base 10, as `Some(value)` or `None` where Python
/// raises `ValueError`.
///
/// The grammar, measured rather than recalled: optional surrounding whitespace
/// (Python's `str.isspace()` table — `pyunicode::is_space`, which is *not*
/// Rust's), optional `+`/`-`, then digits separated by at most one `_` with no
/// leading, trailing or doubled `_`.
/// Two forms, because the value may not fit: Python integers are unbounded and
/// `Variable('99999999999999999999')` is a valid literal on the pinned tree.
/// `Small` is the common case and allocates nothing; `Big` carries canonical
/// ASCII digits so the materialiser can hand them to `int()` — never to `eval`,
/// and never by formatting a source string and evaluating it.
pub fn python_int(s: &str) -> Option<IntValue> {
    let core = strip_python_whitespace(s);
    let (negative, digits) = split_sign(core);
    let digits = drop_underscores(digits)?;
    if digits.is_empty() {
        return None;
    }

    // Accumulate in `i128` and fall to the text form on overflow. The length is
    // deliberately not tested first: leading zeros make it a poor predictor
    // (`'000000000000000000005'` is `5`), and the boundary this code has is the
    // one Task 3's parity corpus measures.
    let mut magnitude: i128 = 0;
    let mut too_big = false;
    for c in digits.chars() {
        let digit = decimal_digit_value(c)? as i128;
        match magnitude.checked_mul(10).and_then(|v| v.checked_add(digit)) {
            Some(v) => magnitude = v,
            None => {
                too_big = true;
                break;
            }
        }
    }
    if !too_big {
        let signed = if negative { -magnitude } else { magnitude };
        if let Ok(small) = i64::try_from(signed) {
            return Some(IntValue::Small(small));
        }
    }

    // Canonical form: an ASCII `-` if negative, then ASCII digits, no
    // underscores. Non-ASCII digits are transliterated, which is safe because
    // this string is only ever fed to `int()`: what the materialiser builds is
    // the *value*, not the literal's spelling.
    let mut text = String::with_capacity(digits.len() + 1);
    if negative {
        text.push('-');
    }
    for c in digits.chars() {
        text.push(char::from_digit(decimal_digit_value(c)?, 10).expect("0..=9"));
    }
    Some(IntValue::Big(text))
}

/// Python's `float(s)`, as `Some(value)` or `None` where Python raises
/// `ValueError`.
///
/// Reached only when the token contains `.` or `e`/`E` (`Variable.__init__`'s
/// own condition), which is why `float("inf")` and `float("nan")` are
/// unreachable in a template and are not special-cased here — the `e` in
/// `"infinity"`… is not present, so `"inf"`, `"nan"` and `"Infinity"` all take
/// the *integer* branch and fail there, measured.
pub fn python_float(s: &str) -> Option<f64> {
    let core = strip_python_whitespace(s);
    let (negative, body) = split_sign(core);
    let canonical = canonical_float_digits(body)?;
    let signed = if negative {
        format!("-{canonical}")
    } else {
        canonical
    };
    // Rust's parser converts an already-validated ASCII decimal string. It never
    // makes the *decision*: `f64::from_str` accepts `"inf"`/`"NaN"` spellings,
    // and accepting them here would be accepting something the caller's `e`/`.`
    // precondition already excluded.
    signed.parse::<f64>().ok()
}

/// Python's surrounding whitespace: `str.isspace()`, which is **not** Rust's
/// `char::is_whitespace()` — 29 code points against 25, the M1 divergence.
/// `pyunicode::is_space` is the table that closed it and is what must be used.
fn strip_python_whitespace(s: &str) -> &str {
    s.trim_matches(crate::pyunicode::is_space)
}

/// A leading `+`/`-`, if any, and the rest. A second sign is left in the body,
/// where the digit walk rejects it — which is how `int('+-5')` and
/// `float('--5')` come out as `ValueError` without a special case.
fn split_sign(s: &str) -> (bool, &str) {
    match s.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    }
}

/// Drop `_` separators, rejecting the placements Python rejects: none leading,
/// trailing or doubled.
fn drop_underscores(s: &str) -> Option<String> {
    if s.is_empty() || s.starts_with('_') || s.ends_with('_') || s.contains("__") {
        return None;
    }
    Some(s.chars().filter(|c| *c != '_').collect())
}

/// The ASCII decimal spelling of a validated Python float body, sign already
/// stripped, or `None` where Python raises `ValueError`.
///
/// Underscores are permitted only *between* digits. Measured: `float('1_0.5')`
/// is `10.5`, while `float('1_.5')`, `float('1._5')`, `float('1e_5')` and
/// `float('1_e5')` are all `ValueError`. `.5` and `5.` are both legal, and an
/// exponent must have digits of its own (`float('1e')` is a `ValueError`).
fn canonical_float_digits(s: &str) -> Option<String> {
    let mut out = String::with_capacity(s.len());
    let mut digits = 0usize;
    let mut seen_dot = false;
    let mut seen_exp = false;
    let mut prev_was_digit = false;
    let mut chars = s.chars().peekable();

    while let Some(c) = chars.next() {
        if c == '_' {
            let followed_by_digit = chars
                .peek()
                .is_some_and(|n| decimal_digit_value(*n).is_some());
            if !prev_was_digit || !followed_by_digit {
                return None;
            }
            continue;
        }
        if c == '.' {
            if seen_dot || seen_exp {
                return None;
            }
            seen_dot = true;
            prev_was_digit = false;
            out.push('.');
            continue;
        }
        if c == 'e' || c == 'E' {
            if seen_exp || digits == 0 {
                return None;
            }
            seen_exp = true;
            prev_was_digit = false;
            out.push('e');
            if matches!(chars.peek(), Some('+' | '-')) {
                out.push(chars.next().expect("peeked"));
            }
            continue;
        }
        let digit = decimal_digit_value(c)?;
        out.push(char::from_digit(digit, 10).expect("0..=9"));
        digits += 1;
        prev_was_digit = true;
    }

    // `digits` counts both sides of the point and the exponent, so `'.'` and
    // `'e5'` are both rejected, as Python rejects them.
    (digits > 0).then_some(out)
}
```

`python_float` must reproduce two measured rules that Rust's parse does not share: underscore placement is the same as `python_int`'s, and `f64::from_str` accepts `"inf"`/`"NaN"` spellings that Python's `float` also accepts but that the caller's `e`/`.` precondition makes unreachable — so the function must **not** rely on Rust's parse for the *decision*, only for the final conversion of an already-validated decimal string.

- [ ] **Step 5: Write `variable.rs` and the two new `AttrValue` variants**

```rust
//! `Variable.__init__` — `base.py:850-925`.
//!
//! The order of operations is load-bearing in one place and the module is
//! written to preserve it: the float branch **assigns before it raises**, so
//! `Variable("2.")` ends up with `literal=2.0` *and* `lookups=("2", "")`. The
//! value is unreachable through `resolve` (which branches on `lookups`), but the
//! slot is observable and the materialiser sets slots directly, so a port that
//! raised before assigning would produce an object Django never produces.

/// A `Variable` as it crosses the wire.
///
/// The pair `(literal, lookups)` is the discriminator, exactly as in Django:
/// `lookups: Some(_)` means a real variable, `lookups: None` means a literal
/// whose value is in `literal`. `Option<AttrValue>` alone could not express it,
/// because `literal` is legitimately `None` for every real variable.
#[derive(Debug, Clone, PartialEq)]
pub struct VariableDesc {
    /// `Variable.var` — the original string, kept for `__str__`/`__repr__`.
    pub var: String,
    pub literal: Option<AttrValue>,
    pub lookups: Option<Vec<String>>,
    pub translate: bool,
}

pub fn parse_variable(raw: &str) -> Result<VariableDesc, Error> { ... }

/// `django.utils.text.unescape_string_literal` — `Some` where Python would not
/// raise `ValueError`.
///
/// The two replacements are ordered: the escaped quote first, then a doubled
/// backslash, which is why `"a \"bc\""` is `a "bc"` and not `a \"bc\"`.
pub fn unescape_string_literal(s: &str) -> Option<String> { ... }
```

Add to `desc.rs`:

```rust
    /// A float literal — `Variable.__init__`'s `float(var)` branch.
    Float(f64),
    /// An integer literal that does not fit `i64`. Materialises as `int(text)`.
    BigInt(String),
```

and in `crates/template-py/src/lib.rs`'s `attr_value_to_py`:

```rust
        AttrValue::Float(f) => (*f).into_pyobject(py)?.into_any().unbind(),
        AttrValue::BigInt(text) => {
            // Converted here rather than in the shell so the wire stays plain:
            // `int()` on the Python side of the boundary is exact and
            // arbitrary-precision, which is the whole reason the variant exists.
            py.eval(&std::ffi::CString::new(format!("int({})", ...))?, None, None)?
        }
```

Do **not** use `py.eval` on a formatted string — it is an injection point and `clippy` will not catch it. Use `py.get_type::<PyInt>()`… which does not exist either; the correct call is `PyLong` from the builtins, or `py.import("builtins")?.getattr("int")?.call1((text,))`. Use the `builtins` form, and say in a comment that the text is a validated decimal produced by `python_int`, so the only input it can receive is `[+-]?[0-9]+`.

- [ ] **Step 6: Replace M2's `{{ x }}` gap test rather than deleting it**

`crates/template-core/src/parser/mod.rs` currently has:

```rust
    #[test]
    fn a_variable_reports_the_m3_gap_rather_than_dropping_it() { ... }
```

`{{ x }}` is this task's deliverable, so that test's premise is gone. Replace it with the two tests that take over its job — a variable now parses, and something that is still unimplemented says so:

```rust
    #[test]
    fn a_variable_now_parses_and_the_m3_gap_is_closed() {
        // M2's `a_variable_reports_the_m3_gap_rather_than_dropping_it` asserted
        // `{{ x }}` was `Unimplemented`. This task supplies it, so the
        // assertion inverts: the node exists, and it is a VariableNode.
        let nodelist = parse_ok("{{ x }}");
        assert_eq!(nodelist.nodes[0].class_name, "VariableNode");
    }

    #[test]
    fn a_variable_that_django_rejects_is_rejected_here_too() {
        let error = failure("{{ a+b }}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid character ('+') in variable name: 'a+b'".to_string(),
            ),
        );
    }
```

`parse_ok` is a one-line sibling of the existing `failure` helper; if `mod.rs` has no such helper, add it next to `failure` rather than inlining a `Parser::new(...)` in each test.

- [ ] **Step 7: Pin the Nd table from the Python side**

Add to `crates/template-py/src/lib.rs`:

```rust
/// Every code point Python's `unicodedata` reports as category `Nd`, ascending.
///
/// Exposed for the same reason `whitespace_codepoints` is: the comparison can
/// only run where both languages exist, because a Rust `char` cannot hold a
/// surrogate and CPython's table is not readable from Rust.
#[pyfunction]
fn decimal_digit_codepoints() -> Vec<u32> {
    template_core::pynum::decimal_digit_codepoints()
}
```

and the test, beside `test_whitespace_table_is_exactly_pythons`:

```python
    def test_the_decimal_digit_table_is_exactly_pythons(self):
        import unicodedata
        from django_rust_templates import _native

        theirs = {c for c in range(0x110000) if unicodedata.category(chr(c)) == "Nd"}
        ours = set(_native.decimal_digit_codepoints())
        self.assertEqual(ours - theirs, set())
        self.assertEqual(theirs - ours, set())
```

This is the *only* check that can see the table move, and it moves for two independent reasons: a Python upgrade changes CPython's `unicodedata`, and nothing in this repository pins either. Add it to the pin-bump checklist alongside the whitespace guard (§10.4's checklist item), and say so in the test's docstring — a guard whose trigger is undocumented is a guard nobody runs.

- [ ] **Step 8: The parity test — our `Variable` against Django's**

`tests/test_variable_parity.py`, new, following `tests/test_parser_parity.py`'s shape (Django imported from `.django/` with no shim in the path, plus the guard that the target really is Django's):

```python
CASES = [
    "abc", "a.b.c", "0x10", ".", "..", "5.5.5", "5_", "1__0",
    'a.b|default:"x"', '""', '"quoted"', "'q'", '_("x")', '_( "x" )',
    "5", " 5 ", "+5", "5\n", "-5", "١٢٣", "1_000",
    "9223372036854775807", "9223372036854775808", "99999999999999999999",
    "1.5", ".5", "1e5", "1E5", "1.5e3", "1e400", "2.", "inf", "nan",
]
```

and, for each, compare `(var, literal, lookups, translate)` from Django's `Variable` against ours materialised through a callback-free path. The two rows that carry the task's real risk are `"2."` (the assign-then-raise order) and `"99999999999999999999"` (the `BigInt` arm); the test must name them in a comment so a future reader deleting a row knows which ones were the point.

- [ ] **Step 9: Run everything**

Run: `source scripts/env.sh && cargo test -p template-core; echo "exit=$?"`
Expected: exit 0.

Run: `source scripts/env.sh && maturin develop && python -m unittest tests.test_variable_parity tests.test_unicode_tables -v`
Expected: PASS. (Adjust the module name to wherever the whitespace guard actually lives; read `tests/` rather than guessing.)

Run: `source scripts/env.sh && cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"`
Expected: exit 0.

Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"`
Expected: exit 0, `Ran 1547 tests … OK (skipped=6)`. Unchanged: the engine is not yet in the suite's path (§10.1).

- [ ] **Step 10: Commit**

```bash
git add crates/template-core/src crates/template-py/src tests
git commit -m "M3 task 3: Python's int/float grammar and Variable's parsing"
```

---

### Task 4: The wire gains containers and a nodelist reference

**Files:**
- Modify: `crates/template-core/src/parser/desc.rs`
- Modify: `crates/template-py/src/lib.rs` (`attr_value_to_py`)
- Modify: `python/django_rust_templates/_impl/parser.py` (`_decode_value`, `materialise_node`, `NODE_CLASSES`)
- Test: `crates/template-core/src/parser/desc.rs` (unit), `tests/test_materialise.py` (extend)

**Interfaces:**
- Consumes: `VariableDesc` (Task 3), `NodeRef`.
- Produces: `AttrValue::{Variable(VariableDesc), List(Vec<AttrValue>), Map(Vec<(String, AttrValue)>), NodeListRef(usize)}`, plus the materialiser accepting a `parser` argument.

**Declared environment.** `desc.rs` unit tests run in a bare shell; the materialiser tests need `maturin develop`. `NodeListRef` is the one arm whose *resolution* cannot be tested from Rust at all — it is an index into a `NodeDesc`'s own `nodelists`, and only the Python materialiser resolves it — so Step 4's proof is a Python test, and that is stated rather than left implicit.

- [ ] **Step 1: Write the failing `desc.rs` unit tests**

```rust
    #[test]
    fn containers_keep_insertion_order_and_nest() {
        let value = AttrValue::List(vec![
            AttrValue::Int(1),
            AttrValue::List(vec![AttrValue::Str("x".to_string())]),
        ]);
        let AttrValue::List(outer) = &value else { unreachable!() };
        assert_eq!(outer.len(), 2);
        assert!(matches!(outer[0], AttrValue::Int(1)));

        let mapping = AttrValue::Map(vec![("a".to_string(), AttrValue::Bool(true))]);
        let AttrValue::Map(entries) = &mapping else { unreachable!() };
        assert_eq!(entries[0].0, "a");
    }

    #[test]
    fn a_nodelist_ref_is_an_index_and_not_a_copy() {
        // The whole point of the variant: the nodelist stays in
        // `NodeDesc.nodelists`, where `materialise_node` already builds
        // children, and the attribute carries a position. A `NodeListDesc`
        // inline would duplicate the subtree and break identity.
        let node = NodeDesc::new("IfNode").attr(
            "conditions_nodelists",
            AttrValue::List(vec![AttrValue::List(vec![
                AttrValue::None,
                AttrValue::NodeListRef(0),
            ])]),
        );
        assert!(node.nodelists.is_empty(), "the ref must not inline a nodelist");
    }
```

- [ ] **Step 2: Run and watch them fail**

Run: `source scripts/env.sh && cargo test -p template-core desc::`
Expected: FAIL to compile — the variants do not exist.

- [ ] **Step 3: Add the variants, and the Python arms**

In `desc.rs`, extend the enum (each variant carries a doc comment saying which Django attribute it exists for — `List` for `IfNode.conditions_nodelists` and `ForNode.loopvars`, `Map` for `WithNode.extra_context`, `NodeListRef` for a nodelist inside a container, `Variable` for a filter argument that is a variable):

```rust
    /// A `Variable` — a filter argument, or a `{% with %}` target.
    Variable(VariableDesc),
    /// A list of values. See the module doc for why a nodelist inside one is
    /// [`AttrValue::NodeListRef`] rather than inline.
    List(Vec<AttrValue>),
    /// A `token_kwargs` result — `WithNode.extra_context`, `IncludeNode.extra_context`.
    Map(Vec<(String, AttrValue)>),
    /// A position in this node's own `NodeDesc.nodelists`.
    ///
    /// `IfNode.conditions_nodelists` is a list of `(condition, nodelist)` pairs,
    /// and the nodelist half must be a **real Python `NodeList`** held inside
    /// the pairs: `IfNode` does not override `child_nodelists` (it inherits
    /// `('nodelist',)` from `Node`) while `nodelist` is a computed property
    /// returning `NodeList(self)`, so `get_nodes_by_type` reaches an `IfNode`'s
    /// children only through the pairs. Inlining a `NodeListDesc` would build a
    /// second, identity-different tree.
    NodeListRef(usize),
```

**Two doc-comment hazards apply to this step**, both of which have already cost this project a build: no indented non-Rust code blocks anywhere in these comments (rustdoc compiles them as doctests and `cargo test` runs them — use a fenced ```text block instead), and the `(String, AttrValue)` tuple in `Map` is three levels deep inside `Vec`, which is where `clippy::type_complexity` fires. If clippy objects, name it: `type MapEntries = Vec<(String, AttrValue)>;`.

In `crates/template-py/src/lib.rs`:

```rust
        AttrValue::Variable(v) => variable_desc_to_py(py, v)?,
        AttrValue::List(items) => {
            let items: Vec<Py<PyAny>> = items
                .iter()
                .map(|item| attr_value_to_py(py, item))
                .collect::<PyResult<_>>()?;
            PyList::new(py, items)?.into_any().unbind()
        }
        AttrValue::Map(entries) => {
            // A real `dict`, not a list of pairs. An earlier draft of this step
            // said the opposite, to "preserve insertion order" — which Python's
            // dict has kept since 3.7, so the list bought nothing and lost
            // something. Measured against `token_kwargs` (`base.py:1196`): it
            // assigns `kwargs[key] = …`, so a repeated key **keeps the first
            // position and takes the last value**, and every consumer
            // (`WithNode.extra_context`, `IncludeNode.extra_context`,
            // `parse_bits`' kwargs) wants a mapping, not pairs. `dict(pairs)`
            // reproduces exactly that; a list handed to `context.new()` would
            // not. The duplicate-key case gets its own test below.
            let mapping = PyDict::new(py);
            for (name, value) in entries {
                mapping.set_item(name, attr_value_to_py(py, value)?)?;
            }
            mapping.into_any().unbind()
        }
        AttrValue::NodeListRef(index) => (*index).into_pyobject(py)?.into_any().unbind(),
```

`PyDict::set_item` on an existing key overwrites the value and leaves the key in
its original position — which is what `kwargs[key] = …` does — so the duplicate
case needs no special handling and must not get one.

M2's `pair_to_py` helper had this arm as its only caller. Delete it in the same
commit: `clippy -D warnings` fails on dead code, and the commit gate is clippy.

- [ ] **Step 3a: Pin the duplicate-key behaviour rather than assuming it**

```python
    def test_a_repeated_kwarg_keeps_its_first_position_and_last_value(self):
        # `token_kwargs` does `kwargs[key] = …` (`base.py:1196`). A `Vec` of
        # pairs materialised by `dict()` reproduces it; a list of pairs passed
        # through unchanged does not.
        decoded = _decode_value(
            _native_attr_map([("a", 1), ("b", 2), ("a", 3)]), None, None
        )
        self.assertEqual(list(decoded.items()), [("a", 3), ("b", 2)])
```

Use the `builtins` route for `BigInt` from Task 3 here too if it has not already been written; do not `eval` a formatted string.

- [ ] **Step 4: The materialiser arms, with the ordering proven rather than assumed**

In `python/django_rust_templates/_impl/parser.py`:

```python
    if isinstance(value, _native.Variable):
        return _materialise_variable(value, parser)
    if isinstance(value, list):
        return [_decode_value(item, resolve_ref, parser) for item in value]
    if isinstance(value, int):
        # `NodeListRef`. Resolved against the *parent's* nodelists, which
        # `materialise_node` builds before it decodes attributes — see the test
        # in `tests/test_materialise.py` that proves the ordering instead of
        # assuming it.
        return nodelists[index]
```

and `materialise_node` gains a `parser=None` parameter that the two expression arms require, raising the same shape of `LookupError` that `resolve_ref` already raises when it is missing.

Ordering test, in `tests/test_materialise.py`:

```python
    def test_a_nodelist_ref_resolves_because_children_are_built_first(self):
        # The claim `AttrValue::NodeListRef` rests on: a node's attributes are
        # decoded *after* its child nodelists are materialised, so the index is
        # populated by the time it is read. If `materialise_node` ever reorders
        # — constructing the parent before the children — this fails loudly with
        # an IndexError instead of silently resolving a nodelist that is not
        # there yet.
        node = materialise_node(
            ("StandinRefNode", [("child", 0)], [("nodelist", ([], False))])
        )
        self.assertEqual(len(node.child), 0)
```

Use the stand-in class the way M2's materialiser tests already do; if a class with a `child` attribute does not exist, add the arm to the test's own registry rather than to `NODE_CLASSES` — a test-only class in the production registry is how the next person loses an hour.

- [ ] **Step 5: Run everything**

Run: `source scripts/env.sh && cargo test -p template-core; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && maturin develop && python -m unittest tests.test_materialise -v` → PASS.
Run: `source scripts/env.sh && cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"` → exit 0, 1547 unchanged.

- [ ] **Step 6: Commit**

```bash
git add crates/template-core/src/parser/desc.rs crates/template-py/src/lib.rs python tests
git commit -m "M3 task 4: containers and a nodelist reference on the wire"
```

---

### Task 5: `filter_re`, and `FilterExpression.__init__`'s match loop

**Files:**
- Create: `crates/template-core/src/parser/filter_expr.rs`
- Modify: `crates/template-core/src/parser/mod.rs`, `crates/template-core/src/parser/desc.rs` (`AttrValue::FilterExpr`)
- Modify: `crates/template-py/src/lib.rs` (`attr_value_to_py`'s `FilterExpr` arm, the `FilterExpr` pyclass, `filter_expr_desc_to_py`, `_native.parse_filter_expression`)
- Modify: `crates/template-core/src/parser/tags/mod.rs` (the `VariableNode` compile path — `{{ … }}` is the lexer's `VARIABLE` token, not a tag, so this is in `parse`)
- Test: `crates/template-core/src/parser/filter_expr.rs`, `tests/test_filter_expr_parity.py` (**new**)

**Interfaces:**
- Consumes: `pynum`, `VariableDesc`, `parse_variable`, `pyregex::{PYTHON_WORD_STANDALONE, PYTHON_WORD_IN_CLASS}`, `AttrValue::{List, Variable}`.
- Produces:
  - `template_core::parser::filter_expr::parse_filter_expression(token: &str) -> Result<FilterExprDesc, Error>`
  - `pub struct FilterExprDesc { pub token: String, pub var: FilterVar, pub filters: Vec<FilterCall> }`
  - `pub struct FilterCall { pub name: String, pub args: Vec<FilterVar> }`
  - `pub type FilterVar = (bool, AttrValue);` — the bool is Django's `lookup` flag, which is `False` for a constant argument (already resolved to its value at parse time) and `True` for a variable. A named alias rather than an inline tuple because `clippy::type_complexity` has already bitten this project twice.
  - `AttrValue::FilterExpr(FilterExprDesc)`
  - `#[pyclass] FilterExpr` with `token`, `var` and `filters` readable, plus `filter_expr_desc_to_py(py, &FilterExprDesc) -> Py<PyAny>` — the parallel of Task 3's `Variable`, in the same style
  - `_native.parse_filter_expression(token: str) -> FilterExpr`
  - the `AttrValue::FilterExpr(expr) => filter_expr_desc_to_py(py, expr)?` arm in `attr_value_to_py`, without which the match stops being exhaustive and the py crate does not build

**Declared environment.** `parser.find_filter` is **Python's** and does not exist on the Rust side; that is the whole reason this task produces names and arguments rather than filter objects (D2). `FilterExpression.args_check` is likewise Python's (Review Focus item 2) and is Task 6's. So this task's Rust tests can assert descriptors and error messages, and the *only* place `find_filter` is exercised is `tests/test_filter_expr_parity.py`, which has Django on one side. Nothing in this task may construct a `NodeRef` for a filter: a filter is not a host value at M3, it is a name resolved at materialisation time.

**The pyclass is this task's, for the same reason Task 3's is Task 3's.** `parse_filter_expression` must be drivable from Python — Task 6's materialiser tests call `_native.parse_filter_expression("x|upper")` and pass the result to `_materialise_filter_expression` — so the binding ships with the parser that produces it, and Task 7 adds only `render` and `resolve`.

#### The specification

Django's pattern is assembled from `constant_string` (`base.py:680-699`) and `filter_raw_string` (`base.py:701-719`), with `var_chars = r"\w\.\+-"` and `filter_name` as `(?P<filter_name>\w+)`. **Both `\w` occurrences go through Task 2's constants, and in different spellings** — `var_chars` is spliced *inside* `[%(var_chars)s]+`, `filter_name` is standalone. Task 2 exposes the contents form and the bracketed form; use each where it belongs. Getting them the wrong way round is asymmetric: the contents form standalone rejects `upper` loudly, while the bracketed form spliced into a class **parses and works by accident** under the `regex` crate's nested character classes, so nothing would report it.

The loop, and the three errors it can raise, verbatim from `FilterExpression.__init__` (`base.py:733-775`):

| condition | message |
|---|---|
| a match starts after `upto` | `"Could not parse some characters: %s\|%s\|%s" % (token[:upto], token[upto:start], token[start:])` |
| the first match has no `constant` and no `var` | `"Could not find variable at start of %s." % token` |
| `upto != len(token)` after the loop | `"Could not parse the remainder: '%s' from '%s'" % (token[upto:], token)` |

and the two branch rules a port gets wrong by tidying them up:

- a **constant** first match is `Variable(constant).resolve({})` — the *value*, and `is_var` is therefore `False`; a **var** first match is a `Variable`, and `is_var` is `True`;
- an argument is `(False, Variable(constant_arg).resolve({}))` for a constant and `(True, Variable(var_arg))` for a var. The bool is the wire's `lookup` flag, and it decides whether `resolve` calls `mark_safe` on it at render time — so it is not decorative.

- [ ] **Step 1: Write the failing test**

```rust
    #[test]
    fn a_plain_variable_is_a_var_and_a_quoted_one_is_a_literal() {
        let plain = parse_filter_expression("x").unwrap();
        assert!(plain.var.0, "a bare name is a Variable, so is_var is true");
        assert!(plain.filters.is_empty());

        let quoted = parse_filter_expression("\"x\"").unwrap();
        assert!(!quoted.var.0, "a constant resolves to its value, so is_var is false");
        assert_eq!(quoted.var.1, AttrValue::SafeStr("x".to_string()));
    }

    #[test]
    fn filter_arguments_keep_the_lookup_flag() {
        let parsed = parse_filter_expression("x|default:\"d\"|join:y").unwrap();
        assert_eq!(parsed.filters.len(), 2);
        assert_eq!(parsed.filters[0].name, "default");
        assert_eq!(parsed.filters[0].args[0].0, false, "a constant arg is not a lookup");
        assert_eq!(parsed.filters[0].args[0].1, AttrValue::SafeStr("d".to_string()));
        assert_eq!(parsed.filters[1].name, "join");
        assert_eq!(parsed.filters[1].args[0].0, true, "a var arg is a lookup");
    }

    #[test]
    fn the_three_error_messages_are_djangos() {
        assert_eq!(
            parse_filter_expression("x|upper!!").unwrap_err().kind,
            ErrorKind::Syntax(
                "Could not parse the remainder: '!!' from 'x|upper!!'".to_string(),
            ),
        );
        assert_eq!(
            parse_filter_expression("|upper").unwrap_err().kind,
            ErrorKind::Syntax("Could not find variable at start of |upper.".to_string()),
        );
    }
```

- [ ] **Step 2: Run and watch it fail**

Run: `source scripts/env.sh && cargo test -p template-core filter_expr`
Expected: FAIL to compile.

- [ ] **Step 3: Write `filter_expr.rs`**

```rust
//! `filter_re` and `FilterExpression.__init__` — `base.py:701-775`.
//!
//! Rust parses the token into a descriptor holding the variable part and an
//! ordered list of `(filter_name, args)`; the shell resolves every name through
//! `parser.find_filter` and runs `args_check` where the function is (D2, Review
//! Focus item 2). Names rather than handles is not a shortcut — a
//! `FilterExpression` holds a Python function, and `args_check` inspects its
//! signature, so no Rust type can carry one.

/// The pattern, assembled exactly as Django assembles `filter_raw_string`.
///
/// Built with `OnceLock` rather than a `lazy_static`-style macro: the crate has
/// one dependency and this needs none.
fn filter_re() -> &'static Regex { ... }

pub fn parse_filter_expression(token: &str) -> Result<FilterExprDesc, Error> { ... }
```

Two implementation notes that are the task's real difficulty, both stated so the port is not "tidied" into being wrong:

- `upto` advances to `match.end()` on **every** iteration including the first, and the "some characters" error compares `upto` to `match.start()` — so a pattern that skips a character in the *middle* is caught, not only one that skips at the start;
- the constant branch swallows `VariableDoesNotExist` and leaves `var_obj` as `None` (`base.py:745-749`). `None` is a legitimate `is_var == False` value, so the descriptor's `var` is `(false, AttrValue::None)` — a port that treated the exception as an error would reject a token Django accepts.

- [ ] **Step 4: Wire `{{ … }}` into `Parser.parse`**

`{{ x }}` is a `VARIABLE` token, not a block tag, so its compile path is in `Parser::parse`'s match on token type, at the place M2 currently returns `Error::unimplemented` naming M3. Replace that arm with one that calls `parse_filter_expression(token.contents)` and builds:

```rust
        NodeDesc::new("VariableNode")
            .attr("filter_expression", AttrValue::FilterExpr(desc))
```

The parameter name `filter_expression` is `VariableNode.__init__`'s own (`base.py:1139`) and the materialiser constructs by keyword, so it is contract rather than style.

- [ ] **Step 5: The parity test**

`tests/test_filter_expr_parity.py`, comparing our parse against Django's real `FilterExpression` over a corpus. The corpus must be generated from the pattern's own alphabet rather than hand-picked — **`\w`, `.`, `+`, `-`, `|`, `:`, both quotes, and the two separators** — because the divergences Task 2 exists to prevent live at exactly the code points a hand-written corpus leaves out. Include, by name, the two user-visible cases from Task 2's Review Focus line: `"²"` (Python word, not a Rust word) and `"á"` (Rust word, not a Python word). Compare, for each source: our outcome (`ok` with a descriptor, or the error message) against Django's (`ok` with `len(fe.filters)` and a repr of `fe.var`, or the exception message).

- [ ] **Step 6: Run everything**

Run: `source scripts/env.sh && cargo test -p template-core; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && maturin develop && python -m unittest tests.test_filter_expr_parity -v` → PASS.
Run: `source scripts/env.sh && cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"` → exit 0, 1547 unchanged.

- [ ] **Step 7: Commit**

```bash
git add crates/template-core/src crates/template-py/src tests
git commit -m "M3 task 5: filter_re and the FilterExpression parse loop"
```

---

### Task 6: Materialise `Variable` and `FilterExpression` as Django's own objects

**Files:**
- Modify: `python/django_rust_templates/_impl/parser.py`
- Test: `tests/test_materialise.py` (extend)

**Interfaces:**
- Consumes: `FilterExprDesc`, `VariableDesc`, the `parser` argument added in Task 4.
- Produces: `_materialise_variable(desc, parser) -> Variable`, `_materialise_filter_expression(desc, parser) -> FilterExpression`; `NODE_CLASSES["VariableNode"]`.

**Declared environment.** This is the first task whose deliverable is entirely Python, so `cargo test -p template-core` has nothing to say about it and `scripts/check.sh` remains unchanged (the engine is not in the suite's path until M9). The right-hand side of every assertion is Django's real `Variable`/`FilterExpression`, so the test compares our object against Django's own construction rather than against a description of it. `parser.find_filter` and `FilterExpression.args_check` are real and callable on the pinned tree.

#### The construction mechanism, and why

`Variable` has `__slots__ = ("var", "literal", "lookups", "translate", "message_context")` and `FilterExpression` has `__slots__ = ("token", "filters", "var", "is_var")`. Both parse in `__init__`. The shell therefore builds them with `__new__` and assigns the slots, rather than calling the constructor — because calling it would re-parse in Python and discard Rust's work, and because Rust's parse is where the error messages and their tokens come from.

`isinstance(obj, Variable)` is still `True` after this, which is load-bearing: `FilterExpression.is_var` is `isinstance(var_obj, Variable)`, so the shell recomputes it the same way rather than trusting a flag.

- [ ] **Step 1: Write the failing test**

```python
class FilterExpressionMaterialisationTests(unittest.TestCase):
    def test_a_materialised_variable_matches_djangos_own(self):
        from django.template.base import Variable

        ours = _materialise_variable(_native.parse_variable("a.b.c"), parser())
        theirs = Variable("a.b.c")
        for slot in ("var", "literal", "lookups", "translate", "message_context"):
            with self.subTest(slot=slot):
                self.assertEqual(getattr(ours, slot), getattr(theirs, slot))
        # `isinstance` is what `FilterExpression.is_var` reads, so it is asserted
        # rather than assumed from the slot values.
        self.assertIsInstance(ours, Variable)

    def test_a_materialised_filter_expression_holds_real_filter_functions(self):
        from django.template.base import FilterExpression

        ours = _materialise_filter_expression(
            _native.parse_filter_expression("x|upper"), parser()
        )
        theirs = FilterExpression("x|upper", parser())
        self.assertEqual(len(ours.filters), 1)
        self.assertIs(ours.filters[0][0], theirs.filters[0][0], "the filter is Django's function, by identity")
        self.assertTrue(ours.is_var)
        self.assertEqual(ours.token, "x|upper")

    def test_args_check_runs_where_the_function_is(self):
        # Review Focus item 2: a Rust-side check cannot reproduce
        # `inspect.getfullargspec` + decorator unwrapping. The assertion is that
        # the *shell* raises, with Django's message, and that it is the real
        # `args_check` doing it rather than a reimplementation.
        with self.assertRaises(Exception) as caught:
            _materialise_filter_expression(
                _native.parse_filter_expression("x|cut"), parser()
            )
        self.assertEqual(str(caught.exception), "cut requires 2 arguments, 1 provided")

    def test_an_unknown_filter_is_djangos_own_error(self):
        with self.assertRaises(Exception) as caught:
            _materialise_filter_expression(
                _native.parse_filter_expression("x|nope"), parser()
            )
        self.assertEqual(str(caught.exception), "Invalid filter: 'nope'")
```

`parser()` is the helper the repository's materialiser tests already need — `Parser(tokens, builtins=Engine().template_builtins)`, because a bare `Parser(tokens)` registers **nothing** and knows no filters either. Reuse the existing helper rather than writing a second one; the "bare Parser knows nothing" trap has already cost this project two plan steps.

- [ ] **Step 2: Run and watch it fail**

Run: `source scripts/env.sh && maturin develop && python -m unittest tests.test_materialise -v`
Expected: FAIL — `AttributeError: module '_native' has no attribute 'parse_variable'`. Task 3 and Task 5 add those bindings; if they are missing, that is the failure this step exists to show.

- [ ] **Step 3: Implement the two materialisers**

```python
def _materialise_variable(desc, parser=None):
    """Build a real `Variable` from a descriptor, without re-parsing.

    `__new__` plus slot assignment rather than `Variable(raw)`: calling the
    constructor would parse the token a second time in Python and throw away the
    Rust parse — including the error messages and their tokens, which are Rust's
    to produce (§6).
    """
    variable = Variable.__new__(Variable)
    variable.var = desc.var
    variable.literal = _decode_value(desc.literal, None, parser)
    variable.lookups = tuple(desc.lookups) if desc.lookups is not None else None
    variable.translate = desc.translate
    variable.message_context = None
    return variable


def _materialise_filter_expression(desc, parser=None):
    """Build a real `FilterExpression`, resolving filters through the parser."""
    expression = FilterExpression.__new__(FilterExpression)
    expression.token = desc.token
    filters = []
    for call in desc.filters:
        # `filters` is a list of `(func, args)`; `args` is a list of
        # `(lookup, value)` pairs, and the *bool* is what `resolve` reads to
        # decide between `mark_safe(arg)` and `arg.resolve(context)`
        # (`base.py:804-810`). A constant argument is resolved to its value at
        # parse time, which is why its flag is False.
        args = []
        for is_lookup, value in call.args:
            if is_lookup:
                args.append((True, _materialise_variable(value, parser)))
            else:
                args.append((False, _decode_value(value, None, parser)))
        func = parser.find_filter(call.name)
        FilterExpression.args_check(call.name, func, args)
        filters.append((func, args))
    expression.filters = filters
    expression.var = (
        _materialise_variable(desc.var[1], parser)
        if desc.var[0]
        else _decode_value(desc.var[1], None, parser)
    )
    expression.is_var = isinstance(expression.var, Variable)
    return expression
```

`args_check` must be called **before** the filter is appended, in the same order
`FilterExpression.__init__` calls it — `find_filter`, then `args_check`, then
`append` (`base.py:771-774`) — so that a bad argument count raises the error
Django raises, at the point Django raises it. Measured, not read: the signature
is `args_check(name, func, provided)` where `provided` is the list of
`(lookup, value)` **pairs**, because `plen = len(provided) + 1` counts the
implicit filter input, and the failure is
`"%s requires %d arguments, %d provided"` — confirmed by
`FilterExpression("x|cut", parser())` raising `cut requires 2 arguments, 1
provided` on the pinned tree.

Note also that a materialised `Variable`'s `literal` for a quoted string is a `SafeString` — `AttrValue::SafeStr` already carries that (M2's `{% verbatim %}` variant), so `_decode_value`'s existing `SafeStr` arm handles it and no new arm is needed. Assert it rather than assuming: `test_a_materialised_variable_matches_djangos_own` covers `'\"quoted\"'` in its slot comparison.

- [ ] **Step 4: Add `VariableNode` to `NODE_CLASSES`, and the `FilterExpr` arm to `_decode_value`**

```python
    "VariableNode": VariableNode,
```

and import it from `django.template.base`. The registry is explicit rather than `getattr`-based, for the reason M2's comment gives.

Then the dispatch arm, which is what turns a parsed `{{ … }}` into a real `FilterExpression` — Step 3's materialiser has no caller without it, and `Task 4`'s `_native.Variable` arm beside it is the shape to copy:

```python
    if isinstance(value, _native.FilterExpr):
        return _materialise_filter_expression(value, parser)
```

Its position matters: `_native.FilterExpr` and `_native.Variable` are both `#[pyclass]`es, so neither is an instance of the other and their order is free — but the arm must precede the generic sequence/dict arms, because a descriptor that reached `isinstance(value, list)` first would be silently passed through as an object rather than materialised. Assert it: `test_a_variable_node_carries_a_real_filter_expression` renders through `NODE_CLASSES["VariableNode"]` and checks `isinstance(node.filter_expression, FilterExpression)` and `node.filter_expression.is_var`.

- [ ] **Step 5: Run everything**

Run: `source scripts/env.sh && maturin develop && python -m unittest tests.test_materialise -v` → PASS.
Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"` → exit 0, 1547 unchanged.

- [ ] **Step 6: Commit**

```bash
git add python tests
git commit -m "M3 task 6: materialise Variable and FilterExpression as Django's own objects"
```

---

### Task 7: The primitive resolver, and the first `_native.render`

**Files:**
- Create: `crates/template-core/src/parser/resolve.rs`
- Modify: `crates/template-py/src/lib.rs` (`_native.render`, `_native.resolve` — **not** `parse_variable` or `parse_filter_expression`, which Task 3 and Task 5 created)
- Create: `python/django_rust_templates/_impl/engine.py` — the shell entry that supplies the `Parser` (D5; written in Step 4)
- Test: `crates/template-core/src/parser/resolve.rs`, `tests/test_render_parity.py` (**new**), `tests/differential/cases.py` (extend)

**Interfaces:**
- Consumes: `VariableDesc`, `parse_variable`, the materialiser and its `parser` argument.
- Produces:
  - `template_core::parser::resolve::{resolve_lookup, LookupOutcome, LookupTarget, Step}`
  - `template_core::parser::resolve::LookupOutcome` — `Found(NodeRef) | Missing { bit: String, container: NodeRef } | Reraise | Unimplemented(String)`
  - `_native.resolve(path: str, context: Any) -> Any` — **no `parser`**, see Step 4
  - `_native.render(source: str, context: Any, parser: Any) -> str`
  - `django_rust_templates._impl.engine.render_string(source: str, context: Any) -> str`

**Declared environment.** `_native.render` needs a real `django.template.base.Parser`, because materialising a `FilterExpression` calls `parser.find_filter`. The caller supplies it — see D5 — so this task adds a Python entry that constructs one from `Engine().template_builtins` and a Rust entry that takes it as an argument. `Context` is likewise a real Python object; it crosses into `Step 4`'s `PyTarget` as an **owned `Py<PyAny>`**, not a `Bound<'py, PyAny>`, because `Step::Found` boxes its child and a boxed child cannot borrow from the container it came from — the reason is in `PyTarget`'s doc comment, and it is the one place in this task where the PyO3 types are load-bearing rather than incidental. Nothing here needs the `Host` trait: tags are still compiled by Rust at M3, and `find_filter` is reached through the `parser` argument rather than through `Host`. `LookupTarget` is a new trait with exactly one implementor, also in Step 4.

#### What M3's resolver does and does not do

`Variable._resolve_lookup` (`base.py:959-1029`) resolves a dotted path against a context. Its **callable arm** — `do_not_call_in_templates`, `alters_data`, `signature(current).bind()` — is M8's, and D3 says M3 must be loud rather than silent about it. The measured reason: when a lookup lands on a callable, Django calls it and renders the *result*; returning the callable renders `<function …>`, which is wrong **and quiet**. So M3 returns `Unimplemented` naming M8.

The arms M3 implements, and the exception each swallows, are exactly `base.py:963-996`: dictionary lookup (`TypeError, AttributeError, KeyError, ValueError, IndexError`, guarded by `hasattr(type(current), "__getitem__")`), attribute lookup (with the `BaseContext` suppression of class attributes), and list-index lookup (`IndexError, ValueError, KeyError, TypeError`).

**The failure is not a formatted message, and an earlier draft of this plan got that wrong.** The raise site is `base.py:989-992`:

```python
raise VariableDoesNotExist(
    "Failed lookup for key [%s] in %r",
    (bit, current),
)
```

The message is passed **unformatted**, because `VariableDoesNotExist.__str__` does the `%` (`base.py:109-110`), so `repr(current)` is called *lazily* — at stringification, if ever — and **not** during resolution. On the render path it usually is never called at all: `FilterExpression.resolve` catches the exception and substitutes `string_if_invalid` (`base.py:792-803`) without touching its message. Django's own suite pins the lazy contract at `tests/template_tests/test_base.py:67-70`.

Measured on the pinned tree, with a parameter whose `__repr__` raises:

```
constructed fine; msg = Failed lookup for key [%s] in %r
params[1] is the object by identity: True
str(e) raised ZeroDivisionError - repr is deferred to __str__
{{ nope }} -> ''    # and no exception was ever stringified
```

So `LookupOutcome::Missing` carries the **failing bit and the container's handle**, and the shell constructs `VariableDoesNotExist(msg, params=(bit, obj))` — two separate attributes, as Django does. There is deliberately **no `repr()` on the trait**: making the host render a repr per miss would call across the seam on the hottest failure path in the engine (`{{ missing }}` inside a `{% for %}`, once per iteration) precisely where Django defers the work, and it would *introduce* the divergence the earlier draft was trying to paper over. A raising `repr` therefore raises out of `str(exc)` in Python, exactly as Django's does.

**The shipped precedent is `Parser::origin_template_repr`** (`crates/template-core/src/parser/mod.rs:44-56`), which pre-renders Python's `repr()` of `Origin.template_name`. That case is the opposite one and the contrast is the rule: there Django *has* already formatted the string before Rust sees it, because the error carries a finished message. Here it has not. **`LookupError::Missing::current_repr` is not a type in this crate** — §5.2 names the *rule* that way, and `parser/mod.rs:48` cites that name in a doc comment. So do not go looking for a `LookupError` in `error.rs`: M2's error protocol is `ErrorKind::{Syntax, Host, UnterminatedMarker, Unimplemented, Internal}` and a missing lookup is not among them, because at M2 nothing could resolve. `LookupOutcome` is where it lands, and it is new in this task.

**Measured, because three of these are easy to get wrong and one is an environment trap:**

| Probe (`{{ <probe> }}`) | Context | Result |
|---|---|---|
| `a.boom` where `a` has a raising `@property boom` | instance | **`RuntimeError: kaboom` propagates** — not swallowed into a missing key |
| `a.boom` where `a` is a plain class without `boom` in `dir()` | instance | `''` — the `bit in dir(current)` guard is what separates the two |
| `c.template` where `c` is a `Context` | — | `''` — **not** `'None'`, though `getattr(c, "template")` is `None`: `template` is an *instance* attribute, so `getattr(type(c), "template")` raises and Django's arm is skipped before the instance is read (`base.py:972-977`) |
| `c.zero` where `c = FalsyCtx()` with class `zero = 0` and instance `zero = 7` | — | `'7'` — a *present and falsy* class attribute does **not** suppress, so the instance attribute is read |
| `a.1` where `a` is `[10, 20]` | — | **`ImproperlyConfigured`** unless `settings.configure()` has run |

Rows 3 and 4 are the two halves of one rule, and a predicate that gets either half wrong passes the other's test. Both were measured on the pinned tree; the first draft of this plan carried only row 3, and inferred from it a predicate (`getattr(type(current), bit, None) is not None`) that fails row 4 and mis-states row 3.

Every value in that table is reproduced by this script, and the fixture expectations below are the ones it prints — so a reader can tell a measured expectation from an asserted one rather than trusting the prose. Run it from the repo root:

```bash
.venv/bin/python -I - <<'PYEOF'
import sys; sys.path.insert(0, '.django')
import django
from django.conf import settings
settings.configure(USE_I18N=False, TEMPLATES=[{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [], "APP_DIRS": False, "OPTIONS": {"builtins": [], "libraries": {}},
}])
django.setup()
from django.template import Context, Template
from django.template.base import VariableDoesNotExist

class RaisingProperty:
    @property
    def boom(self): raise RuntimeError("kaboom")

class Plain: pass

class FalsyCtx(Context):
    zero = 0

def show(src, ctx):
    try: return repr(Template(src).render(Context(ctx)))
    except Exception as e: return f"{type(e).__name__}: {e}"

f = FalsyCtx(); f.zero = 7
c = Context()
print("row 1", show("{{ a.boom }}", {"a": RaisingProperty()}))   # RuntimeError: kaboom
print("row 2", show("{{ a.boom }}", {"a": Plain()}))            # ''
print("row 3", show("{{ c.template }}", {"c": c}))              # ''   (getattr(c,'template') is None)
print("row 4", show("{{ c.zero }}", {"c": f}))                  # '7'
print("row 5", show("{{ a.1 }}", {"a": [10, 20]}))              # '20'
# and the laziness the MAJOR is about:
class R:
    def __repr__(self): raise ZeroDivisionError
e = VariableDoesNotExist("Failed lookup for key [%s] in %r", ("bit", R()))
print("constructs fine; str(e) raises:", end=" ")
try: str(e)
except ZeroDivisionError: print("ZeroDivisionError - repr deferred to __str__")
PYEOF
```

The last row is the trap: rendering a *number* goes through `localize`, which reads `USE_I18N`. So `tests/test_render_parity.py` and `tests/differential/cases.py` must configure settings (or keep numeric contexts out of the corpus). It is a test-harness fact, not an engine fact, and it would otherwise present as an unexplained failure in the first render test.

- [ ] **Step 1: Write the failing test**

Five of the seven tests below are the five measured rows, expressed as fixture answers rather than as prose. They are unit tests because `template-core` has no Python: the fixture *is* the host, and it is what makes the `@property` rule testable at all.

```rust
    #[test]
    fn the_measured_arms() {
        // Dictionary arm. The container the loop ends on is what `Found` names.
        let mapping = Fake {
            map: Some(vec![("a", Fake::scalar(1, "'A'"))]),
            ..Fake::scalar(99, "{'a': 'A'}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["a"]), Box::new(mapping)),
            LookupOutcome::Found(NodeRef(1)),
        );

        // Missing: the failing bit, and the **container's handle** — index 99,
        // not the child's 1, because it is `current` that Django puts in
        // `VariableDoesNotExist.params`. No repr appears here: `__str__` builds
        // the message later, if ever.
        let mapping = Fake {
            map: Some(vec![("a", Fake::scalar(1, "'A'"))]),
            ..Fake::scalar(99, "{'a': 'A'}")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["b"]), Box::new(mapping)),
            LookupOutcome::Missing {
                bit: "b".to_string(),
                container: NodeRef(99),
            },
        );

        // List-index arm: `current[int("1")]`, through Task 3's `python_int`.
        let seq = Fake {
            seq: vec![Fake::scalar(10, "10"), Fake::scalar(20, "20")],
            ..Fake::scalar(99, "[10, 20]")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["1"]), Box::new(seq)),
            LookupOutcome::Found(NodeRef(20)),
        );

        // A bit that is not an integer never reaches the index arm: Django's
        // `int(bit)` raises `ValueError`, which that arm swallows.
        let seq = Fake {
            seq: vec![Fake::scalar(10, "10")],
            ..Fake::scalar(99, "[10]")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["x"]), Box::new(seq)),
            LookupOutcome::Missing { .. },
        ));
    }

    #[test]
    fn the_property_rule_decides_between_a_re_raise_and_a_missing_key() {
        // One raising lookup, two fixtures, and `bit in dir(current)` is the
        // only difference between propagating and rendering `string_if_invalid`.
        let in_dir = Fake {
            raising: vec!["boom"],
            dir: vec!["boom"],
            ..Fake::scalar(99, "<A object>")
        };
        assert_eq!(resolve_lookup(&lookups(&["boom"]), Box::new(in_dir)), LookupOutcome::Reraise);

        let not_in_dir = Fake {
            raising: vec!["boom"],
            dir: vec![],
            ..Fake::scalar(99, "<A object>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["boom"]), Box::new(not_in_dir)),
            LookupOutcome::Missing { .. },
        ));

        // ... and a `BaseContext` has two rules, not one, and the plan's first
        // predicate had both wrong. Measured: `Context().template` is an
        // *instance* attribute with no class attribute, so
        // `{{ c.template }}` renders `''` and not `'None'` — Django's
        // `getattr(type(current), bit)` raises before the instance is read, and
        // that AttributeError is caught one frame out (`base.py:977`).
        let absent_class_attr = Fake {
            attrs: vec![("template", Fake::scalar(1, "<Engine>"))],
            ..Fake::context(0, "<Context>")
        };
        assert!(matches!(
            resolve_lookup(&lookups(&["template"]), Box::new(absent_class_attr)),
            LookupOutcome::Missing { .. },
        ));

        // The other direction, measured on a `Context` subclass with
        // `zero = 0` and an instance `zero = 7`: a *present and falsy* class
        // attribute lets the instance attribute through, so `{{ c.zero }}` is
        // `7`. A predicate written as "the class attribute is set" suppresses
        // this too and renders `''`.
        let falsy_class_attr = Fake {
            attrs: vec![("zero", Fake::scalar(1, "7"))],
            falsy_class_attrs: vec!["zero"],
            ..Fake::context(0, "<FalsyCtx>")
        };
        assert_eq!(
            resolve_lookup(&lookups(&["zero"]), Box::new(falsy_class_attr)),
            LookupOutcome::Found(NodeRef(1)),
        );
    }

    #[test]
    fn the_callable_arm_is_M8s_and_says_so() {
        // D3. The alternative is returning the callable, which renders
        // `<function …>` — wrong and quiet.
        let outcome = resolve_lookup(&lookups(&[]), Box::new(Fake::callable()));
        let LookupOutcome::Unimplemented(message) = outcome else { panic!("{outcome:?}") };
        assert!(message.contains("M8"), "the message must name the milestone: {message}");
    }
```

- [ ] **Step 2: Run and watch it fail**

Run: `source scripts/env.sh && cargo test -p template-core resolve`
Expected: FAIL to compile.

- [ ] **Step 3: Write `resolve.rs`**

The trait. Model the context as a trait the host implements, in the shape §5.2 already establishes — `template-core` must not name a Python type. **Every method returns an outcome rather than raising**, because each Django arm swallows a *specific* exception set and *which* to swallow is the port's decision, not the host's:

```rust
//! The primitive resolver, ported from `Variable._resolve_lookup`.
//!
//! The **loop** lives here rather than in the host, so the arm order and the
//! swallowed-exception sets are testable in a bare shell. The host supplies only
//! what Python knows: a `dir()`, `__getitem__` presence, class-attribute
//! truthiness, callability, and the lookups themselves. It supplies no `repr`:
//! Django formats the missing-key message lazily, in `__str__`.

use crate::parser::desc::NodeRef;
use crate::pynum::{python_int, IntValue};

/// What one lookup arm did.
///
/// Three-way rather than `Option`, and **shared by all three arms**, because
/// Django distinguishes the cases and the distinction is the same in each: a
/// swallowed exception falls through to the next arm, a *returned* `None` is a
/// successful lookup, and any other exception leaves the loop entirely and is
/// judged by the `dir()` rule below. `get_item` and `get_index` return this too,
/// because a `__getitem__` that raises something outside Django's five is not
/// reachable through `Option` at all.
#[derive(Debug)]
pub enum Step {
    /// The arm found the next container.
    Found(Box<dyn LookupTarget>),
    /// A swallowed miss — `AttributeError` on the attribute arm, one of
    /// `TypeError, AttributeError, KeyError, ValueError, IndexError` on the
    /// item arm, one of `IndexError, ValueError, KeyError, TypeError` on the
    /// index arm. The loop tries the next arm.
    Miss,
    /// An exception this arm does not swallow. Django consults
    /// `bit in dir(current)` and either re-raises or reports a missing key
    /// (measured: a `@property` that raises propagates when its name is in
    /// `dir()`, and a plain class without the name renders `''`). It does
    /// **not** fall through to the next arm.
    Raised,
}

/// What the resolver needs from a context, supplied by the host.
///
/// Nine methods, one per predicate Django actually evaluates. That count is the
/// point: collapsing any two of them merges two of Django's arms, and the
/// merged behaviour is wrong in a case this milestone measured.
///
/// There is no `repr` here, and its absence is a decision: Django formats the
/// missing-key message lazily in `VariableDoesNotExist.__str__`, so the host
/// hands back the container's **handle** and Python does the rest. See
/// [`LookupOutcome::Missing`].
pub trait LookupTarget {
    /// `current[bit]`, attempted only when `has_getitem()`. A miss is any of
    /// Django's `TypeError, AttributeError, KeyError, ValueError, IndexError`;
    /// anything else is [`Step::Raised`].
    fn get_item(&self, bit: &str) -> Step;

    /// `hasattr(type(current), "__getitem__")` — the guard on the dictionary
    /// arm, which is why a scalar skips straight to `getattr`.
    fn has_getitem(&self) -> bool;

    /// `getattr(current, bit)` — see [`Step`]. An `AttributeError` here is
    /// `Step::Miss`, which is what falls through to the index arm.
    fn get_attr(&self, bit: &str) -> Step;

    /// Whether Django reads `current`'s **instance** attribute for `bit`, which
    /// for a `BaseContext` happens in exactly one case: the class attribute
    /// exists and is falsy (`base.py:972-976`).
    ///
    /// The predicate is not "the class attribute is set". Django's condition is
    /// `isinstance(current, BaseContext) and getattr(type(current), bit)`, and it
    /// raises its own `AttributeError` when that is truthy; when `getattr` itself
    /// raises — the attribute is *absent* — that same `AttributeError` is caught
    /// one frame out (`base.py:977`) and the instance attribute is never read
    /// either. So for a context, absent and truthy both suppress the arm, and
    /// only *present and falsy* lets it through. Both halves are measured:
    /// `Context().template` is an instance attribute with no class attribute,
    /// and `{{ c.template }}` renders `''` rather than `'None'`; a subclass with
    /// `zero = 0` and an instance `zero = 7` renders `7`.
    fn context_probes_attribute(&self, bit: &str) -> bool;

    /// `isinstance(current, BaseContext)`.
    fn is_context(&self) -> bool;

    /// `bit in dir(current)` — consulted **only** after `get_attr` raised.
    fn in_dir(&self, bit: &str) -> bool;

    /// `current[int(bit)]` — the list-index arm. Takes [`IntValue`] rather than
    /// `i64` because Python's `int()` is unbounded, and Task 3 already parses
    /// exactly Python's grammar; re-parsing here with Rust's would admit `5_0`
    /// and reject `٥`.
    fn get_index(&self, index: &IntValue) -> Step;

    /// `callable(current) and not do_not_call_in_templates and not
    /// alters_data`. The host folds all three in: only it can read the two
    /// flags, and reading them here would need a second round trip.
    fn is_callable(&self) -> bool;

    /// The handle this container *is*. `Found` names the value the loop ended
    /// on; `Missing` names the container it failed **in**, which is the object
    /// Django puts in `VariableDoesNotExist.params`.
    fn node(&self) -> NodeRef;
}

/// What one path resolved to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LookupOutcome {
    /// The path resolved. The host holds the value behind this handle.
    Found(NodeRef),
    /// `VariableDoesNotExist`, as the two arguments Django passes: the failing
    /// `bit` and the *container object* the lookup failed in. The message is
    /// not built here — `VariableDoesNotExist.__str__` does the `%`, so the
    /// host constructs `VariableDoesNotExist(msg, params=(bit, obj))` and
    /// `repr(container)` is never called unless someone stringifies it.
    Missing {
        bit: String,
        container: NodeRef,
    },
    /// Django re-raises the exception the attribute lookup raised. The host
    /// holds it **by identity** and raises it; this crate only decides that
    /// this is the arm, because `bit in dir(current)` is host knowledge.
    /// Flattening the exception to a string would lose `_culprit_node` and
    /// `__traceback__` — the reason `ErrorKind::Host` carries a message today
    /// and is scheduled to carry an object at M4.
    Reraise,
    /// Something M3 deliberately does not do. Loud rather than wrong (D3).
    Unimplemented(String),
}

/// Resolve a dotted path, in Django's arm order (`base.py:959-1029`).
pub fn resolve_lookup(lookups: &[String], mut current: Box<dyn LookupTarget>) -> LookupOutcome {
    for bit in lookups {
        // 1. Dictionary arm, guarded by `__getitem__`'s presence.
        let mut next = None;
        if current.has_getitem() {
            match current.get_item(bit) {
                Step::Found(found) => next = Some(found),
                Step::Miss => {}
                Step::Raised => return raised(bit, &*current),
            }
        }

        // 2. Attribute arm, with the `BaseContext` suppression. Django's
        //    suppression raises its own `AttributeError`, so the arm is skipped
        //    rather than attempted and the index arm still follows — and it is
        //    skipped for an *absent* class attribute too, which is why the
        //    condition reads the positive predicate and negates it.
        if next.is_none() {
            next = if current.is_context() && !current.context_probes_attribute(bit) {
                None
            } else {
                match current.get_attr(bit) {
                    Step::Found(found) => Some(found),
                    Step::Miss => None,
                    Step::Raised => return raised(bit, &*current),
                }
            };
        }

        // 3. List-index arm. `int(bit)` failing is the `ValueError` this arm
        //    swallows, so a non-numeric bit is a miss rather than a panic.
        if next.is_none() {
            if let Some(index) = python_int(bit) {
                match current.get_index(&index) {
                    Step::Found(found) => next = Some(found),
                    Step::Miss => {}
                    Step::Raised => return raised(bit, &*current),
                }
            }
        }

        match next {
            Some(found) => current = found,
            None => return missing(bit, &*current),
        }
    }

    // Django's callable arm sits in `Variable.resolve`, one frame out. M3 owns
    // it because the alternative — handing the callable back — renders
    // `<function …>`, which is wrong and quiet (D3). M8 calls it instead.
    if current.is_callable() {
        return LookupOutcome::Unimplemented(
            "calling a resolved callable (do_not_call_in_templates, alters_data) is M8's"
                .to_string(),
        );
    }
    LookupOutcome::Found(current.node())
}

fn missing(bit: &str, current: &dyn LookupTarget) -> LookupOutcome {
    LookupOutcome::Missing {
        bit: bit.to_string(),
        container: current.node(),
    }
}

/// An exception no arm swallowed leaves the loop, and whether it is re-raised
/// or reported as a missing key is `bit in dir(current)`'s answer — measured,
/// and the only difference between the two fixtures of
/// `the_property_rule_decides_between_a_re_raise_and_a_missing_key`.
///
/// `is_context` is part of the measured predicate, not a convenience: that is
/// what Django's condition reads.
fn raised(bit: &str, current: &dyn LookupTarget) -> LookupOutcome {
    if !current.is_context() && current.in_dir(bit) {
        LookupOutcome::Reraise
    } else {
        missing(bit, current)
    }
}
```

Then the fixture the Step 1 tests need. It lives in the same file, under `#[cfg(test)]`, and every field answers one predicate — which is what makes the five measured rows unit tests instead of differential cases:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn lookups(bits: &[&str]) -> Vec<String> {
        bits.iter().map(|b| b.to_string()).collect()
    }

    /// The host, standing in. One field per trait method that Python would
    /// answer, so no arm needs a real `Context`.
    ///
    /// `Debug` is implemented by hand rather than derived: `label` is read only
    /// by that impl, and rustc's `dead_code` lint deliberately ignores derived
    /// impls, so a derived `Debug` plus `-D warnings` would reject the field.
    #[derive(Clone)]
    struct Fake {
        node: NodeRef,
        /// A human label, read by `Debug` and by nothing else: it exists so an
        /// assertion failure prints `Fake(NodeRef(1) @ "'A'")` instead of a
        /// bare index.
        label: &'static str,
        map: Option<Vec<(&'static str, Fake)>>,
        seq: Vec<Fake>,
        attrs: Vec<(&'static str, Fake)>,
        /// Names whose `getattr` raises a non-`AttributeError` exception.
        raising: Vec<&'static str>,
        /// Names in `dir(self)`.
        dir: Vec<&'static str>,
        /// Class attributes that exist **and are falsy** — the only case in
        /// which a `BaseContext` reads the instance attribute. A name that is
        /// absent and a name that is truthy are both the same answer here, and
        /// both suppress the arm.
        falsy_class_attrs: Vec<&'static str>,
        is_context: bool,
        callable: bool,
    }

    impl Fake {
        /// A leaf: no `[]`, no attributes.
        fn scalar(node: u64, label: &'static str) -> Self {
            Self {
                node: NodeRef(node),
                label,
                map: None,
                seq: Vec::new(),
                attrs: Vec::new(),
                raising: Vec::new(),
                dir: Vec::new(),
                falsy_class_attrs: Vec::new(),
                is_context: false,
                callable: false,
            }
        }

        fn callable() -> Self {
            Self { callable: true, ..Self::scalar(0, "<function f>") }
        }

        /// A `BaseContext`-like container: attribute lookup reaches the instance
        /// only for the names in `falsy_class_attrs`.
        fn context(node: u64, label: &'static str) -> Self {
            Self { is_context: true, ..Self::scalar(node, label) }
        }
    }

    use std::fmt;

    /// Hand-written, and the reason is mechanical: this is the only reader of
    /// `label`, and a *derived* `Debug` is ignored by `dead_code`.
    impl fmt::Debug for Fake {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(f, "Fake({:?} @ {})", self.node, self.label)
        }
    }

    impl LookupTarget for Fake {
        fn get_item(&self, bit: &str) -> Step {
            let Some(map) = self.map.as_ref() else {
                return Step::Miss;
            };
            match map.iter().find(|(name, _)| *name == bit) {
                Some((_, found)) => Step::Found(Box::new(found.clone())),
                None => Step::Miss,
            }
        }

        fn has_getitem(&self) -> bool {
            self.map.is_some()
        }

        fn get_attr(&self, bit: &str) -> Step {
            if self.raising.contains(&bit) {
                return Step::Raised;
            }
            match self.attrs.iter().find(|(name, _)| *name == bit) {
                Some((_, found)) => Step::Found(Box::new(found.clone())),
                None => Step::Miss,
            }
        }

        fn context_probes_attribute(&self, bit: &str) -> bool {
            self.falsy_class_attrs.contains(&bit)
        }

        fn is_context(&self) -> bool {
            self.is_context
        }

        fn in_dir(&self, bit: &str) -> bool {
            self.dir.contains(&bit)
        }

        fn get_index(&self, index: &IntValue) -> Step {
            // A `Big` index is a `TypeError` on a Python list, which this arm
            // swallows — so it is a miss, not a panic.
            let IntValue::Small(index) = index else {
                return Step::Miss;
            };
            let Ok(index) = usize::try_from(*index) else {
                return Step::Miss;
            };
            match self.seq.get(index) {
                Some(found) => Step::Found(Box::new(found.clone())),
                None => Step::Miss,
            }
        }

        fn is_callable(&self) -> bool {
            self.callable
        }

        fn node(&self) -> NodeRef {
            self.node
        }
    }

    // Step 1's three tests go here, verbatim.
}
```

`Fake` needs `Clone` for the `attrs`/`seq`/`map` walks above:

```rust
    #[derive(Clone)]
    struct Fake { ... }
```

Each `Step::Found(Box::new(...))` above coerces `Box<Fake>` to `Box<dyn LookupTarget>` at the variant's field position, which is a coercion site — no `as` cast needed. If the compiler asks for one because a `match` arm's type is not yet known, spell it `Box::new(found.clone()) as Box<dyn LookupTarget>` rather than restructuring the fixture. `Fake` therefore needs `#[derive(Clone)]`, and `Step` deliberately does *not* derive `PartialEq` — its `Found` holds a trait object, so equality would have to be by node, and the tests compare `LookupOutcome` instead. That asymmetry is why `missing`, `raised` and `resolve_lookup` all return `LookupOutcome` and never `Step`.

**The `@property` rule is the one that is easy to drop and changes behaviour.** When attribute lookup raises, and the object is not a `BaseContext`, and `bit in dir(current)`, Django **re-raises** rather than falling through to list-index lookup. Without it, an exception inside a `@property` is swallowed into `VariableDoesNotExist` and the difference is visible in the rendered output. Step 1's `the_property_rule_decides_between_a_re_raise_and_a_missing_key` is that rule's only guard, and its two fixtures differ in one field.

`cargo clippy --workspace --all-targets -- -D warnings` will not object to the nine methods; it *will* object if the fixture's `map`/`attrs`/`seq` fields are never read in a configuration where a test is absent, so keep the fixture beside the tests that read it. `label` is the same trap in a subtler form: a derived `Debug` does **not** satisfy `dead_code`, so a label field "used" only by `#[derive(Debug)]` fails the build. That is why `Fake`'s `Debug` is written by hand below — it reads the field for real.

- [ ] **Step 4: Bind `_native.resolve` and `_native.render`, implement the host side of `LookupTarget`, and add the shell entry**

```rust
/// Resolve a dotted path against a context, for the differential test.
///
/// A separate entry from `render` so the resolver can be compared against
/// `Variable._resolve_lookup` on paths that no template reaches — a nested
/// missing key, a `@property` that raises, a `BaseContext` attribute — which is
/// what makes the resolver *measured* rather than merely unit-tested.
///
/// **No `parser` argument**, unlike `render`: this mirrors `_resolve_lookup`,
/// which needs no filter registry, and it raises `VariableDoesNotExist` rather
/// than substituting `string_if_invalid` (that substitution is `Variable.resolve`'s,
/// one frame out, and the shell's). An unused parameter kept "for symmetry"
/// would be a parameter the next reader has to prove is unused.
#[pyfunction]
fn resolve(py: Python<'_>, path: &str, context: Py<PyAny>) -> PyResult<Py<PyAny>> { ... }

/// Parse, materialise and render — M3's end-to-end entry.
///
/// The `parser` argument is Django's own, supplied by the caller, because
/// materialising a `FilterExpression` calls `parser.find_filter` (D5).
#[pyfunction]
fn render(py: Python<'_>, source: &str, context: Py<PyAny>, parser: Py<PyAny>) -> PyResult<String> { ... }
```

`render` reuses `_native.parse`'s body and then calls the shell's materialiser and `nodelist.render(context)`, catching nothing: a `TemplateSyntaxError` from the parse and an exception from the render must both propagate as themselves, because `tests/test_render_parity.py` compares exception *types and messages* as well as output.

**The host side of the trait**, without which `resolve` has nothing to drive `resolve_lookup` with. `template-core` cannot name a Python type, so this lives in the py crate and is the only implementation of `LookupTarget`:

```rust
use pyo3::exceptions::{PyAttributeError, PyIndexError, PyKeyError, PyTypeError, PyValueError};
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// Which arm's `except` clause is being modelled, so each swallowed set is read
/// off `base.py` in one place instead of three.
enum Arm {
    Item,
    Attr,
    Index,
}

/// Whether `err` is one this arm swallows — the only thing that decides between
/// falling through to the next arm and leaving the loop.
///
/// Django's arms are `except` clauses, so a *subclass* matches; `PyErr::matches`
/// is therefore the right test and a type-*name* comparison would not be.
/// (`PyErr::is_instance_of` is the same test, if the pinned PyO3 has it.)
fn swallowed(py: Python<'_>, err: &PyErr, arm: Arm) -> bool {
    let matches = |t| err.matches(py, t);
    match arm {
        // `base.py:964`.
        Arm::Item => matches(PyTypeError::type_object(py))
            || matches(PyAttributeError::type_object(py))
            || matches(PyKeyError::type_object(py))
            || matches(PyValueError::type_object(py))
            || matches(PyIndexError::type_object(py)),
        // `base.py:966`.
        Arm::Attr => {
            matches(PyTypeError::type_object(py)) || matches(PyAttributeError::type_object(py))
        }
        // `base.py:975`.
        Arm::Index => matches(PyIndexError::type_object(py))
            || matches(PyValueError::type_object(py))
            || matches(PyKeyError::type_object(py))
            || matches(PyTypeError::type_object(py)),
    }
}

/// A Python object, seen through `LookupTarget`.
///
/// **Holds `Py<PyAny>`, not `Bound<'py, PyAny>`, and is therefore `'static`.**
/// That is forced, not stylistic: `Step::Found` boxes its child, and a boxed
/// child that borrowed from the container it came from would make
/// `Some(found) => current = found` in `resolve_lookup` a borrow error — the new
/// `current` would outlive the old one it borrows from. So each method
/// re-acquires the token itself with `Python::attach` — the pinned PyO3's
/// spelling (`pyo3-0.29.3/src/marker.rs:414`; that crate has no
/// `Python::with_gil`), and acquisition is reentrant, so being called under
/// `_native.resolve`'s own token is fine).
#[derive(Clone)]
struct PyTarget {
    value: Py<PyAny>,
    /// Fresh per `resolve` call and shared by every container in it, so a handle
    /// is dereferenceable within the call that produced it and meaningless
    /// outside it. Deliberately **not** M2's `resolve_ref` table: that one is
    /// the shell's and resolves *descriptor* references, while these are values
    /// the caller asked for. A second persistent table would make `NodeRef`
    /// ambiguous, which is why this one is per-call and owns nothing on exit.
    arena: Rc<RefCell<Vec<Py<PyAny>>>>,
    /// The exception an arm raised, held **by identity** until the port decides
    /// whether to re-raise it (§5.2 forbids flattening it to a string: that
    /// would lose `_culprit_node` and `__traceback__`). `Rc<Cell<..>>` rather
    /// than `Cell<..>` because `get_*` takes `&self` and the decision to raise
    /// happens in `outcome_to_py`, outside this object.
    pending: Rc<Cell<Option<PyErr>>>,
}

impl PyTarget {
    fn new(value: Py<PyAny>, arena: &Rc<RefCell<Vec<Py<PyAny>>>>, pending: &Rc<Cell<Option<PyErr>>>) -> Self {
        Self {
            value,
            arena: Rc::clone(arena),
            pending: Rc::clone(pending),
        }
    }

    /// Classify one arm's result: a swallowed exception is a miss the next arm
    /// covers, anything else is stashed for `Reraise` and reported as `Raised`.
    ///
    /// Written so the token never leaves its closure — a `Python<'_>` cannot be
    /// returned out of `Python::attach`, so the classification happens inside.
    fn step(&self, result: PyResult<Py<PyAny>>, arm: Arm) -> Step {
        match result {
            Ok(found) => Step::Found(Box::new(Self::new(found, &self.arena, &self.pending))),
            Err(err) => {
                if Python::attach(|py| swallowed(py, &err, arm)) {
                    Step::Miss
                } else {
                    self.pending.set(Some(err));
                    Step::Raised
                }
            }
        }
    }

    /// `dir(current)`, **not** `hasattr`: `dir()` lists names statically, while
    /// `hasattr` invokes `getattr` — which would re-raise the very exception
    /// this predicate is being asked about. Measured: conflating the two turns
    /// the `@property` guard into a re-raise in *both* fixtures, which is
    /// exactly the distinction Step 1's pair exists to hold.
    fn dir(&self, py: Python<'_>, bit: &str) -> bool {
        let Ok(dir) = py
            .import("builtins")
            .and_then(|builtins| builtins.getattr("dir"))
            .and_then(|dir| dir.call1((self.value.bind(py),)))
        else {
            return false;
        };
        dir.contains(bit).unwrap_or(false)
    }
}

impl LookupTarget for PyTarget {
    fn has_getitem(&self) -> bool {
        Python::attach(|py| {
            self.value
                .bind(py)
                .get_type()
                .hasattr("__getitem__")
                .unwrap_or(false)
        })
    }

    fn get_item(&self, bit: &str) -> Step {
        let result = Python::attach(|py| {
            self.value
                .bind(py)
                .get_item(bit)
                .map(|found| found.unbind())
        });
        self.step(result, Arm::Item)
    }

    fn get_attr(&self, bit: &str) -> Step {
        let result = Python::attach(|py| {
            self.value
                .bind(py)
                .getattr(bit)
                .map(|found| found.unbind())
        });
        self.step(result, Arm::Attr)
    }

    fn get_index(&self, index: &IntValue) -> Step {
        let result = Python::attach(|py| -> PyResult<Py<PyAny>> {
            let key = match index {
                IntValue::Small(n) => (*n).into_pyobject(py)?.into_any().unbind(),
                // `IntValue::Big` exists because Python's `int()` is unbounded,
                // and its digits are canonical ASCII — the same route Task 3's
                // materialiser takes. Never `eval`, never a formatted source
                // string. Letting Python's own `int()` do the conversion is
                // what keeps the arm faithful for an out-of-range index.
                IntValue::Big(digits) => py
                    .import("builtins")?
                    .getattr("int")?
                    .call1((digits,))?
                    .unbind(),
            };
            Ok(self.value.bind(py).get_item(key)?.unbind())
        });
        self.step(result, Arm::Index)
    }

    fn in_dir(&self, bit: &str) -> bool {
        Python::attach(|py| self.dir(py, bit))
    }

    fn context_probes_attribute(&self, bit: &str) -> bool {
        // `getattr(type(current), bit)` raises for an *absent* attribute, and
        // that AttributeError is swallowed by Django's outer `except`
        // (`base.py:977`), so absent and truthy reach the same arm: the instance
        // attribute is not read. Only a *present and falsy* class attribute does
        // (`base.py:972-976`). Measured both ways — `{{ c.template }}` on a
        // `Context` renders `''` even though the instance holds `template=None`,
        // and a subclass with `zero = 0` renders the instance's `7`.
        Python::attach(|py| {
            self.value
                .bind(py)
                .get_type()
                .getattr(bit)
                .map(|value| !value.is_truthy().unwrap_or(true))
                .unwrap_or(false)
        })
    }

    fn is_context(&self) -> bool {
        Python::attach(|py| match base_context(py) {
            Ok(class) => self.value.bind(py).is_instance(class).unwrap_or(false),
            Err(_) => false,
        })
    }

    fn is_callable(&self) -> bool {
        // All three of Django's tests, folded in here because only the host can
        // read the two flags: `callable(current) and not getattr(current,
        // "do_not_call_in_templates", False) and not getattr(current,
        // "alters_data", False)`.
        Python::attach(|py| {
            let flag = |name: &str| {
                self.value
                    .bind(py)
                    .getattr(name)
                    .ok()
                    .and_then(|value| value.is_truthy().ok())
                    .unwrap_or(false)
            };
            self.value.bind(py).is_callable() && !flag("do_not_call_in_templates") && !flag("alters_data")
        })
    }

    fn node(&self) -> NodeRef {
        // Register in this call's arena and hand back the index. `template-core`
        // stores it and never reads it; `outcome_to_py` dereferences it below,
        // in the same call. The token stays inside the closure, as in `step`.
        Python::attach(|py| {
            let mut arena = self.arena.borrow_mut();
            arena.push(self.value.clone_ref(py));
            NodeRef(arena.len() as u64 - 1)
        })
    }
}
```

`base_context` caches Django's class object once, so `is_context` is not six imports per lookup:

```rust
static BASE_CONTEXT: GILOnceCell<Py<PyAny>> = GILOnceCell::new();

fn base_context(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    Ok(BASE_CONTEXT
        .get_or_try_init(py, || {
            Ok(py.import("django.template.base")?.getattr("BaseContext")?.unbind())
        })?
        .bind(py))
}
```

And the one place the port's verdict becomes Python's, so `Reraise`'s mechanism exists exactly once:

```rust
/// Turn the port's verdict into Python's: hand back the value behind the
/// handle, raise Django's `VariableDoesNotExist`, or **re-raise the exception
/// the host stashed by identity**.
fn outcome_to_py(
    py: Python<'_>,
    outcome: LookupOutcome,
    arena: &Rc<RefCell<Vec<Py<PyAny>>>>,
    pending: &Rc<Cell<Option<PyErr>>>,
) -> PyResult<Py<PyAny>> {
    match outcome {
        LookupOutcome::Found(node) => Ok(arena
            .borrow()
            .get(node.0 as usize)
            .ok_or_else(|| PyRuntimeError::new_err("handle escaped its resolve call"))?
            .clone_ref(py)),
        LookupOutcome::Missing { bit, container } => {
            // The container's handle, which is what Django puts in `params`.
            // It is already in the arena: `node()` put it there when the loop
            // gave up on it.
            let container = arena
                .borrow()
                .get(container.0 as usize)
                .ok_or_else(|| PyRuntimeError::new_err("handle escaped its resolve call"))?
                .clone_ref(py);
            Err(variable_does_not_exist(py, &bit, container)?)
        }
        LookupOutcome::Reraise => Err(pending
            .take()
            .expect("Reraise is only returned after an arm stashed an exception")),
        LookupOutcome::Unimplemented(message) => Err(PyNotImplementedError::new_err(message)),
    }
}

static VARIABLE_DOES_NOT_EXIST: GILOnceCell<Py<PyAny>> = GILOnceCell::new();

/// Django's own `VariableDoesNotExist`, constructed the way Django constructs
/// it — `(msg, params)`, with the message **left unformatted**.
///
/// `VariableDoesNotExist.__str__` does the `%` (`base.py:109-110`), so this
/// does not call `repr(container)`; measured on the pinned tree, the exception
/// constructs fine around a parameter whose `__repr__` raises, and raises only
/// at `str()`. Formatting here would reach back across the seam once per failed
/// lookup — the hottest failure path in the engine, since `{{ missing }}`
/// inside a `{% for %}` fails once per iteration — and would produce a message
/// Django never produces.
///
/// The class comes from `django.template.base` through a second `GILOnceCell`,
/// for the same reason `base_context` does: the class is Python's, and raising
/// a *different* exception class would make a differential comparison fail on
/// the class rather than on the message.
fn variable_does_not_exist(py: Python<'_>, bit: &str, container: Py<PyAny>) -> PyResult<PyErr> {
    let class = VARIABLE_DOES_NOT_EXIST
        .get_or_try_init(py, || -> PyResult<Py<PyAny>> {
            Ok(py
                .import("django.template.base")?
                .getattr("VariableDoesNotExist")?
                .unbind())
        })?
        .bind(py);
    // A Rust 2-tuple becomes a Python tuple, which is the shape `params` needs:
    // `self.msg % self.params`.
    Ok(PyErr::from_value(class.call1((
        "Failed lookup for key [%s] in %r",
        (bit, container),
    ))?))
}
```

**`Reraise` also carries its exception by identity across the seam, and that is not a convenience.** `pending` is a `Cell<Option<PyErr>>` on the `PyTarget` chain rather than a field of `LookupOutcome`, because `LookupOutcome` derives `PartialEq` and is compared in `template-core`'s tests where no Python exists: putting a `PyErr` in the variant would make the enum unusable in exactly the crate that decides the arm.

**The one thing to check here rather than take on trust is the handle space.** Both `Found` and `Missing` now carry a `NodeRef`, and M2's shell-side `resolve_ref` table uses indices into a materialised tree; this arena must not collide with it. It does not, because the arena is **per `resolve` call** and never persisted — an index is only ever dereferenced inside the call that pushed it, which is also why `Found`'s `ok_or_else` message says the handle *escaped* its call rather than that it was unknown.

`resolve`'s body is not free-form either. It runs Task 3's `parse_variable(path)` first, so the path is split exactly as `Variable.__init__` splits it, and then:

- `literal` set (a number, a quoted string, a `_("…")`) → materialise the literal and return it, without touching the context. That is `Variable.resolve`'s first branch, and skipping it would make `_native.resolve("5", {})` a lookup of the key `5`.
- `lookups` set → build the root `PyTarget` from the context, share one arena and one `pending` across the call, and map `resolve_lookup(&lookups, root)` through `outcome_to_py`.
- `translate` set → `LookupOutcome::Unimplemented`, naming M12 (D4), so a translated variable is a loud failure rather than an untranslated one.

`_native.resolve` and `_native.render` are registered in the module alongside `parse`, `parse_variable`, `parse_filter_expression` and `builtin_tag_names`; the module already exports `NodeRef`, so no new `m.add_class` is needed for the arena's handles.

Now the caller that supplies the parser — **this is the step that creates the file**, which D5 and the File Structure table both attribute to this task:

```python
# python/django_rust_templates/_impl/engine.py
"""The shell entry `_native.render` needs (D5).

Rust parses; Python owns the node tree (§6, §5.5). Materialising a
`FilterExpression` calls `parser.find_filter(name)` and the real
`FilterExpression.args_check`, so the parser has to be a real
`django.template.base.Parser` — and building one inside Rust would mean reaching
for `Engine().template_builtins`, which is a *host callback* and therefore M6's.
So the caller supplies it.
"""
from django.template.base import Lexer, Parser
from django.template.engine import Engine

from .. import _native


def parser_for(source):
    """Django's own `Parser`, built the way Django builds one.

    The tokens are Django's because `Parser` is Django's, and `find_filter`
    reads `self.filters`, which comes from `builtins`. Nothing in this path
    parses with it: Rust owns the parse, and this object is a filter registry
    that happens to be Django's `Parser`.
    """
    return Parser(Lexer(source).tokenize(), builtins=Engine().template_builtins)


def render_string(source, context):
    """Render `source` with the Rust engine, Django supplying the filters."""
    return _native.render(source, context, parser_for(source))
```

`Engine()` reads settings, so any caller must have configured them — see Step 5.

- [ ] **Step 5: `tests/test_render_parity.py`, and the honest statement of what it proves**

Every case renders twice — once through `render_string`, once through stock Django — and compares. A hard-coded expected output would pin what *this* engine does; comparing against Django is what makes the file a parity test.

```python
import unittest

from django.conf import settings
from django.template import Context, Engine

from django_rust_templates._impl.engine import render_string


class RenderParityTests(unittest.TestCase):
    """M3's acceptance: text, variables, dotted/index lookup and filters render.

    What this proves, and what it does not, stated here because a green render
    test is easy to over-read: the *lookup* in these cases runs through Django's
    own `Variable.resolve`, because M9 has not interposed `base` yet — so a
    passing case is evidence that the Rust **parse** is right, not that Task 7's
    resolver is. The resolver's own evidence is
    `test_the_resolver_matches_djangos_lookup`, which drives `_native.resolve`
    directly, and the differential corpus.
    """

    @classmethod
    def setUpClass(cls):
        # `Engine()` reads settings, and rendering a *number* goes through
        # `localize`, which reads `USE_I18N` — so an unconfigured settings
        # module raises `ImproperlyConfigured` from inside the first numeric
        # render (Task 7's table). `setdefault`-style, because the wider suite
        # may already have configured one.
        if not settings.configured:
            settings.configure(USE_I18N=False)
        cls.engine = Engine()

    def assert_renders_like_django(self, source, context):
        ours = render_string(source, context)
        theirs = self.engine.from_string(source).render(Context(context))
        self.assertEqual(ours, theirs, f"template: {source!r}")

    def test_text_only(self):
        self.assert_renders_like_django("hello", {})

    def test_a_bare_variable(self):
        self.assert_renders_like_django("{{ name }}", {"name": "Ada"})

    def test_a_dotted_lookup_and_a_list_index(self):
        self.assert_renders_like_django("{{ a.b }}", {"a": {"b": "B"}})
        self.assert_renders_like_django("{{ a.1 }}", {"a": [10, 20]})

    def test_a_filter_chain(self):
        # A built-in filter, resolved through the `Parser` the shell builds —
        # which is the one thing in this file that exercises D5's argument.
        self.assert_renders_like_django("{{ name|upper }}", {"name": "ada"})

    def test_a_missing_key_renders_string_if_invalid(self):
        # The `string_if_invalid` substitution is `Variable.resolve`'s and runs
        # on Django's side here, which is exactly why it matches.
        self.assert_renders_like_django("{{ nope }}", {"name": "Ada"})

    def test_the_resolver_matches_djangos_lookup(self):
        # Task 7's resolver, driven directly — the evidence the docstring above
        # says this file does not supply by rendering.
        from django.template.base import Variable, VariableDoesNotExist

        from django_rust_templates import _native

        for context, path in [({"a": {"b": "B"}}, "a.b"), ({"a": [10, 20]}, "a.1")]:
            theirs = Variable(path).resolve(Context(context))
            self.assertEqual(_native.resolve(path, context), theirs, path)

        # A missing key raises, because `_native.resolve` mirrors
        # `_resolve_lookup` rather than `resolve`.
        with self.assertRaises(VariableDoesNotExist):
            _native.resolve("nope", {"name": "Ada"})
```

The four probes of the table above (`@property`, the plain-class pair, `BaseContext`, a numeric context) belong in `tests/differential/cases.py` at Step 6 rather than here, because each one is a *path* the resolver must answer and the differential runner compares exceptions as well as values.

- [ ] **Step 6: Extend the differential corpus**

`tests/differential/cases.py` gains cases for the four things the milestone is about: a bare variable, a dotted lookup, a list index, and a filter chain — each with a context. `scripts/differential.py` compares against stock Django and its exit status gates a commit, so this is the second independent instrument on the same claim.

- [ ] **Step 7: Run everything**

Run: `source scripts/env.sh && cargo test -p template-core; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && maturin develop && python -m unittest tests.test_render_parity -v` → PASS.
Run: `source scripts/env.sh && python scripts/differential.py; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"` → exit 0.
Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"` → exit 0, 1547 unchanged.

- [ ] **Step 8: Commit**

```bash
git add crates/template-core/src crates/template-py/src python tests scripts
git commit -m "M3 task 7: the primitive resolver and the first _native.render"
```

---

### Task 8: `crates/template-core/src/parser/smartif.rs` — Django's `smartif` parser

**Files:**
- Create: `crates/template-core/src/parser/smartif.rs` (the module, with an inline
  `#[cfg(test)] mod tests`)
- Modify: `crates/template-core/src/parser/mod.rs` (add `pub mod smartif;`)
- Modify: whatever module owns `py_repr_str` — raise it from private to
  `pub(crate)`. At M2 that is `crates/template-core/src/parser/tags.rs:159`; M3's
  tag fan-out may have moved it, in which case the same one-word change moves
  with it. This is the only cross-task coupling in the task.

**Interfaces:**
- Consumes: `crate::parser::desc::NodeRef` (M2); `crate::parser::error::{Error,
  ErrorKind}` (M2); `crate::parser::tags::py_repr_str` (M2, made `pub(crate)`).
  **Nothing from M3's other tasks** — not `filter_expr.rs`, not `pyregex.rs`,
  not `resolve.rs`, not the `AttrValue` container arms.
- Produces: `smartif::{NodeId, END, MAX_NESTING_DEPTH, ErrorClass, IfError,
  Operator, LiteralValue, Literal, TokenBase, IfToken, CompileFilter, IfParser}`
  with the exact signatures in Step 2.

---

#### Declared environment

M2's defect rate was dominated by correct knowledge of Django combined with wrong
assumptions about the harness, so this task names its environment.

**Constructible at this point in the plan:**

| Thing | State at Task 8 |
|---|---|
| `crate::parser::desc::NodeRef` | M2. A `pub struct NodeRef(pub u64)`, `Copy`. The handle M2 produces only from `#[cfg(test)]` stand-ins. |
| `crate::parser::error::{Error, ErrorKind}` | M2. `Error::syntax(&str)`, `ErrorKind::Syntax(String)`, `Error.token: Option<TokenDesc>`. |
| `crate::parser::tags::py_repr_str` | M2, private at `tags.rs:159`. Exact on ASCII (it `debug_assert!`s). Made `pub(crate)` by this task. |
| `regex` | The crate's one dependency. Unused here. |
| PyO3 | **Absent from `template-core`.** This module never touches Python. |

**NOT available yet, and not imported:**

- `FilterExpression` and `Parser::compile_filter` (M3 Tasks 2–3). The
  `CompileFilter` trait below stands in for `Parser` as far as `smartif` is
  concerned, and its only M3 implementation is the test stand-in.
- `AttrValue::List` / `Map` / `NodeListRef` (M3 Task 4). Not needed.
- Anything that calls `smartif`: `{% if %}` is **M5** (§10.3's milestone table),
  so `do_if`'s port and `IfNode` are not in this task.

**Python for measuring** (only to check this task): run with the venv Python,
which has the pinned tree's `asgiref` dependency, and insert `.django` on
`sys.path` explicitly (`python -I` ignores `PYTHONPATH` and the cwd):

```bash
.venv/bin/python -I -c "import sys; sys.path.insert(0, '.django'); \
from django.template.smartif import OPERATORS; print(len(OPERATORS))"
```

Expected: `13`.

#### Scope boundary

This task delivers the `smartif` machinery **only**. `{% if %}` itself is M5, so
at M3 nothing in the engine calls this parser, and **the tests drive `IfParser`
directly with constructed tokens rather than through a template.** Do not write a
`tag_if` compile function and do not invent a wire representation for an
`{% if %}` node — both are M5's work, and a `tag_if` now would put an unowned,
untested path into the tree.

`eval` — the boolean semantics inside `infix`/`prefix` — is also M5's, because
nothing renders an `{% if %}` until then. The measured `eval` column is recorded
in the operator table below so M5 implements from a pinned spec rather than
re-reading Django, but this task does not port it.

#### The measured specification

Everything below is measured against `.django/` at 6.0.9, by importing
`django.template.smartif` and calling it. The measurement script is in Step 1's
run command.

##### 1. `OPERATORS` — the operator table

Thirteen keys, in insertion order (`smartif.py:98-116`). Each value is a **class**
(a fresh `Operator` returned by `infix`/`prefix`); `translate_token` calls it as
`op()` to make a per-token instance, and the module-level loop assigns `op.id =
key`. `bp` is the class attribute `lbp`.

| token | source | `bp` (`lbp`) | `nud` | `led` | `eval` (M5) |
|---|---|---|---|---|---|
| `or` | `infix` | 6 | `TokenBase.nud` → error | `first=left; second=expr(6)` | `x.eval or y.eval` |
| `and` | `infix` | 7 | `TokenBase.nud` → error | `first=left; second=expr(7)` | `x.eval and y.eval` |
| `not` | `prefix` | 8 | `first=expr(8); second=None` | `TokenBase.led` → error | `not x.eval` |
| `in` | `infix` | 9 | `TokenBase.nud` → error | `first=left; second=expr(9)` | `x.eval in y.eval` |
| `not in` | `infix` | 9 | `TokenBase.nud` → error | `first=left; second=expr(9)` | `x.eval not in y.eval` |
| `is` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval is y.eval` |
| `is not` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval is not y.eval` |
| `==` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval == y.eval` |
| `!=` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval != y.eval` |
| `>` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval > y.eval` |
| `>=` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval >= y.eval` |
| `<` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval < y.eval` |
| `<=` | `infix` | 10 | `TokenBase.nud` → error | `first=left; second=expr(10)` | `x.eval <= y.eval` |

All thirteen `eval` bodies are wrapped in `try/except Exception: return False`.

The two errors read, verbatim (`smartif.py:24-32`):

```text
Not expecting '%s' in this position in if tag.       # TokenBase.nud, % self.id
Not expecting '%s' as infix operator in if tag.      # TokenBase.led, % self.id
```

`Literal.id` is the constant `"literal"`, `Literal.lbp` is `0`, its `nud`
returns `self`, and its `led` is inherited (`TokenBase.led`). `EndToken.lbp` is
`0`, it sets no `id` (`TokenBase.id = None`), and its `nud` raises
`Unexpected end of expression in if tag.`. `translate_token` catches
`(KeyError, TypeError)` and falls through to `create_var`; a Rust `String` token
cannot raise the `TypeError` arm, so it is unrepresentable here — the same note
M2 records for "a tag raised a string".

**Note on the proposed contract:** its `Operator` enum listed twelve variants;
the tree has **thirteen**. The missing one is `!=` (`Ne`). And it omitted this
task's central fact: `Literal.eval` differs by *which parser built it* — base
`Literal.eval` returns the raw token, while `TemplateLiteral.eval` is
`self.value.resolve(context, ignore_failures=True)` over the compiled
`FilterExpression` (measured: a missing name resolves, it does not raise).

##### 2. Error messages, measured

The script constructs each token list, calls `.parse()`, and prints
`type(e).__name__` and `str(e)`. `IfParser([])` for the empty case; tokens are
`str.split()` of the expression.

**Base `IfParser` (`error_class = ValueError`):**

| constructed tokens | exception | `str()` |
|---|---|---|
| `[]` (also `{% if %}`) | `ValueError` | `Unexpected end of expression in if tag.` |
| `["not"]` | `ValueError` | `Unexpected end of expression in if tag.` |
| `["1", "and"]` | `ValueError` | `Unexpected end of expression in if tag.` |
| `["x", "or"]` | `ValueError` | `Unexpected end of expression in if tag.` |
| `["1", "+"]` | `ValueError` | `Unused ''+'' at end of if expression.` |
| `["(", "1"]` | `ValueError` | `Unused ''1'' at end of if expression.` |
| `["1", ")"]` | `ValueError` | `Unused '')'' at end of if expression.` |
| `["foo", "bar"]` | `ValueError` | `Unused ''bar'' at end of if expression.` |
| `["foo", "xor", "bar"]` | `ValueError` | `Unused ''xor'' at end of if expression.` |
| `["1", "and", "2", "3"]` | `ValueError` | `Unused ''3'' at end of if expression.` |
| `["1", "not", "2"]` | `ValueError` | `Not expecting 'not' as infix operator in if tag.` |
| `["or", "1"]` | `ValueError` | `Not expecting 'or' in this position in if tag.` |
| `["in", "1"]` | `ValueError` | `Not expecting 'in' in this position in if tag.` |
| `["1", "or", "or", "2"]` | `ValueError` | `Not expecting 'or' in this position in if tag.` |
| `["1", "in", "in", "2"]` | `ValueError` | `Not expecting 'in' in this position in if tag.` |
| `[""]` (one empty **string** token) | — | **parses**, to `(literal '')` |

The doubled quotes in `Unused ''bar''` are not a typo: the message is
`"Unused '%s' …" % current_token.display()`, and base `Literal.display()` is
`repr(self.value)`, so a string value contributes its own quotes. This is the
**second** difference between the two classes (see §4).

**`TemplateIfParser` (direct construction, `error_class = TemplateSyntaxError`),
same constructed tokens, with a real engine parser:**

| constructed tokens | exception | `str()` | stage |
|---|---|---|---|
| `[]` | `TemplateSyntaxError` | `Unexpected end of expression in if tag.` | `parse` |
| `["not"]` | `TemplateSyntaxError` | `Unexpected end of expression in if tag.` | `parse` |
| `["1", "and"]` | `TemplateSyntaxError` | `Unexpected end of expression in if tag.` | `parse` |
| `["or", "1"]` | `TemplateSyntaxError` | `Not expecting 'or' in this position in if tag.` | `parse` |
| `["1", "not", "2"]` | `TemplateSyntaxError` | `Not expecting 'not' as infix operator in if tag.` | `parse` |
| `["foo", "bar"]` | `TemplateSyntaxError` | `Unused 'bar' at end of if expression.` | `parse` |
| `["foo", "xor", "bar"]` | `TemplateSyntaxError` | `Unused 'xor' at end of if expression.` | `parse` |
| `["1", "+"]` | `TemplateSyntaxError` | `Invalid character ('+') in variable name: '+'` | **`__init__`** |
| `["(", "1"]` | `TemplateSyntaxError` | `Could not parse the remainder: '(' from '('` | **`__init__`** |
| `["1", ")"]` | `TemplateSyntaxError` | `Could not parse the remainder: ')' from ')'` | **`__init__`** |
| `[""]` | — | **parses**, to `(literal <FilterExpression ''>)` | — |

The last three rows are the reason the template parser is built by a **fallible**
constructor in Rust: `TemplateIfParser.create_var` calls
`self.template_parser.compile_filter(value)` (`defaulttags.py:928-929`), and
Django's `IfParser.__init__` calls `create_var` for every non-operator token via
`translate` — so a bad filter chain fails during `__init__`, before `parse` is
ever reached. Through the real tag (`{% if … %}`) the token is a single lexer
token: `{% if (1 %}` gives `Could not parse the remainder: '(1' from '(1'`,
because the whole `(1` is one token.

##### 3. The `error_class` difference, and what actually attaches `.token`

The two classes differ in `error_class` (`smartif.py:157`,
`defaulttags.py:922`): `ValueError` for `IfParser`, `TemplateSyntaxError` for
`TemplateIfParser`. The task brief says the latter, and not the former, gets a
`.token` from "the parser's error protocol". **Measured, that is not quite
right, and the correction matters for the Rust shape:**

| where the failure is raised | exception | `hasattr(e, "token")` |
|---|---|---|
| `IfParser(tokens).parse()` called directly | `ValueError` | **`False`** |
| `TemplateIfParser(parser, tokens).parse()` called directly | `TemplateSyntaxError` | **`False`** |
| `IfParser(tokens).parse()` inside a compile function | `ValueError` | **`True`** |
| `{% if … %}` (real `do_if` → `TemplateIfParser`) | `TemplateSyntaxError` | **`True`** |

`Parser.error` (`base.py:625-635`) attaches `.token` to **any** `Exception`
lacking one, and `Parser.parse` wraps every compile function in
`except Exception as e: raise self.error(token, e)` (`base.py:585-587`). So a
`ValueError` raised inside a compile function also gets a token — measured, with
a custom tag that calls the base `IfParser` (`ValueError path … hasattr
token=True | token=<Block token: "ifx 1 +...">`).

The real, measured distinction is therefore:

- **the exception class differs on the same input** — the difference the task
  pins, and what `error_class` names;
- `.token` comes from the parser's error protocol, not from the class, and
  **no built-in tag calls the base `IfParser`**, so in the shipped engine every
  `smartif` failure is a `TemplateSyntaxError` *with* a `.token` (the `do_if`
  rows above). The base parser's `ValueError` path has no `.token` only because
  nothing reaches it through a compile function.

The task's tests pin the class difference directly; the `.token` column above is
recorded so M5 wires the two correctly (see "For M5" below).

##### 4. The second difference: `display` — repr versus raw text

`Literal.display()` returns `repr(self.value)` (`smartif.py:133-134`), while
`TemplateLiteral.display()` returns `self.text`, the raw token
(`defaulttags.py:914-915`). Every `Unused '…'` message interpolates `display()`,
so the same leftover literal renders differently through the two classes:

```text
IfParser(["foo", "bar"])            -> ValueError: Unused ''bar'' at end of if expression.
TemplateIfParser(parser, ["foo","bar"]) -> TemplateSyntaxError: Unused 'bar' at end of if expression.
```

So `Literal` must carry a **pre-rendered `display` string**, not derive it from
its value: it is `repr(token)` for the base parser and the raw token for the
template parser. `repr(token)` is `py_repr_str` (M2) — the function that already
exists for exactly this and whose ASCII boundary is documented and asserted.
`Unused` is the **only** message `display()` reaches, and the leftover token is
always a `Literal` (an operator in that position always takes `nud`/`led`, which
raise their own message first).

##### 5. Recursion and repetition: measured, not assumed

`expression` (`smartif.py:204-212`) is recursive in the **prefix** case only:
`nud` calls `expression(bp)` for `not`. Infix is a `while` loop
(`while rbp < self.current_token.lbp`), so it is iterative. Measured:

| input | result |
|---|---|
| `not` × 498 + `x` | parses |
| `not` × 499 + `x` | `RecursionError: maximum recursion depth exceeded` |
| `a and b and …` × 100 000 | parses |
| `a and` + `not` × 400 + `y` | parses |

`sys.getrecursionlimit()` is `1000` and each `not` costs about two frames, which
is the whole of the bound. **The exact number is not a constant and must not be
pinned as one:** an independent measurement of the same input from a probe that
itself sits one frame shallower reads `498` parses / `499` raises where a
shallower-still caller reads one higher. It moves with the caller's own stack
depth, so the reproducible fact is "about two frames per `not`, against a limit
of 1000, i.e. roughly 500" — not `498`. This is the same shape of error as the
count disputes the M2 round recorded: two right answers to two unstated
questions. **There is no explicit bound in Django** — the only limit is the
interpreter's.

Rust has no interpreter recursion limit, so a hostile `not`-chain would overflow
the native stack, which **aborts** the process (SIGSEGV) rather than raising and
so cannot be caught. This task therefore adds an explicit guard
(`MAX_NESTING_DEPTH`); it is the only place this port deliberately diverges from
Django's observable behaviour, and the divergence is flagged as a review
question below.

#### The Rust shape, and why it amends the proposed contract

Three amendments, each forced by a measurement above:

1. **`TokenBase` carries `kind: IfToken`, not `value: String`.** Django's
   `TokenBase.value` is `None` for every operator and its `id` is the
   `OPERATORS` key (`"or"`, …) or the constant `"literal"`; an operator has no
   string value to put in the proposed `value` field. Folding `id` and `value`
   into the token enum keeps each in the one place it is defined.
2. **`IfToken::Literal` holds a `Literal`, not a bare `NodeRef`.** The base
   `IfParser.create_var` is `Literal(value)` with `value` the raw token
   (`smartif.py:214-215`) — it does **not** call `compile_filter`; only
   `TemplateIfParser.create_var` does. A bare `NodeRef` cannot represent the
   base parser's raw string, and the proposed contract's claim that
   `Literal.__init__` calls `compile_filter` is not what the pinned source says.
   `LiteralValue` is the two-variant union the source actually has.
3. **`Operator` gains `Ne` (`!=`)**, for thirteen variants, and `IfParser` has
   **no lifetime**: `TemplateIfParser` compiles every literal during
   `__init__` (`translate` → `create_var`), so the parser stores no borrow of the
   compile host.

`LiteralValue::Compiled` holds the `NodeRef` handle the brief asks for, matching
`AttrValue::Ref` — the seam `desc.rs` reserves for a host-owned value. **Question
for review (one sentence):** if M3 Task 2's `Parser::compile_filter` ends up
returning a Rust `FilterExpression` *descriptor* rather than a host handle (the
M3 plan's D2 says the chain is parsed in Rust and materialised in Python), then
`LiteralValue::Compiled` should hold that descriptor instead of `NodeRef`, and
this one variant changes; no descriptor shape is designed here, because an
`{% if %}` node's wire form is M5's.

- [ ] **Step 1: Raise `py_repr_str` to `pub(crate)`**

The base parser's `Literal.display()` is Python's `repr` of the token, and this
crate already has the exact-on-ASCII helper for it. Find it and widen its
visibility.

```bash
grep -rn "fn py_repr_str" crates/template-core/src/
```

Expected (at M2 head): `crates/template-core/src/parser/tags.rs:159:fn py_repr_str(s: &str) -> String {`

Change `fn py_repr_str` to `pub(crate) fn py_repr_str`. Do not widen `py_list_repr`
and do not change the body: its ASCII `debug_assert!` is the documented boundary,
and the base parser — which is the only caller of it here — is unreachable from
the engine, so its tokens are test-supplied and ASCII.

- [ ] **Step 2: Write `crates/template-core/src/parser/smartif.rs`**

```rust
//! `django.template.smartif`: the `{% if %}` expression parser.
//!
//! A direct port of Django's top-down parser (`smartif.py`, 215 lines at
//! 6.0.9). Rust owns the parse and Python owns the tree that renders, so this
//! module builds the parse tree and detects syntax errors — and nothing in the
//! engine calls it yet: `{% if %}` is M5, so `smartif` is machinery with no
//! consumer at M3. The one caller Django has, `TemplateIfParser`
//! (`defaulttags.py:921`), differs from the base `IfParser` in exactly two
//! ways, both measured against the pinned tree and both pinned by tests here:
//!
//! - `error_class`: the base parser raises `ValueError`, the subclass raises
//!   `TemplateSyntaxError` (`smartif.py:157`, `defaulttags.py:922`);
//! - `create_var`/`display`: the base parser wraps the raw token and displays
//!   its `repr`, while the subclass compiles the token and displays it raw —
//!   so the subclass runs `compile_filter` during `__init__` and can fail
//!   before `parse` is reached.
//!
//! `nud`/`led` and the operator table are ported; `eval` (the boolean
//! semantics) is M5's, because nothing renders an `{% if %}` until then.

use crate::parser::desc::NodeRef;
use crate::parser::error::Error;
use crate::parser::tags::py_repr_str;

/// An index into an [`IfParser`]'s arena.
///
/// Django's nodes point at each other directly (`operator.first = left`); Rust
/// cannot, so a node is addressed by index into the arena the parser owns.
pub type NodeId = usize;

/// The arena index of the `EndToken` sentinel.
///
/// Django's `EndToken` is a module-level singleton (`EndToken = EndToken()`,
/// `smartif.py:153`) and `parse` compares against it with `is`.
pub const END: NodeId = 0;

/// The depth `expression` may nest before it refuses.
///
/// Django has **no** explicit bound. Measured against the pinned tree: 497
/// `not`s parse and 498 raises `RecursionError` (CPython's default recursion
/// limit is 1000, about two frames per prefix operator), while 100 000 `and`s
/// parse because infix parsing is a loop. Rust has no interpreter recursion
/// limit, so without a guard a hostile template overflows the native stack —
/// which aborts the process rather than raising, and so cannot be caught. This
/// makes the guard, not the stack, the thing that fires. The value and the
/// message are deliberately not Django's: Django's behaviour here is an
/// interpreter artifact, not a specification (see the review questions).
pub const MAX_NESTING_DEPTH: usize = 1000;

/// Which Python exception a `smartif` parse failure becomes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorClass {
    /// `smartif.IfParser.error_class = ValueError`.
    Value,
    /// `defaulttags.TemplateIfParser.error_class = TemplateSyntaxError`.
    TemplateSyntax,
}

/// A `smartif` parse failure: the message, and the class it is raised as.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IfError {
    pub class: ErrorClass,
    pub message: String,
}

impl IfError {
    fn new(class: ErrorClass, message: impl Into<String>) -> Self {
        Self {
            class,
            message: message.into(),
        }
    }
}

/// One of `smartif.OPERATORS`' thirteen keys.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operator {
    Or,
    And,
    Not,
    In,
    NotIn,
    Is,
    IsNot,
    Eq,
    Ne,
    Gt,
    Gte,
    Lt,
    Lte,
}

impl Operator {
    /// `OPERATORS[key]` — `translate_token`'s lookup, `None` on the `KeyError`
    /// that Django then turns into `create_var`. (`translate_token` also
    /// catches `TypeError`, for an unhashable token; a `String` token cannot
    /// raise it, so that arm is unrepresentable here.)
    pub fn from_key(key: &str) -> Option<Self> {
        Some(match key {
            "or" => Self::Or,
            "and" => Self::And,
            "not" => Self::Not,
            "in" => Self::In,
            "not in" => Self::NotIn,
            "is" => Self::Is,
            "is not" => Self::IsNot,
            "==" => Self::Eq,
            "!=" => Self::Ne,
            ">" => Self::Gt,
            ">=" => Self::Gte,
            "<" => Self::Lt,
            "<=" => Self::Lte,
            _ => return None,
        })
    }

    /// `operator.id` — the `OPERATORS` key, interpolated into every message.
    pub fn key(self) -> &'static str {
        match self {
            Self::Or => "or",
            Self::And => "and",
            Self::Not => "not",
            Self::In => "in",
            Self::NotIn => "not in",
            Self::Is => "is",
            Self::IsNot => "is not",
            Self::Eq => "==",
            Self::Ne => "!=",
            Self::Gt => ">",
            Self::Gte => ">=",
            Self::Lt => "<",
            Self::Lte => "<=",
        }
    }

    /// `operator.lbp` — the binding power. Precedence follows Python's.
    pub fn bp(self) -> u8 {
        match self {
            Self::Or => 6,
            Self::And => 7,
            Self::Not => 8,
            Self::In | Self::NotIn => 9,
            Self::Is | Self::IsNot | Self::Eq | Self::Ne | Self::Gt | Self::Gte | Self::Lt
            | Self::Lte => 10,
        }
    }

    /// Whether this operator was made by `prefix` (`not`) rather than `infix`.
    ///
    /// `not` is the only prefix operator in `OPERATORS`; its `nud` is its own
    /// and its `led` is `TokenBase.led`. Every other operator is the reverse.
    pub fn is_prefix(self) -> bool {
        self == Self::Not
    }
}

/// `smartif.Literal.value` / `defaulttags.TemplateLiteral.value`.
#[derive(Debug, Clone, PartialEq)]
pub enum LiteralValue {
    /// `IfParser.create_var` is `Literal(value)`, with `value` the raw token;
    /// `eval` returns it unchanged. `smartif.py:214-215`.
    Raw(String),
    /// `TemplateIfParser.create_var` is
    /// `TemplateLiteral(self.template_parser.compile_filter(value), value)`.
    /// The compiled `FilterExpression` is host-owned, so it crosses as a
    /// [`NodeRef`] handle. `defaulttags.py:928-929`.
    Compiled(NodeRef),
}

/// `smartif.Literal` / `defaulttags.TemplateLiteral`.
///
/// `display` is pre-rendered because the base class's `display()` is Python's
/// `repr(self.value)` and the subclass's is `self.text`; deriving it from
/// `value` would lose the difference between them.
#[derive(Debug, Clone, PartialEq)]
pub struct Literal {
    pub value: LiteralValue,
    /// `Literal.display()` / `TemplateLiteral.display()`.
    pub display: String,
}

/// `smartif.TokenBase`: a translated token, and after `parse` a tree node.
///
/// Django has one class hierarchy for both — `translate` makes a token, `nud`
/// and `led` mutate it into a tree node, and `parse` returns the root — so this
/// one struct is both, with `first`/`second` addressing the arena.
#[derive(Debug, Clone, PartialEq)]
pub struct TokenBase {
    pub kind: IfToken,
    pub first: Option<NodeId>,
    pub second: Option<NodeId>,
}

/// The `TokenBase` subclass a node is — Django's `id` and `value`, folded.
#[derive(Debug, Clone, PartialEq)]
pub enum IfToken {
    /// An `infix` or `prefix` operator instance.
    Operator(Operator),
    /// `Literal` / `TemplateLiteral`.
    Literal(Literal),
    /// `EndToken`.
    End,
}

/// A cheap copy of a node's kind, so `nud`/`led` can match on it and then
/// mutate the arena without holding a borrow of the kind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tag {
    Op(Operator),
    Literal,
    End,
}

/// The `parser` a `TemplateIfParser` is built with.
///
/// `TemplateIfParser.create_var` calls `self.template_parser.compile_filter(
/// value)` and stores the result. The result is a host-owned
/// `FilterExpression`, which `template-core` cannot hold, so it crosses as the
/// [`NodeRef`] handle `desc.rs` reserves. `template-core`'s real
/// `compile_filter` (M3 Tasks 2-3) is not wired here: nothing calls `smartif`
/// at M3, so the only M3 implementation is the test stand-in.
pub trait CompileFilter {
    /// `Parser.compile_filter(token)`.
    fn compile_filter(&self, token: &str) -> Result<NodeRef, Error>;
}

/// `smartif.IfParser` / `defaulttags.TemplateIfParser`.
pub struct IfParser {
    /// Every node: the `End` sentinel at [`END`], then one per token.
    arena: Vec<TokenBase>,
    /// The arena indices of the translated tokens, in source order —
    /// Django's `self.tokens`.
    tokens: Vec<NodeId>,
    /// `self.pos`.
    pos: usize,
    /// `self.current_token`, by arena index.
    current: NodeId,
    /// `self.error_class`.
    error_class: ErrorClass,
    /// Prefix-nesting depth, for [`MAX_NESTING_DEPTH`].
    depth: usize,
}

impl IfParser {
    /// `smartif.IfParser(tokens)` — `error_class = ValueError`.
    ///
    /// Infallible because the base `create_var` cannot fail: it is
    /// `Literal(value)`. The proposed contract had `Literal.__init__` calling
    /// `compile_filter`; the pinned source does not, and only
    /// `TemplateIfParser.create_var` does.
    pub fn new(tokens: Vec<String>) -> Self {
        let mut arena = vec![end_node()];
        let mut ids = Vec::new();
        for key in merge_tokens(&tokens) {
            let node = match Operator::from_key(&key) {
                Some(op) => operator_node(op),
                None => literal_node(Literal {
                    display: py_repr_str(&key),
                    value: LiteralValue::Raw(key),
                }),
            };
            ids.push(arena.len());
            arena.push(node);
        }
        Self::assemble(arena, ids, ErrorClass::Value)
    }

    /// `defaulttags.TemplateIfParser(parser, tokens)` — `error_class =
    /// TemplateSyntaxError`.
    ///
    /// Fallible because `compile_filter` runs during `__init__` (Django's
    /// `translate` -> `create_var`) and can raise.
    pub fn template(compiler: &dyn CompileFilter, tokens: Vec<String>) -> Result<Self, Error> {
        let mut arena = vec![end_node()];
        let mut ids = Vec::new();
        for key in merge_tokens(&tokens) {
            let node = match Operator::from_key(&key) {
                Some(op) => operator_node(op),
                None => literal_node(Literal {
                    value: LiteralValue::Compiled(compiler.compile_filter(&key)?),
                    display: key,
                }),
            };
            ids.push(arena.len());
            arena.push(node);
        }
        Ok(Self::assemble(arena, ids, ErrorClass::TemplateSyntax))
    }

    fn assemble(arena: Vec<TokenBase>, tokens: Vec<NodeId>, error_class: ErrorClass) -> Self {
        let mut parser = Self {
            arena,
            tokens,
            pos: 0,
            current: END,
            error_class,
            depth: 0,
        };
        // `IfParser.__init__` ends with `self.current_token = self.next_token()`.
        parser.current = parser.next_token();
        parser
    }

    /// `IfParser.parse()`.
    pub fn parse(&mut self) -> Result<NodeId, IfError> {
        let root = self.expression(0)?;
        if self.current != END {
            return Err(IfError::new(
                self.error_class,
                format!(
                    "Unused '{}' at end of if expression.",
                    self.display(self.current),
                ),
            ));
        }
        Ok(root)
    }

    /// The arena node `id`.
    pub fn node(&self, id: NodeId) -> &TokenBase {
        &self.arena[id]
    }

    /// `self.error_class`.
    pub fn error_class(&self) -> ErrorClass {
        self.error_class
    }

    fn tag(&self, id: NodeId) -> Tag {
        match &self.arena[id].kind {
            IfToken::Operator(op) => Tag::Op(*op),
            IfToken::Literal(_) => Tag::Literal,
            IfToken::End => Tag::End,
        }
    }

    /// `IfParser.next_token` — `EndToken` once the list is exhausted, and it
    /// does not advance, so repeated calls return it.
    fn next_token(&mut self) -> NodeId {
        if self.pos >= self.tokens.len() {
            END
        } else {
            let id = self.tokens[self.pos];
            self.pos += 1;
            id
        }
    }

    /// `IfParser.expression(rbp=0)`.
    fn expression(&mut self, rbp: u8) -> Result<NodeId, IfError> {
        self.depth += 1;
        if self.depth > MAX_NESTING_DEPTH {
            self.depth -= 1;
            return Err(IfError::new(
                self.error_class,
                format!("If expression is nested more than {MAX_NESTING_DEPTH} deep in if tag."),
            ));
        }

        let t = self.current;
        self.current = self.next_token();
        let mut left = self.nud(t)?;

        while rbp < self.lbp(self.current) {
            let t = self.current;
            self.current = self.next_token();
            left = self.led(t, left)?;
        }

        self.depth -= 1;
        Ok(left)
    }

    /// `TokenBase.nud` / `prefix.Operator.nud` / `Literal.nud` / `EndToken.nud`.
    fn nud(&mut self, t: NodeId) -> Result<NodeId, IfError> {
        match self.tag(t) {
            Tag::Op(op) if op.is_prefix() => {
                let first = self.expression(op.bp())?;
                self.arena[t].first = Some(first);
                self.arena[t].second = None;
                Ok(t)
            }
            Tag::Op(op) => Err(IfError::new(
                self.error_class,
                format!("Not expecting '{}' in this position in if tag.", op.key()),
            )),
            Tag::Literal => Ok(t),
            Tag::End => Err(IfError::new(
                self.error_class,
                "Unexpected end of expression in if tag.".to_string(),
            )),
        }
    }

    /// `infix.Operator.led` / `TokenBase.led`.
    ///
    /// Only the first two arms are reachable from `expression`: the loop guard
    /// is `rbp < lbp(current)`, and a literal and `EndToken` both have
    /// `lbp == 0`. The other two mirror `TokenBase.led` for completeness.
    fn led(&mut self, t: NodeId, left: NodeId) -> Result<NodeId, IfError> {
        match self.tag(t) {
            Tag::Op(op) if !op.is_prefix() => {
                let second = self.expression(op.bp())?;
                self.arena[t].first = Some(left);
                self.arena[t].second = Some(second);
                Ok(t)
            }
            Tag::Op(op) => Err(IfError::new(
                self.error_class,
                format!("Not expecting '{}' as infix operator in if tag.", op.key()),
            )),
            Tag::Literal => Err(IfError::new(
                self.error_class,
                "Not expecting 'literal' as infix operator in if tag.".to_string(),
            )),
            Tag::End => Err(IfError::new(
                self.error_class,
                "Not expecting 'None' as infix operator in if tag.".to_string(),
            )),
        }
    }

    /// `TokenBase.lbp` — the binding power, 0 for a literal or `EndToken`.
    fn lbp(&self, id: NodeId) -> u8 {
        match &self.arena[id].kind {
            IfToken::Operator(op) => op.bp(),
            IfToken::Literal(_) | IfToken::End => 0,
        }
    }

    /// `TokenBase.display()`.
    fn display(&self, id: NodeId) -> String {
        match &self.arena[id].kind {
            IfToken::Operator(op) => op.key().to_string(),
            IfToken::Literal(lit) => lit.display.clone(),
            // `EndToken.display()` is `TokenBase.display()` -> `self.id`, which
            // is `None`. `parse` checks identity against `END` first, so this is
            // unreachable; it mirrors `"%s" % None`.
            IfToken::End => "None".to_string(),
        }
    }
}

/// `IfParser.__init__`'s token merge: `is`+`not` -> `is not`, `not`+`in` ->
/// `not in`, and nothing else (`smartif.py:160-171`).
fn merge_tokens(tokens: &[String]) -> Vec<String> {
    let mut out = Vec::with_capacity(tokens.len());
    let mut i = 0;
    while i < tokens.len() {
        let token = &tokens[i];
        if token == "is" && i + 1 < tokens.len() && tokens[i + 1] == "not" {
            out.push("is not".to_string());
            i += 2;
        } else if token == "not" && i + 1 < tokens.len() && tokens[i + 1] == "in" {
            out.push("not in".to_string());
            i += 2;
        } else {
            out.push(token.clone());
            i += 1;
        }
    }
    out
}

fn end_node() -> TokenBase {
    TokenBase {
        kind: IfToken::End,
        first: None,
        second: None,
    }
}

fn operator_node(op: Operator) -> TokenBase {
    TokenBase {
        kind: IfToken::Operator(op),
        first: None,
        second: None,
    }
}

fn literal_node(literal: Literal) -> TokenBase {
    TokenBase {
        kind: IfToken::Literal(literal),
        first: None,
        second: None,
    }
}
```

- [ ] **Step 3: Register the module**

Add one line to `crates/template-core/src/parser/mod.rs`, beside the other
`pub mod` lines:

```rust
pub mod smartif;
```

- [ ] **Step 4: Add the tests**

Append to `crates/template-core/src/parser/smartif.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    /// One row of a measured error table: `(tokens, expected message)`.
    /// Named because `clippy::type_complexity` rejects the anonymous tuple.
    type ErrorCase = (&'static [&'static str], &'static str);

    fn tokens(case: &[&str]) -> Vec<String> {
        case.iter().map(|s| (*s).to_string()).collect()
    }

    /// `TokenBase.__repr__` / `Literal.__repr__` (`smartif.py:40-43`,
    /// `142-143`), so a parsed tree can be compared with Django's own `repr`.
    fn repr(parser: &IfParser, id: NodeId) -> String {
        match &parser.node(id).kind {
            IfToken::End => "()".to_string(),
            IfToken::Literal(lit) => match &lit.value {
                LiteralValue::Raw(value) => format!("(literal {})", py_repr_str(value)),
                LiteralValue::Compiled(_) => "(literal <compiled>)".to_string(),
            },
            IfToken::Operator(op) => {
                let mut parts = vec![op.key().to_string()];
                if let Some(first) = parser.node(id).first {
                    parts.push(repr(parser, first));
                }
                if let Some(second) = parser.node(id).second {
                    parts.push(repr(parser, second));
                }
                format!("({})", parts.join(" "))
            }
        }
    }

    /// An `IfParser` built from `case` and parsed; the base entry point.
    fn parse_base(case: &[&str]) -> Result<NodeId, IfError> {
        IfParser::new(tokens(case)).parse()
    }

    /// A lenient `compile_filter`: every token compiles to the same handle.
    ///
    /// Lenient on purpose — the Rust tests stand in for Django's real
    /// `compile_filter`, and Django's rejects tokens like `+` during
    /// `__init__`. The template-parser rows below use tokens Django's accepts,
    /// so the stand-in is faithful for those rows.
    struct FixedCompiler;

    impl CompileFilter for FixedCompiler {
        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(41))
        }
    }

    /// A `compile_filter` that refuses, to pin the construction-time failure.
    struct RefusingCompiler;

    impl CompileFilter for RefusingCompiler {
        fn compile_filter(&self, token: &str) -> Result<NodeRef, Error> {
            Err(Error::syntax(format!("Invalid filter: '{token}'")))
        }
    }

    /// The template entry point, with the lenient compiler.
    fn parse_template(case: &[&str]) -> Result<NodeId, IfError> {
        IfParser::template(&FixedCompiler, tokens(case))
            .expect("FixedCompiler compiles every token")
            .parse()
    }

    #[test]
    fn operator_table_matches_the_pinned_tree() {
        // Every key of `smartif.OPERATORS` at 6.0.9, its binding power, and
        // whether it came from `prefix` (`not`) or `infix` (the other 12).
        let table: [(Operator, &str, u8, bool); 13] = [
            (Operator::Or, "or", 6, false),
            (Operator::And, "and", 7, false),
            (Operator::Not, "not", 8, true),
            (Operator::In, "in", 9, false),
            (Operator::NotIn, "not in", 9, false),
            (Operator::Is, "is", 10, false),
            (Operator::IsNot, "is not", 10, false),
            (Operator::Eq, "==", 10, false),
            (Operator::Ne, "!=", 10, false),
            (Operator::Gt, ">", 10, false),
            (Operator::Gte, ">=", 10, false),
            (Operator::Lt, "<", 10, false),
            (Operator::Lte, "<=", 10, false),
        ];
        for (op, key, bp, prefix) in table {
            assert_eq!(Operator::from_key(key), Some(op), "from_key({key:?})");
            assert_eq!(op.key(), key, "key of {op:?}");
            assert_eq!(op.bp(), bp, "bp of {key:?}");
            assert_eq!(op.is_prefix(), prefix, "prefix of {key:?}");
        }
        // `KeyError` in `translate_token`: any token that is not a key.
        assert_eq!(Operator::from_key("xor"), None);
        assert_eq!(Operator::from_key(""), None);
        assert_eq!(Operator::from_key("literal"), None);
    }

    #[test]
    fn translate_merges_is_not_and_not_in_into_one_token() {
        // `IfParser.__init__` folds exactly these two pairs before the lookup.
        let parser = IfParser::new(tokens(&["is", "not"]));
        assert_eq!(parser.tokens.len(), 1);
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::IsNot),
        );

        let parser = IfParser::new(tokens(&["not", "in"]));
        assert_eq!(parser.tokens.len(), 1);
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::NotIn),
        );

        // The merge is exactly those two pairs, not "is" + any second word.
        let parser = IfParser::new(tokens(&["is", "in"]));
        assert_eq!(parser.tokens.len(), 2);
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::Is),
        );
        assert_eq!(
            parser.node(parser.tokens[1]).kind,
            IfToken::Operator(Operator::In),
        );

        let parser = IfParser::new(tokens(&["not", "not"]));
        assert_eq!(parser.tokens.len(), 2);
    }

    #[test]
    fn translate_maps_operator_keys_and_turns_everything_else_into_a_literal() {
        let parser = IfParser::new(tokens(&["or", "foo", "(", "+"]));
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Operator(Operator::Or),
        );
        for (id, raw) in [
            (parser.tokens[1], "foo"),
            (parser.tokens[2], "("),
            (parser.tokens[3], "+"),
        ] {
            assert_eq!(
                parser.node(id).kind,
                IfToken::Literal(Literal {
                    value: LiteralValue::Raw(raw.to_string()),
                    display: py_repr_str(raw),
                }),
            );
        }
    }

    #[test]
    fn parse_builds_the_tree_djangos_operator_precedence_produces() {
        // Each expected string is Django's own `repr()` of the parsed tree,
        // measured on the pinned tree.
        for (case, expected) in [
            (
                &["1", "or", "2", "and", "3"][..],
                "(or (literal '1') (and (literal '2') (literal '3')))",
            ),
            (
                &["a", "and", "b", "or", "c"][..],
                "(or (and (literal 'a') (literal 'b')) (literal 'c'))",
            ),
            (&["not", "not", "x"][..], "(not (not (literal 'x')))"),
            (
                &["1", "is", "not", "2"][..],
                "(is not (literal '1') (literal '2'))",
            ),
            (
                &["1", "is", "not", "not", "2"][..],
                "(is not (literal '1') (not (literal '2')))",
            ),
        ] {
            let mut parser = IfParser::new(tokens(case));
            let root = parser.parse().expect("parses");
            assert_eq!(repr(&parser, root), expected, "tree for {case:?}");
        }
    }

    #[test]
    fn parse_errors_match_the_pinned_trees_messages() {
        // The full measured table for the base `IfParser`. Strings are
        // Django's, verbatim, including the doubled quotes in `Unused ''x''`.
        let cases: [ErrorCase; 12] = [
            (&[], "Unexpected end of expression in if tag."),
            (&["not"], "Unexpected end of expression in if tag."),
            (&["1", "and"], "Unexpected end of expression in if tag."),
            (&["x", "or"], "Unexpected end of expression in if tag."),
            (&["1", "+"], "Unused ''+'' at end of if expression."),
            (&["(", "1"], "Unused ''1'' at end of if expression."),
            (&["1", ")"], "Unused '')'' at end of if expression."),
            (&["foo", "bar"], "Unused ''bar'' at end of if expression."),
            (
                &["foo", "xor", "bar"],
                "Unused ''xor'' at end of if expression.",
            ),
            (
                &["1", "and", "2", "3"],
                "Unused ''3'' at end of if expression.",
            ),
            (
                &["1", "not", "2"],
                "Not expecting 'not' as infix operator in if tag.",
            ),
            (
                &["or", "1"],
                "Not expecting 'or' in this position in if tag.",
            ),
        ];
        for (case, message) in cases {
            let error = parse_base(case).expect_err("expected a parse failure");
            assert_eq!(error.class, ErrorClass::Value, "class for {case:?}");
            assert_eq!(error.message, message, "message for {case:?}");
        }
    }

    #[test]
    fn a_single_empty_string_token_is_a_literal_not_the_end_token() {
        // A *constructed* `[""]` is one `Literal("")`; only the empty list is
        // the `EndToken` case. Django: `repr(IfParser([""]).parse())` is
        // `(literal '')`.
        let mut parser = IfParser::new(tokens(&[""]));
        let root = parser.parse().expect("parses");
        assert_eq!(repr(&parser, root), "(literal '')");
    }

    #[test]
    fn error_class_differs_between_the_two_entry_points_but_the_message_does_not() {
        // The point of the task. Each case's message interpolates no `Literal`
        // display, so the two classes agree on the message and only the class
        // differs.
        let cases: [&[&str]; 4] = [&[], &["not"], &["1", "and"], &["or", "1"]];
        for case in cases {
            let base = parse_base(case).expect_err("base fails");
            let template = parse_template(case).expect_err("template fails");
            assert_eq!(base.class, ErrorClass::Value, "base class for {case:?}");
            assert_eq!(
                template.class,
                ErrorClass::TemplateSyntax,
                "template class for {case:?}",
            );
            assert_eq!(base.message, template.message, "message for {case:?}");
        }
    }

    #[test]
    fn the_leftover_literal_display_differs_between_the_two_parsers() {
        // The second difference, and why `Literal` stores `display`: the base
        // `Literal.display()` is `repr(value)`, the subclass's is `self.text`.
        let base = parse_base(&["foo", "bar"]).expect_err("base fails");
        assert_eq!(base.message, "Unused ''bar'' at end of if expression.");

        // `FixedCompiler` accepts `bar`, and Django's real `compile_filter`
        // accepts `bar` too (a valid variable), so this row is faithful.
        let template = parse_template(&["foo", "bar"]).expect_err("template fails");
        assert_eq!(template.message, "Unused 'bar' at end of if expression.");
    }

    #[test]
    fn the_template_parser_compiles_every_literal_during_construction() {
        // `TemplateIfParser.__init__` runs `create_var` for every non-operator
        // token, and `create_var` is where `compile_filter` lives — so a bad
        // filter chain fails before `parse`, not during it.
        let error = IfParser::template(&RefusingCompiler, tokens(&["foo"]))
            .err()
            .expect("compile_filter refuses");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax("Invalid filter: 'foo'".to_string()),
        );

        // A successful compile stores the handle, and `display` keeps the raw
        // token rather than its repr.
        let parser = IfParser::template(&FixedCompiler, tokens(&["foo"]))
            .expect("FixedCompiler compiles every token");
        assert_eq!(
            parser.node(parser.tokens[0]).kind,
            IfToken::Literal(Literal {
                value: LiteralValue::Compiled(NodeRef(41)),
                display: "foo".to_string(),
            }),
        );
    }

    #[test]
    fn long_infix_chains_are_iterative() {
        // `expression`'s loop consumes infix operators, so depth does not grow
        // with the chain. Django parses 100 000 `and`s (measured); this uses
        // 10 000, still far past `MAX_NESTING_DEPTH`.
        let mut case: Vec<String> = Vec::new();
        for _ in 0..10_000 {
            case.push("a".to_string());
            case.push("and".to_string());
        }
        case.push("a".to_string());

        let mut parser = IfParser::new(case);
        let root = parser.parse().expect("parses");
        assert_eq!(
            parser.node(root).kind,
            IfToken::Operator(Operator::And),
            "left-associated chain roots at the last `and`",
        );
    }

    #[test]
    fn deep_prefix_nesting_is_refused_instead_of_overflowing_the_stack() {
        // Django has no explicit bound (497 `not`s parse, 498 raises
        // RecursionError). Rust must not abort, so `expression` refuses past
        // `MAX_NESTING_DEPTH`.
        let mut small: Vec<String> = vec!["not".to_string(); 10];
        small.push("x".to_string());
        assert!(IfParser::new(small).parse().is_ok());

        let mut deep: Vec<String> = vec!["not".to_string(); MAX_NESTING_DEPTH + 10];
        deep.push("x".to_string());
        let error = IfParser::new(deep).parse().expect_err("refuses");
        assert_eq!(error.class, ErrorClass::Value);
        assert_eq!(
            error.message,
            format!("If expression is nested more than {MAX_NESTING_DEPTH} deep in if tag."),
        );
    }

    #[test]
    fn a_literal_and_the_end_token_have_binding_power_zero() {
        // `Literal.lbp = 0` and `EndToken.lbp = 0`, which is why the
        // `expression` loop stops on them and `led` never sees either.
        let parser = IfParser::new(tokens(&["foo"]));
        assert_eq!(parser.lbp(parser.tokens[0]), 0);
        assert_eq!(parser.lbp(END), 0);
        assert_eq!(parser.node(END).kind, IfToken::End);
    }
}
```

- [ ] **Step 5: Build, test, lint**

```bash
source scripts/env.sh
cargo test -p template-core smartif 2>&1 | tail -20
echo "test exit=${PIPESTATUS[0]}"
cargo fmt --all
cargo clippy --workspace --all-targets --quiet -- -D warnings > /tmp/clippy-m3t8.txt 2>&1
echo "clippy exit=$?"
cat /tmp/clippy-m3t8.txt
```

Expected: `test result: ok. 12 passed`, and `clippy exit=0` with no output.

`cargo test` does **not** run clippy, and `tail`'s exit code is not clippy's —
which is why the lint is run separately, unpiped, and its own `$?` echoed. Both
mistakes have already happened in this project. Never pass `--workspace` to
`cargo test` here: `template-py` needs PyO3's build, and `-p template-core` is
what runs in a bare shell.

- [ ] **Step 6: Commit**

```bash
git add -A crates/template-core/src/parser/smartif.rs \
             crates/template-core/src/parser/mod.rs \
             crates/template-core/src/parser/tags.rs
git commit -m "M3 Task 8: the smartif parser

Ports django.template.smartif: OPERATORS (13 keys), TokenBase, infix/prefix
nud/led, Literal, EndToken and IfParser, with the error_class difference
between IfParser (ValueError) and TemplateIfParser (TemplateSyntaxError)
pinned by tests. CompileFilter stands in for Parser.compile_filter and has no
M3 implementation: {% if %} is M5, so nothing calls this yet and the tests
drive IfParser with constructed tokens directly.

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

The `tags.rs` path is the one from Step 1; if M3's tag fan-out moved
`py_repr_str`, add the file it moved to instead. If `git add -A` is given a path
that no task touched, it exits 128 with "pathspec did not match any files" — so
name only files this task changed.

#### Review questions

1. `LiteralValue::Compiled(NodeRef)` assumes the compiled `FilterExpression`
   crosses as a host handle; if M3 Task 2's `Parser::compile_filter` returns a
   Rust descriptor instead (M3 plan D2), this one variant changes.
2. `MAX_NESTING_DEPTH = 1000` and its message are Rust's own, not Django's:
   Django raises `RecursionError` at roughly 498 `not`s (measured; the exact
   figure moves with the caller's stack depth — see §5), an interpreter artifact
   rather than a specification. The guard is required so a hostile template
   cannot abort the process, but its value and wording are a decision. Note the
   direction of the divergence: a `not`-chain of 600 parses here and raises
   there, which is the *safe* direction (a too-permissive engine renders a
   template Django's interpreter could not, rather than rejecting a valid one)
   — but it is a divergence and is stated as one.
3. For M5: a `TemplateIfParser` failure reaches `Parser::error` and gets the
   token attached, but the *token* is the whole `{% if … %}` block token
   (`<Block token: "if foo bar...">`), not the failing word — `do_if` passes
   `token.split_contents()[1:]` and `IfParser` never sees a `Token`. Nothing is
   built for this here; it is recorded so M5 does not assume a finer token.

---

### Task 9: `token_kwargs`, the keyword arguments out of the front of `bits`

**Files:**
- Create: `crates/template-core/src/parser/kwargs.rs`
- Modify: `crates/template-core/src/parser/mod.rs` (`pub mod kwargs;`, and re-export `kwargs::{kwarg_re, token_kwargs, Kwargs}`)
- Test: `crates/template-core/src/parser/kwargs.rs` (unit tests only — nothing in Python calls this at M3)

**Interfaces:**
- Consumes: `parser::error::Error`; `parser::desc::AttrValue` (M3's `AttrValue::FilterExpr` from Task 5, and `Map`/`List` from Task 4 for the callers, not used here); `parser::filter_expr::parse_filter_expression` (Task 5, in one integration test); `pyregex::PYTHON_WORD_STANDALONE` (**the standalone/bracketed spelling**, `r"[\p{L}\p{N}_]"`).
- Produces:
  - `pub fn kwarg_re() -> &'static Regex` — Django's `kwarg_re`, expanded to Python's word class.
  - `pub type Kwargs = Vec<(String, AttrValue)>;`
  - `pub fn token_kwargs(bits: &mut Vec<String>, compile_filter: &mut dyn FnMut(&str) -> Result<AttrValue, Error>, support_legacy: bool) -> Result<Kwargs, Error>;`

**Requirement on Task 2 (measured, and the reason this task names its own constants).** Django uses `\w` in two syntactic positions and they are not interchangeable:

```text
base.py:705   (?P<filter_name>\w+)          standalone
base.py:714   "var_chars": r"\w\.\+-"       spliced INSIDE a class: [%(var_chars)s]+
base.py:1160  kwarg_re = (?:(\w+)=)?(.+)    standalone
```

This task needs the **bracketed** form at line 1160 (`[\p{L}\p{N}_]`); Task 5 needs the **contents** form at line 714. Task 2 exports two constants, each named for its position — `PYTHON_WORD_IN_CLASS` (`r"\p{L}\p{N}_"`) and `PYTHON_WORD_STANDALONE` (`r"[\p{L}\p{N}_]"`) — exactly as this project already distinguishes the two uses of `pyunicode::WS_CLASS` (`r"\s\x{1C}-\x{1F}"`, a contents form spliced as `[^{ws}'"]`), except that `WS_CLASS`'s name does not say which form it is; the new names are the fix for that. **Naming is settled, and no `PYTHON_WORD` is defined** (Task 2's decision): Task 5 consumes both, `PYTHON_WORD_STANDALONE` for `(?P<filter_name>\w+)` and `PYTHON_WORD_IN_CLASS` for `var_chars`; this task consumes `PYTHON_WORD_STANDALONE` for `kwarg_re`. A stale reference to the old single name is a compile error rather than a silent pick of the dangerous form. The failure modes are asymmetric and both silent in different ways: the contents form used standalone gives `\p{L}\p{N}_+` — "one letter, one number, then underscores" — which rejects `upper` loudly and immediately; the bracketed form spliced into a class gives `[[\p{L}\p{N}_]\.\+-]`, which the `regex` crate parses as a **nested character class** and matches correctly *by accident*, while Python treats the inner `[` as a literal, so the two patterns are no longer the same pattern and nothing in the suite would say so. This task's guard is the kwarg half of that (Step 1); Task 5 owns the `filter_raw_string` half; Task 2 owns the class-content tests.

**Declared environment.** At this point in M3 the following exist and resolve: `parser::error::{Error, ErrorKind}`; `parser::desc::AttrValue` including M3's `Variable`, `List`, `Map`, `NodeListRef` (Task 4) and `FilterExpr(FilterExprDesc)` (Task 5); `parser::filter_expr::parse_filter_expression(token: &str) -> Result<FilterExprDesc, Error>` (Task 5); `pyregex::{PYTHON_WORD_STANDALONE, PYTHON_WORD_IN_CLASS}` (Task 2); the `regex` crate; `regex::Regex`, `regex::Captures`. What does **not** exist and must not be reached for: any PyO3 or Python object (`template-core` has exactly one dependency, `regex`, and no Python); a real `FilterExpression` (`FilterExpression.args_check` and `parser.find_filter` run in Python and are Task 6's — Review Focus item 2); `Parser::compile_filter` as a method (Django's is a Python method; Rust's compiler is the free function `parse_filter_expression`); `AttrValue::Ref`/`NodeRef` as a filter value (below); the M5 tags that call `token_kwargs`; and `{% load %}`/custom tags (M6) that would pass a host-owned `compile_filter`. Because nothing in the crate has Python, `cargo test -p template-core` runs in a bare shell and every test here must too.

**The Rust signature, and why it is not the brief's `NodeRef` one.** The brief suggested `Result<Vec<(String, NodeRef)>, Error>`. That shape is wrong at M3 and should be rejected, not merely adjusted:

- M3's design decision D2 says a `FilterExpression` crosses the wire as a **descriptor** (`AttrValue::FilterExpr(FilterExprDesc)`), and Task 5 states in as many words that "nothing in this task may construct a `NodeRef` for a filter: a filter is not a host value at M3, it is a name resolved at materialisation time."
- `NodeRef` is a handle to a **host-owned** value (a `CycleNode`, a `PartialTemplate`). Producing one requires a host that allocates handles. A bare `cargo test -p template-core` has no host, so a `NodeRef`-returning `token_kwargs` could not be unit-tested here at all.
- Returning `AttrValue` keeps `kwargs.rs` free of any dependency on `filter_expr.rs`: the compiler is supplied as a closure, exactly as Django's `token_kwargs` supplies nothing but its `parser`.

The shape satisfies the three constraints the brief set: it mutates the caller's bit list in place (`bits: &mut Vec<String>`); every value goes through one supplied compiler, which is Django's `parser.compile_filter` seam and the same code path as everywhere else; and it is expressible with `template-core`'s existing types and no PyO3. The return type is `Kwargs = Vec<(String, AttrValue)>` — a **type alias**, not an inline tuple, because `clippy::type_complexity` has bitten this project twice — and the aliased type also lets a test write `Kwargs::new()`. Callers wrap the result in `AttrValue::Map(...)` (D1), which keeps the emptiness check (`{% with %}` raises "expected at least one variable assignment") on the caller, where Django has it.

`support_legacy` is a required parameter because Rust has no default arguments; all three call sites pass it explicitly (`True` for `{% with %}`, `False` for `{% include %}` and `parse_bits`).

**Who calls this, measured.** `token_kwargs` is defined at `base.py:1163` and imported into two modules. The complete list of call sites in the pinned checkout:

| File:line | Caller | `support_legacy` |
|---|---|---|
| `template/defaulttags.py:1676` | `{% with %}` (`def with_tag`, line 1675 is `remaining_bits = bits[1:]`) | `True` |
| `template/loader_tags.py:347` | `{% include %}`, the `with` option | `False` |
| `template/library.py:421` | `parse_bits`, `token_kwargs([bit], parser)` | default `False` |
| `templatetags/i18n.py:511`, `:517` | `{% blocktranslate %}` (M12, not in the M5 set) | `True` |
| `template/base.py:1163` | the definition | — |

`parse_bits` is reached by every `simple_tag`/`inclusion_tag`; for the built-ins that is **`{% querystring %}`** (`defaulttags.py:1286`, `@register.simple_tag(name="querystring", takes_context=True)`), whose generated `compile_func` (`library.py:127-147`) calls `parse_bits`. **Measured correction to the brief:** `{% url %}` (`defaulttags.py:1515`) does **not** call `token_kwargs`. It calls the regex directly — `match = kwarg_re.match(bit)` at `defaulttags.py:1575`, then `parser.compile_filter(value)` — so `{% url %}` at M5 needs the **exported `kwarg_re`**, not `token_kwargs`. Grepping for `token_kwargs` alone (which is what the brief suggested) misses this: it is the only other `kwarg_re` consumer in the tree after `base.py` itself, and it is why `kwarg_re` is `pub` rather than private to this module.

**At M3 nothing calls `token_kwargs`.** M3 adds no tags ("M3 adds no new tags"), the four M5 built-ins above are registry stubs until M5, and `parse_bits` is only reachable through `{% load %}`/`Library` (M6) or `{% querystring %}` (M5). So this task's tests drive it directly, and that is stated rather than discovered.

**The measured behaviour — this is the specification.** Produced by calling Django's own `token_kwargs`, from the pinned checkout at `.django/` (Django 6.0.9), with a parser built the way `tests/test_parser_parity.py` builds one — `Parser(Lexer("x").tokenize(), builtins=Engine().template_builtins)`, because a bare `Parser(tokens)` registers no tags. The script records the returned dict's keys and the exact list left in `bits` afterwards; values are `FilterExpression` objects and are listed by the string handed to `compile_filter`.

| `bits` | `support_legacy` | returned keys | `bits` afterwards | `compile_filter` received |
|---|---|---|---|---|
| `[]` | any | — | `[]` | — |
| `["a=1"]` | `False` | `a` | `[]` | `"1"` |
| `["a=1", "b=2"]` | `False` | `a`, `b` | `[]` | `"1"`, `"2"` |
| `["a=1", "junk"]` | `False` | `a` | `["junk"]` | `"1"` |
| `["junk"]` | `False` | — | `["junk"]` | — |
| `["a="]` | `False` | — | `["a="]` | — |
| `["=1"]` | `False` | — | `["=1"]` | — |
| `["a=1", "and", "b=2"]` | `False` | `a` | `["and", "b=2"]` | `"1"` |
| `["a=1", "b=2", "rest", "more"]` | `False` | `a`, `b` | `["rest", "more"]` | `"1"`, `"2"` |
| `["1", "as", "foo"]` | `True` | `foo` (value `"1"`) | `[]` | `"1"` |
| `["1", "as", "foo"]` | `False` | — | `["1", "as", "foo"]` | — |
| `["a=1"]` | `True` | `a` | `[]` | `"1"` |
| `["a=1", "x", "as", "y"]` | `True` | `a` | `["x", "as", "y"]` | `"1"` |
| `["1", "as", "foo", "junk"]` | `True` | `foo` | `["junk"]` | `"1"` |
| `["1", "AS", "foo"]` | `True` | — | `["1", "AS", "foo"]` | — |
| `["1", "as"]` | `True` | — | `["1", "as"]` | — |
| `["1", "as", "a", "and", "2", "as", "b"]` | `True` | `a`, `b` | `[]` | `"1"`, `"2"` |
| `["1", "as", "foo", "and", "junk"]` | `True` | `foo` | `["junk"]` | `"1"` |
| `["person.some_sql_method", "as", "total"]` | `True` | `total` | `[]` | `"person.some_sql_method"` |
| `["²=1"]` (`²`) | `False` | `²` | `[]` | `"1"` |
| `["a²=1"]` (`a²`) | `False` | `a²` | `[]` | `"1"` |
| `["á=1"]` (`a` + combining acute) | `False` | — | `["á=1"]` | — |
| `["́=1"]` | `False` | — | `["́=1"]` | — |
| `["xyz=bad_var"]` | `False` | `xyz` | `[]` | `"xyz"` |
| `["a=1\|add:2"]` | `False` | `a` | `[]` | `"1\|add:2"` |
| `["a=x\|default:y"]` | `False` | `a` | `[]` | `"x\|default:y"` |

Four rows carry the whole risk and are named so a future reader deleting one knows which ones were the point:

- **`["a="]` and `["=1"]` return `{}` and change nothing.** `(?:(\w+)=)?` requires at least one word character before `=`, and `(.+)` requires at least one character after it. `"a="` backtracks to the optional group not participating, so group 1 is `None`, `kwarg_format` is falsy, and the early return fires. `compile_filter` is never called.
- **`["a=1", "and", "b=2"]` stops at `"and"`.** In the kwarg form a stray `and` is just a non-kwarg bit, so the run stops there and `["and", "b=2"]` is left. `and` is consumed **only** between legacy pairs — that is the only asymmetry between the two branches.
- **`["a=1", "junk"]` leaves `["junk"]`.** The docstring's "return as soon as an invalid argument format is reached" is exact: the caller is handed back a partly-consumed list, and `{% with %}` then raises on `remaining_bits[0]`.
- **`²` keys, `a`+U+0301 does not.** Python's `\w` is `isalnum() or '_'` — measured at **142,940** code points — which admits U+00B2 (`No`) and rejects U+0301 (`Mn`). Rust's `\w` is **144,667** code points and does the opposite (measured against `regex` 1.13.1). Rust's `[\p{L}\p{N}_]` is also **142,940**, which is why `PYTHON_WORD_STANDALONE`, not `\w`, builds the pattern.

**Error propagation and the value contract (also measured).** `compile_filter` receives the *whole* value string, filter chain included — `["a=1|add:2"]` hands `"1|add:2"` over, and `FilterExpression` then splits it into `var = <Variable: '1'>` and `filters = [(add, [(True, <Variable: '2'>)])]`. A value that is a valid variable but a bad number (`xyz`) is handed over unchanged and compiles to `var = <Variable: 'xyz'>, filters = []` — `token_kwargs` does not distinguish numbers from variables; that is Task 3's job inside `parse_filter_expression`. An invalid filter or remainder is **not caught**: `["a=|"]` raises `TemplateSyntaxError: Could not parse the remainder: '|' from '|'`, and `["a=1|nofilter"]` raises `TemplateSyntaxError: Invalid filter: 'nofilter'`. Because Django deletes the bit *before* calling `compile_filter` (`del bits[:1]` at `base.py:1194`, then line 1202), the offending bit is **already gone** when the error returns: measured, `["a=|", "rest"]` leaves `["rest"]`, and `["a=1", "b=|", "rest"]` also leaves `["rest"]`. The port must delete first and compile second.

**The newline question — one line, because it is a non-issue.** A tag argument **cannot** contain `\n`: `token_kwargs` runs on `split_contents()` of a BLOCK token, whose `contents` came from a `tag_re` match, and `tag_re` is `({%.*?%}|{{.*?}}|{#.*?#})` with no `DOTALL` in either language — Rust's `regex` crate leaves `.` = "any character except `\n`" by default, the same as Python, so `segments.rs`'s `tags_do_not_span_newlines` asserts `texts("{%\nif x %}") == vec!["{%\nif x %}"]` (no match). The two behaviours therefore agree *and* the input that would distinguish them does not exist. No `(?s)` / `dot_matches_new_line` handling is needed or wanted.

- [ ] **Step 1: Write the failing tests, including the `kwarg_re` guard**

Create `crates/template-core/src/parser/kwargs.rs` with the module doc, the imports, and only `#[cfg(test)] mod tests`. The tests reference `kwarg_re()` and `token_kwargs`, so the file does not compile yet — that is the point.

```rust
//! `token_kwargs`: the keyword arguments at the front of a tag's bits.
//!
//! A direct port of `django.template.base.token_kwargs` (`base.py:1163-1207`)
//! and the module-level `kwarg_re` (`base.py:1160`). Two of Django's behaviours
//! are load-bearing and are therefore in the public doc comments rather than
//! only in tests: `bits` is mutated **in place**, and the function **returns
//! early** on the first argument whose format is invalid instead of raising.

use std::sync::OnceLock;

use regex::{Captures, Regex};

use crate::parser::desc::AttrValue;
use crate::parser::error::Error;
use crate::pyregex::PYTHON_WORD_STANDALONE;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::filter_expr::parse_filter_expression;

    /// A `compile_filter` that records the exact strings it is handed, so the
    /// contract "the whole value string reaches the compiler" is asserted rather
    /// than assumed, and returns a placeholder value.
    #[derive(Default)]
    struct Recorder {
        seen: Vec<String>,
    }

    impl Recorder {
        fn compile(&mut self) -> impl FnMut(&str) -> Result<AttrValue, Error> + '_ {
            |value: &str| {
                self.seen.push(value.to_string());
                Ok(AttrValue::Str(value.to_string()))
            }
        }
    }

    /// Django's `token_kwargs` on `bits`, returning keys, the remaining bits, and
    /// the strings the compiler saw.
    fn run(bits: &[&str], support_legacy: bool) -> (Vec<String>, Vec<String>, Vec<String>) {
        let mut bits: Vec<String> = bits.iter().map(|b| (*b).to_string()).collect();
        let mut rec = Recorder::default();
        let kwargs = {
            let mut compile = rec.compile();
            token_kwargs(&mut bits, &mut compile, support_legacy).expect("valid in this table")
        };
        (
            kwargs.into_iter().map(|(key, _)| key).collect(),
            bits,
            rec.seen,
        )
    }

    #[test]
    fn the_behaviour_table_matches_django() {
        // Every row was measured against Django 6.0.9 at `.django/` by calling
        // `django.template.base.token_kwargs` with a `Parser` built as
        // `Parser(Lexer("x").tokenize(), builtins=Engine().template_builtins)`.
        // The four rows carrying the risk are `["a="]`, `["=1"]`,
        // `["a=1", "and", "b=2"]` and `["a=1", "junk"]`; keep them if trimming.
        // The row type is an alias, not inline: the four-tuple written inline is
        // a `clippy::type_complexity` failure under `-D warnings`, verified.
        type Case<'a> = (&'a [&'a str], bool, &'a [&'a str], &'a [&'a str]);
        let cases: &[Case] = &[
            (&[], false, &[], &[]),
            (&["a=1"], false, &["a"], &[]),
            (&["a=1", "b=2"], false, &["a", "b"], &[]),
            (&["a=1", "junk"], false, &["a"], &["junk"]),
            (&["junk"], false, &[], &["junk"]),
            (&["a="], false, &[], &["a="]),
            (&["=1"], false, &[], &["=1"]),
            (&["a=1", "and", "b=2"], false, &["a"], &["and", "b=2"]),
            (&["a=1", "b=2", "rest", "more"], false, &["a", "b"], &["rest", "more"]),
            (&["1", "as", "foo"], true, &["foo"], &[]),
            (&["1", "as", "foo"], false, &[], &["1", "as", "foo"]),
            (&["a=1"], true, &["a"], &[]),
            (&["a=1", "x", "as", "y"], true, &["a"], &["x", "as", "y"]),
            (&["1", "as", "foo", "junk"], true, &["foo"], &["junk"]),
            (&["1", "AS", "foo"], true, &[], &["1", "AS", "foo"]),
            (&["1", "as"], true, &[], &["1", "as"]),
            (&["1", "as", "a", "and", "2", "as", "b"], true, &["a", "b"], &[]),
            (&["1", "as", "foo", "and", "junk"], true, &["foo"], &["junk"]),
            (&["person.some_sql_method", "as", "total"], true, &["total"], &[]),
            (&["\u{00b2}=1"], false, &["\u{00b2}"], &[]),
            (&["a\u{00b2}=1"], false, &["a\u{00b2}"], &[]),
            (&["a\u{0301}=1"], false, &[], &["a\u{0301}=1"]),
            (&["\u{0301}=1"], false, &[], &["\u{0301}=1"]),
            (&["xyz=bad_var"], false, &["xyz"], &[]),
            (&["a=1|add:2"], false, &["a"], &[]),
            (&["a=x|default:y"], false, &["a"], &[]),
        ];
        for &(bits, legacy, keys, remaining) in cases {
            let (got_keys, got_remaining, _) = run(bits, legacy);
            assert_eq!(got_keys, keys, "keys for {bits:?} legacy={legacy}");
            assert_eq!(got_remaining, remaining, "remaining for {bits:?} legacy={legacy}");
        }
    }

    #[test]
    fn the_value_reaches_compile_filter_whole() {
        // Not the key, not the first filter term — the entire value string, so
        // that the same `FilterExpression` parser sees it as anywhere else.
        let (_, _, seen) = run(&["a=1|add:2"], false);
        assert_eq!(seen, vec!["1|add:2".to_string()]);
        let (_, _, seen) = run(&["a=xyz"], false);
        assert_eq!(seen, vec!["xyz".to_string()]);
        // `["a="]` never reaches the compiler at all.
        let (_, _, seen) = run(&["a="], false);
        assert!(seen.is_empty());
    }

    #[test]
    fn kwarg_re_is_djangos_pattern_with_the_word_class_expanded() {
        // The guard Task 2's two spellings rest on: the bracketed form must be
        // spliced standalone. With the contents form the source would be
        // `(?:(\p{L}\p{N}_+)=)?(.+)` and `upper` would stop matching; this
        // assertion is what makes that unspellable rather than merely untested.
        assert_eq!(kwarg_re().as_str(), r"(?:([\p{L}\p{N}_]+)=)?(.+)");
    }

    #[test]
    fn kwarg_re_matches_djangos_word_class_not_rusts() {
        let group1 = |s: &str| kwarg_re().captures(s).and_then(|c| c.get(1)).map(|m| m.as_str().to_string());
        // Python's `\w` admits U+00B2 (`No`) and rejects U+0301 (`Mn`).
        assert_eq!(group1("\u{00b2}=1"), Some("\u{00b2}".to_string()));
        assert_eq!(group1("a\u{0301}=1"), None);
        // Rust's `\w` would invert both, and `[a-z]`-style classes would reject
        // a non-ASCII letter entirely.
        assert_eq!(group1("\u{03b1}=1"), Some("\u{03b1}".to_string()));
    }

    #[test]
    fn kwarg_re_matches_like_python_match_not_like_rust_captures() {
        // Python's `.match` anchors at the start; Rust's `captures` (and
        // `captures_at(_, 0)`) do not. `"\nb=c"` is the distinguishing input:
        // Python keys nothing, a leftmost search keys `b`.
        assert!(kwarg_re().captures("\nb=c").is_some(), "leftmost search does match");
        assert!(match_start("\nb=c").is_none(), "the port must anchor at the start");
        assert!(match_start("a=1").is_some());
    }

    #[test]
    fn a_compiler_error_propagates_and_leaves_the_bit_consumed() {
        // Django deletes the bit *before* `compile_filter` (`base.py:1194` then
        // :1202), so the offending bit is gone when the error returns. Measured:
        // `["a=|", "rest"]` leaves `["rest"]`; `["a=1", "b=|", "rest"]` too.
        let mut bits: Vec<String> = ["a=|", "rest"].iter().map(|s| s.to_string()).collect();
        let mut compile = |value: &str| -> Result<AttrValue, Error> {
            if value == "|" {
                return Err(Error::syntax("Could not parse the remainder: '|' from '|'"));
            }
            Ok(AttrValue::Str(value.to_string()))
        };
        let err = token_kwargs(&mut bits, &mut compile, false).expect_err("the error propagates");
        assert_eq!(err.kind, crate::parser::error::ErrorKind::Syntax(
            "Could not parse the remainder: '|' from '|'".to_string(),
        ));
        assert_eq!(bits, vec!["rest".to_string()], "the consumed bit stays consumed");
    }

    #[test]
    fn values_compile_through_the_real_filter_parser() {
        // The M3 wiring: the closure every M5 caller passes is
        // `|s| parse_filter_expression(s).map(AttrValue::FilterExpr)`. Asserted
        // through the filter *name*, so the test does not reach into Task 3's or
        // Task 5's descriptor internals; full parity is Task 5's corpus test.
        let mut bits: Vec<String> = ["a=1|add:2", "b=x"].iter().map(|s| s.to_string()).collect();
        let mut compile = |value: &str| parse_filter_expression(value).map(AttrValue::FilterExpr);
        let kwargs = token_kwargs(&mut bits, &mut compile, false).expect("compiles");
        assert_eq!(kwargs.len(), 2);
        let AttrValue::FilterExpr(expr) = &kwargs[0].1 else { panic!("expected a filter expression") };
        assert_eq!(expr.filters.len(), 1);
        assert_eq!(expr.filters[0].name, "add");
        assert!(bits.is_empty());
    }
}
```

- [ ] **Step 2: Run the tests to confirm they fail for the right reason**

Run: `source scripts/env.sh && cargo test -p template-core parser::kwargs; echo "exit=$?"`

Expected: a compile error — `cannot find function kwarg_re` / `cannot find function token_kwargs` / `cannot find function match_start`, and `failed to resolve: use of undeclared crate or module pyregex` if Task 2 has not landed yet. Record the first error rather than working around it; a green run here would mean the tests are not actually testing the port.

- [ ] **Step 3: Assemble `kwarg_re` from `PYTHON_WORD_STANDALONE`**

Add above `#[cfg(test)]`:

```rust
/// Django's `kwarg_re`, `_lazy_re_compile(r"(?:(\w+)=)?(.+)")` (`base.py:1160`).
///
/// `\w` is the one token that does not port: Python's is 142,940 code points
/// (`isalnum() or '_'`), Rust's is 144,667, and the two disagree in both
/// directions. `PYTHON_WORD_STANDALONE` is the *standalone* spelling of Python's set
/// (`[\p{L}\p{N}_]`, also 142,940), so the pattern is assembled with `format!`
/// rather than written literally. Measured against the pinned checkout:
/// `["\u{00b2}=1"]` keys on `\u{00b2}` and `["a\u{0301}=1"]` does not key at
/// all; neither follows from Rust's `\w`.
///
/// Public because `{% url %}` (`defaulttags.py:1575`) calls `kwarg_re.match`
/// directly instead of `token_kwargs`, and is the tree's only other consumer.
pub fn kwarg_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(r"(?:({PYTHON_WORD_STANDALONE}+)=)?(.+)")).expect("kwarg_re is a valid pattern")
    })
}

/// Python's `kwarg_re.match(s)`.
///
/// Python's `.match` anchors at the start of the string; Rust's
/// [`Regex::captures`] returns the leftmost match *anywhere*, and
/// `Regex::captures_at(s, 0)` is not an anchor either — both find a match at
/// offset 1 in `"\nb=c"` where Python finds none (measured against `regex`
/// 1.13.1). Every reachable `bit` starts with a non-whitespace character, so
/// the two agree on all real input; the guard is here because the divergence is
/// silent, and `token_kwargs(["\nb=c"])` would otherwise key `b` rather than
/// returning no arguments.
fn match_start(s: &str) -> Option<Captures<'_>> {
    kwarg_re()
        .captures(s)
        .filter(|caps| caps.get(0).is_some_and(|m| m.start() == 0))
}
```

- [ ] **Step 4: Implement `token_kwargs`**

Add below `match_start` (and define the alias above it, so the tests' `Kwargs::new()` resolves):

```rust
/// The kwargs pulled from the front of `bits`, in Python dict insertion order.
pub type Kwargs = Vec<(String, AttrValue)>;

/// Django's `token_kwargs(bits, parser, support_legacy=False)` (`base.py:1163`).
///
/// Parse token keyword arguments and return them in insertion order. `bits` is
/// the remainder of a tag's `split_contents`, and valid arguments are **removed
/// from the front of it in place**, so the caller reads what is left afterwards
/// — `{% with %}` and `{% include %}` both do, and both raise on what remains.
///
/// `compile_filter` is Django's `parser.compile_filter` (`base.py:1202`), the
/// one thing `token_kwargs` uses its `parser` argument for. It receives the
/// whole value string, filter chain and all: `["a=1|add:2"]` hands `"1|add:2"`
/// over. At M3 the caller passes
/// `|s| parse_filter_expression(s).map(AttrValue::FilterExpr)`; at M6 a loaded
/// custom tag passes the host's compiler. Its error is **not** caught: because
/// Django deletes the bit before calling, the offending bit is already gone
/// when the error returns, and the port preserves that order.
///
/// `support_legacy` selects Django's legacy `1 as foo` form, which names the
/// variable in the *third* bit and compiles the *first*. There is no
/// requirement that all remaining bits are keyword arguments: the function
/// returns as soon as an argument's format is invalid.
pub fn token_kwargs(
    bits: &mut Vec<String>,
    compile_filter: &mut dyn FnMut(&str) -> Result<AttrValue, Error>,
    support_legacy: bool,
) -> Result<Kwargs, Error> {
    if bits.is_empty() {
        return Ok(Kwargs::new());
    }

    // `kwarg_format = match and match[1]`: an argument is in kwarg form iff the
    // optional `key=` group participated.
    let kwarg_format = match_start(&bits[0]).and_then(|caps| caps.get(1)).is_some();
    if !kwarg_format && (!support_legacy || bits.len() < 3 || bits[1] != "as") {
        return Ok(Kwargs::new());
    }

    let mut kwargs = Kwargs::new();
    while !bits.is_empty() {
        let (key, value) = if kwarg_format {
            // Django re-matches every iteration: a later bit may not be a kwarg.
            let Some(caps) = match_start(&bits[0]) else {
                return Ok(kwargs);
            };
            let Some(key) = caps.get(1) else {
                return Ok(kwargs);
            };
            let key = key.as_str().to_string();
            // `(.+)` is mandatory and matched, so group 2 is present.
            let value = caps.get(2).map_or("", |m| m.as_str()).to_string();
            // Django's `del bits[:1]`, before `compile_filter` below.
            bits.remove(0);
            (key, value)
        } else {
            // Django's `key, value = bits[2], bits[0]`: the name is the THIRD
            // bit and the value is the FIRST.
            if bits.len() < 3 || bits[1] != "as" {
                return Ok(kwargs);
            }
            let key = bits[2].clone();
            let value = bits[0].clone();
            // Django's `del bits[:3]`, before `compile_filter` below.
            bits.drain(..3);
            (key, value)
        };

        kwargs.push((key, compile_filter(&value)?));

        // Only the legacy form consumes the `and` between pairs; in kwarg form a
        // stray `and` terminates the run like any other non-kwarg bit.
        if !kwarg_format && !bits.is_empty() {
            if bits[0] != "and" {
                return Ok(kwargs);
            }
            bits.remove(0);
        }
    }

    Ok(kwargs)
}
```

Two implementation notes a reviewer will check: `bits.remove(0)` and `bits.drain(..3)` are Django's `del bits[:1]` / `del bits[:3]` (O(n) on a token's handful of bits, so the shift is not worth optimising away), and the delete happens **before** `compile_filter` in both branches, matching the measured post-error state in Step 1's error test.

- [ ] **Step 5: Declare the module**

In `crates/template-core/src/parser/mod.rs`, add to the module declarations:

```rust
pub mod kwargs;
```

and to the re-exports:

```rust
pub use kwargs::{kwarg_re, token_kwargs, Kwargs};
```

- [ ] **Step 6: Run everything**

Run: `source scripts/env.sh && cargo test -p template-core; echo "exit=$?"`
Expected: exit 0, all tests pass including the six new ones in `parser::kwargs`.

Run: `source scripts/env.sh && cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"`
Expected: exit 0. The scratch implementation of this exact code was checked clippy-clean against `regex` 1.13.1, so a warning here means the surrounding code drifted rather than the algorithm being wrong.

Run: `source scripts/env.sh && scripts/check.sh; echo "exit=$?"`
Expected: exit 0, `Ran 1547 tests … OK (skipped=6)` — **unchanged**. This task adds no Python test, because nothing in Python calls `token_kwargs` until M5 and the engine is not yet in the suite's path (§10.1). The Rust tests are the whole proof.

- [ ] **Step 7: Commit**

```bash
git add crates/template-core/src/parser/kwargs.rs crates/template-core/src/parser/mod.rs
git commit -m "M3 task 9: token_kwargs and kwarg_re for the M5 tag helpers"
```

---

### Task 10: M3's gates, and one spec correction measured against the tree

**Files:**
- Modify: `tests/parser_corpus.py` (variable cases), `tests/test_parser_parity.py` (the vacuity bound)
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.4 gate, corrected)
- Test: the whole repository

**Interfaces:**
- Consumes: everything the milestone produced.
- Produces: the milestone's evidence table, and a spec amendment the reviewer must see.

**Declared environment.** Nothing here is new machinery — it is the corpus, the gate and the measurement that says one gate cannot be met as written.

#### Before anything else, because it decides the rest: what the gate can actually hold

§10.4's M3 gate reads *"a variable ahead of `{% extends %}` … M3 adds that case to the corpus explicitly."* Measured against `.django/` at **6.0.9**, before adding anything:

```
$ python -I -c "… Parser(Lexer(src).tokenize(), builtins=Engine().template_builtins, origin=Origin('t.html','t.html',None)).parse() …"
'{{ x }}{% extends "a" %}'  -> TemplateSyntaxError '{% extends "a" %} must be the first tag in \'t.html\'.'
'text{% extends "a" %}'     -> OK ['TextNode', 'ExtendsNode']
'{% extends "a" %}{{ x }}'  -> OK ['ExtendsNode']
'{{ x }}{% include "a" %}'  -> OK ['VariableNode', 'IncludeNode']
```

Three things follow, and the third is the problem.

1. **Text before `{% extends %}` is legal.** `contains_nontext` is set only for a non-`TextNode` (`base.py:611`), so `text{% extends %}` parses fine. The M2 corpus's text-versus-tag coverage was therefore never testing the ordering rule *at all* — it was testing that the rule does not fire. The rule fires on a **variable**, so the gate's diagnosis is right and sharper than it reads.
2. **Without an origin the tag cannot even compile.** `Parser(...).parse()` on `{{ x }}{% extends "a" %}` with no `origin` raises `AttributeError: 'NoneType' object has no attribute 'template_name'` from `ExtendsNode.__init__`. The parity harness's `django_parse` passes no origin, so the corpus case needs the harness extended to supply one — and the error message's tail is `repr(origin.template_name)`, so Rust's `origin_template_repr` and the harness's `Origin` have to agree on the name for the comparison to mean anything.
3. **Rust cannot produce that error at M3, and the two ways to make it would each be worse than the gap.**

   Django compiles the tag *first* and checks the ordering *after* (`base.py:600-610`, called from the dispatch path): `{{ x }}{% extends %}` — no argument — raises `'extends' takes at least one argument`, **not** the ordering error. So an ordering error produced by Rust *ahead* of dispatch would be wrong for that input, and that input is reachable by any user template.

   Checking `must_be_first` by *name* without compiling is therefore not an emulation of Django; it is a new divergence, traded for a corpus win. The other route — implementing `{% extends %}`'s compile function early — puts a loader tag in Rust, which §6 assigns to M7 and the M2 plan's scope note deliberately keeps out of the Rust registry.

   And `must_be_first` is `True` in exactly one place in the whole template package (`loader_tags.py:87`; `base.py:1034` is the `False`), so there is no Rust-owned tag to substitute into the corpus case.

**So the gate is corrected rather than met, and the correction is the finding.** M3 adds the half it can hold — **variable-versus-text and variable-versus-tag `contains_nontext`**, which is the part that is currently unexercised — and the `{% extends %}`-specific case moves to M7, where the tag is reachable. The spec's §10.4 entry is edited to say exactly this, with the three measurements above, so the next person to read the gate does not re-derive it. **This is the one place M3 does not do what the spec says, and it is flagged for the reviewer** rather than quietly narrowed.

- [ ] **Step 1: Add the variable cases the milestone can hold**

In `tests/parser_corpus.py`, beside the existing entries:

```python
    # M3's half of §10.4's ordering gate. `{{ … }}` is what sets
    # `contains_nontext` — measured: `text{% extends %}` is legal, and
    # `{{ x }}{% extends %}` is not — so variable-versus-text and
    # variable-versus-tag are the cases that exercise the rule's input.
    # (The `{% extends %}`-specific case is M7's; see the plan's Task 10.)
    "{{ x }}{% csrf_token %}",
    "{{ x }}{% csrf_token %}y",
    "a{{ x }}",
    "{% autoescape on %}{{ x }}{% endautoescape %}{% autoescape on %}b{% endautoescape %}",
    "{% spaceless %}{{ x }}{% endspaceless %}",
```

and in `tests/test_parser_parity.py`, a test that says *why* these are the ones:

```python
    def test_a_variable_sets_contains_nontext_where_text_does_not(self):
        """§10.4's ordering gate, in the half M3 can hold.

        Measured on the pinned tree: `contains_nontext` is set only for a
        non-`TextNode`, so `text{% extends %}` parses and `{{ x }}{% extends %}`
        does not. Text-versus-tag therefore never reached the rule. These are
        the inputs that do.
        """
        for source, expected in [
            ("{{ x }}{% csrf_token %}", True),
            ("{{ x }}{% csrf_token %}y", True),
            ("{% autoescape on %}{{ x }}{% endautoescape %}{% autoescape on %}b{% endautoescape %}", False),
        ]:
            with self.subTest(source=source):
                ours = ours_parse(source)
                theirs = django_parse(source)
                index = 0 if "autoescape" not in source else 0
                self.assertEqual(
                    ours[index].nodelist.contains_nontext,
                    theirs[index].nodelist.contains_nontext,
                )
                self.assertEqual(ours[index].nodelist.contains_nontext, expected)
```

The `expected` column is the part that makes this non-vacuous: comparing our tree to Django's would pass even if both sides never set the flag, which is the state M2 left this in.

- [ ] **Step 2: Widen the randomised generator, and floor the widening**

`tests/parser_corpus.py`'s `random_template` gains an inline-variable arm alongside its inline tags:

```python
VARIABLES = (
    "{{ x }}",
    "{{ a.b.c }}",
    "{{ a.0 }}",
    "{{ a|upper }}",
    "{{ a|default:'z'|upper }}",
    "{{ 'lit' }}",
    "{{ 5 }}",
    "{{ a.0|join:', ' }}",
)
```

so the 400 randomised templates stop being variable-free — which was the M2 round's own recorded residue. And the floor, which is the accepted "vacuity bound", goes in `RandomisedParityTests`:

```python
    def test_the_generator_reaches_variables(self):
        """A floor on variables, and one on their *shapes*.

        The M2 residue was that all 400 templates were variable-free outside
        `{% verbatim %}`/`{% comment %}` — so a green randomised pass said
        nothing about M3's new code. A single floor on "some template contains
        `{{`" would be satisfied by one template, so the shapes are counted
        separately: a generator that produced only `{{ x }}` would leave dotted
        lookup, indexing and filters — the whole of M3 — unexercised.
        """
        rng = random.Random(RANDOM_SEED)
        sources = [random_template(rng) for _ in range(RANDOM_CASES)]
        joined = "\n".join(sources)
        self.assertGreater(sum("{{" in s for s in sources), RANDOM_CASES // 4)
        for shape in ("{{ x }}", "{{ a.b.c }}", "{{ a.0 }}", "|upper", "|default:"):
            self.assertIn(shape, joined, f"no randomised template reached {shape}")
```

Re-run the measurement after changing the seed or the arms — the M2 round's numbers were invalidated by exactly that, and the lesson was recorded in the same test.

- [ ] **Step 3: Correct §10.4**

Replace the M3 gate entry with the corrected statement: what the rule's input actually is (a variable, not text), that the `{% extends %}`-specific case lands at M7, and the three measurements from the section above. Do not delete the entry — a gate that was narrowed silently is worse than one that was narrowed with its evidence attached.

- [ ] **Step 4: Run the whole gate**

Run each unpiped, so the exit code is the one being read:

```bash
source scripts/env.sh
cargo test -p template-core; echo "exit=$?"
cargo clippy --workspace --all-targets -- -D warnings; echo "exit=$?"
maturin develop
python -m unittest discover -s tests -v
python scripts/differential.py; echo "exit=$?"
scripts/check.sh; echo "exit=$?"
python scripts/progress.py
```

Expected: every exit 0; the suite at `Ran 1547 tests … OK (skipped=6)`; the progress meter **unchanged at 25 modules still Django's** — M3, like M2, adds engine code that nothing in the suite's path imports yet (§10.1: engine progress and integration progress move independently, and the meter measures the second).

**What this gate does not prove, stated next to the claim rather than only in Step 5 (D5).** A green `tests/test_render_parity.py` is **not** evidence that Task 7's resolver is correct. Every render case's lookup still runs through Django's `Variable.resolve`, because `base` is not interposed until M9 — so what a green render test proves is the Rust *parse* and the host's callbacks, and nothing about the arm order Task 7 built. The resolver's evidence is `_native.resolve` driven directly, plus `tests/differential/cases.py`, which calls it without a render in the path. Anyone reading this gate to decide whether M3 is done should be able to see that without opening Task 7.

- [ ] **Step 5: Write `.review/m3-summary.md`**

Same shape as M2's: what M3 claims, the commit table, the evidence table, the guards-observed-failing list (each mutation actually run), what was **not** verified, and what to attack. Two entries belong in it that no test can express:

- **The spec correction from the opening section**, with its measurement, because it is the milestone's one deliberate deviation.
- **What `_native.render` does and does not prove** (D5): the lookup in a render case still runs through Django's `Variable.resolve` until M9 interposes `base`, so green render tests are evidence about the Rust parse, not about Task 7's resolver. The resolver's evidence is `_native.resolve` and the differential corpus.

- [ ] **Step 6: Commit**

```bash
git add tests docs scripts
git commit -m "M3 task 10: the milestone's gates, and a measured correction to §10.4"
```
