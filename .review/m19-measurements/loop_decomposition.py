#!/usr/bin/env python
"""Decompose the two loop rows into a per-render term and a per-iteration term.

`micro/loop-text` and `micro/loop-var` are the corpus's two widest gaps and they
differ by exactly one construct: `{{ r.name }}` inside the body. Nothing in the
spec says *which* term of a loop row the gap lives in — the loop's setup (a
`context.push()`, the sequence resolve, the `forloop` dict, the final
`"".join`) is per render, and its body (one `context[var] = item`, one
`NodeList.render`, one or two node renders) is per iteration, and a report that
gives one number for the row cannot tell them apart.

**The method is a fit, not a subtraction.** A row measured at one N gives one
point; `cost(N) = a + b·N` fitted over six lengths gives the two terms, and a
difference between two single points is not either of them. The empty loop is
measured as well and printed beside the fitted `a`, because the two should
agree and a disagreement is the finding: the empty loop is the intercept
observed directly, and a fit that misses it is a fit over the wrong family.

**The like-for-like assertion is what licenses the differencing.** Every
rendering here is checked against a string built independently (`ROW * n`), on
every engine, before it is timed. Two rows that render different bytes are two
measurements of different work, and a slope between them is arithmetic rather
than a decomposition.

**The body-width family is the third term.** Inside a loop, the per-iteration
slope is (context bookkeeping + body). Fixing N at 50 and varying `k` in the
body separates the body's own term out of it, in two families whose unit differs
and is named in the output: `text * k` varies the string's *length* (the parser
concatenates adjacent text, so it stays one TextNode), and `{{ r.name }} * k`
varies the *node* count. The second family's slope per row is the same quantity
section 1 reaches by differencing `b(var) - b(text)`, so the two are put side by
side; agreement is the check that either is the per-node cost.

Run:  .venv/bin/python -I .review/m19-measurements/loop_decomposition.py
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

import bench_engines  # noqa: E402

ROWS = [{"name": f"row{i}"} for i in range(50)]
ROW_TEXT = "<tr><td>x</td></tr>"
ROW_VAR_TEXT = [f"<tr><td>row{i}</td></tr>" for i in range(50)]

LENGTHS = (1, 2, 5, 10, 25, 50)
WIDTHS = (1, 2, 4, 8)

REPEATS = 7


def loop_source(body: str) -> str:
    return "{% for r in rows %}" + body + "{% endfor %}"


def timeit(fn, target=0.05):
    """Best per-call microseconds over `REPEATS` samples, calibrated once."""
    n = 1
    while True:
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        if time.perf_counter() - t0 >= target:
            break
        n *= 2
    best = None
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = (time.perf_counter() - t0) / n
        best = dt if best is None else min(best, dt)
    return best * 1e6, n


def fit(xs, ys):
    """Least-squares `y = a + b·x`; returns (a, b)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return my - b * mx, b


class Probe:
    """One engine, one template family, timed at every point of the family."""

    def __init__(self, name):
        self.name = name
        self.engine = bench_engines.build(name, {"page.html": "x"}, {})
        self.handles = {}

    def handle(self, source):
        if source not in self.handles:
            self.handles[source] = self.engine.from_string(source)
        return self.handles[source]

    def render(self, source, data):
        return self.engine.render(
            self.handle(source), self.engine.context(dict(data))
        )

    def time(self, source, data):
        handle, context = self.handle(source), self.engine.context(dict(data))
        return timeit(lambda: self.engine.render(handle, context))

    def close(self):
        self.engine.close()


print("python:  %s on %s" % (platform.python_version(), platform.platform()))
print("engines: %s" % ", ".join(bench_engines.ENGINES))
print("fit:     cost(N) = a + b*N over N in %s, best of %d per point"
      % (", ".join(str(n) for n in LENGTHS), REPEATS))
print()

probes = {name: Probe(name) for name in bench_engines.ENGINES}

# --------------------------------------------------------------------------
# 0. The like-for-like assertion, before anything is timed.
# --------------------------------------------------------------------------
print("== 0. every rendered string is checked against an independently built one")
bad = 0
for n in LENGTHS:
    for label, body, expected in (
        ("text", ROW_TEXT, ROW_TEXT * n),
        ("var", "<tr><td>{{ r.name }}</td></tr>", "".join(ROW_VAR_TEXT[:n])),
    ):
        source = loop_source(body)
        for name, probe in probes.items():
            got = probe.render(source, {"rows": ROWS[:n]})
            if got != expected:
                bad += 1
                print("   %-7s %-5s N=%-3d expected %r got %r"
                      % (name, label, n, expected[:40], got[:40]))
print("   %d of %d renderings match ROW*n" % (
    len(LENGTHS) * 2 * len(probes) - bad, len(LENGTHS) * 2 * len(probes)))
if bad:
    print("   REFUSING: the rows are not like-for-like; a slope between them"
          " would be arithmetic rather than a decomposition")
    raise SystemExit(1)
print()

# --------------------------------------------------------------------------
# 1. The two loop rows, fitted over six lengths.
# --------------------------------------------------------------------------
print("== 1. cost(N) = a + b*N for the two corpus loop rows")
print()
print("%-9s %-7s %9s %9s %9s" % ("engine", "body", "a (us)", "b (us/row)", "N=50"))
print("-" * 48)
fits = {}
for label, body in (("text", ROW_TEXT), ("var", "<tr><td>{{ r.name }}</td></tr>")):
    source = loop_source(body)
    for name, probe in probes.items():
        ys = []
        for n in LENGTHS:
            us, _ = probe.time(source, {"rows": ROWS[:n]})
            ys.append(us)
        a, b = fit(LENGTHS, ys)
        fits[(name, label)] = (a, b, ys[-1])
        print("%-9s %-7s %9.3f %9.3f %9.3f" % (name, label, a, b, ys[-1]))
    da, db, _ = fits[("django", label)]
    ra, rb, _ = fits[("rust", label)]
    ja, jb, _ = fits[("jinja2", label)]
    print("%-9s %-7s %9.2f %9.2f" % ("ours/stock", label, ra / da, rb / db))
    print("%-9s %-7s %9.2f %9.2f" % ("jinja/stock", label, ja / da, jb / db))
    print()

# --------------------------------------------------------------------------
# 2. The intercept, measured directly rather than fitted.
# --------------------------------------------------------------------------
print("== 2. the empty loop: the intercept at one point, beside the fit's a")
print()
print("%-9s %-7s %9s %9s %9s" % ("engine", "body", "empty(us)", "fit a", "delta"))
print("-" * 48)
for label, body in (("text", ROW_TEXT), ("var", "<tr><td>{{ r.name }}</td></tr>")):
    source = loop_source(body)
    for name, probe in probes.items():
        us, _ = probe.time(source, {"rows": []})
        a = fits[(name, label)][0]
        print("%-9s %-7s %9.3f %9.3f %9.3f" % (name, label, us, a, us - a))
    print()

# --------------------------------------------------------------------------
# 3. What the per-iteration term is made of.
# --------------------------------------------------------------------------
# The first version of this section varied `k` in a body of `k` copies of a row
# and called the slope "per node". It is not: `Parser.extend_nodelist` concatenates
# adjacent text, so `ROW_TEXT * 8` is **one** TextNode eight times as long, and the
# family varied text *length* while claiming to vary node count. The two families
# below are the ones whose unit is the node — text (length) and variables — and the
# variable slope is checked against section 1's own `b(var) - b(text)`, which is the
# same quantity reached a different way.
print("== 3. what the per-iteration term is made of")
print()
print("   adjacent text is merged by the parser, so `k` in a text-only body")
print("   changes the string's length and not the node count; the variable")
print("   family is the one whose slope is a per-node cost.")
print()
# `{{ r.name }}` renders `rowN` — a str with nothing to escape — so the expected
# string is built from the data, not from the template text. The first draft of
# this section used `{{ r }}`, which renders a dict as its escaped repr and is
# not `unit * k`; the assertion below is what caught it.
FAMILIES = (
    ("text*k", ROW_TEXT, lambda r: ROW_TEXT, "char"),
    ("var*k", "{{ r.name }}", lambda r: r["name"], "node"),
)
print("%-9s %-8s %9s %9s %10s" % ("engine", "family", "k=1", "k=8", "slope"))
print("-" * 48)
for name, probe in probes.items():
    slopes = {}
    for label, unit, one, noun in FAMILIES:
        ys = []
        for k in WIDTHS:
            source = loop_source(unit * k)
            data = {"rows": ROWS}
            expected = "".join(one(r) * k for r in ROWS)
            got = probe.render(source, data)
            assert got == expected, (name, label, k, expected[:40], got[:40])
            us, _ = probe.time(source, data)
            ys.append(us)
        _, b = fit(WIDTHS, ys)
        slopes[label] = (b, noun)
        print("%-9s %-8s %9.3f %9.3f %10.4f" % (name, label, ys[0], ys[-1], b))
    a_t, b_t, _ = fits[(name, "text")]
    a_v, b_v, _ = fits[(name, "var")]
    # The slope is over all 50 rows at once, so dividing by the row count gives
    # the per-row unit: a character of text, or a variable node.
    print("%-9s per row: %.5f us/%s, %.5f us/%s   (section 1 b(var)-b(text)"
          " = %.4f)" % (
              "", slopes["text*k"][0] / len(ROWS), slopes["text*k"][1],
              slopes["var*k"][0] / len(ROWS), slopes["var*k"][1],
              b_v - b_t))
    print()

for probe in probes.values():
    probe.close()
