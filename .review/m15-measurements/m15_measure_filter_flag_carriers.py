"""Carriers of `do_not_call_in_templates` / `alters_data` in the template layer.

M15's sixth control — `tests/test_filter_inventory.py`'s flag-inventory row — is
the one control in the set with **no failure arm**, and the reason is a claim
about a population. The first version of that claim was "0 of the 57 builtin
filters", which is true and invites the wrong question: *what about the library
filters?* A number that opens a question is a weaker instrument than one that
closes it, so this measures **every registry the template layer exposes** —
the three engine registries plus all five `django.templatetags` libraries, both
filters and tags — and reports carriers across the whole set.

Measured at Django 6.0.9: **111 callables, no carrier of either flag anywhere.**
So the row cannot be made to fire by any object these registries hold, which is
why the mutation column of `.review/m15-record.md` §5 has no entry for it and why
it stays outside the five rather than counted among them.

Two halves, because the row runs under the shim and the pinned tree is stock
Django — and a count that agrees in one tree is a count about that tree:

    # the pinned tree, stock Django
    .venv/bin/python -I .review/m15-measurements/m15_measure_filter_flag_carriers.py

    # the tree the row actually runs in
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \\
        .venv/bin/python .review/m15-measurements/m15_measure_filter_flag_carriers.py

Both print 111 and no carriers. The script prints which tree it read, so a
transcript cannot be mistaken for the other one — `django.template.base.__file__`
is the checkout's `base.py` in the first and `_impl/base.py` in the second.

Pinned tree and interpreter, named by version: `.django/` @ 6.0.9, CPython
3.14.4. The `is_safe` line is the **positive control**: it is a flag on the same
kind of object from the same population, so it says the loop is handed callables
and that `hasattr` reaches them — without it, "no carriers" is as consistent with
an empty registry as with a genuinely flagless one.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FLAGS = ("do_not_call_in_templates", "alters_data")

if str(ROOT / ".django") not in sys.path:
    # Only when the caller has not already set a tree up — the shimmed run
    # arrives with it on `PYTHONPATH` and must not be re-pointed.
    sys.path.insert(0, str(ROOT / ".django"))

import django  # noqa: E402
from django.conf import settings as django_settings  # noqa: E402

if not django_settings.configured:
    django_settings.configure(
        INSTALLED_APPS=[], DATABASES={}, USE_I18N=True, USE_TZ=True,
        STATIC_URL="/static/",
    )
django.setup()

from django.template import defaultfilters, defaulttags, loader_tags  # noqa: E402
import django.templatetags.cache as _cache  # noqa: E402
import django.templatetags.i18n as _i18n  # noqa: E402
import django.templatetags.l10n as _l10n  # noqa: E402
import django.templatetags.static as _static  # noqa: E402
import django.templatetags.tz as _tz  # noqa: E402

registries = [
    ("defaultfilters.register.filters", defaultfilters.register.filters),
    ("defaulttags.register.tags", defaulttags.register.tags),
    ("loader_tags.register.tags", loader_tags.register.tags),
]
for module in (_cache, _i18n, _l10n, _static, _tz):
    registries.append((f"{module.__name__}.register.filters", module.register.filters))
    registries.append((f"{module.__name__}.register.tags", module.register.tags))

total = 0
carriers = []
has_safe = 0
truthy_safe = 0
for name, registry in registries:
    total += len(registry)
    has_here = 0
    true_here = 0
    for key, obj in registry.items():
        if hasattr(obj, "is_safe"):
            has_here += 1
            if getattr(obj, "is_safe"):
                true_here += 1
        for flag in FLAGS:
            if hasattr(obj, flag):
                carriers.append((name, key, flag, repr(getattr(obj, flag))))
    has_safe += has_here
    truthy_safe += true_here
    print(f"{name:46s} {len(registry):4d}   is_safe: {has_here:3d} present, {true_here:3d} truthy")

print(f"{'TOTAL':46s} {total:4d}   is_safe: {has_safe:3d} present, {truthy_safe:3d} truthy")
print(f"carriers of {FLAGS!r}: {carriers!r}")
# The control is `hasattr`-shaped, not truth-shaped, because the row's subject is
# `hasattr` — so the right control number is the "present" one. And it is 57,
# which *coincides with the 57 builtin filters* while naming a different set:
# 55 of the builtins plus both of l10n's. A bare "57" here confirms a wrong
# reading, which is why the per-registry lines above are part of the claim.
print(
    f"control reading, stated so it cannot be taken as the builtin count: "
    f"is_safe is present on {has_safe} of {total} (55 of the 57 builtins plus "
    f"both of l10n's), and truthy on only {truthy_safe}"
)

import django.template.base as _base  # noqa: E402

print(f"django.template.base.__file__ = {_base.__file__!r}")
