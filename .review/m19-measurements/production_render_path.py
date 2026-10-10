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
    counts = {"_native.resolve_lookups": 0, "_native.resolve": 0, "Variable._resolve_lookup": 0}
    real_native = _native.resolve
    real_lookups = _native.resolve_lookups
    real_lookup = eng_base.Variable._resolve_lookup

    def counting_native(*a, **kw):
        counts["_native.resolve"] += 1
        return real_native(*a, **kw)

    def counting_components(*a, **kw):
        counts["_native.resolve_lookups"] += 1
        return real_lookups(*a, **kw)

    def counting_lookup(self, *a, **kw):
        counts["Variable._resolve_lookup"] += 1
        return real_lookup(self, *a, **kw)

    _native.resolve = counting_native
    _native.resolve_lookups = counting_components
    eng_base.Variable._resolve_lookup = counting_lookup
    try:
        ctx = RustContext({"a": "1", "b": {"c": "2"}, "d": {"e": {"f": "3"}}})
        rendered = engine.get_template("page.html").render(ctx)
    finally:
        _native.resolve = real_native
        _native.resolve_lookups = real_lookups
        eng_base.Variable._resolve_lookup = real_lookup

    print("1. which resolver a shipped render calls")
    print("   template   : '{{ a }}|{{ b.c }}|{{ d.e.f }}'          (3 variables)")
    print(f"   rendered   : {rendered!r}")
    print(f"   call counts: {counts}")
    print("   `_native.resolve` reads 0 since M19's step 1: the shell passes the")
    print("   components (`resolve_lookups`) instead of re-joining them into a path")
    print("   this call would classify a second time. A reverted join shows up here")
    print("   as a non-zero first count, and `test_interposition.py`'s")
    print("   `test_variable_resolution_reaches_rust` asserts both halves of it.")
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

    def one_round(verbose):
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
                if verbose:
                    print(
                        "   %-4d %-6s %10.2f %10.2f %10.2f %10.4f"
                        % (n, label, us_v, us_t, us_v - us_t, (us_v - us_t) / n)
                    )
        return {n: rows[("rust", n)] / rows[("stock", n)] for n in (50, 200)}

    # **The ratio is a difference of two differences, and it is reported with its
    # spread rather than to a third decimal.** Three runs of the single-round
    # version printed 1.360, 1.375 and 1.402 for this same quantity on this same
    # tree, so a figure quoted to three places is a run, not a measurement. The
    # round whose absolute costs are printed is round 1; the ratios below are over
    # all of them.
    ROUNDS = 5
    ratios = [one_round(verbose=True)]
    for _ in range(ROUNDS - 1):
        ratios.append(one_round(verbose=False))
    print()
    for n in (50, 200):
        seen = sorted(round_[n] for round_ in ratios)
        print(
            "   N=%d rust/stock per-variable ratio: median %.3f, range %.3f-%.3f over %d rounds"
            % (n, seen[len(seen) // 2], seen[0], seen[-1], ROUNDS)
        )
        print("       every round: " + "  ".join("%.3f" % value for value in (r[n] for r in ratios)))
    print()


# --- 3. the split inside _native.resolve, and render_value_in_context by type ----


def per_operation():
    stock_ctx = __import__("django.template.context", fromlist=["Context"]).Context(
        {"a": "x", "b": {"c": {"d": "x"}}, "row": {"name": "x"}}
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

    print("3c. the same three quantities at the depth the corpus actually uses")
    print(
        "   The reviewer's census of every variable resolved during a render of all\n"
        "   18 rust rows: 1408 variables, of which 27 are depth 1 (2%), 1380 are\n"
        "   depth 2 (98%) and one is depth 3. `{{ row.name }}` is the corpus.\n"
        "   Section 3a above measures depth 1 and depth 3 — the two the corpus is\n"
        "   not — so the row the spec quotes is the one below, not the one above.\n"
        "   The census is the reviewer's instrument; these timings are this probe's,\n"
        "   on the corpus's own spelling.\n"
        "   **The `Variable` objects are hoisted, and that is not a detail.** The\n"
        "   first draft of this section built `Variable(path)` inside the timed\n"
        "   lambda, as §3a does for `resolve` — harmless there, because `resolve`\n"
        "   takes a *string* — and every stock row came out at 1.68-1.71 us against\n"
        "   a depth-1 value of 0.22. That is `Variable.__init__` (about 1.4 us: a\n"
        "   `float()` that raises, then `unescape_string_literal` that raises) being\n"
        "   timed as resolution, and it flattered ours by making stock look five\n"
        "   times slower than it is."
    )
    print()
    hoisted = {p: Variable(p) for p in ("a", "row.name", "b.c.d")}
    print(
        "   **`par %` is an upper bound on the parse's share, not the share.** It\n"
        "   divides by `_native.parse_variable` — a pyfunction that returns a\n"
        "   `#[pyclass] Variable` with `lookups`, `literal` and `translate` on it —\n"
        "   while `_native.resolve` calls the internal Rust\n"
        "   `parse_variable`, which builds no Python object. The column is printed\n"
        "   because it is what an earlier revision of the spec quoted, and the row\n"
        "   that replaced it is §3d; it is not a decomposition of the column to its\n"
        "   left and must not be subtracted from it."
    )
    print()
    print(
        "   %-30s %8s %8s %9s %9s %9s"
        % ("", "ours res", "ours par", "par %", "stock res", "ours/stock")
    )
    for path in ("a", "row.name", "b.c.d"):
        resolve_us = timed(lambda p=path: _native.resolve(p, stock_ctx))
        parse_us = timed(lambda p=path: _native.parse_variable(p))
        stock_us = timed(lambda v=hoisted[path]: v.resolve(stock_ctx))
        print(
            "   %-30s %8.3f %8.3f %8.0f%% %9.3f %10.2fx"
            % (
                f'_native.resolve("{path}", ctx)',
                resolve_us,
                parse_us,
                parse_us / resolve_us * 100,
                stock_us,
                resolve_us / stock_us,
            )
        )
    print()

    print("3d. the same three quantities after step 1, which is the point of step 1")
    print(
        "   The shell calls `_native.resolve_lookups(self.lookups, context)` — no\n"
        "   `\".\".join`, no `parse_variable`. The row to compare against is 3c's,\n"
        "   not 3a's.\n"
        "   **`ours par` is absent here rather than repeated**: there is no parse on\n"
        "   this path, so the column would have to be borrowed from 3c, and a column\n"
        "   borrowed from the row above is how a table stops being a measurement.\n"
        "   **And 3c's `par %` column is an upper bound, not a share** — flagged\n"
        "   there now rather than only here. It times the `_native.parse_variable`\n"
        "   *pyfunction*, which returns a `#[pyclass] Variable` carrying `lookups`,\n"
        "   `literal` and `translate`; `resolve` calls the internal\n"
        "   `template_core::parser::variable::parse_variable`, which builds a Rust\n"
        "   struct and no Python object at all. So the two are not the same\n"
        "   quantity, and the column cannot be subtracted from the resolve it sits\n"
        "   under. The measurement below is what *is* subtractable."
    )
    print()
    ROUNDS_D = 5

    def step_one_round():
        out = {}
        for path in ("a", "row.name", "b.c.d"):
            # `eng_base.Variable` and not the stock one imported at the top: the
            # components the shell hands over are the ones *our* `Variable.__init__`
            # split, and borrowing stock's tuple would compare the resolver on an
            # input the shipped path never builds. They are equal — `__init__` is a
            # transcription — but only one of the two is the shell's.
            components = list(eng_base.Variable(path).lookups)
            before = timed(lambda p=path: _native.resolve(p, stock_ctx))
            after = timed(lambda c=components: _native.resolve_lookups(c, stock_ctx))
            stock_us = timed(lambda v=hoisted[path]: v.resolve(stock_ctx))
            out[path] = (before, after, stock_us)
        return out

    rounds_d = [step_one_round() for _ in range(ROUNDS_D)]
    print(
        "   %-32s %8s %8s %9s %9s %10s"
        % ("", "before", "after", "saved", "stock res", "after/stock")
    )
    for path in ("a", "row.name", "b.c.d"):
        before = sorted(r[path][0] for r in rounds_d)[ROUNDS_D // 2]
        after = sorted(r[path][1] for r in rounds_d)[ROUNDS_D // 2]
        stock_us = sorted(r[path][2] for r in rounds_d)[ROUNDS_D // 2]
        components = list(eng_base.Variable(path).lookups)
        print(
            "   %-32s %8.3f %8.3f %9.3f %9.3f %10.2fx"
            % (
                f"resolve_lookups({components!r})",
                before,
                after,
                before - after,
                stock_us,
                after / stock_us,
            )
        )
    for path in ("a", "row.name", "b.c.d"):
        saved = sorted(r[path][0] - r[path][1] for r in rounds_d)
        print(
            "   %-32s delta over %d rounds: median %+.3f, range %+.3f to %+.3f"
            % (path, ROUNDS_D, saved[ROUNDS_D // 2], saved[0], saved[-1])
        )
    print(
        "   **The saving is ~0.08 us at depth 2, not the 0.25 the `par` column\n"
        "   above would suggest, and the gap is the two quantities differing —\n"
        "   not the win being halved.** Step 1 does not remove a whole\n"
        "   `parse_variable`; it removes the *classification* half and pays a\n"
        "   `Vec<String>` extraction for the components, which the path form did\n"
        "   not pay (one borrowed `&str` became one list walk and one owned\n"
        "   `String` per bit). That is why this row is a range of differences and\n"
        "   not a subtraction of two table cells."
    )
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
print(
    "recorded    : one run. This is a timing probe, so a second run of it differs\n"
    "              in most of its numbers — measured, 52 of 110 lines between two\n"
    "              consecutive runs on this tree, and every one of them a figure\n"
    "              the surrounding text brackets with a spread. The sha above\n"
    "              pins the *artifact*; it does not make the digits repeat."
)
print()

resolver_census()
per_variable()
per_operation()
