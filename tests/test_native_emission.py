"""Native emission preserves Django's safe strings and localized numbers."""

import sys
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

from django.template import Context as DjangoContext, Engine as DjangoEngine
from django.test import override_settings
from django.utils.safestring import SafeString
from django.utils.translation import override

from django_rust_templates.engine import Context, Engine
from django_rust_templates.engine import _stock, base


class NativeEmissionTests(unittest.TestCase):
    def test_safe_string_changed_before_backend_import_is_not_licensed(self):
        script = """
import sys
sys.path.insert(0, sys.argv[2])
from scripts.filter_probe import ensure_settings
ensure_settings()
from django.template import Engine as DE, Context as DC
from django.utils.safestring import SafeString
original = SafeString.__getattribute__
name = sys.argv[1]
if name == '__getattribute__':
    def replacement(self, attr):
        if attr == '__html__':
            return lambda: '<changed attribute>'
        return original(self, attr)
else:
    def replacement(self):
        return '<changed method>'
setattr(SafeString, name, replacement)
from django_rust_templates.engine import Engine, Context, _stock
source = 'before{{ value }}after'
for escape in (False, True):
    data = {'value': SafeString('<original>')}
    expected = DE().from_string(source).render(DC(data, autoescape=escape))
    actual = Engine().from_string(source).render(Context(data, autoescape=escape))
    assert actual == expected, (actual, expected)
"""
        for name in ("__str__", "__html__", "__getattribute__"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    script,
                    name,
                    str(Path(__file__).resolve().parent.parent),
                ],
                capture_output=True,
                text=True,
            )
            with self.subTest(method=name):
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_subscript_absence_check_honors_metaclass_hooks_and_descriptors(self):
        for hook in ("getattr", "getattribute", "descriptor", "plain"):
            trace = []

            class Meta(type):
                pass

            if hook == "getattr":

                def getattr_hook(cls, name):
                    if name == "__getitem__":
                        trace.append("getattr")
                    raise AttributeError(name)

                Meta.__getattr__ = getattr_hook
            elif hook == "getattribute":

                def getattribute_hook(cls, name):
                    if name == "__getitem__":
                        trace.append("getattribute")
                    return type.__getattribute__(cls, name)

                Meta.__getattribute__ = getattribute_hook

            class Value(metaclass=Meta):
                value = "found"

            if hook == "descriptor":

                class Descriptor:
                    def __get__(self, instance, owner):
                        trace.append("descriptor")
                        raise AttributeError("__getitem__")

                Value.__getitem__ = Descriptor()

            source = "{{ obj.value }}"
            expected = (
                DjangoEngine()
                .from_string(source)
                .render(DjangoContext({"obj": Value()}))
            )
            expected_trace = trace[:]
            trace.clear()
            actual = Engine().from_string(source).render(Context({"obj": Value()}))
            with self.subTest(hook=hook):
                self.assertEqual(actual, expected)
                self.assertEqual(trace, expected_trace)

    def compare(self, value, *, autoescape=True, use_l10n=None):
        source = "before{{ value }}after"
        expected = (
            DjangoEngine()
            .from_string(source)
            .render(
                DjangoContext(
                    {"value": value}, autoescape=autoescape, use_l10n=use_l10n
                )
            )
        )
        actual = (
            Engine()
            .from_string(source)
            .render(Context({"value": value}, autoescape=autoescape, use_l10n=use_l10n))
        )
        self.assertEqual(actual, expected)
        self.assertIs(type(actual), type(expected))

    def test_safe_strings_and_surrogates(self):
        for value in (SafeString("<b>&</b>"), SafeString(""), SafeString("\ud800")):
            for autoescape in (False, True):
                with self.subTest(value=repr(value), autoescape=autoescape):
                    self.compare(value, autoescape=autoescape)

    def test_subclasses_keep_their_conversion_hooks(self):
        class CustomSafeString(SafeString):
            def __html__(self):
                return "<custom>"

            def __str__(self):
                return "custom text"

        for autoescape in (False, True):
            self.compare(CustomSafeString("original"), autoescape=autoescape)

    def test_rebound_methods_keep_their_conversion_hooks(self):
        # The pin must predate the rebind, including before the first native render.
        self.assertIs(_stock.SAFE_STRING, SafeString)
        value = SafeString("<original>")
        original_getattribute = SafeString.__getattribute__

        def changed_getattribute(self, name):
            if name == "__html__":
                return lambda: "<attribute hook>"
            return original_getattribute(self, name)

        for name, method in (
            ("__html__", lambda self: "<html hook>"),
            ("__str__", lambda self: "str hook"),
            ("__getattribute__", changed_getattribute),
        ):
            with patch.object(SafeString, name, method):
                for autoescape in (False, True):
                    with self.subTest(method=name, autoescape=autoescape):
                        self.compare(value, autoescape=autoescape)

    def test_integer_grouping_and_locale_are_read_live(self):
        class CustomInt(int):
            def __str__(self):
                return "custom int"

        for grouping in (False, True):
            with override_settings(USE_THOUSAND_SEPARATOR=grouping):
                for language in ("en", "de"):
                    with override(language):
                        for use_l10n in (False, True, None):
                            for autoescape in (False, True):
                                for value in (
                                    -12345,
                                    0,
                                    12345,
                                    10**100,
                                    True,
                                    CustomInt(7),
                                ):
                                    with self.subTest(
                                        grouping=grouping,
                                        language=language,
                                        use_l10n=use_l10n,
                                        autoescape=autoescape,
                                        value=repr(value),
                                    ):
                                        self.compare(
                                            value,
                                            autoescape=autoescape,
                                            use_l10n=use_l10n,
                                        )

    def test_safe_string_and_ungrouped_int_skip_python_formatter(self):
        template = Engine().from_string("{{ value }}")
        calls = []
        code = base.render_value_in_context.__code__

        def profile(frame, event, arg):
            if event == "call" and frame.f_code is code:
                calls.append(frame)

        class CustomSafeString(SafeString):
            pass

        with override_settings(USE_THOUSAND_SEPARATOR=False):
            previous = sys.getprofile()
            sys.setprofile(profile)
            try:
                template.render(Context({"value": SafeString("<safe>")}))
                template.render(Context({"value": 12345}))
                self.assertEqual(len(calls), 0)
                template.render(Context({"value": CustomSafeString("<safe>")}))
                self.assertGreater(len(calls), 0)
            finally:
                sys.setprofile(previous)
