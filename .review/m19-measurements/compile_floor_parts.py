"""The compile floor's parts: which line of `from_string("")` stock does not pay.

`scripts/bench.py`'s `compile` mode reports a floor — the cost of `from_string`
on a template with nothing in it — and ours is ~4.5 us against stock's ~1.45, a
gap of ~3.0 us that no row's *nodes* explain because there are none. This probe
decomposes that gap. It is the answer to a question the M19 step-5 record asked
and left open, and it **corrects an earlier reading of it**: the floor is *not*
the plan being built eagerly. An empty source has no nodes, so the planning pass
has nothing to walk, and the historical check settles it — this same floor
measured ~4.38 us at `51dda9d`, before the walk existed at all. What is in the
floor is the Python shell around the FFI call.

**Levels only, and never subtracted into a column.** Each arm is timed on its
own; `_native.compile_template` is called with its parser, host and materialiser
built *outside* the timed loop, so that row is the Rust call and not its setup.
The arms are chosen so that a reader can see where a boundary is rather than
infer it: `render.parse_template` is the whole Python half, `parser_for` and
`ShellHost` are its two parts, and `_native.compile_template` is the call it
makes.

**The finding is `django_own_tags()`.** `ShellHost.__init__` costs ~1.24 us of
the floor and ~1.12 of that is one call: `django_own_tags()`, a *cached* lookup
whose cache check rebuilds a seven-element tuple from five library registries and
does seven identity comparisons, on every parse. On an empty template the table
it builds is never read — `has_tag` is not called — so the whole cost is paid for
a value nothing looks at. That is why the number is worth having: it names a
Python-side, per-parse, deferrable cost that is larger than the Rust round-trip
the lazy-materialisation hypothesis was aimed at.

Not a claim that the fix is free: the freshness check is what makes a *replaced*
registry rebuild rather than serve stale, and `django_own_tags`' docstring carries
the measured reason it exists (`LibraryTagPredicateTests`). Deferring the call to
first use keeps that property — a later read is a *fresher* one — which is what
makes it a candidate rather than a deletion.

Run from the project root, against the release artifact the gate builds:

    .venv/bin/python -I .review/m19-measurements/compile_floor_parts.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402
from bench import native_artifact  # noqa: E402

REPEATS = 9
ITERS = 4000


def best(fn, iters=ITERS):
    for _ in range(200):
        fn()
    out = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        for _ in range(iters):
            fn()
        out.append((time.perf_counter() - start) / iters * 1e6)
    return min(out)


def main() -> int:
    from django.template.base import Parser as DjangoParser
    from django.template.context import Context as DjangoContext
    from django.template.engine import Engine as DjangoEngine

    from django_rust_templates import _native
    from django_rust_templates import render as our_render
    from django_rust_templates.engine.base import Origin
    from django_rust_templates.engine.engine import Engine as OurEngine
    from django_rust_templates.host import ShellHost, django_own_tags

    origin = Origin("<x>")
    stock_engine = DjangoEngine(loaders=[("x.y", {"": ""})])
    our_engine = OurEngine(loaders=[("x.y", {"": ""})])
    parser = our_render.parser_for("", our_engine, origin)
    host = ShellHost(parser)
    mat = our_render.materialiser()

    print(f"compile floor, {ITERS} iters x {REPEATS} repeats, best-of")
    print(f"artifact: {native_artifact()}")
    print()
    print("the two floors, as `bench.py` reports them:")
    print(f"  stock  Engine.from_string('')             {best(lambda: stock_engine.from_string('')):8.3f} us")
    print(f"  ours   Engine.from_string('')             {best(lambda: our_engine.from_string('')):8.3f} us")
    print()
    print("our Python half, and the two parts of it:")
    print(f"  ours   render.parse_template('')          {best(lambda: our_render.parse_template('', our_engine, origin)):8.3f} us")
    print(f"    ours   parser_for('')      [our Parser]{best(lambda: our_render.parser_for('', our_engine, origin)):8.3f} us")
    print(f"    stock  Parser('')          [its Parser]{best(lambda: DjangoParser('', stock_engine.template_libraries, stock_engine.template_builtins, origin)):8.3f} us")
    print(f"    ours   ShellHost(parser)              {best(lambda: ShellHost(parser)):8.3f} us")
    print(f"      ours   django_own_tags()            {best(django_own_tags):8.3f} us")
    print(f"    ours   materialiser()                 {best(our_render.materialiser):8.3f} us")
    print()
    print("the Rust call the shell makes (parser/host/materialiser built outside the loop):")
    print(f"  ours   _native.compile_template('')       {best(lambda: _native.compile_template('', parser, mat, our_engine, host, origin)):8.3f} us")
    plan = our_engine.from_string("hello")._native_plan
    print(f"  rust   _native.render_plan(plan, ctx)     {best(lambda: _native.render_plan(plan, DjangoContext({}))):8.3f} us")
    return 0


if __name__ == "__main__":
    sys.exit(main())
