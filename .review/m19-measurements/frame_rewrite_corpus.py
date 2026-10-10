#!/usr/bin/env python
"""One side of a focused, alternating A/B on the rows the frame-rewrite gate
can and cannot act on.

`scripts/bench.py` runs all 18 workloads, which is ~170 s and cannot be repeated
enough times to resolve a 1-2% cell.  This runs the six rows that matter, with a
higher repeat count, so the driver beside it can alternate the two artifacts many
times and take a median.  It is `bench.run`'s own `measure`, with the same
`pin_iters` discipline (the iteration count comes from one calibration on the
control engine and is handed to every engine, so a faster engine does not get a
tighter best-of-N), and nothing here re-implements a timer.

Run:  .venv/bin/python -I <this> --json OUT.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import bench  # noqa: E402
import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

# The subjects are the two rows whose sealing loop holds a delegate **and** a
# walk-owned read of the loop variable, so `may_write_context` fires and `rewrite`
# is skipped.  The controls are the three shapes that keep one of those two
# halves and must not move: `filter-arg` reads the loop variable with no delegate
# (the rewrite must still run), `i18n` holds a delegate with no walk-owned read
# (the predicate fires and has nothing to skip), and `large` has no loop at all.
# `normal`'s one `{% include %}` is at block level, outside the loop.
ROWS = (
    "django-only/realistic",
    "real-world/complex",
    "django-only/filter-arg",
    "django-only/i18n",
    "real-world/normal",
    "real-world/large",
)
ENGINES = ("rust", "django")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--repeats", type=int, default=9)
    ap.add_argument("--target", type=float, default=0.05)
    args = ap.parse_args()

    wanted_rows = set(ROWS)
    records = []
    for workload in bench_corpus.workloads():
        if workload.key not in wanted_rows:
            continue
        wanted = [e for e in workload.engines if e in ENGINES]
        built: dict = {}
        try:
            for name in wanted:
                built[name] = bench_engines.build(
                    name, workload.templates, workload.libraries(name)
                )
            contexts = {name: built[name].context(workload.data) for name in wanted}
            pins = {}
            if bench_engines.CONTROL in built:
                for mode in bench.MODES:
                    pins[mode] = bench.calibrate(
                        built[bench_engines.CONTROL],
                        workload,
                        built[bench_engines.CONTROL].get(workload.entry),
                        mode,
                        contexts[bench_engines.CONTROL],
                        args.target,
                    )
            for engine_name in wanted:
                engine = built[engine_name]
                for mode in bench.MODES:
                    handle = engine.get(workload.entry)
                    best, worst, iters, out = bench.measure(
                        engine,
                        workload,
                        handle,
                        mode,
                        contexts[engine_name],
                        args.repeats,
                        args.target,
                        pin_iters=pins.get(mode),
                    )
                    records.append(
                        {
                            "key": workload.key,
                            "mode": mode,
                            "engine": engine_name,
                            "best_us": best,
                            "worst_us": worst,
                            "iters": iters,
                        }
                    )
        finally:
            for engine in built.values():
                engine.close()

    native = pathlib.Path(
        __import__("django_rust_templates._native", fromlist=["x"]).__file__
    ).resolve()
    import hashlib

    payload = {
        "native": f"sha256:{hashlib.sha256(native.read_bytes()).hexdigest()} "
        f"({native.stat().st_size} bytes) {native}",
        "repeats": args.repeats,
        "records": records,
    }
    pathlib.Path(args.json).write_text(json.dumps(payload, indent=1))
    print(f"wrote {args.json}: {payload['native'].split()[0]}, {len(records)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
