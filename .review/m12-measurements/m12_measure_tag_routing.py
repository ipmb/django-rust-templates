"""Which code compiles and renders the `django/templatetags/*` tags today?

M12's row owns **no meter entry** — `i18n`, `l10n`, `static`, `tz` and `cache`
are `django/templatetags/*`, outside the `django.template` namespace
`unshimmed()` counts — so the milestone's worth cannot be read off the meter at
all. The question that decides what it is worth is the one M11's filter probe
asked one namespace in: with the shim installed *today*, when
`{% load i18n %}{% trans "x" %}` is compiled, whose compile function runs and
whose node class is built?

Three answers are possible per tag, and telling them apart is the whole
measurement:

* **theirs/theirs** — Django's compile builds Django's node; the tag is not on
  our path at all.
* **ours/theirs** — the body is Rust's but the node class is still the pinned
  file's, which is the shape M11 found one namespace in (a handle whose
  identity decides routing).
* **ours/ours** — the tag is on our path end to end.

Run, with the shim on `PYTHONPATH` (it is installed by
`scripts/shimpath/sitecustomize.py`, so a bare interpreter measures stock
Django):

    . scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django:$PWD" \\
        .venv/bin/python .review/m12-measurements/m12_measure_tag_routing.py

**Not `-I`.** `-I` implies `-s` and ignores `PYTHONPATH`, so under `-I` this
script measures stock Django and would "prove" the tags are Django's by
measuring a run in which our engine was never installed.
"""

import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / ".django"))

from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(USE_I18N=True, USE_TZ=True, INSTALLED_APPS=[])

import django  # noqa: E402

django.setup()

from django.template.backends.django import DjangoTemplates  # noqa: E402

from django_rust_templates import host  # noqa: E402


def suite_engine():
    """The engine the suite's ``{% load %}`` actually resolves against.

    **Not a bare ``Engine()``, and getting this wrong is the whole trap.** The
    built-in libraries are merged by the *backend*, not the engine:
    ``Engine.get_template_libraries`` loads only what ``settings.LIBRARIES``
    names, and it is ``DjangoTemplates.get_templatetag_libraries`` that calls
    ``get_installed_libraries()`` and folds ``django.templatetags.*`` in. So a
    bare ``Engine()`` compiles ``{% load i18n %}`` to
    ``TemplateSyntaxError: 'i18n' is not a registered tag library. Must be one
    of:`` with an empty list — measured, not reasoned — and the first version of
    this script reported all 18 tags as parse errors for that reason and no
    other. A probe that fails to load the library measures the probe.

    ``get_installed_libraries`` walks ``apps.get_app_configs()``, so
    ``django.setup()`` above is required even with ``INSTALLED_APPS`` empty.
    """
    backend = DjangoTemplates(
        {"NAME": "django", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return backend.engine

LIBS = ["i18n", "l10n", "static", "tz", "cache"]

# One source per registered name: the least it can be while still compiling.
# A name with no entry here is reported as unmeasured rather than skipped
# quietly, so the tally always adds up to the registry.
SOURCES = {
    ("i18n", "get_available_languages"): "{% get_available_languages as langs %}",
    ("i18n", "get_language_info"): '{% get_language_info for "en" as info %}',
    ("i18n", "get_language_info_list"): "{% get_language_info_list for langs as infos %}",
    ("i18n", "get_current_language"): "{% get_current_language as lang %}",
    ("i18n", "get_current_language_bidi"): "{% get_current_language_bidi as bidi %}",
    ("i18n", "translate"): '{% translate "x" %}',
    ("i18n", "trans"): '{% trans "x" %}',
    ("i18n", "blocktranslate"): "{% blocktranslate %}x{% endblocktranslate %}",
    ("i18n", "blocktrans"): "{% blocktrans %}x{% endblocktrans %}",
    ("i18n", "language"): '{% language "en" %}x{% endlanguage %}',
    ("l10n", "localize"): "{% localize on %}x{% endlocalize %}",
    ("static", "get_static_prefix"): "{% get_static_prefix %}",
    ("static", "get_media_prefix"): "{% get_media_prefix %}",
    ("static", "static"): '{% static "x" %}',
    ("tz", "localtime"): "{% localtime on %}x{% endlocaltime %}",
    ("tz", "timezone"): '{% timezone "UTC" %}x{% endtimezone %}',
    ("tz", "get_current_timezone"): "{% get_current_timezone as tz %}",
    ("cache", "cache"): "{% cache 500 x %}y{% endcache %}",
}


def owner(obj):
    """``(module name, is_ours)`` for a callable or a class."""
    module = getattr(obj, "__module__", "?")
    file = getattr(sys.modules.get(module), "__file__", "") or ""
    return module, "_impl" in file


def compile_one(lib, name, source, engine):
    """Compile ``{% load <lib> %}`` + ``source`` and report whose node came out.

    Compiled through ``Engine.from_string`` rather than a hand-built ``Parser``
    so that the routing measured is the routing the suite gets: the load goes
    through whatever ``{% load %}`` is on this tree, and the tag through whatever
    the parse path does with a library tag. A hand-built parser would answer the
    question "what would Django's parser do", which is not the one being asked.

    Two node types are skipped, and the second one is a trap this script fell
    into: ``LoadNode``. ``{% load %}`` is not a no-op in the nodelist — Django's
    ``do_load`` **returns a node** (``defaulttags.py:1131``), so "the first node
    that is not ``TextNode``" is the load node for *every* tag and the table
    comes back uniform. It did: the first working run of this script reported
    ``theirs/ours`` for all 18 names, with all 18 node classes attributed to
    ``django.template.defaulttags``. A verdict that does not vary with its input
    is not a measurement of the input.
    """
    try:
        template = engine.from_string("{%% load %s %%}%s" % (lib, source))
    except Exception as exc:  # noqa: BLE001 - the verdict *is* the message
        return f"parse-error: {type(exc).__name__}: {exc}", "-", "-"
    nodelist = template.nodelist
    node = None
    for candidate in nodelist:
        if type(candidate).__name__ in ("TextNode", "LoadNode"):
            continue
        node = candidate
        break
    if node is None:  # pragma: no cover - defensive
        return "no node", "-", "-"
    node_module, _ = owner(type(node))
    return None, node_module, type(node).__name__


def main():
    import django.template.defaulttags as probe

    print("shim installed :", "_impl" in (probe.__file__ or ""))
    print("defaulttags    :", probe.__file__)
    print()

    engine = suite_engine()
    print("engine         :", type(engine).__name__)
    print()
    rows = []
    filter_rows = []
    for lib in LIBS:
        module = __import__(f"django.templatetags.{lib}", fromlist=["x"])
        ours = "_impl" in (module.__file__ or "")
        print(f"== django.templatetags.{lib}  ours={ours}")
        print(f"   {module.__file__}")
        for name, func in sorted(module.register.tags.items()):
            func_module, func_ours = owner(func)
            source = SOURCES.get((lib, name))
            if source is None:
                verdict, node_label = "unmeasured", "-"
            else:
                error, node_module, node_class = compile_one(lib, name, source, engine)
                if error:
                    verdict, node_label = error, "-"
                else:
                    defining = sys.modules.get(node_module)
                    node_ours = "_impl" in (getattr(defining, "__file__", "") or "")
                    node_label = f"{node_class}"
                    verdict = (
                        "ours/ours"
                        if func_ours and node_ours
                        else "theirs/ours"
                        if not func_ours and node_ours
                        else "ours/theirs"
                        if func_ours
                        else "theirs/theirs"
                    )
            rows.append((lib, name, func_module, func_ours, node_label, verdict))
            print(
                f"   tag {name:<26} compile={func_module:<34} "
                f"node={node_label:<34} {verdict}"
            )
        for name, func in sorted(module.register.filters.items()):
            func_module, func_ours = owner(func)
            # The *routing* verdict, which the file check above cannot give: the
            # callable's `__file__` says whose text the body is (ours since M12
            # Task 3 interposed the library modules), but whether the seam sends
            # it to Rust is `django_own_filters()`, which is keyed by **handle**.
            # Before M12 Task 7 the map held `defaultfilters` only, so these nine
            # were `ours=False`-by-file yet unrouted and the Python body ran.
            routed = func in host.django_own_filters()
            filter_rows.append((lib, name, func_module, func_ours, routed))
            print(
                f"   filter {name:<23} {func_module:<42} ours={func_ours} "
                f"routed={routed}"
            )
        print()

    print("=== verdict tally")
    for verdict, count in Counter(r[5] for r in rows).most_common():
        print(f"  {count:>3}  {verdict}")
    print(f"  --- {len(rows)} registered tag name(s) over {len(LIBS)} librar(ies)")
    print()
    print("=== filter routing tally (handle in `django_own_filters()`)")
    for routed, count in Counter(r[4] for r in filter_rows).most_common():
        print(f"  {count:>3}  routed={routed}")
    print(
        f"  --- {len(filter_rows)} registered filter name(s); "
        f"{sum(1 for r in filter_rows if r[4])} route to Rust"
    )


if __name__ == "__main__":
    main()
