"""Small, deterministic templates for Django’s default and standard-library filters/tags.

These measure complete template operations, including dispatch and output conversion,
not isolated Rust function bodies. Inputs are illustrative, not exhaustive.
"""

from datetime import datetime, timedelta, timezone


def workloads():
    from django.http import QueryDict
    from bench_corpus import DJANGO_ONLY, Workload

    stamp = datetime(2024, 1, 15, 12, 30, tzinfo=timezone.utc)
    # name: (expression, value)
    filters = {
        "add": ("value|add:2", 40),
        "addslashes": ("value|addslashes", "It's a quote"),
        "capfirst": ("value|capfirst", "hello world"),
        "center": ("value|center:30", "hello"),
        "cut": ('value|cut:" "', "hello world"),
        "date": ('value|date:"Y-m-d"', stamp),
        "default": ('value|default:"fallback"', ""),
        "default_if_none": ('value|default_if_none:"fallback"', None),
        "dictsort": ('value|dictsort:"name"', [{"name": "Zoe"}, {"name": "Ada"}]),
        "dictsortreversed": (
            'value|dictsortreversed:"name"',
            [{"name": "Zoe"}, {"name": "Ada"}],
        ),
        "divisibleby": ("value|divisibleby:3", 42),
        "escape": ("value|escape", "<b>Tea & cake</b>"),
        "escapejs": ("value|escapejs", 'hello "world"\n'),
        "escapeseq": ('value|escapeseq|join:", "', ["<one>", "<two>"]),
        "filesizeformat": ("value|filesizeformat", 1234567),
        "first": ("value|first", ["first", "second"]),
        "floatformat": ("value|floatformat:2", 12.3456),
        "force_escape": ("value|force_escape", "<b>Tea & cake</b>"),
        "get_digit": ("value|get_digit:2", 12345),
        "iriencode": ("value|iriencode", "/café/?q=hello world"),
        "join": ('value|join:", "', ["one", "two", "three"]),
        "json_script": ('value|json_script:"data"', {"name": "<hello>", "count": 3}),
        "last": ("value|last", ["first", "second"]),
        "length": ("value|length", list(range(50))),
        "linebreaks": ("value|linebreaks", "First line\nSecond line\n\nNew paragraph"),
        "linebreaksbr": ("value|linebreaksbr", "First line\nSecond line"),
        "linenumbers": ("value|linenumbers", "First\nSecond\nThird"),
        "ljust": ("value|ljust:30", "hello"),
        "lower": ("value|lower", "HELLO WORLD"),
        "make_list": ("value|make_list", "hello"),
        "phone2numeric": ("value|phone2numeric", "1-800-FLOWERS"),
        "pluralize": ("value|pluralize", 3),
        "pprint": ("value|pprint", {"name": "Ada", "count": 3}),
        "random": ("value|random", ["only choice"]),
        "rjust": ("value|rjust:30", "hello"),
        "safe": ("value|safe", "<b>hello</b>"),
        "safeseq": ('value|safeseq|join:", "', ["<b>one</b>", "<i>two</i>"]),
        "slice": ('value|slice:"1:4"', ["zero", "one", "two", "three", "four"]),
        "slugify": ("value|slugify", "Hello, World! Café"),
        "stringformat": ('value|stringformat:"04d"', 42),
        "striptags": ("value|striptags", "<p>Hello <b>world</b></p>"),
        "time": ('value|time:"H:i"', stamp),
        "timesince": ("value|timesince:other", stamp),
        "timeuntil": ("value|timeuntil:other", stamp + timedelta(days=2)),
        "title": ("value|title", "hello world"),
        "truncatechars": ("value|truncatechars:12", "A longer sentence to shorten"),
        "truncatechars_html": (
            "value|truncatechars_html:12",
            "<p>A longer <b>sentence</b> to shorten</p>",
        ),
        "truncatewords": ("value|truncatewords:3", "One two three four five"),
        "truncatewords_html": (
            "value|truncatewords_html:3",
            "<p>One <b>two three four</b> five</p>",
        ),
        "unordered_list": ("value|unordered_list", ["One", ["Nested"], "Two"]),
        "upper": ("value|upper", "hello world"),
        "urlencode": ("value|urlencode", "hello world / café"),
        "urlize": ("value|urlize", "Visit https://example.com or hello@example.com"),
        "urlizetrunc": (
            "value|urlizetrunc:20",
            "Visit https://example.com/a/long/path",
        ),
        "wordcount": ("value|wordcount", "one two three four"),
        "wordwrap": ("value|wordwrap:12", "A sentence with several words to wrap"),
        "yesno": ('value|yesno:"yes,no,maybe"', True),
    }
    from django.template.defaultfilters import register as filter_register

    missing = set(filter_register.filters) - set(filters)
    if missing:
        raise ValueError(f"Add benchmark inputs for filters: {sorted(missing)}")
    result = []
    for name in sorted(filter_register.filters):
        expression, value = filters[name]
        source = "{{ " + expression + " }}"
        data = {"value": value, "other": stamp + timedelta(days=1)}
        if name == "timeuntil":
            data["other"] = stamp
        result.append(
            Workload(
                f"filter/{name}",
                "filters",
                source,
                {"page.html": source},
                "page.html",
                data,
                engines=DJANGO_ONLY,
                deterministic=name == "random",
                note="Singleton input makes output deterministic."
                if name == "random"
                else "",
            )
        )

    tags = {
        "autoescape": "{% autoescape off %}{{ html }}{% endautoescape %}",
        "block": "{% block content %}{{ title }}{% endblock %}",
        "comment": "{% comment %}Ignored content{% endcomment %}",
        "csrf_token": "{% csrf_token %}",
        "cycle": '{% for item in rows %}{% cycle "odd" "even" %}{% endfor %}',
        "debug": "{% debug %}",
        "extends": '{% extends "base.html" %}{% block content %}{{ title }}{% endblock %}',
        "filter": "{% filter upper %}hello world{% endfilter %}",
        "firstof": '{% firstof missing title "fallback" %}',
        "for": "{% for item in rows %}{{ item.name }}{% endfor %}",
        "if": "{% if flag %}yes{% else %}no{% endif %}",
        "ifchanged": "{% for item in rows %}{% ifchanged item.name %}{{ item.name }}{% endifchanged %}{% endfor %}",
        "include": '{% include "included.html" %}',
        "load": "{% load i18n %}",
        "lorem": "{% lorem 1 b %}",
        "now": '{% now "" %}',
        "partial": "{% partialdef card %}{{ title }}{% endpartialdef %}{% partial card %}",
        "partialdef": "{% partialdef card inline %}{{ title }}{% endpartialdef %}",
        "querystring": "{% querystring query page=2 %}",
        "regroup": "{% regroup rows by group as groups %}{% for group in groups %}{{ group.grouper }}{% endfor %}",
        "resetcycle": '{% cycle "odd" "even" as stripe %}{% resetcycle stripe %}{% cycle stripe %}',
        "spaceless": "{% spaceless %}<p> hello </p> <p> world </p>{% endspaceless %}",
        "templatetag": "{% templatetag openvariable %}",
        "url": '{% url "benchmark-example" %}',
        "verbatim": "{% verbatim %}{{ literal }}{% endverbatim %}",
        "widthratio": "{% widthratio 42 100 200 %}",
        "with": "{% with heading=title %}{{ heading }}{% endwith %}",
    }
    from django.template.defaulttags import register as tag_register
    from django.template.loader_tags import register as loader_register

    names = set(tag_register.tags) | set(loader_register.tags)
    if names != set(tags):
        raise ValueError(f"Builtin tag inventory changed: {names ^ set(tags)}")
    data = {
        "title": "Monthly report",
        "html": "<b>hello</b>",
        "flag": True,
        "csrf_token": "fixed-benchmark-token",
        "query": QueryDict("search=bread"),
        "rows": [{"name": f"Item {i}", "group": i // 5} for i in range(20)],
    }
    for name in sorted(tags):
        source = tags[name]
        # debug dumps context, including object reprs: use only stable scalars.
        context = {"title": "Monthly report"} if name == "debug" else data
        note = {
            "random": "Singleton input makes output deterministic.",
            "now": "Empty format keeps output fixed while exercising the tag.",
            "lorem": "Uses the fixed common paragraph.",
            "debug": "Debug output is disabled in these production settings.",
            "csrf_token": "Fixed context token keeps the output deterministic.",
        }.get(name, "")
        result.append(
            Workload(
                f"tag/{name}",
                "tags",
                source,
                {
                    "page.html": source,
                    "base.html": "{% block content %}{% endblock %}",
                    "included.html": "<p>{{ title }}</p>",
                },
                "page.html",
                context,
                engines=DJANGO_ONLY,
                needs_i18n=name == "load",
                note=note,
                deterministic=name in ("csrf_token", "lorem", "now"),
            )
        )
    return result + library_workloads(stamp)


def library_workloads(stamp):
    """The standard loadable libraries, including their registered aliases."""
    import importlib
    from bench_corpus import DJANGO_ONLY, Workload

    filters = {
        "i18n": {
            "language_name": ("value|language_name", "de"),
            "language_name_local": ("value|language_name_local", "de"),
            "language_bidi": ("value|language_bidi", "ar"),
            "language_name_translated": ("value|language_name_translated", "de"),
        },
        "l10n": {
            "localize": ("value|localize", 1234.5),
            "unlocalize": ("value|unlocalize", 1234.5),
        },
        "tz": {
            "localtime": ("value|localtime", stamp),
            "utc": ("value|utc", stamp),
            "timezone": ('value|timezone:"Europe/Paris"', stamp),
        },
    }
    tags = {
        "i18n": {
            "translate": '{% translate "Hello" %}',
            "trans": '{% trans "Hello" %}',
            "blocktranslate": "{% blocktranslate %}Hello {{ title }}{% endblocktranslate %}",
            "blocktrans": "{% blocktrans %}Hello {{ title }}{% endblocktrans %}",
            "language": '{% language "de" %}{% translate "Hello" %}{% endlanguage %}',
            "get_available_languages": "{% get_available_languages as langs %}{{ langs|length }}",
            "get_language_info": '{% get_language_info for "de" as lang %}{{ lang.name }}',
            "get_language_info_list": "{% get_language_info_list for codes as langs %}{{ langs|length }}",
            "get_current_language": "{% get_current_language as lang %}{{ lang }}",
            "get_current_language_bidi": "{% get_current_language_bidi as bidi %}{{ bidi }}",
        },
        "l10n": {"localize": "{% localize on %}{{ number }}{% endlocalize %}"},
        "tz": {
            "localtime": "{% localtime on %}{{ stamp }}{% endlocaltime %}",
            "timezone": '{% timezone "Europe/Paris" %}{{ stamp }}{% endtimezone %}',
            "get_current_timezone": "{% get_current_timezone as zone %}{{ zone }}",
        },
        "static": {
            "static": '{% static "css/site.css" %}',
            "get_static_prefix": "{% get_static_prefix %}",
            "get_media_prefix": "{% get_media_prefix %}",
        },
        "cache": {
            "cache": '{% cache 60 "benchmark-fragment" %}{{ title }}{% endcache %}'
        },
    }
    result = []
    for library in tags:
        registered = importlib.import_module(f"django.templatetags.{library}").register
        if set(registered.tags) != set(tags[library]) or set(registered.filters) != set(
            filters.get(library, {})
        ):
            raise ValueError(f"Add benchmark fixtures for changed {library} inventory")
        for name, (expression, value) in sorted(filters.get(library, {}).items()):
            source = "{% load " + library + " %}{{ " + expression + " }}"
            result.append(
                Workload(
                    f"filter/{name}",
                    "filters",
                    source,
                    {"page.html": source},
                    "page.html",
                    {"value": value},
                    engines=DJANGO_ONLY,
                    library_names=(library,),
                    note=f"Django’s {library} library.",
                )
            )
        for name, body in sorted(tags[library].items()):
            source = "{% load " + library + " %}" + body
            result.append(
                Workload(
                    f"tag/{name}",
                    "tags",
                    source,
                    {"page.html": source},
                    "page.html",
                    {
                        "title": "world",
                        "number": 1234.5,
                        "stamp": stamp,
                        "codes": ["en", "de"],
                    },
                    engines=DJANGO_ONLY,
                    library_names=(library,),
                    note=f"Django’s {library} library."
                    + (" Warm cached fragment." if name == "cache" else ""),
                )
            )
    return result
