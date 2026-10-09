//! `json_script` — `defaultfilters.py:85-91`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! def json_script(value, element_id=None):
//!     """Output value JSON-encoded, wrapped in a <script type="application/json">
//!     tag (with an optional id)."""
//!     return _json_script(value, element_id)
//! ```
//!
//! `_json_script` is `django.utils.html.json_script` (`html.py:95-112`), and
//! **Rust owns its assembly** — the `json.dumps` is the host's
//! ([`FilterHost::json_dumps`]) and everything after it is the body's:
//!
//! ```python
//! _json_script_escapes = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}
//!
//! json_str = json.dumps(value, cls=encoder or DjangoJSONEncoder).translate(_json_script_escapes)
//! if element_id:
//!     template = '<script id="{}" type="application/json">{}</script>'
//!     args = (element_id, mark_safe(json_str))
//! else:
//!     template = '<script type="application/json">{}</script>'
//!     args = (mark_safe(json_str),)
//! return format_html(template, *args)
//! ```
//!
//! **The four pieces, and three are the body's:**
//!
//! * `call.host.json_dumps(&value)?` returns the raw `json.dumps` text — not
//!   escaped, not wrapped. `json.dumps(value, cls=DjangoJSONEncoder)`, whole,
//!   with `json`'s own default separators; both halves are load-bearing and were
//!   both got wrong once (`ece8a3b`) — hence the `{"a": 1}` row below and the
//!   date/Decimal/UUID/set rows.
//! * **The three escapes are Rust's**, [`escape_json_script`], `str.translate`'s
//!   three entries. The JSON's own `"` quoting is **not** escaped: the map has
//!   three entries and `"` is not one of them.
//! * **The template choice is Rust's**, [`wrap_json`], on `element_id`'s Python
//!   truth via [`FilterHost::is_true`]. It is `if element_id:` and not
//!   `if element_id is not None:` — a falsy `0` or `""` drops the attribute.
//! * **The `id`'s escaping is the host's**, one call:
//!   `call.host.conditional_escape(&element_id)` (§D4). `format_html` is
//!   `mark_safe(template.format(*[conditional_escape(a) for a in args]))`
//!   (`html.py:130-140`), so the host half is exactly `conditional_escape` over
//!   the id; there is deliberately no `format_html` method on the trait, and none
//!   is needed. A **`str.format` of a single `{}` placeholder** is `str(v)`
//!   (`object.__format__` with an empty spec), which is what
//!   [`FilterHost::to_str`] is: it reads the escaped id's text, and for an
//!   `Opaque` that is Python's `str(v)`. The one shape it would miss is an
//!   object whose `__format__` disagrees with `__str__`, which `conditional_escape`
//!   could only return out of a `__html__` that returned such an object.
//! * **`mark_safe` is the body's.** `format_html` returns a `SafeString`
//!   already (`html.py:140`), so the faithful Rust equivalent is
//!   `call.host.mark_safe(Value::Str(assembled))` — and it is *needed*, not a
//!   formality: `json_script` is `is_safe=True` but **not** `@stringfilter`, so
//!   the loop marks only when `was_safe` fired (`base.py:816`), which for a bare
//!   `dict` or `str` input it does not. Measured: without the body's marking the
//!   result would be a plain `str`, and the corpus compares safety separately
//!   from text.
//!
//! Measured rows through `FilterExpression.resolve` on the pinned 6.0.9; every
//! answer is a `SafeString`:
//!
//! | row | Django |
//! |---|---|
//! | `{{ x|json_script }}`, `x = {"a": 1}` | `'<script type="application/json">{"a": 1}</script>'` — the space is `json.dumps`' default separator |
//! | `{{ x|json_script:"i" }}`, `x = [1, 2]` | `'<script id="i" type="application/json">[1, 2]</script>'` |
//! | `{{ x|json_script }}`, `x = {"a": "<b>&c"}` | `'…{"a": "<b>&c"}…'` — all three escapes in one answer; the JSON quotes are not escaped |
//! | `{{ x|json_script }}`, `x = {"a": 'he said "hi"'}` | `'…{"a": "he said \"hi\""}…'` — `json.dumps`' own quoting |
//! | `{{ x|json_script }}`, `x = {"d": date(2026, 10, 8)}` | `'…{"d": "2026-10-08"}…'` |
//! | `{{ x|json_script }}`, `x = Decimal("1.50")` | `'…"1.50"…'` — a string, trailing zero kept |
//! | `{{ x|json_script }}`, `x = UUID(int=0)` | `'…"00000000-0000-0000-0000-000000000000"…'` |
//! | `{{ x|json_script }}`, `x = None` | `'…null…'` |
//! | `{{ x|json_script }}`, `x = {"k": "é"}` | `'…{"k": "é"}…'` — `ensure_ascii`, `json.dumps`' default, escapes the non-ASCII. **The brief's table for this row shows the `é` itself; measured, the encoder escapes it.** No flag is set or needed either way: the default is the behaviour and the row is still carried, with the measured literal |
//! | `{{ x|json_script }}`, `x = {1, 2}` | `TypeError: Object of type set is not JSON serializable` |
//! | `{{ x|json_script:"a<b" }}` | `'<script id="a<b" …'` — a **constant** argument is `mark_safe`d by `resolve` (`base.py:800-806`), so `conditional_escape` leaves it alone |
//! | `{{ x|json_script:7 }}` | `'<script id="7" …'` — a non-str id is `str()`ed by the format |
//! | `{{ x|json_script:"" }}` | `'<script type="application/json">1</script>'` — a falsy id drops the attribute |
//! | `{{ x|json_script:0 }}` | the same — `if element_id:`, not `is not None` |
//!
//! **The `id` rows are what pin `conditional_escape` over `escape`.** A constant
//! argument arrives `mark_safe`d, so an id of `"a<b"` comes out **unescaped**;
//! `escape` would answer `a&lt;b`. An *unmarked* string — a bare variable — is
//! escaped instead, which is the same distinction seen from the other side. The
//! corpus can express the constant form and the non-str/falsy forms; the two
//! variable-string forms are refused by `tests/filter_cases.py`'s `arg_token`,
//! which rejects a `str` under `arg_variable` because our side marks every `str`
//! argument and the two engines would then be handed different inputs. See that
//! module.
//!
//! The date/Decimal/UUID/`set` rows are the ones that hold `json_dumps`'
//! `cls=DjangoJSONEncoder` — the defect `ece8a3b` found and no gate could see,
//! because `json_script` was a stub and that method had no other caller.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `_json_script_escapes` (`html.py:88-92`), as `str.translate` applies it: each
/// character independently, so the three replacements commute — none of them
/// introduces a `<`, `>` or `&`. `"` is deliberately absent: the map has three
/// entries, and the JSON's own quoting survives untouched.
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
    // looked at, which is the source's own order.
    let dumped = call.host.json_dumps(&value)?;
    let escaped = escape_json_script(&dumped);
    // The absent argument and the explicit `None` are one call here — the def's
    // default is `element_id=None` and `if element_id:` asks the same question of
    // both.
    let element_id = arg.unwrap_or(Value::None);
    // `if element_id:` — truth, not `is not None`.
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
    /// ignores the value it is handed, so the assembled text is pinned by the
    /// pure-helper tests below and by the corpus; this host pins **which
    /// operations the body reached**.
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

    /// **Each template branch.** The id is spliced between the quotes as it
    /// arrives — escaping it is `conditional_escape`'s job and the body's, so
    /// only the JSON half is run through [`escape_json_script`] first.
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
    /// id is examined — and the body must not swallow it into a default.
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

    /// The **truthy** branch: `is_true`, then `conditional_escape` over the id,
    /// then `to_str` of that escaped value, then `mark_safe`.
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
    /// never reaches the template. This is the row a body that tested
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

    /// The absent argument takes the same path as an explicit `None`: the def's
    /// default is `element_id=None` and `if element_id:` asks one question of
    /// both.
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
    /// distinguishable value, so a body that returned a plain `Value::Str` fails
    /// here even though the text is the corpus's to pin.
    #[test]
    fn the_body_marks_the_result_safe() {
        let h = host("1", false, "unused");
        let result = run(Value::Int(1), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Safe("MARKED".into()));
        assert_ne!(result, Value::Str("MARKED".into()));
        h.assert_called("mark_safe");
    }
}
