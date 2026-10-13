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

# The native emitter can append an exact SafeString directly while these
# implementations still govern its conversion and attribute reads.
from django.utils.safestring import SafeString

SAFE_STRING = SafeString


def _identity(self):
    return self


def _identity_pin(function):
    # Django's class may have been changed before this package was imported.
    # License only the two trivial `return self` bodies, never an arbitrary
    # function that happened to be installed when we first read the class.
    if type(function) is not types.FunctionType:
        return None
    code = function.__code__
    reference = _identity.__code__
    if (
        code.co_code == reference.co_code
        and not code.co_names
        and not code.co_freevars
        and not code.co_cellvars
        and code.co_argcount == 1
        and code.co_kwonlyargcount == 0
        and code.co_nlocals == 1
        and not code.co_flags & (0x04 | 0x08 | 0x20 | 0x80 | 0x200)
    ):
        return function
    return None


SAFE_STRING_STR = _identity_pin(SafeString.__str__)
SAFE_STRING_HTML = _identity_pin(SafeString.__html__)
SAFE_STRING_GETATTRIBUTE = str.__getattribute__
SAFE_STRING_CLASS = vars(object)["__class__"]


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


# --- third-party `simple_tag` nodes ------------------------------------------
#
# `@register.simple_tag` is how most third-party tags are written, and the node it
# generates is **Django's** `django.template.library.SimpleNode` -- a library
# registers with `django.template.Library`, not with this package's, so the class
# is Django's even though the tag is not. Its `render` is scaffolding around a
# call: resolve each argument, call the function, escape the result, optionally
# assign `as var`. None of that is third-party code.
#
# Measured on bakerydemo, splitting each real render into the two halves: the
# scaffolding is ~2.7 us per call, and for a tag whose body is cheap it is nearly
# all of the cost -- `cell_classname` spends 0.42 us doing its job and 3.39 us
# being called (89%). For an expensive one it is a smaller share: `pageurl` is
# 21.63 us of body against 2.91 us of scaffolding (12%).
from django.template.library import SimpleNode as _DjangoSimpleNode

from .library import SimpleNode as _OurSimpleNode

# **Both classes, and Django's is the one that matters.** A third-party library
# registers with `django.template.Library`, so its generated node is Django's even
# though the tag is not -- measured on bakerydemo, all 283 `SimpleNode` renders
# were `django.template.library.SimpleNode`. This package's own `library.py` is a
# transcription of that module, so its `SimpleNode` is the same implementation and
# is accepted on the same terms; `{% querystring %}` is the built-in that reaches
# it.
_SIMPLE_NODE_RENDERS = {
    _DjangoSimpleNode: _DjangoSimpleNode.render,
    _OurSimpleNode: _OurSimpleNode.render,
}


def simple_tag_parts(node):
    """The pieces of a `SimpleNode` the walk needs, or ``None`` to leave it alone.

    ``None`` for anything that is not exactly one of the two `SimpleNode` classes
    with its own `render`: a subclass (`SimpleBlockNode` is one, and it renders a nodelist
    first), a rebound body, or a shape the walk does not reproduce. The identity
    rule is `RENDER`/`PLAN`'s, for the same reason -- what the walk stands in for
    has to be the implementation it was measured against.

    **Keyword arguments are declined for now**, not because they are hard but
    because every one of the 283 `SimpleNode` renders measured across eleven real
    pages had none; the shape that matters is ``takes_context`` with one
    positional argument, which is 68% of them.
    """
    stock_render = _SIMPLE_NODE_RENDERS.get(type(node))
    if stock_render is None:
        return None
    if type(node).render is not stock_render:
        return None
    if node.kwargs:
        return None
    return node.func, bool(node.takes_context), list(node.args), node.target_var
