//! One module per filter (design spec §10.4), each with an entry in [`REGISTRY`].
//!
//! The flags are **measured from `.django` @ 6.0.9**: 36 filters carry
//! `is_safe=True`, 19 `is_safe=False`, and `cut`/`escapejs` have **no `is_safe`
//! attribute at all** — `getattr(..., "is_safe", False)` (`base.py:816`,
//! `defaultfilters.py:48`) makes absent behave as false, so a `bool` models the
//! engine (but not a *test*: `tests/test_filter_inventory.py` asserts the absence).
//! `do_not_call_in_templates` and `alters_data` are measured absent on all 57.

use crate::parser::error::Error;

pub mod host;
pub mod value;

/// Shared helpers — code the bodies use but which is not itself a filter. Not
/// `pub`: nothing outside this module tree calls it.
mod padding;

/// The one pure table more than one group's bodies need; see [`html_escape`].
mod html_escape;

/// Reachable outside the crate while the *module* stays private. `pub use` rather
/// than `pub mod` so `filters::html_escape::html_escape` is not a second name for it.
pub use html_escape::html_escape;

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
pub mod language_bidi;
pub mod language_name;
pub mod language_name_local;
pub mod language_name_translated;
pub mod last;
pub mod length;
pub mod linebreaks;
pub mod linebreaksbr;
pub mod linenumbers;
pub mod ljust;
pub mod localize;
pub mod localtime;
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
pub mod timezone;
pub mod title;
pub mod truncatechars;
pub mod truncatechars_html;
pub mod truncatewords;
pub mod truncatewords_html;
pub mod unlocalize;
pub mod unordered_list;
pub mod upper;
pub mod urlencode;
pub mod urlize;
pub mod urlizetrunc;
pub mod utc;
pub mod wordcount;
pub mod wordwrap;
pub mod yesno;

pub use host::{
    DecimalParse, DecimalTuple, FilterCall, FilterHost, NoFilterHost, NumberFormatArgs,
    ParsedDecimal,
};
// Test-only, like its `#[cfg(test)]` in `host.rs`: a filter module's `mod tests`
// reaches it, and nothing in a real build should be able to.
#[cfg(test)]
pub use host::TestFilterHost;
pub use value::{Value, ValueId};

/// The three flags Django records per filter, by the names it records them under.
///
/// Four shapes cover all 57 — `NONE` (19), `SAFE` (29), `SAFE_AUTOESCAPE` (7) and
/// `LOCALTIME` (2) — which is why the constants exist rather than 57 literals.
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
/// and a filter cannot capture anything — Django's filters are functions of
/// `(value, arg)`.
pub type FilterFn = fn(Value, Option<Value>, &FilterCall<'_>) -> Result<Value, Error>;

/// One filter: what it is called, what Django says about it, and what it does.
pub struct FilterEntry {
    pub name: &'static str,
    pub flags: FilterFlags,
    /// Whether Django's body carries `@stringfilter` (`defaultfilters.py:38-52`).
    ///
    /// Structural, and deliberately **not** on [`FilterFlags`], which is Django's
    /// three measured attributes and nothing else — the flag round-trip in this
    /// file compares those three against the pinned registry, and a fourth field
    /// riding along would make that comparison a statement about us instead.
    ///
    /// The FFI applies the wrapper **once, before the body**: Django rebinds
    /// `first = str(first)` and marks the result safe iff the **coerced** value is
    /// `SafeData` and the inner `is_safe` is true — a test that differs from
    /// `base.py:816`'s, which asks about the value the filter was handed. The two
    /// agree on every `str`/`str` subclass and diverge on a lazy `__proxy__`. A body
    /// that reads the value's *safety* therefore reads the coerced one, which is
    /// what makes `cut` (`defaultfilters.py:445`) writable at all; `stringfilter:
    /// true` is the statement that a body may branch on that.
    pub stringfilter: bool,
    pub run: FilterFn,
}

/// The 57 built-ins plus the nine `django.templatetags` library filters, ascending
/// by name — **66 entries in one table**.
///
/// **One table, not two, and that is measured rather than assumed.** The Python
/// seam is `names.get(func)` — the per-expression name table built from
/// `django_own_filters()` — keyed on the **function object**
/// (`python/django_rust_templates/engine/base.py:889`), so a library filter's handle
/// is in the map only once `{% load %}` registered it. A second Rust filter table
/// would be a routing distinction the seam does not make. The nine are appended *in
/// place*, so the ascending order [`MEASURED_NAMES`] pins is one order over the whole
/// table rather than two concatenated ones.
static REGISTRY: &[FilterEntry] = &[
    FilterEntry {
        name: "add",
        flags: FilterFlags::NONE,
        run: add::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "addslashes",
        flags: FilterFlags::SAFE,
        run: addslashes::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "capfirst",
        flags: FilterFlags::SAFE,
        run: capfirst::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "center",
        flags: FilterFlags::SAFE,
        run: center::run,
        stringfilter: true,
    },
    // `is_safe` is **absent**, not `False`, on the real filter — see the module
    // header; `tests/test_filter_inventory.py` asserts the difference.
    FilterEntry {
        name: "cut",
        flags: FilterFlags::NONE,
        run: cut::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "date",
        flags: FilterFlags::LOCALTIME,
        run: date::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "default",
        flags: FilterFlags::NONE,
        run: default::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "default_if_none",
        flags: FilterFlags::NONE,
        run: default_if_none::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "dictsort",
        flags: FilterFlags::NONE,
        run: dictsort::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "dictsortreversed",
        flags: FilterFlags::NONE,
        run: dictsortreversed::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "divisibleby",
        flags: FilterFlags::NONE,
        run: divisibleby::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "escape",
        flags: FilterFlags::SAFE,
        run: escape::run,
        stringfilter: true,
    },
    // `is_safe` is **absent**, not `False`, on the real filter — see the module
    // header; `tests/test_filter_inventory.py` asserts the difference.
    FilterEntry {
        name: "escapejs",
        flags: FilterFlags::NONE,
        run: escapejs::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "escapeseq",
        flags: FilterFlags::SAFE,
        run: escapeseq::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "filesizeformat",
        flags: FilterFlags::SAFE,
        run: filesizeformat::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "first",
        flags: FilterFlags::NONE,
        run: first::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "floatformat",
        flags: FilterFlags::SAFE,
        run: floatformat::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "force_escape",
        flags: FilterFlags::SAFE,
        run: force_escape::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "get_digit",
        flags: FilterFlags::NONE,
        run: get_digit::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "iriencode",
        flags: FilterFlags::SAFE,
        run: iriencode::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "join",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: join::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "json_script",
        flags: FilterFlags::SAFE,
        run: json_script::run,
        stringfilter: false,
    },
    // --- the four `i18n` language filters -------------------------------------
    // `django/templatetags/i18n.py` registrations, in the one registry for the
    // reason the module doc gives. All `NONE`, `stringfilter: false`;
    // `language_bidi` returns a bool, not text.
    FilterEntry {
        name: "language_bidi",
        flags: FilterFlags::NONE,
        run: language_bidi::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "language_name",
        flags: FilterFlags::NONE,
        run: language_name::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "language_name_local",
        flags: FilterFlags::NONE,
        run: language_name_local::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "language_name_translated",
        flags: FilterFlags::NONE,
        run: language_name_translated::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "last",
        flags: FilterFlags::SAFE,
        run: last::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "length",
        flags: FilterFlags::NONE,
        run: length::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "linebreaks",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: linebreaks::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "linebreaksbr",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: linebreaksbr::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "linenumbers",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: linenumbers::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "ljust",
        flags: FilterFlags::SAFE,
        run: ljust::run,
        stringfilter: true,
    },
    // --- the two `l10n` filters ---------------------------------------------
    // `l10n.py`'s only two registrations, both `is_safe=False` (`l10n.py:7,15`):
    // it is what keeps `base.py:816` from marking a `SafeString` input's result safe.
    FilterEntry {
        name: "localize",
        flags: FilterFlags::NONE,
        run: localize::run,
        stringfilter: false,
    },
    // One of the three `tz` names over the transcribed `do_timezone` (`tz.py:38-84`).
    FilterEntry {
        name: "localtime",
        flags: FilterFlags::NONE,
        run: localtime::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "lower",
        flags: FilterFlags::SAFE,
        run: lower::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "make_list",
        flags: FilterFlags::NONE,
        run: make_list::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "phone2numeric",
        flags: FilterFlags::SAFE,
        run: phone2numeric::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "pluralize",
        flags: FilterFlags::NONE,
        run: pluralize::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "pprint",
        flags: FilterFlags::SAFE,
        run: pprint::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "random",
        flags: FilterFlags::SAFE,
        run: random::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "rjust",
        flags: FilterFlags::SAFE,
        run: rjust::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "safe",
        flags: FilterFlags::SAFE,
        run: safe::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "safeseq",
        flags: FilterFlags::SAFE,
        run: safeseq::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "slice",
        flags: FilterFlags::SAFE,
        run: slice::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "slugify",
        flags: FilterFlags::SAFE,
        run: slugify::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "stringformat",
        flags: FilterFlags::SAFE,
        run: stringformat::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "striptags",
        flags: FilterFlags::SAFE,
        run: striptags::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "time",
        flags: FilterFlags::LOCALTIME,
        run: time::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "timesince",
        flags: FilterFlags::NONE,
        run: timesince::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "timeuntil",
        flags: FilterFlags::NONE,
        run: timeuntil::run,
        stringfilter: false,
    },
    // The `timezone` name is `tz.py`'s `do_timezone` under a registered alias
    // (`tz.py:37-38`).
    FilterEntry {
        name: "timezone",
        flags: FilterFlags::NONE,
        run: timezone::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "title",
        flags: FilterFlags::SAFE,
        run: title::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "truncatechars",
        flags: FilterFlags::SAFE,
        run: truncatechars::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "truncatechars_html",
        flags: FilterFlags::SAFE,
        run: truncatechars_html::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "truncatewords",
        flags: FilterFlags::SAFE,
        run: truncatewords::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "truncatewords_html",
        flags: FilterFlags::SAFE,
        run: truncatewords_html::run,
        stringfilter: true,
    },
    // `l10n`'s second filter — `localize` with the flag forced the other way.
    FilterEntry {
        name: "unlocalize",
        flags: FilterFlags::NONE,
        run: unlocalize::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "unordered_list",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: unordered_list::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "upper",
        flags: FilterFlags::NONE,
        run: upper::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "urlencode",
        flags: FilterFlags::NONE,
        run: urlencode::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "urlize",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: urlize::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "urlizetrunc",
        flags: FilterFlags::SAFE_AUTOESCAPE,
        run: urlizetrunc::run,
        stringfilter: true,
    },
    // The third `tz` name over the one transcribed `do_timezone` (`tz.py:29-34`).
    FilterEntry {
        name: "utc",
        flags: FilterFlags::NONE,
        run: utc::run,
        stringfilter: false,
    },
    FilterEntry {
        name: "wordcount",
        flags: FilterFlags::NONE,
        run: wordcount::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "wordwrap",
        flags: FilterFlags::SAFE,
        run: wordwrap::run,
        stringfilter: true,
    },
    FilterEntry {
        name: "yesno",
        flags: FilterFlags::NONE,
        run: yesno::run,
        stringfilter: false,
    },
];

/// The entry for `name`, if the engine has one.
///
/// A linear scan of 66: the table is small and the lookup is per filter call, so a
/// `HashMap` behind a `OnceLock` would add a synchronisation point to buy nothing.
pub fn filter(name: &str) -> Option<&'static FilterEntry> {
    REGISTRY.iter().find(|entry| entry.name == name)
}

/// Every filter name, ascending.
pub fn filter_names() -> Vec<&'static str> {
    REGISTRY.iter().map(|entry| entry.name).collect()
}

/// The filters whose body carries Django's `@stringfilter`. The registry field's
/// only reader: the FFI exposes it so `tests/test_filter_inventory.py` can compare it
/// against the 29 names it derives from the pinned `defaultfilters.py`'s AST, because
/// nothing in this file can disagree with its own transcription.
pub fn stringfilter_names() -> Vec<&'static str> {
    REGISTRY
        .iter()
        .filter(|entry| entry.stringfilter)
        .map(|entry| entry.name)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::error::ErrorKind;

    /// The **57 built-in** names, spelled out.
    ///
    /// Spelled out, not counted: the set is what a later milestone diffs against
    /// Django's own registry, and a length cannot name the filter that went missing.
    /// The nine library filters are a separate measured set ([`LIBRARY_NAMES`]) from
    /// a separate registry (the five `django.templatetags` libraries); the registry
    /// they both live in is asserted below as their ordered union.
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

    /// The **nine library filter names**, spelled out.
    ///
    /// The five pinned `django.templatetags` libraries register exactly these nine
    /// `register.filter` entries: `i18n.py`'s four `language_*` (`i18n.py:292-310`),
    /// `l10n.py`'s two (`l10n.py:7,15`), and `tz.py`'s three names over one Python
    /// `do_timezone` (`tz.py:19-38`). Spelled out for [`MEASURED_NAMES`]'s reason: a
    /// length-only guard cannot name the one that went missing, and the
    /// registry-coverage row in `tests/test_interposition.py` diffs the *whole*
    /// registry against `django_own_filters()`.
    ///
    /// **The three `tz` names are `ours/theirs` at the seam**, not three Rust bodies:
    /// `timezone` *is* the transcribed Python `do_timezone` and the other two are
    /// two-line calls into it. The list does not distinguish them — routing is what it
    /// measures, and all three route to Rust.
    const LIBRARY_NAMES: [&str; 9] = [
        "language_bidi",
        "language_name",
        "language_name_local",
        "language_name_translated",
        "localize",
        "localtime",
        "timezone",
        "unlocalize",
        "utc",
    ];

    /// The filters whose bodies are still stubs: the 57 minus every name a landed
    /// group already answers — **empty, because M4's last group has landed**.
    ///
    /// Deliberately no count in this doc; the array's length is the count, and the
    /// assertion below fails a stale entry. A body reverted to a stub would be
    /// `unbuilt` and undeclared, so the guard still fires.
    const STILL_STUBBED: [&str; 0] = [];

    #[test]
    fn the_registry_is_the_measured_66() {
        // The union **as one ascending sequence**, not two concatenated lists:
        // the two sets interleave (`language_*`/`last`, `ljust`/`localize`,
        // `timeuntil`/`timezone`), so a registry that appended the nine after
        // `yesno` would fail here — which is the point, since `filter()` scans
        // the registry and its doc claims one ascending order.
        let mut expected: Vec<&str> = MEASURED_NAMES
            .iter()
            .chain(LIBRARY_NAMES.iter())
            .copied()
            .collect();
        expected.sort_unstable();
        assert_eq!(
            filter_names(),
            expected,
            "the filter registry is not the measured 57 built-ins plus the nine \
             library filters; §10.3 gives M4 the 57, M12 Task 7 adds the nine, \
             and each list is the one a later milestone diffs against its own \
             Django source",
        );
    }

    /// The registry's flags against the measured table, filter by filter.
    ///
    /// A separate assertion from the name list because a missing row and a wrong
    /// flag are different bugs. The expected set is spelled out here rather than
    /// derived from the same four constants [`REGISTRY`] uses, or this test would
    /// agree with any constant the registry picked — and that independence is what
    /// licenses the constants at all: a single wrong constant would be wrong in every
    /// row that shares it.
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
    /// Matched on the **kind and the name**, not on "it returned `Err`": a real
    /// filter given `Value::None` raises, so an implemented filter could look
    /// stubbed, and a stub body that named the wrong filter would look implemented.
    /// Matching the name makes that second case a failure here.
    fn stub_error(name: &str, error: &Error) -> bool {
        matches!(
            &error.kind,
            ErrorKind::Unimplemented(message) if message.contains(&format!("'{name}'")),
        )
    }

    /// Every body is a stub, and this is what makes each group's deliverable
    /// visible as it lands.
    ///
    /// **The commit that lands a group moves that group's names out of
    /// [`STILL_STUBBED`]** in the same commit. The assertion names each side, because
    /// the two failures mean opposite things — a name still a stub but no longer
    /// declared means the list was trimmed too early, and a declared name already
    /// implemented means the group landed without saying so.
    #[test]
    fn every_registry_entry_is_a_stub_at_m4() {
        let call = FilterCall {
            host: &NoFilterHost,
            autoescape: true,
            use_tz: false,
        };
        let stubbed: Vec<&str> = REGISTRY
            .iter()
            .filter(|entry| {
                // A body whose **first** host interaction is one of the trait's
                // non-`Result` methods meets `NoFilterHost`'s `refused_panic` before
                // it can return anything — `is_tuple` for `stringformat`, `mark_safe`
                // for `safe`/`safeseq`, `is_safe` for `linebreaks` and `urlize`/
                // `urlizetrunc` (measured). That is the designed refusal, **not a
                // stub**: a stub returns `unimplemented_filter` without touching the
                // host. A panic is caught rather than propagated because a panic has
                // already answered this test's question.
                //
                // **This makes the test depend on unwinding.** Under `panic =
                // "abort"` it would die by signal instead of reporting.
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    (entry.run)(Value::None, None, &call)
                }));
                match outcome {
                    Ok(Err(error)) => stub_error(entry.name, &error),
                    Ok(Ok(_)) | Err(_) => false,
                }
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
