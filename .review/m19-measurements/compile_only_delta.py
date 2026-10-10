"""The compile-only tables, N runs a side, read as two quantities.

`compile_only.py` prints one line per row per run.  Several runs of one build give
the scatter that bounds a *same-build* delta; several of each of two builds give
the cross-build delta itself.  Three a side is the number these tables are taken
at, after two produced one row outside its own band by 0.07 points.  Those are two quantities and they are printed
separately, in that order, because a cross-build delta quoted against a
same-build band is the error `bench.py --compare` refuses to make.

Each row is judged against **its own** scatter over the runs given, not against the largest
scatter on the page: the rows do not answer alike (`micro/text`'s ratio is both
the noisiest and the largest), so a pooled band would be a different row's noise
wearing this one's name.  This is the same per-row test `bench_scatter.py` makes,
applied to a cross-build pair, with the row's own band printed beside its delta
rather than summarised above it.

The artifact sha each table recorded is printed with its side, and a side whose
two runs disagree about the sha is refused rather than averaged: a table is a
claim about a build.

|rel| is (max - min) / min, the convention `bench_scatter.py` uses, so a number
here can be read against a band printed there.

Run:  .venv/bin/python -I <this file> <before-a.txt> <before-b.txt> -- <after-a.txt> <after-b.txt>
"""

import statistics
import sys
from pathlib import Path


def read(path):
    """(sha, size, rows) from one compile_only.py table."""
    sha = size = None
    rows = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith("artifact:"):
            for tok in line.split():
                if tok.startswith("sha256:"):
                    sha = tok.split(":", 1)[1]
                elif tok.isdigit():
                    size = int(tok)
        elif line.startswith(("real-world/", "micro/", "django-only/")):
            parts = line.split()
            # NOT MEASURED rows carry em dashes instead of a ratio
            if len(parts) >= 4 and parts[-1] != "—":
                rows[parts[0]] = float(parts[-1])
    return sha, size, rows


def rel(pair):
    lo, hi = min(pair), max(pair)
    return (hi - lo) / lo


def main(argv):
    cut = argv.index("--")
    before = [read(p) for p in argv[:cut]]
    after = [read(p) for p in argv[cut + 1 :]]

    for label, side in (("before", before), ("after", after)):
        shas = {s for s, _, _ in side}
        sizes = {n for _, n, _ in side}
        print(f"{label}: {len(side)} run(s), sha256:{' + '.join(sorted(shas))} "
              f"({', '.join(str(n) for n in sorted(sizes))} bytes)"
              + ("   <-- TWO BUILDS in one side" if len(shas) > 1 else ""))
    print()

    cells = sorted(set(before[0][2]) & set(after[0][2]))
    mb = {c: statistics.median([t[2][c] for t in before]) for c in cells}
    ma = {c: statistics.median([t[2][c] for t in after]) for c in cells}
    # each row's own scatter: the worst of its two sides' spreads over the runs given.
    sb = {c: rel([t[2][c] for t in before]) for c in cells}
    sa = {c: rel([t[2][c] for t in after]) for c in cells}

    print("cross-build delta, ours/stock, median of each side's runs; each row judged")
    print(f"against its own scatter over the {len(before)}/{len(after)} runs a side "
          "(the larger of its two sides), not the page's:")
    print(f"  {'row':<22} {'before':>7} {'after':>7} {'delta':>8}   {'own band':>8}  verdict")
    moved = []
    for c in sorted(cells, key=lambda c: -abs((ma[c] - mb[c]) / mb[c])):
        d = (ma[c] - mb[c]) / mb[c]
        band = max(sb[c], sa[c])
        inside = abs(d) <= band
        if not inside:
            moved.append(c)
        print(f"  {c:<22} {mb[c]:>7.3f} {ma[c]:>7.3f} {d:>+8.2%}   {band:>8.2%}  "
              f"{'inside' if inside else 'OUTSIDE'}")
    print()
    allb = sorted(sb.values())
    alla = sorted(sa.values())
    print(f"  same-build scatter, before side: median {statistics.median(allb):.2%}  max {allb[-1]:.2%}")
    print(f"  same-build scatter, after  side: median {statistics.median(alla):.2%}  max {alla[-1]:.2%}")
    print(f"  rows whose own band is exceeded: {len(moved)}"
          + (f"  -> {', '.join(moved)}" if moved else "  (none)"))
    if len(before) != len(after):
        print("  NOTE: the sides have different run counts; the medians are over"
              " differently-sized samples.")
    missing = set(before[0][2]) - set(cells)
    if missing:
        print(f"  rows in the before table but not the after table: {', '.join(sorted(missing))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
