"""The two modules M9 interposes: what they contain and who reaches into them.

``unshimmed()`` compares file existence, so the only thing that decides M9's
progress number is that ``_impl/base.py`` and ``_impl/smartif.py`` exist and are
complete. This script measures the *completeness* side: every top-level symbol
each module defines, and every name the rest of the tree imports from them.

Why the importer half is written the long way
---------------------------------------------

A first draft matched any module whose dotted name *ended* in ``.base``. That
is not the same question, and the answer it gave — 259 sites in ``django/``,
about half the framework — was the tell: ``django.db.models.base``,
``django.core.management.base`` and ``django.forms.forms.BaseForm``'s
``django.forms.forms`` all match that predicate and none of them import
``django.template.base``. The corrected predicate resolves **relative** imports
against the importing file's own package and requires the absolute name to be
exactly ``django.template.<module>``.

The wrong number is kept in mind rather than deleted, because the two figures
differ by an order of magnitude and a reader who has seen 259 quoted anywhere
should be able to find out why.

Derived against ``.django/`` @ 6.0.9.
"""

import ast
import collections
import pathlib
import sys

DJANGO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".django")
ROOT = DJANGO / "django" / "template"
OURS = pathlib.Path("python/django_rust_templates")
FRAMEWORK = DJANGO / "django"


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


def dotted_name(path, root, prefix):
    """``django/template/base.py`` under ``.django/django`` → ``django.template.base``.

    ``prefix`` is what ``relative_to(root)`` throws away: the root directory's
    own package name. Without it the name comes back as ``template.base``, and a
    ``from .base import X`` inside ``django/template/library.py`` then resolves to
    ``template.base``, matches nothing, and disappears from the count — which is
    what a first version of this function did, reporting zero importers of
    ``smartif`` in a tree where ``defaulttags.py:43`` imports it.
    """
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return prefix + ".".join(parts)


def from_imports(path, module, root, prefix):
    """Imports of exactly ``django.template.<module>`` from anywhere under ``path``.

    Relative imports are resolved against the importing file's package, because
    ``django/template/library.py`` writing ``from .base import X`` is a claim
    about ``django.template.base`` and nothing in the syntax says so.
    """
    target = f"django.template.{module}"
    names = collections.Counter()
    sites = []
    for py in path.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        package = dotted_name(py, root, prefix).rsplit(".", 1)[0]
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                parts = package.split(".")
                base = parts[: len(parts) - (node.level - 1)]
                resolved = ".".join(base + ([node.module] if node.module else []))
            else:
                resolved = node.module or ""
            if resolved != target:
                continue
            for alias in node.names:
                names[alias.name] += 1
                sites.append((str(py), node.lineno, alias.name))
    return names, sites


def report(root, module, label, prefix):
    names, sites = from_imports(root, module, root, prefix)
    print(f"\n=== {label}: {len(sites)} import site(s), {len(names)} distinct name(s)")
    for name, count in names.most_common():
        print(f"      {count:4}  {name}")
    return sites


for name in ("base.py", "smartif.py", "context.py"):
    path = ROOT / name
    if not path.is_file():
        print(f"{name}: MISSING")
        continue
    text = path.read_text()
    syms = symbols(path)
    print(
        f"\n=== {name}: {len(text.splitlines())} lines, "
        f"{len(syms)} top-level symbols"
    )
    print("   ", ", ".join(syms))

report(FRAMEWORK, "base", "framework modules importing django.template.base", "django.")
report(FRAMEWORK, "smartif", "framework modules importing django.template.smartif", "django.")
# ``.django/tests``, not ``.django/django/tests``: the suite is a sibling of the
# package, and pointing this at the package's own directory reports zero suite
# importers — a number that looks like a fact and is an addressing mistake.
report(DJANGO / "tests", "base", "the pinned suite importing django.template.base", "")
report(DJANGO / "tests", "smartif", "the pinned suite importing django.template.smartif", "")
report(OURS, "base", "our own tree importing django.template.base", "")
report(OURS, "smartif", "our own tree importing django.template.smartif", "")
