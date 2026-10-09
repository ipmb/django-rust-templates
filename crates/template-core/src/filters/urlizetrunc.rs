//! `urlizetrunc` — `defaultfilters.py:389-400`.
//!
//! ```python
//! @register.filter(is_safe=True, needs_autoescape=True)
//! @stringfilter
//! def urlizetrunc(value, limit, autoescape=True):
//!     return mark_safe(
//!         _urlize(value, trim_url_limit=int(limit), nofollow=True, autoescape=autoescape)
//!     )
//! ```
//!
//! The same host helper `urlize` uses ([`FilterHost::urlize`]) with a limit, so
//! the regex, the `settings.URLIZE_ASSUME_HTTPS` branch and the escaping are the
//! host's — see `urlize.rs` for why the helper is delegated whole.
//!
//! **The body's decisions, in order:**
//!
//! 1. `call.host.to_int(&arg.unwrap_or(Value::None))` — `defaultfilters.py:399`'s
//!    `int(limit)`, **first and with no `try`** (the `wordwrap` pattern,
//!    `wordwrap.rs:73`), so its `ValueError` for a non-numeric argument and its
//!    `TypeError` for `None` escape before the text is read.
//!    `unwrap_or(Value::None)` rather than an early return, because
//!    `implemented_filters()` in `tests/test_filter_parity.py` calls *every*
//!    filter with `_native.filter(name, None, ())` to measure the stub set — a
//!    body that panicked on the absent argument would break that gate. The absent
//!    token is unreachable from a template anyway: `{{ x|urlizetrunc }}` is a
//!    `TemplateSyntaxError: urlizetrunc requires 2 arguments, 1 provided`, raised
//!    by `args_check` (`base.py:822`) before any body runs.
//! 2. `call.host.to_str(&value)` — the `@stringfilter` accessor, as in `urlize`.
//! 3. `call.host.urlize(&text, Some(&limit), autoescape)` — the int reaches the
//!    seam as it is. `IntValue::Small` and `IntValue::Big` are both handed
//!    through, and **nothing in Rust decides "too big"**: `trim_url` compares and
//!    slices with the int, so a 30-digit literal is simply longer than any URL.
//!    The `autoescape` is `call.autoescape && !call.host.is_safe(&value)`, the
//!    same collapse [`urlize`](crate::filters::urlize) makes and for the same
//!    reason — `Urlizer.__call__`'s `safe_input = isinstance(text, SafeData)`
//!    (`html.py:319`); see that module's "The `safe_input` collapse".
//! 4. `call.host.mark_safe(Value::Str(rendered))` — `defaultfilters.py:398`'s own
//!    `mark_safe`.
//!
//! **`int(limit)` is the body's, not the seam's.** Django does it at
//! `defaultfilters.py:399`, before the helper call, so [`FilterHost::to_int`] is
//! where `int("abc")`'s `ValueError` and `int(None)`'s `TypeError` come from and
//! the seam is handed an int or `None` only. `None` is `urlize`'s own call site
//! (`defaultfilters.py:386`), not a spelling of `0`.
//!
//! Measured rows on `"see https://example.com/very/long/path now"`; every answer
//! is a `SafeString` and wraps the link as
//! `<a href="https://example.com/very/long/path" rel="nofollow">…</a>`:
//!
//! | token | Django |
//! |---|---|
//! | `{{ x|urlizetrunc:5 }}` | `'see …>http…</a> now'` |
//! | `{{ x|urlizetrunc:"5" }}` | the same — `int("5")` succeeds |
//! | `{{ x|urlizetrunc:n }}`, `n = 5` | the same — a variable argument |
//! | `{{ x|urlizetrunc:0 }}` | `'…'` — `x[: max(0, -1)]` is empty |
//! | `{{ x|urlizetrunc:-1 }}` | `'…'` — the row the `usize` seam could not carry |
//! | `{{ x|urlizetrunc:-5 }}` | `'…'` |
//! | `{{ x|urlizetrunc:1 }}` | `'…'` |
//! | `{{ x|urlizetrunc:3 }}` | `'ht…'` |
//! | `{{ x|urlizetrunc:1000 }}` | untruncated: the whole URL, no ellipsis |
//! | `{{ x|urlizetrunc:<10**29> }}` | untruncated (`len(x) <= limit` holds) |
//! | `{{ x|urlizetrunc:None }}` | `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` |
//! | `{{ x|urlizetrunc:"abc" }}` | `ValueError: invalid literal for int() with base 10: 'abc'` |
//!
//! **`trim_url` is the host's, and its shape is why the seam changed.** It is
//! `if limit is None or len(x) <= limit: return x`, then
//! `"%s…" % x[: max(0, limit - 1)]` (`html.py:407-410`) — the limit is compared
//! *and sliced*, both of which take any Python int. That is why `-1` is `'…'` and
//! a 30-digit literal truncates nothing, and why the seam's `limit` is
//! `Option<&IntValue>` rather than the `Option<usize>` it carried until wave B: a
//! `usize` cannot hold the negatives, and the untruncating literal would have to
//! saturate rather than arrive. See [`FilterHost::urlize`] and the sentinel rule
//! (`date_format`/`time_format`, `c4027d0`).
//!
//! **No collision, and that is the contrast `urlize` is the other half of.**
//! `limit` comes *before* `autoescape` in the signature, so
//! `func(obj, autoescape=…, limit)` binds the positional argument to `limit` and
//! nothing collides. This filter is one of the two three-positional-parameter
//! filters in the set (`join` is the other).
//!
//! **The safe-input collapse is shared with `urlize`, and measured on this
//! filter too:** `mark_safe("<b>bold</b> https://e.com")|urlizetrunc:5` is
//! `SafeString('<b>bold</b> <a href="https://e.com" rel="nofollow">http…</a>')`
//! — the surrounding text unescaped, the same text a plain `str` input would
//! have escaped. The collapse is what makes the seam's flag say so.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `urlizetrunc`'s body:
/// `mark_safe(_urlize(str(value), trim_url_limit=int(limit), nofollow=True, autoescape))`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `int(limit)` (`defaultfilters.py:399`) first and with no `try`, so its
    // refusal escapes before the text is read. `unwrap_or` keeps the absent
    // argument on the same path rather than panicking — see the module doc.
    let limit = call.host.to_int(&arg.unwrap_or(Value::None))?;
    // `autoescape and not isinstance(value, SafeData)` — the `linebreaks`
    // collapse, shared with `urlize` (see that module's doc).
    let autoescape = call.autoescape && !call.host.is_safe(&value);
    // `defaultfilters.py:46`'s `str(value)`, done once by the FFI loop (§D3).
    let text = call.host.to_str(&value)?;
    // The int reaches the seam unread: `trim_url` compares and slices with it.
    let rendered = call.host.urlize(&text, Some(&limit), autoescape);
    // `defaultfilters.py:398`'s own `mark_safe`.
    Ok(call.host.mark_safe(Value::Str(rendered.into())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::{ErrorKind, PyErrorKind};
    use crate::pynum::IntValue;

    fn call(host: &TestFilterHost, autoescape: bool) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape,
            use_tz: false,
        }
    }

    fn host(limit: IntValue, text: &str, rendered: &str) -> TestFilterHost {
        TestFilterHost {
            to_int: Some(limit),
            // The body asks it for the seam's `autoescape` (the collapse shared
            // with `urlize`); `TestFilterHost::is_safe` panics when unset.
            is_safe: Some(false),
            to_str: Some(text.to_string()),
            urlize: Some(rendered.to_string()),
            mark_safe: Some(Value::Safe(rendered.into())),
            ..TestFilterHost::default()
        }
    }

    /// The whole body delegates: `to_int`, `is_safe`, `to_str`, `urlize`,
    /// `mark_safe`, in that order. `TestFilterHost::urlize` ignores the limit and
    /// the flag, so the *value* of the truncation and of `autoescape` are the
    /// corpus's to pin; what this pins is that the body makes the seam call
    /// rather than truncating in Rust.
    #[test]
    fn it_delegates_then_marks_safe() {
        let h = host(IntValue::Small(5), "see https://e.com/x now", "RENDERED");
        assert_eq!(
            run(
                Value::Str("see https://e.com/x now".into()),
                Some(Value::Str("5".into())),
                &call(&h, true)
            )
            .unwrap(),
            Value::Safe("RENDERED".into())
        );
        assert_eq!(
            h.calls(),
            vec!["to_int", "is_safe", "to_str", "urlize", "mark_safe"]
        );
    }

    /// `int(limit)` runs **before** the text is read, and its `ValueError`
    /// escapes — there is no `try`. A body that read the text first would have
    /// made a host call before the refusal, which this pins.
    #[test]
    fn a_non_integer_limit_escapes_before_the_text_is_read() {
        let h = host(IntValue::Small(5), "ignored", "ignored");
        h.raise_on("to_int", PyErrorKind::ValueError);
        let error = run(
            Value::Str("x".into()),
            Some(Value::Str("abc".into())),
            &call(&h, true),
        )
        .unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::ValueError && e.operation == "to_int"),
            "int('abc') must escape: {error:?}"
        );
        assert_eq!(h.calls(), vec!["to_int"], "the text must not be read first");
    }

    /// The **absent** argument reaches the conversion rather than panicking: the
    /// body's first call is `to_int(Value::None)`, the same path `{{ x|f:None }}`
    /// takes. The arity check makes this input unreachable from a template, but
    /// `implemented_filters()` in `tests/test_filter_parity.py` calls every filter
    /// this way, so a panic here would break the gate.
    #[test]
    fn an_absent_argument_reaches_the_conversion() {
        let h = host(IntValue::Small(5), "x", "RENDERED");
        assert_eq!(
            run(Value::Str("x".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("RENDERED".into())
        );
        assert!(h.calls().contains(&"to_int"), "{:?}", h.calls());
    }

    /// A negative limit is not rejected, clamped or sign-flipped in Rust: it is
    /// handed to the seam exactly as a positive one is. `trim_url`'s own
    /// `"%s…" % x[: max(0, limit - 1)]` is what turns it into `'…'`, and the
    /// corpus's `-1`/`-5` rows are where that answer is pinned — a body that
    /// reimplemented the truncation, or that took a `usize`, fails there.
    #[test]
    fn a_negative_limit_reaches_the_seam_unread() {
        for limit in [-1i64, -5, 0, 1, 3, 1000] {
            let h = host(IntValue::Small(limit), "x", "RENDERED");
            assert_eq!(
                run(
                    Value::Str("x".into()),
                    Some(Value::Int(limit)),
                    &call(&h, true)
                )
                .unwrap(),
                Value::Safe("RENDERED".into()),
                "limit {limit} must delegate, not decide"
            );
            h.assert_called("urlize");
        }
    }

    /// A `Big` limit is **not an overflow** and reaches the seam as it is: a
    /// positive one is longer than any URL, which is the host's comparison to
    /// make (`len(x) <= limit`), not Rust's.
    #[test]
    fn a_big_limit_reaches_the_seam_unread() {
        let digits = "1000000000000000000000000000000".to_string();
        let h = host(IntValue::Big(digits.clone()), "x", "RENDERED");
        assert_eq!(
            run(
                Value::Str("x".into()),
                Some(Value::BigInt(digits.into())),
                &call(&h, true)
            )
            .unwrap(),
            Value::Safe("RENDERED".into())
        );
        h.assert_called("urlize");
    }

    /// A plain input is marked by the **body** (`defaultfilters.py:398`), not left
    /// to the loop.
    #[test]
    fn the_body_marks_the_result_safe() {
        let h = host(IntValue::Small(5), "x", "RENDERED");
        let result = run(Value::Str("x".into()), Some(Value::Int(5)), &call(&h, true)).unwrap();
        assert_eq!(result, Value::Safe("RENDERED".into()));
        assert_ne!(result, Value::Str("RENDERED".into()));
        h.assert_called("mark_safe");
    }
}
