# The root read, transcribed in Rust behind a per-use pin

§5.5 of `.review/m19-measurements/frame-rewrite-fix.md` closed the frame-rewrite
record by naming three cost levers and declining to take any of them, on the
argument that *"each would need its own measurement and its own review."* The
first — a loop's seven key strings, built once instead of per iteration — is
`.review/m19-measurements/loop-keys.md`. The second — the loop's write routed at
the innermost dict — is recorded below as **measured and not taken**, with the two
probes that measured it. This is the third, and §5.5 called it *"the largest of the
three."*

## 1. What the lever is

`PyTarget::get_item` (`crates/template-py/src/lib.rs:2215` and below in this tree;
the quoted body is the tree *before* the lever) resolved every lookup segment with
one line:

```rust
self.value.bind(py).get_item(bit).map(|found| found.unbind())
```

`PyObject_GetItem` on the context. For the **root** segment — the first component
of every name a template reads — that lands on `BaseContext.__getitem__`
(`python/django_rust_templates/engine/context.py:85-93`, transcribing the pinned
`.django/django/template/context.py:83-91`):

```python
for d in reversed(self.dicts):
    if key in d:
        return d[key]
raise KeyError(key)
```

Four things are in that method and each is load-bearing:

* **the walk is upward**, over `reversed(self.dicts)` — a `{% for %}` body reading
  `{{ title }}` finds `title` in a layer *outside* the loop;
* **membership is tested before subscription**, so `KeyError` from `d[key]` is
  never a control-flow signal, and a layer that is short a key is skipped rather
  than raised on;
* **the miss is `KeyError(key)`**, not `""` — the walk's `Arm::Item` classifies it;
* **any layer at all may be in `dicts`**, not only a `dict`.

§5.5's first draft of this lever got the target wrong — it named
`engine/context.py:209` (`def __getitem__(self, key):`, the bare
`self.dicts[-1][key]` of `RenderContext`, the innermost layer only, on a class the
ordinary render path never hands the walk) — and the fourth adversarial round
caught it. The correction is what makes the lever large: doing it in Rust means
transcribing **the whole search**.

The change adds `PyTarget::root_item` (`crates/template-py/src/lib.rs:2159`) and
makes `get_item` (`:2215`) ask it first. `root_item` returns
`Ok(Some(value))` when it is certain, and **`Ok(None)` — "use the real thing" — on
every path that is not**, which is the whole safety argument: the fallback is
Django's own method, run by the caller. Four conditions admit the transcription:

1. **`self.value is self.context`.** The root of a `resolve` is the object the walk
   was handed, so this *is* "we are at the root". A non-root segment fails it on
   the first test.
2. **`type(context).__getitem__ is BASE_CONTEXT_GETITEM`**, read on every use and
   never cached. This is the guard, and §5.5's fourth round is why it is shaped
   that way: an identity test on the *class* answers "is this the untampered
   class", which is not the question, because Python can rebind
   `BaseContext.__getitem__` while a body runs.
3. **`context.dicts` is a `list`.**
4. **every layer walked over is a `dict` or a `dict` subclass.** `ContextDict`
   (`engine/context.py:16`) is the subclass `push()` appends for `{% with %}` and
   `{% include %}`; a subclass is still Django's `d[key]`, so it is admitted. A
   layer that is not a `dict` at all — a `UserDict`, a custom mapping — is not, and
   the whole lookup goes back to Python.

Membership and subscription both go through the **object protocol**
(`PyObject_Contains` / `PyObject_GetItem`, which is what `in` and `[]` compile to)
and not through `PyDict_GetItemWithError`. For a plain `dict` the two readings
agree; for a subclass they can disagree, and the way to know which is right is to
build the disagreement rather than reason about it. A subclass defining only
`__missing__` does **not** produce one — condition "membership first" means a miss
is never subscribed to, and measured, both this walk and Django raise `KeyError`
there. A subclass that *also* overrides `__contains__` to claim a key it does not
hold does: membership passes, the subscription misses, and `d[key]` reaches
`__missing__` through CPython's `dict_subscript`, which is what `dict.__getitem__`
*is*. Measured on `class C(dict): __contains__ → True, __missing__ → "MISSING-" + key`,
Django answers `'MISSING-x'` — and so does this, where the raw C lookup would have
answered `''`.

The transcribed walk also re-reads `dicts.len()` on every step. `reversed(list)`
is a `list_reverseiterator`, and that object re-reads the list's length as it goes:
a layer whose `__contains__` shortens `dicts` ends the Python loop early and Django
raises `KeyError`. A length snapshotted once at the top would walk indices the
iterator would have stopped at, so the walk re-reads the length per step and a
shortened list **ends the search** — the iterator's stop is a certain answer, and
Django's `for` falling through to `raise KeyError(key)` is what that answer is.
The transcription returns that same `KeyError`. Treating a truncated stack as
"not certain" and bailing instead re-walked a stack the walk had already partly
consumed, and a bail could answer a key Django never sees (§8 records the
divergence the review found here; `tests/test_loop_read_guard.py` now pins the
agreeing answer).

### 1.1 The pin, and why its spelling is the soundness argument

`root_item` licences itself against `BASE_CONTEXT_GETITEM`
(`crates/template-py/src/lib.rs:2458`), which is not captured from the class on
first use but **read as a name from `django_rust_templates.engine.base`**, where
`engine/base.py:1443` binds it:

```python
BASE_CONTEXT_GETITEM = BaseContext.__getitem__
```

Those two spellings are one line apart and differ in *when they resolve*. Both
resolve once per process. The unsound one — `type(context).__getitem__` taken on
first use — can be taken **after** a project has rebound the attribute, and then
licenses the transcribed body against a class that no longer runs it. The symptom
would be silent: the guard compares the replacement with itself, passes, and the
walk answers a lookup the class no longer performs. Nothing else in the tree would
notice, because on every input that is not about the rebinding the two agree.

So the pin has to be the function as it stood **before any code outside the package
could rebind it**, and the binding's *position* is the whole of that claim. It sits
at the end of `engine/base.py` on purpose and for two reasons at once. Reading the
name requires importing that module, and **the import order of the package this
module is in is what makes that unavoidable** — not this module's own
`from .context import BaseContext` (`engine/base.py:61`), which is *downstream* of
the fact and was this record's first, wrong, reason for it. Any route to
`BaseContext` executes `django_rust_templates/engine/__init__.py` first, because
that is how a submodule is reached, and its first statement is
`from .engine import Engine` (`engine/__init__.py:26`) → `from .base import
Template` (`engine/engine.py:7`) → `engine/base.py`'s body, which is where the
binding happens. A project can therefore only reach `engine/context.py` after the
pin exists, and `tests/test_loop_read_guard.py` pins that ordering in a fresh
interpreter rather than the sentence. And the binding is below every
`COMPARED_PATH["base.py"]` span in `scripts/check_transcriptions.py`, which are
literal line ranges over this file — the last of them is `token_kwargs`'s, ending
on the file's former final line. A count that moves *below* every span re-measures
none of them. The insertion is declared there as `(1408, 1443)` so the parity
harness still knows what the file is allowed to differ by.

The pin is a comparison, so it is worth exactly what its right-hand side is worth,
and the control is `tests/test_loop_read_guard.py` — not the guard. That file's
`PinSoundnessProcessTests` re-runs the first render in a **fresh interpreter** with
the class attribute moved *before* it, because every row in the process that has
already rendered would have taken a lazy pin long ago. It expects `REBOUND`; `1`
is the failure reading, and it is what the lazy spelling prints.

## 2. Why the existing instrument could not answer it

**`frame_rewrite_ab.py` knows what a read costs and cannot price this one.**
Its `A{k}` family — k reads plus an include, k = 1, 2, 4 — fitted a slope of
**+0.1801 µs/read** render and **+0.1810** parse, and `B4 − B2` / `B2 − B1` agreed
to 4%. That is the *same route* this lever shortens: the mechanism paragraph in
§5.5 is explicit that what is bought there is "the price of crossing into Python to
ask, plus the Python-level loop that then answers." But it is a reading about a
**different pair of builds** — the ordered pass against the frame-deleted one — and
a price is a difference. It cannot say what a *third* build, with the loop in Rust,
does to the same read, and it has no row in which the read is not inside a
walk-owned `{% for %}` (§5.5: "it still applies to *every* root lookup the walk
makes and not only to loop variables").

**`loop_keys_ab.py` cannot be reused, and its control inverts.** Its `P` row —
fifty `{{ a }}` with no `{% for %}` anywhere — is declared there as *"the control
this change cannot touch"*, which was true of pre-built key strings and is **false
here**: `P` is a page of nothing but root reads, so for this lever it is a second
*subject* and it has to move. Reusing that script would have printed a control line
that is not true of the change under it.

**And `scripts/bench.py` cannot resolve it.** The corpus's rows hold few root reads
per render — `real-world/normal` is one loop over 50 rows — so a per-read saving in
the tens of nanoseconds is inside a tile's own scatter. The page is where the lever
has to *land*, not where it can be *measured*.

## 3. The instrument

`.review/m19-measurements/root_read_ab.py`. Rows, and each one's job:

| row | template | what it is |
|---|---|---|
| `L{n}` | `{% for r in rows{n} %}{% endfor %}`, n = 1/10/50/200 | **the control**: all the per-iteration machinery and **no read of any kind**. A lever in the read path cannot move a body that reads nothing, so if `L`'s slope moves, the instrument moved and nothing below it is about the code. |
| `V{n}` | the same loop with one `{{ a }}` a row | **the subject.** `a` is a plain name, read at the root: one transcription per iteration. |
| `W{n}` | the same loop with one `{{ r.name }}` a row | the subject under real work — a root read beside the `getattr` arm. |
| `P` | `{{ a }}` × 50, no loop | **the other route**, and a subject rather than a control (see §2). |

Four loop lengths give `cost(n) = a + b·n`, so the slope **is** the per-iteration
cost, and the reading this file exists for is `slope(V) − slope(L)` — the cost of
exactly one root read — taken as a difference of fitted slopes rather than as a row
minus a floor, because `L` and `V` are not the same template and do not share an
intercept. Both intercepts are printed beside every slope for that reason.
`slope(W) − slope(L)` is the same quantity with `getattr` beside it, and `P`'s
paired per-row delta divided by fifty is the same quantity on the non-loop route.

Iteration counts are calibrated **once per (row, mode) on the rust engine** and
used for both engines, so neither is handed a tighter best-of-N. `ab` copies one
artifact into `python/django_rust_templates/_native.<abi>.so` between runs and
**asserts the sha it finds there**, resolved the way the code resolves it, so a
round that ran the wrong build is a refusal and not a number; the live `.so` is
untracked, which is what makes the swap safe. Two builds, 12 rounds a side, the
order reversed on even rounds so each round's two runs are adjacent and
`f[i] − p[i]` carries whatever drifted between rounds in both terms and cancels it.
Three modes: `render`, `parse` and `compile`. The last is the null: a read's cost
lives in the render and in `from_string`'s walk, and neither build compiles a
root read differently. A round's payloads are
`.review/m19-measurements/root-read-fix/{prefix,fixed}-r01..r12.json`, with both builds'
shas and the pins the swap asserted in `pins.json` beside them. The two `.so` files sit
in that directory as well and are deliberately **not** committed — `*.so` is ignored
at `.gitignore:25` — so the sha in `pins.json`, not a file, is what names a build.

## 4. The result

**The bound first.** This run's scatter, `(max − min) / min` over the twelve
rounds of one side, is **2.37%–13.11%** per cell — thirteen label-rows × three modes
× two sides, seventy-eight cells — and **3.99%–13.11%** if each label-row is charged
its wider side, median 5.12% / 5.98%. A paired delta smaller than the scatter of
its own row is not a measurement of the code. **This run is the wider of the two
taken here** — the pre-repair run's own range was 1.67%–9.61% per cell — and the
wider bound costs the record one of its two separation claims. The pre-repair run could
say that the widest cell (`W10` parse, 9.61%) and the `L` family's worst (5.89%)
were far apart; here the widest cell is still a subject row (`W10` compile,
13.11%) but the `L` family reaches **`L200` render 11.73%**, so *the bound no longer
separates the row the lever cannot reach from the rows it can*, and every reading
below is carried by the **paired** column rather than by the separation. That is
the row-to-row spread the paired estimator exists to survive: `cmd_ab` runs a
round's two sides back to back and reverses the order on even rounds, so
`f[i] − p[i]` carries whatever drifted between rounds in both terms and cancels it.
The paired column is not decoration here; on a run this wide it is the reading.

The unit, as four independent fits of `cost(n) = a + b·n` over `n ∈ 1/10/50/200`,
each in µs/iteration:

| family | mode | prefix | fixed | delta |
|---|---|---|---|---|
| `L` empty body | render | +0.1015 | +0.1019 | **+0.0004** |
| `V` one root read a row | render | +0.7874 | +0.6988 | **−0.0886** |
| `W` one read and a `getattr` | render | +0.5870 | +0.5161 | **−0.0709** |
| `L` empty body | parse | +0.1017 | +0.1024 | **+0.0007** |
| `V` one root read a row | parse | +0.7966 | +0.6978 | **−0.0988** |
| `W` one read and a `getattr` | parse | +0.5882 | +0.5224 | **−0.0659** |
| `L` empty body | compile | −0.0002 | −0.0000 | +0.0002 |
| `V` one root read a row | compile | +0.0003 | +0.0006 | +0.0003 |
| `W` one read and a `getattr` | compile | +0.0006 | +0.0005 | −0.0001 |

Read as `V − L`, the cost of exactly one root read:

| mode | prefix | fixed | delta |
|---|---|---|---|
| render | +0.6859 | +0.5969 | **−0.0890 µs/read** |
| parse | +0.6948 | +0.5954 | **−0.0994 µs/read** |

Two fitted readings of one quantity, **11.7% apart** — where the pre-repair run's
two sat 1.4% apart, and that disagreement is itself part of this round's result
rather than a blemish on it. The six fit-free readings below land between them.

**Six more estimates of the same number, none of them using the fit.** The paired
per-row difference `(V − L)/n`, which is the instrument's own arithmetic with no
least squares in it, runs **−98.6, −91.8, −91.2** ns/read in render and **−92.3,
−90.7, −90.9** in parse for `n = 10/50/200`. That is six readings spanning −90.7 to
−98.6, a 8.7% span around their centre **−92.6**, and they sit **between** the two
fitted readings −89.0 and −99.4 rather than agreeing with either. So the number to
carry is **≈ −93 ns a read**, and the honest form of it is a range: **the per-read
saving is measured at −89 to −99 ns**, the fit at the ends of the range and the
fit-free arithmetic inside it. `n = 1` is deliberately not among them, and for the
reason this file takes a difference of slopes rather than a row minus a floor: `L`
and `V` are not the same template, so a one-iteration row is their intercept
difference plus one slope difference, and the two intercepts are 0.42 µs apart in
render. Its readings here are `−118.8` render and **`+2.2`** parse — the second is
the clearest possible statement of why that row is excluded.

**The repair's cost is in this table.** The same instrument on the pre-repair build
read `V − L` at **−0.1027 / −0.1041**, with six fit-free readings spanning −97.9 to
−106.9, centre −103. The repaired build reads −89.0 / −99.4 with six spanning −90.7
to −98.6, centre **−92.6**. So the round's three repairs — the exact-`list` check,
the `PyDict` pre-pass over every layer, and `break` instead of a bail — cost
**≈ 10 ns a root read, about 10%**, and the pre-pass is the whole of it: the other
two are one flag test per read and a branch that does not run. Ten nanoseconds a
read is 2.6–3.7 µs on the 257–370 reads of §5's pages — 1.6%–2.3% of the fastest
page — so the tile below is not expected to move by a resolvable amount, and §6
carries the choice and its alternative rather than leaving it implied.

`P` is the same quantity on the other route — fifty `{{ a }}` and no `{% for %}`
anywhere. Paired, it moved **−3.940 µs over fifty reads = −78.8 ns/read** in render
and **−3.501 = −70.0** in parse. As in the pre-repair run, `P` brackets the six
readings above rather than tightening them and is reported as the same order of
magnitude and not as a seventh estimate. What `P` settles is the *prefix* side,
which is where the lever's target has to be right: its render row divided by fifty
is **0.6942 µs/read** against `V − L`'s 0.6859 — the two routes agree on what a
read costs to 1.2% **before** the change, and that agreement is what makes a read
the thing the transcription transcribes.

**The second estimator of the same saving, and it reads a fifth to a third
smaller.** `W` is one root read *plus* a `getattr` a row; the lever transcribes the
read and does not touch the attribute arm, so `W − L` ought to read what `V − L`
reads. It reads **−0.0709** µs/iteration render and **−0.0659** parse against
`V − L`'s −0.0886 and −0.0988 — a **20% and 33%** shortfall, and a disagreement the
pre-repair run carried too (there −0.0732/−0.0755 against −0.1030/−0.1046, or 29%
and 28%). Two readings of one quantity that disagree by a fifth to a third is not
rounding, and the honest form of the finding is the range rather than either end:
**the per-read saving is −66 to −99 ns depending on which row it is read from**,
and `V` — the row with nothing in it but the read — is the one §5's page prediction
uses. `W` is also this run's noisiest family (`W10` compile scatters 13.11%, the
widest of the seventy-eight cells, `W50` render 8.91%), so its fitted slope is the
reading least able to resolve a 20 ns difference either way. What keeps `V`'s figure
from being the optimistic end of a choice is §5: the three pages are an independent
route, and it is the pages that decide.

**The control's slope is flat, and that is the whole of what it has to say.**
`L`'s slopes move by **+0.0004** render and **+0.0007** parse — 0.4% and 0.7% of
their own slope, against cell scatter of 5.5%–11.7% on the render rows and
5.5%–7.8% on parse — so nothing per-iteration happened to a body that reads
nothing. `L`'s *intercept* falls, −0.079 µs render and −0.224 parse,
and that is the lever rather than a leak: `{% for r in rows %}` resolves `rows` at
the **root**, once a render, so every family's intercept carries exactly one root
read, and a level shift of one read per render belongs there — the parse shift is
wider than one read because the parse rows' intercepts are 18.6–23.2 µs and their
twelve-round scatter is 3.8%–7.8%, which is 0.7–1.8 µs. The other four
families' intercepts are the wrong place to look for corroboration — they move
−0.005 to −0.400 µs, a spread four times the 0.09 µs being read, on rows whose own
one-iteration scatter is 1–2 µs — and it is precisely why the reading this file
exists for is a difference of **slopes**: only the slope is the per-iteration cost.
The intercepts are printed beside every slope because a slope read without its
baseline is the mistake §5.5's own first lever already paid for once, not because
they carry the finding.

**The null, and it is a null.** Compile's slopes are `L` `−0.0002 → −0.0000`, `V`
`+0.0003 → +0.0006` and `W` `+0.0006 → +0.0005` µs/iteration — the largest move in
the mode is `V`'s `+0.0003` — and `V − L` moves **+0.0001 µs/read**. `P`'s paired
compile row moves **+0.485 µs, 0.29% of it**. A read's cost is not paid while
compiling, and this instrument says so in the mode where it cannot be.

**What the number says about §5.5's comparator.** §5.5 priced *"a read that leaves
Rust"* at **+0.17 to +0.25 µs**, and this lever recovers **37% to 55%** of that. The
reason is the one §6 leads with: the transcription still pays a
`PyObject_Contains` and a `PyObject_GetItem` for every layer it walks. What it
removes is the Python frame, the Python-level `for`, and the
`PyObject_GetItem` that opened the method — roughly half the round trip, with the
per-layer protocol left where it was. That is a finding rather than a
discrepancy, and §9 declines to decompose the rest of the 0.597 µs a root read
still costs after the change.

## 5. The headline benchmark

The unit above is a micro row. §5.5's levers exist for pages, so the same two
artifacts were put through `scripts/bench.py` — four full runs, **ABBA** over the two
builds, the artifact copied into the package *and* into cargo's output directory, with
its sha asserted after every swap in both places.

This is the **second** ABBA over this pair. The first was taken on the pre-repair
artifact, and the round's repair to the fast path — the exact-`list` check, the `PyDict`
pre-pass over every layer, and `break` where the build used to bail — changed the code
the numbers were about, so every figure keyed to `f702a231cf4fa410` expired with it.
The *pair* is unchanged: `prefix` is still lever 1's `3d3e99e63eddc036`, and the treated
side is the repaired `caf1589798eac0cf`:

```
.venv/bin/python -I scripts/bench.py --json headline-lever3fix-a1.json              # 3d3e99e63eddc036  21:55 UTC
.venv/bin/python -I scripts/bench.py --json headline-lever3fix-b1.json --html docs/bench.html   # caf1589798eac0cf  21:58 UTC
.venv/bin/python -I scripts/bench.py --json headline-lever3fix-b2.json              # caf1589798eac0cf  22:01 UTC
.venv/bin/python -I scripts/bench.py --json headline-lever3fix-a2.json              # 3d3e99e63eddc036  22:04 UTC
```

The four stamps are the payloads' own (`meta.date`), not the shell's clock. The second
placement is not decoration: `bench.py`'s `native_artifact()` hashes
`target/release/lib_native.dylib` to name the *profile* a row was built with, so a swap
that touches only the package leaves the "before" rows printing `unknown — no built
dylib has this hash`, and a dylib left on the old build is what the next
`maturin develop` copies back into the package, reverting the tree with every gate
still green.

Payloads: `.review/m19-measurements/headline-lever3fix-{a1,a2,b1,b2}.json`; `b1`'s run is the one
that wrote `docs/bench.html`. Every figure below is derived from those four files under
the rule lever 1's record states, restated because it is not implied at each use: a
side's figure is the mean of that side's two runs (`before` = `a1`,`a2`; `after` =
`b1`,`b2`), a ratio is that side's mean `rust` time over that side's mean `stock` time,
and a paired delta is per pairing — `a1`→`b1` and `a2`→`b2`. The two `--compare`
transcripts are `.review/m19-measurements/headline-lever3fix-compare-p{1,2}.txt`, and
the driver that took them is archived beside them (`headline-lever3fix.sh`, with its
log). §5's last paragraph is about what the two ABBAs of this one pair say to each
other.

**The band, measured rather than assumed.** `--band` on the before build's own two runs:
those rows scatter with median |rel| **1.37%** and max **10.63%** over the 54
`(row, mode)` cells, and every delta below is read against it. The band is a
*property of a pair of runs*, not of the rows, and this set is the second measurement of
it over the same artifact: the previous set read median 1.48% and max 5.34%. The median
reproduces to a tenth of a point; the max doubles. So a row can clear "the band" in one
pairing and not the other on that difference alone, and the movers below are counted
with that in mind. One control the tool does not print: the **stock** and **Jinja2**
engines' own best times on the three whole-page render rows move **1.02%–2.05%** and
**1.52%–3.37%** across these four runs, so a whole-page delta under about 3% is the
harness and not the code. Ours moves 8.81%–11.82% on the `normal` and `large` render
rows, in both pairings and in the direction of the change; `complex` render is the page
that does not resolve and is named below.

**The tiles** — geometric mean over the three real-world pages, `ours / stock`, lower is
better (the same figure `docs/feature-checklist.html` publishes):

| | before (`a1` / `a2`) | after (`b1` / `b2`) |
|---|---|---|
| render | 0.454 / 0.461 | **0.429 / 0.414** |
| parse | 0.790 / 0.802 | **0.760 / 0.759** |
| compile | 1.363 / 1.403 | 1.373 / 1.352 |

Under lever 1's side rule (mean of the two runs, then ratio, then geometric mean) the
same readings are render **0.457 → 0.421**, parse **0.796 → 0.759**, compile
**1.383 → 1.362**. So the published render tile goes **0.46× → 0.42×** and parse
**0.80× → 0.76×**, and compile moves **1.38× → 1.36×** — −1.5%, on a lever that cannot
touch compiling, read on a mode whose rows §4's compile null says do not move and whose
four per-run readings here span 1.352–1.403 (3.8%) on the treated side alone. The render
tile is the one to carry: **0.42×**, the same figure the pre-repair set published, and
§4 predicted exactly this — the repair costs ≈10 ns a root read, which is 1.6%–2.3% of
the fastest page, smaller than the tile can resolve. The `1.37×` the regenerated
`docs/bench.html` prints is its own run's second decimal of the same quantity.

**The whole-page renders.** Absolute microseconds, render mode, and the paired delta:

| page | before (`a1` / `a2`) | after (`b1` / `b2`) | paired | band |
|---|---|---|---|---|
| `normal` | 185.5 / 188.0 | **167.1 / 167.0** | −8.81% / −10.17% | 1.14% |
| `complex` | 503.0 / 507.7 | **492.7 / 468.3** | −0.87% / −8.72% | 3.01% |
| `large` | 254.9 / 259.7 | **240.5 / 228.1** | −6.51% / −11.82% | 0.86% |

`normal` and `large` are outside the band in both pairings, in `render` and again in
`parse` (−9.18%/−5.39% against 2.47%, −0.43%/−2.86% against 1.66%). **`complex` does
not resolve in this set**: pairing 1 puts it at −0.87% on a 3.01% band — `inside`, i.e.
"not resolvable to better than the band", which is not the same finding as "did not
move" — while pairing 2 puts it at −8.72%, well outside. Its parse rows behave the same
way (−1.71% inside, −7.88% outside). The pre-repair set had all three pages outside in
both pairings, so this is the set-to-set difference showing up at page granularity, and
the honest form of it is: two of the three pages moved, the third moved by an amount
this set can read in one pairing and not the other. Across the 54 cells, pairing 1 puts
**33 outside** their own band and pairing 2 **37**. **28** are outside in **both**, of
which **19 moved down**, **4 moved up**, and **5 disagree in sign**. The four up-in-both
rows are `real-world/normal` compile — taken apart below — plus three micro compiles
(`micro/loop-var` +7.34%/+0.14%, `micro/lookup` +2.31%/+1.86%, `django-only/escape-table`
+3.07%/+2.11%). The five sign-disagreeing rows are `django-only/filter-arg` compile
(+2.57% / −3.15%), `micro/include` render (+2.75% / −2.63%), `micro/include` parse
(−1.38% / +1.52%), `micro/extends` parse (+2.32% / −0.78%) and `micro/text` parse
(+6.42% / −0.69%), and **every one of them is outside its band in both pairings** —
three of the five by comparable magnitudes in opposite directions. The previous version
of this section read rows of this kind as "drift, each inside the band on the pairing
that contradicts them". That reason was wrong, and it was wrong for the pre-repair set
too: its three sign-disagreeing rows (`micro/filter` compile, `micro/text` render,
`django-only/filter-arg` compile) are outside their band in both pairings as well. The
label these cells earn is not drift but **not resolvable by this design** — an ABBA
resolves a change only where its two pairings agree, and these are the cells where they
do not. None is a whole page and no finding above rests on one; `django-only/filter-arg`
compile is on both sets' lists, which is at least consistent with it being a property of
the row rather than of the change.

**The pages' saving closes against the unit, and that is new.** The per-read unit is
0.0890 µs (§4, the render-side fitted reading; the unit's own range is −89 to −99 ns).
The pages are not loops of one read, so the count that has to be
multiplied by it was measured rather than estimated: a `sys.setprofile` hook filtered on
the pinned function's own `__code__` — `BaseContext.__getitem__`, which `Context` and
`RequestContext` do not override, so one code object catches every subclass — counts
every root read one render makes, under each build. The count is unchanged by the
repair: 0/12/0 reads leave Rust on `normal`/`complex`/`large` under both builds.

| page | root reads, prefix | still leave Rust, fixed | covered | × 0.0890 | measured (render) | measured / predicted |
|---|---|---|---|---|---|---|
| `normal` | 257 | 0 | 257 | 22.9 µs | −19.7 µs | 0.86 |
| `complex` | 276 | 12 | 264 | 23.5 µs | −24.8 µs | 1.06 |
| `large` | 370 | 0 | 370 | 32.9 µs | −23.0 µs | 0.70 |

Three pages, three ratios **0.86 / 1.06 / 0.70**. The unit's own range moves each of
them (0.77/0.95/0.63 at the −99 ns end, 0.83/1.01/0.67 at the fit-free centre) but not
their order, so the shape is invariant to which end of the unit is taken: **`complex`
closes to within 6% and `large` is short by a third, at either end.** That shape is the
one the pre-repair set read — 0.92 / 1.11 / 0.77 against the same counts and a unit of
0.1027 µs — so it replicates across two ABBA sets whose page deltas differ by 18%–21%
and whose units differ by 10%. What replicates is the *shape*; the bound it supports is
**±30%**, not the ±25% this section claimed when `large` read 0.77. `normal` and
`complex` bracket 1.0 and `large` is the outlier, and §9 says what that could be rather
than assuming it is the count.

Lever 1's record could not close this arithmetic — its pages saved roughly *twice* what
its per-iteration unit accounted for and it said so without explaining it — and the
count above is the explanation, offered as a **hypothesis about that lever and not as a
measurement of it**: lever 1's arithmetic multiplied a per-iteration quantity by an
*estimated* iteration count (50, 84 and 60 for these three pages), and these pages
execute 257–370 root reads, so the estimate was the loose term. This file does not count
iterations and does not claim it.

**The 12 reads that still leave Rust, named rather than left as a residue.** The same
hook records the frame that called each one: all 12 come from
`NodeList._render_with_plan` (`engine/base.py:1209`), the *delegated* route — the
nodelist Python renders itself when the walk hands it a `BlockNode`, one call a section
on the 12-section page. Those reads were never the walk's, so on this corpus the lever
transcribes **every** root read the walk makes on all three pages, and the fallback
§5.5's third lever specifies — *"a fallback to Python for any layer that is not a
`PyDict`"* — never fires on these three. That is a statement about this corpus and not
about the condition, which §6 keeps.

**The goal, and the first page it is met on.** The port's time as a multiple of Jinja2's,
per run:

| page | before (`a1` / `a2`) | after (`b1` / `b2`) |
|---|---|---|
| `normal` | 1.065 / 1.058 | **0.944 / 0.947** |
| `complex` | 1.444 / 1.411 | 1.409 / 1.344 |
| `large` | 1.260 / 1.265 | 1.172 / 1.125 |

`normal` crosses 1.00 — **the port is faster than Jinja2 on a whole page** — and it does
so in *both* after-runs and in *neither* before-run, in this set and again in the
pre-repair set (1.075 / 1.085 → 0.932 / 0.948), which is what makes it a reading and not
a row that happened to land low. Over the three pages the geometric mean moves
**1.247 / 1.236 → 1.159 / 1.127**, and every after figure lies below both of its page's
before figures. `complex` and `large` still lose — by 1.34×–1.41× and
1.13×–1.17× — so Jinja2 is still the fastest engine on two of these three pages, which
is what the regenerated page's tile says and what the goal in §1 is still open on.

That flip is also what broke a tile, and the tile's caption is part of the **previous**
commit (`9a8f5f8`). `bench_report._fastest_tile` picks whichever engine won the most
whole-page renders and then wrote "`{leader}` wins **every one** of these {n} pages" — a
sentence true only while the leader also wins all of them. It was true of every run until
that one; the regenerated page printed `jinja2 2/3` beside a note claiming jinja2 won all
three. The clause is read off the per-page winners now, so it reads "jinja2 wins 2 of
these 3 pages, at 0.30x-0.42x stock Django's own time, and real-world/normal to rust",
and the `3/3` branch is kept for the runs that still sweep. **This** commit re-renders
the page from the repaired build's `b1` payload — `docs/bench.html` carries
`sha256:caf1589798eac0cf`, and its caption's range is this set's
(jinja2/stock = 0.417 / 0.404 / 0.300 on the three pages) — and because
`bench_report.render(bench.load_payload(...))` is a pure function of the payload, the
page's provenance is the ABBA's recorded after-run: re-taking the ABBA cost no second
caption fix and no second measurement of anything.

**The one row that moved against us, and what it is.** `real-world/normal` compile is
the only whole-page cell outside its band in both pairings *and* positive: **+3.19% /
+3.14%** on a band of 1.41%. The two pairings agree on the ratio to 0.05 points, which
looks like a reading — and the terms that produce it do not agree at all: between `a1`
and `b1` the numerator moves 170.82 → 177.98 µs (**+4.19%**) while the denominator moves
125.25 → 126.47 (**+0.97%**); between `a2` and `b2` the numerator moves
173.13 → 172.03 (**−0.64%**) and the denominator 128.76 → 124.06 (**−3.66%**). A ratio
of two noisy quantities can land on the same value by opposite routes, and that is what
this is: the port's own compile time moved +4.19% and −0.64% across the two pairings,
against §4's compile null. Compile of the whole pages is reported as a **null with one
noisy cell in it** and not as a finding — the full compile moves are −3.20% … +4.19%
with inconsistent signs across the three pages and the two pairings.

The arithmetic that does not close here is not the page saving — that closed above — but
the *rest* of a read's cost: a read still costs 0.597 µs after the change, 87% of what it
cost before, and this file does not decompose it (§9).

**The two ABBAs of one pair, read against each other.** The pre-repair set and this one
measure the same `prefix` artifact against two builds one repair apart, and the two sets
disagree in a way worth stating plainly, because it bounds every delta above:

* **The null side moved.** `prefix`'s own render tile reads **0.457** here, **0.468** in
  the pre-repair set and **0.464** in lever 1's — one artifact, three sets, a 2.4% spread.
* **The treated side did not.** The two treated artifacts — `f702a231cf4fa410` and the
  repaired `caf1589798eac0cf` — both give a render tile of **0.421**, agreeing to 0.001,
  which is what §4 predicted: ≈10 ns a read is 1.6%–2.3% of the fastest page.
* **The lever's size is therefore not resolvable to better than the null's own scatter.**
  The delta reads 0.047 in one set and 0.036 in the other; at page granularity the same
  saving reads −24.3 / −30.2 / −29.0 µs in one set and −19.7 / −24.8 / −23.0 µs in the
  other — **18%–21% apart**, on pages whose stock-side times differ by up to 4.4% between
  sets (stock `normal` render: 410.9 then, 429.0 now).
* **What survives is the level and the shape.** 0.42× for the render tile, and the
  closure order `complex` > `normal` > `large`, both reproduce; the per-page saving does
  not, and this section's tables are the second measurement of it rather than a
  refinement of the first. A claim about *how much* this lever is worth has to carry that
  band; a claim about *where it left the port* does not.

One further consequence, since §5 is where the numbers are: the four runs of this set are
one instrument's reading of one pair of builds, and where a later slice wants a delta out
of them, the honest comparison is like-for-like within this set. The `before` numbers
here are *this* set's, not the pre-repair set's, for the reason the two bullets above
give.


## 6. What it does not do

* **It does not remove the per-layer protocol work.** Every admitted layer is still
  a `PyObject_Contains` and a `PyObject_GetItem` from Rust. Only the Python frame,
  the Python-level `for`, and the `PyObject_GetItem` that entered the method are
  gone.
* **It caches no resolved value.** §5.5's third-round finding — a cached resolution
  going stale when a body rebinds a layer — has no window to reopen here: nothing
  is remembered between reads. The only long-lived object is the pin, and the pin is
  a function identity, not a value.
* **It does not touch `RenderContext.__getitem__`** (`engine/context.py:209`), the
  innermost-layer one-liner. That is a different class on a different route, and
  the walk does not hand it here.
* **It is not `PyDict`-fast.** Condition 4 admits a `dict` subclass and then goes
  through the object protocol, which is the price of agreeing with `__missing__`
  and `__contains__` overrides. The raw `PyDict` calls would be cheaper and would
  answer `''` where Django answers `'MISSING-x'`.
* **A shrunken `dicts` is transcribed, not bailed on.** The walk re-reads the
  list's length per step and ends where `list_reverseiterator` ends, returning the
  same `KeyError` Django's `raise` would. The earlier build bailed here, and that
  bail was a **wrong render**, not a safety property: it re-walked a stack it had
  already partly consumed. §8 records the divergence.
* **A layer that is not a `dict` is decided before any probe.** Condition 4 is
  checked for every layer ahead of the walk, so a bail runs each layer's
  `__contains__` exactly the once Django runs it. Probing as it went ran the layers
  above the non-dict one twice, and `__contains__` is a user method whose calls are
  observable. The pre-pass is a subtype check per layer on the fast path, and §4
  prices the round's three repairs at **≈10 ns a root read**. It is the price of
  condition 4 — and condition 4 buys nothing else on this path: an admitted layer is
  walked through the **object protocol** either way (`PyObject_Contains` then
  `PyObject_GetItem`, which is what Django's `in` and `[]` compile to for any
  object), so a non-`dict` layer walked through the same protocol would answer
  identically and there would be nothing to double-probe. **Dropping condition 4 is
  an open simplification**, flagged rather than made: it is cheaper and at least as
  faithful, it removes the pre-pass and the `PyDict` bail together, and it is a
  change to a documented invariant of this design that has not been reviewed.
* **`compile` is a null and is reported as one.** The lever is on the read, and the
  read does not happen while compiling.

## 7. Lever 2, measured and not taken

§5.5's second lever routes the walk's write to `dicts[-1]` directly instead of
through `Context.__setitem__` (`engine/context.py:71`,
`self.dicts[-1][key] = value`; the `def` is at `:69`).
The fourth round added the part the original guard was missing, and it is the same
correction as §1.1's: `type(context) is Context` answers "is this the untampered
class", and Python can rebind `Context.__setitem__` *during* a render, so the guard
has to read the attribute and not only the class. Once it does, the lever is a
`type(context).__setitem__` attribute read plus an identity compare **every
iteration**.

Two probes priced it before anything was built, and both are kept:

* `.review/m19-measurements/lever2_ceiling.py` — a `timeit` stand-in whose
  `__setitem__` reproduces the transcribed body verbatim. The ceiling: routing the
  write at a cached `lst[-1]` instead of `context[key]` saves on the order of
  **30 ns/iteration**.
* `.review/m19-measurements/lever2_guard_cost.py` — the same stand-in with the guard
  written in, fallback arm and all. The guard costs **14.8 ns**, and `type(c).__setitem__`
  alone is most of it.

**Net ≈ 18 ns/iteration, against a per-iteration statement lever 1 priced at 277 ns
and a per-read lever this record prices, in §4, at 93 ns.** It is the smallest of
the three — fifteen times smaller than lever 1's statement, five times
smaller than lever 3's read — it is a *per-iteration* cost and so strictly dominated
by lever 1's 277 ns on the same statement, and it buys 19% of what lever 3 buys on
the same loop. Not taken, and the reason is the ratio rather than the guard's
soundness.

## 8. Where the claim is checked

`tests/test_loop_read_guard.py`, eighteen rows in four classes.

* `RootReadPinTests` (3) — the right-hand side of the guard is the class attribute:
  the pin *is* `BaseContext.__getitem__`; `Context` and `RequestContext` both still
  inherit it, so a plain `Context` and a `TEMPLATES`-driven one both take the
  transcription; and the binding is module-level, so it happened when the module
  body ran.
* `RootReadTranscriptionTests` (13) — a root read and a loop-variable read enter
  Python **zero** times, counted by a `sys.setprofile` hook filtered to
  `BaseContext.__getitem__.__code__`; and then the ways the transcription
  could be wrong, each with Django's own method as the reference for both halves:
  rebinding the class attribute (a delegating replacement must still answer, and a
  raising one must decide the answer), a subclass override, an upward search past a
  layer that does not hold the key, the innermost layer winning, a non-`dict` layer,
  the `__contains__`/`__missing__` disagreement, a key no layer holds being a
  miss rather than an error, a `dicts` that a layer's `__contains__` truncates
  mid-walk, a `list` subclass that overrides `__reversed__`, and a bail that must
  not run a layer's `__contains__` twice.
* `PinSoundnessProcessTests` (1) — the lazy-pin hazard, in a fresh interpreter.
* `PinOrderProcessTests` (1) — §1.1's import-order invariant, in a fresh
  interpreter: importing only `django_rust_templates.engine.context` leaves
  `django_rust_templates.engine.base` in `sys.modules` with the pin already bound.
  The record argued that ordering in prose; the row runs it.

**The instrument is a counting profile hook because there is nothing else.**
The transcription is a transcription: it is the same function, so no input
distinguishes "the Rust path answered" from "Django's method answered." What
distinguishes them is a Python frame, and a call to a Python function fires a
`call` event whether the caller is Python or C, while the Rust path performs the
same read without entering a frame at all. The two rows that use it are over the
same population — one helper, one template, one context — and differ in the class
attribute and nothing else; the **non-zero** one is the control for the zero one,
because a zero is what a broken hook prints, so without a row that makes the hook
fire on this exact code object, "the walk never enters Python" and "the hook never
fires" are the same observation, and a build whose fast path never ran would
satisfy every row in the file.

The pin's *insertion* is checked by `scripts/check_transcriptions.py`, which gained
one row for the `engine/base.py` region (§1.1) — keyed to `(1408, 1443)` after the
round below, because the region grew two comment paragraphs. The count pin in
`tests/test_collected_tests.py` moves by the eighteen rows: **532 → 536** with the
four the adversarial round added.

**What the adversarial round changed, and why the numbers here were re-taken.**
Round 4 on `9a8f5f8` returned FAIL with a BLOCKING and two SHOULD-FIX, and both
divergences were **reproduced against the pinned tree before anything was edited**,
with stock Django as the reference:

* *BLOCKING — a truncated `dicts` answered a key Django never sees.* The build
  bailed to Python when a layer's `__contains__` shortened the list. Measured
  fixture: a top layer whose `__contains__` sets a key on a layer the walk has
  already passed, then deletes the layers below it. Django answers `''`; the build
  answered `'HIT'`, because the bail re-walked a stack the walk had partly
  consumed. Repair: `break`, then the same `KeyError` Django raises.
* *SHOULD-FIX — a `list` subclass overriding `__reversed__`.* Condition 3 was
  `is_instance_of::<PyList>()`. Django's `reversed()` dispatches to
  `list.__reversed__`, so a subclass returning a different iterator makes Django
  answer `'FROM_REVERSED'` where positional indexing answers `'FROM_SECOND'`.
  Repair: `is_exact_instance_of::<PyList>()`.
* *SHOULD-FIX — a bail ran a layer's `__contains__` twice*, and the pin's stated
  reason was the wrong one. Repair: the `PyDict` check moved into a pre-pass before
  any probe, and §1.1's reason replaced with the import-order invariant, which
  `PinOrderProcessTests` now runs.

Each repair has its row, and each row's fixture was run against the **pre-repair**
build first, so a row that cannot fail is not what is being relied on. The three
divergences were then re-run **as probes** on the repaired artifact, with the sha
asserted in both places the artifact is read from — the transcript is
`.review/m19-measurements/root-read-fix/divergence_probe.out`, the probe itself sits
beside it, and on `caf1589798eac0cf` all three answer what stock Django answers:
BLOCKING 1 `''`/`''` (was `''`/`'HIT'`), the doubled-`__contains__` fixture `'1'` with
the log `['y']` on both sides — one call, not two — and SF1 `'FROM_REVERSED'`/`'FROM_REVERSED'`
(was `'FROM_REVERSED'`/`'FROM_SECOND'`). Because the
fast path changed, the unit in §4 and the tiles in §5 were **re-measured** on the
repaired artifact; the shas in those sections are the repaired ones.

**The page census is the same hook used as an instrument of a different kind.** §5's
read counts — 257 / 276 / 370 a render — come from
`.review/m19-measurements/root_read_count.py`: the same `sys.setprofile` filter over the
same code object, run once per build, with the counts written to
`.review/m19-measurements/root-read-count-{prefix,fixed}.json`. It counts *reads*, and
the caller attribution in §5 is a second short run of it
(`.review/m19-measurements/root_read_who.py`) that records `frame.f_back` for each hit.
Neither is a timing instrument and neither is used as one — a `setprofile` hook on every
Python call makes a render many times slower, which a count does not care about and a
microsecond would. The tile table's arithmetic is
`.review/m19-measurements/headline_tiles.py`, which implements the side rule above and
**reproduces lever 1's published `0.496` from lever 1's own four payloads** — the check
that the rule is lever 1's rather than one invented for this run.

**The gate.** `scripts/check.sh` was run in full on this tree and its transcript is
`.review/m19-measurements/gate-2026-10-10.lever3fix.txt` (`SCRIPT_EXIT=0`). It runs its
own `maturin develop --release` from the repo root, and the artifact under test was
asserted to be the artifact that build wrote — `caf1589798eac0cf` in both
`python/django_rust_templates/_native.cpython-314-darwin.so` and
`target/release/lib_native.dylib`, the repaired build, unchanged from the build the
figures above were measured on, because the build is deterministic. Acceptance on this
tree: **1547 tests, OK (skipped=6)**, the six declared skips by identity; **536** python
unit tests, OK; the differential harness identical; the anchor manifest, the
transcription diff and the generated-file checks all green. **The first run of this same
gate aborted** (`SCRIPT_EXIT=1`) on the anchor manifest, and the failure is this round's
own: the repair's Rust comments moved line numbers that `docs/superpowers/specs/` cites,
and `crates/template-py/src/lib.rs`'s `LookupTarget` block shifted **+36** while
everything from `resolve_lookups` on shifted **+41**. 15 failures, all of that shape;
re-keying the spec and the manifest and adding rows for the three new self-citations
took it to 0. A drifting anchor is what the manifest is for, and it caught this one.

## 9. What this record does not claim

* That a project **may** rebind `BaseContext.__getitem__` and that we handle it.
  The engine is a drop-in replacement and that is Django's class, so we do not get
  to decide; §8's rows state what happens when someone does.
* That a rebind *during* `import django_rust_templates.engine` is covered. The
  package's import order forbids it and nothing in §7 could construct it anyway.
* Any decomposition of the residual. §4 measures one root read's saving; it does
  not measure how much of it is the frame, how much the Python-level `for`, and how
  much the `PyObject_GetItem` that opened the method.
* That §5's read count is a count of *the walk's* reads. It counts calls to the
  pinned function, whoever makes them, and on `complex` twelve of them are the
  delegated route's; §5 attributes those by their caller rather than assuming.
* That the ±30% in §5 is tighter than it is, or that it is a property of the
  instrument rather than of the pages. It is `reads × 0.0890 µs` against a measured
  page delta — three pages, one pairing rule — and **both** terms are loose: the unit
  is a fitted slope difference whose repaired readings sit 11.7% apart (−89.0 and
  −99.4 ns) with six fit-free estimators spanning 8.7% between them, and the page
  deltas themselves move **18%–21%** between the two ABBA sets of this same pair. The
  ratios are 0.86 / 1.06 / 0.70, and `large` is short by a third at every end of the
  unit's range. This record does not know why: the unit was fitted on a loop body at
  **one** stack depth, and the pages' 257 / 276 / 370 reads have a distribution of stack
  depths that nothing here measures. A read pays a `PyObject_Contains` and a
  `PyObject_GetItem` per layer walked, so reads sitting in shallower stacks would save
  less each and land under the prediction, which is the shape `large` has. That is a
  hypothesis, labeled as one, and it is the reason the bound is quoted as a bound.
* That the per-read saving is one number. §4's two estimators of it disagree by a fifth
  to a third — `W − L` reads −0.0709 / −0.0659 µs per iteration against `V − L`'s
  −0.0886 / −0.0988, and the same disagreement was there pre-repair — so the saving is
  −66 to −99 ns a read depending on which row it is read from. §5's page prediction takes
  `V`, the family with nothing in it but the read, and the pages are the independent
  route that keeps that from being a choice made to suit the answer.
* That a per-page saving in §5 is resolvable to better than the null's own scatter. The
  same `prefix` artifact's render tile reads **0.457 / 0.464 / 0.468** across three ABBA
  sets — a 2.4% spread on one build — while two *different* treated builds both read
  0.421, and this pair's per-page deltas differ 18%–21% between its two sets. The level
  "0.42×" is a reading; the size of the lever is a reading with that band on it.
* Anything about lever 1's pages beyond the hypothesis §5 states and labels. This
  record counts reads, not iterations, and lever 1's arithmetic turns on iterations.

