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
//! **Two built-in names are deliberately absent**, and a test asserts their
//! absence. `load` and `querystring` are §6's, and M6 took them out of the table;
//! the paragraphs below record which of the rule's clauses each one falls under.
//! An entry here would be a promise this project has already decided not to
//! keep. The mechanical reason — the one to reach for when the next built-in's
//! side has to be decided — is the rule below.
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
//! - `block` **and `extends` were the rule's last two cases, and M7 removed
//!   both** — the only two entries that have left the subtracted list, so the
//!   reasoning is worth keeping. They left for two *different* versions of the
//!   same move, and telling them apart is what makes the rule usable on the next
//!   built-in.
//!
//!   `do_block` writes `parser.__loaded_blocks` (`loader_tags.py:227-232`), a
//!   value that accumulates across siblings, and a `NodeDesc` cannot carry it —
//!   which is why the rule subtracted `block` for as long as that state lived
//!   only in Django's parser. M7 added the field to **Rust's** `Parser`
//!   (`mod.rs`, `loaded_blocks`), so every occurrence **Rust compiles** now
//!   reads and writes that state in the same place, and the rule's test is
//!   *satisfied* rather than excepted — for those occurrences. The state did
//!   not *move*, though: it forked. A `{% block %}` inside a **host** tag's body
//!   is still compiled by Django's `do_block` and still accumulates in
//!   Django's `parser.__loaded_blocks`, two counters that cannot see each other.
//!   That is A5's recorded divergence, reached exactly once in the pinned suite
//!   (`test_blocktranslate.py::test_with_block`, where the host raises first).
//!   What the fork costs is one parse-time message — the `'block' tag with name
//!   '…' appears more than once` refusal can miss a duplicate split across the
//!   two counters — and nothing about the rendered tree, which both parsers
//!   build from materialised nodes. That is what "the rule's test is satisfied
//!   *for the occurrences Rust compiles*" means, and it is the shape to look
//!   for in the next built-in whose state is shared with the host. `do_block`
//!   also recurses through `parser.parse(("endblock",))`, which is structural
//!   and was never the obstacle.
//!
//!   `do_extends` reads `parser.origin.template_name` (`loader_tags.py:305`) and
//!   inspects the just-parsed nodelist through `get_nodes_by_type`
//!   (`loader_tags.py:308`) — a query over **materialised** nodes, which do not
//!   exist at compile time, and that half was the real obstacle. M7 did not move
//!   the machinery for it; it **weakened the query to one the descriptors can
//!   answer**, which is a different kind of resolution and the reason this
//!   bullet is not the `block` bullet: `NodeListDesc::get_nodes_by_type` walks
//!   by class *name*, and the one shape it cannot see is a host node that *is*
//!   an `ExtendsNode`. Measured zero in the pinned suite and recorded on the
//!   method. The origin half was never a subtraction reason of its own: Rust
//!   added `Parser::origin_template_name` in Task 2, and reading an attribute off
//!   the parse is exactly the kind of thing the rule permits.
//! - `include` **was the bullet that read like a subtraction reason and was not
//!   one**, which is why it is kept rather than deleted. It "reaches
//!   `parser.compile_filter` and the loader machinery" — and so does every other
//!   tag in this table: `compile_filter` is a method on the parser Rust already
//!   has, and `{% extends %}` reaches it too. What `do_include` actually touches
//!   is `parser.origin.template_name` (`loader_tags.py:361-365`) and
//!   `token_kwargs` (`loader_tags.py:347`) — the one a Task 2 field answers, the
//!   other the helper `{% with %}` already uses. **The loader machinery is on the node's
//!   side of the seam, not the compile's**: `IncludeNode.render` is what resolves
//!   and renders the template, and under A1 every node renders in Python whatever
//!   compiled it. So M7 moved `include` into this table with no new state, no
//!   widened seam and no second mechanism — the subtraction was a name that had
//!   been listed as a reason without ever meeting the rule.
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
//! (`defaulttags.py:1251`, `defaulttags.py:1279`), the same *kind* of host parse
//! state the rule subtracts, so neither can be compiled from a token alone as
//! written. `partial` is the stronger of the two and it is worth recording what
//! it actually does: `partial_func` constructs
//! `DeferredSubDict(extra_data, "partials")` — a **live view over the parser's
//! dict** — and hands it to `PartialNode`, so the compiled node closes over parse
//! state that outlives the parse. That is not "reads host state at compile"; it
//! is a value a `NodeDesc` cannot represent at all. They stay in this table for
//! now because their stubs make the milestone visible and because M13 has a real
//! choice — widen the seam, or grow the wire an `extra_data` channel — that M3
//! must not pre-empt. The reviewer's argument distinguished the pair (only
//! `partial`'s *render* re-enters host state); that distinction does not hold
//! under this rule, which acts at compile time, where both are the same case.
//! What is recorded here is the measurement, not a verdict.
//!
//! The count is 25 = 27 built-ins minus those two, and the metric matters:
//! `Engine().template_builtins[0].tags` is 24 (`defaulttags` alone), while the
//! union across all three builtin libraries is 27 (`defaulttags` 24 +
//! `loader_tags` 3). Both are right answers to different questions, and this
//! project has lost time to that shape four times.
//! `builtin_tag_names_including_stubs` is
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
/// the same 24 names is a drift risk, and `builtin_tag_names_including_stubs`
/// exists precisely so a test can compare the set against Django's.
type Entry = (&'static str, CompileFn);

const REGISTRY: &[Entry] = &[
    ("autoescape", tags::autoescape::compile),
    ("block", tags::block::compile),
    ("comment", tags::comment::compile),
    ("csrf_token", tags::csrf_token::compile),
    ("cycle", tags::cycle::compile),
    ("debug", tags::debug::compile),
    ("extends", tags::extends::compile),
    ("filter", tags::filter::compile),
    ("firstof", tags::firstof::compile),
    ("for", tags::for_tag::compile),
    ("if", tags::if_tag::compile),
    ("ifchanged", tags::ifchanged::compile),
    ("include", tags::include::compile),
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
    // names in `builtin_tag_names_including_stubs` that have no counterpart in
    // Django's registry — which is exactly what the Python cross-check asserts
    // against.
    #[cfg(test)]
    if let Some(compile) = tags::standin_tag(command) {
        return Some(compile);
    }
    REGISTRY
        .iter()
        .find(|(name, _)| *name == command)
        .map(|(_, compile)| *compile)
}

/// The **library** tags Rust compiles — `django.templatetags.*` — ascending.
///
/// A second table rather than rows in [`REGISTRY`], and the reason is the
/// counting trap this module's doc opens with: `builtin_tag_names_including_stubs`
/// is cross-checked against Django's real *built-in* registry by
/// `tests/test_tag_inventory.py`, and none of these names is in it. They belong
/// to `django/templatetags/cache.py`, `static.py` and `tz.py`, they are
/// reachable only after a `{% load %}`, and folding them in would make the
/// engine claim to own built-ins Django does not have.
///
/// **Reachability is the other half, and the host owns it.** A loaded library tag
/// is not in [`REGISTRY`]'s sense of "ours" until the template loaded the
/// library: `{% static %}` with no `{% load static %}` is *not a Rust tag*,
/// Django refuses it, and the refusal is measured (`Invalid block tag on line 1:
/// 'static'. Did you forget to register or load this tag?`). So this table is
/// consulted only behind [`Host::library_tag_loaded`], which is the question a
/// `Command` cannot answer from a token alone — the answer lives in the host's
/// parser, which `{% load %}` mutates.
///
/// [`Host::library_tag_loaded`]: crate::parser::host::Host::library_tag_loaded
const LIBRARY_REGISTRY: &[Entry] = &[
    ("blocktrans", tags::i18n::block_translate),
    ("blocktranslate", tags::i18n::block_translate),
    ("cache", tags::cache::compile),
    (
        "get_available_languages",
        tags::i18n::get_available_languages,
    ),
    ("get_current_language", tags::i18n::get_current_language),
    (
        "get_current_language_bidi",
        tags::i18n::get_current_language_bidi,
    ),
    ("get_current_timezone", tags::tz::get_current_timezone),
    ("get_language_info", tags::i18n::get_language_info),
    ("get_language_info_list", tags::i18n::get_language_info_list),
    ("get_media_prefix", tags::static_tag::compile),
    ("get_static_prefix", tags::static_tag::compile),
    ("language", tags::i18n::language),
    ("localize", tags::l10n::localize),
    ("localtime", tags::tz::localtime),
    ("static", tags::static_tag::compile),
    ("timezone", tags::tz::timezone),
    ("trans", tags::i18n::translate),
    ("translate", tags::i18n::translate),
];

/// The compile function Rust has for the **library** tag `command`, if it has
/// one. See [`LIBRARY_REGISTRY`] for why this is a separate lookup rather than a
/// second arm of [`builtin_tag`].
pub fn library_tag(command: &str) -> Option<CompileFn> {
    LIBRARY_REGISTRY
        .iter()
        .find(|(name, _)| *name == command)
        .map(|(_, compile)| *compile)
}

/// Every command name in [`LIBRARY_REGISTRY`], ascending.
///
/// Consumed by the Python shell, which needs the set to answer
/// `ShellHost.has_tag` for a library tag the way it answers for a built-in one —
/// see `python/django_rust_templates/host.py`'s `_rust_owned`. The shell cannot
/// derive it: the natural-looking source, `django_own_tags()`, holds **all 18**
/// library tags, and claiming one Rust has no body for would send it to
/// `invalid_block_tag` instead of to the host that compiles it (the defect
/// M12's `245 failures of the 1547` measurement is about). So the list of
/// library tags Rust actually compiles has to come from Rust.
///
/// **All 18 as of M12 Task 6b**, the last name-adding task: `blocktranslate` and
/// `blocktrans` joined the sixteen Task 6a left, so this table and
/// `django_own_tags()` now hold the same set — the `245 failures` defect is
/// closed for the library scope. The list stays derived from this table rather
/// than hardcoded, because it is still the *Rust* answer the shell must ask for.
///
/// Three of the entries share [`tags::static_tag::compile`], which is
/// Django's own shape rather than a shortcut here — `do_static`,
/// `get_static_prefix` and `get_media_prefix` are one class-method call with
/// different arguments (`static.py:74`, `static.py:92`, `static.py:171`), and that function
/// recovers the command from the token. [`tags::i18n::translate`] and
/// [`tags::i18n::block_translate`] are shared the same way: `translate`/`trans`
/// (`i18n.py:358-359`) and `blocktranslate`/`blocktrans` (`i18n.py:456-457`) are
/// each one body registered twice.
pub fn library_tag_names() -> Vec<&'static str> {
    LIBRARY_REGISTRY.iter().map(|(name, _)| *name).collect()
}

/// Every command name in the registry, ascending — **including the stubs**.
///
/// The name says what the function answers, because the old one
/// (`builtin_tag_names`, its name until M9's rename) did not, and the misreading
/// it invited has now been made three times: this is the registry's *keys*, not
/// the set of tags Rust implements. A stub is a key whose body raises
/// `Unimplemented`, so anyone who takes this list whole claims `partial` and
/// `partialdef` for Rust and then has to hand them back — measured at 245
/// failures of the 1547 when `ShellHost` did exactly that. The implemented set is
/// this minus [`builtin_tag_stub_names`], and the subtraction is written at each
/// call site rather than offered as a third function because two spellings of
/// one set is the drift this table exists to prevent.
pub fn builtin_tag_names_including_stubs() -> Vec<&'static str> {
    REGISTRY.iter().map(|(name, _)| *name).collect()
}

/// The registry's names whose compile function is **still a milestone stub**,
/// ascending.
///
/// The half of the pair [`builtin_tag_names_including_stubs`] documents: this is
/// what the names above are subtracted by. Named `…_stub_names` rather than
/// `…_stubs` so the two read as one set and a subset of it, which is what they
/// are.
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
/// `builtin_tag_stub_names() & M5_TAGS == set()` — so it has to be the engine's own
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
pub fn builtin_tag_stub_names() -> Vec<&'static str> {
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
        let names = builtin_tag_names_including_stubs();
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
    fn the_registry_has_the_twenty_five_names_rust_owns() {
        // 27 built-in names minus `load` and `querystring`. See the module doc
        // for why those two are absent and for the counting trap. `include` was
        // the third until M7; the doc keeps its bullet because the reason it was
        // listed never met the rule.
        assert_eq!(builtin_tag_names_including_stubs().len(), 25);
    }

    #[test]
    fn the_two_python_owned_builtins_are_not_rust_compile_functions() {
        // Both M6's, for separate reasons: `load` is host parse state,
        // `querystring` is a `simple_tag`. The module doc carries them.
        // **All three of M7's loader tags have now left this list** — `block`
        // and `extends` in Tasks 1 and 3, `include` here — which is why it
        // counts down rather than growing.
        for absent in ["load", "querystring"] {
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
        for name in builtin_tag_names_including_stubs() {
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
        // `builtin_tag_stub_names`, so a stub body that stopped returning
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
            builtin_tag_stub_names(),
            stubs,
            "builtin_tag_stub_names() disagrees with the pin above: either a stub body \
             stopped returning Unimplemented, or the table changed",
        );
        for name in builtin_tag_names_including_stubs() {
            assert!(builtin_tag(name).is_some(), "{name} does not resolve");
        }
        // The *implemented* names, which is the other half of the same pin:
        // every table entry is either here or in `stubs`, and the two halves
        // move in opposite directions. The nine are M2's, the eleven after them
        // landed across M5's tasks, and M6 removed two names from the table
        // rather than implementing them — so this list was 20 and `stubs` is 2,
        // summing to the registry's 22. **M7's `block` and `extends` are the
        // first names to move between the halves rather than in or out of the
        // table**: neither was ever a stub, and both arrive from the `absent`
        // list, so this is 22, `stubs` is still 2, and the sum is 24.
        let mut expected: Vec<&str> = [
            "autoescape",
            "block",
            "comment",
            "csrf_token",
            "cycle",
            "debug",
            "extends",
            "filter",
            "firstof",
            "for",
            "if",
            "ifchanged",
            "include",
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
        assert_eq!(builtin_tag_names_including_stubs(), expected);
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
        let names = builtin_tag_names_including_stubs();
        let stubs = builtin_tag_stub_names();
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

    #[test]
    fn the_library_registry_is_sorted_and_has_no_duplicates() {
        // Same promise as `REGISTRY`'s, for the same reason: a reader finds an
        // entry without scanning all four, and the lookup is a linear scan over
        // a known order.
        let names = library_tag_names();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted, "LIBRARY_REGISTRY is not ascending");
        sorted.dedup();
        assert_eq!(
            sorted.len(),
            names.len(),
            "LIBRARY_REGISTRY has a duplicate"
        );
    }

    #[test]
    fn the_library_registry_holds_the_eighteen_names_m12_has_landed() {
        // The literal, because the *set* is a claim about Django's libraries and
        // not a consequence of anything in this file. `tests/test_tag_inventory.py`
        // cross-checks `REGISTRY` against Django's built-in registry; the library
        // table has no such cross-check yet, because the modules it names are
        // interposed one task at a time — so this row is what notices an entry
        // arriving without a task agreeing to it.
        //
        // Four at Task 3 (`cache`, `static`, and the two `static.py` prefix
        // readers), three more at Task 4 (`tz`'s three tags), one more at Task 5
        // (`l10n`'s single tag, `localize`), eight at Task 6a (`i18n`'s eight
        // non-translating tags) and the last two at Task 6b (`blocktranslate`,
        // `blocktrans`). Eighteen is the whole of `django_own_tags()`, so this is
        // the first commit at which the library table and Django's own set agree.
        // Task 7 adds the nine *filters* of the five libraries and moves none of
        // these names: filters are not tags and never enter this table.
        assert_eq!(
            library_tag_names(),
            [
                "blocktrans",
                "blocktranslate",
                "cache",
                "get_available_languages",
                "get_current_language",
                "get_current_language_bidi",
                "get_current_timezone",
                "get_language_info",
                "get_language_info_list",
                "get_media_prefix",
                "get_static_prefix",
                "language",
                "localize",
                "localtime",
                "static",
                "timezone",
                "trans",
                "translate",
            ],
        );
    }

    #[test]
    fn the_two_tables_are_disjoint_and_the_library_one_adds_nothing_to_the_builtins() {
        // The invariant the dispatch depends on. `builtin_tag_names_including_stubs`
        // is what `tests/test_tag_inventory.py` compares against Django's
        // *built-in* registry, so a library name leaking into `REGISTRY` fails
        // there; this asserts it here, where the mistake would be made.
        let builtins = builtin_tag_names_including_stubs();
        for name in library_tag_names() {
            assert!(
                !builtins.contains(&name),
                "{name} is in both tables: it is a library tag, and REGISTRY is \
                 cross-checked against Django's built-in registry",
            );
        }
        assert_eq!(
            builtin_tag_names_including_stubs().len(),
            25,
            "the library table must not change the built-in count",
        );
    }

    #[test]
    fn a_library_name_resolves_through_library_tag_and_not_through_builtin_tag() {
        // The reachability contract, stated as the two lookups rather than as
        // prose: a caller that asked `builtin_tag` would get `None` and then have
        // to report `Invalid block tag` for a tag Rust compiles. The gate that
        // decides *whether* to ask is `Host::library_tag_loaded` and is the
        // host's; what is asserted here is only that whichever caller asks, the
        // answer is on this side.
        for name in library_tag_names() {
            assert!(
                library_tag(name).is_some(),
                "{name} does not resolve through LIBRARY_REGISTRY",
            );
            assert!(
                builtin_tag(name).is_none(),
                "{name} resolves through builtin_tag: the tables overlap",
            );
        }
    }

    #[test]
    fn the_two_static_prefix_names_are_not_confused_with_the_tag_that_takes_a_path() {
        // A cheap shape check on the four entries, because three of them share
        // one compile function and a copy-paste slip there would be invisible:
        // every row above passes if `get_media_prefix` were wired to `cache`'s
        // function *and* the names were permuted to match. What is asserted is
        // that each name resolves and that the answers are not all one function —
        // the sharing itself is `static.rs`'s to state, and its own tests do.
        assert!(library_tag("static").is_some());
        assert!(library_tag("cache").is_some());
        assert!(
            !std::ptr::eq(
                library_tag("cache").expect("resolves") as *const (),
                library_tag("static").expect("resolves") as *const (),
            ),
            "`cache` is a different module and cannot share `static.rs`'s function",
        );
    }
}
