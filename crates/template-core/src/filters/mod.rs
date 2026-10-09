//! One module per filter (design spec §10.4).
//!
//! M4 owns all 57 built-in filters (§10.3), so every one of them is a module, an
//! entry in [`REGISTRY`] and a **stub** until the group task that owns it replaces
//! the body. A missing registry row would be a filter the engine cannot even name;
//! a stub is a filter it names and refuses to run, which is what makes the
//! milestone's progress visible — `every_registry_entry_is_a_stub_at_m4` shrinks as
//! each group lands, and its failure names what is left.
//!
//! **Written once, in full, and never edited by a fan-out agent**, for the reason
//! `parser::registry` gives: under fan-out each agent owns one `filters/<name>.rs`,
//! and a registry that grew as they landed would be the single shared file they all
//! had to edit — exactly the clobbering this layout exists to prevent. An agent
//! that needs a new `FilterHost` operation stops and reports (§D7); it does not
//! edit this file either.
//!
//! The flags are **measured from `.django` @ 6.0.9**, not inferred from behaviour:
//! 36 filters carry `is_safe=True`, 19 carry `is_safe=False`, and `cut` and
//! `escapejs` have **no `is_safe` attribute at all** — `base.py:816` and
//! `stringfilter`'s wrapper (`defaultfilters.py:48`) both read
//! `getattr(..., "is_safe", False)`, so absent behaves as false and a `bool` is
//! the right model for the engine. It is not the right model for a *test*:
//! `tests/test_filter_inventory.py` asserts the absence separately, because a Rust
//! `bool` cannot tell the two apart and a reader would otherwise assume Django's
//! attribute is always present. `do_not_call_in_templates` and `alters_data` are
//! measured absent on all 57.

use crate::parser::error::Error;

pub mod host;
pub mod value;

pub mod add;
pub mod addslashes;
pub mod capfirst;
pub mod center;
pub mod cut;
pub mod date;
pub mod default;
pub mod default_if_none;
pub mod dictsort;
pub mod dictsortreversed;
pub mod divisibleby;
pub mod escape;
pub mod escapejs;
pub mod escapeseq;
pub mod filesizeformat;
pub mod first;
pub mod floatformat;
pub mod force_escape;
pub mod get_digit;
pub mod iriencode;
pub mod join;
pub mod json_script;
pub mod last;
pub mod length;
pub mod linebreaks;
pub mod linebreaksbr;
pub mod linenumbers;
pub mod ljust;
pub mod lower;
pub mod make_list;
pub mod phone2numeric;
pub mod pluralize;
pub mod pprint;
pub mod random;
pub mod rjust;
pub mod safe;
pub mod safeseq;
pub mod slice;
pub mod slugify;
pub mod stringformat;
pub mod striptags;
pub mod time;
pub mod timesince;
pub mod timeuntil;
pub mod title;
pub mod truncatechars;
pub mod truncatechars_html;
pub mod truncatewords;
pub mod truncatewords_html;
pub mod unordered_list;
pub mod upper;
pub mod urlencode;
pub mod urlize;
pub mod urlizetrunc;
pub mod wordcount;
pub mod wordwrap;
pub mod yesno;

pub use host::{FilterCall, FilterHost, NoFilterHost, NumberFormatArgs};
// Test-only, and re-exported under the same gate for the same reason it is
// `#[cfg(test)]` in `host.rs`: a filter module's `mod tests` reaches it, and
// nothing in a real build should be able to.
#[cfg(test)]
pub use host::TestFilterHost;
pub use value::{Value, ValueId};

/// The three flags Django records per filter, by the names it records them under.
///
/// Four shapes cover all 57 — `NONE` (19), `SAFE` (29), `SAFE_AUTOESCAPE` (7) and
/// `LOCALTIME` (2) — which is why the constants exist rather than 57 spelled-out
/// literals: only the 11 filters carrying a second flag are interesting, and a
/// one-line entry makes which group a filter is in readable at a glance.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FilterFlags {
    pub is_safe: bool,
    pub needs_autoescape: bool,
    pub expects_localtime: bool,
}

impl FilterFlags {
    /// 19 filters: `is_safe` false or absent, and neither of the other two.
    pub const NONE: Self = Self {
        is_safe: false,
        needs_autoescape: false,
        expects_localtime: false,
    };

    /// 29 filters: `is_safe=True` and nothing else.
    pub const SAFE: Self = Self {
        is_safe: true,
        ..Self::NONE
    };

    /// The 7 markup filters: `is_safe=True` with `needs_autoescape=True`.
    pub const SAFE_AUTOESCAPE: Self = Self {
        is_safe: true,
        needs_autoescape: true,
        ..Self::NONE
    };

    /// `date` and `time`: `expects_localtime=True`, and neither is `is_safe`.
    pub const LOCALTIME: Self = Self {
        expects_localtime: true,
        ..Self::NONE
    };
}

/// A filter body: the value, its one optional argument, and the seam.
///
/// A plain `fn` pointer rather than `Box<dyn Fn>`, so the registry is a `static`
/// and so a filter cannot capture anything — Django's filters are functions of
/// `(value, arg)`, and a closure over surrounding state would be the first filter
/// whose behaviour a template author cannot see from its call site.
pub type FilterFn = fn(Value, Option<Value>, &FilterCall<'_>) -> Result<Value, Error>;

/// One filter: what it is called, what Django says about it, and what it does.
pub struct FilterEntry {
    pub name: &'static str,
    pub flags: FilterFlags,
    pub run: FilterFn,
}

/// The 57, ascending by name.
static REGISTRY: &[FilterEntry] = &[
    FilterEntry {
        name: "add",
        flags: FilterFlags::NONE,
        run: add::run,
    },
    FilterEntry {
        name: "addslashes",
        flags: FilterFlags::SAFE,
        run: addslashes::run,
    },
    FilterEntry {
        name: "capfirst",
        flags: FilterFlags::SAFE,
        run: capfirst::run,
    },
    FilterEntry {
        name: "center",
        flags: FilterFlags::SAFE,
        run: center::run,
    },
    // `is_safe` is **absent**, not `False`, on the real filter —
    // `getattr(f, "is_safe", False)` (`base.py:816`, and the
    // `stringfilter` wrapper at `defaultfilters.py:48`) makes the
    // two behave alike. `FilterFlags` cannot spell "absent", and
    // `tests/test_filter_inventory.py` asserts the difference.
    FilterEntry {
        name: "cut",
        flags: FilterFlags::NONE,
        run: cut::run,
    },
    FilterEntry {
        name: "date",
        flags: FilterFlags::LOCALTIME,
        run: date::run,
    },
    FilterEntry {
        name: "default",
        flags: FilterFlags::NONE,
        run: default::run,
    },
    FilterEntry {
        name: "default_if_none",
        flags: FilterFlags::NONE,
        run: default_if_none::run,
    },
    FilterEntry {
        name: "dictsort",
        flags: FilterFlags::NONE,
        run: dictsort::run,
    },
    FilterEntry {
        name: "dictsortreversed",
        flags: FilterFlags::NONE,
        run: dictsortreversed::run,
    },
    FilterEntry {
        name: "divisibleby",
        flags: FilterFlags::NONE,
        run: divisibleby::run,
    },
    FilterEntry {
        name: "escape",
        flags: FilterFlags::SAFE,
        run: escape::run,
    },
    // `is_safe` is **absent**, not `False`, on the real filter —
    // `getattr(f, "is_safe", False)` (`base.py:816`, and the
    // `stringfilter` wrapper at `defaultfilters.py:48`) makes the
    // two behave alike. `FilterFlags` cannot spell "absent", and
    // `tests/test_filter_inventory.py` asserts the difference.
    FilterEntry {
        name: "escapejs",
        flags: FilterFlags::NONE,
        run: escapejs::run,
    },
    FilterEntry {
        name: "escapeseq",
        flags: FilterFlags::SAFE,
        run: escapeseq::run,
    },
    FilterEntry {
        name: "filesizeformat",
        flags: FilterFlags::SAFE,
        run: filesizeformat::run,
    },
    FilterEntry {
        name: "first",
        flags: FilterFlags::NONE,
        run: first::run,
    },
    FilterEntry {
        name: "floatformat",
        flags: FilterFlags::SAFE,
        run: floatformat::run,
    },
    FilterEntry {
        name: "force_escape",
        flags: FilterFlags::SAFE,
        run: force_escape::run,
    },
    FilterEntry {
        name: "get_digit",
        flags: FilterFlags::NONE,
        run: get_digit::run,
    },
    FilterEntry {
        name: "iriencode",
        flags: FilterFlags::SAFE,
        run: iriencode::run,
    },
    FilterEntry {
        name: "join",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: join::run,
    },
    FilterEntry {
        name: "json_script",
        flags: FilterFlags::SAFE,
        run: json_script::run,
    },
    FilterEntry {
        name: "last",
        flags: FilterFlags::SAFE,
        run: last::run,
    },
    FilterEntry {
        name: "length",
        flags: FilterFlags::NONE,
        run: length::run,
    },
    FilterEntry {
        name: "linebreaks",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: linebreaks::run,
    },
    FilterEntry {
        name: "linebreaksbr",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: linebreaksbr::run,
    },
    FilterEntry {
        name: "linenumbers",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: linenumbers::run,
    },
    FilterEntry {
        name: "ljust",
        flags: FilterFlags::SAFE,
        run: ljust::run,
    },
    FilterEntry {
        name: "lower",
        flags: FilterFlags::SAFE,
        run: lower::run,
    },
    FilterEntry {
        name: "make_list",
        flags: FilterFlags::NONE,
        run: make_list::run,
    },
    FilterEntry {
        name: "phone2numeric",
        flags: FilterFlags::SAFE,
        run: phone2numeric::run,
    },
    FilterEntry {
        name: "pluralize",
        flags: FilterFlags::NONE,
        run: pluralize::run,
    },
    FilterEntry {
        name: "pprint",
        flags: FilterFlags::SAFE,
        run: pprint::run,
    },
    FilterEntry {
        name: "random",
        flags: FilterFlags::SAFE,
        run: random::run,
    },
    FilterEntry {
        name: "rjust",
        flags: FilterFlags::SAFE,
        run: rjust::run,
    },
    FilterEntry {
        name: "safe",
        flags: FilterFlags::SAFE,
        run: safe::run,
    },
    FilterEntry {
        name: "safeseq",
        flags: FilterFlags::SAFE,
        run: safeseq::run,
    },
    FilterEntry {
        name: "slice",
        flags: FilterFlags::SAFE,
        run: slice::run,
    },
    FilterEntry {
        name: "slugify",
        flags: FilterFlags::SAFE,
        run: slugify::run,
    },
    FilterEntry {
        name: "stringformat",
        flags: FilterFlags::SAFE,
        run: stringformat::run,
    },
    FilterEntry {
        name: "striptags",
        flags: FilterFlags::SAFE,
        run: striptags::run,
    },
    FilterEntry {
        name: "time",
        flags: FilterFlags::LOCALTIME,
        run: time::run,
    },
    FilterEntry {
        name: "timesince",
        flags: FilterFlags::NONE,
        run: timesince::run,
    },
    FilterEntry {
        name: "timeuntil",
        flags: FilterFlags::NONE,
        run: timeuntil::run,
    },
    FilterEntry {
        name: "title",
        flags: FilterFlags::SAFE,
        run: title::run,
    },
    FilterEntry {
        name: "truncatechars",
        flags: FilterFlags::SAFE,
        run: truncatechars::run,
    },
    FilterEntry {
        name: "truncatechars_html",
        flags: FilterFlags::SAFE,
        run: truncatechars_html::run,
    },
    FilterEntry {
        name: "truncatewords",
        flags: FilterFlags::SAFE,
        run: truncatewords::run,
    },
    FilterEntry {
        name: "truncatewords_html",
        flags: FilterFlags::SAFE,
        run: truncatewords_html::run,
    },
    FilterEntry {
        name: "unordered_list",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: unordered_list::run,
    },
    FilterEntry {
        name: "upper",
        flags: FilterFlags::NONE,
        run: upper::run,
    },
    FilterEntry {
        name: "urlencode",
        flags: FilterFlags::NONE,
        run: urlencode::run,
    },
    FilterEntry {
        name: "urlize",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: urlize::run,
    },
    FilterEntry {
        name: "urlizetrunc",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: urlizetrunc::run,
    },
    FilterEntry {
        name: "wordcount",
        flags: FilterFlags::NONE,
        run: wordcount::run,
    },
    FilterEntry {
        name: "wordwrap",
        flags: FilterFlags::SAFE,
        run: wordwrap::run,
    },
    FilterEntry {
        name: "yesno",
        flags: FilterFlags::NONE,
        run: yesno::run,
    },
];

/// The entry for `name`, if the engine has one.
///
/// A linear scan of 57, for `parser::registry`'s reason: the table is small, the
/// lookup is per filter call rather than per character, and a `HashMap` behind a
/// `OnceLock` would add a synchronisation point to buy nothing.
pub fn filter(name: &str) -> Option<&'static FilterEntry> {
    REGISTRY.iter().find(|entry| entry.name == name)
}

/// Every filter name, ascending.
pub fn filter_names() -> Vec<&'static str> {
    REGISTRY.iter().map(|entry| entry.name).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    /// The 57 names, spelled out rather than counted.
    ///
    /// Counted would be shorter and worse: the set is what a later milestone
    /// compares against Django's own registry, and a test that only knows the
    /// length cannot name the filter that went missing. Comparing to a sorted
    /// literal also pins the "ascending by name" claim, so there is no separate
    /// ordering test to drift from this one.
    const MEASURED_NAMES: [&str; 57] = [
        "add",
        "addslashes",
        "capfirst",
        "center",
        "cut",
        "date",
        "default",
        "default_if_none",
        "dictsort",
        "dictsortreversed",
        "divisibleby",
        "escape",
        "escapejs",
        "escapeseq",
        "filesizeformat",
        "first",
        "floatformat",
        "force_escape",
        "get_digit",
        "iriencode",
        "join",
        "json_script",
        "last",
        "length",
        "linebreaks",
        "linebreaksbr",
        "linenumbers",
        "ljust",
        "lower",
        "make_list",
        "phone2numeric",
        "pluralize",
        "pprint",
        "random",
        "rjust",
        "safe",
        "safeseq",
        "slice",
        "slugify",
        "stringformat",
        "striptags",
        "time",
        "timesince",
        "timeuntil",
        "title",
        "truncatechars",
        "truncatechars_html",
        "truncatewords",
        "truncatewords_html",
        "unordered_list",
        "upper",
        "urlencode",
        "urlize",
        "urlizetrunc",
        "wordcount",
        "wordwrap",
        "yesno",
    ];

    /// The filters whose bodies are still stubs: the 55 of the 57 that M4 has
    /// not landed yet.
    ///
    /// A literal rather than an alias of [`MEASURED_NAMES`] now that the first
    /// group has landed, because the two are no longer the same set and an alias
    /// would have to be trimmed in place to say so. The 55 are the 57 minus
    /// `lower` and `upper`, which Task 3 implements as the corpus's seed.
    ///
    /// **The central commit that lands a group moves that group's names out of
    /// this list**, in the same commit — and the assertion below is what makes
    /// forgetting fail rather than pass.
    const STILL_STUBBED: [&str; 55] = [
        "add",
        "addslashes",
        "capfirst",
        "center",
        "cut",
        "date",
        "default",
        "default_if_none",
        "dictsort",
        "dictsortreversed",
        "divisibleby",
        "escape",
        "escapejs",
        "escapeseq",
        "filesizeformat",
        "first",
        "floatformat",
        "force_escape",
        "get_digit",
        "iriencode",
        "join",
        "json_script",
        "last",
        "length",
        "linebreaks",
        "linebreaksbr",
        "linenumbers",
        "ljust",
        "make_list",
        "phone2numeric",
        "pluralize",
        "pprint",
        "random",
        "rjust",
        "safe",
        "safeseq",
        "slice",
        "slugify",
        "stringformat",
        "striptags",
        "time",
        "timesince",
        "timeuntil",
        "title",
        "truncatechars",
        "truncatechars_html",
        "truncatewords",
        "truncatewords_html",
        "unordered_list",
        "urlencode",
        "urlize",
        "urlizetrunc",
        "wordcount",
        "wordwrap",
        "yesno",
    ];

    #[test]
    fn the_registry_is_the_measured_57() {
        assert_eq!(
            filter_names(),
            MEASURED_NAMES.to_vec(),
            "the filter registry is not the measured 57; §10.3 gives M4 all of \
             them, and this list is the one a later milestone diffs against \
             Django's own registry",
        );
    }

    /// The registry's flags against the measured table, filter by filter.
    ///
    /// A separate assertion from the name list because the two fail for different
    /// reasons — a missing row and a wrong flag are different bugs — and because
    /// the names and the flags come from different columns of the plan's Step 3
    /// table. The expected set is spelled out here rather than derived from the
    /// same four constants [`REGISTRY`] uses, or this test would agree with any
    /// constant the registry picked.
    ///
    /// **That independence is what licenses the constants at all.**
    /// [`FilterFlags::SAFE`], `SAFE_AUTOESCAPE`, `LOCALTIME` and `NONE` are each
    /// shared by many rows, so a single wrong constant would be wrong in every
    /// one of them at once — and the four constants are exactly the kind of
    /// duplication a later reader deletes as "redundant with its uses". It is
    /// not redundant: this test's names and flags are written out by hand, so
    /// they are a second opinion rather than the constants repeated. Deriving
    /// them from `FilterFlags::SAFE` here would remove the guard without
    /// touching a constant, and the four names above would silently become four
    /// aliases.
    #[test]
    fn the_flags_are_the_measured_ones() {
        let safe_names: [&str; 36] = [
            "addslashes",
            "capfirst",
            "center",
            "escape",
            "escapeseq",
            "filesizeformat",
            "floatformat",
            "force_escape",
            "iriencode",
            "join",
            "json_script",
            "last",
            "linebreaks",
            "linebreaksbr",
            "linenumbers",
            "ljust",
            "lower",
            "phone2numeric",
            "pprint",
            "random",
            "rjust",
            "safe",
            "safeseq",
            "slice",
            "slugify",
            "stringformat",
            "striptags",
            "title",
            "truncatechars",
            "truncatechars_html",
            "truncatewords",
            "truncatewords_html",
            "unordered_list",
            "urlize",
            "urlizetrunc",
            "wordwrap",
        ];
        let autoescape_names: [&str; 7] = [
            "join",
            "linebreaks",
            "linebreaksbr",
            "linenumbers",
            "unordered_list",
            "urlize",
            "urlizetrunc",
        ];
        let localtime_names: [&str; 2] = ["date", "time"];

        for name in filter_names() {
            let flags = filter(name).expect("every name resolves").flags;
            assert_eq!(
                flags.is_safe,
                safe_names.contains(&name),
                "`{name}`'s is_safe is not the measured value",
            );
            assert_eq!(
                flags.needs_autoescape,
                autoescape_names.contains(&name),
                "`{name}`'s needs_autoescape is not the measured value",
            );
            assert_eq!(
                flags.expects_localtime,
                localtime_names.contains(&name),
                "`{name}`'s expects_localtime is not the measured value",
            );
        }
    }

    /// The error a stub returns, if that is what it is.
    ///
    /// Matched on the **kind and the name** rather than on "it returned `Err`".
    /// "Any error counts as still-stubbed" would be wrong in both directions: a
    /// real `add` given `Value::None` raises, so an implemented filter could look
    /// stubbed, and a stub body that named the wrong filter — a copy-pasted
    /// `run:` line — would look implemented. Matching the name makes the second
    /// case a failure here, which is the one a 57-line table is likely to make.
    fn stub_error(name: &str, error: &Error) -> bool {
        matches!(
            &error.kind,
            ErrorKind::Unimplemented(message) if message.contains(&format!("'{name}'")),
        )
    }

    /// Every body is a stub, and this is what makes each group's deliverable
    /// visible as it lands.
    ///
    /// **The central commit that lands a group moves that group's names out of
    /// [`STILL_STUBBED`]**, in the same commit. The assertion is checked in both
    /// directions and names each side, because the two failures mean opposite
    /// things — a name that is still a stub but no longer declared means the list
    /// was trimmed too early, and a declared name that is already implemented
    /// means the group landed without saying so. A plain `assert_eq!` of two
    /// 57-name lists makes a reader find the difference by eye.
    #[test]
    fn every_registry_entry_is_a_stub_at_m4() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: false,
        };
        let stubbed: Vec<&str> = REGISTRY
            .iter()
            .filter(|entry| match (entry.run)(Value::None, None, &call) {
                Err(error) => stub_error(entry.name, &error),
                Ok(_) => false,
            })
            .map(|entry| entry.name)
            .collect();
        let declared: Vec<&str> = STILL_STUBBED.to_vec();
        let unbuilt: Vec<&str> = stubbed
            .iter()
            .copied()
            .filter(|name| !declared.contains(name))
            .collect();
        let landed: Vec<&str> = declared
            .iter()
            .copied()
            .filter(|name| !stubbed.contains(name))
            .collect();
        assert!(
            unbuilt.is_empty() && landed.is_empty(),
            "the stub list and the registry disagree — still stubbed but not \
             declared: {unbuilt:?}; declared but already implemented: \
             {landed:?}",
        );
    }
}
