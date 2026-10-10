"""Every ``engine`` transcription differs from upstream only where it declares.

A ``# TRANSCRIBED:`` file is a claim about the pinned tree, and the claim is
only falsifiable if someone reads the diff. ``.review/m9-recon.md`` finding 3
measured the price of not reading it: ``engine/base.py`` as a verbatim copy, and
``engine/base.py`` transcribing *wrongly*, both keep all 1547 tests green. The
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
one transcription that already exists settled the question: ``engine/__init__.py``
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
IMPL = ROOT / "python" / "django_rust_templates" / "engine"

# `# TRANSCRIBED: django/template/base.py (Django 6.0.9)`, on its own line, as
# the first line inside the module docstring.
MARKER = re.compile(
    r"^# TRANSCRIBED: (?P<path>\S+) \(Django (?P<version>[0-9][^)]*)\)$"
)

# How far into the file the marker may be. The convention is the first line
# inside the docstring; `engine/__init__.py` puts a blank line first, so the test
# is "inside the docstring", which four lines covers for both forms.
MARKER_WINDOW = 4

# This package's dotted root, and the one it transcribes. The M18 rename gave the
# engine a namespace of its own, so every dotted path a transcription *names* —
# `default_builtins`, the default loaders, the backend — is this package's
# rather than Django's, and the two spellings differ by exactly this
# substitution. Clause (4) consults both; see `_namespace_only`.
OURS_NAMESPACE = "django_rust_templates.engine."
UPSTREAM_NAMESPACE = "django.template."

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
# 30 functions, spanning 117-1252 in a file of 1252 lines — nearly the whole
# file, which is the finding.
# A guard that checked only the named target's own lines would miss a region
# declared in any of the other 29.
#
# **The 29 above was already stale when M16 arrived, and the correction is
# `git show`, not a recount.** `24bed0f` (M13's "Rust compiles `{% partialdef
# %}`, and no built-in tag is a stub") added the thirtieth entry —
# `PartialTemplate.__init__`, a *real* addition to the compared path and not a
# shift: with the partial's body compiled by Rust the harness now builds the
# node it hands back — and left the sentence at 29, so `b5cc50f`'s
# pin=29/sentence=29 became pin=30/sentence=29 and stayed that way for ten
# commits. Nothing failed, because the sentence is prose and the pin is the
# assertion; the row that could have caught it
# (`test_the_compared_path_is_the_measured_one`) compares the pin to the
# instrument and never reads this paragraph. Recorded rather than silently
# repaired because it is the same class of defect the M15 record files: a number
# whose carrier moved under it, found here while re-deriving the numbers M16
# does move.
#
# **The trailing pair above was stale as well, by eight, and M19 is where it was
# measured.** The committed file was 1220 lines and `token_kwargs` ended at 1220
# (`git show HEAD:python/django_rust_templates/engine/base.py | wc -l`), so
# `117-1212 in a file of 1212 lines` was wrong in both places at once — the same
# single carrier, two numbers, which is why a reviewer reading only one of them
# would have found nothing. It reads `117-1244 in a file of 1244 lines` from
# M19 — 1252 after the `if self.filters:` guard recorded below — and both numbers
# come from `wc -l` and the pin rather than from adding a
# delta to the old text.
#
# **These are the transcription's line numbers, so an insertion moves them, and
# two commits have moved them.** M16's module-level `_native` import adds one
# line at 73 while its two in-function imports remove one inside
# `FilterExpression.resolve` and two inside `Variable._resolve_lookup`, so that
# shift was three-valued: **+1** for the nineteen functions above `resolve`
# (`Origin.__init__` 116-119 → 117-120 through `FilterExpression.__init__`
# 744-788 → 745-789), **0** for the four between `resolve` and
# `_resolve_lookup` (`args_check` 860-877 unchanged — the removal cancels the
# insertion — through `Variable.resolve` 962-978), and **-2** for the seven below
# it (`Node.get_nodes_by_type` 1077-1089 → 1075-1087 through `token_kwargs`
# 1170-1214 → 1168-1212). M19 then inserts **23 lines inside
# `FilterExpression.resolve` itself**, at 809, so every function that *starts*
# below that point moves by **+24** — `args_check` 860-877 → 884-901 through
# `token_kwargs` 1176-1220 → 1200-1244 — while every function above it keeps its
# M16 number. **The same place moves again by +8**, when the routing cache's
# prologue gains its `if self.filters:` guard: `args_check` 884-901 → 892-909
# through `token_kwargs` 1200-1244 → 1208-1252, and the file is 1252 lines.
# Stated as arithmetic so that it can be checked; the spans
# themselves are re-measured with the instrument, because the instrument is the
# evidence, and the *set* of functions is the part that could have moved for a
# real reason. `resolve` is not in this table — the parse harness never renders —
# which is why a change inside it shifts the table without appearing in it.
#
# The arithmetic was wrong here once before, in the direction this block exists
# to warn about: it counted the region someone had named (M11's arm, nineteen
# lines) rather than what that commit did (thirty-three), and the pin was right
# throughout. A shift belongs to the commit, not to the block you have in mind;
# `git show <commit> -- <file> | grep '^@@'` is the measurement.
# The stale pin did not fail quietly: clause (4) read the old `args_check` span
# against the new region and reported an overlap that was not there, which is
# the false *positive* half of the same staleness the re-measurement row catches
# from the other side. **M19 reproduced it exactly, and deliberately**: with
# these spans still 24 lines high, the arm's new region `ours:839-871` overlapped
# a function it has nothing to do with, and clause (4) said so —
# `region ours:839-871 covers FilterExpression.args_check (860-877), which the
# parity harness executes`. The row above caught the other half first
# (`measured[label] (884, 901) != pinned[label] (860, 877)`, for ten labels), so
# the fix was a re-measurement rather than a re-reading. That a *false* clause
# (4) is the loudest thing a stale pin produces is the argument for measuring
# rather than computing, since the same staleness in the other direction is
# silent.
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
    # `autoreload.py` was here, and is not any more — see the note on its
    # removed `DECLARED` row below. It is the only file this directory has ever
    # stopped transcribing, so the row is deleted rather than emptied: a key
    # with an empty list still asserts that the module *is* a transcription.
    "base.py": [
        (117, 120, "Origin.__init__"),
        (307, 314, "PartialTemplate.__init__"),
        (365, 387, "Token.__init__"),
        (396, 409, "Token.split_contents"),
        (413, 415, "Lexer.__init__"),
        (424, 436, "Lexer.tokenize"),
        (438, 470, "Lexer.create_token"),
        (508, 530, "Parser.__init__"),
        (535, 597, "Parser.parse"),
        (599, 604, "Parser.skip_past"),
        (606, 623, "Parser.extend_nodelist"),
        (625, 636, "Parser.error"),
        (638, 654, "Parser.invalid_block_tag"),
        (665, 666, "Parser.next_token"),
        (668, 669, "Parser.prepend_token"),
        (671, 672, "Parser.delete_first_token"),
        (674, 676, "Parser.add_library"),
        (678, 682, "Parser.compile_filter"),
        (684, 688, "Parser.find_filter"),
        (745, 789, "FilterExpression.__init__"),
        (892, 909, "FilterExpression.args_check"),
        (941, 992, "Variable.__init__"),
        (994, 1010, "Variable.resolve"),
        (1115, 1127, "Node.get_nodes_by_type"),
        (1135, 1136, "NodeList.render"),
        (1138, 1143, "NodeList.get_nodes_by_type"),
        (1149, 1150, "TextNode.__init__"),
        (1158, 1165, "TextNode.render_annotated"),
        (1187, 1188, "VariableNode.__init__"),
        (1208, 1252, "token_kwargs"),
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
    # **`utils.py` was a key here until the M18 rewiring, and its removal is the
    # point of this note rather than a tidy-up.** It held `EngineHandler` and
    # `get_app_template_dirs`, and its entry was `[]` — nothing a parity harness
    # executes — for a reason that was itself the argument for the module's
    # deletion: `EngineHandler` is reached only through the `django.template.engines`
    # singleton (`engines.all()`, `engines[alias]`), and this package no longer
    # binds one, because Django's own registry already resolves
    # `TEMPLATES[0]["BACKEND"]` for our backend exactly as for any other. The
    # module is gone from the tree and the name is on
    # `tests/milestone_ownership.py`'s `NOT_THE_ENGINE`, so a key here would be a
    # declaration about a file that does not exist — which the guard reports as
    # `declared but not present`, and did, for as long as the stale key survived.
    # `app_directories` now imports Django's `get_app_template_dirs` outright; see
    # that module's region below.
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
    #
    # **Re-measured in the M18 rewiring, and all three spans moved — none of them
    # because a function joined or left the path.** Two changes stacked: the file
    # itself grew (the `get_default` region added twenty lines below `__init__`),
    # and the instrument that reads the reach had to be repaired first, because
    # its two `sys.modules` sweeps still filtered on ``startswith("django.")`` —
    # a shim-era scope under which *every* engine member's holders measured zero.
    # Read from the broken instrument, seven of the twenty keys looked changed and
    # all seven looked *smaller*; read from the repaired one, only `engine.py`
    # does, and by exactly the line shift. The other six are the instrument's, and
    # the re-pin that would have recorded them is the failure this note exists to
    # keep anyone from repeating. See `tests/compared_path.py`'s `swayable`.
    "engine.py": [
        (20, 67, "Engine.__init__"),
        (159, 160, "Engine.get_template_builtins"),
        (162, 166, "Engine.get_template_libraries"),
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
        # Measured, 48 of the file's 82 functions. **The plan's Step 2 predicted
        # a small set — `load` and `querystring` "because the host does not own
        # them", plus the two stub tags — and the measurement disagrees with it
        # in both directions**, so recording which is which matters more than the
        # list itself:
        #
        # * **`load` is in it; `querystring` is not.** That one is absent because
        #   the parity corpus (`tests/parser_corpus.py`) holds no template that
        #   uses it, not because the shell handles it host-side. The prediction
        #   reasoned about the *render* path; this pin is about the *harness*'s
        #   path, and the two reach different code. `partial` left this bullet in
        #   M13's Task 3 and `partialdef` in Task 4: the corpus gained a
        #   `{% partial p %}` row and then a
        #   `{% partialdef p %}body{% endpartialdef %}{% partial p %}` one, so
        #   `partial_func`/`PartialNode.__init__` and then
        #   `partialdef_func`/`PartialDefNode.__init__` are on the compared path
        #   and the count moved 44 → 46 → 48. That is the bullet's mechanism
        #   demonstrated rather than asserted — the corpus, not the shell,
        #   decides. The second row is also why `base.py`'s list gained
        #   `PartialTemplate.__init__`: the stored object is only *reachable* from
        #   a `PartialNode`'s mapping once a definition exists to be stored.
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
        (416, 419, "PartialDefNode.__init__"),
        (426, 429, "PartialNode.__init__"),
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
        (1206, 1267, "partialdef_func"),
        (1270, 1285, "partial_func"),
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
    # M12 Task 6b's, measured with the same instrument rather than copied:
    # `m9_measure_parse_path.py templatetags/i18n.py` reports **2 of 31**
    # functions over both corpora (hand-written 172: 2; generated 400: 0), and
    # the 31 is `definitions()`' counting unit — **12 module-level defs** (the
    # seven tag bodies behind the eight names — `trans` aliases `translate` —
    # plus `do_block_translate`, plus the
    # four filters `language_name`, `language_name_translated`,
    # `language_name_local` and `language_bidi` at `i18n.py:293,298,304,309`)
    # plus **19 methods** on the eight node classes (two each on the six flat
    # nodes, three on `GetLanguageInfoListNode` — `get_language_info` is its
    # extra — and four on `BlockTranslateNode`). Task 6a measured a zero, and
    # 6b's corpus rows are what moved it: the `{% load i18n %}` rows reach
    # `django.templatetags.i18n`, and the `{% blocktranslate %}` rows run its
    # compile function on **both** sides — `do_block_translate` (458-598) when
    # the reference parser compiles the tag, and `BlockTranslateNode.__init__`
    # (107-127) when the tree is materialised. The other **29** are still
    # `cache.py`'s case one library over: the corpus reaches only
    # `blocktranslate`/`blocktrans` among the eight names, so the other seven
    # tag bodies and every node method but `BlockTranslateNode.__init__` are
    # defined and simply not entered. The re-measurement row keeps it honest —
    # a corpus row that reached another name would move this entry, and
    # `test_the_compared_path_is_the_measured_one` would fail in the commit that
    # added it.
    "templatetags/i18n.py": [
        (107, 127, "BlockTranslateNode.__init__"),
        (458, 598, "do_block_translate"),
    ],
    # M13's, and **the plan predicted an empty entry here — the measurement
    # disagrees, 12 of 18, and the reason it disagrees is the reason the number
    # matters.** The plan's Step 1 read the corpus as holding "no template that
    # registers a tag library"; the corpus holds `{% load custom %}` rows, and
    # `tests/template_tests/templatetags/custom.py` runs `register =
    # template.Library()` and seven `@register.*` decorators **at import time**,
    # which is what `{% load custom %}` triggers. So the reach is a property of
    # the harness's *setup* rather than of its comparison — the same shape as
    # `defaultfilters.py`'s `stringfilter`, one module over — and it makes
    # `library.py` the second entry in this table (with `loader_tags.py`) whose
    # functions the comparison runs rather than merely defines.
    #
    # Measured with
    # `m9_measure_parse_path.py library.py` (hand-written 172: 12; generated
    # 400: 8; reached only by the generator: none), and the 18 is
    # `definitions()`' counting unit: 8 `Library` methods, 2 module-level defs
    # (`parse_bits`, `import_library`) and 8 node methods (two each on
    # `TagHelperNode`, `SimpleNode`, `SimpleBlockNode` and `InclusionNode`). The
    # six not entered are `Library.inclusion_tag`, `TagHelperNode.
    # get_resolved_arguments`, `SimpleNode.render`, `SimpleBlockNode.
    # get_resolved_arguments`, `InclusionNode.__init__` and
    # `InclusionNode.render` — `custom.py` registers no inclusion tag and the
    # corpus never renders a library node, because the harness parses rather than
    # renders.
    #
    # **Clause (4) is satisfied and this entry is what keeps it so**: the
    # declared region in this file is `(1, 2)`, the marker, which intersects
    # nothing below `:27` — and the re-measurement row now protects the whole
    # registration surface rather than an empty set, because a corpus row that
    # stopped loading `custom` would move the pin and
    # `test_the_compared_path_is_the_measured_one` would fail in the commit that
    # made it.
    "library.py": [
        (27, 29, "Library.__init__"),
        (31, 53, "Library.tag"),
        (55, 57, "Library.tag_function"),
        (59, 101, "Library.filter"),
        (103, 104, "Library.filter_function"),
        (106, 159, "Library.simple_tag"),
        (161, 243, "Library.simple_block_tag"),
        (304, 308, "TagHelperNode.__init__"),
        (321, 323, "SimpleNode.__init__"),
        (337, 339, "SimpleBlockNode.__init__"),
        (390, 475, "parse_bits"),
        (478, 494, "import_library"),
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
            # The rewritten docstring — see the module docstring's own reason and
            # design spec §6. One region, because it is one decision, even though
            # `difflib` splits it into five: our prose happens to share blank
            # lines with upstream's, so the matcher pairs them and reports the
            # block as alternating replacements and equal runs. A declaration
            # per opcode would be five numbers for one edit. Ours 1-21 against
            # upstream 1-39 is the tightest span that holds all five: our closing
            # `"""` on 22 matches upstream's on 40, and the blank after it matches
            # too. No executable line in ours — the whole span is inside the
            # docstring, which `executable_lines` excludes — so the snippet is
            # clause (5)'s required note, not a list of statements.
            ((1, 21), (1, 39), '"""The Django Template Language, as this package implements it.'),
            # **The `engines` registry is absent, and this is the region M18.1
            # rewrote from a two-line guard into twelve lines of prose.** Until
            # then ours read `try: from .utils import EngineHandler / except
            # ImportError: pass` and *bound* nothing — the guard existed to keep
            # the module importable under both names. There is no second name
            # now, no `utils` to import, and no handler: upstream's singleton
            # consults `settings.TEMPLATES`, which is what puts our backend in
            # front of a project, and it is Django's object that
            # `django.test.signals.reset_template_engines` and the three
            # `django.template.autoreload` receivers name. A handler built here
            # would be a registry nothing resets — measured, as
            # `test_response.TemplateResponseTest.test_render` reading a backend
            # built from the settings a class-level `override_settings` had
            # already replaced. Clause (5) wants the note: 12 of our lines
            # against one of upstream's, and the line that went is executable, so
            # the shape is not readable off the diff.
            (
                (28, 39),
                (47, 47),
                "no `engines = EngineHandler()` here: upstream's own registry is "
                "what resolves TEMPLATES[0]['BACKEND'], and a second handler "
                "would be a registry no receiver resets",
            ),
            # `engines` leaves `__all__` with it. One line for one line, so the
            # diff is readable and no note is needed; the snippet is our line.
            ((41, 41), (49, 49), '__all__ = ("Engine",)'),
            # M18.1's: `from .utils import EngineHandler` is upstream's line and
            # ours has no counterpart — a pure deletion, hence `None` on our
            # side. The module it names is not in this tree; see the note on the
            # `engines` region above, and `tests/milestone_ownership.py` for the
            # declaration that says so in the ownership table.
            (
                None,
                (45, 45),
                "upstream's `from .utils import EngineHandler` has no counterpart here: "
                "this package ships no handler and no `utils` module",
            ),
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
                (47, 49),
                (55, 57),
                "from .base import VariableDoesNotExist\n"
                "from .context import Context, ContextPopException, RequestContext\n"
                "from .exceptions import TemplateDoesNotExist, TemplateSyntaxError",
            ),
            ((52, 52), (60, 60), "from .base import ("),
            ((62, 62), (70, 70), "from .library import Library"),
            # Upstream imports its `autoreload` sibling here, and this file does
            # not, and nothing is lost. `django/template/autoreload.py` is not the
            # engine: it is three `django.utils.autoreload` receivers that walk
            # `engines.all()`, keep the `DjangoTemplates` instances and reset
            # their loaders when a template file changes. It is Django's view of
            # a *project's* configuration, declared `NOT_THE_ENGINE`, and there is
            # no module here to transcribe in its place.
            #
            # The receivers are registered by Django's **own**
            # `django/template/__init__.py`, which a project imports whether or
            # not it uses this package — `django.template.loader` imports it, and
            # Django's forms, admin and test utilities all import that. They still
            # cover this package's backend, because `RustTemplates` subclasses
            # `DjangoTemplates` precisely so that `isinstance` holds; that
            # subclass is a marker, not an inheritance, and this is one of two
            # places the marker buys something. Measured rather than reasoned:
            # `test_autoreloader` is green with this import absent, including the
            # two rows that assert `reset_loaders()` reaches a configured
            # backend's loaders. Clause (5) wants the note: 18 of our lines
            # against 2 of upstream's, and the second of those two is the import
            # that went.
            (
                (64, 81),
                (72, 73),
                "no `from . import autoreload`: Django's own `django/template/__init__.py` "
                "registers those receivers, and they name our backend because "
                "`RustTemplates` subclasses `DjangoTemplates`",
            ),
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
            # M18.1's, and the one edit in this file that the rename forced
            # rather than chose: upstream's `from django.template.context import
            # BaseContext` becomes `from .context import BaseContext`. **The
            # binding changes, not just the route** — and it has to. `Node.render`
            # and `Template` are transcribed here and are handed a `Context` the
            # caller built; if this file reached Django's `BaseContext` while our
            # `context.py` defines its own, then `Context` would subclass Django's
            # and every `isinstance(context, BaseContext)` in this file would be
            # testing an ancestry that no longer holds. One line for one line, so
            # no note is needed under clause (5); the snippet is our line.
            ((61, 61), (59, 59), "from .context import BaseContext"),
            # M16's module-level `_native` binding, ours only — a pure
            # insertion, `None` upstream. **This region is the one that changes
            # this file's diff shape on purpose, and the reason is the
            # measurement, not a style preference.** Before M16 the two seam
            # functions each carried `from django_rust_templates import _native`
            # *inside the function body*, so the import ran once per
            # `FilterExpression.resolve` and once per `Variable._resolve_lookup`
            # — 201 times for the benchmark's 50-row page, at ~0.25µs a call.
            # Hoisting it here removes all 201 and is worth **~50µs per render**
            # (1691 → 1641µs, `.review/m16-measurements/`). That is exactly the
            # `sys.modules` hit the M11 region's own note refused to pay a
            # region *split* for, and the distinction the note turns on survives
            # here: M11's move was *within* the function, saving `(n-1)` hits
            # for an `n`-filter expression and so nothing at all for the common
            # one-filter case, while this one is 201 per render and is the
            # standard placement rather than a rearrangement. The cost is
            # recorded so it is not discovered as a surprise: the two
            # `_resolve_lookup` regions below merge into one, the
            # `FilterExpression` region loses its first line, and every span
            # from here down shifts by one — the region count is unchanged at
            # six, which is the number the note says is the review surface.
            ((73, 73), None, "from django_rust_templates import _native"),
            # M19's slot, and the only line in this class that is not upstream's.
            # `FilterExpression` declares `__slots__`, so a per-expression cache
            # has to be named here or there is nowhere to put it — a module-level
            # dict keyed on the object would be a weak table plus a lookup, for a
            # value that belongs on the object. One line for one line (upstream's
            # 737 carries the four names), so clause (5) is satisfied by the shape
            # alone and the snippet is our line.
            #
            # **The region clears clause (4) by two lines, and that margin is the
            # whole reason the cache is filled where it is.** The compared path's
            # entry for `FilterExpression.__init__` is `(745, 789)`; `__slots__` is
            # above it, so this line is declared and legal, while a line *inside*
            # `__init__` would make the parity comparison Rust against Rust. That
            # is what forced the lazy fill below, and it is why the two entries are
            # in this file together.
            (
                (743, 743),
                (737, 737),
                '__slots__ = ("token", "filters", "var", "is_var", "_rust_names")',
            ),
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
                (188, 202),
                (185, 196),
                "from django_rust_templates.render import parse_template",
            ),
            (
                (205, 207),
                (199, 201),
                "parsed = parse_template(self.source, self.engine, self.origin)\n"
                "self.extra_data = parsed.extra_data\n"
                "return parsed.nodelist",
            ),
            # M11 Task 2's filter seam: `FilterExpression.resolve` gains one
            # branch, and M19 makes the answer it branches on per *expression*
            # rather than per *application*. Both halves are **pure insertions** —
            # upstream has no counterpart on these lines, hence `None` on the
            # upstream side and snippets that name statements rather than pairing
            # them.
            #
            # **Fifteen executable lines in the arm below**: the import that
            # reaches the shell (a parenthesised `import`, so `executable_lines`
            # counts its four lines rather than its one statement — that
            # granularity is what the guard compares), the identity test against
            # the name table, the two flag reads, the call with its five arguments,
            # and the `continue`. It was sixteen until M19: the import used to
            # reach `django_own_filters` as well, and the arm no longer asks that
            # question. (It was seventeen before M16 moved the first line —
            # `from django_rust_templates import _native` — to the module-level
            # region above; see that region's note.)
            #
            # **M19's second region, and the reason the seam has two where M11's
            # note below insists on one.** The fill has to run *before* the loop
            # over `self.filters` — inside it, it is per application again, which
            # is the cost M19 exists to remove — while the arm has to run *inside*
            # that loop, because it consumes the `arg_vals` upstream's verbatim
            # six lines build. Upstream's lines therefore sit between our two, and
            # `difflib` reports the gap as what it is. **The split is forced by
            # where the two statements must run, not by where they were typed**,
            # which is the distinction the M11 note turns on; the alternative kept
            # the region count down at the cost of a host helper that mutates the
            # expression it is asked about (`_rust_names_of(self)`, which would
            # also put a Python call back on the per-application path this change
            # removes), and the mutation is better here, where a reader of the
            # diff can see it.
            #
            # `host.django_own_filter_names` carries the argument for the shape,
            # the measurement (**1.074 us** a call, 65 calls in `real-world/normal`
            # = 69.8 us of a 620.8 us render, 11.2%), and what the re-reading gives
            # up. The `try`/`except AttributeError` is the unset slot rather than an
            # error: `FilterExpression` has no `__dict__`, and there are two
            # construction routes — `__init__` (ours 745) and the descriptor
            # materialiser (`engine/parser.py`), which builds the object with
            # `__new__` and never calls `__init__`. Filling it in `__init__` is what
            # clause (4) forbids (that span is on the compared path) and filling it
            # only in the materialiser would leave every `Parser.compile_filter`
            # expression unset, so one lazy fill covers both routes at one
            # `AttributeError` per expression.
            #
            # **M11's note on the single region, kept for the decision it records.**
            # The `host` import sat inside the loop above the identity test; a
            # reviewer's NIT moved it above the `for` — once per `resolve`
            # instead of once per filter, which no test can observe — and that
            # split this region in two, because upstream's verbatim `arg_vals`
            # loop then sits between the import
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
            # M19 pays that tax, and the amount is why: the reverted move saved
            # `(n-1)` `sys.modules` hits for an `n`-filter expression and so
            # **nothing at all** for the common one-filter case, while this one
            # saves a 1.074 us call per filter application — 65 of them on one
            # render. The test the note implies is not "is the region count one"
            # but "is the second region bought by something the first cannot
            # express"; M19's is, because the two statements need different
            # scopes, and the reverted NIT's was not.
            #
            # **The prologue is guarded by `if self.filters:`, and the guard is a
            # measurement rather than a tidy-up.** The fill is a *one-off* cost
            # per expression, and `parse` mode is where one-off stops being once:
            # it compiles a fresh template every iteration, so every
            # `FilterExpression` is built again and the prologue is paid again.
            # Measured that way, and before the guard, `real-world/large` — the
            # row with no filters in it at all, ~300 expressions over 62
            # iterations — went **2900 us → 3114/3469 us, ratio 1.353 → 1.527**,
            # while its *render* row, the same template compiled once, did not
            # move at all. That pair is the signature of a per-compile cost, and
            # this was the one row where the change lost; `micro/variable` parse
            # (+5.7%) is the same thing at one expression. With the guard a
            # filter-free expression pays a truth test and nothing else, which is
            # what it paid before M19. **The residual is stated rather than
            # hidden**: a *one-filter* expression in `parse` mode is still
            # slightly slower (`micro/filter` parse, +3.6%), because the fill's
            # import and `AttributeError` cost about what the one
            # `django_own_filters()` call they replace did. The win is on the hot
            # path, where the answer is kept, and on expressions carrying more
            # than one filter.
            (
                (809, 839),
                None,
                "if self.filters:\n"
                "try:\n"
                "names = self._rust_names\n"
                "from django_rust_templates.host import django_own_filter_names\n"
                "names = self._rust_names = django_own_filter_names(self.filters)",
            ),
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
            # holds the 30 names this guard actually compares against the pin;
            # `FilterExpression.resolve` is not among them — the compared path is
            # the *parse* harness — so clause (4) permits this region without
            # examining it, and clause (2) is the only thing standing between
            # these fifteen lines and nothing at all. What the guard therefore
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
                (847, 879),
                None,
                "from django_rust_templates.host import (\n"
                "effective_autoescape,\n"
                "effective_use_tz,\n"
                ")\n"
                "if (builtin_name := names.get(func)) is not None:\n"
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
            # **One region, where M9 declared two, and M16 is why.** `difflib`
            # used to pair our comment block against upstream's loop (one
            # opcode) and our `return` against upstream's `if getattr(e,
            # "silent_variable_failure", …)` tail (another), with one equal
            # blank line between them keeping the two hunks apart. M16 removed
            # the in-function `from django_rust_templates import _native` and
            # the blank line that followed it, so the two hunks now abut and
            # `difflib` reports them as one — our 1027-1073 against upstream's
            # 957-1028, the whole body replaced by a comment block and a call.
            # (The span read 995-1033 until M19 lengthened the comment block to
            # record the `2.` divergence, and 995-1047 while the shell still
            # re-joined the path; M19's step 1 then shortened the block by six
            # lines, because the join is no longer this method's input and the
            # paragraph explaining the join's inexactness is gone with it, and
            # M19's routing cache added 24 lines *above* this region without
            # touching it, which is why the span read 1019-1065, and the
            # `if self.filters:` guard added eight more at the same place, which
            # is why it reads 1027-1073.) The
            # upstream side has not moved in any of it, because the *pairing* is
            # unaffected by how much prose our side carries. Re-derived with the
            # checker's own `differing_lines`, not by adding a delta to the old
            # number: `.review/m19-measurements/rederive_base_regions.py`.)
            # That is a *merge* rather than a split, so it is the direction the
            # M11 note calls a tax reduction rather than a tax; and the region
            # count stood at six through M16's merge — two `compile_nodelist`,
            # the marker, the module-level import, this file's filter seam, and
            # this one. **It is nine from M19**, and the count is enumerated
            # rather than asserted because it moved twice in one milestone: the
            # marker, the `BaseContext` import (M18.1), the module-level `_native`
            # import (M16), `__slots__` (M19), two `compile_nodelist`, the
            # routing cache (M19), this file's filter seam, and this one. The
            # M11 note below is where the second of M19's two is argued for.
            # The upstream span now reaches 1028 because it carries the
            # handler's body *and* the `return current` tail, which is where the
            # alignment landed rather than where the code moved to — stated
            # because a reader comparing the two spans will otherwise assume the
            # pairing is meaningful.
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
                (1027, 1073),
                (957, 1028),
                'return _native.resolve_lookups(self.lookups, context)',
            ),
            # **M18.1's seventh region was here, and it was removed on the day it
            # was written** — the reason is the region count rather than the line.
            # It declared `base.py`'s `from django.template.context import
            # BaseContext` rewritten to `from .context import BaseContext`, which
            # is what makes `engine` resolve to itself once it is a plain package:
            # the absolute spelling imports Django's real module and hands back a
            # second `BaseContext` class, so the rewrite is not optional in the end
            # state. But it is one *systematic* difference with one reason, shared
            # by every module in this directory, and declaring it one module at a
            # time is 22 regions saying the same sentence — the "written twice, one
            # of them is wrong" tax this file's own notes refuse to pay. It belongs
            # in the guard as a single normalization of the compared package prefix
            # (task #23), and until that lands the absolute import stays and this
            # file's diff keeps its pre-M18.1 shape.
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
    # **`utils.py` had an entry here until the M18 rewiring and its deletion is
    # a decision, not a gap.** The module held `EngineHandler` and
    # `get_app_template_dirs`, and the file was removed in `03038e6`: the handler
    # is Django's own registry, which resolves `TEMPLATES[0]["BACKEND"]` for our
    # backend as for any other, and `app_directories` now imports Django's
    # `get_app_template_dirs` directly. The name is on
    # `tests/milestone_ownership.py`'s `NOT_THE_ENGINE`, and the key set here is
    # asserted equal to that table's scope by
    # `tests/test_milestone_ownership.py`, so a key left behind would be a
    # declaration about a file that does not exist — which is exactly what this
    # guard reported, as `declared but not present`, until the key went.
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
            # The marker, which sits on the line upstream's `import functools`
            # occupied — one line against one, so the shape is readable and the
            # snippet is `None` (clause 2: a region with no executable line and
            # an unchanged shape declares nothing and must not pretend to).
            #
            # **That the two lines collided is worth stating rather than hiding
            # behind the marker's usual `None` upstream.** This file no longer
            # imports `functools` because `Engine.get_default` no longer caches —
            # see the region at ours 91-110 and the deleted `@functools.lru_cache`
            # at upstream 88, both below. So the marker did not displace an import
            # that is still in use; it landed where a now-unused one used to be,
            # and `difflib` paired them because both are single lines at the top
            # of the file.
            ((1, 1), (1, 1), None),
            # M18.1's: the three built-in libraries named by this package's dotted
            # path rather than Django's. The strings are data, not code — the class
            # body that holds them is one statement starting at line 14 — but
            # `executable_lines` marks the whole `Assign`, so the three lines the
            # region covers are all executable and the snippet names all three.
            (
                (15, 17),
                (15, 17),
                '"django_rust_templates.engine.defaulttags",\n'
                '"django_rust_templates.engine.defaultfilters",\n'
                '"django_rust_templates.engine.loader_tags",',
            ),
            # M18.1's: the default loader path, one line for one line.
            (
                (38, 38),
                (38, 38),
                'loaders = ["django_rust_templates.engine.loaders.filesystem.Loader"]',
            ),
            # M18.1's, and this one is a shape change rather than a rename:
            # upstream adds one loader and then wraps the list in one entry, while
            # ours takes six lines to do the same thing — the `app_directories`
            # append was reflowed to three lines by the formatter once the string
            # grew past the line limit, and so was the `cached` pair. Six of our
            # lines against two of upstream's, so clause (5) wants the note, and
            # all six lines are executable (two `Assign`s spanning three lines
            # each), so the snippet names all six.
            (
                (40, 45),
                (40, 41),
                "loaders += [\n"
                '    "django_rust_templates.engine.loaders.app_directories.Loader"\n'
                "]\n"
                "loaders = [\n"
                '    ("django_rust_templates.engine.loaders.cached.Loader", loaders)\n'
                "]",
            ),
            # **`Engine.get_default` is deliberately not cached, and this region
            # is the argument for it.** Upstream caches on a function that reads
            # `settings.TEMPLATES` through the engine registry, and can, because
            # Django's own `django.test.signals.reset_template_engines` receiver
            # calls `django.template.engine.Engine.get_default.cache_clear()` on
            # every `TEMPLATES`, `DEBUG` or `INSTALLED_APPS` change. That receiver
            # names *Django's* class object; this package's `Engine` is a
            # different class object, so a cache here is one nothing can ever
            # invalidate — it would hold the settings from the first call for the
            # life of the process. Measured, as three red rows in
            # `test_engine.GetDefaultTests`: with `override_settings(TEMPLATES=[...])`
            # in force, `get_default()` returned the engine built from the
            # *unoverridden* settings, and `test_no_engines_configured` could not
            # raise because a previous answer was already memoised. The work it
            # saves is a dict lookup per configured backend against a registry
            # that caches the backends themselves. Clause (5) wants the note: 20
            # of our lines against none of upstream's, and the lines are prose.
            (
                (91, 110),
                None,
                "no `@functools.lru_cache` on `Engine.get_default`: its answer is a "
                "function of `settings.TEMPLATES`, and the receiver that clears "
                "Django's cache names Django's class object, not this one",
            ),
            # The other half of that edit, and the half a reader meets first:
            # upstream's decorator is gone from ours. A pure deletion — `None` on
            # our side — so clause (5)'s note is required and there are no
            # executable lines of ours for the snippet to name.
            (
                None,
                (88, 88),
                "upstream's `@functools.lru_cache` on `Engine.get_default` is deleted "
                "with the cache it decorates; see the region at ours 91-110",
            ),
            # M18's second arm. Upstream's `get_default` looks for `DjangoTemplates`
            # and nothing else, because upstream ships one backend; this package
            # ships a second, and a caller who configured *this* one and then used
            # the historical API would otherwise be handed stock Django's engine,
            # or nothing. The import is the executable line the snippet names.
            (
                (132, 142),
                None,
                "from django_rust_templates.rust import RustTemplates",
            ),
            # The widened test itself, one line for one line.
            (
                (144, 144),
                (110, 110),
                "if isinstance(engine, (DjangoTemplates, RustTemplates)):",
            ),
            # `ImproperlyConfigured`'s message stays upstream's wording, and this
            # note is the reason: `template_tests.test_engine` asserts the exact
            # string, so naming both backends in it would buy a clearer message at
            # the price of a red acceptance run. It is also reachable only when
            # *neither* backend is configured, which is the case it describes.
            # Clause (5) wants the note: five of our lines against none of
            # upstream's, all prose.
            (
                (146, 150),
                None,
                "the message on `ImproperlyConfigured` stays upstream's wording — "
                "`template_tests.test_engine` asserts the exact string",
            ),
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
    # rewrites its module docstring**, unlike `engine/__init__.py`, which does. A
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
            # **This region absorbed the marker's**, and that is a measurement
            # rather than a tidy-up. Before M18.1 the marker was a region of its
            # own, `((1, 2), None, …)`; rewriting line 3's import leaves no equal
            # line between the inserted marker and the changed import, so
            # `difflib` reports ours 1-3 against upstream's line 1 as one hunk and
            # the old declaration went *stale*. The shape changed (3 of ours
            # against 1 of upstream's), so this is a note as well as a snippet —
            # clause (5) — and it names the region's only executable line.
            (
                (1, 3),
                (1, 1),
                "# TRANSCRIBED: django/template/loaders/base.py (Django 6.0.9)\n"
                "from .. import Template, TemplateDoesNotExist",
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
            # M18.1's — the sibling import, so `engine` resolves to itself as a
            # plain package. One line for one line, so every span above stands.
            ((8, 8), (6, 6), "from .. import Origin, TemplateDoesNotExist"),
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
            # M18.1's, and it *removed* a deviation rather than adding one. Until
            # then ours read `from ..utils import get_app_template_dirs` and this
            # package transcribed that function; the rewiring deleted `utils`
            # along with the handler, so the import is now upstream's own line and
            # the only thing left on ours is the note explaining why. **That is
            # the right way round and worth saying, because the function's identity
            # is load-bearing**: it caches its answer and the cache is invalidated
            # by `django.test.signals.update_installed_apps`, which clears
            # `django.template.utils.get_app_template_dirs`. A second function
            # object holds a second cache that receiver cannot reach — measured,
            # ours returned `[django_suite/template_tests/templates]` under
            # `override_settings(INSTALLED_APPS=[])` while Django's returned `[]`,
            # which is the whole of `test_loaders.AppDirectoriesLoaderTests.
            # test_not_installed`. A pure insertion with no executable line of ours
            # (nine lines of prose), so clause (5) wants the note.
            (
                (8, 16),
                None,
                "import Django's `get_app_template_dirs` rather than a copy of it: its "
                "cache is cleared by `update_installed_apps`, and a second function "
                "object would hold a second cache that receiver cannot reach",
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
            # M18.1's. **The neighbouring line 11 is deliberately unchanged**:
            # `from django.template.backends.django import copy_exception` names
            # Django's backend layer, which `engine` has no counterpart for and
            # should not — `copy_exception` is an ordinary function with no
            # identity requirement, and upstream's spelling is correct in both
            # modes. This is the one `django.template.*` import in `engine` that is
            # not rewritten, and it is not an oversight.
            ((10, 10), (8, 8), "from .. import TemplateDoesNotExist"),
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
            # M18.1's — the sibling import, as in the three files above.
            ((7, 7), (5, 5), "from .. import Origin, TemplateDoesNotExist"),
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
            # M18.1's — the engine package by its stable name. `Library`, `Node`,
            # `VariableDoesNotExist` and `TemplateSyntaxError` are four of the
            # names `engine/__init__.py` re-exports, and `engine/__init__.py` binds
            # `django_rust_templates.engine` to itself, so this is the same object
            # `from django.template import …` gives under the finder.
            #
            # **Not a relative import, and that is the measurement rather than a
            # preference.** This file is served as `django.templatetags.cache`, a
            # sibling of `django.template`, so `..` is `django` — `from .. import
            # Library` asks `django` for a name it has never had and raises
            # `ImportError: cannot import name 'Library' from 'django'`. The
            # relative spelling that would work under the finder (`..template`) is
            # the wrong one standalone, where the engine package is `..` itself.
            # No relative form is correct in both, so this one is absolute.
            (
                (5, 5),
                (3, 3),
                "from django_rust_templates.engine import Library, Node, "
                "TemplateSyntaxError, VariableDoesNotExist",
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
            # M18.1's, and the only import rewrite in the tree that changes what
            # is bound rather than where it is looked up. Upstream's `from django
            # import template` binds the *package*; this binds this package's own
            # engine, which `engine/__init__.py` re-exports the same three names
            # from, so the line is one-for-one and **the seven `template.X` uses in
            # the body below are untouched** — which is the whole point, since the
            # body's claim is that it is upstream's.
            #
            # **Eight of our lines against one of upstream's, and the seven extra
            # are the note above the import.** The region was declared as the
            # single line for as long as the note did not exist; the note is what
            # moved it, so the span is the note plus the line it explains and
            # clause (5) wants a snippet as well as the note. Only line 12 is
            # executable — an `ImportFrom` — and the snippet is that line verbatim,
            # not the paraphrase this declaration used to carry: the guard compares
            # the snippet against the source, and a snippet written from memory
            # named a line that is not in the file.
            (
                (5, 12),
                (3, 3),
                "from django_rust_templates import engine as template",
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
            # M18.1's — the stable-name import, as in `cache.py` above.
            ((6, 6), (4, 4),
             "from django_rust_templates.engine import Library, Node, TemplateSyntaxError"),
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
            # M18.1's, and the same absorption `loaders/base.py` records: the
            # import on line 3 directly follows the marker with no equal line
            # between them, so `difflib` reports ours 1-3 against upstream's line
            # 1 as one hunk and the marker's old `((1, 2), None, …)` declaration
            # went stale. Shape changed (3 against 1), so this is clause (5)'s
            # note as well as clause (2)'s snippet.
            (
                (1, 3),
                (1, 1),
                "# TRANSCRIBED: django/templatetags/l10n.py (Django 6.0.9)\n"
                "from django_rust_templates.engine import Library, Node, "
                "TemplateSyntaxError",
            ),
        ],
    ),
    # M12 Task 6a's, the fifth and last of the five and the same shape: the pin
    # plus a two-line marker, `None` on the upstream side, nothing else. The
    # module opens with imports (`i18n.py:1-8`), so the marker is a comment on
    # line 1 and the region is `(1, 2)`.
    #
    # Verbatim is right for all eight tag bodies although Rust compiles their
    # names: `has_tag` answers False for a Rust-owned name the template loaded,
    # so `do_get_available_languages`, `do_get_language_info`,
    # `do_get_language_info_list`, `do_get_current_language`,
    # `do_get_current_language_bidi`, `do_translate` and `language` are bodies
    # nothing calls — the same case `defaulttags.py`, `tz.py` and `l10n.py` are
    # kept verbatim for. The two names Rust does **not** own yet
    # (`blocktranslate`/`blocktrans`, which are Task 6b's) *are* reached through
    # this file, and they are transcribed here for that reason rather than for
    # the unreachability one — the file is the unit of transcription.
    #
    # **Every node's render stays Python's, and the marker's meaning is why that
    # is a transcription rather than a delegation:** each `render` makes the
    # `django.utils.translation` call (`i18n.py:19,31,44,59,68,169,177,213`),
    # and Rust owns only the *compile* functions. The four filters
    # (`language_name`, `language_name_translated`, `language_name_local`,
    # `language_bidi`, `i18n.py:293,298,304,309`) are Task 7's Rust bodies and are
    # transcribed-verbatim here for the reason `tz.py`'s and `l10n.py`'s are: a
    # per-file table cannot express a half-transcribed module. So nothing in this
    # file delegates to Rust and the generated marker says the upstream path and
    # the pin and nothing more, as every marker in this table does.
    "templatetags/i18n.py": (
        "django/templatetags/i18n.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/i18n.py (Django 6.0.9)",
            ),
            # M18.1's — three engine-internal imports on three consecutive lines,
            # so `difflib` reports them as one hunk and one declaration covers all
            # three. All three name the engine package by its stable name rather
            # than relatively, for the `..`-is-`django` reason `cache.py` records.
            #
            # **`base` is the one that must not be a second copy.** A duplicate of
            # `defaulttags` costs nothing — it is a leaf whose own imports resolve
            # through the finder — but a duplicate of `base` defines a second
            # `TokenType`, and line 5 of this file is where `token.token_type` is
            # compared against it: the comparison would be False for every token,
            # silently. `engine/__init__.py` aliases every sibling it has already
            # imported, which is where `base` arrives from.
            (
                (6, 8),
                (4, 6),
                "from django_rust_templates.engine import Library, Node, "
                "TemplateSyntaxError, Variable\n"
                "from django_rust_templates.engine.base import TokenType, "
                "render_value_in_context\n"
                "from django_rust_templates.engine.defaulttags import token_kwargs",
            ),
        ],
    ),
    # M13's, and the last entry in either table. The pin opens with
    # `from collections.abc import Iterable`, so there is no docstring to carry
    # the marker and it is a comment on line 1 — the `utils.py`/`smartif.py`
    # form. Nothing else differs from the pin: `cat` produced the file and the
    # diff below the marker is empty.
    "library.py": (
        "django/template/library.py",
        [
            # The marker and its blank separator, ours only — a pure insertion
            # (`None` upstream). Upstream has no docstring here, so the marker is
            # a comment on line 1 and the region is `(1, 2)`.
            #
            # **The file is verbatim, and here that claim is load-bearing in a
            # way it is not for M11's three.** `library.py`'s classes are
            # observed by *identity*: the suite's `assertRaises(
            # InvalidTemplateLibrary)` and `isinstance(lib, Library)` rows are
            # about the object, and `engine/__init__.py:54`, `defaulttags.py:44`,
            # `defaultfilters.py:30`, `loader_tags.py:9` and `engine.py:12` all
            # bind these names at import. A re-export would satisfy every
            # behavioural row and leave five modules holding Django's objects —
            # measured in `.review/m13-recon.md` §7, where a module-attribute
            # patch on `import_library` read `0` while the bound name read
            # `11050`.
            ((1, 2), None, "# TRANSCRIBED: django/template/library.py (Django 6.0.9)"),
        ],
    ),
    # **M18.1's row was here, and M18.2 removed it the same day.** It was written
    # because `engine/__init__.py` carries upstream's `from . import autoreload`
    # and the sibling only existed through the checkout's `__path__` entry, which
    # a plain package does not have — so `import django_rust_templates.engine`
    # raised `ImportError: cannot import name 'autoreload'`. Transcribing the file
    # was the fix that needed no exception.
    #
    # The exception is the better fix, and the reason is the one the ownership
    # table already gives: `autoreload` is declared `NOT_THE_ENGINE` because
    # upstream's module registers receivers over `engines.all()` and keeps only
    # `DjangoTemplates` backends — it resets *Django's* loaders, and every line of
    # it belongs to a subsystem this package does not own. A transcription is a
    # promise to keep a Django file faithful across releases; making that promise
    # about a file whose whole job is another framework's dev-server plumbing
    # bought 63 lines of maintenance for an `ImportError` in a mode that has no
    # such sibling. Guarding the import costs four lines in `__init__.py` — a
    # region this file already declares — and it is the *same* four lines that
    # have to exist once the shim is gone. Two rows and a file, replaced by one
    # region.
    #
    # Nothing observable changed in the mode this tree runs in today: under the
    # finder the relative import still resolves to upstream's file and all three
    # receivers are registered exactly as before. `tests/milestone_ownership.py`
    # is what noticed the transposed file in the first place, and now says so
    # where the name is declared.
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
    """The dotted import path of this package's module for transcription ``name``.

    Derived from ``name`` — the ``engine``-relative posix path every table here is
    keyed by — rather than from the declared upstream path, so the two cannot
    disagree: ``loaders/base.py`` is ``django_rust_templates.engine.loaders.base``,
    and ``__init__.py`` is the package itself, whose last segment the filename
    does not name.

    **It was derived from the upstream path until the M18 rewiring, and that
    spelling stopped being true when the shim left.** It used to answer
    ``django.template.base``: the shim loaded our file under Django's name, so
    importing that path got ours, and "what the parity harness executes" was a
    measurement of what the harness actually ran. There is no second name now.
    Left as it was, it would have answered with *Django's* modules, and a row
    that measures the compared path against the checkout's copy of a file passes
    for the wrong module whenever the two happen to have the same function
    names — which is every transcription, by construction.
    """
    if name.endswith("/__init__.py"):
        name = name[: -len("/__init__.py")]
    elif name.endswith(".py"):
        name = name[: -len(".py")]
    stem = name.replace("/", ".")
    return "django_rust_templates.engine" + (f".{stem}" if stem else "")


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


def _namespace_only(ours, ours_span, theirs, theirs_span) -> bool:
    """Is a region's whole divergence the engine's own namespace?

    **Clause (4) exists so the parity harness cannot certify code the
    transcription has already declared divergent** — the harness compares Rust's
    parse against the *transcription's* parse, and `assert_target_is_not_rust_owned`
    admits a declared transcription as a target only because this guard has
    already checked it against Django line by line. A divergence inside a function
    the harness executes breaks the second link of that chain, and clause (4)
    refuses it.

    The M18 rename is the one thing that makes that unsatisfiable at two lines of
    ``engine.py``. Every dotted path a transcription *names* has to be this
    package's rather than Django's — `default_builtins` names our three libraries,
    `Engine.__init__` names our three loaders, `get_default` names our backend —
    and those are not choices: the engine is a different package and its loaders
    live at different names. Two of them fall inside `Engine.__init__` (ours
    20-67), which the harness executes to obtain `template_builtins`; `difflib`
    reports them as ours 38 and ours 40-45. Refusing them leaves the guard with no
    legal way to describe the file it is reading, and deleting the clause opens
    the hole it was written against.

    So the exception is drawn exactly as narrowly as the architecture forces,
    and it is a **rule rather than a waiver**: permitted only when the whole of
    the region is upstream's text with `django.template.` replaced by
    `django_rust_templates.engine.` — whitespace removed, so the formatter's
    reflow of a now-longer string literal does not count as a difference. Every
    token has to match, so a reordered keyword, a changed condition or one new
    statement anywhere in the region fails it.

    **Both constants are the control, and they are checked rather than assumed.**
    The substitution is only offered when our span actually says
    `OURS_NAMESPACE` and upstream's actually says `UPSTREAM_NAMESPACE`; a region
    that names neither — which is every region carrying a *decision* rather than a
    *name* — cannot reach the exception however its text reads. The scenario
    clause (4) was written for, a region on `FilterExpression.args_check`, still
    fails, and `tests/test_compared_path_instrument.py` drives both directions.
    """
    if ours_span is None or theirs_span is None:
        return False
    mine = "\n".join(ours[ours_span[0] - 1 : ours_span[1]])
    upstream = "\n".join(theirs[theirs_span[0] - 1 : theirs_span[1]])
    if OURS_NAMESPACE not in mine or UPSTREAM_NAMESPACE not in upstream:
        return False
    squash = lambda text: "".join(text.split())  # noqa: E731 — one expression
    return squash(mine) == squash(upstream.replace(UPSTREAM_NAMESPACE, OURS_NAMESPACE))


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
    # `engine/smartif.py` on disk and absent from `DECLARED`, because the only
    # files it can name are the ones it was told about.
    #
    # Equality rather than containment for the same reason the `COMPARED_PATH`
    # clause is: a declared name with no file is a claim about nothing (caught
    # below as "declared but not present"), and a marked file with no
    # declaration is a transcription that has not been read.
    # `rglob`, not `glob`, and `engine`-relative keys, not basenames. Both halves
    # matter and both were latent until M10: `glob("*.py")` cannot see
    # `loaders/base.py`, so a marker-bearing file in a subpackage is invisible to
    # the clause below `_check_marker` and to this loop — and `path.name` would
    # key it `base.py`, which is *another* transcription's key, so the entry it
    # looked up would be the wrong file's while every clause passed.
    #
    # `loaders/base.py` is also the spelling `milestone_ownership.shadowing_files()`
    # reports and the one `m10_measure_compared_path.py` prints `COMPARED_PATH` rows
    # in, so this is the fourth table agreeing with the other three rather than a
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
    # `engine/__init__.py` is declared and *is* marked; a declared file that is
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
        # above: a key is a posix path relative to `engine`, not one segment.
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
                if not overlaps(ours_span, first, last):
                    continue
                # The architecture-forced half of clause (4): a region whose whole
                # divergence is this package's namespace in place of Django's. A
                # region carrying a decision cannot reach it — see
                # `_namespace_only`, which is where the narrowness is argued and
                # what the two constants are the control for.
                if _namespace_only(ours, ours_span, theirs, theirs_span):
                    continue
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
