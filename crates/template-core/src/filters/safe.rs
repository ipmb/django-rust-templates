//! `safe` — `defaultfilters.py:511-516`.
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
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `safe`'s body: `mark_safe(value)`, whole, on the value the `@stringfilter`
/// wrapper produced.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    Ok(call.host.mark_safe(value))
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

    /// The marker is the host's answer, and the body makes exactly that one call.
    #[test]
    fn it_marks_the_value_safe_through_the_host() {
        let h = TestFilterHost {
            mark_safe: Some(Value::Safe("MARKED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("x".into()), None, &call(&h)).unwrap(),
            Value::Safe("MARKED".into())
        );
        assert_eq!(h.calls(), vec!["mark_safe"]);
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
