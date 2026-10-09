//! Python's `\w`, which is not Rust's.
//!
//! Django builds `filter_re` on `\w` (`django/template/base.py:705` `\w+` for a
//! filter name, `:714` `var_chars = r"\w\.\+-"` spliced into `[\w\.\+-]`, and
//! `:1160` `kwarg_re`), so the engine's `\w` must be Python's, not the `regex`
//! crate's.
//!
//! Python's `\w` for `str` patterns is exactly `str.isalnum() or '_'` — 142,940
//! code points, measured equal to `re.fullmatch(r"\w", c)` over the whole range
//! with zero disagreement. The `regex` crate's `\w` is
//! `[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]`, 144,667 code points:
//! 2,642 Rust-only (`Mn` 2,020, `Mc` 468, `So` 130, `Me` 13, `Pc` 9, `Cf` 2)
//! and 915 Python-only (all `No`). The candidate `\p{L}\p{N}_` is measured
//! byte-for-byte equal to Python's over U+0000..=U+10FFFF — zero in both
//! directions — while `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]` is not (1,495 too
//! many), which is why it is `\p{L}\p{N}` and not the `Alphabetic` spelling.
//!
//! **Which of those numbers are executable, and which are inherited.** The 771
//! ranges / 142,940 code points are pinned by a test and by a committed data
//! file, and the two divergences are pinned by name. The aggregates for *Rust's*
//! `\w` and for the rejected `Alphabetic` spelling are **inherited from the
//! task's measurement, not re-derived in this tree**, and deliberately: the
//! engine never compiles a bare `\w`, so a `regex-syntax` UCD move must fire the
//! candidate guard once, not twice, and pinning a second aggregate would be a
//! second alarm for the same event. What *is* pinned about the rejected spelling
//! is the thing a future edit would break — see
//! `the_alphabetic_spelling_is_rejected_so_it_cannot_be_simplified_into`, which
//! exists against a **code change** rather than against drift.
//!
//! **What these constants are *not*.** Nothing here compiles them into a working
//! `filter_re`. The Rust tests pin the *class*; `tests/test_pyregex_parity.py`
//! pins the table and the two splice *shapes*. "These constants are verified"
//! and "a pattern built from these constants is verified" are different claims,
//! and only the first is true here — `filter_re` is Task 3's, and it is where a
//! spliced pattern first has to *work*.
//!
//! The divergence is user-visible in both directions, measured on the pinned
//! checkout: `{{ ² }}` (U+00B2, `No`) renders `''` on Django but is not a Rust
//! `\w` character, and `{{ á }}` built from `a` + U+0301 (`Mn`) parses on Rust
//! but raises `Could not parse the remainder: '́' from 'á'` on Django.
//!
//! **Two spellings, because `\w` is used in two syntactic positions.** Django
//! writes a bare `\w` (`(?P<filter_name>\w+)`) and also splices `\w` *inside* a
//! class (`var_chars = r"\w\.\+-"` into `[%(var_chars)s]+`). The two mistakes
//! have opposite failure modes, and the bracketed one is the dangerous one:
//! the `regex` crate supports nested character classes, so `[[\p{L}\p{N}_]\.\+-]`
//! compiles and matches the same code points as `[\p{L}\p{N}_\.\+-]` — correct
//! by accident, and no longer Django's pattern. So each constant is named for
//! its position, and the guard asserts on the pattern *source*, because no
//! set-comparison can see the difference.
//!
//! This is the discipline of `crate::pyunicode::WS_CLASS`, which is likewise
//! class *contents* spliced as `[^{ws}'"]`, rather than a whole class.
//!
//! **The guard is the only thing that can see these tables move.** `\p{L}` and
//! `\p{N}` come from `regex-syntax`, which ships its own copy of the Unicode
//! Character Database (0.8.11 ships UCD 16.0.0, which is what CPython 3.14.4
//! reports too — so they agree *today*), versioned independently of CPython's
//! `unicodedata`. `regex = "1"` lets a routine `cargo update` move `regex-syntax`
//! and nothing in `scripts/check.sh` would notice; the differential corpus
//! cannot be the backstop, because a UCD skew is visible only on code points
//! whose assignment differs between two UCD versions — recently assigned
//! characters — which no corpus contains. The guard in this file walks the
//! *compiled* class and compares it to Python's table, which is the one thing
//! that can disagree.

/// Python's `\w` as a **standalone regular-expression atom** — a complete
/// character class, brackets included.
///
/// Use where Django writes a bare `\w`: `(?P<filter_name>\w+)`
/// (`base.py:705`) and `kwarg_re`'s `(\w+)=` (`base.py:1160`, Task 9's).
///
/// Do **not** splice this inside a `[...]`: `regex` accepts nested classes, so
/// `[[\p{L}\p{N}_]\.\+-]` compiles and matches the right set anyway, and the
/// pattern silently stops being Django's. Use [`PYTHON_WORD_IN_CLASS`] there.
pub const PYTHON_WORD_STANDALONE: &str = r"[\p{L}\p{N}_]";

/// Python's `\w` as the **contents of a character class**, for splicing inside a
/// `[...]` the caller writes.
///
/// This is the form for `var_chars = r"\w\.\+-"` (`base.py:714`); build it as
/// `format!(r"[{PYTHON_WORD_IN_CLASS}\.\+-]+")`. Used standalone it means "one
/// letter, one number, then underscores" and rejects `upper`, which is loud —
/// the safe direction to get wrong.
pub const PYTHON_WORD_IN_CLASS: &str = r"\p{L}\p{N}_";

#[cfg(test)]
mod tests {
    use super::*;
    use regex::Regex;

    /// An inclusive code-point range.
    ///
    /// A named alias rather than a bare `(u32, u32)`: `clippy::type_complexity`
    /// has already bitten this project once, and the alias is what makes the
    /// guard's signature readable.
    type WordRange = (u32, u32);

    /// Python's `\w`, as inclusive ranges, generated by
    /// `scripts/gen_python_word_ranges.py` from CPython 3.14.4 (UCD 16.0.0).
    ///
    /// A data file rather than a `const` in this module: 771 lines of `lo hi`
    /// hex, 8,033 bytes, versus 1,805,848 for the 142,940 code points.
    const PYTHON_WORD_RANGES_TEXT: &str = include_str!("pyregex_word_ranges.txt");

    /// The two Django `\w` positions, spliced with our forms, written by
    /// `tests/test_pyregex_parity.py` from the pinned tree's own
    /// `filter_raw_string`. A `cargo test` cannot read `.django`, so the
    /// right-hand side is derived on the Python side and read here.
    const SPLICES: &str = include_str!("../../../tests/pyregex_splices.txt");

    /// The table above, parsed.
    fn python_word_ranges() -> Vec<WordRange> {
        PYTHON_WORD_RANGES_TEXT
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let mut parts = line.split_whitespace();
                let lo = u32::from_str_radix(parts.next().expect("lo"), 16).expect("hex lo");
                let hi = u32::from_str_radix(parts.next().expect("hi"), 16).expect("hex hi");
                assert!(parts.next().is_none(), "trailing field on {line:?}");
                (lo, hi)
            })
            .collect()
    }

    /// The class as the `regex` crate actually compiles `pattern`, walked to
    /// canonical ascending, non-adjacent inclusive ranges.
    ///
    /// This is the point of the guard: `pattern` is built from the exported
    /// constants, so the code points come from the same `regex-syntax` tables
    /// the engine's `filter_re` will compile against.
    fn compiled_ranges(pattern: &str) -> Vec<WordRange> {
        let re = Regex::new(pattern).unwrap_or_else(|e| panic!("{pattern}: {e}"));
        let mut out: Vec<WordRange> = Vec::new();
        for cp in 0u32..=0x10FFFF {
            if (0xD800..=0xDFFF).contains(&cp) {
                continue;
            }
            let c = char::from_u32(cp).unwrap();
            let mut buf = [0u8; 4];
            if re.is_match(c.encode_utf8(&mut buf)) {
                match out.last_mut() {
                    Some(last) if last.1 + 1 == cp => last.1 = cp,
                    _ => out.push((cp, cp)),
                }
            }
        }
        out
    }

    /// The first difference between two range lists, or how they ended.
    fn first_diff(got: &[WordRange], expected: &[WordRange]) -> String {
        for (index, (g, e)) in got.iter().zip(expected).enumerate() {
            if g != e {
                return format!("index {index}: got {g:?}, expected {e:?}");
            }
        }
        format!(
            "equal over the common prefix; got {} ranges, expected {}",
            got.len(),
            expected.len()
        )
    }

    /// The number of code points two range lists cover.
    fn total_codepoints(ranges: &[WordRange]) -> u64 {
        ranges.iter().map(|(lo, hi)| u64::from(hi - lo + 1)).sum()
    }

    /// Whether every code point of `inner` is also in `outer`. Both canonical:
    /// ascending, disjoint, non-adjacent.
    ///
    /// A cursor walk rather than two sets — 1.1M `u32`s per side is a lot of
    /// allocation to answer a question the ranges already answer.
    fn ranges_subset(inner: &[WordRange], outer: &[WordRange]) -> bool {
        let mut cursor = 0usize;
        for &(lo, hi) in inner {
            while cursor < outer.len() && outer[cursor].1 < lo {
                cursor += 1;
            }
            match outer.get(cursor) {
                Some(&(olo, ohi)) if olo <= lo && hi <= ohi => {}
                _ => return false,
            }
        }
        true
    }

    #[test]
    fn the_embedded_table_is_pythons_measured_word_set() {
        let ranges = python_word_ranges();
        assert_eq!(ranges.len(), 771, "range count");
        let total: u64 = ranges.iter().map(|(lo, hi)| u64::from(hi - lo + 1)).sum();
        assert_eq!(total, 142_940, "code points");
        for pair in ranges.windows(2) {
            assert!(pair[0].1 + 1 < pair[1].0, "not canonical at {pair:?}");
        }
    }

    #[test]
    fn the_compiled_class_is_exactly_pythons_word_set() {
        // The one guard that can see `regex-syntax`'s UCD move under us. Both
        // spellings are walked, each wrapped into a complete one-code-point
        // pattern, so neither can drift from the table unnoticed.
        let expected = python_word_ranges();
        for (label, pattern) in [
            ("standalone", format!(r"\A{PYTHON_WORD_STANDALONE}\z")),
            ("in-class", format!(r"\A[{PYTHON_WORD_IN_CLASS}]\z")),
        ] {
            let got = compiled_ranges(&pattern);
            // `assert!` and not `assert_eq!`: on failure `assert_eq!` prints
            // both ~771-range vectors, which buries the one useful line.
            assert!(
                got == expected,
                "the regex crate's `{label}` class has diverged from Python's \
                 \\w: {}",
                first_diff(&got, &expected),
            );
        }
    }

    #[test]
    fn the_two_spellings_are_the_same_set_in_different_positions() {
        // **This replaces a test that probed five inputs.** The old test asserted
        // that splicing the standalone form inside a class matches the same code
        // points, to show that no behavioural probe can see the mistake. That is
        // true but it is not an empirical fact — nested classes are a documented
        // `regex-syntax` guarantee (`[[a][b]]` is union, and `&&`/`--` are part of
        // the same documented syntax) — so the probe could never have failed, for
        // any number of probes. A test that cannot fail is not a guard. It is
        // replaced by structure, which is the thing that actually differs:
        let standalone = PYTHON_WORD_STANDALONE;
        let in_class = PYTHON_WORD_IN_CLASS;

        assert!(
            standalone.starts_with('[') && standalone.ends_with(']'),
            "the standalone form must be a complete atom, brackets included: {standalone:?}"
        );
        assert!(
            !in_class.contains('[') && !in_class.contains(']'),
            "the in-class form must be bare contents, safe to splice inside a \
             caller's `[...]`: {in_class:?}"
        );
        // The set is the same; only the position differs. Asserting this and not
        // just the bracket shape catches the edit that changes one constant and
        // forgets the other — which the shape assertions alone would not.
        assert_eq!(
            format!("[{in_class}]"),
            standalone,
            "the two constants must describe the same set; only their position may differ"
        );
    }

    #[test]
    fn the_in_class_form_used_standalone_is_loudly_wrong() {
        // The other misuse, made executable. `\p{L}\p{N}_` read as a whole atom is
        // *concatenation*, not a union: `+` binds to `_` alone, so it means
        // exactly one letter, exactly one number, then one or more underscores.
        // That rejects `upper` and every real variable name, which is the safe
        // direction to get wrong — but it was only ever asserted in a doc comment
        // and in a reviewer's message, which is why it is a test now.
        let misused = Regex::new(&format!(r"\A{PYTHON_WORD_IN_CLASS}+\z")).unwrap();
        assert!(
            misused.is_match("a1_"),
            "one letter, one number, then underscores"
        );
        assert!(misused.is_match("a1___"));
        assert!(
            !misused.is_match("upper"),
            "the loud failure: every variable name"
        );
        assert!(!misused.is_match("a_1"));
        assert!(!misused.is_match("ab1_"));

        // The correct spelling of the same position, for contrast.
        let correct = Regex::new(&format!(r"\A[{PYTHON_WORD_IN_CLASS}]+\z")).unwrap();
        for probe in ["upper", "a_1", "ab1_", "a1_"] {
            assert!(correct.is_match(probe), "{probe:?}");
        }
    }

    #[test]
    fn the_alphabetic_spelling_is_rejected_so_it_cannot_be_simplified_into() {
        // A guard against a **code change**, not against UCD drift. Under drift
        // this fails no more usefully than the aggregate counts would; its value
        // is that it is the test that fires when someone later "simplifies"
        // `[\p{L}\p{N}_]` to `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]` because it
        // reads more semantically. That edit is plausible, silent otherwise, and
        // over-accepts by well over a thousand code points.
        let expected = python_word_ranges();
        let wrong = compiled_ranges(r"\A[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]\z");
        assert!(
            wrong != expected,
            "the `Alphabetic` spelling must not be equal to Python's \\w — if it \
             is, this guard has stopped guarding and the constant's doc needs \
             re-measuring"
        );
        // *How* it is wrong matters: it is a strict superset, so the failure mode
        // is over-acceptance (a template Django rejects compiles here) rather
        // than a different set.
        assert!(
            ranges_subset(&expected, &wrong),
            "the `Alphabetic` spelling should accept everything Python's \\w does"
        );
        // And the helper is not vacuously true — the relation is strict, so it
        // must not hold in the other direction. Without this, a `ranges_subset`
        // that returned `true` unconditionally would make the assertion above
        // decorative.
        assert!(
            !ranges_subset(&wrong, &expected),
            "`ranges_subset` returned true both ways, so it cannot tell a \
             superset from an equal set"
        );
        let extra = total_codepoints(&wrong) - total_codepoints(&expected);
        assert!(
            extra > 0,
            "a strict superset, so it has code points of its own"
        );
    }

    #[test]
    fn superscript_two_is_a_word_character() {
        // Django: `Template("{{ ² }}").render(Context({}))` == "". U+00B2 is
        // `No`, Python's `\w` matches it, Rust's does not.
        let ours = Regex::new(&format!(r"\A{PYTHON_WORD_STANDALONE}\z")).unwrap();
        assert!(ours.is_match("\u{00b2}"));
        assert!(
            !Regex::new(r"\A\w\z").unwrap().is_match("\u{00b2}"),
            "Rust's `\\w` does not match, which is why this is the failing-first case"
        );
    }

    #[test]
    fn combining_acute_is_not_a_word_character() {
        // Django: `Engine().from_string("{{ a\u{0301} }}")` raises
        // "Could not parse the remainder: '\u{0301}' from 'a\u{0301}'".
        // U+0301 is `Mn`, a Rust `\w` character but not a Python one.
        let ours = Regex::new(&format!(r"\A{PYTHON_WORD_STANDALONE}\z")).unwrap();
        assert!(!ours.is_match("\u{0301}"));
        assert!(
            Regex::new(r"\A\w\z").unwrap().is_match("\u{0301}"),
            "Rust's `\\w` matches, which is why this is the failing-first case"
        );
    }

    #[test]
    fn the_class_leaves_djangos_remainder() {
        // The same two characters, read the way `filter_re` reads a variable:
        // a leading run of the class. This is the observable consequence —
        // what is left over — which is what Django turns into the error.
        let re = Regex::new(&format!(r"\A[{PYTHON_WORD_IN_CLASS}]+")).unwrap();
        let m = re.find("\u{00b2}").expect("U+00B2 must match");
        assert_eq!(m.end(), "\u{00b2}".len());
        // `m.end()` is a byte offset, so slice on bytes; U+0301 is two bytes.
        let source = "a\u{0301}";
        let m = re.find(source).unwrap();
        assert_eq!(&source[m.end()..], "\u{0301}");
    }

    #[test]
    fn both_forms_splice_reproducing_djangos_pattern_sources() {
        let lines: Vec<&str> = SPLICES
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(str::trim)
            .collect();
        assert_eq!(
            lines.len(),
            2,
            "fixture must hold exactly two atoms:\n{SPLICES}"
        );
        let (label, atom) = lines[0].split_once(' ').expect("labelled fixture line");
        assert_eq!(label, "filter_name");
        assert_eq!(atom, format!("(?P<filter_name>{PYTHON_WORD_STANDALONE}+)"));
        let (label, atom) = lines[1].split_once(' ').expect("labelled fixture line");
        assert_eq!(label, "var_chars");
        assert_eq!(atom, format!(r"[{PYTHON_WORD_IN_CLASS}\.\+-]+"));
    }
}
