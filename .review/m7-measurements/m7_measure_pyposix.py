#!/usr/bin/env python3
"""Is the plan's `pyposix` port actually `posixpath`?

The M7 plan's Task 2 ships three hand-written functions (`join`, `dirname`,
`normpath`) with a table of measured rows as their evidence. That table is what a
reader can check by eye; this is what says the table is not the whole story, and
it is the second half of a review finding — the two rows below that the review
added (`normpath("///a//b")`, `dirname("//a")` and `dirname("///a")`) close
specific dead guards, but "the table passes" was never the claim being tested.

The port is transliterated to Python from the Rust in the plan's Step 3, which
is a real limit and the reason this is a *plan* measurement rather than Task 2's
gate: a transliteration cannot see a byte-versus-char slicing defect, and the
Rust's `&p[..i]` is where such a defect would live. Task 2's gate is `cargo test`
and this is not a substitute for it.

Run: `.venv/bin/python -I .review/m7-measurements/m7_measure_pyposix.py`
`-I` because this reads the pinned interpreter's `posixpath` and nothing here
should be importable from the working directory.
"""

import itertools
import posixpath
import random
import sys

# --- the plan's Rust, transliterated ----------------------------------------


def join(a, b):
    if b.startswith("/"):
        return b
    if a == "" or a.endswith("/"):
        return a + b
    return a + "/" + b


def dirname(p):
    i = p.rfind("/")
    i = 0 if i == -1 else i + 1
    head = p[:i]
    if head != "" and head != "/" * len(head):
        return head.rstrip("/")
    return head


def normpath(path):
    if path == "":
        return "."
    if not path.startswith("/"):
        initial_slashes = 0
    elif path.startswith("//") and not path.startswith("///"):
        initial_slashes = 2
    else:
        initial_slashes = 1
    new_comps = []
    for comp in path.split("/"):
        if comp in ("", "."):
            continue
        if comp != ".." or (initial_slashes == 0 and not new_comps) or (
            new_comps and new_comps[-1] == ".."
        ):
            new_comps.append(comp)
        elif new_comps:
            new_comps.pop()
    out = "/".join(new_comps)
    if initial_slashes > 0:
        out = "/" * initial_slashes + out
    return "." if out == "" else out


# --- the plan's tables, as the plan writes them ------------------------------

TABLE_NORMPATH = [
    ("", "."), (".", "."), ("..", ".."), ("a/..", "."), ("a/../..", ".."),
    ("./a", "a"), ("a//b", "a/b"), ("//a//b", "//a/b"), ("///a//b", "/a/b"),
    ("/a/../b", "/b"), ("a/./b", "a/b"), ("../a", "../a"), ("./../a", "../a"),
]
TABLE_DIRNAME = [
    ("", ""), ("a", ""), ("a/b", "a"), ("a/b/", "a/b"), ("/a", "/"),
    ("//a", "//"), ("///a", "///"), ("sub/index.html", "sub"),
    ("/sub/index.html", "/sub"),
]
TABLE_JOIN = [
    ("", "", "."), ("a", "b", "a/b"), ("a", "", "a"), ("", "b", "b"),
    ("a/b", "../c", "a/c"), ("a", "./b", "a/b"), ("sub", "../x", "x"),
    ("a/b/c", "../../x", "a/x"), ("", "./b", "b"), ("", "../b", "../b"),
    (".", "b", "b"), ("a", ".", "a"), ("a/..", "b", "b"), ("//a", "b", "//a/b"),
    ("/a", "b", "/a/b"),
]

# Every string over this alphabet up to this length, plus random longer ones.
ALPHABET = "/.ab"
EXHAUSTIVE_LENGTH = 7
RANDOM_SEED = 20261009
RANDOM_COUNT = 200_000
JOIN_LEFT, JOIN_RIGHT = 4000, 200


def check_table():
    """Each table row, against both `posixpath` and the plan's stated answer."""
    bad = 0
    for source, want in TABLE_NORMPATH:
        got, ref = normpath(source), posixpath.normpath(source)
        if (got, ref) != (want, want):
            bad += 1
            print(f"normpath table {source!r}: port {got!r} posixpath {ref!r} want {want!r}")
    for source, want in TABLE_DIRNAME:
        got, ref = dirname(source), posixpath.dirname(source)
        if (got, ref) != (want, want):
            bad += 1
            print(f"dirname table {source!r}: port {got!r} posixpath {ref!r} want {want!r}")
    for a, b, want in TABLE_JOIN:
        got = normpath(join(a, b))
        ref = posixpath.normpath(posixpath.join(a, b))
        if (got, ref) != (want, want):
            bad += 1
            print(f"join table ({a!r}, {b!r}): port {got!r} posixpath {ref!r} want {want!r}")
    return len(TABLE_NORMPATH) + len(TABLE_DIRNAME) + len(TABLE_JOIN), bad


def check_unary():
    rng = random.Random(RANDOM_SEED)
    inputs = [
        "".join(t)
        for n in range(EXHAUSTIVE_LENGTH + 1)
        for t in itertools.product(ALPHABET, repeat=n)
    ]
    inputs += [
        "".join(rng.choice(ALPHABET) for _ in range(rng.randint(8, 14)))
        for _ in range(RANDOM_COUNT)
    ]
    bad = 0
    for source in inputs:
        for name, fn, ref in (
            ("normpath", normpath, posixpath.normpath),
            ("dirname", dirname, posixpath.dirname),
        ):
            if fn(source) != ref(source):
                bad += 1
                if bad < 15:
                    print(f"{name}({source!r}): port {fn(source)!r} posixpath {ref(source)!r}")
    return inputs, bad


def check_join(inputs):
    rng = random.Random(RANDOM_SEED + 1)
    left = rng.sample(inputs, min(JOIN_LEFT, len(inputs)))
    right = rng.sample(inputs, min(JOIN_RIGHT, len(inputs)))
    pairs = [(a, b) for a in left for b in right]
    bad = 0
    for a, b in pairs:
        got = normpath(join(a, b))
        ref = posixpath.normpath(posixpath.join(a, b))
        if got != ref:
            bad += 1
            if bad < 15:
                print(f"normpath(join({a!r}, {b!r})): port {got!r} posixpath {ref!r}")
    return pairs, bad


def main():
    print("posixpath:", posixpath.__file__)
    print("python:", sys.version.split()[0])
    rows, table_bad = check_table()
    inputs, unary_bad = check_unary()
    pairs, join_bad = check_join(inputs)
    print(f"table rows: {rows}  mismatches: {table_bad}")
    print(f"sweep inputs: {len(inputs)}  unary mismatches: {unary_bad}")
    print(f"join pairs: {len(pairs)}  mismatches: {join_bad}")
    return 1 if table_bad or unary_bad or join_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
