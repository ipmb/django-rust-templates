"""Python's case mapping, pinned where both languages exist.

`char::to_uppercase` and `str.upper()` apply the same Unicode rules from
**different Unicode releases** — Rust's stdlib tables track the toolchain,
CPython's are compiled into the interpreter — so `pyunicode::case` is generated
from the two live implementations rather than typed (`scripts/gen_python_case_fixups.py`).
This file is what makes that a live comparison instead of a one-off measurement.

Measured 2026-10-08 with `rustc 1.98.0` and the pinned `.venv/bin/python`
(CPython 3.14.4, `unicodedata` 16.0.0): 28 code points diverge in each direction.
The count is **not** asserted here, because the exhaustive comparison below
implies it: 28 is the length of `UNCASED`, which the type `[char; 28]` already
pins at compile time, and a divergence the table does not cover fails this test
at the code point by name.

Rust-side table membership is checked in `case.rs`'s own tests; this file cannot
see `char::to_uppercase`, and that one cannot see CPython. Each covers the side
the other cannot.
"""

import unittest

from django_rust_templates import _native


class CaseMappingParityTests(unittest.TestCase):
    def test_every_code_point_matches_python(self):
        """Exhaustive, not sampled: this is the only pin on the Rust half.

        The workspace declares `rust-version = "1.82"`, a floor, and has no
        `rust-toolchain` file, so the toolchain floats. A `rustc` upgrade that
        moves Rust's case tables must fail here, at a named code point, rather
        than silently changing what a template renders.
        """
        for cp in range(0x110000):
            if 0xD800 <= cp <= 0xDFFF:
                # No Rust `&str` can hold a lone surrogate, so it cannot be
                # passed; Python itself maps these to themselves.
                continue
            ch = chr(cp)
            self.assertEqual(
                _native.python_upper(ch), ch.upper(), f"upper differs at U+{cp:04X}"
            )

    def test_the_two_kinds_of_divergence_are_named(self):
        """The two shapes the table covers, in an executable place.

        Documentation rather than the guard: `python_upper` is the identity for
        these code points, so a pass-through implementation would satisfy this
        too — the exhaustive test above is what fails for one, at U+A7CF. What
        this pins is that the *specific instances* the module doc explains are
        still the instances, so a future Unicode release that moves them is
        noticed here as well as there.
        """
        # Unicode 17.0 assigned the capitals and retro-fitted the mapping; these
        # small letters were already named in 16.0.0, so Python knows the
        # character and maps it to itself.
        already_named = [0xA7D3, 0xA7D5]
        # The other Latin Extended-D entry: Python 16.0.0 has no name for it.
        unnamed = [0xA7CF]
        # Beria Erfe, a Unicode 17.0 block (U+16EA0..U+16EDF) that CPython
        # 3.14.4's `unicodedata` 16.0.0 does not know at all — 25 of the 28.
        beria_erfe = [0x16EBB, 0x16EC0, 0x16ED3]

        for cp in already_named + unnamed + beria_erfe:
            ch = chr(cp)
            self.assertEqual(ch.upper(), ch, f"U+{cp:04X}")
            self.assertEqual(_native.python_upper(ch), ch, f"U+{cp:04X}")

    def test_a_growing_mapping_is_not_collapsed(self):
        """`'ß'` is one code point in and two out, in both languages."""
        self.assertEqual(_native.python_upper("ß"), "SS")
        self.assertEqual(_native.python_upper("straße"), "STRASSE")

    def test_there_is_no_lower_accessor(self):
        # The closed half of the obligation. `lower` is delegated because
        # `str.lower()` is context-sensitive (`"ΟΣ".lower()` is `"ος"`, so a
        # per-code-point table cannot express it), and the guard against someone
        # helpfully adding one back is that its consumers stay on the host side:
        # Task 3's derived delegate set names both of them, and
        # `FilterHost::str_lower` is the only route.
        self.assertFalse(hasattr(_native, "python_lower"))


if __name__ == "__main__":
    unittest.main()
