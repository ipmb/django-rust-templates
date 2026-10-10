"""The ceiling for the inheritance slice, measured before it is written.

**The question.** `.review/m19-measurements/inheritance_share.py` prices the
delegation: **99.0% / 99.3% / 99.6%** of the render on `real-world/normal`,
`real-world/complex` and `django-only/realistic` happens inside the one delegated
`ExtendsNode` frame.  That is a share of a cost, not a prediction of a saving —
the walk still has to render the same page.  This file measures what the page
costs when it already *is* walk-rendered, which is the ceiling the slice can
reach and therefore the number the prediction has to be stated against.

**How the ceiling is built, and what defends it.**  `real-world/normal` is
`{% extends %}` plus two `{% block %}`s, and `node_census.flatten_inheritance`
splices exactly those away by source substitution — so the flattened page has the
same 50-row loop, the same filters, the same include, and renders to **the same
bytes**, which that module asserts on three renders before this probe runs.  The
census confirms the splice worked from the other side: `flat/normal` handles
`ForNode` and `IfNode` and delegates only its `IncludeNode` (0.7% of the output),
where the shipped page delegates `BlockNode=2 ExtendsNode=1` (198.8%).

So the pair is one page in two spellings, and the gap between them is what the
inheritance delegation costs.  **The ceiling is not the prediction and the
difference matters**: `flat/normal` has no `{% extends %}` at all, so it pays no
`ExtendsNode` frame, no block-context push and no `BlockNode` frame.  A walk that
owns the block *bodies* while leaving `ExtendsNode` on the Python side keeps one
frame per page and the block-context dance, so it lands above the ceiling.  Both
numbers are printed so the prediction can be stated as an interval on the right
side of the ceiling.

**The instrument is `bench.py`'s own.**  `measure` is imported rather than
re-implemented — best-of-N over repeats, iterations calibrated on the control and
pinned across engines — so these microseconds are the report's estimator and not
a second instrument wearing its name.  The artifact's sha256 is printed because a
timing of a compiled extension is a timing of a *build*.

Run:  .venv/bin/python -I .review/m19-measurements/step5_ceiling.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
# `node_census` lives beside this file, not under `scripts/`, because it is a
# record rather than a shipped instrument.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench  # noqa: E402
import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402
import node_census  # noqa: E402

REPEATS = 7
TARGET = 0.05


def time_row(label, engine_name, templates, workload):
    engine = bench_engines.build(
        engine_name, templates, workload.libraries(engine_name)
    )
    try:
        handle = engine.get(workload.entry)
        context = engine.context(workload.data)
        best, worst, iters, _ = bench.measure(
            engine,
            workload,
            handle,
            bench.RENDER,
            context,
            REPEATS,
            TARGET,
        )
    finally:
        engine.close()
    return label, best, worst, iters


def main() -> None:
    from django_rust_templates import _native

    artifact = Path(_native.__file__)
    print(
        f"repeats={REPEATS}, target={TARGET}s, artifact sha256:"
        f"{hashlib.sha256(artifact.read_bytes()).hexdigest()[:12]}"
    )
    print("render mode: one handle, compiled outside the loop\n")

    normal = next(w for w in bench_corpus.workloads() if w.key == "real-world/normal")
    flat = dict(normal.templates, **{"page.html": node_census.FLAT_NORMAL})

    print(f"{'row':34}  {'best us':>9}  {'worst us':>9}  {'iters':>7}  {'vs stock':>9}")
    print("-" * 76)
    rows = [
        ("real-world/normal  stock", "django", normal.templates, normal),
        ("real-world/normal  ours (shipped)", "rust", normal.templates, normal),
        ("flat/normal        ours (ceiling)", "rust", flat, normal),
    ]
    stock = None
    for label, engine_name, templates, workload in rows:
        label, best, worst, iters = time_row(label, engine_name, templates, workload)
        if stock is None:
            stock = best
        print(f"{label:34}  {best:>9.2f}  {worst:>9.2f}  {iters:>7}  {best / stock:>8.3f}x")

    micro = next(w for w in bench_corpus.workloads() if w.key == "micro/extends")
    print()
    stock = None
    for engine_name in ("django", "rust"):
        label, best, worst, iters = time_row(
            f"micro/extends {engine_name}", engine_name, micro.templates, micro
        )
        if stock is None:
            stock = best
        print(f"{label:34}  {best:>9.2f}  {worst:>9.2f}  {iters:>7}  {best / stock:>8.3f}x")

    print(
        "\nThe ceiling row renders the same bytes with no `{% extends %}` and no\n"
        "`{% block %}` in the source at all, so it is the floor of what the\n"
        "inheritance delegation costs on this page — a slice that owned the block\n"
        "bodies but not `ExtendsNode` would still pay one Python frame and the\n"
        "block-context push, and would land above it."
    )


if __name__ == "__main__":
    main()
