//! `upper` — `defaultfilters.py:358-362`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! @stringfilter
//! def upper(value):
//!     return value.upper()
//! ```
//!
//! The body is `str.upper`, and this is one of the two filters where that is a
//! **per-code-point** question: Task 1's exhaustive diff against CPython holds
//! `upper` and `capfirst` and nothing else, and `str.upper()` has no
//! context-dependent rule for a table to miss. So the table is Rust's, and this
//! filter is one of its **two** callers: `capfirst` is
//! `value[0].upper() + value[1:]` (§D2), which is one per-code-point `upper` and
//! no lowercasing at all, so it reaches the same table and is unaffected by the
//! Final_Sigma finding that pushed `lower` across the seam.
//!
//! `is_safe=False`, so neither half of `@stringfilter`'s safe-marking can fire:
//! the wrapper's half needs the inner `is_safe` to be true, and the loop's half
//! (`base.py:816`) needs this flag. Nothing here calls `mark_safe`, and that is
//! the measurement rather than an omission.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;
use crate::pyunicode;

/// `upper`'s body.
///
/// The `@stringfilter` coercion is the first line (§D3): Django's wrapper calls
/// `str(value)` before the body sees it, so `upper(5)` is `"5"` and not a
/// `TypeError`. The wrapper lives here rather than in a shared seam because §10.4
/// freezes the shared files against the fan-out, and `cut` — this group's
/// neighbour — needs a rule the wrapper does not have.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    Ok(Value::Str(pyunicode::case::upper_str(&coerced).into()))
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

    /// The three shapes Task 1's exhaustive parity test cannot state: a mapping
    /// that *grows* the string, one of the 28 code points where Python and Rust
    /// disagree, and a non-`str` input, which only the coercion makes work.
    #[test]
    fn it_uppercases_the_coerced_text() {
        let host = TestFilterHost {
            to_str: Some("straße".to_string()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("straße".into()), None, &call(&host)).unwrap(),
            Value::Str("STRASSE".into()),
            "`ß` uppercases to `SS`: the string grows"
        );

        // U+A7D3 is one of the 28 code points where Rust and CPython disagree,
        // but the disagreement runs the *other* way from what the phrase
        // "Python is missing a mapping" suggests: Rust maps it and Python 3.14.4
        // does not, so `pyunicode::case` **suppresses** Rust's answer. The
        // interesting assertion is therefore the identity — and the first line,
        // which pins the direction by showing what Rust alone would have said.
        // Without it, "the filter left this character alone" would also be
        // satisfied by a table with no entry for this code point, which is the
        // inert-guard failure §11 exists to catch.
        assert_eq!(
            '\u{a7d3}'.to_uppercase().collect::<String>(),
            "\u{a7d2}",
            "Rust's own tables do map U+A7D3 — which is what makes the next line a suppression"
        );
        let host = TestFilterHost {
            to_str: Some("\u{a7d3}".to_string()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("\u{a7d3}".into()), None, &call(&host)).unwrap(),
            Value::Str("\u{a7d3}".into()),
            "Python leaves U+A7D3 as the identity, so the filter must too"
        );

        let host = TestFilterHost {
            to_str: Some("5".to_string()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(5), None, &call(&host)).unwrap(),
            Value::Str("5".into()),
            "`upper(5)` is `\"5\"` — the coercion, not a `TypeError`"
        );
    }

    /// The seam is *reached*, not merely available: a body that skipped the
    /// coercion would pass every assertion above by accident on a `Value::Str`
    /// input while being wrong for `upper(5)`.
    #[test]
    fn it_coerces_through_the_host() {
        let host = TestFilterHost {
            to_str: Some("MiXeD".to_string()),
            ..TestFilterHost::default()
        };
        run(Value::Opaque(0), None, &call(&host)).unwrap();
        host.assert_called("to_str");
    }
}
