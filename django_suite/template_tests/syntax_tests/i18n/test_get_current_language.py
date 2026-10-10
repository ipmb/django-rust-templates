from template_tests.utils import setup

from django_rust_templates.engine import TemplateSyntaxError
from django.test import SimpleTestCase


class I18nGetCurrentLanguageTagTests(SimpleTestCase):
    libraries = {"i18n": "django_rust_templates.engine.templatetags.i18n"}

    @setup({"template": "{% load i18n %} {% get_current_language %}"})
    def test_no_as_var(self):
        msg = (
            "'get_current_language' requires 'as variable' (got "
            "['get_current_language'])"
        )
        with self.assertRaisesMessage(TemplateSyntaxError, msg):
            self.engine.render_to_string("template")
