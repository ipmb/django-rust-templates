//! Python's tables, which are not Rust's.
//!
//! Two of them live here, and both are **two-version artifacts**: each is built
//! from a Unicode release that moves independently of the other, Rust's tracking
//! the toolchain and CPython's being compiled into the interpreter. So neither
//! may be computed from one side and asserted; each is generated from the two
//! live implementations and pinned by a test that compares them exhaustively
//! (see the design spec's §4 rule). The instances are this module's whitespace
//! table and the `case` submodule's uppercase mapping.
//!
//! ## Whitespace
//!
//! Django trims tag contents with `token_string[2:-2].strip()` and splits them
//! with `smart_split`, whose pattern is written using `\s`/`\S`. All three
//! follow Python's notion of whitespace, and Python's is **not** Unicode's
//! `White_Space` property, which is what Rust's `str::trim` and the `regex`
//! crate's `\s` use.
//!
//! The two tables differ by exactly four code points. Python's
//! `str.isspace()` (and `re`'s `\s`, which is built on the same predicate) is
//! true for U+001C..U+001F — the information separators — as well as for
//! `White_Space`; Rust's is true only for `White_Space`. Checked exhaustively
//! over U+0000..=U+10FFFF in both directions: Python has 29 such code points,
//! Rust 25, and nothing is whitespace in Rust that is not whitespace in Python.
//!
//! Python's half was checked across interpreter builds rather than assumed
//! stable: `str.isspace()` yields the same 29 code points, byte-identical, on
//! CPython 3.10.16 / 3.11.9 / 3.12.9 / 3.13.7 / 3.14.4 — spanning Unicode
//! 13.0.0 through 16.0.0. The ASCII range comes from a hardcoded CPython table
//! rather than from `unicodedata`, so it does not move with a Unicode revision.
//! That is what makes `White_Space ∪ {U+001C..U+001F}` safe to write out here
//! instead of computing.
//!
//! What is *not* safe is the Rust half's stability, and the risk runs the other
//! way from the one above. Because `is_space` is `c.is_whitespace() || ...`, it
//! inherits Rust's table, which tracks the Unicode version of the toolchain; a
//! future `White_Space` addition would be picked up by Rust and Python at
//! different times, and this function would quietly begin over-trimming. No
//! `cargo test` can see that. The guard is
//! `test_whitespace_table_is_exactly_pythons` in `tests/test_lexer_parity.py`,
//! which compares `_native.whitespace_codepoints()` against `str.isspace()` over
//! the whole range — the one place both languages are present.
//!
//! This is observable rather than theoretical. `{% \u{1c} if x %}` trims to
//! `if x` in Python and to `\u{1c} if x` in Rust, so the two engines disagree
//! about the tag's contents, and therefore about which tag it is.

pub mod case;

pub use case::{upper, upper_str, UNCASED};

/// Whether Python's `str.isspace()` is true for `c`.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}

/// Python's `str.strip()` with no arguments.
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// Python's `str.split()` with no arguments — split on runs of whitespace,
/// discarding leading and trailing runs.
pub fn split(s: &str) -> Vec<&str> {
    s.split(is_space).filter(|part| !part.is_empty()).collect()
}

/// Python's `str.split(None, 1)` — split on the *first* whitespace run only.
///
/// Not `split(s).into_iter().take(2)`, and the difference is observable in the
/// one place this is used. The remainder keeps its **interior and trailing**
/// whitespace and loses only the run that separated it, because stopping after
/// one split is what stops the stripping: measured on the pinned interpreter,
/// `"filter   lower  |upper".split(None, 1)` is
/// `['filter', 'lower  |upper']`, while re-joining a full split gives
/// `'lower |upper'` — a different filter-expression string, and so a different
/// `FilterExpression`.
///
/// A tail that is *all* whitespace yields **one** element, not two with an empty
/// second: `"a  "` is `['a']`. That is the case a `splitn`-shaped port gets
/// wrong, and the reason [`crate::parser::tags::filter`] can treat a
/// one-element result as "no argument at all" — which is exactly Django's
/// unguarded `_, rest = token.contents.split(None, 1)`.
///
/// Returns `str` slices for [`split`]'s reason: nothing here allocates.
pub fn split_once(s: &str) -> Vec<&str> {
    let mut chars = s.char_indices();
    // `start` is Python's "skip the leading run"; nothing but whitespace is no
    // fields at all rather than one empty field.
    let start = loop {
        match chars.next() {
            Some((_, c)) if is_space(c) => continue,
            Some((i, _)) => break i,
            None => return Vec::new(),
        }
    };

    let mut first_end = s.len();
    for (i, c) in chars {
        if is_space(c) {
            first_end = i;
            break;
        }
    }
    if first_end == s.len() {
        return vec![&s[start..]];
    }

    // The tail begins after the whole separating run. Reaching the end of the
    // string here is `"a  "`, whose tail is empty and so is not a field.
    let mut tail = None;
    for (i, c) in s[first_end..].char_indices() {
        if !is_space(c) {
            tail = Some(first_end + i);
            break;
        }
    }
    match tail {
        Some(tail) => vec![&s[start..first_end], &s[tail..]],
        None => vec![&s[start..first_end]],
    }
}

/// The whitespace class as `regex` syntax, for patterns replacing `\s`/`\S`.
///
/// Written with `\x{..}` rather than `\u{..}` because the latter is not
/// accepted in a regex pattern.
pub const WS_CLASS: &str = r"\s\x{1C}-\x{1F}";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_four_separators_are_whitespace() {
        for c in '\u{1c}'..='\u{1f}' {
            assert!(is_space(c), "U+{:04X} should be whitespace", c as u32);
            assert!(
                !c.is_whitespace(),
                "U+{:04X} is not Rust whitespace, which is the whole point",
                c as u32
            );
        }
    }

    #[test]
    fn ordinary_whitespace_is_still_whitespace() {
        for c in [' ', '\t', '\n', '\r', '\u{b}', '\u{c}', '\u{85}', '\u{a0}'] {
            assert!(is_space(c), "U+{:04X}", c as u32);
        }
    }

    #[test]
    fn non_whitespace_is_not_whitespace() {
        for c in ['a', '0', '\u{200b}', '\u{feff}'] {
            assert!(!is_space(c), "U+{:04X}", c as u32);
        }
    }

    #[test]
    fn strip_matches_python() {
        assert_eq!(strip("  x  "), "x");
        assert_eq!(strip("\u{1c}x\u{1f}"), "x");
        assert_eq!(strip("\u{1c}\u{1d}\u{1e}\u{1f}"), "");
        // A separator inside is not stripped, in either language.
        assert_eq!(strip("a\u{1c}b"), "a\u{1c}b");
    }

    #[test]
    fn split_matches_python() {
        assert_eq!(split("a b"), vec!["a", "b"]);
        assert_eq!(split("a\u{1c}b"), vec!["a", "b"]);
        assert_eq!(split("  a  "), vec!["a"]);
        assert_eq!(split("\u{1c}"), Vec::<&str>::new());
        assert_eq!(split(""), Vec::<&str>::new());
    }

    #[test]
    fn split_once_matches_python() {
        // Every pair is `s.split(None, 1)` measured on the pinned interpreter
        // (CPython 3.x via `.venv`), not derived from `split` — the whole
        // reason this function exists is the three rows where the two differ.
        let table: &[(&str, &[&str])] = &[
            ("", &[]),
            (" ", &[]),
            ("  \t\n ", &[]),
            ("a", &["a"]),
            ("a ", &["a"]),
            // The tail is empty, so it is not a second field.
            ("a  ", &["a"]),
            (" a", &["a"]),
            ("  a  ", &["a"]),
            ("a b", &["a", "b"]),
            ("a  b", &["a", "b"]),
            // The run is consumed, the *trailing* whitespace is not.
            ("a b ", &["a", "b "]),
            ("a  b  c", &["a", "b  c"]),
            ("a\tb\tc", &["a", "b\tc"]),
            ("filter", &["filter"]),
            ("filter   lower  |upper", &["filter", "lower  |upper"]),
            // U+001C is Python whitespace and not Rust's, which is the
            // separator table this module exists for.
            ("\u{1c}", &[]),
            ("a\u{1c}b", &["a", "b"]),
            ("\u{1c}a\u{1c}b\u{1c}", &["a", "b\u{1c}"]),
        ];
        for (input, expected) in table {
            assert_eq!(split_once(input), *expected, "split_once({input:?})");
        }
    }

    #[test]
    fn the_documented_case_from_the_module_header() {
        // `{% \u{1c} if x %}` — Python trims to `if x`, Rust alone would not.
        assert_eq!(strip(" \u{1c} if x "), "if x");
        assert_ne!(strip(" \u{1c} if x "), " \u{1c} if x ".trim());
    }
}
