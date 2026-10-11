"""Direct-entry evidence that each ``engine`` module is actually ours.

The suite cannot see this. Measured (``.review/m9-recon.md`` finding 3): with
``engine/base.py`` a byte-for-byte copy of Django's, the coverage count reports
one fewer module, the whole suite is green, and the only row that notices is the
count. So a transcription that delegates nothing, or delegates wrongly, is
invisible to every other count this project accepts.

Every row here imports the module by its real name — ``django_rust_templates.engine.base``
and no other — and asserts on the file it resolved to, or on a Rust call it must
have made — the latter because a delegation that quietly called Django's own
implementation is green too (finding 1a). **Until the M18 rewiring the import ran
through the shim's finder; it does not now, and the rows did not weaken when the
finder left, because a direct import resolves to a strictly better-known path
than a mediated one.**

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

from django_rust_templates.engine import Context, Engine

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The engine's own directory. Rows here assert on the **file** a module resolved
#: to, and until the M18 rewiring they read that path off the shim
#: (``_shim._IMPL_DIR``). There is no shim and there is no second copy of the
#: tree: the file an import resolves to is the file under here, which is why the
#: constant is a plain path and not a lookup. It is spelled once so a rename of
#: the package moves every row at once.
ENGINE_DIR = PROJECT_ROOT / "python" / "django_rust_templates" / "engine"


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
    engine = Engine(loaders=[("django_rust_templates.engine.loaders.filesystem.Loader", [tmpdir])])
    return engine.get_template(template_name).render(Context(dict(context)))


class InterpositionTests(unittest.TestCase):
    def test_base_resolves_to_our_file(self):
        import django_rust_templates.engine.base as base

        self.assertIn("engine", base.__file__, base.__file__)

    def test_the_node_classes_are_defined_by_our_file(self):
        """Resolution and *definition* are different claims.

        The row above says the module is ours. This one says the five class
        objects a render actually uses are *defined* in it rather than bound into
        it from somewhere else — a ``base.py`` doing ``Node =
        django.template.base.Node`` would satisfy that row and fail this one, and
        would be a transcription that had kept Django's classes while wearing our
        file's name.

        **``inspect.getfile`` and ``cls.__module__`` are one mechanism** — the
        former follows the latter into ``sys.modules`` — so today either spelling
        would pass, and the reason this row reaches for the file is historical
        rather than technical: the first draft asserted ``"engine" in
        cls.__module__`` and failed on all five classes, because the engine then
        lived in ``django_rust_templates._impl`` and no class said ``engine``.
        M18 moved it to ``django_rust_templates.engine``, and the assertion is now
        simply what its name says.
        """
        import inspect

        import django_rust_templates.engine.base as base

        for name in ("Template", "NodeList", "Node", "Variable", "VariableNode"):
            with self.subTest(name=name):
                self.assertIn("engine", inspect.getfile(getattr(base, name)))

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
        from django_rust_templates.engine import Engine

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
        from django_rust_templates.engine import Engine

        template = Engine().from_string("{% csrf_token %}")
        self.assertTrue(hasattr(template, "extra_data"))
        self.assertIsInstance(template.extra_data, dict)

    def test_compile_nodelist_attaches_debug_info_to_a_parse_error(self):
        """The debug arm: a failed parse is annotated, not merely re-raised."""
        from django_rust_templates.engine import Engine
        from django_rust_templates.engine.exceptions import TemplateSyntaxError

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
        import django_rust_templates.engine.smartif as smartif

        self.assertIn("engine", smartif.__file__, smartif.__file__)

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
        ``smartif`` objects (``crates/template-py/src/lib.rs:595``). A row
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

        import django_rust_templates.engine.smartif as smartif
        from django_rust_templates.engine import Engine
        from django_rust_templates.engine.defaulttags import TemplateLiteral

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
        self.assertIn("engine", inspect.getfile(type(condition)))
        self.assertIs(TemplateLiteral.__mro__[1], smartif.Literal)
        self.assertIn("engine", inspect.getfile(TemplateLiteral.__mro__[1]))

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
        from django_rust_templates.engine import Engine, Origin, Template
        from django_rust_templates.engine.exceptions import TemplateSyntaxError

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

        project_root = pathlib.Path(__file__).resolve().parent.parent
        ours = (ENGINE_DIR / "base.py").read_text().splitlines()
        theirs = (project_root / ".django" / "django" / "template" / "base.py")
        theirs = theirs.read_text().splitlines()

        matcher = difflib.SequenceMatcher(a=ours, b=theirs, autojunk=False)
        opcodes = matcher.get_opcodes()
        equal = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag == "equal")
        differing = [op for op in opcodes if op[0] != "equal"]

        # The structure, which is what the guard reads: eleven edits — the marker
        # and its blank line as one pure insertion of ours, M18.1's `BaseContext`
        # import substitution, M16's module-level `_native` import as a second
        # pure insertion, two pairs for `compile_nodelist`'s delegation, then
        # M19's two — a substitution for `__slots__` and the routing cache as a
        # third pure insertion — then M11 Task 2's filter seam as a fourth, then
        # one replacement for `Variable._resolve_lookup`'s, then M19's third —
        # the render leaf's fast path as a fifth pure insertion, and the only
        # one of the five that lands *outside* every span the compared-path
        # table names — and then M19's fourth, the render walk, as a sixth.
        # Asserted here too
        # because a re-alignment that merged two edits into one would move
        # `equal` in a way this row could not explain on its own.
        #
        # Five rather than three since M9's Task 5, and the pair that grew is
        # the one worth reading: `difflib` splits the resolver's single
        # replacement in two because our `return` realigns against upstream's
        # `if getattr(e, "silent_variable_failure", …)` tail rather than against
        # the line that follows the loop. Both spans are declared, so the guard
        # is satisfied either way; this row is what would notice if a future
        # edit merged them and the declaration stopped describing the diff.
        #
        # **Six were five until M11 Task 2, and the sixth is a `delete`.**
        # That is the filter seam: ours 847-879 — a comment block, one import,
        # the identity test, the call and its `continue` — against none of
        # upstream's, because the arm *replaces* `resolve`'s loop body with a
        # call rather than wrapping it. `difflib` names a run that only one side
        # has `delete` when it is ours and `insert` when it is theirs, so the
        # shape of this list is also the check that the seam stayed an insertion:
        # an arm that had been written as a *rewrite* of the loop would show up
        # as a `replace` here and as a declared region with two spans.
        #
        # **The seam's run is 33 lines and 15 of them are executable**, and both
        # numbers are in this file and `check_transcriptions.py` because this seam
        # has already been miscounted three ways. `difflib` counts lines in the
        # run; `executable_lines` counts the lines of it that hold a statement,
        # and a parenthesised `import` contributes four of the fifteen. Neither is
        # the other's denominator. Hoisting those imports above the `for` — tried
        # and reverted — would have split this one `delete` into two, with
        # upstream's verbatim `arg_vals` loop equal in place between them, which is
        # why the shape of this list is worth asserting rather than deriving.
        # **M19 does that split for a different reason and at a different
        # boundary** — see its own paragraph below; the imports are still where
        # M11 put them.
        #
        # **M16 moved the shape, deliberately, and this row is why the move was
        # measured rather than noticed.** Hoisting `from django_rust_templates
        # import _native` from the two seam bodies to module scope (worth ~50us
        # a render — see `.review/m16-record.md`) rewrote the list in three ways
        # at once: a new `delete` at ours 73 (one line added at module scope,
        # none upstream), the filter seam's `delete` shrinking from ours 815-847
        # to 816-847 and from 17 executable lines to 16, and — the one worth
        # reading — a **merge**. The in-function import plus the blank line after
        # it were what had kept the resolver's two hunks apart; removing them let
        # `difflib` extend the single replacement to upstream's `return current`
        # tail. So our differing lines went 93 -> 92 while upstream's went 86 ->
        # 87, and the matched count is each side's length less its differing
        # lines, which agrees: 1214-93 = 1121 becomes 1212-92 = 1120, and
        # 1207-87 = 1120. **The equal count falls by one, and it is not the net
        # of the edit** (which is -2 on our side): it falls because upstream's
        # blank line 1022 — which `difflib` had paired with one of our blank
        # lines — now sits inside the replaced run instead of being matched.
        #
        # **The seventh edit is a `replace`, and it is the M18 namespace.** M18.1
        # stopped transcribing Django's *imports*: our 61 is
        # `from .context import BaseContext` where upstream's 59 is
        # `from django.template.context import BaseContext`. The line stood inside
        # a run `difflib` had matched, so substituting it does three things at
        # once and moves `equal` by `-1` **without changing either file's length**
        # — 1212 lines before and 1212 now. One line was taken out of the equal
        # blocks and one line was added to each side's differing lines: ours
        # 92 -> 93, upstream's 87 -> 88, so 1212-93 = 1207-88 = 1119 both ways.
        # It is the *pair* of those two counts that is the check, not either one:
        # +1 differing on **both** sides is a substitution, while +1 on one side
        # alone would be an insertion or a deletion wearing the same list.
        #
        # The op list gained the `replace` **in the middle of the two
        # `delete`s** and this is the reason it is asserted as a list rather than
        # as a count: an inserted `replace` that had landed at either end would
        # have left the length at seven and read as the same edit. Position in
        # this list is `difflib`'s statement of *where in the file* the change is.
        #
        # **M19 adds two ops in one commit, and they are the two shapes this list
        # can have.** `__slots__` gains a fifth name — ours 743 against upstream's
        # 737, one line each, so a `replace` and a **substitution**: `equal` falls
        # by one, 1119 -> 1118, with neither file's *length* relevant (ours grew,
        # but by the other op). The routing cache is a pure insertion at ours 809 —
        # a `delete` — and moves `equal` by nothing at all, which is what an
        # insertion is. So the arithmetic that this row is built on
        # carries through: ours is 1264 lines and 146 of them differ, upstream's
        # 1207 with 89 differing, and 1264-146 = 1207-89 = 1118. **Two ops rather
        # than one because the two changes are of different kinds**, which is the
        # same distinction the pair of counts above draws; a single edit that
        # produced two ops would have been a re-alignment, and this row is what
        # would have said so.
        #
        # **M19's third op is the tenth and the last, and it moves `equal` by
        # nothing.** The render leaf's fast path is twelve lines inserted at ours
        # 1174 against none of upstream's, so it is a `delete` in this list's
        # vocabulary — ours alone — and an insertion is the one shape that leaves
        # both sides' *differing* counts alone on the other side: ours goes 134 →
        # 146 while upstream's stays 89, which is exactly what a twelve-line
        # addition to our file with no counterpart should do, and 1264-146 =
        # 1207-89 holds. It lands **after `Variable._resolve_lookup`'s
        # `replace`** rather than beside the other M19 ops, and that placement is
        # the assertion: the earlier two are inside `FilterExpression`, in the
        # region of the file that class occupies, while this one is in the render
        # leaf a thousand lines below it — a reader who saw three M19 ops and
        # assumed one commit had made them all in one place would be wrong, and
        # the position — last, until step 5.5 appended one below it — is what says so.
        #
        # It is the first op in this list that lands inside a function the
        # compared-path table does not name (`render_value_in_context`), and that
        # is why it is a pure shift of the two entries below it rather than a
        # resizing of one of them — see the note on the guard's `COMPARED_PATH`.
        #
        # The pair lands **between `compile_nodelist`'s two `replace`s and the
        # filter seam's `delete`** — the region of the file `FilterExpression`
        # occupies — and that placement is asserted rather than incidental: the
        # cache's run is ours 809-839 and the seam's begins eight lines after it,
        # in the same method, so a reader who saw the two `delete`s adjacent in
        # this list without reading the file would take them for one change. The
        # run grew from 23 lines to 31 when the prologue gained its
        # `if self.filters:` guard and the comment stating why, which is *inside*
        # the same run: an insertion with no equal line in it stays one op, so the
        # list's length is the check on that too.
        #
        # **The render walk is the eleventh edit and the eleventh and twelfth
        # `difflib` op, and it is the first whose op count the reader can
        # *derive* rather than measure.** Three insertions, and the list gains
        # two: the `_native_plan` class attribute at ours 146-153 and the walk's
        # arm inside `_render` at ours 178-188 are each a run of our lines with no
        # upstream counterpart and no equal line inside, so each is one `delete`;
        # `compile_nodelist`'s plan copy extends the `replace` already there to
        # ours 225-235 against upstream's 199-201, and an insertion into a
        # replaced run extends it without adding an op. Both new `delete`s land
        # **adjacent to the existing `_native` `delete` at ours 74** — the
        # module-scope region M16 made — so the head of the list now reads
        # `delete, replace, delete, delete, delete`, and the run of three is the
        # assertion: three separate insertions in three separate classes of the
        # file, with no equal line between any two of them. `equal` does not move
        # at all, because an insertion on one side only is the shape that cannot
        # take a line out of a matched block — unlike M18's substitution, which
        # did so without changing either file's length. Re-measured rather than
        # derived: ours is 1347 lines, 229 of them differ, upstream's 1207 with 89
        # differing, and 1347-229 = 1207-89 = 1118.
        #
        # **M19 step 5 adds two more, both `delete`s, and they are the first pair
        # this list has carried that a run of *matched* lines sits between.** The
        # slice attaches a plan to every `{% block %}` body and gives `NodeList`
        # the arm that reads it: an eighteen-line region at ours 1181-1198 (the
        # plan attribute, whose comment grew) and a forty-two-line one at ours
        # 1208-1249 (`_render_with_plan`, inserted after `get_nodes_by_type`).
        # Both are ours alone, so both are `delete`s and `equal` moves by nothing
        # — 1118 again — while our *differing* count goes 229 → 289 and upstream's
        # stays 89. Re-measured: ours is 1407 lines, 289 differ, upstream's 1207
        # with 89, and 1407-289 = 1207-89 = 1118, the same number a third time.
        #
        # **They arrive as the twelfth and thirteenth entries of the list this row
        # asserts, and the nine matched lines at ours 1199-1207 are why the pair
        # reads as adjacent here.** That list is the **differing** opcodes —
        # `[op for op in matcher.get_opcodes() if op[0] != "equal"]`, fifteen of
        # the thirty `difflib` returns for this pair of files — and every
        # position named in this comment is a position in *it*, never in
        # `get_opcodes()`. The distinction is worth the sentence because the two
        # counts differ by a factor of two and only one of them is asserted
        # below. `difflib`
        # matched that run because `NodeList.render` and
        # `NodeList.get_nodes_by_type` are upstream's to the character — the two
        # functions the slice deliberately did *not* touch, an arm inside `render`
        # being the shape the guard's compared path refuses. So the newest two
        # `delete`s sit next to each other in this list with no other op between
        # them, while in the file a *matched* block separates them: **adjacency in
        # this list is `difflib`'s statement about the ops and not about the
        # file**, the same way the M18 paragraph above distinguishes order from
        # position. The pair is also what splits the guard's `COMPARED_PATH` into
        # two bands rather than shifting it as one — +18 for the two functions
        # above the second region, +60 below it.
        #
        # The render leaf's `delete` is pushed from position twelve to fourteen by
        # the pair. It held the last position in this list until the read pin
        # below took it, and it is the pushed-back entry rather than either of the
        # newest two that shows the pair landed **above** it in the file rather
        # than beside it.
        #
        # **The fifteenth entry is M19 step 5.5 lever 3, it is the last, and it is
        # the first op here that sits at the end of *our* file.** `engine/base.py`
        # gains `BASE_CONTEXT_GETITEM = BaseContext.__getitem__` at ours 1443, so
        # the run is ours 1408-1443 against upstream's 1207: thirty-six lines, one
        # statement and thirty-five that are comment and blank. It is ours alone,
        # so it is a `delete`, and `equal` moves by nothing for the fourth time in
        # this sequence and for the same reason — an insertion with no matched line
        # inside it cannot take a line out of an equal block. Re-measured: ours is
        # 1443 lines, 325 of them differ, upstream's 1207 with 89, and
        # 1443-325 = 1207-89 = 1118. The block grew from twenty-six lines to
        # thirty-six in the round that repaired the pin's *stated* reason — the
        # statement never moved, so no declared span moved either, which is the
        # property the paragraph below is about.
        #
        # Its *position* is the assertion and not a fact about the edit's size. The
        # binding has to be in this module because **the package's import order
        # runs this module's body before any project code can reach the class**:
        # `django_rust_templates/engine/__init__.py:26` — `from .engine import
        # Engine`, the package's first statement — reaches
        # `engine/engine.py:7`'s `from .base import Template`, so any route to
        # `BaseContext` has already run the block below it. `engine/base.py:61`'s
        # `from .context import BaseContext` is *downstream* of that and not the
        # reason. `test_loop_read_guard.py` pins the invariant. But it has to be
        # *below every declared span*, and those are literal line ranges: the
        # highest ends
        # at 1407, `token_kwargs`'s, which was this file's last line. A binding one
        # line higher would have re-keyed that span and every other one under it in
        # `scripts/check_transcriptions.py`. So this is the one edit in the
        # sequence whose placement was chosen by the *guard's* arithmetic rather
        # than by the code's, and the list's last position is what records it: a
        # reader who found this op at the head would be looking at an edit in the
        # middle of the file. It is also the first entry here that no re-alignment
        # could have moved — the appended text shares no line with upstream's, so
        # there is nothing for `difflib` to pair it against.
        self.assertEqual(
            [op[0] for op in differing],
            [
                "delete",
                "replace",
                "delete",
                "delete",
                "delete",
                "replace",
                "replace",
                "replace",
                "delete",
                "delete",
                "replace",
                "delete",
                "delete",
                "delete",
                "delete",
            ],
        )

        self.assertEqual(
            equal,
            1118,
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

        The ``engine`` assertion is the reviewer's question — "does ``module_of``
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
                "engine", module.__file__, f"{name} did not resolve to our file"
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

    **``utils``'s three rows left in the M18 rewiring, and they left because the
    module did.** ``engine/utils.py`` held ``EngineHandler`` — the registry that
    resolved a project's ``TEMPLATES[0]["BACKEND"]`` to a class — and the registry
    itself is what the backend route replaces: Django's own ``django.template.utils``
    does that job now, for our backend exactly as for any other, and there is no
    ``engines`` binding in this package to build it from. So the module is on the
    ``NOT_THE_ENGINE`` list and there is no file under ``engine`` to have rows
    about. Deleting them was the honest move and not a loss of coverage: the
    claim they made — "a template load runs our code" — is made, end to end and
    against a real ``Engine``, by ``test_a_template_loads_and_renders_through_our_engine``
    below.
    """

    def test_context_resolves_to_our_file(self):
        import django_rust_templates.engine.context as context

        self.assertIn("engine", context.__file__, context.__file__)

    def test_the_context_classes_are_not_djangos(self):
        import inspect

        import django_rust_templates.engine.context as context

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
                self.assertIn("engine", inspect.getfile(ours))

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
        import django_rust_templates.engine.context as context

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
        import django_rust_templates.engine.engine as engine

        self.assertIn("engine", engine.__file__, engine.__file__)

    def test_the_engine_class_is_not_djangos(self):
        import inspect

        import django_rust_templates.engine.engine as engine

        from tests.pinned_module import load_pinned

        self.assertIsNot(engine.Engine, load_pinned("engine.py").Engine)
        self.assertIn("engine", inspect.getfile(engine.Engine))
        # `django_rust_templates.engine.Engine` is the same object, because the package
        # `__init__` imports it from this module by name.
        import django_rust_templates.engine as package

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
        import django_rust_templates.engine.engine as engine

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
        import django_rust_templates.engine.engine as django_engine

        from tests.compared_path import instrumented

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "greet.html").write_text("{{ name }}!")
            engine = Engine(
                loaders=[("django_rust_templates.engine.loaders.filesystem.Loader", [tmpdir])]
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
            Engine(app_dirs=True, loaders=["django_rust_templates.engine.loaders.filesystem.Loader"])

    def test_every_empty_pin_is_a_measurement_and_not_a_silence(self):
        """Every ``COMPARED_PATH`` key pinned ``[]`` is a measurement, not a silence.

        ``COMPARED_PATH[name] == []`` says that no parity harness executes a
        function in that module. It is the one kind of pin a **broken instrument**
        also produces: if the wrapper never fired, every measured set would be
        empty and the empty ones would agree with it. The failure to fear for a
        claim of the form "this count is zero" is therefore an instrument that
        reports zero for everything.

        Three ways a zero can be vacuous, and the row splits the guard's empty
        pins against all three rather than trusting the number:

        * the instrument is globally dead — refuted by the positive control
          below, which instruments ``engine`` over the same ``django_parse`` the
          empty modules are measured against;
        * the module cannot be imported at all, so nothing could have fired —
          asserted by the import in the loop;
        * **the module has nothing to instrument**, which is what the two
          packages are. ``__init__.py`` and ``loaders/__init__.py`` re-export;
          :func:`definitions` finds no function of their own, so "no harness
          executes this module" is true of *any* corpus, including one that runs
          every line of it. They are named and counted here rather than left to
          pad the population, because "eleven modules are untouched" would then
          be 9/11 evidence wearing a bigger n.

        **The row this replaces named ``utils.py``, and ``utils.py`` is gone**
        (``03038e6``): its subject was a module that no longer exists, which is
        the failure mode this whole class is written against.

        The last arm is the discriminating one. Instrumenting a module and
        running the corpus is weak on its own — a module the corpus cannot
        *reach* scores zero for a reason that has nothing to do with the corpus.
        So ``loaders/locmem.py`` is measured twice: once under a call that does
        enter it, where it must report non-zero, and once under ``django_parse``,
        where it must report zero. "The instrument fires, and it did not fire
        here" is one statement, and the two halves should not be able to drift
        apart.

        ``locmem`` is the subject rather than one of the other eight because it
        is the one whose emptiness a reader is most likely to doubt: it defines a
        real ``Loader`` with two methods, and it is reached by an ordinary
        ``TEMPLATES`` setting. What keeps it at zero is that ``template_tests``
        never constructs a locmem loader — a positive statement, which the first
        arm turns into a measurement.
        """
        import importlib

        import scripts.check_transcriptions as guard

        from tests.compared_path import definitions, instrumented
        from tests.test_parser_parity import django_parse

        empty = sorted(name for name, rows in guard.COMPARED_PATH.items() if not rows)
        # The population, before the split: a guard whose table had lost its
        # empty pins would make every assertion below agree about nothing.
        self.assertGreater(len(empty), 5, empty)

        carriers, vacuous = [], []
        for name in empty:
            with self.subTest(name=name):
                module = importlib.import_module(guard.module_of(name))
                (carriers if definitions(module) else vacuous).append(name)

        self.assertEqual(
            vacuous,
            ["__init__.py", "loaders/__init__.py"],
            "the empty pins no longer split the way this row states: a key that "
            "lands in `vacuous` is one whose zero is true of every corpus, and a "
            "package that has grown a function of its own has left that class",
        )

        import django_rust_templates.engine.engine as engine
        import django_rust_templates.engine.loaders.locmem as locmem

        with instrumented(engine) as positive:
            django_parse("{{ x }}")
        self.assertGreater(
            sum(positive.values()), 0, "the instrument reports nothing at all"
        )

        with instrumented(locmem) as reached:
            loader = locmem.Loader(Engine(), {"x": "hi"})
            list(loader.get_template_sources("x"))
        self.assertGreater(
            sum(reached.values()),
            0,
            "the instrument cannot see a call into the module it is pointed at, "
            "so its zero over the corpus would say nothing",
        )

        with instrumented(locmem) as counts:
            django_parse("{{ x }}")
        self.assertEqual(sum(counts.values()), 0, sorted(counts))

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
                module = importlib.import_module(f"django_rust_templates.engine.{name}")
                self.assertIn("engine", module.__file__, module.__file__)

    def test_our_package_shadows_nothing_and_djangos_siblings_are_intact(self):
        """The two packages are disjoint, and the row that said so inverted at M18.

        **This row asserted the opposite until the M18 rewiring, and it was the
        shim's claim.** ``django_rust_templates.engine.loaders`` used to be a
        finder's re-presentation of ``django.template.loaders``, so its
        ``__path__`` was built from *our* directory plus Django's, and "Django's
        is also there" was the half that broke when a milestone was half
        finished. There is no finder now, and there is no fallback: our loaders
        directory holds all five loaders and the ``__init__`` that names them, so
        nothing under it needs Django's directory to resolve. Asserted as an
        equality, because the surviving hazard is the *inverse* of the old one —
        a residue of the deleted finder would put Django's directory back on our
        ``__path__``, and a package that resolves Django's modules under our name
        is the thing M18 removed.

        The ``loaders`` package and not ``engine`` itself: both were spliced, and
        the subpackage is where a leftover splice would matter least visibly — a
        name like ``cached`` would resolve twice from two directories, ours
        first, with no test failing.

        The second half is the direction that survives the rename: **Django's own
        package is untouched.** Asked of the module objects rather than of the
        paths, because two directories named ``loaders`` compare equal by name
        and the claim is that these are two different modules. Django's
        ``loaders/__init__.py`` is empty upstream, so the name to reach for on
        that side is the submodule both trees *do* define.
        """
        import django.template.loaders.filesystem
        import django_rust_templates.engine.loaders as loaders
        import django_rust_templates.engine.loaders.filesystem as ours_filesystem

        self.assertEqual(
            [pathlib.Path(p).resolve() for p in loaders.__path__],
            [ENGINE_DIR / "loaders"],
            "our loaders package resolves from more than its own directory, "
            "which is what a surviving finder splice would look like",
        )

        theirs_filesystem = django.template.loaders.filesystem
        self.assertIsNot(ours_filesystem, theirs_filesystem)
        self.assertNotIn(
            pathlib.Path(theirs_filesystem.__file__).resolve().parent,
            [pathlib.Path(p).resolve() for p in loaders.__path__],
            "Django's loader directory is back on our package's __path__, which "
            "is what a surviving finder splice would look like",
        )
        self.assertEqual(
            ours_filesystem.Loader.__module__,
            "django_rust_templates.engine.loaders.filesystem",
        )
        self.assertEqual(
            theirs_filesystem.Loader.__module__,
            "django.template.loaders.filesystem",
        )
        self.assertIsNot(ours_filesystem.Loader, theirs_filesystem.Loader)

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
                module = importlib.import_module(f"django_rust_templates.engine.{name}")
                self.assertIsNot(module.Loader, load_pinned(relative).Loader)
                self.assertIn("engine", inspect.getfile(module.Loader))

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
        import django_rust_templates.engine.loaders.app_directories as app_directories
        import django_rust_templates.engine.loaders.filesystem as filesystem

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

        `Engine.find_template_loader` does `import_string("django_rust_templates.engine.
        loaders.filesystem.Loader")`, which is the spelling in `TEMPLATES` for
        every Django project and in Django's own loader tests. So the assertion
        that matters is not that the module is ours but that the *object the
        engine instantiated* is ours — and that it is not the pinned copy's.
        """
        from tests.pinned_module import load_pinned

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ name }}")
            engine = Engine(
                loaders=[("django_rust_templates.engine.loaders.filesystem.Loader", [tmpdir])]
            )
            loader = engine.template_loaders[0]

        import django_rust_templates.engine.loaders.filesystem as filesystem

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
        import django_rust_templates.engine.loaders.base as base
        import django_rust_templates.engine.loaders.cached as cached
        import django_rust_templates.engine.loaders.locmem as locmem

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ a }}")

            filesystem_engine = Engine(
                loaders=[("django_rust_templates.engine.loaders.filesystem.Loader", [tmpdir])]
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
                        "django_rust_templates.engine.loaders.cached.Loader",
                        [("django_rust_templates.engine.loaders.filesystem.Loader", [tmpdir])],
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
                    ("django_rust_templates.engine.loaders.locmem.Loader", {"index.html": "{{ a }}"})
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
        from django_rust_templates.engine import TemplateDoesNotExist

        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "index.html").write_text("{{ a }}")
            engine = Engine(
                loaders=[("django_rust_templates.engine.loaders.app_directories.Loader", [tmpdir])]
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
    ``IncludeNode.render``'s cache bypassed in ``engine/loader_tags.py`` — the
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

    def test_loader_tags_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django_rust_templates.engine.loader_tags")
        self.assertIn("engine", module.__file__, module.__file__)

    def test_the_classes_this_module_defines_are_not_djangos(self):
        """The classes with the file as their ``co_filename``, and the one it imports.

        ``load_pinned`` executes the pinned file *fresh*, so ``BlockNode``,
        ``ExtendsNode`` and ``IncludeNode`` are new class objects and the
        comparison is between two real things rather than a name and itself.

        ``Node`` is deliberately **not** compared that way, and the reason is the
        interposition working as intended: the pinned file's
        ``from django_rust_templates.engine.base import Node`` resolves through the shim to
        *our* ``base``, so the pinned module's ``Node`` is ours too. Asserting
        ``is not`` there would fail on a correct tree. What is asserted instead
        is the identity that matters — the two modules name one class — because
        that is what makes ``isinstance`` against either true for every node the
        tree holds.
        """
        import importlib
        import inspect

        from django_rust_templates.engine import base, loader_tags

        from tests.pinned_module import load_pinned

        pinned = load_pinned("loader_tags.py")
        for name in ("BlockNode", "ExtendsNode", "IncludeNode"):
            with self.subTest(name=name):
                ours = getattr(loader_tags, name)
                self.assertIsNot(ours, getattr(pinned, name))
                self.assertIn("engine", inspect.getfile(ours))

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

        from django_rust_templates.engine import loader_tags
        from django_rust_templates.render import parse_template

        engine = Engine(
            loaders=[
                ("django_rust_templates.engine.loaders.locmem.Loader", {"n": "{% include \"m\" %}"})
            ]
        )
        template = parse_template('{% include "n" %}', engine)
        includes = template.nodelist.get_nodes_by_type(loader_tags.IncludeNode)
        self.assertEqual(len(includes), 1)
        self.assertIs(type(includes[0]), loader_tags.IncludeNode)
        self.assertIn("engine", inspect.getfile(type(includes[0])))


class CollisionTests(unittest.TestCase):
    """Which ``engine`` files wear a real module's name, and each one's warrant.

    The rule — a file under ``engine`` may take a real module's name only when it
    implements that module — is as old as the directory, and it is the rule the
    filesystem answers: `shadowing_files()` is every file whose ``engine``-relative
    path Django's tree also has, asked of the two trees rather than read from a
    list.

    **The name of this class was ``ShadowingTests`` and the name was the only
    thing the mechanism decided.** Nothing is shadowed now: ``engine/base.py`` is
    ``django_rust_templates.engine.base`` and Django's ``django.template.base`` is
    untouched, so these files no longer stand in front of anything — they
    *collide* with a name, which is a fact about two directories and not about an
    import. The rule survives the rename unchanged, because it was always about
    the collision; what did not survive is the table.

    **The ``kind`` column is gone, and its deletion is this class's one
    improvement.** ``_shim.SHADOWING`` carried ``"transcribed"``/``"fresh"`` beside
    each path, and the row below compared each against the guard — a second
    account of a question ``check_transcriptions.py`` already answers, held in
    step by an assertion. It is not held in step any more; it is not written down.
    A file is transcribed exactly when the guard declares it and fresh exactly
    when ``tests/test_transcription_equivalence.py`` states an equivalence for it,
    and those two tables are now the only writers of the distinction. A file in
    neither is the failure the row is for, and it has nowhere to hide: there is no
    column left to edit.
    """

    def test_every_colliding_file_has_a_warrant(self):
        """Set equality against the two warrant tables, in both directions.

        A file on disk that neither table carries is a transcription nothing
        checks against the pin — and the guard's own diff is the only instrument
        that falsifies one (M9's finding 3: a verbatim copy and a wrongly
        transcribed file both keep all 1547 tests green). A key with no file is
        the opposite error, a claim about nothing that the next reader takes for a
        measurement; **``utils.py`` was exactly that**, declared in
        ``check_transcriptions.py`` and deleted from the tree in ``03038e6``, and
        this row is written so that the deletion cannot be recorded by leaving the
        key behind.

        The two warrant tables carry different obligations and both are required:
        the guard's diff against the pin for a transcription, and a stated
        equivalence with rows for a fresh file. A fresh file has no diff of its
        own, so nothing in the guard can see it and the suite cannot either — for
        the same reason. Without that arm, ``fresh`` is the free option: one word
        moves a file out of every guard in the tree with everything green.
        """
        import importlib

        import scripts.check_transcriptions as guard

        from tests import test_transcription_equivalence as equivalence
        from tests.milestone_ownership import shadowing_files

        on_disk = set(shadowing_files())
        warrants = set(guard.DECLARED) | set(equivalence.EQUIVALENCE)
        self.assertEqual(
            on_disk, warrants, "a colliding file and its warrant disagree"
        )
        # The control, drawn from the same population: an empty answer here would
        # make the two assertions above agree about nothing at all.
        self.assertGreater(len(on_disk), 15, sorted(on_disk))

        for name in sorted(equivalence.EQUIVALENCE):
            with self.subTest(name=name):
                self.assertNotIn(
                    name,
                    guard.DECLARED,
                    f"{name} is both declared and stated as an equivalence; the "
                    "kind is read off these two tables, so a name in both makes "
                    "it ambiguous rather than doubly warranted",
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
        """A render that resolves a variable resolves it in Rust, not in Python.

        The meter and the file check above say `base` is ours; neither says the
        *lookup* is. A transcription that kept upstream's loop would pass both,
        which is finding 3's shape applied to this method.

        **Three trees, and the third is why the first two mean anything.** Until
        the walk landed every render was Python's: the shell looped the nodes and
        a variable's lookup crossed to `_native.resolve_lookups`, which is what
        the old row counted. `{{ a.b }}` is now an `Act::Emit`, and since M19 step
        3a so is `{{ a.b|upper }}` — the walk owns filtered expressions — so both
        render with no Python node at all. The honest assertion for them is the
        *stronger* one: the seam is not reached, because the lookup never leaves
        Rust.

        **But an absent meter reads the same whether the lookup stopped crossing
        or the meter stopped working**, so the row carries a positive control
        drawn from the same population: `{% if a.b == 1 %}` resolves `a.b` on the
        Python path, because `plan_if` takes `TemplateLiteral` conditions only and
        an operator tree — thirteen operators deep — declines the whole node to
        `Act::Delegate`. That tree must show the crossing the other two do not.
        Note *delegated* and not *declined*: the plan survives and
        `_native_plan is None` is reserved for `plan_list` rejecting the
        template, a different failure an earlier draft of this docstring sent a
        reader looking for.

        **And it must be the components, not a re-joined path.** Until M19 the
        shell called `_native.resolve(".".join(self.lookups), context)`, which is
        one `parse_variable` per variable — 0.25 µs of a 0.74 µs resolve at the
        depth this corpus uses — and, worse, a re-*classification*: `"2."` parses
        back as a number, which is how `{{ 2. }}` rendered `2.0`. So the control
        asserts **both** halves: it reaches the seam, and it reaches it as
        components. Without the second, a revert to the join passes the first —
        `resolve` still reaches Rust, still returns the right value for `a.b`,
        and the cost and the divergence come back silently.
        """
        from django_rust_templates.engine import Context, Engine

        from django_rust_templates import _native

        components = []
        paths = []
        original_lookups = _native.resolve_lookups
        original_resolve = _native.resolve

        def counting_lookups(lookups, context):
            components.append(list(lookups))
            return original_lookups(lookups, context)

        def counting_resolve(path, context):
            paths.append(path)
            return original_resolve(path, context)

        def render(source, data):
            """One tree, meters emptied first so each reads as its own."""
            components.clear()
            paths.clear()
            return Engine().from_string(source).render(Context(data))

        _native.resolve_lookups = counting_lookups
        _native.resolve = counting_resolve
        try:
            walked = render("{{ a.b }}", {"a": {"b": 1}})
            self.assertEqual(walked, "1", "the plain walked tree's premise")
            self.assertEqual(
                components,
                [],
                "the `Act::Emit` tree left the lookup to Python, or the walk "
                f"stopped planning it: {components}",
            )

            filtered = render("{{ a.b|upper }}", {"a": {"b": "x"}})
            self.assertEqual(filtered, "X", "the filtered walked tree's premise")
            self.assertEqual(
                components,
                [],
                "a filtered `{{ }}` is the walk's node since step 3a, so this "
                "tree crossing the seam means `upper`'s handle is not routed "
                f"and the node fell back to Python: {components}",
            )

            delegated = render("{% if a.b == 1 %}y{% endif %}", {"a": {"b": 1}})
            self.assertEqual(delegated, "y", "the control tree's premise")
            self.assertEqual(
                components,
                [["a", "b"]],
                "the control did not cross the seam either, and that is what "
                "makes the two empty rows above assertions rather than "
                "silence: an operator condition is `Act::Delegate` by design "
                "and must resolve `a.b` in Python",
            )
            # The control is the last render and the only one that can push a
            # path, so this asserts on the row that would revert to the join.
            self.assertEqual(
                paths,
                [],
                "the render path re-joined the lookup into a path and re-parsed "
                f"it: {paths}",
            )
        finally:
            _native.resolve_lookups = original_lookups
            _native.resolve = original_resolve

    def test_the_failing_bit_is_passed_to_the_logger(self):
        """The bit `{{ author.name }}` fails on, not the bit it ends on.

        This is the whole reason `LookupOutcome::Escaped` grew a payload, and
        the path makes it a real distinction: `{{ a.b.c }}` over a context whose
        `b` raises logs `b`, while the *path's* last bit is `c`. The plan's
        recon named its two `test_logging` failures as one defect; the silent
        half is the third row below.
        """
        from django_rust_templates.engine import Context, Engine

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

        from django_rust_templates.engine import Context, Engine

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
    render a template that applies a built-in filter and assert the application
    happened **in Rust**. Since M19 step 3a that takes two meters and not one —
    `_native.filter` says the call crossed from Python, `Census::filters` says the
    walk applied it itself — because the fast route has `crossed == []` and so
    does the broken one (a handle missing from `django_own_filters()` declines the
    *node*, and the Python arm then answers from the transcription). And since the
    same step there is a **third**, because the pair is still true of a filter
    whose Rust body is Python: `Census::body_crossings` counts the Python the body
    itself ran. `applied == [("escape", 1)]` with `crossed == []` is the reviewer's
    case and both numbers are honest there — the escaping happened in Python. The
    three are described on `_render_with_both_meters` below, and the site list
    behind the third is gated by `FilterCrossingCoverageTests` at the end of this
    module.

    The premise is asserted separately below, because a row like this passes for a
    *true* reason and a false one — if the module were still Django's, the counter
    would read zero and the row would fail, which is right; but if it passed while
    `defaultfilters` was Django's, that would be M9's finding 3 and the row would
    be measuring nothing.
    """

    def _render_with_both_meters(self, engine, source, data):
        """Render one template and report **all three** filter meters.

        `crossed` is `_native.filter`'s count — applications that went from
        Python into Rust — `applied` is the walk's own count of the applications
        **Rust made without crossing the seam** (`Census::filters`), and
        `body_crossings` is the Python that ran **inside** those bodies.

        **The pair is the row, and one alone is not.** Since M19 step 3a a
        filtered `{{ … }}` is the walk's node whenever its handle is routed, so
        the fast route reads `applied=[(name, 1)]` with `crossed == []`. A handle
        missing from `django_own_filters()` declines the *node* rather than the
        filter, and the Python arm that follows answers from the **transcription**
        — so it reads `crossed == []` too, and the old single meter would have
        gone on reporting success while the body reached was Python's. That is
        exactly M11's finding 3, and `Census::filters` is the instrument that
        still sees it.

        **And the pair is still not enough, which is what the third meter is.**
        `applied` says the walk *routed* the filter to Rust's registry; it does
        not say Rust computed the answer, because a Rust body may call back into
        Python — `escape`'s body *is* `conditional_escape`, and `lower`'s is
        Python's `str.lower`. So `applied=[("escape", 1)]` with `crossed == []`
        is true while the escaping ran in Python, and a row that read it as "Rust
        escaped it" would be M11's finding one level further in.
        `body_crossings` counts the host crossings made between the two reads
        that bracket `(entry.run)(...)` in `run_filter_inner`, so it is a
        statement about the body and not about the wrapper around it.
        """
        from django_rust_templates import _native
        from django_rust_templates.engine import Context

        crossed = []
        original = _native.filter

        def counting(*args, **kwargs):
            crossed.append(args[0])
            return original(*args, **kwargs)

        _native.filter = counting
        _native.census_start()
        try:
            rendered = engine.from_string(source).render(Context(data))
        finally:
            # In the `finally` so a raising render cannot leave the census on:
            # it is a process-global, and the next module's renders would write
            # into this row's counts.
            _, _, _, applied, body_crossings = _native.census_take()
            _native.filter = original
        return rendered, crossed, applied, body_crossings

    def test_a_built_in_filter_reaches_rust(self):
        from django_rust_templates.engine import Engine

        rendered, crossed, applied, body_crossings = self._render_with_both_meters(
            Engine(), "{{ x|lower }}", {"x": "ABC"}
        )

        self.assertEqual(rendered, "abc")
        self.assertEqual(
            applied,
            [("lower", 1)],
            "the walk did not apply `lower` itself: a handle missing from "
            "`django_own_filters()` declines the node, and the Python arm then "
            "answers from the transcription — green, and not Rust's",
        )
        self.assertEqual(
            crossed, [], "the filter was applied on the Python path as well"
        )
        self.assertGreaterEqual(
            body_crossings,
            1,
            "`lower`'s body reached no Python at all, so the third meter is "
            "not counting the body: Rust's `str_lower` is `call_text`, which "
            "calls `str.lower` on a Python str. Zero here means a bump site "
            "is missing (43 of them, one per host method that crosses), and "
            "it is the row `FilterCrossingCoverageTests` is the source-level "
            "half of",
        )

    def test_a_body_that_is_rusts_alone_reads_no_crossing(self):
        """`upper` is the counterpart, and it is why the count is per input.

        Its body is `to_str` — pure for a `Value::Str` — plus a Rust table, so
        the same `applied`/`crossed` pair that reads at least one crossing for
        `lower` reads **zero** here. Without a row asserting zero, a meter that
        counted the *wrapper* instead of the body — the seam's own `classify`,
        the `@stringfilter` coercion, `mark_safe` — would look identical on
        both rows and would still be measuring nothing about the body.
        """
        from django_rust_templates.engine import Engine

        rendered, crossed, applied, body_crossings = self._render_with_both_meters(
            Engine(), "{{ x|upper }}", {"x": "abc"}
        )

        self.assertEqual(rendered, "ABC")
        self.assertEqual(applied, [("upper", 1)], "the walk did not apply `upper`")
        self.assertEqual(crossed, [], "`upper` was applied from Python too")
        self.assertEqual(
            body_crossings,
            0,
            "`upper` on a str is `to_str` (pure for `Value::Str`) plus a Rust "
            "table, so its body enters Python nowhere. A non-zero count here "
            "means the meter is counting the seam's own work — `classify`, the "
            "`@stringfilter` coercion, `mark_safe` — which brackets the body "
            "rather than being in it",
        )

    def test_the_escape_row_is_the_one_the_pair_is_blind_to(self):
        """The reviewer's exact case, as a row: `applied` and `crossed` both lie here.

        `{{ x|escape }}` under autoescape reads `applied == [("escape", 1)]` and
        `crossed == []`, and both are true while the escaping ran in Python —
        Rust's body for `escape` **is** `django.utils.html.conditional_escape`.
        So the row is the pair's counterexample and the third meter's reason to
        exist, and it asserts all three numbers rather than only the third: the
        first two must go on being true, or this row stops being about a blind
        spot and becomes about a routing bug.
        """
        from django_rust_templates.engine import Engine

        rendered, crossed, applied, body_crossings = self._render_with_both_meters(
            Engine(), "{{ x|escape }}", {"x": "<b>"}
        )

        self.assertEqual(rendered, "&lt;b&gt;")
        self.assertEqual(applied, [("escape", 1)], "the walk did not apply `escape`")
        self.assertEqual(crossed, [], "`escape` was applied from Python too")
        self.assertGreaterEqual(
            body_crossings,
            1,
            "`escape`'s body is `conditional_escape`, a Python function: zero "
            "crossings means the body did not run, or the count is not the "
            "body's",
        )

    def test_a_bare_number_argument_reaches_the_walk(self):
        """`|floatformat:2` — a *number* is a `var_arg`, and the walk carries it.

        `var_chars` includes digits (`base.py:714`), so a bare number takes the
        `lookup=True` alternative — `args.append((True, Variable("2")))`
        (`base.py:770`) — and `Variable.__init__` then makes it a **literal**:
        `self.literal = int("2")` with `lookups` left `None` (`base.py:887-895`),
        which `resolve` returns without touching the context (`base.py:929-931`).
        The planner read that `None` as a variable it could not resolve and handed
        the node back: the right answer, no failure anywhere, and the corpus row
        named for filter arguments reporting coverage the walk did not have —
        the reviewer's finding, measured in
        `.review/m19-measurements/filter_arg_route.py`, where the corpus's only
        two filter arguments are both this shape.
        """
        from django_rust_templates.engine import Engine

        rendered, crossed, applied, _ = self._render_with_both_meters(
            Engine(), "{{ x|floatformat:2 }}", {"x": 1.2345}
        )

        self.assertEqual(rendered, "1.23")
        self.assertEqual(
            applied,
            [("floatformat", 1)],
            "the walk did not apply `floatformat` itself: a `lookup=True` "
            "argument whose `lookups` is `None` is a number, not a variable it "
            "cannot resolve",
        )
        self.assertEqual(crossed, [], "the filter was applied from Python too")

    def test_the_number_reaches_the_body_as_a_number(self):
        """The literal is Django's `int`, not its text — the mistake to guard.

        The plausible wrong fix is to take the constant arm's `mark_safe`, and
        that arm is not merely redundant here: `mark_safe(2)` does not wrap a
        non-string, it *stringifies* — `SafeString(2)` is `"2"`. Two filters
        tell the two apart, and they fail differently on purpose:

          * `floatformat` **casts**: `int(2.5)` is 2 and `int("2.5")` raises
            `ValueError`, so the argument written three ways gives Django's three
            answers;
          * `add` raises a **`TypeError` inside the body**: with `x="a"`,
            `int("a")` fails either way and the two disagree only because
            `"a" + 2` is a `TypeError` and `"a" + "2"` is a string.

        The second is the sharper of the two — a later "helpful" coercion of the
        argument on the way in would silence the first and leave this one still
        discriminating.
        """
        from django_rust_templates.engine import Engine

        cases = [
            ("{{ x|floatformat:2.5 }}", {"x": 1.2345}, "1.23",
             "a number: `int(2.5)` is 2"),
            ('{{ x|floatformat:"2.5" }}', {"x": 1.2345}, "1.2345",
             "a quoted constant: `floatformat` takes its `int()` failure path"),
            ("{{ x|add:2 }}", {"x": "a"}, "",
             'a number: `"a" + 2` is a `TypeError`, which `add` answers `""`'),
            ('{{ x|add:"2" }}', {"x": "a"}, "a2",
             'a quoted constant: `"a" + "2"` is a string'),
        ]
        for template, context, expected, why in cases:
            with self.subTest(template=template):
                name = template.split("|")[1].split(":")[0]
                rendered, crossed, applied, _ = self._render_with_both_meters(
                    Engine(), template, context
                )
                self.assertEqual(rendered, expected, why)
                self.assertEqual(applied, [(name, 1)])
                self.assertEqual(crossed, [])

    def test_a_number_argument_lets_the_loop_seal(self):
        """The consequence that is bigger than the one node.

        A loop body seals only if **every** node in it is walk-rendered, so one
        bare number in one filter argument used to hand the whole `{% for %}` —
        and the table it builds — back to Python. The probe's loop row read
        `delegated: ForNode=1` before this and reads `handled` after, and the
        assertion here is the meter's own form of that: `applied` counts the
        walk's applications over the iterations, and a delegated loop contributes
        nothing to it because its render is Python's.
        """
        from django_rust_templates.engine import Engine

        rendered, crossed, applied, _ = self._render_with_both_meters(
            Engine(),
            "{% for r in rows %}{{ r|floatformat:2 }}{% endfor %}",
            {"rows": [1.2345, 2.3456]},
        )

        self.assertEqual(rendered, "1.232.35")
        self.assertEqual(
            applied,
            [("floatformat", 2)],
            "the walk applied `floatformat` twice, once per iteration: two "
            "applications is the seal, and a loop Python rendered reads as an "
            "empty `applied`",
        )
        self.assertEqual(crossed, [], "the filter was applied from Python too")

    def test_a_loop_whose_body_can_write_the_context_still_seals(self):
        """The seal decision, asserted where bytes cannot see it.

        A body holding a delegate is still the walk's loop — M19 step 5 slice 2 —
        and that delegate is what makes the loop write Django's per-iteration
        state, because a node the walk does not model reads the context. What has
        changed in the third round is *why* that is now true of **every** loop
        rather than of a body shape the plan could decide: the walk writes the
        state unconditionally, so the delegate's `{% cycle "X" as s silent %}`
        (`context.set_upward`, `CycleNode.render`) and this loop's own write of
        `s` are the same write. See
        `test_every_read_of_the_loop_variable_reaches_the_context` for the row
        that pins the deletion of the frame route.

        So there are two ways to make the rebinding rows in
        `tests/differential/cases.py` pass: keep the reads on the context, or
        hand the loop back to Python. **Only the first is the port.** A
        re-delegated loop renders Django's bytes and reads `delegated:
        ForNode=1` here, which is why this row exists beside them and why its
        assertion is the census rather than the output.
        """
        from django_rust_templates import _native
        from django_rust_templates.engine import Context, Engine

        source = '{% for s in sections %}{% cycle "X" as s silent %}{{ s }}{% endfor %}'
        _native.census_start()
        try:
            rendered = Engine().from_string(source).render(Context({"sections": ["old"]}))
        finally:
            # In the `finally` for the reason `_render_with_both_meters` gives:
            # the census is a process-global and a raising render must not leave
            # it on for the next module's rows.
            handled, delegated, _, _, _ = _native.census_take()

        self.assertEqual(rendered, "X")
        self.assertIn(
            ("ForNode", 1),
            handled,
            "the walk did not run the loop: `ForNode` is missing from `handled`, "
            "so `ForNode.render` was the one that rendered it",
        )
        self.assertNotIn(
            ("ForNode", 1),
            delegated,
            "the loop was handed back to Python. The rebinding rows pass either "
            "way, so this is the assertion that separates the fix from a "
            "re-delegation",
        )
        self.assertEqual(
            delegated,
            [("CycleNode", 1)],
            "`{% cycle %}` is the delegate this row is about; if it stopped "
            "being delegated the body would be pure, which is a different test",
        )

    def test_a_loop_whose_sequence_can_write_the_context_still_seals(self):
        """The second adversarial round's first finding, at the census.

        `ForNode.render` opens `with context.push():` **before**
        `values = self.sequence.resolve(context, ignore_failures=True)`
        (`engine/defaulttags.py:198-199`), and a sequence is a
        `FilterExpression`, which hands its filter the arguments the template
        gave it — the context is one of them, when the template names it. So a
        **nested** loop's sequence can write its *ancestor's* loop variable, and
        a read of that variable after the inner loop must see the write. The old
        body-wide predicate (`may_write_context`) had argued exactly that away —
        "a sequence is read, not written" — and seven rows in
        `tests/differential/cases.py` diverged on the `35ff15c` build because of
        it (`differential-fix2.txt`: 7 of 59). The ordered pass that replaced it
        stopped rewriting reads at an `Act::For` as well as at a delegate; the
        third round deleted the rewriting altogether, so this row and its
        siblings no longer depend on where a boundary is drawn.

        This row is the sibling of the one above for the same reason that one
        exists: the rows in the corpus pass if the loop is *re-delegated*, and
        the census is what separates the port from that. Both loops are the
        walk's here, and the only delegated node is the `{% load %}`.
        """
        from django_rust_templates import _native
        from django_rust_templates.engine import Context, Engine

        libraries = {"ctxwrite": "tests.differential.ctxwrite_library"}
        source = (
            "{% load ctxwrite %}{% for s in sections %}"
            "{% for x in xs|rebind_up:ctx %}{% endfor %}{{ s }}{% endfor %}"
        )
        engine = Engine(libraries=libraries)
        context = Context({"sections": ["old"], "xs": []})
        context["ctx"] = context

        _native.census_start()
        try:
            rendered = engine.from_string(source).render(context)
        finally:
            handled, delegated, _, _, _ = _native.census_take()

        self.assertEqual(
            rendered,
            "X",
            "`rebind_up` writes `s` upward into the outer loop's layer, so the "
            "read after the inner loop must see it — `differential-fix2.txt` "
            "reads `old` for this row on the pre-fix builds",
        )
        self.assertIn(
            ("ForNode", 2),
            handled,
            "both loops must be the walk's: a `ForNode` missing from `handled` "
            "is a loop `ForNode.render` rendered",
        )
        for count in (1, 2):
            self.assertNotIn(
                ("ForNode", count),
                delegated,
                "a loop was handed back to Python. Both corpus rows and this "
                "row's output pass that way, which is why the assertion is the "
                "census",
            )
        self.assertEqual(
            delegated,
            [("LoadNode", 1)],
            "`{% load %}` is the only node here the walk does not model — every "
            "other node is the walk's own, so a `ForNode` in this list would be "
            "a loop handed back to Python",
        )

    def test_every_read_of_the_loop_variable_reaches_the_context(self):
        """The third round's fix, counted — the claim bytes cannot carry.

        A loop body used to be *sealed*: the walk elided Django's per-iteration
        state and served the body's reads of the loop variable from a Rust frame,
        and *where* a read was allowed to stay on the frame was the argument that
        failed three adversarial rounds — a body-wide predicate, then an ordered
        pass with a boundary set of `{Act::Delegate, Act::For(_)}`, then this.
        None of it was fixable by widening the set, because the thing that moves
        the name is a *runtime value* holding the context (a property, a
        `__str__`, a `__bool__`), which no plan can see.

        **So the frame route is gone**, and this row is its negative: every read
        of the loop variable is a context read, on every build. A **frame** read
        was answered in Rust and never touched `Context.__getitem__`, so a
        counter on the context separates the two routes:
        `{% for s in sections %}{{ title }}{{ s }}{% cycle "X" as s silent %}{{ s }}{% endfor %}`
        reads `s` once before the delegate and once after, and **both** now go to
        the context. The subject is two.

        `title` is the control, drawn from the same render and read by nothing
        but the emit: it must reach the context on every build, so a 0 there
        means the instrument never saw the render and the `s` count below would
        be vacuous. `sections` is deliberately *not* the control — its first read
        is the loop's *sequence*, which Python resolves, so its count moves with
        the sequence's route rather than with the body's.
        """
        from django_rust_templates.engine import Context, Engine

        class CountingContext(Context):
            """A `Context` that records every key read out of it."""

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.reads = []

            def __getitem__(self, key):
                self.reads.append(key)
                return super().__getitem__(key)

        source = (
            "{% for s in sections %}{{ title }}{{ s }}"
            '{% cycle "X" as s silent %}{{ s }}{% endfor %}'
        )
        context = CountingContext({"sections": ["old"], "title": "T"})
        rendered = Engine().from_string(source).render(context)

        self.assertEqual(
            rendered,
            "ToldX",
            "the read after `{% cycle %}` must see the rebound name — `X` — and "
            "the read before it the item, which is still `old`",
        )
        self.assertEqual(
            context.reads.count("title"),
            1,
            "the control: `{{ title }}` is read by the emit and by nothing else, "
            "so its count is one on every build. A 0 here means this instrument "
            "never saw the render, and the `s` count below would be vacuous",
        )
        self.assertEqual(
            context.reads.count("s"),
            2,
            "a read of the loop variable did not reach the context, which can "
            "only mean a frame route came back. Both reads are before or after "
            "`{% cycle %}` writes `s` through `context.set_upward`, so a frame "
            "answer for either is a wrong answer the moment any node in the body "
            "can write the name",
        )

    def test_a_value_side_writer_inside_an_emit_is_visible_to_a_later_read(self):
        """The third round's first BLOCKING finding, at the render.

        `{{ holder.s }}` is an `Act::Emit`, and its head resolves a *property* —
        Python that can hold the context and write the loop variable. The ordered
        pass at `f79f7c7` treated an emit as no boundary at all (its set was
        `{Act::Delegate, Act::For(_)}`), so `{{ s }}` after it was answered from
        the frame and the row rendered `Zold` where stock renders `ZX` — a
        regression against the gate before it, which rendered `ZX`.

        The late `{% firstof "Q" as ignored %}` is the reviewer's and it is
        load-bearing: `firstof` with `as` is a node the walk does not plan, so
        the body holds a delegate *after* the writer, which is the shape the
        ordered pass rewrote around. The same row without it would pass on the
        bug, because a body with no delegate was never rewritten.
        """
        from django_rust_templates.engine import Context, Engine

        class Holder:
            def __init__(self, context):
                self.context = context

            @property
            def s(self):
                self.context.set_upward("s", "X")
                return "Z"

            def __bool__(self):
                self.context.set_upward("s", "X")
                return True

        context = Context({"sections": ["old"]})
        context["holder"] = Holder(context)
        rendered = (
            Engine()
            .from_string(
                '{% for s in sections %}{{ holder.s }}{{ s }}'
                '{% firstof "Q" as ignored %}{% endfor %}'
            )
            .render(context)
        )

        self.assertEqual(
            rendered,
            "ZX",
            "the property wrote `s` = `X` before `{{ s }}` was resolved, so the "
            "read must see `X`. `Zold` is the pre-fix answer: the read came from "
            "a frame the write could not reach",
        )

    def test_a_value_side_writer_inside_an_if_condition_is_visible_to_a_later_read(
        self,
    ):
        """The third round's second BLOCKING finding, at the render.

        The sibling of the row above, one position over. An owned `{% if %}`
        condition is the same Python chain as an emit's head **plus a truth
        test** (`__bool__` / `__len__`), so a `Holder.__bool__` is a writer the
        ordered pass could not name either — condition reads were not a boundary
        — and the row rendered `old` where stock renders `X`. Widening the set to
        "every Python-capable `Act::Emit`" would not have covered it, which is
        why the fix is the deletion of the frame route rather than a wider
        boundary.
        """
        from django_rust_templates.engine import Context, Engine

        class Holder:
            def __init__(self, context):
                self.context = context

            def __bool__(self):
                self.context.set_upward("s", "X")
                return True

        context = Context({"sections": ["old"]})
        context["holder"] = Holder(context)
        rendered = (
            Engine()
            .from_string(
                "{% for s in sections %}{% if holder %}{{ s }}{% endif %}"
                '{% firstof "Q" as ignored %}{% endfor %}'
            )
            .render(context)
        )

        self.assertEqual(
            rendered,
            "X",
            "the condition wrote `s` = `X` before the branch ran, so `{{ s }}` "
            "inside it must see `X`. `old` is the pre-fix answer: the read came "
            "from a frame the condition's write could not reach",
        )

    def test_the_reverse_counters_go_negative_when_the_sequence_outgrows_it_len(self):
        """The fourth round's BLOCKING finding, at the render.

        `{% for %}` writes `revcounter = len_values - i` and
        `revcounter0 = len_values - i - 1` (`engine/defaulttags.py:223-224`) in
        **Python integers**, and `len_values` is read once, before the first
        iteration. Nothing makes the sequence stop at that length, so both
        counters go negative as soon as it yields past it — the reviewers' route
        is an ordinary list whose element appends to it while `{{ s }}` renders
        its `__str__`, and the sibling below is an object whose `__len__` simply
        disagrees with its `__iter__`. The walk computed both in `usize`, so it
        wrapped to `18446744073709551615` instead, and the deletion of the frame
        route is what exposed it: a body that reads `forloop.revcounter` was not
        sealable, so before this change such a loop was handed to Python and
        Django's own arithmetic ran.

        The row asserts stock's answer **and** the literal, because a row that
        only compares the two engines passes when both are wrong, and this is a
        case where "both are wrong" is what a reader would have to rule out.
        """
        from django.template.engine import Engine as StockEngine
        from django.template.context import Context as StockContext

        from django_rust_templates.engine import Context, Engine

        class Grow:
            """Renders to `a`, and appends `b` to the list it is being read from."""

            def __init__(self, xs):
                self.xs = xs

            def __str__(self):
                self.xs.append("b")
                return "a"

        source = (
            "{% for s in xs %}{{ s }}:{{ forloop.revcounter }}/"
            "{{ forloop.revcounter0 }};{% endfor %}"
        )

        def grew(engine, context):
            xs = []
            xs.append(Grow(xs))
            return engine().from_string(source).render(context({"xs": xs}))

        stock = grew(StockEngine, StockContext)
        our = grew(Engine, Context)

        self.assertEqual(
            stock,
            "a:1/0;b:0/-1;",
            "the expectation itself: `len_values` is 1 and the list yields 2, so "
            "the second iteration's `revcounter` is 0 and its `revcounter0` is "
            "-1. A stock answer that is not this means the fixture no longer "
            "grows the list, and the comparison below would be vacuous",
        )
        self.assertEqual(
            our,
            stock,
            "the walk wrapped the unsigned subtraction instead of going negative: "
            "`18446744073709551615` is `0usize - 1`, and Django gives `-1`",
        )

    def test_the_reverse_counters_match_a_sequence_whose_len_disagrees_with_its_iter(
        self,
    ):
        """The sibling: no `__str__` side effect, just a lying `__len__`.

        The row above needs a body that mutates the sequence, which is a lot of
        machinery for an arithmetic bug — and the reviewer asked for this shape
        separately. Here nothing is mutated at all: the object advertises one
        item and yields three, so the counters are `1/0`, `0/-1`, `-1/-2` and the
        walk must print the same. Under the unsigned arithmetic the third
        iteration alone differs in both counters.
        """
        from django.template.engine import Engine as StockEngine
        from django.template.context import Context as StockContext

        from django_rust_templates.engine import Context, Engine

        class Over:
            def __len__(self):
                return 1

            def __iter__(self):
                return iter(["x", "y", "z"])

        source = (
            "{% for s in xs %}{{ s }}:{{ forloop.revcounter }}/"
            "{{ forloop.revcounter0 }};{% endfor %}"
        )
        stock = StockEngine().from_string(source).render(StockContext({"xs": Over()}))
        our = Engine().from_string(source).render(Context({"xs": Over()}))

        self.assertEqual(
            stock,
            "x:1/0;y:0/-1;z:-1/-2;",
            "the expectation itself: three iterations against a length of one",
        )
        self.assertEqual(
            our,
            stock,
            "the third iteration's counters wrapped: the walk printed "
            "`18446744073709551615/18446744073709551614` for `-1/-2`",
        )

    def test_a_non_ascii_loop_variable_name_reaches_the_context(self):
        """The loop's seventh key is a `PyString` built once, and the name it
        carries is the template's — `Ω` survives the pre-build.

        `Walk::run_loop_body` used to hand `plan.loopvar` to
        `Context.__setitem__` as a `&str`, so PyO3 built a `PyString` from it on
        every iteration; the lever pre-builds one at plan time and reuses it. The
        saving is the build, and the risk the pre-build introduces is that the
        string it carries is not the name the template wrote — a conversion made
        lossy for anything outside ASCII would be invisible in every row whose
        loop variable is `item`.

        **A guard, not a discriminator.** Measured on both artifacts — the
        pre-lever `dc174855d926defc` and the shipped `3d3e99e63eddc036` — this row
        passes on each, because both routes build the key from a real `str`. What
        it pins is that the pre-built one still carries the template's name.
        Django's own `Variable` accepts `Ω` as a name (`\\w` is Unicode-aware for
        a `str` pattern), so the row asserts stock's answer as a literal first and
        only then that ours equals it.
        """
        from django.template.engine import Engine as StockEngine
        from django.template.context import Context as StockContext

        from django_rust_templates.engine import Context, Engine

        source = "{% for Ω in xs %}{{ Ω }}:{{ forloop.counter }};{% endfor %}"
        stock = (
            StockEngine().from_string(source).render(StockContext({"xs": ["a", "b"]}))
        )
        our = Engine().from_string(source).render(Context({"xs": ["a", "b"]}))

        self.assertEqual(
            stock,
            "a:1;b:2;",
            "the expectation itself: a non-ASCII loop variable resolves, and the "
            "counters are written beside it",
        )
        self.assertEqual(
            our,
            stock,
            "the pre-built key and the template's name disagree",
        )

    def test_a_loop_named_forloop_writes_the_item_over_the_dict(self):
        """Two keys that are *equal* but not the same object, and the order that
        decides which one the body reads.

        `ForNode.render` writes `context["forloop"] = loop_dict` and then, per
        iteration, `context[loopvars[0]] = item` (`engine/defaulttags.py:248`) —
        so a loop named `forloop` has the *item* written over the dict, and the
        body reads an `int` where it might expect the state. That order is only
        observable when the two writes collide, which is this row.

        **It pins the write order, not the lever's key identity**, and an earlier
        version of this docstring said otherwise. The store is a plain `dict`
        (`BaseContext.__setitem__` — `python/django_rust_templates/engine/context.py:69`
        — is `self.dicts[-1][key] = value`) and a dict compares
        keys by equality and hash and never by identity, so the item lands in the
        same slot whichever `PyString` carries the name. Measured: this row passes
        on the pre-lever build `dc174855d926defc` as well as the shipped
        `3d3e99e63eddc036`. What it guards is that the item is still written
        *after* the dict — the lever kept that order, and a reordering would break
        it.
        """
        from django.template.engine import Engine as StockEngine
        from django.template.context import Context as StockContext

        from django_rust_templates.engine import Context, Engine

        source = "{% for forloop in xs %}{{ forloop }}:{{ forloop.counter }};{% endfor %}"
        stock = StockEngine().from_string(source).render(StockContext({"xs": [1, 2]}))
        our = Engine().from_string(source).render(Context({"xs": [1, 2]}))

        self.assertEqual(
            stock,
            "1:;2:;",
            "the expectation itself: the item is written over the dict, so "
            "`{{ forloop }}` is the item and `{{ forloop.counter }}` is the "
            "empty string — an `int` has no `.counter`",
        )
        self.assertEqual(
            our,
            stock,
            "the plan-time key did not displace the interned dict key",
        )

    def test_the_parser_finds_our_defaultfilters(self):
        """The premise, on its own, so a green counter cannot be the whole story.

        `Parser.find_filter(name)` is the handle the parse keeps
        (`python/django_rust_templates/engine/parser.py:568`) and the object the routing compares against, so
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

        defaultfilters = importlib.import_module("django_rust_templates.engine.defaultfilters")
        self.assertIn("engine", defaultfilters.__file__)

        func = parser_for("").find_filter("lower")
        self.assertIs(func, defaultfilters.register.filters["lower"])
        self.assertIn("engine", inspect.getfile(func))

    def test_every_library_filter_reaches_rust(self):
        """M12 Task 7: the nine `django.templatetags` filters route to Rust.

        The same **pair of meters** the built-in row above uses, widened to the
        library filters — and it is the row the milestone needs rather than a
        repetition, because these nine are the ones whose *routing* could be
        missing while everything looked right. A library filter's handle is only
        in `django_own_filters()` once the merge carries the five registries, and
        the failure mode of a missing handle is not an error: it is the Python
        loop running the **transcribed** body, which produces the right answer,
        with `_native.filter` never reached. Green, and not Rust's. So each case
        asserts the walk applied the filter itself, with the name it was handed,
        **and** that no application crossed from Python.

        The render path is the real one — `{% load %}` merges the library into the
        parser (as a template would), and the token is the template's. `Engine`
        needs the library *map* because a bare `Engine()` has
        `template_libraries == {}` (measured), which is also why the parity
        corpus carries a `libraries` key; the map here is the three modules this
        task ports, since `cache`/`static` register no filters.
        """
        import datetime

        from scripts.filter_probe import ensure_settings

        from django_rust_templates.engine import Engine

        # `language_name_translated` reaches the host's `gettext`, which needs
        # settings and a populated app registry; guarded, so this supplies them
        # only if no earlier module has.
        ensure_settings()

        engine = Engine(
            libraries={
                "i18n": "django_rust_templates.engine.templatetags.i18n",
                "l10n": "django_rust_templates.engine.templatetags.l10n",
                "tz": "django_rust_templates.engine.templatetags.tz",
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

        for library, name, value, arg in cases:
            with self.subTest(filter=name):
                token = f"x|{name}" if arg is None else f'x|{name}:"{arg}"'
                source = "{% load " + library + " %}{{ " + token + " }}"
                rendered, crossed, applied, _ = self._render_with_both_meters(
                    engine, source, {"x": value}
                )
                self.assertTrue(rendered, "the premise: this pair renders something")
                self.assertEqual(
                    applied,
                    [(name, 1)],
                    f"{{{{ x|{name} }}}} did not reach Rust's body through the "
                    f"walk: a handle missing from `django_own_filters()` declines "
                    f"the node, and the Python loop then answers from the "
                    f"transcription — the right answer, and not Rust's",
                )
                self.assertEqual(
                    crossed, [], f"{{{{ x|{name} }}}} was applied from Python too"
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
        from django_rust_templates.engine import Context, Engine, Library

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
        from django_rust_templates.engine import Context, Engine

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
        from django_rust_templates.engine import Context, Engine

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

        from django_rust_templates.engine.base import FilterExpression, Parser

        from tests.pinned_module import load_pinned

        ours_registry = importlib.import_module("django_rust_templates.engine.defaultfilters").register
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
        from django_rust_templates.engine import Context, Engine

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
        import django_rust_templates.engine.defaultfilters as defaultfilters

        from django_rust_templates.engine import Context, Engine

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
            name: importlib.import_module(f"django_rust_templates.engine.templatetags.{name}").register.filters
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

        from django_rust_templates.engine import defaultfilters

        tables = [
            importlib.import_module(f"django_rust_templates.engine.templatetags.{name}").register.filters
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

        from django_rust_templates.engine import defaultfilters

        from django_rust_templates import host

        def sentinel(value):  # pragma: no cover - named, never called
            return value

        sentinel.__name__ = "lower"

        tz = importlib.import_module("django_rust_templates.engine.templatetags.tz")
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

        tz = importlib.import_module("django_rust_templates.engine.templatetags.tz")
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


class FilterCrossingCoverageTests(unittest.TestCase):
    """The site list behind `body_crossings`, held against the source that declares it.

    The third meter is only as trustworthy as its **bump sites**: 43
    `self.crossed();` calls in `crates/template-py/src/filters.rs`, one for each
    host method that reaches Python. `FilterHost` is the trait the render walk
    calls, so a method added later — the next filter or tag this project ports —
    can cross into Python with nobody remembering to bump, and every row in
    `FilterRoutingTests` would stay green: the meter is a *lower bound*, and it
    reads zero for a filter whose body crossed and was not counted. A missing
    site is invisible from the Python side, which is why this row reads the Rust
    file.

    **The rule, and it is deliberately weaker than "the body crosses":** every
    entry point of `impl FilterHost for PyFilterHost<'_>` must either contain
    `self.crossed();` or call another host method that, transitively, does. It is
    a *reachability* rule and not a sufficiency one — a method that bumps in one
    arm and reaches Python in another passes here, and that residual is what
    `FilterRoutingTests`' two rows are for (one that must read zero, one that
    must read non-zero, on the reviewer's own case). What it does catch, with no
    allowlist and no marker list, is the case that actually happens: a new host
    method that crosses and does not bump, or a bump deleted from an existing
    one.

    **Two things it must not be confused with.** The universe is the trait impl's
    own methods, not every `fn` in the file: `intern`, `classify`, `object`,
    `host_error` and `infallible_failure` are plumbing the bumped methods call,
    and bumping *there* would count crossings by method rather than per crossing
    — which is also why the recursion is over `self.` calls and not over the
    file. And a delegating method is not exempt: `to_str` is `text_of`, and
    `gettext` is `utility_text`, so the rule follows them there.
    """

    FILTERS_RS = PROJECT_ROOT / "crates" / "template-py" / "src" / "filters.rs"

    #: The trait impl whose methods the walk calls. Spelled as the reader sees it
    #: rather than matched loosely, so a rename fails here instead of silently
    #: narrowing the universe to nothing.
    TRAIT = "impl FilterHost for PyFilterHost<'_> {"

    @staticmethod
    def _methods(source):
        """Every host `fn`, by name, as the text from its signature to the next one.

        A span rule rather than a balanced-brace scan: a method's body ends where
        the next method begins, and a brace scan would have to know Rust's
        strings and comments to be right. It carries the *next* method's doc
        comment along, which is harmless here only because no comment in this
        file contains `self.crossed();` — measured, 43 occurrences and all of
        them call sites.
        """
        import re

        hits = list(re.finditer(r"\n    fn (\w+)", source))
        methods = {}
        for i, hit in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(source)
            methods[hit.group(1)] = source[hit.start() : end]
        return methods

    @classmethod
    def _entry_points(cls, source):
        import re

        start = source.index(cls.TRAIT)
        end = source.index("\n}\n", start)
        return re.findall(r"\n    fn (\w+)", source[start:end])

    @classmethod
    def _unbumped(cls, source):
        """The entry points with no route to `self.crossed();`, sorted.

        The closure is a fixpoint rather than one level because the phrasing the
        site list is written in — "directly or one delegation level down" — was
        an understatement when it was measured: `date_format` is `formats_text`
        is `date_text` is the call. What matters is reachability, not depth.
        """
        import re

        methods = cls._methods(source)
        covered = set()
        changed = True
        while changed:
            changed = False
            for name, body in methods.items():
                if name in covered:
                    continue
                callees = set(re.findall(r"self\.(\w+)\(", body))
                if "self.crossed();" in body or callees & covered:
                    covered.add(name)
                    changed = True
        return sorted(name for name in cls._entry_points(source) if name not in covered)

    def test_every_host_entry_point_reaches_a_bump(self):
        source = self.FILTERS_RS.read_text()
        offenders = self._unbumped(source)
        self.assertEqual(
            offenders,
            [],
            "these host methods reach Python and no route from them contains "
            "`self.crossed();`, so the third meter reports zero crossings for "
            "every body they run: "
            + ", ".join(offenders)
            + ". The count is a lower bound and nothing else notices — the "
            "meter's own rows are green either way",
        )

    def test_that_row_can_see_a_bump_that_is_missing(self):
        """The positive control: the rule above, on a source with one bump cut.

        `wrap` is an entry point that bumps unconditionally and calls one host
        method, `infallible_failure`, which does not — so cutting its bump leaves
        no route, and a rule that could not say so would be asserting nothing.
        The mutation is a single line, applied to a copy in memory: the file on
        disk is not touched, so a failure here can only be the rule's.
        """
        source = self.FILTERS_RS.read_text()
        signature = "    fn wrap(&self, text: &str, width: usize) -> String {\n"
        self.assertEqual(source.count(signature), 1, "the mutation's anchor moved")
        mutated = source.replace(signature + "        self.crossed();\n", signature)
        self.assertNotEqual(mutated, source, "the bump this row cuts is not there")
        self.assertEqual(
            self._unbumped(mutated),
            ["wrap"],
            "the rule did not name `wrap`, so `test_every_host_entry_point_"
            "reaches_a_bump` would not either",
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

        from django_rust_templates import host as host_module

        cls.host_module = host_module
        cls.libraries = {
            name: importlib.import_module(name).register.filters
            for name in host_module._LIBRARY_TAG_MODULES
        }


    def registries(self):
        """Every registry a template's tag and filter names resolve against.

        Tags and filters both, and both sides of the boundary: Rust's three tag
        lists and its filter list, the merged predicate `has_tag` compares
        against, and Django's own filter registry — which is where a
        `{% load %}`ed library's names live until a later milestone ports them.
        Kept as a mapping rather than a list so a failure names the registry it
        came from.
        """
        from django_rust_templates.engine import defaultfilters

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

        from django_rust_templates import host as host_module

        cls.host_module = host_module
        cls.libraries = {
            name: importlib.import_module(name).register.tags
            for name in host_module._LIBRARY_TAG_MODULES
        }


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
        from django_rust_templates.engine import defaulttags, loader_tags

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

        from django_rust_templates.engine import defaulttags

        def sentinel(parser, token):  # pragma: no cover - named, never compiled
            raise AssertionError("the sentinel is a name, not an implementation")

        sentinel.__name__ = "if"

        static = importlib.import_module("django_rust_templates.engine.templatetags.static")
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

        tz = importlib.import_module("django_rust_templates.engine.templatetags.tz")
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
        from django_rust_templates.engine import Library, Node, defaulttags

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
            self.libraries["django_rust_templates.engine.templatetags.i18n"]["trans"],
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
        from django_rust_templates.engine import Library, Node

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

    ``django_rust_templates.engine.templatetags.tz`` is the third of the milestone's five libraries, and
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

    def test_tz_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django_rust_templates.engine.templatetags.tz")
        self.assertIn("engine", module.__file__, module.__file__)

    def test_each_tz_tag_compiles_to_our_node_class(self):
        """The routing claim, end to end, for all three names.

        A real ``Engine`` with the library spelled as a dotted path, because that
        is the route ``{% load tz %}`` takes and the route the suite takes: a bare
        ``Engine()`` cannot see ``django_rust_templates.engine.templatetags.tz`` at all (measured — it
        reports ``'tz' is not a registered tag library``), so a row written
        against one would measure the probe rather than the routing. The node
        class is compared *by identity* to the one our transcribed module
        defines, which is the object ``get_nodes_by_type`` and every downstream
        ``isinstance`` will see.
        """
        import importlib
        import inspect

        tz = importlib.import_module("django_rust_templates.engine.templatetags.tz")

        engine = Engine(libraries={"tz": "django_rust_templates.engine.templatetags.tz"})
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
                self.assertIn("engine", inspect.getfile(type(nodes[0])))


class L10nInterpositionTests(unittest.TestCase):
    """M12 Task 5: ``l10n`` is our module — one tag, one node class, two filters to Task 7.

    ``django_rust_templates.engine.templatetags.l10n`` is the fourth of the milestone's five libraries
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

    def test_l10n_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django_rust_templates.engine.templatetags.l10n")
        self.assertIn("engine", module.__file__, module.__file__)

    def test_the_localize_tag_compiles_to_our_node_class(self):
        """The routing claim, end to end, for the tag and all three accepted forms.

        A real ``Engine`` with the library spelled as a dotted path — the route
        ``{% load l10n %}`` takes and the route the suite takes — for the reason
        ``TzInterpositionTests`` states: a bare ``Engine()`` cannot see
        ``django_rust_templates.engine.templatetags.l10n`` at all (measured — ``'l10n' is not a
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

        l10n = importlib.import_module("django_rust_templates.engine.templatetags.l10n")

        engine = Engine(libraries={"l10n": "django_rust_templates.engine.templatetags.l10n"})
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
                self.assertIn("engine", inspect.getfile(type(nodes[0])))
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

        l10n = importlib.import_module("django_rust_templates.engine.templatetags.l10n")
        for name in ("localize", "unlocalize"):
            with self.subTest(name=name):
                func = l10n.register.filters.get(name)
                self.assertIsNotNone(
                    func, f"{name} is not in our l10n registry at all"
                )
                self.assertIn(
                    "engine",
                    inspect.getfile(func),
                    f"{name} resolves to {inspect.getfile(func)}, not our file",
                )


class I18nInterpositionTests(unittest.TestCase):
    """M12 Task 6a: ``i18n`` is our module — eight tags, seven node classes.

    ``django_rust_templates.engine.templatetags.i18n`` is the fifth and last of the milestone's five
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

    def _engine(self):
        # A real `Engine` with the library spelled as a dotted path, because that
        # is the route `{% load i18n %}` takes. A bare `Engine()` cannot see
        # `django_rust_templates.engine.templatetags.i18n` at all (measured — `'i18n' is not a
        # registered tag library`), so a row against one would measure the probe.
        return Engine(libraries={"i18n": "django_rust_templates.engine.templatetags.i18n"})

    def test_i18n_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django_rust_templates.engine.templatetags.i18n")
        self.assertIn("engine", module.__file__, module.__file__)

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

        i18n = importlib.import_module("django_rust_templates.engine.templatetags.i18n")
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
                self.assertIn("engine", inspect.getfile(type(nodes[0])))

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine.base import Token, TokenType

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError

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
        from django_rust_templates.engine import TemplateSyntaxError
        from django_rust_templates.engine.templatetags.i18n import BlockTranslateNode

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


    def test_defaulttags_resolves_to_our_file(self):
        import importlib

        module = importlib.import_module("django_rust_templates.engine.defaulttags")
        self.assertIn("engine", module.__file__, module.__file__)

    def test_the_node_classes_this_module_defines_are_not_djangos(self):
        """Review Focus 5: the classes the corpus builds, compared by identity.

        ``load_pinned`` executes the pinned file *fresh*, so every class here is
        a new object and the comparison is between two real things. ``URLNode`` is
        in the list because Django's own ``syntax_tests/test_url.py`` imports it
        **by name** from this module — the one class in the file with a consumer
        outside it.

        ``Node`` is not compared, for ``LoaderTagsInterpositionTests``' reason:
        the pinned file's ``from django_rust_templates.engine.base import Node`` resolves
        through the shim to our ``base``, so the pinned module's ``Node`` is ours
        too and ``is not`` would fail on a correct tree.
        """
        import importlib
        import inspect

        from django_rust_templates.engine import base, defaulttags

        from tests.pinned_module import load_pinned

        pinned = load_pinned("defaulttags.py")
        for name in ("ForNode", "IfNode", "IfChangedNode", "CycleNode", "URLNode"):
            with self.subTest(name=name):
                ours = getattr(defaulttags, name)
                self.assertIsNot(ours, getattr(pinned, name))
                self.assertIn("engine", inspect.getfile(ours))

        self.assertIs(defaulttags.Node, base.Node)

    def test_a_rendered_for_holds_our_for_node(self):
        """The live half: the tree a render builds holds *our* class.

        ``isinstance`` against Django's classes is not enough on its own — the
        pinned module's bases are ours too — so this asserts the ``type`` of the
        node and the file that defines it, which is what a half-interposed module
        would fail.
        """
        import inspect

        from django_rust_templates.engine import defaulttags
        from django_rust_templates.render import parse_template

        template = parse_template("{% for i in xs %}{{ i }}{% endfor %}", Engine())
        nodes = template.nodelist.get_nodes_by_type(defaulttags.ForNode)
        self.assertEqual(len(nodes), 1)
        self.assertIs(type(nodes[0]), defaulttags.ForNode)
        self.assertIn("engine", inspect.getfile(type(nodes[0])))

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
        from django_rust_templates.engine import Context, Engine, Library, Node

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
