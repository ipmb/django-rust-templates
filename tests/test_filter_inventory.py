"""Django's built-in filter inventory, pinned executably rather than in prose.

Modelled on `tests/test_tag_inventory.py`, and for the same reason: M4's plan has
carried the number of built-in filters, the number of `@stringfilter`-decorated
bodies, and the size of the **delegating** set since it was written, and this
project has had seven count disputes that were each two right answers to two
unstated questions. A number in a document can be wrong indefinitely; a number in
a test can be wrong once.

Five things are pinned here:

1. the three counts that have been confused — 57 registered filters, 59 top-level
   `def`s, 29 `@stringfilter` bodies — and the names in each set, so the gap
   between them is a named fact rather than an arithmetic accident;
2. the seven filters whose registered name is not their `def` name, because
   `defaultfilters.escape` **is not** the registered `escape` (it is the raw
   `django.utils.html.escape`, which takes no `arg` and does not mark safe) — the
   trap the plan's own review found;
3. the four delegating metrics of §D2, **derived here rather than transcribed**;
   the third was added after `phone2numeric` scored clean on the first two and
   was still not Rust's — a helper's result-changing decorator is invisible to a
   metric that measures what the body references;
4. the Rust registry's names and flags against Django's registry — read from the
   pinned file since M11 interposed the name, which is also why the last row
   asserts the *interposed* module registers that same set — including `cut`'s
   and `escapejs`'s **absent** `is_safe`;
5. the Rust registry's `stringfilter` column against the decorators — the one
   place the 29 can be disagreed with, since a Rust-side test of that column
   would be the same list read twice.

Metrics A–C are read off `defaultfilters.py` and so answer a question about
**Django**; metric D reads our own bodies and is the only one that reddens when
a body is rewritten to stop delegating. The delegating set is 24, and it is
asserted from both directions (`test_the_delegation_set_is_derived`), so the
number cannot be right about the source and wrong about the thing that ships.

Django is pinned at 6.0.9 under `.django/`. These assertions are meant to fail
loudly on a pin bump that adds or removes a filter — otherwise §10.3's milestone
table goes stale in silence, which is how the tag table got to three corrections.
"""

import ast
import inspect
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

# **Django's file, not the one this name now resolves to.** Until M11 Task 2
# `from django.template import defaultfilters` *was* Django's file, and every
# metric in this module was read off it — the docstring above says so, and says
# the reason: those metrics "answer a question about **Django**". The
# interposition moves the name, so the import that used to deliver Django's file
# now delivers a verbatim transcription of it, and continuing to read through it
# would make Metrics A–C silent about upstream — the self-comparison the guard
# row at the end of this file exists to refuse. `load_pinned` is the established
# way to hold Django's copy under a name the shim has taken (see
# `tests/pinned_module.py`), and it restores the sentence above to a fact.
from tests.pinned_module import load_pinned  # noqa: E402

defaultfilters = load_pinned("defaultfilters.py")

SOURCE = Path(defaultfilters.__file__)

# The 57 invocable names, ascending. §10.3 gives M4 all of them.
REGISTERED = {
    "add", "addslashes", "capfirst", "center", "cut", "date", "default",
    "default_if_none", "dictsort", "dictsortreversed", "divisibleby", "escape",
    "escapejs", "escapeseq", "filesizeformat", "first", "floatformat",
    "force_escape", "get_digit", "iriencode", "join", "json_script", "last",
    "length", "linebreaks", "linebreaksbr", "linenumbers", "ljust", "lower",
    "make_list", "phone2numeric", "pluralize", "pprint", "random", "rjust",
    "safe", "safeseq", "slice", "slugify", "stringformat", "striptags", "time",
    "timesince", "timeuntil", "title", "truncatechars", "truncatechars_html",
    "truncatewords", "truncatewords_html", "unordered_list", "upper",
    "urlencode", "urlize", "urlizetrunc", "wordcount", "wordwrap", "yesno",
}

# Registered under a different name from their own `def`, via
# `@register.filter("<name>", ...)`. Seven, and all seven are load-bearing: a
# scan that assumed `def` name == registered name would silently lose them.
REGISTRY_NAMES_THAT_ARE_NOT_DEF_NAMES = {
    "escape": "escape_filter",
    "escapejs": "escapejs_filter",
    "linebreaks": "linebreaks_filter",
    "phone2numeric": "phone2numeric_filter",
    "slice": "slice_filter",
    "timesince": "timesince_filter",
    "timeuntil": "timeuntil_filter",
}

# The 59 top-level `def`s of `defaultfilters.py`, which is 57 filters plus the two
# module-level helpers. `_property_resolver` is `dictsort`'s `key` argument and
# `stringfilter` is the decorator itself — neither is invocable from a template.
HELPER_DEF_NAMES = {"_property_resolver", "stringfilter"}

DEF_NAMES_THAT_ARE_NOT_REGISTRY_NAMES = (
    set(REGISTRY_NAMES_THAT_ARE_NOT_DEF_NAMES.values()) | HELPER_DEF_NAMES
)

# The 29 bodies carrying `@stringfilter` (`defaultfilters.py:38-52`). Named, not
# counted, because the 29 is the number that has been reported two ways: it
# excludes `slice`, `timesince`, `timeuntil` and `phone2numeric` (which are
# renamed *and* undecorated) and includes the seven renamed bodies that do carry
# it — so it is neither "the 57 minus the renamed ones" nor anything else simple.
STRINGFILTER = {
    "addslashes", "capfirst", "center", "cut", "escape_filter", "escapejs_filter",
    "force_escape", "iriencode", "linebreaks_filter", "linebreaksbr", "linenumbers",
    "ljust", "lower", "make_list", "rjust", "safe", "slugify", "striptags", "title",
    "truncatechars", "truncatechars_html", "truncatewords", "truncatewords_html",
    "upper", "urlencode", "urlize", "urlizetrunc", "wordcount", "wordwrap",
}

# Measured: the registered callable has **no** `is_safe` attribute at all for
# these two, because their decorator is `@register.filter("name")` with no
# `is_safe` kwarg and `Library.register` only `setattr`s the flags it is given.
# `base.py:816` and `stringfilter`'s `_dec` both read `getattr(..., "is_safe",
# False)`, so absent *behaves* as false — which is why the Rust `FilterFlags` bool
# is the right model for the engine and the wrong model for this file. A future
# reader who saw only the Rust table would assume Django's attribute is always
# present, and that assumption is what would break on a Django bump.
IS_SAFE_ABSENT = {"cut", "escapejs"}

# --- §D2's two metrics -------------------------------------------------------

# Metric A: the filter's body's answer comes from a `django.utils` engine that
# bottoms out, within that utility, in runtime state Rust cannot hold (settings,
# locale, the active language, the clock) or in a Python-stdlib table-or-parser
# port. Derived below from the pinned source; this is the expected answer.
DELEGATING_BY_UTILS = {
    "date", "filesizeformat", "floatformat", "iriencode", "json_script", "slugify",
    "striptags", "time", "timesince", "timeuntil", "truncatechars",
    "truncatechars_html", "truncatewords", "truncatewords_html", "urlize",
    "urlizetrunc", "wordwrap", "yesno",
}

# Metric A's **scope**, made explicit rather than left implicit in
# `_import_map()`. Metric A decides by reading a utility's own module to see
# where it bottoms out, and it scans only `django.*` imports — so three filters
# reach their engine without passing through `django.utils` at all:
#
#   defaultfilters.py:3    import random as random_module
#                 :10   from pprint import pformat
#                 :11   from urllib.parse import quote
#
# `pprint` and `urllib.parse` are named in metric A's own docstring as
# Python-stdlib ports, and `random.choice` is stateful, so all three are the
# question metric A asks. They were missed because the *instrument* is scoped to
# a namespace, not because the answer is no — measured by building metric D
# below, which asks the Rust bodies instead and finds all three.
#
# Hand-typed rather than derived, and the reason belongs beside the set:
# extending metric A's own method to CPython's stdlib means reading each stdlib
# module to see where *it* bottoms out, which is a far bigger instrument than
# the question warrants. `delegating_filters_in_rust()` is what checks it, so it
# cannot rot silently.
DELEGATING_BY_STDLIB_ENGINE = {
    "pprint": "`pprint.pformat` — `from pprint import pformat`",
    "random": "`random.choice` — `import random as random_module`",
    "urlencode": "`urllib.parse.quote` — `from urllib.parse import quote`",
}

# Metric B: the body calls a `str` method whose result is not a function of the
# code point alone. `lower` and `title`, and *not* `upper`/`capfirst`: Task 1's
# exhaustive diff shows `str.upper()` is code-point-local (the same 28 code points
# as `char::to_uppercase`), while `str.lower()` applies Final_Sigma and
# `str.title()` lowercases each run's remainder.
DELEGATING_BY_STRING_METHOD = {"lower", "title"}

# Metric C: the body **reproduces** a helper whose `def` carries a result-changing
# lazy decorator, rather than calling it. This is the metric `phone2numeric`
# needed: it references nothing stateful (metric A says no) and calls no
# context-sensitive `str` method (metric B says no), and it was still not pure,
# because `@keep_lazy_text` sits between the registry and the `def` — so the
# wrapped function is the *decorated* one, and a lazy argument returns a lazy
# result, with nothing in the body or in the Python source showing it.
#
# The rule, which is why this is a third clause in §D2 rather than a note on one
# filter: **the wrapped function is the decorated one, not the `def` body.** A
# body that *calls* the host's method for the helper inherits the decorator; a
# body that owns the helper's algorithm owns the wrapper's behaviour too, and
# cannot see it.
#
# The 1 is an intersection of two cuts and both are load-bearing, so the near-miss
# is derived and asserted as well:
#
# * `@stringfilter` resolves the `Promise` with `str(first)` before the body runs,
#   so the laziness branch cannot fire for any of the 29 decorated bodies —
#   `cut`, `safe`, `escape` (whose table *is* Rust's, five replacements) and the
#   six others that reach a lazy-decorated helper. That cut leaves **5**.
# * of those 5, four call `mark_safe`, which is a **host method** calling Django's
#   own `mark_safe` (`filters.rs:69-71`) — so `keep_lazy` rides along and the
#   argument's laziness is answered by Python. That cut leaves **1**.
DELEGATING_BY_LAZY_DECORATOR = {"phone2numeric"}

# The five candidates metric C's two cuts are applied to — derived, not typed in,
# and asserted, so a Django bump that adds or removes one fails here rather than
# silently moving the 1.
REACHING_A_LAZY_HELPER_WITHOUT_STRINGFILTER = {
    "floatformat", "join", "phone2numeric", "safeseq", "unordered_list",
}

# One verdict per candidate, in the `UTILITY_VERDICTS` shape and for the same
# reason: which of them owns the helper's algorithm is a property of *our* Rust
# bodies, not of `defaultfilters.py`, so it cannot be derived from the pinned
# source. An unclassified candidate is an error rather than a silent
# non-delegation, and every verdict must still be a candidate.
LAZY_HELPER_VERDICTS = {
    "phone2numeric": (
        True,
        "the body owned the helper's 26-entry table and its own `.lower()`; "
        "now one call to `FilterHost::phone2numeric`",
    ),
    "floatformat": (
        False,
        "`mark_safe` is a host method — Django's own decorated function is what "
        "runs, so `keep_lazy` is inherited rather than reproduced",
    ),
    "join": (
        False,
        "`mark_safe(data)`, and `mark_safe` is the host's",
    ),
    "safeseq": (
        False,
        "`mark_safe` per element is the host's; a `Promise` element answers a "
        "`__proxy__` through it",
    ),
    "unordered_list": (
        False,
        "`mark_safe(list_formatter(value))`, and `mark_safe` is the host's",
    ),
}

DELEGATING_FILTERS = (
    DELEGATING_BY_UTILS
    | set(DELEGATING_BY_STDLIB_ENGINE)
    | DELEGATING_BY_STRING_METHOD
    | DELEGATING_BY_LAZY_DECORATOR
)

# The neighbouring counts for metric A — each correct for its own question, each
# wrong for this one. Derived below, not typed in.
#
# The plan's first review reported the middle one as **26**. It is measured at
# **27** here, and the plan's prose cannot produce its own number either: it names
# six swept filters (`escape`, `join`, `escapeseq`, `force_escape`, `linenumbers`,
# `linebreaks`) which, added to the 18, gives 24. The measured sweep also takes in
# `escapejs`, `linebreaksbr` (both via `django/utils/html.py`) and
# `phone2numeric` (via `django/utils/text.py`). Rather than pick a definition that
# returns 26, the metric is stated precisely below and the measured value asserted
# — which is the whole point of deriving it in a test.
REFERENCING_ANY_DJANGO_UTILS = 31
IN_A_MODULE_THAT_IMPORTS_STATE = 27

# Which imported names make a module "stateful" for the coarse metric. The three
# things metric A names: settings, the active language, the clock.
STATE_IMPORTS = (
    "django.conf",
    "django.utils.translation",
    "django.utils.timezone",
)

# Per-**utility** verdicts for metric A. Per utility and not per module, which is
# the mistake the coarse metric makes: `strip_tags` and `escape` share
# `django/utils/html.py`, and `Truncator` and `phone2numeric` share
# `django/utils/text.py`, but only the first of each pair needs a parser or a
# Unicode table. Every utility the 57 reference must appear here — an
# unclassified one is an error, not a silent "does not delegate" — and every entry
# must be referenced, so a stale verdict cannot linger.
UTILITY_VERDICTS = {
    "django.utils.formats": (True, "settings and the active locale"),
    "django.utils.dateformat.format": (True, "the active locale and timezone"),
    "django.utils.dateformat.time_format": (True, "the active locale and timezone"),
    "django.utils.timesince.timesince": (True, "the clock and the active language"),
    "django.utils.timesince.timeuntil": (True, "the clock and the active language"),
    "django.utils.translation.gettext": (True, "the active language"),
    "django.utils.translation.ngettext": (True, "the active language"),
    "django.utils.html.strip_tags": (True, "`HTMLParser`"),
    "django.utils.html.urlize": (True, "`html.parser`"),
    "django.utils.html.json_script": (True, "`json`"),
    "django.utils.text.Truncator": (True, "`unicodedata` and the active language"),
    "django.utils.text.slugify": (True, "`unicodedata.normalize`"),
    "django.utils.text.wrap": (True, "`textwrap`"),
    "django.utils.encoding.iri_to_uri": (True, "`urllib.parse.quote`"),
    # Not delegating, and each for a stated reason.
    "django.utils.safestring.mark_safe": (False, "a primitive: `Value::Safe`"),
    "django.utils.safestring.SafeData": (False, "a primitive: `Value::Safe`"),
    "django.utils.html.conditional_escape": (False, "a primitive: `Value::Safe`"),
    "django.utils.html.escape": (False, "a hand table Rust reimplements"),
    "django.utils.html.escapejs": (False, "`_js_escapes`, a hand table"),
    "django.utils.html.linebreaks": (False, "regexes over portable primitives"),
    "django.utils.html.avoid_wrapping": (False, "one `str.replace`"),
    "django.utils.text.normalize_newlines": (False, "one `str.replace`"),
    # Metric A's answer only, and deliberately still `False`: `phone2numeric`'s
    # own module bottoms out in nothing stateful, so A is right to say no and the
    # delegation comes from metric C. A `True` here would credit A with a
    # finding that is C's, which is the count confusion this file exists to stop.
    "django.utils.text.phone2numeric": (False, "a hand table — see metric C"),
}

# The `str` methods whose result is not a function of the code point alone.
DELEGATING_STR_METHODS = {
    "lower": "Final_Sigma: `\"ΟΣ\".lower()` is `\"ος\"`",
    "title": "lowercases each run's remainder, so Final_Sigma again",
}

# The decorators that change a helper's *result* rather than only its call count
# or its metadata — the ones a filter's body cannot see and cannot reproduce.
#
# Swept over `django/utils/*.py`: the other decorators in the tree are
# `lru_cache`, `functools.cache`, `cached_property` (all result-preserving) and
# `wraps` (metadata only), plus `staticmethod`/`classmethod`/`property` and one
# `contextmanager`, none of which a filter body can reach as a call.
DELEGATING_LAZY_DECORATORS = {
    "keep_lazy": "`keep_lazy(SafeString)`: returns a lazy proxy for a lazy argument",
    "keep_lazy_text": "`keep_lazy(str)`: the same, over the text helpers",
}

# `str` methods checked by metric B that do **not** delegate, listed so the
# exclusion is a decision rather than an omission.
CODE_POINT_LOCAL_STR_METHODS = {"upper"}

# --- Metric D: the same question asked of our Rust bodies --------------------

# Metrics A–C are derived from `defaultfilters.py` because at planning time the
# Rust bodies did not exist. Metric D asks the bodies directly, and is the only
# one of the four that fails when someone *edits a body* — which is the claim
# the delegating set is for.
HOST_RS = PROJECT_ROOT / "crates/template-core/src/filters/host.rs"
FILTER_MODULES = HOST_RS.parent
NON_FILTER_MODULES = {"host", "mod", "html_escape", "padding", "value"}

# The trait's section markers, in order. Metric D's entire content is *which of
# these sections count*, so the list is asserted against the trait rather than
# trusted: a section renamed, moved or added must fail a test before it can
# change metric D's answer. `// --- operand conversions … ---` was added for the
# reason the assertion exists — the rule was first proposed on the assumption
# that the sections were already clean, and four arithmetic methods were sitting
# inside "locale, language and the clock" with `gettext`.
TRAIT_SECTIONS = (
    "the Python value protocol",
    "the escaping protocol: one call, four branches",
    "the one ordering operation Rust cannot express",
    "the type questions Rust cannot answer",
    "CPython string-method semantics",
    "engines whose state is not the filter's argument",
    "locale, language and the clock",
    "operand conversions at the source's own statement boundary",
)

# Which sections make a body delegating. The last is deliberately absent: each
# of its four methods sits where the source's own statement does, so a body that
# calls one has not handed its *algorithm* over — `divisibleby` keeps its
# `== 0`, and `int_modulo` is the conversion `int(a) % int(b)` bundled to avoid
# a second `int()` on an object whose `__int__` may have side effects.
DELEGATING_SECTIONS = frozenset(
    {
        "CPython string-method semantics",
        "engines whose state is not the filter's argument",
        "locale, language and the clock",
    }
)

# A host call, matched **across whitespace**: `truncatewords` writes
# `call.host\n            .truncate(…)`, and the first version of this
# derivation used `host\.(\w+)\(`, which cannot see it — reporting 22 where the
# answer is 24 and mis-attributing two filters to the wrong side. The parse is a
# claim of the same kind as any number it replaces; this one is written down.
_HOST_CALL = re.compile(r"\bhost\s*\.\s*(\w+)\s*\(")
_SECTION_MARKER = re.compile(r"^\s*// --- (.+?) -+$", re.MULTILINE)


def _tree():
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _top_level_defs():
    """Every top-level `def` of `defaultfilters.py`, by name."""
    return {
        node.name: node
        for node in _tree().body
        if isinstance(node, ast.FunctionDef)
    }


def _decorator_names(node):
    """The bare names and attribute names on a def's decorators."""
    names = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _registered_name(node):
    """The name `@register.filter` gives this def, or `None` if it has none.

    Handles all **four** forms in the file. Three are calls —
    `@register.filter("name")`, `@register.filter("name", is_safe=True)` and
    `@register.filter(is_safe=False)` — where the first positional argument, when
    there is one, is the registered name. The fourth is the bare
    `@register.filter`, used by exactly one filter, `cut`, where the decorated
    function is itself the `name` argument and the def's own name is registered.

    Missing that fourth form is worth a note, because it fails *quietly*: `cut`
    drops out of the filter set, taking its two `django.utils` references with
    it, and a derivation that counts by reference reports 30 where the answer is
    31. Derived from the source rather than read off `_decorated_function`, so it
    does not depend on a Django internal a bump could move.
    """
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            target = decorator.func
            if isinstance(target, ast.Attribute) and target.attr == "filter":
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    return decorator.args[0].value
                return node.name
        elif isinstance(decorator, ast.Attribute) and decorator.attr == "filter":
            return node.name
    return None


def _filter_defs_by_registry_name():
    """Registry name -> the top-level def that implements it."""
    out = {}
    for node in _tree().body:
        if not isinstance(node, ast.FunctionDef):
            continue
        name = _registered_name(node)
        if name is not None:
            out[name] = node
    return out


def _filter_def_names():
    """The `def` names that implement a registered filter.

    Note this is *not* `set(_filter_defs_by_registry_name())`, which is keyed by
    registry name: the seven renames make the two differ, and subtracting the
    registry names from the def names would report those seven as helpers.
    """
    return {node.name for node in _filter_defs_by_registry_name().values()}


def _import_map():
    """Local name -> fully-qualified path, from `defaultfilters.py`'s imports."""
    out = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("django"):
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _referenced_utilities(func):
    """Every imported `django.utils` name this body mentions.

    Resolves through attribute chains, so `formats.number_format` counts as
    `django.utils.formats` — the *module* — which matters because `date` and
    `filesizeformat` reach their state through the module, not through a named
    utility.
    """
    imports = _import_map()
    found = set()
    for node in ast.walk(func):
        base = node
        while isinstance(base, ast.Attribute):
            base = base.value
        if isinstance(base, ast.Name) and base.id in imports:
            found.add(imports[base.id])
    return found


def _str_method_calls(func):
    """The `str` method names this body calls."""
    return {
        node.func.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.islower()
    }


def _defining_module_source(utility):
    """The source file a fully-qualified utility path lives in."""
    parts = utility.split(".")
    for length in range(len(parts), 0, -1):
        stem = DJANGO_CHECKOUT / ("/".join(parts[:length]) + ".py")
        if stem.exists():
            return stem
        package = DJANGO_CHECKOUT / ("/".join(parts[:length]) + "/__init__.py")
        if package.exists():
            return package
    raise AssertionError(f"no module file for {utility}")


def _module_imports(utility):
    """Every module name the utility's own file imports."""
    tree = ast.parse(_defining_module_source(utility).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _in_a_stateful_module(utility):
    """The coarse metric: the utility's *file* imports something stateful."""
    imports = _module_imports(utility)
    return any(
        name == state or name.startswith(state + ".")
        for name in imports
        for state in STATE_IMPORTS
    )


def _utilities_by_registry_name():
    return {
        name: _referenced_utilities(node)
        for name, node in _filter_defs_by_registry_name().items()
    }


def delegating_filters_by_utils():
    """Metric A, derived — the 18.

    A filter delegates iff the utility its body references bottoms out, within
    that utility's own module, in runtime state Rust cannot hold (settings,
    locale, the active language, the clock) or in a Python-stdlib table-or-parser
    port (`HTMLParser`, `textwrap`, `json`, `unicodedata`, `pprint`,
    `urllib.parse`).

    Classification is per-UTILITY, not per-module. Two traps were hit while
    measuring and both are worth knowing: a `from django.utils import formats`
    binds a MODULE, so looking up `formats` *inside* `formats.py` finds nothing,
    and `gettext` resolves through a package `__init__` that rebinds it.
    """
    out = set()
    for name, utilities in _utilities_by_registry_name().items():
        for utility in utilities:
            if utility not in UTILITY_VERDICTS:
                raise AssertionError(
                    f"`{name}` references {utility}, which has no verdict in "
                    f"UTILITY_VERDICTS; classify it rather than letting it read "
                    f"as a non-delegation"
                )
            if UTILITY_VERDICTS[utility][0]:
                out.add(name)
    return out


def delegating_filters_by_string_method():
    """Metric B, derived — the 2.

    A filter delegates iff its body calls a `str` method whose result is not a
    function of the code point alone. This is the metric that moved the set from
    18 to 20, which is why the two are counted separately rather than merged into
    a hand-checked list of 20.
    """
    out = set()
    for name, node in _filter_defs_by_registry_name().items():
        if _str_method_calls(node) & set(DELEGATING_STR_METHODS):
            out.add(name)
    return out


def _def_decorators(utility):
    """The decorator names on the def a fully-qualified path names, or `None`.

    `None` means the path names something that is not a top-level def — a
    module (`django.utils.formats`), or a name the module rebinds. That is a
    fact about the path, not a "this has no decorators" answer, so it is
    returned as `None` rather than as an empty set.
    """
    source = _defining_module_source(utility)
    wanted = utility.rsplit(".", 1)[-1]
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == wanted:
            return _decorator_names(node)
    return None


def filters_reaching_a_lazy_helper_without_stringfilter():
    """Metric C's candidates, derived — the 5.

    A filter is a candidate iff its body references a helper whose own `def`
    carries a result-changing lazy decorator, and the filter's own `def` carries
    no `@stringfilter` — which would have resolved the `Promise` with `str(first)`
    before the body ran, making the decorator's laziness branch unreachable.

    The decorator is read off the **helper's** `def` in its own module, not off
    the import site, because that is where the wrapper actually is; and the
    filter's own decorators are read off `defaultfilters.py`. Both come from the
    pinned tree.
    """
    out = set()
    imports = _import_map()
    for name, node in _filter_defs_by_registry_name().items():
        if "stringfilter" in _decorator_names(node):
            continue
        for utility in _referenced_utilities(node):
            decorators = _def_decorators(utility)
            if decorators and decorators & set(DELEGATING_LAZY_DECORATORS):
                out.add(name)
    return out


def delegating_filters_by_lazy_decorator():
    """Metric C — the 1, and it is the candidates plus a verdict each.

    The derivation above gives the five filters that reach a lazy-decorated
    helper with no `@stringfilter` in between; which of them *reproduces* the
    helper rather than calling it is a property of our Rust bodies and cannot be
    read off `defaultfilters.py`, so it is a verdict table in the
    `UTILITY_VERDICTS` shape. An unclassified candidate raises rather than
    reading as a non-delegation.

    This is the axis metrics A and B cannot see. Both scored `phone2numeric`
    clean, and it was still not Rust's — which is why the rule is a third clause
    in §D2 rather than a note on one filter.
    """
    out = set()
    for name in filters_reaching_a_lazy_helper_without_stringfilter():
        if name not in LAZY_HELPER_VERDICTS:
            raise AssertionError(
                f"`{name}` reaches a result-changing decorator with no "
                f"`@stringfilter` in between and has no verdict in "
                f"LAZY_HELPER_VERDICTS; classify it rather than letting it read "
                f"as a non-delegation"
            )
        if LAZY_HELPER_VERDICTS[name][0]:
            out.add(name)
    return out


def filters_referencing_any_django_utils():
    """The 31 — the neighbouring count that sweeps in `mark_safe`."""
    return {name for name, refs in _utilities_by_registry_name().items() if refs}


def filters_in_a_stateful_module():
    """The 27 — the neighbouring count that classifies per module."""
    return {
        name
        for name, refs in _utilities_by_registry_name().items()
        if any(_in_a_stateful_module(utility) for utility in refs)
    }


def trait_sections():
    """The trait's `// --- … ---` markers, in order, read off the pinned file."""
    return tuple(_SECTION_MARKER.findall(HOST_RS.read_text(encoding="utf-8")))


def trait_method_sections():
    """Trait method name -> the section marker above it.

    Derived from `host.rs`, so a method that moves between sections changes
    metric D's answer rather than silently changing what the rule means.
    """
    lines = HOST_RS.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("pub trait FilterHost")
    )
    markers = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := _SECTION_MARKER.match(line))
    ]
    sections = {}
    for i in range(start, len(lines)):
        if lines[i] == "}":
            break
        method = re.match(r"    fn (\w+)\s*\(", lines[i])
        if method:
            above = [title for index, title in markers if index < i]
            sections[method.group(1)] = above[-1] if above else None
    return sections


def filter_module_sources():
    """Module stem -> its source, for the 57 filter bodies.

    The support modules are named rather than counted, so 57 stays a fact about
    the filter set rather than an arithmetic accident of a `glob`.
    """
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in FILTER_MODULES.glob("*.rs")
        if path.stem not in NON_FILTER_MODULES
    }


def host_calls(source):
    """The host methods a filter module's non-test, non-comment code calls.

    `#[cfg(test)]` is cut first, or a module's own test — `host.str_lower("ΟΣ")`
    — reads as a call the body makes, which is the same class of parse claim as
    the whitespace above.
    """
    body = source.split("#[cfg(test)]")[0]
    body = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("//")
    )
    return set(_HOST_CALL.findall(body))


def delegating_filters_in_rust():
    """Metric D — the delegating set, derived from our bodies.

    A filter delegates iff its body calls a host method declared in a
    delegating section of the trait. This is the same question metrics A–C ask
    of `defaultfilters.py`, asked of the thing that actually ships; it is the
    only one of the four that reddens when a body is rewritten.
    """
    sections = trait_method_sections()
    return {
        name
        for name, source in filter_module_sources().items()
        if any(sections.get(call) in DELEGATING_SECTIONS for call in host_calls(source))
    }


class FilterInventoryTests(unittest.TestCase):
    def test_the_registry_is_exactly_these_names(self):
        self.assertEqual(set(defaultfilters.register.filters), REGISTERED)

    def test_the_three_counts_that_have_been_confused(self):
        """57 registered, 59 top-level `def`s, 29 `@stringfilter` bodies.

        Each is correct for its own question, and the gaps are named sets rather
        than arithmetic.
        """
        self.assertEqual(len(REGISTERED), 57)
        self.assertEqual(len(_top_level_defs()), 59)
        self.assertEqual(len(STRINGFILTER), 29)

    def test_the_two_helpers_are_the_only_non_filter_defs(self):
        """59 = 57 + 2, and the 2 are named.

        `_property_resolver` and `stringfilter` are top-level `def`s that a
        template cannot invoke. Counted without naming them, 59 and 57 would look
        like a discrepancy; named, it is a partition.
        """
        self.assertEqual(
            set(_top_level_defs()) - _filter_def_names(),
            HELPER_DEF_NAMES,
        )

    def test_the_seven_renames_are_the_only_name_mismatches(self):
        """Derived from the decorators, not read off `_decorated_function`."""
        self.assertEqual(
            set(_filter_defs_by_registry_name()), REGISTERED
        )
        mismatches = {
            name: node.name
            for name, node in _filter_defs_by_registry_name().items()
            if node.name != name
        }
        self.assertEqual(mismatches, REGISTRY_NAMES_THAT_ARE_NOT_DEF_NAMES)
        self.assertEqual(
            set(_top_level_defs()) - REGISTERED, DEF_NAMES_THAT_ARE_NOT_REGISTRY_NAMES
        )

    def test_the_stringfilter_set_is_exactly_these_names(self):
        """The 29, by `def` name.

        Excludes the four renamed-and-undecorated filters (`slice`, `timesince`,
        `timeuntil`, `phone2numeric`) and includes the three renamed ones that do
        carry it (`escape_filter`, `escapejs_filter`, `linebreaks_filter`) — so
        this is not "the 57 minus something" and is asserted by name.
        """
        decorated = {
            node.name
            for node in _top_level_defs().values()
            if "stringfilter" in _decorator_names(node)
        }
        self.assertEqual(decorated, STRINGFILTER)

    def test_escape_is_not_the_registered_escape(self):
        """`defaultfilters.escape` is `django.utils.html.escape`, unwrapped.

        The registered one is `escape_filter`, from `@register.filter("escape",
        is_safe=True)`. The module-level name is the *utility*, which takes no
        `arg` and does not mark safe — so anything that reaches for
        `defaultfilters.escape` to compare against has reached for the wrong
        object, and the difference is free to pin.
        """
        self.assertIsNot(
            defaultfilters.escape, defaultfilters.register.filters["escape"]
        )
        self.assertEqual(
            defaultfilters.register.filters["escape"].__name__, "escape_filter"
        )

    def test_the_delegation_set_is_derived(self):
        """18 by utilities, 3 by stdlib engine, 2 by string method, 1 by lazy
        decorator, 24 in union — and metric D, asked of our own bodies, agrees.

        Each third is asserted separately against its own literal, and the failure
        message prints the computed sets, so a future drift reports "19 by utils,
        2 by string method" rather than just red.

        The last assertion is the one that makes the set a claim about the code
        that ships. A–C are read off `defaultfilters.py`, so all three of them
        answer a question about *Django*; only D reddens when a body is rewritten
        to stop delegating, which is exactly the event the set exists to catch.
        """
        by_utils = delegating_filters_by_utils()
        by_stdlib = set(DELEGATING_BY_STDLIB_ENGINE)
        by_method = delegating_filters_by_string_method()
        by_decorator = delegating_filters_by_lazy_decorator()
        in_rust = delegating_filters_in_rust()
        union = by_utils | by_stdlib | by_method | by_decorator
        # Printed, not just asserted. The plan's Task 3 Step 1 runs this file
        # while the Rust side is still a stub, so the run ends in an
        # `AttributeError` from `_native.filter_names` — these four lines are
        # what makes the derivation's answer visible in a run that fails for a
        # reason that has nothing to do with the derivation.
        print(
            f"\n  utils={len(by_utils)} {sorted(by_utils)}"
            f"\n  stdlib_engine={len(by_stdlib)} {sorted(by_stdlib)}"
            f"\n  string_method={len(by_method)} {sorted(by_method)}"
            f"\n  lazy_decorator={len(by_decorator)} {sorted(by_decorator)}"
            f"\n  union={len(union)} rust_derived={len(in_rust)}"
        )
        self.assertEqual(
            by_utils, DELEGATING_BY_UTILS,
            f"by utils: {sorted(by_utils)} ({len(by_utils)})",
        )
        self.assertEqual(
            by_stdlib, set(DELEGATING_BY_STDLIB_ENGINE),
            f"by stdlib engine: {sorted(by_stdlib)} ({len(by_stdlib)})",
        )
        self.assertEqual(
            by_method, DELEGATING_BY_STRING_METHOD,
            f"by string method: {sorted(by_method)} ({len(by_method)})",
        )
        self.assertEqual(
            by_decorator, DELEGATING_BY_LAZY_DECORATOR,
            f"by lazy decorator: {sorted(by_decorator)} ({len(by_decorator)})",
        )
        self.assertEqual(
            union, DELEGATING_FILTERS,
            f"union: {sorted(union)} ({len(union)})",
        )
        self.assertEqual(
            in_rust, DELEGATING_FILTERS,
            "the Rust bodies and the Python source disagree about who delegates:"
            f" only in Rust: {sorted(in_rust - DELEGATING_FILTERS)};"
            f" only in Python: {sorted(DELEGATING_FILTERS - in_rust)}",
        )
        self.assertEqual(len(DELEGATING_FILTERS), 24)

    def test_the_trait_sections_are_the_delegation_boundary(self):
        """Metric D's whole content, asserted against the trait it reads.

        The rule is "a body delegates iff it calls a method declared in a
        delegating section". The list of sections is therefore the rule, and a
        section renamed, moved or added must fail here before it can silently
        change metric D's answer — which is not hypothetical: the
        `// --- operand conversions … ---` section was created because four
        arithmetic methods were sitting inside "locale, language and the clock"
        with `gettext`, and the rule was first stated as though the sections were
        already clean.
        """
        self.assertEqual(
            trait_sections(), TRAIT_SECTIONS,
            f"the trait's markers: {trait_sections()}",
        )
        # Every section must have a verdict: a new section that is not named in
        # `DELEGATING_SECTIONS` would silently read as non-delegating.
        self.assertTrue(
            DELEGATING_SECTIONS <= set(TRAIT_SECTIONS),
            f"delegating sections with no such marker: "
            f"{sorted(DELEGATING_SECTIONS - set(TRAIT_SECTIONS))}",
        )
        sections = trait_method_sections()
        called = [s for s in sections.values() if s is not None]
        self.assertEqual(
            set(called) - set(TRAIT_SECTIONS), set(),
            "a method sits under a marker that is not in the list",
        )

    def test_the_filter_modules_are_the_registered_filters(self):
        """Metric D reads 57 bodies; this is what says they are the 57.

        Without it, a support module added to `filters/` (`mod`, `padding`, …)
        would be read as a filter and could only move metric D's answer in the
        direction of a false delegation.
        """
        self.assertEqual(
            set(filter_module_sources()), REGISTERED,
            f"modules: {sorted(filter_module_sources())}",
        )

    def test_host_calls_ignore_test_code(self):
        """The parse metric D rests on, checked on the shape that broke it.

        `#[cfg(test)]` cut first, or a module's own `host.str_lower("ΟΣ")` reads
        as a call its body makes; and the match is across whitespace, because
        `truncatewords` writes `call.host\\n            .truncate(…)`. The first
        version of this derivation used `host\\.(\\w+)\\(` and reported 22 where
        the answer is 24, mis-attributing `truncatewords` and
        `truncatewords_html` to the wrong side — the first count dispute whose
        ambiguity was in the measuring instrument rather than the metric.
        """
        self.assertEqual(host_calls("call.host.truncate(x)"), {"truncate"})
        self.assertEqual(
            host_calls("call.host\n            .truncate(x)"), {"truncate"}
        )
        self.assertEqual(
            host_calls("call.host.f(a);\n#[cfg(test)]\nmod t { host.g(b) }"),
            {"f"},
        )
        self.assertEqual(host_calls("// host.h(x)\ncall.host.k(y)"), {"k"})
        # And both multi-line truncators are in the derived set, which is what
        # the whitespace-tolerant match buys.
        for name in ("truncatewords", "truncatewords_html"):
            self.assertIn(name, delegating_filters_in_rust())

    def test_the_lazy_decorator_metric_cuts_its_five_to_one(self):
        """Metric C's candidates, its verdicts, and the cut from 5 to 1.

        The five are derived from the pinned tree and asserted as a set, so a
        Django bump that adds or removes a candidate fails here. The verdicts are
        asserted total in both directions — an unclassified candidate would
        silently read as a non-delegation, and a stale verdict would outlive the
        candidate it describes — and the four non-delegating ones each say which
        host call inherits the decorator instead of reproducing it.
        """
        reaching = filters_reaching_a_lazy_helper_without_stringfilter()
        self.assertEqual(
            reaching, REACHING_A_LAZY_HELPER_WITHOUT_STRINGFILTER,
            f"reaching a lazy helper: {sorted(reaching)} ({len(reaching)})",
        )
        self.assertEqual(
            set(LAZY_HELPER_VERDICTS) - reaching, set(),
            "these verdicts are for filters that no longer reach a lazy helper",
        )
        self.assertEqual(
            reaching - delegating_filters_by_lazy_decorator(),
            REACHING_A_LAZY_HELPER_WITHOUT_STRINGFILTER - DELEGATING_BY_LAZY_DECORATOR,
        )

    def test_every_utility_verdict_is_referenced_and_total(self):
        """Both directions, so a verdict cannot be stale or missing.

        Missing is the dangerous one: an unclassified utility would silently read
        as a non-delegation, and the derivation would quietly shrink.
        """
        referenced = {u for refs in _utilities_by_registry_name().values() for u in refs}
        self.assertEqual(
            referenced - set(UTILITY_VERDICTS), set(),
            "these utilities are referenced but unclassified",
        )
        self.assertEqual(
            set(UTILITY_VERDICTS) - referenced, set(),
            "these verdicts are for utilities no filter references any more",
        )

    def test_the_neighbouring_counts_are_what_they_are(self):
        """31 and 27, both measured, both the wrong question.

        Pinned because §D2's prose states them and a prose number can be wrong
        indefinitely — and the plan's 26 for the second did not follow from its
        own list of swept names, which is why the metric is stated precisely in
        `filters_in_a_stateful_module` instead of the number being copied.
        """
        self.assertEqual(
            len(filters_referencing_any_django_utils()), REFERENCING_ANY_DJANGO_UTILS
        )
        self.assertEqual(
            len(filters_in_a_stateful_module()), IN_A_MODULE_THAT_IMPORTS_STATE,
            f"swept in: {sorted(filters_in_a_stateful_module())}",
        )

    def test_upper_and_capfirst_stay_rusts(self):
        """The two `.upper()` callers, and the measurement that excludes them.

        `str.upper()` has no context rule, so Task 1's per-code-point table is
        complete for both. Asserted rather than assumed, because the opposite
        assumption is exactly what the Final_Sigma finding punished.
        """
        for name in ("upper", "capfirst"):
            with self.subTest(name=name):
                self.assertIn("upper", _str_method_calls(_filter_defs_by_registry_name()[name]))
                self.assertNotIn(name, delegating_filters_by_string_method())
        self.assertIn("upper", CODE_POINT_LOCAL_STR_METHODS)

    def test_the_parity_target_is_genuinely_djangos_registry(self):
        """Guard against self-comparison, restated for the file it now names.

        M9 wrote this row against a hazard that had not arrived: once the shim
        interposes `defaultfilters`, the name resolves to our shell and "any
        inventory read from it would be measuring our registry against itself".
        M11 Task 2 is that arrival, and the row's answer is the import at the top
        of this module — `defaultfilters` is now `load_pinned("defaultfilters.py")`,
        so the object this asserts about is Django's file and not a transcription
        of it. That is the strongest available form of the claim, and it is why
        the row's *body* did not have to change: the fix is the subject, not the
        assertion.

        `tests/parity_target.py` gives the weaker form for targets that cannot be
        re-read this way (a class or function the harness has to hold live): the
        file is Django's *or* a transcription gated against it. That form does not
        apply here and is not used, because the whole inventory — Metrics A–C, the
        `def`/decorator tables, the registered-name map — is read off `SOURCE`, and
        reading all of it from the pinned file is both simpler and stronger than
        warranting a copy.
        """
        source_file = inspect.getsourcefile(defaultfilters)
        self.assertTrue(
            source_file.startswith(str(DJANGO_CHECKOUT)),
            f"inventory target is not the pinned checkout: {source_file}",
        )

    def test_the_interposed_module_registers_the_pinned_registry(self):
        """The other direction: the module that ships registers Django's set.

        Reading the inventory from the pin (above) makes Metrics A–C honest about
        upstream, and it opens the gap this row closes. The interposition made
        "the source the pin names" and "the registry the engine dispatches
        through" two different objects for the first time — the file under
        `_impl/` is still exactly the pin plus a marker, so the two *should*
        agree, but nothing in this module would have noticed if the shim's
        registration were broken, since every read above now goes to the pin.

        This is the module's own "from both directions" rule applied to the
        interposition: `_registered` is read off the pinned file, `_live` off
        `django.template.defaultfilters` *as the shim serves it*, and the row is
        that the two agree name for name and flag for flag. It fails loudly if
        the interposition moves, which is the property `parity_target.py` asks of
        a comparison target.

        **It calls `install()` rather than assuming the shim.** Every other row
        in this class is a read of the pin and needs no shim; this one is about
        the module that ships, and the suite's own runner installs the shim at
        interpreter start (`scripts/run_unit_tests.sh`), so the call is a no-op
        there. It is written anyway because the alternative is a row that, run
        by hand in an unshimmed interpreter, asserts Django's registry equals
        Django's registry and passes — the `install()` raises instead, which is
        what a precondition should do.
        """
        import importlib

        from django_rust_templates import _shim

        _shim.install()

        live = importlib.import_module("django.template.defaultfilters")
        self.assertIn("_impl", live.__file__, live.__file__)
        self.assertEqual(
            set(live.register.filters), set(defaultfilters.register.filters)
        )
        for name, pinned_func in defaultfilters.register.filters.items():
            with self.subTest(name=name):
                ours = live.register.filters[name]
                for flag in ("is_safe", "needs_autoescape", "expects_localtime"):
                    self.assertEqual(
                        getattr(ours, flag, None),
                        getattr(pinned_func, flag, None),
                        f"{name}.{flag}",
                    )


class RustFilterRegistryTests(unittest.TestCase):
    """The Rust registry against Django's, read from the pinned file.

    "Django's" was the live module until M11 interposed the name; the row above
    this class is what keeps the two equal, so which copy is read here does not
    change the claim.

    This is the file's half of §10.3's "incl. `FilterFlags` round-tripping": the
    flags are read back out of the Rust table and compared to the attributes
    Django actually set, filter by filter, rather than to a second Rust literal.
    """

    def _native(self):
        from django_rust_templates import _native

        return _native

    def test_the_rust_registry_is_djangos_filters(self):
        native = self._native()
        self.assertEqual(sorted(native.filter_names()), sorted(REGISTERED))

    def test_the_flags_round_trip(self):
        native = self._native()
        for name in sorted(REGISTERED):
            with self.subTest(name=name):
                registered = defaultfilters.register.filters[name]
                self.assertEqual(
                    native.filter_flags(name),
                    (
                        getattr(registered, "is_safe", False),
                        getattr(registered, "needs_autoescape", False),
                        getattr(registered, "expects_localtime", False),
                    ),
                    f"`{name}`'s flags differ from Django's",
                )

    def test_the_stringfilter_set_matches_the_rust_table(self):
        """The 29, by **registry** name, against `_native.stringfilter_filters()`.

        This is the reader for `FilterEntry.stringfilter`, and the reason that
        field is a transcription rather than something Rust derives: which bodies
        Django decorates is *Django's* fact, and the Rust table is a hand-typed
        copy of it. Nothing in `filters/mod.rs` can disagree with its own 57 rows
        — a test there would be the same list read twice — so the disagreement has
        to come from this side, from the decorators in the pinned
        `defaultfilters.py`. That is also why `_native` exposes the field at all.

        `STRINGFILTER` is keyed by `def` name, because that is where the decorator
        sits, and the Rust table by registry name. The three renamed-and-decorated
        bodies (`escape_filter`, `escapejs_filter`, `linebreaks_filter`) are where
        the two differ, and the mapping is **derived** from the decorators rather
        than written as a three-entry translation table: a hand-written table is
        exactly the copy that would agree with a wrong Rust row, since both would
        be ours. The set assertion on `by_def_name` is not decoration — it is what
        catches a `str`-keyed dict quietly losing a def whose registry name is
        already taken.
        """
        native = self._native()
        by_def_name = {
            node.name: registry_name
            for registry_name, node in _filter_defs_by_registry_name().items()
            if "stringfilter" in _decorator_names(node)
        }
        self.assertEqual(set(by_def_name), STRINGFILTER)
        self.assertEqual(
            set(by_def_name.values()),
            set(native.stringfilter_filters()),
            "the Rust `stringfilter` column is not the 29 decorated bodies: "
            f"django={sorted(by_def_name.values())}, "
            f"rust={sorted(native.stringfilter_filters())}",
        )
        # 29 by name and 29 in the table, so the comparison above cannot pass by
        # both sides being short in the same way.
        self.assertEqual(len(native.stringfilter_filters()), 29)

    def test_cut_and_escapejs_have_no_is_safe_but_report_false(self):
        """The `bool`/`hasattr` gap, asserted on both sides.

        Django's attribute is **absent**; the Rust table says `False`, because
        `getattr(..., "is_safe", False)` is what every reader of the attribute
        does. Asserting both halves is what stops a future reader concluding that
        Django's attribute is always present.
        """
        native = self._native()
        for name in sorted(IS_SAFE_ABSENT):
            with self.subTest(name=name):
                self.assertFalse(hasattr(defaultfilters.register.filters[name], "is_safe"))
                self.assertIs(native.filter_flags(name)[0], False)

    def test_every_other_filter_has_the_attribute(self):
        """The complement, so "absent" stays a two-name exception."""
        present = {
            name
            for name in REGISTERED
            if hasattr(defaultfilters.register.filters[name], "is_safe")
        }
        self.assertEqual(present, REGISTERED - IS_SAFE_ABSENT)

    def test_no_filter_carries_the_two_unset_flags(self):
        """`do_not_call_in_templates` and `alters_data`, measured absent on all 57.

        §D5's `FilterFlags` has three fields because Django's filters set three;
        these two are tag-side attributes, and asserting their absence is what
        stops someone adding them to `FilterFlags` "for symmetry".
        """
        for name in sorted(REGISTERED):
            registered = defaultfilters.register.filters[name]
            with self.subTest(name=name):
                self.assertFalse(hasattr(registered, "do_not_call_in_templates"))
                self.assertFalse(hasattr(registered, "alters_data"))


if __name__ == "__main__":
    unittest.main()
