"""``render_value_in_context``, sized by running the suite and counting.

M9's Task 4 pins ``django.template.base.render_value_in_context`` — a helper, not
a class, so the blast radius is not "the templates that render" but *every value
that reaches output*, once per interpolated value rather than once per render.
The count is what says whether the row is worth its maintenance.

It is not in ``m9_measure_render_blast_radius.py``, which counts ``NodeList``
renders, ``Template.compile_nodelist`` and the render-time annotation protocol —
three different questions, and adding a fourth to that file would make its
numbers mean four things.

**The metric, named before it is printed**, because this function has more than
one call site and "N calls" is ambiguous otherwise:

* the unit is a **call**, not a line, not a template;
* the scope is the whole ``template_tests`` run, the same one ``check.sh``
  accepts;
* calls are attributed **by the calling frame**, so ``FilterExpression.resolve``
  and ``VariableNode.render`` are printed separately rather than summed into a
  figure that hides which path is doing the work;
* the counter wraps ``base.render_value_in_context`` **as the shimmed module
  exposes it**, so what is counted is the object the render path actually calls;
  if the shim were not installed this would count Django's own function and the
  answer would be about stock Django — hence the pre-flight, as in the sibling
  script.

Run it the same way the suite is run:

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        python -I .review/m9-measurements/m9_measure_render_value_blast_radius.py
"""

import collections
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")

if not os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    sys.exit("DJANGO_RUST_TEMPLATES is not 1; the shim would not be installed")

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, CHECKOUT)
sys.path.insert(0, os.path.join(CHECKOUT, "tests"))

import django_rust_templates  # noqa: E402

django_rust_templates.install()

from django_rust_templates import _shim  # noqa: E402

if not _shim.installed():
    sys.exit("shim is not installed")

import django.template.base as base  # noqa: E402

if "_impl" not in (base.__file__ or ""):
    sys.exit(
        f"django.template.base resolved to {base.__file__}; the count below "
        "would be about stock Django, not about our transcription"
    )

# ``_impl/base.py`` calls it by module-global name, so patching the attribute is
# what redirects the render path's calls.
_original = base.render_value_in_context
current = {"id": None}
calls = collections.Counter()
callers = collections.Counter()


def counting_render_value_in_context(value, context):
    caller = sys._getframe(1).f_code.co_name
    callers[caller] += 1
    calls[current["id"]] += 1
    return _original(value, context)


base.render_value_in_context = counting_render_value_in_context

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
finally:
    base.render_value_in_context = _original

per_test = {k: v for k, v in calls.items() if k is not None}
print()
print("render_value_in_context calls, by calling frame:")
for caller, count in callers.most_common():
    print(f"  {count:6d}  {caller}")
print(f"total calls: {sum(per_test.values())}")
print(f"distinct tests that called it at least once: {len(per_test)}")
