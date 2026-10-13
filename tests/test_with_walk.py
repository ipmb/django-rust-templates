"""With bodies use the native walk while preserving Django's scope protocols."""

import sys
import unittest
from unittest.mock import patch

from django.template import Context as DjangoContext, Engine as DjangoEngine
from django.utils.safestring import SafeString

from django_rust_templates import _native
from django_rust_templates.engine import Context, Engine
from django_rust_templates.engine import _stock
from django_rust_templates.engine.base import NodeList, TextNode
from django_rust_templates.engine.defaulttags import ForNode, WithNode


class WithWalkTests(unittest.TestCase):
    def test_special_binding_ignores_metaclass_and_descriptor_instance_hooks(self):
        results = []
        for engine_class, context_class in (
            (DjangoEngine, DjangoContext),
            (Engine, Context),
        ):
            events = []

            class ManagedContext(context_class):
                def push(self, *args, **kwargs):
                    underlying = super().push(*args, **kwargs)

                    class Meta(type):
                        def __getattribute__(cls, name):
                            if name in ("__enter__", "__exit__"):
                                raise AssertionError("metaclass hook")
                            return super().__getattribute__(name)

                    class Descriptor:
                        def __init__(self, name):
                            self.name = name
                            self.__get__ = lambda *args: (_ for _ in ()).throw(
                                AssertionError("instance hook")
                            )

                        def __get__(self, obj, owner):
                            events.append("bind " + self.name)
                            if self.name == "enter":

                                def enter():
                                    events.append("enter")
                                    return underlying.__enter__()

                                return enter

                            def exit(*exc):
                                events.append("exit")
                                return underlying.__exit__(*exc)

                            return exit

                    class Manager(metaclass=Meta):
                        __enter__ = Descriptor("enter")
                        __exit__ = Descriptor("exit")

                    return Manager()

            context = ManagedContext()
            output = (
                engine_class()
                .from_string("{% with x='inner' %}{{ x }}{% endwith %}")
                .render(context)
            )
            results.append((output, events, len(context.dicts)))
        self.assertEqual(results[1], results[0])

    def test_missing_special_methods_match_django(self):
        for attrs in (
            {},
            {"__enter__": lambda self: None},
            {"__exit__": lambda *args: False},
        ):
            results = []
            for engine_class, context_class in (
                (DjangoEngine, DjangoContext),
                (Engine, Context),
            ):
                Manager = type("Manager", (), attrs)

                class ManagedContext(context_class):
                    def push(self, *args, **kwargs):
                        return Manager()

                with self.assertRaises(TypeError) as raised:
                    engine_class().from_string(
                        "{% with x=1 %}{{ x }}{% endwith %}"
                    ).render(ManagedContext())
                results.append(str(raised.exception))
            self.assertEqual(results[1], results[0])

    def test_scoped_loops_preserve_custom_managers_and_render_overrides(self):
        source = "{% with x='inner' %}{% for item in items %}{{ x }}{{ item }}{% empty %}empty{% endfor %}{% endwith %}"
        for items in ([1, 2], []):
            results = []
            for engine_class, context_class in (
                (DjangoEngine, DjangoContext),
                (Engine, Context),
            ):
                events = []

                class ManagedContext(context_class):
                    def push(self, *args, **kwargs):
                        manager = super().push(*args, **kwargs)

                        class Manager:
                            def __enter__(self):
                                events.append(("enter", dict(kwargs)))
                                return manager.__enter__()

                            def __exit__(self, *exc):
                                events.append(("exit", exc[0]))
                                return manager.__exit__(*exc)

                        return Manager()

                context = ManagedContext({"items": items})
                output = engine_class().from_string(source).render(context)
                results.append((output, events, len(context.dicts)))
            self.assertEqual(results[1], results[0])

        template = Engine().from_string(source)
        with patch.object(ForNode, "render", lambda self, context: "custom loop"):
            self.assertEqual(template.render(Context({"items": [1, 2]})), "custom loop")

    def test_body_join_runs_later_nodes_and_preserves_local_error_index(self):
        for body in (
            "A{{ x }}{% non_string %}{% record %}",
            "{% if x %}A{{ x }}{% non_string %}{% record %}{% endif %}{% record %}",
            "{% for item in items %}A{{ item }}{% non_string %}{% record %}{% endfor %}{% record %}",
        ):
            results = []
            for engine_class, context_class in (
                (DjangoEngine, DjangoContext),
                (Engine, Context),
            ):
                events = []
                engine = engine_class(libraries={"scope": "tests.with_walk_library"})
                template = engine.from_string(
                    "{% load scope %}prefix{% with x='inner' %}"
                    + body
                    + "{% endwith %}{% record %}suffix"
                )
                context = context_class({"events": events, "items": [1, 2]})
                with self.assertRaises(TypeError) as raised:
                    template.render(context)
                results.append((str(raised.exception), events, len(context.dicts)))
            with self.subTest(body=body):
                self.assertEqual(results[1], results[0])

    def compare(self, source, data=None, **options):
        expected_context = DjangoContext(dict(data or {}))
        actual_context = Context(dict(data or {}))
        expected = DjangoEngine(**options).from_string(source).render(expected_context)
        actual = Engine(**options).from_string(source).render(actual_context)
        self.assertEqual(actual, expected)
        self.assertIs(type(actual), type(expected))
        self.assertEqual(actual_context.flatten(), expected_context.flatten())

    def test_bindings_nesting_legacy_syntax_and_restoration(self):
        for source in (
            "{{ a }}{% with a='inner' b=a %}{{ a }}/{{ b }}{% endwith %}{{ a }}",
            "{% with a as b and b as a %}{{ a }}/{{ b }}{% endwith %}",
            "{% with x=a|upper %}{% with x=x|lower %}{{ x }}{% endwith %}{{ x }}{% endwith %}",
            "{% for x in items %}{% with a=x %}{{ forloop.counter }}/{{ a }}{% endwith %}{% endfor %}",
            "{% with a=missing|default:'fallback' %}{{ a }}{% endwith %}",
            "{% with a=missing %}{{ a }}{% endwith %}",
            "{% with a=html %}{{ a }}{% endwith %}",
        ):
            for invalid in ("", "invalid:%s"):
                with self.subTest(source=source, invalid=invalid):
                    self.compare(
                        source,
                        {
                            "a": "OUTER",
                            "b": "OTHER",
                            "items": [1, 2],
                            "html": SafeString("<b>&</b>"),
                        },
                        string_if_invalid=invalid,
                    )

    def test_surrogate_output_keeps_the_body_join(self):
        self.compare(
            "A{% with value=html %}{{ value }}{% endwith %}B", {"html": "\ud800"}
        )

    def test_body_is_walked_and_binding_values_are_read_live(self):
        template = Engine().from_string("{% with x='original' %}{{ x }}{% endwith %}")
        node = template.nodelist[0]
        events = []

        class Resolver:
            def resolve(self, context):
                events.append(context["x"])
                return "replacement"

        node.extra_context = {"x": Resolver()}
        _native.census_start()
        try:
            output = template.render(Context({"x": "outer"}))
        finally:
            handled, delegated, *_ = _native.census_take()
        self.assertEqual(output, "replacement")
        self.assertEqual(events, ["outer"])
        self.assertEqual(dict(handled).get("WithNode"), 1)
        self.assertEqual(dict(handled).get("VariableNode"), 1)
        self.assertNotIn("WithNode", dict(delegated))

    def test_rebound_with_render_is_delegated_without_resolving_bindings(self):
        template = Engine().from_string("{% with x='original' %}{{ x }}{% endwith %}")
        events = []

        class Resolver:
            def resolve(self, context):
                events.append("resolved")
                return "x"

        template.nodelist[0].extra_context = {"x": Resolver()}
        for instance in (False, True):
            with self.subTest(instance=instance):
                if instance:
                    template.nodelist[0].render = lambda context: "overridden"
                    try:
                        self.assertEqual(template.render(Context()), "overridden")
                    finally:
                        del template.nodelist[0].render
                else:
                    with patch.object(
                        WithNode, "render", lambda self, context: "overridden"
                    ):
                        self.assertEqual(template.render(Context()), "overridden")
        self.assertEqual(events, [])

    def test_body_replacement_during_entry_is_read_after_push(self):
        source = "{% with x='inner' %}{{ x }}{% endwith %}"
        for engine_class, context_class, list_class, text_class in (
            (DjangoEngine, DjangoContext, None, None),
            (Engine, Context, NodeList, TextNode),
        ):
            if list_class is None:
                from django.template.base import (
                    NodeList as list_class,
                    TextNode as text_class,
                )
            template = engine_class().from_string(source)
            node = template.nodelist[0]
            events = []

            class ReplacingContext(context_class):
                def push(self, *args, **kwargs):
                    manager = super().push(*args, **kwargs)

                    class Manager:
                        def __enter__(self):
                            events.append(("enter", dict(kwargs)))
                            node.nodelist = list_class([text_class("replacement")])
                            return manager.__enter__()

                        def __exit__(self, *exc):
                            events.append(("exit", exc[0]))
                            return manager.__exit__(*exc)

                    return Manager()

            context = ReplacingContext({"x": "outer"})
            self.assertEqual(template.render(context), "replacement")
            self.assertEqual(context["x"], "outer")
            self.assertEqual(events, [("enter", {"x": "inner"}), ("exit", None)])

    def test_body_render_rebound_after_planning_is_called_once(self):
        template = Engine().from_string("{% with x='inner' %}{{ x }}{% endwith %}")
        events = []
        body = template.nodelist[0].nodelist
        body.render = lambda context: events.append(context["x"]) or "overridden body"
        self.assertEqual(template.render(Context()), "overridden body")
        self.assertEqual(events, ["inner"])

    def test_manager_error_paths_match_django_and_restore_scope(self):
        class Raises:
            def __getitem__(self, key):
                raise RuntimeError("body failure")

        source = "prefix{% with x='inner' %}{{ obj.value }}{% endwith %}suffix"
        for suppress, exit_raises in ((False, False), (True, False), (False, True)):
            results = []
            for engine_class, context_class in (
                (DjangoEngine, DjangoContext),
                (Engine, Context),
            ):
                events = []

                class ManagedContext(context_class):
                    def push(self, *args, **kwargs):
                        manager = super().push(*args, **kwargs)

                        class Manager:
                            def __enter__(self):
                                return manager.__enter__()

                            def __exit__(self, *exc):
                                events.append((exc[0], type(sys.exception())))
                                manager.__exit__(*exc)
                                if exit_raises:
                                    raise OSError("exit failure")
                                return suppress

                        return Manager()

                context = ManagedContext({"x": "outer", "obj": Raises()})
                try:
                    engine_class(debug=True).from_string(source).render(context)
                except Exception as error:
                    results.append(
                        (
                            type(error),
                            str(error),
                            type(error.__context__),
                            events,
                            context["x"],
                            len(context.dicts),
                        )
                    )
                else:
                    self.fail("expected a rendering error")
            with self.subTest(suppress=suppress, exit_raises=exit_raises):
                self.assertEqual(results[1], results[0])

    def test_binding_failure_never_pushes_a_scope(self):
        class Raises:
            @property
            def value(self):
                raise ValueError("binding failure")

        for engine_class, context_class in (
            (DjangoEngine, DjangoContext),
            (Engine, Context),
        ):
            events = []

            class CountingContext(context_class):
                def push(self, *args, **kwargs):
                    events.append("push")
                    return super().push(*args, **kwargs)

            context = CountingContext({"obj": Raises()})
            with self.assertRaisesRegex(ValueError, "binding failure"):
                engine_class().from_string(
                    "{% with x=obj.value %}{{ x }}{% endwith %}"
                ).render(context)
            self.assertEqual(events, [])
