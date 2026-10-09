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
import tempfile
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_CHECKOUT = PROJECT_ROOT / ".django"

if str(DJANGO_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(DJANGO_CHECKOUT))

import django  # noqa: E402  (path must be set up first)

from django_rust_templates import _shim  # noqa: E402


class SubstituteDjangoTemplateTests(unittest.TestCase):
    """The shim's own rows, and the one piece of session state they mutate.

    **``tearDown`` restores the state it found rather than uninstalling.** The
    first version uninstalled unconditionally, and the cost was measured by the
    M11 review: ``run_unit_tests.sh`` installs the shim at interpreter start —
    that is the whole reason the script exists (537 order-dependent failures
    without it, 8 with a mid-run install, 0 with an install at start) — so
    uninstalling here destroyed the invariant the runner was written to
    establish, for the *tail* of every run. Every module discovered after this
    one then ran against stock Django, which today is three
    (``test_tag_inventory``, ``test_transcription_equivalence``,
    ``test_variable_parity``) and grows with any module whose name sorts after
    ``test_shim``.

    **It was latent, and that is the part worth keeping.** All three of those
    modules passed both ways, so nothing was red — but a row that is green for a
    reason other than the one it names is worse than a row that fails, because
    the green is read as evidence. ``test_variable_parity``'s guard was in
    exactly that state for two milestones, and it is why its fix is
    ``load_pinned("base.py").Variable``: naming the universe rather than asking
    the session which one it is in.

    The rows that assert ``installed()`` is false after an ``uninstall`` are
    within-row and unaffected: they read the state they just made.
    """

    def setUp(self):
        # The session's baseline, captured rather than assumed — a row run on
        # its own (no `run_unit_tests.sh`) has it out, and must leave it out.
        self._installed_at_setup = _shim.installed()

    def tearDown(self):
        if self._installed_at_setup and not _shim.installed():
            _shim.install()
        elif not self._installed_at_setup and _shim.installed():
            _shim.uninstall()

    def test_tearDown_restores_the_state_it_found(self):
        """The contract ``every row after this module`` depends on, tested.

        Both directions, because the shape being asserted is *restore*, not
        *always install*: this row fails on the unconditional-uninstall version
        that ran until M11's review, and it also fails if someone "fixes" that by
        installing at teardown unconditionally — which would hand a bare
        ``python -m unittest tests.test_shim`` a shimmed session it did not ask
        for, and would hide the same class of bug in the other direction.
        """
        baseline = self._installed_at_setup

        # A row that ends with the shim out, the way `test_uninstall_restores_django`
        # does.
        if baseline:
            _shim.uninstall()
        self.tearDown()
        self.assertEqual(
            _shim.installed(),
            baseline,
            "tearDown did not put the session back where setUp found it",
        )

        # And a row that ends with it in, when it started out.
        self._installed_at_setup = not baseline
        if not _shim.installed():
            _shim.install()
        self.tearDown()
        self.assertEqual(_shim.installed(), not baseline)
        self._installed_at_setup = baseline

    def test_django_template_resolves_to_our_implementation(self):
        # The "before" half is established here rather than inherited from the
        # previous row's teardown, which is what it used to be: with the session
        # baseline installed, a row that needs the shim *out* has to say so.
        _shim.uninstall()
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
        `smartif`, 22 with `utils`, 21 with `context`, 20 with `engine`, 14 with
        the six `loaders` files — the milestone's number — 13 with
        `loader_tags`, M11's first, 12 with `defaultfilters`, M11's second, and
        11 with `defaulttags`, M11's third).
        """
        _shim.install()

        remaining = _shim.unshimmed()
        self.assertEqual(len(remaining), 11, sorted(remaining))
        self.assertNotIn("django.template.base", remaining)
        self.assertNotIn("django.template.smartif", remaining)
        self.assertNotIn("django.template.utils", remaining)
        self.assertNotIn("django.template.context", remaining)
        self.assertNotIn("django.template.engine", remaining)
        self.assertNotIn("django.template.loader_tags", remaining)
        # The package and the five modules under it. `loaders` is the first
        # *package* we provide, so it is the first name here that the meter could
        # drop while its siblings stayed Django's — which is why the six move
        # together in `INTERPOSED` and are asserted here one by one.
        self.assertNotIn("django.template.loaders", remaining)
        self.assertNotIn("django.template.loaders.base", remaining)
        self.assertNotIn("django.template.loaders.filesystem", remaining)
        self.assertNotIn("django.template.loaders.app_directories", remaining)
        self.assertNotIn("django.template.loaders.cached", remaining)
        self.assertNotIn("django.template.loaders.locmem", remaining)
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

        ``django.template.backends`` is a package. The moment we provide
        ``_impl/backends/__init__.py``, that package's ``__path__`` is ours alone
        unless the finder rebuilds it with Django's directory appended — and then
        ``django.template.backends.django`` stops resolving at all. That would
        make the fallback vanish exactly when a milestone is half-finished, which
        is when it is needed most.

        This creates that situation with one throwaway module and checks both
        that our module wins and that its Django sibling still resolves.

        ``django.template.backends`` and ``django.template.backends.probe`` are
        the subjects because they are a package with siblings and are owned by
        M13 (§10.3), not by the milestone being worked. The subject used to be
        ``loaders``, chosen as "as far from the interposition frontier as the tree
        allows" — which expired at M10. The re-pick alone would have expired
        again at M13, because ``django/template/`` has only two subpackages
        (``backends`` and ``loaders``), so the cleanup below is the part that
        matters: the row now records what it found, writes only files that were
        not there, and removes only what it wrote.

        ``probe`` rather than asserting on the package's own ``__init__.py`` is
        what gives the row no expiry left. It is a name Django does not have and
        a file only this row writes, so the positive half of the assertion reads
        *our* module whether or not a later milestone has transcribed
        ``backends/__init__.py`` — where writing the marker into ``__init__.py``
        would have turned the row into an assertion about a file M13 owns, and
        into a failure on the day M13 landed. The package still has to be ours
        for the submodule to be ours at all, and the meter row below is what says
        so. It also leaves ``filesystem.py`` out of the probe set: ``backends``
        has no such sibling.

        The cache-drop below is not incidental. The row writes a module and
        expects the finder to see it, but ``install()`` returns early when the
        shim is already installed (``_shim.py:550``), and a session-start install
        (which ``check.sh`` now does, so every test module binds the same
        classes) means the shim *is* already installed by the time this row
        runs. A ``django.template.backends`` cached earlier in the session then
        wins over the file just written, and the assertion below reads
        ``None != 'ours'``. Measured both ways before the fix.
        """
        package_dir = (
            PROJECT_ROOT / "python" / "django_rust_templates" / "_impl" / "backends"
        )
        # **Record what was here before writing anything.** The first version of
        # this row asserted the directory did not exist and removed it in
        # `finally`, which is a row that can only run while its subject is
        # absent — so it went from a test to a deletion the day M13 gave
        # `_impl/backends/` a transcription, and the failure would have looked
        # like a broken fallback rather than a test cleaning up after itself.
        # Whether or not the subject exists is now the row's input rather than
        # its premise: the probe file is ours to remove, and the `__init__.py`
        # and the directory are ours to remove only if we made them.
        created_package_dir = not package_dir.exists()
        package_dir.mkdir(parents=True, exist_ok=True)
        init = package_dir / "__init__.py"
        had_init = init.exists()
        probe = package_dir / "probe.py"
        try:
            if not had_init:
                init.write_text("")
            probe.write_text("MARKER = 'ours'\n")
            _shim.install()

            # Drop the cached bindings so the finder is consulted for the
            # package we just created — the purge `install()` would have done if
            # it were not already installed.
            for name in [
                n for n in sys.modules if n.startswith("django.template.backends")
            ]:
                del sys.modules[name]

            ours = importlib.import_module("django.template.backends.probe")
            self.assertEqual(getattr(ours, "MARKER", None), "ours")

            sibling = importlib.import_module("django.template.backends.django")
            self.assertNotIn(
                "_impl",
                sibling.__file__,
                "our partial package cut its Django sibling off from the fallback",
            )

            # The meter must now tell the truth about this case too: `backends`
            # is ours, its siblings are not, and they still import.
            remaining = _shim.unshimmed()
            self.assertNotIn("django.template.backends", remaining)
            self.assertIn("django.template.backends.django", remaining)
        finally:
            probe.unlink(missing_ok=True)
            # The bytecode importing `probe` wrote, and only that — anything
            # else in this cache belongs to a module the directory was already
            # serving, and guessing at that is how the rmtree this replaced got
            # its start.
            cache = package_dir / "__pycache__"
            if cache.is_dir():
                for stale in cache.glob("probe.*.pyc"):
                    stale.unlink(missing_ok=True)
            if not had_init:
                init.unlink(missing_ok=True)
            # Importing the package writes a __pycache__ into it, so when we made
            # the directory this is a tree removal rather than an unlink+rmdir —
            # and when we did not, the directory and its cache are somebody
            # else's and stay.
            if created_package_dir:
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

        **The holder is synthetic, and that is the change M11 Task 3 forced.**
        This row used to take ``django.template.defaulttags`` as its example of
        "a module in a capture namespace holding Django's ``Node``", by dropping
        it from ``sys.modules`` and importing it while the shim was out. That
        vehicle is gone: ``defaulttags`` is now one of ours, so ``install``
        *replaces* it rather than leaving it stale, and the name no longer
        reproduces the state at all — the row went from asserting a refusal to
        asserting nothing (``ShimError`` not raised). Any real Django module under
        ``django.template`` would work today and rot the moment a later milestone
        interposes it, which is exactly the recurrence being fixed here, so the
        holder is a module this row builds: it is in the capture namespace,
        ``_impl`` does not serve it, and it holds Django's ``Node``. Nothing about
        the hazard is synthetic — the binding, the identity comparison and the
        refusal are the real ones.

        The state is reproduced rather than described: with the shim out, import
        Django's ``base`` — the finder is not installed, so the import reaches the
        pin rather than our transcription — and hang its ``Node`` off a probe
        module, then install. A refused install must also leave the process where
        it started, so the assertion after it is that the shim is *not* installed.
        """
        _shim.uninstall()
        try:
            django_base = importlib.import_module("django.template.base")
            self.assertNotIn("_impl", django_base.__file__ or "")

            probe = types.ModuleType("django.template.m11_stale_capture_probe")
            probe.Node = django_base.Node
            sys.modules[probe.__name__] = probe

            with self.assertRaises(_shim.ShimError) as caught:
                _shim.install()

            message = str(caught.exception)
            self.assertIn("django.template.m11_stale_capture_probe.Node", message)
            self.assertIn("django.template.base.Node", message)
            self.assertFalse(_shim.installed(), "a refused install left the shim in")
        finally:
            # The probe holding the capture is dropped so the next install finds
            # nothing stale, and the shim goes back to installed for every later
            # row.
            sys.modules.pop("django.template.m11_stale_capture_probe", None)
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

        The rows call `uninstall()` themselves, so the redundant call is the
        ordinary case rather than a corner, and the assertion is on what a caller
        can see: after the cycle, ``install`` still gives back *our* file.
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
        """Strict install refuses while any module would still be Django's.

        The premise — shim *out*, so the install is the one being refused — is
        established here, like the row above. With the session baseline installed
        this call is the idempotent re-install instead, and an install that is a
        no-op has nothing to refuse: the row would assert nothing while staying
        green, which is how it read before the teardown fix made the state at row
        start explicit.
        """
        _shim.uninstall()
        with self.assertRaises(_shim.ShimError):
            _shim.install(strict=True)


class NamespaceTests(unittest.TestCase):
    """M12 Task 1: the shim serves a second namespace, and the meter gains a scope.

    ``django.templatetags`` is that namespace, and it is **forced rather than
    chosen**: three of the suite's own modules import a node class out of a
    library by name — ``from django.templatetags.static import StaticNode``
    (``syntax_tests/test_static.py:5``), ``from django.templatetags.l10n import
    LocalizeNode`` (``i18n/test_translate.py:7``), ``from
    django.templatetags.i18n import BlockTranslateNode``
    (``i18n/test_blocktranslate.py:9``) — so unless the namespace
    is served, those imports take Django's classes however the modules themselves
    are interposed, and a node built by our parser fails an ``assertIsInstance``
    against them.

    **The two namespaces map into ``_impl`` differently, and that is a fact to
    state rather than a wart to smooth over.** ``django.template`` is flat
    because M9 built it that way, and re-keying it under ``_impl/template/``
    would move every key in ``SHADOWING``, ``DECLARED``, ``COMPARED_PATH`` and
    the anchor manifest with their recorded line spans. ``django.templatetags``
    is a subdirectory because the unit of interposition is the path — a key
    carries its subdirectory, ``loaders/base.py`` and not ``base.py``, which M10
    paid to learn.

    ``tearDown`` restores the state it found, per the contract above.
    """

    def setUp(self):
        self._installed_at_setup = _shim.installed()

    def tearDown(self):
        if self._installed_at_setup and not _shim.installed():
            _shim.install()
        elif not self._installed_at_setup and _shim.installed():
            _shim.uninstall()

    def test_the_two_namespaces_map_into_impl_differently(self):
        self.assertEqual(_shim.TARGET, _shim.NAMESPACES[0])
        self.assertEqual(_shim._relative_name("django.template"), "")
        self.assertEqual(_shim._relative_name("django.template.base"), "base")
        self.assertEqual(
            _shim._relative_name("django.template.loaders.base"), "loaders/base"
        )
        self.assertEqual(_shim._relative_name("django.templatetags"), "templatetags")
        self.assertEqual(
            _shim._relative_name("django.templatetags.i18n"), "templatetags/i18n"
        )
        self.assertIsNone(_shim._relative_name("django.utils.translation"))
        # A name that only *looks* like the other namespace's prefix. Without
        # longest-first matching this resolves as `template` + `tags.i18n`.
        self.assertIsNone(_shim._relative_name("django.templateish"))

    def test_the_path_carries_no_extension_because_the_name_cannot_say_which(self):
        """``loaders`` is a package and ``loaders.base`` is not, and the dotted
        name does not distinguish them.

        So the extension is the *filesystem's* answer, given in ``find_spec``
        and ``_served`` (which try ``__init__.py`` first, then ``.py``), and this
        function stays a pure translation of the name.
        """
        self.assertEqual(_shim._relative_name("django.template.loaders"), "loaders")
        self.assertEqual(
            _shim._relative_name("django.template.loaders.base"), "loaders/base"
        )

    def test_the_meter_keeps_its_meaning_and_gains_a_second_population(self):
        """``unshimmed()`` is unchanged, and the second count is the same function.

        Not a second function: two implementations of one count drift, which is
        the failure this tree has hit three times (D3', the vacuity reasons,
        M10's ``origin`` row). The scope argument is the second population.

        The second count is **3** in the commit that transcribes ``cache`` and
        ``static``, where the plan said 2 — measured, not argued. Both modules
        are interposed by that commit, so both leave ``unshimmed``; the plan
        counted one of them twice (``cache`` is the library task's headline and
        ``static`` the one it rides with) and the arithmetic is only visible
        here, where the set is written out.

        It is **2** in M12 Task 4, which interposes ``tz`` — the third library,
        one module, so the delta is one and the set below is the arithmetic
        again. The plan's Step 7 says this commit's line is
        ``the second count is 1``; that assumed Task 3 left the count at 2 and
        Task 3 measured 3, so the plan is off by one and the measured line is
        ``the second count is 2``. See the commit message.
        """
        _shim.install()
        self.assertEqual(_shim.unshimmed(), _shim.unshimmed(scope="django.template"))
        self.assertEqual(len(_shim.unshimmed()), 11)
        self.assertEqual(
            sorted(_shim.unshimmed(scope="django.templatetags")),
            [
                "django.templatetags.i18n",
                "django.templatetags.l10n",
            ],
        )

    def test_a_namespaces_files_are_not_everything_under_its_root(self):
        """The label against the measurement, staged on a real tree.

        `django.template` maps to `_impl` itself, so the bare walk of that root
        also answers for `django.templatetags` — from the commit that creates
        `_impl/templatetags/`, `templatetags.i18n` would be reported as ours for
        `django.template`. Harmless in `unshimmed`, which subtracts, and that is
        why it needs a row rather than a note: a coverage or ownership check is
        the obvious next caller, and the docstring said "our files for one
        namespace" with nothing checking it.

        The tree is staged in a temporary directory rather than waited for,
        because `_impl/templatetags/` does not exist yet — a row that cannot fail
        until Task 5 is a row nobody has read. `_IMPL_DIR` is restored in the
        `finally`, so the shim is untouched outside the block.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base.py").write_text("")
            (root / "templatetags").mkdir()
            (root / "templatetags" / "i18n.py").write_text("")
            original = _shim._IMPL_DIR
            _shim._IMPL_DIR = root
            try:
                outer = _shim._our_modules_in("django.template")
                inner = _shim._our_modules_in("django.templatetags")
            finally:
                _shim._IMPL_DIR = original
        self.assertEqual(inner, {"i18n"})
        self.assertEqual(
            outer,
            {"base"},
            "`django.template`'s files are not everything under `_impl`: a "
            "nested namespace answers for its own names, from its own root",
        )

    def test_serving_a_namespace_does_not_claim_the_whole_of_it(self):
        """Review Focus 3: a library we do not provide must still resolve as Django's.

        ``_impl/templatetags/`` does not exist yet, so the row asserts the
        *mechanism* — the finder names the path and declines it because no file
        is there. It stays meaningful afterwards for every name the five
        libraries do not cover, and it is what would catch a finder that claimed
        the namespace rather than the files it actually has.
        """
        _shim.install()
        self.assertEqual(
            _shim._relative_name("django.templatetags.notalibrary"),
            "templatetags/notalibrary",
        )
        self.assertFalse(_shim._served("django.templatetags.notalibrary"))
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("django.templatetags.notalibrary")

    def test_the_namespace_package_itself_is_not_served(self):
        """``django.templatetags`` is Django's package; only its members are ours.

        Serving the package would interpose ``__init__.py`` for no reason and
        give it a ``__path__`` of ours, so the finder must decline the name
        while accepting the modules beneath it.
        """
        _shim.install()
        self.assertFalse(_shim._served("django.templatetags"))
        self.assertIs(importlib.import_module("django.templatetags"), sys.modules["django.templatetags"])


if __name__ == "__main__":
    unittest.main()
