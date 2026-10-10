"""How much of a compile is the Python tree, before any of it is made lazy.

Spec §5 sequences **step 4, lazy materialisation** before **step 5, the rest of
the tree**.  Step 5's value is measured (`.review/m19-measurements/
inheritance_share.py`: 99% of the render on the three inheritance rows is inside
the delegated `ExtendsNode`).  This file is step 4's counterpart, so the
sequencing decision is made on two numbers rather than on one number and the
order of a list.

**What is timed, and what the instrument can and cannot see.**  Rust drives the
materialisation: `_native.compile_template` calls the Python callable the shell
handed it, `parser.materialise(desc, None, None, parser)`
(`python/django_rust_templates/render.py:78`).  Wrapping that callable times the
whole Python-side tree construction without touching the Rust parse — which is
correct, because the proposal is to skip the Python tree and not to speed up
Rust.  Only the outermost call is timed; the recursion inside is part of it.

`from_string` is timed end to end around the same loop, so the share is a ratio
of two columns measured in one run on one artefact — the artefact's identity is
printed, because a compile measurement is a measurement of a build.

**The share is an upper bound on step 4's saving, not the saving itself.**  A
lazy tree still has to answer `.nodelist` for the acceptance suite, for
`ExtendsNode`'s parent lookup and for every delegated node, so what step 4
removes is the construction on the path that never asks — the render path and
every `from_string` whose tree nobody introspects.  What it buys for a caller
that does introspect is the deferral, not the work.  This file measures the
size of the thing, and says so.

Run:  .venv/bin/python -I .review/m19-measurements/lazy_size.py
"""

from __future__ import annotations

import hashlib
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

ITERS = 300

ROWS = (
    "micro/text",
    "micro/variable",
    "micro/loop-text",
    "real-world/normal",
    "real-world/large",
)


def main() -> None:
    import django_rust_templates._native as _native
    from django_rust_templates.engine import parser as parser_module

    artifact = Path(_native.__file__)
    print(f"iters={ITERS}, rust engine, from_string (compile only)")
    print(f"artifact {artifact.name} sha256:{hashlib.sha256(artifact.read_bytes()).hexdigest()[:12]}")
    print("`materialise` is the Python tree; nested calls are inside the timed frame\n")
    print(f"{'row':22}  {'compile us':>11}  {'materialise us':>15}  {'share':>7}  {'calls':>5}")
    print("-" * 70)

    original = parser_module.materialise
    for key in ROWS:
        workload = next(w for w in bench_corpus.workloads() if w.key == key)
        source = workload.templates[workload.entry]
        engine = bench_engines.build("rust", workload.templates, {})
        try:
            engine.from_string(source)  # warm

            total = 0.0
            inside = 0.0
            calls = 0
            depth = 0

            def timed(*args, **kwargs):
                nonlocal inside, calls, depth
                outer = depth
                depth += 1
                start = time.perf_counter() if outer == 0 else None
                try:
                    return original(*args, **kwargs)
                finally:
                    depth -= 1
                    calls += 1
                    if start is not None:
                        inside += time.perf_counter() - start

            parser_module.materialise = timed
            try:
                start = time.perf_counter()
                for _ in range(ITERS):
                    engine.from_string(source)
                total = time.perf_counter() - start
            finally:
                parser_module.materialise = original
        finally:
            engine.close()

        compile_us = total / ITERS * 1e6
        inside_us = inside / ITERS * 1e6
        print(
            f"{key:22}  {compile_us:>11.2f}  {inside_us:>15.2f}"
            f"  {inside_us / compile_us:>6.1%}  {calls / ITERS:>5.1f}"
        )

    print(
        "\nThe share is the ceiling on what step 4 can remove from this row, and\n"
        "the row it is largest on is the row with the most nodes — which is the\n"
        "row `parse` mode already loses.  A row whose share is small is one where\n"
        "the compile is Rust and the tree was never the cost."
    )


if __name__ == "__main__":
    main()
