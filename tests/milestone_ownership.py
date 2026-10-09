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

**A third bucket since the scope correction: names that are not the engine.**
``unshimmed()`` now subtracts `_shim.NOT_THE_ENGINE`, so the meter counts the
engine rather than the package the engine lives in: ten of `django.template`'s
26 modules are Django's for good. That is what makes M14's end state reachable at
all, and it is why this bucket is the one place in this file where a name is
recorded in order to be taken *off* the schedule rather than onto it — its
membership is the shim's (strict mode reads it) and its reasons are here.

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
    # Task 1 gave the meter as its second scope. **The row is now empty here, as
    # M10's and M11's are above:** all five moved to `INTERPOSED` in the commit
    # that transcribed each — `cache`/`static` first, then `tz`, then `l10n`, and
    # `i18n` last in Task 6a, which is the move
    # `test_no_name_is_both_owned_and_taken` exists to force. M12's row also
    # retires nothing from the 11 the first scope is counted at.
    #
    # §10.3's M13 row, **as corrected**: partials and `library`. The row used to
    # read "partials, `library`, `backends`, `response`" and this bucket used to
    # hold nine names; eight of them moved to `NOT_THE_ENGINE` below, and the
    # ninth (`loader`) went with them. `library` was the last name here — the
    # engine's own rather than an adapter, because `Library`, `register`,
    # `simple_tag`, `inclusion_tag` and `parse_bits` are what `{% load %}`
    # compiles a third-party tag library *with* — and it moved to `INTERPOSED`
    # below in M13's Task 1, the commit that takes `unshimmed()` over
    # `django.template` to `[]`.
    #
    # **The table is empty from that commit, and `pending()` with it** — which is
    # the scope correction's own claim, and why `_shim.NOT_THE_ENGINE` is where
    # the other side of the line is recorded. A row left here would be a name
    # asserting a set of nothing.
}

# Names no milestone will take, because they are **not the template engine** —
# Django's for good rather than ours, which is what separates this bucket from
# `OWNERS` above it. They still count in the meter's universe and still appear in
# `_shim.NOT_THE_ENGINE`; what they do not do is appear in `unshimmed()`.
#
# **The decision is the user's, taken 2026-10-09**, and it is why this bucket is
# not called `UNDECIDED` any more: *"You don't need to rewrite all of
# `django.template`. I see the various backends listed in your TODO. You are just
# focused on the Django template engine."* Before it this bucket held two names
# and `.review/m10-recon.md` §2 called them "undecided, not M14's by default" —
# the bucket was a question, and M14 could not be reached while a question sat in
# the meter. It is an answer now.
#
# The test that decides every one of them is §6's vendoring policy, applied the
# way `autoreload` was: *"with nothing of ours to get right, there is also
# nothing for a test to falsify."* Each reason below names which side of the
# boundary it is on — an adapter around the engine, or glue over objects the
# engine already hands back — because the reason is the part a reviewer attacks.
NOT_THE_ENGINE = {
    "loader": (
        "66 lines and four functions at 6.0.9, and every body is the same "
        "delegation: `engines.all()` or `engines[using]`, then "
        "`engine.get_template(name)`, then `template.render(context, request)`. "
        "Both halves are ours already — `engine` was M10's, `Template.render` is "
        "M9's and M11's — so the facade hands its caller *our* objects and defines "
        "no template machinery of its own. That is §6's test, and the one input "
        "it handles by itself is `select_template`'s string check, which is "
        "argument shaping over `get_template`. **The closest call in this "
        "bucket**, and therefore the one written out: it is the API a project "
        "calls, and it stays Django's precisely because calling it already runs "
        "our engine. The falsifier is a behaviour the engine does not expose."
    ),
    "response": (
        "The view-layer object. `TemplateResponse` and `SimpleTemplateResponse` "
        "own HTTP semantics — status and `Content-Type` negotiation, post-render "
        "hooks, and the pickling for the cache — and reach a template only through "
        "`loader` above. Nothing in the engine imports it; it is Django's view "
        "layer reaching down into the template package, not the engine reaching "
        "up into the view layer, which is why the direction of the import is the "
        "whole argument and not a detail."
    ),
    "backends": (
        "The pluggable-backend registry: `BACKENDS`, per-backend `OPTIONS`, and "
        "the `engine` cache. It is the seam a *project* configures, and the engine "
        "it constructs is `django.template.engine.Engine`, which is ours since "
        "M10. §3 declines the contract this module implements, so owning the "
        "registry would mean owning a protocol whose other implementation — "
        "`backends.jinja2` below — we deliberately do not provide. The acceptance "
        "suite reaches it only from `test_response.py`'s two `test_using` rows "
        "(`test_response.py:141`, `test_response.py:296`, `def test_using(self):` "
        "at each), and those are two of §2's five structural skips."
    ),
    "backends.base": (
        "`BaseEngine` and `Template`: the protocol every backend implements, "
        "including the `Template` wrapper that exists so a backend can return "
        "something which is not `django.template.base.Template`. Implementing it "
        "for one backend is a wrapper around our own `Engine` — the shape §6's "
        "vendoring policy exists to refuse."
    ),
    "backends.django": (
        "`DjangoTemplates`, the backend that wraps the engine we already own: "
        "`Engine(...)` built from the `OPTIONS` dict, plus a `Template` wrapper "
        "around `django.template.base.Template`. Its body is settings plumbing "
        "between §3's declined contract and M10's engine, and the plumbing is the "
        "part that would have to be re-derived from Django's settings semantics "
        "rather than from template semantics."
    ),
    "backends.dummy": (
        "The no-op backend, which exists so a project can configure a template "
        "backend that renders nothing. It defines no template machinery — its "
        "`Template` is a string wrapper — and it is the one member of this bucket "
        "the acceptance suite never reaches at all: `template_tests` is the whole "
        "label set, and the dummy backend's rows live in `template_backends`."
    ),
    "backends.jinja2": (
        "§3's standing non-goal, which has been written in one line there since "
        "the spec's first draft and is measured here: a second, non-Django "
        "implementation of the same contract. The five jinja2-backend skips in "
        "§2's baseline are this decision, not an environment problem — they skip "
        "because Jinja2 is deliberately not installed, and §2 refuses to let a "
        "sixth appear."
    ),
    "backends.utils": (
        "The backend protocol's argument normalisation: resolving an engine name "
        "to a backend and building the `OPTIONS` dict it is constructed with. "
        "Helpers for the contract above, imported by it and by nothing in the "
        "engine."
    ),
    "context_processors": (
        "Reached by import string from `_builtin_context_processors` "
        "(`django/template/context.py:5`), not by a module-level import, so "
        "interposing `context` does not pull it in and nothing in the engine "
        "depends on its identity — Django's own `Engine` looks the string up from "
        "settings. Unlike `autoreload` it does have behaviour of its own "
        "(`csrf`, `debug`, `request`, `auth`), and that is the point: every one "
        "of them takes a *request* and returns context for a response, so it is "
        "view-layer machinery that the engine merely names."
    ),
    "autoreload": (
        "`autoreload.py` consumes objects that become ours (`from django.template "
        "import engines`, and `DjangoTemplates` from `django.template.backends."
        "django`) and defines no template machinery of its own: two signal "
        "receivers and a directory walk. Interposing it would be copying glue, "
        "which is what §6's vendoring policy exists to refuse — with nothing of "
        "ours to get right, there is also nothing for a test to falsify. Its rows "
        "in the acceptance suite, `test_autoreloader.py`, are Django's and stay "
        "green either way."
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
    # only the ones that are transcribed appear in `_shim.SHADOWING` — which is
    # what this table is checked against. `i18n` joined in Task 6a, the last of
    # the five and the one that takes `unshimmed(scope="django.templatetags")` to
    # zero. The package itself (`templatetags`) is absent here and cannot be
    # added: the shim's `_is_foreign_package` declines to serve
    # `django.templatetags`, so no file of ours ever shadows it.
    "M12": [
        "templatetags.cache",
        "templatetags.i18n",
        "templatetags.l10n",
        "templatetags.static",
        "templatetags.tz",
    ],
    # M13's one, and the *last* name in either scope: `unshimmed()` over
    # `django.template` is `[]` from this commit. The scope correction's fourteen
    # names are now ten in `NOT_THE_ENGINE` and four interposed here and above.
    "M13": ["library"],
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


def claimed(scope: str | None = None) -> set:
    """Every name a bucket files, in either of the two directions it can go.

    The union of the milestone bucket (`OWNERS`, taken or not) and the
    not-the-engine bucket, as :func:`interposed` is for the taken one — and
    scope-filtered the same way and for the same reason. Both directions are in
    one function because the meter's identity needs exactly this union: a name in
    either bucket is *settled*, so it is out of ``unshimmed()`` whether it is out
    because a milestone took it or because §3 declines it.

    :func:`pending` is the other question — what a milestone still owes — and the
    two differ by precisely `NOT_THE_ENGINE`.
    """
    names = set(OWNERS) | set(NOT_THE_ENGINE)
    if scope is None:
        return names
    return {name for name in names if scope_of(name) == scope}


def pending(scope: str | None = None) -> set:
    """Every name a milestone has agreed to take and has not taken yet.

    The engine's remaining work, and the set ``unshimmed()`` reports over the
    first scope — one name (`library`) since the scope correction. It is written
    as a subtraction of the *other* table rather than as a literal, because a
    literal here would be a second account of M13's row.
    """
    names = set(OWNERS) - interposed()
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
