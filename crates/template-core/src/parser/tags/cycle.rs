//! `{% cycle %}` — `defaulttags.cycle` (`defaulttags.py:611-701`).
//!
//! # Five shapes, and the two-bit `as`
//!
//! `args = token.split_contents()` (`defaulttags.py:657`), then:
//!
//! 1. `len(args) < 2` → refuse (`defaulttags.py:659-660`).
//! 2. `len(args) == 2` → a **name reference**: the node the name was bound to,
//!    or refuse (`defaulttags.py:662-671`).
//! 3. `len(args) > 4` and `args[-3] == "as"` → `… as name silent` (`defaulttags.py:675-685`).
//! 4. `len(args) > 4` and `args[-2] == "as"` → `… as name` (`defaulttags.py:686-688`).
//! 5. otherwise → the anonymous form (`defaulttags.py:697-699`).
//!
//! The `len(args) > 4` guard is load-bearing and reads as an off-by-one until
//! it fires: `{% cycle a as b %}` has four bits, so it is **shape 5** — a
//! three-value anonymous cycle whose middle value is the literal filter `as`
//! (`"as"` compiles as a variable named `as`). Measured on the pinned tree.
//!
//! `as` consumes **two** bits, which is why the values are `args[1:-2]` in
//! shapes 3-4 and `args[1:]` in shape 5. And shape 3's error fires only when
//! `args[-3]` is the `as`: `{% cycle "a" "b" c as nope %}` is shape 4 and
//! *succeeds* with the name `nope`, which is the case the plan's draft got
//! backwards.
//!
//! # Identity is the whole point, and it is why this tag needs the table
//!
//! `{% cycle a b as c %}` returns a `CycleNode` **and stores that same object**
//! under the name (`defaulttags.py:693-696`). A later `{% cycle c %}` returns it again
//! (`defaulttags.py:669-671`) — Django's own comment calls it out ("This returns the exact
//! same node on each `{% cycle name %}` call"). `CycleNode.render` keys
//! `context.render_context` by the node object (`defaulttags.py:104-114`), so two
//! equal-but-distinct nodes get separate `itertools.cycle` iterators and the
//! second mention restarts the sequence instead of continuing it.
//!
//! The object does not exist during the parse, so what the parser stores is a
//! **descriptor** the materialiser will turn into it: the definition form
//! carries `.slot(idx)` and `named_cycle_nodes[name] = ` that descriptor, and
//! shape 2 returns the same descriptor with the *same* slot, which the
//! materialiser resolves to the object already there — or, if it gets there
//! first, builds from the echoed arguments (design spec §5.5;
//! `tests/test_ref_table.py` pins the wire form).
//!
//! # Shape 2 returns early, and that is observable
//!
//! `defaulttags.py:671` is a `return` inside the `len(args) == 2` branch, so
//! `parser._last_cycle_node = node` (`defaulttags.py:700`) is **not** reached. Measured:
//!
//! ```text
//! {% cycle 'a' 'b' as c %}{% cycle d e as f %}{% cycle c %}{% resetcycle %}
//! →  the ResetCycleNode targets the f definition, not the c reference
//! ```
//!
//! A port that fell through to the shared tail would retarget `{% resetcycle %}`
//! at the last *mention* instead of the last *definition*. Both name the same
//! object in the common case, which is exactly why the case above is the one
//! worth writing down.
//!
//! # The values are filters
//!
//! `[parser.compile_filter(arg) for arg in …]` in both branches
//! (`defaulttags.py:692`, `defaulttags.py:698`)
//! — so `{% cycle a b %}` holds two `FilterExpression`s and nothing resolves
//! them at parse time.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let mut args = split_contents(token)?;

    if args.len() < 2 {
        // No `%s`: this message names no argument.
        return Err(Error::syntax("'cycle' tag requires at least two arguments"));
    }

    if args.len() == 2 {
        let name = args[1].clone();
        // Django tests `hasattr(parser, "_named_cycle_nodes")` first: the dict is
        // created lazily by the `as` branch below, so "absent" means no `as`
        // form has run in this template. The map is created by the same branch
        // that fills it and never emptied, so empty is that case.
        if parser.named_cycle_nodes.is_empty() {
            // The quotes are literal in the format string; `%s` adds none.
            return Err(Error::syntax(format!(
                "No named cycles in template. '{name}' is not defined",
            )));
        }
        let Some(definition) = parser.named_cycle_nodes.get(&name).cloned() else {
            // No trailing period, unlike `{% resetcycle %}`'s. Both are Django's.
            return Err(Error::syntax(format!(
                "Named cycle '{name}' does not exist"
            )));
        };
        // **The definition's own descriptor, not a bare slot.** Django returns
        // `parser._named_cycle_nodes[name]` — the *node object* the definition
        // built — so a reference is the definition, and modelling it as a slot
        // alone says "look up what is already there", which is true only if the
        // definition has been materialised.
        //
        // It has not, when the reference is inside a `{% partialdef %}` body and
        // the definition is outside it: `register_partial_def` materialises the
        // body *during the parse*, so the slot is empty, and a slot-only
        // descriptor then has no constructor arguments to build from —
        // `TypeError: CycleNode.__init__() missing 1 required positional
        // argument: 'cyclevars'`, on `{% cycle 'a' 'b' as c %}{% partialdef p
        // %}{% cycle c %}{% endpartialdef %}{% partial p %}`, a template Django
        // renders. Echoing the arguments makes whichever namer materialises
        // first the one that builds the node; the rest find it in the table and
        // return it, so `CycleNode.render`'s render-context key is still one
        // object and the sequence still advances once.
        //
        // Nothing is recompiled here: the values are the definition's already
        // compiled `AttrValue`s, cloned. The early return
        // (`defaulttags.py:671`) still does not touch `last_cycle_node` — see
        // the module docs.
        return Ok(definition);
    }

    let mut as_form = false;
    let mut silent = false;

    if args.len() > 4 {
        if args[args.len() - 3] == "as" {
            if args[args.len() - 1] != "silent" {
                return Err(Error::syntax(format!(
                    "Only 'silent' flag is allowed after cycle's name, not '{}'.",
                    args[args.len() - 1],
                )));
            }
            as_form = true;
            silent = true;
            // `args = args[:-1]` (`defaulttags.py:685`), so the name is read *after* this.
            args.pop();
        } else if args[args.len() - 2] == "as" {
            as_form = true;
        }
    }

    // Reserved before the values are compiled. Nothing in `compile_filter`
    // allocates a slot, so the order is not observable today — but the table's
    // contract is reserve-before-use, and a value that one day did allocate
    // would otherwise take this node's index.
    let slot = parser.alloc_slot();

    let node = if as_form {
        let name = args[args.len() - 1].clone();
        let values = compile_values(parser, &args[1..args.len() - 2])?;
        let node = NodeDesc::new("CycleNode")
            .attr("cyclevars", AttrValue::List(values))
            .attr("variable_name", AttrValue::Str(name.clone()))
            .attr("silent", AttrValue::Bool(silent))
            .slot(slot);
        // The descriptor, not the slot: a reference is "this node", and it may
        // be materialised before this line's node is (see the reference arm).
        parser.named_cycle_nodes.insert(name, node.clone());
        node
    } else {
        let values = compile_values(parser, &args[1..])?;
        NodeDesc::new("CycleNode")
            .attr("cyclevars", AttrValue::List(values))
            // `CycleNode(values)` (`defaulttags.py:699`) leaves both defaults, emitted rather
            // than omitted so the materialiser passes `variable_name=None` and
            // the descriptor shape does not vary by branch.
            .attr("variable_name", AttrValue::None)
            .attr("silent", AttrValue::Bool(false))
            .slot(slot)
    };

    parser.last_cycle_node = Some(slot);
    Ok(node)
}

/// `[parser.compile_filter(arg) for arg in …]`, shared by the two value lists.
fn compile_values(parser: &mut Parser<'_>, bits: &[String]) -> Result<Vec<AttrValue>, Error> {
    let mut values = Vec::with_capacity(bits.len());
    for bit in bits {
        values.push(parser.compile_filter(bit)?);
    }
    Ok(values)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, parse_with, CountingHost};

    fn values(node: &NodeDesc) -> Vec<String> {
        let AttrValue::List(values) = attr(node, "cyclevars") else {
            panic!("cyclevars is a List");
        };
        values
            .iter()
            .map(|value| {
                let AttrValue::FilterExpr(expression) = value else {
                    panic!("each value is a FilterExpr");
                };
                expression.token.clone()
            })
            .collect()
    }

    #[test]
    fn the_anonymous_form_is_a_value_list_and_two_defaults() {
        let node = compile("{% cycle 'a' 'b' %}");
        assert_eq!(node.class_name, "CycleNode");
        assert_eq!(values(&node), vec!["'a'", "'b'"]);
        assert_eq!(attr(&node, "variable_name"), &AttrValue::None);
        assert_eq!(attr(&node, "silent"), &AttrValue::Bool(false));
    }

    #[test]
    fn the_as_form_takes_its_values_before_the_two_bit_as() {
        // `as` consumes two bits, so `{% cycle 'a' 'b' as c %}` has the values
        // `['a', 'b']` — not `['a', 'b', 'as']`.
        let node = compile("{% cycle 'a' 'b' as c %}");
        assert_eq!(values(&node), vec!["'a'", "'b'"]);
        assert_eq!(
            attr(&node, "variable_name"),
            &AttrValue::Str("c".to_string())
        );
        assert_eq!(attr(&node, "silent"), &AttrValue::Bool(false));
    }

    #[test]
    fn silent_is_only_reachable_through_the_three_bit_tail() {
        let node = compile("{% cycle 'a' 'b' as c silent %}");
        assert_eq!(values(&node), vec!["'a'", "'b'"]);
        assert_eq!(
            attr(&node, "variable_name"),
            &AttrValue::Str("c".to_string())
        );
        assert_eq!(attr(&node, "silent"), &AttrValue::Bool(true));
    }

    #[test]
    fn four_bits_is_the_anonymous_form_even_with_an_as_in_it() {
        // The `len(args) > 4` guard: `{% cycle a as b %}` never enters the `as`
        // branches, so `as` is one of three compiled values. Measured.
        let node = compile("{% cycle a as b %}");
        assert_eq!(values(&node), vec!["a", "as", "b"]);
        assert_eq!(attr(&node, "variable_name"), &AttrValue::None);

        // And a trailing `as` with no name is a value too (`len(args) == 5`,
        // `args[-2]` is `c`, not `as`).
        let trailing = compile("{% cycle a b c as %}");
        assert_eq!(values(&trailing), vec!["a", "b", "c", "as"]);
        assert_eq!(attr(&trailing, "variable_name"), &AttrValue::None);
    }

    #[test]
    fn the_name_silent_is_a_name_when_only_one_bit_follows_as() {
        // `{% cycle a b as silent %}`: `args[-3]` is `b`, so this is shape 4
        // with the name `silent`, not the silent flag. Measured.
        let node = compile("{% cycle a b as silent %}");
        assert_eq!(
            attr(&node, "variable_name"),
            &AttrValue::Str("silent".to_string())
        );
        assert_eq!(attr(&node, "silent"), &AttrValue::Bool(false));
    }

    #[test]
    fn a_name_reference_reuses_the_slot_and_echoes_the_definitions_arguments() {
        // Two nodes, so the counting host gives the definition slot 0 and the
        // reference none of its own — the reference must not reserve.
        let host = CountingHost::default();
        let nodelist = parse_with("{% cycle 'a' 'b' as c %}{% cycle c %}", &host);

        assert_eq!(nodelist.nodes.len(), 2);
        assert_eq!(nodelist.nodes[0].slot, Some(0));
        assert_eq!(values(&nodelist.nodes[0]), vec!["'a'", "'b'"]);

        let reference = &nodelist.nodes[1];
        assert_eq!(reference.class_name, "CycleNode");
        assert_eq!(
            reference.slot,
            Some(0),
            "the reference names the definition's slot"
        );
        // **The arguments, not an empty attribute list.** The row this replaced
        // asserted `attrs.is_empty()` with the reason "the object already
        // exists" — and M13 falsified the reason, not the assertion: a
        // `{% partialdef %}` body is materialised mid-parse, so a reference
        // inside one whose definition is outside reaches an *empty* slot, and a
        // descriptor with nothing to build from is a `TypeError` there where
        // Django renders. Django's map holds the node itself, so a reference
        // *is* the definition; a descriptor carrying the definition's arguments
        // is what that looks like one step earlier.
        assert_eq!(values(reference), vec!["'a'", "'b'"]);
        assert_eq!(
            attr(reference, "variable_name"),
            &AttrValue::Str("c".to_string())
        );
        assert_eq!(attr(reference, "silent"), &AttrValue::Bool(false));
    }

    #[test]
    fn two_definitions_get_two_slots() {
        // Under `NoHost` both would be slot 0 and this test could not tell a
        // correct implementation from one that reused the index.
        let host = CountingHost::default();
        let nodelist = parse_with("{% cycle a b as c %}{% cycle d e as f %}", &host);
        assert_eq!(nodelist.nodes[0].slot, Some(0));
        assert_eq!(nodelist.nodes[1].slot, Some(1));
        assert_eq!(
            attr(&nodelist.nodes[0], "variable_name"),
            &AttrValue::Str("c".to_string())
        );
        assert_eq!(
            attr(&nodelist.nodes[1], "variable_name"),
            &AttrValue::Str("f".to_string())
        );
    }

    #[test]
    fn a_name_reference_does_not_move_last_cycle_node() {
        // The early return (`defaulttags.py:671`) is observable: after the `{% cycle c %}`
        // mention, a bare `{% resetcycle %}` still targets the *f* definition.
        // A port that fell through to `last_cycle_node = node` would target the
        // last mention instead — the same object here only by luck.
        let host = CountingHost::default();
        let nodelist = parse_with(
            "{% cycle a b as c %}{% cycle d e as f %}{% cycle c %}{% resetcycle %}",
            &host,
        );
        assert_eq!(nodelist.nodes.len(), 4);
        assert_eq!(
            nodelist.nodes[2].slot,
            Some(0),
            "the c mention reuses c's slot"
        );
        assert_eq!(
            attr(&nodelist.nodes[3], "node"),
            &AttrValue::NodeSlot(1),
            "a bare resetcycle targets the last *definition* (f), not the last mention",
        );
    }

    #[test]
    fn an_anonymous_cycle_is_still_the_last_cycle_node() {
        // Shape 5 also sets `_last_cycle_node` (`defaulttags.py:700`), which is what makes
        // `{% cycle 'a' 'b' %}{% resetcycle %}` legal.
        let host = CountingHost::default();
        let nodelist = parse_with("{% cycle 'a' 'b' %}{% resetcycle %}", &host);
        assert_eq!(attr(&nodelist.nodes[1], "node"), &AttrValue::NodeSlot(0));
    }

    #[test]
    fn the_four_messages() {
        assert_eq!(
            failure("{% cycle %}").kind,
            ErrorKind::Syntax("'cycle' tag requires at least two arguments".to_string()),
        );
        // No `as` form yet, so the registry itself is absent — the first of the
        // two "unknown name" messages, and the one with no trailing period.
        assert_eq!(
            failure("{% cycle foo %}").kind,
            ErrorKind::Syntax("No named cycles in template. 'foo' is not defined".to_string(),),
        );
        assert_eq!(
            failure("{% cycle a b as c %}{% cycle c2 %}").kind,
            ErrorKind::Syntax("Named cycle 'c2' does not exist".to_string()),
        );
        // `args[-3]` must be the `as` for this branch to be reachable at all.
        assert_eq!(
            failure("{% cycle a b as c d %}").kind,
            ErrorKind::Syntax(
                "Only 'silent' flag is allowed after cycle's name, not 'd'.".to_string(),
            ),
        );
    }

    #[test]
    fn a_name_in_the_middle_of_the_tail_is_a_value_not_the_silent_error() {
        // The plan's draft claimed this raised the silent-flag error. Measured:
        // `args[-3]` is `c`, so `args[-2] == "as"` makes it shape 4 — it
        // succeeds with the name `nope`, values `['"a"', '"b"', 'c']`.
        let node = compile("{% cycle \"a\" \"b\" c as nope %}");
        assert_eq!(values(&node), vec!["\"a\"", "\"b\"", "c"]);
        assert_eq!(
            attr(&node, "variable_name"),
            &AttrValue::Str("nope".to_string())
        );
    }
}
