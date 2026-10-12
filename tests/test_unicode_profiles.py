"""The interpreter's Unicode release governs native parsing, not only fixture data."""

from pathlib import Path
import unicodedata
import unittest

from django.template.base import FilterExpression, Parser
from django_rust_templates import _native


class UnicodeProfileTests(unittest.TestCase):
    def test_newly_assigned_word_characters_use_the_interpreters_grammar(self):
        root = Path(__file__).resolve().parents[1] / "crates/template-core/src"

        def codepoints(path):
            values = set()
            for line in path.read_text().splitlines():
                lo, hi = (int(part, 16) for part in line.split())
                values.update(range(lo, hi + 1))
            return values

        newest = codepoints(root / "pyregex_word_ranges.txt")
        oldest = codepoints(root / "pyunicode/word_15_0.txt")
        self.assertGreater(len(newest - oldest), 4000)
        parser = Parser([])
        for cp in newest - oldest:
            token = "x" + chr(cp)
            expected = chr(cp).isalnum()
            for label, parse in (
                ("Django", lambda: FilterExpression(token, parser)),
                ("native", lambda: _native.parse_filter_expression(token)),
            ):
                try:
                    parse()
                    accepted = True
                except Exception:
                    accepted = False
                self.assertEqual(
                    accepted,
                    expected,
                    f"{label} U+{cp:04X} under Unicode {unicodedata.unidata_version}",
                )
