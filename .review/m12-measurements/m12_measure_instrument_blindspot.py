"""Does the routing instrument's verdict flip on `__file__` alone?

`m12_measure_tag_routing.py` decides `ours/ours` from `owner()`, which reads
`sys.modules[obj.__module__].__file__` — so it answers *whose file defines the
callable*, not *whose code ran*. This is the measurement behind the claim in
M12 Task 6a's brief that interposing `django.templatetags.i18n` flips all ten of
its names at once whether or not a Rust body exists, and therefore that the
plan's "2/16 after 6a, 0/18 after 6b" is a count of *Rust bodies* mislabelled as
this instrument's tally.

Run the instrument's own `owner()` over the ten registered i18n names twice:
once as shipped, and once with `__file__` merely repointed at an `_impl` path.
Nothing is interposed and nothing is written — only the attribute the instrument
reads is changed, which is the point: if the verdict flips on that one attribute
then the verdict is a statement about the attribute.

Measured at `a6a11eb`: **10 of 10** flip, `ours=False` → `ours=True`.

`.review/` is outside the anchor gate's `TREE_ROOTS`, so the line numbers this
prints are prose, not anchors.

    . scripts/env.sh
    .venv/bin/python .review/m12-measurements/m12_measure_instrument_blindspot.py
"""

import pathlib
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / ".django"))

from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(USE_I18N=True)

import django  # noqa: E402

django.setup()


def owner(obj):
    """Verbatim from `.review/m12-measurements/m12_measure_tag_routing.py`."""
    module = getattr(obj, "__module__", "?")
    file = getattr(sys.modules.get(module), "__file__", "") or ""
    return module, "_impl" in file


import django.templatetags.i18n as i18n  # noqa: E402

names = sorted(i18n.register.tags)
real_file = i18n.__file__
print("module      :", real_file)
print()

print("== as shipped")
for name in names:
    module, ours = owner(i18n.register.tags[name])
    print(f"   {name:<28} module={module:<26} ours={ours}")

i18n.__file__ = str(ROOT / "python/django_rust_templates/_impl/templatetags/i18n.py")
print()
print("== with __file__ repointed at _impl (nothing else changed)")
flipped = 0
for name in names:
    module, ours = owner(i18n.register.tags[name])
    flipped += ours
    print(f"   {name:<28} module={module:<26} ours={ours}")
print()
print(f"verdict flips on __file__ alone for {flipped} of {len(names)} names")
i18n.__file__ = real_file
