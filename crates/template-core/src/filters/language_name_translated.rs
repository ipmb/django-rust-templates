//! `language_name_translated` — `i18n.py:297-300`.
//!
//! ```python
//! @register.filter
//! def language_name_translated(lang_code):
//!     english_name = translation.get_language_info(lang_code)["name"]
//!     return translation.gettext(english_name)
//! ```
//!
//! Two host calls, and **neither is `get_language_info`'s own
//! `["name_translated"]`**. `get_language_info` sets that key itself, at
//! `info["name_translated"] = gettext_lazy(info["name"])` (`django/utils/translation/__init__.py:290`),
//! and reaching for it would look shorter — but it is a *different* expression:
//! it is a lazy proxy over `gettext`, and the filter
//! calls the eager `translation.gettext` on the English name. The two agree in
//! text and differ in type, and the filter's answer is the eager `str`. So the
//! body reads `["name"]` and runs it through [`FilterHost::gettext`], which is
//! what makes it the under-`translation.override("cs")` row
//! `template_tests/syntax_tests/i18n/test_filters.py` asserts: `"Hungarian
//! Magyar False maďarsky"`.
//!
//! Both halves are the host's: the language table and the active catalog are
//! locale data (§D2). Rust sequences the two calls and marks nothing — the
//! decorator is bare, so the translated name escapes at render time like the
//! other three.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `language_name_translated`'s body: `gettext` of the info's English name.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let info = call.host.language_info(&value)?;
    Ok(Value::Str(call.host.gettext(&info.name).into()))
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

    fn host(translated: &str) -> TestFilterHost {
        TestFilterHost {
            language_info: Some(LanguageInfo {
                name: "Hungarian".to_string(),
                name_local: "Magyar".to_string(),
                bidi: false,
            }),
            gettext: Some(translated.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The English name goes *through* `gettext` — the body calls the seam twice,
    /// in the source's order, and returns the second answer as a plain `str`.
    #[test]
    fn it_translates_the_english_name() {
        let h = host("maďarsky");
        assert_eq!(
            run(Value::Str("hu".into()), None, &call(&h)).unwrap(),
            Value::Str("maďarsky".into()),
            "the `cs` catalog's Hungarian (test_i18n32's second row)"
        );
        assert_eq!(h.calls(), vec!["language_info", "gettext"]);
    }

    /// **The distinction from `["name_translated"]` is the call count.** A body
    /// that read the info's own already-translated field would reach the host
    /// **once**; the source reaches it twice, and the second call is what makes
    /// this filter `translation.override`-sensitive in the way the gate row
    /// asserts. The host scripts `gettext`'s answer, so the record is the only
    /// place the second call is visible.
    #[test]
    fn it_reaches_the_host_twice_gettext_last() {
        let h = host("translated!");
        run(Value::Str("hu".into()), None, &call(&h)).unwrap();
        assert_eq!(
            h.calls(),
            vec!["language_info", "gettext"],
            "a `name_translated` shortcut would be `[\"language_info\"]`"
        );
    }

    /// The English catalog is the identity: `language_name_translated("hu")` is
    /// `"Hungarian"` when nothing overrides the active language, which is
    /// `test_i18n32`'s first row.
    #[test]
    fn under_the_english_catalog_it_is_the_english_name() {
        let h = host("Hungarian");
        assert_eq!(
            run(Value::Str("hu".into()), None, &call(&h)).unwrap(),
            Value::Str("Hungarian".into())
        );
    }
}
