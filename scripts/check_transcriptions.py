"""Every ``_impl`` transcription differs from upstream only where it declares.

A ``# TRANSCRIBED:`` file is a claim about the pinned tree, and the claim is
only falsifiable if someone reads the diff. ``.review/m9-recon.md`` finding 3
measured the price of not reading it: ``_impl/base.py`` as a verbatim copy, and
``_impl/base.py`` transcribing *wrongly*, both keep all 1547 tests green. The
suite is not an instrument here; the diff is.

So each transcription declares where it differs, and this script asserts:

1. every differing line falls inside a declared region, and no declared region
   is **stale** (covering nothing). Both directions are failures — a line
   outside every region is a change nobody wrote down, and a region covering
   nothing is a claim nothing re-measures any more, the same failure mode the
   anchor manifest already guards against;
2. a region that contains **executable** lines declares a snippet naming them,
   and the snippet accounts for every executable line in the region. A line
   range over an import is a declaration nobody can check by reading; the
   snippet is the part a reviewer actually reads, and widening the range over a
   new statement without naming it is a failure;
3. the ``# TRANSCRIBED:`` marker names this module's upstream path and the
   version of the checkout it was taken from. Without this the marker is
   decoration: a transcription copied from a *different* Django release, or
   pointed at the wrong upstream file, would pass every other check here;
4. no declared region intersects the code a parity harness **executes**
   (``COMPARED_PATH``). This is the clause that stops a parity test from
   becoming Rust against Rust, and it is measured rather than inferred — see
   the note on ``COMPARED_PATH``.

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

import ast
import difflib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "_impl"

# `# TRANSCRIBED: django/template/base.py (Django 6.0.9)`, on its own line, as
# the first line inside the module docstring.
MARKER = re.compile(
    r"^# TRANSCRIBED: (?P<path>\S+) \(Django (?P<version>[0-9][^)]*)\)$"
)

# How far into the file the marker may be. The convention is the first line
# inside the docstring; `_impl/__init__.py` puts a blank line first, so the test
# is "inside the docstring", which four lines covers for both forms.
MARKER_WINDOW = 4

# The functions a parity harness executes, with the lines they occupy in the
# transcription — measured by `.review/m9-measurements/m9_measure_parse_path.py`
# over both parity corpora (the hand-written one and the generator; the
# generator reaches `Parser.error` and `Parser.invalid_block_tag`, which the
# hand-written corpus does not, so both passes are needed).
#
# The table is pinned rather than re-measured on every run because measuring
# means installing the shim and parsing ~566 templates, which is the parity
# suite's own cost and not this static guard's job. It cannot go stale quietly:
# `tests/test_interposition.py::test_the_compared_path_is_the_measured_one`
# re-runs the instrument and fails if the two disagree. A pinned table with a
# live re-measurement behind it is the same shape as the module meter.
#
# 28 functions, spans 116-1165 — nearly the whole file, which is the finding.
# A guard that checked only the named target's own lines would miss a region
# declared in any of the other 27.
COMPARED_PATH = {
    "base.py": [
        (116, 119, "Origin.__init__"),
        (361, 383, "Token.__init__"),
        (392, 405, "Token.split_contents"),
        (409, 411, "Lexer.__init__"),
        (420, 432, "Lexer.tokenize"),
        (434, 466, "Lexer.create_token"),
        (504, 526, "Parser.__init__"),
        (531, 593, "Parser.parse"),
        (595, 600, "Parser.skip_past"),
        (602, 619, "Parser.extend_nodelist"),
        (621, 632, "Parser.error"),
        (634, 650, "Parser.invalid_block_tag"),
        (661, 662, "Parser.next_token"),
        (664, 665, "Parser.prepend_token"),
        (667, 668, "Parser.delete_first_token"),
        (670, 672, "Parser.add_library"),
        (674, 678, "Parser.compile_filter"),
        (680, 684, "Parser.find_filter"),
        (741, 785, "FilterExpression.__init__"),
        (873, 924, "Variable.__init__"),
        (926, 942, "Variable.resolve"),
        (1072, 1084, "Node.get_nodes_by_type"),
        (1092, 1093, "NodeList.render"),
        (1095, 1100, "NodeList.get_nodes_by_type"),
        (1106, 1107, "TextNode.__init__"),
        (1115, 1122, "TextNode.render_annotated"),
        (1144, 1145, "VariableNode.__init__"),
        (1165, 1209, "token_kwargs"),
    ],
}

# module -> (upstream path relative to the checkout, [(ours, theirs, snippet)])
#
# A span is `(first, last)`, inclusive and 1-based; a one-line span is
# `(n, n)`, and `None` says the region has **no lines at all** on that side —
# a pure insertion (`theirs_span=None`) or a pure deletion (`ours_span=None`).
# Declaring the upstream side too is what stops the guard from accepting a
# wholesale deletion: a function removed from ours shows up as upstream lines
# no region accounts for.
#
# The snippet is required exactly when the region contains executable lines, and
# must contain each of them verbatim. Every region carries a comment naming the
# edit, so the declaration reads as a list of decisions rather than a list of
# numbers.
DECLARED = {
    "__init__.py": (
        "django/template/__init__.py",
        [
            # The `# TRANSCRIBED:` marker, its blank separator, and the
            # rewritten docstring — see the module docstring's reason and design
            # spec §6. The upstream span reaches line 39 because the matcher
            # pairs our closing prose against the tail of upstream's docstring.
            # No snippet: the docstring is a string expression, so the region
            # holds no executable line.
            ((1, 23), (1, 39), None),
            # Upstream's `# NOQA isort:skip` markers are **stripped**, not added.
            # Django runs isort on this file and needs them; our transcription
            # does not carry Django's lint directives (design spec §6: the file
            # is upstream's "with the docstring and the `# NOQA isort:skip`
            # comments stripped, and nothing else changed"). The first version
            # of this comment said the opposite direction, which is the failure
            # mode a declaration written from memory has: the conclusion was
            # right and the reason was wrong.
            # The snippet is the region's executable lines verbatim, all three
            # of them: the rule is that a snippet must *account for* every
            # executable line in its region, so that widening a range over a new
            # statement without naming it fails. The first draft of this
            # declaration named only the first import and the guard refused it,
            # which is the rule earning its place on its first run.
            (
                (39, 41),
                (55, 57),
                "from .base import VariableDoesNotExist\n"
                "from .context import Context, ContextPopException, RequestContext\n"
                "from .exceptions import TemplateDoesNotExist, TemplateSyntaxError",
            ),
            ((44, 44), (60, 60), "from .base import ("),
            ((54, 54), (70, 70), "from .library import Library"),
            # The second, unrelated deviation on these lines: upstream's comment
            # reads "the registrations of signals", ours "the registration of
            # signals" (one word restored to the singular in 6.0's source). The
            # import line below is what makes the region executable.
            ((56, 57), (72, 73), "from . import autoreload"),
        ],
    ),
    "base.py": (
        "django/template/base.py",
        [
            # The `# TRANSCRIBED:` marker and its blank separator, ours only —
            # a pure insertion, hence `None` on the upstream side, and no
            # executable line, hence no snippet. Tasks 2 and 5 add the
            # `compile_nodelist` and `_resolve_lookup` bodies here; at this
            # commit the transcription delegates nothing and says so.
            ((2, 3), None, None),
        ],
    ),
}


def pinned_version() -> str:
    """The version of the checkout in ``.django``, read from its own source.

    Read with ``ast`` rather than by importing: this script deliberately
    imports no Django, because whether ``django`` resolves to the checkout or to
    a shimmed module depends on the environment it happens to run in, and a
    guard whose reading of the pin depends on that is not a guard.
    """
    source = (CHECKOUT / "django" / "__init__.py").read_text()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        if any(getattr(t, "id", None) == "VERSION" for t in node.targets):
            major, minor, micro = node.value.elts[:3]
            return f"{major.value}.{minor.value}.{micro.value}"
    raise SystemExit("no VERSION in the pinned django/__init__.py")


def executable_lines(source: str) -> set:
    """1-based lines of ``source`` that hold an executable statement.

    A bare string expression — the module docstring, or a stray string — is not
    executable: it is a no-op in CPython and it is where a transcription's own
    prose lives, so counting it would force a snippet on every rewritten
    docstring. Everything else with a ``lineno`` counts, which includes the
    ``import`` lines a stripped-comment edit leaves behind.
    """
    out = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                continue
        out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


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
    """The ``(ours, theirs)`` spans ``name`` declares, snippets dropped."""
    _upstream, regions = DECLARED[name]
    return [(ours, theirs) for ours, theirs, _snippet in regions]


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


def overlaps(span, first: int, last: int) -> bool:
    """Do ``(first, last)`` and declared ``span`` share a line at all?"""
    if span is None:
        return False
    return not (last < span[0] or first > span[1])


def _check_marker(name, upstream, ours) -> list:
    """Failures about the ``# TRANSCRIBED:`` line of ``name``."""
    failures = []
    found = None
    for index, line in enumerate(ours[:MARKER_WINDOW]):
        match = MARKER.match(line)
        if match:
            found = (index + 1, match)
            break
    if found is None:
        return [
            f"{name}: no `# TRANSCRIBED: <path> (Django <version>)` in the "
            f"first {MARKER_WINDOW} lines, so nothing ties this file to a "
            "release of the checkout"
        ]
    line_number, match = found
    if match["path"] != upstream:
        failures.append(
            f"{name}:{line_number}: marker names upstream {match['path']}, "
            f"but this file is declared against {upstream}"
        )
    pinned = pinned_version()
    if match["version"] != pinned:
        failures.append(
            f"{name}:{line_number}: marker names Django {match['version']}, "
            f"but .django is {pinned} — the transcription is of a different "
            "release"
        )
    return failures


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
        text = ours_path.read_text()
        ours = text.splitlines()
        theirs = theirs_path.read_text().splitlines()
        actual = differing_lines(ours, theirs)
        executable = executable_lines(text)

        failures.extend(_check_marker(name, upstream, ours))

        def one_region_holds(declared, found) -> bool:
            return _fits(declared[0], found[0]) and _fits(declared[1], found[1])

        for found in actual:
            if any(one_region_holds(region[:2], found) for region in regions):
                continue
            failures.append(
                f"{name}: undeclared difference  ours:{_fmt(found[0])}  "
                f"upstream:{_fmt(found[1])}"
            )
        for ours_span, theirs_span, snippet in regions:
            region = (ours_span, theirs_span)
            if not any(one_region_holds(region, found) for found in actual):
                failures.append(
                    f"{name}: stale region  ours:{_fmt(ours_span)}  "
                    f"upstream:{_fmt(theirs_span)}"
                )

            # (4) The compared path must be outside the declared differences.
            # Checked before the snippet clauses and never skipped by them: this
            # is the failure that makes a parity comparison meaningless, and its
            # first draft sat after a `continue` in the snippet logic, so a
            # region with executable lines and no snippet reported only the
            # lesser problem. Measured by
            # `.review/m9-measurements/m9_guard_teeth.py` scenario 3.
            for first, last, label in COMPARED_PATH.get(name, []):
                if overlaps(ours_span, first, last):
                    failures.append(
                        f"{name}: region ours:{_fmt(ours_span)} covers "
                        f"{label} ({first}-{last}), which the parity harness "
                        "executes, so the comparison would be Rust against Rust"
                    )

            # (2) A region holding executable lines must name them.
            if ours_span is None:
                continue
            inside = sorted(set(range(ours_span[0], ours_span[1] + 1)) & executable)
            if not inside:
                if snippet is not None:
                    failures.append(
                        f"{name}: region ours:{_fmt(ours_span)} has no "
                        "executable line, so its snippet declares nothing — "
                        "drop it or widen the region to what it names"
                    )
                continue
            if snippet is None:
                failures.append(
                    f"{name}: region ours:{_fmt(ours_span)} contains "
                    f"{len(inside)} executable line(s) but declares no snippet, "
                    "so the difference it permits is a line range nobody can "
                    f"read: {[ours[n - 1] for n in inside]}"
                )
                continue
            unnamed = [n for n in inside if ours[n - 1] not in snippet]
            if unnamed:
                failures.append(
                    f"{name}: region ours:{_fmt(ours_span)} snippet does not "
                    f"name line(s) {unnamed}: {[ours[n - 1] for n in unnamed]}"
                )
    return failures


def _fmt(span) -> str:
    if span is None:
        return "-"
    first, last = span
    return str(first) if first == last else f"{first}-{last}"


def main() -> int:
    failures = check()
    pinned = pinned_version()
    print(f"pinned checkout: Django {pinned}")
    for name in sorted(DECLARED):
        path = IMPL / name
        if not path.is_file():
            print(f"{name}: missing")
            continue
        ours = path.read_text().splitlines()
        theirs = (CHECKOUT / DECLARED[name][0]).read_text().splitlines()
        regions = declared_regions(name)
        actual = differing_lines(ours, theirs)
        compared = len(COMPARED_PATH.get(name, []))
        print(
            f"{name}: {len(regions)} declared region(s), "
            f"{len(actual)} differing region(s) in the file, "
            f"{compared} executed function(s) guarded against"
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
