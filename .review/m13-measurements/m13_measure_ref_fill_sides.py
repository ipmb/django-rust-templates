"""Which reference-table entries the *host* fills, and which the materialiser does.

Task 5's hypothesis is that `FilterCheckHost::ref_list` builds a fresh Python list
per read (`lib.rs:1257`), so a partial body materialised **mid-parse** writes into
a copy, and the outer materialisation then finds the slot empty and builds a
second node. Which rows can see that depends on a fact about the table, not on a
preference about test style: an entry filled by the *host during the parse* is
already non-`None` in **every** list built afterwards, so no copy can lose it,
while an entry the *materialiser* fills is `None` until something builds it —
and that is the entry a discarded copy loses.

The two kinds are distinct on the wire, and our own source says so:

  * `_impl/parser.py:726-731` — "a `NodeRef` entry is filled by the host during
    the parse, a `NodeSlot` entry by the materialiser as it builds";
  * `lib.rs:1404-1406` — `call_tag` pushes the host's node into `self.refs` and
    returns a `CoreNodeRef` to it, which is the `NodeRef` arm;
  * `alloc_slot` reserves `None` and `materialise_node` fills it on the way out
    (`_impl/parser.py:404-407`), which is the `NodeSlot` arm.

So the instrument parses a source with one of each — a library tag (host-built)
and a named `{% cycle %}` (materialiser-built) — prints the returned table
**before any materialiser has run**, then runs the materialiser and prints it
again. The interesting column is the first one.

Run it with the shim installed, which is what puts our engine behind
`django.template.*`:

    PYTHONPATH=scripts/shimpath:.django DJANGO_RUST_TEMPLATES=1 \
        .venv/bin/python .review/m13-measurements/m13_measure_ref_fill_sides.py

Pinned tree and interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT, ROOT / ".django", ROOT / ".django" / "tests"):
    sys.path.insert(0, str(_path))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(TEMPLATES=[], INSTALLED_APPS=[], DEBUG=True)
django.setup()

from django.template import Engine  # noqa: E402

from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates import _shim  # noqa: E402
from django_rust_templates import host as host_module  # noqa: E402

if not _shim.installed():
    sys.exit("the shim is not installed: this run would measure stock Django")

# A library tag — host-built, so a `NodeRef` — and a named cycle, which reserves
# a slot the materialiser fills. `{% load %}` first so `has_tag` answers True and
# `call_tag` is the path taken.
SOURCE = (
    "{% load custom %}{% counter %}"
    "{% cycle 'a' 'b' as c %}{% cycle c %}"
)

engine = Engine(libraries={"custom": "template_tests.templatetags.custom"})
host = host_module.ShellHost(render.parser_for("", engine))

nodes, contains_nontext, refs = _native.parse(SOURCE, host=host)


def describe(descs, indent=0):
    """One line per descriptor: its class, its slot, and any node it points at."""
    for desc in descs:
        class_name, attrs, nodelists, _token, *rest = desc
        # The slot element is present-but-`None` on descriptors that have none,
        # which is the wire's own way of saying so rather than a shorter list.
        slot = ""
        if rest and rest[0] is not None:
            slot = f" slot={rest[0].index}"
        held = ""
        for name, value in attrs:
            if isinstance(value, _native.NodeRef):
                held = f" NodeRef({value.index}) -> {_shown(refs[value.index])}"
        print(f"  {'  ' * indent}{class_name}{slot}{held}")
        for _name, child in nodelists:
            describe(child, indent + 1)


def _shown(value):
    if value is None:
        return "None"
    return f"{type(value).__name__} at {id(value):#x}"


print(f"django {django.get_version()} on {sys.version.split()[0]}")
print(f"refs table, as `_native.parse` returned it ({len(refs)} entries):")
for index, value in enumerate(refs):
    print(f"  [{index}] {_shown(value)}")
print("\ndescriptors:")
describe(nodes)

print("\n-- before the materialiser runs --")
host_filled = [
    i for i, value in enumerate(refs) if value is not None
]
print(f"  non-None entries: {host_filled}")

tree = render.materialiser().materialise(
    (nodes, contains_nontext, refs),
    resolve_ref=refs.__getitem__,
    register_ref=refs.__setitem__,
    parser=host.parser,
)

print("\n-- after the materialiser runs --")
for index, value in enumerate(refs):
    print(f"  [{index}] {_shown(value)}")
print("\nthe materialised tree, and whether it *is* the table's entry:")
for node in tree:
    print(f"  {type(node).__name__:<12} at {id(node):#x}")
for index, value in enumerate(refs):
    if value is None:
        continue
    same = [id(node) == id(value) for node in tree]
    print(f"  refs[{index}] is a tree node: {any(same)}")

print(
    "\nwhat this says about Task 5's candidate rows: an entry in the first list "
    "above is filled before any partial body could be materialised, so a copy "
    "taken at registration keeps it; only an entry that is None there can be "
    "lost by a snapshot."
)
print(f"  contains_nontext={contains_nontext}")
