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
use crate::filters::safety;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `escape`'s body: `conditional_escape(value)`, whole.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    safety::conditional_escape(&value, call)
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

    /// An exact `str` is escaped in Rust, with Django's own table and Django's
    /// own answer — `conditional_escape` reaches `escape(text)` for a value with
    /// no `__html__`, and the result is safe. The host is not asked, which is the
    /// point: this was one Python crossing per call and measured 1.35x *slower*
    /// than the engine this package replaces.
    #[test]
    fn a_plain_string_is_escaped_in_rust() {
        let h = TestFilterHost::default();
        assert_eq!(
            run(Value::Str("<a>&'\"".into()), None, &call(&h)).unwrap(),
            Value::Safe("&lt;a&gt;&amp;&#x27;&quot;".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    /// The branch that is still the host's, and the reason it has to be: a
    /// `Promise` or a markupsafe `Markup` is an `Opaque`, and `conditional_escape`
    /// answers for both in ways no Rust `Value` can.
    #[test]
    fn an_opaque_value_is_still_the_hosts() {
        let h = TestFilterHost {
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Opaque(0), None, &call(&h)).unwrap(),
            Value::Safe("ESCAPED".into())
        );
        assert_eq!(h.calls(), vec!["conditional_escape"]);
    }

    /// **A `SafeData` string is returned unescaped, and that is the row that
    /// tells `escape` from `force_escape`.** It is answered in Rust now, which is
    /// sound only because `Value::Safe` is produced for `isinstance(…, SafeData)`
    /// alone: a `str` subclass carrying `__html__` *without* being `SafeData` —
    /// markupsafe's `Markup` — is interned as an `Opaque` by the classifier and so
    /// takes the host branch above. See `filters::safety`.
    #[test]
    fn a_safe_string_is_not_escaped_again() {
        let h = TestFilterHost::default();
        assert_eq!(
            run(Value::Safe("<b>".into()), None, &call(&h)).unwrap(),
            Value::Safe("<b>".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }
}
