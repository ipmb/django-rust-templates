# M11 measurements

One script per claim in `.review/m11-recon.md`, named for the claim. Each is
re-runnable and prints what it measured; none of them asserts, so the record is
the output rather than a pass.

| script | claim it measures |
|---|---|
| `m11_measure_filter_path.py` | §1 — with the shim installed, which callable `Parser.find_filter` and `FilterExpression.filters` hold for a built-in filter. The answer is Django's `defaultfilters.py` function, which is why the 454 `filter_tests` rows say nothing about Rust's filter bodies today. |

Run any of them with the shim on `PYTHONPATH` — **not** under `-I`:

```bash
. scripts/env.sh
export PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django:${PYTHONPATH:-}"
.venv/bin/python .review/m11-measurements/m11_measure_filter_path.py
```

`-I` implies `-s` and ignores `PYTHONPATH`, and the shim is installed by a
`sitecustomize` on it — so a measurement run under `-I` silently measures stock
Django and would "prove" the filter path is Django's by measuring a run in which
our engine was never installed. That is not hypothetical: the first run of this
script did exactly that, and printed `django.template.base -> .django/...` while
the conclusion it was written to test was about our `_impl/base.py`.
