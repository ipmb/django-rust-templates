#!/usr/bin/env bash
# The acceptance check for *distribution*: a real wheel, in a real venv, that a
# `pip install` can actually produce and a stranger can actually use.
#
# Why this is not a unit test
# --------------------------
# Every other gate in this repository runs against the working tree, with the
# pinned checkout in `.django/` and the extension built in place by
# `maturin develop`. That is the environment the *engine* is developed in, and it
# is not the environment a user is in. Four things differ, and all four are
# invisible from inside the checkout:
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
# Case 1 is the README's instruction, executed
# -------------------------------------------
# The README tells a reader to write one thing: a `TEMPLATES` entry naming
# `django_rust_templates.rust.RustTemplates`. Case 1 writes exactly that entry
# into a real settings module, runs a real `manage.py`, and renders through the
# loader stack by name. The first version of this case called `settings.configure()`
# and then `django.setup()` in a bare script, which is a route no Django project
# actually takes — and that difference hid a false instruction for a whole
# milestone, because it never had a settings module to be too late in. A gate whose
# fixture is not the reader's route is a gate for a program nobody runs.
#
# **Case 1b is the control, and it is why case 1 is worth anything.** A render of
# `{{ name|upper }}` proves nothing about which engine rendered it — stock Django
# produces the same string, and did for the whole period when the README's
# instruction was wrong. So the same project files are run a second time with one
# line changed: the `BACKEND` string. The render must be byte-identical and the
# engine object behind it must be Django's. Both runs print the class of the object
# they reached, read off the object rather than asserted about the environment, and
# the shell compares the two.
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
# produces. The `TEMPLATES` entry is the README's, plus one line: naming the
# backend is the *whole* of what this package asks a project to do, so a check
# that reached the engine any other way would be checking a program nobody writes.
# The extra line is `"NAME"`, and it is here for the control rather than for the
# reader — `sed` derives `settings_stock.py` from this file by swapping one dotted
# path for another, so a lookup by *derived* alias would have to be spelled two
# different ways in one command. Case 4, which has no control, omits `NAME`, and
# that is where the derivation is exercised.
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
        "NAME": "rust",
        "BACKEND": "django_rust_templates.rust.RustTemplates",
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

# The control's settings module: the same file, one line different. `sed` rather
# than a second heredoc, so that "differs in exactly one thing" is a property of
# the fixture construction and not a promise in a comment.
sed 's|django_rust_templates\.rust\.RustTemplates|django.template.backends.django.DjangoTemplates|' \
  "$PROJ/settings.py" > "$PROJ/settings_stock.py"

cat > "$PROJ/manage.py" <<'PY'
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
PY

# A real app with a management command, so the render below is reached through
# `manage.py` — the entry point a project has — rather than by a second script
# repeating its two lines. Listing the app also puts a real `apps.populate()` on
# the path between settings and render.
mkdir -p "$PROJ/engine_check/management/commands"
: > "$PROJ/engine_check/__init__.py"
: > "$PROJ/engine_check/management/__init__.py"
: > "$PROJ/engine_check/management/commands/__init__.py"

# **It reports the engine rather than asserting it**, and that split is deliberate:
# which engine *should* have run is a fact about the two runs compared against each
# other, so the comparison lives in the shell where both runs meet. What the command
# asserts is the part that is the same either way — that the template renders, by
# name, through the loader stack.
cat > "$PROJ/engine_check/management/commands/check_engine.py" <<'PY'
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Render by name and report which backend and engine actually served it."

    def handle(self, *args, **options):
        import django
        import django.template.base as django_base
        from django.conf import settings
        from django.template import engines
        from django.template.loader import render_to_string

        # Read out of the setting rather than spelled, so the control's `sed`-derived
        # settings module needs no second copy of this command. The `NAME` it reads
        # is the fixture's, and the fixture's reason is here: with no `NAME`,
        # `EngineHandler` derives the alias from the second-to-last dotted segment of
        # `BACKEND` — `django.template.backends.jinja2.Jinja2` becomes `jinja2`, which
        # is why templates normally go unnamed, and `django_rust_templates.rust.
        # RustTemplates` becomes `rust`. The control's `sed` swaps that path for
        # `django.template.backends.django.DjangoTemplates`, whose segment is
        # `django`, so one lookup by derived alias could not find both. Case 4 has no
        # control and no `NAME`, and is where the derivation is proven.
        backend = engines[settings.TEMPLATES[0]["NAME"]]

        rendered = render_to_string("hello.html", {"name": "pete", "items": [1, 2]})
        assert rendered.strip() == "PETE has 2 items", f"rendered {rendered!r}"

        # **Django's own name still means Django's own file.** This is the claim
        # the M18 rewrite is about — the package has its own namespace and replaces
        # nothing — and it is the one thing a render cannot show, because the
        # render is identical either way. Read off the module object, so a package
        # that had taken the name over would fail here and nowhere else.
        assert django_base.__file__.endswith("django/template/base.py"), (
            django_base.__file__
        )

        self.stdout.write(
            f"  {django.get_version()}"
            f"|{type(backend).__module__}"
            f"|{type(backend.engine).__module__}"
            f"|{rendered.strip()}"
        )
PY

# `manage.py check` walks the app registry, the template backends and the
# system-check framework — machinery, not just a `Template()`.
run_project() {
  local venv="$1" settings="$2"
  ( cd "$PROJ" && DJANGO_SETTINGS_MODULE="$settings" "$venv/bin/python" manage.py check ) \
    >/dev/null || fail "manage.py check failed under $settings"
  ( cd "$PROJ" && DJANGO_SETTINGS_MODULE="$settings" "$venv/bin/python" manage.py check_engine )
}

say ""
say "=== case 1: name the backend in settings, then run the project ==="
V="$(make_venv match)"
install_into "$V" "Django==$PINNED_DJANGO" "$WHEEL" || fail "pip install failed for case 1"

ours="$(run_project "$V" settings)" || fail "case 1 failed"
say "$ours"
case "$ours" in
  *"|django_rust_templates.rust|django_rust_templates.engine.engine|"*)
    ;;
  *)
    fail "case 1 did not reach this package's backend and engine: $ours"
    ;;
esac

say ""
say "=== case 1b: the control — the same project, stock Django's backend ==="
# One line of the settings module changed, nothing else: same interpreter, same
# wheel, same template, same command. If this run reaches our engine, the check's
# subject is not what it says; if its render differs, case 1's render was not the
# one a project gets.
theirs="$(run_project "$V" settings_stock)" || fail "case 1b failed"
say "$theirs"
case "$theirs" in
  *"|django.template.backends.django|django.template.engine|"*)
    ;;
  *)
    fail "the control did not run stock Django, so case 1 proves nothing: $theirs"
    ;;
esac
[ "${ours##*|}" = "${theirs##*|}" ] || fail "the two backends rendered differently:
     ours=$ours
     stock=$theirs"

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
#
# **It is driven through `TEMPLATES`, not through a function call**, because that
# is the only door in: `_require_django` runs from `RustTemplates.__init__`, which
# `EngineHandler` reaches exactly once per project. A probe calling the function
# directly would be testing a name rather than the route.
V="$(make_venv mismatch)"
install_into "$V" --no-deps "$WHEEL" || fail "could not place the wheel for case 2"
install_into "$V" "Django==$MISMATCHED_DJANGO" || fail "could not place Django for case 2"
"$V/bin/python" - <<'PY' || fail "case 2 failed"
import django
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template import engines

settings.configure(
    INSTALLED_APPS=[],
    TEMPLATES=[
        {
            "NAME": "rust",
            "BACKEND": "django_rust_templates.rust.RustTemplates",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ],
)
django.setup()

try:
    engines["rust"]
except ImproperlyConfigured as exc:
    message = str(exc)
    assert "6.0.9" in message, f"the pin is not named: {message}"
    assert django.get_version() in message, f"the version found is not named: {message}"
    assert "allow_django_mismatch" in message, f"the hatch is not named: {message}"
    print(f"  refused {django.get_version()}: {message.splitlines()[0][:88]}…")
else:
    raise AssertionError(f"the backend accepted Django {django.get_version()}")

# The hatch, in the same process, so the two outcomes differ in one thing. The
# mutation is visible to `EngineHandler` because its `templates` property merges
# `**tpl` into a new dict and so carries a *reference* to this `OPTIONS` object
# rather than a copy of it.
settings.TEMPLATES[0]["OPTIONS"]["allow_django_mismatch"] = True
backend = engines["rust"]
assert type(backend.engine).__module__ == "django_rust_templates.engine.engine", (
    type(backend.engine)
)
print(f"  allow_django_mismatch built the backend against {django.get_version()}")
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
say "=== case 3: with no Django at all, the package still answers for itself ==="
# **This case says less than it used to, and the difference is the M18 rewrite.**
# It used to assert that `install()` in a Django-less interpreter named our package
# before naming Django. There is no `install()` any more, and no route to the guard
# that does not go through Django: `TEMPLATES` is a Django setting, so a project
# with no Django has no templates to configure. What is left is the honest half —
# the *package* imports, and reports its own engine version, with no Django present
# at all; the *backend* is Django's adapter and cannot be imported without Django,
# which is true of every backend Django ships.
V="$(make_venv nodjango)"
install_into "$V" --no-deps "$WHEEL" || fail "could not place the wheel for case 3"
"$V/bin/python" - <<'PY' || fail "case 3 failed"
import django_rust_templates

assert django_rust_templates.engine_version(), "the extension did not load"
assert django_rust_templates.TRANSCRIBED_DJANGO == (6, 0, 9)

try:
    import django
except ImportError:
    pass
else:
    raise AssertionError("Django is importable in the no-Django venv")

print(f"  no Django | engine {django_rust_templates.engine_version()} | "
      f"transcribed from {'.'.join(map(str, django_rust_templates.TRANSCRIBED_DJANGO))}")
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
import django
from django.conf import settings

settings.configure(
    INSTALLED_APPS=[],
    TEMPLATES=[
        {
            # **No `NAME`**, unlike case 1 and for the opposite reason: this is the
            # one case with no `sed`-derived control to share a command with, so it
            # is where the README's entry can be taken literally. The lookup below
            # names `"rust"`, which nothing in this file spells — it is what
            # `EngineHandler` derives from the second-to-last dotted segment of the
            # path, and the assertion is that the derivation is right.
            "BACKEND": "django_rust_templates.rust.RustTemplates",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ],
)
django.setup()

from django.template import engines

backend = engines["rust"]
assert type(backend).__module__ == "django_rust_templates.rust", type(backend)

rendered = backend.from_string(
    "{{ a|upper }}-{% for i in xs %}{{ i }}{% endfor %}"
).render({"a": "hi", "xs": [1, 2, 3]})
assert rendered == "HI-123", f"rendered {rendered!r}"
print(f"  source install | {django.get_version()} | {rendered}")
PY

say ""
say "=== there is no case 5, and its absence is the M18 rewrite ==="
# The old case 5 existed because `install()` had to run before `django.setup()`,
# and three routes — `django-admin` driven by `DJANGO_SETTINGS_MODULE`, a
# `pytest-django` plugin, a Celery worker — had no entry point to edit, so the
# README sent readers to a `sitecustomize.py` on `PYTHONPATH`. Naming the backend
# in `TEMPLATES` is reached by all three routes already, because all three build
# the backend the ordinary way, and there is nothing left for a fifth case to
# measure. The control that case carried — "without the activation, you get stock
# Django" — is case 1b's, on the population that still has two outcomes.

say ""
# The legend, at the point of output, for the same reason the bench table states
# which statistic its ratio column is: a reader counting banners finds a mismatch
# before finding the explanation 200 lines up at `:489`. There are **six** banners
# and the numbering stops at **four**; both counts are right, and the difference is
# the two lettered controls.
say "packaging: six banners — cases 1, 1b, 2, 2b, 3 and 4. The numbering stops at"
say "           four because case 5 was removed by the M18 rewrite; 'six' counts the"
say "           two lettered controls. In them: a wheel installs, a real project"
say "           activates it by naming the backend"
say "           in TEMPLATES and renders through the loader stack, the same project"
say "           against Django's own backend produces the same render, the backend"
say "           refuses a Django it was not transcribed from and opens under"
say "           allow_django_mismatch, pip refuses the pairing first, the package"
say "           stands alone with no Django, and the source tree builds"
