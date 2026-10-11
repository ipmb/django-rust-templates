"""Regenerate an ``engine`` transcription of a pinned ``django/template`` module.

This script owns one of the two checkable relations: its output must equal the committed
file. The other — the committed file minus the pinned source falls inside the declared
regions — belongs to ``check_transcriptions.py``.

``--check`` regenerates and diffs, so a legitimate move (a pin bump) is fixed by re-running
the generator rather than by editing a file by hand — and a hand edit to ``engine/base.py``
fails here.

The marker goes immediately after the module docstring's opening ``\"\"\"``: **text, not a
line number.** Anchoring to a line would make our file a function of Django's line
numbering, so the next pin bump would change it for a reason that is not ours.

Not every transcribed module opens with a docstring: ``context.py``, ``engine.py`` and
``loaders/base.py`` open with imports, and ``loaders/__init__.py`` is 0 bytes upstream.
For those the marker is a comment on line 1 followed by a blank line — :data:`render`
picks the form from the source, and ``check_transcriptions.py``'s ``MARKER_WINDOW``
accepts both. **Either way the insertion is exactly two lines**, which is what the
declared regions and every measured ``COMPARED_PATH`` span for these files assume.

Keys are file names (``"base.py"``), not module stems, because that is the spelling
``DECLARED``, ``COMPARED_PATH`` and ``shadowing_files()`` all use.

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
    # The six loader files. These are the first keys that are not a single segment:
    # a key is an `engine`-relative posix path here as in `DECLARED` and `COMPARED_PATH`,
    # and the value is `django/template/` + the key for every row — including these,
    # where the key carries the subdirectory.
    "loaders/__init__.py": "django/template/loaders/__init__.py",
    "loaders/base.py": "django/template/loaders/base.py",
    "loaders/filesystem.py": "django/template/loaders/filesystem.py",
    "loaders/app_directories.py": "django/template/loaders/app_directories.py",
    "loaders/cached.py": "django/template/loaders/cached.py",
    "loaders/locmem.py": "django/template/loaders/locmem.py",
    # Under `django/templatetags/`, not `django/template/`, which is why these values
    # are spelled out in full rather than derived. Same *shape* of transcription — the
    # pin plus a marker — because the tags Rust compiles out of these two modules are
    # unreachable through the host: `ShellHost.has_tag` answers False for a Rust-owned
    # name the template loaded, so `do_cache` and `do_static` are bodies nothing calls.
    #
    # **`templatetags/__init__.py` is deliberately absent**: the shim's
    # `_is_foreign_package` declines to serve the `django.templatetags` package itself
    # (only files inside it), so an `engine/templatetags/__init__.py` would never be
    # served while `shadowing_files()` still reported it. Upstream's is 0 bytes.
    "templatetags/cache.py": "django/templatetags/cache.py",
    "templatetags/static.py": "django/templatetags/static.py",
    # Pin-plus-marker like the two above: its three tag bodies (`localtime_tag`,
    # `timezone_tag`, `get_current_timezone_tag`) are unreachable through the host once
    # Rust compiles the names, and its three filters (`localtime`, `timezone`, `utc`) are
    # Rust bodies — so nothing here delegates to Rust and the generator writes the file.
    "templatetags/tz.py": "django/templatetags/tz.py",
    # The smallest of the five: one class (`LocalizeNode`), one tag, two filters.
    # Pin-plus-marker; Rust compiles the one tag name, so `localize_tag` is unreachable
    # through the host, and the two filters are Rust bodies. The `django.utils.formats`
    # delegation the two filters make stays Python's here, as does the tag's render, so
    # nothing in this file delegates to Rust.
    "templatetags/l10n.py": "django/templatetags/l10n.py",
    # The largest of the five. Pin-plus-marker: Rust compiles **eight** of its ten
    # registered tag names — every one but `blocktranslate`/`blocktrans`, which Rust
    # does not own — so those eight bodies are unreachable through the host, and the
    # four filters (`i18n.py:293,298,304,309`) are Rust bodies. The
    # `django.utils.translation` delegation every node's *render* makes
    # (`i18n.py:19,31,44,59,68,169,177,213`) stays Python's. The generator writes the
    # whole of it.
    "templatetags/i18n.py": "django/templatetags/i18n.py",
    # A module the tag and filter corpus renders through, so a verbatim transcription
    # here moves the *registry's* owner and the node classes' owner; the routing that
    # puts the filter bodies on Rust's path is `base.py`'s and is hand-written.
    #
    # `defaultfilters.py` and `defaulttags.py` are deliberately **not** listed here:
    # this table is also what `--check` walks, and a name for a file `engine` does not
    # have fails the generator step in every commit before the task that creates it.
    "loader_tags.py": "django/template/loader_tags.py",
    # Its 59 filter bodies stay verbatim — the routing that puts them on Rust's path is
    # a single branch in `base.py`'s `FilterExpression.resolve`, and a delegate per body
    # would be 59 declared regions all saying the same thing. Pin-plus-marker, the
    # opposite of `base.py` (see HAND_WRITTEN).
    "defaultfilters.py": "django/template/defaultfilters.py",
    # Belongs here rather than in HAND_WRITTEN because its tag bodies stay verbatim.
    # Measured: the module registers 24 tags, 20 of them Rust's — those bodies are
    # unreachable through the render path, because the host's `has_tag` answers False for
    # a name Rust compiles. The four reached are `load` and `querystring`, which Rust does
    # not own, and the two stubs `partial`/`partialdef`.
    "defaulttags.py": "django/template/defaulttags.py",
}

# Transcriptions this generator must **not** write, because their difference from the
# pin is more than the marker. `base.py` delegates `Template.compile_nodelist` to Rust in
# a declared region; regenerating it would silently replace that delegation with
# upstream's text.
#
# **This tool produces "the pin plus a marker", and a file whose difference is more than
# that is `check_transcriptions.py`'s, not this one's.**
#
# `engine.py` is the second, and its rewiring touches the pin in four shapes this tool
# cannot express: `default_builtins`/`loaders` name this package's modules rather than
# `django.template`'s (a one-line replacement shape the table *does* have); the
# `app_dirs` arm is **one line in and three lines out**, because the dotted path it names
# overflows a line, and the one-line-to-one-line invariant is what keeps every
# `COMPARED_PATH` span valid; `import functools` and `@functools.lru_cache` on
# `get_default` are **two lines out and none in** (the receiver that invalidates the
# cache names *Django's* class object); and `get_default`'s `isinstance` grows a second
# arm, `RustTemplates`, a line upstream has no counterpart for.
HAND_WRITTEN = {
    "base.py": "django/template/base.py",
    "engine.py": "django/template/engine.py",
}

# Lines an `engine` transcription does **not** take verbatim, as whole-line
# substitutions keyed by the upstream line's exact text.
#
# `engine` may be imported as `django.template` (the finder serves it under that name,
# with a `__path__` that falls back to the checkout) or as `django_rust_templates.engine`
# (a plain package that resolves to itself).
#
# **Two rewrite forms, decided by where the file itself is served.** `engine/__init__.py`,
# `engine/base.py`, `engine/parser.py` and `engine/loaders/*.py` are served under names
# whose parent chain passes through the engine package, so `from .. import X` names it in
# both modes. The five `engine/templatetags/*.py` cannot: upstream those files live under
# `django/templatetags/`, a **sibling** of `django.template`, served under exactly those
# names, so `..` there is `django` and `from .. import Library` raises
# `ImportError: cannot import name 'Library' from 'django'`. Standalone the same file is
# `django_rust_templates.engine.templatetags.X`, where `..` *is* the engine package — no
# relative spelling is correct in both, and one silently binds stock Django. Those files
# therefore name the engine package **absolutely**, by `django_rust_templates.engine`,
# which `engine/__init__.py` registers as itself so that it is the same object the finder
# serves rather than a second copy of `library.py` with a second `Library` class.
#
# Three properties make the table safe to keep here:
#
# * **every row is one line to one line**, so no transcription's line count moves and
#   every `COMPARED_PATH` span stays valid. An expansion would shift every span below it;
# * `engine/__init__.py` re-exports `Library`, `Node`, `Template`, `Origin`,
#   `VariableDoesNotExist`, `TemplateSyntaxError` and `TemplateDoesNotExist`, so
#   `from django.template import X` — which binds attributes *of that package* — becomes
#   an import of that package with no other change;
# * **a row that stops matching is an error, not a no-op.** See `_transform`.
#
# Two upstream lines that look like this table's business are deliberately absent.
# `loaders/cached.py`'s `from django.template.backends.django import copy_exception` names
# a module `engine` has no counterpart for and should not: Django's backend layer, whose
# `copy_exception` has no identity requirement. `engine.py`'s two imports inside
# `Engine.get_default` likewise name the *stock* backend on purpose — that method exists to
# find a `DjangoTemplates` configured in `TEMPLATES`, and our backend is not one.
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
    # M19 step 5's: the first rows in this table that rewrite a **statement** rather
    # than a name. `loader_tags.py` stays generated — the code the substitution calls
    # (`NodeList._render_with_plan`) lives in `base.py`, already hand-written, so nothing
    # here needs a region upstream has no counterpart for.
    #
    # **What the two rows buy is that this file's shape does not change.** They are one
    # line to one line, so the file stays 372 lines, the `COMPARED_PATH` spans are valid
    # before and after, and the parity harness's view of the module the suite's block
    # semantics live in is bit-identical across the slice. A module-level helper here was
    # withdrawn for that reason: a new function moves a line count, and a line count
    # inside the one module `test_extends.py` and `recursive_templates/` exercise is a
    # change to the instrument that reads it.
    #
    # `BlockNode.render` reaches a block body exactly twice — the no-block-context arm
    # (`self.nodelist`) and the override arm (`block.nodelist`) — and `super()` is covered
    # by the second, because it re-enters through `self.render`. Both keys are upstream's
    # own text, so a pin bump that rewords either line fails this generator loudly.
    #
    # The arm is a call and not a branch in `NodeList.render` because that method is on
    # the guard's compared path (`(1181, 1182)`), and clause (4) refuses a declared region
    # there: the parity harness executes it, so the comparison would be Rust against Rust.
    "loader_tags.py": {
        "                result = self.nodelist.render(context)":
            "                result = self.nodelist._render_with_plan(context)",
        "                result = block.nodelist.render(context)":
            "                result = block.nodelist._render_with_plan(context)",
    },
    # The one *import* row that is not a rewrite of a *name*. `static.py` reaches the
    # package's own attributes (`template.Library()`, `template.Node`,
    # `template.TemplateSyntaxError`) at seven sites, so rewriting the import would mean
    # rewriting the body and the transcription would stop being upstream's.
    # `engine/__init__.py` therefore binds `template` to itself.
    "templatetags/static.py": {
        "from django import template": "from django_rust_templates import engine as template",
    },
}

# Lines a transcription adds that upstream has **no counterpart for at all** — a comment
# block above a line the file otherwise takes verbatim, keyed by that line's exact
# upstream text.
#
# **This is the second row shape, separate from `TRANSFORMS` because it is the one that
# moves a line count**; `TRANSFORMS`' one-line-to-one-line invariant is what keeps every
# `COMPARED_PATH` span valid across a regeneration. The moved spans are not silent: they
# are *measured* from the committed files by `tests/test_compared_path_instrument.py`, so
# a regeneration that inserted a block fails there rather than here.
#
# Neither comment is a rewrite of upstream's line — the line underneath is either taken
# verbatim (`app_directories.py`'s import) or rewritten by a `TRANSFORMS` row
# (`static.py`'s). What is new is the *explanation*.
#
# Ordering is fixed and load-bearing: blocks are inserted **before** `TRANSFORMS` runs, so
# a block's key is always upstream's text and never the table's output.
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

# The pinned version, so a transcription says which upstream it transcribes and a bump has
# one place to change rather than one per file.
DJANGO_VERSION = "6.0.9"


def _transform(name: str, source: str) -> str:
    """``source`` with ``COMMENTS[name]`` and ``TRANSFORMS[name]`` applied.

    **The anchor line must be present exactly once, in both tables.** A row that quietly
    stopped matching — because the pin bumped and upstream reworded the line — would leave
    the transcription importing from the checkout while `--check` reported a match.
    Counting the occurrences is what makes a pin bump break the generator *loudly*.

    Comments first, then transforms: a transform's key is upstream's text, so the
    insertion has to happen while the line still reads the way the pin wrote it.
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
    # No docstring to sit in, so the marker is a comment on line 1 with a blank line
    # after it — the same two-line shape the docstring form has, which is the property
    # the span arithmetic depends on.
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
