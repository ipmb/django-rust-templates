"""Python's `\\w`, pinned where both languages exist.

The Rust guard in `crates/template-core/src/pyregex.rs` compares the *compiled*
`regex` class against the range table, which is what catches a `regex-syntax`
UCD move — `regex = "1"` lets a routine `cargo update` change it, and nothing in
`scripts/check.sh` would notice. That guard cannot see CPython, and this file
cannot see `regex-syntax`, so each covers the side the other cannot.

This file re-derives Python's `\\w` from `str.isalnum()`, checks the committed
table against it (catching a CPython `unicodedata` revision moving the table),
and builds the two splice strings from Django's own `filter_raw_string` rather
than a transcription.
"""

import sys
import unicodedata
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
RANGES_PATH = (
    PROJECT_ROOT / "crates" / "template-core" / "src" / "pyregex_word_ranges.txt"
)
SPLICES_PATH = PROJECT_ROOT / "tests" / "pyregex_splices.txt"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template.base import filter_raw_string  # noqa: E402

# The Rust spellings, as published by `template_core::pyregex`. Written here as
# strings because at this point in the plan there is no `_native` binding for
# them; `pyregex.rs`'s own tests pin that these are the constants' values.
PYTHON_WORD_STANDALONE = r"[\p{L}\p{N}_]"
PYTHON_WORD_IN_CLASS = r"\p{L}\p{N}_"


def measured_word_codepoints():
    """CPython's `\\w`, independently of the committed table."""
    return {
        cp
        for cp in range(0x110000)
        if not (0xD800 <= cp <= 0xDFFF) and (chr(cp).isalnum() or chr(cp) == "_")
    }


def committed_ranges():
    ranges = []
    for line in RANGES_PATH.read_text().splitlines():
        if line.strip():
            lo, hi = (int(part, 16) for part in line.split())
            ranges.append((lo, hi))
    return ranges


class WordTableTests(unittest.TestCase):
    def test_the_committed_table_is_cpythons_word_set(self):
        committed = set()
        for lo, hi in committed_ranges():
            committed.update(range(lo, hi + 1))
        self.assertEqual(
            sorted(committed ^ measured_word_codepoints()),
            [],
            "code points where the committed table and CPython disagree",
        )

    def test_the_measured_counts(self):
        # These two counts *are* the measurement, and they are the whole pin:
        # they fire exactly when the table moves, whatever CPython calls its
        # Unicode database. Deliberately *not* an assertion on
        # `unicodedata.unidata_version`, which is a proxy for the question and is
        # wrong in both directions — a CPython upgrade shipping a new UCD that
        # does not touch the word table would raise a false alarm, while a
        # backport that fixed the table without bumping the version string would
        # give false silence. The version travels in the failure message as
        # diagnostics instead, where it costs nothing.
        ranges = committed_ranges()
        total = sum(hi - lo + 1 for lo, hi in ranges)
        self.assertEqual(
            (len(ranges), total),
            (771, 142_940),
            "the committed table is stale for "
            f"unicodedata {unicodedata.unidata_version}; re-run "
            "scripts/gen_python_word_ranges.py and record the new counts",
        )

    def test_the_table_is_canonical(self):
        ranges = committed_ranges()
        for (lo1, hi1), (lo2, _hi2) in zip(ranges, ranges[1:]):
            self.assertLess(hi1 + 1, lo2, f"not canonical near {(lo1, hi1)}")


class SpliceTests(unittest.TestCase):
    """Django's two `\\w` positions, spliced with our spellings.

    A `cargo test` cannot read `.django`, so the right-hand side is built here,
    from Django's own `filter_raw_string`, and written to `pyregex_splices.txt`
    for `pyregex.rs`'s splice test to read. That is what makes the Rust
    assertion compare against Django rather than against a transcription.
    """

    FILTER_NAME_ATOM = f"(?P<filter_name>{PYTHON_WORD_STANDALONE}+)"
    VAR_CHARS_ATOM = f"[{PYTHON_WORD_IN_CLASS}\\.\\+-]+"

    def test_each_form_splices_where_django_writes_a_bare_w(self):
        ours = filter_raw_string
        ours = ours.replace(r"(?P<filter_name>\w+)", self.FILTER_NAME_ATOM)
        ours = ours.replace(r"[\w\.\+-]+", self.VAR_CHARS_ATOM)

        # No bare `\w` survives: every one of Django's is now one of ours.
        self.assertNotIn(r"\w", ours)
        # Ours occur exactly as often as Django's did — 1 and 2, measured.
        self.assertEqual(ours.count(self.FILTER_NAME_ATOM), 1)
        self.assertEqual(ours.count(self.VAR_CHARS_ATOM), 2)

    def test_the_committed_fixture_is_what_django_produces(self):
        expected = (
            f"filter_name {self.FILTER_NAME_ATOM}\n"
            f"var_chars {self.VAR_CHARS_ATOM}\n"
        )
        self.assertEqual(SPLICES_PATH.read_text(), expected)


if __name__ == "__main__":
    unittest.main()
