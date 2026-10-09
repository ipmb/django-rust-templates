# django-rust-templates

Django's template backend, reimplemented in Rust. A drop-in replacement for
`django.template`.

**Experimental. All code in this repository was written by AI.**

- Django's own `tests/template_tests/` passes unmodified: `Ran 1547 tests … OK
  (skipped=6)`. The six skips are Django's, not ours.
- Currently **1.35× to 1.85× slower** than stock Django 6.0.9. Making it faster
  is the work in progress.
- No wheel is published, so installing builds the Rust extension from source.
  You need a Rust toolchain — [rustup](https://rustup.rs).
- Requires Python 3.12 or later — only 3.14.4 has actually been run — and Django
  6.0.x; `install()` refuses any Django outside 6.0.x.

## Install

```console
pip install "git+https://github.com/ipmb/django-rust-templates"
uv add "git+https://github.com/ipmb/django-rust-templates"
```

From a clone, `pip install .` works too.

## Use

Installing changes nothing until `install()` is called, and that call has to
come before `django.template` is imported: the substitution serves modules by
name, so a module that already took a binding from Django keeps it.

**The rule is: before `django.setup()` runs, in whatever process starts Django.**
In a project from `django-admin startproject`, that is usually the top of
`manage.py`, `wsgi.py` and `asgi.py`:

```python
import django_rust_templates

django_rust_templates.install()
```

Not in `settings.py`, and not in `AppConfig.ready()`. `django.setup()` imports
`django.urls` — and so all of `django.template` — before it reads the settings
module, so both run too late. Where there is no entry point to edit — a
`pytest-django` plugin, a Celery worker, `django-admin` with
`DJANGO_SETTINGS_MODULE` — put those two lines in a `sitecustomize.py` on your
`PYTHONPATH`.

Calling `install()` too late raises, naming the modules that already took a
binding from Django.

## Development

`scripts/check.sh` is the gate; `--fast` is the loop. Both need the pinned Django
checkout, which `scripts/fetch_django.sh` fetches into the gitignored `.django/`.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
