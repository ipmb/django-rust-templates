//! `join` — `defaultfilters.py:611-621`.
//!
//! ```python
//! @register.filter(is_safe=True, needs_autoescape=True)
//! def join(value, arg, autoescape=True):
//!     """Join a list with a string, like Python's ``str.join(list)``."""
//!     try:
//!         if autoescape:
//!             data = conditional_escape(arg).join([conditional_escape(v) for v in value])
//!         else:
//!             data = arg.join(value)
//!     except TypeError:  # Fail silently if arg isn't iterable.
//!         return value
//!     return mark_safe(data)
//! ```
//!
//! **The group's sharpest trap: on `TypeError` the filter returns `value` — the
//! unjoined list — not `""`.** Measured on the pinned tree:
//!
//! ```text
//! join([0, 1, 2], "glue")                 -> "0glue1glue2"      (SafeString)
//! join(["<a>", "<img>"], "<br>")          -> "&lt;a&gt;&lt;br&gt;&lt;img&gt;"
//! join(["<a>", "<img>"], "<br>", ae=F)    -> "<a><br><img>"
//! join(obj, "<br>")                       -> obj   (the same object: TypeError -> value)
//! join(obj, "<br>", autoescape=False)     -> obj
//! join((1, 2), "-", autoescape=False)     -> (1, 2)  (a non-str element is a TypeError)
//! join({"a": 1, "b": 2}, "-", ae=F)       -> "a-b"   (a dict iterates its keys)
//! join("ab", "-", autoescape=False)       -> "a-b"   (a str is a sequence of str)
//! join([], "-")                           -> ""      (SafeString)
//! ```
//!
//! **`is_safe=True` and `needs_autoescape=True`, and `call.autoescape` is the
//! flag.** The two branches differ in more than escaping:
//!
//! * `autoescape=True` escapes the **separator** and every **element**
//!   (`conditional_escape`, the host's), so `join([...], 5)` is `"a5b"` — a
//!   non-str separator is fine because `conditional_escape` stringifies it;
//! * `autoescape=False` is a **bare `arg.join(value)`**, so `arg` must be a `str`
//!   and each element must be a `str`. A non-str separator is
//!   `AttributeError: 'int' object has no attribute 'join'`, which the `except
//!   TypeError` does **not** catch — it escapes (measured `join(["a", "b"], 5,
//!   autoescape=False)`). A non-str **element** is the `TypeError` the filter
//!   catches, returning the value (measured `join((1, 2), "-", autoescape=False)`
//!   is the tuple `(1, 2)`).
//!
//! **Django's own comment on that `except` — `# Fail silently if arg isn't
//! iterable.` — names the wrong cause for the second case.** On the
//! `autoescape=False` branch the `TypeError` comes from `str.join` meeting a
//! non-`str` *element*, with the value iterated perfectly well; only the first
//! case is about iterability at all. Both are `TypeError` and both return the
//! value, so one comment covers two different faults and survives — but a body
//! written to the comment's letter would ask "does it iterate?" and get
//! `join((1, 2), "-", autoescape=False)` wrong.
//!
//! The result is `mark_safe`d in **both** branches — `join([0, 1, 2], "glue")`
//! and `join({"a": 1, "b": 2}, "-", autoescape=False)` are both `SafeString`.
//!
//! Iteration is [`FilterHost::iter_values`] (`list(v)`), which is also where "the
//! value is not iterable" raises the caught `TypeError`. The `str`-instance test
//! in the `autoescape=False` branch is a variant match: a Python `str` (and every
//! `str` subclass that is neither `SafeData` nor carries `__html__`) arrives as
//! [`Value::Str`], and a `SafeString` as [`Value::Safe`]. A `str` subclass that is
//! **both** a str and carries `__html__` (markupsafe's `Markup`) arrives as an
//! `Opaque`, which this test cannot tell from a non-str; that divergence is not
//! reachable from the corpus and is noted in the task report rather than worked
//! around.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};

/// `except TypeError: return value  # Fail silently if arg isn't iterable.`
fn is_type_error(error: &Error) -> bool {
    matches!(&error.kind, ErrorKind::FilterHost(host) if host.kind == PyErrorKind::TypeError)
}

/// The `AttributeError` CPython's `arg.join` raises for a separator that is not a
/// `str` — measured `join(["a", "b"], 5, autoescape=False)`. Synthesised here
/// because no [`FilterHost`] method performs the join, and because the class is
/// load-bearing: `except TypeError` does **not** catch it, so it must escape.
///
/// `operation` is `"str.join"` — the CPython operation this stands in for — and
/// **not** a `FilterHost` method's name, because none ran.
fn not_a_string(name: &str) -> Error {
    Error::filter_host(
        PyErrorKind::AttributeError,
        "str.join",
        format!("'{name}' object has no attribute 'join'"),
    )
}

/// The `TypeError` CPython's `str.join` raises for a sequence holding a non-`str`
/// — measured `join((1, 2), "-", autoescape=False)` is `TypeError: sequence item
/// 0: expected str instance, int found`. Caught by the filter → the value.
fn not_a_str_element() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "str.join",
        "sequence item: expected str instance, non-str found",
    )
}

/// A Python `str` instance, as this crate can see one.
fn as_str(value: &Value) -> Option<&str> {
    match value {
        Value::Str(text) | Value::Safe(text) => Some(&**text),
        _ => None,
    }
}

/// The join separator. With autoescape it is the host's
/// `conditional_escape(arg)`'s text; without, a bare `str` and **not** a coercion
/// — a non-str separator must raise the escaping `AttributeError`.
fn separator(arg: &Value, call: &FilterCall<'_>) -> Result<String, Error> {
    if call.autoescape {
        let escaped = call.host.conditional_escape(arg)?;
        return call.host.to_str(&escaped);
    }
    match as_str(arg) {
        Some(text) => Ok(text.to_string()),
        None => Err(not_a_string(arg.type_name())),
    }
}

/// One element's text. With autoescape it is `conditional_escape(v)`'s text (so a
/// non-str element is stringified); without, `str.join` needs an actual `str`, so
/// anything else is the caught `TypeError`.
fn element_text(value: &Value, call: &FilterCall<'_>) -> Result<String, Error> {
    if call.autoescape {
        let escaped = call.host.conditional_escape(value)?;
        return call.host.to_str(&escaped);
    }
    match as_str(value) {
        Some(text) => Ok(text.to_string()),
        None => Err(not_a_str_element()),
    }
}

/// `join`'s body.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // A missing argument is Python's `None`; `conditional_escape(None)` is
    // `"None"`, so `{{ x|join }}` joins on `"None"`.
    let arg = arg.unwrap_or(Value::None);
    // The receiver `conditional_escape(arg)` / `arg.join` is evaluated before the
    // sequence, so a bad separator fails before the value is ever iterated.
    let separator = match separator(&arg, call) {
        Ok(separator) => separator,
        // `arg` has no `.join` — `AttributeError`, not the caught `TypeError`.
        Err(error) if !is_type_error(&error) => return Err(error),
        Err(_) => return Ok(value),
    };
    let elements = match call.host.iter_values(&value) {
        Ok(elements) => elements,
        Err(error) if is_type_error(&error) => return Ok(value),
        Err(error) => return Err(error),
    };
    let mut parts = Vec::with_capacity(elements.len());
    for element in &elements {
        match element_text(element, call) {
            Ok(text) => parts.push(text),
            Err(error) if is_type_error(&error) => return Ok(value),
            Err(error) => return Err(error),
        }
    }
    // `mark_safe(data)` — in both branches.
    Ok(call
        .host
        .mark_safe(Value::Str(parts.join(&separator).into())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost, autoescape: bool) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape,
            use_tz: false,
        }
    }

    /// A host that answers the escaping branch: `conditional_escape` and `to_str`
    /// both yield `"x"`, so the body joins `"x"`-separated `"x"`s and `mark_safe`
    /// returns a recognisable marker. The **text** is not observable through a
    /// scripted `mark_safe` (it returns a fixed value, not a function of its
    /// input), so the call pattern is what these rows assert; the corpus, against
    /// the real host, is what pins the text.
    fn escaping_host(elements: Vec<Value>, marked: Value) -> TestFilterHost {
        TestFilterHost {
            conditional_escape: Some(Value::Safe("x".into())),
            to_str: Some("x".to_string()),
            iter_values: Some(elements),
            mark_safe: Some(marked),
            ..TestFilterHost::default()
        }
    }

    /// The escaping branch, whole call pattern: the **separator** is escaped
    /// (`conditional_escape` + `to_str`), then the value is iterated, then each
    /// **element** is escaped, then the answer is `mark_safe`d. One element, so
    /// `conditional_escape` is called exactly twice — a body that escaped only the
    /// elements, or only the separator, fails this.
    #[test]
    fn the_escaping_branch_escapes_the_separator_and_every_element() {
        let h = escaping_host(vec![Value::Str("a".into())], Value::Safe("MARKED".into()));
        assert_eq!(
            run(
                Value::List(vec![Value::Str("a".into())]),
                Some(Value::Str(" & ".into())),
                &call(&h, true)
            )
            .unwrap(),
            Value::Safe("MARKED".into())
        );
        assert_eq!(
            h.calls(),
            vec![
                "conditional_escape",
                "to_str",
                "iter_values",
                "conditional_escape",
                "to_str",
                "mark_safe",
            ],
            "the escaping branch's host calls, in order"
        );
    }

    /// Two elements → `conditional_escape` three times (separator + two
    /// elements), which the count guards where a fixed call list could not.
    #[test]
    fn every_element_is_escaped_not_just_the_first() {
        let h = escaping_host(
            vec![Value::Str("a".into()), Value::Str("b".into())],
            Value::Safe("MARKED".into()),
        );
        run(
            Value::List(vec![Value::Str("a".into()), Value::Str("b".into())]),
            Some(Value::Str("-".into())),
            &call(&h, true),
        )
        .unwrap();
        assert_eq!(
            h.calls()
                .iter()
                .filter(|n| **n == "conditional_escape")
                .count(),
            3,
            "separator + 2 elements: {:?}",
            h.calls()
        );
    }

    /// **The bare branch makes no escape call.** `autoescape=False` is
    /// `arg.join(value)`: no `conditional_escape`, and for `str` operands no
    /// `to_str` either — only `iter_values` and `mark_safe`. A body that escaped
    /// unconditionally fails this.
    #[test]
    fn the_bare_branch_never_escapes() {
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Str("a".into()), Value::Str("b".into())]),
            mark_safe: Some(Value::Safe("MARKED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(
                Value::List(vec![Value::Str("a".into()), Value::Str("b".into())]),
                Some(Value::Str("-".into())),
                &call(&h, false)
            )
            .unwrap(),
            Value::Safe("MARKED".into())
        );
        assert_eq!(h.calls(), vec!["iter_values", "mark_safe"]);
    }

    /// **The trap: a bad value returns the value, not `""`.** A non-iterable value
    /// raises `TypeError` from `iter_values`, and the filter gives back the
    /// *original* value — and does not `mark_safe` it.
    #[test]
    fn an_uniterable_value_is_returned_unchanged() {
        for autoescape in [true, false] {
            let h = TestFilterHost {
                conditional_escape: Some(Value::Safe("x".into())),
                to_str: Some("x".to_string()),
                ..TestFilterHost::default()
            };
            h.raise_on("iter_values", PyErrorKind::TypeError);
            let value = Value::Opaque(7);
            assert_eq!(
                run(value.clone(), Some(Value::Str("-".into())), &call(&h, autoescape)).unwrap(),
                value,
                "join(value, ...) with a non-iterable value returns value (autoescape={autoescape})"
            );
            assert!(
                !h.calls().contains(&"mark_safe"),
                "the failing branch must not mark: {:?}",
                h.calls()
            );
        }
    }

    /// In the **bare** branch a non-`str` element is the caught `TypeError`, so
    /// the value comes back (measured `join((1, 2), "-", autoescape=False)` is
    /// `(1, 2)`). A body that coerced every element through `to_str` would join
    /// and `mark_safe` here instead.
    #[test]
    fn a_non_str_element_in_the_bare_branch_returns_the_value() {
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Str("a".into()), Value::Int(1)]),
            mark_safe: Some(Value::Safe("MUST NOT BE REACHED".into())),
            ..TestFilterHost::default()
        };
        let value = Value::List(vec![Value::Str("a".into()), Value::Int(1)]);
        assert_eq!(
            run(
                value.clone(),
                Some(Value::Str("-".into())),
                &call(&h, false)
            )
            .unwrap(),
            value
        );
        assert!(
            !h.calls().contains(&"mark_safe"),
            "a non-str element must not produce a marked join: {:?}",
            h.calls()
        );
    }

    /// **A non-`str` separator in the bare branch is an `AttributeError` that
    /// escapes** — `except TypeError` does not catch it, and the value is never
    /// iterated (the receiver is evaluated first).
    #[test]
    fn a_non_str_separator_in_the_bare_branch_escapes() {
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Str("a".into())]),
            mark_safe: Some(Value::Safe("unreachable".into())),
            ..TestFilterHost::default()
        };
        let error = run(
            Value::List(vec![Value::Str("a".into())]),
            Some(Value::Int(5)),
            &call(&h, false),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::AttributeError),
            "join(list, 5, autoescape=False) must raise AttributeError: {error:?}"
        );
        assert!(
            !h.calls().contains(&"iter_values"),
            "the separator is evaluated before the value: {:?}",
            h.calls()
        );
    }

    /// A `None` separator is the same `AttributeError` in the bare branch, and is
    /// `conditional_escape(None)` = `"None"` in the escaping one (measured
    /// `join(["a", "b"], None)` is `"aNoneb"`).
    #[test]
    fn a_none_separator_differs_by_branch() {
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Str("a".into())]),
            mark_safe: Some(Value::Safe("unreachable".into())),
            ..TestFilterHost::default()
        };
        let error = run(
            Value::List(vec![Value::Str("a".into())]),
            None,
            &call(&h, false),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::AttributeError),
            "join(list, None, autoescape=False) is AttributeError: {error:?}"
        );

        let h = escaping_host(vec![Value::Str("a".into())], Value::Safe("MARKED".into()));
        assert_eq!(
            run(
                Value::List(vec![Value::Str("a".into())]),
                None,
                &call(&h, true)
            )
            .unwrap(),
            Value::Safe("MARKED".into())
        );
        h.assert_called("conditional_escape");
    }
}
