"""The two exceptions a template backend crosses its boundary with.

**These are Django's classes, not copies of them, and that is the whole of this
module.** They were copies until the vendored suite measured what a copy costs,
and the answer is not "a slightly different name in a traceback" — it is that
Django's own machinery cannot catch them.

The seam is ``django/template/loader.py``, which is ``NOT_THE_ENGINE``: it is the
facade a project calls, it is not ours, and every one of its four functions wraps
the engine call in ``except TemplateDoesNotExist``, where that name is
``django.template.exceptions.TemplateDoesNotExist``. So is
``django/template/response.py``, and so is every ``except TemplateDoesNotExist``
in every project that uses this package. An engine that raises its own class is
an engine those catches do not see: ``SimpleTemplateResponse(["foo.html",
"second/test.html", "first/test.html"]).render()`` does not fall through to the
second name, it raises out of ``select_template`` on the first — measured as
``test_response.SimpleTemplateResponseTest.test_template_resolving``, and the
same shape again in ``test_partials`` and four rows of ``test_loaders``.

**Django says this itself, in the module being re-exported.** Its docstring
reads "This module contains generic exceptions used by template backends", and
Django's own backend reaches them through the package root — ``from django.template
import TemplateDoesNotExist`` — rather than defining anything. The exceptions are
the *protocol*, not the engine: an implementation may be replaced, the wire
format it fails on may not.

**Nothing here is a shim.** This module imports two names from a library this
package depends on and hands them back; the engine's own modules go on writing
``from .exceptions import TemplateDoesNotExist``, unchanged, because the name
they get is the one that is caught.

``TemplateSyntaxError`` is aliased for the same reason and on the same evidence:
it is raised for a bad template at parse time and caught by user code and by
Django's debug views, and ``django.template.exceptions`` defines both in one
module with one docstring.
"""

from django.template.exceptions import (  # noqa: F401
    TemplateDoesNotExist,
    TemplateSyntaxError,
)

__all__ = ("TemplateDoesNotExist", "TemplateSyntaxError")
