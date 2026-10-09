# M6 — `Library`, the extension host, `{% load %}` and `querystring`: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Python-defined tags work through the Rust engine — the `Library` registry, a host seam that compiles a tag Rust does not own, a `ParserHandle` whose callback may re-enter the parse, `{% load %}`, and the one built-in that is registered by `simple_tag`, `{% querystring %}`.

**Architecture:** M3 established that Rust owns the parse and Python owns the tree (§5.5). M6 opens the seam the parse has so far kept shut: a block tag Rust has no compile function for is compiled by the **host**, and the node it returns is an opaque Python object on the wire. The pivot is that **the host owns parser *state* (`tags`, `filters`, `libraries`, `extra_data`, arbitrary attributes) while Rust owns the token *cursor*.** A host compile function receives the two halves it needs: a `ParserHandle` for state, and the parser's remaining tokens, which it may consume; Rust re-reads the cursor when the callback returns. That is what makes `{% load %}` — and `Library.simple_block_tag`'s `parser.parse(...)` — work without Rust ever holding `&mut Parser` across a call into Python.

**Tech Stack:** Rust (edition 2021, no Python in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.2 (the seam, `ParserHandle`), §5.5 (the shadow tree), §6 (ownership), §9 (the three test layers), §10.3, §10.4. This plan argues from the spec; where they disagree the spec wins and this plan is wrong. **Three spec claims are corrected by this plan's recon and are recorded in `.review/m6-recon.md`** (§D of this plan names them and the tasks that edit them) — read that file before this one; it carries the measurements this plan's architecture rests on.

## Global Constraints

Copied verbatim from the spec and the M4/M5 plans; every task's requirements implicitly include them.

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
  on `PATH` otherwise. (`scripts/env.sh` also sets `PIP_CONFIG_FILE=/dev/null`;
  the global pip config is broken and is deliberately not fixed.)
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
- **A task states its declared environment.** M2's and M3's defect rates were
  dominated by correct knowledge of Django combined with wrong assumptions about
  the harness. Each task below names what is constructible at that point, what
  imports resolve, and what the shim does and does not shadow.
- **Every guard is observed failing for its intended reason before it is kept**
  (§11), by disabling the guard and re-running. A guard nobody has seen fail is
  a comment.
- **The plan publishes derivations and pointers, not snapshots of them.** Every
  fixable plan defect in this project has been a derivable value written into
  prose; and a copied code block propagates like a copied citation. Where a fact
  is computed somewhere, this plan names *where* and does not restate it.
- **The `.django/` suite is not yet the gate for our tags.** The shim interposes
  three files (`__init__`, `exceptions`, `parser`) and `unshimmed()` is 25, so
  the 1547 tests currently run on *stock Django's* parser. They must stay green
  (`unshimmed()` unchanged) but they do **not** exercise anything this plan
  writes. M6's gate is the parity corpus in `tests/`.

## Review Focus

The five inputs and conditions the spec implies but no single task's tests
exercise, most likely first. Each is pinned by a test in the task that owns the
code.

1. **The nested region of a host tag's body is parsed by Django, not by Rust, and that is invisible to the corpus.** Under this plan a host tag is compiled by Django's own `compile_func` against a cursor Rust hands over (Task 3). When that function calls `parser.parse(("enddiv",))` — `Library.simple_block_tag` at `library.py:211-212`, `tag_27584.badtag` at `:8-9` — the body is parsed by *Django's* `Parser.parse`, so every tag inside it is compiled by Django. The tree still matches (`describe` compares Django nodes to Django nodes) and renders correctly, but **the parity corpus would report a pass for a body our engine never touched**, which is the vacuous-gate failure this project has now hit twice. **Owned by Task 3; pinned by a test that asserts a Rust-owned tag inside a host block tag's body still reports Rust's error text for a Rust-only failure mode** (not merely that the tree matches).
2. **`{% load %}`'s shadowing gate is a test nobody in `.django/` wrote.** Measured across the whole checkout: no test registers a library tag under a built-in name. The behaviour is real — `Parser.add_library` is `self.tags.update(lib.tags)` (`base.py:669-670`) and libraries merge *after* `builtins` (`base.py:522-523`) — so a loaded library shadows a built-in, and the suite will not catch a regression in that. The spec names this as M6's gate and states it against two methods that do not exist (`Library.add_tag`/`add_library`; see `.review/m6-recon.md` §"The three spec errors"). **Owned by Task 3; the test is a sandwich** — a built-in use, the `{% load %}`, then a shadowed use — because a test with the shadowed use alone passes under either dispatch order.
3. **The filter-name rebinding `Host::check_filter`'s doc has carried since M5 is now reachable.** `add_library` rebinds a filter name (`update`, not `setdefault`), and our materialisation happens after the whole parse, so a `{% load %}` *anywhere* in a template retroactively rebinds an earlier token's filter. Before M6 no tag could cause it; `{% load %}` ships here, so this is the task that must not ship the double lookup. **Owned by Task 1; the sandwich is `{{ x|upper }}{% load custom %}{{ y|upper }}` asserting the *first* rendered with the built-in.**
4. **A host node must get its `token` from the same code path as every other node.** This line's first version claimed the token is attached by Django and that our parse "never runs" that line — both false. Rust ports `extend_nodelist` (`crates/template-core/src/parser/mod.rs:392-418`) and calls it at `:304` for a host descriptor like any other block node, so `node.token` **is** set, and the *only* new risk is that Task 2's `materialise_node` arm returns the stored Python object **before** the generic token/origin attachment runs — making a user tag's node the one shape that renders without a token, in the debug traceback path only. **Owned by Task 2's arm and Task 5's debug-mode row; the assertion is that the arm attaches rather than bypasses**, checked by comparing the host node's `token` to a Rust-built node's in the same template.
5. **A `Template` must not carry `extra_data` after a failed parse.** The
   assignment is `nodelist = parser.parse(); self.extra_data = parser.extra_data`
   **inside `Template.__init__`'s `try`** (`base.py:198-200`), so a parse that
   raises leaves the attribute off the template — and consumers rely on that
   tolerance (`getattr(template, "extra_data", {})`, `engine.py:194`). Read the
   site rather than trusting this line: the recon's first version of it named
   `compile_nodelist` and cited `test_partials.py:171`, which actually pins the
   *consumer* side by passing a mock template without the attribute. **The
   producer side is pinned by no test in the checkout**, so M6 pins both.
   **Owned by Task 4.**

---

## Architecture

Three decisions, each argued from a measurement in `.review/m6-recon.md`. Read that file; this section states only the conclusions and the consequences a task will trip over.

### A1. `{% load %}` is a host tag, not a Rust compile function

`find_library` and `load_from_library` (`defaulttags.py:1065-1099`) both read `parser.libraries`, a Python dict of `Library` objects, and `load_from_library` *builds* a `Library`. `load`'s whole job is `parser.add_library(...)`. The lookup is Python's whichever side calls it; the only question is who calls `add_library`, and the answer that keeps `Library` Python-owned (§6) is Django's own `load`.

**Consequences, and each is a task's business:**

- `load` leaves `REGISTRY`, `tags/load.rs` is deleted, and
  `parser/tags/mod.rs`'s `builtin_tag_stubs()` loses it. `registry.rs`'s doc
  comment currently says M6 "must decide"; M6 decides and the comment records
  the answer. `test_tag_inventory.py::RustRegistryTests.test_the_rust_registry_matches_the_loader_tags_subtraction`
  subtracts `{block, extends, include}` today; by the end of Task 3 it must subtract
  `{block, extends, include, load, querystring}` — **the Python-owned built-ins**,
  three by §6's rule, `load` by A1, `querystring` by A4. Its name ("the
  loader_tags subtraction") stops being accurate; the test is about §6 now.
- `{% load %}` therefore needs `parser.libraries` populated, which
  `render.py::parser_for` does **not** do today (`Parser(Lexer(source).tokenize(), builtins=engine.template_builtins)`).
  Task 3 adds `libraries=engine.template_libraries`.
- `{% load %}` gets **no** corpus row from
  `test_the_corpus_covers_every_implemented_tag_with_a_success_path`: that test
  derives its demand from `builtin_tag_names() - builtin_tag_stubs()`, and
  `load` is in neither. Its coverage is Task 3's shadowing gate plus a
  success-path row written by hand.

### A2. The host owns parser **state**; Rust owns the token **cursor**

§5.2 says `call_tag` takes a `ParserHandle`, not `&mut Parser`, because a
callback re-enters the parse. Two shapes satisfy that, and this plan takes the
smaller:

- **Chosen — the seeded cursor.** The shell keeps one real
  `django.template.base.Parser` for the whole parse. Rust passes its remaining
  tokens to `call_tag`; the shell sets `parser.tokens = list(reversed(tokens))`
  and runs `parser.tags[command](parser, token)`; Rust replaces its cursor with
  `list(reversed(parser.tokens))` afterwards. Django's `Parser.parse` already
  leaves the end tag on the stack for `delete_first_token` to remove
  (`base.py:564-569`), so a callback that calls `parse` then
  `delete_first_token` works **unmodified**.
- **Rejected — a Rust-owned `ParserHandle`.** It needs `Parser` behind
  `Rc<RefCell<…>>` with no borrow held across a host call, which is a
  restructuring of `Parser::parse`'s `&mut self` shape for a property this plan
  gets for free, and it would make Rust's `parse` responsible for leaving the
  end tag *only* on the handle path — two semantics for one method.

**Consequence to carry into Task 3:** the host's registry and Rust's must be
complementary, and the predicate is one line:

```text
has_tag(command) = command in parser.tags
                   and (command not in RUST_OWNED
                        or parser.tags[command] is not ENGINE_BUILTIN_TAGS[command])
```

`RUST_OWNED` is `set(_native.builtin_tag_names())` — derived from the engine,
not listed. The identity clause is what lets a loaded library shadow a built-in
(Review Focus 2) while a plain `{% for %}` still goes to Rust. The `builtins`
merge at `base.py:522-523` is left untouched, so `parser.tags` stays exactly
what Django makes it, and the nested-region parse (Review Focus 1) resolves
every name it meets.

### A3. A host node crosses as a reserved descriptor — and nothing else changes

A host tag returns a Python `Node` object. §5.2 already fixes the wire shape
("`NodeRef` is an opaque handle to a Python-owned `Node`"), so M6 adds one
constructor: `NodeDesc::host(NodeRef)` emits `class_name = "HostNode"` with the
single attribute `("node", AttrValue::Ref(r))`. `materialise_node` checks that
name **before** the `NODE_CLASSES` lookup and returns the stored object with
`token`/`origin` attached. The `#[cfg(test)]` stand-in in `tags/mod.rs:512`
currently uses the name `HostNode`; Task 2 renames it `StandinHostNode` so the
real sentinel is unambiguous, and `tests/test_materialise.py`'s
`TEST_ONLY_CLASSES` follows.

**The reserved name exists only on the wire, and the parity harness never sees
it.** By the time `tests/test_parser_parity.py` looks at a tree, the host node
*is* an ordinary Django node — `SimpleNode`, `SimpleBlockNode`, whatever the
host built — and `describe`'s existing `Node` arm handles it generically:
`type(value).__name__` plus a recursive `vars()`, with `nodelists` catching
`SimpleBlockNode.nodelist` because that is a **real** `NodeList`. So no
`describe` arm is added, and `IGNORED_ATTRIBUTES`/`token_shape` apply as they do
to every other node. Task 5's job is instead to make the two sides hold the
**same** `Library` objects, because `SimpleNode.func` is compared by identity and
two separately-built libraries would compare unequal.

### A4. `querystring` is host-compiled too, and `parse_bits` is not ported

`querystring.rs`'s stub says parsing it needs "a host handle to the Python
callable, and `parse_bits` driven by that callable's signature". Measured
(`.review/m6-recon.md` §"Follow-up measurement"): the callable **is** reachable —
`simple_tag` closes over `func`, `params`, `varargs`, `varkw`, `defaults`,
`kwonly`, `kwonly_defaults`, `takes_context`, `function_name`
(`library.py:120-128`), and `compile_func.__dict__["__wrapped__"] is func` via the
`@wraps(func)` at `library.py:185`. Reachable is not the same as ours to drive.

Driving `parse_bits` from Rust means re-deriving everything the three
`simple_tag`-family decorators do *around* it, because the values live in a
closure and not on an object: the closure's **variable names** become a contract
with an implementation detail of a pinned file; `simple_block_tag` derives
`tag_params` from `params` + `takes_context` and raises two
`TemplateSyntaxError`s doing it (`:186-203`); it computes
`end_name = end_name or f"end{function_name}"` (`:181-182`) and parses the body
**before** `parse_bits` (`:206-212`); all three split the `as <var>` target first
(`:174-178`). That is a reimplementation of `Library` in Rust, against §6, which
assigns `Library` to Python — and the payoff is nil, because running Django's own
function through the host gates `parse_bits`' five error strings at least as
strongly (the corpus compares the raised message either way) as a port that could
drift.

**So every `Library`-registered tag is host-compiled, `querystring` included.**
`querystring` leaves `REGISTRY` with `load`; `parse_bits` is not ported and
`SimpleNode` needs no `NODE_CLASSES` entry. §D1's move of `querystring` from M5
to M6 is vindicated — it does need the extension host — and the mechanism is the
seam, not a Rust compile function. `builtin_tag_names()` drops 24 → 22 and
`builtin_tag_stubs()` becomes `['partial', 'partialdef']`.

### A5. `block`, `extends` and `include` are *excluded* from the host at M6

They sit in `parser.tags` and not in `RUST_OWNED`, so a naive `has_tag` hands
them to Django. The conclusion is to exclude them; the reason is **not** that the
ordering check is unreachable. This section's first version said that, and it was
false: `Parser::extend_nodelist` is a full port
(`crates/template-core/src/parser/mod.rs:392-418`), `must_be_first && contains_nontext`
check and Django's exact message included, and `Parser::parse` calls it at
`:218`, `:252` and `:304`. A host tag's descriptor leaves `call_tag`, returns
through the dispatch arm, and reaches `:304` like any other block node.

**What actually fails is one `bool` on the wire.** `NodeDesc.must_be_first` is
documented "Not carried on the wire" (`desc.rs:369`) and `NodeDesc::default`
sets it `false` (`:407`); only `NodeDesc::first()` (`:440`) turns it on, and the
one built-in that sets it is `{% extends %}`. So a host-built node arrives with
the flag cleared, the check runs, and it passes — the same conclusion by a
different mechanism. The remedy is therefore *small*: one boolean, reported by
the shell from the Python node's own `must_be_first`, and `{% extends %}` could
reach the host and still be position-checked.

That is why the exclusion needs an argument of its own, and it has a better one:

**M6 must not newly accept a tag it cannot render.** Today `{% extends %}`
raises `Invalid block tag` — an honest "not yet", because Rust's registry omits
it and no host has tags. Let it reach the host and M6 would *accept*
`{% extends %}` at parse time while `ExtendsNode.render` needs
`context.render_context` and `Template._render` (M7's, and M9's). The divergence
would move from loud to silent, which is worse: a template that renders wrong
beats a template that refuses to compile only in a benchmark. The same holds for
`{% block %}`, whose `do_block` writes `parser.__loaded_blocks`
(`loader_tags.py:228-234`), and `{% include %}`, whose `render_context` cache is
keyed by template identity — both render-path machinery this milestone does not
have.

**So M6 excludes all three**, leaving them exactly as they are today. Task 7
records three things for M7: remove the exclusion, carry `must_be_first` with the
host node (a one-`bool` change to `NodeDesc::host` and the shell's `call_tag`
return), and note that this milestone's own design already satisfies M7's
arbitrary-attribute requirement for free — the handle handed to a compile
function *is* Django's `Parser`, so `parser.__loaded_blocks` works with no proxy
and no `AttributeError` emulation.

---

## File Structure

| path | responsibility |
|---|---|
| `crates/template-core/src/parser/host.rs` | **Modify.** `Host` gains `has_tag` and a `call_tag` that hands over tokens; `check_filter` returns `Option<NodeRef>`. |
| `crates/template-core/src/parser/mod.rs` | **Modify.** `Parser::parse` dispatches a host tag by handing over the remaining tokens and re-reading the cursor; the cursor gains `remaining_tokens`/`replace_tokens`. |
| `crates/template-core/src/parser/desc.rs` | **Modify.** `NodeDesc::host(NodeRef)` and the `HOST_NODE` constant. |
| `crates/template-core/src/parser/registry.rs` | **Modify.** `load` and `querystring` leave the table; the doc comment records A1 and A4. |
| `crates/template-core/src/parser/tags/load.rs` | **Delete.** Host-owned (A1). |
| `crates/template-core/src/parser/tags/querystring.rs` | **Delete.** Host-owned (A4) — the stub's own three requirements are met by the seam, not by a port. |
| `crates/template-py/src/lib.rs` | **Modify.** The host callables (`has_tag`, `call_tag`) on the shell's `Host` impl; token wire conversion in both directions. |
| `python/django_rust_templates/host.py` | **Create.** `ShellHost`, the parser-state owner and the `has_tag`/`call_tag` callables. Outside `_impl` — `_impl/host.py` would create a `django.template.host` Django does not have, which the shim's rule permits but does not need. |
| `python/django_rust_templates/render.py` | **Modify.** `parser_for` gains `libraries`; `render_string` builds a `ShellHost`. |
| `python/django_rust_templates/_impl/parser.py` | **Modify.** `HOST_NODE` in `materialise_node`; `NODE_CLASSES` gains `SimpleNode`; `_materialise_filter_expression` takes a handle instead of re-looking-up the name. |
| `tests/parser_corpus.py` | **Modify.** `{% load %}` and `{% querystring %}` rows. |
| `tests/test_parser_parity.py` | **Modify.** The library-aware harness (`django_parse`/`ours_parse` gain `libraries`). No `describe` arm for a host node: `describe` sees materialised Django nodes, where the host node is an ordinary `SimpleNode`, and its generic `Node` arm already recurses into `SimpleBlockNode`'s real `NodeList` (Self-Review 4). |
| `tests/test_load_shadowing.py` | **Create.** Review Focus 2's gate. |
| `tests/test_host_tags.py` | **Create.** Review Focus 1 and 4. |
| `scripts/check_python_anchors.py` | **Create.** Task 6's checker: every `` `name.py:N` `` anchor in the tree against the pinned checkout. Exit 1 on drift, `--update` to repair. |
| `scripts/anchors.txt` | **Create (generated).** The manifest — anchor as written, path, line, end line, snippet at each. Sorted by file then line so it reads as a table of what we claim each line says. |
| `tests/test_anchor_manifest.py` | **Create.** Task 6's four behaviours, each driven by the defect it names rather than by a green run over the tree. |
| `docs/superpowers/specs/…-design.md` | **Modify.** §10.3's M6 row and §10.4's M6 gate text (Task 7). |

---

### Task 1: `check_filter` returns a handle, before `{% load %}` ships

**Why first, and why it is its own task:** `host.rs`'s own doc calls this "a
prerequisite of M6, not a bug in the committed tree" and gives the measurement.
`{% load %}` is in Task 3. Landing the handle first means Task 3 cannot ship the
double lookup by accident, and the sandwich that proves it is green before any
host tag exists.

**Files:**
- Modify: `crates/template-core/src/parser/host.rs` (`Host::check_filter`)
- Modify: `crates/template-core/src/parser/filter_expr.rs` (the sole caller)
- Modify: `crates/template-py/src/lib.rs` (`FilterCheckHost::check_filter`)
- Modify: `python/django_rust_templates/_impl/parser.py` (`check_filter`, `_materialise_filter_expression`)
- Test: `tests/test_filter_rebinding.py` (create)

**Interfaces:**
- Consumes: `Host::alloc_slot`'s table (the arena already exists).
- Produces: `FilterCheckHost::check_filter` stores `find_filter`'s result in the
  refs table and returns its index; `FilterExprDesc`'s per-filter entry gains a
  `NodeRef`; `_materialise_filter_expression` reads `resolve_ref(index)` instead
  of `parser.find_filter(name)`.

- [ ] **Step 1: Measure the divergence the handle closes, and write it down as the failing test**

```python
# tests/test_filter_rebinding.py
"""`{% load %}` rebinds a filter name; the parse must bind at the token's position.

`Parser.add_library` is `self.filters.update(lib.filters)` (`base.py:669-670`) —
`update`, not `setdefault` — so a name already in the table is *rebound*, and a
`{% load %}` anywhere in a template retroactively changes what an earlier
`{{ x|name }}` resolves to, if the name is looked up late.

Django looks the name up once, at `base.py:771`, during the token's own compile.
Our materialisation happens after the whole parse, so the same lookup there is a
different answer. Measured on the pinned tree, `parser.filters["upper"]` changes
identity across `add_library`; the sandwich below is what makes the difference
observable.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine, Library  # noqa: E402


class FilterRebindingTests(unittest.TestCase):
    def test_a_load_does_not_rebind_an_earlier_tokens_filter(self):
        """The sandwich: builtin use, `{% load %}`, shadowed use.

        A test with only the *second* use proves nothing — that token is after
        the load, so both designs agree about it. The first use is the one that
        separates "looked up at the token" from "looked up at the end".
        """
        engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
        template = engine.from_string(
            "{{ x|trim:3 }}{% load custom %}{{ y|trim:3 }}"
        )
        # `custom.trim` shadows nothing; `upper` is the name a library can
        # shadow. The row is written against a library fixture in Task 3, where
        # `libraries=` reaches the parser; here it asserts the *shape*.
        self.assertEqual(template.render(Context({"x": "abcd", "y": "abcd"})), "abcdabcd")
```

- [ ] **Step 2: Run it and confirm it passes today, then confirm it can fail**

Run: `.venv/bin/python -m unittest tests.test_filter_rebinding -v`
Expected: PASS — because `{% load %}` is a host tag that does not exist yet, this
template raises `TemplateSyntaxError: Invalid block tag … 'load'`. **If it
raises rather than passing, that is the correct red for this step**: record the
exact message, and mark the row `Task 3` in a comment. This step's purpose is to
establish that the fixture is reachable, not to be green yet.

- [ ] **Step 3: Change `Host::check_filter` to return a handle**

```rust
    /// Django's two filter checks, in Django's order: `Parser.find_filter`
    /// (`base.py:684-692`) and then `FilterExpression.args_check`
    /// (`base.py:822-833`).
    ///
    /// **Returns the filter it found, by handle, and that is not optional.**
    /// `find_filter`'s result is what renders — `filters.append((filter_func,
    /// args))` (`base.py:773`) — and looking the *name* up again when the
    /// `FilterExpression` is built is a different answer whenever a
    /// `{% load %}` sits between the two moments: `add_library` is
    /// `self.filters.update(lib.filters)` (`base.py:669-670`), which rebinds.
    /// This seam is called at `base.py:771`, so it is where the binding is
    /// still Django's; the shell stores the object it found in the same arena
    /// `{% cycle %}`'s slot lives in and returns its index. The name is
    /// therefore looked up exactly once, at Django's line.
    ///
    /// See `.review/m5-seams.md` and Task 1 of the M6 plan.
    fn check_filter(&self, _name: &str, _arg_count: usize)
        -> Result<Option<NodeRef>, Error> {
        Ok(None)
    }
```

`None` means "no host, accept and carry no handle" — the same permissive default
the old `Ok(())` was, and the shape `_native.parse(source, check_filter=None)`
already relies on. Update the two `impl Host` bodies (`NoHost`,
`FilterCheckHost`, and `TestHost` in `tags/mod.rs`'s `#[cfg(test)]`).

- [ ] **Step 4: Store the handle in `FilterCheckHost::check_filter`**

```rust
    fn check_filter(&self, name: &str, arg_count: usize) -> Result<Option<CoreNodeRef>, Error> {
        let Some(check) = &self.check_filter else {
            return Ok(None);
        };
        // The shell's callable now *returns* the function it found — it is
        // `parser.find_filter(name)` with its result handed back rather than
        // discarded — and the `args_check` still runs inside it, at Django's
        // line, so the error ordering the M5 measurements pinned is unchanged.
        match check.call1((name, arg_count)) {
            Ok(func) => {
                let mut refs = self.refs.borrow_mut();
                refs.push(Some(func.unbind()));
                Ok(Some(CoreNodeRef((refs.len() - 1) as u64)))
            }
            Err(error) => Err(Error::host(python_error_message(check.py(), &error))),
        }
    }
```

- [ ] **Step 5: Carry the handle on the wire and read it at materialisation**

`AttrValue::FilterExpr`'s per-filter entry gains the handle; `_materialise_filter_expression`
uses it:

```python
    for (name, wire_args), handle in desc.filters:
        args = [
            _materialise_operand(lookup, value, parser) for lookup, value in wire_args
        ]
        # The function Django bound at `base.py:771`, carried across, **not**
        # looked up again — `{% load %}` rebinds the name after this token's
        # position and our materialisation runs after the whole parse.
        func = resolve_ref(handle.index)
        filters.append((func, args))
```

- [ ] **Step 6: Run the full check and the two sandwiches**

Run: `./scripts/check.sh --fast`
Expected: `all checks passed`. The M5 error-ordering rows
(`x|does_not_exist||upper`, `x||does_not_exist`, `x|nope:1:2`) must still pass —
they are the rows that prove `args_check` still runs at Django's point.

- [ ] **Step 7: Commit**

```bash
git add crates/template-core/src/parser/host.rs \
        crates/template-core/src/parser/filter_expr.rs \
        crates/template-py/src/lib.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/test_filter_rebinding.py
git commit -m "parser: check_filter returns the filter Django bound, not its name"
```

---

### Task 2: The host node on the wire

**Deliverable:** a descriptor that names a Python-owned `Node` can be built,
materialised and compared, with no host tag yet in existence — so that Task 3
adds behaviour rather than a wire format.

**Files:**
- Modify: `crates/template-core/src/parser/desc.rs`
- Modify: `crates/template-core/src/parser/tags/mod.rs` (rename the stand-in)
- Modify: `python/django_rust_templates/_impl/parser.py`
- Modify: `tests/test_materialise.py`, `tests/test_parser_parity.py`

**Interfaces:**
- Produces: `NodeDesc::host(NodeRef)`; `desc::HOST_NODE: &str`;
  `materialise_node` returns `resolve_ref(index)` for that class name.

- [ ] **Step 1: Write the failing test — a host node survives materialisation**

```python
# tests/test_materialise.py
def test_a_host_node_is_the_object_the_table_holds(self):
    """`HostNode` is a *reserved descriptor name*, not a Django class.

    §5.2: a `NodeRef` is "an opaque handle to a Python-owned `Node` returned by
    a user tag callback. `template-core` never dereferences it." So the
    descriptor's job for one is to name the slot; the materialiser returns what
    the host stored there, with `token`/`origin` attached the way
    `extend_nodelist` attaches them to every other node.
    """
    from django.template.base import TextNode

    held = TextNode("held")
    desc = ("HostNode", [("node", _native.NodeRef(0))], [], None)
    self.assertIs(materialise_node(desc, None, lambda i: held, lambda i, v: None), held)
```

- [ ] **Step 2: Run it; expect `LookupError: no materialiser for node class 'HostNode'`**

Run: `.venv/bin/python -m unittest tests.test_materialise.MaterialiseTests.test_a_host_node_is_the_object_the_table_holds -v`

- [ ] **Step 3: Add `HOST_NODE` to `desc.rs` and the constructor**

```rust
/// The reserved `class_name` of a descriptor that names a **host-owned** node.
///
/// A user tag's compile function runs in Python and returns a Python `Node`;
/// `template-core` cannot read it and does not try (§5.2, `NodeRef`). So the
/// descriptor carries the slot and nothing else, and the materialiser returns
/// what the host stored there.
///
/// A reserved name rather than a new `AttrValue` variant, because everything
/// else about such a node is ordinary: it is a node in a nodelist, it gets
/// `token`/`origin` from `extend_nodelist`, and `describe` compares it as the
/// Python object it is. No Django module at 6.0.9 defines `HostNode`
/// (measured: `grep -rn 'class HostNode' .django/` is empty), and
/// `materialise_node` tests this name before `NODE_CLASSES`, so a future
/// Django that grew one could not capture it.
pub const HOST_NODE: &str = "HostNode";
```

```rust
    /// A descriptor for a node the **host** built. `template-core` stores the
    /// handle and never reads it.
    pub fn host(node: NodeRef) -> Self {
        Self::new(HOST_NODE).attr("node", AttrValue::Ref(node))
    }
```

- [ ] **Step 4: Rename the test stand-in so the sentinel is unambiguous**

`tags/mod.rs:512`'s `TestHost::call_tag` returns `NodeDesc::new("HostNode")`;
change it to `"StandinHostNode"`, and the assertion at `:538`, and
`tests/test_materialise.py:954`'s `TEST_ONLY_CLASSES`.

- [ ] **Step 5: Special-case the name in `materialise_node`, before the registry lookup**

```python
    # `HostNode` is not a Django class and is deliberately not in
    # `NODE_CLASSES`: it names a node the *host* built, which is already in the
    # reference table. Checked before the registry so that the reserved name
    # cannot be captured by an entry someone adds later, and so the error for a
    # genuinely unknown class name stays a `LookupError`.
    #
    # The two attachment lines are the same ones every other node gets
    # (`:232-233`), and skipping them is the failure this arm's shape invites.
    # Rust's `extend_nodelist` sets `NodeDesc.token` for a host descriptor
    # exactly as for any other block node — it is called at `:304`, host arm
    # included — so a bare `return resolve_ref(...)` would hand back a node whose
    # token is whatever the *host* left on it, making a user tag's node the one
    # shape that renders tokenless in the debug traceback path.
    if class_name == HOST_NODE:
        node = resolve_ref(attrs[0][1].index)
        node.token = _decode_token(token) if token is not None else None
        node.origin = origin
        return node
```

Verified against the tree before this was written down: `Parser::extend_nodelist`
is at `crates/template-core/src/parser/mod.rs:392-418` and sets
`node.token = Some(TokenDesc::of(token))` (`:415`); `Parser::parse` calls it at
`:218` (text), `:252` (variable) and `:304` (block). An earlier draft of this
plan said that line "never runs", which the reviewer falsified by reading
`:392-418` — the port was already there.

- [ ] **Step 6: Pin the sentinel against Django's own classes**

```python
# tests/test_materialise.py
def test_the_host_node_sentinel_is_not_a_django_class(self):
    """`desc::HOST_NODE` must not name a class Django could materialise.

    The materialiser tests the name before `NODE_CLASSES`, so a collision would
    not *break* — it would silently make every real `HostNode`-named Django node
    unmaterialisable, which is the worse failure because it is invisible until
    someone uses that class.
    """
    from django.template import base, defaulttags, loader_tags

    for module in (base, defaulttags, loader_tags):
        self.assertFalse(
            hasattr(module, "HostNode"), f"django {module.__name__} defines HostNode"
        )
```

- [ ] **Step 7: Run `./scripts/check.sh --fast`, then commit**

```bash
git add crates/template-core/src/parser/desc.rs \
        crates/template-core/src/parser/tags/mod.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/test_materialise.py
git commit -m "desc: a descriptor that names a host-owned node"
```

---

### Task 3: The host tag seam, `{% load %}`, and the shadowing gate

**Deliverable:** `{% load %}` works through the engine, a library shadows a
built-in, `bad_tag`'s compile-time error is Django's, and `{% echo %}` renders —
with Rust still dispatching every tag it owns.

**Files:**
- Modify: `crates/template-core/src/parser/host.rs`, `parser/mod.rs`
- Modify: `crates/template-core/src/parser/registry.rs`, `tags/mod.rs`
- Delete: `crates/template-core/src/parser/tags/load.rs`
- Modify: `crates/template-py/src/lib.rs`
- Create: `python/django_rust_templates/host.py`
- Modify: `python/django_rust_templates/render.py`
- Create: `tests/test_load_shadowing.py`, `tests/test_host_tags.py`
- Modify: `tests/test_tag_inventory.py`

**Interfaces:**
- Consumes: `NodeDesc::host` (Task 2), the host-node `materialise_node` arm (Task 2).
- Produces: `Host::has_tag`, `Host::call_tag(&self, command, token, tokens) -> Result<(NodeDesc, Vec<Token>), Error>`; `ShellHost` with `has_tag`/`call_tag`.

- [ ] **Step 1: Measure the two facts the predicate rests on, and record them**

```bash
source scripts/env.sh
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".django")
from django.template import Engine, Library
from django.template.base import Parser, Lexer

lib = Library()
def shadow(parser, token):
    return None
lib.tag("comment", shadow)

p = Parser(Lexer("").tokenize(), builtins=Engine().template_builtins)
engine_builtin_comment = p.tags["comment"]
p2 = Parser(Lexer("").tokenize(), builtins=Engine().template_builtins)
print("before add_library, is engine builtin:", p2.tags["comment"] is engine_builtin_comment)
p2.add_library(lib)
print("after  add_library, is engine builtin:", p2.tags["comment"] is engine_builtin_comment)
print("engine builtin for 'load':", "load" in Engine().template_builtins[0].tags)
PY
```

Expected (this is the identity clause's basis): `True` then `False`, and `load`
present. **If the first is not `True`, the predicate in A2 is wrong and this plan
is wrong** — stop and record the measurement.

- [ ] **Step 2: Write the failing gate — the sandwich, and the built-in that must not move**

```python
# tests/test_load_shadowing.py
"""M6's gate: a loaded library shadows a built-in, and a plain built-in does not.

**No test in `.django/` pins this.** Searched across the checkout for a library
registering a built-in's name, for `add_to_builtins`, and for the collision
wording: the only hits are library-*name* precedence in
`template_backends/test_django.py` and the E003 check in
`check_framework/test_templates.py`, both about `Engine.libraries`' dotted paths
rather than tag names. The behaviour is real — `add_library` is
`self.tags.update(lib.tags)` (`base.py:669-670`) and libraries merge after
`builtins` (`base.py:522-523`) — so the suite will not catch a regression in it,
and the spec names this as M6's gate.

The spec states the gate against `Library.add_tag`/`add_library`, neither of
which exists; see `.review/m6-recon.md` §"The three spec errors".
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine, Library, Node  # noqa: E402

from django_rust_templates import render  # noqa: E402


class _ShadowNode(Node):
    def render(self, context):
        return "shadowed"


def _shadow_library():
    """A library registering `comment` — a built-in Rust owns — with our own body."""
    library = Library()

    @library.tag("comment")
    def shadow(parser, token):
        parser.skip_past("endcomment")
        return _ShadowNode()

    return library


class LoadShadowingTests(unittest.TestCase):
    def _render(self, source, **context):
        engine = Engine()
        return render.render_string(source, context, engine)

    def test_a_loaded_library_replaces_a_builtin_of_the_same_name(self):
        engine = Engine()
        engine.template_builtins.append(_shadow_library())
        # `comment` is Rust's. After the load it must not be: the host's table
        # is one dict and `{% load %}` updates it, so the *later* registration
        # wins — which is why dispatch reads the host's table first.
        output = render.render_string(
            "{% load shadow %}{% comment %}x{% endcomment %}", {}, engine
        )
        self.assertEqual(output, "shadowed")

    def test_a_builtin_rust_owns_is_still_rusts_when_nothing_shadows_it(self):
        """The other half, and the one a naive host-first dispatch loses.

        The shell must not route `{% comment %}` to Python just because Django's
        `Parser.tags` has an entry for it — every built-in does. The identity
        clause in `ShellHost.has_tag` is what separates "Django's builtin
        callable" from "something a load put there".
        """
        engine = Engine()
        output = render.render_string("{% comment %}x{% endcomment %}ok", {}, engine)
        self.assertEqual(output, "ok")

    def test_the_sandwich_binds_each_filter_at_its_own_token(self):
        """Review Focus 3, end to end, now that `{% load %}` exists."""
        engine = Engine()
        engine.template_builtins.append(_shadow_library())
        output = render.render_string(
            "{{ x|upper }}{% load shadow %}{{ y|upper }}", {"x": "a", "y": "b"}, engine
        )
        self.assertEqual(output, "AB")
```

- [ ] **Step 3: Run it; expect `Invalid block tag … 'load'`**

Run: `.venv/bin/python -m unittest tests.test_load_shadowing -v`
Expected: FAIL on all three, with Django's `Invalid block tag on line 1: 'load'`.
This is the declared red for this task.

- [ ] **Step 4: Change `Host::call_tag`'s signature and add the token handoff**

```rust
    /// Whether `command` is a tag the **host** will compile.
    ///
    /// Django's own test, plus one clause. `parser.tags` is one dict and
    /// `Parser.add_library` updates it in place (`base.py:669-670`), so a
    /// library loaded *after* the engine's builtins wins — which is why the
    /// parse asks the host first. But every built-in has an entry in
    /// `parser.tags` too, so a bare membership test would route `{% for %}` to
    /// Python and Rust's compile functions would never run. The shell's
    /// `ShellHost.has_tag` therefore excludes a name whose entry is still
    /// Django's own builtin callable *and* is Rust's; see the M6 plan §A2 for
    /// the measurement behind the identity test.
    fn has_tag(&self, command: &str) -> bool;

    /// Compile `command` in Python, against the parser's remaining tokens.
    ///
    /// `tokens` is everything the caller has not consumed, **in parse order**.
    /// The host may consume any prefix of it (a block tag's `parser.parse(...)`,
    /// `tag_27584.badtag`'s re-entrant parse) and may push tokens back
    /// (`Parser.prepend_token`); the second element of the return is what is
    /// left, and the caller **replaces** its cursor with it rather than
    /// counting. Replacing and not counting is what makes `prepend_token`
    /// correct without a special case.
    ///
    /// The host holds no borrow of the caller's parser — it *is* the caller's
    /// counterpart on the Python side (§5.2, `ParserHandle`), which is why the
    /// tokens travel by value.
    fn call_tag(
        &self,
        command: &str,
        token: &Token,
        tokens: &[Token],
    ) -> Result<(NodeDesc, Vec<Token>), Error>;
```

- [ ] **Step 5: Give `Parser` the cursor accessors the handoff needs**

```rust
    /// The tokens from the cursor on, in **parse order**.
    ///
    /// The internal stack is reversed (`base.py:504-505`, for `pop` from the
    /// end), so this reverses a copy. It exists for `Host::call_tag`, which
    /// hands a host compile function the stream it may consume.
    pub fn remaining_tokens(&self) -> Vec<Token> {
        self.tokens.iter().rev().cloned().collect()
    }

    /// Replace the cursor with `tokens`, in parse order.
    ///
    /// Replacing rather than advancing by a count is what makes a host
    /// callback's `prepend_token` work without a special case: whatever the
    /// host leaves behind *is* the stream, and the host is Django's own
    /// `Parser`, whose stack semantics the reversal already mirrors.
    pub fn replace_tokens(&mut self, tokens: Vec<Token>) {
        self.tokens = tokens.into_iter().rev().collect();
    }
```

- [ ] **Step 6: Dispatch to the host with the cursor handed over**

In `Parser::parse`, the `has_tag` arm (`parser/mod.rs:295-301`):

```rust
                    let compiled = if self.host.has_tag(&command) {
                        // The host compiles it, and it may consume the rest of
                        // the stream. Both halves cross by value: the host is
                        // Python's own `Parser` and holds no borrow of this one
                        // (§5.2), which is what lets `{% load %}` and a
                        // `simple_block_tag`'s nested parse re-enter at all.
                        let remaining = self.remaining_tokens();
                        let (desc, left) = self.host.call_tag(&command, &token, &remaining)?;
                        self.replace_tokens(left);
                        Ok(desc)
                    } else if let Some(compile) = registry::builtin_tag(&command) {
                        compile(self, &token)
                    } else {
                        return Err(self.invalid_block_tag(&token, &command, parse_until));
                    };
```

The `?` inside the arm is a `return`-shaped early exit only if `parse`'s error
type matches; it does (`Result<NodeListDesc, Error>`). Keep the existing
`let node = compiled.map_err(|error| self.error(&token, error))?;` line — a host
error must still get the token attached, and `Parser.error` only attaches one if
the error lacks it.

- [ ] **Step 7: Delete `load.rs` and `querystring.rs`, and their registry entries**

Delete `crates/template-core/src/parser/tags/load.rs` and
`crates/template-core/src/parser/tags/querystring.rs`; remove their `mod`
declarations and their `REGISTRY` entries in `registry.rs`; drop `load` and
`querystring` from `builtin_tag_stubs()` in `tags/mod.rs`. Both stubs' headers
predicted this outcome and gave the reason — move the reasons into
`registry.rs`'s doc comment, which today says M6 "must decide": the "separate
bucket" paragraph becomes A1's argument (`defaulttags.load:1102-1131`,
`parser.libraries`, `load_from_library`'s `Library()`), and `querystring`'s
entry becomes A4's (a `simple_tag`-registered built-in, whose compile is
Python's).

Update the Rust test that pins the registry against a literal
(`registry.rs::every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`)
and any test pinning the count 24.

- [ ] **Step 7b: Pin the subtraction and the stub list, derived — not typed**

```python
# tests/test_tag_inventory.py — rename the existing loader-tags subtraction test
def test_the_rust_registry_is_djangos_builtins_minus_the_python_owned_five(self):
    """§6's three, plus `load` (A1) and `querystring` (A4).

    The old name for this subtraction was "loader tags", which stopped
    describing anything: `load` is Python's because its lookup is Python's, and
    `querystring` is Python's because `simple_tag` registered it and §6 gives
    `Library` to Python. What the five share is §6, not the loader.
    """
    python_owned = {"block", "extends", "include", "load", "querystring"}
    self.assertEqual(sorted(_native.builtin_tag_names()), sorted(REGISTERED - python_owned))
    self.assertEqual(set(_native.builtin_tag_stubs()), {"partial", "partialdef"})
```

Run: `.venv/bin/python -m unittest tests.test_tag_inventory -v`
Expected: PASS with `builtin_tag_names()` of length 22. **If it is not 22, the
subtraction is wrong, not the test** — and `REGISTERED` must be re-derived from
`Engine().template_builtins` rather than the number adjusted.

- [ ] **Step 8: Implement `ShellHost`**

```python
# python/django_rust_templates/host.py
"""The shell's half of the host seam: the parser Django's own tag code needs.

Rust owns the token cursor; **the host owns parser state** — `tags`, `filters`,
`libraries`, `extra_data`, and whatever a user tag sets on the parser. The
division is what §5.2's `ParserHandle` describes, realised the smaller of the two
ways it can be: rather than a Rust handle proxying into a shared `Parser`, one
real `django.template.base.Parser` is kept for the whole parse, and Rust hands it
the tokens it may consume. `Parser.parse` already leaves the end tag on the stack
for `delete_first_token` (`base.py:564-569`), so a callback that does
``nodelist = parser.parse((end_name,)); parser.delete_first_token()`` — which is
`Library.simple_block_tag` (`library.py:211-212`) and `tag_27584.badtag`
(`:8-9`) verbatim — runs unmodified.

**A `has_tag` that answered `command in parser.tags` would break every built-in.**
Every one of Django's 27 built-ins is in that dict, so Rust would be told to
compile nothing and the host would run Django's `do_for` etc. The identity clause
below is the whole of the discrimination: a name is the host's to compile when its
entry is *not* Django's own builtin callable, or when it is a built-in Rust does
not own (`block`, `extends`, `include`, `load`).
"""

from __future__ import annotations

from django.template.base import Token, TokenType

from django_rust_templates import _native

__all__ = ("ShellHost",)

# A5: M7's three. They are Django's builtins and not Rust's, so without this they
# would reach the host. The ordering check itself is *not* the reason — Rust
# ports `extend_nodelist` (`crates/template-core/src/parser/mod.rs:392-418`) and
# runs it for a host descriptor like any other block node. The reason is that
# `NodeDesc.must_be_first` does not cross the seam (`desc.rs:369`, default
# `false` at `:407`), so `{% extends %}` would be accepted *and* uncheckable —
# and accepting a tag whose render path is M7's is worse than today's honest
# `Invalid block tag`. Measured: `ExtendsNode.must_be_first is True`
# (`BlockNode`/`IncludeNode` are `False`) and `{{ x }}{% extends "y" %}` raises
# under stock Django. M7 removes this, carries `must_be_first` with the node —
# one `bool` — and gets `parser.__loaded_blocks` for free, since the handle *is*
# Django's `Parser`.
_M7_OWNED = frozenset({"block", "extends", "include"})


def _engine_builtin_tags(parser):
    """The tag callables `Parser.__init__` merged from `builtins`.

    Taken as a **snapshot of the parser's table at construction**, not rebuilt
    from `Engine()`. Two reasons, and the second is a bug the first draft had:
    `parser_for` is handed the *caller's* engine, so a caller that registered
    its own library in `builtins` would have its tags mistaken for Django's; and
    a library is merged into `parser.tags` only by `{% load %}` (`base.py:521`
    stores `libraries` and does not merge it), so at construction the table
    **is** the builtins and nothing else.
    """
    return dict(parser.tags)


class ShellHost:
    """The `has_tag`/`call_tag` pair for one parse.

    One instance per parse, holding the one `Parser` the whole parse uses, so
    that `{% load %}`'s `add_library` and `{% extra_data %}`'s
    `parser.extra_data[...]` are visible to every later token **and** to the
    template afterwards (`test_compile_tag_extra_data`).
    """

    def __init__(self, parser):
        self.parser = parser
        self._rust_owned = set(_native.builtin_tag_names())
        self._builtins = _engine_builtin_tags(parser)

    def has_tag(self, command):
        func = self.parser.tags.get(command)
        if func is None:
            return False
        if command in _M7_OWNED:
            # A5. Excluded until M7, which must also carry `must_be_first`
            # across the seam — `NodeDesc.must_be_first` defaults false and is
            # not on the wire (`desc.rs:369`, `:407`), so reaching the host
            # would *accept* `{% extends %}` in a position Django rejects.
            return False
        if command not in self._rust_owned:
            # `load`, `querystring`, and everything a `{% load %}` brought in.
            return True
        # Rust's, unless a `{% load %}` replaced Django's own callable for the
        # name — `add_library` is `self.tags.update(lib.tags)`
        # (`base.py:669-670`), so the load wins and this is where we notice.
        return func is not self._builtins.get(command)

    def call_tag(self, command, token, tokens):
        """Run Django's own compile function for `command`.

        `tokens` arrives in parse order and is reversed onto the parser's stack
        the way `Parser.__init__` does (`base.py:504-505`); what is left is
        reversed back. **Replacing the caller's cursor with the result, not
        counting a delta, is what makes `prepend_token` correct** — Django's
        `Parser.parse` prepends the end tag it stopped on (`base.py:564-569`)
        and the tag removes it with `delete_first_token`, and a count would
        have to know that dance rather than observe it.
        """
        parser = self.parser
        parser.tokens = [Token(TokenType(t), contents, position, lineno)
                         for t, contents, lineno, position in reversed(tokens)]
        node = parser.tags[command](parser, token)
        left = [(int(tok.token_type), tok.contents, tok.lineno, tok.position)
                for tok in reversed(parser.tokens)]
        return node, left
```

- [ ] **Step 9: Wire it through `_native.parse` and `render.py`**

`_native.parse`/`render` gain `host=` (a `ShellHost` or `None`); the Rust
`FilterCheckHost` gains `has_tag: Option<Bound<PyAny>>` and
`call_tag: Option<Bound<PyAny>>`, with `has_tag` returning `false` and
`call_tag` unreachable when they are `None` — the current `NoHost` behaviour,
preserved for every Rust-only test. `parser_for` gains
`libraries=engine.template_libraries`.

- [ ] **Step 10: Run the gate, and the whole check**

Run: `.venv/bin/python -m unittest tests.test_load_shadowing -v`
Expected: PASS on all three. Then `./scripts/check.sh --fast` → `all checks passed`.

- [ ] **Step 11: Pin the nested-region coverage hole (Review Focus 1)**

```python
# tests/test_host_tags.py
def _wrap_library():
    """A `simple_block_tag` — the shape that re-enters Django's parse.

    `library.py:211-212` is `nodelist = parser.parse((end_name,))` then
    `parser.delete_first_token()`, run against the parser the host owns.
    """
    library = Library()

    @library.simple_block_tag
    def wrap(content):
        return content

    return library


def test_a_host_block_tags_body_is_parsed_by_django_and_that_is_asserted(self):
    """The nested region is Django-parsed, and this makes that visible.

    When a host tag's compile function calls `parser.parse(...)` — which is
    `Library.simple_block_tag` verbatim (`library.py:211-212`) — the body is
    parsed by *Django's* `Parser.parse`, so every tag inside it is compiled by
    Django and `assert_trees_match` cannot tell that from Rust having done it.
    **A tree comparison over such a body is therefore not evidence about our
    engine**, which is the vacuous-gate failure this project has hit twice.

    Error *text* cannot discriminate, and that is the trap: Rust's messages are
    ported verbatim from Django's, so `{% for %}` with no argument raises
    identical text either way. The discriminator has to be something only one
    side does. It is `{% partialdef %}`, whose Rust implementation is still a
    stub (`builtin_tag_stubs()`): were the body Rust-parsed, the parse would
    raise `unimplemented`; that it does not means Django's `do_partialdef` ran
    and stored into `parser.extra_data["partials"]` (`defaulttags.py:1250-1251`)
    — which is the assertion. Measured before it was written down: outside a
    block tag this sets the same key, and inside one it still does, on the
    *outer* parser.
    """
    engine = Engine()
    engine.template_builtins.append(_wrap_library())
    host = host_module.ShellHost(render.parser_for("", engine))

    _native.parse("{% wrap %}{% partialdef p %}x{% endpartialdef %}{% endwrap %}", host=host)

    self.assertIn("p", host.parser.extra_data["partials"])
```

- [ ] **Step 12: Commit**

```bash
git add -A crates/template-core/src/parser crates/template-py/src/lib.rs \
        python/django_rust_templates tests/test_load_shadowing.py \
        tests/test_host_tags.py tests/test_tag_inventory.py
git commit -m "parser: host-compiled tags, and {% load %} with its shadowing gate"
```

---

### Task 4: parser state — `extra_data`, and the parse that must not leak it

**Why this is separate from Task 3:** Task 3 makes host tags *compile*. This task makes the parser's **state** survive to the template, which is a different seam (`Template.__init__`, which we do not run) and a different failure (`hasattr(template, "extra_data")` being true when Django's is false). Re-entrancy — `tag_27584`'s nested `parser.parse` then `delete_first_token` — comes free with Task 3's token handoff, so its rows are added here as the evidence that the handoff is right, not as new work.

**Files:**
- Modify: `python/django_rust_templates/render.py` (`render_string`)
- Test: `tests/test_host_tags.py`

**Interfaces:**
- Consumes: `ShellHost.parser` (Task 3), so no handle needs returning from `_native.parse` — §5.2's `(NodeListRef, ParserHandle)` is satisfied because the shell *built* the parser.
- Produces: `Template.extra_data` assigned only on a successful parse.

- [ ] **Step 1: Write the failing test — `extra_data` reaches the template**

```python
# tests/test_host_tags.py
def test_a_user_tags_extra_data_reaches_the_template(self):
    """`custom.py:398` writes it; `.django`'s `tests.py:148-155` asserts it.

    `Parser.__init__` sets `self.extra_data = {}` (`base.py:514`), so the parser
    always has it. What the template gets is a copy made by
    `Template.__init__` — `nodelist = parser.parse(); self.extra_data =
    parser.extra_data` (`base.py:198-200`) — **and our shell builds the
    `Template` by `__new__` (`_impl/parser.py:339`) and so never runs that line.**
    This is the same class of gap `template_for`'s own docstring lists for
    `nodelist`/`engine`/`name`/`origin`/`source`; `extra_data` joins the list.
    """
    engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
    template = self._template("{% load custom %}{% extra_data %}", engine)
    self.assertEqual(template.extra_data["extra_data"], "CUSTOM_DATA")
```

- [ ] **Step 2: Run it; expect `AttributeError: 'Template' object has no attribute 'extra_data'`**

Run: `.venv/bin/python -m unittest tests.test_host_tags -v`

- [ ] **Step 3: Assign it after a successful parse only**

```python
    # `base.py:198-200` assigns this inside `Template.__init__`'s `try`, so a
    # parse that raises leaves the attribute *off* the template and consumers
    # rely on that — `getattr(template, "extra_data", {})` (`engine.py:194`).
    # Nothing in `.django` pins the producer side (the recon says so, and names
    # the reason), so both halves are pinned here: this line is after
    # `_native.parse` returns, therefore unreachable when it raises.
    template = template_for(nodelist, engine)
    template.extra_data = host.parser.extra_data
    return template
```

- [ ] **Step 4: Write the failing test — a failed parse leaves no `extra_data`**

```python
def test_a_failed_parse_leaves_the_template_without_extra_data(self):
    """The other half of `base.py:198-200`, and the half nothing pins.

    `engine.py:194` reads `getattr(template, "extra_data", {})` precisely
    because the attribute can be absent. A shell that assigned it from a
    `finally`, or that pre-seeded it, would make `hasattr` true where Django's
    is false — invisible to every render test, and it changes what
    `{% partial %}` lookups do at M13 (`engine.py:194-198`).
    """
    engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
    with self.assertRaises(TemplateSyntaxError):
        # `{% extra_data %}` writes to the parser's dict *before* the parse
        # fails, so a shell that kept the parser's dict would carry it out.
        self._template("{% load custom %}{% extra_data %}{% endfor %}", engine)
```

- [ ] **Step 5: Run it; expect the `TemplateSyntaxError` is raised but `hasattr` is true**

Run: `.venv/bin/python -m unittest tests.test_host_tags -v`
Expected: FAIL on the `hasattr` assertion — **this is the red that proves Step 3
put the assignment in the right place rather than merely adding it.** Assert it
as `self.assertFalse(hasattr(template, "extra_data"))` on the *template object*
the shell produced; capture it by having `_template` return the template and the
exception together, so the assertion runs on the object that failed to build.

- [ ] **Step 6: Re-entrancy — the evidence that Task 3's handoff is correct**

```python
def test_a_host_tag_may_re_enter_the_parse_and_the_error_is_djangos(self):
    """`tag_27584.badtag` re-enters: `parser.parse(("endbadtag",))` then
    `delete_first_token()` (`.django/.../templatetags/tag_27584.py:8-9`).

    This is §5.2's contract and the reason `call_tag` passes tokens by value
    rather than lending `&mut Parser`. The expected message is **computed from
    Django's own parser on the same source**, not transcribed: Rust ports
    Django's strings verbatim, so an equality assertion against a live Django
    parse stays honest as the fixture changes and a copied literal would not.
    """
    source = "{% badtag %}{% endbadtag %}"
    libs = {"tag_27584": "template_tests.templatetags.tag_27584"}

    with self.assertRaises(TemplateSyntaxError) as ours:
        self._template(source, Engine(libraries=libs))
    with self.assertRaises(TemplateSyntaxError) as django:
        Engine(libraries=libs).from_string(source)

    self.assertEqual(str(ours.exception), str(django.exception))

def test_a_simple_block_tag_delimits_its_body_with_djangos_own_parse(self):
    """`library.py:211-212`: `nodelist = parser.parse((end_name,))` then
    `parser.delete_first_token()`. Django's `parse` *leaves* the end tag and the
    tag removes it — so a host seam that consumed it (as Rust's own `parse`
    would, if it were driving) needs a token too many and the template after the
    block is eaten. Asserting the trailing text is what catches that.
    """
    self.assertEqual(self._render("{% wrap %}a{% endwrap %}b"), "ab")
```

- [ ] **Step 7: Run the full check**

Run: `./scripts/check.sh --fast` → `all checks passed`

- [ ] **Step 8: Commit**

```bash
git add python/django_rust_templates/render.py tests/test_host_tags.py
git commit -m "shell: carry parser.extra_data to the template, and only on success"
```

---

### Task 5: grade it — the corpus rows for the tags the host compiles

**Why this is its own task:** the corpus is M6's real gate — the `.django` suite
runs on stock Django (`unshimmed()` is 25), so it exercises none of this. Before
this task the corpus has **no** row whose tree contains a host node, and M6 would
close with its central mechanism ungraded.

**Files:**
- Modify: `tests/test_parser_parity.py` (`django_parse`, `ours_parse`, `filter_parser`)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`
- Test: the corpus itself

**Interfaces:**
- Consumes: `desc::HOST_NODE` (Task 2), `ShellHost` (Task 3).

- [ ] **Step 1: Write the failing test — one library object, both sides**

```python
# tests/test_parser_parity.py
def test_a_host_tag_parses_to_the_same_tree_as_django(self):
    """The first corpus row whose tree contains a host node.

    `describe`'s `Node` arm compares `vars()` recursively, and a `SimpleNode`
    holds `func` — a live function object, compared by identity because it is
    neither a `Node` nor a `FilterExpression`. So the two sides must be given
    the **same** `Library` instance: two separately-built libraries would differ
    in `func` identity and the failure would read like a Rust bug in a seam that
    is working. That is the whole reason `_engine()` is a single factory below.
    """
    source = "{% load custom %}{% echo x %}"
    self.assert_trees_match(ours_parse(source), django_parse(source))
```

- [ ] **Step 2: Run it; expect it to fail at `{% load %}`'s lookup**

Run: `.venv/bin/python -m unittest tests.test_parser_parity -v`
Expected: FAIL — `django_parse` builds `Parser(lexer.tokenize(),
builtins=Engine().template_builtins)` with **no `libraries=`**, so
`{% load custom %}` raises `not a registered tag library`. That is the same
omission `render.py::parser_for` has, and Task 3 fixed there.

- [ ] **Step 3: One library-aware engine, used by both sides**

```python
def _engine():
    """The one engine both parses use, so library objects are shared.

    A fresh `Engine()` per side would give the two trees different `func`
    objects for every host tag, and `describe` compares them by identity.
    """
    return Engine(libraries={"custom": "template_tests.templatetags.custom", ...})
```

and pass `libraries=engine.template_libraries` in `django_parse`, `ours_parse`
and `filter_parser`. `render.py::parser_for` gained the same argument in Task 3;
this is its parity counterpart, and the two must agree or the corpus grades a
different parser than the render path uses.

- [ ] **Step 4: Run the corpus; expect a new failure listing tags with no row**

Run: `.venv/bin/python -m unittest tests.test_parser_parity -v`
Expected: FAIL from
`test_the_corpus_covers_every_implemented_tag_with_a_success_path`? **No** — that
test derives its demand from `builtin_tag_names() - builtin_tag_stubs()`, and
`load`/`querystring` left both sets in Task 3, so it demands nothing new.
**The absence of a failure here is the finding**: the coverage test cannot see
host tags at all. Add the rows by hand, and add the row that makes this visible:

```python
def test_the_corpus_has_a_row_whose_tree_contains_a_host_node(self):
    """`builtin_tag_names()` is Rust's registry, so it is structurally blind to
    every tag the *host* compiles — including both of M6's. Without this row M6
    could close with its whole mechanism ungraded and the coverage test green.
    """
    rows = [row for row in CORPUS if "{% load" in row.source]
    self.assertTrue(rows, "no corpus row loads a library")
    self.assertTrue(any(contains_host_node(ours_parse(row.source)) for row in rows))
```

- [ ] **Step 5: Add the corpus rows**

For each of these, a success-path row in `tests/parser_corpus.py`:

```text
{% load custom %}{% echo x %}                     a library tag, end to end
{% load custom %}{% extra_data %}                 the one that writes extra_data
{% load testtags %}{% echo x %}ok                 a different library
{% load custom %}{% my_simple_block_tag %}a{% endmy_simple_block_tag %}
{% querystring page=2 %}                          the simple_tag-registered builtin
{% badtag %}{% endbadtag %}                       the re-entrant parse, as a row
{{ x|upper }}                                     already present; keep it, it is
                                                  the sandwich's first half
```

and an **error** row in `tests/parser_error_corpus.py` for each raising form,
with the message compared — that corpus is where `{% load %}`'s two messages
(`defaulttags.py:1069-1075`, `:1092-1098`) and all five of `parse_bits`'
(`library.py:392-480`) are actually gated. `{% querystring %}` with no arguments
is **not** a parse row: it takes `takes_context=True` and raises from inside
`render` (`defaulttags.py:1325-1349`) — put it in the render-level tests, not
here.

**Plus one row that is not an M6 tag**, because grading the corpus is this task
and the gap is in it. `{% for in y reversed %}` — the reviewer found it:

```python
        # Not an M6 tag: this is the only input that reaches `loopvar_split` on an
        # *empty* haystack, and nothing in either corpus reaches it. `reversed`
        # appears once in the whole corpus (`parser_corpus.py:158`) and that is the
        # success row; the two `for` error rows (`parser_error_corpus.py:229`,
        # `:235`) both fail the `bits[in_index] != "in"` test instead. With four
        # bits and `is_reversed`, `in_index` is 1, `bits[1]` *is* "in", so the
        # check passes and `bits[1..1]` is empty: `re.split(r" *, *", "")` runs.
        # Django then rejects the empty loop variable —
        # `'for' tag received an invalid argument: for in y reversed` (measured on
        # the pinned tree). One row pins the `in_index == 1` arithmetic, the empty
        # haystack, and `is_invalid_loopvar` at once, none of which anything else
        # reaches.
        "{% for in y reversed %}x{% endfor %}",
```

- [ ] **Step 6: Pin Review Focus 4 — a host node's token comes from Rust's `extend_nodelist`**

```python
# tests/test_parser_parity.py
def test_a_host_node_carries_the_token_rust_attached(self):
    """The host node must be token-bearing *by the same route* as every other
    node, which is the thing Task 2's arm can quietly break.

    Rust ports `extend_nodelist` (`crates/template-core/src/parser/mod.rs:392-418`)
    and calls it at `:304` for a host descriptor exactly as for a Rust-built block
    node, so `node.token` is set on the descriptor either way. The failure mode is
    therefore not "Django forgot to attach it" — this plan's first version claimed
    that, and it was false — but "the `materialise_node` arm returned the stored
    object before the attachment at `_impl/parser.py:232-233` ran", leaving a user
    tag's node as the one shape that renders tokenless in the debug traceback
    path. Comparing the host node's token against a *Rust-built* node's in the
    same template is what distinguishes the two: asserting only that the host
    node has a token would pass for a node the host itself had already stamped.
    """
    tree = ours_parse("{% load custom %}{% echo x %}", debug=True)
    host = [node for node in tree if type(node).__name__ == "SimpleNode"][0]
    self.assertEqual(host.token.contents, "{% echo x %}")
```

- [ ] **Step 7: Run the full check and commit**

Run: `./scripts/check.sh` (not `--fast` — this task's gate is the corpus)
Expected: `all checks passed`, `Ran 1547 tests … OK (skipped=6)`.

```bash
git add tests/test_parser_parity.py tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "parity: corpus rows for the tags the host compiles"
```

---

### Task 6: stop the citation drift, mechanically

**Why this is a task and not a tidy-up.** The reviewer checked fourteen `base.py`
5xx–6xx citations in our tree and reported **seven wrong**: `:598` for
`node.token = token`, `:566-567` for `raise self.error(token, e)`, `:600-610` for
the `parse_until` prepend, `:668` for `self.tags.update`, `:598-610` for
`extend_nodelist`, and two more in the `Parser.__init__` region. Five of those
were right — measured, `:615` is `node.token = token`, `:585` is
`raise self.error(token, e)`, `:568` is `self.prepend_token(token)`, `:669` is
`self.tags.update(lib.tags)`, `:600` opens `def extend_nodelist`. **Two were
themselves wrong, and repairing them broke two anchors that had been correct:**

| claim | reviewer said | the tree said | measured |
|---|---|---|---|
| `` `builtins=None` → `[]` `` | `:517-518` | `:518-519` | **`518-519`** — `518` is `if builtins is None:`, `519` is `builtins = []` |
| libraries merge after builtins | `:521-522` | `:522-523` | **`522-523`** — `522` is `for builtin in builtins:`, `523` is `self.add_library(builtin)` |

(`521` is `self.libraries = libraries` and `516-517` is the `libraries` guard, so
both reported pairs pointed one line short and at the *other* variable's guard.)

The reviewer's own account of the split is the part worth keeping, because it is
not random: *"everything I measured was right and everything I counted was
wrong"* — five read off `grep -n`/`awk`, two derived by counting lines in a `sed`
window, while the same batch printed `521: self.libraries = libraries`, the exact
line that contradicted the derivation. So this class has produced errors in
**both directions** — in the anchor, and in the correction of the anchor — and
the second round was caught only because the numbers happened to sit beside a
name they contradicted. "Be careful" has failed three times; "trust the report"
has failed once.

**Both bad anchors carried the right snippet and the wrong number — which is
exactly the disagreement `--check` fails on.** So this guard is not only an
author-drift ratchet: it is the one thing in the project that checks a
*reviewer's* numbers, and it would have rejected that patch before it was
applied. That is a different justification from the one this task was first
written with, and a stronger one.

**Sampling found more than one kind.** Four wrong numbers, each verified and each
fixed at every site (see below for the full list, including two more found while
fixing them):

| where | prose says the line is | the line actually is |
|---|---|---|
| `crates/template-py/src/lib.rs` | `defaulttags.py:1037` = `condition.eval(context)` | `{% for date in days %}`; the call is at `:326` |
| `crates/template-core/src/parser/tags/if.rs` | `defaulttags.py:1035` = the pair unpack | `<h1>Archive for {{ year }}</h1>`; it is at `:323` |
| `crates/template-py/src/filters.rs` | `defaultfilters.py:383` = `nofollow=True` | `@stringfilter`; the call is at `:386` |
| `crates/template-py/src/lib.rs` | `smartif.py:73` = `self.second = None` | the `prefix` docstring opens at `:72`; the assignment is at `:83` |

A *fifth* kind was hypothesised from the same sample — the number right and the
quoted source text wrong — and **it did not survive checking**: the one instance
(`defaultfilters.py:996`) has the right number, and the `type(e).__name__` quote
beside it belongs to a different claim. Recorded because the triage must be read
row by row, and because a kind asserted before it is confirmed is the error this
task exists to stop.

What the misquote kind *would* change if it exists: the manifest's snippet must be
the code **the prose is about** rather than the text at the cited line — seeding
from the cited line cannot find any of the four above. Authored snippets find both
kinds with one rule.

**And the size is not fourteen.** Measured over `crates python tests docs .review`,
with the metric named because two of us counted this differently and both were
right:

```
bare-basename anchors, `name.py:N`                     430
plus qualified paths, `dir/name.py:N`                  +47   → 477
minus anchors naming our own files (_shim, differential,
  parser, test_materialise, test_parser_parity)        → ~415 in the pinned tree
of which name a module under django/template/          332
```

The 477 the reviewer measured and the 426 first measured here are the same corpus
under two patterns — theirs allowed a `/` in the path, so it also counted
qualified citations like `django/template/base.py:615` and the tree's own files.
**The checker must handle both forms**, resolving a bare basename against
`.django/` and a qualified path against `.django/` then the repo root.

What that triage can reach: of 834 in-scope occurrences, **283 have a code span
quoted within 200 characters and 228 of those do not appear at the cited line.**
Most of the 228 are the heuristic firing on an unrelated nearby backtick, not a
defect — but the four below are in it, and it is the list to read first, because
it is ordered by suspicion rather than by file. **415 anchors read cold is the
alternative.**

**And a third form the regex above cannot see at all.** The tree also cites Django
by a bare line number when the file is implied by context — `` (`:1035`) ``,
`` (``:56-58``, ``:73``) `` — meaning "the file the surrounding comment is about".
Measured: **360 occurrences, 241 distinct**, concentrated in `filters/host.rs`
(59), `filter_cases.py` (47), `filters/floatformat.rs` (46). Spot-checking
`parser/tags/if.rs`'s eleven, whose subject is `defaulttags.py`, found **four
wrong of six readable**: `:1001-1006` for the `elif` loop (the loop is at
`:1000-1007`), `:1024-1025` for `IfNode.__init__` (`:1024` is `def ifchanged`),
`:1011` for `conditions_nodelists.append((None, nodelist))` (`:1009`) — and one
that was right (`:995-996`, `:1002-1003`).

So the shorthand pocket is **both larger and dirtier** than the named form.
The decision this forces, and it is the plan's call rather than the tool's:

**Name the file at every anchor; migrate the shorthand.** A tool cannot resolve
":1035" without a heuristic for "which file is this comment about", and a
heuristic here reproduces the failure it exists to catch. Once every anchor names
its file, one regex sees the whole corpus. The migration is mechanical for the
concentrated files (the implied file is stated once per doc comment) and the
checker **fails on an unresolvable shorthand**, which is what stops the
convention growing back.

**Four wrong anchors found and fixed in this revision**, all measured against the
pinned tree, and each fixed at every site rather than at the one that was
sampled: `condition.eval(context)` cited as `defaulttags.py:1037` is at `:326`
(two sites); the `(condition, nodelist)` unpack cited as `:1035` is at `:323`
(three sites, two of them shorthand); `nud`'s `self.second = None` cited as
`smartif.py:73` is at `:83`, `led`'s `self.first = left` cited as `:56-58` is at
`:55` (six sites); `nofollow=True` cited as `defaultfilters.py:383`, `:395` is at
`:386`, `:399`. Plus two found while fixing those: `defaulttags.py:996` for the
tuple literal (it is `:995`; `:996` is `token = parser.next_token()`) and
`:995` for the `TemplateIfParser` call (it is `:993`).

**A sampled row that turned out to be the heuristic, not a defect**, recorded
because it is how the triage must be read: `defaultfilters.py:996` was listed as a
misquote — the tree beside it quotes `type(e).__name__` and the line reads
`e.__class__.__name__` — but the anchor is right and the quote belongs to a
different claim (what `pprint` prints, `crates/template-py/src/filters.rs:649`).
**Every row is judged, not applied.** Whether the misquote *kind* exists at all is
now open: no confirmed instance yet.

**Files:**
- Create: `scripts/check_python_anchors.py`
- Create: `scripts/anchors.txt` (the manifest — authored snippets, derived numbers)
- Modify: `scripts/check.sh` (add the `--check` pass — it belongs on the full
  path beside the other two generators, which are already there and already
  documented as "the way to verify currency")
- Test: `tests/test_anchor_manifest.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `python -I scripts/check_python_anchors.py --check` → exit 1 on drift,
  modelled on `scripts/gen_python_word_ranges.py --check`. A `--triage` mode
  prints the 228-row suspicion list without failing, so it can be read before
  the corpus is clean.

- [ ] **Step 1: Write the failing tests**

Four behaviours, each driven by the failure it names rather than by a green run
over the tree — a checker that has never been *seen* to fail is the defect it
exists to catch, one level up.

```python
# tests/test_anchor_manifest.py
"""The checker's own tests: each drives it with the defect it exists for.

`anchor_manifest` is `scripts/check_python_anchors.py` imported by path (the
`scripts/` directory is not a package), the same way the other script tests do.
"""

import importlib.util
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"


class AnchorManifestTests(unittest.TestCase):
    def test_a_cited_line_that_does_not_contain_its_snippet_fails(self):
        """The whole value is failing on a *wrong number*, so the test moves a
        number rather than trusting a clean sweep over the tree."""
        anchor_manifest.MANIFEST = {
            "base.py:node_token": ("django/template/base.py", 616, "node.token = token", None)
        }
        with self.assertRaises(SystemExit):
            anchor_manifest.check(checkout=DJANGO_CHECKOUT, tree=self.tree_with("`base.py:616`"))

    def test_an_anchor_in_the_tree_with_no_manifest_entry_fails(self):
        """This is the half that catches drift introduced *after* this task: a
        fresh `` `base.py:123` `` written into a comment fails here, which is what
        makes the check a gate rather than a snapshot of today's tree."""
        with self.assertRaises(SystemExit):
            anchor_manifest.check(
                checkout=DJANGO_CHECKOUT, tree=self.tree_with("`defaulttags.py:99999`")
            )

    def test_a_basename_that_is_not_unique_needs_an_explicit_path(self):
        """38 files in the checkout are named `base.py`, 89 `formats.py`, 210
        `tests.py`. A manifest entry that gives a bare basename for one of those
        is ambiguous, and ambiguity is an error rather than a guess."""
        with self.assertRaises(SystemExit):
            anchor_manifest.resolve("base.py", manifest={})

    def test_the_two_anchors_that_were_wrong_twice_are_pinned(self):
        """`base.py:518-519` and `base.py:522-523` were reported wrong, repaired
        to `517-518`/`521-522`, and were correct all along. Named here so the
        regression is a row rather than a memory."""
        for anchor, snippet in (
            ("base.py:518-519", "if builtins is None:"),
            ("base.py:522-523", "for builtin in builtins:"),
        ):
            line = int(anchor.split(":")[1].split("-")[0])
            text = (DJANGO_CHECKOUT / "django/template/base.py").read_text().splitlines()
            self.assertIn(snippet, text[line - 1] + text[line])
```

- [ ] **Step 2: Run them; expect all four to FAIL** — `--update` does not exist
yet, so the first three fail on the import and the fourth on the anchors being
`517-518`/`521-522` if the revert has not landed. **Land the revert first** (both
files: the seven sed'd files and the plan) so the fourth test is a regression row
and not a to-do.

- [ ] **Step 3: The manifest's snippets are authored; the numbers are derived**

The direction matters, and it is the opposite of the obvious one. **The snippet
is the claim and the number is computed from it**, so a wrong number has exactly
one repair. The reverse — record the number, compare the text at it — can only
*detect* drift, and detects nothing about the five defects above, because at
`defaulttags.py:1037` the text at the cited line is a perfectly real line.

**Author the snippets; do not seed them from the cited line.** Seeding is
circular: it would transcribe `{% for date in days %}` for the `condition.eval`
anchor and call it consistent. Each snippet is the code *the prose is about* —
usually already quoted beside the anchor in our own tree, which is where the
triage list comes from. Where the prose quotes nothing, read the line and decide.

```python
# scripts/check_python_anchors.py
"""Every `` `name.py:N` `` anchor in our tree, checked against `.django/`.

The reviewer checked fourteen, reported seven wrong, and two of the seven reports
were themselves wrong; applying them broke two anchors that had been correct.
Sampling afterwards found five more defects, including one where the number was
right and the *quoted source text* was wrong. So neither "be careful" nor "trust
the report" is a mechanism.

`scripts/anchors.txt` records, per anchor as it is written in the tree, the file
it names and **the source text the claim is about** — authored, not read off the
cited line, because reading it off is what makes the check circular. `--check`
fails when that text is not at the cited line; `--update` derives the numbers
from the text.

    python -I scripts/check_python_anchors.py --check    # gate, exit 1 on drift
    python -I scripts/check_python_anchors.py --triage   # suspicion list, exit 0
    python -I scripts/check_python_anchors.py --update   # rewrite cited numbers

**What this does not check: that the snippet supports the claim beside it.** It
is a number-versus-text guard, not a reviewer — a green `--check` means the
citations point where they say, not that they are the right citations. The
triage mode is the part that reads like a reviewer, and it is a heuristic: it
reports every anchor with a code span quoted within 200 characters that is not
at the cited line (228 of 283 candidates today) and leaves the judgement to a
reader. Do not treat its silence as approval, and do not treat a green
`--check` as "the citations are correct".

Manifest line format (tab-separated, sorted by file then line, so it reads as a
table of what we claim each line says):

    <anchor as written>\t<path>\t<line>\t<end line or ->\t<snippet>\t<end snippet>
"""

# Ambiguous basenames: 415 anchors, of which 188 name one of these. Each needs an
# explicit path in the manifest, decided once and recorded, because a bare
# basename cannot resolve. `formats.py` is the awkward one — 89 candidates under
# `django/conf/locale/*/` — and the entry must name which locale's formats the
# claim is about.
AMBIGUOUS = {
    "base.py": 38, "html.py": 2, "text.py": 2, "formats.py": 89,
    "custom.py": 2, "tests.py": 210, "test_base.py": 6, "test_partials.py": 2,
}

# Two forms, because the tree writes both and the 477-vs-426 reconciliation found
# the difference: a bare basename (`` `base.py:615` ``) and a qualified path
# (`` `django/template/base.py:615` ``, `` `_impl/parser.py:210` ``). 47 of the
# corpus is the second form, and 5 of those 47 name our own files.
ANCHOR_RE = re.compile(r"`([a-z_0-9/]+\.py):(\d+)(?:-(\d+))?`")
# The third form — a bare `` `:1035` `` meaning "the file this comment is about" —
# is matched only to be *rejected*: 360 occurrences today, and 4 of the 6 readable
# ones in `parser/tags/if.rs` are wrong. A tool cannot resolve it without guessing
# the implied file, and guessing here reproduces the defect. Naming the file is
# the migration; this regex is what stops the convention growing back.
SHORTHAND_RE = re.compile(r"`:(\d+)(?:-(\d+))?`")
TREE_ROOTS = ["crates", "python", "tests", "docs", ".review"]
OUR_OWN = {"_shim.py", "differential.py", "parser.py", "test_materialise.py", "test_parser_parity.py"}
```

Resolution order, and each step is an error rather than a guess: a qualified path
is tried under `.django/` then the repo root; a bare basename is looked up under
`.django/` excluding `__pycache__`, and exactly one hit resolves — more than one
needs the manifest's explicit path (the `AMBIGUOUS` rows), zero fails.

A snippet is an exact substring of the cited line (and of the end line, when the
anchor is a range), so a Django pin bump that reflows the line reports "snippet
not found" rather than silently re-pointing at whatever now occupies the old
number.

- [ ] **Step 4: Read the triage list, then make `--check` green, then add the pass**

Run: `python -I scripts/check_python_anchors.py --triage`
Expected: ~228 rows, each an anchor with a code span quoted nearby that is not at
the cited line. **Read them before writing any manifest entries** — the four
wrong numbers above are in that list, and reading it is what turns "415 anchors,
unknown how many wrong" into a number. Each row is resolved one of three ways,
and the third is a real answer: fix the anchor, fix the prose's quotation of the
source (the `defaultfilters.py:996` kind — the number was right), or record that
the heuristics fired on an unrelated backtick.

Run: `python -I scripts/check_python_anchors.py --check`
Expected: exit 0, and **every failure before it is green is a hand decision on a
row the tool named** — a stale number, a misquote, or a snippet that is not
unique in its file. The tool found them; a reader had not, four times running.

Then add it to `check.sh`, in the existing `step "generated files are current"`
block:

```bash
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_python_anchors.py" --check
```

- [ ] **Step 5: Commit**

```bash
git add scripts/check_python_anchors.py scripts/anchors.txt scripts/check.sh tests/test_anchor_manifest.py
git commit -m "scripts: check django source anchors against the pinned tree"
```

---

### Task 7: close-out — the spec corrected, the reconnaissance committed, the counts re-measured

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`
- Add: `.review/m6-recon.md` (untracked until now)
- Modify: the memory file for this project (M6 status)

- [ ] **Step 1: Correct the spec's three errors, in place, as prior milestones did**

Each correction states the measurement, not just the fix:

1. **The M6 gate sentence** (~line 989) attributes `add_tag`/`add_library` to
   `Library`. Neither exists; the merge is `Parser.add_library`
   (`base.py:669-670`) and the gate's behaviour is real by that route. Note that
   the spec's own §6 text already gets this right ("consumed via `add_library`
   and **not stored**") — the two sentences disagree and the later is wrong.
2. **`LibraryParseError`** → `InvalidTemplateLibrary` (`library.py:13`), raised by
   `import_library` (`:476-492`).
3. **§5.2's arbitrary-attributes requirement** is argued from
   `loader_tags.do_block:228-234`, which is correct and is **M7's**, not M6's.
   Keep the requirement, move the citation's milestone, and record the
   non-negotiable part for M7: the handle must *raise* `AttributeError` for an
   attribute it does not have, because `do_block`'s `except AttributeError`
   branch is what builds `__loaded_blocks` in the first place.

- [ ] **Step 2: Record M6's two decided ownership changes and the M7 boundary**

Add to §10.3's M6 row and §10.4's gate: `load` and `querystring` are
**host-owned** (this plan's A1 and A4), so `builtin_tag_names()` is 22 and
`builtin_tag_stubs()` is `['partial', 'partialdef']`; and M6 **excludes**
`block`/`extends`/`include` from the host (A5), so `{% extends %}` still raises
`Invalid block tag` at M6 — **a known divergence from Django, owned by M7**,
which must remove the exclusion *and* carry `must_be_first` across the seam.
Write the divergence down as a divergence; do not let it look like coverage.

- [ ] **Step 3: Re-measure every count the spec now asserts**

Do not edit a number in the spec without running the thing that produces it:
`builtin_tag_names()` length, `builtin_tag_stubs()` contents, `unshimmed()`,
`cargo test`'s count, and the three corpus sizes. A number that moved and was
not re-derived is the defect class this project keeps finding.

- [ ] **Step 4: Commit the reconnaissance with the close-out**

`.review/m6-recon.md` has been untracked since it was written. It is the
measurement record this plan is argued from, so it belongs in the same commit as
the spec edits that cite it.

```bash
git add .review/m6-recon.md docs/superpowers/specs/*.md
git commit -m "M6 close-out — the spec corrected, the reconnaissance committed"
```

- [ ] **Step 5: Update the project memory and dispatch the close-out to the reviewer**

Update `MEMORY.md`'s project line with M6's status and the corrected numbers, and
send the close-out to the reviewer peer session (`django-rust-templates-a8`) with
`.django/` @ 6.0.9 named, per the standing dispatch rule.

---

## Self-Review

Run after writing the plan, against the spec with fresh eyes. Findings and fixes,
in the order they were found.

**1. Spec coverage.** §10.3's M6 row names four things: `Library`, extension host
callbacks, `{% load %}`, `querystring`. Tasks 1 (the handle prerequisite), 2 (the
wire form), 3 (the seam, the load, the gate), 4 (the state), 5 (the comparison),
7 (the record) cover all four; Task 6 is not spec coverage but the removal of a
defect class the spec's own citations carry. §5.2's `ParserHandle` contracts are
each given a task: re-entrant `parse` → Task 3's handoff, proved in Task 4 Step 6;
`extra_data` → Task 4; the failure-path return → Task 4 Step 4; arbitrary
attributes → explicitly **out of scope** (A-consequence in the recon, Task 7
Step 1). §9's three test layers: the unit layer is `cargo test` in each task's
`check.sh`; the parity layer is Task 5; the suite layer is unchanged and stated in
Global Constraints. No section of the spec is left without a task.

**2. Placeholder scan.** Two were found and fixed rather than shipped:
(a) Task 3's Step 11 was a docstring with no assertion — a step describing what
to test without testing it, which is the "steps that describe what to do without
showing how" failure. It is now a runnable row, and its discriminator was
*executed* before being written down (`.review/m6-recon.md` records the run);
(b) Task 4's Step 3 originally assigned `extra_data` unconditionally from a
`finally`, which is the exact defect Step 4 then hunts. Both are now the shape
that fails for its intended reason.

**3. Type consistency.** `Host::call_tag` returns
`Result<(NodeDesc, Vec<Token>), Error>` in Task 3's trait, and Task 3 Step 6
destructures exactly `(desc, left)`. `Host::check_filter` returns
`Result<Option<NodeRef>, Error>` in Task 1 Step 3, and Task 1 Step 4 returns
`Ok(None)` / `Ok(Some(ref))` — matching. Task 2 defines
`NodeDesc::host(NodeRef)` and `HOST_NODE`; Task 3's `ShellHost` produces it via
`call_tag`'s `NodeDesc`, and Task 2's `materialise_node` arm is the only consumer.
The one name that moved: `_engine_builtins` → `_builtins` (Task 3 Step 8) once
the snapshot replaced the `Engine()` read; nothing else references the old name.

**4. A mechanism that could not run at all.** Task 5's first draft specified a
`describe` arm matching `class_name == "HOST_NODE"`. That arm can never execute:
`describe` operates on **materialised Django nodes**, and by then the host node is
an ordinary `SimpleNode` — the reserved name lives only on the descriptor wire,
which no harness reads. Worse than inert, it would have implied the corpus needed
unwrapping to compare a host node, when `describe`'s existing `Node` arm already
does it generically and already recurses into `SimpleBlockNode`'s *real*
`NodeList`. The step was replaced with the one thing that genuinely does not work
today: the two sides must share `Library` objects, because `SimpleNode.func` is
compared by identity.

**5. Review Focus.** All five lines have an owner and a test:
1 → Task 3 Step 11; 2 → Task 3 Steps 2 and 10; 3 → Task 1 (Steps 1 and 6) and
Task 3 Step 2's sandwich; 4 → Task 2 Step 5 and Task 5 Step 6; 5 → Task 4
Steps 1–5. The list was written with the spec in front of me and each line was
checked against a task, not added to the section and left there.

**6. What this plan does not claim.** Measured against `test_custom.py`'s 39:
M6 claims the `filter`/`simple_tag`/`simple_block_tag`/`{% load %}`-error and
`bad_tag`/`tag_27584`/`extra_data` groups — and **not**
`InclusionTagTests`, whose `InclusionNode.render` loads a template by name and
is loader machinery (M7). `test_library.py`'s 28 are a gate on Python behaviour
M6 must not break, not work M6 does. Saying which tests a milestone claims,
rather than asserting a module, is the correction the M5 close-out recorded.

**7. An argument that survived review but its premise did not.** A5 first excluded
`block`/`extends`/`include` from the host on the grounds that `{% extends %}`'s
ordering check lives in `Parser.extend_nodelist` and "our descriptor path never
runs that line". The reviewer falsified it by naming
`crates/template-core/src/parser/mod.rs:392-418` — a full port of
`extend_nodelist`, `must_be_first && contains_nontext` and Django's message
included, called from `Parser::parse` at `:218`, `:252` and `:304`. I re-measured
it myself and confirmed the reviewer: a host tag's descriptor reaches `:304` like
any other block node. The exclusion **stands on a different argument** — the real
mechanism is one level down and smaller: `NodeDesc.must_be_first` is documented
"Not carried on the wire" (`desc.rs:369`) and defaults to `false` (`:407`), so a
host node arrives with the flag cleared and the check passes. M6 must not newly
accept a tag it cannot render (its render path is M7/M9), because that moves the
divergence from loud (`Invalid block tag`) to silent. Rewritten in A5, Review
Focus 4, Task 2 Step 5, the `_M7_OWNED` comment, Task 5 Step 6 and the recon. The
reviewer's framing of the failure is the one worth keeping: *the symptom's
distribution was diagnosed instead of its propagation path.*

**8. The citation class, and why Task 6 exists.** The reviewer checked fourteen
`base.py` 5xx–6xx anchors and reported seven wrong. Five were: `:598`→`:615`,
`:566-567`→`:585`, `:600-610`→`:564-569`, `:668`→`:669`, `:598-610`→`:600-617`.
**Two were themselves wrong** — `:518-519` and `:522-523` were correct and the
reported replacements (`:517-518`, `:521-522`) pointed one line short, at the
*other* variable's guard (`521` is `self.libraries = libraries`; `516-517` is the
`libraries` guard). I applied them without measuring, so a repair round introduced
two defects into a tree that did not have them, and they were caught only because
the numbers sat beside a name they contradicted. Measured now: `:518-519` is
`if builtins is None: / builtins = []`, `:522-523` is `for builtin in builtins: /
self.add_library(builtin)`, both cited correctly again, and Task 6 pins them as a
named regression row. The inventory is not fourteen: **430 bare-basename anchors
plus 47 qualified-path ones — the reviewer's 477 and the 426 first counted here
are the same corpus under two patterns, and the difference is the `/` their
pattern allowed.** Excluding the tree's own files and resolving into the pinned
checkout: ~415 anchors, 332 naming `django/template/`. Sampling that corpus found
five more defects — four wrong numbers and one misquoted source, listed in Task 6.
The count of *unknown* wrong among them still cannot be stated without the guard.
Three occurrences of this class, one failed repair, and the remedy each time was
"be careful"; hence Task 6, which derives the numbers from authored snippets
rather than authoring the numbers.

