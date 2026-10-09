"""What `{% cycle %}` does across a `{% partial %}` boundary, on the pinned tree.

M13's Task 5 was written on an unmeasured premise: that a partial body naming an
outer `{% cycle %}` would, under a snapshot reference table, build a *second*
`CycleNode`, and that the two nodes' independent state would show up in the
rendered string. Three facts had to be measured before that could stand:

* **the expected value**, because a row's expectation is a claim like any other.
  The first draft said `"aa"`; the truth is `"aaa"`;
* **whether the two mentions are one node at all** — Django's `cycle()` returns
  `parser._named_cycle_nodes[name]` for the one-argument form, so they are;
* **whether `CycleNode`'s position survives a partial render**, which is where the
  premise actually breaks: `PartialNode.render` calls into `PartialTemplate`,
  whose `render` does `context.render_context.push_state(self)`
  (`base.py:340-346`), so each partial render gets a fresh layer and the named
  cycle **restarts**. That is why the output is `"aaa"` and not the `"aba"` a
  single monotonically-advancing node would give.

So the snapshot cannot be distinguished from a live table by this shape's
*output*, and Task 5's change is conditional on its own row failing rather than
on the reasoning that motivated it. This instrument is what settles that; run it
in both directions rather than quoting the numbers below.

Pinned tree and pinned interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4. Run under `-I` **plus** an explicit `sys.path` insert
(memory: an unshimmed run needs both halves), with `DJANGO_RUST_TEMPLATES=0`.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".django"))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(TEMPLATES=[], INSTALLED_APPS=[], DEBUG=True)
django.setup()

from django.template import Context, Engine  # noqa: E402
from django.template.base import Lexer, Parser  # noqa: E402

engine = Engine(debug=True)

DEFERRED = "{% partialdef p %}{% cycle c %}{% endpartialdef %}"

CASES = {
    # The shape Task 5's row uses. `as c` is not silent, so the outer tag renders
    # too: one character from it, then one per `{% partial p %}`.
    "outer cycle + 2 partials": (
        "{% cycle 'a' 'b' as c %}" + DEFERRED + "{% partial p %}{% partial p %}"
    ),
    # One more partial than cycle values, which is what separates "resets" from
    # "advances": advancing gives `abab`, resetting gives `aaaa`.
    "outer cycle + 3 partials": (
        "{% cycle 'a' 'b' as c %}"
        + DEFERRED
        + "{% partial p %}{% partial p %}{% partial p %}"
    ),
    # `silent` suppresses the outer tag's own output, isolating the partials.
    "silent outer + 3 partials": (
        "{% cycle 'a' 'b' as c silent %}"
        + DEFERRED
        + "{% partial p %}{% partial p %}{% partial p %}"
    ),
    # The definition rendered inline at the point of use, twice.
    "inline definition, twice": (
        "{% cycle 'a' 'b' as c %}"
        "{% partialdef p inline %}{% cycle c %}{% endpartialdef %}"
        "{% partial p %}{% partial p %}"
    ),
}

print(f"django {django.get_version()} on {sys.version.split()[0]}")
print("rendered output, per shape:")
for label, source in CASES.items():
    print(f"  {label:<28} {engine.from_string(source).render(Context({}))!r}")

# Is the one-argument form literally the same node object? This is the fact that
# makes "one slot, one node" true in Django, and it holds independently of the
# snapshot question.
parser = Parser(
    Lexer("{% cycle 'a' 'b' as c %}{% cycle c %}").tokenize(),
    builtins=engine.template_builtins,
)
nodes = parser.parse()
print("\nnode identity for `{% cycle c %}` after `{% cycle 'a' 'b' as c %}`:")
print(f"  classes               {[type(n).__name__ for n in nodes]}")
print(f"  nodes[0] is nodes[1]  {nodes[0] is nodes[1]}")

# The mechanism behind "a single node still restarts": print the source of
# `PartialTemplate.render` rather than paraphrasing it, so the claim above is
# checkable from this output alone.
import inspect  # noqa: E402

from django.template.base import PartialTemplate  # noqa: E402

print("\n`PartialTemplate.render`, verbatim from the pinned tree:")
for line in inspect.getsource(PartialTemplate.render).splitlines():
    print(f"  {line}")

# And the consequence, isolated to one node: a `{% cycle %}` rendered, then a
# partial rendered (which restarts it), then the same cycle rendered again.
isolated = (
    "{% cycle 'a' 'b' as c %}"
    "{% partialdef p %}x{% endpartialdef %}"
    "{% partial p %}"
    "{% cycle c %}"
)
print("\nisolated restart (render, partial, render — advancing would give 'ab'):")
print(f"  {engine.from_string(isolated).render(Context({}))!r}")
