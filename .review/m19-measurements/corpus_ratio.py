#!/usr/bin/env python
"""The corpus rows of the price A/B, as the page prints them: ours / stock.

`frame_rewrite_ab.py report` prints each side's own microseconds, which is the
right unit for a per-read cost and the wrong one for a headline: the page's
numbers are ratios against stock, and stock is in the same runs. This prints
both, per row and per mode, from the same JSON the report reads — no second
measurement, no re-run.

Usage:  .venv/bin/python -I corpus_ratio.py --work W [--work W2 ...]

With more than one `--work` it prints each work dir's table in turn, so two runs
of the same pair can be compared line by line.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics

ROWS = [
    "django-only/realistic",
    "real-world/complex",
    "django-only/filter-arg",
    "django-only/i18n",
    "real-world/normal",
    "real-world/large",
]
MODES = ("render", "parse", "compile")


def load(work: pathlib.Path, build: str, key: str, mode: str, engine: str) -> list[float]:
    vals = []
    for p in sorted(work.glob(f"corpus-{build}-r*.json")):
        for r in json.loads(p.read_text())["records"]:
            if (r["key"], r["mode"], r["engine"]) == (key, mode, engine):
                vals.append(r["best_us"])
    return vals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", action="append", required=True)
    args = ap.parse_args()
    for w in args.work:
        work = pathlib.Path(w)
        print(f"########## {w}")
        for mode in MODES:
            print(f"--- {mode}: ours/stock, 12 runs a side, medians")
            print(f"{'row':26}{'prefix ours':>12}{'prefix stock':>13}{'prefix x':>10}"
                  f"{'fixed ours':>12}{'fixed stock':>12}{'fixed x':>9}{'rel':>9}")
            for key in ROWS:
                po = statistics.median(load(work, "prefix", key, mode, "rust"))
                ps = statistics.median(load(work, "prefix", key, mode, "django"))
                fo = statistics.median(load(work, "fixed", key, mode, "rust"))
                fs = statistics.median(load(work, "fixed", key, mode, "django"))
                px, fx = po / ps, fo / fs
                print(f"{key:26}{po:>12.2f}{ps:>13.2f}{px:>10.3f}"
                      f"{fo:>12.2f}{fs:>12.2f}{fx:>9.3f}{fx / px - 1:>+9.2%}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
