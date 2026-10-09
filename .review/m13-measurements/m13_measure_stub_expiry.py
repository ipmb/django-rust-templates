"""Which rows stop meaning what they mean the moment `partial`/`partialdef`
stop being stubs — asked as a **mutation**, not as arithmetic.

The rows that break under M13 are the ones whose subject is the stub. Reading
them tells you which rows *name* the pair; it does not tell you which rows
*rest on* it. The mutation does: `ShellHost._rust_owned` subtracts
`builtin_tag_stub_names()` (``host.py:471-474``), so reporting that set as empty
puts both names back on Rust's side of the host predicate. The parse then routes
to the registry, whose bodies for the pair still return `Unimplemented` — the
*other* half of what M13 changes, and enough to expose every row that depends on
the route.

    as shipped   -> the suite is green
    mutated      -> whatever rests on the route goes red

**That table expired the moment Task 4 landed, and the script now refuses to
pretend otherwise.** The mutation is `builtin_tag_stub_names()` returning `[]`;
Task 4 emptied the real set, so the mutated run and the shipped run return the
*same value* and the "as shipped / mutated" contrast above is two spellings of
one run. Measured: ``MUTATED: builtin_tag_stub_names() -> [] (real set [])``,
``Ran 1547 tests … OK (skipped=6)`` — green on both, which is the shape of a
working instrument and of a dead one. The ``_calls["n"] == 0`` guard below does
**not** catch this: the function is still consulted (4,361 times on the run that
expired it), it simply has nothing to change. So the guard is now on the
mutation's *teeth* rather than on its reach, and the exit is before the suite
starts, because 1,547 tests that cannot distinguish two runs are not a
measurement.

What did the work instead, and is the reason the expiry is worth recording: the
**unmutated** run. It was red — 27 errors — for a reason that has nothing to do
with stubs and everything to do with the route the stubs were hiding, and the
cause is in the plan's amendment.

**The trap this script fell into on its first run, kept because it is the one
this project keeps paying for.** It reported `OK (skipped=6)` for a suite that
never installed the engine: `sitecustomize` installs only when
``DJANGO_RUST_TEMPLATES=1`` (`scripts/shimpath/sitecustomize.py`), and the
re-exec below did not set it, so the mutation was inert and the run measured
**stock Django**. Two guards were added then and both are asserted, not assumed:
the env var is set, and `_shim.installed()` is checked before the runner starts.
The expiry guard below is the third of that family — a green suite, a working
instrument and a live mutation are three different claims.

Run it as::

    .venv/bin/python .review/m13-measurements/m13_measure_stub_expiry.py
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _reinstall_under_the_shim():
    if os.environ.get("_M13_EXPIRY_CHILD") == "1":
        return
    env = dict(os.environ)
    env["_M13_EXPIRY_CHILD"] = "1"
    env["DJANGO_RUST_TEMPLATES"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "scripts" / "shimpath"), str(ROOT / ".django"), str(ROOT)]
    )
    os.execve(sys.executable, [sys.executable, __file__], env)


_reinstall_under_the_shim()

from django_rust_templates import _native, _shim  # noqa: E402

if not _shim.installed():
    sys.exit(
        "refusing to measure: the shim is not installed, so this run would test "
        "stock Django and the mutation would be invisible"
    )

MUTATE = os.environ.get("_M13_EXPIRY_MUTATE") == "1"

if MUTATE:
    _calls = {"n": 0}
    _real = _native.builtin_tag_stub_names
    _real_set = _real()

    def _stub_names_empty():
        _calls["n"] += 1
        return []

    _native.builtin_tag_stub_names = _stub_names_empty
    print(
        f"MUTATED: builtin_tag_stub_names() -> [] (real set {_real_set})",
        file=sys.stderr,
    )
    if not _real_set:
        # The guard on the mutation's *teeth*, where the one at the bottom of
        # this file guards its reach. `[] -> []` is a no-op, so every row this
        # run would see is the row the shipped run sees, and a green result
        # would be read as "nothing rests on the route" when it means "this
        # instrument stopped measuring". Task 4 emptied the set; see the
        # docstring. Exit here rather than after the suite: the suite cannot
        # tell the two runs apart, so running it is 1,547 tests of nothing.
        sys.exit(
            "refusing to measure: the mutation has expired. "
            "builtin_tag_stub_names() is already empty, so `[] -> []` changes "
            "nothing and this run cannot be distinguished from the shipped one. "
            "Re-point the mutation at whatever the host predicate consults now "
            "(`ShellHost._rust_owned`), or retire this instrument."
        )

checkout_tests = ROOT / ".django" / "tests"
sys.path.insert(0, str(checkout_tests))
os.chdir(checkout_tests)
sys.argv = ["runtests.py", "template_tests", "--parallel", "1"]

try:
    runpy.run_path(str(checkout_tests / "runtests.py"), run_name="__main__")
except SystemExit:
    pass

if MUTATE:
    print(f"the mutated function was consulted {_calls['n']} time(s)", file=sys.stderr)
    if _calls["n"] == 0:
        sys.exit("the mutation was never consulted; this run measured nothing")
