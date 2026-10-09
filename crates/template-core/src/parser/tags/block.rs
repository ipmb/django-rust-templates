//! `{% block %}` — `django.template.loader_tags.do_block` (`loader_tags.py:215`).
//!
//! `token.contents.split()`, **not** `split_contents()`: Django's own comment
//! (`loader_tags.py:219-220`) says the tag "doesn't accept variable as
//! arguments", and the two differ on U+001C–U+001F. `crate::pyunicode::split` is
//! Python's `str.split`, so the arity check sees the same `bits` Django sees.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = pyunicode::split(&token.contents);
    if bits.len() != 2 {
        // `bits.len() != 2` admits 0, and Django's `bits[0]` would raise
        // `IndexError` there (`loader_tags.py:222-223`) — but `bits[0]` in Rust
        // is a *panic*, and a panic crosses the FFI as a process-level event
        // rather than an exception. Unreachable: `Parser::parse` splits the same
        // contents and raises `Empty block tag on line %d` first
        // (`base.py:560-563`), so nothing with zero bits reaches a compile
        // function. Stated rather than assumed, in the idiom
        // `NoHost::call_tag`'s `unreachable!` already uses.
        let command = bits
            .first()
            .expect("parse rejects an empty block tag before a compile function runs");
        return Err(Error::syntax(format!(
            "'{}' tag takes only one argument",
            command
        )));
    }
    let block_name = bits[1].to_string();

    // Django raises **before** appending (`loader_tags.py:227-234`), so the
    // occurrence that raises is never recorded and `loaded_blocks` holds only
    // names whose block compiled.
    if parser.loaded_blocks().contains(&block_name) {
        return Err(Error::syntax(format!(
            "'{}' tag with name '{}' appears more than once",
            bits[0], block_name
        )));
    }
    parser.register_block_name(block_name.clone());

    let nodelist = parser.parse(&["endblock"])?;

    // `parse(("endblock",))` prepends the terminator it stopped on
    // (`base.py:564-569`), so this is always `Some`; `None` would mean the parse
    // returned without one, which is a defect in `parse` rather than in the
    // template.
    let Some(endblock) = parser.next_token() else {
        return Err(Error::internal(
            "do_block: parse returned without the endblock token",
        ));
    };
    let named = format!("endblock {block_name}");
    if endblock.contents != "endblock" && endblock.contents != named {
        return Err(parser.invalid_block_tag(&endblock, "endblock", &["endblock", &named]));
    }

    Ok(NodeDesc::new("BlockNode")
        .attr("name", AttrValue::Str(block_name))
        .nodelist("nodelist", nodelist))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::NoHost;
    use crate::parser::tags::support::{attr, compile, failure, texts};

    #[test]
    fn a_block_compiles_to_a_block_node_carrying_its_body() {
        let node = compile("{% block a %}x{% endblock %}");
        assert_eq!(node.class_name, "BlockNode");
        assert_eq!(attr(&node, "name"), &AttrValue::Str("a".to_string()));
        assert_eq!(node.nodelists[0].0, "nodelist");
        assert_eq!(texts(&node.nodelists[0].1), vec!["x"]);
    }

    #[test]
    fn a_named_endblock_is_accepted() {
        // `acceptable_endblocks` is a two-element tuple (`loader_tags.py:239`),
        // kept for backwards-compatibility (#3100) — so this is not an error,
        // and the *name* must match: `{% endblock b %}` for `{% block a %}` is
        // the third error row of the plan's corpus.
        let node = compile("{% block a %}x{% endblock a %}");
        assert_eq!(node.class_name, "BlockNode");
        assert_eq!(texts(&node.nodelists[0].1), vec!["x"]);
    }

    #[test]
    fn a_block_may_be_nested_in_another() {
        let node = compile("{% block a %}{% block b %}x{% endblock %}{% endblock %}");
        assert_eq!(attr(&node, "name"), &AttrValue::Str("a".to_string()));
        let inner = &node.nodelists[0].1.nodes[0];
        assert_eq!(inner.class_name, "BlockNode");
        assert_eq!(attr(inner, "name"), &AttrValue::Str("b".to_string()));
    }

    #[test]
    fn a_block_needs_exactly_one_argument() {
        assert_eq!(
            failure("{% block %}x{% endblock %}").kind,
            ErrorKind::Syntax("'block' tag takes only one argument".to_string()),
        );
    }

    #[test]
    fn a_duplicate_block_name_is_rejected_before_the_second_is_recorded() {
        let first = failure("{% block a %}{% endblock %}{% block a %}{% endblock %}");
        assert_eq!(
            first.kind,
            ErrorKind::Syntax("'block' tag with name 'a' appears more than once".to_string()),
        );
    }

    #[test]
    fn the_rejected_occurrence_is_not_recorded() {
        // The observable form of "raise before append" (`loader_tags.py:227-234`).
        // The message cannot carry it — `"'%s' tag with name '%s' appears more
        // than once"` names no position, and the parse ends at the offending
        // tag, so there is no later `{% block %}` for a wrong ordering to
        // misattribute. `loaded_blocks` is where the ordering shows.
        let mut parser = Parser::new(
            Lexer::new("{% block a %}{% endblock %}{% block a %}{% endblock %}").tokenize(),
            &NoHost,
        );
        parser
            .parse(&[])
            .expect_err("the second {% block a %} raises");
        assert_eq!(parser.loaded_blocks(), ["a"]);
    }

    #[test]
    fn a_mismatched_endblock_name_is_an_invalid_block_tag() {
        let error = failure("{% block a %}x{% endblock b %}");
        assert_eq!(
            error.kind,
            ErrorKind::Syntax(
                "Invalid block tag on line 1: 'endblock', expected 'endblock' or \
                 'endblock a'. Did you forget to register or load this tag?"
                    .to_string()
            ),
        );
    }

    #[test]
    fn an_unclosed_block_reports_the_block() {
        assert_eq!(
            failure("{% block a %}x").kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 1: 'block'. Looking for one of: endblock.".to_string()
            ),
        );
    }
}
