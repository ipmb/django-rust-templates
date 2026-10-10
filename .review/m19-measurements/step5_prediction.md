# Step 5, slice 1 — the walk owns a `{% block %}` body

Written before the code, committed with it.  Two sections are measurements and
the rest is a prediction; the split is stated at each heading.

## 1. Why this slice, and why it is not step 4

**Measured.**  Spec §5 sequences *step 4, lazy materialisation* before *step 5,
the rest of the tree*.  Both levers were priced first, in one unit each, because
a decision needs two numbers in the same denominator or it is a reading of the
list's order.

| lever | instrument | measurement |
|---|---|---|
| step 5 — inheritance | `inheritance_share.py` | **99.0% / 99.3% / 99.6%** of the render on `real-world/normal`, `real-world/complex`, `django-only/realistic` happens inside the one delegated `ExtendsNode` frame |
| step 4 — lazy materialisation | `lazy_size.py` | **18%–51%** of `from_string` is `parser.materialise`, the Python tree; the ceiling is `real-world/large` at **50.7%** |

Those are not yet the same unit, so here is the second number for each, taken
from the report's own table on the same artifact (`sha256:bc4c750a`):

| row | mode | ours/stock now | what the lever reaches |
|---|---|---|---|
| `real-world/normal` | render | 0.919 | ceiling **0.298** (`step5_ceiling.py`) |
| `real-world/complex` | render | 0.627 | no flattened fixture; below |
| `django-only/realistic` | render | 0.927 | no flattened fixture; below |
| `real-world/large` | parse | 1.008 | **≈0.50** if materialisation went to zero |
| `real-world/normal` | parse | 0.995 | ≈0.86 |

**The coupling decides it, and it runs 5 → 4.**  Step 4's premise is §3.2's *"the
Python tree does not have to be built to render."*  That premise is **false today
on exactly the rows that matter**, and the census says why: a delegated
`BlockNode` reaches `block.nodelist.render(context)` in Python, and a delegated
`ExtendsNode` reaches `compiled_parent.nodelist`, so on `real-world/normal`,
`complex` and `realistic` the render path *requires* the materialised tree.
A laziness built first would therefore have to serve the delegate on the render
path — and the very next slice would delete that need and simplify it again.

**So this slice takes step 5 first, and the spec's order is deliberately
swapped rather than quietly followed.**  The reason is not that §5 is wrong
about step 5's value — it is the opposite: §5's own note on step 5, *"until it
lands, `ExtendsNode.render` materialises the parent twice and slices 2–4
benchmark as a regression on the rows §4 headlines"*, is the same claim this
section is making, and it is the note that argues for 5 before 4.  Step 4 keeps
its place; only the order of the two moves.

**And slice 1 is the block body, not `ExtendsNode`.**  `flat/normal` —
`real-world/normal` with the inheritance spliced out by source substitution, the
same bytes out, the same 50-row loop, the same include — renders in **121.1 µs**
against the shipped page's **373.0 µs** and stock's **406.0 µs**.  The delegation
costs ~252 µs of our own 373.  `ExtendsNode` is one Python frame per page and the
block-context dance; the *body* is where the loop lives.  This slice takes the
body, keeps `ExtendsNode` on the Python side, and predicts above the ceiling for
that reason.

## 2. What the slice is

**The hook already exists in one place and gains a second.**  `Template._render`
checks `self._native_plan` and hands it to `_native.render_plan`
(`engine/base.py:186-189`), set by `engine/parser.py::template_for` from the plan
`compile_template` built.  `NodeList.render` (`engine/base.py:1177-1178`) is the
other end of the same shape: it is *the* call a delegated `BlockNode` makes on the
overriding block's body, and it has no plan.

So, in Rust, beside the existing top-level `plan_list`:

* a **descent pass** over `(NodeListDesc, NodeList)` pairs — the same pairing
  `plan_list` already does, recursed through every declared nodelist of every
  node, whether or not that node is walk-rendered — which for each
  `BlockNode` plans its body against the materialised `node.nodelist` and, when
  the plan holds, attaches it to that `NodeList` object;
* `NodeList._native_plan = None` as a class default (the same reason
  `Template._native_plan` has one: `NodeList` is built by several paths and a
  missing attribute would be an `AttributeError` in `render` rather than a
  fallback), and one branch in `NodeList.render`.

**Why the attribute is set on the `NodeList` and not on the `BlockNode`.**  The
block that renders a body is frequently *not* the node the walk is looking at:
`BlockNode.render` pops the override out of the block context and calls
`type(self)(block.name, block.nodelist)` on it, and `block.super()` re-enters
through the base template's block.  The one object those paths share is the
**body nodelist**, so that is the object that has to carry the plan.  It also
makes `{{ block.super }}` work without a second mechanism: the base block's body
is a different `NodeList` with its own attached plan.

**The fallback is the existing one and it is total.**  A `NodeList` with no
attached plan renders line 1178 exactly as before.  A body the descent declines
(any node whose class and descriptor disagree) simply gets no attribute.  The
descent is therefore a *lost optimisation* per block, never a different answer,
which is the same contract `plan_list` has.

## 3. The prediction — falsifiable, and checked against the census's four meters

The census is `.review/m19-measurements/node_census.py`; the numbers below are
its columns on the current tree, which is the "before" they are stated against.

### 3.1 `delegated:` — the classes that must leave

| row | before | predicted after |
|---|---|---|
| `micro/extends` | `BlockNode=2 ExtendsNode=1` | `ExtendsNode=1` |
| `real-world/normal` | `BlockNode=2 ExtendsNode=1` | `ExtendsNode=1 IncludeNode=1` |
| `real-world/complex` | `BlockNode=2 ExtendsNode=2` | `ExtendsNode=2 IncludeNode=73` |
| `django-only/realistic` | `BlockNode=3 ExtendsNode=1` | `ExtendsNode=1 BlockTranslateNode=50 …` |
| `real-world/large` | `-` | **unmoved** — the control: no inheritance in the source |

Two of those rows predict a delegate that is **not there now**: the include in
`normal`'s content block and the 73 includes in `complex`'s inner loop are
currently *inside* Python-rendered block bodies, so the walk never sees them and
the census cannot count them.  A row that gains a delegate is the signature of
the walk reaching code it previously could not, which is the opposite of a
regression and has to be read as one deliberately.

`real-world/large` is the positive control.  It has no `{% extends %}` and no
`{% block %}`, so a descent that changed anything at all on that row would be
doing something other than what this section says it does.

### 3.2 `handled:` and `out deleg` — what must appear

* `real-world/normal` **gains** `ForNode=1` and its `IfNode`s in `handled:`,
  and `out deleg` falls from **198.8%** to **~0.7%** — the footer's 41
  characters and nothing else, the same value `flat/normal` reads.
* `micro/extends` `out deleg` **128.1% → 0.0%**: it has no include, so once the
  blocks are walked there is nothing left to delegate but `ExtendsNode`, whose
  own frame returns the whole page and is the one that stays.
* `django-only/realistic` — the loop is inside its content block, so `ForNode`
  moves to `handled:`, but the body holds a `{% blocktranslate %}` per row, so
  the body **does not seal** (`seal` refuses a body with a delegated node) and
  the walk still writes loop state 50 times.  Predicted, not hoped for: its
  `nodes H/N` deleg% falls while its `body xings` stay high.
* `real-world/complex` — every block body is planned but its *inner* loop holds
  an include per iteration, so the inner body does not seal either.

### 3.3 Timing — the pair, because §4 item 9 is not optional

`render` and `parse` both, ours/stock, before → predicted after.  A block-body
plan is built at compile time and read per render, so **the render row is the
one that can move and the parse row is the control**: a per-render saving with a
parse row that moved the same distance would mean the plan is being rebuilt.

| row | mode | before | predicted |
|---|---|---|---|
| `real-world/normal` | render | 0.919 | **0.33–0.45** (ceiling 0.298) |
| `real-world/normal` | parse | 0.995 | 0.85–0.99 |
| `real-world/complex` | render | 0.627 | **0.45–0.60** |
| `real-world/complex` | parse | 0.698 | 0.62–0.72 |
| `django-only/realistic` | render | 0.927 | **0.40–0.60** |
| `django-only/realistic` | parse | 0.971 | 0.85–0.99 |
| `real-world/large` | render | 0.299 | **0.28–0.32** — unmoved |
| `micro/extends` | render | 1.064 | **0.70–0.95** |

The intervals are wide because the residual is a *count* of Python frames I have
not measured one at a time — one `ExtendsNode` frame with `find_template` and
`push_state`, one or two `BlockNode` frames, and the per-render
`compiled_parent.nodelist.get_nodes_by_type(BlockNode)` walk over the whole
parent that `ExtendsNode.render` does at `loader_tags.py:152`.  A result **below
0.298** on `real-world/normal` is not evidence of success; it is evidence the
fixture or the meter is wrong, the same way a result below the floor is.

### 3.4 What would falsify this, and what would not

* **Falsified** if any of the four `django_suite` rows changes bytes, or if the
  1547 moves at all: block semantics are the most heavily tested thing in the
  suite (`test_extends.py`, `test_partials.py`, `recursive_templates/`), and a
  slice that reaches correct output by rendering a *different* block than Django
  would is a divergence that only shows on a template the corpus does not have.
* **Falsified** if `real-world/normal`'s `out deleg` does not fall below, say,
  10%: that would say the block bodies were not the thing being rendered.
* **Not falsified** by `real-world/large` moving: that row has no inheritance,
  so a move there is a bug in the descent and would be reported as one.
* **Not falsified** by a timing above my interval: the interval is a reasoned
  residual and this section says so.  A result outside it gets a re-measurement
  and a corrected interval, not a rewording.

## 4. The instruments this slice adds

| file | what it settles |
|---|---|
| `inheritance_share.py` | the delegated share of the render, in µs, for the three inheritance rows |
| `lazy_size.py` | the materialisation share of `from_string`, step 4's ceiling |
| `step5_ceiling.py` | `flat/normal` against the shipped page, the ceiling for this slice |

All three are `.review/` records rather than shipped instruments, and none of
them is on the gate's path.
