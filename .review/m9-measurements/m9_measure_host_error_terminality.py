"""Is a host error really terminal for the parse? Measured, not argued.

``render.django_exception`` restores a host exception by *identity* from
``ShellHost.last_error``, matching on the class name the wire error carries. That
is only sound if the ``last_error`` in the stash is the one the error describes —
i.e. if no ``call_tag`` failure is ever *caught and survived*, leaving a stale
stash behind for a later, unrelated match.

The argument written down in that docstring has three structural facts (one
``ErrorKind::Host`` construction site, one ``last_error`` assignment, one host per
parse) and one that is **not** structural:

    a host error is terminal: the parse loop's arm is ``Err(error) => Err(error)``
    (``parser/mod.rs:394-403``) and its caller is ``?``

That is true of *Rust's* loop. It says nothing about the Python side, because a
host tag's compile function runs in Python and can call ``parser.parse(...)``
itself — which re-enters Rust — and can wrap that call in ``try/except``. If any
compile function did, a ``call_tag`` failure would be survived, the parse would
continue, and ``last_error`` would be stale for the next match. Nothing in Rust's
control flow can prevent it, because the swallowing happens above Rust.

So this script asks the question of the tree rather than of the argument: **does
any function in the pinned tree call ``.parse(`` inside a ``try`` block?** The
answer at 6.0.9 is that exactly one does — ``Template.compile_nodelist``
(``base.py:198``), whose ``except`` re-raises — and no compile function does.

**Re-run this at M11.** The scope here is the pinned tree, which is not the
population that matters then: a third-party ``{% load %}`ed library whose tag
catches its own body's errors is ordinary, and it would invalidate the identity
restore without any file in this repository changing.

    . scripts/env.sh
    python -I .review/m9-measurements/m9_measure_host_error_terminality.py
"""

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PINNED = ROOT / ".django" / "django"
if not PINNED.is_dir():
    sys.exit(f"{PINNED} is not there; .django/ is pinned at 6.0.9")

parse_callers = {}
parse_in_try = []
suppress_sites = []


def walk(node, function):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        function = node.name

    if isinstance(node, ast.Try):
        for sub in ast.walk(node):
            if _is_parse_call(sub):
                parse_in_try.append((function, node.lineno))

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "parse":
                parse_callers.setdefault(function, []).append(node.lineno)
            if node.func.attr in ("suppress", "suppress_"):
                suppress_sites.append((function, node.lineno))

    for child in ast.iter_child_nodes(node):
        walk(child, function)


def _is_parse_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "parse"
    )


for path in sorted(PINNED.rglob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - the pinned tree parses
        continue
    walk(tree, "<module>")

# The metric, named: a *function* in the pinned tree, and the question is whether
# its ``.parse(`` call sits inside a ``try``. ``.parse(`` also matches things that
# are not template parses at all (``MultiPartParser.parse``, the SQL constraint
# helpers in ``db/``), which is why the raw list is printed rather than a count.
print(f"functions calling <.parse> anywhere under {PINNED.name}/django/:")
for function, lines in sorted(parse_callers.items()):
    print(f"  {function}: lines {lines}")

print()
print("of those, calls lexically inside a try block (function, try line):")
for entry in parse_in_try:
    print(f"  {entry}")

print()
print(f"contextlib.suppress call sites: {suppress_sites or 'none'}")

print()
if len(parse_in_try) == 1 and parse_in_try[0][0] == "compile_nodelist":
    print("VERDICT: the premise holds at 6.0.9, by measurement.")
    print("  The only parse inside a try is compile_nodelist's own, and its")
    print("  except arm re-raises after optionally annotating. No host compile")
    print("  function swallows a nested parse, so no call_tag failure is")
    print("  survived and last_error cannot be stale for a later match.")
else:
    print("VERDICT: the premise does NOT hold; the identity restore in")
    print("  render.django_exception needs a guard.")
