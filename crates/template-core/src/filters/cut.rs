//! `cut` — `defaultfilters.py:441-449`.
//!
//! ```python
//! @register.filter
//! @stringfilter
//! def cut(value, arg):
//!     """Remove all values of arg from the given string."""
//!     safe = isinstance(value, SafeData)
//!     value = value.replace(arg, "")
//!     if safe and arg != ";":
//!         return mark_safe(value)
//!     return value
//! ```
//!
//! `cut` is the one body in M4 that **reads the safety of the value it was
//! handed**, and that value is the one `@stringfilter` produced: Django's wrapper
//! rebinds `first = str(first)` before the body runs (`defaultfilters.py:46`), so
//! `isinstance(value, SafeData)` asks about the **coerced** value. The FFI does
//! the coercion once (§D3) and hands the body a safety-carrying `Value`, so the
//! body's `safe` is `matches!(&value, Value::Safe(_))` and **no host call is
//! needed** for it. This is the filter whose corpus rows (a plain `str`, a
//! `mark_safe`d `SafeString` and a lazy `__proxy__` whose `str()` is a
//! `SafeString`) are what will fail if that coercion ever stops replacing the
//! value the body receives.
//!
//! Four measured subtleties, and a fifth that is a correction to the plan:
//!
//! * **`arg` is not `@stringfilter`-coerced** — only `value` is. So `cut("a1b", 1)`
//!   is a `TypeError` out of `str.replace` (measured), not `"ab"`: the argument's
//!   *type* is observed, which is why it is read as a `Value` and not run through
//!   `to_str` blind.
//! * **A `None` argument is the `replace` `TypeError`, not a missing-argument
//!   error.** The FFI tells the two apart: a missing argument is `None` and the
//!   literal `None` is `Some(Value::None)`, whose *length* is the distinction
//!   (`template-py/src/filters.rs:1587-1589`). Only the second is reachable here
//!   — `{{ x|cut }}` is an arity `TemplateSyntaxError` the parser raises first,
//!   measured `cut requires 2 arguments, 1 provided` (`base.py:819`), so it never
//!   reaches a body — whereas `{{ x|cut:None }}` does and is
//!   `TypeError: replace() argument 1 must be str, not None` (measured). The
//!   message says `None`, **not** `NoneType` — `Value::None.type_name()` would
//!   put the wrong word there.
//! * **The result is `mark_safe`d only when the pre-replacement value was
//!   `SafeData` and `arg != ";"`.** The `!=` compares a **converted primitive**
//!   (`&str`), per §D1's rule, so a safe `";"` argument behaves as Python's
//!   `SafeString(";") != ";"` does — `False` — and does not flip the branch.
//! * **An empty `arg` does not raise.** The plan's Task 4 bullet says
//!   `str.replace` of an empty `arg` raises `ValueError`; measured against
//!   `defaultfilters.py` @ 6.0.9 it does not: `{{ x|cut:"" }}` returns `x`
//!   unchanged (`"abc".replace("", "")` is `"abc"`), and it is `str.split("")`
//!   that raises. The body therefore has no empty-argument special case, and
//!   `cut("abc", "")` is a pinned case below.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// The `TypeError` CPython's `str.replace` raises for an argument that is not a
/// `str` — measured as `not int`, `not bool`, `not float`, `not list`,
/// `not dict`, i.e. the argument's type name. Synthesised here rather than
/// raised across the seam because no [`FilterHost`] method performs the
/// replacement.
///
/// `operation` is `"str.replace"` — the CPython operation this stands in for —
/// and **not** a `FilterHost` method's name, because none ran: naming `"to_str"`
/// here would put a call in the diagnostic that the body never made.
///
/// [`FilterHost`]: crate::filters::host::FilterHost
fn not_a_string(name: &str) -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "str.replace",
        format!("replace() argument 1 must be str, not {name}"),
    )
}

/// `cut`'s body.
///
/// The `@stringfilter` coercion already happened, so `cut(5, "5")` is `""`; the
/// value's text comes back through
/// [`crate::filters::host::FilterHost::to_str`]. The argument is taken from its
/// `Value` directly — not through `to_str` — because `cut` does not coerce it and
/// `str.replace` requires an actual `str`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `isinstance(value, SafeData)` on the **coerced** value the wrapper produced.
    let safe = matches!(&value, Value::Safe(_));
    let text = call.host.to_str(&value)?;
    let arg_text: &str = match &arg {
        // A `SafeString` is a `str`, so it is a valid `replace` argument.
        Some(Value::Str(text)) | Some(Value::Safe(text)) => text,
        // The literal `None` arrives as `Some(Value::None)` — the FFI's length,
        // not an `Option`, is what separates it from a missing argument
        // (`template-py/src/filters.rs:1587-1589`) — and only it is reachable:
        // `{{ x|cut }}` is an arity `TemplateSyntaxError` the parser raises
        // first, measured `cut requires 2 arguments, 1 provided` (`base.py:819`),
        // so it never reaches a body, while `{{ x|cut:None }}` does and is
        // CPython's `replace` `TypeError` — measured. This branch answers the
        // reachable one. CPython's message says **`None`**, not `NoneType`, so
        // the word is spelled out: `Value::None.type_name()` would put the wrong
        // one here. The absent-argument arm is kept so that the two spellings
        // cannot answer differently if a caller ever reaches it.
        None | Some(Value::None) => return Err(not_a_string("None")),
        Some(other) => return Err(not_a_string(other.type_name())),
    };
    let replaced = text.replace(arg_text, "");
    if safe && arg_text != ";" {
        Ok(call.host.mark_safe(Value::Str(replaced.into())))
    } else {
        Ok(Value::Str(replaced.into()))
    }
}

#[cfg(test)]
mod tests {
    //! **Nothing here exercises a refusal, and the reason is a fact about the
    //! wrapper rather than an absence of one.**
    //!
    //! `run` *does* have a propagation path — `to_str(&value)?`, which
    //! [`crate::filters::host::FilterHost::to_str`] is allowed to refuse, so a
    //! refusal would escape. It is **unreachable, not absent**: `@stringfilter`
    //! pre-coerces the value before the body runs (`defaultfilters.py:46`), so
    //! the operand is already a `Value::Str` or `Value::Safe` on arrival and
    //! `to_str` cannot fail — the same fact that lets `upper`/`lower` call it as
    //! a pure accessor. **What would make this sentence false:** if `cut` ever
    //! lost its `@stringfilter` flag, that refusal becomes live and nothing here
    //! is testing it.
    //!
    //! The argument's refusal — `cut`'s only *reachable* one — is the other
    //! case, and is also not tested here: it is **synthesised in Rust**
    //! ([`not_a_string`]; no host operation performs the `str.replace`), so a
    //! test asserting its text compares the body against itself and can only
    //! agree. The doctrine is in `tests/filter_cases.py`. A synthesised
    //! message's only real check is an `ERROR_CASES` row, and `cut` has both:
    //! `{{ x|cut:1 }}` and `{{ x|cut:None }}`, pinned against **Django's**
    //! wording, its class, and the fact that it escapes rather than being
    //! swallowed into `""`. A body test beside those is a restatement to delete.
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    /// `to_str` answers with the **coerced** value's text, and `mark_safe` with
    /// the `Value::Safe` the real host would build. Both scripted, so the tests
    /// can assert which branch the body took. `mark_safe` ignores the value it is
    /// handed, so a test that *expects* a marked result scripts the replaced text.
    fn host_marking(text: &str, marked: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            mark_safe: Some(Value::Safe(marked.into())),
            ..TestFilterHost::default()
        }
    }

    /// A host whose scripted `mark_safe` answer is the input text — used by the
    /// cases that assert `mark_safe` is **not** reached, where a wrong body that
    /// did reach it fails on the value rather than on a refusal.
    fn host(text: &str) -> TestFilterHost {
        host_marking(text, text)
    }

    /// A plain `str` value is never `SafeData`, so no `mark_safe` — even with an
    /// argument that is not `";"`. The guard against reading safety off the wrong
    /// thing: a body that treated every value as safe would call `mark_safe`
    /// (scripted here to a `Safe` result) and fail.
    #[test]
    fn a_plain_string_is_not_marked_safe() {
        let h = host("a-b-c");
        assert_eq!(
            run(
                Value::Str("a-b-c".into()),
                Some(Value::Str("-".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abc".into()),
            "a plain str is not SafeData, so the result is a plain str"
        );
        assert_eq!(h.calls(), vec!["to_str"], "mark_safe must not be reached");
    }

    /// A `SafeString` value with a `";"` argument **is** safe but is still not
    /// marked, because of `arg != ";"`. This is the branch that structural
    /// equality gets wrong for a safe argument, and the one the plan names.
    #[test]
    fn a_safe_value_with_a_semicolon_argument_is_not_marked_safe() {
        let h = host("a;b");
        assert_eq!(
            run(
                Value::Safe("a;b".into()),
                Some(Value::Str(";".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("ab".into()),
            "`arg != \";\"` is False, so the result is not marked safe"
        );
        assert_eq!(h.calls(), vec!["to_str"], "mark_safe must not be reached");
    }

    /// A `SafeString` value with any other argument **is** marked. The pair with
    /// the case above is what pins the `arg != ";"` condition rather than a body
    /// that marks safe whenever the value is safe.
    #[test]
    fn a_safe_value_with_another_argument_is_marked_safe() {
        let h = host_marking("a-b", "ab");
        assert_eq!(
            run(
                Value::Safe("a-b".into()),
                Some(Value::Str("-".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Safe("ab".into()),
            "safe value, arg != \";\": mark_safe"
        );
        h.assert_called("mark_safe");
    }

    /// The value is coerced, so `cut(5, "5")` is `""` — measured. The argument is
    /// **not** coerced, and the case below it pins that.
    #[test]
    fn the_value_is_coerced_and_the_argument_is_not() {
        let h = host("5");
        assert_eq!(
            run(Value::Int(5), Some(Value::Str("5".into())), &call(&h)).unwrap(),
            Value::Str("".into()),
            "`cut(5, \"5\")` is `\"\"`"
        );
    }

    /// An empty argument is the identity, not a `ValueError` — measured, and the
    /// correction to the plan recorded in the module doc.
    #[test]
    fn an_empty_argument_is_the_identity() {
        let h = host("abc");
        assert_eq!(
            run(
                Value::Str("abc".into()),
                Some(Value::Str("".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abc".into())
        );
    }
}
