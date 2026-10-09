"""The four modules M10 interposes, sized by running the suite and counting.

M10 interposes ``django.template.context``, ``engine``, ``utils`` and the
``loaders`` package. §6 says Python owns them, so the milestone's deliverable is
the meter (§2's acceptance criterion) rather than a Rust seam — but §10.1's
premise for interposing anything at all is that *its Django tests become real
signal rather than noise*, and that is a claim about a count. This is that
count.

What it measures, and why it is not one number
----------------------------------------------

The four modules answer four different questions, and adding them together
would make the total mean nothing:

* ``context`` is on **every** render — ``Engine.from_string(...).render(Context())``
  is the shape of nearly every template test — so its count is close to a
  census of the suite and says little. The interesting figure is the opposite
  one: how many tests *do not* touch it.
* ``loaders`` is on the **load** path only, so it counts the tests that read a
  template from disk or a mapping, which is what ``test_loaders`` is about.
* ``engine`` is on both, and is the module ``test_engine`` names.
* ``utils`` is ``EngineHandler`` and ``get_app_template_dirs``, reached when a
  *configured* engine is looked up from settings — framework-coupled, which is
  §6's stated reason to keep it in Python.

So this prints per module, and prints the per-test maps rather than only
totals, because M9's finding 3 is the standing lesson: a count is only evidence
once you know which population it counted.

A pre-flight, not an assumption
-------------------------------

Written before M10 landed, so it opens by saying which file each target
resolved to and measures anyway — the number it produces is the size of the
path a future interposition would sit on, which is what the plan needs before
the interposition exists. Re-running it after M10 lands should reproduce the
same counts, and a difference is the finding.

**It was re-run at the close-out, and the pair is identical.** All eight
targets now report ``is already ours``, and every figure reproduces exactly:
``context`` 984/95,051, ``engine`` 1166/41,987, ``loaders.base`` 1053/10,282,
``loaders.filesystem`` 85/676, ``loaders.app_directories`` 34/58,
``loaders.cached`` 1038/25,249, ``loaders.locmem`` 974/10,854, ``utils`` 46/164,
and **1232 of the 1547 (79.6%) reached any of the four**. That is the result the
recon's §3a predicted and the shape it warned about: this instrument measures
*reach*, and a transcription changes the **identity** of the objects a test
holds without changing which tests reach them. So the numbers moving would have
meant a behaviour changed, and their holding still is not evidence that nothing
did — §3a's identity exposure (79.6%) and behaviour risk (the declared hunks)
are different populations, and this counts the first.

Run it the way the suite is run, or it measures stock Django:

    . scripts/env.sh
    python -I .review/m10-measurements/m10_measure_module_blast_radius.py

``-I`` keeps the ambient environment out, which matters because this script
patches classes in the pinned tree. The paths are inserted by hand below,
because ``-I`` also drops ``PYTHONPATH``.
"""

import collections
import importlib
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")

if not os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    sys.exit("DJANGO_RUST_TEMPLATES is not 1; the shim would not be installed")

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, CHECKOUT)
# ``-I`` drops the script directory *and* the cwd from ``sys.path``, so the
# checkout's own ``tests`` directory — where ``test_sqlite`` and friends live —
# has to be put back by hand or the runner dies on ``ModuleNotFoundError``.
sys.path.insert(0, os.path.join(CHECKOUT, "tests"))

import django_rust_templates  # noqa: E402

django_rust_templates.install()

from django_rust_templates import _shim  # noqa: E402

if not _shim.installed():
    sys.exit("shim is not installed")

TARGETS = (
    "django.template.context",
    "django.template.engine",
    "django.template.utils",
    "django.template.loaders.base",
    "django.template.loaders.filesystem",
    "django.template.loaders.app_directories",
    "django.template.loaders.cached",
    "django.template.loaders.locmem",
)

for name in TARGETS:
    module = importlib.import_module(name)
    if "_impl" in (getattr(module, "__file__", "") or ""):
        print(f"NOTE: {name} is already ours", file=sys.stderr)
    else:
        print(
            f"NOTE: {name} resolved to {module.__file__}; M10 has not landed, so "
            "this measures the path a future interposition would sit on",
            file=sys.stderr,
        )

current = {"id": None}
calls = collections.defaultdict(collections.Counter)
by_test = collections.defaultdict(collections.Counter)
wrapped = []


def wrap(module_name, owner, attr, label):
    """Count calls to ``owner.attr``, attributing each to the running test.

    ``label`` is ``Class.method`` rather than a bare ``method``: ``__init__`` is
    four classes in ``context`` and six in ``loaders``, and M9's finding 3 is
    what happens when several call sites wear one label — the number looks like
    a measurement of one thing and is a sum of several.
    """
    original = getattr(owner, attr)
    if not is_function(original):
        return
    # Exact equality, not ``startswith``: ``django.template.context`` is a
    # prefix of ``django.template.context_processors``, which is a *different*
    # module with a different owner, and a prefix test would count its
    # functions here.
    if original.__module__ != module_name:
        return

    def counting(*args, **kwargs):
        calls[module_name][label] += 1
        if current["id"]:
            by_test[module_name][current["id"]] += 1
        return original(*args, **kwargs)

    counting.__name__ = attr
    setattr(owner, attr, counting)
    wrapped.append((owner, attr, original))


def is_class(obj):
    """``isinstance(obj, type)`` that survives Django's lazy proxies.

    ``django.conf.settings`` (and anything else deriving ``LazyObject``)
    answers ``isinstance`` by calling ``__class__``, which runs ``_setup()`` and
    raises ``ImproperlyConfigured`` when settings are not configured. A module's
    ``vars()`` can contain such an object, so classification has to be total
    rather than raise.
    """
    try:
        return isinstance(obj, type)
    except Exception:
        return False


def is_function(obj):
    try:
        return isinstance(obj, types.FunctionType)
    except Exception:
        return False


for name in TARGETS:
    module = importlib.import_module(name)
    for symbol_name, symbol in list(vars(module).items()):
        if is_class(symbol):
            for attr in list(vars(symbol)):
                wrap(name, symbol, attr, f"{symbol.__qualname__}.{attr}")
        elif is_function(symbol):
            wrap(name, module, symbol_name, symbol_name)

_orig_start = unittest.TestResult.startTest
_orig_stop = unittest.TestResult.stopTest


def startTest(self, test):  # noqa: N802 (unittest's own spelling)
    current["id"] = test.id()
    return _orig_start(self, test)


def stopTest(self, test):  # noqa: N802
    current["id"] = None
    return _orig_stop(self, test)


unittest.TestResult.startTest = startTest
unittest.TestResult.stopTest = stopTest

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

for owner, attr, original in wrapped:
    setattr(owner, attr, original)

print()
every_test = set()
for name in TARGETS:
    per_test = {k: v for k, v in by_test[name].items() if k}
    every_test |= set(per_test)
    print(f"=== {name}")
    print(f"    calls: {sum(calls[name].values())}")
    print(f"    tests that reached it: {len(per_test)}")
    top = ", ".join(f"{n}={c}" for n, c in calls[name].most_common(6))
    print(f"    by function: {top}")
    for test_id in sorted(per_test, key=lambda t: -per_test[t])[:5]:
        print(f"      {per_test[test_id]:6d}  {test_id}")

print()
print(f"distinct tests that reached any of the four modules: {len(every_test)}")
print(f"of 1547 in the suite")
