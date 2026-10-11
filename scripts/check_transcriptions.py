"""Every ``engine`` transcription differs from upstream only where it declares.

A ``# TRANSCRIBED:`` file is a claim about the pinned tree, and only the diff
makes it falsifiable — a verbatim copy and a wrongly transcribed copy both keep
the suite green. So each transcription declares where it differs, and this
script asserts:

1. every differing line falls inside a declared region, and no declared region
   is **stale** (covering nothing) — both directions are failures;
2. a region containing **executable** lines declares a snippet naming them, and
   the snippet accounts for every executable line in the region;
3. the ``# TRANSCRIBED:`` marker names this module's upstream path and the
   version of the checkout it was taken from;
4. no declared region intersects the code a parity harness **executes**
   (``COMPARED_PATH``) — otherwise the parity comparison is Rust against Rust.

**Regions, not exact line pairs:** a rewritten docstring re-aligns as one block
under ``difflib``, so a per-line table would be regenerated on every prose edit
rather than read.

Read-only: a transcription may contain line separators a ``splitlines()``/
``join()`` round-trip would destroy, so this script only ever splits.
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

# How far into the file the marker may be. `engine/__init__.py` puts a blank line
# before it, so the test is "inside the docstring", which four lines covers.
MARKER_WINDOW = 4

# This package's dotted root, and Django's. Every dotted path a transcription
# *names* — its libraries, loaders, backend — must carry ours, which is exactly
# the substitution clause (4)'s `_namespace_only` exception permits.
OURS_NAMESPACE = "django_rust_templates.engine."
UPSTREAM_NAMESPACE = "django.template."

# The functions a parity harness executes, with the line spans they occupy — measured
# by `.review/m9-measurements/m9_measure_parse_path.py`, which regenerates any entry.
# Pinned rather than re-measured per run (measuring parses ~566 templates); it cannot go
# stale quietly, because
# `tests/test_interposition.py::test_the_compared_path_is_the_measured_one` re-runs the
# instrument and fails if the two disagree.
#
# The numbers are the *transcription's*, so an insertion above a function moves its span:
# re-measure rather than add a delta. Spans cover nearly the whole file, so a guard
# checking one function's own lines would miss a region declared in any other.
COMPARED_PATH = {
    # Empty: no `__init__` is reachable from a parity harness. Present because the
    # guard asserts this key set equals `DECLARED`'s.
    "__init__.py": [],
    # `autoreload.py` is absent, not emptied: a key with an empty list still asserts
    # the module *is* a transcription, and this one is deliberately not.
    "base.py": [
        (118, 121, "Origin.__init__"),
        (335, 342, "PartialTemplate.__init__"),
        (393, 415, "Token.__init__"),
        (424, 437, "Token.split_contents"),
        (441, 443, "Lexer.__init__"),
        (452, 464, "Lexer.tokenize"),
        (466, 498, "Lexer.create_token"),
        (536, 558, "Parser.__init__"),
        (563, 625, "Parser.parse"),
        (627, 632, "Parser.skip_past"),
        (634, 651, "Parser.extend_nodelist"),
        (653, 664, "Parser.error"),
        (666, 682, "Parser.invalid_block_tag"),
        (693, 694, "Parser.next_token"),
        (696, 697, "Parser.prepend_token"),
        (699, 700, "Parser.delete_first_token"),
        (702, 704, "Parser.add_library"),
        (706, 710, "Parser.compile_filter"),
        (712, 716, "Parser.find_filter"),
        (773, 817, "FilterExpression.__init__"),
        (938, 955, "FilterExpression.args_check"),
        (987, 1038, "Variable.__init__"),
        (1040, 1056, "Variable.resolve"),
        (1161, 1173, "Node.get_nodes_by_type"),
        (1199, 1200, "NodeList.render"),
        (1202, 1207, "NodeList.get_nodes_by_type"),
        (1255, 1256, "TextNode.__init__"),
        (1264, 1271, "TextNode.render_annotated"),
        (1342, 1343, "VariableNode.__init__"),
        (1363, 1407, "token_kwargs"),
    ],
    # 8 functions, spans 56-214, same instrument and pin relation as `base.py`.
    # `smartif` builds its operator classes inside `infix()`/`prefix()`, binding them
    # into `OPERATORS`, so their methods were invisible until
    # `tests/compared_path.py::classes` looked one level into module-level containers;
    # the two top entries are what that found, listed by the factory's qualified name
    # because all classes one factory makes share its span.
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
    # 3 functions, spans 31-155 of 309. The parity harness is a *parsing* harness, so
    # the reach is not the render path but `defaulttags.py:1607`, where `do_verbatim`
    # renders its block at parse time with a fresh `Context()`. A declared region
    # anywhere else in these lines is Rust-against-Rust that nothing here would flag.
    "context.py": [
        (31, 32, "BaseContext.__init__"),
        (34, 40, "BaseContext._reset_dicts"),
        (146, 155, "Context.__init__"),
    ],
    # 3 functions, spans 22-129: `Engine.__init__` and the two lookups it makes
    # eagerly. The harness builds a fresh `Engine` per call, so the count is per call.
    # The two lookups are plain methods, not the `lru_cache`d pair, each listed under
    # its own span so the guard can name it.
    "engine.py": [
        (20, 67, "Engine.__init__"),
        (159, 160, "Engine.get_template_builtins"),
        (162, 166, "Engine.get_template_libraries"),
    ],
    # 0 each: no parity harness enters a loader — they are reached through
    # `Engine.get_template`, which no parity source calls. Present rather than absent
    # for the key-set-equality clause.
    "loaders/__init__.py": [],
    "loaders/base.py": [],
    "loaders/filesystem.py": [],
    "loaders/app_directories.py": [],
    "loaders/cached.py": [],
    "loaders/locmem.py": [],
    # The three `do_*` compile functions are on the compared path because the
    # *reference* parse runs them — upstream's `Parser.parse` calls
    # `parser.tags[command](parser, token)` — even though the engine compiles
    # `block`/`extends`/`include` host-side. A declared region cannot sit on code the
    # comparison runs, and a tag's compile function is where a transcription of it
    # would most plausibly hide one.
    "loader_tags.py": [
        (45, 48, "BlockNode.__init__"),
        (92, 96, "ExtendsNode.__init__"),
        (168, 174, "IncludeNode.__init__"),
        (216, 245, "do_block"),
        (248, 290, "construct_relative_path"),
        (293, 314, "do_extends"),
        (317, 372, "do_include"),
    ],
    # One function: the *decorator*, not any of the 59 filter bodies. It is reached
    # because `tests/template_tests/templatetags/custom.py` applies `@stringfilter` at
    # import time and the corpus's `{% load custom %}` triggers that import. The reach
    # is import-order sensitive — whichever instrument runs *second* reads 0, since
    # the decorator applies once per process — so the pin is the cold reading.
    "defaultfilters.py": [
        (40, 54, "stringfilter"),
    ],
    "defaulttags.py": [
        # Measured: 48 of the file's 82 functions. Everything reached is a tag the
        # harness's Django side owns, because that side is a bare `Parser` over this
        # transcription — `parser.tags[command]` is this file's function whether or not
        # the shell would route elsewhere. `load` is in it, `querystring` is not (no
        # corpus template uses it).
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
    # 0 each: a parity source reaches a library's tag bodies only by writing
    # `{% load <library> %}` first, and the corpus loads `template_tests.*` and never
    # `cache` or `static`. Present rather than absent for the key-set-equality clause,
    # kept honest by the re-measurement row.
    "templatetags/cache.py": [],
    "templatetags/static.py": [],
    # 0 of 12: the `{% load %}` rows reach `template_tests.*` and never
    # `django.templatetags.tz`, so all 12 are defined and simply not entered.
    "templatetags/tz.py": [],
    # 0 of 6, `tz.py`'s reason one library over.
    "templatetags/l10n.py": [],
    # 2 of 31. The `{% load i18n %}` rows reach `django.templatetags.i18n`, and the
    # `{% blocktranslate %}` rows run `do_block_translate` (compile) and
    # `BlockTranslateNode.__init__` (materialise) on both sides; the other 29 are
    # `tz.py`'s case.
    "templatetags/i18n.py": [
        (107, 127, "BlockTranslateNode.__init__"),
        (458, 598, "do_block_translate"),
    ],
    # Measured 12 of 18. The corpus holds `{% load custom %}` rows, and
    # `tests/template_tests/templatetags/custom.py` runs its `register =
    # template.Library()` and seven `@register.*` decorators at import time, so the
    # reach is a property of the harness's *setup*, the same shape as
    # `defaultfilters.py`'s `stringfilter`.
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
# A span is `(first, last)`, inclusive and 1-based; a one-line span is `(n, n)`;
# `None` says the region has **no lines at all** on that side (a pure insertion
# `theirs_span=None`, or a pure deletion `ours_span=None`). Declaring the upstream
# side is what stops the guard accepting a wholesale deletion: a function removed
# from ours shows up as upstream lines no region accounts for.
#
# The snippet is required exactly when the region contains executable lines and must
# contain each of them verbatim.
DECLARED = {
    "__init__.py": (
        "django/template/__init__.py",
        [
            # The rewritten docstring. One region because it is one decision, even
            # though `difflib` splits it into five (our prose shares blank lines with
            # upstream's). No executable line, so the snippet is clause (5)'s note.
            ((1, 21), (1, 39), '"""The Django Template Language, as this package implements it.'),
            # No `engines = EngineHandler()`: upstream's own registry resolves
            # `TEMPLATES[0]['BACKEND']`, and a second handler would be a registry no
            # receiver resets. Clause (5) wants the note: 12 of our lines against one of
            # upstream's, and the line that went is executable.
            (
                (28, 39),
                (47, 47),
                "no `engines = EngineHandler()` here: upstream's own registry is "
                "what resolves TEMPLATES[0]['BACKEND'], and a second handler "
                "would be a registry no receiver resets",
            ),
            # `engines` leaves `__all__` with it; one line for one line.
            ((41, 41), (49, 49), '__all__ = ("Engine",)'),
            # Upstream's `from .utils import EngineHandler` has no counterpart — a pure
            # deletion, hence `None` on our side; this package ships neither.
            (
                None,
                (45, 45),
                "upstream's `from .utils import EngineHandler` has no counterpart here: "
                "this package ships no handler and no `utils` module",
            ),
            # Upstream's `# NOQA isort:skip` markers are **stripped**, not added. The
            # snippet names all three imports because clause (2) requires a snippet to
            # account for every executable line.
            (
                (47, 49),
                (55, 57),
                "from .base import VariableDoesNotExist\n"
                "from .context import Context, ContextPopException, RequestContext\n"
                "from .exceptions import TemplateDoesNotExist, TemplateSyntaxError",
            ),
            ((52, 52), (60, 60), "from .base import ("),
            ((62, 62), (70, 70), "from .library import Library"),
            # No `from . import autoreload`: that module is three
            # `django.utils.autoreload` receivers over `engines.all()`, declared
            # `NOT_THE_ENGINE`. Django's own `django/template/__init__.py` registers
            # them, and they cover our backend because `RustTemplates` subclasses
            # `DjangoTemplates`. Clause (5) wants the note: 18 of our lines against 2.
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
            # The marker and its blank separator, ours only — a pure insertion
            # (`None` upstream); no executable line, so the snippet is clause (5)'s
            # note.
            ((2, 3), None, "# TRANSCRIBED: django/template/base.py (Django 6.0.9)"),
            # `from django.template.context import BaseContext` becomes
            # `from .context import BaseContext` — the *binding* must change, because
            # our `context.py` defines its own `BaseContext`, and every
            # `isinstance(context, BaseContext)` here would otherwise test an ancestry
            # that no longer holds. The `django.conf` import shares the region because
            # the line diff cannot separate an adjacent insertion, and the `int` fast
            # path below needs `USE_THOUSAND_SEPARATOR`.
            (
                (61, 62),
                (59, 59),
                "from .context import BaseContext\n"
                "from django.conf import settings",
            ),
            # The module-level `_native` binding, ours only — hoisted out of the two
            # seam bodies, where it ran once per `resolve`, worth ~50µs per render on
            # the benchmark page. Every span below shifts by one.
            ((74, 74), None, "from django_rust_templates import _native"),
            # The plan slot on `Template`, a *class* attribute because `Template` is
            # built by `__new__` in more than one place, so an `__init__`-set attribute
            # would be missing on exactly the objects a fallback is for. `None` means
            # "render the nodelist". Clause (5) wants the note.
            (
                (146, 153),
                None,
                "_native_plan = None",
            ),
            # The walk's arm in `_render`, above the fallback rather than replacing it,
            # so a declined plan is a lost optimisation and never a different answer.
            # `_render`, not `NodeList.render`, because the latter is on the compared
            # path, and every `Template.render` funnels through `_render`.
            (
                (178, 188),
                None,
                "plan = self._native_plan\n"
                "if plan is not None:\n"
                "return _native.render_plan(plan, context)",
            ),
            # The plan slot on the body `NodeList`, a class attribute for `Template`'s
            # reason. It lives on the `NodeList` because the node that renders a body
            # is often not the node the plan was built for: `BlockNode.render` builds
            # one over the *same* `NodeList`, and `{{ block.super }}` re-enters through
            # the base template's block, a second body carrying its own plan. Clause (5)
            # wants the note.
            (
                (1181, 1198),
                None,
                "_native_plan = None",
            ),
            # `NodeList._render_with_plan`: the arm and the fallback. `NodeList.render`
            # is in `COMPARED_PATH`, so the arm cannot sit there; `_render_with_plan` is
            # a name upstream does not have, so no table member can cover it. The
            # caller is the other half, in `loader_tags.py::BlockNode.render`, one line
            # for one line via `TRANSFORMS`. The fallback is upstream's own
            # `self.render(context)`, so a declined plan loses optimisation, never bytes.
            (
                (1208, 1249),
                None,
                "def _render_with_plan(self, context):\n"
                "plan = self._native_plan\n"
                "if plan is not None:\n"
                "return _native.render_plan(plan, context)\n"
                "return self.render(context)",
            ),
            # `FilterExpression.__slots__` gains the cache name, one line for one line
            # (upstream's 737 carries the four names). Named here because the
            # declaration is `__slots__`-only; it sits *above* `FilterExpression.__init__`'s
            # compared-path span, which forces the lazy fill below rather than an
            # `__init__` assignment.
            (
                (771, 771),
                (737, 737),
                '__slots__ = ("token", "filters", "var", "is_var", "_rust_names")',
            ),
            # `Template.compile_nodelist` delegates to Rust. Two regions because
            # `difflib` pairs our prose against upstream's parser construction and our
            # statements against upstream's three. The second is four statements against
            # three: `_native_plan` must be copied off the parsed object, since
            # `Template.__init__` discards it and `Template(source)` would otherwise
            # render on the Python path. This region *is* a Rust-vs-Rust hazard, and is
            # safe only because the parity rows build a `Parser` directly and never go
            # through `compile_nodelist`.
            (
                (208, 222),
                (185, 196),
                "from django_rust_templates.render import parse_template",
            ),
            (
                (225, 235),
                (199, 201),
                "parsed = parse_template(self.source, self.engine, self.origin)\n"
                "self.extra_data = parsed.extra_data\n"
                "self._native_plan = parsed._native_plan\n"
                "return parsed.nodelist",
            ),
            # The filter seam: `FilterExpression.resolve` gains a per-expression name
            # fill and one branch, both pure insertions (`None` upstream). Two regions
            # because the fill must run *before* the loop over `self.filters` (inside
            # it, it is once per application again) while the arm must run *inside*
            # that loop, consuming the `arg_vals` upstream builds.
            #
            # The prologue is guarded by `if self.filters:` so a filter-free expression
            # pays a truth test and nothing else; unguarded, the filter-free
            # `real-world/large` row measured 2900µs → 3114/3469µs.
            (
                (837, 867),
                None,
                "if self.filters:\n"
                "try:\n"
                "names = self._rust_names\n"
                "from django_rust_templates.host import django_own_filter_names\n"
                "names = self._rust_names = django_own_filter_names(self.filters)",
            ),
            # The `try`/`except AttributeError` is the unset slot, not an error:
            # `FilterExpression` has no `__dict__` and there are two construction routes
            # (`__init__` and the descriptor materialiser, which uses `__new__`), so one
            # lazy fill covers both; `__init__` is on the compared path, so the fill
            # cannot live there.
            #
            # The arm *replaces* the loop body rather than wrapping it: `_native.filter`
            # already performs upstream's four steps (`expects_localtime`,
            # `needs_autoescape`, the call, `is_safe`/`mark_safe`), so falling through
            # would apply `@stringfilter`'s coercion and `mark_safe` twice. The guarded
            # `continue` is `_native.filter`'s `CANNOT_HOLD` answer for a value Rust cannot
            # hold (a lone surrogate), tested by identity because `None` is an answer a
            # filter may give.
            #
            # Clause (4) permits this region (the compared path is the *parse* harness),
            # so clause (2) is the only guard: `tests/test_interposition.py::FilterRoutingTests`
            # notices an arm that delegated and also ran the four steps, or one keyed by
            # name rather than handle.
            (
                (875, 925),
                None,
                "from django_rust_templates.host import (\n"
                "effective_autoescape,\n"
                "effective_use_tz,\n"
                ")\n"
                "if (builtin_name := names.get(func)) is not None:\n"
                "use_tz = effective_use_tz(context, func)\n"
                "autoescape = effective_autoescape(context, func)\n"
                "filtered = _native.filter(\n"
                "builtin_name,\n"
                "obj,\n"
                "tuple(arg_vals),\n"
                "autoescape,\n"
                "use_tz,\n"
                ")\n"
                "if filtered is not _native.CANNOT_HOLD:\n"
                "obj = filtered\n"
                "continue",
            ),
            # The resolver seam: `Variable._resolve_lookup` delegates to Rust, including
            # Django's outer `except Exception` handler and its `logger.debug`. One
            # region; the upstream span carries the handler's body and the `return
            # current` tail — where the alignment landed, not where the code moved.
            #
            # The second Rust-vs-Rust hazard, and unlike `compile_nodelist` it *is*
            # reached by parity harnesses, whose Python side is the *pinned source*
            # executed from `.django`. What moved here — `_resolve_lookup`'s whole
            # exception semantics into `crates/template-py/src/lib.rs::outcome_to_py` —
            # the diff cannot show, because a body replaced by a call is one hunk.
            (
                (1073, 1119),
                (957, 1028),
                'return _native.resolve_lookups(self.lookups, context)',
            ),
            # The fast paths for a value whose **exact** type is `str` and one whose
            # exact type is `int`. Pure insertion, and the snippet names all fourteen
            # executable lines.
            #
            # Exact type, not `isinstance`: a `str` subclass is where `SafeString` and
            # `markupsafe.Markup` live, both carrying `__html__`, and escaping one is the
            # defect this path exists to keep out. `None` from the binding is a second
            # refusal: PyO3 hands a Python `str` over as `&str` only when UTF-8
            # encodable, so without it a lone surrogate that renders today would raise.
            (
                (1280, 1328),
                None,
                "if context.autoescape and type(value) is str:\n"
                "escaped = _native.html_escape(value)\n"
                "if escaped is not None:\n"
                "return SafeString(escaped)\n"
                "if type(value) is int and (\n"
                "context.use_l10n is False or not settings.USE_THOUSAND_SEPARATOR\n"
                "):\n"
                "text = str(value)\n"
                "if context.autoescape:\n"
                "escaped = _native.html_escape(text)\n"
                "if escaped is not None:\n"
                "return SafeString(escaped)\n"
                "return conditional_escape(text)\n"
                "if context.use_l10n is False:\n"
                "return text\n"
                "return mark_safe(text)",
            ),
            # Pins `BaseContext.__getitem__` for the Rust walk's root lookup — a pure
            # insertion at end of file, at the end so no span above moves. Outside
            # clause (4)'s reach by measurement; `tests/test_loop_read_guard.py` is its
            # control.
            (
                (1408, 1443),
                None,
                "BASE_CONTEXT_GETITEM = BaseContext.__getitem__",
            ),
        ],
    ),
    "smartif.py": (
        "django/template/smartif.py",
        [
            # The marker and its blank separator, ours only — the whole of the
            # difference. Measured: ours 217 lines, upstream 215, one `delete` opcode at
            # ours 2-3, the other 215 equal in place, so `smartif` is a verbatim
            # transcription plus a header. It is interposed because `defaulttags.py:43`
            # takes `IfParser` from it and the grammar it drives is Rust's.
            ((2, 3), None, "# TRANSCRIBED: django/template/smartif.py (Django 6.0.9)"),
        ],
    ),
    "context.py": (
        "django/template/context.py",
        [
            # The marker and its blank separator, ours only — the whole of the
            # difference. Upstream opens with an import, so the marker is a comment on
            # line 1. Measured: 309 of our lines against 307 of upstream's.
            ((1, 2), None, "# TRANSCRIBED: django/template/context.py (Django 6.0.9)"),
        ],
    ),
    "engine.py": (
        "django/template/engine.py",
        [
            # The marker, on the line upstream's `import functools` occupied — one line
            # against one, so the shape is readable and the snippet is `None`.
            ((1, 1), (1, 1), None),
            # The three built-in libraries named by this package's dotted path rather
            # than Django's. `executable_lines` marks the whole `Assign`, so all three
            # lines are executable and the snippet names all three.
            (
                (15, 17),
                (15, 17),
                '"django_rust_templates.engine.defaulttags",\n'
                '"django_rust_templates.engine.defaultfilters",\n'
                '"django_rust_templates.engine.loader_tags",',
            ),
            # The default loader path, one line for one line.
            (
                (38, 38),
                (38, 38),
                'loaders = ["django_rust_templates.engine.loaders.filesystem.Loader"]',
            ),
            # Six of our lines against two of upstream's: the `app_directories` append
            # and the `cached` pair reflowed by the formatter once the dotted strings
            # grew past the line limit. All six are executable, so the snippet names all
            # six.
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
            # `Engine.get_default` is deliberately not cached: its answer is a function
            # of `settings.TEMPLATES`, and the receiver that clears Django's cache names
            # *Django's* class object, so a cache here would hold the first call's
            # settings for the life of the process (three red rows in
            # `test_engine.GetDefaultTests`). Clause (5) wants the note: 20 of our lines
            # against none.
            (
                (91, 110),
                None,
                "no `@functools.lru_cache` on `Engine.get_default`: its answer is a "
                "function of `settings.TEMPLATES`, and the receiver that clears "
                "Django's cache names Django's class object, not this one",
            ),
            # The other half of that edit: upstream's decorator is gone from ours. A
            # pure deletion, so clause (5)'s note is required and there are no executable
            # lines of ours to name.
            (
                None,
                (88, 88),
                "upstream's `@functools.lru_cache` on `Engine.get_default` is deleted "
                "with the cache it decorates; see the region at ours 91-110",
            ),
            # Upstream's `get_default` looks for `DjangoTemplates` only, because
            # upstream ships one backend; this package ships a second, and a caller who
            # configured *this* one and then used the historical API would be handed
            # stock Django's engine, or nothing. The import is the executable line.
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
            # `ImproperlyConfigured`'s message stays upstream's wording because
            # `template_tests.test_engine` asserts the exact string; the case is
            # reachable only when neither backend is configured. Clause (5) wants the
            # note: five of our lines against none.
            (
                (146, 150),
                None,
                "the message on `ImproperlyConfigured` stays upstream's wording — "
                "`template_tests.test_engine` asserts the exact string",
            ),
        ],
    ),
    # The six loader files. `__init__.py` (0 bytes) and `base.py` (opens with an
    # import) carry the marker as a comment on line 1, region `((1, 2), None, ...)`;
    # `filesystem.py`, `app_directories.py`, `cached.py` and `locmem.py` (docstring
    # first) carry it inside, region `((2, 3), None, ...)`. `loaders/__init__.py` is the
    # only transcription whose upstream is the empty string.
    #
    # Every `COMPARED_PATH` span for these files is upstream's line number plus two,
    # which holds only because none of them rewrites its module docstring (unlike
    # `engine/__init__.py`); `gen_impl_transcription.py --check` keeps that true.
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
            # Absorbed the marker's own region: the rewritten import on line 3 leaves
            # no equal line between the inserted marker and the changed import, so
            # `difflib` reports ours 1-3 against upstream's 1 as one hunk. The shape
            # changed (3 against 1), so this is clause (5)'s note as well as clause (2)'s
            # snippet, which names the region's only executable line.
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
            # The sibling import, so `engine` resolves to itself as a plain package;
            # one line for one line, so every span above stands.
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
            # Imports Django's `get_app_template_dirs` rather than a copy: its cache
            # is cleared by `update_installed_apps`, and a second function object would
            # hold a second cache that receiver cannot reach (measured:
            # `test_loaders.AppDirectoriesLoaderTests.test_not_installed`). Pure
            # insertion, no executable line of ours.
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
            # The neighbouring line 11 is deliberately unchanged:
            # `from django.template.backends.django import copy_exception` names
            # Django's backend layer, which `engine` has no counterpart for and should
            # not — `copy_exception` is an ordinary function with no identity
            # requirement.
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
            # The sibling import, as in the files above.
            ((7, 7), (5, 5), "from .. import Origin, TemplateDoesNotExist"),
        ],
    ),
    # The pin opens with `import posixpath`, so the marker is a comment on line 1.
    # Nothing else differs: M7 compiles `block`/`extends`/`include` host-side, but that
    # changes who *runs* `do_block`, not a character of the transcription.
    "loader_tags.py": (
        "django/template/loader_tags.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/loader_tags.py (Django 6.0.9)",
            ),
            # Two lines of upstream's, rewritten, and none of the file's shape moves:
            # the seven `COMPARED_PATH` spans are byte-identical before and after and
            # the file is 372 lines on both sides, because the rows come from
            # `gen_impl_transcription.py`'s `TRANSFORMS`, which keys on upstream's exact
            # text. `_render_with_plan` is `base.py`'s (clause (4) refuses an arm inside
            # `NodeList.render`); `BlockNode.render` is not on the compared path and is
            # the frame a block body funnels through, including `super()`.
            (
                (58, 58),
                (56, 56),
                "result = self.nodelist._render_with_plan(context)",
            ),
            (
                (68, 68),
                (66, 66),
                "result = block.nodelist._render_with_plan(context)",
            ),
        ],
    ),
    "defaultfilters.py": (
        "django/template/defaultfilters.py",
        [
            # The marker and its blank separator — a pure insertion, hence `None` on the
            # upstream side, and the whole of the difference. **Pin-plus-marker on
            # purpose**: all 59 filter bodies stay upstream's text, and what makes them
            # reachable from Rust is a branch in `base.py`'s `FilterExpression.resolve`,
            # declared in *that* file. A delegate per body would be 59 regions here all
            # saying one thing.
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
            # The marker and its blank separator — a pure insertion, hence `None` on the
            # upstream side, and the whole of the difference.
            #
            # **Verbatim, and mostly dead code, and that is the claim the transcription
            # makes.** Measured: the module registers 24 tags, 20 of them Rust-compiled,
            # whose bodies are unreachable through the *render* path because
            # `ShellHost.has_tag` answers False for a Rust-owned name. Stubbing the 20 out
            # would be a worse claim: a library rebinding a built-in tag puts Django's body
            # back on a live path at runtime, which is exactly the case that matters
            # (`tests/test_interposition.py`'s `DefaultTagsInterpositionTests` pins it).
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/template/defaulttags.py (Django 6.0.9)",
            ),
        ],
    ),
    # The first rows here whose upstream module is `django/templatetags/…` rather than
    # `django/template/…` — which is why every value above this comment is
    # `"django/template/" + key` and these are not. The *shape* is unchanged: the pin
    # plus a two-line marker, `None` on the upstream side, the whole of the difference.
    # All these modules open with imports rather than a docstring, so the marker is a
    # comment on line 1 and the region is `(1, 2)`.
    #
    # **`templatetags/__init__.py` is deliberately absent.** The shim's
    # `_is_foreign_package` declines to serve the `django.templatetags` package itself
    # (we serve only files inside it), so our `__init__.py` would never be loaded while
    # `shadowing_files()` still reported it; upstream's is 0 bytes and defines nothing.
    #
    # Verbatim is right for these tag modules even though Rust compiles some of their
    # tags: `has_tag` answers False for a Rust-owned name the template loaded, so those
    # bodies are never called. The case that needs them right is a library rebinding one
    # of the names, which is the same case `defaulttags.py` above is kept verbatim for.
    "templatetags/cache.py": (
        "django/templatetags/cache.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/cache.py (Django 6.0.9)",
            ),
            # The engine package by its stable name. These four names are
            # `engine/__init__.py` re-exports, and that module binds
            # `django_rust_templates.engine` to itself, so this is the same object
            # `from django.template import …` gives under the finder.
            #
            # **Not a relative import, and that is a measurement, not a preference.**
            # This file is served as `django.templatetags.cache`, a sibling of
            # `django.template`, so `..` is `django` and `from .. import Library` raises
            # `ImportError: cannot import name 'Library' from 'django'`. The relative
            # spelling that works under the finder (`..template`) is wrong standalone,
            # where the engine package is `..` itself. No relative form is correct in
            # both, so this one is absolute.
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
            # The only import rewrite in the tree that changes what is bound rather
            # than where it is looked up. Upstream's `from django import template` binds
            # the *package*; this binds this package's own engine, which
            # `engine/__init__.py` re-exports the same three names from, so the line is
            # one-for-one and **the seven `template.X` uses in the body below are
            # untouched** — the body's claim is that it is upstream's.
            #
            # Eight of our lines against one of upstream's: the seven extra are the note
            # above the import, so the span is the note plus the line it explains and
            # clause (5) wants a snippet as well as the note. Only line 12 is executable
            # (an `ImportFrom`).
            (
                (5, 12),
                (3, 3),
                "from django_rust_templates import engine as template",
            ),
        ],
    ),
    # The pin plus a two-line marker, `None` on the upstream side, nothing else; the
    # module opens with imports, so the marker is a comment on line 1.
    #
    # Verbatim is right for the tag bodies though Rust compiles their names: `has_tag`
    # answers False for a Rust-owned name the template loaded, so `localtime_tag`,
    # `timezone_tag` and `get_current_timezone_tag` are bodies nothing calls. The three
    # *filters* (`localtime`, `timezone`, `utc`, the last registered under an alias) are
    # Rust bodies and are transcribed-verbatim here because the unit of transcription is
    # the file, and a per-file table cannot express a half-transcribed module.
    "templatetags/tz.py": (
        "django/templatetags/tz.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/tz.py (Django 6.0.9)",
            ),
            # The stable-name import, as in `cache.py` above.
            ((6, 6), (4, 4),
             "from django_rust_templates.engine import Library, Node, TemplateSyntaxError"),
        ],
    ),
    # The pin plus a two-line marker, `None` on the upstream side, nothing else.
    #
    # Verbatim is right for the one tag body although Rust compiles its name:
    # `localize_tag` is a body nothing calls, the case above. **The render and the
    # filters stay Python's, which is why this is a transcription and not a
    # delegation:** `LocalizeNode.render` is our transcription's Python (only the
    # *compile* function is Rust's), and the two filters (`l10n.py:8,16`, whose bodies
    # make the `django.utils.formats` calls) are Rust bodies transcribed-verbatim for
    # `tz.py`'s reason. The "the formats delegation stays Python's" claim lives here,
    # where the guard reads it.
    "templatetags/l10n.py": (
        "django/templatetags/l10n.py",
        [
            # The same absorption `loaders/base.py` records: the import on line 3
            # directly follows the marker with no equal line between them, so `difflib`
            # reports ours 1-3 against upstream's 1 as one hunk. Shape changed (3 against
            # 1), so this is clause (5)'s note as well as clause (2)'s snippet.
            (
                (1, 3),
                (1, 1),
                "# TRANSCRIBED: django/templatetags/l10n.py (Django 6.0.9)\n"
                "from django_rust_templates.engine import Library, Node, "
                "TemplateSyntaxError",
            ),
        ],
    ),
    # The pin plus a two-line marker, `None` on the upstream side, nothing else.
    #
    # Verbatim is right for all eight tag bodies although Rust compiles their names:
    # those bodies are unreachable through the host (`has_tag` answers False for a
    # Rust-owned name). The two names Rust does **not** own
    # (`blocktranslate`/`blocktrans`) *are* reached through this file, and they are
    # transcribed here for that reason rather than the unreachability one — the file is
    # the unit of transcription.
    #
    # **Every node's render stays Python's:** each `render` makes the
    # `django.utils.translation` call (`i18n.py:19,31,44,59,68,169,177,213`) and Rust
    # owns only the *compile* functions. The four filters (`i18n.py:293,298,304,309`) are
    # Rust bodies transcribed-verbatim for `tz.py`'s reason.
    "templatetags/i18n.py": (
        "django/templatetags/i18n.py",
        [
            (
                (1, 2),
                None,
                "# TRANSCRIBED: django/templatetags/i18n.py (Django 6.0.9)",
            ),
            # Three engine-internal imports on three consecutive lines, so `difflib`
            # reports them as one hunk and one declaration covers all three. All three
            # name the engine package by its stable name rather than relatively, for the
            # `..`-is-`django` reason `cache.py` records.
            #
            # **`base` is the one that must not be a second copy.** A duplicate of
            # `defaulttags` costs nothing — it is a leaf whose own imports resolve
            # through the finder — but a duplicate of `base` defines a second
            # `TokenType`, and `token.token_type` is compared against it below: the
            # comparison would be False for every token, silently. `engine/__init__.py`
            # aliases every sibling it has already imported, which is where `base`
            # arrives from.
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
    # The pin opens with `from collections.abc import Iterable`, so the marker is a
    # comment on line 1. Nothing else differs from the pin.
    "library.py": (
        "django/template/library.py",
        [
            # The marker and its blank separator, ours only — a pure insertion
            # (`None` upstream).
            #
            # **The file is verbatim, and here that claim is load-bearing.** `library.py`'s
            # classes are observed by *identity*: the suite's `assertRaises(
            # InvalidTemplateLibrary)` and `isinstance(lib, Library)` rows are about the
            # object, and `engine/__init__.py:54`, `defaulttags.py:44`,
            # `defaultfilters.py:30`, `loader_tags.py:9` and `engine.py:12` all bind
            # these names at import. A re-export would satisfy every behavioural row and
            # leave five modules holding Django's objects — measured in
            # `.review/m13-recon.md` §7, where a module-attribute patch on
            # `import_library` read `0` while the bound name read `11050`.
            ((1, 2), None, "# TRANSCRIBED: django/template/library.py (Django 6.0.9)"),
        ],
    ),
}


def pinned_version() -> str:
    """The version of the checkout in ``.django``, read from its own source.

    Read with ``ast`` rather than by importing: this script deliberately imports no
    Django, because whether ``django`` resolves to the checkout or to a shim depends on
    the environment it runs in.
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

    Two exclusions:

    * a bare string expression (the module docstring, or a stray string) is not
      executable — a no-op in CPython, and where a transcription's own prose lives, so
      counting it would force a snippet on every rewritten docstring;
    * a **compound** statement contributes only its header line, not its whole span —
      otherwise `ast.walk`'s ``end_lineno`` marks every comment and docstring *inside*
      the function executable. The header is the only line of a compound statement that
      does something; its children are walked separately and counted on their own.
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

    One entry per unmatched ``difflib`` opcode: a block of ours against a block of
    upstream's, either block possibly empty. Regions rather than per-line pairs because
    that is ``DECLARED``'s granularity.
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

    Derived from ``name`` — the ``engine``-relative posix path every table here is keyed
    by — rather than from the declared upstream path: ``__init__.py`` is the package
    itself, whose last segment the filename does not name, and deriving from the upstream
    path would answer with *Django's* modules now that the shim is gone.
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

    ``None`` on either side means *no lines*, and only an absent actual span fits it — a
    declaration of a pure insertion (``theirs_span=None``) must not be made to report
    itself stale. Both the coverage test and the staleness test need this same relation,
    quantified in opposite directions.
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

    **Clause (4) exists so the parity harness cannot certify code the transcription has
    already declared divergent** — the harness compares Rust's parse against the
    *transcription's* parse, and `assert_target_is_not_rust_owned` admits a declared
    transcription as a target only because this guard has already checked it against
    Django line by line. A divergence inside a function the harness executes breaks that
    chain.

    The M18 rename makes that unsatisfiable at two lines of ``engine.py``: every dotted
    path a transcription *names* has to be this package's rather than Django's —
    `default_builtins` names our three libraries, `Engine.__init__` names our three
    loaders, `get_default` names our backend — and two of them fall inside
    `Engine.__init__`, which the harness executes to obtain `template_builtins`. Refusing
    them leaves the guard with no legal way to describe the file it is reading.

    So the exception is drawn exactly as narrowly as the architecture forces, and it is a
    **rule rather than a waiver**: permitted only when the whole of the region is
    upstream's text with `django.template.` replaced by `django_rust_templates.engine.` —
    whitespace removed, so the formatter's reflow of a now-longer string literal does not
    count. Every token has to match, so a reordered keyword, a changed condition or one
    new statement anywhere in the region fails it.

    **Both constants are the control:** the substitution is only offered when our span
    actually says `OURS_NAMESPACE` and upstream's actually says `UPSTREAM_NAMESPACE`, so a
    region carrying a *decision* rather than a *name* cannot reach it however its text
    reads. `tests/test_compared_path_instrument.py` drives both directions.
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

    The *inverse* question to ``_check_marker``: this one asks whether an undeclared file
    has a marker at all. Only the first ``MARKER_WINDOW`` lines are read, the same window
    ``_check_marker`` uses, so the two cannot disagree about which files are
    transcriptions. A file that fails to decode is not a transcription as far as this
    guard is concerned — reporting it as marked would point the reader at the wrong
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

    Returned rather than printed so a test module can call it directly and make this
    guard part of its own evidence.
    """
    failures = []
    # The pinned measured path must cover exactly the transcriptions that declare
    # regions. Equality, not containment: a module missing from the pin has its
    # declared regions checked by nothing, and a module in the pin that is no longer
    # transcribed is a stale measurement — both failures are found in the commit that
    # causes them.
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
    # Every file carrying the marker must be declared, and vice versa. Without this the
    # guard is defeated by the exact change it exists to review: the loop below iterates
    # `DECLARED`, so a transcription nobody declared is never opened, never diffed and
    # never has its regions checked, while every other clause stays green.
    #
    # `rglob`, not `glob`, and `engine`-relative keys, not basenames: `glob("*.py")`
    # cannot see `loaders/base.py`, and `path.name` would key it `base.py`, another
    # transcription's key. `loaders/base.py` is also the spelling
    # `milestone_ownership.shadowing_files()` reports and `m10_measure_compared_path.py`
    # prints rows in.
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
    # A declared file that is merely missing is reported by the per-module loop below, so
    # only report here the ones that exist and are somehow unmarked.
    unmarked = [
        name for name in unmarked if IMPL.joinpath(*name.split("/")).is_file()
    ]
    if unmarked:
        failures.append(
            f"{unmarked} is declared as a transcription but carries no "
            "`# TRANSCRIBED:` marker"
        )
    for name, (upstream, regions) in sorted(DECLARED.items()):
        # A key is a posix path relative to `engine`, not one segment.
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

            # (4) The compared path must be outside the declared differences. Checked
            # before the snippet clauses and never skipped by them: this is the failure
            # that makes a parity comparison meaningless. Measured by
            # `.review/m9-measurements/m9_guard_teeth.py` scenario 3.
            for first, last, label in COMPARED_PATH.get(name, []):
                if not overlaps(ours_span, first, last):
                    continue
                # The architecture-forced half of clause (4): a region whose whole
                # divergence is this package's namespace in place of Django's — the
                # narrowness is argued in `_namespace_only`.
                if _namespace_only(ours, ours_span, theirs, theirs_span):
                    continue
                failures.append(
                    f"{name}: region ours:{_fmt(ours_span)} covers "
                    f"{label} ({first}-{last}), which the parity harness "
                    "executes, so the comparison would be Rust against Rust"
                )

            # (5) A region whose two spans differ in length is one the line diff cannot
            # describe, so prose is the only witness and the guard requires it. A ratio
            # threshold would be a number with no data behind it; the *existence* of a
            # shape change is binary. A region whose spans match can be read off the
            # diff; one whose spans do not, cannot.
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
                # A snippet on a region with no executable line is only wrong when the
                # spans *match* — then it declares nothing. When they differ it is
                # clause (5)'s required note, which is exactly the pure-insertion case.
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
