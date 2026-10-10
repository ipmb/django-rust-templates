"""How much of a page render is the delegated inheritance subtree, in seconds.

**Why this file exists.** The census's third column says the three inheritance
rows return **198.8% / 298.8% / 197.8%** of their output from delegates, and the
sequencing question for the walk's next slice — lazy materialisation (spec §5
step 4) or inheritance (§5 step 5) — turns on which of them costs more.  But
`out D/T` is *characters*, and the other two meters are *counts*.  A byte share
over 100% is a striking number and it is not a time, so a decision taken on it
would be taken on the wrong quantity.  This prices the same delegation in the
unit the report is denominated in.

**Method.** `Act::Delegate` calls `render_annotated` on the materialised Python
node (`crates/template-py/src/lib.rs:3989`), so wrapping that method on the
classes the census names measures exactly the delegated work and nothing else.
A depth stack turns the per-class figure into an **exclusive** one, so a
`BlockNode` rendered inside a delegated `ExtendsNode` is not counted twice — an
inclusive table would read "inheritance is 100% of the render" three times over
and mean nothing.  Python-side `perf_counter` around each call, accumulated
across a timed loop, so the totals are comparable to the loop's own wall time.

The wrapper delegates and records; the bytes are asserted unchanged, so it is an
observer.  `ExtendsNode.render` is *not* in the table by construction — it is
the frame the walk never enters when it delegates, and the class the census
reports is the one `render_annotated` is bound to.

Run:  .venv/bin/python -I .review/m19-measurements/inheritance_share.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

ITERS = 200

ROWS = ("real-world/normal", "real-world/complex", "django-only/realistic")


class Timer:
    """Exclusive wall time for the **top-level** delegated call, per class.

    `render_annotated` is re-entrant — a delegated `ExtendsNode` renders the
    parent, whose `ExtendsNode` and `BlockNode`s are delegated too — so a class's
    inclusive time contains its children's and an inclusive table would read
    "inheritance is 100% of the render" several times over and mean nothing.
    The depth counter records nesting, and only a call at depth 0 is timed; a
    deeper call still increments the counter so the composition is complete.

    **Only depth 0 is timed because the instrument is not free.** The first
    version of this file timed every wrapped call and read `real-world/complex`
    at **565.9 µs** against the bench's **533.5** for the same row on the same
    artefact: 219 wrapped calls (2 `ExtendsNode` + 4 `BlockNode` + 73
    `IncludeNode`, plus re-entries) × two `perf_counter` calls and one Python
    frame each is ~60 µs of observer. A share computed against an inflated
    denominator is a *low* estimate and a composition is a *high* one; one timed
    call per render makes both negligible and the denominator is the bench's.

    Depth 0 is also the right boundary on its own terms: the walk delegates one
    node, and everything under it is Python doing Python work.
    """

    def __init__(self) -> None:
        self.inclusive: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self.top: dict[str, int] = {}
        self.stack = 0

    def wrap(self, cls):
        original = cls.render_annotated
        name = cls.__name__

        def timed(node, context, *args, **kwargs):
            outer = self.stack
            self.stack += 1
            start = time.perf_counter() if outer == 0 else None
            try:
                return original(node, context, *args, **kwargs)
            finally:
                self.stack -= 1
                self.calls[name] = self.calls.get(name, 0) + 1
                if start is not None:
                    self.inclusive[name] = (
                        self.inclusive.get(name, 0.0) + time.perf_counter() - start
                    )
                    self.top[name] = self.top.get(name, 0) + 1

        cls.render_annotated = timed
        return original

    def exclusive(self, name: str) -> float:
        return self.inclusive.get(name, 0.0)


def main() -> None:
    from django_rust_templates.engine import loader_tags

    classes = (loader_tags.ExtendsNode, loader_tags.BlockNode, loader_tags.IncludeNode)

    print(f"iters={ITERS}, rust engine, render mode")
    print("`delegated` is the top-level delegated call's own frame; calls in parens")
    print("is every call of that class, nested ones included\n")
    print(f"{'row':22}  {'render us':>10}  {'delegated us':>12}  {'share':>7}  composition")
    print("-" * 96)

    for key in ROWS:
        workload = next(w for w in bench_corpus.workloads() if w.key == key)
        engine = bench_engines.build(
            "rust", workload.templates, workload.libraries("rust")
        )
        try:
            handle = engine.get(workload.entry)
            context = engine.context(workload.data)
            # Warm the loader cache and materialise everything the delegates need
            # before the clock starts: this is a render-mode measurement.
            plain = engine.render(handle, context)

            timer = Timer()
            originals = [(cls, timer.wrap(cls)) for cls in classes]
            try:
                start = time.perf_counter()
                for _ in range(ITERS):
                    engine.render(handle, context)
                wall = time.perf_counter() - start
            finally:
                for cls, original in originals:
                    cls.render_annotated = original

            wrapped = engine.render(handle, context)
            assert wrapped == plain, (key, "the observer changed the output")
        finally:
            engine.close()

        render_us = wall / ITERS * 1e6
        parts = {name: timer.exclusive(name) / ITERS * 1e6 for name in timer.calls}
        delegated_us = sum(parts.values())
        composition = " ".join(
            f"{n.replace('Node', '')}={parts[n]:.1f}us({timer.calls[n] // ITERS})"
            for n in sorted(parts, key=lambda n: -parts[n])
        ) or "-"
        print(
            f"{key:22}  {render_us:>10.1f}  {delegated_us:>12.1f}"
            f"  {delegated_us / render_us:>6.1%}  {composition}"
        )

    print(
        "\n`delegated us` is what the walk paid to hand the node back; a row whose\n"
        "share is near 1.00 is a row where the walk is a wrapper around Python.\n"
        "A row near 0.00 is one where the walk owns the page and the census's\n"
        "`out deleg` percentage is about where the bytes came from, not the cost.\n"
        "A nested delegate is inside the timed frame, so it is not added again."
    )


if __name__ == "__main__":
    main()
