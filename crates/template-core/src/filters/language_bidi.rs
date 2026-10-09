//! `language_bidi` — `i18n.py:308-310`.
//!
//! ```python
//! @register.filter
//! def language_bidi(lang_code):
//!     return translation.get_language_info(lang_code)["bidi"]
//! ```
//!
//! The one of the four `language_*` filters that returns a **bool**, not a
//! string — `LANG_INFO`'s `bidi` field is `True`/`False` and the body hands it
//! straight back. A body that returned the text `"False"` would be truthy in a
//! template, which is the opposite of the answer for every LTR language; the
//! `Value::Bool` here is what keeps that visible, because `Value::Str` and
//! `Value::Bool` do not compare equal.
//!
//! Bare `@register.filter`, so there is no `is_safe` and no `mark_safe` — but the
//! flag is moot on a bool anyway: `base.py:816`'s `isinstance(obj, SafeData)` is
//! false for both operands, so neither half of the loop's marking could fire.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `language_bidi`'s body: the info's `["bidi"]`, as the bool it is.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let info = call.host.language_info(&value)?;
    Ok(Value::Bool(info.bidi))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::{LanguageInfo, TestFilterHost};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    /// `Value::Bool`, **not** the string — the distinction the test exists for.
    #[test]
    fn it_returns_the_bidi_flag_as_a_bool() {
        let h = TestFilterHost {
            language_info: Some(LanguageInfo {
                name: "Hungarian".to_string(),
                name_local: "Magyar".to_string(),
                bidi: false,
            }),
            ..TestFilterHost::default()
        };
        let out = run(Value::Str("hu".into()), None, &call(&h)).unwrap();
        assert_eq!(out, Value::Bool(false));
        assert_ne!(
            out,
            Value::Str("False".into()),
            "a stringified bool is truthy in a template"
        );
    }

    /// The `True` direction, so a body that hard-coded `false` fails: the fixture
    /// above cannot tell `bool(info.bidi)` from a constant.
    #[test]
    fn a_rtl_language_is_true() {
        let h = TestFilterHost {
            language_info: Some(LanguageInfo {
                name: "Arabic".to_string(),
                name_local: "العربية".to_string(),
                bidi: true,
            }),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("ar".into()), None, &call(&h)).unwrap(),
            Value::Bool(true)
        );
    }
}
