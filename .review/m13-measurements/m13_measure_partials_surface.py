"""The `partial`/`partialdef` surface, pinned: definitions, spans, and where the
engine reads them.

Four questions, because the answers live in four places and a plan that reads
only one of them will miss a piece:

* what `defaulttags.py` holds for the two tags — the nodes and the two compile
  functions, with spans;
* what `Parser`/`Template` carry between them — `extra_data`, which is the
  state the tags share and which `engine.py` reads for a `name#partial` lookup;
* which of our own files already touch that state, i.e. what M13 inherits;
* **whether our materialiser already carries the partial node classes** — the
  question whose answer constrains how M13 can place them. The last section
  imports `_impl/parser.py` and prints `NODE_CLASSES`' size and its partial
  entries; a plan that assumes "the node class is already there" without it will
  plan the wrong seam.

Run under `-I` **on purpose** — the question is what the *pinned* file says, and
the last section reads our own source file rather than the running engine.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = ROOT / ".django"
sys.path.insert(0, str(CHECKOUT))

WANTED = {"PartialTemplate", "PartialDefNode", "PartialNode", "DeferredSubDict",
          "partialdef_func", "partial_func"}


def spans(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in WANTED:
            yield node.name, node.lineno, node.end_lineno


def find_defs(path: Path):
    tree = ast.parse(path.read_text())
    return {
        n.name: (n.lineno, n.end_lineno)
        for n in ast.walk(tree)
        if isinstance(n, (ast.ClassDef, ast.FunctionDef))
    }


print("== .django/django/template/defaulttags.py ==")
for name, start, end in sorted(spans(CHECKOUT / "django/template/defaulttags.py"),
                               key=lambda x: x[1]):
    print(f"  {name:<18} :{start}-{end}")

print()
print("== .django/django/template/base.py ==")
base = CHECKOUT / "django/template/base.py"
defs = find_defs(base)
for name in ("PartialTemplate",):
    start, end = defs[name]
    print(f"  {name:<18} :{start}-{end}")
src = base.read_text().splitlines()
for i, line in enumerate(src, start=1):
    if "extra_data" in line:
        print(f"  extra_data        :{i}  {line.strip()}")

print()
print("== .django/django/template/engine.py ==")
eng = CHECKOUT / "django/template/engine.py"
for i, line in enumerate(eng.read_text().splitlines(), start=1):
    if "partial" in line or "extra_data" in line:
        print(f"  :{i}  {line.strip()}")

print()
print("== our tree: who already touches the same state ==")
for path in sorted((ROOT / "python").rglob("*.py")):
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if "extra_data" in line or "PartialTemplate" in line:
            print(f"  {path.relative_to(ROOT)}:{i}  {line.strip()}")

print()
print("== our tree: does NODE_CLASSES carry a partial node? ==")
# Read the table out of the *source* rather than importing the package: the
# question is what the file declares, and an import would also bring up Django
# and the whole `_impl` chain for a fact that is a dict literal.
parser_src = (ROOT / "python/django_rust_templates/_impl/parser.py").read_text()
parser_tree = ast.parse(parser_src)
node_classes = None
for node in ast.walk(parser_tree):
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "NODE_CLASSES" for t in node.targets
    ):
        node_classes = node.value
if node_classes is None:
    print("  NODE_CLASSES: not found (the table may have been renamed)")
else:
    keys = []
    if isinstance(node_classes, ast.Dict):
        for key in node_classes.keys:
            if isinstance(key, ast.Constant):
                keys.append(key.value)
    print(f"  NODE_CLASSES keys: {len(keys)}")
    partial = [k for k in keys if "artial" in str(k)]
    print(f"  partial ones: {partial}")
    # The positive control: a filter that matched nothing anywhere would report
    # the same `[]` for a table that lost its entries.
    print(f"  control (keys containing 'Block'): "
          f"{[k for k in keys if 'Block' in str(k)]}")
