//! `wordcount` — `defaultfilters.py:403-407`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! @stringfilter
//! def wordcount(value):
//!     """Return the number of words."""
//!     return len(value.split())
//! ```
//!
//! `len(value.split())` is the count of runs of **Python whitespace**, which is
//! not Unicode's `White_Space` property: `str.isspace()` is also true for the
//! information separators U+001C..U+001F, so `wordcount("a\u{1c}b")` is `2`
//! (measured) where Rust's `str::split_whitespace` would yield `1`. The shared
//! table is M1's [`crate::pyunicode::split`], and reaching it — rather than
//! Rust's own split — is this body's one interesting decision.
//!
//! `wordcount` **is** `@stringfilter`-decorated (`defaultfilters.py:404`) and the
//! registry marks it so; the coercion is the FFI loop's, and this body is handed
//! the coerced text. `is_safe=False`, so the `int` result is never marked safe.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;
use crate::pyunicode;

/// `wordcount`'s body.
///
/// The `@stringfilter` coercion already happened, so `wordcount(5)` is `1`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    Ok(Value::Int(pyunicode::split(&coerced).len() as i64))
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

    /// Runs of whitespace count as one word each, and leading/trailing runs are
    /// discarded — Django's `str.split()` with no argument.
    #[test]
    fn it_counts_whitespace_separated_runs() {
        for (input, expected) in [("hello world", 2), ("  a  b  ", 2), ("", 0), ("a\tb\nc", 3)] {
            let h = host(input);
            assert_eq!(
                run(Value::Str(input.into()), None, &call(&h)).unwrap(),
                Value::Int(expected),
                "wordcount({input:?}) is {expected}"
            );
        }
    }

    /// **The Python-whitespace guard.** U+001C is a separator to Python's
    /// `str.split()` and is not `White_Space`, so a body that used Rust's
    /// `split_whitespace` returns `1` here where Django returns `2` (measured).
    #[test]
    fn the_information_separators_are_whitespace() {
        let h = host("a\u{1c}b");
        assert_eq!(
            run(Value::Str("a\u{1c}b".into()), None, &call(&h)).unwrap(),
            Value::Int(2),
            "U+001C separates words for Python"
        );

        let h = host("a\u{1f}b");
        assert_eq!(
            run(Value::Str("a\u{1f}b".into()), None, &call(&h)).unwrap(),
            Value::Int(2),
            "U+001F separates words for Python"
        );
    }

    /// The coercion is reached: `wordcount(5)` is `1`.
    #[test]
    fn it_coerces_through_the_host() {
        let h = host("5");
        assert_eq!(run(Value::Int(5), None, &call(&h)).unwrap(), Value::Int(1));
        h.assert_called("to_str");
    }
}
