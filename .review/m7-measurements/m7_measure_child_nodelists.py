"""Which `Node` subclasses answer `child_nodelists` from a stored attribute?

Task 3's walk descends every nodelist a descriptor carries, and claims that
equals Django's `child_nodelists` walk for every node Rust builds. The claim
rests on a shape the reviewer asked to have swept rather than sampled: for each
class, is `child_nodelists` a plain class attribute with a matching instance
attribute, or does the name it lists resolve to a **property** (or to an
attribute of another name, or to nothing at all)?

`IfNode` is the shape that matters — it inherits `("nodelist",)` and supplies
`nodelist` as a property over `conditions_nodelists` — so a walk driven by
*stored attributes* misses it. Rust instead descends whatever it emitted, which
is a different question, and this script is what turns "the same set" from an
assertion into a count.

Run: .venv/bin/python -I .review/m7-measurements/m7_measure_child_nodelists.py
"""

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DJANGO = ROOT / ".django"
sys.path.insert(0, str(DJANGO))
sys.path.insert(0, str(ROOT / "tests"))

import django.template.base as base  # noqa: E402
import django.template.defaulttags as defaulttags  # noqa: E402
import django.template.loader_tags as loader_tags  # noqa: E402

MODULES = (base, defaulttags, loader_tags)
SOURCES = {m.__name__: Path(m.__file__).read_text(encoding="utf-8") for m in MODULES}


def subclasses(root):
    """Every class under `root`, transitively, in definition order."""
    seen, stack = [], [root]
    while stack:
        cls = stack.pop(0)
        for sub in cls.__subclasses__():
            if sub not in seen:
                seen.append(sub)
                stack.append(sub)
    return seen


def is_property(cls, attr):
    """`attr` on `cls` is a `property`, found in its own or a base's `__dict__`."""
    for klass in cls.__mro__:
        if attr in klass.__dict__:
            return isinstance(klass.__dict__[attr], property)
    return False


def kind_of(cls, attr):
    """Where `attr` comes from, which is the distinction the walk turns on.

    Three answers, and only the first is a divergence: a **property** is
    synthesised and has no stored attribute behind it, so a walk driven by
    "descend the child nodelists I stored" cannot see it. A class attribute and
    an instance attribute set in `__init__` are the same thing to any walk that
    reads `getattr`, which is what Django's does.
    """
    for klass in cls.__mro__:
        if attr in klass.__dict__:
            return "PROPERTY" if isinstance(klass.__dict__[attr], property) else "class attr"
    init = cls.__dict__.get("__init__", cls.__init__)
    try:
        body = inspect.getsource(init)
    except (OSError, TypeError):
        body = ""
    if re.search(rf"self\.{re.escape(attr)}\s*=", body):
        return "instance attr"
    return "absent"


def defined_in(cls):
    """The module and first line of `cls`'s own `class` statement."""
    module = cls.__module__
    source = SOURCES.get(module)
    if source is None:
        return module, None
    for number, line in enumerate(source.splitlines(), start=1):
        if re.match(rf"class {re.escape(cls.__name__)}\b", line):
            return module, number
    return module, None


print(f"{'class':<24} {'child_nodelists':<34} {'resolves to':<30} where")
print("-" * 108)

rows = []
for cls in subclasses(base.Node):
    names = getattr(cls, "child_nodelists", None)
    if names is None:
        kind = "<None: declares none>"
    else:
        kind = ",".join(f"{name}:{kind_of(cls, name)}" for name in names) or "<empty tuple>"
    module, line = defined_in(cls)
    short = module.rsplit(".", 1)[-1]
    rows.append((cls.__name__, repr(names), kind, f"{short}:{line}"))

for name, declared, kind, where in sorted(rows):
    print(f"{name:<24} {declared:<34} {kind:<30} {where}")

properties = [r for r in rows if "PROPERTY" in r[2]]
print("-" * 108)
print(f"{len(rows)} Node subclass(es); {len(properties)} whose child_nodelists "
      f"names a property: {[r[0] for r in properties]}")
print("A property is the divergence: it has no stored attribute for a "
      "name-driven walk to find.")
