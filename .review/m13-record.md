# M13 record

Partials and `library` — the extension API a `{% load %}`ed tag library is
compiled with. The engine's last module and its last two stubs.

Everything below is measured on the closing tree, against `.django/`
@ **6.0.9** on `.venv`/CPython **3.14.4**. Same rule as the M7–M12 records: a
number here whose argument cannot be re-run is one nobody re-examines.

---

## The headline

**Two meter scopes, one commit each, and the stub set empties.** `library.py`
was the engine's sixteenth module and the only one the meter still reported:
`unshimmed("django.template")` is **1 → 0** at `547bfb9`. The second scope,
`django.templatetags`, reached zero at M12 and does not move. So at the close
**both scopes read `[]`** — the engine is wholly Rust's, which is exactly M14's
criterion, reached one milestone early and over the population the scope
correction (`e731196`) set.

`builtin_tag_stub_names()` is **empty**. It held two names before this milestone
— `partial` and `partialdef` — and the milestone's real work was deciding that
they should not stay stubs at all. `builtin_tag_names_including_stubs()` is
**25**, which is the registry's 27 minus the two host-side names `load` and
`querystring`; the count does not move across M13, because emptying the stub set
takes the two names from *stubbed* to *implemented* without changing which names
the table holds.

---

## The decision: who compiles the partial pair

This was the milestone's actual design question, and the recon (§4) framed it as
a disagreement rather than a task.

**The registry's rule** (`crates/template-core/src/parser/registry.rs:117-132`)
says a tag is the host's when its compile touches host parse state that outlives
the parse. Both partial tags qualify, and `partial` is the stronger case:
`partial_func` builds `DeferredSubDict(extra_data, "partials")` — a **live view
over the parser's dict** — and hands it to `PartialNode`
(`defaulttags.py:1279-1280`), so the compiled node closes over parse state a
`NodeDesc` cannot represent at all.

**§10.3's row and M9's record expected the opposite** — that M13 supplies Rust
compile functions for both and the stub set empties. M9 stated it as a behaviour
change with a mechanism: a `{% verbatim %}` inside a `{% partialdef %}` body
reaches Django's `do_verbatim` *because* `partialdef` is a stub, "and it stops
doing so when M13 supplies that compile function."

**The plan took the rule's side and satisfied it rather than overruling it.**
Rust compiles both tags' syntax, body included (`13f9b08`, `24bed0f`), and the
two `NodeDesc`-unrepresentable things stay host-side behind two new `Host` seam
methods, each returning a `NodeRef`:

| seam | what crosses | why it cannot be Rust's |
|---|---|---|
| `Host::partial_node` | the live `DeferredSubDict(extra_data, "partials")` | a live view over a dict the parser owns; a `NodeDesc` is a frozen value |
| `Host::register_partial_def` | the `PartialTemplate`, the body's `source_start`/`source_end`, and the duplicate-name check | `partials[name] = PartialTemplate(nodelist, …)` and `return PartialDefNode(…, nodelist)` are the **same** nodelist object (`defaulttags.py:1257-1265`), so the body has to come back out of the host instead of being built twice |

The seam is committed at `e592fc3`; the two tags' Rust compile functions at
`13f9b08` and `24bed0f`.

### What the choice buys, measured rather than argued

The reason is the hole, not the rule, and the hole is the one
`tests/test_host_tags.py` was written to make visible: a host block tag's body
is walked by Django's own `Parser`, so **every tag inside it is compiled by
Django**. Under the fallback the recon listed — leave both host-compiled by
design — that hole stays open forever, and the parity corpus cannot see it,
because `assert_trees_match` over such a body compares Django's work with
Django's work. Closing it is the one outcome here that is measurable: a partial
body is now Rust-parsed.

### A row whose role changed under it — a defect category this project had not named

**`crates/template-core/src/parser/registry.rs`'s
`a_stub_names_its_milestone_and_says_it_is_the_engine_not_the_template` began
life as a *redundancy* check.** The two stub bodies in `tags/partial.rs` and
`tags/partialdef.rs` were `registry::unimplemented`'s production callers, and
`builtin_tag_stub_names()` reached the function *through* them. The moment Task 4
landed, both bodies became real, no stub calls `unimplemented` any more — and
that row silently became `unimplemented`'s **only** caller while its text stayed
true and its assertion stayed green.

Nothing reddens when a row's role changes under it. The row has to be *read* to
notice. This is a category, not an incident: it belongs in the record because
the next milestone's stub-shrinking will meet it again. Commit `cea06d9`
re-keyed the row to its new role, and a second instrument — `stub_names_in(table)`
at `registry.rs:400`, tested with a *fabricated* stub at `:673` — was added so
the derivation is exercised with something in the set rather than asserted
against an empty one.

**And `tests/test_host_tags.py`'s discriminator changed mechanism the same way.**
Until M13 it was a *tag* only one side implemented: `{% partialdef %}` was a Rust
stub, so a Rust-parsed body raised `UnimplementedTag` while a Django-parsed one
ran `do_partialdef`. Task 4 gave both tags Rust bodies, so the discriminator had
to become the **route**: the claim is now that `ShellHost.register_partial_def` —
the seam every Rust-compiled `{% partialdef %}` crosses — is *not* reached from a
body Django parses. Same class, same tag, one difference in the source. Its
module docstring was rewritten to say so.

---

## The two premises the plan got wrong, and how

Both were Review Focus items, both were falsified while writing the plan, and in
both cases **the example that illustrated the mechanism was wrong before the
mechanism was**.

### 1. `{% endpartialdef %}` with extra whitespace is refused, not accepted

The item reasoned that `parse_until` matches `token.contents.split()[0]`, which
extra whitespace does not disturb, so `{% endpartialdef   p %}` would be
accepted. That is right about the **scan** and wrong about the **tag**: the scan
that ends the block matches the first word (`base.py:561`, `:564`) while the
check that validates it compares the **whole contents** (`defaulttags.py:1244`),
and `Lexer.create_token` strips only the *outer* whitespace (`base.py:446`). So
two spaces, three spaces and a tab each stop the scan and are then refused, with
the single-space form the only named spelling accepted — seven spellings
measured in `m13_measure_endpartial_spellings.py`. The plan's own count is one of
the things this changed: the corpus's `the_accepted_end_spellings_are_exactly_two`
is a *count* row, and it is right because the two checks disagree, not because a
rule says so.

### 2. The named-`{% cycle %}`-across-a-partial row was wrong in all three values

The item asserted two rendered values (`"aa"` correct, `"ab"` under a snapshot)
and a causal story. All three were wrong. Measured, Django gives `'aaa'`, and the
mechanism the plan had not considered is `PartialTemplate.render` calling
`push_state` (`base.py:340-346`): a named cycle **restarts inside every partial
render**, so the *output* cannot discriminate a live reference table from a
snapshot at all. The fix is that Task 5's row asserts **identity**, not output —
`m13_measure_cycle_across_partials.py` is the instrument, and
`m13_measure_cycle_boundary.py` is the parity one: four placements of a
definition and its reference around a `{% partialdef %}` boundary, `'aaa'` /
`'ab'` / `'aba'` / `'aa'` agreeing in all four, **exactly one of which moved**
pre-fix (definition outside, reference in body: `TypeError: CycleNode.__init__()
missing 1 required positional argument: 'cyclevars'` on our side against Django's
`'aaa'`). The other three agreed all along, which is what says the descriptor echo
was needed *and* did not over-reach.

The general lesson, restated because this project keeps relearning it: **a row's
expected value is a claim like any other.** The example that illustrates a
mechanism needs measuring before the mechanism is argued from it.

---

## The reference table is the parse's live list

Task 5 was conditional on its own row failing, and it did. The mechanism is the
reference table: `_native.parse` returns a live list of `NodeRef`/`NodeSlot`
entries the parse and the materialiser fill, and the body of a
`{% partialdef %}` is parsed *with* that list (`with_ref_table`,
`lib.rs:1748`/`:1564`). `NodeRef` entries are host-filled (`call_tag`,
`check_filter`, `partial_node`, `register_partial_def`, `compile_filter`);
`NodeSlot` entries are reserved by `alloc_slot` and filled by the materialiser,
and `alloc_slot`'s only production caller is `tags/cycle.rs` — which is what makes
the cycle's slot the *only* entry a snapshot could lose.

The cross-boundary named cycle is exactly that case, and it is the one placement
of the four that moved. The fix echoes the definition's own arguments into the
descriptor, so whichever namer materialises first builds the node and the rest
find it in the table (`tags/cycle.rs:91-119`). `m13_measure_ref_fill_sides.py`
records the split that made it visible.

**A behavioural row cannot witness this defect**, which is why the identity
assertion is its only observable — a test-style preference turned into a fact by
measurement.

---

## The instruments, and their invocation traps

Ten instruments in `.review/m13-measurements/`, one README, and the README's two
tables are the point: **the invocation is a property of the question, not a
convention.**

- The shim installs through `scripts/shimpath/sitecustomize.py`, reached by
  `PYTHONPATH`; **`-I` implies `-s -E`, so it closes that route**. An instrument
  whose question is *what the pinned file says* runs `-I`; an instrument whose
  question is *which side fills this entry* runs under the shim and asserts
  `_shim.installed()`.
- `m13_measure_cycle_boundary.py` needs **two engines in one process**, so it is
  the mirror: with the shim live, `from django.template import Engine` is *our*
  engine (because `django.template.engine` is **not** in the shim's
  `NOT_THE_ENGINE`), and both columns would agree vacuously, in both the passing
  and the failing direction. It carries **two** guards — `_shim.installed()` must
  be False **and** `django.template.base.__file__` must be the pin's — because
  either one alone leaves a way to measure one engine twice. A `sys.path` insert
  at runtime does **not** install the shim, since `site` has already run.
- `m13_measure_counter_across_partials.py` runs in **both** modes on purpose
  (the expectation is stock Django's, the parity check is ours) and prints
  `side=` on its first line, because the two runs are otherwise byte-identical
  and a shim that failed to install would read as agreement.
- `m13_measure_stub_expiry.py` is a **mutation** instrument
  (`_M13_EXPIRY_MUTATE=1` patches the stub set). It counts consultations of the
  mutated function and exits if the count is zero, because "0 consultations" and
  "no failures" are indistinguishable in the output.
- **Two traps this directory paid for, recorded in the recon's §10 rather than
  only here.** A re-exec chain that forgets `DJANGO_RUST_TEMPLATES=1` measures
  **stock Django and reports `OK (skipped=6)`** — a green suite is not a working
  instrument. And this checkout's `runtests.py:386-389` defaults `--parallel` to
  auto while macOS `multiprocessing` **spawns** rather than forks, so an
  in-process mutation is not inherited by the children; `--parallel 1` is
  required and is not a performance choice.

**One instrument's own comment was false, and its own output said so.**
`m13_measure_counter_across_partials.py` said three top-level mentions "are one
node"; its identity section prints `all the same object False` and the row prints
`'000'`, so the comment asserted the opposite of the measurement printed beside
it. It was rewritten as the control it is.

And its two one-partial rows were added for a **restatement defect, not a wrong
value**. A *non-inline* `{% partialdef %}` renders nothing at its definition
site, so the counter's value depends on the partial **count** — `'0'` for one
partial, `'01'` for two, `'012'` for three, all three measured and all three now
rows. The owed row reached this milestone recapped as *"bare `{% partialdef p %}`
→ `'01'`"* with the count dropped, which names the one-partial shape, whose value
is `'0'`. **Nothing was measured wrongly: a measurement restated without one of
its inputs became a different measurement** — "a number must travel with its
population" at the level of a single row's arguments. The remedy is the shape
table: every value with its shape and its partial count beside it, in a file
rather than in a message. Both engines agree on all seven.

---

## Numbers at the close

Measured on the closed tree, not carried from an earlier row:

| | value |
|---|---|
| `scripts/check.sh` | green, `all checks passed` |
| Django suite | `Ran 1547 tests … OK (skipped=6)` |
| unit suite (`scripts/run_unit_tests.sh`) | **457** |
| `cargo test` (workspace) | **787** |
| anchors (`--check --frozen`) | **1471** occurrences / **866 of 866** cited / **0 failures** |
| differential | **48** cases identical (stock vs shim-installed) |
| `unshimmed("django.template")` | `[]` (was `["library"]`) |
| `unshimmed("django.templatetags")` | `[]` |
| `builtin_tag_stub_names()` | `[]` (was `["partial", "partialdef"]`) |
| built-in tags | **25** Rust of **27**; the two host-side are `load`, `querystring` |
| built-in + library filters | **66 of 66** (57 built-ins + the 9 the five libraries register) |
| the M13 gate | **80 rows over three labels**: `test_partials` (28), `syntax_tests.test_partials` (24), `test_library` (28) |
| `library.py`, as four questions | 492 lines, 6 top-level classes (2 of them **not** nodes — an exception and a registry), 2 module-level `def`s, `Library`'s 8 methods |

**The gate labels are named rather than only the first, and why each is a gate.**
`test_partials` is the pair's own rows; `syntax_tests.test_partials` is the syntax
corpus the two falsified premises above live in; `test_library` is the extension
API, whose transcription `scripts/check_transcriptions.py` guards by declared
differing hunks — its surface is closures Rust cannot call, so
`tests/test_parser_parity.py`'s `_library_engine` corpus, not the suite, is what
compares it against the pin.

---

## Not owed — landed earlier

Kept as the record of what was discharged, so nothing here reads as open:

- the `library` transcription and the meter's last step — `547bfb9`;
- the `Host` seam for the pair, both methods and the shell's half — `e592fc3`;
- the strict-mode refusal fix (it could not refuse in the configuration shipped)
  — `08de820`;
- the anchors' full repo-relative paths — `68d9925`;
- the registry note's two false sentences — `cea06d9`.
