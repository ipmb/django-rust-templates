"""The scatter of `bench.py`'s own ratio, between two runs of *one* artifact.

    .venv/bin/python -I .review/m19-measurements/bench_scatter.py A.json B.json

**What this measures, and what it is for.**  `bench.py --json` writes one
`best_us` per `(key, mode, engine)`.  The table it prints reports `ours/stock`,
a ratio of two of those cells *within one run*.  A cross-run delta -- "this row
moved from 0.894 to 0.33" -- is therefore a difference of two ratios taken from
two runs, and the honest question is how much that difference can be when
**nothing changed**: same artifact, same interpreter, back to back.

So this takes two JSON payloads from the *same* build (it refuses two different
`sha256:` values, and says so, because a scatter measured across two builds is
the delta it was supposed to bound) and reports, per row, the relative change of
the ratio.  That spread is the band a cross-run timing claim owes, and it is
stated **before** the claim it bounds.

**The unit is the ratio, not the microsecond**, because the ratio is what the
table prints and what a delta is quoted in.  A per-cell µs scatter is the wrong
denominator here: `django`'s own cell is a denominator shared by every ratio in
its row, so its noise cancels in a way per-cell µs cannot show.

Nothing here subtracts or corrects; it prints and summarises.  A row whose two
runs disagree by more than the median is listed by name rather than smoothed
away, because "the band is 0.8%" and "this row is 4%" are different facts about
the instrument and a reader needs both.
"""

import json
import pathlib
import statistics
import sys


def load(path):
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    sha = payload["native"].split("sha256:")[1].split()[0]
    cells = {}
    for rec in payload["records"]:
        if rec["engine"] in ("rust", "django"):
            cells[(rec["key"], rec["mode"], rec["engine"])] = rec["best_us"]
    return sha, cells, payload


def ratios(cells):
    out = {}
    for (key, mode, engine) in cells:
        if engine != "rust":
            continue
        stock = cells.get((key, mode, "django"))
        if stock is None or stock == 0:
            continue
        out[(key, mode)] = cells[(key, mode, "rust")] / stock
    return out


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    sha_a, cells_a, pay_a = load(argv[1])
    sha_b, cells_b, pay_b = load(argv[2])

    if sha_a != sha_b:
        print(
            f"REFUSING: the two runs are different artifacts — {sha_a} and {sha_b}. "
            "This script bounds a same-tree scatter; the difference between two "
            "builds is the delta it was meant to bound, not the band around it."
        )
        return 2

    for pay, name in ((pay_a, argv[1]), (pay_b, argv[2])):
        print(f"{name}: repeats={pay['args']['repeats']} target={pay['args']['target']}")
    print(f"artifact: sha256:{sha_a} — one build, two runs\n")

    ra, rb = ratios(cells_a), ratios(cells_b)
    shared = sorted(set(ra) & set(rb))

    print(f"{'row':<34} {'run A':>8} {'run B':>8} {'rel':>9}")
    print("-" * 62)
    rels = []
    for (key, mode) in shared:
        rel = (rb[(key, mode)] - ra[(key, mode)]) / ra[(key, mode)]
        rels.append((abs(rel), key, mode, ra[(key, mode)], rb[(key, mode)], rel))
    for _, key, mode, a, b, rel in sorted(rels, key=lambda r: -r[0]):
        print(f"{key + ' ' + mode:<34} {a:>8.3f} {b:>8.3f} {rel:>+8.2%}")

    mags = [r[0] for r in rels]
    print(f"\n{len(mags)} row(s) with an `ours/stock` ratio in both runs")
    print(f"median |rel|: {statistics.median(mags):.2%}")
    print(f"max    |rel|: {max(mags):.2%}   ({sorted(rels, key=lambda r: -r[0])[0][1]} "
          f"{sorted(rels, key=lambda r: -r[0])[0][2]})")
    print(f"min    |rel|: {min(mags):.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
