"""Does each new clause of `check_transcriptions.py` actually refuse something?

Four clauses were added to the guard in M9's Task 1 follow-up, and a guard whose
teeth have not been measured is a guard that might be decorative — the failure
this project has already paid for twice (`.review/m9-recon.md` findings 1a and
3). So each one is run against a tree that should fail it, from a *copy*, so the
working tree is never mutated:

1. the marker names the wrong release;
2. the marker names the wrong upstream path;
3. a region covers code the parity harness executes (`COMPARED_PATH`);
4. a region with executable lines declares no snippet;
5. a region's snippet does not account for every executable line in it;
6. a transcribed module has no `COMPARED_PATH` entry, so nothing checks whether
   its declared regions sit on code a parity harness executes;
7. a region whose ours-span and upstream-span differ in length declares no note,
   so the line diff is the only thing describing a shape change it cannot
   describe (added in Task 7, and the clause Task 5's one-line handler needed).

Run it:

    .venv/bin/python -I .review/m9-measurements/m9_guard_teeth.py
"""

import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import scripts.check_transcriptions as guard  # noqa: E402

BASE = "base.py"
REAL_BASE = guard.IMPL / BASE
UPSTREAM = guard.DECLARED[BASE][0]
REAL_REGIONS = guard.DECLARED[BASE][1]

# `Parser.parse`'s span, taken from the pin rather than written down. The first
# version of this script hardcoded 531-593, and the very next edit to
# `base.py` — Task 2's delegation, which added five lines above it — moved the
# method and three scenarios silently stopped testing anything. A measurement
# script that has to be updated when the thing it measures moves is a script
# that gets updated without being read.
PARSE_FIRST, PARSE_LAST, _ = next(
    entry for entry in guard.COMPARED_PATH[BASE] if entry[2] == "Parser.parse"
)
# The first three *executable* lines inside it, which is what the snippet
# scenarios need: `Parser.parse` opens with a `def` line followed by a docstring,
# so a fixed offset from `PARSE_FIRST` would land on prose and the scenarios
# would test the docstring exclusion instead of the snippet rule.
_parse_executable = sorted(
    n
    for n in guard.executable_lines(REAL_BASE.read_text())
    if PARSE_FIRST <= n <= PARSE_LAST
)
PARSE_HEAD = (_parse_executable[0], _parse_executable[2])

# The marker insertion, exactly as the declaration carries it. Scenarios below
# need a *second* region so the file's real differences stay declared, and
# clause (5) now requires this one to carry a note — using the real note keeps
# each scenario's output to the one failure it is about. Scenario 7 removes the
# note on purpose.
MARKER_REGION = ((2, 3), None, "# TRANSCRIBED: django/template/base.py (Django 6.0.9)")

results = []


def scenario(label, *, source=None, regions=None, declared=None, compared=None,
             expected):
    """Run ``check()`` against a copy of ``base.py`` and record what it said."""
    with tempfile.TemporaryDirectory() as tmp:
        impl = pathlib.Path(tmp)
        (impl / BASE).write_text(source if source is not None else REAL_BASE.read_text())

        saved_impl, saved_declared, saved_compared = (
            guard.IMPL,
            guard.DECLARED,
            guard.COMPARED_PATH,
        )
        guard.IMPL = impl
        guard.DECLARED = declared if declared is not None else {
            BASE: (UPSTREAM, regions if regions is not None else REAL_REGIONS)
        }
        # Narrowed with `DECLARED`, or the key-set equality check reports every
        # real entry as stale and drowns the scenario's own output.
        guard.COMPARED_PATH = (
            saved_compared if compared is None else compared
        )
        try:
            failures = guard.check()
        finally:
            guard.IMPL, guard.DECLARED, guard.COMPARED_PATH = (
                saved_impl,
                saved_declared,
                saved_compared,
            )

    hit = [f for f in failures if expected in f]
    results.append((label, bool(hit), failures))
    print(f"{'RED ' if hit else 'GREEN'}  {label}")
    for failure in failures:
        print(f"        {failure}")
    if not hit:
        print(f"        EXPECTED a failure containing {expected!r}")


print("=== 0. baseline: the real file and the real declaration are clean ===")
print(f"{'GREEN' if not guard.check() else 'RED  '}  baseline")
for failure in guard.check():
    print(f"        {failure}")

source = REAL_BASE.read_text()

print()
print("=== 1/2. the marker must name this release and this upstream path ===")
scenario(
    "marker names Django 6.0.8",
    source=source.replace("(Django 6.0.9)", "(Django 6.0.8)", 1),
    expected="the transcription is of a different",
)
scenario(
    "marker names the wrong upstream file",
    source=source.replace(
        "# TRANSCRIBED: django/template/base.py",
        "# TRANSCRIBED: django/template/smartif.py",
        1,
    ),
    expected="marker names upstream",
)
scenario(
    "no marker at all",
    source=source.replace("# TRANSCRIBED: django/template/base.py (Django 6.0.9)\n", "", 1),
    expected="no `# TRANSCRIBED:",
)

print()
print("=== 3. a region may not cover code the parity harness executes ===")
# `Parser.parse` is in COMPARED_PATH; its first three statements are executable
# and the region covers them.
scenario(
    "a region inside Parser.parse",
    regions=[MARKER_REGION, (PARSE_HEAD, None, None)],
    expected="which the parity harness executes",
)

print()
print("=== 4/5. snippets on regions with executable lines ===")
scenario(
    "executable region with no snippet",
    regions=[MARKER_REGION, (PARSE_HEAD, None, None)],
    expected="declares no snippet",
)
_first_statement = REAL_BASE.read_text().splitlines()[_parse_executable[0] - 1].strip()
scenario(
    "snippet naming only part of the region",
    regions=[
        MARKER_REGION,
        (PARSE_HEAD, None, _first_statement),  # the rest left unnamed
    ],
    expected="snippet does not name line(s)",
)

print()
print("=== 7. a region of a different shape must say what moved ===")
scenario(
    "pure insertion with no note",
    regions=[((2, 3), None, None)],
    expected="declare a note naming what moved",
)

print()
print("=== 6. the pin must cover exactly the declared transcriptions ===")
scenario(
    "a transcribed module with no COMPARED_PATH entry",
    compared={},
    expected="COMPARED_PATH is missing an entry",
)

print()
print("=== summary ===")
bad = [label for label, hit, _ in results if not hit]
print(f"{len(results) - len(bad)} of {len(results)} scenario(s) refused as expected")
if bad:
    for label in bad:
        print(f"  NOT REFUSED: {label}")
    sys.exit(1)
