"""The Django template backend — the one and only way this engine is activated.

A project uses this package by naming it in settings::

    TEMPLATES = [
        {
            "BACKEND": "django_rust_templates.rust.RustTemplates",
            "DIRS": [BASE_DIR / "templates"],
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": [...]},
        }
    ]

and nothing else. There is no import to perform, no finder to install, no
``AppConfig.ready()`` hook and no entry point: ``EngineHandler`` resolves the
string above with ``django.utils.module_loading.import_string``, which splits at
the last dot and finds the class in this module.

**This file is modelled on ``django/template/backends/django.py``, and its shape is
deliberately identical.** That module is ``NOT_THE_ENGINE`` — it is Django's own
backend, a thin adapter between the settings dict and ``django.template.Engine`` —
so it is not transcribed anywhere and cannot be reached from our namespace. But every
behaviour below it is behaviour a project already depends on, so the copy is
line-for-line wherever it can be, and the three places it could not be are each
recorded at the point they occur:

* the engine, ``make_context`` and ``TemplateDoesNotExist`` are ours, so the
  imports are ours;
* the built-in tag libraries are searched in our package rather than in
  ``django.templatetags`` (see [`BUILTIN_LIBRARY_PACKAGE`], the one substitution
  that changes what a project can ``{% load %}`);
* the class is called ``RustTemplates``, which is also the string a project
  writes in settings, and it subclasses ``DjangoTemplates``.

**The subclass is a marker, not an inheritance.** Every method ``DjangoTemplates``
defines is overridden below, so none of its implementation is reached; what the
subclass buys is ``isinstance``. That is how Django and its ecosystem ask "is this
backend the DTL one?" — ``django.contrib.admin.checks.check_admin_app`` raises
``admin.E403`` unless some configured backend is an instance of it, and
``django.template.autoreload`` asks it **three times, in two functions**:
``get_template_directories`` once and ``reset_loaders`` twice, both of which the
module's two ``@receiver`` callbacks call, and both of which return early unless
the answer is yes. (The count is three checks, not three receivers — ``autoreload``
has two of those, and writing "receivers" here is how the two numbers get
conflated.) So a backend that does not subclass it is unusable with
``django.contrib.admin`` and invisible to
the template autoreloader. Neither is discoverable from this file, and both surfaced
as failures in the vendored suite.

**The wrapper class is called ``Template`` and shadows nothing**, because it is not
in the engine's namespace: the engine's ``Template`` is
``django_rust_templates.engine.base.Template`` and lives behind
``django_rust_templates.engine``. Both names existed in upstream for the same
reason — the backend-level object is a *settings-scoped* wrapper that carries the
backend (so ``TemplateDoesNotExist.backend`` can be filled in) while the engine-level
object is the compiled template.
"""

from collections import defaultdict
from importlib import import_module
from pkgutil import walk_packages

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Warning
from django.template.backends.base import BaseEngine
from django.template.backends.django import DjangoTemplates

from django_rust_templates import _require_django
from django_rust_templates.engine.context import make_context
from django_rust_templates.engine.engine import Engine
from django_rust_templates.engine.exceptions import TemplateDoesNotExist
from django_rust_templates.engine.library import InvalidTemplateLibrary

__all__ = ("RustTemplates", "Template")


# Where ``{% load i18n %}`` finds a library that Django also ships.
#
# **This is a substitution and not a fallback, and it is the one line in this file
# that a project can observe.** Upstream's backend walks ``django.templatetags`` for
# the built-ins; this package carries its own transcription of all five of those
# modules (``cache``, ``i18n``, ``l10n``, ``static``, ``tz``) because ``host.py``
# interposes them, so a project loading one of them must get *ours* — the same
# library the engine's own ``{% load %}`` resolution reaches.
#
# Searching both packages instead would be a defect rather than a belt-and-braces,
# and a *reported* one: ``_check_for_template_tags_with_the_same_name`` emits
# ``templates.W003`` — "used for multiple template tag modules" — for any library
# name found twice, so every project using this backend would see five warnings
# from ``manage.py check`` whether or not it ever loads a library.
BUILTIN_LIBRARY_PACKAGE = "django_rust_templates.engine.templatetags"


class RustTemplates(DjangoTemplates):
    app_dirname = "templates"

    def __init__(self, params):
        params = params.copy()
        options = params.pop("OPTIONS").copy()

        # **First, before the engine is built and before anything is validated.**
        # This is the one door into the package — `EngineHandler` constructs the
        # backend when the engine is first asked for — so one call here covers
        # every route in, and it is the earliest point at which the check can be
        # made from inside the package. `_require_django` carries the measurements
        # and the reason it is a version check at all.
        #
        # `allow_django_mismatch` is popped rather than passed on: `Engine` takes
        # an explicit keyword list and would raise `TypeError` on it, which is the
        # right failure for an unknown option and the wrong one for this.
        _require_django(options.pop("allow_django_mismatch", False))

        options.setdefault("autoescape", True)
        options.setdefault("debug", settings.DEBUG)
        options.setdefault("file_charset", "utf-8")
        libraries = options.get("libraries", {})
        options["libraries"] = self.get_templatetag_libraries(libraries)
        # **`BaseEngine.__init__`, named, rather than `super().__init__`.** The
        # marker superclass above defines an `__init__` that pops `OPTIONS` itself
        # and then builds *Django's* engine; `super()` would therefore reach it and
        # raise `KeyError: 'OPTIONS'` on the copy this method already stripped. What
        # this backend wants from its bases is the settings-shaped part — `name`,
        # `dirs`, `app_dirs` and the rejection of unknown keys — and that is
        # `BaseEngine`'s.
        BaseEngine.__init__(self, params)
        self.engine = Engine(self.dirs, self.app_dirs, **options)

    def check(self, **kwargs):
        return [
            *self._check_string_if_invalid_is_string(),
            *self._check_for_template_tags_with_the_same_name(),
        ]

    def _check_string_if_invalid_is_string(self):
        value = self.engine.string_if_invalid
        if not isinstance(value, str):
            return [
                Error(
                    "'string_if_invalid' in TEMPLATES OPTIONS must be a string but "
                    "got: %r (%s)." % (value, type(value)),
                    obj=self,
                    id="templates.E002",
                )
            ]
        return []

    def _check_for_template_tags_with_the_same_name(self):
        libraries = defaultdict(set)

        for module_name, module_path in get_template_tag_modules():
            libraries[module_name].add(module_path)

        for module_name, module_path in self.engine.libraries.items():
            libraries[module_name].add(module_path)

        errors = []

        for library_name, items in libraries.items():
            if len(items) > 1:
                items = ", ".join(repr(item) for item in sorted(items))
                errors.append(
                    Warning(
                        f"{library_name!r} is used for multiple template tag modules: "
                        f"{items}",
                        obj=self,
                        id="templates.W003",
                    )
                )

        return errors

    def from_string(self, template_code):
        return Template(self.engine.from_string(template_code), self)

    def get_template(self, template_name):
        try:
            return Template(self.engine.get_template(template_name), self)
        except TemplateDoesNotExist as exc:
            reraise(exc, self)

    def get_templatetag_libraries(self, custom_libraries):
        """
        Return a collation of template tag libraries from installed
        applications and the supplied custom_libraries argument.
        """
        libraries = get_installed_libraries()
        libraries.update(custom_libraries)
        return libraries


class Template:
    def __init__(self, template, backend):
        self.template = template
        self.backend = backend

    @property
    def origin(self):
        return self.template.origin

    def render(self, context=None, request=None):
        context = make_context(
            context, request, autoescape=self.backend.engine.autoescape
        )
        try:
            return self.template.render(context)
        except TemplateDoesNotExist as exc:
            reraise(exc, self.backend)


def copy_exception(exc, backend=None):
    """
    Create a new TemplateDoesNotExist. Preserve its declared attributes and
    template debug data but discard __traceback__, __context__, and __cause__
    to make this object suitable for keeping around (in a cache, for example).
    """
    backend = backend or exc.backend
    new = exc.__class__(*exc.args, tried=exc.tried, backend=backend, chain=exc.chain)
    if hasattr(exc, "template_debug"):
        new.template_debug = exc.template_debug
    return new


def reraise(exc, backend):
    """
    Reraise TemplateDoesNotExist while maintaining template debug information.
    """
    new = copy_exception(exc, backend)
    raise new from exc


def get_template_tag_modules():
    """
    Yield (module_name, module_path) pairs for all installed template tag
    libraries.
    """
    candidates = [BUILTIN_LIBRARY_PACKAGE]
    candidates.extend(
        f"{app_config.name}.templatetags" for app_config in apps.get_app_configs()
    )

    for candidate in candidates:
        try:
            pkg = import_module(candidate)
        except ImportError:
            # No templatetags package defined. This is safe to ignore.
            continue

        if hasattr(pkg, "__path__"):
            for name in get_package_libraries(pkg):
                yield name.removeprefix(candidate).lstrip("."), name


def get_installed_libraries():
    """
    Return the built-in template tag libraries and those from installed
    applications. Libraries are stored in a dictionary where keys are the
    individual module names, not the full module paths. Example,
    django_rust_templates.engine.templatetags.i18n is stored as i18n.
    """
    return {
        module_name: full_name for module_name, full_name in get_template_tag_modules()
    }


def get_package_libraries(pkg):
    """
    Recursively yield template tag libraries defined in submodules of a
    package.
    """
    for entry in walk_packages(pkg.__path__, pkg.__name__ + "."):
        try:
            module = import_module(entry[1])
        except ImportError as e:
            raise InvalidTemplateLibrary(
                "Invalid template library specified. ImportError raised when "
                "trying to load '%s': %s" % (entry[1], e)
            ) from e

        if hasattr(module, "register"):
            yield entry[1]
