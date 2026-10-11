"""What the unit suite's discovery pattern can drop without saying so.

``scripts/check.sh`` discovers with ``-p 'test_*.py'``; a module whose name does
not match contributes no rows and nothing says so. These rows walk the tree and
name the offending file rather than pinning a count, which costs a manual bump
on every added row. They catch a class that does not inherit ``TestCase`` and a
subdirectory without an ``__init__.py``; a module that *is* collected but whose
rows all skip at runtime is caught by nobody here.
"""

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DiscoveryHoldsNoHiddenRows(unittest.TestCase):
    """The rows a count of the suite cannot see.

    A count names a number, not the file whose rows nobody runs; these two rows
    ask the same question directly.
    """

    def test_no_module_the_pattern_misses_holds_test_rows(self):
        """Walk the directory the loader walks; fail on a file the pattern misses
        that defines a ``TestCase``, naming it."""
        import ast

        offenders = []
        for path in sorted((PROJECT_ROOT / "tests").glob("*.py")):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Attribute) and base.attr == "TestCase"
                    for base in node.bases
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
        self.assertEqual(
            offenders,
            [],
            "these modules define test cases but do not match `-p 'test_*.py'`, "
            f"so nothing collects them: {offenders}",
        )

    def test_every_test_directory_is_a_package(self):
        """``discover`` recurses only into importable packages, and says nothing;
        a test file under a directory with no ``__init__.py`` is collected by
        nothing."""
        import ast

        offenders = []
        for subdir in sorted(p for p in (PROJECT_ROOT / "tests").iterdir() if p.is_dir()):
            if subdir.name == "__pycache__":
                continue
            holds_rows = any(
                path.name.startswith("test_")
                or "TestCase" in path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(subdir.glob("*.py"))
            )
            if holds_rows and not (subdir / "__init__.py").is_file():
                offenders.append(subdir.name)
        self.assertEqual(
            offenders,
            [],
            "these directories hold test code but are not packages, so "
            f"`discover` cannot recurse into them: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
