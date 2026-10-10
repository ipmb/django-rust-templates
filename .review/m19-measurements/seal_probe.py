"""Which delegated node keeps the real-world loop unsealed.

`node_census.py` reports that the corpus's `real-world/normal` hands 198.8% of its
output to Python, and `construct_cost.py` reports that the delegated constructs
*individually* cost little on our tree (an `{% if %}` costs 1.17 us against
stock's 0.74; a filtered `{{ }}` 1.23 against 0.39).  Neither number says which
construct is **load-bearing for reachability**: the walk seals a loop body only if
every node in it is walk-rendered, so one delegated node anywhere inside the loop
hands the whole loop — and the whole table it builds — back to Python.

This probe answers that with the census rather than with a theory.  It renders the
same page with the loop body's two delegated constructs replaced one at a time —
the 2x2 of "live or baked", both off, both on, and one mixed — and prints the
walk's own visit set for each variant.  The variants differ in their *bytes*, by
construction — a baked `{% if %}` is not a test of output equality — so this file
is about structure only and quotes no timing.

**Since M19 step 3a every variant seals, so this file is no longer a
discriminator** — the question it was written to answer is answered by
construction, and the readings below are all one shape: `ForNode` under
`handled:` (the walk iterates the loop itself) with `IncludeNode` alone under
`delegated:`, which is the footer.  Read it now as a regression witness: a row
that puts `ForNode` back under `delegated:` means some node inside the body
stopped being walk-rendered and took the whole table back to Python with it, and
the per-row construct counts say which one.  The rows are kept as they were so
that the shape of the seal rule — *every* node in a body, or the body is
Python's — stays visible in one file.

Run:  .venv/bin/python -I .review/m19-measurements/seal_probe.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_corpus  # noqa: E402
from node_census import report  # noqa: E402

HEAD = (
    "<!doctype html><html><head><title>{{ title }}</title></head><body>"
    "<h1>{{ title|upper }}</h1>"
    "{% if rows %}<p>{{ rows|length }} rows</p>{% else %}<p>no rows</p>{% endif %}"
)

# The loop, with its inner constructs as switches.  `{if}` is the delegated
# `{% if r.hot %}`; `{filter}` is the one filtered variable inside the body.
LOOP = (
    "<table>"
    "{% for r in rows %}"
    "<tr><td>{{ r.name }}</td><td>{{ r.owner }}</td>"
    "<td>{status}</td><td>{{ r.note }}</td>"
    "<td>{if}</td></tr>"
    "{% endfor %}"
    "</table>"
)

TAIL = '{% include "partials/footer.html" %}</body></html>'

IF_LIVE = "{% if r.hot %}<b>hot</b>{% else %}ok{% endif %}"
IF_BAKED = "<b>hot</b>"

TEMPLATES = {"partials/footer.html": bench_corpus.FOOTER}

VARIANTS = [
    ("as shipped", "{{ r.status|title }}", IF_LIVE),
    ("inner if baked", "{{ r.status|title }}", IF_BAKED),
    ("inner filter baked", "{{ r.status }}", IF_LIVE),
    ("inner if baked, inner filter baked", "{{ r.status }}", IF_BAKED),
]

# `loop body is text only` differs from the row above it by the three plain
# `{{ r.* }}` variables, which the walk already renders natively; it is here as
# the control: if *it* is the first row to seal, the seal is not about the two
# constructs this probe is testing.
TEXT_ONLY = (
    "<table>"
    "{% for r in rows %}"
    "<tr><td>name</td><td>owner</td><td>status</td><td>note</td>"
    "<td>" + IF_BAKED + "</td></tr>"
    "{% endfor %}"
    "</table>"
)


def main() -> None:
    data = {"title": "Monthly report", "rows": bench_corpus.ROWS}
    for label, status, branch in VARIANTS:
        body = HEAD + LOOP.replace("{status}", status).replace("{if}", branch) + TAIL
        report(label, {"page.html": body, **TEMPLATES}, data)
        print()
    report("loop body is text only", {"page.html": HEAD + TEXT_ONLY + TAIL, **TEMPLATES}, data)


if __name__ == "__main__":
    main()
