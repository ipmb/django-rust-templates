"""M8: does the `alters_data` substitution *continue* the bit loop or stop it?

Django's callable arm is inside `for bit in self.lookups:` (`base.py:959`), and
the `alters_data` branch assigns `current = context.template.engine.
string_if_invalid` (`:996`) without leaving the loop. So the *next* bit is
resolved against the invalid value. If that is true, an input exists that
separates "substitute and keep walking" from "substitute and stop":

    {{ x.upper }}   with x.alters_data = True and string_if_invalid="<<invalid>>"
      keep walking -> the remaining bit `.upper` runs on the invalid string
      stop          -> the invalid string itself

Stock Django is the oracle. Run as
`.venv/bin/python -I .review/m8-measurements/m8_measure_alters.py`.
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

print("django %s" % django.get_version())
print()


class Alters:
    alters_data = True

    def __call__(self):
        return "CALLED"

    def name(self):
        return "ada"


def run(src, value, invalid):
    engine = Engine(string_if_invalid=invalid)
    try:
        return repr(Template(src, engine=engine).render(Context({"x": value})))
    except Exception as e:
        return "RAISED %s: %s" % (type(e).__name__, e)


for invalid in ("", "<<invalid>>"):
    print("== string_if_invalid=%r ==" % invalid)
    for src in ("{{ x }}", "{{ x.upper }}", "{{ x.name }}", "{{ x.missing }}"):
        print("  %-16s -> %s" % (src, run(src, Alters(), invalid)))
    print()

print("== the same shape for a callable that just needs args ==")


def needs_args(a):
    return a


for invalid in ("", "<<invalid>>"):
    engine = Engine(string_if_invalid=invalid)
    for src in ("{{ x }}", "{{ x.upper }}"):
        try:
            got = repr(Template(src, engine=engine).render(Context({"x": needs_args})))
        except Exception as e:
            got = "RAISED %s: %s" % (type(e).__name__, e)
        print("  invalid=%-12r %-16s -> %s" % (invalid, src, got))
