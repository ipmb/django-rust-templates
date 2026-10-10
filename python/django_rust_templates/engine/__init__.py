"""The Django Template Language, as this package implements it.

# TRANSCRIBED: django/template/__init__.py (Django 6.0.9)

This file is a transcription, and deliberately so. The export list *is* the
behaviour here — the order of the imports below is part of what upstream
promises, and a "fresh" version that differed would be wrong. The three regions
that do differ from upstream are marked in place, and they are the whole of the
difference: the module docstring, the absence of the ``engines`` registry, and
the absence of ``from . import autoreload``.

Because it is a transcription, **diff it against upstream on every Django bump**
rather than reviewing it as original work. See §6 of the design spec for the
policy and the list of other transcribed files.

**The name of this module is not ``django.template``.** It lives in this
package's own namespace, and the only thing that puts it in front of a project
is ``TEMPLATES[0]["BACKEND"]`` pointing at ``django_rust_templates.backends``.
So the relative imports below resolve to this package's modules throughout, at
every line, in every mode — there is no second spelling of this file and no
``__path__`` fallback that could resolve one of them to Django's.
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

# **Upstream imports its ``autoreload`` sibling here, and this file does not, and
# nothing is lost.** ``django/template/autoreload.py`` is not the engine: it is
# three ``django.utils.autoreload`` receivers that walk ``engines.all()``, keep
# the ``DjangoTemplates`` instances and reset their loaders when a template file
# changes. It is Django's view of a *project's* configuration, declared
# ``NOT_THE_ENGINE``, and this package has no module to transcribe in its place.
#
# The receivers are registered by Django's **own** ``django/template/__init__.py``,
# which is imported by any project whether or not it uses this package —
# ``django.template.loader`` imports it, and Django's forms, admin and test
# utilities all import that. They still cover *this* package's backend, because
# ``RustTemplates`` subclasses ``DjangoTemplates`` precisely so that
# ``isinstance`` holds; that subclass is a marker, not an inheritance, and this
# is one of the two places the marker buys something.
#
# Measured rather than reasoned: ``test_autoreloader`` is green with this import
# absent, including the two rows that assert ``reset_loaders()`` reaches a
# configured backend's loaders.

__all__ += ("Template", "Context", "RequestContext")
