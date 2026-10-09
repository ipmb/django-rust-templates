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
    # No function in the package `__init__` is reachable from a parity harness,
    # so its entry is empty — and it is *present*, because the guard asserts the
    # key set equals `DECLARED`'s rather than checking the modules it happens to
    # list. An absent entry is invisible to a re-measurement that iterates the
    # pin: interposing a module without measuring it would leave its declared
    # regions unchecked with everything green.
    "__init__.py": [],
    "base.py": [
        (116, 119, "Origin.__init__"),
        (364, 386, "Token.__init__"),
        (395, 408, "Token.split_contents"),
        (412, 414, "Lexer.__init__"),
        (423, 435, "Lexer.tokenize"),
        (437, 469, "Lexer.create_token"),
        (507, 529, "Parser.__init__"),
        (534, 596, "Parser.parse"),
        (598, 603, "Parser.skip_past"),
        (605, 622, "Parser.extend_nodelist"),
        (624, 635, "Parser.error"),
        (637, 653, "Parser.invalid_block_tag"),
        (664, 665, "Parser.next_token"),
        (667, 668, "Parser.prepend_token"),
        (670, 671, "Parser.delete_first_token"),
        (673, 675, "Parser.add_library"),
        (677, 681, "Parser.compile_filter"),
        (683, 687, "Parser.find_filter"),
        (744, 788, "FilterExpression.__init__"),
        (876, 927, "Variable.__init__"),
        (929, 945, "Variable.resolve"),
        (1044, 1056, "Node.get_nodes_by_type"),
        (1064, 1065, "NodeList.render"),
        (1067, 1072, "NodeList.get_nodes_by_type"),
        (1078, 1079, "TextNode.__init__"),
        (1087, 1094, "TextNode.render_annotated"),
        (1116, 1117, "VariableNode.__init__"),
        (1137, 1181, "token_kwargs"),
    ],
    # 8 functions, spans 56-214 — the same instrument and the same pin
    # relation as `base.py`, re-measured by
    # `tests/test_interposition.py::test_the_compared_path_is_the_measured_one`.
    #
    # **The blind spot this table used to carry, and how it was closed.** The
    # first version of the instrument could only wrap names reachable from the
    # module namespace and its classes; `smartif` builds its operator classes
    # inside `infix()`/`prefix()` and binds them into the `OPERATORS` dict
    # (lines 98-113), so the methods that actually evaluate `and`, `or`, `not`
    # and every comparison were objects nothing wrapped. Measured then: four
    # sources — `{% if a and b %}`, `{% if not a %}`, `{% if a == 1 %}`,
    # `{% if a > 1 and b < 2 or c %}` — reached the same six functions and none
    # reached `Operator.led`/`Operator.nud`, so a declared region placed on them
    # would not have been caught by clause 4.
    #
    # `tests/compared_path.py::classes` now looks one level into module-level
    # containers, and the two entries at the top of this table are what that
    # found: the corpus does reach `infix`'s and `prefix`'s generated methods.
    # They are listed by the factory's qualified name because all the classes one
    # factory makes share it — and share the span, since the methods are defined
    # in the factory's body.
    #
    # `IfParser.error`, `IfParser.create_var`, `Literal.__init__`, the
    # `TokenBase` display/`__repr__` helpers and the operators' `eval` are
    # defined and simply not entered by these corpora, which is the ordinary
    # meaning of "compared path".
    "smartif.py": [
        (56, 59, "infix.<locals>.Operator.led"),
        (83, 86, "prefix.<locals>.Operator.nud"),
        (138, 139, "Literal.nud"),
        (161, 179, "IfParser.__init__"),
        (181, 187, "IfParser.translate_token"),
        (189, 195, "IfParser.next_token"),
        (197, 204, "IfParser.parse"),
        (206, 214, "IfParser.expression"),
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
            # No executable line, so the snippet is clause (5)'s note rather
            # than a list of statements: 23 of our lines stand against 39 of
            # upstream's, and what moved is the docstring.
            ((1, 23), (1, 39), "rewritten module docstring and the marker line"),
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
            # a pure insertion, hence `None` on the upstream side. No executable
            # line, so the snippet is clause (5)'s required note rather than a
            # list of statements: two of our lines against none of upstream's,
            # and what moved is the marker.
            ((2, 3), None, "# TRANSCRIBED: django/template/base.py (Django 6.0.9)"),
            # M9's parse seam: `Template.compile_nodelist` delegates to Rust.
            # Upstream's lexer construction and `Parser(...)` call are gone, and
            # in their place is a comment block plus the import that reaches the
            # shell. Two regions rather than one because `difflib` pairs our
            # prose against upstream's parser construction and our three
            # statements against upstream's three, and each pair is a separate
            # opcode; the second region is the delegation proper.
            #
            # This is the one declared region that *is* the Rust-vs-Rust hazard
            # the guard exists to refuse, and it is intended: M9's whole point is
            # that the suite's parsing runs in Rust. It is safe for the parity
            # rows only because they do not go through `compile_nodelist` —
            # `tests/test_parser_parity.py:django_parse` builds a `Parser`
            # directly — which is why `Template.compile_nodelist` is absent from
            # `COMPARED_PATH` and why that absence is a measurement rather than
            # an oversight.
            (
                (187, 201),
                (185, 196),
                "from django_rust_templates.render import parse_template",
            ),
            (
                (204, 206),
                (199, 201),
                "parsed = parse_template(self.source, self.engine, self.origin)\n"
                "self.extra_data = parsed.extra_data\n"
                "return parsed.nodelist",
            ),
            # M9's resolver seam: `Variable._resolve_lookup` delegates to Rust,
            # **including Django's outer `except Exception` handler and the
            # `logger.debug` inside it**. The docstring and the `resolve` that
            # calls this are still upstream's and still separate regions above;
            # what is gone is the loop over `self.lookups`, the three arms, and
            # the handler that decided between substituting and re-raising.
            #
            # Two regions rather than one for the same reason `compile_nodelist`
            # has two: `difflib` pairs our comment block against upstream's loop
            # (one opcode) and our `return` against upstream's `if
            # getattr(e, "silent_variable_failure", …)` tail (another). The
            # second region's upstream span is the handler's body, which is
            # where the alignment landed rather than where the code moved to —
            # stating that here because a reader comparing the two spans will
            # otherwise assume the pairing is meaningful.
            #
            # **This is the second declared region that is the Rust-vs-Rust
            # hazard the guard exists to refuse, and unlike `compile_nodelist`
            # it is reached by a parity harness.** `tests/differential/cases.py`,
            # `tests/test_render_parity.py::ResolverParityTests`,
            # `tests/test_resolve_invalid.py`, `tests/test_resolve_callarm.py`
            # and `tests/test_m8_side_effects.py` all compared `_native.resolve`
            # against `Variable._resolve_lookup` — which is exactly the "stock
            # vs rust" failure those harnesses were built to avoid, arriving
            # through the front door. Their Python side is now the *pinned
            # source*, executed from `.django`, by the same `_pinned` mechanism
            # `tests/test_base_parity.py` uses for `render_value_in_context`.
            # The guard above is what named all five rather than letting them
            # pass vacuously.
            #
            # **The declaration is smaller than what moved, and that is the
            # limit this region is at.** A declared region is a claim about a
            # *hunk*: it says which lines differ and why. What moved here is
            # `_resolve_lookup`'s entire exception semantics — the `logger.debug`,
            # the `silent_variable_failure` branch and the re-raise — into
            # `crates/template-py/src/lib.rs::outcome_to_py`, which the diff
            # cannot show, because a body replaced by a call is one hunk however
            # much body went with it. Prose is the only place that can say so,
            # and it is here. Whether the guard should be able to see a moved
            # *handler* rather than only a moved line is Task 7's question; the
            # reviewer's falsification of "a Python handler is impossible" (it is
            # not — a non-raising signature carries the bit on an ordinary return
            # value) is what makes the alternative live rather than closed.
            (
                (962, 1000),
                (957, 1021),
                "from django_rust_templates import _native",
            ),
            (
                (1002, 1002),
                (1023, 1028),
                'return _native.resolve(".".join(self.lookups), context)',
            ),
        ],
    ),
    "smartif.py": (
        "django/template/smartif.py",
        [
            # The `# TRANSCRIBED:` marker and its blank separator, ours only —
            # a pure insertion, hence `None` on the upstream side. No executable
            # line, so the snippet is clause (5)'s required note rather than a
            # list of statements: two of our lines against none of upstream's,
            # and what moved is the marker. **This is the entire file**:
            # measured with `difflib`, ours is 217 lines and upstream 215, the
            # diff is one `delete` opcode at ours 2-3 against upstream 2-1, and
            # the other 215 lines are equal in place.
            #
            # So `smartif` is a verbatim transcription plus a header, and it
            # differs from `base.py` in the way that matters for review: there
            # is no delegation here, so there is nothing to read past the
            # marker. It is interposed because `defaulttags` takes `IfParser`
            # from this module (`defaulttags.py:43`) and the *grammar* it
            # drives is Rust's (`_native.parse_filter_expression`, wired at M5)
            # — the Python file has to exist under Django's name for that
            # import to resolve to us rather than to the checkout, and once it
            # exists the claim it makes is that it is upstream's, which is what
            # this declaration states and what the diff above measures.
            ((2, 3), None, "# TRANSCRIBED: django/template/smartif.py (Django 6.0.9)"),
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

    Two exclusions, each measured rather than guessed:

    * a bare string expression — the module docstring, or a stray string — is
      not executable: it is a no-op in CPython and it is where a transcription's
      own prose lives, so counting it would force a snippet on every rewritten
      docstring;
    * a **compound** statement contributes only its header line, not its whole
      span. ``ast.walk`` yields the ``FunctionDef``/``If``/``Try`` node with a
      ``lineno`` and an ``end_lineno`` covering the body, and taking that span
      marks every comment and every docstring *inside* the function as
      executable — measured on this milestone's own `compile_nodelist`
      declaration, where a 15-line region reported all 15 lines as executable
      because one ``import`` at its end sits inside a `FunctionDef`. The rule
      wants the lines that *do* something, and a compound statement's header is
      the only line of it that does; its children are walked separately and
      counted on their own.
    """
    out = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                continue
        if hasattr(node, "body"):
            out.add(node.lineno)
        else:
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


def module_of(name: str) -> str:
    """The dotted import path of the interposed module for transcription ``name``.

    Derived from the declared upstream path rather than written a second time,
    so the two cannot disagree: ``django/template/base.py`` is
    ``django.template.base`` and ``django/template/__init__.py`` is the package
    ``django.template``, whose last segment the ``__init__`` does not name.

    That this is the *interposed* module rather than the checkout's is the point
    of the name — the shim loads our file under Django's name, so importing it
    gets ours, and a measurement of "what the parity harness executes" has to be
    a measurement of what the harness would actually run.
    """
    upstream = DECLARED[name][0]
    if upstream.endswith("/__init__.py"):
        upstream = upstream[: -len("/__init__.py")]
    elif upstream.endswith(".py"):
        upstream = upstream[: -len(".py")]
    return upstream.replace("/", ".")


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


def _marked(path: pathlib.Path) -> bool:
    """Does ``path`` carry a ``# TRANSCRIBED:`` marker in its opening lines?

    Deliberately the *inverse* question to ``_check_marker``: that one asks
    whether a declared file's marker is correct, this one asks whether an
    undeclared file has a marker at all. Only the first ``MARKER_WINDOW`` lines
    are read, the same window ``_check_marker`` uses, so the two cannot disagree
    about which files are transcriptions. A file that somehow fails to decode is
    not a transcription as far as this guard is concerned — it is not one of
    ours to read, and reporting it as marked would point the reader at the wrong
    problem.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= MARKER_WINDOW:
                    return False
                if MARKER.match(line.rstrip("\n")):
                    return True
    except (OSError, UnicodeDecodeError):
        return False
    return False


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
    # The pinned measured path must cover exactly the transcriptions that
    # declare regions. Equality, not containment: a module missing from the pin
    # has its declared regions checked by nothing, and a module in the pin that
    # is no longer transcribed is a stale measurement — both are failures, and
    # both are found in the commit that causes them rather than later.
    missing = sorted(set(DECLARED) - set(COMPARED_PATH))
    if missing:
        failures.append(
            f"COMPARED_PATH is missing an entry for {missing}, so nothing "
            "checks whether a declared region sits on code a parity harness "
            "executes — measure it and add the entry in the commit that "
            "transcribes the module"
        )
    stale = sorted(set(COMPARED_PATH) - set(DECLARED))
    if stale:
        failures.append(
            f"COMPARED_PATH has a stale entry for {stale}, which is no longer "
            "a declared transcription"
        )
    # Every file carrying the marker must be declared, and vice versa. Without
    # this the guard is defeated by the exact change it exists to review: the
    # loop below iterates `DECLARED`, so a transcription nobody declared is
    # never opened, never diffed and never has its regions checked — it is
    # invisible, and every other clause stays green. Measured, on the commit
    # that added the second transcription: `python -I
    # scripts/check_transcriptions.py` returned **exit 0** with
    # `_impl/smartif.py` on disk and absent from `DECLARED`, because the only
    # files it can name are the ones it was told about.
    #
    # Equality rather than containment for the same reason the `COMPARED_PATH`
    # clause is: a declared name with no file is a claim about nothing (caught
    # below as "declared but not present"), and a marked file with no
    # declaration is a transcription that has not been read.
    marked = sorted(path.name for path in IMPL.glob("*.py") if _marked(path))
    undeclared = sorted(set(marked) - set(DECLARED))
    if undeclared:
        failures.append(
            f"{undeclared} carr(ies) a `# TRANSCRIBED:` marker but is not in "
            "DECLARED, so nothing reads its diff — every clause of this guard "
            "skips a file it was not told about"
        )
    unmarked = sorted(set(DECLARED) - set(marked))
    # `_impl/__init__.py` is declared and *is* marked; a declared file that is
    # merely missing is reported by the per-module loop below, so only report
    # here the ones that exist and are somehow unmarked.
    unmarked = [name for name in unmarked if (IMPL / name).is_file()]
    if unmarked:
        failures.append(
            f"{unmarked} is declared as a transcription but carries no "
            "`# TRANSCRIBED:` marker"
        )
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

            # (5) A region whose two spans differ in length is a region the line
            # diff cannot describe, so prose is the only witness and the guard
            # requires it. **This is the clause M9's Task 5 needed and did not
            # have**: `_resolve_lookup`'s exception handler — thirty-odd lines of
            # upstream — was replaced by a one-line call, the declaration
            # recorded `ours 1002` against `upstream 1023-1028` honestly, and
            # nothing made anyone say so. The reviewer's objection to the first
            # version of this rule was that a ratio threshold is a number with no
            # data behind it, and this project has produced a wrong number from a
            # chosen scope parameter three times; the *existence* of a shape
            # change is binary and needs no threshold. A region whose spans match
            # can be read off the diff; one whose spans do not, cannot.
            ours_len = 0 if ours_span is None else ours_span[1] - ours_span[0] + 1
            theirs_len = (
                0 if theirs_span is None else theirs_span[1] - theirs_span[0] + 1
            )
            shape_changed = ours_len != theirs_len
            if shape_changed and snippet is None:
                failures.append(
                    f"{name}: region ours:{_fmt(ours_span)} "
                    f"upstream:{_fmt(theirs_span)} holds {ours_len} of our "
                    f"line(s) against {theirs_len} of upstream's, so the line "
                    "diff cannot say what happened — declare a note naming what "
                    "moved, not only the span"
                )

            # (2) A region holding executable lines must name them.
            if ours_span is None:
                continue
            inside = sorted(set(range(ours_span[0], ours_span[1] + 1)) & executable)
            if not inside:
                # A snippet on a region with no executable line is only wrong
                # when the spans *match*: then it declares nothing the region
                # does not already say. When they differ it is clause (5)'s
                # required note, and a pure insertion — the marker line, whose
                # upstream span is `None` — is exactly that case.
                if snippet is not None and not shape_changed:
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
            unnamed = [n for n in inside if ours[n - 1].strip() not in snippet]
            if unnamed:
                failures.append(
                    f"{name}: region ours:{_fmt(ours_span)} snippet does not "
                    f"name line(s) {unnamed}: {[ours[n - 1].strip() for n in unnamed]}"
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
