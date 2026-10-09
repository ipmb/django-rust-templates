# M9 — Interpose `base` and `smartif` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `django.template.base` and `django.template.smartif` resolve to our files, so the whole suite's parsing and rendering run through our `Template`, `NodeList`, `Node` and `Variable` — with the parse, render and resolver seams delegating to the Rust engine that already implements them.

**Architecture:** Both modules are transcriptions of the pinned 6.0.9 source, carrying a `# TRANSCRIBED:` marker in the docstring the way `_impl/__init__.py` does, with a small number of *delegation* edits where Rust already owns the work: `Template.compile_nodelist` (parse), `Variable._resolve_lookup` (resolve). The node classes themselves stay Python — per §5.5 the Python node tree *is* the render path — so `Node.render_annotated` and `render_value_in_context` become ours by transcription rather than by porting.

**Tech Stack:** Python 3.14 (pinned interpreter), Rust (`crates/template-core`, `crates/template-py` via PyO3/maturin), Django 6.0.9 pinned at `.django/`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§5.2, §5.5, §6, §10.3, §10.4). Recon that produced this plan: `.review/m9-recon.md`; its scripts in `.review/m9-measurements/`.

## Global Constraints

- `.django/` is gitignored, pinned at **6.0.9**, and **never modified**. Every transcription reads from it; nothing writes to it.
- **`scripts/run_django_suite.sh` is the only meaningful suite invocation.** `scripts/shimpath/sitecustomize.py` installs the shim only when `DJANGO_RUST_TEMPLATES == "1"`, which only that script exports. Running `.django/tests/runtests.py` directly produces output byte-identical to stock Django's and is not evidence of anything. Every `Ran N tests` in prose or a commit message must sit within twelve lines of a marker naming the run — `scripts/check_suite_counts.py` gates this and *will* fire on a bare count.
- **Never `git checkout` to restore a file after a mutation.** The work is uncommitted; use `git show HEAD:<path> > <path>` or an `Edit`. This constraint exists because a `git checkout` during recon would have destroyed in-flight work.
- `maturin develop` must be re-run after any Rust change or the Python tests silently exercise a stale `.so`.
- Never round-trip `tests/filter_cases.py` through `splitlines()`/`join()` — it holds a `U+2028`/`U+2029` pair.
- `_impl/` must not contain a file named after a real `django.template` module that we do not intend to shadow. `base.py` and `smartif.py` are names Django *does* have, which is the point. `_impl/parser.py` is safe only because Django has no `template/parser.py`.
- Commit messages end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- **Acceptance at M9 close:** `scripts/check.sh` prints `all checks passed`; `scripts/run_django_suite.sh` prints `Ran 1547 tests in …s` / `OK (skipped=6)`; the progress meter (`_shim.unshimmed()`) is **23**; `cargo test -p template-core` is green; clippy clean; anchors 0 failures; suite counts all provenance-bearing.

## Review Focus

Each line is a failure mode this milestone can ship while every count stays green. Each gets a test in the task that owns the code — see `.review/m9-recon.md` findings 3, 4 and 6 for the measurements behind them.

1. **The transcription drifts from upstream and nobody notices.** A `# TRANSCRIBED` file is only faithful if a bump re-diffs it; the docstring says so, but nothing *enforces* it. A silent divergence in `Token.split_contents` or `Lexer.tokenize` is invisible to the suite because the suite is green either way. → Task 7's drift guard, which fails on any hunk not in the declared list.
2. **The delegation is removed or bypassed and the count stays 1547.** A `compile_nodelist` that quietly called Django's own `Lexer`/`Parser` passes every test (finding 1a). → Task 2's counting-wrapper row, which fails if `_native.compile_template` is not reached.
3. **`render_value_in_context`'s conversion test is written `isinstance` instead of `issubclass(type(value), str)`.** The two differ exactly on a `str` *subclass* — `SafeString` — which is the common case in Django. → Task 4's row.
4. **A `str` subclass or a `SafeString` reaching an escaping path through our transcription changes output.** This is the same divergence as (3) observed at the render, not the classifier. → Task 4's second row, asserting rendered output against Django's.
5. **The resolver's escaping arm loses the failing `bit`,** so `Variable._resolve_lookup`'s outer handler logs nothing useful — measured as exactly two failures (`test_logging`). → Task 5's rows, plus the suite.

---

## File Structure

| Path | Responsibility |
|---|---|
| `python/django_rust_templates/_impl/base.py` | **Create.** Transcription of `.django/django/template/base.py` @ 6.0.9 with a `# TRANSCRIBED` docstring marker; `Template.compile_nodelist` and `Variable._resolve_lookup` delegate to Rust. ~1210 lines. |
| `python/django_rust_templates/_impl/smartif.py` | **Create.** Transcription of `.django/django/template/smartif.py` @ 6.0.9, marker only, no delegation (M5 already wired the Rust grammar). ~215 lines. |
| `python/django_rust_templates/_impl/parser.py` | **Modify.** Its `template_for` is the `__new__`-based constructor the Rust path uses; it must keep working when `Template` becomes ours. Read-only verification unless a cycle surfaces. |
| `crates/template-core/src/parser/resolve.rs` | **Modify (Task 5).** `LookupOutcome::Escaped` gains the failing `bit`. |
| `crates/template-py/src/lib.rs` | **Modify (Task 5).** The resolver binding exposes that `bit` to the host. |
| `tests/test_interposition.py` | **Create.** Direct-entry rows proving each seam actually delegates (Review Focus 2). |
| `tests/test_base_parity.py` | **Create.** Direct-entry rows for `render_value_in_context` (Review Focus 3, 4) and the render-annotation protocol (Task 6). |
| `tests/test_shim.py` | **Modify.** The meter row moves from `assertIn("django.template.base", remaining)` to asserting the count is 23 (Task 1, at 24 first); the partial-subpackage row gains the purge `install()` no longer performs (Task 0). |
| `tests/parity_target.py` | **Create (Task 0).** The single claim the six `*_target_is_genuinely_djangos*` rows were proxies for, restated directly: the comparison's Python side is Django's file or a gated transcription, and no declared region sits on the code the harness executes. |
| `tests/compared_path.py` | **Create (Task 1 follow-up).** The instrument that measures which functions a parity harness executes: wrap every function the module defines, run the corpus through the harness entry point, report the reached spans. Wrapping the defining module is not enough — consumers' `from … import name` bindings are rebound too, or the reach set depends on import order. |
| `scripts/check_transcriptions.py` | **Create (Task 0).** Fails when a `_impl` transcription differs from upstream outside its declared regions, when a declared region covers nothing, when a region over executable lines does not name them in a snippet, when the `# TRANSCRIBED:` marker names the wrong path or release, or when a region sits on the measured compared path (`COMPARED_PATH`). |
| `.review/m9-measurements/m9_measure_parse_path.py` | **Create (Task 1 follow-up).** Provenance for `COMPARED_PATH`: prints the measured reach over both parity corpora, per corpus, plus the paste-ready table. |
| `.review/m9-measurements/m9_guard_teeth.py` | **Create (Task 1 follow-up).** Runs each guard clause against a tree that should fail it, from a copy, and prints which clauses refused — so the guard's teeth are measured rather than assumed. |
| `scripts/gen_impl_transcription.py` | **Create (Task 1).** Regenerates a transcription from the pinned tree, with `--check`; the marker is inserted after the docstring's opening `"""`, text-anchored rather than line-anchored. |
| `scripts/check.sh` | **Modify.** Install the shim for the unit-test session (Task 0) and add the transcription guard to the fast path (Task 0). |

---

### Task 0: Make the unit-test harness shim-consistent

**DONE — commit `a105469`.** The numbering of the tasks below is unchanged, so the recon's references stay valid. This task was found by executing Task 1, not by reading it, and executing *it* corrected five of this section's own claims; those corrections are recorded here rather than quietly applied, because four of them were reasons rather than decisions.

**The defect.** With `_impl/base.py` present, the project's own unit tests went from 283 green to **543 failures** — and running `test_parser_parity` alone passed. The direction test settles it:

```
python -m unittest tests.test_shim tests.test_parser_parity   → FAILED (failures=537)
python -m unittest tests.test_parser_parity tests.test_shim   → OK
```

Import order decides the result, so the harness is order-dependent, not wrong.

**Root cause.** `describe()` in `test_parser_parity.py` dispatches on `isinstance(value, Node)`. The shim loads our file under Django's module name, so our `Node` is a **second class object** distinct from Django's. A module that binds `Node` at import time *before* any test calls `_shim.install()` holds Django's; a module imported after holds ours. `isinstance` then fails across the boundary, `describe` falls through to its `repr()` fallback, and the assertion compares **memory addresses**:

```
-  'nodes': [<django.template.defaulttags.LoadNode object at 0x10921ee90>,
+  'nodes': [<django.template.defaulttags.LoadNode object at 0x10921efd0>,
```

Two structurally identical trees, reported as different. This is the central hazard of M9 in miniature: interposition is exactly the act of creating a second class object for a name, and anything holding the first is silently outside the new world.

It is not caused by Task 1's new test module. `tests/test_shim.py` already called `_shim.install()` mid-run, before M9, and it was harmless because `exceptions.py` was the only interposed module and no parity test imported it. M9 is what makes the existing leak observable.

**The fix.** Install the shim once at unit-test session start, so every module binds the same classes. `scripts/check.sh`'s `python unit tests` step now uses the same `shimpath` + `DJANGO_RUST_TEMPLATES` mechanism `run_django_suite.sh` does.

#### The five corrections execution produced

**(1) The failure count, and whose failures they were.** Measured with the shim at session start:

| state | result |
|---|---|
| `base` absent | `Ran 283 tests` / `FAILED (failures=3)` |
| `base` present | `Ran 283 tests` / `FAILED (failures=8)` |

The first draft of this section said 7, and treated *all* of them as consequences of interposing `base`. Both were wrong: **three fail before `base` exists**, so the harness change alone breaks them, and the base-caused set is five.

**(2) The loaders row's cause and remedy were both wrong.** The first draft said it fails "because `base` no longer falls back" and that the fix is to change its subject. It fails with `base` absent, so the cause is elsewhere — and its subject was already correct:

```
install() early-returns if installed()   (_shim.py:166)
  → _purge() never runs
  → a `django.template.loaders` cached earlier in the session shadows the
    `_impl/loaders/__init__.py` the row just wrote
```

Measured both ways, with the module pre-cached: `MARKER=None, ours_is_impl=False` without a purge; `MARKER='ours', ours_is_impl=True, sibling_is_impl=False` with one. The remedy is the purge the row now does explicitly, and changing the subject would have failed identically for any subject.

**(3) The restated guard row as written would have failed.** The first draft restated the rows with `inspect.getsource(Parser)` and `assertIn("_impl", source)`. `getsource` returns the *class body*, which contains no `_impl` — measured `False` against `getsourcefile`'s `…/_impl/base.py`. This is the same mistake, in the same family, as asserting `cls.__module__` during Task 1's first `test_interposition.py`: a source-location question asked of a text accessor.

**(4) Guard-row enumeration.** The name pattern matches **8 rows across 7 files**; five of those are `base`-dependent and three (`test_filter_inventory`, `test_tag_inventory`, `test_variable_parity`) check registries and are not. The first draft named four files and "six rows". The rows that actually fail are enumerated in (5).

**(5) `scripts/check_transcriptions.py` was scheduled three tasks late.** The restated claim's warrant *is* the transcription gate, and this section said Task 0 "must land before Task 1" while putting the gate in Task 7. It now lands here, and it declares `_impl/__init__.py` from the start so it is not vacuous while it waits for `base.py` — the omission the reviewer had already predicted for the plan's other transitive warrants.

#### What landed

- [x] **Step 1: Run the unit tests the way the suite is run** — `scripts/check.sh`'s `python unit tests` step now sources the same `shimpath` + `DJANGO_RUST_TEMPLATES=1` environment `run_django_suite.sh` uses, rather than retyping the paths.

- [x] **Step 2: Confirm the count** — `scripts/check.sh --fast` → `Ran 283 tests` / `OK`, with `base` absent. The 3-then-8 measurement above was taken with a throwaway verbatim `_impl/base.py`, since reverted; the tree has not been left interposed.

- [x] **Step 3: Restate the guard rows — do not delete them** — six rows across four modules now call `tests/parity_target.py::assert_target_is_not_rust_owned`, which asserts the claim both proxies stood for:

  > the code the Python half of this comparison executes is not Rust.

  Concretely: the target's file is either Django's pinned copy or a `# TRANSCRIBED:` transcription; if it is a transcription, it is declared to `check_transcriptions.py`, `guard.check()` is clean **when this test runs** (not "was run by `check.sh` at some point"), and the compared path lies outside the declared regions.

  That last clause is the one that fails loudly when the interposition frontier moves, and it replaces the first draft's `setUpModule` — running the guard in-process is strictly better evidence than importing a module that asserts a different command ran. Both arms measured against a throwaway `base.py` with a real edit inside `Parser.parse`:

  | declaration | `guard.check()` | row |
  |---|---|---|
  | verbatim only | `undeclared difference ours:532 upstream:530` | RED |
  | parse edit declared, target ours | clean | RED — `[] != [((532, 532), (530, 530))]` |

  The second row is the disjointness clause: with the parse path declared, the transcription check is satisfied and the *parity* row is what catches it.

- [x] **Step 3b: `scripts/check_transcriptions.py`** — created here (moved from Task 7), wired into `check.sh`'s fast path. Declares regions rather than exact `(ours, upstream)` line pairs, because the one existing transcription, `_impl/__init__.py`, differs on **45 lines** — its docstring is deliberately rewritten (design spec §6) — and `difflib` re-aligns that whole block when one word changes. A declaration that must be regenerated whenever someone improves a sentence is one that gets regenerated without being read. Declaring the upstream side too is what stops a wholesale deletion from passing. Three red states measured before it was trusted: an undeclared edit, a stale region, and a deletion.

- [x] **Step 4: Fix the shim fallback row** — the subject stays `django.template.loaders` / `django.template.loaders.filesystem`; what changed is that the row now purges the cached bindings itself, per correction (2). Its docstring records that the subject is chosen for distance from the frontier, and that §10.3 (spec line 789) schedules `loaders` for M10 — so the re-pick is a known date rather than a discovery.

- [x] **Step 5: Verify each restated guard still has teeth** — done, and better than proposed: rather than renaming the gate script and watching an import fail, the teeth were measured against a real interposition with a real edit on the compared path (Step 3's table).

- [x] **Step 6: Commit** — `a105469`. Full `scripts/check.sh` green, including `Ran 1547 tests … OK (skipped=6)` with the meter still at 25.

#### What did NOT land here, and why

The **two rows that assert the meter** and the **three registry guard rows** are not touched. The meter row (`tests/test_shim.py:95`) passes while `base` falls through and moves in Task 1, where the interposition that changes it happens. The task boundary is "the harness is consistent", and Task 1 is "the harness is consistent *under interposition*".

The **pristine-module stash** is still rejected, on the reason recorded below, which survives this correction: the disjointness row fails loudly and names what moved; a stale stash fails silently.

**(c) The alternative, considered and not taken — and the reason is its scope, not its feasibility.** The reviewer proposed holding the pristine module — `import django.template.base as _pristine_base` *before* `install()`, which `_purge()` (`_shim.py:158-162`) does not destroy, since it deletes the binding and not the object. That is strictly stronger where it works: identity rather than text-equivalence, runtime rather than build time, and it needs no transcription guard in the premise at all.

**It is feasible.** An earlier draft rejected it on the grounds that `describe()` could not span two class universes. That was wrong, and wrong in a way worth recording so it is not re-derived: the two-universe problem is real but its fix is two tuples, because **`describe()`'s `isinstance` is a dispatch, not an assertion.**

```python
_NODE_CLASSES = (Node, _pristine_base.Node)   # both known objects, still identity-based
if isinstance(value, _NODE_CLASSES):
```

`type(value).__name__` is already in `describe`'s output and already compared — `"type": type(value).__name__`, and a literal `"type": "NodeList"` for the other arm — so the `isinstance` only chooses *which arm to take*. Widening it to both universes cannot weaken an assertion the dispatch does not make. (Pre-importing `_impl/parser.py` before `install()`, the other option in that draft, was correctly rejected: one cached module object for the session, so it would change every other unit test's world.)

**What actually decides it is the stash's scope, which must grow with the interposition frontier.** `django_parse` builds `Parser(tokens, engine.template_libraries, engine.template_builtins, …)`. Today `engine.py` and `defaulttags.py` are Django's, so one pristine module is internally consistent. §10.3 puts `engine` at **M10** (spec line 789) and `defaulttags` at **M11** (line 790). So a pristine `Parser` would then be fed *our* `Engine`'s builtins and *our* `defaulttags`' node classes: "the pristine side" becomes a **mix**, silently, and the stash has to grow to `{base, engine}` at M10 and `{base, engine, defaulttags, loader_tags, defaultfilters}` at M11. Each growth is a judgement about which modules the target depends on, and forgetting one produces a half-pristine target that still *looks* like Django's.

The disjointness row fails **loudly and names what moved**; a stale stash fails silently. That is the whole trade, and it is why the row is the stronger artifact: it converts a per-milestone argument into a per-milestone failure.

---

### Task 1: The walking skeleton — `_impl/base.py` transcribed, interposition proven

This is the highest-risk edit in the project: from here, every one of the 1547 tests renders through *our* `NodeList` (**964**, a lower bound — recon finding 1) and parses through *our* `Template` (1170). Do it verbatim first, delegate in Task 2. A verbatim transcription was measured to keep the whole suite green (recon finding 3), so any red after this step is an addressing/import error, not a behavioural one — which is exactly the property that makes this the right walking skeleton.

**Files:**
- Create: `python/django_rust_templates/_impl/base.py`
- Modify: `tests/test_shim.py:95`
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `django.template.base` resolving to `_impl/base.py`; the module exports every one of the **23 names** the falling-through framework imports from it (`_impl/__init__.py`, `_impl/parser.py`, `defaulttags.py`, `loader_tags.py`, `context.py`, …) and the **15 names** the pinned suite imports.

- [ ] **Step 1: Commit the generator, then generate**

**Not a heredoc.** Task 7's `scripts/gen_impl_transcription.py` moves here, because the file it generates is created here and a one-off script evaluates "the generator's output equals the committed file" exactly once, in the commit that created it, and never again — after which a hand edit diverges silently. It takes the module name as an argument and has the `--check` mode the repo's other generators have.

```python
"""Regenerate an `_impl` transcription of a pinned `django/template` module.

Two relations are checkable and this script owns the first: its output must
equal the committed file. The second — the committed file minus the pinned
source falls inside the declared regions — belongs to
`check_transcriptions.py`, and the two together are what make a 1207-line
transcription reviewable at all: the surface becomes "these regions differ",
which is a statement someone can check, where "we rewrote `base.py`" is not.

`--check` regenerates and diffs, like `gen_python_word_ranges.py`, so a
legitimate move (a pin bump) is fixed by re-running the generator rather than by
editing a file by hand — and a hand edit to `_impl/base.py` fails here.

The marker goes immediately after the module docstring's opening `\"\"\"`: **text,
not a line number.** Anchoring to a line would make our file a function of
Django's line numbering, so the next pin bump would change it for a reason that
is not ours.
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

MODULES = {"base": "django/template/base.py", "smartif": "django/template/smartif.py"}


def render(name: str) -> str:
    source = (CHECKOUT / MODULES[name]).read_text()
    marker = f"# TRANSCRIBED: {MODULES[name]} (Django 6.0.9)\n"
    assert source.startswith('"""\n'), "docstring shape changed; fix the insertion point"
    return '"""\n' + marker + "\n" + source[4:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("module", nargs="*", default=sorted(MODULES))
    args = ap.parse_args()

    failures = 0
    for name in args.module:
        want = render(name)
        path = IMPL / f"{name}.py"
        if args.check:
            got = path.read_text() if path.is_file() else ""
            if got != want:
                failures += 1
                print(f"{name}.py is not what the generator produces "
                      f"(run: scripts/gen_impl_transcription.py {name})")
            else:
                print(f"{name}.py matches the generator")
        else:
            path.write_text(want)
            print(f"wrote {path.relative_to(ROOT)} ({len(want.splitlines())} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

Then run it:

```bash
cd /Users/pete/projects/lincolnloop/django-rust-templates
.venv/bin/python -I scripts/gen_impl_transcription.py base
```

Expected: `wrote python/django_rust_templates/_impl/base.py (1209 lines)`.

- [ ] **Step 2: Verify the export surface before anything else**

A partial file fails at import, loudly — but check the count rather than trusting the loudness, because the failure surfaces in whichever module imports the missing name first, which is confusing.

```bash
.venv/bin/python -I - <<'PY'
import ast, pathlib
ours = ast.parse(pathlib.Path("python/django_rust_templates/_impl/base.py").read_text())
theirs = ast.parse(pathlib.Path(".django/django/template/base.py").read_text())

def top(tree):
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names

missing = top(theirs) - top(ours)
print(f"upstream symbols: {len(top(theirs))}, ours: {len(top(ours))}")
print(f"missing: {sorted(missing)}")
assert not missing, "transcription is not complete"
PY
```

Expected: `missing: []`, and our symbol count equals upstream's.

- [ ] **Step 3: Measure the progress meter**

```bash
.venv/bin/python -I - <<'PY'
import sys
sys.path[:0] = [".django", "scripts/shimpath"]
import django_rust_templates
django_rust_templates.install()
from django_rust_templates import _shim
remaining = _shim.unshimmed()
print(f"unshimmed: {len(remaining)}")
assert "django.template.base" not in remaining, "base did not interpose"
assert "django.template" not in remaining, "ours is not the package"
PY
```

Expected: `unshimmed: 24`.

- [ ] **Step 4: Run the suite — this is the walking-skeleton gate**

```bash
scripts/run_django_suite.sh template_tests
```

Expected: `Ran 1547 tests in …s` / `OK (skipped=6)`. If it is red, the cause is an import or attribute addressing error in the transcription, **not** a behaviour divergence — recon finding 3 measured a verbatim copy as green. Do not start editing behaviour to fix it; find the import.

- [ ] **Step 4b: Declare the transcription, and restate the three remaining guard rows**

**In this commit, not a later one.** `tests/parity_target.py` asserts a row loud and specific when an interposed module has no declaration, measured against a throwaway `base.py`:

```
AssertionError: 'base.py' not found in {'__init__.py': (…)} : base.py is
interposed but declares no differences from upstream, so nothing checks it
```

So `check_transcriptions.py` gains `base.py`, and the declaration is measured rather than guessed — for a verbatim transcription it is exactly the marker:

```python
    "base.py": (
        "django/template/base.py",
        [
            # The `# TRANSCRIBED:` marker and its blank separator, ours only.
            # Tasks 2 and 5 add the `compile_nodelist` and `_resolve_lookup`
            # bodies to this list.
            ((2, 3), None),
        ],
    ),
```

`((2, 3), None)` is what the differ reports for a verbatim copy — ours lines 2-3 have no upstream counterpart — and it is the honest starting point: at this commit the transcription delegates nothing, and the declaration says so.

Then the three rows that only fail once `base` is ours, each now calling `tests/parity_target.py::assert_target_is_not_rust_owned` the way Task 0's six do: `test_lexer_parity.py:259` and the three targets in `test_filter_expr_parity.py:417`.

- [ ] **Step 5: Replace the meter row with one that pins the count**

The existing row asserts `django.template.base` is *still* Django's, which is now false. Replace it with one that pins the **count**, so the meter cannot drift unnoticed.

**It asserts 24 here, not 23** — Task 1 interposes one of the two modules, so a row asserting 23 would leave this commit red, and every commit must pass `check.sh`. Task 3 is where it becomes 23. (This plan originally had it written red at Task 1; execution corrected it.)

Modify `tests/test_shim.py`, replacing the body of `test_unshimmed_reports_progress_and_is_currently_everything`:

```python
    def test_unshimmed_reports_progress(self):
        """The meter, pinned as a number rather than a membership test.

        This row used to assert ``"django.template.base" in remaining``, which
        was true until M9 interposed it — and which told us nothing about the
        other twenty-four modules, so a later change that handed any of them
        *back* would have gone on passing. The count is the claim the milestone
        is accepted on, so the count is what is asserted; it moves by one in the
        same commit as each interposition.
        """
        _shim.install()

        remaining = _shim.unshimmed()
        self.assertEqual(len(remaining), 24, sorted(remaining))
        self.assertNotIn("django.template.base", remaining)
        # Ours is not in the list, because ours is the package itself.
        self.assertNotIn("django.template", remaining)
```

- [ ] **Step 6: Measure the row's teeth**

A count assertion is only worth having if it can fail. Temporarily change `24` to `25`, run the row, confirm it fails with `24 != 25`, and restore with an `Edit` — never `git checkout`.

Run: `.venv/bin/python -m unittest tests.test_shim.ShimTests.test_unshimmed_reports_progress -v`
Expected: FAIL with `24 != 25` before the restore; PASS after.

- [ ] **Step 7: Write the direct-entry interposition rows**

Create `tests/test_interposition.py`. These are the rows recon finding 3 demands: interposition is invisible to the suite, so each seam needs a row that would fail if it were removed. Only Task 1's rows here — `smartif` (Task 3), `compile_nodelist` (Task 2) and the resolver (Task 5) each add theirs in their own commit, so every commit is green.

```python
"""Direct-entry evidence that the interposed modules are actually ours.

The suite cannot see this. Measured (`.review/m9-recon.md` finding 3): with
``_impl/base.py`` a byte-for-byte copy of Django's, ``unshimmed()`` reports one
fewer module, the whole suite is green, and the only row that notices is the
meter. So a transcription that delegates nothing, or delegates wrongly, passes
every count this project otherwise accepts.

Every row here imports the module through the shim and asserts on the file it
resolved to, or on a Rust call it must have made — the latter because a
delegation that quietly called Django's own implementation is green too
(finding 1a).
"""

import unittest

from django_rust_templates import _shim


class InterpositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_base_resolves_to_our_file(self):
        import django.template.base as base

        self.assertIn("_impl", base.__file__, base.__file__)

    def test_the_node_classes_are_defined_by_our_file(self):
        """Resolution and *identity* are different claims.

        The module resolving to our file does not by itself mean the class a
        render uses was defined there: a consumer that bound the name from an
        earlier import keeps Django's object while the module is ours. So the
        assertion is on the defining file.

        **Not on ``__module__``**, which reads ``django.template.base`` for our
        classes and is *supposed* to — the shim loads our file under Django's
        name, so that is the correct value for a drop-in replacement. The first
        draft of this row asserted ``"_impl" in cls.__module__`` and failed on
        all five classes for exactly that reason.
        """
        import inspect

        import django.template.base as base

        for name in ("Template", "NodeList", "Node", "Variable", "VariableNode"):
            with self.subTest(name=name):
                self.assertIn("_impl", inspect.getfile(getattr(base, name)))
```

- [ ] **Step 8: Run the new rows**

Run: `.venv/bin/python -m unittest tests.test_interposition -v`
Expected: both PASS. Then confirm the identity row has teeth by measuring the unshimmed path:

```bash
.venv/bin/python -c "
import sys, inspect
sys.path.insert(0, '.django')
import django.template.base as base
print(inspect.getfile(base.Template))
"
```

Expected: `.django/django/template/base.py` — no `_impl`, so the row would fail. (Note: do **not** run this with `-I`, which implies `-E` and drops `PYTHONPATH`; the first attempt at this check failed with `ModuleNotFoundError: No module named 'django'` and tested nothing.)

- [ ] **Step 9: Commit**

```bash
git add python/django_rust_templates/_impl/base.py scripts/gen_impl_transcription.py \
        scripts/check_transcriptions.py scripts/check.sh \
        tests/test_shim.py tests/test_interposition.py \
        tests/test_lexer_parity.py tests/test_filter_expr_parity.py
git commit -m "$(cat <<'EOF'
M9: interpose django.template.base

`_impl/base.py` is a transcription of the pinned 6.0.9 source, marked in the
docstring the way `_impl/init.py` is, with no delegation yet: this commit is
the walking skeleton, and a verbatim copy was measured to keep the suite green
(`.review/m9-recon.md` finding 3), so a red run here is an addressing error
rather than a behaviour one.

Interposition is invisible to the suite, so the evidence is direct-entry:
`tests/test_interposition.py` asserts the file each module resolved to and that
the node classes are ours, and `tests/test_shim.py`'s meter row now pins the
count (24) instead of a membership test on one module.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The parse seam — `Template.compile_nodelist` goes to Rust

The wider seam: **1170 of 1547 tests** call `Template.compile_nodelist` — 4360 calls — because every `Engine.from_string` builds a `Template` whose `__init__` ends in `self.nodelist = self.compile_nodelist()` (`base.py:155`). Delegating here flips the suite's parsing onto Rust **without `engine.py` being touched**, which matters because `engine.py` is M10's module.

There is no recursion hazard: the Rust path builds its `Template` through `_impl/parser.py::template_for`, which uses `Template.__new__` and assigns attributes directly, so it never re-enters `Template.__init__`.

**Files:**
- Modify: `python/django_rust_templates/_impl/base.py` (`Template.compile_nodelist`)
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: `django_rust_templates.render.parse_template(source, engine=None, origin=UNKNOWN_ORIGIN) -> Template` (existing; returns a `Template` built by `__new__` whose `nodelist`, `extra_data`, `name`, `origin`, `source` and `engine` are set).
- Produces: `Template.compile_nodelist()` returns a Rust-materialised `NodeList` and sets `self.extra_data`. `django.template.base`'s `Template` is now ours in substance, not only in name.

- [ ] **Step 1: Read upstream's method, so the delegation preserves all of it**

Run: `.venv/bin/python -I -c "import pathlib;print(''.join(pathlib.Path('.django/django/template/base.py').read_text().splitlines(keepends=True)[177:214]))"`

Expected — the exact body to be replaced, with three obligations called out:

```python
    def compile_nodelist(self):
        """
        Parse the template and return the parsed list of nodes.

        The parser is responsible for the parsing and the compiler.
        """
        if self.engine.debug:
            lexer = DebugLexer(self.source)
        else:
            lexer = Lexer(self.source)

        tokens = lexer.tokenize()
        parser = Parser(
            tokens,
            self.engine.template_libraries,
            self.engine.template_builtins,
            self.origin,
        )

        try:
            return parser.parse()
        except Exception as e:
            if self.engine.debug:
                e.template_debug = self.get_exception_info(e, e.token)
            raise
```

Three things live here and all three must survive: the `extra_data` assignment (which upstream puts immediately after `parser.parse()`, `base.py:198-200`, and which `{% partial %}` reads at M13), the debug `template_debug` attachment (`base.py:201-213`), and the exception being re-raised unchanged.

- [ ] **Step 2: Write the failing row first**

Add to `tests/test_interposition.py`, in `InterpositionTests`:

```python
    def test_compile_nodelist_reaches_rust(self):
        """The parse seam, pinned to a Rust call rather than to a result.

        The count `scripts/run_django_suite.sh` prints is not evidence for this:
        a `compile_nodelist`
        that quietly called Django's own `Lexer` and `Parser` keeps every count
        green (`.review/m9-recon.md` finding 1a). So the row wraps the binding
        and asserts it was entered — which fails the moment the delegation is
        reverted, whatever the templates render.
        """
        from django.template import Engine
        from django_rust_templates import _native

        calls = []
        original = _native.compile_template

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.compile_template = counting
        try:
            Engine().from_string("{{ a }}{% if b %}x{% endif %}")
        finally:
            _native.compile_template = original

        self.assertEqual(len(calls), 1, calls)

    def test_compile_nodelist_preserves_extra_data_and_debug_info(self):
        """The two obligations that a delegation is most likely to drop.

        `extra_data` is assigned inside upstream's `try`, between `parse()` and
        the `return`; the debug path attaches `template_debug` to the exception.
        A delegation that returned the nodelist and nothing else would lose both.
        """
        from django.template import Engine
        from django.template.exceptions import TemplateSyntaxError

        engine = Engine(debug=True)
        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string("{% badtag %}")
        self.assertTrue(hasattr(caught.exception, "template_debug"))
        self.assertEqual(
            caught.exception.template_debug["during"],
            "{% badtag %}",
        )
```

- [ ] **Step 3: Run the rows to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_interposition.InterpositionTests.test_compile_nodelist_reaches_rust -v`
Expected: FAIL — `0 != 1`. The transcription still calls Django's `Parser`.

- [ ] **Step 4: Replace the method body with the delegation**

In `python/django_rust_templates/_impl/base.py`, replace `Template.compile_nodelist`'s body. Keep upstream's signature, docstring (augmented) and exception contract.

```python
    def compile_nodelist(self):
        """
        Parse the template and return the parsed list of nodes.

        **Delegated to Rust at M9.** The lexer, the parser and the node
        construction all live in `template-core`; the shell that binds them to
        Django's real node classes is `django_rust_templates.render`, which
        returns a `Template` built by `__new__` so that this method is not
        re-entered. The `extra_data` assignment below is upstream's
        `base.py:198-200`, kept here rather than inside `render` because it is
        this class's attribute; the debug attachment is `base.py:201-213`.
        """
        from django_rust_templates.render import parse_template

        parsed = parse_template(self.source, self.engine, self.origin)
        self.extra_data = parsed.extra_data
        try:
            return parsed.nodelist
        except Exception as e:  # pragma: no cover - parse_template already raised
            if self.engine.debug:
                e.template_debug = self.get_exception_info(e, e.token)
            raise
```

Note the debug path: `parse_template` raises with `template_debug` already attached from Rust's token positions, so the `except` here is a belt-and-braces arm that upstream needs and we keep for fidelity; the comment says so rather than leaving a bare `try`.

- [ ] **Step 5: Run the rows to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_interposition -v`
Expected: the two new rows PASS; `test_smartif_resolves_to_our_file` still FAIL (Task 3).

- [ ] **Step 6: Run the suite**

Run: `scripts/run_django_suite.sh template_tests`
Expected: `Ran 1547 tests in …s` / `OK (skipped=6)`. This is the first run in which the suite's parsing is Rust's; **340** `get_exception_info` *annotations* arrive from `compile_nodelist`, so a message-rewriting divergence shows up here as `template_tests.test_exceptions` failures. (The recon's original total of 430 was frames, not annotations: `PartialTemplate.get_exception_info` delegates, so four calls are counted twice. Measured, the run has 430 frames over 426 annotations — recon finding 1.)

- [ ] **Step 7: Commit**

```bash
git add python/django_rust_templates/_impl/base.py tests/test_interposition.py
git commit -m "$(cat <<'EOF'
M9: Template.compile_nodelist delegates to the Rust parse

The wider of the two seams: 1170 of the 1547 tests parse at least once, because
every Engine.from_string builds a Template whose __init__ ends in
compile_nodelist. It also flips parsing onto Rust without engine.py being
touched, which is M10's module.

No recursion hazard: the Rust path constructs its Template through
_impl/parser.py::template_for, which uses __new__, so Template.__init__ is
never re-entered.

The row wraps _native.compile_template and asserts it was entered, because a
compile_nodelist that quietly called Django's own Lexer/Parser keeps all 1547
green (recon finding 1a). extra_data and the debug template_debug attachment
are pinned separately, being the two things a delegation drops.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `_impl/smartif.py` — the small side

215 lines, 8 top-level symbols. Only two framework modules import it (`defaulttags.py:43` takes `IfParser` and `Literal`; `loader_tags.py` takes `Literal`) and one suite site. The Rust grammar it shadows is already wired — `crates/template-core/src/parser/smartif.rs`, 1003 lines, wired by M5 — so this task is a transcription plus proof that the *right* one is reached.

**Files:**
- Create: `python/django_rust_templates/_impl/smartif.py`
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: Task 1's shim state.
- Produces: `django.template.smartif` resolving to `_impl/smartif.py`; meter 23.

- [ ] **Step 1: Generate the transcription**

```bash
cd /Users/pete/projects/lincolnloop/django-rust-templates
.venv/bin/python -I - <<'PY'
from pathlib import Path

SRC = Path(".django/django/template/smartif.py")
DST = Path("python/django_rust_templates/_impl/smartif.py")
MARKER = "# TRANSCRIBED: django/template/smartif.py (Django 6.0.9)\n"

source = SRC.read_text()
assert source.startswith('"""\n'), "docstring shape changed; fix the insertion point"
DST.write_text('"""\n' + MARKER + "\n" + source[4:])
print(f"wrote {DST} ({len(DST.read_text().splitlines())} lines)")
PY
```

Expected: `wrote python/django_rust_templates/_impl/smartif.py (217 lines)`.

- [ ] **Step 2: Confirm the symbols match upstream**

```bash
.venv/bin/python -I - <<'PY'
import ast, pathlib
def top(p):
    return {n.name if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else t.id
            for n in ast.parse(pathlib.Path(p).read_text()).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)) or
               (isinstance(n, ast.Assign) for _ in ())}
ours = top("python/django_rust_templates/_impl/smartif.py")
theirs = top(".django/django/template/smartif.py")
print("ours  ", sorted(ours))
print("missing", sorted(theirs - ours))
assert not (theirs - ours), "transcription incomplete"
PY
```

Expected: `missing []`, and both lists contain `Literal`, `IfParser`, `TokenBase`, `operator`, `infix`, `prefix`, `TOKEN_*`.

- [ ] **Step 3: Run the suite, then the meter**

Run: `scripts/run_django_suite.sh template_tests`
Expected: `Ran 1547 tests in …s` / `OK (skipped=6)`.

- [ ] **Step 4: Verify the meter is now 23**

```bash
.venv/bin/python -I - <<'PY'
import sys
sys.path[:0] = [".django", "scripts/shimpath"]
import django_rust_templates
django_rust_templates.install()
from django_rust_templates import _shim
remaining = _shim.unshimmed()
print(f"unshimmed: {len(remaining)}")
assert len(remaining) == 23, sorted(remaining)
PY
```

Expected: `unshimmed: 23`.

- [ ] **Step 5: Move the meter row to 23, and add the `smartif` interposition row**

Change `24` to `23` in `tests/test_shim.py::test_unshimmed_reports_progress`, and add to `tests/test_interposition.py`:

```python
    def test_smartif_resolves_to_our_file(self):
        import django.template.smartif as smartif

        self.assertIn("_impl", smartif.__file__, smartif.__file__)
```

Run: `.venv/bin/python -m unittest tests.test_shim tests.test_interposition -v`
Expected: all rows PASS, including `test_unshimmed_reports_progress` (23) and `test_smartif_resolves_to_our_file`.

- [ ] **Step 6: Add the row proving the Rust grammar is the one reached**

`defaulttags.py:43`'s `IfParser` is now ours, but the *grammar* it drives is Rust's (`_native.parse_filter_expression` / the smart-if binding wired at M5). Pin that the interposed `IfParser` reaches it.

Add to `tests/test_interposition.py`:

```python
    def test_smartif_parser_reaches_the_rust_grammar(self):
        """`{% if %}` must not be parsed by the transcribed Python grammar.

        `_impl/smartif.py` is a faithful transcription, so it would happily
        parse on its own and every test would stay green while Rust never saw
        the expression. The row asserts the binding is entered, for the same
        reason `test_compile_nodelist_reaches_rust` does.
        """
        from django.template import Engine
        from django_rust_templates import _native

        calls = []
        original = _native.parse_filter_expression

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.parse_filter_expression = counting
        try:
            Engine().from_string("{% if a and b %}x{% endif %}")
        finally:
            _native.parse_filter_expression = original

        self.assertTrue(calls, "the smart-if grammar was not reached")
```

If this row fails because the shell reaches Rust through a *different* binding than `parse_filter_expression`, replace the wrapped name with the one `_impl/defaulttags`' compiled `IfNode` actually calls — read `crates/template-py/src/lib.rs` for the exported name rather than guessing, and note the substitution in the commit message.

- [ ] **Step 7: Commit**

```bash
git add python/django_rust_templates/_impl/smartif.py tests/test_interposition.py
git commit -m "$(cat <<'EOF'
M9: interpose django.template.smartif

The small side: 215 lines, 8 symbols, two framework importers (defaulttags.py
takes IfParser and Literal). Transcription with the docstring marker, no
delegation — the Rust grammar it shadows was wired at M5.

The meter reaches 23 here, which is the milestone's number. test_shim.py now
asserts it, so a later regression that hands a module back cannot pass quietly.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `render_value_in_context` — §5.2's open obligation

A module-level function (`base.py:1123-1136`) that runs on all 9630 renders above. It becomes ours the moment `base` is interposed — but "becomes ours" is not the same as "is known correct", and the conversion test is the trap: `issubclass(type(value), str)`, **not** `isinstance`.

**Files:**
- Test: `tests/test_base_parity.py`
- Reference (read, not modified): `.django/django/template/base.py:1123-1136`

**Interfaces:**
- Consumes: Task 1's interposed `base`.
- Produces: `django.template.base.render_value_in_context` pinned against Django's own behaviour on the `str`-subclass boundary.

- [ ] **Step 1: Read the function and write down what it does**

Run: `.venv/bin/python -I -c "import pathlib;print(''.join(pathlib.Path('.django/django/template/base.py').read_text().splitlines(keepends=True)[1122:1136]))"`

Expected — the source of truth:

```python
def render_value_in_context(value, context):
    """
    Convert any value to a string to become part of a rendered template. This
    means wrapping them in a SafeString if necessary, or converting them to
    strings. If the value is a SafeString and the template context isn't
    autoescaping, this method returns the string unchanged.
    """
    value = template_localtime(value, use_tz=context.use_tz)
    value = localize(value, use_l10n=context.use_l10n)
    if context.autoescape:
        if not issubclass(type(value), str):
            value = str(value)
        return conditional_escape(value)
    else:
        return str(value)
```

- [ ] **Step 2: Write the failing rows — the `str`-subclass boundary**

Create `tests/test_base_parity.py`. The point is not that our transcription matches upstream (it does by construction); it is that the *behaviour* is asserted, so a future hand-edit, a future partial port to Rust, or a future `isinstance` "cleanup" fails here.

```python
"""Direct-entry rows for `base.py`'s render-time helpers.

M9 interposes `django.template.base`, so `render_value_in_context` — which ran
on all 9630 renders the suite performs (`m9_measure_render_blast_radius.py`) —
is now our code. It is a transcription, so it is correct by construction today;
these rows exist so it stays correct, and so the one character that matters
(`issubclass(type(value), str)` versus `isinstance(value, str)`) has a test that
would notice if someone "tidied" it.

`issubclass(type(value), str)` and `isinstance(value, str)` agree on every str
*instance* and disagree on nothing else either — both are true for a subclass
instance. The difference the upstream form makes is on a **`str` subclass that
overrides `__str__` or `__html__`**, where skipping the conversion preserves the
subclass; a port that converted would flatten a `SafeString` to `str` before
`conditional_escape` saw it and lose the "already safe" arm.

Measured against Django's own function imported from the pinned tree.
"""

import unittest

import django_rust_templates
from django_rust_templates import _shim


class RenderValueInContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()
        import django.template.base as base

        cls.base = base
        # The function under test, imported through the shim: ours.
        cls.ours = base.render_value_in_context
        # Django's own, loaded from the pinned tree by file path so the shim
        # cannot intercept it. This is the self-comparison guard M1 established:
        # a row that compares our function to our function proves nothing.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_pinned_base", ".django/django/template/base.py"
        )
        cls.pinned = importlib.util.module_from_spec(spec)

    def test_it_is_our_function(self):
        self.assertIn("_impl", self.ours.__module__)

    def test_a_str_subclass_is_not_reconverted(self):
        """The conversion test, observed rather than read.

        `SafeString` is a `str` subclass carrying `__html__`. Under
        `issubclass(type(value), str)` it is passed through; a port that tested
        `type(value) is str` (or converted unconditionally) would call `str()`
        on it. The observable difference is `type()` of the result.
        """
        import django.template.base as base
        from django.template import Context
        from django.utils.safestring import SafeString, mark_safe

        context = Context(autoescape=True)
        value = mark_safe("<b>&</b>")
        self.assertIsInstance(value, SafeString)

        rendered = base.render_value_in_context(value, context)
        # `conditional_escape` returns a SafeString for something already safe.
        self.assertIsInstance(rendered, SafeString)
        self.assertEqual(rendered, "<b>&</b>")

    def test_autoescape_off_returns_a_plain_str(self):
        from django.template import Context

        context = Context(autoescape=False)
        rendered = self.base.render_value_in_context(mark_safe("<b>"), context)
        self.assertEqual(rendered, "<b>")
        self.assertNotIsInstance(rendered, SafeStringLike)
```

Replace `SafeStringLike` with `__import__("django.utils.safestring", fromlist=["SafeString"]).SafeString` — write the import at the top of the module instead of inline, and assert `type(rendered) is str`.

- [ ] **Step 3: Run the rows**

Run: `.venv/bin/python -m unittest tests.test_base_parity -v`
Expected: PASS. If `test_it_is_our_function` fails, Task 1 did not interpose and this whole task is measuring Django.

- [ ] **Step 4: Break the guard deliberately, then restore**

Change `issubclass(type(value), str)` to `isinstance(value, str)` in `_impl/base.py` and re-run. **It should still pass** — record that in the module docstring, because it is the honest finding: the upstream form and `isinstance` are equivalent for every case either can reach, and the docstring's claim that they differ is what *this* measurement contradicts.

Then restore with an `Edit` (never `git checkout`), and rewrite the docstring to say what was measured rather than what was assumed. If the two forms are genuinely indistinguishable, say so, and keep the row that pins the observable behaviour.

- [ ] **Step 5: Commit**

```bash
git add tests/test_base_parity.py python/django_rust_templates/_impl/base.py
git commit -m "$(cat <<'EOF'
M9: pin render_value_in_context

§5.2's second open obligation. The function ran on all 9630 renders the suite
performs and is ours from the moment base interposes; these rows assert its
observable behaviour rather than its text, so a future hand-edit or a partial
port to Rust fails here.

The `issubclass(type(value), str)` versus `isinstance(value, str)` claim was
measured rather than repeated: the two agree on every input either can reach,
which contradicts the note this row was written from. The docstring now says
what was measured.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The resolver seam — `Variable._resolve_lookup`, with the failing `bit` on the wire

Recon finding 4: wiring `Variable._resolve_lookup` to `_native.resolve` leaves **exactly two** failures, both the same defect. `LookupOutcome::Escaped` carries no `bit`, so the host's outer handler (`base.py:1014-1018`) cannot log the variable that failed:

```python
logger.debug(
    "Exception while resolving variable '%s' in template '%s'.",
    bit, template_name, exc_info=True,
)
```

This task carries the `bit` through the Rust outcome and logs from Python, where the logger lives.

**The obligation is four-part, and `exc_info` is the half that is easy to miss** (the reviewer's catch, and it would have shipped). Upstream's handler is

```python
except Exception as e:
    template_name = getattr(context, "template_name", None) or "unknown"
    logger.debug("Exception while resolving variable '%s' in template '%s'.",
                 bit, template_name, exc_info=True)
    if getattr(e, "silent_variable_failure", False):
        current = context.template.engine.string_if_invalid
    else:
        raise
```

so it needs `bit`, `template_name`, the exception **object**, and `exc_info`. And `exc_info=True` resolves via `sys.exc_info()` **at call time** — which is empty outside an active `except`. M8's stash design is precisely "the exception is held, never active", so a handler that runs after the Rust call returns gets `(None, None, None)`.

`logging` stores that tuple rather than `None`, so the assertion a reader reaches for passes and the one below it fails:

```python
self.assertIsNotNone(log_record.exc_info)                              # passes
self.assertEqual(str(log_record.exc_info[1]), "Attribute does not exist.")   # fails: str(None) == 'None'
```

§D4 again, this time in the test rather than the code: the obvious assertion cannot discriminate; the one two lines down can.

**The fix sidesteps the active-context requirement.** `logging` accepts an exception *instance* for `exc_info`, so the host passes `exc_info=exc` — the object the by-identity stash already holds — instead of `True`. `record.exc_info` is then `(type(exc), exc, exc.__traceback__)` and both assertions hold whatever `sys.exc_info()` says. `exc.__traceback__` may be `None` if PyO3 dropped it; `test_logging` reads only `exc_info[1]`, so that is survivable — worth knowing rather than discovering at execution.

So the thing Step 5 must establish is **not** "can the binding carry the bit and the exception" — those are two plain values and a stash we already have. It is: *does the host build the record from the held instance, and does `exc_info[1]` come back as the original exception with its original `str()`?* The two `test_logging` rows answer it, and they cover both branches of `:1023-1026` — `'Attribute does not exist.'` is the `silent_variable_failure` case and `'Failed lookup for key [author] in …'` is the raising one.

**Files:**
- Modify: `crates/template-core/src/parser/resolve.rs` (`LookupOutcome::Escaped`)
- Modify: `crates/template-py/src/lib.rs` (the resolver binding)
- Modify: `python/django_rust_templates/_impl/base.py` (`Variable._resolve_lookup`)
- Test: `tests/test_interposition.py`

**Interfaces:**
- Consumes: `_native.resolve(dotted_name: str, context) -> value` (existing; raises `NotImplementedError` for a translated variable, which is M12's).
- Produces: the escaping outcome exposes the failing `bit` to the host, so the outer handler logs it.

- [ ] **Step 1: Read the current arm order and the handler**

Run: `.venv/bin/python -I -c "import pathlib;print(''.join(pathlib.Path('.django/django/template/base.py').read_text().splitlines(keepends=True)[955:1029]))"`

Expected: upstream's `_resolve_lookup`, ending in the outer `except Exception as e:` handler at `base.py:1014` that logs and then substitutes-or-raises on `silent_variable_failure`.

- [ ] **Step 2: Write the failing rows**

Add to `tests/test_interposition.py`:

```python
    def test_variable_resolution_reaches_rust(self):
        from django.template import Context, Engine
        from django_rust_templates import _native

        calls = []
        original = _native.resolve

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.resolve = counting
        try:
            Engine().from_string("{{ a.b }}").render(Context({"a": {"b": 1}}))
        finally:
            _native.resolve = original

        self.assertEqual(calls, ["a.b"], calls)

    def test_the_failing_bit_is_passed_to_the_logger(self):
        """The two rows this pins were the only failures in the recon wiring.

        `Variable._resolve_lookup`'s outer handler logs the variable that failed
        (`base.py:1014-1018`). `LookupOutcome::Escaped` did not carry it, so the
        log line had no `bit` and `test_logging` reported two failures. The bit
        now travels with the outcome.
        """
        import logging
        from django.template import Context, Engine

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("django.template")
        handler = Capture()
        logger.addHandler(handler)
        try:
            Engine().from_string("{{ missing }}").render(Context({}))
        finally:
            logger.removeHandler(handler)

        self.assertTrue(
            any("missing" in (r.args[0] % r.args[1:]) for r in records if r.args),
            [r.getMessage() for r in records],
        )
```

- [ ] **Step 3: Run the rows to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_interposition -v`
Expected: both FAIL — the transcription still runs Django's own resolver.

- [ ] **Step 4: Carry the `bit` on `LookupOutcome::Escaped`**

In `crates/template-core/src/parser/resolve.rs`, change the variant and its producers:

```rust
    /// ... (existing doc comment unchanged) ...
    ///
    /// **The failing `bit` travels with it.** Django's outer handler logs the
    /// variable that failed (`base.py:1014-1018`):
    ///
    /// ```text
    /// logger.debug(
    ///     "Exception while resolving variable '%s' in template '%s'.",
    ///     bit, template_name, exc_info=True,
    /// )
    /// ```
    ///
    /// Measured: with the bit absent, wiring `Variable._resolve_lookup` to this
    /// crate left exactly two failures, both in
    /// `template_tests.test_logging.VariableResolveLoggingTests`, both the same
    /// missing argument. Carrying it here rather than re-deriving it in the host
    /// keeps the loop's own notion of "which bit" authoritative — the host never
    /// sees the loop.
    Escaped { bit: String },
```

Then update every `LookupOutcome::Escaped` construction (`resolve.rs:334`, `:342`, `:363`, `:425`, `:441`, `:444`, `:454`) to pass the bit in scope, and the `Err(LookupOutcome::Escaped)` returns in `step_one` similarly. The bit is always the `bit` parameter of `step_one` / the loop variable of `resolve_lookup`.

Update the unit tests in `resolve.rs` (`:717`, `:746`, `:763`) from `LookupOutcome::Escaped` to `LookupOutcome::Escaped { bit: … }` with the expected bit.

- [ ] **Step 5: Expose the bit, and confirm the held exception reaches the record**

In `crates/template-py/src/lib.rs`, find the `resolve` binding and make the escaping path surface the bit to the host. Read the existing binding's error path first; the choice must match how `CallFailure::Raised` and the `pending` slot already reach Python, since the handler needs the *exception object* as well as the bit.

Then write the row that pins the `exc_info` half, because neither of Task 5's earlier rows can see it — `test_the_failing_bit_is_passed_to_the_logger` only checks the message, and the message is right even when `exc_info` is empty:

```python
    def test_the_log_record_carries_the_exception_instance(self):
        """`exc_info=True` cannot work here, and the obvious assertion hides that.

        Upstream passes `exc_info=True`, which resolves `sys.exc_info()` **at call
        time** and is `(None, None, None)` outside an active `except`. The stash is
        exactly "held, not active", so the record's `exc_info` is a truthy tuple
        of Nones: `assertIsNotNone` passes and `str(exc_info[1])` is `'None'`.
        The host therefore passes the instance — `exc_info=exc` — and this row
        asserts on the instance rather than on truthiness.
        """
        import logging
        from django.template import Context, Engine

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("django.template")
        handler = Capture()
        logger.addHandler(handler)
        try:
            Engine().from_string("{{ author.name }}").render(Context({}))
        finally:
            logger.removeHandler(handler)

        record = next(r for r in records if r.exc_info is not None)
        self.assertIsNotNone(record.exc_info[1], "exc_info held no exception")
        self.assertIn("Failed lookup for key", str(record.exc_info[1]))
```

Run it **before** the Step 6 delegation, against a hand-written handler that passes `exc_info=True`, and confirm it fails with `str(None)`. A row asserting the instance is only worth having if the `True` form is known to break it.

- [ ] **Step 6: Delegate `Variable._resolve_lookup`**

In `_impl/base.py`, replace the body of `Variable._resolve_lookup` with a delegation that preserves the outer handler verbatim, including the log line and the `silent_variable_failure` branch. Per recon finding 4, the correct call is on the joined lookups, with translation handled after the lookup as Django does it:

```python
    def _resolve_lookup(self, context):
        """...upstream docstring... **Delegated to Rust at M9.**

        `_native.resolve` takes the dotted path rather than the `Variable`,
        because the loop's notion of "which bit failed" is inside Rust and the
        host's log line needs it back. Note `".".join(self.lookups)`: `_("x")`
        sets both `translate` and `lookups`, and upstream translates the
        *result*, so translation is applied after the lookup, below.
        """
        from django_rust_templates import _native

        try:
            current = _native.resolve(".".join(self.lookups), context)
        except Exception as e:
            if getattr(e, "silent_variable_failure", False):
                current = context.template.engine.string_if_invalid
            else:
                # ...upstream's logger.debug, with the bit from the outcome...
                raise
        if self.translate:
            return self._translate(current, context)
        return current
```

The `except` body must keep upstream's log call verbatim, taking `bit` from where Step 5 put it.

- [ ] **Step 7: Rebuild and run the two rows from the recon**

```bash
.venv/bin/python -m maturin develop -q 2>/dev/null || maturin develop
.venv/bin/python -m unittest tests.test_interposition -v
.venv/bin/python -m unittest tests.template_tests_placeholder 2>/dev/null || true
scripts/run_django_suite.sh template_tests.test_logging
```

Expected: the two `VariableResolveLoggingTests` rows now PASS, and `scripts/run_django_suite.sh template_tests.test_logging` is `OK`.

- [ ] **Step 8: Run the full suite**

Run: `scripts/run_django_suite.sh template_tests`
Expected: `Ran 1547 tests in …s` / `OK (skipped=6)`.

- [ ] **Step 9: Commit**

```bash
git add crates/template-core/src/parser/resolve.rs crates/template-py/src/lib.rs python/django_rust_templates/_impl/base.py tests/test_interposition.py
git commit -m "$(cat <<'EOF'
M9: Variable._resolve_lookup delegates, with the failing bit on the wire

The third seam, and the one recon found a real defect in: wiring the resolver
left exactly two failures, both template_tests.test_logging rows, because
LookupOutcome::Escaped carried the exception and the frame but not the variable
bit Django's outer handler logs (base.py:1014-1018).

The bit now travels with the outcome, so the log line is emitted from Python
where the logger lives rather than imitated on the Rust side.

".".join(self.lookups), with translation applied after the lookup: _("x") sets
both translate and lookups, and upstream translates the result.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: The render-annotation protocol — direct-entry evidence

Recon finding 6, measured on `template_tests/test_nodelist.py:44-86`: 10 exceptions propagate through `Node.render_annotated`, 5 frames pass the origin comparison. Case 4's culprit is the inner `{% for %}` — a node nested in another node — and `(38, 57)` covers it. At M9 these are Python nodes, so the protocol holds by transcription; the work is to **pin** it, because §5.5 has Rust rendering interior subtrees from its own compiled form *later*, and that is the change that would break it.

**The ratio is not the assertion; the pairs are.** The reviewer's sharpening, and it decides how the rows must be written: if Rust attaches at the *wrong* node the 10/5 ratio is unchanged, because the `hasattr` guard admits exactly one annotation per propagating chain. A port that annotates five wrong nodes passes "five frames annotate" identically. So every row below asserts the exact `(start, end)` pair.

One adjacent fact, from the reviewer's enumeration of every `template_debug` writer in `django/`: `backends/django.py:120-121` **copies** an existing `template_debug` onto a re-raised exception. It is not a second annotator — `render_annotated` is the only render-time one — but it is a second path to a test, and being a copy it is transparent. Any backend-level assertion can tell *present* from *absent* and never *correct* from *incorrect*; do not write one and call it coverage.

**Files:**
- Test: `tests/test_base_parity.py`

**Interfaces:**
- Consumes: Task 1's interposed `base`; `.review/m9-measurements/m9_measure_render_annotated.py` as the model.
- Produces: direct-entry rows for `Node.render_annotated`'s two attachments and `Template.get_exception_info`.

- [ ] **Step 1: Write the failing rows**

Add to `tests/test_base_parity.py`:

```python
class RenderAnnotationTests(unittest.TestCase):
    """§5.2/§5.5's annotation protocol, run directly rather than through the suite.

    Measured on the five rows of `template_tests/test_nodelist.py:44-86`: 10
    exceptions propagate through `Node.render_annotated` and 5 frames pass the
    origin comparison. That ratio *is* the protocol — the innermost raising node
    wins `_culprit_node` (the `hasattr` guard) and only the frame whose
    `context.render_context.template.origin` matches attaches `template_debug`.

    At M9 the nodes are Python, so this holds by transcription. It is pinned
    now because §5.5 has Rust rendering interior subtrees from its own compiled
    form later, and an interior node rendered without a Python frame is exactly
    what stops `_culprit_node` being attached at the right level.
    """

    SOURCES = [
        ("{% badsimpletag %}", "SimpleNode", (38, 56)),
        ("{% for i in five %}{% badsimpletag %}{% endfor %}", "SimpleNode", (58, 76)),
    ]

    def test_culprit_node_is_the_innermost_raising_node(self):
        ...

    def test_template_debug_is_attached_only_when_the_origin_matches(self):
        ...
```

Fill both bodies from `.review/m9-measurements/m9_measure_render_annotated.py`'s technique — `Engine(debug=True, libraries={"bad_tag": "template_tests.templatetags.bad_tag"})`, render the source, catch the exception, assert `_culprit_node` and `template_debug` — using the *measured* `(start, end)` pairs from finding 6 rather than the placeholders shown.

- [ ] **Step 2: Run the rows**

Run: `.venv/bin/python -m unittest tests.test_base_parity.RenderAnnotationTests -v`
Expected: PASS. This confirms the transcription carries the protocol.

- [ ] **Step 3: Confirm the rows have teeth**

Temporarily remove the `hasattr(e, "_culprit_node")` guard in `_impl/base.py`'s `Node.render_annotated` (making the outermost node win instead of the innermost), re-run, and confirm `test_culprit_node_is_the_innermost_raising_node` fails naming the wrong culprit. Restore with an `Edit`, never `git checkout`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_base_parity.py python/django_rust_templates/_impl/base.py
git commit -m "$(cat <<'EOF'
M9: pin the render-annotation protocol

Node.render_annotated's two attachments are Python at M9, so they hold by
transcription; the rows exist because §5.5 has Rust rendering interior subtrees
without a Python frame later, which is what would stop _culprit_node being
attached at the right level (recon finding 6, case 4: the culprit is the inner
{% for %}, a node nested in another node).

The guard was made to fail before being trusted: removing the hasattr guard
makes the outermost node win, and the innermost-culprit row names the wrong node.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The transcription drift guard, and M9 close-out

A `# TRANSCRIBED` file is faithful only while someone re-diffs it. The docstring says "diff it against upstream on every Django bump" — nothing enforces that, and Review Focus 1 is that a silent divergence in `base.py` is invisible to a green suite. The gate itself landed in Task 0; this task finishes it, which means filling in the real spans and closing the milestone.

**Files:**
- Modify: `scripts/check_transcriptions.py`, `scripts/check.sh` (both created in Tasks 0–1)
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.3 M9 row, §10.4)
- Test: the script's own red state, measured

**Interfaces:**
- Consumes: `_impl/base.py`, `_impl/smartif.py` from Tasks 1–3.
- Produces: a gate that fails when a transcription differs from `.django/` outside its declared regions.

**Steps 1 and 2 have already happened, and not here.** `scripts/gen_impl_transcription.py` landed in Task 1 (with the file it generates) and `scripts/check_transcriptions.py` in Task 0 — because the restated parity claim's warrant *is* the transcription gate, and a warrant cannot be scheduled three tasks after the claim that leans on it. Their red states were measured in those commits. What is left here is filling in the real spans and closing the milestone.

A transcription has **two** checkable relations, and they catch different things:

| | relation | catches |
|---|---|---|
| generator | generator output == committed file | a hand edit to the generated file |
| diff guard | committed file − pinned source == declared hunks | an undeclared change, whoever made it |

Task 1 Step 1's generation buys the first relation, but only if the generator is a **committed script with a `--check` mode** — the `gen_python_word_ranges.py --check` pattern this repo already uses. A one-off heredoc, as Task 1 currently writes it, evaluates the first relation exactly once, in the commit that created it, and never again; a later hand edit then diverges silently, which is the failure the diff guard exists to prevent one layer up.

**And the marker must be anchored to text, not to a line number** (the reviewer's second condition). Task 1 Step 1 inserts after the opening `"""`, which is text-anchored and therefore stable. Any variant that inserts *at line N* would make our file a function of Django's line numbering, so the next pin bump would change our file for a reason that is not ours — the drift class this project has spent two milestones removing, reintroduced in the one place where it would look like a legitimate regeneration.

- [ ] **Step 1: Commit the generator, with `--check`**

Move Task 1 Step 1's body into `scripts/gen_impl_transcription.py`, taking the module name as an argument, and give it the `--check` mode the other generators have:

```python
"""Regenerate an `_impl` transcription of a pinned `django/template` module.

Two relations are checkable and this script owns the first: its output must
equal the committed file. The second — the committed file minus the pinned
source equals the declared hunks — belongs to `check_transcriptions.py`, and the
two together are what make a 1207-line transcription reviewable at all: the
surface becomes "these hunks differ", which is a statement someone can check,
where "we rewrote `base.py`" is not.

`--check` regenerates and diffs, like `gen_python_word_ranges.py`, so a
legitimate move (a pin bump) is fixed by re-running the generator rather than by
editing a file by hand — and a hand edit to `_impl/base.py` fails here.

The marker goes immediately after the module docstring's opening `\"\"\"`: **text,
not a line number.** Anchoring to a line would make our file a function of
Django's line numbering, so the next pin bump would change it for a reason that
is not ours.
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

MODULES = {"base": "django/template/base.py", "smartif": "django/template/smartif.py"}


def render(name: str) -> str:
    upstream = CHECKOUT / MODULES[name]
    source = upstream.read_text()
    marker = f"# TRANSCRIBED: {MODULES[name]} (Django 6.0.9)\n"
    assert source.startswith('"""\n'), "docstring shape changed; fix the insertion point"
    return '"""\n' + marker + "\n" + source[4:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("module", nargs="*", default=sorted(MODULES))
    args = ap.parse_args()

    failures = 0
    for name in args.module:
        want = render(name)
        path = IMPL / f"{name}.py"
        if args.check:
            got = path.read_text() if path.is_file() else ""
            if got != want:
                failures += 1
                print(f"{name}.py is not what the generator produces "
                      f"(run: scripts/gen_impl_transcription.py {name})")
            else:
                print(f"{name}.py matches the generator")
        else:
            path.write_text(want)
            print(f"wrote {path.relative_to(ROOT)} ({len(want.splitlines())} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the diff guard**

Create `scripts/check_transcriptions.py`:

```python
"""Every `_impl` transcription differs from upstream only where it says it does.

A `# TRANSCRIBED:` file is a claim about the pinned tree, and the claim is only
falsifiable if the diff is read. `.review/m9-recon.md` finding 3 measured the
consequence of not reading it: a `base.py` that transcribes wrongly, or not at
all, keeps all 1547 tests green.

So each transcription declares its differences, and this script asserts the set
of differing hunks **equals** the declared set. An undeclared edit fails; a
declared edit that is no longer present also fails, because a stale declaration
is a claim nothing re-measures — the same failure this repo's anchor manifest
already guards against.
"""

import difflib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

# module -> (upstream path, [(ours_line, upstream_line)] declared differences)
DECLARED = {
    "base.py": (
        "django/template/base.py",
        [
            (3, None),  # the `# TRANSCRIBED:` marker line, ours only
            # `Template.compile_nodelist` delegation: the body replaced at M9.
            # Replace with the real span once Task 2 lands.
        ],
    ),
    "smartif.py": (
        "django/template/smartif.py",
        [(3, None)],
    ),
}


def differing_lines(ours: list[str], theirs: list[str]) -> list[tuple[int, int]]:
    """(ours_i, theirs_i) for every line the two do not share, 1-based."""
    out = []
    matcher = difflib.SequenceMatcher(a=ours, b=theirs, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            out.append((i1 + k + 1 if i1 + k < i2 else None,
                        j1 + k + 1 if j1 + k < j2 else None))
    return out


failures = 0
for name, (upstream, declared) in DECLARED.items():
    ours_path = IMPL / name
    theirs_path = CHECKOUT / upstream
    if not ours_path.is_file():
        print(f"MISSING: {ours_path.relative_to(ROOT)}")
        failures += 1
        continue
    ours = ours_path.read_text().splitlines()
    theirs = theirs_path.read_text().splitlines()
    actual = differing_lines(ours, theirs)
    declared_set = set(declared)
    if set(actual) != declared_set:
        failures += 1
        print(f"\n=== {name}: transcription differs outside its declaration")
        for row in sorted(set(actual) - declared_set, key=lambda t: (t[0] or 0)):
            print(f"  undeclared  ours:{row[0]}  upstream:{row[1]}")
        for row in sorted(declared_set - set(actual), key=lambda t: (t[0] or 0)):
            print(f"  stale       ours:{row[0]}  upstream:{row[1]}")
    else:
        print(f"{name}: {len(actual)} declared difference(s), all accounted for")

if failures:
    sys.exit(f"{failures} transcription(s) failed")
```

- [ ] **Step 3: Measure the red state before trusting it**

Deliberately edit a line in `_impl/smartif.py` that is not the marker (e.g. change a docstring word), run `scripts/check_transcriptions.py`, and confirm it prints an `undeclared` row and exits non-zero. Restore with an `Edit`.

Then delete a declared row (remove the marker line from `DECLARED["smartif.py"]`) and confirm it prints `stale`. Restore.

Both red states, measured — a guard whose failure has been observed is the standard this project holds gates to.

- [ ] **Step 4: Wire it into `check.sh`'s fast path**

In `scripts/check.sh`, after `step "suite counts carry their provenance"`:

```sh
step "transcriptions differ from upstream only where declared"
# `_impl/base.py` and `_impl/smartif.py` are claims about the pinned tree. The
# suite cannot falsify a wrong transcription (measured: a verbatim copy and a
# wrong one are both green), so the diff is the instrument. Fast path: it reads
# text and needs only `.django/`.
"$PROJECT_ROOT/.venv/bin/python" -I "$HERE/check_transcriptions.py"
```

- [ ] **Step 5: Fill in the declared hunks with the real spans**

Run `scripts/check_transcriptions.py` and copy the *actual* differing rows for `base.py` into `DECLARED`, each with a comment naming the edit. Expect: the marker line, plus the `compile_nodelist` body and the `_resolve_lookup` body from Tasks 2 and 5. Every other line must be identical to upstream — that is the whole point.

- [ ] **Step 6: Run the full check**

Run: `scripts/check.sh`
Expected: `all checks passed`, including the new step, and `Ran 1547 tests in …s` / `OK (skipped=6)` from `scripts/run_django_suite.sh template_tests`.

- [ ] **Step 7: Move the acceptance label off its M9 caveat**

`scripts/check.sh:72-77` currently reads "Not '(rust engine)' until M9 interposes `base` — measured 2026-10-09, this suite calls `_native` zero times". Re-measure that claim now: with Tasks 2, 3 and 5 landed, the suite *does* call `_native`. Update the comment to say what is now true, and re-run `.review/m9-measurements/m9_measure_render_blast_radius.py` (it prints the pre-flight NOTE if `base` is not ours) to confirm the seams are live.

Do not change the step's *label* to "(rust engine)" unless the measurement supports it for the whole suite — M10's `engine.py` is still Django's, so some paths may still be stock.

- [ ] **Step 8: Update the spec's M9 row and close it**

In `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md`, §10.3's M9 row and §10.4: record the closing commit, the meter at **23**, the three seams with their measured sizes (parse 1170, render 964, resolve), and the two things M9 proved but did not implement (§5.5's interior-subtree rendering, deferred; `render_value_in_context`'s equivalence result from Task 4).

- [ ] **Step 9: Commit**

```bash
git add scripts/check_transcriptions.py scripts/check.sh docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md
git commit -m "$(cat <<'EOF'
M9: gate transcription drift, and close the milestone

A `# TRANSCRIBED:` file is a claim about the pinned tree and the suite cannot
falsify it — measured, a verbatim copy and a wrong one are both green. So each
transcription now declares its differences and check_transcriptions.py asserts
the differing hunks equal the declaration: an undeclared edit fails, and so does
a stale declaration.

M9 closes at 23 modules, with the parse (1170 tests), render (964) and resolve
seams delegated to Rust and each pinned by a direct-entry row, since
interposition alone is invisible to every count this project accepts.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage.** §10.3's M9 done-when: `test_base`, `test_parser`, `test_nodelist` (incl. `ErrorIndexTest`) and `test_smartif` are real signal — Tasks 1–3 flip them onto our modules; `ErrorIndexTest` specifically exercises `Template.get_exception_info` from `compile_nodelist`, which Task 2's `extra_data`/debug row covers. The full suite unchanged — each task ends on `scripts/run_django_suite.sh template_tests`. §5.2's two open obligations: `render_value_in_context` (Task 4), the annotation protocol (Task 6). §5.5's interior-subtree rendering is explicitly *deferred* and stated as such in Task 6 and Task 7 Step 7 — it is not M9's to implement.

**Gap accepted:** M9 does not port any render path to Rust. That is deliberate: §5.5 puts the Python node tree on the render path, so M9's job is interposition and seam delegation, and the milestone is sized around that.

**Placeholder scan.** Task 6 Step 1 shows two row bodies as `...`, which is a plan failure — the executor must fill them from `m9_measure_render_annotated.py`, and the step says so explicitly with the technique named, rather than leaving them vague. Task 4 Step 2 contains a deliberately wrong identifier (`SafeStringLike`) with the correction spelled out in the following paragraph; that is a defect in this plan, and the executor should write the import at module top and drop `SafeStringLike` entirely. Both are called out rather than hidden.

**Type consistency.** `_native.compile_template`, `_native.parse_filter_expression` and `_native.resolve` are named once each and wrapped by the same counting-closure pattern. `LookupOutcome::Escaped { bit: String }` is introduced in Task 5 Step 4 and consumed in Steps 5–6 with the same field name. `_shim.unshimmed()` returns a container whose length is asserted as 24 (Task 1) then 23 (Task 3).

**Review Focus.** 1 → Task 0's guard (it landed there, not in Task 7). 2 → Tasks 2, 3, 5's counting rows. 3 → Task 4 Step 2. 4 → Task 4 Step 2's second row. 5 → Task 5 Steps 2 and 7.

**Executed-so-far.** Task 0 is done and committed at `a105469`, and executing it corrected five of its own claims — the failure count and whose failures they were, the loaders row's cause *and* remedy, a restated row that would have failed on `getsource` vs `getsourcefile`, the guard-row enumeration, and the transcription gate's schedule. Each correction is recorded in Task 0 with the measurement that produced it, on the principle this milestone keeps re-learning: the decision is usually right and the reason attached to it is usually reached for rather than measured, so the reason needs a pointer to its evidence before it is worth anything.

Task 1 is done and committed at `2eedab2`. Its review produced a follow-up on
the transcription guard, all of it measured before it was written down:

- **the compared path is measured, not inferred.** `tests/parity_target.py`
  took the compared path to be the lines of the one target the row names. The
  measurement (`.review/m9-measurements/m9_measure_parse_path.py`, instrument in
  `tests/compared_path.py`) finds 28 functions spanning lines 116-1209 — nearly
  the whole module — because `Parser.parse` calls `extend_nodelist`,
  `compile_filter`, every tag's compile function and `token_kwargs`. The spans
  are pinned in `scripts/check_transcriptions.py`'s `COMPARED_PATH` and
  re-measured by `tests/test_interposition.py`, the same pinned-number-plus-live-
  instrument shape as the module meter;
- **the instrument was import-order dependent, and that was a defect in the
  measurement, not a finding.** The first version wrapped the defining module
  only, so a function another module reached with `from ... import token_kwargs`
  was called through the consumer's binding and never counted: `token_kwargs`
  appeared in one run and vanished from the next, the only difference being
  whether the wrapping happened before the parity module imported. The context
  manager now rebinds consumers too;
- **regions over executable lines must declare a snippet naming every one of
  them.** The rule refused this milestone's own first declaration, which named
  one of a region's three imports;
- **the `# TRANSCRIBED:` marker is parsed** and must name this file's upstream
  path and the checkout's own version (`pinned_version()` reads it from
  `.django/django/__init__.py` with `ast`, importing no Django). Spec §6 has
  been corrected: it said `# TRANSCRIBED FROM django/...`, which no file ever
  used;
- **a false reason was corrected.** The `__init__.py` region comment said
  upstream "does not need" its `# NOQA isort:skip` markers because it "has no
  linter"; the diff shows the direction is the other way — upstream *has* them
  and the transcription strips them, exactly as spec §6 records;
- **the guard's own ordering was wrong**, found by measuring its teeth
  (`.review/m9-measurements/m9_guard_teeth.py`, six scenarios): the compared-path
  clause sat after a `continue` in the snippet logic, so the most serious
  failure available was masked by a lesser one. The teeth script is the
  evidence; the baseline is green and all six scenarios refuse.

Task 2 is done and committed at `fdb4eb0`. Delegating `Template.compile_nodelist`
to the Rust parse met its gate on the first full run — `Ran 1547 tests in …s` /
`OK (skipped=6)`, unit tests `OK`, `scripts/check.sh` → `all checks passed` — but
the run between the plan and that gate exposed six things the plan did not have:
five omissions in its own Step 4 code, and one defect in a **test** rather than
in this milestone's code. Each was measured, and each is recorded here because
the plan's Step 4 assumed work that did not exist while its Step 6 expectation
was right.

**(1) The plan has no exception-conversion step anywhere.** Rust raises one
class for everything a parse rejects; Python owns the exception *object* (design
decision 2). Until M9 the suite's parses went through Django's `Parser`, which
raises `TemplateSyntaxError` itself, so nothing ever had to convert. M9 makes
`compile_nodelist` the suite's only parse path and, without a conversion, the
first run dies three ways at once, all measured: upstream's `except` arm hands
`e.token` to `get_exception_info`, which reads `token.position` off the wire
tuple — `AttributeError: 'tuple' object has no attribute 'position'`;
`assertRaises(TemplateSyntaxError)` sees a class it does not know; and Django's
parallel runner cannot pickle the result — `Can't pickle <class
'_native.UnimplementedTag'>: No module named '_native'`. The conversion is
`render.django_exception`, it lives in `render.parse_template` (the shell's only
source-to-`Template` path) and **not** in `compile_nodelist`, because putting it
there would have meant editing upstream's method rather than transcribing it —
the one thing M9 must not do. `lib.rs:952` had said "the shell's conversion is
M9's" since M6 and the plan never picked the obligation up.

**(2) The Rust lexer's debug flag was hardcoded.** `compile_template` called
`parse_source(py, source, false, …)`. Upstream reads `self.engine.debug` and
chooses `DebugLexer` over `Lexer` (`base.py:183-186`); the positions it turns on
are what every downstream debug path needs and none of them can recover them.
Measured with the flag false: `Engine(debug=True).from_string("{% badtag %}")`
reaches `Template.get_exception_info`, which does `start, end = token.position`,
and dies with `TypeError: cannot unpack non-iterable NoneType object`.

**(3) `ShellHost.has_tag` read the registry's *keys* as its implementations.**
`_rust_owned` was `set(_native.builtin_tag_names())`, which includes the two
stub names (`partial`, `partialdef`) whose bodies raise `Unimplemented`; the
identity clause then declined them on the host's behalf. Measured: **245 of the
1547**, every one `'partial' is a built-in tag this engine does not implement
yet (M13)` where `syntax_tests.test_partials` expected a render or Django's own
syntax error. The subtraction `names() - stubs()` is not a new rule — it is what
`tests/test_parser_parity.py:532` and `tests/test_tag_inventory.py:296` already
did, and the host was the one place reading the keys as the registry.

**(4) A host exception has to be re-raised by identity.** After (1)–(3), five
rows still failed — `test_include_fail1`, `test_include_error07`,
`test_include_error09` and `test_compile_tag_error` twice. A tag whose own code
raised had its object replaced by a class-only rebuild, losing `__traceback__`
and `_culprit_node`. `ShellHost.last_error` now keeps the object and
`django_exception` re-raises it when `error.class_name` matches — which is what
Django does (`base.py:583-585`).

**(5) `RenderParityTests`' "stock Django" oracle became Rust, and nothing could
see it.** `engine.from_string(source).render(...)` was stock Django until this
task interposed `base`; afterwards it builds *our* `Template`, whose
`compile_nodelist` calls `render.parse_template` — the same function
`render_string` calls — so every row in that class compared Rust against Rust
while the module docstring still claimed the comparison was evidence about the
Rust parse. Measured rather than reasoned
(`.review/m9-measurements/m9_measure_render_parity_oracle.py`): both sides enter
`render.parse_template` exactly once. The repair is `tests/test_render_parity.py`'s
`django_render`, which builds its tree from Django's own `Parser` — the claim
`tests/parity_target.py` already states for the other harnesses — and a row,
`test_the_python_side_is_not_the_rust_parse`, that pins the call counts so
"someone simplifies it back" is a change a test can see. This is Review Focus 2
arriving through a door the plan did not list: the delegation is not removed,
the *comparison* is.

**(6) The transcription guard had to be re-measured, not just re-run.** Adding
twelve lines to `compile_nodelist` moved all 28 `COMPARED_PATH` spans, so
`tests/test_interposition.py::test_the_compared_path_is_the_measured_one` was
restated to re-measure per module from the installed module rather than compare
a pinned file, `scripts/check_transcriptions.py` gained `module_of()` (the
dotted path derived from the declared upstream path, so the two cannot
disagree), and the guard gained a clause the teeth script had made obvious: the
`COMPARED_PATH` key set must **equal** `DECLARED`'s, because a transcribed module
with no entry has its declared regions checked by nothing. Two further defects
surfaced in the guard itself, both measured: `executable_lines` took a compound
statement's whole span, so a 15-line region reported all 15 lines executable
because one `import` sat inside a `FunctionDef`; and the snippet comparison
matched unstripped lines, so an indentation difference read as a missing line.

Task 3 is done and committed at `b34b2b0`, with the defect its own row exposed
fixed first at `8c76ca0`. The transcription itself needed nothing: the generated
file is 217 lines against upstream's 215, `difflib` reports one `delete` opcode
(ours 2-3, upstream 2-1) and 215 equal lines, so the whole declared difference is
the marker plus its blank separator. Three things the plan got wrong, and one it
could not have known:

**(1) Step 2's symbol claim is wrong, and its script is broken.** The step says
"215 lines, 8 top-level symbols" and expects `operator` and `TOKEN_*` among them.
Measured: **neither name exists anywhere in the file**, ours or upstream's.
There are 7 distinct top-level definitions — `EndToken`, `IfParser`, `Literal`,
`OPERATORS`, `TokenBase`, `infix`, `prefix` — across 8 top-level *statements*,
because `EndToken = EndToken()` (line 153) rebinds the class to its singleton
instance. The runtime namespace holds 9 names: those 7 plus `key` and `op`, the
loop variables `for key, op in OPERATORS.items()` (line 115) leaves behind. So
"8" was right about statements and wrong about everything it was attached to.
The step's script is worse than its prose: its comprehension ends
`(isinstance(n, ast.Assign) for _ in ())`, a generator object in a boolean
position, which is always truthy — so the filter admits every statement and the
assertion `not (theirs - ours)` passes for any file at all. It was not run as
written; the measurement above is `ast.parse` plus `exec` into a throwaway
module, which is what the step was reaching for.

**(2) Step 6's premise is false, and the row is renamed and rewritten because of
it.** The step asks for a row pinning that the interposed `IfParser` reaches
`_native.parse_filter_expression`, and anticipates it failing "because the shell
reaches Rust through a *different* binding". The failure is not the binding — it
is that **nothing is reached at all**. Measured:
`Engine().from_string("{% if a and b %}x{% endif %}")` calls
`_native.parse_filter_expression` **zero** times and `smartif.IfParser.parse`
**zero** times, and a corpus of `{% if %}` sources with `and`, `not`, `==`, `<`
and `or` reaches the same six functions and no operator method. M5 moved the
whole grammar into `crates/template-core/src/parser/smartif.rs`; the parse seam
drives it inside `compile_template` and the shell rebuilds the tree with
`if_condition_to_py` (`template-py/src/lib.rs:667`). A row named after the
step's premise would have asserted something untrue about a call that does not
happen, so it is `test_the_if_condition_tree_comes_from_rust_not_the_python_grammar`
and pins the pair: `compile_template` ran, the Python grammar did not, and the
`IfNode`'s condition — plus `defaulttags.TemplateLiteral`, Django's own leaf in
the *unshimmed* `defaulttags` — is an object our file defines.

**(3) A `COMPARED_PATH` blind spot on this module, for Task 7 to close.**
`measure` can only wrap functions reachable from the module namespace and its
classes. `smartif` builds its operator classes inside `infix()`/`prefix()` and
binds them into the `OPERATORS` dict, so the methods that actually evaluate
`and`, `or`, `not` and every comparison are objects the instrument never wraps —
`TokenBase.led` and `op.led` are defined and never entered, and `op` is not an
operator at all but the loop variable from (1). A region declared over those
methods would not be caught by clause 4. It is safe today because `smartif`'s
only declared region is lines 2-3, but the hole is real and Task 7 owns it. This
is the "what class of dependency can this metric not see" question asked of a
new metric, and the answer here is *decorator-generated classes*.

**(4) The shim re-executed modules, and Task 3's row is what caught it.**
`install()`/`uninstall()` dropped every `django.template` module and imported it
again, so a cycle produced new class objects while everything holding the old
ones kept them. Two failures from one cycle, both measured:

    render.TemplateSyntaxError is django.template.exceptions.TemplateSyntaxError
    -> False
    host.django_own_tags()["if"] is defaulttags.register.tags["if"]  -> False

The first makes `assertRaises(TemplateSyntaxError)` miss what
`render.django_exception` raises. The second is the serious one: `django_own_tags()`
is frozen at import *by design*, so the re-executed `defaulttags` published new
tag callables, `ShellHost.has_tag` judged `{% if %}` to be Django's, and Rust
stopped parsing the condition — Task 3's row failing for exactly the reason it
exists. The fix (`8c76ca0`) executes each module at most once per process and
restores from a cache, and `_place()` sets the parent package's attribute too,
without which `sys.modules["django.template"]` is ours while `django.template`
is still Django's.

None of this was visible in the accepted run, and the reason is ordering:
`run_django_suite.sh` installs once at interpreter start, and `unittest discover`
happens to run `test_interposition` before `test_shim` because "i" sorts before
"s". A row that is green because of the alphabet is the same failure mode as a
region declared to pass, and it is why the fix landed before the row that found
it rather than after.

**(5) The guard could not see an undeclared transcription.** Measured on the
tree before `b34b2b0`: `python -I scripts/check_transcriptions.py` returned
**exit 0** with `_impl/smartif.py` on disk and absent from `DECLARED`, because
every clause iterates the files it was told about. The clause added in that
commit scans `_impl/*.py` for the marker and requires the key sets to be equal,
mirroring the `COMPARED_PATH` clause; it was verified to fail on a planted
marked file before it was committed.

### Task 6 is not a pinning task — its premise was false, and the protocol was broken on both sides

Executed ahead of Tasks 4 and 5, because the reviewer's Task 2 verdict asked
whether `django_render` could be trusted from Task 6 and the answer turned out to
be "no, and neither could `render_string`". Measurement:
`.review/m9-measurements/m9_measure_render_annotation_wiring.py`.

Task 6 above opens "At M9 these are Python nodes, so the protocol holds by
transcription; the work is to **pin** it". Three things were wrong with that
sentence, and each was measured rather than argued:

**(1) `compile_template` never passed `origin` to `template_for`, so the
comparison never held on the shell's own path.** The Rust docstring already
claimed the opposite — "every materialised node's `node.origin`, and the
`Template.origin` of the tree `template_for` builds" — and the call handed
`origin` to `materialise` and to nothing else. Every node therefore carried the
real `Origin` while the `Template` carried `None`, `Node.render_annotated`'s
equality (`base.py:1058-1062`) was false for every exception, and
`render_string` produced **no `template_debug` at all** where Django produces
one. Nothing failed: the exception simply propagated bare. It is invisible to the
whole Django suite for a structural reason worth stating — `Template.__init__`
builds the object that renders, `compile_nodelist` returns only `nodelist` from
the one `template_for` builds, and that object is discarded — so only a direct
caller of `parse_template`/`render_string` ever saw it.

**(2) `template_for` set `source = ""`, which made the repaired protocol lie
politely.** With the origin threaded, `get_exception_info` no longer failed —
it returned a full `template_debug` whose `start`/`end` were correct and whose
`before`/`during`/`after`/`source_lines`/`line`/`total` were empty or wrong.
Measured, both states, side by side:

    origin=None,  source=""  -> no template_debug attached at all
    origin=set,   source=""  -> start 9, end 25, line 0, total 2, during ""
    origin=set,   source=SRC -> start 9, end 25, line 2, total 5, during "{% for x in a %}"

`template_for` gained a `source` parameter and `compile_template` passes it; the
attribute list is now exactly `Template.__init__`'s.

**(3) `render.parser_for` always built a plain `Lexer`, so the Python side of
`tests/test_render_parity.py` destroyed its own annotation.** Upstream reads
`self.engine.debug` at the top of `compile_nodelist` to choose `DebugLexer`
(`base.py:183-186`); `parser_for` transcribes the `Parser` construction and
omitted that arm. The only difference is `Token.position`, and
`get_exception_info` unpacks it — so `django_render` died *inside* its own
`except` with `TypeError: cannot unpack non-iterable NoneType object`,
**replacing** the exception it was annotating. Invisible on the Rust path, which
lexes for itself and materialises positioned tokens (measured: a `{% for %}` node
at `(9, 25)`), so it bit exactly the callers that parse *with* this parser — the
parity harnesses.

**(4) What replaces the plan's Task 6 Step 1 and Step 2.** The rows are not
"pin the protocol"; they are `tests/test_render_parity.py`'s
`test_the_oracle_template_has_djangos_attributes` (the oracle's `Template`
carries Django's whole attribute surface, `vars()` set-equality plus `origin` and
`source` by value) and `test_both_sides_annotate_a_render_error_the_same_way`
(the two sides' `template_debug` dicts are equal, *and* the fields are pinned to
their literals, because two sides that drifted together would agree about
nothing). Both were made to fail before being trusted: setting
`template.origin = None` removes the annotation from both sides — caught by the
literals, not by the comparison, which is why the literals are there — and
reverting `parser_for` to `Lexer` breaks the comparison, which is the one-sided
case. Task 6 still owes the *direct-entry* rows its Step 1 describes
(`_culprit_node` innermost-wins, the five measured `(start, end)` pairs from
`m9_measure_render_annotated.py`); what it no longer owes is a claim that the
protocol works by transcription.

**(5) The reviewer's two other questions, answered by measurement rather than by
edit.** *Is `host.last_error` stale-able?* No, structurally: `ErrorKind::Host` is
built at one place (`crates/template-py/src/lib.rs:1104`, called only from the
`call_tag` boundary at `crates/template-py/src/lib.rs:1251`), `last_error` is
assigned at one place (`python/django_rust_templates/host.py:243`), and a host
error is **terminal** — the parse loop's arm is `Err(error) => Err(error)`
(`parser/mod.rs:394-403`) and its caller is `?`, so the failing call is
necessarily the last to have touched either field. The host is per parse as well.
Recorded in `django_exception`'s docstring, with a note that clearing on read
would hide the regression it would be guarding against, rather than as a row
about a negative that is structural. *Does `module_of()` resolve a
declared-but-not-transcribed module?* Yes — it resolves to **Django's** file,
which is what the fallback is for, so the row now asserts `"_impl" in
module.__file__` before measuring. `check_transcriptions.py` catches the case,
but it is a later step of `check.sh` than the unit suite, so a bare
`python -m unittest` had this row answering about the wrong module in silence.

**(6) The registry's two questions need two names — carried to Task 7.** The
reviewer's remark that `builtin_tag_names()` / `builtin_tag_stubs()` confusion
has now happened three times is right and is not fixed here; subtraction at the
call site is the workaround, and Task 7 owns the rename.

### Task 6's Step 3 predicted teeth the rows do not have, and the pairs are invariant under them

The rows landed in `tests/test_base_parity.py::RenderAnnotationTests`, three of
them, from `m9_measure_render_annotated.py`'s technique. What the *plan* said
about them was wrong in a way worth recording, because it is the third
"asserted teeth" near-miss in M9 and the first one caught before commit.

**(1) Step 3's mutation does not make the row it names fail.** Step 3 says:
remove `hasattr(e, "_culprit_node")` in `_impl/base.py`'s `Node.render_annotated`
"making the outermost node win instead of the innermost", re-run, and confirm
the innermost-culprit row fails. Measured: removing it **does** move
`_culprit_node` from the inner `{% for %}` node to the outer one — and leaves
`template_debug`'s `(38, 57)` **unchanged**, because the second guard
(`context.render_context.template.origin == e._culprit_node.origin`) still picks
the same frame. A row asserting only the span therefore stays green, and the
mutation is invisible to it. Step 3 was written for a different row than Step 1
produces.

The first probe of this appeared to show *no* change at all, and that was the
probe's fault, not the guard's: the `hasattr` line had been left inside the
monkeypatched replacement, so the mutation under test was never applied.
Re-measured with the guard genuinely removed.

**(2) What the rows assert instead is node identity.** Row 1 asserts
`assertIs(error._culprit_node, loops[1])` and `assertIsNot(…, loops[0])` — the
*nested* node, not the outer one — alongside the spans. Two mutations were run
against the finished rows and restored byte-identical (`cmp`):
**A**, removing the `hasattr` guard, fails rows 1 and 2 (row 2 with
`'parent.html' != 'child.html'`); **B**, removing only the origin comparison's
*choice of frame*, fails row 2 alone. Row 1 is the mutation-A row; row 2 is the
row that needs both guards; the flat case and the `debug=False` case carry the
other half of each comparison.

**(3) The plan's sharpening needs one more notch.** It says "the ratio is not the
assertion; the pairs are". Measured, the pairs are **also** invariant under both
guards — so for the guard question they are not teeth either. What they *are* is
exactly what Django's own `ErrorIndexTest` (`tests/template_tests/test_nodelist.py:44-86`)
already pins, which is why the accepted 1547-test suite is blind to both guards:
the suite asserts the same `(start, end)` pairs and passes with either guard
removed. These rows exist for the part the suite cannot see, and the part that
can see it is identity plus the frame the origin comparison picks.

**(4) The fixtures are the tree's, not invented.** `NESTED` and `FLAT` are cases
4 and 5 of `test_correct_exception_index` (`(38, 57)` and `(18, 37)`), and the
extends/block fixtures `PARENT`/`CHILD` reproduce the natural origin-mismatch
case, where the **third** frame annotates and the innermost is skipped — the
case where a port that annotated at the innermost frame would look right on the
spans and be wrong on the origin.

**(5) `ErrorIndexTest`'s five rows were also transcribed into the anchor
manifest** (`tests/template_tests/test_nodelist.py:44-86`, now carrying a start
and end snippet rather than the bare span it had), because these fixtures are
only defensible if they can be re-read off the tree.

### Task 4's four predictions, all four of them measured and three of them false

Task 4 is `render_value_in_context`, which §5.2 leaves as an open obligation
because it is a *helper* rather than a class: interposing `base` puts it on the
render path whether or not anything is written about it. The task's deliverable
is `tests/test_base_parity.py`, and the body it pins turned out to be the least
interesting part of it.

**(1) Step 1 quotes a docstring that is not in the pinned tree.** The step
prints "This means wrapping them in a SafeString if necessary…" as the source of
truth. 6.0.9's actual docstring, transcribed verbatim at
`python/django_rust_templates/_impl/base.py:1129-1134`, reads "This means
escaping, if required, and conversion to a string. If value is a string, it's
expected to already be translated." The **body** the step prints is right, which
is what would have made the mistake expensive: a row written from the quoted
docstring asserts nothing about the code that runs.

**(2) Step 2's `test_it_is_our_function` asserts on `__module__`, which this
repository already documented as the wrong instrument.**
`tests/test_interposition.py::test_the_node_classes_are_defined_by_our_file`
records the measurement: the shim loads our file *under Django's name*, so
`render_value_in_context.__module__` is `django.template.base` and is *supposed*
to be. The row uses `inspect.getfile` and says why.

**(3) Step 2's row is false as written.** The step supplies a placeholder type
name and instructs the implementer to replace it with `SafeString` and assert
`type(rendered) is str` — on a `mark_safe`d input. Measured:
`type(rendered)` is `SafeString`, because `SafeString.__str__` returns `self`
(it has to, so that `str()` and f-strings cannot launder safety). The row raises
`AssertionError: <class 'SafeString'> is not <class 'str'>` on the first input
anyone would reach for.

What replaced it is the contrast the arm actually makes, on an input the
escaping is visible on: autoescape off returns `str`, on returns `SafeString`,
same input; and the marked value is kept in the row as the *falsifier* of the
naive claim rather than as the claim.

**(4) Step 4's prediction is false, in both of its clauses.** The step says that
replacing `issubclass(type(value), str)` with `isinstance(value, str)` "should
still pass", and Step 2's docstring draft says the two "agree on every `str`
*instance* and disagree on nothing else either". Measured, both are wrong: an
object whose `__class__` *property* returns `str` while its type does not — which
is exactly what `django.utils.functional.lazy` produces, so this is not a
contrived class — is `isinstance` of `str` and not a subclass of it. Give it an
`__html__` and the two forms produce **different output**, not merely a different
intermediate: upstream escapes `str(value)`
(`'&lt;…Pretender object at 0x…&gt;'`), the `isinstance` form passes the object
straight to `conditional_escape`'s `__html__` arm (`'<raw>'`).

So the row the plan did not anticipate is
`test_the_conversion_test_is_issubclass_of_type_not_isinstance`, with both
literals, and `test_the_rows_can_fail` runs the mutation *inside the suite* so
the divergence is an executable fact rather than a paragraph. Teeth confirmed the
project's way, on a `/tmp` copy of `_impl/base.py` with the expression replaced:
three rows red, the two literals among them.

**(5) The plan treats it as one function with one call site; it has four, and two
of them are copies.** `render_value_in_context` is called 6505 times across the
accepted `template_tests` run, from 542 distinct tests, and the callers are split
6071 from frames named `render` (`VariableNode.render` at
`python/django_rust_templates/_impl/base.py:1161`, `CycleNode.render` at
`django/template/defaulttags.py:114`, `TranslateNode.render` at `django/templatetags/i18n.py:91`) and 434 from a
nested `render_value` inside `{% blocktranslate %}` (`django/templatetags/i18n.py:185`).
`.review/m9-measurements/m9_measure_render_value_blast_radius.py` is the
measurement, and it names its metric before printing: **calls**, not lines; the
whole run, not one module; attributed by calling frame, because a single total
would hide which path does the work.

The two out-of-module callers bind it with `from django.template.base import
render_value_in_context` **at module scope** — a copy taken when *they* were
imported, which nothing done to `django.template.base` afterwards can change.
Measured in both orders: with `install()` first (the order `check.sh` establishes
for every unit-test process, via the `sitecustomize` on `shimpath`) both
bindings are ours; import `Engine` and `defaulttags` *before* `install()` and
they stay Django's while `django.template.base` resolves to `_impl/base.py`, with
`is` between the two `False`. `{% cycle %}`, `{% firstof %}` and `{% translate %}`
would then render through Django's copy while every `VariableNode` went through
ours — the interposition silently partial, one module out, and nothing raised.

`tests/test_base_parity.py::test_the_modules_that_copied_it_hold_ours` pins the
shipped order, and Task 7 owns the general form: **any module-level binding of a
Django class is a capture of the current universe**, and it is correct only while
no install or uninstall follows. It is mechanically searchable
(`from django.template… import <CapitalisedName>` at module level in `python/`
and `tests/`), and its symptom has been a silent wrong answer every time.

**(6) The suite's settings are first-writer-wins, and this file's name is a
sort order.** Not a prediction the plan made — a defect the plan's own Step 2
would have written, caught by running it. The first version of the test class
configured ``USE_I18N=False``, copying the four modules that do
(`tests/test_filter_rebinding.py`, `tests/test_render_parity.py`,
`tests/test_host_tags.py`, `tests/test_parser_errors.py`). Measured: four rows in
`tests/test_filter_parity.py` went red — `yesno/locale-translates-the-default-
arguments` rendered `'maybe'` against its pinned `'Vielleicht'`, and
`date/german-default-format` `'8. October 2026'` against `'8. Oktober 2026'`.

The value is not the cause; the **ordering** is. ``settings.configure`` is a
no-op once settings are configured, and `unittest discover` walks modules
alphabetically, so `filter_probe.ensure_settings()` — which needs `USE_I18N=True`
*and* `django.setup()` before a catalog can be read at all — always won, because
`test_filter_parity` sorts before every other module that configures.
`test_base_parity` sorts before *it*, so the same line that is harmless in four
other files silently changed another module's expectations. The class now calls
`filter_probe.ensure_settings()`, guarded on `settings.configured`, so whichever
module arrives first the answer is the suite's answer.

This is the third time in M9 that a row has been green or red because of the
alphabet — `_shim.py`'s own comment records the second, where
`unittest discover` happened to run `test_interposition` before `test_shim` — and
Task 7 should decide whether the suite wants a single explicit settings
configuration rather than five guarded ones racing.

### Task 5's "two failures, both the same defect" is two defects, and the handler could not stay in Python

Task 5 is the resolver seam, and it is the one task in M9 that recon found a
*real* defect in rather than an obligation. The defect is real. Everything the
plan says about it is not, and the corrections below are the difference between
a fix and a fix that leaves one of the two rows red.

**(1) Recon finding 4's "exactly two failures, both the same defect" — measured,
they are two defects that share a symptom.** Both are in
`template_tests.test_logging.VariableResolveLoggingTests`, and both read as "the
record is wrong", which is why one reading covered them:

  - **`test_log_on_variable_does_not_exist_not_silent`** —
    `Variable("article.author").resolve({"article": {"section": "News"}})`. The
    key is *missing*, so Django reaches `raise VariableDoesNotExist(` at
    `base.py:989` — which is **inside** the outer `try` opened at `base.py:958`.
    The handler at `base.py:1014` therefore catches it, logs it, and re-raises.
    A port that treats "missing" as "already decided, nothing left to do"
    produces the right exception, with the right message, and **no log record at
    all**: `assertLogs` reports "no logs of level DEBUG or higher triggered on
    django.template". This defect is a *missing log*.
  - **`test_log_on_variable_does_not_exist_silent`** — `Variable("article").resolve(TestObject())`,
    whose `article` property raises `SilentDoesNotExist`. The log fires either
    way; what fails is `str(log_record.exc_info[1])`, expected to be
    `"Attribute does not exist."`. This defect is a *wrong `exc_info`*.

Fixing either one leaves the other red, so the plan's Step 5 — written from the
recon — would have fixed the second and reported the first as a new discovery.
The two are now separate rows, `ResolverDelegationTests.test_the_failing_bit_is_passed_to_the_logger`
(the message, which is the `Missing` defect) and
`test_the_log_record_carries_the_exception_instance` (the instance), so that a
regression in one cannot hide behind the other.

**(2) Step 6's sketch invents an attribute and a method that do not exist.**
The step prints:

```python
        if self.translate:
            return self._translate(current, context)
        return current
```

`_resolve_lookup` has no such tail and `Variable` has no `_translate`. The
`translate` branch lives in `Variable.resolve`, one frame out, at upstream
`base.py:932-941` (`isinstance(value, SafeData)`, `value.replace("%", "%%")`,
`pgettext_lazy`/`gettext_lazy`). Both `_impl/base.py`'s delegation and this plan
say translation is applied *after* the lookup, which is true; the sketch says it
by moving it *into* the lookup, which would duplicate a branch that is still
there and still upstream's. `Variable.resolve` is untouched at M9 and remains
outside `COMPARED_PATH`'s reach for exactly one reason: it is not declared, and
the guard's clause on unreached functions is what keeps it honest.

**(3) The handler could not stay in Python, and the bit's transport is why.**
Step 5 says "find the `resolve` binding and make the escaping path surface the
bit to the host … the choice must match how `CallFailure::Raised` and the
`pending` slot already reach Python, since the handler needs the *exception
object* as well as the bit". There is no such choice to make, because there is
no transport that carries both. For the Python frame to be *inside* an `except`
block, Rust must **raise** — and raising is precisely the operation that cannot
carry a second value alongside the caller's already-constructed exception
object, which must also be the one held by identity, since `exc_info[1]` is
asserted *is* the instance the property raised. The only shapes that work are to
mutate the caller's exception (changing what `str()` returns for user code that
catches it) or to add an out-parameter whose entire purpose is to move one
string one frame.

So the handler — the `logger.debug`, the `silent_variable_failure` branch, and
the re-raise — is `crates/template-py/src/lib.rs::outcome_to_py`, and Rust
receives the held exception and passes it as `exc_info=<instance>`. **This is a
deviation from the plan and it is the interesting one**, because it is the only
place in M9 where the answer to "should this be Python?" was no for a reason
other than size: the logger is Python's, the handler is Rust's, and
`logging.getLogger("django.template")` is reached by call so the record lands on
the object `assertLogs` replaces the handlers of.

**(4) `exc_info=True` is the wrong spelling, and the obvious assertion hides
that.** Upstream writes `exc_info=True`, which resolves `sys.exc_info()` at call
time and is `(None, None, None)` in any frame without a live `except`. A port
carrying the exception in a stash is exactly "held, never active" — so
`assertIsNotNone(record.exc_info)` passes on a tuple of `None`s while
`str(record.exc_info[1])` is `'None'`. The `test_the_log_record_carries_the_exception_instance`
row executes the naive form on the same logger before executing the render, so
the premise is produced and asserted on rather than described.

**(5) Three of Task 5's own citations were wrong, and `scripts/anchors.txt`
caught all three.** `base.py:16` was cited for `logger = logging.getLogger("django.template")`,
which is at **`base.py:94`**; `base.py:994` was cited for the
`raise VariableDoesNotExist(`, which is at **`base.py:989`**; and
`base.py:1015-1021` was a loose range whose end line is the closing paren of the
`logger.debug` call, so its endpoint quoted `)`. All three were written from the
plan's own prose rather than read off the tree — the plan's Step 4 doc-comment
itself says "(`base.py:1014-1018`)", which is the handler's `except` line
followed by the log call's first three lines and omits both `template_name` and
`exc_info`. The repaired citations are `base.py:94`, `base.py:989`, and
`base.py:1015-1020`, each with a snippet authored from the sentence's own claim
(`logging.getLogger("django.template")`, `raise VariableDoesNotExist(`, and
`exc_info=True,` at the endpoint) rather than left unquoted.

This is the fifth time in M9 that a number copied from a document — this plan
included — was wrong, and it is the first time every instance was caught before
commit rather than after, because the guard compares a snippet to a line. The
plan is not a source. Read the tree.

**(6) The delegation made five harnesses vacuous, and the guard named them.**
`tests/differential/cases.py`, `tests/test_render_parity.py::ResolverParityTests`,
`tests/test_resolve_invalid.py`, `tests/test_resolve_callarm.py` and
`tests/test_m8_side_effects.py` all compared `_native.resolve` against
`Variable._resolve_lookup`, which after this task *is* `_native.resolve` — the
"stock vs Rust" self-comparison those harnesses exist to refuse, arriving
through the front door and green. Their Python side is now the pinned source
executed out of `.django` (`tests/parity_target.py::pinned_resolve_lookup`), and
their teeth were proved by mutation rather than asserted: with `step_one`'s
`bit` replaced by the loop's last bit, and again with the `Missing` arm's log
call deleted, the 37 resolver rows across the five harnesses produced 28F+1E and
21F+15E respectively. The transcription guard's declared-region clause is what
found the five; nothing in the suite would have.

**(7) Correction (3)'s reason was false, and the reviewer falsified it in the
same round it was written.** Correction (3) above says the handler "could not
stay in Python, and the bit's transport is why". It can. Return the outcome
instead of raising it — `(value, bit, error)` — and the shell can log
`exc_info=error`, an *instance*, which `logging` accepts and which is exactly
why `record.exc_info[1]` is the caller's object by identity; then substitute or
`raise error`. No mutated exception, no out-parameter, no thread-local, and no
live `except` anywhere. The `exc_info=<instance>` spelling is the one this very
task adopted for its own row — the argument ruling out the Python handler was
using a fact the fix had already established.

"Rust must raise for Python to be inside an `except` block" is true, and it is a
property of `fn resolve(…) -> PyResult<Py<PyAny>>` — a signature that has one
channel. The signature was **chosen**; the constraint was not found. This is the
same error the milestone keeps recording, one level up: not a wrong number or a
wrong premise, but a *reason reached for* and then used as a proof.

The decision survives, but **the second argument the corrections first reached
for was false too, and this one was falsified after it had been written down** —
the reviewer caught it in the same pass as the first. It said rerouting the five
harnesses through the shell's `_resolve_lookup` would be refused by
`tests/parity_target.py`. That guard's subject is the *target* — its own
docstring at `tests/parity_target.py:41` describes `target` as a class or
function whose source file is the comparison's Python side, Django's — and
rerouting changes **our** side, not the target, which stays
the ast-extraction out of the pinned checkout (`tests/parity_target.py:147`).
That is the whole job of `pinned_resolve_lookup()`, so the guard would be
satisfied and unmoved. The sentence conflated the side under test with the
oracle.

What does hold is smaller, and it is about **when**, not **whether**.
`_native.resolve` is the Rust mirror of `_resolve_lookup`, built so the five
comparison harnesses can compare a lookup against Django's **at the method**,
with the arm that failed named in the report. `tests/test_render_parity.py:529`
(`test_a_silent_variable_failure_is_substituted`) and
`tests/test_resolve_callarm.py:86` (`test_a_silent_type_error_is_substituted`)
exercise `silent_variable_failure` against `_native.resolve`, so a version that
returned the raw failure would diverge from the pinned method on precisely the
rows they were built for. The trade is therefore **one comparison point against
two**, not a mirror against a verbatim handler: an arm-level row calling
`_native.resolve` directly still gets the arm-named report with no handler in
Rust, because it needs the *outcome*, which `(value, bit, error)` carries more
explicitly than an applied handler does, and a behaviour row at the method covers
the silent-failure branch. Two granularities is what every other seam here
already has.

So the handler stays in Rust **for sequencing**: the five harnesses' teeth were
established by mutation, and reworking them inside a commit whose job is to
correct a false reason is the "redesign bundled into a fix" shape that has made a
premise false twice already — the new teeth would be asserted rather than
measured, in a commit nobody is reading for that. Task 7 owns the design question
with this corrected cost, and not with the cost as first written.

One consequence is already fixed and one is not. Fixed: `outcome_to_py`'s
docstring and the shell's comment now state the trade rather than an
impossibility. Not fixed, and it is the sharper half the reviewer named: **the
declaration is smaller than what moved.** `check_transcriptions.py` records a
hunk of the loop, while the whole of `_resolve_lookup`'s exception semantics
left `base.py` — a body replaced by a call is one hunk however much body went
with it, so the diff cannot show it and prose is the only place that can. The
declared region now says so, and whether the guard should be able to see a moved
*handler* rather than only a moved line is Task 7's — restated there as the
achievable half of that question.

### Obligations M9 is handing to Task 7

Collected here because they were written down in nine different places and a
close-out that has to grep for its own obligations is a close-out that misses
one. Each names the thing it needs read off the tree, since four of these
arrived as someone else's summary.

1. **The universe-capture decision.** `install()` should **fail loudly** when any
   `django.template*` or `django.templatetags*` module is already in
   `sys.modules`, rather than rebinding a list of names. Rebinding is an
   enumeration, and this project's enumerations are its worst record. Three
   capture *kinds* behave differently and none is visible at the import line:
   a captured **function** is stale but working; a captured **enum member**
   makes every comparison `False` (`django/templatetags/i18n.py:5` imports
   `TokenType`); a captured **class** makes every `isinstance` `False`
   (`django/template/defaulttags.py:30-38` captures nine names including `Node`
   and `NodeList`). **The `i18n.py` line numbers are the reviewer's and have not
   been re-read off the tree — read them before they go in.** The flag stays even
   though the reviewer has since confirmed measuring them: from the message alone
   you cannot tell which kind of number it is, the carrier looks identical either
   way, and the flag costs one file-open at close-out.
2. **`builtin_tag_names()` / `builtin_tag_stubs()` need two names.** The
   confusion has now happened three times; the rename is the fix, subtraction at
   the call site is the workaround.
3. **`COMPARED_PATH` has a blind spot on decorator-generated operator classes.**
   `smartif`'s comparison operators are built by a decorator, so
   `inspect.getsourcelines` reports the decorator's span and the guard sees the
   wrong region.
4. **Five guarded settings configurations race, and `unittest discover` decides
   by alphabet.** Task 4's correction (6) is the third M9 instance of a row
   being green or red because of a module's *name*. One explicit suite-wide
   configuration is the question; five guards is the current answer.
5. **The handler's side of the seam — this section's (7).** One comparison point
   (today) against two: move the handler verbatim into the shell and split the
   five harnesses — `tests/differential/cases.py`, `tests/test_render_parity.py`,
   `tests/test_resolve_invalid.py`, `tests/test_resolve_callarm.py` and
   `tests/test_m8_side_effects.py` — into an arm-level row calling
   `_native.resolve` directly and a behaviour row at `Variable.resolve`.
   `tests/parity_target.py` does **not** forbid this: it guards the *target*,
   which stays `pinned_resolve_lookup()`. The cost is the retargeting, and the
   reason it is not paid now is sequencing — new teeth would be asserted rather
   than measured, inside a commit whose job is a correction.
6. **The transcription guard cannot see a moved handler, and should not try.**
   It sees a moved *line*; a body replaced by a call is one hunk whatever moved
   with it. Seeing the handler would take comparing semantics, which is not
   obviously achievable. The achievable half is the **asymmetry**: a region where
   upstream has forty lines and ours has two is a relocation *by construction*,
   and the guard already holds both spans — reporting that ratio, and requiring a
   declared note above a threshold, is mechanical and would have flagged this
   hunk. It does not say *what* moved; it says something did, which is currently
   prose's only witness.
7. **The acceptance label's M9 caveat** (Task 7 Step 7) and the spec's §10.3/§10.4
   rows, still owed.
