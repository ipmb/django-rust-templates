//! `{% partialdef %}` — `defaulttags.partialdef_func` (`defaulttags.py:1204`).
//!
//! Shared with `{% partial %}`: the two tags use one parser-side registry of
//! partial definitions, so they land in the same milestone.
//!
//! **The body is parsed by Rust and the definition crosses the seam.** Every tag
//! inside a partial is compiled by this engine — the block scan below is an
//! ordinary `Parser::parse` — and only the finished nodelist is handed over, by
//! `Host::register_partial_def`. The seam is at the end rather than the start
//! because of what Django does with the result: `partials[partial_name] =
//! PartialTemplate(nodelist, …)` and `return PartialDefNode(partial_name,
//! inline, nodelist)` are the **same** argument (`defaulttags.py:1257-1265`), so
//! the stored partial and the returned node have to share one nodelist object,
//! and only the side that owns the materialiser can arrange that. The duplicate
//! check rides along for a second reason: the message quotes
//! `parser.origin.name` (`defaulttags.py:1252-1256`), Python state the Rust side
//! does not hold.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;

    // Django's five arms, in Django's order (`defaulttags.py:1221-1234`). The
    // order is observable, which is why it is not collapsed into a counting
    // rule: a three-bit token whose third bit is not `inline` takes arm 2 and
    // not the wildcard, and the one-bit token takes arm 4 and not the wildcard.
    // Both messages below are therefore reachable while the wildcard's is too,
    // and a `match bits.len()` would have to special-case them back apart.
    let (name, inline) = match bits.as_slice() {
        [_, name, third] if third == "inline" => (name, true),
        [_, _, _] => {
            return Err(Error::syntax(
                "The 'inline' argument does not have any parameters; either use \
                 'inline' or remove it completely.",
            ))
        }
        [_, name] => (name, false),
        [_] => return Err(Error::syntax("'partialdef' tag requires a name")),
        _ => return Err(Error::syntax("'partialdef' tag takes at most 2 arguments")),
    };

    // `valid_endpartials`, and the tuple **is** the message: it is handed to
    // `invalid_block_tag` and, through `Parser::parse`, to `unclosed_block_tag`
    // unchanged, so both messages list exactly these two spellings and neither
    // is spelled anywhere else.
    let valid = [
        String::from("endpartialdef"),
        format!("endpartialdef {name}"),
    ];
    let valid_refs: Vec<&str> = valid.iter().map(String::as_str).collect();

    // `getattr(token, "position", None)` and `pos_open[0]`: the open tag's
    // **start**. `None` under the non-debug lexer, and the two offsets come from
    // *different* tokens — reading both off one gets the wrong pair and, in the
    // shell, the wrong substring.
    let source_start = token.position.map(|position| position.0);

    // `parser.parse(valid_endpartials)`, then `parser.next_token()`. The token
    // cannot be `None`: `Parser::parse` returns `Ok` with the matching token
    // prepended exactly when it stopped on one (`parser/mod.rs:336-338`), and
    // returns `Err(unclosed_block_tag)` rather than `Ok` when the tokens run out
    // (`parser/mod.rs:465-469`). So a defensive branch here would be one no
    // input reaches and therefore none can redden.
    let body = parser.parse(&valid_refs)?;
    let endpartial = parser
        .next_token()
        .expect("parse stopped on a matching token and prepended it");

    // The **whole contents**, where the scan above matched the **first word**
    // (`base.py:561`, `base.py:564`). That asymmetry is the tag: `endpartialdef
    //  p` ends the block and is then refused here, and the message names the
    // first word — `'endpartialdef'` — not the contents. `command` is the
    // literal Django passes, not `bits[0]`.
    if !valid_refs.contains(&endpartial.contents.as_str()) {
        return Err(parser.invalid_block_tag(&endpartial, "endpartialdef", &valid_refs));
    }
    // `pos_close[1]`: the end tag's **end**, not its start.
    let source_end = endpartial.position.map(|position| position.1);

    let handle =
        parser.register_partial_def(token, name, inline, &body, source_start, source_end)?;
    Ok(NodeDesc::host(handle))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::desc::HOST_NODE;
    use crate::parser::tags::support::{self, PartialHost};

    fn parse(source: &str) -> (NodeDesc, PartialHost) {
        // The host has to outlive the parse for its recording to be read, so the
        // helper returns it rather than a borrowed descriptor.
        let host = PartialHost::default();
        let node = support::parse_with(source, &host).nodes.remove(0);
        (node, host)
    }

    #[test]
    fn a_plain_definition_records_the_body_and_is_not_inline() {
        let (node, host) = parse("{% partialdef p %}x{% endpartialdef %}");
        assert_eq!(node.class_name, HOST_NODE);
        let defs = host.defs.borrow();
        assert_eq!(defs.len(), 1);
        assert_eq!(defs[0].name, "p");
        assert!(!defs[0].inline);
        assert_eq!(defs[0].body, vec!["x"]);
    }

    #[test]
    fn inline_is_a_third_bit_and_not_a_parameter() {
        let (_, host) = parse("{% partialdef p inline %}x{% endpartialdef %}");
        assert!(host.defs.borrow()[0].inline);
    }

    #[test]
    fn the_inline_message_is_the_three_bit_arms_and_not_a_shape_rule() {
        // Review Focus, arm 2 — and the row exists because the plan wrote it
        // with the wrong source. It predicted `{% partialdef p inline x %}`
        // would take arm 2; measured on the pinned tree it does not, because
        // that token has **four** bits and falls through to the wildcard. The
        // arm is reached only by a *three*-bit token whose third bit is not
        // `inline`. So the two inputs below differ by one bit and get different
        // messages, which is the whole reason the arms are not a length check:
        // `{% partialdef p q %}` is "the inline argument has parameters" and
        // `{% partialdef p inline x %}` is "at most 2 arguments".
        assert_eq!(
            support::failure("{% partialdef p q %}z{% endpartialdef %}").message(),
            "The 'inline' argument does not have any parameters; either use \
             'inline' or remove it completely.",
        );
        assert_eq!(
            support::failure("{% partialdef p inline x %}z{% endpartialdef %}").message(),
            "'partialdef' tag takes at most 2 arguments",
        );
    }

    #[test]
    fn no_name_and_too_many_arguments_are_two_messages() {
        assert_eq!(
            support::failure("{% partialdef %}").message(),
            "'partialdef' tag requires a name",
        );
        // Four bits: the wildcard arm, and a *different* string from the three-
        // bit one above.
        assert_eq!(
            support::failure("{% partialdef p q r %}").message(),
            "'partialdef' tag takes at most 2 arguments",
        );
    }

    #[test]
    fn the_accepted_end_spellings_are_exactly_two() {
        // `valid_endpartials = ("endpartialdef", f"endpartialdef {partial_name}")`
        // (`defaulttags.py:1237`), and both are accepted. Nothing else is.
        let (_, host) = parse("{% partialdef p %}x{% endpartialdef %}");
        assert_eq!(host.defs.borrow()[0].name, "p");
        let (_, host) = parse("{% partialdef p %}x{% endpartialdef p %}");
        assert_eq!(host.defs.borrow()[0].name, "p");
    }

    #[test]
    fn whitespace_stops_the_scan_and_then_fails_the_whole_contents_check() {
        // Review Focus 3, and the reason there are *two* checks on one token.
        // Django's scan matches the first word (`base.py:561`, `base.py:564`)
        // and the validation compares the whole contents
        // (`defaulttags.py:1244`), so `endpartialdef  p` terminates the block and
        // is then refused. Measured on the pinned tree: two spaces, three spaces
        // and a tab all print this same message, and the message prints the
        // *first word*.
        for source in [
            "{% partialdef p %}x{% endpartialdef  p %}",
            "{% partialdef p %}x{% endpartialdef   p %}",
            "{% partialdef p %}x{% endpartialdef\tp %}",
        ] {
            let message = support::failure(source).message();
            assert!(
                message.contains("Invalid block tag"),
                "{source:?}: {message}"
            );
            assert!(
                message.contains("'endpartialdef p'"),
                "{source:?}: {message}"
            );
        }
    }

    #[test]
    fn the_wrong_end_tag_name_is_invalid() {
        let message = support::failure("{% partialdef p %}x{% endpartialdef q %}").message();
        assert!(message.contains("Invalid block tag"), "{message}");
        assert!(message.contains("'endpartialdef p'"), "{message}");
    }

    #[test]
    fn an_unclosed_definition_names_both_accepted_end_tags() {
        let message = support::failure("{% partialdef p %}x").message();
        assert!(message.contains("Unclosed tag"), "{message}");
        assert!(message.contains("endpartialdef p"), "{message}");
    }

    #[test]
    fn the_two_source_offsets_come_from_the_two_tokens() {
        // Debug-lexer positions, so this row is separate from the rest.
        // Expected values are computed from the source, not frozen:
        // `source_start` is the open tag's start, `source_end` the end tag's end.
        let source = "{% partialdef p %}x{% endpartialdef %}";
        let host = PartialHost::default();
        let mut parser = Parser::new(Lexer::debug(source).tokenize(), &host);
        parser.parse(&[]).expect("expected a successful parse");
        let defs = host.defs.borrow();
        assert_eq!(defs.len(), 1);
        // `texts(body)` is the body's text nodes, and the scan is over the
        // *whole* source, so the open tag's start is 0 and the end tag's end is
        // the source's length — computed here rather than written down.
        assert_eq!(defs[0].body, vec!["x"]);
        assert_eq!(defs[0].source_start, Some(0));
        assert_eq!(defs[0].source_end, Some(source.len()));
    }
}
