# Bakerydemo render measurements — 2026-10-12

The optimized native walk takes **2.26% less time than stock Django** and **3.11% less time than the previous Rust backend** in the final warm-render comparison. All eleven pages are faster than Django in this run, and their output hashes match across all three backends. These are local measurements on Python 3.12.9 and Django 6.0.9, not a general speed guarantee.

## What changed

- Keep Python lookup-name objects on the compiled plan, avoiding repeated allocation and hashing during rendering.
- Prove that ordinary objects have no subscript method without constructing an `AttributeError`. Custom metaclass hooks and descriptors retain the attribute-probing path.
- Append exact stock `SafeString` values directly and spell ungrouped exact integers in the native emitter. Subclasses, changed string methods, and localized grouped numbers retain Python formatting.
- Reuse interned names for callable flags and delegated render methods.

## Final comparison

Each backend runs in a separate persistent process with a single configured template backend, production settings, and template caching. Each page receives ten warmup renders and 250 timed renders per backend. The three-way run rotates backend order between samples. The contexts come from real bakerydemo requests; rendering still includes work initiated by template tags and the ORM. Timings include UTF-8 encoding.

| Page | Django (ms) | Previous Rust (ms) | Optimized Rust (ms) | Rust/Django |
| --- | ---: | ---: | ---: | ---: |
| `/` | 17.935 | 17.954 | 17.550 | 0.9785 |
| `/blog/` | 10.912 | 10.982 | 10.639 | 0.9750 |
| `/blog/wild-yeast/` | 6.485 | 6.495 | 6.327 | 0.9757 |
| `/breads/` | 7.295 | 7.389 | 7.042 | 0.9654 |
| `/breads/bagel/` | 4.279 | 4.325 | 4.156 | 0.9712 |
| `/locations/` | 6.760 | 6.811 | 6.664 | 0.9859 |
| `/locations/hof/` | 7.074 | 7.189 | 6.991 | 0.9883 |
| `/recipes/` | 4.985 | 4.995 | 4.833 | 0.9694 |
| `/recipes/hot-cross-bun/` | 6.239 | 6.480 | 6.168 | 0.9886 |
| `/gallery/` | 10.484 | 10.473 | 10.139 | 0.9671 |
| `/about/` | 3.826 | 3.832 | 3.776 | 0.9867 |

The summary is the geometric mean of the per-page median ratios, giving each page equal weight. The previous Rust backend is 0.88% slower than Django in the same run. A separate final two-backend render run measured a 2.05% reduction. The final Django test-client request run measured **2.35% less time** with matching output hashes on all eleven pages; it includes views, middleware, database work, and rendering, but excludes network transport. Earlier intermediate implementations ranged from near parity to small regressions. Full request samples are saved locally under `target/benchmarks/bakerydemo-request-2026-10-12.json`.

[Measurement summary and output hashes](bakerydemo-2026-10-12.json) records the bakerydemo commit, baseline engine commit, interpreter version, and native artifact hashes. The candidate is the optimization accompanying this report, built before the alpha2 version bump. Full three-way samples are saved locally under `target/benchmarks/bakerydemo-three-way-2026-10-12.json`.

## Reproduce

The bakerydemo environment needs its demo database, production settings, and the Rust backend installed. The harness uses bakerydemo’s `.venv/bin/python` by default; `--python` selects another environment. It records the imported native extension path and SHA-256 so a stale installation is visible.

```sh
python scripts/bench_bakerydemo.py ../bakerydemo --samples 250 --output render.json
python scripts/bench_bakerydemo.py ../bakerydemo --mode request --samples 250 --output request.json
```

To measure a new wheel without changing bakerydemo’s installation, extract the wheel into a temporary directory and pass that directory as `--rust-pythonpath`. Each worker uses exactly one backend, including templates loaded internally by Wagtail. Configuring both backends in one process would let those internal loads use the first backend and distort the comparison.

## Validation

The full `scripts/check.sh` gate passes: 799 core Rust tests, 12 interpreter-backed Rust tests, 679 Python tests, and 1,547 Django template tests (six expected skips), plus formatting, Clippy, transcription checks, differential comparisons, and packaging. New tests cover safe-string subclasses and method rebinding before and after backend import, lone surrogates, metaclass and descriptor probes, live grouping settings, and English/German number formatting.
