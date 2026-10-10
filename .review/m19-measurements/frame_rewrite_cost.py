#!/usr/bin/env python
"""SUPERSEDED -- do not cite a figure from this file. Kept for its result.

**What it returned.** Two runs of it on the *same* artifact
(`sha256:b0c073c33ecdfe6c`) gave the gate's cost as **+0.042 us/read** and
**+0.295 us/read** -- a factor of seven. The template halves say where it went:
the frame route (`B{k}`, no include) held to 0.2% between the two runs while the
stated-loop half (`A{k}`, one include per iteration, ~140 us) moved 6%. So the
difference of two ~140 us numbers, one of which moves, is not an instrument.

**What replaced it.** `frame_rewrite_ab.py`, which times this same family under
*both* builds and alternates them, so the fix's cost is a cross-build delta of one
template rather than a difference of two baselines inside one build, and `B`, `C`
and `D` are controls in the same runs. That instrument reports **+0.2076 us/read**
(the slope of `A{k}` over k; `A1/50` alone reads +0.2418) with its controls moving
**at most 0.454 us** -- 27x below the smallest subject delta. The two readings here
bracket it; they are recorded rather than deleted because an abandoned instrument
that quietly vanishes is how the same mistake gets made twice.

**What it was trying to do**, kept because the design is still worth reading:

`may_write_context` makes `seal` skip its write pass for a body that can write the
context, so the loop-variable reads in that body stay *context* reads where every
other sealed body's become *frame* reads. That is the fix's whole cost, and it is
a **difference of two read routes**, so measuring it as a cross-build ratio of two
whole corpus rows puts a ±3–4% build-layout term on top of a term that is a
hundred reads of a few nanoseconds each — which is what the first attempt did, and
the floor came back wider than every subject delta.

This measures the routes against each other **inside one build**, by differencing
a four-template family. The corpus row is not the unit; the read is:

    D  {% for r in rows %}{% endfor %}                     a sealed loop, nothing in the body
    B  {% for r in rows %}{{ r.name }}{% endfor %}          the read, no delegate -> frame
    C  {% for r in rows %}{% include 'inc.html' %}{% endfor %}  the delegate, no read
    A  {% for r in rows %}{{ r.name }}{% include 'inc.html' %}{% endfor %}
                                                           the read *with* a delegate -> context

`C` and `D` are the two intercepts. `B - D` is one **frame** read of `r.name` per
iteration and `A - C` is one **context** read of the same name at the same N, so
the fix's per-read cost is `((A - C) - (B - D)) / N_ROWS` — and the include that `A`
and `C` share cancels by construction, which is the only reason the difference is
about the read and not about Python running one more node.

**Four assumptions are checked rather than assumed**, because each one is a way
this differencing could be arithmetic instead of a decomposition:

  * every template renders the bytes an independent construction predicts, on both
    engines, before it is timed (`loop_decomposition.py`'s like-for-like rule — two
    rows of different work have no slope between them);
  * `A` really routes its read through the context rather than the frame, read off
    the same predicate the seam uses (below), not inferred from `A > B`;
  * `B` and `D` really run `rewrite` — the same predicate, `false`;
  * the family is linear in the number of reads, from a three-point fit, because a
    difference of two singletons is not a slope.

Run:  .venv/bin/python -I .review/m19-measurements/frame_rewrite_cost.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

REPEATS = 7

N_ROWS = 50
ROWS = [{"name": f"row{i}"} for i in range(N_ROWS)]

INC = {"inc.html": "x"}

D_SRC = "{% for r in rows %}{% endfor %}"
B_SRC = "{% for r in rows %}{{ r.name }}{% endfor %}"
C_SRC = "{% for r in rows %}{% include 'inc.html' %}{% endfor %}"
A_SRC = "{% for r in rows %}{{ r.name }}{% include 'inc.html' %}{% endfor %}"

# The width family, to check the read is linear in the read count before any
# slope between two points is read as one read's cost.
B_K = {k: "{% for r in rows %}" + "{{ r.name }}" * k + "{% endfor %}" for k in (1, 2, 4)}
A_K = {
    k: "{% for r in rows %}" + "{{ r.name }}" * k + "{% include 'inc.html' %}{% endfor %}"
    for k in (1, 2, 4)
}


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
    return best * 1e6


def fit(xs, ys):
    """Least-squares `y = a + b·x`; returns (a, b)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return my - b * mx, b


def census_of(engine_name, source):
    """Render `source` and return the walk's own census partition for it.

    The predicate that decides the read's route is over the *plan*, and the plan
    is built at compile time; the census is the one instrument that reports what
    the walk did with it. `ForNode` under `handled:` with the include under
    `delegated:` is the shape that says the loop sealed *and* a delegate is inside
    it — which is `may_write_context == true`, the condition under test.
    """
    from django_rust_templates import _native

    eng = bench_engines.build(engine_name, dict(INC), {})
    tpl = eng.from_string(source)
    _native.census_start()
    out = eng.render(tpl, eng.context({"rows": ROWS}))
    handled, delegated, _, _, _ = _native.census_take()
    eng.close()
    return out, dict(handled), dict(delegated)


def main() -> int:
    families = {"A": A_SRC, "B": B_SRC, "C": C_SRC, "D": D_SRC}
    families.update({f"B{k}": s for k, s in B_K.items()})
    families.update({f"A{k}": s for k, s in A_K.items()})

    # --- the like-for-like assertion, before anything is timed -----------------
    print("the corpus, and the bytes it must render")
    print(f"  {N_ROWS} rows; each row's name appears once per read in the body")
    for label, src in families.items():
        expected = predict(label)
        got = {}
        for name in ("django", "rust"):
            eng = bench_engines.build(name, dict(INC), {})
            try:
                got[name] = eng.render(eng.from_string(src), eng.context({"rows": ROWS}))
            finally:
                eng.close()
        same = got["django"] == got["rust"] == expected
        if same:
            print(f"  {label:<4} {len(expected):>6} bytes  ok")
        else:
            print(
                f"  {label:<4} {len(expected):>6} bytes  MISMATCH\n"
                f"       stock     {got['django']!r}\n"
                f"       ours      {got['rust']!r}\n"
                f"       predicted {expected!r}"
            )
            return 1
    print()

    # --- the predicate, read off the census rather than inferred ---------------
    print("the predicate, read off the walk's own census (rust engine)")
    for label in ("A", "B", "C", "D"):
        _, handled, delegated = census_of("rust", families[label])
        sealed = handled.get("ForNode", 0)
        inside = "IncludeNode" in delegated
        print(
            f"  {label:<4} ForNode handled={sealed}  delegate inside the loop body="
            f"{'yes' if inside else 'no '}  -> rewrite "
            f"{'SKIPPED (context reads)' if inside else 'RUNS (frame reads)'}"
        )
    print()

    # --- the timing -----------------------------------------------------------
    print(f"{'template':34}{'stock us':>10}{'ours us':>10}{'ratio':>8}")
    timed = {}
    for engine_name in ("django", "rust"):
        eng = bench_engines.build(engine_name, dict(INC), {})
        ctx = eng.context({"rows": ROWS})
        handles = {label: eng.from_string(src) for label, src in families.items()}
        timed[engine_name] = {
            label: timeit(lambda h=h: eng.render(h, ctx)) for label, h in handles.items()
        }
        eng.close()
    for label in families:
        s, o = timed["django"][label], timed["rust"][label]
        print(f"  {label:<32}{s:>10.3f}{o:>10.3f}{o / s:>8.3f}")
    print()

    # --- the decomposition ----------------------------------------------------
    #
    # **The unit is one read, and `B - D` is not one read.**  The body of `B` is a
    # single `{{ r.name }}` *inside a loop over `N_ROWS`*, so `B - D` is the cost
    # of `N_ROWS` reads — one per iteration — and dividing it out is what turns
    # the family into a per-read figure.  The first draft of this probe read
    # `B - D` as one read and priced the corpus at 50x the truth; the printed
    # `N_ROWS` beside each line is there so that mistake cannot recur silently.
    per_read_route = {}
    for engine_name in ("django", "rust"):
        t = timed[engine_name]
        frame = (t["B"] - t["D"]) / N_ROWS
        context = (t["A"] - t["C"]) / N_ROWS
        per_read_route[engine_name] = context - frame
        print(f"{engine_name}:")
        print(f"  one loop-variable read, frame route   ((B - D)/{N_ROWS}) : {frame:>7.3f} us")
        print(f"  one loop-variable read, context route ((A - C)/{N_ROWS}) : {context:>7.3f} us")
        print(f"  the gate's cost, per read                                 : {context - frame:>+7.3f} us")
        print(
            f"  the include both A and C pay (cancels)                    : "
            f"{t['C'] - t['D']:>7.3f} us  (C - D, over {N_ROWS} iterations)"
        )
    print()

    # --- linearity, so a difference of two points is a slope --------------------
    #
    # The second estimator, and it is the better-founded one: the loop's own
    # machinery is per-iteration and therefore constant in `k`, so it lands in the
    # intercept, and the slope of each route is the read alone.  `A{k} - B{k}` is
    # the third: the frame route cancels *inside* it and the include becomes the
    # intercept, so its slope is the gate's cost with no differencing of two
    # intercepts at all.  All three are printed because they are three, and a
    # disagreement between them is the only signal that one of them is arithmetic.
    print(f"three estimators of the gate's per-read cost ({N_ROWS} rows per template)")
    slopes = {}
    for engine_name in ("django", "rust"):
        t = timed[engine_name]
        ks = sorted(B_K)
        _, b_slope = fit(ks, [t[f"B{k}"] - t["D"] for k in ks])
        _, a_slope = fit(ks, [t[f"A{k}"] - t["C"] for k in ks])
        _, ab_slope = fit(ks, [t[f"A{k}"] - t[f"B{k}"] for k in ks])
        inter_ab, _ = fit(ks, [t[f"A{k}"] - t[f"B{k}"] for k in ks])
        slopes[engine_name] = (a_slope - b_slope) / N_ROWS
        print(f"  {engine_name}:")
        print(f"    1. (A - C)/{N_ROWS} - (B - D)/{N_ROWS}          = {per_read_route[engine_name]:>+7.3f} us/read")
        print(f"    2. context slope - frame slope, fit over k  = {(a_slope - b_slope) / N_ROWS:>+7.3f} us/read")
        print(f"    3. slope of (A_k - B_k), fit over k         = {ab_slope / N_ROWS:>+7.3f} us/read")
        print(
            f"    (3)'s intercept {inter_ab:>7.3f} us should equal the include (C - D) "
            f"{t['C'] - t['D']:.3f} us -- it is the one cross-check that"
        )
        print("         the slope is the read and not a mis-set baseline")
    print()

    # --- what that is on the corpus rows the fix can act on --------------------
    #
    # Read counts come from the corpus source (`scripts/bench_corpus.py`), one
    # entry per walk-owned read of the loop variable inside the sealing loop.
    # `realistic`'s body is `{{ row.name }}` and `{{ row.value|floatformat:2 }}`
    # over 50 rows: 100 reads, and the body is short enough to count by eye.  The
    # row beside it is the corpus's other subject and its count is **not** counted
    # by eye — the outer body holds an `{% if %}` condition and a nested loop's
    # sequence, and whether the walk owns those reads is a question about the walk
    # rather than about the source, so it is bracketed instead.
    print("the corpus rows the gate can act on, priced from the per-read cost")
    per_read = per_read_route["rust"]
    for note, iterations, lo, hi in (
        ("{{ row.name }} + {{ row.value|floatformat:2 }}", 50, 2, 2),
        ("{{ s.title|title }}, {% if s.total %}, {{ s.total }}, s.lines", 12, 2, 4),
    ):
        for reads in range(lo, hi + 1):
            n = iterations * reads
            mark = " " if lo == hi else ("  (if the walk owns only the emits)" if reads == lo else "  (if it also owns the `if` and the sequence)")
            print(
                f"  {iterations:>3} iterations x {reads} read(s) = {n:>4} reads  "
                f"{n * per_read:>+8.2f} us{mark}   [{note}]"
            )
    print()
    print(
        "  a negative per-read cost would be a result, not a bug -- the context route can\n"
        "  be *cheaper* than the frame route -- and this probe exists so that is read off\n"
        "  the number rather than argued.  It is positive here, so the gate costs."
    )
    return 0


def predict(label: str) -> str:
    """The bytes `label`'s template must render, built independently of the engine.

    `A{k}`/`B{k}` read `r.name` k times per row; `C` renders the include (`x`) and
    reads nothing; `D` renders nothing. Written out rather than derived from the
    engine, which is the point — a prediction taken from the subject is a
    tautology.
    """
    if label == "D":
        return ""
    if label == "C":
        return "x" * N_ROWS
    k = 1
    if len(label) > 1 and label[1:].isdigit():
        k = int(label[1:])
    reads = label.startswith("A") or label.startswith("B")
    return "".join(
        (f"row{i}" * k if reads else "") + ("x" if label.startswith("A") else "")
        for i in range(N_ROWS)
    )


if __name__ == "__main__":
    sys.exit(main())
