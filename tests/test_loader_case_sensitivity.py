"""§2's coverage hole, closed from outside Django's tree.

Django's row is `.django/tests/template_tests/test_loaders.py`'s
`test_case_sensitivity`, and it is *guarded* rather than failing:

    @unittest.skipUnless(
        os.path.normcase("/TEST") == os.path.normpath("/test"),
        "This test only runs on case-sensitive file systems.",
    )

`normcase` is the identity function on POSIX, so the condition is `"/TEST" ==
"/test"` → False everywhere except Windows. **The filesystem is irrelevant**: a
case-sensitive APFS volume would not recover the row, because the guard is
evaluated before any file is opened. And there is no folded branch to port —
`grep -n normcase` over `django/template/` returns nothing; the loader is
`safe_join(template_dir, template_name)` inside `get_template_sources`.

So this module reproduces the *question* the guarded row asks — does joining a
name onto a configured directory fold case? — rather than a behaviour that does
not exist, and it runs on every platform. That matters because the loader it
exercises is code we own from M10 (`_impl/loaders/filesystem.py`), and because
the no-edit rule means the upstream row can never be unskipped by us.

The two rows are deliberately different in kind: the first is pure path
arithmetic and asserts Django's own expectations, and the second touches the
filesystem and therefore *measures* the volume's case sensitivity instead of
assuming it — a row that skipped on the wrong volume would be the same hole one
level down.
"""

import pathlib
import tempfile
import unittest

from django.template import Context, Engine
from django.template.exceptions import TemplateDoesNotExist


def sources(dirs, name):
    """The template origins a filesystem loader produces for ``name``."""
    engine = Engine(loaders=[("django.template.loaders.filesystem.Loader", dirs)])
    loader = engine.template_loaders[0]
    return [origin.name for origin in loader.get_template_sources(name)]


class CaseSensitivityTests(unittest.TestCase):
    def setUp(self):
        import importlib

        filesystem = importlib.import_module("django.template.loaders.filesystem")
        self.assertIn("_impl", filesystem.__file__, filesystem.__file__)

    def test_djangos_row_holds_without_its_platform_guard(self):
        """Django's assertions, against our loader, on this platform.

        Nothing is opened: `get_template_sources` is `safe_join` per configured
        directory, so both expectations are path arithmetic and both hold
        anywhere. That is the point — the upstream row's guard hides a question
        whose answer does not depend on the platform at all.

        The second expectation is *not* upstream's verbatim. Upstream pairs the
        name `/DIR1/index.HTML` with the directories `["/dir1", "/DIR2"]`, which
        yields it only under `normcase` folding — on POSIX the containment guard
        in `safe_join` compares `/DIR1/index.HTML` against `/dir1/` without
        folding, rejects both directories, and produces `[]`. So the case here is
        made to match the directory, which is the shape the question is actually
        about: the *name's* case is preserved, not folded onto the directory's.
        """
        self.assertEqual(
            sources(["/dir1", "/DIR2"], "index.html"),
            ["/dir1/index.html", "/DIR2/index.html"],
        )
        self.assertEqual(
            sources(["/DIR1", "/DIR2"], "/DIR1/index.HTML"),
            ["/DIR1/index.HTML"],
        )

    def test_a_case_mismatch_is_not_a_template_where_the_volume_is_case_sensitive(self):
        """The filesystem half, with the expectation *measured* rather than assumed.

        A probe writes `probe.html` and asks whether `PROBE.HTML` exists — the
        same question in miniature, asked of the same volume — so this row runs
        on a case-sensitive volume and on a case-insensitive one, and asserts the
        matching expectation rather than skipping. The probe is a directory
        lookup, not a loader call, so it cannot be satisfied by the code under
        test.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "probe.html").write_text("probe")
            case_sensitive = not pathlib.Path(tmpdir, "PROBE.HTML").exists()

            pathlib.Path(tmpdir, "Index.html").write_text("{{ name }}")
            engine = Engine(
                loaders=[("django.template.loaders.filesystem.Loader", [tmpdir])]
            )

            if case_sensitive:
                with self.assertRaises(TemplateDoesNotExist):
                    engine.get_template("index.html")
            else:
                self.assertEqual(
                    engine.get_template("index.html").render(Context({"name": "x"})),
                    "x",
                )

    def test_the_configured_directory_keeps_its_case(self):
        """The other direction: our loader must not fold the *configured* side.

        A directory spelled `/Straße` or `/DIR2` is joined as written; folding it
        would make a loader find templates in a directory nobody configured.
        """
        self.assertEqual(sources(["/Mixed/Case"], "a.html"), ["/Mixed/Case/a.html"])


if __name__ == "__main__":
    unittest.main()
