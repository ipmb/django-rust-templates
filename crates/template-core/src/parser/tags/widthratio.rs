//! `{% widthratio %}` — `defaulttags.widthratio` (`defaulttags.py:1610-1650`).
//!
//! # All three arguments are `compile_filter`ed, including the width
//!
//! `parser.compile_filter(max_width)` (`:1648`), not `int(max_width)`. So
//! `{% widthratio a b 100|add:1 %}` parses, and the number 100 is a *runtime*
//! value like the other two — `WidthRatioNode.render` does the `int()` at
//! render time and raises `TemplateSyntaxError("widthratio final argument must
//! be a number")` there (`:541-548`), which is why nothing here validates it.
//!
//! # Arity is exact, and the `else` is the surprising branch
//!
//! Four bits (the tag plus three) or six (the tag plus `as` and a name);
//! anything else is `"widthratio takes at least three arguments"` (`:1643`).
//! Two measured cases make the shape of that branch worth stating rather than
//! deriving:
//!
//! - `{% widthratio a b c d %}` — five bits — takes the `else`, so the message
//!   says "at least three" about a tag that has four arguments.
//! - `{% widthratio a b c d e %}` — **six** bits — takes the `as` branch and
//!   reports `Invalid syntax in widthratio tag. Expecting 'as' keyword`,
//!   because `bits[4]` is `d`.
//!
//! The plan for this task had the second one down as the first. Counting the
//! tag name is the whole difference, and it is the mistake the tests below pin.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // `bits[0]` is the tag name and is never compiled; Django unpacks it into
    // an unused `tag` both times (`:1634-1637`).
    let bits = split_contents(token)?;

    let asvar = match bits.len() {
        4 => AttrValue::None,
        6 => {
            if bits[4] != "as" {
                return Err(Error::syntax(
                    "Invalid syntax in widthratio tag. Expecting 'as' keyword",
                ));
            }
            AttrValue::Str(bits[5].clone())
        }
        _ => return Err(Error::syntax("widthratio takes at least three arguments")),
    };

    Ok(NodeDesc::new("WidthRatioNode")
        .attr("val_expr", parser.compile_filter(&bits[1])?)
        .attr("max_expr", parser.compile_filter(&bits[2])?)
        .attr("max_width", parser.compile_filter(&bits[3])?)
        // Emitted even when absent, for `{% firstof %}`'s reason: Django
        // passes `asvar=asvar` on both paths, so the attribute always exists
        // and `describe` compares `vars(node)`.
        .attr("asvar", asvar))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    fn source_of(value: &AttrValue) -> &str {
        let AttrValue::FilterExpr(expression) = value else {
            panic!("a widthratio operand is a FilterExpr");
        };
        expression.token.as_str()
    }

    #[test]
    fn widthratio_compiles_all_three_operands() {
        let node = compile("{% widthratio a b 100 %}");
        assert_eq!(node.class_name, "WidthRatioNode");
        assert_eq!(source_of(attr(&node, "val_expr")), "a");
        assert_eq!(source_of(attr(&node, "max_expr")), "b");
        assert_eq!(source_of(attr(&node, "max_width")), "100");
        assert_eq!(attr(&node, "asvar"), &AttrValue::None);
    }

    #[test]
    fn the_as_form_names_the_variable() {
        let node = compile("{% widthratio a b 100 as w %}");
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("w".to_string()));
    }

    #[test]
    fn the_width_is_a_filter_expression_like_the_other_two() {
        // The pin that separates `compile_filter(max_width)` from
        // `int(max_width)`: this template is rejected by the latter.
        let node = compile("{% widthratio a|add:1 b c|add:1 %}");
        assert_eq!(source_of(attr(&node, "val_expr")), "a|add:1");
        assert_eq!(source_of(attr(&node, "max_width")), "c|add:1");
    }

    #[test]
    fn the_arity_branches_are_measured_not_derived() {
        // Five bits is the `else`, and six bits whose fourth argument is not
        // `as` is the `as` branch's own error. The plan for this task had these
        // two the other way round.
        assert_eq!(
            failure("{% widthratio a b c d %}").kind,
            ErrorKind::Syntax("widthratio takes at least three arguments".to_string()),
        );
        assert_eq!(
            failure("{% widthratio a b c d e %}").kind,
            ErrorKind::Syntax(
                "Invalid syntax in widthratio tag. Expecting 'as' keyword".to_string()
            ),
        );
    }

    #[test]
    fn widthratio_rejects_a_short_call_and_a_wrong_keyword() {
        assert_eq!(
            failure("{% widthratio a b %}").kind,
            ErrorKind::Syntax("widthratio takes at least three arguments".to_string()),
        );
        assert_eq!(
            failure("{% widthratio a b c nope w %}").kind,
            ErrorKind::Syntax(
                "Invalid syntax in widthratio tag. Expecting 'as' keyword".to_string()
            ),
        );
    }
}
