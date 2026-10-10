# TRANSCRIBED: django/template/engine.py (Django 6.0.9)

from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import cached_property
from django.utils.module_loading import import_string

from .base import Template
from .context import Context, _builtin_context_processors
from .exceptions import TemplateDoesNotExist
from .library import import_library


class Engine:
    default_builtins = [
        "django_rust_templates.engine.defaulttags",
        "django_rust_templates.engine.defaultfilters",
        "django_rust_templates.engine.loader_tags",
    ]

    def __init__(
        self,
        dirs=None,
        app_dirs=False,
        context_processors=None,
        debug=False,
        loaders=None,
        string_if_invalid="",
        file_charset="utf-8",
        libraries=None,
        builtins=None,
        autoescape=True,
    ):
        if dirs is None:
            dirs = []
        if context_processors is None:
            context_processors = []
        if loaders is None:
            loaders = ["django_rust_templates.engine.loaders.filesystem.Loader"]
            if app_dirs:
                loaders += [
                    "django_rust_templates.engine.loaders.app_directories.Loader"
                ]
            loaders = [
                ("django_rust_templates.engine.loaders.cached.Loader", loaders)
            ]
        else:
            if app_dirs:
                raise ImproperlyConfigured(
                    "app_dirs must not be set when loaders is defined."
                )
        if libraries is None:
            libraries = {}
        if builtins is None:
            builtins = []

        self.dirs = dirs
        self.app_dirs = app_dirs
        self.autoescape = autoescape
        self.context_processors = context_processors
        self.debug = debug
        self.loaders = loaders
        self.string_if_invalid = string_if_invalid
        self.file_charset = file_charset
        self.libraries = libraries
        self.template_libraries = self.get_template_libraries(libraries)
        self.builtins = self.default_builtins + builtins
        self.template_builtins = self.get_template_builtins(self.builtins)

    def __repr__(self):
        return (
            "<%s:%s app_dirs=%s%s debug=%s loaders=%s string_if_invalid=%s "
            "file_charset=%s%s%s autoescape=%s>"
        ) % (
            self.__class__.__qualname__,
            "" if not self.dirs else " dirs=%s" % repr(self.dirs),
            self.app_dirs,
            (
                ""
                if not self.context_processors
                else " context_processors=%s" % repr(self.context_processors)
            ),
            self.debug,
            repr(self.loaders),
            repr(self.string_if_invalid),
            repr(self.file_charset),
            "" if not self.libraries else " libraries=%s" % repr(self.libraries),
            "" if not self.builtins else " builtins=%s" % repr(self.builtins),
            repr(self.autoescape),
        )

    # **Upstream caches this answer and this one deliberately does not**, and
    # the difference is not an optimisation traded away. ``get_default`` reads
    # ``settings.TEMPLATES`` through the engine registry, so its answer is a
    # function of a setting; upstream can cache that because Django's own
    # ``django.test.signals.reset_template_engines`` receiver calls
    # ``django.template.engine.Engine.get_default.cache_clear()`` on every
    # ``TEMPLATES``, ``DEBUG`` or ``INSTALLED_APPS`` change. That receiver names
    # *Django's* class object, and this package's ``Engine`` is a different
    # class object, so a cache here is one nothing can ever invalidate: it would
    # hold the settings from the first call for the life of the process.
    # Measured, as three red rows in ``test_engine.GetDefaultTests`` — with
    # ``override_settings(TEMPLATES=[...])`` in force, ``get_default()`` returned
    # the engine built from the *unoverridden* settings, and
    # ``test_no_engines_configured`` could not raise because a previous answer
    # was already memoised.
    #
    # The work it saves is ``EngineHandler.all()``, which is a dict lookup per
    # configured backend against a registry that caches the backends themselves
    # — so the uncached version is correct by construction and cheap by
    # measurement, which is the right way round.
    @staticmethod
    def get_default():
        """
        Return the first DjangoTemplates backend that's configured, or raise
        ImproperlyConfigured if none are configured.

        This is required for preserving historical APIs that rely on a
        globally available, implicitly configured engine such as:

        >>> from django.template import Context, Template
        >>> template = Template("Hello {{ name }}!")
        >>> context = Context({'name': "world"})
        >>> template.render(context)
        'Hello world!'
        """
        # Since Engine is imported in django.template and since
        # DjangoTemplates is a wrapper around this Engine class,
        # local imports are required to avoid import loops.
        from django.template import engines
        from django.template.backends.django import DjangoTemplates

        from django_rust_templates.rust import RustTemplates

        # **``RustTemplates`` is the second arm, and it is not decoration.** This
        # method exists for the historical API that has no engine to hand —
        # `Template(...)` at module scope, `{% extends %}` in code that never saw
        # settings — and a caller who configured this package and then used it
        # would otherwise be handed *stock Django's* engine: the loop would find
        # no `DjangoTemplates` and raise, or find one configured alongside ours
        # and return the wrong engine, in both cases without a traceback pointing
        # anywhere near the cause. Upstream's loop is single-armed because upstream
        # has one backend; the package ships a second.
        for engine in engines.all():
            if isinstance(engine, (DjangoTemplates, RustTemplates)):
                return engine.engine
        # Upstream's wording, and pinned as such: `template_tests.test_engine`
        # asserts this exact string, so changing it to name both backends would
        # buy a clearer message at the price of a red acceptance run. It is also
        # reachable only when *neither* backend is configured, which is the case
        # it describes.
        raise ImproperlyConfigured("No DjangoTemplates backend is configured.")

    @cached_property
    def template_context_processors(self):
        context_processors = _builtin_context_processors
        context_processors += tuple(self.context_processors)
        return tuple(import_string(path) for path in context_processors)

    def get_template_builtins(self, builtins):
        return [import_library(x) for x in builtins]

    def get_template_libraries(self, libraries):
        loaded = {}
        for name, path in libraries.items():
            loaded[name] = import_library(path)
        return loaded

    @cached_property
    def template_loaders(self):
        return self.get_template_loaders(self.loaders)

    def get_template_loaders(self, template_loaders):
        loaders = []
        for template_loader in template_loaders:
            loader = self.find_template_loader(template_loader)
            if loader is not None:
                loaders.append(loader)
        return loaders

    def find_template_loader(self, loader):
        if isinstance(loader, (tuple, list)):
            loader, *args = loader
        else:
            args = []

        if isinstance(loader, str):
            loader_class = import_string(loader)
            return loader_class(self, *args)
        else:
            raise ImproperlyConfigured(
                "Invalid value in template loaders configuration: %r" % loader
            )

    def find_template(self, name, dirs=None, skip=None):
        tried = []
        for loader in self.template_loaders:
            try:
                template = loader.get_template(name, skip=skip)
                return template, template.origin
            except TemplateDoesNotExist as e:
                tried.extend(e.tried)
        raise TemplateDoesNotExist(name, tried=tried)

    def from_string(self, template_code):
        """
        Return a compiled Template object for the given template code,
        handling template inheritance recursively.
        """
        return Template(template_code, engine=self)

    def get_template(self, template_name):
        """
        Return a compiled Template object for the given template name,
        handling template inheritance recursively.
        """
        original_name = template_name
        try:
            template_name, _, partial_name = template_name.partition("#")
        except AttributeError:
            raise TemplateDoesNotExist(original_name)

        if not template_name:
            raise TemplateDoesNotExist(original_name)

        template, origin = self.find_template(template_name)
        if not hasattr(template, "render"):
            # template needs to be compiled
            template = Template(template, origin, template_name, engine=self)

        if not partial_name:
            return template

        extra_data = getattr(template, "extra_data", {})
        try:
            partial = extra_data["partials"][partial_name]
        except (KeyError, TypeError):
            raise TemplateDoesNotExist(partial_name, tried=[template_name])
        partial.engine = self

        return partial

    def render_to_string(self, template_name, context=None):
        """
        Render the template specified by template_name with the given context.
        For use in Django's test suite.
        """
        if isinstance(template_name, (list, tuple)):
            t = self.select_template(template_name)
        else:
            t = self.get_template(template_name)
        # Django < 1.8 accepted a Context in `context` even though that's
        # unintended. Preserve this ability but don't rewrap `context`.
        if isinstance(context, Context):
            return t.render(context)
        else:
            return t.render(Context(context, autoescape=self.autoescape))

    def select_template(self, template_name_list):
        """
        Given a list of template names, return the first that can be loaded.
        """
        if not template_name_list:
            raise TemplateDoesNotExist("No template names provided")
        not_found = []
        for template_name in template_name_list:
            try:
                return self.get_template(template_name)
            except TemplateDoesNotExist as exc:
                if exc.args[0] not in not_found:
                    not_found.append(exc.args[0])
                continue
        # If we get here, none of the templates could be loaded
        raise TemplateDoesNotExist(", ".join(not_found))
