#!/usr/bin/env python
"""The project's first benchmark: stock Django vs the Rust-backed engine.

Run the *same* file under both sides; the harness prints which tree it loaded so
a transcript cannot be read as the run it is not.

    source scripts/env.sh
    # our engine (shim installed)
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        .venv/bin/python scripts/bench.py
    # stock Django (control)
    DJANGO_RUST_TEMPLATES=0 PYTHONPATH="$PWD/.django" \
        .venv/bin/python -I scripts/bench.py

Four workloads, matching the four-row table that motivated this milestone (§M16):

  realistic/render   a 50-row loop with i18n `{% translate %}` /
                     `{% blocktranslate %}` over `{% extends %}` inheritance,
                     **pre-compiled** and rendered repeatedly — the render hot
                     path with the parse removed.
  realistic/parse    the same page through `Engine.get_template` + `render`, so
                     each iteration parses and renders.
  large/parse        a generated ~200-node / 6.8 KB template, parse + render.
  tiny/parse         `{{ a|upper }}`, parse + render — the fixed-overhead row.

What is measured: `perf_counter` around a tight inner loop of `iters` calls,
repeated `--repeats` times; the reported figure is the **best** per-call time in
microseconds (best-of-N is the stable estimator for a warm, CPU-bound loop; the
spread is printed too, so a noisy run is visible rather than hidden).

`--count` wraps every `_native` entry point and reports how many times each is
called per render — the FFI-crossing census. It needs the shim side; stock has no
`_native`.

The module never edits a template to make a side agree: the two sides load the
same source strings, and each workload's output is printed as a sha256 so the two
transcripts can be compared byte for byte. They are two processes, so the
comparison is a `diff` of the two runs, not a flag here.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

# Settings are configured in exactly one place in this tree, and this file is not
# it: `tests/test_settings_configuration.py` refuses a second `settings.configure`
# under `scripts/` or `tests/`, because `settings.configure` is a no-op once
# settings exist, so *which module runs first* would decide the values. Reusing
# `filter_probe.ensure_settings` also removes this harness's own `.django`
# `sys.path` insert, which the stock side needs under `-I` (`-I` implies `-E`, so
# `PYTHONPATH` is ignored and `import django` dies): filter_probe does it at
# import, for the same reason, and one copy cannot disagree with itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.template import base as template_base  # noqa: E402

# --- the templates -----------------------------------------------------------
# `{% load i18n %}` sits in *both* files: `{% load %}` is per-file, so a parent
# that uses `{% translate %}` without it fails the parse even when the child has
# loaded the library.

BASE = """{% load i18n %}<html><head><title>{% block title %}Default{% endblock %}</title></head>
<body><h1>{% block heading %}{% translate "Report" %}{% endblock %}</h1>
{% block content %}{% endblock %}</body></html>"""

PAGE = """{% extends "base.html" %}{% load i18n %}
{% block title %}{% translate "Monthly Report" %}{% endblock %}
{% block content %}
<table>
{% for row in rows %}
<tr><td>{{ row.name }}</td><td>{{ row.value|floatformat:2 }}</td>
<td>{% blocktranslate count count=row.count %}{{ count }} item{% plural %}{{ count }} items{% endblocktranslate %}</td></tr>
{% endfor %}
</table>
{% endblock %}"""

ROWS = [{"name": f"Row {i}", "value": i * 1.5, "count": i} for i in range(50)]


def large_template() -> str:
    """A ~200-node, ~6.8 KB template: parse-heavy, no loop, no i18n.

    Alternating `{{ row.N }}` and `{% if %}`/`{% for %}` blocks so the node count
    is dominated by variables and control tags rather than text.
    """
    parts = ['{% load i18n %}']
    parts.append("<article>")
    for i in range(20):
        parts.append(f'<section id="s{i}">')
        # ten variable nodes each
        for j in range(10):
            parts.append(f'<p>{{{{ section{i}.field{j} }}}}</p>')
        # one if + one for block each, with variables inside
        parts.append(f"{{% if section{i}.enabled %}}<b>{{{{ section{i}.label }}}}</b>{{% endif %}}")
        parts.append(
            f"{{% for item in section{i}.items %}}<li>{{{{ item.name }}}}: "
            f"{{{{ item.value }}}}</li>{{% endfor %}}"
        )
        parts.append("</section>")
    parts.append("</article>")
    return "".join(parts)


LARGE = large_template()


def make_engine() -> Engine:
    return Engine(
        loaders=[("django.template.loaders.locmem.Loader", {"base.html": BASE, "page.html": PAGE})],
        libraries={"i18n": "django.templatetags.i18n"},
    )


# --- measurement -------------------------------------------------------------


def measure(fn, iters: int, repeats: int) -> tuple[float, float, str]:
    """Best and worst per-call microseconds, plus the joined output of one call.

    `fn` is called with no arguments and must return a `str`; one return value is
    kept so the caller can hash it and compare the two engines' bytes.
    """
    for _ in range(max(3, iters // 4)):  # warmup
        out = fn()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(iters):
            out = fn()
        samples.append((time.perf_counter() - start) / iters)
    return min(samples) * 1e6, max(samples) * 1e6, out


def build_workloads(engine: Engine):
    """Each entry: (name, callable-per-measurement, iter count)."""

    def realistic_render():
        t = engine.get_template("page.html")
        ctx = Context({"rows": ROWS})
        return lambda: t.render(ctx)

    def realistic_parse():
        ctx = Context({"rows": ROWS})
        return lambda: engine.get_template("page.html").render(ctx)

    def large_parse():
        ctx = Context({"s": {}})
        # `engine.from_string`, not `Template(...)`: the bare `Template` reaches
        # `Engine.get_default()`, which needs a TEMPLATES setting this harness
        # does not configure. Same compile-then-render work either way.
        return lambda: engine.from_string(LARGE).render(ctx)

    def tiny_parse():
        ctx = Context({"a": "hello world"})
        return lambda: engine.from_string("{{ a|upper }}").render(ctx)

    return [
        ("realistic/render", realistic_render, 200),
        ("realistic/parse", realistic_parse, 100),
        ("large/parse", large_parse, 100),
        ("tiny/parse", tiny_parse, 2000),
    ]


# --- the FFI census ----------------------------------------------------------


class Counter:
    """Counts calls per `_native` name over one workload's iteration."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self._originals = {}

    def install(self, native):
        for name in dir(native):
            if name.startswith("_"):
                continue
            fn = getattr(native, name)
            # Skip classes: `_native.ParseError` and friends are callable but
            # wrapping one breaks `except _native.ParseError` in `render.py`.
            if isinstance(fn, type) or not callable(fn):
                continue
            self.counts[name] = 0
            self._originals[name] = fn

            def make(name, fn):
                def wrapper(*a, **k):
                    self.counts[name] += 1
                    return fn(*a, **k)

                return wrapper

            setattr(native, name, make(name, fn))

    def uninstall(self, native):
        for name, fn in self._originals.items():
            setattr(native, name, fn)


def run_census(engine: Engine, iters: int) -> dict[str, int]:
    """Per-workload `_native` call counts, as deltas over `iters` iterations."""
    from django_rust_templates import _native

    counter = Counter()
    counter.install(_native)
    try:
        for name, make, _ in build_workloads(engine):
            fn = make()
            for _ in range(3):  # warmup so import-lazy paths settle
                fn()
            before = dict(counter.counts)
            for _ in range(iters):
                fn()
            delta = {n: counter.counts[n] - before[n] for n in counter.counts}
            per_call = {n: c / iters for n, c in delta.items() if c}
            detail = ", ".join(f"{n}={c:.1f}/call" for n, c in
                               sorted(per_call.items(), key=lambda kv: -kv[1]))
            print(f"  census {name:18s} {detail or '(no crossings)'}")
    finally:
        counter.uninstall(_native)
    return counter.counts


# --- main --------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every iter count")
    ap.add_argument("--count", action="store_true", help="print the FFI call census (shim side)")
    args = ap.parse_args()

    shimmed = "django_rust_templates" in template_base.__file__
    print(f"tree:    {template_base.__file__}")
    print(f"django:  {django.get_version()}")
    print(f"side:    {'rust-shim' if shimmed else 'stock'}")
    print()

    engine = make_engine()
    results = {}
    for name, make, iters in build_workloads(engine):
        iters = max(1, int(iters * args.scale))
        best, worst, out = measure(make(), iters, args.repeats)
        results[name] = out
        print(f"{name:18s} {best:9.1f} us/call   (worst {worst:9.1f}, {iters} iters)")

    if args.count and shimmed:
        print()
        run_census(engine, 50)

    # There is no `--check` flag: the two sides are two processes, so the hash of
    # each workload's output is printed and a wrapper compares them.
    print()

    for name, out in results.items():
        print(f"sha256 {name:18s} {hashlib.sha256(out.encode()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
