//! `make_list` — `defaultfilters.py:254-263`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! @stringfilter
//! def make_list(value):
//!     """
//!     Return the value turned into a list.
//!
//!     For an integer, it's a list of digits.
//!     For a string, it's a list of characters.
//!     """
//!     return list(value)
//! ```
//!
//! `list(str(value))` is a list of **code points**, not of bytes and not of
//! grapheme clusters: a combining sequence is two elements, measured
//! (`make_list("e\u{301}")` is `["e", "\u{301}"]`), and a multi-byte code point is
//! one (`make_list("é")` is `["é"]`). The `@stringfilter` coercion is what turns
//! an integer into its digit characters, so `make_list(123)` is
//! `["1", "2", "3"]`.
//!
//! `is_safe=False`, so the result is never marked safe and a `SafeString` input
//! still produces a plain list — the [`Value::List`] this body returns is what
//! the FFI loop leaves alone.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `make_list`'s body.
///
/// The `@stringfilter` coercion already happened, so `make_list(123)` is
/// `["1", "2", "3"]`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    let items = coerced
        .chars()
        .map(|c| Value::Str(c.to_string().into()))
        .collect();
    Ok(Value::List(items))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(text: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// A string becomes its code points.
    #[test]
    fn it_lists_the_code_points() {
        let h = host("abc");
        assert_eq!(
            run(Value::Str("abc".into()), None, &call(&h)).unwrap(),
            Value::List(vec![
                Value::Str("a".into()),
                Value::Str("b".into()),
                Value::Str("c".into())
            ])
        );
    }

    /// **The code-point-not-byte guard.** `"é"` is one code point and two UTF-8
    /// bytes; a body that split on bytes would produce two elements here. A
    /// `combining sequence` guard sits beside it: `e` + U+0301 is two code points
    /// and one grapheme cluster, and Django's `list()` gives two elements.
    #[test]
    fn it_splits_on_code_points_not_bytes_or_graphemes() {
        let h = host("é");
        assert_eq!(
            run(Value::Str("é".into()), None, &call(&h)).unwrap(),
            Value::List(vec![Value::Str("é".into())]),
            "one code point, two bytes, one element"
        );

        let h = host("e\u{301}");
        assert_eq!(
            run(Value::Str("e\u{301}".into()), None, &call(&h)).unwrap(),
            Value::List(vec![Value::Str("e".into()), Value::Str("\u{301}".into())]),
            "a combining sequence is two code points, so two elements"
        );
    }

    /// The coercion is what makes an integer a list of digits.
    #[test]
    fn an_integer_becomes_its_digits() {
        let h = host("123");
        assert_eq!(
            run(Value::Int(123), None, &call(&h)).unwrap(),
            Value::List(vec![
                Value::Str("1".into()),
                Value::Str("2".into()),
                Value::Str("3".into())
            ])
        );
        h.assert_called("to_str");
    }

    /// The empty string is an empty list, and a `SafeString` still yields a plain
    /// list: `is_safe=False`, so the elements are [`Value::Str`] and never
    /// [`Value::Safe`].
    #[test]
    fn the_empty_string_is_empty_and_safety_is_not_propagated() {
        let h = host("");
        assert_eq!(
            run(Value::Str("".into()), None, &call(&h)).unwrap(),
            Value::List(vec![])
        );

        let h = host("ab");
        assert_eq!(
            run(Value::Safe("ab".into()), None, &call(&h)).unwrap(),
            Value::List(vec![Value::Str("a".into()), Value::Str("b".into())]),
            "elements are plain str, not SafeString"
        );
    }
}
