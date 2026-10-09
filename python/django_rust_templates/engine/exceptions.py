"""Exceptions raised by template backends.

Historically Django's template language also raises these from its internals, so
they live here rather than alongside the language, and the signature is part of
the public contract: loader and backend code constructs these positionally and
by keyword, and ``django.test`` inspects ``tried`` and ``chain``.

Behaviour is deliberately identical to ``django.template.exceptions``; in
particular ``args`` carries only the message, which is what keeps these
picklable and is asserted by the loaders' caching tests.
"""


class TemplateDoesNotExist(Exception):
    """Raised when a template cannot be found.

    ``backend``
        The backend class that was looking, when there was one.

    ``tried``
        ``(origin, status)`` pairs describing each source attempted and why it
        was rejected.

    ``chain``
        Intermediate ``TemplateDoesNotExist`` exceptions, used to carry failures
        from several engines at once rather than only the last.
    """

    def __init__(self, msg, tried=None, backend=None, chain=None):
        self.backend = backend
        self.tried = [] if tried is None else tried
        self.chain = [] if chain is None else chain
        super().__init__(msg)


class TemplateSyntaxError(Exception):
    """Raised for syntax errors during parsing or rendering."""
