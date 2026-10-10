# Step 3a acceptance prediction — written before the measurement

The slice: the walk owns a filtered `{{ … }}` and a delegated `{% if %}`. The
planner gains `plan_emit` (head from the descriptor, filter chain from
`FilterExpression.filters` routed by handle through `django_own_filter_names`)
and `plan_if` (`TemplateLiteral` conditions only), and `seal` therefore accepts a
loop body that contains either — which is the whole reason the step is worth
taking, because **a body is sealed only if every node in it is walk-rendered**.

Baseline is `node_census.txt` (taken on `0f5ae85`, the commit under this one).

| row | baseline `out` delegated | prediction | why |
|---|---|---|---|
| `micro/if` | 100.0% | **0.0%** | the only node is the `IfNode`, and it is now planned |
| `micro/filter` | 100.0% | **0.0%** | the only node is the filtered `VariableNode` |
| `flat/normal` | 98.2% (5623/5728) | **≈ 0.7%** (41/5728) | every node in the loop body becomes walk-rendered, so the loop **seals** and the table stops coming back from Python; what is left is the `IncludeNode`, whose whole output is `bench_corpus.FOOTER` = 41 chars |
| `real-world/large` | 3.5% | **0.0%** | its 20 delegated nodes are all `IfNode`s with bare-lookup conditions |
| `real-world/normal` | 198.8% | **unmoved** | its page body sits behind two `BlockNode` delegates, and baking the leaves inside a block changes nothing about who renders the block |
| `real-world/complex` | 308.6% | **unmoved**, node share down | same shape: the leaves shrink, the `BlockNode`s stay |
| `django-only/for-tags` | 99.6% (3408/3423) | **unmoved** | its body holds `{% cycle %}`, `{% widthratio %}` and `{{ forloop.counter }}`: two nodes nothing plans, and a `forloop` read that `seal` refuses outright |
| `nested for (2 deep)` | 100.0% | **100.0%** | `seal` refuses a body containing a nested `{% for %}` — this row is the negative control for the seal rule |

Two of these are claims the census cannot make on its own, so they are asserted
separately and *before* running: the seal half of `flat/normal` and
`real-world/large` is only visible as `ForNode` absent from `delegated:` and its
inner `TextNode`/`VariableNode`s present under `handled:`.

**What this prediction deliberately does not claim.** It says nothing about
timing. `bench.py`'s render mode times a pre-compiled handle and amortises any
per-object one-off over `iters`, so a step that removes a per-node delegate can
be flat in render mode and still be large in parse mode — and flat-in-render
alone is ambiguous between "free" and "did nothing". The timing claim belongs to
the bench run that follows, on both rows.
