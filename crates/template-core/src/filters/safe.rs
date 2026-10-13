//! `safe` — `defaultfilters.py:511-515`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def safe(value):
//!     """Mark the value as a string that should not be auto-escaped."""
//!     return mark_safe(value)
//! ```
//!
//! **`mark_safe` is the host's, not a Rust cast to [`Value::Safe`]** — the reason
//! is the non-`str` half. Measured on the pinned tree, `mark_safe(5)` returns the
//! `str` subclass `SafeString` (through `SafeString.__new__`'s `str()`), and a
//! value carrying `__html__` returns **unchanged and not necessarily `SafeData`**
//! (`safestring.py:64-78`). A Rust `Value::Safe(to_str(value))` would answer the
//! common `str` case but not those two, and would be a second `mark_safe` drifting
//! from the first. So the body asks
//! [`mark_safe`](crate::filters::host::FilterHost::mark_safe).
//!
//! `@stringfilter` has usually coerced the value to a `str` before the body, but
//! the conversion is the host's either way. Measured: `safe(5)` is
//! `(SafeString, '5') safe=True`, and `safe(mark_safe("<b>"))` is
//! `(SafeString, '<b>')` — the passthrough.

use crate::filters::host::FilterCall;
use crate::filters::safety;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `safe`'s body: `mark_safe(value)`, whole, on the value the `@stringfilter`
/// wrapper produced.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    Ok(safety::mark_safe(value, call))
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

    /// An exact `str` is marked in Rust: `mark_safe` of a plain `str` is
    /// `SafeString` with the same text, which `Value::Safe` is. The host is not
    /// asked — this was one Python crossing per call and measured 1.21x *slower*
    /// than the engine this package replaces.
    #[test]
    fn a_plain_string_is_marked_in_rust() {
        let h = TestFilterHost::default();
        assert_eq!(
            run(Value::Str("x".into()), None, &call(&h)).unwrap(),
            Value::Safe("x".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    /// **A non-`str` value is handed to the host rather than cast in Rust.** The
    /// scripted marker is what comes back, which a Rust `Value::Safe(to_str(…))`
    /// could not produce — and `mark_safe(5)` really does convert, so the seam is
    /// not decoration.
    #[test]
    fn a_non_str_value_is_marked_by_the_host() {
        let h = TestFilterHost {
            mark_safe: Some(Value::Safe("5".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Safe("5".into())
        );
        h.assert_called("mark_safe");
    }
}
