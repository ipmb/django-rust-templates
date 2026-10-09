"""Who imports `django.template.base` / `.smartif`, measured exactly.

The first draft of this script matched any module ending in `.base`, so it
counted `django.db.backends.base` and `django.core.management.base` and reported
259 import sites -- a number about half of Django rather than about `base`. The
metric is "the module is `django.template.base`", and for a relative import
inside `django/template/` it is `level == 1 and module == "base"`. Written down
because 259 looked like an answer.

Derived against `.django/` @ 6.0.9.
"""

import ast
import collections
import pathlib
import sys

DJANGO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".django")


def importer_of(path, module, package="django/template"):
    """(name, count, sample site) for every `from ... import name` of `module`.

    A relative import counts only when the importing file is inside `package`,
    so `from .base import Template` in any other app is not this module.
    """
    names = collections.Counter()
    sites = {}
    for py in path.rglob("*.py"):
        rel = py.relative_to(DJANGO).as_posix()
        try:
            tree = ast.parse(py.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                hit = node.module == f"django.template.{module}"
            else:
                # `from .base import X` (level 1) inside django/template/, or
                # `from ..template.base import X` (level 2) from a sibling app.
                parts = rel.split("/")
                base_dir = "/".join(parts[: -node.level])
                hit = base_dir == package and (node.module or "") == module
            if not hit:
                continue
            for alias in node.names:
                names[alias.name] += 1
                sites.setdefault(alias.name, f"{rel}:{node.lineno}")
    return names, sites


for root, label in (
    (DJANGO / "django", "django/ framework"),
    (DJANGO / "tests", "tests/ suite"),
):
    for module in ("base", "smartif"):
        names, sites = importer_of(root, module)
        print(f"\n=== {label}: from django.template.{module}")
        if not names:
            print("    (none)")
        for n, c in names.most_common():
            print(f"    {c:4}  {n:32} {sites[n]}")
