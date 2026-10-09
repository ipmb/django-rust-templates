//! `linebreaks` — `defaultfilters.py:485-494`.
//!
//! ```python
//! @register.filter("linebreaks", is_safe=True, needs_autoescape=True)
//! @stringfilter
//! def linebreaks_filter(value, autoescape=True):
//!     autoescape = autoescape and not isinstance(value, SafeData)
//!     return mark_safe(linebreaks(value, autoescape))
//! ```
//!
//! The inner helper is `django.utils.html.linebreaks` (`html.py:169-178`) and it
//! is **Rust's** — there is no `FilterHost::linebreaks`:
//!
//! ```python
//! def linebreaks(value, autoescape=False):
//!     value = normalize_newlines(value)
//!     paras = re.split("\n{2,}", str(value))
//!     if autoescape:
//!         paras = ["<p>%s</p>" % escape(p).replace("\n", "<br>") for p in paras]
//!     else:
//!         paras = ["<p>%s</p>" % p.replace("\n", "<br>") for p in paras]
//!     return "\n\n".join(paras)
//! ```
//!
//! * `normalize_newlines` is `django.utils.text.normalize_newlines`
//!   (`text.py:308-310`): `re_newlines.sub("\n", str(text))` with
//!   `re_newlines = \r\n|\r` (`text.py:36`), so `\r\n` and a lone `\r` become
//!   `\n` **before** the `\n{2,}` split.
//! * The escaping primitive is Django's **unconditional** `escape` =
//!   `SafeString(html.escape(str(text)))` (`html.py:51-60`), which is Rust's
//!   [`html_escape`] — **not** `conditional_escape`, which is unreachable here
//!   (`@stringfilter`'s `str(first)` coercion is what makes it so). Escape
//!   **before** replacing `\n` with `<br>`.
//! * The `autoescape` the helper sees is the **value**
//!   `call.autoescape && !call.host.is_safe(&value)`, and the `SafeData` test is
//!   on the coerced value a `@stringfilter` body receives.
//! * `mark_safe(...)` → [`Value::Safe`].
//!
//! Measured on `TEXT = "First line.\nSecond <line> & 'more'.\n\nNew paragraph here."`
//! (pinned 6.0.9):
//!
//! | case | Django |
//! |---|---|
//! | plain, autoescape on | `SafeString '<p>First line.<br>Second &lt;line&gt; &amp; &#x27;more&#x27;.</p>\n\n<p>New paragraph here.</p>'` |
//! | plain, `autoescape: False` | `SafeString "<p>First line.<br>Second <line> & 'more'.</p>\n\n<p>New paragraph here.</p>"` |
//! | `mark_safe(TEXT)`, autoescape on | same as `autoescape: False` |
//! | `"a\r\nb\rc\n\nd"` | `'<p>a<br>b<br>c</p>\n\n<p>d</p>'` |
//! | `"a\n\n\nb"` | `'<p>a</p>\n\n<p>b</p>'` |
//! | `"a\n"` | `'<p>a<br></p>'` |
//! | `"\n\n"` | `'<p></p>\n\n<p></p>'` |
//! | `""` | `'<p></p>'` |
//! | `"just one line"` | `'<p>just one line</p>'` |
//!
//! ## Why any argument at all is refused
//!
//! This filter's own second parameter is *named* `autoescape`, and Django's loop
//! calls `func(obj, autoescape=context.autoescape, *arg_vals)` (`base.py:813`) —
//! keyword before positional — so **any** argument binds twice and raises at call
//! time. `args_check` (`base.py:822`) counts `autoescape` among the parameters,
//! so the parse accepts the token; the failure is CPython's binding of the call.
//! Measured for all four shapes tried (`5`, `"x"`, `None`, `""`):
//!
//! ```text
//! linebreaks -> builtins.TypeError: linebreaks_filter() got multiple values for argument 'autoescape'
//! ```
//!
//! Our engine takes `autoescape` as a *value*, so there is no keyword to collide
//! with and the argument would otherwise be silently ignored — answering where
//! Django raises, which this project treats as a defect. The body refuses instead
//! and hardcodes the callable's name, which is `linebreaks_filter` and **not** the
//! registered `linebreaks`.

use crate::filters::host::FilterCall;
use crate::filters::html_escape::html_escape;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// The `TypeError` CPython's call binding raises when a positional argument
/// collides with the `autoescape` keyword. The operation names the callable,
/// which is the filter's Python function name and not its registered name.
fn autoescape_collision() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "linebreaks_filter",
        "linebreaks_filter() got multiple values for argument 'autoescape'",
    )
}

/// `re_newlines.sub("\n", str(text))` with `re_newlines = \r\n|\r`: CRLF first,
/// then a lone CR, so `\r\n` becomes one `\n` rather than two.
fn normalize_newlines(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

/// `re.split("\n{2,}", text)`: split on each **run** of two or more newlines,
/// consuming the whole run. A plain `split("\n\n")` would leave the extra
/// newlines on the following paragraph and split `"a\n\n\nb"` into
/// `["a", "\nb"]` instead of `["a", "b"]`.
fn split_paragraphs(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut paragraphs = Vec::new();
    let mut start = 0;
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'\n' {
            i += 1;
            continue;
        }
        let run_start = i;
        while i < bytes.len() && bytes[i] == b'\n' {
            i += 1;
        }
        if i - run_start >= 2 {
            paragraphs.push(&text[start..run_start]);
            start = i;
        }
    }
    paragraphs.push(&text[start..]);
    paragraphs
}

/// `linebreaks`'s body: `mark_safe(linebreaks(value, autoescape))`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if arg.is_some() {
        return Err(autoescape_collision());
    }
    // `autoescape and not isinstance(value, SafeData)`: Rust's `&&` short-circuits
    // exactly as Python's does, so `is_safe` is not asked when `call.autoescape` is
    // false — which is Django's own evaluation order.
    let autoescape = call.autoescape && !call.host.is_safe(&value);
    let text = call.host.to_str(&value)?;
    let normalized = normalize_newlines(&text);
    let paragraphs = split_paragraphs(&normalized);
    let mut rendered = Vec::with_capacity(paragraphs.len());
    for paragraph in paragraphs {
        // `escape(p).replace("\n", "<br>")`: the escape comes **first**, so a
        // `<` cannot survive inside the paragraph.
        let body = if autoescape {
            html_escape(paragraph).replace('\n', "<br>")
        } else {
            paragraph.replace('\n', "<br>")
        };
        rendered.push(format!("<p>{body}</p>"));
    }
    Ok(Value::Safe(rendered.join("\n\n").into()))
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

    /// Any argument is refused, with Django's callable name and message, and the
    /// refusal happens **before** the seam — so a bare host suffices and the test
    /// also pins that no host call is made.
    #[test]
    fn any_argument_is_refused_with_the_call_collision() {
        let h = TestFilterHost::default();
        let error = run(
            Value::Str("a\nb".into()),
            Some(Value::Int(5)),
            &call(&h, true),
        )
        .unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::TypeError);
                assert_eq!(host_error.operation, "linebreaks_filter");
                assert_eq!(
                    host_error.message,
                    "linebreaks_filter() got multiple values for argument 'autoescape'"
                );
            }
            other => panic!("an argument must raise the call collision, got {other:?}"),
        }
        assert!(
            h.calls().is_empty(),
            "the refusal is a call-binding fact, not a body that reached the host"
        );
    }

    /// The measured `TEXT` rows: escaping on, off, and the `SafeData` short
    /// circuit that makes a safe input behave like `autoescape: False`.
    #[test]
    fn it_matches_the_measured_rows() {
        let h = host(TEXT, false);
        assert_eq!(
            run(Value::Str(TEXT.into()), None, &call(&h, true)).unwrap(),
            Value::Safe(
                "<p>First line.<br>Second &lt;line&gt; &amp; &#x27;more&#x27;.</p>\n\n\
                 <p>New paragraph here.</p>"
                    .into()
            )
        );

        let h = host(TEXT, false);
        assert_eq!(
            run(Value::Str(TEXT.into()), None, &call(&h, false)).unwrap(),
            Value::Safe(
                "<p>First line.<br>Second <line> & 'more'.</p>\n\n\
                 <p>New paragraph here.</p>"
                    .into()
            )
        );

        // `SafeData` input with autoescape on is the same as autoescape off.
        let h = host(TEXT, true);
        assert_eq!(
            run(Value::Str(TEXT.into()), None, &call(&h, true)).unwrap(),
            Value::Safe(
                "<p>First line.<br>Second <line> & 'more'.</p>\n\n\
                 <p>New paragraph here.</p>"
                    .into()
            )
        );
        h.assert_called("is_safe");
    }

    /// **The `\n{2,}` run split.** `"a\n\n\nb"` is one paragraph break, not a
    /// break plus a leading newline; a `split("\n\n")` body would answer
    /// `'<p>a</p>\n\n<p><br>b</p>'`.
    #[test]
    fn a_run_of_newlines_is_one_paragraph_break() {
        let h = host("a\n\n\nb", false);
        assert_eq!(
            run(Value::Str("a\n\n\nb".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("<p>a</p>\n\n<p>b</p>".into())
        );
    }

    /// `normalize_newlines` runs **before** the split, so `\r\n` and a lone `\r`
    /// are single paragraph-internal newlines.
    #[test]
    fn newlines_are_normalized_before_the_split() {
        let h = host("a\r\nb\rc\n\nd", false);
        assert_eq!(
            run(Value::Str("a\r\nb\rc\n\nd".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("<p>a<br>b<br>c</p>\n\n<p>d</p>".into())
        );
    }

    /// The edge rows from the table: a trailing newline, an all-newline input and
    /// the empty string each keep their paragraph count.
    #[test]
    fn the_edge_rows_keep_their_paragraph_count() {
        for (input, expected) in [
            ("a\n", "<p>a<br></p>"),
            ("\n\n", "<p></p>\n\n<p></p>"),
            ("", "<p></p>"),
            ("just one line", "<p>just one line</p>"),
        ] {
            let h = host(input, false);
            assert_eq!(
                run(Value::Str(input.into()), None, &call(&h, true)).unwrap(),
                Value::Safe(expected.into()),
                "linebreaks({input:?})"
            );
        }
    }

    /// The escape is applied to each paragraph **before** `\n` becomes `<br>`, so
    /// a `<` cannot survive — the same input under `autoescape: False` keeps it.
    #[test]
    fn escaping_is_unconditional_when_it_runs() {
        let h = host("<a>\n<b>", false);
        assert_eq!(
            run(Value::Str("<a>\n<b>".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("<p>&lt;a&gt;<br>&lt;b&gt;</p>".into())
        );
        let h = host("<a>\n<b>", false);
        assert_eq!(
            run(Value::Str("<a>\n<b>".into()), None, &call(&h, false)).unwrap(),
            Value::Safe("<p><a><br><b></p>".into())
        );
    }
}
