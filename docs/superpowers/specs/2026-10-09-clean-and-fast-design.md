# Make it fast, and measure it — the M19 design

Status: **revision 2, for review.** Revision 1 was reviewed and FAILed with five
BLOCKING items and six defects. §8 is the reply: what changed, and the one claim
I dispute with a measurement. Supersedes the M18.0 gating argument (§7 of the M18
design) on one point, and takes over M18.2–M18.4.

## 0. What this milestone is

Pete's brief: *"clean this up and make it fast"*, with a benchmark against stock
Django and Jinja2 as the evidence. **The benchmark is three engines** — stock
Django, Jinja2, and this package — after Pete removed minijinja from it
(2026-10-09); the corpus's exclusions were re-measured over the three that remain
rather than inherited from a run over four, and the removal's two costs are named
in §4. The engine works — it is a drop-in backend, and Django's own
`template_tests` pass — but it is **slower than the thing it replaces**, which for
this package is the only claim that matters.

Two deliverables:

1. **An instrument.** A benchmark that renders the same templates through three
   engines and writes an HTML report.
2. **Speed.** The engine must beat stock Django at render and at compile.

**Revision 1 described the instrument as future work, and it is not.** `scripts/bench.py`
already has `bench_engines.ENGINES` (three engines), output-equality verdicts
(`equality`, `refusals`), and the extension-identity check (`native_artifact`).
§4 below is therefore written as a delta against what exists, not as a build
list — revision 1's version would have had an implementer rebuild it.

## 1. The measurement this milestone is built on

Django 6.0.9, CPython 3.14.4, `--release` extension
(`python/django_rust_templates/_native.cpython-314-darwin.so`, 2641824 bytes,
sha256 `1b3bf993eb2d136d`, identical to `target/release/lib_native.dylib`),
best-of-7. Settings come from `filter_probe.ensure_settings()`, which is what the
harness runs — **`USE_I18N=True`**, and §1.5 is why that clause is in this line.

### 1.1 Where the engine is today

`scripts/bench.py --html`, the instrument that exists. Ratios are ours over
stock; every row prints the same sha256 on every engine that ran it.

| workload | mode | stock | ours | ratio |
|---|---|---|---|---|
| real-world/normal | render | 434.1 µs | 680.9 µs | **1.57×** |
| real-world/normal | parse | 615.9 µs | 921.0 µs | **1.50×** |
| real-world/complex | render | 960.8 µs | 1380.5 µs | **1.44×** |
| real-world/complex | parse | 1106.6 µs | 1566.8 µs | **1.42×** |
| real-world/large | render | 676.5 µs | 910.3 µs | **1.35×** |
| real-world/large | parse | 2163.5 µs | 3006.9 µs | **1.39×** |
| micro/text | parse | 3.9 µs | 14.9 µs | **3.80×** |
| micro/loop-text | render | 14.8 µs | 15.4 µs | 1.04× |

The worst ratio in the corpus is `micro/text`'s **3.80×**, and it is the row with
no nodes in it at all — 14.9 µs of compile against stock's 3.9 µs, on a template
whose *render* is at parity (1.8 µs on both). The best is `micro/loop-text` at
1.04×, where the work is a Python loop body and neither engine's per-node cost
dominates.

### 1.2 Per variable, on the shipped path

Floor differencing: a page of N `{{ a }}` minus a page of N text nodes of the same
width, both rendering byte-identical output — the subtraction is only
like-for-like because that is asserted, not assumed
(`.review/m19-measurements/production_render_path.py`).

| N | rust per variable | stock per variable | ratio, median of 5 rounds |
|---|---|---|---|
| 50 | 1.4581 µs | 1.0723 µs | **1.37** (1.36–1.38) |
| 200 | 1.4456 µs | 1.0753 µs | **1.36** (1.34–1.37) |

The absolute columns are round 1 of 5; the probe now prints every round, because
this ratio is a difference of two differences and **a single run of it is a run,
not a measurement** — three runs printed 1.360, 1.375 and 1.402 for the same
quantity on the same tree, which is why §1.2 quotes two places and the transcript
prints all five rounds.

**The number that governs this milestone is ~1.37×, and it is neither of the two
the review was offered.** Revision 1 said 2.24× (from `_native.resolve` 0.524
against stock's 0.234); the review said "at parity or slightly ahead", 0.949,
measured on the M18.0 `render_floor` prototype. Both are measurements of
something real and neither is a measurement of the shipped engine: the prototype
is not what the package installs, and a bare resolver call is not what a variable
costs. The decomposition of that per-variable cost is:

| | µs | removable? |
|---|---|---|
| `_native.resolve("a", ctx)` | 0.52 | 0.22 of it is a re-parse (§1.3a) |
| `render_value_in_context` — a plain `str` | 0.79 | ~0.76 (§1.3b) |
| Python frames: `Variable.render`, `render_annotated`, `NodeList.render` | 0.16 | ~0.16 (§3.1) |
| | **1.47** | **~1.14 of it** |

Those three components are separate best-of-7 probes and sum to 1.47; the
differenced measurement they decompose is 1.458 µs (N=50, round 1). The two close
to a hundredth of a microsecond, and **that gap is the precision the components
carry** — a third decimal here would be a run, not a measurement. Stock spends
0.24 + 0.79 + 0.05 = 1.08 µs on the same variable by the same arithmetic, against
a differenced 1.072. So the honest target for a native walk is ~0.34 µs —
**about 3.2× ahead on variables**, not the 3.7× the review estimated and not
revision 1's number at all.

### 1.3 The per-operation costs

Every timing in this section is this probe's, hoisted `Variable` objects, best of
7, recorded in `.review/m19-measurements/production_render_path.txt`. The third
column is the corpus's own depth distribution, which is the row of this table that
matters and was added after the review. **The four `parse_variable` rows are a
different function from the one they sit under** — see below — and are marked so
rather than aligned as if they decomposed their neighbours:

| operation | µs | share of the corpus |
|---|---|---|
| `_native.resolve("a", ctx)` — depth 1 | 0.52 | 2% |
| `_native.resolve("row.name", ctx)` — depth 2 | 0.73 | **98%** |
| stock `Variable("row.name").resolve(ctx)` | 0.30 | |
| **`_native.resolve_lookups(['row', 'name'], ctx)` — depth 2, after step 1** | **0.65** | **98%** |
| `_native.resolve("b.c.d", ctx)` — depth 3 | 0.82 | one variable |
| stock `Variable("b.c.d").resolve(ctx)` | 0.34 | |
| `stock_ctx["a"]` — bare dict read | 0.12 | |
| *not a decomposition, a different pyfunction:* | | |
| `_native.parse_variable("a")` | 0.22 | |
| `_native.parse_variable("row.name")` | 0.26 | |
| `_native.parse_variable("b.c.d")` | 0.28 | |

**(a) The render path does call into Rust per variable, and the re-parse it paid
for was the smaller half of the gap.** The review's BLOCKING 1 said the render
path "never does" and calls `LookupScope::resolve` with strings pre-parsed at plan
time; it was measured on `render_floor`, a prototype the package does not install.
Measured at the time of the review, it called `_native.resolve` once per variable:
a three-variable template rendered `'1|2|3'` with `{'_native.resolve': 3,
'Variable._resolve_lookup': 3}`. The chain was `VariableNode.render` →
`FilterExpression.resolve` → `Variable._resolve_lookup` →
`_native.resolve(".".join(self.lookups), context)` →
`crates/template-py/src/lib.rs:2635-2636`, `parse_variable(path)`.
`LookupScope::resolve` is the *floor prototype's* path, and the prototype is not
what ships.

**Step 1 has since landed**, and the call is now `engine/base.py:1073`,
`_native.resolve_lookups(self.lookups, context)`, entering at
`crates/template-py/src/lib.rs:2692-2697`, with no `parse_variable` between the
shell and the walk. The row above it is what that bought, measured rather than
predicted.

So revision 1's 2.24× was a true statement about the shipped engine and a wrong
account of why. **And so was revision 2's: "0.22 µs of every variable is spent
re-deriving a lookup".** The review's decomposition (`parse_variable` 0.219 of
0.522) is a correct measurement of *two calls*, but `_native.parse_variable` is a
pyfunction returning a `#[pyclass] Variable` with `lookups`, `literal` and
`translate` on it, while `_native.resolve` calls the internal Rust
`parse_variable`, which builds a struct and no Python object. The 0.22 was never a
share of the 0.52 and cannot be subtracted from it. **The measurement that can be
subtracted is step 1's own: 0.081 µs at depth 2 (median of five rounds, range
0.074–0.084), which takes the ratio against stock from 2.42× to 2.15× and closes
19% of the resolver gap — not 35%, and not 52%.**

**The saving is depth-independent, and that is worth a line because the rest of
this section is about depth.** Measured at 0.080 / 0.081 / 0.079 µs for depths 1,
2 and 3: the classification grows with the path and so does the `Vec<String>` the
components cost to hand over, and the two nearly cancel. So this is a *flat* win
on a corpus that is 98% one depth, which is the one case where a depth-blind
number is the right number.

**0.22 was also the depth-1 operand, and depth 1 is 2% of the corpus.** The
review's second pass censused every variable resolved during a render of all 18
rust rows: 1408 variables, 27 at depth 1, **1380 at depth 2**, one at depth 3. At
that depth the ratio the corpus actually pays is **2.42×** rather than 2.23× — our
resolver gets *relatively* worse as the path gets longer, which is M18 §7.1's
inversion arriving on the shipped path. The review's own instrument (which
resolves through `_resolve_lookup`, one frame in from mine) puts the same three
gaps at +0.351 / +0.535 / +0.580 µs, and its residual after step 1 at depth 2 is
+0.255 against my +0.347. **The two instruments are not reconciled and should not
be**: different probes, different frames, and neither is wrong.

**Two reasons to stop re-joining, and the second is the load-bearing one.**

*It is 0.08 µs a variable at the depth the corpus uses, and it was cheap.* Pass
`&[String]` across the FFI, which `LookupScope::resolve` already accepts — and it
is a **flat** win, so it does not need re-pricing as the corpus changes depth.

*And the join is not a faithful inverse of the parse, which is a correctness bug
today, not a cost.* `parse_variable` classifies as well as splits, and
`".".join(("2", ""))` is `"2."`, which the re-parse reads back as a **number**.
`Variable.__init__` gives `2.` a `2.0` literal *and* the lookups `("2", "")`, and
`Variable.resolve` reads the lookups first (`base.py:926-928`) — but
`_native.resolve` read the literal first, so `{{ 2. }}` rendered `2.0` where stock
Django renders `''`, and `{{ 0. }}` and `{{ 123. }}` did the same. That was
measured at M19 and **fixed in this milestone** by putting the Rust dispatch back
into `Variable.resolve`'s order; the shortcut removes the input class rather than
patching it, which is the better of the two and the reason step 1 is not optional.
**This is the reason that decided the step**, and it is worth more than the 0.08:
the class is not "trailing-dot decimals" but "`float()` succeeds *and* the token
ends in `.`", which includes `1_0.` — CPython's `float` takes underscore
separators — so a fix enumerated from the instances I had found would have missed
it. The class, the render comparison that found it, and the search showing the
1547 never writes one are in `.review/m19-measurements/lookup_roundtrip.txt`.

**(b) `render_value_in_context` is where a rendered variable's money goes** —
0.79 of the 1.47 µs, and for an `int`, 3.49. See §1.5, which is where BLOCKING 3
lands.

**(c) Our `escape` filter is 1.884 µs because its Rust body is one host call.**
That is deliberate and correct (`filters/escape.rs` delegates to
`FilterHost::conditional_escape` because the `__html__` protocol is shared with
markupsafe, which implements it without being `SafeData`). It is not a bug. It
is, however, the reason a Rust renderer must specialise the *common* case
natively — and **§3.1 is where revision 1 got that specialisation wrong**.

**(d) The M18.0 "floor" inherited (b), and only (b).** Revision 1 said it
"inherited (a), (b) and (c)". The review is right on both counts and I withdraw
the sentence:

- **(a) is not inherited.** The floor calls `LookupScope::resolve` through
  `PyTarget`, which takes `&[String]` and never parses. The parse is an artefact
  of the `_native.resolve` binding, which the floor does not use.
- **(c) is not inherited.** The floor declines expressions with filters outright
  — `plan_expression` returns `None` on `!expr.filters.is_empty()`. It never
  reaches the escape filter.
- **(b) is inherited**, and it is the whole of the floor's per-variable cost:
  `render_floor` fetches `render_value_in_context` once per render
  (`crates/template-py/src/lib.rs:2956-2958`) and calls it per variable in
  `append_expression_value`. That the floor and `render_desc` share exactly these
  two helpers — the resolver and the escaper — is the floor's own claim about
  itself (`crates/template-py/src/lib.rs:2755-2757`), and it is what makes the
  subtraction isolate dispatch.

The correction is therefore **narrower than revision 1 made it and points the
same way**: the floor's 0.936–0.948 is a measurement of a Rust loop paying one
Python call per variable, so it does not bound a renderer that does not make that
call. 1.076 = 0.760 removable + ≤0.316 resolver, so the direction is up.

**And the floor is not the shipped path** — the review's second sequencing note,
which I accept and have written into §5: until inheritance is native,
`ExtendsNode.render` walks `compiled_parent.nodelist` twice
(`engine/loader_tags.py:148`, `engine/loader_tags.py:154`), so every realistic-page render materialises
the parent tree regardless. Slices 2–3 benchmark as a *regression* on exactly the
rows §4 headlines.

### 1.4 What the corpus actually renders

BLOCKING 3's answer, measured by wrapping `render_value_in_context` during each
workload's render and tallying the value's **exact** type
(`.review/m19-measurements/value_mix.py`):

| family | variables | non-`str` | by exact type | `str` values escaping changed |
|---|---|---|---|---|
| micro | 57 | 1 (2%) | int 1, str 56 | 1 of 56 |
| real-world | 774 | 135 (**17%**) | int 135, str 639 | 122 of 639 |
| django-only | 556 | 403 (**72%**) | int 250, SafeString 150, lazy 3, str 153 | 3 of 153 |

Three things follow, and the second and third are defects in the corpus:

- The non-`str` share is **17% in `real-world` and 72% in `django-only`**, so
  §2's str-only pricing understates a real-world variable by roughly a quarter
  and a `django-only` variable by a factor of three.
- **`real-world/large` has 330 variables and not one of them escapes.** Its
  bytes would hash identically with autoescape off, so for that row — the
  flagship row — the equality gate says nothing about escaping. D2, and it is
  fixed in §4.
- `SafeString` appears 150 times in the corpus. Every one of them is a value
  §3.1's predicate has to *decline* (§3.1, BLOCKING 2).

### 1.5 `render_value_in_context` by value type

| value | µs | renders |
|---|---|---|
| `str "hello world"` | 0.794 | `'hello world'` |
| `str "a & b < c"` | 0.851 | `'a &amp; b &lt; c'` |
| `SafeString(mark_safe('x'))` | 0.239 | `'x'` |
| `int 12` | 3.492 | `'12'` |
| `float 12.5` | 4.144 | `'12.5'` |
| `Decimal('12.50')` | 4.451 | `'12.50'` |
| `datetime(2026, 10, 8, 12, 0)` | 13.229 | `'Oct. 8, 2026, noon'` |

These reproduce the review's numbers within ~3% (3.388 / 3.922 / 4.371 /
13.040), and **revision 1 did not**: its first probe configured
`USE_I18N=False` and every non-`str` row came out at half. `localize`
short-circuits on the setting, so 1.67 µs becomes 3.49 µs. Django's default is
`True`, and so is `filter_probe.ensure_settings()`'s. **A probe must run the
harness's settings**, and this one now does.

The consequence for §2: for an `int`, essentially all of the cost is
`localize`/`number_format` — locale-dependent Python the walk cannot remove
without reimplementing `django.utils.formats` — and `template_localtime` and
`localize` run **before** the autoescape branch, so a fast path has to clear both
before it is a fast path at all.

## 2. The thesis

Every cost centre above is a Python cost a Rust renderer does not have to pay,
and each is individually removable. Revision 1 headlined the lookup row; the
review is right that the escape row is the headline, and it is restated here
with the operand §1.5 supplies:

| cost | today | native | note |
|---|---|---|---|
| **escaping one value** | **0.79 (`str`), 3.49 (`int`)** | **~0.03 / ~0.10** | the headline. `html_escape` exists in Rust (`filters/html_escape.rs`); the fast path is exact-`str` only (§3.1) |
| a variable's lookup | 0.52 | ~0.30 | 0.22 of it is the re-join re-parse (§1.3a); the rest is the `Python::attach` per `PyTarget` method (§3.1) |
| Python frames per node | 0.16 | ~0 | `Variable.render`, `render_annotated`, `NodeList.render` |
| a filter application | 0.208 crossing + a Python frame | ~0 | 66 filter bodies already in `crates/template-core/src/filters/` |
| node dispatch | a Python method call | a jump table | |
| compile, our nodes | 2175 µs of 2397 on `large` | ~0 | materialisation disappears |

The one thing that legitimately crosses is **a value that comes out of the
context** — which is every value, but only *once* per value rather than once per
node, per filter and per lookup component.

## 3. Architecture

**A compiled template is a Rust object. Rendering walks it in Rust. Python is
reached only where the operand is a Python object.**

Unchanged from M18 §4 and not re-argued here; the boundary is the whole design:

| crosses | why |
|---|---|
| the `Context` | the API every third-party tag and filter is written against |
| a variable's final value | it comes out of the context |
| a tag or filter from a library we do not own | the compile function and the `Node` are Python |
| the output of a Python node | ditto |
| a value that is not an **exact** `str` when escaping | the `__html__` protocol (§3.1) |

### 3.1 The three changes, and the predicate correction

1. **The walk escapes natively, and the predicate is the exact type.**
   Revision 1 wrote `is_instance_of::<PyString>() && !SafeData`. The review is
   right that this reintroduces the bug §1.3c exists to prevent:
   `markupsafe.Markup` is a `str` subclass, is not `SafeData`, and carries
   `__html__`, so the predicate is **true** for it and the naive fast path
   escapes it — `'<b>bold</b>'` becomes `'&lt;b&gt;bold&lt;/b&gt;'`. The corpus
   renders 150 `SafeString` values (§1.4), so this is not hypothetical.

   The sound predicate is `is_exact_instance_of::<PyString>`: a type-pointer
   compare, cheaper than the subclass walk it replaces, and it declines every
   subclass — `SafeString`, `Markup`, and anything a user writes. Everything it
   declines takes the existing `FilterHost::conditional_escape` route, which is
   correct by construction. This is the 0.79 µs line for the ~83% of real-world
   variables that are plain `str`.
2. **The walk resolves with the GIL already held.** The review measured this
   rather than leaving it open, and the answer is that every `LookupTarget`
   method on `PyTarget` re-enters `Python::attach` — `has_getitem`
   (`crates/template-py/src/lib.rs:2042`), `get_item`
   (`crates/template-py/src/lib.rs:2052`), `get_attr`
   (`crates/template-py/src/lib.rs:2063`), `get_index`
   (`crates/template-py/src/lib.rs:2068`) and `in_dir`
   (`crates/template-py/src/lib.rs:2089`) — because the trait in `template-core`
   carries no `py`. A depth-1 lookup is
   ≥2 attaches plus a real `hasattr(type(v), "__getitem__")`. It is not fatal
   today and it is why depth 3 inverts. **Removing it is a `LookupTarget`
   signature change across `template-core`, and §5 now budgets for it** rather
   than leaving it implicit.
3. **The walk applies builtin filters in Rust**, so `{{ x|upper }}` costs a
   lookup and a jump rather than a lookup, a crossing, a callback and a Python
   frame.

### 3.2 Materialisation becomes lazy — and `extra_data` must not

The acceptance suite inspects `.nodelist`: it calls `get_nodes_by_type`,
constructs `Node` subclasses, and its custom tags call `parser.parse()`. So the
Python tree must remain the thing a caller can introspect.

It does not have to be built to *render*. But **BLOCKING 4 is correct and
revision 1 missed it**: `{% partialdef %}` does not need a render to break.
`Engine.get_template("x.html#frag")` reads `getattr(template, "extra_data", {})`
(`engine/engine.py:233`), and `extra_data` is set in
`engine/parser.py::template_for` — `template.extra_data = {} if parser is None
else parser.extra_data` (`engine/parser.py:546`) — which is *materialisation*. The partials
mapping is written there by the `partialdef` compile function
(`engine/defaulttags.py:1253`). Defer `template_for` and `extra_data` is `{}`, the
lookup raises `KeyError`, and the `getattr` default converts a missing attribute
into `TemplateDoesNotExist(partial_name)` — a plausible-looking 404 rather than a
crash. Oracle: `django_suite/template_tests/syntax_tests/test_partials.py`.

So lazy materialisation needs a **two-part protocol**, not one: the nodelist is
lazy, and the parse's `extra_data` is **eager** — copied onto the template
whenever the descriptor tree is built, whether or not anyone asked for a
`.nodelist`. The general form of the rule, which is what makes it worth stating
rather than patching: *anything a compile function wrote into `parser` and
anything `template_for` copied out of it is parse output, not render input, and
laziness must not reach it.* The test that enforces it is
`Engine.get_template` on a `#fragment` name for the whole `test_partials` module.

### 3.3 The debug contract: a native node needs a span

**BLOCKING 5 is correct and revision 1 missed it too**, and it is the stronger of
the two: `Node.render_annotated` (`engine/base.py:1089`) sets
`e._culprit_node = self` first-writer-wins, then builds `template_debug` from
`e._culprit_node.token`. A natively-rendered node has no Python `Node` to be the
culprit and no `.token`, so `e.template_debug` is absent and
`test_nodelist.py::ErrorIndexTest` (`tests/template_tests/test_nodelist.py:40`) — which renders with `debug=True` and
asserts exact `(start, end)` spans, including a `{% for %}` that raises *itself* —
fails on the row whose whole subject is the span.

`.nodelist` being lazy does not help: `_culprit_node` is a *render-time*
protocol, so it is on the walk's path by construction.

**§3 therefore needs: every natively-rendered node participates in the
`_culprit_node` first-writer protocol, carrying its own `(start, end)` span.** The
data exists — the Rust parser's tokens carry correct positions, and
`python/django_rust_templates/render.py:264` cites a `{% for %}` at (9, 25) — but
there is no protocol for handing one back, and
`python/django_rust_templates/host.py:737` already notes that Rust can carry the
class's token but not the object.

The review's three non-blocking checks on this area all pass and are recorded
rather than re-derived: `context.render_context.push_state(self)` takes the
Template and not the nodelist; `get_exception_info` reads only `self.source` and
`token.position`; `TemplateDoesNotExist.template_debug` rides the same path.

### 3.4 Delegation must be a column, not a report

A node the walk cannot render falls back to the materialised Python subtree.
**D1 is right that a `1.00×` row cannot be distinguished from a declined row
without a per-row census**, and a separate report does not fix it because the
headline chart is what a reader looks at. So the census is a **column in the same
table as the timings**: per row, how many node renders went through Rust and how
many delegated, with the delegated *class names* listed. §3.3 and §3.2 of the M18
design already require the instrument to have seen something and its
unattributed list to be empty; this makes it visible where the number it
qualifies is printed.

**Two pins, with deliberately different semantics.** They are not the same
instrument and must not share one gate:

- **The benchmark corpus is a column, and a quoting rule.** A row may not be
  quoted as a win while its delegated set is non-empty — the number is printed
  either way, but the prose and the headline tile may take a row only if its
  column reads zero. This is a per-row, per-run reading; it is not stored.
- **`template_tests` is a ratchet.** A pinned set of delegated **class names**
  that may only shrink, in `tests/` beside the other pinned counts. Three
  properties are load-bearing: it is taken at **render** time, not parse time
  (parse-time delegation is a different and much larger set — the walk's whole
  point is that a node it cannot *render* is not the same node as one it cannot
  *parse*); it is keyed by class name **and** the count of occurrences, because a
  set that shrinks by dropping a rare node is not the same progress as one that
  shrinks by dropping a common one; and it reports **both numbers always** —
  classes delegated, nodes delegated — since a judgement of "how much of Django
  is still Python" is a question about nodes and a review of "what is left to
  port" is a question about classes.


### 3.5 A family summary is over a population, and the population changed

**D3 is right.** `{% blocktranslate %}` has no Jinja2 equivalent, so a
"complex"-family average silently becomes three engines on the easy rows and two
on the hard ones. Rules, both cheap:

- **Per-row reporting is primary.** A family average is printed only when all
  three engines rendered **every** row in that family; otherwise the row's count
  is printed beside the average, or the average is withheld entirely.
- The report already prints the shared and Django-only families side by side
  rather than hiding the second. That is the right shape and it now carries the
  population in the same caption.

## 4. The benchmark

`scripts/bench.py` is the project's public evidence. **This section is a delta
against the instrument that exists**, which already has: three engines behind one
interface (`bench_engines.py`); 18 workloads in three families
(`bench_corpus.py`); two modes, pre-compiled render and parse+render; per-row
sha256 equality with `rust`-differs being a hard refusal; the extension-identity
check that names the profile that built the loaded `.so`; the iteration count
calibrated once per row on the control and pinned for every engine on it; and a
self-contained HTML report (`bench_report.py`).

**What the removal of minijinja cost, and where it is recorded.** Two things were
lost and neither is silent. (a) The corpus's exclusions were partly minijinja's:
it was the only engine of the four that escaped the solidus, so `{{ a }}` over
`a</b>` used to be an excluded case and is now a shared one — and the shared
corpus takes the win, rendering a closing tag in `micro/escape` so the agreement
is asserted on every run instead of assumed. The probe that licenses every
exclusion runs `bench_engines.ENGINES` and not a list of its own, and it is
re-run: **21 cases, 12 shared, 9 excluded** (was 10 and 11). (b) `{% empty %}`
against `{% else %}` was a one-against-two split and is now one-against-one, so
finding 3 of the corpus docstring had to be corrected twice in one sitting — to
"one against two" when the four-engine run showed it, and back to a draw when
minijinja left. **An exclusion whose width is a count of engines expires when the
engine set changes**, and both directions of that have now happened here.

What this milestone adds:

1. **Escaping coverage in every family, asserted** (D2). `real-world/large` has
   330 variables and zero of them escape (§1.4), so its equality gate tests
   nothing about escaping. Every family gains a row whose value escapes, and the
   report asserts it escaped rather than inferring it from a hash.
2. **A delegation census column** (§3.4, D1).
3. **A compile column, and the ratio that goes with it** (D4). The report has
   both modes already; what is missing is the claim. §0 promises render *and*
   compile, `micro/text` parses at 3.80×, and §2's largest single win is compile —
   so the report's headline tile must carry both, not only render.
4. **The family-population rule** (§3.5, D3).
5. **`micro/escape`'s solidus agreement becomes an assertion.** The row renders
   `a</b>` and today its claim is that the engines *agree*; the review's point is
   that agreement is the weaker claim of the two available. The row asserts the
   literal — the rendered text is exactly `a/b` — and the agreement then follows
   from the equality gate instead of being the thing tested. An agreement row
   passes when every engine is wrong together; an asserted-literal row cannot.
6. **No `rust`-vs-`django` headline over the shared-12 set.** The 12 shared rows
   are the corpus's *intersection*, and after minijinja's removal **all 9
   exclusions name jinja2 alone** — which means the 12 are, by construction, the
   constructs this engine handles, and the 9 that were dropped are exactly the
   ones it is slow at. A ratio computed over the 12 is therefore a ratio over a
   population selected by our own strengths, and it may be printed per row (where
   §3.4's delegation column qualifies it) but never aggregated into a
   family- or suite-level "we are N× faster" tile. The Django-only family stays
   printed beside it for the same reason §3.5 gives.
7. **A `--json` payload that is the report's whole input**, so the HTML is a view
   of a checkable file rather than the evidence itself. (Already true; stated
   because it is what makes 1–6 reviewable.)
8. **The report regenerated from the palette, not from a hand-spelled
   template.** Found while removing minijinja: `bench_report` spelled the `--sN`
   declarations and the `.sN` rules out by hand, so dropping the fourth hue left
   a literal `@light3@` in the emitted CSS — an invalid custom property that no
   browser reports and that a diff of the *generated* file does not make obvious,
   because the generated file is not the one anybody reads. Both are now generated
   from `SERIES_LIGHT`/`SERIES_DARK`, and `render` refuses any `@…@` left over.
9. **The two modes are not interchangeable when the cost is lazy, and the pair
   is what a slice reports.** `measure` builds its callable once per row: render
   renders a pre-compiled handle, parse calls `from_string` every iteration. A
   cost paid **once per template, per compile** is therefore amortised over
   `iters` in one mode and paid `iters` times in the other, and **`render` is the
   mode that cannot see it**. M19 measured this the hard way: the filter-routing
   cache's first version had no guard, and `real-world/large` render sat flat at
   866.6/870.8 against a 860.1/865.4 baseline while its parse row — the same
   workload, ~300 expressions and not one filter — went 2900.5/2900.1 to
   3113.6/3468.6 µs. One `if self.filters:` guard returned both to flat.
   **Flat-in-render alone is ambiguous** between "the cost is free in this mode"
   and "the change did nothing", and that ambiguity is not incidental: it is what
   made the first version look like a success. Only the *pair* discriminates, so
   a slice that reports its render number and not its parse number is reporting
   the mode chosen to hide its cost (§5 step 2).

The three-engine wiring, the corpus's intersection argument, the equality gate and
the identity check are **not** re-specified here — §0's correction.

## 5. Sequencing

1. **The cheap wins, on the shipped path.** `FilterExpression.resolve` passes
   `&[String]` instead of re-joining (§1.3a). **Landed**, and re-priced from the
   measurement rather than the prediction: **0.081 µs/variable at the corpus's
   depth 2**, flat across depths 1–3, taking our resolver from 2.42× stock to
   2.15×. Revision 3's first pass said 0.25 µs and 35% of the resolver, which came
   from a column that measures a *different pyfunction* and cannot be subtracted
   (§1.3a). **The step is still not optional, and the size is not why**: the join
   is not a faithful inverse of `parse_variable`, and `{{ 2. }}` rendered `2.0`
   where stock Django renders `''` until this milestone put the Rust dispatch back
   into `Variable.resolve`'s order. Passing the components removes the input class
   rather than patching it. Nothing here needs the walk.
2. **The render walk.** Text, variable, escape (exact-type predicate, §3.1), the
   66 builtin filters, `{% if %}`, `{% for %}` — each gated by the 1547.
   **The first slice reports its own per-variable cost against §2's 0.34 µs
   prediction, and the prediction is the thing on trial.** With minijinja out of
   the benchmark, §2's ceiling has no external witness left: it is a number this
   project predicted and this project will measure, and a prediction checked only
   by its author is a description. One number, printed beside the prediction, on
   the first slice that makes it checkable — not at the end of the step, when the
   walk is already the shape that produced it.
   **And the slice reports the render/parse *pair*, not the render row (§4 item
   9), because this step is the hazard's largest instance.** "Build a structure
   once, walk it many times" — a flat plan, a jump table, a compiled node form —
   is the same shape as the filter-routing cache at a bigger scale: every such
   structure is built on first render, amortised over `iters` in render mode, and
   paid every iteration in parse mode. A slice reporting only its render number
   is reporting the mode that cannot falsify the cost it just added.

   **Slice 1 — the escape — landed** (`38f58e1`). `render_value_in_context` now
   takes an exact-`str` fast path into Rust's `html_escape` and declines
   everything else (subclasses, lone surrogates) to the slow path unchanged, so
   only the ~0.76 µs row of §1.2's table is in play and the lookup and the Python
   frames are still there to measure against. On §1.2's instrument — rebuilt,
   since the original lived under `.review/m19-measurements/`, which no longer
   exists — **§2's prediction is now checkable, and this is the number beside it:
   0.73 µs per variable against a predicted 0.34.** Ours was 1.387 (N=50) /
   1.407 (N=200) before the slice and 0.731 / 0.731 after; a second run of the
   same instrument on the same commit gave 0.758 / 0.757, so the slice is
   **−47% ± 2 pts** and **~0.39 µs of the gap remains — 53% of it**. Stock is flat
   at 1.077 both ways, so ours/stock on this instrument moves **1.28× → 0.68×**:
   the port now beats stock per variable, where before the slice it lost. The
   render/parse pair moves together (micro/escape render 3.485 → 2.793 µs and its
   parse row with it), which is the signature of a per-render cost and the exact
   inverse of the filter-routing row above — the one that was per-compile and so
   showed only in parse.
3. **`LookupTarget` takes `py`.** The signature change across `template-core`
   that removes the per-method `Python::attach` (§3.1 item 2). Its own step
   because it touches a trait the whole walk is written against, and because
   **it is the step that has to be sized at depth 2, not at the depth where the
   gap is smallest.** At depth 1 the whole gap is 0.29 µs and the ordering looks
   like a detail; at depth 2 — where 98% of the corpus's 1408 variables live —
   step 1 leaves **+0.347 µs on my instrument and +0.255 on the review's**, larger
   than the entire depth-1 gap. A step-3 sized from the depth-1 row would be
   scheduled as optional.
   **The 0.09 µs between the two instruments is stated and not reconciled**, and
   it should not be: they are different probes on different frames — this one
   times `_native.resolve` at its entry point, the review's times
   `Variable._resolve_lookup` one Python frame in — and neither is wrong, so
   averaging them would manufacture a precision neither has. Both are larger than
   the quantity they are being compared to, which is all the ordering needs.
4. **Lazy materialisation, with `extra_data` eager** (§3.2), and the culprit-span
   protocol (§3.3) in the *same* commit — they are both "what laziness breaks",
   and either alone is a green suite that lies.
5. **The rest of the tree** — inheritance, `{% include %}`, the remaining tags —
   until the delegated set is empty on the benchmark corpus and pinned as a set.
   **Inheritance is not last-by-convenience**: until it lands,
   `ExtendsNode.render` materialises the parent twice and slices 2–4 benchmark as
   a regression on the rows §4 headlines.
6. **Cleanup**, in the same commits: the M18.0 spike's
   `render_desc`/`plan_nodelist` machinery goes; the duplicated wire-tuple
   spellings (`LexedToken`/`WireToken` and the four inline rebuilds) collapse to
   one; the doubled `from django_rust_templates import _native` at the head of
   `render.py` goes.

## 6. Non-goals

- **Not dropping `django.template` support.** The 1547 are the oracle.
- **Not a new template language.** Every construct renders as Django renders it,
  byte for byte.
- **Not touching `django_suite/`.** It is generated; `gen_django_suite.py --check`
  stays the gate on that.
- **Not reimplementing `django.utils.formats`.** An `int`'s 3.49 µs is mostly
  locale-dependent Python, and §1.5 says so rather than implying the escape fast
  path covers it.
- **Not abi3, not PyPI, not CI.**

## 7. Open questions for review

1. **Where does the delegated set get pinned?** The benchmark corpus is written
   by the implementer; the acceptance suite is not. Should the pin be over
   `template_tests`' own templates instead, so it is not a set we chose?
2. **Does the `{% block %}` carrier survive laziness? — measured, and yes.** M18
   §4.1 established that a rendered block puts a Python carrier in the context and
   that `{{ block.super }}` reaches it through variable resolution, which calls it
   if it is callable. Laziness adds a narrower question: *what may be read off that
   carrier*, since a lazily-built tree has no `BlockNode` to put there. Measured
   (`.review/m19-measurements/block_carrier.py`, transcript beside it), on Django
   6.0.9: in a real two-level render the carrier is placed four times and **exactly
   one name is ever read off it — `super`, called with no arguments** — with the
   rendered bytes identical under a delegating proxy that would have changed them
   had it not been an observer. Django writes `context["block"]` twice
   (`loader_tags.py:55`, `loader_tags.py:65`) and reads it **nowhere** in
   `django/`; every other quoted `"block"` in the tree is the `@register.tag`
   declaration (`loader_tags.py:214`). So the carrier's interface is entirely what
   templates ask of it, and the acceptance suite asks for `block.super` 38 times
   across 22 files — re-derived in both suite trees under four metrics, since a
   count is only comparable to a count taken the same way — and for no other
   attribute. **The carrier is one object per rendered block exposing a
   zero-argument `super`; the block body stays in Rust.** The re-entrancy is real
   and is the whole content of that callback; what this settles is that the
   callback need not hold a node tree, which is the only thing step 2 was waiting
   on. Note also that M18's own probe for §4.1 is **not in the tree** — `198fa27`
   removed every `.review/m*-measurements/` directory and `bb5de99` restored the
   specs but not the probes — so this file re-measures those four facts rather than
   inheriting them, and the `38 across 22 files` reproduces exactly.
3. **Is the culprit-span protocol per-node or per-descriptor?** §3.3 needs a span
   on every natively-rendered node; whether that is an `Option<(u32, u32)>` on
   the walk's frame or a table the walk indexes is an implementation choice with
   a measurable difference, and I would rather measure it than pick.

## 8. Reply to the review

**Accepted, and changed above:** B2 (§3.1, exact-type predicate), B3 (§1.4, §1.5,
§2 — the value mix is now measured and the escape row is the headline), B4
(§3.2), B5 (§3.3), B1's decomposition (§1.3a), the §1.3d correction (§1.3d,
narrowed to "inherited (b)"), D1 (§3.4), D2 (§4.1 — and the hole is in
`real-world/large`, which the review did not name), D3 (§3.5), D4 (§4.3), D5
(§0 — and this is the correction that saves the most work), D6 (the scripts are
committed), the NIT (`iters` is pinned per row).

**Disputed, with a measurement: B1's premise.** The render path does call
`_native.resolve` — three calls for a three-variable template, reached from
`engine/base.py:1073` — so `LookupScope::resolve` is the prototype's path and not the
shipped one, and revision 1's 2.24× was a true statement about the shipped engine
with a wrong account of why. The review's decomposition of that 0.522 is exactly
right and is adopted; its *conclusion* that the engine's resolver is "at parity
or slightly ahead" is a property of a prototype the package does not install.
The number that governs the milestone is ~1.37× (§1.2), measured on the shipped
path, and neither revision 1's 2.24× nor the review's 0.949× is it.

**One correction of my own, not raised in review:** revision 1's
`render_value_in_context` table was measured with `USE_I18N=False` and was low by
a factor of two on every non-`str` row (§1.5). The review's numbers were right
and mine were wrong, because the review ran the harness's settings and I
hand-built mine.
