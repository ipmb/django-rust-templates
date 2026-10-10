#!/usr/bin/env python
"""What a shipped render actually pays, per variable, and where.

The M19 review priced our resolver two ways and got two answers — 0.499 for
`_native.resolve("a", ctx)` and "<= 0.316" by floor differencing — and concluded
the render path "never" calls `_native.resolve`. That conclusion decides which
number a reader takes, so it is worth one probe rather than an argument.

Three measurements, all against the extension `bench.py` prints:

  1. **Which resolver a shipped render calls**, by counting calls inside a real
     `Engine.get_template(...).render(context)` — the class `bench_engines.py`
     builds for the `rust` column, not the M18.0 `render_floor` prototype.
  2. **Per-variable cost on the shipped path**, by differencing a page of N
     `{{ a }}` against a page of N text nodes of the same width — the reviewer's
     own method, run on the shipped engine rather than on the prototype.
  3. **`render_value_in_context` by value type**, because §2's "~0.03 for
     escaping" is a plain-`str` number and the corpus's value mix was never
     measured.

Every value printed goes through `{v!r}` and never `type(v).__name__`: a
transcript that names an input it did not evaluate cannot be pasted back.

Run:  .venv/bin/python -I .review/m19-measurements/production_render_path.py
"""

import hashlib
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

# **The settings come from the harness, not from this file.** The first draft of
# this probe configured its own, with `USE_I18N=False`, and every non-`str` row
# came out at half the harness's cost — `render_value_in_context(12, ctx)` is
# 1.67 us with i18n off and 3.56 us with it on, because `localize` short-circuits
# on the setting. `USE_I18N` is `True` in Django's defaults and in
# `filter_probe.ensure_settings()`, so the hand-built fixture was the flattering
# one. A probe that measures the harness's subject runs the harness's settings.
import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django.template.base import Variable, render_value_in_context  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine import base as eng_base  # noqa: E402
from django_rust_templates.engine.context import Context as RustContext  # noqa: E402
from django_rust_templates.engine.engine import Engine as RustEngine  # noqa: E402

REPEATS = 7


def timed(fn, iters=20000):
    """Best-of-`REPEATS` per-call microseconds; the return value is kept alive."""
    holder = []
    bestus = float("inf")
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for _ in range(iters):
            holder.append(fn())
        dt = (time.perf_counter() - t0) / iters * 1e6
        holder.clear()
        bestus = min(bestus, dt)
    return bestus


def page(n, kind):
    """`n` constructs in a row: `{{ a }}` (7 chars) or a text node of 7 chars."""
    return "".join(("{{ a }}" if kind == "var" else "xxxxxxx") for _ in range(n))


def rust_engine(source):
    return RustEngine(
        loaders=[
            (
                "django_rust_templates.engine.loaders.locmem.Loader",
                {"page.html": source},
            )
        ],
        libraries={},
    )


# --- 1. which resolver a shipped render calls ------------------------------------


def resolver_census():
    engine = rust_engine("{{ a }}|{{ b.c }}|{{ d.e.f }}")
    counts = {"_native.resolve": 0, "Variable._resolve_lookup": 0}
    real_native = _native.resolve
    real_lookup = eng_base.Variable._resolve_lookup

    def counting_native(*a, **kw):
        counts["_native.resolve"] += 1
        return real_native(*a, **kw)

    def counting_lookup(self, *a, **kw):
        counts["Variable._resolve_lookup"] += 1
        return real_lookup(self, *a, **kw)

    _native.resolve = counting_native
    eng_base.Variable._resolve_lookup = counting_lookup
    try:
        ctx = RustContext({"a": "1", "b": {"c": "2"}, "d": {"e": {"f": "3"}}})
        rendered = engine.get_template("page.html").render(ctx)
    finally:
        _native.resolve = real_native
        eng_base.Variable._resolve_lookup = real_lookup

    print("1. which resolver a shipped render calls")
    print("   template   : '{{ a }}|{{ b.c }}|{{ d.e.f }}'          (3 variables)")
    print(f"   rendered   : {rendered!r}")
    print(f"   call counts: {counts}")
    print()


# --- 2. per-variable cost on the shipped path ------------------------------------


def per_variable():
    from django.template.context import Context as StockContext
    from django.template.engine import Engine as StockEngine

    def stock_engine(source):
        return StockEngine(
            loaders=[("django.template.loaders.locmem.Loader", {"page.html": source})],
            libraries={},
        )

    # The subtraction is only like-for-like if both bodies emit the same bytes at
    # the same width, so it is asserted rather than assumed.
    rv = rust_engine(page(1, "var")).get_template("page.html").render(RustContext({"a": "x"}))
    rt = rust_engine(page(1, "text")).get_template("page.html").render(RustContext({"a": "x"}))
    print("2. per-variable cost on the shipped path  (best of %d)" % REPEATS)
    print(f"   N=1 rust  `{{{{ a }}}}` -> {rv!r}")
    print(f"   N=1 rust  text node   -> {rt!r}   (same width, same bytes)")
    print()
    print(
        "   %-4s %-6s %10s %10s %10s %10s"
        % ("N", "engine", "vars us", "text us", "delta", "per-var")
    )
    rows = {}
    for n in (50, 200):
        for label, make in (("rust", rust_engine), ("stock", stock_engine)):
            ctx_cls = RustContext if label == "rust" else StockContext
            hv = make(page(n, "var")).get_template("page.html")
            ht = make(page(n, "text")).get_template("page.html")
            ctx = ctx_cls({"a": "x"})
            us_v = timed(lambda: hv.render(ctx), 300)
            us_t = timed(lambda: ht.render(ctx), 300)
            rows[(label, n)] = us_v - us_t
            print(
                "   %-4d %-6s %10.2f %10.2f %10.2f %10.4f"
                % (n, label, us_v, us_t, us_v - us_t, (us_v - us_t) / n)
            )
    for n in (50, 200):
        print(
            "   N=%d rust/stock per-variable ratio = %.3f"
            % (n, rows[("rust", n)] / rows[("stock", n)])
        )
    print()


# --- 3. the split inside _native.resolve, and render_value_in_context by type ----


def per_operation():
    stock_ctx = __import__("django.template.context", fromlist=["Context"]).Context(
        {"a": "x", "b": {"c": {"d": "x"}}}
    )
    v_a, v_d3 = Variable("a"), Variable("b.c.d")

    print("3a. the split inside `_native.resolve`")
    for label, fn in [
        ('_native.resolve("a", ctx)', lambda: _native.resolve("a", stock_ctx)),
        ("_native.parse_variable('a')", lambda: _native.parse_variable("a")),
        ('_native.resolve("b.c.d", ctx)', lambda: _native.resolve("b.c.d", stock_ctx)),
        ("_native.parse_variable('b.c.d')", lambda: _native.parse_variable("b.c.d")),
        ("stock Variable('a').resolve(ctx)", lambda: v_a.resolve(stock_ctx)),
        ("stock Variable('b.c.d').resolve(ctx)", lambda: v_d3.resolve(stock_ctx)),
        ('stock_ctx["a"]   (bare dict read)', lambda: stock_ctx["a"]),
    ]:
        print("   %-38s %8.3f us" % (label, timed(fn)))
    print()

    print("3b. render_value_in_context by value type  (autoescape is on here)")
    values = [
        ('str "hello world"', "hello world"),
        ('str "a & b < c"', "a & b < c"),
        ("SafeString(mark_safe('x'))", mark_safe("x")),
        ("int 12", 12),
        ("float 12.5", 12.5),
        ("Decimal('12.50')", Decimal("12.50")),
        ("datetime(2026, 10, 8, 12, 0)", datetime(2026, 10, 8, 12, 0)),
    ]
    ctx = RustContext({"a": "x"})
    for label, value in values:
        us = timed(lambda v=value: render_value_in_context(v, ctx))
        print("   %-38s %8.3f us   ->  %r" % (label, us, render_value_in_context(value, ctx)))
    print()


print("interpreter : %s" % sys.version.split()[0])
print("extension   : %s" % Path(_native.__file__).name)
print("sha256      : %s" % hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest()[:16])
print()

resolver_census()
per_variable()
per_operation()
