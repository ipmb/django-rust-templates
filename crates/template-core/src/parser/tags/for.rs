//! `{% for %}` — `defaulttags.do_for` (`defaulttags.py:807-931`).
//!
//! # Three checks, in Django's order, each on the raw `token.contents`
//!
//! `do_for` reads `bits = token.split_contents()`, then
//!
//! 1. `len(bits) < 4` → `"'for' statements should have at least four words: %s"`;
//! 2. `bits[in_index] != "in"` → `"'for' statements should use the format 'for x
//!    in y': %s"`;
//! 3. any loop variable empty or containing an "invalid character" →
//!    `"'for' tag received an invalid argument: %s"`.
//!
//! All three interpolate `%s` of `token.contents` — **not** a `repr`, and not
//! the command name — so the message echoes the tag source verbatim, spacing and
//! all. Measured on the pinned tree: `{% for a b in y %}` reports
//! `'for' tag received an invalid argument: for a b in y`.
//!
//! The order matters and is preserved: arity before the `in` keyword, the `in`
//! keyword before the variable shape, and the sequence is `compile_filter`ed
//! only after all three pass. `{% for a|b in %}` is therefore the *arity* error,
//! not the filter error.
//!
//! # `in_index` is counted from the end, and `reversed` is a single bit
//!
//! `is_reversed = bits[-1] == "reversed"` (`:869`) and `in_index = -3 if
//! is_reversed else -2` (`:870`). Reading from the *end* is what lets the
//! sequence be a lone bit and the loop variables be everything between `for` and
//! `in`; there is no scan for the first `in`. `{% for x in y %}` and
//! `{% for x y in z %}` differ only in the loop-variable slice, and the second
//! is rejected by check 3, not by check 2.
//!
//! # The loop variables come from `re.split`, through a join
//!
//! `re.split(r" *, *", " ".join(bits[1:in_index]))` (`:877`) — the bits are
//! rejoined with **single** spaces and then split on a comma with any number of
//! spaces around it. That is why `{% for a , b in y %}`, `{% for a, b in y %}`
//! and `{% for a,b in y %}` all yield `["a", "b"]` (measured), and why
//! `{% for a b in y %}` (no comma at all) yields the *single* loop variable
//! `"a b"`, which check 3 then rejects for its space.
//!
//! # The invalid characters are `space`, both quotes, and `|`
//!
//! `frozenset((" ", '"', "'", FILTER_SEPARATOR))` (`:875`), tested with
//! `not invalid_chars.isdisjoint(var)` — so it fires on any *single* character,
//! and on an empty variable. `FILTER_SEPARATOR` is `"|"` (`base.py`), so
//! `{% for a|b in y %}` is invalid rather than a filter to compile.
//!
//! # `nodelist_empty` is absent as `None`, and observable as an empty `NodeList`
//!
//! `ForNode(..., nodelist_empty=None)` (`:930`) and `ForNode.__init__` replaces
//! `None` with a fresh `NodeList()` (`:177-180`), so the attribute is an empty
//! nodelist either way. Emitting the empty nodelist keeps the descriptor's shape
//! fixed across the two paths and produces the same object; the parity harness
//! compares `vars(node)` and cannot tell them apart.

use std::sync::OnceLock;

use regex::Regex;

use crate::parser::desc::{AttrValue, NodeDesc, NodeListDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

/// Django's `re.split(r" *, *", …)` (`defaulttags.py:877`).
///
/// The pattern cannot match the empty string — it requires a comma — so Python's
/// `re.split` and `regex`'s [`Regex::split`] agree on every input, including the
/// adjacent-comma case: `"a,,b"` is `["a", "", "b"]` in both.
fn loopvar_split() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r" *, *").expect("the loop-variable separator is a valid pattern"))
}

/// `re.split(r" *, *", " ".join(bits[1:in_index]))` — Django's loop-variable list.
fn split_loopvars(bits: &[String]) -> Vec<String> {
    loopvar_split()
        .split(&bits.join(" "))
        .map(str::to_string)
        .collect()
}

/// `not var or not invalid_chars.isdisjoint(var)` (`:878-879`).
///
/// `isdisjoint` is *false* when the two share any character, so the condition is
/// "empty, **or** holds at least one of the four forbidden characters".
fn is_invalid_loopvar(var: &str) -> bool {
    // `(" ", '"', "'", FILTER_SEPARATOR)`.
    var.is_empty() || var.chars().any(|c| matches!(c, ' ' | '"' | '\'' | '|'))
}

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;

    if bits.len() < 4 {
        // `%s` of `token.contents`: the tag source, not a repr.
        return Err(Error::syntax(format!(
            "'for' statements should have at least four words: {}",
            token.contents,
        )));
    }

    let is_reversed = bits[bits.len() - 1] == "reversed";
    // `-3 if is_reversed else -2`, counted from the end.
    let in_index = if is_reversed {
        bits.len() - 3
    } else {
        bits.len() - 2
    };
    if bits[in_index] != "in" {
        return Err(Error::syntax(format!(
            "'for' statements should use the format 'for x in y': {}",
            token.contents,
        )));
    }

    let loopvars = split_loopvars(&bits[1..in_index]);
    for var in &loopvars {
        if is_invalid_loopvar(var) {
            return Err(Error::syntax(format!(
                "'for' tag received an invalid argument: {}",
                token.contents,
            )));
        }
    }

    let sequence = parser.compile_filter(&bits[in_index + 1])?;

    let nodelist_loop = parser.parse(&["empty", "endfor"])?;
    let terminator = parser
        .next_token()
        .expect("parse returns only after prepending its terminator");
    let nodelist_empty = if terminator.contents == "empty" {
        let nodelist = parser.parse(&["endfor"])?;
        parser.delete_first_token();
        nodelist
    } else {
        // Django's `None`, which `ForNode.__init__` turns into `NodeList()`.
        NodeListDesc::default()
    };

    Ok(NodeDesc::new("ForNode")
        .attr(
            "loopvars",
            AttrValue::List(loopvars.into_iter().map(AttrValue::Str).collect()),
        )
        .attr("sequence", sequence)
        .attr("is_reversed", AttrValue::Bool(is_reversed))
        .nodelist("nodelist_loop", nodelist_loop)
        .nodelist("nodelist_empty", nodelist_empty))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile, failure, texts};

    fn loopvars(node: &NodeDesc) -> Vec<String> {
        let AttrValue::List(values) = attr(node, "loopvars") else {
            panic!("loopvars is a List");
        };
        values
            .iter()
            .map(|value| {
                let AttrValue::Str(name) = value else {
                    panic!("each loop variable is a Str");
                };
                name.clone()
            })
            .collect()
    }

    fn nodelist(node: &NodeDesc, name: &str) -> Vec<String> {
        let (_, nodelist) = node
            .nodelists
            .iter()
            .find(|(key, _)| key == name)
            .unwrap_or_else(|| panic!("{name} is a named nodelist"));
        texts(nodelist)
    }

    fn sequence(node: &NodeDesc) -> String {
        let AttrValue::FilterExpr(expression) = attr(node, "sequence") else {
            panic!("sequence is a FilterExpr");
        };
        expression.token.clone()
    }

    #[test]
    fn for_reads_the_loop_variable_sequence_and_direction_from_the_end() {
        let node = compile("{% for x in y %}{% endfor %}");
        assert_eq!(node.class_name, "ForNode");
        assert_eq!(loopvars(&node), vec!["x"]);
        assert_eq!(sequence(&node), "y");
        assert_eq!(attr(&node, "is_reversed"), &AttrValue::Bool(false));
        assert_eq!(nodelist(&node, "nodelist_loop"), Vec::<String>::new());
        assert_eq!(nodelist(&node, "nodelist_empty"), Vec::<String>::new());

        let reversed = compile("{% for x in y reversed %}{% endfor %}");
        assert_eq!(loopvars(&reversed), vec!["x"]);
        assert_eq!(sequence(&reversed), "y");
        assert_eq!(attr(&reversed, "is_reversed"), &AttrValue::Bool(true));
    }

    #[test]
    fn every_comma_spelling_produces_the_same_loop_variables() {
        // The join-then-split (`:877`): measured on the pinned tree, all three
        // spellings give `["a", "b"]`.
        for source in [
            "{% for a, b in y %}{% endfor %}",
            "{% for a,b in y %}{% endfor %}",
            "{% for a , b in y %}{% endfor %}",
        ] {
            assert_eq!(loopvars(&compile(source)), vec!["a", "b"], "{source}");
        }
    }

    #[test]
    fn the_empty_branch_is_a_second_nodelist() {
        let node = compile("{% for x in y %}loop{% empty %}none{% endfor %}");
        assert_eq!(nodelist(&node, "nodelist_loop"), vec!["loop"]);
        assert_eq!(nodelist(&node, "nodelist_empty"), vec!["none"]);
    }

    #[test]
    fn a_missing_in_keyword_is_reported_before_the_loop_variables() {
        // Check 2 fires even though `a b` is also an invalid loop variable:
        // `bits[-2]` is `b`, not `in`. Measured wording.
        assert_eq!(
            failure("{% for a b c %}{% endfor %}").kind,
            ErrorKind::Syntax(
                "'for' statements should use the format 'for x in y': for a b c".to_string(),
            ),
        );
    }

    #[test]
    fn the_arity_check_comes_first() {
        assert_eq!(
            failure("{% for x in %}").kind,
            ErrorKind::Syntax(
                "'for' statements should have at least four words: for x in".to_string(),
            ),
        );
    }

    #[test]
    fn a_space_or_a_pipe_in_a_loop_variable_is_rejected() {
        // `{% for a b in y %}` has no comma, so the split yields the one
        // variable `"a b"` — which the space makes invalid. Measured.
        assert_eq!(
            failure("{% for a b in y %}{% endfor %}").kind,
            ErrorKind::Syntax("'for' tag received an invalid argument: for a b in y".to_string(),),
        );
        // `FILTER_SEPARATOR` is one of the four invalid characters, so this is
        // an argument error and not a filter to compile.
        assert_eq!(
            failure("{% for a|b in y %}{% endfor %}").kind,
            ErrorKind::Syntax("'for' tag received an invalid argument: for a|b in y".to_string(),),
        );
    }

    #[test]
    fn the_sequence_is_compiled_only_after_every_check() {
        // `{% for a|b in %}` has three words, so the arity error wins even
        // though the loop variable and the sequence are both bad.
        assert_eq!(
            failure("{% for a|b in %}").kind,
            ErrorKind::Syntax(
                "'for' statements should have at least four words: for a|b in".to_string(),
            ),
        );
        // With four words the sequence reaches `compile_filter`.
        let node = compile("{% for x in y|default:'z' %}{% endfor %}");
        let AttrValue::FilterExpr(expression) = attr(&node, "sequence") else {
            panic!("sequence is a FilterExpr");
        };
        assert_eq!(expression.token, "y|default:'z'");
    }

    #[test]
    fn split_loopvars_matches_pythons_re_split() {
        // Measured on the pinned interpreter; `"a,,b"` is the case that would
        // differ if the pattern were `[, ]+`.
        let bits = |parts: &[&str]| parts.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(split_loopvars(&bits(&["a,", "b"])), vec!["a", "b"]);
        assert_eq!(split_loopvars(&bits(&["a,b"])), vec!["a", "b"]);
        assert_eq!(split_loopvars(&bits(&["a", ",", "b"])), vec!["a", "b"]);
        assert_eq!(split_loopvars(&bits(&["a b"])), vec!["a b"]);
        assert_eq!(split_loopvars(&bits(&["a,,b"])), vec!["a", "", "b"]);
        assert_eq!(split_loopvars(&bits(&[",a"])), vec!["", "a"]);
        assert_eq!(split_loopvars(&bits(&["a,"])), vec!["a", ""]);
    }
}
