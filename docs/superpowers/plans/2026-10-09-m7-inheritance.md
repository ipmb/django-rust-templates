# M7 — Inheritance: `{% block %}`, `{% extends %}`, `{% include %}`: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the last three built-in tags §6 assigns to Python onto Rust's side of the seam — compile functions for `{% block %}`, `{% extends %}` and `{% include %}` in `template-core`, the origin plumbing `{% extends %}` and `{% include %}` read, and the `must_be_first` carry that lets a *host* tag's node be refused in a non-first position — so the inheritance machinery is Rust's to compile and Django's to render.

**Architecture:** M6 left the host seam in place and three built-ins deliberately outside it (`host.py:48`, `_M7_OWNED`). M7 closes that gap, and the shape it takes is **Design B**: the three names join `REGISTRY`, so their compile functions are Rust's, while their *node classes* stay Django's and render in Python — exactly the division M3 established (§5.5) and every prior tag already uses. What makes Design B non-trivial is a **two-clause** change to the host predicate: adding the names to Rust's table is not enough and is actively harmful alone, because `loader_tags.register.tags` is a *separate* dict from `defaulttags.register.tags`, so the identity clause in `ShellHost.has_tag` would see `func is not self._django_tags.get("block")` (which is `None`) and route all three back to the host. The names must land in `REGISTRY` **and** `loader_tags` must merge into `django_own_tags()`, in one commit, or the milestone accepts `{% extends %}` in positions Django refuses it.

**Tech Stack:** Rust (edition 2021, no Python in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.2 (the seam), §5.5 (the shadow tree, and `test_include_cache`), §6 (ownership), §9 (the three test layers), §10.3 (the M7 row), §10.4 (the M7 obligation). This plan argues from the spec; where they disagree the spec wins and this plan is wrong. **One spec citation is a vintage and one spec claim is vacuous, and both are corrected here** (§D names them). Read `.review/m7-recon.md` before this file; it carries the measurements this plan's architecture rests on, and §D records where this plan corrects it.

## Global Constraints

Copied verbatim from the spec and the M4/M5/M6 plans; every task's requirements implicitly include them.

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
  writes. M7's gate is the parity corpus in `tests/` plus Task 6's
  `test_include_cache` row, which is §10.3's named gate and is discharged
  through `render_string` rather than through the suite (see A6).

## Review Focus

The five inputs and conditions the spec implies but no single task's tests
exercise, most likely first. Each is pinned by a test in the task that owns the
code.

1. **The two-clause host predicate, and the half-fix that looks correct.** Adding
   `block`/`extends`/`include` to `REGISTRY` *without* merging `loader_tags` into
   `django_own_tags()` silently re-routes all three to the host — measured, and
   the row is in A2's matrix. The trap is not a wrong answer to a hard question;
   it is that `has_tag` keeps returning a *plausible* bool (`False` before the
   change, `True` after — both consistent with "Rust handles it") while the code
   that runs is Django's. **Owned by Task 4; pinned by a test that asserts
   `ShellHost.has_tag("block") is False` against a parser whose `tags` hold
   `loader_tags.do_block`,** which is the only way the identity clause can be
   seen to have been reached.
2. **`{% extends %}`'s must-be-first check depends on an origin, and three
   shell call sites pass none.** `do_extends` reads
   `parser.origin.template_name` (`loader_tags.py:305`) and `extend_nodelist`
   renders the origin into its message; today `render.parser_for` builds its
   `Parser` with no `origin` (so `parser.origin is None`) and
   `compile_template` passes `py.None()` as the materialisation origin. A
   `{% extends %}` row therefore raises `AttributeError: 'NoneType' object has
   no attribute 'template_name'` **before** any of M7's code is wrong.
   **Owned by Task 2; pinned by an error-corpus row whose expected message is
   the full `must be the first tag in <origin>` string**, which can only match
   if the origin travelled.
3. **A nodelist the host returned has no descriptor children to walk.** Django's
   `do_extends` asks the just-parsed nodelist `get_nodes_by_type(ExtendsNode)`
   (`loader_tags.py:308`) to reject a second `{% extends %}`. Under the host
   seam a *host* tag's body is a Python `NodeList` stored behind a `NodeRef`, and
   Rust cannot walk it. M7's three take the Rust path, so the walk is over
   `NodeDesc`s and is exact — but a host block tag wrapping an `{% extends %}`
   still hides one, and the failure is silent acceptance rather than a wrong
   tree. **Owned by Task 3; the divergence is recorded, not fixed** (it is the
   same class as M6's Review Focus item 1), and the test asserts the *reachable*
   half: a second `{% extends %}` at top level is rejected with Django's exact
   message.
4. **`{% include %}`'s cache key is the node object, and a second parse makes
   two.** `IncludeNode.render` does
   `cache = context.render_context.dicts[0].setdefault(self, {})`
   (`loader_tags.py:197`), so two nodes that are equal-but-distinct render
   independently — which is correct — while *the same* node reached twice must
   hit the cache. §5.5 and §10.3 name `test_include_cache` as M7's proof that the
   shadow tree is materialised once and node identity is stable. **Owned by
   Task 6; the test is a loop of the same `{% include %}` in one template**
   (which is the one node rendered twice), paired with a stock-side assertion in
   the same row.
5. **`{% block %}`'s duplicate-name check raises *before* recording, and its
   state is per-parse.** `do_block` raises when the name is already in
   `parser.__loaded_blocks` and appends only on the success path
   (`loader_tags.py:227-234`); Rust's `try/except AttributeError` collapse is an
   empty `Vec`, and the append must sit after the check or a *third* `{% block %}`
   of the same name would report the second as the first. The state is also the
   parser's, not the node's, so it must survive across a `{% block %}` nested in
   another tag's body. **Owned by Task 1; pinned by the error-corpus rows for
   `{% block a %}…{% block a %}…` and for the `{% endblock %}` forms**, whose
   exact strings were measured against the pinned tree.

---

## Architecture

Six decisions, each argued from a measurement in `.review/m7-recon.md` or
re-measured for this plan. Read that file; this section states the conclusions
and the consequences a task will trip over.

### A1. Design B: Rust owns the three compile functions; Django owns the nodes

The spec's §6 assigns *compiling* the inheritance structure to Rust (§10.4), and
there are two ways to read that. **Design A** — leave the three names as host
tags and carry an origin through the seam — keeps them out of `REGISTRY` and puts
their compile functions in Python. **Design B** — chosen — moves
`do_block`/`do_extends`/`do_include` into `tags/block.rs`, `tags/extends.rs` and
`tags/include.rs`, adds the three names to `REGISTRY`, and materialises Django's
`BlockNode`/`ExtendsNode`/`IncludeNode` from Rust descriptors exactly as M2–M6
materialise every other node.

Design B is chosen because Design A does not remove the work, it relocates it:
`do_extends` needs `parser.origin.template_name` and a walk over the parsed
nodelist, `do_block` needs `parser.__loaded_blocks`, and neither of those is a
value `NodeDesc` can carry — which is precisely the rule `registry.rs:19-34`
already records for why the three were subtracted. Carrying all of that across
the seam means growing the wire, in exchange for keeping the *compile* functions
in a file that is not Rust's. Design B pays one predicate change instead.

**Consequences, and each is a task's business:**

- `registry.rs:11-17`'s "**Five built-in names are deliberately absent**" becomes
  two, then one, then none as the tasks land; the count paragraph at `:94-102`
  and the test `the_five_python_owned_builtins_are_not_rust_compile_functions`
  track it. **Per task, not at the end**: 22 → 23 (Task 1, `block`) → 24 (Task 3,
  `extends`) → 25 (Task 4, `include`), with the literal shrinking in the same
  commit that grows the table. That is the registry doc's own recorded rule
  ("the literal shrinks with them here rather than at the end of the milestone,
  which is what keeps every commit in between green").
- `test_tag_inventory.py:252`'s `PYTHON_OWNED = {"block", "extends", "include",
  "load", "querystring"}` shrinks on the same three boundaries, and the assertions
  at `:253` and `:261` follow it. The test *names* mention "the python owned
  five" and must be renamed as they shrink.
- `_impl/parser.py:100-115`'s comment names block/extends/include as classes the
  materialiser "must *not* materialise from Rust". M7 makes that false; the
  comment is corrected in the task that adds each class to `NODE_CLASSES`.
- `host.py`'s `_M7_OWNED` (`:48`, with the A5 comment at `:37-47`) is deleted in
  Task 4, and `django_own_tags()`'s docstring at `:75-78` stops saying
  `loader_tags` "is not here".

### A2. The host predicate is a **two-clause** change, and one clause alone is a trap

`ShellHost.has_tag` (`host.py:113-131`) decides who compiles a name by identity:
`func is not self._django_tags.get(command)`, where `_django_tags` is
`django_own_tags()` — today `dict(defaulttags.register.tags)`. The three M7 names
are **not in that dict**: `loader_tags.register.tags` is a separate registry, and
the two are merged by `get_installed_libraries`/`Engine`, not by either module.
Measured on the pinned tree (`/tmp/m7_measureA.py`):

```text
22, defaulttags only  (today)               block=False extends=False include=False
25, defaulttags only  <-- the trap          block=True  extends=True  include=True
25, defaulttags+loader_tags  <-- Design B   block=False extends=False include=False
25, +loader_tags, library overrides block   block=True  extends=False include=False
```

`True` means *the host compiles it*. Row 3 is the wanted state; row 2 is what
adding the names to `REGISTRY` **alone** produces, and it is worse than today —
today the host declines (`has_tag` False) and Rust answers `Invalid block tag`,
which is honest; under row 2 the host compiles, `{% extends %}` is accepted
anywhere, and `extend_nodelist`'s check never runs. Row 4 is the invariant that
must survive: a `{% load %}`ed library registering `block` still wins, because
`add_library` is `self.tags.update(lib.tags)` (`base.py:669-670`) and the loaded
callable is not `loader_tags.do_block`.

So Task 4 makes **both** edits in one commit:

1. delete `_M7_OWNED` and its `has_tag` clause, and
2. widen `django_own_tags()` to
   `{**defaulttags.register.tags, **loader_tags.register.tags}`.

The `block`/`extends`/`include` entries in `REGISTRY` arrive in Tasks 1, 3 and 4
respectively; the predicate change is only *safe* once all three are there, so it
lands with the last of them and the three commits in between stay green (a name
in `REGISTRY` and in neither `django_own_tags` nor `_M7_OWNED` is unreachable
before Task 4 — `_M7_OWNED` still refuses `block`/`extends`/`include` from the
host, so `has_tag` returns `False` and Rust compiles them, which is correct).

### A3. `origin_template_repr` becomes `origin_template_name`, and the repr is derived

`Parser::origin_template_repr: Option<String>` (`parser/mod.rs:59-66`) holds
Python's `repr()` of `Origin.template_name`, and only `extend_nodelist`'s message
reads it. `{% extends %}`'s compile function needs the **raw**
`parser.origin.template_name`, because `construct_relative_path` (`LT:246-288`)
compares it against a path (`posixpath.dirname(current_template_name.lstrip("/"))`)
and interpolates it into two more messages with plain `%s` — a repr would be
wrong in all three.

So the field is renamed to `origin_template_name: Option<String>` and holds the
raw name; `extend_nodelist` derives the repr from it through the seam that
already exists for exactly this purpose — `Parser::py_repr` (`mod.rs:167-169`) →
`Host::repr` → Python's own `repr` (M5's seam; `FilterCheckHost` overrides it at
`lib.rs:1336`). The `py_repr_str`/`NoHost` fallback is exact on ASCII only, which
is the same caveat `Host::repr`'s doc already carries.

**Plumbing, and all three call sites are load-bearing:**

- `render.parser_for` (`render.py:127-131`) builds its `Parser` with no `origin`,
  so `parser.origin` is `None` and `do_extends`'s read would raise
  `AttributeError`. It gains an `origin=None` parameter and passes it through.
- `_native.parse`'s `origin_template_repr` keyword (`lib.rs:1532-1537`) is renamed
  to `origin_template_name`; `parse_source` (`:1442-1464`) and
  `Parser::with_origin_template_repr` (`mod.rs:108-112`) follow.
- `compile_template` (`lib.rs:2169`) passes `py.None()` as the materialisation
  origin, and `template_for` (`_impl/parser.py:434`) sets `template.origin =
  None`. `ExtendsNode.render` reads `self.origin` (its `nodelist`'s
  `render_context` seed is `[self.origin]`, `LT:104`) and `IncludeNode.render`
  reads `self.origin.template_name` (`LT:197`). Task 2 threads the same origin
  through `compile_template`'s new argument, and Task 4's row is what proves it.

### A4. `must_be_first` crosses the seam **inbound**, and `call_tag` must raise

The recon's §2 framed `must_be_first` on the outbound wire — the 5-tuple pinned
by `tests/test_ref_table.py:173-181`. That is the wrong boundary. The flag that
matters crosses **inbound**: a library tag returning a `Node` subclass with
`must_be_first = True`. Measured (`/tmp/m7_measureB.py`), Django refuses it in a
non-first position with the identical message —

```text
{% load mylib %}{% first_tag %}          -> OK  'F'
text{% load mylib %}{% first_tag %}      -> TemplateSyntaxError: {% first_tag %} must be the first tag in the template.
{% load mylib %}{% plain_tag %}text{% plain_tag %} -> TemplateSyntaxError: {% plain_tag %} must be the first tag in the template.
```

— and `{% load %}` itself sets `contains_nontext`, so the second row fails on the
`{% load %}`'s account rather than the `text`'s. `Rust`'s `Parser::extend_nodelist`
(`mod.rs:429-457`) runs the check for a host descriptor like any other block node,
so the *only* missing piece is the flag.

`lib.rs::FilterCheckHost::call_tag` must therefore read `node.getattr("must_be_first")`
**before** `node.unbind()` (the unbind is at `lib.rs:1289-1293`), map a missing
attribute to `Error::internal`, and set `desc.must_be_first`. **Raise, not default
`False`**: Django reads `must_be_first` unconditionally at `base.py:602`, so every
node has it via the `Node` class attribute; an attribute that is genuinely absent
means the host returned something that is not a node, and defaulting would turn
that into a silent "no check needed".

`NodeDesc.must_be_first` is already `pub` (`desc.rs:407`), defaulted `false`
(`:445`) and set by `.first()` (`:477-478`). The 5-tuple outbound wire is
**untouched** by M7 — the recon's §2 correction.

### A5. `parser.__loaded_blocks` is Rust's, and one divergence is recorded

`do_block`'s duplicate-name check reads and writes `parser.__loaded_blocks`
(`LT:227-234`), a Python attribute with exactly **one reader and one writer** —
`do_block` itself. Django reaches it through `try/except AttributeError` so that
the first `{% block %}` initialises it; Rust models that as
`Parser::loaded_blocks: Vec<String>` initialised empty, with the raise *before*
the append, which is Django's order.

Two things a task will trip over, both recorded rather than fixed:

- The check straddles a `{% load %}`. Under Design B the state is Rust's, so a
  `{% block %}` body handled by a *host* tag's nested `parser.parse` (a
  `simple_block_tag`, `library.py:211-212`) accumulates in Django's
  `parser.__loaded_blocks` while every Rust-compiled `{% block %}` accumulates in
  `Parser::loaded_blocks` — two counters that cannot see each other. This is the
  same class as M6's Review Focus item 1 and is *unreachable in the pinned
  suite*, which registers no library tag named `block`.
- `do_block` uses `token.contents.split()`, **not** `split_contents()`, and
  Django's own comment says why (`LT:218-219`: the tag "doesn't accept variable as
  arguments"). Rust must call `crate::pyunicode::split`
  (`crates/template-core/src/pyunicode.rs:64`), never `str::split_whitespace`
  (differs on U+001C–U+001F) and never `tags::split_contents` (`tags/mod.rs:61`).

### A6. `test_include_cache` is discharged through `render_string`, paired with stock

§10.3 names `test_include_cache` as M7's gate and the spec says it proves "the
shadow tree is materialised once and node identity is stable (§5.5)". The
`.django/` row itself (`.django/tests/template_tests/syntax_tests/test_include.py:371`)
cannot be the gate, for the reason the Global Constraints state: the shim does
not yet interpose `defaulttags`/`loader_tags`, so the suite runs on stock
Django's parser and the row would pass whether or not M7 shipped.

The obligation is therefore discharged in `tests/`, with the instrument pointed
at our engine: **the same `{% include %}` appearing twice in one template** is
one `IncludeNode` rendered twice, so `IncludeNode.render`'s
`context.render_context.dicts[0].setdefault(self, {})` (`LT:197`) must hit on the
second render and the loop must not re-load. `render_string` is sufficient (the
shell's source-to-output path), and the test pairs an ours-side assertion with a
stock-side assertion in the same row — the pattern this project has now needed
three times, of which `scripts/differential.py:57-69` is the record.

What the gate can and cannot see, stated so a reader does not over-read it: it
pins **node identity across renders of one template**, which is what the loader
cache is keyed on. It does **not** pin that the template is materialised once
(rust-materialisation happens once by construction), and it does not exercise the
`{% extends %}` chain's `render_context.push_state` (`LT:134-160`).

---

## D. Corrections this plan makes

Four, each with the artifact it corrects and the measurement behind it. The
first two correct `.review/m7-recon.md` (the recon precedes and is corrected by
the plan, as M6's did); the other two correct derived artifacts.

**D1 — the recon's §2 frames `must_be_first` on the outbound wire. It crosses
inbound.** The 5-tuple `node_desc_to_py` emits (`lib.rs:871-915`, pinned by
`tests/test_ref_table.py:173-181`) is a *descriptor-to-shell* wire; the flag
that is missing is read off the *Python node the host returned*, before
`node.unbind()`. See A4; the measurement is `/tmp/m7_measureB.py`. This plan does
not touch the 5-tuple.

**D2 — the recon's §6 items 2 and 6 contradict each other.** Item 2 says "carry
`must_be_first` with the node"; item 6 says "write `do_block`/`do_include`/
`construct_relative_path`". Carrying the flag is Design A's prerequisite;
writing the compile functions is Design B's. This plan resolves it in favour of
**Design B** (A1) and keeps item 2's flag carry as a *separate* task (Task 5),
because it is a real M6 defect independent of which design wins: a library tag
returning a `must_be_first` node is accepted today, in a position Django
refuses.

**D3 — the spec's §10.4 cites `parser/mod.rs:336` for `extend_nodelist`; the
port is at `:429`.** A vintage, not a claim; recorded so the next reader does not
go looking.

**D4 — the spec's §10.3 M7 row names `test_include_cache` as the gate, and as
written that gate is vacuous.** The `.django/` row runs on stock Django's parser
(the shim does not yet interpose `loader_tags`), so it passes regardless of M7.
The obligation is discharged in `tests/` against our engine (A6); the spec's
claim about *what* the row proves is kept, and the instrument that can see it is
named.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `crates/template-core/src/parser/tags/block.rs` | **Create.** `{% block %}`'s compile function, ported from `do_block` (`LT:215-243`) | 1 |
| `crates/template-core/src/parser/tags/mod.rs` | **Modify.** `pub mod block;` (+ `extends`, `include`) | 1, 3, 4 |
| `crates/template-core/src/parser/registry.rs` | **Modify.** `("block", …)` entry; the "absent names" doc; the count paragraph | 1, 3, 4 |
| `crates/template-core/src/parser/mod.rs` | **Modify.** `Parser::loaded_blocks` + accessors; `origin_template_repr` → `origin_template_name`; `extend_nodelist` derives the repr | 1, 2 |
| `crates/template-core/src/parser/relative.rs` | **Create.** `construct_relative_path` and its three messages, ported from `LT:246-288` | 2 |
| `crates/template-core/src/parser/tags/extends.rs` | **Create.** `{% extends %}`'s compile function, ported from `do_extends` (`LT:291-312`) | 3 |
| `crates/template-core/src/parser/desc.rs` | **Modify.** `NodeListDesc::get_nodes_by_type` — the `child_nodelists` walk | 3 |
| `crates/template-core/src/parser/tags/include.rs` | **Create.** `{% include %}`'s compile function, ported from `do_include` (`LT:315-370`) | 4 |
| `crates/template-py/src/lib.rs` | **Modify.** `origin_template_repr` → `origin_template_name` (`parse`, `parse_source`, `compile_template`); the inbound `must_be_first` read in `call_tag` | 2, 5 |
| `python/django_rust_templates/host.py` | **Modify.** Delete `_M7_OWNED`; `django_own_tags()` merges `loader_tags` | 4 |
| `python/django_rust_templates/render.py` | **Modify.** `parser_for`/`parse_template`/`render_string` take an `origin` | 2 |
| `python/django_rust_templates/_impl/parser.py` | **Modify.** `NODE_CLASSES` gains `BlockNode`/`ExtendsNode`/`IncludeNode`; the "Python-owned" comment; `template_for`'s origin | 1, 3, 4 |
| `tests/test_tag_inventory.py` | **Modify.** `PYTHON_OWNED`, the 22→25 literal, the two test names | 1, 3, 4 |
| `tests/parser_corpus.py` | **Modify.** Success-path rows for the three tags | 1, 3, 4 |
| `tests/parser_error_corpus.py` | **Modify.** The measured error strings | 1, 2, 3, 4 |
| `tests/test_inheritance_gate.py` | **Create.** A6's `test_include_cache` row, and A2's predicate rows | 4, 6 |
| `.review/m7-seams.md` | **Create.** The dated seams audit, in `m4-seams.md`/`m5-seams.md`'s shape | 7 |

---

## Task 1: `{% block %}` — the compile function, and Rust's `loaded_blocks`

**Files:**
- Create: `crates/template-core/src/parser/tags/block.rs`
- Modify: `crates/template-core/src/parser/tags/mod.rs` (module list, `:14-40`)
- Modify: `crates/template-core/src/parser/registry.rs` (`REGISTRY` `:125-148`; the "Five built-in names" doc `:11-17`; the count paragraph `:94-102`; the tests below)
- Modify: `crates/template-core/src/parser/mod.rs` (`Parser` fields `:44-71`, `Parser::new` `:84-96`, and a new accessor pair)
- Modify: `python/django_rust_templates/_impl/parser.py` (imports `:22-57`; `NODE_CLASSES` `:116-139`; the comment `:100-115`)
- Modify: `tests/test_tag_inventory.py` (`PYTHON_OWNED` `:252`, the literal at `:253-258`, the name at `:259`)
- Modify: `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `Parser::parse(&[&str])`, `Parser::next_token`, `Parser::invalid_block_tag`, `crate::pyunicode::split`, `AttrValue::Str`, `NodeDesc::new/.attr/.nodelist`.
- Produces: `tags::block::compile(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>`; `Parser::loaded_blocks(&self) -> &[String]`; `Parser::register_block_name(&mut self, String)`. Task 3 consumes none of these (its `get_nodes_by_type` walk reads descriptors, not the parser); Task 5's `must_be_first` carry is independent.

**Declared environment:** `maturin develop` is not yet needed (this task adds no `template-py` code), but run it anyway after the cargo build, or `tests/test_tag_inventory.py` will read the previous `.so`'s 22 names. `loader_tags` is **not** importable from `_impl/parser.py` under the shim today — the shim does not interpose it — so the import at Step 5 is a plain Python import and resolves.

- [ ] **Step 1: Write the Rust unit test, in `block.rs`**

```rust
#[cfg(test)]
mod tests {
    use crate::parser::tags::support::{compile, failure};

    #[test]
    fn a_block_compiles_to_a_block_node_carrying_its_body() {
        let node = compile("{% block a %}x{% endblock %}");
        assert_eq!(node.class_name, "BlockNode");
        assert_eq!(node.attr("name"), Some(&AttrValue::Str("a".to_string())));
        assert_eq!(node.nodelist("nodelist").map(|n| n.nodes.len()), Some(1));
    }

    #[test]
    fn a_duplicate_block_name_is_rejected_before_the_second_is_recorded() {
        let first = failure("{% block a %}{% endblock %}{% block a %}{% endblock %}");
        assert_eq!(
            first.kind,
            ErrorKind::Syntax,
            "'block' tag with name 'a' appears more than once"
        );
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source scripts/env.sh && cargo test -p template-core block::`
Expected: FAIL to compile — `tags/block.rs` does not exist, and `mod.rs` has no `pub mod block;`.

- [ ] **Step 3: Write `block.rs`**

```rust
//! `{% block %}` — `django.template.loader_tags.do_block` (`loader_tags.py:215`).
//!
//! `token.contents.split()`, **not** `split_contents()`: Django's own comment
//! (`loader_tags.py:218-219`) says the tag "doesn't accept variable as
//! arguments", and the two differ on U+001C–U+001F. `crate::pyunicode::split` is
//! Python's `str.split`, so the arity check sees the same `bits` Django sees.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = pyunicode::split(&token.contents);
    if bits.len() != 2 {
        return Err(Error::syntax(format!(
            "'{}' tag takes only one argument",
            bits[0]
        )));
    }
    let block_name = bits[1].to_string();

    // Django raises **before** appending (`loader_tags.py:228-232`), so a third
    // `{% block a %}` reports the second as the one that already had the name.
    if parser.loaded_blocks().iter().any(|name| *name == block_name) {
        return Err(Error::syntax(format!(
            "'{}' tag with name '{}' appears more than once",
            bits[0], block_name
        )));
    }
    parser.register_block_name(block_name.clone());

    let nodelist = parser.parse(&["endblock"])?;

    // `parse(("endblock",))` prepends the terminator it stopped on
    // (`base.py:564-569`), so this is always `Some`; `None` would mean the parse
    // returned without one, which is a defect in `parse` rather than in the
    // template.
    let Some(endblock) = parser.next_token() else {
        return Err(Error::internal(
            "do_block: parse returned without the endblock token",
        ));
    };
    let named = format!("endblock {block_name}");
    if endblock.contents != "endblock" && endblock.contents != named {
        return Err(parser.invalid_block_tag(&endblock, "endblock", &["endblock", &named]));
    }

    Ok(NodeDesc::new("BlockNode")
        .attr("name", AttrValue::Str(block_name))
        .nodelist("nodelist", nodelist))
}
```

- [ ] **Step 4: Register the module and the name**

In `tags/mod.rs`, add to the module list (alphabetical, after `autoescape`):

```rust
pub mod block;
```

In `registry.rs`, add the `REGISTRY` entry (ascending, after `autoescape`):

```rust
    ("block", tags::block::compile),
```

and update the "Five built-in names are deliberately absent" paragraph
(`:11-17`) to "**Four**", keeping `extends`, `include`, `load`, `querystring` in
it, and the count paragraph (`:94-102`) to `23 = 27 built-ins minus those four`.
Update the two registry tests in the same edit:
`the_registry_has_the_twenty_two_names_rust_owns` → `…_twenty_three_…` with
`assert_eq!(builtin_tag_names().len(), 23)`, and
`the_five_python_owned_builtins_are_not_rust_compile_functions` drops `"block"`
from its list. Then in `every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`,
add `"block"` to the `expected` list.

- [ ] **Step 5: Add `loaded_blocks` to `Parser`**

In `parser/mod.rs`, add the field beside `named_cycle_nodes`:

```rust
    /// `parser.__loaded_blocks` — the names of every `{% block %}` compiled in
    /// this template, in order.
    ///
    /// Django reaches it with `try/except AttributeError` so the first `do_block`
    /// initialises it; an empty `Vec` is that collapse. Its only reader and only
    /// writer are `do_block` (`loader_tags.py:227-234`), which is why it is
    /// Rust's under Design B — and why a `{% block %}` compiled by a *host*
    /// tag's nested `parser.parse` accumulates in Django's attribute instead
    /// (see A5; recorded, not fixed).
    loaded_blocks: Vec<String>,
```

initialise it in `Parser::new` (`loaded_blocks: Vec::new(),`), and add:

```rust
    /// The `{% block %}` names seen so far, in the order `do_block` recorded them.
    pub fn loaded_blocks(&self) -> &[String] {
        &self.loaded_blocks
    }

    /// `parser.__loaded_blocks.append(block_name)`.
    pub fn register_block_name(&mut self, name: String) {
        self.loaded_blocks.push(name);
    }
```

- [ ] **Step 6: Materialise `BlockNode`**

In `_impl/parser.py`, import from `django.template.loader_tags`:

```python
from django.template.loader_tags import BlockNode
```

add `"BlockNode": BlockNode,` to `NODE_CLASSES`, and correct the `:100-115`
comment: after M7 the three classes *are* materialised from Rust, and the
explicit registry's remaining job is to refuse an unknown name loudly rather
than resolving it by `getattr`.

- [ ] **Step 7: Move the inventory literal**

In `tests/test_tag_inventory.py`, `PYTHON_OWNED` becomes
`{"extends", "include", "load", "querystring"}`, the assertion at `:253` becomes
`self.assertEqual(len(names), 23)`, and both test names at `:253` and `:261`
lose "five" (they are about §6's rule now, and the number is in the body).

- [ ] **Step 8: Corpus rows**

In `tests/parser_corpus.py` add to `TEMPLATES`, under the `contains_nontext`
block's neighbourhood, the four measured success rows:

```python
    "{% block a %}x{% endblock %}",
    "{% block a %}x{% endblock a %}",
    "A{% block a %}x{% endblock %}B",
    "{% block a %}{% block b %}x{% endblock %}{% endblock %}",
```

In `tests/parser_error_corpus.py` add the measured failures (each string was
produced against `.django` @ 6.0.9 with `settings.configure(USE_I18N=False,
USE_TZ=False)` + `django.setup()`, because `invalid_block_tag` goes through the
translation layer):

```python
    ("{% block %}x{% endblock %}", "'block' tag takes only one argument"),
    (
        "{% block a %}{% endblock %}{% block a %}{% endblock %}",
        "'block' tag with name 'a' appears more than once",
    ),
    (
        "{% block a %}x{% endblock b %}",
        "Invalid block tag on line 1: 'endblock', expected 'endblock' or "
        "'endblock a'. Did you forget to register or load this tag?",
    ),
    (
        "{% block a %}x",
        "Unclosed tag on line 1: 'block'. Looking for one of: endblock.",
    ),
```

- [ ] **Step 9: Run the guards, then the gates**

Run: `source scripts/env.sh && cargo test -p template-core && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m pytest tests/ -x -q`
Expected: all pass; `tests/test_tag_inventory.py` reports 23 names; the corpus
counts rise by 4 success rows and 4 error rows.

Observe each new guard failing for its intended reason before keeping it: delete
the `bits.len() != 2` arm and confirm `{% block %}x{% endblock %}` reports the
*wrong* thing; move the `register_block_name` call above the duplicate check and
confirm the third-`{% block %}` case changes its message.

- [ ] **Step 10: Commit**

```bash
git add crates/template-core/src/parser/tags/block.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/registry.rs \
        crates/template-core/src/parser/mod.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/test_tag_inventory.py tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "M7 Task 1: {% block %} — the compile function, and Rust's loaded_blocks"
```

---

## Task 2: The origin, threaded — and `construct_relative_path`

**Files:**
- Create: `crates/template-core/src/pyposix.rs`
- Create: `crates/template-core/src/parser/relative.rs`
- Modify: `crates/template-core/src/lib.rs` (module list — add `pub mod pyposix;`)
- Modify: `crates/template-core/src/parser/mod.rs` (`origin_template_repr` field `:59-66` → `origin_template_name`; `with_origin_template_repr` `:108-112`; `extend_nodelist` `:445-452`)
- Modify: `crates/template-py/src/lib.rs` (`parse` signature `:1532-1549`, `parse_source` `:1442-1464`, `compile_template` `:2169`)
- Modify: `python/django_rust_templates/render.py` (`parser_for` `:103`, `parse_template` `:134`, `render_string` `:164`)
- Modify: `python/django_rust_templates/_impl/parser.py` (`template_for` `:434`)

**Interfaces:**
- Consumes: `Host::repr` (`host.rs:161`), `Parser::py_repr` (`parser/mod.rs:167`).
- Produces: `pyposix::normpath(&str) -> String`, `pyposix::dirname(&str) -> String`, `pyposix::join(&str, &str) -> String`; `parser::relative::construct_relative_path(Option<&str>, &str, bool) -> Result<String, Error>`; `Parser::origin_template_name(&self) -> Option<&str>`; `Parser::with_origin_template_name(String)`. Task 3 consumes `origin_template_name` and `construct_relative_path`; Task 4 consumes `construct_relative_path`.

**Declared environment:** `render.parser_for`'s new `origin` keyword defaults to `None`, so every existing caller (the parity corpus in `tests/`, `test_parser_errors.py`) keeps compiling unchanged. `_native.parse`'s keyword rename is **not** backward-compatible — the two `tests/` call sites that pass it are updated in the same commit, and a grep for `origin_template_repr` over `crates/ python/ tests/ scripts/` must come back empty before the commit.

- [ ] **Step 1: Write the failing `pyposix` test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normpath_matches_python_on_the_measured_table() {
        // Measured against `posixpath.normpath` on the pinned interpreter; the
        // table is the plan's evidence, not a guess.
        for (input, want) in [
            ("", "."),
            (".", "."),
            ("..", ".."),
            ("a/..", "."),
            ("a/../..", ".."),
            ("./a", "a"),
            ("a//b", "a/b"),
            ("//a//b", "//a/b"),
            ("/a/../b", "/b"),
            ("a/./b", "a/b"),
            ("../a", "../a"),
            ("./../a", "../a"),
        ] {
            assert_eq!(normpath(input), want, "normpath({input:?})");
        }
    }

    #[test]
    fn join_normpath_and_dirname_match_python_on_the_measured_table() {
        for (a, b, want) in [
            ("", "", "."),
            ("a", "b", "a/b"),
            ("a", "", "a"),
            ("", "b", "b"),
            ("a/b", "../c", "a/c"),
            ("a", "./b", "a/b"),
            ("sub", "../x", "x"),
            ("a/b/c", "../../x", "a/x"),
            ("", "./b", "b"),
            ("", "../b", "../b"),
            (".", "b", "b"),
            ("a", ".", "a"),
            ("a/..", "b", "b"),
            ("//a", "b", "//a/b"),
            ("/a", "b", "/a/b"),
        ] {
            assert_eq!(normpath(&join(a, b)), want, "normpath(join({a:?}, {b:?}))");
        }
        for (p, want) in [
            ("", ""),
            ("a", ""),
            ("a/b", "a"),
            ("a/b/", "a/b"),
            ("/a", "/"),
            ("sub/index.html", "sub"),
            ("/sub/index.html", "/sub"),
        ] {
            assert_eq!(dirname(p), want, "dirname({p:?})");
        }
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source scripts/env.sh && cargo test -p template-core pyposix`
Expected: FAIL to compile — `pyposix` does not exist.

- [ ] **Step 3: Write `pyposix.rs`**

A faithful port of `posixpath.normpath`/`join`/`dirname` (Python 3.14's
`posixpath.py`). `std::path` is not a substitute: `Path::components` collapses
`..` with a different rule and has no `//` case at all.

**`dirname` is Python 3.14's `rfind` form, not the older `split()[0]` form**, and
the two disagree on a bare filename — `dirname("a")` is `""`, not `"."`. That
case is reachable here: `construct_relative_path` calls it on
`current_template_name.lstrip("/")`, which for a top-level template *is* a bare
filename. The first draft of this step had the `split` form and failed on exactly
that row; the code below is the version that passes all twelve measured rows.

```rust
//! `posixpath`, the three functions `construct_relative_path` needs.
//!
//! A hand port rather than `std::path`: these are Python's semantics, and they
//! differ on cases the templates in the suite actually hit. `Path::components`
//! resolves `..` with lexically-different rules and has no `//` case;
//! `Path::file_name` has no `dirname("a") == ""` case at all.

/// `posixpath.join(a, b)`.
pub fn join(a: &str, b: &str) -> String {
    if b.starts_with('/') {
        return b.to_string();
    }
    if a.is_empty() || a.ends_with('/') {
        return format!("{a}{b}");
    }
    format!("{a}/{b}")
}

/// `posixpath.dirname(p)` — Python 3.14's `rfind` form, **not** `split()[0]`.
///
/// The two disagree on a bare filename: `dirname("a")` is `""`, not `"."`.
pub fn dirname(p: &str) -> String {
    let i = p.rfind('/').map_or(0, |n| n + 1);
    let head = &p[..i];
    if !head.is_empty() && head != "/".repeat(head.len()) {
        head.trim_end_matches('/').to_string()
    } else {
        head.to_string()
    }
}

/// `posixpath.normpath(path)`.
pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    // Python counts the leading slashes: 0 when relative, 2 for exactly `//x`,
    // else 1. The count is what keeps `//a` distinct from `/a`.
    let initial_slashes = if !path.starts_with('/') {
        0
    } else if path.starts_with("//") && !path.starts_with("///") {
        2
    } else {
        1
    };
    let mut new_comps: Vec<&str> = Vec::new();
    for comp in path.split('/') {
        if comp.is_empty() || comp == "." {
            continue;
        }
        if comp != ".."
            || (initial_slashes == 0 && new_comps.is_empty())
            || new_comps.last() == Some(&"..")
        {
            new_comps.push(comp);
        } else if !new_comps.is_empty() {
            new_comps.pop();
        }
    }
    let mut out = new_comps.join("/");
    if initial_slashes > 0 {
        out = format!("{}{}", "/".repeat(initial_slashes), out);
    }
    if out.is_empty() {
        ".".to_string()
    } else {
        out
    }
}
```

Add `pub mod pyposix;` to `crates/template-core/src/lib.rs`'s module list, in the
position the existing modules' order implies.

- [ ] **Step 4: Write the failing `relative.rs` test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_non_relative_name_is_returned_unchanged() {
        for name in ["'base.html'", "base.html", "'sub/base.html'", "'/abs/x.html'"] {
            assert_eq!(
                construct_relative_path(Some("index.html"), name, false).unwrap(),
                name,
                "{name}"
            );
        }
    }

    #[test]
    fn a_relative_name_resolves_against_the_origin_and_requotes() {
        assert_eq!(
            construct_relative_path(Some("sub/index.html"), "'../base.html'", false).unwrap(),
            "\"base.html\""
        );
        assert_eq!(
            construct_relative_path(Some("index.html"), "'./base.html'", false).unwrap(),
            "\"base.html\""
        );
        assert_eq!(
            construct_relative_path(Some("index.html"), "./base.html", false).unwrap(),
            "base.html"
        );
    }

    #[test]
    fn the_three_failures_carry_djangos_exact_text() {
        let unknown = construct_relative_path(None, "'./base.html'", false).unwrap_err();
        assert_eq!(
            unknown.kind,
            ErrorKind::Syntax,
            // The message, verbatim (`loader_tags.py:266-269`); note it does not
            // quote `relative_name`.
        );

        let outside = construct_relative_path(Some("index.html"), "'../../x.html'", false)
            .unwrap_err();
        assert_eq!(outside.kind, ErrorKind::Syntax);

        let same = construct_relative_path(Some("index.html"), "'index.html'", false);
        assert!(same.is_ok(), "not relative: returns early, no recursion check");

        let same = construct_relative_path(Some("index.html"), "'./index.html'", false)
            .unwrap_err();
        assert_eq!(same.kind, ErrorKind::Syntax);
        assert!(
            construct_relative_path(Some("index.html"), "'./index.html'", true).is_ok(),
            "allow_recursion skips the third check"
        );
    }
}
```

- [ ] **Step 5: Run it to verify it fails**

Run: `source scripts/env.sh && cargo test -p template-core relative::`
Expected: FAIL to compile — `parser/relative.rs` does not exist.

- [ ] **Step 6: Write `relative.rs`**

Port `loader_tags.construct_relative_path` (`LT:246-288`). `Error::syntax` for all
three messages, with `%s`-style interpolation (they are **not** reprs, so
`Host::repr` is not involved — only `extend_nodelist`'s message needs one):

```rust
//! `construct_relative_path` — `django.template.loader_tags` (`loader_tags.py:246`).
//!
//! Read by `{% extends %}` and `{% include %}`: it turns a `./`- or `../`-prefixed
//! template name into a full one against the current template's own name. The
//! three messages interpolate with `%s`, not `repr`, so this is a pure string
//! port and needs no host round-trip.

use crate::parser::error::{Error, ErrorKind};
use crate::pyposix;

pub fn construct_relative_path(
    current_template_name: Option<&str>,
    relative_name: &str,
    allow_recursion: bool,
) -> Result<String, Error> {
    // Python's `str.strip("'\"")` removes any leading/trailing quote, in any mix.
    let new_name = relative_name.trim_matches(['\'', '"']);
    if !(new_name.starts_with("./") || new_name.starts_with("../")) {
        return Ok(relative_name.to_string());
    }
    let Some(current) = current_template_name else {
        return Err(Error::syntax(format!(
            "The relative path {relative_name} cannot be evaluated due to an \
             unknown template origin."
        )));
    };
    let stripped = current.trim_start_matches('/');
    let new_name = pyposix::normpath(&pyposix::join(&pyposix::dirname(stripped), new_name));
    if new_name.starts_with("../") {
        return Err(Error::syntax(format!(
            "The relative path '{relative_name}' points outside the file hierarchy \
             that template '{current}' is in."
        )));
    }
    if !allow_recursion && stripped == new_name {
        return Err(Error::syntax(format!(
            "The relative path '{relative_name}' was translated to template name \
             '{new_name}', the same template in which the tag appears."
        )));
    }
    let bytes = relative_name.as_bytes();
    let has_quotes = matches!(bytes.first(), Some(b'"' | b'\''))
        && bytes.first() == bytes.last();
    Ok(if has_quotes {
        format!("\"{new_name}\"")
    } else {
        new_name
    })
}
```

- [ ] **Step 7: Rename the origin field and thread it**

In `parser/mod.rs`: rename the field to `origin_template_name`, retarget the doc
(:59-66) to say it holds the **raw** `Origin.template_name` (the repr is derived
in `extend_nodelist`), rename the builder to `with_origin_template_name`, add
`pub fn origin_template_name(&self) -> Option<&str>`, and change
`extend_nodelist`'s message construction to:

```rust
        if node.must_be_first && nodelist.contains_nontext {
            let origin = match self.origin_template_name() {
                Some(name) => self.py_repr(name),
                None => "the template".to_string(),
            };
```

In `crates/template-py/src/lib.rs`: rename the `parse` keyword and
`parse_source`'s parameter to `origin_template_name`, and pass it to
`with_origin_template_name`. Give `compile_template` an `origin` parameter —
`#[pyo3(signature = (source, parser, tree, engine, host = None, origin = None))]`
— and pass it where `py.None()` is today (`:2169`), so the materialiser's
`origin` is a `Bound<'_, PyAny>` or `None`.

In `render.py`: `parser_for(source, engine=None, origin=None)`, passing
`origin=origin` to `Parser(...)`; `parse_template(source, engine=None,
origin=None)` forwarding it both to `parser_for` and to `compile_template`'s new
keyword; `render_string(source, context, engine=None, origin=None)` forwarding
it. **`compile_template`'s materialisation origin is the same object**, so
`ExtendsNode.render`'s `[self.origin]` and `IncludeNode.render`'s
`self.origin.template_name` both read a real `Origin`.

- [ ] **Step 8: Run the guards, then the gates**

Run: `source scripts/env.sh && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings && grep -rn origin_template_repr crates/ python/ tests/ scripts/ ; maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m pytest tests/ -x -q`

Expected: all pass; the `grep` prints nothing (exit 1, which is the wanted
result). No corpus row exercises an origin yet — Task 3's rows are the first —
so the rename's only visible effect here is that everything still passes.

Observe the guard: revert `extend_nodelist` to a hard-coded `"the template"` and
confirm Task 1's error-corpus rows still pass but the *stand-in* must-be-first row
in `tests/parser_error_corpus.py` (which passes an origin) fails. If no row
notices, one is missing and this task adds it.

- [ ] **Step 9: Commit**

```bash
git add crates/template-core/src/pyposix.rs crates/template-core/src/lib.rs \
        crates/template-core/src/parser/relative.rs crates/template-core/src/parser/mod.rs \
        crates/template-py/src/lib.rs python/django_rust_templates/render.py \
        python/django_rust_templates/_impl/parser.py tests/
git commit -m "M7 Task 2: the origin threaded to the parser and the tree; construct_relative_path"
```

---

## Task 3: `{% extends %}` — and the descriptor-tree walk Django does with `get_nodes_by_type`

**Files:**
- Create: `crates/template-core/src/parser/tags/extends.rs`
- Modify: `crates/template-core/src/parser/tags/mod.rs`
- Modify: `crates/template-core/src/parser/desc.rs` (`NodeListDesc`, `:386-394`)
- Modify: `crates/template-core/src/parser/registry.rs`
- Modify: `python/django_rust_templates/_impl/parser.py` (`NODE_CLASSES`, import)
- Modify: `tests/test_tag_inventory.py`, `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `relative::construct_relative_path`, `Parser::origin_template_name`, `Parser::compile_filter` (`parser/mod.rs:144`), `NodeDesc::nodelist`, `AttrValue`.
- Produces: `tags::extends::compile`; `NodeListDesc::get_nodes_by_type(&self, class_name: &str) -> Vec<&NodeDesc>`. Task 4 consumes neither (its `do_include` does no walk), but Task 7's seams audit reads `get_nodes_by_type`'s divergence note.

**Declared environment:** `NODE_CLASSES` gains `ExtendsNode`, whose `__init__(self, nodelist, parent_name, template_dirs=None)` reads its own subtree — `self.blocks = {n.name: n for n in nodelist.get_nodes_by_type(BlockNode)}` (`LT:94`). The materialiser already builds children before the parent (`_impl/parser.py:288-307`), which is what makes that work; Task 1's `BlockNode` is what makes it *find* anything, so Task 1 precedes this one.

- [ ] **Step 1: Write the failing `get_nodes_by_type` test**

```rust
#[test]
fn get_nodes_by_type_reaches_an_extends_node_inside_an_if_branch() {
    // `{% if x %}{% extends "a" %}{% endif %}` — Django's walk reaches it,
    // because `IfNode.child_nodelists` is `()` and the branch lists live inside
    // `conditions_nodelists`, which Rust emits as empty-named nodelists
    // (`desc.rs:649-650`). A walk that only followed *named* nodelists would
    // miss it.
    let tree = compile("{% if x %}{% extends \"a\" %}{% endif %}");
    assert_eq!(tree.get_nodes_by_type("ExtendsNode").len(), 1);
    assert!(tree.get_nodes_by_type("BlockNode").is_empty());
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source scripts/env.sh && cargo test -p template-core get_nodes_by_type`
Expected: FAIL to compile — no such method on `NodeListDesc`.

- [ ] **Step 3: Write the walk**

```rust
impl NodeListDesc {
    /// `NodeList.get_nodes_by_type(nodetype)` — `base.py:1092-1104`.
    ///
    /// Django's `NodeList` version delegates to each node's, which tests
    /// `isinstance(self, nodetype)` and then recurses into `child_nodelists`.
    /// The descriptor has the class **name**, and Rust emits only child
    /// nodelists — including `IfNode`'s branch lists, whose names are empty
    /// (`desc.rs:649-650`) — so the two walks visit the same set for every node
    /// Rust builds.
    ///
    /// **One recorded divergence:** a descriptor whose `class_name` is
    /// [`HOST_NODE`] names a Python node Rust must not read, so it is neither
    /// matched nor descended into. A host tag returning an `ExtendsNode`
    /// *subclass* would match Django's `isinstance` and not this — and so would
    /// a host block tag whose body contains a second `{% extends %}`. Both are
    /// unreachable in the pinned suite, which registers no library tag named
    /// `block` or `extends`; see the plan's Review Focus item 3.
    pub fn get_nodes_by_type(&self, class_name: &str) -> Vec<&NodeDesc> {
        let mut found = Vec::new();
        for node in &self.nodes {
            if node.class_name == class_name {
                found.push(node);
            }
            for (_name, child) in &node.nodelists {
                found.extend(child.get_nodes_by_type(class_name));
            }
        }
        found
    }
}
```

- [ ] **Step 4: Write the failing `extends` test**

```rust
#[test]
fn extends_carries_the_parent_as_a_compiled_filter_expression() {
    let node = compile("{% extends \"base.html\" %}");
    assert_eq!(node.class_name, "ExtendsNode");
    assert!(matches!(node.attr("parent_name"), Some(AttrValue::Ref(_))));
    assert_eq!(node.nodelist("nodelist").map(|n| n.nodes.len()), Some(0));
}

#[test]
fn a_second_extends_is_rejected_but_a_nested_one_is_unreachable() {
    let error = failure("{% extends \"a\" %}{% extends \"b\" %}");
    assert_eq!(error.kind, ErrorKind::Syntax);
    // `do_extends` ends with an unbounded `parser.parse()` (`LT:309`), so the
    // second `{% extends %}` is always consumed as a top-level sibling — which
    // is why the check is a scan of the nodelist just parsed.
    assert_eq!(failure("{% extends \"a\" %}").kind, ErrorKind::Unimplemented);
}

#[test]
fn extends_takes_exactly_one_argument() {
    assert_eq!(failure("{% extends %}").kind, ErrorKind::Syntax);
    assert_eq!(failure("{% extends \"a\" \"b\" %}").kind, ErrorKind::Syntax);
}
```

- [ ] **Step 5: Write `extends.rs`**

```rust
//! `{% extends %}` — `django.template.loader_tags.do_extends` (`loader_tags.py:291`).

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::relative::construct_relative_path;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // `split_contents()`, not `contents.split()`: `{% extends %}` takes a
    // *variable* as readily as a literal (`LT:296-299`), so the quoting is
    // `compile_filter`'s business below.
    let mut bits = split_contents(token)?;
    if bits.len() != 2 {
        return Err(Error::syntax(format!("'{}' takes one argument", bits[0])));
    }
    let resolved =
        construct_relative_path(parser.origin_template_name(), &bits[1], false)?;
    bits[1] = resolved;
    // The parent is a host-built `FilterExpression`: `ExtendsNode.get_parent`
    // calls `self.parent_name.resolve(context, ignore_failures=True)`
    // (`LT:123`), which only a real one answers.
    let parent_name = parser.compile_filter(&bits[1])?;
    let nodelist = parser.parse(&[])?;
    if !nodelist.get_nodes_by_type("ExtendsNode").is_empty() {
        return Err(Error::syntax(format!(
            "'{}' cannot appear more than once in the same template",
            bits[0]
        )));
    }
    Ok(NodeDesc::new("ExtendsNode")
        .attr("parent_name", parent_name)
        .nodelist("nodelist", nodelist))
}
```

- [ ] **Step 6: Register it, and materialise it**

`tags/mod.rs`: `pub mod extends;`. `registry.rs`: `("extends", tags::extends::compile),`
after `debug`; the doc paragraph drops `extends` (and says **Three**); the count
becomes `24 = 27 built-ins minus those three`; the two registry tests and the
`expected` list follow (23 → 24). In `tests/test_tag_inventory.py`,
`PYTHON_OWNED` loses `"extends"` and the literal becomes 24.

In `_impl/parser.py`, import `ExtendsNode` from `django.template.loader_tags`
and add `"ExtendsNode": ExtendsNode,` to `NODE_CLASSES`.

- [ ] **Step 7: Corpus rows**

`tests/parser_corpus.py`:

```python
    "{% extends \"base.html\" %}",
    "{% extends \"base.html\" %}{% block a %}x{% endblock %}",
    "{% extends parent %}",
```

`tests/parser_error_corpus.py`:

```python
    ("{% extends %}", "'extends' takes one argument"),
    ("{% extends \"a\" \"b\" %}", "'extends' takes one argument"),
    (
        "{% extends \"a\" %}{% extends \"b\" %}",
        "'extends' cannot appear more than once in the same template",
    ),
```

Add to `tests/parser_error_corpus.py`'s module docstring the note that the two
relative-path messages are **not** reachable from a bare `from_string` origin
(they need a template name), so their rows carry an explicit origin — see Task
2's `_native.parse(..., origin_template_name=...)` call sites.

- [ ] **Step 8: Run the guards, then the gates**

Run: `source scripts/env.sh && cargo test -p template-core && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m pytest tests/ -x -q`
Expected: all pass.

Observe the walk's guard failing: change `get_nodes_by_type` to skip nodelists
whose name is empty and confirm Step 1's test fails — that is the branch the
`{% if %}`-wrapped case exists for.

- [ ] **Step 9: Commit**

```bash
git add crates/template-core/src/parser/tags/extends.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/desc.rs \
        crates/template-core/src/parser/registry.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/test_tag_inventory.py tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "M7 Task 3: {% extends %}, and the descriptor-tree walk"
```

---

## Task 4: `{% include %}` — and the two-clause predicate change

**Files:**
- Create: `crates/template-core/src/parser/tags/include.rs`
- Create: `tests/test_inheritance_gate.py`
- Modify: `crates/template-core/src/parser/tags/mod.rs`, `registry.rs`
- Modify: `python/django_rust_templates/host.py` (delete `_M7_OWNED` `:37-48`; `has_tag` `:113-131`; `django_own_tags` `:51-80`)
- Modify: `crates/template-core/src/parser/mod.rs` (`parse`'s host-first comment `:309-317`)
- Modify: `python/django_rust_templates/_impl/parser.py`, `tests/test_tag_inventory.py`, `tests/parser_corpus.py`, `tests/parser_error_corpus.py`

**Interfaces:**
- Consumes: `relative::construct_relative_path`, `Parser::origin_template_name`, `Parser::compile_filter`, `kwargs::token_kwargs` (`parser/kwargs.rs:123`, `support_legacy=false`), `AttrValue::Map`, `AttrValue::Bool`.
- Produces: `tags::include::compile`. Nothing downstream consumes it.

**Declared environment:** this is the commit where the *predicate* changes, so it
is the first commit in which `ShellHost.has_tag("block")` can be observed
returning the wrong answer. Run Step 6's test **before** Step 5's edits and
confirm it fails, then after and confirm it passes — that ordering is the whole
of A2's evidence.

- [ ] **Step 1: Write the failing `include` test**

```rust
#[test]
fn include_carries_its_options_and_is_not_recursion_checked() {
    let node = compile("{% include \"a.html\" %}");
    assert_eq!(node.class_name, "IncludeNode");
    assert!(matches!(node.attr("template"), Some(AttrValue::Ref(_))));
    assert_eq!(node.attr("isolated_context"), Some(&AttrValue::Bool(false)));
    assert_eq!(node.attr("extra_context"), Some(&AttrValue::Map(vec![])));
}

#[test]
fn include_reads_with_and_only() {
    let node = compile("{% include \"a.html\" with b=1 only %}");
    assert_eq!(node.attr("isolated_context"), Some(&AttrValue::Bool(true)));
    assert_eq!(node.attr("extra_context").map(|v| match v {
        AttrValue::Map(entries) => entries.len(),
        _ => 0,
    }), Some(1));
}

#[test]
fn includes_three_option_failures_are_djangos() {
    assert_eq!(failure("{% include %}").kind, ErrorKind::Syntax);
    assert_eq!(failure("{% include \"a\" with %}").kind, ErrorKind::Syntax);
    assert_eq!(failure("{% include \"a\" only only %}").kind, ErrorKind::Syntax);
    assert_eq!(failure("{% include \"a\" nope %}").kind, ErrorKind::Syntax);
}
```

- [ ] **Step 2: Write `include.rs`**

Port `do_include` (`LT:315-370`) with the option loop verbatim — three `%r`
messages, so `parser.py_repr` is the interpolation for all three, and
`token_kwargs(..., support_legacy=false)` is what makes `{% include "a" nope %}`
reach the "Unknown argument" arm rather than being read as a kwarg. The final
call is:

```rust
    Ok(NodeDesc::new("IncludeNode")
        .attr("template", template)
        .attr("extra_context", AttrValue::Map(namemap))
        .attr("isolated_context", AttrValue::Bool(isolated_context)))
```

`namemap` is an empty `Vec` when there is no `with`, which is Django's
`extra_context or {}` collapsed — an empty `Map` materialises as `{}`, and
`IncludeNode.__init__` stores `extra_context or {}` either way.

- [ ] **Step 3: Register it**

`tags/mod.rs`: `pub mod include;`. `registry.rs`: `("include", tags::include::compile),`
after `ifchanged`; the doc paragraph drops `include` (and says **Two**: `load` and
`querystring`); the count becomes `25 = 27 built-ins minus those two`; the two
registry tests and the `expected` list follow (24 → 25).
`test_tag_inventory.py`: `PYTHON_OWNED = {"load", "querystring"}`, the literal
25, and both test names stop being about a number of M7 names.

- [ ] **Step 4: Materialise `IncludeNode`**

Import `IncludeNode` from `django.template.loader_tags`; add
`"IncludeNode": IncludeNode,` to `NODE_CLASSES`.

- [ ] **Step 5: The two-clause predicate change**

Delete `host.py`'s `_M7_OWNED` (`:37-48`), the clause that reads it
(`:117-122`), and the sentence in the module docstring at `:14-19` that names the
five names — the identity clause is now the **whole** of the discrimination, for
the two Python-owned names as much as for the rest. Widen `django_own_tags()`:

```python
def django_own_tags():
    """The tag callables Rust ports — read from the modules it ports, once.

    ... (keep the existing docstring's body) ...
    """
    return {**_defaulttags.register.tags, **_loader_tags.register.tags}
```

with `from django.template import loader_tags as _loader_tags` beside the
`defaulttags` import, and the docstring's last paragraph replaced by the
measurement A2 records: `loader_tags.register.tags`'s keys are exactly
`['block', 'extends', 'include']`, its dict is disjoint from `defaulttags`', and
without the merge all three would route to the host.

Update `parser/mod.rs`'s host-first comment (`:309-317`) — "a custom library that
redefines a built-in name wins" is still true, and now so is the case where the
*baseline* callable lives in a second module.

- [ ] **Step 6: The predicate test — write it, watch it fail, then fix**

In `tests/test_inheritance_gate.py`:

```python
def test_the_host_declines_every_name_rust_owns():
    """A2: the predicate must say 'Rust's' for all three, with no exception list.

    Before `django_own_tags` merged `loader_tags`, this returned True for all
    three: `loader_tags.register.tags` is a second dict, so
    `self._django_tags.get("block")` was None and `func is not None` held. That
    is the trap row of the plan's A2 matrix — a half-fix that compiles
    `{% extends %}` anywhere.
    """
    from django.template import loader_tags

    from django_rust_templates.host import ShellHost
    from django_rust_templates.render import parser_for

    parser = parser_for("")
    host = ShellHost(parser)
    for name in ("block", "extends", "include"):
        with self.subTest(name=name):
            self.assertIs(parser.tags[name], loader_tags.register.tags[name])
            self.assertIs(host.has_tag(name), False)


def test_a_loaded_library_still_shadows_a_built_in():
    """A2's row 4: the identity clause survives the widening."""
    from django.template import Engine, Library

    from django_rust_templates.host import ShellHost
    from django_rust_templates.render import parser_for

    def mine(parser, token):
        raise NotImplementedError

    lib = Library()
    lib.tag("block", mine)
    engine = Engine()
    engine.template_libraries["custom"] = lib
    parser = parser_for("", engine)
    parser.add_library(lib)
    self.assertIs(ShellHost(parser).has_tag("block"), True)
```

Run it **before** Step 5's edits: the first test must fail on `block` (and would
pass on nothing else), which is the trap made visible. Then apply Step 5 and
re-run.

- [ ] **Step 7: Corpus rows**

`tests/parser_corpus.py`:

```python
    "{% include \"a.html\" %}",
    "{% include \"a.html\" with b=1 %}",
    "{% include \"a.html\" with b=1 only %}",
    "{% include template_name %}",
```

`tests/parser_error_corpus.py`:

```python
    (
        "{% include %}",
        "'include' tag takes at least one argument: the name of the template to be included.",
    ),
    ("{% include \"a\" with %}", "\"with\" in 'include' tag needs at least one keyword argument."),
    ("{% include \"a\" only only %}", "The 'only' option was specified more than once."),
    ("{% include \"a\" nope %}", "Unknown argument for 'include' tag: 'nope'."),
```

(The four strings are produced by the ported code from Django's own `%r`
interpolations; run them against `.django` before committing the rows, as every
snippet in this plan is run — the quoting is what a reader will get wrong.)

- [ ] **Step 8: Run the guards, then the gates**

Run: `source scripts/env.sh && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m pytest tests/ -q && .venv/bin/python -m pytest tests/parser_corpus.py`
Expected: all pass.

Observe the trap: revert `django_own_tags` to `dict(_defaulttags.register.tags)`
and confirm Step 6's test fails **and** that `{% extends "a" %}{% extends "b" %}`
stops raising (the error-corpus row from Task 3 fails) — the half-fix produces
both symptoms, which is why they are asserted together.

- [ ] **Step 9: Commit**

```bash
git add crates/template-core/src/parser/tags/include.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/registry.rs \
        crates/template-core/src/parser/mod.rs \
        python/django_rust_templates/host.py \
        python/django_rust_templates/_impl/parser.py \
        tests/test_inheritance_gate.py tests/test_tag_inventory.py \
        tests/parser_corpus.py tests/parser_error_corpus.py
git commit -m "M7 Task 4: {% include %}, and the two-clause host predicate"
```

---

## Task 5: `must_be_first` crosses inbound — the M6 defect M7 inherits

**Files:**
- Modify: `crates/template-py/src/lib.rs` (`FilterCheckHost::call_tag`, `:1212-1295` — the read goes before the `node.unbind()` at `:1289-1293`)
- Modify: `tests/test_inheritance_gate.py`

**Interfaces:**
- Consumes: `NodeDesc.must_be_first` (`desc.rs:407`, `pub`), `NodeDesc::host`.
- Produces: nothing another task consumes. `Parser::extend_nodelist`
  (`parser/mod.rs:429-457`) already reads `node.must_be_first`; this task is what
  makes it non-`false` for a host node.

**Declared environment:** this is a `template-py` change, so `maturin develop` is
required before the Python test. The behaviour is measured, not assumed
(`/tmp/m7_measureB.py`, on the pinned tree):

```text
{% load mylib %}{% first_tag %}                     -> OK  'F'
text{% load mylib %}{% first_tag %}                 -> {% first_tag %} must be the first tag in the template.
{% load mylib %}{% plain_tag %}text{% plain_tag %}  -> {% plain_tag %} must be the first tag in the template.
```

Note the second row fails on the `{% load %}`'s account: `{% load %}` is itself a
block tag, so `contains_nontext` is already true before the `text` is reached.
The message names the tag, not the offending node's position.

- [ ] **Step 1: Write the failing test**

In `tests/test_inheritance_gate.py`:

```python
class MustBeFirstGateTests(unittest.TestCase):
    """A4: the flag crosses inbound, off the Python node the host built."""

    def _engine(self):
        from django.template import Engine, Library
        from django.template.base import Node

        class FirstNode(Node):
            must_be_first = True

            def render(self, context):
                return "F"

        lib = Library()

        @lib.tag("first_tag")
        def do_first(parser, token):
            return FirstNode()

        engine = Engine()
        engine.template_libraries["mylib"] = lib
        return engine

    def test_a_host_node_that_must_be_first_is_refused_after_text(self):
        from django.template import Context, TemplateSyntaxError

        from django_rust_templates.render import render_string

        engine = self._engine()
        source = "text{% load mylib %}{% first_tag %}"

        with self.assertRaises(TemplateSyntaxError) as ours:
            render_string(source, {}, engine)
        # The stock side, in the same row — the pattern this project has needed
        # three times (scripts/differential.py:57-69 is the record).
        with self.assertRaises(TemplateSyntaxError) as stock:
            engine.from_string(source).render(Context({}))
        self.assertEqual(str(ours.exception), str(stock.exception))
        self.assertEqual(
            str(ours.exception),
            "{% first_tag %} must be the first tag in the template.",
        )

    def test_a_host_node_that_must_be_first_is_accepted_first(self):
        from django_rust_templates.render import render_string

        self.assertEqual(render_string("{% load mylib %}{% first_tag %}", {}, self._engine()), "F")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m pytest tests/test_inheritance_gate.py -k must_be_first -q`
Expected: FAIL — the refused case renders `'textF'` instead of raising, because
`NodeDesc.must_be_first` is `false` for a host descriptor.

- [ ] **Step 3: Read the flag in `call_tag`**

In `crates/template-py/src/lib.rs`, immediately after the `(node, left)` match
(`:1246-1258`) and **before** anything moves `node`:

```rust
        // The flag crosses **inbound**: this is the Python node the host's
        // compile function just returned, and `must_be_first` is a class
        // attribute every Django `Node` has (`base.py:1031-1035`) which
        // `extend_nodelist` reads unconditionally (`base.py:602`).
        //
        // **Raise rather than default `false`.** An object without the attribute
        // is not a node, and defaulting would turn "the host returned the wrong
        // thing" into "no ordering check needed" — silently accepting a tag
        // Django refuses. The outbound 5-tuple (`node_desc_to_py`, `:871-915`)
        // is untouched: it is a descriptor-to-shell wire, not this one.
        let must_be_first = node
            .getattr("must_be_first")
            .and_then(|flag| flag.extract::<bool>())
            .map_err(|error| {
                Error::internal(format!(
                    "ShellHost.call_tag({command:?}) returned a node without a \
                     bool `must_be_first`: {error}"
                ))
            })?;
```

and where the descriptor is built (`:1289-1294`):

```rust
        let mut desc = NodeDesc::host(handle);
        desc.must_be_first = must_be_first;
        Ok((desc, left))
```

- [ ] **Step 4: Run the test, then the gates**

Run: `source scripts/env.sh && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m pytest tests/ -q`
Expected: PASS; no other test changes.

Observe the guard: change `.map_err(...)?` to `.unwrap_or(false)` and confirm
Step 1's first test fails again — that is the difference between the two
spellings, and it is the whole of A4.

- [ ] **Step 5: Commit**

```bash
git add crates/template-py/src/lib.rs tests/test_inheritance_gate.py
git commit -m "M7 Task 5: must_be_first crosses the seam inbound"
```

---

## Task 6: `test_include_cache`, in a form that can fail

**Files:**
- Modify: `tests/test_inheritance_gate.py`

**Interfaces:**
- Consumes: `render_string(source, context, engine)` (`render.py:164`).
- Produces: nothing. This task is the discharge of §10.3's named gate (A6).

**Declared environment:** the engine is built with a `locmem` loader and the
`template_tests.templatetags.custom` library, exactly as
`.django/tests/template_tests/syntax_tests/test_include.py:371` builds it — the
row is a *copy in shape*, not in instrument. `tests/test_host_tags.py:15-19` is
the precedent for `CUSTOM_LIBRARIES` resolving in this suite.

- [ ] **Step 1: Write the test, and confirm its instrument can fail**

```python
class IncludeCacheGateTests(unittest.TestCase):
    """§5.5 / §10.3: the shadow tree is materialised once, node identity stable.

    The `.django` row cannot be this gate — the shim does not yet interpose
    `loader_tags`, so the suite runs on stock Django's parser and would pass
    whether or not M7 shipped. This is the same row pointed at our engine.
    """

    SOURCE = '{% for x in vars %}{% include "include" %}{% endfor %}'

    def _engine(self):
        from django.template import Engine

        return Engine(
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        # The same `{% include %}` in a loop is **one**
                        # `IncludeNode`, so `IncludeNode.render`'s
                        # `render_context.dicts[0].setdefault(self, {})`
                        # (`loader_tags.py:197`) must hit from the second
                        # iteration on. A materialiser that built a fresh node
                        # per render would miss every time and restart the
                        # counter -- which is what makes the assertion able to
                        # fail rather than merely able to pass.
                        "template": '{% for x in vars %}{% include "include" %}{% endfor %}',
                        "include": '{% include "next" %}',
                        "next": "{% load custom %}{% counter %}",
                    },
                ),
            ],
            libraries={"custom": "template_tests.templatetags.custom"},
        )

    def test_the_included_template_is_resolved_once(self):
        from django.template import Context

        from django_rust_templates.render import render_string

        engine = self._engine()
        context = {"vars": range(9)}
        self.assertEqual(
            render_string(self.SOURCE, dict(context), engine), "012345678"
        )
        # Stock, in the same row.
        self.assertEqual(engine.render_to_string("template", context), "012345678")
```

- [ ] **Step 2: Confirm the instrument fails for its intended reason**

Mutate `IncludeNode.render`'s cache key in a scratch copy — or, cheaper and what
the project's rule asks for, break our side: in `_impl/parser.py::materialise_node`,
make the `IncludeNode` arm return a *copy* of the node. Run the test and confirm
it reports `"012345678"` vs a string with repeated digits — the counter
restarting. Revert.

- [ ] **Step 3: Run the gates**

Run: `source scripts/env.sh && .venv/bin/python -m pytest tests/test_inheritance_gate.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_inheritance_gate.py
git commit -m "M7 Task 6: the include-cache gate, pointed at our engine"
```

---

## Task 7: Close-out — the seams audit, the counts, and the spec

**Files:**
- Create: `.review/m7-seams.md`
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.3's M7 row, `:769`; §10.4's M7 paragraph, `:988-994`)
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.4's M7-obligation line if the `installed()` instrument moved)
- Modify: `docs/superpowers/plans/2026-10-09-m7-inheritance.md` (this file — the counts it states as targets become measurements)

**Interfaces:**
- Consumes: everything above.
- Produces: the M8 entry state.

**Declared environment:** this task runs the **full** gate, not the fast one:
`./scripts/check.sh` (which adds the generated-file `--check`s, the anchor
manifest, the differential harness, and `run_django_suite.sh template_tests`).
The `.django/` suite must still report `Ran 1547 tests … OK (skipped=6)` with
`unshimmed()` unchanged at 25 — M7 changes no interposition, so any movement
there is a defect this task fixes rather than records.

- [ ] **Step 1: Measure, then write `.review/m7-seams.md`**

In `m4-seams.md`/`m5-seams.md`'s shape: the seam inventory with each crossing's
*direction*, the divergences recorded (A5's straddling `__loaded_blocks`, A3's
non-matching `get_nodes_by_type` for a host `ExtendsNode` subclass), and the
dated verdict from `m5-seams.md:1-40` — "**A verdict here is dated.** It is a
number only for as long as the thing it numbered holds still." Take each number
from the command that produces it in this step; do not restate a number measured
in an earlier session.

- [ ] **Step 2: Update the spec**

§10.3's M7 row: the gate is `tests/test_inheritance_gate.py`, and
`test_include_cache` is named as the *shape* copied rather than the suite row
that runs. §10.4's M7 paragraph: the `parser/mod.rs:336` citation becomes a
symbol (`Parser::extend_nodelist`) rather than a line, per the project's
pointer-not-snapshot rule. Record D1–D4 as corrections in the spec's own voice.

- [ ] **Step 3: Correct this plan's targets**

Every count this plan states as a target (`22 → 23 → 24 → 25`, the corpus and
error-corpus deltas, `PYTHON_OWNED`) is replaced by the measured value and the
command that produced it, or the sentence is deleted. A plan that ships a stale
target is the defect the "derivations, not snapshots" constraint exists for.

- [ ] **Step 4: Run the full gate**

Run: `source scripts/env.sh && ./scripts/check.sh`
Expected: `all checks passed`, and `Ran 1547 tests … OK (skipped=6)` with
`unshimmed()` 25.

- [ ] **Step 5: Commit**

```bash
git add .review/m7-seams.md docs/superpowers/specs/ docs/superpowers/plans/
git commit -m "M7 close-out: the seams audit, the counts re-derived, the spec corrected"
```

---

## Self-Review

Run against the spec with fresh eyes, as the writing-plans skill requires.

**1. Spec coverage.** §10.3's M7 row names three things: the three tags'
compile functions (Tasks 1, 3, 4), inheritance corpus parity (each task's corpus
rows), and `test_include_cache` (Task 6). §10.4's M7 obligation — "compiling the
inheritance structure" — is A1's Design B, and the §5.2 seam obligations it
names are A2's predicate and A4's flag. §5.5's materialise-once claim is Task 6's
instrument. **One spec requirement has no task and is deliberately not given
one:** §10.3's M7 row is the last row that mentions the `.django/` suite, and M7
does not interpose `loader_tags`, so the suite-side gate remains M9's and M11's.
That is stated in the Global Constraints rather than silently skipped.

**2. Placeholder scan.** No "TBD"/"TODO"/"implement later". One code step is
deliberately not a full listing, and it says why and where the reference is:
Task 4 Step 2 (`include.rs`'s option loop — `do_include`'s body verbatim
(`LT:319-348`), with the three `%r` messages and the final `NodeDesc` given).
Transcribing Django's source into a plan is the thing §6's "not vendored" rule
exists to avoid, and the loop is line-for-line `do_include` with `parser` for
`parser`, `bits[0]` for `bits[0]`. **Task 2 Step 3 was the plan's other such
step and no longer is**: it now carries the full `pyposix.rs`, because the
"mechanical port" framing hid a real decision — Python 3.14's `dirname` is the
`rfind` form, and the `split`-based form the first draft would have produced
fails `dirname("a") == ""`. That is the shape of placeholder this project has
lost time to, so the code is in the plan.

**3. Type consistency.** Every pure-Rust expression this plan writes was compiled
and run before the plan was committed: the `&[String]`/`String` comparison at
Task 1 Step 3 (`*name == block_name`, which is `String == String` through
`PartialEq` and does not move `block_name`), the `trim_matches(['\'', '"'])`
port, the `bytes.first() == bytes.last()` requoting test, and the whole
`pyposix` port against its twelve-row measured table. `construct_relative_path`'s
signature (`Option<&str>, &str, bool`) is identical in Tasks 2, 3 and 4.
`get_nodes_by_type` returns `Vec<&NodeDesc>` in Task 3 and is only called for
`.is_empty()`. `token_kwargs`'s three parameters match `parser/kwargs.rs:123-127`
in Task 4. The `template-py` snippets (Tasks 2 and 5) were **not** compiled:
Task 5's `node.getattr(...)` is written against a `Bound<'_, PyAny>` whose
`getattr` returns `PyResult<Bound<'_, PyAny>>` and whose `extract::<bool>()`
returns `PyResult<bool>`, which is what `.and_then` chains — verify it at Task 5
Step 2, which is a compile, not a guess.

**4. Review Focus.** Each of the five is pinned: item 1 by Task 4 Step 6; item 2
by Task 2 Step 7's plumbing and Task 3's error rows; item 3 by Task 3's
`get_nodes_by_type` divergence note and its error row; item 4 by Task 6; item 5
by Task 1 Step 8's duplicate-name row. No line is left without a test.

**5. Known-unsound.** The `is_ok()`/`is_err()` assertions in Task 2 Step 4's and
Task 3 Step 4's tests do not check the *message*; the messages are checked by
the error-corpus rows instead, which is where Django's exact text lives. A reader
should not read `assert_eq!(kind, ErrorKind::Syntax)` as a message assertion.
