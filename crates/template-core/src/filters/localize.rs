//! `localize` — `l10n.py:7-12`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def localize(value):
//!     return str(formats.localize(value, use_l10n=True))
//! ```
//!
//! **`is_safe=False` is a registration property and not decoration** — it is what
//! stops `base.py:816` marking the result safe when the input was, so
//! `{{ price|localize }}` on a `SafeString` escapes exactly as Django does. The
//! Rust registry carries it as [`FilterFlags::NONE`](crate::filters::FilterFlags),
//! and this body must not `mark_safe` anything.
//!
//! **The `str()` is the source's, and the host does it.** The whole expression
//! crosses the seam as [`FilterHost::localize`], because `str()` here is *not*
//! `to_str`: measured on the pinned tree,
//! `str(formats.localize(mark_safe("123"), use_l10n=True))` is a `SafeString`
//! (`localize` returns a `str` unchanged — `formats.py:201-202` — and `str()` of
//! a `str` is that same object), while `to_str` would flatten it to a plain
//! `String` and the value would escape where Django does not. Everything the body
//! branches on inside `formats.localize` — the `str`/`bool`/`Decimal`/`float`/
//! `int`/`datetime`/`date`/`time` arms and the three format calls
//! (`formats.py:193-215`) — is likewise the host's.
//!
//! [`unlocalize`](crate::filters::unlocalize) is the same body with the one flag
//! flipped; the two are separate modules because they are separate registered
//! names, exactly as Django writes them.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `localize`'s body: `str(formats.localize(value, use_l10n=True))`, whole.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host.localize(&value, true)
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

    /// The body passes `use_l10n=True` and returns the host's answer unchanged —
    /// **including its safety**, which is why the fixture is a `Value::Safe`. A
    /// body that ran the answer through `to_str` and rebuilt a `Value::Str` would
    /// fail here, and that is the whole measurement behind the trait method's
    /// shape.
    #[test]
    fn it_forces_use_l10n_true_and_keeps_the_safety() {
        let h = TestFilterHost {
            localize: Some(Value::Safe("1,23".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Safe("1.23".into()), None, &call(&h)).unwrap(),
            Value::Safe("1,23".into())
        );
        assert_eq!(h.calls(), vec!["localize"]);
    }

    /// The body makes no branch of its own: a plain string in, the host's answer
    /// out, one call. `to_str`/`mark_safe` would be a Rust re-implementation of
    /// the `str()` the source wrote, which the trait method owns.
    #[test]
    fn it_adds_no_branch_of_its_own() {
        let h = TestFilterHost {
            localize: Some(Value::Str("3.14".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("3.14".into()), None, &call(&h)).unwrap(),
            Value::Str("3.14".into())
        );
        assert!(
            !h.calls().contains(&"mark_safe"),
            "`is_safe=False` means nothing is marked: {:?}",
            h.calls()
        );
    }
}
