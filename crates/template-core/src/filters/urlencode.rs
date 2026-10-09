//! `urlencode` — `defaultfilters.py:365-379`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! @stringfilter
//! def urlencode(value, safe=None):
//!     kwargs = {}
//!     if safe is not None:
//!         kwargs["safe"] = safe
//!     return quote(value, **kwargs)
//! ```
//!
//! `@stringfilter` coerces the value and `is_safe=False`, so the body takes
//! [`FilterHost::to_str`] and returns a plain `Value::Str` — measured
//! `mark_safe("a b/c")|urlencode` is `(str, 'a%20b/c')`, never a `SafeString`.
//!
//! ## Why the body is one host call and not a `safe` selection
//!
//! An earlier revision had Rust classify `safe` and hand the host a `(&str,
//! &str)` pair, on the reading that the argument branch is three cases:
//! absent-or-`None` → `"/"`, a `str` → itself, and everything else "iterated by
//! `quote`, and raising". **The third case is false**, and the measurement is the
//! table this module used to carry as a divergence notice: `quote("a b/c",
//! safe=…)` *answers* for every all-`int` container (`safe=[1]` is
//! `'a%20b%2Fc'`), for `bytes` and `bytearray` (`safe=b'/'` is `'a%20b/c'`), and
//! for `[]`; it raises `ValueError: bytes must be in range(0, 256)` for `[-1]`
//! and a *different* `TypeError` for `[1.0]`. That is not a case analysis at
//! all: it is `bytes([c for c in safe if c < 128])` (`urllib/parse.py`,
//! `quote_from_bytes`), Python's own `bytes()` over a comprehension.
//!
//! §D7.1 already answers for an argument whose *type* the Python function
//! observes — pass the `Value` through and let Python decide — so
//! [`FilterHost::quote`] takes `Option<&Value>` and this body is exactly the
//! selection Django's own two lines make. Every shape above is therefore a
//! **parity** row in the corpus rather than a documented divergence, which is the
//! point of the change: the rejected version raised where Django answers, and a
//! drop-in that refuses a template Django renders is wrong in the worse
//! direction.
//!
//! Measured rows (pinned 6.0.9), the default `safe` being `'/'`:
//!
//! | token | result |
//! |---|---|
//! | `"a b/c?d=e&f#g"` | `'a%20b/c%3Fd%3De%26f%23g'` |
//! | `safe=''` | `'a%20b%2Fc%3Fd%3De%26f%23g'` |
//! | `safe='abc'` | `'a%20b%2Fc%3Fd%3De%26f%23g'` (`a`,`b`,`c` absent from input) |
//! | explicit `None` | the same as no argument |
//! | `5` | `'5'` |
//! | `"éü 中"` | `'%C3%A9%C3%BC%20%E4%B8%AD'` |

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `urlencode`'s body: `str(value)` through the host, then `quote(value, safe)`.
///
/// The mapping below is Django's `if safe is not None` and nothing more: an
/// **absent** argument and an explicit `None` both leave the keyword off, and any
/// other object is handed over as itself. Nothing here inspects the argument's
/// type; see the module doc for the measurement that moved that inspection across
/// the seam.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let text = call.host.to_str(&value)?;
    let safe = match &arg {
        None | Some(Value::None) => None,
        Some(other) => Some(other),
    };
    Ok(Value::Str(call.host.quote(&text, safe)?.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::{ErrorKind, PyErrorKind};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    fn host(quoted: &str, text: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            quote: Some(quoted.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The value is quoted through the seam and returned as a plain `str`.
    ///
    /// `TestFilterHost` records the operation and not the argument, so *which*
    /// `safe` the body passes is pinned by the corpus rather than here — and it
    /// can be, now that the shapes are parity rows: `safe=[1]`, `safe=b'/'` and
    /// `safe=['/']` are all reachable through `arg_variable` and all agree with
    /// Django, where the classifying body raised for the first two.
    #[test]
    fn it_returns_the_hosts_quote() {
        let h = host("a%20b/c%3Fd%3De%26f%23g", "a b/c?d=e&f#g");
        assert_eq!(
            run(Value::Str("a b/c?d=e&f#g".into()), None, &call(&h)).unwrap(),
            Value::Str("a%20b/c%3Fd%3De%26f%23g".into())
        );
        h.assert_called("quote");
    }

    /// An absent argument and an explicit `None` are both `None` at the seam —
    /// Django's `kwargs` stays empty for either, so the two tokens are one call —
    /// and both still reach the host.
    #[test]
    fn none_and_absent_both_omit_the_safe_keyword() {
        for arg in [None, Some(Value::None)] {
            let h = host("a%20b/c", "a b/c");
            assert_eq!(
                run(Value::Str("a b/c".into()), arg, &call(&h)).unwrap(),
                Value::Str("a%20b/c".into())
            );
            h.assert_called("quote");
        }
    }

    /// A refusal out of `quote` keeps the seam's class and operation, which is
    /// how Python's own refusal — `'<' not supported between instances of 'str'
    /// and 'int'` for `safe=['/']`, `'int' object is not iterable` for
    /// `safe=5` — reaches a template author unchanged. The body adds no `try`.
    #[test]
    fn a_quote_failure_escapes_with_the_seams_class() {
        let h = host("", "a b/c");
        h.raise_on("quote", PyErrorKind::TypeError);
        let error = run(Value::Str("a b/c".into()), Some(Value::Int(5)), &call(&h)).unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::TypeError);
                assert_eq!(host_error.operation, "quote");
            }
            other => panic!("a `quote` refusal must escape, got {other:?}"),
        }
        h.assert_called("quote");
    }

    /// The text is read through the seam, so the `@stringfilter` coercion is the
    /// host's and an `int` value arrives quoted.
    #[test]
    fn the_text_is_read_through_the_host() {
        let h = host("5", "5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        h.assert_called("to_str");
    }
}
