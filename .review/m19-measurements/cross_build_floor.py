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
# not.  Both lists are read off the census partitions, not guessed.
#
# **Slice 2's lists.**  The two rows whose partition changed were the two loops
# that gained a delegate-bearing body, and the two asserted to still refuse were
# `complex` (a nested `{% for %}`) and `for-tags` (a `forloop` read).
#
# **Slice 3's lists invert those two roles, which is the whole point of the
# slice.**  `real-world/complex`'s outer loop no longer refuses, so it is the
# subject; `django-only/for-tags` still refuses — for the *other* reason, its
# body's own `{{ forloop.counter }}` — so it is the control that says the
# refusal `sealable` keeps is still the one that fires.  Every other row is a
# control, which for this slice is a much larger population than slice 2's 46:
# `real-world/complex` is the only corpus row with a nested loop (verified over
# all 18 workloads: max `for`-nesting depth 2, 1 row), so nothing else can act.
SUBJECT_SLICE2 = {
    ("django-only/realistic", "render"): "loop body holds `{% blocktranslate %}` -> seals",
    ("django-only/realistic", "parse"): "same loop, reached by `from_string` too",
    ("django-only/i18n", "render"): "loop body holds `{% translate %}` -> seals",
    ("django-only/i18n", "parse"): "same loop",
}
REFUSAL_SLICE2 = {
    ("real-world/complex", "render"): "outer body holds a nested `{% for %}` -> must still refuse",
    ("real-world/complex", "parse"): "same loop, must still refuse",
    ("django-only/for-tags", "render"): "body references `forloop` -> must still refuse",
    ("django-only/for-tags", "parse"): "same loop, must still refuse",
}
SUBJECT_SLICE3 = {
    ("real-world/complex", "render"): "nested `{% for %}` seals -> the inner loop leaves Python",
    ("real-world/complex", "parse"): "same loop; parse is compile + a cold render, so it owes this too",
}
REFUSAL_SLICE3 = {
    ("django-only/for-tags", "render"): "body reads `forloop` -> the refusal that stays must still fire",
    ("django-only/for-tags", "parse"): "same loop, must still refuse",
}

# **The fix's lists, and they are named by a predicate rather than by the census
# — which is the one place this file departs from its own rule, so it says so.**
# The rows above were read off the census partitions, because `sealable` is what
# moved and the census counts `sealable`'s answer. The fix moves `seal`'s *write*
# pass instead (`may_write_context` gates `rewrite`), and the census counts the
# read pass only: the partition is identical on both sides of this change by
# construction, so the census cannot name the subject and a run of it would print
# the same rows twice. What names the subject is the predicate itself, localized
# against the corpus source (`scripts/bench_corpus.py`): a *sealing* loop whose
# body holds an `Act::Delegate` **and** has a walk-owned read of the loop
# variable for the skipped `rewrite` to have moved. Two rows are that, and both
# are named with their read:
#
#   * `real-world/complex`'s **outer** loop. Its body holds a nested `{% for %}`
#     and an `{% if %}`; `may_write_context` descends into the nested `Act::For`,
#     finds the `{% include %}` in *its* body, and answers true — so the outer
#     body's `{{ s.title|title }}` and `{% if s.total %}{{ s.total }}` stay
#     context reads (12 outer iterations, 2 reads each). The *inner* loop's own
#     rewrite is skipped too, and costs nothing: its body is the include alone, so
#     it had no loop-variable read to move.
#
#   * `django-only/realistic`'s loop. Body holds `{% blocktranslate %}`, and
#     `{{ row.name }}` and `{{ row.value|floatformat:2 }}` are walk-owned reads of
#     `row` (50 rows, 2 reads each).
#
# `django-only/i18n` is the pair's third case and is deliberately a *refusal*: its
# loop body holds `{% blocktranslate %}` so the predicate is true and `rewrite` is
# skipped, but every name the delegate reads (`count=r.value`) is read *inside*
# the delegate, which resolves against the context either way — the walk owns no
# read of `r` in that body, so the skip has nothing to skip. It is in this list
# rather than left unclassified because "the predicate fired" and "the route
# changed" are two statements, and a row that shows the first without the second
# is the control that keeps them apart.
SUBJECT_FIX = {
    ("real-world/complex", "render"): "outer body holds a delegate one `{% for %}` deep, and reads `s.title`/`s.total`",
    ("real-world/complex", "parse"): "same loop; parse is compile + a cold render, so it owes this too",
    ("django-only/realistic", "render"): "loop body holds `{% blocktranslate %}`, and reads `row.name`/`row.value`",
    ("django-only/realistic", "parse"): "same loop; parse is compile + a cold render, so it owes this too",
}
REFUSAL_FIX = {
    ("django-only/filter-arg", "render"): "body reads `r.name`/`r.value` and holds **no** delegate -> the rewrite must still run",
    ("django-only/filter-arg", "parse"): "same loop, must still rewrite",
    ("real-world/normal", "render"): "body reads `r.*`; the one `{% include %}` is at block level, outside the loop",
    ("real-world/normal", "parse"): "same loop, must still rewrite",
    ("django-only/i18n", "render"): "predicate fires but the body has no walk-owned read of `r` -> nothing to lose",
    ("django-only/i18n", "parse"): "same loop, same nothing to lose",
    ("django-only/for-tags", "render"): "the loop refuses, so neither pass of `seal` reaches it",
    ("django-only/for-tags", "parse"): "same loop, still refuses",
}

# Which slice's lists this run reports.  The sets are kept rather than
# overwritten because each output beside it is the record of a run this file made
# (`cross_build_floor.slice2.txt`, `cross_build_floor.slice3.txt`), and a file
# edited in place would leave that record describing a script that no longer
# exists.  `fix` is the default now, and the two earlier runs are reproducible by
# setting `SLICE` back — the payload paths are arguments either way.
SLICES = {
    "slice2": (SUBJECT_SLICE2, REFUSAL_SLICE2),
    "slice3": (SUBJECT_SLICE3, REFUSAL_SLICE3),
    "fix": (SUBJECT_FIX, REFUSAL_FIX),
}
SLICE = "fix"
SUBJECT, REFUSAL = SLICES[SLICE]


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
