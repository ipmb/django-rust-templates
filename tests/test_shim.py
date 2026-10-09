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

    def test_unshimmed_reports_progress(self):
        """The meter, pinned as a number rather than a membership test.

        This row used to assert ``"django.template.base" in remaining``, which
        was true until M9 interposed it — and which told us nothing about the
        other twenty-four modules, so a later change that handed any of them
        *back* would have gone on passing. The count is the claim each milestone
        is accepted on, so the count is what is asserted; it moves by one in the
        same commit as each interposition (25 at M8, 24 with `base`, 23 with
        `smartif`, 22 with `utils`, 21 with `context` — the milestone's number).
        """
        _shim.install()

        remaining = _shim.unshimmed()
        self.assertEqual(len(remaining), 21, sorted(remaining))
        self.assertNotIn("django.template.base", remaining)
        self.assertNotIn("django.template.smartif", remaining)
        self.assertNotIn("django.template.utils", remaining)
        self.assertNotIn("django.template.context", remaining)
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

    def test_a_reinstall_does_not_re_execute_a_module(self):
        """A cycle must not run a module again, or every held reference rots.

        ``install``/``uninstall`` used to drop every ``django.template`` module
        and import it again, which re-executes the file and yields new classes.
        Anything that captured the old ones keeps holding them, and one measured
        cycle produced two failures — neither visible in the accepted run,
        because ``discover`` runs ``test_interposition`` before this module and
        "i" sorts before "s":

        * ``django_rust_templates.render`` holds ``TemplateSyntaxError`` at
          module scope, so ``assertRaises(TemplateSyntaxError)`` stopped
          catching what ``django_exception`` raises — two ``TemplateSyntaxError``
          classes, one raised and one caught;
        * ``host.django_own_tags()`` is frozen at import by design, and the
          re-executed ``defaulttags`` published *new* tag callables, so
          ``ShellHost.has_tag`` judged ``{% if %}`` to be Django's and handed it
          back to Python's ``do_if`` — Rust silently stopped parsing the
          condition, which is the Rust-against-Rust hazard one level down.

        So the assertion is **identity of the objects a caller already holds**,
        not a file path. The paths are checked by the rows above; this is the
        property they cannot see, and it is why the fix caches modules rather
        than purging them.
        """
        _shim.install()

        import django.template.defaulttags as defaulttags
        import django.template.exceptions as exceptions
        import django.template.smartif as smartif

        from django_rust_templates import host, render

        _shim.uninstall()
        _shim.install()

        self.assertIs(sys.modules["django.template.exceptions"], exceptions)
        self.assertIs(sys.modules["django.template.smartif"], smartif)
        self.assertIs(sys.modules["django.template.defaulttags"], defaulttags)
        self.assertIs(render.TemplateSyntaxError, exceptions.TemplateSyntaxError)
        self.assertIs(
            host.django_own_tags()["if"], defaulttags.register.tags["if"]
        )

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

        ``django.template.loaders`` and ``django.template.loaders.filesystem``
        are the subjects because they are a package with siblings and are as far
        from the interposition frontier as the tree allows. The spec's §10.3
        schedules ``loaders`` for M10 (line 789), so this subject has a known
        expiry rather than an open-ended one, and the next person should re-pick
        it deliberately rather than by bisecting a failure.

        The cache-drop below is not incidental. The row writes a module and
        expects the finder to see it, but ``install()`` returns early when the
        shim is already installed (``_shim.py:398``), and a session-start install
        (which ``check.sh`` now does, so every test module binds the same
        classes) means the shim *is* already installed by the time this row
        runs. A ``django.template.loaders`` cached earlier in the session then
        wins over the file just written, and the assertion below reads
        ``None != 'ours'``. Measured both ways before the fix.
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

            # Drop the cached bindings so the finder is consulted for the
            # package we just created — the purge `install()` would have done if
            # it were not already installed.
            for name in [
                n for n in sys.modules if n.startswith("django.template.loaders")
            ]:
                del sys.modules[name]

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

    def test_install_refuses_a_capture_from_the_universe_it_replaces(self):
        """A module that imported Django's `Node` first is refused, not papered over.

        ``from django.template.base import Node`` is a copy, and nothing done to
        ``django.template.base`` afterwards can change it — so a module executed
        while Django's template modules were current keeps Django's ``Node`` for
        the rest of the process, and every ``isinstance`` against ours is then
        false. The measured shape is ``host.django_own_tags()["if"] is
        defaulttags.register.tags["if"]`` going ``False``, which makes
        ``ShellHost.has_tag`` hand ``{% if %}`` back to Python's ``do_if``.

        The state is reproduced rather than described: drop the cached
        ``defaulttags``, import it while the shim is out, then install. A refused
        install must also leave the process where it started, so the assertion
        after it is that the shim is *not* installed.
        """
        _shim.uninstall()
        try:
            sys.modules.pop("django.template.defaulttags", None)
            import django.template.defaulttags as django_defaulttags

            self.assertNotIn("_impl", django_defaulttags.__file__ or "")

            with self.assertRaises(_shim.ShimError) as caught:
                _shim.install()

            message = str(caught.exception)
            self.assertIn("django.template.defaulttags", message)
            self.assertIn("Node", message)
            self.assertFalse(_shim.installed(), "a refused install left the shim in")
        finally:
            # The module holding the capture is dropped so the next import takes
            # ours, and the shim goes back to installed for every later row.
            sys.modules.pop("django.template.defaulttags", None)
            _shim.install()

    def test_uninstall_while_uninstalled_does_not_poison_the_cache(self):
        """The second ``uninstall()`` must not file Django's modules as ours.

        ``uninstall`` moves every served name from ``sys.modules`` into the
        *ours* cache. Called when the shim is already out, that cache is holding
        Django's own modules — so afterward ``install`` places Django's ``base``
        under our name, and every module that captured our ``Node`` is then a
        module holding a class nothing else has. The visible damage is the
        fail-loud check refusing a legitimate install, which is how this was
        found: 24 bindings reported stale across `test_shim`'s run, all of them
        correct against the cache before the second `uninstall` touched it.

        `tearDown` uninstalls and the rows call `uninstall()` themselves, so the
        redundant call is the ordinary case rather than a corner, and the
        assertion is on what a caller can see: after the cycle, ``install``
        still gives back *our* file.
        """
        _shim.install()
        _shim.uninstall()

        django_base = sys.modules["django.template.base"]
        _shim.uninstall()  # already out: must change nothing

        self.assertIs(sys.modules["django.template.base"], django_base)

        _shim.install()
        ours = sys.modules["django.template.base"]
        self.assertTrue(
            Path(ours.__file__).resolve().is_relative_to(
                (PROJECT_ROOT / "python" / "django_rust_templates" / "_impl").resolve()
            ),
            f"the second install put {ours.__file__} under our name",
        )

    def test_strict_mode_raises_while_falling_back(self):
        with self.assertRaises(_shim.ShimError):
            _shim.install(strict=True)


if __name__ == "__main__":
    unittest.main()
