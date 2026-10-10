#!/usr/bin/env python
"""Micro-cost of the render path's crossings, ours vs stock, same operations."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "scripts"))
import filter_probe  # noqa: E402

filter_probe.ensure_settings()
sys.path.insert(0, str(ROOT))

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine.context import Context  # noqa: E402
from django.template.context import Context as StockContext  # noqa: E402
from django.template.defaultfilters import floatformat as stock_floatformat  # noqa: E402
from django.template.base import Variable as StockVariable  # noqa: E402


def best(fn, iters=20000, repeats=7):
    for _ in range(iters // 10):
        fn()
    out = []
    for _ in range(repeats):
        t = time.perf_counter()
        for _ in range(iters):
            fn()
        out.append((time.perf_counter() - t) / iters)
    return min(out) * 1e6


def row(label, ours, stock):
    a, b = best(ours, 20000), best(stock, 20000)
    print(f"{label:34s} ours {a:8.3f}   stock {b:8.3f}   ratio {a / b:5.2f}")


def main():
    ctx = Context({"a": "x", "b": {"c": {"d": "y"}}})
    sctx = StockContext({"a": "x", "b": {"c": {"d": "y"}}})

    print(f"{'operation':34s} {'ours':>16s}   {'stock':>15s}")
    row(
        "resolve bare name",
        lambda: _native.resolve("a", ctx),
        lambda: StockVariable("a").resolve(sctx),
    )
    row(
        "resolve depth 3",
        lambda: _native.resolve("b.c.d", ctx),
        lambda: StockVariable("b.c.d").resolve(sctx),
    )
    row(
        "filter upper (no arg)",
        lambda: _native.filter("upper", "x", ()),
        lambda: __import__("django.template.defaultfilters", fromlist=["upper"]).upper("x"),
    )
    row(
        "filter floatformat:2",
        lambda: _native.filter("floatformat", 1.5, ("2",)),
        lambda: stock_floatformat(1.5, "2"),
    )
    row(
        "filter length",
        lambda: _native.filter("length", [1, 2, 3], ()),
        lambda: __import__("django.template.defaultfilters", fromlist=["length"]).length([1, 2, 3]),
    )

    print()
    print("--- rust floor/desc vs the python walk, 200 identical variables ---")
    src = "".join(f"<p>{{{{ a }}}}</p>" for _ in range(200))
    tree = _native.parse_desc(src, False, None, None, None, None)
    ctx200 = Context({"a": "x"})

    print(f"render_floor   {best(lambda: _native.render_floor(tree, ctx200), 3000):8.3f} us")
    print(f"render_desc    {best(lambda: _native.render_desc(tree, ctx200), 3000):8.3f} us")

    from django.template.engine import Engine as StockEngine  # noqa: E402

    t = StockEngine().from_string(src)
    sc = StockContext({"a": "x"})
    print(f"stock walk     {best(lambda: t.render(sc), 3000):8.3f} us")


if __name__ == "__main__":
    main()
