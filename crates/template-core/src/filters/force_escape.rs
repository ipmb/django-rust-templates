//! `force_escape` — `defaultfilters.py:474-482`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def force_escape(value):
//!     """
//!     Escape a string's HTML. Return a new string containing the escaped
//!     characters (as opposed to "escape", which marks the content for later
//!     possible escaping).
//!     """
//!     return escape(value)
//! ```
//!
//! where that `escape` is the imported `django.utils.html.escape`
//! (`html.py:107-116`: `SafeString(html.escape(str(text)))`). **It is Rust's**,
//! and the pair `force_escape`/`escape` is the sharpest illustration in M4 of
//! §D2's line: this five-replacement table is a function of its argument alone, so
//! [`html_escape`] owns it and no host method is needed, whereas `escape`'s
//! next-door `conditional_escape` is a protocol and is the host's.
//!
//! The difference from the `escape` filter is one row wide, and it is the
//! **`SafeData` input**: measured on the pinned tree, `force_escape(mark_safe(
//! "<b>"))` is `(SafeString, '&lt;b&gt;')` while `escape(mark_safe("<b>"))`
//! returns the input unchanged — because `force_escape` runs the table directly
//! rather than the `__html__`-aware protocol. The table escapes `&` **first**, so
//! an already-escaped entity is escaped again: `force_escape("&lt;")` is
//! `(SafeString, '&amp;lt;')` (measured).
//!
//! `mark_safe` here is Rust's, not the host's. The value wrapped is
//! [`html_escape`]'s own `String`, known to be a Python `str`, so [`Value::Safe`]
//! states the identical result `mark_safe` would — the host's
//! [`mark_safe`](crate::filters::host::FilterHost::mark_safe) exists for the
//! non-`str` conversion and the `__html__` passthrough that `safe` and `safeseq`
//! need, and neither is reachable here.
//!
//! The `str(text)` inside `html.escape` is
//! [`to_str`](crate::filters::host::FilterHost::to_str): `@stringfilter` has
//! normally already coerced, but the helper stringifies regardless, and the seam
//! call is what makes `force_escape(5)` `(SafeString, '5')` (measured).

use crate::filters::host::FilterCall;
use crate::filters::html_escape::html_escape;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `force_escape`'s body: `mark_safe(html.escape(str(value)))`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let text = call.host.to_str(&value)?;
    Ok(Value::Safe(html_escape(&text).into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    fn host(text: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The five replacements, character for character, with the apostrophe's
    /// measured spelling `&#x27;`.
    #[test]
    fn it_escapes_the_five_characters() {
        let h = host("a&b<c>d\"e'f");
        assert_eq!(
            run(Value::Str("a&b<c>d\"e'f".into()), None, &call(&h)).unwrap(),
            Value::Safe("a&amp;b&lt;c&gt;d&quot;e&#x27;f".into())
        );
    }

    /// **The row that tells `force_escape` from `escape`.** `&` is escaped first,
    /// so an entity is escaped again — and unlike `escape`, this holds even when
    /// the input is already safe, because the table does not consult `__html__`.
    #[test]
    fn an_entity_is_escaped_again() {
        let h = host("&lt;");
        assert_eq!(
            run(Value::Str("&lt;".into()), None, &call(&h)).unwrap(),
            Value::Safe("&amp;lt;".into())
        );
    }

    /// The safe marker is Rust's: the body asks the host for `to_str` and
    /// **nothing else**, so it never reaches `mark_safe`.
    #[test]
    fn the_safe_marker_is_constructed_in_rust_not_by_the_host() {
        let h = host("<b>");
        assert_eq!(
            run(Value::Str("<b>".into()), None, &call(&h)).unwrap(),
            Value::Safe("&lt;b&gt;".into())
        );
        assert_eq!(
            h.calls(),
            vec!["to_str"],
            "the table and the marker are Rust's; no host marking"
        );
    }

    /// `str(text)` is reached: `force_escape(5)` is `'5'`.
    #[test]
    fn it_stringifies_a_non_str_input_through_the_host() {
        let h = host("5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Safe("5".into())
        );
        h.assert_called("to_str");
    }
}
