"""The render-annotation protocol's wiring, measured before and after the fix.

M9's Task 6 was scoped as "pin the protocol, because at M9 the nodes are Python
and it holds by transcription". It does not hold, on two attributes, in two
different places — and neither was visible to the Django suite. This script is
the measurement that says so, and the fix it produced. Run it against the tree
of the commit that adds it; the ``BEFORE``/``AFTER`` split is what a reader in
the future wants and what a rerun can no longer show.

What the protocol is: ``Node.render_annotated`` (``base.py:1043-1068``) attaches
``template_debug`` to a propagating render exception when

    context.render_context.template.origin == e._culprit_node.origin

and then calls ``Template.get_exception_info(e, culprit.token)``, which unpacks
``token.position`` and slices ``self.source``.

The two defects, both in what the shell hands the ``Template`` it builds by
``__new__``:

1. ``_native.compile_template`` passed ``origin`` to ``materialise`` (every node)
   and **not** to ``template_for`` (the ``Template``), although its own docstring
   says "every materialised node's ``node.origin``, and the ``Template.origin``
   of the tree ``template_for`` builds". So the equality compared a real
   ``Origin`` against ``None``: it never held, and ``render_string`` produced no
   ``template_debug`` at all. Invisible to the Django suite because
   ``Template.__init__`` builds the object that actually renders and the one
   ``template_for`` builds is discarded — ``compile_nodelist`` returns only its
   ``nodelist``.

2. ``template_for`` set ``source = ""`` unconditionally. With the origin fixed,
   that is no longer silence but a plausible lie: ``get_exception_info`` returns
   a full ``template_debug`` whose ``start``/``end`` are right and whose
   ``before``/``during``/``after``/``source_lines``/``line``/``total`` are empty
   or wrong.

And one defect on the other side of the parity harness, which is why the
reviewer's "the oracle will agree with you" is the right worry:

3. ``render.parser_for`` always built a plain ``Lexer`` where upstream reads
   ``self.engine.debug`` to choose ``DebugLexer`` (``base.py:183-186``). The only
   difference is ``Token.position``, and ``get_exception_info`` unpacks it — so
   ``django_render``, the Python side of ``tests/test_render_parity.py``, died
   inside its own ``except`` with ``TypeError: cannot unpack non-iterable
   NoneType object``, **replacing** the exception it was annotating. Rust lexes
   with its own lexer and materialises positioned tokens, so the Rust path never
   saw this; only a caller that parses *with* this parser does, and those are the
   parity harnesses.

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \
        python -I .review/m9-measurements/m9_measure_render_annotation_wiring.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")

if not os.environ.get("DJANGO_RUST_TEMPLATES") == "1":
    sys.exit("DJANGO_RUST_TEMPLATES is not 1; the shim would not be installed")

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, CHECKOUT)

import django_rust_templates  # noqa: E402

django_rust_templates.install()

from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(USE_I18N=False, DEBUG=False)

from django.template import Context, Engine  # noqa: E402

from django_rust_templates import render  # noqa: E402
from django_rust_templates.render import render_string  # noqa: E402

SRC = "line one\n{% for x in a %}body{% endfor %}\nline three\n"
CTX = {"a": 1}
ENGINE = Engine(debug=True)


def report(label, source, context, engine, *, origin=None, template_source=None):
    """Render through the shell's own path.

    ``origin``/``template_source`` are here to re-create the pre-fix state, which
    a rerun otherwise cannot show: ``None`` means "leave what the shell set", so
    passing neither is the tree as it is now, and passing each in turn is what
    the two defects looked like one at a time. On a fixed tree all three agree —
    that agreement is the point, not a redundancy.
    """
    template = render.parse_template(source, engine, render.UNKNOWN_ORIGIN)
    if origin is not None:
        template.origin = origin
    if template_source is not None:
        template.source = template_source
    print(f"\n=== {label} ===")
    print(f"  template.origin: {template.origin!r}")
    print(f"  node origin:     {template.nodelist[1].origin!r}")
    print(f"  equal:           {template.origin == template.nodelist[1].origin}")
    token = getattr(template.nodelist[1], "token", None)
    print(f"  token position:  {getattr(token, 'position', '<no token>')!r}")
    print(f"  template.source: {template.source!r}")
    try:
        template.render(Context(context))
        print("  rendered without raising")
    except Exception as exc:
        debug = getattr(exc, "template_debug", None)
        print(f"  raised {type(exc).__name__}: {exc}")
        if debug is None:
            print("  template_debug: ABSENT")
        else:
            for key in ("start", "end", "line", "total", "during", "after"):
                print(f"    {key}: {debug[key]!r}")


print("engine.debug:", ENGINE.debug)
print("\n########## the shell's own path (`parse_template`/`render_string`) ##########")
print("BEFORE, defect 1: template.origin printed None, `equal` printed False, and")
print("  there was no `template_debug` line at all — the exception propagated bare.")
print("BEFORE, defect 2: with only the origin restored, `template.source` printed")
print("  '', and start/end were right while line/total/during/after were empty.")
report("parse_template, as it is now", SRC, CTX, ENGINE)

print("\n########## the Rust materialised tokens carry positions ##########")
template = render.parse_template(SRC, ENGINE, render.UNKNOWN_ORIGIN)
for node in template.nodelist:
    token = getattr(node, "token", None)
    print(f"  {type(node).__name__}: {getattr(token, 'position', '<no token>')!r}")

print("\n########## the python side of the parity harness ##########")
print("BEFORE: parser_for's Lexer -> position None -> get_exception_info")
print("        dies with 'cannot unpack non-iterable NoneType object',")
print("        replacing the exception it was annotating.")


def oracle(source, context, engine):
    parser = render.parser_for(source, engine)
    nodelist = parser.parse()
    template = render.materialiser().template_for(
        nodelist, engine, parser, render.UNKNOWN_ORIGIN, source
    )
    return template.render(Context(context))


try:
    oracle(SRC, CTX, ENGINE)
except Exception as exc:
    print(f"  django_render now raises {type(exc).__name__}: {exc}")
    print(f"  template_debug keys: {sorted(getattr(exc, 'template_debug', {}))}")

print("\n########## both sides, live ##########")
for label, render_one in (("render_string", render_string), ("django_render", oracle)):
    try:
        render_one(SRC, CTX, ENGINE)
    except Exception as exc:
        print(f"  {label}: {type(exc).__name__}: {exc}")
        print(f"    template_debug: {getattr(exc, 'template_debug', None)}")
