"""Direct-entry rows for `base.py`'s render-time helpers.

M9 interposes ``django.template.base``, so ``render_value_in_context`` — which
the accepted ``template_tests`` run calls **6505** times, from 542 distinct
tests (``.review/m9-measurements/m9_measure_render_value_blast_radius.py``,
which names its metric: calls, not lines; the whole run, not one module;
attributed by calling frame) — is our code from the moment the module is
interposed. It is a transcription, so it is correct by construction today;
these rows exist so it *stays* correct, and so the one character in it that has
teeth has a row with teeth.

**It has more than one call site, and not all of them are in this module.**
6071 of those calls come from frames named render — VariableNode.render,
CycleNode.render and TranslateNode.render — and 434 from a nested function
called render_value inside the blocktranslate tag. The call sites are
`python/django_rust_templates/_impl/base.py:1161`, which is ours, and
`django/template/defaulttags.py:114`, `django/templatetags/i18n.py:91` and
`django/templatetags/i18n.py:185`, which are not. The two out-of-module modules
reach it through a from-import of render_value_in_context at module scope, and
that is a copy taken when they were imported — nothing done to
django.template.base afterwards can change it. Our own file is different: it
calls the module global, so it always follows. That is what the row
test_the_modules_that_copied_it_hold_ours pins, and it was measured rather than
reasoned about.

**The plan's prediction for this row was wrong, and the measurement is the
point.** Task 4 Step 4 says that changing ``issubclass(type(value), str)`` to
``isinstance(value, str)`` "should still pass", and Step 2's docstring draft
says the two "agree on every str *instance* and disagree on nothing else
either". Measured: **both statements are false**, in the direction that makes
the row worth writing.

``isinstance(value, str)`` and ``issubclass(type(value), str)`` differ for
exactly one class of value: an object whose ``__class__`` *property* claims
``str`` while its actual type does not — ``isinstance`` consults ``__class__``
for such an object and ``type(value)`` cannot. Give that object an ``__html__``
and the two forms produce **different output**, not merely a different
intermediate:

    issubclass(type(value), str)  -> str(value), then escaped
                                     '&lt;__main__.Pretender object at 0x…&gt;'
    isinstance(value, str)        -> passed through, so conditional_escape
                                     honours __html__: '<raw>'

``test_the_conversion_test_is_issubclass_of_type_not_isinstance`` is that pair,
with both literals. It is the row that fails if someone tidies the expression.

**And the plan's Step 1 quoted the wrong function.** The docstring it prints as
"the source of truth" ("This means wrapping them in a SafeString if necessary…")
is not Django 6.0.9's; the pinned tree's reads "This means escaping, if
required, and conversion to a string. If value is a string, it's expected to
already be translated." The *body* the step prints is right, which is why the
mistake would have gone unnoticed — a row written from the quoted docstring
would have asserted nothing about the code.

The oracle is Django's own function, executed from the pinned file by
``_pinned`` below rather than retyped here: a row that compared our function
against our function would prove nothing, and a hand-copied expectation would
pin today's behaviour rather than Django's.

**The file has a second subject, and it is not a helper.** Interposing ``base``
also interposes ``Node.render_annotated`` and ``Template.get_exception_info``,
so the whole debug-annotation protocol becomes ours for every node in the
suite — including the ones Rust did not build. ``RenderAnnotationTests`` below
pins it, and the reason is §5.5: Rust rendering interior subtrees from its own
compiled form is exactly what would stop ``_culprit_node`` being attached at the
right level. The plan's Task 6 predicted the ``(start, end)`` pairs would be the
assertion that has teeth; measured, they are **invariant under both guards**,
and what discriminates is node *identity* and the origin comparison's choice of
frame. The docstrings below carry both measurements.
"""

import ast
import importlib
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PINNED_BASE = PROJECT_ROOT / ".django" / "django" / "template" / "base.py"

# ``.django/tests`` as well, because ``Engine(libraries=…)`` resolves its
# dotted paths lazily through ``import_module`` and the fixtures name
# ``template_tests.templatetags.bad_tag``. The same guarded insertion
# ``tests/test_parser_parity.py`` makes, and for the same reason: without it the
# rows pass only when some earlier module happened to put the path there,
# because ``tests/test_host_tags.py`` inserts it and sorts between us.
DJANGO_TESTS = PROJECT_ROOT / ".django" / "tests"
if str(DJANGO_TESTS) not in sys.path:
    sys.path.insert(0, str(DJANGO_TESTS))

import django_rust_templates  # noqa: E402
from django_rust_templates import _shim  # noqa: E402

_PINNED_CACHE = {}


def _pinned(name):
    """Django's own ``name``, executed from the pinned file.

    Read with ``ast`` and ``exec``'d into a copy of our module's globals, so the
    body under test is upstream's byte-for-byte while the helpers it calls
    (``conditional_escape``, ``localize``, ``template_localtime``) are the same
    objects the render path uses — those are Django's ``django.utils.*``
    functions and are not transcribed by this project, so sharing them is what
    makes this a comparison of the function and not of its dependencies.

    Not ``importlib``: the shim serves ``django.template.base`` from ``_impl``,
    so importing it by name gives the very function under test.
    """
    if name in _PINNED_CACHE:
        return _PINNED_CACHE[name]

    source = PINNED_BASE.read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            break
    else:  # pragma: no cover - the pinned tree has it
        raise AssertionError(f"{name} is not a top-level function in {PINNED_BASE}")

    base = importlib.import_module("django.template.base")
    namespace = dict(base.__dict__)
    exec(compile(ast.get_source_segment(source, node), str(PINNED_BASE), "exec"), namespace)
    _PINNED_CACHE[name] = namespace[name]
    return _PINNED_CACHE[name]


class _IsAStringButSaysSoWithClass:
    """``isinstance(x, str)`` is True; ``issubclass(type(x), str)`` is False.

    That is the whole of the difference between the two conversion tests, and
    ``__html__`` is what makes it observable rather than merely internal: it is
    read by ``conditional_escape``, so whichever way the branch goes, the output
    differs.
    """

    @property
    def __class__(self):
        return str

    def __html__(self):
        return "raw-marker"


class RenderValueInContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The *suite's* settings helper, not a local `settings.configure`.
        #
        # The first version of this class configured ``USE_I18N=False``, copying
        # the four modules that do. Measured: it reddened four rows in
        # ``tests/test_filter_parity.py`` — ``yesno/locale-translates-the-default-
        # arguments`` rendered ``'maybe'`` where its pinned literal is
        # ``'Vielleicht'``, and ``date/german-default-format`` ``'8. October
        # 2026'`` where the literal is ``'8. Oktober 2026'``.
        #
        # The cause is not the value, it is the ordering. Settings are
        # **first-writer-wins** for the whole process, and ``discover`` walks
        # modules alphabetically: every other module that configures sorts after
        # ``test_filter_parity``, so ``filter_probe.ensure_settings()`` — which
        # needs ``USE_I18N=True`` **and** ``django.setup()`` to read a catalog at
        # all — always won. ``test_base_parity`` sorts *before* it, so the same
        # line of code that is harmless in four other files locked the suite into
        # ``USE_I18N=False`` and silently changed another module's expectations.
        #
        # So this calls the same helper rather than a copy of its effect. It is
        # guarded on ``settings.configured``, so whichever module gets there
        # first the answer is the suite's answer, and the alphabet stops being
        # load-bearing.
        from scripts.filter_probe import ensure_settings

        ensure_settings()
        _shim.install()

        base = importlib.import_module("django.template.base")
        cls.base = base
        # ``staticmethod``, or ``self.ours`` is a *bound method*: a plain function
        # stored as a class attribute is the descriptor protocol's method case,
        # so ``self`` would be passed as ``value`` and every call would raise
        # "takes 2 positional arguments but 3 were given". Measured, not
        # anticipated — the first run of this file failed 24 times on it.
        cls.ours = staticmethod(base.render_value_in_context)
        cls.theirs = staticmethod(_pinned("render_value_in_context"))

    def test_it_is_our_function(self):
        """``inspect.getfile``, not ``__module__``.

        The same correction ``tests/test_interposition.py`` records for the node
        classes: the shim loads our file *under Django's name*, so
        ``render_value_in_context.__module__`` reads ``django.template.base``
        and is supposed to. Asserting on it here would be asserting the shim is
        not installed.
        """
        self.assertIn("_impl", inspect.getfile(self.ours))

    def test_the_modules_that_copied_it_hold_ours(self):
        """The out-of-module call sites, which are copies taken at *import* time.

        Two of the modules that call the function are not ours: defaulttags, at
        `django/template/defaulttags.py:37`, and the i18n library, at
        `django/templatetags/i18n.py:5`. Both reach it through a from-import at
        module scope, so each holds whatever the module attribute was when it was
        imported — and nothing this project does to django.template.base
        afterwards can change it. That is the difference from our own file's
        call, at `python/django_rust_templates/_impl/base.py:1161`, which reads
        the module global at call time and therefore always follows.

        **Measured, in both import orders.** With ``install()`` first — the order
        ``scripts/check.sh`` establishes for every unit-test process, via the
        ``sitecustomize`` on ``shimpath`` — both bindings are ours. Import
        Django's ``Engine`` and ``defaulttags`` *before* ``install()`` and they
        stay Django's: ``django.template.base`` resolves to ``_impl/base.py``
        while ``defaulttags.render_value_in_context`` resolves to the pinned
        file, and ``is`` between the two is ``False``. So ``{% cycle %}`,
        ``{% firstof %}` and ``{% translate %}` would render through Django's
        copy while every ``VariableNode`` went through ours — the interposition
        *silently partial*, which is the failure this file exists to catch, one
        module out.

        The row is therefore an assertion about startup order as much as about
        the function, and it is the reason the ordering is not incidental: a
        consumer who imports Django's template machinery and *then* installs the
        shim gets this state, and nothing raises.
        """
        import django.template.defaulttags as defaulttags
        import django.templatetags.i18n as i18n

        for module in (defaulttags, i18n):
            with self.subTest(module=module.__name__):
                self.assertIs(module.render_value_in_context, self.ours)

    def test_a_safe_string_is_not_reconverted(self):
        """The conversion is skipped for a ``str`` subclass, so it stays safe.

        The observable is the *type*: ``conditional_escape`` returns a
        ``SafeString`` for something already safe, and would return one anyway
        after escaping — so the type alone is weak evidence, and the value is
        compared too. What a port that converted unconditionally would break is
        the escaping: ``str(SafeString('<b>&</b>'))`` is still the raw text and
        ``conditional_escape`` would escape it.
        """
        from django.template import Context
        from django.utils.safestring import SafeString, mark_safe

        value = mark_safe("<b>&</b>")
        self.assertIsInstance(value, SafeString)

        rendered = self.ours(value, Context(autoescape=True))
        self.assertIsInstance(rendered, SafeString)
        self.assertEqual(rendered, "<b>&</b>")

    def test_autoescape_off_skips_the_escape(self):
        """The branch, seen as ``conditional_escape`` *absent*.

        **Not "the return type is ``str``", which is what the plan's row asserts
        and which is false.** ``SafeString.__str__`` returns ``self`` — it has to,
        so that ``str()`` and f-strings cannot launder safety — so a marked value
        comes back still marked and ``type(rendered) is str`` fails on the first
        input anyone would reach for. The plan's Step 2 row was written with a
        marked input and the assertion ``type(rendered) is str``; measured, it
        raises ``AssertionError: <class 'SafeString'> is not <class 'str'>``.

        So the input has to be one the escaping is visible on, and the type
        assertion has to be on that same input — where it *does* discriminate,
        because the two arms return ``str`` and ``SafeString`` respectively. The
        marked case is kept below as the falsifier rather than as the claim.
        """
        from django.template import Context
        from django.utils.safestring import SafeData, SafeString, mark_safe

        off = Context(autoescape=False)
        self.assertEqual(self.ours("<b>", off), "<b>")
        self.assertIs(type(self.ours("<b>", off)), str)

        on = Context(autoescape=True)
        self.assertEqual(self.ours("<b>", on), "&lt;b&gt;")
        self.assertIs(type(self.ours("<b>", on)), SafeString)
        self.assertIsInstance(self.ours("<b>", on), SafeData)

        # The case the plan's row was written on, and the reason it cannot be
        # the claim: ``SafeString`` is unchanged by ``str()``, so this stays a
        # ``SafeString`` with autoescape *off* too.
        rendered = self.ours(mark_safe("<b>"), off)
        self.assertEqual(rendered, "<b>")
        self.assertIs(type(rendered), SafeString)

    def test_the_conversion_test_is_issubclass_of_type_not_isinstance(self):
        """``issubclass(type(value), str)``, and this is the case that says why.

        Both literals, because the pair is the claim: the value is converted and
        escaped by the upstream form, and passed through to
        ``conditional_escape``'s ``__html__`` arm by the ``isinstance`` form.
        Measured on the pinned tree before either number was written down.
        """
        from django.template import Context

        context = Context(autoescape=True)
        value = _IsAStringButSaysSoWithClass()
        self.assertTrue(isinstance(value, str))
        self.assertFalse(issubclass(type(value), str))

        self.assertEqual(
            self.ours(value, context),
            f"&lt;{type(value).__module__}.{type(value).__qualname__} object at "
            f"0x{id(value):x}&gt;",
        )
        self.assertEqual(self.theirs(value, context), self.ours(value, context))

    def test_it_agrees_with_the_pinned_source(self):
        """The table, run against Django's own function from the pinned file.

        Not the whole contract — that is what the rows above are for — but the
        breadth: numbers, ``None``, bytes, a lazy string, a ``__html__`` object,
        a ``str`` subclass, and both autoescape settings. A transcription edit
        that kept the three rows above green and changed an arm would have to
        survive this too.
        """
        from django.template import Context
        from django.utils.functional import lazy
        from django.utils.safestring import mark_safe

        values = [
            "plain",
            mark_safe("<b>safe</b>"),
            7,
            7.5,
            None,
            True,
            b"bytes",
            ["a", "b"],
            lazy(lambda: "lazy", str)(),
            _IsAStringButSaysSoWithClass(),
        ]
        for autoescape in (True, False):
            context = Context(autoescape=autoescape)
            for value in values:
                with self.subTest(autoescape=autoescape, value=repr(value)):
                    self.assertEqual(
                        self.ours(value, context),
                        self.theirs(value, context),
                    )

    def test_the_rows_can_fail(self):
        """The mutation the plan predicted would be invisible, run here.

        ``isinstance`` in place of ``issubclass(type(…))`` is asserted to produce
        the *other* output on the same input, so this file states the divergence
        as an executable fact rather than only in prose. The second form is
        built from upstream's own body with that one expression replaced, so the
        difference measured is the expression and not a rewrite.
        """
        from django.template import Context

        source = inspect.getsource(self.ours).replace(
            "issubclass(type(value), str)", "isinstance(value, str)"
        )
        self.assertNotEqual(source, inspect.getsource(self.ours))
        namespace = dict(self.base.__dict__)
        exec(compile(source, "<mutated>", "exec"), namespace)

        context = Context(autoescape=True)
        value = _IsAStringButSaysSoWithClass()
        self.assertEqual(namespace["render_value_in_context"](value, context), "raw-marker")
        self.assertNotEqual(
            namespace["render_value_in_context"](value, context),
            self.ours(value, context),
        )


if __name__ == "__main__":
    unittest.main()
