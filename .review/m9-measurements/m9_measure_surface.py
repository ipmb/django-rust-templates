"""The two modules M9 interposes: what they contain and who reaches into them.

`unshimmed()` compares file existence, so the only thing that decides M9's
progress number is that `_impl/base.py` and `_impl/smartif.py` exist and are
complete. This script measures the *completeness* side: every top-level symbol
each module defines, and every name the rest of the tree imports from them.

Derived against `.django/` @ 6.0.9.
"""

import ast
import collections
import pathlib
import sys

DJANGO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".django")
ROOT = DJANGO / "django" / "template"
OURS = pathlib.Path("python/django_rust_templates")


def symbols(path):
    tree = ast.parse(path.read_text())
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.Assign):
            out.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append(node.target.id)
    return out


def from_imports(path, module):
    """Names imported as `from <module> import a, b` anywhere in `path`."""
    names = collections.Counter()
    sites = []
    for py in path.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == module or mod.endswith("." + module):
                    for alias in node.names:
                        names[alias.name] += 1
                        sites.append((str(py), node.lineno, alias.name))
    return names, sites


for name in ("base.py", "smartif.py", "context.py"):
    p = ROOT / name
    if not p.is_file():
        print(f"{name}: MISSING")
        continue
    text = p.read_text()
    syms = symbols(p)
    print(f"\n=== {name}: {len(text.splitlines())} lines, {len(syms)} top-level symbols")
    print("   ", ", ".join(syms))

print("\n=== who imports from django.template.base")
names, sites = from_imports(DJANGO / "django", "base")
print(f"    {len(sites)} import sites in .django/django, {len(names)} distinct names")
for n, c in names.most_common():
    print(f"      {c:4}  {n}")

print("\n=== who imports from django.template.smartif")
names, sites = from_imports(DJANGO / "django", "smartif")
print(f"    {len(sites)} import sites in .django/django, {len(names)} distinct names")
for n, c in names.most_common():
    print(f"      {c:4}  {n}")

print("\n=== our own tree importing from django.template.base / smartif")
names, sites = from_imports(OURS, "base")
print(f"    base:    {len(sites)} sites, {len(names)} distinct names")
for n, c in names.most_common():
    print(f"      {c:4}  {n}")
names, sites = from_imports(OURS, "smartif")
print(f"    smartif: {len(sites)} sites, {len(names)} distinct names")
