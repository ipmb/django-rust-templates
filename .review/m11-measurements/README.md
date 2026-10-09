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

`-I` implies both `-s` and `-E`, and it is **`-E`** that ignores `PYTHONPATH` —
`-s` only drops the user site directory. Measured here:

```
PYTHONPATH=$PWD/.django .venv/bin/python -s -c "import django"  -> .django/django/__init__.py
PYTHONPATH=$PWD/.django .venv/bin/python -E -c "import django"  -> ModuleNotFoundError
```

The narrow flag is therefore not a substitute: someone who reached for `-s` from a
sentence that named it as the cause would keep the shim on the path, which is the
failure this paragraph exists to prevent. And because the shim is installed by a
`sitecustomize` on `PYTHONPATH`, a measurement run under `-I` silently measures
stock Django and would "prove" the filter path is Django's by measuring a run in
which our engine was never installed. That is not hypothetical: the first run of
this script did exactly that, and printed `django.template.base -> .django/...`
while the conclusion it was written to test was about our `_impl/base.py`.

**The rule is not "never use `-I`" — it is "never use `-I` when you want the
shim".** An instrument that wants the *opposite* needs the opposite flag, and
`.review/m12-measurements/`'s unshimmed probe headers **require** `-I` for the
same reason this forbids it: `-I` makes `sitecustomize` unreachable rather than
asking it to stand down. That is a sharper guarantee than
`DJANGO_RUST_TEMPLATES=0`, and its cost is that the pinned tree goes with it —
`.django/` arrives by the `PYTHONPATH` that `-E` closes, so those probes carry an
explicit `sys.path.insert` of their own.

The two directories therefore disagree about one flag **deliberately**. Stated
here as well as there because a reader who finds the same flag forbidden in one
place and required in another will read a contradiction, and the repair they will
reach for — making one match the other — silently breaks whichever instrument
they did not run. That is D3′, the vacuity reasons and the `milestone_ownership`
clause a fourth time: two correct statements, where the second reader cannot tell
the disagreement was chosen.
