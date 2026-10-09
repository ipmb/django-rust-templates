//! `{% verbatim %}…{% endverbatim %}`.

use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc, TEXT_NODE};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

/// Django renders `{% verbatim %}`'s body at **parse** time —
/// `nodelist.render(Context())` — and keeps the result as a `SafeString`, which
/// is why the descriptor carries `AttrValue::SafeStr` rather than `Str`
/// (Review Focus item 8).
///
/// At M2 the body can only be text: the lexer turns everything between
/// `{% verbatim %}` and `{% endverbatim %}` into text tokens, and a variable
/// would have failed the parse before reaching here for want of M3's
/// `FilterExpression`. So the non-text arm is unreachable today, and it is an
/// error rather than an `unreachable!()` so that the milestone which *does*
/// reach it gets a message instead of a panic through the FFI.
fn render_text_only(nodelist: &NodeListDesc) -> Result<String, Error> {
    let mut out = String::new();
    for node in &nodelist.nodes {
        if node.class_name != TEXT_NODE {
            return Err(Error::unimplemented(format!(
                "a {{% verbatim %}} body contains a {} node, which needs a real render",
                node.class_name,
            )));
        }
        match node.attrs.as_slice() {
            [(name, AttrValue::Str(s))] if name.as_str() == "s" => out.push_str(s),
            other => {
                return Err(Error::internal(format!(
                    "a TextNode descriptor is not [(\"s\", Str)]: {other:?}",
                )))
            }
        }
    }
    Ok(out)
}

pub fn compile(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    let nodelist = parser.parse(&["endverbatim"])?;
    parser.delete_first_token();
    Ok(NodeDesc::new("VerbatimNode")
        .attr("content", AttrValue::SafeStr(render_text_only(&nodelist)?)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::NoHost;
    use crate::parser::tags::support::{attr, compile};

    #[test]
    fn verbatim_content_is_computed_at_parse_time_and_kept_safe() {
        let node = compile("{% verbatim %}{{ x }}{% endverbatim %}");
        assert_eq!(node.class_name, "VerbatimNode");
        assert!(
            node.nodelists.is_empty(),
            "the body is rendered at parse time, not kept as a nodelist",
        );
        match attr(&node, "content") {
            AttrValue::SafeStr(content) => assert_eq!(content, "{{ x }}"),
            other => panic!("verbatim content must be a SafeStr, got {other:?}"),
        }
    }

    #[test]
    fn verbatim_bodies_are_not_shared_between_instances() {
        let mut parser = Parser::new(
            Lexer::new("{% verbatim %}a{% endverbatim %}{% verbatim %}b{% endverbatim %}")
                .tokenize(),
            &NoHost,
        );
        let nodelist = parser.parse(&[]).unwrap();
        assert_eq!(
            attr(&nodelist.nodes[0], "content"),
            &AttrValue::SafeStr("a".to_string()),
        );
        assert_eq!(
            attr(&nodelist.nodes[1], "content"),
            &AttrValue::SafeStr("b".to_string()),
        );
    }

    #[test]
    fn an_empty_verbatim_body_is_the_empty_string() {
        assert_eq!(
            attr(&compile("{% verbatim %}{% endverbatim %}"), "content"),
            &AttrValue::SafeStr(String::new()),
        );
    }

    #[test]
    fn render_text_only_refuses_a_non_text_body() {
        // Unreachable through a real parse at M2, which is the point: a milestone
        // that makes it reachable must get a message, not a wrong answer.
        let nodelist = NodeListDesc {
            nodes: vec![NodeDesc::new("DebugNode")],
            contains_nontext: true,
        };
        assert!(matches!(
            render_text_only(&nodelist).unwrap_err().kind,
            ErrorKind::Unimplemented(_),
        ));
    }
}
