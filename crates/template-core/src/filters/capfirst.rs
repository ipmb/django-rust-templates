//! `capfirst` — `defaultfilters.py:71-75`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def capfirst(value):
//!     """Capitalize the first character of the value."""
//!     return value and value[0].upper() + value[1:]
//! ```
//!
//! The body is `value[0].upper()` and nothing else: one **per-code-point**
//! uppercase and **no lowercasing at all**, so `capfirst("ΟΣ")` is `"ΟΣ"` and not
//! `"Ος"`. That is measured, not derived, and it is why this filter stays Rust's
//! while its neighbour `title` is the host's: `str.upper()` has no
//! context-sensitive rule for a table to miss (§D2), and Task 1's exhaustive diff
//! holds it.
//!
//! `capfirst` is the second and last caller of `pyunicode::case` (Task 3's
//! `upper` is the first), so it shares that table's suppression of the 28 code
//! points Python 3.14.4 maps to themselves: `capfirst("\u{a7d3}")` is itself, not
//! `"\u{a7d2}"`.
//!
//! `value[0]` is a **code point**, so the string can grow: `capfirst("ßx")` is
//! `"SSx"`, measured. And `value and …` short-circuits on the empty string, which
//! is returned unchanged rather than built from nothing.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;
use crate::pyunicode;

/// `capfirst`'s body.
///
/// The `@stringfilter` coercion already happened (§D3), so `capfirst(5)` is
/// `"5"`; the text is taken back out through [`crate::filters::host::FilterHost::to_str`]
/// as the seam's accessor. The safe-marking half of both `@stringfilter` and
/// `base.py:816` is the FFI loop's — this body returns a plain [`Value::Str`] and
/// never calls `mark_safe`, which is what `is_safe=True` plus a `SafeString`
/// input makes correct.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    let mut chars = coerced.chars();
    let Some(first) = chars.next() else {
        // `value and …`: the empty string is falsy, so it is returned as-is.
        return Ok(Value::Str(coerced.into()));
    };
    // `value[0].upper()` is one code point's mapping, which can be more than one
    // `char` (`ß` → `SS`), and `value[1:]` is everything after that code point
    // untouched — no lowercasing of the remainder.
    let mut out: String = pyunicode::case::upper(first).collect();
    out.push_str(chars.as_str());
    Ok(Value::Str(out.into()))
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

    /// The empty string is returned **as-is** by `value and …`, and a body that
    /// built `first.upper() + rest` from nothing would panic or produce `""` by a
    /// different route. Pinned because the short circuit is the whole reason the
    /// filter is not a slice.
    #[test]
    fn the_empty_string_is_returned_unchanged() {
        let h = host("");
        assert_eq!(
            run(Value::Str("".into()), None, &call(&h)).unwrap(),
            Value::Str("".into())
        );
    }

    /// Only the first code point is touched, and the remainder is not
    /// lowercased: `capfirst("ΟΣ")` is `"ΟΣ"`, measured, where `title` would give
    /// `"Ος"`. The second assertion is what a body that called `str.title` or
    /// lowercased the tail would fail.
    #[test]
    fn only_the_first_code_point_is_uppercased() {
        let h = host("hello");
        assert_eq!(
            run(Value::Str("hello".into()), None, &call(&h)).unwrap(),
            Value::Str("Hello".into())
        );

        let h = host("ΟΣ");
        assert_eq!(
            run(Value::Str("ΟΣ".into()), None, &call(&h)).unwrap(),
            Value::Str("ΟΣ".into()),
            "the `Σ` after the first code point is left alone"
        );
    }

    /// `value[0]` is a code point and its uppercase can grow: `ß` → `SS`.
    #[test]
    fn the_uppercase_can_grow_the_string() {
        let h = host("ßx");
        assert_eq!(
            run(Value::Str("ßx".into()), None, &call(&h)).unwrap(),
            Value::Str("SSx".into())
        );
    }

    /// The table is reached, and its suppression is load-bearing: U+A7D3 is one
    /// of the 28 code points Rust would map and Python leaves alone. Using
    /// `char::to_uppercase` directly — which looks identical on every ASCII
    /// input — fails here.
    #[test]
    fn it_suppresses_the_code_point_rust_would_map() {
        assert_eq!(
            '\u{a7d3}'.to_uppercase().collect::<String>(),
            "\u{a7d2}",
            "Rust's own tables do map U+A7D3 — which is what makes the next line a suppression"
        );
        let h = host("\u{a7d3}");
        assert_eq!(
            run(Value::Str("\u{a7d3}".into()), None, &call(&h)).unwrap(),
            Value::Str("\u{a7d3}".into()),
            "Python leaves U+A7D3 as the identity, so the filter must too"
        );
    }

    /// The coercion is reached: `capfirst(5)` is `"5"`, and a body that took the
    /// text without going through the seam would not be handed it.
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
