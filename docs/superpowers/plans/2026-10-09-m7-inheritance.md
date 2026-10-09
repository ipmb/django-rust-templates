# M7 — Inheritance: `{% block %}`, `{% extends %}`, `{% include %}`: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the last three built-in tags §6 assigns to Python onto Rust's side of the seam — compile functions for `{% block %}`, `{% extends %}` and `{% include %}` in `template-core`, the origin plumbing `{% extends %}` and `{% include %}` read, and the `must_be_first` carry that lets a *host* tag's node be refused in a non-first position — so the inheritance machinery is Rust's to compile and Django's to render.

**Architecture:** M6 left the host seam in place and three built-ins deliberately outside it (`host.py:48`, `_M7_OWNED`). M7 closes that gap, and the shape it takes is **Design B**: the three names join `REGISTRY`, so their compile functions are Rust's, while their *node classes* stay Django's and render in Python — exactly the division M3 established (§5.5) and every prior tag already uses. What makes Design B non-trivial is a **two-clause** change to the host predicate: adding the names to Rust's table is not enough and is actively harmful alone, because `loader_tags.register.tags` is a *separate* dict from `defaulttags.register.tags`, so the identity clause in `ShellHost.has_tag` would see `func is not self._django_tags.get("block")` (which is `None`) and route all three back to the host. The names must land in `REGISTRY` **and** `loader_tags` must merge into `django_own_tags()`, in one commit, or the milestone accepts `{% extends %}` in positions Django refuses it.

**Tech Stack:** Rust (edition 2021, no Python in `template-core`), PyO3 0.29 in `template-py`, Python 3.14 shell, Django 6.0.9 pinned at `.django/`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — read §5.2 (the seam), §5.5 (the shadow tree, and `test_include_cache`), §6 (ownership), §9 (the three test layers), §10.3 (the M7 row), §10.4 (the M7 obligation). This plan argues from the spec; where they disagree the spec wins and this plan is wrong. **One spec citation is a vintage and one spec gate is vacuous, and both are corrected here**, alongside two findings about the spec itself that came out of the reviewer's round (§D names all six). Read `.review/m7-recon.md` before this file; it carries the measurements this plan's architecture rests on, and §D records where this plan corrects it.

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
2. **`{% extends %}`'s behaviour depends on an origin, and the shell passes
   none.** Every `Origin` Django builds without a loader has
   `template_name = None`, so that `None` **is** the unknown-origin state and a
   *relative* `{% extends %}` under it must raise
   `The relative path … cannot be evaluated due to an unknown template origin.`
   (`loader_tags.py:266-269`) rather than parse on. Today `render.parser_for`
   builds its `Parser` with `origin=None` — a state Django's own `Parser` never
   has — and `compile_template` passes `py.None()` as the materialisation
   origin. **Two distinct repairs, and conflating them is the trap**: the
   *host* `Parser` needs a real `Origin(UNKNOWN_SOURCE)` (because Django's own
   `do_extends` can still be reached through a host tag's nested parse), while
   Rust's `origin_template_name` needs the *attribute*, whose value is `None`
   either way. **Owned by Task 2; pinned by an error-corpus row whose expected
   message is the full `must be the first tag in <origin>` string and by a
   relative-`{% extends %}` row carrying the unknown-origin message verbatim**,
   which can only match if the origin travelled.
3. **A nodelist the host returned has no descriptor children to walk.** Django's
   `do_extends` asks the just-parsed nodelist `get_nodes_by_type(ExtendsNode)`
   (`loader_tags.py:308`) to reject a second `{% extends %}`. Under the host
   seam a *host* tag's body is a Python `NodeList` stored behind a `NodeRef`, and
   Rust cannot walk it. M7's three take the Rust path, so the walk is over
   `NodeDesc`s and is exact — but a host block tag wrapping an `{% extends %}`
   still hides one, and the failure is silent acceptance rather than a wrong
   tree. **Measured zero in the pinned suite** — `{% extends %}` nested inside a
   host-owned block-level tag occurs 0 times over 1046 literals and 62 fixture
   templates (`.review/m7-measurements/m7_measure_nest2.py`), and no library returns an
   `ExtendsNode` subclass. **Owned by Task 3; the divergence is recorded, not
   fixed** (it is the same class as M6's Review Focus item 1), and the test
   asserts the *reachable* half: a second `{% extends %}` at top level is
   rejected with Django's exact message.
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
   another tag's body — but only for tags Rust owns, which is A5's recorded
   divergence: a `{% block %}` inside a *host* tag's body is counted by Django,
   not here. **That case is reached once in the pinned suite** (A5's measurement)
   and is harmless there, so the requirement is that the counter survives a
   nested `{% block %}` under a Rust-owned tag, which the error corpus can
   exercise. **Owned by Task 1; pinned by the error-corpus rows for
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

**The reason Design A loses is not that its data cannot be carried — it can.**
The first version of this section argued that `parser.origin.template_name`,
`parser.__loaded_blocks` and `nodelist.get_nodes_by_type` are not values a
`NodeDesc` can carry, so Design A would have to grow the wire. The reviewer
falsified that, and the falsification is correct: **under Design A nothing needs
to cross**, because the host does the carrying. `ShellHost` holds one real Django
`Parser` for the whole parse, so `do_block`'s `parser.__loaded_blocks`
(`loader_tags.py:228-234`) persists across the template and `do_extends`'s
`nodelist.get_nodes_by_type(BlockNode)` (`:94`, `:308`) runs on a nodelist the
host parsed itself; `parser.origin.template_name` (`:305`) is the only gap, and
it is one keyword argument — the one Task 2 threads anyway. The claim was true
and did not bear on the choice, which is the shape of argument this project keeps
having to unmake.

**The reason that does carry it is M6's Review Focus item 1, made permanent.**
A host tag's body is parsed by Django — `do_block` calls the *handle's*
`parser.parse`, so every tag inside a `{% block %}` is compiled by Django's
registry, not Rust's. Under Design A that is true of `block`, `extends` and
`include` **forever**: they are host tags in M7 and stay host tags. So when the
suite routes through our parser at M9, M7's inheritance tests would be comparing
Django's `loader_tags` against Django's `loader_tags` for exactly the three tags
M7 exists to implement — four milestones of inheritance work behind a label that
claims the Rust engine ran. That is `scripts/differential.py:57-69`'s defect
("`installed()` means the finder is up, not that Rust ran") and `check.sh:63`'s
label and this plan's D4, in its fourth appearance, and it is the one class of
defect this project has paid for most.

Design B's price is one clause in `ShellHost.has_tag` (A2). Design A's price is
that the milestone cannot be measured.

**The measured size of what is at stake — and the granularity, because a number
here has already travelled wrongly.** Four counts, each with its unit and its
population named, each reproduced on the pinned tree through
`scripts/run_django_suite.sh` (2026-10-09); they are four right answers to four
questions and none of them is a correction of another:

| unit | population | count |
|---|---|---|
| test cases | the five modules that exist to test these three tags | **115** |
| test cases | recon §4.1's two groups, `80 + 96` | **176** |
| test cases | every module under `template_tests/` whose source mentions one of the tags (13 modules) | **373** |
| tag occurrences | those same 13 modules | **205** |

The five modules of the first row are `test_extends` (10),
`test_extends_relative` (15), `syntax_tests/test_extends` (46),
`syntax_tests/test_include` (37) and `syntax_tests/test_named_endblock` (7).
The "**176**" is this plan's own: recon §4.1's `test_extends` +
`test_extends_relative` + `test_loaders` + `tests` is 80, and
`syntax_tests/{test_extends,test_include,test_named_endblock,test_exceptions}`
is 96.

**Withdrawn: the "85" that stood here, and which the reviewer adopted.** It
named `template_tests.syntax_tests.{...,test_block,test_blocktrans}`, and the
pinned tree has neither module — the `{% blocktrans %}` module is
`syntax_tests/i18n/test_blocktranslate.py`. It was unmeasurable as written. The
other three reproduce exactly, and the earlier claim that "176 matches none of
them" was wrong for the reason this project keeps re-learning: the numbers
differ because the *populations* differ, not because any of them is false.

Design B is chosen. It also shrinks M6's Review Focus item 1 by one tag: after
Task 1, a `{% block %}` body is parsed by Rust, and its nested tags are Rust's.

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

`ShellHost.has_tag` (`host.py:113-131`) decides who compiles a name in three
clauses: refuse nothing, refuse `_M7_OWNED`, refuse a name Rust does not own,
then compare identity — `func is not self._django_tags.get(command)`, where
`_django_tags` is `django_own_tags()`, today `dict(defaulttags.register.tags)`.

The three M7 names are **always** in `parser.tags`, and that is the first thing
this section measures: `Engine.default_builtins` (`engine.py:14-18`) lists
`django.template.loader_tags` third, and `Parser.__init__` merges every builtin
through `add_library` (`base.py:668-670`). What they are not in is
`django_own_tags()` — a second registry — so the identity clause reads `None`
for each of them and calls `loader_tags`' callable *"not Django's"*. Measured
(`.review/m7-measurements/m7_measureA.py`); `True` means **the host compiles
it**:

```text
1  today, guard present, 22, defaulttags          block=False extends=False include=False
2  no guard, 22, defaulttags                      block=True  extends=True  include=True
3  no guard, 25, defaulttags   <-- the trap       block=True  extends=True  include=True
4  no guard, 25, +loader_tags  <-- Design B       block=False extends=False include=False
5  no guard, 25, +loader_tags, + {% load %}ed     block=True  extends=False include=False
6  guard present, 25, +loader_tags, + {% load %}  block=False extends=False include=False
```

Row 1 is measured on the shipping `ShellHost`; rows 2–6 model the predicate
with `_M7_OWNED` deleted, which is half of what Task 4 does. Everything else in
the model is read from the shipping modules (`_M7_OWNED`, `django_own_tags`,
`_native.builtin_tag_names()`, `loader_tags.register.tags`), so the predicate
change is the only modelled thing.

Row 4 is the wanted state. **Rows 2 and 3 are the same answer, and that is the
correction this matrix carries against its own earlier form: the registry count
is not the deciding column.** Deleting the guard is what flips these three to
the host, and the identity table is what flips them back — so the guard must not
go before `django_own_tags()` is widened, which is why Task 4 makes both edits
in one commit. Row 3, the half-fix named as the trap, is what a reviewer
reasonably expects from "add the names to `REGISTRY` and delete the exception
list": names in `REGISTRY`, table unwidened, and the host silently compiles a
tag M7 owns — `{% extends %}` is then accepted anywhere and `must_be_first`
never crosses.

Rows 5 and 6 are **one input under the two forms of the predicate, and they
disagree.** With the guard kept, a `{% load %}`ed library that registers `block`
loses (`False`), because `_M7_OWNED` is consulted before identity. Measured on
the shipping `ShellHost`, not modelled: `parser.tags["block"]` *is* the loaded
callable and `has_tag` still declines. That is the bug `django_own_tags`'s
docstring describes for `for` — so deleting the guard is not a simplification,
it is a fix, and "a loaded library wins" is an invariant M7 *establishes* for
these three rather than one it preserves.

So Task 4 makes **both** edits in one commit:

1. delete `_M7_OWNED` and its `has_tag` clause, and
2. widen `django_own_tags()` to
   `{**defaulttags.register.tags, **loader_tags.register.tags}`.

The `block`/`extends`/`include` entries in `REGISTRY` arrive in Tasks 1, 3 and 4
respectively, and the two edits are two different hazards, which is why the
matrix needs both rows 2 and 3:

- **Deleting the guard early** sends *any* name Rust does not yet own to the
  host, table or no table — clause 2 is `command not in self._rust_owned`, and
  it is consulted before identity. So the guard stays until all three names are
  in `REGISTRY`: the commits in between keep it, `has_tag` returns `False` for
  the three, and Rust compiles them, which is correct.
- **Widening the table late** sends the three to the host the moment the guard
  goes — that is row 3.

Both hazards are discharged by Task 4's single commit, which lands last because
the predicate change is only *safe* once all three names are owned. Until then
a name in `REGISTRY` and in neither `django_own_tags()` nor `_M7_OWNED` is
unreachable, because `_M7_OWNED` refuses the three before the ownership clause
can see them.

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

**The field is the attribute, never the object — and that distinction is the
correction this section carries.** The reviewer read `origin=None` as
manufacturing a state Django cannot reach, on the argument that `do_extends`
raises `AttributeError` where Django reads `"<unknown source>"` and parses on.
The hazard is real and the mechanism is not, and the difference changes what
Task 2 must do. Measured (`.review/m7-measurements/m7_measure_origin.py`):

```text
Origin(UNKNOWN_SOURCE).name            = '<unknown source>'
Origin(UNKNOWN_SOURCE).template_name   = None          <-- the field Rust holds
construct_relative_path(None, './x')   -> TemplateSyntaxError:
    The relative path './x' cannot be evaluated due to an unknown template origin.
construct_relative_path(None, "b.html") -> "'b.html'"   (returns before the None check)
```

`do_extends` passes `parser.origin.**template_name**`
(`loader_tags.py:305`), and for the no-origin `Origin` Django itself builds
(`base.py:151-152`) that attribute is `None` — not the string. So `None` **is**
Django's unknown-origin state, `Option<String>` is the faithful encoding of it,
and a relative `{% extends %}` under it **raises** rather than parsing on. The
reviewer's `dirname("…lstrip('/')") == ""` never runs, because the string never
reaches `construct_relative_path`.

**So `Parser::origin_template_name` stays `Option<String>`, and Task 2's port of
`construct_relative_path` must implement the `current_template_name is None`
branch with Django's message verbatim.** That branch is what `None` means here;
without it, `None` would be a silent "no check" rather than the refusal.

**Plumbing, and all three call sites are load-bearing:**

- `render.parser_for` (`render.py:127-131`) builds its `Parser` with no `origin`,
  so the **host** `Parser.origin` is `None`. Under Design B no Rust-owned tag
  reads it, but a *host-compiled* block tag's nested `parser.parse()` reaches
  Django's own `do_extends` and `extend_nodelist`, which read
  `parser.origin.template_name` — and Django never has `None` there, because
  every `Parser` Django builds itself comes from `Template.__init__`, which
  guarantees `Origin(UNKNOWN_SOURCE)` at `base.py:151-152`. So `parser_for`
  gains `origin=Origin(UNKNOWN_SOURCE)` as its default rather than `None`,
  which is one line and is `base.py:151-152`'s own value. **The Rust value is
  the same either way** — `Origin(UNKNOWN_SOURCE).template_name` is `None`, so
  the host-side object only has to be real for Django's own code to read.
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
`must_be_first = True`. Measured (`.review/m7-measurements/m7_measureB.py`), Django refuses it in a
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

- **The check straddles any host-compiled block tag, and the predicate is
  measured.** Under Design B the state is Rust's, so a `{% block %}` inside a
  *host* tag's body is compiled by Django's `do_block` — the nested
  `parser.parse()` is the handle's, so it dispatches through Django's registry —
  and accumulates in Django's `parser.__loaded_blocks`, while every
  Rust-compiled `{% block %}` accumulates in `Parser::loaded_blocks`. **No
  library tag named `block` is needed for this**, which is what this bullet
  said before the reviewer corrected it: the trigger is any `{% block %}` whose
  enclosing block-level tag Rust does not own.

  The predicate — *does any `{% block %}` sit inside a body Django parses?* —
  had not been run, and it returns **one**, not zero. Measured by walking every
  string literal under `tests/template_tests` with `ast`, tokenizing each with
  Django's own `Lexer` and tracking the open-tag stack
  (`.review/m7-measurements/m7_measure_nesting.py`; 1046 literals, 1046 tokenized), plus the 62
  fixture templates under `templates/`: the only hit in either is
  `syntax_tests/i18n/test_blocktranslate.py`'s `test_with_block` —

  ```text
  {% load i18n %}{% blocktranslate %}{% block b %} {% endblock %}{% endblocktranslate %}
  ```

  — which expects a `TemplateSyntaxError` from `blocktranslate` itself
  (`"'%s' doesn't allow other block tags (seen 'block b') inside it"`). **So the
  divergence is reached and is harmless there**, for a reason that is not the
  one first written: the host raises before any duplicate-name check runs, and
  the one `{% block %}` it sees is not a duplicate. The fixture scan's single
  hit is a scanner artifact (`{% endbadtag %}` is not a known end tag), not a
  second case.

  **The divergence is a missing refusal, and the refusal is load-bearing — but
  the thing it protects is a *render*, so "one message wide" understates it.**
  `ExtendsNode.__init__` builds `self.blocks` as a dict comprehension keyed by
  name, `{n.name: n for n in nodelist.get_nodes_by_type(BlockNode)}` (`LT:94`),
  over *materialised* nodes, which both parsers produce. A dict keyed by name
  keeps the **last** insertion, so where two blocks share a name the later one
  silently wins. In stock Django that pairing is unreachable: `do_block` raises
  `'block' tag with name '…' appears more than once` (`LT:227-234`) before the
  template ever renders, so the dict never sees a duplicate. Under the split a
  duplicate that straddles the two counters is **not** refused — each counter
  sees only its own half — so both nodes reach `self.blocks` and the **later one
  renders**. The observable difference in that one template is therefore not a
  missing message but a different `{% block %}` body: stock raises, ours renders
  the wrong half. Saying what does **not** diverge is what makes a recorded
  divergence usable, and the part that does not is every template without a
  straddling duplicate — there the two counters agree and the trees are
  identical. The earlier wording, "the split can change nothing about the
  rendered tree", read the unreachability of the duplicate as though it followed
  from the dict being shared; it is the refusal, not the dict, that keeps stock
  Django out of the case.

  **"Both nodes reach `self.blocks`" carries a reachability condition, and it is
  not the one a reader will assume.** `self.blocks` is built by **Django's** walk
  over *materialised* nodes, and Django's walk iterates `child_nodelists` — so a
  `{% block %}` inside a body that traversal does not descend into is not in the
  dict either, whatever the counters say. `simple_block_tag` is exactly that
  shape: `SimpleBlockNode` stores `self.nodelist` (`library.py:337`) and inherits
  `child_nodelists = ()` from `SimpleNode` (`:317`), so a block inside
  `{% div %}` — the example a reader reaches for first — **cannot** produce this.
  The scenario needs a host block tag whose node class *declares* its nodelist as
  a child, which is the ordinary shape for a block tag written by hand rather
  than through `simple_block_tag`. Reachable in principle through the public API;
  not reached by anything in the pinned suite. Without this condition the next
  reader builds the case with `{% div %}`, finds no divergence, and concludes the
  recorded one is imaginary. Measured, both halves:
  `.review/m7-measurements/m7_measure_child_nodelists.py` for the class shape, and
  a `{% wrap %}x{% endwrap %}` parse for the descriptor — `('HostNode', (('node',
  NodeRef 0),), (), …)`, the nodelist tuple empty.
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

Ten, each with the artifact it corrects and the measurement behind it. The first
two correct `.review/m7-recon.md` (the recon precedes and is corrected by the
plan, as M6's did); D3–D6 correct derived artifacts, and D5/D6 came out of the
reviewer's round on this plan; **D7 was written at Task 3's execution**, **D8 at
Task 4's**, **D9 at Task 5's**, and **D10 at Task 7's**, correcting this plan's
own tasks — the place
a plan's errors cost a run rather than a line of prose. D8 carries a **D8′**, its
own re-measurement after the reviewer showed its first number came from a
harness artifact; that is the shape a correction should be able to take.

**D1 — the recon's §2 frames `must_be_first` on the outbound wire. It crosses
inbound.** The 5-tuple `node_desc_to_py` emits (`lib.rs:871-915`, pinned by
`tests/test_ref_table.py:173-181`) is a *descriptor-to-shell* wire; the flag
that is missing is read off the *Python node the host returned*, before
`node.unbind()`. See A4; the measurement is `.review/m7-measurements/m7_measureB.py`. This plan does
not touch the 5-tuple.

**D2 — the recon's §6 items 2 and 6 contradict each other.** Item 2 says "carry
`must_be_first` with the node"; item 6 says "write `do_block`/`do_include`/
`construct_relative_path`". Carrying the flag is Design A's prerequisite;
writing the compile functions is Design B's. This plan resolves it in favour of
**Design B** (A1) and keeps item 2's flag carry as a *separate* task (Task 5),
because it is a real M6 defect independent of which design wins: a library tag
returning a `must_be_first` node is accepted today, in a position Django
refuses.

**D3 — the spec's §10.4 cited a line number for `extend_nodelist` that pointed
at the wrong thing.** A vintage, not a claim. **Fixed rather than only recorded**,
in `7ac772e`: the spec is a living document under the anchor guard now, so a
number left as a vintage would be a number the guard re-checks forever. Found by
reading the 29 unmanifested spec anchors against the pinned tree one at a time —
the widening's first catch, and the second was a line twelve short of
`_resolve_lookup`'s end.

**D3′ — and this entry's own replacement number was itself the defect, so it no
longer carries one.** `7ac772e` wrote the check's line into the spec; Task 1
inserted `loaded_blocks` above it and the line moved three more times. The spec
and the manifest tracked it in step and **nothing was ever red** — the tracked
citation followed the line through all five commits — while this entry stayed at
the first value, because the plan is not scanned and a written number reads as
settled. Measured before deleting the number: spec and manifest agreed at every
commit; D3 and the recon's §6 did not.

**The remedy is the location, not the diligence, and it is the one this project
already applied to the checker's own header.** A correction in an unscanned
document has exactly the lifetime of the error it replaced — and is worse,
because it carries the authority of having been checked once. So:

> **D3's subject: `extend_nodelist`'s must-be-first check, and the spec's
> citation of it. What was wrong: a line number pointing elsewhere. Where the
> truth lives: the `scripts/anchors.txt` row for that check** — the one
> `--check` guards and `--update` moves. **No replacement number is recorded
> here.**

A plan is two documents glued together: the task steps are a dated record of
intent, and the D-sections are live claims about the current tree. The exclusion
in `NOT_SCANNED_BECAUSE` treats both halves as dated, and only the first half is.
Taking the number out is what lets a D-section say the thing a dated document can
say truthfully forever: that a mistake was made, and where the truth now lives.
**Ten D-sections across M6 and M7 are ten frozen numbers today**, and Step 2's
work is to move the live claims into the spec, which `--check` does scan, rather
than to keep them current here.

**D4 — the spec's §10.3 M7 row names `test_include_cache` as the gate, and as
written that gate is vacuous.** The `.django/` row runs on stock Django's parser
(the shim does not yet interpose `loader_tags`), so it passes regardless of M7.
The obligation is discharged in `tests/` against our engine (A6); the spec's
claim about *what* the row proves is kept, and the instrument that can see it is
named.

**D5 — D4 is one instance of a class, and the class is swept, not fixed one at
a time.** The reviewer's reading: §10.3 carries a gate per milestone, most
naming `.django/tests/` modules, and the mechanical question is *"is the module
whose behaviour this gate exercises interposed by this milestone?"* Swept, one
pass over §10.3 against the interposition schedule it states:

| Milestone | gate names | interposes at | exercised module interposed at | vacuous? |
| --- | --- | --- | --- | --- |
| M0–M6, M8 | `tests/` corpora, the differential harness, `.review/mN-seams.md` — **no `.django` module** | — | n/a | no |
| **M7** | `test_include_cache` (`syntax_tests/test_include.py`) | none | **M11** (`loader_tags`) | **yes** |
| M9 | `test_base`, `test_parser`, `test_nodelist`, `test_smartif` | `base`, `smartif` | M9 | no |
| M10 | `test_context`, `test_engine`, `test_loaders`, `test_origin` | `context`, `engine`, `utils`, `loaders`, `origin` | M10 | no |
| M11 | `syntax_tests`, `filter_tests`, `test_url` | `defaulttags`, `defaultfilters`, `loader_tags` | M11 | no |
| M12 | `syntax_tests/i18n/*`, `test_cache` | `i18n`, `l10n`, `static`, `tz`, `cache` | M12 | no |
| M13 | `test_partials`, `test_response` | partials, `library`, `backends`, `response` | M13 | no |
| M14 | `unshimmed()` empty | all | M14 | no |

**The sweep comes back with exactly one, and the rule that finds it is
derivable**: a milestone's gate is vacuous iff it names a module a **later**
milestone interposes. Every M9–M13 gate names modules its own milestone
interposes (or an earlier one); M0–M6 and M8 name no `.django` module at all;
M7 is the only row whose gate reaches forward. That is why it is one row and not
a list — and why it should be re-run, not re-read, when §10.3 changes.

**D6 — the anchor checker's scope excludes the spec by omission, not by rule.**
`scripts/check_python_anchors.py:240-266` names `TREE_ROOTS = ["crates",
"python", "tests"]` and explains, in `NOT_SCANNED_BECAUSE`, why
`docs/superpowers/plans/` and `.review/` are out — both reasons being that those
files are *dated records*, and that holding them current would destroy the
distinction. **`docs/superpowers/specs/` is in neither**: the spec is a living
document, it is §2's acceptance reference, and its anchors are not one of the two
excluded classes. Measured with the checker's own pattern: the spec carries
**36** anchors; `docs/superpowers/plans/` carries **331** and `.review/` **463**,
all unchecked. So `parser/mod.rs:336` (D3) is not an unlucky number — it is one
of a class the guard's own comment says should not exist by accident.

This plan does not widen `TREE_ROOTS`: it is a harness change, it needs the 36
rows verified one at a time rather than `--update`d (which "moves a number only
when the snippet occurs nowhere in here" — the report's own warning that
`--update` is the wrong direction for a wrong citation), and it belongs in its
own commit with its own review. It is scheduled as the commit immediately after
this plan revision and is recorded here so it is not rediscovered at M8.

**D7 — Task 3's draft carried five statements that are false on the pin, and
three of them are the kind a test would have caught.** Corrected in Task 3's own
steps, and recorded here because each is a fact about Django rather than a
typo — the next task that touches `{% extends %}` should not re-derive them:

| The draft said | The pin says | How it showed |
| --- | --- | --- |
| `{% if x %}{% extends "a" %}{% endif %}` compiles, so it is the walk's test case | **raises** `Invalid block tag on line 1: 'endif'` — `do_extends`' unbounded parse eats the enclosing end tag | a run, in both engines |
| `IfNode.child_nodelists` is `()`, so the branch lists are unreachable by name | it is `('nodelist',)` — inherited — and `nodelist` is a **property** over `conditions_nodelists` (`defaulttags.py:314-320`), so there is no attribute to name at all | read at the pin |
| `parent_name` is an `AttrValue::Ref` | it is an `AttrValue::FilterExpr` — `compile_filter`'s output | `no field is_var on &Box<FilterExprDesc>` |
| `ErrorKind::Syntax` compares as a unit variant | it is a **tuple variant carrying the message** | it did not compile |
| a lone `{% extends "a" %}` is `ErrorKind::Unimplemented` | it **compiles** — the assertion is that it does | it did not compile |

The last two are the pair worth keeping: both were written as *assertions*, and
both were wrong in the direction that makes a milestone look unfinished when it
is done. A claim in a test fails at the next run; the same claim in prose beside
a skipped test is the one that survives.

Two corrections that are ours rather than Django's: `do_extends` is at
`loader_tags.py:292` (`:291` is its `@register.tag("extends")` decorator, and the
tool's rule is that the number holds the text the sentence names), and
`nodelist = parser.parse()` is at `:307`, not `:309` — the draft's number was
two lines late, at the `raise` that follows the check. `:309` had been copied
into three files; the anchor guard is what found it, because a snippet-keyed row
cannot stay green on a number that points at the wrong line.

**D7 also closes a class the plan had carried unexecuted since Task 2**:
`assert_eq!(kind, ErrorKind::Syntax)` appears seven times across this plan's
Task 2 and Task 4 blocks, and `ErrorKind::Syntax` is a **tuple variant**
(`parser/error.rs:109`) — it does not compile. Task 2's three were corrected in
the code at execution and the plan was not updated with them; Task 4's four were
still waiting to be hit. All seven now read `matches!(kind,
ErrorKind::Syntax(_))`. This is the D7 pattern a second time: a plan's assertion
is only as good as a run, and a plan step nobody has executed is prose wearing a
checkbox.

**D7's third and fourth instances were found by the anchor guard, not by a
run, and both are wrong *claims about Django* rather than wrong numbers** — the
class a test cannot catch, because the test asserts the engine's behaviour and
the engine was built from the claim:

| The claim | The pin says | Where it was |
| --- | --- | --- |
| `ExtendsNode.get_parent` calls `self.parent_name.resolve(context, ignore_failures=True)` | `parent = self.parent_name.resolve(context)` (`loader_tags.py:118`) — **there is no `ignore_failures` in `loader_tags.py` at all**; the seven call sites of that argument in the pinned tree are all in `defaulttags.py` | `extends.rs`'s seam comment |
| `extend_nodelist`'s `contains_nontext` check is at `base.py:510` | it is at `base.py:602-612`; `:510` is a comment about custom tag data stored on the parser — an unrelated paragraph that happens to be 100 lines earlier | `parser_corpus.py`'s `x{% extends %}` row |

Both were seeded as *unquoted* manifest rows, which is the mechanism: an
unquoted row checks only that the line exists, and both lines exist and are
unrelated. Authoring the snippet — the tool's own rule, "the snippet be authored
from the sentence's own claim in the same edit that names the file" — is what
made the numbers' wrongness visible, and it is why this commit quotes every row
it adds rather than leaving them as line-existence checks. **The lesson
generalises past this task: a new anchor added unquoted is a claim nobody
re-measures, and the seven rows this task would have added that way included two
that were false.**

**D8 — Task 4's Step 8 predicts the half-fix's second symptom wrongly, and the
correction's own measurement came from a different file.** The plan said
reverting `django_own_tags` to `dict(_defaulttags.register.tags)` leaves
`{% extends "a" %}{% extends "b" %}` *not raising* (Django's duplicate scan
skipped because Rust, not Django, compiles the tag). It still raises, with
Django's own text.

The first execution of Step 8 recorded a *different* symptom — seven corpus rows
dying with `AttributeError: 'NoneType' object has no attribute 'template_name'` —
and that symptom was an artifact of the harness, not a property of the half-fix:
`tests/test_parser_errors.py::ours_failure` built its `Parser` without the
`Origin` its `django_failure` sibling passes, so `parser.origin` was `None` where
Django's was an `Origin`, and the misrouted `{% extends %}` reached
`parser.origin.template_name` on the `None` one. **D8′ is the re-measurement
after that one argument was added**, and it dissolves the recorded symptom
entirely: 0 of 106 corpus rows differ, 0 of 3 `ORIGIN_ERRORS` rows differ, and
the whole suite's only failures are the three subTests of the predicate row. The
half-fix's only detector at the time was the predicate itself; a wire-shape row
asking the same question structurally was added later (Task 4 Step 8, below) and
sees the half-fix too.

Two things are worth keeping from this, and neither is the predicted row.
**First: a routing change under a seam that re-enters Django's own parse is
invisible to every message-comparing corpus.** `do_extends` calls `parser.parse()`
(`loader_tags.py:307`) before its duplicate scan (`:308-311`), so a misrouted
`{% extends %}` runs the same code down a different path and reaches the same
rule — the routing does not skip the check, it re-enters the parser that makes
it. **Second: the harness defect is the better finding**, and it is M6's
`parser_for` shape repeating — a `Parser` missing one constructor argument,
invisible until a host-compiled tag reads what that argument supplies, and
discovered through a tag that had nothing to do with it. The cost is latent and
the corpus is green: the row that first reaches it — any host tag reading
`parser.origin`, which M13's `{% partialdef %}` work could introduce — would
diverge for a reason having nothing to do with the tag under test.

The plan's prediction was checkable and wrong in the *interesting* way: it was
one line above the check it meant to exercise, and so was the correction.

**D9 — Task 5's own three false measurements, and a guard whose named mutation
cannot fail.** The task's "declared environment" block carried two rows that are
false on the pin and the mechanism to explain them away with:

```text
{% load mylib %}{% first_tag %}                     -> OK  'F'                  # false
{% load mylib %}{% plain_tag %}text{% plain_tag %}  -> {% plain_tag %} must …   # false
```

`extend_nodelist` sets `nodelist.contains_nontext = True` the first time a
non-`TextNode` is appended and never clears it (`base.py:611-612`, class default
`:1088`), and `{% load %}` appends a `LoadNode` — so a *loaded* library's
`must_be_first` tag is **never** accepted in first position, on either engine,
and the accepted case needs a builtin registration instead. `{% plain_tag %}`
sets no flag and its bare `Node.render` returns `None`, which is the `TypeError`
the draft read as an ordering message. The draft's test could not pass either: it
caught Django's `TemplateSyntaxError` for **our** side, where design decision 2
makes `_native.ParseError` correct until M9's conversion. And Step 4's named
mutation — `.map_err(...)?` → `.unwrap_or(false)` — is a **no-op**:
`unwrap_or` returns the `Ok` value, so a node carrying the attribute answers the
same either way, and the four rows stayed green under it. The two spellings
differ only where the attribute is absent, so a third row (a tag returning
`object()`) is what makes the guard observable, and the mutant's symptom surfaces
in the materialiser — `AttributeError: 'object' object has no attribute 'token'`
from `_impl/parser.py:271` — a file the bad tag never touched.

The shape is D7's, and it is why this entry is here rather than in a comment: **a
measurement block written into a plan is a claim nobody re-runs until the task
runs it, and two of the three rows were authored rather than executed.** The
guard is the second half and the sharper one — a mutation whose stated symptom is
in another file measures nothing, and a plan that names it is asking the executor
to confirm a prediction rather than to observe a difference.

The two other measured facts are the asymmetry Step 6 turns on, and it **inverts**
under the half-fix: `test_the_host_declines_every_name_rust_owns` fails
(`has_tag` says `True` for all three) while
`test_a_loaded_library_still_shadows_a_built_in` *passes*, because the half-fix
is exactly the pre-M7 state for the second test. That is the reason Step 6 runs
the pair in the order it does: neither test alone distinguishes "the list is
gone" from "the table was widened", and the pair does.

**D10 — this plan's target counts, re-derived at close-out; two were right, one
was a right answer to a different question.** Step 3's subject. The registry's
`22 → 23 → 24 → 25` is **correct, and it happened per commit** — the plan's own
rule kept, not deferred to the end:

```text
commit    task    registry literal   absent-count doc   registry test name   PYTHON_OWNED
1b131a0   1       23                 "Four…"            the_four_…           4 members
156a24a   3       24                 "Three…"           the_three_…          3 members
b8796e2   4       25                 "Two…"             the_two_…            2 members
```

Measured with `git show <commit>:<path> | grep` over the three paths. The corpus
deltas, measured the same way and counted by `ast` over the `TEMPLATES` /
`ERRORS` / `ORIGIN_ERRORS` list literals:

```text
                          pre-M7   Task 1   Task 3   Task 4
parser_corpus.TEMPLATES      152      156      162      166
parser_error_corpus.ERRORS    91       95      100      106
ORIGIN_ERRORS                  —        —        2        3
```

**The sentence at `:219-221` that predicts this shape is wrong in one clause.**
It says the "deliberately absent" count "becomes two, then one, then none";
measured, it is **four, then three, then two**, and the test it names,
`the_five_python_owned_builtins_are_not_rust_compile_functions`, was the *pre-M7*
name and was renamed in step at each of the three commits above. The likely
reading is that "two, then one, then none" is `loader_tags`' own three names
retiring (3 → 2 → 1 → 0), written into the sentence about the absent-count — the
shape this plan has caught four times, a right answer to a different question.
As written it is false of the thing it is attached to, so it is corrected here
rather than left to read as a live claim.

**Where the truth lives, because a dated document can only say that much:**
`tests/test_tag_inventory.py:270` and `:277` hold `PYTHON_OWNED` and the literal,
and `crates/template-core/src/parser/registry.rs:11` and `:324` hold the
absent-count sentence and the test name — all four inside `TREE_ROOTS`, so the
*citations* are `--check`-guarded and the *counts* are asserted
(`test_the_rust_registry_is_djangos_builtins_minus_the_python_owned_two` fails if
the table shrinks without the literal). The corpus sizes are the exception: the
only assertion on them is a floor, `test_parser_parity.py:442`'s
`assertGreaterEqual(len(TEMPLATES), 40)`, so they are written out once here and
re-derivable only by the `ast` command above. Task 1's
`m7_measure_gettextlist.py` note at `:1029` — "of the 95 rows" — is the same
species: right for the commit that wrote it, and superseded the moment the file
grew, which is why the script now prints its population from the live file.

**One command, in two plans, that could not run.** D7 found that this project has
no `pytest`; the M5 plan's Task 6 Step 2 kept the draft's `pytest tests/test_ref_table.py
-v` as its alternative invocation. Measured: `.venv/bin/python -m pytest` raises
`ModuleNotFoundError: No module named 'pytest'`, while `tests/__init__.py` exists
so `.venv/bin/python -m unittest tests.test_ref_table -v` runs the same file (17
tests). Corrected in the M5 plan too, in this pass, because a dated plan that
ships an unrunnable command is the defect D7 named — cheaper to fix than to
explain three milestones later.

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
    use crate::lexer::Lexer;
    use crate::parser::host::NoHost;
    use crate::parser::tags::support::{compile, failure};
    use crate::parser::Parser;

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

    #[test]
    fn the_rejected_occurrence_is_not_recorded() {
        // The observable form of "raise before append" (`loader_tags.py:227-234`).
        // The message cannot carry it — `"'%s' tag with name '%s' appears more
        // than once"` names no position, and the parse ends at the offending
        // tag, so there is no later `{% block %}` for a wrong ordering to
        // misattribute. `loaded_blocks` is where the ordering shows.
        let mut parser = Parser::new(
            Lexer::new("{% block a %}{% endblock %}{% block a %}{% endblock %}").tokenize(),
            &NoHost,
        );
        parser.parse(&[]).expect_err("the second {% block a %} raises");
        assert_eq!(parser.loaded_blocks(), ["a"]);
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
//! (`loader_tags.py:219-220`) says the tag "doesn't accept variable as
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
        // `bits.len() != 2` admits 0, and Django's `bits[0]` would raise
        // `IndexError` there (`loader_tags.py:222-223`) — but `bits[0]` in Rust
        // is a *panic*, and a panic crosses the FFI as a process-level event
        // rather than an exception. Unreachable: `Parser::parse` splits the same
        // contents and raises `Empty block tag on line %d` first
        // (`base.py:560-563`), so nothing with zero bits reaches a compile
        // function. Stated rather than assumed, in the idiom
        // `NoHost::call_tag`'s `unreachable!` already uses.
        let command = bits
            .first()
            .expect("parse rejects an empty block tag before a compile function runs");
        return Err(Error::syntax(format!(
            "'{}' tag takes only one argument",
            command
        )));
    }
    let block_name = bits[1].to_string();

    // Django raises **before** appending (`loader_tags.py:227-234`), so the
    // occurrence that raises is never recorded and `loaded_blocks` holds only
    // names whose block compiled.
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
USE_TZ=False)` + `django.setup()`). Settings are needed because the
`{% endblock b %}` row is the first corpus row whose `parse_until` has two
elements, and only `get_text_list`'s two-element branch evaluates `_(", ")` —
eagerly, so the translation layer is reached even though the separator is
discarded (`utils/text.py:297-303`). It is *not* `last_word`'s lazy default:
`invalid_block_tag` passes `"or"` explicitly. Measured per row in
`.review/m7-measurements/m7_measure_gettextlist.py` — of the 95 rows, exactly
that one raises `ImproperlyConfigured` without settings:

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

Run: `source scripts/env.sh && cargo test -p template-core && cargo clippy --workspace --all-targets -- -D warnings && cargo fmt --all --check && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'`
Expected: all pass; `tests/test_tag_inventory.py` reports 23 names; the corpus
counts rise by 4 success rows and 4 error rows.

Observe each new guard failing for its intended reason before keeping it.

*Mutation A — delete the `bits.len() != 2` arm.* Measured: three tests go red, and
the wrong thing is a **panic**, not a wrong message —
`index out of bounds: the len is 1 but the index is 1` at `bits[1]`, because
`{% block %}` splits to one bit. The failing three are
`a_block_needs_exactly_one_argument` plus the registry's two reachability tests,
which compile each entry against its own bare `{% name %}` token. A panic is what
the arm exists to prevent, so the arm is load-bearing and its message is the
observable.

*Mutation B — the ordering.* The plan's first phrasing of this ("move the
`register_block_name` call above the duplicate check") is **too coarse to
demonstrate the claim**, and the measurement is worth keeping for that reason:
with the call moved above the whole `if`, the first occurrence sees its own
registration and *six* of the eight block tests go red for the same unrelated
reason — while `the_rejected_occurrence_is_not_recorded` still **passes**, since
`loaded_blocks` is `["a"]` either way (the parse dies at the first block).
The mutation that actually isolates the ordering registers inside the branch,
immediately before the raise:

```rust
    if parser.loaded_blocks().contains(&block_name) {
        parser.register_block_name(block_name.clone());   // <- Django appends after
        return Err(Error::syntax(format!(
```

Measured: exactly one test red — `the_rejected_occurrence_is_not_recorded` —
with `left: ["a", "a"]`, `right: ["a"]`. So the ordering is pinned by that test
and by nothing else, **and the corpus cannot see it**: the duplicate's message
names no position, and the parse ends at the offending tag, so no later
`{% block %}` exists for a wrong ordering to misattribute. That is why it is a
unit test on the field rather than an error-corpus row.

- [ ] **Step 10: Commit**

```bash
git add crates/template-core/src/parser/tags/block.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/registry.rs \
        crates/template-core/src/parser/mod.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/test_tag_inventory.py tests/parser_corpus.py \
        tests/parser_error_corpus.py tests/test_parser_errors.py \
        docs/superpowers/plans/2026-10-09-m7-inheritance.md
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

**Declared environment:** `render.parser_for`'s new `origin` keyword defaults to
`UNKNOWN_ORIGIN` — an `Origin`, not `None`, for A3's reason — so every existing
caller (the parity corpus in `tests/`, `test_parser_errors.py`) keeps compiling
unchanged and the host `Parser` carries a real `Origin` for Django's own nested
parses. `_native.parse`'s keyword rename is **not** backward-compatible, and a
grep for `origin_template_repr` over `crates/ python/ tests/ scripts/` must come
back empty before the commit. (No call site in `tests/` passed the old keyword:
the grep is empty before the rename as well as after it — measured, not assumed,
and the plan's earlier claim that two sites needed updating was wrong.)

- [ ] **Step 1: Write the failing `pyposix` test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normpath_matches_python_on_the_measured_table() {
        // Measured against `posixpath.normpath` on the pinned interpreter
        // (3.14.4, `posixpath.py` under mise's install); the table is the
        // plan's evidence, not a guess.
        //
        // `("///a//b", "/a/b")` is here for one clause and it is a review
        // finding: without it nothing exercises `initial_slashes`'s `else 1`
        // arm from a path that *starts* with `///`, and the `!path
        // .starts_with("///")` conjunct in the `== 2` arm is dead code as far
        // as this table can tell. Deleting that conjunct changes this row to
        // `"//a/b"` and changes nothing else.
        for (input, want) in [
            ("", "."),
            (".", "."),
            ("..", ".."),
            ("a/..", "."),
            ("a/../..", ".."),
            ("./a", "a"),
            ("a//b", "a/b"),
            ("//a//b", "//a/b"),
            ("///a//b", "/a/b"),
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
        // `("//a", "//")` and `("///a", "///")` are the second half of the same
        // review finding. `("/a", "/")` already reaches the `"/".repeat(n)`
        // guard, but only at n == 1, where `head != "/"` and
        // `head != "/".repeat(head.len())` are the same test; the n > 1 rows
        // are what separate the length-sized repeat from a hard-coded `"//"`,
        // which `("//a", "//")` alone would let through.
        for (p, want) in [
            ("", ""),
            ("a", ""),
            ("a/b", "a"),
            ("a/b/", "a/b"),
            ("/a", "/"),
            ("//a", "//"),
            ("///a", "///"),
            ("sub/index.html", "sub"),
            ("/sub/index.html", "/sub"),
        ] {
            assert_eq!(dirname(p), want, "dirname({p:?})");
        }
    }
}
```

**The table is a subset of the evidence, and the rest was measured.** The port
in Step 3 was transliterated to Python and diffed against `posixpath` on
221,845 generated inputs (every string over `/ . a b` up to length 7, plus
200,000 random ones of length 8–14) for `normpath` and `dirname` — 0 mismatches
— and on 800,000 `normpath(join(a, b))` pairs — 0 mismatches. The two rows this
plan's review added are in that corpus by construction, but the point of running
it is the rows nobody proposed: the table is what a reader can check by eye, and
the sweep is what says the table is not the whole story. Transliteration rather
than the Rust itself is a real limit and is recorded as one — it cannot see a
byte-versus-char slicing defect — so Task 2's own gate is `cargo test`, and the
sweep above is not a substitute for it.

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
that row; the code below is the version that passes all thirteen rows of the
`normpath` table and all nine of the `dirname` one.

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
/// The `"/".repeat(head.len())` comparison is a run of *slashes*, not one slash
/// or two: `dirname("//a")` is `"//"` and `dirname("///a")` is `"///"`. Both
/// rows are in the table, and the second is the one that fails a version
/// written against a hard-coded `"//"`.
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
    // else 1 — so `///x` is 1, not 3, and not 2. The count is what keeps `//a`
    // distinct from `/a`, and the `else` arm is pinned by the table's
    // `///a//b` row.
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

    /// `None` is Django's *unknown origin*, not "no origin object" (A3): the
    /// value it stands for is `Origin(UNKNOWN_SOURCE).template_name`, which is
    /// `None` on every `Template` Django builds without a loader. So the
    /// message is asserted, not just the kind — a `None` that fell through to
    /// "no check" would still be an `ErrorKind::Syntax` only by accident, and
    /// this is the branch that makes `None` mean something.
    #[test]
    fn the_three_failures_carry_djangos_exact_text() {
        let unknown = construct_relative_path(None, "'./base.html'", false).unwrap_err();
        assert!(matches!(unknown.kind, ErrorKind::Syntax(_)));
        // Verbatim (`loader_tags.py:266-269`); note it does **not** quote
        // `relative_name`, unlike the other two. The literal is long on purpose:
        // rustfmt does not wrap it, and a wrapped literal would hide the space
        // this message's line break sits on.
        assert_eq!(
            unknown.message(),
            "The relative path './base.html' cannot be evaluated due to an unknown template origin."
        );

        let outside = construct_relative_path(Some("index.html"), "'../../x.html'", false)
            .unwrap_err();
        assert!(matches!(outside.kind, ErrorKind::Syntax(_)));
        assert_eq!(
            outside.message(),
            "The relative path ''../../x.html'' points outside the file hierarchy that \
             template 'index.html' is in."
        );

        let same = construct_relative_path(Some("index.html"), "'index.html'", false);
        assert!(same.is_ok(), "not relative: returns early, no recursion check");

        let same = construct_relative_path(Some("index.html"), "'./index.html'", false)
            .unwrap_err();
        assert!(matches!(same.kind, ErrorKind::Syntax(_)));
        assert_eq!(
            same.message(),
            "The relative path ''./index.html'' was translated to template name \
             'index.html', the same template in which the tag appears."
        );
        // The origin with a leading slash, which is the input that separates
        // `current` from `new_name` in that message: `stripped` is
        // `index.html` and so is `new_name`, but Django prints the *origin*.
        // A test that only used slash-free origins would pass with either.
        // Measured on the pin.
        let same = construct_relative_path(Some("/index.html"), "'./index.html'", false)
            .unwrap_err();
        assert_eq!(
            same.message(),
            "The relative path ''./index.html'' was translated to template name \
             '/index.html', the same template in which the tag appears."
        );
        assert_eq!(
            construct_relative_path(Some("index.html"), "'./index.html'", true).unwrap(),
            "\"index.html\"",
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
        // `current`, **not** `new_name`: the second `%s` in Django's message is
        // `current_template_name` (`loader_tags.py:281-283`), and the two differ
        // whenever the origin had a leading slash — measured on the pin,
        // `construct_relative_path("/index.html", "'./index.html'", False)`
        // says "template name '/index.html'" while the new name is
        // `index.html`. That the two coincide for every slash-free origin is
        // what makes this the branch a same-shape test misses.
        return Err(Error::syntax(format!(
            "The relative path '{relative_name}' was translated to template name \
             '{current}', the same template in which the tag appears."
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

In `render.py`: `parser_for(source, engine=None, origin=UNKNOWN_ORIGIN)`, where
`UNKNOWN_ORIGIN = Origin(UNKNOWN_SOURCE)` is a module constant — **not `None`**,
per A3: the host `Parser` Django's own code may read must carry a real `Origin`,
and `Origin(UNKNOWN_SOURCE).template_name` is `None`, so the Rust side is
unchanged. Then `parse_template(source, engine=None, origin=UNKNOWN_ORIGIN)`
forwarding it both to `parser_for` and to `compile_template`'s new keyword, and
`render_string(source, context, engine=None, origin=UNKNOWN_ORIGIN)`
forwarding it. **`compile_template`'s materialisation origin is the same
object**, so `ExtendsNode.render`'s `[self.origin]` and `IncludeNode.render`'s
`self.origin.template_name` both read a real `Origin`.

`origin` reaches Rust as `origin.template_name`, i.e. `origin.template_name if
origin is not None else None` — the *attribute*, never the object (A3).

**`template_for` gains the same `origin`, and leaving it at `None` is the
silent half-fix this step exists to prevent.** Django sets `node.origin` in
`extend_nodelist` (`base.py:616`) *and* `Template.origin` in `__init__`
(`base.py:154`), and `Node.render_annotated` decides whether to attach the debug
traceback by comparing them:

```python
if (not hasattr(e, "template_debug")
    and context.render_context.template.origin == e._culprit_node.origin):
```

so a tree whose nodes carry a real origin under a `Template` that carries `None`
stops annotating `engine.debug` templates. Both were `None` before this task
(equal, and the comparison held trivially); threading one and not the other
would break it where nothing in the M7 corpora looks. Measured on the pin:
`Origin(UNKNOWN_SOURCE) == None` is `False` (`Origin.__eq__` returns `False` for
anything that is not an `Origin`, `base.py:125-129`), while two
`Origin(UNKNOWN_SOURCE)` values are equal — so threading the materialiser's
origin and leaving `template.origin` at `None` silently stops the annotation.

**And the must-be-first message is Django's *truthiness* test, not `is None`.**
`extend_nodelist` reads `if self.origin.template_name:` (`base.py:603`), so an
origin whose name is the **empty string** says "the template" too — while
`Option<String>` distinguishes `Some("")` from `None` and would answer `''`.
The Rust arm is therefore `Some(name) if !name.is_empty()`, and the row that
separates the two encodings is
`parser::tags::tests::an_empty_origin_name_says_the_template_too` (observed
failing with `left: … must be the first tag in ''.` when the conjunct is
removed). The `None` row cannot separate them: both encodings agree there.

- [ ] **Step 8: Run the guards, then the gates**

Run: `source scripts/env.sh && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings && grep -rn origin_template_repr crates/ python/ tests/ scripts/ ; maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'`

Expected: all pass; the `grep` prints nothing (exit 1, which is the wanted
result). No corpus row exercises an origin yet — Task 3's rows are the first —
so the rename's only visible effect here is that everything still passes.

Observe the guard: revert `extend_nodelist` to a hard-coded `"the template"` and
confirm Task 1's error-corpus rows still pass but the *stand-in* must-be-first row
in `tests/parser_error_corpus.py` (which passes an origin) fails. If no row
notices, one is missing and this task adds it.

**Measured, run as written:** with `extend_nodelist`'s arm replaced by
`let origin = "the template".to_string();`, `cargo test -p template-core` reddens
exactly one test — `parser::tags::tests::the_innermost_token_wins_across_a_nested_parse`
— and `tests.test_parser_errors` stays `OK` (14 tests). The new
`an_empty_origin_name_says_the_template_too` row does **not** redden under that
mutation, because the hard-coded string is what it expects; it reddens under the
*conjunct's* removal instead, which is the other half of the same guard.

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

**Corrected at execution time (D7): the source below is `{% block %}`, not
`{% extends %}`.** The draft wrapped `{% extends "a" %}` in an `{% if %}` and
that raises — on the pin, `Invalid block tag on line 1: 'endif'` — because
`do_extends` ends with an unbounded `parser.parse()` and eats the enclosing end
tag. The branch list is what this test is for and `{% block %}` reaches it
without that accident. The draft's *reason* was wrong too: `IfNode` does not
have an empty `child_nodelists`. It inherits `("nodelist",)`, and `nodelist` is
a **property** over `conditions_nodelists` (`defaulttags.py:314-320`) — so what
a name-driven Rust walk misses is not an unnamed attribute but a
`child_nodelists` entry with no stored attribute behind it at all. See D7.

```rust
#[test]
fn get_nodes_by_type_reaches_a_node_inside_an_if_branch() {
    // The walk, through the one node shape that would break a name-driven
    // traversal: `IfNode` emits its branch lists as empty-named nodelists, and
    // Django reaches the same nodes through `nodelist`, a property over
    // `conditions_nodelists` (`defaulttags.py:314-320`).
    let tree = nodelist("{% if x %}{% block a %}{% endblock %}{% endif %}");
    assert_eq!(tree.get_nodes_by_type("BlockNode").len(), 1);
    assert!(tree.get_nodes_by_type("ExtendsNode").is_empty());
    assert_eq!(tree.get_nodes_by_type("IfNode").len(), 1);
}
```

`compile` asserts a single top-level node, and this source has one — but
`extends_must_be_the_first_non_text_node` (Step 4) needs the whole nodelist, so
Step 3's helper pair in `tags/mod.rs` is `support::nodelist`, not only
`support::compile`:

```rust
    /// Parse `source` with no host, and return the whole top-level nodelist.
    pub(crate) fn nodelist(source: &str) -> NodeListDesc {
        let mut parser = Parser::new(Lexer::new(source).tokenize(), &NoHost);
        parser.parse(&[]).expect("expected a successful parse")
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source scripts/env.sh && cargo test -p template-core get_nodes_by_type`
Expected: FAIL to compile — no such method on `NodeListDesc`.

- [ ] **Step 3: Write the walk**

```rust
impl NodeListDesc {
    /// `NodeList.get_nodes_by_type(nodetype)` — `base.py:1093-1098`, whose
    /// per-node half is `Node.get_nodes_by_type` (`base.py:1070-1082`).
    ///
    /// Django's `NodeList` version delegates to each node's, which tests
    /// `isinstance(self, nodetype)` and then recurses into `child_nodelists`.
    /// The descriptor has the class **name**, and Rust emits only child
    /// nodelists — including `IfNode`'s branch lists, whose names are empty
    /// (`desc.rs:543-546`, emitted at `if.rs:109`) — so the two walks visit the
    /// same set for every node Rust builds.
    ///
    /// **Pre-order, and document order is the contract**, not an incidental:
    /// `ExtendsNode.blocks` is a dict folded from this list, and `describe`
    /// descends it without sorting.
    ///
    /// **The fold's "a repeated name keeps the last" is not reachable from a
    /// Rust tree.** `{% block %}`'s duplicate-name check refuses the repeat, so
    /// the only way two same-named blocks reach one nodelist is through a
    /// host-compiled block — the fork A5 records, and there it changes a
    /// *render*, not just a message (D7).
    ///
    /// **One recorded divergence:** a descriptor whose `class_name` is
    /// [`HOST_NODE`] names a Python node Rust must not read, so it is neither
    /// matched nor descended into. A host tag returning an `ExtendsNode`
    /// *subclass* would match Django's `isinstance` and not this — and so would
    /// a host block tag whose body contains a second `{% extends %}`. **Both
    /// are measured zero in the pinned suite**, not assumed: no
    /// `class <name>(ExtendsNode|BlockNode|IncludeNode)` exists anywhere under
    /// `tests/template_tests`, and `{% extends %}` nested inside a
    /// host-owned block-level tag occurs 0 times, over 1046 string literals and
    /// 62 fixture templates (`.review/m7-measurements/m7_measure_nest2.py`). The *neighbouring*
    /// case is not zero — a nested `{% block %}` occurs once, in
    /// `test_blocktranslate.py`; see A5. Review Focus item 3.
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

- [ ] **Step 4: Write the failing `extends` tests**

**Corrected at execution time (D7), three times.** (a) `parent_name` is an
`AttrValue::FilterExpr`, not `AttrValue::Ref` — the parent name is
`parser.compile_filter`'s output and `Ref` is a different variant. (b)
`ErrorKind::Syntax` **carries the message**, so `assert_eq!(error.kind,
ErrorKind::Syntax)` does not compile: it is a tuple variant and the assertion is
against the whole thing. (c) The draft asserted `failure("{% extends \"a\" %}")`
is `ErrorKind::Unimplemented` — the opposite of the truth, and the kind of
statement that would have been written into prose if the test had been skipped:
a lone `{% extends "a" %}` **compiles**, and the assertion is that it does.

```rust
#[test]
fn extends_carries_the_parent_as_a_compiled_filter_expression() {
    let node = compile("{% extends \"base.html\" %}");
    assert_eq!(node.class_name, "ExtendsNode");
    let AttrValue::FilterExpr(expression) = attr(&node, "parent_name") else {
        panic!("parent_name is a FilterExpr");
    };
    // The token is the *quoted* literal: `compile_filter` sees what
    // `construct_relative_path` returned, and that keeps the original quoting.
    assert_eq!(expression.token, "\"base.html\"");
    let (is_var, _) = &expression.var;
    assert!(!is_var, "a literal, not a lookup");
    assert_eq!(node.nodelists[0].0, "nodelist");
    assert_eq!(node.nodelists[0].1.nodes.len(), 0);
}

#[test]
fn a_second_extends_is_rejected_from_inside_the_first() {
    // `do_extends`'s unbounded parse (`LT:307`) makes the second one a child of
    // the first, so the check is a scan of that child nodelist — and the
    // **token** is what makes the shape observable: it is the *first*
    // `{% extends %}`, the frame the check ran in. Measured on the pin: Django
    // reports `(2, "extends 'a'", 1, None)`.
    let error = failure("{% extends \"a\" %}{% extends \"b\" %}");
    assert_eq!(
        error.kind,
        ErrorKind::Syntax(
            "'extends' cannot appear more than once in the same template".to_string()
        ),
    );
    let token = error.token.expect("the tag's own token is attached");
    assert_eq!(token.contents, "extends \"a\"");
}

#[test]
fn extends_takes_exactly_one_argument() {
    assert_eq!(
        failure("{% extends %}").kind,
        ErrorKind::Syntax("'extends' takes one argument".to_string()),
    );
    assert_eq!(
        failure("{% extends \"a\" \"b\" %}").kind,
        ErrorKind::Syntax("'extends' takes one argument".to_string()),
    );
}

#[test]
fn extends_must_be_the_first_non_text_node() {
    // `must_be_first`, and both sides of the `contains_nontext` test are here
    // because only one of them raises.
    assert_eq!(
        failure("{{ x }}{% extends \"a\" %}").kind,
        ErrorKind::Syntax(
            "{% extends \"a\" %} must be the first tag in the template.".to_string()
        ),
    );
    assert_eq!(
        failure("{% block b %}y{% endblock %}{% extends \"a\" %}").kind,
        ErrorKind::Syntax(
            "{% extends \"a\" %} must be the first tag in the template.".to_string()
        ),
    );
    // Text before it is the **control**: `extend_nodelist` flips
    // `contains_nontext` only for a node that is not a `TextNode`, so this one
    // compiles — and a port that used "is the nodelist empty" instead would
    // raise here too. Two top-level nodes, hence `nodelist` and not `compile`.
    let tree = nodelist("x{% extends \"a\" %}");
    assert_eq!(
        tree.nodes
            .iter()
            .map(|node| node.class_name.as_str())
            .collect::<Vec<_>>(),
        vec!["TextNode", "ExtendsNode"],
    );
}
```

- [ ] **Step 5: Write `extends.rs`**

**Corrected at execution time (D7).** `AttrValue` is imported by the `tests`
module, not the module body — the body builds a `FilterExpr` through
`compile_filter` and never names the variant, so a top-level import is an unused
one and `clippy -D warnings` rejects it. `.first()` is the third call in the
chain and is what A4's inbound rule reads; the draft's version omitted it and
the port would have accepted `{{ x }}{% extends "a" %}`.

```rust
//! `{% extends %}` — `django.template.loader_tags.do_extends` (`loader_tags.py:292`).
//!
//! The only built-in whose `CompileFn` ends by **consuming the rest of the
//! template** — `nodelist = parser.parse()` with no `parse_until`
//! (`loader_tags.py:307`) — and most of what looks surprising about this tag
//! follows from that one line.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::relative::construct_relative_path;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // `split_contents()`, not `contents.split()`: Django takes a *variable* as
    // readily as a literal here (`loader_tags.py:296-299`).
    let mut bits = split_contents(token)?;
    if bits.len() != 2 {
        // `bits.len() != 2` admits 0, and Django's `% bits[0]` would raise
        // `IndexError` there. `Parser::parse` splits the same contents and
        // raises `Empty block tag on line %d` first (`base.py:560-563`).
        let command = bits
            .first()
            .expect("parse rejects an empty block tag before a compile function runs");
        return Err(Error::syntax(format!("'{command}' takes one argument")));
    }
    bits[1] = construct_relative_path(parser.origin_template_name(), &bits[1], false)?;
    // The parent is a host-built `FilterExpression`: `ExtendsNode.get_parent`
    // calls `self.parent_name.resolve(context, ignore_failures=True)`
    // (`loader_tags.py:123`), which only a real one answers.
    let parent_name = parser.compile_filter(&bits[1])?;
    let nodelist = parser.parse(&[])?;
    if !nodelist.get_nodes_by_type("ExtendsNode").is_empty() {
        return Err(Error::syntax(format!(
            "'{}' cannot appear more than once in the same template",
            bits[0]
        )));
    }
    // `.first()` is `ExtendsNode.must_be_first = True` (`loader_tags.py:87`),
    // the only built-in that sets it, read by `extend_nodelist` when this node
    // is appended.
    Ok(NodeDesc::new("ExtendsNode")
        .attr("parent_name", parent_name)
        .nodelist("nodelist", nodelist)
        .first())
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

`tests/parser_corpus.py` — **corrected at execution time (D7)**: six rows, not
three. The `{% if %}`-wrapped row is in the draft and is a parity row, not the
`{% block %}`-in-an-`{% if %}` row Step 1 was rewritten to; the `{% block %}`
rows are what make the unbounded parse visible to the parity harness.

```python
    "{% extends \"base.html\" %}",
    "{% extends \"base.html\" %}{% block a %}x{% endblock %}",
    "{% extends parent %}",
    "x{% extends \"base.html\" %}",
    "{% if x %}{% block a %}x{% endblock %}{% endif %}",
    "{% extends \"base.html\" %}{% block a %}A{% endblock %}{% if x %}{% block b %}B{% endblock %}{% endif %}",
```

`tests/parser_error_corpus.py` — four rows, and the fourth is not in the draft:

```python
    ("{% extends %}", "'extends' takes one argument"),
    ("{% extends \"a\" \"b\" %}", "'extends' takes one argument"),
    (
        "{% extends \"a\" %}{% extends \"b\" %}",
        "'extends' cannot appear more than once in the same template",
    ),
    ("{{ x }}{% extends \"a\" %}", "{% extends \"a\" %} must be the first tag in the template."),
```

The two relative-path messages are **not** reachable from a bare `from_string`
origin — they need a template name — so they go into a **separate module-level
list** of `(source, origin_template_name, expected)` triples, not into `ERRORS`:

```python
ORIGIN_ERRORS = [
    ("{% extends '../../x.html' %}", "index.html", "..."),
    ("{% extends './index.html' %}", "/index.html", "..."),
]
```

**Both test harnesses needed the origin threaded (D7), and that is a change to
two files the draft does not list.** Django's `do_extends` reads
`parser.origin.template_name` (`loader_tags.py:305`) and a bare `Parser(...)` has
`origin=None`, so `django_failure` in `tests/test_parser_errors.py` and
`django_parse` in `tests/test_parser_parity.py` each raised
`AttributeError: 'NoneType' object has no attribute 'template_name'` on the ten
new rows. `Parser`'s fourth positional argument is `origin`:
`Origin(UNKNOWN_SOURCE, origin_name)` — whose `template_name` is the second
argument — with `Origin(UNKNOWN_SOURCE)` where the row carries none. `Template`
defaults that itself (`base.py:151-154`), which is why the corpus never hit it
before.

- [ ] **Step 8: Run the guards, then the gates**

Run: `source scripts/env.sh && cargo test -p template-core && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py' && ./scripts/check.sh --fast`

Expected: all pass. **The last two are the corrected commands (D7): there is no
`pytest` in this project**, and the anchor gate is a gate — Step 6 shifted two
lines in `tags/mod.rs`, so `scripts/anchors.txt` moves with it and `--check
--frozen` is what notices.

Observe the walk's guard failing: change `get_nodes_by_type` to skip nodelists
whose name is empty and confirm Step 1's test fails — that is the branch the
`{% if %}`-wrapped case exists for.

Observe the `.first()` guard failing: drop `.first()` from Step 5's chain and
confirm `extends_must_be_the_first_non_text_node` fails — the only assertion
that covers it.

- [ ] **Step 9: Commit**

**Corrected at execution time (D7)**: two files the draft's list omits —
`tests/test_parser_errors.py` and `tests/test_parser_parity.py`, both edited in
Step 7 to thread the origin — plus `scripts/anchors.txt` and the two citing
files the line shift moved.

```bash
git add crates/template-core/src/parser/tags/extends.rs \
        crates/template-core/src/parser/tags/mod.rs \
        crates/template-core/src/parser/desc.rs \
        crates/template-core/src/parser/registry.rs \
        crates/template-core/src/parser/relative.rs \
        python/django_rust_templates/_impl/parser.py \
        tests/test_tag_inventory.py tests/parser_corpus.py \
        tests/parser_error_corpus.py tests/test_parser_errors.py \
        tests/test_parser_parity.py tests/test_materialise.py \
        scripts/anchors.txt \
        docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md
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
    assert!(matches!(failure("{% include %}").kind, ErrorKind::Syntax(_)));
    assert!(matches!(failure("{% include \"a\" with %}").kind, ErrorKind::Syntax(_)));
    assert!(matches!(failure("{% include \"a\" only only %}").kind, ErrorKind::Syntax(_)));
    assert!(matches!(failure("{% include \"a\" nope %}").kind, ErrorKind::Syntax(_)));
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
the three reach the identity clause at all only because
`Engine.default_builtins` (`engine.py:14-18`) lists
`django.template.loader_tags` third and `add_library` (`base.py:668-670`) merges
it into `parser.tags` — so the clause reads `None` for all three, `func is not
None` holds, and without the merge all three would route to the host. The
paragraph must also record the guard's cost, since that is why the clause is
deleted rather than kept: `_M7_OWNED` is consulted *before* identity, so today a
`{% load %}`ed library that registers `block` loses (measured — the parser's
`block` *is* the loaded callable and `has_tag` still returns `False`).

Update `parser/mod.rs`'s host-first comment (`:309-317`) — "a custom library that
redefines a built-in name wins" is still true, and now so is the case where the
*baseline* callable lives in a second module.

- [ ] **Step 6: The predicate test — write it, watch it fail, then fix**

In `tests/test_inheritance_gate.py`:

```python
def test_the_host_declines_every_name_rust_owns():
    """A2: the predicate must say 'Rust's' for all three, with no exception list.

    This is row 4 of the plan's A2 matrix, and it is deliberately two
    assertions: that the parser's `block` really is `loader_tags`' callable
    (row 3's precondition — the identity clause can only work if the callable
    it compares against is the one it finds), and that the host declines.

    **It does not fail before Step 5's edits, and it is not meant to.** Today's
    `_M7_OWNED` clause returns `False` first, so the test passes on the
    pre-M7 tree too; it goes red only if the guard is deleted *without* the
    table being widened (A2's rows 2 and 3). It is a pin on the end state, not
    a red-then-green step.
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
    """A2's rows 5 and 6: the identity clause reaches a `{% load %}`ed override.

    **This is Step 6's failing test.** Before Step 5 it returns `False` — the
    `_M7_OWNED` clause is consulted before identity, so a library registering
    `block` loses — and Step 5 is what makes it `True`. Note the shadowing is
    established *for* these three names, not preserved for them; the same
    override of `comment` already worked (M6's gate,
    `tests/test_load_shadowing.py`).
    """
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

Run both **before** Step 5's edits: `test_a_loaded_library_still_shadows_a_built_in`
must fail (`False is not True`) and `test_the_host_declines_every_name_rust_owns`
must pass — that asymmetry *is* the guard's cost, and it is why Step 5 does both
edits. Then apply Step 5 and re-run: both green.

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

Run: `source scripts/env.sh && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py' && .venv/bin/python -I scripts/check_python_anchors.py --check --frozen`
Expected: all pass.

**Corrected at execution time (D8), then re-measured (D8′).** The trap is real
and *both* symptoms this step predicted were wrong — the recorded one because it
came from a different file. Measured, reverting `django_own_tags` to
`dict(_defaulttags.register.tags)` (`.review/m7-measurements/m7_measure_half_fix.py`,
which models the half-fix by rebinding the module global and so is re-runnable
against the shipping tree):

| what | predicted here | measured |
|---|---|---|
| `test_the_host_declines_every_name_rust_owns` | fails | **fails** — `has_tag` returns `True` for all three names, so `block`/`extends`/`include` route to the host |
| `test_a_loaded_library_still_shadows_a_built_in` | (unstated) | **passes** — the half-fix *is* the pre-M7 state for it, which is the asymmetry Step 6's ordering uses |
| `{% extends "a" %}{% extends "b" %}` | "stops raising" | **still raises, with Django's own text** — `'extends' cannot appear more than once in the same template` |
| the rest of the error corpus | (unstated) | **0 of 106 rows differ, and 0 of 3 of `ORIGIN_ERRORS`** |
| the whole suite | (unstated) | **256 tests, 3 failures** — the three subTests of the row above, and nothing else |

**The first measurement of this step reported seven corpus rows dying with
`AttributeError: 'NoneType' object has no attribute 'template_name'`, and that
was a harness artifact.** `tests/test_parser_errors.py::ours_failure` built its
`Parser` with three arguments where its `django_failure` sibling passes a fourth,
`Origin(UNKNOWN_SOURCE, origin_name)` — and `Parser.__init__` stores `origin`
verbatim (`base.py:524`), so `parser.origin` was `None` on our side and an
`Origin` on Django's. Under the half-fix, `{% extends %}` routes to the host,
Django's `do_extends` runs on *that* `Parser`, and `parser.origin.template_name`
is the raise. Nothing in the corpus reached it while `extends` and `include` are
Rust's, which is why five commits of M7 passed over it. One argument fixes it
(committed with this correction), and with it fixed the corpus is green under the
half-fix: no row sees the half-fix at all.

**Why the predicted symptom cannot happen by that route.** `do_extends` calls
`parser.parse()` itself (`loader_tags.py:307`) and scans what it returns for an
`ExtendsNode` (`:308-311`). `ShellHost.call_tag` hands the host's `Parser` the
remaining tokens, so that inner parse runs Django's own tag table and compiles
the second `{% extends %}` into the nodelist the outer scan then finds. The
duplicate rule is not skipped by the routing — the routing *reaches* it. The
prediction assumed Rust's outer loop was the only thing that could have compiled
the second tag, and under a seam that re-enters Django's own `parse` it is not.

So the half-fix's only detector is the predicate, which is the row above and the
whole of Step 6's argument — and the generalisation is worth more than the row:
**a routing change under a seam that re-enters Django's parse is invisible to
every message-comparing corpus, because the misrouted tag runs the same code down
a different path.** The second generalisation is the harness one, and it is M6's
`parser_for` shape repeating: a harness `Parser` missing one constructor argument
is invisible until a host-compiled tag reads the thing that argument supplies,
and the first place a reader looks is the tag.

**A second detector was added while Task 6 was being executed**, and it is the
one that survives a rewrite of the predicate:
`HostPredicateTests.test_the_wire_shows_which_side_compiled_each_name` reads
*which side compiled a name* off the parse result rather than off the predicate.
A Rust-compiled tag arrives from `_native.parse` as a plain class name
(`BlockNode`/`ExtendsNode`/`IncludeNode`); a host-compiled one arrives as
`HostNode` carrying a `NodeRef`. Measured under the half-fix — rebinding
`.django`'s `defaulttags.register.tags` into `django_own_tags`, the same model
`m7_measure_half_fix.py` uses — the wire reads `HostNode` for all three names,
so the row fails for the same reason the predicate row does but through a
mechanism that does not consult `has_tag` at all. The two are not redundant in
the other direction either: a routing break with the predicate intact (Rust
declining a name it should compile, or the reverse) moves the wire and leaves
the predicate row green. The pair says *why* the routing is what it is; the wire
says *that* it is — which is the property a change will actually alter.

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
(`.review/m7-measurements/m7_measureB.py`, on the pinned tree):

```text
{% load mylib %}{% first_tag %}                     -> {% first_tag %} must be the first tag in the template.
text{% load mylib %}{% first_tag %}                 -> {% first_tag %} must be the first tag in the template.
{% load mylib %}{% bad_tag %}   (returns object())  -> AttributeError: 'object' object has no attribute 'must_be_first'
```

**Two rows of the draft block were false, and the explanation with them (D9).**
The draft had `{% load mylib %}{% first_tag %}` rendering `'F'` and
`{% load mylib %}{% plain_tag %}text{% plain_tag %}` raising a must-be-first
message. Both are wrong on the pin, and the cause is one mechanism:
`extend_nodelist` sets `nodelist.contains_nontext = True` the first time a
non-`TextNode` is appended and **never clears it** (`base.py:611-612`, the class
default at `:1088`) — and `{% load %}` appends a `LoadNode`. So a *loaded*
library's `must_be_first` tag can never be first, on either engine; the leading
`text` in the second row is a `TextNode` and flips nothing, so that row fails on
the load's account alone. `{% plain_tag %}` sets no flag at all and its bare
`Node.render` returns `None`, which is the `TypeError` the draft mistook for an
ordering message. The accepted case therefore needs the tag registered as a
**builtin**, which reaches the parser through `Parser.__init__`'s merge with no
node of its own; measured, `engine.template_builtins.append(lib)` is
`OPTIONS["builtins"]` with the module indirection removed (`get_template_builtins`
only runs `import_library` over that list, `engine.py:120-121`).

**Corrected at execution time (D9).** The test as drafted cannot pass, in three
places, and each is a different kind of wrong:

| the draft | why it cannot pass | what shipped |
|---|---|---|
| `assertRaises(TemplateSyntaxError)` for **our** side | `_native` raises its own `ParseError` and the shell converts it at M9 (design decision 2, `tests/test_parser_errors.py:274-283`) | `assertRaises(_native.ParseError)`, with the **message** compared against stock's `TemplateSyntaxError` — which is the whole of what the row is about |
| the accepted row, `{% load mylib %}{% first_tag %}` | Django itself refuses it: `{% load %}` appends a `LoadNode` and `contains_nontext` is sticky | a second engine, the library appended to `template_builtins`, so the tag is genuinely first |
| `{% load mylib %}` in a *refused* row | fine — but for the load's reason, not the text's | kept, with the comment naming the load |

The refused row also gained a third test the draft did not have, and it is the
one that makes the guard observable — see Step 4.

- [ ] **Step 1: Write the failing test**

In `tests/test_inheritance_gate.py` — the shipped form, which is longer than the
draft above because two of the draft's three rows were false:

```python
class MustBeFirstGateTests(unittest.TestCase):
    def _library(self):
        # `first_tag` returns a Node with must_be_first = True; `bad_tag` returns
        # something that is not a node at all, for Step 4's row.
        …

    def _engine(self, builtin=False):
        engine = Engine()
        lib = self._library()
        if builtin:
            engine.template_builtins.append(lib)
        else:
            engine.template_libraries["mylib"] = lib
        return engine

    def test_a_host_node_that_must_be_first_is_refused_after_text(self):
        with self.assertRaises(_native.ParseError) as ours:
            render_string("text{% load mylib %}{% first_tag %}", {}, self._engine())
        with self.assertRaises(TemplateSyntaxError) as stock:
            self._engine().from_string(…).render(Context({}))
        self.assertEqual(str(ours.exception), str(stock.exception))
        self.assertEqual(str(ours.exception), "{% first_tag %} must be the first tag in the template.")

    def test_a_host_node_that_must_be_first_is_accepted_first(self):
        # Builtin engine; position is the only variable between the two rows.
        self.assertEqual(render_string("{% first_tag %}", {}, self._engine(builtin=True)), "F")
        # and the same engine refuses the second of two
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest tests.test_inheritance_gate -v`
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

Run: `source scripts/env.sh && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'`
Expected: PASS; no other test changes.

**Corrected at execution time (D9): the mutation this step names is a no-op.**
`unwrap_or(false)` returns the `Ok` value when the call succeeds, so a node that
*carries* `must_be_first = True` answers `true` under both spellings — and the
refusal test cannot see the difference. Measured: with `.map_err(...)?` swapped
for `.unwrap_or(false)` the four rows stayed green.

The two spellings differ only where the attribute is **absent**, so the shipped
file has a third row that supplies one: a `bad_tag` returning `object()`.

| spelling | `{% load mylib %}{% bad_tag %}` | the refusal rows |
|---|---|---|
| `.map_err(...)?` (shipped) | `ParseError: ShellHost.call_tag("bad_tag") returned a node without a bool \`must_be_first\`: AttributeError: …` | green |
| `.unwrap_or(false)` (mutant) | silently accepted at the seam; dies later in the materialiser as `AttributeError: 'object' object has no attribute 'token'` (`_impl/parser.py:271`) | **green** |

So the guard's justification — "raise rather than default `false`" — is
observable, but only through the row that supplies a non-node, and the mutant's
symptom lands in a file the author of the bad tag never touched. The plan
believed the refusal row carried the difference; it carries nothing of it.

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

**Corrected at execution time (D10): it does not, and it cannot.** Measured, the
mutation this step names — make the `IncludeNode` arm of `materialise_node`
return a copy — leaves the row green, because materialisation runs once per
*parse*: the copy is one object across the loop's nine iterations and the cache
still hits. "One node per tag" is structural on our side, not something the
materialiser chooses, so no mutation of ours makes the counter restart.

What does make it restart is breaking Django's own cache — popping
`render_context.dicts[0][self]` before every render — and that gives
`'000000000'` on **both** engines. So the row is a *parity* instrument: it
detects our engine disagreeing with stock about what it handed the renderer, not
a per-render rebuild. The shared input is the cache itself — `IncludeNode.render`
keys Django's `render_context`, both engines read that one dict — so a row
comparing the two outputs cannot see a defect in the thing they share.

**That is `parity validates agreement, never correctness` in its fourth
appearance in this project and its first in M7.** The remedy is not a better
parity row but a non-parity row beside it, and the shape is what generalises: a
row that observes something **the rendered output cannot**. The structural row
below is exactly that — `2 != 1` on the materialised nodelist, where the extra
node renders `''` and is therefore invisible to any output comparison. The next
shared input will not be a cache, and the recipe still holds.

The shipped file therefore carries a second row, and it is the one a mutation of
ours can fail: `test_the_loop_body_holds_exactly_one_include_node` walks the
materialised tree with Django's own `get_nodes_by_type` and asserts that the
body holds exactly one node and that it is the `IncludeNode` the walk found.
Measured, with the materialiser appending a stray empty `TextNode` to every
nodelist: this row fails (`2 != 1`) and the counter row stays green — which is
the whole reason for having it, since an empty text node renders `''` and the
output cannot see it.

- [ ] **Step 3: Run the gates**

Run: `source scripts/env.sh && maturin develop --manifest-path crates/template-py/Cargo.toml && .venv/bin/python -m unittest tests.test_inheritance_gate -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_inheritance_gate.py scripts/anchors.txt \
        docs/superpowers/plans/2026-10-09-m7-inheritance.md
git commit -m "M7 Task 6: the include-cache gate, pointed at our engine"
```

The three files, and why each is in: the gate file carries the counter row, the
structural row Step 2 describes, and the wire-shape row; the manifest carries the
two pin anchors those rows cite (`loader_tags.py:197`,
`syntax_tests/test_include.py:371-397`); this plan carries D10 and the Task 4
Step 8 note.

**The two wrong spans this step found, and which half of the guard found them.**
Both citations landed with no manifest entry, and `--check --frozen` failed
*loudly* on that — `check` appends the failure and `continue`s
(`scripts/check_python_anchors.py:526-529`), and the docstring at `:498-499`
lists "an anchor with no entry needs one authored" as one of the three repairs.
So the gap did not hide them; it forced the entries. **What convicted the wrong
numbers was authoring those entries with a snippet**: the span's claim is checked
against text that has to appear inside it, and `…test_include.py:371-396` cannot
contain `self.assertEqual(output, "012345678")`, which is at `:397`. The
`sys.path` bootstrap citation had the same shape — `test_host_tags.py:15-19`
named a paragraph about error text where the sentence described the loop at
`tests/test_host_tags.py:32-37`, and reading the span to quote it is what showed
that.

The distinction is worth keeping because the two halves imply different habits,
and only one of them is a habit. "Check new citations" is a discipline nobody
keeps. "A new entry carries a snippet" is enforced by `--check` the moment it is
followed — and the failure mode it closes is the one this step could have walked
into: **an unquoted entry** would have satisfied the gate with the wrong number
intact, silently. That is D7's class, and the invariant is the one already
adopted — never author an unquoted entry.

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

**The measurement scripts A3/A4/A5 cite are already preserved, and this step
re-runs them rather than copying them.** They lived under `/tmp/`, which does not
survive; they are now in `.review/m7-measurements/` with a `README.md` mapping
each script to the claim it carries and the plan section that cites it, and every
citation in this plan names that path. They are evidence for four claims no test
carries — the two-clause predicate's matrix, the origin's unknown-source value,
the inbound `must_be_first`, and A5's nesting predicate. `.review/` is the
dated-record home for exactly this, and a divergence recorded with an unrunnable
argument is one nobody re-examines.

Run each one — `.venv/bin/python -I .review/m7-measurements/<name>.py` — and
confirm it still reproduces the figure its section states. A script that runs and
prints something else is the correction this step exists to catch; the run is
recorded in `m7-seams.md` with the date, not assumed from the README.

- [ ] **Step 2: Update the spec**

§10.3's M7 row: the gate is `tests/test_inheritance_gate.py`, and
`test_include_cache` is named as the *shape* copied rather than the suite row
that runs. §10.4's M7 paragraph: **already done** — `parser/mod.rs:336` was
corrected to `:502` and the spec was put under the anchor guard in the commit
before this plan's first task (`7ac772e`), so the citation is now *checked*
rather than merely written. That is stronger than the symbol this step
originally asked for, and it is why the step no longer asks for it: a symbol
carries no anchor, and the guard is what keeps the other 38 from rotting the
same way. Record D1–D6 as corrections in the spec's own voice,
and add §10.3's vacuity rule (D5) beside the table rather than as prose about
M7: the next milestone's gate is checked by the rule, not by re-reading this
plan.

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
