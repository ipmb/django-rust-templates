//! Python's whitespace table, which is not Rust's.
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
//! This is observable rather than theoretical. `{% \u{1c} if x %}` trims to
//! `if x` in Python and to `\u{1c} if x` in Rust, so the two engines disagree
//! about the tag's contents, and therefore about which tag it is.

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
    fn the_documented_case_from_the_module_header() {
        // `{% \u{1c} if x %}` — Python trims to `if x`, Rust alone would not.
        assert_eq!(strip(" \u{1c} if x "), "if x");
        assert_ne!(strip(" \u{1c} if x "), " \u{1c} if x ".trim());
    }
}
