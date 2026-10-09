"""Project test suite.

The acceptance criterion lives elsewhere — Django's own ``tests/template_tests``
run against this engine. These tests cover the project's own machinery: the
substitution layer, the compiled extension, and eventually the engine's Rust-side
units.

**The suite's settings are configured once, here.** Ten test classes need
settings (``Engine()`` reads them, and ``localize`` reads ``USE_I18N``). Five
reached ``scripts.filter_probe.ensure_settings()`` already; the other five,
across four modules, configured settings themselves and guarded the call —
``settings.configure`` is a no-op once settings exist, so the configuration that
won was the one belonging to whichever module ``unittest`` walked first. That is
an alphabet deciding a value: measured once already
(``2026-10-09-m9-interpose-base-and-smartif.md``, Task 6's correction (6)), when
four ``test_filter_parity`` rows went red because ``test_base_parity`` sorted
before ``filter_probe``. Configuring here removes the race rather than making it
rarer, and ``test_settings_configuration.py`` fails if a local
``settings.configure`` comes back.
"""

from scripts.filter_probe import ensure_settings

ensure_settings()
