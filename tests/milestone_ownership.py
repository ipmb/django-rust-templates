"""Which milestone owns each module of the shim's namespaces, as a table.

The meter (``_shim.unshimmed()``) counts what is left. It never said *who* takes
each name, and until M10 nothing did: ``.review/m10-recon.md`` §2 mapped all 23
names against §10.3's rows and found three with no owner at all, and `origin` in
M10's own row although no ``django/template/origin.py`` exists at 6.0.9.

A partition written as a row of prose is a claim nobody re-reads. This one is a
table with an assertion under it (``tests/test_milestone_ownership.py``), so a
name that leaves the meter without entering a bucket, or a bucket that names a
module the checkout does not have, fails in the commit that causes it.

**Two namespaces since M12 Task 1, and therefore two meters.** ``unshimmed()``
counts over ``django.template`` and over ``django.templatetags``, the second
scope's five libraries starting beside the first scope's 26 modules. The table
partitions both, so a name in either population that no row claims is the same
failure it has always been — which is why M12's row below names all five even
though `cache` and `static` were the only two transcribed by Task 3, and those
two have already moved out of `OWNERS` and into `INTERPOSED`.

Keys are ``_impl``-relative, the spelling :func:`_shim.dotted_suffix` produces
from a file path and the one ``SHADOWING``, ``DECLARED`` and ``COMPARED_PATH``
all use. That is *not* the meter's spelling — the meter answers in full dotted
names — and the two differ between scopes: ``django.template.base`` is the key
``base`` while ``django.templatetags.tz`` is the key ``templatetags.tz``. The
split is derived from :data:`_shim.NAMESPACE_ROOTS` below rather than written as
a literal prefix, because a second spelling of a root is how the two drift.
"""

import pathlib
import re

from django_rust_templates._shim import NAMESPACE_ROOTS, TARGET

SPEC = (
    pathlib.Path(__file__).resolve().parent.parent
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-10-08-django-template-engine-in-rust-design.md"
)

# Names a milestone will interpose, keyed by dotted suffix — `loaders.base`, not
# `loaders/base.py` — because that is the spelling `_shim.unshimmed()` reports
# and `_shim.dotted_suffix()` derives from a path.
#
# M10's nine are listed here until the commit that interposes them moves each to
# `INTERPOSED` below. That move *is* the discipline: it is what keeps the table
# and the meter in step, and `test_no_name_is_both_owned_and_taken` fails if a
# name is in both.
OWNERS = {
    # §10.3's M10 row. Its nine names lived here until the commit that
    # interposed each moved it to `INTERPOSED` below; the six `loaders` files
    # were the last of them, so **M10's row is now empty here and the milestone
    # is done with this table**. `origin` is absent and cannot be added: it is
    # not a module (§1 — `Origin` is a class in `base.py`, which M9 owns), so the
    # row naming it is an error, corrected by this plan's close-out task.
    # `test_the_table_partitions_the_universe` makes it unrepresentable here,
    # because `origin` is not in `django_modules()`.
    #
    # §10.3's M11 row is now empty here, as M10's is above: `defaulttags` was
    # the last of its three and moved to `INTERPOSED` in the commit that
    # interposed it. `loader_tags` and `defaultfilters` went the same way, each
    # in its own task's commit.
    # §10.3's M12 row, which is the first row whose names are in the **second**
    # namespace rather than folded into the `django.template` universe: `i18n`,
    # `l10n`, `static`, `tz` and `cache` are `django/templatetags/*`, which M12
    # Task 1 gave the meter as its second scope. The three below are here
    # because the table partitions both populations; `cache` and `static` moved
    # to `INTERPOSED` in the commit that transcribed them, which is the move
    # `test_no_name_is_both_owned_and_taken` exists to force. M12's row also
    # retires nothing from the 11 the first scope is counted at.
    "templatetags.i18n": "M12",
    "templatetags.l10n": "M12",
    "templatetags.tz": "M12",
    #
    # §10.3's M13 row — partials, `library`, `backends`, `response` — plus
    # `loader`, which the row does not name and which §2 had to decide: the
    # singular `django.template.loader` is the *public facade* (`get_template`,
    # `select_template`, `render_to_string`), it reaches templates through
    # settings, and its only importer inside the template package is
    # `response.py`, which M13 owns. That is §6's framework-coupling test,
    # applied by hand, and it is stated here rather than left for the next
    # reader to resolve to whichever spelling they thought of first.
    "loader": "M13",
    "library": "M13",
    "backends": "M13",
    "backends.base": "M13",
    "backends.django": "M13",
    "backends.dummy": "M13",
    "backends.jinja2": "M13",
    "backends.utils": "M13",
    "response": "M13",
}

# Names no milestone has agreed to take, each with the reason it is not simply
# assigned to M14. `.review/m10-recon.md` §2: "undecided, not M14's by default"
# — the reading that they are M14's rests on the premise that zero is the right
# end state, and both of these argue against it.
UNDECIDED = {
    "autoreload": (
        "`autoreload.py` consumes objects that become ours (`from django.template "
        "import engines`, and `DjangoTemplates` from `django.template.backends."
        "django`) and defines no template machinery of its own: two signal "
        "receivers and a directory walk. Interposing it would be copying glue, "
        "which is what §6's vendoring policy exists to refuse — with nothing of "
        "ours to get right, there is also nothing for a test to falsify."
    ),
    "context_processors": (
        "Reached by import string from `_builtin_context_processors` "
        "(`django/template/context.py:5`), not by a module-level import, so "
        "interposing `context` does not pull it in and nothing in the engine "
        "depends on its identity — Django's own `Engine` looks it up from the "
        "settings string. Unlike `autoreload` it does have behaviour of its own "
        "(`csrf`, `debug`, `request`, `auth`), so it is a real candidate rather "
        "than a leaf, and nothing in this milestone decides it."
    ),
}

# What is already ours, by the milestone that took it. The sets here are checked
# against `_shim.SHADOWING` by `test_interposed_is_the_shadowing_table`, so this
# table contributes the attribution and no membership of its own.
INTERPOSED = {
    "M0": ["exceptions"],
    "M9": ["base", "smartif"],
    "M10": [
        "utils",
        "context",
        "engine",
        "loaders",
        "loaders.base",
        "loaders.filesystem",
        "loaders.app_directories",
        "loaders.cached",
        "loaders.locmem",
    ],
    "M11": ["loader_tags", "defaultfilters", "defaulttags"],
    # M12's five live in the *second* meter scope, `django.templatetags`, and
    # only the two that are transcribed appear in `_shim.SHADOWING` — which is
    # what this table is checked against. `i18n`, `l10n` and `tz` join as Tasks
    # 4-6 port them. The package itself (`templatetags`) is absent here and
    # cannot be added: the shim's `_is_foreign_package` declines to serve
    # `django.templatetags`, so no file of ours ever shadows it.
    "M12": ["templatetags.cache", "templatetags.static"],
}

# The namespace a table key belongs to, derived from the shim's own roots.
#
# Keys are `_impl`-relative, so the second scope's are prefixed with the root
# `NAMESPACE_ROOTS` gives it (`templatetags.cache`) while the first scope's are
# bare (`loaders.base`) — the first namespace's root is `""`. Derived rather
# than written as a literal prefix, because that literal would be a second
# spelling of a root the shim owns, which is how `loaders/base.py` becomes
# `loaders.base.py` in one of two derivations.
_ROOT_TO_SCOPE = {root: scope for scope, root in NAMESPACE_ROOTS.items() if root}


def scope_of(name: str) -> str:
    """The scope a table key belongs to; ``django.template`` for a bare one."""
    return _ROOT_TO_SCOPE.get(name.split(".", 1)[0], TARGET)


def interposed(scope: str | None = None) -> set:
    """Every name an earlier milestone has already taken.

    ``scope=None`` is the union across namespaces — what ``SHADOWING`` carries,
    which is what ``test_interposed_is_the_shadowing_table`` compares against.
    A scope name filters to one namespace's keys, which is what the per-scope
    partition and meter rows need after M12 put the second namespace's names in
    this table: ``unshimmed()`` counts one population at a time, so a row that
    compared it against the union would be reading 3 names as 3 out of 14.
    """
    names = {name for group in INTERPOSED.values() for name in group}
    if scope is None:
        return names
    return {name for name in names if scope_of(name) == scope}


def owned(scope: str | None = None) -> set:
    """Every name a milestone has agreed to take, taken or not.

    The union of the two pending-and-taken buckets, as :func:`interposed` is for
    the taken one, and scope-filtered the same way and for the same reason.
    """
    names = set(OWNERS) | set(UNDECIDED)
    if scope is None:
        return names
    return {name for name in names if scope_of(name) == scope}


def spec_milestones() -> set:
    """The milestone labels §10.3's table defines.

    Read from the spec rather than written here: a second list would agree with
    itself and a typo in `OWNERS` would be invisible. The rows are
    ``| M<n> | <component> | <done when> |``; this takes the first cell of each.
    """
    return set(re.findall(r"^\| (M\d+) \|", SPEC.read_text(encoding="utf-8"), re.M))
