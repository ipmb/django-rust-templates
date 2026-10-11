//! `json_script` — `defaultfilters.py:85-91`, delegating to
//! `django.utils.html.json_script` (`html.py:95-112`). Rust owns the assembly; the
//! host owns `json.dumps`.
//!
//! * `call.host.json_dumps` returns the raw `json.dumps(value,
//!   cls=DjangoJSONEncoder)` text with `json`'s default separators — not escaped,
//!   not wrapped. Both halves are load-bearing (the `date`/`Decimal`/`UUID`/`set`
//!   rows below hold the encoder; the `{"a": 1}` row shows the separator).
//! * The three escapes are Rust's, [`escape_json_script`] — `str.translate`'s
//!   three entries (`html.py:88-92`). `"` is not escaped: the map has three
//!   entries and it is not one of them.
//! * The template is chosen in Rust, [`wrap_json`], on the id's Python truth via
//!   [`FilterHost::is_true`] — `if element_id:`, so a falsy `0` or `""` drops the
//!   attribute.
//! * The id's escaping is the host's, one `conditional_escape` call. `format_html`
//!   (`html.py:130-140`) is `conditional_escape` over each arg; a constant argument
//!   is `mark_safe`d by `resolve` (`base.py:800-806`), so `"a<b"` comes out
//!   unescaped where `escape` would answer `a&lt;b`. A single `{}` placeholder is
//!   `str(v)`, which is what [`FilterHost::to_str`] reads.
//! * **`mark_safe` is the body's, and needed**: `format_html` returns a
//!   `SafeString` already (`html.py:140`); `json_script` is `is_safe=True` but not
//!   `@stringfilter`, so the loop marks only when `was_safe` fired (`base.py:816`)
//!   — for a bare `dict`/`str` it does not. The corpus checks safety separately
//!   from text.
//!
//! Measured rows through `FilterExpression.resolve` on 6.0.9 — every answer is a
//! `SafeString`:
//!
//! * `{"a": 1}` → `'<script type="application/json">{"a": 1}</script>'`; the space
//!   is `json.dumps`' default separator.
//! * `{"a": "<b>&c"}` → all three escapes in one answer, and the JSON quotes are
//!   not escaped; `{"a": 'he said "hi"'}` keeps `json.dumps`' own `\"` quoting.
//! * `{"d": date(2026, 10, 8)}` → `"2026-10-08"`; `Decimal("1.50")` → `"1.50"` (a
//!   string, trailing zero kept); `UUID(int=0)` → the zero UUID; `None` → `null`;
//!   `{1, 2}` → `TypeError: Object of type set is not JSON serializable`. These
//!   are the rows that hold `cls=DjangoJSONEncoder`.
//! * `{"k": "é"}` → `ensure_ascii`, `json.dumps`' default, escapes the non-ASCII
//!   (the brief's table shows the `é` itself; measured, the encoder escapes it).
//! * The id rows pin `conditional_escape` over `escape`: `json_script:"a<b"` →
//!   `'<script id="a<b" …'` (a constant argument is `mark_safe`d), `json_script:7`
//!   → `"7"` (`str()`ed by the format), `json_script:""` and `json_script:0` →
//!   attribute dropped. The two variable-string id forms are refused by
//!   `tests/filter_cases.py`'s `arg_token`, since our side marks every `str`
//!   argument and the engines would be handed different inputs.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `_json_script_escapes` (`html.py:88-92`) as `str.translate` applies it: each
/// character independently, so the three replacements commute (none introduces a
/// `<`, `>` or `&`). `"` is absent, so the JSON's own quoting survives untouched.
fn escape_json_script(text: &str) -> String {
    text.replace('>', "\\u003E")
        .replace('<', "\\u003C")
        .replace('&', "\\u0026")
}

/// The two templates (`html.py:106-110`), chosen on `element_id`'s truth. The id
/// is the **already escaped** text; `None` is the falsy branch.
fn wrap_json(escaped: &str, element_id: Option<&str>) -> String {
    match element_id {
        Some(id) => format!("<script id=\"{id}\" type=\"application/json\">{escaped}</script>"),
        None => format!("<script type=\"application/json\">{escaped}</script>"),
    }
}

/// `json_script`'s body: `format_html(template, *args)` over
/// `json.dumps(value, cls=DjangoJSONEncoder).translate(_json_script_escapes)`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `html.py:103` — first, so a `set`'s `TypeError` escapes before the id is
    // read, which is the source's own order.
    let dumped = call.host.json_dumps(&value)?;
    let escaped = escape_json_script(&dumped);
    // The absent argument and an explicit `None` are one call: the def's default
    // is `element_id=None` and `if element_id:` asks the same question of both.
    let element_id = arg.unwrap_or(Value::None);
    let rendered = if call.host.is_true(&element_id)? {
        // `format_html`'s `conditional_escape(element_id)`; the id is unescaped
        // by `escape` iff it carries `SafeData` or `__html__`.
        let escaped_id = call.host.conditional_escape(&element_id)?;
        // `str.format`'s `{}` is `str(v)`, which is what `to_str` asks.
        let id_text = call.host.to_str(&escaped_id)?;
        wrap_json(&escaped, Some(&id_text))
    } else {
        wrap_json(&escaped, None)
    };
    // `format_html`'s own `mark_safe`; the loop's does not fire for a bare input.
    Ok(call.host.mark_safe(Value::Str(rendered.into())))
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

    /// A host scripting the dump, the id's truth and the escaping. `mark_safe`
    /// ignores its argument, so the assembled text is pinned by the pure-helper
    /// tests and the corpus; this host pins **which operations the body reached**.
    fn host(dumped: &str, id_truthy: bool, escaped_id: &str) -> TestFilterHost {
        TestFilterHost {
            json_dumps: Some(dumped.to_string()),
            is_true: Some(id_truthy),
            conditional_escape: Some(Value::Safe(escaped_id.into())),
            to_str: Some(escaped_id.to_string()),
            mark_safe: Some(Value::Safe("MARKED".into())),
            ..TestFilterHost::default()
        }
    }

    /// **Each of the three `_json_script_escapes` entries**, and the `"` that is
    /// not one of them.
    #[test]
    fn each_escape_entry_is_applied_and_the_quote_is_not() {
        assert_eq!(escape_json_script("<"), "\\u003C");
        assert_eq!(escape_json_script(">"), "\\u003E");
        assert_eq!(escape_json_script("&"), "\\u0026");
        // The measured answer: all three in one `json.dumps` string.
        assert_eq!(
            escape_json_script("{\"a\": \"<b>&c\"}"),
            "{\"a\": \"\\u003Cb\\u003E\\u0026c\"}"
        );
        // The JSON's own quoting survives, and so does a backslash.
        assert_eq!(escape_json_script("\"x\""), "\"x\"");
    }

    /// **Each template branch.** The id is spliced in as it arrives — escaping it
    /// is `conditional_escape`'s job — so only the JSON half is escaped first.
    #[test]
    fn each_template_branch_is_assembled() {
        let escaped = escape_json_script(r#""<b>&c""#);
        assert_eq!(
            wrap_json(&escaped, Some("i")),
            "<script id=\"i\" type=\"application/json\">\"\\u003Cb\\u003E\\u0026c\"</script>"
        );
        assert_eq!(
            wrap_json("1", None),
            "<script type=\"application/json\">1</script>"
        );
    }

    /// The dump is called **first**, so its refusal reaches the caller before the
    /// id is examined and is not swallowed into a default.
    #[test]
    fn a_dump_refusal_escapes_before_the_id_is_read() {
        let h = host("unused", true, "unused");
        h.raise_on("json_dumps", PyErrorKind::TypeError);
        let error = run(Value::List(vec![]), None, &call(&h)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError && e.operation == "json_dumps"),
            "a set's TypeError must escape: {error:?}"
        );
        assert_eq!(
            h.calls(),
            vec!["json_dumps"],
            "the id must not be read first"
        );
    }

    /// The **truthy** branch: `is_true`, `conditional_escape`, `to_str`, then
    /// `mark_safe`.
    #[test]
    fn the_truthy_id_branch_escapes_through_the_host() {
        let h = host(r#"[1, 2]"#, true, "a&lt;b");
        assert_eq!(
            run(
                Value::List(vec![]),
                Some(Value::Str("a<b".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Safe("MARKED".into())
        );
        assert_eq!(
            h.calls(),
            vec![
                "json_dumps",
                "is_true",
                "conditional_escape",
                "to_str",
                "mark_safe"
            ]
        );
    }

    /// The **falsy** branch: no `conditional_escape` and no `to_str`, and the id
    /// never reaches the template — the row a body that tested
    /// `element_id is not None` would get wrong for `0` and `""`.
    #[test]
    fn the_falsy_id_branch_drops_the_attribute() {
        for falsy in [Value::None, Value::Int(0), Value::Str("".into())] {
            let h = host("1", false, "unused");
            assert_eq!(
                run(Value::Int(1), Some(falsy.clone()), &call(&h)).unwrap(),
                Value::Safe("MARKED".into())
            );
            assert_eq!(
                h.calls(),
                vec!["json_dumps", "is_true", "mark_safe"],
                "a falsy `{falsy:?}` must not reach the id escaping"
            );
        }
    }

    /// The absent argument takes the same path as an explicit `None`.
    #[test]
    fn an_absent_argument_is_the_falsy_branch() {
        let h = host("1", false, "unused");
        assert_eq!(
            run(Value::Int(1), None, &call(&h)).unwrap(),
            Value::Safe("MARKED".into())
        );
        assert!(
            !h.calls().contains(&"conditional_escape"),
            "{:?}",
            h.calls()
        );
    }

    /// The body marks the result safe itself. `mark_safe` is scripted to a
    /// distinguishable value, so a plain `Value::Str` return fails here.
    #[test]
    fn the_body_marks_the_result_safe() {
        let h = host("1", false, "unused");
        let result = run(Value::Int(1), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Safe("MARKED".into()));
        assert_ne!(result, Value::Str("MARKED".into()));
        h.assert_called("mark_safe");
    }
}
