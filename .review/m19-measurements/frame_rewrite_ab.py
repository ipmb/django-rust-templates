#!/usr/bin/env python
"""The frame-rewrite gate's cost, measured by alternating two builds.

`frame_rewrite_reads.py` and `frame_rewrite_corpus.py` each produce one side of a
measurement; this builds both sides from source, alternates them round by round,
and prints the report.  It exists because the earlier attempt at this question
(`frame_rewrite_cost.py`) tried to get the cost out of **one** build by
differencing two templates, and that instrument moved by a factor of seven between
two runs on the same artifact.  Alternating the artifacts puts the question where
it can be answered: the same template, timed under both builds, so the cost is a
cross-build delta with no subtraction of two baselines anywhere.

Two sets are run, and they answer two different questions:

  * `reads` -- the four-template family, `A{k}` (k reads + an include, so the gate
    fires) against `B{k}` (k reads, no include), `C` (the include alone) and `D`
    (the empty loop).  `A{k}`'s delta over k is the per-read cost; `B`, `C` and `D`
    are controls **in the same family and the same runs**, which is what makes the
    floor the same population as the thing it bounds.
  * `corpus` -- the six corpus rows, two of which the gate can act on and four of
    which it cannot, so the per-read cost can be scaled to a page a reader
    recognizes.

**Which rows are controls depends on the two trees, and the report says which.**
The family's B/D rows hold a `{% for %}` and no delegate, so they carry the *push
order* — the fix's other half, which every loop pays — and they are therefore
controls **only** for a comparison in which the push order is the same on both
sides (`frame_rewrite_ab.txt`: the gate, against slice 3).  In
`frame_rewrite_ab2.txt` (the ordered pass, against the gate) B/D are *subject*
rows for that half and `C` is the only control the change cannot touch, which is
what the report's two lines print rather than one line labelled "must be 0".
A control line that is false at the point of output is worse than no line.

The iteration counts are calibrated once, on one side, and pinned for every run:
`bench.measure`'s own rule, and the reason a faster artifact is not handed a
larger N and a tighter best-of-N.

Usage:

    .venv/bin/python -I frame_rewrite_ab.py build   --prefix-src P.rs --fixed-src F.rs --work W
    .venv/bin/python -I frame_rewrite_ab.py ab      --work W --rounds 12
    .venv/bin/python -I frame_rewrite_ab.py report  --work W

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

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
LIB_RS = ROOT / "crates/template-py/src/lib.rs"
SO = ROOT / "python/django_rust_templates/_native.cpython-314-darwin.so"
PY = ROOT / ".venv/bin/python"

READS = ROOT / ".review/m19-measurements/frame_rewrite_reads.py"
CORPUS = ROOT / ".review/m19-measurements/frame_rewrite_corpus.py"

SUBJECT_ROWS = {"django-only/realistic", "real-world/complex"}


def sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def live_sha() -> str:
    """The artifact the package resolves, the way the code resolves it.

    Not `find _native*.so`: a stale `_native/` orphan beside the live editable
    install is what a bare `import _native` finds instead, and a `find` over the
    tree finds both without saying which one Python loads.
    """
    out = subprocess.run(
        [
            str(PY), "-I", "-c",
            "import django_rust_templates._native as n, hashlib, pathlib;"
            "print(hashlib.sha256(pathlib.Path(n.__file__).resolve().read_bytes()).hexdigest()[:16])",
        ],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def cmd_build(args) -> int:
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    saved = LIB_RS.read_text()
    try:
        for name, src in (("prefix", args.prefix_src), ("fixed", args.fixed_src)):
            LIB_RS.write_text(pathlib.Path(src).read_text())
            subprocess.run(
                ["maturin", "develop", "--release"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            shutil.copy(SO, work / f"{name}.so")
            print(f"{name}: source {src} -> artifact sha256:{sha16(work / f'{name}.so')}")
    finally:
        LIB_RS.write_text(saved)
    # The tree is left holding the fix, rebuilt, so a later run of the suites is
    # exercising the shipped source and not whichever side ran last.
    subprocess.run(["maturin", "develop", "--release"], cwd=ROOT, check=True,
                   capture_output=True, text=True)
    print(f"tree restored: artifact sha256:{live_sha()}")
    return 0


def cmd_ab(args) -> int:
    work = pathlib.Path(args.work)
    expect = {n: sha16(work / f"{n}.so") for n in ("prefix", "fixed")}
    for name, sha in expect.items():
        print(f"{name} artifact sha256:{sha}")
    # Pins are calibrated once, on whichever side happens to be in place, and used
    # by both. The corpus set calibrates per row inside each run, off the control
    # engine, so it needs none here.
    subprocess.run(
        [str(PY), "-I", str(READS), "--calibrate",
         "--pins", str(work / "family.pins.json")],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    print(f"calibrated (on the artifact in place); pins in {work / 'family.pins.json'}")
    for r in range(1, args.rounds + 1):
        order = ("prefix", "fixed") if r % 2 else ("fixed", "prefix")
        for name in order:
            shutil.copy(work / f"{name}.so", SO)
            got = live_sha()
            if got != expect[name]:
                print(f"VOID: round {r} {name} -- live artifact {got} != {expect[name]}",
                      file=sys.stderr)
                return 1
            for setname, script, extra in (
                ("family", READS, ["--pins", str(work / "family.pins.json")]),
                ("corpus", CORPUS, []),
            ):
                out = work / f"{setname}-{name}-r{r:02d}.json"
                subprocess.run(
                    [str(PY), "-I", str(script), "--json", str(out), *extra],
                    cwd=ROOT, check=True, capture_output=True, text=True,
                )
            print(f"round {r:>2}  {name:<6} sha {got}")
    print(f"done: {args.rounds * 2} runs per set")
    return 0


def _load(work: pathlib.Path, setname: str, build: str, key: str, mode: str, engine: str):
    vals = []
    for p in sorted(work.glob(f"{setname}-{build}-r*.json")):
        for r in json.loads(p.read_text())["records"]:
            if (r["key"], r["mode"], r["engine"]) == (key, mode, engine):
                vals.append(r["best_us"])
    return vals


def _med(vals):
    return statistics.median(vals) if vals else float("nan")


def _paired(p, f):
    """The per-round differences the two sides' *separate* medians cannot carry.

    `_load` returns each side's runs in round order, and `cmd_ab` runs the round's
    two sides back to back (order reversed on even rounds), so `f[i] - p[i]` is a
    difference taken within one round: whatever drifted between rounds — thermal,
    machine load, a daemon waking up — is in both terms and cancels. The median of
    those 12 differences is therefore the estimate, and the two separate medians
    printed beside it are the reading the second adversarial round asked to be
    replaced: they are taken over different moments in time, so their difference
    is a delta *plus* the drift between the two halves of the session.
    """
    return [b - a for a, b in zip(p, f)] if len(p) == len(f) else []


def _fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def cmd_report(args) -> int:
    work = pathlib.Path(args.work)
    for setname, keys, subjects in (
        ("family", ["A1", "A2", "A4", "B1", "B2", "B4", "C", "D"], ("A1", "A2", "A4")),
        ("corpus", sorted(SUBJECT_ROWS) + ["django-only/filter-arg", "django-only/i18n",
                                           "real-world/normal", "real-world/large"],
         tuple(sorted(SUBJECT_ROWS))),
    ):
        print(f"########## {setname}")
        for mode in ("render", "parse", "compile"):
            print(f"--- ours, {mode}")
            print(f"{'label':28}{'prefix med':>12}{'fixed med':>11}{'sep delta':>11}"
                  f"{'paired med':>12}{'paired min':>12}{'paired max':>12}"
                  f"{'p drift':>9}{'f drift':>9}   (12 runs a side)")
            deltas = {}
            for k in keys:
                key = f"family/{k}" if setname == "family" else k
                p = _load(work, setname, "prefix", key, mode, "rust")
                f = _load(work, setname, "fixed", key, mode, "rust")
                if not p or not f:
                    continue
                mp, mf = _med(p), _med(f)
                pair = _paired(p, f)
                dp = (max(p) - min(p)) / min(p)
                df = (max(f) - min(f)) / min(f)
                deltas[k] = _med(pair) if pair else mf - mp
                sub = abs(deltas[k] / mp) > 0.02
                print(f"{k:28}{mp:>12.3f}{mf:>11.3f}{mf - mp:>+11.3f}"
                      f"{deltas[k]:>+12.3f}"
                      f"{(min(pair) if pair else float('nan')):>+12.3f}"
                      f"{(max(pair) if pair else float('nan')):>+12.3f}"
                      f"{dp:>9.2%}{df:>9.2%}   {'MOVE' if sub else ''}")
            if setname == "family":
                loops = [k for k in ("B1", "B2", "B4", "D") if k in deltas]
                print("   no-delegate loops (a `{% for %}` pays them, so they carry the "
                      "push order): " + "  ".join(f"{k} {deltas[k]:+.3f}" for k in loops))
                if "C" in deltas:
                    print(f"   no-loop control (this change cannot touch it): "
                          f"C {deltas['C']:+.3f} us")
                print(f"   subject min {min(abs(deltas[k]) for k in subjects):.3f} us"
                      f"   push-order floor (D: one loop, no reads) "
                      f"{deltas.get('D', float('nan')):+.3f} us")
                if all(k in deltas for k in subjects):
                    ks = [1, 2, 4]
                    inter, slope = _fit(ks, [deltas[f"A{k}"] for k in ks])
                    print(f"   per-read: A1/50 = {deltas['A1'] / 50:+.4f} us; "
                          f"slope over k = {slope / 50:+.4f} us/read; "
                          f"intercept {inter:+.3f} us (the include is in both builds)")
            print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--prefix-src", required=True)
    b.add_argument("--fixed-src", required=True)
    b.add_argument("--work", required=True)
    b.set_defaults(fn=cmd_build)
    a = sub.add_parser("ab")
    a.add_argument("--work", required=True)
    a.add_argument("--rounds", type=int, default=12)
    a.set_defaults(fn=cmd_ab)
    r = sub.add_parser("report")
    r.add_argument("--work", required=True)
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
