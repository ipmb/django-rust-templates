"""A stateful *host-compiled* tag across a `{% partial %}` boundary, stock Django.

Task 5 needs to know whether a behavioural row can carry the conditional, and a
row is only a discriminator if the two candidate implementations answer it
differently. The candidate row is

    {% load custom %}{% partialdef p %}{% counter %}{% endpartialdef %}
    {% partial p %}{% partial p %}{% partial p %}

with `'012'` meaning "one `CounterNode`, its state advancing" and `'000'` meaning
"a fresh node per render". This instrument measures the expectation on the pinned
tree, and — more to the point — prints the shape of the tag that produces it:

* `counter` is a **library** tag (`custom.py:381-393`), registered by
  `register.tag`, so in our engine its node is built by `ShellHost.call_tag`
  during the parse, not by the materialiser;
* `CounterNode.__init__` is `self.count = 0` and `render` reads-then-increments
  (`custom.py:386-393`), so its state lives on the instance and nothing in
  Django resets it — no `render_context`, no `push_state`;
* therefore `'012'` is exactly "the same object three times".

This is the same probe shape M10 finding 6 validated for `{% include %}`
(`test_include.py:372-397`), which is why it is worth measuring rather than
deriving: the counter fixture is the project's established instrument for "was
this node built once".

Pinned tree and pinned interpreter, named by version: `.django/` @ 6.0.9 on
`.venv`/CPython 3.14.4. It runs in **both** engine modes and prints which one it
measured: under `-I` the pin gives the expectation, and with the shim's
`PYTHONPATH` ours gives the parity check. All seven shapes agree either way.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / ".django", ROOT / ".django" / "tests"):
    sys.path.insert(0, str(_path))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(TEMPLATES=[], INSTALLED_APPS=[], DEBUG=True)
django.setup()

from django.template import Context, Engine  # noqa: E402

LIBRARIES = {"custom": "template_tests.templatetags.custom"}
engine = Engine(debug=True, libraries=LIBRARIES)

CASES = {
    # The control: three mentions at top level compile to **three distinct**
    # nodes (`call_tag` runs once per token), each with its own `count`. So they
    # print `'000'`, and the identity section below prints `all the same object
    # False` for exactly these three. That is what makes the partial row's
    # `'012'` mean something: the same output *shape* from one stored nodelist is
    # one node rendered three times, not three nodes.
    "top level, 3 mentions": (
        "{% load custom %}{% counter %}{% counter %}{% counter %}"
    ),
    # The candidate row. Two partials is where a per-render rebuild shows.
    "partialdef p, 2 partials": (
        "{% load custom %}"
        "{% partialdef p %}{% counter %}{% endpartialdef %}"
        "{% partial p %}{% partial p %}"
    ),
    # The one-partial shapes, and the row that corrected an owed value. A
    # **non-inline** `{% partialdef %}` renders nothing at its definition site
    # (`PartialDefNode.render` is `""` unless `inline`), so one `{% partial p %}`
    # renders the counter **once** and prints `'0'` — not the `'01'` it was
    # quoted as, which is the `inline` shape's value. Measured, not derived: both
    # engines print `'0'` here and `'01'` for the `inline` row below.
    "partialdef p, 1 partial": (
        "{% load custom %}"
        "{% partialdef p %}{% counter %}{% endpartialdef %}"
        "{% partial p %}"
    ),
    "partialdef p inline, 1 partial": (
        "{% load custom %}"
        "{% partialdef p inline %}{% counter %}{% endpartialdef %}"
        "{% partial p %}"
    ),
    "partialdef p, 3 partials": (
        "{% load custom %}"
        "{% partialdef p %}{% counter %}{% endpartialdef %}"
        "{% partial p %}{% partial p %}{% partial p %}"
    ),
    # `inline` renders the definition at the point of use as well as registering
    # it, which is the other spelling of the same tag. It is `'0'` here with no
    # mention at all, which is the row that says the definition *site* renders
    # once and only the `inline` spelling renders there.
    "partialdef p inline, no mentions": (
        "{% load custom %}"
        "{% partialdef p inline %}{% counter %}{% endpartialdef %}"
    ),
    # The M10 shape, for contrast: an `{% include %}` whose template is loaded
    # once and rendered nine times.
    "include, 3 renders": (
        "{% load custom %}"
        "{% for x in vars %}{% include \"inc\" %}{% endfor %}"
    ),
}

# Which engine was measured, printed rather than assumed. Run under `-I` this is
# the pin and the rows are the expectation; run with the shim's `PYTHONPATH` it is
# ours and the rows are the parity check. Without the marker the two runs are
# byte-identical, so a shim that quietly failed to install would read as
# agreement — the trap `m13_measure_cycle_boundary.py` exits on.
from django.template import base as django_base  # noqa: E402

_under_shim = "django_rust_templates/_impl/base.py" in django_base.__file__
_side = "ours (shim)" if _under_shim else f"stock Django ({django_base.__file__})"
print(f"side={_side}")
print(f"django {django.get_version()} on {sys.version.split()[0]}")
print("\nrendered output, per shape:")
for label, source in CASES.items():
    if "include" in label:
        include_engine = Engine(
            debug=True,
            libraries=LIBRARIES,
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {"inc": "{% load custom %}{% counter %}"},
                )
            ],
        )
        out = include_engine.from_string(source).render(Context({"vars": range(3)}))
    else:
        out = engine.from_string(source).render(Context({}))
    print(f"  {label:<32} {out!r}")

# The identity fact behind the expected value, read straight off the tree rather
# than inferred from the string: the three mentions share one node object.
parser = engine.template_libraries  # noqa: F841  (engine built above)
compiled = engine.from_string(
    "{% load custom %}{% counter %}{% counter %}{% counter %}"
)
counters = [n for n in compiled.nodelist if type(n).__name__ == "CounterNode"]
print("\nthe three top-level mentions, as objects:")
print(f"  classes              {[type(n).__name__ for n in counters]}")
print(f"  all the same object  {all(n is counters[0] for n in counters)}")
print(f"  count attribute      {[n.count for n in counters]}")

# And `CounterNode`, verbatim, so the "state lives on the instance" claim in this
# docstring is checkable from this output alone.
import inspect  # noqa: E402

from template_tests.templatetags.custom import CounterNode  # noqa: E402

print("\n`CounterNode`, verbatim from the pinned tree:")
for line in inspect.getsource(CounterNode).splitlines():
    print(f"  {line}")
