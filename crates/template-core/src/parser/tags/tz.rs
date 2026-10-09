//! `{% localtime %}`, `{% timezone %}` and `{% get_current_timezone %}` —
//! `django/templatetags/tz.py` @ 6.0.9.
//!
//! # Three tags, and the three filters beside them are not here
//!
//! The file registers six names: three tags and three filters (`localtime`,
//! `timezone`, `utc`, `tz.py:20,30,37`). Only the tags are compiled here; the
//! three filter *bodies* are M12 Task 7's Rust port and this task transcribes
//! their Python instead. That division is the milestone's own — the five files
//! are interposed whole, and no version of interposing `django.templatetags.tz`
//! leaves its filters Django's — so this module's absence of filters is a task
//! boundary and not an oversight.
//!
//! # The arguments are checked before the body is parsed, in two of the three
//!
//! `localtime_tag` splits its token and validates `bits[1]` *before*
//! `parser.parse(("endlocaltime",))` (`tz.py:145-152`), and `timezone_tag` does
//! the same before `parser.parse(("endtimezone",))` (`tz.py:172-176`). The order
//! is load-bearing and it is observable in both directions:
//!
//! ```text
//! {% localtime maybe %}x        → TemplateSyntaxError: 'localtime' argument should be 'on' or 'off'
//! {% localtime %}x              → TemplateSyntaxError: Unclosed tag on line 1: 'localtime'. …
//! {% timezone %}x               → TemplateSyntaxError: 'timezone' takes one argument (timezone)
//! {% timezone "UTC" %}x         → TemplateSyntaxError: Unclosed tag on line 1: 'timezone'. …
//! ```
//!
//! All four measured on the pinned tree. A port that parsed the body first —
//! which is `cache.rs`'s shape and so the tempting one — would report the
//! *unclosed block* for the first and third templates instead of the argument
//! error, and every Rust test below would still pass if it only checked the two
//! valid forms.
//!
//! # `%r` of the argument list, and why it goes through the host
//!
//! `get_current_timezone_tag` interpolates its split `args` — a Python **list** —
//! with `%r` (`tz.py:197-199`), so the message carries each element's `repr`:
//!
//! ```text
//! {% get_current_timezone nope %} → 'get_current_timezone' requires 'as variable'
//!                                    (got ['get_current_timezone', 'nope'])
//! ```
//!
//! `args` is arbitrary template text, so each element goes through
//! [`Parser::py_repr`] — the host's Python `repr` — rather than
//! [`py_repr_str`](crate::parser::tags::py_repr_str), whose ASCII-only assertion
//! exists for exactly this reason (see its doc). The `py_list_repr` helper in
//! `tags/mod.rs` is the constant-argument case of the same shape and is not
//! reused here because its input is a `&[&str]` that is ASCII by construction.
//!
//! [`Parser::py_repr`]: crate::parser::Parser::py_repr

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::{python_list_repr, split_contents};
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

/// `localtime_tag` (`tz.py:136-154`) — `{% localtime [on|off] %}…{% endlocaltime %}`.
///
/// `bits` has one, two or more entries and each means something different:
/// nothing is `use_tz = True`, a single `on`/`off` is the boolean it names, and
/// anything else (a second bit, or a first bit that is not `on`/`off`) is the
/// syntax error below. `bits[0]` is always present — `Parser::parse` rejects an
/// empty block tag before it dispatches — so the index is safe.
pub fn localtime(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;

    let use_tz = if bits.len() == 1 {
        // `if len(bits) == 1: use_tz = True` (`tz.py:146-147`).
        true
    } else if bits.len() > 2 || (bits[1] != "on" && bits[1] != "off") {
        // `"%r argument should be 'on' or 'off'" % bits[0]` (`tz.py:148-149`) —
        // `%r`, so the tag name goes through the host's `repr`.
        return Err(Error::syntax(format!(
            "{} argument should be 'on' or 'off'",
            parser.py_repr(&bits[0]),
        )));
    } else {
        // `use_tz = bits[1] == "on"` (`tz.py:151`).
        bits[1] == "on"
    };

    // The body is parsed *after* the argument check — see the module doc.
    let nodelist = parser.parse(&["endlocaltime"])?;
    parser.delete_first_token();
    Ok(
        NodeDesc::with_nodelist("LocalTimeNode", "nodelist", nodelist)
            .attr("use_tz", AttrValue::Bool(use_tz)),
    )
}

/// `timezone_tag` (`tz.py:158-178`) — `{% timezone <expr> %}…{% endtimezone %}`.
///
/// The argument is one filter expression, compiled before the body is parsed
/// (`tz.py:175-176`), and the node stores the compiled expression rather than
/// its value — `TimezoneNode.render` resolves it against the context
/// (`tz.py:117`).
pub fn timezone(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    if bits.len() != 2 {
        // `"'%s' takes one argument (timezone)" % bits[0]` (`tz.py:173-174`) —
        // `%s`, so no host `repr` is needed.
        return Err(Error::syntax(format!(
            "'{}' takes one argument (timezone)",
            bits[0],
        )));
    }

    let tz = parser.compile_filter(&bits[1])?;

    // After the argument check, as above.
    let nodelist = parser.parse(&["endtimezone"])?;
    parser.delete_first_token();
    Ok(NodeDesc::with_nodelist("TimezoneNode", "nodelist", nodelist).attr("tz", tz))
}

/// `get_current_timezone_tag` (`tz.py:182-200`) — `{% get_current_timezone as name %}`.
///
/// No body: the tag stores the name of the active time zone into the context
/// variable it is given, and it reads its arguments with `token.contents.split()`
/// rather than `token.split_contents()` — the source says why (`tz.py:193-194`):
/// the tag accepts no variable arguments, so the quote-aware splitter is not
/// wanted.
pub fn get_current_timezone(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 3 || args[1] != "as" {
        // `"'get_current_timezone' requires 'as variable' (got %r)" % args`
        // (`tz.py:197-199`) — `%r` of the whole list. See the module doc.
        return Err(Error::syntax(format!(
            "'get_current_timezone' requires 'as variable' (got {})",
            python_list_repr(parser, &args),
        )));
    }
    Ok(NodeDesc::new("GetCurrentTimezoneNode")
        .attr("variable", AttrValue::Str(args[2].to_string())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile_loaded, failure_loaded};

    fn filter_token(value: &AttrValue) -> String {
        let AttrValue::FilterExpr(expression) = value else {
            panic!("not a FilterExpr: {value:?}");
        };
        expression.token.clone()
    }

    #[test]
    fn localtime_with_no_argument_is_on() {
        let node = compile_loaded("{% localtime %}x{% endlocaltime %}");
        assert_eq!(node.class_name, "LocalTimeNode");
        assert_eq!(node.nodelists.len(), 1);
        assert_eq!(node.nodelists[0].0, "nodelist", "`LocalTimeNode.__init__`");
        assert_eq!(
            attr(&node, "use_tz"),
            &AttrValue::Bool(true),
            "no argument is `use_tz = True`, and it stays a bool on the wire",
        );
    }

    #[test]
    fn localtime_reads_on_and_off_as_booleans() {
        for (argument, expected) in [("on", true), ("off", false)] {
            let source = format!("{{% localtime {argument} %}}x{{% endlocaltime %}}");
            assert_eq!(
                attr(&compile_loaded(&source), "use_tz"),
                &AttrValue::Bool(expected),
                "{source}",
            );
        }
    }

    #[test]
    fn localtime_rejects_a_first_bit_that_is_not_on_or_off() {
        assert_eq!(
            failure_loaded("{% localtime maybe %}x{% endlocaltime %}").kind,
            ErrorKind::Syntax("'localtime' argument should be 'on' or 'off'".to_string()),
        );
    }

    #[test]
    fn localtime_rejects_a_second_bit_even_when_the_first_is_valid() {
        // `len(bits) > 2` OR a bad `bits[1]` — so `on off` is an error, not
        // "read the first and ignore the rest".
        assert_eq!(
            failure_loaded("{% localtime on off %}x{% endlocaltime %}").kind,
            ErrorKind::Syntax("'localtime' argument should be 'on' or 'off'".to_string()),
        );
    }

    #[test]
    fn localtime_checks_its_argument_before_it_parses_the_body() {
        // The unclosed block is *not* what Django reports here, because the
        // argument check runs first (`tz.py:145-149`). Measured. A port that
        // parsed the body first would report `Unclosed tag` instead.
        assert_eq!(
            failure_loaded("{% localtime maybe %}x").kind,
            ErrorKind::Syntax("'localtime' argument should be 'on' or 'off'".to_string()),
        );
        // And the control: with a *valid* argument the missing terminator is
        // what is reported, so the row above is measuring the order and not a
        // blanket refusal.
        assert_eq!(
            failure_loaded("{% localtime %}x").kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 1: 'localtime'. Looking for one of: endlocaltime."
                    .to_string(),
            ),
        );
    }

    #[test]
    fn timezone_compiles_its_argument_as_a_filter_expression() {
        let node = compile_loaded(r#"{% timezone "Europe/Paris" %}x{% endtimezone %}"#);
        assert_eq!(node.class_name, "TimezoneNode");
        assert_eq!(node.nodelists.len(), 1);
        assert_eq!(node.nodelists[0].0, "nodelist", "`TimezoneNode.__init__`");
        assert_eq!(
            filter_token(attr(&node, "tz")),
            r#""Europe/Paris""#,
            "the argument is a FilterExpression, quotes and all — it is resolved at render",
        );
    }

    #[test]
    fn timezone_takes_exactly_one_argument() {
        // Both directions: none and two. `"'%s' takes one argument (timezone)"`.
        for source in [
            "{% timezone %}x{% endtimezone %}",
            "{% timezone a b %}x{% endtimezone %}",
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax("'timezone' takes one argument (timezone)".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn timezone_checks_its_argument_before_it_parses_the_body() {
        // The same order test as `{% localtime %}`, for the same measured
        // reason, with its own valid-argument control.
        assert_eq!(
            failure_loaded("{% timezone %}x").kind,
            ErrorKind::Syntax("'timezone' takes one argument (timezone)".to_string()),
        );
        assert_eq!(
            failure_loaded("{% timezone \"UTC\" %}x").kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 1: 'timezone'. Looking for one of: endtimezone.".to_string(),
            ),
        );
    }

    #[test]
    fn get_current_timezone_stores_the_name_it_is_given() {
        let node = compile_loaded("{% get_current_timezone as TIME_ZONE %}");
        assert_eq!(node.class_name, "GetCurrentTimezoneNode");
        assert!(
            node.nodelists.is_empty(),
            "the tag reads no body: `GetCurrentTimezoneNode` takes only `variable`",
        );
        assert_eq!(
            attr(&node, "variable"),
            &AttrValue::Str("TIME_ZONE".to_string()),
            "a plain Str, not a FilterExpression — it is the variable's *name*",
        );
    }

    #[test]
    fn get_current_timezone_requires_the_literal_word_as() {
        // `len(args) != 3 or args[1] != "as"` (`tz.py:196`): neither the bare
        // two-word form nor a three-word form whose middle word is not the
        // literal `as`. The expected messages are written out rather than
        // derived from the implementation's own helper — a message built by
        // calling `python_list_repr` again agrees with itself by construction.
        for (args, expected) in [
            (
                "get_current_timezone",
                "'get_current_timezone' requires 'as variable' (got ['get_current_timezone'])",
            ),
            (
                "get_current_timezone nope",
                "'get_current_timezone' requires 'as variable' \
                 (got ['get_current_timezone', 'nope'])",
            ),
            (
                "get_current_timezone of v",
                "'get_current_timezone' requires 'as variable' \
                 (got ['get_current_timezone', 'of', 'v'])",
            ),
        ] {
            assert_eq!(
                failure_loaded(&format!("{{% {args} %}}")).kind,
                ErrorKind::Syntax(expected.to_string()),
                "{args}",
            );
        }
    }
}
