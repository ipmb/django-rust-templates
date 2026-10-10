"""The `Promise` and `SafeData` protocols at the resolver and filter seams.

**A gate with no implementation beside it.** §5.2 lists `force_promise` as a
seam method and nothing in the tree needs one: **no forcing site is on the
resolve path**, which is the claim this gate asserts and the only one it needs.
(`render_value_in_context` and `conditional_escape` do force, and both are on the
render path M9 interposes — but the gate does not depend on that being all of
them.) So this milestone's obligation is discharged by **not** adding a forcing
site — a claim the next edit to `PyTarget` can falsify in one line, which is why
it is pinned rather than asserted in prose.

Every row is a *direct-entry* row. The render path today is Django's, so a
`{{ x }}` renders identically whether our resolver forces or not — measured, all
eight render rows in `.review/m8-measurements/m8_measure_promise.py` §3 are
`same`. A render-based gate would be green and blind; only the entry points can
see the difference.

Both sides run against the pinned tree. Django's filter side is
`FilterExpression.resolve` (`base.py:785-820`, including the `is_safe` /
`needs_autoescape` post-processing the registry function alone does not do);
ours is `_native.filter`. The probe is loaded by path so the instrument behind
M4's gate is the instrument here — two comparisons would drift, and the drift
would be invisible in the worst way.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django_rust_templates.engine import Context  # noqa: E402
from django_rust_templates.engine.base import Variable  # noqa: E402
from django.utils.functional import Promise, lazy  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402
from django.utils.translation import gettext_lazy  # noqa: E402

from django_rust_templates import _native  # noqa: E402

_PROBE_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates.filter_probe",
    PROJECT_ROOT / "scripts" / "filter_probe.py",
)
probe = importlib.util.module_from_spec(_PROBE_SPEC)
sys.modules[_PROBE_SPEC.name] = probe
_PROBE_SPEC.loader.exec_module(probe)


class HasHtml:
    """A `__html__` object that is *not* a `str`.

    The naive expectation is that `|escape` honours `__html__`. It does not:
    `escape_filter` is `@stringfilter` (`defaultfilters.py:457-459`), so the
    decorator's `str()` runs before `conditional_escape` ever sees the object and
    `__html__` is never consulted. Keeping the default `repr` is deliberate — it
    is what makes the row show the protocol was *skipped* rather than reached.
    """

    def __html__(self):
        return "<b>already safe</b>"


class SafeHtml(str):
    """A `str` subclass carrying `__html__` — the shape that *does* reach it."""

    def __html__(self):
        return "<b>already safe</b>"


class ThePromiseIsHandedBackUnforced(unittest.TestCase):
    """Review Focus 4, at the entry point, because no render row can see it."""

    # `lazy(f, str)` returns a **wrapper function**, not a proxy — the obvious
    # spelling hands every row a plain function, which is callable and not a
    # `Promise`. That is trap T2's shape (a fixture inert in the arm under test)
    # and it was measured before it was believed.
    PROXIES = (
        ("gettext_lazy", lambda: gettext_lazy("lazy & value")),
        ("lazy -> SafeString", lambda: lazy(lambda s: mark_safe(s), str)("<b>")),
    )

    def test_the_resolver_returns_the_object_it_was_given(self):
        # Identity, not equality: `str(proxy)` for a `str`-resultclass proxy is
        # `==` the proxy (measured above), so an equality row passes on a
        # resolver that forced every promise — the defect this row exists for.
        for label, make in self.PROXIES:
            value = make()
            context = Context({"x": value})
            with self.subTest(label):
                self.assertIs(_native.resolve("x", context), value)
                self.assertIs(Variable("x").resolve(context), value)
                self.assertIsInstance(_native.resolve("x", {"x": value}), Promise)

    def test_a_resolved_promise_is_still_not_callable(self):
        # Django's callable arm is entered on `callable(current)` and nothing
        # else (`base.py:993`), so an unforced promise takes no arm at all. This
        # row is the *other* half of the contract: it fails if a forcing site is
        # added, and it also fails if `PyTarget::call_arm` drops its
        # `is_callable` guard — `__proxy__` defines no `__call__`, so the
        # failure arrives as a `TypeError` out of our resolver rather than as a
        # wrong string. Two tasks, one row, opposite mistakes.
        for label, make in self.PROXIES:
            value = make()
            with self.subTest(label):
                self.assertFalse(callable(value))
                self.assertIs(_native.resolve("x", Context({"x": value})), value)


class TheEscapingProtocolThroughTheFilterSeam(unittest.TestCase):
    """`SafeData`/`__html__` at `_native.filter`, against Django's own pipeline."""

    FILTERS = ("escape", "force_escape", "safe", "escapejs")

    @classmethod
    def setUpClass(cls):
        # The loop's `use_tz` question needs settings: `Context.use_tz` is `None`
        # and `effective_use_tz` falls back to `settings.USE_TZ`. Called here
        # rather than at module scope because `settings.configure` changes what
        # every module imported afterwards sees.
        probe.ensure_settings()

    def values(self):
        yield "plain str", "a & b"
        yield "SafeString", mark_safe("a & b")
        yield "__html__ object", HasHtml()
        yield "SafeHtml (str + __html__)", SafeHtml("a & b")
        yield "Promise (gettext_lazy)", gettext_lazy("a & b")

    def test_the_two_sides_agree_on_type_value_and_safety(self):
        # `describe` is `(type name, repr)` plus `isinstance(..., SafeData)`,
        # because for 36 of the 57 filters a correct value with the wrong safety
        # is still a wrong answer — the classes that matter here are `str` and
        # `SafeString`, which hold the same text.
        for name in self.FILTERS:
            for label, value in self.values():
                stock, _, context = probe.django_answer(name, value, probe.NO_ARG, {})
                ours = probe.rust_answer(name, value, probe.NO_ARG, context)
                with self.subTest(filter=name, value=label):
                    self.assertEqual(probe.describe(stock), probe.describe(ours))

    def test_a_stringfilter_skips_html_rather_than_honouring_it(self):
        # The row above would pass even if BOTH sides honoured `__html__`, so the
        # behaviour itself is pinned here: the object is escaped by its `repr`,
        # i.e. `__html__` was never consulted.
        #
        # **One object, both sides.** The repr carries the id, and the first
        # draft built `HasHtml()` twice — so it compared `0x1073d0830` against
        # `0x1017e1810` and failed on a correct pair. The failure was the
        # assertion's, not the code's.
        obj = HasHtml()
        stock, _, context = probe.django_answer("escape", obj, probe.NO_ARG, {})
        ours = probe.rust_answer("escape", obj, probe.NO_ARG, context)
        self.assertIn("&lt;", str(stock))
        self.assertIn("object at", str(stock))
        self.assertEqual(str(stock), str(ours))

    def test_the_safe_str_subclass_is_where_html_is_reachable(self):
        # `conditional_escape`'s `__html__` branch (`html.py:124-125`) is reached
        # through a *str* subclass only: `@stringfilter`'s `str()` preserves the
        # subclass, so the protocol survives here where it was skipped above.
        for name in self.FILTERS:
            value = SafeHtml("a & b")
            stock, _, context = probe.django_answer(name, value, probe.NO_ARG, {})
            ours = probe.rust_answer(name, value, probe.NO_ARG, context)
            with self.subTest(filter=name):
                self.assertEqual(probe.describe(stock), probe.describe(ours))
                self.assertIsInstance(stock, SafeData)


if __name__ == "__main__":
    unittest.main()
