//! The built-in tag registry: command name → compile function.
//!
//! Written once, in full, and never edited by a fan-out agent (§10.4): under fan-out
//! each agent owns one `tags/<name>.rs`, and a registry that grew as they landed would
//! be the single shared file they all had to edit. Every built-in tag Rust owns has an
//! entry here, and a tag not implemented yet is a stub in its own file, not a missing
//! entry.
//!
//! **The rule for what this table owns**, since "which built-ins does Rust own" has
//! been asked several times with different answers: a tag is Rust's iff its `CompileFn`
//! is a function of the token and the parser's structural cursor alone (`Parser::parse`,
//! `next_token`, the end-tag names). A tag is subtracted when its compile reads or writes
//! **host-side parser state whose lifetime is the parse, not the node** — a `NodeDesc` is
//! one node's worth of data and cannot carry a value that accumulates across siblings.
//!
//! **Two names are deliberately absent**, and a test asserts their absence: `load`
//! (`do_load` writes host state via `parser.add_library` → `self.tags.update`,
//! `base.py:669`, that a *later* compile must see, so it is a host tag outright) and
//! `querystring` (registered by `simple_tag`, `defaulttags.py:1286`, so its compile
//! function is generated and Python's). The `partial`/`partialdef` pair is in the table
//! because both cross to the host mid-compile on `parser.extra_data`
//! (`defaulttags.py:1251`, `defaulttags.py:1279`) — `Host::partial_node`,
//! `Host::register_partial_def`.
//!
//! The count is 25 = 27 built-ins minus those two. The metric matters:
//! `Engine().template_builtins[0].tags` is 24 (`defaulttags` alone), while the union
//! across all three builtin libraries is 27 (`defaulttags` 24 + `loader_tags` 3) — both
//! right to different questions. `builtin_tag_names_including_stubs` is cross-checked
//! against Django's real registry by `tests/test_tag_inventory.py`, so the number cannot
//! drift unnoticed.

use crate::parser::desc::NodeDesc;
use crate::parser::error::{Error, ErrorKind};
use crate::parser::host::NoHost;
use crate::parser::tags;
use crate::parser::Parser;
use crate::token::{Token, TokenType};

/// A tag's compile function: Django's `Parser.compile_func(self, token)`.
///
/// A plain `fn` pointer rather than `Box<dyn Fn>`, so a table of them could be a
/// `static`; the `'_` in `Parser<'_>` is higher-ranked, so one value works with any
/// parser lifetime.
pub type CompileFn = fn(&mut Parser<'_>, &Token) -> Result<NodeDesc, Error>;

/// `(command, compile function)`, ascending by command.
///
/// One table rather than a `match` plus a name list: two spellings of the same names
/// is a drift risk, and `builtin_tag_names_including_stubs` exists so a test can
/// compare the set against Django's.
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
/// A linear scan of the table: it is small and read once per block tag, and a
/// `HashMap` behind a `OnceLock` would add a synchronisation point for no benefit.
pub fn builtin_tag(command: &str) -> Option<CompileFn> {
    // Test-only stand-ins, an arm rather than table rows: they are parser machinery,
    // not Django tags, and in `REGISTRY` they would add names to
    // `builtin_tag_names_including_stubs` that Django's registry has no counterpart for.
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
/// A second table, not rows in [`REGISTRY`], which is cross-checked against Django's
/// *built-in* registry by `tests/test_tag_inventory.py`; these belong to
/// `cache.py`/`static.py`/`tz.py` and are reachable only after a `{% load %}`.
///
/// Reachability is the host's: this table is consulted only behind
/// [`Host::library_tag_loaded`], because `{% static %}` with no `{% load static %}`
/// is refused by Django (`Invalid block tag on line 1: 'static'. …`) — an answer a
/// `Command` cannot give from a token alone, since `{% load %}` mutates the host's
/// parser.
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
/// The Python shell consumes it to answer `ShellHost.has_tag` for a library tag the
/// way it does for a built-in (see `host.py`'s `_rust_owned`), which it cannot derive:
/// `django_own_tags()` holds all 18, and claiming one Rust has no body for would send
/// it to `invalid_block_tag` instead of the host that compiles it (M12's `245 failures
/// of the 1547`). So the list has to come from Rust.
///
/// Three entries share [`tags::static_tag::compile`] and two pairs share
/// [`tags::i18n::translate`] / [`tags::i18n::block_translate`] — Django's own shape:
/// `do_static`, `get_static_prefix` and `get_media_prefix` are one class-method call
/// with different arguments (`static.py:74`, `static.py:92`, `static.py:171`), as are
/// `translate`/`trans` (`i18n.py:358-359`) and `blocktranslate`/`blocktrans`
/// (`i18n.py:456-457`).
pub fn library_tag_names() -> Vec<&'static str> {
    LIBRARY_REGISTRY.iter().map(|(name, _)| *name).collect()
}

/// Every command name in the registry, ascending — **including the stubs**.
///
/// The registry's *keys*, not the set Rust implements: a key can have a stub body
/// raising `Unimplemented`, and taking the list whole as "implemented" measured 245
/// failures of the 1547. The implemented set is this minus
/// [`builtin_tag_stub_names`], subtracted at each call site because two spellings of
/// one set is the drift this table exists to prevent. That subtraction is currently a
/// no-op ([`builtin_tag_stub_names`] answers `[]`); the pair keeps its shape because
/// the *relationship* is what the Python cross-check consumes.
pub fn builtin_tag_names_including_stubs() -> Vec<&'static str> {
    REGISTRY.iter().map(|(name, _)| *name).collect()
}

/// The registry's names whose compile function is **still a milestone stub**,
/// ascending. The half of the pair [`builtin_tag_names_including_stubs`] documents.
///
/// Derived from the table by **asking it**, not from a second list: a name is a stub
/// iff compiling it returns [`ErrorKind::Unimplemented`]. Every stub body is exactly
/// `Err(unimplemented(name, milestone))` and reads neither parser nor token, so a
/// well-formed, minimal, **body-free** probe (`{% x %}`) tells the two apart — a real
/// tag answers with `Syntax`/`Internal`/`Ok`, never `Unimplemented`, because
/// `unimplemented` is called only from a stub.
///
/// **The probe must stay body-free, and this is the whole insulation**: a stub ignores
/// its content, so any content is decisive only for a real tag. Give the probe an
/// argument a real tag recognises and that tag could answer `Unimplemented` for it, at
/// which point it would be reported as a stub and stop demanding the corpus row this
/// function exists to demand. The probe is not a fixture for anything else either — a
/// test driving a real tag builds its own token.
///
/// The derivation is [`stub_names_in`] over [`REGISTRY`], which keeps it checkable now
/// that its answer is empty: a fabricated table separates the derivation from a
/// hardcoded `vec![]`.
pub fn builtin_tag_stub_names() -> Vec<&'static str> {
    stub_names_in(REGISTRY)
}

/// [`builtin_tag_stub_names`] over an arbitrary table — same probe, same predicate,
/// table as a parameter. Private; its own test hands it a fabricated table whose stub
/// set is not empty, since `REGISTRY`'s is.
fn stub_names_in(table: &[Entry]) -> Vec<&'static str> {
    let probe = Token {
        token_type: TokenType::Block,
        contents: "x".to_string(),
        lineno: 1,
        position: None,
    };
    table
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
/// `Unimplemented` rather than `Syntax`: the tag is real and Django would compile it,
/// so this is about the engine, not the template. The message names the milestone.
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
        // The table is documented as ascending so a reader can find an entry without
        // scanning all of it; a drift out of order silently breaks that.
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
        // 27 built-in names minus `load` and `querystring`; see the module doc for the
        // counting trap.
        assert_eq!(builtin_tag_names_including_stubs().len(), 25);
    }

    #[test]
    fn the_two_python_owned_builtins_are_not_rust_compile_functions() {
        // `load` is host parse state, `querystring` a `simple_tag`; the module doc
        // carries both reasons.
        for absent in ["load", "querystring"] {
            assert!(
                builtin_tag(absent).is_none(),
                "{absent} must not be a Rust compile function (§6: Python-owned)",
            );
        }
    }

    #[test]
    fn every_builtin_tag_name_is_ascii() {
        // The premise four `py_repr_str` call sites rest on: they repr a tag *name*
        // through a function exact on ASCII only (`char::is_control` is Rust's Cc,
        // while Python's `repr` escapes every non-printable category-C code point), so
        // the name is safe only if the registry's keys are ASCII. It is a claim about
        // *our* table, not the merged one — `ShellHost.has_tag` routes any name Rust
        // does not own to the host first.
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
        // What this row holds is the *message* a user sees for an engine gap. The kind
        // stays because the shell's `except ParseError` and `describe()`'s dispatch are
        // written against it, and `tags/verbatim.rs` still returns an `Unimplemented`
        // for a non-text body (pinned by its own row).
        assert_eq!(
            unimplemented("for", "M5").kind,
            ErrorKind::Unimplemented(
                "'for' is a built-in tag this engine does not implement yet (M5)".to_string(),
            ),
        );
    }

    #[test]
    fn every_entry_is_reachable_and_every_unimplemented_one_is_a_stub() {
        // An independent pin on the derivation in `builtin_tag_stub_names`, so a stub
        // body that stopped returning `Unimplemented` reddens here instead of silently
        // shrinking the set the Python gate is a set-difference against. The pin is now
        // empty (every table entry has a real body), which only says no entry is
        // unimplemented — so every assertion the emptiness cannot carry is made over the
        // *names*: the loop below, and `expected` under it.
        let stubs: [&str; 0] = [];
        assert_eq!(
            builtin_tag_stub_names(),
            stubs,
            "builtin_tag_stub_names() disagrees with the pin above: either a stub body \
             stopped returning Unimplemented, or the table changed",
        );
        for name in builtin_tag_names_including_stubs() {
            assert!(builtin_tag(name).is_some(), "{name} does not resolve");
        }
        // The *implemented* names, the other half of the same pin: this is 25 and
        // `stubs` is 0, summing to the table's 25, which
        // `the_registry_has_the_twenty_five_names_rust_owns` asserts directly.
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
            "partial",
            "partialdef",
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
        // The derivation is only worth having if it can disagree with the table, so the
        // two rows below hand [`stub_names_in`] a **fabricated** table that does have a
        // stub in it — `REGISTRY`'s own stub set is empty, which a copy of `[]` would
        // also satisfy. The last block runs the partition over the live table, where a
        // name list gone unsorted or duplicated reddens. The stub body is fabricated
        // rather than borrowed so the row cannot expire when a real tag lands.
        fn a_stub(_: &mut Parser<'_>, _: &Token) -> Result<NodeDesc, Error> {
            Err(unimplemented("z_a_stub", "M00"))
        }
        let with_a_stub: &[Entry] = &[("a_real", tags::comment::compile), ("z_a_stub", a_stub)];
        assert_eq!(
            stub_names_in(with_a_stub),
            ["z_a_stub"],
            "the probe is reported for a stub body and not for a real one",
        );

        // The control over the same population and the *same name*: a real body
        // under `z_a_stub` must stop the derivation reporting it, or the row
        // above would pass on a predicate that answered by name.
        let all_real: &[Entry] = &[
            ("a_real", tags::comment::compile),
            ("z_a_stub", tags::comment::compile),
        ];
        assert_eq!(stub_names_in(all_real), Vec::<&str>::new());

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
        // Same promise as `REGISTRY`'s: the lookup is a linear scan over a known order.
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
        // The literal, because the *set* is a claim about Django's libraries, not a
        // consequence of this file. `tests/test_tag_inventory.py` cross-checks `REGISTRY`
        // against Django's built-in registry; the library table has no such cross-check,
        // so this row is what notices an entry arriving without a task agreeing to it.
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
        // The invariant dispatch depends on: `builtin_tag_names_including_stubs` is what
        // `tests/test_tag_inventory.py` compares against Django's *built-in* registry, so
        // a library name leaking into `REGISTRY` fails there — asserted here, where the
        // mistake would be made.
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
        // The reachability contract as the two lookups: a caller asking `builtin_tag`
        // would get `None` and report `Invalid block tag` for a tag Rust compiles.
        // Whether to ask is `Host::library_tag_loaded`'s decision.
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
        // A shape check on the four entries: three share one compile function, so a
        // copy-paste slip would be invisible — every row above passes if
        // `get_media_prefix` were wired to `cache`'s function and the names permuted.
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
