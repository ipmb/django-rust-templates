//! `linenumbers` — `defaultfilters.py:230-244`.
//!
//! ```python
//! @register.filter(is_safe=True, needs_autoescape=True)
//! @stringfilter
//! def linenumbers(value, autoescape=True):
//!     """Display text with line numbers."""
//!     lines = value.split("\n")
//!     # Find the maximum width of the line count, for use with zero padding
//!     # string format command
//!     width = str(len(str(len(lines))))
//!     if not autoescape or isinstance(value, SafeData):
//!         for i, line in enumerate(lines):
//!             lines[i] = ("%0" + width + "d. %s") % (i + 1, line)
//!     else:
//!         for i, line in enumerate(lines):
//!             lines[i] = ("%0" + width + "d. %s") % (i + 1, escape(line))
//!     return mark_safe("\n".join(lines))
//! ```
//!
//! Rust throughout. Three facts are easy to get wrong and each is measured:
//!
//! * **`width` is the number of digits of the line count**, so it really pads:
//!   12 lines → `%02d` → `'01. '`; 100 lines → `%03d` → `'001. '`; one line →
//!   `'1. '`. A body that hardcoded a width or padded to the *length* of the
//!   last number would answer `'1. '` where Django answers `'01. '`.
//! * The escaping is Django's **unconditional** `escape` = Rust's [`html_escape`]
//!   per line, and again **not** `conditional_escape`. The branch is
//!   `not autoescape or isinstance(value, SafeData)`: escape only when
//!   `call.autoescape && !call.host.is_safe(&value)`. (Task 8's bullet says the
//!   body runs "over the host's `conditional_escape` and Rust's `escape`" — the
//!   pinned body calls only `escape`, and `conditional_escape` is not reachable
//!   from here.)
//! * There is **no** `normalize_newlines` here: the split is on `\n` only, so a
//!   `\r` is kept. Measured `"a\r\nb"` → `'1. a\r\n2. b'`.
//!
//! Measured (pinned 6.0.9), on 12 lines `line 1`…`line 12`:
//! `'01. line 1\n02. line 2\n…\n12. line 12'`; the same with `autoescape: False`;
//! `"<a>\n<b>"` autoescape on → `'1. &lt;a&gt;\n2. &lt;b&gt;'`;
//! `mark_safe("<a>\n<b>")` on → `'1. <a>\n2. <b>'`; `"only"` → `'1. only'`;
//! `""` → `'1. '` (one empty line).
//!
//! ## Why any argument at all is refused
//!
//! Same collision as `linebreaks`/`linebreaksbr`: the second parameter is named
//! `autoescape`, Django's loop passes it as a keyword (`base.py:813`), so any
//! positional argument binds twice. Measured for `5`, `"x"`, `None`, `""`:
//!
//! ```text
//! linenumbers -> builtins.TypeError: linenumbers() got multiple values for argument 'autoescape'
//! ```
//!
//! The callable's name is the registered name (`linenumbers`).

use crate::filters::host::FilterCall;
use crate::filters::html_escape::html_escape;
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// The `TypeError` CPython's call binding raises for the `autoescape` collision.
fn autoescape_collision() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "linenumbers",
        "linenumbers() got multiple values for argument 'autoescape'",
    )
}

/// `linenumbers`'s body: number each `\n`-split line, escaping when the branch
/// says so, and `mark_safe` the join.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    if arg.is_some() {
        return Err(autoescape_collision());
    }
    // `not autoescape or isinstance(value, SafeData)` → escape only when the loop
    // asked for it *and* the value is not safe. Rust's `&&` short-circuits as
    // Python's `and` does, so `is_safe` is not asked when `call.autoescape` is
    // false — the negated condition, in the same evaluation order.
    let escaped = call.autoescape && !call.host.is_safe(&value);
    let text = call.host.to_str(&value)?;
    let lines: Vec<&str> = text.split('\n').collect();
    // `width = str(len(str(len(lines))))` — the number of digits of the line
    // count, which is what the `%0Nd` format pads to.
    let width = lines.len().to_string().len();
    let mut numbered = Vec::with_capacity(lines.len());
    for (index, line) in lines.iter().enumerate() {
        let rendered = if escaped {
            html_escape(line)
        } else {
            (*line).to_string()
        };
        numbered.push(format!(
            "{:0width$}. {}",
            index + 1,
            rendered,
            width = width
        ));
    }
    Ok(Value::Safe(numbered.join("\n").into()))
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

    fn host(text: &str, safe: bool) -> TestFilterHost {
        TestFilterHost {
            to_str: Some(text.to_string()),
            is_safe: Some(safe),
            ..TestFilterHost::default()
        }
    }

    /// Any argument is refused, and the refusal is before the seam.
    #[test]
    fn any_argument_is_refused_with_the_call_collision() {
        let h = TestFilterHost::default();
        let error = run(
            Value::Str("a\nb".into()),
            Some(Value::None),
            &call(&h, true),
        )
        .unwrap_err();
        match &error.kind {
            ErrorKind::FilterHost(host_error) => {
                assert_eq!(host_error.kind, PyErrorKind::TypeError);
                assert_eq!(host_error.operation, "linenumbers");
                assert_eq!(
                    host_error.message,
                    "linenumbers() got multiple values for argument 'autoescape'"
                );
            }
            other => panic!("an argument must raise the call collision, got {other:?}"),
        }
        assert!(h.calls().is_empty());
    }

    /// **The zero padding is the number of digits of the line count.** 12 lines
    /// pad to width 2 and 100 to width 3; a body that hardcoded a width would
    /// answer `'1. line 1'`.
    #[test]
    fn the_width_is_the_digit_count_of_the_line_count() {
        let twelve = (1..=12)
            .map(|i| format!("line {i}"))
            .collect::<Vec<_>>()
            .join("\n");
        let expected = (1..=12)
            .map(|i| format!("{i:02}. line {i}"))
            .collect::<Vec<_>>()
            .join("\n");
        let h = host(&twelve, false);
        assert_eq!(
            run(Value::Str(twelve.clone().into()), None, &call(&h, true)).unwrap(),
            Value::Safe(expected.into())
        );

        let hundred = (1..=100)
            .map(|i| format!("line {i}"))
            .collect::<Vec<_>>()
            .join("\n");
        let hundred_expected = (1..=100)
            .map(|i| format!("{i:03}. line {i}"))
            .collect::<Vec<_>>()
            .join("\n");
        let h = host(&hundred, false);
        assert_eq!(
            run(Value::Str(hundred.clone().into()), None, &call(&h, true)).unwrap(),
            Value::Safe(hundred_expected.into())
        );
    }

    /// One line has width 1; the empty string is a single empty line.
    #[test]
    fn the_single_line_rows() {
        for (input, expected) in [("only", "1. only"), ("", "1. ")] {
            let h = host(input, false);
            assert_eq!(
                run(Value::Str(input.into()), None, &call(&h, true)).unwrap(),
                Value::Safe(expected.into()),
                "linenumbers({input:?})"
            );
        }
    }

    /// The escape branch and the `SafeData` short circuit, per line.
    #[test]
    fn it_escapes_per_line_unless_the_value_is_safe() {
        let h = host("<a>\n<b>", false);
        assert_eq!(
            run(Value::Str("<a>\n<b>".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("1. &lt;a&gt;\n2. &lt;b&gt;".into())
        );

        let h = host("<a>\n<b>", true);
        assert_eq!(
            run(Value::Str("<a>\n<b>".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("1. <a>\n2. <b>".into())
        );
        h.assert_called("is_safe");

        let h = host("<a>\n<b>", false);
        assert_eq!(
            run(Value::Str("<a>\n<b>".into()), None, &call(&h, false)).unwrap(),
            Value::Safe("1. <a>\n2. <b>".into())
        );
    }

    /// **No `normalize_newlines` here**, unlike `linebreaks`/`linebreaksbr`: the
    /// `\r` is kept because the split is on `\n` alone.
    #[test]
    fn a_carriage_return_is_kept() {
        let h = host("a\r\nb", false);
        assert_eq!(
            run(Value::Str("a\r\nb".into()), None, &call(&h, true)).unwrap(),
            Value::Safe("1. a\r\n2. b".into())
        );
    }
}
