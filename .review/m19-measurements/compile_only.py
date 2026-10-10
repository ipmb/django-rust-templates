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
"""

import sys
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

SOURCES = [
    ("micro/text", "<p>plain text, no variables at all</p>"),
    ("micro/variable", "<p>{{ title }}</p>"),
    ("micro/if", "{% if flag %}<b>on</b>{% else %}<i>off</i>{% endif %}"),
    ("micro/filter", "{{ title|upper }}"),
    ("micro/loop-text", "{% for r in rows %}<tr><td>x</td></tr>{% endfor %}"),
]

ITERS = 200
REPEATS = 7


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
    print(f"compile only: from_string(source), iters={ITERS}, {REPEATS} repeats, best-of")
    print(f"{'row':18} {'ours us':>9} {'stock us':>9} {'ours/stock':>11}")
    for label, source in SOURCES:
        ours = compile_us("rust", source)
        stock = compile_us("django", source)
        print(f"{label:18} {ours:9.2f} {stock:9.2f} {ours / stock:11.3f}")


if __name__ == "__main__":
    main()
