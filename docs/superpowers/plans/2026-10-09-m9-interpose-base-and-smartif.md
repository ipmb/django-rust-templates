# M9 — Interpose `base` and `smartif` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `django.template.base` and `django.template.smartif` resolve to our files, so the whole suite's parsing and rendering run through our `Template`, `NodeList`, `Node` and `Variable` — with the parse, render and resolver seams delegating to the Rust engine that already implements them.

**Architecture:** Both modules are transcriptions of the pinned 6.0.9 source, carrying a `# TRANSCRIBED:` marker in the docstring the way `_impl/__init__.py` does, with a small number of *delegation* edits where Rust already owns the work: `Template.compile_nodelist` (parse), `Variable._resolve_lookup` (resolve). The node classes themselves stay Python — per §5.5 the Python node tree *is* the render path — so `Node.render_annotated` and `render_value_in_context` become ours by transcription rather than by porting.

**Tech Stack:** Python 3.14 (pinned interpreter), Rust (`crates/template-core`, `crates/template-py` via PyO3/maturin), Django 6.0.9 pinned at `.django/`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-07-django-template-engine-in-rust-design.md` (§5.2, §5.5, §6, §10.3, §10.4). Recon that produced this plan: `.review/m9-recon.md`; its scripts in `.review/m9-measurements/`.

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
| `tests/test_shim.py` | **Modify:95.** The meter row moves from `assertIn("django.template.base", remaining)` to asserting the count is 23. |
| `scripts/check_transcriptions.py` | **Create (Task 7).** Fails when a `_impl` transcription differs from upstream outside a declared hunk list. |
| `scripts/check.sh` | **Modify.** Add the transcription guard to the fast path. |

---

### Task 0: Make the unit-test harness shim-consistent

**This lands before Task 1.** The numbering of the tasks below is unchanged, so the recon's references stay valid; this one was found by executing Task 1, not by reading it.

**The defect, measured during Task 1's execution.** With `_impl/base.py` present, the project's own unit tests went from 283 green to **543 failures** — and running `test_parser_parity` alone passed. The direction test settles it:

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

**It is not caused by Task 1's new test module.** `tests/test_shim.py` already called `_shim.install()` mid-run, before M9, and it was harmless because `exceptions.py` was the only interposed module and no parity test imported it. M9 is what makes the existing leak observable.

**The fix, measured.** Install the shim once at unit-test session start, so every module binds the same classes:

```
PYTHONPATH=…/scripts/shimpath:…/.django DJANGO_RUST_TEMPLATES=1 python -m unittest discover …
→ Ran 285 tests, FAILED (failures=7)
```

**543 → 7.** The remaining seven are all guard rows doing their job, and each is enumerated in Step 3.

**Files:**
- Modify: `scripts/check.sh` (the `python unit tests` step)
- Modify: `tests/test_parser_parity.py`, `tests/test_lexer_parity.py`, `tests/test_parser_errors.py`, `tests/test_filter_expr_parity.py` (the guard rows)
- Modify: `tests/test_shim.py` (`test_a_partial_subpackage_still_falls_back_for_its_siblings`)

**Interfaces:**
- Consumes: nothing.
- Produces: a unit-test session in which `django.template.*` resolves consistently, so Tasks 1–7 can interpose modules without breaking the harness.

- [ ] **Step 1: Run the unit tests the way the suite is run**

In `scripts/check.sh`, change the `python unit tests` step to export the same environment `run_django_suite.sh` does. Do **not** hand-roll the paths — source them, so the two invocations cannot drift:

```sh
step "python unit tests"
# Shimmed, like the Django suite: the shim loads our file under Django's module
# name, so a test module that binds `Node` before `install()` holds a *different
# class object* than one that binds it after, and `isinstance` fails across the
# boundary — measured, as 537 order-dependent failures in the parity harness.
# Installing at session start makes every module bind the same classes.
DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$HERE/shimpath:$PROJECT_ROOT/.django${PYTHONPATH:+:$PYTHONPATH}" \
  "$PROJECT_ROOT/.venv/bin/python" -m unittest discover -s "$PROJECT_ROOT/tests" -t "$PROJECT_ROOT" -p 'test_*.py'
```

- [ ] **Step 2: Confirm the count drops to 7**

Run: `scripts/check.sh --fast`
Expected: `Ran 285 tests` / `FAILED (failures=7)`, and nothing else regressed.

- [ ] **Step 3: Restate the guard rows — do not delete them**

All six `test_*_target_is_genuinely_djangos*` rows assert that the comparison target is Django's own file, and all six are now **correctly failing**. The claim they make is still the right claim; what changed is what "Django's" can mean in-process once we interpose.

The target is now our transcription, and the transcription's fidelity to upstream is what makes the comparison meaningful — so each guard is restated to assert **the target is the transcription, and the transcription is gated**:

```python
    def test_the_comparison_target_is_the_gated_transcription(self):
        """The target is `_impl/base.py`, and that is a *stronger* claim than it looks.

        Before M9 this row asserted the target was Django's own file, so that the
        parity comparison could not silently become a self-comparison. Once `base`
        is interposed that assertion cannot hold — Django's file is no longer what
        `django.template.base` resolves to — and the honest replacement is not to
        drop the guard but to name the new chain of trust:

            Rust  ==  `_impl/base.py`'s Python  ==  upstream (by diff)

        The middle link is this comparison. The last link is
        `scripts/check_transcriptions.py`, which fails on any hunk of our file
        that differs from upstream outside the declared set. Remove that gate and
        this row is worthless, which is exactly why it is asserted here rather
        than assumed.
        """
        source = inspect.getsource(Parser)
        self.assertIn("_impl", source)
        self.assertTrue(
            (PROJECT_ROOT / "scripts" / "check_transcriptions.py").is_file(),
            "the transcription gate is what makes this comparison meaningful",
        )
```

Apply the same restatement, in its own idiom, to the five sibling rows: `test_lexer_parity.py:269`, `test_parser_errors.py:435`, `test_parser_parity.py` (ShapeTests), and the three targets in `test_filter_expr_parity.py`.

- [ ] **Step 4: Fix the shim fallback row**

`test_a_partial_subpackage_still_falls_back_for_its_siblings` fails because `base` no longer falls back. Read it, and change it to name a module that *does* still fall back — the row is about the shim's partial-subpackage behaviour, not about `base`, so the subject should be a module that is still Django's.

- [ ] **Step 5: Verify each restated guard still has teeth**

For one row, temporarily delete `scripts/check_transcriptions.py` (or rename it), re-run, and confirm the row fails. Restore. A guard that cannot fail is the thing this whole task exists to remove.

- [ ] **Step 6: Commit**

```bash
git add scripts/check.sh tests/
git commit -m "$(cat <<'EOF'
M9: install the shim for the unit-test session

Found by executing Task 1 rather than reading it: with `_impl/base.py` present the
unit tests went 283 green -> 543 failures, and `test_parser_parity` passed when run
alone. Direction test: test_shim then test_parser_parity is 537 failures; the
reverse is OK.

Root cause: the shim loads our file under Django's module name, so our `Node` is a
second class object. A module binding `Node` before `install()` holds Django's and
one binding after holds ours; `isinstance` fails across the boundary, `describe()`
falls to its `repr()` arm, and the assertion compares memory addresses — two
identical trees reported as different. `test_shim.py` has called `install()`
mid-run since before M9; it was harmless while only `exceptions` was interposed.

Installing at session start is measured: 543 -> 7 failures. The seven are all
guards doing their job. Six assert the comparison target is Django's own file,
which cannot hold once `base` is ours — restated to name the new chain, Rust ==
the transcription == upstream, with `check_transcriptions.py` as the last link.
That gate is now load-bearing for the whole parity harness, not just for
documentation rot.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

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

- [ ] **Step 1: Generate the transcription from the pinned tree**

Run from the repo root. The marker goes on the first line *inside* the module docstring, matching `_impl/__init__.py`.

```bash
cd /Users/pete/projects/lincolnloop/django-rust-templates
.venv/bin/python -I - <<'PY'
from pathlib import Path

SRC = Path(".django/django/template/base.py")
DST = Path("python/django_rust_templates/_impl/base.py")
MARKER = "# TRANSCRIBED: django/template/base.py (Django 6.0.9)\n"

source = SRC.read_text()
assert source.startswith('"""\n'), "docstring shape changed; fix the insertion point"
DST.write_text('"""\n' + MARKER + "\n" + source[4:])
print(f"wrote {DST} ({len(DST.read_text().splitlines())} lines)")
PY
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
git add python/django_rust_templates/_impl/base.py tests/test_shim.py tests/test_interposition.py
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

A `# TRANSCRIBED` file is faithful only while someone re-diffs it. The docstring says "diff it against upstream on every Django bump" — nothing enforces that, and Review Focus 1 is that a silent divergence in `base.py` is invisible to a green suite. This task makes the diff a gate: every difference from upstream must be in a *declared* list, so an undeclared edit fails.

**Files:**
- Create: `scripts/check_transcriptions.py`
- Modify: `scripts/check.sh`
- Modify: `docs/superpowers/specs/2026-10-07-django-template-engine-in-rust-design.md` (§10.3 M9 row, §10.4)
- Test: the script's own red state, measured

**Interfaces:**
- Consumes: `_impl/base.py`, `_impl/smartif.py` from Tasks 1–3.
- Produces: a gate that fails when a transcription differs from `.django/` outside its declared hunks.

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

In `docs/superpowers/specs/2026-10-07-django-template-engine-in-rust-design.md`, §10.3's M9 row and §10.4: record the closing commit, the meter at **23**, the three seams with their measured sizes (parse 1170, render 964, resolve), and the two things M9 proved but did not implement (§5.5's interior-subtree rendering, deferred; `render_value_in_context`'s equivalence result from Task 4).

- [ ] **Step 9: Commit**

```bash
git add scripts/check_transcriptions.py scripts/check.sh docs/superpowers/specs/2026-10-07-django-template-engine-in-rust-design.md
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

**Review Focus.** 1 → Task 7's guard. 2 → Tasks 2, 3, 5's counting rows. 3 → Task 4 Step 2. 4 → Task 4 Step 2's second row. 5 → Task 5 Steps 2 and 7.
