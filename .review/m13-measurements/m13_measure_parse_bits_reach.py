"""How far into the 1,547 `library.py`'s compile-time surface actually reaches.

M9's shape, applied to M13's module: not "is the module imported" but *which of
its functions are called, how often, and from how many test rows*. The M9 recon
measured the seam at 1,170 tests / 4,360 calls for the parse; this asks the same
question of `Library` and `parse_bits`.

The counters are installed on the module objects before Django's runner starts,
and the runner is invoked with `--parallel 1` because the default runner forks
and a child's counters never reach the parent.

    .venv/bin/python .review/m13-measurements/m13_measure_parse_bits_reach.py

Run through the shim's own `PYTHONPATH` (this script re-execs itself under it),
so the numbers describe the tree the acceptance criterion describes.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

WATCHED_MODULE = "django.template.library"
WATCHED_NAMES = ("parse_bits", "import_library")


def _reinstall_under_the_shim():
    if os.environ.get("_M13_REACH_CHILD") == "1":
        return False
    env = dict(os.environ)
    env["_M13_REACH_CHILD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "scripts" / "shimpath"), str(ROOT / ".django"), str(ROOT)]
    )
    # The shim installs through `sitecustomize`, i.e. at interpreter start, so a
    # fresh interpreter is the only honest way to get it.
    os.execve(sys.executable, [sys.executable, __file__], env)
    raise SystemExit("unreachable")


if _reinstall_under_the_shim():
    pass

import django.template.library as library  # noqa: E402

COUNTS = {}
CALLERS = {}


def _wrap(owner, name, label):
    original = getattr(owner, name)

    def counted(*args, **kwargs):
        COUNTS[label] = COUNTS.get(label, 0) + 1
        frame = sys._getframe(1)
        while frame is not None:
            filename = frame.f_code.co_filename
            if "template_tests" in filename or "/tests/" in filename:
                CALLERS.setdefault(label, set()).add(filename)
                break
            frame = frame.f_back
        return original(*args, **kwargs)

    counted.__wrapped__ = original
    setattr(owner, name, counted)


for name in WATCHED_NAMES:
    _wrap(library, name, f"{WATCHED_MODULE}.{name}")
for name in ("tag", "filter", "simple_tag", "simple_block_tag", "inclusion_tag"):
    _wrap(library.Library, name, f"Library.{name}")
for name in ("render", "get_resolved_arguments"):
    for cls in ("SimpleNode", "SimpleBlockNode", "InclusionNode", "TagHelperNode"):
        if hasattr(getattr(library, cls), name):
            _wrap(getattr(library, cls), name, f"{cls}.{name}")

# **A module-attribute patch cannot see a binding made at import**, which is the
# M9 hazard this project keeps writing rows against: `_impl/engine.py:12` is
# `from .library import import_library`, so it holds Django's function object and
# never consults the module attribute wrapped above. Patching the module
# attribute alone reports a confident `0` for a function the engine calls once
# per `Engine`. The bound name is wrapped here too, and the two counts are
# printed separately rather than summed.
# It is reached the way the tree reaches it — *as* `django.template.engine`. The
# same import spelled `django_rust_templates._impl.engine` raises
# `ModuleNotFoundError: No module named 'django_rust_templates._impl.library'`,
# because `.library` only resolves through the `django.template` package whose
# `__path__` carries Django's directory as its second entry. The module is only
# ever loaded under the first name; M13 creates `_impl/library.py` so that the
# relative import has something of ours to find.
import django.template.engine as _our_engine  # noqa: E402

if hasattr(_our_engine, "import_library"):
    _wrap(_our_engine, "import_library", "engine.import_library (bound name)")

checkout_tests = ROOT / ".django" / "tests"
sys.path.insert(0, str(checkout_tests))
os.chdir(checkout_tests)
sys.argv = ["runtests.py", "template_tests", "--parallel", "1"]

try:
    runpy.run_path(str(checkout_tests / "runtests.py"), run_name="__main__")
except SystemExit:
    pass

print()
print("== calls into library.py's compile-time surface, over template_tests ==")
for label in sorted(set(COUNTS) | {f"{WATCHED_MODULE}.{n}" for n in WATCHED_NAMES}):
    files = CALLERS.get(label, set())
    print(f"  {label:<40} calls={COUNTS.get(label, 0):<6} files={len(files)}")
    for filename in sorted(files):
        print(f"      {Path(filename).relative_to(ROOT)}")
