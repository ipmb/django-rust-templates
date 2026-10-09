//! `{% resetcycle [name] %}`.
//!
//! Both paths fail at M2, because nothing populates the registry until
//! `{% cycle %}` (M5) — so `ResetCycleNode` is not reachable from a template
//! yet, and its descriptor shape is pinned by Task 7's corpus only through the
//! materialiser's hand-built fixtures.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::{py_repr_str, split_contents};
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = split_contents(token)?;
    if args.len() > 2 {
        // `args[0]` is safe to `%r` because it is the tag name dispatch matched
        // against the registry, not text from the template — see `py_repr_str`'s
        // bound. Anything reaching here with a non-ASCII byte trips that
        // function's assertion rather than producing Django-incompatible output.
        return Err(Error::syntax(format!(
            "{} tag accepts at most one argument.",
            py_repr_str(&args[0]),
        )));
    }

    if args.len() == 2 {
        let name = &args[1];
        // Django catches `(AttributeError, KeyError)` here: the attribute does
        // not exist until `{% cycle %}` has run, and the name may not be in it.
        // Both mean "no such cycle", so a miss in the map is the whole story —
        // the absent-attribute case is the map being empty.
        //
        // The value is a reference-table *slot*, not a node: the `CycleNode` is
        // Python's and is built at materialisation. `{% cycle %}` reserved the
        // slot, so this resolves to that same object.
        return match parser.named_cycle_nodes.get(name) {
            Some(slot) => {
                Ok(NodeDesc::new("ResetCycleNode").attr("node", AttrValue::NodeSlot(*slot)))
            }
            None => Err(Error::syntax(format!(
                "Named cycle '{name}' does not exist."
            ))),
        };
    }

    match parser.last_cycle_node {
        Some(slot) => Ok(NodeDesc::new("ResetCycleNode").attr("node", AttrValue::NodeSlot(slot))),
        None => Err(Error::syntax("No cycles in template.")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::NoHost;
    use crate::parser::tags::support::{attr, failure};

    #[test]
    fn resetcycle_reports_the_two_empty_registry_cases() {
        assert_eq!(
            failure("{% resetcycle %}").kind,
            ErrorKind::Syntax("No cycles in template.".to_string()),
        );
        assert_eq!(
            failure("{% resetcycle x %}").kind,
            ErrorKind::Syntax("Named cycle 'x' does not exist.".to_string()),
        );
    }

    #[test]
    fn resetcycle_counts_arguments_before_looking_anything_up() {
        // The count check is first, so its message wins even with an empty
        // registry — and it is the only one of the three that quotes the command
        // with `%r`.
        assert_eq!(
            failure("{% resetcycle a b %}").kind,
            ErrorKind::Syntax("'resetcycle' tag accepts at most one argument.".to_string()),
        );
    }

    #[test]
    fn resetcycle_finds_a_named_cycle_the_parser_already_holds() {
        // M2 cannot reach this through a template, so the registry is populated
        // by hand. It is the only check that the *success* shape — and the
        // `AttrValue::NodeSlot` it carries — is what M5 will need: `{% cycle %}`'s
        // Task 6 writes the other end, and `tests/test_ref_table.py` resolves
        // this operand against a real `CycleNode`.
        let mut parser = Parser::new(Lexer::new("{% resetcycle named %}").tokenize(), &NoHost);
        parser.named_cycle_nodes.insert("named".to_string(), 11);
        let nodelist = parser.parse(&[]).unwrap();

        assert_eq!(nodelist.nodes[0].class_name, "ResetCycleNode");
        assert_eq!(attr(&nodelist.nodes[0], "node"), &AttrValue::NodeSlot(11));
    }

    #[test]
    fn resetcycle_falls_back_to_the_last_cycle_node() {
        let mut parser = Parser::new(Lexer::new("{% resetcycle %}").tokenize(), &NoHost);
        parser.last_cycle_node = Some(12);
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(attr(&nodelist.nodes[0], "node"), &AttrValue::NodeSlot(12));
    }
}
