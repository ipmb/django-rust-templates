#!/usr/bin/env python3
"""Branch against main, counterbalanced, with an interval on the paired difference.

What review round 7 required and the first harness did not provide:

* **The baseline is `main`'s actual binary**, built in a separate worktree with
  its own venv, not this branch with its Python classifier stubbed out. The
  stubbed arm still ran the new Rust dispatch — the negative-cache lookup and
  the first classifier crossing — so it could not price them.
* **Counterbalanced order.** Each repetition runs A/B then B/A, so a monotonic
  drift in machine state cancels instead of loading onto whichever arm always
  went second.
* **An observed range over the paired relative differences**, not the
  baseline's IQR. A median inside the spread of individual baseline samples is
  perfectly compatible with a consistent slowdown, and reporting that as "no
  change" would hide a real cost behind variance.

  **That range is a descriptive statistic, not a confidence interval.** With
  `reps` repetitions it is the span of `reps` observed paired differences --
  at the default of 6 it is simply their minimum and maximum. It says what was
  seen, not what would be seen again, and it must not be read as bounding the
  effect. Raising `reps` widens the evidence but never turns this into an
  interval estimate.

A positive percentage is a slowdown. The verdict column reports the *interval*:
"slower" only when the whole interval is above the tolerance, "faster" only when
it is entirely below zero, and "uncertain" when it straddles — which is a result,
not a failure to get one.

Usage:
    source scripts/env.sh
    python .review/stream-free-host-calls/compare.py [reps]
"""

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

BRANCH = Path(__file__).resolve().parents[2]
MAIN = Path("/tmp/drt-main")
WORKLOAD = ".review/stream-free-host-calls/workload.py"

# What we would accept on templates that gain nothing. Predeclared, per the
# review: deciding the threshold after seeing the numbers is not a threshold.
TOLERANCE = 2.0  # percent

WORKLOADS = [
    "median-8", "p90-22", "max-59", "mixed",
    "all-native", "all-native-stress",
    "tiny-one-host", "all-streamful", "all-streamful-stress",
]


def arm(root, name, runs, debug):
    env = dict(os.environ, PIP_CONFIG_FILE="/dev/null")
    out = subprocess.run(
        [str(root / ".venv/bin/python"), str(root / WORKLOAD), name, str(runs), debug],
        capture_output=True, text=True, cwd=str(root), env=env,
    )
    if out.returncode != 0:
        raise SystemExit(f"{root.name}/{name} failed:\n{out.stderr[-1500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])["median_ms"]


def observed_range(values):
    """The full span of the observed paired differences.

    Literally min and max, so that "every observed repetition agreed on the
    sign" is a statement the number supports. An earlier version trimmed order
    statistics, which happened to equal min/max at 8 repetitions and would
    quietly stop doing so at larger counts -- 29 negatives and one positive
    would still have printed "faster".

    Descriptive, not inferential: it says what was seen, not what bounds the
    effect.
    """
    return min(values), max(values)


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    # The instrument records what it compared, so a result cannot be read
    # against the wrong pair of builds.
    for label, root in (("branch", BRANCH), ("baseline", MAIN)):
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
                              capture_output=True, text=True).stdout.strip()
        version = subprocess.run([str(root / ".venv/bin/python"), "-V"],
                                 capture_output=True, text=True).stdout.strip()
        print(f"{label:>9}: {root}  {head}  {version}")
    mine = (BRANCH / WORKLOAD).read_bytes()
    theirs = (MAIN / WORKLOAD).read_bytes()
    if mine != theirs:
        raise SystemExit(
            f"the two arms would run different workload.py files; copy "
            f"{BRANCH / WORKLOAD} to {MAIN / WORKLOAD} before measuring"
        )
    print(f"{'workload.py':>9}: identical in both arms")
    print()
    if not (MAIN / ".venv/bin/python").exists():
        raise SystemExit(f"no baseline build at {MAIN} — see results.md for setup")

    corpus = sorted(
        f"corpus/{p.stem}" for p in (BRANCH / "scripts/bench_templates").glob("*.html")
    )
    names = WORKLOADS + corpus

    for debug in ("nodebug", "debug"):
        print("=" * 88)
        print(f"branch vs main   engine.debug={debug == 'debug'}   "
              f"{reps} counterbalanced reps   tolerance {TOLERANCE}%")
        print("=" * 88)
        print(f"{'workload':<26} {'main ms':>9} {'branch ms':>10} "
              f"{'change':>9} {'observed range':>18} {'verdict':>11}")
        for name in names:
            diffs, mains, branches = [], [], []
            for rep in range(reps):
                # A/B then B/A: order cancels across the pair.
                if rep % 2 == 0:
                    m = arm(MAIN, name, 9, debug)
                    b = arm(BRANCH, name, 9, debug)
                else:
                    b = arm(BRANCH, name, 9, debug)
                    m = arm(MAIN, name, 9, debug)
                mains.append(m)
                branches.append(b)
                diffs.append((b - m) / m * 100)
            lo, hi = observed_range(diffs)
            med = statistics.median(diffs)
            # "faster"/"slower" mean every observed repetition agreed on the
            # sign; "unresolved" means they did not. Neither is a claim about
            # repetitions not performed.
            if hi < 0:
                verdict = "faster"
            elif lo > TOLERANCE:
                verdict = "SLOWER"
            elif lo > 0:
                verdict = "small cost"
            else:
                verdict = "unresolved"
            print(f"{name:<26} {statistics.median(mains):>9.4f} "
                  f"{statistics.median(branches):>10.4f} {med:>+8.1f}% "
                  f"{lo:>+7.1f}..{hi:<+7.1f} {verdict:>11}")
        print()


if __name__ == "__main__":
    main()
