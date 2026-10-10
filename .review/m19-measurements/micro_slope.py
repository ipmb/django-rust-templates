#!/usr/bin/env python
"""Is the empty-template subtraction valid, or only its intercept?

`micro_baseline.py` answers Pete's question by subtracting each engine's cost for a
template that renders nothing, at a single point. That is only sound if the cost of
a row is **additive** — `cost(n) = a + b·n`, with `a` the per-call overhead the
empty template measures and `b` the per-construct cost — and the empty-template
control can only ever validate `a`. A text node's residual being ~0.06 us says the
intercept is real; it says nothing about whether the intercept *adds*.

So this measures the same loop at several row counts and fits the line, which is a
**stronger** instrument than the subtraction for two reasons:

* it does not assume the empty template's overhead equals the row's — it measures
  each engine's own intercept from that engine's own points;
* the slope is per-iteration cost, which is what "one construct" means, and it is
  independent of how many iterations the row happens to use.

If the fit's slope disagrees with `(row_us - empty_us) / n`, the subtraction is not
cancelling what it claims to and the residual column must not be published. If they
agree, the subtraction is validated *and* the slope gives a cleaner number.

Same instrument discipline as the sibling probe: `bench.calibrate`/`bench.measure`,
one pin per row count taken on the control, one engine per workload.

Run:  .venv/bin/python -I .review/m19-measurements/micro_slope.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import bench  # noqa: E402
import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

REPEATS = 7
TARGET = 0.05

# The row counts. The largest is the corpus's own 50 so the fit's top point is the
# row the report prints; two below it give the line something to be a line through.
COUNTS = (5, 20, 50)

EMPTY_NAME = "__empty__.html"


def measure_at(engines, control, source, rows, template_entry="page.html"):
    """Per-call microseconds for `source` with `len(rows)` loop iterations, per engine."""
    templates = {template_entry: source, EMPTY_NAME: ""}
    built = {
        name: bench_engines.build(name, templates, {}) for name in engines
    }
    data = {"rows": rows}
    contexts = {name: built[name].context(data) for name in engines}
    w = bench_corpus.Workload(
        "probe/loop", "probe", "probe", templates, template_entry, data, (), False, ""
    )
    # Pin on the control at *this* row count, so every engine on this point is timed
    # over the same number of calls. (The point-to-point comparison is between the
    # fitted lines, not between two points, so a per-point pin is not a bias: each
    # point is a best-of-N on its own row, exactly as the report takes it.)
    pin = bench.calibrate(
        built[control], w, built[control].get(template_entry), bench.RENDER,
        contexts[control], TARGET,
    )
    empty = {}
    out = {}
    for name in engines:
        eng = built[name]
        out[name] = bench.measure(
            eng, w, eng.get(template_entry), bench.RENDER, contexts[name],
            REPEATS, TARGET, pin_iters=pin,
        )[0]
    # The empty template is measured at the same pin too, so the subtraction's two
    # terms are taken under one precision.
    for name in engines:
        eng = built[name]
        empty[name] = bench.measure(
            eng, w, eng.get(EMPTY_NAME), bench.RENDER, contexts[name],
            REPEATS, TARGET, pin_iters=pin,
        )[0]
    return out, empty


def main():
    engines = [e for e in bench_engines.ENGINES if e not in bench_engines.missing_engines()]
    control = bench_engines.CONTROL
    print("interpreter : %s" % sys.version.split()[0])
    print("instrument  : bench.measure, best of %d, pinned on the control per point" % REPEATS)
    print()

    for key, source in (
        ("micro/loop-text", "{% for r in rows %}<tr><td>x</td></tr>{% endfor %}"),
        ("micro/loop-var", "{% for r in rows %}<tr><td>{{ r.name }}</td></tr>{% endfor %}"),
    ):
        print("=" * 78)
        print(key)
        print("=" * 78)
        points = {}
        empties = {}
        for n in COUNTS:
            rows = bench_corpus._rows(n)
            cost, empty = measure_at(engines, control, source, rows)
            points[n] = cost
            empties[n] = empty
            print(
                "  n=%-4d %s"
                % (
                    n,
                    "  ".join("%s %.3f (empty %.3f)" % (e, cost[e], empty[e]) for e in engines),
                )
            )
        n0, n1 = COUNTS[0], COUNTS[-1]
        print()
        print("  %-8s %12s %12s %12s %14s" % ("engine", "slope us/it", "intercept us", "sub/n us/it", "slope vs sub"))
        for name in engines:
            slope = (points[n1][name] - points[n0][name]) / (n1 - n0)
            intercept = points[n0][name] - slope * n0
            # the subtraction's own answer at the corpus's n=50
            subn = (points[50][name] - empties[50][name]) / 50
            print(
                "  %-8s %12.4f %12.4f %12.4f %14s"
                % (name, slope, intercept, subn, "%.2fx" % (slope / subn))
            )
        print()
        print("  ratios to the control, by each method:")
        for method, get in (
            ("slope", lambda e: (points[n1][e] - points[n0][e]) / (n1 - n0)),
            ("sub/n  ", lambda e: (points[50][e] - empties[50][e]) / 50),
            ("raw    ", lambda e: points[50][e] / points[50][control]),
        ):
            d = get(control)
            print(
                "    %s  %s"
                % (method, "  ".join("%s %.2fx" % (e, get(e) / d) for e in engines))
            )
        print()


main()
