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
5. a region's snippet does not account for every executable line in it.

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

results = []


def scenario(label, *, source=None, regions=None, declared=None, expected):
    """Run ``check()`` against a copy of ``base.py`` and record what it said."""
    with tempfile.TemporaryDirectory() as tmp:
        impl = pathlib.Path(tmp)
        (impl / BASE).write_text(source if source is not None else REAL_BASE.read_text())

        saved_impl, saved_declared = guard.IMPL, guard.DECLARED
        guard.IMPL = impl
        guard.DECLARED = declared if declared is not None else {
            BASE: (UPSTREAM, regions if regions is not None else REAL_REGIONS)
        }
        try:
            failures = guard.check()
        finally:
            guard.IMPL, guard.DECLARED = saved_impl, saved_declared

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
# `Parser.parse` is lines 531-593 in our transcription and is in COMPARED_PATH;
# lines 540-542 are its first three statements.
scenario(
    "a region inside Parser.parse",
    regions=[((2, 3), None, None), ((540, 542), (546, 548), None)],
    expected="which the parity harness executes",
)

print()
print("=== 4/5. snippets on regions with executable lines ===")
scenario(
    "executable region with no snippet",
    regions=[((2, 3), None, None), ((540, 542), (546, 548), None)],
    expected="declares no snippet",
)
scenario(
    "snippet naming only part of the region",
    regions=[
        ((2, 3), None, None),
        (
            (540, 542),
            (546, 548),
            "if parse_until is None:",  # lines 541 and 542 are unnamed
        ),
    ],
    expected="snippet does not name line(s)",
)

print()
print("=== summary ===")
bad = [label for label, hit, _ in results if not hit]
print(f"{len(results) - len(bad)} of {len(results)} scenario(s) refused as expected")
if bad:
    for label in bad:
        print(f"  NOT REFUSED: {label}")
    sys.exit(1)
