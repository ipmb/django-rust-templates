#!/usr/bin/env python3
"""Differential harness.

Renders a corpus of templates twice — once through stock Django, once through
this package's engine — and compares the results. This gives a fast, granular
signal that is independent of the Django test suite: when a milestone breaks
something, it names the template rather than a test module.

**The two engines are two imports.** Until M18 the "ours" side was reached by
putting a `sitecustomize` on `PYTHONPATH` and setting `DJANGO_RUST_TEMPLATES=1`, so
that the substitution shim loaded our file *under Django's module name*; the two
runs then had to be separate processes, because in one process there was only one
name to import. Now `django.template.engine.Engine` and
`django_rust_templates.engine.engine.Engine` are two ordinary names in one tree,
and which one ran is read back off the object rather than asserted about the
environment.

Run with no arguments to compare. ``--render <ours|stock>`` is the worker mode used
by the subprocesses and is not meant to be called directly; the subprocesses are
kept so that neither engine's module-level state can reach the other.

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
PYTHON = ROOT / ".venv" / "bin" / "python"

# The two engines, by the name each is importable under. `ours` is this package's
# transcription and `stock` is Django's own, and nothing else distinguishes them —
# which is the point: a reader can check either one by hand.
ENGINES = {
    "ours": "django_rust_templates.engine.engine",
    "stock": "django.template.engine",
}

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


def render_all(mode: str) -> dict[str, str]:
    """Render every case through the engine `mode` names.

    Exceptions are captured as their repr rather than raised, so that error
    *behaviour* — types and messages — is compared too. A template that should
    raise `TemplateSyntaxError` is as much a case as one that renders.

    **Settings are configured, and until M19's `int` arm this harness ran with
    none.** That is the whole reason the line is here, and the measurement is
    what makes it more than tidiness: of the **52** cases, **11** render a value
    whose type is `int` — `integer_literal`, `a_bare_number`, the three
    `for_*` loops (the counter), `length_filter` and `add_filter` (both return
    one), `ifchanged_tag`, `nested_for` and two more — and with settings
    unconfigured *both* engines refuse to render those at all, because
    `render_value_in_context` reaches `number_format` and `get_format` reads a
    setting. So the two columns were **two `ImproperlyConfigured` reprs**, and
    they matched because both engines read the same setting first. The case was
    covering nothing and said so only when the first setting read changed.

    That is exactly what M19's `int` arm does: the arm reads
    `settings.USE_THOUSAND_SEPARATOR` *before* the slow path reaches
    `get_language()`'s `USE_I18N`, so the 11 reprs parted — `11 of 48 cases
    differ` **on the 48-case corpus this was measured on**, both sides naming a
    setting rather than rendering a number. That was the first thing the harness
    had ever said about those cases, and what it said was that it had never been
    showing their output.

    Configured, the same corpus is **52 of 52 identical**, and the 11 now compare
    `'12345'` against `'12345'` instead of one error string against another.

    **48 → 52 is the four `{% cycle … as %}` rebinding rows**, added with the fix
    for the sealed-loop frame rewrite: they render `str`, so the 11 above is
    still 11 and the delta is entirely cases with no settings dependency. The
    historical `11 of 48` is left standing rather than restated, because it is a
    measurement of the corpus *as it was* and rewriting its denominator would
    make the record say something nobody observed.

    The settings are the tree's shared helper rather than a local `configure(...)`,
    for the reason `scripts/filter_probe.py` gives: `USE_I18N` and
    `django.setup()` are two separate requirements and a hand-built fixture got
    it wrong for the benchmark harness once already.

    **The unconfigured behaviour is not deleted with the fixture — it is pinned
    where it can be seen.** `tests/test_base_parity.py`'s
    `UnconfiguredSettingsTests` runs both engines in a fresh interpreter with no
    settings and asserts the pair: same exception type, and ours naming
    `USE_THOUSAND_SEPARATOR` where stock names `USE_I18N`. A divergence that a
    harness change hides is worse than one the harness reports.
    """
    import importlib

    sys.path.insert(0, str(CASES_DIR))
    sys.path.insert(0, str(ROOT / "scripts"))
    from filter_probe import ensure_settings  # noqa: PLC0415

    ensure_settings()
    import cases  # noqa: PLC0415  (deliberately late; path set up above)

    from django.template import Context

    calls = _counting_native()

    engine_cls = getattr(
        importlib.import_module(ENGINES[mode]), "Engine"
    )
    engine = engine_cls()
    # **A case may name a library and a context that holds itself**, and both are
    # still JSON: ``libraries`` is a mapping of tag-library name to module path and
    # ``context_ref`` is a name. That is what lets a case reach a *filter handed the
    # context as an argument* — the route that falsified "a filter cannot write the
    # context" — without pickling a callable or an object graph into the case file.
    # Engines are built per library set and cached, because the module a case names
    # is importable from `CASES_DIR`, which is already on `sys.path` above.
    engines_by_library: dict[tuple, object] = {}

    def engine_for(case: dict) -> object:
        libraries = case.get("libraries")
        if not libraries:
            return engine
        key = tuple(sorted(libraries.items()))
        if key not in engines_by_library:
            engines_by_library[key] = engine_cls(libraries=dict(key))
        return engines_by_library[key]

    results: dict[str, str] = {}
    for case in cases.CASES:
        name = case["name"]
        try:
            context = Context(case.get("context", {}))
            reference = case.get("context_ref")
            if reference:
                context[reference] = context
            template = engine_for(case).from_string(case["template"])
            results[name] = template.render(context)
        except Exception as exc:
            results[name] = f"!!{type(exc).__name__}: {exc}"

    # A marker so the driver can prove the two runs reached *different* engines.
    # Without it, an import that silently resolved to the same module twice would
    # make every comparison trivially identical and the harness silently worthless.
    #
    # **It is read off the object rather than asserted about the environment**, and
    # that is the M18 change. The sentinel used to be `installed()` — whether a
    # finder was on `sys.meta_path` — which was a *proxy* for "our engine ran", and
    # a proxy that was measured wrong once: spies on `_native.parse` and
    # `_native.compile_template` recorded 0 calls across this whole corpus while
    # the finder was up and `base` and `engine` were still Django's. `type(engine)`
    # is the engine.
    results[ENGINE_MARKER] = type(engine).__module__
    results[NATIVE_MARKER] = str(calls())
    return results


def render_in_subprocess(mode: str) -> dict[str, str]:
    """Run the corpus in a fresh interpreter, so neither engine's state leaks.

    The isolation used to be load-bearing for a second reason — one process could
    hold only one `django.template`, so the two engines could not coexist — and it
    is kept now for the ordinary one: `Engine` and the modules under it keep
    module-level caches, and a differential harness that let one warm the other
    would be measuring the wrong thing in a way no assertion here would catch.
    """
    proc = subprocess.run(
        [str(PYTHON), str(Path(__file__).resolve()), "--render", mode],
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"render failed in {mode} mode:\n{proc.stderr}")
    return json.loads(proc.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--render", choices=sorted(ENGINES), help=argparse.SUPPRESS, default=None
    )
    args = parser.parse_args()

    if args.render:
        json.dump(render_all(args.render), sys.stdout)
        return

    if not CHECKOUT.is_dir():
        raise SystemExit(f"no Django checkout at {CHECKOUT} — run scripts/fetch_django.sh")

    stock = render_in_subprocess("stock")
    ours = render_in_subprocess("ours")

    # Prove the harness can detect a difference before trusting it to report that
    # there isn't one. Two propositions, and they are separate rows: the two runs
    # reached *different* engine objects, and — the one this harness could not make
    # until M9 — the `ours` run reached Rust while the stock run did not.
    for label, results, expected in (
        ("stock", stock, ENGINES["stock"]),
        ("ours", ours, ENGINES["ours"]),
    ):
        actual = results.pop(ENGINE_MARKER, None)
        if actual != expected:
            raise SystemExit(
                f"harness is not working: {label} run used engine {actual!r}, "
                f"expected {expected!r}"
            )

    stock_calls = int(stock.pop(NATIVE_MARKER, "0"))
    ours_calls = int(ours.pop(NATIVE_MARKER, "0"))
    if stock_calls != 0 or ours_calls == 0:
        raise SystemExit(
            "harness is not working: `_native` entry points were called "
            f"{stock_calls} time(s) by stock Django and {ours_calls} by this "
            "engine, expected 0 and > 0"
        )

    diffs = [
        (name, stock.get(name, "<missing>"), value)
        for name, value in ours.items()
        if stock.get(name, "<missing>") != value
    ]

    if not diffs:
        print(
            f"differential: {len(ours)} cases, identical "
            f"({ENGINES['stock']} vs {ENGINES['ours']})"
        )
        return

    print(f"differential: {len(diffs)} of {len(stock)} cases differ\n")
    for name, stock_value, ours_value in diffs:
        print(f"  {name}")
        print(f"    stock: {stock_value!r}")
        print(f"    ours:  {ours_value!r}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
