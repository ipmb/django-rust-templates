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
resolver in Task 5.
"""

import unittest

from django_rust_templates import _shim


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
        ``smartif`` objects (``template-py/src/lib.rs:667``). A row
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

        # The structure, which is what the guard reads: three edits, two of them
        # pairs and one a pure insertion of ours. Asserted here too because a
        # re-alignment that merged two edits into one would move `equal` in a way
        # this row could not explain on its own.
        self.assertEqual([op[0] for op in differing], ["delete", "replace", "replace"])

        self.assertEqual(
            equal,
            1192,
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
        and ``Parser.invalid_block_tag`` and the hand-written corpus does not.
        """
        import importlib
        import random

        import scripts.check_transcriptions as guard
        from tests.compared_path import measure
        from tests.parser_corpus import (
            RANDOM_CASES,
            RANDOM_SEED,
            TEMPLATES,
            random_template,
        )
        from tests.test_parser_parity import django_parse

        rng = random.Random(RANDOM_SEED)
        sources = list(TEMPLATES)
        sources += [random_template(rng) for _ in range(RANDOM_CASES)]

        for name, pinned_entries in sorted(guard.COMPARED_PATH.items()):
            module = importlib.import_module(guard.module_of(name))
            measured = measure(module, sources, django_parse)
            pinned = {label: (first, last) for first, last, label in pinned_entries}

            with self.subTest(module=name):
                self.assertEqual(
                    sorted(measured),
                    sorted(pinned),
                    f"the functions the parity harness executes in {name} have "
                    f"changed, so COMPARED_PATH['{name}'] is stale — regenerate "
                    "it with .review/m9-measurements/m9_measure_parse_path.py",
                )
            for label in sorted(pinned):
                with self.subTest(module=name, function=label):
                    self.assertEqual(measured[label], pinned[label])


if __name__ == "__main__":
    unittest.main()
