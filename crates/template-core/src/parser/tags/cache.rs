//! `{% cache %}` — `django/templatetags/cache.py` @ 6.0.9.
//!
//! # The body is parsed before the arguments are looked at, and that is visible
//!
//! `do_cache` parses to `{% endcache %}` and consumes the terminator
//! (`cache.py:84-85`) *before* it splits the token or checks how many arguments
//! there are (`cache.py:86-88`). The order is load-bearing in both directions:
//!
//! ```text
//! {% cache 500 %}{% if %}{% endcache %}
//!   → TemplateSyntaxError: Unexpected end of expression in if tag.
//! {% cache 500 name %}
//!   → TemplateSyntaxError: Unclosed tag on line 1: 'cache'. Looking for one of:
//!                          endcache.
//! ```
//!
//! Both measured on the pinned tree, and neither is the argument-count message
//! (`''cache'' tag requires at least 2 arguments.`) that `{% cache %}` and
//! `{% cache 500 %}` produce. A port that checked the count first, which reads
//! more naturally, would report the second template above as a missing argument
//! — one wrong message, and wrong in the direction that hides the real mistake.
//!
//! # The doubled quote in the message is Django's, not a typo here
//!
//! `"'%r' tag requires at least 2 arguments." % tokens[0]` (`cache.py:88`) puts
//! `%r` inside a pair of literal single quotes, and `%r` of a `str` is itself
//! single-quoted — so the message for `{% cache %}` is:
//!
//! ```text
//! ''cache'' tag requires at least 2 arguments.
//! ```
//!
//! Reproduced exactly, through [`Parser::py_repr`], which is the host's Python
//! `repr` rather than [`py_repr_str`](crate::parser::tags::py_repr_str): the
//! doubling is what the message *says*, and "fixing" the quotes would be the
//! divergence.
//!
//! # `using=` is read from the last bit only, and before `tokens[1]`
//!
//! `tokens[-1].startswith("using=")` (`cache.py:89`) — the *last* bit, so a
//! `using=` in the middle is an ordinary `vary_on` argument, compiled as a
//! `FilterExpression` and rejected as one:
//!
//! ```text
//! {% cache 500 sidebar using="other" page %}
//!   → TemplateSyntaxError: Could not parse the remainder: '="other"' from
//!                          'using="other"'
//! ```
//!
//! The order inside the branch is Django's too, and this file keeps it:
//! `cache_name` is compiled from `tokens[-1]` (`cache.py:90`) before
//! `tokens = tokens[:-1]` (`cache.py:91`), and then the `CacheNode(...)` call
//! evaluates its arguments left to right (`cache.py:96-98`) — `tokens[1]`, then `tokens[3:]`. The last
//! of those is the only one a template can tell apart, since two bad
//! `compile_filter` calls on one parser report whichever ran first:
//!
//! ```text
//! {% cache a=b name c=d %}   → ...from 'a=b'   (expire_time_var, not vary_on)
//! ```
//!
//! Measured, and asserted below, because `compile_filter` is lenient enough that
//! a port which reordered them would pass every other template in this file.

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

const END: &str = "endcache";

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Django's order: the body first, the terminator consumed, and only then the
    // arguments. See the module doc for the two templates this decides.
    let nodelist = parser.parse(&[END])?;
    parser.delete_first_token();

    let mut tokens = split_contents(token)?;
    if tokens.len() < 3 {
        // `"'%r' tag requires at least 2 arguments." % tokens[0]` (`cache.py:88`)
        // — `%r`, so this one goes through the host. `tokens[0]` always exists:
        // `Parser::parse` rejects an empty block tag before dispatching.
        return Err(Error::syntax(format!(
            "'{}' tag requires at least 2 arguments.",
            parser.py_repr(&tokens[0]),
        )));
    }

    // `if len(tokens) > 3 and tokens[-1].startswith("using=")` (`cache.py:89`).
    // Strictly greater than three, so `{% cache 500 name using=x %}` — exactly
    // four bits — takes the branch while `{% cache 500 name %}` does not reach
    // it at all. `{% cache 500 using=x %}` has three bits and is rejected above,
    // with `using=x` read as the fragment name.
    let cache_name = if tokens.len() > 3 && tokens[tokens.len() - 1].starts_with("using=") {
        let source = tokens[tokens.len() - 1]
            .strip_prefix("using=")
            .expect("`starts_with` just checked the prefix");
        let compiled = parser.compile_filter(source)?;
        tokens.truncate(tokens.len() - 1);
        compiled
    } else {
        // `cache_name = None` (`cache.py:93`) — emitted rather than omitted, so
        // the descriptor names every parameter `CacheNode.__init__` takes
        // (`cache.py:9`).
        AttrValue::None
    };

    // Left to right, as the `CacheNode(...)` call evaluates them.
    let expire_time_var = parser.compile_filter(&tokens[1])?;
    // `tokens[2]  # fragment_name can't be a variable.` (`cache.py:97`) — the
    // source comment is why this is a `Str` and not a `FilterExpr`, and it is
    // why `{% cache 500 name %}` and `{% cache 500 "name" %}` have *different*
    // fragment names rather than the quotes being stripped by a filter parse.
    let fragment_name = AttrValue::Str(tokens[2].clone());
    let vary_on = AttrValue::List(
        tokens
            .get(3..)
            .unwrap_or_default()
            .iter()
            .map(|bit| parser.compile_filter(bit))
            .collect::<Result<Vec<_>, _>>()?,
    );

    Ok(NodeDesc::with_nodelist("CacheNode", "nodelist", nodelist)
        .attr("expire_time_var", expire_time_var)
        .attr("fragment_name", fragment_name)
        .attr("vary_on", vary_on)
        .attr("cache_name", cache_name))
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

    fn vary_on_tokens(node: &NodeDesc) -> Vec<String> {
        let AttrValue::List(items) = attr(node, "vary_on") else {
            panic!("vary_on is not a List: {:?}", attr(node, "vary_on"));
        };
        items.iter().map(filter_token).collect()
    }

    #[test]
    fn cache_parses_its_body_and_names_every_constructor_parameter() {
        let node = compile_loaded("{% cache 500 sidebar %}body{% endcache %}");
        assert_eq!(node.class_name, "CacheNode");
        assert_eq!(node.nodelists.len(), 1);
        assert_eq!(node.nodelists[0].0, "nodelist", "`CacheNode.__init__`");
        assert_eq!(
            filter_token(attr(&node, "expire_time_var")),
            "500",
            "the timeout is a FilterExpression even when it is an integer literal",
        );
        assert_eq!(
            attr(&node, "fragment_name"),
            &AttrValue::Str("sidebar".to_string()),
            "a plain Str: `fragment_name can't be a variable`",
        );
        assert_eq!(vary_on_tokens(&node), Vec::<String>::new());
        assert_eq!(
            attr(&node, "cache_name"),
            &AttrValue::None,
            "no `using=` is Django's `cache_name = None`, emitted as None",
        );
    }

    #[test]
    fn cache_reads_every_bit_after_the_name_as_a_vary_on_argument() {
        let node =
            compile_loaded("{% cache 500 sidebar request.user.username page %}x{% endcache %}");
        assert_eq!(vary_on_tokens(&node), vec!["request.user.username", "page"],);
    }

    #[test]
    fn cache_reads_a_trailing_using_as_the_cache_name() {
        let named = compile_loaded(r#"{% cache 500 sidebar using="other" %}x{% endcache %}"#);
        assert_eq!(
            filter_token(attr(&named, "cache_name")),
            r#""other""#,
            "compiled from the bit with `using=` removed, quotes and all",
        );
        assert_eq!(
            vary_on_tokens(&named),
            Vec::<String>::new(),
            "and the `using=` bit is dropped from `vary_on`",
        );
    }

    #[test]
    fn cache_treats_a_using_that_is_not_last_as_a_vary_on_argument() {
        // Measured: the middle `using=` is compiled as a filter expression like
        // any other vary_on bit, which is what makes it fail — `=` is not valid
        // in a `Variable`. The failure *is* the evidence that the branch was not
        // taken, and it is why this row exists rather than a row asserting a
        // `using=` that happens to compile either way.
        assert_eq!(
            failure_loaded(r#"{% cache 500 sidebar using="other" page %}x{% endcache %}"#).kind,
            ErrorKind::Syntax(
                r#"Could not parse the remainder: '="other"' from 'using="other"'"#.to_string(),
            ),
        );
    }

    #[test]
    fn cache_needs_three_bits_and_says_so_with_a_doubled_quote() {
        // `"'%r' tag requires at least 2 arguments." % "cache"` — the `%r` is
        // already quoted and the literal adds a second pair. Measured.
        for source in [
            "{% cache %}x{% endcache %}",
            "{% cache 500 %}x{% endcache %}",
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax("''cache'' tag requires at least 2 arguments.".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn cache_parses_the_body_before_it_checks_the_arguments() {
        // The body has two bits, so the count check would pass — and the body's
        // own error still wins, because `do_cache` parsed it first
        // (`cache.py:84`). Measured.
        assert_eq!(
            failure_loaded("{% cache 500 %}{% if %}{% endcache %}").kind,
            ErrorKind::Syntax("Unexpected end of expression in if tag.".to_string()),
        );
    }

    #[test]
    fn cache_reports_the_unclosed_block_before_the_argument_count() {
        // The other half of the same ordering: with no `{% endcache %}` the
        // length check never runs, so `{% cache 500 name %}` reports the missing
        // terminator rather than a missing argument. Measured — and this is the
        // message a count-first port gets wrong.
        assert_eq!(
            failure_loaded("{% cache 500 name %}").kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 1: 'cache'. Looking for one of: endcache.".to_string(),
            ),
        );
    }

    #[test]
    fn cache_compiles_the_expire_time_before_any_vary_on_bit() {
        // Both operands are unparseable and both would raise the same *shape* of
        // message, so this row is the only place the evaluation order in
        // `cache.py:96-98` is observable. Measured: Django names `a=b`, the
        // first argument, not `c=d`.
        assert_eq!(
            failure_loaded("{% cache a=b name c=d %}x{% endcache %}").kind,
            ErrorKind::Syntax("Could not parse the remainder: '=b' from 'a=b'".to_string()),
        );
    }

    #[test]
    fn cache_does_not_reject_an_empty_using() {
        // `"using=".removeprefix("using=")` is `""`, and `compile_filter("")`
        // succeeds rather than raising — so this is a template Django accepts,
        // and the descriptor carries an empty filter expression where a port
        // that validated the name would refuse. Measured.
        let node = compile_loaded("{% cache 500 sidebar using= %}x{% endcache %}");
        assert_eq!(filter_token(attr(&node, "cache_name")), "");
    }

    #[test]
    fn cache_takes_its_fragment_name_from_the_third_bit_verbatim() {
        // Measured: the quotes are part of the name, because the fragment name
        // is never compiled as a filter expression.
        let node = compile_loaded(r#"{% cache 500 "sidebar" %}x{% endcache %}"#);
        assert_eq!(
            attr(&node, "fragment_name"),
            &AttrValue::Str(r#""sidebar""#.to_string()),
        );
    }
}
