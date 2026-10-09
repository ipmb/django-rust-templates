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

**The population is import-bounded, and the number is only meaningful with its
imports named.** `Node.__subclasses__` is transitive but not global, so a class
in a module nobody imported is not in the list. `base` + `defaulttags` +
`loader_tags` — and everything those import, which includes `library` — is
**34**; adding the tag libraries (`cache`, `i18n`, `static`, `tz`, `l10n`) makes
it **44**, and the ten extra are the i18n and cache nodes. `IfNode` is the only
property either way, so the verdict is stable under the widening — but "34
subclasses in the pinned tree" is a bound stated as a total, and this line is the
correction.

**The third shape is the one the first two categories cannot hold**: a class that
*stores* a nodelist it does not declare. `SimpleBlockNode` (`library.py:334`)
sets `self.nodelist = nodelist` (`:337`) and inherits `child_nodelists = ()` from
`SimpleNode` (`:317`), so Django does **not** descend into a `simple_block_tag`'s
body — the inverse of `IfNode`. The `PROPERTY`/class-attr/instance-attr/absent
classification is blind to it, because it only classifies the names a class
*declares*; a class that declares `()` has no names to classify. Hence the second
pass below.

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


# `__subclasses__` is transitive but not *global*: the population is whatever has
# been imported. Measured here rather than asserted, because the two numbers are
# both right and the difference is the whole of the sentence `desc.rs` had to
# correct — 34 from the three modules above, 44 once the tag libraries are in.
NARROW = len(subclasses(base.Node))

import importlib  # noqa: E402

for _name in (
    "django.templatetags.cache",
    "django.templatetags.i18n",
    "django.templatetags.static",
    "django.templatetags.tz",
    "django.templatetags.l10n",
    "django.template.library",
):
    _module = importlib.import_module(_name)
    SOURCES[_name] = Path(_module.__file__).read_text(encoding="utf-8")

WIDE = len(subclasses(base.Node))


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
print(f"population: {NARROW} with base+defaulttags+loader_tags, {WIDE} after "
      f"importing the tag libraries — both correct, and the difference is imports")

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


# --- the third shape -------------------------------------------------------
#
# A class that *stores* a nodelist it does not declare. The pass above cannot
# see one, because it classifies only the names in `child_nodelists` — and this
# shape is exactly "a name that is not there". Django's walk declines to descend
# into it (`child_nodelists` is what it iterates), so the question it answers is
# the *inverse* of `IfNode`'s and it is invisible to the same table.
def stored_nodelists(cls):
    """Every `self.<attr> = <value>` in `cls`'s own `__init__` whose name says nodelist."""
    init = cls.__dict__.get("__init__", cls.__init__)
    try:
        body = inspect.getsource(init)
    except (OSError, TypeError):
        return []
    return [
        (attr, value)
        for attr, value in re.findall(r"self\.(\w+)\s*=\s*(\w+)", body)
        if "nodelist" in attr
    ]


print()
undeclared = []
for cls in subclasses(base.Node):
    names = getattr(cls, "child_nodelists", None) or ()
    for attr, value in stored_nodelists(cls):
        if attr not in names:
            undeclared.append((cls.__name__, attr, value, repr(getattr(cls, "child_nodelists", None))))

for cls_name, attr, value, declared in sorted(undeclared):
    print(f"{cls_name:<24} stores self.{attr} = {value}, declares child_nodelists = {declared}")
print(f"{len(undeclared)} class(es) store a nodelist they do not declare — "
      "Django's walk does not descend into these, and neither does a walk "
      "driven by a descriptor's nodelists.")
print("`IfNode` shows the same split from the other side: it stores "
      "`conditions_nodelists` and declares `nodelist`, which it synthesises.")

