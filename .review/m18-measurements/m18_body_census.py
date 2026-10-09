"""M18.0 slice 1, follow-up probe: what is *inside* a loop body, per body?

Requested by review. §7.1 prices the for-loop fork at ~40 points between "the loop
variable lives in a Rust overlay" and "it is pushed onto the Python `Context`", and
review's correction is that the overlay arm is not a design choice one can simply
take: **an overlay living only in Rust is invisible to any node delegated to Python**,
and a body with one delegated node forces the loop variable into the Python `Context`
for that node -- at which point the push is paid anyway. So the realistic outcome is
mixed per loop, and the arm's *applicability* is the fraction of loop bodies that are
entirely Rust-handled.

That fraction is a property of Rust's coverage against the template, so it has to be
reported as a **class-name census per body**, not as a bare percentage: the reader then
recomputes the weight for whatever coverage is actually under discussion. A percentage
would silently bake in slice 1's two classes.

This is a stock-only, parse-only probe: it renders nothing, so it is unaffected by the
port's current state and by the gate's corpus. It reads the templates §3's parse
measurement was taken on -- the ones the gate could not reach at all.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

import m18_render_slice1 as gate  # noqa: E402  (settings + pinned tree, as the gate does)

import bench  # noqa: E402

from django.template import Engine  # noqa: E402
from django.template.base import NodeList  # noqa: E402
# `ForNode` is not in `base` at 6.0.9; importing the module also registers
# `{% for %}`/`{% if %}`, without which `engine.from_string` cannot parse the corpus.
from django.template.defaulttags import ForNode  # noqa: E402

CORPUS = {"BASE": bench.BASE, "PAGE": bench.PAGE, "LARGE": bench.LARGE}


def body_classes(nodelist) -> Counter:
    """Every node class reachable from `nodelist`, counting nested nodelists too.

    Nested, because a body's cost and its delegability both include what its tags
    contain: `{% for %}{% if %}{{ x }}{% endif %}{% endfor %}` is not a bare body just
    because its top level holds one node.
    """
    found: Counter = Counter()
    stack = [nodelist]
    while stack:
        current = stack.pop()
        for node in current:
            found[type(node).__name__] += 1
            # Every `NodeList` a node holds, found by inspection rather than by a
            # per-class table: `attr_value` is not a `NodeList`, and a hardcoded list
            # of attribute names would miss the next tag that adds one.
            for value in vars(node).values():
                if isinstance(value, NodeList):
                    stack.append(value)
                elif isinstance(value, (list, tuple)) and value and all(
                    isinstance(v, tuple) for v in value
                ):
                    # `IfNode.conditions_nodelists` is a list of `(cond, nodelist)`.
                    for v in value:
                        if len(v) == 2 and isinstance(v[1], NodeList):
                            stack.append(v[1])
    return found


def loops(nodelist):
    """Every `ForNode` reachable from `nodelist`, with its body's class census."""
    out = []
    stack = [nodelist]
    while stack:
        current = stack.pop()
        for node in current:
            if isinstance(node, ForNode):
                out.append(node)
            for value in vars(node).values():
                if isinstance(value, NodeList):
                    stack.append(value)
                elif isinstance(value, (list, tuple)) and value and all(
                    isinstance(v, tuple) for v in value
                ):
                    for v in value:
                        if len(v) == 2 and isinstance(v[1], NodeList):
                            stack.append(v[1])
    return out


def main() -> int:
    print("m18.0 slice 1 -- what is inside a loop body, per body")
    print(f"tree:    {gate.template_base.__file__}")
    print(f"django:  {gate.django.get_version()}")
    print(f"python:  {sys.version.split()[0]}")
    print()

    # `Engine()` ships no libraries, and every template in this corpus opens with
    # `{% load i18n %}`. Naming it here is the pinned tree's own module, not ours.
    engine = Engine(libraries={"i18n": "django.templatetags.i18n"})
    # The classes slice 1's floor handles. A reader substitutes their own set; this
    # is printed as a *set*, not folded into a count, precisely so it can be swapped.
    HANDLED = {"TextNode", "VariableNode"}
    print(f"slice 1's floor handles: {sorted(HANDLED)}")
    print("(printed as a set on purpose -- the weight below is recomputable for any")
    print(" coverage, and a percentage would bake this pair in)")
    print()

    for name, src in CORPUS.items():
        tree = engine.from_string(src)
        bodies = loops(tree.nodelist)
        print(f"{name}: {len(src)} chars, {len(bodies)} `{{% for %}}` node(s)")
        if not bodies:
            continue
        fully = 0
        for i, node in enumerate(bodies):
            # `ForNode` holds `nodelist_loop` and `nodelist_empty` (`defaulttags.py:175`),
            # not a plain `nodelist` -- the generic attribute scan in `body_classes` finds
            # them, but the *body* has to be named: `nodelist_empty` runs zero times and
            # folding it into a per-iteration weight would count a branch that never runs.
            loop_body = getattr(node, "nodelist_loop", None)
            if loop_body is None:
                print(f"  body {i}: NO nodelist_loop; attrs="
                      f"{sorted(k for k, v in vars(node).items() if isinstance(v, NodeList))}")
                continue
            classes = body_classes(loop_body)
            unhandled = sorted(set(classes) - HANDLED)
            bare = not unhandled
            fully += bare
            seq = "+".join(f"{k}x{v}" for k, v in sorted(classes.items()))
            print(f"  body {i}: {seq}")
            print(f"           unhandled={unhandled or '[]'}  "
                  f"overlay applies={bare}")
            empty = getattr(node, "nodelist_empty", None)
            if empty is not None and len(empty):
                print(f"           `{{% empty %}}` branch: "
                      f"{sorted(body_classes(empty))} (runs 0 times per iteration)")
            # The variables the body resolves *per iteration*, which is the depth the
            # lookup probe says is at or past crossover.
            exprs = [
                n.token.contents
                for n in loop_body
                if type(n).__name__ == "VariableNode"
            ]
            if exprs:
                print(f"           per-iteration variables: {exprs}")
        print(f"  overlay applies to {fully}/{len(bodies)} of this template's loop bodies")
        print()

    print("reading -- `overlay applies=True` means every node in that body is one the")
    print("floor handles in Rust, so the loop variable can live in a Rust-side scope and")
    print("cost no crossing per iteration. One unhandled node anywhere in the body (the")
    print("census is nested) and the variable must be pushed for that node, at which")
    print("point the saving is gone. So the fork's ~23% arm is weighted by the fraction")
    print("of bodies printing True, and that fraction moves with Rust's coverage --")
    print("which is why the class names are printed and the weight is not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
