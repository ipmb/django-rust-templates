//! `urlize` — `defaultfilters.py:382-386`.
//!
//! `_urlize` is `django.utils.html.urlize` (`html.py:496-499`), a
//! `@keep_lazy_text`-decorated wrapper over the module's shared `Urlizer`. The URL
//! regex, the `settings.URLIZE_ASSUME_HTTPS` branch, `nofollow` and every escaping
//! decision inside the helper are the host's ([`FilterHost::urlize`]); the body is
//! the seam call plus `mark_safe`. `Urlizer` is a stateful instance built once at
//! import (`html.py:492`) whose `@cached_property` tables derive from `settings` —
//! an answer that does not live in the template.
//!
//! **The body's decisions, in order:**
//!
//! 1. **Any argument refuses**, before any host interaction (see below).
//! 2. `call.host.to_str(&value)` — the `@stringfilter` coercion
//!    (`defaultfilters.py:44-48`), already done once by the FFI loop.
//! 3. **The `autoescape` handed to the helper is
//!    `call.autoescape && !call.host.is_safe(&value)`**, the `linebreaks` pattern
//!    (`crates/template-core/src/filters/linebreaks.rs:130`, and Django's own
//!    `defaultfilters.py:493`). The helper's escaping is governed by
//!    `autoescape and not safe_input`, `safe_input = isinstance(text, SafeData)`
//!    (`html.py:319`, `html.py:387-389`). The seam takes `&str`, so the marker
//!    cannot cross it; collapsing the pair is text-equivalent for every input
//!    because `safe_input`'s only other use, `if safe_input: return
//!    mark_safe(word)`, yields the same `word` text either way.
//! 4. `call.host.urlize(&text, None, autoescape)`. `None` is
//!    `defaultfilters.py:386`'s absent `trim_url_limit`; `nofollow=True` is the
//!    seam's constant, hardcoded at both call sites, so the trait carries no
//!    `nofollow` parameter.
//! 5. `safety::mark_safe(Value::Str(rendered), call)` — the `def` body's own
//!    `mark_safe` (`defaultfilters.py:386`). The loop marks only when
//!    `is_safe and isinstance(obj, SafeData)` (`base.py:816`), and for a plain
//!    `str` input neither half fires, so the body's marking is what makes every
//!    row below a `SafeString`.
//!
//! Measured rows through `FilterExpression.resolve` on 6.0.9,
//! `URLIZE_ASSUME_HTTPS` unset unless said otherwise:
//!
//! | input | Django |
//! |---|---|
//! | `"see https://example.com/x now"` | `SafeString 'see <a href="https://example.com/x" rel="nofollow">https://example.com/x</a> now'` |
//! | `"visit www.example.com please"` | an `http://` href, **and emits `RemovedInDjango2028Warning`**; with `URLIZE_ASSUME_HTTPS=True` the href is `https://` and nothing is emitted |
//! | `"mail me at a@b.com"` | a `mailto:` link, which has no `rel` |
//! | `"<b>bold</b> https://e.com"` | the surrounding text is escaped, the link is not; as `mark_safe(…)`, or with context autoescape off, nothing is escaped (`safe_input`) |
//! | `{{ x|urlize:5 }}` | `TypeError: urlize() got multiple values for argument 'autoescape'` |
//!
//! The `RemovedInDjango2028Warning` is emitted by the host, so no corpus row can
//! assert it; what the corpus pins is its **effect** — `http://` against
//! `https://` under the two `URLIZE_ASSUME_HTTPS` states — under its `settings`
//! key.
//!
//! ## Why any argument at all is refused
//!
//! This filter's own second parameter is *named* `autoescape`, and Django's loop
//! calls `func(obj, autoescape=context.autoescape, *arg_vals)` (`base.py:813`) —
//! keyword before positional — so **any** argument binds twice and raises. The
//! failure is CPython's binding of the call, not a parse error (`args_check`,
//! `base.py:822`, counts `autoescape` and accepts the token), which is why a
//! parser that rejected the token would be wrong. It is the fifth of the five
//! collision filters (`linebreaks`, `linebreaksbr`, `linenumbers`,
//! `unordered_list`, this one). The test is the argument's *presence*, not its
//! value — `{{ x|urlize:None }}` collides too. Our engine takes `autoescape` as a
//! *value*, so the argument would otherwise be silently ignored, answering where
//! Django raises; the body refuses instead.

use crate::filters::host::FilterCall;
use crate::filters::safety;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// The `TypeError` CPython's call binding raises when a positional argument
/// collides with the `autoescape` keyword; the operation names the callable.
fn autoescape_collision() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "urlize",
        "urlize() got multiple values for argument 'autoescape'",
    )
}

/// `urlize`'s body: `mark_safe(_urlize(str(value), nofollow=True, autoescape))`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if arg.is_some() {
        return Err(autoescape_collision());
    }
    // `autoescape and not isinstance(value, SafeData)` — the same collapse
    // `linebreaks` makes (`crates/template-core/src/filters/linebreaks.rs:130`);
    // Rust's `&&` short-circuits as Python's does, so `is_safe` is not asked when
    // `call.autoescape` is false.
    let autoescape = call.autoescape && !call.host.is_safe(&value);
    // `defaultfilters.py:46`'s `str(value)`, done once by the FFI loop (§D3).
    let text = call.host.to_str(&value)?;
    // `html.py:496-499` whole; `nofollow=True` is the seam's constant and `None`
    // is `defaultfilters.py:386`'s absent `trim_url_limit`.
    let rendered = call.host.urlize(&text, None, autoescape);
    // `defaultfilters.py:386`'s own `mark_safe`; the loop's does not fire for a
    // plain `str`.
    Ok(safety::mark_safe(Value::Str(rendered.into()), call))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;
    use crate::parser::error::ErrorKind;

    fn call(host: &TestFilterHost, autoescape: bool) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape,
            use_tz: false,
        }
    }

    /// A host whose `urlize` answer is scripted and whose `mark_safe` returns the
    /// text it was *told* to return (it ignores its argument), so the assembled
    /// answer is the corpus's to pin; what this host proves is **which operations
    /// the body reached**. `is_safe` is scripted because the body asks it, and
    /// `TestFilterHost::is_safe` panics when unset.
    fn host(text: &str, rendered: &str) -> TestFilterHost {
        TestFilterHost {
            is_safe: Some(false),
            to_str: Some(text.to_string()),
            urlize: Some(rendered.to_string()),
            mark_safe: Some(Value::Safe(rendered.into())),
            ..TestFilterHost::default()
        }
    }

    /// Any argument is refused, with Django's callable name and message, and the
    /// refusal happens **before** the seam, so a bare host suffices and no host
    /// call is made.
    #[test]
    fn any_argument_is_refused_with_the_call_collision() {
        let h = TestFilterHost::default();
        let error = run(Value::Str("x".into()), Some(Value::Int(5)), &call(&h, true)).unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::TypeError);
                assert_eq!(host_error.operation, "urlize");
                assert_eq!(
                    host_error.message,
                    "urlize() got multiple values for argument 'autoescape'"
                );
            }
            other => panic!("an argument must raise the call collision, got {other:?}"),
        }
        assert!(
            h.calls().is_empty(),
            "the refusal is a call-binding fact, not a body that reached the host"
        );
    }

    /// **The refusal tests the argument's *presence*, not its value.** The
    /// explicit `None` token collides exactly as `5` does.
    #[test]
    fn an_explicit_none_argument_is_refused_too() {
        let h = TestFilterHost::default();
        let error = run(Value::Str("x".into()), Some(Value::None), &call(&h, true)).unwrap_err();
        assert!(
            matches!(&error.kind, ErrorKind::FilterHost(e) if e.kind == PyErrorKind::TypeError),
            "`{{ x|urlize:None }}` collides as well: {error:?}"
        );
        assert!(h.calls().is_empty(), "{:?}", h.calls());
    }

    /// The body delegates: `is_safe`, `to_str`, then `urlize`, then `mark_safe` —
    /// the source's own order, with the seam call before the marking.
    #[test]
    fn it_delegates_then_marks_safe() {
        let h = host("see https://e.com now", "RENDERED");
        assert_eq!(
            run(
                Value::Str("see https://e.com now".into()),
                None,
                &call(&h, true)
            )
            .unwrap(),
            Value::Safe("RENDERED".into())
        );
        assert_eq!(h.calls(), vec!["is_safe", "to_str", "urlize"]);
    }

    /// **The `safe_input` collapse, as far as a name-recording host can see it.**
    /// A safe value turns `autoescape` off at the seam even when the context has
    /// it on, and with `call.autoescape` false `&&` short-circuits so `is_safe` is
    /// never asked. `TestFilterHost::urlize` ignores the flag it is handed, so the
    /// flag's *value* is the corpus's to pin; a body that asked `is_safe` and then
    /// threw the answer away would satisfy this test and fail the corpus.
    #[test]
    fn a_safe_value_is_consulted_for_the_seam_autoescape() {
        let h = host("x", "RENDERED");
        run(Value::Str("x".into()), None, &call(&h, true)).unwrap();
        assert_eq!(h.calls(), vec!["is_safe", "to_str", "urlize"]);

        // `call.autoescape` false: `&&` short-circuits, so `is_safe` is not
        // asked, as Django's own expression evaluates it.
        let h = host("x", "RENDERED");
        run(Value::Str("x".into()), None, &call(&h, false)).unwrap();
        assert_eq!(h.calls(), vec!["to_str", "urlize"]);
    }

    /// A plain input is marked by the **body**, not left to the loop: the body
    /// returns `Value::Safe`, which structural equality tells from `Value::Str`.
    #[test]
    fn the_body_marks_the_result_safe() {
        let h = host("x", "RENDERED");
        let result = run(Value::Str("x".into()), None, &call(&h, true)).unwrap();
        assert_eq!(result, Value::Safe("RENDERED".into()));
        assert_ne!(result, Value::Str("RENDERED".into()));
        // The safety is asserted on the value just above. `mark_safe` of a
        // native string is Rust's now (`filters::safety`), so there is no host
        // call left to assert on.
    }

    /// The `autoescape` the body hands the seam is `call.autoescape` and not a
    /// constant; `TestFilterHost::urlize` ignores the flag, so the *value* is the
    /// corpus's, while this pins that the call is made under both context states.
    #[test]
    fn it_runs_under_both_autoescape_states() {
        for autoescape in [true, false] {
            let h = host("x", "RENDERED");
            assert_eq!(
                run(Value::Str("x".into()), None, &call(&h, autoescape)).unwrap(),
                Value::Safe("RENDERED".into())
            );
            h.assert_called("urlize");
        }
    }
}
