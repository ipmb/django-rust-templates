//! Splitting a template into alternating text and tag segments.
//!
//! This reproduces `tag_re` from `django/template/base.py`, which is
//! `re.compile(r"({%.*?%}|{{.*?}}|{#.*?#})")`.
//!
//! Two properties of that regex are load-bearing and easy to lose:
//!
//! 1. There is no `DOTALL`, so `.` never matches a newline. A tag may not span
//!    lines.
//! 2. If the leftmost opener has no matching closer, Python's engine advances
//!    one character and tries again. In `{{% a %}` the `{{` at index 0 never
//!    closes, so the match is `{% a %}` from index 1 and the leading `{` is text.
//!    Hand-rolling "find an opener, then find its closer" gets this wrong.
//!
//! Using the `regex` crate rather than scanning by hand is deliberate: it has
//! the same leftmost-first alternation semantics as Python, so property 2 comes
//! for free.

use std::sync::OnceLock;

use regex::Regex;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment<'a> {
    pub text: &'a str,
    /// Character indices, matching Python's `re` spans.
    pub span: (usize, usize),
}

fn tag_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\})").expect("tag_re is a valid pattern")
    })
}

/// Maps byte offsets to character indices, matching Python's indexing.
struct CharIndex {
    /// Byte offset at which each character starts.
    starts: Vec<usize>,
}

impl CharIndex {
    fn new(source: &str) -> Self {
        Self {
            starts: source.char_indices().map(|(byte, _)| byte).collect(),
        }
    }

    /// Number of characters before this byte offset.
    fn chars_before(&self, byte: usize) -> usize {
        self.starts.partition_point(|&start| start < byte)
    }
}

/// Split `source` into alternating text and tag segments.
///
/// Equivalent to Python's `tag_re.split(source)` with a capturing group: the
/// result always begins and ends with a text segment, either of which may be
/// empty, and tags occupy the odd indices.
pub fn segments(source: &str) -> Vec<Segment<'_>> {
    let index = CharIndex::new(source);
    let total_chars = index.starts.len();

    let mut out = Vec::new();
    let mut byte_cursor = 0usize;

    for m in tag_re().find_iter(source) {
        let (start, end) = (m.start(), m.end());

        out.push(Segment {
            text: &source[byte_cursor..start],
            span: (index.chars_before(byte_cursor), index.chars_before(start)),
        });
        out.push(Segment {
            text: m.as_str(),
            span: (index.chars_before(start), index.chars_before(end)),
        });

        byte_cursor = end;
    }

    out.push(Segment {
        text: &source[byte_cursor..],
        span: (index.chars_before(byte_cursor), total_chars),
    });

    // Load-bearing: the lexer's in_tag toggle assumes the vec starts and ends
    // with a text segment and alternates strictly, so tags are the odd indices.
    // Every push above is paired, so this holds by construction — the assert is
    // here so that a future edit to the loop cannot break it silently.
    assert_eq!(out.len() % 2, 1, "segments must alternate text/tag/text");

    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texts(source: &str) -> Vec<&str> {
        segments(source).into_iter().map(|s| s.text).collect()
    }

    fn spans(source: &str) -> Vec<(usize, usize)> {
        segments(source).into_iter().map(|s| s.span).collect()
    }

    #[test]
    fn plain_text_is_a_single_segment() {
        assert_eq!(texts("hello"), vec!["hello"]);
        assert_eq!(spans("hello"), vec![(0, 5)]);
    }

    #[test]
    fn alternates_text_and_tags() {
        assert_eq!(texts("a{{ b }}c"), vec!["a", "{{ b }}", "c"]);
    }

    #[test]
    fn leading_tag_yields_an_empty_leading_text_segment() {
        assert_eq!(texts("{{ b }}c"), vec!["", "{{ b }}", "c"]);
    }

    #[test]
    fn adjacent_tags_yield_an_empty_segment_between_them() {
        assert_eq!(
            texts("{{ a }}{{ b }}"),
            vec!["", "{{ a }}", "", "{{ b }}", ""]
        );
    }

    #[test]
    fn all_three_tag_kinds_are_recognised() {
        assert_eq!(
            texts("{% a %}{{ b }}{# c #}"),
            vec!["", "{% a %}", "", "{{ b }}", "", "{# c #}", ""],
        );
    }

    #[test]
    fn tags_do_not_span_newlines() {
        // No DOTALL: the `.` in `.*?` cannot match \n, so this is all text.
        assert_eq!(texts("{%\nif x %}"), vec!["{%\nif x %}"]);
        assert_eq!(texts("{{ a\n}}"), vec!["{{ a\n}}"]);
    }

    #[test]
    fn unmatched_opener_is_retried_one_character_later() {
        // The `{{` at 0 never closes, so the match is `{% a %}` from index 1.
        assert_eq!(texts("{{% a %}"), vec!["{", "{% a %}", ""]);
        assert_eq!(spans("{{% a %}"), vec![(0, 1), (1, 8), (8, 8)]);
    }

    #[test]
    fn comment_wins_over_an_opener_inside_it() {
        // A `{%` inside a comment is not a tag: the comment matches first.
        assert_eq!(texts("{# {% #}"), vec!["", "{# {% #}", ""]);
    }

    #[test]
    fn spans_are_character_indices_not_byte_offsets() {
        // "héllo" is 5 characters but 6 bytes. A byte-offset implementation
        // reports the tag starting at 6, not 5.
        let source = "héllo{{ x }}";
        assert_eq!(spans(source), vec![(0, 5), (5, 12), (12, 12)]);
    }

    #[test]
    fn segments_always_alternate_text_and_tag() {
        // The lexer's in_tag toggle depends on this shape: odd length, text at
        // even indices, tags at odd indices. Assert it directly rather than
        // leaving it as an assumption the toggle happens to rely on.
        const SOURCES: &[&str] = &[
            "",
            "a",
            "{{ a }}",
            "{{% a %}",
            "{{ a }}{{ b }}",
            "{% a %}{# b #}",
            "a{% b %}c",
            "{%\nif x %}",
            "héllo{{ x }}",
            "{#}{#}",
            "{{ {% }}",
            "{% verbatim %}{{ x }}{% endverbatim %}",
        ];
        for source in SOURCES {
            let segs = segments(source);
            assert_eq!(segs.len() % 2, 1, "odd length for {source:?}");
            for (i, seg) in segs.iter().enumerate() {
                let is_tag = seg.text.starts_with("{%")
                    || seg.text.starts_with("{{")
                    || seg.text.starts_with("{#");
                // Even indices are text, odd indices are tags. An even-index
                // segment may still *look* like a tag if it is plain text that
                // merely begins with those characters, so only assert the odd
                // direction, which is the one the toggle relies on.
                if i % 2 == 1 {
                    assert!(is_tag, "odd index {i} is not a tag in {source:?}");
                }
            }
        }
    }

    #[test]
    fn segments_reconstruct_the_source() {
        let source = "a{{ b }}c{% d %}{# e #}f";
        let rebuilt: String = segments(source).into_iter().map(|s| s.text).collect();
        assert_eq!(rebuilt, source);
    }
}
