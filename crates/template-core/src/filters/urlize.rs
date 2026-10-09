//! `urlize` — `defaultfilters.py:382-386`.
//!
//! ```python
//! @register.filter(is_safe=True, needs_autoescape=True)
//! @stringfilter
//! def urlize(value, autoescape=True):
//!     """Convert URLs in plain text into clickable links."""
//!     return mark_safe(_urlize(value, nofollow=True, autoescape=autoescape))
//! ```
//!
//! `_urlize` is `django.utils.html.urlize` (`html.py:496-499`), a
//! `@keep_lazy_text`-decorated wrapper over the module's shared `Urlizer`
//! instance. So the URL regex, the `settings.URLIZE_ASSUME_HTTPS` branch, the
//! `nofollow` attribute and every escaping decision inside the helper are the
//! host's ([`FilterHost::urlize`]), and the body is the seam call plus
//! `mark_safe`. The helper is delegated whole rather than reimplemented for the
//! reason §D2 gives and `html.py` bears out: `Urlizer` is a stateful instance
//! built once at import (`html.py:489-490`) whose `@cached_property` tables are
//! derived from `settings` — the very definition of an answer that does not live
//! in the template.
//!
//! **The body's decisions, in order:**
//!
//! 1. **Any argument refuses** — see below. The refusal is the body's and comes
//!    first, before any host interaction.
//! 2. `call.host.to_str(&value)` — the `@stringfilter` coercion (`:44-48`), done
//!    once by the FFI loop (§D3), so this is a pure accessor on an already
//!    coerced value. The call site reads it exactly as `slugify.rs:99` does.
//! 3. **The `autoescape` handed to the helper is
//!    `call.autoescape && !call.host.is_safe(&value)`**, the `linebreaks` pattern
//!    (`linebreaks.rs:130`) and the same expression Django's own `linebreaks`
//!    `def` writes at its level (`defaultfilters.py:486`). The helper's escaping
//!    is governed by `autoescape and not safe_input`, where
//!    `safe_input = isinstance(text, SafeData)` (`html.py:314`, `:374-376`) —
//!    measured, `mark_safe("<b>bold</b> https://e.com")` is **not** escaped while
//!    the same text as a plain `str` is. The seam takes `&str`, so the marker
//!    cannot cross it; collapsing the pair in the body is text-equivalent for
//!    every input, because `safe_input`'s only other use is
//!    `if safe_input: return mark_safe(word)`, whose text is the same `word` a
//!    falsy flag would yield. See "The `safe_input` collapse" below.
//! 4. `call.host.urlize(&text, None, autoescape)`. The limit is `None` because
//!    `:383` passes no `trim_url_limit`, so the helper's default applies;
//!    `nofollow=True` is the seam's constant — both call sites hardcode it, so
//!    the trait carries no `nofollow` parameter (`host.rs`).
//! 5. `call.host.mark_safe(Value::Str(rendered))`. The `def` body calls
//!    `mark_safe` itself (`:384`), and the loop does not supply it here: the
//!    loop marks only when `is_safe and isinstance(obj, SafeData)`
//!    (`base.py:816`), and for a plain `str` input neither half fires. So the
//!    body's own marking is what makes every row below a `SafeString`.
//!
//! ## The `safe_input` collapse
//!
//! `Urlizer.__call__` computes `safe_input = isinstance(text, SafeData)`
//! (`html.py:314`) and `handle_word` (`:333-397`) uses it in exactly four places:
//! the two `if autoescape and not safe_input:` guards (`:374-376`) and the two
//! `if safe_input: return mark_safe(word)` branches (`:387-390`, `:394-397`).
//! Under the substitution `autoescape <- autoescape and not safe_input`:
//!
//! * the first pair is the substitution verbatim;
//! * the second pair differ only in *safety* at the word level — `mark_safe(word)`
//!   against `word` — and `__call__` ends in `"".join(urlized_words)`, which
//!   returns a plain `str` for either, so the two are text-identical and the
//!   filter's final safety is the `def` body's own `mark_safe` regardless.
//!
//! Measured over 32 cases — `{plain, mark_safe} × {autoescape on, off} × 8`
//! inputs, including one with all four of `<`, `&`, `'`, `"` in one string —
//! the collapse changes no answer. Django's own `linebreaks`
//! (`defaultfilters.py:486`) is this same collapse, written in the source rather
//! than forced by a `&str` seam, which is why `linebreaks.rs` carries it too.
//!
//! Measured rows through `FilterExpression.resolve` on the pinned 6.0.9,
//! `URLIZE_ASSUME_HTTPS` unset unless said otherwise:
//!
//! | input | Django |
//! |---|---|
//! | `"see https://example.com/x now"` | `SafeString 'see <a href="https://example.com/x" rel="nofollow">https://example.com/x</a> now'` |
//! | `"visit www.example.com please"` | `SafeString 'visit <a href="http://www.example.com" rel="nofollow">www.example.com</a> please'`, **and emits `RemovedInDjango2028Warning`** |
//! | the same, `URLIZE_ASSUME_HTTPS=True` | `…href="https://www.example.com"…`, and emits nothing |
//! | `"mail me at a@b.com"` | `SafeString 'mail me at <a href="mailto:a@b.com">a@b.com</a>'` — a `mailto` link has no `rel` |
//! | `"no links here"` | `SafeString 'no links here'` |
//! | `"<b>bold</b> https://e.com"`, autoescape on | `'&lt;b&gt;bold&lt;/b&gt; <a href="https://e.com" rel="nofollow">https://e.com</a>'` — the surrounding text is escaped, the link is not |
//! | `mark_safe("<b>bold</b> https://e.com")`, autoescape on | `'<b>bold</b> <a href="https://e.com" rel="nofollow">https://e.com</a>'` — a safe input is not escaped (`safe_input`) |
//! | `"<b>bold</b> https://e.com"`, context autoescape off | the same as the `mark_safe` row |
//! | `{{ x|urlize:5 }}` | `TypeError: urlize() got multiple values for argument 'autoescape'` |
//!
//! **The `RemovedInDjango2028Warning` is real and the corpus cannot pin it.**
//! It is emitted by the host, because the host makes the call; a Python warning
//! is not part of any answer `_native.filter` returns, so no corpus row can
//! assert it. What the rows *can* pin is its **effect** — `http://` against
//! `https://` under the two states of `URLIZE_ASSUME_HTTPS` — and that pair of
//! rows is what `tests/filter_cases.py` carries, under its `settings` key. The
//! warning itself is delegated-only and nothing in this crate sees it.
//!
//! ## Why any argument at all is refused
//!
//! This filter's own second parameter is *named* `autoescape`, and Django's loop
//! calls `func(obj, autoescape=context.autoescape, *arg_vals)` (`base.py:813`) —
//! keyword before positional — so **any** argument binds twice and raises at call
//! time. `args_check` (`base.py:822`) counts `autoescape` among the parameters,
//! so the parse accepts the token; the failure is CPython's binding of the call,
//! which is why the class is a `TypeError` and not a `TemplateSyntaxError` and
//! why a parser that rejected the token would be wrong. It is the fifth of the
//! five collision filters (`linebreaks`, `linebreaksbr`, `linenumbers`,
//! `unordered_list`, this one). Measured:
//!
//! ```text
//! {{ x|urlize:5 }} -> builtins.TypeError: urlize() got multiple values for argument 'autoescape'
//! ```
//!
//! The refusal does not consult the argument — `{{ x|urlize:None }}` collides
//! too — so the test is the argument's *presence* (`arg.is_some()`), not its
//! value. For this filter the registered name and the `def` name coincide, so
//! the callable the message names is `urlize`; `linebreaks` is the one whose two
//! names differ (`linebreaks_filter`).
//!
//! Our engine takes `autoescape` as a *value*, so there is no keyword to collide
//! with and the argument would otherwise be silently ignored — answering where
//! Django raises, which this project treats as a defect. The body refuses instead.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// The `TypeError` CPython's call binding raises when a positional argument
/// collides with the `autoescape` keyword. The operation names the callable,
/// which here is the registered name because the two coincide.
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
    // `linebreaks` makes (`linebreaks.rs:130`), and Rust's `&&` short-circuits
    // as Python's does, so `is_safe` is not asked when `call.autoescape` is
    // false. See the module doc's "The `safe_input` collapse".
    let autoescape = call.autoescape && !call.host.is_safe(&value);
    // `defaultfilters.py:44`'s `str(value)`, done once by the FFI loop (§D3).
    let text = call.host.to_str(&value)?;
    // `html.py:496-499` whole; `nofollow=True` is the seam's constant and `None`
    // is `:383`'s absent `trim_url_limit`.
    let rendered = call.host.urlize(&text, None, autoescape);
    // `:384`'s own `mark_safe` — the loop's does not fire for a plain `str`.
    Ok(call.host.mark_safe(Value::Str(rendered.into())))
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
    /// text it was *told* to return — `TestFilterHost::mark_safe` ignores the
    /// value it is handed, so the assembled answer is the corpus's to pin, not
    /// this test's. What this host can prove is **which operations the body
    /// reached**, which is the half a Rust reimplementation cannot satisfy.
    ///
    /// `is_safe` is scripted because the body asks it: the helper's `autoescape`
    /// is `call.autoescape && !is_safe(value)` (the module doc's collapse), and
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
    /// refusal happens **before** the seam — so a bare host suffices and the test
    /// also pins that no host call is made.
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
    /// explicit `None` token collides exactly as `5` does — measured, so a body
    /// that branched on the argument's truth would answer here.
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

    /// The body delegates: `is_safe`, `to_str`, then `urlize`, then `mark_safe`.
    /// The `urlize` call is the guard a Rust urlizer cannot satisfy, and it comes
    /// **before** the marking, which is the source's own order.
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
        assert_eq!(h.calls(), vec!["is_safe", "to_str", "urlize", "mark_safe"]);
    }

    /// **The `safe_input` collapse, as far as a name-recording host can see it.**
    /// A safe value turns `autoescape` off at the seam even when the context has
    /// it on — the row `mark_safe("<b>bold</b> https://e.com")` is the corpus's,
    /// and its *answer* is what pins the flag. What this pins is that the body
    /// asks `is_safe` at all, and that it asks it with `&&`'s short circuit: with
    /// the context's `autoescape` false the question is never put, exactly as
    /// Django's `autoescape and not isinstance(value, SafeData)` evaluates it.
    /// `TestFilterHost::urlize` ignores the flag it is handed, so a body that
    /// asked `is_safe` and then threw the answer away would satisfy this test and
    /// fail the corpus — which is the split the two checks are for.
    #[test]
    fn a_safe_value_is_consulted_for_the_seam_autoescape() {
        let h = host("x", "RENDERED");
        run(Value::Str("x".into()), None, &call(&h, true)).unwrap();
        assert_eq!(h.calls(), vec!["is_safe", "to_str", "urlize", "mark_safe"]);

        // `call.autoescape` false: `&&` short-circuits, so `is_safe` is not
        // asked — Django's own evaluation order for the same expression.
        let h = host("x", "RENDERED");
        run(Value::Str("x".into()), None, &call(&h, false)).unwrap();
        assert_eq!(h.calls(), vec!["to_str", "urlize", "mark_safe"]);
    }

    /// A plain input is marked by the **body**, not left to the loop — the body
    /// returns `Value::Safe` and not `Value::Str`, which structural equality tells
    /// apart.
    #[test]
    fn the_body_marks_the_result_safe() {
        let h = host("x", "RENDERED");
        let result = run(Value::Str("x".into()), None, &call(&h, true)).unwrap();
        assert_eq!(result, Value::Safe("RENDERED".into()));
        assert_ne!(result, Value::Str("RENDERED".into()));
        h.assert_called("mark_safe");
    }

    /// The `autoescape` the body hands the seam is `call.autoescape` and not a
    /// constant. `TestFilterHost::urlize` ignores the flag, so the *value* is
    /// pinned by the corpus's autoescape rows; what this pins is that the body
    /// makes the call at all for both context states, so a body that refused to
    /// run with autoescape off would fail here.
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
