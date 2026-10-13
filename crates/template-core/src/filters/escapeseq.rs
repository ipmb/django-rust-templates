//! `escapeseq` — `defaultfilters.py:464-471`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def escapeseq(value):
//!     """
//!     An "escape" filter for sequences. Mark each element in the sequence,
//!     individually, as a string that should be auto-escaped. Return a list with
//!     the results.
//!     """
//!     return [conditional_escape(obj) for obj in value]
//! ```
//!
//! No `@stringfilter`: the value is **iterated**, not coerced, so each element is
//! asked the same `__html__`-aware question `escape` asks of its whole value. Each
//! element therefore goes through
//! [`conditional_escape`](crate::filters::host::FilterHost::conditional_escape) —
//! a `SafeString` element passes through, a plain `str` is escaped — and the
//! **list itself is not safe**: `is_safe=True` is the loop's flag, and
//! `base.py:816` marks only when the pre-filter value is `SafeData`, which a list
//! is not.
//!
//! Measured on the pinned tree, `escapeseq(["a", "<b>"])` is
//! `(list, ['a', '&lt;b&gt;']) safe=False` with element types
//! `['SafeString', 'SafeString']` — the per-element comparison §D5 requires, since
//! a text-only row would not see the markers.
//!
//! The value is [`iter_values`](crate::filters::host::FilterHost::iter_values)
//! (`list(v)`), which is also where a non-iterable raises the escaping `TypeError`
//! — `escapeseq(5)` has no `try`.

use crate::filters::host::FilterCall;
use crate::filters::safety;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `escapeseq`'s body: `conditional_escape` over every element, in order.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let items = call.host.iter_values(&value)?;
    let mut escaped = Vec::with_capacity(items.len());
    for item in &items {
        escaped.push(safety::conditional_escape(item, call)?);
    }
    Ok(Value::List(escaped))
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

    /// Every element is escaped, not just the first, and the list is a plain
    /// [`Value::List`] — **not** `Value::Safe` — so the safety is per element, as
    /// `is_safe=True` plus `base.py:816` measure it.
    ///
    /// Asserted on the text rather than on a scripted sentinel: native strings are
    /// escaped in Rust now (`filters::safety`), and the real table is the stronger
    /// claim anyway.
    #[test]
    fn it_escapes_every_element() {
        // `iter_values` is scripted because it is a host *trait* call whatever the
        // value is; the real host answers it for a `Value::List` without Python.
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Str("a".into()), Value::Str("<b>".into())]),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h)).unwrap(),
            Value::List(vec![
                Value::Safe("a".into()),
                Value::Safe("&lt;b&gt;".into()),
            ])
        );
        assert_eq!(
            h.calls(),
            vec!["iter_values"],
            "no element reached `conditional_escape`"
        );
    }

    /// The same claim for elements Rust cannot escape itself: one host call per
    /// element, never one for the sequence.
    #[test]
    fn it_escapes_every_opaque_element_through_the_host() {
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Opaque(0), Value::Opaque(1)]),
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h)).unwrap(),
            Value::List(vec![
                Value::Safe("ESCAPED".into()),
                Value::Safe("ESCAPED".into()),
            ])
        );
        assert_eq!(
            h.calls()
                .iter()
                .filter(|name| **name == "conditional_escape")
                .count(),
            2,
            "`conditional_escape` runs once per element, not once for the sequence"
        );
    }

    /// An empty sequence is an empty list, and nothing is escaped.
    #[test]
    fn an_empty_sequence_is_an_empty_list_and_escapes_nothing() {
        let h = TestFilterHost {
            iter_values: Some(vec![]),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h)).unwrap(),
            Value::List(vec![])
        );
        assert_eq!(h.calls(), vec!["iter_values"]);
    }

    /// A non-iterable escapes: there is no `try` around the comprehension.
    #[test]
    fn a_non_iterable_value_escapes() {
        let h = TestFilterHost::default();
        h.raise_on("iter_values", PyErrorKind::TypeError);
        let error = run(Value::Int(5), None, &call(&h)).unwrap_err();
        assert!(
            matches!(
                &error.kind,
                ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError
            ),
            "escapeseq(5) must raise TypeError: {error:?}"
        );
        assert!(!h.calls().contains(&"conditional_escape"));
    }
}
