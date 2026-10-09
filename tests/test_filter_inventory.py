"""Django's built-in filter inventory, pinned executably rather than in prose.

Modelled on `tests/test_tag_inventory.py`, and for the same reason: M4's plan has
carried the number of built-in filters, the number of `@stringfilter`-decorated
bodies, and the size of the **delegating** set since it was written, and this
project has had seven count disputes that were each two right answers to two
unstated questions. A number in a document can be wrong indefinitely; a number in
a test can be wrong once.

Four things are pinned here:

1. the three counts that have been confused — 57 registered filters, 59 top-level
   `def`s, 29 `@stringfilter` bodies — and the names in each set, so the gap
   between them is a named fact rather than an arithmetic accident;
2. the seven filters whose registered name is not their `def` name, because
   `defaultfilters.escape` **is not** the registered `escape` (it is the raw
   `django.utils.html.escape`, which takes no `arg` and does not mark safe) — the
   trap the plan's own review found;
3. the two delegating metrics of §D2, **derived here rather than transcribed**;
4. the Rust registry's names and flags against Django's live registry, including
   `cut`'s and `escapejs`'s **absent** `is_safe`.

Django is pinned at 6.0.9 under `.django/`. These assertions are meant to fail
loudly on a pin bump that adds or removes a filter — otherwise §10.3's milestone
table goes stale in silence, which is how the tag table got to three corrections.
"""

import ast
import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import defaultfilters  # noqa: E402

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

# Metric B: the body calls a `str` method whose result is not a function of the
# code point alone. `lower` and `title`, and *not* `upper`/`capfirst`: Task 1's
# exhaustive diff shows `str.upper()` is code-point-local (the same 28 code points
# as `char::to_uppercase`), while `str.lower()` applies Final_Sigma and
# `str.title()` lowercases each run's remainder.
DELEGATING_BY_STRING_METHOD = {"lower", "title"}

DELEGATING_FILTERS = DELEGATING_BY_UTILS | DELEGATING_BY_STRING_METHOD

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
    "django.utils.text.phone2numeric": (False, "Django's own hand table"),
}

# The `str` methods whose result is not a function of the code point alone.
DELEGATING_STR_METHODS = {
    "lower": "Final_Sigma: `\"ΟΣ\".lower()` is `\"ος\"`",
    "title": "lowercases each run's remainder, so Final_Sigma again",
}

# `str` methods checked by metric B that do **not** delegate, listed so the
# exclusion is a decision rather than an omission.
CODE_POINT_LOCAL_STR_METHODS = {"upper"}


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
        """18 by utilities, 2 by string method, 20 in union.

        Each half is asserted separately against its own literal, and the failure
        message prints the computed sets, so a future drift reports "19 by utils,
        2 by string method" rather than just red.
        """
        by_utils = delegating_filters_by_utils()
        by_method = delegating_filters_by_string_method()
        # Printed, not just asserted. The plan's Task 3 Step 1 runs this file
        # while the Rust side is still a stub, so the run ends in an
        # `AttributeError` from `_native.filter_names` — these three lines are
        # what makes the derivation's answer visible in a run that fails for a
        # reason that has nothing to do with the derivation.
        print(
            f"\n  utils={len(by_utils)} {sorted(by_utils)}"
            f"\n  string_method={len(by_method)} {sorted(by_method)}"
            f"\n  union={len(by_utils | by_method)}"
        )
        self.assertEqual(
            by_utils, DELEGATING_BY_UTILS,
            f"by utils: {sorted(by_utils)} ({len(by_utils)})",
        )
        self.assertEqual(
            by_method, DELEGATING_BY_STRING_METHOD,
            f"by string method: {sorted(by_method)} ({len(by_method)})",
        )
        self.assertEqual(
            by_utils | by_method, DELEGATING_FILTERS,
            f"union: {sorted(by_utils | by_method)} ({len(by_utils | by_method)})",
        )
        self.assertEqual(len(DELEGATING_FILTERS), 20)

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
        """Guard against self-comparison once M9 interposes the shim.

        The same trap `test_lexer_parity`'s source-file guard exists for: after
        M9, `django.template.defaultfilters` resolves to our shell, and any
        inventory read from it would be measuring our registry against itself.
        """
        source_file = inspect.getsourcefile(defaultfilters)
        self.assertTrue(
            source_file.startswith(str(DJANGO_CHECKOUT)),
            f"inventory target is not the pinned checkout: {source_file}",
        )


class RustFilterRegistryTests(unittest.TestCase):
    """The Rust registry against Django's live one.

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
