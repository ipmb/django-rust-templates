"""Filters that are handed the context **as an argument** and use it.

Registered as an ordinary Django library, and imported the ordinary way — the
case naming it declares ``libraries={"ctxwrite": "ctxwrite_library"}`` and the
template opens with ``{% load ctxwrite %}``. That matters more than it looks:
the differential runner keeps the finder *off*, so ``from django.template import
Library`` here is stock Django's ``Library`` under **both** engines, which is
what a real ``templatetags`` module gets and is therefore the route worth
testing. (A library built on this package's own transcription of ``Library``
would have been a fixture measuring the fixture.)

**Why a filter can write the context at all.** ``FilterExpression.resolve``
hands a filter the arguments the *template* gave it, and the context object is a
value a template can name (``{{ xs|rebind_up:ctx }}`` with ``ctx`` set to the
context itself). ``takes_context`` is ``simple_tag``'s protocol and a filter
never gets the context implicitly — but explicitly is enough, and that is the
route `rewrite_body`'s docstring states.

Two of these write where Django's ``ForNode.render`` would have them write and
one only *reads* the layer stack, which is how the loop's ``context.push()``
becomes measurable: the push is not a claim about an object, it is a count of
layers that Python can see.

``pop_layer`` is the fourth and it is here rather than in a scratch directory:
the probe that measures these routes (``.review/m19-measurements/
ctx_write_channels.py``) writes a name into a layer it pushes and then discards,
which is the one write no frame could be holding. A fixture in a job's temp
directory would make the probe's citation unreproducible, so the library is
where the templates that use it can find it.
"""

from django.template import Library

register = Library()


@register.filter(name="rebind_up")
def rebind_up(value, context):
    """Write the enclosing loop's variable *upward*, as ``{% cycle … as %}`` does.

    ``set_upward`` lands the name in the innermost layer already holding it, so
    the write survives to the rest of that layer's body — and goes away with the
    layer, which is what makes *where* it lands observable.
    """
    context.set_upward("s", "X")
    return value


@register.filter(name="rebind_item")
def rebind_item(value, context):
    """Write the name into the **innermost** layer (``BaseContext.__setitem__``).

    The other half of the pair: ``set_upward`` reaches out of a layer, this
    writes only into the current one, so a loop that opens its layer *after*
    resolving its sequence leaks this write outward and a loop that opens it
    first does not.
    """
    context["s"] = "X"
    return value


@register.filter(name="ctxdepth")
def ctxdepth(value, context):
    """Return ``len(context.dicts)`` as a one-item list, so ``{% for %}`` takes it.

    The observation without the write: Django's ``ForNode.render`` opens
    ``with context.push():`` at ``engine/defaulttags.py:198`` and resolves the
    sequence at ``engine/defaulttags.py:198-199``, so a sequence filter sees the loop's layer already
    open — one deeper than the page's own layers.
    """
    return [str(len(context.dicts))]


@register.filter(name="pop_layer")
def pop_layer(value, context):
    """Write ``s`` into a layer this filter pushes and discards itself.

    The control for the two writes above: the name never lands anywhere the
    enclosing loop can read it, so a render that reports the write as surviving
    has lost a layer rather than gained one.
    """
    context.push()
    try:
        context["s"] = "X"
    finally:
        context.pop()
    return value
