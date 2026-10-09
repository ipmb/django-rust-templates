//! `{% include %}` — `django.template.loader_tags.do_include` (`loader_tags.py:316`).
//!
//! # The option loop is Django's, verbatim, and `support_legacy` is why
//!
//! `do_include` walks the bits after the template name one at a time
//! (`loader_tags.py:339-358`): a repeated option is refused *before* it is
//! interpreted, `with` reads its kwargs with **`support_legacy=False`**, `only`
//! is a flag, and anything else is an unknown argument. The `False` is load
//! bearing rather than a detail: `{% with %}` passes `True` (`defaulttags.py:1676`)
//! and `{% include %}` passes `False`, so `{% include "a" b as c %}` is an
//! unknown argument where `{% with b as c %}` is a legacy assignment — the same
//! helper, two behaviours, and the flag is the whole of the difference.
//!
//! It is also what makes `{% include "a" with b=1 nope %}` report `nope` rather
//! than reading it as a kwarg: `token_kwargs` stops at the first bit that is not
//! in kwarg form and leaves it in the list, and the loop then reaches it as an
//! option. Measured on the pin.
//!
//! # `allow_recursion=True`, and that is the one structural difference from
//! `{% extends %}`
//!
//! `construct_relative_path(…, allow_recursion=True)` (`loader_tags.py:361-365`)
//! is called with the opposite flag from `do_extends`', and it is observable:
//! `{% include "./index.html" %}` from a template named `index.html` compiles,
//! while the same shape under `{% extends %}` raises "was translated to template
//! name … the same template in which the tag appears". A path that escapes the
//! hierarchy is refused by both — that check is in `construct_relative_path` and
//! not behind the flag.
//!
//! # Three messages, all through `%r`
//!
//! `'%s' tag takes at least one argument…` is the exception — it interpolates
//! `bits[0]` with `%r` too (`loader_tags.py:334-337`), so all three go through
//! [`Parser::py_repr`], and one of them interpolates a *template token*
//! (`"Unknown argument for %r tag: %r." % (bits[0], option)`) whose escaping is
//! therefore observable.
//!
//! [`Parser::py_repr`]: crate::parser::Parser::py_repr

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::kwargs::{token_kwargs, Kwargs};
use crate::parser::relative::construct_relative_path;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // A dispatched block tag always has a first bit, so `bits[0]` is the command
    // and the length test is Django's `len(bits) < 2`.
    let mut bits = split_contents(token)?;
    let tag_name = bits[0].clone();
    if bits.len() < 2 {
        return Err(Error::syntax(format!(
            "{} tag takes at least one argument: the name of the template to be included.",
            parser.py_repr(&tag_name),
        )));
    }

    // Django iterates `bits[2:]` — everything after the template name, which
    // stays at `bits[1]` for `construct_relative_path` below.
    let mut remaining_bits = bits.split_off(2);
    let mut seen: Vec<String> = Vec::new();
    let mut isolated_context = false;
    let mut namemap: Kwargs = Vec::new();

    while !remaining_bits.is_empty() {
        let option = remaining_bits.remove(0);
        // The duplicate check is `if option in options`, and it runs *before*
        // the option is interpreted — which is why `{% include "a" nope nope %}`
        // reports the duplicate rather than the unknown argument.
        if seen.contains(&option) {
            return Err(Error::syntax(format!(
                "The {} option was specified more than once.",
                parser.py_repr(&option),
            )));
        }
        match option.as_str() {
            "with" => {
                // `support_legacy=false`, Django's argument here and the
                // opposite of `{% with %}`'s.
                let mut compile_filter = |source: &str| parser.compile_filter(source);
                namemap = token_kwargs(&mut remaining_bits, &mut compile_filter, false)?;
                if namemap.is_empty() {
                    return Err(Error::syntax(format!(
                        "\"with\" in {} tag needs at least one keyword argument.",
                        parser.py_repr(&tag_name),
                    )));
                }
            }
            // `options["only"] = True`; `options.get("only", False)` is what the
            // node is handed, so the flag and the default are one field here.
            "only" => isolated_context = true,
            other => {
                return Err(Error::syntax(format!(
                    "Unknown argument for {} tag: {}.",
                    parser.py_repr(&tag_name),
                    parser.py_repr(other),
                )));
            }
        }
        seen.push(option);
    }

    bits[1] = construct_relative_path(parser.origin_template_name(), &bits[1], true)?;
    let template = parser.compile_filter(&bits[1])?;

    // Django passes `extra_context=namemap` where `namemap = options.get("with",
    // {})` (`loader_tags.py:360`), so no `with` means an empty dict and not an
    // absent argument. `IncludeNode.__init__` stores `extra_context or {}`, which
    // collapses the two — an empty `Map` materialises as `{}` — so the
    // descriptor emits the pair either way, named for the parameter it fills.
    Ok(NodeDesc::new("IncludeNode")
        .attr("template", template)
        .attr("extra_context", AttrValue::Map(namemap))
        .attr("isolated_context", AttrValue::Bool(isolated_context)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, failure_with_origin};

    fn extra_context(node: &NodeDesc) -> Vec<String> {
        let AttrValue::Map(pairs) = attr(node, "extra_context") else {
            panic!("extra_context is a Map");
        };
        pairs.iter().map(|(key, _)| key.clone()).collect()
    }

    #[test]
    fn include_carries_its_options_and_has_no_nodelist() {
        let node = compile("{% include \"a.html\" %}");
        assert_eq!(node.class_name, "IncludeNode");
        let AttrValue::FilterExpr(expression) = attr(&node, "template") else {
            panic!("template is a FilterExpr");
        };
        // The token keeps the original quoting, exactly as `{% extends %}`'s
        // does: `compile_filter` sees what `construct_relative_path` returned.
        assert_eq!(expression.token, "\"a.html\"");
        assert_eq!(attr(&node, "isolated_context"), &AttrValue::Bool(false));
        assert_eq!(attr(&node, "extra_context"), &AttrValue::Map(vec![]));
        // `IncludeNode` declares `child_nodelists = ("nodelist",)` and never
        // assigns it, so the walk skips it and this node carries no nodelist at
        // all — the class of 14 the child_nodelists sweep counts.
        assert!(node.nodelists.is_empty());
    }

    #[test]
    fn include_reads_with_and_only() {
        let node = compile("{% include \"a.html\" with b=1 only %}");
        assert_eq!(attr(&node, "isolated_context"), &AttrValue::Bool(true));
        assert_eq!(extra_context(&node), vec!["b"]);
        // The value side is compiled by `token_kwargs`, so it is a real
        // FilterExpression and not the raw text.
        let AttrValue::Map(pairs) = attr(&node, "extra_context") else {
            panic!("extra_context is a Map");
        };
        let AttrValue::FilterExpr(expression) = &pairs[0].1 else {
            panic!("the value is a FilterExpr");
        };
        assert_eq!(expression.token, "1");
    }

    #[test]
    fn include_accepts_only_before_with() {
        // The loop order is the template's, not a fixed parse: `options` is a
        // dict filled as the bits are read, so either order works and neither
        // is a special case.
        let node = compile("{% include \"a.html\" only with b=1 %}");
        assert_eq!(attr(&node, "isolated_context"), &AttrValue::Bool(true));
        assert_eq!(extra_context(&node), vec!["b"]);
    }

    #[test]
    fn include_compiles_a_variable_name_as_readily_as_a_literal() {
        let node = compile("{% include template_name %}");
        let AttrValue::FilterExpr(expression) = attr(&node, "template") else {
            panic!("template is a FilterExpr");
        };
        let (is_var, _) = &expression.var;
        assert!(is_var, "`template_name` is a lookup");
    }

    #[test]
    fn includes_four_option_failures_are_djangos_exact_text() {
        // Every one of these is measured on the pinned tree, quote for quote —
        // the `%r` interpolation is what a hand-written expectation gets wrong.
        for (source, message) in [
            (
                "{% include %}",
                "'include' tag takes at least one argument: the name of the template to be included.",
            ),
            (
                "{% include \"a\" with %}",
                "\"with\" in 'include' tag needs at least one keyword argument.",
            ),
            (
                "{% include \"a\" only only %}",
                "The 'only' option was specified more than once.",
            ),
            (
                "{% include \"a\" nope %}",
                "Unknown argument for 'include' tag: 'nope'.",
            ),
        ] {
            assert_eq!(
                failure(source).kind,
                ErrorKind::Syntax(message.to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn a_repeated_option_is_refused_before_it_is_interpreted() {
        // `if option in options` runs first, so the second `with` is a
        // duplicate rather than a second kwargs run — and the message names the
        // option, not the tag.
        assert_eq!(
            failure("{% include \"a\" with b=1 with c=2 %}").kind,
            ErrorKind::Syntax("The 'with' option was specified more than once.".to_string()),
        );
    }

    #[test]
    fn the_non_legacy_flag_is_what_stops_token_kwargs_at_a_stray_bit() {
        // `support_legacy=False`: `token_kwargs` takes `b=1` and leaves `nope`,
        // which the loop then reaches as an option. Under `{% with %}`'s `True`
        // the same bits would be a legacy assignment.
        assert_eq!(
            failure("{% include \"a\" with b=1 nope %}").kind,
            ErrorKind::Syntax("Unknown argument for 'include' tag: 'nope'.".to_string()),
        );
        // A quoted second argument is not a name either — the tag takes exactly
        // one, and everything else is an option.
        assert_eq!(
            failure("{% include \"a\" \"b\" %}").kind,
            ErrorKind::Syntax("Unknown argument for 'include' tag: '\"b\"'.".to_string()),
        );
    }

    #[test]
    fn include_resolves_a_relative_name_with_recursion_allowed() {
        // The flag's whole effect. `{% extends %}` raises on this exact shape —
        // the name resolving to the template the tag is in — and `{% include %}`
        // does not, because it passes `allow_recursion=True`.
        let mut parser = Parser::new(
            crate::lexer::Lexer::new("{% include './index.html' %}").tokenize(),
            &crate::parser::host::NoHost,
        )
        .with_origin_template_name("index.html".to_string());
        let nodelist = parser.parse(&[]).expect("expected a successful parse");
        let AttrValue::FilterExpr(expression) = attr(&nodelist.nodes[0], "template") else {
            panic!("template is a FilterExpr");
        };
        assert_eq!(expression.token, "\"index.html\"");
    }

    #[test]
    fn include_still_refuses_a_path_that_escapes_the_hierarchy() {
        // The escape check is not behind `allow_recursion` — it is the other
        // branch of `construct_relative_path`, and both tags reach it.
        //
        // The message interpolates the **raw token**, quotes and all, so its
        // quoting is the input's and not Django's: `'../../x.html'` lands
        // inside `'%s'` as `''../../x.html''`. Both spellings are measured on
        // the pin, because this is exactly the pair a hand-written expectation
        // gets wrong — the double-quoted one *looks* like a Python repr and is
        // not one.
        for (source, message) in [
            (
                "{% include '../../x.html' %}",
                "The relative path ''../../x.html'' points outside the file hierarchy \
                 that template 'index.html' is in.",
            ),
            (
                "{% include \"../../x.html\" %}",
                "The relative path '\"../../x.html\"' points outside the file hierarchy \
                 that template 'index.html' is in.",
            ),
        ] {
            assert_eq!(
                failure_with_origin(source, Some("index.html")).kind,
                ErrorKind::Syntax(message.to_string()),
                "{source}",
            );
        }
    }
}
