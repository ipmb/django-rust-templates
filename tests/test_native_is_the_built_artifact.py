"""The build-identity check's own rows: each drives it with the defect it exists for.

`scripts/assert_native_is_the_built_artifact.py` is the behavioural half of the claim that the
extension the Python suites import is the one the gate just built. The row in
``tests/test_suite_isolation.py::GateBuildTests`` pins the *text* — the
``maturin develop --release`` invocation and its position — and cannot fail if the
build succeeds and installs somewhere the tests do not read. This file is about
the other half, and it is shaped like ``tests/test_anchor_manifest.py``: a checker
that has never been *seen* to fail is the defect it exists to catch, one level up,
so none of these is a green run over the tree.

The fixtures are fabricated rather than drawn from the real ``target/`` directory,
and that is deliberate: a row that required a release build to be installed would
fail for anyone running ``scripts/run_unit_tests.sh`` after a plain
``maturin develop``, which is the correct loop and installs the *debug* profile.
The claim that the gate built the released file belongs in the gate — where the
script runs one line after the build — and what is testable in isolation is the
comparison itself.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifact_check = _load("assert_native_is_the_built_artifact", "assert_native_is_the_built_artifact.py")


class BuiltArtifactTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def artifact(self, name, content):
        path = self.dir / name
        path.write_bytes(content)
        return path

    def test_it_finds_the_built_artifact_that_matches(self):
        """The positive control, over the *same* population as the refusal below.

        ``matching`` returns ``None`` for an artifact no build produced, and an
        empty set is also what a lookup that matched nothing at all would return,
        so this row names the hit first: the identical file is found, and it is
        the identical one rather than the merely-first one.
        """
        built = [
            self.artifact("lib_native.a", b"one build"),
            self.artifact("lib_native.b", b"another build"),
        ]
        self.assertEqual(artifact_check.matching(built[0], built), built[0])
        self.assertEqual(artifact_check.matching(built[1], built), built[1])
        # And it is *content* that matches, not the name: `maturin develop` copies
        # the library to a differently-named file, so the gate depends on this.
        copy = self.artifact("_native.cpython-314-darwin.so", b"one build")
        self.assertEqual(artifact_check.matching(copy, built), built[0])

    def test_it_refuses_an_artifact_no_build_produced(self):
        """The negative branch, driven rather than trusted.

        Two refusals. One is a foreign build that differs from every candidate.
        The other is the case a *size* comparison admits and a digest refuses: the
        same length as a built file with different bytes. Without this row the
        script's failure path would be a branch nothing had ever executed.
        """
        built = [
            self.artifact("lib_native.a", b"one build"),
            self.artifact("lib_native.b", b"another build"),
        ]
        foreign = self.artifact("_native.abi.so", b"a foreign build!")
        self.assertIsNone(artifact_check.matching(foreign, built))

        same_length = self.artifact("_native.other.so", b"ONE BUILD")
        self.assertEqual(len(same_length.read_bytes()), len(built[0].read_bytes()))
        self.assertIsNone(artifact_check.matching(same_length, built))

    def test_release_artifacts_reads_the_profile_and_skips_dep_files(self):
        """The glob and its one exclusion, on a fabricated profile directory.

        cargo writes a ``.d`` dep file beside each library, so a bare
        ``lib_native.*`` glob would hand the digest a text file and report a
        mismatch on a tree that built correctly.
        """
        release = self.dir / "release"
        release.mkdir()
        lib = self.artifact("release/lib_native.dylib", b"the library")
        self.artifact("release/lib_native.d", b"target/x: src/lib.rs")
        self.artifact("release/other_thing.rlib", b"not the extension")
        self.assertEqual(artifact_check.release_artifacts(release), [lib])


if __name__ == "__main__":
    unittest.main()
