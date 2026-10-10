#!/usr/bin/env python
"""The four-template family, timed under *both* builds.

The differencing design (`frame_rewrite_cost.py`) put the fix's cost inside one
build, as `((A - C) - (B - D)) / N_ROWS`.  It is unstable: two runs of it on the
same artifact returned **+0.042** and **+0.295** us/read, a factor of seven, on
templates whose stated-loop half drifted 6% between the runs while the frame half
held to 0.2%.  A difference of two ~140 us numbers, one of which moves, is not an
instrument.

This puts the same family under the two artifacts instead.  `A{k}` is
`{% for r in rows %}` + k copies of `{{ r.name }}` + one `{% include %}`, so the
loop seals and `may_write_context` fires and the read stays on the context; `B{k}`
is the same without the include, so the read goes to the frame; `C` is the include
alone; `D` is the empty loop.  The fix can act on `A{k}` only, and its cost there
is `A{k}(fixed) - A{k}(prefix)` — **a cross-build delta of one template, with no
subtraction of two baselines anywhere**, and with `B`, `C` and `D` as controls
drawn from the same family, timed in the same runs, so the floor is measured on
the same population it bounds.

Three things this buys that the earlier design could not state:

  * the slope of the `A{k}` deltas over `k` is the per-read cost with the include
    cancelling as a *shared* term of two builds rather than of two intercepts;
  * `A1`'s delta over 1 read is the same number a second way;
  * `B`, `C` and `D` deltas should all be zero — if `B` moves, the frame route
    moved, and the difference is not the gate.

Run:  .venv/bin/python -I <this> --json OUT.json [--pins PINS.json | --calibrate]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import bench  # noqa: E402
import bench_engines  # noqa: E402

N_ROWS = 50
ROWS = [{"name": f"row{i}"} for i in range(N_ROWS)]


def _loop(reads: int, include: bool) -> str:
    return (
        "{% for r in rows %}"
        + "{{ r.name }}" * reads
        + ("{% include 'inc.html' %}" if include else "")
        + "{% endfor %}"
    )


# `A` = the subject (the gate acts); `B`, `C`, `D` = the controls.
SOURCES = {
    **{f"A{k}": _loop(k, True) for k in (1, 2, 4)},
    **{f"B{k}": _loop(k, False) for k in (1, 2, 4)},
    "C": "{% for r in rows %}{% include 'inc.html' %}{% endfor %}",
    "D": "{% for r in rows %}{% endfor %}",
}
SUBJECT = tuple(f"A{k}" for k in (1, 2, 4))
CONTROL = tuple(f"B{k}" for k in (1, 2, 4)) + ("C", "D")
LABELS = (*SUBJECT, *CONTROL)
MODES = ("render", "parse", "compile")
ENGINES = ("rust", "django")


def workload(label: str):
    return SimpleNamespace(
        key=f"family/{label}",
        templates={"inc.html": "x", **{f"{n}.html": s for n, s in SOURCES.items()}},
        entry=f"{label}.html",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--pins")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--target", type=float, default=0.04)
    args = ap.parse_args()
    if not args.calibrate and not args.json:
        ap.error("--json is required unless --calibrate")

    built = {n: bench_engines.build(n, {"inc.html": "x", **{f"{k}.html": s for k, s in SOURCES.items()}}, {})
             for n in ENGINES}
    contexts = {n: built[n].context({"rows": ROWS}) for n in ENGINES}

    # The iteration count is calibrated once and pinned for every run, on both
    # builds: `bench.measure`'s own rule is that a faster artifact must not be
    # handed a larger N and therefore a tighter best-of-N.
    if args.calibrate:
        pins = {}
        for label in LABELS:
            w = workload(label)
            for mode in MODES:
                pins[f"{label}/{mode}"] = bench.calibrate(
                    built["rust"], w, built["rust"].get(w.entry), mode, contexts["rust"], args.target
                )
        pathlib.Path(args.pins).write_text(json.dumps(pins, indent=1))
        print(f"wrote {args.pins}: {len(pins)} pins")
        for n, e in built.items():
            e.close()
        return 0

    pins = json.loads(pathlib.Path(args.pins).read_text())
    records = []
    for label in LABELS:
        w = workload(label)
        for mode in MODES:
            for name in ENGINES:
                handle = built[name].get(w.entry)
                best, worst, iters, out = bench.measure(
                    built[name], w, handle, mode, contexts[name],
                    args.repeats, args.target, pin_iters=pins[f"{label}/{mode}"],
                )
                records.append(
                    {"key": w.key, "mode": mode, "engine": name,
                     "best_us": best, "worst_us": worst, "iters": iters,
                     "bytes": len(out)}
                )
    # One invariant across modes and engines: every (label, mode) renders the same
    # bytes. The family is three shapes of the same arithmetic, so a template that
    # renders something else is not measuring what the label says.
    seen = {}
    for r in records:
        seen.setdefault(r["key"], set()).add(r["bytes"])
    bad = {k: v for k, v in seen.items() if len(v) > 1}
    for n, e in built.items():
        e.close()
    if bad:
        print(f"VOID: labels whose modes render different lengths: {bad}", file=sys.stderr)
        return 1

    # Resolved the way the code resolves it — through the package, not by globbing
    # for a `.so` — because a stale `_native/` orphan beside the live editable
    # install is exactly what a bare `import _native` finds instead.
    import django_rust_templates._native as native_mod

    native = pathlib.Path(native_mod.__file__).resolve()
    payload = {
        "native": f"sha256:{hashlib.sha256(native.read_bytes()).hexdigest()} "
                  f"({native.stat().st_size} bytes)",
        "repeats": args.repeats,
        "records": records,
    }
    pathlib.Path(args.json).write_text(json.dumps(payload, indent=1))
    print(f"wrote {args.json}: {payload['native'].split()[0]}, {len(records)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
