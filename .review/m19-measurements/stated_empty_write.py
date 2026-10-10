"""The one place a loop body's context write can survive the loop, asked of both engines.

`ForNode.render` wraps **everything** in `with context.push():` — the sequence
resolution, the empty test, and the loop
(`.django/django/template/defaulttags.py:196`, before the `if len_values < 1` at
204).  So nothing a `{% for %}` renders writes into a layer that outlives the
tag, including its `{% empty %}` body.  A walk that renders the empty body
*outside* any push would let a delegate's write escape, where Django discards it.

The search this file is: does that shape exist, and does it diverge?  It is
written because slice 2's `run_for` puts the push after the empty test — which is
where the shipped code already had it — and an unreachable divergence and a
reachable one are different findings, so the difference has to be *measured*
rather than reasoned about.

Both a non-empty and an empty sequence are run for each body shape, because a row
that DIFFs empty and not full localises the divergence to the empty path rather
than to delegates or to context writes in general.

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

LIBRARIES = {"reg": "mylib"}

# `{% for r in none %}` iterates nothing, so its `{% empty %}` body is what
# renders; `{% for r in one %}` renders its body once and never the empty arm.
BODIES = {
    # a node the walk models — the body seals
    "model": "{{ r }}",
    # a delegate — the body states, and the delegate is the only thing that can
    # write into a context at all
    "delegate": '{% include "w.html" %}',
    # a `forloop` read — the body refuses, which is the third route to a delegate
    "refused": "{% if forloop %}{{ r }}{% endif %}",
    # nothing but text, so the empty arm has no delegate in it at all
    "text": "none",
    # a delegate that pushes nothing of its own: the `{% include %}` rows above
    # cannot reach the shape this is about, because `IncludeNode.render` pushes a
    # layer of its own and the write is discarded there whatever the loop did.
    "tag": '{% writes "foo" %}',
}
TEMPLATES = {"w.html": '{% load reg %}{% writes "W" %}'}
for _name, _body in BODIES.items():
    TEMPLATES[f"empty_{_name}.html"] = (
        "{% for r in none %}{{ r }}{% empty %}" + _body + "{% endfor %}"
    )
    TEMPLATES[f"full_{_name}.html"] = (
        "{% for r in one %}" + _body + "{% empty %}{{ r }}{% endfor %}"
    )

DATA = {"none": [], "one": ["a"]}
PROBE = "[{{ foo }}]"


def main() -> int:
    failures = 0
    stock = bench_engines.build("django", TEMPLATES, LIBRARIES)
    ours = bench_engines.build("rust", TEMPLATES, LIBRARIES)
    try:
        # A fresh context per row: the *subject* is whether a write outlives one
        # render, and a context reused across rows would answer a different one.
        for key in sorted(TEMPLATES):
            if key == "w.html":
                continue
            source = "{% load reg %}" + TEMPLATES[key] + PROBE
            a = stock.from_string(source).render(stock.context(DATA))
            b = ours.from_string(source).render(ours.context(DATA))
            mark = "ok  " if a == b else "DIFF"
            if a != b:
                failures += 1
            print(f"{mark} {key:18} {source}")
            if a != b:
                print(f"       stock: {a!r}")
                print(f"       ours : {b!r}")
    finally:
        stock.close()
        ours.close()
    rows = len([k for k in TEMPLATES if k != "w.html"])
    print(f"\n{rows} rows, {failures} differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
