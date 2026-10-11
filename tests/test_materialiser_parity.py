"""The Rust walk and the Python walk must build the same tree, and the same
objects where Django relies on identity.

`fingerprint` is the oracle for the tree. Its rules are the ones a review round
found the first version missing, and each is a shape where Python's `==` says
"same" about two things this engine must tell apart:

  * **primitives carry their type.** `True == 1 == 1.0` and `"x" == mark_safe("x")`
    in Python. A fingerprint returning raw values passes on all four wrong
    answers. `{% autoescape on %}` is a `bool`, a literal is an `int`, and a
    `{% verbatim %}` body is a `SafeString`.
  * **a real `dict` keeps its order.** `token_kwargs` gives a repeated key its
    first position and its last value; sorting erases the first half of that.
    A node's own `vars()` *is* sorted by name, because that order is CPython's
    and not a contract — the two are different questions and get different arms.
  * **an operator carries its id.** `a == b` and `a != b` are the same class:
    `infix`/`prefix` define `class Operator` inside the factory
    (`smartif.py:45-92`), so all thirteen are named `Operator` and none is a
    module attribute. The class is looked up in `smartif.OPERATORS`, which is
    the only table that can tell them apart.
  * **a class is carried whole, not by its name.** `type(value).__name__` calls a
    node a `TextNode` whether or not it is the one `NODE_CLASSES` holds, and two
    libraries can define one name between them. The class *object* is carried
    instead, so the comparison is exact and no address is printed. It is exact
    *for the classes this engine builds*: `type` on any node here is an ordinary
    `type`, and `==` between two of those is identity. A node whose class came
    from a metaclass that overrides `__eq__` would compare by that override and
    this arm would inherit its answer — no node in the registry does that today,
    and none is expected to, but the claim is about these classes and not about
    classes in general. Two classes the operator table does not name are told
    apart the same way.
  * **a `Literal` is not an operator, and the arm order is what makes that true.**
    `Literal` is a `TokenBase` with `lbp = 0`, and `TokenBase` carries `first` and
    `second` as `None`, so an operator arm placed above the literal arm swallowed
    every literal in the tree — `{% if a %}` and `{% if b %}` fingerprinted alike,
    value gone. `TemplateLiteral` subclasses `Literal`, so the whole `{% if %}`
    operand family was invisible, and `TemplateLiteral.eval` *resolves* its operand
    where `Literal.eval` returns it verbatim. The class and `text` both ride along.
  * **a callable carries a name**, because a filter function is not
    fingerprinted by address. `__qualname__` alone is not enough — two closures
    from one factory share it — so the callable itself is carried as the last
    element of the identity: `==` on two functions is identity, so the comparison
    is exact and no address is ever printed. `_callable_identity`'s docstring has
    the reasoning; `test_two_filters_with_one_qualname_are_told_apart` is the row.

**Every value in a tuple goes through `fingerprint` or through an arm that already
carries its type.** An earlier draft wrote `is_var` and `tuple(value.lookups)` into
its tuples raw, and `False == 0` and `True == 1` are exactly what the primitive arms
one screen down exist to prevent — the same collision, committed in the two arms that
had stopped calling `fingerprint` on their own contents. The `Variable` comment
claimed to be *preserving* the tuple shape while its `tuple(...)` call coerced a list
into one.

`Var`iables and `FilterExpression`s are fingerprinted by their slots, and
`_rust_names` is excluded: it is a lazily-filled cache of names derived from
`filters`, so it is a *consequence* of what is already compared.

Identities are asserted separately, in `CycleIdentityTests` and
`HostNodeIdentityTests`. A fingerprint cannot see them, and that is the point of
having both.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from tests import materialiser_corpus  # noqa: E402
from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates.engine import smartif  # noqa: E402
from django_rust_templates.engine.base import (  # noqa: E402
    FilterExpression,
    Node,
    NodeList,
    Token,
    Variable,
)
from django_rust_templates.engine.engine import Engine  # noqa: E402
from django_rust_templates.engine.library import Library  # noqa: E402
from django_rust_templates.engine.library import SimpleBlockNode  # noqa: E402
# **A plain import, and it matters.** An earlier draft loaded `parser.py` by path
# through `importlib.util.spec_from_file_location` and installed it into
# `sys.modules` under its own name. That produces a *second* module object: the
# one this file patches is then not the one `render`, the engine and `_CACHED`
# hold, so `with_native` flips a global nothing reads and every parity row passes
# by comparing the Python walk with itself. It is the same class of error as the
# stock-`django.template.Engine` mix-up that made an earlier probe report 7345 µs.
# There is exactly one `parser` module; import it. **Four other test modules had the
# same load and the same obsolete justification** — `test_materialise`,
# `test_filter_expr_parity`, `test_parser_parity`, `test_variable_parity`,
# `test_ref_table` — and all five were converted together, because one survivor is
# enough to re-install the second object. `test_materialise.py`'s docstring has the
# measurement.
from django_rust_templates.engine import parser as materialiser  # noqa: E402
from django_rust_templates.engine import library as library_mod  # noqa: E402
from django_rust_templates.engine import defaulttags  # noqa: E402
from django.utils.safestring import SafeString  # noqa: E402

_ADDRESS = re.compile(r"\bat 0x[0-9a-fA-F]+")

# **No skip list, and that is a decision rather than an omission.** Nothing in a
# node's `vars()` is dropped: `origin` is compared because it is what the annotation
# protocol matches on, so a walk that lost it would build a tree that never annotates,
# and `token` is compared for the message a `TemplateSyntaxError` would carry. An
# earlier draft carried a `_SKIPPED_NODE_ATTRS = frozenset()` beside these lines: an
# exclusion that excluded nothing, which reads as a policy and holds none.


def _callable_identity(func):
    """A filter function, named *and* held.

    **The name alone is not enough, and the review is why.** `__module__` plus
    `__qualname__` equates two *different* closures produced by the same factory —
    reachable whenever a library rebinds a filter mid-parse — and the fingerprint
    would then call two trees identical while `{{ a|f }}` renders differently in
    each. The callable itself rides as the last element: `==` on two functions is
    identity, so the comparison is exact, and no address is ever *printed* — which
    is what `fingerprint`'s guard refuses, not what it compares.
    """
    return [
        "callable",
        getattr(func, "__module__", None),
        getattr(func, "__qualname__", None) or getattr(func, "__name__", None),
        func,
    ]


def _operator_id(operator):
    """Which `smartif.OPERATORS` entry built this, by class identity.

    The classes are defined inside the `infix`/`prefix` factories, so they are
    all named `Operator` and none is a module attribute. The table is the only
    place they are reachable from, and without this `a == b` and `a != b`
    fingerprint identically — two conditions that evaluate differently on every
    input.
    """
    for key, factory in smartif.OPERATORS.items():
        if type(operator) is factory:
            return key
    return None


def fingerprint(value):
    """A structural description of a materialised tree.

    Raises rather than returns when it falls through to `repr` of something
    carrying an address: a fingerprint that *contains* an address compares
    unequal for a reason that has nothing to do with the two trees, which turns
    a real divergence and a memory layout into the same red row. The project has
    lost to this once — `describe()`'s dispatch falling to its memory-address arm
    measured 537 order-dependent failures in the parser harness.
    """
    if isinstance(value, NodeList):
        # `contains_nontext` through `fingerprint` rather than `bool(...)`: the flag is
        # a `bool`, and `bool(1) is True` is the normalisation the primitive arms exist
        # to prevent. Three fields in this file had it — this one, `FilterExpression`'s
        # `is_var` and `Variable`'s `translate` — and each was invisible to a mutation
        # that swapped the boolean for its equal integer.
        return [
            "nodelist",
            fingerprint(value.contains_nontext),
            [fingerprint(n) for n in value],
        ]
    if isinstance(value, Node):
        # **The class object, not its name.** `type(value).__name__` calls two
        # different classes "TextNode" — reachable from a library that defines a
        # node under a name another library already uses — and the two render
        # differently. Same trick as `_callable_identity`: the object itself rides
        # in the tuple, and for a class built by plain `type` the `==` the
        # comparison eventually runs is identity, so no address is printed. See the
        # rule list in the module docstring for where that guarantee stops.
        return [
            type(value),
            sorted(
                ((name, fingerprint(attr)) for name, attr in vars(value).items()),
                key=lambda pair: pair[0],
            ),
        ]
    if isinstance(value, Token):
        return ["token", value.token_type.name, value.contents, value.position, value.lineno]
    if isinstance(value, FilterExpression):
        return [
            "filterexpr",
            # `filters` is `[(filter_func, args)]` and `args` is
            # `[(is_var, operand)]` (`base.py:768-781`). Two elements per pair,
            # not three: an earlier draft unpacked three names and would have
            # raised on every `{{ a|default:'x' }}` in the corpus. And `is_var`
            # goes through `fingerprint` rather than into the list bare, because
            # it is a `bool` and `True == 1` — an earlier draft compared a
            # literal-argument filter against a variable-argument one as equal.
            [
                [
                    _callable_identity(func),
                    [[fingerprint(is_var), fingerprint(arg)] for is_var, arg in args],
                ]
                for func, args in value.filters
            ],
            value.token,
            fingerprint(value.is_var),
            fingerprint(value.var),
        ]
    if isinstance(value, Variable):
        return [
            "variable",
            value.var,
            # **`lookups` through `fingerprint`, not `tuple(...)`, and the
            # difference is the point.** `Variable.__init__` splits the name with
            # `tuple(var.split(...))`, so a *tuple* is what the parser produces and
            # the type is a shape the render walk reads — which is what an earlier
            # draft's comment claimed while its `tuple(...)` call did the opposite:
            # it coerced a list into a tuple and made the two compare equal. Passing
            # the value through keeps the shape and reports it.
            fingerprint(value.lookups),
            fingerprint(value.literal),
            fingerprint(value.translate),
            value.message_context,
        ]
    if isinstance(value, smartif.Literal):
        # **This arm has to come before the operator arm, and that ordering is a
        # review finding.** `Literal` is a `TokenBase` with `lbp = 0`, and
        # `TokenBase` carries `first` and `second` as `None`, so the operator arm
        # below used to swallow every literal in the tree: `smartif.Literal("x")`
        # fingerprinted as `['op', None, 0, ['none'], ['none']]` — the value gone,
        # and a literal indistinguishable from an operator class `_operator_id`
        # could not name. `TemplateLiteral` is a `Literal` subclass, so the whole
        # `{% if %}` operand family was invisible.
        #
        # `text` and the class are both carried: `TemplateLiteral.eval` resolves
        # `value`, while `Literal.eval` returns it verbatim, so `a=None` gives
        # `False` for one and `True` for the other, and `text` is what
        # `display()` shows in an error message.
        return [
            "literal",
            type(value),
            fingerprint(value.value),
            fingerprint(getattr(value, "text", None)),
        ]
    if isinstance(value, smartif.TokenBase) and hasattr(value, "lbp"):
        # An operator, infix or prefix. `second is None` is the prefix shape and
        # is part of the fingerprint because it is part of the class. The class
        # rides along with `_operator_id`'s key so that two classes the table does
        # not name are still told apart.
        #
        # **An unnamed class is loud rather than silent.** `TokenBase` carries `first`
        # and `second` as `None`, so an unnamed subclass reaching this arm is read as an
        # operandless operator — its real operands never compared. `Literal` and
        # `TemplateLiteral` used to land here; `EndToken`, a parser sentinel that does
        # not appear in a tree, is the only remaining subclass this arm can claim.
        operator_id = _operator_id(value)
        if operator_id is None:
            raise AssertionError(
                f"{type(value).__name__} reached the operator arm and smartif.OPERATORS "
                f"does not name it, so `first`/`second` may not be its operands. "
                f"Give it an arm above this one, or name the class."
            )
        return [
            "op",
            type(value),
            operator_id,
            value.lbp,
            fingerprint(value.first),
            fingerprint(value.second),
        ]
    if isinstance(value, dict):
        # Insertion order, and not sorted: a `dict` here is `token_kwargs`'
        # result, where a repeated key keeps its first position.
        return ["dict", [[key, fingerprint(item)] for key, item in value.items()]]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [fingerprint(item) for item in value]]
    # Primitives carry their type, and the subclass checks come first: `bool` is
    # an `int`, `SafeString` is a `str`, and a fingerprint that returned the bare
    # value would call `True`, `1` and `1.0` the same tree.
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, SafeString):
        return ["safe", str(value)]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", value]
    if isinstance(value, str):
        return ["str", value]
    rendered = repr(value)
    if _ADDRESS.search(rendered):
        raise AssertionError(
            f"fingerprint fell through to an address-bearing repr for "
            f"{type(value).__name__}: {rendered!r}. Add an arm for it."
        )
    return [type(value).__name__, rendered]


def _tree(source, engine=None, native=None):
    """The materialised `NodeList` for `source`.

    **Through `_native.compile_template` and not through `materialise`
    directly**, because that is the only Python-reachable entry point holding the
    *Rust* `NodeListDesc` the native walk takes. There is no way to hand a
    `NodeListDesc` across the boundary, so the alternative would be a test-only
    API on the shipped extension. Driving the real entry point is stronger
    anyway: it is the object a request actually builds.

    `native=None` means **leave the switch alone** — the module default, whatever
    Task 8 leaves it at. Passing `False` explicitly is how the parity row asks
    for the Python path, and a default of `False` would mean the module default
    could never select native in any test.
    """
    engine = engine or materialiser_corpus.corpus_engine()
    parser = render.parser_for(source, engine, render.UNKNOWN_ORIGIN)
    host = render.ShellHost(parser)
    compile_it = lambda: _native.compile_template(  # noqa: E731
        source, parser, materialiser, engine, host, render.UNKNOWN_ORIGIN
    )
    template = compile_it() if native is None else with_native(compile_it, enabled=native)
    return template.nodelist



def _hosting_engine():
    """An engine whose `{% wrap %}` builds its node in **Python**, during the parse.

    `Library.simple_block_tag` parses the body host-side and constructs the
    `SimpleBlockNode` there, so the object exists before the walk is ever called
    and is reachable only through the reference table. That is Review Focus item
    1's shape, and it cannot be produced any other way.
    """
    library = Library()

    @library.simple_block_tag
    def wrap(content):
        return content

    engine = Engine()
    engine.template_builtins.append(library)
    return engine


def with_native(body, enabled=True):
    """Run `body` with a named branch selected, and restore the switch after.

    The constant is the *default*; a test that needs a specific branch sets it
    explicitly rather than depending on what Task 8 leaves it at, so every test
    here keeps meaning the same thing on both sides of the flip.

    **Read through `getattr` and restored as absence, because this file runs
    before Task 3 adds the constant.** Task 3 appends `USE_NATIVE_MATERIALISER`
    to `parser.py`; a bare read here would make this whole module fail to import
    until then, which is the same defect `compile_profile._on_path` is written to
    avoid. Setting an attribute nothing reads yet is harmless — `compile_template`
    ignores the name until Task 3's call site consults it — so the parity row is
    genuinely green on both sides of the flip rather than skipped.
    """
    real = getattr(materialiser, "USE_NATIVE_MATERIALISER", None)
    had = hasattr(materialiser, "USE_NATIVE_MATERIALISER")
    materialiser.USE_NATIVE_MATERIALISER = enabled
    try:
        return body()
    finally:
        if had:
            materialiser.USE_NATIVE_MATERIALISER = real
        else:
            del materialiser.USE_NATIVE_MATERIALISER


def _collect(nodelist):
    """Every node in a tree, including the ones inside `{% if %}`'s pairs.

    `IfNode` inherits `child_nodelists == ('nodelist',)` from `Node` while
    `nodelist` is a computed property returning `NodeList(self)`, so an
    `IfNode`'s children are reached through `conditions_nodelists` and not
    through the inherited name. Both are walked here.
    """
    for node in nodelist:
        yield node
        children = [getattr(node, name, None) for name in node.child_nodelists]
        for pair in getattr(node, "conditions_nodelists", None) or ():
            if isinstance(pair, tuple):
                children.append(pair[1])
        for child in children:
            if isinstance(child, NodeList):
                yield from _collect(child)


class FingerprintSensitivityTests(unittest.TestCase):
    """Mutations the oracle must see, each one a shape Python calls equal.

    A structural oracle that cannot fail is a green light with no bulb in it, and
    the failure is silent: every later task passes and the tree is wrong. Most of
    these rows are review findings, one per review round.
    """

    def test_a_changed_text_is_seen(self):
        self.assertNotEqual(fingerprint(_tree("hello")), fingerprint(_tree("goodbye")))

    def test_a_reordered_nodelist_is_seen(self):
        self.assertNotEqual(
            fingerprint(_tree("{{ a }}{{ b }}")), fingerprint(_tree("{{ b }}{{ a }}"))
        )

    def test_true_and_one_are_not_the_same_tree(self):
        # `True == 1` in Python, and `{% autoescape on %}`'s `setting` is a
        # `bool` while a literal `1` is an `int`. A fingerprint that returned
        # raw values would pass on the wrong one.
        self.assertNotEqual(fingerprint(True), fingerprint(1))
        self.assertNotEqual(fingerprint(1), fingerprint(1.0))

    def test_a_safe_string_is_not_a_plain_string(self):
        # `mark_safe("x") == "x"`. A `{% verbatim %}` body's value is a
        # `SafeString`, and returning it as a `str` would pass while making the
        # node escape its own literal.
        from django.utils.safestring import mark_safe

        self.assertNotEqual(fingerprint(mark_safe("x")), fingerprint("x"))

    def test_two_operators_with_one_class_name_are_told_apart(self):
        # The review's finding: every operator is `class Operator`, defined
        # inside its factory, so class *name* cannot distinguish them and the
        # class is not a module attribute either.
        equal = _tree("{% if a == b %}x{% endif %}")[0].conditions_nodelists[0][0]
        differs = _tree("{% if a != b %}x{% endif %}")[0].conditions_nodelists[0][0]
        self.assertEqual(type(equal).__name__, type(differs).__name__)
        self.assertNotEqual(fingerprint(equal), fingerprint(differs))

    def test_a_literal_carries_its_value(self):
        # **A dead arm, and this row is what caught it.** `Literal` is a
        # `TokenBase` with `lbp = 0`, and `TokenBase` carries `first` and `second`
        # as `None`, so the operator arm claimed every literal before the literal
        # arm below it could run: `{% if a %}` and `{% if b %}` — whose condition
        # *is* a `TemplateLiteral`, with no operator at all — fingerprinted
        # identically, as `['op', None, 0, ['none'], ['none']]`.
        self.assertNotEqual(
            fingerprint(smartif.Literal("x")), fingerprint(smartif.Literal("y"))
        )
        self.assertNotEqual(fingerprint(smartif.Literal("x")), fingerprint(smartif.Literal(1)))
        # The **condition**, and not the whole tree. A tree-level version of this
        # row passed against the broken fingerprint for an unrelated reason: an
        # `IfNode` carries its own `{% if a %}` token, whose contents differ, so the
        # two trees differed while the two literals did not.
        self.assertNotEqual(
            fingerprint(_tree("{% if a %}x{% endif %}")[0].conditions_nodelists[0][0]),
            fingerprint(_tree("{% if b %}x{% endif %}")[0].conditions_nodelists[0][0]),
        )

    def test_a_template_literal_is_not_a_bare_literal(self):
        # The review's repro, and the two evaluate differently rather than merely
        # looking different. `TemplateLiteral.eval` resolves its operand against
        # the context; `Literal.eval` returns it verbatim (`smartif.py:139-140`,
        # `defaulttags.py:917-918`). With `a=None` one condition is false and the
        # other is true, and an earlier fingerprint called them the same tree.
        expression = _tree("{{ a }}")[0].filter_expression
        template_literal = defaulttags.TemplateLiteral(expression, "a")
        plain = smartif.Literal(expression)
        self.assertNotEqual(fingerprint(template_literal), fingerprint(plain))

    def test_two_literals_with_different_source_text_are_told_apart(self):
        # `text` is what `TemplateLiteral.display()` shows in an error message, and
        # it is not derivable from `value`: `{% if a %}` and `{% if a %}` written
        # around different spellings of one operand carry the same expression.
        expression = _tree("{{ a }}")[0].filter_expression
        self.assertNotEqual(
            fingerprint(defaulttags.TemplateLiteral(expression, "a")),
            fingerprint(defaulttags.TemplateLiteral(expression, "a.b")),
        )

    def test_two_node_classes_with_one_name_are_told_apart(self):
        # A node class is not identified by its name. Two libraries can each define
        # a node called `TextNode`, they render differently, and `NODE_CLASSES` — a
        # dict keyed by name — is where the collision surfaces.
        class First(Node):
            def __init__(self):
                self.s = "x"

        Second = type("First", (Node,), {"__init__": First.__init__})
        self.assertEqual(type(First()).__name__, type(Second()).__name__)
        self.assertIsNot(type(First()), type(Second()))
        self.assertNotEqual(fingerprint(First()), fingerprint(Second()))

    def test_a_list_lookups_is_not_a_tuple_lookups(self):
        # `Variable.__init__` builds a tuple, and the render walk reads that shape.
        # An earlier fingerprint wrote `tuple(value.lookups)`, which coerced a list
        # into a tuple and made the two compare equal — normalising away the very
        # difference its own comment claimed to be protecting.
        variable = Variable("a.b")
        self.assertEqual(variable.lookups, ("a", "b"))
        self.assertIsInstance(variable.lookups, tuple)
        coerced = Variable("a.b")
        coerced.lookups = list(coerced.lookups)
        self.assertNotEqual(fingerprint(Variable("a.b")), fingerprint(coerced))

    def test_a_literal_filter_argument_is_not_a_variable_one(self):
        # `FilterExpression.filters` holds `(func, [(is_var, operand)])` and `is_var`
        # is a `bool`. `False == 0` and `True == 1`, so an earlier fingerprint put a
        # literal argument and a variable argument on the same side of the
        # comparison — the same collision the primitive arms exist to prevent,
        # committed in the one arm that wrote a raw value into the tuple instead of
        # calling `fingerprint` on it.
        #
        # **Both directions, and the colliding value in each.** A first draft mutated
        # `False` to `1`, which does not collide with anything and so passed against
        # the broken fingerprint — a row that goes green for the wrong reason.
        for source, is_var, collision in (
            ("{{ a|default:'x' }}", False, 0),
            ("{{ a|default:b }}", True, 1),
        ):
            with self.subTest(source=source):
                node = _tree(source)[0]
                before = fingerprint(node)
                func, args = node.filter_expression.filters[0]
                self.assertIs(type(args[0][0]), bool)
                self.assertIs(args[0][0], is_var)
                node.filter_expression.filters[0] = (func, [(collision, args[0][1])])
                self.assertNotEqual(before, fingerprint(node))

    def test_a_swapped_child_between_two_parents_is_seen(self):
        whole = _tree("{% if a %}x{% endif %}{% if b %}y{% endif %}")
        before = fingerprint(whole)
        whole[0].conditions_nodelists[0][1][0].s = "y"
        whole[1].conditions_nodelists[0][1][0].s = "x"
        self.assertNotEqual(before, fingerprint(whole))

    def test_a_dropped_token_is_seen(self):
        tree = _tree("{% if a %}{{ b }}{% endif %}")
        before = fingerprint(tree)
        tree[0].token = None
        self.assertNotEqual(before, fingerprint(tree))

    def test_a_dropped_origin_is_seen(self):
        tree = _tree("{% if a %}{{ b }}{% endif %}")
        before = fingerprint(tree)
        tree[0].origin = None
        self.assertNotEqual(before, fingerprint(tree))

    def test_the_typed_flags_are_not_normalised_by_bool(self):
        """Three fields that read a bare `bool(...)` and so could not see `1`.

        `bool(1) is True`, so a fingerprint that wraps a flag in `bool()` reports the
        same value whether the walk produced `True` or `1`, and the arms one screen
        down that exist to tell exactly those apart never see the field. Found by
        mutating each flag to its equal integer and getting an unchanged
        fingerprint — three rows' worth of hidden green.
        """
        nodelist = _tree("hello")
        self.assertIs(nodelist.contains_nontext, False)
        nodelist.contains_nontext = 0
        self.assertNotEqual(fingerprint(_tree("hello")), fingerprint(nodelist))

        # Both reachable values of the field, and **not** because one alone would
        # miss the mutation: `bool()` collapses `False`/`0` *and* `True`/`1`, so
        # either direction already kills it. Running both is coverage of the field
        # rather than of the regression — `is_var` is `True` for `{{ a }}` and
        # `False` for `{{ "x" }}`, and a row that only walked one of those was
        # asserting about half of what the arm carries.
        is_var = _tree('{{ "x" }}')[0]
        self.assertIs(is_var.filter_expression.is_var, False)
        before = fingerprint(is_var)
        is_var.filter_expression.is_var = 0
        self.assertNotEqual(before, fingerprint(is_var))

        is_a_var = _tree("{{ a }}")[0]
        self.assertIs(is_a_var.filter_expression.is_var, True)
        before = fingerprint(is_a_var)
        is_a_var.filter_expression.is_var = 1
        self.assertNotEqual(before, fingerprint(is_a_var))

    def test_a_translate_flag_is_not_an_integer(self):
        # `Variable.translate` is set by `_('…')` and drives a `gettext` call at
        # render time, so a `1` where a `True` belongs is a different node.
        plain = Variable("a")
        self.assertIs(plain.translate, False)
        translated = Variable("a")
        translated.translate = 0
        self.assertNotEqual(fingerprint(plain), fingerprint(translated))

    def test_an_operator_class_the_table_does_not_name_is_loud(self):
        # `TokenBase` carries `first` and `second` as `None`, so an unnamed operator
        # class would be fingerprinted as an operandless operator with its real
        # operands never compared. `Literal` and `TemplateLiteral` were exactly that
        # until they got an arm of their own; this makes the next one fail loudly
        # instead of quietly.
        class Rogue(smartif.TokenBase):
            lbp = 3

            def __init__(self):
                self.first = None
                self.second = None

        with self.assertRaises(AssertionError) as caught:
            fingerprint(Rogue())
        self.assertIn("OPERATORS", str(caught.exception))

    def test_two_filters_with_one_qualname_are_told_apart(self):
        # The collision a name-only identity cannot see, and it is reachable in
        # production: a library that rebinds a filter mid-parse leaves two
        # *different* closures under one module and one qualname.
        #
        # A **rebinding sandwich**, because a row that only compared two
        # fingerprints would pass if the factory returned the same object twice —
        # which is not the case here and is what the `assertIsNot` rules out. It
        # asserts the collision is real (`assertEqual` on the identity) before
        # asserting the fingerprints are not, so a row that went green for the
        # wrong reason is a failure rather than a pass.
        def make(marker):
            def filter_fn(value):
                return f"{marker}{value}"

            return filter_fn

        first = make("a")
        second = make("a")
        self.assertIsNot(first, second)
        self.assertEqual(
            _callable_identity(first)[:3], _callable_identity(second)[:3],
            "the fixture is not a collision: the names already differ",
        )

        # A filter library registered on the engine is visible to the parse
        # (`python/django_rust_templates/render.py:345`, where `parser_for` hands
        # `engine.template_builtins` to Django's `Parser`), which is the only way
        # to put a closure where the compiler will find it. The engine's own
        # `defaultfilters` are *not* mutated: that would leak a `rebound` filter
        # into every later test in the process.
        engine = Engine()
        engine.template_builtins.append(library_mod.Library())
        library = engine.template_builtins[-1]

        library.filters["rebound"] = first
        before = fingerprint(_tree("{{ a|rebound }}", engine))
        library.filters["rebound"] = second
        after = fingerprint(_tree("{{ a|rebound }}", engine))
        self.assertNotEqual(before, after)

    def test_the_fingerprint_refuses_an_address_bearing_repr(self):
        class Opaque:
            __slots__ = ()

        with self.assertRaises(AssertionError):
            fingerprint(Opaque())


class TwoPathParityTests(unittest.TestCase):
    """Every corpus source, fingerprinted through both branches of the switch.

    **Two different branches, explicitly.** An earlier draft compared
    `_tree(source)` with `_tree(source)` — the Python path with itself, which
    passes for a trivial reason and would go on passing after every task in this
    plan. The two calls below name their branch.
    """

    def test_the_two_paths_agree_on_every_corpus_source(self):
        for label, source in materialiser_corpus.SOURCES:
            with self.subTest(source=label):
                self.assertEqual(
                    fingerprint(_tree(source, native=False)),
                    fingerprint(_tree(source, native=True)),
                )

    def test_append_is_bound_before_the_node_is_built_on_both_paths(self):
        """`nodelist.append` is resolved *before* the node's own construction.

        `materialise` reads `nodelist.append(materialise_node(desc, ...))`, so
        `LOAD_METHOD` resolves `append` first and only then evaluates the argument.
        A factory that rebinds `NodeList.append` while it builds a node is therefore
        **not** seen by that node's own append — and *is* seen by every later one.

        **This row exists because the Rust walk got it wrong, and a reviewer found
        it, not a test.** The first version built the node and then called
        `nodelist.call_method1("append", …)`, which looks the method up *after* the
        build, so it uses the replacement for the very append the original was bound
        to serve. Measured before the fix: Python 0 calls, native 1 call. The two
        paths agreed on all 48 corpus sources either way, which is why the parity row
        could not see it — the divergence needs a factory that mutates the class
        mid-build, and no corpus source does.

        **The engine's own `NodeList`, not `django.template.base.NodeList`.** They are
        different classes (`django_rust_templates.engine.base`), and patching the
        Django one makes this row pass on both paths for a reason that has nothing to
        do with the ordering — which is how the first draft of the probe was fooled.
        """
        nodelist_cls = materialiser.NodeList
        # **Presence is remembered, not just the value.** `append` is inherited from
        # `list` and is *not* in `NodeList.__dict__`, so saving it and assigning it
        # back is not a restoration: it leaves a real class attribute behind, on a
        # class every compiled template in the process shares. A round-2 review
        # measured exactly that leak. The same shape as `_register` in
        # `tests/test_materialise.py`, and for the same reason.
        had_append = "append" in nodelist_cls.__dict__
        real_append = nodelist_cls.append
        real_text = materialiser.NODE_CLASSES["TextNode"]

        for native, source in ((False, "hello"), (True, "world")):
            # Two sources rather than one, so that a compiled-template cache — if
            # one is ever added between `compile_template` and here — cannot let the
            # second branch reuse the first branch's tree and never call the factory.
            replacements = []

            def recorder(self, node):
                replacements.append(node)
                return real_append(self, node)

            def rebinding_text(*args, **kwargs):
                node = real_text(*args, **kwargs)
                nodelist_cls.append = recorder
                return node

            materialiser.NODE_CLASSES["TextNode"] = rebinding_text
            try:
                with self.subTest(native=native, source=source):
                    _tree(source, native=native)
                    self.assertEqual(
                        replacements,
                        [],
                        "append was looked up after the node was built",
                    )
            finally:
                if had_append:
                    nodelist_cls.append = real_append
                else:
                    del nodelist_cls.append
                materialiser.NODE_CLASSES["TextNode"] = real_text

        # The restoration is pinned in both directions, because a cleanup that leaves
        # a class attribute behind is invisible to every assertion above it — the row
        # still passes, and the next test in the process inherits the patch.
        self.assertEqual("append" in nodelist_cls.__dict__, had_append)
        self.assertIs(nodelist_cls.append, real_append)
        self.assertTrue(hasattr(nodelist_cls, "append"))

    def test_every_registry_entry_is_exercised_by_the_corpus(self):
        # A structural check and not a count: a count pins churn, while this
        # fails for exactly one reason — a node class no template here builds.
        #
        # **No exclusion set, and it was not obvious.** An earlier draft carved
        # out `{"HostNode", "TemplateNode"}`, on the reasoning that a reserved name
        # and the root class could not be built. Both are in fact absent from
        # `NODE_CLASSES` — measured, `HostNode in NODE_CLASSES` is `False`, and
        # `parser.py` says why in as many words: it "is not a class", it is "a
        # reserved *name*". So the carve-out excluded nothing, and an exclusion
        # that cannot fire is the same green bulb as no check at all. Deleting it
        # leaves a predicate with no exceptions in it.
        built = set()
        for _label, source in materialiser_corpus.SOURCES:
            for node in _collect(_tree(source)):
                built.add(type(node).__name__)
        unexercised = sorted(
            name for name in materialiser.NODE_CLASSES if name not in built
        )
        self.assertEqual(
            unexercised,
            [],
            "add a template to materialiser_corpus.TAG_SOURCES for each of these",
        )


class SwitchTests(unittest.TestCase):
    """The switch reads a bool or nothing, and says which it got.

    `unwrap_or(false)` was the first version and it is wrong twice over: it
    swallows a genuine extraction failure as if it were a missing attribute, and
    it makes a typo in the constant's name read as "off" instead of failing.
    """

    def test_a_missing_constant_reads_as_off(self):
        # **The control is built before the deletion, and that is not stylistic.**
        # `_tree(..., native=False)` goes through `with_native`, which does a bare
        # `materialiser.USE_NATIVE_MATERIALISER` read to save and restore it — so
        # asking for the Python path *after* deleting the constant raises
        # `AttributeError` from the harness and never reaches the assertion. An
        # earlier draft did exactly that and would have failed on correct code.
        control = fingerprint(_tree("{{ a }}", native=False))
        real = materialiser.USE_NATIVE_MATERIALISER
        del materialiser.USE_NATIVE_MATERIALISER
        try:
            # `native=None` leaves the switch alone, so this is the module with no
            # constant at all — which the Rust read must treat as off.
            self.assertEqual(fingerprint(_tree("{{ a }}")), control)
        finally:
            materialiser.USE_NATIVE_MATERIALISER = real

    def test_a_present_but_not_boolean_constant_is_loud(self):
        real = materialiser.USE_NATIVE_MATERIALISER
        materialiser.USE_NATIVE_MATERIALISER = "yes"
        try:
            with self.assertRaises(TypeError):
                _tree("{{ a }}")
        finally:
            materialiser.USE_NATIVE_MATERIALISER = real

    def test_the_native_branch_does_not_call_python_materialise(self):
        """The one row here that separates the branches instead of comparing them.

        **Equality cannot say which branch ran, and until this row exists nothing
        does.** Every other row in this file passes whether or not the Rust walk is
        ever entered, because on both sides of the switch the tree is built the same
        way — the Python walk and, before the walk exists, the Python walk again.
        Booby-trapping the Python `materialise` makes dispatch observable: on the
        Python path it must raise, and on the native path it must not be reached.

        It works only because there is exactly one `parser` module in the process.
        Patching this module's attribute is what the Rust side looks up
        (`tree.getattr("materialise")`), so a second module object under the same name
        would leave this row passing while the real path went unpatched — the same
        trap the loader repair in Task 2 closed.
        """

        def explode(*args, **kwargs):
            raise AssertionError("the Python walk was reached through the native branch")

        real = materialiser.materialise
        materialiser.materialise = explode
        try:
            with self.assertRaises(AssertionError):
                _tree("{{ a }}", native=False)
            native = fingerprint(_tree("{{ a }}", native=True))
        finally:
            materialiser.materialise = real
        # Both branches agree, and the control is taken *after* the patch is lifted
        # so that the Python side is not the same broken call that just raised.
        self.assertEqual(native, fingerprint(_tree("{{ a }}", native=False)))


class RegistryOrderTests(unittest.TestCase):
    """The registry is read *before* the children, and the review is why.

    `materialise_node` does `cls = NODE_CLASSES[class_name]` at
    `python/django_rust_templates/engine/parser.py:347` and builds the children
    at `python/django_rust_templates/engine/parser.py:372`. So a factory that
    rebinds the registry while a *child* is built is observed by that child's
    siblings and by anything deeper, and **not** by the parent whose class was
    already captured. A walk that read the registry after its children would build
    a different tree, and the difference is invisible to a fingerprint — the two
    nodes have the same class name.

    The fixture is awkward on purpose, and two earlier drafts of it did not run:
    `TextNode.__init__(self, s)` takes the text as its one argument, so `real()`
    with no arguments is a `TypeError`; and `Marker` has to subclass the real
    `IfNode` *class* (`class Marker(real_if)`), not `type(real_if())`, because the
    claim is "the parent built the class the registry held at the top", and a
    freshly constructed instance is a different thing from the class.

    **The `native=True` row is a regression guard rather than a failing-first
    test.** It already passes with the Task 3 walk in place, because Task 3 hands
    each top-level node to `materialise_node` whole, and *that* reads the registry
    in Python's order. It earns its keep from Task 4 on, when `materialise_one`
    builds the parent in Rust and is the only thing left holding the ordering.
    What proves it discriminates is the mutation, not the order it was written
    in: with `tree.getattr("NODE_CLASSES")` moved below the child loop this row
    goes red and `test_the_python_path_is_the_control` stays green.
    """

    def test_a_rebind_during_a_child_is_not_seen_by_the_parent(self):
        real_text = materialiser.NODE_CLASSES["TextNode"]
        real_if = materialiser.NODE_CLASSES["IfNode"]

        class Marker(real_if):
            """Distinguishable from `real_if` by identity of the *class*."""

        rebinds = []

        def rebinding_text(*args, **kwargs):
            # The text is passed by keyword (`cls(**kwargs)`), so this is the
            # shape the walk actually calls and not a convenience.
            node = real_text(*args, **kwargs)
            if materialiser.NODE_CLASSES.get("IfNode") is not Marker:
                materialiser.NODE_CLASSES["IfNode"] = Marker
                rebinds.append(True)
            return node

        materialiser.NODE_CLASSES["TextNode"] = rebinding_text
        try:
            tree = _tree("{% if a %}x{% endif %}", native=True)
        finally:
            # Restored from locals captured at the top of this method. An earlier
            # draft restored from a `setUp` that this class does not have, which
            # is a `NameError` rather than a passing row.
            materialiser.NODE_CLASSES["TextNode"] = real_text
            materialiser.NODE_CLASSES["IfNode"] = real_if

        self.assertTrue(rebinds, "the fixture never rebound the registry")
        self.assertIs(type(tree[0]), real_if, "the parent read the registry after its children")

    def test_the_python_path_is_the_control(self):
        # If this fails the fixture is wrong, not the walk: the ordering under
        # test is one `materialise_node` already has, so the same rebind must
        # leave it alone too.
        real_text = materialiser.NODE_CLASSES["TextNode"]
        real_if = materialiser.NODE_CLASSES["IfNode"]

        class Marker(real_if):
            pass

        rebinds = []

        def rebinding_text(*args, **kwargs):
            node = real_text(*args, **kwargs)
            materialiser.NODE_CLASSES["IfNode"] = Marker
            rebinds.append(True)
            return node

        materialiser.NODE_CLASSES["TextNode"] = rebinding_text
        try:
            tree = _tree("{% if a %}x{% endif %}", native=False)
        finally:
            materialiser.NODE_CLASSES["TextNode"] = real_text
            materialiser.NODE_CLASSES["IfNode"] = real_if

        self.assertTrue(rebinds)
        self.assertIs(type(tree[0]), real_if)


class UnknownClassTests(unittest.TestCase):
    """An unknown class name raises before any child is built.

    Two claims, and the second is the one the Rust arm can get wrong. Raising is
    easy; raising *before the children exist* is what a walk that built its
    subtree first would not do, and the observable difference is that the child's
    constructor never ran.
    """

    def test_the_error_names_the_class_and_the_registry(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._unknown_node_class("NoSuchNode")
        self.assertIn("NoSuchNode", str(caught.exception))
        self.assertIn("NODE_CLASSES", str(caught.exception))

    def test_the_error_comes_before_any_child_is_built(self):
        built = []
        real_text = materialiser.NODE_CLASSES["TextNode"]

        def recording_text(*args, **kwargs):
            built.append(kwargs)
            return real_text(*args, **kwargs)

        materialiser.NODE_CLASSES["TextNode"] = recording_text
        try:
            # An unknown *parent* whose child is a perfectly good `TextNode`. The
            # child is reachable from the descriptor, so a walk that built its
            # children before consulting the registry would run `recording_text`
            # and only then fail — leaving a constructed node for a parent that
            # does not exist.
            #
            # **The child is a real node descriptor, and an earlier draft of this
            # row is why that is spelled out.** It wrote `(("nodelist", ("hello",)),)`
            # — a bare string where a *nodelist* descriptor goes. A nodelist is a
            # `(nodes, contains_nontext)` pair whose first element is a tuple of
            # node descriptors (`nodelist_desc_to_py`,
            # `crates/template-py/src/lib.rs:1104`), and a node descriptor is
            # `(class_name, attrs, nodelists, token)`. With the
            # malformed form, a walk that built children *first* raised
            # `ValueError: not enough values to unpack` while iterating `"hello"` —
            # before `recording_text` could run — so `built == []` held for exactly
            # the ordering the row exists to reject. The malformed fixture was
            # indistinguishable from the fix.
            child = ("TextNode", (("s", "hello"),), (), None)
            desc = ("NoSuchParent", (), (("nodelist", ((child,), False)),), None)
            with self.assertRaises(LookupError) as caught:
                materialiser.materialise_node(desc)
            self.assertIn("NoSuchParent", str(caught.exception))
        finally:
            materialiser.NODE_CLASSES["TextNode"] = real_text
        self.assertEqual(built, [], "the parent's children were built before the registry was read")

    def test_the_native_walk_raises_before_any_child_is_built(self):
        """The same claim, driven through the *Rust* walk rather than Python.

        The row above is a good row and it is not evidence for the arm it sits
        beside: it calls `materialise_node`, so it pins Python's order and says
        nothing about `materialise_one`'s. The Rust arm's unknown-class path is
        `loud(tree, Bridge::UnknownNodeClass, …)`, and until this row existed
        **nothing reached it at all** — a walk that never consulted the registry
        and a walk that consulted it after its children both passed every other
        row in this file.

        The fixture is the review's: an ordinary `{% if a %}` whose *own* class is
        removed from the registry, with the body's `TextNode` factory recording.
        If the registry read moves below the child loop, `recording_text` runs and
        `built` is non-empty; the template need not be a malformed one, because
        the point is the ordering and not the descriptor shape.

        **A regression guard, like `RegistryOrderTests`:** at Task 3 this passes
        for Python's order, since the whole node is still handed to
        `materialise_node`. The mutation is what proves it discriminates.
        """
        built = []
        real_text = materialiser.NODE_CLASSES["TextNode"]
        real_if = materialiser.NODE_CLASSES.pop("IfNode")

        def recording_text(*args, **kwargs):
            built.append(kwargs)
            return real_text(*args, **kwargs)

        materialiser.NODE_CLASSES["TextNode"] = recording_text
        try:
            with self.assertRaises(LookupError) as caught:
                _tree("{% if a %}x{% endif %}", native=True)
            self.assertIn("IfNode", str(caught.exception))
            self.assertIn("NODE_CLASSES", str(caught.exception))
        finally:
            materialiser.NODE_CLASSES["TextNode"] = real_text
            materialiser.NODE_CLASSES["IfNode"] = real_if
        self.assertEqual(built, [], "the parent's children were built before the registry was read")


class RegistrySubscriptionTests(unittest.TestCase):
    """`NODE_CLASSES[class_name]`, not `dict.__getitem__`.

    `materialise_node` subscribes — `cls = NODE_CLASSES[class_name]` at
    `python/django_rust_templates/engine/parser.py:347` — and subscription goes
    through the *type's* `__getitem__`. `NODE_CLASSES` is a plain dict in this tree,
    so the two spellings agree on every input the corpus reaches; they stop
    agreeing the moment it is a dict **subclass** that overrides `__getitem__`,
    which is exactly the thing a project is allowed to substitute. PyO3's
    `PyDict::get_item` reads the concrete dict storage and never calls the
    override; `Bound::<PyAny>::get_item` is `PyObject_GetItem` and does.

    **Measured on the round-1 build, which is why the rust walk no longer casts the
    registry down:** with a subclass whose `__getitem__` answers with a substituted
    class for `"TextNode"`, the Python path built the substitution and the native
    path built the real node.

    **Why nothing in this file caught that, stated correctly.** An earlier draft of
    this docstring said no fingerprint could show it "because the names are
    identical". Both halves are wrong. `Tagging` is a *subclass*, so it is a distinct
    class object with its own `__name__`, and this file's `fingerprint` carries node
    classes **whole** — `test_two_node_classes_with_one_name_are_told_apart` is the
    row that proves it, and it exists precisely so two classes of one name are told
    apart. A fingerprint *would* have shown the divergence. What let it through was
    that **every other row in this file uses the real `NODE_CLASSES`**, and the corpus
    never substitutes one; the defect needed a row that swapped the mapping out, and
    there was none. That is the gap this class closes, and the two rows below are
    direct assertions on subscription rather than a fingerprint comparison, so they
    name the mechanism rather than the symptom.
    """

    def _substituted(self, native):
        """Build `"hello"` with `NODE_CLASSES` replaced by an overriding subclass.

        Returns the keys the override was asked for and the class the node came
        out as, so a row can distinguish "the override ran and returned the real
        class" from "the override was never consulted" — which is the whole
        difference between subscription and `PyDict::get_item`, and the reason the
        second is not good enough.
        """
        seen = []
        real_registry = materialiser.NODE_CLASSES
        real_text = real_registry["TextNode"]

        class Tagging(real_text):
            """A *subclass* of the real `TextNode`, so a distinct class object.

            Reachable from the registry only through the override — nothing else in
            the tree hands it out — which is what makes `type(tree[0])` a witness to
            whether `__getitem__` ran.
            """

        class Overriding(dict):
            def __getitem__(self, key):
                seen.append(key)
                if key == "TextNode":
                    return Tagging
                return super().__getitem__(key)

        materialiser.NODE_CLASSES = Overriding(real_registry)
        try:
            tree = _tree("hello", native=native)
        finally:
            materialiser.NODE_CLASSES = real_registry
        return seen, Tagging, type(tree[0])

    def test_the_native_walk_reaches_an_overridden_getitem(self):
        seen, tagging, built = self._substituted(native=True)
        self.assertIn("TextNode", seen, "the registry was read without calling __getitem__")
        self.assertIs(built, tagging, "the override's answer was ignored")

    def test_the_native_walk_lets_a_non_keyerror_out_of_getitem(self):
        # `materialise_node` catches `KeyError` and nothing else, so a `__getitem__`
        # that raises something else must not be rewritten into the registry
        # message. A walk that caught every `PyErr` there would report
        # "no materialiser for node class 'TextNode'" for a `RuntimeError` from a
        # registry that plainly has one.
        real_registry = materialiser.NODE_CLASSES

        class Raising(dict):
            def __getitem__(self, key):
                if key == "TextNode":
                    raise RuntimeError("the registry is closed")
                return super().__getitem__(key)

        materialiser.NODE_CLASSES = Raising(real_registry)
        try:
            with self.assertRaises(RuntimeError) as caught:
                _tree("hello", native=True)
        finally:
            materialiser.NODE_CLASSES = real_registry
        self.assertIn("the registry is closed", str(caught.exception))

    def test_the_python_path_is_the_control(self):
        # If this fails the fixture is wrong, not the walk: Python's subscription
        # is the behaviour the native one has to match, and it reaches the override
        # by construction.
        seen, tagging, built = self._substituted(native=False)
        self.assertIn("TextNode", seen)
        self.assertIs(built, tagging)


class ConstructionOrderTests(unittest.TestCase):
    """The three contract orderings in `materialise.rs`, through **both** branches.

    `materialise.rs` names three orderings as contracts and says each "gets a row
    when the task that could break it lands". Task 3 pinned the first one only
    indirectly, in the root-list loop. Task 4 is the task that builds a parent in
    Rust, so this is where the other two stop being Python's promise and become
    this module's:

    2. children are materialised **before** the parent is constructed;
    3. `token` and `origin` are attached **after** construction, and not passed to it.

    **Contract 2's second half — attributes decoded after the children exist — runs
    through Task 4 already, by way of the fallback.** `AttrValue::NodeListRef` is not
    one of the primitive arms, so `attr_to_py` hands it to `_decode_value` — with the
    `child_lists` this function built. So the ordering is not deferred to Task 5's arm;
    Task 5 inherits it, and the third row below pins it now. **An earlier draft of this
    docstring said a row here would be "a row about nothing" and that was wrong**, which
    is worth recording because the reasoning looked right: the `NodeListRef` arm is
    Task 5's, but the *value* reaches `_decode_value` today.

    **Contract 3 gets one row, and a second one was written and deleted.** The probe
    that reads `self.token` inside `__init__` looks like the natural way to express
    "attached after", and it cannot fail against this walk. The narrow reason matters:
    an instance does exist while `__init__` runs, so the claim is not that there is
    nothing to probe — it is that **the Rust path is handed the instance only once
    `factory.call` has returned**, and holds no reference to it before then, so no edit
    to `materialise_one` can have put a value on it earlier. What *is* falsifiable is the
    call, and that is what the surviving row asserts.

    Every row runs `native in (True, False)` as `subTest`s rather than twice in two
    methods: the claim is that the two paths agree, and a row that only ever ran one
    of them would still pass on the day they stopped agreeing.
    """

    def test_the_children_exist_before_the_parent_is_constructed(self):
        # `{% for %}` rather than `{% if %}` on purpose. `ForNode`'s body nodelist is
        # **named** (`nodelist_loop`), where `IfNode`'s is positional-only, so a walk
        # that constructed the parent first would hand `ForNode` an *empty* nodelist
        # and get a clean, informative failure — `("parent", 0)` — instead of the
        # `TypeError` `IfNode` gives for a missing `conditions_nodelists`. A row whose
        # mutation is a `TypeError` proves the reordering happened; a row whose
        # mutation is the wrong *number* proves the ordering the contract is about.
        for native in (True, False):
            with self.subTest(native=native):
                events = []
                real_for = materialiser.NODE_CLASSES["ForNode"]
                real_text = materialiser.NODE_CLASSES["TextNode"]

                class Watching(real_for):
                    def __init__(self, **kwargs):
                        # Read the body *inside* the constructor, which is what
                        # `ExtendsNode.__init__` does and what makes the ordering
                        # load-bearing rather than cosmetic.
                        events.append(("parent", len(kwargs["nodelist_loop"])))
                        super().__init__(**kwargs)

                def watching_text(*args, **kwargs):
                    events.append(("child", None))
                    return real_text(*args, **kwargs)

                materialiser.NODE_CLASSES["TextNode"] = watching_text
                materialiser.NODE_CLASSES["ForNode"] = Watching
                try:
                    _tree("{% for x in y %}z{% endfor %}", native=native)
                finally:
                    materialiser.NODE_CLASSES["TextNode"] = real_text
                    materialiser.NODE_CLASSES["ForNode"] = real_for

                self.assertEqual(
                    events,
                    [("child", None), ("parent", 1)],
                    "the parent was constructed before its children were built",
                )

    def test_the_attributes_resolve_against_a_populated_child_list(self):
        """Contract 2's second half, through `attr_to_py`'s fallback arm.

        `{% if %}` is the shape that can see this at all: its branch nodelists are
        **positional** — empty name, not passed to `cls` — so `IfNode` reaches them
        through `conditions_nodelists`, which carries `AttrValue::NodeListRef` for
        each branch rather than the list itself (`crates/template-core/src/parser/
        tags/if.rs:121`). Decoded against a `child_lists` that is still empty, that
        reference is not an empty answer but a loud one — measured, with the mutation
        below: `LookupError: <NodeListRef 0> is out of range: the enclosing node has
        0 nodelist(s)`.

        **`NodeListRef` is Task 5's arm and the ordering is Task 4's**, which is why
        this row belongs here: the value travels `attr_to_py` → `fallback_attr` →
        `Bridge::DecodeValue` → `_decode_value` with the `child_lists` built above it,
        and the fallback is not a reason to leave the ordering unpinned. Task 5
        replaces the *arm* and inherits the *row*.

        The Python path is the control throughout, and the same claim is asserted one
        level down in `tests/test_materialise.py:464`
        (`test_a_nodelist_ref_resolves_because_children_are_built_first`), which calls
        `materialise_node` with a hand-built descriptor. This row is that claim driven
        through `_native.compile_template`, which is the only way to reach the Rust
        walk.
        """
        for native in (True, False):
            with self.subTest(native=native):
                tree = _tree("{% if a %}x{% endif %}", native=native)

                self.assertIs(type(tree[0]), materialiser.NODE_CLASSES["IfNode"])
                condition, branch = tree[0].conditions_nodelists[0]
                self.assertTrue(hasattr(condition, "eval"), "the condition half is not a condition")
                # The witness: a real, *populated* nodelist, not the empty one an
                # attributes-before-children walk would have resolved to — or failed
                # on outright, which is the measured failure above.
                self.assertIsInstance(branch, NodeList)
                self.assertEqual(len(branch), 1, "the reference resolved to an empty child list")
                self.assertIs(type(branch[0]), materialiser.NODE_CLASSES["TextNode"])

    def test_the_token_and_origin_are_attached_after_the_constructor_runs(self):
        for native in (True, False):
            with self.subTest(native=native):
                seen = []
                real_text = materialiser.NODE_CLASSES["TextNode"]

                class Probing(real_text):
                    def __init__(self, *args, **kwargs):
                        # `Node` declares `token = None` as a *class* attribute, so a
                        # probe built on `hasattr(self, "token")` is True on a bare
                        # instance and cannot fail. What the walk cannot do is have the
                        # values present when `cls(**kwargs)` is called — and the way to
                        # see that is to look at what it passes, read from inside the
                        # constructor the way the walk's own call would run it.
                        seen.append(sorted(kwargs))
                        super().__init__(*args, **kwargs)

                materialiser.NODE_CLASSES["TextNode"] = Probing
                try:
                    tree = _tree("hello", native=native)
                finally:
                    materialiser.NODE_CLASSES["TextNode"] = real_text

                # Contract 3 stated as a fact about the *call*: `token` and `origin`
                # are the walk's to attach afterwards, never constructor arguments.
                # Passing them is the realistic edit — one `kwargs.set_item` in the
                # attribute loop — and it is what this row refuses. A row that instead
                # read `self.token` *inside* `__init__` would assert something no edit
                # to the walk can break: an instance does exist while `__init__` runs,
                # but the walk is handed it only once `factory.call` has returned and
                # holds no reference before then. That row was written, found
                # unfalsifiable and dropped, which is the standard Step 2 holds these to.
                self.assertEqual(seen, [["s"]], "the constructor was handed more than the descriptor's attributes")
                # And the pair really is on the node once the walk is done, so this is
                # "attached later" rather than "never attached".
                self.assertIsInstance(tree[0].token, Token)
                self.assertIs(tree[0].origin, render.UNKNOWN_ORIGIN)



class CycleIdentityTests(unittest.TestCase):
    """The second `{% cycle c %}` must be the *same object* as the first.

    `do_cycle` returns the identical node and `CycleNode.render` keys
    `context.render_context` by that object, so two equal-but-distinct nodes cycle
    independently — output that is right on the first iteration and wrong on the
    second. **A fingerprint cannot see this**: the two nodes fingerprint
    identically. That is why identity has its own rows.
    """

    def test_the_second_mention_is_the_first_object(self):
        tree = _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=True)
        self.assertIs(tree[0], tree[1])

    def test_the_python_path_still_shares_the_object(self):
        # The control. If this fails the fixture is wrong, not the walk.
        tree = _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=False)
        self.assertIs(tree[0], tree[1])

    def test_the_shared_object_carries_the_last_occurrence_token(self):
        # `extend_nodelist` does `node.token = token` at *every* append, so the
        # shared object ends up carrying the last occurrence's token and every
        # position reads that one.
        tree = _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=True)
        self.assertEqual([node.token.contents for node in tree], ["cycle c", "cycle c"])

    def _cycle_with_the_node_arm_trapped(self, native):
        """Build the two-cycle fixture with `materialise_node` replaced by a trap.

        `materialise_node` is the one entry point `fallback_node` calls, and
        `call_bridge` reads it back off the module by name on every call — which is
        what makes it trappable, and what makes "the fallback did it" an error
        instead of a pass.
        """

        def refuse(*_args, **_kwargs):
            raise AssertionError("the walk fell back to `materialise_node`")

        real = materialiser.materialise_node
        materialiser.materialise_node = refuse
        try:
            return _tree("{% cycle 'a' 'b' as c %}{% cycle c %}", native=native)
        finally:
            materialiser.materialise_node = real

    def test_the_native_walk_shares_it_without_the_python_node_arm(self):
        # **The row that makes the three above mean something.** Identity alone
        # proves nothing about *which* walk produced it: the Python walk shares the
        # object too, so a fallback that rebuilt every node would leave them all
        # green — the gap the plan's Task 5 note names, and the reason `mod
        # malformed` carries slot rows on the Rust side.
        #
        # If the native branch is not live at all, `compile_template` takes the Python
        # arm, which reaches the same name — so this row is green only when the native
        # walk ran *and* did not fall back for either cycle.
        tree = self._cycle_with_the_node_arm_trapped(native=True)
        self.assertIs(tree[0], tree[1])

    def test_the_trap_fires_on_the_python_path(self):
        # The control, and the row that keeps the one above from being vacuous: a
        # trap that never fires is an assertion about nothing. The Python path *does*
        # reach `materialise_node`, so the same trap on it must raise.
        with self.assertRaises(AssertionError):
            self._cycle_with_the_node_arm_trapped(native=False)


class HostNodeIdentityTests(unittest.TestCase):
    """Review Focus item 1. Identity, and the token that must survive it.

    A host tag's node is built by Python during the parse and stored in the
    reference table. The walk must hand back **that object** — not a copy, not a
    new one — with this occurrence's token attached.

    **The claim is identity, so the assertion is `assertIs`**, and to make it the
    test has to know which object the host built — which means capturing it at
    construction. `SimpleBlockNode` is looked up as a module global by
    `compile_func`, so a subclass installed there records every one the tag builds.
    """

    def _held_node(self, source, native):
        """The object the host's tag factory built, and the tree that should hold it."""
        held = []

        class Held(SimpleBlockNode):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                held.append(self)

        real = library_mod.SimpleBlockNode
        library_mod.SimpleBlockNode = Held
        try:
            tree = _tree(source, engine=_hosting_engine(), native=native)
        finally:
            library_mod.SimpleBlockNode = real
        self.assertEqual(len(held), 1, "the fixture built no host node")
        return held[0], tree

    def test_the_walk_hands_back_the_hosts_own_object(self):
        held, tree = self._held_node("{% wrap %}body{% endwrap %}", native=True)
        self.assertIs(tree[0], held)

    def test_the_host_node_carries_this_occurrences_token_and_origin(self):
        held, tree = self._held_node("{% wrap %}body{% endwrap %}", native=True)
        # `extend_nodelist` attaches the token to a host descriptor exactly as it
        # does to any other block node, so materialisation has to re-attach it. A
        # bare `return resolve_ref(index)` hands back a node carrying whatever token
        # the *host* left on it — `None` — and a user tag's node is then the one
        # shape that renders tokenless in the debug traceback path.
        self.assertIsNotNone(held.token)
        self.assertEqual(held.token.contents, "wrap")
        self.assertIsNotNone(held.origin)
        self.assertIs(tree[0].token, held.token)

    def test_the_python_path_shares_the_object_too(self):
        # The control. If this fails the fixture is wrong, not the walk — the Python
        # materialiser returns `resolve_ref(index)` for a host descriptor, so
        # identity is the behaviour being preserved rather than introduced.
        held, tree = self._held_node("{% wrap %}body{% endwrap %}", native=False)
        self.assertIs(tree[0], held)


class ContainerShapeTests(unittest.TestCase):
    """A tuple is not a list, and a dict's order is the repeated-key rule.

    **The first three rows are contract rows**: they cannot fail while the container
    arms are on the fallback, because the fallback decoded them correctly. They
    exist because a container that "renders identically" is still the wrong object,
    and the fingerprint tells them apart. **The last two are the movers.**
    """

    def test_if_conditions_are_tuples(self):
        tree = _tree("{% if a %}x{% endif %}", native=True)
        for pair in tree[0].conditions_nodelists:
            self.assertIsInstance(pair, tuple)
            self.assertNotIsInstance(pair, list)

    def test_with_extra_context_is_a_dict(self):
        tree = _tree("{% with a=1 %}{{ a }}{% endwith %}", native=True)
        self.assertIsInstance(tree[0].extra_context, dict)
        self.assertEqual(list(tree[0].extra_context), ["a"])

    def test_a_repeated_keyword_keeps_first_position_and_last_value(self):
        # `token_kwargs` does `kwargs[key] = ...` (`base.py:1202`), so a repeated
        # key keeps its first position and takes its last value. `dict(pairs)`
        # reproduces that; a list of pairs does not.
        #
        # The value is a `FilterExpression`, not `3`: `Variable.__init__` stores
        # the expression and resolution happens at render time, so the claim is
        # on its `.token`.
        tree = _tree("{% include 'p.html' with a=1 b=2 a=3 %}", native=True)
        self.assertEqual(list(tree[0].extra_context), ["a", "b"])
        self.assertEqual(tree[0].extra_context["a"].token, "3")

    def test_a_discarded_duplicate_keyword_is_never_decoded(self):
        """The dedup precedes the decode, and the survivors are decoded in Python's order.

        The row above checks the finished dict, which is the same either way. This
        one checks **what was decoded on the way there**, and it is the row that
        caught the bug: an arm that recursed entry-by-entry would decode the `1`
        that the repeated `a` discards, so a helper raising on it would make this
        walk fail where Python succeeds — on a template that renders.

        Measured before the fix: Python recorded `["'p.html'", "3", "2"]` and native
        recorded `["'p.html'", "1", "2", "3"]`.
        """

        def decoded_tokens(native):
            tokens = []
            real = materialiser._materialise_filter_expression

            def recording(desc, *rest):
                tokens.append(desc.token)
                return real(desc, *rest)

            materialiser._materialise_filter_expression = recording
            try:
                _tree("{% include 'p.html' with a=1 b=2 a=3 %}", native=native)
            finally:
                materialiser._materialise_filter_expression = real
            return tokens

        self.assertEqual(decoded_tokens(True), decoded_tokens(False))

    def test_no_container_reaches_the_python_decoder(self):
        """The mover: `List`, `Tuple` and `Map` are built in Rust, not handed over.

        The three contract rows above pin the *contract*, and they hold on either
        path; this one is the census claim. `_decode_value` is reached from the Rust
        walk only *through* the helpers Task 7 keeps — `_decode_value` is what
        `_materialise_filter_expression` calls for its operands and what
        `_decode_condition` calls for its operands — never for an attribute. A
        `list`, `tuple` or `dict` arriving there is a container that fell back,
        whatever it then built.

        The record is therefore not empty and the row is not vacuous: the leaves
        still arrive, and `assertNotEqual` below is what says so.
        """
        seen = []
        real = materialiser._decode_value

        def recording(value, *args, **kwargs):
            seen.append(type(value).__name__)
            return real(value, *args, **kwargs)

        materialiser._decode_value = recording
        try:
            for source in (
                "{% if a %}x{% endif %}",
                "{% with a=1 %}{{ a }}{% endwith %}",
                "{% include 'p.html' with a=1 b=2 a=3 %}",
                "{% for x in y %}{{ x }}{% endfor %}",
            ):
                _tree(source, native=True)
        finally:
            materialiser._decode_value = real

        self.assertNotEqual(seen, [], "the walk never reached the decoder; fixture moved")
        self.assertEqual(
            [name for name in seen if name in ("list", "tuple", "dict")],
            [],
            "a container attribute was handed to the Python decoder",
        )


class ExpressionArmTests(unittest.TestCase):
    """Three of the four arms, driven by a template. The fourth cannot be.

    `_materialise_variable` uses `__new__` plus slot assignment rather than the
    constructor, because calling `Variable(token)` would parse the token a second
    time in Python and discard Rust's parse — including the error messages and the
    token each carries, which are Rust's to produce. So the arm hands it the *wire*
    object, exactly as the Python decoder does.

    **`AttrValue::Variable` is not one of the four, and no template reaches it.**
    Every producer of a `Variable` — `variable_of` in `filter_expr.rs`, and so both
    of `FilterExprDesc`'s operands — puts it *inside* a `FilterExprDesc`, so the
    attribute `attr_to_py` is handed is always the `FilterExpr`. The arm exists
    because the match is exhaustive, and its row is `mod malformed` in
    `materialise.rs` for the same reason the reference arms' rows are. Adding a
    template-driven row here would be a row that passes for the filter-expression
    arm's reason; measured — replacing the `Variable` arm with a `panic!` reddens
    no row in this suite.

    **These rows pin the contract; the movers are Task 8's census.** An arm that
    dispatched here but built the wrong object would be red below; an arm that
    stopped dispatching at all is what `Bridge::ALL` and
    `test_every_reachable_bridge_is_reached_by_the_corpus` catch.
    """

    def test_a_filter_expressions_variable_keeps_its_rust_parse(self):
        # The *filter-expression* arm, not the `Variable` arm: `{{ a.b.c }}` is a
        # `FilterExpression` attribute, and it is `_materialise_filter_expression`
        # that builds the `Variable` nested in it.
        tree = _tree("{{ a.b.c }}", native=True)
        self.assertEqual(tree[0].filter_expression.var.lookups, ("a", "b", "c"))

    def test_a_filter_expression_keeps_its_function_and_args(self):
        tree = _tree("{{ a|default:'x' }}", native=True)
        # `filters` is `[(filter_func, args)]` and `args` is `[(is_var, operand)]`
        # — two elements per pair, and the first arg is a *constant* here.
        ((func, args),) = tree[0].filter_expression.filters
        self.assertEqual(getattr(func, "__name__", None), "default")
        self.assertEqual([is_var for is_var, _arg in args], [False])

    def test_a_condition_operator_survives_the_walk(self):
        tree = _tree("{% if a == b %}x{% endif %}", native=True)
        condition = tree[0].conditions_nodelists[0][0]
        self.assertEqual(
            fingerprint(condition),
            fingerprint(
                _tree("{% if a == b %}x{% endif %}", native=False)[0].conditions_nodelists[0][0]
            ),
        )

    def test_a_blocktranslate_keeps_token_types(self):
        # `{% load i18n %}` first: `blocktranslate` is not a builtin, so the load
        # node is `tree[0]` and the block is the last node.
        tree = _tree(
            "{% load i18n %}{% blocktranslate %}Hi {{ name }}{% endblocktranslate %}",
            native=True,
        )
        self.assertEqual([token.token_type.name for token in tree[-1].singular], ["TEXT", "VAR"])


class MalformedDescriptorTests(unittest.TestCase):
    """Review Focus item 5. The messages have one spelling, and both walkers use it.

    None of these is reachable from a template — Django refuses the source first —
    so the rows drive the helpers directly. What they cannot show is that the
    *Rust* arms call them; that is `mod malformed` in `materialise.rs`, and Task 8's
    census.

    **Four messages, five sites**, and three of the sites are already pinned by
    `tests/test_materialise.py` — as *fragments*, which is why the rows below assert
    the complete strings beside them.
    """

    def test_an_empty_slot_names_the_slot(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._empty_slot_error("<NodeSlot 0>")
        self.assertEqual(
            str(caught.exception), "<NodeSlot 0> is an empty reference-table slot"
        )

    def test_a_missing_resolver_names_the_handle(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._missing_resolver_error("<NodeRef 3>")
        self.assertEqual(
            str(caught.exception),
            "<NodeRef 3> needs a resolve_ref callback; none was passed to materialise",
        )

    def test_a_missing_enclosing_node_names_itself_not_materialise(self):
        # The odd one out, and pinned as odd: this path is reached from
        # `_decode_value` directly — a filter argument, say — where there is no
        # enclosing node at all, so the message names `_decode_value` and not
        # `materialise`.
        with self.assertRaises(LookupError) as caught:
            materialiser._missing_child_lists_error("<NodeListRef 0>")
        self.assertIn("none were passed to _decode_value", str(caught.exception))

    def test_an_out_of_range_nodelist_ref_names_the_arity(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._nodelist_ref_out_of_range_error("<NodeListRef 2>", 1)
        self.assertEqual(
            str(caught.exception),
            "<NodeListRef 2> is out of range: the enclosing node has 1 nodelist(s)",
        )

    def test_the_host_arm_keeps_its_own_phrasing(self):
        # The host arm's empty-slot message says *which* descriptor, because the
        # failure is a broken table rather than a broken wire and the reader needs
        # to know which side to look at. Unifying it into the `NodeSlot` wording
        # would still pass `test_materialise.py`'s `assertIn("empty
        # reference-table slot")` while losing the distinction.
        with self.assertRaises(LookupError) as caught:
            materialiser._empty_slot_error("the HostNode descriptor's <NodeRef 0>")
        self.assertIn("the HostNode descriptor's", str(caught.exception))

    def test_the_python_decoder_uses_the_same_message(self):
        # The other half of "one spelling": these are not new strings added
        # *beside* `_decode_value`'s.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeSlot(0), None, None, None)
        self.assertEqual(
            str(caught.exception),
            "<NodeSlot 0> needs a resolve_ref callback; none was passed to materialise",
        )

    def test_an_empty_slot_over_a_real_table_is_loud(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeSlot(0), lambda index: None, None, None)
        self.assertEqual(
            str(caught.exception), "<NodeSlot 0> is an empty reference-table slot"
        )

    def test_a_nodelist_ref_with_no_enclosing_node_keeps_its_own_message(self):
        # The fifth site, driven through the decoder rather than the helper, so that
        # a helper wired to the wrong arm is red here.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(0))
        self.assertIn("none were passed to _decode_value", str(caught.exception))

    def test_an_out_of_range_ref_through_the_decoder_says_out_of_range(self):
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(3), None, None, [None])
        self.assertIn("is out of range", str(caught.exception))
        self.assertIn("1 nodelist(s)", str(caught.exception))

    def test_the_out_of_range_context_is_suppressed(self):
        # Asserted on the exception object, because `str()` is identical either
        # way and a traceback is not what a test can hold. The helpers *raise*
        # rather than return, which is why `from None` belongs inside
        # `_nodelist_ref_out_of_range_error` and not at its call site: that site
        # sits in an `except IndexError:`, so a `raise helper(...) from None`
        # there never runs — the helper raises first — and the `IndexError`
        # survives as `__context__`, printing `list index out of range` under a
        # message that has already said what went wrong.
        with self.assertRaises(LookupError) as caught:
            materialiser._decode_value(_native.NodeListRef(3), None, None, [None])
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)


# The Python entry points the Rust walk calls. **Kept in step with `Bridge` in
# `materialise.rs` by hand, and safe to keep that way**, because this list is not
# what makes "no unlisted bridge" true — `Bridge` is, and `the_bridge_list_is_pinned`
# in `materialise.rs`'s `mod malformed` is the row that reads it. This list's job is
# the opposite direction: to prove each of these is *reached*, so that a bridge that
# silently stopped firing is red rather than merely permitted.
#
# The previous draft had it make both claims, which is why it wrapped only the names
# below and then asserted `called <= _BRIDGES` — true of any list whatsoever.
_BRIDGES = {
    # Host nodes only: the object is the host's, and its descriptor validation
    # and error messages belong to the parse.
    "materialise_node",
    # Kept whole rather than reimplemented, because reimplementing means a second
    # `Variable.__new__` and a second `smartif`. Their *dispatch* is Rust's.
    "_materialise_filter_expression",
    "_decode_condition",
    # The token wire form has one spelling, shared with `_native.lex`.
    "_decode_token",
    # `{% verbatim %}`'s body is a `SafeString`.
    "mark_safe",
    # **Reached, but not through its own `Bridge` variant** — and this is the one
    # place the census cannot tell the two apart, so it is written down rather than
    # left to look like the walk dispatched it. `_materialise_operand` calls this for
    # every `FilterExpression` operand (`python/django_rust_templates/engine/parser.py:448`), so wrapping the *module
    # attribute* records it as reached on any template with a filter expression. The
    # Rust arm that names `Bridge::MaterialiseVariable` is a different thing and no
    # parser-produced tree reaches it: `variable_of`
    # (`template-core/src/parser/filter_expr.rs:250`) is the only constructor of
    # `AttrValue::Variable` and it puts the value inside a `FilterExprDesc`. That arm's
    # row is `mod malformed` in `materialise.rs`. Measured: replacing it with a
    # `panic!` leaves this suite at its one pre-existing failure and reddens only that
    # Rust row.
    "_materialise_variable",
}

# The bridges the corpus cannot reach, and why each is excluded rather than
# forgotten. Every one of them is an error path, so no valid template produces it —
# which is what `UnknownClassTests`, `MalformedDescriptorTests` and `mod malformed`
# cover instead.
_UNREACHABLE_BRIDGES = {
    # Raised by `UnknownClassTests`, dispatched in Rust from the registry read.
    "_unknown_node_class",
    # The reference-handle and arity errors, raised by `MalformedDescriptorTests`
    # and, for the dispatch itself, by `mod malformed`.
    "_missing_resolver_error",
    "_empty_slot_error",
    "_nodelist_ref_out_of_range_error",
}


def _record_bridge_calls(names):
    """Run the corpus native with `names` wrapped, and return the ones that fired.

    One implementation for both rows below, so that a wrapper that stopped
    recording cannot make one row quiet while the other stays green.

    Wrapping the module attribute is the instrument and not a proxy for it:
    `call_bridge` does `tree.getattr(bridge.name())?.call1(args)` — resolved per
    call, off the module — so the name bound here is the one the walk reaches.
    """
    called = set()
    saved = {}
    for name in names:
        real = getattr(materialiser, name)
        saved[name] = real

        def recorder(*args, _real=real, _name=name, **kwargs):
            called.add(_name)
            return _real(*args, **kwargs)

        setattr(materialiser, name, recorder)
    try:
        for _label, source in materialiser_corpus.SOURCES:
            _tree(source, native=True)
    finally:
        for name, real in saved.items():
            setattr(materialiser, name, real)
    return called


class FallbackCensusTests(unittest.TestCase):
    """What still goes through Python, recorded at the boundary.

    The walk moving to Rust is invisible in the tree — by construction the tree is
    identical — so it is measured at the boundary instead.

    **`_decode_value` is deliberately not the instrument.** An earlier draft wrapped
    it and asserted no calls, on the theory that any call meant an attribute still on
    the fallback. That is false: `_materialise_filter_expression` and
    `_decode_condition` call it for their operands, and both helpers are *kept on
    purpose*, so the count is non-zero on every template with a filter expression and
    the assertion would fail on correct code.

    The per-value fallback is proven gone **by the bridge enum** instead: Task 7
    deletes `Bridge::DecodeValue` and adds three, so `ALL` goes from eight to ten,
    which `the_bridge_list_is_pinned` reads. A fallback cannot call Python without a
    variant, so the *deletion* is what proves the fallback gone, and a substring
    search is not — the one this replaced would have rejected Task 7's own comment.

    **The count is not the proof, and an earlier draft of this docstring said it
    was.** Add a variant *with* its `name()` arm and omit it from `ALL`, and
    `the_bridge_list_is_pinned` stays green — the count derives from a hand-written
    list, as `ALL`'s own comment states. What the enum establishes is that no
    *argument to `call_bridge`* exists for a deleted capability; dispatch at runtime
    is established by mutation, per arm. The two are different claims and only the
    first is a property of the type.
    """

    def test_only_host_nodes_reach_the_python_node_materialiser(self):
        nodes = set()
        real = materialiser.materialise_node

        def recording_node(desc, *rest):
            nodes.add(desc[0])
            return real(desc, *rest)

        materialiser.materialise_node = recording_node
        try:
            for _label, source in materialiser_corpus.SOURCES:
                _tree(source, native=True)
        finally:
            materialiser.materialise_node = real

        self.assertTrue(
            nodes <= {materialiser.HOST_NODE},
            "these node classes are still on the fallback: "
            f"{sorted(nodes - {materialiser.HOST_NODE})}",
        )
        # And the corpus really does reach it, so the line above is not a statement
        # about the empty set: `{% url %}` and `{% static %}` are host tags, but a
        # corpus of `{% if %}` and `{{ var }}` alone would leave `nodes` empty and
        # this row green with nothing in it.
        self.assertTrue(nodes, "no host node was materialised; the census is vacuous")

    def test_every_reachable_bridge_is_reached_by_the_corpus(self):
        """The listed bridges all fire.

        `called == _BRIDGES`, and **only one of the two directions is real**:
        `_record_bridge_calls` wraps exactly the names it is handed, so `called` is a
        subset of `_BRIDGES` by construction and "reached but unlisted" can never
        appear. The equality therefore carries the *unreached* direction — a bridge
        that should fire and does not, which is the failure mode of a walk that
        quietly stopped calling one. Measured, by adding a real but non-firing name:
        `_record_bridge_calls(_BRIDGES | {"_nodelist_ref_out_of_range_error"})` returns
        the six and the row goes red on it.

        The other direction — a bridge the walk calls that is on neither list — is
        `Bridge`'s to prove and this file cannot, which is what `ALL`'s comment in
        `materialise.rs` says.
        """
        called = _record_bridge_calls(_BRIDGES)
        self.assertEqual(called, _BRIDGES, f"unreached: {sorted(_BRIDGES - called)}")

    def test_the_unreachable_bridges_are_unreachable(self):
        """The other half of the list, and the reason it is a list at all.

        `_UNREACHABLE_BRIDGES` names four bridges a valid corpus cannot reach
        because they are error paths. If one of them *is* reached by a valid
        template then the exclusion is simply wrong and that bridge belongs in
        `_BRIDGES` — a fact about the walk, not a cosmetic tidy-up. **This row is
        what caught exactly that**, on a draft that had put `_materialise_variable`
        here on the theory that no Rust arm reaches it: the arm is indeed unreachable,
        but the *function* is called by `_materialise_operand` (`python/django_rust_templates/engine/parser.py:448`), so
        the census saw it and the exclusion was wrong.
        """
        called = _record_bridge_calls(_UNREACHABLE_BRIDGES)
        self.assertEqual(called, set(), f"the corpus reaches these after all: {sorted(called)}")


if __name__ == "__main__":
    unittest.main()
