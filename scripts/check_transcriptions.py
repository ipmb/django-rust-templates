"""Every ``_impl`` transcription differs from upstream only where it declares.

A ``# TRANSCRIBED:`` file is a claim about the pinned tree, and the claim is
only falsifiable if someone reads the diff. ``.review/m9-recon.md`` finding 3
measured the price of not reading it: ``_impl/base.py`` as a verbatim copy, and
``_impl/base.py`` transcribing *wrongly*, both keep all 1547 tests green. The
suite is not an instrument here; the diff is.

So each transcription declares where it differs, and this script asserts every
differing line falls inside a declared region, and that no declared region is
**stale** (covering nothing). Both directions are failures:

* a line outside every region is a change nobody wrote down — the drift this
  exists to catch, whoever made it;
* a region covering nothing is a claim nothing re-measures any more, the same
  failure mode the anchor manifest already guards against.

**Regions, not exact line pairs.** The first draft declared the exact
``(ours, upstream)`` pair for every differing line, and measuring it against the
one transcription that already exists settled the question: ``_impl/__init__.py``
differs on **45 lines**, because its docstring is deliberately rewritten (design
spec §6 — the export list is the behaviour, so the prose around it is ours). A
45-pair table keyed to a rewritten docstring re-churns on every prose edit —
``difflib`` re-aligns the whole block when one word changes — and a declaration
that must be regenerated whenever someone improves a sentence is a declaration
that gets regenerated without being read. Regions state the same claim ("nothing
outside these spans differs") in a form that survives the edits it is there to
permit.

Read-only: it parses and compares, and never writes either file. That matters
more than usual here, because a transcription may contain line separators that a
``splitlines()``/``join()`` round-trip would destroy (the rule this repo already
learned from ``tests/filter_cases.py``); this script only ever splits.
"""

import difflib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

# module -> (upstream path relative to the checkout, [(ours_span, theirs_span)])
#
# A span is `(first, last)`, inclusive and 1-based; a one-line span is
# `(n, n)`, and `None` says the region has **no lines at all** on that side —
# a pure insertion (`theirs_span=None`) or a pure deletion (`ours_span=None`).
# Declaring the upstream side too is what stops the guard from accepting a
# wholesale deletion: a function removed from ours shows up as upstream lines
# no region accounts for.
#
# Every region carries a comment naming the edit, so the declaration reads as a
# list of decisions rather than a list of numbers.
DECLARED = {
    "__init__.py": (
        "django/template/__init__.py",
        [
            # The `# TRANSCRIBED:` marker, its blank separator, and the
            # rewritten docstring — see the module docstring's reason and design
            # spec §6. The upstream span reaches line 39 because the matcher
            # pairs our closing prose against the tail of upstream's docstring.
            ((1, 23), (1, 39)),
            # `# NOQA isort:skip` on imports whose order is load-bearing, which
            # upstream does not need because it has no linter.
            ((39, 41), (55, 57)),
            ((44, 44), (60, 60)),
            ((54, 54), (70, 70)),
            ((56, 57), (72, 73)),
        ],
    ),
}


def differing_lines(ours: list, theirs: list) -> list:
    """Every differing ``(ours_line, theirs_line)`` region, 1-based.

    One entry per unmatched ``difflib`` opcode: a block of ours lines against a
    block of upstream ones, either block possibly empty. Regions rather than
    per-line pairs because that is the granularity ``DECLARED`` uses, so the
    comparison is between like and like.
    """
    out = []
    matcher = difflib.SequenceMatcher(a=ours, b=theirs, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        out.append(
            (
                (i1 + 1, i2) if i2 > i1 else None,
                (j1 + 1, j2) if j2 > j1 else None,
            )
        )
    return out


def declared_regions(name: str) -> list:
    """The regions, as ``(ours_span, theirs_span)``, that ``name`` declares."""
    _upstream, regions = DECLARED[name]
    return regions


def _within(span, first: int, last: int) -> bool:
    """Does an actual ``(first, last)`` span sit inside declared ``span``?"""
    if span is None:
        return first > last  # only an empty actual span fits an absent side
    return span[0] <= first and last <= span[1]


def _fits(declared, actual) -> bool:
    """Is an actual ``(first, last)`` span, or its absence, inside ``declared``?

    ``None`` on either side means *no lines*, and only an absent actual span
    fits it — which is the case the first draft of this script got wrong: a
    declaration of a pure insertion (``theirs_span=None``, the marker line that
    upstream does not have) could never match, so it reported itself stale
    forever. Both the coverage test and the staleness test need this same
    relation, just quantified in opposite directions.
    """
    first, last = actual if actual is not None else (0, -1)
    return _within(declared, first, last)


def check() -> list:
    """Every failure, as a string. Empty means the tree is consistent.

    Returned rather than printed so a test module can call it directly and make
    this guard part of its own evidence, instead of trusting that some other
    command ran it at some earlier time.
    """
    failures = []
    for name, (upstream, regions) in sorted(DECLARED.items()):
        ours_path = IMPL / name
        theirs_path = CHECKOUT / upstream
        if not ours_path.is_file():
            failures.append(f"{name}: declared but not present at {ours_path}")
            continue
        ours = ours_path.read_text().splitlines()
        theirs = theirs_path.read_text().splitlines()
        actual = differing_lines(ours, theirs)

        def one_region_holds(declared, found) -> bool:
            return _fits(declared[0], found[0]) and _fits(declared[1], found[1])

        for found in actual:
            if any(one_region_holds(region, found) for region in regions):
                continue
            failures.append(
                f"{name}: undeclared difference  ours:{_fmt(found[0])}  "
                f"upstream:{_fmt(found[1])}"
            )
        for region in regions:
            if not any(one_region_holds(region, found) for found in actual):
                failures.append(
                    f"{name}: stale region  ours:{_fmt(region[0])}  "
                    f"upstream:{_fmt(region[1])}"
                )
    return failures


def _fmt(span) -> str:
    if span is None:
        return "-"
    first, last = span
    return str(first) if first == last else f"{first}-{last}"


def main() -> int:
    failures = check()
    for name in sorted(DECLARED):
        path = IMPL / name
        if not path.is_file():
            print(f"{name}: missing")
            continue
        ours = path.read_text().splitlines()
        theirs = (CHECKOUT / DECLARED[name][0]).read_text().splitlines()
        regions = declared_regions(name)
        actual = differing_lines(ours, theirs)
        print(
            f"{name}: {len(regions)} declared region(s), "
            f"{len(actual)} differing region(s) in the file"
        )
    for failure in failures:
        print(f"  {failure}")
    if failures:
        print(
            f"\n{len(failures)} problem(s); the transcription is not what it claims to be"
        )
        return 1
    print("every transcription differs from upstream only where it declares")
    return 0


if __name__ == "__main__":
    sys.exit(main())
