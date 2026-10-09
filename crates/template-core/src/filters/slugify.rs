//! `slugify` — `defaultfilters.py:266-274`.
//!
//! ```python
//! @register.filter(is_safe=True)
//! @stringfilter
//! def slugify(value):
//!     """
//!     Convert to ASCII. Convert spaces to hyphens. Remove characters that aren't
//!     alphanumerics, underscores, or hyphens. Convert to lowercase. Also strip
//!     leading and trailing whitespace.
//!     """
//!     return _slugify(value)
//! ```
//!
//! and the whole of `_slugify` — `django.utils.text.slugify`
//! (`.django/django/utils/text.py:463-481`) — is [`FilterHost::slugify`]:
//!
//! ```python
//! value = str(value)
//! if allow_unicode:
//!     value = unicodedata.normalize("NFKC", value)
//! else:
//!     value = (
//!         unicodedata.normalize("NFKD", value)
//!         .encode("ascii", "ignore")
//!         .decode("ascii")
//!     )
//! value = re.sub(r"[^\w\s-]", "", value.lower())
//! return re.sub(r"[-\s]+", "-", value).strip("-_")
//! ```
//!
//! `@stringfilter` and `is_safe=True`: the coercion is the FFI loop's (§D3), so
//! the text arrives through [`FilterHost::to_str`] as a pure accessor, and the
//! safe-marking of the result is the loop's too — measured, `mark_safe("AbC
//! dEf")|slugify` is `(SafeString, 'abc-def')` while a plain input is
//! `(str, 'abc-def') safe=False`. The body returns a plain [`Value::Str`].
//!
//! **The filter takes no argument, and the arity is refused before any body
//! runs.** Measured: `{{ x|slugify:1 }}` is `TemplateSyntaxError: slugify
//! requires 1 arguments, 2 provided` out of `args_check` — Django's, and the
//! Rust parser's own (`parser/filter_expr.rs`). So `_arg` cannot hold anything
//! and is not a dropped parameter; there is nothing to refuse here because the
//! refusal already happened a layer up.
//!
//! Measured rows (pinned 6.0.9, through `FilterExpression.resolve`):
//!
//! ```text
//! 'Hello, World!'       -> 'hello-world'
//! '  hello  world  '    -> 'hello-world'
//! 'a--b'                -> 'a-b'
//! '-a-b-'               -> 'a-b'
//! 'a_b'                 -> 'a_b'          (underscores are kept; strip("-_") is ends-only)
//! '--'                  -> ''
//! 'hello@example.com'   -> 'helloexamplecom'
//! 'CamelCase 42'        -> 'camelcase-42'
//! 'a b\tc\nd'           -> 'a-b-c-d'
//! 5                     -> '5'            (the @stringfilter coercion)
//! None                  -> 'none'
//! mark_safe('AbC dEf')  -> (SafeString, 'abc-def') safe=True
//! ```
//!
//! **Three corrections to the plan's Task 9 bullet and to this seam's own doc,
//! all measured on the pinned tree:**
//!
//! * **The ascii step is not "a combining-mark strip".** It is
//!   `.encode("ascii", "ignore")`, which drops *every* non-ASCII code point
//!   after NFKD. The two differ where NFKD does not decompose a letter to an
//!   ASCII base plus combining marks: measured, `slugify("Straße")` is `'strae'`
//!   (the `ß` is dropped, not expanded to `ss`) and `slugify("ΟΣ")` is `''`.
//!   `slugify("á")` → `'a'` is the case where both descriptions agree.
//! * **`slugify("ΟΣ")` is `''`, not `'ος'`.** The plan proposes that row to
//!   "reach the host's lowercasing"; it cannot, because the `.lower()`
//!   (`text.py:480`) runs *after* the ascii encode, so it can only ever see
//!   ASCII. Consequence for this seam's own justification: the Final_Sigma
//!   argument that [`FilterHost::str_lower`] exists for is **not observable
//!   through `slugify`** — the input that would exercise it cannot survive the
//!   ascii step. What forces the whole-method delegation is NFKD
//!   (`unicodedata`, a large table), which the plan names as well.
//! * **"The two regexes and the ordering are Rust's" contradicts the same
//!   paragraph's "the whole of `_slugify` is the host's", the fixed
//!   `slugify(&str) -> String` signature, and the pinned source**, which is one
//!   Python function. The ordering the plan gives is also wrong: the pinned
//!   order is NFKD + ascii-drop, then `value.lower()` *inside* the first
//!   `re.sub`, then `re.sub(r"[-\s]+", "-", …)`, then `.strip("-_")`. The plan
//!   puts `.strip().lower()` before the second `re.sub`, and spells `strip()`
//!   rather than `strip("-_")` — which is why `slugify("a_b")` is `'a_b'` and
//!   not `'ab'` (measured).

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::Error;

/// `slugify`'s body: `_slugify(str(value))`.
///
/// Both halves are the host's: the `@stringfilter` coercion through
/// [`FilterHost::to_str`] (a pure accessor on an already-coerced value, §D7.1's
/// `&str` case) and the whole of `django.utils.text.slugify` through
/// [`FilterHost::slugify`]. The body has no algorithm of its own to get wrong.
pub fn run(value: Value, _arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // `defaultfilters.py:44`'s `str(value)`, done once by the FFI loop (§D3).
    let text = call.host.to_str(&value)?;
    // `text.py:471-481`, whole — NFKD, the ascii drop and its two `re.sub`s.
    Ok(Value::Str(call.host.slugify(&text).into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape: true,
            use_tz: true,
        }
    }

    fn host(text: &str, slug: &str) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            slugify: Some(slug.to_string()),
            ..TestFilterHost::default()
        }
    }

    /// The whole body is the one seam call, and its answer is returned as a plain
    /// `str` (the loop marks a `SafeData` input, not the body).
    #[test]
    fn it_returns_the_hosts_slugify() {
        let h = host("Hello, World!", "hello-world");
        assert_eq!(
            run(Value::Str("Hello, World!".into()), None, &call(&h)).unwrap(),
            Value::Str("hello-world".into())
        );
        h.assert_called("to_str");
        h.assert_called("slugify");
    }

    /// A plain input is **not** marked safe by the body. `Value::Safe` and
    /// `Value::Str` are structurally unequal, so a body that wrongly marked
    /// would fail here — and `mark_safe` is not scripted, so reaching it panics
    /// rather than answering.
    #[test]
    fn the_body_does_not_mark_the_result_safe() {
        let h = host("AbC dEf", "abc-def");
        let result = run(Value::Str("AbC dEf".into()), None, &call(&h)).unwrap();
        assert_eq!(result, Value::Str("abc-def".into()));
        assert_ne!(result, Value::Safe("abc-def".into()));
    }

    /// The text is read through the seam: `slugify(5)` is `'5'`, the
    /// `@stringfilter` coercion — measured on the pinned tree.
    #[test]
    fn it_coerces_through_the_host() {
        let h = host("5", "5");
        assert_eq!(
            run(Value::Int(5), None, &call(&h)).unwrap(),
            Value::Str("5".into())
        );
        h.assert_called("to_str");
    }

    /// The non-ASCII half is the host's answer, not the body's — scripted here
    /// to the two measured rows the module doc corrects, so a body that grew a
    /// Rust ascii-drop would fail on the value rather than on a refusal.
    #[test]
    fn the_non_ascii_behaviour_is_the_hosts() {
        let h = host("Straße", "strae");
        assert_eq!(
            run(Value::Str("Straße".into()), None, &call(&h)).unwrap(),
            Value::Str("strae".into()),
            "the `ß` is dropped by `.encode(\"ascii\", \"ignore\")`, not expanded"
        );

        let h = host("ΟΣ", "");
        assert_eq!(
            run(Value::Str("ΟΣ".into()), None, &call(&h)).unwrap(),
            Value::Str("".into()),
            "the Greek letters are gone before `.lower()` ever runs"
        );
    }
}
