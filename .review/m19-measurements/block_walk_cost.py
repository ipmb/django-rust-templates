"""Price the per-render `get_nodes_by_type(BlockNode)` walk (step 5 slice 3).

**The claim being priced.**  `loader_tags.py:152-155` (the pin's line numbers)
walks the *whole* parent nodelist on every render of an inherited page, to
collect the root template's blocks into the block context:

    blocks = {n.name: n for n in compiled_parent.nodelist.get_nodes_by_type(BlockNode)}

`ExtendsNode.__init__` pays the same walk once per *parse* (`loader_tags.py:96`),
and slice 1 already established that a compile-time descent pass
(`plan_block_bodies`) visits the same tree.  So the question this probe answers
is whether the per-render copy is worth caching, and it answers it in the two
quantities a cache would remove: **how many node visits** it makes per render,
and **how many microseconds** that is.

**Method.**  `NodeList.get_nodes_by_type` is the entry point and `Node`'s the
recursion, so wrapping both counts a walk exactly.  A depth counter times only
the outermost `NodeList` call, which is the whole walk *including* its recursion
and *excluding* the render — the number a cache would delete.  Parse-time calls
are attributed separately from render-time ones, because `ExtendsNode.__init__`
is a parse cost and this slice's other bullet is about the compile floor:
folding the two together would answer neither question.

The observer asserts the rendered bytes are unchanged, so it cannot be measuring
a different page than the one the bench times.

Run:  .venv/bin/python -I <this>
"""

from __future__ import annotations

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

ROWS = (
    "real-world/normal",
    "real-world/complex",
    "real-world/large",
    "django-only/realistic",
    "micro/extends",
    "flat/normal",
)


class Counter:
    """Node visits, walk calls and outermost-walk time, split by phase."""

    def __init__(self) -> None:
        self.phase = "compile"
        self.depth = 0
        self.counts: dict[str, dict[str, float]] = {}

    def _bump(self, phase: str, key: str, amount: float = 1.0) -> None:
        row = self.counts.setdefault(phase, {})
        row[key] = row.get(key, 0.0) + amount

    def wrap(self, cls, name: str, is_entry: bool):
        original = cls.get_nodes_by_type

        def observed(node, nodetype, *args, **kwargs):
            outer = self.depth
            self.depth += 1
            start = time.perf_counter() if (is_entry and outer == 0) else None
            try:
                return original(node, nodetype, *args, **kwargs)
            finally:
                self.depth -= 1
                self._bump(self.phase, name)
                if start is not None:
                    self._bump(
                        self.phase, "walk_us", (time.perf_counter() - start) * 1e6
                    )

        cls.get_nodes_by_type = observed
        return original


def main() -> None:
    from django_rust_templates.engine import base as engine_base

    print(f"iters={ITERS}, rust engine, render mode")
    print(
        "`walk us` is the per-render cost of the *whole* recursive\n"
        "get_nodes_by_type(BlockNode) call, outermost frame inclusive of its\n"
        "recursion and exclusive of the render; `nodes` is how many node visits\n"
        "that walk made, which is the quantity a compile-time cache removes.\n"
    )
    header = (
        f"{'row':22} {'render us':>9} {'walk us':>8} {'share':>6} "
        f"{'walks/render':>12} {'nodes/render':>12} {'parse walks':>11} {'parse nodes':>11}"
    )
    print(header)
    print("-" * len(header))

    for key in ROWS:
        workload = next(w for w in bench_corpus.workloads() if w.key == key)
        engine = bench_engines.build("rust", workload.templates, workload.libraries("rust"))
        counter = Counter()
        originals = [
            (engine_base.NodeList, counter.wrap(engine_base.NodeList, "list_calls", True)),
            (engine_base.Node, counter.wrap(engine_base.Node, "node_visits", False)),
        ]
        try:
            counter.phase = "compile"
            handle = engine.get(workload.entry)
            context = engine.context(workload.data)
            plain = engine.render(handle, context)

            counter.phase = "render"
            start = time.perf_counter()
            for _ in range(ITERS):
                engine.render(handle, context)
            wall = (time.perf_counter() - start) / ITERS * 1e6

            observed = engine.render(handle, context)
            assert observed == plain, (key, "the observer changed the output")
        finally:
            for cls, original in originals:
                cls.get_nodes_by_type = original
            engine.close()

        render = counter.counts.get("render", {})
        compile_ = counter.counts.get("compile", {})
        walk_us = render.get("walk_us", 0.0) / ITERS
        list_calls = render.get("list_calls", 0.0) / ITERS
        nodes = render.get("node_visits", 0.0) / ITERS
        parse_walks = compile_.get("list_calls", 0.0)
        parse_nodes = compile_.get("node_visits", 0.0)
        share = walk_us / wall if wall else 0.0
        print(
            f"{key:22} {wall:>9.1f} {walk_us:>8.2f} {share:>6.2%} "
            f"{list_calls:>11.2f} {nodes:>12.1f} {parse_walks:>11.0f} {parse_nodes:>11.0f}"
        )

    print(
        "\nA row with `walk/render` 0.00 makes no per-render call: no `{% extends %}`\n"
        "in the source, so nothing reaches loader_tags.py's render-time arm. Those\n"
        "rows are the control — the observer's own cost is bounded by their totals,\n"
        "which must stay 0."
    )


if __name__ == "__main__":
    main()
