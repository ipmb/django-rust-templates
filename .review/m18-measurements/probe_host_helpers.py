"""Are `host.py`'s four ambiguity helpers still reached after the shim's deletion?

`docs/superpowers/specs/2026-10-09-rust-template-backend-design.md` §5 marks four
functions — `django_own_tags`, `django_own_filters`, `effective_autoescape`,
`effective_use_tz` — as *conditional*: they "go only if the shim goes". The shim
went (M18.2) and all four are still present, so the plan's conditional was
resolved the wrong way. But "still present" is a `grep`, and the interesting claim
is the stronger one: **still reached**. That is what this measures.

**Why a counter rather than a call-site audit.** The first draft of that note
named two call sites and called them representative. Two sites out of however many
is an argument, and a call site that exists is not a call site that runs. The
counter is the same cost as the audit — one suite run, ~4 s — and it answers the
question with a number instead of a sample.

**Why it works at all**, i.e. why patching `host`'s attributes is seen by the call
sites: both of them resolve the name through the module each time. `host.py`'s own
site is a module-global lookup, and `engine/base.py`'s site is a
`from django_rust_templates.host import (...)` **inside the filter loop body**, so
it re-binds from the module on every iteration. A patch installed after import is
therefore visible to both. Had either site bound the function at module import
time, this probe would have measured zero for a function that is in fact called,
which is the failure mode to check for first — hence the `effective_*` pair below.

Usage (from the repository root)::

    .venv/bin/python -I .review/m18-measurements/probe_host_helpers.py

`-I` is deliberate: it keeps the interpreter's own directory and the user site off
`sys.path`, so the engine under test is the one the venv points at and nothing
else. `run.py` inserts its own directory for the suite's top-level imports, which
is the one path this needs and `-I` does not take away.

**Recorded transcript, 2026-10-09, `.django/` at 6.0.9, CPython 3.14.4**
(`git` clean at the time of the run):

    Ran 1547 tests in 4.224s

    OK (skipped=6)
    System check identified no issues (0 silenced).
    RC = 0
      django_own_tags        4361
      django_own_filters     2598
      effective_autoescape   2536
      effective_use_tz       2536

The falsifier is a **zero** in any column. The `effective_*` pair being equal is
the probe checking itself: `engine/base.py` reads both unconditionally on adjacent
lines of the same arm, so a difference would mean one wrapper had not landed, and
the two non-zero counts above it would then be suspect too.

**What this does not show.** Reachability *by the pinned suite* is not reachability
by any project. A host call site that no vendored test exercises is invisible here,
and would be invisible in exactly the same way under the old call-site audit — this
instrument narrows the claim ("the suite runs through all four") rather than
proving the plan's conditional was right to keep them.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("suite_run", ROOT / "django_suite" / "run.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import django_rust_templates.host as host  # noqa: E402

NAMES = (
    "django_own_tags",
    "django_own_filters",
    "effective_autoescape",
    "effective_use_tz",
)
counts = {name: 0 for name in NAMES}


def _counting(name, original):
    def counter(*args, **kwargs):
        counts[name] += 1
        return original(*args, **kwargs)

    return counter


for _name in NAMES:
    setattr(host, _name, _counting(_name, getattr(host, _name)))

rc = mod.main(["-v", "0", "template_tests"])
print("RC =", rc)
for _name in NAMES:
    print(f"  {_name:22s} {counts[_name]}")
