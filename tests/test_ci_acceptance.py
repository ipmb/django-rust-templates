"""Matrix acceptance still rejects lost tests and changed skip identities."""

import unittest
from unittest.mock import patch

from scripts.assert_suite_outcome import outcome
from scripts.ci.django_acceptance import expected_skips
from tests.test_suite_outcome import transcript


class MatrixAcceptanceTests(unittest.TestCase):
    def test_timezone_skips_match_upstreams_platform_capability(self):
        with patch("scripts.ci.django_acceptance.TZ_SUPPORT", True):
            supported = expected_skips()
        with patch("scripts.ci.django_acceptance.TZ_SUPPORT", False):
            unsupported = expected_skips()
        self.assertEqual(
            unsupported - supported,
            {
                "template_tests.filter_tests.test_timesince.TimesinceTests.test_timesince12",
                "template_tests.filter_tests.test_timesince.TimesinceTests.test_timesince13",
                "template_tests.filter_tests.test_timeuntil.TimeuntilTests.test_timeuntil10",
                "template_tests.filter_tests.test_timeuntil.TimeuntilTests.test_timeuntil11",
            },
        )

    def test_windows_only_adds_the_upstream_permission_skip(self):
        with patch("scripts.ci.django_acceptance.sys.platform", "linux"):
            linux = expected_skips()
        with patch("scripts.ci.django_acceptance.sys.platform", "win32"):
            windows = expected_skips()
        self.assertEqual(
            windows - linux,
            {
                "template_tests.test_loaders.FileSystemLoaderTests.test_permissions_error"
            },
        )

    def test_same_skip_count_with_a_different_identity_is_refused(self):
        skips = sorted(expected_skips())
        healthy = transcript(summary=f"OK (skipped={len(skips)})", skips=skips)
        self.assertEqual(outcome(healthy).refusals(expected_skips=set(skips)), [])
        replaced = skips[:-1] + [
            "template_tests.syntax_tests.test_numpy.NumpyTests.test_missing"
        ]
        changed = transcript(summary=f"OK (skipped={len(skips)})", skips=replaced)
        self.assertTrue(outcome(changed).refusals(expected_skips=set(skips)))

    def test_missing_test_is_refused_under_matrix_skip_rules(self):
        skips = expected_skips()
        changed = transcript(
            ran=1546, summary=f"OK (skipped={len(skips)})", skips=skips
        )
        self.assertTrue(outcome(changed).refusals(expected_skips=skips))
