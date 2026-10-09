"""`{% load %}` rebinds a filter name; the parse must bind at the token's position.

`Parser.add_library` is `self.filters.update(lib.filters)` (`base.py:669-670`) —
`update`, not `setdefault` — so a name already in the table is *rebound*, and a
`{% load %}` anywhere in a template retroactively changes what an earlier
`{{ x|name }}` resolves to, if the name is looked up late.

Django looks the name up once, at `base.py:771`, during the token's own compile.
Our materialisation happens after the whole parse, so the same lookup there is a
different answer whenever a load sits between the two moments.

Three things about the fixture below were measured on the pinned tree rather
than assumed, and each one corrects a first draft of it:

* **The shadowed name has to be a builtin.** `{{ x|trim:3 }}` cannot be the first
  token, because `trim` is not a builtin at all — stock Django raises
  `Invalid filter: 'trim'` before the `{% load %}` is reached, so no rebinding
  could ever be observed. `upper` is a builtin, and a library loaded later
  rebinds it.
* **Both uses have to be the shadowed name**, in source order around one load.
  A row with only the *second* use proves nothing: that token is after the load,
  so both designs agree about it.
* **Django's own answer is `'ABCshadowed-abc'`** for this template, and asserting
  it here is what keeps the template a sandwich rather than a formality. The
  parity assertion alone would pass on a template with no rebinding in it.

The library is built in-process and injected into `engine.template_libraries`
rather than loaded from a file, because `find_library` reads
`parser.libraries` (`defaulttags.py:1065-1075`), which `Template.compile_nodelist`
fills from the engine's map — so no module on disk and no `sys.path` entry is
needed to make a loadable name.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Context, Engine, Library, defaultfilters  # noqa: E402

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.render import materialiser, parser_for, render_string  # noqa: E402

_MATERIALISER = materialiser()

# A builtin use, a load that rebinds the name, then a shadowed use.
SOURCE = "{{ x|upper }}{% load shadow %}{{ y|upper }}"
CONTEXT = {"x": "abc", "y": "abc"}
# Django's answer, measured: the first token kept the builtin it was bound to
# before the load, the third got the shadow.
DJANGO_ANSWER = "ABCshadowed-abc"


def shadowing_engine():
    """An `Engine` whose loadable library rebinds a builtin filter name."""
    library = Library()
    library.filter("upper", lambda value: "shadowed-%s" % value)
    engine = Engine()
    engine.template_libraries["shadow"] = library
    return engine


class FilterRebindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # `Engine()` reads settings. Configured here rather than at import time
        # because the rest of this suite deliberately runs without them, and a
        # module-level `settings.configure` changes what every test module
        # discovered after this one sees.
        from django.conf import settings

        if not settings.configured:
            settings.configure(USE_I18N=False)

    @unittest.expectedFailure
    def test_a_load_does_not_rebind_an_earlier_tokens_filter(self):
        """The sandwich: builtin use, `{% load %}`, shadowed use.

        **Task 3 owns this decorator.** The row is red until `{% load %}` exists,
        and it is red *today* for exactly that reason — measured, through our own
        engine:

            UnimplementedTag: 'load' is a built-in tag this engine does not
            implement yet (M6)

        It is marked rather than skipped or omitted so that it stays visible and
        so that the day it starts passing it reports **unexpected success**,
        which is a failure: Task 3 cannot land `{% load %}` without being told to
        delete this line. A skip would have gone quiet instead.

        `render_string` is the subject and `Engine.from_string` only the oracle,
        so a green row here cannot be stock Django answering for us — which is
        the trap this row was rewritten twice to avoid. Two earlier drafts failed
        for reasons they did not name: `{{ x|trim:3 }}` as the first token, where
        `trim` is not a builtin and stock Django itself raises
        `Invalid filter: 'trim'` before reaching the load; and `upper` under a
        library that does not shadow it, which observes nothing at all.
        """
        ours = render_string(SOURCE, dict(CONTEXT), engine=shadowing_engine())
        theirs = shadowing_engine().from_string(SOURCE).render(Context(CONTEXT))

        self.assertEqual(
            theirs,
            DJANGO_ANSWER,
            "the fixture stopped being a sandwich: nothing was rebound",
        )
        self.assertEqual(ours, theirs, f"template: {SOURCE!r}")

    def test_materialisation_uses_the_bound_function_not_a_second_lookup(self):
        """The mechanism, observable before `{% load %}` exists.

        The sandwich above is the *behaviour*; this is the seam it depends on,
        and it is worth a row of its own because the sandwich is red until Task 3
        while this one is green from the moment the handle ships.

        A `{% load %}` makes the rebinding happen inside a template, but what it
        triggers is a second lookup by name at materialisation — and that can be
        provoked directly: compile the token, rebind the registry entry the way
        `add_library` does, then materialise. A late lookup returns the rebound
        function; the handle returns the one bound when the token was compiled.
        """
        bound = defaultfilters.register.filters["upper"]
        parser = parser_for("x|upper")
        desc = _native.parse_filter_expression("x|upper", _MATERIALISER.check_filter(parser))

        rebound = parser.filters["upper"] = lambda value: "rebound"

        expression = _MATERIALISER._materialise_filter_expression(desc, parser)
        self.assertIs(
            expression.filters[0][0],
            bound,
            "materialisation looked the name up again and got "
            f"{expression.filters[0][0]!r} ({rebound!r} is what a late lookup "
            "returns) instead of the function bound at base.py:771",
        )
