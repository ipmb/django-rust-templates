//! `{% comment %}…{% endcomment %}`.
//!
//! `skip_past`, not `parse`: the body is never compiled, so a template with a
//! syntax error inside `{% comment %}` still parses (Review Focus item 9). An
//! unclosed comment is reported by `skip_past` through `unclosed_block_tag`,
//! which pops the `comment` frame that `parse` pushed.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    parser.skip_past("endcomment")?;
    // `CommentNode` has `child_nodelists = ()` and no `__init__`, so the body is
    // discarded rather than attached: there is nothing to render.
    Ok(NodeDesc::new("CommentNode"))
}

#[cfg(test)]
mod tests {
    use crate::parser::tags::support::compile;

    #[test]
    fn comment_skips_its_body_without_compiling_it() {
        // A syntax error inside the body must not fail the parse: the body is
        // never compiled (Review Focus item 9).
        let node = compile("{% comment %}{% nope %}{{ x }}a{% endcomment %}");
        assert_eq!(node.class_name, "CommentNode");
        assert!(node.attrs.is_empty(), "CommentNode takes no arguments");
        assert!(
            node.nodelists.is_empty(),
            "the body must be discarded, not attached",
        );
    }

    #[test]
    fn an_empty_comment_is_fine() {
        assert_eq!(
            compile("{% comment %}{% endcomment %}").class_name,
            "CommentNode"
        );
    }
}
