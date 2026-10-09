//! `{% spaceless %}…{% endspaceless %}`.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, _token: &Token) -> Result<NodeDesc, Error> {
    let nodelist = parser.parse(&["endspaceless"])?;
    parser.delete_first_token();
    Ok(NodeDesc::with_nodelist(
        "SpacelessNode",
        "nodelist",
        nodelist,
    ))
}

#[cfg(test)]
mod tests {
    use crate::parser::tags::support::{compile, texts};

    #[test]
    fn spaceless_reads_its_body() {
        let node = compile("{% spaceless %}  a  {% endspaceless %}");
        assert_eq!(node.class_name, "SpacelessNode");
        assert_eq!(node.nodelists[0].0, "nodelist");
        assert_eq!(texts(&node.nodelists[0].1), vec!["  a  "]);
    }
}
