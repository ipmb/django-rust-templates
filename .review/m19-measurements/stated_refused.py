"""The one route to `Walk::write_frame`, and what it is load-bearing for.

`delegate_slot` takes a `Frame` (rather than returning `delegate(slot)` at once)
only when the enclosing loop is **sealed and did not write the context** — a body
with no `Act::Delegate`, so `ForPlan::context_state` is false and `Frame`'s
`in_context` is false.  A node in such a body is delegated only on
`Rendered::Refused`, and that enum has exactly one producer: a filter whose answer
is a lone surrogate (`cannot_hold`) — see `crates/template-py/src/lib.rs:3996`.

**A custom filter cannot make this shape**, which is the trap this file walked
into first: a filter the Rust registry does not answer is an `Act::Delegate` at
plan time, so it sets `context_state` and `delegate_slot` returns early.  The
refusal needs a filter the walk *plans* — `upper` — fed a value that is a lone
surrogate, so the seam answers `cannot_hold` only once the loop is already sealed.
The surrogate therefore lives in the **data**, not in the tag library.

The row set is built so each claim has its own row:

  * `held` — the same loop with a value Rust can hold.  Control: nothing refuses,
    and the census must show the body planned.
  * `refuse` — the value is a lone surrogate, so the node refuses at render time.
    Reachability: the loop's `ForNode` and its other nodes are in `handled:`
    while the refused node is in `delegated:`, in one loop.
  * `loopvar` — the refused head **is** the loop variable, so the delegated
    `VariableNode.render` re-resolves it *from the context*, which only
    `write_frame` wrote.  Without that write the row renders `string_if_invalid`
    where stock renders the value — this is `write_frame`'s load-bearing row.
  * `after` — the same, with the loop variable read back after the loop.

Every row is rendered by stock Django and by ours and compared byte for byte.

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
SURRO = {"rows": [{"name": "\ud800"}]}

ROWS = {
    "held": ("{% load reg %}{% for r in rows %}{{ r.name|upper }}{% endfor %}", HELD),
    "refuse": ("{% load reg %}{% for r in rows %}{{ r.name|upper }}{% endfor %}", SURRO),
    "loopvar": (
        "{% load reg %}{% for r in rows %}{{ r|upper }}{% endfor %}",
        {"rows": ["\ud800", "x"]},
    ),
    "after": (
        "{% load reg %}{% for r in rows %}{{ r|upper }}{% endfor %}[{{ r }}]",
        {"rows": ["\ud800", "x"]},
    ),
}


def main() -> int:
    failures = 0
    stock = bench_engines.build("django", {}, LIBRARIES)
    ours = bench_engines.build("rust", {}, LIBRARIES)
    try:
        for key, (source, data) in ROWS.items():
            a = stock.from_string(source).render(stock.context(data))
            handle = ours.from_string(source)
            context = ours.context(data)
            _native.census_start()
            b = handle.render(context)
            handled, delegated, _, _, _ = _native.census_take()
            # A lone surrogate cannot be printed, so a row carrying one is shown
            # by its code points rather than by `{!r}`.
            mark = "ok  " if a == b else "DIFF"
            if a != b:
                failures += 1
            h = ", ".join(f"{n} x{c}" for n, c in handled) or "none"
            d = ", ".join(f"{n} x{c}" for n, c in delegated) or "none"
            print(f"{mark} {key:8} {source}")
            if a != b:
                print(f"       stock: {[hex(ord(c)) for c in a]}")
                print(f"       ours : {[hex(ord(c)) for c in b]}")
            print(f"       handled:   {h}")
            print(f"       delegated: {d}")
    finally:
        stock.close()
        ours.close()
    print(f"\n{len(ROWS)} rows, {failures} differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
