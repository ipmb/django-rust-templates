"""The acceptance pre-flight, driven with a fabricated meter.

M14's change makes `run_django_suite.sh` refuse unless `unshimmed()` is empty in
both scopes. The positive control is the real run; this file is the negative
control, because a guard whose only exercise is a green run is a guard that might
be a no-op. The fabricated meter is drawn from the same population the guard
reads — a scope with a name in it — so "refuses" is measured against something,
not against an empty universe.

The fabricated module names carry the `unregistered_` marker the tag inventory
uses, and a row asserts none of them exists in the real population, so the control
cannot be overtaken by progress.
"""

import unittest

from django_rust_templates import _shim

from scripts.assert_engine_installed import engine_fallbacks

SCOPES = ("django.template", "django.templatetags")
# A name no tree can ever contain — the `unregistered_` rule, in the module
# namespace. `test_the_fabricated_names_are_not_real_modules` pins it.
FABRICATED = "unregistered_m14_fabricated"


def meter(*non_empty):
    """A stand-in for `_shim.unshimmed` with `non_empty` scopes non-empty."""
    by_scope = {scope: [f"{scope}.{FABRICATED}"] for scope in non_empty}
    return lambda scope: by_scope.get(scope, [])


class EngineFallbacksTests(unittest.TestCase):
    def test_an_empty_meter_reports_no_fallbacks(self):
        self.assertEqual(engine_fallbacks(meter(), SCOPES), {})

    def test_a_name_in_the_first_scope_is_reported(self):
        self.assertEqual(
            engine_fallbacks(meter("django.template"), SCOPES),
            {"django.template": [f"django.template.{FABRICATED}"]},
        )

    def test_a_name_in_the_second_scope_is_reported(self):
        # The scope the runner's pre-flight did not consult before M14.
        self.assertEqual(
            engine_fallbacks(meter("django.templatetags"), SCOPES),
            {"django.templatetags": [f"django.templatetags.{FABRICATED}"]},
        )

    def test_both_scopes_are_reported_together(self):
        self.assertEqual(set(engine_fallbacks(meter(*SCOPES), SCOPES)), set(SCOPES))

    def test_the_fabricated_names_are_not_real_modules(self):
        """The control is drawn from the real population and is outside it.

        `FABRICATED` must be a name no tree contains, or the control answers a
        question about a module that might one day exist. The population is the
        meter's own: `django_modules(scope)`, both scopes, minus `NOT_THE_ENGINE`.
        """
        _shim.install()
        universe = set()
        for scope in SCOPES:
            universe |= {
                f"{scope}.{name}"
                for name in _shim.django_modules(scope) - _shim.NOT_THE_ENGINE
            }
        self.assertGreater(
            len(universe), 0, "the population the control is drawn from is empty"
        )
        for scope in SCOPES:
            with self.subTest(scope=scope):
                self.assertNotIn(f"{scope}.{FABRICATED}", universe)


if __name__ == "__main__":
    unittest.main()
