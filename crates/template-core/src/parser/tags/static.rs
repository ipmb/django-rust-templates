//! `{% static %}`, `{% get_static_prefix %}` and `{% get_media_prefix %}` —
//! `django/templatetags/static.py` @ 6.0.9.
//!
//! # One module, three commands, and that is Django's shape
//!
//! `registry::LIBRARY_REGISTRY` maps all three names to [`compile`], because
//! `do_static` is `StaticNode.handle_token` and the two prefix tags are
//! `PrefixNode.handle_token` with a different setting name
//! (`static.py:74`, `static.py:92`, `static.py:171`). So the first thing this function does is
//! read the command back out of the token and branch on it — the first tag in
//! the tree whose registry entry is shared.
//!
//! # Two splitters, and the source says which
//!
//! `PrefixNode.handle_token` uses `token.contents.split()` and says why in a
//! comment (`static.py:29-31`): *"token.split_contents() isn't useful here
//! because tags using this method don't accept variable as arguments."* That is
//! Python's `str.split` — so [`pyunicode::split`], whose whitespace table is
//! `str.isspace()` and so includes U+001C-U+001F — and **not** Rust's
//! `str::split_whitespace`, which would disagree on those four code points and
//! so on whether a `varname` was given at all. `StaticNode.handle_token` uses
//! `token.split_contents()` (`static.py:126`), the quote-aware splitter, so the
//! two paths in this file use two different ones. Both are deliberate.
//!
//! # Two unguarded indexes, and what they become here
//!
//! Django indexes a token list with no length guard in two places, and both are
//! reachable:
//!
//! ```text
//! {% get_static_prefix as %}   → tokens[2]  → IndexError: list index out of range
//! {% static as as %}           → bits[3]    → IndexError: list index out of range
//! ```
//!
//! Measured on the pinned tree; both leave `Engine.from_string` as an
//! `IndexError`, not a `TemplateSyntaxError`, because `Parser.parse` catches
//! `Exception` only to attach `.token` (`base.py:583-585`) and re-raises the
//! object it caught. This port answers with [`ErrorKind::Syntax`] instead, and
//! the divergence is deliberate rather than unnoticed: an index past the end of
//! a token list is a verdict on the *template* — the same judgement the
//! `{% static %}` and `{% get_static_prefix nope %}` messages above it make —
//! and reproducing an `IndexError` would need a new [`ErrorKind`] variant for a
//! case with no producer, no test and no reader. The message is Rust's, which §6
//! grants for syntax errors. Each site has a Rust test that pins it, so the
//! choice is a recorded decision rather than an accident of the port.
//!
//! [`ErrorKind::Syntax`]: crate::parser::error::ErrorKind::Syntax
//! [`ErrorKind`]: crate::parser::error::ErrorKind

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::pyunicode;
use crate::token::Token;

/// The command this token opens with, as `Parser::parse` dispatched on it.
///
/// Python's `str.split` for the same reason `parse` uses it: this function's
/// answer must be the name the registry was consulted with, or one of the three
/// tags would take another's body. A dispatched block tag always has a first
/// word — `parse` rejects `Empty block tag` before it looks the command up — so
/// the empty answer is unreachable and becomes [`ErrorKind::Internal`] rather
/// than a panic.
///
/// [`ErrorKind::Internal`]: crate::parser::error::ErrorKind::Internal
fn command(token: &Token) -> Result<String, Error> {
    pyunicode::split(&token.contents)
        .first()
        .map(|word| (*word).to_string())
        .ok_or_else(|| {
            Error::internal(format!(
                "`static.rs` was dispatched on an empty block tag (line {}); \
                 `Parser::parse` rejects that before it reaches a registry entry",
                token.lineno,
            ))
        })
}

pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    match command(token)?.as_str() {
        "static" => static_node(parser, token),
        "get_static_prefix" => prefix_node(token, "STATIC_URL"),
        "get_media_prefix" => prefix_node(token, "MEDIA_URL"),
        other => Err(Error::internal(format!(
            "`static.rs` is registered for three commands and `{other}` is not \
             one of them",
        ))),
    }
}

/// `PrefixNode.handle_token` (`static.py:26-41`) for one setting name.
fn prefix_node(token: &Token, name: &str) -> Result<NodeDesc, Error> {
    let tokens = pyunicode::split(&token.contents);
    // `parse` checked the first word exists, so `tokens[0]` is safe here.
    let tag = tokens[0];
    if tokens.len() > 1 && tokens[1] != "as" {
        // `"First argument in '%s' must be 'as'" % tokens[0]` (`static.py:35-37`)
        // — `%s`, not `%r`, so no host `repr` is needed.
        return Err(Error::syntax(format!(
            "First argument in '{tag}' must be 'as'"
        )));
    }
    let varname = if tokens.len() > 1 {
        // `varname = tokens[2]`, unguarded in the source. See the module doc.
        match tokens.get(2) {
            Some(varname) => AttrValue::Str((*varname).to_string()),
            None => return Err(as_name_required(tag)),
        }
    } else {
        // `cls(varname, name)` with `varname = None`; emitted rather than
        // omitted so the descriptor names every parameter `PrefixNode.__init__`
        // takes (`static.py:15`).
        AttrValue::None
    };
    Ok(NodeDesc::new("PrefixNode")
        .attr("varname", varname)
        .attr("name", AttrValue::Str(name.to_string())))
}

/// `StaticNode.handle_token` (`static.py:123-141`).
fn static_node(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;
    if bits.len() < 2 {
        // `"'%s' takes at least one argument (path to file)" % bits[0]`
        // (`static.py:129-131`) — `%s` again.
        return Err(Error::syntax(format!(
            "'{}' takes at least one argument (path to file)",
            bits[0],
        )));
    }
    let path = parser.compile_filter(&bits[1])?;
    // `if len(bits) >= 2 and bits[-2] == "as": varname = bits[3]`
    // (`static.py:135-138`). The `%`-relative index is what makes
    // `{% static 'a' as u extra %}` mean *no* varname — `bits[-2]` is `u`, not
    // `as` — and it is why the check is on the second-to-last bit rather than on
    // the presence of `as` anywhere. Measured; the Rust tests pin all three
    // shapes.
    let varname = if bits.len() >= 2 && bits[bits.len() - 2] == "as" {
        match bits.get(3) {
            Some(varname) => AttrValue::Str(varname.clone()),
            None => return Err(as_name_required(&bits[0])),
        }
    } else {
        AttrValue::None
    };
    Ok(NodeDesc::new("StaticNode")
        .attr("varname", varname)
        .attr("path", path))
}

/// The error for Django's unguarded index into a short token list.
///
/// One message for both sites because it is one shape: `as` was given and the
/// variable name after it is not in the token list. See the module doc for why
/// this is a `Syntax` error where Django raises `IndexError`.
fn as_name_required(tag: &str) -> Error {
    Error::syntax(format!("'{tag}' takes 'as' followed by a variable name"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::host::NoHost;
    use crate::parser::tags::support::{attr, compile_loaded, failure_loaded};
    use crate::token::TokenType;

    fn filter_token(node: &NodeDesc, name: &str) -> String {
        let AttrValue::FilterExpr(expression) = attr(node, name) else {
            panic!("{name} is a FilterExpr, not {:?}", attr(node, name));
        };
        expression.token.clone()
    }

    #[test]
    fn the_two_prefix_tags_differ_only_in_the_setting_they_name() {
        let static_prefix = compile_loaded("{% get_static_prefix %}");
        assert_eq!(static_prefix.class_name, "PrefixNode");
        assert_eq!(
            attr(&static_prefix, "name"),
            &AttrValue::Str("STATIC_URL".to_string()),
        );
        assert_eq!(
            attr(&static_prefix, "varname"),
            &AttrValue::None,
            "no `as` is Django's `varname = None`, emitted as None",
        );

        let media = compile_loaded("{% get_media_prefix %}");
        assert_eq!(media.class_name, "PrefixNode");
        assert_eq!(
            attr(&media, "name"),
            &AttrValue::Str("MEDIA_URL".to_string()),
        );
    }

    #[test]
    fn the_prefix_tags_read_the_variable_after_as_and_ignore_the_rest() {
        // Measured: `{% get_static_prefix as p extra %}` is `varname='p'` with
        // the extra bit silently dropped, because Django reads `tokens[2]` and
        // never looks at `tokens[3:]`.
        let node = compile_loaded("{% get_static_prefix as static_prefix extra %}");
        assert_eq!(
            attr(&node, "varname"),
            &AttrValue::Str("static_prefix".to_string()),
        );
        assert_eq!(
            attr(&node, "name"),
            &AttrValue::Str("STATIC_URL".to_string())
        );
    }

    #[test]
    fn the_prefix_tags_reject_a_first_argument_that_is_not_as() {
        // The message names the command that was dispatched, which is why the
        // two tags in this file cannot share one literal: `get_media_prefix`
        // reports itself.
        for (source, message) in [
            (
                "{% get_static_prefix nope %}",
                "First argument in 'get_static_prefix' must be 'as'",
            ),
            (
                "{% get_media_prefix nope %}",
                "First argument in 'get_media_prefix' must be 'as'",
            ),
            (
                // `tokens[1]` is `q`, and the check is on `tokens[1]` alone, so
                // a *second* `as` does not rescue it.
                "{% get_static_prefix q as p %}",
                "First argument in 'get_static_prefix' must be 'as'",
            ),
        ] {
            assert_eq!(
                failure_loaded(source).kind,
                ErrorKind::Syntax(message.to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn the_prefix_tags_report_a_missing_variable_rather_than_panicking() {
        // Django: `IndexError: list index out of range` (measured). See the
        // module doc for the divergence and why it is taken.
        assert_eq!(
            failure_loaded("{% get_static_prefix as %}").kind,
            ErrorKind::Syntax(
                "'get_static_prefix' takes 'as' followed by a variable name".to_string(),
            ),
        );
    }

    #[test]
    fn the_prefix_split_is_pythons_str_split_not_rusts() {
        // The one row that falsifies the choice of splitter, built by hand
        // because no template the lexer accepts can carry U+001C as contents.
        // Python's `"a\u{1c}as\u{1c}p".split()` is three fields — U+001C is
        // `str.isspace()` — while `split_whitespace` is one, and the two answers
        // differ on whether `varname` was given at all.
        let token = Token {
            token_type: TokenType::Block,
            contents: "get_static_prefix\u{1c}as\u{1c}p".to_string(),
            lineno: 1,
            position: None,
        };
        let node = prefix_node(&token, "STATIC_URL").expect("three fields, so `as p`");
        assert_eq!(attr(&node, "varname"), &AttrValue::Str("p".to_string()));
    }

    #[test]
    fn static_compiles_its_path_as_a_filter_expression() {
        let node = compile_loaded(r#"{% static "myapp/css/base.css" %}"#);
        assert_eq!(node.class_name, "StaticNode");
        assert_eq!(
            filter_token(&node, "path"),
            r#""myapp/css/base.css""#,
            "the quotes survive `split_contents`, which is why this tag uses it",
        );
        assert_eq!(attr(&node, "varname"), &AttrValue::None);
    }

    #[test]
    fn static_takes_a_varname_only_when_as_is_the_second_to_last_bit() {
        // Three measured shapes, and the third is the one a port written from
        // the prose gets wrong: `as` is present, `bits[-2]` is not it, and
        // Django silently reads *no* varname.
        let named = compile_loaded(r#"{% static "a/b.css" as admin_css %}"#);
        assert_eq!(
            attr(&named, "varname"),
            &AttrValue::Str("admin_css".to_string()),
        );

        for source in [
            // `bits[-2]` is `u`, so the varname is dropped rather than read
            // from `bits[2]`.
            r#"{% static "a/b.css" as u extra %}"#,
            // `bits[-2]` is `b`.
            "{% static a b as c d %}",
        ] {
            let node = compile_loaded(source);
            assert_eq!(attr(&node, "varname"), &AttrValue::None, "{source}");
        }
        assert_eq!(
            filter_token(&compile_loaded("{% static a b as c d %}"), "path"),
            "a",
        );
    }

    #[test]
    fn static_rejects_a_missing_path() {
        assert_eq!(
            failure_loaded("{% static %}").kind,
            ErrorKind::Syntax("'static' takes at least one argument (path to file)".to_string()),
        );
    }

    #[test]
    fn static_reports_a_missing_variable_rather_than_panicking() {
        // Django: `IndexError` (measured). See the module doc.
        assert_eq!(
            failure_loaded("{% static as as %}").kind,
            ErrorKind::Syntax("'static' takes 'as' followed by a variable name".to_string()),
        );
    }

    #[test]
    fn a_command_the_registry_does_not_map_is_an_internal_error() {
        // The invariant `compile` rests on, staged: it is reachable only through
        // `LIBRARY_REGISTRY`, so a fourth command reaching it is a registry bug
        // rather than a template's.
        let token = Token {
            token_type: TokenType::Block,
            contents: "not_a_static_tag".to_string(),
            lineno: 1,
            position: None,
        };
        let mut parser = Parser::new(Vec::new(), &NoHost);
        // The module's own `compile`, by name: the test module's helper for a
        // source string is `compile_loaded`, so there is no shadowing to route
        // around here.
        let error = compile(&mut parser, &token).expect_err("not in the registry");
        assert!(
            matches!(error.kind, ErrorKind::Internal(_)),
            "expected Internal, got {:?}",
            error.kind,
        );
    }
}
