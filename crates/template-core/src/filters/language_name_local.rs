//! `language_name_local` — `i18n.py:303-305`.
//!
//! ```python
//! @register.filter
//! def language_name_local(lang_code):
//!     return translation.get_language_info(lang_code)["name_local"]
//! ```
//!
//! [`language_name`](crate::filters::language_name)'s twin, one key over: the
//! same host lookup, the `["name_local"]` field instead of `["name"]`. The three
//! filters that share `get_language_info` are three rows rather than one for the
//! same reason Django writes them as three: the key is the whole of each body,
//! and a single filter parameterised by key would be a name the template cannot
//! call.
//!
//! Bare `@register.filter` again — no `is_safe`, so the local name is a plain
//! `str` that escapes at render time (`i18n.py:290-310`, measured rather than
//! assumed). `language_name_local("hu")` is `"Magyar"`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `language_name_local`'s body: the info's `["name_local"]`, as a plain `str`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let info = call.host.language_info(&value)?;
    Ok(Value::Str(info.name_local.into()))
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

    /// The local name, not the English one — the two fields differ in the fixture
    /// so a body that read the wrong key fails rather than agreeing by luck.
    #[test]
    fn it_returns_the_local_name() {
        let h = TestFilterHost {
            language_info: Some(LanguageInfo {
                name: "Hungarian".to_string(),
                name_local: "Magyar".to_string(),
                bidi: false,
            }),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::Str("hu".into()), None, &call(&h)).unwrap(),
            Value::Str("Magyar".into()),
            "`language_name_local('hu')` is `'Magyar'`"
        );
        assert_eq!(h.calls(), vec!["language_info"]);
    }
}
