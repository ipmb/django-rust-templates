"""Django's template engine, reimplemented in Rust, as an ordinary backend.

**There is exactly one way to use this package, and it is a setting.** A project
names the backend in its template configuration::

    TEMPLATES = [
        {
            "BACKEND": "django_rust_templates.rust.RustTemplates",
            "DIRS": [BASE_DIR / "templates"],
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": [...]},
        }
    ]

and nothing else. There is no import to perform, no finder to install, no
``AppConfig.ready()`` hook and no environment variable: ``EngineHandler``
resolves the string above with ``import_string``, which is the same route every
other Django template backend takes. Importing this package by hand does
nothing at all, which is why this module is four functions wide.

**This package has its own namespace and does not impersonate Django's.** It
used to substitute itself for ``django.template`` on ``sys.meta_path``, and that
machinery is gone: the engine lives at ``django_rust_templates.engine``, the
backend at ``django_rust_templates.rust``, and no module of Django's is
replaced, patched or shadowed at any point. The consequence worth stating is
that a name reached across the boundary — ``engines``, ``TemplateDoesNotExist``,
``django.template.loader`` — is *Django's* object and behaves exactly as it does
for any other backend, because there is no second copy anywhere for it to be
confused with.
"""

from __future__ import annotations

from . import _native

__all__ = ("engine_version",)

# The Django this engine is a *transcription* of — the version §4 pins and
# ``django.lock`` records the SHA of, and the only one every gate in this project
# has ever been run against.
#
# It is a tuple beside the code rather than a lookup into ``django.lock`` because
# the two answer different questions: the lock pins the *checkout the suite runs*,
# and this names the *behaviour the Rust engine reproduces*. An installed wheel has
# no ``.django/`` and no lock file to read, so the claim has to travel with the
# artifact or it does not exist. :func:`_require_django` is the reader;
# ``tests/test_packaging_guard.py`` is the guard that the two cannot drift, and
# ``scripts/check_packaging.sh`` checks it against a real venv.
TRANSCRIBED_DJANGO = (6, 0, 9)


def engine_version() -> str:
    """Version of the compiled Rust engine behind this package."""
    return _native.engine_version()


def _require_django(allow_mismatch: bool = False) -> None:
    """Refuse a Django this engine was not transcribed from.

    Called from ``RustTemplates.__init__``, which is the one door into this
    package, so every project that names the backend passes through here exactly
    once — before its first template is loaded.

    **Why a version check earns its keep, measured rather than argued.** The
    engine reproduces one Django's template machinery, and every gate this
    project owns — the acceptance suite, the differential harness, the meter —
    runs against that one checkout, so none of them can speak about any other
    Django. Before this function existed, two outcomes fell out of that gap and
    both were measured on wheels installed into a clean venv:

    * **Django 6.1.2** — the engine loaded, rendered a template, and said nothing
      about the transcription and the running Django being different versions.
      This is the direction that matters, because it is silent: it is the shape
      this project keeps finding, a green result that means nothing.
    * **Django 5.2.7** — import died with ``ImportError: cannot import name
      'django_file_prefixes' from 'django.utils.deprecation'``, an error that
      names Django for a problem that is ours.

    **It raises ``ImproperlyConfigured``**, because that is the exception a
    project already catches around a bad ``TEMPLATES`` entry, and the thing that
    is wrong *is* the configuration: this backend and the installed Django do not
    belong together. Every other misconfiguration in this file's neighbourhood —
    an unknown ``OPTIONS`` key, ``APP_DIRS`` beside an explicit ``loaders`` —
    arrives the same way, so a project needs no second handler.

    **A missing Django is the third case and gets our own message**, because
    ``pip install django-rust-templates`` with no Django beside it must not read
    as a broken Django. It is exercised in ``scripts/check_packaging.sh``, where
    the population is a real venv.

    **The bound is major.minor, not the patch.** Any 6.0.x passes, which matches
    the declared dependency ``Django>=6.0,<6.1``; treating a patch release as a
    new engine would make the guard fire on routine security updates and train
    the reader to reach for ``allow_mismatch``. The message still names 6.0.9,
    because that is the version the behaviour was actually pinned against and
    the one worth reporting.
    """
    from django.core.exceptions import ImproperlyConfigured

    try:
        import django
    except ImportError as exc:  # pragma: no cover - covered end-to-end, see above
        raise ImproperlyConfigured(
            "django-rust-templates is a template backend for Django and needs "
            "Django, which is not importable in this interpreter. Install it "
            "(`pip install 'Django>=6.0,<6.1'`) before naming this backend in "
            "TEMPLATES."
        ) from exc

    if allow_mismatch:
        return

    if django.VERSION[:2] == TRANSCRIBED_DJANGO[:2]:
        return

    pin = ".".join(str(part) for part in TRANSCRIBED_DJANGO)
    raise ImproperlyConfigured(
        f"this engine is a transcription of Django {pin}, but Django "
        f"{django.get_version()} is installed. The Rust engine reimplements "
        f"Django's template machinery by reproducing one version's behaviour, "
        f"and it is proven against that version only — run it against another "
        f"and every check this project has would still report success. Install "
        f"Django {TRANSCRIBED_DJANGO[0]}.{TRANSCRIBED_DJANGO[1]}.x, or set "
        f"``allow_django_mismatch`` in this backend's OPTIONS if you mean to "
        f"test a transcription against a Django it was not taken from."
    )
