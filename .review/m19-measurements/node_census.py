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

import re
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
# `{% block %}` are spliced away: the same bytes out, the same loop, the same
# filters, reachable by the walk instead of sitting behind two `BlockNode`
# delegates. It is here to price *reachability* on its own, which is the quantity
# a decision about scheduling inheritance first turns on.
#
# **It is derived from the corpus, not typed here, and that is the point.**  A
# hand-written lookalike is the classic way a fixture drifts away from what ships:
# it is edited by whoever last touched the row, and the shipped page's own edits
# never reach it.  This round produced the demonstration — two people each spliced
# "normal without its blocks" by hand and got two different templates (this file's
# 98.2% delegated output against the reviewer's 99.6% on the same row), which is
# how a fixture becomes a claim about itself.  `check_flat_normal` is the gate
# that keeps the derivation honest: it renders the shipped page and this one and
# requires the bytes to be identical, so a corpus edit the splice fails to carry
# fails loudly here instead of silently moving the row.
EXTENDS_RE = re.compile(r"""{%\s*extends\s+["']([^"']+)["']\s*%}""")
BLOCK_RE = re.compile(r"{%\s*block\s+(\w+)\s*%}(.*?){%\s*endblock\s*%}", re.S)


def _flat_blocks(source, what):
    """The `{% block %}` bodies of `source`, refusing a source the rule cannot cut.

    `BLOCK_RE` is lazy, so a `{% block %}` nested inside another cuts at the
    inner `{% endblock %}` and yields a body that looks plausible and is wrong.
    The corpus's `normal` and `base` pages are flat, and the equality of the two
    counts says so mechanically rather than by inspection.
    """
    blocks = BLOCK_RE.findall(source)
    if source.count("{% block") != len(blocks):
        raise AssertionError(f"{what}: nested `{{% block %}}`; the splice rule cannot cut it")
    return dict(blocks)


def flatten_inheritance(sources, entry):
    """`entry`'s page with `{% extends %}`/`{% block %}` removed by substitution.

    A source rewrite rather than a node-tree one, which is exact for pages with
    no `{{ block.super }}` and no nested blocks — held to that by
    `check_flat_normal`'s byte comparison, not by this docstring.
    """
    child = sources[entry]
    for _ in range(8):
        match = EXTENDS_RE.search(child)
        if match is None:
            break
        parent = EXTENDS_RE.sub("", sources[match.group(1)])
        overrides = _flat_blocks(EXTENDS_RE.sub("", child), entry)
        _flat_blocks(parent, match.group(1))
        child = BLOCK_RE.sub(lambda m: overrides.get(m.group(1), m.group(2)), parent)
    else:
        raise AssertionError(f"{entry}: more than 8 levels of extends")
    if "{% extends" in child or "{% block" in child:
        raise AssertionError(f"{entry}: inheritance left in the flattened page")
    return child


FLAT_NORMAL = flatten_inheritance(
    {"base.html": bench_corpus.BASE, "page.html": bench_corpus.NORMAL}, "page.html"
)


def check_flat_normal():
    """The shipped page and the flattened one must render to the same bytes.

    Three renders, one byte string: stock Django's `real-world/normal` is the
    reference, ours on the shipped page says we agree with it, and ours on the
    flattened page says the census row is about *that* page and not a lookalike.
    The templates and the data are the corpus's own row, read from
    `bench_corpus.workloads()` rather than restated here.
    """
    workload = next(w for w in bench_corpus.workloads() if w.key == "real-world/normal")
    flat = dict(workload.templates, **{"page.html": FLAT_NORMAL})
    outs = []
    for engine_name, templates in (
        ("django", workload.templates),
        ("rust", workload.templates),
        ("rust", flat),
    ):
        engine = bench_engines.build(engine_name, templates, workload.libraries(engine_name))
        try:
            handle = engine.get(workload.entry)
            outs.append(engine.render(handle, engine.context(workload.data)))
        finally:
            engine.close()
    if not outs[0] == outs[1] == outs[2]:
        lengths = ", ".join(f"{len(o)} chars" for o in outs)
        raise AssertionError(f"flat/normal is not the shipped page: {lengths}")
    print(f"flat/normal derived from real-world/normal: {len(outs[0])} chars, identical under all three renders\n")

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
        handled, delegated, delegated_len, filters, body_crossings = _native.census_take()
    except BaseException:
        _native.census_take()
        raise
    return (
        [(name, count / ITERS) for name, count in handled],
        [(name, count / ITERS) for name, count in delegated],
        delegated_len / ITERS,
        [(name, count / ITERS) for name, count in filters],
        body_crossings / ITERS,
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
        handled, delegated, delegated_len, filters, body_crossings, out_len = render_census(
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
        # The third meter: Python executed inside the *bodies* `applied:` names.
        # Zero is the strong reading and it is per filter-and-input, not per
        # filter — `upper` on a str is Rust's table, `escape` is
        # `conditional_escape` whatever it is given.
        print(f"{'':24}   body xings: {body_crossings:g}")
    finally:
        engine.close()


def main() -> None:
    print(f"iters={ITERS}, rust engine, render mode")
    print("  `nodes H/N` is the walk's own visit set; `out D/T` is characters returned from a delegate")
    print("  over the render's output length. The second is the one a page with inheritance turns on.\n")
    check_flat_normal()
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
