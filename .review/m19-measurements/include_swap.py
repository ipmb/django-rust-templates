"""Is `real-world/complex`'s jinja2 gap the include path? Swap the include out.

**The question.**  `real-world/complex` renders in 523.18 us against jinja2's
341.65 (**1.53x**, the largest render loss on the board, +181.5 us).  The
inheritance frames are not it — `extends_frame_cost.py` prices
`ExtendsNode.render`'s own lines at 10.53 us of 563.9 and `block_walk_cost.py`
the per-render block walk at 3.22 us.  What `complex` has that no other row has
is **73 `IncludeNode`s per render** (12 sections x 6 lines, plus a footer), and
`include_cost.py` prices the include frames' own lines at 186.64 us — but that
instrument's observer sits *inside* the timed frame, so it is a high estimate
and neither number is clean enough to act on.

**The method that needs no observer.**  Change one construct in the page and
render both engines.  `partials/line.html` is a 7-node template included once per
inner iteration; replacing the `{% include "partials/line.html" %}` tag with that
template's own text leaves the page's **output bytes identical** — the include
renders with `line` bound, and the inline copy is inside the same `{% for line %}`
— and removes all 72 inner includes.  The footer include stays, so the row still
exercises the include path; only the per-inner-iteration ones go.

The byte-identity is asserted, not assumed: the two variants' renders are hashed
and the probe exits non-zero on a mismatch.  A row whose `sha256` differs is
measuring two different pages and the timings are void.

Both engines render both variants, so the number is a *delta of deltas*: ours
minus jinja2's.  If the include path is the gap, ours falls by ~180 us and
jinja2's by much less, and the ratio moves toward parity.

Run:  .venv/bin/python -I <this>
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

ITERS = 200
REPEATS = 7


def best(fn) -> float:
    for _ in range(max(3, ITERS // 4)):
        fn()
    out = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        for _ in range(ITERS):
            fn()
        out.append((time.perf_counter() - start) / ITERS)
    return min(out) * 1e6


def main() -> int:
    workload = next(w for w in bench_corpus.workloads() if w.key == "real-world/complex")
    templates = dict(workload.templates)
    line = templates.get("partials/line.html")
    if line is None:
        print("! partials/line.html is not in the complex workload; this probe is stale")
        return 2
    inline = {
        name: body.replace('{% include "partials/line.html" %}', line)
        for name, body in templates.items()
    }
    if inline["page.html"] == templates["page.html"]:
        print("! the include tag did not match; the swap did not happen")
        return 2

    print(f"iters={ITERS} repeats={REPEATS}, django 6.0.9 pin, best-of-{REPEATS}")
    print(f"swapped out: {line!r}")
    print()
    print(f"{'engine':8} {'variant':12} {'us':>9} {'sha256':>14} {'vs jinja2':>10}")
    print("-" * 60)

    results: dict[tuple[str, str], tuple[float, str]] = {}
    for variant, tpl in (("include", templates), ("inlined", inline)):
        for engine_name in ("rust", "jinja2"):
            engine = bench_engines.build(
                engine_name, tpl, workload.libraries(engine_name)
            )
            try:
                handle = engine.get(workload.entry)
                context = engine.context(workload.data)
                us = best(lambda: engine.render(handle, context))
                out = engine.render(handle, context)
                digest = hashlib.sha256(out.encode()).hexdigest()[:12]
            finally:
                engine.close()
            results[(variant, engine_name)] = (us, digest)

    bad = False
    for variant in ("include", "inlined"):
        ours = results[(variant, "rust")]
        theirs = results[(variant, "jinja2")]
        if ours[1] != theirs[1]:
            print(f"! {variant}: rust and jinja2 disagree ({ours[1]} vs {theirs[1]})")
            bad = True
    if results[("include", "rust")][1] != results[("inlined", "rust")][1]:
        print(
            "! the swap changed the output: "
            f"{results[('include', 'rust')][1]} vs {results[('inlined', 'rust')][1]}"
        )
        bad = True

    for variant in ("include", "inlined"):
        for engine_name in ("rust", "jinja2"):
            us, digest = results[(variant, engine_name)]
            ratio = us / results[(variant, "jinja2")][0]
            print(f"{engine_name:8} {variant:12} {us:>9.1f} {digest:>14} {ratio:>10.2f}")

    if bad:
        print("\nVOID: the two variants are not the same page.")
        return 1

    ours_delta = results[("inlined", "rust")][0] - results[("include", "rust")][0]
    theirs_delta = results[("inlined", "jinja2")][0] - results[("include", "jinja2")][0]
    print(
        f"\nremoving the 72 inner includes: ours {ours_delta:+.1f} us, "
        f"jinja2 {theirs_delta:+.1f} us -> ours pays {theirs_delta - ours_delta:+.1f} us "
        "more than jinja2 for the include path"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
