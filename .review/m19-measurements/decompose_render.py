#!/usr/bin/env python
"""Where the render gap lives: same page, one construct at a time.

Each row renders the same page shape with exactly one construct swapped out, on
one engine, output hashed so a row that is not the same template as its sibling
is visible. Run once per side and diff.

    .venv/bin/python -I <this> rust
    .venv/bin/python -I <this> stock
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "scripts"))
import filter_probe  # noqa: E402

filter_probe.ensure_settings()
sys.path.insert(0, str(ROOT))

BASE = """{% load i18n %}<html><head><title>{% block title %}Default{% endblock %}</title></head>
<body><h1>{% block heading %}{% translate "Report" %}{% endblock %}</h1>
{% block content %}{% endblock %}</body></html>"""

PAGE = """{% extends "base.html" %}{% load i18n %}
{% block title %}{% translate "Monthly Report" %}{% endblock %}
{% block content %}
<table>
__BODY__
</table>
{% endblock %}"""

# Each body is the full row minus the constructs the other rows isolate.
BODIES = {
    "full": """{% for row in rows %}
<tr><td>{{ row.name }}</td><td>{{ row.value|floatformat:2 }}</td>
<td>{% blocktranslate count count=row.count %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</td></tr>
{% endfor %}""",
    "no-filter": """{% for row in rows %}
<tr><td>{{ row.name }}</td><td>{{ row.value }}</td>
<td>{% blocktranslate count count=row.count %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</td></tr>
{% endfor %}""",
    "no-i18n": """{% for row in rows %}
<tr><td>{{ row.name }}</td><td>{{ row.value|floatformat:2 }}</td>
<td>{{ row.count }} items</td></tr>
{% endfor %}""",
    "bare-loop": """{% for row in rows %}
<tr><td>{{ row.name }}</td><td>{{ row.value }}</td>
<td>{{ row.count }}</td></tr>
{% endfor %}""",
    "text-loop": """{% for row in rows %}
<tr><td>x</td><td>y</td><td>z</td></tr>
{% endfor %}""",
    "no-loop": """<tr><td>{{ r.name }}</td><td>{{ r.value|floatformat:2 }}</td>
<td>{% blocktranslate count count=r.count %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</td></tr>""",
    "page-only": """{% for row in rows %}{% endfor %}""",
}

ROWS = [{"name": f"Row {i}", "value": i * 1.5, "count": i} for i in range(50)]


def best(fn, iters, repeats=7):
    for _ in range(max(3, iters // 4)):
        fn()
    out = []
    for _ in range(repeats):
        t = time.perf_counter()
        for _ in range(iters):
            fn()
        out.append((time.perf_counter() - t) / iters)
    return min(out) * 1e6, fn()


def main() -> int:
    side = sys.argv[1]
    import bench

    if side == "rust":
        loader = "django_rust_templates.engine.loaders.locmem.Loader"
        lib = "django_rust_templates.engine.templatetags.i18n"
    else:
        loader = "django.template.loaders.locmem.Loader"
        lib = "django.templatetags.i18n"

    print(f"engine: {side}")
    for name, body in BODIES.items():
        engine, ctx_cls = bench.make_engine(side)
        engine = type(engine)(
            loaders=[(loader, {"base.html": BASE, "page.html": PAGE.replace("__BODY__", body)})],
            libraries={"i18n": lib},
        )
        t = engine.get_template("page.html")
        c = ctx_cls({"rows": ROWS, "r": ROWS[0]})
        us, out = best(lambda: t.render(c), 300)
        print(f"{name:12s} {us:9.1f} us   sha256:{hashlib.sha256(out.encode()).hexdigest()[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
