#!/usr/bin/env python
"""Where a template's *compile* goes: Rust lex, Rust parse, Python materialise.

M16's four workloads are all `from_string(...).render(...)`, so nothing in the
tree isolates the parser, and §6 read the ratios as if the engine's parse were a
cost centre it could tune. This probe splits it. On our side there are three
measurable stages and they nest:

    _native.lex(src)              Rust lexing only
    render.parser_for(src, eng)   + the Rust parse, and the Parser object
    render.parse_template(...)    + materialising the descriptor tree into
                                    Python `Node` objects, and the `Template`

Stock has no such seam — its whole compile is one `Engine.from_string` — so the
stock side prints the single column and the comparison is made by reading the two
transcripts side by side. The script deliberately does **not** subtract across
them: a subtraction the instrument does is a number no reader can check.

It prints `side:` and the tree it loaded, for the same reason `scripts/bench.py`
does — the module names are identical on both sides and only `__file__` tells
them apart.

Run it under both sides exactly as `scripts/bench.py` is run:

    source scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        .venv/bin/python .review/m18-measurements/m18_parse_split.py
    DJANGO_RUST_TEMPLATES=0 PYTHONPATH="$PWD/.django" \
        .venv/bin/python -I .review/m18-measurements/m18_parse_split.py

and diff the two transcripts by hand.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

# Same bootstrap as `scripts/bench.py`, for the same reason: one place in this
# tree configures settings (`tests/test_settings_configuration.py` refuses a
# second), and `filter_probe` also puts `.django` on `sys.path`, which the stock
# side needs because `-I` implies `-E`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.template import base as template_base  # noqa: E402

import bench  # noqa: E402

# `bench`'s own four sources. `large` is the parse-heavy one (9294 chars, 462
# nodes, measured by the node census below — not `bench`'s docstring's guess);
# `tiny` is the fixed-overhead one.
WORKLOADS = {
    "tiny": "{{ a|upper }}",
    "base": bench.BASE,
    "page": bench.PAGE,
    "large": bench.LARGE,
}


def measure(fn, iters: int, repeats: int = 7) -> float:
    """Best-of-N per-call microseconds, the same estimator `scripts/bench.py` uses."""
    for _ in range(max(3, iters // 4)):
        fn()
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(iters):
            fn()
        best = min(best, (time.perf_counter() - start) / iters)
    return best * 1e6


def main() -> int:
    shimmed = "django_rust_templates" in template_base.__file__
    print(f"tree:    {template_base.__file__}")
    print(f"django:  {django.get_version()}")
    print(f"side:    {'rust-shim' if shimmed else 'stock'}")
    print(f"python:  {sys.version.split()[0]}")
    print()

    engine = Engine(
        loaders=[
            (
                "django.template.loaders.locmem.Loader",
                {"base.html": bench.BASE, "page.html": bench.PAGE},
            )
        ],
        libraries={"i18n": "django.templatetags.i18n"},
    )

    render = None
    native = None
    if shimmed:
        from django_rust_templates import _native, render as _render

        render, native = _render, _native

    columns = ["lex", "parser_for", "compile", "materialise"]
    print(f"{'workload':10s} " + " ".join(f"{c:>12s}" for c in columns) + f" {'nodes':>6s} {'sha256':>8s}")

    for name, source in WORKLOADS.items():
        template = engine.from_string(source)  # warm, and the node census
        nodes = sum(1 for _ in template.nodelist)
        out = template.render(Context({"a": "x"}))
        digest = hashlib.sha256(out.encode()).hexdigest()[:8]

        compile_ = measure(lambda s=source: engine.from_string(s), 200)

        if render is None:
            # Stock has no seam, so `lex`/`parser_for`/`materialise` are dashes and
            # the one number it has goes under `compile`. Printed positionally
            # rather than as a row of dashes and a trailing value, which would put
            # stock's whole compile under the `materialise` heading.
            print(
                f"{name:10s} {'-':>12s} {'-':>12s} {compile_:12.3f} {'-':>12s} "
                f"{nodes:6d} {digest:>8s}"
            )
            continue

        lex = measure(lambda s=source: native.lex(s), 200)
        parser_for = measure(lambda s=source: render.parser_for(s, engine), 200)
        print(
            f"{name:10s} {lex:12.3f} {parser_for:12.3f} {compile_:12.3f} "
            f"{compile_ - parser_for:12.3f} {nodes:6d} {digest:>8s}"
        )

    print()
    print("chars: " + "  ".join(f"{k}={len(v)}" for k, v in WORKLOADS.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
