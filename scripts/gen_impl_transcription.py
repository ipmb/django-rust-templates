"""Regenerate an ``engine`` transcription of a pinned ``django/template`` module.

Two relations are checkable here and this script owns the first: its output must
equal the committed file. The second — the committed file minus the pinned
source falls inside the declared regions — belongs to
``check_transcriptions.py``, and the two together are what make a 1207-line
transcription reviewable at all. The surface becomes "these regions differ",
which is a statement someone can check, where "we rewrote ``base.py``" is not.

``--check`` regenerates and diffs, like ``gen_python_word_ranges.py``, so a
legitimate move (a pin bump) is fixed by re-running the generator rather than by
editing a file by hand — and a hand edit to ``engine/base.py`` fails here.

The marker goes immediately after the module docstring's opening ``\"\"\"``:
**text, not a line number.** Anchoring to a line would make our file a function
of Django's line numbering, so the next pin bump would change it for a reason
that is not ours — the drift class this project has spent two milestones
removing, reintroduced in the one place where it would look like a legitimate
regeneration.

Not every transcribed module opens with a docstring, and M10's nine are where
that stopped being hypothetical: ``context.py``, ``engine.py`` and
``loaders/base.py`` open with imports, and ``loaders/__init__.py`` is 0 bytes
upstream. (``utils.py`` was a fourth until ``03038e6`` deleted it.) For those the marker is a comment on line 1 followed by a blank line —
:data:`render` picks the form from the source, and ``check_transcriptions.py``'s
``MARKER_WINDOW`` accepts both. **Either way the insertion is exactly two
lines**, which is what the declared regions and every measured ``COMPARED_PATH``
span for these files are written against.

Keys are file names (``"base.py"``), not module stems, because that is the
spelling ``DECLARED`` and ``COMPARED_PATH`` both use, and the spelling
``shadowing_files()`` reports: one spelling of a file's identity. It was
``_shim.SHADOWING``'s spelling until M18 deleted that table, and the third
member of the set is now the filesystem rather than a table.

Usage:
    python -I scripts/gen_impl_transcription.py base.py smartif.py
    python -I scripts/gen_impl_transcription.py --check
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT = ROOT / ".django"
IMPL = ROOT / "python" / "django_rust_templates" / "engine"

MODULES = {
    "context.py": "django/template/context.py",
    "smartif.py": "django/template/smartif.py",
    # **Two M0-era rows left this table at M18, and neither left a hole.**
    # `utils.py` was deleted from the tree by `03038e6` and a row here for a file
    # `engine` does not have fails `--check` in every commit — which is the same
    # reason the table's own comment gives for adding a row in the commit that
    # creates its file, read backwards. `engine.py` moved to `HAND_WRITTEN`
    # below, because M18.1's rewiring is more than this table can express.
    # M10's six loader files. The first keys in this table that are not a single
    # segment, which is the point: a key is an `engine`-relative posix path here as
    # it is in `DECLARED` and `COMPARED_PATH`, and the value is
    # `django/template/` + the key for every row in the table — including these,
    # where the key carries the subdirectory the value has to carry too.
    "loaders/__init__.py": "django/template/loaders/__init__.py",
    "loaders/base.py": "django/template/loaders/base.py",
    "loaders/filesystem.py": "django/template/loaders/filesystem.py",
    "loaders/app_directories.py": "django/template/loaders/app_directories.py",
    "loaders/cached.py": "django/template/loaders/cached.py",
    "loaders/locmem.py": "django/template/loaders/locmem.py",
    # M12's first, and the first row the sentence above stops holding for:
    # this lives under `django/templatetags/`, not `django/template/`, which is
    # why every value here is spelled out in full rather than derived. It is
    # still the same *shape* of transcription — the pin plus a marker — because
    # the four tags Rust compiles out of these two modules are unreachable
    # through the host: `ShellHost.has_tag` answers False for a name Rust owns
    # and the template has loaded, so `do_cache` and `do_static` are bodies
    # nothing calls. Faithful is what a transcription is for; the case that needs
    # these bodies right is a library that rebinds one of the four names.
    #
    # **`templatetags/__init__.py` is deliberately absent**, though the plan
    # listed it. The shim's `_is_foreign_package` declines to serve the
    # `django.templatetags` package itself — only files inside it — so an
    # `engine/templatetags/__init__.py` would never be served while
    # `shadowing_files()` still reported it. Upstream's is 0 bytes: there is no
    # behaviour in it to transcribe.
    "templatetags/cache.py": "django/templatetags/cache.py",
    "templatetags/static.py": "django/templatetags/static.py",
    # M12 Task 4's, the third of the five. Pin-plus-marker like the two above,
    # and for their reason: its three tag bodies (`localtime_tag`,
    # `timezone_tag`, `get_current_timezone_tag`) are unreachable through the
    # host once Rust compiles the names, and its three filters
    # (`localtime`, `timezone`, `utc`) are Task 7's Rust bodies — so nothing
    # here delegates to Rust and the generator can write the whole file.
    # `do_timezone`, the helper registered as the `timezone` filter, stays
    # Python-side too; the commit says why.
    "templatetags/tz.py": "django/templatetags/tz.py",
    # M12 Task 5's, the fourth of the five, and the smallest: one class
    # (`LocalizeNode`), one tag, two filters. Pin-plus-marker like the three
    # above, and for their reason — Rust compiles the one tag name, so its body
    # (`localize_tag`) is unreachable through the host, and the two filters are
    # Task 7's Rust bodies. The `django.utils.formats` delegation the two filters
    # make stays Python's here (the tag's *render*, `LocalizeNode.render`, is
    # Python's too, and is our transcription's), so nothing in this file
    # delegates to Rust and the generator can write the whole of it.
    "templatetags/l10n.py": "django/templatetags/l10n.py",
    # M12 Task 6's, the fifth and last of the five, and the largest (616 lines).
    # Pin-plus-marker like the four above, and for their reason: Rust compiles
    # **eight** of its ten registered tag names — every one but
    # `blocktranslate`/`blocktrans`, which are Task 6b's — so those eight bodies
    # are unreachable through the host once the names are Rust's, and the four
    # filters (`language_name`, `language_name_translated`, `language_name_local`,
    # `language_bidi`, `i18n.py:293,298,304,309`) are Task 7's Rust bodies. The
    # `django.utils.translation` delegation every node's *render* makes
    # (`i18n.py:19,31,44,59,68,169,177,213` and the filter bodies) stays Python's,
    # because the render is Python's on every path (§6's framework-coupling test,
    # the same sentence `l10n.py`'s and `tz.py`'s rows carry): Rust owns the
    # *compile* functions, and nothing in this file delegates to Rust. So the
    # generator writes the whole of it.
    #
    # This is the fifth of the five, so `unshimmed(scope="django.templatetags")`
    # reaches zero with the same commit — the second scope of the milestone's
    # meter, which started at Django's five libraries.
    "templatetags/i18n.py": "django/templatetags/i18n.py",
    # M11's first, and the only one this table names yet. Unlike M10's loader
    # files it is a module the tag and filter corpus renders through, so a
    # verbatim transcription here moves the *registry's* owner and the node
    # classes' owner; the routing that puts the filter bodies on Rust's path is
    # `base.py`'s and is hand-written.
    #
    # `defaultfilters.py` and `defaulttags.py` are M11's other two and are
    # deliberately **not** listed: this table is also what `--check` walks, and a
    # name here for a file `engine` does not have fails the generator step in
    # every commit that runs before the task which creates it. Each is added by
    # its own task, in the same commit as the file it generates — measured, in
    # the commit that interposed `loader_tags` alone.
    "loader_tags.py": "django/template/loader_tags.py",
    # M11's second. Its 59 filter bodies stay verbatim — the routing that puts
    # them on Rust's path is a single branch in `base.py`'s
    # `FilterExpression.resolve`, and a delegate per body would be 59 declared
    # regions all saying the same thing. So this file is pin-plus-marker and the
    # generator owns it, which is the opposite of `base.py` (see HAND_WRITTEN).
    "defaultfilters.py": "django/template/defaultfilters.py",
    # M11's third and last, added by its own task in the same commit as the file
    # the row above describes the rule for.
    #
    # It belongs in this table rather than in HAND_WRITTEN because its tag
    # bodies stay verbatim. Measured, since the plan's counts were not: the
    # module registers 24 tags, 20 of them Rust's — those bodies are unreachable
    # through the render path, because the host's `has_tag` answers False for a
    # name Rust compiles. The four that are reached are `load` and `querystring`,
    # which Rust does not own, and the two stubs `partial`/`partialdef`. Faithful
    # is what a transcription is for, and the case that needs the bodies right is
    # a `{% load %}`ed library rebinding a built-in tag.
    "defaulttags.py": "django/template/defaulttags.py",
    # **This row existed for a day and was removed with the `DECLARED` row it
    # fed.** `engine/__init__.py:57` is `from . import autoreload`, and that one
    # line is why `import django_rust_templates.engine` raised
    # `ImportError: cannot import name 'autoreload'`: the import resolves through
    # the package's `__path__`, which the finder widens to include
    # `django/template/`, and imported as `engine` there is no second path entry,
    # so the sibling has to be a real file. Transcribing it was the fix that needs
    # no exception; guarding the import is the better one, for the reason
    # `tests/milestone_ownership.py` gives `autoreload` as `NOT_THE_ENGINE` — the
    # module is three receivers that reset *Django's* loaders, and a transcription
    # is a promise to keep a Django file faithful across releases. The four-line
    # guard in `engine/__init__.py` is a region `check_transcriptions.py` already
    # declares, and it is the same four lines the end state needs.
}

# Transcriptions this generator must **not** write, because their difference from
# the pin is more than the marker: `base.py` delegates `Template.compile_nodelist`
# to Rust, in a declared region. Regenerating it would silently replace that
# delegation with upstream's text — a change `check_transcriptions.py` permits
# only because it is declared, and one this tool cannot express.
#
# That is the whole partition, and it is why this list exists rather than a
# per-file flag: **this tool produces "the pin plus a marker", and a file whose
# difference is more than that is the guard's, not this one's.** Keeping
# `base.py` in `MODULES` is what made this script stale for two milestones
# without anyone noticing — `--check` had been failing on `base.py` since M9
# wrote the delegation by hand, and nothing invoked it. M10 measured it and puts
# it on the fast path, which is why the failure could no longer be ignored.
#
# **`engine.py` is the second, from M18.1, and it is the reason the sentence
# above is about the *kind* of difference rather than about delegations.**
# Its rewiring touches the pin in four shapes this tool cannot express, and any
# one of them alone would have been enough:
#
# * `default_builtins` and the `loaders` list name this package's modules rather
#   than `django.template`'s — three one-line replacements, which is a row shape
#   the table *does* have;
# * the `app_dirs` arm is **one line in and three lines out**, because the dotted
#   path it names is longer than a line allows (`app_directories.Loader` alone
#   would overflow), and the table's one-line-to-one-line invariant is what keeps
#   every `COMPARED_PATH` span in `check_transcriptions.py` valid;
# * `import functools` and the `@functools.lru_cache` on `get_default` are **two
#   lines out and none in** — M18.1 drops the cache, because the receiver that
#   invalidates it (`reset_template_engines`) names *Django's* class object;
# * `get_default`'s `isinstance` grows a second arm, `RustTemplates`, which is a
#   line upstream has no counterpart for at all.
#
# `base.py` is here for the first reason and `engine.py` for the other three, and
# between them they are the whole of the list: **this tool produces "the pin plus
# a marker", and a file whose difference is more than that is the guard's, not
# this one's.** Keeping `base.py` in `MODULES` is what made this script stale for
# two milestones without anyone noticing — `--check` had been failing on `base.py`
# since M9 wrote the delegation by hand, and nothing invoked it. M10 measured it
# and puts it on the fast path, which is why the failure could no longer be
# ignored.
HAND_WRITTEN = {
    "base.py": "django/template/base.py",
    "engine.py": "django/template/engine.py",
}

# Lines an `engine` transcription does **not** take verbatim, as whole-line
# substitutions keyed by the upstream line's exact text.
#
# M18.1 made `engine` importable without the meta-path finder, and this table is
# the whole of what that cost the transcriptions. `engine` may be imported as
# `django.template` (the finder serves it under that name, with a `__path__` that
# falls back to the checkout) or as `django_rust_templates.engine` (a plain package
# that resolves to itself).
#
# **There are two rewrite forms, and which one a file gets is decided by where
# the file itself is served — not by taste.** `engine/__init__.py`,
# `engine/base.py`, `engine/parser.py` and `engine/loaders/*.py` are all served under
# names whose parent chain passes through the engine package, so `from .. import
# X` names it in both modes and is used there.
#
# The five `engine/templatetags/*.py` cannot use that form, and an earlier version
# of this table wrongly said they could. Upstream those files live under
# `django/templatetags/`, a **sibling** of `django.template`, and the finder
# serves them under exactly those names; so `..` there is `django`, and `from ..
# import Library` asks `django` for a `Library` it has never had:
#
#     ImportError: cannot import name 'Library' from 'django'
#
# Standalone the same file is `django_rust_templates.engine.templatetags.X`, where
# `..` *is* the engine package, so the two modes want `..` and `..template`
# respectively — no relative spelling is correct in both, and one of them
# silently binds stock Django. Those files therefore name the engine package
# **absolutely**, by the stable name §"importable identity" specifies:
# `django_rust_templates.engine`, which `engine/__init__.py` registers as itself so
# that it is the same object the finder serves rather than a second copy of
# `library.py` with a second `Library` class.
#
# Three properties make the table safe to keep here rather than in prose:
#
# * **every row is one line to one line**, so no transcription's line count
#   moves and every `COMPARED_PATH` span in `check_transcriptions.py` stays
#   valid. An expansion — `from django.template import Origin, TemplateDoesNotExist`
#   becoming two lines — would shift every span below it and force a
#   re-measurement; `from .. import Origin, TemplateDoesNotExist` does not;
# * `engine/__init__.py` re-exports `Library`, `Node`, `Template`, `Origin`,
#   `VariableDoesNotExist`, `TemplateSyntaxError` and `TemplateDoesNotExist`, so
#   `from django.template import X` — which binds attributes *of that package* —
#   becomes an import of that package with no other change;
# * **a row that stops matching is an error, not a no-op.** See `_transform`.
#
# Two upstream lines that look like this table's business are deliberately
# absent. `loaders/cached.py`'s `from django.template.backends.django import
# copy_exception` names a module `engine` has no counterpart for and should not:
# it is Django's backend layer, `copy_exception` is an ordinary function with no
# identity requirement, and the import is correct as written in both modes.
# `engine.py`'s two imports inside `Engine.get_default` likewise name the
# *stock* backend on purpose — that method exists to find a `DjangoTemplates`
# configured in `TEMPLATES`, and our backend is not one.
TRANSFORMS = {
    "base.py": {
        "from django.template.context import BaseContext":
            "from .context import BaseContext",
    },
    "loaders/base.py": {
        "from django.template import Template, TemplateDoesNotExist":
            "from .. import Template, TemplateDoesNotExist",
    },
    "loaders/filesystem.py": {
        "from django.template import Origin, TemplateDoesNotExist":
            "from .. import Origin, TemplateDoesNotExist",
    },
    "loaders/locmem.py": {
        "from django.template import Origin, TemplateDoesNotExist":
            "from .. import Origin, TemplateDoesNotExist",
    },
    "loaders/cached.py": {
        "from django.template import TemplateDoesNotExist":
            "from .. import TemplateDoesNotExist",
    },
    # Absolute, not relative: `..` from here is `django`, so no relative form
    # reaches the engine package in both modes. See the table's comment.
    "templatetags/cache.py": {
        "from django.template import Library, Node, TemplateSyntaxError, VariableDoesNotExist":
            "from django_rust_templates.engine import Library, Node, TemplateSyntaxError, VariableDoesNotExist",
    },
    "templatetags/i18n.py": {
        "from django.template import Library, Node, TemplateSyntaxError, Variable":
            "from django_rust_templates.engine import Library, Node, TemplateSyntaxError, Variable",
        "from django.template.base import TokenType, render_value_in_context":
            "from django_rust_templates.engine.base import TokenType, render_value_in_context",
        "from django.template.defaulttags import token_kwargs":
            "from django_rust_templates.engine.defaulttags import token_kwargs",
    },
    "templatetags/l10n.py": {
        "from django.template import Library, Node, TemplateSyntaxError":
            "from django_rust_templates.engine import Library, Node, TemplateSyntaxError",
    },
    "templatetags/tz.py": {
        "from django.template import Library, Node, TemplateSyntaxError":
            "from django_rust_templates.engine import Library, Node, TemplateSyntaxError",
    },
    # M19 step 5's, and the first rows in this table that rewrite a **statement**
    # rather than a name. `loader_tags.py` is generated, and it stays generated:
    # the code the substitution calls (`NodeList._render_with_plan`) lives in
    # `base.py`, which is hand-written already, so nothing here needs a region
    # upstream has no counterpart for and `HAND_WRITTEN` is not the answer.
    #
    # **What the two rows buy is that this file's shape does not change.** They
    # are one line to one line, so the file stays 372 lines (370 of the pin plus
    # the marker), the `COMPARED_PATH` spans in `check_transcriptions.py` are
    # valid before and after, and the parity harness's view of the module the
    # suite's block semantics live in is bit-identical across the slice. That
    # property is why a module-level `_render_block_body` helper here was
    # withdrawn: a new function moves a line count, and a line count inside the
    # one module `test_extends.py` and `recursive_templates/` exercise is a
    # change to the instrument that reads it.
    #
    # **Which lines, and why those are the funnel.** `BlockNode.render` reaches a
    # block body exactly twice — the no-block-context arm (`self.nodelist`) and
    # the override arm (`block.nodelist`, which is `type(self)(block.name,
    # block.nodelist)`'s reference to the same object) — and `super()` is covered
    # by the second, because it re-enters through `self.render`. Both keys are
    # upstream's own text, which is what makes the rows checkable: a Django pin
    # bump that rewords either line fails this generator loudly instead of
    # leaving a body render that silently stops taking the plan.
    #
    # **Why the arm is a call and not a branch in `NodeList.render`, which would
    # have left this file alone.** That method is on the guard's compared path
    # (`(1181, 1182)`), and `check_transcriptions.py`'s clause (4) refuses a
    # declared region there for the reason it exists: the parity harness executes
    # it, so the comparison would be Rust against Rust. The arm's home and the
    # arm's caller are therefore two different files, and this is the caller's.
    "loader_tags.py": {
        "                result = self.nodelist.render(context)":
            "                result = self.nodelist._render_with_plan(context)",
        "                result = block.nodelist.render(context)":
            "                result = block.nodelist._render_with_plan(context)",
    },
    # The one *import* row that is not a rewrite of a *name*. `static.py` reaches the
    # package's own attributes — `template.Library()`, `template.Node`,
    # `template.TemplateSyntaxError` — at seven sites, so rewriting the import
    # would mean rewriting the body and the transcription would stop being
    # upstream's. `engine/__init__.py` therefore binds `template` to itself, and
    # this import becomes an import of that binding with the body untouched.
    "templatetags/static.py": {
        "from django import template": "from django_rust_templates import engine as template",
    },
}

# Lines a transcription adds that upstream has **no counterpart for at all** — a
# comment block above a line the file otherwise takes verbatim, keyed by that
# line's exact upstream text.
#
# **This is the second row shape, and it is separate from `TRANSFORMS` because it
# is the one that moves a line count.** `TRANSFORMS`' one-line-to-one-line
# invariant is what keeps every `COMPARED_PATH` span in `check_transcriptions.py`
# valid across a regeneration; a block here does move the spans below it, so
# keeping the two tables apart is what stops that invariant from quietly becoming
# "usually true". The moved spans are not silent either way: they are *measured*
# from the committed files by `tests/test_compared_path_instrument.py`, so a
# regeneration that inserted a block would fail there rather than here.
#
# **M18.2 wrote both of these, and a block rather than a `TRANSFORMS` row is the
# honest spelling for each.** Neither comment is a rewrite of upstream's line —
# the line underneath is either taken verbatim (`app_directories.py`'s import,
# which is Django's own line and the whole reason this package has no
# `engine/utils.py`) or rewritten by a row in the table above (`static.py`'s). What
# is new is the *explanation*, and an explanation with nowhere to live in a
# pin-plus-marker tool is how the two of them came to be hand-written and
# unregenerable in the first place.
#
# Ordering is fixed and load-bearing: blocks are inserted **before** `TRANSFORMS`
# runs, so a block's key is always upstream's text and never the table's output.
COMMENTS = {
    "loaders/app_directories.py": {
        "from django.template.utils import get_app_template_dirs": [
            "# **Django's `get_app_template_dirs`, not a copy of it**, and this line is why",
            "# this package has no `engine/utils.py`. The function caches its answer — it",
            "# walks every installed app and stats a directory in each — and the cache is",
            "# invalidated by ``django.test.signals.update_installed_apps``, which clears",
            "# ``django.template.utils.get_app_template_dirs``. A second function object holds",
            "# a second cache that receiver cannot reach: measured, ours returned",
            "# ``[django_suite/template_tests/templates]`` under ``override_settings(",
            "# INSTALLED_APPS=[])`` while Django's returned ``[]``, which is the whole of",
            "# ``test_loaders.AppDirectoriesLoaderTests.test_not_installed``.",
        ],
    },
    "templatetags/static.py": {
        "from django import template": [
            "# Upstream's line here is `from django import template`, which binds the name",
            "# `template` to the DTL package. Ours binds the same name to the same package",
            "# under the name this package ships it as; the seven uses below are",
            "# `template.Library`, `template.Node` and `template.TemplateSyntaxError`, all",
            "# re-exported from `django_rust_templates.engine`'s root, so the body is",
            "# unchanged. The name stays `template` because the body is what is being",
            "# transcribed — a rename would make every later diff against upstream noisy.",
        ],
    },
}

# The pinned version, so a transcription says which upstream it transcribes and
# a bump has one place to change rather than one per file.
DJANGO_VERSION = "6.0.9"


def _transform(name: str, source: str) -> str:
    """``source`` with ``COMMENTS[name]`` and ``TRANSFORMS[name]`` applied.

    **The anchor line must be present exactly once, in both tables.** A row that
    quietly stopped matching — because the pin bumped and upstream reworded the
    line, or because someone edited the table's key — would leave the
    transcription importing from the checkout while `--check` reported a match,
    which is the silent-degradation shape this project has refused three times
    over. Counting the occurrences is what makes a pin bump break the generator
    *loudly*, in the commit that takes it.

    Comments first, then transforms: a comment's key is upstream's text and a
    transform's key is too, so the insertion has to happen while the line still
    reads the way the pin wrote it.
    """
    for anchor, block in COMMENTS.get(name, {}).items():
        found = source.count(anchor + "\n")
        if found != 1:
            raise SystemExit(
                f"{name}: the comment block anchored at {anchor!r} matches "
                f"{found} line(s) of the pin, not 1 — upstream has reworded it, "
                "or the table's key is wrong. Fix the key; do not drop the row."
            )
        source = source.replace(
            anchor + "\n", "".join(line + "\n" for line in block) + anchor + "\n"
        )
    for old, new in TRANSFORMS.get(name, {}).items():
        found = source.count(old + "\n")
        if found != 1:
            raise SystemExit(
                f"{name}: the transform for {old!r} matches {found} line(s) of "
                "the pin, not 1 — upstream has reworded it, or the table's key "
                "is wrong. Fix the key; do not drop the row."
            )
        source = source.replace(old + "\n", new + "\n")
    return source


def render(name: str) -> str:
    """The exact contents ``engine/<name>`` must have."""
    if name in HAND_WRITTEN:
        raise SystemExit(
            f"{name} differs from the pin by more than the marker — its declared "
            "region carries code upstream does not have, so this generator would "
            "overwrite it. Edit it by hand and let check_transcriptions.py check it."
        )
    if name not in MODULES:
        raise SystemExit(f"{name}: not a transcription; known: {sorted(MODULES)}")
    source = _transform(name, (CHECKOUT / MODULES[name]).read_text())
    marker = f"# TRANSCRIBED: {MODULES[name]} (Django {DJANGO_VERSION})\n"
    if source.startswith('"""\n'):
        return '"""\n' + marker + "\n" + source[4:]
    # No docstring to sit in, so the marker is a comment on line 1 with a blank
    # line after it — the same shape the docstring form has, two lines either
    # way, which is the property the span arithmetic depends on. This branch
    # used to be a SystemExit reading "does not open with a bare docstring; the
    # marker's insertion point is text-anchored and needs fixing by hand", which
    # was right while `base.py` was the only transcription and M10 made wrong:
    # four of its nine files open with imports and one is empty.
    return marker + "\n" + source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report instead of writing; exit 1 on any mismatch")
    parser.add_argument("module", nargs="*", default=sorted(MODULES))
    args = parser.parse_args()

    failures = 0
    for name in args.module:
        want = render(name)
        path = IMPL / name
        if args.check:
            got = path.read_text() if path.is_file() else ""
            if got != want:
                failures += 1
                print(f"{name} is not what the generator produces "
                      f"(run: scripts/gen_impl_transcription.py {name})")
            else:
                print(f"{name} matches the generator")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want)
            print(f"wrote {path.relative_to(ROOT)} ({len(want.splitlines())} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
