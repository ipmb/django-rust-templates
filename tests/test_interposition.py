"""Direct-entry evidence that the interposed modules are actually ours.

The suite cannot see this. Measured (``.review/m9-recon.md`` finding 3): with
``_impl/base.py`` a byte-for-byte copy of Django's, ``unshimmed()`` reports one
fewer module, the whole suite is green, and the only row that notices is the
meter. So a transcription that delegates nothing, or delegates wrongly, passes
every count this project otherwise accepts.

Every row here imports the module through the shim and asserts on the file it
resolved to, or on a Rust call it must have made — the latter because a
delegation that quietly called Django's own implementation is green too
(finding 1a).

Rows arrive with the commit that makes them true, so every commit is green:
``base``'s here, ``compile_nodelist`` in Task 2, ``smartif`` in Task 3, the
resolver in Task 5.
"""

import unittest

from django_rust_templates import _shim


class InterpositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shim.install()

    def test_base_resolves_to_our_file(self):
        import django.template.base as base

        self.assertIn("_impl", base.__file__, base.__file__)

    def test_the_node_classes_are_defined_by_our_file(self):
        """Resolution and *identity* are different claims.

        The module resolving to our file does not by itself mean the class a
        render uses was defined there: a consumer that bound the name from an
        earlier import keeps Django's object while the module is ours. So the
        assertion is on the defining file.

        **Not on ``__module__``**, which reads ``django.template.base`` for our
        classes and is *supposed* to — the shim loads our file under Django's
        name, so that is the correct value for a drop-in replacement. The first
        draft of this row asserted ``"_impl" in cls.__module__`` and failed on
        all five classes for exactly that reason.
        """
        import inspect

        import django.template.base as base

        for name in ("Template", "NodeList", "Node", "Variable", "VariableNode"):
            with self.subTest(name=name):
                self.assertIn("_impl", inspect.getfile(getattr(base, name)))

    def test_the_transcription_is_the_pinned_source(self):
        """A second opinion on the file itself, independent of the guard.

        ``check_transcriptions.py`` says the differing regions are the declared
        ones; that is a statement about a diff. This says the *bulk* of the file
        is upstream's, by line count, which is the thing a reader wants to know
        before believing the guard: the transcription is 1209 lines against
        upstream's 1207, and the two extra are the marker and its blank
        separator. A transcription that had drifted by a paragraph would move
        both numbers together and this row would not notice — that is the
        guard's job — but a transcription that had been *replaced* would.
        """
        import pathlib

        from django_rust_templates import _shim as shim

        project_root = pathlib.Path(__file__).resolve().parent.parent
        ours = (shim._IMPL_DIR / "base.py").read_text().splitlines()
        theirs = (project_root / ".django" / "django" / "template" / "base.py")
        theirs = theirs.read_text().splitlines()

        # Ours is `\"\"\"`, the marker, a blank, then upstream from line 2 on.
        self.assertEqual(len(ours), len(theirs) + 2)
        self.assertEqual(ours[3:], theirs[1:])


if __name__ == "__main__":
    unittest.main()
