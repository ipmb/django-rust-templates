# The loop's seven keys, built once instead of per iteration

§5.5 of `.review/m19-measurements/frame-rewrite-fix.md` closed the frame-rewrite
record by naming three cost levers and declining to take any of them, on the
argument that *"each would need its own measurement and its own review."* This is
the first of the three, taken.

## 1. What the lever is

`Walk::run_loop_body` wrote Django's per-iteration state with seven calls that
each took a `&str` key:

```rust
forloop.set_item("counter0", index)?;          // + counter, revcounter,
forloop.set_item("revcounter0", revcounter - 1)?;  //   revcounter0, first, last
self.context.set_item(&plan.loopvar, &item)?;
```

A `&str` argument is not a key: PyO3 builds a `PyString` from it, on every call.
Six counters plus the loop variable is seven string builds per iteration — 350
for a 50-item loop — for seven names that cannot change between the first
iteration and the last. Six of them are literals in the source; the seventh,
`plan.loopvar`, is `self.loopvars[0]` and is known before the template is ever
rendered.

The change hoists all seven. The six literals go through `intern!`, which answers
with the same `PyString` object on every call and is what CPython already does
with the literal keys of Django's own `loop_dict = {"counter0": …}` (an
identifier-like string constant is interned by the compiler). The seventh becomes
a field of the plan — `ForPlan.loopvar` is a `Py<PyString>`, built once in
`plan_for` at compile time, rather than a `String` converted at every write.
`PyString::new` and not `PyString::intern` for that one, because the name comes
from the template and the interpreter's intern table is permanent: interning it
would let any template grow that table without bound.

Nothing else moves. The keys are the same names in the same order, the values are
the same Python integers, the dict is the same dict, and the write order
(`engine/defaulttags.py:220-227` then `engine/defaulttags.py:248`) is untouched.

## 2. Why the existing instrument could not answer it

`frame_rewrite_ab.py` was the instrument for §5.1, §5.4 and §5.5, and reusing it
here would have been cheaper. It is wrong for this change, and the reason is its
control structure. Its family is `A{k}`, `B{k}`, `C` and `D`, and **every one of
the four holds a `{% for %}`** — `A{k}` a loop with `k` reads and an include,
`B{k}` the same without the include, `C` the include alone inside a loop, `D` the
empty loop. For a change inside `Walk::run_loop_body`, there is no control in that
family at all: every row is a subject. The report's own line calls `C`

> `no-loop control (this change cannot touch it)`

which is true of `C` for the frame rewrite and **false for this change** — `C` is
`{% for r in rows %}{% include 'inc.html' %}{% endfor %}`, a loop, and the six
counter writes happen in it 50 times a render. A control line that is false at the
point of output is worse than no line, so this slice carries its own instrument
rather than quoting that one's.

## 3. The instrument

`.review/m19-measurements/loop_keys_ab.py` — `build`, `ab`, `report`, the same
shape as `frame_rewrite_ab.py`: builds both sides from source, alternates them
round by round ABBA, copies one artifact into the package between runs and
**asserts the sha it finds there**, so a round that ran the wrong build is a
refusal and not a number.

Its rows are chosen so the answer is a **unit** and not a total:

| row | template | why |
|---|---|---|
| `L{n}` | `{% for r in rows{n} %}{% endfor %}`, n ∈ 1/10/50/200 | an empty body: the only per-iteration work is the seven writes and the iterator's `next`, so the slope over `n` **is** the per-iteration cost |
| `W{n}` | the same with one `{{ r.name }}` a row | the same loop beside real work, so a saving that only exists in an empty body would show up as a discrepancy between the two slopes |
| `P` | `{{ a }}` fifty times, no `{% for %}` anywhere | the control — a lever inside the loop body cannot move it, so if it moves, the instrument moved |

`D` in the other family gives one loop at one length: a total. Four lengths give
`cost(n) = a + b·n`, and `b` is the number the lever is a claim about. The
intercept is reported **beside** it and not subtracted from it: `row − floor` is
neither term, because the floor is an intercept measured at one point and the next
template's intercept is not the same number — a loop's setup and an empty
template's are different quantities, and a difference of two of them is a number
about neither.

## 4. The result

**The bound first.** This run's per-row scatter, `(max − min) / min` over the twelve
rounds of one side, is **1.42%–7.82%**, and it is the resolution every number below
is read at. A paired delta smaller than the scatter of its own row is not a
measurement of the code. The paired interval is printed beside each delta for that
reason, and the control's interval is the one that matters most.

The unit, as four independent fits of `cost(n) = a + b·n` over `n ∈ 1/10/50/200`:

| family | mode | per-iteration prefix | fixed | delta |
|---|---|---|---|---|
| `L` empty body | render | +0.2770 µs | +0.0993 µs | **−0.1777** |
| `W` one read a row | render | +0.7698 | +0.5856 | **−0.1842** |
| `L` empty body | parse | +0.2779 | +0.0998 | **−0.1781** |
| `W` one read a row | parse | +0.7785 | +0.5854 | **−0.1931** |

Four estimates of one quantity, spread 0.015 µs: **≈0.18 µs off every iteration**,
which is 0.1777/7 = **25 ns a key** — arithmetic on the model of §1, and it says the
saving is the size of the thing removed and nothing beside it. On a fifty-iteration
loop that is −9.045 µs (`L50`, −51%) and −9.517 µs (`W50`, −22%) in render.

`W`'s slope is the same lever read beside real work, and the two slopes agree in
both modes, so this is not an empty-body artefact. The residual per-row cost —
everything in `W` that is not the seven keys, i.e. the read and the counter *values*
— is 0.4928 µs before and 0.4863 after in render, 0.5006 and 0.4856 in parse: 1.3%
and 3.0% apart, inside the scatter of the rows that make it (1.6%–7.4%). The lever
took the seven key builds and left the rest of the row where it was.

**The control.** `P` is `{{ a }}` fifty times, no `{% for %}` anywhere. Paired
**+0.220 µs (0.64% of its row)** in render, **+0.329 (0.17%)** in parse, **−0.034
(0.02%)** in compile — none over the 2% MOVE threshold, and each interval (render
−1.309…+1.284, parse −1.265…+3.063, compile −1.189…+1.916) straddles zero. That is
the one row a lever inside the loop body cannot touch, and it did not move.

**The expected negative.** Every `L` row compiles in 13.5 µs whatever `n` is and
every `W` row in 17.4, so the loop does not run at compile time: the fitted slopes
are ±0.0002 and ±0.0006 µs/iteration on both sides and the deltas are −0.0000 and
+0.0000. The one build the change *does* add, per compiled loop rather than per
iteration, is `ForPlan::loopvar` — a single `PyString` in `plan_for`, ~25 ns on
13.5 µs, 0.2%, under half the smallest scatter on those rows (1.86%). It is there
and this instrument cannot see it: the compile rows say **not larger than the
noise**, not "free".

**The `n = 1` rows, and the intercept.** Each fit's intercept is reported beside its
slope, and both fell as well — 3.884 → 3.768 µs and 4.218 → 4.090 in render — by
about 0.12 µs, two-thirds of one iteration's saving. That is the small-`n` end of the
fit and not a second result: `L1` and `W1` are the widest intervals on the smallest
rows (−0.418…−0.199 on 4.1 µs, −0.417…−0.136 on 4.6 µs), and their point estimates,
−0.272 and −0.251, sit above the slope. A four-point least squares dominated by
`n = 200` puts its largest residuals at `n = 1`. The slope is the estimate; these two
rows are not a second one.

## 5. What it does not do

- **The read is untouched.** `W`'s saving is `L`'s beside a read — 0.18 µs either
  way — which is what says the lever reached the seven writes and only them. §5.5's
  levers 2 and 3, the target dict and `BaseContext.__getitem__`'s upward search, are
  where a read spends its ~0.49 µs; neither is taken here.
- **No claim about whole pages.** This instrument's rows are loops of one shape at
  four lengths. What a real page does is a separate measurement over the same two
  artifacts, and it is in §6 rather than inferred from a slope here.
- **Not a nested loop.** The lever applies at every `{% for %}` the walk owns, and
  the seven keys are the same seven at every depth, but a loop inside a loop is not
  among these rows and is not measured.
- **`intern!` is not free, and it is bounded.** The six literals enter the
  interpreter's intern table and stay for the process's life — six fixed names,
  decided when this crate was compiled, so the table cannot grow with a template.
  That is exactly why the seventh key is `PyString::new` and not `PyString::intern`:
  the loop variable's name comes from *the template*, and interning a
  template-controlled name would let any template grow that table without bound.
- **The dict and the values are unchanged.** A fresh `PyDict` per loop execution and
  a fresh Python int or bool per counter per iteration are still built; only the keys
  moved.

## 6. The headline benchmark

The unit above is a micro row. §5.5's levers exist for pages, so the same two
artifacts were put through `scripts/bench.py` — four full runs, **ABBA** over the two
builds, the artifact copied into the package and its sha asserted after every swap:

```
.venv/bin/python -I scripts/bench.py --json headline-a1.json   # dc174855d926defc  19:29 UTC
.venv/bin/python -I scripts/bench.py --json headline-b1.json   # 3d3e99e63eddc036  19:32 UTC
.venv/bin/python -I scripts/bench.py --json headline-b2.json   # 3d3e99e63eddc036  19:34 UTC
.venv/bin/python -I scripts/bench.py --json headline-a2.json   # dc174855d926defc  19:37 UTC
```

The four stamps are the payloads' own (`meta.date`), not the shell's clock.

Payloads: `.review/m19-measurements/headline-lever1-{a1,a2,b1,b2}.json`;
`b1`'s run is the one that wrote `docs/bench.html`. **Every figure below is derived
from those four files under one rule, stated here rather than implied at each use:** a
side's figure is the mean of that side's two runs (`before` = `a1`,`a2`; `after` =
`b1`,`b2`), a ratio is that side's mean `rust` time over that side's mean `stock` time,
and a paired delta is per pairing — `a1`→`b1` and `a2`→`b2`. The two `--compare`
transcripts quoted below are `.review/m19-measurements/headline-lever1-compare-p{1,2}.txt`.

**The band, measured rather than assumed.** `--band` on each build's own two runs:
the before build's rows scatter with median |rel| **2.01%** and max **8.67%**, the
after build's **1.69%** and **7.26%**. The conservative band is the before build's,
and every delta below is read against it. One control the tool does not print: the
*stock* engine's own best time on the three whole-page render rows moves
**2.07%–2.14%** across these four runs — so a whole-page delta under about 2% is the
harness, not the code.

**The tiles** — geometric mean over the three real-world pages, `ours / stock`,
lower is better (the same figure `docs/feature-checklist.html` publishes):

| | before (`a1` / `a2`) | after (`b1` / `b2`) |
|---|---|---|
| render | 0.494 / 0.498 | **0.466 / 0.462** |
| parse | 0.830 / 0.824 | **0.797 / 0.787** |
| compile | 1.398 / 1.354 | 1.384 / 1.356 |

So the published render tile goes **0.50× → 0.46×** and parse **0.82× → 0.79×**, and
compile is unmoved — which is what §4's compile mode said it would be.

**The rows that must move did.** The loop micro rows, absolute microseconds, and as
a ratio to stock:

| row | mode | before | after | ratio |
|---|---|---|---|---|
| `micro/loop-text` | render | 19.14 µs | **9.94** | 1.292 → **0.661** |
| `micro/loop-text` | parse | 36.24 | **26.88** | 1.373 → **1.014** |
| `micro/loop-var` | render | 45.66 | **36.01** | 0.575 → **0.448** |
| `micro/loop-var` | parse | 69.77 | **60.54** | 0.730 → **0.621** |

`micro/loop-text` render is the one to look at: the port took **29% more time than stock
Django** on a loop of text nodes and now takes **34% less**. In `parse` the same row
lands at 1.014×, i.e. level — the port's compile cost is what is left of it.

**Which of those are findings.** Under the wider band, each pairing puts **26 of the 54**
`(row, mode)` cells outside their own band, and only **15 are outside in both** (37 in at
least one). The four loop rows above are outside in both and are the largest movers
there, **−14% to −50%**, against a next-largest **−9%** (`real-world/large` render,
−9.2%/−7.6%, also outside in both). Cells outside in one pairing only — including
`micro/filter compile +5.7%`, `django-only/realistic compile +5.1%` and
`micro/if compile +3.0%`, all of which are *positive* on rows with no loop — are
drift and are not reported as findings. At a 2% median band on 54 cells that is the
rate to expect by chance.

**The whole-page renders.** All three moved down in both pairings — `normal`
−3.5%/−5.3%, `complex` −2.6%/−7.0%, `large` −8.4%/−7.2% — and `normal` and `large`
are outside their band in both. Absolute: 195.6 → 186.9 µs, 530.3 → 504.7, 282.7 →
260.5.

**The arithmetic that does not close, recorded and not explained.** The pages are
loops plus other work, so `iterations × the unit from §4` is a prediction this slice
can be checked against. `normal` is one loop over 50 rows: 50 × 0.18 = **9.0 µs**
against **−8.7 µs** measured — it closes. `complex` is 12 sections × 6 lines, so 84
iterations counting both levels: 84 × 0.18 = **15.1 µs** against **−25.6** — 1.7×.
`large` is 20 loops × 3 items = 60 iterations: 60 × 0.18 = **10.8 µs** against
**−22.2** — 2.1×. Two of the three pages save roughly twice what the per-iteration
unit accounts for. The unit was measured on a loop whose context holds one list and
whose body is empty or a single read; these pages are neither, and this record does
not claim to know which of the two readings — the unit being context-dependent, or
something else moving alongside — is the one. The number to trust on a page is the
page's own measured delta and the tile, not this arithmetic.

**The goal.** The port is still slower than Jinja2 on **every** whole-page render. Its
time as a multiple of Jinja2's, per after-run, is **1.09× / 1.07×** (`normal`),
**1.46× / 1.42×** (`complex`) and **1.32× / 1.26×** (`large`) — from **1.11× / 1.12×**,
**1.52× / 1.56×** and **1.44× / 1.35×** before the lever, the same four runs. Every after
figure lies below both of its page's before figures, so the lever narrows the gap on all
three and closes it on none.


