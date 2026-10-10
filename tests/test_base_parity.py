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
`python/django_rust_templates/engine/base.py:1296`, which is ours, and
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

**A third subject arrived from a gate going red, and it is the smallest.** With
*nothing* configured — no settings at all — both engines refuse to render a
variable, and M19's `int` arm made them name different settings in the refusal.
`UnconfiguredSettingsTests` pins that pair, because the harness that found it
now configures settings and would no longer show it. It is not a compatibility
claim: stock Django cannot render a variable without settings either.

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

# ``django_suite`` as well, because ``Engine(libraries=…)`` resolves its
# dotted paths lazily through ``import_module`` and the fixtures name
# ``template_tests.templatetags.bad_tag``. The same insertion
# ``tests/test_parser_parity.py`` makes, and for the same reason: without it the
# rows pass only when some earlier module happened to put the path there,
# because ``tests/test_host_tags.py`` inserts it and sorts between us.
# The *vendored* corpus and not the checkout's, so the fixture the row resolves
# is the one `gen_django_suite.py` repointed at this package — see the note in
# ``tests/test_parser_parity.py``.
DJANGO_TESTS = PROJECT_ROOT / "django_suite"
if str(DJANGO_TESTS) not in sys.path:
    sys.path.insert(0, str(DJANGO_TESTS))

import django_rust_templates  # noqa: E402

_PINNED_CACHE = {}


def _pinned(name):
    """Django's own ``name``, executed from the pinned file.

    Read with ``ast`` and ``exec``'d into a copy of our module's globals, so the
    body under test is upstream's byte-for-byte while the helpers it calls
    (``conditional_escape``, ``localize``, ``template_localtime``) are the same
    objects the render path uses — those are Django's ``django.utils.*``
    functions and are not transcribed by this project, so sharing them is what
    makes this a comparison of the function and not of its dependencies.

    Not ``importlib``: the shim serves ``django.template.base`` from ``engine``,
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

    base = importlib.import_module("django_rust_templates.engine.base")
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

        base = importlib.import_module("django_rust_templates.engine.base")
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
        self.assertIn("engine", inspect.getfile(self.ours))

    def test_the_modules_that_copied_it_hold_ours(self):
        """The out-of-module call sites, which are copies taken at *import* time.

        Two of this package's modules call the function through a from-import at
        module scope — ``engine/defaulttags.py``, at upstream's
        ``django/template/defaulttags.py:37``, and ``engine/templatetags/i18n.py``,
        at upstream's ``django/templatetags/i18n.py:5`` — so each holds whatever
        the module attribute was when it was imported. That is the difference from
        ``engine/base.py``'s own call, which reads the module global at call time
        and therefore always follows.

        **What this row was before the M18 rewiring, and what it is now.** It was
        an interposition row: it imported ``django.template.defaulttags`` and
        ``django.templatetags.i18n``, which under the shim *were* those two files,
        and asserted that the copies they had taken were ours — a statement about
        whether the shim had been installed before they executed, and the reason
        ``scripts/check.sh`` fixed the startup order. There is no shim and there
        are no two names: both modules are this package's, both import from this
        package's ``base``, and the row is now the simpler and stricter claim that
        the three files agree. The hazard it was written for — a copy frozen
        before the module it copies from is final — is unchanged in shape; it just
        has one tree to be wrong in rather than two.
        """
        import django_rust_templates.engine.defaulttags as defaulttags
        import django_rust_templates.engine.templatetags.i18n as i18n

        self.assertIsNot(defaulttags, i18n, "the two call sites are one module")
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
        from django_rust_templates.engine import Context
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
        from django_rust_templates.engine import Context
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
        from django_rust_templates.engine import Context

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

    def test_the_int_arm_declines_when_grouping_is_on(self):
        """The settings half of the arm's predicate, in both directions.

        The arm is `type(value) is int and (context.use_l10n is False or not
        settings.USE_THOUSAND_SEPARATOR)`. Everywhere else in this file the
        setting is False, so *both* disjuncts are true and a version that
        dropped either one would pass every row above. Here the setting is on,
        and the pair of cases separates them:

          - a default context (``use_l10n`` is ``None``) must **decline**, so
            ``12345`` comes out ``'12,345'`` — Django's grouped number, from
            ``numberformat.format``;
          - ``use_l10n=False`` must still **fire**, so the same value comes out
            ``'12345'``, because upstream's ``localize`` short-circuits on
            ``use_l10n is False`` *before* ``number_format`` is entered at all.

        Both are asserted against the pinned function rather than against a
        literal, and the first is then asserted to *differ* from ``str(value)``
        — the arm's own answer — because equality with the oracle alone cannot
        tell a correct slow path from an arm that fired and agreed by luck.
        That second assertion only has teeth on a value grouping would change:
        for ``-7`` and ``0`` the grouped and ungrouped answers are the same
        string, so the row would pass with the arm firing. Hence two tuples, and
        the first assertion is applied to the one where it discriminates — which
        is not a stylistic split, it is the difference between a row that can
        fail and one that cannot.

        Measured on the pinned tree at 6.0.9, over ``12345``, ``-7``, ``0``,
        ``10**30``, ``7`` and ``1234567``: all **48** combinations of the two
        settings states, two ``use_l10n`` values and two autoescape values agree
        with the pinned function, and the arm declines exactly when
        ``USE_THOUSAND_SEPARATOR`` is on *and* ``use_l10n`` is not ``False``.

        **Both settings states are exercised here, not just the on one.** The
        first version of this row ran the loop inside the ``override_settings``
        block, so it only ever saw ``True`` — and the arm's *other* disjunct is
        the one that fires in production, where the setting is ``False``. The
        default state is also where the one defect this row has caught lives, so
        a row that could not reach it was a row that could not fail on it.

        **And the type is asserted beside the characters**, because that is
        where the defect was: upstream's ``autoescape=False`` answer for an exact
        ``int`` is ``str(localize(value))``, and ``str()`` is the identity on the
        ``SafeString`` that ``numberformat.format`` returns for an ungrouped
        number — so the answer is a ``SafeString`` unless ``use_l10n is False``,
        where ``localize`` returns ``str(value)`` and it is a plain ``str``. The
        arm returned a plain ``str`` in both cases. No render can see the
        difference (``NodeList.render`` joins), which is exactly why the row has
        to compare types rather than output.
        """
        from django.test.utils import override_settings
        from django_rust_templates.engine import Context

        # Grouping changes the output for these three, so a decline is visible.
        grouped = (12345, 10**30, 1234567)
        # These read the same either way — Django's own comment calls the answer
        # ``mark_safe(number)`` and ``str()`` is what makes it text — so they
        # pin the oracle and the types but cannot pin the route.
        unchanged = (-7, 0, 7)
        values = grouped + unchanged

        for grouping_on in (False, True):
            with override_settings(USE_THOUSAND_SEPARATOR=grouping_on):
                for use_l10n in (None, False):
                    # The predicate's answer, restated from the arm: it declines
                    # when grouping could change the value, which is the setting
                    # *and* ``localize`` not having short-circuited.
                    declines = grouping_on and use_l10n is not False
                    for autoescape in (True, False):
                        context = Context(autoescape=autoescape, use_l10n=use_l10n)
                        for value in values:
                            with self.subTest(
                                grouping_on=grouping_on,
                                use_l10n=use_l10n,
                                autoescape=autoescape,
                                value=value,
                            ):
                                rendered = self.ours(value, context)
                                oracle = self.theirs(value, context)
                                self.assertEqual(rendered, oracle)
                                self.assertIs(type(rendered), type(oracle))
                                if declines:
                                    # Only visible where grouping changes the
                                    # answer; ``str(value)`` is the arm's own.
                                    if value in grouped:
                                        self.assertNotEqual(
                                            rendered,
                                            str(value),
                                            "the arm fired with grouping on",
                                        )
                                else:
                                    # The arm fired. Its characters are the
                                    # slow path's here, so this pins the arm's
                                    # *answer* rather than its route — which is
                                    # the most a character can do.
                                    self.assertEqual(rendered, str(value))

    def test_it_agrees_with_the_pinned_source(self):
        """The table, run against Django's own function from the pinned file.

        Not the whole contract — that is what the rows above are for — but the
        breadth: numbers, ``None``, bytes, a lazy string, a ``__html__`` object,
        a ``str`` subclass, and both autoescape settings. A transcription edit
        that kept the three rows above green and changed an arm would have to
        survive this too.
        """
        from django_rust_templates.engine import Context
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
        from django_rust_templates.engine import Context

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


class NativeEscapeTests(unittest.TestCase):
    """`_native.html_escape`, the binding both fast arms trust to be *escape*.

    M19's two arms call it on every `str` and every `int` a template renders, and
    nothing in the gate called it *directly* until this class: the rows above
    reach it only through ``render_value_in_context``, where ``None`` and a
    correct answer are indistinguishable unless the input is chosen to tell them
    apart. Its contract has two sides, and the second one is the one that
    matters.

    **It returns ``None`` for a string it cannot answer for; it does not raise,
    and ``None`` does not mean "unescaped".** Measured on the installed
    extension — ``_native.cpython-314-darwin.so``, sha256 ``df7413aa…``, Django
    6.0.9, interpreter 3.14.4 — a lone surrogate makes Python's UTF-8 encoding
    impossible, and Django's own ``django.utils.html.escape`` returns such a
    string **unescaped but otherwise intact** (`escape('a & \\ud800')` is
    `'a &amp; \\ud800'`; upstream never encodes, so it is not a refusal there
    either). The native fast path cannot produce that answer, so it declines —
    and both arms read the decline as "go the long way", falling through to
    ``conditional_escape``, which is Django's function and gives Django's
    answer. ``test_the_refused_value_still_gets_djangos_answer`` is that claim
    as a row, and it is why the refusal is *safe* rather than merely
    non-fatal: the ``None`` is a control-flow signal, not an output.

    Every literal in these rows is a measured output rather than a recollection
    — the two sides are printed ``repr``-for-``repr`` by the probe whose output
    they were written from — and the values are deliberately the ones where the
    two differ from a naive `str.replace`, i.e. ``'`` becoming ``&#x27;`` and not
    ``&#39;``.
    """

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()
        cls.base = importlib.import_module("django_rust_templates.engine.base")
        cls.native = cls.base._native
        cls.ours = staticmethod(cls.base.render_value_in_context)

    def test_it_agrees_with_djangos_escape_on_every_string_it_answers(self):
        """The positive side, against Django's function rather than a literal.

        ``assertIs(type(...), str)`` as well as the value, because the arms wrap
        the answer in a ``SafeString`` themselves and a binding that returned an
        already-wrapped value would be double-marking what the arm then marks
        again.
        """
        from django.utils.html import escape

        for text in ("", "plain", "a & b", "<b>", "it's", 'say "hi"', "a</b>", "&amp;", "é"):
            with self.subTest(text=text):
                got = self.native.html_escape(text)
                self.assertIs(type(got), str)
                self.assertEqual(got, escape(text))

    def test_a_lone_surrogate_is_refused_with_none_rather_than_raised(self):
        """The negative side, and "refused" is the whole claim.

        A binding that raised would be *caught* by neither arm — the arms test
        ``is not None`` — so the exception would escape ``render_value_in_context``
        and turn a value Django renders fine into a 500. Three spellings, because
        the encoding failure depends on the whole string and not on the character:
        a lone surrogate alone, one with ASCII either side, and a different
        unpaired code point.
        """
        for text in ("\ud800", "a\ud800b", "\udfff"):
            with self.subTest(text=repr(text)):
                self.assertIsNone(self.native.html_escape(text))

    def test_the_refused_value_still_gets_djangos_answer(self):
        """The refusal read the way an arm reads it, on input where it shows.

        The surrogate is mixed with text that *is* escaped, so the two failure
        modes a fallback could have are both visible: dropping the string
        entirely, and skipping the escape on the way past. Autoescaping off is
        the other half — the answer there is the raw string, which is what
        Django gives too.
        """
        from django_rust_templates.engine import Context

        for text in ("\ud800", "a\ud800b", "a & \ud800", "<\ud800>", "\ud800\ud800"):
            for autoescape in (True, False):
                with self.subTest(text=repr(text), autoescape=autoescape):
                    context = Context(autoescape=autoescape)
                    self.assertEqual(
                        self.ours(text, context),
                        _pinned("render_value_in_context")(text, context),
                    )


class FilterSeamRefusalTests(unittest.TestCase):
    """`_native.filter` on a value Rust cannot hold — the equality row.

    Slice 1b. ``classify`` converts a Python ``str`` to Rust's ``Box<str>``
    through ``PyString::to_str``, and a lone surrogate has no UTF-8 encoding, so
    until this the seam raised ``UnicodeEncodeError`` where Django renders the
    string. Measured before the repair, on the installed extension: **all 57
    built-in filter names raised** on ``'\\ud800'``, where Django's own body
    answers 33 of the 37 that take only a value
    (``.review/m19-measurements/surrogate_boundary.{py,txt}``).

    The repair reuses the branch that already existed for a name that is not a
    builtin: the seam returns the ``_native.CANNOT_HOLD`` singleton and
    ``FilterExpression.resolve`` lets Django's own callable answer. That is a
    *refusal*, not an exception — and the first reason written for that was
    **false**: "the render path would swallow it". Measured at 6.0.9,
    ``VariableNode.render`` catches ``UnicodeDecodeError`` and nothing else, and
    a raising filter reaches the caller from all six render shapes tried, with
    ``debug`` on and off
    (``.review/m19-measurements/surrogate_boundary.txt`` §8). The reasons that
    hold are that a refusal is not an *error*, and that the sibling refusal one
    function away — ``_native.html_escape``, tested above — already declines by
    returning.

    Every row below compares against Django's engine rather than a literal, and
    the inputs are chosen so that the two shapes of "nothing" are
    distinguishable: a surrogate the seam cannot hold, and a filter whose
    *answer* is ``None``.
    """

    SURROGATES = ("\ud800", "a\ud800b", "a & \ud800", "\udfff")
    TEMPLATES = (
        "{{ x|upper }}",
        "{{ x|length }}",
        "{{ x|force_escape }}",
        "{{ x|slugify }}",
        "{{ x|truncatechars:2 }}",
        "{{ x|default:y }}",
    )

    @classmethod
    def setUpClass(cls):
        from scripts.filter_probe import ensure_settings

        ensure_settings()
        cls.base = importlib.import_module("django_rust_templates.engine.base")
        cls.native = cls.base._native

    def test_a_string_the_seam_cannot_hold_renders_as_django_renders_it(self):
        """The equality row: whole templates, ours against stock Django.

        Through the whole engine and not the seam, because "reachable" is the
        claim that matters — a seam-level refusal that no template reaches is
        not a divergence. ``upper`` and ``length`` were the two probes that
        showed it; the other four are the shapes that could break a fallback
        quietly: ``force_escape`` is non-idempotent, ``slugify`` rewrites the
        text around the surrogate, ``truncatechars`` counts it, and
        ``default`` is the filter whose *answer* may itself be ``None``.
        """
        import django.template as stock
        from django_rust_templates.engine import Context, Engine

        ours = Engine()
        theirs = stock.Engine()
        for template in self.TEMPLATES:
            for text in self.SURROGATES:
                for autoescape in (True, False):
                    with self.subTest(
                        template=template, text=repr(text), autoescape=autoescape
                    ):
                        variables = {"x": text, "y": None}
                        self.assertEqual(
                            ours.from_string(template).render(
                                Context(variables, autoescape=autoescape)
                            ),
                            theirs.from_string(template).render(
                                stock.Context(variables, autoescape=autoescape)
                            ),
                        )

    def test_the_refusal_is_a_singleton_and_not_nones_answer(self):
        """The protocol, which is the part a repair could get wrong silently.

        ``None`` cannot carry this message: ``default`` returns its argument, so
        ``{{ x|default:y }}`` with a falsy ``x`` and ``y = None`` is a filter
        *answering* ``None``. Both spellings arrive as Python ``None`` from a
        seam that used ``None`` as its sentinel — measured — and the shell would
        then run Django's body a second time over the first. So the sentinel is
        its own object, compared by identity, and this row is what says the two
        are distinguishable.
        """
        refused = self.native.filter("upper", "\ud800", (), True, True)
        self.assertIs(refused, self.native.CANNOT_HOLD)
        self.assertIs(self.native.CANNOT_HOLD, self.native.CANNOT_HOLD)

        answered = self.native.filter("default", None, (None,), True, True)
        self.assertIsNone(answered)
        self.assertIsNot(answered, self.native.CANNOT_HOLD)

    def test_an_ordinary_value_is_unaffected(self):
        """The positive control, over the same population.

        Without it every row above passes for an engine that renders *nothing*
        for these templates — the equality would hold at ``"" == ""``. Same six
        templates, same context, a value a ``str`` Rust can hold; the surrogate
        is the only variable between this row and the one above. Each answer is
        asserted non-empty as well as equal, so "both engines agree" cannot be
        satisfied by agreeing on nothing.
        """
        import django.template as stock
        from django_rust_templates.engine import Context, Engine

        ours = Engine()
        theirs = stock.Engine()
        variables = {"x": "a & B-", "y": "fallback"}
        for template in self.TEMPLATES:
            with self.subTest(template=template):
                mine = ours.from_string(template).render(Context(variables))
                self.assertTrue(mine, f"{template} rendered nothing at all")
                self.assertEqual(
                    mine,
                    theirs.from_string(template).render(stock.Context(variables)),
                )


class UnconfiguredSettingsTests(unittest.TestCase):
    """What the two engines do when there are no settings at all.

    Not a supported configuration — stock Django cannot render a variable
    without settings either — so this is not a compatibility claim and not a row
    about rendering. It is here because M19's `int` arm made the two engines
    disagree *visibly* here, and the disagreement was found by the differential
    harness going red: **11 of its 48 cases differ**, both sides an
    `ImproperlyConfigured` repr. With no settings, both engines refuse, and
    which setting they name is whichever the code happens to read first. Stock
    reaches `get_language()`'s `USE_I18N`; the arm reads
    `settings.USE_THOUSAND_SEPARATOR` before it, because that is the setting its
    predicate is about. The *type* is unchanged.

    **A divergence a harness change hides is worse than one the harness
    reports.** `scripts/differential.py` now configures settings precisely so
    that its 11 int-bearing cases compare rendered numbers rather than these two
    strings — and that change erases the only place the difference showed. So it
    is pinned here, both halves spelled out: same exception type, and the setting
    each engine names. Not asserted equal and not deleted.

    A fresh interpreter, because settings are first-writer-wins for the process
    and every class above this one has already called `ensure_settings()`.
    """

    def test_both_engines_refuse_and_name_the_setting_they_read_first(self):
        import subprocess

        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT / 'python')!r})\n"
            "from django.template.base import render_value_in_context as stock\n"
            "from django.template.context import Context\n"
            "from django_rust_templates.engine import base as ours\n"
            "for label, fn in (('stock', stock),"
            " ('ours', ours.render_value_in_context)):\n"
            "    try:\n"
            "        print(label, 'RENDERED', repr(fn(12345, Context())))\n"
            "    except Exception as exc:\n"
            "        print(label, type(exc).__name__, '|', exc)\n"
        )
        # ``-I``, so neither `PYTHONPATH` nor the user site can put a shim in
        # front of the two names this row is about; the package's own directory
        # is then put on the path explicitly, which is the half `-I` closes.
        proc = subprocess.run(
            [sys.executable, "-I", "-c", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = dict(
            line.split(" ", 1) for line in proc.stdout.splitlines() if " " in line
        )
        self.assertEqual(set(lines), {"stock", "ours"}, proc.stdout)
        for engine in ("stock", "ours"):
            with self.subTest(engine=engine):
                self.assertTrue(
                    lines[engine].startswith(
                        "ImproperlyConfigured | Requested setting "
                    ),
                    lines[engine],
                )
        self.assertIn("Requested setting USE_I18N,", lines["stock"])
        self.assertIn("Requested setting USE_THOUSAND_SEPARATOR,", lines["ours"])


class RenderAnnotationTests(unittest.TestCase):
    """``Node.render_annotated``'s two attachments, entered directly.

    Interposing ``base`` interposes ``Node``, and every node class in
    ``defaulttags``/``loader_tags`` inherits from whatever ``django.template.base``
    binds, so this protocol is ours for the whole suite the moment the module is
    interposed — including nodes Rust did not build, because Rust materialises
    its descriptors into those same Django classes. At M9 it holds by
    transcription. The rows exist because §5.5 has Rust rendering interior
    subtrees from its own compiled form later, and an interior node rendered
    without a Python frame is exactly what would stop ``_culprit_node`` being
    attached at the right level.

    **The plan's Task 6 predicted the ``(start, end)`` pairs would be the
    assertion that discriminates and the ratio would not. Measured: the pairs do
    not discriminate either guard.** Both guards are idempotence guards on the
    exception, and the frame that wins is the innermost either way:

    * removing ``if not hasattr(e, "_culprit_node")`` moves ``_culprit_node``
      from the inner loop to the outer, and leaves ``template_debug``'s
      ``(38, 57)`` **unchanged** — because ``template_debug`` is attached by the
      innermost frame, whose ``_culprit_node`` is still itself, before any outer
      frame can overwrite the attribute;
    * removing ``if not hasattr(e, "template_debug")`` re-derives the same dict
      from the same token, because the culprit guard pinned the same node.

    So the plan's Step 3 — "remove the ``hasattr`` guard, confirm the innermost
    row fails naming the wrong culprit" — would have left the row **green** and
    drawn a conclusion from a mutation nothing could observe. What discriminates
    is node *identity* (first row) and the origin comparison's choice of frame
    (second row), and both were measured by mutation before being written.

    **The pairs are not decorative, though, and the mutation above does not
    reach the case they are for.** The §5.5 failure is not a removed guard, it is
    a *missing frame*: once Rust renders the interior subtree, the inner
    ``{% for %}``'s ``render_annotated`` never runs, so the innermost *available*
    frame is an outer one and ``template_debug`` is computed from the **outer**
    token. There the span moves, and moves with identity. That is why both
    assertions belong in the row and why neither is a spare: identity catches a
    frame that attached at the wrong level while every frame was present, the
    span catches the frame that is not there at all. Trimming this to "the
    identity assertion plus a descriptive extra" would remove the half that
    fires at M11+.
    """

    # ``tests/template_tests/test_nodelist.py:44-86``'s nested case: the outer loop
    # iterates and the inner one does not, so the culprit is a node nested in
    # another node. The flat case is the same exception from the same node class
    # with no nesting at all, which is what makes the pair a comparison.
    NESTED = (
        "{% load bad_tag %}{% for i in range %}{% for j in five %}"
        "{% badsimpletag %}{% endfor %}{% endfor %}"
    )
    FLAT = "{% load bad_tag %}{% for j in five %}{% badsimpletag %}{% endfor %}"

    PARENT = "A{% block body %}x{% endblock %}Z"
    CHILD = (
        "{% extends 'parent.html' %}{% load bad_tag %}{% block body %}"
        "{% badsimpletag %}{% endblock %}"
    )

    @classmethod
    def setUpClass(cls):
        # The suite's settings helper and the shim install, for the reason
        # ``RenderValueInContextTests`` records: settings are first-writer-wins
        # and ``discover`` decides by alphabet.
        from scripts.filter_probe import ensure_settings

        ensure_settings()

    def _engine(self, debug=True, **kwargs):
        from django_rust_templates.engine import Engine

        options = {"libraries": {"bad_tag": "template_tests.templatetags.bad_tag"}}
        options.update(kwargs)
        return Engine(debug=debug, **options)

    def _render_error(self, template, data=None):
        """The exception the fixture raises, or an assertion failure if it does not."""
        from django_rust_templates.engine import Context

        try:
            template.render(Context({} if data is None else data))
        except Exception as error:  # the fixture's whole purpose
            return error
        raise AssertionError("the fixture is supposed to raise")

    def test_culprit_node_is_the_innermost_raising_node(self):
        """The culprit is the *inner* loop, asserted by identity and not by span.

        The span alone cannot see this guard: measured, deleting
        ``if not hasattr(e, "_culprit_node")`` leaves ``template_debug`` at
        ``(38, 57)`` because the annotation was already taken by the frame that
        caught the exception first. The identity does see it — the outer loop
        takes the attribute when the guard is gone — so ``assertIs`` against the
        node object is the assertion, and the span is kept beside it as the part
        that pins *which* text the annotation describes.
        """
        from django_rust_templates.engine.defaulttags import ForNode

        engine = self._engine()
        template = engine.from_string(self.NESTED)
        loops = template.nodelist.get_nodes_by_type(ForNode)
        self.assertEqual(len(loops), 2, "the fixture needs an outer loop and an inner one")
        self.assertEqual(loops[0].token.position, (18, 38), "the outer loop's tag")
        self.assertEqual(loops[1].token.position, (38, 57), "the inner loop's tag")

        error = self._render_error(template, {"range": range(5), "five": 5})

        self.assertEqual(type(error), TypeError, error)
        self.assertIs(
            error._culprit_node,
            loops[1],
            "the culprit is the inner loop; the outer one raised only because the "
            "inner one's exception passed through it",
        )
        self.assertIsNot(error._culprit_node, loops[0])
        debug = error.template_debug
        self.assertEqual((debug["start"], debug["end"]), (38, 57))
        self.assertEqual((debug["start"], debug["end"]), loops[1].token.position)

        # The same exception from the same node class with no nesting, so the
        # row above is a claim about the innermost node rather than about loops.
        flat = engine.from_string(self.FLAT)
        (only,) = flat.nodelist.get_nodes_by_type(ForNode)
        other = self._render_error(flat, {"five": 5})
        self.assertIs(other._culprit_node, only)
        self.assertEqual(
            (other.template_debug["start"], other.template_debug["end"]), (18, 37)
        )

    def test_template_debug_is_attached_only_when_the_origin_matches(self):
        """The frame that annotates is not the innermost one, and that is the point.

        ``{% extends %}`` renders the child's ``{% block %}`` while
        ``context.render_context.template`` is the **parent** — measured, three
        frames receive the exception (``SimpleNode``, ``BlockNode``,
        ``ExtendsNode``) and only the third attaches, because the first two see
        the parent's origin against the child's culprit.

        Teeth, measured rather than argued: delete the origin comparison and the
        *first* frame attaches instead, producing ``name`` ``parent.html`` with
        ``during`` empty and a ``(start, end)`` that is a span of the child's
        source — an annotation that is present, wrong in every field a caller
        reads, and indistinguishable from a correct one by presence alone. That
        is why this row asserts ``name`` and ``during`` and not merely that
        ``template_debug`` exists.

        The controlled half renders the same child block with only the parent's
        state pushed, which is exactly the state the real render passes through
        at its innermost frame: ``_culprit_node`` is attached and
        ``template_debug`` is withheld, because it is the comparison and not the
        ``hasattr`` guard that withholds it.
        """
        from django_rust_templates.engine import Context
        from django_rust_templates.engine.library import SimpleNode
        from django_rust_templates.engine.loader_tags import BlockNode

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "parent.html").write_text(self.PARENT)
            (root / "child.html").write_text(self.CHILD)
            engine = self._engine(dirs=[directory])
            child = engine.get_template("child.html")
            parent = engine.get_template("parent.html")

            error = self._render_error(child)
            debug = error.template_debug
            start = self.CHILD.index("{% badsimpletag %}")
            self.assertEqual(Path(debug["name"]).name, "child.html")
            self.assertEqual(
                (debug["start"], debug["end"]),
                (start, start + len("{% badsimpletag %}")),
            )
            self.assertEqual(debug["during"], "{% badsimpletag %}")
            self.assertEqual(str(debug["message"]), "I am a bad simpletag")

            # The innermost frame, entered alone: the comparison withholds the
            # annotation, and the culprit is attached anyway.
            (block,) = child.nodelist.get_nodes_by_type(BlockNode)
            context = Context({})
            context.template = child
            withheld = None
            with context.render_context.push_state(parent):
                try:
                    block.nodelist.render(context)
                except RuntimeError as error:
                    withheld = error
            self.assertIsNotNone(withheld, "the fixture is supposed to raise")
            self.assertIs(
                withheld._culprit_node,
                block.nodelist.get_nodes_by_type(SimpleNode)[0],
                "the culprit is still attached by the innermost frame",
            )
            self.assertFalse(
                hasattr(withheld, "template_debug"),
                "the innermost frame must not annotate; its template is the parent",
            )

    def test_nothing_is_attached_when_debug_is_off(self):
        """``engine.debug`` gates both attributes, and it is the outer gate.

        Not a row about ``hasattr``: with ``debug=False`` the branch never runs,
        so the exception arrives bare even though the origin would have matched
        and no outer frame has annotated it. A port that moved the branch out of
        the ``if`` — or that attached from Rust, where ``engine.debug`` is one
        more object to reach — produces a ``template_debug`` on a non-debug
        engine, which no Django-side caller would expect.
        """
        engine = self._engine(debug=False)
        template = engine.from_string(self.NESTED)
        error = self._render_error(template, {"range": range(5), "five": 5})

        self.assertFalse(hasattr(error, "_culprit_node"))
        self.assertFalse(hasattr(error, "template_debug"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
