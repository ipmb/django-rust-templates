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
4. **The file-level claim and the runtime claim stay distinct in the message.** `unshimmed()` compares source trees, not resolved imports (`_shim.py:777-779`), so a non-empty meter means "a name Django defines has no same-named file in `_impl`" — the runtime half is the existing `exceptions`-resolves-to-`_impl` check, which stays. The record must not let "no fallback" stand for the conjunction unstated. → **Task 2**.
5. **A version bump adding `django/template/*.py` is the failure the guard exists for.** It is not testable by making the pin do that, but it is worth a row that the guard's population is `django_modules(scope) - ours - NOT_THE_ENGINE`, so a new upstream module arrives in the meter rather than silently outside it. → **Task 1** (asserted against `_shim.django_modules`, the same call the meter makes).

---

## File structure

| file | change | responsibility |
|---|---|---|
| `scripts/assert_engine_installed.py` | **create** | The runner's pre-flight, extracted so it can be tested: install check, the `exceptions`-resolves-to-`_impl` runtime check, and the new both-scopes empty-meter refusal. Exposes a pure `engine_fallbacks(unshimmed, scopes)` for the test to drive with a fabricated meter. |
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
  - `engine_fallbacks(unshimmed: Callable[[str], list[str]], scopes: Iterable[str]) -> dict[str, list[str]]` — the pure check: `{scope: names}` for every scope whose `unshimmed(scope)` is non-empty. No I/O, no imports of the shim.
  - `main() -> int` — binds the real `_shim.unshimmed`, `_shim.NAMESPACES`, `_shim.installed()`, `_shim.NOT_THE_ENGINE`; returns `0` when every scope is empty after the runtime check, non-zero with a message on stderr otherwise. Called as `python scripts/assert_engine_installed.py` and as `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 1: Write the failing test**

```python
"""The acceptance pre-flight, driven with a fabricated meter.

M14's change makes `run_django_suite.sh` refuse unless `unshimmed()` is empty in
both scopes. The positive control is the real run; this file is the negative
control, because a guard whose only exercise is a green run is a guard that might
be a no-op. The fabricated meter is drawn from the same population the guard
reads — a scope with a name in it — so "refuses" is measured against something,
not against an empty universe.
"""

import unittest

from scripts.assert_engine_installed import engine_fallbacks

SCOPES = ("django.template", "django.templatetags")


def meter(*non_empty):
    """A stand-in for `_shim.unshimmed` with `non_empty` scopes non-empty."""
    by_scope = {scope: [f"{scope}.left_behind"] for scope in non_empty}
    return lambda scope: by_scope.get(scope, [])


class EngineFallbacksTests(unittest.TestCase):
    def test_an_empty_meter_reports_no_fallbacks(self):
        self.assertEqual(engine_fallbacks(meter(), SCOPES), {})

    def test_a_name_in_the_first_scope_is_reported(self):
        self.assertEqual(
            engine_fallbacks(meter("django.template"), SCOPES),
            {"django.template": ["django.template.left_behind"]},
        )

    def test_a_name_in_the_second_scope_is_reported(self):
        # The scope the runner's pre-flight did not consult before M14.
        self.assertEqual(
            engine_fallbacks(meter("django.templatetags"), SCOPES),
            {"django.templatetags": ["django.templatetags.left_behind"]},
        )

    def test_both_scopes_are_reported_together(self):
        self.assertEqual(
            set(engine_fallbacks(meter(*SCOPES), SCOPES)),
            set(SCOPES),
        )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `scripts/run_unit_tests.sh tests.test_acceptance_preflight -v`
Expected: `ModuleNotFoundError: No module named 'scripts.assert_engine_installed'`

- [ ] **Step 3: Write the script**

```python
"""Pre-flight for the acceptance suite: the engine is installed and complete.

`run_django_suite.sh` runs this before spending a suite run, and refuses to
continue if it fails. Two failures are checked, and they are the two halves of
"no fallback":

* the engine did not install, or a module we provide resolved to Django's file —
  a **runtime** claim about the import system;
* a name the engine owns is still served by Django — a **file-level** claim, from
  `unshimmed()`, which compares source trees rather than resolving imports.

M14 adds the second, over **both** of the meter's scopes. Before it, the runner
printed the meter for one scope and never refused on it, so a green run was
consistent with a non-empty meter.
"""

import sys


def engine_fallbacks(unshimmed, scopes):
    """``{scope: names}`` for every scope whose meter is non-empty.

    Pure, so a test can drive it with a fabricated meter: the guard's only other
    exercise is a healthy run, which cannot show that it *would* refuse.
    """
    return {scope: names for scope in scopes if (names := unshimmed(scope))}


def main() -> int:
    import django.template.exceptions

    from django_rust_templates import _shim

    if not _shim.installed():
        sys.stderr.write("shim is not installed; refusing to run the suite\n")
        return 1

    module_file = django.template.exceptions.__file__ or ""
    if "_impl" not in module_file:
        sys.stderr.write(
            f"django.template.exceptions resolved to {module_file}, not to our "
            "implementation; refusing to run the suite\n"
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
Expected: 4 rows, `OK`.

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

real = _shim.unshimmed
print('healthy ->', pre.engine_fallbacks(real, _shim.NAMESPACES))

_shim.unshimmed = lambda scope='django.template': (
    ['django.template.left_behind'] if scope == 'django.template' else []
)
raise SystemExit(pre.main())
"
echo "exit=$?"
```
Expected: `healthy -> {}`, then the refusal message on stderr, then `exit=1`. Paste
both lines into the commit message.

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
- **Review Focus:** each of the five lines names its task; 1–4 have rows, 5 is
  asserted by the population row.
- **M15** ("§2 met with margin") is deliberately **not** planned here: "margin" has
  no definition in the spec yet, and defining it is M15's opening question rather
  than a dependency of M14. Flagged for the next sitting.
