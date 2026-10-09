# M12 Task 7 — dispatch brief: the nine filters reach Rust

You are implementing **one task** of a milestone in an existing, working repo:
porting Django's template engine to Rust. Everything you need is below; the repo
is at `/Users/pete/projects/lincolnloop/django-rust-templates`.

Pinned truth: Django **6.0.9** at `.django/` (gitignored, **never modify it**).
Interpreter: `.venv/bin/python` (CPython 3.14.4). Rust: edition 2021, MSRV 1.82
— let-chains (`&& let`) are unavailable.

Announce in your final report every number you measured, and do not report a
number you did not measure.

> **Re-keyed at dispatch time, 2026-10-09, against the tree at `0a6727b`.** This
> brief was written while Task 6b was still in flight; every line citation in it
> was re-read against the tree as it stands now and all still land where they
> say (`.django/` is pinned and did not move; `filters/mod.rs:193,679,852`;
> `host.py:216`; `registry.rs:236`; `filters/host.rs:1608,1709,1842`;
> `_impl/base.py:829` and its `:820-821` "decided by handle identity";
> `_impl/parser.py` — see trap 7's note). The one number that moved is
> `PINNED`, 426 → **432** (6b added six rows); it was corrected above. The
> `.django/` re-reads the brief already carries (§3's gate-coverage bullet,
> trap 3's `localize`/`localtime`/`timezone` counts) were re-measured at
> `ec47f4f` and still hold.

---

## 1. The task

The five `django.templatetags` libraries are interposed by Tasks 3–6, so their
*filters* are already transcribed Python whatever happens here. **This task is
Rust bodies and the routing that reaches them, and nothing else.**

**Nine names, all `register.filter` entries in the five pinned files — measured,
not read off the census:**

| name | file:line |
|---|---|
| `language_name` | `i18n.py:292-294` |
| `language_name_translated` | `i18n.py:297-300` |
| `language_name_local` | `i18n.py:303-305` |
| `language_bidi` | `i18n.py:308-310` |
| `localize` | `l10n.py:7-12` |
| `unlocalize` | `l10n.py:15-21` |
| `localtime` | `tz.py:19-26` |
| `utc` | `tz.py:29-34` |
| `timezone` | `tz.py:37-38`, which is `do_timezone` under a registered alias |

**`do_timezone` is already decided, and it did not go the way the plan expected.**
The plan (`…m12…libraries.md:368`) calls `do_timezone` "the helper
`localtime_tag` and `timezone_tag` call in Task 4". Task 4 measured
the three tag *bodies* calling it **zero** times — the two callers are the
`localtime` and `utc` **filters** (`tz.py:26`, `tz.py:34`) — and committed that as
its decision 1 in `f89a9d4`. So:

- `do_timezone` (`tz.py:38-84`) stays **Python-side, under the seam**, transcribed
  verbatim by Task 4.
- `timezone` (the filter) *is* `do_timezone`, so its "Rust body" is a shim that
  reaches the transcribed Python helper — **`ours/theirs` at the seam**, and it
  must be recorded as such rather than counted as ninth-of-nine. The commit
  message says so.
- `localtime` and `utc` are two lines each (`do_timezone(value, tz)`) over the
  same helper, so if you write Rust bodies for them they carry the same seam.

Read `f89a9d4`'s full message before you start; it also names the three filters
as Task 7's and says why no Rust bodies were written for them there.

## 2. Deliverables

1. **The Rust bodies** — `crates/template-core/src/filters/`, one file per filter
   or one for the group, following the shapes already there (`date.rs` is the
   closest neighbour for a body that delegates to the host; `escape.rs` for one
   that returns a marked value).
2. **`django_own_filters()` widened** in `python/django_rust_templates/host.py`
   to cover the five libraries' `register.filters`. The plan says this is "the
   whole change" and that `_impl/base.py`'s filter seam should not need touching
   — **verify that rather than assume it**, and if it does need touching, say so.
3. **The nine names in the Rust filter registry**, and out of `STILL_STUBBED`
   if M11's `crates/template-core/src/filters/mod.rs` still carries a list — it
   is `[]` today (`:679`, an empty array; the row that reads it is `:852`), so
   there is most likely nothing to remove. Say which you found.
4. **Whatever the Host trait needs for the three `tz` filters**, decided against
   the tree rather than guessed: `crates/template-core/src/filters/host.rs`
   already declares ~150 `fn`s and every one has a refusal default, with
   `refused_result_operations()` (`:1608`) and
   `the_refusal_list_covers_every_result_operation` (`:1709`) enforcing that a
   new method cannot be added without joining the list. There is also
   `the_two_hosts_refuse_in_the_same_words` (`:1842`). Say in the commit which
   method you added (or which existing one you reused) and that the refusal list
   moved with it.
5. The tables: `tests/milestone_ownership.py` if it tracks filters,
   `tests/test_shim.py` prose, `tests/test_collected_tests.py`'s `PINNED`
   (line 193 at `0a6727b`, value **432** — re-find it rather than trusting the
   line number, since every task that adds a row moves it),
   the routing instrument's filter lines, and `scripts/anchors.txt` for every new
   citation (run the tool's own repair path; never hand-edit a line number).

## 3. Rows to write

- **The parity corpus for the nine** — `tests/filter_cases.py`'s neighbours,
  each `expected` verified against Django as well as against the engine (the M4
  discipline). Do not round-trip `tests/filter_cases.py` through
  `splitlines()`/`join()` — it holds a `U+2028`/`U+2029` pair.
- **The registry-coverage row** — all nine ours.
- **The stale-map control** — see §4.
- **The gate-covered four**, with Django's own exact outputs:
  `template_tests/syntax_tests/i18n/test_filters.py` is 3 rows of the 1547 and
  its first two assert `language_name`, `language_name_local`, `language_bidi`
  and `language_name_translated` by exact output — `"Hungarian Magyar False
  Hungarian"`, and `"Hungarian Magyar False maďarsky"` under
  `translation.override("cs")`. The routing must be right for the suite to stay
  green, and these are the rows that say so.

  *Re-read in the pinned tree at dispatch time (`.django/` @ 6.0.9): accurate.
  `I18nFiltersTests` has exactly three methods — `test_i18n32` (literal `"hu"`),
  `test_i18n33` (a `langcode` variable, `"Dutch Nederlands False nizozemsky"`
  under `cs`) and `test_i18n38_2`, which is `get_language_info_list` and the
  dict's own `l.name_translated` — so two rows exercise the filters, the third
  the Task 6a tag.*

## 4. Traps

1. **The M11 lesson, unchanged, and this task's risk:** the filter map must be
   **keyed on the registry object it was built from**, not on emptiness. A map
   built from a replaced registry is stale in the one way nothing notices —
   every filter falls through to the Python loop and the whole 1547 stays green.
   The stale-map control is that row.
2. **The plan's first draft cut this task for the wrong reason** — *"the 140 gate
   rows are all tags, so the filters buy no gate coverage"*. They do: 3 rows, and
   four names. Two right numbers to two different questions. If you find yourself
   reasoning about coverage, measure the row count yourself.
3. **`localtime`, `utc` and `timezone` are three names over one body.** Two of
   them are two-liners over `do_timezone`; the third *is* it. Three Rust bodies
   that each reimplement `do_timezone` would be three chances to diverge from a
   Python function the suite does not exercise (`unlocalize` appears once in
   `tests/i18n/`, outside the suite; `localize`, `localtime`, `utc` and
   `timezone` appear nowhere in the 1547). Prefer one seam, not three.

   *Re-measured 2026-10-09 at `ec47f4f`, because this is the trap's whole
   premise:* word-boundary greps over `.django/tests/template_tests/` (the 1547)
   find `localize` 0, `unlocalize` 0, `utc` 0, `localtime` 1 and `timezone` 9 —
   and every one of those ten is the **`django.utils.timezone` module**
   (`from django.utils import timezone`, `timezone.localtime(...)`,
   `timezone.get_fixed_timezone(...)`) in `filter_tests/timezone_utils.py`,
   `test_time.py` and `test_date.py`. The template *filters* are exercised zero
   times. The one near-miss is `filter_tests/test_floatformat.py:130`'s
   `def test_unlocalize`, which is a method name and tests `floatformat`'s `u`
   suffix, not the filter.
4. **All four `language_*` return plain `str` — none is marked safe.** Checked in
   the pinned file rather than assumed (`i18n.py:290-310`): `language_name`
   returns `get_language_info(lang_code)["name"]`, `language_name_local` and
   `language_bidi` the same dict's `name_local`/`bidi`, and
   `language_name_translated` runs the English name through
   `translation.gettext`. There is no `mark_safe` and no `SafeString` — so the
   result escapes at render time, and a Rust body that returned a marked value
   would be *wrong* while rendering identically for every ASCII language name in
   the corpus. `language_bidi` returns a **bool**, not a string.
5. **`localize`/`unlocalize` are `is_safe=False`** (`l10n.py:7,15`), which is a
   registration property and not decoration — it decides escaping at render
   time. If the Rust registry carries per-filter flags, this is where they land.
6. **A negative fixture naming a real filter expires** the moment this lands.
   Fabricated names carry the `unregistered_` prefix.
7. **Do not add a second Rust filters table — and if you conclude you need one,
   say so in your report instead of adding it quietly.** The nine go into the
   existing `filters::REGISTRY` (`crates/template-core/src/filters/mod.rs:193`,
   `&[FilterEntry]`, 57 entries today, alongside `FilterFlags` and the
   `stringfilter` bit). The tag side has two tables (`registry.rs`'s `REGISTRY`
   and `LIBRARY_REGISTRY`, `:236`) because a library *tag* is not "ours" until
   `{% load %}`ed; filters do not, **measured at `ec47f4f`**: the Python seam is
   `_impl/base.py:829`'s `django_own_filters().get(func)` — keyed on the function
   **object**, so a library filter's handle exists only once `{% load %}`
   registered it and "loaded" is already implied by the key being there at all
   (`:820-821`, "decided by handle identity"). Adding rows is the whole Rust-side
   change. If your reading of the tree contradicts this, that is the first thing
   to put in your report.

## 5. Order of work

1. Re-run the routing instrument
   (`.review/m12-measurements/m12_measure_tag_routing.py`, invocation in its
   header — the shim on `PYTHONPATH`, **not** `-I`) and record its filter lines
   before you change anything.
2. Write the failing rows. Run and watch them fail.
3. Implement the four `i18n` filters (pure lookups into
   `django.utils.translation`) — the plan allows them as one commit; the three
   `tz` and two `l10n` ones each delegate to a subsystem M12 does not port, so
   each says in its `# TRANSCRIBED:` marker what it delegates to.
4. Widen `django_own_filters()`, then re-run the instrument: its filter lines
   must read `ours=True` for all nine, and the *tag* lines must be unchanged.
5. Full gate, then commit.

Commit subject: one per group, or one for the set if the routing change is
shared. The message names the nine and states plainly if `do_timezone` stayed
Python-side (it did). End with:

```
Co-Authored-By: Claude Code <noreply@anthropic.com>
```

## 6. Hard constraints

- **`.django/` is pinned and read-only.** Never write into it.
- **Never `git checkout` to undo a mutation** — the work is uncommitted and
  `checkout` destroys it. Restore a file with `git show HEAD:<path> > <path>`.
- Source `scripts/env.sh` (`PIP_CONFIG_FILE=/dev/null`; the global pip config is
  broken — never edit it).
- Any script needing the shim on `PYTHONPATH` must not use `python -I` (`-I`
  implies `-s`/`-E`); any script needing `-I` must add `sys.path` by hand. The
  shim directory is `scripts/shimpath`, **not** `<root>/shimpath`.
- The gate is `bash scripts/check.sh` → `all checks passed`, Django's suite
  staying `Ran 1547 tests … OK (skipped=6)`.
- **Do not commit anything else.** Do not amend earlier M12 commits.
- **Other agents share this tree.** Leave uncommitted changes you did not make
  alone, and say so in your report. A measurement taken while another dispatch is
  in flight is that dispatch's work in progress, not a landed commit — label
  which tree yours came from.

## 7. What to report back

Every number measured in your own run:

- the commit hash and subject;
- the routing instrument's tag **and** filter tallies, before and after;
- the meter (`unshimmed()`, `unshimmed(scope="django.templatetags")`) before and
  after — the *second* one is the one this milestone moved;
- the Host method you added or reused, and the refusal-list row that moved with it;
- the five gate numbers: cargo, unit suite `Ran N tests … OK`, anchors
  (`occurrences`, `cited of N`, `failures`, `unquoted`, `name-only`),
  `differential`, Django's `Ran 1547 … OK (skipped=6)`, and
  `engine installed; N module(s) still served by Django`;
- whether `_impl/base.py`'s filter seam needed touching;
- anything in this brief the tree contradicts, named as such.
