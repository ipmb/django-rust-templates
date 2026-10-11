"""Which milestone owns each module of Django's template package, as a table.

**This file used to partition a substitution and now partitions a namespace.**
It was written against a finder that served ``engine/<name>.py`` as
``django.template.<name>``, and every question in it was phrased as "would this
file be served?" — a question about a mechanism. The mechanism is gone (§D, the
M18 rewiring), and the questions underneath it survive unchanged, because they
were never really about the finder: *which of Django's modules have we written
ourselves, and for each one we have not, is that a debt or a decision?*

So the derivations below read the two source trees directly rather than asking a
finder, and the four functions that asked it — ``django_modules``,
``_our_modules_in``, ``dotted_suffix`` and ``shadowing_files`` — are the shim's
bodies, kept because their subject did not change with their mechanism.

The meter (``unshimmed()``) counts what is left. It never said *who* wrote each
name, and until M10 nothing did: ``.review/m10-recon.md`` §2 mapped all 23 names
against §10.3's rows and found three with no owner at all, and `origin` in M10's
own row although no ``django/template/origin.py`` exists at 6.0.9.

A partition written as a row of prose is a claim nobody re-reads. This one is a
table with an assertion under it (``tests/test_milestone_ownership.py``), so a
name that leaves the meter without entering a bucket, or a bucket that names a
module the checkout does not have, fails in the commit that causes it.

**Two scopes, and the second is a different Django package.** ``django.template``
is 26 modules at 6.0.9 and ``django.templatetags`` is 5; our five libraries live
under ``engine/templatetags/`` while the first scope's 16 files are flat in
``engine/``. :data:`SCOPES` holds that mapping once, in both directions, so the
file path and the dotted name cannot drift apart.

Keys are ``engine``-relative, the spelling :func:`dotted_suffix` produces from a
file path — ``base``, ``loaders/base.py``'s ``loaders.base``, and
``templatetags/i18n.py``'s ``templatetags.i18n``. That is *not* the meter's
spelling, which answers in full dotted names: the two differ between scopes, so
the split is derived from :data:`SCOPES` rather than written as a literal prefix.
"""

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = PROJECT_ROOT / "python" / "django_rust_templates" / "engine"
CHECKOUT = PROJECT_ROOT / ".django"


TARGET = "django.template"
TEMPLATETAGS = "django.templatetags"

# scope -> (root under `engine`, directory under the pinned checkout).
#
# The single place the two spellings of a scope meet. ``django.template``'s root
# is ``""`` — our files are flat in ``engine`` — while ``django.templatetags``
# maps to a real subdirectory on both sides. A second table, or a literal
# ``"templatetags"`` anywhere below, is how ``loaders/base.py`` becomes
# ``loaders.base.py`` in one derivation and not the other.
SCOPES = {
    TARGET: ("", "django/template"),
    TEMPLATETAGS: ("templatetags", "django/templatetags"),
}

# Names a milestone will transcribe, keyed by dotted suffix — `loaders.base`, not
# `loaders/base.py` — because that is the spelling :func:`unshimmed` reports and
# :func:`dotted_suffix` derives from a path.
#
# **Empty, and it is empty by construction rather than by accident.** M10's row
# held nine names, M11's three, M12's five and M13's one, and each moved to
# `INTERPOSED` below in the commit that transcribed it; the last was `library`.
# The dict stays as the *declared shape* of the two buckets — a name that a
# milestone owes goes here first, and `test_the_table_partitions_the_universe`
# fails if it is in both — but there is nothing owed, and the four rows that used
# to read `OWNERS` now read :func:`interposed` alone.
OWNERS: dict = {}

# Names we will not transcribe, because they are **not the template engine** —
# Django's for good rather than ours. They still count in the meter's universe
# and still appear in `django_modules`; what they do not do is appear in
# `unshimmed()`.
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
        "transcribing `context` does not pull it in and nothing in the engine "
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
        "receivers and a directory walk. Transcribing it would be copying glue, "
        "which is what §6's vendoring policy exists to refuse — with nothing of "
        "ours to get right, there is also nothing for a test to falsify. Its rows "
        "in the acceptance suite, `test_autoreloader.py`, are Django's and stay "
        "green either way."
    ),
    "utils": (
        "**The newest member, and it moved here from `INTERPOSED[M10]` in the "
        "M18 rewiring** — which is the move this table exists to make visible. "
        "`utils.py` is `EngineHandler`, `get_app_template_dirs` and "
        "`get_installed_libraries`, and under the substitution it had to be ours "
        "for one reason: `django.template.loader` and `django.template.backends` "
        "read `engines` out of *our* `EngineHandler`, so a second registry would "
        "have been an engine cache nothing reset. Under this package's own "
        "namespace that reason is gone in both directions — nothing of ours "
        "imports it, and the one caller that does, `engine/loaders/"
        "app_directories.py`, wants Django's `get_app_template_dirs` because it "
        "enumerates *Django app* template directories, which is a fact about the "
        "project's installed apps rather than about the engine. The whole module "
        "is the settings-mediated facade `backends` and `loader` above are, and "
        "it is the same argument: `EngineHandler` is what resolves "
        "``TEMPLATES[0][\"BACKEND\"]`` to our class, so it is precisely the seam "
        "that must stay Django's. It was deleted rather than re-pinned in "
        "`03038e6`, and this row is where the deletion is recorded; until it was "
        "written the meter reported one name of progress that did not exist."
    ),
}

# What is already ours, by the milestone that took it. The sets here are checked
# against the filesystem by `test_interposed_is_the_shadowing_table`, so this
# table contributes the attribution and no membership of its own.
INTERPOSED = {
    "M0": ["exceptions"],
    "M9": ["base", "smartif"],
    "M10": [
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
    # M12's five live in the *second* scope, `django.templatetags`. `i18n` joined
    # in Task 6a, the last of the five and the one that took
    # `unshimmed(scope="django.templatetags")` to zero.
    "M12": [
        "templatetags.cache",
        "templatetags.i18n",
        "templatetags.l10n",
        "templatetags.static",
        "templatetags.tz",
    ],
    # M13's one, and the *last* name in either scope.
    "M13": ["library"],
}

# The namespace a table key belongs to, derived from :data:`SCOPES`.
#
# Keys are `engine`-relative, so the second scope's are prefixed with its root
# (`templatetags.cache`) while the first scope's are bare (`loaders.base`) — the
# first scope's root is `""`. Derived rather than written as a literal prefix,
# because that literal would be a second spelling of a root, which is how
# `loaders/base.py` becomes `loaders.base.py` in one of two derivations.
_ROOT_TO_SCOPE = {root: scope for scope, (root, _) in SCOPES.items() if root}


def dotted_suffix(relative: str) -> str:
    """``engine``-relative posix path → the dotted suffix it names.

    ``base.py`` → ``base``; ``loaders/__init__.py`` → ``loaders``;
    ``loaders/base.py`` → ``loaders.base``; ``__init__.py`` → ``""``, the
    package itself, which is the one name no table in this tree carries.

    One function rather than four spellings: the file path is how a table finds
    the file, the dotted name is how :func:`unshimmed` and the ownership table
    count it, and a derivation written twice is how ``loaders/base.py`` becomes
    ``loaders.base.py`` in one of them.
    """
    parts = relative.split("/")
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _dotted_module_names(root: pathlib.Path) -> set:
    """Dotted suffixes of every module and package under ``root``.

    ``root/base.py`` yields ``base``; ``root/loaders/app_directories.py`` yields
    ``loaders.app_directories``; ``root/loaders/__init__.py`` makes ``loaders`` a
    package rather than a module, but either way the name is ``loaders``. The
    derivation is :func:`dotted_suffix`'s, so the walk and the tables agree by
    construction.
    """
    names = set()
    if not root.is_dir():
        return names

    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        name = dotted_suffix(path.relative_to(root).as_posix())
        if name:
            # The root package itself, which is ours by construction.
            names.add(name)

    return names


def django_modules(scope: str = TARGET) -> set:
    """Every name Django's own package at ``scope`` defines.

    The universe the meter and the ownership table are counted over — 26 names
    at 6.0.9 for ``django.template``, 5 for ``django.templatetags``. Read from
    the pinned checkout rather than from a list here, because the failure it
    exists to catch is a name nobody thought about: ``autoreload``,
    ``context_processors`` and ``loader`` were absent from every milestone row
    precisely because no list mentioned them (``.review/m10-recon.md`` §2).
    """
    names = set()
    for real_path in _django_dirs(scope):
        names |= _dotted_module_names(real_path)
    return names


def our_modules(scope: str = TARGET) -> set:
    """Dotted suffixes of our files for one scope, ``engine``-relative.

    The scope's root is what makes this more than one call: our
    ``django.templatetags`` files live under ``engine/templatetags/`` while our
    ``django.template`` files are flat in ``engine/``, and the two are compared
    against Django's directories for the *same* scope.

    **A scope's files are not everything under its root.** ``django.template``
    maps to ``engine`` itself, so the bare walk also answers for
    ``django.templatetags`` — ``templatetags.i18n`` would be reported as ours for
    ``django.template``. Each nested scope's names are therefore dropped, keyed on
    :data:`SCOPES` rather than special-cased on ``"templatetags"``.

    The file count is 16 and 5 at this commit, against Django's 26 and 5. The two
    counts are *not* meant to agree: `parser` is ours and has no Django
    counterpart at all (6.0.9 has no ``django/template/parser.py``), while the
    eleven Django names we do not have are :data:`NOT_THE_ENGINE`.
    """
    root = _impl_root(scope)
    ours = _dotted_module_names(root)
    for other, (nested, _) in SCOPES.items():
        if other == scope or not nested:
            continue
        other_root = _impl_root(other)
        if other_root != root and root in other_root.parents:
            ours = {
                name
                for name in ours
                if name != nested and not name.startswith(nested + ".")
            }
    return ours


def _impl_root(scope: str) -> pathlib.Path:
    """Where one scope's files live under ``engine``.

    An unknown scope is refused rather than silently compared against ``engine``:
    a typo would otherwise report the whole of Django's namespace as missing,
    which reads as progress in reverse and sends the reader to the wrong tree.
    """
    try:
        root = SCOPES[scope][0]
    except KeyError:
        raise ValueError(
            f"{scope!r} is not a scope this package transcribes; "
            f"expected one of {sorted(SCOPES)}"
        ) from None
    return ENGINE / root if root else ENGINE


def _django_dirs(scope: str = TARGET) -> list:
    """Django's own directories for one scope, in the pinned checkout.

    A list rather than a path because the shim's caller read the finder's
    captured search paths here, and there can be more than one; the pinned
    checkout gives exactly one, and the union in :func:`django_modules` is
    written for the general case so a second does not silently drop the first.
    """
    try:
        relative = SCOPES[scope][1]
    except KeyError:
        raise ValueError(
            f"{scope!r} is not a scope this package transcribes; "
            f"expected one of {sorted(SCOPES)}"
        ) from None
    path = CHECKOUT / relative
    if not path.is_dir():
        raise AssertionError(
            f"no pinned tree at {path} — the anchor is the Django version in "
            "`python/django_rust_templates/__init__.py`'s TRANSCRIBED_DJANGO"
        )
    return [path]


def shadowing_files() -> list:
    """``engine`` files whose name Django's tree also defines.

    A file per name we have taken, asked of the filesystem against Django's
    directories, so it stays true as modules are added rather than being a
    second, hand-maintained list that can disagree (the failure
    ``.review/m10-recon.md`` §2 found in the milestone table).

    **Asked per scope, with the root stripped.** ``engine/templatetags/i18n.py``
    names ``django.templatetags.i18n``, so the name Django must be asked about is
    ``i18n.py`` under ``django/templatetags/`` — comparing the whole
    ``engine``-relative path against every scope's directory would look for
    ``django/template/templatetags/i18n.py`` and report no clash for a file that
    plainly is one.

    Paths are relative to ``engine`` and posix-style, so a subpackage reports
    ``loaders/__init__.py`` and a library ``templatetags/i18n.py``. Sorted across
    scopes, not just within one: the walk is per scope, so the concatenation is
    *not* in ``engine``-relative order once a file lives under ``templatetags/``.

    **``engine/__init__.py`` is included, and that is the guard's question rather
    than the ownership table's.** It is a colliding file — Django has
    ``django/template/__init__.py`` — and the guard does declare it, in
    ``DECLARED["__init__.py"]``, because the file *is* a transcription with
    regions. The ownership table cannot carry it, because its dotted suffix is
    ``""``: the package itself, the one name :func:`dotted_suffix` documents that
    no table here has a row for. The filter therefore lives at that comparison
    and not in this derivation — 21 files, 20 rows — because a function that
    answered 20 would leave the guard's own key unaccounted for.
    """
    clashes = []
    for scope, (root, _) in SCOPES.items():
        subtree = ENGINE / root if root else ENGINE
        if not subtree.is_dir():  # pragma: no cover - defensive
            continue
        real_paths = _django_dirs(scope)
        other_roots = [
            other for name, (other, _) in SCOPES.items() if other and name != scope
        ]
        for path in sorted(subtree.rglob("*.py")):
            inner = path.relative_to(subtree)
            if "__pycache__" in inner.parts:
                continue
            # A file under another scope's root belongs to that scope.
            inside = inner.as_posix()
            if any(inside == r or inside.startswith(r + "/") for r in other_roots):
                continue
            if any((real / inner).is_file() for real in real_paths):
                clashes.append(path.relative_to(ENGINE).as_posix())
    return sorted(clashes)


def unshimmed(scope: str = TARGET) -> list:
    """Django modules under ``scope`` that we neither transcribe nor declare out.

    The project's coverage number, in the spelling it has had since M12: the
    scope's Django modules, minus ours, minus :data:`NOT_THE_ENGINE`. It was the
    progress meter toward M14 and it is the coverage statement now — the same
    set, asked about a namespace instead of a substitution. ``[]`` means every
    module Django's template package defines is either written here or recorded
    as deliberately not ours; a non-empty answer is a name that is neither.

    What the subtraction costs is the literal reading of the name: a declared
    module *is* still Django's and is no longer named here. The two questions are
    one set apart — ``django_modules(scope) - our_modules(scope)`` — and a caller
    that wants the file listing rather than the coverage answer should ask that.

    **One implementation over two populations, not two functions.**
    ``unshimmed_templatetags()`` would be a second spelling of one count, and two
    spellings drift — the failure this tree has hit three times (D3′, the vacuity
    reasons, M10's ``origin`` row). The second name is the scope argument.

    Computed by comparing the two source trees rather than by importing, so it
    has no side effects — importing Django's template modules can require
    configured settings, and this must be safe to call anywhere.
    """
    remaining = django_modules(scope) - our_modules(scope) - set(NOT_THE_ENGINE)
    return sorted(f"{scope}.{name}" for name in remaining)


def scope_of(name: str) -> str:
    """The scope a table key belongs to; ``django.template`` for a bare one."""
    return _ROOT_TO_SCOPE.get(name.split(".", 1)[0], TARGET)


def interposed(scope: str | None = None) -> set:
    """Every name an earlier milestone has already taken.

    ``scope=None`` is the union across scopes — what
    ``test_interposed_is_the_shadowing_table`` compares against the files on
    disk. A scope name filters to one namespace's keys, which is what the
    per-scope partition and coverage rows need: :func:`unshimmed` counts one
    population at a time, so a row that compared it against the union would be
    reading 5 names as 20.
    """
    names = {name for group in INTERPOSED.values() for name in group}
    if scope is None:
        return names
    return {name for name in names if scope_of(name) == scope}


def claimed(scope: str | None = None) -> set:
    """Every name that is settled in either direction.

    The union of the milestone bucket (`OWNERS`, still owed) and the
    not-the-engine bucket, scope-filtered as :func:`interposed` is — because the
    partition's two directions are "we will write this" and "we will not", and a
    name in either is out of the coverage count.

    Both directions are in one function because :func:`unshimmed`'s identity needs
    exactly this union. With `OWNERS` empty the answer is `NOT_THE_ENGINE`, which
    is the honest reading: every name not already ours has been *declined* rather
    than deferred.
    """
    names = set(OWNERS) | set(NOT_THE_ENGINE)
    if scope is None:
        return names
    return {name for name in names if scope_of(name) == scope}


