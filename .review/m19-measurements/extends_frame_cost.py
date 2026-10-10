"""Price `ExtendsNode.render`'s own frame — step 5 slice 3's bullet 1.

**The claim being priced.**  Slice 1's §3 says `ExtendsNode.render` must stop
being a delegate, because `out deleg` can only fall on `normal` once the frame
that returns the whole page is Rust's.  `inheritance_share.py` measures that
frame at **97.4%** of `real-world/normal`'s wall time — but *inclusive* of the
`compiled_parent._render(context)` it calls, which is the walk re-entering.  So
that number is not the prize; it is the prize plus everything the walk already
does.  This probe isolates the prize.

**Why the first version of this file was wrong, and what replaced it.**  It
computed `own = inclusive(ExtendsNode.render) - inclusive(Template._render while
inside one)`.  That subtraction does not close: on `real-world/complex`, which
has two nested `ExtendsNode`s, the outer frame's nested `_render` **contains the
inner `ExtendsNode` frame**, so the subtracted term contains part of the term it
is subtracted from and the result came out at **-237.58 µs** — a negative time,
which is the subtraction telling on itself.  The replacement times the frame
*directly*: `Template._render` **pauses** the innermost open `ExtendsNode` frame
on entry and resumes it on exit, so the frame accumulates exactly the time it
spends on its own lines.  No term is derived from another.

**What "its own lines" is, and what Rust would still pay.**  Under
`compiled_parent._render(context)` the walk keeps the work.  Above it the frame
runs four things, and Rust taking the frame over would still call three of them
because they are Python methods on Python objects: `get_parent` ->
`find_template` (the loader lookup with its skip-history), the
`for node in compiled_parent.nodelist` loop — whose expensive arm is the
`get_nodes_by_type(BlockNode)` walk that `block_walk_cost.py` prices, and the one
term this slice can *delete* — `BlockContext.add_blocks`, and `push_state`.
So `own us` is the ceiling on bullet 1, and only the `blockwalk` part of it is
removable rather than movable.

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
    "micro/extends",
    "django-only/realistic",
    "real-world/large",
)


class Own:
    """Own-frame time for `ExtendsNode.render`, by pausing on nested renders.

    A stack of open frames, each `[own_seconds, resumed_at_or_None, pause_n]`.
    Entering a `Template._render` pauses the innermost open frame and leaving it
    resumes; `pause_n` is **per frame**, and that is the correction a first
    version of this file needed.  A *global* pause depth does not survive
    nesting: `real-world/complex`'s outer frame is paused for the whole of its
    `compiled_parent._render`, so a depth counter already at 1 makes the inner
    frame's own pause a no-op and the inner frame then reads its parent's entire
    render as its own — measured at **100.17%** of the row's wall time, which is
    the tell.  With the counter on the frame, each frame pauses and resumes on
    its own boundaries and a paused frame stays paused through any number of
    nested renders.
    """

    def __init__(self) -> None:
        self.stack: list[list] = []
        self.own_us = 0.0
        self.inclusive_us = 0.0
        self.calls = 0

    def enter(self) -> None:
        self.stack.append([0.0, time.perf_counter(), 0])
        self.calls += 1

    def exit(self) -> None:
        own, started, _ = self.stack.pop()
        if started is not None:
            own += (time.perf_counter() - started) * 1e6
        self.own_us += own

    def pause(self) -> None:
        if not self.stack:
            return
        frame = self.stack[-1]
        frame[2] += 1
        if frame[2] > 1 or frame[1] is None:
            return
        frame[0] += (time.perf_counter() - frame[1]) * 1e6
        frame[1] = None

    def resume(self) -> None:
        if not self.stack:
            return
        frame = self.stack[-1]
        frame[2] -= 1
        if frame[2] > 0 or frame[1] is not None:
            return
        frame[1] = time.perf_counter()

    def wrap_ext(self, cls):
        original = cls.render

        def observed(node, context, *args, **kwargs):
            start = time.perf_counter()
            self.enter()
            try:
                return original(node, context, *args, **kwargs)
            finally:
                self.exit()
                self.inclusive_us += (time.perf_counter() - start) * 1e6

        cls.render = observed
        return original

    def wrap_render(self, cls):
        """`Template._render` everywhere: the walk, and every include.

        The top-level one is a no-op for this instrument — the stack is empty
        when `Template.render` calls it — so no depth guard is needed, and every
        render nested under an `ExtendsNode` pauses that frame whether it came
        from the extends line or from a delegated `IncludeNode`.
        """
        original = cls._render

        def observed(node, context, *args, **kwargs):
            self.pause()
            try:
                return original(node, context, *args, **kwargs)
            finally:
                self.resume()

        cls._render = observed
        return original


def main() -> None:
    from django_rust_templates.engine import base as engine_base
    from django_rust_templates.engine import loader_tags

    print(f"iters={ITERS}, rust engine, render mode, django 6.0.9 pin")
    print(
        "`own us` is the frame Rust would take, measured by pausing the open\n"
        "`ExtendsNode` frame wherever a nested template render begins — so it is\n"
        "the frame's own lines and neither contains nor is contained by the walk.\n"
        "`calls` is `ExtendsNode.render` calls per render; `incl us` is their\n"
        "inclusive sum, quoted so the pause can be read against it.\n"
    )
    header = (
        f"{'row':22} {'render us':>9} {'own us':>8} {'share':>7} {'calls':>6} "
        f"{'incl us':>9} {'find us':>8}"
    )
    print(header)
    print("-" * len(header))

    for key in ROWS:
        workload = next(w for w in bench_corpus.workloads() if w.key == key)
        engine = bench_engines.build("rust", workload.templates, workload.libraries("rust"))
        own = Own()
        find_total = [0.0]
        originals = [
            (loader_tags.ExtendsNode, "render", own.wrap_ext(loader_tags.ExtendsNode)),
            (engine_base.Template, "_render", own.wrap_render(engine_base.Template)),
        ]
        original_find = loader_tags.ExtendsNode.find_template

        def observed_find(node, *args, _o=original_find, **kwargs):
            start = time.perf_counter()
            try:
                return _o(node, *args, **kwargs)
            finally:
                find_total[0] += (time.perf_counter() - start) * 1e6

        loader_tags.ExtendsNode.find_template = observed_find
        originals.append((loader_tags.ExtendsNode, "find_template", original_find))

        try:
            handle = engine.get(workload.entry)
            context = engine.context(workload.data)
            plain = engine.render(handle, context)

            start = time.perf_counter()
            for _ in range(ITERS):
                engine.render(handle, context)
            wall = (time.perf_counter() - start) / ITERS * 1e6

            observed = engine.render(handle, context)
            assert observed == plain, (key, "the observer changed the output")
        finally:
            for cls, name, original in originals:
                setattr(cls, name, original)
            engine.close()

        print(
            f"{key:22} {wall:>9.1f} {own.own_us / ITERS:>8.2f} "
            f"{own.own_us / ITERS / wall:>7.2%} {own.calls / ITERS:>6.2f} "
            f"{own.inclusive_us / ITERS:>9.1f} {find_total[0] / ITERS:>8.3f}"
        )

    print(
        "\n`find us` is `get_parent`'s loader lookup: the one term Rust would\n"
        "still pay after taking the frame, so it is quoted beside `own us` rather\n"
        "than inside it. A row with `calls` 0 has no `{% extends %}` and is the\n"
        "control — the observer's own overhead is bounded by what it does there.\n"
        "`real-world/large` must read 0.00 across."
    )


if __name__ == "__main__":
    main()
