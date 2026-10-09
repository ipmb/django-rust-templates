//! Python's **uppercase** mapping, which is not Rust's. **Generated** by
//! `scripts/gen_python_case_fixups.py` — do not edit by hand.
//!
//! `char::to_uppercase` and `str.upper()` both apply the Unicode default full
//! case mappings, but from *different Unicode versions*: Rust's stdlib tables
//! track the toolchain's release, CPython's are compiled into the interpreter.
//! Measured 2026-10-08 with `rustc 1.98.0` and the pinned `.venv/bin/python`
//! (CPython 3.14.4, `unicodedata` 16.0.0): **28 code points diverge
//! in the uppercase direction and 28 in the lowercase**.
//!
//! All 28 go the same way — Python maps the code point to itself where
//! Rust maps it elsewhere — so this is a **pure suppression list**. The only
//! thing that can be wrong is an entry that need not be here; a *missing* entry
//! is a character that renders differently from Django's.
//!
//! The cause is Unicode 17.0 (September 2025), which Rust 1.98 ships and
//! `unicodedata` 16.0.0 does not. The entries split into two kinds, and the
//! split is **measured with `unicodedata.name()`**, not with a count — a count of
//! diverging code points cannot tell an assignment from a mapping, and the two
//! need different words:
//!
//! * **26 are new assignments.** 16.0.0 has
//!   no name for the code point at all — `unicodedata.name()` raises
//!   `ValueError`, measured:
//!   * 25 are the lowercase letters of **Beria Erfe** (Zaghawa),
//!     U+16EBB..U+16ED3, whose capitals U+16EA0..U+16EB8 complete the block's
//!     case pairs;
//!   * 1 is U+A7CF, whose capital U+A7CE 17.0 also introduced.
//! * **2 are retro-fitted mappings, and they are the interesting
//!   ones.** U+A7D3 (LATIN SMALL LETTER DOUBLE THORN) and U+A7D5 (…DOUBLE
//!   WYNN) are *already named in 16.0.0*, and their capitals U+A7D2 and U+A7D4
//!   are `unassigned` there; 17.0 assigned the capitals and retro-fitted the
//!   mappings. Python knows the character, knows it is a lowercase letter, and
//!   nevertheless maps it to **itself** — so this is not "a character 16.0.0
//!   lacks" but two releases disagreeing about a character they both have, and
//!   only a mapping table can see the difference.
//!
//! There is deliberately **no `lower` here**. The generator measures the lower
//! divergence too — 28 code points, the same shape, the same blocks —
//! and that symmetry is a trap rather than a licence: `str.lower()` also applies
//! Unicode's Final_Sigma condition, so `"ΟΣ".lower()` is `"ος"` and not `"οσ"`,
//! and a per-code-point table cannot express a rule about a character's
//! *context*. The `lower` filter and `slugify` go through `FilterHost::str_lower`
//! instead (§D2's second metric, Task 4). The upper direction has no such rule,
//! which is why it is the one that can live here.
//!
//! The guard is `tests/test_pyunicode_case_parity.py`, which compares
//! `_native.python_upper` against `chr(cp).upper()` for every code point and
//! names the one that disagrees. It is the **only** pin on the Rust half of this
//! pair: the workspace declares `rust-version = "1.82"`, a floor, and has no
//! `rust-toolchain` file, so a `rustc` upgrade that moves Rust's tables fails
//! that test rather than silently changing what a template renders.

/// Code points whose **uppercase** mapping Python 3.14.4 (Unicode 16.0.0) leaves
/// as the identity where Rust's tables map them elsewhere.
///
/// Sorted ascending, so `binary_search` is the lookup and a stray reorder is
/// visible. The Rust half of each entry is asserted in this module's tests; the
/// Python half is asserted by the parity test, which is the only place both
/// languages are present.
pub const UNCASED: [char; 28] = [
    '\u{a7cf}',
    '\u{a7d3}',
    '\u{a7d5}',
    '\u{16ebb}',
    '\u{16ebc}',
    '\u{16ebd}',
    '\u{16ebe}',
    '\u{16ebf}',
    '\u{16ec0}',
    '\u{16ec1}',
    '\u{16ec2}',
    '\u{16ec3}',
    '\u{16ec4}',
    '\u{16ec5}',
    '\u{16ec6}',
    '\u{16ec7}',
    '\u{16ec8}',
    '\u{16ec9}',
    '\u{16eca}',
    '\u{16ecb}',
    '\u{16ecc}',
    '\u{16ecd}',
    '\u{16ece}',
    '\u{16ecf}',
    '\u{16ed0}',
    '\u{16ed1}',
    '\u{16ed2}',
    '\u{16ed3}',
];

/// One code point's Python uppercase mapping, as an iterator of code points.
///
/// An iterator rather than a `char` because the mapping is not
/// character-for-character: `'ß'` maps to `'S' 'S'`. Python's answers are
/// length-varying in exactly the same places (and one of them is in this table's
/// own direction), so collapsing to a single `char` would be wrong for the same
/// strings the table exists to fix.
pub fn upper(ch: char) -> impl Iterator<Item = char> {
    if UNCASED.binary_search(&ch).is_ok() {
        // Python leaves this one alone and Rust would map it. Yield the code
        // point itself, and only it — the two arms have different lengths and
        // that asymmetry is the point.
        CaseMap::Identity(Some(ch))
    } else {
        CaseMap::Rust(ch.to_uppercase())
    }
}

/// Python's `str.upper()` for a whole string, allocating at most one `String`.
pub fn upper_str(s: &str) -> String {
    // Sized from the input, which is exact for everything but the handful of
    // code points that grow (`'ß'` → `"SS"`), so the common case is a single
    // allocation rather than a per-character `format!`.
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        out.extend(upper(ch));
    }
    out
}

enum CaseMap {
    Identity(Option<char>),
    Rust(std::char::ToUppercase),
}

impl Iterator for CaseMap {
    type Item = char;

    fn next(&mut self) -> Option<char> {
        match self {
            CaseMap::Identity(slot) => slot.take(),
            CaseMap::Rust(iter) => iter.next(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every entry is a code point Rust *would* map, so no entry is inert.
    ///
    /// This is the guard the parity test cannot be. `UNCASED` is a suppression
    /// list, so an entry naming a code point Rust already maps to itself is
    /// harmless — Rust and Python agree about it with or without the entry — and
    /// the parity test passes either way. Deleting a real entry fails loudly;
    /// *adding* a bogus one is silent. This is what makes it not silent, and it
    /// is a property of the Rust table alone, so it holds here rather than on the
    /// Python side.
    #[test]
    fn every_entry_is_one_rust_would_map() {
        for ch in UNCASED {
            let mapped = ch.to_uppercase().next();
            assert_ne!(
                mapped,
                Some(ch),
                "U+{:04X} is inert: Rust maps it to itself, so the entry hides \
                 a real divergence",
                ch as u32
            );
        }
    }

    /// Sorted, because the lookup is `binary_search` and a reorder would make it
    /// silently wrong for every code point after the first inversion.
    #[test]
    fn the_table_is_sorted() {
        assert!(
            UNCASED.windows(2).all(|w| w[0] < w[1]),
            "UNCASED is not ascending; binary_search requires it"
        );
    }

    #[test]
    fn ordinary_ascii_is_rusts() {
        assert_eq!(upper_str("abc"), "ABC");
        assert_eq!(upper_str("aBc1-_"), "ABC1-_");
        assert_eq!(upper_str(""), "");
    }

    /// The mapping is not character-for-character, and the table's arm must not
    /// flatten that: `'ß'` is one code point in and two out.
    #[test]
    fn a_growing_mapping_stays_growing() {
        assert_eq!(upper_str("straße"), "STRASSE");
        assert_eq!(upper('ß').collect::<String>(), "SS");
    }

    /// The table's whole purpose, spelled concretely: Python leaves these alone
    /// and Rust moves them, so the table's answer is the code point itself.
    #[test]
    fn the_entries_python_leaves_alone_are_left_alone() {
        // Latin Extended-D: Python *names* this character (Unicode 16.0.0) and
        // maps it to itself; Rust 1.98 maps it to the capital added in 17.0.
        assert_eq!('\u{a7d3}'.to_uppercase().collect::<String>(), "\u{a7d2}");
        assert_eq!(upper_str("\u{a7d3}"), "\u{a7d3}");

        // Beria Erfe: a block 16.0.0 does not know at all.
        assert_eq!('\u{16ebb}'.to_uppercase().collect::<String>(), "\u{16ea0}");
        assert_eq!(upper_str("\u{16ebb}"), "\u{16ebb}");
    }

    /// The upper direction is a function of the code point alone, which is why
    /// it can be a table at all — unlike `lower`, where `"ΟΣ".lower()` is
    /// `"ος"` and a `char`-at-a-time answer is wrong.
    #[test]
    fn upper_has_no_context_rule() {
        for s in ["ΣΣ", "ΟΣ", "Σ1", "ςΣ"] {
            assert_eq!(upper_str(s), s.chars().flat_map(upper).collect::<String>());
        }
        assert_eq!(upper_str("ΟΣ"), "ΟΣ");
        assert_eq!(upper_str("ς"), "Σ");
    }
}
