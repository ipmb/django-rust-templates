//! `unordered_list` — `defaultfilters.py:669-737`.
//!
//! The four pieces Rust owns are the ones that are *not* a Python protocol: the
//! **pairwise walk** (`["a", ["b"], "c"]` becomes `("a", ["b"])`, `("c", None)`),
//! **`StopIteration`-as-loop-end**, the **two output formats**, and the **recursion**.
//!
//! **The nesting decision is [`FilterHost::as_nested_sequence`], not `iter_values`**,
//! because `walk_items` asks two questions together: `isinstance(next_item, (list,
//! tuple, types.GeneratorType))` **and** "does `iter()` succeed". A `dict` iterates but
//! is not in the triple, so it renders flat (measured); a tuple *is*, so it nests. The
//! whole value goes through [`FilterHost::iter_values`] (`list(v)`), so
//! `unordered_list(item_generator())` renders the generator's items.
//!
//! **The escaper is `conditional_escape` or the identity**, through
//! [`FilterHost::to_str`] either way, and the output is `mark_safe`d.
//! `"%s" % escaper(item)` stringifies, so an object's `__str__` renders, and a
//! `SafeString` element is not double-escaped.
//!
//! **`if children:` tests the container *object*, not `!children.is_empty()`.** A
//! nested empty **generator** is truthy and builds an empty `<ul>` (measured), while a
//! nested empty *list* gets none — so [`NestedSequence`] carries `truthy`, evaluated on
//! the original object, and this body tests that.

use crate::filters::host::{FilterCall, NestedSequence};
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// One `walk_items` pair: an item, and the nested sequence under it, if any (`None`
/// when the next element was a plain item).
type WalkPair = (Value, Option<NestedSequence>);

/// `walk_items` — the pairwise walk. A plain `next_item` is yielded with `None` and
/// becomes the new item; a nested one is yielded with its children and the item
/// *after* it is read next; running out of items at any `next()` ends the walk.
fn walk_items(items: &[Value], call: &FilterCall<'_>) -> Result<Vec<WalkPair>, Error> {
    let mut pairs = Vec::new();
    if items.is_empty() {
        // `next()` raised `StopIteration` immediately.
        return Ok(pairs);
    }
    let mut index = 0usize;
    let mut item = items[index].clone();
    index += 1;
    loop {
        if index >= items.len() {
            // The next `next()` raised `StopIteration`.
            pairs.push((item, None));
            break;
        }
        let next_item = items[index].clone();
        index += 1;
        match call.host.as_nested_sequence(&next_item)? {
            Some(children) => {
                pairs.push((item, Some(children)));
                if index >= items.len() {
                    // Running out here ends the generator with no further pair.
                    break;
                }
                item = items[index].clone();
                index += 1;
            }
            None => {
                pairs.push((item, None));
                item = next_item;
            }
        }
    }
    Ok(pairs)
}

/// The escaper applied to one item, then stringified: `"%s" % escaper(item)`. With
/// autoescape, `to_str(conditional_escape(item))`; without, a bare `to_str(item)`.
fn escape_item(item: &Value, call: &FilterCall<'_>) -> Result<String, Error> {
    if call.autoescape {
        let escaped = call.host.conditional_escape(item)?;
        return call.host.to_str(&escaped);
    }
    call.host.to_str(item)
}

/// `list_formatter` — `tabs` starts at 1, the recursion adds one per level.
/// `pub(crate)` so a unit test can assert the Rust-owned output format and recursion
/// directly, since `run`'s `mark_safe` makes the text unobservable through a scripted
/// host.
pub(crate) fn list_formatter(
    items: &[Value],
    tabs: usize,
    call: &FilterCall<'_>,
) -> Result<String, Error> {
    let indent = "\t".repeat(tabs);
    let mut output = Vec::new();
    for (item, children) in walk_items(items, call)? {
        let mut sublist = String::new();
        // `if children:` — the **object's** truthiness, which the host evaluated
        // before materialising. Not `!children.items.is_empty()`: see [`NestedSequence`].
        if let Some(children) = children {
            if children.truthy {
                let inner = list_formatter(&children.items, tabs + 1, call)?;
                sublist = format!("\n{indent}<ul>\n{inner}\n{indent}</ul>\n{indent}");
            }
        }
        output.push(format!(
            "{indent}<li>{}{sublist}</li>",
            escape_item(&item, call)?
        ));
    }
    Ok(output.join("\n"))
}

/// The `base.py:813` collision: the filter's own second parameter is named
/// `autoescape`, and Django's loop passes `autoescape` by keyword **before** the
/// positional argument, so *any* argument raises before the body is entered. Measured
/// over an `int`, `None`, `str`, `float`, `bool` and a resolved variable: one message,
/// naming the filter's own `def` name (which is also its registered name).
fn argument_collision() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "unordered_list",
        "unordered_list() got multiple values for argument 'autoescape'",
    )
}

/// `unordered_list`'s body: format, then `mark_safe`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // The argument is unreachable in Django, so it is unreachable here too; the
    // corpus row `unordered_list/argument` pins the refusal.
    if arg.is_some() {
        return Err(argument_collision());
    }
    // A non-iterable value raises here (`unordered_list(5)` is a `TypeError`) and,
    // with no `try` around it, escapes.
    let items = call.host.iter_values(&value)?;
    let formatted = list_formatter(&items, 1, call)?;
    Ok(call.host.mark_safe(Value::Str(formatted.into())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filters::host::TestFilterHost;

    fn call(host: &TestFilterHost, autoescape: bool) -> FilterCall<'_> {
        FilterCall {
            host,
            autoescape,
            use_tz: false,
        }
    }

    /// A plain-item host whose `to_str` answers `"ESCAPED"`, distinguishable from any
    /// item's own text, so a body that rendered the raw `Value` instead of the host's
    /// stringified escaper would fail every format assertion below.
    fn plain_host(text: &str) -> TestFilterHost {
        TestFilterHost {
            as_nested_sequence: Some(None),
            to_str: Some(text.to_string()),
            conditional_escape: Some(Value::Safe(text.into())),
            ..TestFilterHost::default()
        }
    }

    /// The flat format: `\t<li>…</li>` joined by `\n`, two items, one tab.
    #[test]
    fn the_flat_format_is_tab_indented_lis() {
        let h = plain_host("ESCAPED");
        assert_eq!(
            list_formatter(
                &[Value::Str("a".into()), Value::Str("b".into())],
                1,
                &call(&h, true)
            )
            .unwrap(),
            "\t<li>ESCAPED</li>\n\t<li>ESCAPED</li>"
        );
    }

    /// The nested format: the child `<ul>` is indented one tab deeper and the
    /// parent's `</li>` closes after it.
    #[test]
    fn a_nested_sequence_recurses_into_a_sublist() {
        let h = TestFilterHost {
            as_nested_sequence: Some(Some(NestedSequence::sized(vec![Value::Str(
                "child".into(),
            )]))),
            to_str: Some("ESCAPED".to_string()),
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        let items = [
            Value::Str("a".into()),
            Value::List(vec![Value::Str("child".into())]),
        ];
        assert_eq!(
            list_formatter(&items, 1, &call(&h, true)).unwrap(),
            "\t<li>ESCAPED\n\t<ul>\n\t\t<li>ESCAPED</li>\n\t</ul>\n\t</li>"
        );
    }

    /// An **empty** nested sequence is falsy, so it gets no `<ul>` (measured
    /// `unordered_list(["a", []])` is `"\t<li>a</li>"`).
    #[test]
    fn an_empty_child_sequence_gets_no_sublist() {
        let h = TestFilterHost {
            as_nested_sequence: Some(Some(NestedSequence::sized(vec![]))),
            to_str: Some("ESCAPED".to_string()),
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        let items = [Value::Str("a".into()), Value::List(vec![])];
        assert_eq!(
            list_formatter(&items, 1, &call(&h, true)).unwrap(),
            "\t<li>ESCAPED</li>"
        );
    }

    /// **A nested generator is truthy even when it yields nothing**, because Django's
    /// `if children:` tests the object — the one case an emptiness check on the items
    /// cannot express, and the reason [`NestedSequence`] carries `truthy` separately.
    /// Measured: an *empty* `<ul>`, where the empty *list* above gets none.
    #[test]
    fn a_nested_generator_is_truthy_even_when_empty() {
        let h = TestFilterHost {
            as_nested_sequence: Some(Some(NestedSequence {
                items: vec![],
                truthy: true,
            })),
            to_str: Some("ESCAPED".to_string()),
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        let items = [Value::Str("a".into()), Value::List(vec![])];
        assert_eq!(
            list_formatter(&items, 1, &call(&h, true)).unwrap(),
            "\t<li>ESCAPED\n\t<ul>\n\n\t</ul>\n\t</li>"
        );
    }

    /// The `True` escaper is `conditional_escape` **and** `to_str`; the `False` one is
    /// `to_str` alone. The outputs are identical here, so what distinguishes them is
    /// the call pattern: `conditional_escape` must be absent when autoescape is off.
    #[test]
    fn the_escaper_branches_on_autoescape() {
        let h = plain_host("ESCAPED");
        let items = [Value::Str("a".into())];
        assert_eq!(
            list_formatter(&items, 1, &call(&h, true)).unwrap(),
            "\t<li>ESCAPED</li>"
        );
        h.assert_called("conditional_escape");
        h.assert_called("to_str");

        let h = plain_host("ESCAPED");
        assert_eq!(
            list_formatter(&items, 1, &call(&h, false)).unwrap(),
            "\t<li>ESCAPED</li>"
        );
        assert!(
            !h.calls().contains(&"conditional_escape"),
            "autoescape=False must not escape: {:?}",
            h.calls()
        );
        h.assert_called("to_str");
    }

    /// **The walk pairs a nested sequence with its parent and skips it as an item.**
    /// `["a", ["b"], "c"]` is two pairs, and `as_nested_sequence` is consulted only for
    /// the one element that is not the last.
    #[test]
    fn the_walk_pairs_a_child_sequence_with_its_parent() {
        let h = TestFilterHost {
            as_nested_sequence: Some(Some(NestedSequence::sized(vec![Value::Str(
                "child".into(),
            )]))),
            to_str: Some("ESCAPED".to_string()),
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            ..TestFilterHost::default()
        };
        let items = [
            Value::Str("a".into()),
            Value::List(vec![Value::Str("child".into())]),
            Value::Str("c".into()),
        ];
        assert_eq!(
            list_formatter(&items, 1, &call(&h, true)).unwrap(),
            "\t<li>ESCAPED\n\t<ul>\n\t\t<li>ESCAPED</li>\n\t</ul>\n\t</li>\n\
             \t<li>ESCAPED</li>"
        );
        assert_eq!(
            h.calls()
                .iter()
                .filter(|n| **n == "as_nested_sequence")
                .count(),
            1,
            "the last item is never asked: {:?}",
            h.calls()
        );
    }

    /// **The whole value is `iter_values`, not an index walk.** The generator case
    /// (`test_unordered_list.py:143`) is why: `run` iterates the value as a whole, so
    /// the host is asked for `iter_values` and never for `get_index`.
    #[test]
    fn the_whole_value_is_iterated_and_not_indexed() {
        let h = TestFilterHost {
            iter_values: Some(vec![Value::Str("a".into()), Value::Str("b".into())]),
            as_nested_sequence: Some(None),
            to_str: Some("ESCAPED".to_string()),
            conditional_escape: Some(Value::Safe("ESCAPED".into())),
            mark_safe: Some(Value::Safe("MARKED".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h, true)).unwrap(),
            Value::Safe("MARKED".into())
        );
        h.assert_called("iter_values");
        h.assert_called("as_nested_sequence");
        h.assert_called("mark_safe");
        assert!(
            !h.calls().contains(&"get_index"),
            "the value is iterated, never indexed: {:?}",
            h.calls()
        );
    }

    /// The empty list is `""` and still **marked safe** (measured), so `mark_safe`
    /// runs even when there is nothing to format.
    #[test]
    fn the_empty_list_is_the_empty_string_marked_safe() {
        let h = TestFilterHost {
            iter_values: Some(vec![]),
            to_str: Some("unused".to_string()),
            mark_safe: Some(Value::Safe("".into())),
            ..TestFilterHost::default()
        };
        assert_eq!(
            run(Value::List(vec![]), None, &call(&h, true)).unwrap(),
            Value::Safe("".into())
        );
        h.assert_called("mark_safe");
        assert!(
            !h.calls().contains(&"as_nested_sequence"),
            "no items means no walk: {:?}",
            h.calls()
        );
    }

    /// **A non-iterable value escapes** (measured `unordered_list(5)` is a `TypeError`):
    /// there is no `try` around `list_formatter(value)`, and the value must not reach
    /// `mark_safe`.
    #[test]
    fn a_non_iterable_value_escapes() {
        let h = TestFilterHost {
            to_str: Some("unused".to_string()),
            ..TestFilterHost::default()
        };
        h.raise_on("iter_values", crate::parser::error::PyErrorKind::TypeError);
        let error = run(Value::Int(5), None, &call(&h, true)).unwrap_err();
        assert!(
            matches!(
                &error.kind,
                crate::parser::error::ErrorKind::FilterHost(e)
                    if e.kind == crate::parser::error::PyErrorKind::TypeError
            ),
            "unordered_list(5) must raise TypeError: {error:?}"
        );
        assert!(
            !h.calls().contains(&"mark_safe"),
            "an iterating failure must not mark: {:?}",
            h.calls()
        );
    }
}
