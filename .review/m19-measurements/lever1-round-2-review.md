# Review round 2 — slice 5.5 lever 1's correction round (`d33fa12`)

Adjudicated against `.django/` @ Django 6.0.9 and CPython 3.14.4 (`.venv/`); our
source is the working tree at `d33fa12`. The live artifact
`python/django_rust_templates/_native.cpython-314-darwin.so` was swapped (backup
kept, restored and sha-verified at `3d3e99e63eddc036`) and `lib.rs` was
temporarily rewritten and restored (sha `3a3c47726829c991`, `git status` clean).

## Verdict
**APPROVE** — no BLOCKING; the correction's two falsified premises and its stale
argument are correct, measured, and honestly scoped. Two counts in section 8's
own audit do not match the tree.

## Confirmed findings

### 1. SHOULD-FIX (minor) — §8 undercounts the citations the +1 insertion moved: 11, not 9
- Where: `.review/m19-measurements/loop-keys.md:324-327` and the `d33fa12`
  message ("one citation (+27, missed) and nine (+1, re-keyed)"). Django side is
  not involved; the subject is our own citation set.
- What happens: the sentence "nine moved by +1 and were not [missed]" reads as
  *nine citations sat in the +1 zone*. It is eleven. At `c631d5b` the gated
  roots hold **12** `lib.rs:N` citation sites (`git grep -nE 'lib\.rs:[0-9]+'
  c631d5b -- crates python tests docs/superpowers/specs`): eleven point below the
  line-11 insertion (lines 146, 851, 1104, 1251, 2123, 2133, 2144, 2149, 2170,
  2716-2717, 2773-2778) and one points into the +27 zone (4573). `0018591b`
  re-keyed nine; it left `python/django_rust_templates/render.py:150` (→`lib.rs:1104`)
  and `render.py:151` (→`lib.rs:1251`) — both in the +1 zone. So the +1 insertion
  moved **eleven** targets by one line, and two of them were missed.
- The internal inconsistency this exposes: §8 counts `spec09:238` (stale by 3,
  `-` row) as a citation "its third insertion moved", but does not count
  `render.py`'s two (stale by 184/239, `-` rows) as citations "its first insertion
  moved". Both are stale `-` rows in the respective zones; the classification
  treats them oppositely. The repaired values `1289`/`1490` *incorporate* the +1,
  so "not by this commit's shift" is not accurate as written (a better sentence:
  "their error is dominated by drift since M12; the +1 adds one line to it").
- Fix: change "nine moved by +1 and were not" to "eleven moved by +1 — nine
  re-keyed, two left (and repaired below)"; or state explicitly that only
  snippet-carrying rows are being counted. No repair set changes; the conclusion
  (which citations to fix) is right.

### 2. NIT — §8 table row 2 gives the wrong anchor line for the `ForPlan.loopvar` insertion
- Where: `.review/m19-measurements/loop-keys.md:294` — "ten lines of
  `ForPlan.loopvar` doc | 3049 | +11".
- Measured: the hunk is `@@ -3051 +3052,11 @@ struct ForPlan {`; the ten added
  doc lines are new lines **3052-3061** (old line 3051, `loopvar: String,`, is
  replaced). `3049` is the pre-existing doc line
  (`/// iteration, after the counters, as ForNode.render writes it`). The other
  two table rows use the new-file insertion line (`11`, `4453`); this one is off
  by three. The shift (`+11`) is correct; only the label is.

### 3. NIT — §7 says the identity class's loop row "asserts an `isinstance`"
- Where: `.review/m19-measurements/loop-keys.md:271-272`.
- Measured: `test_a_rendered_for_holds_our_for_node`
  (`tests/test_interposition.py:4456`) asserts `self.assertIs(type(nodes[0]),
  defaulttags.ForNode)` and `inspect.getfile(type(nodes[0]))` — a `type(...) is`
  identity assertion, not `isinstance`, and not a render. The substantive point
  ("its one loop row ... [does not render]") holds, and the decline is otherwise
  well-supported by the 27/10 count.

### 4. NIT — the test row's `context.py:69` citation is the `def`, not the assignment
- Where: `tests/test_interposition.py:2258-2259` — "`BaseContext.__setitem__` is
  `self.dicts[-1][key] = value`, `python/django_rust_templates/engine/context.py:69`".
- Measured: `context.py:69` is `def __setitem__(self, key, value):`; the quoted
  assignment is at `:71`. The anchor row quotes the `def` line, so the manifest is
  honest — but the sentence names the assignment and cites the definition line.

## Plausible / unverified
- **The full `scripts/check.sh` "all checks passed"** is not independently
  reproduced here (it builds, lints and runs the 1547-test suite plus packaging).
  The anchor half is reproduced (see below). Report the full-gate claim as
  PLAUSIBLE, not CONFIRMED.

## What the prompt asked, and what I measured

**A — the two false premises (highest priority): the correction is RIGHT.**
- I rebuilt `lib.rs` from `git show c631d5b:crates/template-py/src/lib.rs` with
  `maturin develop --release` and got sha16 `dc174855d926defc` — byte-identical to
  `.review/m19-measurements/loop-keys/prefix.so`. So `prefix.so` *is* the pre-lever
  `c631d5b` build (and `fixed.so` == the shipped `3d3e99e63eddc036`).
- With each artifact resolved the way the code resolves it (confirmed by printing
  `_native.__file__` and its sha in the same environment after each swap), the two
  rows in class `FilterRoutingTests` pass on **both** builds:
  `test_a_non_ascii_loop_variable_name_reaches_the_context` and
  `test_a_loop_named_forloop_writes_the_item_over_the_dict` → `Ran 2 tests … OK`
  on `dc174855d926defc` and on `3d3e99e63eddc036`. Neither row discriminates the
  lever; the corrected docstrings ("A guard, not a discriminator"; "It pins the
  write order, not the lever's key identity") are accurate. A pure output-preserving
  optimization cannot be discriminated by an output test, which is the point.

  *(Provenance line added by the `d33fa12` corrections commit, not by this round. The
  bullet above quotes a count without naming the run, and this repository's provenance
  gate requires one. Rather than assert an invocation this round did not record, the
  same two rows were re-run for the record in one invocation —
  `.venv/bin/python -I -m unittest`, two labels, the artifact sha asserted after each
  swap — and that run is `loop-keys.md` §9 and `loop-keys/rows-two-builds.txt`. The row
  names and the result above are this round's own.)*

**B — `render.py`'s stale-argument correction: correct.**
- `Error::host_exception` (`crates/template-core/src/parser/error.rs:273`) is the
  only constructor that sets `class: Some(...)`; it is called from exactly one
  place, `lib.rs:1290` inside `python_host_error`. The other `Host` constructor
  (`error.rs:227`) sets `class: None`. So "constructed at exactly one place …
  carrying a class" holds.
- Three callers verified: `lib.rs:1490` (`call_tag`), `:1612` (`partial_node`),
  `:1687` (`register_partial_def`). Two non-`None` `last_error` sites verified:
  `host.py:746` (`call_tag`) and `host.py:849` (`register_partial_def`); `:610` is
  the `None` init. `ShellHost.partial_node` (`host.py:752`) assigns nothing — the
  asymmetry is real.
- Is it safe? The matcher (`render.py:224-227`) returns `host.last_error` only
  when `type(raised).__name__ == class_name`. Under the terminality in fact 3
  (parse arm `Err(error) => Err(error)` at `parser/mod.rs:438`, caller `?`), at
  most one host failure has occurred, so a non-`None` `last_error` is that
  failure's own object; a `partial_node` failure (assigning nothing) leaves it
  `None` and the matcher falls through to `TemplateSyntaxError(*error.args)`.
  The argument closes under terminality — **and** the residual risk is stated
  honestly: fact 4 ("measured, not structural") names the swallow-a-nested-parse
  case, records that exactly one function in the pinned 6.0.9 tree does a parse
  inside a `try` (`base.py:198`, whose except re-raises), and says the script must
  be re-run at M11 when `{% load %}`ed libraries join the population. If a stale
  `last_error` *did* survive and a later failure shared its type name, the matcher
  would hand back the wrong object — which is exactly why the identity restore is
  conditional on terminality, and the docstring says so. Nothing here is
  overstated.
- `parse_template` builds a fresh `ShellHost` (`render.py:322`), so nothing
  survives between parses; a succeeding `call_tag` leaves `last_error` untouched.

**C — the citation audit (section 8).** Every number except finding 1 checks out:
- `lib.rs` between `c631d5b` and `0018591b`: `@@ -10,0 +11 @@` (+1),
  `@@ -3051 +3052,11 @@` (+10 net ⇒ +11 cumulative),
  `@@ -4441,0 +4453,16 @@` (+16 net ⇒ +27 cumulative). Net +27; three insertion
  hunks. Verified.
- Nine re-keyed, each +1, by content: 146→147, 851→852, 2123/2133/2144/2149/2170
  → +1, 2716-2717→2717-2718, 2773-2778→2774-2779; each old and new line holds the
  claimed snippet (checked by `sed -n` on both trees). Matches the commit message.
- The missed citation: `spec09:238` → `lib.rs:4573`; the
  `render_value_in_context` doc line is at `4570` in `c631d5b` (3 high) and `4597`
  at HEAD (24 low). Re-keyed to 4597 with a snippet; verified.
- The gate blindness claim is **true**: in `_check_entry`
  (`scripts/check_python_anchors.py:658-682`) a `None` snippet skips the
  `snippet not in span` test, so a `-` row is checked only for the tree/manifest
  number agreeing and the line existing — it cannot fail on a moved line. The
  three drifted rows (`lib.rs:1104`, `:1251`, `:4573`) all carried `- - -` in
  `c631d5b:scripts/anchors.txt`; the two in `render.py` were correct at M12
  (`8c59934:lib.rs` has `fn python_host_error` at 1104 and the
  `.map_err(...python_host_error(py, &error))?` at 1251), so the "185/239, since
  M12" attribution is exact.
- `scripts/check_python_anchors.py --check --frozen` run independently: exit 0,
  `1587 anchor occurrence(s) checked, 931 cited of 931, 0 failure(s)` — reproduces
  the gate's anchor step. `d33fa12` also correctly re-keyed its own shift
  (`render.py:264`→`275`, in both `spec09:547` and `anchors.txt`).

**D — the counter probe: sound; ran it.**
- `.review/m19-measurements/counter_id.py` output on the shipped artifact: three
  renders return the identical four `counter` ids; `all distinct within a run:
  True`; `int("256")` cached / `int("257")` / `int("1000")` not; bools are the
  singletons; `ids[0][0] == id(1)` is `True`. The `ALIVE` list keeps every
  comparable object alive, so no address-reuse explanation survives; the
  `int(v), int(v)` pair holds both objects at the comparison. The §5 reword
  ("the values are still written; they are not *built*") is supported: for the
  report's rows (n ≤ 200) every counter is ≤ 256 and every `first`/`last` is a
  bool singleton, so no counter allocates.

**E — NIT 3 declined: counts right.** `ast` over
`tests/test_interposition.py`: `FilterRoutingTests` = 27 test methods, **10** with
`{% for %}` in the source, spanning 1780-2287;
`DefaultTagsInterpositionTests` = 4 methods over lines 4397-4530 (133 lines).
The decline is well-founded (see the NIT above about "isinstance").

**F — the checklist's bench.html tile: right.** `.review/m19-measurements/headline-lever1-b1.json`
`meta.date` = `2026-10-10 19:32 UTC`; `native` = `… sha256:3d3e99e63eddc036`
(shipped); `command` writes `…/docs/bench.html`. Geometric mean of `rust/django`
`best_us` over the three `real-world` rows = **0.4662 / 0.7974 / 1.3841** →
`0.47× / 0.80× / 1.38×`, matching `docs/bench.html`'s tiles exactly.

**Other checks.**
- `tests/test_interposition.py`'s edit is **docstring-only**: AST with docstrings
  stripped from `0018591b` vs `d33fa12` compares equal.
- `report.txt` regenerates **byte-identically** from the committed instrument
  (`loop_keys_ab.py report --work … --rounds 12 | diff` → clean), and the
  `0018591b`→`d33fa12` diff touches only the intercept clause — every other number
  is byte-identical, as the message says.
- `d33fa12` did not change `lib.rs`, so its edits cannot have moved any Rust line.

## What's working
- The correction round does not amend history: it corrects the two false premises
  and the stale `render.py` argument where they stand, and says so.
- The self-reported miss (the +27 citation) and the two-drifted-row repair are
  honest and reproducible; the probe's own first-version trap is documented rather
  than hidden.
- The anchor gate's scope and its `-`-row blindness are stated accurately, and the
  frozen anchor run reproduces green.

VERDICT: PASS
