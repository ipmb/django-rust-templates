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
//! absence: `block`, `extends` and `include`. §6 assigns them to Python and M7
//! supplies them through the host seam, so they are not Rust compile functions
//! at any milestone, and an entry here would be a promise this project has
//! already decided not to keep. The mechanical reason — the one to reach for
//! when the next built-in's side has to be decided — is the rule below.
//!
//! **The rule behind that subtraction, stated rather than listed**, because
//! "which built-ins does Rust own" has now been asked four times and each time a
//! different list was a right answer to a different question. This table is the
//! set of tags whose `CompileFn` is a function of **the token and the parser's
//! structural cursor alone** — the cursor being `Parser::parse`, `next_token`
//! and the end-tag names. A tag is subtracted when its compile reads or writes
//! **host-side parser state whose lifetime is the parse, not the node**: a
//! `NodeDesc` is one node's worth of data and cannot carry a value that
//! accumulates across siblings, which is what all three of these are.
//!
//! - `block` writes `parser.__loaded_blocks` (`loader_tags.py:227-232`), a Python
//!   attribute the duplicate-name check is built on, and the check *raises*, so
//!   the accumulated value changes the parse. Note the discrimination is not the
//!   field's type — a `Vec<String>` would hold those names — it is that no
//!   per-node value can carry state that the *second* `{% block %}` needs to see.
//!   `do_block` also recurses through `parser.parse(("endblock",))`.
//! - `extends` reads `parser.origin.template_name` (`:308`) and inspects the
//!   just-parsed nodelist through `get_nodes_by_type` (`:311`) — a query over
//!   *materialised* nodes, which do not exist at compile time.
//! - `include` reaches `parser.compile_filter` and the loader machinery.
//!
//! **A separate bucket, and `load` is its only member — it is in the table for
//! now, and M6 must decide.** `do_load` writes host state too
//! (`parser.add_library` → `self.tags.update`, `base.py:668`), and the effect
//! must be visible to a *later* compile: `Parser::parse` consults
//! `self.host.has_tag(&command)` **first** (`parser/mod.rs:210`), so a tag
//! registered by `{% load %}` reaches Rust only through the host. Under the rule
//! above that is a subtraction, and the table currently keeps it as a stub
//! anyway, because M6 has a real choice the rule does not make for it: subtract
//! it (host tag), or give it a Rust compile function that calls one host seam
//! (`add_library`). `tags/load.rs`'s own doc records the same measurement and
//! the same deferral. Flagged rather than resolved, because M3 must not
//! pre-empt M6.
//!
//! Note what the rule is **not**: it is not "the node's `render` needs the
//! host". Every node is materialised as Django's own class and renders in
//! Python, so that test would subtract all 24 — `NowNode.render` reads
//! `settings.USE_TZ` and `timezone.get_current_timezone()`
//! (`defaulttags.py:404-405`) and `now` is one of M2's nine. It is also not
//! "`__init__` takes arguments Rust cannot build": `BlockNode.__init__(name,
//! nodelist, parent=None)` is descriptor-shaped, and `block` is still
//! subtracted. The discriminator is on the **compile** side, and specifically on
//! the lifetime of the state it touches.
//!
//! **Flagged, not resolved: `partial` and `partialdef` are the rule's next two
//! candidates, and M13 must place them.** Both touch `parser.extra_data`
//! (`defaulttags.py:1251`, `:1279`), the same *kind* of host parse state the rule
//! subtracts, so neither can be compiled from a token alone as written. `partial`
//! is the stronger of the two and it is worth recording what it actually does:
//! `partial_func` constructs `DeferredSubDict(extra_data, "partials")` — a **live
//! view over the parser's dict** — and hands it to `PartialNode`, so the compiled
//! node closes over parse state that outlives the parse. That is not "reads host
//! state at compile"; it is a value a `NodeDesc` cannot represent at all. They
//! stay in this table for now because their stubs make the milestone visible and
//! because M13 has a real choice — widen the seam, or grow the wire an
//! `extra_data` channel — that M3 must not pre-empt. The reviewer's argument
//! distinguished the pair (only `partial`'s *render* re-enters host state); that
//! distinction does not hold under this rule, which acts at compile time, where
//! both are the same case. What is recorded here is the measurement, not a
//! verdict.
//!
//! The count is 24 = 27 built-ins minus those three, and the metric matters:
//! `Engine().template_builtins[0].tags` is 24 (`defaulttags` alone), while the
//! union across all three builtin libraries is 27 (`defaulttags` 24 +
//! `loader_tags` 3). Both are right answers to different questions, and this
//! project has lost time to that shape four times. `builtin_tag_names` is
//! cross-checked against Django's real registry by `tests/test_tag_inventory.py`
//! so the number cannot drift unnoticed — and that cross-check computes the
//! complement from `REGISTERED` minus an explicit set, so a change here is a
//! change in both places at once rather than a silent drift.

use crate::parser::desc::NodeDesc;
use crate::parser::error::{Error, ErrorKind};
use crate::parser::host::NoHost;
use crate::parser::tags;
use crate::parser::Parser;
use crate::token::{Token, TokenType};

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

/// The registered names whose compile function is **still a milestone stub**,
/// ascending.
///
/// Derived from the table by **asking it**, not from a second list: a name is a
/// stub iff compiling it returns [`ErrorKind::Unimplemented`]. Every stub body is
/// exactly `Err(unimplemented(name, milestone))` and reads neither the parser nor
/// the token, so a probe token that no real tag accepts is enough to tell the two
/// apart — a *real* tag answers the same probe with `Syntax`/`Internal`/`Ok`
/// (measured: `autoescape` → `Syntax`, `comment` → `Internal`, `csrf_token` →
/// `Ok`) and never with `Unimplemented`, because `unimplemented` is only ever
/// called from a stub.
///
/// **Why not a list, or an `is_stub` column.** Both are the same defect one layer
/// down: right until they are not, with nothing to fail when they drift from the
/// bodies they describe. This is the set M5's gate is a set-difference against —
/// `builtin_tag_stubs() & M5_TAGS == set()` — so it has to be the engine's own
/// answer. It also makes the milestone *progress* derivable: a tag lands, and its
/// name leaves this list, demanding a corpus row from
/// `test_the_corpus_covers_every_implemented_tag_with_a_success_path`.
///
/// The probe is well-formed and minimal (`{% x %}`), so a panic here would mean
/// the tag panics on ordinary input — already a defect, and not one this
/// function should hide.
pub fn builtin_tag_stubs() -> Vec<&'static str> {
    let probe = Token {
        token_type: TokenType::Block,
        contents: "x".to_string(),
        lineno: 1,
        position: None,
    };
    REGISTRY
        .iter()
        .filter(|(_, compile)| {
            let mut parser = Parser::new(Vec::new(), &NoHost);
            matches!(
                compile(&mut parser, &probe),
                Err(Error {
                    kind: ErrorKind::Unimplemented(_),
                    ..
                })
            )
        })
        .map(|(name, _)| *name)
        .collect()
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
        // Two independent statements of the same set, and the independence is the
        // point: the literal below is a **pin** on the derivation in
        // `builtin_tag_stubs`, so a stub body that stopped returning
        // `Unimplemented` — the one way that function can drift from the entries
        // it describes — reddens here instead of silently shrinking the set M5's
        // gate is a set-difference against. The derivation, not this literal, is
        // what the Python gate consumes; this is the second opinion it is checked
        // against.
        //
        // M5's last task rewrites this literal. Implementing eleven of the
        // fifteen here is exactly what makes them stop being `Unimplemented`, so
        // the pin must shrink to `load`, `partial`, `partialdef` and
        // `querystring` in the same commit that lands the eleventh tag. A pin
        // that did not have to move would not have been a pin.
        //
        // **It moves once per task, not once per milestone**, which is the
        // smaller thing the same argument buys: the fan-out tasks land their
        // tags one group at a time, and a literal left at fifteen until the end
        // would fail every commit in between. Nine of the eleven have landed —
        // `filter`, `firstof`, `for`, `ifchanged`, `lorem`, `regroup`, `url`,
        // `widthratio` and `with` — so those nine are gone from the list.
        let stubs = [
            "cycle",
            "if",
            "load",
            "partial",
            "partialdef",
            "querystring",
        ];
        assert_eq!(
            builtin_tag_stubs(),
            stubs,
            "builtin_tag_stubs() disagrees with the pin above: either a stub body \
             stopped returning Unimplemented, or the table changed",
        );
        for name in builtin_tag_names() {
            assert!(builtin_tag(name).is_some(), "{name} does not resolve");
        }
        // The *implemented* names, which is the other half of the same pin:
        // every table entry is either here or in `stubs`, and the two halves
        // move in opposite directions as M5 lands. The nine are M2's; the nine
        // after them have landed across M5's tasks.
        let mut expected: Vec<&str> = [
            "autoescape",
            "comment",
            "csrf_token",
            "debug",
            "filter",
            "firstof",
            "for",
            "ifchanged",
            "lorem",
            "now",
            "regroup",
            "resetcycle",
            "spaceless",
            "templatetag",
            "url",
            "verbatim",
            "widthratio",
            "with",
        ]
        .into_iter()
        .chain(stubs)
        .collect();
        expected.sort_unstable();
        assert_eq!(builtin_tag_names(), expected);
    }

    #[test]
    fn the_stub_derivation_is_not_a_copy_of_the_table() {
        // The derivation is only worth having if it can disagree with the table.
        // `unimplemented` is the single gate every stub body goes through, so a
        // name is a stub for exactly one reason and the two sets must partition
        // the table — no name in both, none in neither. If a future milestone
        // gives a tag a real body, the name leaves `stubs` and this still holds,
        // which is what makes it useful at every milestone rather than only at
        // M5's gate.
        let names = builtin_tag_names();
        let stubs = builtin_tag_stubs();
        for name in &stubs {
            assert!(
                names.contains(name),
                "{name} is a stub but is not a registered name",
            );
        }
        let mut both: Vec<&str> = stubs.clone();
        both.extend(names.iter().copied().filter(|n| !stubs.contains(n)));
        both.sort_unstable();
        assert_eq!(
            both, names,
            "stubs and implementations do not partition the table"
        );
    }
}
