//! `striptags` — `defaultfilters.py:528-532`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def striptags(value):
//!     """Strip all [X]HTML tags."""
//!     return strip_tags(value)
//! ```
//!
//! The whole body is [`FilterHost::strip_tags`], which is Django's
//! `HTMLParser`-based `django.utils.html.strip_tags` (`html.py:210-229`): the tag
//! grammar, the entity handling and the malformed-input behaviour are all
//! Python's (§D2). `@stringfilter` has already coerced the **value**, so the text
//! is read back through [`FilterHost::to_str`].
//!
//! **The body returns a plain `Value::Str`.** `is_safe=True` plus a `SafeData`
//! input makes the FFI loop mark the result (`base.py:816`), so the marking is the
//! loop's and never this body's. Measured on the pinned 6.0.9:
//! `mark_safe("<b>hi</b>")|striptags` is `(SafeString, 'hi')` while a plain input
//! gives `(str, 'hi there')`.
//!
//! **Entities are re-emitted verbatim, not decoded** — the stripper is fed with
//! `convert_charrefs=False` and rebuilds `&name;` / `&#name;` by hand
//! (`html.py:181-197`). Measured, `"a &amp; b &lt;c&gt; &#65;"` is
//! `'a &amp; b &lt;c&gt; &#65;'` — identical text.
//!
//! **The depth guard raises, and the raise is the filter's answer.** `strip_tags`
//! is `Result` rather than `String` because `MAX_STRIP_TAGS_DEPTH = 50`
//! (`html.py:44`) has **two** guards and both raise a bare
//! `django.core.exceptions.SuspiciousOperation` (empty message): the
//! `long_open_tag_without_closing_re` scan (`:47`, `<[a-zA-Z][^>]{1000,}` with
//! `>= 50` `<`) and the loop that counts `_strip_once` iterations (`:220-222`,
//! needing both `<` and `>` present). The body `?`s the seam call so that raise
//! escapes rather than becoming an answer.
//!
//! Measured rows (pinned 6.0.9):
//!
//! | input | Django |
//! |---|---|
//! | `"<b>hi</b> <i>there</i>"` | `'hi there'` |
//! | `"a &amp; b &lt;c&gt; &#65;"` | `'a &amp; b &lt;c&gt; &#65;'` |
//! | `"a <!-- c --> <b>x</b>"` | `'a  x'` |
//! | `"a <b b"` | `'a <b b'` (no `>`, so the loop never runs) |
//! | `"<div><span><b>x</b></span></div>"` | `'x'` |
//! | `"<d>"*50 + "x" + "</d>"*50` | `'x'` (one `_strip_once` removes them all) |
//! | `"<"*51 + "a>"*51` | raises `SuspiciousOperation` |
//! | `">" + "<a"*501` | raises `SuspiciousOperation` |
//! | `"<a"*50 + "a"*950` | raises `SuspiciousOperation` |
//!
//! **`SuspiciousOperation` crosses the FFI as itself.** The real host's
//! `strip_tags` is `utility_text_checked` → `host_error`, which keeps the live
//! `PyErr`, and `run_filter`'s `report` returns that object rather than anything
//! rebuilt from the class name
//! (`crates/template-py/src/filters.rs:854`, `:248`, `:1528`). So a corpus row
//! **can** pin it as a parity row — `raises: "SuspiciousOperation"`,
//! `message: ""` — rather than a documented divergence: the exception's class
//! name is `SuspiciousOperation` on both sides. (A Rust-synthesised class name
//! would have gone through `rebuilt_builtin`, found no `builtins.SuspiciousOperation`
//! and degraded to `builtins.Exception`; that path is not taken here.)

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `striptags`'s body: `strip_tags(str(value))`, `?`-ing the depth guard's raise.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let text = call.host.to_str(&value)?;
    Ok(Value::Str(call.host.strip_tags(&text)?.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::{ErrorKind, PyErrorKind};

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: false,
        }
    }

    /// Scripts both seam calls the body makes: the `to_str` coercion read and the
    /// stripper's answer. `text` is what `strip_tags` returns here, so a test's
    /// expected value is the scripted one.
    fn host(text: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            strip_tags: Some(text.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The body returns exactly what the host's stripper answered, as a plain
    /// `str` — the safe-marking of a `SafeData` input is the FFI loop's.
    #[test]
    fn it_returns_the_hosts_stripped_text() {
        let h = host("hi there");
        assert_eq!(
            run(Value::Str("<b>hi</b> <i>there</i>".into()), None, &call(&h)).unwrap(),
            Value::Str("hi there".into())
        );
        h.assert_called("strip_tags");
    }

    /// The text is read through the seam, so a value the loop coerced from a
    /// non-string is still the string `strip_tags` is asked about.
    #[test]
    fn the_text_is_read_through_the_host() {
        let h = host("5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        h.assert_called("to_str");
        h.assert_called("strip_tags");
    }

    /// **The depth guard's raise escapes.** `strip_tags` is the one `Result`
    /// operation here whose failure is the filter's *answer*, so a body that
    /// swallowed it would answer where Django raises. The class is
    /// `SuspiciousOperation`, which no other check can reach from a bare shell.
    #[test]
    fn the_depth_guards_raise_escapes() {
        let h = host("unused");
        h.raise_on(
            "strip_tags",
            PyErrorKind::Other("SuspiciousOperation".into()),
        );
        let error = run(Value::Str("<a>".into()), None, &call(&h)).unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(
                    host_error.kind,
                    PyErrorKind::Other("SuspiciousOperation".into())
                );
                assert_eq!(host_error.operation, "strip_tags");
            }
            other => panic!("the depth guard must reach the caller, got {other:?}"),
        }
    }
}
