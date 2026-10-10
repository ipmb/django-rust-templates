"""M8's non-parity rows: the property is that the method was *not called*.

Everything else in this milestone compares outputs, and this class of defect has
no output to compare. Measured with `string_if_invalid=''` on an `alters_data`
callable that returns `''`: a port that calls it renders exactly what a port that
suppresses it renders — `('value', '')`. So a counter observes the call, and the
counter needs a **positive control**, or the whole module passes on a counter
that is simply never incremented.

There is deliberately no parity corpus here. `tests/test_render_parity.py` owns
that harness (`assert_resolves_like_django` over `_native.resolve` against
`Variable._resolve_lookup`), and a second copy would agree with our side for a
reason this gate does not share.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django_rust_templates.engine import Context, Engine  # noqa: E402
from django_rust_templates.engine.base import Variable  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from tests.parity_target import pinned_resolve_lookup  # noqa: E402


class Suppressed:
    """A callable whose only observable effect is that it ran.

    `alters_data = True`, and `__call__` returns `""` — the same value
    `string_if_invalid=''` substitutes. The output is `""` either way.
    """

    alters_data = True
    calls = 0

    def __call__(self):
        type(self).calls += 1
        return ""

    def reset(self):
        type(self).calls = 0


class DoNotCall:
    """`do_not_call_in_templates` — and note the arm is `pass`: the *object* is
    the value, not `""`."""

    do_not_call_in_templates = True
    calls = 0

    def __call__(self):
        type(self).calls += 1
        return ""

    def reset(self):
        type(self).calls = 0


class Control:
    """No flags, so Django *does* call it. The counter must see this one."""

    calls = 0

    def __call__(self):
        type(self).calls += 1
        return ""

    def reset(self):
        type(self).calls = 0


def _bound(value, invalid=""):
    """A context bound to an engine — both sides need the binding, because the
    `alters_data` arm reads `context.template.engine.string_if_invalid`."""
    engine = Engine(string_if_invalid=invalid)
    ctx = Context({"x": value})
    return ctx, ctx.bind_template(engine.from_string(""))


class ThePropertyNoOutputCanShow(unittest.TestCase):
    def _run(self, value, call):
        ctx, binding = _bound(value)
        with binding:
            return call(ctx)

    def test_the_positive_control_the_counter_can_see_a_call(self):
        # Without this row every assertion below also holds for a counter that is
        # never incremented — a guard that cannot fail. Stock Django calls an
        # unflagged callable, so `calls` must reach 1.
        value = Control()
        value.reset()
        self._run(value, lambda c: pinned_resolve_lookup()(Variable("x"), c))
        self.assertEqual(value.calls, 1, "stock Django did not call the control")

    def test_alters_data_suppresses_the_call_stock(self):
        value = Suppressed()
        value.reset()
        got = self._run(value, lambda c: pinned_resolve_lookup()(Variable("x"), c))
        self.assertEqual(got, "")
        self.assertEqual(value.calls, 0)

    def test_do_not_call_suppresses_the_call_stock(self):
        # **Not `""` — the object.** Django's branch is `pass` (`base.py:994-995`),
        # so the callable is left as the value, and it only becomes a string one
        # layer later in `render_value_in_context`. The first draft of this module
        # asserted `== ""` and failed against correct stock Django.
        value = DoNotCall()
        value.reset()
        got = self._run(value, lambda c: pinned_resolve_lookup()(Variable("x"), c))
        self.assertIs(got, value)
        self.assertEqual(value.calls, 0)

    def test_alters_data_suppresses_the_call_ours(self):
        value = Suppressed()
        value.reset()
        got = self._run(value, lambda c: _native.resolve("x", c))
        self.assertEqual(got, "")
        self.assertEqual(value.calls, 0)

    def test_do_not_call_suppresses_the_call_ours(self):
        value = DoNotCall()
        value.reset()
        got = self._run(value, lambda c: _native.resolve("x", c))
        self.assertIs(got, value)
        self.assertEqual(value.calls, 0)

    def test_the_control_is_called_ours(self):
        value = Control()
        value.reset()
        got = self._run(value, lambda c: _native.resolve("x", c))
        self.assertEqual(got, "")
        self.assertEqual(value.calls, 1)


if __name__ == "__main__":
    unittest.main()
