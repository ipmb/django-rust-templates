"""Where a render's *entry* cost goes: `render_context.push_state`, `bind_template`, an include.

`micro/include` is the row jinja2 still wins (7.8 us against 6.1) and
`real-world/complex` is 72 of them, so the include path is worth decomposing
before anything else is built.  Two fixed terms sit under every render and both
are Django's own, not the walk's:

  * `Template.render` is `with context.render_context.push_state(self): if
    context.template is None: with context.bind_template(self): ...`
    (`.django/django/template/base.py:168-176`), so a *page* render pays
    `push_state` **and** `bind_template`, and a nested render (an `{% include %}`,
    an `{% extends %}`'s parent) pays only `push_state`.
  * `{% include %}` adds `IncludeNode.render`'s own frame, a `context.push(**{})`
    around the nested render, and a `template.resolve` — none of which the walk
    can remove while the node is delegated.

Every number is one call, best of N, on the same tree; `t.render` is printed
beside `t._render` so the entry term is the difference and not an inference.

Run:  .venv/bin/python -I .review/m19-measurements/render_entry_cost.py
"""

import sys
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

FOOTER = "<footer>{{ title }} - {{ rows|length }} rows</footer>"
ROWS = [{"name": f"n{i}"} for i in range(50)]
DATA = {"title": "Monthly report", "rows": ROWS}

ITERS = 300_000
REPEATS = 7


def bench(label, fn, iters=ITERS):
    best = min(timeit.repeat(fn, number=iters, repeat=REPEATS)) / iters * 1e6
    print(f"  {label:52} {best:9.3f} us")
    return best


def one_include():
    engine = bench_engines.build(
        "rust",
        {"page.html": '{% include "partials/footer.html" %}', "partials/footer.html": FOOTER},
        {},
    )
    try:
        handle = engine.get("page.html")
        context = engine.context(DATA)
        for _ in range(10):
            engine.render(handle, context)
        return engine, handle, context
    except BaseException:
        engine.close()
        raise


def main() -> None:
    engine, handle, context = one_include()
    try:
        footer = engine.get("partials/footer.html")
        print(f"iters={ITERS}, best of {REPEATS}\n")
        total = bench("micro/include  t.render(ctx)  [the whole row]", lambda: engine.render(handle, context))
        bench("the included template alone: t.render(ctx)", lambda: footer.render(context))
        bench("  ... t._render(ctx)  [minus the entry]", lambda: footer._render(context))
        print()
        # `Template.render`'s two terms, taken apart on a template that renders
        # nothing so the walk's own cost is the floor and not a confound.
        empty = engine.from_string("")
        bench("empty t.render(ctx)  [entry + walk floor]", lambda: empty.render(context))
        bench("empty t._render(ctx)  [walk floor]", lambda: empty._render(context))
        rc = context.render_context
        bench("context.render_context.push_state(empty)", lambda: rc.push_state(empty))
        bench("context.bind_template(empty)", lambda: context.bind_template(empty))
        print()
        print(f"  the whole row is {total:.2f} us; the terms above are printed beside it, not subtracted from it,")
        print("  because the row's own entry is the first line and the included render's is the third.")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
