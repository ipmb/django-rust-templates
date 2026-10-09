"""What is actually in the five `django.templatetags` libraries M12 owns?

M12's counts have been quoted from a grep three times in this project's history
(`i18n 616, l10n 60, static 179, tz 200, cache 100` lines; "18 registered tag
names, 9 filters"; "15 more node classes" in the spec), and every one of those
is a count of *something* — lines are not classes are not registry entries. This
instrument prints each question separately so the plan can cite which one it
means.

Prints, per module:
  * file line count (`wc -l` equivalent, via `len(read_text().splitlines())`)
  * classes defined in the file, and how many of them are `Node` subclasses
  * module-level `def`s and decorators registered onto `register`
  * what the module imports from `django.*` outside `django.templatetags`
    (the delegation surface — anything here is machinery M12 does not port)
  * every entry in `register.tags` / `register.filters`

Run with `.django` on the path but **without** the shim: this is a census of the
pinned tree, and the question is what Django's files contain, not what our tree
serves. `-I` is fine here for the same reason — there is no shim install to lose.

    . scripts/env.sh
    .venv/bin/python -I .review/m12-measurements/m12_measure_library_census.py
"""

import ast
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PINNED = ROOT / ".django" / "django" / "templatetags"

LIBS = ["i18n", "l10n", "static", "tz", "cache"]


def module_report(name):
    path = PINNED / f"{name}.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    node_classes = []
    for cls in classes:
        for base in cls.bases:
            spelling = (
                base.id
                if isinstance(base, ast.Name)
                else base.attr
                if isinstance(base, ast.Attribute)
                else ""
            )
            if spelling in ("Node", "NodeList"):
                node_classes.append(cls.name)
                break

    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    foreign = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if not node.module.startswith("django.templatetags"):
                foreign[node.module] += len(node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("django.templatetags"):
                    foreign[alias.name] += 1

    return {
        "name": name,
        "path": path,
        "lines": len(source.splitlines()),
        "classes": [c.name for c in classes],
        "node_classes": node_classes,
        "defs": [d.name for d in defs],
        "foreign": foreign,
    }


def main():
    print(f"pinned tree: {PINNED}")
    print()
    total_lines = total_classes = total_nodes = total_defs = 0
    for name in LIBS:
        r = module_report(name)
        total_lines += r["lines"]
        total_classes += len(r["classes"])
        total_nodes += len(r["node_classes"])
        total_defs += len(r["defs"])
        print(f"== {name}.py  {r['lines']} line(s)")
        print(f"   classes      {len(r['classes'])}: {', '.join(r['classes'])}")
        print(f"   Node subcls  {len(r['node_classes'])}: {', '.join(r['node_classes'])}")
        print(f"   module defs  {len(r['defs'])}: {', '.join(r['defs'])}")
        print("   imports outside django.templatetags:")
        for mod, count in sorted(r["foreign"].items()):
            print(f"     {mod}  ({count} name(s))")
        print()

    print("=== totals")
    print(f"  lines       {total_lines}")
    print(f"  classes     {total_classes}")
    print(f"  Node subcls {total_nodes}")
    print(f"  module defs {total_defs}")
    print()

    # The registry, read from the pinned modules rather than from a grep.
    sys.path.insert(0, str(ROOT / ".django"))
    from django.conf import settings

    if not settings.configured:
        settings.configure(USE_I18N=True, USE_TZ=True, INSTALLED_APPS=[])
    import django

    django.setup()

    tags = filters = 0
    print("=== registry (pinned modules, imported)")
    for name in LIBS:
        module = __import__(f"django.templatetags.{name}", fromlist=["x"])
        t = module.register.tags
        f = module.register.filters
        tags += len(t)
        filters += len(f)
        print(f"  {name:<8} tags={len(t):<3} {sorted(t)}")
        print(f"  {'':<8} filters={len(f):<3} {sorted(f)}")
    print(f"  --- {tags} registered tag name(s), {filters} filter(s)")


if __name__ == "__main__":
    main()
