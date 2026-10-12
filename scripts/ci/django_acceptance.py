"""Run the pinned suite, checking its count and version/platform-specific skips."""

import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from assert_suite_outcome import DECLARED_RAN, DECLARED_SKIPS, outcome  # noqa: E402

TZ_SUPPORT = hasattr(time, "tzset")


def expected_skips():
    skips = set(DECLARED_SKIPS)
    # This is upstream's normcase predicate, including its behavior on macOS.
    if os.path.normcase("/TEST") == os.path.normpath("/test"):
        skips.remove(
            "template_tests.test_loaders.FileSystemLoaderTests.test_case_sensitivity"
        )
    if sys.platform == "win32":
        skips.add(
            "template_tests.test_loaders.FileSystemLoaderTests.test_permissions_error"
        )
    if not TZ_SUPPORT:
        skips.update(
            {
                "template_tests.filter_tests.test_timesince.TimesinceTests.test_timesince12",
                "template_tests.filter_tests.test_timesince.TimesinceTests.test_timesince13",
                "template_tests.filter_tests.test_timeuntil.TimeuntilTests.test_timeuntil10",
                "template_tests.filter_tests.test_timeuntil.TimeuntilTests.test_timeuntil11",
            }
        )
    if sys.version_info < (3, 14):
        skips.update(
            {
                "template_tests.test_library.InclusionTagRegistrationTests.test_inclusion_tag_deferred_annotation",
                "template_tests.test_library.SimpleBlockTagRegistrationTests.test_simple_block_tag_deferred_annotation",
                "template_tests.test_library.SimpleTagRegistrationTests.test_tag_deferred_annotation",
                "template_tests.test_parser.ParserTests.test_filter_deferred_annotation",
                "template_tests.tests.DebugTemplateTests.test_callable_uses_deferred_annotations",
                "template_tests.tests.TemplateTests.test_callable_uses_deferred_annotations",
            }
        )
    return skips


def main():
    run = subprocess.run(
        [sys.executable, "-I", str(ROOT / "django_suite" / "run.py"), "-v", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    Path("django-suite.log").write_text(run.stdout, encoding="utf-8")
    report = outcome(run.stdout)
    skips = expected_skips()

    # Some early 3.12 releases excluded decorator skips from testsRun. Measure
    # unittest's reporting rule while still requiring all 1547 discovered tests.
    class SkipProbe(unittest.TestCase):
        @unittest.skip("counting probe")
        def runTest(self):
            pass

    probe = unittest.TestResult()
    SkipProbe().run(probe)
    expected_ran = DECLARED_RAN if probe.testsRun else DECLARED_RAN - len(skips)
    problems = report.refusals(expected_ran=expected_ran, expected_skips=skips)
    if f"Found {DECLARED_RAN} test(s)." not in run.stdout:
        problems.append(f"Expected {DECLARED_RAN} discovered Django tests")
    if run.returncode or problems:
        print(run.stdout)
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"{report.ran} tests: {report.summary}; every skip checked by identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
