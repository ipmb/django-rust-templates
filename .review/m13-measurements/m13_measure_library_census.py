"""What is actually in `django/template/library.py`, M13's last engine module.

Four separate questions, because they have four answers and this project has
mis-quoted one for another before: file lines, top-level classes, module-level
`def`s, and — the one that matters for a *delegating* module — how many of those
`def`s are the `Library` methods a `{% load %}`ed tag library reaches.

Run under `-I` **on purpose**: the question is what the pinned file says, and
under the shim `django.template.library` is our module. `-I` implies `-s -E`, so
`PYTHONPATH` is ignored and we must add the checkout ourselves.

    .venv/bin/python -I .review/m13-measurements/m13_measure_library_census.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = ROOT / ".django"
sys.path.insert(0, str(CHECKOUT))

TARGET = CHECKOUT / "django" / "template" / "library.py"

source = TARGET.read_text()
tree = ast.parse(source)

# `splitlines()`, not `count("\n") + 1`: the file ends with a newline, so the
# arithmetic form reports one line that does not exist. Both are printed by the
# caller below rather than one being trusted.
lines = len(source.splitlines())
newline_count = source.count("\n")
classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

print(f"file            {TARGET.relative_to(CHECKOUT)}")
print(f"lines           {lines} (splitlines; newline count {newline_count})")
print(f"top-level classes  {len(classes)}")
print(f"module-level defs  {len(funcs)}")
print()

print("classes:")
for node in classes:
    methods = [
        m.name
        for m in node.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    print(f"  {node.name:<18} :{node.lineno}-{node.end_lineno}  methods={methods}")
print()

print("module-level defs:")
for node in funcs:
    args = [a.arg for a in node.args.args]
    print(f"  {node.name:<18} :{node.lineno}-{node.end_lineno}  args={args}")
