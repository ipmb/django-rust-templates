"""Ceiling probe for §5.5 lever 2 — the loop's write routed at the innermost dict.

NOT a measurement of the slice. This is a `timeit` probe over a stand-in class
whose `__setitem__` reproduces `BaseContext.__setitem__`'s body verbatim
(`python/django_rust_templates/engine/context.py:69-71`,
`self.dicts[-1][key] = value`). It answers one question before anything is
built: is the Python-level `__setitem__` dispatch large enough, as a ceiling,
to be worth building and pricing on the real render path?

The render path's own per-iteration cost is a different quantity, and is
measured by `loop_keys_ab.py` over two builds, not here.
"""

import timeit

N = 400_000
REPEAT = 21


class Transcribed:
    """`BaseContext`, with only what the write touches."""

    def __init__(self):
        self.dicts = [{}]

    def __setitem__(self, key, value):
        self.dicts[-1][key] = value


def bench(stmt, glb):
    return min(timeit.repeat(stmt, globals=glb, number=N, repeat=REPEAT)) / N * 1e9


def main():
    c = Transcribed()
    lst = c.dicts  # the list object, cached once per loop
    layer = lst[0]  # the innermost dict, i.e. `lst[-1]` at this moment
    g = {"c": c, "lst": lst, "layer": layer, "plain": {}}

    rows = [
        ("shipped: `context[key] = item`", "c['item'] = 3"),
        ("lever: `lst[-1][key] = item` (list cached)", "lst[-1]['item'] = 3"),
        ("lever without caching the list", "c.dicts[-1]['item'] = 3"),
        ("`context.dicts` alone (the lookup row 3 adds)", "c.dicts"),
        ("floor: a bare dict, `plain[key] = item`", "plain['item'] = 3"),
    ]
    print(f"CPython {__import__('sys').version.split()[0]}, min of {REPEAT} x {N} reps, ns/op\n")
    got = {}
    for label, stmt in rows:
        got[label] = bench(stmt, g)
        print(f"{label:<46} {got[label]:>7.2f}")

    a = got["shipped: `context[key] = item`"]
    b = got["lever: `lst[-1][key] = item` (list cached)"]
    print(f"\nsaving, list cached{'':<26} {a - b:>7.2f} ns/iteration")
    print(f"saving, list re-read each time{'':<20} {a - got['lever without caching the list']:>7.2f} ns/iteration")


if __name__ == "__main__":
    main()
