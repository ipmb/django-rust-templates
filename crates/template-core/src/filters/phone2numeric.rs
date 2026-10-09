//! `phone2numeric` — `defaultfilters.py:984-987`, delegated whole.
//!
//! ```python
//! @register.filter("phone2numeric", is_safe=True)
//! def phone2numeric_filter(value):
//!     """Take a phone number and converts it in to its numerical equivalent."""
//!     return phone2numeric(value)
//! ```
//!
//! The helper it calls, `django.utils.text.phone2numeric`
//! (`.django/django/utils/text.py:313-344`), is a 26-entry a–z table and one
//! expression — **not** a `str.translate`:
//!
//! ```python
//! @keep_lazy_text
//! def phone2numeric(phone):
//!     """Convert a phone number with letters into its numeric equivalent."""
//!     char2number = { "a": "2", … "z": "9" }
//!     return "".join(char2number.get(c, c) for c in phone.lower())
//! ```
//!
//! **The whole helper is the host's, and this module's first version was wrong
//! to split it.** That version owned the table and delegated only the `.lower()`,
//! on the reasoning that the table is a pure function of a code point while the
//! lowercasing is Final_Sigma and therefore the host's. The table *is* pure. The
//! helper is not, because it is `@keep_lazy_text`-decorated and **the wrapped
//! function is the decorated one, not the `def` body** — so a Rust body reading
//! the `def` line is right about the table and the argument, and wrong about the
//! wrapper. Two consequences, neither expressible from inside a body that owns
//! the table:
//!
//! * a lazy argument yields a lazy *result* — measured, a `lazy(...)` proxy in
//!   gives a `__proxy__` out, which the corpus sees as a type difference even
//!   though the rendered text matches;
//! * a non-`str` is refused by `phone.lower()` **with its own class named** —
//!   `'dict' object has no attribute 'lower'` — where a Rust-side
//!   classification can only say `'object'`, because [`Value::Opaque`] cannot
//!   name its Python class and its `type_name` doc says as much.
//!
//! §D2's third clause is the rule this is the instance of: **a filter's body
//! must call the host for a helper whose `def` carries a result-changing
//! decorator, rather than reproduce it** — a body that owns the algorithm owns
//! the wrapper's behaviour too, and nothing in the `def` line or in the body
//! shows it. `phone2numeric` references nothing stateful and calls no string
//! method whose Rust and CPython answers diverge — the two axes that classified
//! it as pure — and was still not pure, because neither axis can see a decorator
//! that changes the *result*.
//!
//! **The bound is a sweep, and this module's first version of this paragraph got
//! it wrong: the count is fifteen, not fourteen, and the cut is not
//! `@stringfilter` alone.** Measured on the pinned tree: 15 helpers in
//! `django/utils/*.py` carry `@keep_lazy`/`@keep_lazy_text`, 10 of them are
//! referenced by a filter body in 23 `(filter, helper)` pairs, and **5** pairs
//! sit in bodies with no `@stringfilter` — `floatformat`, `join`, `safeseq` and
//! `unordered_list` reaching `mark_safe`, and this one. Four of those five are
//! safe because `mark_safe` is a **host method** calling Django's own decorated
//! function, so `keep_lazy` rides along; this is the only one whose body owned
//! the helper's algorithm. `tests/test_filter_inventory.py` derives the five and
//! asserts a verdict for each, so the class is closed at one by measurement
//! rather than by the claim that every other filter has `str(first)` run first.
//!
//! No `@stringfilter` — it is one of the four the 29 exclude (see
//! `tests/test_filter_inventory.py`'s `STRINGFILTER`), which is the other half
//! of why the [`Value`] goes in uncoerced: `{{ x|phone2numeric }}` over an `int`
//! must raise, not answer `str(value)`.
//!
//! Measured rows, through `FilterExpression.resolve` on the pinned tree:
//!
//! ```text
//! '800-COLLECT'            -> '800-2655328'
//! '1-800-FLOWERS'          -> '1-800-3569377'
//! 'aBc'                    -> '222'
//! 'abcXYZ'                 -> '222999'
//! 'a-b_c'                  -> '2-2_2'      (punctuation is not in the table)
//! '123-456'                -> '123-456'
//! ''                       -> ''
//! 'éè'                     -> 'éè'         (not a–z, so not mapped)
//! 'ΟΣ'                     -> 'ος'         (CPython's Final_Sigma)
//! "꟎"                 -> "꟎"     (Rust maps this code point; CPython does not)
//! mark_safe('800-COLLECT') -> '800-2655328'   a plain str — the marker does NOT survive
//! lazy(...)                -> __proxy__       the decorator, visible as a type
//! 5 / True / 5.0 / None / [1] / {'a': 1} / (1, 2)
//!     -> AttributeError: '<its own class>' object has no attribute 'lower'
//! ```
//!
//! `tests/filter_cases.py` carries the first, tenth, eleventh, twelfth and last
//! of those; the two lazy rows are the ones that failed for the split version.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `phone2numeric`'s body: the helper, whole, and nothing else.
///
/// Nothing is decided here — not the table, not the lowercasing, not the
/// refusal's class name — because the decorator behind the seam observes the
/// argument's *type* and its *laziness*, and a body that recognised either would
/// be reconstructing the wrapper from the `def` line. The [`Value`] is passed
/// through as it is.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host.phone2numeric(&value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::ErrorKind;
    use crate::parser::error::PyErrorKind;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// A host whose one scripted answer is `answer`.
    fn answering(answer: Value) -> TestFilterHost {
        TestFilterHost {
            phone2numeric: Some(answer),
            ..TestFilterHost::default()
        }
    }

    /// The body is the seam call and nothing else: the host's answer comes back
    /// unmodified. The table that produced it lives in `text.py`, and a body
    /// that still held its own would have to be asked for the answer rather than
    /// given it — which is what `assert_called` catches.
    #[test]
    fn it_returns_the_hosts_answer() {
        let h = answering(Value::Str("800-2655328".into()));
        assert_eq!(
            run(Value::Str("800-COLLECT".into()), None, &call(&h)).unwrap(),
            Value::Str("800-2655328".into())
        );
        h.assert_called("phone2numeric");
    }

    /// The input goes in **as it is** — not `to_str`d, not lowered, not
    /// classified — because every one of those is something the decorator
    /// observes. `str_lower` and `to_str` are not scripted, so reaching either
    /// would panic rather than pass quietly.
    #[test]
    fn the_value_reaches_the_host_uncoerced() {
        let h = answering(Value::Str("222".into()));
        assert_eq!(
            run(Value::Safe("aBc".into()), None, &call(&h)).unwrap(),
            Value::Str("222".into())
        );
        assert_eq!(h.calls(), vec!["phone2numeric"], "no other seam call");
    }

    /// The answer is the host's **including its marking**: `@keep_lazy_text`
    /// does not preserve a `SafeString`, so a host answering with a plain `str`
    /// must not have it `mark_safe`d on the way out. Measured on the pinned
    /// tree — `mark_safe('800-COLLECT')` answers a plain `str`.
    #[test]
    fn the_hosts_marking_is_the_answer() {
        let h = answering(Value::Str("800-2655328".into()));
        assert_eq!(
            run(Value::Safe("800-COLLECT".into()), None, &call(&h)).unwrap(),
            Value::Str("800-2655328".into())
        );
    }

    /// **The row the split version could not produce.** A lazy answer stays a
    /// distinct `Value`; the body does not flatten it, and cannot — the
    /// `__proxy__` is the decorator's doing and it is opaque to Rust on purpose.
    #[test]
    fn a_lazy_answer_is_not_flattened() {
        let h = answering(Value::Opaque(1));
        assert_eq!(
            run(Value::Str("aBc".into()), None, &call(&h)).unwrap(),
            Value::Opaque(1)
        );
    }

    /// **The other row it could not produce**: a refusal arrives from the host,
    /// carrying the class name the host's own Python produced — `'dict'` for a
    /// `dict`, which a Rust classification of the `Value` cannot say. The body
    /// synthesises nothing, so the only thing to assert here is that it passed
    /// the host's error through untouched.
    #[test]
    fn a_host_refusal_propagates() {
        let h = TestFilterHost::default();
        h.raise_on("phone2numeric", PyErrorKind::AttributeError);
        let error = run(Value::Int(5), None, &call(&h)).expect_err("must refuse");
        match &error.kind {
            ErrorKind::FilterHost(host) => {
                assert_eq!(host.operation, "phone2numeric");
                assert_eq!(host.kind, PyErrorKind::AttributeError);
            }
            other => panic!("not a FilterHost error: {other:?}"),
        }
    }
}
