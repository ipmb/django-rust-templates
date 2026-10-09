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

# Sentinel keys reporting which engine actually ran and how much of it was
# Rust. Never treated as cases.
ENGINE_MARKER = "__engine__"
NATIVE_MARKER = "__native_calls__"


def _counting_native():
    """Wrap every callable on ``_native`` with a counter; return the total.

    Wrapping the extension module's attributes is what makes "Rust ran" a
    measurement instead of a claim about the shim's finder. Types are left
    alone — wrapping a constructor tells you nothing a call site does not —
    and the wrapper counts only Python-side calls, which is the question.
    """
    import django_rust_templates._native as native

    total = 0

    for name in sorted(vars(native)):
        original = getattr(native, name)
        if not callable(original) or isinstance(original, type):
            continue

        def make(original):
            def counting(*args, **kwargs):
                nonlocal total
                total += 1
                return original(*args, **kwargs)

            return counting

        setattr(native, name, make(original))

    return lambda: total


def render_all() -> dict[str, str]:
    """Render every case with whichever engine is currently installed.

    Exceptions are captured as their repr rather than raised, so that error
    *behaviour* — types and messages — is compared too. A template that should
    raise `TemplateSyntaxError` is as much a case as one that renders.
    """
    sys.path.insert(0, str(CASES_DIR))
    import cases  # noqa: PLC0415  (deliberately late; path set up above)

    from django.template import Context, Engine

    calls = _counting_native()

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
    # **`installed()` truthfully means "the finder is up", not "Rust ran"** —
    # measured 2026-10-09, spies on `_native.parse` and `_native.compile_template`
    # recorded **0 calls** across this whole corpus while `base` and `engine` were
    # still Django's. That measurement was recorded here as M9's obligation, and
    # M9 discharged it: with `base` interposed the corpus now reaches Rust, and
    # the count is asserted below rather than described. The two propositions
    # coincide, which is what this sentinel was waiting for.
    import django_rust_templates  # noqa: PLC0415

    results[ENGINE_MARKER] = (
        "shim-installed" if django_rust_templates.installed() else "stock"
    )
    results[NATIVE_MARKER] = str(calls())
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
    # that there isn't one. Two propositions, and they are now separate rows:
    # the shim installed, and — the one this harness could not make until M9 —
    # that the shim run reached Rust while the stock run did not.
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

    stock_calls = int(stock.pop(NATIVE_MARKER, "0"))
    shim_calls = int(shim.pop(NATIVE_MARKER, "0"))
    if stock_calls != 0 or shim_calls == 0:
        raise SystemExit(
            "harness is not working: `_native` entry points were called "
            f"{stock_calls} time(s) without the shim and {shim_calls} with it, "
            "expected 0 and > 0"
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
