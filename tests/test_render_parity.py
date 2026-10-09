"""M3 Task 7's acceptance: the end-to-end render, and Task 7's resolver.

Two claims live in this file and they are **not** the same claim, which is the
first thing to read here rather than the last:

* ``RenderParityTests`` renders each template twice — once through
  ``render_string`` (Rust parse, shell materialise, Django render) and once
  through stock Django — and compares. A passing case is evidence that the Rust
  **parse** and the host callbacks are right. It is *not* evidence about Task 7's
  resolver, because the lookup in these templates still runs through Django's own
  ``Variable._resolve_lookup``: ``django.template.base`` is not interposed until
  M9, so the arm order Task 7 built is not on the render path at all.
* ``ResolverParityTests`` drives ``_native.resolve`` directly against
  ``Variable._resolve_lookup``. That is where the resolver is measured, on paths
  no M3 template can reach — a failing ``@property``, a ``BaseContext``
  attribute, an exception carrying ``silent_variable_failure``.

Comparing against Django rather than against recorded output is what makes both
of them parity tests: a hard-coded expectation would pin what *this* engine does
today and would keep passing after the pinned tree moved under it.

Every comparison here is on the **outcome**, value or exception, and the
exception comparison is on the class object as well as the message — ``type(exc)``
is ``django.template.base.VariableDoesNotExist`` itself on our side, because the
host constructs Django's class rather than a lookalike. A message-only assertion
would pass against a port that raised a Rust-defined exception with the same text.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine  # noqa: E402
from django.template.base import BaseContext, Variable  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.render import render_string  # noqa: E402


class _RaisesAttributeError:
    """A ``@property`` in the attribute arm's swallowing set.

    Measured: Django re-raises it, because the name is in ``dir()``
    (``base.py:970-977``). A port that treated a swallowed exception as a miss
    would fall through to the index arm and render ``string_if_invalid``.
    """

    @property
    def boom(self):
        raise AttributeError("no such attribute")


class _RaisesRuntimeError:
    """A ``@property`` outside the swallowing set — the row that keeps the
    ``dir()`` rule in one place, since the name *is* in ``dir()`` and Django
    raises anyway."""

    @property
    def boom(self):
        raise RuntimeError("kaboom")


class _Silent(Exception):
    """Django's own idiom for "this object may not be there": the outer handler
    substitutes ``string_if_invalid`` for any exception carrying this flag
    (``base.py:1023``)."""

    silent_variable_failure = True


class _RaisesSilently:
    @property
    def boom(self):
        raise _Silent("quiet")


class _RaisesSilentlyFromTheItemArm:
    """The same exception from ``__getitem__``, so both arms of the outer handler
    are measured rather than one."""

    def __getitem__(self, key):
        raise _Silent("quiet-item")


class _PlainWithAFalsyClassAttribute:
    """For a non-``BaseContext`` the class attribute is irrelevant, so ``zero``
    is the instance's ``7``. A predicate that consulted the class attribute for
    every object would answer ``0``."""

    zero = 0

    def __init__(self):
        self.zero = 7


class _FalsyClassAttributeContext(BaseContext):
    """For a ``BaseContext`` the class attribute *does* decide, and only when it
    is present **and falsy**: Django's condition is ``isinstance(current,
    BaseContext) and getattr(type(current), bit)`` (``base.py:972-976``), and a
    truthy one raises the ``AttributeError`` that suppresses the arm."""

    zero = 0


class _ParityTest(unittest.TestCase):
    """Settings and an ``Engine``, once per class.

    ``Engine()`` reads settings, and rendering a *number* goes through
    ``localize``, which reads ``USE_I18N`` — so an unconfigured settings module
    raises ``ImproperlyConfigured`` from inside the first numeric render. Set
    here rather than at import time because the rest of this suite deliberately
    runs without settings configured, and a module-level ``settings.configure``
    would change what a test module discovered after this one sees.

    The import is inside the method for a second, unrelated reason, measured:
    ``unittest``'s loader walks the module's attributes and calls
    ``isinstance(obj, type)`` on each, and a module-level ``settings`` proxy in
    that walk raises ``ImproperlyConfigured`` before a single test is collected.
    """

    @classmethod
    def setUpClass(cls):
        from django.conf import settings

        if not settings.configured:
            settings.configure(USE_I18N=False)
        cls.engine = Engine()


class RenderParityTests(_ParityTest):
    def assert_renders_like_django(self, source, context):
        ours = render_string(source, context)
        theirs = self.engine.from_string(source).render(Context(context))
        self.assertEqual(ours, theirs, f"template: {source!r}")

    def test_text_only(self):
        self.assert_renders_like_django("hello", {})

    def test_a_bare_variable(self):
        self.assert_renders_like_django("{{ name }}", {"name": "Ada"})

    def test_a_dotted_lookup_and_a_list_index(self):
        self.assert_renders_like_django("{{ a.b }}", {"a": {"b": "B"}})
        self.assert_renders_like_django("{{ a.1 }}", {"a": [10, 20]})

    def test_a_filter_chain(self):
        # A built-in filter, resolved through the `Parser` the shell builds —
        # which is the one thing in this class that exercises D5's argument.
        self.assert_renders_like_django("{{ name|upper }}", {"name": "ada"})

    def test_an_escaped_variable_is_escaped_once(self):
        # `mark_safe` on a literal is `_native`'s, and the `|safe` filter is the
        # shell's `find_filter`. Both sides must agree on the escaping, since a
        # wire `SafeStr` that was not a `SafeData` would be escaped twice.
        self.assert_renders_like_django("{{ html }}", {"html": "<b>&</b>"})
        self.assert_renders_like_django("{{ html|safe }}", {"html": "<b>&</b>"})

    def test_a_missing_key_renders_string_if_invalid(self):
        # The missing-key substitution is `Variable.resolve`'s, one frame out
        # from the resolver, and runs on Django's side here — which is exactly
        # why it matches. This is the case that needs `context.template` bound,
        # and `_native.render` binds it by rendering through a real `Template`.
        self.assert_renders_like_django("{{ nope }}", {"name": "Ada"})

    def test_the_engine_the_caller_passes_is_the_one_that_renders(self):
        """The engine reaches the render path, not only the parser.

        Measured before the shell took an engine: with ``string_if_invalid`` set
        to ``"<<invalid>>"`` stock Django rendered ``'&lt;&lt;invalid&gt;&gt;'``
        and ``render_string`` rendered ``''``, because the shell built its own
        default ``Engine`` for the ``Template`` it hands to Rust. A default
        ``string_if_invalid`` is ``""``, so *every* other row in this class passed
        while that one was wrong.

        The escaping is Django's and is compared rather than predicted: the
        substituted value goes through ``render_value_in_context``, which escapes
        a non-``SafeData`` value under autoescape.
        """
        engine = Engine(string_if_invalid="<<invalid>>")
        ours = render_string("{{ nope }}", {"name": "Ada"}, engine)
        theirs = engine.from_string("{{ nope }}").render(Context({"name": "Ada"}))
        self.assertEqual(ours, theirs)

    def test_string_if_invalid_has_two_consumers_and_they_differ(self):
        """The miss path and the silent-failure path are not the same path.

        A finding from the reviewer, and it is invisible under the default
        ``""``: an empty string substituted or filtered is still empty, so every
        other row in this class passes however wrong these two are. Measured on
        the pinned tree with ``string_if_invalid="<b>%s</b>"``::

            {{ nope }}          -> '&lt;b&gt;nope&lt;/b&gt;'
            {{ p.boom }}        -> '&lt;b&gt;%s&lt;/b&gt;'
            {{ nope|upper }}    -> '&lt;b&gt;nope&lt;/b&gt;'
            {{ p.boom|upper }}  -> '&lt;B&gt;%S&lt;/B&gt;'

        Two asymmetries, and the value above carries both at once:

        * ``%s`` is substituted with the variable name **only** on the
          ``VariableDoesNotExist`` path — ``FilterExpression.resolve`` does
          ``string_if_invalid % self.var`` — while the silent arm assigns the
          string raw. So the silent rows render a literal ``%s``.
        * the filter chain runs on the silent path and **not** on the miss
          path, because ``FilterExpression.resolve`` returns early on
          ``VariableDoesNotExist``. So ``|upper`` never runs there, and on the
          silent row it uppercases both the surrounding tags and the leftover
          ``%s``.

        ``<b>`` is there to make the escaping observable as well: the
        substituted value is not a ``SafeData``, so ``render_value_in_context``
        escapes it, and a render path that emitted it raw would fail here rather
        than pass on a string nobody compares.

        Both asymmetries are Django's own code on this path today, so these rows
        cannot fail yet — they are written for M9, when the resolver is
        interposed and the silent arm becomes ``_native.resolve``'s. A row that
        is cheap now and load-bearing then is the reason the corpus and this
        class are split the way they are.
        """
        engine = Engine(string_if_invalid="<b>%s</b>")
        context = {"p": _RaisesSilently()}
        expected = {
            "{{ nope }}": "&lt;b&gt;nope&lt;/b&gt;",
            "{{ p.boom }}": "&lt;b&gt;%s&lt;/b&gt;",
            "{{ nope|upper }}": "&lt;b&gt;nope&lt;/b&gt;",
            "{{ p.boom|upper }}": "&lt;B&gt;%S&lt;/B&gt;",
        }
        for source, literal in expected.items():
            ours = render_string(source, context, engine)
            theirs = engine.from_string(source).render(Context(context))
            self.assertEqual(ours, theirs, f"template: {source!r}")
            # The literal as well as the comparison: comparing two runs that
            # changed together would keep passing, and these four strings are
            # the whole of the claim.
            self.assertEqual(ours, literal, f"literal for {source!r}")


class ResolverParityTests(_ParityTest):
    """Task 7's resolver, compared with ``Variable._resolve_lookup``.

    ``_resolve_lookup`` and not ``resolve``: the port mirrors the private method,
    which raises ``VariableDoesNotExist`` where ``resolve`` substitutes
    ``string_if_invalid``. Comparing against ``resolve`` would compare a raise
    against a value and fail on the difference that makes the two functions
    different.

    The contexts are bound to a template, because two of the rows below reach
    ``context.template.engine.string_if_invalid`` and would otherwise measure an
    ``AttributeError`` about an unbound context rather than the substitution they
    are there for.
    """

    def assert_resolves_like_django(self, path, context):
        ours_context = Context(context)
        theirs_context = Context(context)
        # `Context.bind_template` is a context manager, not a setter — calling it
        # without `with` does nothing at all, which is what an earlier version of
        # this probe did and why the two silent rows first read as an
        # `AttributeError` about `NoneType`.
        with ours_context.bind_template(self.engine.from_string("")):
            with theirs_context.bind_template(self.engine.from_string("")):
                ours = _outcome(lambda: _native.resolve(path, ours_context))
                theirs = _outcome(
                    lambda: Variable(path)._resolve_lookup(theirs_context)
                )
        self.assertEqual(ours, theirs, f"path: {path!r}")

    def test_a_dotted_lookup_and_two_missing_keys(self):
        self.assert_resolves_like_django("a.b", {"a": {"b": "B"}})
        self.assert_resolves_like_django("a.b.c", {"a": {"b": {"c": 7}}})
        # A miss at the root and a miss one bit in: the two produce different
        # containers in `VariableDoesNotExist.params`, so they are different
        # messages and not one case twice.
        self.assert_resolves_like_django("nope", {"a": {"b": "B"}})
        self.assert_resolves_like_django("a.nope", {"a": {"b": "B"}})

    def test_a_list_index_and_an_out_of_range_index(self):
        self.assert_resolves_like_django("a.1", {"a": [10, 20]})
        self.assert_resolves_like_django("a.9", {"a": [10, 20]})

    def test_a_property_in_the_dir_rule_and_out_of_it(self):
        # The pair that places Django's `dir()` rule on the attribute arm's
        # *swallowed* exception: same arm, same exception class, different
        # `dir()`, different outcome.
        self.assert_resolves_like_django("obj.boom", {"obj": _RaisesAttributeError()})
        self.assert_resolves_like_django("obj.boom", {"obj": _RaisesRuntimeError()})

    def test_the_plain_class_attribute_pair(self):
        # A non-context: the class attribute is never consulted, so the instance
        # wins. `0` and `7` are distinguishable, which is the point of picking a
        # falsy class attribute.
        self.assert_resolves_like_django(
            "obj.zero", {"obj": _PlainWithAFalsyClassAttribute()}
        )

    def test_a_context_class_attribute_suppresses_the_instance_one(self):
        # A `BaseContext` with a *truthy* class attribute — `push` is inherited
        # from `BaseContext` itself, which is why the host's probe must walk the
        # MRO rather than read `type(obj).__dict__`.
        self.assert_resolves_like_django("c.push", {"c": Context({"n": 1})})
        # ... and one with a present-and-falsy class attribute, where the
        # instance attribute is read after all.
        self.assert_resolves_like_django("c.zero", {"c": _FalsyClassAttributeContext()})

    def test_a_silent_variable_failure_is_substituted(self):
        # Both arms of Django's outer handler (`base.py:1014-1026`): an exception
        # carrying `silent_variable_failure` is substituted, everything else is
        # re-raised. The property row and the `__getitem__` row reach the handler
        # by different routes, so both are here.
        self.assert_resolves_like_django("obj.boom", {"obj": _RaisesSilently()})
        self.assert_resolves_like_django(
            "obj.x", {"obj": _RaisesSilentlyFromTheItemArm()}
        )

    def test_the_substitution_reads_the_binding_contexts_engine(self):
        """The value substituted is the *context's* engine's, not a constant.

        A port that returned ``""`` for a silent failure would pass every row
        above, because the default ``string_if_invalid`` is ``""``. This is the
        row that separates the substitution from the default.

        ``"%s"`` is in the value for the reviewer's second reason: the ``%``
        substitution with the variable name lives one frame **up**, in
        ``FilterExpression.resolve``, so ``_resolve_lookup`` and the silent arm
        return the string raw. A port that applied ``"%s" % self.var`` here
        would pass with ``"<<invalid>>"`` and fail with ``"<%s>"`` — and it
        would double-substitute once M9 puts this resolver under
        ``FilterExpression.resolve``, which is the only reason the difference is
        observable at all.
        """
        engine = Engine(string_if_invalid="<%s>")
        ours_context = Context({"obj": _RaisesSilently()})
        theirs_context = Context({"obj": _RaisesSilently()})
        with ours_context.bind_template(engine.from_string("")):
            with theirs_context.bind_template(engine.from_string("")):
                self.assertEqual(
                    _native.resolve("obj.boom", ours_context),
                    Variable("obj.boom")._resolve_lookup(theirs_context),
                )
                self.assertEqual(_native.resolve("obj.boom", ours_context), "<%s>")


def _outcome(call):
    """A value or an exception, as one comparable tuple.

    The exception is compared as ``(type, str)`` rather than by identity, because
    two raises are two different objects — but the *type* here is the class
    object, so an exception of a different class with the same message fails.
    """
    try:
        return ("value", call())
    except Exception as exc:  # noqa: BLE001  (the exception is the compared value)
        return ("raised", type(exc), str(exc))


if __name__ == "__main__":
    unittest.main()
