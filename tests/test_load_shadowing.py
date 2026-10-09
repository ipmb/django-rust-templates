"""M6's gate: a loaded library shadows a built-in, and a plain built-in does not.

**No test in `.django/` pins this.** Searched across the checkout for a library
registering a built-in's name, for `add_to_builtins`, and for the collision
wording: the only hits are library-*name* precedence in
`template_backends/test_django.py` and the E003 check in
`check_framework/test_templates.py`, both about `Engine.libraries`' dotted paths
rather than tag names. The behaviour is real — `add_library` is
`self.tags.update(lib.tags)` (`base.py:669-670`) and libraries merge after
`builtins` (`base.py:522-523`) — so the suite will not catch a regression in it,
and the spec names this as M6's gate.

**The library is registered under a name, not appended to `template_builtins`**,
and that is a correction measured before this file was written. The obvious
fixture — `engine.template_builtins.append(_shadow_library())` — makes
`{% load shadow %}` raise `'shadow' is not a registered tag library`, because
`find_library` reads `parser.libraries` (`defaulttags.py:1065-1075`), which
`Template.compile_nodelist` fills from `Engine.template_libraries`; making the
tag *unconditionally* available is the opposite of testing what a `{% load %}`
does. `engine.template_libraries["shadow"] = _shadow_library()` is the form whose
`{% load shadow %}` finds the library, whose `add_library` then rebinds `comment`
in the parser's one tag dict, and which therefore exercises the shadow.

The spec states the gate against `Library.add_tag`/`add_library`, neither of
which exists; see `.review/m6-recon.md` §"The three spec errors".
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine, Library, Node  # noqa: E402

from django_rust_templates import render  # noqa: E402

# A builtin use, a load that rebinds the name, then a shadowed use. The
# rebinding is what the sandwich is for; see `tests/test_filter_rebinding.py`.
SHADOW_SOURCE = "{{ x|upper }}{% load shadow %}{{ y|upper }}"
SHADOW_CONTEXT = {"x": "a", "y": "b"}


class _ShadowNode(Node):
    def render(self, context):
        return "shadowed"


def _shadow_library():
    """A library rebinding a built-in **tag** and a built-in **filter**.

    `comment` is a tag Rust compiles and `upper` is a filter Rust names the
    handle for; a `{% load %}` of this library replaces both in the parser's own
    tables (`base.py:669-670`), which is the whole of M6's gate.
    """
    library = Library()

    @library.tag("comment")
    def shadow(parser, token):
        parser.skip_past("endcomment")
        return _ShadowNode()

    library.filter("upper", lambda value: "shadowed-%s" % value)
    return library


def _shadowing_engine():
    """An `Engine` whose *loadable* library `shadow` rebinds `comment`."""
    engine = Engine()
    engine.template_libraries["shadow"] = _shadow_library()
    return engine


def _builtin_shadowing_engine():
    """The same library, but configured as the caller's `OPTIONS["builtins"]`.

    Not the same thing as `{% load %}`: `Parser.__init__` merges `builtins`
    before anything is parsed (`base.py:522-523`), so this is the path
    `Engine(..., builtins=[...])` / `OPTIONS["builtins"]` takes and it never
    touches `parser.libraries` or the lexer.
    """
    engine = Engine()
    engine.template_builtins.append(_shadow_library())
    return engine


class LoadShadowingTests(unittest.TestCase):
    def test_a_loaded_library_replaces_a_builtin_of_the_same_name(self):
        """`{% load %}` updates the parser's one tag dict, so the load wins.

        `comment` is Rust's. After the load it must not be: `add_library` is
        `self.tags.update(lib.tags)` (`base.py:669-670`) and libraries merge
        after `builtins` (`base.py:522-523`), so the *later* registration is the
        one in the table — which is why dispatch reads the host's table first.
        """
        output = render.render_string(
            "{% load shadow %}{% comment %}x{% endcomment %}", {}, _shadowing_engine()
        )
        self.assertEqual(output, "shadowed")

    def test_a_builtin_rust_owns_is_still_rusts_when_nothing_shadows_it(self):
        """The other half, and the one a naive host-first dispatch loses.

        The shell must not route `{% comment %}` to Python just because Django's
        `Parser.tags` has an entry for it — every built-in does. The identity
        clause in `ShellHost.has_tag` is what separates "Django's builtin
        callable" from "something a load put there".
        """
        output = render.render_string("{% comment %}x{% endcomment %}ok", {}, Engine())
        self.assertEqual(output, "ok")

    def test_a_callers_own_builtin_outranks_rusts_builtin(self):
        """The predicate must be about *defaulttags*, not about the engine.

        A caller whose `OPTIONS["builtins"]` provides `comment` has replaced
        Django's `do_comment` in `parser.tags` at construction. Django runs the
        caller's tag; so must we. The discriminating assertion is the output:
        Rust's own `{% comment %}` renders `"ok"`, the caller's renders
        `"shadowedok"`.

        **This row is a correction, and the defect it corrects was measured.**
        `ShellHost` used to compare against `dict(parser.tags)` taken at
        construction — which is *after* `Parser.__init__` merged the engine's
        builtins, so it captured the caller's library as if it were Django's.
        `parser.tags["comment"] is snapshot["comment"]` was True, `has_tag`
        answered "Rust's", and Rust's `do_comment` ran while the caller's tag was
        silently dropped. Found by review against the pinned tree, not by the
        suite: the only `builtins` use anywhere in `template_tests` is a *filter*
        library in `test_parser.py`, so nothing in the 1547 reaches it.
        """
        source = "{% comment %}x{% endcomment %}ok"

        ours = render.render_string(source, {}, _builtin_shadowing_engine())
        theirs = _builtin_shadowing_engine().from_string(source).render(Context({}))

        self.assertEqual(theirs, "shadowedok", "the fixture stopped shadowing")
        self.assertEqual(ours, theirs, f"template: {source!r}")
        # The value Rust's own `comment` would have produced, so that the
        # equality above cannot be satisfied by both sides taking the Rust path.
        self.assertNotEqual(ours, "ok")

    def test_the_sandwich_binds_each_filter_at_its_own_token(self):
        """Review Focus 3, end to end, now that `{% load %}` exists.

        Both uses of `upper` are the *same* name around one `{% load %}`, so a
        late lookup collapses the two into one answer and the row goes red. The
        expected string is Django's own, measured on the pinned tree —
        `'Ashadowed-b'`, not `'AB'` — because the parity assertion alone would
        also pass on a source with no rebinding in it.
        """
        engine = _shadowing_engine()
        ours = render.render_string(SHADOW_SOURCE, dict(SHADOW_CONTEXT), engine)
        theirs = _shadowing_engine().from_string(SHADOW_SOURCE).render(
            Context(SHADOW_CONTEXT)
        )

        self.assertEqual(theirs, "Ashadowed-b", "the fixture stopped being a sandwich")
        self.assertEqual(ours, theirs, f"template: {SHADOW_SOURCE!r}")


if __name__ == "__main__":
    unittest.main()
