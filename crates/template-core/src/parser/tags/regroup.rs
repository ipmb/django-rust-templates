//! `{% regroup %}` — `defaulttags.regroup` (`defaulttags.py:1352-1419`).
//!
//! # The third argument is compiled *around* the fifth, not from itself
//!
//! `expression = parser.compile_filter(var_name + "." + bits[3])`
//! (`:1414-1416`), so `{% regroup m by k as g %}` builds its `expression`
//! `FilterExpression` from the source **`"g.k"`** — the `var_name` `bits[5]`
//! joined to the key `bits[3]` through `VARIABLE_ATTRIBUTE_SEPARATOR`
//! (`base.py:75`, `"."`). A body written from the tag's surface syntax compiles
//! `bits[3]` and produces an `expression` of `"k"`, which parses without error
//! and resolves the wrong variable at render time — the same class of quiet
//! divergence as `{% filter %}`'s synthetic `"var|"`. The corpus row
//! `{% regroup m|dictsort:"k" by k as g %}` pins it by asserting the compiled
//! source is `g.k` and not `k`.
//!
//! # Arity is checked first, then the two keywords, and the target between them
//!
//! `len(bits) != 6` (`:1400-1401`) runs before anything else, so
//! `{% regroup m by k %}` reports the arity message even though it is also
//! missing its `as`. The `by` check (`:1403-1404`) runs after `target` is
//! compiled, so a malformed target reports its *filter* error first; the `as`
//! check (`:1405-1406`) runs last. All three messages are literal — no
//! interpolation — and the order is preserved here rather than reordered for
//! readability, because a reordering changes which message a doubly-bad call
//! gets.
//!
//! # The node is positional
//!
//! `RegroupNode(target, expression, var_name)` (`:361-365`) — all three are
//! plain attributes, and the descriptor names every one.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Django's `token.split_contents()`, tag name included, because the arity
    // check counts it.
    let bits = split_contents(token)?;
    if bits.len() != 6 {
        return Err(Error::syntax("'regroup' tag takes five arguments"));
    }

    let target = parser.compile_filter(&bits[1])?;

    if bits[2] != "by" {
        return Err(Error::syntax(
            "second argument to 'regroup' tag must be 'by'",
        ));
    }
    if bits[4] != "as" {
        return Err(Error::syntax(
            "next-to-last argument to 'regroup' tag must be 'as'",
        ));
    }

    let var_name = bits[5].clone();
    // `var_name + VARIABLE_ATTRIBUTE_SEPARATOR + bits[3]` — the derived string
    // this tag exists in Task 4 to get right (`:1414-1416`).
    let expression = parser.compile_filter(&format!("{var_name}.{}", bits[3]))?;

    Ok(NodeDesc::new("RegroupNode")
        .attr("target", target)
        .attr("expression", expression)
        .attr("var_name", AttrValue::Str(var_name)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    fn source(value: &AttrValue) -> &str {
        let AttrValue::FilterExpr(expression) = value else {
            panic!("a regroup operand is a FilterExpr");
        };
        expression.token.as_str()
    }

    #[test]
    fn regroup_compiles_the_target_from_its_own_bit() {
        let node = compile("{% regroup m by k as g %}");
        assert_eq!(node.class_name, "RegroupNode");
        assert_eq!(source(attr(&node, "target")), "m");
        assert_eq!(attr(&node, "var_name"), &AttrValue::Str("g".to_string()));
    }

    #[test]
    fn regroup_compiles_the_expression_around_the_var_name() {
        // The Task-4 pin: `"g.k"`, not `"k"`. A body that compiled `bits[3]`
        // would give `"k"` here and pass every other assertion in this module.
        for (source_text, expected) in [
            ("{% regroup m by k as g %}", "g.k"),
            (r#"{% regroup m|dictsort:"k" by k as g %}"#, "g.k"),
            (r#"{% regroup m by k.v as grouped %}"#, "grouped.k.v"),
        ] {
            let node = compile(source_text);
            assert_eq!(source(attr(&node, "expression")), expected, "{source_text}");
        }
    }

    #[test]
    fn the_arity_check_runs_before_the_keyword_checks() {
        // Five bits: neither `by` nor `as` is in place, and the arity message is
        // the one that fires (`:1400-1401`).
        for source_text in [
            "{% regroup m by k %}",
            "{% regroup m by k as %}",
            "{% regroup %}",
        ] {
            assert_eq!(
                failure(source_text).kind,
                ErrorKind::Syntax("'regroup' tag takes five arguments".to_string()),
                "{source_text}",
            );
        }
    }

    #[test]
    fn the_target_is_compiled_before_the_by_check() {
        // A malformed target reports its filter error, not the `by` message, so
        // this six-bit call with a bad `by` still fails on the target — the
        // order `:1402` then `:1403` is what makes it so.
        assert_eq!(
            failure("{% regroup m| by k as g %}").kind,
            ErrorKind::Syntax("Could not parse the remainder: '|' from 'm|'".to_string()),
        );
    }

    #[test]
    fn regroup_names_the_keyword_that_is_wrong() {
        assert_eq!(
            failure("{% regroup m ny k as g %}").kind,
            ErrorKind::Syntax("second argument to 'regroup' tag must be 'by'".to_string()),
        );
        assert_eq!(
            failure("{% regroup m by k is g %}").kind,
            ErrorKind::Syntax("next-to-last argument to 'regroup' tag must be 'as'".to_string()),
        );
    }
}
