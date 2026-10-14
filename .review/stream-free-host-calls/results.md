# §8 pre-ship gate — results

## Method

**The baseline is `main`'s actual binary**, built from a separate git worktree
with its own venv. Both arms run the *same* `workload.py` under their own
interpreter, and `compare.py` refuses to run if the two copies differ. It
prints both commits and interpreter versions, so a result cannot be read
against the wrong pair of builds.

An earlier harness compared this branch against itself with the Python
classifier stubbed to `False`. Review round 7 rejected it, correctly: both arms
still ran the new Rust dispatch — the negative-cache lookup and the first
classifier crossing — so it could neither price them nor say anything about
`main`. It was deleted rather than kept, because a misleading instrument left
lying around gets re-run.

* order is **counterbalanced** (alternating A/B and B/A across repetitions);
* **observed range** is `min..max` of the paired relative differences. It is
  **descriptive, not inferential**: it says what was seen across 8
  repetitions, not what bounds the effect. `faster`/`small cost` therefore mean
  every observed repetition agreed on the sign — nothing about repetitions not
  performed;
* the tolerance (2%) was **predeclared** in `compare.py` before the run;
* `unresolved` means the observed repetitions disagreed on sign. It is a
  result, and it is weaker than "no regression".

Reproduce:

```bash
git worktree add /tmp/drt-main main
cd /tmp/drt-main && python3.14 -m venv .venv
PIP_CONFIG_FILE=/dev/null .venv/bin/pip install maturin "django>=6.0.9,<6.1"
.venv/bin/maturin develop --release
ln -s <branch>/.django .django; ln -s <branch>/django_suite django_suite
ln -s <branch>/scripts/bench_templates scripts/bench_templates
mkdir -p .review/stream-free-host-calls
cp <branch>/.review/stream-free-host-calls/workload.py .review/stream-free-host-calls/

cd <branch> && source scripts/env.sh
python .review/stream-free-host-calls/compare.py 8
```

## Which corpus templates can be affected at all

Seven of the nine contain **no host-compiled tag**, so they never reach the
classifier arm. Measured through `_native.compile_template`, `translated_base`
and `translated_report` each make exactly **one** host call — a stream-free
`load` — and the other seven make none.

That rules out the direct costs and savings this change is about: no classifier
crossing, no suffix transfer. It does **not** prove nothing else changed. The
patch initialises `stream_free_declined` on every Rust `Parser` construction
(`parser/mod.rs:128`), including for these templates, and it alters the
parser's layout and dispatch code. So a difference on those seven rows cannot
be *attributed to the stream-free mechanism* from this experiment; that is
weaker than calling it noise, and they are kept as regression controls rather
than treated as rows exempt from being affected.

| template | host-compiled tags |
|---|---|
| `base`, `footer`, `invoice`, `large`, `layout`, `line`, `report` | none |
| `translated_base` | `load` |
| `translated_report` | `load` |

## Results

A negative number is faster. Machine: Apple Silicon, macOS, release build.

```
   branch: /Users/pete/projects/lincolnloop/django-rust-templates  00515fb  Python 3.14.4
 baseline: /tmp/drt-main  1970aca  Python 3.14.4
workload.py: identical in both arms

========================================================================================
branch vs main   engine.debug=False   8 counterbalanced reps   tolerance 2.0%
========================================================================================
workload                     main ms  branch ms    change     observed range     verdict
median-8                      0.1436     0.1162    -19.4%   -25.8..-17.0        faster
p90-22                        0.4753     0.3145    -34.2%   -36.2..-31.4        faster
max-59                        1.6816     0.8372    -50.3%   -50.5..-49.3        faster
mixed                         0.8610     0.6140    -28.4%   -29.8..-25.3        faster
all-native                    0.4880     0.4854     -0.6%    -2.8..+1.3     unresolved
all-native-stress             2.4079     2.4925     +3.6%    -2.5..+4.9     unresolved
tiny-one-host                 0.0105     0.0106     +0.8%    -3.1..+1.6     unresolved
all-streamful                 0.6382     0.6412     +0.1%    -0.2..+0.7     unresolved
all-streamful-stress         11.8807    11.8790     +0.2%    -0.4..+1.0     unresolved
corpus/base                   0.0160     0.0159     -0.0%    -4.4..+28.3    unresolved
corpus/footer                 0.0244     0.0248     +1.4%    -1.1..+2.3     unresolved
corpus/invoice                0.0876     0.0881     +1.0%    -4.4..+3.0     unresolved
corpus/large                  1.5744     1.5721     +0.2%    -1.5..+6.6     unresolved
corpus/layout                 0.0300     0.0303     +1.0%    -9.2..+4.7     unresolved
corpus/line                   0.0297     0.0302     +1.6%    +0.3..+11.9    small cost
corpus/report                 0.1249     0.1273     +1.7%    -0.0..+10.3    unresolved
corpus/translated_base        0.0378     0.0334    -11.3%   -12.9..-9.1         faster
corpus/translated_report      0.0914     0.0830     -9.5%   -10.5..-1.8         faster

========================================================================================
branch vs main   engine.debug=True   8 counterbalanced reps   tolerance 2.0%
========================================================================================
workload                     main ms  branch ms    change     observed range     verdict
median-8                      0.1466     0.1175    -19.3%   -21.1..-17.2        faster
p90-22                        0.5007     0.3192    -36.3%   -38.4..-32.9        faster
max-59                        1.8214     0.8694    -52.1%   -52.7..-51.7        faster
mixed                         0.9247     0.6443    -30.2%   -31.1..-29.3        faster
all-native                    0.4932     0.4932     +0.0%    -4.0..+2.7     unresolved
all-native-stress             2.6077     2.6225     +0.5%    -0.3..+2.4     unresolved
tiny-one-host                 0.0105     0.0106     +0.4%    -1.6..+4.7     unresolved
all-streamful                 0.6887     0.6941     +0.7%    -1.4..+4.4     unresolved
all-streamful-stress         13.3786    13.4286     +0.4%    -1.1..+2.1     unresolved
corpus/base                   0.0161     0.0161     +0.5%    -1.7..+0.8     unresolved
corpus/footer                 0.0247     0.0249     +0.7%    -0.9..+3.7     unresolved
corpus/invoice                0.0890     0.0890     +0.3%    -4.9..+7.6     unresolved
corpus/large                  1.6917     1.7007     +1.1%    -2.0..+2.5     unresolved
corpus/layout                 0.0303     0.0301     -0.6%    -2.0..+2.5     unresolved
corpus/line                   0.0299     0.0303     +1.1%    -0.8..+2.7     unresolved
corpus/report                 0.1271     0.1297     +2.6%   -12.1..+5.9     unresolved
corpus/translated_base        0.0383     0.0338    -12.0%   -12.7..+0.6     unresolved
corpus/translated_report      0.0933     0.0846     -9.8%   -11.8..-4.1         faster

```

## Reading

**Faster, every observed repetition agreeing** — the four synthetic host-heavy
shapes, and the two corpus templates that actually exercise the feature:
`translated_report` at −9.5% / −9.8% and `translated_base` at −11.3%
(non-debug). `translated_base` in debug has a median of −12.0% but one
repetition landed at +0.6%, so it is recorded as unresolved rather than
promoted.

Those two i18n templates are the **only non-synthetic evidence here**, and they
are the rows worth quoting. The synthetic shapes are constructed to be
host-heavy; they show the mechanism works, not that a real page looks like
that.

**One small-cost row:** `corpus/line` at +1.6% (non-debug), range +0.3..+11.9.
It contains no template tags whatsoever, so it does not exercise the
stream-free mechanism and the difference cannot be attributed to it here. It is
recorded rather than dropped, because dropping inconvenient rows is how a
harness stops being evidence.

Everything else is **unresolved**. Note that `all-native` (non-debug, −0.6%)
and `corpus/layout` (debug, −0.6%) have negative medians; they are unresolved
rather than wins, not silently folded into a claim of no change.

**On the tolerance, precisely.** No row was labelled `SLOWER`, which by the
predeclared rule requires the *entire* observed range to exceed 2%. Two rows
nonetheless have medians above it:

| row | mode | median | range |
|---|---|---:|---|
| `all-native-stress` | nodebug | **+3.6%** | −2.5..+4.9 |
| `corpus/report` | debug | **+2.6%** | −12.1..+5.9 |

Both straddle zero across the eight repetitions, so neither is an established
slowdown — but saying "no row exceeded the tolerance" would be false, and a
mixed-sign range does not erase a median above it. Neither template exercises
the stream-free mechanism (`all-native-stress` has no host tag; `report` has
none either), so neither is attributable to it from this experiment.

The accepted costs are **medians**; the observed ranges are wider and are not a
bound.

## What the gate caught

Before `STREAM_FREE_MIN_TOKENS`, `tiny-one-host` measured **+4.9%**, range
+4.2..+7.7 — a real cost, outside the noise, above the predeclared tolerance,
on a template with nothing to gain. The threshold exists because of that
measurement, and it is recorded here rather than quietly fixed. Eight tokens is
a conservative tuning parameter, not a measured break-even.
