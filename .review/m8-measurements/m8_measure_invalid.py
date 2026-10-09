"""M8 recon: the `string_if_invalid` substitution — where Django reads it, what
the loop does with it, and what an *eager* read would cost.

Three questions:
  1. does the substitution continue the bit loop, or end it;
  2. what does the read do on a context with no template bound (the case an
     eager read turns into a failure of the whole lookup);
  3. does the substitution site re-read the attribute, or read it once.

Run as `.venv/bin/python -I .review/m8-measurements/m8_measure_invalid.py`.

Django's control flow is `base.py:957-1028`: the assignment
`current = context.template.engine.string_if_invalid` occurs at `:996`
(`alters_data`), `:1002` (`signature()` raised `ValueError`) and `:1009`
(`bind()` raised `TypeError`) — all **inside** `for bit in self.lookups:` and
all inside the outer `try:` at `:957`. So the attribute is read once per
substitution, at the substitution site, and a failure of that read is caught by
the outer handler at `:1015`, which re-raises it (no `silent_variable_failure`).
"""

import sys
import unittest

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/.django/tests")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(
    USE_I18N=False,
    USE_TZ=False,
    USE_L10N=False,
    DEBUG=False,
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates"}],
)
django.setup()

from django.template import Context, Engine, Template  # noqa: E402
from django.template.base import Variable  # noqa: E402

INVALID = "<<invalid>>"


def outcome(fn):
    try:
        return ("value", fn())
    except Exception as e:  # noqa: BLE001
        return ("raised", type(e).__name__, str(e))


class Alters:
    """`alters_data` on the *class body* of a callable instance.

    The instance itself is the resolved object, so a class attribute is visible
    — recon §4's rule, which is the spelling that works here.
    """

    alters_data = True
    calls = 0

    def __call__(self):
        type(self).calls += 1
        return "SIDE EFFECT"

    def upper(self):
        return "NOT THE STRING ONE"

    name = "ada"


class NeedsOneArg:
    alters_data = True

    def __call__(self, a):
        return a


engine = Engine(string_if_invalid=INVALID)
bound = Context({"x": Alters()})
with bound.bind_template(Template("", engine=engine)):
    print("django %s" % django.get_version())
    print()
    print("== (1) the substitution continues the loop (`base.py:996`) ==")
    print("  string_if_invalid=%r, x.alters_data=True on a callable instance" % INVALID)
    for src in ("{{ x }}", "{{ x.upper }}", "{{ x.name }}"):
        path = src.removeprefix("{{ ").removesuffix(" }}")
        rendered = outcome(lambda src=src: Template(src, engine=engine).render(bound))
        print("    %-14s render -> %r" % (src, rendered))
        print("    %-14s lookup -> %r" % ("", outcome(
            lambda path=path: Variable(path)._resolve_lookup(bound))))
    print()

    print("== (2) the read is not eager: an unbound context ==")
    unbound = Context({"x": "plain"})
    print("  context.template                      = %r" % (unbound.template,))
    print("  .template.engine.string_if_invalid    -> %r" % (outcome(
        lambda: unbound.template.engine.string_if_invalid),))
    print("  Variable('x')._resolve_lookup(unbound) -> %r" % (outcome(
        lambda: Variable("x")._resolve_lookup(unbound)),))
    print("  -> a plain value never reaches the read; an EAGER read would raise")
    print("     here, on the most ordinary template there is.")
    print()
    print("  and where the substitution IS reached, unbound:")
    for label, value in (("x.alters_data", Alters()), ("x needs an arg", NeedsOneArg())):
        u = Context({"x": value})
        print("    %-16s lookup -> %r" % (label, outcome(
            lambda u=u: Variable("x")._resolve_lookup(u))))
    print()

    print("== (3) the output-blindness Review Focus 5 names ==")
    silent_engine = Engine(string_if_invalid="")
    ctx = Context({"x": Alters()})
    with ctx.bind_template(Template("", engine=silent_engine)):
        before = Alters.calls
        got = outcome(lambda: Variable("x")._resolve_lookup(ctx))
        print("  string_if_invalid='', x.alters_data=True, __call__ -> 'SIDE EFFECT'")
        print("    lookup -> %r   calls delta = %d" % (got, Alters.calls - before))
        print("    -> ('value', '') whether or not the callable ran: the")
        print("       substitution HIDES the call, so no parity row can see it.")
        print("       Reaching this row with calls > 0 is the substitution failing.")
