import json, math, pathlib, sys
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

L = "/Users/pete/projects/lincolnloop/django-rust-templates/.review/m19-measurements/"
print("=== validation: lever-1 payloads, published 0.494/0.498 → 0.466/0.462 (render) ===")
o, keys = tiles([L + f"headline-lever1-{n}.json" for n in ("a1", "a2", "b1", "b2")])
for m, (b, a) in o.items():
    print(f"  {m:8} before {b:.3f}   after {a:.3f}")
print(f"  real-world rows: {[k for k,_ in keys]}")
print()
H = "/Users/pete/.claude/jobs/627577b5/tmp/headline3/"
print("=== lever-3 payloads ===")
o2, _ = tiles([H + f"{n}.json" for n in ("a1", "a2", "b1", "b2")])
for m, (b, a) in o2.items():
    print(f"  {m:8} before {b:.3f}   after {a:.3f}")
