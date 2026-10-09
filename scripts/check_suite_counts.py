#!/usr/bin/env python3
"""A suite count in our prose must say where it came from.

Why this exists
---------------

Every milestone's acceptance is a *count* — ``Ran 1547 tests … OK (skipped=6)``
— and a count is the easiest kind of claim to make vacuously. Measured, on this
project: running ``.django/tests/runtests.py`` directly, with
``DJANGO_RUST_TEMPLATES`` unset, produced a run that was **byte-identical to
stock Django's** and reported ``OK``. The number was real, the run was real, and
it said nothing at all about our code, because ``sitecustomize`` installs the
shim only when that variable is ``1`` — and only ``scripts/run_django_suite.sh``
sets it and then *proves* the shim is installed before spending the run.

So a bare count is not a weak claim; it is an unfalsifiable one. This gate
requires every ``Ran N tests`` in our prose to sit within a few lines of
something that identifies the run: the command, the progress meter, or an
explicit note that the figure is stock Django's.

What it deliberately does not do
--------------------------------

It does not check that the number is *right* — that is the suite's job, and a
gate that re-ran it would be the suite. It checks that a reader can tell which
run produced it, which is the property that was missing.

Scope
-----

Every ``.md`` under ``docs/`` and ``.review/`` — the acceptance records, the
plans, the recon notes and the review dispatches, all of them. The scope is
whole-tree rather than a list of "the files that matter" because a list is how a
gate quietly stops covering the file where the next mistake is written; making
it whole-tree cost nine one-line edits when it was introduced, and every one of
them made its document better.

The one thing it insists on is that the scope is not empty: a tree with no count
lines at all fails, because "found nothing to check" and "checked everything and
it was fine" must not print the same way.

Usage:
    python scripts/check_suite_counts.py            # gate
    python scripts/check_suite_counts.py --list     # show every count and its
                                                    # provenance, passing or not
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COUNT_RE = re.compile(r"Ran\s+\d+\s+tests")

# How far away a provenance line may sit. Twelve lines, measured rather than
# picked: ``plans/2026-10-08-m3-filter-expression-and-variable.md`` puts its
# ``Run:`` line exactly twelve above the ``Ran 5 tests`` that line produced,
# because a fenced transcript interposes the fence, five result lines and a
# ``-----`` separator between them.
#
# It is deliberately *not* widened to make the gate pass. Swept 12 → 30 while
# this was written and the count falls from 7 bare rows to 2, but the extra rows
# are covered by markers that happen to sit nearby for unrelated reasons — a
# ``check.sh`` named in a neighbouring paragraph, a count of something else — and
# a gate that goes quiet because its window grew is the vacuity this file exists
# to catch.
WINDOW = 12

# Each pattern answers "which run produced this number?". They are deliberately
# about the *invocation*, not about our engine: `stock Django` is here because a
# count that is honestly labelled as Django's is exactly as useful as one that is
# honestly labelled as ours, and forcing it to be deleted would lose the
# baseline.
#
# ``-m unittest`` rather than ``python -m unittest``, because the invocations in
# this tree are spelled ``source scripts/env.sh && .venv/bin/python -I -m
# unittest <module>`` and an interpreter path with flags in it is the normal
# case, not the exception.
MARKERS = (
    # The command.
    r"run_django_suite\.sh",
    r"check\.sh",
    r"-m unittest",
    # The pre-flight and the progress meter it prints.
    r"module\(s\) still served by Django",
    r"engine installed",
    r"unshimmed\(\)",
    # ... or an explicit statement that this figure is not ours.
    r"stock Django",
)

MARKER_RES = tuple(re.compile(pattern) for pattern in MARKERS)


def count_lines(path: Path) -> list[int]:
    lines = path.read_text(encoding="utf-8").split("\n")
    return [i for i, line in enumerate(lines) if COUNT_RE.search(line)]


def provenance_for(lines: list[str], index: int) -> str | None:
    start = max(0, index - WINDOW)
    end = min(len(lines), index + WINDOW + 1)
    for line in lines[start:end]:
        for pattern, marker in zip(MARKER_RES, MARKERS):
            if pattern.search(line):
                return marker
    return None


def prose_files() -> list[Path]:
    files: list[Path] = []
    for directory in ("docs", ".review"):
        files.extend(sorted((ROOT / directory).rglob("*.md")))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every count line with the marker that licenses it",
    )
    args = parser.parse_args()

    total = 0
    failures: list[tuple[Path, int, str]] = []

    for path in prose_files():
        if ".django" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for index in count_lines(path):
            total += 1
            marker = provenance_for(lines, index)
            relative = path.relative_to(ROOT)
            if args.list:
                tag = marker if marker else "-- NO PROVENANCE --"
                print(f"{relative}:{index + 1}: [{tag}] {lines[index].strip()[:70]}")
            elif marker is None:
                failures.append((relative, index + 1, lines[index].strip()))

    if args.list:
        print(f"\n{total} count line(s)")
        return 0

    if total == 0:
        print(
            "check_suite_counts: found no `Ran N tests` line anywhere under "
            "docs/ or .review/.\n"
            "That is not a pass — it means this gate is looking at nothing. "
            "Check the scope before trusting it.",
            file=sys.stderr,
        )
        return 1

    if failures:
        print(
            f"`Ran N tests` with no provenance within {WINDOW} lines:\n",
            file=sys.stderr,
        )
        for relative, line_number, text in failures:
            print(f"  {relative}:{line_number}: {text[:90]}", file=sys.stderr)
        print(
            "\nName the run in the surrounding prose: `scripts/run_django_suite.sh` "
            "or `python -m unittest` for the invocation, "
            "`module(s) still served by Django` for the meter, `stock Django` when "
            "the figure is the baseline rather than ours.",
            file=sys.stderr,
        )
        return 1

    print(f"suite counts: {total} line(s), every one with its provenance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
