//! `escapejs` — `defaultfilters.py:78-82`.
//!
//! ```python
//! @register.filter("escapejs")
//! @stringfilter
//! def escapejs_filter(value):
//!     """Hex encode characters for use in JavaScript strings."""
//!     return escapejs(value)
//! ```
//!
//! and the `django.utils.html.escapejs` it calls (`html.py:63-85`), table and all:
//!
//! ```python
//! _js_escapes = {
//!     ord("\\"): "\\u005C", ord("'"): "\\u0027", ord('"'): "\\u0022",
//!     ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026",
//!     ord("="): "\\u003D", ord("-"): "\\u002D", ord(";"): "\\u003B",
//!     ord("`"): "\\u0060",
//!     ord(" "): "\\u2028", ord(" "): "\\u2029",
//! }
//! _js_escapes.update((ord("%c" % z), "\\u%04X" % z) for z in range(32))
//!
//! @keep_lazy(SafeString)
//! def escapejs(value):
//!     """Hex encode characters for use in JavaScript strings."""
//!     return mark_safe(str(value).translate(_js_escapes))
//! ```
//!
//! **This is the one filter in the group with no `is_safe` attribute**, so the FFI
//! loop's `base.py:816` marks nothing and the body must return the safe value
//! itself — exactly where `django.utils.html.escapejs` calls `mark_safe`. It does
//! so in Rust ([`Value::Safe`]), because the text is the table's own `String`,
//! known to be a Python `str`; the absent flag is recorded in the registry row and
//! asserted separately by `tests/test_filter_inventory.py`.
//!
//! **The table's edges are part of it.** Besides the twelve explicit entries it
//! maps every code point below `0x20` to a four-digit uppercase `\uXXXX`, so a raw
//! newline never survives. Measured on the pinned tree: `escapejs("\n")` is
//! `(SafeString, '\\u000A')`, `escapejs("\x00")` is `'\\u0000'` and
//! `escapejs("\x1f")` is `'\\u001F'` — uppercase hex, zero-padded — while
//! `escapejs("é😀")` passes both through unchanged, being above the table.
//!
//! `str(value)` is [`to_str`](crate::filters::host::FilterHost::to_str):
//! `@stringfilter` has already coerced in the FFI, but the helper stringifies
//! regardless, which is why `escapejs(5)` is `(SafeString, '5')` (measured).

use std::fmt::Write as _;

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `str(value).translate(_js_escapes)`, the twelve explicit entries plus the
/// `range(32)` control update.
fn translate(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\\' => escaped.push_str("\\u005C"),
            '\'' => escaped.push_str("\\u0027"),
            '"' => escaped.push_str("\\u0022"),
            '>' => escaped.push_str("\\u003E"),
            '<' => escaped.push_str("\\u003C"),
            '&' => escaped.push_str("\\u0026"),
            '=' => escaped.push_str("\\u003D"),
            '-' => escaped.push_str("\\u002D"),
            ';' => escaped.push_str("\\u003B"),
            '`' => escaped.push_str("\\u0060"),
            '\u{2028}' => escaped.push_str("\\u2028"),
            '\u{2029}' => escaped.push_str("\\u2029"),
            // `range(32)`, generated as `"\\u%04X" % z` — four uppercase hex
            // digits. A code point below `0x20` is one UTF-8 byte, so the output
            // is bounded by the input and no fallible reserve is needed.
            control if (control as u32) < 0x20 => {
                write!(escaped, "\\u{:04X}", control as u32)
                    .expect("writing to a String cannot fail");
            }
            other => escaped.push(other),
        }
    }
    escaped
}

/// `escapejs`'s body: `mark_safe(str(value).translate(_js_escapes))`.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let text = call.host.to_str(&value)?;
    Ok(Value::Safe(translate(&text).into()))
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

    fn host(text: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The twelve explicit entries, one measured string each. The `-`, `=`, `;`
    /// and `` ` `` mappings are the ones a reader would not guess.
    #[test]
    fn it_escapes_the_twelve_explicit_entries() {
        let cases = [
            ('\\', "\\u005C"),
            ('\'', "\\u0027"),
            ('"', "\\u0022"),
            ('>', "\\u003E"),
            ('<', "\\u003C"),
            ('&', "\\u0026"),
            ('=', "\\u003D"),
            ('-', "\\u002D"),
            (';', "\\u003B"),
            ('`', "\\u0060"),
            ('\u{2028}', "\\u2028"),
            ('\u{2029}', "\\u2029"),
        ];
        for (input, expected) in cases {
            let text = input.to_string();
            let h = host(&text);
            assert_eq!(
                run(Value::Str(text.into()), None, &call(&h)).unwrap(),
                Value::Safe(expected.into()),
                "input {input:?}"
            );
        }
    }

    /// **The table's edge a reading of the dict alone would miss**: every code
    /// point below `0x20` is escaped too, by the `range(32)` update, to four
    /// uppercase hex digits. Measured `"\n"` -> `\u000A`, `"\x00"` -> `\u0000`,
    /// `"\x1f"` -> `\u001F`.
    #[test]
    fn it_escapes_every_ascii_code_point_below_32() {
        for code in 0u32..32 {
            let text = char::from_u32(code)
                .expect("0..32 are all scalar values")
                .to_string();
            let expected = format!("\\u{code:04X}");
            let h = host(&text);
            assert_eq!(
                run(Value::Str(text.into()), None, &call(&h)).unwrap(),
                Value::Safe(expected.into()),
                "U+{code:04X}"
            );
        }
    }

    /// Text with nothing in the table comes back as the same characters — multi-
    /// byte and non-BMP included, which a byte-wise translate would split.
    #[test]
    fn untabled_text_passes_through_unchanged() {
        for text in ["plain text", "é😀", "a b", "ABC"] {
            let h = host(text);
            assert_eq!(
                run(Value::Str(text.into()), None, &call(&h)).unwrap(),
                Value::Safe(text.into()),
                "input {text:?}"
            );
        }
    }

    /// **The marker is the body's, because the flag is absent.** The host is asked
    /// only for `to_str`; a body that returned a plain `Value::Str` would lose the
    /// safety the filter exists to add.
    #[test]
    fn the_result_is_marked_safe_by_the_body_not_the_host() {
        let h = host("<");
        assert_eq!(
            run(Value::Str("<".into()), None, &call(&h)).unwrap(),
            Value::Safe("\\u003C".into())
        );
        assert_eq!(
            h.calls(),
            vec!["to_str"],
            "the flag is absent, so no host marking can happen"
        );
    }

    /// `str(value)` is reached: `escapejs(5)` is `'5'`.
    #[test]
    fn it_stringifies_a_non_str_input_through_the_host() {
        let h = host("5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Safe("5".into())
        );
        h.assert_called("to_str");
    }
}
