"""Where a named `{% cycle %}` and its reference straddle a partial boundary.

`{% cycle 'a' 'b' as c %}` stores the `CycleNode` under `c`, and `{% cycle c %}`
returns *that object* (`defaulttags.py:669-671`). Our parser cannot store the
object — it exists in Python — so `parser.named_cycle_nodes` holds a **descriptor**
and the materialiser builds the node from it. Until M13 the descriptor carried
nothing but `.slot(idx)`, which says "take whatever is already there".

That reading is true only if the definition has been materialised first, and
`{% partialdef %}` is what breaks it: `register_partial_def` materialises the
body **during the parse**, so a reference inside a body whose definition is
outside reaches a slot that is still empty — and a descriptor with no arguments
cannot build a `CycleNode`:

    TypeError: CycleNode.__init__() missing 1 required positional argument:
    'cyclevars'

on `{% cycle 'a' 'b' as c %}{% partialdef p %}{% cycle c %}{% endpartialdef %}
{% partial p %}` — a template Django renders. The fix echoes the definition's own
arguments into the descriptor, so whichever namer materialises first builds the
node and the rest find it in the table (`tags/cycle.rs:91-119`, and the mutation
it replaced is recorded there).

# The four placements, and why exactly one of them moved

The definition and the reference can each be inside or outside the body, and the
pre-fix divergence is exactly the cross-boundary one. Django is the baseline,
not a preference: every source below is one Django renders, and the row that
matters is the one where the two columns disagree.

**Before the descriptor echo**, measured on that tree: row 1 was the `TypeError`
above on our side against Django's `'aaa'`; rows 2-4 already agreed. So this is a
**parity** instrument rather than a behavioural one — it is what says the echo
was needed *and* that it did not over-reach into the other three shapes. A
re-run of this tree can only show agreement; the disagreement is history, kept
here because the four rows are the claim.

# Two engines, or the measurement means nothing

The instrument compares our engine against stock Django **in one process**, which
is only sound while the two columns really are two engines. The shim installs
through `scripts/shimpath/sitecustomize.py`, reached by `PYTHONPATH`, and
`django.template.engine` is **not** in the shim's `NOT_THE_ENGINE` — so with the
shim live, `from django.template import Engine` is *our* engine and both columns
agree trivially, in both the passing and the failing direction. The `sys.path`
insert below happens after `site` has already run, so it does not install the
shim; the guard asserts that rather than relying on it (`_shim.installed()` must
be False **and** `django.template.base.__file__` must be the pin's).

Run it plain, with no `PYTHONPATH`:

    .venv/bin/python .review/m13-measurements/m13_measure_cycle_boundary.py

Pinned tree and interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# `scripts/shimpath` is inserted first only so the guard below can import
# `_shim` to *ask*; it is last on the path and `site` has already run, so it
# installs nothing. See the docstring.
for _path in (ROOT, ROOT / "scripts" / "shimpath"):
    sys.path.insert(0, str(_path))
os.environ.setdefault("DJANGO_RUST_TEMPLATES", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scripts.filter_probe")

from scripts.filter_probe import ensure_settings  # noqa: E402

ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine, base as django_base  # noqa: E402

from django_rust_templates import _shim, render  # noqa: E402

if _shim.installed():
    sys.exit(
        "refusing to measure: our engine is installed behind `django.template`, "
        "so the `django` column below is not Django. Run it plain, with no "
        "`PYTHONPATH=scripts/shimpath` and no `DJANGO_RUST_TEMPLATES=1` in the "
        "environment."
    )
if not str(django_base.__file__).startswith(str(ROOT / ".django")):
    sys.exit(
        f"refusing to measure: `django.template.base` is {django_base.__file__}, "
        f"not the pin's under {ROOT / '.django'}. The baseline would be whatever "
        "Django happens to be importable, which is not the tree this pins."
    )

SHAPES = {
    "definition outside, reference in body":
        "{% cycle 'a' 'b' as c %}"
        "{% partialdef p %}{% cycle c %}{% endpartialdef %}"
        "{% partial p %}{% partial p %}",
    "definition inside body, reference in body":
        "{% partialdef p %}{% cycle 'a' 'b' as c %}{% cycle c %}{% endpartialdef %}"
        "{% partial p %}",
    "definition outside, reference outside":
        "{% cycle 'a' 'b' as c %}{% cycle c %}{% cycle c %}",
    "definition inside body, reference outside":
        "{% partialdef p %}{% cycle 'a' 'b' as c %}{% endpartialdef %}"
        "{% partial p %}{% cycle c %}",
}

print(
    f"django {django.get_version()} at {django_base.__file__}\n"
    f"on {sys.version.split()[0]}, shim installed: {_shim.installed()}\n"
)
for label, source in SHAPES.items():
    for side, run in (
        ("django", lambda s: Engine().from_string(s).render(Context({}))),
        ("ours", lambda s: render.render_string(s, {})),
    ):
        try:
            # `repr`, not the rendered string: a divergence can be an exception,
            # and an exception printed bare would be indistinguishable from a
            # side that produced nothing.
            got = repr(run(source))
        except Exception as error:  # noqa: BLE001 — the message is the measurement
            got = f"{type(error).__name__}: {error}"
        print(f"  {label:45s} {side:7s} {got}")
    print()
