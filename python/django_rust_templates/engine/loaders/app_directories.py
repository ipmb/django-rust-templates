"""
# TRANSCRIBED: django/template/loaders/app_directories.py (Django 6.0.9)

Wrapper for loading templates from "templates" directories in INSTALLED_APPS
packages.
"""

# **Django's `get_app_template_dirs`, not a copy of it**, and this line is why
# this package has no `engine/utils.py`. The function caches its answer — it
# walks every installed app and stats a directory in each — and the cache is
# invalidated by ``django.test.signals.update_installed_apps``, which clears
# ``django.template.utils.get_app_template_dirs``. A second function object holds
# a second cache that receiver cannot reach: measured, ours returned
# ``[django_suite/template_tests/templates]`` under ``override_settings(
# INSTALLED_APPS=[])`` while Django's returned ``[]``, which is the whole of
# ``test_loaders.AppDirectoriesLoaderTests.test_not_installed``.
from django.template.utils import get_app_template_dirs

from .filesystem import Loader as FilesystemLoader


class Loader(FilesystemLoader):
    def get_dirs(self):
        return get_app_template_dirs("templates")
