#!/usr/bin/env python
"""What the corpus's loader choice costs each engine.

`bench_engines.build` gives both Django-side engines `loaders=[(<locmem>, {...})]`
— an *uncached* loader — so every `{% include %}` and every `{% extends %}` looks
its parent up again and parses it again on every render. Stock Django's default
is the opposite: `Engine(loaders=None)` wraps the filesystem and app-directories
loaders in `cached.Loader`, so a real project parses a partial once and then only
renders it. Jinja2 caches compiled templates by construction.

This measures the same corpus twice, once under each configuration, so the size
of the artefact is a number rather than an argument. `render` mode only; the
`parse` row's `from_string` never touches the loader.

Run:  .venv/bin/python -I .review/m19-measurements/loader_cache_probe.py
"""

import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

REPEATS = 5
TARGET = 0.05

LOADERS = {
    "django": "django.template.loaders.locmem.Loader",
    "rust": "django_rust_templates.engine.loaders.locmem.Loader",
}
CACHED = {
    "django": "django.template.loaders.cached.Loader",
    "rust": "django_rust_templates.engine.loaders.cached.Loader",
}


class _Engine:
    """`bench_engines.DjangoEngine`'s call shape, with the loader spelled here."""

    def __init__(self, engine_cls, context_cls, loaders, libraries):
        self._context_cls = context_cls
        self._engine = engine_cls(loaders=loaders, libraries=dict(libraries))

    def from_string(self, source):
        return self._engine.from_string(source)

    def context(self, data):
        return self._context_cls(data)

    def render(self, handle, context):
        return handle.render(context)

    def close(self):
        pass


def build(name, templates, libraries, cached):
    if name == "jinja2":
        return bench_engines.build(name, templates, libraries)
    from django.template.context import Context as StockContext
    from django.template.engine import Engine as StockEngine

    from django_rust_templates.engine.context import Context as OurContext
    from django_rust_templates.engine.engine import Engine as OurEngine

    engine_cls, context_cls = (
        (StockEngine, StockContext) if name == "django" else (OurEngine, OurContext)
    )
    inner = (LOADERS[name], dict(templates))
    loaders = [(CACHED[name], [inner])] if cached else [inner]
    return _Engine(engine_cls, context_cls, loaders, libraries)


def best_us(fn, repeats=REPEATS, target=TARGET):
    n = 1
    while True:
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        if time.perf_counter() - t0 >= target:
            break
        n *= 2
    best = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = (time.perf_counter() - t0) / n
        best = dt if best is None else min(best, dt)
    return best * 1e6


print("python:  %s on %s" % (platform.python_version(), platform.platform()))
print("best of %d, calibrated to %.2fs per sample; `render` mode" % (REPEATS, TARGET))
print()
print("%-24s %9s %9s %9s %9s %9s %9s" % (
    "row", "django", "django*", "rust", "rust*", "jinja2", "ours*/jinja2"))
print("-" * 88)

for workload in bench_corpus.workloads():
    engines = [e for e in bench_engines.ENGINES if e in workload.engines]
    if "rust" not in engines or "django" not in engines:
        continue
    source = workload.templates[workload.entry]
    row = {}
    for name in engines:
        for cached in ((False, True) if name != "jinja2" else (False,)):
            engine = build(name, workload.templates, workload.libraries(name), cached)
            handle = engine.from_string(source)
            context = engine.context(dict(workload.data))
            first = engine.render(handle, context)
            # Rendered twice: with a cached loader the first render is the only
            # one that parses, so timing from the second is timing the design.
            row[(name, cached)] = best_us(lambda: engine.render(handle, context))
            engine.close()
    label = workload.key
    ours_star = row[("rust", True)]
    jinja = row.get(("jinja2", False))
    ratio = f"{ours_star / jinja:9.3f}" if jinja else f"{'-':>9s}"
    print("%-24s %9.3f %9.3f %9.3f %9.3f %9s %s" % (
        label,
        row[("django", False)], row[("django", True)],
        row[("rust", False)], row[("rust", True)],
        f"{jinja:.3f}" if jinja else "-", ratio))
