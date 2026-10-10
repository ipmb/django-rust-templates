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
`python/django_rust_templates/engine/base.py:1161`, which is ours, and
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
