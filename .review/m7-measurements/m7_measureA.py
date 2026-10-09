"""M7 A2: the host predicate's two clauses, and what each one alone buys.

`ShellHost.has_tag` (`python/django_rust_templates/host.py:113-131`) has three
clauses today: refuse nothing, refuse `_M7_OWNED`, refuse a name Rust does not
own, else compare identity against `django_own_tags()`.

Every row runs the *same* `Parser` — a real `Engine()`'s, whose `tags` already
carry `block`/`extends`/`include`, because `Engine.default_builtins`
(`engine.py:14-18`) lists `django.template.loader_tags` third and
`Parser.__init__` merges each builtin through `add_library` (`base.py:668-670`).
So the question is never whether the tag exists — it always does. The only
question is who compiles it.

  row  guard  rust_owned  django_own_tags            what it is
   1   yes    22          defaulttags                today
   2   no     22          defaulttags                clause 1 alone
   3   no     25          defaulttags                <-- the trap
   4   no     25          defaulttags+loader_tags     Design B
   5   no     25          defaulttags+loader_tags     + a {% load %}ed override of block
   6   yes    25          defaulttags+loader_tags     + a {% load %}ed override of block

`has_tag == True` means the host compiles the name; `False` means Rust does.

**Rows 1 and 6-today are measured on the real `ShellHost`.** Rows 2-6 model it
with the guard deleted, which is half of what Task 4 does. Everything else in
the model is read from the shipping modules — `_M7_OWNED`, `django_own_tags`,
`_native.builtin_tag_names()`, `loader_tags.register.tags` — so the only
modelled thing is the predicate change itself.

Row 3 is the trap the plan's A2 names: widen `REGISTRY` to 25 without widening
`django_own_tags()`, and the identity clause finds `None` where `loader_tags`
has a callable, calls it "not Django's", and hands the host a tag M7 owns.
Note rows 2 and 3 agree: the registry count is *not* the deciding column, the
identity table is. Deleting the guard is what flips these three, so the guard
must not go before the table is widened — which is why Task 4 does both edits
in one commit.

Rows 5 and 6 are the same input under the two forms of the predicate, and they
disagree: with the guard kept, a `{% load %}`ed library that registers `block`
loses (row 6, `block=False`), because `_M7_OWNED` is consulted before identity.
That is the bug `django_own_tags`'s docstring describes for `for` — and it is
why deleting the guard is not merely cosmetic.
"""
import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/python")

from django.template import Engine, defaulttags, loader_tags
from django.template.base import Lexer, Parser
from django.template.library import Library

from django_rust_templates import _native
from django_rust_templates.host import _M7_OWNED, ShellHost, django_own_tags

NAMES = ("block", "extends", "include")

TODAY = set(_native.builtin_tag_names())
M7 = sorted(TODAY | set(NAMES))
DEFAULT_ONLY = django_own_tags()
WITH_LOADER = {**django_own_tags(), **loader_tags.register.tags}

print(f"len(TODAY) = {len(TODAY)}  len(M7) = {len(M7)}  _M7_OWNED = {sorted(_M7_OWNED)}")
print(f"'block' in loader_tags.register.tags: {'block' in loader_tags.register.tags}")
print(f"'block' in django_own_tags():        {'block' in DEFAULT_ONLY}")
print(f"'block' in Engine().template_builtins[2].tags: "
      f"{'block' in Engine().template_builtins[2].tags}")
print()


def make_parser(override=None):
    """A real `Parser`, with `override` registered as if `{% load %}`ed."""
    eng = Engine()
    p = Parser(Lexer("").tokenize(), eng.template_libraries, eng.template_builtins)
    if override is not None:
        p.add_library(override)
    return p


def library_overriding_block():
    lib = Library()
    lib.tag("block", lambda parser, token: None)
    return lib


def real(label, override=None):
    """Row measured on the shipping `ShellHost`."""
    h = ShellHost(make_parser(override))
    row = " ".join(f"{n}={h.has_tag(n)}" for n in NAMES)
    print(f"  {label:46} {row}")


class Model:
    """`ShellHost.has_tag` with `guard` as a knob; `guard=False` is Task 4's form."""

    def __init__(self, parser, rust_owned, django_tags, guard):
        self.parser = parser
        self._rust_owned = set(rust_owned)
        self._django_tags = django_tags
        self._guard = guard

    def has_tag(self, command):
        func = self.parser.tags.get(command)
        if func is None:
            return False
        if self._guard and command in _M7_OWNED:
            return False
        if command not in self._rust_owned:
            return True
        return func is not self._django_tags.get(command)


def model(label, rust_owned, django_tags, guard=False, override=None):
    h = Model(make_parser(override), rust_owned, django_tags, guard)
    row = " ".join(f"{n}={h.has_tag(n)}" for n in NAMES)
    print(f"  {label:46} {row}")


print("--- measured on the real ShellHost ---")
real("1  today, guard present, 22, defaulttags")
real("6' today, guard present, + {% load %}ed block override")
print()

print("--- modelled: the predicate with the guard deleted (Task 4's form) ---")
model("2  no guard, 22, defaulttags", TODAY, DEFAULT_ONLY)
model("3  no guard, 25, defaulttags   <-- the trap", M7, DEFAULT_ONLY)
model("4  no guard, 25, +loader_tags  <-- Design B", M7, WITH_LOADER)
model("5  no guard, 25, +loader_tags, + {% load %}ed", M7, WITH_LOADER,
      override=library_overriding_block())
print()

print("--- the same override under the guard (what keeping it costs) ---")
model("6  guard present, 25, +loader_tags, + {% load %}", M7, WITH_LOADER,
      guard=True, override=library_overriding_block())
