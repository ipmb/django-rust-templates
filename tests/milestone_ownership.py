"""Which milestone owns each module of ``django.template``, as a table.

The meter (``_shim.unshimmed()``) counts what is left. It never said *who* takes
each name, and until M10 nothing did: ``.review/m10-recon.md`` §2 mapped all 23
names against §10.3's rows and found three with no owner at all, and `origin` in
M10's own row although no ``django/template/origin.py`` exists at 6.0.9.

A partition written as a row of prose is a claim nobody re-reads. This one is a
table with an assertion under it (``tests/test_milestone_ownership.py``), so a
name that leaves the meter without entering a bucket, or a bucket that names a
module the checkout does not have, fails in the commit that causes it.
"""

import pathlib
import re

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
    # §10.3's M10 row. `origin` is deliberately absent and cannot be added: it
    # is not a module (§1 — `Origin` is a class in `base.py`, which M9 owns), so
    # the row naming it is an error, corrected by this plan's close-out task.
    # `test_the_table_partitions_the_universe` makes it unrepresentable here,
    # because `origin` is not in `django_modules()`.
    "context": "M10",
    "engine": "M10",
    "loaders": "M10",
    "loaders.base": "M10",
    "loaders.filesystem": "M10",
    "loaders.app_directories": "M10",
    "loaders.cached": "M10",
    "loaders.locmem": "M10",
    # §10.3's M11 row.
    "defaulttags": "M11",
    "defaultfilters": "M11",
    "loader_tags": "M11",
    # §10.3's M12 row owns no meter entry: `i18n`, `l10n`, `static`, `tz` and
    # `cache` are `django/templatetags/*`, outside the namespace the meter
    # covers. M12 moves tag bodies and retires nothing here.
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
    "M10": ["utils"],
}


def interposed() -> set:
    """Every name an earlier milestone has already taken."""
    return {name for names in INTERPOSED.values() for name in names}


def spec_milestones() -> set:
    """The milestone labels §10.3's table defines.

    Read from the spec rather than written here: a second list would agree with
    itself and a typo in `OWNERS` would be invisible. The rows are
    ``| M<n> | <component> | <done when> |``; this takes the first cell of each.
    """
    return set(re.findall(r"^\| (M\d+) \|", SPEC.read_text(encoding="utf-8"), re.M))
