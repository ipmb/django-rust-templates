//! `{% autoescape on|off %}…{% endautoescape %}`.
//!
//! Django splits with `token.contents.split()`, not `split_contents()`, "because
//! this tag doesn't accept variable as arguments". The two are observable here:
//! for `{% autoescape "on off" %}` the raw split sees three arguments and
//! rejects on the count, while `split_contents` keeps the quoted phrase whole,
//! sees two, and rejects on the value. Both fail, with different messages, and
//! only one of them is Django's. (`{% autoescape a"b c %}` does *not*
//! distinguish them: the trailing quote is unmatched, so `smart_split` splits it
//! exactly as `str.split` does — verified against Django 6.0.9.)

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 2 {
        return Err(Error::syntax(
            "'autoescape' tag requires exactly one argument.",
        ));
    }
    let arg = args[1];
    if arg != "on" && arg != "off" {
        return Err(Error::syntax(
            "'autoescape' argument should be 'on' or 'off'",
        ));
    }

    let nodelist = parser.parse(&["endautoescape"])?;
    parser.delete_first_token();
    Ok(
        NodeDesc::with_nodelist("AutoEscapeControlNode", "nodelist", nodelist)
            .attr("setting", AttrValue::Bool(arg == "on")),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, texts};

    #[test]
    fn autoescape_reads_its_body_and_records_the_setting() {
        let on = compile("{% autoescape on %}x{% endautoescape %}");
        assert_eq!(on.class_name, "AutoEscapeControlNode");
        assert_eq!(attr(&on, "setting"), &AttrValue::Bool(true));
        assert_eq!(on.nodelists[0].0, "nodelist");
        assert_eq!(texts(&on.nodelists[0].1), vec!["x"]);

        let off = compile("{% autoescape off %}{% endautoescape %}");
        assert_eq!(attr(&off, "setting"), &AttrValue::Bool(false));
        assert!(texts(&off.nodelists[0].1).is_empty());
    }

    #[test]
    fn autoescape_nests() {
        let outer = compile(
            "{% autoescape off %}{% autoescape on %}x{% endautoescape %}{% endautoescape %}",
        );
        assert_eq!(attr(&outer, "setting"), &AttrValue::Bool(false));
        let inner = &outer.nodelists[0].1;
        assert_eq!(inner.nodes.len(), 1);
        assert_eq!(inner.nodes[0].class_name, "AutoEscapeControlNode");
        assert_eq!(attr(&inner.nodes[0], "setting"), &AttrValue::Bool(true));
        assert_eq!(texts(&inner.nodes[0].nodelists[0].1), vec!["x"]);
    }

    #[test]
    fn autoescape_uses_the_raw_split_not_split_contents() {
        // `"on off"` is one bit to `split_contents` (which keeps a quoted phrase
        // whole) and two to the raw split, so this count error is reachable only
        // through the raw split: a `split_contents` implementation would see two
        // arguments and fail on the *value* instead. The plan's original input,
        // `{% autoescape a"b c %}`, does not distinguish them — `smart_split`
        // splits the unmatched trailing quote exactly as `str.split` does — so it
        // passed under either implementation (verified against Django 6.0.9).
        assert_eq!(
            failure("{% autoescape \"on off\" %}").kind,
            ErrorKind::Syntax("'autoescape' tag requires exactly one argument.".to_string()),
        );
    }

    #[test]
    fn autoescape_rejects_the_value_django_rejects() {
        assert_eq!(
            failure("{% autoescape maybe %}").kind,
            ErrorKind::Syntax("'autoescape' argument should be 'on' or 'off'".to_string()),
        );
    }
}
