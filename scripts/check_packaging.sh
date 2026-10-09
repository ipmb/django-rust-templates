#!/usr/bin/env bash
# The acceptance check for *distribution*: a real wheel, in a real venv, that a
# `pip install` can actually produce and a stranger can actually use.
#
# Why this is not a unit test
# --------------------------
# Every other gate in this repository runs against `.django/` on `PYTHONPATH`,
# with the shim installed at interpreter start by `scripts/shimpath/`. That is
# the environment the *engine* is developed in, and it is not the environment a
# user is in. Four things differ, and all four are invisible from inside the
# checkout:
#
#   1. a user gets the files `maturin` chose to put in the wheel, not the working
#      tree — a module excluded from `python-source`, or a `.so` matched by
#      `.gitignore` but missed by the build, is simply absent;
#   2. a user has a Django from *their* environment, not the pinned checkout, so
#      the version guard is load-bearing rather than hypothetical;
#   3. a user may have no Django at all, which is the one case where our own
#      error message has to arrive before Django's does;
#   4. a user installs from a *source tree* — `pip install .`, or a git URL, which
#      is the same build — and not from the wheel this script built two lines
#      ago. Case 4 covers that, and it is the shape the README advertises.
#
# The subjects here are a wheel and a venv, and the probes are run with that
# venv's interpreter rather than ours.
#
# Case 1 is the README's *instruction*, executed
# ----------------------------------------------
# It activates from an entry point and then calls `django.setup()` against a real
# settings module, because that is what the README tells a reader to write. The
# first version of this case called `settings.configure()` and then `django.setup()`
# in a bare script, which is a route no Django project actually takes — and that
# difference hid a false instruction for a whole milestone: the README said to
# activate from `settings.py`, which cannot work, and this case passed anyway,
# because it never had a settings module to be too late in. Case 1b now pins the
# ordering claim directly. A gate whose fixture is not the reader's route is a
# gate for a program nobody runs.
#
# Network
# -------
# Django, its dependencies, and maturin (needed by case 4's build) are downloaded
# once into a cache under `target/` (gitignored) and reused. A cold cache with no
# network is a **failure, not a skip**: a packaging check that quietly does nothing
# on the machine where it matters is the failure mode this repository keeps
# finding, and the fix is never to print a green line for a run that did not happen.
#
# Config isolation
# ----------------
# Every `pip` here is invoked `--isolated`, and the reason is narrower than it
# first appears.
#
# This repository *does* pin pip's configuration. `env.sh` exports
# `PIP_CONFIG_FILE=/dev/null`, with a comment naming the setting in this machine's
# global `pip.conf` that makes every pip invocation abort with an ISO 8601 parse
# error, and every script here sources `env.sh` — this one included, at the top.
# So the config-file channel is closed, deliberately, and has been.
#
# What `env.sh` cannot close is the *environment-variable* channel, and that is
# what the flag adds, since `--isolated` ignores `PIP_*` variables as well as
# config files. Measured, with `PIP_CONFIG_FILE` set exactly as `env.sh` sets it
# and `PIP_TARGET` exported: the venv received no Django and the target directory
# received it instead — a failure every case below would have reported as a defect
# in this package. With `--isolated` the venv is correct and `PIP_TARGET` is
# ignored.
#
# The subject of this script is what a *stranger's* `pip install` produces, and a
# stranger has whatever their shell exports. `PIP_INDEX_URL`, `PIP_FIND_LINKS`,
# `PIP_REQUIRE_HASHES` and `PIP_TARGET` each change or break a case.
#
# A note for whoever reads this next, because the first draft of this comment was
# wrong in two ways. It claimed the cases passed only because `--no-index` never
# consults an index, and that this project pins pip nowhere. Neither held: the
# global config is rejected while pip *parses* it, before any index decision is
# reached, and `env.sh` had pinned it all along. Both were an absence asserted
# without the one search that would have falsified it — `grep PIP_CONFIG_FILE
# scripts/` — which is the cheapest check available here and the one that was
# skipped. The flag stays, on the measurement above rather than on the story.
#
# It is on `check.sh`'s slow path, beside the generators, because it builds a
# wheel and creates five virtualenvs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$HERE/env.sh"

PY="$PROJECT_ROOT/.venv/bin/python"
CACHE="$PROJECT_ROOT/target/packaging"
WHEELS="$CACHE/wheels"
DIST="$CACHE/dist"
PROJ="$CACHE/project"

# The two Djangos this script is a statement about: the transcription pin, and
# the next minor — the version that used to install cleanly and pass everything.
PINNED_DJANGO="6.0.9"
MISMATCHED_DJANGO="6.1.2"

say() { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

rm -rf "$DIST"
mkdir -p "$WHEELS" "$DIST"

say "=== build the wheel ==="
( cd "$PROJECT_ROOT" && maturin build --release --out "$DIST" >/dev/null )
WHEEL="$(find "$DIST" -name '*.whl' -print -quit)"
[ -n "$WHEEL" ] || fail "maturin produced no wheel in $DIST"
say "wheel: $(basename "$WHEEL")"

# --- Django, its dependencies, and maturin, cached ---------------------------
# Dependencies included: the venvs below install with `--no-index`, so anything
# Django needs must already be in the cache. Fetching only the Django wheel and
# then resolving offline is what a first draft of this script did, and it fails
# with `Could not find a version that satisfies the requirement asgiref>=3.9.1` —
# a packaging check that cannot build its own fixture.
# Keyed on a marker written *after* a successful download, rather than on the
# wheel being present. The first draft keyed on the wheel, and a cache populated
# by the earlier `--no-deps` version of this script then looked warm while missing
# `asgiref` — a cache check that answers "is some of it here?".
fetch() {
  local marker="$WHEELS/.complete-$1"
  shift
  [ -f "$marker" ] && return 0
  say "fetching $* (once; cached under target/packaging/wheels)"
  "$PY" -m pip download --isolated "$@" --only-binary=:all: -d "$WHEELS" >/dev/null \
    || fail "could not fetch '$*', and the cache is cold. This check needs the
     network once, or a warm cache at $WHEELS."
  : > "$marker"
}

fetch "django-$PINNED_DJANGO" "Django==$PINNED_DJANGO"
fetch "django-$MISMATCHED_DJANGO" "Django==$MISMATCHED_DJANGO"
# Case 4 builds the source tree, and pip's build isolation would reach for the
# index to get the backend. `--no-build-isolation` with maturin already in the
# venv keeps the case offline, which is what makes it runnable in this gate.
fetch "maturin" "maturin"

# --- one venv per case -----------------------------------------------------
make_venv() {
  local dir="$CACHE/venv-$1"
  rm -rf "$dir"
  "$PY" -m venv "$dir"
  printf '%s' "$dir"
}

# Installs from the cache only. `--no-index` is what makes this offline *and*
# makes a missing dependency a failure rather than a download.
install_into() {
  local dir="$1"
  shift
  "$dir/bin/python" -m pip install --isolated --quiet --no-index \
    --find-links "$WHEELS" --find-links "$DIST" "$@"
}

# --- the reader's project, written out ---------------------------------------
# A settings module and an entry point, in the shape `django-admin startproject`
# produces and the README tells you to write. Both cases 1 and 1b install *this*,
# because the ordering claim below is about a real settings import and cannot be
# made with `settings.configure()`.
rm -rf "$PROJ"
mkdir -p "$PROJ/templates"
cat > "$PROJ/settings.py" <<'PY'
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "packaging-check"
DEBUG = False
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "engine_check",
]
DATABASES = {}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]
USE_TZ = True
PY
cat > "$PROJ/templates/hello.html" <<'HTML'
{{ name|upper }} has {{ items|length }} item{{ items|length|pluralize }}
HTML

# A real app with a management command, so the render below is reached through
# `manage.py` — the entry point under test — rather than by a second script
# repeating its two lines. `install()` is per-process, so a probe in its own
# process would be measuring its own activation, not this one's; running the
# assertion *inside* `manage.py`'s process is what makes case 1 a statement about
# the README's instruction. Listing the app also puts a real `apps.populate()` on
# the path between activation and render.
mkdir -p "$PROJ/engine_check/management/commands"
: > "$PROJ/engine_check/__init__.py"
: > "$PROJ/engine_check/management/__init__.py"
: > "$PROJ/engine_check/management/commands/__init__.py"
cat > "$PROJ/engine_check/management/commands/check_engine.py" <<'PY'
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Render a template through the engine and assert the engine served it."

    def handle(self, *args, **options):
        import django
        import django.template.base as base
        from django.template.loader import render_to_string

        import django_rust_templates

        # A template rendered the way a user renders one: by name, through the
        # DjangoTemplates backend and the loader stack, not by handing `Template`
        # a string. Loaders are engine modules too, so this exercises the
        # substitution rather than the constructor alone.
        rendered = render_to_string("hello.html", {"name": "pete", "items": [1, 2]})
        assert rendered.strip() == "PETE has 2 items", f"rendered {rendered!r}"

        # The point of the whole substitution: Django's own name resolves to the
        # file the wheel shipped, not to Django's. The render alone would pass on
        # stock Django — it is the same template either way.
        assert base.__file__.endswith("django_rust_templates/_impl/base.py"), base.__file__
        assert django_rust_templates.unshimmed() == [], django_rust_templates.unshimmed()
        self.stdout.write(
            f"  {django.get_version()} | {rendered.strip()} | "
            f"{base.__file__.split('site-packages/')[-1]}"
        )
PY

say ""
say "=== case 1: activate from an entry point, then run a real project ==="
V="$(make_venv match)"
install_into "$V" "Django==$PINNED_DJANGO" "$WHEEL" || fail "pip install failed for case 1"

# The entry point, verbatim from the README: two lines, before Django.
cat > "$PROJ/manage.py" <<'PY'
import django_rust_templates

django_rust_templates.install()

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
PY

# `check` walks the app registry, the template backends and the system-check
# framework with the substitution in place — machinery, not just a `Template()`.
# `check_engine` is the render, asserted from inside this same process.
( cd "$PROJ" && "$V/bin/python" manage.py check ) >/dev/null \
  || fail "manage.py check failed against the shimmed engine"
( cd "$PROJ" && "$V/bin/python" manage.py check_engine ) || fail "case 1 failed"

say ""
say "=== case 1b: the settings module is too late, and says so ==="
# The README tells readers *not* to activate from `settings.py`, and gives a
# reason: `django.setup()` imports `django.urls` (and so all of `django.template`)
# before the line that first reads the settings module. A warning in prose is a
# claim like any other, so it is measured here — in the same venv, against the
# same wheel, differing from case 1 in one thing: where `install()` is called.
#
# If this case ever starts *passing*, Django's `setup()` changed the order and the
# README's warning is stale. It fails; do not delete it.
sed 's/^SECRET_KEY/import django_rust_templates\n\ndjango_rust_templates.install()\n\nSECRET_KEY/' \
  "$PROJ/settings.py" > "$PROJ/settings_activates.py"
( cd "$PROJ" && "$V/bin/python" - <<'PY'
import os

os.environ["DJANGO_SETTINGS_MODULE"] = "settings_activates"
import django

from django_rust_templates import _shim, installed

try:
    django.setup()
except _shim.ShimError as exc:
    message = str(exc)
    assert "django.template" in message, f"not our ordering error: {message}"
    assert not installed(), "a refused install left a finder in sys.meta_path"
    print(f"  refused, naming {message.count('django.template')} module(s) already imported")
else:
    raise SystemExit(
        "install() from a settings module SUCCEEDED. Django's setup() no longer "
        "imports django.template before it reads settings, so the README's "
        "'activate from your entry point' warning is stale and should be rewritten."
    )
PY
) || fail "case 1b failed"

say ""
say "=== case 2: a Django the engine was not transcribed from ==="
# **The bound is enforced twice, and the two are not redundant.** This case was
# first written as a plain `pip install Django==6.1.2 <wheel>`, and it does not
# get that far: pip refuses the pairing outright, citing the declared
# `Django>=6.0,<6.1`. That is case 2b, and it is a claim worth asserting on its
# own rather than a step to route around. The runtime guard below is what covers
# a resolver that has been *bypassed* — `--no-deps`, a vendored wheel, another
# installer, a venv whose Django arrived some other way — which is exactly how
# the mismatched Django in this script's own history was produced.
V="$(make_venv mismatch)"
install_into "$V" --no-deps "$WHEEL" || fail "could not place the wheel for case 2"
install_into "$V" "Django==$MISMATCHED_DJANGO" || fail "could not place Django for case 2"
"$V/bin/python" - <<'PY' || fail "case 2 failed"
import django
from django_rust_templates import _shim, install, installed

try:
    install()
except _shim.ShimError as exc:
    message = str(exc)
    assert "6.0.9" in message, f"the pin is not named: {message}"
    assert "6.1.2" in message, f"the version found is not named: {message}"
    assert not installed(), "a refused install left a finder in sys.meta_path"
    print(f"  refused {django.get_version()}: {message.splitlines()[0][:96]}…")
else:
    raise AssertionError(f"install() accepted Django {django.get_version()}")

# The hatch, on the same interpreter, so the two outcomes differ in one thing.
install(allow_django_mismatch=True)
assert installed(), "the escape hatch did not install"
print(f"  escape hatch installed against {django.get_version()}")
PY

say ""
say "=== case 2b: pip's resolver refuses the pairing, from the declared bound ==="
# The metadata is the first line of defence and this is where it is asserted. A
# `dependencies` entry that nothing checks is a claim, and the whole point of
# putting the bound in `pyproject.toml` as well as in `_require_django` is that
# most users never reach the guard — pip stops them first, with a message about
# *dependencies* rather than a traceback about templates.
V="$(make_venv conflict)"
if out="$(install_into "$V" "Django==$MISMATCHED_DJANGO" "$WHEEL" 2>&1)"; then
  fail "pip installed Django $MISMATCHED_DJANGO beside this wheel, so the declared
     'Django>=6.0,<6.1' did not constrain the resolver"
fi
printf '%s\n' "$out" | grep -q "conflicting dependencies" || fail "pip refused, but not for the
     declared bound — the refusal has another cause and this case is not testing what
     it says: $out"
say "  pip refused the pairing, citing the declared bound"

say ""
say "=== case 3: no Django at all produces our error, not Django's ==="
V="$(make_venv nodjango)"
install_into "$V" --no-deps "$WHEEL" || fail "could not place the wheel for case 3"
"$V/bin/python" - <<'PY' || fail "case 3 failed"
from django_rust_templates import _shim

try:
    _shim.install()
except _shim.ShimError as exc:
    message = str(exc)
    assert "django-rust-templates" in message, f"our package is not named: {message}"
    print(f"  {message.splitlines()[0][:96]}…")
else:
    raise AssertionError("install() succeeded with no Django importable")
PY

say ""
say "=== case 4: the source tree, which is what 'pip install .' builds ==="
# Cases 1-3 install a wheel this script built with `maturin build --out`. A reader
# never does that: they run `pip install .` or `pip install git+https://…`, and pip
# drives the PEP 517 backend over the *source tree* instead. The two paths can
# disagree — a file the wheel build picks up through `python-source` but the sdist
# rules exclude, a stale `target/` the in-place build reuses — and the requirement
# this whole task exists to satisfy names this path, not the wheel path.
#
# `--no-build-isolation` with maturin already in the venv is what keeps it in the
# cache rather than on the index. Measured: pip builds a local directory *in
# place*, so this does not copy the checkout (peak temp usage 1 MB against a
# 3.6 GB `target/`), and it leaves no tracked file modified.
V="$(make_venv source)"
install_into "$V" "Django==$PINNED_DJANGO" maturin || fail "could not place the build deps for case 4"
"$V/bin/python" -m pip install --isolated --quiet --no-index --find-links "$WHEELS" \
  --no-build-isolation "$PROJECT_ROOT" || fail "pip install of the source tree failed"
"$V/bin/python" - <<'PY' || fail "case 4 failed"
import django_rust_templates

django_rust_templates.install()
assert django_rust_templates.installed(), "install() did not take"
assert django_rust_templates.unshimmed() == [], django_rust_templates.unshimmed()

from django.conf import settings

settings.configure(
    INSTALLED_APPS=[],
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ],
)
import django

django.setup()

from django.template import Context, Template

rendered = Template("{{ a|upper }}-{% for i in xs %}{{ i }}{% endfor %}").render(
    Context({"a": "hi", "xs": [1, 2, 3]})
)
assert rendered == "HI-123", f"rendered {rendered!r}"

import django.template.base as base

assert "site-packages" in base.__file__, base.__file__
assert base.__file__.endswith("_impl/base.py"), base.__file__
print(f"  source install | {django.get_version()} | {rendered} | {base.__file__.split('site-packages/')[-1]}")
PY

say ""
say "=== case 5: the activation an entry point cannot reach ==="
# The README states the rule — *before `django.setup()` runs, in whatever process
# starts Django* — and then names `manage.py`, `wsgi.py` and `asgi.py` as where
# that usually is. It is not the rule. It does not reach `django-admin` driven by
# `DJANGO_SETTINGS_MODULE` with no project file to edit, a `pytest-django` plugin
# that calls `django.setup()` itself, a Celery worker whose `proj/celery.py`
# triggers setup, or a library that does setup on import. For those the README
# names a `sitecustomize.py` on `PYTHONPATH`, which runs during interpreter
# start-up before any entry point exists, and this is that route measured against
# the installed wheel with *nothing* edited.
#
# The control drops one directory from `PYTHONPATH` — the one holding
# `sitecustomize.py` — and must come out as **stock** Django. A row asserting our
# engine is present proves nothing unless the same command, without the
# activation, is shown to produce Django's; that is the population, and the two
# runs differ in exactly one thing.
V="$CACHE/venv-match"   # still on disk from case 1: the same wheel, the same Django
SITE="$CACHE/activation"
rm -rf "$SITE"
mkdir -p "$SITE/site" "$SITE/app"
cat > "$SITE/site/sitecustomize.py" <<'PY'
import django_rust_templates

django_rust_templates.install()
PY
cat > "$SITE/app/settings.py" <<'PY'
SECRET_KEY = "packaging-check"
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
DATABASES = {}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {},
    }
]
USE_TZ = True
PY

out="$(cd "$SITE/app" && PYTHONPATH="$SITE/app" DJANGO_SETTINGS_MODULE=settings \
  "$V/bin/python" -c 'import django; django.setup(); import django.template.base as b; print("engine" if "_impl" in b.__file__ else "stock")' 2>&1)" \
  || fail "the control run failed: $out"
[ "$out" = "stock" ] || fail "the control is not a control: without sitecustomize.py on
     PYTHONPATH this still resolved our engine ('$out'), so the subject below proves nothing"
say "  control (no sitecustomize): $out"

# `django-admin` from the venv's own bin, with no project file in sight.
( cd "$SITE/app" && PYTHONPATH="$SITE/site:$SITE/app" DJANGO_SETTINGS_MODULE=settings \
  "$V/bin/django-admin" check ) >/dev/null || fail "django-admin check failed under sitecustomize"

# The subject: same interpreter, same settings module, one directory added to
# PYTHONPATH. It inherits both, so the activation it is testing is the one under
# test rather than one this heredoc performs for itself.
( cd "$SITE/app" && PYTHONPATH="$SITE/site:$SITE/app" DJANGO_SETTINGS_MODULE=settings \
  "$V/bin/python" - <<'PY'
import django

django.setup()

import django.template.base as base

import django_rust_templates

assert "django_rust_templates/_impl" in base.__file__, base.__file__
assert django_rust_templates.unshimmed() == [], django_rust_templates.unshimmed()
print(f"  django-admin, nothing edited | {django.get_version()} | "
      f"{base.__file__.split('site-packages/')[-1]}")
PY
) || fail "case 5 failed"

say ""
say "packaging: a wheel installs, is activated from an entry point the way the"
say "           README says, refuses a settings-module activation and a Django it"
say "           was not transcribed from, keeps pip from pairing it with one, names"
say "           itself when Django is absent, builds from the source tree, and can"
say "           be activated where no entry point exists to edit"
