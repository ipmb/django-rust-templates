//! `unordered_list` — `defaultfilters.py:669-743`.
//!
//! ```python
//! @register.filter(is_safe=True, needs_autoescape=True)
//! def unordered_list(value, autoescape=True):
//!     if autoescape:
//!         escaper = conditional_escape
//!     else:
//!         def escaper(x):
//!             return x
//!
//!     def walk_items(item_list):
//!         item_iterator = iter(item_list)
//!         try:
//!             item = next(item_iterator)
//!             while True:
//!                 try:
//!                     next_item = next(item_iterator)
//!                 except StopIteration:
//!                     yield item, None
//!                     break
//!                 if isinstance(next_item, (list, tuple, types.GeneratorType)):
//!                     try:
//!                         iter(next_item)
//!                     except TypeError:
//!                         pass
//!                     else:
//!                         yield item, next_item
//!                         item = next(item_iterator)
//!                         continue
//!                 yield item, None
//!                 item = next_item
//!         except StopIteration:
//!             pass
//!
//!     def list_formatter(item_list, tabs=1):
//!         indent = "\t" * tabs
//!         output = []
//!         for item, children in walk_items(item_list):
//!             sublist = ""
//!             if children:
//!                 sublist = "\n%s<ul>\n%s\n%s</ul>\n%s" % (
//!                     indent,
//!                     list_formatter(children, tabs + 1),
//!                     indent,
//!                     indent,
//!                 )
//!             output.append("%s<li>%s%s</li>" % (indent, escaper(item), sublist))
//!         return "\n".join(output)
//!
//!     return mark_safe(list_formatter(value))
//! ```
//!
//! The largest body in the group, and the four pieces Rust owns are the ones that
//! are *not* a Python protocol: the **pairwise walk** (a list like
//! `["a", ["b"], "c"]` becomes the pairs `("a", ["b"])` and `("c", None)`),
//! **`StopIteration`-as-loop-end** (running out of items after a nested child
//! ends the walk with no pair for a non-existent item), the **two output formats**
//! (tabs and the `<li>`/`<ul>` shape), and the **recursion**. Everything else is
//! the host's.
//!
//! **The nesting decision is [`FilterHost::as_nested_sequence`], not
//! `iter_values`**, because `walk_items` asks two questions that must be answered
//! together: `isinstance(next_item, (list, tuple, types.GeneratorType))` **and**
//! "does `iter()` succeed". A `dict` iterates but is not in the triple, so the
//! `isinstance` decides (measured `unordered_list(["a", {"b": 1}])` renders the
//! dict flat, escaped); a tuple *is* in the triple, so `(["a", ("b", "c")])` nests
//! (measured). The host returns the nested sequence when it nests and `None` when
//! it does not, which is also why the **whole value** is
//! [`FilterHost::iter_values`] (`list(v)`): measured
//! `unordered_list(item_generator())` renders the generator's items.
//!
//! **The escaper is `conditional_escape` or the identity**, and it goes through
//! [`FilterHost::to_str`] either way: `"%s" % escaper(item)` stringifies, so an
//! object whose `__str__` is not its `repr` renders as its `__str__` (measured
//! `test_ulitem`), and with autoescape a `SafeString` element is not
//! double-escaped (`test_unordered_list03`). The output is `mark_safe`d.
//!
//! Measured rows on the pinned tree:
//!
//! ```text
//! unordered_list(["item 1", "item 2"])          -> "\t<li>item 1</li>\n\t<li>item 2</li>"
//! unordered_list(["item 1", ["item 1.1"]])      -> "\t<li>item 1\n\t<ul>\n\t\t<li>item 1.1</li>\n\t</ul>\n\t</li>"
//! unordered_list(["<a>item 1</a>", "item 2"])   -> the escaped form (SafeString)
//! unordered_list([same], autoescape=False)      -> the raw form
//! unordered_list(["a", []])                     -> "\t<li>a</li>"        (empty child -> no <ul>)
//! unordered_list(["a", (x for x in [])])        -> "\t<li>a\n\t<ul>\n\n\t</ul>\n\t</li>"
//! unordered_list([])                            -> ""                    (SafeString)
//! unordered_list(5)                             -> TypeError: 'int' object is not iterable (escapes)
//! ```
//!
//! **`if children:` tests the container *object*, and that is not
//! `!children.is_empty()`.** A generator object is truthy even when it yields
//! nothing, so a nested **empty** generator builds a `<ul>` around nothing
//! (measured above) while a nested empty *list* gets no `<ul>` — the same
//! emptiness, two answers, because the two objects answer `bool()` differently.
//! A materialised `Vec<Value>` cannot tell them apart, so the host's
//! [`NestedSequence`] carries `truthy`, evaluated on the original object, and
//! this body tests that instead of the items' length. The distinction is
//! reachable: `["a", (y for y in [])]` is a legal template value.

use crate::filters::host::{FilterCall, NestedSequence};
use crate::filters::value::Value;
use crate::parser::error::{Error, PyErrorKind};

/// One `walk_items` pair: an item, and the nested sequence that belongs under it
/// if the next element was one (`None` when it was a plain item).
type WalkPair = (Value, Option<NestedSequence>);

/// `walk_items` — the pairwise walk, in Rust. Each `(item, children)` is an item
/// followed by the nested sequence that belongs under it, if any.
///
/// The walk is a straight port of the generator's `try`/`next()` structure: a
/// plain `next_item` is yielded with `None` and becomes the new item, a nested
/// one is yielded with its children and the item *after* it is read next — and
/// running out of items at any of the three `next()` calls is how the walk ends.
fn walk_items(items: &[Value], call: &FilterCall<'_>) -> Result<Vec<WalkPair>, Error> {
    let mut pairs = Vec::new();
    if items.is_empty() {
        // `item = next(item_iterator)` raised `StopIteration` immediately.
        return Ok(pairs);
    }
    let mut index = 0usize;
    let mut item = items[index].clone();
    index += 1;
    loop {
        if index >= items.len() {
            // `next_item = next(item_iterator)` raised `StopIteration`.
            pairs.push((item, None));
            break;
        }
        let next_item = items[index].clone();
        index += 1;
        match call.host.as_nested_sequence(&next_item)? {
            Some(children) => {
                pairs.push((item, Some(children)));
                if index >= items.len() {
                    // `item = next(item_iterator)` raised `StopIteration`: the
                    // generator ends with no further pair.
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

/// The escaper applied to one item, then stringified: `"%s" % escaper(item)`.
/// With autoescape that is `to_str(conditional_escape(item))`; without, a bare
/// `to_str(item)` — which is `str(item)`, so an object's own `__str__` is used.
fn escape_item(item: &Value, call: &FilterCall<'_>) -> Result<String, Error> {
    if call.autoescape {
        let escaped = call.host.conditional_escape(item)?;
        return call.host.to_str(&escaped);
    }
    call.host.to_str(item)
}

/// `list_formatter` — `tabs` starts at 1 and the recursion adds one per level.
/// Module-visible so a unit test can assert the Rust-owned output format (and the
/// recursion) directly, since `run`'s `mark_safe` makes the text unobservable
/// through a scripted host.
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
        // before materialising. Not `!children.items.is_empty()`: a nested
        // generator is truthy even when it yields nothing, and Django builds an
        // empty `<ul>` for it. See [`NestedSequence`].
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

/// The `base.py:813` collision: `unordered_list`'s own second parameter is named
/// `autoescape`, and Django's loop passes `autoescape` by keyword **before** the
/// positional argument, so *any* argument raises before the body is entered.
///
/// Measured on the pinned tree over an `int`, `None`, a `str`, a `float`, a
/// `bool` and a resolved variable: one message, naming the filter's own `def`
/// name (which for `unordered_list` is also its registered name).
fn argument_collision() -> Error {
    Error::filter_host(
        PyErrorKind::TypeError,
        "unordered_list",
        "unordered_list() got multiple values for argument 'autoescape'",
    )
}

/// `unordered_list`'s body: format, then `mark_safe`.
pub fn run(value: Value, arg: Option<Value>, call: &FilterCall<'_>) -> Result<Value, Error> {
    // The argument is unreachable in Django, so it is unreachable here too. It
    // used to be ignored — the body answered where Django raised, which is the
    // wrong direction for a drop-in — and the corpus row
    // `unordered_list/argument` is what pins the refusal.
    if arg.is_some() {
        return Err(argument_collision());
    }
    // `list_formatter(value)` -> `walk_items(value)` -> `iter(value)`. A
    // non-iterable value raises here and, with no `try` around it, escapes
    // (measured `unordered_list(5)` is a `TypeError`).
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

    /// A plain-item host whose `to_str` answers `"ESCAPED"` — distinguishable
    /// from any item's own text, so a body that rendered the raw `Value` instead
    /// of the host's stringified escaper would fail every format assertion below.
    fn plain_host(text: &str) -> TestFilterHost {
        TestFilterHost {
            as_nested_sequence: Some(None),
            to_str: Some(text.to_string()),
            conditional_escape: Some(Value::Safe(text.into())),
            ..TestFilterHost::default()
        }
    }

    /// The flat format, measured shape: `\t<li>…</li>` joined by `\n`. Two items,
    /// one tab. The `"ESCAPED"` text is the escaper's, not the item's.
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

    /// The nested format, measured shape: the child `<ul>` is indented one tab
    /// deeper and the parent's `</li>` closes after it.
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

    /// **A nested generator is truthy even when it yields nothing**, because
    /// Django's `if children:` tests the object — so this is the one case an
    /// emptiness check on the items cannot express, and the reason
    /// [`NestedSequence`] carries `truthy` separately. Measured
    /// `unordered_list(["a", (x for x in [])])` is
    /// `"\t<li>a\n\t<ul>\n\n\t</ul>\n\t</li>"` — an _empty_ `<ul>`, where the
    /// empty *list* above gets none.
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

    /// The `autoescape=True` escaper is `conditional_escape` **and** `to_str`; the
    /// `False` one is `to_str` alone. The outputs are identical here (both hosts
    /// answer the same text), so what distinguishes them is the call pattern:
    /// `conditional_escape` must be absent when autoescape is off.
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

    /// **The walk pairs a nested sequence with its parent and skips it as an
    /// item.** `["a", ["b"], "c"]` is two pairs — `("a", ["b"])` and `("c",
    /// None)` — and `as_nested_sequence` is consulted once, for the one element
    /// that is not the last. A body that treated the nested list as a *plain
    /// item* would call it once too but render three `<li>`s; a body that
    /// iterated the whole value without pairing would never nest.
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
    /// (`test_unordered_list.py:143`) is why: `run` iterates the value as a whole
    /// and never indexes it, so the host is asked for `iter_values` and not
    /// `get_index`.
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

    /// The empty list is `""` and is still **marked safe** (measured: a
    /// `SafeString`), so `mark_safe` runs even when there is nothing to format.
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

    /// **A non-iterable value escapes.** `unordered_list(5)` is `TypeError: 'int'
    /// object is not iterable` (measured) — there is no `try` around
    /// `list_formatter(value)`, and the value must not reach `mark_safe`.
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
