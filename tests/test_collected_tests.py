"""The unit suite's own collection, pinned as a number.

``scripts/check.sh`` discovers with ``-p 'test_*.py'``. That pattern is a claim
about the tree that nothing checked — and M10 Task 2 is what it cost.
``tests/transcription_equivalence.py`` held a table and its seven rows, named
without the ``test_`` prefix, so **its rows were collected by nothing**. Every
other check was green, including ``check.sh --fast``'s ``all checks passed`` with
the file sitting in the tree, and ``ShadowingTests`` went on asserting that the
table existed and had rows: a guard checking that a guard exists, while the
content it was guarding never ran.

It was found by the unit count not moving — 323 where 330 was expected. That is
the detector, and this module is it written as a row instead of as a number in a
message. Same shape as ``_shim.unshimmed()``'s pinned count: the number moves in
the commit that changes it, so the commit has to say by how much and why.

What a count sees that a sweep over file names cannot:

* a module that *is* collected but whose rows are all skipped at runtime;
* a class that does not inherit ``TestCase``, so the loader drops it;
* a subdirectory without an ``__init__.py``, which ``discover`` cannot recurse
  into whatever its files are called — ``tests/differential/`` is that shape, and
  is currently data-only.

None of those are visible to ``grep`` over names and all of them are visible
here. The pin is not a substitute for reading the tree; it is the thing that
tells you the tree changed underneath you.
"""

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The count `scripts/check.sh`'s discovery produces. Update it in the same commit
# as the change that moves it, and only after asking whether the *delta* is the
# one you intended — a jump of four where you expected seven is what this row is
# for.
#
# 370 → 371 is M11 Task 1's registry-escape row in
# `tests/test_compared_path_instrument.py`; 371 → 374 is the same task's
# `LoaderTagsInterpositionTests`, three rows. Both deltas are the rows added, so
# no module went uncollected on the way.
#
# 374 → 378 is M11 Task 2, four rows: `test_filter_inventory`'s
# "the interposed module registers the pinned registry", `test_filter_parity`'s
# "the comparison target is not rust owned", and two in
# `tests/test_filter_parity.py`'s neighbourhood that the same task needed —
# the delta is again the rows added and not a module falling out of the pattern.
#
# 378 → 382 is the rest of Task 2, and it is written down because the pin spent a
# while four behind: the four rows are `FilterRoutingTests`' three routing rows
# (Steps 6-8: the rebound filter, the `stringfilter` coercion, and the
# doubled-wrapper discriminator) plus the `needs_autoescape` row that Step 8's
# second bullet needed. Measured the other way as well — `git diff tests/` since
# the previous commit shows `+8` rows and no removals, and 374 + 8 = 382 — so
# the whole delta is rows and no module left the pattern. A pin updated from the
# failure message alone would have taken 382 without that second measurement.
#
# 382 → 383 is the row that pins the arm's *laziness*, added when the acceptance
# suite found the defect the unit suite could not see:
# `FilterExpression(...).resolve({})` — a `dict` context — raised
# `AttributeError: 'dict' object has no attribute 'autoescape'` for every
# built-in, because the arm read `context.autoescape` eagerly. One row, and the
# delta is the row. See the amendment in the M11 plan for why nothing smaller
# than the 1547 could have caught it.
# 383 → 389 is M11 Task 3, **six** rows. This note has been wrong about that
# number twice inside one task, which is the reason it is worth reading: it was
# first written as 387 against a four-row list — `DefaultTagsInterpositionTests`'
# module resolution, the node classes compared against the pinned file, the
# rendered `{% for %}` holding our node, and the tag-rebinding escape — then 388
# when `test_tag_inventory`'s "the interposed modules register the pinned
# registries" was added while fixing that module's guard, and now 389 for
# `test_interposition`'s "the builtin filter map tracks the registry it was built
# from", which the reviewer's source-keyed-cache finding added. Each time
# discovery had moved and the recorded number had not, and each time the
# correction came from the same place: `git diff tests/ | grep -c "^+    def
# test_"` is 6 and 383 + 6 = 389, so the whole delta is rows and no module left
# the pattern. The lesson is the one this pin exists for: a count taken from the
# failure message *and not from the diff* is a count taken at one moment.
#
# `tests/test_variable_parity.py` also changed in that commit and that is not
# part of the delta: its `Variable` moved from `from django.template.base import
# Variable` to `load_pinned("base.py").Variable`, a swap of one binding for
# another rather than a row. It moved because a row above left the shim
# installed where `tests.test_shim`'s `tearDown` used to leave it out, and the
# module's guard had been green on that accident — `getsourcefile` follows a
# class through `sys.modules[cls.__module__]`, so an interposed name answers for
# the class defined in it. Same class of defect as the row above it, found the
# same way: by an unrelated commit moving the ground under it.
#
# 389 → 390 is the M11 review's teardown finding, **one** row:
# `test_shim.py::test_tearDown_restores_the_state_it_found`. `git diff tests/ |
# grep -c "^+    def test_"` is 1 and 389 + 1 = 390. What that row is *about* is
# the reason it is here rather than a note somewhere: `test_shim`'s `tearDown`
# uninstalled the shim unconditionally, so every module discovered after it ran
# against stock Django — three of them today, none red, which is the state this
# pin's own comment calls the worse one. The fix is the two-method contract in
# that class; the row is what makes it checkable, and it fails on the old code
# and on an unconditional install at teardown alike.
#
# 390 → 392 is M12 Task 0b, `TagPredicateCoverageTests`, **two** rows: the
# containment itself and the control that shows it can fail. The invariant they
# hold (`_rust_owned` ⊆ `django_own_tags()`) was asserted in a docstring and
# nowhere else, and the docstring's version of it was false in both halves — so
# this is the delta that turns a sentence into a check.
#
# 392 → 397 is M12 Task 1, `test_shim.py::NamespaceTests`, **five** rows, and the
# delta is again measured rather than read off the failure: `git diff tests/`
# shows `+5` rows in one file and nothing else, and 392 + 5 = 397. Those five are
# the new namespace's mapping, the extension the name cannot decide, the meter's
# second scope, a library we do not provide still resolving as Django's, and the
# namespace package itself being declined. The fourth is Review Focus 3's row and
# the fifth guards a refusal that is deliberate rather than a side effect of no
# `__init__.py` existing.
#
# 397 → 403 is M12 Task 2, `test_interposition.py::LibraryTagPredicateTests`,
# **six** rows. `git diff tests/` since the previous commit is `+6` rows in that
# one file and no removals, so 397 + 6 = 403. The six are the coverage of all 18
# library tags, the measured disjointness the merge order rests on, the sentinel
# that stages a library shadowing a builtin name, the cache's tracking of the
# registries it was built from, the caller's `builtins` versus the parse's own
# table, and the staged reading of `has_tag`'s clause for a library tag. Five of
# the six redden when `django_own_tags()` is reverted to the two-module merge —
# the second does not, because it measures the registries rather than the table.
#
# 403 → 404 is the M12 review's `_our_modules_in` finding, **one** row in
# `test_shim.py::NamespaceTests`: a namespace that maps to `_impl` itself walks
# the other namespace's files too. The row stages the tree, so it fails on the
# unfixed function today rather than only after Task 5 creates
# `_impl/templatetags/`. `git diff tests/` is `+1` row, one file, no removals.
#
# 404 → 408 is M12 Task 4, **four** rows across two files: two in
# `test_interposition.py::TzInterpositionTests` (the module resolves to `_impl`,
# and each of the three tags compiles to our node class — one row, three
# subtests) and two in `test_materialise.py` (the three tz node classes are
# registered and are ours; the `BlockTranslateNode` refusal is live until Task
# 6). `git diff tests/` since the previous commit is `+4` rows in two files and
# no removals, so 404 + 4 = 408 — measured by discovery, and the failure text
# before the pin moved read `discovery found 408 tests, the pin says 404`.
#
# 408 → 410 is the post-Task-4 review batch, **two** rows in
# `test_interposition.py::FabricatedNameTests`: the registry check that makes the
# `unregistered_` prefix trustworthy, and the control that shows it is neither
# vacuous over an empty registry nor blind to a name. The batch's other two
# pieces move no count — the `m12_not_a_tag` → `unregistered_tag` rename is a
# rename, and the Rust-side rows are `cargo test`'s, not this suite's. `git diff
# tests/` since `f89a9d4` is `+2` rows in one file, no removals.
#
# 410 → 414 is M12 Task 5, **four** rows in two files: three in
# `test_interposition.py::L10nInterpositionTests` (the module resolves to `_impl`;
# the tag and its three accepted forms compile to our `LocalizeNode`; and the two
# filters resolve to our module — one row, the `localize`/`unlocalize` subtests)
# and one in `test_materialise.py` (`LocalizeNode` is registered and is ours).
# `git diff tests/` since `b83ec01` is `+4` rows in two files, no removals, so
# 410 + 4 = 414 — measured by discovery before the pin moved, and the failure text
# before the pin moved read `discovery found 414 tests, the pin says 410`.
#
# 414 → 426 is M12 Task 6a, **twelve** rows in three files:
# `test_interposition.py::I18nInterpositionTests` contributes **eleven** (the
# module resolves to `_impl`; the eight names compile to our seven classes; the
# `as var` and `noop` render branches; the six refusal messages; `{% language %}`'s
# argument and body; and four rows for the argument scan's branches — the
# duplicate check, the `context` value set, the unvalidated `as` name, and the
# trailing-option message — plus the language argument-before-body order).
# `test_materialise.py` contributes **one** (`test_the_i18n_node_classes_are_registered_and_are_ours`,
# seven subtests over the seven names plus the `BlockTranslateNode` absence).
# `test_base_parity.py` and `test_interposition.py::LibraryTagPredicateTests` move
# no count: the first is a docstring fix, the second re-points its staged name
# from `trans` to `blocktranslate` because `trans` became Rust's in this commit —
# a modification, not an addition. `git diff tests/` since `b83ec01` is `+12` rows
# in three files, no removals (measured by discovery, and the failure text before
# the pin moved read `discovery found 426 tests, the pin says 414`).
#
# 426 → 432 is M12 Task 6b, a **net six** in three files — and it is a net
# because one row *left*. `test_interposition.py::I18nInterpositionTests`
# contributes **six** (the token stream and the absent nodelist;
# `{% blocktranslate %}` inside `{% block %}` with `{% plural %}`'s
# unregisteredness; the four message shapes plus the two end-tag ones; trap 2's
# own-token-before-exhaustion case; the nine option-scan messages with the
# malformed-`context` filter; and the render rows with the count-type refusal).
# `test_parser_parity.py` contributes **one**
# (`test_a_block_translate_body_is_a_list_of_real_tokens`) — the class the new
# corpus rows carry. `test_materialise.py` **loses one**:
# `test_the_refusal_is_live_for_block_translate_node_until_task_6` is deleted in
# the commit that registers the entry, which its own docstring said was its
# whole purpose; the registration row beside it gains `BlockTranslateNode`
# as a modification, not a row. `LibraryTagPredicateTests`' staged-name row is
# likewise repointed, not added. `git diff tests/` since `ec47f4f` is `+8/-1`
# rows in three files, so 426 + 8 - 1 = 432 — measured by discovery (and the
# failure text before the pin moved read
# `discovery found 432 tests, the pin says 426`).
#
# 432 → 437 is M12 Task 7, **five** rows in one file,
# `test_interposition.py::FilterRoutingTests`: the counter that the nine library
# filters reach Rust; the coverage row (nine names, same handle under each); the
# disjoint-and-injective row (66 names over 66 handles, no name in two of the six
# tables); the merge-order sentinel (a library's `lower` cannot take the name from
# `defaultfilters`); and the cache-tracks-a-library-registry row. The corpus rows
# and the registry constants that moved with this task are **modifications, not
# methods** — `tests/filter_cases.py` grows rows inside two lists and
# `tests/test_filter_parity.py` and `tests/test_filter_inventory.py` re-point
# existing assertions — so none of them moves this count. Measured by discovery:
# `git diff tests/` since `4e0cee6` adds exactly five `def test_` bodies.
#
# 437 → 440 is M12 Task 8's compared-path agreement gate, **three** rows in one
# file, `test_compared_path_instrument.py::ComparedPathAgreementTests`: the
# per-module cross-check of the pin against a profiler that wraps nothing, and
# the two rows that pin what the comparison's one tolerance does at each of its
# boundaries (a group of members sharing a span, and a frame no span covers).
# `tests/parser_corpus.py` gains `parity_sources()` (so that this gate and the
# pin's own re-measurement row measure the *same* corpus rather than two
# rebuildings of it), `tests/test_interposition.py`'s pin row now calls it, and
# `tests/compared_path.py` gains the observer — none of the three is a
# `def test_`, and none of them moves this count. Measured by discovery:
# `git diff tests/` since `a2bd289` adds exactly three `def test_` bodies and
# removes none.
PINNED = 440


def collected() -> unittest.TestSuite:
    """The suite ``check.sh`` runs, discovered the way ``check.sh`` runs it."""
    return unittest.TestLoader().discover(
        start_dir=str(PROJECT_ROOT / "tests"),
        pattern="test_*.py",
        top_level_dir=str(PROJECT_ROOT),
    )


class CollectionIsPinned(unittest.TestCase):
    def test_the_collected_count_is_the_pinned_one(self):
        found = collected().countTestCases()
        self.assertEqual(
            found,
            PINNED,
            f"discovery found {found} tests, the pin says {PINNED}. If the delta "
            "is smaller than the rows you added, a module's name is not "
            "matching `-p 'test_*.py'` and its rows are running for nobody — "
            "which is exactly what `check.sh --fast` cannot tell you, because "
            "it prints `all checks passed` either way.",
        )

    def test_no_module_the_pattern_misses_holds_test_rows(self):
        """The count above is the detector; this is the diagnosis.

        A count tells you something is wrong and not what. This walks the same
        directory for modules the pattern skips and fails if one of them defines
        a ``TestCase`` — turning "the number is off by seven" into the name of
        the file whose rows nobody runs.
        """
        import ast

        offenders = []
        for path in sorted((PROJECT_ROOT / "tests").glob("*.py")):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Attribute) and base.attr == "TestCase"
                    for base in node.bases
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
        self.assertEqual(
            offenders,
            [],
            "these modules define test cases but do not match `-p 'test_*.py'`, "
            f"so nothing collects them: {offenders}",
        )

    def test_every_test_directory_is_a_package(self):
        """The shape the count *cannot* see, and the reason this row exists.

        ``discover`` recurses only into directories that are importable packages.
        A ``tests/subdir/test_thing.py`` with no ``tests/subdir/__init__.py`` is
        therefore collected by nothing — and, unlike the top-level case above,
        **the pinned count does not move when it happens**, so the detector is
        blind to it by construction. `tests/differential/` has that shape today
        and is data-only, which is fine; the row fires the moment one of its
        files grows a ``TestCase``.
        """
        import ast

        offenders = []
        for subdir in sorted(p for p in (PROJECT_ROOT / "tests").iterdir() if p.is_dir()):
            if subdir.name == "__pycache__":
                continue
            holds_rows = any(
                path.name.startswith("test_")
                or "TestCase" in path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(subdir.glob("*.py"))
            )
            if holds_rows and not (subdir / "__init__.py").is_file():
                offenders.append(subdir.name)
        self.assertEqual(
            offenders,
            [],
            "these directories hold test code but are not packages, so "
            f"`discover` cannot recurse into them: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
