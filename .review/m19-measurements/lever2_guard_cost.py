"""What the guard costs — the second half of lever 2's price.

`ceiling_lever2.py` bounds the lever at ~30 ns/iteration. But §5.5's lever 2 as
the fourth round corrected it needs a per-iteration guard: `type(context)`
alone is not the question, because `Context.__setitem__` can be rebound while a
body runs. So the lever's price is the statement *with* the check in it, and
this measures that, on the same stand-in class.

The check is a `_PyType_Lookup`-cached attribute read plus an identity compare.
The fallback arm is never taken here; it is in the statement because a guard
whose alternative is unwritten is not a guard.
"""

import timeit

N = 400_000
REPEAT = 21


class Transcribed:
    def __init__(self):
        self.dicts = [{}]

    def __setitem__(self, key, value):
        self.dicts[-1][key] = value


BASE = Transcribed.__setitem__


def main():
    c = Transcribed()
    lst = c.dicts
    g = {"c": c, "lst": lst, "BASE": BASE}

    guarded = """
if type(c).__setitem__ is BASE:
    lst[-1]['item'] = 3
else:
    c['item'] = 3
"""
    rows = [
        ("shipped: `context[key] = item`", "c['item'] = 3"),
        ("the lever, unguarded", "lst[-1]['item'] = 3"),
        ("the guard alone: `type(c).__setitem__`", "type(c).__setitem__"),
        ("the guard: `type(c).__setitem__ is BASE`", "type(c).__setitem__ is BASE"),
        ("the lever, guarded", guarded),
    ]
    print(f"CPython {__import__('sys').version.split()[0]}, min of {REPEAT} x {N} reps, ns/op\n")
    got = {}
    for label, stmt in rows:
        got[label] = min(timeit.repeat(stmt, globals=g, number=N, repeat=REPEAT)) / N * 1e9
        print(f"{label:<42} {got[label]:>7.2f}")

    shipped = got["shipped: `context[key] = item`"]
    print()
    print(f"{'net, lever with no guard':<42} {shipped - got['the lever, unguarded']:>7.2f} ns/iteration")
    print(f"{'net, lever with the guard':<42} {shipped - got['the lever, guarded']:>7.2f} ns/iteration")


if __name__ == "__main__":
    main()
