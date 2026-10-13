"""Presentation arithmetic, inventory completeness, and a real Rust-backed export."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bench_report


def payload():
    records = []
    for key, family, stock, rust in [
        ("real-world/normal", "real-world", 100, 80),
        ("filter/upper", "filters", 10, 12),
    ]:
        for engine, time in [("django", stock), ("rust", rust)]:
            records.append(
                {
                    "key": key,
                    "family": family,
                    "mode": "render",
                    "engine": engine,
                    "best_us": time,
                    "sha256": "same",
                    "summary": "<unsafe example>",
                }
            )
    return {
        "records": records,
        "modes": ["render"],
        "ran": ["django", "rust"],
        "meta": {
            "django": "6.0.9",
            "cpython": "3.14",
            "platform": "test",
            "date": "2026-10-12",
        },
        "args": {"repeats": 7, "target": 0.05},
        "native": "test-artifact",
        "workloads": {
            "real-world/normal": {
                "source": "{{ title }}",
                "url": "https://example.com/template.html",
            }
        },
    }


class BenchmarkPresentationTests(unittest.TestCase):
    def test_time_reduction_and_regression_have_correct_direction(self):
        self.assertEqual(bench_report.comparison(100, 80)["text"], "20.0% less time")
        self.assertEqual(bench_report.comparison(100, 120)["text"], "20.0% more time")
        self.assertEqual(bench_report.comparison(100, 100)["text"], "About the same")

    def test_page_summary_excludes_filter_rows_and_preserves_source_links(self):
        context = bench_report.context(payload())
        self.assertEqual(context["summary"]["text"], "20.0% less time")
        self.assertEqual(
            context["modes"][0]["pages"][0]["url"], "https://example.com/template.html"
        )
        self.assertEqual(
            context["modes"][0]["filters"][0]["difference"]["class"], "slower"
        )
        changed = payload()
        changed["records"][1]["sha256"] = "different"
        self.assertFalse(bench_report.context(changed)["all_match"])

    def test_page_charts_share_a_linear_scale_and_small_pairs_use_their_own(self):
        rows = [
            {"timings": [("django", 100), ("rust", 50)]},
            {"timings": [("django", 20), ("rust", 10)]},
        ]
        self.assertEqual(bench_report.charts(rows, shared=True), "100.00")
        self.assertEqual(rows[1]["bars"][0]["width"], "20.000")
        bench_report.charts(rows)
        self.assertEqual(rows[1]["bars"][0]["width"], "100.000")
        self.assertEqual(rows[1]["bars"][1]["width"], "50.000")

    def test_every_registered_filter_and_tag_has_a_unique_workload(self):
        import importlib
        import bench_features
        from django.template import defaultfilters, defaulttags, loader_tags

        workloads = bench_features.workloads()
        expected_filters = set(defaultfilters.register.filters)
        expected_tags = set(defaulttags.register.tags) | set(loader_tags.register.tags)
        for name in ("i18n", "l10n", "tz", "static", "cache"):
            register = importlib.import_module(f"django.templatetags.{name}").register
            expected_filters.update(register.filters)
            expected_tags.update(register.tags)
        self.assertEqual(
            {w.key for w in workloads},
            {f"filter/{n}" for n in expected_filters}
            | {f"tag/{n}" for n in expected_tags},
        )
        self.assertEqual(len(workloads), len({w.key for w in workloads}))

    def test_linked_templates_are_the_measured_source(self):
        import bench_corpus

        names = {
            "real-world/normal": "report.html",
            "real-world/complex": "invoice.html",
            "real-world/large": "large.html",
            "django-only/realistic": "translated_report.html",
        }
        for w in bench_corpus.workloads():
            if w.key in names:
                self.assertEqual(
                    w.templates[w.entry],
                    (ROOT / "scripts/bench_templates" / names[w.key]).read_text(),
                )

    def test_export_runs_in_separate_nanodjango_process_and_escapes_examples(self):
        if not (ROOT / ".bench-deps/nanodjango").is_dir():
            self.skipTest(
                "Report dependencies are installed only for the benchmark workflow"
            )
        with tempfile.TemporaryDirectory(prefix="benchmark-report-test-") as directory:
            results = Path(directory) / "results.json"
            output = Path(directory) / "report.html"
            results.write_text(json.dumps(payload()), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(ROOT / "scripts/bench_site.py"),
                    "--export",
                    str(output),
                ],
                env=dict(os.environ, BENCHMARK_RESULTS=str(results)),
                check=True,
            )
            page = output.read_text(encoding="utf-8")
        self.assertIn("Rendered with django-rust-templates + nanodjango.", page)
        self.assertIn("&lt;unsafe example&gt;", page)
        self.assertNotIn("<unsafe example>", page)
        self.assertIn("20.0% less time", page)
        self.assertIn("20.0% more time", page)
        self.assertIn("Recorded run", page)
        self.assertNotIn("micro/variable", page)


if __name__ == "__main__":
    unittest.main()
