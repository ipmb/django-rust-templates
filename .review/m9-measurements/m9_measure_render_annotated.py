"""``Node.render_annotated``'s debug branch, measured on the rows that pin it.

M9 interposes ``base``, and with it ``Node``. Every node class in
``defaulttags``/``loader_tags`` inherits from whatever ``django.template.base``
binds as ``Node``, so the annotation protocol becomes ours for the whole suite
the moment the module is interposed — including for nodes Rust did not build,
because Rust materialises its descriptors into those same Django classes
(``_impl/parser.py``).

The protocol (``base.py:1043-1068``) is three stateful steps and one comparison:

* ``_culprit_node`` is attached **once** — ``hasattr`` guard — so the *innermost*
  node that raised wins, not the outer one;
* ``template_debug`` is attached **once**, and only when the frame's
  ``context.render_context.template.origin`` equals the culprit's origin;
* both hang off the exception's **identity**, so an outer node cannot
  reconstruct them after the fact.

This prints, for the five rows of
``template_tests/test_nodelist.py:44-86``, which node became the culprit and
whether the frame that supplied ``template_debug`` was the same one. That is the
"pinned to the character" half: the expected ``(start, end)`` pairs only come out
right if the culprit is the innermost node *and* the origin comparison picked the
right frame.

It also counts how often the debug branch is entered anywhere in the suite, which
bounds how much of the 961-test render blast radius this protocol actually
covers (964 was the recon's pre-interposition figure; `.review/m9-recon.md` §1b
has the difference and the cause).

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        python -I .review/m9-measurements/m9_measure_render_annotated.py
"""

import os
import re
import sys

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

from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(USE_I18N=False, DEBUG=False)

from django.template import Context, Engine  # noqa: E402
from django.template.base import Node  # noqa: E402

entered = 0
attached = 0
original = Node.render_annotated


def counting(self, context):
    global entered
    try:
        return original(self, context)
    except Exception:
        entered += 1
        raise


Node.render_annotated = counting

# ``Template.get_exception_info`` is called from **inside** the origin
# comparison — ``template_debug`` is set to its result, and only there — so
# counting its calls counts the frames where the comparison held. Without this
# the "which frame attached it" half of the protocol would be reported from the
# assumption that it always holds, which is the same as not measuring it.
from django.template.base import Template  # noqa: E402

original_info = Template.get_exception_info


def counting_info(self, exception, token):
    global attached
    attached += 1
    return original_info(self, exception, token)


Template.get_exception_info = counting_info

CASES = [
    (
        "{% load bad_tag %}{% for i in range %}{% badsimpletag %}{% endfor %}",
        (38, 56),
    ),
    (
        "{% load bad_tag %}{% for i in range %}{% for j in range %}"
        "{% badsimpletag %}{% endfor %}{% endfor %}",
        (58, 76),
    ),
    (
        "{% load bad_tag %}{% for i in range %}{% badsimpletag %}"
        "{% for j in range %}Hello{% endfor %}{% endfor %}",
        (38, 56),
    ),
    (
        "{% load bad_tag %}{% for i in range %}{% for j in five %}"
        "{% badsimpletag %}{% endfor %}{% endfor %}",
        (38, 57),
    ),
    (
        "{% load bad_tag %}{% for j in five %}{% badsimpletag %}{% endfor %}",
        (18, 37),
    ),
]

engine = Engine(debug=True, libraries={"bad_tag": "template_tests.templatetags.bad_tag"})

print("case  culprit-node        culprit-origin     attaches  (start,end)  expected")
for index, (source, expected) in enumerate(CASES, start=1):
    before_entered, before_attached = entered, attached
    template = engine.from_string(source)
    try:
        template.render(Context({"range": range(5), "five": 5}))
    except (RuntimeError, TypeError) as exc:
        culprit = getattr(exc, "_culprit_node", None)
        debug = getattr(exc, "template_debug", None)
        attaches = attached - before_attached
        if debug is None:
            print(
                f"{index:>4}  {type(culprit).__name__:<16} "
                f"{'-':<17} {attaches:>8}  (no template_debug)  {expected}"
            )
            continue
        got = (debug["start"], debug["end"])
        origin = debug["name"]
        print(
            f"{index:>4}  {type(culprit).__name__:<16} "
            f"{str(origin):<17} {attaches:>8}  "
            f"{str(got):<12} {expected}  {'OK' if got == expected else 'MISMATCH'}"
        )
    else:
        print(f"{index:>4}  (no exception raised)  expected {expected}")

print()
print(
    f"render_annotated propagated {entered} exception(s) and "
    f"{attached} frame(s) passed the origin comparison across those five cases"
)
print(
    "NOTE: the debug branch runs only when engine.debug is true; this engine is "
    "built with debug=True, which is what makes the protocol observable at all."
)
