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
# hand-written corpus does not, so both passes are needed). That script takes
# the module as an argument — `m9_measure_parse_path.py context.py engine.py` —
# so one instrument regenerates any entry here, and it prints the entry ready to
# paste. It lives under `m9-` because M9 wrote it; nothing about it is M9's.
#
# The table is pinned rather than re-measured on every run because measuring
# means installing the shim and parsing ~566 templates, which is the parity
# suite's own cost and not this static guard's job. It cannot go stale quietly:
# `tests/test_interposition.py::test_the_compared_path_is_the_measured_one`
# re-runs the instrument and fails if the two disagree. A pinned table with a
# live re-measurement behind it is the same shape as the module meter.
#
# 29 functions, spanning 116-1200 in a file of 1200 lines — nearly the whole
# file, which is the finding.
# A guard that checked only the named target's own lines would miss a region
# declared in any of the other 28.
#
# **These are the transcription's line numbers, so an insertion moves them, and
# M11 Task 2's is why eleven of them are not what M10 pinned.** The filter seam
# adds nineteen lines to `FilterExpression.resolve` (815-833, declared above),
# which shifts every function below it by nineteen: `args_check` 827-844 →
# 846-863 through `token_kwargs` 1137-1181 → 1156-1200. Re-measured with the
# instrument rather than computed, because the instrument is the evidence; the
# shift is arithmetic anyone can check, and the *set* of functions is the part
# that could have moved for a real reason (the routing arm is on `resolve`'s own
# lines, and `resolve` is not in this table — the parse harness never renders).
# The stale pin did not fail quietly: clause (4) read the old `args_check` span
# against the new region and reported an overlap that was not there, which is
# the false *positive* half of the same staleness the re-measurement row catches
# from the other side.
#
# The count was 28 until the instrument learned to see through a descriptor.
# `FilterExpression.args_check` is bound with `staticmethod(...)`, and the sweep
# wrapped only `inspect.isfunction`, so the 85 calls the parity corpus makes to
# it were counted by nothing and a declared region could have sat on 827-844
# with clause (4) satisfied — Rust against Rust. The reviewer found it; the
# measurement confirmed the span and the call count before anything changed.
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
        (860, 877, "FilterExpression.args_check"),
        (909, 960, "Variable.__init__"),
        (962, 978, "Variable.resolve"),
        (1077, 1089, "Node.get_nodes_by_type"),
        (1097, 1098, "NodeList.render"),
        (1100, 1105, "NodeList.get_nodes_by_type"),
        (1111, 1112, "TextNode.__init__"),
        (1120, 1127, "TextNode.render_annotated"),
        (1149, 1150, "VariableNode.__init__"),
        (1170, 1214, "token_kwargs"),
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
    # 0 functions, measured rather than assumed: the parity harness executes
    # nothing in this file. The reason is worth stating, because the tempting
    # one is wrong: the harness *does* build a fresh `Engine` per call
    # (`tests/test_parser_parity.py:114`), so this is not about when the Engine
    # is constructed. It is about *which* route the harness takes. `EngineHandler`
    # is reached only through the `django.template.engines` singleton
    # (`engines.all()`, `engines[alias]`), and the harness resolves no template
    # alias; `get_app_template_dirs` is reached only by the `app_directories`
    # loader, and the harness builds no loaders. It passes `libraries=` straight
    # to `Engine`, whose `get_template_libraries` calls `import_library` — which
    # lives in `library.py`, not here.
    #
    # Measured with `.review/m10-measurements/m10_measure_compared_path.py`,
    # which interposes all nine M10 files verbatim in a scratch universe and
    # runs both corpora through `django_parse`; re-measured live on every unit
    # run by `test_the_compared_path_is_the_measured_one`.
    #
    # **Which harness, because the tree has two and they differ in exactly the
    # dimension a timing premise is about.** This is measured through
    # `django_parse`, the *parity* harness (`test_parser_parity.py:108`), which
    # builds a fresh `Engine` per call. The *error* harness
    # (`test_parser_errors.py:63`) holds `_ENGINE = Engine(libraries=LIBRARIES)`
    # at module level, built at import — so a premise about when the Engine is
    # constructed is true there and false here. The route argument above does
    # not transfer either: a module reached only on an error path would need its
    # own measurement. Naming the harness is the difference between a premise
    # that can be checked and one that merely sounds right.
    "utils.py": [],
    # 3 functions, spans 31-155 of 309. The parity harness is a *parsing*
    # harness, and `Context` is a rendering object — so the reach here is not
    # the render path but one line of `defaulttags`: `do_verbatim` renders its
    # block at parse time with a fresh `Context()` (`defaulttags.py:1607`), and
    # every source that reaches these three functions is a `{% verbatim %}` row.
    # Measured with `.review/m10-measurements/m10_measure_compared_path.py`.
    #
    # The narrowness is the finding, and it is why `context` is worth pinning
    # even though the module is on the render path for 984 of the 1,547 tests:
    # the pin is about what the *parity* harness executes, and for this module
    # that is three functions and no more. A declared region anywhere else in
    # these 309 lines is Rust-against-Rust in the parity rows and nothing here
    # would have said so.
    "context.py": [
        (31, 32, "BaseContext.__init__"),
        (34, 40, "BaseContext._reset_dicts"),
        (146, 155, "Context.__init__"),
    ],
    # 3 functions, spans 22-129 of 236 — `Engine.__init__` and the two lookups
    # it makes eagerly. The harness builds a **fresh** `Engine` per call
    # (`test_parser_parity._library_engine`), so this is entered once per
    # corpus source rather than once per process, and the count is per call.
    #
    # `get_template_builtins` and `get_template_libraries` are plain methods —
    # **not** the `lru_cache`d pair the plan calls them, which is `Engine.
    # get_default` (a `staticmethod`, measured as never reached: the harness
    # builds its Engines directly and never asks for the global default). They
    # are in the pin under their own spans because each is a separate function
    # whose lines the guard has to be able to name; a declared region placed in
    # either belongs to that region and not to `__init__`'s.
    # Measured with `.review/m10-measurements/m10_measure_compared_path.py`.
    "engine.py": [
        (22, 65, "Engine.__init__"),
        (122, 123, "Engine.get_template_builtins"),
        (125, 129, "Engine.get_template_libraries"),
    ],
    # 0 functions each: nothing a parity harness executes reaches the loaders.
    # Measured with `.review/m10-measurements/m10_measure_compared_path.py`; the
    # loaders are entered through `Engine.get_template`, which no parity source
    # calls (the harness parses, it does not render), so the twenty-one functions
    # these six files define between them are all outside the reach of both
    # corpora. Present rather than absent, for the key-set-equality clause: an
    # entry missing here is a guard failure, not a silent pass.
    "loaders/__init__.py": [],
    "loaders/base.py": [],
    "loaders/filesystem.py": [],
    "loaders/app_directories.py": [],
    "loaders/cached.py": [],
    "loaders/locmem.py": [],
    # The three `do_*` compile functions are here and they are the interesting
    # part of this entry. The engine does not run them — Rust compiles
    # `block`/`extends`/`include` host-side (M7's design B) — but the *reference*
    # parse does, because `Parser.parse` is upstream's text driving
    # `parser.tags[command](parser, token)`. They were missing until the sweep
    # learned to see registry entries; the frames instrument read 7 while the
    # sweep read 4. Left in the pin rather than noted as a blind spot, because
    # clause (4)'s whole purpose is that a declared region cannot sit on code
    # the comparison runs — and a tag's compile function is where a
    # transcription of it would most plausibly hide one.
    "loader_tags.py": [
        (45, 48, "BlockNode.__init__"),
        (92, 96, "ExtendsNode.__init__"),
        (168, 174, "IncludeNode.__init__"),
        (216, 245, "do_block"),
        (248, 290, "construct_relative_path"),
        (293, 314, "do_extends"),
        (317, 372, "do_include"),
    ],
    # One function, and it is the *decorator* rather than any of the 59 filter
    # bodies the module exists for. `stringfilter` is reached because
    # `tests/template_tests/templatetags/custom.py` does
    # `from django.template.defaultfilters import stringfilter` and then applies
    # `@stringfilter` **at import time**, and the corpus's `{% load custom %}`
    # triggers that import. So the reach is a property of the harness's *setup*
    # rather than of its comparison — and it is in the pin anyway, because the
    # decorator's body decides what `custom.myfilter` is and the corpus's
    # expected output depends on that. Clause (4) refusing a region there is the
    # point, not an accident of the instrument.
    #
    # **The reach is import-order sensitive, measured, and that is why it is
    # recorded here rather than left to be rediscovered.** Whichever of the two
    # instruments runs *second* in a process reads 0: the import has happened,
    # `@stringfilter` is applied once per process, and nothing re-applies it.
    # Cold — which is what a fresh measurement process, and `scripts/check.sh`'s
    # differential step, actually do — both read 1, and `stringfilter` alone is
    # the module where the two orderings differ; every other pinned module's
    # functions are called per parse. The pin is the cold reading, because the
    # cold reading is the harness's.
    "defaultfilters.py": [
        (40, 54, "stringfilter"),
    ],
    "defaulttags.py": [
        # Measured, 44 of the file's 82 functions. **The plan's Step 2 predicted
        # a small set — `load` and `querystring` "because the host does not own
        # them", plus the two stub tags — and the measurement disagrees with it
        # in both directions**, so recording which is which matters more than the
        # list itself:
        #
        # * **`load` is in it and the other three are not.** `querystring`,
        #   `partial` and `partialdef` are absent because the parity corpus
        #   (`tests/parser_corpus.py`) holds no template that uses them, not
        #   because the shell handles them host-side. The prediction reasoned
        #   about the *render* path; this pin is about the *harness*'s path, and
        #   the two reach different code.
        # * **Everything else in it is the tags the shell does own** — `cycle`,
        #   `do_if`, `do_for`, `url`, `widthratio` — for the same reason: the
        #   harness's Django side is a bare `Parser` over this transcription, so
        #   `parser.tags[command]` *is* this file's function whether or not the
        #   shell would have routed elsewhere. The prediction's premise ("Rust's,
        #   so unreachable") is true of the render path and false here.
        #
        # The direction of the error is the safe one for clause (4) — the guard
        # protects a superset of the prediction — and the unsafe direction is
        # already covered by the under-protection note below, which is the reason
        # this list is re-measured live rather than trusted.
        (53, 55, "AutoEscapeControlNode.__init__"),
        (101, 104, "CycleNode.__init__"),
        (139, 141, "FilterNode.__init__"),
        (151, 153, "FirstOfNode.__init__"),
        (171, 181, "ForNode.__init__"),
        (264, 267, "IfChangedNode.__init__"),
        (310, 311, "IfNode.__init__"),
        (316, 318, "IfNode.__iter__"),
        (320, 322, "IfNode.nodelist"),
        (341, 344, "LoremNode.__init__"),
        (364, 367, "RegroupNode.__init__"),
        (400, 402, "NowNode.__init__"),
        (441, 442, "ResetCycleNode.__init__"),
        (450, 451, "SpacelessNode.__init__"),
        (471, 472, "TemplateTagNode.__init__"),
        (481, 485, "URLNode.__init__"),
        (528, 529, "VerbatimNode.__init__"),
        (536, 540, "WidthRatioNode.__init__"),
        (569, 575, "WithNode.__init__"),
        (586, 601, "autoescape"),
        (604, 610, "comment"),
        (613, 703, "cycle"),
        (706, 708, "csrf_token"),
        (711, 723, "debug"),
        (726, 757, "do_filter"),
        (760, 806, "firstof"),
        (809, 908, "do_for"),
        (912, 914, "TemplateLiteral.__init__"),
        (926, 928, "TemplateIfParser.__init__"),
        (930, 931, "TemplateIfParser.create_var"),
        (934, 1022, "do_if"),
        (1025, 1064, "ifchanged"),
        (1067, 1077, "find_library"),
        (1104, 1133, "load"),
        (1136, 1180, "lorem"),
        (1183, 1203, "now"),
        (1354, 1419, "regroup"),
        (1422, 1445, "resetcycle"),
        (1448, 1476, "spaceless"),
        (1479, 1513, "templatetag"),
        (1516, 1586, "url"),
        (1589, 1609, "verbatim"),
        (1612, 1652, "widthratio"),
        (1655, 1689, "do_with"),
    ],
    # 0 functions each, and the zero is the measurement rather than a blank left
    # to be filled in: `.review/m9-measurements/m9_measure_parse_path.py` reports
    # 0 of 3 for `cache.py` and 0 of 15 for `static.py` over both corpora. The
    # reason is the corpora's — a parity source reaches a library's tag bodies
    # only by writing `{% load <library> %}` first, and `tests/parser_corpus.py`
    # loads the `template_tests.*` libraries and never `cache` or `static`. So
    # `do_cache`, `do_static` and the two `PrefixNode`/`StaticNode.handle_token`s
    # are defined and simply not entered, which is `smartif.py`'s ordinary
    # meaning of "compared path" above: nothing is hidden by the zero, because
    # clause (4) protects only code the comparison actually runs.
    #
    # The two are present rather than absent for the key-set-equality clause —
    # an entry missing here is a guard failure, not a silent pass — and the
    # re-measurement row is what keeps the zero honest: a corpus that grew a
    # `{% load static %}` row would move these entries and
    # `test_the_compared_path_is_the_measured_one` would fail in the commit that
    # added it.
    "templatetags/cache.py": [],
    "templatetags/static.py": [],
    # M12 Task 4's, measured rather than copied: `m9_measure_parse_path.py
    # templatetags/tz.py` reports **0 of 12** functions over both corpora, and
    # the 12 is `definitions()`' counting unit — six module-level defs (the
    # three tag bodies, the three filter bodies) plus two methods each on
    # `LocalTimeNode`, `TimezoneNode` and `GetCurrentTimezoneNode`. The reason
    # for the zero is the corpora's: `tests/parser_corpus.py`'s `{% load %}`
    # rows reach the `template_tests.*` libraries and never
    # `django.templatetags.tz`, so every one of the 12 is defined and simply not
    # entered — `cache.py`'s and `static.py`'s zero, one library over. The
    # re-measurement row keeps it
    # honest: a corpus that grew a `{% load tz %}` row would move this entry and
    # `test_the_compared_path_is_the_measured_one` would fail in the commit that
    # added it.
    "templatetags/tz.py": [],
    # M12 Task 5's, measured with the same instrument rather than copied:
    # `m9_measure_parse_path.py templatetags/l10n.py` reports **0 of 6**
    # functions over both corpora (hand-written 166: 0; generated 400: 0), and
    # the 6 is `definitions()`' counting unit — three module-level defs
    # (`localize`, `unlocalize`, `localize_tag`) plus three methods on
    # `LocalizeNode` (`__init__`, `__repr__`, `render`). The zero has
    # `cache.py`'s reason, one library over: `tests/parser_corpus.py`'s
    # `{% load %}` rows reach the `template_tests.*` libraries and never
    # `django.templatetags.l10n`, so every one of the 6 is defined and simply not
    # entered. The re-measurement row keeps it honest — a corpus that grew a
    # `{% load l10n %}` row would move this entry, and
    # `test_the_compared_path_is_the_measured_one` would fail in the commit that
    # added it.
    "templatetags/l10n.py": [],
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
            # M11 Task 2's filter seam: `FilterExpression.resolve`'s loop over
            # `self.filters` gains one branch, and it is a **pure insertion** —
            # upstream has no counterpart on these lines, hence `None` on the
            # upstream side and a snippet that names statements rather than
            # pairing them. Seventeen executable lines in the one region below:
            # the import that reaches the shell, the identity test against the
            # built-in registry, the two flag reads, the call with its five
            # arguments, and the `continue`. Seventeen rather than the eleven
            # this note first claimed, because `executable_lines` counts a
            # parenthesised `import`'s continuation lines: the six-line import
            # is one statement and six lines, and only the second granularity is
            # what the guard compares.
            #
            # **One region, and that is a decision rather than a coincidence.**
            # The imports sat inside the loop above the identity test; a
            # reviewer's NIT moved them above the `for` — once per `resolve`
            # instead of once per filter, which no test can observe — and that
            # split this region in two, because upstream's verbatim `arg_vals`
            # loop (ours 809-814, equal in place) then sits between the imports
            # and the branch, and equal lines are what makes `difflib` see two
            # differing regions rather than one. Reverted on the reviewer's own
            # measurement of the cost: a declared region is the diff a reader
            # diffs against upstream on every Django bump, so **two regions where
            # one would do is a permanent tax on every future bump**, paid for a
            # `sys.modules` hit. In this tree a NIT that changes a transcription's
            # diff shape is not a NIT — the region count is part of the review
            # surface. The code comment above the import says the same in three
            # lines, where someone reading the file will meet it.
            #
            # **That `continue` is the design, not a shortcut.** The arm
            # *replaces* the loop body below rather than wrapping it, because
            # `_native.filter` already performs upstream's four steps —
            # `expects_localtime`, `needs_autoescape`, the call, and
            # `is_safe`/`mark_safe` — inside itself (design spec §2,
            # constraint 1). Letting the arm fall through to those four steps
            # would apply `@stringfilter`'s coercion and `mark_safe` a second
            # time, and the second `mark_safe` is invisible: it is idempotent
            # on `SafeString`, so the doubled coercion is what a reader would
            # have to be watching for.
            #
            # **This region is outside clause (4)'s reach, and that is a
            # measurement rather than an assumption.** `COMPARED_PATH["base.py"]`
            # holds the 29 names this guard actually compares against the pin;
            # `FilterExpression.resolve` is not among them — the compared path is
            # the *parse* harness — so clause (4) permits this region without
            # examining it, and clause (2) is the only thing standing between
            # these seventeen lines and nothing at all. What the guard therefore
            # cannot see is the one failure that matters here: an arm that
            # delegated to `_native.filter` and then *also* ran the four steps,
            # or an arm whose identity test were keyed by name rather than
            # handle. Both keep every filter row in the suite green. The
            # instruments that would notice are named elsewhere —
            # `tests/test_interposition.py::FilterRoutingTests` counts the
            # `_native.filter` calls and asserts the name it was handed — and the
            # honest statement is that the *transcription* guard is not one of
            # them.
            #
            # Second consequence, and the reason the corpus's own parity harness
            # moved in the same commit: `resolve` used to be upstream's text, so
            # `scripts/filter_probe.py`'s Django side *was* Django. It is now a
            # delegating `resolve`, which would have made
            # `tests/test_filter_parity.py` a comparison of Rust against Rust —
            # green for every built-in row. The probe reads the pinned
            # `FilterExpression` for that reason, and
            # `test_filter_parity.py::test_the_comparison_target_is_not_rust_owned`
            # is what watches *that*.
            (
                (815, 847),
                None,
                "from django_rust_templates import _native\n"
                "from django_rust_templates.host import (\n"
                "django_own_filters,\n"
                "effective_autoescape,\n"
                "effective_use_tz,\n"
                ")\n"
                "if (builtin_name := django_own_filters().get(func)) is not None:\n"
                "use_tz = effective_use_tz(context, func)\n"
                "autoescape = effective_autoescape(context, func)\n"
                "obj = _native.filter(\n"
                "builtin_name,\n"
                "obj,\n"
                "tuple(arg_vals),\n"
                "autoescape,\n"
                "use_tz,\n"
                ")\n"
                "continue",
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
                (995, 1033),
                (957, 1021),
                "from django_rust_templates import _native",
            ),
            (
                (1035, 1035),
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
    "utils.py": (
        "django/template/utils.py",
        [
            # The marker and its blank separator, ours only — a pure insertion
            # (`None` upstream). Upstream has no docstring here, so the marker
            # is a comment on line 1 rather than a docstring's first line, which
            # is why this region is `(1, 2)` where `smartif.py`'s is `(2, 3)`;
            # `MARKER_WINDOW` accepts both. No executable line either way, so
            # the snippet is clause (5)'s required note.
            #
            # Like `smartif`, and unlike `base.py`, **that is the entire file**:
            # no delegation, nothing to read past the marker.
            ((1, 2), None, "# TRANSCRIBED: django/template/utils.py (Django 6.0.9)"),
        ],
    ),
    "context.py": (
        "django/template/context.py",
        [
            # The marker and its blank separator, ours only. Upstream opens with
            # an import, so the marker is a comment on line 1 rather than a
            # docstring's first line; no executable line, hence clause (5)'s
            # note rather than a list of statements.
            #
            # **This is the entire file**, and it is the strongest form of that
            # claim in the tree: 309 lines of ours against 307 of upstream's,
            # with 984 of the suite's 1,547 tests rendering through `Context`.
            # A transcription this load-bearing is the one where "the diff is
            # the marker" has to be measured rather than asserted, so it is —
            # `scripts/check_transcriptions.py` fails the commit if any line
            # here is not upstream's.
            ((1, 2), None, "# TRANSCRIBED: django/template/context.py (Django 6.0.9)"),
        ],
    ),
    "engine.py": (
        "django/template/engine.py",
        [
            # The marker and its blank separator, ours only. Upstream opens with
            # `import functools`, so the marker is a comment on line 1; no
            # executable line, hence clause (5)'s note rather than statements.
            #
            # **This is the entire file**, like `context.py` and `smartif.py` —
            # 236 lines against upstream's 234, and the diff is the two lines of
            # marker. The four imports below the marker are upstream's
            # (`functools`, `ImproperlyConfigured`, `cached_property`,
            # `import_string`) and so is every line after them.
            ((1, 2), None, "# TRANSCRIBED: django/template/engine.py (Django 6.0.9)"),
        ],
    ),
    # M10's six loader files. Three shapes among them, and the shape decides
    # which side of the file the marker lands on — so each row below is written
    # from what the generator produced, which the guard then re-checks:
    #
    # * `__init__.py` (0 bytes), `base.py` (opens with an import): a comment on
    #   line 1, region `((1, 2), None, ...)`;
    # * `filesystem.py`, `app_directories.py`, `cached.py`, `locmem.py` (all open
    #   with a docstring): inside it, region `((2, 3), None, ...)`.
    #
    # **`loaders/__init__.py` is 0 bytes upstream**, so ours is the marker and
    # its blank line and nothing else — a two-line file whose whole content is
    # the claim that it transcribes an empty one. It is the only transcription in
    # the tree where upstream *is* the empty string.
    #
    # **The premise the +2 span arithmetic rests on, stated because it is
    # invisible in the table.** Every `COMPARED_PATH` span for M10's files is
    # upstream's line number plus two, and that holds only because each
    # transcription inserts its marker and nothing else — **none of these nine
    # rewrites its module docstring**, unlike `_impl/__init__.py`, which does. A
    # file that both inserted a marker and edited its docstring would shift its
    # spans by two *plus* the docstring delta, and a uniform +2 would then be
    # wrong for every function in it while the table looked like every other row.
    # `gen_impl_transcription.py --check` on the fast path is what keeps that
    # true, and it is why it is worth a step in `check.sh`.
    "loaders/__init__.py": (
        "django/template/loaders/__init__.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/loaders/__init__.py (Django 6.0.9)",
            ),
        ],
    ),
    "loaders/base.py": (
        "django/template/loaders/base.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/loaders/base.py (Django 6.0.9)",
            ),
        ],
    ),
    "loaders/filesystem.py": (
        "django/template/loaders/filesystem.py",
        [
            (
                (2, 3),
                None,
                "# TRANSCRIBED: django/template/loaders/filesystem.py (Django 6.0.9)",
            ),
        ],
    ),
    "loaders/app_directories.py": (
        "django/template/loaders/app_directories.py",
        [
            (
                (2, 3),
                None,
                "# TRANSCRIBED: django/template/loaders/app_directories.py (Django 6.0.9)",
            ),
        ],
    ),
    "loaders/cached.py": (
        "django/template/loaders/cached.py",
        [
            (
                (2, 3),
                None,
                "# TRANSCRIBED: django/template/loaders/cached.py (Django 6.0.9)",
            ),
        ],
    ),
    "loaders/locmem.py": (
        "django/template/loaders/locmem.py",
        [
            (
                (2, 3),
                None,
                "# TRANSCRIBED: django/template/loaders/locmem.py (Django 6.0.9)",
            ),
        ],
    ),
    # M11's first. The pin opens with `import posixpath`, so there is no
    # docstring to carry the marker and it is a comment on line 1 — the
    # docstring-less form `loaders/base.py` uses. Nothing else in the file
    # differs from the pin: M7 made the three `block`/`extends`/`include` tags
    # Rust's by compiling them host-side, which changes who *runs* `do_block`,
    # not one character of the transcription, and `BlockNode.render`'s
    # render-time node building stays Python by M7's measured design.
    "loader_tags.py": (
        "django/template/loader_tags.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/loader_tags.py (Django 6.0.9)",
            ),
        ],
    ),
    "defaultfilters.py": (
        "django/template/defaultfilters.py",
        [
            # The marker and its blank separator — a pure insertion, hence `None`
            # on the upstream side, and the whole of the difference. **The file
            # is pin-plus-marker on purpose**: all 59 filter bodies stay
            # upstream's text, and what makes them reachable from Rust is a
            # branch in `base.py`'s `FilterExpression.resolve`, which is declared
            # in *that* file and is where the under-protection note lives. A
            # delegate per body would be 59 regions here all saying one thing.
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/defaultfilters.py (Django 6.0.9)",
            ),
        ],
    ),
    "defaulttags.py": (
        "django/template/defaulttags.py",
        [
            # The marker and its blank separator — a pure insertion, hence `None`
            # on the upstream side, and the whole of the difference.
            #
            # **Pin-plus-marker here is a stronger claim than it is in the two
            # files above it, and the difference is worth stating — with the
            # counts measured rather than taken from the plan, which had two of
            # them wrong.** Measured on this tree: the module registers **24**
            # tags and defines **26** module-level functions; the plan's "the 26
            # `do_*` bodies are Rust-compiled" reads the second number as if it
            # were the first. Of the 24 registered tags, **20 are Rust's** — not
            # 24 — and their bodies are unreachable through the *render* path,
            # because Rust compiles and renders them and `ShellHost.has_tag`
            # answers False. The four reached host-side are `load`, `querystring`
            # (Rust owns neither name) and the two stubs, `partial` and
            # `partialdef`, which Rust reserves without implementing.
            #
            # So the honest reading of this file is "faithful, and mostly dead
            # code", and that is the claim the verbatim transcription makes. The
            # alternative — stubbing the 20 out — would be a *different* claim
            # ("nothing here needs to be right") and a worse one: `{% load %}`
            # rebinding a built-in tag puts Django's body back on a live path at
            # runtime (`ShellHost.has_tag`'s identity test), and a stub would then
            # be a stub in the one case this milestone is most concerned with.
            #
            # Measured rather than argued: `m9_measure_parse_path.py defaulttags.py`
            # reports the harness's reached set (44 functions), and
            # `tests/test_interposition.py`'s `DefaultTagsInterpositionTests` pins
            # the one input that puts a body from this file on a live path.
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/defaulttags.py (Django 6.0.9)",
            ),
        ],
    ),
    # M12's first two, and the first rows here whose upstream module is
    # `django/templatetags/…` rather than `django/template/…` — which is why
    # every value above this comment is `"django/template/" + key` and these
    # two are not. The *shape* of the transcription is unchanged: the pin plus
    # a two-line marker, `None` on the upstream side, and the whole of the
    # difference. Both open with imports rather than a docstring, so the marker
    # is a comment on line 1 and the region is `(1, 2)` — the
    # `loader_tags.py`/`loaders/base.py` form.
    #
    # **`templatetags/__init__.py` is deliberately absent.** The plan listed it,
    # and the shim's `_is_foreign_package` is why it cannot be a transcription:
    # it declines to serve the `django.templatetags` package itself — we serve
    # only files inside it — so our `__init__.py` would never be loaded while
    # `shadowing_files()` still reported it. Upstream's is 0 bytes and defines
    # nothing; there is no behaviour to transcribe and none to get wrong.
    #
    # Verbatim is the right claim for both tag modules even though the four tags
    # Rust now compiles out of them are unreachable through the host: `has_tag`
    # answers False for a Rust-owned name the template loaded, so `do_cache`,
    # `do_static` and the two `handle_token`s are bodies nothing calls. The case
    # that needs them right is a library rebinding one of the four names, which
    # is the same case `defaulttags.py` above is kept verbatim for.
    "templatetags/cache.py": (
        "django/templatetags/cache.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/cache.py (Django 6.0.9)",
            ),
        ],
    ),
    "templatetags/static.py": (
        "django/templatetags/static.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/static.py (Django 6.0.9)",
            ),
        ],
    ),
    # M12 Task 4's, the third of the five and the same shape as the two above:
    # the pin plus a two-line marker, `None` on the upstream side, and nothing
    # else. The module opens with imports, so the marker is a comment on line 1
    # and the region is `(1, 2)`.
    #
    # Verbatim is right for the three tag bodies although Rust compiles all three
    # names: `has_tag` answers False for a Rust-owned name the template loaded,
    # so `localtime_tag`, `timezone_tag` and `get_current_timezone_tag` are
    # bodies nothing calls — the same case `defaulttags.py` is kept verbatim for.
    # The three *filters* (`localtime`, `timezone`, `utc`, the last of which is
    # `do_timezone` under a registered alias) are Task 7's Rust bodies and are
    # transcribed-verbatim here for the same reason: the unit of transcription is
    # the file, and a per-file table cannot express a half-transcribed module.
    "templatetags/tz.py": (
        "django/templatetags/tz.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/tz.py (Django 6.0.9)",
            ),
        ],
    ),
    # M12 Task 5's, the fourth of the five and the same shape: the pin plus a
    # two-line marker, `None` on the upstream side, nothing else. The module
    # opens with imports, so the marker is a comment on line 1 and the region is
    # `(1, 2)`.
    #
    # Verbatim is right for the one tag body even though Rust compiles its name:
    # `has_tag` answers False for a Rust-owned name the template loaded, so
    # `localize_tag` is a body nothing calls — the same case `defaulttags.py` and
    # the three modules above are kept verbatim for. **The render and the
    # filters stay Python's, and the marker's meaning is why that is a
    # transcription rather than a delegation:** `LocalizeNode.render` is our
    # transcription's Python (only the *compile* function is Rust's), and the two
    # filters (`l10n.py:8,16`, whose bodies make the `django.utils.formats`
    # calls) are Task 7's Rust
    # bodies — transcribed-verbatim here for the reason `tz.py`'s are: the unit
    # of transcription is the file, and a per-file table cannot express a
    # half-transcribed module. The generated marker line says the upstream path
    # and the pin and nothing more, which is what every marker in this table
    # says; the "the formats delegation stays Python's" claim lives here, where
    # the guard reads it, as it does for the four modules above.
    "templatetags/l10n.py": (
        "django/templatetags/l10n.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/l10n.py (Django 6.0.9)",
            ),
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
    # `rglob`, not `glob`, and `_impl`-relative keys, not basenames. Both halves
    # matter and both were latent until M10: `glob("*.py")` cannot see
    # `loaders/base.py`, so a marker-bearing file in a subpackage is invisible to
    # the clause below `_check_marker` and to this loop — and `path.name` would
    # key it `base.py`, which is *another* transcription's key, so the entry it
    # looked up would be the wrong file's while every clause passed.
    #
    # `loaders/base.py` is also the spelling `_shim.shadowing_files()` reports
    # and the one `m10_measure_compared_path.py` prints `COMPARED_PATH` rows in,
    # so this is the fourth table agreeing with the other three rather than a
    # fourth convention.
    marked = sorted(
        path.relative_to(IMPL).as_posix() for path in IMPL.rglob("*.py") if _marked(path)
    )
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
    unmarked = [
        name for name in unmarked if IMPL.joinpath(*name.split("/")).is_file()
    ]
    if unmarked:
        failures.append(
            f"{unmarked} is declared as a transcription but carries no "
            "`# TRANSCRIBED:` marker"
        )
    for name, (upstream, regions) in sorted(DECLARED.items()):
        # `joinpath(*name.split("/"))`, the same reading of a key as the clause
        # above: a key is a posix path relative to `_impl`, not one segment.
        ours_path = IMPL.joinpath(*name.split("/"))
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
