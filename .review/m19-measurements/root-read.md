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
iterator would have stopped at, so a list that has shrunk bails to Python instead.

### 1.1 The pin, and why its spelling is the soundness argument

`root_item` licences itself against `BASE_CONTEXT_GETITEM`
(`crates/template-py/src/lib.rs:2458`), which is not captured from the class on
first use but **read as a name from `django_rust_templates.engine.base`**, where
`engine/base.py:1433` binds it:

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
name requires importing that module, and every route to `BaseContext` runs through
this module's import of it (`engine/base.py:61 from .context import BaseContext`),
so the binding happens *inside* the statement that hands the class to whoever means
to rebind it. And it is below every `COMPARED_PATH["base.py"]` span in
`scripts/check_transcriptions.py`, which are literal line ranges over this file —
the last of them is `token_kwargs`'s, ending on the file's former final line. A
count that moves *below* every span re-measures none of them. The insertion is
declared there as `(1408, 1433)` so the parity harness still knows what the file is
allowed to differ by.

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
root read differently.

## 4. The result

**The bound first.** This run's scatter, `(max − min) / min` over the twelve
rounds of one side, is **1.67%–9.61%** per cell — thirteen label-rows × three modes
× two sides, seventy-eight cells — and **2.60%–9.61%** if each label-row is charged
its wider side. A paired delta smaller than the scatter of its own row is not a
measurement of the code. The widest two cells are `W10` parse at 9.61% and `W1`
compile at 8.12%, both subject rows; the `L` family's worst is `L1` render at
5.89%, so the widest scatter in this run is not in the row the lever cannot reach.
That is the row-to-row spread the paired estimator exists to survive: `cmd_ab`
runs a round's two sides back to back and reverses the order on even rounds, so
`f[i] − p[i]` carries whatever drifted between rounds in both terms and cancels it.

The unit, as four independent fits of `cost(n) = a + b·n` over `n ∈ 1/10/50/200`,
each in µs/iteration:

| family | mode | prefix | fixed | delta |
|---|---|---|---|---|
| `L` empty body | render | +0.1011 | +0.1008 | **−0.0003** |
| `V` one root read a row | render | +0.7884 | +0.6854 | **−0.1030** |
| `W` one read and a `getattr` | render | +0.5811 | +0.5079 | **−0.0732** |
| `L` empty body | parse | +0.1015 | +0.1010 | **−0.0005** |
| `V` one root read a row | parse | +0.7918 | +0.6871 | **−0.1046** |
| `W` one read and a `getattr` | parse | +0.5867 | +0.5112 | **−0.0755** |
| `L` empty body | compile | +0.0004 | +0.0004 | −0.0000 |
| `V` one root read a row | compile | −0.0006 | +0.0001 | +0.0007 |
| `W` one read and a `getattr` | compile | +0.0003 | +0.0003 | +0.0000 |

Read as `V − L`, the cost of exactly one root read:

| mode | prefix | fixed | delta |
|---|---|---|---|
| render | +0.6873 | +0.5845 | **−0.1027 µs/read** |
| parse | +0.6903 | +0.5862 | **−0.1041 µs/read** |

Two readings of one quantity, 1.4% apart: **≈ −103 ns a read**, which is **15% of
the 0.687 µs a bare `{{ a }}` cost per iteration** before the change.

**Six more estimates of the same number, none of them using the fit.** The paired
per-row difference `(V − L)/n`, which is the instrument's own arithmetic with no
least squares in it, runs **−104.5, −106.9, −100.7** ns/read in render and **−105.6,
−97.9, −100.8** in parse for `n = 10/50/200`. That is six readings spanning −97.9 to
−106.9 — 9% of their centre −103 — and they straddle the fitted slopes −102.7 and
−104.1 rather than sitting on one side of them, so the fit is neither the most
generous reading nor an artefact of the four-point shape; what all eight readings
agree on is the sign and the order of magnitude. `n = 1` is deliberately not among
them, and for the reason this file takes a difference of slopes rather than a row
minus a floor: `L` and `V` are not the same template, so a one-iteration row is
their intercept difference plus one slope difference, and the two intercepts are
0.47 µs apart in render.

`P` is the same quantity on the other route — fifty `{{ a }}` and no `{% for %}`
anywhere. Paired, it moved **−4.586 µs over fifty reads = −91.7 ns/read** in render
and **−6.760 = −135.2** in parse, so on this run `P` brackets the six readings above
rather than tightening them, and it is reported as the same order of magnitude and
not as a seventh estimate. What `P` does settle is the *prefix* side, which is where
the lever's target has to be right: its render row divided by fifty is **0.6945
µs/read** against `V − L`'s 0.6873 — the two routes agree on what a read costs to
1.0% **before** the change, and that agreement is what makes a read the thing the
transcription transcribes.

**The control's slope is flat, and that is the whole of what it has to say.**
`L`'s slopes move by −0.0003 render and −0.0005 parse — 0.3% and 0.5% of their own
slope, against cell scatter of 2%–5% on those four rows — so nothing per-iteration
happened to a body that reads nothing. `L`'s *intercept* does fall, −0.099 µs render and −0.140 parse,
and that is the lever rather than a leak: `{% for r in rows %}` resolves `rows` at
the **root**, once a render, so every family's intercept carries exactly one root
read, and a level shift of one read per render belongs there. The other four
families' intercepts are the wrong place to look for corroboration — they move
−0.005 to −0.400 µs, a spread four times the 0.10 µs being read, on rows whose own
one-iteration scatter is 1–2 µs — and it is precisely why the reading this file
exists for is a difference of **slopes**: only the slope is the per-iteration cost.
The intercepts are printed beside every slope because a slope read without its
baseline is the mistake §5.5's own first lever already paid for once, not because
they carry the finding.

**The null, and it is a null.** Compile's slopes are `L` `+0.0004 → +0.0004`, `V`
`−0.0006 → +0.0001` and `W` `+0.0003 → +0.0003` µs/iteration — the largest move in
the mode is `V`'s `+0.0007` — and `V − L` moves **+0.0007 µs/read**. `P`'s paired
compile row moves −0.865 µs, 0.52% of it. A read's cost is not paid while
compiling, and this instrument says so in the mode where it cannot be.

**What the number says about §5.5's comparator.** §5.5 priced *"a read that leaves
Rust"* at **+0.17 to +0.25 µs**, and this lever recovers **41% to 60%** of that. The
reason is the one §6 leads with: the transcription still pays a
`PyObject_Contains` and a `PyObject_GetItem` for every layer it walks. What it
removes is the Python frame, the Python-level `for`, and the
`PyObject_GetItem` that opened the method — roughly half the round trip, with the
per-layer protocol left where it was. That is a finding rather than a
discrepancy, and §9 declines to decompose the rest of the 0.585 µs a root read
still costs after the change.

## 5. The headline benchmark

The unit above is a micro row. §5.5's levers exist for pages, so the same two
artifacts were put through `scripts/bench.py` — four full runs, **ABBA** over the two
builds, the artifact copied into the package *and* into cargo's output directory, with
its sha asserted after every swap in both places:

```
.venv/bin/python -I scripts/bench.py --json headline-lever3-a1.json              # 3d3e99e63eddc036  21:17 UTC
.venv/bin/python -I scripts/bench.py --json headline-lever3-b1.json --html docs/bench.html   # f702a231cf4fa410  21:20 UTC
.venv/bin/python -I scripts/bench.py --json headline-lever3-b2.json              # f702a231cf4fa410  21:23 UTC
.venv/bin/python -I scripts/bench.py --json headline-lever3-a2.json              # 3d3e99e63eddc036  21:26 UTC
```

The four stamps are the payloads' own (`meta.date`), not the shell's clock. The second
placement is not decoration: `bench.py`'s `native_artifact()` hashes
`target/release/lib_native.dylib` to name the *profile* a row was built with, so a swap
that touches only the package leaves the "before" rows printing `unknown — no built
dylib has this hash`, and a dylib left on the old build is what the next
`maturin develop` copies back into the package, reverting the tree with every gate
still green.

Payloads: `.review/m19-measurements/headline-lever3-{a1,a2,b1,b2}.json`; `b1`'s run is the one
that wrote `docs/bench.html`. Every figure below is derived from those four files under
the rule lever 1's record states, restated because it is not implied at each use: a
side's figure is the mean of that side's two runs (`before` = `a1`,`a2`; `after` =
`b1`,`b2`), a ratio is that side's mean `rust` time over that side's mean `stock` time,
and a paired delta is per pairing — `a1`→`b1` and `a2`→`b2`. The two `--compare`
transcripts are `.review/m19-measurements/headline-lever3-compare-p{1,2}.txt`.

**The band, measured rather than assumed.** `--band` on the before build's own two runs:
those rows scatter with median |rel| **1.48%** and max **5.34%** over the 54
`(row, mode)` cells, and every delta below is read against it. It is a *tighter* band
than lever 1's run carried (2.01% / 8.67%), which is why more rows clear it here. One
control the tool does not print: the **stock** and **Jinja2** engines' own best times on
the three whole-page render rows move **0.47%–2.09%** across these four runs, so a
whole-page delta under about 2% is the harness and not the code. Ours moves 9.01%–16.98%
on the same rows, in the direction of the change.

**The tiles** — geometric mean over the three real-world pages, `ours / stock`, lower is
better (the same figure `docs/feature-checklist.html` publishes):

| | before (`a1` / `a2`) | after (`b1` / `b2`) |
|---|---|---|
| render | 0.463 / 0.473 | **0.416 / 0.426** |
| parse | 0.783 / 0.791 | **0.748 / 0.764** |
| compile | 1.344 / 1.344 | 1.364 / 1.348 |

Under lever 1's side rule (mean of the two runs, then ratio, then geometric mean) the
same readings are render **0.468 → 0.421**, parse **0.787 → 0.756**, compile
**1.344 → 1.356**. So the published render tile goes **0.47× → 0.42×** and parse
**0.79× → 0.76×**, and compile moves **1.34× → 1.36×** — +0.9%, which is *inside* the
±3.7% the stock denominator alone moves on these rows and is what §4's compile null
said to expect. The `1.36×` the regenerated `docs/bench.html` prints is its own run's
second decimal of the same quantity.

**The whole-page renders.** Absolute microseconds, render mode, and the paired delta:

| page | before (`a1` / `a2`) | after (`b1` / `b2`) | paired |
|---|---|---|---|
| `normal` | 183.9 / 187.4 | **160.2 / 162.4** | −12.91% / −13.33% |
| `complex` | 493.0 / 507.3 | **465.4 / 474.6** | −5.61% / −6.44% |
| `large` | 259.1 / 262.4 | **228.1 / 235.2** | −11.94% / −10.37% |

All three are outside their band in both pairings, in `render` and again in `parse`
(−5.71%/−6.49%, −4.89%/−3.26%, −2.02%/−0.46%). Across the 54 cells, pairing 1 puts **36
outside** their own band and pairing 2 **31**; **28** are outside in **both**, of which
**24 moved down** and — see below — **1 moved up and 3 disagree in sign**. The three
sign-disagreeing rows (`django-only/filter-arg` compile, `micro/filter` compile,
`micro/text` render) are each inside the band on the pairing that contradicts them, so
they are drift; lever 1's record read the same three rows the same way.

**The pages' saving closes against the unit, and that is new.** The per-read unit is
0.1027 µs (§4). The pages are not loops of one read, so the count that has to be
multiplied by it was measured rather than estimated: a `sys.setprofile` hook filtered on
the pinned function's own `__code__` — `BaseContext.__getitem__`, which `Context` and
`RequestContext` do not override, so one code object catches every subclass — counts
every root read one render makes, under each build:

| page | root reads, prefix | still leave Rust, fixed | covered | × 0.1027 | measured (render) | measured / predicted |
|---|---|---|---|---|---|---|
| `normal` | 257 | 0 | 257 | 26.4 µs | −24.4 µs | 0.92 |
| `complex` | 276 | 12 | 264 | 27.1 µs | −30.2 µs | 1.11 |
| `large` | 370 | 0 | 370 | 38.0 µs | −29.1 µs | 0.77 |

Three pages, three ratios 0.92 / 1.11 / 0.77: a per-read unit measured on a
one-construct loop predicts the saving of a whole page to **±25%**, against a page
scatter the four runs put at 9%–17%. Lever 1's record could not close this arithmetic —
its pages saved roughly *twice* what its per-iteration unit accounted for and it said so
without explaining it — and the count above is the explanation, offered as a
**hypothesis about that lever and not as a measurement of it**: lever 1's arithmetic
multiplied a per-iteration quantity by an *estimated* iteration count (50, 84 and 60 for
these three pages), and these pages execute 257–370 root reads, so the estimate was the
loose term. This file does not count iterations and does not claim it.

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
| `normal` | 1.075 / 1.085 | **0.932 / 0.948** |
| `complex` | 1.436 / 1.482 | 1.363 / 1.381 |
| `large` | 1.285 / 1.299 | 1.143 / 1.167 |

`normal` crosses 1.00 — **the port is faster than Jinja2 on a whole page for the first
time** — and it does so in *both* after-runs and in *neither* before-run, which is what
makes it a reading and not a row that happened to land low. Over the three pages the
geometric mean moves **1.257 / 1.278 → 1.132 / 1.152**. Every after figure lies below
both of its page's before figures: the lever narrows the gap on all three and closes it
on one.

That flip is also what broke a tile, and the tile is part of this commit.
`bench_report._fastest_tile` picks whichever engine won the most whole-page renders and
then wrote "`{leader}` wins **every one** of these {n} pages" — a sentence true only
while the leader also wins all of them. It was true of every run until this one; now the
regenerated page printed `jinja2 2/3` beside a note claiming jinja2 won all three. The
clause is read off the per-page winners now, so it reads "jinja2 wins 2 of these 3 pages,
at 0.29x-0.42x stock Django's own time, and real-world/normal to rust", and the
`3/3` branch is kept for the runs that still sweep. `docs/bench.html` is regenerated by
re-rendering the **same `b1` payload** — `bench_report.render(bench.load_payload(...))`,
which is a pure function of the payload — so the page's provenance is still the ABBA's
recorded after-run and the caption fix cost no fifth run and no second measurement of
anything.

**The one row that moved against us, and what it is.** `real-world/normal` compile is
the only whole-page cell outside its band in both pairings *and* positive: **+4.11% /
+0.82%**. The two readings disagree by 5×, and the larger one is a stock-side excursion
rather than a port-side cost: the *ratio* is `rust / stock`, and between `a1` and `b1`
the numerator moves 165.88 → 166.29 µs (**+0.25%**) while the denominator moves
125.54 → 120.88 (**−3.7%**). The second pairing puts the same row at +0.82% on a band of
0.48%. So compile of the whole pages is reported as a **null with one noisy cell in it**
and not as a finding, which is what §4's compile rows said independently. The full
compile moves are −2.06% … +4.11% with inconsistent signs across the three pages and the
two pairings.

The arithmetic that does not close here is not the page saving — that closed above — but
the *rest* of a read's cost: a read still costs 0.585 µs after the change, 85% of what it
cost before, and this file does not decompose it (§9).


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
* **A shrunken `dicts` bails to Python**, so the fast path is skipped for exactly
  the case where a layer's `__contains__` mutates the stack mid-walk.
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
and a per-read lever this record prices, in §4, at 103 ns.** It is the smallest of
the three — fifteen times smaller than lever 1's statement, five and a half times
smaller than lever 3's read — it is a *per-iteration* cost and so strictly dominated
by lever 1's 277 ns on the same statement, and it buys 17% of what lever 3 buys on
the same loop. Not taken, and the reason is the ratio rather than the guard's
soundness.

## 8. Where the claim is checked

`tests/test_loop_read_guard.py`, fourteen rows in three classes.

* `RootReadPinTests` (3) — the right-hand side of the guard is the class attribute:
  the pin *is* `BaseContext.__getitem__`; `Context` and `RequestContext` both still
  inherit it, so a plain `Context` and a `TEMPLATES`-driven one both take the
  transcription; and the binding is module-level, so it happened when the module
  body ran.
* `RootReadTranscriptionTests` (10) — a root read and a loop-variable read enter
  Python **zero** times, counted by a `sys.setprofile` hook filtered to
  `BaseContext.__getitem__.__code__`; and then the seven ways the transcription
  could be wrong, each with Django's own method as the reference for both halves:
  rebinding the class attribute (a delegating replacement must still answer, and a
  raising one must decide the answer), a subclass override, an upward search past a
  layer that does not hold the key, the innermost layer winning, a non-`dict` layer,
  the `__contains__`/`__missing__` disagreement, and a key no layer holds being a
  miss rather than an error.
* `PinSoundnessProcessTests` (1) — the lazy-pin hazard, in a fresh interpreter.

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
one row for the `engine/base.py` region (§1.1), and the count pin in
`tests/test_collected_tests.py` moves by the fourteen rows.

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
`.review/m19-measurements/gate-2026-10-10.lever3.txt` (`SCRIPT_EXIT=0`). It runs its own
`maturin develop --release` from the repo root, and the artifact under test was asserted to
be the artifact that build wrote — `f702a231cf4fa410` in both
`python/django_rust_templates/_native.cpython-314-darwin.so` and
`target/release/lib_native.dylib`, unchanged from the build the figures above were measured
on, because the build is deterministic. Acceptance on this tree: **1547 tests, OK
(skipped=6)**, the six declared skips by identity; **532** python unit tests, OK; the
differential harness identical; the anchor manifest, the transcription diff and the
generated-file checks all green.

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
* That the ±25% in §5 is tighter than it is. It is `reads × 0.1027 µs` against a
  measured page delta — three pages, one pairing rule — and the unit is itself a
  fitted slope difference whose two readings sit 1.4% apart with six fit-free
  estimators spanning 9% around them.
* Anything about lever 1's pages beyond the hypothesis §5 states and labels. This
  record counts reads, not iterations, and lever 1's arithmetic turns on iterations.

