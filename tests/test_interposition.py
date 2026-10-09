"""Direct-entry evidence that the interposed modules are actually ours.

The suite cannot see this. Measured (``.review/m9-recon.md`` finding 3): with
``_impl/base.py`` a byte-for-byte copy of Django's, ``unshimmed()`` reports one
fewer module, the whole suite is green, and the only row that notices is the
meter. So a transcription that delegates nothing, or delegates wrongly, passes
every count this project otherwise accepts.

Every row here imports the module through the shim and asserts on the file it
resolved to, or on a Rust call it must have made — the latter because a
delegation that quietly called Django's own implementation is green too
(finding 1a).

Rows arrive with the commit that makes them true, so every commit is green:
``base``'s here, ``compile_nodelist`` in Task 2, ``smartif`` in Task 3, the
resolver in Task 5, and M10's ``ModuleInterpositionTests`` below.

``ModuleInterpositionTests`` carries M10's rows, one group per module, and each
module gets three kinds of claim that do not imply one another — see that
class's docstring. It uses :func:`render_through`, defined here because Task 7's
loader rows and Task 8's case-sensitivity rows both need the same construction.
"""

import pathlib
import tempfile
import unittest

from django.template import Context, Engine
from django_rust_templates import _shim

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def render_through(tmpdir, source, *, template_name="index.html", **context):
    """Write ``source`` into ``tmpdir`` and render it through a real ``Engine``.

    A real ``Engine``, with the loader spelled as a dotted path, because that is
    the route every Django project and the suite itself take:
    `find_template_loader` does `import_string` on the string, which is the path
    Review Focus 4 is about. ``dirs`` is not passed alongside it —
    `Engine.__init__` raises `ImproperlyConfigured` when `loaders` and `app_dirs`
    are both given — and the tuple form `(path, dirs)` is how Django's own loader
    tests spell the same thing.

    ``template_name`` is keyword-only, and that is load-bearing rather than
    stylistic: the first row to use this helper renders ``"{{ name }}!"`` with
    ``name="Ada"``, and a plain ``name`` parameter would have swallowed the
    variable as the *file's* name. The template then resolves to ``"!"`` and the
    row fails in a way that reads like a resolver bug — measured, before the
    rename. ``name`` is also the commonest context key there is, so the helper is
    the thing that has to give way.
    """
    pathlib.Path(tmpdir, template_name).write_text(source)
    engine = Engine(loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])])
    return engine.get_template(template_name).render(Context(dict(context)))


class InterpositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_base_resolves_to_our_file(self):
        import django.template.base as base

        self.assertIn("_impl", base.__file__, base.__file__)

    def test_the_node_classes_are_defined_by_our_file(self):
        """Resolution and *identity* are different claims.

        The module resolving to our file does not by itself mean the class a
        render uses was defined there: a consumer that bound the name from an
        earlier import keeps Django's object while the module is ours. So the
        assertion is on the defining file.

        **Not on ``__module__``**, which reads ``django.template.base`` for our
        classes and is *supposed* to — the shim loads our file under Django's
        name, so that is the correct value for a drop-in replacement. The first
        draft of this row asserted ``"_impl" in cls.__module__`` and failed on
        all five classes for exactly that reason.
        """
        import inspect

        import django.template.base as base

        for name in ("Template", "NodeList", "Node", "Variable", "VariableNode"):
            with self.subTest(name=name):
                self.assertIn("_impl", inspect.getfile(getattr(base, name)))

    def test_compile_nodelist_reaches_rust(self):
        """The parse seam, pinned to a Rust call rather than to a result.

        The counts are not evidence for this: a ``compile_nodelist`` that
        quietly called Django's own ``Lexer`` and ``Parser`` keeps every count
        green (``.review/m9-recon.md`` finding 1a — this is the failure the M9
        transcription makes *possible*, since our ``Lexer`` and ``Parser`` are
        themselves interposed and therefore look ours). So the row wraps the
        binding and asserts it was entered, which fails the moment the
        delegation is reverted, whatever the templates render.

        ``Engine.from_string`` is the caller because it is the one the suite
        uses 1170 times: ``Template.__init__`` ends in ``compile_nodelist``.
        """
        from django.template import Engine

        from django_rust_templates import _native

        calls = []
        original = _native.compile_template

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.compile_template = counting
        try:
            Engine().from_string("{{ a }}{% if b %}x{% endif %}")
        finally:
            _native.compile_template = original

        self.assertEqual(len(calls), 1, calls)

    def test_compile_nodelist_preserves_extra_data(self):
        """``extra_data`` is assigned between ``parse()`` and the ``return``.

        Upstream sets it inside the ``try`` (``base.py:198-200``) and
        ``{% partial %}`` reads it at M13, so a delegation that returned the
        nodelist and nothing else would drop it. It is also the one attribute
        the *shell* cannot set — ``render.parse_template`` returns a ``Template``
        built by ``__new__``, and the docstring there says why setting
        ``extra_data`` in two places is how the two would drift.
        """
        from django.template import Engine

        template = Engine().from_string("{% csrf_token %}")
        self.assertTrue(hasattr(template, "extra_data"))
        self.assertIsInstance(template.extra_data, dict)

    def test_compile_nodelist_attaches_debug_info_to_a_parse_error(self):
        """The debug arm: a failed parse is annotated, not merely re-raised."""
        from django.template import Engine
        from django.template.exceptions import TemplateSyntaxError

        engine = Engine(debug=True)
        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string("{% badtag %}")

        self.assertTrue(hasattr(caught.exception, "template_debug"))
        self.assertEqual(caught.exception.template_debug["during"], "{% badtag %}")

    def test_smartif_resolves_to_our_file(self):
        """`defaulttags`'s ``IfParser``/``Literal`` come from our module.

        The plan's Step 5 row, unchanged: the shim loads our file under Django's
        name, so this is the ``__file__`` a reader can check by hand.
        """
        import django.template.smartif as smartif

        self.assertIn("_impl", smartif.__file__, smartif.__file__)

    def test_the_if_condition_tree_comes_from_rust_not_the_python_grammar(self):
        """Why ``smartif`` is interposed, pinned as a conjunction.

        **The plan's Step 6 premise is false, and this row is the measurement.**
        It asked for a row showing the interposed ``IfParser`` reaches
        ``_native.parse_filter_expression``. Measured: it reaches nothing,
        because it is never entered. ``Engine().from_string("{% if a and b %}")``
        calls ``_native.parse_filter_expression`` **zero** times and
        ``smartif.IfParser.parse`` **zero** times — M5 moved the whole
        ``{% if %}`` grammar into ``crates/template-core/src/parser/smartif.rs``,
        which the parse seam drives inside ``compile_template``, and the shell
        then materialises the resulting ``IfCondition`` tree into real
        ``smartif`` objects (``crates/template-py/src/lib.rs:740``). A row
        named after the plan's premise would have asserted something untrue
        about a call that does not happen, so it is renamed and rewritten here;
        the substitution is recorded in the plan's Task 3 corrections.

        What is worth pinning is therefore the pair, which is the shape the
        render-parity repair took: the tree is **built by Rust** and **not built
        by the Python grammar**. Our ``IfParser`` is a faithful transcription,
        so a regression that routed ``{% if %}`` back through Python would keep
        every count green, every render correct, and Rust blind to the
        expression — the same Rust-against-Rust hazard one level down.

        The classes are asserted as well, because that is what interposition
        actually buys here: ``IfNode.render`` evaluates objects defined by our
        file, and ``TemplateLiteral`` — Django's own leaf, in the *unshimmed*
        ``defaulttags`` — subclasses our ``Literal``, which is the sharpest form
        of the claim and the one that would break first if the shim's ordering
        changed.
        """
        import inspect

        import django.template.smartif as smartif
        from django.template import Engine
        from django.template.defaulttags import TemplateLiteral

        from django_rust_templates import _native

        compiled = []
        original_compile = _native.compile_template

        def counting_compile(*args, **kwargs):
            compiled.append(args[0])
            return original_compile(*args, **kwargs)

        parsed = []
        original_parse = smartif.IfParser.parse

        def counting_parse(self, *args, **kwargs):
            parsed.append(self)
            return original_parse(self, *args, **kwargs)

        _native.compile_template = counting_compile
        smartif.IfParser.parse = counting_parse
        try:
            template = Engine().from_string("{% if a and b %}x{% endif %}")
        finally:
            _native.compile_template = original_compile
            smartif.IfParser.parse = original_parse

        self.assertEqual(len(compiled), 1, "the parse seam did not run")
        self.assertEqual(parsed, [], "the Python grammar parsed the condition")

        condition = template.nodelist[0].conditions_nodelists[0][0]
        self.assertIs(type(condition), smartif.OPERATORS["and"])
        self.assertIn("_impl", inspect.getfile(type(condition)))
        self.assertIs(TemplateLiteral.__mro__[1], smartif.Literal)
        self.assertIn("_impl", inspect.getfile(TemplateLiteral.__mro__[1]))

    def test_compile_nodelist_keeps_upstreams_error_message_rewriting(self):
        """The obligation this milestone's own plan omitted, so it gets a row.

        Upstream's ``compile_nodelist`` has **four** obligations, not three: as
        well as ``extra_data``, the debug annotation and the re-raise, it
        prefixes a ``TemplateSyntaxError``'s message with the template's origin
        name when the origin is named — ``base.py:204-212``:

            e.raw_error_message = raw_message
            e.args = (f"Template: {self.origin.name}, {raw_message}", ...)

        The plan's Step 1 transcription of the method stopped at the debug arm,
        and its Step 4 delegation put ``return parsed.nodelist`` *inside* the
        ``try`` with an ``except`` arm marked ``# pragma: no cover`` — which
        would have made this code unreachable and silently dropped the prefix
        for every template loaded from a file, which is every template the
        suite's loader and backend tests use. ``Engine.from_string`` cannot see
        it, because its origin is ``UNKNOWN_SOURCE`` and the rewrite is
        conditional on the name differing from that.
        """
        from django.template import Engine, Origin, Template
        from django.template.exceptions import TemplateSyntaxError

        origin = Origin(name="some/template.html", template_name="some/template.html")
        with self.assertRaises(TemplateSyntaxError) as caught:
            Template("{% badtag %}", engine=Engine(), origin=origin)

        raw = caught.exception.raw_error_message
        self.assertEqual(
            caught.exception.args[0],
            f"Template: some/template.html, {raw}",
        )

    def test_the_transcription_is_the_pinned_source(self):
        """A second opinion on the file itself, independent of the guard.

        ``check_transcriptions.py`` says the differing regions are the declared
        ones; that is a statement about a diff's *edges*. This says the *bulk*
        of the file is upstream's, by content rather than by region arithmetic,
        which is the thing a reader wants before believing the guard.

        The metric is the **number of lines in ``difflib``'s equal blocks** —
        the lines that are upstream's verbatim and in place — and it is pinned.
        The first version of this row pinned the line *count* instead ("ours is
        upstream plus the marker and its blank"), which M9's own Task 2 broke,
        because a delegation is a real edit and not a marker. The second version
        — written and thrown away in the same sitting — counted lines that
        ``in upstream``, which is a membership test against the whole 1207-line
        file and therefore measures nothing: it returned 1192, the entire
        untouched population, because every line of Django's ``base.py`` appears
        *somewhere* in it. A replacement of the whole file would have scored the
        same. Equal blocks are what discriminate, because an equal block is by
        construction a maximal run of identical lines: edit one and the run
        breaks.

        The declared regions are deliberately *not* subtracted here. Subtracting
        them would make this row agree with the guard by construction — the two
        would be right and wrong together, which is not a second opinion. What
        this row is for is a file that has been replaced or rewritten wholesale,
        where the guard's regions are still individually valid and the file is
        nonetheless no longer a transcription.
        """
        import difflib
        import pathlib

        from django_rust_templates import _shim as shim

        project_root = pathlib.Path(__file__).resolve().parent.parent
        ours = (shim._IMPL_DIR / "base.py").read_text().splitlines()
        theirs = (project_root / ".django" / "django" / "template" / "base.py")
        theirs = theirs.read_text().splitlines()

        matcher = difflib.SequenceMatcher(a=ours, b=theirs, autojunk=False)
        opcodes = matcher.get_opcodes()
        equal = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag == "equal")
        differing = [op for op in opcodes if op[0] != "equal"]

        # The structure, which is what the guard reads: six edits — the marker
        # and its blank line as one pure insertion of ours, then two pairs for
        # `compile_nodelist`'s delegation, then M11 Task 2's filter seam as a
        # second pure insertion, then two more for `Variable._resolve_lookup`'s.
        # Asserted here too because a re-alignment that merged two edits into
        # one would move `equal` in a way this row could not explain on its own.
        #
        # Five rather than three since M9's Task 5, and the pair that grew is
        # the one worth reading: `difflib` splits the resolver's single
        # replacement in two because our `return` realigns against upstream's
        # `if getattr(e, "silent_variable_failure", …)` tail rather than against
        # the line that follows the loop. Both spans are declared, so the guard
        # is satisfied either way; this row is what would notice if a future
        # edit merged them and the declaration stopped describing the diff.
        #
        # **Six rather than five since M11 Task 2, and the sixth is a `delete`.**
        # That is the filter seam: ours 815-847 — a comment block, two imports,
        # the identity test, the call and its `continue` — against none of
        # upstream's, because the arm *replaces* `resolve`'s loop body with a
        # call rather than wrapping it. `difflib` names a run that only one side
        # has `delete` when it is ours and `insert` when it is theirs, so the
        # shape of this list is also the check that the seam stayed an insertion:
        # an arm that had been written as a *rewrite* of the loop would show up
        # as a `replace` here and as a declared region with two spans.
        #
        # **The run is 33 lines and 17 of them are executable, and the two
        # numbers are both in this file and `check_transcriptions.py` because
        # this seam has already been miscounted three ways.** `difflib` counts
        # lines in the run; `executable_lines` counts the lines of it that hold a
        # statement, and a parenthesised `import` contributes six of the
        # seventeen. Neither is the other's denominator. Hoisting those imports
        # above the `for` — tried and reverted — would have split this one
        # `delete` into two, with upstream's verbatim `arg_vals` loop equal in
        # place between them, which is why the shape of this list is worth
        # asserting rather than deriving.
        self.assertEqual(
            [op[0] for op in differing],
            ["delete", "replace", "replace", "delete", "replace", "replace"],
        )

        self.assertEqual(
            equal,
            1121,
            "the number of lines this transcription shares with upstream, in "
            "place, has moved; if that was a delegation, re-pin it here and "
            "declare the region in scripts/check_transcriptions.py",
        )

    def test_the_compared_path_is_the_measured_one(self):
        """Re-measure the guard's ``COMPARED_PATH`` table and compare, per module.

        That table is what ``tests/parity_target.py`` uses to decide whether a
        declared transcription region sits on code the parity harness executes.
        A pinned table is only as good as the thing that notices it went stale,
        and the failure it would hide is the worst one available: a region
        covering the compared path makes a parity test Rust against Rust, and
        both sides then agree no matter what Rust does.

        So the measurement is run here, in the same process as the parity rows,
        for **every** module in the pin — not for the one this milestone happens
        to be working on. Iterating the pin is what gives the guard its teeth
        without a second place to remember: ``check_transcriptions.py`` asserts
        the pin's key set equals the declared set, so interposing a module
        without measuring it fails in the commit that interposes it, and this
        row then fails if the spans it was given are wrong.

        Both corpora, because they differ: the generator reaches ``Parser.error``
        and ``Parser.invalid_block_tag`` and the hand-written corpus does not —
        and they are built by ``parser_corpus.parity_sources()`` rather than
        here, because the agreement gate in
        ``test_compared_path_instrument`` measures over the same run.

        The ``_impl`` assertion is the reviewer's question — "does ``module_of``
        resolve a declared-but-not-yet-transcribed module?" — answered inside the
        row rather than in prose: it does, and it resolves it to *Django's* file,
        because that is what the fallback is for. The guard itself catches that
        case (`ours_path.is_file()`), but the guard is a separate step of
        ``check.sh`` and a bare ``python -m unittest`` runs this row without it.
        """
        import importlib

        import scripts.check_transcriptions as guard
        from tests.compared_path import measure
        from tests.parser_corpus import parity_sources
        from tests.test_parser_parity import django_parse

        sources = parity_sources()

        for name, pinned_entries in sorted(guard.COMPARED_PATH.items()):
            module = importlib.import_module(guard.module_of(name))
            # That the import found *our* file, not Django's fallback. Without
            # this the row measures Django's functions whenever the transcription
            # is absent, and the pins would still match for a module whose
            # declared regions are a copy of upstream's — the guard's own
            # per-module loop reports a missing file, but it runs in `check.sh`
            # *after* the unit suite, so a bare `python -m unittest` would have
            # this row answering about the wrong module and saying nothing.
            self.assertIn(
                "_impl", module.__file__, f"{name} did not resolve to our file"
            )
            measured = measure(module, sources, django_parse)
            pinned = {label: (first, last) for first, last, label in pinned_entries}

            with self.subTest(module=name):
                self.assertEqual(
                    sorted(measured),
                    sorted(pinned),
                    f"the functions the parity harness executes in {name} have "
                    f"changed, so COMPARED_PATH['{name}'] is stale — regenerate "
                    "it with .review/m9-measurements/m9_measure_parse_path.py "
                    f"{name} (the module is an argument; it prints the entry "
                    "ready to paste)",
                )
            for label in sorted(pinned):
                with self.subTest(module=name, function=label):
                    self.assertEqual(measured[label], pinned[label])


class ModuleInterpositionTests(unittest.TestCase):
    """Every module M10 interposes, with the evidence that it is ours.

    Three claims per module, and none of them implies the others:

    * **resolution** — importing the dotted name gives our file. This is what
      the meter and `check.sh`'s pre-flight read.
    * **identity** — the class a caller gets is not the pinned copy's. Resolution
      and identity differ: a module can be ours while a consumer that imported
      the name earlier still holds Django's object, and `isinstance` is then
      false across the boundary.
    * **reach** — the code path the milestone is about actually enters it.
      Measured with `tests/compared_path.instrumented`, over the parity harness
      the `COMPARED_PATH` table was measured with, so the row and the pin are one
      spelling of the question. Where a module is on the *render* path rather
      than the parse path, the row renders through a real `Engine` instead, and
      the pinned copy is instrumented alongside as a positive control: a
      non-zero count only shows the instrument counted something.

    Rows arrive with the commit that makes them true, so every commit is green.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_utils_resolves_to_our_file(self):
        import django.template.utils as utils

        self.assertIn("_impl", utils.__file__, utils.__file__)

    def test_the_engine_handler_class_is_not_djangos(self):
        import django.template.utils as utils

        from tests.pinned_module import load_pinned

        pinned = load_pinned("utils.py")
        self.assertIsNot(utils.EngineHandler, pinned.EngineHandler)
        self.assertIsNot(
            utils.InvalidTemplateEngineError, pinned.InvalidTemplateEngineError
        )

    def test_the_package_built_its_handler_from_our_class(self):
        """The discriminating row: ``django.template.engines`` is *our* handler.

        ``_impl/__init__.py`` is a transcription and does ``from .utils import
        EngineHandler``, then ``engines = EngineHandler()``. So the singleton that
        ``loader.py`` reaches through ``engines.all()`` is built from whichever
        class that import bound — and if the interposition were absent it would be
        Django's, while the two rows above stayed green.
        """
        import django.template as package
        import django.template.utils as utils

        self.assertIs(package.engines.__class__, utils.EngineHandler)

    def test_the_engine_lookup_runs_our_code(self):
        """``engines.all()`` is the route out of ``utils`` into a template load.

        The suite's settings have ``TEMPLATES = []`` — the project's own
        ``tests/__init__.py`` configures with ``USE_I18N`` only, so the global
        default applies — which makes ``all()`` an empty list and a *safe* call.
        The count is what the row reads, not the result.
        """
        import django.template as package
        import django.template.utils as utils

        from tests.compared_path import instrumented

        with instrumented(utils) as counts:
            self.assertEqual(list(package.engines.all()), [])

        self.assertIn("EngineHandler.all", counts)

    def test_context_resolves_to_our_file(self):
        import django.template.context as context

        self.assertIn("_impl", context.__file__, context.__file__)

    def test_the_context_classes_are_not_djangos(self):
        import inspect

        import django.template.context as context

        from tests.pinned_module import load_pinned

        pinned = load_pinned("context.py")
        for name in (
            "BaseContext",
            "Context",
            "ContextDict",
            "RequestContext",
            "RenderContext",
        ):
            with self.subTest(name=name):
                ours = getattr(context, name)
                self.assertIsNot(ours, getattr(pinned, name))
                self.assertIn("_impl", inspect.getfile(ours))

    def test_a_template_renders_through_our_context(self):
        """The end-to-end row: `Context` is on the render path, not beside it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rendered = render_through(tmpdir, "{{ name }}!", name="Ada")
        self.assertEqual(rendered, "Ada!")

    def test_the_parse_harness_runs_our_context(self):
        """The compared path, pinned to the three functions it measured.

        Measured (`.review/m10-measurements/m10_measure_compared_path.py`): the
        parity harness executes `BaseContext.__init__`, `BaseContext._reset_dicts`
        and `Context.__init__` and nothing else in this file — and every source
        that reaches them is a `{% verbatim %}` row. The harness's Python half
        runs `Parser.parse`, and Django's `do_verbatim` renders the block *at
        parse time* with a fresh `Context()` (`defaulttags.py:1607`), which is
        the route. So the row runs that source, and it will move the day that
        line stops being the route — which is why the failure message names it.

        The second half is a **positive control**: the same call, with the pinned
        copy of this file instrumented instead, must reach nothing. Without it a
        non-zero count would only show that the instrument counted something.
        """
        import django.template.context as context

        from tests.compared_path import instrumented
        from tests.pinned_module import load_pinned
        from tests.test_parser_parity import django_parse

        with instrumented(context) as counts:
            django_parse("{% verbatim %}a{% endverbatim %}")
        self.assertIn("Context.__init__", counts)
        self.assertIn("BaseContext.__init__", counts)

        with instrumented(load_pinned("context.py")) as pinned_counts:
            django_parse("{% verbatim %}a{% endverbatim %}")
        self.assertEqual(sum(pinned_counts.values()), 0)

    def test_engine_resolves_to_our_file(self):
        import django.template.engine as engine

        self.assertIn("_impl", engine.__file__, engine.__file__)

    def test_the_engine_class_is_not_djangos(self):
        import inspect

        import django.template.engine as engine

        from tests.pinned_module import load_pinned

        self.assertIsNot(engine.Engine, load_pinned("engine.py").Engine)
        self.assertIn("_impl", inspect.getfile(engine.Engine))
        # `django.template.Engine` is the same object, because the package
        # `__init__` imports it from this module by name.
        import django.template as package

        self.assertIs(package.Engine, engine.Engine)

    def test_the_parse_harness_runs_our_engine(self):
        """The compared path here is `Engine.__init__` and its two lookups.

        Measured: `django_parse` builds a **fresh** `Engine` per call
        (`test_parser_parity._library_engine`), so the harness enters
        `Engine.__init__`, `Engine.get_template_builtins` and
        `Engine.get_template_libraries` on every source — and because the engine
        is fresh each time, the count is per call rather than once per process.
        The positive control is the pinned copy, which must reach nothing.
        """
        import django.template.engine as engine

        from tests.compared_path import instrumented
        from tests.pinned_module import load_pinned
        from tests.test_parser_parity import django_parse

        with instrumented(engine) as counts:
            django_parse("{{ x }}")
        for label in (
            "Engine.__init__",
            "Engine.get_template_builtins",
            "Engine.get_template_libraries",
        ):
            self.assertIn(label, counts)

        with instrumented(load_pinned("engine.py")) as pinned_counts:
            django_parse("{{ x }}")
        self.assertEqual(sum(pinned_counts.values()), 0)

    def test_a_template_loads_and_renders_through_our_engine(self):
        """The milestone's premise in one row: our `Engine`'s own load path.

        `get_template` → `find_template` → `find_template_loader` (which does
        `import_string` on the dotted path) → the loader's `get_contents`. At
        this commit the loader itself is still Django's, which is why the row
        asserts the *engine* functions and not the loader's; Task 7 extends it.
        """
        import django.template.engine as django_engine

        from tests.compared_path import instrumented

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "greet.html").write_text("{{ name }}!")
            engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )
            with instrumented(django_engine) as counts:
                rendered = engine.get_template("greet.html").render(
                    Context({"name": "Ada"})
                )
        self.assertEqual(rendered, "Ada!")
        self.assertIn("Engine.get_template", counts)

    def test_loaders_with_app_dirs_is_still_refused(self):
        """An argument-combination contract, kept across interposition.

        `Engine.__init__` raises `ImproperlyConfigured("app_dirs must not be set
        when loaders is defined.")` — it is the guard every misconfigured
        `TEMPLATES` setting hits — and no `.django` test reaches it through our
        engine, so without this row a transcription that dropped the `if` would
        ship silently. The message is asserted, not just the type: it is what a
        user of this software sees.

        `assertRaisesRegex` and not `assertRaisesMessage`: that helper is
        Django's, for `SimpleTestCase`, and these rows are plain `unittest` on
        purpose — they have to be runnable without Django's test runner, which
        is the same reason `render_through` builds its own `Engine`.
        """
        import re

        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaisesRegex(
            ImproperlyConfigured,
            re.escape("app_dirs must not be set when loaders is defined."),
        ):
            Engine(app_dirs=True, loaders=["django.template.loaders.filesystem.Loader"])

    def test_the_utils_zero_is_a_measurement_and_not_a_silence(self):
        """A zero is evidence only if the same instrument reports non-zero nearby.

        ``COMPARED_PATH['utils.py']`` is ``[]``, and an empty pin is the one kind
        of pin a *broken instrument* also produces: if the wrapper never fired,
        every measured set would be empty and the empty ones would agree with it.
        The failure to fear for a claim of the form "this count is zero" is
        therefore the instrument reporting zero for **everything**.

        The guard already covers that globally —
        ``test_the_compared_path_is_the_measured_one`` re-measures ``base.py``'s
        twenty-nine functions in the same pass, so a globally dead instrument
        fails there. This row adds the same guarantee *locally*, beside the claim
        it underwrites, because "the instrument fires, and it did not fire here"
        is one statement and the two halves should not be able to drift apart.

        The control this **replaces** was instrumenting the *pinned* copy of a
        module and asserting zero. That refutes a different risk — an instrument
        that counts by name rather than by identity — and it is a fine locality
        check, but it cannot refute a silent instrument, because a module the
        harness never imports is silent either way.

        ``engine`` is the positive half rather than ``base`` or ``defaulttags``
        because it is entered on every source through the same ``django_parse``,
        so the two halves are one call apart rather than one corpus apart.
        """
        import django.template.engine as engine
        import django.template.utils as utils

        from tests.compared_path import instrumented
        from tests.test_parser_parity import django_parse

        with instrumented(engine) as positive:
            django_parse("{{ x }}")
        self.assertGreater(
            sum(positive.values()), 0, "the instrument reports nothing at all"
        )

        with instrumented(utils) as counts:
            django_parse("{{ x }}")
        self.assertEqual(sum(counts.values()), 0)

    LOADER_MODULES = [
        "loaders",
        "loaders.base",
        "loaders.filesystem",
        "loaders.app_directories",
        "loaders.cached",
        "loaders.locmem",
    ]

    def test_every_loader_module_resolves_to_our_file(self):
        import importlib

        for name in self.LOADER_MODULES:
            with self.subTest(name=name):
                module = importlib.import_module(f"django.template.{name}")
                self.assertIn("_impl", module.__file__, module.__file__)

    def test_the_package_still_reaches_djangos_siblings(self):
        """The fallback survives one level down, which is the whole hazard.

        `django.template.loaders` is a package now, and a package's `__path__`
        is built by the finder from *our* directory plus Django's. If the second
        half were missing, every loader we have not taken would stop resolving —
        and the failure would appear when a milestone is half-finished, which is
        exactly when the fallback is needed.

        Asserted as two memberships rather than as a length, because "our
        directory is first" is the ordering the finder promises and "Django's is
        also there" is the half that breaks; a `len(__path__) == 2` would be
        true of our directory twice.
        """
        import django.template.loaders as loaders

        paths = [pathlib.Path(p).resolve() for p in loaders.__path__]
        self.assertIn(
            (
                PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "loaders"
            ).resolve(),
            paths,
        )
        self.assertTrue(
            any(p.name == "loaders" and "_impl" not in str(p) for p in paths),
            f"Django's own loader directory is not on {paths}",
        )

    def test_the_loader_classes_are_not_djangos(self):
        import importlib
        import inspect

        from tests.pinned_module import load_pinned

        for name, relative in [
            ("loaders.base", "loaders/base.py"),
            ("loaders.filesystem", "loaders/filesystem.py"),
            ("loaders.app_directories", "loaders/app_directories.py"),
            ("loaders.cached", "loaders/cached.py"),
            ("loaders.locmem", "loaders/locmem.py"),
        ]:
            with self.subTest(name=name):
                module = importlib.import_module(f"django.template.{name}")
                self.assertIsNot(module.Loader, load_pinned(relative).Loader)
                self.assertIn("_impl", inspect.getfile(module.Loader))

    def test_our_app_directories_loader_subclasses_our_filesystem_loader(self):
        """The cross-module inheritance, which is what a half-built package breaks.

        `loaders/app_directories.py` does `from .filesystem import Loader`. With
        the package interposed, that relative import resolves through our
        `__path__` — so the base class is ours, and this row is what says so.
        Interposing one of the two without the other would leave Django's class
        as the base of ours, and `isinstance` against either would then be false
        for half the objects in the tree.

        The `not issubclass(…, pinned.Loader)` half is the discriminating one:
        with only our two modules interposed the first assertion is already true
        of a hierarchy rooted in Django's class, since our `filesystem.Loader`
        subclasses it. What this milestone claims is the *inner* link.
        """
        import django.template.loaders.app_directories as app_directories
        import django.template.loaders.filesystem as filesystem

        from tests.pinned_module import load_pinned

        self.assertTrue(
            issubclass(app_directories.Loader, filesystem.Loader),
            app_directories.Loader.__mro__,
        )
        self.assertFalse(
            issubclass(app_directories.Loader, load_pinned("loaders/filesystem.py").Loader),
            app_directories.Loader.__mro__,
        )

    def test_the_engines_loader_class_is_ours(self):
        """Review Focus 4, as a row: a dotted path in settings resolves to us.

        `Engine.find_template_loader` does `import_string("django.template.
        loaders.filesystem.Loader")`, which is the spelling in `TEMPLATES` for
        every Django project and in Django's own loader tests. So the assertion
        that matters is not that the module is ours but that the *object the
        engine instantiated* is ours — and that it is not the pinned copy's.
        """
        from tests.pinned_module import load_pinned

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ name }}")
            engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )
            loader = engine.template_loaders[0]

        import django.template.loaders.filesystem as filesystem

        self.assertIsInstance(loader, filesystem.Loader)
        self.assertNotIsInstance(loader, load_pinned("loaders/filesystem.py").Loader)

    def test_every_loader_class_loads_and_renders(self):
        """Each loader on its own path, constructed the way upstream spells it.

        Four constructors, and the differences are what the row is for — a
        transcription that "tidied" a signature fails here rather than in the
        suite, where the message would be about a template that did not load.
        Every construction below is copied from the pinned file being exercised:

        * ``base.py`` — ``Loader(engine)``; its ``get_template_sources`` raises
          ``NotImplementedError``, so it has no path of its own and the
          assertion about it is the ``issubclass`` below.
        * ``filesystem.py`` — ``Loader(engine, dirs=None)``.
        * ``cached.py`` — ``Loader(engine, loaders)``, where ``loaders`` is a
          list of **specs** that ``__init__`` resolves through
          ``engine.get_template_loaders()``; the inner filesystem loader is
          therefore spelled as a tuple, which is what gives it its directory.
        * ``locmem.py`` — ``Loader(engine, templates_dict)``, keyed by
          ``origin.name`` — the template name as written, not a path.
        * ``app_directories.py`` — no ``__init__`` of its own; it inherits
          ``FilesystemLoader``'s and overrides ``get_dirs()``. The override is
          the class, so its row is the next one.
        """
        import django.template.loaders.base as base
        import django.template.loaders.cached as cached
        import django.template.loaders.locmem as locmem

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ a }}")

            filesystem_engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )
            self.assertEqual(
                filesystem_engine.get_template("index.html").render(
                    Context({"a": "x"})
                ),
                "x",
            )
            self.assertTrue(
                issubclass(filesystem_engine.template_loaders[0].__class__, base.Loader)
            )

            cached_engine = Engine(
                loaders=[
                    (
                        "django.template.loaders.cached.Loader",
                        [("django.template.loaders.filesystem.Loader", [tmpdir])],
                    )
                ]
            )
            loader = cached_engine.template_loaders[0]
            self.assertIsInstance(loader, cached.Loader)
            self.assertEqual(
                cached_engine.get_template("index.html").render(Context({"a": "x"})),
                "x",
            )
            # The cache is this loader's behaviour, and the row is that it is
            # populated by a load rather than merely declared.
            self.assertIsNot(loader.get_template_cache, {})

            locmem_engine = Engine(
                loaders=[
                    ("django.template.loaders.locmem.Loader", {"index.html": "{{ a }}"})
                ]
            )
            self.assertIsInstance(locmem_engine.template_loaders[0], locmem.Loader)
            self.assertEqual(
                locmem_engine.get_template("index.html").render(Context({"a": "x"})),
                "x",
            )

    def test_the_app_directories_loader_keeps_its_override(self):
        """``app_directories.Loader`` is three lines, and all three are assertions.

        It declares no ``__init__`` — it inherits ``FilesystemLoader``'s — and
        overrides ``get_dirs()`` to return ``get_app_template_dirs("templates")``,
        which is **ours** from Task 3. That override is why passing ``dirs`` to it
        does nothing, and this row asserts exactly that: with no app declaring a
        ``templates/`` directory (the suite's settings have ``INSTALLED_APPS=[]``),
        the loader finds nothing even though it was given a directory that has
        the file. A transcription that dropped the override would find it.

        That it finds templates when an app *does* declare them is
        ``.django/tests/template_tests/test_loaders.py::AppDirectoriesLoaderTests``
        — the milestone's ``test_loaders`` gate — which is where the app-registry
        machinery that row needs already lives.
        """
        from django.template import TemplateDoesNotExist

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ a }}")
            engine = Engine(
                loaders=[("django.template.loaders.app_directories.Loader", [tmpdir])]
            )
            loader = engine.template_loaders[0]
            self.assertEqual(list(loader.get_dirs()), [])
            with self.assertRaises(TemplateDoesNotExist):
                engine.get_template("index.html")


class LoaderTagsInterpositionTests(unittest.TestCase):
    """M11 Task 1: ``loader_tags`` is ours, and the row it expires is re-read.

    ``loader_tags`` is the module whose interposition **expires a vacuity reason
    M7 recorded**, so this class carries two claims that are usually separate:

    * that the module, its classes and the ``Node`` it inherits are ours; and
    * that Django's own ``test_include_cache`` — the row §10.3 names as M7's gate
      and M7 had to declare vacuous — is now **real signal**, decided by
      breaking the behaviour and watching which rows fail rather than by
      inference.

    The measurement, since the second claim cannot be read off a green run: with
    ``IncludeNode.render``'s cache bypassed in ``_impl/loader_tags.py`` — the
    three lines keyed on ``self`` replaced by a bare
    ``context.template.engine.select_template(template_name)`` — Django's row at
    ``syntax_tests/test_include.py:371-397`` fails ``'000000000' != '012345678'``,
    and so does ``tests/test_inheritance_gate.py``'s counter row. Before the
    interposition the first of those could not move: it ran on stock Django's
    ``IncludeNode`` whatever we wrote.

    **Both instruments stay, and which is which is now stated rather than left
    to inference.** Django's row is the *end-to-end* one — it reaches the cache
    through ``render_to_string`` and the real loader, which is the path a Django
    project takes and the one nothing else here reproduces. The gate's counter
    row is the *direct-entry* one: it calls our ``render_string`` directly and
    renders stock in the same row, so it is the only one of the two that can
    report our engine disagreeing with Django about the same input. And the
    gate's other row — the node-count shape assertion — is neither, and is the
    one thing the pair above still cannot see: two nodes rendering identical
    output pass both counter rows. §10.3's Step 6 caution applies to all three:
    a vacuity justification is a claim about the tree on a date, and this is the
    milestone that dated it.
    """

    def setUp(self):
        _shim.install()

    def test_loader_tags_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django.template.loader_tags")
        self.assertIn("_impl", module.__file__, module.__file__)

    def test_the_classes_this_module_defines_are_not_djangos(self):
        """The classes with the file as their ``co_filename``, and the one it imports.

        ``load_pinned`` executes the pinned file *fresh*, so ``BlockNode``,
        ``ExtendsNode`` and ``IncludeNode`` are new class objects and the
        comparison is between two real things rather than a name and itself.

        ``Node`` is deliberately **not** compared that way, and the reason is the
        interposition working as intended: the pinned file's
        ``from django.template.base import Node`` resolves through the shim to
        *our* ``base``, so the pinned module's ``Node`` is ours too. Asserting
        ``is not`` there would fail on a correct tree. What is asserted instead
        is the identity that matters — the two modules name one class — because
        that is what makes ``isinstance`` against either true for every node the
        tree holds.
        """
        import importlib
        import inspect

        from django.template import base, loader_tags

        from tests.pinned_module import load_pinned

        pinned = load_pinned("loader_tags.py")
        for name in ("BlockNode", "ExtendsNode", "IncludeNode"):
            with self.subTest(name=name):
                ours = getattr(loader_tags, name)
                self.assertIsNot(ours, getattr(pinned, name))
                self.assertIn("_impl", inspect.getfile(ours))

        self.assertIs(loader_tags.Node, base.Node)

    def test_the_include_node_djangos_cache_row_renders_is_ours(self):
        """The live half of the re-read: the *object the row renders* is ours.

        The mutation above is a measurement and not a row — no test can edit its
        own subject's source and stay honest. This is the durable assertion it
        stands on: parse a template containing ``{% include %}`` through our
        engine and check that the node the resulting tree holds is our class,
        by ``type`` and by the file that defines it. That is the object Django's
        row hands to ``render_context`` and keys its cache on.
        """
        import inspect

        from django.template import loader_tags
        from django_rust_templates.render import parse_template

        engine = Engine(
            loaders=[
                ("django.template.loaders.locmem.Loader", {"n": "{% include \"m\" %}"})
            ]
        )
        template = parse_template('{% include "n" %}', engine)
        includes = template.nodelist.get_nodes_by_type(loader_tags.IncludeNode)
        self.assertEqual(len(includes), 1)
        self.assertIs(type(includes[0]), loader_tags.IncludeNode)
        self.assertIn("_impl", inspect.getfile(type(includes[0])))


class ShadowingTests(unittest.TestCase):
    """Which ``_impl`` files wear a real module's name, and that each was meant.

    ``_shim.py``'s docstring states the rule — a file under ``_impl`` may take a
    real module's name only when it implements that module — and the rule as
    first written forbade the mechanism itself: four shipped files broke it on
    the day it was written, because interposing a module *is* taking its name
    (``.review/m10-recon.md`` §6). :data:`_shim.SHADOWING` replaces the
    prohibition with a declaration, and these rows are what make the declaration
    binding rather than decorative.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_the_shadowing_files_are_the_declared_ones(self):
        """Set equality, and both directions are the point.

        A file on disk that is not declared is the ``render.py``-at-``engine.py``
        accident: it shadows a real module for Django's internal imports as much
        as for ours, and nothing else in the tree notices — the ``ImportError``
        that eventually fires names Django, three files from the cause. A
        declared key with no file is the opposite error, a claim about nothing
        that the next reader would take for a measurement.
        """
        self.assertEqual(_shim.shadowing_files(), sorted(_shim.SHADOWING))

    def test_each_kind_is_the_transcription_guards_own_answer(self):
        """``kind`` is compared, not trusted — and each kind has its own warrant.

        It is the one column here that could rot into a second, disagreeing
        account of which files are transcriptions — the account
        ``scripts/check_transcriptions.py`` already owns and already checks
        against each file's marker. So the two are compared directly, and the
        comparison now has two arms because the two kinds carry different
        obligations:

        * ``transcribed`` must be a name that guard declares. The **diff against
          the pin** is the warrant, and it is the only instrument that falsifies
          a transcription (M9's finding 3: a verbatim copy and a wrongly
          transcribed file both keep all 1547 tests green).
        * ``fresh`` must be a name ``tests/test_transcription_equivalence.py`` states
          an equivalence for. A fresh file has no diff of its own, so nothing in
          the transcription guard can see it, and the suite cannot see it
          either — for the same reason. Without this arm ``fresh`` is the free
          option: changing one word in the table above would move a file out of
          every guard in the tree with everything green.

        The class is required to have rows rather than merely to be named,
        because an empty test case is how a warrant becomes a gesture.
        """
        import importlib

        import scripts.check_transcriptions as guard

        from tests import test_transcription_equivalence as equivalence

        for name, kind in sorted(_shim.SHADOWING.items()):
            with self.subTest(name=name):
                self.assertIn(kind, ("transcribed", "fresh"))
                if kind == "transcribed":
                    self.assertIn(
                        name,
                        guard.DECLARED,
                        f"{name} is declared 'transcribed' in SHADOWING but "
                        "check_transcriptions.py does not declare it, so "
                        "nothing reads its diff against the pin",
                    )
                    continue
                self.assertIn(
                    name,
                    equivalence.EQUIVALENCE,
                    f"{name} is declared 'fresh' in SHADOWING but states no "
                    "equivalence in tests/test_transcription_equivalence.py, so "
                    "nothing in this tree can falsify it — transcribe it "
                    "instead, or state the equivalence as rows",
                )
                module_name, class_name = equivalence.EQUIVALENCE[name]
                case = getattr(importlib.import_module(module_name), class_name)
                rows = [a for a in dir(case) if a.startswith("test_")]
                self.assertGreater(
                    len(rows), 0, f"{module_name}.{class_name} has no rows"
                )


class ResolverDelegationTests(unittest.TestCase):
    """Task 5: ``Variable._resolve_lookup`` is Rust's, handler included.

    The rows below are direct-entry evidence, which is what this module is for:
    the accepted suite cannot see whether the resolver delegates. Measured in
    `.review/m9-recon.md` finding 3, a transcription that delegates nothing is
    green everywhere except the meter — and the third row here is the one that
    would have caught the version of this task that shipped the handler in Rust
    *without* the log.

    **A deviation from the plan's Step 5 and Step 6, recorded because it changes
    what these rows are evidence for.** The plan put Django's outer `except`
    block in the shell, with `LookupOutcome::Escaped`'s bit crossing to Python
    so the log could be emitted there. It cannot be: for the shell to be inside
    an `except` block, Rust must *raise*, and the bit has no transport that does
    not either mutate the caller's exception or add an out-parameter whose only
    purpose is to move one string one frame. So the handler — the log, the
    `silent_variable_failure` branch, and the re-raise — is
    `crates/template-py/src/lib.rs::outcome_to_py`, and what the rows below pin
    is the *record*, which is the same either way.
    """

    @classmethod
    def setUpClass(cls):
        # The suite's settings helper rather than a local `configure` — the
        # alphabet lesson `tests/test_base_parity.py` records. Guarded on
        # `settings.configured` inside, so this cannot change the answer for
        # another module; it only supplies one if none exists yet.
        from scripts.filter_probe import ensure_settings

        ensure_settings()
        _shim.install()

    def _capture(self):
        """A ``Capture`` handler on ``django.template``, plus its records.

        The level is set explicitly because the logger inherits ``WARNING`` from
        the root: ``assertLogs`` does this itself, and a hand-rolled capture that
        does not would collect nothing and read as "no log was emitted", which
        is the wrong conclusion in the most expensive direction.
        """
        import logging

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("django.template")
        handler = Capture()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger, handler, records

    def test_variable_resolution_reaches_rust(self):
        """A render that resolves a variable calls `_native.resolve`.

        The meter and the file check above say `base` is ours; neither says the
        *lookup* is. A transcription that kept upstream's loop would pass both,
        which is finding 3's shape applied to this method.
        """
        from django.template import Context, Engine

        from django_rust_templates import _native

        calls = []
        original = _native.resolve

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.resolve = counting
        try:
            Engine().from_string("{{ a.b }}").render(Context({"a": {"b": 1}}))
        finally:
            _native.resolve = original

        self.assertEqual(calls, ["a.b"], calls)

    def test_the_failing_bit_is_passed_to_the_logger(self):
        """The bit `{{ author.name }}` fails on, not the bit it ends on.

        This is the whole reason `LookupOutcome::Escaped` grew a payload, and
        the path makes it a real distinction: `{{ a.b.c }}` over a context whose
        `b` raises logs `b`, while the *path's* last bit is `c`. The plan's
        recon named its two `test_logging` failures as one defect; the silent
        half is the third row below.
        """
        from django.template import Context, Engine

        logger, handler, records = self._capture()
        try:
            Engine().from_string("{{ author.name }}").render(Context({}))
        finally:
            logger.removeHandler(handler)

        messages = [record.getMessage() for record in records]
        self.assertIn(
            "Exception while resolving variable 'author' in template 'unknown'.",
            messages,
            messages,
        )

    def test_the_log_record_carries_the_exception_instance(self):
        """`exc_info=True` cannot work here, and the obvious assertion hides it.

        Upstream passes `exc_info=True`, which resolves `sys.exc_info()` **at
        call time** and is `(None, None, None)` outside an active `except`. The
        M8 stash is exactly "held, never active", so a handler that used the
        verbatim form would record a truthy tuple of `None`s:
        `assertIsNotNone(record.exc_info)` passes and `str(record.exc_info[1])`
        is `'None'`.

        Both halves are executed rather than argued — the naive form first, on
        this logger, outside any `except`, so the `(None, None, None)` the row
        warns about is produced here and asserted on, and then the render, whose
        record must carry the instance.
        """
        import logging

        from django.template import Context, Engine

        logger, handler, records = self._capture()
        try:
            logger.debug("naive form", exc_info=True)
            naive = records[-1]
            self.assertEqual(
                naive.exc_info,
                (None, None, None),
                "the premise of this row: `exc_info=True` outside an `except` "
                "records a tuple of Nones, which is truthy",
            )
            self.assertIsNotNone(naive.exc_info, "and this is the assertion that "
                                 "would have passed on it")

            Engine().from_string("{{ author.name }}").render(Context({}))
        finally:
            logger.removeHandler(handler)

        ours = [record for record in records if record.exc_info is not None]
        ours = [record for record in ours if record.getMessage() != "naive form"]
        self.assertEqual(len(ours), 1, [r.getMessage() for r in records])
        self.assertIsNotNone(ours[0].exc_info[1], "exc_info held no exception")
        self.assertIn("Failed lookup for key [author]", str(ours[0].exc_info[1]))

class FilterRoutingTests(unittest.TestCase):
    """M11 Task 2: a built-in filter's *body* is reached through Rust.

    This is the milestone's load-bearing claim, and the reason it needs a row of
    its own is that **nothing else in the tree can see it**. `filter_tests` is
    454 rows and all of them are green today — against Django's filter bodies,
    because `defaultfilters` is not interposed and the render path never asked
    Rust for a filter (`.review/m11-recon.md` §1). The suite going green at M0 is
    the same finding from the other end.

    So the row is a **counter**, the shape `test_variable_resolution_reaches_rust`
    uses and `scripts/differential.py` uses for the whole `_native` surface:
    render a template that applies a built-in filter and assert `_native.filter`
    ran, with the name it was handed. The premise is asserted separately below,
    because a row like this passes for a *true* reason and a false one — if the
    module were still Django's, the counter would read zero and the row would
    fail, which is right; but if it passed while `defaultfilters` was Django's,
    that would be M9's finding 3 and the row would be measuring nothing.
    """

    def setUp(self):
        _shim.install()

    def test_a_built_in_filter_reaches_rust(self):
        from django.template import Context, Engine

        from django_rust_templates import _native

        calls = []
        original = _native.filter

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.filter = counting
        try:
            rendered = Engine().from_string("{{ x|lower }}").render(
                Context({"x": "ABC"})
            )
        finally:
            _native.filter = original

        self.assertEqual(rendered, "abc")
        self.assertEqual(calls, ["lower"], calls)

    def test_the_parser_finds_our_defaultfilters(self):
        """The premise, on its own, so a green counter cannot be the whole story.

        `Parser.find_filter(name)` is the handle the parse keeps
        (`python/django_rust_templates/_impl/parser.py:568`) and the object the routing compares against, so
        this asserts the *identity* the routing will test — ours, from our file —
        rather than a name. Two claims, because interposing the file is not the
        same as the registry being the one the parser reads: `register` could be
        ours while the parser's table still held Django's copies, and that is the
        `django_own_tags()` defect one level over (`host.py`'s docstring records
        its first version snapshotting `parser.tags` *after* `Parser.__init__`
        merged the builtins).
        """
        import importlib
        import inspect

        from django_rust_templates.render import parser_for

        defaultfilters = importlib.import_module("django.template.defaultfilters")
        self.assertIn("_impl", defaultfilters.__file__)

        func = parser_for("").find_filter("lower")
        self.assertIs(func, defaultfilters.register.filters["lower"])
        self.assertIn("_impl", inspect.getfile(func))

    def test_every_library_filter_reaches_rust(self):
        """M12 Task 7: the nine `django.templatetags` filters route to Rust.

        The same **counter** the built-in row above uses, widened to the library
        filters — and it is the row the milestone needs rather than a repetition,
        because these nine are the ones whose *routing* could be missing while
        everything looked right. A library filter's handle is only in
        `django_own_filters()` once the merge carries the five registries, and the
        failure mode of a missing handle is not an error: it is the Python loop
        running the **transcribed** body, which produces the right answer. Green,
        and not Rust's. So each case asserts `_native.filter` ran, with the name
        it was handed.

        The render path is the real one — `{% load %}` merges the library into the
        parser (as a template would), and the token is the template's. `Engine`
        needs the library *map* because a bare `Engine()` has
        `template_libraries == {}` (measured), which is also why the parity
        corpus carries a `libraries` key; the map here is the three modules this
        task ports, since `cache`/`static` register no filters.
        """
        import datetime

        from scripts.filter_probe import ensure_settings

        from django.template import Context, Engine

        from django_rust_templates import _native

        # `language_name_translated` reaches the host's `gettext`, which needs
        # settings and a populated app registry; guarded, so this supplies them
        # only if no earlier module has.
        ensure_settings()

        engine = Engine(
            libraries={
                "i18n": "django.templatetags.i18n",
                "l10n": "django.templatetags.l10n",
                "tz": "django.templatetags.tz",
            }
        )
        naive = datetime.datetime(2026, 10, 8, 22, 30)
        cases = [
            ("i18n", "language_name", "hu", None),
            ("i18n", "language_name_local", "hu", None),
            ("i18n", "language_bidi", "hu", None),
            ("i18n", "language_name_translated", "hu", None),
            ("l10n", "localize", 1234.5, None),
            ("l10n", "unlocalize", 1234.5, None),
            ("tz", "localtime", naive, None),
            ("tz", "utc", naive, None),
            ("tz", "timezone", naive, "Europe/Paris"),
        ]

        original = _native.filter
        for library, name, value, arg in cases:
            with self.subTest(filter=name):
                calls = []

                def counting(*args, **kwargs):
                    calls.append(args[0])
                    return original(*args, **kwargs)

                token = f"x|{name}" if arg is None else f'x|{name}:"{arg}"'
                source = "{% load " + library + " %}{{ " + token + " }}"
                _native.filter = counting
                try:
                    engine.from_string(source).render(Context({"x": value}))
                finally:
                    _native.filter = original
                self.assertEqual(
                    calls,
                    [name],
                    f"{{{{ x|{name} }}}} did not reach Rust: a handle missing "
                    f"from `django_own_filters()` falls through to the Python "
                    f"loop, which answers correctly and so fails only here",
                )

    def test_a_loaded_library_that_rebinds_a_builtin_filter_runs_its_own_body(self):
        """Review Focus 1: the handler is the **handle**, so a rebinding wins.

        The decision this task makes is identity of the callable, and the input
        that discriminates it from a decision by *name* is a `{% load %}`ed
        library registering `lower`. A name-keyed arm reads `"lower"`, finds it
        in Rust's registry, and runs Rust's body — silently, with the library's
        function never called and the output still looking like a lowercased
        string. So the row asserts three things, and the third is the one no
        output comparison can make: the library's callable ran, the value it
        received was the resolved one, and `_native.filter` was **not** reached.

        The library is injected into `engine.template_libraries` rather than
        imported from a file (`tests/test_filter_rebinding.py`'s idiom, and for
        its reason): `find_library` reads `parser.libraries`, which
        `Template.compile_nodelist` fills from the engine's map, so no module on
        disk and no `sys.path` entry is needed to make a loadable name.
        """
        from django.template import Context, Engine, Library

        from django_rust_templates import _native

        library = Library()
        ran = []

        @library.filter("lower")
        def shadowing_lower(value):
            ran.append(value)
            return f"shadow({value})"

        engine = Engine()
        engine.template_libraries["m11_shadow"] = library

        calls = []
        original = _native.filter

        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)

        _native.filter = counting
        try:
            rendered = engine.from_string(
                "{% load m11_shadow %}{{ x|lower }}"
            ).render(Context({"x": "ABC"}))
        finally:
            _native.filter = original

        self.assertEqual(ran, ["ABC"], "the library's callable did not run")
        self.assertEqual(rendered, "shadow(ABC)")
        self.assertEqual(
            calls,
            [],
            "Rust's arm ran for a name the library had rebound; the routing "
            "decided by name rather than by the handle the parse bound",
        )

    def test_the_stringfilter_flag_travels_and_coerces_once(self):
        """Review Focus 4: the coercion happens inside, on our side of the FFI.

        `@stringfilter` is a *Python* decorator, so the coercion `{{ x|lower }}`
        performs for an `int` or an arbitrary object is one of the four steps the
        arm takes over — and the flag that says which filters get it is a Rust
        table (`FilterEntry.stringfilter`) the parse never sees. So the input that
        catches a lost flag is a filter reached through a rendered template with
        a non-`str`. `lower` cannot lowercase a `Counted` in either language, so
        `'mixed'` out of the render is Rust having been handed the `str`; the
        value is `str`-convertible rather than hostile on purpose, since a body
        handed the raw object would fail and the row would then be about the
        error rather than about the coercion.

        **The count is here because the plan asked for it, and it is recorded as
        the weak meter it measured out to be.** A counting `__str__` is the
        natural way to ask "once or twice?", and the answer is `1` in *all three*
        worlds: the correct arm, an arm that also fell through to the loop, and
        no arm at all. Measured, by deleting the arm's `continue` so it wraps
        instead of replacing — still `1`. The doubling is invisible to it by
        construction: the second `str()` is applied to the first one's *result*,
        which is already a `str`, so the counting `__str__` is not called again.
        The docstring used to claim this count caught the wrapper; the row that
        actually does is the `force_escape` one below.

        **`theirs` is the pinned body, not a second `Engine()`.** `Engine()`
        inside this suite resolves to our own implementation, so the previous
        version's `ours == theirs` compared our engine to itself and its
        `"Django coerces once"` label was attached to our answer. The stock
        number is measured where stock code can be reached at all — the pinned
        `lower` called directly.
        """
        from django.template import Context, Engine

        from tests.pinned_module import load_pinned

        class Counted:
            def __init__(self, text):
                self.text = text
                self.calls = 0

            def __str__(self):
                self.calls += 1
                return self.text

        value = Counted("MiXeD")
        rendered = Engine().from_string("{{ x|lower }}").render(Context({"x": value}))

        self.assertEqual(rendered, "mixed")
        self.assertEqual(
            value.calls,
            1,
            "coerced more than once at this boundary, which the `lower` body "
            "cannot do through Rust's own `str()`",
        )

        pinned_value = Counted("MiXeD")
        self.assertEqual(
            load_pinned("defaultfilters.py").register.filters["lower"](pinned_value),
            "mixed",
        )
        self.assertEqual(pinned_value.calls, 1, "stock Django coerces once")

    def test_the_arm_replaces_the_loop_rather_than_wrapping_it(self):
        """Review Focus 6: `force_escape`, the input that can see a wrapper.

        `_native.filter` performs upstream's four steps inside itself, so an arm
        that *also* fell through to the loop would run them a second time. Three
        of the four are invisible: `template_localtime` is a no-op for a `str`,
        `needs_autoescape` is passed the same flag twice, and `mark_safe` is
        idempotent on a `SafeString`. The fourth is `@stringfilter`'s `str()`
        coercion, and **it is not visible either** — which is where this row's
        first version went wrong, and the plan with it.

        The plan named `escape` and reasoned from the bare functions:
        `escape("&")` is `&amp;` and `escape(escape("&"))` is `&amp;amp;`.
        That is true of `django.utils.html.escape` and **false of the filter
        registered as `escape`**, whose body is `conditional_escape`
        (`defaultfilters.py:457-461`), and `conditional_escape` returns
        `text.__html__()` untouched for a `SafeData` input
        (`utils/html.py:115-125`). So the second application never reaches
        `escape()` at all, and the flag is not even lost on the way: measured
        against `.django` @ 6.0.9, `str(SafeString("&amp;"))` returns the *same
        object* (`type` is `SafeString`, `isinstance(..., SafeData)` is `True`).
        Measured through the render path, `'&amp;'` for both `{{ x|escape }}`
        and `{{ x|escape|escape }}` — and `'&amp;'` for both under a doubled
        wrapper too, so `escape` sees nothing.

        `force_escape` is the filter whose body is that unconditional
        `escape(str(value))` — `@register.filter(is_safe=True) @stringfilter`,
        `defaultfilters.py:474-476` — and it is non-idempotent in the render
        path: `'&amp;'` once, `'&amp;amp;'` twice, measured on the pinned body
        directly and through the engine.

        So the row asserts the literal for the single application, which is what
        a doubled wrapper changes, and the doubled *filter expression* as the
        premise that this filter is non-idempotent — without that premise the
        first assertion proves nothing about the wrapper, and it is exactly the
        premise `escape` would have passed while seeing nothing.

        **Verified by mutation rather than by argument**: with the arm's
        `continue` deleted, so `_native.filter` runs and the loop body runs on
        top of it, the first assertion fails as
        `AssertionError: '&amp;amp;' != '&amp;'` — and it is one of exactly two
        rows in `FilterRoutingTests` that redden. The other three stay green,
        which is the measurement behind the claim that these inputs
        discriminate and the rest do not.
        """
        from django.template import Context, Engine

        engine = Engine()
        self.assertEqual(
            engine.from_string("{{ x|force_escape }}").render(Context({"x": "&"})),
            "&amp;",
            "the escaped form of `&`; `&amp;amp;` here is the arm running the "
            "body a second time",
        )
        self.assertEqual(
            engine.from_string("{{ x|force_escape|force_escape }}").render(
                Context({"x": "&"})
            ),
            "&amp;amp;",
            "the premise: this filter is non-idempotent, so a doubled wrapper "
            "is visible in the text rather than silent",
        )

    def test_a_dict_context_is_read_only_where_upstream_reads_it(self):
        """The arm's two scalars are computed as lazily as upstream reads them.

        `FilterExpression.resolve` is called with a plain `dict` by Django's own
        suite — `.django/tests/template_tests/test_parser.py:207`, against
        `0|default:{num}` — and that is legal because upstream's loop touches
        `context` only inside its two `getattr`-guarded branches
        (`base.py:810-815`). The arm has to compute the same two FFI scalars
        before the call, so this row is what says it computed them under the same
        guards. Measured before the fix: `AttributeError: 'dict' object has no
        attribute 'autoescape'` for **all five** expressions below, while every
        other row of the 1547 stayed green — the acceptance suite is what found
        it, and this row is so the unit suite finds it next time.

        **Which attribute the error names is part of the claim**, and what
        produces it is upstream's *conditionality* rather than its statement
        order: each attribute is read only under its own flag, and the two flag
        sets are disjoint — measured at 6.0.9, two filters set
        `expects_localtime` (`date`, `time`), seven set `needs_autoescape`, and
        the intersection is empty, so 48 of the 57 read neither. Order alone
        would not be the reason: were one filter to set both, the first read
        would be the one that raised. Within that, `expects_localtime` is tested
        before `needs_autoescape`, so `date` must blame `use_tz` and `join` —
        which has `needs_autoescape` only — must blame `autoescape`. Computed as
        arguments to the FFI call, where `autoescape` comes first positionally,
        `date` would blame the wrong attribute and a row asserting only "an
        AttributeError was raised" would not notice.

        Two registries, one per engine, so nothing of ours is the comparator's
        input: the pinned `defaultfilters` for the pinned parser and ours for
        ours. The five answers are asserted one by one rather than in aggregate,
        since the two error cases carry different text.
        """
        import importlib

        from django.template.base import FilterExpression, Parser

        from tests.pinned_module import load_pinned

        ours_registry = importlib.import_module("django.template.defaultfilters").register
        pinned = load_pinned("base.py")
        pinned_registry = load_pinned("defaultfilters.py").register

        ours_parser = Parser("", builtins=[ours_registry])
        theirs_parser = pinned.Parser("", builtins=[pinned_registry])

        for expression, expected in [
            ("0|default:1", "1"),
            ("0|lower", "'0'"),
            ("0|force_escape", "'0'"),
        ]:
            self.assertEqual(
                repr(FilterExpression(expression, ours_parser).resolve({})),
                expected,
                expression,
            )
            self.assertEqual(
                repr(pinned.FilterExpression(expression, theirs_parser).resolve({})),
                expected,
                expression,
            )

        for expression, blame in [
            ("0|date:'Y'", "use_tz"),
            ("0|join:'-'", "autoescape"),
        ]:
            with self.assertRaises(AttributeError) as ours_error:
                FilterExpression(expression, ours_parser).resolve({})
            with self.assertRaises(AttributeError) as theirs_error:
                pinned.FilterExpression(expression, theirs_parser).resolve({})
            self.assertEqual(
                str(ours_error.exception), str(theirs_error.exception), expression
            )
            self.assertIn(blame, str(ours_error.exception), expression)

    def test_the_autoescape_flag_travels_and_the_needs_autoescape_arm_is_not_doubled(self):
        """Review Focus 6's second input: `join`, the filter that reads the flag.

        `_native.filter`'s fourth argument is `context.autoescape`, and until
        this row nothing pinned it. `join` is one of the six `needs_autoescape`
        filters (`defaultfilters.py:611-621` in the pin), so its body escapes or
        does not escape *because* of that flag — and the flag is not a constant
        a Rust side could guess, since it is the `Context`'s. Measured on the
        pinned body with `["a<b", "c"]` and the separator `-`: `'a&lt;b-c'` for
        `autoescape=True`, `'a<b-c'` for `False`. A Rust arm that hard-coded
        either answer passes one of the two assertions and fails the other, so
        the pair is what is asserted rather than either literal alone.

        **The same filter is the wrapper's second discriminator, and on a
        different step than `force_escape`.** `needs_autoescape` is the step
        that calls `func(obj, autoescape=..., *args)`; `force_escape` is not a
        `needs_autoescape` filter at all, so it says nothing about this arm.
        `join` is not idempotent under a second call — it joins the *characters*
        of the string it just produced. Measured with `["a", "b"]`: `'a-b'`
        correctly, `'a---b'` under a doubled wrapper (the arm's `continue`
        deleted, the same mutation the row above records), and measured on the
        pinned body as `'a---b'` too, so the doubled value is the body's own
        answer and not an artifact of ours.
        """
        from django.template import Context, Engine

        engine = Engine()
        self.assertEqual(
            engine.from_string("{{ x|join:'-' }}").render(Context({"x": ["a", "b"]})),
            "a-b",
            "`a---b` here is the arm running the body a second time",
        )

        # The flag itself, on a value where it changes the answer.
        escaped = ["a<b", "c"]
        self.assertEqual(
            engine.from_string("{{ x|join:'-' }}").render(Context({"x": escaped})),
            "a&lt;b-c",
            "the body did not escape with autoescape on",
        )
        self.assertEqual(
            engine.from_string(
                "{% autoescape off %}{{ x|join:'-' }}{% endautoescape %}"
            ).render(Context({"x": escaped})),
            "a<b-c",
            "the flag did not travel: the body escaped with autoescape off",
        )

    def test_the_builtin_filter_map_tracks_the_registry_it_was_built_from(self):
        """The cache's key is the registry *object*, not its own emptiness.

        `host.django_own_filters()` inverts `defaultfilters.register.filters` and
        caches the result, because the arm asks it for *every* filter of every
        rendered variable. Cached on `None` alone, the map outlives its source:
        re-executing `defaultfilters` — a reload, or the scratch-universe
        instrumentation M11's measurement scripts build — installs 57 fresh
        handles, none of which is in the stale map, so the arm misses on all 57
        and falls through to the Python loop. **Every test stays green**, because
        the Python loop is upstream's own body and produces the right answer; the
        only thing lost is the milestone, silently. It is the same shape as the
        `django_own_tags()` defect one level over, which is why the fix and this
        row travel with the routing that depends on it.

        The row reproduces the hazard rather than describing it: a registry
        object the map has never seen, holding a *sentinel* handle under a
        built-in name. The map must name the sentinel — and the stale map the old
        cache would have returned must not, which is asserted too, so the row
        cannot pass by asserting something both maps satisfy.
        """
        import django.template.defaultfilters as defaultfilters

        from django.template import Context, Engine

        from django_rust_templates import host

        # Warm the cache against the registry actually in force.
        Engine().from_string("{{ x|lower }}").render(Context({"x": "A"}))

        def sentinel(value):  # pragma: no cover - never called, only named
            return value

        sentinel.__name__ = "lower"

        original = defaultfilters.register.filters
        replacement = dict(original)
        replacement["lower"] = sentinel

        stale = {func: name for name, func in original.items()}
        self.assertNotIn(
            sentinel,
            stale,
            "the control: the map built from the old registry cannot name it",
        )

        defaultfilters.register.filters = replacement
        try:
            self.assertIs(
                host.django_own_filters().get(sentinel),
                "lower",
                "the map did not track its source, so a re-executed "
                "`defaultfilters` would fall through to Python for all 57 filters",
            )
        finally:
            defaultfilters.register.filters = original

        # And restoring the object restores the map, so the row does not leave a
        # poisoned cache behind for the rest of the session.
        self.assertIs(host.django_own_filters().get(original["lower"]), "lower")

    def test_the_filter_map_covers_every_registered_library_filter(self):
        """M12 Task 7: nine names, and the *same object* under each.

        The filter-side twin of `test_the_predicate_covers_every_registered_library_tag`,
        and identity is the claim rather than presence for the same reason:
        `resolve`'s arm asks `django_own_filters().get(handle)`, so a map carrying
        a *different* callable under `language_name` names nothing the parse would
        ever bind, and the Python loop runs while every row stays green.

        The nine are taken from the registries themselves, so a pin that adds a
        library filter moves this number and the row says by how much. Measured on
        `.django` @ 6.0.9: cache 0, i18n 4, l10n 2, static 0, tz 3.
        """
        import importlib

        from django_rust_templates import host

        registries = {
            name: importlib.import_module(f"django.templatetags.{name}").register.filters
            for name in ("cache", "i18n", "l10n", "static", "tz")
        }
        own = host.django_own_filters()
        registered = set().union(*registries.values())
        self.assertEqual(
            len(registered),
            9,
            f"the five libraries register {len(registered)} filters, not 9: "
            f"{sorted(registered)}",
        )
        for module, filters in sorted(registries.items()):
            for name, func in sorted(filters.items()):
                with self.subTest(module=module, filter=name):
                    self.assertEqual(
                        own.get(func),
                        name,
                        f"{module}.{name} is not the handle the filter map "
                        f"carries, so the routing cannot clear it and the "
                        f"transcribed Python body runs instead of Rust's",
                    )

    def test_the_six_filter_registries_are_disjoint_and_the_inversion_injective(
        self,
    ):
        """The two measurements the merge and the inversion rest on, held as rows.

        Both are claims the docstrings make and nothing re-read:
        `django_own_filters()` merges **libraries first, `defaultfilters` last**
        because a name shared between them would otherwise let a library rebind a
        built-in; and it inverts the merge **by handle**, which is faithful only
        while no two names share one callable. Disjoint and injective at 6.0.9 —
        the assertion says so, and a pin that broke either would move this row
        rather than the answer.
        """
        import importlib
        from collections import Counter

        from django.template import defaultfilters

        tables = [
            importlib.import_module(f"django.templatetags.{name}").register.filters
            for name in ("cache", "i18n", "l10n", "static", "tz")
        ]
        tables.append(defaultfilters.register.filters)

        merged = {}
        for table in tables:
            merged.update(table)
        self.assertEqual(len(merged), 66, f"the merge is {len(merged)} names, not 66")

        shared = [
            name for name, count in Counter(n for t in tables for n in t).items() if count > 1
        ]
        self.assertEqual(
            sorted(shared),
            [],
            "a name is registered by two of the six tables, so the merge order "
            "in `django_own_filters()` decides which callable the routing "
            "carries for it",
        )
        collisions = [n for n, c in Counter(merged.values()).items() if c > 1]
        self.assertEqual(
            collisions,
            [],
            "two names share one handle, so the handle-keyed inversion drops one "
            "of them and Rust runs the wrong body for it — silently",
        )

    def test_a_library_rebinding_a_builtin_filter_name_cannot_take_it_from_the_map(
        self,
    ):
        """The merge order, staged with the collision it guards against.

        A sentinel goes into `tz.register.filters` under `lower` — a name
        `defaultfilters` also registers — and under `timezone`, a name no other
        merged table has. The second placement is the control: it shows the
        library table really is merged, so the first assertion cannot be passing
        because the patch never reached the merge.
        """
        import importlib

        from django.template import defaultfilters

        from django_rust_templates import host

        def sentinel(value):  # pragma: no cover - named, never called
            return value

        sentinel.__name__ = "lower"

        tz = importlib.import_module("django.templatetags.tz")
        before = tz.register.filters
        tz.register.filters = {**before, "lower": sentinel, "timezone": sentinel}
        try:
            table = host.django_own_filters()
            self.assertEqual(
                table.get(sentinel),
                "timezone",
                "the control: the library's filter table did not reach the merge",
            )
            self.assertNotEqual(
                table.get(sentinel),
                "lower",
                "a library's `lower` won the merge, so a `{% load %}`ed rebind of "
                "`lower` now compares equal to it and Rust's body runs over the "
                "library's function",
            )
            self.assertEqual(
                table.get(defaultfilters.register.filters["lower"]), "lower"
            )
        finally:
            tz.register.filters = before

        self.assertEqual(
            host.django_own_filters().get(defaultfilters.register.filters["lower"]),
            "lower",
            "restoring the registry did not restore the map",
        )

    def test_the_filter_map_tracks_a_library_registry_it_was_built_from(self):
        """The cache's key is *every* source object, the five libraries included.

        `test_the_builtin_filter_map_tracks_the_registry_it_was_built_from` covers
        the `defaultfilters` source. This is the half M12 Task 7 added: the map is
        now built from six registries, and a cache keyed on `defaultfilters` alone
        (or on `None`) would serve a stale map after a *library* was re-executed —
        the scratch-universe instrumentation M11's measurement scripts build does
        exactly that — leaving every one of the library's filters to fall through
        to Python with the suite green.
        """
        import importlib

        from django_rust_templates import host

        def sentinel(value):  # pragma: no cover - named, never called
            return value

        sentinel.__name__ = "m12_filter_sentinel"

        tz = importlib.import_module("django.templatetags.tz")
        before = tz.register.filters
        self.assertNotIn(sentinel, host.django_own_filters())
        tz.register.filters = {**before, "m12_filter_sentinel": sentinel}
        try:
            self.assertEqual(
                host.django_own_filters().get(sentinel),
                "m12_filter_sentinel",
                "the map did not track a replaced library registry, so a "
                "re-executed library would be answered for by a stale map",
            )
        finally:
            tz.register.filters = before
        self.assertNotIn(
            sentinel,
            host.django_own_filters(),
            "the map kept the sentinel after its registry was restored",
        )


class TagPredicateCoverageTests(unittest.TestCase):
    """M12 Task 0b: Rust-owned must be a subset of the tag predicate.

    `has_tag` answers a Rust-owned name with
    `func is not self._django_tags.get(command)`
    (`python/django_rust_templates/host.py:422`), so the merge
    `django_own_tags()` returns is not decoration — it is the *other side of the
    comparison*. A name in `_rust_owned` that the merge does not carry has
    nothing to be compared against, `func is not None` is True, and the escape
    resolves the wrong way: the host compiles a tag Rust owns, silently, with
    the whole 1547 green.

    **This row exists because the sentence that explains the predicate denied
    it.** `django_own_tags()`'s docstring asserted *"the 25 Rust-owned names are
    exactly the union of these two tables"*, and both halves were false at
    6.0.9: the count is 23, and the relation is containment. That is not a
    cosmetic correction — under a claimed *equality*, widening the merge looks
    self-maintaining, so a reader concludes the predicate keeps itself honest
    and the inversion M12 exists to repair reads as impossible. The docstring is
    corrected in the same commit as this row.

    Nothing in the tree related the two sets before this. The claim lived only
    in the docstring, which nothing re-reads.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()
        from django_rust_templates import host as host_module
        from django_rust_templates.render import parser_for

        cls.host = host_module.ShellHost(parser_for(""))
        cls.own = host_module.django_own_tags()

    def assert_covers(self, rust_owned, own):
        missing = sorted(rust_owned - set(own))
        self.assertFalse(
            missing,
            f"Rust-owned names the tag predicate does not carry: {missing}. "
            f"`has_tag` decides these with `func is not _django_tags.get(command)`, "
            f"so a name missing from the merge has nothing to compare against and "
            f"the escape resolves the wrong way — silently, with the suite green.",
        )

    def test_every_rust_owned_name_is_in_the_tag_predicate(self):
        """Read off a real ``ShellHost``, not recomputed from ``_native``.

        Recomputing `set(builtin_tag_names_including_stubs()) - set(...)` here
        would be a second account of the set the predicate actually uses, and a
        row that agrees with its own copy of the rule is the failure this whole
        file is about.
        """
        self.assert_covers(self.host._rust_owned, self.own)

    def test_that_row_would_catch_a_name_the_predicate_lacks(self):
        """The control the row above cannot be read without.

        A containment assertion over a set that happens to satisfy it is green
        on arrival and proves nothing until it has been shown to fail. The
        fabricated name is the point: after M12 Task 2 widened the table, no
        real name is both Rust-owned and absent from it, which is the containment
        the row above asserts. **This control used `trans` until that widening**,
        chosen as the first name M12 adds to Rust's registry — and Task 2 broke
        it, which is the widening measured from the other side: a name that was
        absent from the table in Task 0b is in it now.

        The fixture wears the `unregistered_` prefix, which
        `FabricatedNameTests` below holds to a registry check — the replacement
        above was one rename, and the convention is what makes the next one
        unnecessary.
        """
        with self.assertRaises(AssertionError):
            self.assert_covers(self.host._rust_owned | {"unregistered_tag"}, self.own)


class FabricatedNameTests(unittest.TestCase):
    """A negative fixture that names a real tag or filter **expires**.

    Any row asserting "the registry lacks this name", "this is not a library
    tag" or "this tag is unloaded" is a negative only until some milestone ports
    that name, after which the fixture flips to the positive case and keeps
    whatever colour it had. It does not redden, so nothing announces it. This
    file has paid for that twice already: `TagPredicateCoverageTests`' control
    named `trans` and Task 2 broke it, and the plan for Task 4 flagged the same
    risk for the three `tz` names before they landed.

    The repair is a rename, not a restructure: a fabricated name the tree cannot
    overtake, spelled `unregistered_`. The prefix is the point — the property
    the name must have is that no registry holds it, so the name states its own
    invariant at the call site instead of leaving it to a comment.

    **The registry row below checks the converse of that property.** It makes
    the prefix trustworthy — an `unregistered_`-prefixed name really is in no
    registry — and it cannot show that every negative fixture wears the prefix.
    A real name used as a negative (`trans` here, `nope` in `parser/mod.rs`,
    `m12_not_a_tag` before this commit) is not identifiable from the source
    without reading the fixture's intent, and no mechanical check can read
    intent. So the remaining obligation is a reviewer's, and this row exists to
    make the prefix worth writing rather than to close the class.
    """

    @classmethod
    def setUpClass(cls):
        import importlib

        _shim.install()
        from django_rust_templates import host as host_module

        cls.host_module = host_module
        cls.libraries = {
            name: importlib.import_module(name).register.filters
            for name in host_module._LIBRARY_TAG_MODULES
        }

    def setUp(self):
        _shim.install()

    def registries(self):
        """Every registry a template's tag and filter names resolve against.

        Tags and filters both, and both sides of the boundary: Rust's three tag
        lists and its filter list, the merged predicate `has_tag` compares
        against, and Django's own filter registry — which is where a
        `{% load %}`ed library's names live until a later milestone ports them.
        Kept as a mapping rather than a list so a failure names the registry it
        came from.
        """
        from django.template import defaultfilters

        from django_rust_templates import _native

        return {
            "builtin_tag_names_including_stubs": set(
                _native.builtin_tag_names_including_stubs()
            ),
            "builtin_tag_stub_names": set(_native.builtin_tag_stub_names()),
            "library_tag_names": set(_native.library_tag_names()),
            "filter_names": set(_native.filter_names()),
            "django_own_tags": set(self.host_module.django_own_tags()),
            "defaultfilters": set(defaultfilters.register.filters),
            "loaded_library_filters": set().union(
                *(set(filters) for filters in self.libraries.values())
            ),
        }

    def test_no_registry_holds_an_unregistered_prefixed_name(self):
        for registry, names in sorted(self.registries().items()):
            with self.subTest(registry=registry):
                self.assertEqual(
                    sorted(name for name in names if name.startswith("unregistered_")),
                    [],
                    f"{registry} holds an `unregistered_`-prefixed name, so the "
                    f"prefix no longer means what a negative fixture using it "
                    f"asserts: that no registry can overtake the name",
                )

    def test_the_row_above_can_see_a_name_that_is_there(self):
        """The control, in the two ways that row can pass without meaning it.

        Empty is the first: a registry with no names satisfies "none of them is
        prefixed" trivially, so each is asserted non-empty. A filter that never
        matches is the second: the same prefix test is run over these registries
        with one staged name added, and the staged registry must be the one it
        names. Without the second half, a typo in the prefix would leave the row
        green forever.
        """
        registries = self.registries()
        # The one registry that is legitimately empty, and the file that owns
        # that fact: `tests/test_tag_inventory.py::test_no_builtin_tag_is_still_a_stub`
        # asserts `builtin_tag_stub_names()` is `set()` *with* a control of its
        # own. Asserting it non-empty here would be two rows claiming one fact in
        # opposite directions, which is a disagreement a later milestone would
        # have to find and adjudicate. It stays in `registries()`, so the row
        # above still covers it — a "no name carries this prefix" claim is
        # satisfied vacuously by an empty registry, and the emptiness itself is
        # the other file's claim to make.
        #
        # The exclusion is asserted to name something, so a future edit that
        # removed the registry from `registries()` cannot leave this skip behind
        # as a silent no-op.
        empty_by_design = {"builtin_tag_stub_names"}
        self.assertTrue(
            empty_by_design <= set(registries),
            f"`empty_by_design` names {sorted(empty_by_design - set(registries))}, "
            f"which is no longer in `registries()` — the exclusion is now dead",
        )
        for registry, names in sorted(registries.items()):
            if registry in empty_by_design:
                continue
            with self.subTest(registry=registry):
                self.assertTrue(names, f"{registry} is empty, so the row is vacuous")

        staged = {**registries, "staged": {"unregistered_tag"}}
        self.assertEqual(
            sorted(
                registry
                for registry, names in staged.items()
                if any(name.startswith("unregistered_") for name in names)
            ),
            ["staged"],
        )


class LibraryTagPredicateTests(unittest.TestCase):
    """M12 Task 2: the tag predicate carries the five libraries' tags.

    `django_own_tags()` is the *other side* of `has_tag`'s identity clause
    (`python/django_rust_templates/host.py:422`), so a library tag Rust ports
    must be in it before the port means anything. Widen Rust's registry alone
    and the clause still reads `None` against a real callable —
    `func is not None` is True — so the host keeps compiling the tag Rust just
    implemented, and **every row stays green**:
    ``.review/m12-recon.md`` §3 measured exactly that.

    What the widening buys, stated as the comparison rather than as the port:
    after `{% load i18n %}`, `parser.tags["trans"]` *is* the callable this table
    carries for `trans` (`Library.tags` is a plain dict and `Parser.add_library`
    is `self.tags.update(lib.tags)`, `base.py:669-670`), so once Rust owns the
    name the clause reads False and Rust compiles Django's own tag. A library
    that *rebinds* `trans` puts a different callable there, the clause reads
    True, and the host compiles the rebinding. Both halves are asserted below,
    with `"trans"` staged into `_rust_owned` so the row exercises the clause
    instead of the early return — the same staging `TagPredicateCoverageTests`
    performs one class up, and for the same reason.

    **The merge order is a safety property, not a preference**, and it needs a
    row because the collision it guards against does not exist yet: a library
    registering a name Django's two modules also register. `update` gives the
    last writer the name, so libraries merge *first* and `defaulttags`/
    `loader_tags` last. Were it the other way, this table's `if` would become a
    library's, a `{% load %}`ed rebind of `if` would compare equal to it, and
    Rust's `do_if` would compile over the caller's tag. The row stages that
    collision with a sentinel rather than waiting for a future pin to introduce
    it.
    """

    @classmethod
    def setUpClass(cls):
        import importlib

        _shim.install()
        from django_rust_templates import host as host_module

        cls.host_module = host_module
        cls.libraries = {
            name: importlib.import_module(name).register.tags
            for name in host_module._LIBRARY_TAG_MODULES
        }

    def setUp(self):
        _shim.install()

    def test_the_predicate_covers_every_registered_library_tag(self):
        """18 names, and the *same object* under each, counted rather than prose.

        Identity is the claim, not presence: `has_tag` asks
        `func is not _django_tags.get(command)`, and a table carrying a
        *different* callable under `trans` answers True — so the tag stays
        Django's even after Rust owns it, with every row green. Coverage alone
        would not say that.

        The union is taken from the registries themselves, not from a list here,
        so a pin that adds a tag to one of the five moves this number and the row
        says by how much. The count is the measured inventory the M12 plan
        carries: cache 1, i18n 10, l10n 1, static 3, tz 3.
        """
        own = self.host_module.django_own_tags()
        registered = set().union(*(tags for tags in self.libraries.values()))
        self.assertEqual(
            len(registered),
            18,
            f"the five libraries register {len(registered)} tags, not 18: "
            f"{sorted(registered)}",
        )
        self.assertEqual(
            sorted(registered - set(own)),
            [],
            "library tags the predicate does not carry: `has_tag` decides these "
            "with `func is not _django_tags.get(command)`, so an absent name "
            "leaves the tag to the host however Rust's registry spells it",
        )
        for module, tags in sorted(self.libraries.items()):
            for name, func in sorted(tags.items()):
                with self.subTest(module=module, tag=name):
                    self.assertIs(
                        own.get(name),
                        func,
                        f"{module}.{name} is not the callable the predicate "
                        f"carries, so the identity clause cannot clear it",
                    )

    def test_the_five_libraries_share_no_name_with_the_two_builtin_modules(self):
        """The measurement the merge-order argument rests on, held as a row.

        Disjoint at 6.0.9 — so the merge order changes no answer today, and the
        sentinel row below is the only thing standing between the order and a
        future pin that makes it matter. Written down here because a claim of
        disjointness that lives in a comment is one nothing re-reads, which is
        the correction M12 Task 0b exists for.
        """
        from django.template import defaulttags, loader_tags

        library_names = set().union(*(tags for tags in self.libraries.values()))
        base_names = set(defaulttags.register.tags) | set(loader_tags.register.tags)
        self.assertEqual(
            sorted(library_names & base_names),
            [],
            "a library now shares a name with Django's own builtins, so the "
            "merge order in `django_own_tags()` decides which callable the "
            "predicate carries for it",
        )
        self.assertEqual(len(base_names), 27, sorted(base_names))

    def test_a_library_rebinding_a_builtin_name_cannot_take_it_from_the_predicate(
        self,
    ):
        """The order, staged with the collision it guards against.

        A sentinel goes into `static.register.tags` under `if` — a name
        `defaulttags` also registers — and under `get_static_prefix`, a name no
        other merged table has. The second placement is the control: it shows
        the library table really is merged, so the first assertion cannot be
        passing because the patch never reached the merge.
        """
        import importlib

        from django.template import defaulttags

        def sentinel(parser, token):  # pragma: no cover - named, never compiled
            raise AssertionError("the sentinel is a name, not an implementation")

        sentinel.__name__ = "if"

        static = importlib.import_module("django.templatetags.static")
        before = static.register.tags
        static.register.tags = {
            **before,
            "if": sentinel,
            "get_static_prefix": sentinel,
        }
        try:
            table = self.host_module.django_own_tags()
            self.assertIs(
                table["get_static_prefix"],
                sentinel,
                "the control: the library's table did not reach the merge at all",
            )
            self.assertIsNot(
                table["if"],
                sentinel,
                "a library's `if` entered the predicate, so a `{% load %}`ed "
                "rebind of `if` now compares equal to it and Rust compiles over "
                "the caller's tag",
            )
            self.assertIs(table["if"], defaulttags.register.tags["if"])
        finally:
            static.register.tags = before

        self.assertIs(
            self.host_module.django_own_tags()["if"],
            defaulttags.register.tags["if"],
            "restoring the registry did not restore the table",
        )

    def test_the_predicate_map_tracks_the_registries_it_was_built_from(self):
        """The cache's key is the registry *object*, one table over from filters.

        `django_own_filters()` has the same row for the same reason: caching on
        `None` alone lets the table outlive a re-executed library, and the arm
        then misses on every handle it was built with while the suite stays
        green. Here the failure is quieter still — a table built from a
        library module that has since been re-executed carries callables no
        parse will ever bind, so `has_tag` answers True for tags Rust owns.
        """
        import importlib

        def sentinel(parser, token):  # pragma: no cover - named, never compiled
            raise AssertionError("the sentinel is a name, not an implementation")

        tz = importlib.import_module("django.templatetags.tz")
        before = tz.register.tags
        self.assertNotIn("m12_sentinel", self.host_module.django_own_tags())
        tz.register.tags = {**before, "m12_sentinel": sentinel}
        try:
            self.assertIs(
                self.host_module.django_own_tags()["m12_sentinel"],
                sentinel,
                "the table did not track a replaced library registry, so a "
                "re-executed library would be answered for by a stale table",
            )
        finally:
            tz.register.tags = before
        self.assertNotIn(
            "m12_sentinel",
            self.host_module.django_own_tags(),
            "the table kept the sentinel after its registry was restored",
        )

    def test_a_callers_builtins_are_not_what_the_predicate_carries(self):
        """The snapshot defect, for a builtin and a library tag alike.

        The table used to be a snapshot of `parser.tags` at `ShellHost`
        construction — which is *after* `Parser.__init__` merged the engine's
        `builtins`, so it captured the caller's callables rather than Django's.
        The predicate then answered "Rust's" for a tag the caller had just
        replaced, and Rust compiled over it.

        Both halves are asserted: identity, which is what the clause reads, and
        the render, which is what the user sees. The `for` render is the
        discriminator — `12` is the correct render of the same template without
        the caller's library, so a row that reported `shadow(for)` for both
        would not be evidence of anything.
        """
        from django.template import Library, Node, defaulttags

        class ShadowNode(Node):
            def __init__(self, label):
                self.label = label

            def render(self, context):
                return "shadow(%s)" % self.label

        def compile_shadow_for(parser, token):
            nodelist = parser.parse(("endfor",))
            parser.delete_first_token()
            return ShadowNode("for")

        def compile_shadow_trans(parser, token):
            return ShadowNode("trans")

        shadow = Library()
        shadow.tag("for", compile_shadow_for)
        shadow.tag("trans", compile_shadow_trans)

        engine = Engine()
        engine.template_builtins = [*Engine().template_builtins, shadow]

        table = self.host_module.django_own_tags()
        self.assertIsNot(table["for"], shadow.tags["for"], "the caller's `for`")
        self.assertIs(table["for"], defaulttags.register.tags["for"])
        self.assertIsNot(
            table["trans"],
            shadow.tags["trans"],
            "the caller's `trans`: a library tag the caller registered is not "
            "Django's own callable, and the clause must compare against Django's",
        )
        self.assertIs(
            table["trans"],
            self.libraries["django.templatetags.i18n"]["trans"],
        )

        template = "{% for i in xs %}{{ i }}{% endfor %}"
        self.assertEqual(
            engine.from_string(template).render(Context({"xs": [1, 2]})),
            "shadow(for)",
            "the caller's `for` did not run: the predicate carried the parse's "
            "own table, so it answered `Rust's` for the caller's callable",
        )
        self.assertEqual(
            Engine().from_string(template).render(Context({"xs": [1, 2]})),
            "12",
            "the discriminator: Rust's own `for` does not render `shadow(for)`",
        )

    def test_the_clause_reads_a_library_tag_the_way_it_will_once_rust_owns_it(self):
        """The exact comparison `has_tag` makes, with the port staged.

        **A fabricated name, not a real one.** This row staged `trans` while it
        was a *future* Rust name; M12 Task 6a registered it and the unstaged
        control below stopped answering True, failing loudly. It was repointed to
        `blocktranslate` — the remaining unported name — and **Task 6b registers
        that too**, so it is repointed once more to a name no registry can
        overtake, spelled `unregistered_` (`FabricatedNameTests` states the
        convention). M12 now owns all 18 library names, so no real library tag is
        left to stage, and the fabricated name is what keeps the fixture a
        negative forever.

        The clause is `func is not self._django_tags.get(command)`: Rust's, unless
        something replaced the registered callable — `add_library` is
        `self.tags.update(lib.tags)` (`base.py:669-670`), so a `{% load %}` wins.
        Two libraries and an injected `_django_tags` entry are the whole input:
        one binds the fabricated name to the callable Django's own table holds
        for it (False — Rust's), the other to a different callable (True — the
        host's). Without the `_rust_owned` staging both answer True by the early
        return, which is why this row stages at all — an unstaged version asserts
        the early return twice and says nothing about the clause.
        """
        from django.template import Library, Node

        from django_rust_templates.render import parser_for

        name = "unregistered_blocktranslate"

        class ShadowNode(Node):
            def render(self, context):
                return "shadow(blocktranslate)"

        # Two distinct callables: `rust_body` stands in for the callable Django's
        # own table holds for the name, `shadow_body` for a library's replacement.
        def rust_body(parser, token):
            return ShadowNode()

        def shadow_body(parser, token):
            return ShadowNode()

        registered = Library()
        registered.tag(name, rust_body)
        shadow = Library()
        shadow.tag(name, shadow_body)

        def routed(library):
            parser = parser_for("", Engine())
            parser.add_library(library)
            host = self.host_module.ShellHost(parser)
            host._rust_owned = host._rust_owned | {name}
            # The fabricated name is in no registry, so `django_own_tags()` has
            # no entry for it; injecting one is what lets the "the registered
            # callable is Django's own" half be observable at all.
            host._django_tags = {**host._django_tags, name: rust_body}
            return host.has_tag(name)

        self.assertFalse(
            routed(registered),
            "the registered callable is the one Django's own table holds, so "
            "Rust owns it",
        )
        self.assertTrue(
            routed(shadow),
            "a library rebinding the name must still run its own body",
        )

        # And the same two names without the staging: both True, so the row
        # above is measuring the clause and not the early return.
        def unstaged(library):
            parser = parser_for("", Engine())
            parser.add_library(library)
            return self.host_module.ShellHost(parser).has_tag(name)

        self.assertTrue(
            unstaged(registered) and unstaged(shadow),
            "the unstaged control did not answer True for both, so the staging "
            "above is not what makes the two answers differ",
        )


class TzInterpositionTests(unittest.TestCase):
    """M12 Task 4: ``tz`` is our module — three tags, three node classes, no filter body.

    ``django.templatetags.tz`` is the third of the milestone's five libraries, and
    its three tags are the ones whose compile functions read a **body** and, for
    two of them, something other than a filter expression: ``{% localtime %}``
    takes ``on``/``off``/nothing and stores a *boolean*, ``{% get_current_timezone %}``
    takes only ``as name`` and reads no body at all, and ``{% timezone %}`` takes
    one filter expression and opens a block.

    **The three filters the same file registers — ``localtime``, ``timezone``,
    ``utc`` — are Task 7's and are deliberately not asserted here.** This task
    transcribes their Python bodies (the file is the unit of transcription) and
    writes no Rust filter body, only the three compile functions. So a row
    demanding ``_native``'s filter registry carry these names belongs to Task 7,
    and the filter claim lives in ``FilterRoutingTests``; their absence here is a
    division of labour rather than an omission. ``timezone`` is the interesting
    one: it is ``do_timezone`` under a registered alias, and it is the shared
    helper behind the ``localtime``/``utc`` filters — *not*, measured, behind the
    two tags that name it (see the commit).

    The routing claim is measured with the instrument the recon committed —
    ``.review/m12-measurements/m12_measure_tag_routing.py`` — whose verdict for
    these three flips ``theirs/theirs`` to ``ours/ours`` in this commit. That
    instrument answers "which *file* defines the compile function and the node
    class", which interposition alone moves; the row below is the direct-entry
    version of the claim, so a green instrument is not the whole story.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_tz_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django.templatetags.tz")
        self.assertIn("_impl", module.__file__, module.__file__)

    def test_each_tz_tag_compiles_to_our_node_class(self):
        """The routing claim, end to end, for all three names.

        A real ``Engine`` with the library spelled as a dotted path, because that
        is the route ``{% load tz %}`` takes and the route the suite takes: a bare
        ``Engine()`` cannot see ``django.templatetags.tz`` at all (measured — it
        reports ``'tz' is not a registered tag library``), so a row written
        against one would measure the probe rather than the routing. The node
        class is compared *by identity* to the one our transcribed module
        defines, which is the object ``get_nodes_by_type`` and every downstream
        ``isinstance`` will see.
        """
        import importlib
        import inspect

        tz = importlib.import_module("django.templatetags.tz")

        engine = Engine(libraries={"tz": "django.templatetags.tz"})
        for source, class_name in [
            ("{% load tz %}{% localtime on %}x{% endlocaltime %}", "LocalTimeNode"),
            ('{% load tz %}{% timezone "UTC" %}x{% endtimezone %}', "TimezoneNode"),
            (
                "{% load tz %}{% get_current_timezone as tzname %}",
                "GetCurrentTimezoneNode",
            ),
        ]:
            with self.subTest(source=source):
                template = engine.from_string(source)
                # `{% load %}` is itself a node in the nodelist — Django's
                # `do_load` returns one (`defaulttags.py:1131`) — so it is
                # skipped alongside the text node.
                nodes = [
                    node
                    for node in template.nodelist
                    if type(node).__name__ not in ("TextNode", "LoadNode")
                ]
                self.assertEqual([type(node).__name__ for node in nodes], [class_name])
                self.assertIs(type(nodes[0]), getattr(tz, class_name))
                self.assertIn("_impl", inspect.getfile(type(nodes[0])))


class L10nInterpositionTests(unittest.TestCase):
    """M12 Task 5: ``l10n`` is our module — one tag, one node class, two filters to Task 7.

    ``django.templatetags.l10n`` is the fourth of the milestone's five libraries
    and the smallest: 60 lines, one class (``LocalizeNode``), one tag
    (``{% localize %}``) and two filters. The tag is the only one in the
    milestone whose compile function is **variadic in what it accepts** — `on`,
    `off`, and *nothing* — and the "nothing" form is the one most easily mistaken
    for "accepts anything", which is why the rejected fourth form (a first bit
    that is neither ``on`` nor ``off``, and a second bit at all) is asserted in
    ``l10n.rs``'s own Rust tests, measured against Django rather than read off
    the prose.

    **The render stays Python's, and that is the coupling test, not a shortcut.**
    ``LocalizeNode.render`` flips ``context.use_l10n`` and calls the body's
    render; the tag's *compile* function is Rust's. The ``django.utils.formats``
    delegation lives in the two *filters* (``l10n.py:8,16``), whose bodies are
    M12 Task 7's Rust port — so this task transcribes their Python (the file is
    the unit of transcription) and delegates nothing.

    The routing claim is the instrument ``.review/m12-measurements/
    m12_measure_tag_routing.py``'s ``theirs/theirs`` for ``localize`` flipping to
    ``ours/ours``, 11/7 → 10/8; the row below is the direct-entry version, so a
    green instrument is not the whole story.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_l10n_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django.templatetags.l10n")
        self.assertIn("_impl", module.__file__, module.__file__)

    def test_the_localize_tag_compiles_to_our_node_class(self):
        """The routing claim, end to end, for the tag and all three accepted forms.

        A real ``Engine`` with the library spelled as a dotted path — the route
        ``{% load l10n %}`` takes and the route the suite takes — for the reason
        ``TzInterpositionTests`` states: a bare ``Engine()`` cannot see
        ``django.templatetags.l10n`` at all (measured — ``'l10n' is not a
        registered tag library``), so a row against one would measure the probe.
        ``use_l10n`` is asserted because it is the *only* attribute the descriptor
        carries and it is the argument the compile function branches on: nothing,
        ``on`` and ``off`` are three inputs and the node distinguishes two, so a
        port that read only ``bits[1]`` would build ``True`` for the bare form by
        accident and the two-argument forms correctly — the bare form is the one
        that catches the difference.
        """
        import importlib
        import inspect

        l10n = importlib.import_module("django.templatetags.l10n")

        engine = Engine(libraries={"l10n": "django.templatetags.l10n"})
        for source, class_name, use_l10n in [
            ("{% load l10n %}{% localize %}x{% endlocalize %}", "LocalizeNode", True),
            ("{% load l10n %}{% localize on %}x{% endlocalize %}", "LocalizeNode", True),
            (
                "{% load l10n %}{% localize off %}x{% endlocalize %}",
                "LocalizeNode",
                False,
            ),
        ]:
            with self.subTest(source=source):
                template = engine.from_string(source)
                nodes = [
                    node
                    for node in template.nodelist
                    if type(node).__name__ not in ("TextNode", "LoadNode")
                ]
                self.assertEqual([type(node).__name__ for node in nodes], [class_name])
                self.assertIs(type(nodes[0]), getattr(l10n, class_name))
                self.assertIn("_impl", inspect.getfile(type(nodes[0])))
                self.assertIs(nodes[0].use_l10n, use_l10n)

    def test_the_two_filters_are_registered_by_our_module(self):
        """Task 7's two filters, pinned as **coverage** and not as a Rust body.

        ``localize`` and ``unlocalize`` (``l10n.py:8,16``) are the module's two
        filters, and Task 7 is the task that gives them Rust bodies. This task
        transcribes their Python — the file is the unit of transcription, and a
        per-file table cannot express a half-transcribed module — so the claim
        this commit can make is the interposition one: both names resolve to
        callables defined by *our* file, which is the object the filter predicate
        compares against once Task 7 widens ``django_own_filters()``.

        Nothing here asserts a Rust body exists, because none does yet — and an
        assertion that the registry *lacks* the names would redden the very
        commit that adds them, which is the negative-fixture expiry
        ``FabricatedNameTests`` documents. So the row is the positive identity
        claim only.
        """
        import importlib
        import inspect

        l10n = importlib.import_module("django.templatetags.l10n")
        for name in ("localize", "unlocalize"):
            with self.subTest(name=name):
                func = l10n.register.filters.get(name)
                self.assertIsNotNone(
                    func, f"{name} is not in our l10n registry at all"
                )
                self.assertIn(
                    "_impl",
                    inspect.getfile(func),
                    f"{name} resolves to {inspect.getfile(func)}, not our file",
                )


class I18nInterpositionTests(unittest.TestCase):
    """M12 Task 6a: ``i18n`` is our module — eight tags, seven node classes.

    ``django.templatetags.i18n`` is the fifth and last of the milestone's five
    libraries, and the largest. Task 6a landed eight of its ten tag names; Task
    6b adds ``blocktranslate``/``blocktrans`` — one body under two names, the
    node that carries the token stream rather than a nodelist — so it is 6b that
    takes the meter's second scope to zero and completes the library.

    **The four filters the same file registers — ``language_name``,
    ``language_name_translated``, ``language_name_local`` and ``language_bidi``
    (``i18n.py:293,298,304,309``) — are Task 7's.** This task transcribes their
    Python (the file is the unit of transcription) and writes no Rust filter
    body, so a row demanding ``_native``'s filter registry carry these names
    belongs to Task 7; their absence here is a division of labour, not an
    omission.

    **Two splittings, and which tag gets which** (the module doc in
    ``crates/template-core/src/parser/tags/i18n.rs`` states it for the Rust
    side): ``get_available_languages``, ``get_current_language`` and
    ``get_current_language_bidi`` read ``token.contents.split()`` and store a
    **bare string** as the ``as`` target; the other five use ``split_contents``
    and compile their operand. A port that swapped them compiles and passes
    nothing, which is why the resolution row below asserts the stored value's
    type and not only the class.

    The routing claim has an instrument —
    ``.review/m12-measurements/m12_measure_tag_routing.py`` — whose verdict for
    all ten i18n names flips ``theirs/theirs`` to ``ours/ours`` in this commit,
    *measured to be a file-ownership verdict rather than a body count*: the
    blind-spot probe (``.review/m12-measurements/m12_measure_instrument_blindspot.py``)
    shows repointing ``__file__`` alone flips 10 of 10. The rows below are the
    direct-entry version, so a green instrument is not the whole story.

    6b's rows are at the end, and the two that matter are the ones no reading of
    ``do_block_translate``'s source produces: the missing-``{% plural %}``
    complaint is a *block-tag* message with **no** ``(seen …)`` clause (§4 trap
    4), and the ``(seen …)`` text can hold a **TEXT token's contents** because
    the scan loop reassigns the function's own ``token`` (§4 trap 2). Those two
    are why the assertions are on the full message rather than on the exception
    class.
    """

    @classmethod
    def setUpClass(cls):
        _shim.install()

    def _engine(self):
        # A real `Engine` with the library spelled as a dotted path, because that
        # is the route `{% load i18n %}` takes. A bare `Engine()` cannot see
        # `django.templatetags.i18n` at all (measured — `'i18n' is not a
        # registered tag library`), so a row against one would measure the probe.
        return Engine(libraries={"i18n": "django.templatetags.i18n"})

    def test_i18n_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django.templatetags.i18n")
        self.assertIn("_impl", module.__file__, module.__file__)

    def test_each_i18n_tag_compiles_to_our_node_class(self):
        """The routing claim, end to end, for the ten names and eight classes.

        ``translate`` and ``trans`` are one body under two registry entries, so
        both are asserted against the same class — the shape `static`'s three
        tag names have, and the same shape ``blocktranslate``/``blocktrans``
        have from Task 6b. The node class is compared *by identity* to the one
        our transcribed module defines, which is the object
        ``get_nodes_by_type`` and every downstream ``isinstance`` sees.
        """
        import importlib
        import inspect

        i18n = importlib.import_module("django.templatetags.i18n")
        engine = self._engine()

        for source, class_name in [
            (
                "{% load i18n %}{% get_available_languages as langs %}",
                "GetAvailableLanguagesNode",
            ),
            (
                "{% load i18n %}{% get_language_info for code as li %}",
                "GetLanguageInfoNode",
            ),
            (
                "{% load i18n %}{% get_language_info_list for langs as ls %}",
                "GetLanguageInfoListNode",
            ),
            (
                "{% load i18n %}{% get_current_language as cur %}",
                "GetCurrentLanguageNode",
            ),
            (
                "{% load i18n %}{% get_current_language_bidi as bidi %}",
                "GetCurrentLanguageBidiNode",
            ),
            ('{% load i18n %}{% translate "x" %}', "TranslateNode"),
            ('{% load i18n %}{% trans "x" %}', "TranslateNode"),
            (
                '{% load i18n %}{% language "de" %}x{% endlanguage %}',
                "LanguageNode",
            ),
            (
                "{% load i18n %}{% blocktranslate %}x{% endblocktranslate %}",
                "BlockTranslateNode",
            ),
            (
                "{% load i18n %}{% blocktrans %}x{% endblocktrans %}",
                "BlockTranslateNode",
            ),
        ]:
            with self.subTest(source=source):
                template = engine.from_string(source)
                # `{% load %}` is itself a node in the nodelist — Django's
                # `do_load` returns one (`defaulttags.py:1131`) — so it is
                # skipped alongside the text node.
                nodes = [
                    node
                    for node in template.nodelist
                    if type(node).__name__ not in ("TextNode", "LoadNode")
                ]
                self.assertEqual([type(node).__name__ for node in nodes], [class_name])
                self.assertIs(type(nodes[0]), getattr(i18n, class_name))
                self.assertIn("_impl", inspect.getfile(type(nodes[0])))

    def test_translate_as_var_stores_and_returns_nothing(self):
        """The ``as var`` branch: store into the context and emit nothing.

        `TranslateNode.render` returns ``""`` on this branch (`i18n.py:97-99`),
        so the ``[`` before ``{{ greeting }}`` is the tag emitting nothing and the
        ``[`` in the output is the text node — a port that returned the value
        *and* stored it would render ``[JaJa]``.

        German rather than a fake catalog: Django's own catalogue translates
        ``Yes`` to ``Ja`` (measured on the pinned tree with
        ``translation.override("de")``), which makes the branch observable
        without any test-local catalogue. That is what shows the value came
        *through the translation*, not that the node echoed its operand.
        """
        from django.utils import translation

        template = self._engine().from_string(
            '{% load i18n %}{% translate "Yes" as greeting %}[{{ greeting }}]'
        )
        with translation.override("de"):
            self.assertEqual(template.render(Context({})), "[Ja]")

    def test_translate_noop_marks_but_does_not_translate(self):
        """The ``noop`` branch: mark for extraction, return the string unchanged.

        Under the same German catalogue ``{% translate "Yes" %}`` renders ``Ja``;
        with ``noop`` it renders ``Yes``. That is the whole point of the option
        (`i18n.py:375-379`) and it is the pair the milestone's gate weighs, so it
        is asserted as a pair — the ``Ja`` half is the control that shows the
        catalogue is live, without which ``Yes`` would be indistinguishable from
        a broken engine.
        """
        from django.utils import translation

        engine = self._engine()
        translated = engine.from_string('{% load i18n %}{% translate "Yes" %}')
        marked = engine.from_string('{% load i18n %}{% translate "Yes" noop %}')
        with translation.override("de"):
            self.assertEqual(translated.render(Context({})), "Ja")
            self.assertEqual(marked.render(Context({})), "Yes")

    def test_the_six_refusals_name_the_spelling_that_was_used(self):
        """Every message names ``bits[0]``, so ``trans`` and ``translate`` differ.

        Six distinct messages (`i18n.py:400-450`), each written out in full —
        the assertion on the *text* is what makes the row able to tell the two
        spellings apart, since a row that used the wrong spelling still compiles
        and raises. ``{% trans %}`` is exercised against the same two messages a
        second time so the substitution is measured rather than assumed.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, expected in [
            (
                "{% load i18n %}{% translate %}",
                "'translate' takes at least one argument",
            ),
            (
                "{% load i18n %}{% trans %}",
                "'trans' takes at least one argument",
            ),
            (
                '{% load i18n %}{% translate "x" noop noop %}',
                "The 'noop' option was specified more than once.",
            ),
            (
                '{% load i18n %}{% translate "x" context %}',
                "No argument provided to the 'translate' tag for the context option.",
            ),
            (
                '{% load i18n %}{% translate "x" context as %}',
                "Invalid argument 'as' provided to the 'translate' tag for the context option",
            ),
            (
                '{% load i18n %}{% translate "x" as %}',
                "No argument provided to the 'translate' tag for the as option.",
            ),
            (
                '{% load i18n %}{% translate "x" bogus %}',
                "Unknown argument for 'translate' tag: 'bogus'. The only options "
                "available are 'noop', 'context \"xxx\", and 'as VAR'.",
            ),
            (
                '{% load i18n %}{% trans "x" bogus %}',
                "Unknown argument for 'trans' tag: 'bogus'. The only options "
                "available are 'noop', 'context \"xxx\", and 'as VAR'.",
            ),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(str(caught.exception), expected)

    def test_language_takes_one_argument_and_reads_a_body(self):
        """`{% language %}`'s single-argument refusal and its body parse.

        The refusal is its own message, and the accepted form reads a body — so
        the row asserts the node and its nodelist, not only the error.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        template = engine.from_string(
            '{% load i18n %}{% language "de" %}x{% endlanguage %}'
        )
        node = next(
            n for n in template.nodelist if type(n).__name__ == "LanguageNode"
        )
        self.assertEqual(len(node.nodelist), 1, "the body is one text node")

        for source in [
            "{% load i18n %}{% language %}x{% endlanguage %}",
            "{% load i18n %}{% language a b %}x{% endlanguage %}",
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(
                    str(caught.exception), "'language' takes one argument (language)"
                )

    def test_the_duplicate_check_runs_before_dispatch(self):
        """`option in seen` is checked first (`i18n.py:414-417`), before dispatch.

        So a repeated option is refused even when its first use was valid. **The
        message names the option and not the tag** (`"The '%s' option was
        specified more than once." % option`, `i18n.py:415-417`), which is the
        observable that tells this branch from every other one in the scan — all
        five of which interpolate `bits[0]`. A port that dispatched first would
        silently ignore the second occurrence.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, option in [
            ('{% load i18n %}{% translate "x" noop noop %}', "noop"),
            ('{% load i18n %}{% translate "x" context "a" context "b" %}', "context"),
            ('{% load i18n %}{% translate "x" as a as b %}', "as"),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(
                    str(caught.exception),
                    f"The '{option}' option was specified more than once.",
                )

    def test_a_context_value_may_not_be_as_or_noop(self):
        """The `invalid_context` set is `{"as", "noop"}` (`i18n.py:410,428-432`)."""
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for value in ("as", "noop"):
            source = f'{{% load i18n %}}{{% translate "x" context {value} %}}'
            with self.subTest(value=value):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(
                    str(caught.exception),
                    f"Invalid argument '{value}' provided to the 'translate' tag "
                    "for the context option",
                )

    def test_the_as_name_is_not_validated(self):
        """`asvar = value` with no check at all (`i18n.py:441`).

        A word that is not a legal variable name is still stored — unlike
        `context`'s value, which has a rejection set. The contrast with the row
        above is the claim.
        """
        engine = self._engine()
        template = engine.from_string('{% load i18n %}{% translate "x" as not-a-var %}')
        node = next(n for n in template.nodelist if type(n).__name__ == "TranslateNode")
        self.assertEqual(node.asvar, "not-a-var")

    def test_a_trailing_context_or_as_has_its_own_message(self):
        """`remaining.pop(0)`'s `IndexError` is caught and given a dedicated text.

        Not the unknown-argument branch (`i18n.py:421-427,435-440`) — the message
        names the option that ran out of arguments.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, expected in [
            (
                '{% load i18n %}{% translate "x" context %}',
                "No argument provided to the 'translate' tag for the context option.",
            ),
            (
                '{% load i18n %}{% translate "x" as %}',
                "No argument provided to the 'translate' tag for the as option.",
            ),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(str(caught.exception), expected)

    def test_language_validates_its_argument_before_parsing_the_body(self):
        """The argument is compiled before `parser.parse` (`i18n.py:613-614`).

        Observable in both directions: a bad argument reports the *argument*
        error, and a valid argument with a missing terminator reports
        `Unclosed tag` — which is the control that shows the row measures the
        order and not a blanket refusal. `{% timezone %}` has the identical
        shape and its row states the same thing.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string("{% load i18n %}{% language %}x")
        self.assertEqual(
            str(caught.exception), "'language' takes one argument (language)"
        )
        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string('{% load i18n %}{% language "de" %}x')
        self.assertEqual(
            str(caught.exception),
            "Unclosed tag on line 1: 'language'. Looking for one of: endlanguage.",
        )

    # --- M12 Task 6b: `{% blocktranslate %}` / `{% blocktrans %}` -----------

    def test_block_translate_carries_the_token_stream_and_no_nodelist(self):
        """The one node in the milestone with no child nodelist.

        ``singular``/``plural`` are lists of real ``Token`` objects — the wire
        type is ``AttrValue::Tokens``, and a shell that decoded the body to
        strings would still render, so this asserts the *objects*. Then
        ``get_nodes_by_type``: ``Node.child_nodelists`` is ``("nodelist",)`` and
        the walk reads ``getattr(self, attr, None)`` (``base.py:1079``), so a
        node with no ``nodelist`` is skipped silently — confirmed here rather
        than assumed (§4 trap 7).
        """
        from django.template.base import Token, TokenType

        engine = self._engine()
        template = engine.from_string(
            "{% load i18n %}{% blocktranslate %}x{{ y }}{% endblocktranslate %}"
        )
        node = next(
            n for n in template.nodelist if type(n).__name__ == "BlockTranslateNode"
        )
        self.assertEqual([type(item) for item in node.singular], [Token, Token])
        self.assertEqual(
            [(item.token_type, item.contents) for item in node.singular],
            [(TokenType.TEXT, "x"), (TokenType.VAR, "y")],
        )
        self.assertEqual(node.plural, [], "no `count`, so no plural split")
        # The walk finds the node and nothing under it: there is no `nodelist`
        # to descend into, and no token in the two lists holds a node.
        self.assertEqual(node.get_nodes_by_type(type(node)), [node])

    def test_block_translate_the_four_message_shapes(self):
        """Four shapes from two raise sites, and the reassigned parameter.

        Row three is the one no reading of ``do_block_translate`` produces: the
        parenthetical holds a **TEXT token's contents** (``many``), because the
        scan loop writes ``token = parser.next_token()`` — the function's own
        parameter — and inside the plural scan the last write was a text token.
        Row one is the other message, with **no** ``(seen …)``, and it is
        reachable *with* ``count``: `{% plural %}`'s absence is reported by the
        singular scan, not by the final check.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, expected in [
            (
                "{% load i18n %}{% blocktranslate count n=x %}one{% endblocktranslate %}",
                "'blocktranslate' doesn't allow other block tags inside it",
            ),
            (
                "{% load i18n %}{% blocktranslate count n=x %}one{% plural %}many{% foo %}",
                "'blocktranslate' doesn't allow other block tags (seen 'foo') inside it",
            ),
            (
                "{% load i18n %}{% blocktranslate count n=x %}one{% plural %}many",
                "'blocktranslate' doesn't allow other block tags (seen 'many') inside it",
            ),
            (
                "{% load i18n %}{% blocktranslate %}one{% plural %}many{% endblocktranslate %}",
                "'blocktranslate' doesn't allow other block tags (seen 'plural') inside it",
            ),
            # The end-tag failures, one per scan, and the alias as spelled.
            (
                "{% load i18n %}{% blocktranslate %}x{% endblocktrans %}",
                "'blocktranslate' doesn't allow other block tags (seen 'endblocktrans') inside it",
            ),
            (
                "{% load i18n %}{% blocktrans %}x{% endblocktranslate %}",
                "'blocktrans' doesn't allow other block tags (seen 'endblocktranslate') inside it",
            ),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(str(caught.exception), expected)

    def test_block_translate_reports_its_own_token_when_the_stream_ends(self):
        """Trap 2: ``token`` starts as the tag's own token and is reassigned.

        So ``{% blocktranslate %}`` with nothing after it names *itself*, and a
        template whose last token before exhaustion was text names that text.
        Both are the same raise site; what differs is which token the scan last
        held. Rust has no parameter aliasing, so this is the rule it must
        reproduce.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, seen in [
            ("{% load i18n %}{% blocktranslate %}", "blocktranslate"),
            ("{% load i18n %}{% blocktranslate %}x", "x"),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(
                    str(caught.exception),
                    f"'blocktranslate' doesn't allow other block tags "
                    f"(seen {seen!r}) inside it",
                )

    def test_block_translate_option_scan_messages(self):
        """Every option-scan branch, each message written out in full.

        The duplicate check runs **before** dispatch (``i18n.py:506-509``), so
        ``trimmed trimmed`` reports the duplicate and not anything about the
        repeated option's value — the row is one of a pair per branch.
        ``context``'s failure handling is ``except Exception``
        (`i18n.py:524-530`), not ``except IndexError``, so a *malformed filter*
        and a *missing argument* share one message: the ``x||y`` row is the
        malformed-filter half (§4 trap 5).
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, expected in [
            (
                "{% load i18n %}{% blocktranslate with %}",
                "\"with\" in 'blocktranslate' tag needs at least one keyword argument.",
            ),
            (
                "{% load i18n %}{% blocktranslate count %}",
                "\"count\" in 'blocktranslate' tag expected exactly one keyword argument.",
            ),
            (
                "{% load i18n %}{% blocktranslate count a=1 b=2 %}{% endblocktranslate %}",
                "\"count\" in 'blocktranslate' tag expected exactly one keyword argument.",
            ),
            (
                "{% load i18n %}{% blocktranslate context %}",
                "\"context\" in 'blocktranslate' tag expected exactly one argument.",
            ),
            (
                "{% load i18n %}{% blocktranslate context x||y %}",
                "\"context\" in 'blocktranslate' tag expected exactly one argument.",
            ),
            (
                "{% load i18n %}{% blocktranslate asvar %}",
                "No argument provided to the 'blocktranslate' tag for the asvar option.",
            ),
            (
                "{% load i18n %}{% blocktranslate trimmed trimmed %}",
                "The 'trimmed' option was specified more than once.",
            ),
            (
                "{% load i18n %}{% blocktranslate with a=1 with b=2 %}",
                "The 'with' option was specified more than once.",
            ),
            (
                "{% load i18n %}{% blocktranslate nope %}",
                "Unknown argument for 'blocktranslate' tag: 'nope'.",
            ),
        ]:
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError) as caught:
                    engine.from_string(source)
                self.assertEqual(str(caught.exception), expected)

        # The alias reports its own spelling, so the same raise yields two texts.
        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string("{% load i18n %}{% blocktrans with %}")
        self.assertEqual(
            str(caught.exception),
            "\"with\" in 'blocktrans' tag needs at least one keyword argument.",
        )

    def test_block_translate_renders(self):
        """The render-time rows, through the shell and Django's own node.

        Each is a value the port must produce, and the count family is a pair —
        ``b`` for two and ``a`` for one — because a port that ignored the count
        would render one of them for both. ``count c=s`` is the type check
        (`i18n.py:161-165`), raised at *render* time off the compiled
        expression. The two trim rows differ only by the option.
        """
        from django.template import TemplateSyntaxError

        engine = self._engine()
        for source, context, expected in [
            (
                "{% load i18n %}{% blocktranslate count c=n %}a{% plural %}b{% endblocktranslate %}",
                {"n": 2},
                "b",
            ),
            (
                "{% load i18n %}{% blocktranslate count c=n %}a{% plural %}b{% endblocktranslate %}",
                {"n": 1},
                "a",
            ),
            (
                "{% load i18n %}{% blocktranslate count c=n %}{% plural %}b{% endblocktranslate %}",
                {"n": 1},
                "",
            ),
            (
                "{% load i18n %}{% blocktranslate asvar v %}hi{% endblocktranslate %}[{{ v }}]",
                {},
                "[hi]",
            ),
            (
                "{% load i18n %}{% blocktranslate with x=y %}{{ x }}{% endblocktranslate %}",
                {"y": 7},
                "7",
            ),
            (
                "{% load i18n %}{% blocktranslate %}{{ missing }}{% endblocktranslate %}",
                {},
                "",
            ),
            (
                "{% load i18n %}{% blocktrans %}x{% endblocktrans %}",
                {},
                "x",
            ),
            (
                "{% load i18n %}{% blocktranslate trimmed %}\n   a   b\n{% endblocktranslate %}",
                {},
                "a   b",
            ),
            (
                "{% load i18n %}{% blocktranslate %}\n   a   b\n{% endblocktranslate %}",
                {},
                "\n   a   b\n",
            ),
        ]:
            with self.subTest(source=source):
                template = engine.from_string(source)
                self.assertEqual(template.render(Context(context)), expected)

        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string(
                "{% load i18n %}{% blocktranslate count c=s %}a{% plural %}b{% endblocktranslate %}"
            ).render(Context({"s": "x"}))
        self.assertEqual(
            str(caught.exception),
            "'c' argument to 'blocktranslate' tag must be a number.",
        )

    def test_block_translate_is_legal_inside_a_block(self):
        """Trap 7: a node with no nodelist that Django still nests.

        `{% blocktranslate %}` inside `{% block %}` is legal, and the walk that
        `ExtendsNode` and `get_nodes_by_type` use must still find it — the block
        *has* a nodelist and the translate node sits in it, so the finding is
        about the container, and it confirms the node itself contributes no
        children of its own. `{% plural %}` is **not** a registered tag (§4
        trap 3), so on its own it is an `Invalid block tag`; inside
        `{% blocktranslate %}` the scan consumes it and the parser never sees
        it.
        """
        from django.template import TemplateSyntaxError
        from django.templatetags.i18n import BlockTranslateNode

        engine = self._engine()
        template = engine.from_string(
            "{% load i18n %}{% block a %}{% blocktranslate %}x"
            "{% endblocktranslate %}{% endblock %}"
        )
        block = next(n for n in template.nodelist if type(n).__name__ == "BlockNode")
        found = block.get_nodes_by_type(BlockTranslateNode)
        self.assertEqual(len(found), 1, "the walk did not find the nested node")

        with self.assertRaises(TemplateSyntaxError) as caught:
            engine.from_string("{% load i18n %}{% plural %}")
        self.assertIn("Invalid block tag", str(caught.exception))


class DefaultTagsInterpositionTests(unittest.TestCase):
    """M11 Task 3: ``defaulttags`` is ours, and the tag-rebinding escape with it.

    Three claims, and the middle one is the milestone's Review Focus 2 rather
    than a restatement of the other two:

    * the module and the ``Node`` classes its tag corpus builds are ours;
    * a ``{% load %}``ed library that registers ``if`` wins over Rust's — the
      same escape as the filter row above, one level over, and the reason the
      host predicate is *identity* rather than a list of names it owns;
    * a rendered ``{% for %}`` holds an instance of *our* ``ForNode``, because
      the annotation and inheritance protocols compare classes.

    **What makes the rebinding row non-vacuous here.** Of the 24 tags this module
    registers, 20 are compiled and rendered by Rust, so their bodies are
    unreachable through the render path — a test that only rendered ``{% if %}``
    would pass whatever the module contained. The rebinding is the one input that
    puts a body from this file back on a live path, which is why it is the row
    that has to exist. (The other four — ``load``, ``querystring`` and the two
    stubs — are reached for the opposite reason: Rust owns none of those names.)
    """

    def setUp(self):
        _shim.install()

    def test_defaulttags_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django.template.defaulttags")
        self.assertIn("_impl", module.__file__, module.__file__)

    def test_the_node_classes_this_module_defines_are_not_djangos(self):
        """Review Focus 5: the classes the corpus builds, compared by identity.

        ``load_pinned`` executes the pinned file *fresh*, so every class here is
        a new object and the comparison is between two real things. ``URLNode`` is
        in the list because Django's own ``syntax_tests/test_url.py`` imports it
        **by name** from this module — the one class in the file with a consumer
        outside it.

        ``Node`` is not compared, for ``LoaderTagsInterpositionTests``' reason:
        the pinned file's ``from django.template.base import Node`` resolves
        through the shim to our ``base``, so the pinned module's ``Node`` is ours
        too and ``is not`` would fail on a correct tree.
        """
        import importlib
        import inspect

        from django.template import base, defaulttags

        from tests.pinned_module import load_pinned

        pinned = load_pinned("defaulttags.py")
        for name in ("ForNode", "IfNode", "IfChangedNode", "CycleNode", "URLNode"):
            with self.subTest(name=name):
                ours = getattr(defaulttags, name)
                self.assertIsNot(ours, getattr(pinned, name))
                self.assertIn("_impl", inspect.getfile(ours))

        self.assertIs(defaulttags.Node, base.Node)

    def test_a_rendered_for_holds_our_for_node(self):
        """The live half: the tree a render builds holds *our* class.

        ``isinstance`` against Django's classes is not enough on its own — the
        pinned module's bases are ours too — so this asserts the ``type`` of the
        node and the file that defines it, which is what a half-interposed module
        would fail.
        """
        import inspect

        from django.template import defaulttags
        from django_rust_templates.render import parse_template

        template = parse_template("{% for i in xs %}{{ i }}{% endfor %}", Engine())
        nodes = template.nodelist.get_nodes_by_type(defaulttags.ForNode)
        self.assertEqual(len(nodes), 1)
        self.assertIs(type(nodes[0]), defaulttags.ForNode)
        self.assertIn("_impl", inspect.getfile(type(nodes[0])))

    def test_a_loaded_library_that_rebinds_a_builtin_tag_runs_its_own_body(self):
        """Review Focus 2: a library's ``if`` beats Rust's, decided by identity.

        ``Parser.add_library`` is ``self.tags.update(lib.tags)``
        (``base.py:669-670``), so a ``{% load %}``ed library that registers ``if``
        replaces the compile function for that name *from the load onward*, and
        the host predicate is asked per token. Read the other way — "is ``if`` a
        name Rust owns?" — the second tag compiles in Rust and the library's body
        never runs, with the output still looking like a conditional.

        **Measured on the pinned tree first, and the template is a sandwich so
        that the answer says which half ran.** ``{% if x %}A{% endif %}`` before
        the load and ``{% if x %}B{% endif %}`` after it, with a library whose
        ``if`` renders ``shadow(...)``: stock Django answers ``Ashadow(B)`` —
        the first token bound the builtin, the second the library's. Ours must
        answer the same string, and ``AB`` is what the name-keyed arm produces.
        """
        from django.template import Context, Engine, Library, Node

        class ShadowIfNode(Node):
            def __init__(self, nodelist):
                self.nodelist = nodelist

            def render(self, context):
                return "shadow(%s)" % self.nodelist.render(context)

        def compile_shadow_if(parser, token):
            nodelist = parser.parse(("endif",))
            parser.delete_first_token()
            return ShadowIfNode(nodelist)

        library = Library()
        library.tag("if", compile_shadow_if)
        engine = Engine()
        engine.template_libraries["m11_shadow_tags"] = library

        source = "{% if x %}A{% endif %}{% load m11_shadow_tags %}{% if x %}B{% endif %}"
        self.assertEqual(
            engine.from_string(source).render(Context({"x": True})),
            "Ashadow(B)",
            "the library's `if` did not run for the token after the load; the "
            "routing decided by name rather than by the handle the parse bound",
        )

        # The discriminator, measured rather than asserted from the expected
        # string: the same two conditionals with the load removed, so both tokens
        # are Rust's. `AB` is what the name-keyed arm reports for the sandwich
        # above, because a load it did not consult leaves the routing unchanged.
        self.assertEqual(
            engine.from_string("{% if x %}A{% endif %}{% if x %}B{% endif %}").render(
                Context({"x": True})
            ),
            "AB",
            "Rust's own `if` does not render as `AB`, so the row above would not "
            "have discriminated a name-keyed arm from an identity-keyed one",
        )


if __name__ == "__main__":
    unittest.main()
