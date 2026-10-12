#!/usr/bin/env python3
"""Generate `crates/template-core/src/pyunicode/case.rs`: Python's case mapping.

`char::to_uppercase` and `str.upper()` both apply the Unicode default full case
mappings, but they are built from **different Unicode versions**: Rust's stdlib
tables track the toolchain's release, CPython's are compiled into the
interpreter. Measured 2026-10-08 with `rustc 1.98.0` and the pinned
`.venv/bin/python` (CPython 3.14.4, `unicodedata` 16.0.0): 28 code points
diverge in each direction, and in every case Python maps the code point to
itself where Rust maps it elsewhere. So the table that ships is a pure
suppression list.

Only the **upper** direction becomes a table. The lower direction is measured
anyway, and the count is written into the emitted module's doc as the reason
there is no `lower`: `str.lower()` also applies Final_Sigma, so a per-code-point
`lower` would be wrong for the strings that reach it. See Task 4 of the M4 plan
and `FilterHost::str_lower`.

**Run this under `.venv/bin/python`.** The divergence count is a property of two
version pairs, not one: with `rustc 1.98.0` held fixed, the system
`/usr/bin/python3` (Unicode 13.0.0) disagrees on 95 code points per direction
where the pinned 3.14.4 disagrees on 28.

Rust's mapping is obtained by **compiling and running a probe**, never by
reimplementing it in Python — a reimplementation would make the generator agree
with its own wrong answer. This needs `rustc` on `PATH` (`source scripts/env.sh`).

Usage:
    .venv/bin/python -I scripts/gen_python_case_fixups.py           # write the file
    .venv/bin/python -I scripts/gen_python_case_fixups.py --check   # verify it is current

`--check` exits 1 when the committed file and the live pair disagree. Run it
after a `rustc` upgrade or a Django pin bump; `tests/test_pyunicode_case_parity.py`
is the guard that runs on every `cargo test`.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "crates" / "template-core" / "src" / "pyunicode" / "case.rs"

# Pinned so that an accidental environment change refuses rather than writing a
# plausible-looking wrong table: running this under the system python3 emits 67
# extra entries, and a generator that only overwrites would happily commit them.
# A *deliberate* bump (new `rustc`, new CPython) is expected to move these, and
# the failure message says so.
EXPECTED_UPPER = 28
EXPECTED_LOWER = 28

# The uppercase table's three groups, counted rather than described. The module
# doc states them as facts, so this is what keeps the facts true: the split was
# first written as 25 + 3 and the `unicodedata.name()` check below moved U+A7CF
# into the "new assignment" half, which is exactly the correction a doc claim
# nothing measures does not get.
EXPECTED_BERIA_ERFE = 25
EXPECTED_UNNAMED_OTHER = 1  # U+A7CF, assigned in 17.0 alongside its capital U+A7CE
EXPECTED_NAMED = 2  # U+A7D3, U+A7D5: named in 16.0.0, mapping retro-fitted by 17.0

# The Beria Erfe (Zaghawa) block as Unicode 17.0 laid it out: capitals
# U+16EA0..U+16EB8, lowercase U+16EBB..U+16ED3. Used only to *identify* the group
# in the emitted doc, never to compute a mapping.
BERIA_ERFE_BLOCK = (0x16EA0, 0x16EDF)

PROBE_SRC = r"""
use std::io::Write;

fn main() {
    let out = std::io::stdout();
    let mut w = std::io::BufWriter::new(out.lock());
    for cp in 0u32..=0x10FFFF {
        if (0xD800..=0xDFFF).contains(&cp) {
            continue;
        }
        let c = match char::from_u32(cp) {
            Some(c) => c,
            None => continue,
        };
        let up: Vec<u32> = c.to_uppercase().map(|x| x as u32).collect();
        let lo: Vec<u32> = c.to_lowercase().map(|x| x as u32).collect();
        if up == [cp] && lo == [cp] {
            continue;
        }
        write!(w, "{:X}", cp).unwrap();
        for x in &up {
            write!(w, " {:X}", x).unwrap();
        }
        w.write_all(b" |").unwrap();
        for x in &lo {
            write!(w, " {:X}", x).unwrap();
        }
        w.write_all(b"\n").unwrap();
    }
}
"""


def rust_case_map():
    """Rust's case mappings, read from a compiled probe. Identity is omitted."""
    rustc = shutil.which("rustc")
    if rustc is None:
        raise SystemExit(
            "rustc not on PATH. `source scripts/env.sh` first — the generated "
            "table is a property of the toolchain's Unicode version, so it has "
            "to come from the toolchain."
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "probe.rs"
        exe = tmp / "probe"
        src.write_text(PROBE_SRC)
        subprocess.run([rustc, "-O", "-o", str(exe), str(src)], check=True)
        raw = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout

    upper, lower = {}, {}
    for line in raw.splitlines():
        hex_cp, _, rest = line.partition(" ")
        up_text, sep, lo_text = rest.partition("|")
        if not sep:
            raise SystemExit(f"probe emitted an unparsable line: {line!r}")
        cp = int(hex_cp, 16)
        # An empty list means the probe omitted nothing here because that
        # direction is the identity — it only emits a code point when at least
        # one direction moves. Restore the identity rather than storing `[]`,
        # which `divergences` would read as "maps to nothing" and report as a
        # divergence in the *other* direction.
        up = [int(x, 16) for x in up_text.split()] or [cp]
        lo = [int(x, 16) for x in lo_text.split()] or [cp]
        upper[cp] = up
        lower[cp] = lo
    return upper, lower


def divergences(rust, py_of):
    """Code points where Rust's mapping and Python's disagree.

    Walks **every** code point a `str` can hold, not the probe's keys: Rust's
    identity for a code point Python moves would be invisible in the probe's
    output, and that direction is exactly what a suppression list cannot
    express, so it has to be looked for.
    """
    found = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        py = [ord(x) for x in py_of(ch)]
        rs = rust.get(cp, [cp])
        if py != rs:
            found.append((cp, rs, py))
    return found


def classify(name, found):
    """Refuse anything the emitted table could not express.

    `UNCASED` can only say "Python leaves this alone and Rust does not". A code
    point diverging the other way, or to a different character, needs a mapping
    table rather than a suppression list, and silently dropping it would ship a
    filter that renders a character Django does not.
    """
    odd = [(cp, rs, py) for cp, rs, py in found if py != [cp]]
    if odd:
        detail = "\n".join(
            f"  U+{cp:04X} rust={[f'U+{x:04X}' for x in rs]} py={[f'U+{x:04X}' for x in py]}"
            for cp, rs, py in odd[:10]
        )
        raise SystemExit(
            f"{name}: {len(odd)} divergence(s) are not 'Python identity, Rust "
            f"elsewhere', which is the only shape UNCASED can hold:\n{detail}\n"
            f"`pyunicode::case` needs a mapping table, not a suppression list."
        )
    return [cp for cp, _, _ in found]


def split_by_assignment(cps):
    """The table's three groups, **measured**, so the emitted doc cannot lie.

    `unicodedata.name()` is the whole test and it is the only available one: a
    code point the pinned release has assigned has a name, and one it has not
    raises `ValueError`. Two diverging code points look identical to a count and
    are different facts — "16.0.0 has never heard of this character" versus
    "16.0.0 names this character, calls it lowercase, and maps it to itself".
    The second is the one worth writing a paragraph about, and the one a count
    silently absorbs, which is how the emitted doc said "25 Beria Erfe, plus
    U+A7CF, U+A7D3, U+A7D5" for a release.

    Returns `(beria_erfe, unnamed_other, named)`.
    """
    beria_erfe, unnamed_other, named = [], [], []
    for cp in cps:
        try:
            unicodedata.name(chr(cp))
        except ValueError:
            if BERIA_ERFE_BLOCK[0] <= cp <= BERIA_ERFE_BLOCK[1]:
                beria_erfe.append(cp)
            else:
                unnamed_other.append(cp)
        else:
            named.append(cp)
    return beria_erfe, unnamed_other, named


def emit(upper_cps, lower_count, upper_count, groups):
    # One entry per line. rustfmt's own choice for a table this long, and it is
    # normalized by `rustfmt_format` either way — so this is written for the
    # reader, not to satisfy the formatter.
    const = "\n".join(f"    '\\u{{{cp:x}}}'," for cp in upper_cps)
    # The three groups, counted by `split_by_assignment` rather than restated:
    # every number below is a measurement, so a release that moves one moves the
    # emitted doc with it.
    beria_erfe, unnamed_other, named = groups

    return f'''//! Python's **uppercase** mapping, which is not Rust's. **Generated** by
//! `scripts/gen_python_case_fixups.py` — do not edit by hand.
//!
//! `char::to_uppercase` and `str.upper()` both apply the Unicode default full
//! case mappings, but from *different Unicode versions*: Rust's stdlib tables
//! track the toolchain's release, CPython's are compiled into the interpreter.
//! Measured 2026-10-08 with `rustc 1.98.0` and the pinned `.venv/bin/python`
//! (CPython 3.14.4, `unicodedata` 16.0.0): **{upper_count} code points diverge
//! in the uppercase direction and {lower_count} in the lowercase**.
//!
//! All {upper_count} go the same way — Python maps the code point to itself where
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
//! * **{len(beria_erfe) + len(unnamed_other)} are new assignments.** 16.0.0 has
//!   no name for the code point at all — `unicodedata.name()` raises
//!   `ValueError`, measured:
//!   * {len(beria_erfe)} are the lowercase letters of **Beria Erfe** (Zaghawa),
//!     U+16EBB..U+16ED3, whose capitals U+16EA0..U+16EB8 complete the block's
//!     case pairs;
//!   * {len(unnamed_other)} is U+A7CF, whose capital U+A7CE 17.0 also introduced.
//! * **{len(named)} are retro-fitted mappings, and they are the interesting
//!   ones.** U+A7D3 (LATIN SMALL LETTER DOUBLE THORN) and U+A7D5 (…DOUBLE
//!   WYNN) are *already named in 16.0.0*, and their capitals U+A7D2 and U+A7D4
//!   are `unassigned` there; 17.0 assigned the capitals and retro-fitted the
//!   mappings. Python knows the character, knows it is a lowercase letter, and
//!   nevertheless maps it to **itself** — so this is not "a character 16.0.0
//!   lacks" but two releases disagreeing about a character they both have, and
//!   only a mapping table can see the difference.
//!
//! There is deliberately **no `lower` here**. The generator measures the lower
//! divergence too — {lower_count} code points, the same shape, the same blocks —
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
pub const UNCASED: [char; {upper_count}] = [
{const}
];

/// One code point's Python uppercase mapping, as an iterator of code points.
///
/// An iterator rather than a `char` because the mapping is not
/// character-for-character: `'ß'` maps to `'S' 'S'`. Python's answers are
/// length-varying in exactly the same places (and one of them is in this table's
/// own direction), so collapsing to a single `char` would be wrong for the same
/// strings the table exists to fix.
pub fn upper(ch: char) -> impl Iterator<Item = char> {{
    if UNCASED.binary_search(&ch).is_ok() || super::profile::suppress_upper(ch) {{
        // Python leaves this one alone and Rust would map it. Yield the code
        // point itself, and only it — the two arms have different lengths and
        // that asymmetry is the point.
        CaseMap::Identity(Some(ch))
    }} else {{
        CaseMap::Rust(ch.to_uppercase())
    }}
}}

/// Python's `str.upper()` for a whole string, allocating at most one `String`.
pub fn upper_str(s: &str) -> String {{
    // Sized from the input, which is exact for everything but the handful of
    // code points that grow (`'ß'` → `"SS"`), so the common case is a single
    // allocation rather than a per-character `format!`.
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {{
        out.extend(upper(ch));
    }}
    out
}}

enum CaseMap {{
    Identity(Option<char>),
    Rust(std::char::ToUppercase),
}}

impl Iterator for CaseMap {{
    type Item = char;

    fn next(&mut self) -> Option<char> {{
        match self {{
            CaseMap::Identity(slot) => slot.take(),
            CaseMap::Rust(iter) => iter.next(),
        }}
    }}
}}

#[cfg(test)]
mod tests {{
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
    fn every_entry_is_one_rust_would_map() {{
        for ch in UNCASED {{
            let mapped = ch.to_uppercase().next();
            assert_ne!(
                mapped,
                Some(ch),
                "U+{{:04X}} is inert: Rust maps it to itself, so the entry hides \\
                 a real divergence",
                ch as u32
            );
        }}
    }}

    /// Sorted, because the lookup is `binary_search` and a reorder would make it
    /// silently wrong for every code point after the first inversion.
    #[test]
    fn the_table_is_sorted() {{
        assert!(
            UNCASED.windows(2).all(|w| w[0] < w[1]),
            "UNCASED is not ascending; binary_search requires it"
        );
    }}

    #[test]
    fn ordinary_ascii_is_rusts() {{
        assert_eq!(upper_str("abc"), "ABC");
        assert_eq!(upper_str("aBc1-_"), "ABC1-_");
        assert_eq!(upper_str(""), "");
    }}

    /// The mapping is not character-for-character, and the table's arm must not
    /// flatten that: `'ß'` is one code point in and two out.
    #[test]
    fn a_growing_mapping_stays_growing() {{
        assert_eq!(upper_str("straße"), "STRASSE");
        assert_eq!(upper('ß').collect::<String>(), "SS");
    }}

    /// The table's whole purpose, spelled concretely: Python leaves these alone
    /// and Rust moves them, so the table's answer is the code point itself.
    #[test]
    fn the_entries_python_leaves_alone_are_left_alone() {{
        // Latin Extended-D: Python *names* this character (Unicode 16.0.0) and
        // maps it to itself; Rust 1.98 maps it to the capital added in 17.0.
        assert_eq!('\\u{{a7d3}}'.to_uppercase().collect::<String>(), "\\u{{a7d2}}");
        assert_eq!(upper_str("\\u{{a7d3}}"), "\\u{{a7d3}}");

        // Beria Erfe: a block 16.0.0 does not know at all.
        assert_eq!('\\u{{16ebb}}'.to_uppercase().collect::<String>(), "\\u{{16ea0}}");
        assert_eq!(upper_str("\\u{{16ebb}}"), "\\u{{16ebb}}");
    }}

    /// The upper direction is a function of the code point alone, which is why
    /// it can be a table at all — unlike `lower`, where `"ΟΣ".lower()` is
    /// `"ος"` and a `char`-at-a-time answer is wrong.
    #[test]
    fn upper_has_no_context_rule() {{
        for s in ["ΣΣ", "ΟΣ", "Σ1", "ςΣ"] {{
            assert_eq!(upper_str(s), s.chars().flat_map(upper).collect::<String>());
        }}
        assert_eq!(upper_str("ΟΣ"), "ΟΣ");
        assert_eq!(upper_str("ς"), "Σ");
    }}
}}
'''


def rustfmt_format(text):
    """Run the emitted source through rustfmt and return the result.

    This is applied rather than checked, because rustfmt's layout for the table
    depends on how many entries there are: 28 char literals go one per line, but
    a short array collapses onto one line, so a template that hard-codes either
    is only right for the length it was written at. Formatting the output means
    the generator cannot emit a table that `scripts/check.sh`'s
    `cargo fmt --all -- --check` would reject — worth having in the generator
    rather than in the gate, because `cargo fmt` silently *skips* a `.rs` file
    that is not reachable from the crate root, so an unwired generated file's
    formatting is never checked at all.

    Rustfmt runs on a temp file with the edition written out, since there is no
    `Cargo.toml` beside it to read it from.
    """
    rustfmt = shutil.which("rustfmt")
    if rustfmt is None:
        raise SystemExit("rustfmt not on PATH; `source scripts/env.sh` first.")
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "emitted.rs"
        probe.write_text(text)
        result = subprocess.run(
            [rustfmt, "--edition", "2021", str(probe)], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise SystemExit(
                "rustfmt refused the emitted template, which means it is not "
                "valid Rust. Fix `emit()`; rustfmt says:\n" + result.stderr
            )
        return probe.read_text()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed file is current instead of writing it",
    )
    args = parser.parse_args()

    rust_upper, rust_lower = rust_case_map()
    upper_cps = classify("upper", divergences(rust_upper, str.upper))
    lower_cps = classify("lower", divergences(rust_lower, str.lower))

    if len(upper_cps) != EXPECTED_UPPER or len(lower_cps) != EXPECTED_LOWER:
        raise SystemExit(
            f"divergence counts moved: upper {len(upper_cps)} "
            f"(pinned {EXPECTED_UPPER}), lower {len(lower_cps)} "
            f"(pinned {EXPECTED_LOWER}).\n"
            f"If a `rustc` or CPython upgrade is the cause, that is expected: "
            f"update EXPECTED_UPPER/EXPECTED_LOWER here and the count in "
            f"tests/test_pyunicode_case_parity.py's docstring, then commit the "
            f"regenerated table.\n"
            f"If it is not, suspect the interpreter: this must run under "
            f".venv/bin/python (CPython 3.14.4, Unicode 16.0.0), and the system "
            f"python3 disagrees on 95 code points per direction."
        )

    # The table's three groups, measured. `split_by_assignment` is the only thing
    # that can tell an assignment from a retro-fitted mapping, and the emitted
    # doc's headline is that distinction, so this refusal is what keeps the
    # headline true. An earlier revision of that doc said "25 Beria Erfe, plus
    # U+A7CF, U+A7D3, U+A7D5" — U+A7CF belongs with the assignments, and the
    # number was wrong in the direction that overstates the interesting case.
    groups = split_by_assignment(upper_cps)
    expected_groups = (
        EXPECTED_BERIA_ERFE,
        EXPECTED_UNNAMED_OTHER,
        EXPECTED_NAMED,
    )
    if tuple(len(g) for g in groups) != expected_groups:
        beria, other, named = (sorted(g) for g in groups)
        raise SystemExit(
            f"the assignment/mapping split moved: Beria Erfe {len(beria)} "
            f"(pinned {EXPECTED_BERIA_ERFE}), unnamed elsewhere {len(other)} "
            f"(pinned {EXPECTED_UNNAMED_OTHER}), named-but-retrofitted "
            f"{len(named)} (pinned {EXPECTED_NAMED}).\n"
            f"  Beria Erfe: {[f'U+{cp:04X}' for cp in beria]}\n"
            f"  unnamed elsewhere: {[f'U+{cp:04X}' for cp in other]}\n"
            f"  named but mapped to itself: {[f'U+{cp:04X}' for cp in named]}\n"
            f"A Unicode release that assigns or names one of these is the "
            f"expected cause: update the EXPECTED_* group counts here, the "
            f"matching lists in tests/test_pyunicode_case_parity.py, and commit "
            f"the regenerated table."
        )

    text = rustfmt_format(emit(upper_cps, len(lower_cps), len(upper_cps), groups))

    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            raise SystemExit(
                f"{OUT.relative_to(ROOT)} is not what the current pair produces. "
                f"Run this without --check and commit the result."
            )
        print(f"{OUT.relative_to(ROOT)} is current ({len(upper_cps)} upper entries)")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(
        f"wrote {OUT.relative_to(ROOT)}: {len(upper_cps)} upper entries "
        f"(lower: {len(lower_cps)} measured, deliberately not emitted)"
    )


if __name__ == "__main__":
    sys.exit(main())
