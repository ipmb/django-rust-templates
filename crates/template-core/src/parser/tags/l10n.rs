//! `{% localize %}` — `django/templatetags/l10n.py` @ 6.0.9.
//!
//! # One tag, and the two filters beside it are not here
//!
//! The file registers three names: one tag (`localize`) and two filters
//! (`localize`, `unlocalize`, `l10n.py:8,16`). Only the tag is compiled here;
//! the two filter *bodies* are M12 Task 7's Rust port, and this task transcribes
//! their Python instead. The division is the milestone's own — the five files
//! are interposed whole, and no version of interposing `django.templatetags.l10n`
//! leaves its filters Django's.
//!
//! # The render is Python's, and that is the coupling test rather than a gap
//!
//! `LocalizeNode.render` flips `context.use_l10n`, renders the body, and
//! restores it (`l10n.py:31-36`). That is our transcription's Python, reached
//! through the node the materialiser builds from this descriptor — *not* a Rust
//! body. Only the **compile** function below is Rust's. §6's framework-coupling
//! test is why the two halves can differ: what M12 ports is the parse, and the
//! render delegates to a subsystem (`django.utils.formats`, via `context`
//! machinery) the milestone does not port.
//!
//! # Three accepted forms, and the fourth is the one that matters
//!
//! `{% localize %}` accepts `on`, `off`, and *nothing*:
//!
//! ```text
//! {% localize %}x{% endlocalize %}      → use_l10n = True
//! {% localize on %}x{% endlocalize %}   → use_l10n = True
//! {% localize off %}x{% endlocalize %}  → use_l10n = False
//! ```
//!
//! "Accepts nothing" is one keystroke from "accepts anything", so the forms
//! Django **rejects** are pinned too, measured on the pinned tree rather than
//! read off the prose:
//!
//! ```text
//! {% localize maybe %}x{% endlocalize %}   → 'localize' argument should be 'on' or 'off'
//! {% localize on off %}x{% endlocalize %}  → 'localize' argument should be 'on' or 'off'
//! {% localize On %}x{% endlocalize %}      → 'localize' argument should be 'on' or 'off'
//! ```
//!
//! The condition is `len(bits) > 2 or bits[1] not in ("on", "off")`
//! (`l10n.py:54`), so a second bit is an error even when the first is valid —
//! `on off` is not "read the first and ignore the rest". `On` is not `on`:
//! the comparison is exact, with no case folding.
//!
//! # The argument is checked before the body is parsed
//!
//! As in `tz.rs`, and observable in both directions: the whole `if`/`elif`/`else`
//! over `use_l10n` (`l10n.py:52-57`) runs **before** `parser.parse(("endlocalize",))`
//! (`l10n.py:58`). So `{% localize maybe %}x` reports the *argument* error, not
//! `Unclosed tag`, and the control — a valid argument with a missing terminator —
//! reports `Unclosed tag`. A port that parsed the body first would report the
//! unclosed block for the first template, and every Rust test below that checked
//! only the valid forms would still pass.
//!
//! # `%r` of the tag name, and why it goes through the host
//!
//! `"%r argument should be 'on' or 'off'" % bits[0]` (`l10n.py:55`) interpolates
//! the tag name with `%r`, so `bits[0]` goes through [`Parser::py_repr`] — the
//! host's Python `repr` — rather than [`py_repr_str`](crate::parser::tags::py_repr_str),
//! whose ASCII-only assertion exists for exactly this reason (see its doc). In
//! practice `bits[0]` is always the ASCII literal `localize`; the indirection is
//! the same one `tz.rs`'s `localtime` makes and is kept for the same reason.
//!
//! [`Parser::py_repr`]: crate::parser::Parser::py_repr

use crate::parser::desc::{AttrValue, NodeDesc};
use crate::parser::error::Error;
use crate::parser::tags::split_contents;
use crate::parser::Parser;
use crate::token::Token;

/// `localize_tag` (`l10n.py:40-60`) — `{% localize [on|off] %}…{% endlocalize %}`.
///
/// `bits` has one, two or more entries and each means something different:
/// nothing is `use_l10n = True`, a single `on`/`off` is the boolean it names, and
/// anything else (a second bit, or a first bit that is not `on`/`off`) is the
/// syntax error below. `bits[0]` is always present — `Parser::parse` rejects an
/// empty block tag before it dispatches — so the index is safe.
pub fn localize(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = split_contents(token)?;

    let use_l10n = if bits.len() == 1 {
        // `if len(bits) == 1: use_l10n = True` (`l10n.py:52-53`).
        true
    } else if bits.len() > 2 || (bits[1] != "on" && bits[1] != "off") {
        // `"%r argument should be 'on' or 'off'" % bits[0]` (`l10n.py:54-55`) —
        // `%r`, so the tag name goes through the host's `repr`.
        return Err(Error::syntax(format!(
            "{} argument should be 'on' or 'off'",
            parser.py_repr(&bits[0]),
        )));
    } else {
        // `use_l10n = bits[1] == "on"` (`l10n.py:57`).
        bits[1] == "on"
    };

    // The body is parsed *after* the argument check — see the module doc.
    let nodelist = parser.parse(&["endlocalize"])?;
    parser.delete_first_token();
    Ok(
        NodeDesc::with_nodelist("LocalizeNode", "nodelist", nodelist)
            .attr("use_l10n", AttrValue::Bool(use_l10n)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;
    use crate::parser::tags::support::{attr, compile_loaded, failure_loaded};

    #[test]
    fn localize_with_no_argument_is_on() {
        let node = compile_loaded("{% localize %}x{% endlocalize %}");
        assert_eq!(node.class_name, "LocalizeNode");
        assert_eq!(node.nodelists.len(), 1);
        assert_eq!(node.nodelists[0].0, "nodelist", "`LocalizeNode.__init__`");
        assert_eq!(
            attr(&node, "use_l10n"),
            &AttrValue::Bool(true),
            "no argument is `use_l10n = True`, and it stays a bool on the wire",
        );
    }

    #[test]
    fn localize_reads_on_and_off_as_booleans() {
        for (argument, expected) in [("on", true), ("off", false)] {
            let source = format!("{{% localize {argument} %}}x{{% endlocalize %}}");
            assert_eq!(
                attr(&compile_loaded(&source), "use_l10n"),
                &AttrValue::Bool(expected),
                "{source}",
            );
        }
    }

    #[test]
    fn localize_rejects_a_first_bit_that_is_not_on_or_off() {
        // `maybe` is neither; `On` is the same word in a different case, and the
        // comparison is exact — measured on the pinned tree.
        for argument in ["maybe", "On", "OFF"] {
            let source = format!("{{% localize {argument} %}}x{{% endlocalize %}}");
            assert_eq!(
                failure_loaded(&source).kind,
                ErrorKind::Syntax("'localize' argument should be 'on' or 'off'".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn localize_rejects_a_second_bit_even_when_the_first_is_valid() {
        // `len(bits) > 2` OR a bad `bits[1]` — so `on off` is an error, not
        // "read the first and ignore the rest". The fourth form the plan warns
        // is most easily missed: "accepts nothing" is not "accepts anything".
        for argument in ["on off", "off on", "on on"] {
            let source = format!("{{% localize {argument} %}}x{{% endlocalize %}}");
            assert_eq!(
                failure_loaded(&source).kind,
                ErrorKind::Syntax("'localize' argument should be 'on' or 'off'".to_string()),
                "{source}",
            );
        }
    }

    #[test]
    fn localize_checks_its_argument_before_it_parses_the_body() {
        // The unclosed block is *not* what Django reports here, because the
        // argument check runs first (`l10n.py:52-57`). Measured. A port that
        // parsed the body first would report `Unclosed tag` instead.
        assert_eq!(
            failure_loaded("{% localize maybe %}x").kind,
            ErrorKind::Syntax("'localize' argument should be 'on' or 'off'".to_string()),
        );
        // And the control: with a *valid* argument the missing terminator is
        // what is reported, so the row above is measuring the order and not a
        // blanket refusal.
        assert_eq!(
            failure_loaded("{% localize %}x").kind,
            ErrorKind::Syntax(
                "Unclosed tag on line 1: 'localize'. Looking for one of: endlocalize.".to_string(),
            ),
        );
    }
}
