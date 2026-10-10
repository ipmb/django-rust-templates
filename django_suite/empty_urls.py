"""An empty URLconf, so that ``settings.ROOT_URLCONF`` names something real.

``TEMPLATES`` and the rest of the suite's configuration live in ``suite_settings.py``;
this module is here because Django refuses to start without a URLconf and the
checkout's equivalent (``tests/urls.py``) is empty for the same reason. A test that
needs routes asks for them: ``override_settings(ROOT_URLCONF="template_tests.urls")``
appears in ``test_response.py``, ``test_partials.py`` and ``syntax_tests/test_url.py``.
"""

urlpatterns = []
