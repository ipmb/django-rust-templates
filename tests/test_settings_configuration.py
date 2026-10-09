"""The suite configures Django's settings once, in ``tests/__init__.py``.

``settings.configure`` is a no-op once settings exist, so *when* it is called
decides *what* it says. Ten test classes need settings — ``Engine()`` reads them,
and ``localize`` reads ``USE_I18N``. Five reached the suite's helper already; the
other five, across four modules, configured settings locally and guarded the
call. The winner was therefore whichever module ``unittest discover`` walked
first, and modules are walked alphabetically.

That is not hypothetical here. Measured on 2026-10-09 (recorded in
``docs/superpowers/plans/2026-10-09-m9-interpose-base-and-smartif.md``, Task 6's
correction (6)): four rows of ``test_filter_parity`` went red, rendering
``'maybe'`` against a pinned ``'Vielleicht'``, because ``test_base_parity`` sorts
before ``filter_probe`` and configured ``USE_I18N=False`` first. The value was
not the cause; the ordering was.

So the configuration moved to the package's ``__init__``, which is imported
before any test module, and this test is what keeps it there: a local
``settings.configure`` is a line whose effect depends on a filename, and the
only way to notice one is to refuse it.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
SCRIPTS = ROOT / "scripts"

#: The one file allowed to call it, and the one place the call is made.
CONFIGURER = SCRIPTS / "filter_probe.py"

# Matches the call, not the mentions of the call in prose. `#` comments and
# docstrings in this tree quote it often — the guards this test exists to
# prevent were each explained in a comment — so a bare substring search would
# report every explanation as a violation.
CALL = re.compile(r"^\s*(?:\w+\.)*settings\.configure\s*\(", re.MULTILINE)


def callers() -> list[str]:
    """Every file under ``tests/`` and ``scripts/`` that calls it, by path."""
    found = []
    for directory in (TESTS, SCRIPTS):
        for path in sorted(directory.rglob("*.py")):
            if path == CONFIGURER:
                continue
            if CALL.search(path.read_text(encoding="utf-8")):
                found.append(str(path.relative_to(ROOT)))
    return found


class SettingsConfigurationTests(unittest.TestCase):
    def test_only_the_probe_configures_settings(self):
        self.assertEqual(
            callers(),
            [],
            "settings are configured once, in `tests/__init__.py`, via "
            "`scripts.filter_probe.ensure_settings()`; a local "
            "`settings.configure` makes the suite's settings depend on which "
            "module `unittest` walks first",
        )

    def test_the_package_configures_them(self):
        """The single configuration is the package's, so it precedes every test."""
        source = (TESTS / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "ensure_settings()",
            source,
            "`tests/__init__.py` must call `scripts.filter_probe.ensure_settings()` "
            "at import; it is imported before any test module, which is what "
            "makes the configuration order-independent",
        )

    def test_the_probe_still_makes_the_call_this_test_permits(self):
        """The exemption above is not a hole: the one permitted call exists."""
        self.assertTrue(
            CALL.search(CONFIGURER.read_text(encoding="utf-8")),
            "`scripts/filter_probe.py` no longer calls `settings.configure`; if "
            "the configuration moved, move this test's exemption with it",
        )


if __name__ == "__main__":
    unittest.main()
