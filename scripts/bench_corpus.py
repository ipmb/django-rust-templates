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

BASE = (
    "<!doctype html><html><head><title>{% block title %}Report{% endblock %}</title>"
    "</head><body>{% block content %}{% endblock %}</body></html>"
)

FOOTER = "<footer>{{ title }} - {{ rows|length }} rows</footer>"

# real-world / normal: a page a project would actually have. Two levels of
# inheritance, one loop over 50 dicts, a filter, an `{% if %}` with both branches
# live, one include, and four escaped columns.
NORMAL = (
    '{% extends "base.html" %}'
    "{% block title %}{{ title }}{% endblock %}"
    "{% block content %}"
    "<h1>{{ title|upper }}</h1>"
    "{% if rows %}<p>{{ rows|length }} rows</p>{% else %}<p>no rows</p>{% endif %}"
    "<table>"
    "{% for r in rows %}"
    "<tr><td>{{ r.name }}</td><td>{{ r.owner }}</td>"
    "<td>{{ r.status|title }}</td><td>{{ r.note }}</td>"
    "<td>{% if r.hot %}<b>hot</b>{% else %}ok{% endif %}</td></tr>"
    "{% endfor %}"
    "</table>"
    '{% include "partials/footer.html" %}'
    "{% endblock %}"
)

# real-world / complex: three levels of inheritance, a nested loop (12 outer x 6
# inner), an `{% include %}` per *inner* iteration, escaping on every row, and a
# conditional whose branches are both taken.
LAYOUT = (
    '{% extends "base.html" %}'
    "{% block content %}"
    "<header><h1>{% block heading %}Invoice{% endblock %}</h1></header>"
    "{% block body %}{% endblock %}"
    '{% include "partials/footer.html" %}'
    "{% endblock %}"
)

COMPLEX = (
    '{% extends "layout.html" %}'
    "{% block heading %}{{ title|upper }}{% endblock %}"
    "{% block body %}"
    "{% for s in sections %}"
    "<section><h2>{{ s.title|title }}</h2><table>"
    "{% for line in s.lines %}"
    '{% include "partials/line.html" %}'
    "{% endfor %}"
    "</table>"
    "{% if s.total %}<p>total {{ s.total }}</p>{% endif %}"
    "</section>"
    "{% endfor %}"
    "{% endblock %}"
)

LINE = "<tr><td>{{ line.sku|upper }}</td><td>{{ line.note }}</td><td>{{ line.qty }}</td></tr>"


def large_template() -> str:
    """A ~9 KB page with no loop: the parse-bound row.

    Alternating `{{ section.N }}` and `{% if %}`/`{% for %}` so the node count is
    dominated by variables and control tags rather than text. There is no
    `{% load i18n %}`: this row is in the shared family and Jinja2 has no such
    library. The `load_i18n` parameter this used to take had one call site, and it
    passed `False` — a dead branch in a corpus whose whole argument is that a row
    is where it is for a reason, so it is gone.
    """
    parts = ["<article>"]
    for i in range(20):
        parts.append(f'<section id="s{i}">')
        for j in range(10):
            parts.append(f"<p>{{{{ section{i}.field{j} }}}}</p>")
        parts.append(f"{{% if section{i}.enabled %}}<b>{{{{ section{i}.label }}}}</b>{{% endif %}}")
        parts.append(
            f"{{% for item in section{i}.parts %}}<li>{{{{ item.name }}}}: "
            f"{{{{ item.value }}}}</li>{{% endfor %}}"
        )
        parts.append("</section>")
    parts.append("</article>")
    return "".join(parts)


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

# The M16 page, kept verbatim so the historical row still has a home and the
# milestone-to-milestone comparison does not silently change subject.
M16_BASE = (
    "{% load i18n %}<html><head><title>{% block title %}Default{% endblock %}</title></head>\n"
    '<body><h1>{% block heading %}{% translate "Report" %}{% endblock %}</h1>\n'
    "{% block content %}{% endblock %}</body></html>"
)
M16_PAGE = (
    '{% extends "base.html" %}{% load i18n %}\n'
    '{% block title %}{% translate "Monthly Report" %}{% endblock %}\n'
    "{% block content %}\n<table>\n"
    "{% for row in rows %}\n"
    "<tr><td>{{ row.name }}</td><td>{{ row.value|floatformat:2 }}</td>\n"
    "<td>{% blocktranslate count count=row.count %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</td></tr>\n"
    "{% endfor %}\n</table>\n"
    "{% endblock %}"
)
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
    needs_i18n: bool = False
    note: str = ""

    def libraries(self, engine: str) -> dict:
        if not self.needs_i18n:
            return {}
        return {"i18n": I18N_LIBRARY[engine]}


def _one(template: str, **kw) -> dict:
    return {"page.html": template}


def workloads() -> list[Workload]:
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
            "real-world/large",
            "real-world",
            "a 9 KB page with no loop: the parse-bound row",
            _one(large_template()),
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
    ]


FAMILIES = ("micro", "real-world", "django-only")

FAMILY_SUMMARY = {
    "micro": "one construct per row, so a regression names itself",
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
