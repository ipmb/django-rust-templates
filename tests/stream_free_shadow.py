"""A library whose `shifty` is a block compiler, for the shadowing test.

`{% load %}` must be able to rebind a name that was an eligible `simple_tag`
into one that consumes its body, and the classifier must notice *within the
same parse*. A library reached by name is how `find_library` resolves it.
"""

from django_rust_templates.engine.base import Node
from django_rust_templates.engine.library import Library

register = Library()


@register.tag("shifty")
def shifty_block(parser, token):
    parser.parse(("endshifty",))
    parser.delete_first_token()
    return Node()
