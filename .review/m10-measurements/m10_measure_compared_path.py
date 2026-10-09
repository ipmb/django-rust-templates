"""Which functions in each M10 module does the parity harness execute?

`scripts/check_transcriptions.py`'s `COMPARED_PATH` claims, per transcription,
the code a parity harness runs — and clause (4) refuses a declared region that
sits on it. M9 measured one module (`base.py`); M10 adds nine more files, and
the table asserts *key-set equality* with `DECLARED`, so interposing a module
without measuring it is a guard failure in the commit that interposes it.

That makes this the provenance for nine numbers, and it has to be run before
the modules exist: the alternative is nine plan steps saying "now go measure
it", which is how a table ends up filled in with what someone expected. So the
whole milestone is interposed *verbatim* in a scratch universe — the same
mechanism as `m10_probe_verbatim_interposition.py`, and the same one `.review`
uses to keep out of the real tree — and each module's reach is measured over
both parity corpora.

The instrument is `tests/compared_path.py`, the same one
`tests/test_interposition.py::test_the_compared_path_is_the_measured_one`
re-measures with, so the printed table and the pinned table come from one
spelling of the question.

`django_parse` builds a **fresh** `Engine` per call (`test_parser_parity.py`,
`_library_engine`), so measuring the modules one at a time over the same corpus
is fair: no module's window is the one that happens to pay for construction.

Run it the way the suite is run, minus the shim — `DJANGO_RUST_TEMPLATES=0`
keeps `shimpath`'s `sitecustomize` from installing the real `_impl` at
interpreter start, which would leave nothing for this script to install:

    . scripts/env.sh
    DJANGO_RUST_TEMPLATES=0 .venv/bin/python -I \\
        .review/m10-measurements/m10_measure_compared_path.py

`-I` keeps the ambient environment out, which matters because this repoints the
shim at a temp directory. The paths are inserted by hand below, because `-I`
also drops `PYTHONPATH`.
"""

import os
import pathlib
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")
REAL_IMPL = os.path.join(ROOT, "python", "django_rust_templates", "_impl")
DJANGO_TEMPLATE = os.path.join(CHECKOUT, "django", "template")

# M10's nine files, as paths relative to `django/template/`.
TARGETS = [
    "context.py",
    "engine.py",
    "utils.py",
    "loaders/__init__.py",
    "loaders/base.py",
    "loaders/filesystem.py",
    "loaders/app_directories.py",
    "loaders/cached.py",
    "loaders/locmem.py",
]

if os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    sys.exit(
        "DJANGO_RUST_TEMPLATES is 1, so sitecustomize already installed the real "
        "_impl at interpreter start and this script has nothing to install. "
        "Re-run with DJANGO_RUST_TEMPLATES=0."
    )

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, CHECKOUT)
sys.path.insert(0, os.path.join(CHECKOUT, "tests"))
sys.path.insert(0, ROOT)

import django_rust_templates  # noqa: E402
from django_rust_templates import _shim  # noqa: E402

scratch = tempfile.mkdtemp(prefix="m10-compared-path-")
impl = os.path.join(scratch, "_impl")
shutil.copytree(REAL_IMPL, impl)
for relative in TARGETS:
    source = os.path.join(DJANGO_TEMPLATE, relative)
    destination = os.path.join(impl, relative)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copyfile(source, destination)

_shim._IMPL_DIR = pathlib.Path(impl)
_shim._IMPL_INIT = _shim._IMPL_DIR / "__init__.py"

print(f"scratch universe: {impl}", file=sys.stderr)
_shim.install()
print(f"unshimmed: 23 -> {len(_shim.unshimmed())}", file=sys.stderr)

import importlib  # noqa: E402
import random  # noqa: E402

from tests.compared_path import definitions, measure  # noqa: E402
from tests.parser_corpus import (  # noqa: E402
    RANDOM_CASES,
    RANDOM_SEED,
    TEMPLATES,
    random_template,
)
from tests.test_parser_parity import django_parse  # noqa: E402

_rng = random.Random(RANDOM_SEED)
generated = [random_template(_rng) for _ in range(RANDOM_CASES)]
sources = list(TEMPLATES) + generated
print(
    f"corpus: {len(TEMPLATES)} hand-written + {len(generated)} generated",
    file=sys.stderr,
)


def dotted(relative: str) -> str:
    """``loaders/base.py`` → ``django.template.loaders.base``."""
    stem = relative[: -len(".py")] if relative.endswith(".py") else relative
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    elif stem == "__init__":
        stem = ""
    return "django.template" + ("." + stem.replace("/", ".") if stem else "")


for relative in TARGETS:
    module = importlib.import_module(dotted(relative))
    if "_impl" not in (module.__file__ or ""):
        sys.exit(f"{dotted(relative)} resolved to {module.__file__}")

    reached = measure(module, sources, django_parse)
    defined = {
        name: span for name, span in definitions(module).items() if span is not None
    }
    print()
    print(f"{relative}: {len(reached)} of {len(defined)} defined function(s) reached")
    for name, span in reached.items():
        print(f"    {span[0]:5d}-{span[1]:<5d} {name}")
    print(f'COMPARED_PATH["{relative}"] = [')
    for name, span in reached.items():
        print(f'    ({span[0]}, {span[1]}, "{name}"),')
    print("]")

    # A reach of three functions in `context.py` is small enough that the pin
    # should say *which* sources produce it: a pin whose route nobody has named
    # is a pin that fails silently the next time the corpus moves. Printed only
    # where something was reached, so the empty modules say nothing.
    if reached:
        print(f"  which sources reach {relative}:")
        for index, source in enumerate(sources):
            one = measure(module, [source], django_parse)
            if one:
                shown = source.replace("\n", " ")[:70]
                print(f"    [{index:3d}] {shown!r}")

shutil.rmtree(scratch, ignore_errors=True)
