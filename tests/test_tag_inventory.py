"""Django's built-in tag inventory, pinned executably rather than in prose.

M2's plan has carried a count of Django's built-in tags since it was written, and
that count has been wrong three times — most recently because "how many built-in
tags" was asked without ever defining the metric, so a registry count (27) and a
decorated-function count (26) were reported at each other, and a review
"confirmed" one of them against the other's question.

A number in a document can be wrong indefinitely. A number in a test can be
wrong once. Three things are pinned here:

1. the exact set of names in the two modules that register built-in tags;
2. the two counts that have been confused, and the single name that separates
   them; and
3. the partition of those names by whether the tag's compile function can reach
   `parser.compile_filter` — that is, whether it needs a `FilterExpression`.

The third is what drives the milestone split, so it is asserted by name and not
only by size.

`querystring` is the name at the centre of it. It is registered by `simple_tag`
rather than `tag` (`defaulttags.py:1286`), so it is a registry entry but not a
`tag`-decorated function; and `simple_tag`'s generated compile function passes
`parser.compile_filter` into `parse_bits` (`library.py:454`), so it belongs in
the FilterExpression bucket. Both facts are unintuitive enough to have been got
wrong twice, which is why they are asserted rather than commented.

Django is pinned at 6.0.9 under `.django/`. These assertions are meant to fail
loudly on a pin bump that adds or removes a tag — otherwise the plan's milestone
table goes stale in silence, which is how it got to three corrections.
"""

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import defaulttags, loader_tags  # noqa: E402

# Every tag a template can invoke: `defaulttags` (24) + `loader_tags` (3).
REGISTERED = {
    "autoescape", "block", "comment", "csrf_token", "cycle", "debug", "extends",
    "filter", "firstof", "for", "if", "ifchanged", "include", "load", "lorem",
    "now", "partial", "partialdef", "querystring", "regroup", "resetcycle",
    "spaceless", "templatetag", "url", "verbatim", "widthratio", "with",
}

# Tags whose compile function can reach `parser.compile_filter`, so they cannot
# be compiled until M3 supplies `FilterExpression` and `smartif`.
#   11 directly:   cycle, extends, filter, firstof, for, ifchanged, include,
#                  lorem, regroup, url, widthratio
#   plus `with`    via `token_kwargs`
#   plus `if`      via `TemplateIfParser`
#   plus `querystring` via `simple_tag` -> `parse_bits` (library.py:454)
NEEDS_FILTER_EXPRESSION = {
    "cycle", "extends", "filter", "firstof", "for", "ifchanged", "include",
    "lorem", "querystring", "regroup", "url", "widthratio", "with", "if",
}

# The 13 that touch no FilterExpression at all.
NO_FILTER_EXPRESSION = REGISTERED - NEEDS_FILTER_EXPRESSION

# M2's nine: the tags with no filter or expression machinery.
M2_TAGS = {
    "autoescape", "comment", "csrf_token", "debug", "now", "resetcycle",
    "spaceless", "templatetag", "verbatim",
}

# The four no-FilterExpression tags deferred for reasons unrelated to filters.
# The set is what *M2* left behind, so it does not shrink as later milestones
# land: `block` was M7's and has landed, `load` is host-owned since M6, and
# `partial`/`partialdef` are M13's pair.
DEFERRED_NO_FILTER_EXPRESSION = {"block", "load", "partial", "partialdef"}

# M5's eleven: every FilterExpression tag that is not Python-owned, minus the
# one the M5 plan's §D1 moves to M6.
#
# The name this set is *not* is the one worth recording. `NEEDS_FILTER_EXPRESSION`
# has fourteen members, and the three it must shed are shed for three different
# reasons: `extends` and `include` are the loader tags §6 assigns to Python
# outright, and `querystring` is deferred *within* the FilterExpression bucket
# because it is the only `simple_tag` in `defaulttags.py` (`defaulttags.py:1286`)
# — parsing it needs `SimpleNode` in `NODE_CLASSES`, a host handle to the Python
# callable, and `parse_bits` driven by that callable's signature, all of which are
# M6's `Library` machinery. A bespoke node would fail parity, because Django's own
# tree holds a `SimpleNode` and the parity comparison is by class name.
M5_TAGS = {
    "cycle", "filter", "firstof", "for", "if", "ifchanged", "lorem",
    "regroup", "url", "widthratio", "with",
}


def registry_names():
    """The names actually invocable from a template."""
    return set(defaulttags.register.tags) | set(loader_tags.register.tags)


def decorated_function_names():
    """Names of functions carrying an `@register.tag` decorator, found by AST.

    Handles both `@register.tag` and `@register.tag("name")`, which look
    different in the tree — an earlier count in this repo handled only one form
    and silently halved the answer.
    """
    found = set()
    for module in (defaulttags, loader_tags):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute) and target.attr == "tag":
                    found.add(node.name)
    return found


def decorators_on(function_name, module):
    """Decorator attribute names on a top-level function, by AST.

    Handles both `@register.tag` and `@register.tag("name")`: the latter is an
    `ast.Call` whose `func` is the attribute.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            names = set()
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute):
                    names.add(target.attr)
            return names
    raise AssertionError(f"{function_name} is not a top-level function of {module.__name__}")


class TagInventoryTests(unittest.TestCase):
    def test_the_registry_is_exactly_these_names(self):
        self.assertEqual(registry_names(), REGISTERED)

    def test_the_two_counts_that_have_been_confused(self):
        """27 registry entries and 26 `@register.tag` functions: both correct.

        The gap is one registration mechanism, not one missing tag.
        """
        self.assertEqual(len(registry_names()), 27)
        self.assertEqual(len(decorated_function_names()), 26)
        self.assertIn("querystring", registry_names())

    def test_querystring_is_registered_by_simple_tag_not_tag(self):
        """The single name that separates the two counts.

        Asserted against the decorator in the source, not against the registered
        callable's `__name__`: a `tag`-decorated function routinely registers
        under a different name from its own (`do_filter` registers as `filter`),
        and `simple_tag` synthesises a compile function, so the callable's name
        is not a reliable signal either way. Which decorator produced the
        registration is the fact that matters.
        """
        decorators = decorators_on("querystring", defaulttags)
        self.assertIn("simple_tag", decorators)
        self.assertNotIn("tag", decorators)

    def test_the_filter_expression_partition(self):
        self.assertEqual(len(NEEDS_FILTER_EXPRESSION), 14)
        self.assertEqual(len(NO_FILTER_EXPRESSION), 13)
        self.assertEqual(len(REGISTERED), 14 + 13)
        self.assertEqual(NEEDS_FILTER_EXPRESSION | NO_FILTER_EXPRESSION, REGISTERED)
        self.assertEqual(NEEDS_FILTER_EXPRESSION & NO_FILTER_EXPRESSION, set())

    def test_the_non_obvious_members_of_the_filter_expression_bucket(self):
        """Membership a per-function scan of `compile_filter` calls misplaces."""
        self.assertIn("querystring", NEEDS_FILTER_EXPRESSION)
        self.assertIn("with", NEEDS_FILTER_EXPRESSION)
        self.assertIn("if", NEEDS_FILTER_EXPRESSION)
        for name in ("querystring", "with", "if"):
            self.assertNotIn(name, M2_TAGS)

    def test_m2s_nine_tags_and_the_four_deferred_ones(self):
        """The plan's `13 = 4 + 9`, asserted by name.

        If this fails, M2's plan's milestone table is wrong, not this test.
        """
        self.assertLessEqual(M2_TAGS, NO_FILTER_EXPRESSION)
        self.assertEqual(len(M2_TAGS), 9)
        self.assertEqual(NO_FILTER_EXPRESSION - M2_TAGS, DEFERRED_NO_FILTER_EXPRESSION)

    def test_m5s_eleven_tags_and_the_two_derivations_agree(self):
        """The set, derived two ways, and the two must agree.

        Derivation 1 is the filter partition: every tag whose compile function
        can reach `parser.compile_filter`, minus the three a later milestone
        claims. Derivation 2 is the registry minus every tag every later
        milestone's §10.3 row takes — M2's nine plus M5's eleven are exactly the
        twenty registered names no later row claims, which is the assertion's
        second half. Two independent routes to the same eleven, so a slip in
        either is caught by the other; the *count* is asserted only because both
        already are, since a count and a set are different claims.

        The eleven are the M5 plan's scope and the milestone's gate is a set
        difference against them (`test_no_m5_tag_is_still_a_stub`), so this is
        where "which tags M5 is" stops being a prose phrase.
        """
        self.assertEqual(
            M5_TAGS, NEEDS_FILTER_EXPRESSION - {"extends", "include", "querystring"}
        )
        self.assertEqual(len(M5_TAGS), 11)
        self.assertEqual(
            M5_TAGS | M2_TAGS,
            REGISTERED - {"block", "extends", "include", "load",
                          "partial", "partialdef", "querystring"},
        )

    def test_the_parity_target_is_genuinely_djangos_registry(self):
        """Guard against self-comparison once M9 interposes the shim.

        The same trap `test_lexer_parity`'s source-file guard exists for: after
        M9, `django.template.defaulttags` resolves to our shell, and any
        inventory read from it would be measuring our registry against itself.
        """
        import inspect

        source_file = inspect.getsourcefile(defaulttags)
        self.assertTrue(
            source_file.startswith(str(DJANGO_CHECKOUT)),
            f"inventory target is not the pinned checkout: {source_file}",
        )


class RustRegistryTests(unittest.TestCase):
    """The Rust registry is Django's built-ins minus the three Python owns as tags.

    M2 could assert the complement from Rust alone (`the_registry_holds_exactly_the_nine_m2_tags`
    listed the 18 names that must *not* be registered), because a missing entry
    and an absent tag were the same thing. From M3 they are not: the registry is
    written once and in full, so an unimplemented tag has an entry whose compile
    function returns `Unimplemented` rather than no entry at all. The set is
    therefore only checkable where Django's registry is readable, which is here.

    M6 moved the count from three to five by handing `load` and `querystring` to
    the host, and the two left for two different reasons. Both are §6's — a
    Python-owned tag is one whose compile function Django must run — but
    `load`'s reason is *state* (`parser.add_library` rebinds `parser.tags` and
    `parser.filters`, and `A2` keeps that state in one real `Parser`) while
    `querystring`'s is *machinery* (`simple_tag`'s generated compile function
    calls `parse_bits` driven by the callable's signature — `library.py:454`).

    **M7 moves it back to three, and the direction is the point — twice over.**
    `block` was Python-owned for the same *state* reason as `load` — `do_block`
    writes `parser.__loaded_blocks` — and M7 put that state on Rust's `Parser`,
    so the name became Rust's. `extends` was Python-owned for *machinery*'s
    reason — `do_extends` reads `parser.origin.template_name` and queries the
    just-parsed nodelist through `get_nodes_by_type` — and M7 did not move that
    machinery: it replaced the query with one descriptors can answer
    (`NodeListDesc::get_nodes_by_type`, by class name) and gave the origin a
    Rust field. The two departures are the two versions of the same move, and
    `registry.rs` carries them as one paragraph for that reason. `include`
    remains, on §6's list and for its loader machinery.
    """

    # The three Django registers that Rust does not own the compile function for.
    PYTHON_OWNED = {"include", "load", "querystring"}

    def test_the_rust_registry_is_djangos_builtins_minus_the_python_owned_three(self):
        from django_rust_templates import _native

        names = _native.builtin_tag_names()
        self.assertEqual(sorted(names), sorted(REGISTERED - self.PYTHON_OWNED))
        self.assertEqual(len(names), 24)

    def test_the_python_owned_tags_are_absent_on_purpose(self):
        from django_rust_templates import _native

        for name in sorted(self.PYTHON_OWNED):
            with self.subTest(name=name):
                self.assertNotIn(name, _native.builtin_tag_names())

    def test_the_only_two_unimplemented_entries_are_the_m13_pair(self):
        """A stub is a real M13 tag, not a stand-in for a name Rust handed over.

        `builtin_tag_stubs()` asks the registry itself which names have a compile
        function that returns `Unimplemented`, so pinning the set here is what
        separates "not done yet" from "not ours": `load` and `querystring` are
        absent from the table entirely, and only `partial`/`partialdef` remain.
        """
        from django_rust_templates import _native

        self.assertEqual(set(_native.builtin_tag_stubs()), {"partial", "partialdef"})

    def test_no_m5_tag_is_still_a_stub(self):
        """M5's switch: the milestone is done iff this intersection is empty.

        The two sides are both derived — `M5_TAGS` above, and the stub set from
        the registry itself, which classifies a name by *asking its compile
        function* rather than by consulting a list someone maintains. So this
        cannot be satisfied by editing a literal: the only way to empty the
        intersection is for every M5 tag's body to stop returning
        `Unimplemented`, which is the work.

        Deliberately red from Task 1 of the M5 plan until its last tag lands.
        That is the one red commit the plan declares, and the reason for it is
        that a gate nobody has seen fail is a comment — the vacuous shape this
        whole file exists to replace.
        """
        from django_rust_templates import _native

        still = set(_native.builtin_tag_stubs()) & M5_TAGS
        self.assertEqual(still, set(), f"M5 has not landed: {sorted(still)}")

        # Why this test cannot be satisfied from Python, which is the property
        # that makes it a gate rather than a comment: both sides come from the
        # engine. `M5_TAGS` is derived above from Django's registry, and
        # `builtin_tag_stubs()` is derived in Rust by asking each compile
        # function, so there is no Python literal to edit here. The Rust side
        # pins its own derivation against a literal in
        # `registry.rs::every_entry_is_reachable_and_every_unimplemented_one_is_a_stub`
        # — checked there and not here, because a Python test cannot tell a
        # derivation from a copy of one.


if __name__ == "__main__":
    unittest.main()
