//! `lower` — `defaultfilters.py:247-251`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def lower(value):
//!     return value.lower()
//! ```
//!
//! The body is **the host's** `str.lower`, and the reason is the fifth Unicode
//! surprise of this project: `str.lower()` is not a function of the code point.
//! `"ΟΣ".lower()` is `"ος"`, not `"οσ"` — Unicode's **Final_Sigma** rule, which
//! depends on what surrounds the `Σ`. Task 1's per-code-point table provably
//! cannot express it, Rust's `str::to_lowercase` implements the rule but
//! disagrees with Python at the 28 suppressed code points, and combining the two
//! would leave a silent divergence on a string holding both. So there is
//! deliberately **no `lower` in `pyunicode::case`** for this to call.
//!
//! `is_safe=True` here, unlike its neighbour `upper` — measured, and the two
//! differ because `upper`'s decorator is `@register.filter(is_safe=False)` while
//! `lower`'s omits the flag entirely and `is_safe` defaults to `True`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `lower`'s body.
///
/// The `@stringfilter` coercion comes first (§D3), so `lower(5)` is `"5"`.
///
/// **The wrapper's `mark_safe` half is the loop's, here.** Django's wrapper marks
/// the result safe iff the *coerced* value was `SafeData` **and** the inner
/// `is_safe` is true; `lower` is `is_safe=True`, and `base.py:816` applies the
/// same test to the value the filter was handed. The two sites agree on every
/// input whose `str()` preserves safety — which is every `str` and every `str`
/// subclass, because `str(SafeString)` returns itself — so nothing is lost by
/// leaving that half to the loop. They diverge on exactly the operand §D3
/// measured: a lazy `__proxy__`, which is not `SafeData` itself but whose `str()`
/// is. That divergence is a property of the seam and is recorded on the M4 plan's
/// review list rather than papered over here; a body-side check would not fix it,
/// since the body is handed a Rust `String` and the question is about a Python
/// type.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let coerced = call.host.to_str(&value)?;
    Ok(Value::Str(call.host.str_lower(&coerced).into()))
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

    /// The body's **whole** behaviour is the delegation, so this asserts that
    /// rather than a lowercasing: the scripted answer is Final_Sigma's, and no
    /// per-code-point rule — which is all `pyunicode::case` has — could produce
    /// it. A body that lowered with a Rust table would fail here on the first
    /// assertion and pass every "does it lowercase" test anyone would write.
    #[test]
    fn it_lowercases_through_the_host() {
        let host = TestFilterHost {
            to_str: Some("ΟΣ".to_string()),
            str_lower: Some("ος".to_string()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("ΟΣ".into()), None, &call(&host)).unwrap(),
            Value::Str("ος".into()),
            "the host's Final_Sigma answer is the filter's answer"
        );
        host.assert_called("str_lower");
    }

    #[test]
    fn it_coerces_and_lowers_through_the_host() {
        let host = TestFilterHost {
            to_str: Some("5".to_string()),
            str_lower: Some("5".to_string()),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(5), None, &call(&host)).unwrap(),
            Value::Str("5".into()),
            "`lower(5)` is `\"5\"` — the coercion, not a `TypeError`"
        );
        host.assert_called("to_str");
        host.assert_called("str_lower");
    }
}
