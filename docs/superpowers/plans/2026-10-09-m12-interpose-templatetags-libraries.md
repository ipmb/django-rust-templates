# M12 Implementation Plan — interpose the five `django/templatetags` libraries

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `django.templatetags.{i18n, l10n, static, tz, cache}` resolve to modules this project provides, their 18 registered tags are compiled by Rust, and their 9 filters run Rust bodies — so the gate rows that have run Django's code since M1 run ours: **140 from the five libraries' tag corpus**, plus **`syntax_tests/i18n/test_filters.py`'s 3 rows**, which assert four of the nine filters by exact output under a `translation.override`. **The 140 counts tag rows only**, and treating it as the milestone's whole gate population is a mistake this plan made in its first draft — see Task 7.

**Architecture:** The M9-M11 shape, one namespace over. The five modules are transcribed into `_impl/templatetags/` with declared regions; the shim's namespace mapping grows from one root to an explicit table; `ShellHost.django_own_tags()`/`django_own_filters()` are widened to the five modules so the identity escape in `has_tag`/`resolve` keeps meaning "is this callable still the library's own"; Rust gains 18 tag compile functions emitting `NodeDesc`s and 9 filter bodies, and `_impl/parser.py`'s `NODE_CLASSES` gains the 15 classes.

**Tech Stack:** Rust (`template-core`, no PyO3 in tests), Python 3.14 transcriptions, the `_shim.py` interposition harness, `scripts/check_transcriptions.py` as the transcription guard, the pinned `.django/` @ 6.0.9.

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — §10.3's M12 row, §7's census ("15 more node classes … 47 in total"), §6's framework-coupling test, §1 and §10.1 for the end state. Recon: `.review/m12-recon.md`, instruments in `.review/m12-measurements/`.

## Global Constraints

- `.django/` is pinned at **6.0.9**, is gitignored, and is **never modified**.
- `bash scripts/check.sh` exits 0 with `all checks passed`, `Ran 1547 tests … OK (skipped=6)`, and prints `every transcription differs from upstream only where it declares`.
- Unit suite is run as `bash scripts/run_unit_tests.sh [args]` — never bare `python -m unittest`.
- `unshimmed()` means exactly what it means today (the `django.template` namespace; **11** at M12's start) and must not be renumbered. The second population gets a second, separately-named count.
- Never `git checkout` to restore a file after a mutation — use `git show HEAD:<path> > <path>` or a `/tmp` copy.
- Never round-trip `tests/filter_cases.py` through `splitlines()`/`join()` (it holds a `U+2028`/`U+2029` pair).
- `python -I` ignores `PYTHONPATH`: scripts needing the shim must not use `-I`; scripts needing `-I` must insert `sys.path` by hand.
- Every commit ends with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## Review Focus

Five input classes the spec implies and no task's tests exercise directly, most likely to bite first. Each gets its test in the owning task.

1. **A `{% load %}` that rebinds a library tag.** `parser.tags.update(lib.tags)` (`base.py:669`) means a caller's library can replace `trans`. The identity escape must let the *caller* win — the M11 filter rule restated for tags. Owner: Task 2.
2. **A caller who registers a library tag name in `OPTIONS["builtins"]`.** Reaches `has_tag` at construction, before any `{% load %}`. Owner: Task 2.
3. **`{% load %}` of a library whose module is *not* ours** (a third-party library, or a future Django library we have not transcribed). `django.templatetags.<unknown>` must still resolve through the finder's fallback path and must not be claimed as ours. Owner: Task 1.
4. **A node class Rust names that has no materialiser.** `NODE_CLASSES` is an explicit registry so a Rust typo fails loudly; a descriptor naming `BlockTranslateNode` before Task 6 adds it must raise the registry's error, not silently build something else. Owner: each library task.
5. **Import order across the two namespaces.** `django.templatetags.i18n` captures `Node`/`TokenType` from `django.template` at import; if a module executes while Django's template modules are current, it holds the wrong universe. The five are lazy (`{% load %}`-time), so today there is no live path — but our `_impl/templatetags/*.py` will capture too, and `_stale_captures()` skips modules we serve. Owner: Task 1 (the sweep is re-established for the new namespace).

## File structure

| file | change | responsibility |
|---|---|---|
| `python/django_rust_templates/_shim.py` | modify | `NAMESPACES` table replacing the single `TARGET`; `_relative_name`, `_served`, `_real_search_paths`, `install`, `unshimmed(scope)`, `django_modules(scope)`, `shadowing_files` |
| `python/django_rust_templates/host.py` | modify | `django_own_tags()` and `django_own_filters()` widened to the five modules |
| `python/django_rust_templates/_impl/parser.py` | modify | `NODE_CLASSES` gains the 15 library node classes |
| `python/django_rust_templates/_impl/templatetags/__init__.py` | create | the package (upstream's is empty, 0 bytes) |
| `python/django_rust_templates/_impl/templatetags/{cache,static,tz,l10n,i18n}.py` | create | transcriptions |
| `crates/template-core/src/parser/tags/{cache,static,tz,l10n,i18n}.rs` | create | 18 compile functions |
| `crates/template-core/src/filters/{language_name,language_name_local,language_name_translated,language_bidi,localize,unlocalize,localtime,timezone,utc}.rs` | create | 9 filter bodies |
| `crates/template-core/src/parser/registry.rs` | modify | the 18 names added to the Rust-owned set |
| `scripts/check_transcriptions.py` | modify | `DECLARED`/`COMPARED_PATH` for the five, keyed `templatetags/<name>.py` |
| `tests/test_interposition.py`, `tests/test_milestone_ownership.py`, `tests/milestone_ownership.py` | modify | the second population, the new rows |
| `docs/feature-checklist.html`, spec §10.3 | modify | M12's row |

---

### Task 0: Recon (done)

`.review/m12-recon.md` and both instruments are committed. Headline measured: **18 `theirs/theirs`**, 9 filters `ours=False`; **1155 lines, 16 classes, 15 `Node` subclasses, 26 module defs**; registry **18 tags + 9 filters**; gates **140 rows**.

---

### Task 0b: the predicate's stated invariant becomes a row

**Files:**
- Modify: `python/django_rust_templates/host.py` (docstring only)
- Test: `tests/test_interposition.py`

**Proposed by the reviewer against the recon, and measured here before adopting.** `django_own_tags()`'s docstring ends *"Measured: the 25 Rust-owned names are exactly the union of these two tables."* Neither half holds at 6.0.9:

- **The count is 23, not 25** — the sentence predates `_rust_owned` subtracting the two stubs (`host.py:303-305`).
- **The relation is containment, never equality.** Measured: `rust - django` is `[]`, `django - rust` is `['load', 'partial', 'partialdef', 'querystring']`. Those four are carried by the merge without being Rust's, because `has_tag` answers them at `host.py:315` — *before* the merge is consulted.

**This is not hygiene, and it argues against Task 2.** Under a claimed equality the widening looks self-maintaining: if the two sets must be equal, then putting `trans` into Rust's registry obviously forces the merge to widen, and a reader concludes the predicate maintains itself. Under the true containment nothing forces it — which is the inversion Task 2 exists to repair. **The sentence that explains the predicate argues against this plan's central finding**, and it is the most-repeated defect in this project in its purest form: a measurement written when the instrument was designed, which nothing re-reads. `host.py` has carried the wrong number through five milestones.

**And nothing guards it.** No row anywhere relates Rust's registry to `django_own_tags()`; the claim lives only in that docstring. The guard is what makes Task 2's widening load-bearing rather than decorative.

- [ ] **Step 1: Write the failing test** — `TagPredicateCoverageTests` in `tests/test_interposition.py`, beside `FilterRoutingTests`:

```python
class TagPredicateCoverageTests(unittest.TestCase):
    """Rust-owned ⊆ the tag predicate, or `has_tag`'s escape inverts."""

    @classmethod
    def setUpClass(cls):
        cls.host = host_module.ShellHost(render.parser_for("", _hosting_engine()))
        cls.own = host_module.django_own_tags()

    def assert_covers(self, rust_owned, own):
        missing = sorted(rust_owned - set(own))
        self.assertFalse(
            missing,
            f"Rust-owned names the tag predicate does not carry: {missing}. "
            f"`has_tag` answers these with `func is not _django_tags.get(command)`, "
            f"so a name missing from the merge has nothing to be compared against "
            f"and the escape resolves the wrong way — silently, with the suite green.",
        )

    def test_every_rust_owned_name_is_in_the_tag_predicate(self):
        # Read off a real ShellHost rather than recomputed from `_native`:
        # recomputing would be a second account of the set the predicate uses.
        self.assert_covers(self.host._rust_owned, self.own)

    def test_that_row_would_catch_a_name_the_predicate_lacks(self):
        # The control the row above cannot be read without. `trans` is not
        # arbitrary — it is the first name this milestone adds to Rust's
        # registry, so this is the failure Task 2's ordering exists to prevent,
        # staged one task early.
        with self.assertRaises(AssertionError):
            self.assert_covers(self.host._rust_owned | {"trans"}, self.own)
```

- [ ] **Step 2: Run and watch it fail** — `bash scripts/run_unit_tests.sh tests.test_interposition.TagPredicateCoverageTests`. Both rows error on the missing helper.
- [ ] **Step 3: Correct the docstring** — 23, containment, the four names, and *where* they are answered.
- [ ] **Step 4: Run** — both rows green; `bash scripts/run_unit_tests.sh`.
- [ ] **Step 5: Commit.**

---

### Task 1: the shim's second namespace, and the meter's second count

**Files:**
- Modify: `python/django_rust_templates/_shim.py`
- Modify: `tests/test_shim.py`, `tests/test_interposition.py`
- Test: `tests/test_shim.py` (new rows)

**Interfaces:**
- Produces: `_shim.NAMESPACES: tuple[str, ...]`, `_shim.NAMESPACE_ROOTS: dict[str, str]` (namespace → `_impl` subdirectory, `""` meaning flat), `_shim.unshimmed(scope="django.template")`, `_shim.django_modules(scope="django.template")`.
- Consumes: nothing new.

**The design, stated once.** `TARGET` becomes a table, because the two namespaces map into `_impl` differently and the asymmetry is a *fact to state* rather than to smooth over:

```python
# The namespaces this package serves, and where each one's files live under
# `_impl`. The values are `_impl`-relative subdirectories, `""` for a namespace
# served from the root.
#
# **The asymmetry is deliberate.** `django.template` is flat (`_impl/base.py`),
# because M9 built it that way and re-keying it under `_impl/template/` for
# symmetry would move every key in `SHADOWING`, `DECLARED`, `COMPARED_PATH` and
# the anchor manifest, and every recorded line span with them. `django.templatetags`
# is a subdirectory, because `loaders/` taught this tree that a key must carry
# its subdirectory — `loaders/base.py`, not `base.py` — and M10 Task 6 paid for
# learning it after the fact. A stated wart is cheaper than a tidy re-key.
NAMESPACE_ROOTS = {
    "django.template": "",
    "django.templatetags": "templatetags",
}
NAMESPACES = tuple(NAMESPACE_ROOTS)
```

`_relative_name(fullname)` returns an `_impl`-relative posix path for any name in any namespace, and `None` outside all of them:

- `django.template` → `""`
- `django.template.base` → `"base.py"`
- `django.templatetags` → `"templatetags"` (the package directory)
- `django.templatetags.i18n` → `"templatetags/i18n.py"`
- `django.templatetags` is matched **before** `django.template` is tried, and by longest-prefix, so `django.template.foo` cannot be mistaken for a templatetags name or vice versa.

`_ShimFinder` takes `real_paths_by_namespace: dict[str, list[str]]` instead of one list, because `_real_subdirs` needs the *right* namespace's fallback directories — a `django.templatetags` package must fall back to Django's `templatetags/`, not to its `template/`.

`unshimmed(scope)` and `django_modules(scope)` take the namespace; `unshimmed()` keeps its signature and default so every existing caller and doc keeps its meaning. `unshimmed_templatetags()` does **not** exist — a second function would drift from the first (the failure mode this tree keeps hitting: D3′, the vacuity reasons, M10's `origin` row), so it is one implementation over two populations, and the second name is the scope argument.

- [ ] **Step 1: Write the failing test** — in `tests/test_shim.py`:

```python
class NamespaceTests(unittest.TestCase):
    def test_the_two_namespaces_map_into_impl_differently(self):
        self.assertEqual(_shim._relative_name("django.template"), "")
        self.assertEqual(_shim._relative_name("django.template.base"), "base.py")
        self.assertEqual(_shim._relative_name("django.templatetags"), "templatetags")
        self.assertEqual(
            _shim._relative_name("django.templatetags.i18n"), "templatetags/i18n.py"
        )
        self.assertIsNone(_shim._relative_name("django.utils.translation"))
        # A name that only *looks* like the other namespace's prefix.
        self.assertIsNone(_shim._relative_name("django.templateish"))

    def test_the_meter_keeps_its_meaning_and_gains_a_second_population(self):
        self.assertEqual(_shim.unshimmed(), _shim.unshimmed(scope="django.template"))
        self.assertEqual(len(_shim.unshimmed()), 11)  # unchanged by this task
        self.assertEqual(
            sorted(_shim.unshimmed(scope="django.templatetags")),
            [
                "django.templatetags.cache",
                "django.templatetags.i18n",
                "django.templatetags.l10n",
                "django.templatetags.static",
                "django.templatetags.tz",
            ],
        )
```

- [ ] **Step 2: Run it and watch it fail** — `bash scripts/run_unit_tests.sh tests.test_shim.NamespaceTests` → `AttributeError: module … has no attribute 'NAMESPACE_ROOTS'` / `TypeError: _relative_name() got an unexpected keyword`.

- [ ] **Step 3: Implement** — the table, `_relative_name`, `_ShimFinder.real_paths_by_namespace`, `install()`, `django_modules(scope)`, `unshimmed(scope)`, `shadowing_files()` (which must walk both roots), and `_served`. Keep `TARGET` as a module-level alias for `"django.template"` **only if** `tests/` or `scripts/` reference it; otherwise delete it so no second spelling of the root survives — check with `grep -rn "TARGET" python/ scripts/ tests/`.

- [ ] **Step 4: Run the row, then the whole guard set** — `bash scripts/run_unit_tests.sh`, `bash scripts/check.sh --fast`. Both green, `unshimmed()` still 11.

- [ ] **Step 5: Commit** — `M12: the shim serves a second namespace, and the meter gains a scope`.

**Review Focus 3's test, in this task:** `test_an_unknown_templatetags_library_still_resolves` — `django.templatetags.<name we do not serve>` must fall through to the finder's fallback and not be claimed as ours. Written against a name that exists in the pinned tree but has no `_impl` file (there is none today, so the row asserts the *mechanism*: `_served` is False and `_relative_name` names a path with no file).

---

### Task 2: the library names enter the tag predicate

**Files:**
- Modify: `python/django_rust_templates/host.py`
- Test: `tests/test_interposition.py`

**Interfaces:**
- Produces: `django_own_tags()` covering all five modules, carrying each library's own callable under its own tag names — so once Rust owns a name, `has_tag`'s identity clause reads False and Rust compiles Django's tag, and a `{% load %}`ed rebinding still reads True. The 18 names stay absent from Rust's registry until Tasks 3–6 port them, so today `has_tag` reaches them one clause earlier and sends them to the host.
- Consumes: Task 1's namespace table.

**Amended after implementation — `registry.rs` is *not* touched, and the reason is `Produces` read as two questions.** The original `Produces` line said "the 18 names present in `_native.builtin_tag_names_including_stubs()` but **no Rust compile function yet**", which is a forward statement about Tasks 3–6 written as if it were this task's deliverable. Adding 18 stubs to `REGISTRY` here would aggravate the "registry keys ≠ implemented set" misreading `registry.rs` warns about three times, and would force rewrites of `tests/test_tag_inventory.py`'s pinned 25 and `{"partial","partialdef"}` rows — which is not what "the routing table … *unchanged*" (Step 4) can mean. The Steps bind: the widening **alone**, routing unchanged. The unchanged 18-`theirs/theirs` table is the measurement; `registry.rs` and its pinned inventory are untouched by this commit.

**This task is the one the recon's §3 exists for.** Adding the names to Rust's registry *without* widening `django_own_tags()` changes nothing, silently: `_django_tags.get("trans")` is `None`, so `has_tag`'s escape inverts and the tag goes to the host exactly as before, with the 1547 green. The order is: widen the predicate first, prove the tags still route to the host, then port the bodies.

**Why the bodies stay in this milestone, against the reviewer's proposal to split them out.** The reviewer asked for M12 to be transcription only — five files made ours, zero Rust bodies, the widening and the bodies deferred to the milestone that ships the first one — on the grounds that M4 shipped Rust filter bodies seven milestones ahead of the routing that reached them. Both halves of that are answered here rather than waved off:

- **The M4 defect is a shape this plan cannot take.** M4's state was *bodies with no routing*. Here the routing lands first (this task) and the bodies only afterwards, so at no commit in M12 does a Rust body exist that the predicate cannot reach — and the last commit of every library task is the one that turns its routing on. A milestone that passes through the bad state only by being interrupted mid-task is not the same risk as one that opens in it.
- **Splitting would make M12 the only milestone in this tree shaped that way.** M11 is the nearest precedent and is exactly the shape kept here: Task 1 *loader_tags is our module* (transcription alone, because M7 had already ported those bodies), Task 2 **built-in filters reach Rust** (transcription *and* the `FilterExpression.resolve` routing that turns 454 rows from Django's to ours), Task 3 *defaulttags is our module*. M9 and M10 combine the same two. A "transcription-only" M12 would be a new kind of milestone, invented at the end of the sequence, to avoid a risk the ordering already removes.
- **What the reviewer was right about is Task 0b**, and it is adopted: the predicate's stated invariant was false and unguarded, and it argued against this task. That correction is what makes the widening provably necessary rather than merely plausible.

The reviewer's other consequence — that the nine filters are therefore not a separate decision because the unit of transcription is the file — is also adopted, and it is why Task 7 is scoped as **Rust bodies only**. The five files are fully transcribed by Tasks 3-6; a per-file table (`DECLARED`, `SHADOWING`, `COMPARED_PATH`, the anchors) cannot express a half-transcribed file, so "tags now, filters later" was never the choice it looked like.

- [ ] **Step 1: Write the failing test** — `LibraryTagPredicateTests` in `tests/test_interposition.py`, six rows: the coverage-and-identity row below, the measured disjointness of the five libraries from `defaulttags`/`loader_tags`, the merge-order sentinel (a library shadowing `if`, with a control that the library table really reached the merge), the cache tracking a replaced registry, the caller's `template_builtins` versus the parse's own table, and the clause read for a library tag with `"trans"` staged into `_rust_owned` (with an unstaged control showing the staging is what makes the answers differ).

```python
def test_the_predicate_covers_every_registered_library_tag(self):
    # Identity, not presence: `has_tag` asks `func is not _django_tags.get(command)`,
    # so a table carrying a *different* callable under `trans` answers True and the
    # tag stays Django's even after Rust owns it. 18 names, counted.
    for module, tags in sorted(self.libraries.items()):
        for name, func in sorted(tags.items()):
            self.assertIs(own.get(name), func, ...)

def test_the_clause_reads_a_library_tag_the_way_it_will_once_rust_owns_it(self):
    # `"trans"` staged into a *local* host's `_rust_owned`, so the shipped routing
    # is untouched: Django's own `trans` → False, a rebinding library → True, and
    # both True without the staging.
    ...
```

- [ ] **Step 2: Run and watch it fail** — `own.get("trans")` is `None`.

- [ ] **Step 3: Implement** — `django_own_tags()` merges the five registries. Read the modules **lazily inside the function**, not at import, because importing `django.templatetags.i18n` at `host.py` import time executes it before the shim has necessarily installed — the exact `_stale_captures` hazard, and the reason `_CAPTURE_NAMESPACES` already names this namespace. Cache the merged table against the five registry objects the way `django_own_filters()` caches against its source registry.

- [ ] **Step 4: Run** — the predicate rows pass, the routing table from the recon is *unchanged* (still 18 `theirs/theirs`), `check.sh --fast` green. That unchanged table is this task's evidence that the widening alone is behaviour-preserving.

- [ ] **Step 5: Commit** — `M12: the tag predicate covers the five libraries, and changes no routing yet`.

**Review Focus 1 and 2's tests, in this task:** the rebound-tag row and the `OPTIONS["builtins"]` row, both copied in shape from `FilterRoutingTests`' existing pair — a library registering `trans` must win, and a caller's `builtins` must win at construction.

---

### Task 3: `cache` and `static` are our modules

The worked example: two modules, 279 lines, 3 node classes, 4 tag names, 0 filters. It proves the whole path — namespace, transcription guard, `NODE_CLASSES`, Rust compile, `_rust_owned` — on the smallest surface, and every later library repeats this shape with its own inventory.

**Files:**
- Create: `python/django_rust_templates/_impl/templatetags/__init__.py` (empty, matching upstream's 0 bytes), `.../cache.py`, `.../static.py`
- Create: `crates/template-core/src/parser/tags/cache.rs`, `.../static.rs`
- Modify: `_shim.py` (`SHADOWING` gains `templatetags/__init__.py`, `templatetags/cache.py`, `templatetags/static.py`), `_impl/parser.py` (`NODE_CLASSES`), `registry.rs`, `scripts/check_transcriptions.py`

**Interfaces:**
- Produces: `_impl/templatetags/static.py::StaticNode`, `PrefixNode`; `_impl/templatetags/cache.py::CacheNode`; Rust `tags::static::compile` / `tags::cache::compile`.
- Consumes: `NodeDesc` (`crates/template-core/src/parser/desc.rs`), `NODE_CLASSES` (`_impl/parser.py:120`).

The tag bodies, from the pinned source, are the specification. **Spans measured with `ast`, not read off a grep**, and the `NodeDesc` attribute names are the *classes' own kwarg names* — which is not a detail to guess: `PrefixNode` stores `varname` while `NowNode` stores `asvar`, and a descriptor that emits the wrong spelling raises `TypeError` at materialisation rather than building the wrong node.

| tag | pinned body | `NodeDesc` |
|---|---|---|
| `get_static_prefix` | `static.py:60-74`, via `PrefixNode.handle_token(…, "STATIC_URL")` | `PrefixNode`, `varname`, `name="STATIC_URL"` |
| `get_media_prefix` | `static.py:78-92`, via `PrefixNode.handle_token(…, "MEDIA_URL")` | `PrefixNode`, `varname`, `name="MEDIA_URL"` |
| `static` | `static.py:156-171`, via `StaticNode.handle_token` | `StaticNode`, `varname`, `path` |
| `cache` | `cache.py:59-100` | `CacheNode`, `expire_time_var`, `fragment_name`, `vary_on`, `cache_name` |

Constructors, measured signatures: `PrefixNode.__init__(self, varname=None, name=None)` (`static.py:15`), `StaticNode.__init__(self, varname=None, path=None)` (`static.py:98`), `CacheNode.__init__(self, nodelist, expire_time_var, fragment_name, vary_on, cache_name)` (`cache.py:9`). All three are ordinary keyword constructors, so `NODE_CLASSES["PrefixNode"] = PrefixNode` and so on are enough — no `_ifchanged_factory`-style shim is needed here, and the plan says so because `IfChangedNode` is the precedent that makes the question worth asking.

- [ ] **Step 1: Write the failing rows** — the routing row from Task 1's `NamespaceTests` inverted into a positive claim for these four names, plus `NODE_CLASSES` membership rows for the three classes, plus `cargo test -p template-core` unit tests for each compile function (the `now.rs` shape: syntax accepted, syntax rejected, and the arg-slicing edges Django clamps).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Transcribe** the two modules from `.django/django/templatetags/{cache,static}.py` into `_impl/templatetags/`, changing **nothing** except adding the `# TRANSCRIBED:` marker and declaring the Rust seam where the tag body is Rust's.
- [ ] **Step 4: Declare the diff** in `scripts/check_transcriptions.py`, keyed `templatetags/cache.py` / `templatetags/static.py`.
- [ ] **Step 5: Port the four compile functions** to Rust and add the four names to the owned set.
- [ ] **Step 6: Run** — `bash scripts/check.sh` (full, `Ran 1547 tests … OK (skipped=6)`), the routing table now shows `ours/ours` for these four, `bash scripts/run_unit_tests.sh`, `cargo test -p template-core`.
- [ ] **Step 7: Commit** — `M12: cache and static are our modules — the meter's second count is 2`.
- [ ] **Step 8: Update the second count's `PINNED`-style record** in `tests/test_milestone_ownership.py`'s neighbourhood, in this commit, with the delta stated.

---

### Task 4: `tz` is our module

**Files:** create `_impl/templatetags/tz.py`, `crates/template-core/src/parser/tags/tz.rs`; modify the same three tables.

**Inventory:** 200 lines, 4 classes (`datetimeobject` is a `datetime` subclass, **not** a `Node` — the census's 16-vs-15), 3 node classes (`LocalTimeNode`, `TimezoneNode`, `GetCurrentTimezoneNode`), 3 tags (`localtime`, `timezone`, `get_current_timezone`), 3 filters (`localtime`, `timezone`, `utc`).

The three filters are **not** in this task's Rust scope — they are Task 7's, and this task only transcribes their Python bodies. Say so in the task's commit, or the filters' absence reads as an oversight.

Measured spans: `do_timezone` `tz.py:38-84` (47 lines), `localtime_tag` `136-154`, `timezone_tag` `158-178`, `get_current_timezone_tag` `182-200`. The last three are the tag bodies; `do_timezone` is the helper they and `timezone_tag` share, and the plan should decide — in the commit, not by default — whether it becomes Rust's or stays Python's under the seam.

- [ ] **Step 1: Write the failing rows** — the three routing rows (`localtime`, `timezone`, `get_current_timezone` → `ours/ours`), `NODE_CLASSES` membership for `LocalTimeNode`/`TimezoneNode`/`GetCurrentTimezoneNode`, and the Review Focus 4 refusal row described below.
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Transcribe** `.django/django/templatetags/tz.py` → `_impl/templatetags/tz.py`, `# TRANSCRIBED:` marker, `datetimeobject` kept as transcribed (it is a `datetime` subclass, not a node).
- [ ] **Step 4: Declare the diff** in `check_transcriptions.py` under `templatetags/tz.py`.
- [ ] **Step 5: Port the three compile functions** to `crates/template-core/src/parser/tags/tz.rs`, with the `now.rs`-shaped unit tests including the rejected forms Django rejects.
- [ ] **Step 6: Run** — full `check.sh`, unit suite, `cargo test -p template-core`; the routing table must show these three `ours/ours` and the other 15 unchanged.
- [ ] **Step 7: Commit** — `M12: tz is our module — the second count is 1`.
- [ ] **Step 8: Move `tz` from the unshimmed side of the second count**, in the same commit.

**Review Focus 4's test goes here:** a `NodeDesc` naming `BlockTranslateNode` — not yet in `NODE_CLASSES` at this point — must raise `_impl/parser.py:279`'s refusal. Pin the refusal *before* Task 6 removes it, so the registry's loud-failure property has a row while it is still observable.

---

### Task 5: `l10n` is our module

**Files:** create `_impl/templatetags/l10n.py`, `crates/template-core/src/parser/tags/l10n.rs`.

**Inventory:** 60 lines, 1 class (`LocalizeNode`), 1 tag (`localize` — the only tag whose compile function is variadic in what it accepts: `on`/`off`/nothing), 2 filters (`localize`, `unlocalize`, Task 7's).

`localize_tag` (`l10n.py:40-60`, 21 lines) is the milestone's smallest tag body and it is worth stating why it is not the trivial one: it accepts `on`, `off` and nothing, it resolves `settings.USE_L10N`-era behaviour through `django.utils.formats`, and its *render* delegates to a subsystem M12 does not port. The `# TRANSCRIBED:` marker says so, per §6's coupling test.

- [ ] **Step 1: Write the failing rows** — the `localize` routing row (`ours/ours`), `NODE_CLASSES` membership for `LocalizeNode`, and Task 7's two filter names as registry-coverage rows only (bodies are Task 7's).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Transcribe** `.django/django/templatetags/l10n.py` → `_impl/templatetags/l10n.py` with the `# TRANSCRIBED:` marker; the `django.utils.formats` delegation stays Python's and the marker says so.
- [ ] **Step 4: Declare the diff** in `check_transcriptions.py` under `templatetags/l10n.py`.
- [ ] **Step 5: Port `localize_tag`** to `crates/template-core/src/parser/tags/l10n.rs` with `now.rs`-shaped unit tests covering all three accepted forms (`on`, `off`, nothing) **and** the rejected fourth, since "accepts nothing" is the form most likely to be mistaken for "accepts anything".
- [ ] **Step 6: Run** — full `check.sh`, unit suite, `cargo test -p template-core`; one routing row flips, the other 17 unchanged.
- [ ] **Step 7: Commit** — `M12: l10n is our module — the second count is 2`.
- [ ] **Step 8: Move `l10n` off the unshimmed side of the second count**, in the same commit.

---

### Task 6: `i18n` is our module

The largest task in the milestone — 616 lines, 8 node classes, 10 tag names, 4 filters — and it should be dispatched as **two** agents sequentially (they share one tree; worktrees cannot see the gitignored `.django/`):

- **6a: the seven non-translating tags** — the 3 node classes behind `get_available_languages`, `get_language_info`, `get_language_info_list` (`i18n.py:219-289`), the 2 behind `get_current_language`/`get_current_language_bidi` (`314-355`), and `TranslateNode` (`do_translate`, `i18n.py:360-453` — **94 lines**, and it is the one that reads `{% translate … as var %}` and the `noop` marker). Plus `LanguageNode` (`600-616`).
- **6b: `blocktranslate`/`blocktrans`** — `BlockTranslateNode` and `do_block_translate` (`i18n.py:458-596`, **139 lines**), the singular/plural form both with and without a `count`, the `with`/`context`/`trimmed`/`notrimmed` kwargs, and the `{% plural %}` block. This is the largest single compile function in the milestone and the one the gate weighs most: `test_blocktranslate.py` is 47 of the 140 rows.

**Files:** create `_impl/templatetags/i18n.py`, `crates/template-core/src/parser/tags/i18n.rs`.

The 4 filters (`language_name`, `language_name_local`, `language_name_translated`, `language_bidi`) are Task 7's.

- [ ] **Step 1: Write the failing rows for 6a** — the seven routing rows, `NODE_CLASSES` membership for `GetAvailableLanguagesNode`, `GetLanguageInfoNode`, `GetLanguageInfoListNode`, `GetCurrentLanguageNode`, `GetCurrentLanguageBidiNode`, `TranslateNode`, `LanguageNode`, and the `{% translate … as var %}` / `{% translate … noop %}` rows as the two forms the tag body branches on.
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Transcribe** `.django/django/templatetags/i18n.py` → `_impl/templatetags/i18n.py` with the `# TRANSCRIBED:` marker; the `django.utils.translation` delegation stays Python's and the marker says so.
- [ ] **Step 4: Declare the diff** in `check_transcriptions.py` under `templatetags/i18n.py`.
- [ ] **Step 5: Port the seven tag bodies** to `crates/template-core/src/parser/tags/i18n.rs` with `now.rs`-shaped unit tests.
- [ ] **Step 6: Run and commit 6a** — `M12: i18n's seven non-translating tags are ours`.
- [ ] **Step 7: Write the failing rows for 6b** — the `blocktranslate`/`blocktrans` routing rows, `NODE_CLASSES` membership for `BlockTranslateNode`, and the form matrix: singular, plural, `{% plural %}` with `count`, and each of `with`/`context`/`trimmed`/`notrimmed`. This is the milestone's densest test set; write it from `test_blocktranslate.py`'s own cases, not from the tag's prose.
- [ ] **Step 8: Run and watch them fail**, then **port `do_block_translate`** (`i18n.py:458-596`) with its own `cargo test -p template-core` cycle.
- [ ] **Step 9: Run and commit 6b** — `M12: blocktranslate is ours — i18n is complete`. Both halves must be committed separately: a bisect between them should show 6a green and 6b's rows the only failures.

---

### Task 7: the nine filters reach Rust

**Files:** create the nine `crates/template-core/src/filters/*.rs`; modify `_impl/base.py`'s filter seam only if the routing needs it (it should not — `django_own_filters()`'s widening is the whole change), `host.py`.

**Interfaces:**
- Produces: `django_own_filters()` covering the five libraries' `register.filters`; nine Rust bodies; nine names in the Rust filter registry.

**Task 7 means Rust bodies, and only Rust bodies.** The nine filters' *transcription* is not this task's work and cannot be separated from Tasks 3-6: the five files are interposed whole, and no version of interposing `django.templatetags.i18n` leaves its four filters as Django's. This task writes the nine `crates/template-core/src/filters/*.rs` bodies and the `django_own_filters()` widening that routes to them.

**The gate coverage this task buys was mis-stated in the plan's first draft, and the correction is the reason it is a task rather than a footnote.** The draft argued the nine filters could be cut because *"the 140 gate rows are all tags, so the filters buy no gate coverage"*. Measured: they do. `template_tests/syntax_tests/i18n/test_filters.py` is 3 rows in the 1547, and its first two assert `language_name`, `language_name_local`, `language_bidi` and `language_name_translated` by exact output — `"Hungarian Magyar False Hungarian"`, and `"Hungarian Magyar False maďarsky"` under `translation.override("cs")` — so four of the nine are gate-covered and the routing must be right for the suite to stay green. The 140 figure was a count of **tag** rows answering a question about **filters**: two right numbers to two different questions, which is the family this project has paid for five times.

The remaining five — `localize`, `unlocalize`, `localtime`, `utc`, `timezone` — have no row in the 1547 (measured: `unlocalize` occurs once in `tests/i18n/`, outside the suite; the other four appear nowhere). They are ported because the file is the unit of transcription, not because the gate weighs them.

The nine, measured from the pinned files rather than from the census's total: `language_name`, `language_name_translated`, `language_name_local`, `language_bidi` (`i18n.py:293,298,304,309`); `localize`, `unlocalize` (`l10n.py:8,16`); `localtime`, `utc` (`tz.py:20,30`) and `timezone`, which is `do_timezone` under a registered alias (`tz.py:37`).

**`do_timezone` is the one name that appears in two tasks**, and the two must not decide it separately: it is the `timezone` *filter* this task ports, and it is also the helper `localtime_tag` and `timezone_tag` call in Task 4. Task 4's Step 5 says to decide that in the commit; whichever way it goes, this task reads Task 4's commit message before it starts, and if Task 4 left it Python-side then this task's `timezone` filter is a thin Rust shim over a transcribed Python helper — which is `ours/theirs` at the seam and must be recorded as such, not quietly counted as ninth-of-nine.

**The M11 lesson applies unchanged and is the task's risk:** the map must be **keyed on the registry object** it was built from, not on emptiness, or a map built from a replaced registry is stale in the one way nothing notices — every filter falls through to the Python loop and the whole 1547 stays green.

- [ ] **Step 1: Write the failing rows** — the parity corpus for these nine (`tests/filter_cases.py`'s neighbours; each `expected` verified against Django as well as against the engine, the M4 discipline), the registry-coverage row, and the stale-map control.
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement** the nine bodies, one at a time, each with its own `cargo test -p template-core` cycle. The `i18n` four are pure lookups into `django.utils.translation` and may be one commit; `localize`/`unlocalize` and the three `tz` ones each delegate to a subsystem M12 does not port, so each says in its `# TRANSCRIBED:` marker what it delegates to.
- [ ] **Step 4: Run** — full `check.sh`, unit suite, `cargo test`; and re-run the routing instrument, whose filter lines must now read `ours=True` for all nine.
- [ ] **Step 5: Commit** — one per group of filters, or one for the set if the routing change is shared; the commit names the nine and, if `do_timezone` stayed Python-side, says so.

---

### Task 8: Close-out

- [ ] **Step 1:** Spec §10.3's M12 row written from the tree, the way M9-M11's were — every number measured at the close, not carried from this plan.
- [ ] **Step 2:** The M14 end-state wording. Per `.review/m12-recon.md` §5 this is one decision with two parts — M12's second namespace and §2's pending goal question — and it is **the user's**. The plan implements the recommendation (existing count unchanged, second count added, M14 reading on the first and naming the second) and records in the spec both the decision and the fact that it was taken under the standing delegation to decide and report, so the user can overrule it in one place.
- [ ] **Step 3:** `docs/feature-checklist.html` follows the milestone.
- [ ] **Step 4:** The M12 plan's own amendment section, recording what the plan got wrong — the counts this project's plans keep conflating.
- [ ] **Step 5:** Final `bash scripts/check.sh`, and the milestone's commit.

---

## Self-review notes (to be replaced by the amendment section as tasks land)

- **Spec coverage:** §10.3's M12 row → Tasks 3-7; §7's census → Task 3's `NODE_CLASSES` rows; §6's coupling test → each transcription's marker; §1/§10.1's end state → Task 1 and Task 8.
- **Known gap at plan time:** the plan does not yet fix the *order* of Tasks 4-7 beyond "smallest first", which is deliberate — they are independent, and the reviewer or the user may want `i18n` first because it is 102 of the 140 gate rows. State the choice in the task that is picked first.
- **Type consistency:** `NAMESPACE_ROOTS`, `unshimmed(scope)`, `django_modules(scope)`, `django_own_tags()`, `NODE_CLASSES` are the five names every task must spell identically.
