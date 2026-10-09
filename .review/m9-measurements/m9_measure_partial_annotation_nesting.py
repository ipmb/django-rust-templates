"""Is the by-calling-frame breakdown of ``get_exception_info`` counting frames or annotations?

`m9_measure_render_blast_radius.py` reported:

    340  compile_nodelist
     84  render_annotated
      6  get_exception_info

and the fix that produced it was replacing a single "430 annotations" with a
breakdown by call site. The reviewer observed that the repair may carry the same
defect one level in: ``base.py:310-312`` is a **delegation**,

    def get_exception_info(self, exception, token):          # PartialTemplate
        template = self.origin.loader.get_template(self.origin.template_name)
        return template.get_exception_info(exception, token)

so one partial-template annotation produces **two** calls — an outer one
attributed to `compile_nodelist` or `render_annotated`, and an inner one
attributed to `get_exception_info`. If that is what is happening, the three
numbers sum to 430 *frames* over 424 *annotations*, and the 6 are six of the
340/84 counted a second time.

The check the reviewer named: for each call attributed to ``get_exception_info``,
does the stack also contain a ``compile_nodelist`` or ``render_annotated`` frame?
If yes, the breakdown is of call sites and the annotation count is 424.

Run it the same way the suite is run, because the count is meaningless otherwise:

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        python -I .review/m9-measurements/m9_measure_partial_annotation_nesting.py
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

ORIGIN = {204: "compile_nodelist", 1063: "render_annotated", 312: "get_exception_info"}

frames_by_caller = collections.Counter()
nested = collections.Counter()
outer_for_inner = collections.Counter()
stacks = []

_original_info = base.Template.get_exception_info


def stack_names(depth=200):
    """The co_name of every frame between the wrapper and the test runner.

    ``depth`` is 200 rather than 25 because the first run of this script used 25
    and reported two calls with "no annotating frame" on the stack — which is
    exactly the shape of a window that is too small rather than a fact about the
    tree. A window that decides the answer is not a measurement.
    """
    names = []
    frame = sys._getframe(1)
    for _ in range(depth):
        if frame is None:
            break
        names.append(frame.f_code.co_name)
        frame = frame.f_back
    return names


def counting_info(self, exception, token):
    caller = sys._getframe(1).f_code.co_name
    frames_by_caller[caller] += 1
    if caller == "get_exception_info":
        names = stack_names()
        # The wrapper's own caller is the delegating PartialTemplate method; the
        # question is whether an annotating frame sits further out.
        for enclosing in ("compile_nodelist", "render_annotated"):
            if enclosing in names:
                nested[enclosing] += 1
                outer_for_inner[enclosing] += 1
                break
        else:
            # Not merely "no annotating frame" — name what *is* there, so the
            # reader can see whether the window was the problem.
            nested["<no annotating frame>"] += 1
            stacks.append(" <- ".join(names[1:8]))
    return _original_info(self, exception, token)


base.Template.get_exception_info = counting_info

_orig_start = unittest.TestResult.startTest
_orig_stop = unittest.TestResult.stopTest


def startTest(self, test):  # noqa: N802
    return _orig_start(self, test)


def stopTest(self, test):  # noqa: N802
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

print()
print("get_exception_info calls, by calling frame:")
for caller, count in frames_by_caller.most_common():
    print(f"  {count:5d}  {caller}")

print()
print("calls attributed to ``get_exception_info`` (= the :312 delegation), by")
print("whether an annotating frame is also on the stack:")
for enclosing, count in nested.most_common():
    print(f"  {count:5d}  enclosing: {enclosing}")

total_frames = sum(frames_by_caller.values())
duplicates = sum(outer_for_inner.values())
print()
print(f"total frames: {total_frames}")
print(f"of which nested inside an annotating frame: {duplicates}")
print(f"distinct annotations (frames minus nested): {total_frames - duplicates}")
print()
print("stacks of the non-nesting :312 calls (skipping our wrapper frame):")
for stack in stacks:
    print(f"  {stack}")
