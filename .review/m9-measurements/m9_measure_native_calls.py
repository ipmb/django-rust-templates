"""Does the accepted suite call ``_native`` at all, and which entry points?

``scripts/check.sh``'s Django step carries a comment that was true on
2026-10-09 and may not be now: "Not '(rust engine)' until M9 interposes
``base`` — measured 2026-10-09, this suite calls ``_native`` zero times, so the
label would assert what the run does not do."

Task 7 Step 7 says to re-measure that claim rather than reason about it, because
the whole point of M9 is that interposing ``base`` and ``smartif`` puts the
suite on our code *without* touching a test. The number this prints is the
difference between "the suite exercises Rust" and "the suite imports Rust".

``lex`` and ``parse`` are also counted, because they are what a reader will mean
by "the parse seam": ``base.Lexer`` still lexes in Python at M9, so a nonzero
``lex`` would be surprising and is worth seeing rather than assuming.

Every public callable is wrapped; types are left alone, since wrapping a
constructor tells you nothing a call site does not. The Rust code calls its own
functions directly, so the wrappers see only Python-side calls — which is
exactly the question.

Run it the way the suite is run, or it measures stock Django:

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        python -I .review/m9-measurements/m9_measure_native_calls.py

``-I`` keeps the ambient environment out, which matters because this script
patches an extension module's attributes.
"""

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")

if not os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    sys.exit("DJANGO_RUST_TEMPLATES is not 1; the shim would not be installed")

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, CHECKOUT)
# ``-I`` drops the script directory *and* the cwd from ``sys.path``, so the
# checkout's own ``tests`` directory has to be put back by hand (``test_sqlite``
# and friends live there and the runner imports them).
sys.path.insert(0, os.path.join(CHECKOUT, "tests"))

import django_rust_templates  # noqa: E402

django_rust_templates.install()

from django_rust_templates import _native, _shim  # noqa: E402

if not _shim.installed():
    sys.exit("shim is not installed")

import django.template.base as base  # noqa: E402

if "_impl" not in (base.__file__ or ""):
    print(
        f"NOTE: django.template.base resolved to {base.__file__}; M9 has not "
        "landed, so this measures the path a future interposition would sit on",
        file=sys.stderr,
    )

calls = collections.Counter()
by_test = collections.defaultdict(collections.Counter)
current = {"id": None}
wrapped = []

for name in sorted(vars(_native)):
    original = getattr(_native, name)
    if not callable(original) or isinstance(original, type):
        continue

    def make(original, name):
        def counting(*args, **kwargs):
            calls[name] += 1
            by_test[current["id"]][name] += 1
            return original(*args, **kwargs)

        counting.__name__ = name
        return counting

    wrapped.append((name, original))
    setattr(_native, name, make(original, name))

import unittest  # noqa: E402

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

for name, original in wrapped:
    setattr(_native, name, original)

print()
print(f"_native calls: {sum(calls.values())}")
for name, count in calls.most_common():
    print(f"  {count:8d}  {name}")

per_test = {k: v for k, v in by_test.items() if k is not None}
print()
print(f"distinct tests that called _native at least once: {len(per_test)}")
print(f"distinct _native entry points reached: {len(calls)} of {len(wrapped)}")
print()
print("entry points reached by no test:")
for name, _ in wrapped:
    if not calls[name]:
        print(f"  {name}")
