"""Is `escape`-over-safe-input a divergence, or an artifact of the entry point?

Reads only the pinned tree and our binding. No writes.
"""
import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/.django/tests")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402
settings.configure(USE_I18N=False, USE_TZ=False, USE_L10N=False, DEBUG=False)
django.setup()

from django.template import defaultfilters as df  # noqa: E402
from django.utils.html import conditional_escape, escape as html_escape  # noqa: E402
from django.utils.safestring import mark_safe, SafeData  # noqa: E402
from django_rust_templates import _native  # noqa: E402

print("django %s" % django.get_version())
print()

print("== which object is which ==")
print("df.escape is html_escape          :", df.escape is html_escape)
print("df.escape_filter is html_escape   :", df.escape_filter is html_escape)
print("df.escape is conditional_escape   :", df.escape is conditional_escape)
print("df.escape_filter is cond_escape   :", df.escape_filter is conditional_escape)
reg = df.register.filters["escape"]
print("register['escape'] is df.escape_filter:", reg is df.escape_filter)
print("register['escape'] is html_escape :", reg is html_escape)
print()

print("== the registered filter, stock vs ours, over safe and plain input ==")
CASES = [
    ("plain", "a & b"),
    ("safe", mark_safe("a & b")),
]
for fname in ("escape", "force_escape", "safe"):
    stock = df.register.filters[fname]
    for label, value in CASES:
        s = stock(value)
        try:
            o = _native.filter(fname, value, ())
        except Exception as e:
            o = "%s: %s" % (type(e).__name__, e)
        same = (s == o) and (isinstance(s, SafeData) == isinstance(o, SafeData))
        print("  %-12s %-5s stock=%-14r ours=%-14r  %s"
              % (fname, label, str(s), str(o), "same" if same else "DIFFERS"))
print()

print("== and the two functions the name could have meant ==")
for label, value in CASES:
    print("  %-5s html_escape=%-14r conditional_escape=%r"
          % (label, str(html_escape(value)), str(conditional_escape(value))))
