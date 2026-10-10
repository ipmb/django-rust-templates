"""A tag library for the `stated_*` probes: one delegate of each kind.

`simple_tag` (no context), `simple_tag(takes_context=True)`, and a filter — all
three are nodes or seams this walk hands to Python, which is what makes a
`{% for %}` body holding one a *stated* loop rather than a purely sealed one.

`writes` is the one that can make a render's *layer* observable from outside it;
`boom` is the failure path; `shout` proves a delegate sees the values rather than
merely not raising.  `surro` is here as a documented dead end — read its
docstring before reaching for it as a way to make the filter seam refuse.
"""

from django.template import Library

register = Library()


@register.simple_tag
def noop():
    return "!"


@register.simple_tag(takes_context=True)
def shout(context, value):
    counter = context["forloop"]["counter"] if "forloop" in context else "-"
    return f"<{value}:{counter}>"


@register.filter
def mark(value):
    return f"*{value}*"


@register.filter
def surro(value):
    """The route that looks right and is not — left here as the trap it was.

    A filter this library registers is a filter the Rust registry does **not**
    answer, so a node carrying it is an `Act::Delegate` at plan time: it sets
    `ForPlan::context_state` and the loop takes `Walk::run_stated`, the
    per-iteration path. `Walk::write_frame` is never reached. Anything reading
    `surro` as "the way to make the seam refuse" is one edit away from a probe
    that reports `delegated: VariableNode x1` and measures the wrong path.

    The seam refuses on the *value*, not on the filter: a lone surrogate has to
    arrive from the data at a filter the walk **plans** (`upper`).  See
    `stated_refused.py`, whose `SURRO` dict is that shape.
    """
    return "\ud800"


@register.simple_tag
def boom():
    raise ValueError("boom")


@register.simple_tag(takes_context=True)
def writes(context, name):
    """Write `name` into the context and render nothing.

    The one delegate that can make the *layer* a render writes into observable
    from outside it, which is what `stated_empty_write.py` needs: a write Django
    discards at a `push`'s exit and a walk that never pushed would keep.
    """
    context[name] = "W"
    return ""

