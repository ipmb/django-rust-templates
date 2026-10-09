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
//! **Five built-in names are deliberately absent**, and a test asserts their
//! absence. `block`, `extends` and `include` are §6's — Python's, supplied
//! through the host seam, M7's. `load` and `querystring` are §6's too, and M6
//! took them out of the table; the paragraphs below record which of the rule's
//! clauses each one falls under. An entry here would be a promise this project
//! has already decided not to keep. The mechanical reason — the one to reach for
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
//! - `extends` reads `parser.origin.template_name` (`:305`) and inspects the
//!   just-parsed nodelist through `get_nodes_by_type` (`:308`) — a query over
//!   *materialised* nodes, which do not exist at compile time.
//! - `include` reaches `parser.compile_filter` and the loader machinery.
//!
//! **`load` and `querystring` were the table's last two members under a
//! deferred decision, and M6 resolved both by subtraction — for two different
//! reasons, which is why they are written down separately.**
//!
//! `load` is A1's: `do_load` writes host state (`parser.add_library` →
//! `self.tags.update`, `base.py:669`), and the effect must be visible to a
//! *later* compile — `Parser::parse` consults `self.host.has_tag(&command)`
//! **first**, so a tag a `{% load %}` registered reaches Rust only through the
//! host. `do_load` also resolves library *names* through `parser.libraries`,
//! which `Template.compile_nodelist` fills from `Engine.template_libraries` — a
//! lookup Rust cannot perform and must not duplicate. So it is a host tag
//! outright: no Rust compile function, no seam, nothing to keep in step.
//!
//! `querystring` is A4's, and its reason is not the rule above at all.
//! `defaulttags.querystring` is registered by `simple_tag`
//! (`defaulttags.py:1286`), so its compile function is *generated* —
//! `simple_tag`'s wrapper calls `parse_bits` with `parser.compile_filter`
//! (`library.py:454`), reads the callable's signature, and builds a
//! `SimpleNode`. Rust has no version of that and should not grow one: the node
//! the host builds is Django's own `SimpleNode`, holding the Python function,
//! and a bespoke Rust node of the same name would fail the parity comparison
//! (which is by class name) while rendering the same output — the worst shape a
//! divergence can take. Same conclusion as `load`, different clause: `load` is
//! state the parse accumulates, this is a compile function that is Python's
//! because the *decorator* is.
//!
//! Note what the rule is **not**: it is not "the node's `render` needs the
//! host". Every node is materialised as Django's own class and renders in
//! Python, so that test would subtract all of them — `NowNode.render` reads
//! `settings.USE_TZ` and `timezone.get_current_timezone()`
//! (`defaulttags.py:403`) and `now` is one of M2's nine. It is also not
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
//! The count is 22 = 27 built-ins minus those five, and the metric matters:
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
    ("lorem", tags::lorem::compile),
    ("now", tags::now::compile),
    ("partial", tags::partial::compile),
    ("partialdef", tags::partialdef::compile),
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
///
/// **The probe must stay body-free, and this is the whole insulation.** The
/// predicate is sound only because a stub body ignores both the parser and the
/// token and returns `Unimplemented` unconditionally; the probe's *content* is
/// therefore irrelevant to a stub and decisive for a real tag. Give the probe a
/// body — an argument, a name a real tag also recognises — and a real tag could
/// answer `Unimplemented` for that one input, at which point it would be
/// reported as a stub, leave the set, and quietly stop demanding the corpus row
/// this function exists to demand. The same reasoning makes the reverse safe:
/// no real tag returns `Unimplemented` for *any* input, because `unimplemented`
/// is only ever called from a stub.
///
/// The probe is also **not** a fixture for anything else. A test that wants to
/// drive a real tag's compile function builds its own token, as the corpus does;
/// reusing this one would couple a real tag's behaviour to a predicate that must
/// stay insensitive to it.
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
    fn the_registry_has_the_twenty_two_names_rust_owns() {
        // 27 built-in names minus `block`, `extends`, `include`, `load` and
        // `querystring`. See the module doc for why those five are absent and
        // for the counting trap.
        assert_eq!(builtin_tag_names().len(), 22);
    }

    #[test]
    fn the_five_python_owned_builtins_are_not_rust_compile_functions() {
        // Three for M7 (§6's loader tags) and two M6 removed: `load` is host
        // state, `querystring` is a `simple_tag`. The reasons are separate and
        // the module doc carries both.
        for absent in ["block", "extends", "include", "load", "querystring"] {
            assert!(
                builtin_tag(absent).is_none(),
                "{absent} must not be a Rust compile function (§6: Python-owned)",
            );
        }
    }

    #[test]
    fn every_builtin_tag_name_is_ascii() {
        // The premise four `py_repr_str` call sites rest on, stated once where
        // it can fail. `{% with %}`, `{% lorem %}` and `{% resetcycle %}` all
        // repr their *tag name* through `py_repr_str` — which is exact on ASCII
        // only, since `char::is_control` is Rust's Cc while Python's `repr`
        // escapes every non-printable category-C code point. Each of those
        // sites argues the input is safe because the name is "the tag name
        // dispatch matched against the registry, not text from the template".
        // That sentence is only true if the registry's keys are ASCII, so the
        // claim is checked here rather than left inside the argument.
        //
        // It survives M6, when a `{% load %}` makes a second tag registry
        // reachable, because it is a claim about *our* table and not about the
        // merged one — the merged table is held off by `ShellHost.has_tag`'s
        // identity predicate, which routes any name Rust does not own to the
        // host before our compile function runs. So the assertion stays a
        // sufficient reason even though the surrounding support changed from
        // "Rust owns the registry outright" to "the dispatch predicate filters
        // what reaches it" (`.review/m5-seams.md`, Finding A point 3).
        for name in builtin_tag_names() {
            assert!(
                name.is_ascii(),
                "{name:?} is not ASCII: a tag name reaches `py_repr_str`, which \
                 escapes only Cc, so a non-ASCII name would be repr'd raw where \
                 Python escapes it. Route it through the host's `repr` instead \
                 of widening `py_repr_str` (design spec §10.4).",
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
        // **All eleven M5 tags landed**, leaving the four names M5 was never
        // going to touch; **M6 then took two of them out of the table
        // entirely** — `load` and `querystring` are host-owned now, so they are
        // not stubs, they are absent. Two names remain, both M13's, and the
        // literal shrinks with them here rather than at the end of the
        // milestone, which is what keeps every commit in between green.
        let stubs = ["partial", "partialdef"];
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
        // move in opposite directions. The nine are M2's, the eleven after them
        // landed across M5's tasks, and M6 removed two names from the table
        // rather than implementing them — so this list is 20 and `stubs` is 2,
        // summing to the registry's 22.
        let mut expected: Vec<&str> = [
            "autoescape",
            "comment",
            "csrf_token",
            "cycle",
            "debug",
            "filter",
            "firstof",
            "for",
            "if",
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
