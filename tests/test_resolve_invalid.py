"""M8 Task 2: the `string_if_invalid` substitution, compared with Django's.

The middle row of `test_the_substitution_continues_the_loop` is the only one
that separates "substitute and continue" from "substitute and stop", and it is
the only row in this milestone that does. Compared against
`Variable._resolve_lookup`, not against a render: `FilterExpression.resolve`
substitutes a missing key one frame out, so a render row would hide the third
path behind a second, unrelated substitution.
"""

import unittest

from tests.test_render_parity import _ParityTest, _outcome


class InvalidSubstitutionParityTests(_ParityTest):
    def assert_substitutes_like_django(self, value, path="x", invalid="<<invalid>>"):
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
        self.assertEqual(ours, theirs, f"value: {value!r} path: {path!r}")

    def test_the_substitution_continues_the_loop(self):
        """`x.upper` is the discriminator; the other two rows guard it.

        Django assigns `string_if_invalid` **inside** `for bit in self.lookups:`
        (`base.py:997`), so `upper` is resolved on the substituted string and
        the string's own `upper` runs. A port that substituted and stopped
        renders the row above it — `'<<invalid>>'` — and every other input in
        this milestone agrees with Django either way.
        """

        class Alters:
            alters_data = True
            name = "ada"

            def __call__(self):
                return "SIDE EFFECT"

            def upper(self):
                # Distinguishable from `str.upper`, so the middle row cannot
                # pass by calling this.
                return "NOT THE STRING ONE"

        for path in ("x", "x.upper", "x.name"):
            # `subTest`, so all three rows report. A plain loop aborts on the
            # first failure, which hides whether `x.name` is also red — and
            # "both of the guards fail for the substitution-stopped port" is
            # exactly the evidence Review Focus 1 owes.
            with self.subTest(path=path):
                self.assert_substitutes_like_django(Alters(), path=path)

    def test_the_flag_is_read_off_the_resolved_object(self):
        """The one `alters_data` rule, in the shape that makes both spellings fall out.

        Two fixtures that differ only in *where* the flag is written, and they
        are two different **resolved objects**: a bound method, which proxies
        attribute lookup to `__func__` and so sees only a function attribute,
        and a callable instance, where a class attribute is visible. A port that
        consults the owning class gets the first wrong — and gets it wrong in the
        dangerous direction, invoking a method Django suppresses.
        """

        class OnTheFunction:
            def save(self):
                return "SIDE EFFECT"

            save.alters_data = True

        class OnTheInstance:
            alters_data = True

            def __call__(self):
                return "CALLED"

        self.assert_substitutes_like_django(OnTheFunction().save)
        self.assert_substitutes_like_django(OnTheInstance())

    def test_a_failed_read_escapes_rather_than_substituting(self):
        """No template bound, and the substitution *is* reached.

        Not `assert_substitutes_like_django`: that helper binds a template,
        which is precisely the condition this row must not create.
        """
        from django.template import Context
        from django.template.base import Variable

        from django_rust_templates import _native

        class Alters:
            alters_data = True

            def __call__(self):
                return "SIDE EFFECT"

        context = Context({"x": Alters()})
        self.assertEqual(
            _outcome(lambda: _native.resolve("x", context)),
            _outcome(lambda: Variable("x")._resolve_lookup(context)),
        )


if __name__ == "__main__":
    unittest.main()
