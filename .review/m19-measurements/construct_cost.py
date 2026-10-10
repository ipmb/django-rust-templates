"""What each construct costs on the shipped path, by byte-identical A/B.

`node_census.py` says *which* nodes the walk hands back to Python on the
real-world rows; this says what handing them back **costs**, which is the number
that picks the next slice.  The instrument is a pair of templates that render to
the same bytes and differ in exactly one construct, so the difference is that
construct and not the output:

  * `extends` vs flat — the same page once through `{% extends %}` + `{% block %}`
    and once with the parent spliced in by hand.  This is lesson 4's quantity: on
    the real-world rows the walk delegates `ExtendsNode` and every `BlockNode`,
    and a block body is rendered by `NodeList.render`, so the whole page's content
    is Python's either way.
  * `{% if %}` vs nothing — `{% if true %}…{% endif %}` against the same text,
    which is a delegated `IfNode` and its body on the Python path.
  * `{{ x|upper }}` vs `{{ X }}` — a delegated filtered `VariableNode` against an
    unfiltered one that already resolves natively, the same characters out.

Every row is printed with a hash of its output, and the two sides of a pair must
hash equal or the pair is a lie; `same bytes` is therefore a printed assertion
and not an assumption.  Both engines are measured, because "what does a block
cost" is a question about ours *relative to* stock's — a construct stock also
pays for heavily is not where the gap is.

Run:  .venv/bin/python -I .review/m19-measurements/construct_cost.py
"""

import hashlib
import sys
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

# The page shape of `real-world/normal` with the corpus's own data, so the numbers
# are comparable to the bench table rather than to a page invented here.
BODY = (
    "<h1>{{ title|upper }}</h1>"
    "{% if rows %}<p>{{ rows|length }} rows</p>{% else %}<p>no rows</p>{% endif %}"
    "<table>"
    "{% for r in rows %}"
    "<tr><td>{{ r.name }}</td><td>{{ r.owner }}</td>"
    "<td>{{ r.status|title }}</td><td>{{ r.note }}</td>"
    "<td>{% if r.hot %}<b>hot</b>{% else %}ok{% endif %}</td></tr>"
    "{% endfor %}"
    "</table>"
)

BASE = (
    "<!doctype html><html><head><title>{% block title %}Report{% endblock %}</title>"
    "</head><body>{% block content %}{% endblock %}</body></html>"
)

EXTENDS = '{% extends "base.html" %}{% block title %}{{ title }}{% endblock %}{% block content %}' + BODY + "{% endblock %}"
FLAT = (
    "<!doctype html><html><head><title>{{ title }}</title></head><body>" + BODY + "</body></html>"
)

# `{% if %}`: a live delegated IfNode against the same text with the branch baked
# in.  `rows` is non-empty, so the true branch is what the pair must match.
IF_TMPL = "<p>{% if rows %}yes{% endif %}</p>"
IF_FLAT = "<p>yes</p>"

# A filtered variable against an unfiltered one holding the already-cased value.
FILTER_TMPL = "{{ word|upper }}"
FILTER_FLAT = "{{ upper_word }}"


def rows(n=50):
    return [
        {
            "name": f"Name {i}",
            "owner": "Acme",
            "status": "open",
            "note": "note",
            "hot": i % 3 == 0,
        }
        for i in range(n)
    ]


DATA = {"title": "Monthly report", "rows": rows(), "word": "abc", "upper_word": "ABC"}


PAIRS = [
    ("full page: extends+blocks vs flat", EXTENDS, FLAT, {"base.html": BASE, "page.html": EXTENDS}),
    ("{% if %} vs the baked branch", IF_TMPL, IF_FLAT, {"page.html": IF_TMPL}),
    ("{{ x|upper }} vs {{ X }}", FILTER_TMPL, FILTER_FLAT, {"page.html": FILTER_TMPL}),
]

ITERS = 300
REPEATS = 7


def one(engine_name, source, templates, data):
    templates = dict(templates)
    templates["page.html"] = source
    engine = bench_engines.build(engine_name, templates, {})
    try:
        handle = engine.get("page.html")
        context = engine.context(data)
        for _ in range(10):
            engine.render(handle, context)
        out = engine.render(handle, context)
        # Every repeat is kept, not just the best: the *difference* of two rows is
        # the quantity this file is for, and two rows can each be tight while their
        # difference is not (measured: the rust extends row's A-B ran 1.35 to 10.60
        # us over four runs while each side moved by ~3 us).  A best-of-7 point
        # estimate alone cannot say whether the effect it reports is above its own
        # noise, so the spread is printed beside it.
        times = [t / ITERS * 1e6 for t in timeit.repeat(lambda: engine.render(handle, context), number=ITERS, repeat=REPEATS)]
        return str(out), times
    finally:
        engine.close()


def main() -> None:
    print(f"iters={ITERS}, {REPEATS} repeats, render mode; A-B is the paired median over the repeats, [..] its range\n")
    for label, a, b, templates in PAIRS:
        print(label)
        for engine_name in ("rust", "django"):
            out_a, times_a = one(engine_name, a, templates, DATA)
            out_b, times_b = one(engine_name, b, templates, DATA)
            us_a, us_b = min(times_a), min(times_b)
            diffs = sorted(x - y for x, y in zip(times_a, times_b))
            median = diffs[len(diffs) // 2]
            same = "same bytes" if out_a == out_b else "*** DIFFERENT BYTES ***"
            ha, hb = hashlib.sha1(out_a.encode()).hexdigest()[:8], hashlib.sha1(out_b.encode()).hexdigest()[:8]
            print(
                f"  {engine_name:7} A {us_a:9.2f} us  B {us_b:9.2f} us  "
                f"A-B {median:8.2f} us [{diffs[0]:7.2f}..{diffs[-1]:6.2f}]  {same} ({ha}/{hb})"
            )
        print()


if __name__ == "__main__":
    main()
