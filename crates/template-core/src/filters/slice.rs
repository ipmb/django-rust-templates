//! `slice` — `defaultfilters.py:651-665`.
//!
//! ```python
//! @register.filter("slice", is_safe=True)
//! def slice_filter(value, arg):
//!     """
//!     Return a slice of the list using the same syntax as Python's list slicing.
//!     """
//!     try:
//!         bits = []
//!         for x in str(arg).split(":"):
//!             if not x:
//!                 bits.append(None)
//!             else:
//!                 bits.append(int(x))
//!         return value[slice(*bits)]
//!
//!     except (ValueError, TypeError, KeyError):
//!         return value  # Fail silently.
//! ```
//!
//! **Rust parses the argument; the host performs the slicing.** [`FilterHost::get_slice`]
//! is what runs `value[start:stop:step]`, and that is not a convenience: it
//! cannot be rebuilt from [`FilterHost::get_index`] plus Rust slicing, because
//! `"abcdef"|slice:":3"` is the **string** `"abc"` where an index walk yields a
//! list — the same text in the wrong type, exactly what §D5's comparison is built
//! to catch.
//!
//! **The trap is that `slice`'s single positional argument is its *stop*, not its
//! start.** `slice(1)` is `slice(None, 1, None)` — measured, and it makes
//! `slice_filter("abcdefg", "1")` the string `"a"`, not `"bcdefg"`. So the bit
//! mapping is one-to-one and **off-by-one in the obvious place**:
//!
//! ```text
//! [b]       -> slice(b)       -> start=None, stop=b,   step=None
//! [a, b]    -> slice(a, b)    -> start=a,    stop=b,   step=None
//! [a, b, c] -> slice(a, b, c) -> start=a,    stop=b,   step=c
//! ```
//!
//! Measured rows on the pinned tree (all on `"abcdefg"`, and the result of the
//! first group is a `str`):
//!
//! ```text
//! "0"       -> ""        "1"       -> "a"        "-1"      -> "abcdef"
//! "1:2"     -> "b"       "1:3"     -> "bc"       "::2"     -> "aceg"
//! "0::2"    -> "aceg"    ":3"      -> "abc"      ""        -> "abcdefg"
//! None      -> "abcdefg" " 3 "    -> "abc"      "1:2:3"   -> "b"
//! {}        -> {}        (the whole value returns when the slicing raises)
//! ```
//!
//! Three ways the `except` is reached, and the brief's `"a:b:c:d"` row is only one
//! of them — **measured, and it is not the one it looks like**: `int("a")` raises
//! `ValueError` *before* `slice(*bits)` is built, so `"a:b:c:d"` exercises the
//! parse arm. A four-component argument made of *valid* integers (`"1:2:3:4"`) is
//! what exercises the arity arm, where `slice(1, 2, 3, 4)` raises `TypeError:
//! slice expected at most 3 arguments, got 4`. Both return the value; the corpus
//! needs a row for each, and the second is the one that pins the arity guard.
//!
//! A component's `int(x)` goes through [`FilterHost::to_int`] rather than a Rust
//! `parse::<i64>()`, because Python's `int` accepts `" 3 "`, `"3_0"` and `"٠٥"`
//! and is arbitrary-precision; measured, `slice_filter("abcdefg", " 3 ")` is
//! `"abc"`. A component outside `i64` **saturates** (a positive `Big` to
//! `i64::MAX`, a negative one to `i64::MIN`), which is faithful because Python
//! clamps a slice bound to the sequence's own length anyway.
//!
//! A `TypeError` from `value[slice]` (a `dict` is unhashable-by-slice) is also
//! caught — measured `slice_filter({}, "1")` is `{}`.

use crate::filters::host::FilterCall;
use crate::filters::value::Value;
use crate::parser::error::{Error, ErrorKind, PyErrorKind};
use crate::pynum::IntValue;

/// `except (ValueError, TypeError, KeyError): return value`.
fn is_caught(error: &Error) -> bool {
    matches!(
        &error.kind,
        ErrorKind::FilterHost(host)
            if matches!(
                host.kind,
                PyErrorKind::ValueError | PyErrorKind::TypeError | PyErrorKind::KeyError
            )
    )
}

/// `int(x)` for one non-empty component, via the host's Python `int`.
fn component(text: &str, call: &FilterCall<'_>) -> Result<Option<i64>, Error> {
    if text.is_empty() {
        return Ok(None);
    }
    let parsed = call.host.to_int(&Value::Str(text.into()))?;
    Ok(Some(saturate(parsed)))
}

/// A component outside `i64`, saturated to the sign's bound. Python clamps a
/// slice bound to the sequence's length, so for any sequence shorter than
/// `i64::MAX` (i.e. all of them) the saturated value slices identically.
fn saturate(value: IntValue) -> i64 {
    match value {
        IntValue::Small(number) => number,
        // `IntValue::Big` holds canonical decimal text, so the sign is its first
        // character (`int("-10**30")`).
        IntValue::Big(text) if text.starts_with('-') => i64::MIN,
        IntValue::Big(_) => i64::MAX,
    }
}

/// `str(arg).split(":")` into `None`/`int` components — the Rust half of the
/// body, and the whole of it that can raise.
fn parse_bits(arg: Option<Value>, call: &FilterCall<'_>) -> Result<Vec<Option<i64>>, Error> {
    // `str(arg)`; a missing argument is Python's `None`, whose `str()` is
    // `"None"`, which is what makes `{{ x|slice }}` return the value unchanged.
    let text = call.host.to_str(&arg.unwrap_or(Value::None))?;
    let mut bits = Vec::new();
    for part in text.split(':') {
        bits.push(component(part, call)?);
    }
    Ok(bits)
}

/// `slice`'s body.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    let bits = match parse_bits(arg, call) {
        Ok(bits) => bits,
        Err(error) if is_caught(&error) => return Ok(value),
        Err(error) => return Err(error),
    };
    // `slice(*bits)`. The single-component case is the stop; four or more
    // components raise `TypeError`, which the same `try` catches.
    let (start, stop, step) = match bits.as_slice() {
        [stop] => (None, *stop, None),
        [start, stop] => (*start, *stop, None),
        [start, stop, step] => (*start, *stop, *step),
        _ => return Ok(value),
    };
    match call.host.get_slice(&value, start, stop, step) {
        Ok(sliced) => Ok(sliced),
        Err(error) if is_caught(&error) => Ok(value),
        Err(error) => Err(error),
    }
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

    fn host(text: &str, component: IntValue, sliced: Value) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            to_int: Some(component),
            get_slice: Some(sliced),
            ..TestFilterHost::default()
        }
    }

    /// The slicing is delegated: `to_str` (for `str(arg)`), `to_int` (for the one
    /// non-empty component of `"::2"`) and `get_slice` are all reached, and the
    /// host's answer is returned **as-is** — a body that sliced in Rust would
    /// return a list here where the scripted answer is the string `"aceg"`.
    ///
    /// The argument set this must send is the module doc's table: for `"::2"`,
    /// `start=None, stop=None, step=Some(2)`. `TestFilterHost` records the name
    /// and not the arguments, so this row cannot pin them — the central corpus
    /// does, because the real host consumes them.
    #[test]
    fn it_delegates_the_slice_to_the_host() {
        let h = host("::2", IntValue::Small(2), Value::Str("aceg".into()));
        assert_eq!(
            run(
                Value::Str("abcdefg".into()),
                Some(Value::Str("::2".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("aceg".into()),
            "the host's slice answer is returned unchanged"
        );
        h.assert_called("to_str");
        h.assert_called("to_int");
        h.assert_called("get_slice");
    }

    /// A one-component argument is one `to_int` call and one `get_slice` call:
    /// `"1"` is `slice(1)`, whose single argument is the **stop**, and the
    /// measured answer is `"a"`. The count of `to_int` calls is what this row
    /// guards (a body that split the component again, or that called the host
    /// once per character, would not make exactly one).
    #[test]
    fn a_single_component_is_one_stop_component() {
        let h = host("1", IntValue::Small(1), Value::Str("a".into()));
        assert_eq!(
            run(
                Value::Str("abcdefg".into()),
                Some(Value::Str("1".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("a".into())
        );
        assert_eq!(
            h.calls().iter().filter(|name| **name == "to_int").count(),
            1,
            "one non-empty component -> one int(): {:?}",
            h.calls()
        );
        h.assert_called("get_slice");
    }

    /// `" 3 "` is a single component whose `int()` the host parses: measured
    /// `slice_filter("abcdefg", " 3 ")` is `"abc"` (a Rust `parse` would refuse
    /// the spaces).
    #[test]
    fn a_component_with_spaces_is_python_int() {
        let h = host(" 3 ", IntValue::Small(3), Value::Str("abc".into()));
        assert_eq!(
            run(
                Value::Str("abcdefg".into()),
                Some(Value::Str(" 3 ".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abc".into())
        );
        h.assert_called("to_int");
        h.assert_called("get_slice");
    }

    /// **The arity guard.** A four-component argument of valid integers reaches
    /// `slice(1, 2, 3, 4)`, which raises `TypeError`, so the value comes back
    /// **without** the host being asked to slice (measured `"1:2:3:4"` →
    /// `"abcdefg"`).
    #[test]
    fn four_components_return_the_value_without_slicing() {
        let h = host("1:2:3:4", IntValue::Small(1), Value::Str("unused".into()));
        let value = Value::Str("abcdefg".into());
        assert_eq!(
            run(value.clone(), Some(Value::Str("1:2:3:4".into())), &call(&h)).unwrap(),
            value
        );
        h.assert_called("to_int");
        assert!(
            !h.calls().contains(&"get_slice"),
            "four components must not reach the slicer: {:?}",
            h.calls()
        );
    }

    /// `"a:b:c:d"` reaches the **parse** arm: `int("a")` raises `ValueError`
    /// first, so the value returns before the arity is ever consulted. The row is
    /// here rather than folded into the previous one because it fails under a
    /// different mutation (a Rust `parse` that treated the failure as non-fatal).
    #[test]
    fn a_non_numeric_component_returns_the_value() {
        let h = host("a:b:c:d", IntValue::Small(1), Value::Str("unused".into()));
        h.raise_on("to_int", PyErrorKind::ValueError);
        let value = Value::Str("abcdefg".into());
        assert_eq!(
            run(value.clone(), Some(Value::Str("a:b:c:d".into())), &call(&h)).unwrap(),
            value
        );
        assert!(!h.calls().contains(&"get_slice"), "{:?}", h.calls());
    }

    /// A `TypeError` from the slicing itself — `slice_filter({}, "1")` — is
    /// caught, so the **value** comes back (measured `{}` → `{}`), not `""`.
    #[test]
    fn a_type_error_from_the_slice_returns_the_value() {
        let h = host("1", IntValue::Small(1), Value::Str("unused".into()));
        h.raise_on("get_slice", PyErrorKind::TypeError);
        assert_eq!(
            run(Value::None, Some(Value::Str("1".into())), &call(&h)).unwrap(),
            Value::None
        );
        h.assert_called("get_slice");
    }

    /// A `KeyError` from the slice is caught too — the source names three classes
    /// and this is the one nothing else reaches.
    #[test]
    fn a_key_error_from_the_slice_returns_the_value() {
        let h = host("1", IntValue::Small(1), Value::Str("unused".into()));
        h.raise_on("get_slice", PyErrorKind::KeyError);
        let value = Value::List(vec![Value::Int(1)]);
        assert_eq!(
            run(value.clone(), Some(Value::Str("1".into())), &call(&h)).unwrap(),
            value
        );
    }

    /// A component outside `i64` saturates and still slices: the host is asked,
    /// rather than the body panicking on a `Big` it could not convert.
    #[test]
    fn a_big_component_saturates_rather_than_failing() {
        let h = host(
            "1",
            IntValue::Big("1000000000000000000000000000000".to_string()),
            Value::Str("abcdefg".into()),
        );
        assert_eq!(
            run(
                Value::Str("abcdefg".into()),
                Some(Value::Str("1".into())),
                &call(&h)
            )
            .unwrap(),
            Value::Str("abcdefg".into())
        );
        h.assert_called("get_slice");
    }

    /// The saturating map itself, both signs — a Rust-side unit test because the
    /// sign is the part the host cannot see once it holds an `i64`.
    #[test]
    fn saturation_takes_the_signs_bound() {
        assert_eq!(saturate(IntValue::Small(-2)), -2);
        assert_eq!(saturate(IntValue::Big("9".repeat(40))), i64::MAX);
        assert_eq!(
            saturate(IntValue::Big(format!("-{}", "9".repeat(40)))),
            i64::MIN
        );
    }
}
