#!/usr/bin/env python3
"""Generate `crates/template-core/src/pyregex_word_ranges.txt`: Python's `\\w`.

Django builds `filter_re` on `\\w` (`django/template/base.py:705` and `:714`),
and Python's `\\w` for `str` patterns is exactly `str.isalnum() or '_'`. Both
spellings are checked against each other over every code point a `str` can
hold, so the generator's own correctness is two independent predicates agreeing
plus two pinned counts.

Why ranges and not code points: the set is 142,940 code points, which as a Rust
list is 1,805,848 bytes of source; the 771 inclusive ranges are 8,033 bytes
(225x smaller), and a divergence reads `U+XXXX-U+YYYY` rather than one needle
in 143k.

Usage:
    python -I scripts/gen_python_word_ranges.py           # write the file
    python -I scripts/gen_python_word_ranges.py --check   # verify it is current

`--check` exits 1 when the committed file and CPython disagree, which is what
notices a CPython `unicodedata` revision moving the table.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "crates" / "template-core" / "src" / "pyregex_word_ranges.txt"

EXPECTED_CODEPOINTS = 142_940
EXPECTED_RANGES = 771


def python_word_codepoints():
    """Python's `\\w` for `str`, with `re` and `isalnum()` cross-checked.

    A disagreement raises rather than being silently resolved: `\\w` is
    documented as `isalnum() or '_'`, so if CPython ever stops agreeing, the
    table the guard is built on is the question and the generator must say so.
    """
    word = re.compile(r"\w")
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        by_re = word.fullmatch(ch) is not None
        by_pred = ch.isalnum() or ch == "_"
        if by_re != by_pred:
            raise SystemExit(f"U+{cp:04X}: re.fullmatch disagrees with isalnum()")
        if by_re:
            yield cp


def to_ranges(codepoints):
    ranges = []
    for cp in codepoints:
        if ranges and ranges[-1][1] + 1 == cp:
            ranges[-1][1] = cp
        else:
            ranges.append([cp, cp])
    return ranges


def render(ranges):
    return "".join(f"{lo:X} {hi:X}\n" for lo, hi in ranges)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    ranges = to_ranges(python_word_codepoints())
    total = sum(hi - lo + 1 for lo, hi in ranges)
    if len(ranges) != EXPECTED_RANGES or total != EXPECTED_CODEPOINTS:
        raise SystemExit(
            f"the table moved: {len(ranges)} ranges (expected {EXPECTED_RANGES}), "
            f"{total} code points (expected {EXPECTED_CODEPOINTS})"
        )

    text = render(ranges)
    if args.check:
        if OUT.read_text() != text:
            raise SystemExit(f"{OUT} is stale; re-run without --check")
        print(f"{OUT.name} is current: {len(ranges)} ranges, {total} code points")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT}: {len(ranges)} ranges, {len(text)} bytes")


if __name__ == "__main__":
    main()
