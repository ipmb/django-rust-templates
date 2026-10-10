"""What compiling alone costs, ours against stock — the term `parse` mode hides.

`scripts/bench.py`'s `parse` mode calls `from_string` on every iteration and so
times **compile + render** as one number.  Step 3a moved render on `micro/if` by
−22% and `micro/filter` by −18% while their `parse` rows moved +2.8% and (nothing
outside the band): the explanation offered in `.review/m19-measurements/step3a.md`
is that the compile path, not the render, owns those rows' `parse` number.  This
probe measures that term directly instead of asserting it — the same sources
`bench_corpus.py` uses, timed as `from_string(source)` with no render at all.

The three shapes are chosen to separate the account's two halves:

  * `micro/text`, `micro/variable` — sources with nothing for the walk to plan,
    so ours/stock here is the *baseline* compile gap with no new machinery in it.
  * `micro/if`, `micro/filter` — the two rows step 3a moved.  If the account
    holds, the gap on these should sit near the baseline rows' and be of the order
    of the row's whole `parse` budget.
  * `micro/loop-text` — a body that now seals, i.e. a source where the compile
    path is genuinely ours (the plan is built in Rust) and stock's is CPython
    (NodeList construction).

`compile only` is a printed claim about the instrument: the call timed builds a
fresh `Template` every iteration, and neither engine caches `from_string` (our
`Engine.from_string` is `Template(template_code, engine=self)` with no cache in
front).  The scatter of the ratio is measured the way the record's is — this file
is run twice and the two ratio columns differenced — because a ratio of two
timings owes its own band before anything is called a gap.

Run:  .venv/bin/python -I .review/m19-measurements/compile_only.py
      .venv/bin/python -I .review/m19-measurements/compile_only.py \
          --corpus real-world/normal,real-world/complex,django-only/realistic

**`--corpus KEY[,KEY]` adds rows from `bench_corpus` itself** (the row's entry
template, the same source `bench.py` compiles), because the micro rows are not
where a *later* slice's compile question gets asked: step 5's claim is about
`real-world/normal`, and a claim about a corpus row has to be measured on that
row's source rather than on a micro row's.  The rows are named on the command
line rather than defaulted, so a run says which population it covered.

**`scripts/bench.py` has a `compile` mode now, and this probe is what it was
checked against.**  The report used to carry `render` and `parse` and nothing
that could show a per-compile cost, so step 5's `+4.6%…+11.7%` was measured here
and reported from a record — an instrument off the gate's path, which is where a
claim goes to stop being checkable.  The mode is in the report as of this file's
last edit; the probe stays because a second implementation of a definition is the
only thing that can catch a definition that is wrong (here it also runs a
*different* iteration count — 200 pinned against the mode's calibrated-per-row).
The two land in the same place on all eight shared rows, with a bound rather than
the word "agree": the `rust` cell within 2.13% of this probe's two-run span, the
`stock` cell within 2.50%, the ratio within 3.48% — six ratios below the span,
the two real-world ones 0.55% and 1.02% above, and the µs columns with no
consistent sign.  The gap is unattributed; the two also differ in warmup
(`max(3, iters // 4)` there against this file's own).  `real-world/normal` is the
row to look at: 166.36 there against 166.12–167.50 here.
"""

import sys
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402
from bench import native_artifact  # noqa: E402

SOURCES = [
    ("micro/text", "<p>plain text, no variables at all</p>"),
    ("micro/variable", "<p>{{ title }}</p>"),
    ("micro/if", "{% if flag %}<b>on</b>{% else %}<i>off</i>{% endif %}"),
    ("micro/filter", "{{ title|upper }}"),
    ("micro/loop-text", "{% for r in rows %}<tr><td>x</td></tr>{% endfor %}"),
]


def corpus_rows(keys):
    """`(key, entry source)` for corpus rows named on the command line.

    A key that no workload carries is a loud error rather than a missing row:
    the whole point of naming the population is that the reader can check it
    was the one they meant.
    """
    by_key = {w.key: w for w in bench_corpus.workloads()}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise SystemExit(
            f"no such corpus row: {unknown}; known: {sorted(by_key)}"
        )
    return [(k, by_key[k].templates[by_key[k].entry]) for k in keys]


ITERS = 200
REPEATS = 7
unbuilt = "—"


def compile_us(engine_name, source):
    engine = bench_engines.build(engine_name, {"page.html": source}, {})
    try:
        for _ in range(10):
            engine.from_string(source)
        times = timeit.repeat(lambda: engine.from_string(source), number=ITERS, repeat=REPEATS)
        return min(times) / ITERS * 1e6
    finally:
        engine.close()


def main() -> None:
    keys = []
    argv = sys.argv[1:]
    while argv:
        arg = argv.pop(0)
        if arg == "--corpus":
            keys = [k.strip() for k in argv.pop(0).split(",") if k.strip()]
        else:
            raise SystemExit(f"unknown argument: {arg}")
    rows = SOURCES + corpus_rows(keys)
    print(f"compile only: from_string(source), iters={ITERS}, {REPEATS} repeats, best-of")
    print(f"artifact: {native_artifact()}")
    print(f"{'row':18} {'ours us':>9} {'stock us':>9} {'ours/stock':>11}")
    skipped = []
    for label, source in rows:
        try:
            ours = compile_us("rust", source)
            stock = compile_us("django", source)
        except Exception as exc:  # a row that cannot be built here is reported, not dropped
            skipped.append((label, f"{type(exc).__name__}: {exc}".splitlines()[0]))
            print(f"{label:18} {unbuilt:>9} {unbuilt:>9} {unbuilt:>11}")
            continue
        print(f"{label:18} {ours:9.2f} {stock:9.2f} {ours / stock:11.3f}")
    for label, why in skipped:
        print(f"NOT MEASURED  {label}: {why}")


if __name__ == "__main__":
    main()
