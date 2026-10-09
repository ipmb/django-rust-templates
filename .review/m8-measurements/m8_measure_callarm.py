"""M8: Django's callable arm, executed rather than read.

`base.py:993-1014` is three nested trys whose innermost `else` is a bare
`raise`, which lands in the *outer* `except Exception` at `:1016` — where
`silent_variable_failure` is consulted. The plan has to reproduce that, so the
fixture table it uses is derived here by running stock Django, not by reading
the source.

Every case is rendered through a real engine (`{{ x }}`), with
`string_if_invalid` set to a sentinel so "the substitution happened" is
distinguishable from "the value was empty".

Run as `.venv/bin/python -I .review/m8-measurements/m8_measure_callarm.py`.
"""

import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(USE_I18N=False, USE_TZ=False, USE_L10N=False, DEBUG=False)
django.setup()

from django.template import Context, Engine, Template  # noqa: E402

INVALID = "<<invalid>>"
engine = Engine(string_if_invalid=INVALID)
print("django %s   string_if_invalid=%r" % (django.get_version(), INVALID))
print()


# --- the fixtures, one per branch the arm can take -------------------------
def no_args():
    return "NO_ARGS"


def needs_args(a):
    return "NEEDS_ARGS"


def no_signature():
    raise AssertionError("unused")


class CallableInstance:
    def __call__(self):
        return "INSTANCE_CALLED"


def raises_type_error():
    raise TypeError("boom from the body")


class Silent:
    """A callable whose TypeError carries `silent_variable_failure`."""

    def __call__(self):
        exc = TypeError("silent boom")
        exc.silent_variable_failure = True
        raise exc


CASES = [
    ("no-arg function", no_args),
    ("needs one arg", needs_args),
    ("callable instance", CallableInstance()),
    ("body raises TypeError", raises_type_error),
]

print("== the four branches ==")
for label, value in CASES:
    src = "{{ x }}"
    try:
        got = Template(src, engine=engine).render(Context({"x": value}))
        shown = repr(got)
    except Exception as e:
        shown = "RAISED %s: %s" % (type(e).__name__, e)
    print("  %-24s -> %s" % (label, shown))

# The `signature()` ValueError branch needs an object `inspect.signature`
# refuses; the re-raise branch needs a callable that raises TypeError while
# binding zero args successfully, which `raises_type_error` is.
print()
print("== the TypeError re-raise, and silent_variable_failure ==")
for label, value in [("bare TypeError", raises_type_error), ("silent", Silent())]:
    try:
        got = Template("{{ x }}", engine=engine).render(Context({"x": value}))
        print("  %-24s -> %r" % (label, got))
    except Exception as e:
        print("  %-24s -> RAISED %s: %s" % (label, type(e).__name__, e))

print()
print("== and with string_if_invalid unset (the default '') ==")
plain = Engine()
for label, value in [("needs one arg", needs_args), ("bound method", CallableInstance().__call__)]:
    try:
        got = Template("{{ x }}", engine=plain).render(Context({"x": value}))
        print("  %-24s -> %r" % (label, got))
    except Exception as e:
        print("  %-24s -> RAISED %s: %s" % (label, type(e).__name__, e))
