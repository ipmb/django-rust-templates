"""Which real-world rows the render walk owns, by node count **and** by output share.

The walk (M19 step 2) reports its own coverage through `_native.census_start` /
`census_take`.  Two numbers come back and they answer different questions:

  * the **node counts** — every node the walk *visits* lands in `handled` and
    every node it hands back to `render_annotated` lands in `delegated`, keyed by
    the node class name the descriptor recorded;
  * `delegated_len` — the characters that came back from a delegated node, summed
    at the delegate call site, reported here against the length of the render's
    own output.

**The second is the one that decides anything on a page with inheritance, and the
first is the one that misleads.**  A delegated `BlockNode` is *one node* and
carries an entire page body into Python with it, so a node-count fraction reads
"36% delegated" for a page where essentially all of the output came back from
Python.  `real-world/normal` below is that page.  The share is not a partition: a
delegate nested inside another delegate is counted once per delegate above it, so
a row can exceed 100% and is deliberately not clamped.

**What the counts can and cannot see.**  They count the walk's *visits*.  A node
the walk delegates is rendered by Python, which walks that node's own subtree —
blocks, the body of an `{% extends %}`, the inside of a loop that was not sealed —
and none of those nodes are visits, so they appear in neither list.  The census
therefore answers "what did the walk itself do", not "what is in the template".
`route` and `plan` are printed beside it so that shape is visible rather than
inferred: `plan` is the length of the plan the walk was handed, which is the
top-level nodelist and not the tree.

`plan is None` is the *template*-level stop and a different question from a
delegated node: `None` means `plan_list` declined the whole template and
`NodeList.render` ran; non-`None` with a non-zero `delegated` means the walk owns
the template and handed one node back — `micro/if` is the extreme case, a walked
template that walks nothing.

Run:  .venv/bin/python -I .review/m19-measurements/node_census.py
`-I` drops the cwd from `sys.path`; `filter_probe.ensure_settings` and the two
inserts below are what put the working tree's package and the bench modules on it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
import bench_engines  # noqa: E402

ITERS = 20

# The shape the corpus's `real-world/normal` has once `{% extends %}` and
# `{% block %}` are spliced away by hand: the same bytes out, the same loop, the
# same filters, reachable by the walk instead of sitting behind two `BlockNode`
# delegates. It is here to price *reachability* on its own, which is the quantity
# a decision about scheduling inheritance first turns on.
FLAT_NORMAL = (
    "<!doctype html><html><head><title>{{ title }}</title></head><body>"
    "<h1>{{ title|upper }}</h1>"
    "{% if rows %}<p>{{ rows|length }} rows</p>{% else %}<p>no rows</p>{% endif %}"
    "<table>"
    "{% for r in rows %}"
    "<tr><td>{{ r.name }}</td><td>{{ r.owner }}</td>"
    "<td>{{ r.status|title }}</td><td>{{ r.note }}</td>"
    "<td>{% if r.hot %}<b>hot</b>{% else %}ok{% endif %}</td></tr>"
    "{% endfor %}"
    "</table>"
    '{% include "partials/footer.html" %}'
    "</body></html>"
)

EXTRA = [
    ("flat/normal (no extends)", {"page.html": FLAT_NORMAL, "partials/footer.html": bench_corpus.FOOTER}),
    (
        "nested for (2 deep)",
        {"page.html": "{% for a in outer %}{% for b in a %}<i>{{ b }}</i>{% endfor %}{% endfor %}"},
    ),
]


def render_census(engine, handle, context):
    """Render `ITERS` times under the census; per-render counts and output length."""
    from django_rust_templates import _native

    engine.render(handle, context)  # warm the loader cache the corpus installs
    _native.census_start()
    try:
        out = None
        for _ in range(ITERS):
            out = engine.render(handle, context)
        handled, delegated, delegated_len, filters = _native.census_take()
    except BaseException:
        _native.census_take()
        raise
    return (
        [(name, count / ITERS) for name, count in handled],
        [(name, count / ITERS) for name, count in delegated],
        delegated_len / ITERS,
        [(name, count / ITERS) for name, count in filters],
        len(out),
    )


def fmt(rows):
    if not rows:
        return "-"
    return " ".join(f"{name}={count:g}" for name, count in rows)


def report(key, templates, data, entry="page.html", libraries=None):
    engine = bench_engines.build("rust", templates, libraries or {})
    try:
        handle = engine.get(entry)
        plan = getattr(handle, "_native_plan", None)
        context = engine.context(data)
        handled, delegated, delegated_len, filters, out_len = render_census(
            engine, handle, context
        )
        nodes = sum(c for _, c in handled) + sum(c for _, c in delegated)
        node_share = sum(c for _, c in delegated) / nodes if nodes else 0.0
        out_share = delegated_len / out_len if out_len else 0.0
        route = "declined" if plan is None else "walked"
        plan_nodes = "-" if plan is None else str(len(handle.nodelist))
        print(
            f"{key:24} {route:9} {plan_nodes:>5}  "
            f"nodes {sum(c for _, c in handled):>4g}/{nodes:<4g} deleg {node_share:6.1%}  "
            f"out {delegated_len:>6.0f}/{out_len:<6d} deleg {out_share:6.1%}"
        )
        print(f"{'':24}   handled:   {fmt(handled)}")
        print(f"{'':24}   delegated: {fmt(delegated)}")
        print(f"{'':24}   applied:   {fmt(filters)}")
    finally:
        engine.close()


def main() -> None:
    print(f"iters={ITERS}, rust engine, render mode")
    print("  `nodes H/N` is the walk's own visit set; `out D/T` is characters returned from a delegate")
    print("  over the render's output length. The second is the one a page with inheritance turns on.\n")
    for workload in bench_corpus.workloads():
        if "rust" not in workload.engines or workload.entry not in workload.templates:
            continue
        report(workload.key, workload.templates, workload.data, workload.entry, workload.libraries("rust"))
    print()
    data = {"title": "Monthly report", "rows": bench_corpus.ROWS, "outer": [[1, 2], [3, 4]]}
    for key, templates in EXTRA:
        report(key, templates, data)
        print()


if __name__ == "__main__":
    main()
