//! `iriencode` — `defaultfilters.py:223-227`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def iriencode(value):
//!     """Escape an IRI value for use in a URL."""
//!     return iri_to_uri(value)
//! ```
//!
//! The **entire** body is `django.utils.encoding.iri_to_uri(value)` —
//! [`FilterHost::iri_to_uri`] — and `@stringfilter` has already coerced the value,
//! so the text comes back through [`FilterHost::to_str`]. The result is a plain
//! `str` (`urllib.parse.quote` returns one), so this body returns `Value::Str`;
//! `is_safe=True` and a `SafeData` input make the FFI loop mark it, which is
//! measured: `mark_safe("abc")|iriencode` is `(SafeString, 'abc')` while a plain
//! input is `(str, 'abc') safe=False`.
//!
//! **No `try`/`except` here, and the plan's claim of one is wrong.** Task 8's
//! `iriencode` bullet (and the first-review round's row 3) describe a
//! `try/except TypeError -> ""` wrapper around this call. The pinned `6.0.9`
//! source has neither: `django.utils.encoding.iri_to_uri`
//! (`.django/django/utils/encoding.py:107-135`) is three statements —
//! `if iri is None: return iri`, `isinstance(iri, Promise)` → `str(iri)`, then
//! `return quote(iri, safe="/#%[]=:;$&()+,!?*@'~")` — with no `try` at all. There
//! is no branch to port, and coding to the claim would have added a behaviour the
//! pinned tree does not have.
//!
//! The `iri_to_uri` seam is **infallible** on the strength of an audit rather
//! than assumption: the one input that makes it raise is a lone surrogate, which
//! a Rust `&str` cannot carry (`host.rs`'s `iri_to_uri` doc). Measured rows
//! (pinned 6.0.9): `'abc'` → `'abc'`; `"a b/c?d=e&f#g h iéü"` →
//! `'a%20b/c?d=e&f#g%20h%20i%C3%A9%C3%BC'`; `"a%20b"` → `'a%20b'` (an
//! already-quoted value is left alone); `5` → `'5'` (the `@stringfilter`
//! coercion).

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `iriencode`'s body: `iri_to_uri(str(value))`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let text = call.host.to_str(&value)?;
    Ok(Value::Str(call.host.iri_to_uri(&text).into()))
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

    fn host(quoted: &str, text: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            iri_to_uri: Some(quoted.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The whole body is the one seam call, and its answer is returned as a plain
    /// `str` (the loop marks a `SafeData` input, not the body).
    #[test]
    fn it_returns_the_hosts_iri_to_uri() {
        let h = host("a%20b/c?d=e&f#g%20h%20i%C3%A9%C3%BC", "a b/c?d=e&f#g h iéü");
        assert_eq!(
            run(Value::Str("a b/c?d=e&f#g h iéü".into()), None, &call(&h)).unwrap(),
            Value::Str("a%20b/c?d=e&f#g%20h%20i%C3%A9%C3%BC".into())
        );
        h.assert_called("iri_to_uri");
    }

    /// A plain input is **not** marked safe by the body — this is the half of the
    /// contract that says the safe-marking is the FFI loop's. `Value::Safe` and
    /// `Value::Str` are structurally unequal, so a body that wrongly marked would
    /// fail here.
    #[test]
    fn the_body_does_not_mark_the_result_safe() {
        let h = host("abc", "abc");
        let result = run(Value::Str("abc".into()), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("abc".into()));
        assert_ne!(result, Value::Safe("abc".into()));
    }

    /// The text is read through the seam: `iriencode(5)` is `'5'`, the
    /// `@stringfilter` coercion.
    #[test]
    fn it_coerces_through_the_host() {
        let h = host("5", "5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        h.assert_called("to_str");
    }
}
