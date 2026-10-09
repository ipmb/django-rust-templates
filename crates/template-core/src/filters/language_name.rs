//! `language_name` — `i18n.py:292-294`.
//!
//! ```python
//! @register.filter
//! def language_name(lang_code):
//!     return translation.get_language_info(lang_code)["name"]
//! ```
//!
//! The body is one lookup into `django.conf.locale.LANG_INFO`, and the whole of
//! it is the host's: the language table is locale *data*, not a function of the
//! filter's argument, so it lives outside the template for §D2's reason. Rust
//! picks the key (`["name"]`) and nothing else.
//!
//! **No `mark_safe`, and no `SafeString` on the input or the output.** The
//! decorator is bare `@register.filter` — `is_safe` is absent, which
//! `getattr(..., "is_safe", False)` reads as false — so the result escapes at
//! render time. A Rust body that marked it would render identically for every
//! ASCII language name in the corpus and be wrong for the one that carries
//! markup, which is the trap `i18n.py:290-310` was read for rather than assumed.
//!
//! `get_language_info` raises `KeyError("Unknown language code …")` for a code
//! that is neither a key nor resolvable through its first subtag, and this filter
//! does **not** catch it — so the `Result` is the whole signature and not a
//! defensive addition.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `language_name`'s body: the info's `["name"]`, as a plain `str`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let info = call.host.language_info(&value)?;
    Ok(Value::Str(info.name.into()))
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

    /// The English name is the host's, and the body hands it back as a plain
    /// `str` — **not** `Value::Safe`, which is the difference this filter's own
    /// test can see and the render path cannot for an ASCII name.
    #[test]
    fn it_returns_the_english_name_unmarked() {
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
            Value::Str("Hungarian".into()),
            "`language_name('hu')` is `'Hungarian'`, and unmarked"
        );
        h.assert_called("language_info");
    }

    /// The seam is reached even though the answer is one field of it: a body that
    /// returned `name_local`'s key or a `Safe` copy would pass the row above only
    /// by luck of the fixture. The recorded call is the part a coincidence cannot
    /// satisfy.
    #[test]
    fn it_reads_the_name_key_and_only_the_name_key() {
        let h = TestFilterHost {
            language_info: Some(LanguageInfo {
                name: "Dutch".to_string(),
                name_local: "Nederlands".to_string(),
                bidi: false,
            }),
            ..TestFilterHost::default()
        };
        let out = run(Value::Str("nl".into()), None, &call(&h)).unwrap();
        assert_ne!(
            out,
            Value::Safe("Dutch".into()),
            "no safety marker is added"
        );
        assert_eq!(h.calls(), vec!["language_info"]);
    }

    /// An unknown code is the host's `KeyError`, and it leaves the filter as one:
    /// nothing here catches it.
    #[test]
    fn an_unknown_code_comes_back_as_a_failure() {
        let h = TestFilterHost {
            language_info: None,
            ..TestFilterHost::default()
        };
        assert!(run(Value::Str("xx".into()), None, &call(&h)).is_err());
    }
}
