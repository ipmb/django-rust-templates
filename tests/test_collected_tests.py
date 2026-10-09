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
PINNED = 383


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
