#!/usr/bin/env python
"""What one rendered `{{ a }}` costs, decomposed, ours vs stock.

The M18.0 floor's comment says both its walks call the *same* two helpers for the
non-dispatch work: `LookupScope::resolve` and `django.template.base.render_value_in_context`.
If the second is a Python call per variable, the "floor" is bounded by Python and is
not a floor. This measures the pieces with the objects pre-built, so the number is
about the work and not about construction (an earlier version built `Variable("a")`
inside the loop and reported the floor 3x *faster* than stock; that was a measurement
of `Variable.__init__`).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "scripts"))
import filter_probe  # noqa: E402

filter_probe.ensure_settings()
sys.path.insert(0, str(ROOT))

from django.template.base import Variable, render_value_in_context  # noqa: E402
from django.template.context import Context as StockContext  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine.context import Context  # noqa: E402


def best(fn, iters=50000, repeats=7):
    for _ in range(iters // 10):
        fn()
    out = []
    for _ in range(repeats):
        t = time.perf_counter()
        for _ in range(iters):
            fn()
        out.append((time.perf_counter() - t) / iters)
    return min(out) * 1e6


def main():
    ours = Context({"a": "x", "b": {"c": {"d": "y"}}})
    stock = StockContext({"a": "x", "b": {"c": {"d": "y"}}})
    v_a = Variable("a")
    v_bcd = Variable("b.c.d")

    print(f"{'operation':44s} {'us':>8s}")
    print(f"{'_native.resolve(\"a\", ctx)':44s} {best(lambda: _native.resolve('a', ours)):8.3f}")
    print(f"{'stock Variable(\"a\").resolve(ctx)  pre-built':44s} {best(lambda: v_a.resolve(stock)):8.3f}")
    print(f"{'_native.resolve(\"b.c.d\", ctx)':44s} {best(lambda: _native.resolve('b.c.d', ours)):8.3f}")
    print(f"{'stock Variable(\"b.c.d\").resolve  pre-built':44s} {best(lambda: v_bcd.resolve(stock)):8.3f}")
    print()
    print(f"{'render_value_in_context(\"x\", ctx) (python)':44s} {best(lambda: render_value_in_context('x', stock)):8.3f}")
    print(f"{'conditional_escape(\"x\")':44s} {best(lambda: conditional_escape('x')):8.3f}")
    print(f"{'str(\"x\")':44s} {best(lambda: str('x')):8.3f}")
    print(f"{'\"\".join([\"x\", \"y\"])':44s} {best(lambda: ''.join(['x', 'y'])):8.3f}")
    print()
    print(f"{'stock ctx[\"a\"] (bare dict read)':44s} {best(lambda: stock['a']):8.3f}")


if __name__ == "__main__":
    main()
