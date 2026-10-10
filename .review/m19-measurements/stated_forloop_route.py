"""The search that would falsify "no template can read `forloop` from a refused node".

`Walk::write_frame` runs on exactly one route: a node inside a **pure-sealed**
loop (a body with no `Act::Delegate`, so `ForPlan::context_state` is false) that
**refuses** at render time.  Refusal has one producer — a filter whose answer Rust
cannot hold (`cannot_hold`).  `write_frame` builds the `forloop` dict, and
`stated_refused.py` measures the loop-variable half of it.

Each row puts a `forloop` read in a loop body and asks the census two questions:

  * did the loop seal?  (`ForNode` in `handled:`)
  * is the reading node one the census can see at all?  (a `forloop` read is
    often executed *inside* a delegated node's own render, where the census has
    no visibility — see the `census-visible reader:` column)

**What this search returned, 2026-10-10 (9 rows, 0 differ).**  It falsified the
claim it was written to test.  Two rows matter:

  * Every row whose *own body* reads `forloop` (`cnt`, `len`, `iflen`, `up`,
    `uplen`, `fmt`, `both`, `bothc`) is **NOT sealed** — a body that mentions
    `forloop` never seals.  So `write_frame` (family B) never runs with a
    `forloop` read on its path, and the `length` key of *that* dict is
    unobservable for a structural reason, not a statistical one.
  * `plen` (nested loop) **seals** — family A — and its inner `ForNode` is
    Python's, reading `forloop.parentloop.length` off the dict `run_stated`
    builds.  Mutant A″ (drop `run_stated`'s `length` write) makes it render `''`
    where this renders `'333'`.  So the `length` write **is** observable, on the
    other state path — which the earlier mutant A, confined to `write_frame`,
    could not reach.  See `stated_forloop_route.mutant-stated-length.txt`.

    .venv/bin/python -I <this file>
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

import bench_engines  # noqa: E402

from django_rust_templates import _native  # noqa: E402

LIBRARIES = {"reg": "mylib"}

HELD = {"rows": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
SURRO = {"rows": [{"name": "\ud800"}, {"name": "b"}, {"name": "c"}]}

L = "{% load reg %}{% for r in rows %}"

# Each row is (label, source, data).  Every source reads `forloop` somewhere in a
# loop body; `SURRO` rows carry a lone surrogate so one node refuses.  Built by
# concatenation rather than an f-string: the brace doubling an f-string needs for
# `{{ r.name }}` is a place a reader can mis-count, and this file is read for its
# rows.
ROWS = [
    ("cnt", L + "{{ r.name }}{{ forloop.counter }}{% endfor %}", HELD),
    ("len", L + "{{ r.name }}{{ forloop.length }}{% endfor %}", HELD),
    ("plen", L + "{% for c in r.name %}{{ forloop.parentloop.length }}{% endfor %}{% endfor %}", HELD),
    ("iflen", L + "{{ r.name }}{% if forloop.length %}{{ r.name }}{% endif %}{% endfor %}", HELD),
    ("up", L + "{{ r.name }}{{ forloop|upper }}{% endfor %}", HELD),
    ("uplen", L + "{{ r.name }}{{ forloop.length|upper }}{% endfor %}", HELD),
    ("fmt", L + '{{ r.name }}{{ forloop.length|stringformat:"s" }}{% endfor %}', HELD),
    # closest approach: a refusal and a `forloop` read in one sealed body
    ("both", L + "{{ r.name|upper }}{{ forloop.length }}{% endfor %}", SURRO),
    ("bothc", L + "{{ r.name|upper }}{{ forloop.counter }}{% endfor %}", SURRO),
]


def main() -> int:
    failures = 0
    stock = bench_engines.build("django", {}, LIBRARIES)
    ours = bench_engines.build("rust", {}, LIBRARIES)
    try:
        for label, source, data in ROWS:
            want = stock.from_string(source).render(stock.context(data))
            handle = ours.from_string(source)
            _native.census_start()
            got = handle.render(ours.context(data))
            handled, delegated, _, _, _ = _native.census_take()
            h = dict(handled)
            d = dict(delegated)
            sealed = "sealed" if h.get("ForNode") else "NOT sealed"
            # The census sees only nodes the walk visits.  A `forloop` read that
            # lives inside a delegated node's own render (the `plen` row's inner
            # `ForNode`, which is Python's) is executed on the far side of the
            # seam and shows up as *nothing* here — which is why this column says
            # "census" and not "who reads it".
            if h.get("VariableNode") and not d.get("VariableNode"):
                reader = "census: a VariableNode is handled by rust"
            elif d.get("VariableNode"):
                reader = "census: a VariableNode is delegated to python"
            else:
                reader = ("census sees none — the read is inside a delegated "
                          "node's own render, or the body has no read")
            mark = "ok  " if want == got else "DIFF"
            if want != got:
                failures += 1
            print(f"{mark} {label:6} {source}")
            print(f"         out: {got!r}")
            print(f"         {sealed:11} | refusal path: {'yes' if d.get('VariableNode') and h.get('ForNode') else 'no'}")
            print(f"         {reader}")
            print(f"         handled:   {', '.join(f'{n} x{c}' for n, c in handled) or 'none'}")
            print(f"         delegated: {', '.join(f'{n} x{c}' for n, c in delegated) or 'none'}")
    finally:
        stock.close()
        ours.close()
    print(f"\n{len(ROWS)} rows, {failures} differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
