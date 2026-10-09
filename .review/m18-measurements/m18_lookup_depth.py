"""M18.0 slice 1, follow-up probe: is the 6% a property of the design or of the corpus?

Requested by review after slice 1 passed. The reviewer's form was: "a workload with
many variables whose lookups are cheap should flip the sign -- all simple top-level
names, no attribute chains, no filters." **That workload is already the corpus**, and
this probe exists because the premise is measurable and false:

    m18_render_slice1.py:109  def _units(n):  f"...{i}</p>{{{{ a }}}}\\n"
    m18_render_slice1.py:147  DATA = {"a": "hello world"}

`{{ a }}` is one top-level name, no chain, no filter. So the cheap-lookup end cannot
be reached by *simplifying the variable*; the only cheaper form is removing the
Context read entirely, which is the `{{ "hello world" }}` literal row the gate
already prints. The probe therefore runs the reviewer's experiment in the direction
that is actually available: **make the lookup richer, and see which way the margin
moves.**

Why that is the interesting direction. The reviewer's sensitivity argument is that
the margin is the residue of two terms each ~11x larger -- stock's Python lookup
against the Rust->Python call tax -- so a 10% move in the tax flips the sign. If the
margin instead *grows* with lookup depth, the two terms are not the free parameters
they look like: the depth raises stock's term only, and the corpus the gate measured
on is then the pessimistic end rather than the optimistic one.

That matters because §3's own corpus uses chains -- `scripts/bench.py:90-114`,
`section{i}.field{j}` -- so the gate's `{{ a }}` is unrepresentative of the templates
the milestone is justified by, and unrepresentative in the direction that understates
the result.

The instrument is the gate's own: `measure` and `paired` are imported from
`m18_render_slice1` rather than re-implemented, so a ratio here is a ratio as that
script defines it and not a second definition of the same word.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from statistics import median  # noqa: F401  (imported for parity with the gate)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import m18_render_slice1 as gate  # noqa: E402

from django.template import Context, Engine  # noqa: E402

from django_rust_templates import _native  # noqa: E402

"""One context per row, each ending at the *same* leaf string.

The first version of this probe shared a single nested `DATA = {"a": {"b": ...}}`,
which is a **defect** the control column caught rather than the timings: with `a`
bound to a dict, `{{ a }}` renders `str(dict)` and `{{ a.b }}` renders a shorter
repr, so the five rows escaped different bytes and the table measured the length of
a `dict` repr rather than the depth of a lookup. Its `sha256` and `len` columns
differed row by row, which is exactly what those columns are printed to show.

So each row carries its own context, all five resolving to the string
`"hello world"`. Every row must then render *identical bytes* -- 11902 of them, as
the gate's `large` does -- and the control asserts that across rows rather than
only against the engine. With that pinned, the only thing varying is how many
`__getitem__` steps the Context read takes, and how many the Rust side can do
before it has to call back for an object that is not a dict.
"""

# The same text run and the same variable count as the gate's `large` (154 variables,
# 309 nodes), so a row here lines up against the gate's rows rather than beside them.
TEXT = "<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit {i}</p>"

LEAF = "hello world"

# (row name, expression, the context that row is rendered against). Every context
# resolves the expression to `LEAF`, so all five rows render the same 11902 bytes.
ROWS = [
    ("literal", '{{ "hello world" }}', {}, "compile-time constant, no Context read"),
    ("top", "{{ a }}", {"a": LEAF}, "one dict read -- WHAT THE GATE MEASURED"),
    ("depth 1", "{{ a.b }}", {"a": {"b": LEAF}}, "two reads"),
    ("depth 2", "{{ a.b.c }}", {"a": {"b": {"c": LEAF}}}, "three reads"),
    ("depth 3", "{{ a.b.c.d }}", {"a": {"b": {"c": {"d": LEAF}}}}, "four reads"),
]

N = 154
ITERS = 25
ROUNDS = 30


def build(expr: str) -> str:
    return "".join(f"{TEXT.format(i=i)}{expr}\n" for i in range(N))


def main() -> int:
    print("m18.0 slice 1 -- does the render margin grow with lookup depth?")
    print(f"tree:    {gate.template_base.__file__}")
    print(f"django:  {gate.django.get_version()}")
    print(f"python:  {sys.version.split()[0]}")
    print(f"rows:    {N} variables, {len(build(ROWS[1][1]))} chars each, "
          f"same escaper crossing in every row")
    print()

    engine = Engine()
    census = {}
    for name, expr, data, _note in ROWS:
        src = build(expr)
        tmpl = engine.from_string(src)
        tree = _native.parse_desc(src)
        ctx = Context(data)
        ctx.template = tmpl
        result = _native.render_floor(tree, ctx)
        census[name] = (tmpl, tree, ctx, result)

    # Positive control, the same condition the gate uses: every arm's bytes against
    # the engine's own render. A row whose floor silently declined a node would
    # otherwise read as a very fast floor.
    print("control -- floor bytes against the engine's own render (positive control)")
    ok = True
    shas = set()
    for name, _expr, _data, _note in ROWS:
        tmpl, tree, ctx, result = census[name]
        expected = tmpl.render(ctx)
        match = result.output == expected
        ok = ok and match
        sha = hashlib.sha256((expected or "").encode()).hexdigest()
        shas.add(sha)
        print(f"  {name:<9} match={match} sha256={sha[:16]} len={len(expected or '')} "
              f"handled={result.handled} delegated={result.delegated}")
    # The second control, and the one the first draft of this probe failed: the rows
    # must be the *same bytes*, or the escaper crosses on different work in each row
    # and the table is not about lookup depth at all. One sha for five rows, or the
    # timings below are not comparable and the run should not be read.
    print(f"  all rows match={ok}  identical across rows={len(shas) == 1} "
          f"({len(shas)} distinct sha256)")
    if not (ok and len(shas) == 1):
        print("  REFUSING: the rows do not render the same bytes, so no timing below "
              "is a comparison. Fix the corpus, not the reading.")
        return 1
    print()

    print("the table -- floor vs stock render, interleaved, "
          f"{ROUNDS} rounds, {ITERS} iters")
    print("  row        floor us  render us   ratio   wins  floor/var  render/var  "
          "render-floor")
    per_var = {}
    for name, _expr, _data, _note in ROWS:
        tmpl, tree, ctx, result = census[name]
        fu = gate.measure(lambda tr=tree, c=ctx: _native.render_floor(tr, c), ITERS)
        ru = gate.measure(lambda tm=tmpl, c=ctx: tm.render(c), ITERS)
        fmed, rmed, ratio, wins, _ = gate.paired(
            lambda tr=tree, c=ctx: _native.render_floor(tr, c),
            lambda tm=tmpl, c=ctx: tm.render(c),
            ITERS,
            rounds=ROUNDS,
        )
        per_var[name] = (fmed / N, rmed / N)
        print(f"  {name:<9} {fu:9.3f} {ru:10.3f}  {ratio:7.4f}  {wins:2d}/{ROUNDS} "
              f"{fmed / N:10.3f} {rmed / N:11.3f} {rmed - fmed:13.3f}")

    print()
    print("  `floor us` / `render us` are best-of-7 per call; `ratio` and `wins` are the")
    print("  median of the per-round ratios, as in the gate. The per-variable columns are")
    print("  the *paired medians* over the row's variable count, so they reconcile with")
    print("  `ratio` and not with the best-of-7 columns beside them.")
    print()

    # The two readings the probe exists to separate. Under the reviewer's model the
    # margin shrinks as the lookup gets richer (their cheap end is unreachable, so the
    # only available direction is richer). Under the design model, the floor's Context
    # read stays cheap while stock's grows, so the margin widens.
    print("reading -- the lookup term is `render/var - floor/var`, split against the")
    print("           literal row, which crosses the escaper with no Context read at all")
    flit, rlit = per_var["literal"]
    for name, _expr, _data, _note in ROWS:
        f, r = per_var[name]
        print(f"  {name:<9} stock lookup term {r - rlit:7.3f} us/var   "
              f"floor lookup term {f - flit:7.3f} us/var")
    print()
    print("  Under the design model the floor's term stays flat and stock's term grows with")
    print("  depth, so `ratio` falls and the margin widens. Under the reviewer's model the")
    print("  margin is set by the FFI tax, which no row here moves, so the ratio is flat.")
    print("  Which of the two the table shows is the whole question; no prose is emitted")
    print("  below this line on purpose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
