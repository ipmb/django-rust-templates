#!/usr/bin/env python
"""How many loop iterations each corpus row runs, and how often it reads a loop
variable — counted, not timed.

The deletion's price is a per-iteration cost, and the corpus deltas in
`frame_rewrite_ab4` are `us` per *render*.  Turning one into the other needs the
iteration count, and that count is a property of the template and its context,
so it is countable directly — with no timer, no build, and no `.so`:

  * `ForNode.render` is wrapped, so a call is one loop statement executed and a
    nesting stack of its loop variable names is kept.
  * `BaseContext.__setitem__` is wrapped.  Django's `for` writes its variable
    exactly once per iteration (`context[self.loopvars[0]] = item`) and writes
    its six counters into the `forloop` *dict*, not through the context, so a
    setitem whose key is a live loop variable is an iteration — the same write
    the walk makes, counted at the same place.
  * `BaseContext.__getitem__` is wrapped, so a getitem whose key is a live loop
    variable is a read of one.

**What this measures is stock Django's execution of the row, and that is the
point of it.**  The counts are Django's own — the reference the engine must
match — and they are the same numbers the walk's plan must reproduce.  What they
are *not* is a count of what the walk itself does, so the `for` column, which is
the walk's own census, is what turns them into a statement about the delta: a
row whose loops the walk *delegates* runs them in Python and pays nothing for
this change, and the count is about someone else's execution.

The `predicted` column is a **floor, not an upper bound, and it does not close**,
and **the fourth adversarial round is where I learned it was built from the wrong
pair of numbers.**  It is `iters * per_iteration + reads * per_read`, with both
prices as arguments so neither is buried in this file — but `iters` and `reads`
are *stock's* traffic, and the price this change charges is the traffic the two
*builds* differ by.  Those are the same number only if the ordered pass wrote no
state and read no loop variable, and it did neither of those things on four of the
six rows.  So this file now prints **the walk's own writes and reads as well**, and
must be run **once per artifact** — swapping the installed `.so` and asserting its
sha, the way `frame_rewrite_ab.py` does it — with the two outputs compared.  The
`predicted*` column that comparison produces is `new_writes * per_iteration +
new_reads * per_read`, and it is the one to read; `predicted` is kept beside it
because deleting it would hide the size of the correction.

Where the corrected `m/Δ` lands near 0 the row's loops were already paying the
statement in the comparison build — `i18n` is exactly 0, its writes and reads equal in
both builds, and `complex` is 12 reads, 2.2 µs inside a ±8 µs interval. Where it is above
1 — `normal` 1.11, `large` 1.19, `realistic` 1.26, `filter-arg` 1.52 — the two constants,
which came from micro-templates, do not transfer to whole pages; `realistic` reading
1.26 rather than the 0.55 the stock-traffic floor produced is the correction this file
exists to make. All of that is in the record; none of it is a bug in the count. The
measured column is read from the A/B's own JSON rather than retyped.

The artifact sha is printed first, because a row of this table is a claim about a
**build**: the reads and writes columns move with it (that is the measurement), so
a table with no sha beside it cannot be checked.

Run:  .venv/bin/python -I <this> --work .review/m19-measurements/deletion-price/ab4
      .venv/bin/python -I <this> --work .review/m19-measurements/deletion-price/ab3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

# The tree's one settings bootstrap, and not a local `configure(...)`: it also
# inserts the pinned `.django` checkout on `sys.path`, which is what makes
# `import django` resolve at all under `-I`.
import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

ROWS = (
    "real-world/normal",
    "real-world/large",
    "django-only/filter-arg",
    "django-only/realistic",
    "real-world/complex",
    "django-only/i18n",
)


def counted(workload, handle, context, seen):
    """(loops, iters, reads) for one render of one row, under stock Django.

    `seen` is filled with every loop-variable name the row's loops bind: the other
    half of this table counts *our* walk's reads of the same names, so the two
    columns are about the same keys.
    """
    from django.template import defaulttags
    from django.template.context import BaseContext

    state = {"loops": 0, "iters": 0, "reads": 0, "all": 0, "live": []}
    orig_for = defaulttags.ForNode.render
    orig_set = BaseContext.__setitem__
    orig_get = BaseContext.__getitem__

    def for_render(self, ctx):
        state["loops"] += 1
        state["live"].append(tuple(self.loopvars))
        seen.update(self.loopvars)
        try:
            return orig_for(self, ctx)
        finally:
            state["live"].pop()

    def setitem(self, key, value):
        if any(key in names for names in state["live"]):
            state["iters"] += 1
        return orig_set(self, key, value)

    def getitem(self, key):
        state["all"] += 1
        if any(key in names for names in state["live"]):
            state["reads"] += 1
        return orig_get(self, key)

    defaulttags.ForNode.render = for_render
    BaseContext.__setitem__ = setitem
    BaseContext.__getitem__ = getitem
    try:
        handle.render(context)
    finally:
        defaulttags.ForNode.render = orig_for
        BaseContext.__setitem__ = orig_set
        BaseContext.__getitem__ = orig_get
    return state["loops"], state["iters"], state["reads"], state["all"]


def measured(work, rows):
    """The A/B's paired median render delta per row, from its own JSON."""
    out = {}
    for row in rows:
        sides = {}
        for build in ("prefix", "fixed"):
            vals = []
            for path in sorted(work.glob(f"corpus-{build}-r*.json")):
                for rec in json.loads(path.read_text())["records"]:
                    if (rec["key"], rec["mode"], rec["engine"]) == (row, "render", "rust"):
                        vals.append(rec["best_us"])
            sides[build] = vals
        if len(sides["prefix"]) == len(sides["fixed"]) and sides["prefix"]:
            out[row] = statistics.median(
                f - p for p, f in zip(sides["prefix"], sides["fixed"])
            )
    return out


def ours(workload, names):
    """(writes, reads, any-key reads, ForNodes ran, ForNodes delegated) for the walk.

    The two counters wrap **our** `Context` rather than Django's, over the names
    the stock pass collected. The walk answers a lookup with `PyObject_GetItem`
    and writes one with `PyObject_SetItem`, which reach a class's `__getitem__`
    and `__setitem__` exactly as `context[key]` and `context[key] = v` do, so one
    counter shape sees both engines and the columns are comparable.

    **The writes column is the fourth round's correction.** `iters` is stock's
    count of the state writes, and the price is paid on the writes this build
    adds — which is `iters` only where the other build wrote none. Reading it off
    this build is what makes the two-build subtraction possible; `ours` on the
    ordered pass is a different number on four of the six rows, so the table is
    only meaningful with an artifact sha beside it.

    The two ForNode counts are the walk's own account, and they answer the question
    the stock counts cannot: *which* loops this change reaches. The deletion removed
    elision, not dispatch, so the ordered pass ran the same set of nodes — the
    `ran`/`gave` columns are the same on both builds, and the `reads`/`writes`
    columns are not.
    """
    import django_rust_templates._native as native
    from django_rust_templates.engine.context import Context as OurContext

    engine = bench_engines.build(
        "rust", workload.templates, workload.libraries("rust")
    )
    state = {"reads": 0, "all": 0, "writes": 0}
    orig_get = OurContext.__getitem__
    orig_set = OurContext.__setitem__

    def getitem(self, key):
        state["all"] += 1
        if key in names:
            state["reads"] += 1
        return orig_get(self, key)

    def setitem(self, key, value):
        if key in names:
            state["writes"] += 1
        return orig_set(self, key, value)

    OurContext.__getitem__ = getitem
    OurContext.__setitem__ = setitem
    native.census_start()
    try:
        handle = engine.get(workload.entry)
        context = engine.context(workload.data)
        engine.render(handle, context)
    finally:
        OurContext.__getitem__ = orig_get
        OurContext.__setitem__ = orig_set
        handled, delegated, _, _, _ = native.census_take()
        engine.close()
    return (state["writes"], state["reads"], state["all"],
            dict(handled).get("ForNode", 0), dict(delegated).get("ForNode", 0))


def artifact_sha() -> str:
    """The sha256 of the `.so` this run is measuring, resolved the way the code does.

    Not `find _native*.so`: a stale `site-packages/_native/` orphan beside the
    editable install answers that search instead of the live artifact.
    """
    import django_rust_templates._native as native

    return hashlib.sha256(pathlib.Path(native.__file__).read_bytes()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="the A/B directory to read deltas from")
    ap.add_argument("--label", default="", help="what this artifact is, for the header")
    ap.add_argument("--json", default=None, help="write the per-row counts here")
    ap.add_argument("--per-iteration", type=float, default=0.2872,
                    help="us per iteration of the statement (family D, ab4)")
    ap.add_argument("--per-read", type=float, default=0.1801,
                    help="us per read of a loop variable served from the context")
    args = ap.parse_args()

    wanted = set(ROWS)
    deltas = measured(pathlib.Path(args.work), ROWS)
    print(f"artifact sha256 {artifact_sha()}   {args.label}".rstrip())
    print(f"prices: {args.per_iteration:.4f} us/iteration, {args.per_read:.4f} us/read")
    print()
    print(f"{'row':<24}{'loops':>6}{'iters':>7}{'reads':>7}{'get':>7}"
          f"{'owrite':>7}{'ours':>7}{'oget':>7}{'for':>7}{'predicted':>11}{'measured':>10}{'m/p':>7}")
    dump = {}
    for workload in bench_corpus.workloads():
        if workload.key not in wanted:
            continue
        templates = workload.templates
        names: set[str] = set()
        engine = bench_engines.build("django", templates, workload.libraries("django"))
        try:
            handle = engine.get(workload.entry)
            context = engine.context(workload.data)
            loops, iters, reads, gets = counted(workload, handle, context, names)
        finally:
            engine.close()
        writes, our_reads, our_gets, ran, gave = ours(workload, names)
        predicted = iters * args.per_iteration + reads * args.per_read
        got = deltas.get(workload.key, float("nan"))
        dump[workload.key] = {
            "loops": loops, "iters": iters, "reads": reads, "get": gets,
            "owrite": writes, "ours": our_reads, "oget": our_gets,
            "ran": ran, "gave": gave, "measured": got,
        }
        print(f"{workload.key:<24}{loops:>6}{iters:>7}{reads:>7}{gets:>7}"
              f"{writes:>7}{our_reads:>7}{our_gets:>7}{ran:>4}/{gave:<2}"
              f"{predicted:>11.3f}{got:>10.3f}{got / predicted:>7.2f}")
    print()
    print("loops/iters/reads: stock Django's, counted at the writes and reads of a live")
    print("  loop variable. get: stock's reads of any key, for comparison.")
    print("owrite/ours/oget: the walk's writes and reads of those same names, and of any")
    print("  key — these move with the build, so run this once per artifact and subtract.")
    print("for: ForNodes the walk ran / handed to Python (census); the same on both builds.")
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps({"artifact": artifact_sha(), "rows": dump}, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
