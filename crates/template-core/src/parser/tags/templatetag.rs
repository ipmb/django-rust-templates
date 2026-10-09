//! `{% templatetag openblock %}` and the other seven.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::py_list_repr;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

/// The eight bits `{% templatetag %}` accepts, in Django's mapping order — which
/// is also the order the error message lists them in, because Python's `list()`
/// of a dict literal preserves insertion order. The *values* are the bits
/// themselves and are render-time, so they live on the Python class.
const TEMPLATETAG_ARGUMENTS: [&str; 8] = [
    "openblock",
    "closeblock",
    "openvariable",
    "closevariable",
    "openbrace",
    "closebrace",
    "opencomment",
    "closecomment",
];

pub fn compile(_parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Raw split, as in Django: the tag takes no variables, so quoting is not
    // meaningful and `{% templatetag "openblock" %}` is an invalid argument
    // rather than a quoted one. The distinguishing case is a quoted phrase with
    // a space — `{% templatetag "open block" %}` — where the raw split sees
    // three arguments (a count error) and `split_contents` sees two (an
    // invalid-argument error).
    let bits = pyunicode::split(&token.contents);
    if bits.len() != 2 {
        return Err(Error::syntax("'templatetag' statement takes one argument"));
    }

    let tag = bits[1];
    if !TEMPLATETAG_ARGUMENTS.contains(&tag) {
        // `tag` — arbitrary template text — is *not* passed through
        // `py_repr_str`: Django's own message interpolates it with `%s`, so this
        // uses `{}`. The only `%r` here is `py_list_repr(&TEMPLATETAG_ARGUMENTS)`
        // over the constant above, which is ASCII by construction and is why
        // that function's assertion is not tripped from this path.
        return Err(Error::syntax(format!(
            "Invalid templatetag argument: '{}'. Must be one of: {}",
            tag,
            py_list_repr(&TEMPLATETAG_ARGUMENTS),
        )));
    }

    Ok(NodeDesc::new("TemplateTagNode").attr("tagtype", AttrValue::Str(tag.to_string())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    #[test]
    fn templatetag_accepts_every_mapped_argument() {
        for name in TEMPLATETAG_ARGUMENTS {
            let node = compile(&format!("{{% templatetag {name} %}}"));
            assert_eq!(node.class_name, "TemplateTagNode");
            assert_eq!(attr(&node, "tagtype"), &AttrValue::Str(name.to_string()));
        }
    }

    #[test]
    fn templatetag_rejects_anything_else_with_the_full_ordered_list() {
        // The list must be in Django's mapping order, because Python's `list()`
        // of a dict literal is insertion-ordered and the message is compared
        // exactly.
        assert_eq!(
            failure("{% templatetag nope %}").kind,
            ErrorKind::Syntax(
                "Invalid templatetag argument: 'nope'. Must be one of: \
                 ['openblock', 'closeblock', 'openvariable', 'closevariable', \
                 'openbrace', 'closebrace', 'opencomment', 'closecomment']"
                    .to_string(),
            ),
        );
    }

    #[test]
    fn templatetag_does_not_strip_quotes() {
        // The quotes are part of the argument, so it is invalid. This pins that
        // *neither* splitter strips them — `smart_split` keeps the quotes too —
        // so it is not a raw-vs-smart discriminator; the test below is.
        match failure(r#"{% templatetag "openblock" %}"#).kind {
            ErrorKind::Syntax(message) => {
                assert!(message.contains("'\"openblock\"'"), "{message}");
            }
            other => panic!("expected a syntax error, got {other:?}"),
        }
    }

    #[test]
    fn templatetag_uses_the_raw_split_not_split_contents() {
        // `"open block"` is two bits to `split_contents` and three to the raw
        // split, so this count error is reachable only through the raw split: a
        // `split_contents` implementation would reach the invalid-argument
        // error for `"open block"` instead. `{% templatetag "openblock" %}`
        // cannot distinguish them (verified against Django 6.0.9).
        assert_eq!(
            failure(r#"{% templatetag "open block" %}"#).kind,
            ErrorKind::Syntax("'templatetag' statement takes one argument".to_string()),
        );
    }

    #[test]
    fn templatetag_requires_exactly_one_argument() {
        for source in ["{% templatetag %}", "{% templatetag a b %}"] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax("'templatetag' statement takes one argument".to_string()),
                "{source}",
            );
        }
    }
}
