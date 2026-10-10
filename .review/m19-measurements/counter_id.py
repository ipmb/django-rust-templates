#!/usr/bin/env python
"""Does our loop's counter write build a Python object, or hand back a cached one?

`.review/m19-measurements/loop-keys.md` §5 claimed the lever left *"a fresh Python
int or bool per counter per iteration"* in place, and the claim is checkable
rather than arguable: CPython's small-int cache makes `id()` stable for the values
a loop's counters take, so if the write path goes through the cache the same
object comes back on every render, and if it allocates a new one the ids differ.

The measurement is three renders of one loop, with a filter that returns `id()` of
each counter, compared across the renders. It runs on **this tree's installed
artifact**, so the answer is a fact about the shipped path and not about PyO3 in
the abstract.

Run:  .venv/bin/python -I <this>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "differential"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django.template import Library  # noqa: E402

from django_rust_templates.engine import Engine  # noqa: E402
from django_rust_templates.engine.context import Context  # noqa: E402


# **Kept alive deliberately.** `id()` is an address, so comparing the ids of two
# objects that are already dead compares addresses CPython is free to reuse — and
# `int(v)` inside one expression produces exactly that trap: a temporary dies
# before the next line and the allocator hands the same address back, so
# `id(int("1000")) == id(int("1000"))` reads `True` for a value the cache does not
# hold. Every object this probe is a claim about is held here for the process's
# life, so an equal id can only mean the same object.
ALIVE: list[object] = []


def main() -> int:
    lib = Library()
    lib.filter("idof", lambda v: (ALIVE.append(v), id(v))[1])
    lib.filter("kindof", lambda v: type(v).__name__)

    engine = Engine()
    engine.template_builtins.append(lib)

    source = "{% for x in xs %}{{ forloop.counter|idof }},{% endfor %}"
    ids = []
    for _ in range(3):
        rendered = engine.from_string(source).render(Context({"xs": [0, 1, 2, 3]}))
        ids.append([int(part) for part in rendered.split(",") if part])

    print("counter ids, three renders of a 4-item loop:")
    for run, row in enumerate(ids):
        print(f"  run {run}: {row}")
    print("same objects across runs:", ids[0] == ids[1] == ids[2])
    print("all distinct within a run:", len(set(ids[0])) == len(ids[0]))

    # The boundary of the cache, measured here rather than quoted from the docs —
    # and measured with **both objects alive at the comparison**, which is the one
    # thing this block got wrong in its first version.
    print()
    print("CPython's small-int cache, at the boundary (both objects alive):")
    for value in ("0", "256", "257", "1000"):
        first, second = int(value), int(value)
        print(f'  int("{value}") cached: {id(first) == id(second)}')
    print("bool singletons:", (1 == 1) is True, (1 == 0) is False)
    print()
    print("the first iteration's counter is the interpreter's own 1:", ids[0][0] == id(1))

    kinds = engine.from_string(
        "{% for x in xs %}{{ forloop.first|kindof }},{{ forloop.counter|kindof }};{% endfor %}"
    ).render(Context({"xs": [0, 1]}))
    print("kinds of `first` and `counter`:", kinds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
