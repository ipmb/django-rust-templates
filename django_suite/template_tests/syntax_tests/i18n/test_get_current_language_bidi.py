from template_tests.utils import setup

from django_rust_templates.engine import TemplateSyntaxError
from django.test import SimpleTestCase


class I18nGetCurrentLanguageBidiTagTests(SimpleTestCase):
    libraries = {"i18n": "django_rust_templates.engine.templatetags.i18n"}

    @setup({"template": "{% load i18n %} {% get_current_language_bidi %}"})
    def test_no_as_var(self):
        msg = (
            "'get_current_language_bidi' requires 'as variable' (got "
            "['get_current_language_bidi'])"
        )
        with self.assertRaisesMessage(TemplateSyntaxError, msg):
            self.engine.render_to_string("template")
