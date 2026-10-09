//! `escape` — `defaultfilters.py:457-461`.
//!
//! ```python
//! @register.filter("escape", is_safe=True)
//! @stringfilter
//! def escape_filter(value):
//!     """Mark the value as a string that should be auto-escaped."""
//!     return conditional_escape(value)
//! ```
//!
//! **The body is one host call, not a Rust helper** — §D4. `conditional_escape`
//! (`django/utils/html.py:115-127`) branches on `isinstance(text, Promise)`, then
//! `hasattr(text, "__html__")`, and only last on the escape itself; the
//! `__html__` convention is shared with markupsafe, which implements it **without
//! being `SafeData`**. A Rust `is_safe`-then-escape would get those objects wrong,
//! so the whole protocol is
//! [`conditional_escape`](crate::filters::host::FilterHost::conditional_escape)'s
//! and this body adds no branch of its own.
//!
//! The `@stringfilter` coercion is the FFI loop's (§D3), so the value here is
//! already the string Django's wrapper produced and the body hands it over
//! untouched. `is_safe=True` is likewise the loop's: the result's safety is marked
//! by `base.py:816`, not here.
//!
//! The one row that tells this filter from `force_escape` is the **`SafeData`
//! input**. Measured on the pinned tree, `escape(mark_safe("<b>"))` is the input
//! returned **unchanged** — `(SafeString, '<b>') safe=True` — because
//! `conditional_escape` takes the `__html__` branch, while `escape("&lt;")` on a
//! *plain* `str` escapes the `&` again to `(SafeString, '&amp;lt;')` (measured).
//! A plain `str` has no `__html__`, so the `&` is escaped; only the safe input
//! skips the table.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `escape`'s body: `conditional_escape(value)`, whole.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host.conditional_escape(&value)
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

    /// The body is one call and returns whatever it answered, so both the text and
    /// the safety marker are the host's.
    #[test]
    fn it_delegates_to_conditional_escape_and_returns_its_answer() {
        let h = TestFilterHost {
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("<a>".into()), None, &call(&h)).unwrap(),
            Value::Safe("ESCAPED".into())
        );
        assert_eq!(h.calls(), vec!["conditional_escape"]);
    }

    /// **The `SafeData` branch is the host's, deliberately.** A body that asked
    /// [`FilterHost::is_safe`](crate::filters::host::FilterHost::is_safe) itself
    /// and skipped the escape would be a second, drifting `conditional_escape` —
    /// wrong for markupsafe's `Markup` and for a `Promise`, per §D4. So the body
    /// must reach `conditional_escape` even when the input is already safe.
    #[test]
    fn it_leaves_the_safe_data_branch_to_the_host() {
        let h = TestFilterHost {
            conditional_escape: Some(Value::Safe("<b>".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Safe("<b>".into()), None, &call(&h)).unwrap(),
            Value::Safe("<b>".into())
        );
        h.assert_called("conditional_escape");
        assert!(
            !h.calls().contains(&"is_safe"),
            "the host owns the `__html__` question: {:?}",
            h.calls()
        );
    }
}
