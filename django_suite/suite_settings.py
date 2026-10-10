"""Settings for the vendored copy of Django's ``template_tests``.

This module exists so the suite can run **without the substitution shim**: the
vendored tests import ``django_rust_templates.engine`` directly (the generator
rewrote their imports), and ``TEMPLATES`` below points at this package's backend so
that the tests which reach the engine through ``django.template.loader``,
``engines[...]`` or ``TemplateResponse`` — rather than through ``Engine(...)`` —
reach *this* engine too.

**That last sentence is the whole reason this file is not the checkout's settings.**
``template_tests/test_response.py``, ``test_partials.py`` and ``test_logging.py``
reach the engine through the backend registry, and a settings module naming
``django.template.backends.django.DjangoTemplates`` — the obvious choice, and the one
the checkout uses — would have them render with stock Django's engine while every
assertion still passed. The suite would then be green about code it never ran, which
is the one failure mode a vendored suite exists to avoid.

**Every value below is copied from the checkout rather than invented**, because the
suite is a copy of the checkout's and reads the same settings. Two of them are
load-bearing and were each measured by a failing test:

* ``USE_TZ = False`` comes from ``tests/test_sqlite.py``, not from Django's global
  default of ``True``, and the ``date``/``time`` filters' ``e`` format is where it
  shows: with ``USE_TZ`` on, ``{{ d|date:"e" }}`` converts an aware value to
  ``TIME_ZONE`` and prints ``CDT``, and ``test_date06`` expects ``+0030``.
* ``TEMPLATES[0]["DIRS"]`` and ``APP_DIRS`` come from ``runtests.py``'s
  ``setup_collect_tests``. With neither, the backend cannot resolve a single
  template, and every test that goes through ``TemplateResponse`` or
  ``loader.render_to_string`` fails or errors — fifteen of them, all reporting
  ``TemplateDoesNotExist`` for a fixture that is plainly present on disk.
"""

import os
import tempfile

SECRET_KEY = "django-rust-templates-vendored-suite"

# From tests/test_sqlite.py.
USE_TZ = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# From runtests.py's ALWAYS_INSTALLED_APPS, plus the suite itself.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sites",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin.apps.SimpleAdminConfig",
    "django.contrib.staticfiles",
    "template_tests",
]

# From runtests.py's ALWAYS_MIDDLEWARE.
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

MIGRATION_MODULES = {
    "auth": None,
    "contenttypes": None,
    "sessions": None,
}

# `django_suite/empty_urls.py`. The checkout's equivalent is `tests/urls.py`, which
# is empty for the same reason: every test that needs a URLconf says which one with
# `override_settings(ROOT_URLCONF=...)`, and a default that resolved anything would
# let a `reverse()` succeed where the test expects `NoReverseMatch`.
ROOT_URLCONF = "empty_urls"

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template_tests", "templates")

TEMPLATES = [
    {
        "NAME": "django",
        "BACKEND": "django_rust_templates.backends.RustTemplates",
        "DIRS": [TEMPLATE_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

STATIC_URL = "static/"
STATIC_ROOT = os.path.join(
    os.environ.get("TMPDIR", tempfile.gettempdir()), "django-rust-templates-static"
)

SITE_ID = 1
LANGUAGE_CODE = "en"
USE_I18N = True
LOCALE_PATHS = []

SILENCED_SYSTEM_CHECKS = [
    "fields.W342",
    "postgres.E005",
]
