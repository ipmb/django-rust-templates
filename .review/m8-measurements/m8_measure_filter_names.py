"""M8 recon: which registered filter names are NOT the module attribute of that name?

`defaultfilters.escape` is `django.utils.html.escape`; the filter a template
reaches by `|escape` is `defaultfilters.escape_filter`. Any measurement that
compares against `defaultfilters.<name>` is measuring a different function
wherever those two differ.

Reads the pinned tree only. Run as
`.venv/bin/python -I .review/m8-measurements/m8_measure_filter_names.py`.
"""

import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402
settings.configure(USE_I18N=False, USE_TZ=False, USE_L10N=False, DEBUG=False)
django.setup()

from django.template import defaultfilters as df  # noqa: E402

print("django %s" % django.get_version())

reg = df.register.filters
names = sorted(reg)
print("registered filters: %d" % len(names))

missing = [n for n in names if not hasattr(df, n)]
shadowed = [n for n in names if hasattr(df, n) and getattr(df, n) is not reg[n]]
plain = [n for n in names if hasattr(df, n) and getattr(df, n) is reg[n]]

print("no module attribute of that name : %d  %s" % (len(missing), missing))
print("attribute is the registered filter: %d" % len(plain))
print("attribute is a DIFFERENT object   : %d" % len(shadowed))
print()
print("== every shadowed name, both objects ==")
for n in shadowed:
    a = getattr(df, n)
    r = reg[n]
    print("  %-14s attr=%-34s reg=%s"
          % (n, getattr(a, "__qualname__", repr(a)), getattr(r, "__qualname__", repr(r))))
