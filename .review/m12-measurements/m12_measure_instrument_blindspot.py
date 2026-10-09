"""Is the routing instrument's verdict a function of `__file__` and nothing else?

`m12_measure_tag_routing.py` decides `ours/ours` from `owner()`, which reads
`sys.modules[obj.__module__].__file__` — so it answers *whose file defines the
callable*, not *whose code ran*. This is the measurement behind the claim in
M12 Task 6a's brief that interposing `django.templatetags.i18n` flips all ten of
its names at once whether or not a Rust body exists, and therefore that the
plan's "2/16 after 6a, 0/18 after 6b" is a count of *Rust bodies* mislabelled as
this instrument's tally.

It runs the instrument's own `owner()` over the ten registered i18n names three
times — as shipped, with `__file__` pointed at an `_impl` path, and with
`__file__` pointed at a path that merely *lacks* `_impl` — and reports, per
name, whether the verdict changed. Nothing is imported, interposed or written:
only the one attribute the instrument reads is moved, which is the point. If the
verdict follows that attribute in **both** directions then the verdict is a
statement about the attribute, whatever the module happens to be.

**The three passes and not two, because of a bug two passes hide.** The first
version of this script counted `ours=True` in the repointed pass and printed it
as "flips". That reads 10 of 10 only while the module is *not* yet ours: once
`_impl/templatetags/i18n.py` exists and the shim interposes it, the shipped pass
is already all-`True`, the repoint changes nothing, and the same line still
printed 10 of 10. **Counting was never measuring.** The honest quantity is a
per-name comparison between passes, and the third pass is what makes the
demonstration independent of tree state.

**The blast radius is one instrument.** The progress meter is compared before and
after the repoint and reads the same pair either way, because `unshimmed()`
compares source *trees* (`_our_modules_in(scope)` against `django_modules(scope)`)
and never reads a module attribute; `m12_measure_l10n_seam.py` is immune for a
different reason — it decides by class identity (`is l10n.LocalizeNode`), not by
path. So the two numbers that carry the milestone, the meter and the seam, do not
share this dependency.

The two directional rows are the claim and hold on any tree. The as-shipped row
is the *tree's* state and is reported rather than asserted: at `a6a11eb` it reads
0 of 10 ours, not from a run at that commit but from
`git ls-tree a6a11eb python/django_rust_templates/_impl/templatetags/` — which
lists cache, l10n, static and tz and **no i18n** — plus `owner()`'s own
definition, which looks for `_impl` in the file path.

**A verdict here is dated, and during a fan-out it is worse than dated:** agents
share one tree, so a run made while a dispatch is in flight measures that
dispatch's work in progress. The first commit of this script printed an
all-`True` as-shipped column and a meter of 11/**0** because 6a's transcription
already existed in the working tree. Re-run at a landed commit, not mid-dispatch.

`.review/` is outside the anchor gate's `TREE_ROOTS`, so the line numbers this
prints are prose, not anchors.

Run with the shim on `PYTHONPATH` so the meter half can report, and **not** `-I`
(which implies `-s` and ignores `PYTHONPATH`, so under it this measures stock
Django). The `owner()` half does not depend on the shim, and says so if absent:

    . scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django:$PWD" \\
        .venv/bin/python .review/m12-measurements/m12_measure_instrument_blindspot.py
"""

import pathlib
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / ".django"))
sys.path.insert(0, str(ROOT / "python"))

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


def meter():
    """`unshimmed()` at both scopes, or a note that the shim is absent."""
    import django_rust_templates as drt

    if not drt._shim.installed():
        return None
    return (
        len(drt.unshimmed()),
        len(drt._shim.unshimmed(scope="django.templatetags")),
    )


import django.templatetags.i18n as i18n  # noqa: E402

OURS = str(ROOT / "python/django_rust_templates/_impl/templatetags/i18n.py")
THEIRS = str(ROOT / "elsewhere/django/templatetags/i18n.py")

names = sorted(i18n.register.tags)
tags = i18n.register.tags
real_file = i18n.__file__


def pass_over(names):
    """`{name: ours}` under whatever `__file__` currently says."""
    return {name: owner(tags[name])[1] for name in names}


print("module      :", real_file)
print("meter       :", meter() or "(shim not installed)")
print()

shipped = pass_over(names)
i18n.__file__ = OURS
repointed = pass_over(names)
i18n.__file__ = THEIRS
derel = pass_over(names)
i18n.__file__ = real_file

for label, results in (
    ("as shipped", shipped),
    ("__file__ -> _impl", repointed),
    ("__file__ -> no _impl", derel),
):
    print(f"== {label:<21} ours={sum(results.values())} of {len(names)}")
    for name in names:
        print(f"   {name:<28} ours={results[name]}")
    print()

n = len(names)
print(f"_impl path points every name at ours     : {sum(repointed.values())} of {n}")
print(f"no-_impl path points every name at theirs: {n - sum(derel.values())} of {n}")
print(f"as shipped, the ten names read           : {sum(shipped.values())} ours / "
      f"{n - sum(shipped.values())} theirs  <- this column is the tree's state, not the claim")
print("meter       :", meter() or "(shim not installed)")
