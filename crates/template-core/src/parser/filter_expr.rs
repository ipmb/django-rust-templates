//! `filter_re` and `FilterExpression.__init__` — `base.py:701-775`.
//!
//! The descriptor carries names, not handles: a `FilterExpression` holds a Python
//! function and `args_check` inspects its signature, so no Rust type can carry one.
//!
//! Two substitutions in the assembled `filter_raw_string` are behavioural. Both
//! `\w` go through `pyregex`, in *different spellings* — `var_chars` inside a
//! class, `filter_name` standalone — and a swap is asymmetric: the bracketed form
//! spliced into a class **parses and works by accident** under the `regex` crate's
//! nested classes, so nothing reports it. `\s` becomes `[WS_CLASS]` because
//! Python's `\s` includes U+001C-U+001F and the crate's does not; measured, Django
//! accepts `x\x1c|upper`, which `\s` would reject. The pattern is assembled rather
//! than spelled out with `(?x)`, so the four `\s*` in the filter alternative — the
//! only whitespace there that is pattern rather than layout — cannot be lost.
//!
//! `constant` and `var` carry `^` and the filter alternative does not, so
//! `find_iter` matches the variable part at 0 and each filter *anywhere* after — a
//! skipped character then shows as a match not starting where the last one ended.
//! `upto` advances on every iteration including the first, and the gap error
//! compares it to `match.start()`.
//!
//! Django's `if var_obj is None` flag (`base.py:752`) is an `Option` here, which
//! agrees with Django on every input.

use std::sync::OnceLock;

use regex::Regex;

use crate::parser::desc::{AttrValue, FilterCall, FilterExprDesc, FilterVar};
use crate::parser::error::Error;
use crate::parser::host::Host;
use crate::parser::variable::parse_variable;
use crate::pyunicode::WS_CLASS;

/// The double-quoted string alternative from `constant_string` (`base.py:695`).
const STR_DQ: &str = r#""[^"\\]*(?:\\.[^"\\]*)*""#;

/// The single-quoted string alternative (`base.py:696`).
const STR_SQ: &str = r"'[^'\\]*(?:\\.[^'\\]*)*'";

/// `constant_string`: a quoted string, or one wrapped in Django's `_("…")` marker
/// (`base.py:688-699`). Built rather than written out because `filter_raw_string`
/// splices it **twice** — as `constant` and as `constant_arg` — and the two must
/// stay identical.
fn constant_string() -> String {
    // `i18n_close` is `re.escape(")")` (`base.py:690-698`), so the marker's closing
    // paren is a bare `\)`. `_\)` reads naturally and is wrong: the alternative then
    // fails on `_("x")`, the `var` group matches the leading `_`, and the token is
    // reported as an underscore violation. Named arguments because `format!` cannot
    // capture from the surrounding scope when its format string comes from a macro.
    format!(
        concat!(
            r"(?:",
            r"_\({STR_DQ}\)|",
            r"_\({STR_SQ}\)|",
            r"{STR_DQ}|",
            r"{STR_SQ}",
            r")",
        ),
        STR_DQ = STR_DQ,
        STR_SQ = STR_SQ,
    )
}
/// `filter_raw_string`, assembled (`base.py:701-719`). `(?::` is `(?:%(arg_sep)s`
/// with `arg_sep` spliced in: an argument separator inside a non-capturing group.
fn filter_re_source() -> String {
    let constant = constant_string();
    // `var_chars = r"\w\.\+-"` spliced into `[%(var_chars)s]+` (`base.py:714`):
    // inside a class, so the *contents* form of Python's `\w`.
    let word = crate::pyunicode::profile::word_in_class();
    let var_chars = format!(r"[{word}\.\+-]+");
    // `(?P<filter_name>\w+)` (`base.py:705`): standalone, so the bracketed form.
    let filter_name = format!("[{word}]+");
    format!(
        concat!(
            r"^(?P<constant>{constant})|",
            r"^(?P<var>{var_chars})|",
            r"(?:[{ws}]*\|[{ws}]*",
            r"(?P<filter_name>{filter_name})",
            r"(?::",
            r"(?:(?P<constant_arg>{constant})|(?P<var_arg>{var_chars}))",
            r")?",
            r")",
        ),
        constant = constant,
        var_chars = var_chars,
        filter_name = filter_name,
        ws = WS_CLASS,
    )
}

/// The compiled pattern, built once. A `OnceLock` rather than a
/// `lazy_static`-style macro: the crate has one dependency and needs no other.
fn filter_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&filter_re_source()).expect("filter_re's source is fixed and must compile")
    })
}

/// `FilterExpression.__init__`, with the filter checks routed to `host`. Takes the
/// raw token rather than a `Token`: the caller already decided this is a `{{ … }}`.
/// [`Host::check_filter`] says why the check is here and why it follows the args.
pub fn parse_filter_expression(token: &str, host: &dyn Host) -> Result<FilterExprDesc, Error> {
    let mut var: Option<FilterVar> = None;
    let mut filters: Vec<FilterCall> = Vec::new();
    let mut upto = 0usize;

    for captures in filter_re().captures_iter(token) {
        let whole = captures
            .get(0)
            .expect("group 0 is the whole match and is always present");
        let start = whole.start();

        if upto != start {
            // Django's `%s|%s|%s` (`base.py:747-750`). Byte offsets here, code-point
            // offsets in Python, but both land on a character boundary, so the
            // substrings are identical.
            return Err(Error::syntax(format!(
                "Could not parse some characters: {}|{}|{}",
                &token[..upto],
                &token[upto..start],
                &token[start..],
            )));
        }

        if var.is_none() {
            if let Some(constant) = captures.name("constant") {
                var = Some((false, variable_of(constant.as_str())?));
            } else if let Some(name) = captures.name("var") {
                var = Some((true, variable_of(name.as_str())?));
            } else {
                // A filter alternative at position 0, so the token starts with `|`.
                // `is None` rather than falsiness is what makes `|upper` an error
                // rather than a silent empty variable.
                return Err(Error::syntax(format!(
                    "Could not find variable at start of {token}."
                )));
            }
        } else {
            let name = captures
                .name("filter_name")
                .expect("only the filter alternative can match past position 0")
                .as_str()
                .to_string();

            // **The arguments before the check**: Django builds `args` at
            // `base.py:766-770`, then calls `find_filter` at `base.py:771`; the order
            // is observable — `x|does_not_exist:_bad` reports the underscore error,
            // `x|does_not_exist:-5` the filter error.
            let mut args: Vec<FilterVar> = Vec::new();
            if let Some(constant) = captures.name("constant_arg") {
                args.push((false, variable_of(constant.as_str())?));
            } else if let Some(name) = captures.name("var_arg") {
                args.push((true, variable_of(name.as_str())?));
            }

            // The handle comes back from here, where Django binds `filter_func`
            // (`base.py:771`) — that is what renders, so the name is never looked up
            // again. `None` is a host with no registry; see `Host::check_filter`.
            let handle = host.check_filter(&name, args.len())?;
            filters.push(FilterCall { name, args, handle });
        }

        upto = whole.end();
    }

    if upto != token.len() {
        return Err(Error::syntax(format!(
            "Could not parse the remainder: '{}' from '{}'",
            &token[upto..],
            token,
        )));
    }

    Ok(FilterExprDesc {
        token: token.to_string(),
        // `FilterExpression("")` is **valid** and leaves `self.var = None`: no match,
        // so the loop never runs and `upto == len(token)` already. `Parser.parse`
        // rejects an empty `{{ }}`, so this is only reachable directly.
        var: var.unwrap_or((false, AttrValue::None)),
        filters,
    })
}

/// A matched group as the `Variable` it names. Both branches are a `Variable`; the
/// caller's flag, not this function, says whether the materialiser resolves it now
/// or keeps it. See [`FilterVar`] for why the constant branch cannot carry a value.
fn variable_of(raw: &str) -> Result<AttrValue, Error> {
    Ok(AttrValue::Variable(Box::new(parse_variable(raw)?)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::desc::{NodeRef, VariableDesc};
    use crate::token::Token;

    /// A host that rejects exactly what it is told to, and accepts the rest. The
    /// rejections are Django's messages verbatim, because the point of the tests
    /// below is **where in the loop** a filter fails. `Error::host` because it is
    /// the variant the FFI adapter builds out of a Python exception.
    #[derive(Default)]
    struct Rejecting {
        unknown: Option<&'static str>,
        bad_arity: Option<(&'static str, usize)>,
        /// Every call, in order, so a test can assert *when* the host was asked.
        seen: std::cell::RefCell<Vec<(String, usize)>>,
    }

    impl Rejecting {
        fn for_name(name: &'static str) -> Self {
            Self {
                unknown: Some(name),
                ..Self::default()
            }
        }

        fn for_arity(name: &'static str, count: usize) -> Self {
            Self {
                bad_arity: Some((name, count)),
                ..Self::default()
            }
        }
    }

    impl Host for Rejecting {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(crate::parser::desc::NodeDesc, Vec<Token>), Error> {
            unreachable!("Rejecting compiles no tags")
        }

        fn check_filter(&self, name: &str, arg_count: usize) -> Result<Option<NodeRef>, Error> {
            self.seen.borrow_mut().push((name.to_string(), arg_count));
            if self.unknown == Some(name) {
                return Err(Error::host(format!("Invalid filter: '{name}'")));
            }
            if let Some((bad, count)) = self.bad_arity {
                if bad == name && count == arg_count {
                    return Err(Error::host(format!(
                        "{name} requires 1 arguments, {} provided",
                        arg_count + 1,
                    )));
                }
            }
            // No handle: this host is a filter *rejector*. A real host returns the
            // function it looked up — see `Host::check_filter`.
            Ok(None)
        }

        fn alloc_slot(&self) -> usize {
            // No tags are compiled here, so no descriptor names a slot. Returning 0
            // keeps this host `Cell`-free, which lets `&Rejecting::default()` be
            // passed where a `'static` host is wanted.
            0
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            // No test here compiles a `{% if %}`, so there is no handle to hand back
            // and `0` is the null one. It does **not** consult `unknown`: in Django
            // this is `TemplateIfParser.create_var`'s `compile_filter`, not
            // `FilterExpression.__init__`'s `find_filter`.
            Ok(NodeRef(0))
        }
    }

    /// A host that hands back a handle, and nothing else.
    ///
    /// The counterpart of [`Rejecting`]: one asks what the host *refused*, this what
    /// it *bound*. `check_filter`'s return value is the binding itself (`base.py:773`)
    /// — what renders — so a handle a host returns must arrive on the descriptor.
    struct Binding;

    impl Host for Binding {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(crate::parser::desc::NodeDesc, Vec<Token>), Error> {
            unreachable!("Binding compiles no tags")
        }

        fn check_filter(&self, _name: &str, _arg_count: usize) -> Result<Option<NodeRef>, Error> {
            Ok(Some(NodeRef(7)))
        }

        fn alloc_slot(&self) -> usize {
            0
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(0))
        }
    }

    #[test]
    fn the_handle_the_host_bound_is_what_the_descriptor_carries() {
        let bound = parse_filter_expression("x|upper", &Binding).expect("accepted");
        assert_eq!(bound.filters[0].handle, Some(NodeRef(7)));
        assert_eq!(
            bound.filters[0].name, "upper",
            "the name is still carried: it is what Django's messages say"
        );

        // A permissive host carries no handle rather than a fabricated one, which
        // tells the shell to look the name up instead.
        let unbound = ok("x|upper");
        assert_eq!(unbound.filters[0].handle, None);
        assert_eq!(unbound.filters[0].name, "upper");
    }

    /// Parse a token that must succeed.
    fn ok(raw: &str) -> FilterExprDesc {
        parse_filter_expression(raw, &Rejecting::default())
            .unwrap_or_else(|error| panic!("{raw:?} failed: {error:?}"))
    }

    /// The message of the failure `raw` must produce.
    fn failure(raw: &str) -> String {
        failure_with(raw, &Rejecting::default())
    }

    fn failure_with(raw: &str, host: &Rejecting) -> String {
        parse_filter_expression(raw, host)
            .err()
            .unwrap_or_else(|| panic!("{raw:?} unexpectedly parsed as {raw}"))
            .message()
    }

    /// The `Variable` inside a `FilterVar`, for the rows that carry one.
    fn variable(value: &AttrValue) -> &VariableDesc {
        match value {
            AttrValue::Variable(desc) => desc,
            other => panic!("expected a Variable, got {other:?}"),
        }
    }

    #[test]
    fn a_plain_variable_is_a_lookup_and_a_quoted_one_is_not() {
        let plain = ok("x");
        assert!(
            plain.var.0,
            "a bare name is a Variable, resolved per render"
        );
        assert!(plain.filters.is_empty());
        assert_eq!(plain.token, "x");

        let quoted = ok("\"x\"");
        assert!(
            !quoted.var.0,
            "a constant is resolved at materialisation, so it is not a lookup"
        );
        // The literal lives on the `Variable`, not the `FilterVar`: the materialiser
        // calls `resolve({})` on it, so `_("…")` becomes `gettext_lazy`'s proxy
        // rather than a string. See `FilterVar`.
        assert_eq!(
            variable(&quoted.var.1).literal,
            Some(AttrValue::SafeStr("x".to_string())),
        );
        assert_eq!(variable(&quoted.var.1).lookups, None);
        assert_eq!(variable(&quoted.var.1).var, "\"x\"");
    }

    #[test]
    fn a_translated_constant_keeps_its_translate_flag_on_the_variable() {
        // Why the constant branch carries a `Variable`: `_("x")` must materialise
        // into `gettext_lazy(mark_safe("x"))`, which Rust cannot build.
        let translated = ok("_(\"x\")");
        assert!(!translated.var.0);
        let desc = variable(&translated.var.1);
        assert!(desc.translate, "the marker survives to the Variable");
        assert_eq!(desc.literal, Some(AttrValue::SafeStr("x".to_string())));
        assert_eq!(desc.var, "_(\"x\")", "and `var` keeps the raw token");
    }

    #[test]
    fn filter_arguments_keep_the_lookup_flag_and_are_variables_both_ways() {
        let parsed = ok("x|default:\"d\"|join:y");
        assert_eq!(parsed.filters.len(), 2);

        assert_eq!(parsed.filters[0].name, "default");
        assert_eq!(parsed.filters[0].args.len(), 1);
        assert!(
            !parsed.filters[0].args[0].0,
            "a quoted argument is a constant, so it is resolved at materialisation"
        );
        assert_eq!(
            variable(&parsed.filters[0].args[0].1).literal,
            Some(AttrValue::SafeStr("d".to_string())),
        );

        assert_eq!(parsed.filters[1].name, "join");
        assert!(parsed.filters[1].args[0].0, "a bare name is a lookup");
        assert_eq!(
            variable(&parsed.filters[1].args[0].1).lookups,
            Some(vec!["y".to_string()])
        );
    }

    #[test]
    fn a_filter_with_no_argument_has_an_empty_argument_list() {
        // Not `[None]` and not a placeholder: Django appends nothing, and
        // `args_check`'s `plen = len(provided) + 1` counts that nothing.
        let parsed = ok("x|upper");
        assert!(parsed.filters[0].args.is_empty());
    }

    #[test]
    fn the_three_error_messages_are_djangos() {
        assert_eq!(
            failure("x|upper!!"),
            "Could not parse the remainder: '!!' from 'x|upper!!'",
        );
        assert_eq!(
            failure("|upper"),
            "Could not find variable at start of |upper.",
        );
        assert_eq!(
            failure("x||upper"),
            "Could not parse some characters: x||||upper",
        );
        // The gap error's three fields are the pieces around the *new* match, so a
        // skip in the middle is reported as well as one at the start.
        assert_eq!(
            failure("x|upper y"),
            "Could not parse the remainder: ' y' from 'x|upper y'",
        );
        assert_eq!(
            failure("x|upper :y"),
            "Could not parse the remainder: ' :y' from 'x|upper :y'",
        );
    }

    #[test]
    fn an_empty_token_is_valid_and_has_no_variable() {
        // `FilterExpression("")` yields no match, so the loop never runs and
        // `upto == len(token)` already; Django leaves `self.var = None`. Only
        // reachable directly — `Parser.parse` rejects an empty `{{ }}`.
        let parsed = ok("");
        assert_eq!(parsed.var, (false, AttrValue::None));
        assert!(parsed.filters.is_empty());
    }

    #[test]
    fn a_token_that_cannot_start_a_variable_is_rejected_before_the_host_is_asked() {
        // The host is consulted per *filter*, and this token never reaches one: the
        // first match is the filter alternative, which has no `var` group.
        let host = Rejecting::for_name("upper");
        assert_eq!(
            failure_with("|upper", &host),
            "Could not find variable at start of |upper.",
        );
        assert!(
            host.seen.borrow().is_empty(),
            "the host must not be asked: {:?}",
            host.seen.borrow(),
        );
    }

    #[test]
    fn the_whitespace_around_a_filter_separator_is_pythons_not_rusts() {
        // Python's `\s` is `str.isspace()`, which includes U+001C-U+001F; the
        // `regex` crate's is `\p{White_Space}`, which does not. Measured on the
        // pinned tree: Django accepts all four positions.
        for raw in [
            "x\x1c|upper",
            "x| \x1dupper",
            "x\x1e|\x1fupper",
            "x | upper",
            "x\t|\tupper",
        ] {
            let parsed = ok(raw);
            assert_eq!(parsed.filters[0].name, "upper", "for {raw:?}");
        }
        // And the other direction, so the class is not "everything": U+200B is
        // whitespace in neither, so the scan cannot reach the `|` from where the
        // variable part ended and the failure is the *gap*. Measured, Django's
        // message is `x|\u{200b}||upper`.
        assert_eq!(
            failure("x\u{200b}|upper"),
            "Could not parse some characters: x|\u{200b}||upper",
        );
    }

    #[test]
    fn a_filter_name_is_pythons_word_class_in_the_standalone_position() {
        // `²` is `No`, so Python's `\w` takes it and the `regex` crate's does not: a
        // bare `\w` splits one character early and reports a remainder where Django
        // reports a filter name.
        let parsed = ok("x|up\u{00b2}er");
        assert_eq!(parsed.filters[0].name, "up\u{00b2}er");

        // `á` built from `a` + U+0301 (`Mn`) is the opposite divergence: a Rust `\w`
        // takes the combining mark and Python's does not. Measured on the pinned tree,
        // the *split* is the same on both sides — the name is `a`, U+0301 is left
        // over — and only the outcome differs, so this row asserts the split through
        // the pattern's own groups.
        let captures = filter_re()
            .captures_iter("x|a\u{0301}")
            .nth(1)
            .expect("two matches");
        assert_eq!(captures.name("filter_name").unwrap().as_str(), "a");
        assert_eq!(captures.get(0).unwrap().end(), 3, "the mark is left over");
        assert_eq!(
            failure("x|a\u{0301}"),
            "Could not parse the remainder: '\u{0301}' from 'x|a\u{0301}'",
        );
    }

    #[test]
    fn the_variable_part_is_pythons_word_class_in_the_class_position() {
        // The other spelling of `\w`, and the one that fails silently if the two are
        // swapped: `²` must be a *variable* here, and the combining mark must split
        // the token.
        assert_eq!(
            variable(&ok("\u{00b2}").var.1).lookups,
            Some(vec!["\u{00b2}".to_string()])
        );
        assert_eq!(
            failure("a\u{0301}"),
            "Could not parse the remainder: '\u{0301}' from 'a\u{0301}'",
        );
    }

    #[test]
    fn the_host_is_asked_once_per_filter_with_djangos_argument_count() {
        let host = Rejecting::default();
        ok_with("x|default:\"d\"|join:y", &host);
        assert_eq!(
            *host.seen.borrow(),
            vec![("default".to_string(), 1), ("join".to_string(), 1)],
        );

        let host = Rejecting::default();
        ok_with("x|upper", &host);
        assert_eq!(*host.seen.borrow(), vec![("upper".to_string(), 0)]);

        // The variable part is not a filter and must not be reported as one.
        let host = Rejecting::default();
        ok_with("x", &host);
        assert!(host.seen.borrow().is_empty());
    }

    fn ok_with(raw: &str, host: &Rejecting) -> FilterExprDesc {
        parse_filter_expression(raw, host)
            .unwrap_or_else(|error| panic!("{raw:?} failed: {error:?}"))
    }

    #[test]
    fn a_rejected_filter_name_preempts_the_next_iterations_gap_error() {
        // Measured, and the reason the seam cannot be deferred: the filter error
        // is raised inside iteration 2, before iteration 3's gap check.
        let host = Rejecting::for_name("does_not_exist");
        assert_eq!(
            failure_with("x|does_not_exist||upper", &host),
            "Invalid filter: 'does_not_exist'",
        );
        // And the converse, in the same loop: iteration 2's *gap* pre-empts
        // iteration 2's filter, because `upto != start` is tested first.
        assert_eq!(
            failure_with("x||does_not_exist", &host),
            "Could not parse some characters: x||||does_not_exist",
        );
    }

    #[test]
    fn a_rejected_arity_preempts_the_remainder_check() {
        // The remainder check is *after* the loop (`base.py:775`) and `args_check`
        // is inside it, so an arity error wins over a trailing unmatched character.
        // Measured.
        let host = Rejecting::for_arity("upper", 1);
        assert_eq!(
            failure_with("x|upper:5||add", &host),
            "upper requires 1 arguments, 2 provided",
        );
        assert_eq!(
            failure_with("x|upper:5|does_not_exist", &host),
            "upper requires 1 arguments, 2 provided",
        );
        // The trailing unmatched character is only reached when nothing in the loop
        // fails first. Measured.
        assert_eq!(
            failure_with("x|upper:5 ", &host),
            "upper requires 1 arguments, 2 provided",
        );
        assert_eq!(
            failure_with("x|upper::y", &Rejecting::default()),
            "Could not parse the remainder: '::y' from 'x|upper::y'",
        );
    }

    #[test]
    fn the_argument_variable_is_built_before_the_filter_is_checked() {
        // Django builds `args` at `base.py:766-770` and calls `find_filter` at
        // `base.py:771`, so a bad argument name is reported by `Variable`, not by the
        // filter lookup: asking the host first reports `Invalid filter` for the first
        // row. Both rows measured on the pinned tree.
        let host = Rejecting::for_name("does_not_exist");
        assert_eq!(
            failure_with("x|does_not_exist:_bad", &host),
            "Variables and attributes may not begin with underscores: '_bad'",
        );
        assert!(
            host.seen.borrow().is_empty(),
            "the filter must not be checked before the argument parses",
        );
        assert_eq!(
            failure_with("x|does_not_exist:-5", &host),
            "Invalid filter: 'does_not_exist'",
        );
    }

    #[test]
    fn the_pattern_compiles_and_matches_the_whole_token() {
        // A panic inside `Regex::new` in the `OnceLock` initialiser would only fire on
        // the first `{{ … }}` in a template, so the compile is reached here on purpose.
        // The named groups are read back by name, so a pattern that lost one fails at
        // `captures.name` rather than silently treating every match as the variable
        // part.
        let captures = filter_re()
            .captures_iter("x|upper:y")
            .nth(1)
            .expect("a well-formed token matches twice");
        assert_eq!(captures.name("filter_name").unwrap().as_str(), "upper");
        assert_eq!(captures.name("var_arg").unwrap().as_str(), "y");
        assert!(
            captures.name("constant").is_none(),
            "only the variable-part alternative can match at position 0",
        );
        assert_eq!(
            filter_re()
                .captures("x|upper:y")
                .expect("matches")
                .name("var")
                .unwrap()
                .as_str(),
            "x",
        );
    }
}
