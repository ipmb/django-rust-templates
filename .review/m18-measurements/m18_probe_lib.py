"""A `Library` whose one tag reports what a *third-party tag* can observe mid-render.

Kept in its own module because `{% load %}` resolves a library by module name, and
the driver is `__main__`.
"""

from __future__ import annotations

from django.template import Library
from django.template.loaders.locmem import Loader as LocmemLoader

register = Library()

# Every call appends here; the driver prints the list.
OBSERVATIONS: list[dict] = []

# --- a counting loader, for the include probe -------------------------------
#
# It has to live in an importable module because `Engine.find_template_loader`
# accepts only a *string* import path (or a tuple starting with one): it splits
# `(loader, *args)`, requires `loader` to be a str, imports it, and calls
# `cls(engine, *args)`. A live instance or a bare class is rejected with
# `ImproperlyConfigured`.
#
# Subclassing locmem's loader rather than hand-rolling `get_template_sources`,
# so that everything except the count is Django's own code path.

TEMPLATES: dict[str, str] = {}
LOADS: dict[str, int] = {}


class CountingLoader(LocmemLoader):
    def get_contents(self, origin):
        LOADS[origin.name] = LOADS.get(origin.name, 0) + 1
        return super().get_contents(origin)



def _type_of(obj):
    if obj is None:
        return None
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


@register.simple_tag(takes_context=True)
def observe(context, label):
    """Report the render-time state a third-party `takes_context` tag can see."""
    try:
        block = context.get("block")
    except Exception:  # noqa: BLE001 — the point is to record what happens
        block = "<context.get('block') raised>"

    render_context = getattr(context, "render_context", None)
    try:
        rc_keys = sorted(k for k in render_context if isinstance(k, str))
    except Exception:  # noqa: BLE001
        rc_keys = ["<render_context not iterable>"]

    try:
        block_context = render_context.get("block_context")
    except Exception:  # noqa: BLE001
        block_context = None

    template = getattr(context, "template", None)

    OBSERVATIONS.append(
        {
            "label": label,
            "block": _type_of(block) if not isinstance(block, str) else block,
            "block_name": getattr(block, "name", None),
            "block_has_super": hasattr(block, "super"),
            "render_context_keys": rc_keys,
            "block_context": _type_of(block_context),
            "template": _type_of(template),
            "engine": _type_of(getattr(template, "engine", None)),
        }
    )
    return ""
