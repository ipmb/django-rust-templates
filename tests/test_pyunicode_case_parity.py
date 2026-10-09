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

import unicodedata
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

        **The two kinds are separated by `unicodedata.name()`, and that is the
        only thing that separates them** — a count of diverging code points
        cannot. An earlier revision of the module doc said "25 Beria Erfe, plus
        U+A7CF, U+A7D3, U+A7D5", which lumped U+A7CF in with the retro-fitted
        mappings; it has no name in 16.0.0, so it is a new *assignment* like the
        Beria Erfe letters. The assertion below is what makes the doc's split
        checked rather than asserted.
        """
        # Unicode 17.0 assigned the capitals and retro-fitted the mappings; the
        # small letters were already named in 16.0.0, so Python knows the
        # character, knows it is lowercase, and maps it to itself. These two are
        # the genuinely interesting case, and only a mapping table can see them.
        retrofitted = [0xA7D3, 0xA7D5]
        # New assignments: 16.0.0 has no name for the code point at all. The
        # Beria Erfe block (U+16EA0..U+16EDF) that Unicode 17.0 introduced, plus
        # U+A7CF whose capital U+A7CE arrived in the same release.
        unassigned_then = [0x16EBB, 0x16EC0, 0x16ED3, 0xA7CF]

        for cp in retrofitted:
            # The distinction, measured: named in the pinned release.
            unicodedata.name(chr(cp))
            self.assertEqual(chr(cp).upper(), chr(cp), f"U+{cp:04X}")
            self.assertEqual(_native.python_upper(chr(cp)), chr(cp), f"U+{cp:04X}")

        for cp in unassigned_then:
            with self.assertRaises(ValueError, msg=f"U+{cp:04X} is named here"):
                unicodedata.name(chr(cp))
            self.assertEqual(chr(cp).upper(), chr(cp), f"U+{cp:04X}")
            self.assertEqual(_native.python_upper(chr(cp)), chr(cp), f"U+{cp:04X}")

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
        # The control, and it is the sibling accessor rather than a fabricated
        # one: `_native` is a module built in Rust, so a `hasattr` against it can
        # be answered `False` by the module failing to expose anything at all.
        # `python_upper` is the accessor the rest of this file calls, so it says
        # the instrument reaches the module and that the absence above is the
        # module's answer.
        self.assertTrue(hasattr(_native, "python_upper"))


if __name__ == "__main__":
    unittest.main()
