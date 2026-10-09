//! `{% filter %}` — `defaulttags.do_filter` (`defaulttags.py:724-755`).
//!
//! # The synthetic source
//!
//! The tag does not compile the text it was given. It rebuilds a *variable*
//! around it — `parser.compile_filter("var|%s" % rest)` (`defaulttags.py:745`) —
//! so the descriptor's `FilterExpr` is built from `"var|lower"` for
//! `{% filter lower %}`, and `var` is a name that appears nowhere in the
//! template. The variable part is what `FilterNode.render` pushes onto the
//! context as `var` before resolving (`defaulttags.py:144`), so the prefix is
//! not cosmetic: change it and the node resolves something else.
//!
//! # The refusal, and why it is checked by name
//!
//! `escape` and `safe` are refused because a `{% filter %}` block would apply
//! them to *already-rendered* output, which is what `{% autoescape %}` is for.
//! Django reads the name off the **compiled function** —
//! `getattr(func, "_filter_name", None)` (`defaulttags.py:747`) — not off the
//! source text, and the distinction is real: a custom library may register the
//! `escape` function under another name, and Django would still refuse it.
//!
//! This port reads [`FilterCall::name`], the name as written. Measured on the
//! pinned tree, that is exact for every built-in — all 57 entries of
//! `defaultfilters.register.filters` have `_filter_name == key`, including
//! `escape`, whose function is named `escape_filter` but carries
//! `_filter_name == "escape"`. It can only diverge under a **custom library**
//! that registers `escape`'s function under a second name, which is `{% load %}`
//! and so M6 — the same prerequisite `Host::check_filter` documents, and the
//! fix is the same handle: carry the function, not the name.
//!
//! # The unguarded unpack
//!
//! `_, rest = token.contents.split(None, 1)` (`defaulttags.py:744`) has no
//! length check, so `{% filter %}` — whose contents hold no separating run —
//! raises a bare `ValueError` out of Django's parser. See
//! [`ErrorKind::ValueError`]; the shell raises the same class for us.
//!
//! [`ErrorKind::ValueError`]: crate::parser::error::ErrorKind::ValueError
//! [`FilterCall::name`]: crate::parser::desc::FilterCall

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // `_, rest = token.contents.split(None, 1)`. `split_once` returns one
    // element exactly when Django's unpack has too few, so this is the same
    // condition rather than a length check that happens to agree.
    let parts = pyunicode::split_once(&token.contents);
    let Some(rest) = parts.get(1) else {
        return Err(Error::value_error(
            "not enough values to unpack (expected 2, got 1)",
        ));
    };

    let expression = parser.compile_filter(&format!("var|{rest}"))?;
    let AttrValue::FilterExpr(filter_expr) = &expression else {
        // `Parser::compile_filter` builds this variant unconditionally, two
        // lines up. Kept as a loud error rather than an `unreachable!` so a
        // future change to the seam reports itself instead of unwinding
        // through the FFI.
        return Err(Error::internal(
            "compile_filter did not return a FilterExpr",
        ));
    };

    // Every filter in the chain is checked, and the FIRST offender raises —
    // `{% filter upper|safe %}` reports `safe`, not nothing. The order is
    // Django's `for func, unused in filter_expr.filters`
    // (`defaulttags.py:746-752`), which walks the chain as written.
    for call in &filter_expr.filters {
        if call.name == "escape" || call.name == "safe" {
            return Err(Error::syntax(format!(
                "\"filter {}\" is not permitted. Use the \"autoescape\" tag instead.",
                call.name
            )));
        }
    }

    let nodelist = parser.parse(&["endfilter"])?;
    parser.delete_first_token();
    Ok(NodeDesc::new("FilterNode")
        .attr("filter_expr", expression)
        .nodelist("nodelist", nodelist))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, texts};

    #[test]
    fn filter_compiles_a_variable_around_the_argument_not_the_argument() {
        // The whole reason this tag has its own module: the descriptor's token
        // is `"var|lower"`, and `var` is not in the template.
        let node = compile("{% filter lower %}X{% endfilter %}");
        assert_eq!(node.class_name, "FilterNode");
        let AttrValue::FilterExpr(expression) = attr(&node, "filter_expr") else {
            panic!("filter_expr is a FilterExpr");
        };
        assert_eq!(expression.token, "var|lower");
        let (is_var, value) = &expression.var;
        assert!(is_var, "`var` is a lookup, not a constant");
        let AttrValue::Variable(variable) = value else {
            panic!("the variable part is a Variable");
        };
        assert_eq!(variable.var, "var");
        assert_eq!(expression.filters.len(), 1);
        assert_eq!(expression.filters[0].name, "lower");
        assert_eq!(texts(&node.nodelists[0].1), vec!["X"]);
    }

    #[test]
    fn filter_keeps_the_whitespace_between_a_pipe_and_its_argument() {
        // Re-joining a full `split()` would give `"var|lower |upper"` here and
        // a different `FilterExpression`. Django's `split(None, 1)` keeps the
        // run's interior spacing, which is what `split_once` is for.
        let node = compile("{% filter lower  |upper %}x{% endfilter %}");
        let AttrValue::FilterExpr(expression) = attr(&node, "filter_expr") else {
            panic!("filter_expr is a FilterExpr");
        };
        assert_eq!(expression.token, "var|lower  |upper");
        assert_eq!(expression.filters.len(), 2);
    }

    #[test]
    fn filter_refuses_escape_and_safe_by_name() {
        for name in ["escape", "safe"] {
            let source = "{% filter ".to_string() + name + " %}x{% endfilter %}";
            assert_eq!(
                failure(&source).kind,
                ErrorKind::Syntax(format!(
                    "\"filter {name}\" is not permitted. Use the \"autoescape\" tag instead."
                )),
            );
        }
    }

    #[test]
    fn filter_refuses_the_offender_at_its_own_position_in_the_chain() {
        // `upper|safe`: the chain is walked in order, so the message names the
        // second filter and not the first.
        assert_eq!(
            failure("{% filter upper|safe %}x{% endfilter %}").kind,
            ErrorKind::Syntax(
                "\"filter safe\" is not permitted. Use the \"autoescape\" tag instead.".to_string()
            ),
        );
        assert_eq!(
            failure("{% filter escape|upper %}x{% endfilter %}").kind,
            ErrorKind::Syntax(
                "\"filter escape\" is not permitted. Use the \"autoescape\" tag instead."
                    .to_string()
            ),
        );
    }

    #[test]
    fn a_filter_tag_with_no_argument_is_djangos_value_error() {
        // The unguarded unpack, reproduced. Note the *kind*: not `Syntax`.
        // Measured against the pinned tree,
        // `Engine().from_string("{% filter %}x{% endfilter %}")` raises
        // `ValueError: not enough values to unpack (expected 2, got 1)`.
        for source in [
            "{% filter %}x{% endfilter %}",
            "{% filter  %}x{% endfilter %}",
        ] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::ValueError(
                    "not enough values to unpack (expected 2, got 1)".to_string()
                ),
            );
        }
    }
}
