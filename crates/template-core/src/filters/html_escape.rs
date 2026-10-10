//! The five-replacement table `django.utils.html.escape` is a `SafeString`
//! wrapper around — `defaultfilters.py`'s `force_escape`, `linenumbers`,
//! `linebreaks` and `linebreaksbr` are its four callers in M4.
//!
//! ```python
//! def escape(text):                      # django/utils/html.py:107-116
//!     return SafeString(html.escape(str(text)))
//! ```
//!
//! and `html.escape` itself is five `str.replace`s in one order
//! (`html/__init__.py`, CPython — measured on the pinned interpreter):
//!
//! ```python
//! s = s.replace("&", "&amp;")            # Must be done first!
//! s = s.replace("<", "&lt;")
//! s = s.replace(">", "&gt;")
//! s = s.replace('"', "&quot;")
//! s = s.replace("'", "&#x27;")
//! ```
//!
//! **Why this is a module of its own rather than a helper inside `escape.rs`.**
//! `escape.rs` is the module of the `escape` *filter*, whose body is
//! `FilterHost::conditional_escape` and never this table (§D4) — and the table has
//! four callers, spread across two of Task 8's waves: `force_escape` in the first,
//! and `linenumbers`, `linebreaks` and `linebreaksbr` in the second. A filter
//! module belongs to whichever agent is landing that filter, so a primitive two
//! waves share lives where no agent owns it — the shape `padding.rs` beside it
//! already has, and the reason both sit outside the `pub mod` filter list.
//!
//! **The order is the semantics, and a single pass is the same thing.** CPython
//! escapes `&` first — its source says "Must be done first!" — so `&lt;` in the
//! input comes out `&amp;lt;`. One pass over the code points gives exactly that
//! answer and not merely an equivalent-looking one: no replacement introduces a
//! character a *later* one rewrites, because every replacement that introduces an
//! `&` does so after the `&` pass. Measured on the pinned tree, `escape('a&b<c>d"e\'f')`
//! is `'a&amp;b&lt;c&gt;d&quot;e&#x27;f'` — note the apostrophe is `&#x27;` and
//! not `&#39;` — and `escape(5)` is `'5'`, the `str(text)` in the wrapper.
//!
//! **A `str` goes in and a `str` comes out**, with the safe-marking left to the
//! caller, and that is measured rather than tidy: `django.utils.html.escape`
//! returns a `SafeString`, but three of the four callers immediately run a plain
//! `str` method over the result — `escape(p).replace("\n", "<br>")` in
//! `linebreaks`, `value.replace("\n", "<br>")` in `linebreaksbr`, the `"%0…d. %s"`
//! format in `linenumbers` — and a `str` method on a `SafeString` returns a plain
//! `str`, so each of them re-marks the *assembled* text anyway
//! (`mark_safe(…)` at the end of all three bodies). Only `force_escape` returns
//! the table's own result unmodified, and it marks it itself.

/// `html.escape(text)` — the five replacements above, in that order.
///
/// Takes `&str` and not a `Value` because every caller has already run the
/// `@stringfilter` coercion the FFI loop applied (§D3): `force_escape`'s `value`,
/// `linenumbers`'s `line`, `linebreaks`'s paragraph `p` and `linebreaksbr`'s
/// `value` are all `str`s by the time their body sees them.
/// `pub` rather than `pub(crate)` as of M19: the shell's `render_value_in_context`
/// fast path is the fifth caller and the first one outside this crate. It goes
/// through the `filters` module's re-export, so the module stays private — see
/// the note there.
pub fn html_escape(text: &str) -> String {
    // Sized for the common case — most text has nothing to escape — and grown by
    // the allocator otherwise. Deliberately not `try_reserve`d: `padding.rs` needs
    // fallibility because a *width* is an unbounded number a template may name,
    // whereas this output is bounded by five times a string that already exists,
    // so the refusal it would have to report is not a case a body can reach.
    let mut escaped = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&#x27;"),
            other => escaped.push(other),
        }
    }
    escaped
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The measured row, character for character — all five replacements plus the
    /// apostrophe's spelling, which is the one a reader would guess wrong.
    #[test]
    fn it_escapes_the_five_characters() {
        assert_eq!(
            html_escape("a&b<c>d\"e'f"),
            "a&amp;b&lt;c&gt;d&quot;e&#x27;f"
        );
    }

    /// **`&` is first**, so an already-escaped entity is escaped again rather than
    /// left alone — the property `force_escape` has and `escape` does not, and the
    /// reason `force_escape` is not `conditional_escape`.
    #[test]
    fn an_entity_is_escaped_again() {
        assert_eq!(html_escape("&lt;"), "&amp;lt;");
    }

    /// Text with nothing to escape comes back unchanged, including the multi-byte
    /// and the non-BMP shapes a byte-wise implementation would split.
    #[test]
    fn unescaped_text_is_returned_unchanged() {
        for text in ["", "plain text", "é — 😀 \u{2028}", "\\ / ; = ` -"] {
            assert_eq!(html_escape(text), text, "input {text:?}");
        }
    }
}
