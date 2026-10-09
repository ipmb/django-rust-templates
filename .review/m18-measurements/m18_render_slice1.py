#!/usr/bin/env python
"""M18.0 slice 1: can a Rust walk over the descriptor tree render, with no
Python `Node` objects at all?

The question §3.1 leaves open. Compiling a template is 91% Python
*re-materialisation* of a tree Rust already parsed 7x faster (`m18_parse_split.py`);
slice 1 asks whether a render can walk Rust's descriptor tree directly instead.
Two node classes only — `TextNode` and a `VariableNode` with **no filters** — which
is the smallest thing that proves the plumbing (Rust walk + context crossing +
output buffer) end to end.

Three arms, and the order they are reported in is the point.

**The gate is `floor`, and it is printed before the dispatched number appears.**
The gate brief's first form — "if the Rust walk does not beat stock, say so and
distinguish the route" — was unfalsifiable: its subject was the *route*, so
"route (a)'s dispatch overhead is not route (b)'s" is a rebuttal always in hand,
because route (b) does not exist to be measured. The repair gates on the
**dispatch-free floor** instead:

    floor        the render walk with dispatch *removed entirely* — a flat plan
                 (a `Vec<Op>`) built once at parse time, so the render loop has
                 no per-node `class_name` comparison and no name-keyed `attrs`
                 lookup. This is the invariant render work: whatever the dispatch
                 mechanism, every renderer must resolve a variable, escape it, and
                 append it. (b) cannot be faster than a floor that already excludes
                 the dispatch (b) removes.

    > If the dispatch-free floor is not faster than stock, neither route can
    > win, and the design is dead.

    rust         the same walk dispatching on `class_name` strings and finding
                 attributes by name — route (a) as it would actually be written.
                 The gap `rust − floor` is the dispatch tax, measured rather than
                 asserted.

    stock        Django's own `from_string(...).render(...)`, and a pre-compiled
                 `.render(...)`, side by side.

`floor` and `rust` are the *same helpers* for everything that is not dispatch —
variable resolution through the ported resolver, escaping through
`django.template.base.render_value_in_context` — so the subtraction isolates the
dispatch and nothing else.

**Why there is a paired table under the gate.** Best-of-N cannot resolve this
effect. Two runs of part 1 in separate processes put `floor/render` at 0.988
(gate open) and 1.031 (gate shut) for the same template, because the true effect
is a few percent and process-to-process drift is the same size. Part 1 therefore
carries a second measurement: the two arms alternate *within* each round, 40
rounds, and the reported statistic is the median of the per-round ratios with the
count of rounds the floor won. A ratio below 1.0 with a win count near 40/40 is
the floor; a ratio straddling 1.0 with a win count near 20/40 is a tie.

**Which process carries the gate.** The two arms that matter (`floor`, and stock's
render) are both available only in one process: under the shim `django.template`
*is* our transcription, so `t.render(ctx)` there is *our* materialised render, not
stock's. `floor`/`rust`/`parse` are engine-agnostic — they cross to Python only for
the resolved value and the escaper — so they are measured on both sides, but the
gate's `floor vs stock-render` comparison is only valid in the **stock** transcript
(`transcript-render-slice1-stock.txt`); the shim transcript confirms the same
floor/rust numbers hold under our transcription. The `side:` line below says which
`engine-render` column this process is reading.

**Two instruments in the literals block, labelled where they print.** The block
that prices the look-up ("what the walk cannot reach") prints both a best-of-7
column and an interleaved-median column for the same row, because there the
effect is a couple of microseconds inside a 170 us render: `measure()` cannot
resolve it, and in the shim its rows even put the sign the wrong way. Every delta
quoted in that block's prose is a difference of the `med` columns, and the prose
says so — a number a reader cannot reconcile with the row printed above it costs
more trust than the two lines it takes to label it.

Run it under both sides exactly as `scripts/bench.py` is run:

    source scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \\
        .venv/bin/python .review/m18-measurements/m18_render_slice1.py
    DJANGO_RUST_TEMPLATES=0 PYTHONPATH="$PWD/.django" \\
        .venv/bin/python -I .review/m18-measurements/m18_render_slice1.py

and diff the two transcripts by hand. The `tree:` line discriminates them: under
the shim and under stock every node prints as `django.template.base`, so
`__module__` says nothing — only `__file__` (or `_native.engine_version()`) does.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from statistics import median

# Same bootstrap as `scripts/bench.py` and `m18_parse_split.py`, for the same
# reason: one place in this tree configures settings.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.template import base as template_base  # noqa: E402

from django_rust_templates import _native  # noqa: E402


def _units(n: int) -> str:
    """`n` repeats of a text run, one `{{ a }}`, and a newline — text/var mix."""
    return "".join(
        f"<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit {i}</p>{{{{ a }}}}\n"
        for i in range(n)
    )


def _text_only(n: int) -> str:
    """`n` text runs and nothing else.

    The whole thing is **one `TextNode`** and the `nodes` column says so, because
    slice 1 has no tag that can interrupt a run of text without also being a node it
    must decline: the most text nodes a slice-1 template can have is
    `text nodes = variable nodes + 1` (`mixed` and `large` are that case). A
    text-only template is therefore genuinely one node holding all the characters,
    which is the shape a `{{ }}`-free page really has.
    """
    return "".join(
        f"<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit {i}</p>\n"
        for i in range(n)
    )


# Slice-1 workloads: text and an unfiltered variable, and nothing else. `large` is
# generated to sit near the 9294-char / 462-node `large` row of §3, so the reader can
# line the two up; it is *not* the same template (that one is `{% if %}`/`{% for %}`
# heavy and slice 1 must decline it). `textonly` is the same size with the variables
# taken out — one `TextNode`, see `_text_only` — and it is the row that says how much
# of the floor's percentage win is the walk and how much is stock's fixed per-render
# machinery, which a 1.9 us render cannot amortise.
WORKLOADS = {
    "tiny": "{{ a }}",
    "textonly": _text_only(154),
    "mixed": _units(120),
    "large": _units(154),
}

DATA = {"a": "hello world"}

# A template slice 1 *must decline*, to show the delegated set is observable and
# named rather than a silent fallback (§6 item 3). `{% for %}` parses without a
# host, unlike `{% if %}` whose every operand needs `compile_filter` — measured
# while building this probe.
DECLINE_SOURCE = "<p>{{ a }}</p>{% for x in y %}{{ x }}{% endfor %}"

# Per-workload iteration counts, chosen so one timing pass is milliseconds: long
# enough that `perf_counter`'s resolution is irrelevant, short enough that 40
# interleaved rounds stay inside one thermal state.
ITERS = {"tiny": 3000, "textonly": 250, "mixed": 30, "large": 25}


def _timeit(fn, iters: int) -> float:
    start = time.perf_counter()
    for _ in range(iters):
        fn()
    return (time.perf_counter() - start) / iters * 1e6


def measure(fn, iters: int, repeats: int = 7) -> float:
    """Best-of-N per-call microseconds, the estimator `scripts/bench.py` uses."""
    for _ in range(max(3, iters // 4)):
        fn()
    return min(_timeit(fn, iters) for _ in range(repeats))


def paired(a, b, iters: int, rounds: int = 40):
    """Interleaved paired timing of `a` against `b`.

    The arms alternate order within each round so a machine that warms or throttles
    between rounds moves both alike, and the statistic is the median of the
    per-round *ratios* rather than a ratio of medians — a ratio per round is what
    makes the win count meaningful. Returns `(median_a, median_b, median_ratio,
    wins, rounds)` where `wins` counts the rounds with `a < b`.

    This exists because best-of-N is not enough here: see the module docstring.
    """
    for _ in range(max(3, min(iters, 50))):
        a()
        b()
    ra: list[float] = []
    rb: list[float] = []
    for i in range(rounds):
        if i % 2 == 0:
            ra.append(_timeit(a, iters))
            rb.append(_timeit(b, iters))
        else:
            rb.append(_timeit(b, iters))
            ra.append(_timeit(a, iters))
    ratios = [x / y for x, y in zip(ra, rb)]
    return median(ra), median(rb), median(ratios), sum(1 for r in ratios if r < 1.0), rounds


def main() -> int:
    shimmed = "django_rust_templates" in template_base.__file__
    side = "rust-shim" if shimmed else "stock"
    print(f"tree:    {template_base.__file__}")
    print(f"django:  {django.get_version()}")
    print(f"side:    {side}")
    print(f"python:  {sys.version.split()[0]}")
    print(f"native:  {_native.engine_version()}")
    print()

    engine = Engine()

    # Our own materialise-then-walk path (§3.1's "our current path"). It loads
    # `_impl/parser.py` from a file when the shim is not installed, so it runs on
    # both sides; see `render.py::materialiser`.
    try:
        from django_rust_templates import render as _render
    except Exception as error:  # pragma: no cover - a broken shell is not a datum
        _render = None
        print(f"note: our materialiser is unimportable here: {error!r}")
        print()

    plan = {}
    for name, source in WORKLOADS.items():
        template = engine.from_string(source)
        nodes = sum(1 for _ in template.nodelist)
        tree = _native.parse_desc(source)

        # Our arms render against a context bound to a real template, so that
        # `string_if_invalid` (read through `context.template.engine`) is reachable
        # exactly as `Template.render` would leave it. The bytes are asserted
        # against the engine's own render below, both sides.
        ctx_ours = Context(DATA)
        ctx_ours.template = template
        ctx_engine = Context(DATA)
        ctx_walk = Context(DATA)
        ctx_walk.template = template

        floor = _native.render_floor(tree, ctx_ours)
        rust = _native.render_desc(tree, ctx_ours)

        arms = {
            "floor": lambda t=tree, c=ctx_ours: _native.render_floor(t, c),
            "rust": lambda t=tree, c=ctx_ours: _native.render_desc(t, c),
            "parse": lambda s=source: _native.parse_desc(s),
            # Public API: `Template.render` pushes a render-context state and binds
            # the template on every call.
            "engine-render": lambda t=template, c=ctx_engine: t.render(c),
            # The same walk with the fixed machinery hoisted out — `_render` is
            # `NodeList.render` and nothing else. The gap to `engine-render` is what
            # the floor gets for free just by not being the public API.
            "walk-only": lambda t=template, c=ctx_walk: t._render(c),
            "engine-full": lambda s=source, c=ctx_engine: engine.from_string(s).render(c),
        }
        if _render is not None:
            arms["materialise"] = lambda s=source: _render.parse_template(
                s, engine
            ).render(Context(DATA))

        plan[name] = {
            "chars": len(source),
            "nodes": nodes,
            "arms": arms,
            "outputs": (template.render(Context(DATA)), floor.output, rust.output),
            "walk_output": template._render(ctx_walk),
            "handled": floor.handled,
            "delegated": floor.delegated,
        }

    # --- part 1: the gate, before any dispatched number -----------------------
    print("part 1 -- the gate: floor (dispatch-free) vs this engine's render")
    print(
        f"  {'workload':10s} {'chars':>6s} {'nodes':>6s} {'floor us':>10s} "
        f"{'render us':>10s} {'floor/render':>12s}"
    )
    gate = {}
    for name, row in plan.items():
        iters = ITERS[name]
        floor_us = measure(row["arms"]["floor"], iters)
        render_us = measure(row["arms"]["engine-render"], iters)
        gate[name] = (floor_us, render_us)
        print(
            f"  {name:10s} {row['chars']:6d} {row['nodes']:6d} {floor_us:10.3f} "
            f"{render_us:10.3f} {floor_us / render_us:12.3f}"
        )

    print()
    print(f"  gate (best-of-7; floor vs engine-render, engine here = {side}):")
    all_open = True
    # The design verdict is only assertable where `engine-render` is *stock's*
    # render. Under the shim that column is our own materialised render, and the
    # same ratio there is 0.71 where the gate's is 0.94 — so the per-row tag says
    # which denominator it used rather than reading the row as a verdict.
    for name, (floor_us, render_us) in gate.items():
        ratio = floor_us / render_us
        speed = "faster" if ratio < 1.0 else "slower"
        if shimmed:
            state = f"floor {speed} than {side}'s render; NOT THE GATE"
        elif ratio < 1.0:
            state = f"floor faster than {side}'s render; the design is OPEN"
        else:
            state = f"floor slower than {side}'s render; BOTH ROUTES DEAD"
            all_open = False
        print(
            f"    {name:10s} floor {floor_us:9.3f} us vs render {render_us:9.3f} us"
            f"  -> {ratio:.3f}x, {speed}  [{state}]"
        )
    if shimmed:
        print(
            "    note: this process's engine is our shim, so every `render` above is"
            " *our* materialised render."
        )
        print(
            "          The gate's stock denominator is in"
            " transcript-render-slice1-stock.txt."
        )
    # Names `side`, because under the shim the subject of every row above is our
    # render and not stock's; a bare "faster than stock" would assert a denominator
    # this block has just disclaimed. And under the shim it declines to decide.
    if shimmed:
        print(
            "    verdict: every floor arm is faster than this engine's render"
            " (= rust-shim). Whether\n    that opens the design is decided in"
            " transcript-render-slice1-stock.txt."
        )
    elif all_open:
        print(
            "    verdict: every floor arm is faster than stock's render; the design is"
            " open."
        )
    else:
        print(
            "    verdict: the dispatch-free floor is not faster than stock's render on"
            " every workload, so neither route (a) nor route (b) can win here."
        )
    print()

    # The same comparison, interleaved, because the one above cannot resolve it.
    print("  the gate, interleaved (40 rounds, arms alternate order within each round):")
    print(
        f"    {'workload':10s} {'floor med':>10s} {'render med':>11s} {'ratio med':>10s} "
        f"{'floor wins':>11s}  reading"
    )
    paired_gate = {}
    for name, row in plan.items():
        iters = ITERS[name]
        fa, fb, ratio, wins, rounds = paired(
            # Each arm gets its own pre-bound context: `Template.render` sets and
            # restores `ctx.template` around the call, and reusing one context for
            # both arms would have the floor's arm inherit a binding the other one's
            # `finally` had just cleared.
            row["arms"]["floor"],
            row["arms"]["engine-render"],
            iters,
        )
        paired_gate[name] = (fa, fb, ratio, wins, rounds)
        if ratio < 1.0 and wins >= rounds * 3 // 4:
            reading = "floor"
        elif ratio > 1.0 and wins <= rounds // 4:
            reading = f"{side}'s render"
        else:
            reading = "a tie"
        print(
            f"    {name:10s} {fa:10.3f} {fb:11.3f} {ratio:10.4f} "
            f"{wins:5d}/{rounds:<5d}  {reading}"
        )
    print()

    # How much of `render` the floor could beat without walking anything at all.
    print("  decomposition -- what of the public render is walk, and what is machinery:")
    print(
        f"    {'workload':10s} {'render us':>10s} {'walk-only us':>13s} "
        f"{'machinery us':>13s} {'floor us':>10s} {'floor/walk':>11s}"
    )
    for name, row in plan.items():
        fa, fb, _, _, _ = paired_gate[name]
        walk_us, _, _, _, _ = paired(
            row["arms"]["walk-only"], row["arms"]["engine-render"], ITERS[name], rounds=20
        )
        print(
            f"    {name:10s} {fb:10.3f} {walk_us:13.3f} {fb - walk_us:13.3f} "
            f"{fa:10.3f} {fa / walk_us:11.4f}"
        )
    print(
        "    `walk-only` is the same `NodeList.render` the floor replaces, with the"
        " per-call\n    `push_state`/`bind_template` hoisted out, so `floor/walk` is the"
        " walk against the walk —\n    no public-API machinery either side. It is a few"
        " percent below 1.0 at `mixed`/`large`\n    and never a multiple: the floor"
        " deletes a Python loop over `Node`s and pays FFI crossings\n    of comparable"
        " cost for what it still has to ask Python for. On a variable-heavy template"
        "\n    that is most of the render, and the block below prices it."
    )
    print()

    # The sharpest number in the table, and the one the prose has to carry rather
    # than leaving it under a heading. `{{ a }}` and `{{ "hello world" }}` are the same
    # tree — a `VariableNode`, no filters, one attribute, one `render_value_in_context`
    # call each — differing only in whether the value comes from the `Context`. So the
    # difference between the two rows prices the look-up, and what is left after it is
    # the part of the per-variable cost no dispatch mechanism can move.
    print("  what the walk cannot reach -- the same tree with its look-ups made literals:")
    lookup_src = _units(154)
    literal_src = lookup_src.replace("{{ a }}", '{{ "hello world" }}')
    census = {}
    for key, src in (("lookup", lookup_src), ("literal", literal_src)):
        t2 = engine.from_string(src)
        tree2 = _native.parse_desc(src)
        ctx2 = Context(DATA)
        ctx2.template = t2
        census[key] = (sum(1 for _ in t2.nodelist), t2, tree2, ctx2)
    floor_us_ = {}
    render_us_ = {}
    for key in ("lookup", "literal"):
        _, t2, tree2, ctx2 = census[key]
        floor_us_[key] = measure(lambda tr=tree2, c=ctx2: _native.render_floor(tr, c), 25)
        render_us_[key] = measure(lambda tm=t2, c=ctx2: tm.render(c), 25)

    # The floor's look-up cost is a couple of microseconds inside a 170 us render, which
    # `measure()` (best-of-7) cannot resolve -- in the shim its rows even put the sign the
    # wrong way. So the pairing is run for *both* arms and its medians are printed beside
    # the best-of-7, and every delta below is a `med` difference, not a `us` difference: a
    # number has to reconcile with the row it is printed under, and a reader checks
    # arithmetic before they read a legend.
    lf, ll, _, lwins, _ = paired(
        lambda: _native.render_floor(census["lookup"][2], census["lookup"][3]),
        lambda: _native.render_floor(census["literal"][2], census["literal"][3]),
        25,
        rounds=30,
    )
    rf, rl, _, rwins, _ = paired(
        lambda: census["lookup"][1].render(census["lookup"][3]),
        lambda: census["literal"][1].render(census["literal"][3]),
        25,
        rounds=30,
    )
    # Round both pairings' medians to the three decimals they are printed at, and read
    # the deltas from *those*, so the arithmetic closes against the columns a reader sees
    # in both transcripts. Deriving the deltas from the unrounded values leaves one
    # transcript off by a unit in the last place, which reads as a defect beside the one
    # that closes.
    lf, ll, rf, rl = (round(v, 3) for v in (lf, ll, rf, rl))

    # Under the shim there is no stock arm: `render med` there is *our* materialised
    # render, the same distinction the part-1 note draws. Calling it "stock" in both
    # transcripts names an arm this process does not have.
    renderer = "our materialised render" if shimmed else "stock"
    nv = 154
    floor_dir = "negative" if lf < ll else "positive"
    print(
        f"    {'workload':18s} {'nodes':>6s} {'floor us':>10s} {'render us':>10s}"
        f" {'floor med':>10s} {'render med':>11s}"
    )
    for label, key, med_f, med_r in (
        ("154 look-ups", "lookup", lf, rf),
        ("154 literals", "literal", ll, rl),
    ):
        print(
            f"    {label:18s} {census[key][0]:6d} {floor_us_[key]:10.3f}"
            f" {render_us_[key]:10.3f} {med_f:10.3f} {med_r:11.3f}"
        )
    print(
        "    `us` is best-of-7 per call; `med` is the median of the interleaved 30-round"
        "\n    pairing. They disagree here because this effect is small against"
        " `measure()`'s noise,\n    so every delta in the prose below is read from the"
        " `med` columns, not the `us` ones."
    )
    print(
        f"    The {nv} `Context` look-ups cost {renderer} {rf - rl:.3f} us"
        f" ({(rf - rl) / nv:.3f} us each, from `render med`) and the floor"
        f" {lf - ll:.3f} us\n    ({(lf - ll) / nv:.3f} us each, from `floor med`). Per"
        f" variable that is {renderer}'s {(rf - rl) / nv:.3f}\n    against the floor's"
        f" {(lf - ll) / nv:.3f}. `wins` counts rounds an arm's look-up came in under its"
        f"\n    literal one: {renderer}'s is {rwins}/30, the floor's {lwins}/30 and not"
        f" stable across runs --\n    the floor's delta is {floor_dir} this run. So"
        f" resolution is not where the floor's time goes, and\n    the floor is not paying"
        f" Django's Python `_resolve_lookup`."
    )
    print(
        f"    What is left is identical in both rows: one crossing to"
        f" `render_value_in_context` per\n    variable. Strip the look-ups and the floor"
        f" spends {ll / nv:.3f} us per variable where {renderer} spends\n"
        f"    {rl / nv:.3f} us -- the same Python function, called from Rust instead of"
        f" from Python.\n    So the gate's few percent is the residue of two larger"
        f" opposite effects: the floor wins at the\n    resolution and hands most of it"
        f" back at the escaper. On this evidence the headroom slice\n    1 found is in"
        f" *resolution*, and the escaper is what to bring across next -- not the\n"
        f"    dispatch, which the interleaved table below prices in its `tax% of render`"
        f" column."
    )
    print()

    # --- part 2: the dispatched walk, and everything else ---------------------
    print("part 2 -- the dispatched walk (route (a)) and the rest, for the reader")
    print(
        f"  {'workload':10s} {'rust us':>10s} {'floor us':>10s} {'dispatch us':>12s} "
        f"{'parse us':>10s} {'engine-full':>12s} {'our-mat.':>9s} {'rust/floor':>11s}"
    )
    for name, row in plan.items():
        iters = ITERS[name]
        rust_us = measure(row["arms"]["rust"], iters)
        parse_us = measure(row["arms"]["parse"], iters)
        full_us = measure(row["arms"]["engine-full"], iters)
        floor_us = gate[name][0]
        materialise = (
            measure(row["arms"]["materialise"], iters) if _render else float("nan")
        )
        print(
            f"  {name:10s} {rust_us:10.3f} {floor_us:10.3f} {rust_us - floor_us:12.3f} "
            f"{parse_us:10.3f} {full_us:12.3f} {materialise:9.3f} {rust_us / floor_us:11.3f}"
        )
    print(
        "  `engine-full` is `engine.from_string(s).render(c)` and `our-mat.` is"
        " `render.parse_template(s,\n  engine).render(...)`: two independent totals,"
        " not addends of `parse` or of each other. Under\n  the shim they are the same"
        " pipeline twice (our engine); under stock they are stock's pipeline\n  beside"
        " ours, which is the comparison §3 exists to make."
    )
    print()

    # The subtraction the brief asks for — arms 1 and 2 — interleaved, for the same
    # reason the gate is: it is a few percent and best-of-7 cannot hold its sign.
    # `rust` against the render as well, so route (a)'s standing needs no product of
    # two medians taken in two different blocks.
    print("  the dispatch tax, interleaved (40 rounds each pairing):")
    print(
        f"    {'workload':10s} {'floor med':>10s} {'rust med':>10s} {'render med':>11s} "
        f"{'floor/rust':>11s} {'wins':>7s} {'tax us':>8s} {'tax% of render':>15s} "
        f"{'rust/render':>12s} {'wins':>7s}"
    )
    for name, row in plan.items():
        iters = ITERS[name]
        floor_med, rust_med, ratio, wins, rounds = paired(
            row["arms"]["floor"], row["arms"]["rust"], iters
        )
        _, render_med, vs_render, rwins, _ = paired(
            row["arms"]["rust"], row["arms"]["engine-render"], iters
        )
        print(
            f"    {name:10s} {floor_med:10.3f} {rust_med:10.3f} {render_med:11.3f} "
            f"{ratio:11.4f} {wins:3d}/{rounds:<3d} {rust_med - floor_med:8.3f} "
            f"{(rust_med - floor_med) / render_med * 100:15.2f} "
            f"{vs_render:12.4f} {rwins:3d}/{rounds:<3d}"
        )
    print(
        "    `floor/rust` and `rust/render` are the **median of the 40 per-round"
        " ratios**, not a\n    ratio of the medians printed beside them — `wins` counts"
        " those same ratios. `tax us`,\n    `tax% of render` (that `tax us` over the `render"
        " med` on its left) and both medians are\n    plain arithmetic."
    )
    print(
        "    `wins` is the count of rounds the first arm of the pair was under the"
        " second, so 40/40\n    is an effect this run can resolve and ~20/40 is one it"
        " cannot. `rust/render` in the\n    stock transcript is route (a) against"
        " stock; in the shim transcript it is against our\n    materialised render, and"
        " no column here is the gate."
    )
    print()

    # --- the bytes: every arm against the engine's own render ----------------
    print("control -- bytes of every arm against the engine's own render (positive control)")
    for name, row in plan.items():
        engine_out, floor_out, rust_out = row["outputs"]
        walk_out = row["walk_output"]
        digest = hashlib.sha256(engine_out.encode()).hexdigest()[:16]
        print(
            f"  {name:10s} match={engine_out == floor_out == rust_out == walk_out!r} "
            f"sha256={digest} len={len(engine_out)} engine={engine_out[:40]!r}"
        )
    print()

    # --- reachability (§6 item 3) ---------------------------------------------
    print("reachability -- handled/delegated class sets, and a template that must decline")
    saw_handled = False
    for name, row in plan.items():
        if row["handled"]:
            saw_handled = True
        print(f"  {name:10s} handled={row['handled']!r} delegated={row['delegated']!r}")
    print(
        f"  saw_handled={saw_handled!r}  (a report empty because the walk never ran"
        " would agree with 'everything was handled')"
    )
    decline_tree = _native.parse_desc(DECLINE_SOURCE)
    print(f"  decline source={DECLINE_SOURCE!r}")
    for arm in ("render_floor", "render_desc"):
        decline = getattr(_native, arm)(decline_tree, Context(DATA))
        print(
            f"    {arm:12s} handled={decline.handled!r} delegated={decline.delegated!r} "
            f"output={decline.output!r}"
        )
    print(
        "    output is None on both: a declined node is named, not silently skipped,"
        " so a fallback\n    reads as a set difference and not as a passing render."
    )
    print()

    print("chars: " + "  ".join(f"{k}={row['chars']}" for k, row in plan.items()))
    print("nodes: " + "  ".join(f"{k}={row['nodes']}" for k, row in plan.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
