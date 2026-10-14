#!/usr/bin/env python
"""The templates the benchmark renders, and why each row is in the corpus it is in.

Two syntax families, and **the split is a measurement**. Before any of this was
written, one construct at a time was rendered through every engine in the
benchmark to ask which of them agree byte for byte
(`.review/m19-measurements/subset_probe.py`, whose transcript is beside it). Five
things came back, and every one of them prices a row out of the shared corpus or
reshapes it:

1. **Escaping differs two ways.** Django writes `&#x27;` for an apostrophe and
   `&quot;` for a quote; Jinja2 (through markupsafe) writes `&#39;` and `&#34;`.
   So a shared row may not put `'` or `"` in an *escaped* value. `escape` is still
   exercised — `&`, `<`, `>` and `/` agree — but a row built to show the *table* is
   a Django-only row. This was three ways while minijinja was in the benchmark: it
   was the only one of the four that escaped the solidus, so `a</b>` was a
   divergence too. It is now an agreement, and the shared corpus takes advantage of
   that — `micro/escape` renders a closing tag, which asserts on every run that
   Django and Jinja2 both leave the solidus alone.
2. **A dict key named `items` resolves to the method in Jinja2.** Jinja2 tries
   attribute before item, so `{{ row.items }}` calls `dict.items` and raises.
   Django tries item before attribute and returns the value. `keys`, `values`,
   `get`, `copy`, `count` and `update` are the same trap, so the corpus data
   avoids all of them — the nested-loop key is `parts`, not `items`.
3. **`{% empty %}` and `{% else %}` are two spellings of one idea.** Django
   refuses `{% for … %}{% else %}` and accepts `{% empty %}`; Jinja2 does the
   opposite. The first version of this list said "each one engine's spelling …
   neither engine knows the other's", which read as a draw and was wrong by a
   count; it was then corrected to "one against two", which was right while
   minijinja — the second `{% else %}` engine — was in the benchmark. With
   minijinja gone it is one against one, and the exclusion is Jinja2's alone.
4. **Filter arguments have different syntax** — `floatformat:2` against
   `floatformat(2)` — so *any* filter with an argument is a Jinja2-excluded row.
   This is why `django-only` is a family and not an afterthought. It is weaker
   than it looks in one direction: `floatformat(2)` is not merely unsupported in
   Jinja2, it is a *parse* error (`expected token 'end of print statement'`), so
   the row is not "the same construct spelled differently" but "a construct one
   engine has no grammar for".
5. **A chained lookup through an undefined name raises in Jinja2** —
   `{{ nope.deeper }}` → `UndefinedError` — where Django returns `''`. This list
   has been wrong about the width of this exclusion twice: it first said "fine in
   Django and Jinja2 and an error in minijinja", which was wrong about Jinja2
   (its default `Undefined` raises on an attribute of an undefined), and was then
   corrected to "two engines wide", which was right about the four-engine set and
   is now one engine wide. Every shared row therefore supplies its data; the
   parse-bound row that renders `{{ section0.field0 }}` against an empty context
   is a *Django-only* row, which is where it belongs anyway.

The probe's own output is `.review/m19-measurements/subset_probe.txt`, and it is
re-run whenever the engine set changes — the findings above are a measurement of
the engine set in the benchmark now, not a memory of the first one. It prints each
case's bytes on every engine, and it is where the two escape spellings are readable
side by side: the corpus's escape-table row is Django-only and never had a Jinja2
column, so the Jinja2 spelling of `'` and `"` lives in the transcript and in that
row's note rather than in a column of it.

Its first run was wrong in a way worth recording, because the failure mode is
this file's whole subject: it called `handle.render(context)` directly instead of
`engine.render(handle, context)`, and every minijinja row came back
`AttributeError: '_Inline' object has no attribute 'render'` — which read as
"minijinja differs on everything" rather than as a probe that could not drive one
of its engines. A probe whose arms are not all known to work is a probe that
will report an exclusion for the wrong reason.

The shared family is therefore the intersection, and every one of its rows is
expected to hash identically on all three engines — which `bench.py` asserts
rather than assumes. The `django-only` family is where the constructs that make
Django templates Django templates live, and it runs two engines because two is
the number that can render it.

**The data is part of the claim.** Every escaped value in the shared corpus was
chosen to avoid the characters Django and Jinja2 spell differently, so "the three
agree" is a fact about the corpus and not a fact about the engines.
`django-only/escape-table` exists to show the difference rather than to hide it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "bench_templates"

def _template(name):
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")

SHARED = ("django", "rust", "jinja2")
DJANGO_ONLY = ("django", "rust")

# --- data --------------------------------------------------------------------------
# Ints and strings only. No floats: ``1.5`` renders as ``1.5`` on the Python side
# and minijinja's ``f64`` Display is not the same algorithm, so a float is a
# formatting difference dressed as a timing difference. Money arrives as a string
# for the same reason, which is also how a real template gets it. (The float ban
# outlived its reason: minijinja is gone, and the corpus keeps the rule rather than
# churn every row's bytes for a float nobody asked for.)

_TITLES = ("Monthly report", "Quarterly review", "Annual summary", "Incident log")
_OWNERS = ("Ada", "Grace", "Linus", "Barbara", "Ken", "Margaret")
_STATUS = ("in progress", "done", "blocked", "queued")
# Escaped on the way out, and inside the intersection: `&`, `<`, `>` and nothing
# else. No `/`, no apostrophe, no quote — see the module docstring.
_NOTES = (
    "Rush & priority",
    "Fragile <handle with care>",
    "Recheck > thresholds",
    "Standard & slow",
)


def _rows(count: int) -> list[dict]:
    return [
        {
            "name": f"{_TITLES[i % len(_TITLES)]} {i}",
            "owner": _OWNERS[i % len(_OWNERS)],
            "status": _STATUS[i % len(_STATUS)],
            "note": _NOTES[i % len(_NOTES)],
            "hot": i % 3 == 0,
            "value": i * 3,
        }
        for i in range(count)
    ]


ROWS = _rows(50)

_TITLE = "Monthly report"
# The closing tag is deliberate and it is new. While minijinja was in the
# benchmark the solidus was the one character *it* escaped and the others did not,
# so a `</b>` here would have put the row outside the intersection it belongs to.
# With minijinja gone the constraint inverted: Django and Jinja2 both leave the
# solidus alone, and rendering one asserts that agreement on every run instead of
# leaving it to a note. `django-only/escape-table` still shows the two spellings
# for `'` and `"`, which is the divergence that survives.
_HTML = "Acme & Sons <b>bold</b>"

ROW = {"name": "Widget press", "value": 12}
NESTED = {"meta": {"label": "deep"}}

# The complex page: 12 sections of 6 line items. ``total`` is empty on every
# third section so the `{% if %}` inside the loop takes both branches.
SECTIONS = [
    {
        "title": f"Section {s}",
        "total": "" if s % 3 == 2 else f"{s * 120}.00",
        "lines": [
            {
                "sku": f"SKU-{s:02d}-{i}",
                "note": _NOTES[(s + i) % len(_NOTES)],
                "qty": s * 6 + i,
            }
            for i in range(6)
        ],
    }
    for s in range(12)
]


# --- templates ---------------------------------------------------------------------

BASE = _template("base.html")

FOOTER = _template("footer.html")

# real-world / normal: a page a project would actually have. Two levels of
# inheritance, one loop over 50 dicts, a filter, an `{% if %}` with both branches
# live, one include, and four escaped columns.
NORMAL = _template("report.html")

# real-world / complex: three levels of inheritance, a nested loop (12 outer x 6
# inner), an `{% include %}` per *inner* iteration, escaping on every row, and a
# conditional whose branches are both taken.
LAYOUT = _template("layout.html")

COMPLEX = _template("invoice.html")

LINE = _template("line.html")


def large_template() -> str:
    """The large-page benchmark source, also used by the parity corpus."""
    return _template("large.html")


LARGE_DATA = {
    f"section{i}": {
        "enabled": i % 2 == 0,
        "label": f"Label {i}",
        **{f"field{j}": f"v{i}-{j}" for j in range(10)},
        "parts": [{"name": f"part{k}", "value": i * 10 + k} for k in range(3)],
    }
    for i in range(20)
}

# --- django-only templates ----------------------------------------------------------

D_FILTER_ARG = (
    "{% load i18n %}<table>"
    "{% for r in rows %}<tr><td>{{ r.name }}</td><td>{{ r.value|floatformat:2 }}</td></tr>{% endfor %}"
    "</table>"
)
D_I18N = (
    "{% load i18n %}<html><head><title>{% translate \"Report\" %}</title></head><body>"
    "{% for r in rows %}"
    "<li>{% blocktranslate count count=r.value %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</li>"
    "{% endfor %}"
    "</body></html>"
)
D_FOR_TAGS = (
    "<table>"
    "{% for r in rows %}"
    '<tr class="{% cycle \'odd\' \'even\' %}">'
    "<td>{{ forloop.counter }}</td><td>{{ r.name }}</td>"
    "<td>{% widthratio r.value 100 4 %}</td></tr>"
    "{% empty %}<tr><td>none</td></tr>{% endfor %}"
    "</table>"
)
# The escape table, shown rather than averaged away: one row per divergent
# character, so the report can print which character each engine spells differently.
D_ESCAPE_TABLE = "{{ apos }}|{{ quot }}|{{ solidus }}"

# real-world / translation: the same page in each engine's own spelling of
# gettext. Django writes `{% translate %}` and `{% blocktranslate %}…{% plural %}`;
# Jinja2 writes `{% trans %}…{% pluralize %}…{% endtrans %}` and needs
# `jinja2.ext.i18n` loaded. There is no single source that both parse, which is
# why the existing `django-only/realistic` row leaves the Jinja2 column blank --
# and why a translation comparison needed equivalent templates instead.
#
# Null translations on both sides, so every msgid renders as itself and the
# plural is still chosen by count. The row measures the translation machinery,
# not a catalogue lookup. Its data key is `qty` rather than `count`, because
# `count` is one of the dict-method names this module's docstring warns about.
TRANSLATED_DJANGO = _template("translated_page_django.html")
TRANSLATED_JINJA2 = _template("translated_page_jinja2.html")

# The M16 page, kept verbatim so the historical row still has a home and the
# milestone-to-milestone comparison does not silently change subject.
M16_BASE = _template("translated_base.html")
M16_PAGE = _template("translated_report.html")
M16_ROWS = [{"name": f"Row {i}", "value": i * 1.5, "count": i} for i in range(50)]

I18N_LIBRARY = {
    "django": "django.templatetags.i18n",
    "rust": "django_rust_templates.engine.templatetags.i18n",
}


@dataclass(frozen=True)
class Workload:
    """One row of the report: one template set, one entry point, one dataset."""

    key: str
    family: str
    summary: str
    templates: dict
    entry: str
    data: dict
    engines: tuple = SHARED
    #: Per-engine template sources, for a row whose construct has no single
    #: spelling. The engines render *equivalent* templates rather than identical
    #: ones, and the row is only legitimate because its output is asserted equal
    #: across them -- `tests/test_bench_report.py` is where that is enforced.
    #: Every other row shares one source, which is the stronger guarantee and
    #: stays the default.
    engine_templates: dict = None
    needs_i18n: bool = False
    note: str = ""
    library_names: tuple = ()
    deterministic: bool = False

    def templates_for(self, engine: str) -> dict:
        """The source this engine renders: its own variant, or the shared one."""
        if self.engine_templates and engine in self.engine_templates:
            return self.engine_templates[engine]
        return self.templates

    def libraries(self, engine: str) -> dict:
        names = set(self.library_names)
        if self.needs_i18n:
            names.add("i18n")
        package = "django.templatetags" if engine == "django" else "django_rust_templates.engine.templatetags"
        return {name: f"{package}.{name}" for name in names}


def _one(template: str, **kw) -> dict:
    return {"page.html": template}


def workloads() -> list[Workload]:
    from bench_features import workloads as feature_workloads

    W = Workload
    return [
        # --- micro: one construct per row, so a regression names itself --------
        W(
            "micro/text",
            "micro",
            "one text node, no variables",
            _one("<p>plain text, no variables at all</p>"),
            "page.html",
            {},
        ),
        W(
            "micro/variable",
            "micro",
            "one variable",
            _one("<p>{{ title }}</p>"),
            "page.html",
            {"title": _TITLE},
        ),
        W(
            "micro/lookup",
            "micro",
            "one depth-3 lookup",
            _one("<p>{{ row.meta.label }}</p>"),
            "page.html",
            {"row": NESTED},
        ),
        W(
            "micro/loop-text",
            "micro",
            "50-row loop, text body",
            _one("{% for r in rows %}<tr><td>x</td></tr>{% endfor %}"),
            "page.html",
            {"rows": ROWS},
        ),
        W(
            "micro/loop-var",
            "micro",
            "50-row loop, one variable per row",
            _one("{% for r in rows %}<tr><td>{{ r.name }}</td></tr>{% endfor %}"),
            "page.html",
            {"rows": ROWS},
        ),
        W(
            "micro/if",
            "micro",
            "one if/else, taken branch",
            _one("{% if flag %}<b>on</b>{% else %}<i>off</i>{% endif %}"),
            "page.html",
            {"flag": True},
        ),
        W(
            "micro/filter",
            "micro",
            "one no-arg filter",
            _one("{{ title|upper }}"),
            "page.html",
            {"title": _TITLE},
        ),
        W(
            "micro/escape",
            "micro",
            "one variable needing escaping",
            _one("{{ html }}"),
            "page.html",
            {"html": _HTML},
        ),
        W(
            "micro/include",
            "micro",
            "one include",
            {"page.html": '{% include "partials/footer.html" %}', "partials/footer.html": FOOTER},
            "page.html",
            {"title": _TITLE, "rows": ROWS},
        ),
        W(
            "micro/extends",
            "micro",
            "one level of extends, one block",
            {
                "base.html": BASE,
                "page.html": '{% extends "base.html" %}{% block content %}<p>{{ title }}</p>{% endblock %}',
            },
            "page.html",
            {"title": _TITLE},
        ),
        # --- real-world: whole pages -------------------------------------------
        W(
            "real-world/normal",
            "real-world",
            "a report page: 2-level extends, 50-row loop, filters, if/else, one include",
            {
                "base.html": BASE,
                "page.html": NORMAL,
                "partials/footer.html": FOOTER,
            },
            "page.html",
            {"title": _TITLE, "rows": ROWS},
        ),
        W(
            "real-world/complex",
            "real-world",
            "an invoice: 3-level extends, 12x6 nested loop, include per line, escaping",
            {
                "base.html": BASE,
                "layout.html": LAYOUT,
                "page.html": COMPLEX,
                "partials/footer.html": FOOTER,
                "partials/line.html": LINE,
            },
            "page.html",
            {"title": _TITLE, "rows": ROWS, "sections": SECTIONS},
        ),
        W(
            "real-world/translation",
            "real-world",
            "50 translated strings with plurals, each engine's own gettext syntax",
            {"page.html": TRANSLATED_DJANGO},
            "page.html",
            {"rows": [{"name": f"Row {i}", "qty": i % 4} for i in range(50)]},
            engine_templates={"jinja2": {"page.html": TRANSLATED_JINJA2}},
            needs_i18n=True,
        ),
        W(
            "real-world/large",
            "real-world",
            "a 9 KB page of 20 sections, 20 loop statements: the parse-bound row",
            _one(_template("large.html")),
            "page.html",
            LARGE_DATA,
        ),
        # --- django-only: the constructs the intersection excludes -------------
        W(
            "django-only/filter-arg",
            "django-only",
            "a filter with an argument (floatformat:2)",
            _one(D_FILTER_ARG),
            "page.html",
            {"rows": ROWS},
            engines=DJANGO_ONLY,
            needs_i18n=True,
            note="Jinja2 spells a filter argument floatformat(2), not floatformat:2",
        ),
        W(
            "django-only/i18n",
            "django-only",
            "{% translate %} and {% blocktranslate %} in a loop",
            _one(D_I18N),
            "page.html",
            {"rows": ROWS},
            engines=DJANGO_ONLY,
            needs_i18n=True,
            note="Jinja2 has no i18n tag library at all",
        ),
        W(
            "django-only/for-tags",
            "django-only",
            "forloop.counter, {% cycle %}, {% widthratio %}, {% empty %}",
            _one(D_FOR_TAGS),
            "page.html",
            {"rows": ROWS},
            engines=DJANGO_ONLY,
            note="loop metadata is forloop.* in DTL and loop.* in Jinja2; {% empty %} has no counterpart there",
        ),
        W(
            "django-only/escape-table",
            "django-only",
            "the escape tables, side by side",
            _one(D_ESCAPE_TABLE),
            "page.html",
            {"apos": "it's", "quot": 'say "hi"', "solidus": "a</b>"},
            engines=DJANGO_ONLY,
            note="Django writes &#x27; and &quot;; Jinja2 writes &#39; and &#34;; both leave the solidus alone",
        ),
        W(
            "django-only/realistic",
            "django-only",
            "the M16 page, verbatim: extends + i18n + floatformat over 50 rows",
            {"base.html": M16_BASE, "page.html": M16_PAGE},
            "page.html",
            {"rows": M16_ROWS},
            engines=DJANGO_ONLY,
            needs_i18n=True,
            note="kept verbatim so the milestone-to-milestone row does not change subject",
        ),
    ] + feature_workloads()


FAMILIES = ("micro", "real-world", "django-only", "filters", "tags")

FAMILY_SUMMARY = {
    "filters": "one illustrative template per default Django filter",
    "tags": "one illustrative template per builtin Django tag",
    "micro": (
        "one construct per row, so a regression names itself. It is two families and "
        "the earlier note here claimed one: the single-node rows (text, variable, "
        "lookup, if, filter, escape) are mostly a measurement of the render call, and "
        "the loop, include and extends rows are a measurement of the construct. The "
        "call share in the caption says where the split falls on this run"
    ),
    "real-world": "whole pages, rendered by all three engines",
    "django-only": "the constructs outside the three-engine intersection, rendered by the two engines that have them",
}


if __name__ == "__main__":  # a corpus listing is the cheapest way to review the corpus
    for w in workloads():
        names = ", ".join(sorted(w.templates))
        print(f"{w.key:26s} {len(w.templates)} file(s) {names}")
        print(f"{'':26s} engines: {', '.join(w.engines)}")
        if w.note:
            print(f"{'':26s} note:    {w.note}")
