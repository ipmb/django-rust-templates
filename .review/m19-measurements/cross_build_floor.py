"""The cross-build control floor, and the deltas it bounds.

`bench.py --compare` bounds each delta by the scatter of *its own row* across two
runs of one build, and refuses to mix a band from two builds into a comparison of
two builds.  It is right to: this comparison's two sides **are** two builds, so
the noise a delta owes includes build-to-build layout, which a same-build pair
cannot see.  The population that measures it is the cells **this change cannot
act on** — a row with no `{% for %}` whose body holds a node the walk hands back,
which for step 5 slice 2 is exactly the set the census reports as identical in
every column.  Their measured |rel|, over the same corpus with the same
instrument across the same two builds, is the floor a cross-build delta has to
clear.

Three quantities are printed, and they are three:

  * `same-build` — |rel| between two runs of *one* side.  This is what
    `bench.py --compare`'s own band measures.  It is printed so the control floor
    can be read against it.
  * `control floor` — |rel| between the two builds, on cells the change cannot
    reach.  Everything a delta owes that a same-build pair misses is in here.
  * the named deltas — the cells that should move, each with the mechanism that
    should move it, so a cell that moves without a mechanism is visible as such
    instead of counted as a win.

The floor is printed **before** the deltas it bounds, and the control's own right
tail is named rather than smoothed: a control cell outside the floor is the
statement that the floor is still too tight.

Run:  .venv/bin/python -I <this file> <before1.json> [<before2.json>] -- <after...json>
"""

import json
import statistics
import sys
from pathlib import Path

# The cells this slice should move, with the mechanism, and the cells that must
# not.  Both lists are read off `node_census.slice2-before.txt` against
# `node_census.step5-slice2.txt`, not guessed: these are the only two rows whose
# partition changed, and the two rows whose `{% for %}` is asserted to still
# refuse.
SUBJECT = {
    ("django-only/realistic", "render"): "loop body holds `{% blocktranslate %}` -> seals",
    ("django-only/realistic", "parse"): "same loop, reached by `from_string` too",
    ("django-only/i18n", "render"): "loop body holds `{% translate %}` -> seals",
    ("django-only/i18n", "parse"): "same loop",
}
REFUSAL = {
    ("real-world/complex", "render"): "outer body holds a nested `{% for %}` -> must still refuse",
    ("real-world/complex", "parse"): "same loop, must still refuse",
    ("django-only/for-tags", "render"): "body references `forloop` -> must still refuse",
    ("django-only/for-tags", "parse"): "same loop, must still refuse",
}


def ratios(pay):
    """(row, mode) -> ours/stock, which is the unit a delta of ours must be in."""
    out = {}
    for r in pay["records"]:
        if r["engine"] not in ("rust", "django"):
            continue
        out.setdefault((r["key"], r["mode"]), {})[r["engine"]] = r["best_us"]
    return {k: v["rust"] / v["django"] for k, v in out.items() if len(v) == 2}


def median_of(pays):
    """Cell -> median ratio over the runs, and cell -> the runs' own spread."""
    each = [ratios(p) for p in pays]
    cells = set(each[0])
    for e in each[1:]:
        cells &= set(e)
    med, spread = {}, {}
    for c in sorted(cells):
        vals = [e[c] for e in each]
        med[c] = statistics.median(vals)
        if len(vals) > 1:
            spread[c] = (max(vals) - min(vals)) / min(vals)
    return med, spread


def fmt_spread(spread):
    if not spread:
        return "n/a (one run)"
    v = sorted(spread.values())
    return f"median {statistics.median(v):.2%}  p90 {v[int(0.9 * (len(v) - 1))]:.2%}  max {v[-1]:.2%}"


def main(argv):
    cut = argv.index("--")
    before = [json.loads(Path(p).read_text()) for p in argv[:cut]]
    after = [json.loads(Path(p).read_text()) for p in argv[cut + 1 :]]
    mb, sb = median_of(before)
    ma, sa = median_of(after)

    for label, pays in (("before", before), ("after", after)):
        shas = {pay["native"].split("sha256:")[1].split()[0] for pay in pays}
        print(f"{label}: {len(pays)} run(s), artifact sha256:{' + '.join(sorted(shas))}"
              + ("  <-- TWO BUILDS, the runs are not one side" if len(shas) > 1 else ""))
    print(f"  before same-build scatter |rel|: {fmt_spread(sb)}")
    print(f"  after  same-build scatter |rel|: {fmt_spread(sa)}")
    print()

    cells = sorted(set(mb) & set(ma))
    named = set(SUBJECT) | set(REFUSAL)
    controls = [c for c in cells if c not in named]
    print(f"cells compared: {len(cells)}")
    print(f"  named subject/refusal: {len(named & set(cells))}")
    print(f"  control (this change cannot act): {len(controls)}")
    print()

    ctrl = {c: (ma[c] - mb[c]) / mb[c] for c in controls}
    vals = sorted(abs(v) for v in ctrl.values())
    worst = max(ctrl, key=lambda c: abs(ctrl[c]))
    print(f"CONTROL floor, cross-build, {len(vals)} cells the change cannot reach:")
    print(f"  median {statistics.median(vals):.2%}   "
          f"p90 {vals[int(0.9 * (len(vals) - 1))]:.2%}   max {vals[-1]:.2%}  "
          f"({worst[0]} {worst[1]})")
    print("  every named delta below has to clear the max to be a move")
    print()

    for title, group in (("SUBJECT (must move)", SUBJECT), ("REFUSAL (must not move)", REFUSAL)):
        print(f"--- {title}")
        rows = [(c, (ma[c] - mb[c]) / mb[c]) for c in group if c in cells]
        for c, d in sorted(rows, key=lambda r: -abs(r[1])):
            flag = "MOVE " if abs(d) > vals[-1] else "     "
            print(f"  {flag}{c[0] + ' ' + c[1]:<34} {d:>+8.2%}   {group[c]}")
        print()

    print("--- CONTROL cells furthest from zero (the floor's right tail, named)")
    for c in sorted(controls, key=lambda c: -abs(ctrl[c]))[:6]:
        print(f"     {c[0] + ' ' + c[1]:<34} {ctrl[c]:>+8.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
