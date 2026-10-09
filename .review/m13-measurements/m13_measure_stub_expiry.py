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

**The trap this script fell into on its first run, written down because it is
the one this project keeps paying for: it reported `OK (skipped=6)` for a suite
that never installed the engine.** `sitecustomize` installs only when
``DJANGO_RUST_TEMPLATES=1`` (`scripts/shimpath/sitecustomize.py`), and the
re-exec below did not set it, so the mutation was inert and the run measured
**stock Django**. Two guards were added and both are asserted, not assumed: the
env var is set, and `_shim.installed()` is checked before the runner starts.
A green suite and a working instrument are different claims.

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

    def _stub_names_empty():
        _calls["n"] += 1
        return []

    _native.builtin_tag_stub_names = _stub_names_empty
    print(
        f"MUTATED: builtin_tag_stub_names() -> [] (real set {_real()})",
        file=sys.stderr,
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
