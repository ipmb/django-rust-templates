"""M6's blind spot, made visible: a host block tag's body is *Django-parsed*.

Rust hands a host-compiled tag the token cursor and takes back whatever the tag
did not consume. `Library.simple_block_tag` (`library.py:211-212`) does
``nodelist = parser.parse((end_name,)); parser.delete_first_token()`` against
that cursor, and `parser` is Django's own `Parser` (A2), so its `parse` runs
Django's tokenizer-free *node* loop over tokens Rust lexed: every tag in the
body is compiled by Django's `do_*`, not by our registry.

The consequence is a hole in every tree comparison, and it is worth naming
precisely because this project's parity tests are the thing that would normally
catch a divergence: **`assert_trees_match` over such a body compares Django's
work with Django's work.** A green row there is evidence about `simple_block_tag`
and nothing at all about our engine.

Error *text* cannot close the hole — our messages are ported verbatim from
Django's, so `{% for %}` with a missing argument raises identical text whichever
side compiled it. The discriminator has to be something only one side does, and
that is `{% partialdef %}`: its Rust entry is still a stub
(`builtin_tag_stubs()` contains it), so a Rust-parsed body would raise
`UnimplementedTag` (`measured`), while a Django-parsed one runs `do_partialdef`
and stores into `parser.extra_data["partials"]` (`defaulttags.py:1250-1251`).
The contrast is asserted below rather than argued in this docstring, because a
discriminator nobody has seen fail on both sides is the same kind of comment
this file exists to replace.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"
if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django.template import Engine, Library  # noqa: E402

from django_rust_templates import _native, render  # noqa: E402
from django_rust_templates import host as host_module  # noqa: E402

# `{% partialdef %}` is the discriminator; see the module docstring. It sits
# between the block tag's opening and closing tokens, so it is inside the region
# Django's own `parse` walks.
BODY_SOURCE = "{% wrap %}{% partialdef p %}x{% endpartialdef %}{% endwrap %}"
# The same tag with no host block around it: Rust compiles this one, and its
# stub is what makes the difference measurable.
BARE_SOURCE = "{% partialdef p %}x{% endpartialdef %}"


def _wrap_library():
    """A `simple_block_tag` — the shape that re-enters Django's parse.

    `library.py:211-212` is `nodelist = parser.parse((end_name,))` then
    `parser.delete_first_token()`, run against the parser the host owns.
    """
    library = Library()

    @library.simple_block_tag
    def wrap(content):
        return content

    return library


def _hosting_engine():
    engine = Engine()
    engine.template_builtins.append(_wrap_library())
    return engine


class HostBlockTagRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # `Engine()` reads settings. Configured here rather than at import time
        # because a module-level `settings.configure` changes what every test
        # module discovered after this one sees.
        from django.conf import settings

        if not settings.configured:
            settings.configure(USE_I18N=False)

    def test_a_host_block_tags_body_is_parsed_by_django_and_that_is_asserted(self):
        """The nested region is Django-parsed, and this makes that visible.

        When a host tag's compile function calls `parser.parse(...)` — which is
        `Library.simple_block_tag` verbatim (`library.py:211-212`) — the body is
        parsed by *Django's* `Parser.parse`, so every tag inside it is compiled
        by Django and `assert_trees_match` cannot tell that from Rust having
        done it. **A tree comparison over such a body is therefore not evidence
        about our engine**, which is the vacuous-gate failure this project has
        hit twice.

        The assertion is that `do_partialdef` ran, which is only reachable
        through Django's parse: Rust's entry for the name returns
        `UnimplementedTag` (the row below pins that).
        """
        engine = _hosting_engine()
        host = host_module.ShellHost(render.parser_for("", engine))

        _native.parse(BODY_SOURCE, host=host)

        self.assertIn("p", host.parser.extra_data["partials"])

    def test_the_discriminator_is_Rust_compiling_the_same_tag(self):
        """The other side of the contrast the row above rests on.

        Without this, "the body reached Django" is a claim about a parse that
        might equally have been Rust's — and since our messages are Django's,
        only a tag *one* side implements can tell them apart. `partialdef` is
        that tag, and this is what Rust does with it.
        """
        with self.assertRaises(_native.UnimplementedTag):
            _native.parse(BARE_SOURCE)


if __name__ == "__main__":
    unittest.main()
