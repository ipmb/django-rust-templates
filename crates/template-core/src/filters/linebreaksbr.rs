//! `linebreaksbr` — `defaultfilters.py:497-508`.
//!
//! ```python
//! @register.filter(is_safe=True, needs_autoescape=True)
//! @stringfilter
//! def linebreaksbr(value, autoescape=True):
//!     autoescape = autoescape and not isinstance(value, SafeData)
//!     value = normalize_newlines(value)
//!     if autoescape:
//!         value = escape(value)
//!     return mark_safe(value.replace("\n", "<br>"))
//! ```
//!
//! Rust throughout, with the same primitives `linebreaks` uses:
//! [`html_escape`] (Django's **unconditional** `escape`, not
//! `conditional_escape`), `normalize_newlines` (`\r\n` and `\r` → `\n`), and
//! [`Value::Safe`] for the `mark_safe` result. The order is the source's: the
//! escaping is applied to the whole normalized string, then `\n` becomes `<br>`,
//! so an escaped `<` stays escaped. The `autoescape` the body acts on is the
//! **value** `call.autoescape && !call.host.is_safe(&value)`.
//!
//! Measured (pinned 6.0.9), on the same `TEXT` as `linebreaks`:
//!
//! | case | Django |
//! |---|---|
//! | plain, autoescape on | `SafeString 'First line.<br>Second &lt;line&gt; &amp; &#x27;more&#x27;.<br><br>New paragraph here.'` |
//! | plain, `autoescape: False` | `"First line.<br>Second <line> & 'more'.<br><br>New paragraph here."` |
//! | `mark_safe(TEXT)`, autoescape on | same as `autoescape: False` |
//! | `"a\r\nb"` | `'a<br>b'` |
//! | `"a\n"` | `'a<br>'` |
//! | `"<>&''\n\""` on | `'&lt;&gt;&amp;&#x27;&#x27;<br>&quot;'` |
//!
//! ## Why any argument at all is refused
//!
//! Same collision as `linebreaks`: the second parameter is named `autoescape`
//! and Django's loop passes it as a keyword (`base.py:813`), so any positional
//! argument binds twice. Measured for `5`, `"x"`, `None`, `""`:
//!
//! ```text
//! linebreaksbr -> builtins.TypeError: linebreaksbr() got multiple values for argument 'autoescape'
//! ```
//!
//! The callable's name is the registered name here (`linebreaksbr`), because the
//! function is `def linebreaksbr(...)` — unlike `linebreaks`, whose callable is
//! `linebreaks_filter`.

use crate::filters::host::FilterCall;
use crate::filters::html_escape::html_escape;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// The `TypeError` CPython's call binding raises for the `autoescape` collision.
fn autoescape_collision() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "linebreaksbr",
        "linebreaksbr() got multiple values for argument 'autoescape'",
    )
}

/// `re_newlines.sub("\n", str(value))` with `re_newlines = \r\n|\r`.
fn normalize_newlines(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

/// `linebreaksbr`'s body: `mark_safe(escape?(normalize_newlines(value)).replace("\n", "<br>"))`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if arg.is_some() {
        return Err(autoescape_collision());
    }
    // Rust's `&&` short-circuits exactly as Python's `and` does, so `is_safe` is
    // not asked when `call.autoescape` is false.
    let autoescape = call.autoescape && !call.host.is_safe(&value);
    let text = call.host.to_str(&value)?;
    let normalized = normalize_newlines(&text);
    let body = if autoescape {
        html_escape(&normalized)
    } else {
        normalized
    };
    Ok(Value::Safe(body.replace('\n', "<br>").into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::ErrorKind;

    const TEXT: &str = "First line.\nSecond <line> & 'more'.\n\nNew paragraph here.";

    fn call(host: &TestFilterHost, autoescape: bool) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape,
            use_tz: false,
        }
    }

    fn host(text: &str, safe: bool) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            is_safe: Some(safe),
            ..TestFilterHost::default()
        }
    }

    /// Any argument is refused, and the refusal is before the seam.
    #[test]
    fn any_argument_is_refused_with_the_call_collision() {
        let h = TestFilterHost::default();
        let error = run(
            Value::Str("a\nb".into()),
            Some(Value::Str("x".into())),
            &call(&h, true),
        )
        .unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::TypeError);
                assert_eq!(host_error.operation, "linebreaksbr");
                assert_eq!(
                    host_error.message,
                    "linebreaksbr() got multiple values for argument 'autoescape'"
                );
            }
            other => panic!("an argument must raise the call collision, got {other:?}"),
        }
        assert!(h.calls().is_empty());
    }

    /// The measured `TEXT` rows, including the `SafeData` short circuit.
    #[test]
    fn it_matches_the_measured_rows() {
        let h = host(TEXT, false);
        assert_eq!(
            run(Value::Str(TEXT.into()), None, &call(&h, true)).unwrap(),
            Value::Safe(
                "First line.<br>Second &lt;line&gt; &amp; &#x27;more&#x27;.<br><br>\
                 New paragraph here."
                    .into()
            )
        );

        let h = host(TEXT, false);
        assert_eq!(
            run(Value::Str(TEXT.into()), None, &call(&h, false)).unwrap(),
            Value::Safe("First line.<br>Second <line> & 'more'.<br><br>New paragraph here.".into())
        );

        let h = host(TEXT, true);
        assert_eq!(
            run(Value::Str(TEXT.into()), None, &call(&h, true)).unwrap(),
            Value::Safe("First line.<br>Second <line> & 'more'.<br><br>New paragraph here.".into())
        );
        h.assert_called("is_safe");
    }

    /// The escape comes **before** the newline replacement, and it is the
    /// unconditional table: the apostrophe's measured spelling is `&#x27;`.
    #[test]
    fn escaping_precedes_the_newline_replacement() {
        let h = host("<>&''\n\"", false);
        assert_eq!(
            run(Value::Str("<>&''\n\"".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("&lt;&gt;&amp;&#x27;&#x27;<br>&quot;".into())
        );
    }

    /// `normalize_newlines` and the trailing-newline edge.
    #[test]
    fn it_normalizes_crlf_and_keeps_a_trailing_break() {
        let h = host("a\r\nb", false);
        assert_eq!(
            run(Value::Str("a\r\nb".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("a<br>b".into())
        );
        let h = host("a\n", false);
        assert_eq!(
            run(Value::Str("a\n".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("a<br>".into())
        );
    }
}
