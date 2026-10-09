"""M10's central risk, asked as a measurement: does interposing the four
modules break the suite?

``§10.3``'s M10 row names ``context``, ``engine``, ``utils`` and ``loaders``,
and ``§6`` says Python owns all four — so unlike M9 there is no Rust seam to
build and no delegation to write. The milestone's content is the
*interposition itself*, and its risk is that these four are load-bearing for
everything else: ``_impl/__init__.py`` imports ``Engine`` from ``.engine``,
``EngineHandler`` from ``.utils`` and ``Context``/``RequestContext`` from
``.context``, so the whole package re-binds at once, and every one of the 1,547
tests goes through ``Context`` (measured: 984 of them).

The cheapest form of the question is a *verbatim* interposition — no markers,
no declarations, just Django's own source at our path. Two outcomes, both
useful:

* the suite stays green, and the transcription side of M10's per-file decision
  is proven rather than assumed (the plan then argues which files that is
  honest for, and ``§6``'s "written fresh" files carry their own obligations);
* it goes red, and the failures *are* the milestone's task list — the plan
  stops guessing about import order, re-binding and the ``loaders`` /
  ``backends`` coupling.

Nothing here is written to the tree. The scratch universe is a temp directory:
a copy of ``_impl`` plus verbatim copies of the four modules, and
``_shim._IMPL_DIR`` is repointed at it before ``install()``. The finder reads
that module global on every call, so the real ``_impl`` is never touched.

Run it the way the suite is run, minus the shim — ``DJANGO_RUST_TEMPLATES=0``
keeps ``shimpath``'s ``sitecustomize`` from installing Django's ``_impl`` at
interpreter start, which would leave nothing for this script to install:

    . scripts/env.sh
    DJANGO_RUST_TEMPLATES=0 python -I \\
        .review/m10-measurements/m10_probe_verbatim_interposition.py

``-I`` keeps the ambient environment out, which matters because this repoints
the shim at a temp directory. The paths are inserted by hand below, because
``-I`` also drops ``PYTHONPATH``.
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

import django_rust_templates  # noqa: E402
from django_rust_templates import _shim  # noqa: E402

# What M10 interposes, as paths relative to `django/template/`.
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

scratch = tempfile.mkdtemp(prefix="m10-probe-")
impl = os.path.join(scratch, "_impl")
shutil.copytree(REAL_IMPL, impl)
for relative in TARGETS:
    source = os.path.join(DJANGO_TEMPLATE, relative)
    destination = os.path.join(impl, relative)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copyfile(source, destination)

# The finder and every helper read this module global on each call, so moving
# it moves the whole universe: `_served`, `_is_ours`, `find_spec`.
_shim._IMPL_DIR = pathlib.Path(impl)
_shim._IMPL_INIT = _shim._IMPL_DIR / "__init__.py"

print(f"scratch universe: {impl}", file=sys.stderr)

# `unshimmed()` refuses to answer before `install()` — it walks the tree the
# finder serves, and there is no finder yet — so the baseline is the number the
# closed milestone reports, checked here rather than assumed.
BASELINE = 23

_shim.install()
after = len(_shim.unshimmed())
print(f"unshimmed: {BASELINE} -> {after}", file=sys.stderr)
print("still served by Django: " + ", ".join(_shim.unshimmed()), file=sys.stderr)

# Prove the interposition is real before spending the run on it: if any of the
# four still resolves to Django's file, the suite result below is about Django.
import importlib  # noqa: E402

for name in (
    "django.template.context",
    "django.template.engine",
    "django.template.utils",
    "django.template.loaders.base",
    "django.template.loaders.cached",
):
    module = importlib.import_module(name)
    file = module.__file__ or ""
    if "_impl" not in file:
        sys.exit(f"{name} resolved to {file}, not to the scratch universe")

sys.argv = ["runtests.py", "template_tests", "--parallel", "1"]
os.chdir(os.path.join(CHECKOUT, "tests"))

runtests = os.path.join(CHECKOUT, "tests", "runtests.py")
namespace = {"__name__": "__main__", "__file__": runtests}
with open(runtests, encoding="utf-8") as handle:
    source = handle.read()

try:
    exec(compile(source, runtests, "exec"), namespace)
except SystemExit:
    pass

shutil.rmtree(scratch, ignore_errors=True)
