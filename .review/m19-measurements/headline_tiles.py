import json, math, pathlib
def tiles(paths):
    """paths = (a1, a2, b1, b2). Rule: per row, mean of a side's two runs;
    ratio = mean rust / mean stock; geo mean over the three real-world rows."""
    R = []
    for p in paths:
        d = json.loads(pathlib.Path(p).read_text())
        R.append({(r["key"], r["mode"], r["engine"]): r["best_us"] for r in d["records"]})
    a1, a2, b1, b2 = R
    keys = sorted({(k, m) for (k, m, e) in a1 if e == "rust" and k.startswith("real-world/")})
    out = {}
    for mode in ("render", "parse", "compile"):
        rows = [(k, m) for k, m in keys if m == mode]
        def g(pair, engine):
            return [math.log(sum(p[(k, m, engine)] for p in pair) / 2) for k, m in rows]
        def side(pair):
            num, den = g(pair, "rust"), g(pair, "django")
            return math.exp(sum(n - d for n, d in zip(num, den)) / len(rows))
        out[mode] = (side((a1, a2)), side((b1, b2)))
    return out, keys

# Every payload lives beside this script: the tiles are a function of the archived
# JSONs and of nothing else, so re-running this file reproduces §5's table.
L = pathlib.Path(__file__).resolve().parent
SETS = [
    ("lever 1 (`dc174855d926defc` -> `3d3e99e63eddc036`)", "headline-lever1"),
    ("lever 3, pre-repair (`3d3e99e63eddc036` -> `f702a231cf4fa410`)", "headline-lever3"),
    ("lever 3, repaired (`3d3e99e63eddc036` -> `caf1589798eac0cf`)", "headline-lever3fix"),
]
for label, pre in SETS:
    o, keys = tiles([L / f"{pre}-{n}.json" for n in ("a1", "a2", "b1", "b2")])
    print(f"=== {label}   (side rule: mean of a side's two runs, then ratio, then geomean)")
    for m, (b, a) in o.items():
        print(f"  {m:8} before {b:.3f}   after {a:.3f}")
    if pre == "headline-lever1":
        print(f"  real-world rows: {[k for k, _ in keys]}")
