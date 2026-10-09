#!/usr/bin/env python
"""Does `{% extends %}`/`{% block %}` force materialisation back into Python?

This is the architectural risk M18's spec names as its most likely killer: if
inheritance requires a Python `Node` tree, then "the tree stays in Rust" is dead
for any template that inherits, which is most of them.

Read from Django 6.0.9's source, three mechanisms say it might:

  * `ExtendsNode.__init__` builds `self.blocks = {n.name: n for n in
    nodelist.get_nodes_by_type(BlockNode)}` — a census of block nodes *by type*,
    so a Rust tree needs its own.
  * `BlockNode.render` puts the node itself into the context: `context["block"] =
    block`, where `block` is a `BlockNode` (or a shallow copy carrying
    `.context`).
  * and `{{ block.super }}` reaches that object through *variable resolution*,
    which in `base.py` calls it if it is callable (`current = current()`).

The last one is the load-bearing claim, and it is testable without reading any
more source: if `{{ block.super }}` renders the parent block's *body text*, the
bound method was called. If it renders anything else — a repr, an error — it was
not.

So this probe renders an inheriting template in which a third-party
`takes_context` tag reports what it can see, and asserts the three mechanisms
from the outside:

  1. inside a block, `context["block"]` is a live `BlockNode` with `.super`
  2. at top level (outside any block), it is absent
  3. `{{ block.super }}` renders the parent body, so the callable was called
  4. `render_context["block_context"]` is a `BlockContext` during the render

It also prints `context.template` and `context.template.engine`, because
`base.py` reaches through them (`current = context.template.engine
.string_if_invalid`) from inside variable resolution — so a backend owning the
tree must still expose both.

Run under either side; nothing here is engine-specific and it should print the
same shapes both ways:

    source scripts/env.sh
    PYTHONPATH="$PWD/.django" .venv/bin/python .review/m18-measurements/m18_block_carrier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE.parents[1] / "scripts"))
sys.path.insert(0, str(HERE))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import django  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.template import base as template_base  # noqa: E402

import m18_probe_lib  # noqa: E402

PARENT = "PARENT[{% block content %}parent-body{% endblock %}]"
CHILD = (
    '{% extends "base.html" %}{% load probe %}'
    '{% block content %}'
    'CHILD({% observe "in-block" %}{{ block.super }})'
    "{% endblock %}"
)
# `{% observe %}` at top level, after the extends, to see the context outside a
# block. It renders after the extends' output, which is fine — the tag reports
# state, it does not care about position.
PLAIN = '{% load probe %}{% observe "top-level" %}plain'


def main() -> int:
    print(f"tree:    {template_base.__file__}")
    print(f"django:  {django.get_version()}")
    print(f"python:  {sys.version.split()[0]}")
    print()

    engine = Engine(
        loaders=[
            (
                "django.template.loaders.locmem.Loader",
                {"base.html": PARENT, "child.html": CHILD, "plain.html": PLAIN},
            )
        ],
        libraries={"probe": "m18_probe_lib"},
    )

    child = engine.get_template("child.html")
    out = child.render(Context({}))
    plain_out = engine.get_template("plain.html").render(Context({}))

    print(f"rendered child: {out!r}")
    print(f"rendered plain: {plain_out!r}")
    print()

    print("what the tag saw:")
    for obs in m18_probe_lib.OBSERVATIONS:
        print(f"  --- {obs['label']} ---")
        for key, value in obs.items():
            if key != "label":
                print(f"      {key:22s} {value}")

    print()
    # The three assertions, stated as the reader can check them from the output
    # above rather than as a bare "ok".
    in_block = next(o for o in m18_probe_lib.OBSERVATIONS if o["label"] == "in-block")
    top = next(o for o in m18_probe_lib.OBSERVATIONS if o["label"] == "top-level")

    checks = [
        (
            "block.super was CALLED (parent body reached the output)",
            "parent-body" in out,
            f"out={out!r}",
        ),
        (
            'inside a block, context["block"] is a live BlockNode with .super',
            in_block["block"] is not None and in_block["block_has_super"] is True,
            f"{in_block['block']} has_super={in_block['block_has_super']}",
        ),
        (
            'outside a block, context["block"] is absent',
            top["block"] is None,
            f"{top['block']!r}",
        ),
        (
            'render_context carries a "block_context" during the render',
            in_block["block_context"] is not None,
            f"{in_block['block_context']}",
        ),
        (
            "context.template.engine is reachable (base.py resolves through it)",
            in_block["engine"] is not None,
            f"{in_block['engine']}",
        ),
    ]

    failed = 0
    for name, ok, detail in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
        if not ok:
            failed += 1
            print(f"         {detail}")

    print()
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
