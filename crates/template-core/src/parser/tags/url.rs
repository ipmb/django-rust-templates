//! `{% url %}` — `defaulttags.url` (`defaulttags.py:1514-1586`).
//!
//! # Compile calls no reversal
//!
//! The view name and every argument are `compile_filter`ed and nothing is resolved
//! (`defaulttags.py:1565`, `defaulttags.py:1574-1584`). `ViewDoesNotExist` and
//! `NoReverseMatch` are `URLNode.render`'s, and are unported here. A body that
//! looked the view name up at parse time would diverge on the first template whose
//! URLconf is not yet loaded, which is every template during a `manage.py` startup
//! check.
//!
//! # Each bit is classified by `kwarg_re.match`, and `as` is read from the end
//!
//! `bits = bits[2:]`, then a trailing `as` (`len(bits) >= 2 and bits[-2] == "as"`,
//! `:1568-1571`) is stripped, then every surviving bit goes through
//! [`match_kwarg`]. A participating name group makes it a kwarg
//! (`kwargs[name] = compile_filter(value)`), its absence makes it positional
//! (`args.append(compile_filter(value))`) — the `if name:` on `:1581` is the
//! group's presence, since `\w+` can never capture an empty string.
//!
//! Note `as` is stripped **before** classification, so `{% url 'v' as u %}` has
//! an empty `args` and no `as` in it, while `{% url 'v' a as u %}` has `a` as
//! its only positional.
//!
//! # The "Malformed" branch is unreachable from a token, and is kept anyway
//!
//! `if not match: raise TemplateSyntaxError("Malformed arguments to url tag")`
//! (`defaulttags.py:1575-1577`) cannot fire: `kwarg_re`'s value group is `(.+)`
//! and Python's `.` does not cross `\n`, so `match` fails only for an empty or
//! `\n`-leading bit, and no bit `token.split_contents` produces is either
//! ([`match_kwarg`] carries the measurement). The branch is ported because it is
//! in Django's source and its message is part of the tag's surface; the test below
//! drives [`classify_bits`] with a `\n`-leading bit, which no template can reach,
//! so the branch is exercised rather than merely written.
//!
//! # The descriptor is positional, and `asvar` is always present
//!
//! `URLNode(view_name, args, kwargs, asvar)` (`defaulttags.py:479-483`) — all four
//! are plain attributes, and `:1585` passes `asvar` on both paths, so it is
//! emitted as `AttrValue::None` when there is no `as`.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::kwargs::{match_kwarg, Kwargs};
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let mut bits = split_contents(token)?;

    if bits.len() < 2 {
        // `"'%s' takes at least one argument, a URL pattern name." % bits[0]`
        // (`defaulttags.py:1562-1564`) — the quotes are literal in the format
        // string and `%s` of the command name adds no escaping of its own.
        return Err(Error::syntax(format!(
            "'{}' takes at least one argument, a URL pattern name.",
            bits[0],
        )));
    }

    let view_name = parser.compile_filter(&bits[1])?;
    bits.drain(..2);

    // `bits[-2] == "as"`, read from the end before classification.
    let asvar = if bits.len() >= 2 && bits[bits.len() - 2] == "as" {
        let name = bits.pop().expect("len >= 2 checked above");
        bits.pop();
        AttrValue::Str(name)
    } else {
        AttrValue::None
    };

    let (args, kwargs) = {
        let mut compile_filter = |source: &str| parser.compile_filter(source);
        classify_bits(&bits, &mut compile_filter)?
    };

    Ok(NodeDesc::new("URLNode")
        .attr("view_name", view_name)
        .attr("args", AttrValue::List(args))
        .attr("kwargs", AttrValue::Map(kwargs))
        .attr("asvar", asvar))
}

/// Django's classification loop (`defaulttags.py:1574-1584`): each bit becomes a
/// kwarg or a positional, and the first bit `kwarg_re.match` refuses raises.
///
/// Split out from [`compile`] for one reason: the `None` arm is unreachable
/// through a token (see the module docs), and a private function taking the bits
/// directly is the only way to exercise it. The error text is Django's.
fn classify_bits(
    bits: &[String],
    compile_filter: &mut dyn FnMut(&str) -> Result<AttrValue, Error>,
) -> Result<(Vec<AttrValue>, Kwargs), Error> {
    let mut args = Vec::new();
    let mut kwargs = Kwargs::new();
    for bit in bits {
        let Some((name, value)) = match_kwarg(bit) else {
            return Err(Error::syntax("Malformed arguments to url tag"));
        };
        match name {
            Some(name) => kwargs.push((name.to_string(), compile_filter(value)?)),
            None => args.push(compile_filter(value)?),
        }
    }
    Ok((args, kwargs))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure};

    fn args(node: &NodeDesc) -> Vec<&str> {
        let AttrValue::List(values) = attr(node, "args") else {
            panic!("args is a List");
        };
        values
            .iter()
            .map(|value| {
                let AttrValue::FilterExpr(expression) = value else {
                    panic!("each positional is a FilterExpr");
                };
                expression.token.as_str()
            })
            .collect()
    }

    fn kwargs(node: &NodeDesc) -> Vec<(String, String)> {
        let AttrValue::Map(pairs) = attr(node, "kwargs") else {
            panic!("kwargs is a Map");
        };
        pairs
            .iter()
            .map(|(key, value)| {
                let AttrValue::FilterExpr(expression) = value else {
                    panic!("each kwarg value is a FilterExpr");
                };
                (key.clone(), expression.token.clone())
            })
            .collect()
    }

    fn view_name(node: &NodeDesc) -> String {
        let AttrValue::FilterExpr(expression) = attr(node, "view_name") else {
            panic!("view_name is a FilterExpr");
        };
        expression.token.clone()
    }

    #[test]
    fn url_compiles_the_view_name_and_no_arguments() {
        let node = compile("{% url 'view' %}");
        assert_eq!(node.class_name, "URLNode");
        assert_eq!(view_name(&node), "'view'");
        assert!(args(&node).is_empty());
        assert!(kwargs(&node).is_empty());
        assert_eq!(attr(&node, "asvar"), &AttrValue::None);
    }

    #[test]
    fn positionals_and_kwargs_are_separated_by_the_name_group() {
        let mixed = compile("{% url 'view' a k=v %}");
        assert_eq!(args(&mixed), vec!["a"]);
        assert_eq!(kwargs(&mixed), vec![("k".to_string(), "v".to_string())]);

        // Order is preserved: two kwargs stay in the order written.
        let two = compile("{% url 'view' b=2 a=1 %}");
        assert_eq!(
            kwargs(&two),
            vec![
                ("b".to_string(), "2".to_string()),
                ("a".to_string(), "1".to_string()),
            ],
        );
    }

    #[test]
    fn the_kwarg_value_is_a_filter_expression_like_a_positional() {
        // The value side goes through `compile_filter`, filter chain and all.
        let node = compile("{% url 'view' k=v|default:'x' %}");
        assert_eq!(
            kwargs(&node),
            vec![("k".to_string(), "v|default:'x'".to_string())],
        );
    }

    #[test]
    fn url_name_can_be_a_variable() {
        let node = compile("{% url url_name a %}");
        assert_eq!(view_name(&node), "url_name");
        assert_eq!(args(&node), vec!["a"]);
    }

    #[test]
    fn as_is_read_from_the_end_before_classification() {
        let node = compile("{% url 'view' a k=v as u %}");
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("u".to_string()));
        assert_eq!(args(&node), vec!["a"]);
        assert_eq!(kwargs(&node), vec![("k".to_string(), "v".to_string())]);

        // A lone `as u` leaves no arguments at all — the `as` never reaches
        // classification.
        let bare = compile("{% url 'view' as u %}");
        assert_eq!(attr(&bare, "asvar"), &AttrValue::Str("u".to_string()));
        assert!(args(&bare).is_empty());
        assert!(kwargs(&bare).is_empty());
    }

    #[test]
    fn a_digit_is_a_valid_kwarg_name_because_word_includes_it() {
        // `\w` admits digits: `{% url 'v' 1=2 %}` keys `1`.
        let node = compile("{% url 'view' 1=2 %}");
        assert_eq!(kwargs(&node), vec![("1".to_string(), "2".to_string())]);
    }

    #[test]
    fn url_requires_the_pattern_name() {
        assert_eq!(
            failure("{% url %}").kind,
            ErrorKind::Syntax("'url' takes at least one argument, a URL pattern name.".to_string()),
        );
    }

    #[test]
    fn the_value_reaches_the_filter_parser_whole_so_its_error_surfaces() {
        // `kwarg_re.match("nope=")` keys nothing and takes `"nope="` as the
        // value, so this is a compile_filter failure, not the Malformed one.
        // Measured on the pinned tree: Django raises exactly this.
        assert_eq!(
            failure("{% url 'view' k=v nope= %}").kind,
            ErrorKind::Syntax("Could not parse the remainder: '=' from 'nope='".to_string()),
        );
    }

    #[test]
    fn the_malformed_branch_fires_for_a_bit_no_token_can_produce() {
        // Unreachable through `split_contents` (a bit never starts with `\n`),
        // so it is driven directly. `"\\n"` is the distinguishing input:
        // `kwarg_re`'s `(.+)` cannot cross the newline, and `(\w+)=` cannot
        // match, so Python's `match` returns `None`.
        let mut compile = |source: &str| Ok(AttrValue::Str(source.to_string()));
        let bits = vec!["\n".to_string()];
        assert_eq!(
            classify_bits(&bits, &mut compile)
                .expect_err("the Malformed branch")
                .kind,
            ErrorKind::Syntax("Malformed arguments to url tag".to_string()),
        );

        // And the same bits vector is fine once the leading newline is gone, so
        // the test above fails for the reason it claims rather than because the
        // helper rejects everything.
        let ok = vec!["a".to_string()];
        let (args, kwargs) = classify_bits(&ok, &mut compile).expect("ordinary bit");
        assert_eq!(args.len(), 1);
        assert!(kwargs.is_empty());
    }
}
