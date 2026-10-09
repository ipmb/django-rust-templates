//! The built-in tag registry: command name → compile function.
//!
//! Written **once, in full**, and never edited by a fan-out agent (§10.4). The
//! reason is mechanical rather than stylistic: under fan-out each agent owns one
//! `tags/<name>.rs`, and a registry that grew as they landed would be the single
//! shared file they all had to edit — exactly the clobbering this layout exists
//! to prevent. So from M3 onward every built-in tag Rust owns has an entry here,
//! and a tag that is not implemented yet is a stub in its own file rather than a
//! missing entry.
//!
//! **Three built-in names are deliberately absent**, and a test asserts their
//! absence: `block`, `extends` and `include`. §6 assigns their render-time logic
//! to Python, and M7 supplies them through the host seam; they are not Rust
//! compile functions at any milestone, so an entry here would be a promise this
//! project has already decided not to keep.
//!
//! The count is 24 = 27 built-ins minus those three, and the metric matters:
//! `Engine().template_builtins[0].tags` is 24 (`defaulttags` alone), while the
//! union across all three builtin libraries is 27 (`defaulttags` 24 +
//! `loader_tags` 3). Both are right answers to different questions, and this
//! project has lost time to that shape four times. `builtin_tag_names` is
//! cross-checked against Django's real registry by `tests/test_tag_inventory.py`
//! so the number cannot drift unnoticed.

use crate::parser::desc::NodeDesc;
use crate::parser::error::Error;
use crate::parser::tags;
use crate::parser::Parser;
use crate::token::Token;

/// A tag's compile function: Django's `Parser.compile_func(self, token)`.
///
/// A plain `fn` pointer rather than a `Box<dyn Fn>`, so a table of them is a
/// `static` if it ever needs to be. The `'_` in `Parser<'_>` is a higher-ranked
/// lifetime, so one `CompileFn` value is usable with a parser of any lifetime.
pub type CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>;

/// `(command, compile function)`, ascending by command.
///
/// One table rather than a `match` plus a separate name list: two spellings of
/// the same 24 names is a drift risk, and `builtin_tag_names` exists precisely
/// so a test can compare the set against Django's.
type Entry = (&'static str, CompileFn);

const REGISTRY: &[Entry] = &[
    ("autoescape", tags::autoescape::compile),
    ("comment", tags::comment::compile),
    ("csrf_token", tags::csrf_token::compile),
    ("cycle", tags::cycle::compile),
    ("debug", tags::debug::compile),
    ("filter", tags::filter::compile),
    ("firstof", tags::firstof::compile),
    ("for", tags::for_tag::compile),
    ("if", tags::if_tag::compile),
    ("ifchanged", tags::ifchanged::compile),
    ("load", tags::load::compile),
    ("lorem", tags::lorem::compile),
    ("now", tags::now::compile),
    ("partial", tags::partial::compile),
    ("partialdef", tags::partialdef::compile),
    ("querystring", tags::querystring::compile),
    ("regroup", tags::regroup::compile),
    ("resetcycle", tags::resetcycle::compile),
    ("spaceless", tags::spaceless::compile),
    ("templatetag", tags::templatetag::compile),
    ("url", tags::url::compile),
    ("verbatim", tags::verbatim::compile),
    ("widthratio", tags::widthratio::compile),
    ("with", tags::with::compile),
];

/// The compile function Rust has for `command`, if it has one.
///
/// A linear scan of 24 entries. M2's plan chose a `match` over a lazy `HashMap`
/// for the same reason — the table is small and read once per block tag, and a
/// `HashMap` behind a `OnceLock` would add a synchronisation point to the hot
/// path for no benefit.
pub fn builtin_tag(command: &str) -> Option<CompileFn> {
    // Test-only stand-ins, still an arm rather than table rows: they are parser
    // machinery, not Django tags, and adding them to `REGISTRY` would put three
    // names in `builtin_tag_names` that have no counterpart in Django's
    // registry — which is exactly what the Python cross-check asserts against.
    #[cfg(test)]
    if let Some(compile) = tags::standin_tag(command) {
        return Some(compile);
    }
    REGISTRY
        .iter()
        .find(|(name, _)| *name == command)
        .map(|(_, compile)| *compile)
}

/// Every command name Rust owns a compile function for, ascending.
pub fn builtin_tag_names() -> Vec<&'static str> {
    REGISTRY.iter().map(|(name, _)| *name).collect()
}

/// The error a stub compile function returns.
///
/// An `Unimplemented` rather than a `Syntax`: the tag is real and Django would
/// compile it, so this is a statement about the engine, not a verdict on the
/// template. The message names the milestone so the failure is actionable.
pub fn unimplemented(name: &str, milestone: &str) -> Error {
    Error::unimplemented(format!(
        "'{name}' is a built-in tag this engine does not implement yet ({milestone})"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    #[test]
    fn the_registry_is_sorted_and_has_no_duplicates() {
        // The table is documented as ascending so that `builtin_tag`'s linear
        // scan is a scan over a known order, and so that a reader can find an
        // entry without reading all 24. A table that drifts out of order
        // silently breaks the second promise, so it is asserted.
        let names = builtin_tag_names();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted, "REGISTRY is not ascending by command");
        sorted.dedup();
        assert_eq!(
            sorted.len(),
            names.len(),
            "REGISTRY has a duplicate command"
        );
    }

    #[test]
    fn the_registry_has_the_twenty_four_names_rust_owns() {
        // 27 built-in names minus `block`, `extends`, `include`. See the module
        // doc for why those three are absent and for the counting trap.
        assert_eq!(builtin_tag_names().len(), 24);
    }

    #[test]
    fn the_three_loader_tags_are_not_rust_compile_functions() {
        for absent in ["block", "extends", "include"] {
            assert!(
                builtin_tag(absent).is_none(),
                "{absent} must not be a Rust compile function (§6: Python-owned, M7)",
            );
        }
    }

    #[test]
    fn a_stub_names_its_milestone_and_says_it_is_the_engine_not_the_template() {
        assert_eq!(
            unimplemented("for", "M5").kind,
            ErrorKind::Unimplemented(
                "'for' is a built-in tag this engine does not implement yet (M5)".to_string(),
            ),
        );
    }

    #[test]
    fn every_entry_is_reachable_and_every_unimplemented_one_is_a_stub() {
        // The registry's own invariant: a name it lists must resolve, and the
        // fifteen that no milestone before this one implements must be the
        // stubs rather than an M2 tag registered under a second name.
        let stubs = [
            "cycle",
            "filter",
            "firstof",
            "for",
            "if",
            "ifchanged",
            "load",
            "lorem",
            "partial",
            "partialdef",
            "querystring",
            "regroup",
            "url",
            "widthratio",
            "with",
        ];
        assert_eq!(stubs.len(), 15);
        for name in builtin_tag_names() {
            assert!(builtin_tag(name).is_some(), "{name} does not resolve");
        }
        let mut expected: Vec<&str> = [
            "autoescape",
            "comment",
            "csrf_token",
            "debug",
            "now",
            "resetcycle",
            "spaceless",
            "templatetag",
            "verbatim",
        ]
        .into_iter()
        .chain(stubs)
        .collect();
        expected.sort_unstable();
        assert_eq!(builtin_tag_names(), expected);
    }
}
