#!/usr/bin/env python
"""What a rendered `{% block %}` puts in the context, and every name read off it.

**Why this file exists.** M18 §4.1 measured this on Django 6.0.9 and cites its own
probe — `.review/m18-measurements/m18_block_carrier.py`. That probe is not in the
tree: `198fa27` ("clean slate", Pete) removed every `.review/m*-measurements/`
directory, and `bb5de99` restored the two specs the suite reads but not the
probes. So a reader of M18 §4.1, or of M19 §7 question 2, follows a citation to a
file that does not open. The file is recoverable (`git show a214c3a:.review/
m18-measurements/m18_block_carrier.py`), and this probe does not restore it — it
re-measures the same facts against the tree in front of it, and adds the one M18
did not need and M19 does.

M19's question is narrower than M18's. M18 asked *does* inheritance force
materialisation; the answer was no, but one object crosses per rendered block.
M19 asks what a **lazily built** tree — one that has no `BlockNode` to put in the
context — must supply in that object's place. So:

  1. **The census of readers**, by a pure-Python walk of three named trees. If
     nothing outside Django's `loader_tags.py` reads the carrier, then the
     carrier's interface is whatever `{{ block.super }}` resolution touches and
     nothing wider.
  2. **The read trace**: the carrier is replaced, as it is placed, with a proxy
     that records every attribute read and every call, delegating all of them. The
     render's bytes are asserted unchanged, so the recorder is an observer.
  3. **The acceptance suite's exposure**: every `block.super`, every
     `context["block"]` and every other `block.<attribute>` in the suite,
     enumerated — and M18's "38 times across 22 files" re-derived under four
     metrics, because a count is only comparable to a count taken the same way.

Two things about method, because both have bitten this project:

* **`.django/` is gitignored** (`.gitignore:14`), so `git grep` returns zero on
  every search of it and the zero is a property of the tool, not of Django. This
  probe walks the directory with `pathlib` instead, and names the scope it walked.
* The scope of every search below is printed with its result, because "X is not in
  the tree" has no subject to inspect and is only as good as the search behind it.

Run:  .venv/bin/python -I .review/m19-measurements/block_carrier.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

import filter_probe  # noqa: E402

filter_probe.ensure_settings()

from django.template import context as context_mod  # noqa: E402
from django.template import loader_tags  # noqa: E402
from django.template.engine import Engine  # noqa: E402

DJANGO_TREE = ROOT / ".django" / "django"
OURS_TREE = ROOT / "python" / "django_rust_templates"

# **The acceptance suite is `django_suite/template_tests/`**, because that is the
# tree `check.sh:208` runs (`django_suite/run.py … template_tests`), and it is a
# copy of `.django/tests/template_tests/` that `scripts/gen_django_suite.py
# --check` re-derives and diffs on every gate run. The first draft of this probe
# walked `tests/template_tests/`, which **does not exist** — `tests/` is the
# project's own unit-test directory and holds no `template_tests` — and
# `Path.rglob` on a missing directory yields nothing without complaining, so the
# scope printed "(none)" and read as "the acceptance suite never uses
# `block.super`". That is the failure this file's docstring warns about, in this
# file's own first draft, which is why `walk` now refuses a tree it cannot open.
SUITE_TREE = ROOT / "django_suite" / "template_tests"


def walk(tree: Path, needles: list[str], *, suffix: str = ".py") -> list[str]:
    """`path:line | source` for every line of `tree` containing any needle.

    A pure-Python walk rather than a shell search, because the shell tools here
    are not pinned: `grep` is ugrep behind a function while `git grep` is POSIX
    BRE and, worse, searches *tracked* files only — so `git grep BlockNode
    .django/django/` returns zero on all of Django, `.django/` being gitignored at
    `.gitignore:14`, and the zero is a property of the tool rather than of Django.
    A line of source is printed beside its location because the counts that
    summarise a search have been wrong here four different ways, and the
    enumeration settles in one glance what no count does.
    """
    if not tree.is_dir():
        raise SystemExit(
            f"refusing to search {tree}: not a directory. A walk of a tree that "
            f"does not exist prints nothing and reads as an absence."
        )
    hits = []
    for path in sorted(tree.rglob(f"*{suffix}")):
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if any(needle in line for needle in needles):
                hits.append(
                    "%s:%d | %s"
                    % (path.relative_to(ROOT).as_posix(), number, line.strip())
                )
    return hits


def report(
    title: str,
    hits: list[str],
    scope: str,
    tree: Path,
    counted: tuple[str, ...] = ("*.py",),
) -> None:
    """The scope line carries the number of files actually walked, always.

    A scope printed without its size cannot be told from a scope that matched
    nothing because it searched nothing — which is precisely how this probe's first
    draft reported "the acceptance suite never uses `block.super`". `counted` is
    spelled out separately from what was searched, and covers only text files: an
    earlier version walked `*` and died on a `.pyc` under `__pycache__`, having
    counted it as a file it had searched.
    """
    files = sum(1 for s in counted for p in tree.rglob(s) if p.is_file())
    print(f"{title}   [{scope}; {files} files walked: {'+'.join(counted)}]")
    if not hits:
        print("   (none)")
    for hit in hits:
        print(f"   {hit}")
    print()


# --- 1. the census of readers ----------------------------------------------------


print("=" * 100)
print("1. who reads the carrier")
print("=" * 100)
print()

report(
    "1a. `BLOCK_CONTEXT_KEY` and `BlockContext`, Django 6.0.9",
    walk(DJANGO_TREE, ["BLOCK_CONTEXT_KEY", "BlockContext"]),
    f"walked {DJANGO_TREE.relative_to(ROOT)}/**/*.py",
    DJANGO_TREE,
)

report(
    "1b. `BLOCK_CONTEXT_KEY` and `BlockContext`, this package",
    walk(OURS_TREE, ["BLOCK_CONTEXT_KEY", "BlockContext"]),
    f"walked {OURS_TREE.relative_to(ROOT)}/**/*.py",
    OURS_TREE,
)

report(
    "1c. every quoted `\"block\"` in Django 6.0.9 — the wider needle",
    walk(DJANGO_TREE, ['"block"', "'block'"]),
    f"walked {DJANGO_TREE.relative_to(ROOT)}/**/*.py",
    DJANGO_TREE,
)

report(
    "1d. every `BlockNode` in Django 6.0.9, including the other one",
    walk(DJANGO_TREE, ["BlockNode"]),
    f"walked {DJANGO_TREE.relative_to(ROOT)}/**/*.py",
    DJANGO_TREE,
)

report(
    "1e. every quoted `\"block\"` in this package",
    walk(OURS_TREE, ['"block"', "'block'"]),
    f"walked {OURS_TREE.relative_to(ROOT)}/**/*.py",
    OURS_TREE,
)

# --- 2. the acceptance suite's exposure ------------------------------------------


print("=" * 100)
print("2. what the acceptance suite asks of the carrier")
print("=" * 100)
print()

report(
    "2a. `block.super` in the acceptance suite",
    walk(SUITE_TREE, ["block.super"]),
    f"walked {SUITE_TREE.relative_to(ROOT)}/**/*.py",
    SUITE_TREE,
)

report(
    "2b. the context key `\"block\"` in the acceptance suite",
    walk(SUITE_TREE, ['["block"]', "['block']"]),
    f"walked {SUITE_TREE.relative_to(ROOT)}/**/*.py",
    SUITE_TREE,
)

report(
    "2c. `block.super` in the templates the acceptance suite loads",
    walk(SUITE_TREE, ["block.super"], suffix=".html"),
    f"walked {SUITE_TREE.relative_to(ROOT)}/**/*.html",
    SUITE_TREE,
    ("*.html",),
)

# "and nothing else" needs its own search or it is just a confident sentence. Every
# attribute named after `block.` anywhere in the suite, `.py` and `.html`, so that a
# template reaching for `{{ block.name }}` would appear here rather than be assumed
# absent. `{% block content %}` has no dot and cannot collide.
report(
    "2d. every `block.<attribute>` in the acceptance suite, not just `.super`",
    walk(SUITE_TREE, ["block."]) + walk(SUITE_TREE, ["block."], suffix=".html"),
    f"walked {SUITE_TREE.relative_to(ROOT)}/**/*.py and **/*.html",
    SUITE_TREE,
    ("*.py", "*.html"),
)

# --- 3. the read trace -----------------------------------------------------------

TRACE: list[str] = []
DEPTH = [0]


class CarrierProxy:
    """Stands where a `BlockNode` would in the context, recording every read.

    `__getattr__` fires only for names the object does not have, which is the
    point: the proxy has no `super` and no `name`, so every read of either is a
    miss through here. The value it returns is the real node's, so a render under
    the proxy must produce the same bytes as one without it — asserted below.
    """

    def __init__(self, wrapped):
        object.__setattr__(self, "_wrapped", wrapped)

    def __getattr__(self, name):
        TRACE.append("   " * DEPTH[0] + f"read  .{name}")
        value = getattr(object.__getattribute__(self, "_wrapped"), name)
        if not callable(value):
            return value

        def recorded_call(*args, **kwargs):
            TRACE.append(
                "   " * DEPTH[0]
                + f"call  .{name}({len(args)} positional, {len(kwargs)} keyword)"
            )
            return value(*args, **kwargs)

        return recorded_call


REAL_SETITEM = context_mod.ContextDict.__setitem__


def recording_setitem(self, key, value):
    """Swap the carrier for the proxy at the moment it is placed.

    `BlockNode.render` does `context["block"] = <a BlockNode>` and `with
    context.push():` yields a `ContextDict` (`context.py:53-60`), so this is the
    one place the carrier is written and the one seam that catches every one of
    them, including the nested re-entry `{{ block.super }}` makes.
    """
    if isinstance(value, loader_tags.BlockNode):
        TRACE.append(f"   " * DEPTH[0] + f"place context[{key!r}] = {value!r}")
        DEPTH[0] += 1
        value = CarrierProxy(value)
    return REAL_SETITEM(self, key, value)


BASE = """<html>{% block title %}base-title{% endblock %}|{% block content %}base-body{% endblock %}</html>"""
CHILD = """{% extends "base.html" %}
{% block title %}child-title of [{{ block.super }}]{% endblock %}
{% block content %}child-body of [{{ block.super }}]{% endblock %}"""


def render(engine_cls, loader, templates, entry, data):
    """Stock Django's own contract, so the context class is Django's own."""
    engine = engine_cls(loaders=[(loader, templates)], libraries={})
    handle = engine.get_template(entry)
    return handle.render(context_mod.Context(data))


print("=" * 100)
print("3. what is read off the carrier during a real render")
print("=" * 100)
print()
print(f"   base  : {BASE!r}")
print(f"   child : {CHILD!r}")
print()

stock = ("django.template.loaders.locmem.Loader", Engine)
templates = {"base.html": BASE, "page.html": CHILD}
control = render(Engine, stock[0], templates, "page.html", {})

context_mod.ContextDict.__setitem__ = recording_setitem
try:
    probed = render(Engine, stock[0], templates, "page.html", {})
finally:
    context_mod.ContextDict.__setitem__ = REAL_SETITEM

print("   trace, in order:")
for line in TRACE:
    print(f"      {line}")
print()

names: set[str] = set()
calls: set[str] = set()
for line in TRACE:
    parts = line.strip().split()
    if parts and parts[0] == "read":
        names.add(parts[1].lstrip("."))
    elif parts and parts[0] == "call":
        calls.add(parts[1].lstrip(".").split("(", 1)[0])
print(f"   distinct names read   : {sorted(names)}")
print(f"   distinct names called : {sorted(calls)}")
print(
    "   carriers placed       : %d  (one per rendered block, nested: the child's, "
    "then the re-entry `{{ block.super }}` makes)"
    % sum(1 for l in TRACE if "place " in l)
)
print()
print(f"   rendered, unproxied : {control!r}")
print(f"   rendered, proxied   : {probed!r}")
print(
    "   sha256 unproxied %s / proxied %s -> %s"
    % (
        hashlib.sha256(control.encode()).hexdigest()[:12],
        hashlib.sha256(probed.encode()).hexdigest()[:12],
        "IDENTICAL" if control == probed else "DIFFERENT, so the recorder is not an observer",
    )
)
print()

# --- 4. does M18's "38 across 22 files" reproduce? -------------------------------


CHECKOUT_SUITE = ROOT / ".django" / "tests" / "template_tests"

print("=" * 100)
print("4. M18 §4.1 said `block.super` occurs 38 times across 22 files")
print("=" * 100)
print()
print(
    "   A count is only comparable to a count taken the same way, so the metrics are"
    "\n   printed side by side rather than the one that matches: *lines* containing"
    "\n   `block.super` and *occurrences* of it (a line may hold two), over the whole"
    "\n   tree and over its `.py` files alone, in both trees. Django's checkout copy"
    "\n   and the vendored copy the acceptance run uses hold the same templates —"
    "\n   `gen_django_suite.py --check` re-derives the second from the first on every"
    "\n   gate run — so the two rows agreeing is expected, not a second measurement."
)
print()
print("   %-34s %7s %7s %8s   %s" % ("tree", "lines", "occ", "files", "of which .py"))
for label, tree in (
    ("django_suite/template_tests", SUITE_TREE),
    (".django/tests/template_tests", CHECKOUT_SUITE),
):
    if not tree.is_dir():
        print(f"   {label:<34} (not a directory; refused rather than reported as zero)")
        continue
    total_lines = total_occ = 0
    files: set[str] = set()
    py_lines = 0
    py_files: set[str] = set()
    for pattern in ("*.py", "*.html"):
        for path in sorted(tree.rglob(pattern)):
            for line in path.read_text().splitlines():
                if "block.super" in line:
                    total_lines += 1
                    total_occ += line.count("block.super")
                    files.add(path.as_posix())
                    if pattern == "*.py":
                        py_lines += 1
                        py_files.add(path.as_posix())
    print(
        "   %-34s %7d %7d %8d   %d lines / %d files"
        % (label, total_lines, total_occ, len(files), py_lines, len(py_files))
    )
print()
