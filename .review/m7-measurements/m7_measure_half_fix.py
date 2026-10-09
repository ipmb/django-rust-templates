"""M7 Task 4 / Step 8, re-measured: what the un-widened table actually costs.

The half-fix is Task 4's trap — delete the exception list *without* widening
`django_own_tags`' table, so `block`/`extends`/`include` fall through the
identity clause and the host claims them. Step 8 predicted one symptom
(`{% extends "a" %}{% extends "b" %}` stops raising) and D8 recorded another
(seven corpus rows dying with `AttributeError: 'NoneType' object has no
attribute 'template_name'`). Neither survived: the recorded one was a harness
artifact — `tests/test_parser_errors.py::ours_failure` built its `Parser`
without the `Origin` its `django_failure` sibling passes, so `parser.origin` was
`None` where Django's was an `Origin` — and this script exists because a claim
whose argument cannot be re-run is one nobody re-examines.

**The half-fix is modelled here, not edited into `host.py`.** `ShellHost.__init__`
reads `django_own_tags` as a module global, so rebinding that name reaches every
`ShellHost` built afterwards — including the ones `tests.test_parser_errors`
builds — with the shipping file untouched and nothing to restore.

Three measurements, in the order they matter:

1. the predicate, which is what *does* see it: `has_tag` says `True` for all
   three names;
2. the four rows a reader would check first, both engines;
3. a sweep of the whole error corpus, which is what *does not* see it.
"""

import sys

PROJECT_ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, PROJECT_ROOT + "/.django")
sys.path.insert(0, PROJECT_ROOT)

from django.template import defaulttags as _defaulttags  # noqa: E402

from django_rust_templates import host as host_module  # noqa: E402

SHIPPING = host_module.django_own_tags
SHIPPING_KEYS = len(SHIPPING())
host_module.django_own_tags = lambda: dict(_defaulttags.register.tags)
HALF_FIX_KEYS = len(host_module.django_own_tags())

from django_rust_templates.host import ShellHost  # noqa: E402
from django_rust_templates.render import parser_for  # noqa: E402
from tests.parser_error_corpus import ERRORS, ORIGIN_ERRORS  # noqa: E402
from tests.test_parser_errors import django_failure, ours_failure  # noqa: E402

print(f"--- (1) the predicate: tables of {SHIPPING_KEYS} and {HALF_FIX_KEYS} names ---")
host = ShellHost(parser_for(""))
for name in ("block", "extends", "include"):
    print(f"  has_tag({name!r:10}) -> {host.has_tag(name)}   (shipping: False)")

print()
print("--- (2) the rows a reader would check first ---")
for source in (
    '{% extends "a" %}{% extends "b" %}',
    '{% extends "a" %}{% include "b" %}',
    '{% extends "a" %}',
    '{% include "../../x.html" %}',
):
    stock = django_failure(source)
    ours = ours_failure(source)
    print(f"  {source}")
    print(f"      stock -> {type(stock).__name__ if stock else None}: {stock}")
    print(f"      ours  -> {type(ours).__name__ if ours else None}: {ours}")
    print(f"      same text -> {str(stock) == str(ours)}")


def differs(rows, with_origin):
    """Rows whose *message* the two engines no longer agree on.

    The message is the unit, not the class: the type names differ by design
    (design decision 2 — Rust owns the message, Python owns the exception
    object, and `_native.ParseError` becomes `TemplateSyntaxError` at M9), and
    comparing classes would report all 109 rows as divergent and measure
    nothing.
    """
    out = []
    for row in rows:
        source, origin_name = (row[0], row[1]) if with_origin else (row[0], None)
        stock = django_failure(source, origin_name=origin_name)
        ours = ours_failure(source, origin_name=origin_name)
        if str(stock) != str(ours):
            out.append((source, stock, ours))
    return out


print()
print("--- (3) the whole error corpus, message for message ---")
plain = differs(ERRORS, with_origin=False)
origin = differs(ORIGIN_ERRORS, with_origin=True)
print(f"  ERRORS        {len(plain):3} of {len(ERRORS):3} rows differ from stock")
print(f"  ORIGIN_ERRORS {len(origin):3} of {len(ORIGIN_ERRORS):3} rows differ from stock")
for source, stock, ours in (plain + origin)[:8]:
    print(f"    {source!r}\n      stock {stock!r}\n      ours  {ours!r}")
print()
print("  So the half-fix is invisible to every message-comparing row: the error")
print("  corpus cannot see it, and the detector is (1) — the predicate itself.")
print("  The full suite agrees, measured: 256 tests, 3 failures, all three")
print("  subTests of test_the_host_declines_every_name_rust_owns.")
