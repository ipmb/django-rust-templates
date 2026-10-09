"""Smoke tests for the compiled Rust extension.

These exist to catch the failure mode where a stale `_native` shared object is
left in the tree and the Python shell silently talks to an old build. The version
read back must be the version the workspace declares.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

from django_rust_templates import _native  # noqa: E402


class NativeExtensionTests(unittest.TestCase):
    def test_extension_is_importable(self):
        self.assertTrue(hasattr(_native, "engine_version"))

    def test_engine_version_matches_workspace_version(self):
        cargo_toml = (PROJECT_ROOT / "Cargo.toml").read_text()
        match = re.search(r'^version = "([^"]+)"', cargo_toml, re.MULTILINE)
        self.assertIsNotNone(match, "workspace version not found in Cargo.toml")
        self.assertEqual(_native.engine_version(), match.group(1))

    def test_extension_is_a_compiled_binary(self):
        origin = Path(_native.__file__)
        suffix = origin.suffixes
        self.assertIn(
            ".so",
            suffix,
            f"expected a compiled extension, got {origin}",
        )


if __name__ == "__main__":
    unittest.main()
