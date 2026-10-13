//! `mark_safe` and `conditional_escape`, answered in Rust when the value is one
//! Rust already holds as text.
//!
//! **These stay host methods for everything else, and the reasons the bodies used
//! to give are still right** — they are reasons about values that are *not* native
//! strings. `mark_safe(5)` is `SafeString("5")` through `SafeString.__new__`;
//! a value carrying `__html__` comes back unchanged and not necessarily `SafeData`
//! (`safestring.py:64-78`); `conditional_escape` branches on
//! `isinstance(text, Promise)` before it branches on anything else
//! (`django/utils/html.py:115-127`). None of those are questions a Rust `Value`
//! can answer, so none of them are answered here.
//!
//! **What makes the two fast paths provable is the classifier, not this module.**
//! `PyFilterHost::classify` is exact about which Python objects become which
//! variant (`crates/template-py/src/filters.rs`):
//!
//! * [`Value::Str`] is produced **only** for `is_exact_instance_of::<PyString>()`
//!   — an exact `str`. Not a subclass, so it carries no `__html__`; not a
//!   `Promise`, which is not a `str` at all.
//! * [`Value::Safe`] is produced **only** for a `str` that `isinstance(…, SafeData)`.
//! * a `str` subclass answering `__html__` **without** being `SafeData` — markupsafe's
//!   `Markup`, the object both bodies' docstrings warn about — is **interned as
//!   [`Value::Opaque`]** precisely so this branch stays Python's.
//!
//! So for those two variants Django's answer is fully determined, and it is the
//! one below. Every other variant goes to the host unchanged. A change to
//! `classify` that widened either variant would invalidate this module, which is
//! why the rule is stated here rather than inferred at each call site.
//!
//! Measured: `escape` was 1.35x and `safe` 1.21x *against the engine this package
//! replaces*, one Python crossing per call, because the whole body was a host
//! call for a value Rust was already holding as a `String`.

use crate::filters::host::FilterCall;
use crate::filters::html_escape;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `django.utils.safestring.mark_safe`, native for text Rust holds.
///
/// `Safe` is returned unchanged rather than re-wrapped: `mark_safe` returns a
/// value carrying `__html__` as it came (`safestring.py:64-78`), and every
/// `SafeString` carries one.
pub fn mark_safe(value: Value, call: &FilterCall<'_>) -> Value {
    match value {
        Value::Safe(_) => value,
        Value::Str(text) => Value::Safe(text),
        other => call.host.mark_safe(other),
    }
}

/// `django.utils.html.conditional_escape`, native for text Rust holds.
///
/// An exact `str` has no `__html__`, so Django reaches `escape(text)` and returns
/// a `SafeString`; a `SafeData` string takes the `__html__` branch and is returned
/// unchanged. Both are reproduced here, and nothing else is.
pub fn conditional_escape(value: &Value, call: &FilterCall<'_>) -> Result<Value, Error> {
    match value {
        Value::Safe(_) => Ok(value.clone()),
        Value::Str(text) => Ok(Value::Safe(html_escape(text).into_boxed_str())),
        other => call.host.conditional_escape(other),
    }
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

    #[test]
    fn a_plain_string_is_marked_without_asking_the_host() {
        let h = TestFilterHost::default();
        assert_eq!(
            mark_safe(Value::Str("<b>".into()), &call(&h)),
            Value::Safe("<b>".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    #[test]
    fn an_already_safe_string_is_returned_as_it_came() {
        let h = TestFilterHost::default();
        assert_eq!(
            mark_safe(Value::Safe("<b>".into()), &call(&h)),
            Value::Safe("<b>".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    /// The non-str half is the host's, because `mark_safe(5)` is `SafeString("5")`
    /// and that conversion is Python's.
    #[test]
    fn a_non_string_still_goes_to_the_host() {
        let h = TestFilterHost {
            mark_safe: Some(Value::Safe("5".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(mark_safe(Value::Int(5), &call(&h)), Value::Safe("5".into()));
        assert_eq!(h.calls(), vec!["mark_safe"]);
    }

    #[test]
    fn a_plain_string_is_escaped_without_asking_the_host() {
        let h = TestFilterHost::default();
        assert_eq!(
            conditional_escape(&Value::Str("a<b>&'\"".into()), &call(&h)).unwrap(),
            // Django's table: & < > " ' all escaped, and the result is safe.
            Value::Safe("a&lt;b&gt;&amp;&#x27;&quot;".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    /// The row that tells `escape` from `force_escape`: a safe input is **not**
    /// escaped a second time.
    #[test]
    fn a_safe_string_is_not_escaped_again() {
        let h = TestFilterHost::default();
        assert_eq!(
            conditional_escape(&Value::Safe("&lt;".into()), &call(&h)).unwrap(),
            Value::Safe("&lt;".into())
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    /// An `Opaque` is the markupsafe/`Promise` case, and it stays Python's.
    #[test]
    fn an_opaque_value_still_goes_to_the_host() {
        let h = TestFilterHost {
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            conditional_escape(&Value::Opaque(0), &call(&h)).unwrap(),
            Value::Safe("ESCAPED".into())
        );
        assert_eq!(h.calls(), vec!["conditional_escape"]);
    }
}
