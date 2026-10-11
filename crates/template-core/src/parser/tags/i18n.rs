//! `i18n`'s ten tags — `django/templatetags/i18n.py` @ 6.0.9.
//!
//! `blocktranslate`/`blocktrans` are **one body** ([`block_translate`],
//! `i18n.py:456-596`) under two names, as `translate`/`trans` are, and the one node
//! with **no nodelist**: its body is a slice of the token stream, carried as
//! [`AttrValue::Tokens`]. Only the **compile** functions are Rust's; every `render`
//! reaches `django.utils.translation` through the transcribed Python node the
//! materialiser builds from the descriptor.
//!
//! Five tags read their arguments with `token.contents.split()`, not
//! `split_contents()`, because they accept no *variable* arguments
//! (`i18n.py:232-234`): `get_available_languages`, `get_current_language` and
//! `get_current_language_bidi` use [`pyunicode::split`], and their `as <name>`
//! target is a **bare string**. The other five — the `get_language_info` pair,
//! `translate`/`trans`, `language` — use [`split_contents`] and
//! [`Parser::compile_filter`]. Getting the pair backwards compiles but passes
//! nothing: the bare-string form hands `GetAvailableLanguagesNode` a `Variable`
//! where its `render` does `context[self.variable] = …`.
//!
//! Five tags interpolate a Python **list** with `%r` in their refusal message —
//! three the whole `args` (`i18n.py:237,330,353`), two the `args[1:]` slice
//! (`i18n.py:260,287`). The elements are **user text**, so each goes through
//! [`Parser::py_repr`] (the host's `repr`), not [`py_repr_str`], whose ASCII-only
//! assertion exists for exactly this reason:
//! [`tags::python_list_repr`](crate::parser::tags::python_list_repr).
//!
//! `{% translate %}`'s `while remaining:` loop (`i18n.py:412-451`) is ordered, and
//! every branch is observable. The **duplicate check runs first**
//! (`i18n.py:414-417`), before dispatch, so a repeat is refused even when its first
//! use was valid. `context`'s **value** is refused when it is `as` or `noop`
//! (`i18n.py:410,428-432`); `as`'s value is **not** validated at all
//! (`i18n.py:434-441`). A trailing `context`/`as` with nothing after it raises its
//! **own** message (`i18n.py:421-427,435-440`) — what `remaining.pop(0)`'s
//! `IndexError` catches, not the generic unknown-argument branch. All six messages
//! name `bits[0]`, so the two spellings differ in their text; the tests below write
//! each message out.
//!
//! For a **literal** operand `TranslateNode.__init__` sets `is_var = True` and
//! rebuilds `var` as `Variable("'%s'" % var)` (`i18n.py:80-82`) — the transcribed
//! class's job, not Rust's. Rust hands over `filter_expression`, `noop`, `asvar` and
//! `message_context`; reimplementing the rewrite here would put a `Variable` on the
//! wire where the class expects a `FilterExpression`.
//!
//! [`Parser::compile_filter`]: crate::parser::Parser::compile_filter
//! [`Parser::py_repr`]: crate::parser::Parser::py_repr

use crate::parser::desc::{AttrValue, NodeDesc, TokenDesc};
use crate::parser::error::Error;
use crate::parser::kwargs::token_kwargs;
use crate::parser::tags::{python_list_repr, split_contents};
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::{Token, TokenType};

/// `do_get_available_languages` (`i18n.py:219-239`).
///
/// `token.contents.split()`, bare-string `as` target; the refusal interpolates
/// the whole `args` list with `%r`.
pub fn get_available_languages(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 3 || args[1] != "as" {
        return Err(Error::syntax(format!(
            "'get_available_languages' requires 'as variable' (got {})",
            python_list_repr(parser, &args),
        )));
    }
    Ok(NodeDesc::new("GetAvailableLanguagesNode")
        .attr("variable", AttrValue::Str(args[2].to_string())))
}

/// `do_get_language_info` (`i18n.py:243-262`).
///
/// `split_contents()` and a compiled operand. The refusal interpolates `args[0]`
/// with `%s` and the `args[1:]` **slice** with `%r`.
pub fn get_language_info(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = split_contents(token)?;
    if args.len() != 5 || args[1] != "for" || args[3] != "as" {
        return Err(Error::syntax(format!(
            "'{}' requires 'for string as variable' (got {})",
            args[0],
            python_list_repr(parser, &args[1..]),
        )));
    }
    Ok(NodeDesc::new("GetLanguageInfoNode")
        .attr("lang_code", parser.compile_filter(&args[2])?)
        .attr("variable", AttrValue::Str(args[4].to_string())))
}

/// `do_get_language_info_list` (`i18n.py:266-289`).
///
/// Differs from [`get_language_info`] in two places: the word `sequence` and the node class.
pub fn get_language_info_list(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = split_contents(token)?;
    if args.len() != 5 || args[1] != "for" || args[3] != "as" {
        return Err(Error::syntax(format!(
            "'{}' requires 'for sequence as variable' (got {})",
            args[0],
            python_list_repr(parser, &args[1..]),
        )));
    }
    Ok(NodeDesc::new("GetLanguageInfoListNode")
        .attr("languages", parser.compile_filter(&args[2])?)
        .attr("variable", AttrValue::Str(args[4].to_string())))
}

/// `do_get_current_language` (`i18n.py:314-332`).
pub fn get_current_language(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 3 || args[1] != "as" {
        return Err(Error::syntax(format!(
            "'get_current_language' requires 'as variable' (got {})",
            python_list_repr(parser, &args),
        )));
    }
    Ok(NodeDesc::new("GetCurrentLanguageNode")
        .attr("variable", AttrValue::Str(args[2].to_string())))
}

/// `do_get_current_language_bidi` (`i18n.py:336-355`).
pub fn get_current_language_bidi(
    parser: &mut Parser<'_>,
    token: &Token,
) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 3 || args[1] != "as" {
        return Err(Error::syntax(format!(
            "'get_current_language_bidi' requires 'as variable' (got {})",
            python_list_repr(parser, &args),
        )));
    }
    Ok(NodeDesc::new("GetCurrentLanguageBidiNode")
        .attr("variable", AttrValue::Str(args[2].to_string())))
}

/// `do_translate` (`i18n.py:360-453`) — `{% translate … %}` and `{% trans … %}`.
///
/// One body registered under two names (`i18n.py:358-359`). The scan's ordered branches
/// are in the module doc; the six refusal messages all name `bits[0]`, so the two spellings differ.
pub fn translate(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    if bits.len() < 2 {
        return Err(Error::syntax(format!(
            "'{}' takes at least one argument",
            bits[0],
        )));
    }
    let message_string = parser.compile_filter(&bits[1])?;
    let mut remaining: Vec<String> = bits[2..].to_vec();

    let mut noop = false;
    let mut asvar: Option<String> = None;
    let mut message_context: Option<AttrValue> = None;
    // A `Vec`, not a `HashSet`: the scan is tiny, and `contains` mirrors the source's `option in seen`.
    let mut seen: Vec<String> = Vec::new();
    let invalid_context = ["as", "noop"];

    while !remaining.is_empty() {
        let option = remaining.remove(0);
        if seen.contains(&option) {
            // The duplicate check runs **before** dispatch — see the module doc.
            return Err(Error::syntax(format!(
                "The '{}' option was specified more than once.",
                option,
            )));
        } else if option == "noop" {
            noop = true;
        } else if option == "context" {
            if remaining.is_empty() {
                return Err(Error::syntax(format!(
                    "No argument provided to the '{}' tag for the context option.",
                    bits[0],
                )));
            }
            let value = remaining.remove(0);
            if invalid_context.contains(&value.as_str()) {
                return Err(Error::syntax(format!(
                    "Invalid argument '{}' provided to the '{}' tag for the context option",
                    value, bits[0],
                )));
            }
            message_context = Some(parser.compile_filter(&value)?);
        } else if option == "as" {
            if remaining.is_empty() {
                return Err(Error::syntax(format!(
                    "No argument provided to the '{}' tag for the as option.",
                    bits[0],
                )));
            }
            // No validation of the name: any word is stored — see the module doc.
            asvar = Some(remaining.remove(0));
        } else {
            return Err(Error::syntax(format!(
                "Unknown argument for '{}' tag: '{}'. The only options available are \
                 'noop', 'context \"xxx\", and 'as VAR'.",
                bits[0], option,
            )));
        }
        seen.push(option);
    }

    Ok(NodeDesc::new("TranslateNode")
        .attr("filter_expression", message_string)
        .attr("noop", AttrValue::Bool(noop))
        .attr(
            "asvar",
            asvar.map(AttrValue::Str).unwrap_or(AttrValue::None),
        )
        .attr(
            "message_context",
            message_context.unwrap_or(AttrValue::None),
        ))
}

/// `language` (`i18n.py:600-616`) — `{% language <expr> %}…{% endlanguage %}`.
///
/// The argument is compiled **before** the body is parsed (`i18n.py:613-614`), as in
/// `{% timezone %}`: `{% language %}x` reports the argument error, not `Unclosed tag`.
pub fn language(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    if bits.len() != 2 {
        return Err(Error::syntax(format!(
            "'{}' takes one argument (language)",
            bits[0],
        )));
    }
    let language = parser.compile_filter(&bits[1])?;
    let nodelist = parser.parse(&["endlanguage"])?;
    parser.delete_first_token();
    Ok(NodeDesc::with_nodelist("LanguageNode", "nodelist", nodelist).attr("language", language))
}

/// `do_block_translate` (`i18n.py:458-596`) — `{% blocktranslate %}` and
/// `{% blocktrans %}`.
///
/// One body registered under two names (`i18n.py:456-457`) — the one node with no
/// nodelist: `singular`/`plural` are **lists of `Token` objects**
/// ([`AttrValue::Tokens`]), which `BlockTranslateNode.render_token_list` walks by
/// `token.token_type` and `token.contents`.
///
/// The loop at `i18n.py:562-567` reassigns Django's own `token` parameter, so the
/// name after it is the last token *read*, not the tag's: with the stream exhausted
/// and nothing read, `{% blocktranslate %}` alone reports `(seen 'blocktranslate')`
/// while `{% blocktranslate %}x` reports `(seen 'x')`. Rust has no parameter
/// aliasing, so `scan_token` below carries the rule.
///
/// The four message shapes come from *which scan stopped*, not from `count` (which
/// only decides whether the plural check happens, `i18n.py:568`). The singular
/// scan's stopping token is compared against `plural` and raises **without** naming
/// it (`i18n.py:569-572`); the final token is compared against `end_tag_name` and
/// raises **naming** whatever `scan_token` last held (`i18n.py:580-584`) — inside the
/// plural scan a TEXT or VAR token, so the parenthetical can be a text node's
/// *contents* (`... (seen 'many')`), not a tag name. Formatting it as a command, or
/// stripping a `{% %}` wrapper, would diverge on a template Django accepts.
///
/// [`AttrValue::Tokens`]: crate::parser::desc::AttrValue::Tokens
pub fn block_translate(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    // A dispatched block tag always has a first bit — `Parser::parse` rejects an empty
    // block tag before it looks the command up.
    let tag_name = bits[0].clone();
    let mut remaining_bits: Vec<String> = bits[1..].to_vec();

    // Ordered pairs, not a map: mirrors the source's `option in options`, keeps insertion
    // order, and stores each branch's own `AttrValue` (`Map`, `FilterExpr`, `Bool`, `Str`).
    let mut options: Vec<(String, AttrValue)> = Vec::new();
    let mut asvar: Option<String> = None;

    while !remaining_bits.is_empty() {
        let option = remaining_bits.remove(0);
        // Duplicate check first (`i18n.py:506-509`): a repeat is refused even when its
        // first use was valid. `option` is always one of the five ASCII names, so `%r` is plain.
        if options.iter().any(|(name, _)| name == &option) {
            return Err(Error::syntax(format!(
                "The {} option was specified more than once.",
                parser.py_repr(&option),
            )));
        }
        let value = if option == "with" {
            let kwargs = {
                let mut compile_filter = |source: &str| parser.compile_filter(source);
                token_kwargs(&mut remaining_bits, &mut compile_filter, true)?
            };
            if kwargs.is_empty() {
                return Err(Error::syntax(format!(
                    "\"with\" in {} tag needs at least one keyword argument.",
                    parser.py_repr(&tag_name),
                )));
            }
            AttrValue::Map(kwargs)
        } else if option == "count" {
            let kwargs = {
                let mut compile_filter = |source: &str| parser.compile_filter(source);
                token_kwargs(&mut remaining_bits, &mut compile_filter, true)?
            };
            if kwargs.len() != 1 {
                return Err(Error::syntax(format!(
                    "\"count\" in {} tag expected exactly one keyword argument.",
                    parser.py_repr(&tag_name),
                )));
            }
            AttrValue::Map(kwargs)
        } else if option == "context" {
            // `except Exception`, not `except IndexError` (`i18n.py:524-530`): a
            // malformed filter and a missing argument share one message.
            let Some(source) = remaining_bits.first().cloned() else {
                return Err(Error::syntax(format!(
                    "\"context\" in {} tag expected exactly one argument.",
                    parser.py_repr(&tag_name),
                )));
            };
            remaining_bits.remove(0);
            match parser.compile_filter(&source) {
                Ok(value) => value,
                Err(_) => {
                    return Err(Error::syntax(format!(
                        "\"context\" in {} tag expected exactly one argument.",
                        parser.py_repr(&tag_name),
                    )))
                }
            }
        } else if option == "trimmed" {
            // The literal `True` (`i18n.py:531-532`), not a value read from `bits`.
            AttrValue::Bool(true)
        } else if option == "asvar" {
            let Some(value) = remaining_bits.first().cloned() else {
                // `%s` of `tag_name` with the quotes in the format string: the name
                // goes in bare, not through `%r`.
                return Err(Error::syntax(format!(
                    "No argument provided to the '{}' tag for the asvar option.",
                    tag_name,
                )));
            };
            remaining_bits.remove(0);
            asvar = Some(value.clone());
            AttrValue::Str(value)
        } else {
            return Err(Error::syntax(format!(
                "Unknown argument for {} tag: {}.",
                parser.py_repr(&tag_name),
                parser.py_repr(&option),
            )));
        };
        options.push((option, value));
    }

    let (countervar, counter) = match options.iter().find(|(name, _)| name == "count") {
        Some((_, AttrValue::Map(entries))) => {
            // `next(iter(options["count"].items()))` — exactly one item, proven above.
            let (key, value) = entries
                .first()
                .cloned()
                .expect("count carries exactly one keyword argument");
            (Some(key), Some(value))
        }
        _ => (None, None),
    };
    let message_context = options
        .iter()
        .find(|(name, _)| name == "context")
        .map(|(_, value)| value.clone())
        .unwrap_or(AttrValue::None);
    let extra_context = options
        .iter()
        .find(|(name, _)| name == "with")
        .map(|(_, value)| value.clone())
        .unwrap_or(AttrValue::Map(Vec::new()));
    let trimmed = options.iter().any(|(name, _)| name == "trimmed");

    // Django's `if countervar and counter:` (`i18n.py:568`): `counter` is always
    // truthy, so this decides only that `count` is present with a non-empty name.
    let has_counter =
        countervar.as_deref().is_some_and(|name| !name.is_empty()) && counter.is_some();

    let mut singular: Vec<TokenDesc> = Vec::new();
    let mut plural: Vec<TokenDesc> = Vec::new();

    // Read until the first token that is not TEXT or VAR; `scan_token` starts as the tag's token.
    let mut scan_token = token.clone();
    while let Some(next) = parser.next_token() {
        scan_token = next;
        if matches!(scan_token.token_type, TokenType::Var | TokenType::Text) {
            singular.push(TokenDesc::of(&scan_token));
        } else {
            break;
        }
    }
    if has_counter {
        // `{% plural %}` is **not** a registered tag — compared as a bare string
        // (`i18n.py:569`), so a misplaced one gives this message, not an unknown-tag error.
        if pyunicode::strip(&scan_token.contents) != "plural" {
            return Err(Error::syntax(format!(
                "{} doesn't allow other block tags inside it",
                parser.py_repr(&tag_name),
            )));
        }
        while let Some(next) = parser.next_token() {
            scan_token = next;
            if matches!(scan_token.token_type, TokenType::Var | TokenType::Text) {
                plural.push(TokenDesc::of(&scan_token));
            } else {
                break;
            }
        }
    }

    let end_tag_name = format!("end{}", tag_name);
    if pyunicode::strip(&scan_token.contents) != end_tag_name {
        return Err(Error::syntax(format!(
            "{} doesn't allow other block tags (seen {}) inside it",
            parser.py_repr(&tag_name),
            parser.py_repr(&scan_token.contents),
        )));
    }

    Ok(NodeDesc::new("BlockTranslateNode")
        .attr("extra_context", extra_context)
        .attr("singular", AttrValue::Tokens(singular))
        .attr("plural", AttrValue::Tokens(plural))
        .attr(
            "countervar",
            countervar.map(AttrValue::Str).unwrap_or(AttrValue::None),
        )
        .attr("counter", counter.unwrap_or(AttrValue::None))
        .attr("message_context", message_context)
        .attr("trimmed", AttrValue::Bool(trimmed))
        .attr(
            "asvar",
            asvar.map(AttrValue::Str).unwrap_or(AttrValue::None),
        )
        .attr("tag_name", AttrValue::Str(tag_name)))
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
    fn get_available_languages_stores_the_name_as_a_bare_string() {
        let node = compile_loaded("{% get_available_languages as LANGS %}");
        assert_eq!(node.class_name, "GetAvailableLanguagesNode");
        assert!(
            node.nodelists.is_empty(),
            "the tag reads no body: `GetAvailableLanguagesNode` takes only `variable`",
        );
        assert_eq!(
            attr(&node, "variable"),
            &AttrValue::Str("LANGS".to_string()),
            "a plain Str, not a FilterExpression — it is the variable's *name*",
        );
    }

    #[test]
    fn get_available_languages_requires_the_literal_word_as() {
        // `%r` of the whole list, written out rather than derived from `python_list_repr`:
        // a message built by calling the helper agrees with itself by construction.
        for (args, expected) in [
            (
                "get_available_languages",
                "'get_available_languages' requires 'as variable' \
                 (got ['get_available_languages'])",
            ),
            (
                "get_available_languages nope",
                "'get_available_languages' requires 'as variable' \
                 (got ['get_available_languages', 'nope'])",
            ),
            (
                "get_available_languages of v",
                "'get_available_languages' requires 'as variable' \
                 (got ['get_available_languages', 'of', 'v'])",
            ),
        ] {
            assert_eq!(
                failure_loaded(&format!("{{% {args} %}}")).kind,
                ErrorKind::Syntax(expected.to_string()),
                "{args}",
            );
        }
    }

    #[test]
    fn get_language_info_compiles_its_operand_and_stores_the_name() {
        let node = compile_loaded("{% get_language_info for code as li %}");
        assert_eq!(node.class_name, "GetLanguageInfoNode");
        assert_eq!(
            filter_token(attr(&node, "lang_code")),
            "code",
            "the operand is a FilterExpression; `render` resolves it",
        );
        assert_eq!(attr(&node, "variable"), &AttrValue::Str("li".to_string()));
    }

    #[test]
    fn get_language_info_message_names_the_tag_and_the_slice() {
        // `%s` of the tag, `%r` of the `args[1:]` *slice* for the rest.
        // `split_contents`, so quotes survive: `for "x" as` is four bits.
        for (args, expected) in [
            (
                "get_language_info for x",
                "'get_language_info' requires 'for string as variable' \
                 (got ['for', 'x'])",
            ),
            (
                "get_language_info x as y",
                "'get_language_info' requires 'for string as variable' \
                 (got ['x', 'as', 'y'])",
            ),
            (
                "get_language_info for x of y",
                "'get_language_info' requires 'for string as variable' \
                 (got ['for', 'x', 'of', 'y'])",
            ),
        ] {
            assert_eq!(
                failure_loaded(&format!("{{% {args} %}}")).kind,
                ErrorKind::Syntax(expected.to_string()),
                "{args}",
            );
        }
    }

    #[test]
    fn get_language_info_list_says_sequence_not_string() {
        let node = compile_loaded("{% get_language_info_list for LANGS as langs %}");
        assert_eq!(node.class_name, "GetLanguageInfoListNode");
        assert_eq!(
            filter_token(attr(&node, "languages")),
            "LANGS",
            "the operand is a FilterExpression; `render` resolves it",
        );
        assert_eq!(
            attr(&node, "variable"),
            &AttrValue::Str("langs".to_string())
        );
        assert_eq!(
            failure_loaded("{% get_language_info_list for x %}").kind,
            ErrorKind::Syntax(
                "'get_language_info_list' requires 'for sequence as variable' (got ['for', 'x'])"
                    .to_string(),
            ),
        );
    }

    #[test]
    fn the_two_current_language_tags_store_bare_names() {
        for (source, class_name, variable) in [
            (
                "{% get_current_language as LANGUAGE_CODE %}",
                "GetCurrentLanguageNode",
                "LANGUAGE_CODE",
            ),
            (
                "{% get_current_language_bidi as LANGUAGE_BIDI %}",
                "GetCurrentLanguageBidiNode",
                "LANGUAGE_BIDI",
            ),
        ] {
            let node = compile_loaded(source);
            assert_eq!(node.class_name, class_name, "{source}");
            assert_eq!(
                attr(&node, "variable"),
                &AttrValue::Str(variable.to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn the_current_language_tags_refuse_a_missing_as() {
        for (args, expected) in [
            (
                "get_current_language",
                "'get_current_language' requires 'as variable' \
                 (got ['get_current_language'])",
            ),
            (
                "get_current_language_bidi",
                "'get_current_language_bidi' requires 'as variable' \
                 (got ['get_current_language_bidi'])",
            ),
        ] {
            assert_eq!(
                failure_loaded(&format!("{{% {args} %}}")).kind,
                ErrorKind::Syntax(expected.to_string()),
                "{args}",
            );
        }
    }

    #[test]
    fn translate_needs_at_least_one_argument() {
        assert_eq!(
            failure_loaded("{% translate %}").kind,
            ErrorKind::Syntax("'translate' takes at least one argument".to_string()),
        );
        assert_eq!(
            failure_loaded("{% trans %}").kind,
            ErrorKind::Syntax("'trans' takes at least one argument".to_string()),
        );
    }

    #[test]
    fn translate_stores_its_operand_and_the_two_optional_parts() {
        let node = compile_loaded(r#"{% translate "hello" noop context "greeting" as msg %}"#);
        assert_eq!(node.class_name, "TranslateNode");
        assert!(
            node.nodelists.is_empty(),
            "`TranslateNode.child_nodelists = ()` (`i18n.py:73`) — it reads no body",
        );
        assert_eq!(
            filter_token(attr(&node, "filter_expression")),
            r#""hello""#,
            "the operand, quotes and all — the literal rewrite is `__init__`'s",
        );
        assert_eq!(attr(&node, "noop"), &AttrValue::Bool(true));
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("msg".to_string()));
        assert_eq!(
            filter_token(attr(&node, "message_context")),
            r#""greeting""#,
            "the context argument is compiled, not a bare string",
        );
    }

    #[test]
    fn translate_leaves_noop_false_and_the_two_optionals_none() {
        let node = compile_loaded(r#"{% translate "hello" %}"#);
        assert_eq!(attr(&node, "noop"), &AttrValue::Bool(false));
        assert_eq!(attr(&node, "asvar"), &AttrValue::None);
        assert_eq!(attr(&node, "message_context"), &AttrValue::None);
    }

    #[test]
    fn translate_refuses_a_repeated_option_before_dispatch() {
        // `option in seen` is checked *first* (`i18n.py:414-417`): a repeat is refused even
        // though the first use was valid, and the message names the option, not the tag.
        assert_eq!(
            failure_loaded(r#"{% translate "x" noop noop %}"#).kind,
            ErrorKind::Syntax("The 'noop' option was specified more than once.".to_string()),
        );
        assert_eq!(
            failure_loaded(r#"{% translate "x" context "a" context "b" %}"#).kind,
            ErrorKind::Syntax("The 'context' option was specified more than once.".to_string()),
        );
    }

    #[test]
    fn translate_refuses_context_and_as_as_a_context_value() {
        // `invalid_context == {"as", "noop"}` (`i18n.py:410`); the message names
        // both value and tag.
        for value in ["as", "noop"] {
            let source = format!(r#"{{% translate "x" context {value} %}}"#);
            assert_eq!(
                failure_loaded(&source).kind,
                ErrorKind::Syntax(format!(
                    "Invalid argument '{value}' provided to the 'translate' tag for the context option"
                )),
                "{source}",
            );
        }
    }

    #[test]
    fn translate_gives_a_trailing_option_its_own_message() {
        // `remaining.pop(0)`'s `IndexError` is caught and re-raised with a dedicated
        // message, not the unknown-argument branch (`i18n.py:421-427,435-440`).
        assert_eq!(
            failure_loaded(r#"{% translate "x" context %}"#).kind,
            ErrorKind::Syntax(
                "No argument provided to the 'translate' tag for the context option.".to_string(),
            ),
        );
        assert_eq!(
            failure_loaded(r#"{% translate "x" as %}"#).kind,
            ErrorKind::Syntax(
                "No argument provided to the 'translate' tag for the as option.".to_string(),
            ),
        );
    }

    #[test]
    fn translate_does_not_validate_the_as_name() {
        // `asvar = value` with no check at all (`i18n.py:441`): a non-variable name
        // is still stored, unlike `context`'s value.
        let node = compile_loaded(r#"{% translate "x" as not-a-variable %}"#);
        assert_eq!(
            attr(&node, "asvar"),
            &AttrValue::Str("not-a-variable".to_string()),
        );
    }

    #[test]
    fn translate_reports_an_unknown_option_verbatim() {
        assert_eq!(
            failure_loaded(r#"{% translate "x" bogus %}"#).kind,
            ErrorKind::Syntax(
                "Unknown argument for 'translate' tag: 'bogus'. The only options available are \
                 'noop', 'context \"xxx\", and 'as VAR'."
                    .to_string(),
            ),
        );
        // The *other* spelling of the same tag names itself.
        assert_eq!(
            failure_loaded(r#"{% trans "x" bogus %}"#).kind,
            ErrorKind::Syntax(
                "Unknown argument for 'trans' tag: 'bogus'. The only options available are \
                 'noop', 'context \"xxx\", and 'as VAR'."
                    .to_string(),
            ),
        );
    }

    #[test]
    fn language_compiles_its_argument_and_reads_a_body() {
        let node = compile_loaded(r#"{% language "de" %}x{% endlanguage %}"#);
        assert_eq!(node.class_name, "LanguageNode");
        assert_eq!(node.nodelists.len(), 1);
        assert_eq!(node.nodelists[0].0, "nodelist", "`LanguageNode.__init__`");
        assert_eq!(
            filter_token(attr(&node, "language")),
            r#""de""#,
            "the argument is a FilterExpression, resolved at render",
        );
    }

    #[test]
    fn language_takes_exactly_one_argument() {
        for source in [
            "{% language %}x{% endlanguage %}",
            "{% language a b %}x{% endlanguage %}",
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax("'language' takes one argument (language)".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn language_checks_its_argument_before_it_parses_the_body() {
        // Compiled before `parser.parse` (`i18n.py:613-614`), as in `{% timezone %}`:
        // the missing argument is reported, not the unclosed block.
        assert_eq!(
            failure_loaded("{% language %}x").kind,
            ErrorKind::Syntax("'language' takes one argument (language)".to_string()),
        );
        assert_eq!(
            failure_loaded(r#"{% language "de" %}x"#).kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 1: 'language'. Looking for one of: endlanguage.".to_string(),
            ),
        );
    }

    /// `(token_type, contents)` of a `Tokens` attribute, so a row states the *scan*,
    /// not the whole `TokenDesc`.
    fn tokens_of(node: &NodeDesc, name: &str) -> Vec<(TokenType, String)> {
        let AttrValue::Tokens(list) = attr(node, name) else {
            panic!("{name} is not a Tokens attribute: {:?}", attr(node, name));
        };
        list.iter()
            .map(|token| (token.token_type, token.contents.clone()))
            .collect()
    }

    #[test]
    fn block_translate_carries_its_body_as_token_lists() {
        // `singular` holds the `Token` objects themselves: `render_token_list` reads
        // `token.token_type` and `token.contents` off them.
        let node = compile_loaded("{% blocktranslate %}x{{ y }}{% endblocktranslate %}");
        assert_eq!(node.class_name, "BlockTranslateNode");
        assert!(
            node.nodelists.is_empty(),
            "`BlockTranslateNode` has no child nodelist: its body is tokens",
        );
        assert_eq!(
            tokens_of(&node, "singular"),
            [
                (TokenType::Text, "x".to_string()),
                (TokenType::Var, "y".to_string()),
            ],
            "both TEXT and VAR tokens are the body, contents kept verbatim",
        );
        assert_eq!(tokens_of(&node, "plural"), [], "no `count`, so no plural");
        assert_eq!(
            attr(&node, "tag_name"),
            &AttrValue::Str("blocktranslate".to_string())
        );
    }

    #[test]
    fn block_translate_keeps_the_token_fields_not_just_the_contents() {
        // `TokenDesc`, not a bare string: `lineno`/`position` are part of the real
        // `Token` the shell rebuilds, and dropping them would still render.
        let node = compile_loaded("{% blocktranslate %}a\nb{% endblocktranslate %}");
        let AttrValue::Tokens(list) = attr(&node, "singular") else {
            panic!("singular is not Tokens");
        };
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].token_type, TokenType::Text);
        assert_eq!(list[0].contents, "a\nb");
        assert_eq!(list[0].lineno, 1);
        assert_eq!(list[0].position, None, "the non-debug lexer records none");
    }

    #[test]
    fn block_translate_scans_the_plural_body_into_its_own_list() {
        let node =
            compile_loaded("{% blocktranslate count c=n %}a{% plural %}b{% endblocktranslate %}");
        assert_eq!(
            tokens_of(&node, "singular"),
            [(TokenType::Text, "a".to_string())],
        );
        assert_eq!(
            tokens_of(&node, "plural"),
            [(TokenType::Text, "b".to_string())],
        );
        assert_eq!(attr(&node, "countervar"), &AttrValue::Str("c".to_string()));
        assert_eq!(filter_token(attr(&node, "counter")), "n");
    }

    #[test]
    fn block_translate_carries_the_whole_option_set() {
        let node = compile_loaded(
            "{% blocktranslate with a=1 context \"g\" trimmed asvar v %}x{% endblocktranslate %}",
        );
        let AttrValue::Map(extra) = attr(&node, "extra_context") else {
            panic!("extra_context is not a Map");
        };
        assert_eq!(extra.len(), 1);
        assert_eq!(extra[0].0, "a");
        assert_eq!(filter_token(&extra[0].1), "1");
        assert_eq!(filter_token(attr(&node, "message_context")), r#""g""#);
        assert_eq!(attr(&node, "trimmed"), &AttrValue::Bool(true));
        assert_eq!(attr(&node, "asvar"), &AttrValue::Str("v".to_string()));
        // The option default: no `count` gives a `None` name and counter, passed as
        // the constructor defaults.
        assert_eq!(attr(&node, "countervar"), &AttrValue::None);
        assert_eq!(attr(&node, "counter"), &AttrValue::None);
    }

    #[test]
    fn block_translate_defaults_with_empty_and_trimmed_false() {
        let node = compile_loaded("{% blocktranslate %}x{% endblocktranslate %}");
        assert_eq!(attr(&node, "extra_context"), &AttrValue::Map(Vec::new()));
        assert_eq!(attr(&node, "trimmed"), &AttrValue::Bool(false));
        assert_eq!(attr(&node, "asvar"), &AttrValue::None);
        assert_eq!(attr(&node, "message_context"), &AttrValue::None);
    }

    #[test]
    fn block_translate_supports_the_legacy_with_form() {
        // `support_legacy=True` (`i18n.py:511`): the name is the third bit and the
        // value the first (`base.py:1200`), as in `{% with %}`.
        let node = compile_loaded("{% blocktranslate with a as b %}x{% endblocktranslate %}");
        let AttrValue::Map(extra) = attr(&node, "extra_context") else {
            panic!("extra_context is not a Map");
        };
        assert_eq!(extra.len(), 1);
        assert_eq!(extra[0].0, "b");
        assert_eq!(filter_token(&extra[0].1), "a");
    }

    #[test]
    fn block_translate_needs_a_real_plural_section_when_count_is_given() {
        // A missing plural is reported as a **block-tag** violation with no
        // `(seen …)` clause, and the message names the spelling used.
        for source in [
            "{% blocktranslate count n=x %}one{% endblocktranslate %}",
            "{% blocktranslate count n=x %}one{% foo %}",
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax(
                    "'blocktranslate' doesn't allow other block tags inside it".to_string(),
                ),
                "{source}",
            );
        }
        assert_eq!(
            failure_loaded("{% blocktrans count n=x %}one{% endblocktrans %}").kind,
            ErrorKind::Syntax("'blocktrans' doesn't allow other block tags inside it".to_string(),),
        );
    }

    #[test]
    fn block_translate_names_the_stopping_token_only_from_the_final_check() {
        // Two raise sites, four shapes; row three's parenthetical holds a TEXT token's *contents*.
        for (source, expected) in [
            (
                "{% blocktranslate count n=x %}one{% plural %}many{% foo %}",
                "'blocktranslate' doesn't allow other block tags (seen 'foo') inside it",
            ),
            (
                "{% blocktranslate count n=x %}one{% plural %}many",
                "'blocktranslate' doesn't allow other block tags (seen 'many') inside it",
            ),
            (
                "{% blocktranslate %}one{% plural %}many{% endblocktranslate %}",
                "'blocktranslate' doesn't allow other block tags (seen 'plural') inside it",
            ),
            (
                "{% blocktranslate %}x{% endblocktrans %}",
                "'blocktranslate' doesn't allow other block tags (seen 'endblocktrans') inside it",
            ),
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax(expected.to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn block_translate_reports_its_own_token_when_the_stream_runs_out() {
        // An exhausted stream with nothing read leaves the tag's own token, because
        // `do_block_translate` reassigns its parameter; `scan_token` carries the rule.
        assert_eq!(
            failure_loaded("{% blocktranslate %}").kind,
            ErrorKind::Syntax(
                "'blocktranslate' doesn't allow other block tags (seen 'blocktranslate') inside it"
                    .to_string(),
            ),
        );
        // Once a token *has* been read, that one — the last TEXT token — is reported instead.
        assert_eq!(
            failure_loaded("{% blocktranslate %}x").kind,
            ErrorKind::Syntax(
                "'blocktranslate' doesn't allow other block tags (seen 'x') inside it".to_string(),
            ),
        );
    }

    #[test]
    fn block_translate_option_scan_messages() {
        for (source, expected) in [
            (
                "{% blocktranslate with %}",
                "\"with\" in 'blocktranslate' tag needs at least one keyword argument.",
            ),
            (
                "{% blocktranslate count %}",
                "\"count\" in 'blocktranslate' tag expected exactly one keyword argument.",
            ),
            (
                "{% blocktranslate count a=1 b=2 %}{% endblocktranslate %}",
                "\"count\" in 'blocktranslate' tag expected exactly one keyword argument.",
            ),
            (
                "{% blocktranslate context %}",
                "\"context\" in 'blocktranslate' tag expected exactly one argument.",
            ),
            (
                "{% blocktranslate asvar %}",
                "No argument provided to the 'blocktranslate' tag for the asvar option.",
            ),
            (
                "{% blocktranslate trimmed trimmed %}",
                "The 'trimmed' option was specified more than once.",
            ),
            (
                "{% blocktranslate with a=1 with b=2 %}",
                "The 'with' option was specified more than once.",
            ),
            (
                "{% blocktranslate nope %}",
                "Unknown argument for 'blocktranslate' tag: 'nope'.",
            ),
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax(expected.to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn block_translate_catches_a_malformed_context_filter() {
        // `except Exception`, not `except IndexError` (`i18n.py:524-530`): a malformed filter
        // shares the missing-argument message. `x||y` fails in `parse_filter_expression`
        // before any host lookup, so `NoHost` reaches the arm.
        for source in [
            "{% blocktranslate context %}",
            "{% blocktranslate context x||y %}",
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax(
                    "\"context\" in 'blocktranslate' tag expected exactly one argument."
                        .to_string(),
                ),
                "{source}",
            );
        }
    }

    #[test]
    fn block_translate_the_two_spellings_name_themselves() {
        // `bits[0]` throughout, so one body registered twice produces two texts.
        assert_eq!(
            failure_loaded("{% blocktrans with %}").kind,
            ErrorKind::Syntax(
                "\"with\" in 'blocktrans' tag needs at least one keyword argument.".to_string(),
            ),
        );
        let node = compile_loaded("{% blocktrans %}x{% endblocktrans %}");
        assert_eq!(
            attr(&node, "tag_name"),
            &AttrValue::Str("blocktrans".to_string())
        );
    }
}
