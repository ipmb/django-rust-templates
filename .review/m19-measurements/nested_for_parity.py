"""Nested `{% for %}` parity: ours vs stock Django, byte for byte.

**Why this probe before the suite.**  Slice 3 lets a nested `{% for %}` seal and
therefore forces the *enclosing* loop to write Django's per-iteration state
(`needs_context_state`).  The one thing that mechanism can get wrong is
`parentloop` — the identity and the numbers a nested loop reads off
`context["forloop"]` — and `run_stated`'s dict is *reused and mutated* rather
than built per iteration, which is Django's own shape but is exactly the kind of
thing a test that only reads `.counter` passes while identity differs.

So this renders a fixed set of nested-loop templates under two engines — stock
Django's `Template` and ours — and compares the rendered bytes.  It does not
time anything; the question is correctness, and a single byte of difference is
the whole answer.

Run:  .venv/bin/python -I <this>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django.template import Context, Engine  # noqa: E402

from django_rust_templates import engine as rust_engine  # noqa: E402

DATA = {
    "sections": [
        {"title": "alpha", "lines": ["a1", "a2", "a3"]},
        {"title": "beta", "lines": ["b1", "b2"]},
        {"title": "gamma", "lines": []},
    ],
    "rows": [{"cells": [1, 2]}, {"cells": [3]}],
    "flat": [1, 2],
    "nested": [[1, 2], [3]],
}

# `partials/cell.html` is the delegate half: an `{% include %}` inside the inner
# loop, which makes the inner loop `context_state` and so makes it read
# `context["forloop"]` for its own `parentloop`.
TEMPLATES = {
    "partials/cell.html": "[{{ label }}:{{ forloop.parentloop.counter }}:{{ line }}]",
    "loopvar_in_inner_seq.html": (
        "{% for s in sections %}{% for l in s.lines %}{{ s.title }}-{{ l }};{% endfor %}{% endfor %}"
    ),
    "parentloop_counter.html": (
        "{% for s in sections %}{% for l in s.lines %}"
        "{{ forloop.parentloop.counter }}.{{ forloop.counter }};{% endfor %}{% endfor %}"
    ),
    "parentloop_deep.html": (
        "{% for s in sections %}{% for l in s.lines %}{% for c in flat %}"
        "{{ forloop.parentloop.counter }}.{{ forloop.parentloop.parentloop.counter }};"
        "{% endfor %}{% endfor %}{% endfor %}"
    ),
    "inner_in_if.html": (
        "{% for s in sections %}{% if s.lines %}{% for l in s.lines %}{{ l }}"
        "{% endfor %}{% endif %}{% endfor %}"
    ),
    "outer_var_in_inner_body.html": (
        "{% for s in sections %}{% for l in s.lines %}{{ s.title }}{{ l }}{% endfor %}{% endfor %}"
    ),
    "empty_inner.html": (
        "{% for s in sections %}{% for l in s.lines %}{{ l }}{% empty %}"
        "none({{ forloop.parentloop.counter }}){% endfor %}{% endfor %}"
    ),
    "delegate_in_inner.html": (
        "{% for s in sections %}{% for l in s.lines %}"
        "{% include 'partials/cell.html' with label=s.title line=l %}"
        "{% endfor %}{% endfor %}"
    ),
    "reversed_inner.html": (
        "{% for s in sections %}{% for l in s.lines reversed %}{{ l }}{% endfor %}{% endfor %}"
    ),
    "inner_over_outer_var.html": (
        "{% for r in rows %}{% for c in r.cells %}{{ forloop.parentloop.counter }}:{{ c }},"
        "{% endfor %}{% endfor %}"
    ),
    "sibling_loops.html": (
        "{% for s in sections %}{{ s.title }}{% for l in s.lines %}{{ l }}{% endfor %}{% endfor %}"
        "{% for r in rows %}{% for c in r.cells %}{{ c }}{% endfor %}{% endfor %}"
    ),
    # The new paths slice 3 opens: a **nested sealed loop whose own body refuses
    # a node**, so the refused node writes its own layer (parentloop read off the
    # enclosing stated loop) — and one where the enclosing loop is *pure* and the
    # nested loop is the stated one.  These are the two shapes that did not exist
    # before, because a nested loop was always a delegate.
    "inner_refused_filter.html": (
        "{% for s in sections %}{% for l in s.lines %}{{ l|slice:':1' }};{% endfor %}{% endfor %}"
    ),
    "outer_pure_inner_stated.html": (
        "{% for r in rows %}{% for c in r.cells %}{{ forloop.parentloop.counter0 }}{{ c }}{% endfor %}"
        "{% endfor %}"
    ),
    "inner_include_only.html": (
        "{% for s in sections %}{% for l in s.lines %}{% include 'partials/cell.html' only %}"
        "{% endfor %}{% endfor %}"
    ),
    "cycle_in_inner.html": (
        "{% for r in rows %}{% for c in r.cells %}<{% cycle 'a' 'b' %}>{{ c }}{% endfor %}{% endfor %}"
    ),
    "outer_empty_branch.html": (
        "{% for s in sections %}{% for l in s.lines %}{{ l }}{% empty %}E{{ forloop.parentloop.counter }}"
        "{% endfor %}{% endfor %}"
    ),
    "nested_three_deep.html": (
        "{% for s in sections %}{% for l in s.lines %}{% for c in flat %}"
        "{{ forloop.counter }}{{ forloop.parentloop.counter }}{{ forloop.parentloop.parentloop.counter }}:{{ s.title }}"
        "{% endfor %}{% endfor %}{% endfor %}"
    ),
}


def main() -> int:
    # Both engines must resolve `partials/cell.html`, so both get a loader over
    # the same dict.  The loader is the reader's route: an include that failed to
    # resolve would make this probe compare an error to an error.
    tpl_map = dict(TEMPLATES)

    stock = Engine(
        loaders=[("django.template.loaders.locmem.Loader", tpl_map)],
    )
    ours = rust_engine.Engine(
        loaders=[("django.template.loaders.locmem.Loader", tpl_map)],
    )

    print(f"django {__import__('django').get_version()}, {len(TEMPLATES)} templates")
    print()
    header = f"{'template':28} {'stock len':>9} {'ours len':>9}  verdict"
    print(header)
    print("-" * len(header))
    bad = 0
    for name in TEMPLATES:
        if name.startswith("partials/"):
            continue
        src = TEMPLATES[name]
        try:
            stock_out = stock.from_string(src).render(Context(DATA))
        except Exception as exc:  # noqa: BLE001
            stock_out = f"<raised {type(exc).__name__}: {exc}>"
        try:
            ours_out = ours.from_string(src).render(Context(DATA))
        except Exception as exc:  # noqa: BLE001
            ours_out = f"<raised {type(exc).__name__}: {exc}>"
        ok = stock_out == ours_out
        bad += not ok
        print(
            f"{name:28} {len(stock_out):>9} {len(ours_out):>9}  "
            f"{'ok' if ok else 'DIFF'}"
        )
        if not ok:
            print(f"    stock: {stock_out!r}")
            print(f"    ours : {ours_out!r}")
    print()
    if bad:
        print(f"VOID: {bad} of {len(TEMPLATES)} differ")
        return 1
    print("every template rendered byte-identically under both engines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
