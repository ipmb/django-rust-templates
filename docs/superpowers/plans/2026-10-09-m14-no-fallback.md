# M14 Implementation Plan — the empty meter, enforced at the acceptance run

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_django_suite.sh` refuse to run the acceptance suite unless `unshimmed()` is empty in **both** of the meter's scopes — turning M14's acceptance phrase, "full suite green with no fallback", from a line the run prints beside the result into a condition of the run — then record the milestone.

**Architecture:** M14 does nothing to the tree to become true. `unshimmed()` is already `[]` over `django.template` and `django.templatetags` (measured, `.review/m14-recon.md` §1), and `tests/test_milestone_ownership.py:151` already asserts it with a positive control drawn from the population. The milestone is therefore about *enforcement and record*, not construction. The one gap is that the runner's pre-flight computes the meter for **one** scope and never refuses on it: a green run today is consistent with a non-empty meter, because the only thing that asserts emptiness is a unit row, and the unit suite is not the acceptance criteria. The change is small and its risk is in the opposite direction — a guard that fires on a healthy run — which is why Task 1 ships with a negative control over a *fabricated* non-empty meter.

**Tech Stack:** Bash (`scripts/run_django_suite.sh`), Python 3.14 (the extracted pre-flight and its test). No Rust changes.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — §2 (the acceptance criterion), §3's non-goals bullet (the declared `NOT_THE_ENGINE` set), §10.3's M14 row and its amendment paragraph.

**Recon:** `.review/m14-recon.md`. Every count below is that recon's, re-run rather than re-quoted.

## Global Constraints

- Pinned Django tree: `.django/` at **6.0.9**. A citation names the version, never the path.
- Acceptance is `bash scripts/check.sh` → exit 0 and `all checks passed`, which includes Django `Ran 1547 tests … OK (skipped=6)`.
- **The acceptance label set is `template_tests` only** (`scripts/run_django_suite.sh`). A test in `timezones/` or `i18n/` covers us not at all.
- Python floor 3.14; `.venv/bin/python` is CPython 3.14.4.
- No new crates, no new Python dependencies.
- `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings` are green at every commit.
- Every `name.py:N` in prose is a claim the anchor manifest re-measures; `--check --frozen` is on the fast path.
- Unit tests run through `scripts/run_unit_tests.sh` (the shim must be installed at interpreter start).
- Conventional commits, one task per commit, ending with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## Review Focus

The input classes / failure modes M14's own change introduces, each owned by the task that writes its test, most likely first.

1. **A healthy run must not be refused.** The guard's whole risk is a false positive: if it fires when both scopes are empty, the project has no acceptance path. The positive control is the real run itself (Task 1 Step 6), and the negative control (Task 1 Step 4) is drawn from the same population — a fabricated meter with a name in it. → **Task 1**.
2. **`DJANGO_RUST_TEMPLATES=0` must still bypass the guard.** The runner is used deliberately against stock Django to prove a failure is Django's and not ours; the pre-flight is inside `if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]` and the extraction must keep it there. A guard that fired on the stock-Django comparison run would make the one debugging tool unusable. → **Task 1**.
3. **The second scope must be consulted, not respelled.** The runner's pre-flight today calls `_shim.unshimmed()` with no argument (the default `django.template`). The fix iterates `_shim.NAMESPACES` (`_shim.py:98`, `= tuple(NAMESPACE_ROOTS)`) rather than naming `"django.templatetags"` again, so the checked population cannot drift from the meter's — the drift this tree has hit three times (D3′, the vacuity reasons, M10's `origin`). → **Task 1**.
4. **The file-level claim and the runtime claim stay distinct in the message.** `unshimmed()` compares source trees, not resolved imports (`_shim.py:777-779`), so a non-empty meter means "a name Django defines has no same-named file in `_impl`". Before M14 the runtime half was **one** module (`django.template.exceptions`); the extraction makes it **total** — every engine module Django defines is imported and asserted to resolve under `_impl`, measured at 21 modules (16 under `django.template`, 5 under `django.templatetags`) importing cleanly with settings unconfigured. The import is safe here because the *pre-flight* has no side-effect-free constraint — that constraint belongs to `unshimmed()`, which a caller may reach for anywhere. What the total check still does **not** establish is *behaviour*: it proves each module's file is ours, not that the file is right — that is the suite's job. The record must state the conjunction exactly. → **Task 1**, **Task 2** (Review Focus 4's row).
5. **The guard's correctness is dated to the engine being complete.** A pre-flight refusing on a non-empty meter would have blocked every suite run through M10–M13, when the meter was non-empty by design. It is correct *now* because the engine is done, and it will refuse on the first commit of any future milestone that interposes a new engine module. That is the guard's intent, but it must be **recorded as a condition** so the first person to hit it is not left to guess whether it is a bug — the dated-verdict rule, applied to a guard. → **Task 1** (the script's docstring and its refusal message both say so).
6. **A version bump adding `django/template/*.py` is the failure the guard exists for.** Not testable by making the pin do that, but the guard's population is `django_modules(scope) - ours - NOT_THE_ENGINE`, so a new upstream module arrives in the meter (and in the total runtime check) rather than silently outside it. → **Task 1** (asserted against `_shim.django_modules`, the same call the meter makes).
7. **The meter can reach empty by widening its subtrahend rather than by interposing.** `unshimmed()` is `django_modules - ours - NOT_THE_ENGINE`, so a name moved out of the engine by editing the shim and the reason beside it empties the meter *without any module being written* — and §3, the document the decision is recorded in and the one M14's criterion names, is read by nothing. → **Task 1b**.

---

## File structure

| file | change | responsibility |
|---|---|---|
| `scripts/assert_engine_installed.py` | **create** | The runner's pre-flight, extracted so it can be tested: install check, a **total** runtime check (every engine module imported and asserted to resolve under `_impl`), and the new both-scopes empty-meter refusal. Exposes a pure `engine_fallbacks(unshimmed, scopes)` for the test to drive with a fabricated meter. |
| `scripts/run_django_suite.sh` | modify | The heredoc pre-flight is replaced by a call to the new script, still inside the `DJANGO_RUST_TEMPLATES=1` branch. |
| `tests/test_acceptance_preflight.py` | **create** | The negative control: a fabricated non-empty meter in each scope drives `engine_fallbacks` and the refusal; an empty meter passes; the population is `django_modules(scope) - ours - NOT_THE_ENGINE`. |
| `docs/feature-checklist.html` | modify | Pete's status page: M14 row, both meter scopes, re-stamped date. |
| `docs/superpowers/specs/2026-10-08-…-design.md` | modify | §10.3's M14 row, marked landed, with the counts measured on the closing tree. |
| `.review/m14-record.md` | **create** | The milestone record, in M9–M13's shape: the three fallback guards and which failure each carries, the file-level/runtime caveat, and "the criterion was met, the milestone enforced it". |

---

### Task 1: The acceptance run refuses a non-empty meter, in both scopes

**Files:**
- Create: `scripts/assert_engine_installed.py`
- Modify: `scripts/run_django_suite.sh` (the pre-flight heredoc, under the `if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]` branch)
- Test: `tests/test_acceptance_preflight.py`

**Interfaces:**
- Produces: `scripts/assert_engine_installed.py` with
  - `engine_fallbacks(unshimmed: Callable[[str], list[str]], scopes: Iterable[str]) -> dict[str, list[str]]` — the file-level check: `{scope: names}` for every scope whose `unshimmed(scope)` is non-empty. No I/O.
  - `runtime_fallbacks(import_module, django_modules, not_the_engine, scopes) -> dict[str, str]` — the runtime check: `{dotted: file}` for every engine module that did not resolve under `_impl`. Total over `django_modules(scope) - not_the_engine`.
  - `main() -> int` — binds the real `_shim` functions and the two checks above; returns `0` only when both are clean, non-zero with a message on stderr otherwise. Called as `python scripts/assert_engine_installed.py` and as `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 1: Write the failing test**

```python
"""The acceptance pre-flight, driven with a fabricated meter.

M14's change makes `run_django_suite.sh` refuse unless `unshimmed()` is empty in
both scopes. The positive control is the real run; this file is the negative
control, because a guard whose only exercise is a green run is a guard that might
be a no-op. The fabricated meter is drawn from the same population the guard
reads — a scope with a name in it — so "refuses" is measured against something,
not against an empty universe.

The fabricated module names carry the `unregistered_` marker the tag inventory
uses, and a row asserts none of them exists in the real population, so the
control cannot be overtaken by progress.
"""

import unittest

from django_rust_templates import _shim

from scripts.assert_engine_installed import engine_fallbacks

SCOPES = ("django.template", "django.templatetags")
# A name no tree can ever contain — the `unregistered_` rule, in the module
# namespace. `test_the_fabricated_names_are_not_real_modules` pins it.
FABRICATED = "unregistered_m14_fabricated"


def meter(*non_empty):
    """A stand-in for `_shim.unshimmed` with `non_empty` scopes non-empty."""
    by_scope = {scope: [f"{scope}.{FABRICATED}"] for scope in non_empty}
    return lambda scope: by_scope.get(scope, [])


class EngineFallbacksTests(unittest.TestCase):
    def test_an_empty_meter_reports_no_fallbacks(self):
        self.assertEqual(engine_fallbacks(meter(), SCOPES), {})

    def test_a_name_in_the_first_scope_is_reported(self):
        self.assertEqual(
            engine_fallbacks(meter("django.template"), SCOPES),
            {"django.template": [f"django.template.{FABRICATED}"]},
        )

    def test_a_name_in_the_second_scope_is_reported(self):
        # The scope the runner's pre-flight did not consult before M14.
        self.assertEqual(
            engine_fallbacks(meter("django.templatetags"), SCOPES),
            {"django.templatetags": [f"django.templatetags.{FABRICATED}"]},
        )

    def test_both_scopes_are_reported_together(self):
        self.assertEqual(set(engine_fallbacks(meter(*SCOPES), SCOPES)), set(SCOPES))

    def test_the_fabricated_names_are_not_real_modules(self):
        """The control is drawn from the real population and is outside it.

        `FABRICATED` must be a name no tree contains, or the control answers a
        question about a module that might one day exist. The population is the
        meter's own: `django_modules(scope)`, both scopes, minus `NOT_THE_ENGINE`.
        """
        _shim.install()
        universe = set()
        for scope in SCOPES:
            universe |= {
                f"{scope}.{name}"
                for name in _shim.django_modules(scope) - _shim.NOT_THE_ENGINE
            }
        self.assertGreater(len(universe), 0, "the population the control is drawn from is empty")
        for scope in SCOPES:
            with self.subTest(scope=scope):
                self.assertNotIn(f"{scope}.{FABRICATED}", universe)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `scripts/run_unit_tests.sh tests.test_acceptance_preflight -v`
Expected: `ModuleNotFoundError: No module named 'scripts.assert_engine_installed'`

- [ ] **Step 3: Write the script**

```python
"""Pre-flight for the acceptance suite: the engine is installed and complete.

`run_django_suite.sh` runs this before spending a suite run, and refuses to
continue if it fails. Two claims are checked, and together they are "no fallback":

* **runtime** — every module the engine owns, imported, resolves to our file
  under `_impl`, not to Django's;
* **file level** — `unshimmed()` is empty in both scopes, i.e. no name Django's
  package defines lacks a same-named file in `_impl`.

**Correctness is dated to the engine being complete.** Refusing on a non-empty
meter would have blocked every suite run through M10–M13, when the meter was
non-empty by design. It is right *now* because the engine is finished — and it
will refuse on the first commit of any future milestone that interposes a new
engine module. That refusal is this script doing its job, not a regression; if
you are reading this because a milestone run stopped here, the milestone is the
thing to look at.

The runtime half imports every engine module, which `unshimmed()` deliberately
does not (it may be called anywhere, and importing can require settings). The
pre-flight has no such constraint — it runs once, immediately before the suite,
in the same interpreter the suite uses.
"""

import sys


def engine_fallbacks(unshimmed, scopes):
    """``{scope: names}`` for every scope whose meter is non-empty.

    Pure, so a test can drive it with a fabricated meter: the guard's only other
    exercise is a healthy run, which cannot show that it *would* refuse.
    """
    return {scope: names for scope in scopes if (names := unshimmed(scope))}


def runtime_fallbacks(import_module, django_modules, not_the_engine, scopes):
    """``{module: file}`` for every engine module not imported from under ``_impl``.

    Total over the engine: the population is `django_modules(scope) - not_the_engine`
    — the same call the meter makes — so a module a version bump adds to Django's
    package is imported and checked rather than passing unseen.
    """
    wrong = {}
    for scope in scopes:
        for name in django_modules(scope) - not_the_engine:
            dotted = f"{scope}.{name}"
            module = import_module(dotted)
            file = getattr(module, "__file__", "") or ""
            if "_impl" not in file:
                wrong[dotted] = file or "<no __file__>"
    return wrong


def main() -> int:
    import importlib

    from django_rust_templates import _shim

    if not _shim.installed():
        sys.stderr.write("shim is not installed; refusing to run the suite\n")
        return 1

    wrong = runtime_fallbacks(
        importlib.import_module,
        _shim.django_modules,
        _shim.NOT_THE_ENGINE,
        _shim.NAMESPACES,
    )
    if wrong:
        for dotted, file in sorted(wrong.items()):
            sys.stderr.write(f"{dotted} resolved to {file}, not to our implementation\n")
        sys.stderr.write(
            "refusing to run the acceptance suite: the engine is not fully "
            "installed.\n"
        )
        return 1

    # Both scopes, derived from the shim's own table rather than respelled, so
    # the checked population cannot drift from the meter's.
    remaining = engine_fallbacks(_shim.unshimmed, _shim.NAMESPACES)
    if remaining:
        for scope, names in sorted(remaining.items()):
            sys.stderr.write(
                f"{scope}: {len(names)} module(s) the engine owns are still "
                "served by Django:\n"
            )
            for name in names:
                sys.stderr.write(f"  {name}\n")
        sys.stderr.write(
            "refusing to run the acceptance suite: M14's criterion is "
            "unshimmed() empty in both scopes, so a green run here would not be "
            "the acceptance criterion.\n"
        )
        return 1

    sys.stderr.write(
        f"engine installed; unshimmed() is [] over "
        f"{', '.join(sorted(_shim.NAMESPACES))} "
        f"({len(_shim.NOT_THE_ENGINE)} more of django.template are declared not "
        "the engine)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `scripts/run_unit_tests.sh tests.test_acceptance_preflight -v`
Expected: 5 rows, `OK`.

- [ ] **Step 5: Point the runner at it**

In `scripts/run_django_suite.sh`, replace the whole pre-flight heredoc
(`"$PROJECT_ROOT/.venv/bin/python" - <<'PY' … PY`) with:

```bash
  echo "verifying the Rust engine is installed..." >&2
  "$PROJECT_ROOT/.venv/bin/python" "$HERE/assert_engine_installed.py"
```

Leave it inside `if [ "${DJANGO_RUST_TEMPLATES:-1}" = "1" ]; then … fi` — Review
Focus 2. Run `bash -n scripts/run_django_suite.sh` to check the syntax.

- [ ] **Step 6: Prove the guard fires — the negative control, at the real site**

The unit test drives the *pure* check; this drives `main()`'s refusal arm, with a
fabricated meter, in-process and without touching the tree. Do not make the tree
non-empty to test this — a checkout with a renamed `_impl` file is a checkout
whose other measurements are suspect.

Run:
```bash
PYTHONPATH=scripts/shimpath:.django DJANGO_RUST_TEMPLATES=1 .venv/bin/python -c "
from django_rust_templates import _shim
_shim.install()
from scripts import assert_engine_installed as pre

print('healthy ->', pre.engine_fallbacks(_shim.unshimmed, _shim.NAMESPACES))

_shim.unshimmed = lambda scope='django.template': (
    ['django.template.unregistered_m14_fabricated']
    if scope == 'django.template' else []
)
raise SystemExit(pre.main())
"
echo "exit=$?"
```
Expected: `healthy -> {}`, then the refusal message on stderr naming
`django.template.unregistered_m14_fabricated`, then `exit=1`. Paste both lines into
the commit message.

- [ ] **Step 7: Run the acceptance suite and confirm it is green**

Run: `bash scripts/run_django_suite.sh template_tests`
Expected: the new pre-flight line (`engine installed; unshimmed() is [] over
django.template, django.templatetags …`), then `Ran 1547 tests … OK (skipped=6)`.

- [ ] **Step 8: Run the gate and commit**

Run: `bash scripts/check.sh` — expect exit 0 and `all checks passed`.

```bash
git add scripts/assert_engine_installed.py scripts/run_django_suite.sh \
        tests/test_acceptance_preflight.py
git commit -m "M14 Task 1: the acceptance run refuses a non-empty meter, in both scopes"
```

---

### Task 1b: §3's declaration becomes a row (added after review)

**Why this was not in the plan.** The reviewer's Q2 on Task 1: the ten names
live in three places — §3's prose, `_shim.NOT_THE_ENGINE`, and
`tests/milestone_ownership.py::NOT_THE_ENGINE` — and
`test_the_declared_names_are_the_excluded_ones` ties only the last two
together. So **either can drift from §3**, and the direction matters: the meter
is `django_modules - ours - NOT_THE_ENGINE`, so widening the subtrahend empties
it exactly as interposing does. A name moved out of the engine by editing the
shim and its reason beside it — which is what this project's discipline asks
for — leaves every row green while §3 still says ten, and M14's empty meter is
then indistinguishable from the engine being complete. M14's own criterion row
names §3, so the tie is this milestone's.

The precedent is in the same file, one function away: `spec_milestones()`
(`tests/milestone_ownership.py:312`) reads §10.3's labels out of the spec, with
the reason stated as *"a second list would agree with itself"*.

**Files:**
- Modify: `tests/milestone_ownership.py` (three helpers beside `spec_milestones`)
- Modify: `tests/test_milestone_ownership.py` (two rows)
- Modify: `tests/test_collected_tests.py` (the pin)

**Interfaces:**
- Produces:
  - `spec_section(number: str) -> str` — §`number`'s body, up to the next `## `.
  - `between(text, start, end) -> str` — the frame, with both anchors checked.
  - `spec_not_the_engine() -> set[str]` — the names §3 enumerates.

- [ ] **Step 1: Write the failing row**

In `OwnershipTableTests`, importing `spec_not_the_engine`: assert
`len(declared) >= 10` (the reader's own guard, the shape
`test_every_owner_is_a_milestone_the_spec_defines` has), then
`declared == set(_shim.NOT_THE_ENGINE)` with a message that prints the
extraction, so a failure says which side moved.

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=scripts/shimpath:.django DJANGO_RUST_TEMPLATES=1 .venv/bin/python
-m unittest tests.test_milestone_ownership.OwnershipTableTests.test_the_specs_declaration_is_the_shims`
Expected: `ImportError: cannot import name 'spec_not_the_engine'`.

- [ ] **Step 3: Implement the reader**

`spec_section` slices the section rather than searching the document (§3
mentions `django.template` three times outside its list, and the phrase occurs
in a dozen other sections). `between` checks both anchors, because `partition`
cannot fail and a moved anchor would return the tail of the section as the
frame — a *wrong set*, not an error. `spec_not_the_engine` reads the frame
between `"stay Django's for good:"` and `"The decision is the"` and takes the
backticked spans.

- [ ] **Step 4: Run it to verify it passes**

Expected: OK, and the extraction is the ten.

- [ ] **Step 5: Add the reader's control**

`test_the_frame_reader_refuses_a_moved_anchor` — `between` on a literal, both
anchor arms asserted to raise. The failure arm is the one a green run never
reaches.

- [ ] **Step 6: Run the gate, bump the pin, and commit**

Run: `bash scripts/check.sh` — expect exit 0 and `all checks passed`, 464 unit
rows (two added). The pin moves 462 → 464 in `tests/test_collected_tests.py`,
with the delta written down beside it.

---

### Task 2: Close-out — the record and the spec row

**Files:**
- Create: `.review/m14-record.md`
- Modify: `docs/superpowers/specs/2026-10-08-…-design.md` (the M14 row, line 833)
- Modify: `docs/feature-checklist.html`

- [ ] **Step 1: Write `.review/m14-record.md`**

In M9–M13's shape: `# M14 record`, then sections —
`## The headline` (the criterion was met at M13; M14 enforces and records it);
`## The three fallback guards, and which failure each carries` (the recon §3
table, verbatim, with the file-level/runtime split stated — Review Focus 4);
`## What the pre-flight did before, and what it does now` (one scope, printed and
not refused → both scopes, refused, with the negative-control row named);
`## Numbers at the close` (measured on the closing tree, not quoted).

- [ ] **Step 2: Mark the spec's M14 row landed**

Replace the M14 row (`spec:833`) with a ✅ row naming the guard, the two scopes,
the negative-control test, the file-level/runtime split, and the close counts —
same shape as M13's row. Do **not** restate the §3 amendment paragraph; link to it.

- [ ] **Step 3: Re-stamp `docs/feature-checklist.html`**

Add the M14 row, keep both meter scopes at `[]`, re-stamp the date, and commit by
pathspec if a dispatch is in flight.

- [ ] **Step 4: Run the gate and commit**

Run: `bash scripts/check.sh` — expect exit 0 and `all checks passed`.

```bash
git add .review/m14-record.md \
        docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md \
        docs/feature-checklist.html
git commit -m "M14 close-out: the record, the spec row, and the status page"
```

---

## Self-review

- **Spec coverage:** §10.3's M14 row — "empty over the engine in both scopes, with
  every name it still reports one §3 declares not the engine | full suite green
  with no fallback". The empty-in-both-scopes half is asserted (Task 1) and the
  full-suite-green half is the run (Task 1 Step 7). The "every name it still
  reports" clause is vacuous at an empty set and is carried by
  `tests/test_milestone_ownership.py`'s partition row, which the record cites.
- **Placeholder scan:** no TBDs; every step names a file and a command.
- **Type consistency:** `engine_fallbacks(unshimmed, scopes)` is the same signature
  in the test and the script; `NAMESPACES`/`NOT_THE_ENGINE`/`unshimmed` are the
  shim's own names.
- **Review Focus:** each of the six lines names its task. 1–4 and 6 have rows or
  assertions; 5 (the dated condition) is carried by the script's docstring and its
  refusal message, and repeated in the record.
- **M15** ("§2 met with margin") is deliberately **not** planned here: "margin" has
  no definition in the spec yet, and defining it is M15's opening question rather
  than a dependency of M14. Flagged for the next sitting.
