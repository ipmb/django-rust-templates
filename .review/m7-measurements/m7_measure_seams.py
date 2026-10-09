"""M7 close-out: the seam inventory, as numbers.

Task 7 Step 1 asks for the seam inventory with each crossing's direction, and
says to take every number from the command that produces it. This is that
command. It states four things and nothing else:

1. the split — how many names Rust compiles, and which names the host does;
2. the wire shape per side, which is how "who compiled this" is read
   structurally (`BlockNode` from Rust, `HostNode` from the host);
3. that the two built-ins Rust still does not own are answered by the
   registry's absence and by no list — the same claim Task 4's D-deletion
   turned on;
4. the divergence M7 closed (a `{% load %}`ed library overriding `block`) and
   the one it did not (A5's forked `__loaded_blocks` counters).

Run as `.venv/bin/python -I .review/m7-measurements/m7_measure_seams.py`.
"""

import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/.django/tests")
sys.path.insert(0, ROOT)

from django.template import Engine, Library  # noqa: E402

from django_rust_templates import _native, _shim  # noqa: E402
from django_rust_templates.host import ShellHost  # noqa: E402
from django_rust_templates.render import parser_for  # noqa: E402

ENGINE = Engine(libraries={"custom": "template_tests.templatetags.custom"})

# **`_shim.install()` must come last, and that is measured.** It rewrites the
# callables Django registered, so `ShellHost`'s identity clause reads "not
# Django's" for every name and the split below reports 0/27 instead of 25/2.
# The split is a property of the seam *before* interposition, which is the
# state M7 ships in; the unshimmed count is the only thing that needs install.

print("--- (1) the split ---")
parser = parser_for("", ENGINE)
host = ShellHost(parser)
rust_side = sorted(name for name in parser.tags if not host.has_tag(name))
host_side = sorted(name for name in parser.tags if host.has_tag(name))
registry = sorted(_native.builtin_tag_names())
print(f"  names in parser.tags: {len(parser.tags)}")
print(f"  Rust compiles: {len(rust_side)}")
print(f"  host compiles: {host_side}")
print(f"  registry == the Rust side: {registry == rust_side}")

print()
print("--- (2) the wire shape, per side ---")
cases = [
    ("rust", "{% block a %}{% endblock %}", True),
    ("rust", '{% extends "base" %}', True),
    ("rust", '{% include "a.html" %}', True),
    ("host", "{% load custom %}{% counter %}", False),
    ("host", "{% load custom %}", False),
    ("host", "{% querystring a=1 %}", False),
]
for side, source, expect_rust in cases:
    nodes, _contains_nontext, _refs = _native.parse(
        source, host=ShellHost(parser_for(source, ENGINE))
    )
    kinds = [node[0] for node in nodes]
    is_rust = bool(kinds) and all(kind != "HostNode" for kind in kinds)
    flag = "ok" if is_rust == expect_rust else "MISMATCH"
    print(f"  {side:4} {source!r:42} -> {kinds}  {flag}")

print()
print("--- (3) the two names the registry answers for ---")
for name in ("load", "querystring"):
    print(f"  {name!r:12} in registry: {name in registry}   "
          f"host compiles: {host.has_tag(name)}")
print("  Neither is named by any list on our side: `has_tag` asks the registry")
print("  first (`host.py`), so a name Rust does not own cannot reach the")
print("  identity clause. That is what makes the clause whole rather than the")
print("  last of three rules.")

print()
print("--- (4) the divergence M7 closed, and the one it did not ---")
lib = Library()
lib.tag("block", lambda parser, token: None)
overriding = parser_for("", ENGINE)
overriding.add_library(lib)
print("  a {% load %}ed library overriding `block`: host compiles "
      f"{ShellHost(overriding).has_tag('block')}  (was False: the override lost)")
print("  A5's fork is not measurable from here — it is a property of which")
print("  parser a `{% block %}` is compiled by, and `m7_measure_nesting.py`")
print("  and `m7_measure_nest2.py` are the scripts that count it (2 nested in a")
print("  host body across the corpus; 1 reached in the pinned suite).")

print()
print("--- (5) the interposition count, which is why install() is last ---")
_shim.install()
print(f"  unshimmed() modules: {len(_shim.unshimmed())}")
print("  M7 adds none: it interposes no new module, so this is M6's figure and")
print("  any movement here is a defect rather than a milestone result.")

