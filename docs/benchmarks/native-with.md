# Native with-scopes

`{% with %}` previously delegated its whole body to Python. The native walk now resolves bindings through Python's expression and mapping protocols, enters the actual context manager, and renders the body in Rust. It reuses the enclosing walk's lookup state while keeping the body's output and join separate.

Bindings still resolve against the outer context before any new layer is pushed. Custom `WithNode.render` methods, changed body renderers, and bodies replaced by `Context.push` retain their Python calls. Scoped loops use the native path with the ordinary context manager and delegate with custom managers. Nested conditions preserve their own joins.

The port also fixes two execution-order issues: invalid string pieces wait until the nodelist join, so later sibling tags run; manager methods are found through the actual type dictionaries and descriptor slots, avoiding metaclass and descriptor-instance hooks. Binding order and missing-method errors follow the running Python version, including Python 3.14's exit-first binding order.

## Measurements

Two eleven-page bakerydemo comparisons measured about **0.3–0.4% less warm render time than alpha2**, with matching output hashes throughout. This is a small result, near the noise floor of the full-page workload. The final three-way medians and artifact hashes are in [native-with.json](native-with.json); raw samples are retained locally under `target/benchmarks/native-with-three-way.json`.

Focused scope workloads show larger savings over alpha2 (Python 3.12.9, medians of 500 samples; identical outputs):

| Workload | Alpha2 (µs) | Native with (µs) | Reduction |
| --- | ---: | ---: | ---: |
| Twelve lookups inside one with | 16.42 | 10.08 | 39% |
| Six condition/lookup pairs | 16.13 | 9.79 | 39% |
| Fifty looped lookups | 65.96 | 35.83 | 46% |
| Twelve lookups in nested with scopes | 17.42 | 11.96 | 31% |

The bakerydemo benchmark still spends most of its time in tag bodies, ORM work, and caches, which these changes preserve. All implementation and test changes are inside django-rust-templates.

Use `scripts/bench_bakerydemo.py` with a freshly extracted candidate wheel and `--rust-pythonpath` to reproduce the page comparison. Baseline and candidate were built separately from alpha2's source and this implementation; the three-way run rotates Django, baseline, and candidate order, with 250 samples per page and backend.

## Validation

The complete `scripts/check.sh` gate passes, including 799 core Rust tests, 12 interpreter-backed Rust tests, 691 Python tests, and Django's 1,547 template tests with six expected skips. New differential rows cover scope restoration, simultaneous binding visibility, legacy syntax, missing values, surrogate output, changed render methods and bodies, custom manager entry and exit, descriptor binding, suppressed exceptions, and join order inside nested conditions and loops.
