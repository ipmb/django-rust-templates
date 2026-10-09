"""M0 proof: the substitution mechanism works before any engine code exists.

This is the highest-risk infrastructure in the project. If we cannot interpose
ourselves under ``django.template`` such that Django's *own internal* imports pick
up our modules, then reimplementing the engine one module at a time is impossible
and the whole plan needs rethinking. So it is proven first, against nothing but
Django's fallback.
"""

import importlib
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

import django  # noqa: E402  (path must be set up first)

from django_rust_templates import _shim  # noqa: E402


class SubstituteDjangoTemplateTests(unittest.TestCase):
    def tearDown(self):
        if _shim.installed():
            _shim.uninstall()

    def test_django_template_resolves_to_our_implementation(self):
        real_file = importlib.import_module("django.template").__file__
        self.assertNotIn("_impl", real_file)

        _shim.install()

        shimmed = sys.modules["django.template"]
        self.assertTrue(
            Path(shimmed.__file__).resolve().is_relative_to(
                (PROJECT_ROOT / "python" / "django_rust_templates" / "_impl").resolve()
            ),
            f"django.template came from {shimmed.__file__}",
        )

    def test_public_api_survives_substitution(self):
        """The shell must present the same names even when nothing is ours yet."""
        expected = ["Engine", "Template", "Context", "RequestContext", "Node",
                    "NodeList", "Origin", "Variable", "Library", "engines",
                    "TemplateDoesNotExist", "TemplateSyntaxError"]

        _shim.install()
        import django.template as shimmed

        for name in expected:
            self.assertTrue(hasattr(shimmed, name), f"missing {name}")

    def test_internal_imports_are_interposed(self):
        """The point of the design: Django's own modules see our modules.

        ``django.template``'s ``__path__`` must search our implementation
        directory ahead of Django's, so that when we later replace ``base`` our
        replacement is what every other Django module resolves.
        """
        _shim.install()

        import django.template

        impl_dir = (PROJECT_ROOT / "python" / "django_rust_templates" / "_impl").resolve()
        search = [Path(p).resolve() for p in django.template.__path__]

        self.assertEqual(search[0], impl_dir)
        self.assertGreater(len(search), 1, "Django's own path must remain as fallback")
        # Django's real tree is still reachable, which is what makes incremental
        # reimplementation possible.
        self.assertTrue(any((p / "base.py").is_file() for p in search[1:]))

    def test_rendering_still_works_through_fallback(self):
        """With no engine code written, templates render via Django's fallback."""
        _shim.install()

        from django.template import Context, Engine, Template

        # An explicit engine keeps this hermetic: Engine.get_default() would
        # require configured settings.
        engine = Engine()
        rendered = Template("{{ a }}", engine=engine).render(Context({"a": "ok"}))
        self.assertEqual(rendered, "ok")

    def test_unshimmed_reports_progress_and_is_currently_everything(self):
        _shim.install()

        remaining = _shim.unshimmed()
        self.assertTrue(remaining, "most modules should still fall back")
        self.assertIn("django.template.base", remaining)
        # Ours is not in the list, because ours is the package itself.
        self.assertNotIn("django.template", remaining)

    def test_an_implemented_module_wins_over_djangos(self):
        """The load-bearing claim: a module in ``_impl`` is the one everyone gets.

        ``exceptions`` is implemented, so both a direct import and Django's own
        internal import of it must resolve to our file.
        """
        _shim.install()

        import django.template.exceptions as ours

        impl_dir = (PROJECT_ROOT / "python" / "django_rust_templates" / "_impl").resolve()
        self.assertEqual(Path(ours.__file__).resolve().parent, impl_dir)

        # And it is no longer counted as falling back.
        self.assertNotIn("django.template.exceptions", _shim.unshimmed())

    def test_implemented_exception_behaves_like_djangos(self):
        _shim.install()

        from django.template.exceptions import TemplateDoesNotExist

        exc = TemplateDoesNotExist("nope", tried=[("x", "y")], backend="b")
        self.assertEqual(exc.args, ("nope",))
        self.assertEqual(exc.tried, [("x", "y")])
        self.assertEqual(exc.backend, "b")
        self.assertEqual(exc.chain, [])

    def test_uninstall_restores_django(self):
        _shim.install()
        _shim.uninstall()

        restored = sys.modules["django.template"]
        self.assertFalse(
            Path(restored.__file__).resolve().is_relative_to(
                (PROJECT_ROOT / "python" / "django_rust_templates" / "_impl").resolve()
            )
        )
        self.assertFalse(_shim.installed())

    def test_a_partial_subpackage_still_falls_back_for_its_siblings(self):
        """The fallback has to be reapplied at every level, not just the top.

        ``django.template.loaders`` is a package. The moment we provide
        ``_impl/loaders/__init__.py``, that package's ``__path__`` is ours alone
        unless the finder rebuilds it with Django's directory appended — and then
        ``django.template.loaders.filesystem`` stops resolving at all. That would
        make the fallback vanish exactly when a milestone is half-finished, which
        is when it is needed most.

        This creates that situation with one throwaway module and checks both
        that our module wins and that its Django sibling still resolves.
        """
        package_dir = (
            PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "loaders"
        )
        init = package_dir / "__init__.py"
        self.assertFalse(package_dir.exists(), "leftover from an earlier run")

        package_dir.mkdir()
        init.write_text("MARKER = 'ours'\n")
        try:
            _shim.install()

            ours = importlib.import_module("django.template.loaders")
            self.assertEqual(getattr(ours, "MARKER", None), "ours")

            sibling = importlib.import_module("django.template.loaders.filesystem")
            self.assertNotIn(
                "_impl",
                sibling.__file__,
                "our partial package cut its Django sibling off from the fallback",
            )

            # The meter must now tell the truth about this case too: `loaders` is
            # ours, its siblings are not, and they still import.
            remaining = _shim.unshimmed()
            self.assertNotIn("django.template.loaders", remaining)
            self.assertIn("django.template.loaders.filesystem", remaining)
        finally:
            # Importing the package writes a __pycache__ into it, so this is a
            # tree removal rather than an unlink+rmdir.
            shutil.rmtree(package_dir, ignore_errors=True)

    def test_install_is_idempotent(self):
        _shim.install()
        before = sys.modules["django.template"]
        _shim.install()
        self.assertIs(before, sys.modules["django.template"])

    def test_strict_mode_raises_while_falling_back(self):
        with self.assertRaises(_shim.ShimError):
            _shim.install(strict=True)


if __name__ == "__main__":
    unittest.main()
