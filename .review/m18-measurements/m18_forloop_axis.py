"""M18.0 slice 1, follow-up probe: what does stock pay per `{% for %}` iteration?

Requested by review, which predicted: "the floor's ratio should degrade monotonically
with iteration count on a fixed template -- one probe, one axis."

The prediction needs the floor to render a `{% for %}`, and it cannot: `Op::Delegate`
names the node and declines the whole render, which is the reachability design working
as intended. So the end-to-end axis is not available without implementing a tag, which
is the next slice's work rather than a probe.

**What is available without touching Rust is the half that decides the prediction.**
Route (a) crosses once per delegated node; a Rust-owned loop crosses per iteration only
for whatever it has to ask Python for. Either way, the number the design is measured
against is *stock's own per-iteration control cost* -- if stock's loop control is cheap,
no Rust design can win it back, and if it is expensive, the tag-heavy corpus is where
Rust wins most rather than least.

The instrument is a difference of two templates with the **same node count and the same
per-item work**, one wrapped in a loop and one not. Everything that is not loop control
cancels:

    flat(N)  <li>{{ v0 }}</li><li>{{ v1 }}</li>...      N variables, one dict read each
    loop(N)  {% for x in seq %}<li>{{ x }}</li>...     N iterations, one dict read each

so `(loop - flat) / N` is stock's per-iteration control cost: `context.push()`,
`context[x] = item`, the `forloop` update, and `context.pop()`. Nothing else differs.

This is a stock-only probe. It runs under the pinned `.django/` at 6.0.9 and needs no
native module, which is the point: it is a fact about the thing being replaced, not
about our port, so it is unaffected by the port's own current state.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import m18_render_slice1 as gate  # noqa: E402

from django.template import Context, Engine  # noqa: E402  (imported for parity)

COUNTS = [1, 5, 20, 50, 200]


def flat(n: int) -> str:
    return "".join(f"<li>{{{{ v{i} }}}}</li>" for i in range(n))


def loop(n: int) -> str:
    """One body copy, `n` iterations -- the reviewer's axis.

    The first draft put `n` copies of the body inside one `{% for %}` over an
    `n`-long sequence, so it rendered n*n items against `flat`'s n and the control
    read `same_bytes=False` at every n but 1. A probe whose two arms do different
    amounts of work is not measuring their difference.
    """
    return "{% for x in seq %}<li>{{ x }}</li>{% endfor %}"


def main() -> int:
    print("m18.0 slice 1 -- stock's per-iteration cost of `{% for %}`")
    print(f"tree:    {gate.template_base.__file__}")
    print(f"django:  {gate.django.get_version()}")
    print(f"python:  {sys.version.split()[0]}")
    print()

    engine = Engine()
    print("control -- the two templates must render the same bytes, per count")
    rows = {}
    ok = True
    for n in COUNTS:
        f_src, l_src = flat(n), loop(n)
        f_t, l_t = engine.from_string(f_src), engine.from_string(l_src)
        f_ctx = Context({f"v{i}": f"item{i}" for i in range(n)})
        l_ctx = Context({"seq": [f"item{i}" for i in range(n)]})
        f_ctx.template, l_ctx.template = f_t, l_t
        f_out, l_out = f_t.render(f_ctx), l_t.render(l_ctx)
        same = f_out == l_out
        ok = ok and same
        f_nodes = sum(1 for _ in f_t.nodelist)
        l_nodes = sum(1 for _ in l_t.nodelist)
        rows[n] = (f_t, f_ctx, l_t, l_ctx, f_nodes, l_nodes)
        print(f"  n={n:<4} same_bytes={same} len={len(f_out):<6} "
              f"flat nodes={f_nodes} loop nodes={l_nodes}")
    print(f"  all counts same_bytes={ok}")
    if not ok:
        print("  REFUSING: flat and loop do not render the same bytes, so the "
              "difference below is not loop control.")
        return 1
    print()
    print("  Note the node columns: `loop` has a constant node count regardless of n, "
          "while")
    print("  its *work* scales with n. That gap is the whole reason the gate's node "
          "census")
    print("  cannot see this cost -- a for-loop renders n bodies from one node.")
    print()

    print("the axis -- render cost against iteration count, interleaved per count")
    print("  n      flat us   loop us   loop-flat   per-iteration  loop/flat")
    f0 = l0 = 0.0
    for n in COUNTS:
        f_t, f_ctx, l_t, l_ctx, _fn, _ln = rows[n]
        iters = max(20, 4000 // max(n, 1))
        fmed, lmed, ratio, wins, _ = gate.paired(
            lambda tm=f_t, c=f_ctx: tm.render(c),
            lambda tm=l_t, c=l_ctx: tm.render(c),
            iters,
            rounds=30,
        )
        if n == COUNTS[0]:
            f0, l0 = fmed, lmed
        print(f"  {n:<5} {fmed:9.3f} {lmed:9.3f} {lmed - fmed:11.3f} "
              f"{(lmed - fmed) / n:13.4f} {ratio:10.4f}  {wins:2d}/30")
    print()
    print("  `flat us`/`loop us` are paired medians, so `loop-flat` is a difference of")
    print("  two medians and `per-iteration` divides it by n. Both templates render the")
    print("  same bytes at every n (control above), so the difference is the loop's own")
    print("  control: push, bind the loop variable, update `forloop`, render, pop.")
    print()
    print("  A per-iteration figure that does NOT fall as n grows is a fixed per-render")
    print("  cost being divided by n rather than a per-iteration cost; a figure that is")
    print("  flat across counts is the per-iteration cost the design has to beat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
