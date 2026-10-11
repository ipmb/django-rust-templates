//! `{% cycle %}` — `defaulttags.cycle` (`defaulttags.py:611-701`).
//!
//! Five shapes, from `args = token.split_contents()` (`defaulttags.py:657`): `len(args) < 2`
//! refuses (`defaulttags.py:659-660`); `== 2` is a name reference (`defaulttags.py:662-671`); `> 4` with
//! `args[-3] == "as"` is `… as name silent` (`defaulttags.py:675-685`); `> 4` with
//! `args[-2] == "as"` is `… as name` (`defaulttags.py:686-688`); otherwise the anonymous form
//! (`defaulttags.py:697-699`).
//!
//! The `len(args) > 4` guard reads as an off-by-one until it fires: `{% cycle a as
//! b %}` has four bits, so it is the anonymous form — a three-value cycle whose
//! middle value is the literal filter `as` (a variable named `as`). Measured. `as`
//! consumes **two** bits, so values are `args[1:-2]` in shapes 3-4 and `args[1:]` in
//! shape 5; and shape 3's error fires only when `args[-3]` is the `as`, so
//! `{% cycle "a" "b" c as nope %}` is shape 4 and *succeeds* with the name `nope`.
//!
//! **Identity is the point.** `{% cycle a b as c %}` returns a `CycleNode` and
//! stores *that same object* under the name (`defaulttags.py:693-696`); a later `{% cycle c %}`
//! returns it again (`defaulttags.py:669-671`). `CycleNode.render` keys `context.render_context`
//! by the node object (`defaulttags.py:104-114`), so two equal-but-distinct nodes get separate
//! iterators and the second mention restarts the sequence. The object does not exist
//! during the parse, so what is stored is a descriptor the materialiser turns into
//! it: the definition form carries `.slot(idx)` and the same descriptor under
//! `named_cycle_nodes[name]`, which shape 2 returns with the *same* slot, so
//! whichever namer materialises first builds the node and the rest find it.
//!
//! Shape 2's early `return` (`defaulttags.py:671`) means `parser._last_cycle_node = node` (`defaulttags.py:700`)
//! is **not** reached. Measured: with
//! `{% cycle 'a' 'b' as c %}{% cycle d e as f %}{% cycle c %}{% resetcycle %}`, the
//! reset targets the `f` definition, not the `c` reference. A port that fell through
//! to the shared tail would target the last *mention* instead.
//!
//! Values are compiled filters — `[parser.compile_filter(arg) for arg in …]` in both
//! branches (`defaulttags.py:692`, `defaulttags.py:698`) — so `{% cycle a b %}` holds two `FilterExpression`s and
//! nothing resolves them at parse time.

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
        // Django tests `hasattr(parser, "_named_cycle_nodes")`: the dict is created
        // by the `as` branch below and never emptied, so empty means no `as` ran.
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
        // `parser._named_cycle_nodes[name]` — the *node object* the definition built
        // — so a reference is the definition. A slot-only descriptor is wrong when
        // the definition has not been materialised: a reference inside a
        // `{% partialdef %}` body, materialised mid-parse, whose definition is
        // outside, reaches an empty slot and has no constructor arguments to build
        // from (`TypeError: CycleNode.__init__() missing ... 'cyclevars'`), where
        // Django renders. Echoing the arguments makes whichever namer materialises
        // first build the node; the rest find it in the table and return it.
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

    // Reserved before the values compile: nothing in `compile_filter` allocates a
    // slot today, but the table's contract is reserve-before-use.
    let slot = parser.alloc_slot();

    let node = if as_form {
        let name = args[args.len() - 1].clone();
        let values = compile_values(parser, &args[1..args.len() - 2])?;
        let node = NodeDesc::new("CycleNode")
            .attr("cyclevars", AttrValue::List(values))
            .attr("variable_name", AttrValue::Str(name.clone()))
            .attr("silent", AttrValue::Bool(silent))
            .slot(slot);
        // The descriptor, not the slot: a reference may materialise before this node does.
        parser.named_cycle_nodes.insert(name, node.clone());
        node
    } else {
        let values = compile_values(parser, &args[1..])?;
        NodeDesc::new("CycleNode")
            .attr("cyclevars", AttrValue::List(values))
            // `CycleNode(values)` (`defaulttags.py:699`) leaves both defaults; emitting them keeps
            // the descriptor shape identical across branches.
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
        // `as` consumes two bits, so `{% cycle 'a' 'b' as c %}` has values `['a', 'b']`,
        // not `['a', 'b', 'as']`.
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
        // The `len(args) > 4` guard: `{% cycle a as b %}` never enters the `as` branches,
        // so `as` is one of three compiled values. Measured.
        let node = compile("{% cycle a as b %}");
        assert_eq!(values(&node), vec!["a", "as", "b"]);
        assert_eq!(attr(&node, "variable_name"), &AttrValue::None);

        // A trailing `as` with no name is a value too: `args[-2]` is `c`, not `as`.
        let trailing = compile("{% cycle a b c as %}");
        assert_eq!(values(&trailing), vec!["a", "b", "c", "as"]);
        assert_eq!(attr(&trailing, "variable_name"), &AttrValue::None);
    }

    #[test]
    fn the_name_silent_is_a_name_when_only_one_bit_follows_as() {
        // `args[-3]` is `b`, so this is shape 4 with the name `silent`, not the flag.
        let node = compile("{% cycle a b as silent %}");
        assert_eq!(
            attr(&node, "variable_name"),
            &AttrValue::Str("silent".to_string())
        );
        assert_eq!(attr(&node, "silent"), &AttrValue::Bool(false));
    }

    #[test]
    fn a_name_reference_reuses_the_slot_and_echoes_the_definitions_arguments() {
        // The counting host gives the definition slot 0 and the reference none of its
        // own — the reference must not reserve.
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
        // **The arguments, not an empty attribute list:** a `{% partialdef %}` body
        // materialises mid-parse, so a reference inside one whose definition is outside
        // reaches an empty slot and must still carry arguments to build from.
        assert_eq!(values(reference), vec!["'a'", "'b'"]);
        assert_eq!(
            attr(reference, "variable_name"),
            &AttrValue::Str("c".to_string())
        );
        assert_eq!(attr(reference, "silent"), &AttrValue::Bool(false));
    }

    #[test]
    fn two_definitions_get_two_slots() {
        // Under `NoHost` both would be slot 0, hiding a reused index.
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
        // The early return (`defaulttags.py:671`) is observable: after the `{% cycle c %}` mention, a
        // bare `{% resetcycle %}` still targets the `f` definition, not the last mention.
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
        // The anonymous form also sets `_last_cycle_node` (`defaulttags.py:700`), making
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
        // No `as` form yet, so the registry is absent — the message with no trailing period.
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
        // `args[-3]` is `c`, so `args[-2] == "as"` makes this shape 4: it succeeds with
        // the name `nope`, not the silent-flag error.
        let node = compile("{% cycle \"a\" \"b\" c as nope %}");
        assert_eq!(values(&node), vec!["\"a\"", "\"b\"", "c"]);
        assert_eq!(
            attr(&node, "variable_name"),
            &AttrValue::Str("nope".to_string())
        );
    }
}
