"""M7 A2: the host predicate's two clauses, and what each one alone buys.

**This script has been re-pointed once, and the reason is the correction it is
here to make.** It was written before Task 4, when `has_tag` had three clauses —
refuse nothing, refuse `_M7_OWNED`, refuse a name Rust does not own, else compare
identity — and it modelled rows 2-6 by deleting the guard. Task 4 shipped, the
guard is gone, and `_M7_OWNED` no longer exists: the import died, so the script
stopped running rather than printing a stale figure. That is the failure mode a
preserved measurement script is for, and it was caught by re-running it in Task 7
Step 1 rather than by reading the README.

What the shipping predicate is now: `python/django_rust_templates/host.py`, which
asks the identity question and nothing else. `django_own_tags()` is the union of
`defaulttags`' and `loader_tags`' registration tables, so the three names below
reach the identity clause and are declined as Rust's — no list in front of it.

Every row runs the *same* `Parser` — a real `Engine()`'s, whose `tags` already
carry `block`/`extends`/`include`, because `Engine.default_builtins`
(`engine.py:14-18`) lists `django.template.loader_tags` third and
`Parser.__init__` merges each builtin through `add_library` (`base.py:668-670`).
So the question is never whether the tag exists — it always does. The only
question is who compiles it.

  row  guard  rust_owned  django_own_tags            what it is
   1   yes    22          defaulttags                the predicate before Task 4
   2   no     22          defaulttags                clause 1 alone
   3   no     25          defaulttags                <-- the trap
   4   no     25          defaulttags+loader_tags     **shipping** (Design B)
   5   no     25          defaulttags+loader_tags     + a {% load %}ed override of block
   6   yes    25          defaulttags+loader_tags     the override under the deleted guard

`has_tag == True` means the host compiles the name; `False` means Rust does.

**Rows 4 and 5 are measured on the real `ShellHost`**, because they *are* the
shipping predicate. Rows 1, 2, 3 and 6 are counterfactuals and are modelled: the
guard they turn on was deleted, and a deleted clause cannot be measured by
calling the code that no longer has it. `M7_OWNED_WAS` records the list verbatim
so the counterfactual is the one that shipped, not a paraphrase of it.

Row 3 is the trap the plan's A2 names: widen `REGISTRY` to 25 without widening
`django_own_tags()`, and the identity clause finds `None` where `loader_tags`
has a callable, calls it "not Django's", and hands the host a tag M7 owns.
Note rows 2 and 3 agree: the registry count is *not* the deciding column, the
identity table is.

Rows 5 and 6 are the same input under the two forms of the predicate, and they
disagree: with the guard kept the `{% load %}`ed override of `block` loses (row
6, `block=False`) — `has_tag` answers "Rust's", so Rust's `do_block` compiles the
caller's tag and the library that overrode it is ignored. `_M7_OWNED` was
consulted before the identity clause, so identity never ran.

**Task 4 closed that divergence**: row 5 is the shipping `block=True`, and it is
`True` for the right reason — the loaded callable is not the one
`django_own_tags()` names for `block`, so the host owns it and the override
wins. The value alone does not distinguish the two rows from rows 1-4's `False`,
which is why this table is read by the guard column rather than by the value.
"""
import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT)

from django.template import Engine, defaulttags, loader_tags
from django.template.base import Lexer, Parser
from django.template.library import Library

from django_rust_templates import _native
from django_rust_templates.host import ShellHost, django_own_tags

NAMES = ("block", "extends", "include")

# The exception list Task 4 deleted, verbatim. Only rows 1 and 6 consult it, and
# they are counterfactuals by construction.
M7_OWNED_WAS = {"block", "extends", "include"}

TODAY = set(_native.builtin_tag_names())
M7 = sorted(TODAY | set(NAMES))
# **`django_own_tags()` is the *widened* table today** — Task 4 made it the union
# of both modules. Rows 1-3's column is the table *before* that, so it is taken
# from `defaulttags` directly rather than from the function whose whole point is
# that it is no longer that. Measured: using `django_own_tags()` here flattens
# rows 1-3 into row 4, because every `None` the identity clause relied on has
# been filled in.
DEFAULT_ONLY = dict(defaulttags.register.tags)
WITH_LOADER = {**django_own_tags(), **loader_tags.register.tags}
# The registry before Task 4. Recorded rather than computed: `TODAY` already
# contains the three, so subtracting them is the only way back to 22.
TODAY_WAS = sorted(TODAY - set(NAMES))

print(f"len(TODAY) = {len(TODAY)} (was {len(TODAY_WAS)})  len(M7) = {len(M7)}  "
      f"registry is 25: {len(TODAY) == 25}  was 22: {len(TODAY_WAS) == 22}")
print(f"'block' in loader_tags.register.tags: {'block' in loader_tags.register.tags}")
print(f"'block' in DEFAULT_ONLY (pre-Task-4): {'block' in DEFAULT_ONLY}")
print(f"'block' in django_own_tags() (today): {'block' in django_own_tags()}")
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
    """A row measured on the shipping `ShellHost` — no model, no knob."""
    h = ShellHost(make_parser(override))
    row = " ".join(f"{n}={h.has_tag(n)}" for n in NAMES)
    print(f"  {label:46} {row}")


class Model:
    """`has_tag` as it was *before* Task 4, with `guard` as the knob.

    `guard=False` is Task 4's form and, since Task 4 shipped, also the shipping
    form — rows 2-5. `guard=True` is the predicate the plan's A2 argued against.
    """

    def __init__(self, parser, rust_owned, django_tags, guard):
        self.parser = parser
        self._rust_owned = set(rust_owned)
        self._django_tags = django_tags
        self._guard = guard

    def has_tag(self, command):
        func = self.parser.tags.get(command)
        if func is None:
            return False
        if self._guard and command in M7_OWNED_WAS:
            return False
        if command not in self._rust_owned:
            return True
        return func is not self._django_tags.get(command)


def model(label, rust_owned, django_tags, guard=False, override=None):
    h = Model(make_parser(override), rust_owned, django_tags, guard)
    row = " ".join(f"{n}={h.has_tag(n)}" for n in NAMES)
    print(f"  {label:46} {row}")


print("--- the shipping predicate, measured on the real ShellHost ---")
real("4  shipping, 25, +loader_tags  <-- Design B")
real("5  shipping, 25, +loader_tags, + {% load %}ed", override=library_overriding_block())
print()

print("--- counterfactuals: modelled, because the guard no longer exists ---")
model("1  guard present, 22, defaulttags", TODAY_WAS, DEFAULT_ONLY, guard=True)
model("2  no guard, 22, defaulttags", TODAY_WAS, DEFAULT_ONLY)
model("3  no guard, 25, defaulttags   <-- the trap", M7, DEFAULT_ONLY)
model("6  guard present, 25, +loader_tags, + {% load %}", M7, WITH_LOADER,
      guard=True, override=library_overriding_block())
print()
print("  Rows 4 and 5 are the code that shipped. Rows 1, 2, 3 and 6 model a")
print("  clause that was deleted, so they cannot be measured on it.")
