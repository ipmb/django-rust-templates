#!/usr/bin/env python
"""Where the render gap is: one variable, one filter, one tag, one loop body.

`scripts/bench.py` answers "how much slower"; this answers "slower *where*", by
measuring the same tiny template under both engines and subtracting. Every
workload is a *per-unit* one — 50 of one thing and nothing else — so the two
sides' difference is (our per-unit cost - stock's), and the units are the four
the realistic page is built from.

Run it under both sides exactly as `scripts/bench.py` is run:

    source scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        .venv/bin/python .review/m16-measurements/m16_bench_decompose.py
    DJANGO_RUST_TEMPLATES=0 PYTHONPATH="$PWD/.django" \
        .venv/bin/python -I .review/m16-measurements/m16_bench_decompose.py

and diff the two transcripts by hand; the script deliberately does **not**
subtract across processes, because a subtraction the instrument does is a
number no reader can check. It prints `side:` and the tree it loaded for the
same reason `bench.py` does.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Same bootstrap as `scripts/bench.py`, for the same reason: one place in this
# tree configures settings (`tests/test_settings_configuration.py` refuses a
# second), and `filter_probe` also puts `.django` on `sys.path`, which the stock
# side needs because `-I` implies `-E`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.template import base as template_base  # noqa: E402

N = 50

# One workload per unit, each a loop of N identical steps so the per-unit cost
# is the whole call over N. Kept as a loop for every unit — including the
# ones a list comprehension could fuse — because the realistic page's steps are
# a loop's iterations, and a fused workload would measure something the page
# does not do.
WORKLOADS = {
    "var.name": "{% for r in rows %}{{ r.name }}{% endfor %}",
    "var.nested": "{% for r in rows %}{{ r.a.b }}{% endfor %}",
    "filter.upper": "{% for r in rows %}{{ r.name|upper }}{% endfor %}",
    "filter.floatformat": "{% for r in rows %}{{ r.value|floatformat:2 }}{% endfor %}",
    "tag.translate": '{% load i18n %}{% for r in rows %}{% translate "Report" %}{% endfor %}',
    "tag.blocktranslate": (
        '{% load i18n %}{% for r in rows %}'
        "{% blocktranslate count c=r.count %}{{ c }} item{% plural %}{{ c }} items"
        "{% endblocktranslate %}{% endfor %}"
    ),
    "loop.empty": "{% for r in rows %}{% endfor %}",
}


def make_engine() -> Engine:
    return Engine(libraries={"i18n": "django.templatetags.i18n"})


def measure(fn, iters: int, repeats: int) -> float:
    for _ in range(max(3, iters // 4)):
        fn()
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(iters):
            fn()
        best = min(best, (time.perf_counter() - start) / iters)
    return best * 1e6


def main() -> int:
    shimmed = "django_rust_templates" in template_base.__file__
    print(f"tree:    {template_base.__file__}")
    print(f"django:  {django.get_version()}")
    print(f"side:    {'rust-shim' if shimmed else 'stock'}")
    print(f"rows:    {N}")
    print()

    engine = make_engine()
    rows = [{"name": f"Row {i}", "value": i * 1.5, "count": i, "a": {"b": i}} for i in range(N)]
    ctx = Context({"rows": rows})

    for name, source in WORKLOADS.items():
        template = engine.from_string(source)
        template.render(ctx)  # warm
        us = measure(lambda t=template: t.render(ctx), 200, 7)
        print(f"{name:22s} {us:9.3f} us/call   {us / N:8.3f} us/unit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
