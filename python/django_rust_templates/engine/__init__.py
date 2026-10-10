"""The implementation substituted for :mod:`django.template`.

# TRANSCRIBED: django/template/__init__.py (Django 6.0.9)

This file is a transcription, and deliberately so. The export list *is* the
behaviour here: import order is load-bearing (``engines`` must be bound before
the later imports; ``from . import autoreload`` registers signal receivers), and
a "fresh" version that differed would be wrong. Only the module docstring and
the ``# NOQA isort:skip`` comments differ from upstream.

Because it is a transcription, **diff it against upstream on every Django bump**
rather than reviewing it as original work. See §6 of the design spec for the
policy and the list of other transcribed files.

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

# **No registry here, and its absence is deliberate.** Upstream's
# ``django/template/__init__.py`` — of which this file is the engine half —
# also binds ``engines = EngineHandler()``, the per-project registry that
# ``django.template.loader``, ``django.template.response`` and
# ``django.template.autoreload`` all consult, and that
# ``django.test.signals.reset_template_engines`` empties on every ``TEMPLATES``
# change. All four of those are Django's, and they name *Django's* instance, so a
# second handler built here would be a registry nothing resets: measured, as
# ``test_response.TemplateResponseTest.test_render`` reading a backend built from
# the settings a class-level ``override_settings`` had already replaced. Our
# backend registers itself there like any other backend, by being named in
# ``settings.TEMPLATES``; there is nothing for this package to keep.

__all__ = ("Engine",)


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
#
# Guarded, and it is the only *behavioural* difference from upstream in this
# file. Upstream's `django/template/autoreload.py` is not the engine — it is a
# `django.utils.autoreload` signal receiver that iterates `engines.all()` and
# keeps only `DjangoTemplates` backends, i.e. it exists to reset *Django's* loaders
# on file change. It is therefore declared `NOT_THE_ENGINE` and is not transcribed
# here. Loaded as `django.template` (the transitional shimmed mode) the relative
# import still finds upstream's file on the package's fallback `__path__`, so
# every signal registration upstream makes is still made, exactly as before.
# Loaded as `django_rust_templates.engine` — the mode this package ships in —
# there is no such sibling and nothing to register: the backend's own loaders are
# not `DjangoTemplates`, so the receivers above would skip them anyway.
try:
    from . import autoreload
except ImportError:
    pass

__all__ += ("Template", "Context", "RequestContext")


# M18.1's, and the only code in this file that is not upstream's.
#
# **This package needs one name it can be reached by from either mode**, and this
# is the whole of what that costs the transcription. It is loaded as
# `django.template` under the finder and as `django_rust_templates.engine` without
# one, and the two have different parents. For most of the tree that does not
# matter — a relative import is the same file either way — but
# `engine/templatetags/*.py` is served as `django.templatetags.*`, a *sibling* of
# `django.template`, so from there `..` is `django` and no relative spelling
# reaches this package at all. One absolute literal correct in both modes is the
# only option, and `_impl_alias` is what makes `django_rust_templates.engine` that
# literal. The machinery, the measurements behind it and the reason it is a
# finder rather than a `sys.modules` loop live there, in a file that is ours; this
# file stays upstream's plus four lines.
#
# `template` is the package bound to its own name, which is what lets
# `templatetags/static.py` keep upstream's `from django import template` shape and
# all seven of its `template.X` uses.
import sys as _sys

from django_rust_templates._impl_alias import install as _install_alias

template = _sys.modules[__name__]

_install_alias(__name__)
