"""The implementations the native walk stands in for, captured at import.

``{% include %}`` ends in ``template.render(context)``, and the native walk
answers that call itself rather than making it. That is only the same thing
while the target's ``render`` and ``_render`` still resolve to the two functions
below — the ones ``engine/base.py`` defines and the walk reproduces.

**Read here, once, and never again.** A second read is what turns the check
into a follow: measured, rebinding ``Template.render`` to a wrapper that adds
brackets leaves a slot that rereads the class rendering ``[x]`` where stock
Django renders ``[[x]]``. The wrapper is not an implementation Rust reproduces,
so a target carrying it has to decline the slot and go back to Python.

**The capture point is the import of this module**, which is why
``django_rust_templates/rust.py`` names it: that file is the one door into the
package — ``EngineHandler`` resolves it before any template is loaded — so
nothing can rebind either name ahead of the capture. Reaching the engine
directly, as the test suite does, and rebinding before the first render is the
one shape that gets past this; it is not a shape a project can be in.
"""

import types

from .base import Template

RENDER = Template.render
PLAN = Template._render

# ``types.MethodType`` is CPython's ``method``, the type ``target.render`` has when the lookup
# really did find a function on the type. It cannot be subclassed, so an exact type test is the
# whole question.
METHOD_TYPE = types.MethodType


# --- nodes whose text is fixed before any context exists ---------------------
#
# Four built-ins render something known at compile time: `{% comment %}` and
# `{% load %}` render ``""``, `{% verbatim %}` renders the content it was built
# with, and `{% templatetag %}` renders one of eight constants. Delegating them
# costs a Python frame per render to return a value the compile already knew --
# measured against stock Django, `{% verbatim %}` 2.20x, `{% comment %}` 2.02x
# and `{% templatetag %}` 1.80x, and `{% load %}` is 119 of the 709 delegated
# nodes across eleven real pages.
#
# **The identity check is the point, not the lookup.** A subclass, or a body
# rebound after import, is not the implementation measured here, so the walk must
# decline it rather than answer from a table -- the same rule, and the same
# reason, as `RENDER`/`PLAN` above. The table is read once at import, before
# anything can rebind it.
from .defaulttags import CommentNode, LoadNode, TemplateTagNode, VerbatimNode

_CONSTANT_NODES = {
    CommentNode: (CommentNode.render, lambda node: ""),
    LoadNode: (LoadNode.render, lambda node: ""),
    VerbatimNode: (VerbatimNode.render, lambda node: node.content),
    TemplateTagNode: (
        TemplateTagNode.render,
        lambda node: TemplateTagNode.mapping.get(node.tagtype, ""),
    ),
}


def constant_text(node):
    """The text ``node`` will always render, or ``None`` if it is not one of the four.

    Called once per node at **plan** time, never per render. ``None`` is the
    answer for everything the walk must go on delegating, including a subclass of
    one of the four and an instance whose class had ``render`` rebound.

    A non-``str`` answer is refused rather than coerced: `{% verbatim %}`'s
    ``content`` is a `SafeString` the parser built, and anything else there means
    the node was not constructed by this engine.
    """
    entry = _CONSTANT_NODES.get(type(node))
    if entry is None:
        return None
    stock_render, text_of = entry
    if type(node).render is not stock_render:
        return None
    text = text_of(node)
    return text if type(text) is str or isinstance(text, str) else None
