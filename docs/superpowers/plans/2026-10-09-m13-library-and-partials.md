# M13 Implementation Plan — interpose `library`, and compile `partial`/`partialdef`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interpose `django/template/library.py` — the engine's last module, which takes `unshimmed()` from 1 to 0 — and give the last two tag stubs, `partial` and `partialdef`, real compile functions in Rust, which takes `builtin_tag_stub_names()` from 2 to 0 and moves 52 of Django's own test rows onto a route this engine owns.

**Architecture:** Two jobs with different shapes sharing one milestone. `library.py` follows the M9/M11/M12 pattern exactly: a verbatim transcription plus a two-line marker, because the file's Python identity is observable (`isinstance(lib, Library)`, `assertRaises(InvalidTemplateLibrary)`) and Rust owning `parse_bits` would buy no engine behaviour. The partial pair is the opposite: today the *host* compiles both, so every tag inside a partial body is compiled by Django; M13 closes that hole by adding two defaulted `Host` seam methods that let Rust compile both tags while Python keeps `extra_data` — the state the `{% partial %}` lookup closes over. A third, smaller job falls out of the second: the reference table must become the parse's *live* Python list, or a partial body that names an outer `{% cycle %}` slot materialises a second node.

**Tech Stack:** Rust (`template-core`, `template-py` via PyO3) and Python 3.14 (the shell, `django_rust_templates`).

**Spec:** `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` — §5.2 (`ExtensionHost`), §6 (transcription policy), §10.3 (the M13 row and its vacuity rule), §10.4 (`cargo test -p template-core` must stay Python-free).

**Recon:** `.review/m13-recon.md`, with its instruments in `.review/m13-measurements/`. Every count below is that recon's, re-run rather than re-quoted.

## Global Constraints

- Pinned Django tree: `.django/` at **6.0.9**. A citation names the version, never the path — our side moves one commit at a time and a verdict here is dated.
- Acceptance is `bash scripts/check.sh` → exit 0 and `all checks passed`, which includes Django `Ran 1547 tests … OK (skipped=6)`.
- **The acceptance label set is `template_tests` only** (`scripts/run_django_suite.sh:25`). A test in `timezones/` or `i18n/` asserts Django's behaviour and covers us not at all; `.django/tests/template_tests/` is the whole acceptance surface.
- Python floor 3.14 (`test_library.py`'s deferred-annotation rows are `skipUnless(PY314)`); `.venv/bin/python` is CPython 3.14.4.
- No new crates, no new Python dependencies.
- Every transcription opens with `# TRANSCRIBED: <upstream path> (Django 6.0.9)` — line 1, blank line 2, upstream verbatim from line 3 — and is declared in `scripts/check_transcriptions.py`'s `DECLARED` *and* `COMPARED_PATH` (the two key sets are asserted equal).
- `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings` are green at every commit.
- Every `name.py:N` in prose is a claim the anchor manifest re-measures; `--check --frozen` is on the fast path.
- Unit tests run through `scripts/run_unit_tests.sh` (the shim must be installed at interpreter start; a bare `python -m unittest` measures something else).
- Conventional commits, one task per commit, ending with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## Review Focus

The five input classes the spec implies but no existing row exercises, each owned by the task that writes its test. These are the ones most likely to bite a user, most likely first.

1. **A `{% partial %}` that appears *before* its `{% partialdef %}`.** Django's `partial_mapping` is a `DeferredSubDict` — a live view looked up at *render* time — so the order in the source is irrelevant and the template renders. A Rust implementation that read the mapping at compile time would raise `Partial 'p' is not defined in the current template.` on a template Django renders. → **Task 3**.
2. **A duplicate `{% partialdef %}` name.** Django raises `TemplateSyntaxError` at compile time, naming the *origin* template: `Partial 'p' is already defined in the 'x.html' template.` The check is "in template order", so the *first* definition wins and the *second* raises. → **Task 4**.
3. **`{% endpartialdef %}`'s two spellings — and the whitespace that is *not* a third.** Django accepts exactly `endpartialdef` and `endpartialdef p`, with one space and no more. The two checks are different checks on the same token: the scan that decides where the block *ends* matches the **first word** (`base.py:561`, `:564` → our `parser/mod.rs:367-375`, the `pyunicode::split(...).first()` command and the `parse_until.contains` test), while the check that decides whether it ended *legitimately* compares the **whole contents** (`defaulttags.py:1244`). So `{% endpartialdef  p %}` with two spaces — or three, or a tab — stops the scan and is then **refused**, with `Invalid block tag on line 1: 'endpartialdef', expected 'endpartialdef' or 'endpartialdef p'.` Measured on the pinned tree at 6.0.9, all four spellings: an implementation that compares whole contents to *stop* reports `Unclosed tag` for the multi-space form, and one that compares the first word to *validate* silently accepts a template Django rejects. The two failure directions are different errors on a template that never reaches render. Measured, all four spellings, by `.review/m13-measurements/m13_measure_endpartial_spellings.py` — run it rather than quoting the table. → **Task 4**.
4. **A `{% partialdef %}` nested inside a host-compiled block tag's body.** The hole this milestone closes is *per-route*, not per-tag: `Library.simple_block_tag`'s compile function calls Django's `parser.parse`, so a partial defined inside a user tag's body is still Django's work, and `assert_trees_match` over that body compares Django with Django. → **Task 7**.
5. **A partial body that names an enclosing `{% cycle %}`.** Django's one-argument `{% cycle c %}` returns the *same node object* as the `{% cycle … as c %}` that named it — measured, `nodes[0] is nodes[1]` is `True` — and ours is an *index* into a per-parse reference table, so "one slot" only means "one node" if the table is one object for the whole parse. `register_partial_def` materialises a body mid-parse, which is where a snapshot table would split it in two. The output does **not** reveal this: `PartialTemplate.render` pushes a render-context state (`base.py:340-346`), so a named cycle restarts inside every partial render anyway, and Django's own string for the shape is `'aaa'` rather than the `'aba'` a single advancing node would give. The row therefore asserts **identity**, not output. → **Task 5**, which is **conditional on that row failing** — the premise's illustration was falsified twice before the mechanism was understood, so the task ships a comment instead of a change if the row passes.

---

## File structure

| file | change | responsibility |
|---|---|---|
| `python/django_rust_templates/_impl/library.py` | **create** | The transcription of `django/template/library.py`: `Library`, `parse_bits`, `import_library`, `InvalidTemplateLibrary`, the four node classes. Defines its names; never re-exports Django's. |
| `crates/template-core/src/parser/host.rs` | modify | Two new **defaulted** `Host` methods: `partial_node`, `register_partial_def`. |
| `crates/template-core/src/parser/mod.rs` | modify | Two thin `Parser` forwards, for `alloc_slot`'s reason: `Parser::host` is private. |
| `crates/template-core/src/parser/tags/partial.rs` | modify | `{% partial %}`'s body — one message. |
| `crates/template-core/src/parser/tags/partialdef.rs` | modify | `{% partialdef %}`'s body — five match arms, the two source offsets, the body parse. |
| `crates/template-core/src/parser/tags/mod.rs` | modify | `support::PartialHost` — the test host the two tags' own rows run under. |
| `crates/template-core/src/parser/registry.rs` | modify | The stub-set pins shrink from 2 → 1 → 0; the "Flagged, not resolved" note is amended; a Rust-side pin on `unimplemented` replaces the producer that leaves. |
| `crates/template-py/src/lib.rs` | modify | `FilterCheckHost::partial_node` / `::register_partial_def`, plus the live-reference-table change **only if Task 5's identity row fails**. |
| `.review/m13-measurements/m13_measure_cycle_across_partials.py` | **create** | The instrument that decides Task 5: Django's output for four cycle-across-partial shapes, the node-identity fact, and `PartialTemplate.render`'s source. |
| `.review/m13-measurements/m13_measure_endpartial_spellings.py` | **create** | Review Focus 3's instrument: all seven `{% endpartialdef %}` spellings, and the token's first word beside its whole contents. |
| `python/django_rust_templates/host.py` | modify | `ShellHost.partial_node` / `.register_partial_def` — the Python half, including the duplicate check and the error-transport dance `call_tag` already does. |
| `python/django_rust_templates/_shim.py` | modify | `SHADOWING["library.py"] = "transcribed"`. |
| `scripts/check_transcriptions.py` | modify | `COMPARED_PATH["library.py"]` (measured) and `DECLARED["library.py"]`. |
| `tests/milestone_ownership.py` | modify | `library` leaves `OWNERS`, joins `INTERPOSED["M13"]`. |
| `tests/test_milestone_ownership.py` | modify | The meter row becomes `…_is_empty`, with a positive control drawn from the same population. |
| `tests/test_tag_inventory.py` | modify | The stub-set row shrinks twice, then gets its positive control. |
| `tests/test_parser_parity.py` | modify | The `UnimplementedTag` row is reshaped: the class survives, its producer does not. |
| `tests/test_host_tags.py` | modify | The discriminator is replaced by a spy on the seam, plus the reason edits §5(c) of the recon names. |
| `tests/test_partials_rust.py` | **create** | The project-side rows for the seam: the two tags' argument errors, the live mapping, the slot identity, the body route. |
| `docs/feature-checklist.html` | modify | Pete's status page: both meter scopes, the tag/filter counts, re-stamped date. |
| `docs/superpowers/specs/2026-10-08-…-design.md` | modify | §10.3's M13 row, marked landed. |
| `.review/m13-record.md` | **create** | The milestone record, in M9–M12's shape. |

---

### Task 1: Transcribe `library.py`, and take the meter to zero

The engine's last module. `unshimmed()` is one name today — `['django.template.library']` — and this task is the whole of what the meter records.

**Files:**
- Create: `python/django_rust_templates/_impl/library.py`
- Modify: `python/django_rust_templates/_shim.py` (`SHADOWING`, `:169-216`)
- Modify: `scripts/check_transcriptions.py` (`COMPARED_PATH`, `:117-460`; `DECLARED`, `:469-1050`)
- Modify: `tests/milestone_ownership.py` (`OWNERS` `:59-92`, `INTERPOSED` `:207-243`)
- Modify: `tests/test_milestone_ownership.py` (`:151-181`)
- Modify: `tests/test_shim.py` — **three rows read the meter as a pinned literal and all three move**; the draft named this file without listing them, and the gate is what found them. `test_unshimmed_reports_progress` (`:184`, `1` → `[]`) and `test_the_meter_keeps_its_meaning_and_gains_a_second_population` (`:635`, `len(...) == 1` → `0`) are mechanical. The third is not, and it is the interesting one: `test_strict_mode_raises_while_falling_back` (`:518`) asserts that `install(strict=True)` **refuses**, and with `unshimmed()` empty the refusal's premise is gone — the branch is not dead (it fires against any Django holding a module we have not transcribed) but nothing in the pinned tree can trigger it, so the row becomes two: strict install now *succeeds*, and the refusal is re-triggered by appending a directory to `django.template.__path__` — a real lever, since `django_modules` reads `find_spec`, and no monkeypatch
- Modify: `tests/test_collected_tests.py` (`PINNED`, `:236`) — the unit-test count pin, `442` → `443`: one row out, two in, net one
- Test: `tests/test_milestone_ownership.py`, `tests/test_shim.py`, `tests/test_collected_tests.py`

**Interfaces:**
- Consumes: nothing from earlier tasks; this is the plan's first.
- Produces: `django.template.library` resolving to `_impl/library.py` for the five importers measured in the recon §7 (`_impl/__init__.py:54`, `defaulttags.py:44`, `defaultfilters.py:30`, `loader_tags.py:9`, `engine.py:12`), and `unshimmed(TARGET) == []`.

**Why a transcription and not Rust.** The structural reason first: **Rust has no dispatch path to `parse_bits`.** A tag library is Python by definition — `Library.simple_tag` (`library.py:104`), `simple_block_tag` (`:159`) and `inclusion_tag` (`:243`) each build `compile_func` as a closure over the user's Python function, and all three of the calls into `parse_bits` (`:133`, `:214`, `:269`) sit inside such a closure. Rust reaches a tag by name only through `Host::has_tag` then `Host::call_tag`, and the shell's half of that runs Django's *own* registered compile function — `host.py:567` is `parser.tags[command](parser, token)` — which **is** the closure. So the only edge into `parse_bits` is Rust → host → Python → closure, and the closure is Python's; a Rust `parse_bits` would be a second implementation of a function nothing on the Rust side can call.

The project has already decided this question once, one level up, and the reasoning is recorded in `crates/template-core/src/parser/registry.rs:94-102`: `querystring` is registered by `simple_tag` in Django's own tree, so its compile function is *generated*, and the decision written there is that Rust "has no version of that and should not grow one", because a bespoke Rust node of the same name "would fail the parity comparison (which is by class name) while rendering the same output — the worst shape a divergence can take". `library.py` **is** the decorator that generates it. Transcribing the module is that same decision one level down; the alternative is the shape that file already names as the worst.

The measurement then confirms reach rather than carrying the argument: 250 calls to `parse_bits` from 6 test files, every one arriving from a `compile_func` of that shape (§6). One more clause, about consumers the labels cannot see: `.django/django/contrib/admin/templatetags/base.py:3` imports `parse_bits` and `InclusionNode` from this module, and `contrib` is outside the acceptance labels, so a wrong arity would never redden a run. Verbatim is the claim that keeps that consumer correct. The transcription is not an island either: `parse_bits` reads `token_kwargs` (`library.py:9`) and calls `parser.compile_filter` and `parser.error`, and `django.template.base` is already interposed by M9 — so the module rides on what the tree already serves.

- [ ] **Step 1: Measure what the parity harness reaches inside `library.py`**

**Run this after Steps 4 and 5, not here** — the ordering in this plan's first draft was wrong and the instrument says so twice. `m9_measure_parse_path.py` exits with *"`django.template.library` is not ours; this measurement is meaningless"* while the module is still Django's, and `module_of(name)` raises `KeyError` for a name that is not yet in `DECLARED`. So the file must exist and both shim and gate must know about it before the measurement can run; Step 5 below is what it needs, and this step is placed first only because it answers a question Step 5's text depends on.

`COMPARED_PATH` is a *measurement* — the functions a parity harness executes in this module — and clause (4) refuses any declared region that intersects it. Do not write `[]` from the `utils.py` precedent; measure it. **The invocation needs the shim environment**, which the first draft omitted:

```bash
PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" DJANGO_RUST_TEMPLATES=1 \
    .venv/bin/python .review/m9-measurements/m9_measure_parse_path.py library.py
```

Record the printed spans. **The draft predicted "likely empty" and the measurement falsified it — 12 of 18 functions are on the compared path.** The reason is the harness's *setup* rather than its comparison: the corpus's `{% load custom %}` rows run `tests/template_tests/templatetags/custom.py` at import, and that module calls `Library.__init__` and seven `@register.*` decorators, which is where `parse_bits` is entered. That makes `library.py` the second entry in the table (with `loader_tags.py`) whose functions the comparison *runs*, and the entry's comment carries the measurement rather than a guess.

- [ ] **Step 2: Rewrite the meter row to its post-M13 claim, and watch it fail**

`tests/test_milestone_ownership.py`, replacing `test_the_engines_remaining_work_is_one_module` (`:151-181`). The name changes because the claim changes: after M13 the engine's remaining work is *empty*, and a row still called `…_is_one_module` would be a name asserting a set of one.

```python
    def test_the_engines_remaining_work_is_empty(self):
        """The scope correction, closed: the engine's remaining work is none.

        `pending()` is `OWNERS` minus what is taken. M9 through M12 took thirteen
        of the fourteen names this correction left; M13 takes the last one, and
        the meter over the *first* scope reads zero for the first time.

        **The positive control is drawn from this population, not from the
        universe.** `unshimmed() - ours` passes happily on an empty universe:
        if `django_modules(TARGET)` returned nothing, `owned_by_django` would be
        empty, `pending()` would be empty, and every assertion below would be
        green while the instrument measured nothing. So the row states the
        population's members — `NOT_THE_ENGINE` is non-empty, and
        `owned_by_django` is exactly it, so the meter is measuring a set that
        has fourteen names in it (ten declared out of scope, and `library` until
        this commit) rather than one that has none.
        """
        _shim.install()
        owned_by_django = set(_shim.django_modules(TARGET)) - _shim._our_modules_in(TARGET)
        # The control, before the claim: the population the subtraction runs
        # over is non-empty, and the declared set is exactly the difference.
        self.assertEqual(owned_by_django, set(NOT_THE_ENGINE))
        self.assertGreater(len(NOT_THE_ENGINE), 0, "nothing is declared out of scope")
        # The claim.
        self.assertEqual(pending(TARGET), set())
        self.assertEqual(suffixes(_shim.unshimmed(TARGET), TARGET), set())
        self.assertEqual(pending(TEMPLATETAGS), set())
        self.assertEqual(suffixes(_shim.unshimmed(TEMPLATETAGS), TEMPLATETAGS), set())
```

Then: `scripts/run_unit_tests.sh tests.test_milestone_ownership -v`
Expected: **FAIL** — `unshimmed(TARGET)` is `['library']` and `owned_by_django` is `{*NOT_THE_ENGINE, 'library'}`. If it passes, the row is not measuring what it claims and the instrument is wrong, not the tree.

- [ ] **Step 3: Move `library` in the ownership tables and watch the failure move**

`tests/milestone_ownership.py`: delete the `"library": "M13"` entry from `OWNERS` (`:91`, with its comment) and add to `INTERPOSED`:

```python
    # M13's one, and the *last* name in either scope: `unshimmed()` over
    # `django.template` is `[]` from this commit. The scope correction's fourteen
    # names are now ten in `NOT_THE_ENGINE` and four interposed here and above.
    "M13": ["library"],
```

Run: `scripts/run_unit_tests.sh tests.test_milestone_ownership -v`
Expected: **FAIL**, now on `owned_by_django == set(NOT_THE_ENGINE)` — `owned_by_django` still has `library` in it. That is the transcription's absence showing up as a data error, which is the correct order: the table says the file is ours, and the finder cannot serve one that does not exist.

- [ ] **Step 4: Create the transcription**

No docstring of ours, so the marker is a comment on line 1 and the whole of the difference is a two-line insertion — the `utils.py` form, not the `__init__.py` form.

```bash
{ echo '# TRANSCRIBED: django/template/library.py (Django 6.0.9)'; echo; \
  cat .django/django/template/library.py; } \
  > python/django_rust_templates/_impl/library.py
wc -l python/django_rust_templates/_impl/library.py   # 494 = 492 upstream + 2
```

**It must define its names, not re-export them** (§7's rule, and the silent failure it guards). A `from django.template.library import Library` in this file would leave all five importers holding *Django's* objects, `unshimmed()` reading 0, and every `isinstance(lib, Library)` in the suite answering about the wrong class — the meter green and the engine not interposed. Verbatim `cat` is what makes that true by construction; a hand-written file is where it would go wrong.

- [ ] **Step 5: Declare it to the shim and to the transcription gate**

`python/django_rust_templates/_shim.py`, in `SHADOWING` (**alphabetically between `exceptions.py` and `loader_tags.py`** — the draft said "after `loader_tags.py`", and `library.py` sorts before it):

```python
    # M13's module and the last one in this namespace. Verbatim: the pin plus a
    # marker, `None` on the upstream side, and the whole of the difference —
    # `library.py` opens with imports rather than a docstring, so this is the
    # `utils.py`/`smartif.py` form and not `__init__.py`'s. See
    # `check_transcriptions.py` for why a transcription is the right claim here
    # (Rust owning `parse_bits` would buy no engine behaviour and would put a
    # signature behind the seam that `contrib` imports) — measured, `.review/
    # m13-recon.md` §6/§7.
    "library.py": "transcribed",
```

`scripts/check_transcriptions.py` — both tables, because their key sets are asserted equal (`:1250`, `:1258`):

**`COMPARED_PATH` is not `[]`, and the draft's comment saying it is comes from the same falsified prediction Step 1 records.** The measured entry is twelve spans, its comment carries the measurement (`12 of 18`, hand-written 172 / generated 400) and the reason for the reach (the corpus's `{% load custom %}` rows importing `custom.py`), and neither is a value to copy — re-run the Step 1 command and use what it prints.

```python
    "library.py": (
        "django/template/library.py",
        [
            # The marker and its blank separator, ours only — a pure insertion
            # (`None` upstream). Upstream has no docstring here, so the marker is
            # a comment on line 1 and the region is `(1, 2)`.
            #
            # **The file is verbatim, and here that claim is load-bearing in a
            # way it is not for M11's three.** `library.py`'s classes are
            # observed by *identity*: the suite's `assertRaises(
            # InvalidTemplateLibrary)` and `isinstance(lib, Library)` rows are
            # about the object, and `_impl/__init__.py:54`, `defaulttags.py:44`,
            # `defaultfilters.py:30`, `loader_tags.py:9` and `engine.py:12` all
            # bind these names at import. A re-export would satisfy every
            # behavioural row and leave five modules holding Django's objects —
            # measured in `.review/m13-recon.md` §7, where a module-attribute
            # patch on `import_library` read `0` while the bound name read
            # `11050`.
            ((1, 2), None, "# TRANSCRIBED: django/template/library.py (Django 6.0.9)"),
        ],
    ),
```

- [ ] **Step 6: Run the meter row and the transcription gate**

```bash
scripts/run_unit_tests.sh tests.test_milestone_ownership -v
scripts/run_unit_tests.sh tests.test_shim -v
.venv/bin/python scripts/check_transcriptions.py
```

Expected: the meter row PASSES; **`test_shim` fails on its three pinned meter literals, which is Step 6b below**; the transcription gate reports no undeclared difference.

- [ ] **Step 6b: Move the three pinned meter literals in `test_shim.py`, and the unit count**

See this task's Files block. Two are mechanical; `test_strict_mode_raises_while_falling_back` becomes two rows and the reasoning is in its docstring. Then `tests/test_collected_tests.py`'s `PINNED` moves `442` → `443` — **and the two rows are each other's control**, which is why replacing the one row with a pair is better than deleting it: `test_strict_mode_accepts_a_complete_engine` would fail if the staged row's lever were silently inert, and the staged row would fail if the meter were not actually empty.

Run: `scripts/run_unit_tests.sh tests.test_shim tests.test_collected_tests -v`
Expected: PASS, 25 tests (22 + 3).

- [ ] **Step 6c: The review's strict-mode finding — the check runs on both paths**

**This step is in the plan because the plan was wrong about the mechanism, not just about a count.** Step 6b's pair of rows is each other's control, as the sentence above says — and both of them `uninstall()` first, so both call `install(strict=True)` on the path that *performs* an install. That is not the path a real run takes. `scripts/shimpath/sitecustomize.py:33` installs **non-strict, unconditionally** under `DJANGO_RUST_TEMPLATES=1`, so every caller-requested strict install is the second call and takes `install`'s `if installed(): return` — which the check sat *behind*. So in the shipped configuration `strict` could not refuse whatever the meter said.

Measured, with a module staged as in the row above:

```text
uninstall()
sys.modules['django.template'].__path__.append(tmp)   # not_yet_ported.py in tmp
install()                                             # what sitecustomize does
unshimmed() -> ['django.template.not_yet_ported']      # the meter really is non-empty
install(strict=True) -> RETURNED SILENTLY             # ← the defect
```

**Fix:** `_refuse_if_incomplete()` in `_shim.py`, called from both paths — from the already-installed branch when `strict`, and from the tail where the inline check was. Both paths leave the shim installed (deliberately), so a refused install is repeatable: two consecutive `install(strict=True)` calls refuse twice, which is the second half of the fix and what the new row's loop pins.

**The third row**, `test_strict_mode_refuses_under_the_production_ordering`, is the one that pins the *ordering*: it installs non-strict first, asserts the meter is exactly the staged module (the positive control — a staging that silently failed would leave a whole engine and the row would pass for the wrong reason), then asserts the refusal on two consecutive calls. A fix that ran the check only when `installed()` is false passes Step 6b's row and fails this one.

`tests/test_collected_tests.py`'s `PINNED` moves `443` → `444` in the same commit.

Run: `scripts/run_unit_tests.sh tests.test_shim tests.test_collected_tests -v`
Expected: PASS, 26 tests (22 + 3 + 1).

- [ ] **Step 7: Run the acceptance suite — this task's real risk**

The 28 rows of `template_library` and the 166 library-facing rows now exercise **our** `Library`. This is where a transcription that is not verbatim shows up.

```bash
bash scripts/run_django_suite.sh
```

Expected: `Ran 1547 tests … OK (skipped=6)`.

- [ ] **Step 8: Run the whole gate and commit**

```bash
bash scripts/check.sh
git add python/django_rust_templates/_impl/library.py \
        python/django_rust_templates/_shim.py \
        scripts/check_transcriptions.py \
        tests/milestone_ownership.py tests/test_milestone_ownership.py \
        tests/test_shim.py tests/test_collected_tests.py
git commit
```

Message: `scope: the engine's last module — library.py interposed, unshimmed() 1 → 0`

---

### Task 2: The `Host` seam for partials, and the shell's half

The pair's bodies are the only two tags whose compile-time behaviour the host has been providing. Rust needs two things it cannot do itself: build a `PartialNode` over a live view of `parser.extra_data`, and register a partial definition. Both are call-and-return during the parse — the shape `call_tag` already has.

**Files:**
- Modify: `crates/template-core/src/parser/host.rs` (trait at `:22-257`, `NoHost` at `:263-308`)
- Modify: `crates/template-core/src/parser/mod.rs` (forwards beside `alloc_slot` `:179`, `compile_filter` `:198`)
- Modify: `crates/template-core/src/parser/tags/mod.rs` (`support`, `:262-510`)
- Modify: `crates/template-py/src/lib.rs` (`FilterCheckHost` `:1191`, methods `:1304-1500`)
- Modify: `python/django_rust_templates/host.py` (`has_tag` `:480`, `call_tag` `:527`)
- Test: `crates/template-core/src/parser/host.rs` (unit), `tests/test_partials_rust.py` (create)

**Interfaces:**
- Consumes: `Parser::alloc_slot`'s precedent and `Host::check_filter`'s defaulted shape; `NodeDesc::host(NodeRef)`; `nodelist_desc_to_py` / `with_ref_table` (`lib.rs:992`, `:1593`).
- Produces: `Host::partial_node(&self, name: &str) -> Result<NodeRef, Error>` and `Host::register_partial_def(&self, token: &Token, name: &str, inline: bool, body: &NodeListDesc, source_start: Option<usize>, source_end: Option<usize>) -> Result<NodeRef, Error>`; `Parser::partial_node` / `Parser::register_partial_def`; `ShellHost.partial_node` / `.register_partial_def`; `support::PartialHost`.

**The token crosses the seam, and that is not optional.** The shell must run `error = parser.error(token, error)` so the *Python* exception object carries `.token` before it is stored in `self.last_error` and re-raised — `render.django_exception` (`:246`) re-raises that object **by identity** when the class name matches, so the object the shell stored is the object the caller sees, and Rust's own token attach reaches a different object. This is `call_tag`'s dance exactly; the second new method is its second user.

- [ ] **Step 1: Write the failing Rust test for the defaults**

`crates/template-core/src/parser/host.rs`, in its `#[cfg(test)] mod tests`:

```rust
    #[test]
    fn a_host_without_the_partial_seam_says_so_rather_than_returning_a_handle() {
        // `NoHost` is the default every Rust-only row parses under, and the
        // failure mode this pins is a *silent* one: a default returning
        // `NodeRef(0)` would hand the materialiser a handle into a table that
        // does not exist, and the error would surface much later as an empty
        // slot. The default is loud, like `NoHost::call_tag`'s `unreachable!`.
        let error = NoHost
            .partial_node("p")
            .expect_err("NoHost cannot build a PartialNode");
        assert!(error.to_string().contains("partial_node"), "{error}");
    }
```

Run: `cargo test -p template-core partial_seam` — expected FAIL (method does not exist).

- [ ] **Step 2: Add the two methods to the trait**

In `crates/template-core/src/parser/host.rs`, after `compile_filter` (`:256`). Both **defaulted**, for `check_filter`'s reason: it is what keeps `cargo test -p template-core` runnable with no Python in the process (§10.4), and the seven Rust test hosts keep compiling.

```rust
    /// Build the `PartialNode` for `{% partial name %}` — `partial_func`'s
    /// second arm (`defaulttags.py:1279-1281`).
    ///
    /// **A handle, not a descriptor, and the reason is the one thing about this
    /// tag the registry's rule is about.** `partial_func` builds
    /// `PartialNode(name, partial_mapping=DeferredSubDict(parser.extra_data,
    /// "partials"))`, and that mapping is a **live view over a dict that
    /// outlives the parse** — it is looked up at *render* time, so
    /// `{% partial p %}` may precede `{% partialdef p %}` and still render. A
    /// `NodeDesc` cannot represent a value that is re-read later; the host can,
    /// because the host is the thing that owns the dict.
    ///
    /// The name is not resolved here. Resolution is `PartialNode.render`'s
    /// `self.partial_mapping[self.partial_name]` (`defaulttags.py:430-435`),
    /// which raises `Partial 'p' is not defined in the current template.` when
    /// the lookup fails — so a compile-time lookup would raise the *render*
    /// message at the wrong moment, on a template Django renders.
    ///
    /// **Defaulted to an error rather than to a handle.** `NoHost` has no
    /// `extra_data` and no materialiser, so there is no correct handle for it to
    /// return; `NodeRef(0)` would be a lie the failure of which is a silent
    /// empty slot at materialisation. The tag's *failure* paths — the argument
    /// count — are still testable under `NoHost`, because they raise before this
    /// call.
    fn partial_node(&self, _name: &str) -> Result<NodeRef, Error> {
        Err(Error::internal(
            "this host cannot build a PartialNode: implement Host::partial_node",
        ))
    }

    /// Register `{% partialdef name %}`'s body and return the node —
    /// `partialdef_func`'s tail (`defaulttags.py:1245-1265`).
    ///
    /// The body arrives as the **descriptor** Rust parsed, not as a Python
    /// object, and the host materialises it: `nodelist` is shared, by identity,
    /// between the `PartialTemplate` stored in `extra_data` and the
    /// `PartialDefNode` returned (`defaulttags.py:1250` and `:1265` both take
    /// the same object), so it must be built exactly once, by the side that can
    /// build it.
    ///
    /// `source_start`/`source_end` are the character offsets `PartialTemplate`
    /// keeps for `find_partial_source` (`base.py:316-325`), read from the two
    /// tokens' `position` attributes — `None` under the non-debug lexer, which
    /// is why they are `Option`.
    ///
    /// The duplicate check is the **host's**, not the caller's: it raises
    /// Django's `TemplateSyntaxError` naming `parser.origin.name`, which is
    /// Python state Rust does not hold. See `ShellHost.register_partial_def`.
    ///
    /// `token` is passed for `Host::call_tag`'s reason: the shell has to attach
    /// it to the exception object it stores for `django_exception` to re-raise
    /// by identity.
    fn register_partial_def(
        &self,
        _token: &Token,
        _name: &str,
        _inline: bool,
        _body: &NodeListDesc,
        _source_start: Option<usize>,
        _source_end: Option<usize>,
    ) -> Result<NodeRef, Error> {
        Err(Error::internal(
            "this host cannot register a partial definition: implement \
             Host::register_partial_def",
        ))
    }
```

Add `NodeListDesc` to the file's imports (`use crate::parser::desc::{NodeDesc, NodeListDesc, NodeRef};`).

- [ ] **Step 3: Add the two `Parser` forwards**

In `crates/template-core/src/parser/mod.rs`, after `alloc_slot` (`:179-181`), each with the same one-line reason `alloc_slot`'s doc gives — the table is the host's while the *tag* is the caller, and `Parser::host` is private so a tag cannot reach around the seam:

```rust
    /// `partial_func`'s `PartialNode` construction, by handle — a thin forward to
    /// [`Host::partial_node`], for [`Parser::alloc_slot`]'s reason.
    pub fn partial_node(&self, name: &str) -> Result<NodeRef, Error> {
        self.host.partial_node(name)
    }

    /// `partialdef_func`'s `extra_data` write and its duplicate check — a thin
    /// forward to [`Host::register_partial_def`], for the same reason.
    #[allow(clippy::too_many_arguments)]
    pub fn register_partial_def(
        &self,
        token: &Token,
        name: &str,
        inline: bool,
        body: &NodeListDesc,
        source_start: Option<usize>,
        source_end: Option<usize>,
    ) -> Result<NodeRef, Error> {
        self.host
            .register_partial_def(token, name, inline, body, source_start, source_end)
    }
```

- [ ] **Step 4: Add the test host**

`crates/template-core/src/parser/tags/mod.rs`, inside `support` (`:262`). It records what crossed the seam, so a row can assert the *arguments* rather than only that a handle came back.

```rust
    /// One `register_partial_def` call, as the tag made it.
    #[derive(Debug, PartialEq, Eq)]
    pub(crate) struct PartialDefCall {
        pub(crate) name: String,
        pub(crate) inline: bool,
        /// The body's text nodes, in order — `texts` rather than the descriptor,
        /// so the host does not need `NodeListDesc: Clone` to keep one.
        pub(crate) body: Vec<String>,
        pub(crate) source_start: Option<usize>,
        pub(crate) source_end: Option<usize>,
    }

    /// The host `{% partial %}` and `{% partialdef %}` compile under.
    ///
    /// Two answers, both deliberately uninformative about *materialisation*:
    /// `partial_node` returns a fixed handle and `register_partial_def` records
    /// the call and returns another, because nothing parsed under this host is
    /// ever materialised — `NodeRef` values are indices into a table the real
    /// host owns. The rows here are about what the **tag** passes across the
    /// seam; what the shell does with it is `tests/test_partials_rust.py`'s
    /// subject.
    #[derive(Default)]
    pub(crate) struct PartialHost {
        pub(crate) defs: RefCell<Vec<PartialDefCall>>,
        pub(crate) slots: Cell<usize>,
    }

    impl crate::parser::host::Host for PartialHost {
        fn has_tag(&self, _command: &str) -> bool {
            false
        }

        fn call_tag(
            &self,
            _command: &str,
            _token: &Token,
            _tokens: &[Token],
        ) -> Result<(NodeDesc, Vec<Token>), Error> {
            unreachable!("PartialHost::call_tag is never reached: has_tag returns false")
        }

        fn alloc_slot(&self) -> usize {
            let slot = self.slots.get();
            self.slots.set(slot + 1);
            slot
        }

        fn compile_filter(&self, _token: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(0))
        }

        fn partial_node(&self, _name: &str) -> Result<NodeRef, Error> {
            Ok(NodeRef(0))
        }

        fn register_partial_def(
            &self,
            _token: &Token,
            name: &str,
            inline: bool,
            body: &NodeListDesc,
            source_start: Option<usize>,
            source_end: Option<usize>,
        ) -> Result<NodeRef, Error> {
            self.defs.borrow_mut().push(PartialDefCall {
                name: name.to_string(),
                inline,
                body: texts(body),
                source_start,
                source_end,
            });
            Ok(NodeRef(0))
        }
    }
```

Add `use std::cell::{Cell, RefCell};` to the `support` module's imports.

- [ ] **Step 5: Run the Rust tests**

```bash
cargo test -p template-core partial_seam
cargo test -p template-core
```

Expected: PASS, and the whole crate still green — the seven existing test hosts are untouched because both methods are defaulted.

- [ ] **Step 6: Write the failing shell test**

`tests/test_partials_rust.py` — **created here, added to by Tasks 3, 4 and 5.**

Two things constrain what this file's *first* rows can be, and both are about which code exists yet. `register_partial_def` needs a `NodeListDesc` — the descriptor Rust parsed — and there is no public Python route to one, so a row calling it directly would have to fabricate the argument. And until Task 4, `{% partialdef %}` is still a stub, so no source containing it reaches either seam. So Task 2's rows call `partial_node` **directly** (it takes only a name), and every descriptor-needing or `partialdef`-containing row arrives in Task 4 once `_native.parse(source, host=host)` can drive it for real. That idiom — build the host, hand it to `_native.parse`, then read `host.parser.extra_data` — is `tests/test_host_tags.py:124`'s and is how the identity facts below get observed at all.

```python
"""The partial pair's seam, from the Python side.

The Rust rows in `crates/template-core/src/parser/tags/partial*.rs` assert what
the *tag* passes across the seam. These assert what the shell does with it —
which is where the two behaviours the acceptance corpus cannot see live: the
mapping is **live** (looked up at render, so source order does not matter), and
the body's nodelist is **one object** shared by the `PartialTemplate` and the
`PartialDefNode`.
"""

import unittest

from django.template import Engine
from django.template.exceptions import TemplateSyntaxError

from django_rust_templates import _native, render
from django_rust_templates import host as host_module
from scripts.filter_probe import ensure_settings


def _host(source=""):
    """A `ShellHost` over a freshly parsed template's parser.

    `ensure_settings()` is called here rather than at module level: a
    module-level `settings.configure` changes what every test module discovered
    *after* this one sees (`tests/test_host_tags.py`'s `setUpClass` records the
    same reason). The third argument to `parser_for` is left at its default
    `UNKNOWN_ORIGIN` — passing `None` explicitly is a different origin and would
    change the message `test_a_duplicate_definition_names_the_origin_template`
    asserts.
    """
    ensure_settings()
    return host_module.ShellHost(render.parser_for(source, Engine()))


class PartialNodeSeamTests(unittest.TestCase):
    def test_partial_node_does_not_resolve_the_name(self):
        """Review Focus 1 at its smallest: the seam reads no state at all.

        `DeferredSubDict.__getitem__` is where the lookup happens, and that is at
        *render* time. So this must succeed on a name that has never been defined
        anywhere — if `partial_node` raised, or read `extra_data["partials"]`,
        the snapshot bug would be exactly here.
        """
        node = _host().partial_node("never-defined")
        self.assertEqual(node.partial_name, "never-defined")

    def test_the_mapping_is_a_view_over_extra_data_and_not_a_copy(self):
        """The distinction `DeferredSubDict` exists for, and the row a copy fails.

        A snapshot taken at compile time satisfies the row above and fails this
        one: a definition written into `extra_data` *after* the node was built
        must be visible through the node's mapping.
        """
        host = _host()
        node = host.partial_node("p")
        host.parser.extra_data.setdefault("partials", {})["p"] = "sentinel"
        self.assertEqual(node.partial_mapping["p"], "sentinel")

    def test_the_mapping_reads_the_parser_it_was_built_from(self):
        """Two parsers, two mappings — the control for "live view".

        Without this, a `DeferredSubDict` wired to some module-global dict would
        pass both rows above. The population is two hosts, and the assertion is
        that each sees only its own `extra_data`.
        """
        first, second = _host(), _host()
        node = first.partial_node("p")
        first.parser.extra_data.setdefault("partials", {})["p"] = "one"
        second.parser.extra_data.setdefault("partials", {})["p"] = "two"
        self.assertEqual(node.partial_mapping["p"], "one")
```

Run: `scripts/run_unit_tests.sh tests.test_partials_rust -v`
Expected: **FAIL** with `AttributeError: 'ShellHost' object has no attribute 'partial_node'`.

- [ ] **Step 7: Implement `ShellHost.partial_node`**

`python/django_rust_templates/host.py`, after `call_tag` (`:527-613`):

```python
    def partial_node(self, name):
        """`partial_func`'s second arm (`defaulttags.py:1279-1281`).

        `DeferredSubDict(extra_data, "partials")` is a **live view**, not a copy
        — the subscript happens at render time — so this method must not resolve
        `name` and must not read `partials` at all. The failure it would cause is
        Review Focus 1: `{% partial p %}{% partialdef p %}…` is a template
        Django renders, and a compile-time lookup raises
        `Partial 'p' is not defined in the current template.` on it.

        Imported here rather than at module level for `call_tag`'s reason: this
        name must be the object the *suite's* `isinstance` rows are about, which
        is ours under the shim — and a module-level binding would capture
        whatever `django.template.base` was when this module loaded.
        """
        from django.template.base import PartialNode
        from django.utils.datastructures import DeferredSubDict

        return PartialNode(
            name, partial_mapping=DeferredSubDict(self.parser.extra_data, "partials")
        )
```

- [ ] **Step 8: Implement `ShellHost.register_partial_def`**

```python
    def register_partial_def(
        self, token_wire, name, inline, body_desc, source_start, source_end
    ):
        """`partialdef_func`'s tail (`defaulttags.py:1245-1265`), in its order.

        Four things happen here and the order is Django's: the body is
        materialised, the duplicate check runs against `extra_data["partials"]`
        **after** the body exists but **before** anything is stored, the
        `PartialTemplate` is written, and the node is built **over the same
        nodelist object** (`defaulttags.py:1250` and `:1265` are the same
        argument — `PartialDefNode(nodelist)` and `PartialTemplate(nodelist)`
        hold one object, and a shell that materialised twice would give the
        inline and deferred render paths different trees).

        The duplicate check is here and not in Rust because its message names
        `parser.origin.name`, which is Python state. Its **class** is what the
        caller observes, and that survives the FFI through
        `python_host_error`'s `py_error_kind`.

        The error dance is `call_tag`'s, and it is required rather than
        defensive: `render.django_exception` re-raises `self.last_error` **by
        identity** when its class matches, so unless this method attaches the
        token and stores the object, the `TemplateSyntaxError` a caller catches
        is a *rebuilt* one with no `.token` — and `Template.get_exception_info`
        reads exactly that attribute.
        """
        parser = self.parser
        kind, contents, lineno, position = token_wire
        token = Token(TokenType(kind), contents, position, lineno)
        try:
            nodelist = materialiser()(body_desc, parser.origin, None, None, parser)
            partials = parser.extra_data.setdefault("partials", {})
            if name in partials:
                raise TemplateSyntaxError(
                    f"Partial '{name}' is already defined in the "
                    f"'{parser.origin.name}' template."
                )
            partials[name] = PartialTemplate(
                nodelist,
                parser.origin,
                name,
                source_start=source_start,
                source_end=source_end,
            )
            return _defaulttags.PartialDefNode(name, inline, nodelist)
        except Exception as error:
            error = parser.error(token, error)
            error.token_wire = _wire_token(error.token)
            self.last_error = error
            raise
```

`materialiser()` and `PartialTemplate` are imported at module level beside `_defaulttags` (`host.py:34-37`), and `Token`/`TokenType` are already there — but check `render.py`'s import direction: `host.py` must not import `render` at module level if `render` imports `host`. Use the deferred `from django_rust_templates.render import materialiser` inside the method if the cycle bites (measure it: `python -c "import django_rust_templates.host"`).

- [ ] **Step 9: Run the shell rows and the full gate**

```bash
scripts/run_unit_tests.sh tests.test_partials_rust -v
bash scripts/check.sh --fast
```

Expected: PASS. `--fast` is the right gate here: no Django behaviour has changed yet, because nothing calls the two methods until Task 3.

- [ ] **Step 10: Commit**

Message: `feat(seam): partial_node and register_partial_def on Host, and the shell's half`

---

### Task 3: `{% partial %}`

**Files:**
- Modify: `crates/template-core/src/parser/tags/partial.rs` (the whole body)
- Modify: `crates/template-core/src/parser/registry.rs` (the pin at `:503`)
- Modify: `tests/test_tag_inventory.py` (`:345-355`)
- Test: `crates/template-core/src/parser/tags/partial.rs`, `tests/test_partials_rust.py`

**Interfaces:**
- Consumes: `Parser::partial_node` (Task 2).
- Produces: `tags::partial::compile` with a real body; `builtin_tag_stub_names() == ["partialdef"]`.

**Upstream, verbatim** (`.django/django/template/defaulttags.py:1277-1283`):

```python
    match token.split_contents():
        case "partial", partial_name:
            extra_data = parser.extra_data
            partial_mapping = DeferredSubDict(extra_data, "partials")
            return PartialNode(partial_name, partial_mapping=partial_mapping)
        case _:
            raise TemplateSyntaxError("'partial' tag requires a single argument")
```

- [ ] **Step 1: Write the failing rows**

In `partial.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::desc::HOST_NODE;
    use crate::parser::tags::support::{self, PartialHost};

    #[test]
    fn one_argument_builds_a_host_node() {
        // The whole tag. `NodeDesc::host` and not a `NODE_CLASSES` row: the
        // mapping is live host state, so the object can only be built host-side.
        let host = PartialHost::default();
        let node = support::parse_with("{% partial p %}", &host);
        let desc = &node.nodes[0];
        assert_eq!(desc.class_name, HOST_NODE);
    }

    #[test]
    fn no_argument_is_djangos_message() {
        assert_eq!(
            support::failure("{% partial %}").to_string(),
            "'partial' tag requires a single argument",
        );
    }

    #[test]
    fn two_arguments_are_the_same_message() {
        // Django's `case _` catches both, so the same string covers "too few"
        // and "too many" — a length check spelled `!= 2` would agree on both of
        // these and differ on neither, which is why the row exists to say the
        // *reason* is the wildcard arm.
        assert_eq!(
            support::failure("{% partial p q %}").to_string(),
            "'partial' tag requires a single argument",
        );
    }
}
```

Run: `cargo test -p template-core tags::partial` — expected FAIL (`unimplemented (M13)`).

- [ ] **Step 2: Write the body**

```rust
pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    // Django's match has one productive arm and a wildcard, so the *one*
    // condition is "exactly two bits" and the message is the same for both
    // failures. Slicing rather than a length check keeps that shape visible: a
    // reader sees one arm, not a comparison that has to be read twice.
    let bits = token
        .split_contents()
        .map_err(|error| parser.error(token, Error::split(error)))?;
    match bits.as_slice() {
        [_, name] => Ok(NodeDesc::host(parser.partial_node(name)?)),
        _ => Err(Error::syntax("'partial' tag requires a single argument")),
    }
}
```

`Error::split` may not exist — use whatever `split_contents`'s `SplitError` conversion is at the other call sites (measure: `git grep -n "split_contents()" crates/template-core/src/parser/tags/ | head`). The other tags already convert it; copy that line rather than inventing one.

- [ ] **Step 3: Run the rows**

```bash
cargo test -p template-core tags::partial
```

Expected: PASS.

- [ ] **Step 4: Shrink the stub pins — both of them, in this commit**

`registry.rs`, inside `every_entry_is_reachable_and_every_unimplemented_one_is_a_stub` (`:487`). The literal is at `:503` and the comment above it **already argues for exactly this move** — *"the literal shrinks with them here rather than at the end of the milestone, which is what keeps every commit in between green"* — so amend it in place rather than adding a sentence:

```rust
        // **All eleven M5 tags landed**, leaving the four names M5 was never
        // going to touch; **M6 then took two of them out of the table
        // entirely** — `load` and `querystring` are host-owned now, so they are
        // not stubs, they are absent. **M13 takes the last two out one at a
        // time**: `partial` went in Task 3, which is why this is one name.
        let stubs = ["partialdef"];
```

**The same test has a second half, and shrinking `stubs` alone reddens it.** `let mut expected: Vec<&str>` (a literal list of the *implemented* names) sits below the `stubs` pin, and the two halves move in opposite directions: a tag that lands leaves `stubs` and joins `expected` while the table's size stays put. `partial` stays in the tag table — only its *body* stops being a stub — so `builtin_tag_names_including_stubs()` still contains it and `expected` must **gain `"partial"` in this commit**. After Task 3 the list is **24**, `stubs` is **1**, and the table is **25** (`the_registry_has_the_twenty_five_names_rust_owns` asserts 25 directly). The comment beside `expected` carried its own stale sum — *"this is 22, `stubs` is still 2, and the sum is 24"*, against a list of 23 and a table of 25 — and it was stale **before** Task 3. Re-derive every figure in that sentence from the two literals rather than adjusting one of them: the plan's operands are not to be borrowed.

`#[expect(dead_code)]` on `support::PartialHost` (`tags/mod.rs:437`, and the one on `PartialDefCall`, `:412` at `b266741`) is removed in this commit, not left in place: Task 3's first row constructs a `PartialHost`, so both attributes fire `unfulfilled_lint_expectations` under `-D warnings`. That is the attribute working as designed — it removes itself when the code it guards is finally used — and the replacement is a comment recording that it fired. (Measured on landing: both fired; the surviving line numbers are `:412` and `:437`.)

`tests/test_tag_inventory.py:345` — the row is `test_the_only_two_unimplemented_entries_are_the_m13_pair`, and its docstring already opens *"A stub is a real M13 tag, not a stand-in for a name Rust handed over"*. Rename it and shrink its literal:

```python
    def test_the_last_unimplemented_entry_is_the_m13_pair_head(self):
        """A stub is a real M13 tag, not a stand-in for a name Rust handed over.

        `builtin_tag_stub_names()` asks the registry itself which names have a compile
        function that returns `Unimplemented`, so pinning the set here is what
        separates "not done yet" from "not ours": `load` and `querystring` are
        absent from the table entirely, and after M13's Task 3 only `partialdef`
        remains. The row's *name* moves with the literal — a row still called
        `..._the_m13_pair` would be a name asserting a set of two.
        """
        from django_rust_templates import _native

        self.assertEqual(set(_native.builtin_tag_stub_names()), {"partialdef"})
```

`test_no_m5_tag_is_still_a_stub`, the next row down, is a **set intersection** (`M5_TAGS & stubs`) and stays green throughout — do not touch it.

Run: `cargo test -p template-core registry && scripts/run_unit_tests.sh tests.test_tag_inventory -v`
Expected: PASS.

- [ ] **Step 5: The Python-side rows**

Add to `tests/test_partials_rust.py`'s `PartialNodeSeamTests`, below Task 2's three. **End-to-end rows that mention `{% partialdef %}` cannot live here** — that tag is still a stub until Task 4, so a source containing it raises `UnimplementedTag` before any of this task's code runs. Those two rows are written in Task 4 Step 5, where both tags exist.

```python
    def test_the_tag_now_compiles_and_fails_at_render_instead(self):
        """The change this task makes, stated as the *move* of one failure.

        Before: `{% partial p %}` raised `UnimplementedTag` from the registry, at
        compile. After: it compiles — the node is built — and the failure (if the
        name is never defined) comes from `PartialNode.render`
        (`defaulttags.py:430-435`). So the pair of assertions is about *when*, and
        asserting only the message would pass on the old tree too.
        """
        with self.assertRaises(TemplateSyntaxError) as caught:
            render.render_string("{% partial nodice %}", {})
        self.assertEqual(
            str(caught.exception),
            "Partial 'nodice' is not defined in the current template.",
        )
        self.assertNotIsInstance(caught.exception, _native.UnimplementedTag)

    def test_the_argument_errors_are_djangos_two_messages_end_to_end(self):
        """The tag body's messages, through the whole shell rather than a unit row.

        `crates/template-core/src/parser/tags/partial.rs` asserts these two under
        `PartialHost`; this asserts the *class* the shell hands back for them, which is
        the half the Rust row cannot see.
        """
        for source, message in [
            ("{% partial %}", "'partial' tag requires a single argument"),
            ("{% partial p q %}", "'partial' tag requires a single argument"),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    render.render_string(source, {})
                self.assertEqual(str(caught.exception), message)
                self.assertNotIsInstance(caught.exception, _native.UnimplementedTag)
```

Run: `scripts/run_unit_tests.sh tests.test_partials_rust -v`
Expected: PASS. If `test_the_tag_now_compiles_and_fails_at_render_instead` fails with `UnimplementedTag`, the stub body was not replaced — and if it fails with the *message* only, the render path is not ours yet, so check `_impl/engine.py:181-203` (the recon §3 measured it as ours) before touching the tag.

- [ ] **Step 6: Run the partial corpus**

```bash
bash scripts/run_django_suite.sh
```

Expected: `Ran 1547 tests … OK (skipped=6)` — and note that the partial modules' rows now run one tag on our route and one on Django's.

- [ ] **Step 7: Commit**

Message: `feat(partial): Rust compiles {% partial %} — the mapping is live, so the node is host-side`

---

### Task 4: `{% partialdef %}`

The harder half: five match arms, two source offsets read from token positions, the body parsed by **Rust**, and the duplicate check on the other side of the seam.

**Files:**
- Modify: `crates/template-core/src/parser/tags/partialdef.rs` (the whole body)
- Modify: `crates/template-core/src/parser/registry.rs` (`:117-132` the note, `:503` the pin, `:555-580` the derivation row)
- Modify: `crates/template-core/src/parser/tags/mod.rs` (the two tag files' module docs, `:5-10` of each)
- Modify: `python/django_rust_templates/host.py` (`_rust_owned`'s comment `:484-490`)
- Modify: `tests/test_tag_inventory.py` (`:345`)
- Test: `crates/template-core/src/parser/tags/partialdef.rs`, `tests/test_partials_rust.py`

**Interfaces:**
- Consumes: `Parser::register_partial_def` (Task 2), `Parser::parse`, `Parser::next_token`, `Parser::invalid_block_tag`, `Parser::unclosed_block_tag`.
- Produces: `builtin_tag_stub_names() == []`, and every tag inside a partial body compiled by Rust.

**Upstream, verbatim** (`.django/django/template/defaulttags.py:1221-1265`) — the five arms, the `valid_endpartials` tuple, the two `getattr(token, "position", None)` guards, the `parser.parse`/`next_token`/`invalid_block_tag` sequence, the `setdefault("partials", {})` and the duplicate raise.

- [ ] **Step 1: Write the failing rows**

In `partialdef.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::desc::HOST_NODE;
    use crate::parser::tags::support::{self, PartialHost};

    fn parse(source: &str) -> (NodeDesc, PartialHost) {
        // The host has to outlive the parse for its recording to be read, so the
        // helper returns it rather than a borrowed descriptor.
        let host = PartialHost::default();
        let node = support::parse_with(source, &host).nodes.remove(0);
        (node, host)
    }

    #[test]
    fn a_plain_definition_records_the_body_and_is_not_inline() {
        let (node, host) = parse("{% partialdef p %}x{% endpartialdef %}");
        assert_eq!(node.class_name, HOST_NODE);
        let defs = host.defs.borrow();
        assert_eq!(defs.len(), 1);
        assert_eq!(defs[0].name, "p");
        assert!(!defs[0].inline);
        assert_eq!(defs[0].body, vec!["x"]);
    }

    #[test]
    fn inline_is_a_third_bit_and_not_a_parameter() {
        let (_, host) = parse("{% partialdef p inline %}x{% endpartialdef %}");
        assert!(host.defs.borrow()[0].inline);
    }

    #[test]
    fn inline_with_a_parameter_is_its_own_message() {
        // Review Focus, arm 2: three bits where the third is not `inline`. The
        // message is about the *inline* argument, not the tag's arity, which is
        // why it cannot be folded into the "at most 2 arguments" arm.
        assert_eq!(
            support::failure("{% partialdef p inline x %}").to_string(),
            "The 'inline' argument does not have any parameters; either use \
             'inline' or remove it completely.",
        );
    }

    #[test]
    fn no_name_and_too_many_arguments_are_two_messages() {
        assert_eq!(
            support::failure("{% partialdef %}").to_string(),
            "'partialdef' tag requires a name",
        );
        // Four bits: the wildcard arm, and a *different* string from the three-
        // bit one above.
        assert_eq!(
            support::failure("{% partialdef p q r %}").to_string(),
            "'partialdef' tag takes at most 2 arguments",
        );
    }

    #[test]
    fn the_accepted_spellings_are_exactly_two() {
        // `valid_endpartials = ("endpartialdef", f"endpartialdef {partial_name}")`,
        // and both are accepted. Nothing else is.
        let (_, host) = parse("{% partialdef p %}x{% endpartialdef %}");
        assert_eq!(host.defs.borrow()[0].name, "p");
        let (_, host) = parse("{% partialdef p %}x{% endpartialdef p %}");
        assert_eq!(host.defs.borrow()[0].name, "p");
    }

    #[test]
    fn whitespace_stops_the_scan_and_then_fails_the_whole_contents_check() {
        // Review Focus 3, and the reason there are *two* checks on one token.
        // Django's scan matches the first word (`base.py:561`, `:564`) and the
        // validation compares the whole contents (`defaulttags.py:1244`), so
        // `endpartialdef  p` terminates the block and is then refused. Measured
        // on the pinned tree: two spaces, three spaces and a tab all print this
        // same message, and the message prints the *first word*.
        for source in [
            "{% partialdef p %}x{% endpartialdef  p %}",
            "{% partialdef p %}x{% endpartialdef   p %}",
            "{% partialdef p %}x{% endpartialdef\tp %}",
        ] {
            let message = support::failure(source).to_string();
            assert!(message.contains("Invalid block tag"), "{source:?}: {message}");
            assert!(message.contains("'endpartialdef p'"), "{source:?}: {message}");
        }
    }

    #[test]
    fn the_wrong_end_tag_name_is_invalid() {
        let message = support::failure("{% partialdef p %}x{% endpartialdef q %}").to_string();
        assert!(message.contains("Invalid block tag"), "{message}");
        assert!(message.contains("'endpartialdef p'"), "{message}");
    }

    #[test]
    fn an_unclosed_definition_names_both_accepted_end_tags() {
        let message = support::failure("{% partialdef p %}x").to_string();
        assert!(message.contains("Unclosed tag"), "{message}");
        assert!(message.contains("endpartialdef p"), "{message}");
    }

    #[test]
    fn the_two_source_offsets_come_from_the_two_tokens(self) {
        // Debug-lexer positions, so this row needs `Lexer::debug` and is
        // separate from the rest. Expected values are computed from the source,
        // not frozen: `source_start` is the open tag's start, `source_end` the
        // end tag's end.
        let source = "{% partialdef p %}x{% endpartialdef %}";
        let host = PartialHost::default();
        let mut parser = Parser::new(crate::lexer::Lexer::debug(source).tokenize(), &host);
        parser.parse(&[]).expect("expected a successful parse");
        let defs = host.defs.borrow();
        assert_eq!(defs[0].source_start, Some(0));
        assert_eq!(defs[0].source_end, Some(source.len()));
    }
}
```

Run: `cargo test -p template-core tags::partialdef` — expected FAIL.

- [ ] **Step 2: Write the body**

```rust
pub fn compile(parser: &mut Parser<'_>, token: &Token) -> Result<NodeDesc, Error> {
    let bits = token
        .split_contents()
        .map_err(|error| parser.error(token, Error::split(error)))?;

    // Django's five arms, in Django's order. The order is observable: a
    // three-bit token whose third bit is not `inline` takes arm 2 and not the
    // wildcard (Review Focus, `inline x`), and the two-bit token that is
    // `["partialdef"]` alone takes arm 4 and not the wildcard.
    let (name, inline) = match bits.as_slice() {
        [_, name, third] if third == "inline" => (name, true),
        [_, _, _] => {
            return Err(Error::syntax(
                "The 'inline' argument does not have any parameters; either use \
                 'inline' or remove it completely.",
            ))
        }
        [_, name] => (name, false),
        [_] => return Err(Error::syntax("'partialdef' tag requires a name")),
        _ => return Err(Error::syntax("'partialdef' tag takes at most 2 arguments")),
    };

    // `valid_endpartials`, and the tuple *is* the message: it is handed to
    // `invalid_block_tag`/`unclosed_block_tag` unchanged, so both messages list
    // exactly these two spellings.
    let valid = ["endpartialdef".to_string(), format!("endpartialdef {name}")];
    let valid_refs: Vec<&str> = valid.iter().map(String::as_str).collect();

    // `getattr(token, "position", None)`; `None` under the non-debug lexer, and
    // `pos_open[0]` / `pos_close[1]` — the open tag's start and the end tag's
    // end, not the open tag's end. A reader who assumes both offsets come from
    // one token gets the wrong pair and the wrong substring in
    // `find_partial_source`.
    let source_start = token.position.map(|position| position.0);

    // `Parser::parse` returns `Err(unclosed_block_tag)` rather than `Ok` when
    // `parse_until` is non-empty and the tokens run out (`parser/mod.rs:451-455`),
    // and it *prepends* the token it stopped on (`parser/mod.rs:380`,
    // Django's `base.py:568`). So reaching here means the scan stopped on a
    // matching token and that token is the next one — it cannot be `None`, and a
    // defensive `else` would be a branch no input can reach and therefore no row
    // can redden.
    let body = parser.parse(&valid_refs)?;
    let endpartial = parser
        .next_token()
        .expect("parse stopped on a matching token and prepended it");
    // The **whole contents**, where the scan above matched the **first word**.
    // That asymmetry is the tag: `endpartialdef  p` ends the block and is then
    // refused here (Review Focus 3, measured). `invalid_block_tag` is called with
    // the first word as `command`, which is why the message names
    // `'endpartialdef'` and not the contents.
    if !valid_refs.contains(&endpartial.contents.as_str()) {
        return Err(parser.invalid_block_tag(&endpartial, "endpartialdef", &valid_refs));
    }
    let source_end = endpartial.position.map(|position| position.1);

    let handle = parser.register_partial_def(
        token,
        name,
        inline,
        &body,
        source_start,
        source_end,
    )?;
    Ok(NodeDesc::host(handle))
}
```

Check `Parser::invalid_block_tag`'s signature (`parser/mod.rs:452`) and `unclosed_block_tag`'s (`:478`) before writing these calls; `invalid_block_tag` takes `(&self, token, command, parse_until)` and returns an `Error`, `unclosed_block_tag` takes `&mut self`.

- [ ] **Step 3: Run the Rust rows**

```bash
cargo test -p template-core tags::partialdef
```

Expected: PASS. If `the_two_source_offsets_come_from_the_two_tokens` is off by the tag's length, the cause is `pos_close` read from the wrong token — the offsets are measured against `source`, never copied from this plan.

- [ ] **Step 4: Empty the stub pins and amend the rule's note**

`registry.rs:504` (`:503` at `b266741`; Task 3's comment expansion moved it) → `let stubs: [&str; 0] = [];`, **and the `expected` list below it gains `"partialdef"` in the same edit** (the list becomes 25, `stubs` is empty, the table stays 25 — all three re-derived, since the same sentence's old operands were stale at Task 3), and `registry.rs:117-132`'s "Flagged, not resolved" note: the pair is no longer flagged, so the note must say so rather than be deleted — the reason it gave (a live view over parse state, which a `NodeDesc` cannot represent) is *why the seam exists*, and it is now a satisfied rule rather than an open one.

`tests/test_tag_inventory.py:345` → the row Task 3 renamed to `test_the_last_unimplemented_entry_is_the_m13_pair_tail` becomes `test_no_builtin_tag_is_still_a_stub`, asserting `set() == set()` **with a positive control**, because an empty set is satisfied by a registry that lost its entries:

```python
    def test_no_builtin_tag_is_still_a_stub(self):
        """The stub set is empty, and an empty set is not on its own evidence.

        `builtin_tag_stub_names()` derives the set by *asking each compile
        function* whether it returns `Unimplemented`, so `set()` is also what a
        table that lost its rows would produce. The control is drawn from the
        same population and asserts the population is still there: the
        including-stubs list has the implemented names in it, and every one of
        them resolves.
        """
        from django_rust_templates import _native

        names = _native.builtin_tag_names_including_stubs()
        self.assertGreater(len(names), 0, "the registry is empty, so this row measures nothing")
        self.assertIn("for", names)
        self.assertIn("partialdef", names)
        self.assertEqual(set(_native.builtin_tag_stub_names()), set())
```

`host.py:484-490`'s comment lists "the two stub tags" as names `_rust_owned` excludes; after this task the pair is in `_rust_owned` and the comment's example list is wrong. Update it to the two that remain (`load`, `querystring`) and say that the pair joined the Rust side in M13.

- [ ] **Step 5: Add the duplicate-definition row, then run the corpus**

Review Focus 2 is a *project-side* row: the corpus has its own duplicate-definition test inside `template_tests.test_partials`, but that is a gate label asserting Django's behaviour — it is not evidence about **our** route, which is where the message's `parser.origin.name` comes from. This step also carries the three rows Tasks 2 and 3 could not write, because both tags exist only now — a new class in `tests/test_partials_rust.py`, since all four observe the host rather than the rendered string:

```python
class PartialDefSeamTests(unittest.TestCase):
    def test_a_partial_before_its_definition_renders(self):
        """Review Focus 1, end to end — the row Tasks 2 and 3 had to defer.

        The definition appears *after* the use, and the template renders, because
        `partial_mapping` is looked up at render. This cannot be written before
        this task: `{% partialdef %}` was a stub until now.
        """
        source = "{% partial p %}{% partialdef p %}body{% endpartialdef %}"
        self.assertEqual(render.render_string(source, {}), "body")

    def test_an_inline_definition_renders_at_the_point_of_use(self):
        source = "{% partialdef p inline %}body{% endpartialdef %}"
        self.assertEqual(render.render_string(source, {}), "body")

    def test_a_duplicate_definition_names_the_origin_template(self):
        """Review Focus 2: the message names the origin, and the *second* raises.

        Django's check is "in template order" — `if partial_name in partials` —
        so the first definition wins and the second raises, quoting
        `parser.origin.name` rather than anything in the tag. That name is Python
        state the Rust side does not hold, which is why the check lives in
        `ShellHost.register_partial_def` and not in the tag body.
        """
        with self.assertRaises(TemplateSyntaxError) as caught:
            render.render_string(
                "{% partialdef p %}a{% endpartialdef %}"
                "{% partialdef p %}b{% endpartialdef %}",
                {},
            )
        self.assertEqual(
            str(caught.exception),
            "Partial 'p' is already defined in the 'None' template.",
        )

    def test_the_stored_partial_and_the_node_share_one_nodelist(self):
        """`defaulttags.py:1250` and `:1265` are the *same* argument, by identity.

        The `PartialTemplate` in `extra_data` and the `PartialDefNode` returned
        hold one nodelist object, so an inline render and a deferred render walk
        one tree. A shell that materialised the descriptor twice would pass every
        other row in this file — the two trees would be *equal* but not
        *identical*, and nothing about the rendered output can tell them apart.
        So the node is captured rather than rendered: `_native.parse(source,
        host=host)` drives the real call and a spy on the seam keeps the return
        value, which `render_string` would have built and discarded. (The same
        spy idiom Task 7 uses on the host route.)
        """
        host = _host()
        captured = []
        original = host.register_partial_def

        def spy(*args, **kwargs):
            node = original(*args, **kwargs)
            captured.append(node)
            return node

        host.register_partial_def = spy
        _native.parse("{% partialdef p inline %}body{% endpartialdef %}", host=host)

        stored = host.parser.extra_data["partials"]["p"]
        self.assertEqual(len(captured), 1, "the seam was not called exactly once")
        self.assertIs(stored.nodelist, captured[0].nodelist)
```

`PartialDefNode`'s attribute is `nodelist` and `PartialNode`'s are `partial_name`/`partial_mapping` — read off `_impl/defaulttags.py:415-430`, which is upstream `defaulttags.py:413-428` at the usual +2.

The origin's name for an `Engine.from_string` template is the string `"None"` — **measure it, do not trust that**: if the assertion is off, the printed message is the measurement, and the fix is to the expectation rather than to the shell.

Then the corpus, which is the milestone's real measurement:

```bash
bash scripts/run_django_suite.sh
```

Expected: `Ran 1547 tests … OK (skipped=6)`. This is the 52-row population whose expiry mutation reddens **240 reports (73 failures + 167 errors)** across exactly two modules, split 24 (2F+22E) for `template_tests.test_partials` and 216 (71F+145E) for `template_tests.syntax_tests.test_partials` (`.review/m13-recon.md` §5(a)). Those reports are now green *on our route* — prove it rather than assume it, by re-running the expiry mutation and reading the count collapse:

```bash
_M13_EXPIRY_MUTATE=1 .venv/bin/python .review/m13-measurements/m13_measure_stub_expiry.py
```

After Task 4 the mutation has nothing to mutate, so it must report **`Ran 1547 tests … OK`** with no red — a mutation that no longer reddens anything is the correct outcome here, and a *stubbornly red* run would mean a stub survived somewhere.

- [ ] **Step 6: Commit**

Message: `feat(partialdef): Rust compiles {% partialdef %} — partial bodies are Rust-parsed, stub set empty`

---

### Task 5: The reference table is the parse's live list — **conditional**

**Read this task's first step before believing the rest of it.** The hazard below is a *hypothesis*, it was written on an unmeasured premise, and the instrument that tests it (`Step 1`) may well come back saying the premise is false — in which case the honest deliverable is the rows plus a comment and **no change to `lib.rs` at all**.

The hypothesis: `{% partialdef p %}{% cycle c %}{% endpartialdef %}` where `c` is named outside the body. The body's descriptor carries `NodeDesc::new("CycleNode").slot(outer_slot)`, and `register_partial_def` materialises that body **mid-parse**. `FilterCheckHost::ref_list` (`lib.rs:1257`) builds a *fresh* Python list from the Rust `Vec`, so the mid-parse materialisation writes into a snapshot; the outer materialisation then reads a second fresh list, finds the slot empty, and builds a **second** `CycleNode` for the same slot. Django builds one.

**Why the hypothesis is in doubt, measured.** A first draft of this task asserted the output would differ (`"aa"` when correct, `"ab"` under the snapshot). Both numbers were wrong, and the reason is a mechanism this plan had not considered: `PartialTemplate.render` does `with context.render_context.push_state(self)` (`base.py:340-346`, printed verbatim by the instrument), so a **named cycle restarts inside every partial render**. Django's own output for the shape is therefore `'aaa'`, not the `'aba'` a single monotonically-advancing node would give, and `'aaaa'` with three partials where advancing would give `'abab'`. Once the named cycle restarts inside each partial render anyway, a *second* node whose state also restarts in the same layer may well produce the identical string — so the **output is not the discriminator this task needs**, and the premise may not be reachable at all.

The instrument is committed at `.review/m13-measurements/m13_measure_cycle_across_partials.py` and prints all four shapes, the node-identity fact, and `PartialTemplate.render`'s source. Run it rather than quoting these numbers:

```bash
DJANGO_RUST_TEMPLATES=0 .venv/bin/python -I .review/m13-measurements/m13_measure_cycle_across_partials.py
```

**And the behavioural row is not available either — also measured.** The obvious replacement for an identity assertion is a row a user could see: put a *stateful* tag in the body and count the renders. This project's established instrument for "was this node built once" is `custom.py`'s `CounterNode`, which M10 finding 6 used (`test_include.py:372-397` asserts `"012345678"` through `{% include %}`), so

```
{% load custom %}{% partialdef p %}{% counter %}{% endpartialdef %}
{% partial p %}{% partial p %}{% partial p %}
```

gives `'012'` on the pinned tree (measured; `'000'` is what three *independent* mentions give, and what a per-render rebuild would give) — instrument `.review/m13-measurements/m13_measure_counter_across_partials.py`. It is a good row and Step 1 keeps it. It is **not** evidence about the table, because the node it counts is in the table before any materialiser runs:

| table entry | filled by | our code | lost by a snapshot? |
| --- | --- | --- | --- |
| `NodeRef` | the **host**, during the parse | `call_tag` pushes the node and returns the handle (`lib.rs:1404-1406`) | no — non-`None` in every list built later |
| `NodeSlot` | the **materialiser**, as it builds | `alloc_slot` reserves `None` (`lib.rs:1468`), `materialise_node` fills it on the way out (`_impl/parser.py:404-407`) | yes — `None` until something builds it |

`_impl/parser.py:726-731` states that split in as many words ("a `NodeRef` entry is filled by the host during the parse, a `NodeSlot` entry by the materialiser as it builds"). And the second row is *one tag wide*: `alloc_slot` has a single caller in the whole workspace, `crates/template-core/src/parser/tags/cycle.rs:128` (`resetcycle.rs` only reads the slot a cycle reserved). So the population a snapshot can lose is the cycle's slot alone — and the cycle's behaviour is precisely what `push_state` masks. A behavioural row therefore **cannot** see this defect, and the identity assertion is not a taste for instrumentation over behaviour; it is the defect's only observable.

**Say which fact the conditional rests on, not only what it concluded.** The sentence above is a dated verdict and it has to be readable as one: the identity assertion is the defect's only observable **because** `cycle.rs:128` is `alloc_slot`'s sole production caller **and** `push_state` masks a cycle. The moment a second tag allocates a slot, the snapshot-losable population widens beyond `CycleNode`, and if that tag's state is not masked the way a cycle's is, a behavioural row becomes possible — and the reason for preferring identity stops being true while this paragraph stays readable. So the next person gets a thing to re-measure rather than a conclusion to trust; the measured count is in the row above, and it is three `alloc_slot` hits, two of which are `Parser::alloc_slot`'s own body (`parser/mod.rs:180`) and a `#[cfg(test)]` support host (`tags/mod.rs:387`) — "one production caller" is right and is not what a bare `grep` prints.

**Files:**
- Modify: `crates/template-py/src/lib.rs` — **only if the identity row fails** (`refs` field `:1227`, `ref_list` `:1257`, `alloc_slot` `:1468`, the three construction sites `:1539`, `:1663`, `:2566` — **re-derive every one of these with `grep -n` before editing**: Tasks 1-4 land first, Task 1 transcribes a module and Task 2 adds two methods to this very file, so these are pre-M13 numbers)
- Test: `tests/test_partials_rust.py`
- Instruments (already committed): `.review/m13-measurements/m13_measure_cycle_across_partials.py`, `m13_measure_counter_across_partials.py`, `m13_measure_ref_fill_sides.py`

**Interfaces:**
- Consumes: `Host::alloc_slot`'s "reserved-before-use" contract; `_native.parse`'s **three-tuple** return, `(nodes, contains_nontext, refs)` (`tests/test_ref_table.py:163` is the idiom — the nodes are *descriptors*, so identity has to be observed after `render.materialiser()` runs them).
- Produces: **if the change is warranted**, one Python list per parse, created before it and pushed into by the host; `ref_list` returning *that* object. If not, the two rows below and a comment recording the measurement.

- [ ] **Step 1: Write the rows, and let them decide the task**

Three rows. The **identity** row is the decision. The second is the measured output — a regression guard whatever it turns out to be. The third is the behavioural row from the table above: kept because it pins a guarantee a user would notice and because it guards a *different* real defect (a body re-parsed or re-materialised per render), and named here as **not** the decision, with the fill-sides measurement as the reason rather than a preference.

Note the shape of the materialise call: `render.materialiser()` returns the **module** (`python/django_rust_templates/render.py:95`), not a function, and `materialise` takes the whole three-tuple as its first argument with `parser=` required for a body containing any filter expression. The idiom is `tests/test_ref_table.py:150-155`'s.

```python
    def test_a_partial_body_and_the_outer_template_share_one_cycle_node(self):
        """Review Focus 5: one slot, one node.

        `{% cycle c %}` outside names the node; the body's `{% cycle c %}` names
        the same **slot**. `alloc_slot` reserves one slot and `named_cycle_nodes`
        maps the name to it, so a live reference table materialises one
        `CycleNode` and both mentions point at it. A *snapshot* table materialises
        the body mid-parse against a list that is then discarded, and the outer
        materialisation builds a second — the two are equal trees, distinct
        objects.

        Identity is asserted rather than output, because output cannot see this:
        `PartialTemplate.render` pushes a render-context state (`base.py:340-346`),
        so a named cycle restarts inside every partial render either way. The
        measurement that settled it is
        `.review/m13-measurements/m13_measure_cycle_across_partials.py`, and the
        reason no *behavioural* row can replace this one is
        `.review/m13-measurements/m13_measure_ref_fill_sides.py` — the body's
        `{% cycle %}` is the only kind of table entry a snapshot can lose.
        """
        host = _host()
        nodes, contains_nontext, refs = _native.parse(_CYCLE_ACROSS_PARTIAL, host=host)
        tree = render.materialiser().materialise(
            (nodes, contains_nontext, refs),
            resolve_ref=refs.__getitem__,
            register_ref=refs.__setitem__,
            parser=host.parser,
        )

        outer_cycle = [n for n in tree if type(n).__name__ == "CycleNode"]
        body = host.parser.extra_data["partials"]["p"].nodelist
        body_cycle = [n for n in body if type(n).__name__ == "CycleNode"]
        self.assertEqual(len(outer_cycle), 1)
        self.assertEqual(len(body_cycle), 1)
        self.assertIs(outer_cycle[0], body_cycle[0])
```

```python
    def test_a_named_cycle_across_a_partial_renders_djangos_string(self):
        """The same shape, end to end, against the measured value.

        `'aaa'`: one character from `{% cycle 'a' 'b' as c %}` and one from each
        `{% partial p %}` — and both partials render `'a'` because the named cycle
        restarts inside a partial render. Advancing monotonically would give
        `'aba'`. The expectation is the pinned tree's output, measured, not
        derived; re-run the instrument before changing it.
        """
        self.assertEqual(render.render_string(_CYCLE_ACROSS_PARTIAL, {}), "aaa")
```

```python
    def test_a_stateful_library_tag_in_a_body_is_built_once(self):
        """The behavioural guard, and explicitly *not* this task's trigger.

        `'012'`: `{% counter %}` is a library tag, so `do_counter` builds its
        node once while Rust parses the body, and the three `{% partial p %}`
        renders advance it. A body re-parsed or re-materialised per render gives
        `'000'` — which is also what three independent mentions give, measured.
        That is a real defect and a user-visible one, which is why the row is
        here; it says nothing about the reference table, because the counter's
        entry is filled by the **host** before any materialiser runs. The rows
        above carry the decision.
        """
        self.assertEqual(
            render.render_string(_COUNTER_ACROSS_PARTIAL, {}, _counter_engine()),
            "012",
        )
```

with, at module level:

```python
_CYCLE_ACROSS_PARTIAL = (
    "{% cycle 'a' 'b' as c %}"
    "{% partialdef p %}{% cycle c %}{% endpartialdef %}"
    "{% partial p %}{% partial p %}"
)

_COUNTER_ACROSS_PARTIAL = (
    "{% load custom %}{% partialdef p %}{% counter %}{% endpartialdef %}"
    "{% partial p %}{% partial p %}{% partial p %}"
)


def _counter_engine():
    """An engine that can `{% load custom %}`.

    `.django/tests` is on `sys.path` because `find_library` resolves
    `template_tests.templatetags.custom` through it — the fixture `custom.py:381-393`
    defines `CounterNode` in, and the same one `test_include.py:372-397` uses for
    this question.
    """
    ensure_settings()
    return Engine(libraries={"custom": "template_tests.templatetags.custom"})
```

and at the top of the module, beside the other imports:

```python
import sys
from pathlib import Path

_DJANGO_TESTS = Path(__file__).resolve().parent.parent / ".django" / "tests"
if str(_DJANGO_TESTS) not in sys.path:
    sys.path.insert(0, str(_DJANGO_TESTS))
```

Run: `scripts/run_unit_tests.sh tests.test_partials_rust -v`

Expected: the output row and the counter row **PASS** (both are Django's measured values, and if either does not pass, our render is wrong for a reason unrelated to the table — find that first). The identity row is the decision:

- **If the identity row FAILS** — the hypothesis is confirmed, and Steps 2-4 below are the fix.
- **If the identity row PASSES** — the hazard is not reachable through this shape. **Stop.** Delete the `lib.rs` change from this task, keep all three rows, and write a comment on `ref_list` recording that a mid-parse materialisation over the snapshot was *measured* not to produce a second node, naming the instrument. That comment is the deliverable; do not "harden" the table on an unconfirmed premise.

- [ ] **Step 2: Make the table the parse's live list — only if Step 1 failed**

In `FilterCheckHost`, replace `refs: RefCell<Vec<Option<Py<PyAny>>>>` with the list itself:

```rust
    /// The parse's reference table, as the **Python list** `materialise` will
    /// subscript and assign into.
    ///
    /// A list rather than a Rust `Vec` copied out on demand, and the difference
    /// is observable: `register_partial_def` materialises a partial body *during
    /// the parse*, so a copy taken at that moment is a different object from the
    /// one the outer materialisation reads — and a `{% cycle %}` slot filled
    /// into the first is empty in the second. Django's `_named_cycle_nodes`
    /// holds the node; ours is an index into a table, and the table has to be
    /// one object for the whole parse or the index means two things.
    ///
    /// It is created **before** the parse and lives here, on the host, which is
    /// the object the parser borrows for the parse's whole life.
    refs: Py<PyAny>,
```

`alloc_slot` becomes:

```rust
    fn alloc_slot(&self) -> usize {
        // `list.append(None)` — the placeholder `materialise`'s `register_ref`
        // overwrites with `list.__setitem__`. `Python::attach` is available
        // here, which is why the Rust-`None` placeholder this used to carry is
        // gone: the reason it existed was the absence of a `Python<'_>`, and
        // holding the list is what removes that absence.
        Python::attach(|py| {
            let refs = self.refs.bind(py);
            let index: usize = refs.len().expect("a list has a length");
            refs.call_method1("append", (py.None(),))
                .expect("the reference table is a list");
            index
        })
    }
```

`ref_list` becomes `Ok(self.refs.clone_ref(py))`, and `with_ref_table`/`compile_template`'s `refs` handling simplifies to that one call. The three construction sites create the list eagerly: `PyList::empty(py).unbind()`.

- [ ] **Step 3: Run both rows**

```bash
scripts/run_unit_tests.sh tests.test_partials_rust -v
```

Expected: **PASS**, both rows — the identity row is what Step 2 was for.

- [ ] **Step 4: Prove nothing else moved**

```bash
cargo test --workspace
bash scripts/check.sh
```

Expected: green, `Ran 1547 tests … OK (skipped=6)`. The `{% cycle %}` corpus rows are the ones that would notice a broken table, and `tests/test_ref_table.py` is where the three-tuple's contract is pinned — if the `refs` element changed from a Rust-built list to the live one, that file is where a disagreement shows up first.

- [ ] **Step 5: Commit**

If Step 2 ran:

```
fix(seam): the reference table is the parse's live list, not a snapshot per read
```

If Step 1's identity row **passed** and Step 2 was skipped, the commit is the rows plus the comment instead, and the message says what was measured:

```
test(partials): one slot one node across a partial boundary — measured, table unchanged
```

Either way this commit is small. A milestone's riskiest task being *conditionally* a no-op is the correct outcome, not a wasted one: the alternative was changing `lib.rs` on a premise whose illustration had already been falsified twice.

---

### Task 6: `UnimplementedTag` loses its last producer

Nothing returns `Unimplemented` any more, so `parse_error`'s `ErrorKind::Unimplemented` arm (`lib.rs:1030`) is unreachable from the compile path, and `tests/test_parser_parity.py:857` — whose docstring already says *"M13 implements the pair that is left"* — has no subject.

**Files:**
- Modify: `crates/template-core/src/parser/registry.rs` (the Rust-side pin)
- Modify: `tests/test_parser_parity.py` (`:857-885`)
- Modify: `python/django_rust_templates/_native`? — no: `UnimplementedTag` stays, as a class.

**Interfaces:**
- Consumes: `registry::unimplemented(name, milestone)`.
- Produces: a Rust-side row that keeps `unimplemented`'s message pinned after its last caller leaves, and a parity row whose subject is the class relationship rather than a tag.

- [ ] **Step 1: Add the Rust-side pin**

`registry.rs`'s test module already has `a_stub_names_its_milestone_and_says_it_is_the_engine_not_the_template` (`:477`), which asserts `unimplemented("for", "M5").kind == ErrorKind::Unimplemented("'for' is a built-in tag this engine does not implement yet (M5)")`. **Keep it, and say why it is now the last one.** It was never the derivation's *caller* — `builtin_tag_stub_names()` calls each **compile function** and asks whether it returns `Unimplemented` — but `unimplemented` was reached *through* those two bodies, and after Task 4 it has no production caller at all. So the row stops being a redundancy check on the derivation and becomes the only thing standing between `unimplemented` and silent deletion-by-clippy-dead-code. Amend its comment rather than adding a second row:

```rust
        // **M13's Task 4 left this row as `unimplemented`'s only caller.** The
        // two stub bodies in `tags/partial.rs` and `tags/partialdef.rs` were the
        // production callers, and `builtin_tag_stub_names()` reached the
        // function *through* them; those bodies have real implementations now, so
        // nothing in the crate produces an `Unimplemented` any more. The kind and
        // its FFI mapping (`ErrorKind::Unimplemented` → `UnimplementedTag`) both
        // stay — the shell's `except ParseError` and `describe()`'s dispatch are
        // written against them — but this assertion is what keeps them honest
        // instead of merely undeleted.
```

- [ ] **Step 2: Reshape the parity row**

`tests/test_parser_parity.py:857`, today's `test_an_unimplemented_tag_is_its_own_kind_at_the_ffi`. Its docstring's own last sentence — *"M13 implements the pair that is left"* — is the instruction: the row cannot name a stub because there is none, so its subject becomes the class relationship, and the missing producer is recorded rather than left as a silent hole. Note that two of its three assertions survive untouched (`assertNotEqual`, `issubclass`, and the `{% endif %}` control, which has always used a *syntax* error for that half).

```python
    def test_the_unimplemented_kind_has_no_producer_and_still_maps(self):
        """`UnimplementedTag` survives M13 with nothing left to produce it.

        Every milestone before this one had a tag to point at, and each moved
        this row's example forward — `{% if %}` until M5's Task 7, `{% load x %}`
        until M6 handed `load` to the host, `{% partialdef x %}` until M13's Task
        4. There is no fourth example: `builtin_tag_stub_names()` is empty, which
        `test_tag_inventory.py` asserts with a control drawn from the same
        population, so this row's subject is the *class* and no longer a tag.

        The relationship is what a caller depends on — the shell's single
        `except ParseError` still converts it, and `describe()` still dispatches
        on it — so it is asserted directly rather than through an input that no
        longer exists. What is **not** asserted is that some template produces it,
        because none does; the producer is `registry::unimplemented`, pinned on
        the Rust side by `registry.rs`'s
        `a_stub_names_its_milestone_and_says_it_is_the_engine_not_the_template`.
        """
        self.assertNotEqual(_native.UnimplementedTag, _native.ParseError)
        self.assertTrue(issubclass(_native.UnimplementedTag, _native.ParseError))

        # The control the old row got from a real stub, kept because it is still
        # true and still load-bearing: a syntax error is not swept into the "not
        # built yet" bucket. `{% endif %}` is the example this row has always
        # used for that half.
        with self.assertRaises(_native.ParseError) as syntax:
            _native.parse("{% endif %}")
        self.assertNotIsInstance(syntax.exception, _native.UnimplementedTag)
```

Check the row's neighbourhood before editing: `git grep -n "Unimplemented" tests/` — a second consumer of the class that feeds it a real input is a second producer to find, and if there is one this step is not done.

- [ ] **Step 3: Run the rows**

```bash
cargo test -p template-core registry
scripts/run_unit_tests.sh tests.test_parser_parity -v
```

Expected: PASS. If `describe()` or the shell's conversion has a row that feeds it a `Unimplemented`, that row is a second producer to find — `git grep -n "Unimplemented" tests/ python/ crates/`.

- [ ] **Step 4: Commit**

Message: `test: UnimplementedTag has no compile-path producer left — pin the class, not a tag`

---

### Task 7: The discriminator, and the per-route hole

`tests/test_host_tags.py` exists to make the vacuous-gate failure visible, and it used `partialdef` as the tag only one side implements. After M13 both sides implement it, so the discriminator must be replaced — not deleted, because the phenomenon it documents is still real and is now **per-route**: a `{% partialdef %}` inside a *host-compiled block tag's body* is still Django's work (`Library.simple_block_tag`'s compile function calls Django's `parser.parse`).

**Files:**
- Modify: `tests/test_host_tags.py` (module docstring `:1-45`, `:108-141`)
- Test: the same file; no production change.

**Interfaces:**
- Consumes: `ShellHost.register_partial_def` (Task 2), `_wrap_library()` / `_hosting_engine()` (already in the file).
- Produces: a permanent, falsifiable pair of rows: the seam is **not** called on the host route and **is** called on the Rust route.

- [ ] **Step 1: Replace the discriminator with a spy**

Two rows carry this today: `test_a_host_block_tags_body_is_parsed_by_django_and_that_is_asserted` (`:108`, whose reason sentence is *"Rust's entry for the name returns `UnimplementedTag`"*) and `test_the_discriminator_is_Rust_compiling_the_same_tag` (`:130`, which *is* the `assertRaises(UnimplementedTag)`). Both reasons expire at Task 4; the *phenomenon* does not, because it is now per-route.

The replacement has to be a difference *one side* produces, and after M13 the difference is no longer "who implements the tag" but "who owns the route". A spy on the seam is the same-population control called for by `control-over-the-same-population`: both rows call `_native.parse` with the **same** host class over the **same** tag, and only the route differs. Keep both row names' subject and rename them to what they now assert.

```python
    def test_the_nested_region_never_reaches_the_partial_seam(self):
        """The host route's half, asserted on the seam rather than on a tag.

        `do_partialdef` ran — that is the row above — and it ran inside Django's
        `Parser.parse`, so it reached `_impl/defaulttags.py`'s compile function
        and **not** `ShellHost.register_partial_def`. That is the hole this
        module documents, and it is now *per-route* rather than per-tag: M13 made
        both tags Rust's, and this body is still Django's because
        `Library.simple_block_tag`'s compile function parses it host-side.

        A spy rather than an `UnimplementedTag` assertion, because the class
        assertion M13 removed was a claim about a *stub*, and the claim that
        survives is about a *route*.
        """
        engine = _hosting_engine()
        host = host_module.ShellHost(render.parser_for("", engine))
        calls = []
        original = host.register_partial_def

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        host.register_partial_def = spy
        _native.parse(BODY_SOURCE, host=host)

        self.assertEqual(calls, [], "the host route reached the Rust partial seam")
        self.assertIn("p", host.parser.extra_data["partials"])

    def test_the_bare_template_reaches_the_seam_once(self):
        """The Rust route's half, over the same host class and the same tag.

        The control the row above needs: the spy *does* fire when Rust drives the
        parse, so `[]` there is a fact about the route and not about a spy that
        never fires. Same host, same tag, one argument's difference in the
        source.
        """
        engine = _hosting_engine()
        host = host_module.ShellHost(render.parser_for("", engine))
        calls = []
        original = host.register_partial_def

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        host.register_partial_def = spy
        _native.parse(BARE_SOURCE, host=host)

        self.assertEqual(len(calls), 1)
        self.assertIn("p", host.parser.extra_data["partials"])
```

- [ ] **Step 2: Fix the two reason statements and the citation**

The module docstring (`:19-22`) names `partialdef` as *"its Rust entry is still a stub"* — false from Task 4 onward. Its other sentence, `do_partialdef` storing into `parser.extra_data["partials"]` (`defaulttags.py:1250-1251`), carries an **unqualified** citation: our transcription's copy of that statement is two lines lower, at `_impl/defaulttags.py:1253`. Name the base:

```
  `defaulttags.py:1250-1251` (the pinned checkout, `.django/` @ 6.0.9 — our
  transcription's copy of the same statement is at `_impl/defaulttags.py:1253`,
  two lines later because the marker and its blank line are an insertion)
```

**Two citations in this file need their base named, and one of them is new *because* of Task 1.** The first is the module docstring's `defaulttags.py:1250-1251` for the `extra_data` store — unqualified, and our transcription's copy of that statement sits two lines lower. The second is `:108`'s docstring: *"which is `Library.simple_block_tag` verbatim (`library.py:211-212`)"* — a citation into a file that, since Task 1, is **ours**. Both get the same treatment:

```
  `defaulttags.py:1250-1251` (the pinned checkout, `.django/` @ 6.0.9 — our
  transcription's copy of the same statement is at `_impl/defaulttags.py:1253`,
  two lines later because the marker and its blank line are an insertion)
```

```
  `Library.simple_block_tag` (`library.py:211-212` in the pinned checkout,
  `.django/` @ 6.0.9; `_impl/library.py:213-214` is our copy)
```

**Get the second number by grep, not by adding two** — the offset is a measurement:

```bash
grep -n "parser.parse((end_name,))" python/django_rust_templates/_impl/library.py
```

`tests/test_host_tags.py:108`'s docstring premise ("Rust's entry for the name returns `UnimplementedTag`") dies too — that is §5(c) of the recon: a row that keeps passing while its stated reason expires. Its new reason is the route. Also `tests/test_parser_errors.py:129`'s parenthetical names the pair; check it and move it.

- [ ] **Step 3: Run the rows**

```bash
scripts/run_unit_tests.sh tests.test_host_tags -v
bash scripts/check.sh --fast
```

Expected: PASS, 442+ OK.

- [ ] **Step 4: Commit**

Message: `test(host_tags): the discriminator is a route, not a stub — spy on the partial seam`

---

### Task 8: Close-out

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-django-template-engine-in-rust-design.md` (§10.3's M13 row)
- Modify: `docs/feature-checklist.html` (both meter scopes, tag/filter counts, status bullets, date)
- Modify: `README.md` (the two rows)
- Create: `.review/m13-record.md`
- Modify: `.review/m13-measurements/README.md` if any instrument changed

- [ ] **Step 1: The full gate, from cold**

```bash
bash scripts/check.sh
```

Expected: exit 0, `all checks passed`, Django `Ran 1547 tests … OK (skipped=6)`, meter `unshimmed()` `[]` over both scopes, `builtin_tag_stub_names()` empty, anchors `--check --frozen` clean.

- [ ] **Step 2: Update the spec's M13 row**

§10.3's row is `Partials and library` — the recon §4 recorded that the registry's rule and the spec's row disagreed about *who* compiles the pair, and this plan resolved it in the rule's favour by *satisfying* it. The row says which, in one sentence, and names the gate labels (`test_partials`, `syntax_tests.test_partials`, `test_library`) rather than only `test_partials`.

- [ ] **Step 3: Update `docs/feature-checklist.html`**

Both meter scopes to zero, the tag counts (`25` including stubs → the registry's 22 with no stubs), the filter count (66), the status bullets for M13, and a re-stamped date. Commit it by pathspec if a dispatch is in flight.

- [ ] **Step 4: Write `.review/m13-record.md`**

M9–M12's shape: the headline, what the meter could see and what it could not, the decisions with their measurements, the instruments and their invocation traps, and the numbers at close.

One entry in the decisions section is a **defect category this project has not named before**, and it belongs in the record because the next milestone's stub-shrinking should look for it: `crates/template-core/src/parser/registry.rs:477`'s pin began life as a *redundancy* check and, the moment Task 4 lands, silently becomes `unimplemented(...)`'s **only** caller while its text stays true and its row stays green — *a row whose role changes under it*. Nothing reddens when that happens; the row has to be read to notice. The record names it, and Task 6's rewrite is where the row is re-keyed to its new role.

- [ ] **Step 5: Commit and send the plan's closing note to the reviewer**

Message: `docs: M13 closes — the last module, the empty stub set, and the live table`

---

## Self-review

**1. Spec coverage.** §10.3's M13 row names `test_partials`, `test_library`, partials and `library` → Tasks 1, 3, 4, plus the gate labels named in Task 8 Step 2. §5.2's `ExtensionHost` → Task 2's two seam methods. §6's transcription policy → Task 1's marker, `DECLARED` and `COMPARED_PATH`. §10.4 (`cargo test -p template-core` Python-free) → both new methods defaulted. The recon's §9's six open questions: (1) placement → **Task 3/4, `(a)`**: the hole is live and widening, and `(c)`'s honest version costs two instruments rather than a comment; (2) `parse_bits` → **Task 1, transcription**, on the `contrib/admin/templatetags/base.py:3` signature blind spot; (3) `import_library` → the same, by the same measurement (it calls `importlib` and reads `module.register`); (4) the message surface → Python's, by (2); (5) the discriminator → Task 7; (6) the transcription's declaration shape → Task 1 Step 5.

**2. Placeholder scan.** No "TBD"/"implement later". Two steps deliberately say *measure this, do not copy the value* (Task 1 Step 1's `COMPARED_PATH` spans; Task 4's source offsets) and one says *if this passes, the task reduces to a comment* (Task 5 Step 1) — each names the instrument and the falsifier rather than a number to inherit.

**3. Type consistency.** `partial_node(&self, name: &str) -> Result<NodeRef, Error>` and `register_partial_def(&self, token, name, inline, body: &NodeListDesc, source_start, source_end) -> Result<NodeRef, Error>` are spelled identically in Task 2's trait, its `Parser` forwards, its FFI methods, its test host and both callers in Tasks 3 and 4. `PartialDefCall`'s fields match what Task 4's rows read. `ShellHost.register_partial_def`'s parameter order matches the wire the FFI passes.

**4. Review Focus.** Each of the five has a named task and a real row: (1) Task 3 Step 5, then Task 4 Step 5's `test_a_partial_before_its_definition_renders` once both tags exist; (2) the check is Task 2 Step 8's (`ShellHost.register_partial_def`), the row is Task 4 Step 5's `test_a_duplicate_definition_names_the_origin_template` — project-side, because the corpus's own duplicate row is a gate label asserting Django's behaviour and not evidence about our route; (3) Task 4 Step 1's `the_accepted_spellings_are_exactly_two`, `whitespace_stops_the_scan_and_then_fails_the_whole_contents_check` and `the_wrong_end_tag_name_is_invalid`; (4) Task 7; (5) Task 5 Step 1.

**Two Review Focus items were falsified while writing this, and the corrections are the interesting part.** Item 3 originally claimed `{% endpartialdef   p %}` was *accepted* — reasoning that `parse_until` matches `token.contents.split()[0]`, which extra whitespace does not disturb. That reasoning is right about the scan and wrong about the tag: measured on the pinned tree at 6.0.9, two spaces, three spaces and a tab each stop the scan and are then **refused** by `partialdef_func`'s whole-contents check, with the single-space form the only named spelling accepted. Two consequences are now in the plan: Task 4's body compares whole contents while the scan (which is `Parser::parse`'s) compares the first word, and `next_token` after a successful `parse_until` parse is `expect`ed rather than matched — because `parse` returns `Err(unclosed_block_tag)` when the tokens run out (`parser/mod.rs:451-455`), so a defensive `else` is a branch no input can reach and no row can pin.

Item 5 was worse: it asserted two rendered values (`"aa"` correct, `"ab"` under the snapshot) and a causal story, and all three were wrong. Measured, Django gives `'aaa'`, and the reason is a mechanism the plan had not considered — `PartialTemplate.render` pushes a render-context state, so a named cycle **restarts inside every partial render** and the output no longer distinguishes a live table from a snapshot. So Task 5's row asserts identity instead of output, the instrument that decides it is committed at `.review/m13-measurements/m13_measure_cycle_across_partials.py`, and the task is explicitly conditional on its own row failing. The general lesson is the one this project keeps relearning: **a row's expected value is a claim like any other**, and the example that illustrates a mechanism needs measuring before the mechanism is argued from it.

**One gap found and closed while writing this:** Review Focus 2's row is a *project-side* row and the corpus's own duplicate-definition test (if any) lives in `template_tests.test_partials`, which is a gate label, not evidence about our route. Task 4 Step 5 must therefore add it explicitly rather than relying on the corpus.

---

## Amendment: four tasks in one commit, and a hypothesis confirmed by a mechanism the plan did not name

*Appended at Task 4's close. The plan's text is left as written; what follows is what the measurements say. Tasks 5, 6 and 7 did not run as tasks — their deliverables landed inside Task 4's commit, and the reason is structural rather than a shortcut.*

**Tasks 5, 6 and 7 were absorbed by Task 4, and each was absorbed by a different one of Task 4's own effects.** Every commit in this project is green, and Task 4 is the commit that (a) makes `{% partialdef %}` compile in Rust — which is what first materialises a body *mid-parse*, the condition Task 5 exists to test; (b) empties `builtin_tag_stub_names()` — which is Task 6's premise, "nothing returns `Unimplemented` any more"; and (c) hands both partial tags to Rust — which is Task 7's discriminator, "the tag only one side implements". So the moment Task 4 lands, Task 5's hazard is either present or not, Task 6's row has no subject, and Task 7's two rows state a reason that is false. Committing (a) without (b) and (c) is not a smaller commit, it is a red one. The four tasks are one deliverable, and the plan's separation of them was a plan-time convenience rather than a testable boundary.

**Task 6's premise is false in one sentence and its reason is false in another.** "Nothing returns `Unimplemented` any more" was measured wrong: `crates/template-core/src/parser/tags/verbatim.rs:23` still returns `Error::unimplemented` in production, on the arm its own row pins (a `{% verbatim %}` body that is not text), so the kind keeps a producer and the `ErrorKind::Unimplemented` arm at `lib.rs`'s `parse_error` is not unreachable. What Task 4 emptied is `builtin_tag_stub_names()` — the population the *derivation* probes — not the function's callers. The second premise was the stated reason for keeping the Rust-side pin: that it is the only thing between `unimplemented` and "silent deletion-by-clippy-dead-code". Measured, `registry.rs:428` is `pub fn unimplemented`, so dead-code analysis would leave it alone. The row is still worth keeping, but for what it actually holds — the *message* a user sees for an engine gap — which is how its comment now reads.

**Task 7's replacement is the one place the plan's own instruction was already the right one.** Its Step 1 asks for "a spy on the seam"; that survived unchanged, including the same-host/same-tag control. What the plan did not anticipate is that the *fixture* would need a source of its own: Task 7's two rows are the only rows in `test_host_tags.py` that call `register_partial_def` directly, and the identity they assert (`assertIn("p", host.parser.extra_data["partials"])`) is the assertion `test_the_nested_region_never_reaches_the_partial_seam` uses for the *opposite* direction — the host route reaching Django's `do_partialdef` and storing the same key. Two rows, one key, opposite claims.

**The unpredicted pieces, in the order they cost time.** Four of Task 4's changes are in files or shapes no step of this plan names:

1. **`FilterCheckHost::register_partial_def` had to hand the parse's reference table to the body it materialises** (`with_ref_table` at `crates/template-py/src/lib.rs:1748`, called on the body at `:1564`), and `FilterCheckHost.refs` had to become the **live Python list** rather than a `RefCell<Vec<Option<Py<PyAny>>>>` read out per call (`:1282`'s `ref_list` now returns the object itself; `fill_slot` at `:1299` is the new push helper the five `NodeRef` sites share). This is Task 5 Step 2's change, and it landed in Task 4 because without it the suite was **27 red** on `LookupError: the filter 'default' arrived as <NodeRef 0> but no resolve_ref was passed to materialise`. That is Task 5's *conditional* answered by the acceptance suite rather than by its identity row: the row the plan designated as the decision was not the instrument that decided it.
2. **`named_cycle_nodes` had to hold a `NodeDesc`, not a slot** (`crates/template-core/src/parser/mod.rs`; `tags/cycle.rs:91-119`, `tags/resetcycle.rs:40`). See below — this is the second thing the plan's hypothesis did not cover.
3. **`registry.rs`'s `stub_names_in(table)` split** — the derivation had to become a function over an arbitrary table so a test could hand it a **fabricated** stub (`:400`, `:673`). This is the negative-fixture rule from the other end: with the real stub set empty, the only way to keep the derivation's probe under test is to feed it a stub that no registry holds.
4. **Three housekeeping counts moved in this commit and each is a different population:** `tests/test_parser_parity.py`'s `describe_partial_template` (`:352`), `scripts/check_transcriptions.py`'s `COMPARED_PATH` rows, and `tests/test_collected_tests.py`'s `PINNED` 449 → **457** (`449 + 12 - 4`, measured by discovery after a first pass at this comment named only one of `test_partials_rust.py`'s two new classes and came up four short). The plan names none of them, and `COMPARED_PATH` is the one where a stale entry is *checked* (`check_transcriptions.py:1344`) rather than merely tolerated.

**Task 5's hypothesis was confirmed, and by neither of the two mechanisms the plan argued from.** The identity row failed, as Step 1 said it might. But the failure was not "a snapshot lost a filled slot": with the descriptor carrying only `.slot(idx)`, a body materialised **mid-parse** reaches a slot that the outer tree has not filled yet — materialisation runs after the parse returns — so there was nothing to lose and nothing to build from, and the shape raised `TypeError: CycleNode.__init__() missing 1 required positional argument: 'cyclevars'` on our side where Django renders `'aaa'` (measured, `.review/m13-measurements/m13_measure_cycle_boundary.py`, whose four shapes are the claim: **exactly one of the four moved**, and the other three agreed all along). So the live list is necessary but not sufficient, and the second change — echoing the definition's arguments into the descriptor, which is what `named_cycle_nodes: HashMap<String, NodeDesc>` is for — is the one the plan had no slot for. The reason it is still correct to call the hypothesis confirmed: the plan's *conclusion* ("the table is one object for the parse") was right, its *illustration* (`'aaa'`/`'aba'`) is right and already corrected once in its own text, and its *root cause* was a different one from the two it named.

**A mutation instrument that had silently expired, and the guard it now carries.** `.review/m13-measurements/m13_measure_stub_expiry.py` patches `builtin_tag_stub_names()` to `[]`, and after Task 4 the real set *is* `[]` — so `[] -> []` changed nothing and a green mutated run was indistinguishable from a green shipped one, which is the worst possible shape for a mutation instrument. It now reads the real set first and exits with an explicit message when the mutation has expired rather than running a comparison that cannot fail.

*Spec coverage, unchecked at plan time and confirmed at the close:* §10.3's M13 row → Tasks 1, 3, 4 (its gate labels are Task 8 Step 2's); §5.2's `ExtensionHost` → Task 2's two seam methods, with the trait defaulted so `cargo test -p template-core` stays Python-free (§10.4); §6's transcription policy → Task 1's marker and declarations. Type consistency held: `partial_node(&self, name: &str)` and `register_partial_def(&self, token, name, inline, body: &NodeListDesc, source_start, source_end)` are spelled identically across Task 2's trait, its `Parser` forwards, its FFI methods, `ShellHost`'s two (`host.py:641`, `:669`) and both callers. The one addition the plan did not have a name for is `FilterCheckHost::fill_slot`, which is a helper rather than a seam because the five `NodeRef` sites now share one push instead of five copies of it.
