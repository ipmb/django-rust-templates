"""How many of the 1547 tests put ``NodeList.render`` on their path, and why.

M9 interposes ``django.template.base``, which is the module ``NodeList``,
``Node`` and ``Variable`` live in. The milestone is the risky one because the
blast radius is *not* the tests that name templates: ``setup_test_environment``
replaces ``Template._render`` with ``instrumented_test_render``
(``django/test/utils.py:108-114``), whose whole body is

    template_rendered.send(sender=self, template=self, context=context)
    return self.nodelist.render(context)

so **every** ``template.render()`` inside the suite — including the ones in
Django's own view, form and admin tests — goes through ``NodeList.render``. This
counts them, rather than arguing from the source.

Run it the same way the suite is run, because the count is meaningless
otherwise: without ``DJANGO_RUST_TEMPLATES=1`` and ``shimpath`` on
``PYTHONPATH`` the shim is never installed and this measures stock Django.

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        python -I .review/m9-measurements/m9_measure_render_blast_radius.py

``-I`` keeps the ambient environment out, which matters because this script
patches a class in the pinned tree.
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
# ``-I`` drops the script directory *and* the cwd from ``sys.path``, so the
# checkout's own ``tests`` directory — where ``test_sqlite`` and friends live —
# has to be put back by hand. Without it the runner dies on
# ``ModuleNotFoundError: No module named 'test_sqlite'``, which looks like a
# broken checkout rather than a missing path entry.
sys.path.insert(0, os.path.join(CHECKOUT, "tests"))

import django_rust_templates  # noqa: E402

django_rust_templates.install()

from django_rust_templates import _shim  # noqa: E402

if not _shim.installed():
    sys.exit("shim is not installed")

# A pre-flight, because this whole measurement is about *our* ``base``: if
# ``django.template.base`` still resolves to Django's own file, the counter
# below counts calls on the wrong class and the answer is about stock Django.
import django.template.base as base  # noqa: E402

if "_impl" not in (base.__file__ or ""):
    print(
        f"NOTE: django.template.base resolved to {base.__file__}; M9 has not "
        "landed, so this measures the path a future interposition would sit on",
        file=sys.stderr,
    )

current = {"id": None}
renders = collections.Counter()
annotations = collections.Counter()
originals = {}

for cls_name in ("NodeList",):
    cls = getattr(base, cls_name)
    originals[cls_name] = cls.render

    def make(original):
        def render(self, context):
            renders[current["id"]] += 1
            return original(self, context)

        return render

    cls.render = make(originals[cls_name])

# The other half of the same interposition: ``base.Node`` carries the
# render-time annotation protocol.
#
# ``Template.get_exception_info`` has **three** call sites, not one
# (``base.py:204`` compile-time, ``base.py:312`` the ``PartialTemplate``
# delegation, ``base.py:1063`` the render-time protocol), so counting it plainly
# answers a different question than the one asked — a first draft of this
# measurement reported "430 annotations across 192 tests" and that number was
# three call sites wearing one label. The caller's frame name separates them, and
# both figures are printed rather than only the wanted one.
_original_info = base.Template.get_exception_info

annotations = collections.Counter()
annotations_by_caller = collections.Counter()


def counting_info(self, exception, token):
    caller = sys._getframe(1).f_code.co_name
    annotations_by_caller[caller] += 1
    if caller == "render_annotated":
        annotations[current["id"]] += 1
    return _original_info(self, exception, token)


base.Template.get_exception_info = counting_info

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
except SystemExit as exc:
    code = exc.code

per_test = {k: v for k, v in renders.items() if k is not None}
total = sum(per_test.values())
print()
print(f"NodeList.render calls: {total}")
print(f"distinct tests that rendered at least once: {len(per_test)}")
top = sorted(per_test.items(), key=lambda kv: -kv[1])[:10]
for name, count in top:
    print(f"  {count:5d}  {name}")

per_test_annotations = {k: v for k, v in annotations.items() if k is not None}
print()
print("Template.get_exception_info calls, by calling frame:")
for caller, count in annotations_by_caller.most_common():
    print(f"  {count:5d}  {caller}")
print(
    "render-time annotations (caller ``render_annotated``): "
    f"{sum(per_test_annotations.values())} across {len(per_test_annotations)} test(s)"
)
for name in sorted(per_test_annotations):
    print(f"  {per_test_annotations[name]:5d}  {name}")
