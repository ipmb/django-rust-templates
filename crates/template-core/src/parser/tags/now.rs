//! `{% now "format" [as var] %}`.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::{split_contents, strip_first_and_last};
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(_parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let mut bits = split_contents(token)?;

    let mut asvar = None;
    if bits.len() == 4 && bits[bits.len() - 2] == "as" {
        asvar = Some(bits[bits.len() - 1].clone());
        bits.truncate(bits.len() - 2);
    }

    if bits.len() != 2 {
        return Err(Error::syntax("'now' statement takes one argument"));
    }

    let mut node = NodeDesc::new("NowNode").attr(
        "format_string",
        AttrValue::Str(strip_first_and_last(&bits[1])),
    );
    // Django passes `asvar=None` when absent, and `NowNode` stores it, so omitting
    // the keyword here produces the same node.
    if let Some(asvar) = asvar {
        node = node.attr("asvar", AttrValue::Str(asvar));
    }
    Ok(node)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    #[test]
    fn now_strips_the_surrounding_characters() {
        let node = compile(r#"{% now "jS F Y H:i" %}"#);
        assert_eq!(node.class_name, "NowNode");
        assert_eq!(
            attr(&node, "format_string"),
            &AttrValue::Str("jS F Y H:i".to_string()),
        );
        assert!(
            node.attrs.iter().all(|(name, _)| name != "asvar"),
            "no `as` means the keyword is absent, not None-valued",
        );
    }

    #[test]
    fn now_with_as_drops_the_last_two_bits() {
        let node = compile(r#"{% now "Y" as year %}"#);
        assert_eq!(
            attr(&node, "format_string"),
            &AttrValue::Str("Y".to_string())
        );
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("year".to_string()));
    }

    #[test]
    fn now_clamps_like_python_when_the_argument_is_short() {
        // `s[1:-1]` clamps; a Rust `[1..len - 1]` would panic on `""` and `"5"`.
        assert_eq!(strip_first_and_last(""), "");
        assert_eq!(strip_first_and_last("a"), "");
        assert_eq!(strip_first_and_last("ab"), "");
        assert_eq!(strip_first_and_last("abc"), "b");
        assert_eq!(strip_first_and_last("héllo"), "éll");
        assert_eq!(
            attr(&compile("{% now 5 %}"), "format_string"),
            &AttrValue::Str(String::new()),
        );
    }

    #[test]
    fn now_does_not_check_that_the_argument_was_quoted() {
        // Django slices regardless, so `abc` yields `b`.
        assert_eq!(
            attr(&compile("{% now abc %}"), "format_string"),
            &AttrValue::Str("b".to_string()),
        );
    }

    #[test]
    fn now_rejects_the_forms_django_rejects() {
        for source in [
            "{% now %}",
            "{% now a b %}",
            "{% now a b c %}",
            "{% now a b c d %}",
            "{% now \"Y\" as %}",
        ] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax("'now' statement takes one argument".to_string()),
                "{source}",
            );
        }
    }
}
