//! `addslashes` — `defaultfilters.py:60-68`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def addslashes(value):
//!     """
//!     Add slashes before quotes. Useful for escaping strings in CSV, for
//!     example. Less useful for escaping JavaScript; use the ``escapejs``
//!     filter instead.
//!     """
//!     return value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
//! ```
//!
//! Three `replace`s in Django's order: **backslash first**, then the double
//! quote, then the apostrophe. The order is load-bearing — escaping the quotes
//! before the backslash would double the backslashes the quote escapes just
//! added — and it is pinned by a test whose input holds both a `"` and a `\`.
//!
//! `@stringfilter` and `is_safe=True`: the coercion, like the safe-marking of the
//! result, is the FFI loop's (§D3), so this body is three replacements and a
//! plain [`Value::Str`].

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `addslashes`'s body.
///
/// The `@stringfilter` coercion already happened, so `addslashes(5)` is `"5"`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    let escaped = coerced
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\'', "\\'");
    Ok(Value::Str(escaped.into()))
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

    /// Each of the three replacements, measured against the pinned tree.
    #[test]
    fn it_escapes_backslashes_quotes_and_apostrophes() {
        let h = host("I'm using Django");
        assert_eq!(
            run(Value::Str("I'm using Django".into()), None, &call(&h)).unwrap(),
            Value::Str("I\\'m using Django".into()),
            "an apostrophe gains a backslash"
        );

        let h = host("say \"hi\"");
        assert_eq!(
            run(Value::Str("say \"hi\"".into()), None, &call(&h)).unwrap(),
            Value::Str("say \\\"hi\\\"".into()),
            "a double quote gains a backslash"
        );

        let h = host("a\\b");
        assert_eq!(
            run(Value::Str("a\\b".into()), None, &call(&h)).unwrap(),
            Value::Str("a\\\\b".into()),
            "a backslash is doubled"
        );
    }

    /// **The order guard.** With a `"` and a `\` in one input, escaping the quote
    /// first and the backslash second doubles the backslash the quote escape
    /// added, giving `a\\"b\\c` instead of `a\"b\\c` (measured). This is the only
    /// input shape that tells the two orders apart.
    #[test]
    fn the_backslash_is_escaped_before_the_quotes() {
        let h = host("a\"b\\c");
        assert_eq!(
            run(Value::Str("a\"b\\c".into()), None, &call(&h)).unwrap(),
            Value::Str("a\\\"b\\\\c".into()),
            "the backslash is replaced first, so the one the quote escape adds is not doubled"
        );
    }

    /// The coercion is reached: `addslashes(5)` is `"5"`.
    #[test]
    fn it_coerces_through_the_host() {
        let h = host("5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        h.assert_called("to_str");
    }
}
