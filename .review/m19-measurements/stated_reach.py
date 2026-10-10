"""Reachability: did `Walk::run_stated` actually run for a delegate in a loop body?

The differential probe (`stated_delegate.py`) shows the two engines agree, which
is what a *wrong* implementation would also show if the loop simply refused to
seal and Python rendered the body. So agreement is not evidence that the new path
ran. This is.

The census is the instrument: a body that seals with a delegate inside it reads
**native nodes as `handled` and the delegate as `delegated` in one and the same
loop**. Before this slice that combination could not exist — `sealable` refused
any body holding an `Act::Delegate`, so every non-delegate node in the body was
handed to Python too. The row set is the positive control (a pure sealed loop,
whose `handled` shape is already known) beside the subject.

Run from the project root:

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

DATA = {"rows": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}

ROWS = {
    # positive control: sealed, no delegate — the shape this slice must not change
    "control": "{% load reg %}{% for r in rows %}{{ r.name }}{% endfor %}",
    # the subject: same loop, one delegate in the body
    "subject": "{% load reg %}{% for r in rows %}{{ r.name }}{% noop %}{% endfor %}",
    # two delegates, so the per-iteration write count is not the delegate count
    "two": "{% load reg %}{% for r in rows %}{{ r.name }}{% noop %}{% noop %}{% endfor %}",
    # the delegate reads `forloop`, which only the context write can supply
    "ctx": "{% load reg %}{% for r in rows %}{% shout r.name %}{% endfor %}",
}


def main() -> int:
    engine = bench_engines.build("rust", {}, {"reg": "mylib"})
    try:
        for key, source in ROWS.items():
            handle = engine.from_string(source)
            context = engine.context(DATA)
            _native.census_start()
            out = handle.render(context)
            handled, delegated, _, _, _ = _native.census_take()
            h = ", ".join(f"{name} x{count}" for name, count in handled) or "none"
            d = ", ".join(f"{name} x{count}" for name, count in delegated) or "none"
            print(f"{key:8} {source}")
            print(f"         out: {out!r}")
            print(f"         handled:   {h}")
            print(f"         delegated: {d}")
            print()
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
