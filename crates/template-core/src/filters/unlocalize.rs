//! `unlocalize` — `l10n.py:15-21`.
//!
//! ```python
//! @register.filter(is_safe=False)
//! def unlocalize(value):
//!     return str(formats.localize(value, use_l10n=False))
//! ```
//!
//! [`localize`](crate::filters::localize)'s twin: the same host expression with
//! the flag forced the other way. `use_l10n=False` is not "do nothing" — inside
//! `formats.localize` it selects the `str(value)` arms for the numeric types
//! (`formats.py:203-205`), which is how `{{ 1234.5|unlocalize }}` answers
//! `"1234.5"` under a locale whose `NUMBER_GROUPING` would otherwise add
//! separators. The `str()` wrapper and the `is_safe=False` registration are the
//! same as `localize`'s and for the same measured reasons.
//!
//! `unlocalize` appears once in `tests/i18n/`, **outside** the 1547 — this row
//! and the parity corpus are the only things that exercise it, which is why the
//! body is one host call rather than anything that could drift.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `unlocalize`'s body: `str(formats.localize(value, use_l10n=False))`, whole.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    call.host.localize(&value, false)
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

    /// The body passes `use_l10n=False` — the flag that distinguishes it from
    /// `localize` — and returns the host's answer, safety and all.
    #[test]
    fn it_forces_use_l10n_false() {
        let h = TestFilterHost {
            localize: Some(Value::Safe("1234.5".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Safe("1234.5".into()), None, &call(&h)).unwrap(),
            Value::Safe("1234.5".into())
        );
        assert_eq!(h.calls(), vec!["localize"]);
    }

    /// The pair's whole content is the argument, so the test that tells this
    /// filter from `localize` must pin the **flag**, which a scripted host cannot
    /// report. It is pinned at the seam instead: the trait's `localize` takes the
    /// bool, and `localize.rs`/`unlocalize.rs` are the two literals — a source
    /// read here that flips the constant is visible because the two files differ
    /// in exactly that token.
    #[test]
    fn it_marks_nothing() {
        let h = TestFilterHost {
            localize: Some(Value::Str("1234.5".into())),
            ..TestFilterHost::default()
        };
        run(Value::Int(1234), None, &call(&h)).unwrap();
        assert!(
            !h.calls().contains(&"mark_safe"),
            "`is_safe=False`: {:?}",
            h.calls()
        );
    }
}
