#!/usr/bin/env python3
"""Vendor Django's `template_tests` into `django_suite/`, driving *this* engine.

Usage:
    scripts/gen_django_suite.py            # write django_suite/
    scripts/gen_django_suite.py --check    # re-derive and diff; exit 1 on drift

**Why a copy rather than a finder.** The engine used to reach Django's suite by
impersonating `django.template` on `sys.meta_path`, which made the suite run
*unmodified* — and made every one of its files reachable under two dotted names,
so that a module imported before `install()` and a module imported after held
two different `Node` classes and `assertIsInstance` failed across the seam.
Copying removes the second name: `django_suite.template_tests` is the only name
these files have, so there is exactly one class object per class.

**What the copy changes, and what it deliberately does not.** Every import of a
module this package owns is repointed at `django_rust_templates.engine.*`; every
import of a module it does not own is left alone. Which is which is *not* a table
restated here — it is read off the filesystem, so a module that has not been
ported yet is automatically still Django's, and porting one (or deleting one)
re-partitions the suite in the same commit that re-partitions the package. That
is the same rule `tests/milestone_ownership.py` states in prose and
`check_transcriptions.py` states as `IMPL`; this is the third place it has to
hold, and the only one where getting it wrong silently tests the wrong engine.

So `test_autoreloader.py` and `test_response.py` keep importing Django's
`autoreload`, `response`, `loader` and `backends.django` — those are Django's
view layer and Django's backend registry, and §6's vendoring policy declines
them with a reason each. They reach this engine the way a *project* does: through
`settings.TEMPLATES`, which `django_suite/run.py` points at
`django_rust_templates.rust.RustTemplates`.

**The rewrite is structural, not textual.** `assertLogs("django.template", …)`
is a *logger* name, and it occurs three times in `test_logging.py`; a regex over
`django.template` would rename it and the three rows would go quiet rather than
red. Imports are rewritten from the AST, by exact `(lineno, col_offset)` span;
string literals are rewritten only when they name a module this package owns.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
SRC = CHECKOUT / "tests" / "template_tests"
SUITE = ROOT / "django_suite"
DST = SUITE / "template_tests"

# The trees vendored, as ``(source, destination, is_python_we_rewrite)``.
#
# The second one is not a test tree. It is the catalogue
# ``template_tests/syntax_tests/i18n/base.py`` computes a path to, and it is
# vendored because that arithmetic is *depth-relative*: ``base.py`` takes the
# grandparent of its own directory as ``pdir`` and joins ``i18n/other/locale``
# onto it, so in the checkout the answer is ``<django>/tests/i18n/other/locale``
# and under ``django_suite/`` — one level shallower — it is
# ``django_suite/i18n/other/locale``. Nothing else would put it there, and
# without it the six ``@override_settings(LOCALE_PATHS=extended_locale_paths)``
# rows in ``syntax_tests/i18n/`` find no German catalogue and quietly render
# English, which is a red test rather than a wrong one only because those rows
# spell the German out.
TREES = [
    (SRC, DST, True),
    (
        CHECKOUT / "tests" / "i18n" / "other" / "locale",
        SUITE / "i18n" / "other" / "locale",
        False,
    ),
]

# The package the engine lives in, and the three spellings the suite uses for it.
ENGINE = "django_rust_templates.engine"
DJANGO = "django.template"
TAGS = "django.templatetags"

# The backend a project names in ``TEMPLATES`` to activate this engine, and the
# one the suite's ``TEMPLATES`` literals name instead — Django's own.
#
# **Rewriting the second into the first is what makes the suite a test of this
# package**, and it is not an optimisation. Nine of the suite's rows build their
# backend with a class-level ``override_settings`` and then reach the engine
# *through the registry* — ``Engine.get_default()`` in ``test_engine`` and
# ``test_context``, ``engines["django"]`` in ``test_response``, the
# ``engines.all()`` walk in ``test_autoreloader``. Left as Django's, every one of
# them renders with stock Django while asserting exactly what it would assert
# against ours, and the suite is green about code it never ran. That is the one
# failure mode a vendored suite exists to make impossible.
DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"
OUR_BACKEND = "django_rust_templates.rust.RustTemplates"

# The same two classes split into the module they live in and the name they are
# bound to, for the one row that reaches the backend by *import* instead of by the
# string a project writes in settings.
BACKEND_MODULE, BACKEND_CLASS = DJANGO_BACKEND.rsplit(".", 1)
OUR_BACKEND_MODULE, OUR_BACKEND_CLASS = OUR_BACKEND.rsplit(".", 1)

# Where the engine's modules are on disk. This is the whole partition: a name is
# ours exactly when there is a file here for it.
ENGINE_DIR = ROOT / "python" / "django_rust_templates" / "engine"

# The ten names `tests/milestone_ownership.py` declares Django's. Imported from
# there rather than restated, so a name moving across the boundary moves in one
# place. `tests/` is not a package on this script's path, so it is added.
sys.path.insert(0, str(ROOT / "tests"))
from milestone_ownership import NOT_THE_ENGINE  # noqa: E402

# Names importable from the ``django.template`` *package* that are not this
# engine's, though the package is. ``NOT_THE_ENGINE`` is the same judgement at the
# granularity of a module; this is that judgement at the granularity of a name,
# and it has exactly one member.
#
# Django's own ``template/__init__.py`` splits itself in two and says which half
# is which: the Multiple Template Engines subsystem is ``backends``, ``loader``,
# ``response`` and the ``engines`` registry, and the Django Template Language is
# everything else. ``engines`` is the registry instance those three consult, and
# ``django.test.signals.reset_template_engines`` empties *that* instance on every
# ``TEMPLATES`` change. A ``from django.template import engines`` in a test is
# therefore a reference to Django's — rewriting it would need a second registry
# in this package for it to name, and a second registry is one nothing resets.
# That is not hypothetical: it is ``test_response.TemplateResponseTest``, whose
# backend is installed by a class-level ``override_settings`` that only Django's
# registry hears about.
NOT_THE_ENGINE_NAMES = {"engines"}


def _djangos(name: str) -> bool:
    """Is ``name``, imported from the ``django.template`` package, Django's?"""
    return name in NOT_THE_ENGINE or name in NOT_THE_ENGINE_NAMES



def ours(rest: str) -> bool:
    """Is `django.template.<rest>` (or the package itself, for "") this package's?

    `rest` is a dotted path with no leading dot. Decided by the filesystem:
    `engine/<rest>.py` or `engine/<rest>/__init__.py`. The first segment is what
    is tested, so `django.template.loader_tags` is ours and
    `django.template.loader` is not, though one is a prefix of the other.
    """
    if not rest:
        return True
    head = rest.split(".")[0]
    return (ENGINE_DIR / (head + ".py")).is_file() or (
        ENGINE_DIR / head / "__init__.py"
    ).is_file()


def our_templatetag(rest: str) -> bool:
    """Is `django.templatetags.<rest>` one of the engine's own libraries?"""
    return (ENGINE_DIR / "templatetags" / (rest.split(".")[0] + ".py")).is_file()


def _line_starts(source: str):
    """Character offset of each line, counting ``\\n`` and *only* ``\\n``.

    ``str.splitlines`` splits on ``\\v``, ``\\f``, ``\\x1c``-``\\x1e``, ``\\x85``
    and the Unicode line separators too. ``ast`` counts none of those. One form
    feed anywhere in a file would shift every line number after it and the
    rewrite would edit text one line off, silently — the failure this whole
    module is one splice bug deep into already.
    """
    starts, offset = [0], 0
    for line in source.split("\n")[:-1]:
        offset += len(line) + 1
        starts.append(offset)
    return starts


def _alias(alias: ast.alias) -> str:
    return alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"


def _spans(source: str):
    """``(edits, retarget)``: the rewrites in absolute offsets, and whether the
    backend class was reached by import so that the caller can say so to
    :func:`_verify`.

    ``edits`` are ``(start, end, replacement)``.

    **A mixed statement is split, not skipped.** `syntax_tests/test_include.py:1`
    is `from django.template import Context, Engine, TemplateDoesNotExist,
    TemplateSyntaxError, loader` — four of this package's names and one of
    Django's in a single statement. Leaving the statement alone because one name
    is Django's would point `Engine` and `Context` at Django while the file goes
    on to assert things about them, which is the one outcome this generator
    exists to make impossible. So a statement whose names straddle the boundary
    becomes two statements, one per owner. It is the only place the rewrite
    reformats rather than splices, and it affects exactly the files that need it.
    """
    starts = _line_starts(source)
    tree = ast.parse(source)
    out = []

    def at(lineno, column):
        return starts[lineno - 1] + column

    # **The backend class is reachable by two spellings and only one of them is a
    # string.** Everything above rewrites the ``BACKEND`` *literal* a
    # ``TEMPLATES`` entry carries; ``test_partials.py`` instead does ``from
    # django.template.backends.django import DjangoTemplates`` and calls it, and
    # the string rule never sees that. Measured, before this branch existed:
    # ``test_partials_use_cached_loader_when_configured`` built **Django's**
    # backend — Django's ``Engine`` — because that is what the name it imported
    # was bound to, while the ``loaders`` in the same ``OPTIONS`` dict *were*
    # rewritten, because those are dotted strings. The row rendered through a
    # hybrid that exists in no configuration at all and passed.
    #
    # **This rule cannot be derived from `ours()`, and that is the part worth
    # recording.** `ours()` is `engine/<rest>.py exists`, and the backend is
    # `django_rust_templates/rust.py` — outside `engine/`. The one component
    # of this package that is not under `engine/` is invisible to a partition
    # keyed on `engine/`, so it is spelled out here instead. A second such
    # component would inherit the same blindness.
    #
    # Renaming the bound name is what the whole-statement rewrite buys: the call
    # site is a bare `DjangoTemplates`, so it follows the import. It is safe here
    # because the file that imports it is the only place in the suite the name
    # occurs as code — asserted in `_verify`, which refuses the forms this rule
    # does not know (the attribute spelling, and the name in a file that never
    # imported it).
    retarget = any(
        isinstance(node, ast.ImportFrom)
        and not node.level
        and node.module == BACKEND_MODULE
        and any(a.name == BACKEND_CLASS for a in node.names)
        for node in ast.walk(tree)
    )

    for node in ast.walk(tree):
        if retarget and isinstance(node, ast.Name) and node.id == BACKEND_CLASS:
            end = at(node.lineno, node.col_offset) + len(BACKEND_CLASS)
            out.append((at(node.lineno, node.col_offset), end, OUR_BACKEND_CLASS))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                # A relative import. `from ..utils import setup` is relative to
                # the copy, and the copy has the same shape, so it already
                # resolves inside the vendored package. Untouched.
                continue
            if module == "django" and any(n.name == "template" for n in node.names):
                # `from django import template`, rewritten as a plain import of
                # the package under whatever name the file already uses, so all
                # of its `template.X` uses keep working. Split for the same
                # reason as below, in case this statement ever carries others.
                others = [n for n in node.names if n.name != "template"]
                own = [n for n in node.names if n.name == "template"][0]
                name = own.asname or "template"
                lines = [f"import {ENGINE} as {name}"]
                if others:
                    lines.append("from django import " + ", ".join(map(_alias, others)))
                out.append((at(node.lineno, node.col_offset), at(node.end_lineno, node.end_col_offset), "\n".join(lines)))
                continue
            if module == BACKEND_MODULE:
                # The statement is *replaced*, not repointed: this package's
                # backend is not Django's module under another name, it is a
                # different module entirely, so there is no prefix to splice.
                if [a.name for a in node.names] != [BACKEND_CLASS]:
                    raise SystemExit(
                        f"line {node.lineno} imports {module!r} expecting "
                        f"{BACKEND_CLASS!r} alone; the whole statement is "
                        f"replaced by this rule, so any other name would be "
                        f"dropped: {[_alias(a) for a in node.names]}"
                    )
                out.append(
                    (
                        at(node.lineno, node.col_offset),
                        at(node.end_lineno, node.end_col_offset),
                        f"from {OUR_BACKEND_MODULE} import {OUR_BACKEND_CLASS}",
                    )
                )
                continue
            if module == DJANGO:
                own = [n for n in node.names if not _djangos(n.name)]
                theirs = [n for n in node.names if _djangos(n.name)]
                if not own:
                    # `from django.template import autoreload`, or
                    # `from django.template import engines` — the package is
                    # ours, the only name is not, and this package has no
                    # `autoreload` and keeps no registry to import. Left as
                    # Django's.
                    continue
                if theirs:
                    out.append(
                        (
                            at(node.lineno, node.col_offset),
                            at(node.end_lineno, node.end_col_offset),
                            "from " + ENGINE + " import " + ", ".join(map(_alias, own))
                            + "\nfrom " + DJANGO + " import " + ", ".join(map(_alias, theirs)),
                        )
                    )
                    continue
                new = ENGINE
            elif module.startswith(DJANGO + ".") and ours(module[len(DJANGO) + 1 :]):
                new = ENGINE + module[len(DJANGO) :]
            elif module == TAGS or (
                module.startswith(TAGS + ".") and our_templatetag(module[len(TAGS) + 1 :])
            ):
                new = ENGINE + "." + module.replace("django.", "", 1)
            else:
                continue
            # A plain single-owner statement: edit the prefix only, width taken
            # from the module name. Never from a slice of the source — see the
            # note this function's first version earned.
            out.append(
                (
                    at(node.lineno, node.col_offset),
                    at(node.lineno, node.col_offset) + len("from ") + len(module),
                    "from " + new,
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                if not module.startswith(DJANGO + "."):
                    continue
                if not ours(module[len(DJANGO) + 1 :]):
                    continue
                new = ENGINE + module[len(DJANGO) :]
                # `alias.col_offset`, not the statement's: one `import a, b`
                # puts the second name somewhere else on the line.
                start = at(alias.lineno, alias.col_offset)
                out.append((start, start + len("import ") + len(module), "import " + new))
    return out, retarget


# A string literal naming a module this package owns. The dotted tail is
# required — a bare `"django.template"` is `test_logging.py`'s logger name, and
# `logging.getLogger("django_rust_templates.engine")` would make those three
# rows pass on nothing.
STRING = re.compile(
    r"(?P<quote>['\"])(?P<path>django\.template\.[A-Za-z_][\w.]*|django\.templatetags\.[A-Za-z_][\w.]*)(?P=quote)"
)

# String literals the rule above would rewrite and must not, keyed
# ``(file, path)`` with the reason each one is not ours.
#
# **Keyed by file as well as path, and it has to be**, because the path below is
# legitimately ours everywhere else in the suite: `cached.Loader` is ours. The
# question a string literal answers is not "do we ship this module" — that is what
# `ours()` settles — but "which class object will the *fixture* this string is
# part of actually instantiate", and that is a fact about the test, not about the
# module tree.
#
# **The key is the whole literal, attribute included**, because the key is matched
# against what :data:`STRING` captures and that pattern's tail is `[\w.]*`, which
# is greedy: in ``mock.patch("django.template.loaders.cached.Loader.reset")`` the
# capture is ``django.template.loaders.cached.Loader.reset`` and not the dotted
# prefix of it. `ours()` looks only at the *first* segment, ``loaders``, which has
# an ``__init__.py``, so the attribute does not save the prefix from being
# rewritten — it only means a key without it matches nothing and exempts nothing.
STRING_EXEMPT = {
    ("test_autoreloader.py", "django.template.loaders.cached.Loader.reset"): (
        "`TemplateReloadTests.test_form_template_reset_template_change_reset_call` "
        "and `test_form_template_reset_template_change_no_djangotemplates` patch a "
        "`reset` and assert it is reached by `django.template.autoreload."
        "reset_loaders()`. That function resets two things: every backend in the "
        "registry, and `get_default_renderer()`'s. The first is the class "
        "`TEMPLATES` in this file, which names our loaders explicitly and so "
        "wraps them in *no* `cached.Loader` — which is why the sibling row "
        "`test_reset_all_loaders` patches `loaders.base.Loader.reset`, ours, and "
        "counts 2. The second is `django.forms.renderers.DjangoTemplates`, which "
        "is Django's and builds Django's `Engine`, so the `cached.Loader` it "
        "wraps `APP_DIRS` in is Django's. Nothing in either row instantiates "
        "ours, and rewriting the target points the assertion at a class that is "
        "never built — the row then fails with `Called 0 times`."
    ),
}


def _import_targets(source: str):
    """Every absolute import in `source` as ``(module, names)``."""
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.append((node.module, [n.name for n in node.names]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, []))
    return out


def _owned(module: str) -> bool:
    """Does `module` name something this package owns, in Django's spelling?"""
    if module == DJANGO:
        return ours("")
    if module.startswith(DJANGO + "."):
        return ours(module[len(DJANGO) + 1 :])
    if module == TAGS:
        return True
    if module.startswith(TAGS + "."):
        return our_templatetag(module[len(TAGS) + 1 :])
    return False


# The alias Django gives a ``TEMPLATES`` entry that does not name itself, and the
# rule that produces it. ``EngineHandler.__init__`` reads
# ``default_name = tpl["BACKEND"].rsplit(".", 2)[-2]``, so Django's backend,
# ``django.template.backends.django.DjangoTemplates``, is addressed as
# ``"django"`` — which is the name the suite uses: ``engines["django"]`` in
# ``test_response``, ``test_context`` and ``test_partials``.
DJANGO_ALIAS = DJANGO_BACKEND.rsplit(".", 2)[-2]


def _name_insertions(source: str):
    """``(start, end, text)`` inserting the derived ``"NAME"`` entry into a fixture.

    **Rewriting the backend string renames the engine, and that is not obvious
    from the line being edited.** The alias is not a property of the backend
    class — it is arithmetic on the *string*, and the two strings' second-to-last
    segments differ: Django's spelling gives ``"django"`` and this package's
    (``django_rust_templates.rust.RustTemplates``) gives ``"rust"``. So a
    `TEMPLATES` literal whose `BACKEND` is rewritten from
    Django's to ours silently stops answering to ``"django"``, and every row that
    reaches the engine through the registry by that name raises
    ``InvalidTemplateEngineError: Could not find config for 'django' in
    settings.TEMPLATES`` instead of testing anything. Measured: the four
    ``test_response.TemplateResponseTest`` errors, whose class-level
    ``override_settings`` is the fixture.

    An entry that names itself is left alone — ``test_engine`` spells ``"NAME":
    "default"`` and ``"other"``, and those are names the test chose deliberately.
    Only a fixture whose alias is currently *derived* gets one written in.

    **And that makes this key the one place the suite pins a value upstream
    computes.** The alias is an implementation detail of ``EngineHandler``, not a
    property the four ``test_response`` rows are about — which is why they must
    not be allowed to fail for seeing it change, and why the key is written down
    rather than derived at run time. The cost is that if upstream ever changes the
    derivation, their fixture follows and ours does not, silently. So: this key is
    load-bearing, not cosmetic, and a Django bump that touches
    ``EngineHandler.__init__`` is the moment to re-read it.

    The literal is located twice, once by the syntax tree and once by
    :data:`STRING`, and the two are matched up **by line number**: the tree knows
    which dicts already carry a ``NAME``, the pattern knows which text to splice
    against, and neither knows both. One backend string per line is the
    assumption, and it is asserted rather than hoped for.
    """
    by_line = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        named = any(isinstance(k, ast.Constant) and k.value == "NAME" for k in node.keys)
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "BACKEND"
                and isinstance(value, ast.Constant)
                and value.value == DJANGO_BACKEND
            ):
                if value.lineno in by_line:
                    raise SystemExit(
                        f"two backend literals on line {value.lineno}; the "
                        f"line-keyed match below cannot tell them apart"
                    )
                by_line[value.lineno] = named

    out = []
    for match in STRING.finditer(source):
        if match.group("path") != DJANGO_BACKEND:
            continue
        lineno = source.count("\n", 0, match.start()) + 1
        if lineno not in by_line:
            raise SystemExit(
                f"a backend literal on line {lineno} is not the value of a "
                f"BACKEND entry, so there is no dict to add a NAME to"
            )
        if by_line.pop(lineno):
            continue
        line = source.rfind("\n", 0, match.start()) + 1
        indent = source[line : match.start()]
        indent = indent[: len(indent) - len(indent.lstrip())]
        # Inserted *after* the comma that already terminates the BACKEND entry,
        # carrying its own. Splitting the pair is the whole edit; a NAME spliced
        # between the literal and its comma gives ``"django",,`` and the file stops
        # parsing, which `_verify` catches from one file and one traceback away
        # from the line that caused it.
        if source[match.end() : match.end() + 1] != ",":
            raise SystemExit(
                f"the BACKEND entry at offset {match.start()} is not followed by "
                f"a comma, so a NAME inserted after it would not separate two entries"
            )
        at = match.end() + 1
        out.append((at, at, f'\n{indent}"NAME": "{DJANGO_ALIAS}",'))
    if by_line:
        raise SystemExit(
            f"BACKEND entries on line(s) {sorted(by_line)} were never matched by "
            f"the literal pattern"
        )
    return out


def rewrite(source: str, relative: pathlib.Path) -> str:
    """The vendored text for one source file, checked before it is returned.

    A splice bug does not fail to compile in general — it fails to compile, or
    it renames the wrong thing, and the second is worse because the suite goes
    on running. Both directions are asserted here, and they are opposite halves
    of one rule:

    * nothing this package owns is left spelled ``django.template.*``, except a
      statement that is *entirely* names declared Django's; and
    * nothing Django owns has been renamed — checked as a positive control over
      the imports that were **not** rewritten, because "no ``django.template``
      remains" is satisfied perfectly by a rewrite that takes Django's too.

    The second is the one a hand-check forgets: the first is what you look at.

    ``relative`` is the destination path, and the one rule that needs it is
    :data:`STRING_EXEMPT`. The one rule that is not a substitution at all is
    :func:`_name_insertions`, which *adds* a key to the fixture: a `TEMPLATES`
    entry that Django's backend string left unnamed is addressed by an alias
    derived from that string, so rewriting the string without writing the alias
    down would rename the engine out from under its own tests.
    """
    edits, retarget = _spans(source)
    before = _import_targets(source)
    for match in STRING.finditer(source):
        path = match.group("path")
        if path == DJANGO_BACKEND:
            edits.append((match.start("path"), match.end("path"), OUR_BACKEND))
            continue
        if (relative.as_posix(), path) in STRING_EXEMPT:
            continue
        if path.startswith(TAGS):
            rest = path[len(TAGS) + 1 :]
            if not our_templatetag(rest):
                continue
            new = ENGINE + "." + path.replace("django.", "", 1)
        else:
            rest = path[len(DJANGO) + 1 :]
            if not ours(rest):
                continue
            new = ENGINE + path[len(DJANGO) :]
        edits.append((match.start("path"), match.end("path"), new))

    edits.extend(_name_insertions(source))

    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]

    _verify(source, before, retarget)
    return source


def _verify(result: str, before, retarget: bool) -> None:
    """Assert the two halves described in `rewrite`, or raise."""
    try:
        tree = ast.parse(result)
    except SyntaxError as error:
        raise SystemExit(f"the rewrite produced a file that does not parse: {error}") from error

    after = _import_targets(result)
    for module, names in after:
        if _owned(module) and not all(_djangos(name) for name in names):
            raise SystemExit(f"the rewrite left {module!r} pointing at Django")

    # The positive control. `django.template` and the bare `django` root are the
    # only non-owned targets the rules above may legitimately remove; everything
    # else in `before` that is Django's must still be imported by name.
    kept = {
        module
        for module, _ in before
        if not _owned(module) and module != "django" and module.split(".")[0] != ENGINE
    }
    # The backend module joins that short list for one file, because the rule
    # above *replaces* its import rather than repointing it — the class this
    # package ships is in a different module, so the name cannot follow the
    # prefix the way `django.template.base` can. Subtracted only where the rule
    # actually fired, so a file that reaches the class by a spelling the rule
    # does not know still raises here instead of losing its import quietly.
    if retarget:
        kept.discard(BACKEND_MODULE)
    elif BACKEND_MODULE in {module for module, _ in before}:
        raise SystemExit(
            f"a file imports {BACKEND_MODULE!r} by a spelling the rewrite does "
            f"not know, so its backend is still Django's"
        )
    now = {module for module, _ in after}
    missing = sorted(kept - now)
    if missing:
        raise SystemExit(f"the rewrite renamed something Django's: {missing}")

    # And the closure on the rename above: the two spellings it does not handle.
    # An attribute access would be `import django.template.backends.django as b;
    # b.DjangoTemplates`, and a bare name in a file with no such import can only
    # be a local binding this rule has no business touching. Neither occurs in
    # the suite today; both are one plausible edit away, and both would leave the
    # row green against Django's engine.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == BACKEND_CLASS:
            raise SystemExit(
                f"the rewrite produced an attribute access to {BACKEND_CLASS!r}, "
                f"which the import rule cannot see"
            )
        if isinstance(node, ast.Name) and node.id == BACKEND_CLASS:
            raise SystemExit(
                f"the rewrite produced a bare {BACKEND_CLASS!r} in a file it did "
                f"not retarget, so that name does not bind what it looks like"
            )


def sources():
    """`(destination path, vendored content)` for every vendored byte.

    Absolute destination paths, because the trees in :data:`TREES` do not share a
    root and a relative spelling would need a second, per-tree convention for
    what it is relative *to* — which is the drift this script exists to catch,
    reintroduced into the script. Sorted within a tree; :func:`check` compares
    per tree for the same reason.
    """
    for source, destination, rewrite_python in TREES:
        for path in sorted(source.rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            relative = path.relative_to(source)
            if path.suffix == ".py" and rewrite_python:
                yield (
                    destination / relative,
                    rewrite(path.read_text(encoding="utf-8"), relative),
                )
            else:
                yield destination / relative, path.read_bytes()


def check_partition() -> None:
    """Assert the two spellings of the partition agree.

    `ours` reads the filesystem; `tests/milestone_ownership.py` records ten names
    as Django's with a reason each. They are the same rule said twice, and today
    they agree — nothing in `NOT_THE_ENGINE` has a file here. If one ever appears,
    the two would quietly disagree, and the consequence would be a suite that
    tests Django's module while the ownership table says it is testing nothing.
    Cheaper to make that impossible than to notice it.
    """
    for name in sorted(NOT_THE_ENGINE):
        relative = name.replace(".", "/")
        for path in (ENGINE_DIR / (relative + ".py"), ENGINE_DIR / relative / "__init__.py"):
            if path.is_file():
                raise SystemExit(
                    f"{name!r} is declared Django's in tests/milestone_ownership.py "
                    f"and also exists at {path.relative_to(ROOT)}"
                )


def write():
    files = dict(sources())
    for target, content in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    for _, destination, _ in TREES:
        shutil.rmtree(destination / "__pycache__", ignore_errors=True)
    roots = ", ".join(sorted(str(d.relative_to(ROOT)) for _, d, _ in TREES))
    print(f"wrote {len(files)} files under {roots}")


def check() -> int:
    """Re-derive every file and diff it against what is on disk."""
    drift = 0
    expected = dict(sources())
    on_disk = {
        path: path
        for _, destination, _ in TREES
        for path in sorted(destination.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    for target in sorted(set(expected) | set(on_disk), key=str):
        if target not in on_disk:
            print(f"{target.relative_to(ROOT)}: missing from the tree")
            drift += 1
            continue
        if target not in expected:
            print(
                f"{target.relative_to(ROOT)}: in the tree but nothing in Django's "
                f"suite produces it"
            )
            drift += 1
            continue
        want = expected[target]
        got = on_disk[target].read_bytes()
        want_bytes = want if isinstance(want, bytes) else want.encode("utf-8")
        if want_bytes == got:
            continue
        drift += 1
        print(f"{target.relative_to(ROOT)}: edited by hand")
        for line in list(
            difflib.unified_diff(
                got.decode("utf-8", "replace").splitlines(),
                want_bytes.decode("utf-8", "replace").splitlines(),
                "on disk",
                "re-derived",
                lineterm="",
            )
        )[:20]:
            print("  " + line)
    if drift:
        print(f"\n{drift} file(s) differ; re-run scripts/gen_django_suite.py to rewrite them")
        return 1
    print(f"{len(expected)} vendored file(s) match the generator exactly")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="gate: exit 1 on drift")
    args = parser.parse_args()
    if not SRC.is_dir():
        print(f"no Django checkout at {SRC} — run scripts/fetch_django.sh", file=sys.stderr)
        return 1
    check_partition()
    if args.check:
        return check()
    write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
