"""Every configured loader must build *this* engine's `Template`.

The failure these rows exist for is silent and was measured rather than imagined:
with `OPTIONS["loaders"]` naming `django.template.loaders.cached.Loader`, a real
site (bakerydemo) compiled every template with Django's parser, rendered
correctly, and reported an empty native census — an engine doing nothing, with
no error anywhere. So the assertions are about the **class that comes out**, not
about output, which is identical either way and therefore proves nothing.
"""

import unittest

from django.core.exceptions import ImproperlyConfigured
from django.template.loaders.base import Loader as DjangoBaseLoader
from django.test import SimpleTestCase, override_settings

from django_rust_templates.engine.base import Template as OurTemplate
from django_rust_templates.engine.loaders.base import Loader as OurBaseLoader
from django_rust_templates.loaders import ALIASES, AdoptedLoader, adapt_loaders
from django_rust_templates.rust import RustTemplates

BACKEND = "django_rust_templates.rust.RustTemplates"
DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"
TEMPLATES = {"index.html": "<p>{{ x|upper }}</p>{% if x %}<b>y</b>{% endif %}"}


def backend(loaders, dirs=()):
    return RustTemplates(
        {
            "NAME": "rust",
            "DIRS": list(dirs),
            "APP_DIRS": False,
            "OPTIONS": {"loaders": loaders},
        }
    )


class ForeignLoaderThatOnlyLocates(DjangoBaseLoader):
    """A third-party loader of the common shape: origins and contents, no more."""

    def get_template_sources(self, template_name):
        from django.template import Origin

        if template_name in TEMPLATES:
            yield Origin(
                name=f"memory:{template_name}",
                template_name=template_name,
                loader=self,
            )

    def get_contents(self, origin):
        from django.template import TemplateDoesNotExist

        try:
            return TEMPLATES[origin.template_name]
        except KeyError:
            raise TemplateDoesNotExist(origin) from None


class ThirdPartyLoaderWrittenAgainstUs(OurBaseLoader):
    """Outside this package, but builds our `Template` because it subclasses ours."""

    def get_template_sources(self, template_name):
        return iter(())


class ForeignLoaderThatBuildsTemplates(ForeignLoaderThatOnlyLocates):
    """A loader that binds an engine itself, so this package must refuse it."""

    def get_template(self, template_name, skip=None):
        raise AssertionError("never reached: the backend must refuse this loader")


class LoaderAdaptationTests(SimpleTestCase):
    def test_djangos_own_loaders_are_aliased_to_ours(self):
        for django_path, our_path in ALIASES.items():
            with self.subTest(loader=django_path):
                self.assertEqual(adapt_loaders([django_path]), [our_path])

    def test_sub_loaders_of_the_caching_loader_are_aliased_too(self):
        # The nesting case: the caching loader's first argument is itself a list
        # of specs, and a sub-loader left as Django's would build Django's
        # Template for everything that loader found.
        adapted = adapt_loaders(
            [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ]
        )
        self.assertEqual(
            adapted,
            [
                (
                    ALIASES["django.template.loaders.cached.Loader"],
                    [
                        ALIASES["django.template.loaders.filesystem.Loader"],
                        ALIASES["django.template.loaders.app_directories.Loader"],
                    ],
                )
            ],
        )

    def test_a_loader_of_ours_is_left_alone(self):
        path = "django_rust_templates.engine.loaders.filesystem.Loader"
        self.assertEqual(adapt_loaders([path]), [path])

    def test_a_foreign_loader_that_only_locates_is_wrapped(self):
        path = f"{__name__}.ForeignLoaderThatOnlyLocates"
        self.assertEqual(
            adapt_loaders([path]),
            [("django_rust_templates.loaders.AdoptedLoader", path)],
        )

    def test_a_loaders_own_arguments_survive_the_wrap(self):
        path = f"{__name__}.ForeignLoaderThatOnlyLocates"
        self.assertEqual(
            adapt_loaders([(path, "a", "b")]),
            [("django_rust_templates.loaders.AdoptedLoader", path, "a", "b")],
        )

    def test_a_foreign_loader_that_builds_templates_is_refused_by_name(self):
        path = f"{__name__}.ForeignLoaderThatBuildsTemplates"
        with self.assertRaises(ImproperlyConfigured) as caught:
            adapt_loaders([path])
        # The class has to be named: the whole point is that the alternative is a
        # silent no-op, so the message must say which entry to change.
        self.assertIn(path, str(caught.exception))

    def test_a_third_party_loader_written_against_this_package_is_left_alone(self):
        # Subclassing our base means `get_template` already builds our `Template`,
        # so the loader is used as-is even though its path is not ours. This is
        # the escape hatch the refusal message points a third party at.
        path = f"{__name__}.ThirdPartyLoaderWrittenAgainstUs"
        self.assertTrue(issubclass(ThirdPartyLoaderWrittenAgainstUs, OurBaseLoader))
        self.assertEqual(adapt_loaders([path]), [path])


class ConfiguredBackendTests(SimpleTestCase):
    """The end the adaptation exists for: what `get_template` hands back."""

    def test_naming_djangos_cached_loader_still_yields_our_template(self):
        engine = backend(
            [
                (
                    "django.template.loaders.cached.Loader",
                    [("django.template.loaders.locmem.Loader", TEMPLATES)],
                )
            ]
        )
        template = engine.get_template("index.html")
        self.assertIsInstance(template.template, OurTemplate)

    def test_a_wrapped_foreign_loader_yields_our_template_and_renders(self):
        engine = backend([f"{__name__}.ForeignLoaderThatOnlyLocates"])
        template = engine.get_template("index.html")
        self.assertIsInstance(template.template, OurTemplate)
        self.assertEqual(template.render({"x": "hi"}), "<p>HI</p><b>y</b>")

    def test_a_wrapped_loader_reports_a_miss_as_django_does(self):
        from django.template import TemplateDoesNotExist

        engine = backend([f"{__name__}.ForeignLoaderThatOnlyLocates"])
        with self.assertRaises(TemplateDoesNotExist):
            engine.get_template("absent.html")

    def test_the_wrapper_is_only_used_where_it_is_needed(self):
        engine = backend([f"{__name__}.ForeignLoaderThatOnlyLocates"])
        self.assertIsInstance(engine.engine.template_loaders[0], AdoptedLoader)
        engine = backend([("django.template.loaders.locmem.Loader", TEMPLATES)])
        self.assertNotIsInstance(engine.engine.template_loaders[0], AdoptedLoader)


class BackendOrderingCheckTests(SimpleTestCase):
    """`render_to_string(using=None)` resolves to the FIRST backend."""

    def ids(self, errors):
        return [error.id for error in errors]

    @override_settings(
        TEMPLATES=[
            {"BACKEND": DJANGO_BACKEND, "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
            {"BACKEND": BACKEND, "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
        ]
    )
    def test_warns_when_another_backend_is_configured_first(self):
        engine = RustTemplates(
            {"NAME": "rust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        self.assertIn("django_rust_templates.W001", self.ids(engine.check()))

    @override_settings(
        TEMPLATES=[
            {"BACKEND": BACKEND, "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
            {"BACKEND": DJANGO_BACKEND, "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
        ]
    )
    def test_silent_when_this_backend_is_first(self):
        engine = RustTemplates(
            {"NAME": "rust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        self.assertNotIn("django_rust_templates.W001", self.ids(engine.check()))

    @override_settings(
        TEMPLATES=[{"BACKEND": BACKEND, "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}]
    )
    def test_silent_when_it_is_the_only_backend(self):
        engine = RustTemplates(
            {"NAME": "rust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        self.assertNotIn("django_rust_templates.W001", self.ids(engine.check()))


class FormRendererCheckTests(SimpleTestCase):
    """`FORM_RENDERER` builds a private engine the TEMPLATES setting cannot reach."""

    def ids(self, errors):
        return [error.id for error in errors]

    def engine(self):
        return RustTemplates(
            {"NAME": "rust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )

    @override_settings(FORM_RENDERER="django.forms.renderers.DjangoTemplates")
    def test_warns_for_the_default_private_renderer(self):
        self.assertIn("django_rust_templates.W002", self.ids(self.engine().check()))

    @override_settings(FORM_RENDERER="django.forms.renderers.Jinja2")
    def test_warns_for_the_private_jinja2_renderer(self):
        self.assertIn("django_rust_templates.W002", self.ids(self.engine().check()))

    @override_settings(FORM_RENDERER="django.forms.renderers.TemplatesSetting")
    def test_silent_when_widgets_go_through_TEMPLATES(self):
        self.assertNotIn("django_rust_templates.W002", self.ids(self.engine().check()))


if __name__ == "__main__":
    unittest.main()
