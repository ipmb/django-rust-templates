//! `i18n`'s ten tags — `django/templatetags/i18n.py` @ 6.0.9.
//!
//! # Ten bodies, and the library is complete
//!
//! The file registers ten tag names and all ten gain Rust compile functions
//! here: `get_available_languages`, `get_language_info`,
//! `get_language_info_list`, `get_current_language`,
//! `get_current_language_bidi`, `translate`, `trans`, `language`,
//! `blocktranslate` and `blocktrans`. The last two are **one body**
//! ([`block_translate`], `i18n.py:456-596`) registered under two names, exactly
//! as `translate`/`trans` are. Task 6a landed the first eight; Task 6b lands
//! these two, which take `LIBRARY_REGISTRY` to the whole of
//! `django_own_tags()`'s eighteen library names.
//!
//! `blocktranslate` is the milestone's one node with **no nodelist**: its body is
//! a slice of the token stream, carried as [`AttrValue::Tokens`]. See
//! [`block_translate`] for the scan and the four message shapes it produces.
//!
//! The file's four *filters* (`language_name`, `language_name_translated`,
//! `language_name_local`, `language_bidi`, `i18n.py:293,298,304,309`) are Task
//! 7's Rust bodies and this task transcribes their Python instead. That division
//! is M12's own — the five files are interposed whole, and no version of
//! interposing `django.templatetags.i18n` leaves its filters Django's.
//!
//! # The render stays Python's, and that is the coupling test rather than a gap
//!
//! Every node here reaches `django.utils.translation` in its `render` — the
//! five `get_*` nodes read a language or a layout, `TranslateNode.render` runs
//! the filter expression with `var.translate` set, `LanguageNode.render` wraps
//! the body in `translation.override` (`i18n.py:213`). All of that is our
//! transcription's Python, reached through the node the materialiser builds from
//! the descriptor below — *not* a Rust body. Only the **compile** functions are
//! Rust's, for §6's reason: what M12 ports is the parse, and the render delegates
//! to a subsystem the milestone does not port.
//!
//! # Two splittings, and which tag gets which
//!
//! Five tags read their arguments with `token.contents.split()` rather than
//! `token.split_contents()`, and their source says why (`i18n.py:232-234`): the
//! tag accepts no *variable* arguments, so the quote-aware splitter is not
//! wanted. So `{% get_available_languages as lang %}` and its two siblings
//! (`get_current_language`, `get_current_language_bidi`) go through
//! [`pyunicode::split`], and their `as <name>` target is a **bare string**, not a
//! compiled filter. The other five — `get_language_info`,
//! `get_language_info_list`, `translate`/`trans`, `language` — use
//! [`split_contents`] and compile their operand with [`Parser::compile_filter`].
//! Getting the pair backwards compiles and passes nothing: the bare-string form
//! would hand `GetAvailableLanguagesNode` a `Variable` where its `render` does
//! `context[self.variable] = …`.
//!
//! # `%r` of a list, and why it goes through the host
//!
//! Five of these tags interpolate a Python **list** with `%r` in their refusal
//! message — three the whole `args` (`i18n.py:237,330,353`) and two the
//! `args[1:]` slice (`i18n.py:260,287`):
//!
//! ```text
//! {% get_available_languages nope %}  → 'get_available_languages' requires
//!                                        'as variable' (got ['get_available_languages', 'nope'])
//! {% get_language_info for x %}       → 'get_language_info' requires
//!                                        'for string as variable' (got ['for', 'x'])
//! ```
//!
//! The elements are **user text**, so each goes through [`Parser::py_repr`] —
//! the host's Python `repr` — rather than [`py_repr_str`], whose ASCII-only
//! assertion exists for exactly this reason. That is
//! [`tags::python_list_repr`](crate::parser::tags::python_list_repr), the
//! user-text counterpart of the constant-argument `py_list_repr`; lifted into
//! `tags/mod.rs` in this task because `tz.rs` had the one private copy and two
//! callers need it now.
//!
//! # `{% translate %}`'s argument scan is ordered, and every branch is observable
//!
//! The `while remaining:` loop (`i18n.py:412-451`) has four branches, and the
//! order between them is load-bearing:
//!
//! * The **duplicate check runs first** (`i18n.py:414-417`), before dispatch, so
//!   a repeated option is refused even when its first use was valid —
//!   `{% translate "x" noop noop %}` reports
//!   `The 'noop' option was specified more than once.` and not the second
//!   `noop` being ignored.
//! * `context`'s **value** is refused when it is the literal `as` or `noop`
//!   (`i18n.py:410,428-432`) — the `invalid_context` set — so
//!   `{% translate "x" context as %}` is `Invalid argument 'as' provided…`.
//! * `as`'s value is **not** validated at all (`i18n.py:434-441`): any word is
//!   stored as the context variable's name.
//! * A trailing `context`/`as` with nothing after it raises its **own** message
//!   (`i18n.py:421-427,435-440`), which is what `remaining.pop(0)`'s `IndexError`
//!   catches — not the generic unknown-argument branch.
//!
//! All six messages name `bits[0]` where they name the tag, so `{% trans %}` and
//! `{% translate %}` produce *different text for the same mistake*. A row that
//! uses the wrong spelling still passes unless it asserts the message, which is
//! why the Rust tests below write each expected message out in full.
//!
//! # What the transcribed `TranslateNode.__init__` does, and what Rust must not
//!
//! `TranslateNode.__init__` rewrites a **literal** string in place
//! (`i18n.py:80-82`): if the compiled operand's `var` is a `str` it sets
//! `is_var = True` and rebuilds `var` as `Variable("'%s'" % var)`, so a bare
//! `{% translate "hello" %}` marks the literal for extraction. That is the
//! *transcribed class's* job — Rust hands over the parts
//! (`filter_expression`, `noop`, `asvar`, `message_context`) and materialisation
//! runs the real `__init__`. A port that reimplemented the rewrite here would
//! produce a `Variable` on the wire where the class expects a `FilterExpression`,
//! and would duplicate a rule that already has an owner.
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
/// `token.contents.split()` and a bare-string `as` target — see the module doc.
/// The refusal interpolates the whole `args` list with `%r`.
pub fn get_available_languages(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = pyunicode::split(&token.contents);
    if args.len() != 3 || args[1] != "as" {
        // `"'get_available_languages' requires 'as variable' (got %r)" % args`.
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
/// `split_contents()` and a compiled operand, unlike its `_list` sibling's
/// neighbour above. The refusal interpolates `args[0]` with `%s` and the
/// `args[1:]` **slice** with `%r`.
pub fn get_language_info(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = split_contents(token)?;
    if args.len() != 5 || args[1] != "for" || args[3] != "as" {
        // `"'%s' requires 'for string as variable' (got %r)" % (args[0], args[1:])`.
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
/// The `_list` spelling differs from [`get_language_info`] in exactly two
/// places: the word `sequence` in the message and the node class.
pub fn get_language_info_list(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let args = split_contents(token)?;
    if args.len() != 5 || args[1] != "for" || args[3] != "as" {
        // `"'%s' requires 'for sequence as variable' (got %r)" % (args[0], args[1:])`.
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
        // `"'get_current_language' requires 'as variable' (got %r)" % args`.
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
        // `"'get_current_language_bidi' requires 'as variable' (got %r)" % args`.
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
/// One body registered under two names (`i18n.py:358-359`), like
/// `tags::static_tag::compile`. The scan's four branches and their order are in
/// this module's doc; the six refusal messages all name `bits[0]`, so the two
/// spellings differ in their text.
pub fn translate(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    if bits.len() < 2 {
        // `"'%s' takes at least one argument" % bits[0]`.
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
    // A `Vec` rather than a `HashSet`: the values are short option words and the
    // scan is tiny, and `contains` keeps the source's `option in seen` reading
    // without pulling in a hasher.
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
/// The argument is one filter expression, compiled **before** the body is parsed
/// (`i18n.py:613-614`), exactly as `{% timezone %}` does — so
/// `{% language %}x` reports the argument error, not `Unclosed tag`, and the
/// valid-argument control reports `Unclosed tag`.
pub fn language(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    if bits.len() != 2 {
        // `"'%s' takes one argument (language)" % bits[0]`.
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
/// One body registered under two names (`i18n.py:456-457`), like [`translate`].
/// It is the milestone's one node that carries no nodelist: `singular`/`plural`
/// are **lists of `Token` objects** ([`AttrValue::Tokens`]), scanned out of the
/// token stream and handed to `BlockTranslateNode`, whose `render_token_list`
/// walks them by `token.token_type` and `token.contents`.
///
/// # The scan reassigns the function's own `token` parameter
///
/// `def do_block_translate(parser, token)` and the loop at `i18n.py:562-567`
/// writes `token = parser.next_token()`, so the name after the loop is the last
/// token *read*, not the tag's — and that is observable: when the stream ends
/// before the end tag and nothing was read, `token` is still the tag's own, so
/// `{% blocktranslate %}` alone reports `(seen 'blocktranslate')` while
/// `{% blocktranslate %}x` reports `(seen 'x')` (the last TEXT token). Rust has
/// no parameter aliasing, so the rule is carried by `scan_token` below, which
/// starts as the tag's token and is overwritten by every read.
///
/// # The four message shapes come from *which scan stopped*
///
/// Not from "`count` decides": `count` only decides whether the plural check
/// happens at all (`i18n.py:568`). The singular scan's stopping token is
/// compared against `plural` and raises **without** naming it
/// (`i18n.py:569-572`); the final token is compared against `end_tag_name` and
/// raises **naming** it (`i18n.py:580-584`). So with `count` both forms are
/// reachable — a `count` template whose `{% plural %}` is present and whose end
/// tag is not takes the second. And the named token is whatever `scan_token`
/// last held, which inside the plural scan is a TEXT or VAR token: the
/// parenthetical can be a text node's *contents* (`... (seen 'many')`), not a
/// tag name. Formatting it as a command, or stripping a `{% %}` wrapper, would
/// diverge on a template Django accepts the shape of.
///
/// [`AttrValue::Tokens`]: crate::parser::desc::AttrValue::Tokens
pub fn block_translate(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    // A dispatched block tag always has a first bit — `Parser::parse` rejects an
    // empty block tag before it looks the command up — so this is Django's
    // `bits[1:]` without the slice's empty-safety dance.
    let tag_name = bits[0].clone();
    let mut remaining_bits: Vec<String> = bits[1..].to_vec();

    // `options` is Django's dict, kept as ordered pairs so the duplicate check
    // reads the source's `option in options` and the insertion order survives.
    // Each value is stored as the `AttrValue` its branch produces: a `Map` for
    // `with`/`count`, a compiled `FilterExpr` for `context`, `Bool` for
    // `trimmed`, `Str` for `asvar`.
    let mut options: Vec<(String, AttrValue)> = Vec::new();
    let mut asvar: Option<String> = None;

    while !remaining_bits.is_empty() {
        let option = remaining_bits.remove(0);
        // The duplicate check runs **before** dispatch (`i18n.py:506-509`), so a
        // repeated option is refused even when its first use was valid. Only a
        // recognised option can be in `options`, so `option` here is always one
        // of the five ASCII names and its `%r` is the plain quoted form.
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
            // **malformed filter** and a **missing argument** become the same
            // message, so any failure from `compile_filter` is caught.
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
            // The literal `True`, not a value read from `bits` (`i18n.py:531-532`).
            AttrValue::Bool(true)
        } else if option == "asvar" {
            let Some(value) = remaining_bits.first().cloned() else {
                // `%s` of `bits[0]` in Django's source, with the quotes in the
                // format string — so the tag name goes in bare, not through `%r`.
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
            // `next(iter(options["count"].items()))` — the dict has exactly one
            // item, which the `count` branch above proved.
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

    // Django's `if countervar and counter:` (`i18n.py:568`), truthiness and all:
    // `counter` is a `FilterExpression` and always truthy, so what the test
    // really decides is that the `count` option is present with a non-empty
    // name.
    let has_counter =
        countervar.as_deref().is_some_and(|name| !name.is_empty()) && counter.is_some();

    let mut singular: Vec<TokenDesc> = Vec::new();
    let mut plural: Vec<TokenDesc> = Vec::new();

    // `while parser.tokens:` — read one token and stop at the first that is not
    // TEXT or VAR. `scan_token` starts as the tag's own token, which is what
    // `token` still holds when the stream is empty (see the doc above).
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
        // `{% plural %}` is **not** a registered tag: it is compared as a bare
        // string (`i18n.py:569`), which is why it in the wrong place produces
        // this message rather than an unknown-tag error.
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
        // `token.contents.split()`, so the message is `%r` of the whole list.
        // Written out rather than derived from `python_list_repr` — a message
        // built by calling the helper again agrees with itself by construction.
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
        // `"'%s' requires 'for string as variable' (got %r)" % (args[0], args[1:])`
        // — `%s` for the tag, `%r` of the *slice* for the rest. `split_contents`,
        // so quotes survive: `for "x" as` is four bits, not five.
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
        // The one word that differs from `get_language_info`'s message.
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
        // `"'%s' takes at least one argument" % bits[0]` — `%s`, and the tag
        // name is the spelling used, so the two names differ here too.
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
        // `option in seen` is checked *first* (`i18n.py:414-417`), so a repeated
        // `noop` is refused even though the first use was valid — and the
        // message names the option, not the tag.
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
        // The `invalid_context` set is `{"as", "noop"}` (`i18n.py:410`), and the
        // message names both the value and the tag.
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
        // `remaining.pop(0)`'s `IndexError` is caught and re-raised with a
        // dedicated message — not the unknown-argument branch (`i18n.py:421-427,435-440`).
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
        // `asvar = value` with no check at all (`i18n.py:441`): a name that is
        // not a variable is still stored, unlike `context`'s value.
        let node = compile_loaded(r#"{% translate "x" as not-a-variable %}"#);
        assert_eq!(
            attr(&node, "asvar"),
            &AttrValue::Str("not-a-variable".to_string()),
        );
    }

    #[test]
    fn translate_reports_an_unknown_option_verbatim() {
        // The long message, and its two `%s` slots are the tag and the option.
        assert_eq!(
            failure_loaded(r#"{% translate "x" bogus %}"#).kind,
            ErrorKind::Syntax(
                "Unknown argument for 'translate' tag: 'bogus'. The only options available are \
                 'noop', 'context \"xxx\", and 'as VAR'."
                    .to_string(),
            ),
        );
        // And the *other* spelling of the same tag names itself in the message,
        // which is the whole reason each row writes its text out.
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
        // The argument is compiled before `parser.parse` (`i18n.py:613-614`), as
        // in `{% timezone %}`: the missing argument is reported rather than the
        // unclosed block, and the valid-argument control reports `Unclosed tag`.
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

    /// The `(token_type, contents)` of a `Tokens` attribute, so a row can state
    /// the *scan* rather than the whole `TokenDesc`.
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
        // The milestone's one node with no nodelist and the one attribute that
        // is a slice of the token stream: `singular` is the `Token` objects
        // themselves, because `render_token_list` reads `token.token_type` and
        // `token.contents` off them.
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
        // `TokenDesc` and not a bare string: `lineno`/`position` are part of what
        // the shell rebuilds a real `Token` from (the `Tokens` pyclass carries the
        // same 4-tuple `_native.lex` emits), and dropping them would still render.
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
        // The option default: no `count` gives both a `None` name and a `None`
        // counter, which the materialiser passes as the constructor defaults.
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
        // value the first (`base.py:1200`), exactly as `{% with %}`.
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
        // The family a port derives wrongly from reading the source: the missing
        // plural is reported as a **block-tag** violation, with **no `(seen …)`**
        // clause, and the message names the spelling used.
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
        // The alias as spelled, which is a different message from the same raise.
        assert_eq!(
            failure_loaded("{% blocktrans count n=x %}one{% endblocktrans %}").kind,
            ErrorKind::Syntax("'blocktrans' doesn't allow other block tags inside it".to_string(),),
        );
    }

    #[test]
    fn block_translate_names_the_stopping_token_only_from_the_final_check() {
        // Four shapes from two raise sites. Row three is the one no reading of the
        // source produces: the parenthetical holds a TEXT token's *contents*,
        // because it is whatever the reassigned `token` last held.
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
        // Trap 2: `do_block_translate` reassigns its own `token` parameter, so an
        // exhausted stream with nothing read leaves the tag's own token and
        // `{% blocktranslate %}` reports itself. Rust has no parameter aliasing;
        // the rule is `scan_token` starting at the tag's token.
        assert_eq!(
            failure_loaded("{% blocktranslate %}").kind,
            ErrorKind::Syntax(
                "'blocktranslate' doesn't allow other block tags (seen 'blocktranslate') inside it"
                    .to_string(),
            ),
        );
        // And once a token *has* been read, that one is reported instead — the
        // last TEXT token, not the tag.
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
        // `except Exception`, not `except IndexError` (`i18n.py:524-530`): a
        // malformed filter becomes the *same* message as a missing argument.
        // `x||y` is a filter chain Django cannot parse (`Could not parse some
        // characters`), which `parse_filter_expression` raises before any host
        // lookup — so `NoHost` is enough to reach the arm.
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
