"""Make a project's configured loaders build *this* engine's templates.

**The failure this module exists to prevent is silent.** A loader's one
engine-binding method is ``get_template``: everything else it does is find
origins and read source text. Django's own
``django.template.loaders.base.Loader.get_template`` ends in
``django.template.base.Template(...)`` — *Django's* class, compiled by Django's
parser. So a project that names Django's loader classes in ``OPTIONS["loaders"]``
gets Django's template engine back, with no error and no warning, and the only
symptom is that this package appears to do nothing. Measured against the
bakerydemo site: with ``OPTIONS["loaders"]`` naming
``django.template.loaders.cached.Loader``, every template came back as
``django.template.base.Template`` and the native census read empty.

Three outcomes, decided by what the named class is:

* **ours** — used unchanged. That includes a third-party loader that subclasses
  this package's :class:`~django_rust_templates.engine.loaders.base.Loader`,
  since it already builds the right class.
* **Django's own four** — aliased to this package's transcriptions of them
  ([`ALIASES`]). The transcription is the same code, so the substitution cannot
  change behaviour; it only changes which ``Template`` comes out.
* **anything else** — wrapped in [`AdoptedLoader`] when it only supplies origins
  and contents, and **refused** when it overrides ``get_template``, because then
  what it builds is its own business and this package cannot know it is right.

``TemplateDoesNotExist`` needs no translation across the wrap: this package's
``engine.exceptions`` re-exports *Django's* class rather than defining one, so a
wrapped loader's misses are already the exception our base catches. ``Origin`` is
likewise compatible — ours is a transcription with the same two attributes
``__eq__`` reads.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from django_rust_templates.engine.loaders.base import Loader

__all__ = ("ALIASES", "AdoptedLoader", "adapt_loaders")

_PACKAGE = "django_rust_templates."

#: Django's four loaders, mapped to this package's transcriptions of them. The
#: pairs are the same code — ``scripts/check_transcriptions.py`` is what keeps
#: that true — so this substitution is a change of ``Template`` class and
#: nothing else.
ALIASES = {
    f"django.template.loaders.{name}.Loader": f"{_PACKAGE}engine.loaders.{name}.Loader"
    for name in ("filesystem", "app_directories", "cached", "locmem")
}

#: The aliased path of the caching loader, whose first argument is a list of
#: loader specs rather than a value. It is the only nesting case: Django's other
#: three take directories or a dict, and a blanket recursion would rewrite a
#: template *directory* that happened to look like a dotted path.
_CACHED = ALIASES["django.template.loaders.cached.Loader"]


def _django_base_get_template():
    """``django.template.loaders.base.Loader.get_template``, the unoverridden one."""
    from django.template.loaders.base import Loader as DjangoLoader

    return DjangoLoader.get_template


def _builds_its_own_template(cls):
    """Whether ``cls`` binds an engine by overriding ``get_template``.

    The test is against Django's base implementation rather than a name, because
    the inherited function is shared by every loader that only supplies origins
    and contents — Django's ``filesystem``, ``app_directories`` and ``locmem``
    among them, and every third-party loader shaped like them.
    """
    return cls.get_template is not _django_base_get_template()


class AdoptedLoader(Loader):
    """A foreign loader, used for everything except building the ``Template``.

    Constructed from the dotted path of the loader it wraps plus that loader's
    own arguments, so it is expressible as an ordinary entry in
    ``OPTIONS["loaders"]`` and needs no change to ``Engine.find_template_loader``.

    ``get_template`` is **not** defined here: it is inherited from this package's
    base loader, which is the whole point — the origins and the source text are
    the wrapped loader's, and the ``Template`` built from them is ours.
    """

    def __init__(self, engine, path, *args):
        super().__init__(engine)
        self.path = path
        self.inner = import_string(path)(engine, *args)

    def __repr__(self):
        return f"<{type(self).__name__} wrapping {self.path}>"

    def get_template_sources(self, template_name):
        return self.inner.get_template_sources(template_name)

    def get_contents(self, origin):
        return self.inner.get_contents(origin)

    def get_dirs(self):
        # Consulted by the caching loader, which yields from every sub-loader that
        # has one. A wrapped loader need not: `locmem` has no directories.
        get_dirs = getattr(self.inner, "get_dirs", None)
        return get_dirs() if get_dirs is not None else []

    def reset(self):
        # The autoreload receiver walks loaders and resets the ones that can be.
        # A wrapped loader that caches anything of its own must still hear it.
        reset = getattr(self.inner, "reset", None)
        if reset is not None:
            reset()


def adapt_loaders(specs):
    """Rewrite a configured ``loaders`` list so every entry builds our ``Template``.

    Returns a new list; the caller's is not mutated. Raises
    ``ImproperlyConfigured`` for a loader this package cannot safely adopt, which
    is the case it must not pass over quietly — a refusal names the class and is
    fixable, where the silent alternative is an engine that does nothing.
    """
    return [_adapt(spec) for spec in specs]


def _adapt(spec):
    if isinstance(spec, (list, tuple)):
        if not spec:
            return spec
        path, *args = spec
    else:
        path, args = spec, []

    # Not a string: `Engine.find_template_loader` raises its own
    # `ImproperlyConfigured` for this, and that message is Django's.
    if not isinstance(path, str):
        return spec

    path = ALIASES.get(path, path)

    if path == _CACHED and args:
        # The caching loader's first argument is its sub-loaders, which are specs
        # in their own right and get the same three-way treatment.
        args = [adapt_loaders(args[0]), *args[1:]]

    if path.startswith(_PACKAGE):
        return (path, *args) if args else path

    cls = import_string(path)

    if issubclass(cls, Loader):
        # A third-party loader written against this package already builds the
        # right class, whatever else it overrides.
        return (path, *args) if args else path

    if _builds_its_own_template(cls):
        raise ImproperlyConfigured(
            f"{path!r} cannot be used with this template backend. It overrides "
            f"get_template(), so it builds its own Template class, and this "
            f"engine cannot know that the result was compiled by it rather than "
            f"by Django. Loaders that only locate templates "
            f"(get_template_sources/get_contents) are adopted automatically; "
            f"subclass django_rust_templates.engine.loaders.base.Loader to take "
            f"responsibility for building the Template yourself."
        )

    return (f"{_PACKAGE}loaders.AdoptedLoader", path, *args)
