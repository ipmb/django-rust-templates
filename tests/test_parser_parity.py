"""M2's acceptance test: our parse tree is Django's parse tree.

Both sides are materialised into real Django nodes and compared structurally.
The target is the real `Parser` that `django.template.base` resolves to, and
`test_the_comparison_target_is_not_rust_owned` keeps that from becoming a
Rust-against-Rust comparison — see `tests/parity_target.py` for what that now
means, and why it is no longer "the target file is Django's".

Two independent traversals are compared for every node: one reading `vars()`
directly (which catches a wrong attribute *name*), and one through
`get_nodes_by_type` (which walks `child_nodelists` — the same path Django's
rendering and `ExtendsNode.__init__` use, and the one that catches children
attached under a name nothing walks).

Two things here are not the obvious spelling and both were measured against the
pinned tree rather than assumed:

* the materialiser is loaded **from its file**, not imported as
  `django_rust_templates._impl.parser`. See the note below.
* Django's parser is given `builtins=Engine().template_builtins`. A bare
  `Parser(tokens)` registers *nothing* (`base.py:518-519`: `builtins=None`
  becomes `[]`), so it raises `Invalid block tag` on `{% csrf_token %}` and
  every other built-in — which would have made the whole corpus compare
  `ours=ok` against `theirs=error`. `Engine.template_builtins` is an *instance*
  attribute (`engine.py:63`), not a class attribute, and
  `get_template_builtins` is an instance method; `Engine().template_builtins`
  is the form that resolves. This is the same trap Task 6 hit.
"""

import importlib.util
import inspect
import random
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
# `.django/tests` as well, since `LIBRARIES` names `template_tests.*` and
# `Engine(libraries=…)` resolves those lazily through `import_module`. It was
# already on `sys.path` during a full discovery run because `test_host_tags`
# inserts it and sorts earlier — so the corpus only failed when this module ran
# alone, which is exactly the kind of ordering dependence a test module should
# not have.
DJANGO_TESTS = DJANGO_CHECKOUT / "tests"

for _path in (DJANGO_CHECKOUT, DJANGO_TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from django.template.base import (  # noqa: E402
    UNKNOWN_SOURCE,
    DebugLexer,
    Lexer,
    Node,
    NodeList,
    Origin,
    Parser,
)
from django.template.base import FilterExpression, Variable  # noqa: E402
from django.template.engine import Engine  # noqa: E402
from django.template import smartif  # noqa: E402
from django.utils.safestring import SafeData  # noqa: E402

from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates.host import ShellHost  # noqa: E402
from tests.parity_target import assert_target_is_not_rust_owned  # noqa: E402
from tests.parser_corpus import (  # noqa: E402
    LIBRARIES as TEMPLATE_LIBRARIES,
    RANDOM_CASES,
    RANDOM_SEED,
    TEMPLATES,
    random_template,
)

# Loaded from its file rather than imported, for the reason spelled out in
# `tests/test_materialise.py`: `_impl/__init__.py` is the shim's
# `django.template` and imports its own siblings (`from .engine import Engine`
# …), so importing the `_impl` *package* by its ordinary name executes that file
# and fails for want of `engine`. Loading the module directly is how this test
# reaches the materialiser without installing the shim, which is what keeps the
# node classes Django's own.
_MATERIALISER_SPEC = importlib.util.spec_from_file_location(
    "django_rust_templates._impl.parser",
    PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "parser.py",
)
materialise_module = importlib.util.module_from_spec(_MATERIALISER_SPEC)
sys.modules[_MATERIALISER_SPEC.name] = materialise_module
_MATERIALISER_SPEC.loader.exec_module(materialise_module)
materialise = materialise_module.materialise

# Kept for symmetry with Task 6's module, where the same tuple describes a wire
# token from either side.
IGNORED_ATTRIBUTES = ("token", "origin")

# The libraries come from the corpus, beside the rows that name them, so the
# parity module and the error module cannot drift into two different engines.
# Reached by *path* and not by building a `Library` here, and that is load-bearing
# rather than tidy: `Engine(libraries=…)` resolves each path through `find_library`
# → `import_module(...).register`, so both sides get the **same module-level
# `Library` instance**. A `SimpleNode` holds `func` — a live function object — and
# `describe` compares it by identity, so two separately-built libraries would
# differ in `func` and the failure would read like a bug in a seam that works.
LIBRARIES = TEMPLATE_LIBRARIES


def _library_engine():
    """The engine whose `libraries` the corpus's `{% load %}` rows resolve through.

    A fresh `Engine` per call, which is safe for the reason above: the `Library`
    objects come from the modules, not from the engine.
    """
    return Engine(libraries=LIBRARIES)


def django_parse(source, debug=False):
    lexer = DebugLexer(source) if debug else Lexer(source)
    # `builtins` is not optional: a bare `Parser` knows no tags at all.
    # `libraries` is not either, as of M6's Task 5 — `{% load %}` reads
    # `parser.libraries`, which `Parser.__init__` fills from
    # `Engine.template_libraries`.
    engine = _library_engine()
    return Parser(
        lexer.tokenize(),
        engine.template_libraries,
        engine.template_builtins,
        # A real `Parser` always carries an `Origin`: `Template.compile_nodelist`
        # passes `self.origin`, which `Template.__init__` defaults to
        # `Origin(UNKNOWN_SOURCE)` (`base.py:151-154`). `origin=None` was fine
        # until M7 because nothing read it; `do_extends` does
        # (`parser.origin.template_name`, `loader_tags.py:305`), and it raises
        # `AttributeError` on `None`. The corpus never passes a template *name*,
        # so the unknown origin is the faithful default here.
        Origin(UNKNOWN_SOURCE),
    ).parse()


def filter_parser():
    """The one `Parser` the parse uses, built by the shell's own factory.

    M3's corpus contains `{{ … }}`, and every variable materialises to a
    `FilterExpression` — even `{{ x }}`, which has no filters — because Django's
    `do_variable` builds one via `compile_filter`, and
    `_materialise_filter_expression` needs `parser.find_filter` to turn each
    wire filter *name* back into a function. Without a parser it raises
    `LookupError` by design (`python/django_rust_templates/_impl/parser.py:285`),
    which is what the corpus hit the moment variables were added to it.

    **This delegates to `render.parser_for` as of M6's Task 5, rather than
    re-spelling the construction.** The two used to be separate spellings of
    `Parser(tokens, engine.template_libraries, engine.template_builtins)` and
    they drifted: `render.py`'s copy gained `libraries=` in Task 3 and this one
    did not, so the corpus graded a parser with no libraries — a `{% load %}`
    row raised `'custom' is not a registered tag library` here while the render
    path was fine. One construction site is the fix.
    """
    return render.parser_for("", _library_engine())


def ours_parse(source, debug=False):
    # **One** parser, handed to both halves — the same shape
    # `_native.compile_template` uses. It is the filter registry the parse builds each `{% if %}` operand
    # from (`compile_filter`) *and* the one materialisation turns the wire
    # filter names back into functions with, and `find_filter` reads one
    # `self.filters` dict, so two instances would be two registries where Django
    # has one.
    #
    # `compile_filter` is reached through the shell's own `compile_filter`, not
    # re-spelled here, so the function the tests exercise is the function the
    # render path uses. `check_filter` is deliberately not passed: it is a
    # different seam, and leaving it `None` keeps every earlier row's
    # filter-error ordering exactly as it was measured.
    #
    # `host` arrived with M6's Task 5. Without it no corpus row can contain a
    # tag the *host* compiles — `{% load %}` among them, and `{% load %}` is how
    # every host row reaches its library — because Rust's registry does not have
    # those names and raises `Invalid block tag` before the host is consulted.
    # Measured: `_native.parse("{% load custom %}{% no_params %}")` without a
    # host is `ParseError("Invalid block tag on line 1: 'load'. …")`.
    parser = filter_parser()
    tree = _native.parse(
        source,
        debug,
        compile_filter=materialise_module.compile_filter(parser),
        host=ShellHost(parser),
    )
    return materialise(tree, parser=parser)


def token_shape(token):
    if token is None:
        return None
    if isinstance(token, tuple):
        return token
    return (token.token_type.value, token.contents, token.lineno, token.position)


def describe(value):
    """A comparable description of a node tree, without node identity.

    `token` and `origin` are pulled out to their own key rather than dropped, so
    a node whose token is missing is distinguishable from one whose token differs.
    """
    if isinstance(value, NodeList):
        return {
            "type": "NodeList",
            "contains_nontext": value.contains_nontext,
            "nodes": [describe(node) for node in value],
        }
    if isinstance(value, Node):
        attributes = vars(value)
        return {
            "type": type(value).__name__,
            "attrs": {
                key: describe(item)
                for key, item in sorted(attributes.items())
                if key not in IGNORED_ATTRIBUTES
                and not isinstance(item, (Node, NodeList))
            },
            "nodelists": {
                key: describe(item)
                for key, item in sorted(attributes.items())
                if isinstance(item, (Node, NodeList))
            },
            "token": token_shape(getattr(value, "token", None)),
        }
    # M3's two object types. Neither defines `__eq__`, so without these arms a
    # bare `{{ x }}` compared two live objects by identity and could never be
    # equal — which is what adding variables to the corpus reported.
    if isinstance(value, FilterExpression):
        return describe_filter_expression(value)
    if isinstance(value, Variable):
        return describe_variable(value)
    if isinstance(value, SafeData):
        # `SafeString` subclasses `str`, so a value-only comparison cannot tell
        # a marked-safe literal from a plain one — and the mark is what decides
        # autoescaping at render time. Django builds a constant's value with
        # `mark_safe` (`base.py:755`, `base.py:906`), so both sides are safe
        # today and losing the mark should fail here rather than at M9.
        return {"safe_str": str(value)}
    # M5's `if`. A condition is a smartif tree, which is neither a `Node` nor
    # any of the value types above, and **nothing in smartif defines `__eq__`**
    # — so without these two arms every `{% if %}` compared two live objects by
    # identity and could never be equal, the same failure the `FilterExpression`
    # and `Variable` arms above exist for.
    if isinstance(value, smartif.Literal):
        # `TemplateLiteral` is the one `TemplateIfParser.create_var` builds; the
        # bare `Literal` is the base parser's and has no `text`. One arm for
        # both, with Django's own class name carried so the two stay
        # distinguishable in a diff, and `text` present-but-`None` for the one
        # that has none.
        return {
            "type": type(value).__name__,
            "text": getattr(value, "text", None),
            "value": describe(value.value),
        }
    if isinstance(value, smartif.TokenBase):
        # Every operator class is a local `Operator` defined inside
        # `infix`/`prefix` (`smartif.py:51`, `smartif.py:78`), so
        # `type(...).__name__` is `"Operator"` for all fourteen of them and
        # cannot tell `and` from `==`. `id` is the operator's own spelling
        # (`"and"`, `"not in"`, `">="`, …) and is the property that differs. The
        # two children are described recursively, which is what makes precedence
        # — `a and b or not c` nesting one way and not the other — a diff rather
        # than a coincidence.
        return {
            "type": "smartif.Operator",
            "id": value.id,
            "first": describe(value.first),
            "second": describe(value.second),
        }
    if isinstance(value, list):
        return [describe(item) for item in value]
    if isinstance(value, tuple):
        # `IfChangedNode._varlist` is a **tuple**, and it is the one attribute
        # whose *container type* is the contract: the constructor's `*varlist`
        # is what makes it a tuple, and rebuilding it as a list would render
        # identically and be a different node. So this arm carries a `"tuple"`
        # marker where the `list` arm deliberately carries none — the marker is
        # what makes "list where a tuple belongs" a diff instead of a
        # coincidence.
        return {"tuple": [describe(item) for item in value]}
    if isinstance(value, dict):
        # `AttrValue::Map`'s product — `WithNode.extra_context` is the first
        # dict-valued attribute any node carries, and `{% include %}`'s `with`
        # option is the second at M7. Without this arm the dict is returned as
        # it stands, and its values are live `FilterExpression`s, which define
        # no `__eq__`: two identical `{% with %}` trees compared unequal by
        # identity. That is the same failure the `FilterExpression` and
        # `Variable` arms above were added for, one container further out.
        #
        # No `"type"` marker, for the `list` arm's reason: nothing else
        # describes to a string-keyed dict, and a marker would show up in every
        # diff as noise around the keys that actually differ.
        return {key: describe(item) for key, item in value.items()}
    return value


def describe_filter_expression(expression):
    """A `FilterExpression` by value: `__slots__` is `("token", "filters", "var",
    "is_var")` and none of it is comparable on its own.

    `token` here is a **string**, not a `Token` — `do_variable` splits the
    token's contents and hands the bare string to `compile_filter`
    (`base.py:1016-1020`), so `token_shape` would raise on it. Filters are
    described by [`filter_identity`] — see it for why `__name__` was not enough.
    """
    return {
        "type": "FilterExpression",
        "token": expression.token,
        "var": describe(expression.var),
        "is_var": expression.is_var,
        "filters": [
            (filter_identity(func), [(lookup, describe(arg)) for lookup, arg in args])
            for func, args in expression.filters
        ],
    }


def filter_identity(func):
    """The filter function the pipeline actually selected, exactly.

    This described `func.__name__` until M3's review, on the reasoning that the
    same registry object reaches both sides so identity "would prove nothing".
    **That is backwards**, and it is backwards in the direction that matters: the
    property under test is *both parsers selected the same function*, and
    `__name__` cannot see a difference the moment two libraries register
    different functions under one name — which is not hypothetical. Django's
    `Parser.add_library` is `self.filters.update(lib.filters)` (`base.py:669`),
    so `{% load %}` shadowing a built-in *replaces* the entry, and the
    registered `escape` is already a different object from the module-level
    `defaultfilters.escape` (measured: `defaultfilters.escape.__module__` is
    `django.utils.html` while `register.filters["escape"].__name__` is
    `escape_filter`). Two such pipelines would have compared equal.

    So the identity is carried, and the names are carried *with* it: `id` is the
    exact property, and a bare `id` in a diff says nothing about what diverged.
    The ids are safe to compare because both sides are live at the same moment —
    `describe` runs inside one `assertEqual` and both `FilterExpression`s are
    alive throughout — so no id can be reused out from under the comparison.
    """
    return (func.__module__, func.__qualname__, id(func))


def describe_variable(variable):
    """A `Variable` by value, for the same reason.

    `var` is carried *as well as* `lookups` because the two are not redundant:
    `_("…")` rewrites `var` to the inner message (`base.py:898-902`) while
    `lookups` is what resolution actually walks, and a divergence in which of
    the two a side kept would otherwise be invisible.
    """
    return {
        "type": "Variable",
        "var": variable.var,
        "literal": describe(variable.literal),
        "lookups": variable.lookups,
        "translate": variable.translate,
        "message_context": variable.message_context,
    }


def walked_subtree(node):
    """Type names of a node's subtree, reached through `child_nodelists`.

    The second traversal. `describe` reads the attributes it is given; this asks
    the node itself, which is what Django's renderer does.
    """
    return [type(child).__name__ for child in node.get_nodes_by_type(Node)]


def fingerprint(nodelist):
    return {
        "tree": describe(nodelist),
        "subtrees": [walked_subtree(node) for node in nodelist],
    }


def outcome(parse, source, debug):
    """(kind, payload) for either implementation, so failures compare too."""
    try:
        return ("ok", fingerprint(parse(source, debug)))
    except Exception as exc:  # noqa: BLE001  (StopIteration is deliberate)
        return ("error", str(exc), token_shape(getattr(exc, "token", None)))


def descriptors(source, debug=False):
    """Rust's node descriptors, **before** materialisation.

    The only place a host node is visible *as* a host node. After
    materialisation both kinds are ordinary Django classes — the host node is
    whatever its compile function returned — so the tree cannot be asked where a
    node came from. `_native.parse` returns
    `(nodelist, contains_nontext, …)` and a descriptor is
    `(class_name, attrs, nodelists, token, origin)`.
    """
    parser = filter_parser()
    tree = _native.parse(
        source,
        debug,
        compile_filter=materialise_module.compile_filter(parser),
        host=ShellHost(parser),
    )
    return tree[0]


def contains_host_node(descriptor_list):
    """Whether any descriptor, at any depth, is a `HOST_NODE`."""
    for desc in descriptor_list:
        if desc[0] == materialise_module.HOST_NODE:
            return True
        nodelists = desc[2] if len(desc) > 2 else ()
        if any(contains_host_node(nodelist) for nodelist in nodelists):
            return True
    return False


class CorpusParityTests(unittest.TestCase):
    def assert_trees_match(self, source, debug):
        theirs = django_parse(source, debug)
        ours = ours_parse(source, debug)
        self.assertEqual(
            describe(ours),
            describe(theirs),
            f"tree differs for {source!r} (debug={debug})",
        )
        self.assertEqual(
            [walked_subtree(node) for node in ours],
            [walked_subtree(node) for node in theirs],
            f"subtree walk differs for {source!r} (debug={debug})",
        )

    def test_corpus_matches_without_debug_positions(self):
        for source in TEMPLATES:
            with self.subTest(source=source):
                self.assert_trees_match(source, debug=False)

    def test_corpus_matches_with_debug_positions(self):
        for source in TEMPLATES:
            with self.subTest(source=source):
                self.assert_trees_match(source, debug=True)

    def test_the_corpus_is_not_trivially_small(self):
        self.assertGreaterEqual(len(TEMPLATES), 40)
        self.assertGreaterEqual(len(set(TEMPLATES)), 40, "duplicate corpus entries")

    def test_a_host_tag_parses_to_the_same_tree_as_django(self):
        """The first corpus row whose tree contains a host node, named.

        Named rather than left to the loop above because this is the row that
        says the seam produces a *comparable* tree at all, and because the
        comparison here is the one that rests on a subtlety: `describe`'s `Node`
        arm compares `vars()` recursively, and a `SimpleNode` holds `func` — a
        live function object, compared by identity because it is neither a
        `Node` nor a `FilterExpression`. So the two sides must be given the
        **same** `Library` instance, which is what `LIBRARIES` being dotted
        paths buys: `Engine(libraries=…)` resolves them to the module's own
        `register`, once. Two separately-built libraries would differ in `func`
        and this row would fail looking like a Rust bug in a working seam.
        """
        source = "{% load custom %}{% no_params %}"
        self.assert_trees_match(source, debug=False)
        self.assert_trees_match(source, debug=True)

    def test_the_corpus_has_a_row_whose_tree_contains_a_host_node(self):
        """`builtin_tag_names_including_stubs()` is Rust's registry, so the
        coverage gate is structurally blind to every tag the *host* compiles —
        including both of M6's, and `{% load %}` itself.

        That is not a hypothetical: the gate derives its demand from
        `builtin_tag_names_including_stubs() - builtin_tag_stub_names()`, so
        when `load` and
        `querystring` left the stub set in Task 3 it demanded nothing new, and
        M6 could have closed with its whole mechanism ungraded and every
        coverage assertion green. This row is what makes that impossible, and it
        asserts the *descriptor*, since materialisation erases the distinction.
        """
        rows = [row for row in TEMPLATES if "{% load" in row]
        self.assertTrue(rows, "no corpus row loads a library")
        self.assertTrue(
            any(contains_host_node(descriptors(row)) for row in rows),
            "every `{% load %}` row materialised without a host node, so the "
            "corpus proves nothing about the seam",
        )

    def test_a_host_node_carries_the_token_rust_attached(self):
        """Review Focus 4: the host node must be token-bearing *by the same route*
        as every other node, which is the thing Task 2's arm can quietly break.

        Rust ports `extend_nodelist` (`crates/template-core/src/parser/mod.rs:518`)
        and calls it at `crates/template-core/src/parser/mod.rs:430` for a host
        descriptor exactly as for a Rust-built block node, so the token reaches
        the descriptor either way. The failure
        mode is therefore not "Django forgot to attach it" but "the
        `materialise_node` arm returned the stored object before the attachment
        ran", leaving a user tag's node the one shape that renders tokenless in
        the debug traceback path. Both nodes below are compared for that reason:
        asserting only that the *host* node has a token would pass for a node the
        host had already stamped itself.
        """
        tree = ours_parse("{% load testtags %}{% echo x %}{% csrf_token %}", debug=True)
        # Positional, not by class name: `tree[1]` is the host's `{% echo %}` and
        # `tree[2]` is Rust's `{% csrf_token %}`. Selecting by class would be
        # circular here — the node's class is the thing the host chose.
        host, rust = tree[1], tree[2]

        self.assertEqual(host.token.contents, "echo x")
        self.assertEqual(rust.token.contents, "csrf_token")
        # `position` is the field that separates a token Rust stamped from one
        # rebuilt from `contents` alone: `Token.__init__` defaults it to `None`
        # (`base.py:472`) and the debug lexer always fills it. Measured on the
        # pinned tree: `(19, 31)`, with `(0, 19)` for the `{% load %}` before it.
        self.assertEqual(host.token.position, (19, 31))
        self.assertIsNotNone(rust.token.position)

    def test_the_corpus_covers_every_implemented_tag_with_a_success_path(self):
        """Every tag the engine can *parse* has a happy-path corpus row.

        Enumerated from the engine, not hardcoded. M2's version of this test
        listed eight names, so it could not fail for any tag it did not name —
        the vacuous gate the M5 plan exists to close. It now names none and
        derives the set instead: a tag leaves `builtin_tag_stub_names()` when its body
        lands, and this test starts demanding a row for it in that same commit,
        whether or not anyone remembered to update a list here.

        It asserts the exact set of *gaps* rather than looping `assertIn`, which
        is the difference that matters: a loop can only report a tag it already
        knows to look for, so an exemption written as a missing list entry is
        indistinguishable from an oversight. Here the exceptions are named,
        counted, and asserted to be all of them.
        """
        implemented = set(_native.builtin_tag_names_including_stubs()) - set(
            _native.builtin_tag_stub_names()
        )
        joined = "\n".join(TEMPLATES)
        # `\b` and not a bare prefix: `{% ifchanged %}` is not a `{% if %}` row,
        # and a prefix match would let the longer tag's row satisfy the shorter
        # tag's requirement — quietly, and in the direction that makes a missing
        # row look present.
        missing = {
            tag
            for tag in implemented
            if not re.search(r"\{%\s*" + re.escape(tag) + r"\b", joined)
        }
        # `resetcycle` is the one implemented tag with no success path *yet*, and
        # the reason is not an oversight: every successful compile of it needs a
        # preceding `{% cycle %}`, because `do_resetcycle` raises "No cycles in
        # template." when `parser.last_cycle_node` is unset, and `cycle` does not
        # land until M5's Task 6. So the row arrives with that task's corpus, and
        # this set shrinks to empty there — a second pin that has to move, for the
        # same reason the Rust one does.
        # `resetcycle`'s exemption is *empty* as of M5's Task 6, and that is the
        # pin moving rather than the test weakening: `{% cycle 'a' 'b' as c
        # %}{% resetcycle %}` is the first template in which `resetcycle` has a
        # success path at all, because every earlier one raised "No cycles in
        # template." (Task 1 declared the exemption; this is where it empties.)
        deferred = set()
        self.assertEqual(
            missing, deferred, f"corpus gaps that are not the declared exemption: "
            f"{sorted(missing - deferred)}; "
            f"declared gaps now covered: {sorted(deferred - missing)}",
        )


class RandomisedParityTests(unittest.TestCase):
    def test_randomised_templates_match(self):
        rng = random.Random(RANDOM_SEED)
        outcomes = {"ok": 0, "error": 0}

        for index in range(RANDOM_CASES):
            source = random_template(rng)
            for debug in (False, True):
                with self.subTest(case=index, source=source, debug=debug):
                    theirs = outcome(django_parse, source, debug)
                    ours = outcome(ours_parse, source, debug)
                    self.assertEqual(ours, theirs, f"outcome differs for {source!r}")
                    outcomes[theirs[0]] += 1

        # A generator that only ever produced failures would compare error
        # messages and never a tree, so pin that most cases parse.
        self.assertGreater(
            outcomes["ok"],
            RANDOM_CASES,
            f"the generator barely produced successful parses: {outcomes}",
        )

    def test_the_generator_reaches_the_splitting_path(self):
        """The randomised pass must exercise `smart_split`, not only text nodes.

        `RANDOM_ALPHABET` carries U+001C..U+001F because Python's `str.isspace()`
        accepts them and Rust's `char::is_whitespace()` does not — the M1
        divergence. That table is read in `smart_split`, so separators inside a
        *text node* prove nothing; they have to land in a tag argument. Measured
        with the current seed — re-measured when M3's Task 10 widened the
        generator, since a new arm shifts the whole RNG stream: 400 templates,
        188 containing an `{% autoescape %}` tag, 304 arguments, 127 of them
        carrying a Python-only separator, 90 a quote, 16 empty.

        There is deliberately **no** floor on the raw argument *count*. An
        earlier version asserted `len(arguments) > 100` and it was false: the
        regex matches the fixed `{% autoescape on %}` block tags as well, so the
        number passed even after the argument path was deleted. A floor that
        survives deletion of the thing it floors reads as coverage and provides
        none. The two floors below measure what they name, because `on` and
        `off` carry neither a separator nor a quote.

        The numbers above are the evidence for those floors' headroom. Changing
        the seed invalidates them: re-run the measurement rather than assume it.
        """
        rng = random.Random(RANDOM_SEED)
        arguments = []
        for _ in range(RANDOM_CASES):
            source = random_template(rng)
            for match in re.finditer(r"\{% autoescape (.*?) %\}", source, re.S):
                arguments.append(match.group(1))

        self.assertGreater(
            sum(1 for a in arguments if any(c in a for c in "\u001c\u001d\u001e\u001f")),
            10,
            "no argument carried a Python-only separator, so the M1 divergence "
            "is not being exercised where it is read",
        )
        self.assertGreater(
            sum(1 for a in arguments if "'" in a or '"' in a),
            5,
            "no argument carried a quote, so `smart_split`'s quoting arm is idle",
        )

    def test_the_generator_reaches_variables(self):
        """A floor on variables, and one on their *shapes*.

        The M2 residue was that all 400 templates were variable-free outside
        `{% verbatim %}`/`{% comment %}` — so a green randomised pass said
        nothing about M3's new code. Measured with the current seed: 154 of the
        400 templates contain `{{`.

        A single floor on "some template contains `{{`" would be satisfied by one
        template, so the shapes are counted separately: a generator that produced
        only `{{ x }}` would leave dotted lookup, indexing and filters — the whole
        of M3 — unexercised. Re-run the measurement after changing the seed or
        the arms; the M2 round's numbers were invalidated by exactly that, and
        the lesson is recorded in the test above too.
        """
        rng = random.Random(RANDOM_SEED)
        sources = [random_template(rng) for _ in range(RANDOM_CASES)]
        joined = "\n".join(sources)
        self.assertGreater(
            sum("{{" in source for source in sources),
            RANDOM_CASES // 4,
            "the generator barely reached variables",
        )
        for shape in ("{{ x }}", "{{ a.b.c }}", "{{ a.0 }}", "|upper", "|default:"):
            self.assertIn(shape, joined, f"no randomised template reached {shape}")


class ShapeTests(unittest.TestCase):
    """Claims about the tree that a diff against Django would not isolate."""

    def test_text_nodes_are_real_text_nodes(self):
        # Django's `extend_nodelist` and `ExtendsNode.render` both isinstance-test
        # TextNode (Review Focus item 7), so the materialiser must not produce a
        # generic node for text — and it must not produce a TextNode for anything
        # else either, since `ExtendsNode.render` skips TextNode children.
        from django.template.base import TextNode

        nodes = ours_parse("a{% csrf_token %}b")
        self.assertEqual([type(node).__name__ for node in nodes],
                         ["TextNode", "CsrfTokenNode", "TextNode"])
        self.assertIsInstance(nodes[0], TextNode)
        self.assertNotIsInstance(nodes[1], TextNode)
        self.assertEqual(nodes[2].s, "b")

    def test_every_node_carries_a_real_token_and_an_origin(self):
        for node in ours_parse("a{% spaceless %}b{% endspaceless %}{% debug %}"):
            self.assertIsNotNone(node.token, f"{type(node).__name__} has no token")
            self.assertTrue(hasattr(node, "origin"))

    def test_verbatim_content_is_safe_and_rendered_at_parse_time(self):
        from django.utils.safestring import SafeString

        node = ours_parse("{% verbatim %}{{ x }}{% endverbatim %}")[0]
        self.assertIsInstance(node.content, SafeString)
        self.assertEqual(node.content, "{{ x }}")
        self.assertEqual(
            node.content,
            django_parse("{% verbatim %}{{ x }}{% endverbatim %}")[0].content,
        )

    def test_contains_nontext_is_per_nodelist(self):
        ours = ours_parse(
            "{% autoescape on %}{% csrf_token %}{% endautoescape %}"
            "{% autoescape on %}text{% endautoescape %}"
        )
        theirs = django_parse(
            "{% autoescape on %}{% csrf_token %}{% endautoescape %}"
            "{% autoescape on %}text{% endautoescape %}"
        )
        for index in (0, 1):
            self.assertEqual(
                ours[index].nodelist.contains_nontext,
                theirs[index].nodelist.contains_nontext,
                f"body {index}",
            )
        self.assertTrue(ours[0].nodelist.contains_nontext)
        self.assertFalse(ours[1].nodelist.contains_nontext)

    def test_a_variable_sets_contains_nontext_where_text_does_not(self):
        """§10.4's ordering gate, in the half M3 can hold.

        Measured on the pinned tree: `contains_nontext` is set only for a
        non-``TextNode`` (`base.py:611`), so `text{% extends "a" %}` parses and
        `{{ x }}{% extends "a" %}` raises the must-be-first error. Text-versus-tag
        therefore never reached the rule at all — it only showed that the rule
        does not fire — and a variable is the input that does.

        The ``expected`` column is what makes this non-vacuous: comparing our
        tree to Django's would pass even if neither side ever set the flag, which
        is exactly the state M2 left this in. Every source is a *top-level*
        nodelist so the flag read is the one the rule tests.
        """
        for source, expected in [
            ("hello", False),
            ("", False),
            ("{{ x }}", True),
            ("a{{ x }}", True),
            ("{{ x }}{% csrf_token %}", True),
            ("{{ x }}{% csrf_token %}y", True),
            ("{% csrf_token %}", True),
            ("{% spaceless %}{{ x }}{% endspaceless %}", True),
        ]:
            with self.subTest(source=source):
                ours = ours_parse(source)
                theirs = django_parse(source)
                self.assertEqual(
                    ours.contains_nontext,
                    theirs.contains_nontext,
                    f"contains_nontext differs for {source!r}",
                )
                self.assertEqual(ours.contains_nontext, expected, f"for {source!r}")

        # And per nodelist, so a body's flag is not read off the top level's:
        # the first autoescape body holds a variable, the second holds text.
        source = (
            "{% autoescape on %}{{ x }}{% endautoescape %}"
            "{% autoescape on %}b{% endautoescape %}"
        )
        ours = ours_parse(source)
        theirs = django_parse(source)
        self.assertEqual(
            [node.nodelist.contains_nontext for node in ours],
            [node.nodelist.contains_nontext for node in theirs],
        )
        self.assertEqual(
            [node.nodelist.contains_nontext for node in ours],
            [True, False],
        )

    def test_a_variable_now_parses_where_it_used_to_be_reported(self):
        """M2's `test_a_variable_is_reported_rather_than_dropped`, inverted.

        At M2 the `{{ … }}` arm of `Parser::parse` returned `Unimplemented`, and
        the assertion was that it was loud. M3's Task 5 supplies the arm, so the
        premise is gone and the assertion inverts: `{{ x }}` yields a
        `VariableNode` carrying a `filter_expression`, and it is the same node
        Django builds.

        Asserted on the **wire descriptor** — `_native.parse` returns
        `(nodes, contains_nontext, refs)` and each node is
        `(class_name, attrs, nodelists, token, slot)` — rather than through
        `ours_parse`. Materialising a `VariableNode` needs
        `NODE_CLASSES["VariableNode"]`, which is Task 6's deliverable, and
        reaching for it here would make this task's test depend on the next
        one's. The class name is compared against `type(...).__name__` on
        Django's side so it is still Django's name and not a transcription.
        """
        nodes, contains_nontext, _refs = _native.parse("a{{ x }}b")
        theirs = django_parse("a{{ x }}b")
        self.assertEqual(
            [desc[0] for desc in nodes],
            [node.__class__.__name__ for node in theirs],
        )
        self.assertTrue(contains_nontext)

        class_name, attrs, nodelists, token, slot = nodes[1]
        self.assertEqual(class_name, "VariableNode")
        self.assertEqual(nodelists, ())
        self.assertIsNone(slot, "a node outside a `{% cycle %}` names no slot")
        self.assertEqual(token[1], "x")
        expression = dict(attrs)["filter_expression"]
        self.assertEqual(expression.token, "x")
        self.assertEqual(expression.token, theirs[1].filter_expression.token)
        self.assertEqual(expression.filters, [])
        self.assertEqual(expression.var[0], True)

    def test_a_variable_that_django_rejects_is_rejected_here_too(self):
        """The character rule, reached through `Parser.parse`.

        `tests/test_filter_expr_parity.py` pins this message against Django's own
        over a generated corpus, but it drives `parse_filter_expression` directly;
        what is only observable here is that the error survives the `{{ … }}`
        arm of `Parser.parse` — which wraps the call in `self.error(token, e)`
        (`base.py:549-551`) — with the **token attached**.
        """
        with self.assertRaises(_native.ParseError) as caught:
            _native.parse("{{ a+b }}")
        self.assertEqual(
            str(caught.exception),
            "Invalid character ('+') in variable name: 'a+b'",
        )
        self.assertEqual(caught.exception.token[1], "a+b")

    def test_an_unimplemented_tag_is_its_own_kind_at_the_ffi(self):
        """`Unimplemented` is not just a `ParseError` with a different message.

        Django compiles `{% load %}`, so our refusing it is a fact about *this
        engine*. A harness that compared messages would match our text against
        Django's "Did you forget to register or load this tag?" and score the
        one case where we are wrong as a pass. The class carries it instead —
        and as a subclass, so the shell's single `except ParseError` still
        converts it to `TemplateSyntaxError` exactly as before.

        The example is a tag that is **still** a stub, enumerated rather than
        guessed: `_native.builtin_tag_stub_names()` is `['partial', 'partialdef']` on
        this tree, and `partialdef` is the one of the two Django's base parser
        will not reject first. `{% if %}` was the example until M5's Task 7
        landed it, and `{% load x %}` until M6 handed `load` to the host — which
        is exactly the rot this test was written to survive: it names a stub, so
        every milestone that implements the last stub, or hands one over, has to
        move it. M13 implements the pair that is left.
        """
        with self.assertRaises(_native.UnimplementedTag) as caught:
            _native.parse("{% partialdef x %}")
        self.assertIsInstance(caught.exception, _native.ParseError)
        self.assertEqual(caught.exception.token[1], "partialdef x")

        # The classes are genuinely different, and a syntax error is not
        # silently swept into the "not built yet" bucket.
        self.assertNotEqual(_native.UnimplementedTag, _native.ParseError)
        self.assertTrue(issubclass(_native.UnimplementedTag, _native.ParseError))
        with self.assertRaises(_native.ParseError) as syntax:
            _native.parse("{% endif %}")
        self.assertNotIsInstance(syntax.exception, _native.UnimplementedTag)

    def test_the_comparison_target_is_not_rust_owned(self):
        """The Python side of this comparison must not itself be Rust.

        Restated at M9. See `tests/parity_target.py` for the claim both this row
        and its siblings in `test_parser_errors.py`, `test_lexer_parity.py` and
        `test_filter_expr_parity.py` now assert, and why "the target file is
        Django's" and "the shim is not installed" both stopped being available:
        `check.sh` installs the shim for the session, and `base` is ours.
        """
        assert_target_is_not_rust_owned(self, Parser, "Parser")

        # And the other interposed module this corpus executes. A `{% if %}`
        # condition is a `smartif` tree (`describe` below walks one), so the
        # transcription defining its operator classes is Python-side code this
        # comparison runs. Until M9's close-out **nothing consulted
        # `COMPARED_PATH["smartif.py"]`** — no row named a `smartif` target, so
        # the table was measured and then never read, and a declared region on
        # the operators' methods would have gone unnoticed twice over. The
        # instrument could not see those methods either (`infix()`/`prefix()`
        # build them inside the module body and bind them into `OPERATORS`),
        # which is the other half of the same blind spot.
        assert_target_is_not_rust_owned(self, smartif.IfParser, "smartif.IfParser")


if __name__ == "__main__":
    unittest.main()
