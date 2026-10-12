"""Exercise the installed distribution, including native include unwinding."""

import django
import os

os.environ["DJANGO_SETTINGS_MODULE"] = "django.conf.global_settings"
django.setup()

from django_rust_templates import _native  # noqa: E402
from django_rust_templates.engine import Context  # noqa: E402
from django_rust_templates.engine import TemplateDoesNotExist  # noqa: E402
from django_rust_templates.rust import RustTemplates  # noqa: E402

backend = RustTemplates(
    {
        "NAME": "rust",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                (
                    "django_rust_templates.engine.loaders.locmem.Loader",
                    {
                        "child.html": "{{ name|upper }}",
                        "broken.html": '{% include "missing.html" %}',
                    },
                )
            ]
        },
    }
)
template = backend.from_string(
    '{% for name in names %}{% include "child.html" %}{% endfor %}'
)
assert template.render({"names": ["Ada", "Grace"]}) == "ADAGRACE"
context = Context()
depth = len(context.dicts)
assert backend.engine.from_string('{% include "child.html" %}').render(context) == ""
assert len(context.dicts) == depth
try:
    backend.engine.from_string('{% include "broken.html" %}').render(context)
except TemplateDoesNotExist:
    pass
else:
    raise AssertionError("A missing included template should raise")
assert len(context.dicts) == depth
print(f"Installed native extension: {_native.__file__}")
