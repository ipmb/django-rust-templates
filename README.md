# django-rust-templates

Django's template engine, reimplemented in Rust as a drop-in replacement for
`django.template`.

The Rust crate (`template-core`) implements the lexer, parser, filter
expressions, the resolver, tags, inheritance and loaders. A thin Python shell
substitutes itself for `django.template` through a meta-path finder, so Django's
own internals — and your project's `{% load %}`ed libraries — import our modules
without knowing anything changed. Everything outside `django.template` is real
Django.

## Status

Compatibility first, and the compatibility target is met: Django's own
`tests/template_tests/` suite runs unmodified against this engine and reports
`Ran 1547 tests … OK (skipped=6)`, with the six skips structural (five
jinja2-backend tests, one POSIX-wide upstream guard) rather than tests we
skipped.

Speed is measured and is **not yet a win**. On four workloads, one binary run
under both engines, the port is **1.35×–1.85× slower** than stock Django 6.0.9.
Both sides print a hash of every rendered page and the hashes agree, so that is
a like-for-like comparison and not two different templates. Making it faster is
the next piece of work; the number is here because the tree measures it, and a
benchmark that publishes only the flattering direction has stopped being one.

## Requirements

- **Django 6.0.x.** The engine is a *transcription* of Django 6.0.9: it
  reproduces that version's behaviour, and that is the only version any of this
  project's checks have ever been run against. The dependency is declared as
  `Django>=6.0,<6.1` and enforced at `install()` time — see *The version guard*
  below.
- **Python 3.12 or later.** Only 3.14.4 has actually been run; 3.12 and 3.13 are
  permitted by the metadata and untested, which is a statement about this
  project's evidence rather than about your interpreter.
- **A Rust toolchain**, because no wheel is published yet: `pip install` builds
  the extension from source. Install Rust with [rustup](https://rustup.rs).

## Install

```console
$ pip install .                                              # from a clone
$ pip install git+https://github.com/ipmb/django-rust-templates   # from git
```

## Turn it on

Installing it changes nothing on its own. The substitution happens when
`install()` is called, and it must be called **before `django.template` is
imported at all** — the finder serves modules by name, so a module that already
took a binding from Django keeps the one it took. There is no way to undo that
from inside the interpreter that did it.

That rules out the two obvious places, and not because they are slightly late.
`django.setup()` imports `django.urls` — which imports `django.template` —
*before* it reads your settings module or populates the app registry, so every
import it performs is already finished by the time either one runs:

```python
# django/__init__.py, Django 6.0.9
def setup(set_prefix=True):
    from django.apps import apps
    from django.conf import settings
    from django.urls import set_script_prefix  # <- django.template arrives on this line
    from django.utils.log import configure_logging

    configure_logging(settings.LOGGING_CONFIG, settings.LOGGING)  # <- first read of settings
```

A settings module therefore runs strictly after the imports it would need to
precede, and `AppConfig.ready()` runs later still.

**The rule is: before `django.setup()` runs, in whatever process starts Django.**
In a project from `django-admin startproject`, that is usually the top of
`manage.py`, `wsgi.py` and `asgi.py`:

```python
# manage.py
import django_rust_templates

django_rust_templates.install()

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
```

Those three files are where the rule usually lands, not the rule itself. It does
not reach `django-admin` driven by `DJANGO_SETTINGS_MODULE` with no project file
to edit, a `pytest-django` plugin that calls `django.setup()` on your behalf, a
Celery worker whose `proj/celery.py` triggers setup, or a library that does setup
on import. For those, put a `sitecustomize.py` on your `PYTHONPATH`: it is
imported during interpreter start-up, before any entry point exists, so it covers
every case — including ones that do not exist yet. It is what this repository's
own harness uses rather than editing a `manage.py` that does not exist.

```python
# sitecustomize.py, anywhere on your PYTHONPATH
import django_rust_templates

django_rust_templates.install()
```

Get the order wrong and you are told, rather than quietly getting stock
behaviour: `install()` raises `ShimError` naming each module that took a binding
from Django first. Both routes are run against an installed wheel by
`scripts/check_packaging.sh` — case 1 through a real `manage.py`, case 5 through
`django-admin` with nothing edited — so the instructions on this page are the
ones the gate executes.

Diagnostics, if you want to see what is going on:

```python
import django_rust_templates

django_rust_templates.installed()      # is the substitution in place?
django_rust_templates.unshimmed()       # engine modules still served by Django — [] at M14
django_rust_templates.engine_version()  # version of the compiled Rust extension
```

> **There is no environment variable in the installed package.** The
> `DJANGO_RUST_TEMPLATES=1` variable you may see in this repository belongs to
> its own test harness (`scripts/shimpath/sitecustomize.py`), which is not
> shipped and needs `scripts/shimpath` on `PYTHONPATH`. The `sitecustomize.py`
> above is *yours*, not ours — it is three lines you write. From a wheel,
> importing this package and calling `install()` is the only way in.

## The version guard

`install()` refuses a Django it was not transcribed from:

```
ShimError: this engine is a transcription of Django 6.0.9, but Django 6.1.2 is
installed. The Rust engine reimplements Django's template machinery by
reproducing one version's behaviour, and it is proven against that version
only …
```

Two measured outcomes are what this is for. Against **Django 6.1.2** the engine
installed cleanly, reported an empty `unshimmed()`, and rendered — every check
this project has would have called that green, on a transcription taken from a
different version. Against **Django 5.2.7** it died with
`ImportError: cannot import name 'django_file_prefixes' from
'django.utils.deprecation'`, an error that blames Django for a problem in this
package.

The same check runs before anything is modified, so a caller that catches the
refusal is left exactly where it started. If you are deliberately testing this
engine against a Django it was not taken from:

```python
django_rust_templates.install(allow_django_mismatch=True)
```

## Development

`scripts/check.sh` is the gate; `scripts/check.sh --fast` is the loop. Both need
a Django checkout, which `scripts/fetch_django.sh` fetches at the pinned tag
into the gitignored `.django/`.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
