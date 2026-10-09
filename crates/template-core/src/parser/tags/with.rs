//! `{% with %}` — `defaulttags.do_with` (`defaulttags.py:1654-1687`).
//!
//! # Both messages interpolate through Python's `repr`
//!
//! `"%r expected at least one variable assignment" % bits[0]` (`:1678-1680`) and
//! `"%r received an invalid token: %r" % (bits[0], remaining_bits[0])`
//! (`:1682-1684`). The second one's *second* value is the leftover bit — an
//! arbitrary token from the template — so the escaping rules are observable:
//! measured on the pinned tree, `{% with a=1 foo<U+00AD>bar %}` reports
//! `'with' received an invalid token: 'foo\xadbar'`. Rust's `char::is_control`
//! covers Cc only, so this goes through [`Parser::py_repr`], which the shell
//! implements with Python's own `repr` ([`Host::repr`]).
//!
//! # The modern form is the kwargs, and the legacy form is not dead
//!
//! `WithNode(var, name, nodelist, extra_context=None)` still takes the two
//! legacy positionals, and `:1687` passes **`None, None`** for them — the values
//! live in `extra_context`, built by `token_kwargs(…, support_legacy=True)`
//! (`:1676`). `support_legacy` is what makes `{% with a as b %}` work at all, so
//! a port that read `support_legacy=False` would reject a form the docs call
//! supported, and a port that passed the pair positionally would build a node
//! whose `extra_context` is empty.
//!
//! # The order of the two checks
//!
//! Emptiness first: `{% with a %}` reports "expected at least one variable
//! assignment" and never reaches the leftover check, because `token_kwargs`
//! removed nothing. `{% with a=1 b %}` removed `a=1` and so has both an entry
//! and a leftover. Both measured.
//!
//! [`Host::repr`]: crate::parser::host::Host::repr
//! [`Parser::py_repr`]: crate::parser::Parser::py_repr

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::kwargs::token_kwargs;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // A dispatched block tag always has a first bit — `Parser::parse` rejects
    // an empty block tag before it looks the command up — so this is Django's
    // `bits[1:]` without the slice's empty-safety dance.
    let mut bits = split_contents(token)?;
    let tag_name = bits[0].clone();
    let mut remaining_bits = bits.split_off(1);

    // `token_kwargs` mutates `remaining_bits` in place, which is how the
    // leftover check below can see what it refused. `compile_filter` borrows
    // the parser immutably, so the block is what lets `parse` take it back.
    let extra_context = {
        let mut compile_filter = |source: &str| parser.compile_filter(source);
        token_kwargs(&mut remaining_bits, &mut compile_filter, true)?
    };

    if extra_context.is_empty() {
        return Err(Error::syntax(format!(
            "{} expected at least one variable assignment",
            parser.py_repr(&tag_name),
        )));
    }
    if let Some(leftover) = remaining_bits.first() {
        return Err(Error::syntax(format!(
            "{} received an invalid token: {}",
            parser.py_repr(&tag_name),
            parser.py_repr(leftover),
        )));
    }

    let nodelist = parser.parse(&["endwith"])?;
    parser.delete_first_token();
    Ok(NodeDesc::with_nodelist("WithNode", "nodelist", nodelist)
        // Django's two positionals are literally `None` (`:1687`); the data is
        // in `extra_context`. They are emitted rather than omitted so the
        // descriptor names every parameter `WithNode.__init__` takes.
        .attr("var", AttrValue::None)
        .attr("name", AttrValue::None)
        .attr("extra_context", AttrValue::Map(extra_context)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, texts};

    fn context(node: &NodeDesc) -> Vec<String> {
        let AttrValue::Map(pairs) = attr(node, "extra_context") else {
            panic!("extra_context is a Map");
        };
        pairs.iter().map(|(key, _)| key.clone()).collect()
    }

    #[test]
    fn with_puts_the_kwargs_in_extra_context_and_leaves_the_positionals_none() {
        let node = compile("{% with a=1 b=2 %}{{ a }}{% endwith %}");
        assert_eq!(node.class_name, "WithNode");
        assert_eq!(attr(&node, "var"), &AttrValue::None);
        assert_eq!(attr(&node, "name"), &AttrValue::None);
        assert_eq!(context(&node), vec!["a", "b"]);
        assert_eq!(node.nodelists[0].0, "nodelist");
    }

    #[test]
    fn with_compiles_the_value_side_of_each_assignment() {
        let node = compile("{% with a=1|add:2 %}{% endwith %}");
        let AttrValue::Map(pairs) = attr(&node, "extra_context") else {
            panic!("extra_context is a Map");
        };
        let AttrValue::FilterExpr(expression) = &pairs[0].1 else {
            panic!("the value is a FilterExpr");
        };
        assert_eq!(expression.token, "1|add:2");
        assert_eq!(expression.filters[0].name, "add");
    }

    #[test]
    fn with_accepts_the_legacy_as_form() {
        // `support_legacy=True` is what makes this reachable, and the name is
        // the *third* bit while the value is the first.
        let node = compile("{% with a as b %}{{ b }}{% endwith %}");
        assert_eq!(context(&node), vec!["b"]);
        let AttrValue::Map(pairs) = attr(&node, "extra_context") else {
            panic!("extra_context is a Map");
        };
        let AttrValue::FilterExpr(expression) = &pairs[0].1 else {
            panic!("the value is a FilterExpr");
        };
        assert_eq!(expression.token, "a");
    }

    #[test]
    fn with_legacy_pairs_are_joined_by_and() {
        let node = compile("{% with a as b and c as d %}{% endwith %}");
        assert_eq!(context(&node), vec!["b", "d"]);
    }

    #[test]
    fn with_reads_its_body() {
        let node = compile("{% with a=1 %}x{% endwith %}");
        assert_eq!(texts(&node.nodelists[0].1), vec!["x"]);
    }

    #[test]
    fn with_reports_an_empty_assignment_list_first() {
        // `{% with a %}` is the legacy shape without its `as`: nothing is
        // removed, so the emptiness check fires and the leftover check is never
        // reached.
        for source in ["{% with %}{% endwith %}", "{% with a %}{% endwith %}"] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax("'with' expected at least one variable assignment".to_string()),
            );
        }
    }

    #[test]
    fn with_reports_the_leftover_bit_through_the_hosts_repr() {
        // Measured on the pinned tree: `{% with a=1 b %}` and
        // `{% with a=1 and %}` both name the leftover, and a stray `and` in
        // *kwarg* form is not a separator — that is `support_legacy`'s
        // asymmetry, not a special case here.
        for (source, leftover) in [
            ("{% with a=1 b %}{% endwith %}", "b"),
            ("{% with a=1 and %}{% endwith %}", "and"),
        ] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax(format!("'with' received an invalid token: '{leftover}'")),
            );
        }
    }
}
