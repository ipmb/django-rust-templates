"""Context-layer balance across a stated loop — including the failing path.

`Walk::run_stated` pushes a layer for the whole loop and pops it in the caller,
which means an exception travelling through the body is a place the layer could
be left behind. Django gets this from `with`; this walk calls `__exit__` on both
paths, and this is the check that it does.

Both engines are measured, because the observable is a property of the *context*
and the row is only interesting if both agree on it: the dict stack's depth, and
what a lookup of the loop variable finds after the loop.

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

DATA = {"rows": [{"name": "a"}, {"name": "b"}]}

GOOD = "{% load reg %}{% for r in rows %}{{ r.name }}{% noop %}{% endfor %}"
BAD = "{% load reg %}{% for r in rows %}{{ r.name }}{% boom %}{% endfor %}"
PROBE = "{{ r }}|{{ forloop }}"


def run(engine, source, data):
    handle = engine.from_string(source)
    context = engine.context(data)
    before = len(context.dicts)
    error = None
    try:
        out = handle.render(context)
    except ValueError as exc:
        out, error = None, exc
    after = len(context.dicts)
    # What the *same* context says about the loop's two names afterwards.
    probe = engine.from_string(PROBE).render(context)
    # The exception's *identity* is per-raise and would make every failing row
    # differ; what the two engines owe each other is the type and the message.
    return (
        before,
        after,
        out,
        type(error).__name__ if error else None,
        str(error) if error else None,
        probe,
    )


def main() -> int:
    stock = bench_engines.build("django", {}, {"reg": "mylib"})
    ours = bench_engines.build("rust", {}, {"reg": "mylib"})
    bad = 0
    try:
        for name in ("success", "failure"):
            source = GOOD if name == "success" else BAD
            results = {}
            for engine, label in ((stock, "stock"), (ours, "ours ")):
                results[label] = run(engine, source, DATA)
            s, o = results["stock"], results["ours "]
            agree = s == o
            bad += not agree
            print(f"{name}: {'ok' if agree else 'DIFF'}  {source}")
            for label in ("stock", "ours "):
                before, after, out, kind, message, probe = results[label]
                print(
                    f"   {label}  dicts {before} -> {after}"
                    f"  out={out!r} err={kind}"
                    f"  probe={probe!r}"
                )
    finally:
        stock.close()
        ours.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
