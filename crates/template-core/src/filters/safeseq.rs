//! `safeseq` — `defaultfilters.py:518-525`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def safeseq(value):
//!     """
//!     A "safe" filter for sequences. Mark each element in the sequence,
//!     individually, as safe, after converting them to strings. Return a list with
//!     the results.
//!     """
//!     return [mark_safe(obj) for obj in value]
//! ```
//!
//! No `@stringfilter`: the elements are marked, not the value. Each element goes
//! through [`mark_safe`](crate::filters::host::FilterHost::mark_safe), so a
//! non-`str` element is `str()`d and an element already carrying `__html__` is
//! returned unchanged — the same conversion/passthrough split `safe` relies on,
//! once per element.
//!
//! Measured on the pinned tree, `safeseq(["a", 5, "<b>"])` is
//! `(list, ['a', '5', '<b>']) safe=False` with element types
//! `['SafeString', 'SafeString', 'SafeString']` — note the `int` became the safe
//! `'5'`, which is why the per-element §D5 comparison is needed. The **list** is
//! not `SafeData`, so `base.py:816` does not mark it (measured `safe=False`).
//!
//! The value is [`iter_values`](crate::filters::host::FilterHost::iter_values); a
//! non-iterable raises there with no `try`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `safeseq`'s body: `mark_safe` over every element, in order.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let items = call.host.iter_values(&value)?;
    let mut marked = Vec::with_capacity(items.len());
    for item in items {
        marked.push(call.host.mark_safe(item));
    }
    Ok(Value::List(marked))
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

    /// One `mark_safe` per element — including the non-`str` one, which the host
    /// converts — and a plain [`Value::List`], not a `Value::Safe`.
    #[test]
    fn it_marks_every_element_through_the_host() {
        let h = TestFilterHost {
            iter_values: Some(vec![
                Value::Str("a".into()),
                Value::Int(5),
                Value::Safe("<b>".into()),
            ]),
            mark_safe: Some(Value::Safe("MARKED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h)).unwrap(),
            Value::List(vec![
                Value::Safe("MARKED".into()),
                Value::Safe("MARKED".into()),
                Value::Safe("MARKED".into()),
            ])
        );
        assert_eq!(
            h.calls()
                .iter()
                .filter(|name| **name == "mark_safe")
                .count(),
            3,
            "`mark_safe` runs once per element, not once for the sequence"
        );
    }

    /// An empty sequence marks nothing.
    #[test]
    fn an_empty_sequence_is_an_empty_list_and_marks_nothing() {
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

    /// A non-iterable escapes, as it does for `escapeseq`.
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
            "safeseq(5) must raise TypeError: {error:?}"
        );
        assert!(!h.calls().contains(&"mark_safe"));
    }
}
