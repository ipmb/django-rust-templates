#!/usr/bin/env python3
"""Differential harness.

Renders a corpus of templates twice — once through stock Django, once through
the Rust-backed shim — and compares the results. This gives a fast, granular
signal that is independent of the Django test suite: when a milestone breaks
something, it names the template rather than a test module.

Run with no arguments to compare. ``--render`` is the worker mode used by the
subprocesses and is not meant to be called directly.

Exit status is 1 when any case differs, so this can gate a commit.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
CASES_DIR = ROOT / "tests" / "differential"
SHIM_PATH = ROOT / "scripts" / "shimpath"
PYTHON = ROOT / ".venv" / "bin" / "python"

# Sentinel key reporting which engine actually ran. Never treated as a case.
ENGINE_MARKER = "__engine__"


def render_all() -> dict[str, str]:
    """Render every case with whichever engine is currently installed.

    Exceptions are captured as their repr rather than raised, so that error
    *behaviour* — types and messages — is compared too. A template that should
    raise `TemplateSyntaxError` is as much a case as one that renders.
    """
    sys.path.insert(0, str(CASES_DIR))
    import cases  # noqa: PLC0415  (deliberately late; path set up above)

    from django.template import Context, Engine

    engine = Engine()
    results: dict[str, str] = {}
    for case in cases.CASES:
        name = case["name"]
        try:
            template = engine.from_string(case["template"])
            results[name] = template.render(Context(case.get("context", {})))
        except Exception as exc:
            results[name] = f"!!{type(exc).__name__}: {exc}"

    # A marker so the driver can prove the shim installed in one run and not the
    # other. Without it, a shim that failed to install would make every
    # comparison trivially identical and the harness silently worthless.
    #
    # What it does **not** prove is that Rust ran. Measured 2026-10-09: with the
    # shim installed, spies on `_native.parse` and `_native.compile_template`
    # record **0 calls** across this whole corpus, because `django.template.base`
    # and `django.template.engine` are still Django's — the shim only owns the
    # package `__init__` and `exceptions` until M9 interposes `base`. So
    # `installed()` truthfully means "the finder is up", and the value below is
    # named for that. M9's obligation, recorded here so it is declared and not
    # noticed: replace this with a call counter on the `_native` entry points and
    # assert `> 0` for the shim run and `== 0` for stock. That is the commit where
    # the two propositions coincide.
    import django_rust_templates  # noqa: PLC0415

    results[ENGINE_MARKER] = (
        "shim-installed" if django_rust_templates.installed() else "stock"
    )
    return results


def render_in_subprocess(mode: str) -> dict[str, str]:
    """Run the corpus in a fresh interpreter, so the shim choice is isolated."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SHIM_PATH), str(CHECKOUT), env.get("PYTHONPATH", "")]
    )
    env["DJANGO_RUST_TEMPLATES"] = "1" if mode == "shim" else "0"

    proc = subprocess.run(
        [str(PYTHON), str(Path(__file__).resolve()), "--render"],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"render failed in {mode} mode:\n{proc.stderr}")
    return json.loads(proc.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.render:
        json.dump(render_all(), sys.stdout)
        return

    if not CHECKOUT.is_dir():
        raise SystemExit(f"no Django checkout at {CHECKOUT} — run scripts/fetch_django.sh")

    stock = render_in_subprocess("stock")
    shim = render_in_subprocess("shim")

    # Prove the harness can detect a difference before trusting it to report
    # that there isn't one. This proves the *shim installed*; see the note in
    # `render_all` for why that is not yet the same claim as "Rust ran".
    for label, results, expected in (
        ("stock", stock, "stock"),
        ("shim", shim, "shim-installed"),
    ):
        actual = results.pop(ENGINE_MARKER, None)
        if actual != expected:
            raise SystemExit(
                f"harness is not working: {label} run used engine {actual!r}, "
                f"expected {expected!r}"
            )

    diffs = [
        (name, stock.get(name, "<missing>"), value)
        for name, value in shim.items()
        if stock.get(name, "<missing>") != value
    ]

    if not diffs:
        print(f"differential: {len(shim)} cases, identical (stock vs shim-installed)")
        return

    print(f"differential: {len(diffs)} of {len(stock)} cases differ\n")
    for name, stock_value, shim_value in diffs:
        print(f"  {name}")
        print(f"    stock: {stock_value!r}")
        print(f"    shim:  {shim_value!r}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
