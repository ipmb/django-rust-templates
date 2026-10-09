//! `filter_re` and `FilterExpression.__init__` — `base.py:701-775`.
//!
//! Rust parses the token into a descriptor holding the variable part and an
//! ordered list of `(filter_name, args)`; the **host** validates each filter at
//! the point in the loop where Django does, and the shell resolves every name
//! again at materialisation time. Names rather than handles is not a shortcut: a
//! `FilterExpression` holds a Python function and `args_check` inspects its
//! signature, so no Rust type can carry one.
//!
//! # The pattern
//!
//! Assembled the way Django assembles `filter_raw_string` (`base.py:701-719`)
//! from `constant_string` (`:680-699`), with two substitutions that are
//! **behavioural rather than cosmetic**:
//!
//! - both `\w` occurrences go through `pyregex`, and in *different spellings* —
//!   `var_chars` is spliced inside a class (`[%(var_chars)s]+`), `filter_name`
//!   is standalone. Getting them the wrong way round is asymmetric: the contents
//!   form used standalone rejects `upper` loudly, while the bracketed form
//!   spliced into a class **parses and works by accident** under the `regex`
//!   crate's nested character classes, so nothing would report it.
//! - `\s` becomes `[WS_CLASS]`, because Python's `\s` is `str.isspace()`, which
//!   includes U+001C-U+001F (category `Cc`) and the `regex` crate's `\s` is
//!   `\p{White_Space}`, which does not. Measured: Django accepts `x\x1c|upper`,
//!   so a port that used `\s` rejects a template Django renders.
//!
//! The pattern is written in the **assembled** form, without `(?x)`. Django's
//! source leans on `re.VERBOSE`, and the four `\s*` in the filter alternative
//! are the only whitespace in it that is pattern rather than layout — so a
//! layout-preserving port has to keep them straight by hand. Building the
//! assembled string makes that impossible to get wrong, and the pieces below are
//! one per line of `filter_raw_string`.
//!
//! # The loop, and why it is a loop rather than a split
//!
//! Two anchors and one unanchored alternative: the `constant` and `var`
//! alternatives carry `^`, the filter alternative does not, so `find_iter`
//! matches the variable part at position 0 and each filter *anywhere* after it.
//! That is what makes the gap check meaningful — a skipped character shows up as
//! a match that does not start where the last one ended, not as a failed match.
//!
//! `upto` advances on **every** iteration including the first, and the gap error
//! compares `upto` to `match.start()`.
//!
//! # One place the port is deliberately not Django's shape
//!
//! Django tests `if var_obj is None` (`base.py:747`) to decide whether a match is
//! the variable part or a filter — a *value* used as a flag. If the variable part
//! ever resolved to `None`, the second match would take the first-match branch,
//! find neither a `constant` nor a `var` group, and raise `Could not find
//! variable at start of …` for a token Django would otherwise accept. The
//! branch is unreachable: `Variable.resolve` reaches `_resolve_lookup`, the only
//! raiser of `VariableDoesNotExist`, only `if self.lookups is not None`
//! (`base.py:926-928`), and every shape the `constant` group can match — a quoted
//! string or the `_("…")` marker — gives `lookups is None`. Here the flag is an
//! `Option` over the descriptor instead, which agrees with Django on every input
//! and cannot be a wrong value.

use std::sync::OnceLock;

use regex::Regex;

use crate::parser::desc::{AttrValue, FilterCall, FilterExprDesc, FilterVar};
use crate::parser::error::Error;
use crate::parser::host::Host;
use crate::parser::variable::parse_variable;
use crate::pyregex::{PYTHON_WORD_IN_CLASS, PYTHON_WORD_STANDALONE};
use crate::pyunicode::WS_CLASS;

/// The double-quoted string alternative, verbatim from `constant_string`
/// (`base.py:695`).
const STR_DQ: &str = r#""[^"\\]*(?:\\.[^"\\]*)*""#;

/// The single-quoted string alternative (`base.py:696`).
const STR_SQ: &str = r"'[^'\\]*(?:\\.[^'\\]*)*'";

/// `constant_string`: a quoted string, or one wrapped in Django's `_("…")`
/// marker (`base.py:688-699`).
///
/// Built rather than written out because `filter_raw_string` splices it **twice**
/// — once as the `constant` group and once as `constant_arg` — and the two must
/// stay identical: a divergence between them is a token Django accepts as a
/// variable part and rejects as an argument.
fn constant_string() -> String {
    // `i18n_open` is `re.escape("_(")` and `i18n_close` is `re.escape(")")`
    // (`base.py:690-698`), so the marker's *closing* paren is a bare `\)` with no
    // underscore. `_\)` is the spelling that reads naturally and it is wrong:
    // the alternative then fails on `_("x")`, the `var` group matches the leading
    // `_` instead, and the token is reported as an underscore rule violation.
    // Written as separate pieces so the asymmetry is visible rather than fixed by
    // eye. Named arguments because `format!` cannot capture from the surrounding
    // scope when its format string comes from a macro.
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
/// `filter_raw_string`, assembled (`base.py:701-719`).
///
/// `(?::` is Django's `(?:%(arg_sep)s` with `arg_sep` spliced in: it is an
/// argument separator inside a non-capturing group, not a typo.
fn filter_re_source() -> String {
    let constant = constant_string();
    // `var_chars = r"\w\.\+-"` spliced into `[%(var_chars)s]+` (`base.py:714`).
    // Inside a class, so the *contents* form of Python's `\w`.
    let var_chars = format!(r"[{PYTHON_WORD_IN_CLASS}\.\+-]+");
    // `(?P<filter_name>\w+)` (`base.py:705`): standalone, so the bracketed form.
    let filter_name = format!("{PYTHON_WORD_STANDALONE}+");
    // Named arguments rather than inline captures: `format!` cannot capture from
    // the surrounding scope when its format string comes from a macro.
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

/// The compiled pattern, built once.
///
/// A `OnceLock` rather than a `lazy_static`-style macro: the crate has one
/// dependency and this needs no other.
fn filter_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&filter_re_source()).expect("filter_re's source is fixed and must compile")
    })
}

/// `FilterExpression.__init__`, with the filter checks routed to `host`.
///
/// Takes the raw token rather than a `Token`: the caller has already decided this
/// is a `{{ … }}`, and Django's constructor does too.
///
/// `host` is where `find_filter` and `args_check` live. [`Host::check_filter`]
/// documents why the check is here rather than in the shell and why it happens
/// after the arguments are built.
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
            // Django's `%s|%s|%s` (`base.py:743-746`): everything before the
            // last match, the skipped run itself, and everything from the new
            // match on. `Match::start`/`end` are *byte* offsets and Python's are
            // code-point offsets, but both are always at a character boundary
            // here, so the three substrings are identical.
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
                // A filter alternative at position 0, so the token starts with
                // `|`. `match["var"]` is `None` for every alternative but the
                // second, and `is None` rather than falsiness is what makes
                // `|upper` an error here rather than a silent empty variable.
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

            // **The arguments before the check.** Django builds `args` at
            // `base.py:757-761` and only then calls `find_filter` at `:771`, and
            // the order is observable: `x|does_not_exist:_bad` reports the
            // underscore error while `x|does_not_exist:-5` reports the filter
            // error.
            let mut args: Vec<FilterVar> = Vec::new();
            if let Some(constant) = captures.name("constant_arg") {
                args.push((false, variable_of(constant.as_str())?));
            } else if let Some(name) = captures.name("var_arg") {
                args.push((true, variable_of(name.as_str())?));
            }

            // The handle comes back from the same call, at the same point:
            // Django binds `filter_func` here (`base.py:771`) and it is what
            // renders, so the name is never looked up again. `None` is a host
            // with no registry — see `Host::check_filter`.
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
        // `FilterExpression("")` is **valid** and leaves `self.var = None`:
        // no match, so the loop never runs, and `upto == len(token)` already.
        // `Parser.parse` rejects an empty `{{ }}` before reaching here, so this
        // is only reachable through the direct entry points, and it is Django's
        // own outcome rather than an invariant violation.
        var: var.unwrap_or((false, AttrValue::None)),
        filters,
    })
}

/// A matched group as the `Variable` it names.
///
/// Both branches are a `Variable`, and the caller's flag — not this function —
/// says whether the materialiser resolves it now or keeps it. See
/// [`FilterVar`] for why the constant branch cannot carry a resolved value.
fn variable_of(raw: &str) -> Result<AttrValue, Error> {
    Ok(AttrValue::Variable(Box::new(parse_variable(raw)?)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::desc::{NodeRef, VariableDesc};
    use crate::token::Token;

    /// A host that rejects exactly what it is told to, and accepts the rest.
    ///
    /// The two rejections are Django's messages verbatim, because the point of
    /// the tests below is not that a filter *can* fail but **where in the loop**
    /// it fails. `Error::host` rather than `Error::syntax` because these are the
    /// host's own errors, and it is the variant the FFI adapter builds out of a
    /// Python exception.
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
        ) -> Result<crate::parser::desc::NodeDesc, Error> {
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
            // No handle: this host is a filter *rejector*, and `alloc_slot`
            // reserves nothing for it. A real host returns the function it
            // looked up — see `Host::check_filter`.
            Ok(None)
        }

        fn alloc_slot(&self) -> usize {
            // This host compiles no tags, so no descriptor it builds ever names a
            // slot. Returning 0 keeps it `Cell`-free, which is what lets
            // `&Rejecting::default()` be passed where a `'static` host is wanted.
            0
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            // Same reason as `alloc_slot`, and the same honesty: this host is
            // here to reject *filter names* through `check_filter`, which the
            // tests below observe through `seen`. No test compiles a `{% if %}`,
            // so there is no handle to hand back and `0` is the null one.
            //
            // It does **not** consult `unknown`, because the two seams are not
            // the same call in Django either: `check_filter` is
            // `FilterExpression.__init__`'s `find_filter`, and this is
            // `TemplateIfParser.create_var`'s `compile_filter`, which builds a
            // whole `FilterExpression` and fails with its error rather than the
            // name alone. Making this one reject too would put a filter error on
            // a path no test here walks.
            Ok(NodeRef(0))
        }
    }

    /// A host that hands back a handle, and nothing else.
    ///
    /// The counterpart of [`Rejecting`]: one asks what the host *refused*, this
    /// asks what it *bound*. `check_filter`'s return value is the binding itself
    /// — `filters.append((filter_func, args))` (`base.py:773`) is what renders —
    /// so a host that returns a handle must have it arrive on the descriptor.
    struct Binding;

    impl Host for Binding {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
        ) -> Result<crate::parser::desc::NodeDesc, Error> {
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

        // And the permissive host carries no handle rather than a fabricated one,
        // which is what tells the shell to look the name up instead.
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
        // The literal lives on the `Variable`, not on the `FilterVar`: the
        // materialiser calls `resolve({})` on it so that `_("…")` becomes
        // `gettext_lazy`'s proxy rather than a string. See `FilterVar`.
        assert_eq!(
            variable(&quoted.var.1).literal,
            Some(AttrValue::SafeStr("x".to_string())),
        );
        assert_eq!(variable(&quoted.var.1).lookups, None);
        assert_eq!(variable(&quoted.var.1).var, "\"x\"");
    }

    #[test]
    fn a_translated_constant_keeps_its_translate_flag_on_the_variable() {
        // The whole reason the constant branch carries a `Variable`: `_("x")`
        // must materialise into `gettext_lazy(mark_safe("x"))`, and Rust has no
        // way to build that. The flag is what the materialiser's
        // `Variable.resolve({})` reads.
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
        // The gap error's three fields are the pieces around the *new* match, so
        // a skip in the middle is reported as well as one at the start.
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
        // `FilterExpression("")`: `filter_re` yields no match, the loop never
        // runs, and `upto == len(token)` is already true. Django leaves
        // `self.var = None` and `is_var = False`. Unreachable from a template —
        // `Parser.parse` rejects an empty `{{ }}` — but it is Django's own
        // outcome rather than an error, so it is not an invariant violation.
        let parsed = ok("");
        assert_eq!(parsed.var, (false, AttrValue::None));
        assert!(parsed.filters.is_empty());
    }

    #[test]
    fn a_token_that_cannot_start_a_variable_is_rejected_before_the_host_is_asked() {
        // The host is consulted per *filter*, and this token never reaches one:
        // the first match is the filter alternative, which has no `var` group.
        // A host that was asked anyway would report a filter error here.
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
        // And the other direction, so the class is not simply "everything":
        // U+200B is neither Python's whitespace nor Rust's, so the scan cannot
        // reach the `|` from where the variable part ended — and the failure is
        // the *gap*, not the remainder, because the filter alternative matches
        // at the `|` and the two positions disagree. Measured: Django's message
        // is `x|\u{200b}||upper`, the middle field being the skipped character.
        assert_eq!(
            failure("x\u{200b}|upper"),
            "Could not parse some characters: x|\u{200b}||upper",
        );
    }

    #[test]
    fn a_filter_name_is_pythons_word_class_in_the_standalone_position() {
        // `²` is `No`, so Python's `\w` takes it and the Rust `regex` crate's
        // does not: a port using a bare `\w` splits this token one character
        // early and reports a remainder where Django reports a filter name.
        let parsed = ok("x|up\u{00b2}er");
        assert_eq!(parsed.filters[0].name, "up\u{00b2}er");

        // `á` built from `a` + U+0301 (`Mn`) is the opposite divergence: a Rust
        // `\w` takes the combining mark and Python's does not. Measured on the
        // pinned tree, the *split* is the same on both sides — the name is `a`
        // and U+0301 is left over — and it is the outcome that differs, because
        // Django's `find_filter` rejects `a` while the loop here has not
        // finished. So this row asserts the split through the pattern's own
        // groups and Django's `Invalid filter: 'a'` is not reproducible without
        // a host.
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
        // The other spelling of `\w`, and the one that fails silently if the two
        // are swapped: `²` must be a *variable* here, and the combining mark
        // must split the token.
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
        // The remainder check is *after* the loop (`base.py:774`), and
        // `args_check` is inside it, so an arity error wins over a trailing
        // character the loop could not match. Measured.
        let host = Rejecting::for_arity("upper", 1);
        assert_eq!(
            failure_with("x|upper:5||add", &host),
            "upper requires 1 arguments, 2 provided",
        );
        assert_eq!(
            failure_with("x|upper:5|does_not_exist", &host),
            "upper requires 1 arguments, 2 provided",
        );
        // The trailing unmatched character is only reached when nothing in the
        // loop fails first.
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
        // Django builds `args` at `base.py:757-761` and calls `find_filter` at
        // `:771`, so a bad argument name is reported by `Variable`, not by the
        // filter lookup. An implementation that asked the host first — tempting,
        // because the filter name is known earlier and the error path is
        // cheaper — reports `Invalid filter` for the first row. Both rows were
        // measured on the pinned tree, and they differ only in whether the
        // argument parses.
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
        // A `OnceLock` initialiser that panicked inside `Regex::new` would only
        // fail on the first `{{ … }}` in a template, so the compile is reached
        // here on purpose. The two named groups are read back by name, so a
        // pattern that lost one fails at the first `captures.name` rather than
        // silently treating every match as the variable part.
        // The first match is the variable part — the two `^`-anchored
        // alternatives win at position 0 — and the filter is the second.
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
