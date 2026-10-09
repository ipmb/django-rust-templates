"""M8 Task 1: the callable arm, compared with Django's.

`_resolve_lookup`, not `resolve`: the port mirrors the private method. And the
contexts are bound to a template, because two rows below reach
`context.template.engine.string_if_invalid` and would otherwise measure an
`AttributeError` about an unbound context — both of these are properties
`ResolverParityTests` documents and this class inherits rather than restates.
"""

import unittest

from tests.test_render_parity import _ParityTest, _outcome


class CallArmParityTests(_ParityTest):
    def assert_calls_like_django(self, value, path="x", invalid="<<invalid>>"):
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        engine = self.engine.__class__(string_if_invalid=invalid)
        ours_context = Context({"x": value})
        theirs_context = Context({"x": value})
        with ours_context.bind_template(engine.from_string("")):
            with theirs_context.bind_template(engine.from_string("")):
                ours = _outcome(lambda: _native.resolve(path, ours_context))
                theirs = _outcome(
                    lambda: Variable(path)._resolve_lookup(theirs_context)
                )
        self.assertEqual(ours, theirs, f"value: {value!r}")

    def test_a_zero_arg_callable(self):
        def no_args():
            return "NO_ARGS"

        self.assert_calls_like_django(no_args)

    def test_a_callable_instance(self):
        class Callable:
            def __call__(self):
                return "INSTANCE_CALLED"

        self.assert_calls_like_django(Callable())

    def test_a_callable_requiring_an_argument(self):
        def needs_one_arg(a):
            return a

        # Both sides substitute the invalid value rather than raising.
        self.assert_calls_like_django(needs_one_arg)

    def test_a_callable_with_no_signature_is_substituted(self):
        """The *other* path to `string_if_invalid`, and the one a port drops.

        Django's fallback has two questions, not one: `signature()` raising
        `ValueError` ("No signature found", `base.py:1004`) and `bind()` raising
        `TypeError` ("Arguments *were* required", `base.py:1009`). The comment at
        `:1009` is the one a reader remembers, so `bind()` reads as the whole
        test and this branch goes missing. Both substitute, so a port that
        dropped it is right on every fixture that has a signature — which is
        all of them except this shape. `__signature__` raising is exactly what a
        C-level callable without `__text_signature__` does.
        """

        class NoSignature:
            @property
            def __signature__(self):
                raise ValueError("no signature found for builtin")

            def __call__(self):
                raise TypeError("takes no arguments")

        self.assert_calls_like_django(NoSignature())

    def test_a_body_type_error_propagates_on_both_sides(self):
        def body_raises():
            raise TypeError("boom from the body")

        # `_outcome` captures the exception *class and message* on both sides, so
        # this row separates "re-raised" from "substituted" without asserting
        # which — the oracle decides.
        self.assert_calls_like_django(body_raises)

    def test_a_silent_type_error_is_substituted(self):
        class Silent:
            def __call__(self):
                exc = TypeError("silent boom")
                exc.silent_variable_failure = True
                raise exc

        self.assert_calls_like_django(Silent())

    def test_a_class_body_flag_on_a_method_is_inert(self):
        """A flag written where the lookup cannot see it does not suppress the call.

        `getattr(bound_method, "do_not_call_in_templates")` reads through to
        `__func__` and not to the class, so Django **calls** this method.
        Asserted to call, so a later "fix" that consults the owning class
        suppresses a call Django makes and fails here loudly. The `alters_data`
        half of this rule is Task 2's, because it substitutes rather than calls.
        """

        class Inert:
            do_not_call_in_templates = True

            def name(self):
                return "ada"

        # The method must have run for both sides to agree, and the oracle makes
        # that visible without this test asserting the literal.
        self.assert_calls_like_django(Inert().name, path="x")

    def test_the_invalid_read_is_lazy(self):
        """A plain value, on a context with **no template bound**.

        Not `assert_calls_like_django`: that helper binds a template, and
        binding one is exactly what this row must not do — it is the only row
        in the milestone whose subject is the *absence* of a template. Django
        returns the value and never reaches `context.template.engine`; so must
        we.
        """
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        context = Context({"x": "plain"})
        self.assertEqual(
            _outcome(lambda: _native.resolve("x", context)),
            _outcome(lambda: Variable("x")._resolve_lookup(context)),
        )

    def test_a_failed_invalid_read_propagates(self):
        """Where the substitution *is* reached and no template is bound.

        Django reads the attribute inside the outer `try` (`base.py:958`), so
        the `AttributeError` lands in the handler at `base.py:1014`, which finds no
        `silent_variable_failure` and re-raises. A port that swallowed it and
        substituted `""` is quiet where Django is loud.
        """
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        class NeedsOneArg:
            def __call__(self, a):
                return a

        context = Context({"x": NeedsOneArg()})
        self.assertEqual(
            _outcome(lambda: _native.resolve("x", context)),
            _outcome(lambda: Variable("x")._resolve_lookup(context)),
        )


if __name__ == "__main__":
    unittest.main()
