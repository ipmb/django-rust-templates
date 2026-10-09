#!/usr/bin/env python
"""Does `{% include %}` force materialisation back into Python, and how does it
recognise a template?

§4.1 answered this for inheritance and left include open. Reading Django 6.0.9's
`IncludeNode.render` (`loader_tags.py`) suggests it is *friendlier* than
inheritance, in a way worth pinning because the two constructs disagree:

  * it recognises a template by **duck typing** — `if not callable(getattr(
    template, "render", None))` — not by `isinstance`, which is what
    `ExtendsNode.get_parent` uses. So an object that is not a Django `Template`
    but has a `render(context)` is accepted as one.
  * it caches the loaded template in `context.render_context.dicts[0]` keyed by
    **the node object itself**, explicitly so that a `{% include %}` inside a loop
    does not re-resolve per iteration. `dicts[0]` is the outermost dict, so the
    cache survives loop pushes.
  * the miss path is `context.template.engine.select_template(tuple_of_names)` —
    `select_template`, not `find_template`, so that is part of the engine surface
    a backend has to provide.

Four claims, each measured from outside with a counting loader:

  1. a plain object with a `render(context)` method is accepted by `{% include %}`
     — reached through a context variable, so it is `self.template.resolve()`
  2. one `{% include %}` in a 5-iteration loop resolves the template **once**
  3. two separate `{% include %}` nodes for the same name resolve it **twice**
     (the cache key is the node, so this is the control for claim 2 — a cache
     keyed by *name* would make this 1)
  4. `{% include "x" only %}` isolates the context

Claim 3 is the one that makes claim 2 mean something; without it, "called once in a
loop" is equally consistent with a global name-keyed cache.

Run under either side; nothing here is engine-specific.

    source scripts/env.sh
    PYTHONPATH="$PWD/.django" .venv/bin/python .review/m18-measurements/m18_include.py
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


TEMPLATES = {
    "inc.html": "INC({{ v }})",
    "loop.html": '{% for i in items %}{% include "inc.html" %}{% endfor %}',
    "twice.html": '{% include "inc.html" %}-{% include "inc.html" %}',
    "only.html": '{% include "inc.html" only %}',
    "viavar.html": "{{ x }}",
}


class DuckTemplate:
    """Not a Django `Template`. Only quacks."""

    def __init__(self, text):
        self.text = text
        self.rendered_with = None

    def render(self, context):
        self.rendered_with = context
        return f"DUCK[{self.text}]"


def main() -> int:
    print(f"tree:    {template_base.__file__}")
    print(f"django:  {django.get_version()}")
    print(f"python:  {sys.version.split()[0]}")
    print()

    # `Engine.loaders` takes *specs*, not instances: `find_template_loader` splits
    # a tuple into `(loader, *args)`, requires the first element to be an import
    # *string*, then calls `cls(engine, *args)`. So the counting loader has to live
    # in an importable module (`m18_probe_lib`). Assigning `engine.loaders` after
    # construction does not work either — `template_loaders` is a cached_property
    # that the first `from_string` has already frozen.
    counter = m18_probe_lib.LOADS
    engine = Engine(loaders=[("m18_probe_lib.CountingLoader", TEMPLATES)])

    # 1: a duck-typed template, reached through a context variable so that it goes
    # through `self.template.resolve(context)` rather than the name path.
    duck = DuckTemplate("hi")
    out_duck = engine.from_string("{% include x %}").render(Context({"x": duck}))
    print(f"1. include of a non-Template object: {out_duck!r}")
    print(f"   its .render was called:           {duck.rendered_with is not None}")

    # 2: one node, five iterations.
    counter.clear()
    out_loop = engine.get_template("loop.html").render(
        Context({"items": list(range(5))})
    )
    loop_loads = counter.get("inc.html", 0)
    print(f"2. include in a 5-iteration loop:     {out_loop!r}")
    print(f"   inc.html loaded {loop_loads} time(s)")

    # 3: the control — two *distinct* nodes naming the same template.
    counter.clear()
    out_twice = engine.get_template("twice.html").render(Context({}))
    twice_loads = counter.get("inc.html", 0)
    print(f"3. two separate include nodes:        {out_twice!r}")
    print(f"   inc.html loaded {twice_loads} time(s)")

    # 4: `only` isolates.
    out_only = engine.get_template("only.html").render(Context({"v": "outer"}))
    print(f"4. include ... only, v='outer':       {out_only!r}")

    print()
    checks = [
        (
            "an object that merely has .render(context) is accepted by {% include %}",
            out_duck == "DUCK[hi]" and duck.rendered_with is not None,
            f"out={out_duck!r}",
        ),
        (
            "one include node in a 5-iteration loop resolves the template once",
            loop_loads == 1,
            f"loads={loop_loads}",
        ),
        (
            "control: two separate include nodes resolve it twice (cache is per-node)",
            twice_loads == 2,
            f"loads={twice_loads}",
        ),
        (
            "`only` isolates: the outer v does not reach the included template",
            out_only == "INC()",
            f"out={out_only!r}",
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
