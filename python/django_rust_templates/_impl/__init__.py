"""The implementation substituted for :mod:`django.template`.

This file is loaded *as* ``django.template``, with a ``__path__`` that searches
this directory first and Django's own template directory second. Imports below
therefore resolve to our modules where they exist and to Django's where they do
not yet.

The exports here define our public contract and mirror ``django.template``'s.
As milestones land, ``engine``, ``base``, ``context`` and friends stop falling
through to Django and start resolving to our implementations; nothing in this
file needs to change when that happens.
"""

# Multiple Template Engines

from .engine import Engine
from .utils import EngineHandler

engines = EngineHandler()

__all__ = ("Engine", "engines")


# Django Template Language

# Public exceptions
from .base import VariableDoesNotExist
from .context import Context, ContextPopException, RequestContext
from .exceptions import TemplateDoesNotExist, TemplateSyntaxError

# Template parts
from .base import (
    Node,
    NodeList,
    Origin,
    PartialTemplate,
    Template,
    Variable,
)

# Library management
from .library import Library

# Import the .autoreload module to trigger the registration of signals.
from . import autoreload

__all__ += ("Template", "Context", "RequestContext")
