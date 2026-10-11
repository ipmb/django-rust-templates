"""Templates the materialiser is exercised on, and the check that they cover it.

Two families, and the second is the one that matters. The bench rows are the
real-world shapes; the tag templates are one per `{% tag %}` so that a class in
`NODE_CLASSES` with no template is a test failure rather than an untested arm.

The coverage check is a predicate over `NODE_CLASSES`, not a count. A count pins
churn — adding a tag would fail a number that means nothing — while "every entry
in the registry is produced by some source here" is the property that has to hold,
and it fails for exactly one reason.

**This module carries no `def test_` and no `TestCase`, and that is load-bearing.**
`tests/test_collected_tests.py::test_no_module_the_pattern_misses_holds_test_rows`
fails for any module outside the `test_*.py` pattern that holds a row, which is
how the tree catches a test file discovery drops. A corpus module with a row in it
would be that failure, correctly.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import bench_corpus  # noqa: E402

from tests.parser_corpus import LIBRARIES as _PARSER_LIBRARIES  # noqa: E402

# **The two libraries these rows reach, and the `i18n` path is borrowed rather
# than respelled.** `parser_corpus`'s own comment names the failure a second
# spelling causes ("two spellings is how a corpus stops grading the engine"), so
# the `i18n` entry is read from that map instead of duplicated here.
#
# **The rest of that map is deliberately *not* taken.** Its other entries are
# `template_tests.*`, which resolve only with `django_suite/` on `sys.path` —
# `test_parser_parity.py` arranges that at its own import, and without it
# `Engine(libraries=…)` raises `Invalid template library … No module named
# 'template_tests'` (measured). This corpus is about node classes, none of which
# needs those five, so it does not take on that path arrangement to carry them.
LIBRARIES = {
    "i18n": _PARSER_LIBRARIES["i18n"],
    # **These four are this package's transcriptions and not `django.templatetags.*`,
    # and the difference is a node class, not a spelling.** `parser.py` builds
    # `NODE_CLASSES` from `django_rust_templates.engine.templatetags.*`, so a
    # `{% load %}` naming *Django's* library hands the tag to a compile function
    # that builds *Django's* class instead — measured, as
    # `fingerprint fell through to an address-bearing repr for TimezoneNode:
    # '<django.templatetags.tz.TimezoneNode object at 0x…>'`, which is also how the
    # oracle caught it. `parser_corpus` reaches its `i18n` path the same way.
    "static": "django_rust_templates.engine.templatetags.static",
    "tz": "django_rust_templates.engine.templatetags.tz",
    "l10n": "django_rust_templates.engine.templatetags.l10n",
    "cache": "django_rust_templates.engine.templatetags.cache",
}

# One source per node class the registry carries. `{% cycle %}` appears twice on
# purpose: the second occurrence is the identity case, and a corpus whose only
# cycle template has one occurrence cannot see it.
TAG_SOURCES = [
    ("text", "hello {{ name }}"),
    ("if", "{% if a %}x{% elif b %}y{% else %}z{% endif %}"),
    ("if_nested", "{% if a %}{% if b %}n{% endif %}{% endif %}"),
    ("if_operators", "{% if a == b %}e{% endif %}{% if a != b %}n{% endif %}"),
    ("for", "{% for x in xs %}{{ x }}{% empty %}none{% endfor %}"),
    ("for_nested", "{% for x in xs %}{% for y in x %}{{ y }}{% endfor %}{% endfor %}"),
    # Django's `{% with %}` takes the kwarg form (`a=1 b=2`) or the `as` form
    # (`a as b`); `{% with total = a b %}` is neither, and dies at
    # `ParseError: 'with' received an invalid token: 'b'` (measured).
    ("with", "{% with a=1 b=2 %}{{ a }}{{ b }}{% endwith %}"),
    ("autoescape", "{% autoescape off %}{{ a }}{% endautoescape %}"),
    ("verbatim", "{% verbatim %}{{ not_a_var }}{% endverbatim %}"),
    ("cycle_twice", "{% cycle 'a' 'b' as c %}{% cycle c %}"),
    ("resetcycle", "{% cycle 'a' 'b' as c %}{% resetcycle c %}"),
    ("firstof", "{% firstof a b 'fallback' %}"),
    ("spaceless", "{% spaceless %} <b> a </b> {% endspaceless %}"),
    ("ifchanged", "{% for x in xs %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}"),
    ("templatetag", "{% templatetag openblock %}"),
    ("url_ish", "{% url 'view' a=1 b=2 %}"),
    # `{% blocktranslate %}` is `django.templatetags.i18n`'s and is **not a builtin**:
    # the `{% load %}` is load-bearing, not decoration. Measured — without it this
    # row dies at `ParseError: Invalid block tag on line 1: 'blocktranslate'`.
    ("blocktranslate", "{% load i18n %}{% blocktranslate %}Hello {{ name }}"
                       "{% endblocktranslate %}"),
    ("blocktranslate_plural", "{% load i18n %}{% blocktranslate count n=items %}"
                              "{{ n }} item{% plural %}{{ n }} items"
                              "{% endblocktranslate %}"),
    ("filter_args", "{{ a|default:'x' }} {{ b|join:', ' }}"),
    ("string_if_invalid", "{{ missing }}"),
    ("literal", "{{ 1.5 }} {{ 99999999999999999999 }} {{ True }} {{ None }}"),
    ("extends", "{% extends 'base.html' %}{% block b %}{{ a }}{% endblock %}"),
    ("include", "{% include 'p.html' with a=1 only %}"),
    # The twenty entries the coverage row named when only the rows above existed.
    # Each `{% load %}` is required: `template_libraries` is what makes a `{% load %}`
    # resolve at all, and these tags are not builtins.
    ("cache", "{% load cache %}{% cache 500 key %}x{% endcache %}"),
    ("csrf_token", "{% csrf_token %}"),
    ("debug", "{% debug %}"),
    ("filter", "{% filter upper %}x{% endfilter %}"),
    ("get_available_languages", "{% load i18n %}{% get_available_languages as langs %}"),
    ("get_current_language", "{% load i18n %}{% get_current_language as lang %}"),
    ("get_current_language_bidi", "{% load i18n %}{% get_current_language_bidi as bidi %}"),
    ("get_current_timezone", "{% load tz %}{% get_current_timezone as zone %}"),
    ("get_language_info", "{% load i18n %}{% get_language_info for 'en' as li %}"),
    ("get_language_info_list", "{% load i18n %}{% get_language_info_list for xs as infos %}"),
    ("language", "{% load i18n %}{% language 'en' %}x{% endlanguage %}"),
    ("localtime", "{% load tz %}{% localtime on %}x{% endlocaltime %}"),
    ("localize", "{% load l10n %}{% localize on %}x{% endlocalize %}"),
    ("lorem", "{% lorem 2 p %}"),
    ("now", "{% now 'Y' %}"),
    ("get_static_prefix", "{% load static %}{% get_static_prefix %}"),
    ("regroup", "{% regroup xs by g as grouped %}"),
    ("timezone", "{% load tz %}{% timezone 'UTC' %}x{% endtimezone %}"),
    ("translate", "{% load i18n %}{% translate 'x' %}"),
    ("widthratio", "{% widthratio a b 100 %}"),
    ("comment", "{% comment %}hidden{% endcomment %}"),
    ("load", "{% load static %}{% static 'x.css' %}"),
]

BENCH_SOURCES = [
    ("bench/normal", bench_corpus.NORMAL),
    ("bench/complex", bench_corpus.COMPLEX),
    ("bench/large", bench_corpus.large_template()),
]

SOURCES = TAG_SOURCES + BENCH_SOURCES


def corpus_engine():
    """The engine the parity rows parse through — the libraries belong to it.

    **On the engine and not in the source.** `{% load %}` resolves through
    `Engine.template_libraries`, so a bare `Engine()` raises `'static' is not a
    registered tag library` and `Invalid block tag on line 1: 'blocktranslate'`
    (both measured). Three `NODE_CLASSES` entries are reachable only through
    those two libraries, so the coverage row cannot be satisfied without this.

    A **fresh** engine per call, because an engine carries mutable filter and tag
    registries: one row that rebinds a filter must not be visible to the next.
    """
    from django_rust_templates.engine.engine import Engine

    return Engine(libraries=LIBRARIES)
