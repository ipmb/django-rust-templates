# M10 — Interpose `context`, `engine`, `utils`, `loaders` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `django.template.context`, `django.template.engine`, `django.template.utils` and the six modules of `django.template.loaders` resolve to our files, taking the progress meter from **23 to 14**, and land the ownership table that says which milestone takes each of the remaining names.

**Architecture:** All nine files are **transcriptions** of the pinned 6.0.9 source carrying a `# TRANSCRIBED:` marker — none of them is Rust's, because §6 gives every one of them to Python (framework coupling: `EngineHandler` reads settings, `Engine` constructs loader classes by dotted path, the loaders touch the filesystem). So unlike M9 there is no seam to build: the milestone's content is the interposition itself plus the *evidence* that each file is what it claims to be. That evidence is four instruments — the transcription guard (per-file diff), the ownership table (per-name partition), direct-entry rows (resolution, identity and reach), and a stated equivalence for the one `fresh` file that has no diff. §10.3's M10 row also names `origin`, which is **not a module at 6.0.9** — `Origin` is a class in `base.py`, M9's — so the row is corrected in Task 9 rather than implemented.

**Tech Stack:** Python 3.14 (pinned interpreter), Django 6.0.9 pinned at `.django/`, `unittest`, the `scripts/shimpath` `sitecustomize` that installs the shim at interpreter start. No Rust changes: this milestone adds no Rust.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§2, §6, §10.1, §10.3). Recon: `.review/m10-recon.md`; its instruments in `.review/m10-measurements/`.

## Global Constraints

- `.django/` is gitignored, pinned at **6.0.9**, and **never modified**. Every transcription reads from it; nothing writes to it.
- **`scripts/run_unit_tests.sh` is the only meaningful unit-suite invocation**, for the
  same reason: it exports `DJANGO_RUST_TEMPLATES=1` and puts `shimpath` ahead of the
  checkout, so the shim is installed at interpreter start rather than by the first test
  that needs it. Every per-task `Run:` below calls it; `scripts/check.sh` calls it too,
  so the two cannot drift. It also pins its own discovery through
  `tests/test_collected_tests.py`, which asserts the number of tests this command
  collects — a module named without `test_` runs its rows for nobody, and
  `all checks passed` cannot tell you.
- **`scripts/run_django_suite.sh` is the only meaningful suite invocation.** `scripts/shimpath/sitecustomize.py` installs the shim only when `DJANGO_RUST_TEMPLATES == "1"`, which only that script exports. Running `.django/tests/runtests.py` directly produces output byte-identical to stock Django's and is evidence of nothing. Every `Ran N tests` in prose or a commit message must sit within twelve lines of a marker naming the run — `scripts/check_suite_counts.py` gates this and *will* fire on a bare count.
- **The meter is the acceptance number:** `_shim.unshimmed()` is **23** at the start of M10 and must be **14** at its close. It moves in the commit that interposes, and `tests/test_shim.py::test_unshimmed_reports_progress` pins the count rather than a membership test.
- **Never `git checkout` to restore a file after a mutation.** The work is uncommitted; use `git show HEAD:<path> > <path>` or an `Edit`.
- `_impl/` must not contain a file named after a real `django.template` module that we do not intend to shadow. All nine M10 files are names Django *does* have, which is the point; `_shim.SHADOWING` is what makes each one deliberate and `tests/test_shim.py` asserts the table and the directory agree.
- Every transcription is generated, never hand-written: `python -I scripts/gen_impl_transcription.py <name>` writes it and `--check` refuses a hand edit.
- An anchor written as `` `name.py:N` `` in a scanned root (`crates`, `python`, `tests`, `docs/superpowers/specs`) is a claim the manifest must carry. `docs/superpowers/plans/` and `.review/` are excluded by rule.
- Commit messages end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- **Acceptance at M10 close:** `scripts/check.sh` prints `all checks passed`; `scripts/run_django_suite.sh` prints `Ran 1547 tests in …s` / `OK (skipped=6)`; the meter is **14**; the unit suite is green and its count is stated in the spec row; anchors 0 failures; suite counts all provenance-bearing.

## Review Focus

Each line is a failure mode this milestone can ship while every count stays green. Each gets a test in the task that owns the code.

1. **A `fresh` `_impl` file is one nobody can falsify.** The suite cannot see it (M9's finding 3: a file whose behaviour is upstream's keeps all 1547 green), and the transcription guard has no diff to measure. → Task 2's equivalence table and the `ShadowingTests` clause that refuses a `fresh` name missing from it.
2. **The sub-package fallback vanishes for Django's siblings.** The moment `_impl/loaders/__init__.py` exists, `django.template.loaders.__path__` is ours alone unless the finder reappends Django's directory — and then `django.template.loaders.filesystem` stops resolving. → Task 6 re-picks `test_shim.py`'s partial-subpackage row, Task 7 pins the six-module package.
3. **`FileSystemLoader`'s case-sensitivity is unexercised on every platform we run** (§2, measured: the upstream guard is `normcase`, structural rather than environmental). → Task 8.
4. **A `%s`-style dotted loader path in settings stops resolving after interposition.** `Engine.find_template_loader` does `import_string`, and `django.template.loaders.filesystem.Loader` must be *our* class afterwards — Django's own tests and every project's `TEMPLATES` setting spell it that way. → Task 7's `isinstance` row.
5. **`Engine(loaders=…, app_dirs=True)` must still raise `ImproperlyConfigured`** — an argument-combination contract that interposition could silently drop, and that no `.django` test exercises through our engine. → Task 5's row.

---

## File Structure

| Path | Responsibility |
|---|---|
| `python/django_rust_templates/_shim.py` | **Modify (Task 1).** `dotted_suffix()` (one spelling of path→dotted name, used by `_dotted_module_names`), `django_modules()` (the universe the ownership table is counted over), and `unshimmed()` restated in terms of them. `SHADOWING` gains nine rows over Tasks 3–7 and its docstring gains the two-obligation rule (Task 2). |
| `python/django_rust_templates/_impl/utils.py` | **Create (Task 3).** Transcription of `django/template/utils.py` @ 6.0.9 — `EngineHandler`, `InvalidTemplateEngineError`. Marker only; no delegation. |
| `python/django_rust_templates/_impl/context.py` | **Create (Task 4).** Transcription of `django/template/context.py` @ 6.0.9. Marker only. |
| `python/django_rust_templates/_impl/engine.py` | **Create (Task 5).** Transcription of `django/template/engine.py` @ 6.0.9. Marker only. |
| `python/django_rust_templates/_impl/loaders/…` | **Create (Task 7).** Six transcriptions: `__init__.py` (0 bytes upstream), `base.py`, `filesystem.py`, `app_directories.py`, `cached.py`, `locmem.py`. Markers only. |
| `scripts/gen_impl_transcription.py` | **Modify (Task 3).** Keys become `_impl`-relative paths (`base.py`, not `base`), matching the other four tables; `render()` gains the no-docstring branch (four of M10's files open with imports and one is empty). |
| `scripts/check_transcriptions.py` | **Modify (Tasks 3–7, 6).** Nine `DECLARED` and nine `COMPARED_PATH` entries; the undeclared-file clause switches from `IMPL.glob("*.py")` to `rglob` with `_impl`-relative keys (Task 6 — without it a file under `loaders/` is invisible). |
| `scripts/check.sh` | **Modify (Task 6).** Add `gen_impl_transcription.py --check` to the fast path; it is currently invoked by nothing, so a hand edit inside a declared region is unguarded. |
| `tests/milestone_ownership.py` | **Create (Task 1).** `OWNERS`, `UNDECIDED`, `INTERPOSED`, `interposed()`, `spec_milestones()`. |
| `tests/test_milestone_ownership.py` | **Create (Task 1).** The table asserted against `_shim.django_modules()` and `unshimmed()` in both directions. |
| `tests/pinned_module.py` | **Create (Task 2).** `load_pinned(relative)` — the pinned file executed under a private name, the only way to reach the copy the shim replaced. |
| `tests/test_transcription_equivalence.py` | **Create (Task 2).** `EQUIVALENCE` and `TemplateExceptionsEquivalenceTests`, the stated warrant for the one `fresh` file. |
| `tests/test_interposition.py` | **Modify (Tasks 3–7).** A `ModuleInterpositionTests` class: per module, resolution, identity against the pinned copy, and reach through `tests/compared_path.instrumented`. `ShadowingTests` gains the `fresh` clause (Task 2). |
| `tests/parity_target.py` | **Modify (Task 6).** Key `DECLARED`/`COMPARED_PATH` by `_impl`-relative path, not by basename. |
| `tests/test_shim.py` | **Modify (Tasks 3–7).** The meter count moves 23 → 22 → 21 → 20 → 14; the partial-subpackage row moves its subject to `django.template.backends` **and** its cleanup to create-if-missing / remove-only-what-it-wrote (Task 6). |
| `scripts/anchors.txt` | **Modify (Task 1).** One row for the `context_processors` citation. |
| `tests/test_loader_case_sensitivity.py` | **Create (Task 8).** §2's coverage hole, closed project-side. |
| `.review/m9-measurements/m9_measure_parse_path.py` | **Modify (Task 3).** Takes module arguments so it is the single re-measurement instrument for every `COMPARED_PATH` entry, M10's included. |
| `docs/superpowers/specs/…-design.md` | **Modify (Task 9).** §10.3's M10 row corrected to four modules and its own measured counts; the goal question is recorded, not answered. |

---

### Task 1: The ownership table

The recon's §2 mapped all 23 meter names against §10.3's rows and found **three with no owner at all** (`autoreload`, `context_processors`, `loader`) and one row naming a module that does not exist (`origin`). §8's repair is a table asserted against the tree in both directions. This task lands the table and the meter refactor it needs, and moves **no** names: the meter stays 23.

**Files:**
- Modify: `python/django_rust_templates/_shim.py`
- Create: `tests/milestone_ownership.py`
- Create: `tests/test_milestone_ownership.py`
- Modify: `scripts/anchors.txt`

**Interfaces:**
- Consumes: `_shim._dotted_module_names(root)`, `_shim._django_template_dirs()`, `_shim.unshimmed()`, `_shim.SHADOWING`.
- Produces: `_shim.dotted_suffix(relative: str) -> str`, `_shim.django_modules() -> set[str]`; `tests.milestone_ownership.OWNERS: dict[str, str]`, `.UNDECIDED: dict[str, str]`, `.INTERPOSED: dict[str, list[str]]`, `.interposed() -> set[str]`, `.spec_milestones() -> set[str]`.

- [ ] **Step 1: Write the failing tests.**

Create `tests/test_milestone_ownership.py`:

```python
"""The ownership table, checked against the tree it claims to partition.

Each row below is a direction the table can be wrong in. Together they are the
repair `.review/m10-recon.md` §8 asks for — "the rows and the meter become two
sets with an assertion between them, which is the thing that was missing in the
first place" — which is why they live in the milestone that noticed the problem
even though they are about every milestone after it.

The failures they exist for, both measured in the recon: `origin` sat in M10's
row although no `django/template/origin.py` exists at 6.0.9, and `autoreload`,
`context_processors` and `loader` sat in no row at all. A table with an
assertion under it cannot do either.
"""

import unittest

from django_rust_templates import _shim
from tests.milestone_ownership import (
    INTERPOSED,
    OWNERS,
    UNDECIDED,
    interposed,
    spec_milestones,
)

TARGET = "django.template"


def suffixes(names) -> set:
    """Dotted suffixes of full ``django.template.x`` names.

    `unshimmed()` answers in full dotted names because that is what a reader of
    the progress meter wants; the table is keyed by suffix because that is what
    `dotted_suffix()` produces from a file path, and comparing two spellings of
    the same name would be the drift this module exists to catch.
    """
    return {name[len(TARGET) + 1 :] for name in names}


class OwnershipTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_the_table_partitions_the_universe(self):
        """Both directions, and this is the row that makes the table binding.

        `django_modules()` is every name Django's template package defines — 26
        at 6.0.9, of which 23 are still Django's and 3 are ours. A name in the
        tree and in no bucket is a module no milestone has agreed to take, which
        is the failure §2 found three of. A name in a bucket that is not in the
        tree is a row about nothing — `origin`, as M10's row had it, and as this
        row would have caught.
        """
        self.assertEqual(
            set(OWNERS) | set(UNDECIDED) | interposed(),
            _shim.django_modules(),
        )

    def test_no_name_is_both_owned_and_taken(self):
        """A name in two buckets is a table that cannot be read as a partition.

        It is also the specific mistake this milestone makes possible: a task
        that interposes a module and forgets to move it out of `OWNERS` leaves
        the meter and the table disagreeing, and
        `test_the_meter_is_the_owners_plus_the_undecided` would then fail with
        the arithmetic rather than the reason.
        """
        self.assertEqual(set(OWNERS) & interposed(), set())
        self.assertEqual(set(UNDECIDED) & interposed(), set())
        self.assertEqual(set(OWNERS) & set(UNDECIDED), set())

    def test_the_meter_is_the_owners_plus_the_undecided(self):
        """The progress number and the table, as two sets with this between them.

        `unshimmed()` is the count every milestone is accepted on. If it can
        drift from the table, the table is decoration — and if the table can
        drift from it, the next reader plans against a schedule that is wrong.
        """
        expected = (set(OWNERS) | set(UNDECIDED)) - interposed()
        self.assertEqual(suffixes(_shim.unshimmed()), expected)

    def test_interposed_is_the_shadowing_table(self):
        """`INTERPOSED` adds the milestone attribution and no new membership.

        Which modules are ours is `_shim.SHADOWING`'s to state: it is derived
        from the files on disk (`shadowing_files()`) and compared against the
        guard's `DECLARED` by the rows in `test_interposition.py`. A second list
        of membership here would be the drift that comment warns against, so
        this row compares the two and `INTERPOSED` carries only *when*.
        """
        self.assertEqual(
            interposed(),
            {name for name in map(_shim.dotted_suffix, _shim.SHADOWING) if name},
        )

    def test_every_owner_is_a_milestone_the_spec_defines(self):
        """The labels are read from §10.3 rather than restated here.

        A second list of milestone names would agree with itself, so a typo
        (`M9` for `M13`) would be invisible. `spec_milestones()` parses the
        spec's own table, which makes the label a claim about that document.
        """
        defined = spec_milestones()
        self.assertGreaterEqual(len(defined), 15, sorted(defined))
        for name, owner in sorted(OWNERS.items()):
            with self.subTest(name=name):
                self.assertIn(owner, defined, f"{name} is owned by {owner!r}")
        for label in sorted(INTERPOSED):
            with self.subTest(label=label):
                self.assertIn(label, defined)

    def test_the_undecided_names_are_the_target_and_carry_their_reason(self):
        """§2's end state, as a set with a reason per name.

        The spec states the target as *zero* in two places (§10.1 and §10.3's
        M14 row), and §2 measured that no row reaches zero without interposing
        glue §6 forbids. So the end state M14 is held to is the `UNDECIDED` set,
        and this row pins which names are in it — the assertion is what makes
        "M14's by default" unavailable, and the reason field is what makes the
        set arguable rather than silent. `test_the_meter_is_the_owners_plus_the
        _undecided` is what gives it force: these are the only two names this
        table declines to own.

        The length check is a weak guard and is written as one: it refuses a
        placeholder, and the prose is reviewed rather than measured.
        """
        self.assertEqual(set(UNDECIDED), {"autoreload", "context_processors"})
        for name, reason in sorted(UNDECIDED.items()):
            with self.subTest(name=name):
                self.assertGreater(len(reason), 80, f"{name}'s reason is a stub")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `scripts/run_unit_tests.sh tests.test_milestone_ownership -v`
Expected: `ModuleNotFoundError: No module named 'tests.milestone_ownership'`.

- [ ] **Step 3: Write the table.**

Create `tests/milestone_ownership.py`:

```python
"""Which milestone owns each module of ``django.template``, as a table.

The meter (``_shim.unshimmed()``) counts what is left. It never said *who* takes
each name, and until M10 nothing did: ``.review/m10-recon.md`` §2 mapped all 23
names against §10.3's rows and found three with no owner at all, and `origin` in
M10's own row although no ``django/template/origin.py`` exists at 6.0.9.

A partition written as a row of prose is a claim nobody re-reads. This one is a
table with an assertion under it (``tests/test_milestone_ownership.py``), so a
name that leaves the meter without entering a bucket, or a bucket that names a
module the checkout does not have, fails in the commit that causes it.
"""

import pathlib
import re

SPEC = (
    pathlib.Path(__file__).resolve().parent.parent
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-10-08-django-template-engine-in-rust-design.md"
)

# Names a milestone will interpose, keyed by dotted suffix — `loaders.base`, not
# `loaders/base.py` — because that is the spelling `_shim.unshimmed()` reports
# and `_shim.dotted_suffix()` derives from a path.
#
# M10's nine are listed here until the commit that interposes them moves each to
# `INTERPOSED` below. That move *is* the discipline: it is what keeps the table
# and the meter in step, and `test_no_name_is_both_owned_and_taken` fails if a
# name is in both.
OWNERS = {
    # §10.3's M10 row. `origin` is deliberately absent and cannot be added: it
    # is not a module (§1 — `Origin` is a class in `base.py`, which M9 owns), so
    # the row naming it is an error, corrected in Task 9 of this plan's
    # milestone. `test_the_table_partitions_the_universe` makes it
    # unrepresentable here, because `origin` is not in `django_modules()`.
    "context": "M10",
    "engine": "M10",
    "utils": "M10",
    "loaders": "M10",
    "loaders.base": "M10",
    "loaders.filesystem": "M10",
    "loaders.app_directories": "M10",
    "loaders.cached": "M10",
    "loaders.locmem": "M10",
    # §10.3's M11 row.
    "defaulttags": "M11",
    "defaultfilters": "M11",
    "loader_tags": "M11",
    # §10.3's M12 row owns no meter entry: `i18n`, `l10n`, `static`, `tz` and
    # `cache` are `django/templatetags/*`, outside the namespace the meter
    # covers. M12 moves tag bodies and retires nothing here.
    #
    # §10.3's M13 row — partials, `library`, `backends`, `response` — plus
    # `loader`, which the row does not name and which §2 had to decide: the
    # singular `django.template.loader` is the *public facade*
    # (`get_template`, `select_template`, `render_to_string`), it reaches
    # templates through settings, and its only importer inside the template
    # package is `response.py`, which M13 owns. That is §6's framework-coupling
    # test, applied by hand, and it is stated here rather than left for the next
    # reader to resolve to whichever spelling they thought of first.
    "loader": "M13",
    "library": "M13",
    "backends": "M13",
    "backends.base": "M13",
    "backends.django": "M13",
    "backends.dummy": "M13",
    "backends.jinja2": "M13",
    "backends.utils": "M13",
    "response": "M13",
}

# Names no milestone has agreed to take, each with the reason it is not simply
# assigned to M14. `.review/m10-recon.md` §2: "undecided, not M14's by default"
# — the reading that they are M14's rests on the premise that zero is the right
# end state, and two of the three argue against it.
UNDECIDED = {
    "autoreload": (
        "`autoreload.py` consumes objects that become ours (`from django.template "
        "import engines`, and `DjangoTemplates` from `django.template.backends."
        "django`) and defines no template machinery of its own: two signal "
        "receivers and a directory walk. Interposing it would be copying glue, "
        "which is what §6's vendoring policy exists to refuse — with nothing of "
        "ours to get right, there is also nothing for a test to falsify."
    ),
    "context_processors": (
        "Reached by import string from `_builtin_context_processors` "
        "(`django/template/context.py:5`), not by a module-level import, so "
        "interposing `context` does not pull it in and nothing in the engine "
        "depends on its identity — Django's own `Engine` looks it up from the "
        "settings string. Unlike `autoreload` it does have behaviour of its own "
        "(`csrf`, `debug`, `request`, `auth`), so it is a real candidate rather "
        "than a leaf, and nothing in this milestone decides it."
    ),
}

# What is already ours, by the milestone that took it. The sets here are checked
# against `_shim.SHADOWING` by `test_interposed_is_the_shadowing_table`, so this
# table contributes the attribution and no membership of its own.
INTERPOSED = {
    "M0": ["exceptions"],
    "M9": ["base", "smartif"],
    "M10": [],
}


def interposed() -> set:
    """Every name an earlier milestone has already taken."""
    return {name for names in INTERPOSED.values() for name in names}


def spec_milestones() -> set:
    """The milestone labels §10.3's table defines.

    Read from the spec rather than written here: a second list would agree with
    itself and a typo in `OWNERS` would be invisible. The rows are
    ``| M<n> | <component> | <done when> |``; this takes the first cell of each.
    """
    return set(re.findall(r"^\| (M\d+) \|", SPEC.read_text(encoding="utf-8"), re.M))
```

- [ ] **Step 4: Run the tests again to see which rows now fail.**

Run: `scripts/run_unit_tests.sh tests.test_milestone_ownership -v`
Expected: `AttributeError: module 'django_rust_templates._shim' has no attribute 'dotted_suffix'` — the failure that names the next step.

- [ ] **Step 5: Add the two `_shim` helpers and restate `unshimmed()`.**

In `python/django_rust_templates/_shim.py`, replace `_dotted_module_names` with the pair below (the refactor is the point: one spelling of path → dotted name, used by the universe walk, by the meter and by `tests/milestone_ownership.py`):

```python
def dotted_suffix(relative: str) -> str:
    """``_impl``-relative posix path → the dotted suffix it is served under.

    ``base.py`` → ``base``; ``loaders/__init__.py`` → ``loaders``;
    ``loaders/base.py`` → ``loaders.base``; ``__init__.py`` → ``""``, the
    package itself, which is the one name no table in this tree carries.

    One function rather than four spellings: the file path is how a table finds
    the file, the dotted name is how `unshimmed()` and the ownership table count
    it, and a derivation written twice is how `loaders/base.py` becomes
    `loaders.base.py` in one of them.
    """
    parts = relative.split("/")
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _dotted_module_names(root: Path) -> set[str]:
    """Dotted suffixes of every module and package under ``root``.

    ``root/base.py`` yields ``base``; ``root/loaders/app_directories.py`` yields
    ``loaders.app_directories``; ``root/loaders/__init__.py`` makes ``loaders`` a
    package rather than a module, but either way the name is ``loaders``. The
    derivation is `dotted_suffix`'s, so the walk and the tables agree by
    construction.
    """
    names: set[str] = set()
    if not root.is_dir():
        return names

    for path in root.rglob("*.py"):
        name = dotted_suffix(path.relative_to(root).as_posix())
        if name:
            # The root package itself, which is ours by construction.
            names.add(name)

    return names


def django_modules() -> set[str]:
    """Every name Django's own template package defines.

    The universe both the meter and the ownership table are counted over — 26
    names at 6.0.9. Read from Django's directories rather than from a list here,
    because the failure it exists to catch is a name nobody thought about:
    `autoreload`, `context_processors` and `loader` were absent from every
    milestone row precisely because no list mentioned them
    (`.review/m10-recon.md` §2).

    Callable before ``install()``, which is what lets
    `tests/test_milestone_ownership.py` state the partition without depending on
    the order the suite happens to install the shim in: `_django_template_dirs`
    falls back to `_real_search_paths`, which is the same set of paths the finder
    captures at install.
    """
    names: set[str] = set()
    for real_path in _django_template_dirs():
        names |= _dotted_module_names(Path(real_path))
    return names
```

Then restate `unshimmed()`'s body (docstring unchanged apart from the last line):

```python
    if not installed():
        raise ShimError("not installed; call install() first")

    ours = _dotted_module_names(_IMPL_DIR)
    return sorted(f"{TARGET}.{name}" for name in django_modules() - ours)
```

- [ ] **Step 6: Run the tests to verify they pass.**

Run: `scripts/run_unit_tests.sh tests.test_milestone_ownership tests.test_shim -v`
Expected: PASS. The meter row in `test_shim.py` still asserts **23** — this task moves no names — and that is the check that the `unshimmed()` restatement is a refactor rather than a change.

- [ ] **Step 7: Add the anchor row for the `context_processors` citation.**

`tests/` is inside the anchor gate's tree, and the reason string above cites `` `django/template/context.py:5` ``. Add to `scripts/anchors.txt` (tab-separated, in the same shape as the neighbouring rows):

```
django/template/context.py:5	django/template/context.py	5	-	_builtin_context_processors = ("django.template.context_processors.csrf",)	-
```

- [ ] **Step 8: Run the whole fast check.**

Run: `. scripts/env.sh && bash scripts/check.sh --fast`
Expected: `all checks passed`, unit suite still green.

- [ ] **Step 9: Commit.**

```bash
git add python/django_rust_templates/_shim.py tests/milestone_ownership.py \
        tests/test_milestone_ownership.py scripts/anchors.txt
git commit -m "M10: the ownership table, asserted against the meter in both directions"
```

---

### Task 2: The criterion and its teeth

The reviewer's replacement for the 5/4 transcribed/fresh split: **transcribe unless you can state the equivalence as a row**. Nine of the nine M10 files are transcriptions, so this rule lands before them, and its teeth are the one `fresh` file the tree already has — `_impl/exceptions.py`, written from scratch at M0, whose equivalence has been a reviewer's judgement rather than an instrument for nine milestones.

**What the rule is, stated so a reader does not over-trust it.** It is a *toll*, not a proof. An equivalence row over the published surface — names, MRO, construction shapes, list identity, `args`, pickle — is stronger than nothing and weaker than a diff, and no arrangement of rows makes a `fresh` file falsifiable the way `scripts/check_transcriptions.py` falsifies a transcription. What makes the rule work is the **cost curve**, not the strength of any one row: writing that equivalence is cheap for a 44-line module of two exception classes and expensive for `context.py`'s dict-stack protocol, and an author discovers which while writing it. So the rule makes `fresh` cost something where `fresh` is the lazy answer, and stays out of the way where `fresh` is the right answer. The reviewer's phrasing is the one to keep in mind: *a forcing function that makes `fresh` expensive where `fresh` is wrong, and an equivalence row where `fresh` is cheap.*

**Files:**
- Create: `tests/pinned_module.py`
- Create: `tests/test_transcription_equivalence.py`
- Modify: `tests/test_interposition.py` (`ShadowingTests`)
- Modify: `python/django_rust_templates/_shim.py` (`SHADOWING`'s docstring)

**Interfaces:**
- Consumes: `_shim.SHADOWING`, `scripts.check_transcriptions.DECLARED`.
- Produces: `tests.pinned_module.load_pinned(relative: str) -> module`; `tests.test_transcription_equivalence.EQUIVALENCE: dict[str, tuple[str, str]]` and `TemplateExceptionsEquivalenceTests`. Tasks 3–7 use `load_pinned`.

- [ ] **Step 1: Write the failing test row.**

In `tests/test_interposition.py`, replace `ShadowingTests.test_each_kind_is_the_transcription_guards_own_answer` with:

```python
    def test_each_kind_is_the_transcription_guards_own_answer(self):
        """``kind`` is compared, not trusted — and each kind has its own warrant.

        It is the one column here that could rot into a second, disagreeing
        account of which files are transcriptions — the account
        ``scripts/check_transcriptions.py`` already owns and already checks
        against each file's marker. So the two are compared directly, and the
        comparison now has two arms because the two kinds carry different
        obligations:

        * ``transcribed`` must be a name that guard declares. The **diff against
          the pin** is the warrant, and it is the only instrument that falsifies
          a transcription (M9's finding 3: a verbatim copy and a wrongly
          transcribed file both keep all 1547 tests green).
        * ``fresh`` must be a name ``tests/test_transcription_equivalence.py`` states
          an equivalence for. A fresh file has no diff of its own, so nothing in
          the transcription guard can see it, and the suite cannot see it
          either — for the same reason. Without this arm ``fresh`` is the free
          option: changing one word in the table above would move a file out of
          every guard in the tree with everything green.

        The class is required to have rows rather than merely to be named,
        because an empty test case is how a warrant becomes a gesture.
        """
        import importlib

        import scripts.check_transcriptions as guard

        from tests import test_transcription_equivalence as equivalence

        for name, kind in sorted(_shim.SHADOWING.items()):
            with self.subTest(name=name):
                self.assertIn(kind, ("transcribed", "fresh"))
                if kind == "transcribed":
                    self.assertIn(
                        name,
                        guard.DECLARED,
                        f"{name} is declared 'transcribed' in SHADOWING but "
                        "check_transcriptions.py does not declare it, so "
                        "nothing reads its diff against the pin",
                    )
                    continue
                self.assertIn(
                    name,
                    equivalence.EQUIVALENCE,
                    f"{name} is declared 'fresh' in SHADOWING but states no "
                    "equivalence in tests/test_transcription_equivalence.py, so "
                    "nothing in this tree can falsify it — transcribe it "
                    "instead, or state the equivalence as rows",
                )
                module_name, class_name = equivalence.EQUIVALENCE[name]
                case = getattr(importlib.import_module(module_name), class_name)
                rows = [a for a in dir(case) if a.startswith("test_")]
                self.assertGreater(
                    len(rows), 0, f"{module_name}.{class_name} has no rows"
                )
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `scripts/run_unit_tests.sh tests.test_interposition.ShadowingTests -v`
Expected: `ModuleNotFoundError: No module named 'tests.test_transcription_equivalence'`.

- [ ] **Step 3: Write the pinned-module reader.**

Create `tests/pinned_module.py`:

```python
"""The pinned checkout's copy of a module we have interposed.

A transcription under ``_impl`` wears a real module's name, so
``importlib.import_module("django.template.context")`` cannot reach Django's file
— that is the whole point of the shim. The only way to hold both objects at once
is to read and execute the pinned file directly, and that is what this does.

**Under a private module name, with ``__package__`` set to the real package.**
Several of these files import relatively (``engine.py``'s ``from .base import
Template``, ``loaders/app_directories.py``'s ``from .filesystem import Loader``),
and a relative import needs a package to resolve against. What those imports bind
is *our* siblings — deliberately, and not a compromise: the only thing a caller
reads off the returned module is the class or function objects it defines
itself, and a class is defined by the file that lexically contains it.

``import_module`` is deliberately not used: it would hand back ours and the
comparison would be against itself, which is the failure ``tests/parity_target.py``
exists to refuse one level up.
"""

import importlib.util
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = PROJECT_ROOT / ".django"


def load_pinned(relative: str):
    """The module at ``.django/django/template/<relative>``, executed fresh.

    ``relative`` is ``_impl``-relative and posix: ``context.py``,
    ``loaders/base.py``.
    """
    path = CHECKOUT / "django" / "template" / relative
    if not path.is_file():
        raise SystemExit(f"no pinned module at {path}")

    package = "django.template"
    if "/" in relative:
        package += "." + relative.rsplit("/", 1)[0].replace("/", ".")

    name = "pinned_" + relative[: -len(".py")].replace("/", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 4: State the `fresh` file's equivalence.**

Create `tests/test_transcription_equivalence.py`:

```python
"""What a ``fresh`` ``_impl`` file owes, stated as rows rather than as a promise.

Every file under ``_impl`` that wears a real module's name is one of two things
(``_shim.SHADOWING``): **transcribed** — a copy of the pinned file, differing
only where ``scripts/check_transcriptions.py``'s ``DECLARED`` says — or
**fresh** — written from scratch with Django as the specification. The two carry
different obligations, and until M10 only the first had a guard:

* a transcription is **falsified** by the diff against the pin, which is why
  ``scripts/check_transcriptions.py`` exists at all;
* a fresh file has no diff of its own, and the suite cannot stand in for one —
  measured as M9's finding 3, where a file whose behaviour is upstream's keeps
  all 1,547 tests green either way.

So ``fresh`` is the honest choice only when the equivalence can be **stated as a
row**, and this module is where it is stated. ``EQUIVALENCE`` maps an ``_impl``
file to the test case that states its equivalence;
``ShadowingTests.test_each_kind_is_the_transcription_guards_own_answer`` fails
when a ``fresh`` file is missing from the table, and the rows below are what make
the entry mean something.

``exceptions.py`` is the only ``fresh`` file in the tree, and it is the one that
made the rule necessary: written from scratch at M0, behaviourally upstream's,
and the only reason anyone knows that is that a reviewer diffed it by hand — an
instrument nothing re-runs.
"""

import pickle
import unittest

from tests.pinned_module import load_pinned

EQUIVALENCE = {
    # `_impl`-relative path → (importable module, TestCase class name).
    "exceptions.py": (
        "tests.test_transcription_equivalence",
        "TemplateExceptionsEquivalenceTests",
    ),
}


class TemplateExceptionsEquivalenceTests(unittest.TestCase):
    """``_impl/exceptions.py`` behaves as ``django/template/exceptions.py`` does.

    The equivalence is stated as rows about the *published surface*: the names,
    the base chain, and the construction protocol over every argument shape the
    class accepts. What it deliberately does not claim is the file's text — the
    differences are docstrings and one expression (`[] if tried is None else
    tried` against `if tried is None: tried = []`), which is exactly the class of
    change a transcription would have to declare and a fresh file does not.

    Every row compares our class against ``load_pinned``'s copy of the same
    class. That is the only way to compare against Django's, for the reason
    `tests/pinned_module.py` gives.
    """

    @classmethod
    def setUpClass(cls):
        import django.template.exceptions as ours

        cls.ours = ours
        cls.pinned = load_pinned("exceptions.py")

    # (args, kwargs) with the sentinels built once, outside the loop: a passed
    # list must come back *identical*, so the two constructions have to be
    # handed the same object rather than two equal ones.
    SENTINEL_LIST = [("origin", "not found")]
    SENTINEL_BACKEND = type("Backend", (), {})

    SHAPES = [
        (("nope",), {}),
        (("nope", []), {}),
        (("nope", None, None, None), {}),
        (("nope",), {}),
        (("nope",), {}),
    ]

    def observed(self, cls, args, kwargs):
        """What one construction produces, or the error it produces instead.

        A ``TypeError`` from the wrong argument shape is an observation like any
        other — the row is that both classes do the *same* thing, and for the
        keyword-only shapes below the same thing is a refusal. The attribute
        reads are inside the ``try`` on purpose: `TemplateSyntaxError` accepts no
        `tried`, and an ``AttributeError`` is part of what this compares.
        """
        try:
            exc = cls(*args, **kwargs)
            return (
                "constructed",
                exc.args,
                exc.tried,
                exc.backend,
                exc.chain,
                [type(error).__name__ for error in exc.chain],
                str(exc),
                repr(exc),
            )
        except Exception as error:  # noqa: BLE001 - the disagreement is the data
            return ("raised", type(error).__name__, str(error))

    def shapes(self):
        """The argument shapes, built once so both sides see the same objects."""
        return [
            (("nope",), {}),
            (("nope", self.SENTINEL_LIST), {}),
            (("nope",), {"tried": self.SENTINEL_LIST}),
            (("nope",), {"backend": self.SENTINEL_BACKEND}),
            (("nope", self.SENTINEL_LIST, self.SENTINEL_BACKEND, []), {}),
            ((), {}),
        ]

    def test_the_public_names_are_the_same_set(self):
        ours = {n for n in vars(self.ours) if not n.startswith("_")}
        theirs = {n for n in vars(self.pinned) if not n.startswith("_")}
        self.assertEqual(ours, theirs)

    def test_each_class_has_the_same_base_chain(self):
        for name in ("TemplateDoesNotExist", "TemplateSyntaxError"):
            with self.subTest(name=name):
                ours = [c.__name__ for c in getattr(self.ours, name).__mro__]
                theirs = [c.__name__ for c in getattr(self.pinned, name).__mro__]
                self.assertEqual(ours, theirs)

    def test_construction_agrees_on_every_argument_shape(self):
        for name in ("TemplateDoesNotExist", "TemplateSyntaxError"):
            for args, kwargs in self.shapes():
                with self.subTest(name=name, args=args, kwargs=sorted(kwargs)):
                    self.assertEqual(
                        self.observed(getattr(self.ours, name), args, kwargs),
                        self.observed(getattr(self.pinned, name), args, kwargs),
                    )

    def test_a_passed_list_is_kept_by_identity(self):
        """The one line the two files spell differently, pinned as behaviour.

        Ours is ``[] if tried is None else tried`` and Django's is
        ``if tried is None: tried = []`` — equal by value either way, and
        different only if a rewrite copied the list or defaulted to a shared
        literal. Identity is the assertion a value comparison cannot make.
        """
        given = [("origin", "x")]
        ours = self.ours.TemplateDoesNotExist("nope", given)
        self.assertIs(ours.tried, given)

    def test_the_message_is_the_only_positional_argument_and_it_pickles(self):
        for name in ("TemplateDoesNotExist", "TemplateSyntaxError"):
            with self.subTest(name=name):
                ours = getattr(self.ours, name)("nope", self.SENTINEL_LIST)
                self.assertEqual(ours.args, ("nope",))
                restored = pickle.loads(pickle.dumps(ours))
                self.assertIs(type(restored), type(ours))
                self.assertEqual(restored.args, ("nope",))
                self.assertEqual(restored.tried, self.SENTINEL_LIST)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: State the two-obligation rule in `_shim.SHADOWING`'s docstring.**

In `python/django_rust_templates/_shim.py`, append this paragraph to the comment block immediately above `SHADOWING` (it currently ends `…a file that collides without being listed here is exactly what the table exists to catch.`):

```python
# **The two kinds carry different obligations, and both are checked.** A
# `transcribed` file is falsified by `scripts/check_transcriptions.py`: the diff
# against the pin is its instrument. A `fresh` file has no diff of its own, so
# its instrument is a stated equivalence —
# `tests/test_transcription_equivalence.py`'s `EQUIVALENCE` — and
# `ShadowingTests.test_each_kind_is_the_transcription_guards_own_answer` refuses
# a `fresh` name that is missing from it. Without that second clause `fresh` is
# the free option: changing one word here would move a file out of every guard in
# the tree with everything green, which is M9's finding 3 one level up.
```

- [ ] **Step 6: Run the tests to verify they pass.**

Run: `scripts/run_unit_tests.sh tests.test_interposition.ShadowingTests tests.test_transcription_equivalence -v`
Expected: PASS. If a row of `TemplateExceptionsEquivalenceTests` fails, the equivalence is **not** stated — fix the classes rather than the row, and say so in the commit message.

- [ ] **Step 7: Run the whole fast check and commit.**

Run: `. scripts/env.sh && bash scripts/check.sh --fast`
Expected: `all checks passed`.

```bash
git add tests/pinned_module.py tests/test_transcription_equivalence.py \
        tests/test_interposition.py python/django_rust_templates/_shim.py
git commit -m "M10: a 'fresh' _impl file owes a stated equivalence, not a promise"
```

---

### Task 3: `_impl/utils.py` (meter 23 → 22)

The first M10 interposition, and the one that proves the generator can express a file with no docstring. `django/template/utils.py` defines `EngineHandler`, which `_impl/__init__.py` already imports to build `engines` — so this commit is what makes the package's singleton ours.

**Files:**
- Modify: `scripts/gen_impl_transcription.py`
- Create: `python/django_rust_templates/_impl/utils.py` (generated)
- Modify: `scripts/check_transcriptions.py`, `python/django_rust_templates/_shim.py`, `tests/milestone_ownership.py`, `tests/test_shim.py`, `tests/test_interposition.py`, `.review/m9-measurements/m9_measure_parse_path.py`

**Interfaces:**
- Consumes: `tests.pinned_module.load_pinned`, `tests.compared_path.instrumented`, `_shim.SHADOWING`.
- Produces: `render_through(tmpdir, source, *, template_name="index.html", **context) -> str` at the top of `tests/test_interposition.py`, and the `ModuleInterpositionTests` class Tasks 4, 5 and 7 add to.

- [ ] **Step 1: Write the failing rows.**

In `tests/test_interposition.py`, add after the imports (`import pathlib`, `import tempfile`, `from django.template import Context, Engine` go beside `import unittest`):

```python
def render_through(tmpdir, source, *, template_name="index.html", **context):
    """Write ``source`` into ``tmpdir`` and render it through a real ``Engine``.

    A real ``Engine``, with the loader spelled as a dotted path, because that is
    the route every Django project and the suite itself take: `find_template_loader`
    does `import_string` on the string, which is the path Review Focus 4 is about.
    ``dirs`` is not passed alongside it — `Engine.__init__` raises
    `ImproperlyConfigured` when `loaders` and `app_dirs` are both given, and the
    tuple form `(path, dirs)` is how Django's own loader tests spell the same
    thing.

    ``template_name`` is keyword-only because it *collided*: written as a plain
    ``name`` parameter it swallowed the first caller's context key — Task 4's row
    renders ``"{{ name }}!"`` with ``name="Ada"``, so the file was named ``Ada``,
    the variable resolved to empty, and the row failed as ``'!' != 'Ada!'``,
    which reads like a resolver bug. `name` is the commonest context key there
    is, so the helper is what gives way.
    """
    pathlib.Path(tmpdir, template_name).write_text(source)
    engine = Engine(
        loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
    )
    return engine.get_template(template_name).render(Context(dict(context)))


class ModuleInterpositionTests(unittest.TestCase):
    """Every module M10 interposes, with the evidence that it is ours.

    Three claims per module, and none of them implies the others:

    * **resolution** — importing the dotted name gives our file. This is what
      `check.sh`'s pre-flight and the meter both read.
    * **identity** — the class a caller gets is not the pinned copy's. Resolution
      and identity differ: a module can be ours while a consumer that imported
      the name earlier still holds Django's object, and `isinstance` is then
      false across the boundary.
    * **reach** — the code path the milestone is about actually enters it.
      Measured with `tests/compared_path.instrumented`, over the parity harness
      the `COMPARED_PATH` table was measured with, so the row and the pin are
      one spelling of the question.

    Rows arrive with the commit that makes them true, so every commit is green.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_utils_resolves_to_our_file(self):
        import django.template.utils as utils

        self.assertIn("_impl", utils.__file__, utils.__file__)

    def test_the_engine_handler_class_is_not_djangos(self):
        import django.template.utils as utils

        from tests.pinned_module import load_pinned

        pinned = load_pinned("utils.py")
        self.assertIsNot(utils.EngineHandler, pinned.EngineHandler)
        self.assertIsNot(
            utils.InvalidTemplateEngineError, pinned.InvalidTemplateEngineError
        )

    def test_the_package_built_its_handler_from_our_class(self):
        """The discriminating row: `django.template.engines` is *our* handler.

        `_impl/__init__.py` is a transcription and does `from .utils import
        EngineHandler`, then `engines = EngineHandler()`. So the singleton that
        `loader.py` reaches through `engines.all()` is built from whichever class
        that import bound — and if the interposition were absent, it would be
        Django's, while every other row here stayed green.
        """
        import django.template as package
        import django.template.utils as utils

        self.assertIs(package.engines.__class__, utils.EngineHandler)

    def test_the_engine_lookup_runs_our_code(self):
        """`engines.all()` is the route out of `utils` into a template load.

        The suite's settings have `TEMPLATES = []` (the project's own
        `tests/__init__.py` configures with `USE_I18N` only, so the global
        default applies), which makes `all()` an empty list and a *safe* call —
        the count is what the row reads, not the result.
        """
        import django.template as package
        import django.template.utils as utils

        from tests.compared_path import instrumented

        with instrumented(utils) as counts:
            self.assertEqual(list(package.engines.all()), [])

        self.assertIn("EngineHandler.all", counts)
```

- [ ] **Step 2: Run to verify they fail.**

Run: `scripts/run_unit_tests.sh tests.test_interposition.ModuleInterpositionTests -v`
Expected: `ModuleInterpositionTests.test_utils_resolves_to_our_file` fails with the path to `.django/django/template/utils.py`; the identity row passes by accident (both are Django's object, so `assertIsNot` is true only against a *fresh* pinned copy — it does pass, and Step 3 is what makes it mean something); `test_the_engine_lookup_runs_our_code` fails with `KeyError`/empty counter.

- [ ] **Step 3: Teach the generator the no-docstring shape, and add the module.**

In `scripts/gen_impl_transcription.py`:

```python
MODULES = {
    "base.py": "django/template/base.py",
    "smartif.py": "django/template/smartif.py",
    "utils.py": "django/template/utils.py",
}
```

```python
def render(name: str) -> str:
    """The exact contents ``_impl/<name>`` must have."""
    if name not in MODULES:
        raise SystemExit(f"{name}: not a transcription; known: {sorted(MODULES)}")
    source = (CHECKOUT / MODULES[name]).read_text()
    marker = f"# TRANSCRIBED: {MODULES[name]} (Django {DJANGO_VERSION})\n"
    if source.startswith('"""\n'):
        return '"""\n' + marker + "\n" + source[4:]
    # **Not every transcription has a docstring to sit in.** `context.py`,
    # `engine.py`, `utils.py` and `loaders/base.py` open with imports, and
    # `loaders/__init__.py` is 0 bytes in the pin. For those the marker is a
    # comment on line 1 followed by a blank line: `check_transcriptions.py`'s
    # `MARKER_WINDOW` is 4, so both forms satisfy the guard, and the blank is
    # what makes every insertion exactly two lines — which is what the declared
    # regions and the measured `COMPARED_PATH` offsets are written against.
    return marker + "\n" + source
```

In `main()`, the module argument is now a file name and the two message strings lose their `.py` suffix:

```python
        path = IMPL / name
        if args.check:
            got = path.read_text() if path.is_file() else ""
            if got != want:
                failures += 1
                print(f"{name} is not what the generator produces "
                      f"(run: scripts/gen_impl_transcription.py {name})")
            else:
                print(f"{name} matches the generator")
```

and the docstring's usage block becomes `python -I scripts/gen_impl_transcription.py base.py smartif.py utils.py`. The keys are file names because that is the spelling `DECLARED`, `COMPARED_PATH` and `SHADOWING` all use — four tables, one spelling of a file's identity.

- [ ] **Step 4: Generate the file and prove the generator is idempotent.**

Run:
```bash
. scripts/env.sh
.venv/bin/python -I scripts/gen_impl_transcription.py utils.py
.venv/bin/python -I scripts/gen_impl_transcription.py base.py smartif.py
.venv/bin/python -I scripts/gen_impl_transcription.py --check
```
Expected: three `… matches the generator` lines and exit 0. The second command regenerates the two M9 files under the new key spelling; if either changes, the new `render()` is not byte-compatible with what M9 shipped and that is a failure to investigate, not to accept.

- [ ] **Step 5: Register the transcription in the three tables.**

In `scripts/check_transcriptions.py`:

```python
    # 0 functions: nothing a parity harness executes reaches `utils`. Measured
    # with `.review/m10-measurements/m10_measure_compared_path.py`, which
    # interposes all nine M10 files verbatim in a scratch universe and runs both
    # corpora through `django_parse` — `EngineHandler` is entered through
    # `engines.all()`, which no parity source calls. An empty entry is *present*
    # rather than absent, because the guard asserts key-set equality with
    # `DECLARED`: a missing entry is a declared region nothing checks.
    "utils.py": [],
```

```python
    "utils.py": (
        "django/template/utils.py",
        [
            # The marker and its blank separator, ours only — a pure insertion
            # (`None` upstream). Upstream has no docstring here, so the marker is
            # a comment on line 1 rather than the first line of a docstring; no
            # executable line, so the snippet is clause (5)'s required note.
            ((1, 2), None, "# TRANSCRIBED: django/template/utils.py (Django 6.0.9)"),
        ],
    ),
```

In `python/django_rust_templates/_shim.py`:

```python
    "utils.py": "transcribed",
```

In `tests/milestone_ownership.py`: delete `"utils": "M10",` from `OWNERS` and add it to `INTERPOSED`:

```python
    "M10": ["utils"],
```

- [ ] **Step 6: Move the meter.**

In `tests/test_shim.py`, `test_unshimmed_reports_progress`:

```python
        remaining = _shim.unshimmed()
        self.assertEqual(len(remaining), 22, sorted(remaining))
        self.assertNotIn("django.template.base", remaining)
        self.assertNotIn("django.template.smartif", remaining)
        self.assertNotIn("django.template.utils", remaining)
```

and its docstring's history line becomes `(25 at M8, 24 with `base`, 23 with `smartif`, 22 with `utils` — the milestone's number)`.

- [ ] **Step 7: Generalize the re-measurement instrument.**

`.review/m9-measurements/m9_measure_parse_path.py` is cited by `check_transcriptions.py`'s `COMPARED_PATH` comment and by `test_interposition`'s failure message as *the* way to regenerate an entry, and it is hardcoded to `base.py`. Make it take module arguments:

```python
TARGETS = ["base.py"]
...
parser.add_argument("module", nargs="*", default=TARGETS, help="…")
```

and inside the loop replace `guard.module_of("base.py")` with `guard.module_of(name)`, printing one block per module. In `tests/test_interposition.py`, the stale-pin failure message becomes:

```python
                    f"the functions the parity harness executes in {name} have "
                    f"changed, so COMPARED_PATH['{name}'] is stale — regenerate "
                    "it with .review/m9-measurements/m9_measure_parse_path.py "
                    f"(its first argument is the module: {name})",
```

- [ ] **Step 8: Measure, and confirm the pin is `[]`.**

Run:
```bash
. scripts/env.sh
DJANGO_RUST_TEMPLATES=0 .venv/bin/python -I \
    .review/m10-measurements/m10_measure_compared_path.py 2>&1 | grep -A3 '^utils.py'
```
Expected: `utils.py: 0 of N defined function(s) reached` followed by `COMPARED_PATH["utils.py"] = [` and `]`. If anything is reached, paste the measured spans into `COMPARED_PATH` and say in the commit message which source reached them.

- [ ] **Step 9: Run the tests, the fast check, and the Django suite.**

Run:
```bash
. scripts/env.sh
.venv/bin/python -m unittest tests.test_interposition tests.test_shim tests.test_milestone_ownership
bash scripts/check.sh --fast
bash scripts/run_django_suite.sh
```
Expected: unit rows green; `all checks passed`; `Ran 1547 tests in …s` / `OK (skipped=6)`. The suite is the milestone's real risk check — `_impl/__init__.py` builds `engines` from this module at import, so every path that touches settings goes through it.

- [ ] **Step 10: Commit.**

```bash
git add -A
git commit -m "M10: interpose django.template.utils — the meter is 22"
```

---

### Task 4: `_impl/context.py` (meter 22 → 21)

`Context` is the module the whole suite goes through (measured: 984 of the 1,547 tests). It is a transcription with no delegation, so the risk is entirely in the interposition — and the measured reach is unusual enough to pin: the parity harness executes exactly three functions, all reached by `{% verbatim %}`.

**Files:**
- Modify: `scripts/gen_impl_transcription.py`, `scripts/check_transcriptions.py`, `python/django_rust_templates/_shim.py`, `tests/milestone_ownership.py`, `tests/test_shim.py`, `tests/test_interposition.py`
- Create: `python/django_rust_templates/_impl/context.py` (generated)

**Interfaces:**
- Consumes: `render_through`, `load_pinned`, `instrumented`, `tests.test_parser_parity.django_parse`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing rows.**

Add to `ModuleInterpositionTests`:

```python
    def test_context_resolves_to_our_file(self):
        import django.template.context as context

        self.assertIn("_impl", context.__file__, context.__file__)

    def test_the_context_classes_are_not_djangos(self):
        import inspect

        import django.template.context as context

        from tests.pinned_module import load_pinned

        pinned = load_pinned("context.py")
        for name in (
            "BaseContext",
            "Context",
            "ContextDict",
            "RequestContext",
            "RenderContext",
        ):
            with self.subTest(name=name):
                ours = getattr(context, name)
                self.assertIsNot(ours, getattr(pinned, name))
                self.assertIn("_impl", inspect.getfile(ours))

    def test_a_template_renders_through_our_context(self):
        """The end-to-end row: `Context` is on the render path, not beside it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rendered = render_through(tmpdir, "{{ name }}!", name="Ada")
        self.assertEqual(rendered, "Ada!")

    def test_the_parse_harness_runs_our_context(self):
        """The compared path, pinned to the three functions it measured.

        Measured (`.review/m10-measurements/m10_measure_compared_path.py`): the
        parity harness executes `BaseContext.__init__`, `BaseContext._reset_dicts`
        and `Context.__init__` and nothing else in this file — and every source
        that reaches them is a `{% verbatim %}` row. The harness's Python half
        runs `Parser.parse`, and Django's `do_verbatim` renders the block *at
        parse time* with a fresh `Context()` (`defaulttags.py:1607`), which is
        the route. So the row runs that source, and it will move the day that
        line stops being the route — which is why the failure message names it.

        The second half is a **positive control**: the same call, with the pinned
        copy of this file instrumented instead, must reach nothing. Without it a
        non-zero count would only show that the instrument counted something.
        """
        import django.template.context as context

        from tests.compared_path import instrumented
        from tests.pinned_module import load_pinned
        from tests.test_parser_parity import django_parse

        with instrumented(context) as counts:
            django_parse("{% verbatim %}a{% endverbatim %}")
        self.assertIn("Context.__init__", counts)
        self.assertIn("BaseContext.__init__", counts)

        with instrumented(load_pinned("context.py")) as pinned_counts:
            django_parse("{% verbatim %}a{% endverbatim %}")
        self.assertEqual(sum(pinned_counts.values()), 0)
```

- [ ] **Step 2: Run to verify they fail.**

Run: `scripts/run_unit_tests.sh tests.test_interposition.ModuleInterpositionTests -v`
Expected: `test_context_resolves_to_our_file` fails naming Django's file; `test_the_parse_harness_runs_our_context` fails with an empty counter for `Context.__init__`.

**Two corrections to that expectation, both measured while executing this step.**

1. `test_the_parse_harness_runs_our_context` does **not** fail before the interposition, and the reason is worth keeping: `instrumented(context)` instruments whatever object `django.template.context` currently is, which is Django's — and `do_verbatim` does run Django's `Context.__init__`. So the row is green on both sides of the commit, and what makes it discriminating is the pin plus the resolution row, not a red-to-green transition. Only five rows go red here: resolution, and the five classes.
2. `test_a_template_renders_through_our_context` fails for a reason that is not the interposition at all — `render_through`'s third parameter was named `name`, so the row's `name="Ada"` set the *template file's* name and `{{ name }}` rendered empty (`'!' != 'Ada!'`). The helper's signature is now `render_through(tmpdir, source, *, template_name="index.html", **context)`.

- [ ] **Step 3: Generate the file and register it.**

Run: `. scripts/env.sh && .venv/bin/python -I scripts/gen_impl_transcription.py context.py`

Then, in `scripts/check_transcriptions.py` (both tables), `_shim.py` (`SHADOWING`) and `tests/milestone_ownership.py` (`OWNERS` → `INTERPOSED["M10"]`), add the entry. The measured spans, from `.review/m10-measurements/m10_measure_compared_path.py` — already shifted by the two lines the marker adds, and re-checked by `test_the_compared_path_is_the_measured_one` on every unit run:

```python
    "context.py": [
        (31, 32, "BaseContext.__init__"),
        (34, 40, "BaseContext._reset_dicts"),
        (146, 155, "Context.__init__"),
    ],
```

```python
    "context.py": (
        "django/template/context.py",
        [
            # The marker and its blank separator, ours only. Upstream opens with
            # an import, so the marker is a comment on line 1; no executable
            # line, hence clause (5)'s note rather than statements.
            ((1, 2), None, "# TRANSCRIBED: django/template/context.py (Django 6.0.9)"),
        ],
    ),
```

```python
    "context.py": "transcribed",
```

```python
    "M10": ["utils", "context"],
```

and the meter in `tests/test_shim.py` becomes `self.assertEqual(len(remaining), 21, sorted(remaining))` with `self.assertNotIn("django.template.context", remaining)`.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `scripts/run_unit_tests.sh tests.test_interposition tests.test_shim tests.test_milestone_ownership -v`
Expected: PASS, including `test_the_compared_path_is_the_measured_one`, which re-measures `context.py`'s spans in-process and fails if the numbers above are wrong.

- [ ] **Step 5: Run the fast check and the suite.**

Run: `. scripts/env.sh && bash scripts/check.sh --fast && bash scripts/run_django_suite.sh`
Expected: `all checks passed`; `Ran 1547 tests in …s` / `OK (skipped=6)`.

- [ ] **Step 6: Commit.**

```bash
git add -A
git commit -m "M10: interpose django.template.context — the meter is 21"
```

---

### Task 5: `_impl/engine.py` (meter 21 → 20)

`Engine` is where the loaders are constructed and where the settings enter, and it is the last of the three single-module interpositions. Its measured reach is also three functions: `Engine.__init__` and the two lookups it makes eagerly, `get_template_builtins` and `get_template_libraries`. They are **plain methods** — the `lru_cache`d one in this file is `Engine.get_default`, a `staticmethod` the harness never reaches, because it builds its Engines directly and never asks for the global default. (Corrected while executing Task 5: the first draft of this paragraph called the two lookups `lru_cache`d classmethods, which they are not.)

**Files:**
- Modify: `scripts/gen_impl_transcription.py`, `scripts/check_transcriptions.py`, `python/django_rust_templates/_shim.py`, `tests/milestone_ownership.py`, `tests/test_shim.py`, `tests/test_interposition.py`
- Create: `python/django_rust_templates/_impl/engine.py` (generated)

**Interfaces:**
- Consumes: `render_through`, `load_pinned`, `instrumented`, `django_parse`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing rows.**

Add to `ModuleInterpositionTests`:

```python
    def test_engine_resolves_to_our_file(self):
        import django.template.engine as engine

        self.assertIn("_impl", engine.__file__, engine.__file__)

    def test_the_engine_class_is_not_djangos(self):
        import inspect

        import django.template.engine as engine

        from tests.pinned_module import load_pinned

        self.assertIsNot(engine.Engine, load_pinned("engine.py").Engine)
        self.assertIn("_impl", inspect.getfile(engine.Engine))
        # `django.template.Engine` is the same object, because the package
        # `__init__` imports it from this module by name.
        import django.template as package

        self.assertIs(package.Engine, engine.Engine)

    def test_the_parse_harness_runs_our_engine(self):
        """The compared path here is `Engine.__init__` and its two lookups.

        Measured: `django_parse` builds a **fresh** `Engine` per call
        (`test_parser_parity._library_engine`), so the harness enters
        `Engine.__init__`, `Engine.get_template_builtins` and
        `Engine.get_template_libraries` on every source — and because the engine
        is fresh each time, the count is per call rather than once per process.
        The positive control is the pinned copy, which must reach nothing.
        """
        import django.template.engine as engine

        from tests.compared_path import instrumented
        from tests.pinned_module import load_pinned
        from tests.test_parser_parity import django_parse

        with instrumented(engine) as counts:
            django_parse("{{ x }}")
        for label in (
            "Engine.__init__",
            "Engine.get_template_builtins",
            "Engine.get_template_libraries",
        ):
            self.assertIn(label, counts)

        with instrumented(load_pinned("engine.py")) as pinned_counts:
            django_parse("{{ x }}")
        self.assertEqual(sum(pinned_counts.values()), 0)

    def test_a_template_loads_and_renders_through_our_engine(self):
        """The milestone's premise in one row: our `Engine`'s own load path.

        `get_template` → `find_template` → `find_template_loader` (which does
        `import_string` on the dotted path) → the loader's `get_contents`. At
        this commit the loader itself is still Django's, which is why the row
        asserts the *engine* functions and not the loader's; Task 7 extends it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "greet.html").write_text("{{ name }}!")
            engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )
            with instrumented(django_engine) as counts:
                rendered = engine.get_template("greet.html").render(
                    Context({"name": "Ada"})
                )
        self.assertEqual(rendered, "Ada!")
        self.assertIn("Engine.get_template", counts)

    def test_loaders_with_app_dirs_is_still_refused(self):
        """An argument-combination contract, kept across interposition.

        `Engine.__init__` raises `ImproperlyConfigured("app_dirs must not be set
        when loaders is defined.")` — it is the guard every misconfigured
        `TEMPLATES` setting hits — and no `.django` test reaches it through our
        engine, so without this row a transcription that dropped the `if` would
        ship silently. The message is asserted, not just the type: it is what a
        user of this software sees.
        """
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaisesMessage(
            ImproperlyConfigured, "app_dirs must not be set when loaders is defined."
        ):
            Engine(app_dirs=True, loaders=["django.template.loaders.filesystem.Loader"])
```

with `import django.template.engine as django_engine` added to that row's local imports (module-level imports in `tests/test_interposition.py` stay as they are, to keep each row's dependency visible).

- [ ] **Step 2: Run to verify they fail.**

Run: `scripts/run_unit_tests.sh tests.test_interposition.ModuleInterpositionTests -v`
Expected: `test_engine_resolves_to_our_file` fails naming Django's file.

- [ ] **Step 3: Generate the file and register it.**

Run: `. scripts/env.sh && .venv/bin/python -I scripts/gen_impl_transcription.py engine.py`

Register in the four tables as in Tasks 3 and 4, with the measured pin:

```python
    "engine.py": [
        (22, 65, "Engine.__init__"),
        (122, 123, "Engine.get_template_builtins"),
        (125, 129, "Engine.get_template_libraries"),
    ],
```

```python
    "engine.py": (
        "django/template/engine.py",
        [
            ((1, 2), None, "# TRANSCRIBED: django/template/engine.py (Django 6.0.9)"),
        ],
    ),
```

```python
    "engine.py": "transcribed",
```

```python
    "M10": ["utils", "context", "engine"],
```

and the meter in `tests/test_shim.py` becomes `20` with `assertNotIn("django.template.engine", remaining)`.

- [ ] **Step 4: Run the tests, the fast check and the suite, then commit.**

Run: `scripts/run_unit_tests.sh tests.test_interposition tests.test_shim tests.test_milestone_ownership && bash scripts/check.sh --fast && bash scripts/run_django_suite.sh`
Expected: green throughout; `Ran 1547 tests in …s` / `OK (skipped=6)`.

```bash
git add -A
git commit -m "M10: interpose django.template.engine — the meter is 20"
```

---

### Task 6: The tooling that assumed `_impl` is flat

Four gaps, three of them latent defects found by reading and one an unrun check. They must land **before** Task 7, because Task 7 is what makes each of them live: a subpackage under `_impl/` is the first thing in the tree that the guard cannot see, the parity target cannot key, and `test_shim.py`'s partial-subpackage row cannot coexist with.

**Files:**
- Modify: `scripts/check_transcriptions.py`, `tests/parity_target.py`, `tests/test_shim.py`, `scripts/check.sh`
- Create: `.review/m10-measurements/m10_guard_teeth.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `tests.test_shim.SubstituteDjangoTemplateTests`'s re-picked subject; the guard's `rglob` clause.

- [ ] **Step 1: Fix the guard's undeclared-file clause.**

In `scripts/check_transcriptions.py`'s `check()`, the clause currently reads:

```python
    marked = sorted(path.name for path in IMPL.glob("*.py") if _marked(path))
```

Replace it with:

```python
    # `rglob`, not `glob`, and `_impl`-relative keys, not basenames. Both halves
    # matter and both were latent until M10: `glob("*.py")` cannot see
    # `loaders/base.py`, so a marker-bearing file in a subpackage is invisible to
    # the clause below `_check_marker` and to this loop — and `path.name` would
    # key it `base.py`, which is *another* transcription's key, so the entry it
    # looked up would be the wrong file's while every clause passed.
    marked = sorted(
        path.relative_to(IMPL).as_posix() for path in IMPL.rglob("*.py") if _marked(path)
    )
```

and the same file's `unmarked` filter, which builds `IMPL / name` from a posix relative string:

```python
    unmarked = [name for name in unmarked if IMPL.joinpath(*name.split("/")).is_file()]
```

- [ ] **Step 2: Fix the parity target's key.**

In `tests/parity_target.py`, add below `IMPL_DIR`:

```python
def key_of(source: Path) -> str:
    """``_impl``-relative posix path — the key the guard's tables use.

    ``source.name`` was the key until M10, and it was correct while ``_impl`` was
    flat. For the first file in a subpackage it is silently wrong in the worst
    available way: ``loaders/base.py`` keys as ``base.py``, which *is* a
    transcription — a different one — so `DECLARED` answers, the regions returned
    are `base.py`'s, and every clause here passes while checking the wrong file.
    """
    return source.relative_to(IMPL_DIR.resolve()).as_posix()
```

and replace the four `source.name` uses with `key = key_of(source)` (computed once, after the `is_relative_to(IMPL_DIR)` assertion, and used in the marker assertion, the `DECLARED` assertion, `declared_regions()` and the `COMPARED_PATH.get()`).

- [ ] **Step 3: Re-pick the partial-subpackage subject — and fix why it expired.**

`tests/test_shim.py::test_a_partial_subpackage_still_falls_back_for_its_siblings` uses `django.template.loaders`, asserts the directory does **not** exist, and `shutil.rmtree`s it in `finally` — which Task 7 makes destructive to committed work.

**The re-pick alone is not the fix, and the review is where that was established.** `django/template/` has exactly **two** subpackages: `backends` and `loaders`. The row needs a subpackage that is real in Django and not yet ours, so after Task 7 takes `loaders` there is one candidate left, and M13 takes it — moving the subject to `backends` buys three milestones and then the same commit-deleting failure arrives at M13, where whoever hits it will not have this plan. **The expiry is in the cleanup, not in the name**: the row asserts its subject is absent and removes it unconditionally, so it can only run in a world where the subject does not exist, and it destroys evidence on the day that stops being true.

So do both. Re-pick to `django.template.backends` (correct for M10 — it is M13's, as far from the frontier as the tree allows), and make the cleanup **create-if-missing and remove-only-what-it-created**:

```python
        package_dir = (
            PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "backends"
        )
        # **Record what was here before writing anything.** The first version of
        # this row asserted the directory did not exist and removed it in
        # `finally`, which is a row that can only run while its subject is
        # absent — so it went from a test to a deletion the day M13 gave
        # `_impl/backends/` a transcription, and the failure would have looked
        # like a broken fallback rather than a test cleaning up after itself.
        # Whether or not the subject exists is now the row's input rather than
        # its premise: the probe file is ours to remove, and the `__init__.py`
        # and the directory are ours to remove only if we made them.
        created_package_dir = not package_dir.exists()
        package_dir.mkdir(parents=True, exist_ok=True)
        init = package_dir / "__init__.py"
        had_init = init.exists()
        probe = package_dir / "probe.py"
        try:
            if not had_init:
                init.write_text("")
            probe.write_text("MARKER = 'ours'\n")
            _shim.install()
            ...  # the cache-drop, the three assertions and the meter row, as the
                 # row has them today; only `loaders` → `backends.probe` changes
        finally:
            probe.unlink(missing_ok=True)
            cache = package_dir / "__pycache__"
            if cache.is_dir():
                for stale in cache.glob("probe.*.pyc"):
                    stale.unlink(missing_ok=True)
            if not had_init:
                init.unlink(missing_ok=True)
            if created_package_dir:
                shutil.rmtree(package_dir, ignore_errors=True)
```

**Two corrections to the first draft of this step, both found while writing it.**

`init.write_text(MARKER)` named a constant `tests/test_shim.py` does not have; the
marker text was meant to be the probe module's *source line*, and writing it into
`__init__.py` is the second mistake — it makes the positive half of the row an
assertion about a file M13 will own, so the row expires at M13 with a `None !=
'ours'` failure exactly where it was supposed to stop being able to expire. The
probe is now a file only this row writes, at a name Django does not have
(`probe.py`), and the finder serves it: `_ShimFinder.find_spec` returns a spec for
any `_impl/*.py`, not only for names that shadow. `backends` has no
`filesystem.py` sibling, which is why the probe set is `("probe.py",)` here and not
the `("filesystem.py",)` the first draft carried over from `loaders`.

The package still has to be ours for the submodule to be ours at all, and the
meter row (`assertNotIn("django.template.backends", remaining)`) is what says so.

and update the row's docstring paragraph that justifies the subject:

```
        ``django.template.backends`` and ``django.template.backends.probe`` are
        the subjects because they are a package with siblings and are owned by
        M13 (§10.3), not by the milestone being worked. The subject used to be
        `loaders`, chosen as "as far from the interposition frontier as the tree
        allows" — which expired at M10. The re-pick alone would have expired
        again at M13, because `django/template/` has only two subpackages, so the
        cleanup is the part that matters: the row now records what it found,
        writes only files that were not there, and removes only what it wrote.
        It has no expiry left, and a future milestone can change the subject
        freely without first checking whether it is about to delete someone's
        work.
```

- [ ] **Step 4: Run the guard's teeth against a tree that should fail it.**

Create `.review/m10-measurements/m10_guard_teeth.py` — the M9 instrument's shape, for the clause Step 1 fixes:

```python
"""Does the undeclared-file clause actually see a file in a subpackage?

The clause is "every file carrying a marker must be declared, and vice versa".
Before Task 6 it iterated `IMPL.glob("*.py")` and keyed by basename, so the file
this question is about — `_impl/loaders/base.py`, once M10 creates it — was
invisible: not opened, not diffed, and its regions not checked, with every other
clause green.

The teeth are *measured* rather than argued: a scratch copy of `_impl` is made,
a marker-bearing file is planted at `loaders/probe.py` (a name Django does not
have, so `shadowing_files()` is unaffected), and the guard is run twice — once
with the pre-fix clause and once with the post-fix clause, restored from git
rather than retyped. The pre-fix run printing "no failures" is the defect; the
post-fix run naming `loaders/probe.py` is the repair.

    . scripts/env.sh
    .venv/bin/python -I .review/m10-measurements/m10_guard_teeth.py
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"
GUARD = ROOT / "scripts" / "check_transcriptions.py"
MARKER = "# TRANSCRIBED: django/template/loaders/probe.py (Django 6.0.9)\n"

scratch = pathlib.Path(tempfile.mkdtemp(prefix="m10-guard-teeth-"))
shutil.copytree(IMPL, scratch / "_impl")
planted = scratch / "_impl" / "loaders" / "probe.py"
planted.parent.mkdir(parents=True, exist_ok=True)
planted.write_text(MARKER + "\n# planted\n")

# The guard reads its paths from module-level constants, so the scratch universe
# is installed by rewriting those two names in a copy of the guard.
source = GUARD.read_text()
source = source.replace(
    'IMPL = ROOT / "python" / "django_rust_templates" / "_impl"',
    f'IMPL = pathlib.Path({str(scratch / "_impl")!r})',
)
patched = scratch / "guard.py"
patched.write_text(source)

result = subprocess.run(
    [sys.executable, "-I", str(patched)], capture_output=True, text=True
)
print("guard exit:", result.returncode)
print(result.stdout.strip() or "(no output)")
print(result.stderr.strip(), file=sys.stderr)
shutil.rmtree(scratch, ignore_errors=True)
```

Run it before Step 1's fix (with the working tree at the previous commit's guard) and after; record both outputs in the commit message.

**Corrected while writing it: the sketch above does not run, and the M9 shape is
better than the shape it imitates.** The copy at `scratch / "guard.py"` derives
`ROOT` from its own `__file__`, so `CHECKOUT` becomes `<tmp>/.django`, which does
not exist — patching `IMPL` alone leaves the guard looking for the pinned tree
beside itself and it dies before reaching the clause under test. It also measures
only the `glob` half.

What was written instead keeps M9's mechanism — import the guard as a module and
repoint `IMPL`, so no copy is made and no path is derived from a temp directory —
and takes the *source* from `git show <rev>:scripts/check_transcriptions.py`,
exec'd with `__file__` set to the real guard's so `ROOT` resolves correctly. Three
variants, not two:

* `before` — restored from the revision, not retyped;
* `rglob-only` — the post-fix source with the key mutated back to `path.name`,
  one token, **because the basename defect cannot be seen under the real `before`
  clause**: there the file is invisible for the `glob` reason, so the second
  defect would otherwise be fixed blind and untested;
* `after` — the working tree.

Each runs against a scratch copy of the real `_impl` with the real `DECLARED` and
`COMPARED_PATH`, so the only differences between cells are the guard revision and
the one planted file. Two plants: `loaders/probe.py` (a name Django lacks) and
`loaders/base.py` (a real transcription's bytes at a subpackage path — the shape
Task 7 creates for real, and the one that keys as `base.py`). Measured result:
`before` is **silent on both**; `rglob-only` names `['probe.py']` — by basename,
losing the subpackage — and stays **silent on `loaders/base.py`**; `after` names
both correctly. The `BEFORE_REV` SHA is written into the script rather than
`HEAD~1`, because the script outlives the commit it measures and `HEAD~1` would
then quietly turn the `before` column into a second `after`.

- [ ] **Step 5: Put the generator on the fast path.**

`scripts/gen_impl_transcription.py --check` is invoked by nothing today, so a hand edit *inside* a declared region — the one change `check_transcriptions.py` permits by construction — is unguarded. In `scripts/check.sh`, beside the two existing generator checks:

```bash
  "$PROJECT_ROOT/.venv/bin/python" -I "$HERE/gen_impl_transcription.py" --check
```

with a comment naming why it belongs there: the transcription guard says *which* regions may differ, and this says the rest of each file is still generated rather than edited.

**Corrected: "beside the two existing generator checks" and Step 6's expectation
contradict each other, and Step 6 is right.** Those two sit inside `if [ "$FAST"
-eq 0 ]`, because `gen_python_case_fixups.py` compiles a Rust probe — so a check
placed literally beside them does *not* run on `--fast`, which is the run Step 6
tells you to make and to read the new check's line out of. It went on the fast
path instead, directly under the transcriptions guard, which is where it belongs
on the merits: the two read the same four `_impl` files and `.django/`, and
together they are one claim — the guard says which regions may differ, the
generator says the rest of each file is still generated. Step 6's "all three
transcriptions" is stale by two: `MODULES` holds four (`context`, `engine`,
`smartif`, `utils`), and `base.py` is excluded by name — it delegates to Rust by
hand, so `HAND_WRITTEN` refuses it and `--check` would otherwise have been
rewriting that delegation with upstream's text.

- [ ] **Step 6: Run the fast check and commit.**

Run: `. scripts/env.sh && bash scripts/check.sh --fast`
Expected: `all checks passed`, including the new generator check (which regenerates all four transcriptions and compares).

```bash
git add scripts/check_transcriptions.py tests/parity_target.py tests/test_shim.py \
        scripts/check.sh .review/m10-measurements/m10_guard_teeth.py
git commit -m "M10: the guard, the parity target and the shim rows stop assuming _impl is flat"
```

---

### Task 7: `loaders/` (meter 20 → 14)

The milestone's six-name task, and the one with a hazard the others do not have: `django.template.loaders` becomes a *package we provide*, so its `__path__` must still reach Django's directory for any sibling we have not taken — and `app_directories.Loader` subclasses `filesystem.Loader` across that boundary. It also closes the loop Task 3 opened: `loaders/app_directories.py` imports `get_app_template_dirs` from `django.template.utils`, so `utils` must already be ours for the package's imports to resolve inside a single spelling of the tree.

**Files:**
- Modify: `scripts/check_transcriptions.py`, `python/django_rust_templates/_shim.py`, `tests/milestone_ownership.py`, `tests/test_shim.py`, `tests/test_interposition.py`
- Create: `python/django_rust_templates/_impl/loaders/{__init__,base,filesystem,app_directories,cached,locmem}.py` (generated)

**Interfaces:**
- Consumes: `render_through`, `load_pinned`, `instrumented`, `Engine.find_template_loader`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing rows.**

Add to `ModuleInterpositionTests`:

```python
    LOADER_MODULES = [
        "loaders",
        "loaders.base",
        "loaders.filesystem",
        "loaders.app_directories",
        "loaders.cached",
        "loaders.locmem",
    ]

    def test_every_loader_module_resolves_to_our_file(self):
        import importlib

        for name in self.LOADER_MODULES:
            with self.subTest(name=name):
                module = importlib.import_module(f"django.template.{name}")
                self.assertIn("_impl", module.__file__, module.__file__)

    def test_the_package_still_reaches_djangos_siblings(self):
        """The fallback survives one level down, which is the whole hazard.

        `django.template.loaders` is a package now, and a package's `__path__`
        is built by the finder from *our* directory plus Django's. If the second
        half were missing, every loader we have not taken would stop resolving —
        and the failure would appear when a milestone is half-finished, which is
        exactly when the fallback is needed.
        """
        import django.template.loaders as loaders

        paths = [pathlib.Path(p).resolve() for p in loaders.__path__]
        self.assertIn(
            (PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "loaders").resolve(),
            paths,
        )
        self.assertTrue(
            any(p.name == "loaders" and "_impl" not in str(p) for p in paths),
            f"Django's own loader directory is not on {paths}",
        )

    def test_the_loader_classes_are_not_djangos(self):
        import importlib
        import inspect

        from tests.pinned_module import load_pinned

        for name, relative in [
            ("loaders.base", "loaders/base.py"),
            ("loaders.filesystem", "loaders/filesystem.py"),
            ("loaders.app_directories", "loaders/app_directories.py"),
            ("loaders.cached", "loaders/cached.py"),
            ("loaders.locmem", "loaders/locmem.py"),
        ]:
            with self.subTest(name=name):
                module = importlib.import_module(f"django.template.{name}")
                self.assertIsNot(module.Loader, load_pinned(relative).Loader)
                self.assertIn("_impl", inspect.getfile(module.Loader))

    def test_our_app_directories_loader_subclasses_our_filesystem_loader(self):
        """The cross-module inheritance, which is what a half-built package breaks.

        `loaders/app_directories.py` does `from .filesystem import Loader`. With
        the package interposed, that relative import resolves through our
        `__path__` — so the base class is ours, and this row is what says so.
        Interposing one of the two without the other would leave Django's class
        as the base of ours, and `isinstance` against either would then be false
        for half the objects in the tree.
        """
        import django.template.loaders.app_directories as app_directories
        import django.template.loaders.filesystem as filesystem

        self.assertTrue(
            issubclass(app_directories.Loader, filesystem.Loader),
            app_directories.Loader.__mro__,
        )

    def test_the_engines_loader_class_is_ours(self):
        """Review Focus 4, as a row: a dotted path in settings resolves to us.

        `Engine.find_template_loader` does `import_string("django.template.
        loaders.filesystem.Loader")`, which is the spelling in `TEMPLATES` for
        every Django project and in Django's own loader tests. So the assertion
        that matters is not that the module is ours but that the *object the
        engine instantiated* is ours — and that it is not the pinned copy's.
        """
        from tests.pinned_module import load_pinned

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ name }}")
            engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )
            loader = engine.template_loaders[0]

        import django.template.loaders.filesystem as filesystem

        self.assertIsInstance(loader, filesystem.Loader)
        self.assertNotIsInstance(loader, load_pinned("loaders/filesystem.py").Loader)

    def test_every_loader_class_loads_and_renders(self):
        """Each loader on its own path, constructed the way upstream spells it.

        Five constructors, four of them differing, and the differences are what
        the row is for — a transcription that "tidied" a signature fails here
        rather than in the suite, where the message would be about a template
        that did not load. Every construction below is copied from the pinned
        file being exercised:

        * ``base.py`` — ``Loader(engine)``; its ``get_template_sources`` raises
          ``NotImplementedError``, so it has no path of its own and this row's
          assertion about it is the ``issubclass`` below.
        * ``filesystem.py`` — ``Loader(engine, dirs=None)``.
        * ``cached.py`` — ``Loader(engine, loaders)``, where ``loaders`` is a
          list of **specs** that ``__init__`` resolves through
          ``engine.get_template_loaders()``; the inner filesystem loader is
          therefore spelled as a tuple, which is what gives it its directory.
        * ``locmem.py`` — ``Loader(engine, templates_dict)``, keyed by
          ``origin.name`` — the template name as written, not a path.
        * ``app_directories.py`` — no ``__init__`` of its own; it inherits
          ``FilesystemLoader``'s and overrides ``get_dirs()``. The override is
          the class, so its row is the next one.
        """
        import django.template.loaders.base as base
        import django.template.loaders.cached as cached
        import django.template.loaders.locmem as locmem

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ a }}")

            filesystem_engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )
            self.assertEqual(
                filesystem_engine.get_template("index.html").render(
                    Context({"a": "x"})
                ),
                "x",
            )
            self.assertTrue(
                issubclass(filesystem_engine.template_loaders[0].__class__, base.Loader)
            )

            cached_engine = Engine(
                loaders=[
                    (
                        "django.template.loaders.cached.Loader",
                        [
                            (
                                "django.template.loaders.filesystem.Loader",
                                [tmpdir],
                            )
                        ],
                    )
                ]
            )
            loader = cached_engine.template_loaders[0]
            self.assertIsInstance(loader, cached.Loader)
            self.assertEqual(
                cached_engine.get_template("index.html").render(Context({"a": "x"})),
                "x",
            )
            # The cache is this loader's behaviour, and the row is that it is
            # populated by a load rather than merely declared.
            self.assertIsNot(loader.get_template_cache, {})

            locmem_engine = Engine(
                loaders=[
                    ("django.template.loaders.locmem.Loader", {"index.html": "{{ a }}"})
                ]
            )
            self.assertIsInstance(locmem_engine.template_loaders[0], locmem.Loader)
            self.assertEqual(
                locmem_engine.get_template("index.html").render(Context({"a": "x"})),
                "x",
            )

    def test_the_app_directories_loader_keeps_its_override(self):
        """``app_directories.Loader`` is three lines, and all three are assertions.

        It declares no ``__init__`` — it inherits ``FilesystemLoader``'s — and
        overrides ``get_dirs()`` to return ``get_app_template_dirs("templates")``,
        which is **ours** from Task 3. That override is why passing ``dirs`` to it
        does nothing, and this row asserts exactly that: with no app declaring a
        ``templates/`` directory (the suite's settings have ``INSTALLED_APPS =
        []``), the loader finds nothing even though it was given a directory that
        has the file. A transcription that dropped the override would find it.

        That it finds templates when an app *does* declare them is
        ``.django/tests/template_tests/test_loaders.py::AppDirectoriesLoaderTests``
        — the milestone's ``test_loaders`` gate — which is where the app-registry
        machinery that row needs already lives.
        """
        from django.template import TemplateDoesNotExist

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ a }}")
            engine = Engine(
                loaders=[
                    ("django.template.loaders.app_directories.Loader", [tmpdir])
                ]
            )
            loader = engine.template_loaders[0]
            self.assertEqual(list(loader.get_dirs()), [])
            with self.assertRaises(TemplateDoesNotExist):
                engine.get_template("index.html")

- [ ] **Step 2: Run to verify they fail.**

Run: `scripts/run_unit_tests.sh tests.test_interposition.ModuleInterpositionTests -v`
Expected: every loader row fails naming Django's file or Django's class.

- [ ] **Step 3: Generate the six files.**

Run:
```bash
. scripts/env.sh
mkdir -p python/django_rust_templates/_impl/loaders
for f in __init__ base filesystem app_directories cached locmem; do
    .venv/bin/python -I scripts/gen_impl_transcription.py "loaders/$f.py"
done
.venv/bin/python -I scripts/gen_impl_transcription.py --check
```
Expected: six `wrote … (N lines)` lines, then `… matches the generator` for all nine. `loaders/__init__.py` is 0 bytes upstream and becomes the two-line marker file — check that the generator produced it (`wc -c` gives 76, not 0), because an empty file here would fail the guard's marker clause with a message about a file that is in `DECLARED`.

- [ ] **Step 4: Register the six in the four tables.**

`scripts/check_transcriptions.py`, `COMPARED_PATH` — one comment block, six empty entries:

```python
    # 0 functions each: nothing a parity harness executes reaches the loaders.
    # Measured with `.review/m10-measurements/m10_measure_compared_path.py`; the
    # loaders are entered through `Engine.get_template`, which no parity source
    # calls (the harness parses, it does not render). Present rather than absent,
    # for the key-set-equality clause.
    "loaders/__init__.py": [],
    "loaders/base.py": [],
    "loaders/filesystem.py": [],
    "loaders/app_directories.py": [],
    "loaders/cached.py": [],
    "loaders/locmem.py": [],
```

`DECLARED` — the marker region per file. Four of the six begin with a docstring, so their marker sits inside it and the region is `((2, 3), None, …)`; `loaders/__init__.py` is empty and `loaders/base.py` opens with an import, so theirs is `((1, 2), None, …)`:

```python
    # `loaders/__init__.py` is **0 bytes** in the pin, so ours is the marker and
    # its blank line and nothing else — a two-line file whose whole content is
    # the claim that it transcribes an empty one.
    "loaders/__init__.py": (
        "django/template/loaders/__init__.py",
        [
            ((1, 2), None, "# TRANSCRIBED: django/template/loaders/__init__.py (Django 6.0.9)"),
        ],
    ),
    "loaders/base.py": (
        "django/template/loaders/base.py",
        [
            ((1, 2), None, "# TRANSCRIBED: django/template/loaders/base.py (Django 6.0.9)"),
        ],
    ),
```
and the same shape for the other four with `((2, 3), None, …)`. **Check each one against the guard rather than against this plan**: run `python -I scripts/check_transcriptions.py` and let it tell you which side the marker landed on — that is the measurement, and a region that covers nothing is a failure with a message naming the file.

**The premise the +2 arithmetic rests on, stated because it is invisible in the table.** Every `COMPARED_PATH` span for these nine is upstream's line number plus two, and that is true only because each transcription inserts its marker and nothing else — **none of these nine rewrites its module docstring**, unlike `_impl/__init__.py`, which does. A file that both inserted a marker and edited its docstring would shift its spans by two *plus* the docstring delta, and a uniform +2 would then be wrong for every function in it while the table looked like every other row. So: when registering each file, confirm with `python -I scripts/gen_impl_transcription.py --check` (and by eye, for the first one) that the only diff at the top of the file is the marker and its blank line. If a docstring ever does need rewriting, the span arithmetic is re-derived for that file rather than copied.

`_shim.SHADOWING` gains six rows, all `"transcribed"`. `tests/milestone_ownership.py` moves all six names:

```python
    "M10": ["utils", "context", "engine", "loaders", "loaders.base",
            "loaders.filesystem", "loaders.app_directories", "loaders.cached",
            "loaders.locmem"],
```

and `tests/test_shim.py`'s meter becomes `self.assertEqual(len(remaining), 14, sorted(remaining))`, with the six `assertNotIn` lines for the loader names and the history line in the docstring extended to `…, 20 with engine, 14 with loaders — the milestone's number`.

**Five things this task needs that its Files list does not name, all found by
running it.**

* `scripts/gen_impl_transcription.py` — **the step does not run without it.**
  `render()` refuses any name not in `MODULES`, so `gen_impl_transcription.py
  "loaders/__init__.py"` exits before reading anything. Six rows were added. They
  are the first keys in that table that are not a single segment, which is the
  table's own rule holding rather than bending: key is the `_impl`-relative posix
  path, value is `django/template/` + key, for all ten rows.
* `tests/milestone_ownership.py`'s **`OWNERS`, not just `INTERPOSED`.** The six
  names were also in `OWNERS`, and `test_no_name_is_both_owned_and_taken` failed
  on exactly those six — the table's own discipline working. They leave `OWNERS`
  in this commit, so §10.3's M10 row is now empty there and the milestone is done
  with the table.
* `tests/test_collected_tests.py` — `PINNED` 347 → 354, the seven new rows. The
  row fired on the first `--fast` run and named the delta, which is what it is
  for. (Task 8 takes it further.)
* `scripts/anchors.txt` and the prose in `tests/test_shim.py` — adding six rows to
  `SHADOWING` moves `if installed():` from `_shim.py:399` to `:405`, and the
  manifest row key moves with it, because the key is the citation as written.
* `loaders/__init__.py` is **67 bytes**, not the 76 this step predicts. The
  prediction was arithmetic done in prose and not run; the file is the marker
  line (65 bytes including its newline) plus the blank separator. The check that
  matters is that it is non-zero, and it is.

One thing Step 2 predicts that is not true: **four of the seven rows pass before
the interposition.** `test_the_engines_loader_class_is_ours`,
`test_every_loader_class_loads_and_renders`,
`test_our_app_directories_loader_subclasses_our_filesystem_loader` and
`test_the_app_directories_loader_keeps_its_override` assert behaviour Django's own
loaders already have, so they cannot fail first — `load_pinned` loads a *separate*
module object, so even `assertNotIsInstance` against it holds while the imported
class is Django's. The twelve that do fail first are the three resolution/identity
rows; these four are regression rows, and they are the ones that would catch a
transcription that dropped `get_dirs()` or tidied a constructor signature.

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `scripts/run_unit_tests.sh tests.test_interposition tests.test_shim tests.test_milestone_ownership tests.test_loader_case_sensitivity -v`
Expected: PASS. `test_the_compared_path_is_the_measured_one` re-measures all nine new entries in-process — the loaders must measure `[]`, and if any is not empty the paste in Step 4 is wrong.

- [ ] **Step 6: Run the fast check and the suite.**

Run: `. scripts/env.sh && bash scripts/check.sh --fast && bash scripts/run_django_suite.sh`
Expected: `all checks passed`; `Ran 1547 tests in …s` / `OK (skipped=6)`. The loaders are load-bearing for `test_loaders`, `syntax_tests` and every `{% include %}`/`{% extends %}` row — this is the run that says the six transcriptions are faithful.

- [ ] **Step 7: Commit.**

```bash
git add -A
git commit -m "M10: interpose the loaders package — the meter is 14"
```

---

**Reviewer finding, fixed in the commit after Task 7's: the instrument was blind
to descriptors, and `FilterExpression.args_check` was on the compared path.**

`tests/compared_path.py` wrapped what `inspect.isfunction` accepts, in both the
wrap sweep and `definitions`. Five shapes a class dict holds a function in were
therefore invisible — plain, `staticmethod`, `classmethod`, `property`,
`cached_property` — plus a function under an `lru_cache`. The reviewer named
`FilterExpression.args_check`, a `staticmethod` at `base.py:827-844`, and every
element of the finding was measured against the pinned tree before anything
changed: the predicate at `compared_path.py:195`/`:199`; the class-dict type
`staticmethod`; **85** calls over the 566-source corpus; the span sitting in the
*gap* between the pinned `(744, 788)` and `(876, 927)`; and no declared region of
`base.py` covering it — so the defect was latent rather than live, which is the
only reason it cost nothing to find late.

`_invoked` is now the table of kinds (unwrap to the callable, re-box in the same
kind), `residue` fails loudly on a kind it has no case for, and `members` is one
iterator the three readers share. Re-measured, the reviewer's prediction held
exactly: **one row moved, `base.py` 28 → 29, span unchanged at 827-844.** Four
more members joined `definitions` without joining any pin — `Origin.loader_name`,
`PartialTemplate.source`, `Engine.template_loaders`,
`get_app_template_dirs` — and two corrections belong on the record:

* **`_innermost` had to follow `__wrapped__` as well as descriptors.** The first
  version of the fix dropped `Context.bind_template`, `RenderContext.push_state`
  and `RequestContext.bind_template` from `definitions`, because they are
  `@contextlib.contextmanager` and the object in the class dict is a helper
  *created in `contextlib.py`*. Following `__wrapped__` restores them with the
  span this file wrote.

  **Corrected in the close-out: the reason first written here was wrong, and the
  measurement that falsifies it is one line.** This block said the previous sweep
  "gave them spans read out of `contextlib.py`". It did not.
  `inspect.getsourcelines` unwraps `__wrapped__` **internally** before it reads,
  so those three spanned `context.py` then exactly as they do now — measured,
  `Context.bind_template`'s helper has `co_firstlineno` 303 in `contextlib.py`
  and a span of 157-165 in `context.py`. The file was never the defect. What the
  old sweep got wrong was **ownership**: `_innermost` returned the `contextlib.py`
  helper, so `_is_ours` was False and the member was dropped. The distinction is
  the whole reason the walk and the span read are separate questions —
  `getsourcelines` unwraps, `getsourcefile` reads `__code__.co_filename` and does
  not — and it is the confusion the finding below then reproduced one function
  later.
* **`TokenType.__new__` is no longer listed, and that is the correct answer.**
  It is `Enum.__new__`, injected into the class by the metaclass from `enum.py`.
  The previous sweep listed it with a span read out of `enum.py`. It is not
  `base.py`'s code and cannot be inside a declared region of `base.py`, so
  counting it was a false-positive generator for clause (4), not a safeguard.

`tests/test_compared_path_instrument.py` pins all of it, including the
reviewer's scenario run for real: a synthetic declared region on 827-844 is
injected into `DECLARED` and the guard's own `check()` must refuse it.

**Reviewer finding, fixed in the close-out: `residue()` was silent for exactly
the kind it exists to catch — the same defect one level up.**

`residue` filtered by `_is_ours`, which asks `_owning_file`, which asks
`_innermost` — **the table whose incompleteness `residue` exists to detect. The
check was asking the thing it was checking.** Measured, with a descriptor class
defined in another module holding a function the subject module wrote:

    _innermost -> the Lazy *instance*    _invoked -> None
    _callable_member -> True             _is_ours -> False
    residue -> []                        definitions -> {}
    instrumented -> no complaint

Silently uncounted, no `UnhandledMember` — `args_check` again, surviving inside
the guard written to make `args_check` impossible.

The mechanism: for an unhandled member `_innermost` falls through to `return
value`, the descriptor *instance*; `getsourcefile` on an instance raises, so the
owning file is unknown and `_is_ours`'s first arm is False; the second arm needs
the descriptor **class** to have been written in the module being measured — and
every kind that has ever caused trouble here (`staticmethod`, `property`,
`cached_property`, `lru_cache`) is defined elsewhere. So the arm meant to catch
"a kind nobody has written a case for" fired only for the rarest shape there is.
**`_owning_file` returns `None` for two different facts — "no function here" and
"I cannot see through this kind" — and the old arm read both as *not ours*.** For
`definitions` that conflation is harmless; for `residue`, the one place where
*I don't know* has to be loud, it was the whole defect.

The fix inverts the polarity: report unless **positively** foreign
(`_is_foreign`). Skip `ismemberdescriptor`/`isgetsetdescriptor` (C slot and
`__dict__`/`__weakref__`, which cannot hold a Python function); skip a builtin
(no Python source anywhere, so it cannot be a `def` this module wrote — this is
what excludes `TokenType._new_member_`, i.e. `object.__new__`, the same family
as the `__new__` already dropped deliberately); skip a known owning file that is
not this module's; and **report an unknown owning file**, which is the arm that
was missing.

Measured on the closed tree: **0 members reported over all twelve
`COMPARED_PATH` modules** — so the rule is free — and `['Subject.reached']` on
the `Lazy` case. Two rows pin it, both asserting the premise as well as the
verdict: the foreign-descriptor case asserts `_is_ours` is False *and* that
`residue` reports anyway (either alone is satisfied by a wrong instrument), and
the builtin arm asserts `_innermost` really is a C function before asserting it
goes unreported.

**One trap, measured rather than reasoned about.** `inspect.isroutine` is not
the exclusion to reach for: it includes `ismethoddescriptor`, true for any
object with `__get__` and no `__set__`, so it swallows precisely the case being
hunted — `inspect.isroutine(Lazy_instance)` is `True`, and the reviewer's first
draft of the rule returned `[]` for the very example it was written to catch.

**Reviewer finding, fixed in the close-out: the `__wrapped__` walk was
unconditional, and it could leave the file.**

The same arm, one direction over. `_innermost` followed `__wrapped__` without
asking whose file the current function was in. Correct for
`@contextlib.contextmanager`, where the wrapper is `contextlib.py`'s and the
`def` behind it is ours; wrong for a `functools.wraps` wrapper **we** wrote
around a foreign function, where the current function *is* the `def` we wrote
and `__wrapped__` points out of the file. Measured with the reviewer's
reproduction: `getsourcefile(joined)` is the subject's own file, `_innermost`
returned `os.path.join`, `_is_ours` said False, `definitions` carried only the
other member — **an owned, recognised, executed function missing from the
compared path**, which is the `args_check` under-count arriving *through* the fix
for `args_check`.

The fix is one condition — follow `__wrapped__` only while the current function
is not already ours (`_written_elsewhere`) — and it serves both directions.
Measured pin-neutral: **0 of the twelve `COMPARED_PATH` modules' `definitions`
change**, fast check green.

**And the span read had the same disagreement, which the first version of the
`ForeignSpan` guard could not see.** `span_of` uses `getsourcelines`, which
unwraps; the guard's first version compared `_defining_file(written)` — the
*wrapper's* `co_filename` — so for that same wrapper it agreed with itself and
recorded **72-92 of `<frozen posixpath>`** (that is `def join`) as this module's
`def inner`. Measured: the span for `functools.wraps`-wrapped `os.path.join` is
posixpath's lines 72-92, and `span_of`'s first version reported only the
integers. The row that caught it printed the *test file's* lines 72-92 in its
failure message, which is the hazard in one sentence — **two files can share a
line number and a span is two integers** — and it is why `span_of` now returns
the file alongside the span and `definitions` compares *that*, taken from the
same `inspect.unwrap` `getsourcelines` uses. Re-measured with the unwrapping
comparison: still **0 of the twelve** read a span from a foreign file, so the
class is absent from the corpus and now impossible rather than merely unlikely.
Two rows execute both arms of the raise.

---

### Task 8: §2's case-sensitivity hole

§2 states it as a consequence, not a wish: `FileSystemLoader`'s case-sensitivity is unexercised by the suite on any platform we will run, because the upstream guard is `os.path.normcase`, which is the identity function on POSIX — *structural, not environmental*. The spec schedules the project-side test at M10, which is the milestone that owns the loader.

**Files:**
- Create: `tests/test_loader_case_sensitivity.py`

**Interfaces:**
- Consumes: `django.template.engine.Engine`, `django.template.loaders.filesystem.Loader`, `django.template.context.Context`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test.**

Create `tests/test_loader_case_sensitivity.py`:

```python
"""§2's coverage hole, closed from outside Django's tree.

Django's row is `.django/tests/template_tests/test_loaders.py`'s
`test_case_sensitivity`, and it is *guarded* rather than failing:

    @unittest.skipUnless(
        os.path.normcase("/TEST") == os.path.normpath("/test"),
        "This test only runs on case-sensitive file systems.",
    )

`normcase` is the identity function on POSIX, so the condition is `"/TEST" ==
"/test"` → False everywhere except Windows. **The filesystem is irrelevant**: a
case-sensitive APFS volume would not recover the row, because the guard is
evaluated before any file is opened. And there is no folded branch to port —
`grep -n normcase` over `django/template/` returns nothing; the loader is
`safe_join(template_dir, template_name)` inside `get_template_sources`.

So this module reproduces the *question* the guarded row asks — does joining a
name onto a configured directory fold case? — rather than a behaviour that does
not exist, and it runs on every platform. That matters because the loader it
exercises is code we own from M10 (`_impl/loaders/filesystem.py`), and because
the no-edit rule means the upstream row can never be unskipped by us.

The two rows are deliberately different in kind: the first is pure path
arithmetic and asserts Django's own expectations, and the second touches the
filesystem and therefore *measures* the volume's case sensitivity instead of
assuming it — a row that skipped on the wrong volume would be the same hole one
level down.
"""

import pathlib
import tempfile
import unittest

from django.template import Context, Engine
from django.template.exceptions import TemplateDoesNotExist


def sources(dirs, name):
    """The template origins a filesystem loader produces for ``name``."""
    engine = Engine(loaders=[("django.template.loaders.filesystem.Loader", dirs)])
    loader = engine.template_loaders[0]
    return [origin.name for origin in loader.get_template_sources(name)]


class CaseSensitivityTests(unittest.TestCase):
    def setUp(self):
        import importlib

        filesystem = importlib.import_module("django.template.loaders.filesystem")
        self.assertIn("_impl", filesystem.__file__, filesystem.__file__)

    def test_djangos_row_holds_without_its_platform_guard(self):
        """Django's assertions, against our loader, on this platform.

        Nothing is opened: `get_template_sources` is `safe_join` per configured
        directory, so both expectations are path arithmetic and both hold
        anywhere. That is the point — the upstream row's guard hides a question
        whose answer does not depend on the platform at all.
        """
        self.assertEqual(
            sources(["/dir1", "/DIR2"], "index.html"),
            ["/dir1/index.html", "/DIR2/index.html"],
        )
        self.assertEqual(
            sources(["/DIR1", "/DIR2"], "/DIR1/index.HTML"),
            ["/DIR1/index.HTML"],
        )

    def test_a_case_mismatch_is_not_a_template_where_the_volume_is_case_sensitive(self):
        """The filesystem half, with the expectation *measured* rather than assumed.

        A probe writes `probe.html` and asks whether `PROBE.HTML` exists — the
        same question in miniature, asked of the same volume — so this row runs
        on a case-sensitive volume and on a case-insensitive one, and asserts the
        matching expectation rather than skipping. The probe is a directory
        lookup, not a loader call, so it cannot be satisfied by the code under
        test.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "probe.html").write_text("probe")
            case_sensitive = not pathlib.Path(tmpdir, "PROBE.HTML").exists()

            pathlib.Path(tmpdir, "Index.html").write_text("{{ name }}")
            engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )

            if case_sensitive:
                with self.assertRaises(TemplateDoesNotExist):
                    engine.get_template("index.html")
            else:
                self.assertEqual(
                    engine.get_template("index.html").render(Context({"name": "x"})),
                    "x",
                )

    def test_the_configured_directory_keeps_its_case(self):
        """The other direction: our loader must not fold the *configured* side.

        A directory spelled `/Straße` or `/DIR2` is joined as written; folding it
        would make a loader find templates in a directory nobody configured.
        """
        self.assertEqual(sources(["/Mixed/Case"], "a.html"), ["/Mixed/Case/a.html"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `scripts/run_unit_tests.sh tests.test_loader_case_sensitivity -v`
Expected: `setUp` fails with Django's `loaders/filesystem.py` path — Task 7 has not landed. (If Tasks 7 and 8 are being run in one sitting, this is the row's *first* honest run: it fails until `_impl/loaders/filesystem.py` exists.)

- [ ] **Step 3: Run it again after Task 7 and fix any expectation that is wrong.**

Run: `scripts/run_unit_tests.sh tests.test_loader_case_sensitivity -v`
Expected: PASS on a case-insensitive volume through the second branch, and PASS on a case-sensitive one through the first. The `sources()` expectations are Django's own and are copied from the upstream row; if one of them fails, the failure is real and is about our `filesystem.py`, not about this test.

- [ ] **Step 4: Run the fast check and commit.**

Run: `. scripts/env.sh && bash scripts/check.sh --fast`

```bash
git add tests/test_loader_case_sensitivity.py
git commit -m "M10: §2's FileSystemLoader case-sensitivity hole, closed project-side"
```

**Corrected in Step 1, and the correction is measured.** The second `sources`
expectation above is not upstream's verbatim, and the difference was checked
rather than argued. Upstream's `source_checker(["/dir1", "/DIR2"])` pairs the
name `/DIR1/index.HTML` with **both** directories and asserts it comes back as
`["/DIR1/index.HTML"]` — an answer only `normcase` folding produces. On this
platform:

    upstream pairing -> []
    normcase('/TEST') == normpath('/test'): False

`[]` is the same `safe_join` containment guard that `test_directory_security`
exercises, comparing `/DIR1/index.HTML` against `/dir1/` without folding and
rejecting both directories. So the module pairs the name with directories whose
case matches (`["/DIR1", "/DIR2"]`), which is the shape the question is actually
about — the name's case is preserved, not folded onto the directory's — and the
docstring records why. The `False` is also the confirmation that upstream's row
is skipped here for the reason §2 gives: the guard is `normcase`, evaluated
before any file is opened.

**And upstream's input is pinned too, not only left in that paragraph** (the
reviewer's SHOULD-FIX, and the reason is this project's own rule — a measurement
that lives only in prose has the lifetime of prose). The `[]` is doing work the
substituted row cannot: it is the only thing in the tree that would notice
`safe_join`'s containment guard starting to *fold*, and
`test_directory_security` would not, because folding there makes the guard more
permissive in the same direction and its rejections stay rejections. So
`test_the_directorys_case_is_not_folded_onto_the_name` asserts
`sources(["/dir1", "/DIR2"], "/DIR1/index.HTML") == []`. The two rows now cover
upstream's question and upstream's input separately: the first is the half that
can be ported, the second the half that cannot.

**And the filesystem row's branch is measured too.** On this volume
`Path(tmpdir, "PROBE.HTML").exists()` is True, so `case_sensitive` is False and
the `else` arm is the live one: the loader finds `Index.html` for `index.html`
through a real `get_template` and `render`, not through a mock. The
case-sensitive arm is unreachable here by construction, which the row states
rather than hides.

Step 4's `git add` is superseded: the fast check fails on the collection pin
(361 → 364, exactly the three rows this task adds), so the commit carries
`tests/test_collected_tests.py` as well — the rule that file states about
itself.

---

### Task 9: Close-out

The milestone's numbers, recorded in the two places that carry them, and the goal question handed forward rather than answered.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.3's M10 row)
- Modify: `.review/m10-recon.md` §8 (a closing note, if the section's forward references need it)

**Interfaces:**
- Consumes: every count this milestone produced.
- Produces: nothing.

- [ ] **Step 1: Measure the close-out numbers.**

Run:
```bash
. scripts/env.sh
bash scripts/check.sh
.venv/bin/python -I -c "
import sys; sys.path[:0] = ['python', 'scripts/shimpath', '.django']
import django_rust_templates
from django_rust_templates import _shim
_shim.install()
print(len(_shim.unshimmed()), _shim.unshimmed())
"
```
Expected: `all checks passed` with the unit suite's count visible in the output, `Ran 1547 tests in …s` / `OK (skipped=6)` from the suite step, and `14 ['django.template.autoreload', 'django.template.backends', …]` from the meter. Record all three, plus the unit-suite count, in the step below.

- [ ] **Step 2: Correct §10.3's M10 row.**

Replace the row:

```
| M10 | Interpose `context`, `engine`, `utils`, `loaders`, `origin` | `test_context`, `test_engine`, `test_loaders`, `test_origin`; project-side `FileSystemLoader` case-sensitivity test (§2) |
```

with a row stating: **four** modules and the nine meter entries (`loaders` is six names), so the meter is 23 → 14; `origin` is **not** a module at 6.0.9 — `Origin` is a class in `base.py`, which M9 owns — so the gate is `test_context` (25), `test_engine` (10) and `test_loaders` (21), and `test_origin` is M9's gate rather than this milestone's; the project-side case-sensitivity test (§2) landed as `tests/test_loader_case_sensitivity.py`; the ownership table (`tests/milestone_ownership.py`) now carries the partition that §10.3's rows did not, and the two names it leaves undecided (`autoreload`, `context_processors`) are recorded as the §2 goal question rather than assigned to M14; the counts at close — `scripts/check.sh` green, `Ran 1547 tests … OK (skipped=6)`, unit suite at the number Step 1 measured, meter **14**.

Two constraints on the text: it carries no anchor-shaped citation (the spec is inside the anchor gate's tree, so a new citation needs a manifest row, and this row needs none), and the `Ran 1547 tests` count sits within twelve lines of `check.sh`, which licenses it for `scripts/check_suite_counts.py`.

- [ ] **Step 3: Re-measure the blast radius and record the comparison.**

Run:
```bash
. scripts/env.sh
DJANGO_RUST_TEMPLATES=0 .venv/bin/python -I \
    .review/m10-measurements/m10_measure_module_blast_radius.py 2>&1 | tail -20
```
Expected: the same population as the recon recorded — the instrument wraps the functions each `.django` module defines and counts which tests enter them, and after M10 those functions are *ours*, so a difference is information rather than a defect. Record both numbers in the commit message. If the count moved, name the module whose reach changed before writing anything else: M9's lesson was that a blast-radius figure the recon called interposition-independent was not (the `{% verbatim %}` parse-time render), and the honest record is the measured pair.

- [ ] **Step 4: Commit.**

```bash
git add docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md
git commit -m "M10 close-out: §10.3's M10 row corrected to four modules; the meter is 14"
```

**Measured, and Step 3's command is wrong in one token.** `DJANGO_RUST_TEMPLATES=0`
is refused outright — the instrument's guard is
`if not os.environ.get("DJANGO_RUST_TEMPLATES") == "1": sys.exit(...)` — and with
the shim genuinely off it would measure stock Django rather than our
transcriptions, which is the opposite of what Step 3 asks for. The pre-landing
recon ran it with the shim **on** (its own docstring: "run it the way the suite
is run"), and that is the run the pair is against. Correct command:

```bash
. scripts/env.sh
.venv/bin/python -I .review/m10-measurements/m10_measure_module_blast_radius.py
```

**The pair is identical.** All eight targets report `is already ours`, and every
figure reproduces exactly:

| module | recon (Django's files) | close (ours) |
| --- | --- | --- |
| `context` | 984 / 95,051 | 984 / 95,051 |
| `engine` | 1166 / 41,987 | 1166 / 41,987 |
| `loaders.base` | 1053 / 10,282 | 1053 / 10,282 |
| `loaders.cached` | 1038 / 25,249 | 1038 / 25,249 |
| `loaders.locmem` | 974 / 10,854 | 974 / 10,854 |
| `loaders.filesystem` | 85 / 676 | 85 / 676 |
| `utils` | 46 / 164 | 46 / 164 |
| `loaders.app_directories` | 34 / 58 | 34 / 58 |
| **any of the four** | **1232** / 1547 (79.6%) | **1232** / 1547 (79.6%) |

The instrument resolves each target with `importlib.import_module` and filters on
`__module__`, so the close-out run wrapped **our** functions, not Django's — no
count moved *because no test changed which code it reaches*, which is what a
transcription is for. The paragraph in the instrument's docstring that still said
M10 had not landed is replaced with this pair.

And the module the recon singled out did not move: §3a called
`loaders.filesystem` "a loader the suite barely uses" at 85 tests, and it is
still 85. **No module's reach changed, so there is no module to name** — which is
the one outcome Step 3's "if the count moved" clause does not cover, and it is
recorded here rather than left as an absent paragraph.

- [ ] **Step 5: Report the goal question to the user.**

M10 does **not** edit §10.1's sentence or §10.3's M14 row: both say the end state is `unshimmed()` empty, and the recon's §2 measured that no row reaches zero without interposing glue §6's vendoring policy exists to refuse (two names: `autoreload`, `context_processors`). The report to the user states the question and the recommendation — that the target become the *set* `UNDECIDED` with a reason per name, which is what `tests/milestone_ownership.py` now expresses and what `tests/test_milestone_ownership.py` asserts — and leaves the decision to them, because changing what §2's acceptance criterion means is theirs and not a milestone's.

**The reason is not politeness, and the report says so** (the reviewer's
addition). §2 is what the whole project is measured against; editing it to match
what the implementation can reach is the criterion drifting toward the work, and
the drift is invisible afterwards because the new text reads as settled. An
acceptance criterion is the user's to move.

**And each option is stated with its cost**, because a target stated without one
gets chosen by whichever is easier to say. The two options differ in exactly one
place:

* `unshimmed()` **empty** — the criterion as written. Cost: §6's vendoring policy
  has to move, because the only way to reach zero is to interpose
  `autoreload` (two signal receivers and a directory walk, with no template
  machinery of ours to get right and nothing for a test to falsify) and
  `context_processors` (which does have behaviour, so it is a real candidate
  rather than a leaf). Both are glue.
* `unshimmed()` **== the `UNDECIDED` set** — the criterion restated as a set with
  a reason per name. Cost: §2's acceptance criterion changes meaning, which is
  the user's decision and not a milestone's.

---

## Self-Review

**Spec coverage.** §2's case-sensitivity item → Task 8. §6's ownership split → Task 1's table (and the milestone adds no Rust, which §6 requires). §10.1's "a module is interposed only when it is complete" → the nine transcriptions are complete by construction, and each task's row set is the evidence. §10.3's M10 row → Tasks 3–8 implement it and Task 9 corrects it. §10.3's vacuity rule → every gate here is `tests/`-side: `test_context`/`test_engine`/`test_loaders` are non-vacuous only *because* this milestone interposes the modules they exercise, and the rows that make it measurable are in `tests/test_interposition.py`.

**Placeholders.** None. Task 7's five-constructor row was the one place a plan of this shape is tempted to write "construct each loader appropriately" — it was written from the pinned `cached.py`, `locmem.py`, `base.py` and `app_directories.py`, read in full, and the four signatures it depends on are quoted in the row's docstring.

**A dependency the plan states rather than leaves implicit:** `app_directories.py` imports `get_app_template_dirs` from `django.template.utils`, which Task 3 interposes — so the loader package's transcription chain is only closed because `utils` comes first. Task 7's ordering is not arbitrary, and `test_the_app_directories_loader_keeps_its_override` is the row that would fail if it were reversed (the app-directories `get_dirs()` would still be Django's, and the row's empty-dirs assertion would pass for the wrong reason). The ordering is called out again in Task 7's own File Structure entry.

**Type consistency.** `dotted_suffix` / `django_modules` (Task 1) are used by Tasks 3–7 through `tests/milestone_ownership.py`; `render_through` is defined in Task 3 and used in Tasks 4, 5, 7 and 8 (as `sources()`' sibling); `load_pinned` is defined in Task 2 and used in Tasks 2–7; `instrumented` and `measure` are the existing `tests/compared_path.py` API, and `COMPARED_PATH` entries are `(first, last, label)` triples everywhere, matching what `test_the_compared_path_is_the_measured_one` reads.

**Review Focus.** Each of the five lines names the task that pins it: (1) Task 2, (2) Tasks 6 and 7, (3) Task 8, (4) Task 7, (5) Task 5.
