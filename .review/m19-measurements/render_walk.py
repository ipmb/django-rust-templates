"""The render walk (M19 step 2): what it owns, what it costs, what the fix removed.

Three sections, and they answer three different questions.  Nothing here is a
second copy of `scripts/bench.py`; the corpus table is the corpus table, and this
file measures *one* template at a time so that a route and a fixed cost can be
named rather than averaged.

  1. The census.  For a handful of one-construct templates, which route the
     render took — the walk, or `NodeList.render` — and whether Python's lookup
     seam was reached at all.  Both signals are printed because they are not the
     same signal, and the two places the walk can stop are easy to run together:

       * `plan_list` returns `None` — the **template** is declined, whole, and
         `Template._render` runs `NodeList.render`.  Every node is Python's.
       * `plan_slot` returns `Act::Delegate` for one node — the plan still
         exists and the walk still owns the template, but *that node* is handed
         to `render_annotated`, which is Python's route for it.

     So a non-`None` plan is **not** the same as a native render, and the meter
     is what separates them: on a delegated node the lookup crosses back through
     our `Variable.resolve` to `_native.resolve_lookups`.  `{{ a.b }}` is the one
     row here the walk renders with no Python at all; the other three each
     delegate a node and show it.

     The second seam in the meter, `_native.resolve`, is the *re-joined path*
     entry the shell used before M19 step 1.  Nothing in the shipped package
     calls it any more — it is kept for the tests that pin its classification —
     so `paths` printing empty on every row is itself the assertion that the
     re-joined path is on no live route.  It is printed rather than dropped
     because an empty list from a meter nobody wired up is worthless.

  2. The render call's split, on one text template.  `t.render` is the
     production route (`Template.render` -> `_render` -> the walk); `_render` is
     the same minus the context push; `_native.render_plan` is the walk entry
     alone; `t.nodelist.render` is the path the row would take **without** the
     walk, which is what makes the difference a measurement rather than a story.
     The empty template is the floor, and it is printed beside the row it floors
     rather than subtracted from it — the two have different setups.

  3. The fixed per-render costs the walk made visible, and the repair.  A walk
     fetches its escape callable once per render, so an import+getattr that used
     to be amortised by nothing became a per-render term; `mark_safe` was the
     other.  `SafeString` and `mark_safe` are both printed because the faster one
     is also the faithful one — `NodeList.render` builds a `SafeString` directly.

Run:  .venv/bin/python -I .review/m19-measurements/render_walk.py
`-I` drops the cwd from `sys.path`, so the insert below is what puts the working
tree's package ahead of anything installed.
"""

import sys

sys.path.insert(0, "/Users/pete/projects/lincolnloop/django-rust-templates")

import timeit  # noqa: E402

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(
    DEBUG=True,
    INSTALLED_APPS=[],
    TEMPLATES=[
        {
            "BACKEND": "django_rust_templates.engine.Engine",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ],
)
django.setup()

from django.utils.safestring import SafeString, mark_safe  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine import Context, Engine  # noqa: E402

ENGINE = Engine()


def census(src, ctx):
    """Which route the render took, and whether Python's lookup seam was entered.

    The meter wraps the two seam functions the shell calls.  A node the walk
    renders *natively* never calls them — its lookup is resolved inside Rust — so
    an empty meter means the node never left; a populated one means the node was
    delegated and Python resolved it.  `plan is None` is a different question: it
    is template-level, and it prints False even when every node is delegated.
    The output is printed as `repr` so a `SafeString` and a `str` are
    distinguishable; they render the same and are not the same object.
    """
    template = ENGINE.from_string(src)
    components, paths = [], []
    original_lookups, original_resolve = _native.resolve_lookups, _native.resolve
    _native.resolve_lookups = lambda l, c: (components.append(list(l)), original_lookups(l, c))[1]
    _native.resolve = lambda p, c: (paths.append(p), original_resolve(p, c))[1]
    try:
        out = template.render(Context(ctx))
    finally:
        _native.resolve_lookups = original_lookups
        _native.resolve = original_resolve
    route = "declined" if template._native_plan is None else "walked"
    print(f"{src!r:40} route={route:8} out={out!r:12}")
    print(f"{'':40}   seam: components={components!r} paths={paths!r}")


def bench(label, fn, n):
    best = min(timeit.repeat(fn, number=n, repeat=7)) / n * 1e6
    print(f"  {label:44} {best:8.3f} us")


print("== 1. census: which route, and does the lookup reach Python ==")
census("{{ a.b }}", {"a": {"b": 1}})  # Act::Emit — no Python at all
census("{{ a.b|upper }}", {"a": {"b": "x"}})  # filtered: the node delegates
census("{% for x in xs %}{{ x }}{% endfor %}", {"xs": [1, 2]})  # the iterable resolves in Python
census("{% if a %}y{% endif %}", {"a": 1})  # an `if`: the node delegates

print()
print("== 2. the render call's split (text template, then the empty floor) ==")
ctx = Context({})
text = ENGINE.from_string("hello there this is text")
plan = text._native_plan
print("  plan is None:", plan is None)
bench("t.render(ctx)          [production route]", lambda: text.render(ctx), 200_000)
bench("t._render(ctx)         [minus context push]", lambda: text._render(ctx), 200_000)
bench("_native.render_plan(plan, ctx)  [walk entry]", lambda: _native.render_plan(plan, ctx), 200_000)
bench("t.nodelist.render(ctx)  [the Python path]", lambda: text.nodelist.render(ctx), 200_000)

empty = ENGINE.from_string("")
print("  empty plan is None:", empty._native_plan is None)
bench("empty t.render(ctx)", lambda: empty.render(ctx), 200_000)
bench("empty _native.render_plan  [the floor]", lambda: _native.render_plan(empty._native_plan, ctx), 300_000)

print()
print("== 3. the fixed costs the walk made visible, and the repair ==")
bench("mark_safe('')", lambda: mark_safe(""), 300_000)
bench("SafeString('')", lambda: SafeString(""), 300_000)
bench("_native.engine_version()  [minimal FFI]", lambda: _native.engine_version(), 300_000)
bench("_native.html_escape('abc')", lambda: _native.html_escape("abc"), 300_000)
bench("''.join(['a'])", lambda: "".join(["a"]), 300_000)
