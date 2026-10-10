#!/usr/bin/env python
"""The root read's cost per lookup, measured by alternating two builds.

`.review/m19-measurements/frame-rewrite-fix.md` §5.5's third cost lever — the
read. `PyTarget::get_item` enters Python at `BaseContext.__getitem__` for **every
root lookup the walk makes**, and this build transcribes that upward search in
Rust behind a guard read against the function `engine/base.py` pinned at import.
Unlike the first lever this one is not confined to loops: it applies to every
root read, inside a `{% for %}` or not, which is why the lever is the largest of
the three and why the family below carries a row with no loop in it at all.

The rows:

  * `L{n}` — `{% for r in rows{n} %}{% endfor %}`, an **empty body**, for n in
    1/10/50/200. The per-iteration machinery (seven `forloop` writes, the
    iterator's `next`) and **no read of any kind**. This is the control, and the
    only row here a change to `get_item` cannot reach: a lever in the read path
    cannot move a body that reads nothing. If `L`'s slope moves, the instrument
    moved and no number below is about the code.
  * `V{n}` — the same loop with one `{{ a }}` a row. `a` is a plain name, so the
    walk reads it at the **root** — one transcription per iteration, and
    `slope(V) - slope(L)` is the cost of exactly one root read. That difference
    is the reading this file exists for, and it is taken as a difference of
    fitted slopes rather than as a row minus a floor because `L` and `V` do not
    share an intercept: the two templates are not the same template.
  * `W{n}` — the same loop with one `{{ r.name }}` a row. The subject under real
    work: `r` is a root read like `a`'s, and the lever's saving has to survive
    beside the `getattr` arm that `name` takes.
  * `P` — `{{ a }}` fifty times, **no `{% for %}` anywhere**. This is **not a
    control for this lever** and calling it one would be false where it prints:
    §5.5 says the transcription "applies to every root lookup the walk makes and
    not only to loop variables", so `P` is a second *subject*, the non-loop route,
    and it has to move. It was the control for the first lever, which lived inside
    the loop body; the row is kept because a reading of the same quantity on the
    other route is the cheapest check that the two agree.

A slope and not a row: one loop of one length gives a total, and four lengths give
`cost(n) = a + b*n`, where `b` is the number the lever is a claim about. The
intercepts are printed beside the slopes because a slope read without its
baseline is the mistake §5.5's own first lever already paid for once.

Usage:

    .venv/bin/python -I root_read_ab.py build --prefix-src P.rs --fixed-src F.rs --work W
    .venv/bin/python -I root_read_ab.py ab     --work W --rounds 12
    .venv/bin/python -I root_read_ab.py report --work W

`build` leaves `W/prefix.so` and `W/fixed.so` and prints their sha256; `ab` copies
one into the package between runs and **asserts the sha it finds there**, so a
round that ran the wrong build is a refusal and not a number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
from types import SimpleNamespace

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

LIB_RS = ROOT / "crates/template-py/src/lib.rs"
SO = ROOT / "python/django_rust_templates/_native.cpython-314-darwin.so"
PY = ROOT / ".venv/bin/python"

N_LIST = (1, 10, 50, 200)
CONTEXT = {f"rows{n}": [{"name": f"row{i}"} for i in range(n)] for n in N_LIST}
CONTEXT["a"] = 1

SOURCES = {
    **{f"L{n}": f"{{% for r in rows{n} %}}{{% endfor %}}" for n in N_LIST},
    **{f"V{n}": f"{{% for r in rows{n} %}}{{{{ a }}}}{{% endfor %}}" for n in N_LIST},
    **{f"W{n}": f"{{% for r in rows{n} %}}{{{{ r.name }}}}{{% endfor %}}" for n in N_LIST},
    "P": "{{ a }}" * 50,
}
LABELS = tuple(SOURCES)
MODES = ("render", "parse", "compile")
ENGINES = ("rust", "django")

THRESHOLD = 0.02


def sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def live_sha() -> str:
    """The artifact the package resolves, the way the code resolves it."""
    out = subprocess.run(
        [
            str(PY), "-I", "-c",
            "import django_rust_templates._native as n, hashlib, pathlib;"
            "print(hashlib.sha256(pathlib.Path(n.__file__).resolve().read_bytes()).hexdigest()[:16])",
        ],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def workload(label: str):
    return SimpleNamespace(
        key=f"read/{label}",
        templates={f"{n}.html": s for n, s in SOURCES.items()},
        entry=f"{label}.html",
    )


def cmd_build(args) -> int:
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    saved = LIB_RS.read_text()
    try:
        for name, src in (("prefix", args.prefix_src), ("fixed", args.fixed_src)):
            LIB_RS.write_text(pathlib.Path(src).read_text())
            subprocess.run(["maturin", "develop", "--release"], cwd=ROOT, check=True,
                           capture_output=True, text=True)
            shutil.copy(SO, work / f"{name}.so")
            print(f"{name}: source {src} -> artifact sha256:{sha16(work / f'{name}.so')}")
    finally:
        LIB_RS.write_text(saved)
    subprocess.run(["maturin", "develop", "--release"], cwd=ROOT, check=True,
                   capture_output=True, text=True)
    print(f"tree restored: artifact sha256:{live_sha()}")
    return 0


def cmd_ab(args) -> int:
    work = pathlib.Path(args.work)
    expect = {n: sha16(work / f"{n}.so") for n in ("prefix", "fixed")}
    for name, sha in expect.items():
        print(f"{name} artifact sha256:{sha}")
    subprocess.run(
        [str(PY), "-I", str(pathlib.Path(__file__)), "calibrate",
         "--pins", str(work / "pins.json")],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    print(f"calibrated (on the artifact in place); pins in {work / 'pins.json'}")
    for r in range(1, args.rounds + 1):
        order = ("prefix", "fixed") if r % 2 else ("fixed", "prefix")
        for name in order:
            shutil.copy(work / f"{name}.so", SO)
            got = live_sha()
            if got != expect[name]:
                print(f"VOID: round {r} {name} -- live artifact {got} != {expect[name]}",
                      file=sys.stderr)
                return 1
            out = work / f"{name}-r{r:02d}.json"
            subprocess.run(
                [str(PY), "-I", str(pathlib.Path(__file__)), "run", "--json", str(out),
                 "--pins", str(work / "pins.json")],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            print(f"round {r:>2}  {name:<6} sha {got}")
    print(f"done: {args.rounds * 2} runs")
    return 0


def cmd_calibrate(args) -> int:
    import bench
    import bench_engines

    built = {n: bench_engines.build(n, {f"{k}.html": s for k, s in SOURCES.items()}, {})
             for n in ENGINES}
    contexts = {n: built[n].context(CONTEXT) for n in ENGINES}
    pins = {}
    for label in LABELS:
        w = workload(label)
        for mode in MODES:
            pins[f"{label}/{mode}"] = bench.calibrate(
                built["rust"], w, built["rust"].get(w.entry), mode, contexts["rust"], 0.04
            )
    for e in built.values():
        e.close()
    pathlib.Path(args.pins).write_text(json.dumps(pins, indent=1))
    print(f"wrote {args.pins}: {len(pins)} pins")
    return 0


def cmd_run(args) -> int:
    import bench
    import bench_engines

    built = {n: bench_engines.build(n, {f"{k}.html": s for k, s in SOURCES.items()}, {})
             for n in ENGINES}
    contexts = {n: built[n].context(CONTEXT) for n in ENGINES}
    pins = json.loads(pathlib.Path(args.pins).read_text())

    records = []
    for label in LABELS:
        w = workload(label)
        for mode in MODES:
            for name in ENGINES:
                handle = built[name].get(w.entry)
                best, worst, iters, out = bench.measure(
                    built[name], w, handle, mode, contexts[name],
                    7, 0.04, pin_iters=pins[f"{label}/{mode}"],
                )
                records.append({"key": w.key, "mode": mode, "engine": name,
                                "best_us": best, "worst_us": worst, "iters": iters,
                                "bytes": len(out)})
    seen = {}
    for r in records:
        seen.setdefault(r["key"], set()).add(r["bytes"])
    bad = {k: v for k, v in seen.items() if len(v) > 1}
    for e in built.values():
        e.close()
    if bad:
        print(f"VOID: labels whose modes render different lengths: {bad}", file=sys.stderr)
        return 1

    import django_rust_templates._native as native_mod

    native = pathlib.Path(native_mod.__file__).resolve()
    payload = {
        "native": f"sha256:{hashlib.sha256(native.read_bytes()).hexdigest()} "
                  f"({native.stat().st_size} bytes)",
        "records": records,
    }
    pathlib.Path(args.json).write_text(json.dumps(payload, indent=1))
    print(f"wrote {args.json}: {payload['native'].split()[0]}, {len(records)} records")
    return 0


def _load(work: pathlib.Path, build: str, key: str, mode: str, engine: str = "rust"):
    vals = []
    for p in sorted(work.glob(f"{build}-r*.json")):
        for r in json.loads(p.read_text())["records"]:
            if (r["key"], r["mode"], r["engine"]) == (key, mode, engine):
                vals.append(r["best_us"])
    return vals


def _med(vals):
    return statistics.median(vals) if vals else float("nan")


def _paired(p, f):
    """Within-round differences: `cmd_ab` runs a round's two sides back to back
    (order reversed on even rounds), so `f[i] - p[i]` carries whatever drifted
    between rounds in both terms and cancels it."""
    return [b - a for a, b in zip(p, f)] if len(p) == len(f) else []


def _fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def cmd_report(args) -> int:
    work = pathlib.Path(args.work)
    for mode in MODES:
        print(f"--- ours, {mode}")
        print(f"{'label':10}{'n':>5}{'prefix med':>12}{'fixed med':>11}{'sep delta':>11}"
              f"{'paired med':>12}{'paired min':>12}{'paired max':>12}"
              f"{'p drift':>9}{'f drift':>9}   ({args.rounds} runs a side)")
        delta = {}
        for label in LABELS:
            n = int(label[1:]) if label[0] in "LVW" else 0
            key = f"read/{label}"
            p = _load(work, "prefix", key, mode)
            f = _load(work, "fixed", key, mode)
            if not p or not f:
                continue
            mp, mf = _med(p), _med(f)
            pair = _paired(p, f)
            delta[label] = _med(pair) if pair else mf - mp
            sub = abs(delta[label] / mp) > THRESHOLD
            print(f"{label:10}{n:>5}{mp:>12.3f}{mf:>11.3f}{mf - mp:>+11.3f}"
                  f"{delta[label]:>+12.3f}"
                  f"{(min(pair) if pair else float('nan')):>+12.3f}"
                  f"{(max(pair) if pair else float('nan')):>+12.3f}"
                  f"{(max(p) - min(p)) / min(p):>9.2%}{(max(f) - min(f)) / min(f):>9.2%}"
                  f"   {'MOVE' if sub else ''}")
        print()
        slopes = {}
        for fam, what in (("L", "empty body, no read anywhere"),
                          ("V", "one `{{ a }}` a row -- one root read"),
                          ("W", "one `{{ r.name }}` a row")):
            if all(f"{fam}{n}" in delta for n in N_LIST):
                xs = list(N_LIST)
                pv = [_med(_load(work, "prefix", f"read/{fam}{n}", mode)) for n in N_LIST]
                fv = [_med(_load(work, "fixed", f"read/{fam}{n}", mode)) for n in N_LIST]
                ip, sp = _fit(xs, pv)
                if_i, sf = _fit(xs, fv)
                slopes[fam] = (sp, sf, ip, if_i)
                print(f"  {fam} ({what}): per-iteration "
                      f"prefix {sp:+.4f} us -> fixed {sf:+.4f} us  "
                      f"delta {sf - sp:+.4f} us/iteration  "
                      f"(intercepts prefix {ip:+.3f} us -> fixed {if_i:+.3f} us)")
        print()
        # The reading this file exists for: the cost of one root read, which is
        # `V`'s slope minus `L`'s, on each build. Both are printed, and so are the
        # paired per-row deltas that carry the same claim without a fit in it.
        if "L" in slopes and "V" in slopes:
            sp_l, sf_l, _, _ = slopes["L"]
            sp_v, sf_v, _, _ = slopes["V"]
            print("  one root read, as `V - L` on slopes: "
                  f"prefix {sp_v - sp_l:+.4f} us -> fixed {sf_v - sf_l:+.4f} us  "
                  f"delta {(sf_v - sf_l) - (sp_v - sp_l):+.4f} us/read")
        for n in N_LIST:
            vl, vf = delta.get(f"L{n}"), delta.get(f"V{n}")
            if vl is not None and vf is not None:
                print(f"  paired per-row, n={n:<4} V {vf:+.3f} - L {vl:+.3f} = "
                      f"{vf - vl:+.3f} us over {n} reads"
                      f" = {(vf - vl) / n * 1000:+.1f} ns/read")
        if "P" in delta:
            p = _load(work, "prefix", "read/P", mode)
            print("  P (fifty `{{ a }}`, no `{% for %}` at all -- the other route, "
                  f"and a subject here rather than a control): paired "
                  f"{delta['P']:+.3f} us, {abs(delta['P'] / _med(p)):.2%} of its row"
                  f"{'  MOVED' if abs(delta['P'] / _med(p)) > THRESHOLD else ''}"
                  f", {delta['P'] / 50 * 1000:+.1f} ns/read")
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--prefix-src", required=True)
    b.add_argument("--fixed-src", required=True)
    b.add_argument("--work", required=True)
    a = sub.add_parser("ab")
    a.add_argument("--work", required=True)
    a.add_argument("--rounds", type=int, default=12)
    c = sub.add_parser("calibrate")
    c.add_argument("--pins", required=True)
    r = sub.add_parser("run")
    r.add_argument("--json", required=True)
    r.add_argument("--pins", required=True)
    p = sub.add_parser("report")
    p.add_argument("--work", required=True)
    p.add_argument("--rounds", type=int, default=12)
    args = ap.parse_args()
    return {"build": cmd_build, "ab": cmd_ab, "calibrate": cmd_calibrate,
            "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
