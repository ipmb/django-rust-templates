#!/usr/bin/env python
"""Preview/export JSON benchmarks with nanodjango and our Rust template backend.

BENCHMARK_RESULTS=results.json .venv/bin/python -I scripts/bench_site.py
BENCHMARK_RESULTS=results.json .venv/bin/python -I scripts/bench_site.py --export docs/bench.html
BENCHMARK_BAKERYDEMO optionally selects a recorded Bakerydemo JSON file.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append(str(ROOT / ".bench-deps"))

try:
    from nanodjango import Django
except ImportError as error:
    raise SystemExit(
        "Install report dependencies: python -m pip install --target .bench-deps -r scripts/bench-report-requirements.txt"
    ) from error

import bench_report

app = Django(
    DEBUG=False,
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
    SQLITE_DATABASE=Django.SQLITE_MEMORY,
    INSTALLED_APPS=["django.forms"],
    FORM_RENDERER="django.forms.renderers.TemplatesSetting",
    MIDDLEWARE=[],
    TEMPLATES=[
        {
            "NAME": "rust",
            "BACKEND": "django_rust_templates.rust.RustTemplates",
            "DIRS": [str(ROOT / "scripts/templates")],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": []},
        }
    ],
)


def results():
    filename = os.environ.get("BENCHMARK_RESULTS")
    if not filename:
        raise ValueError("Set BENCHMARK_RESULTS to a bench.py JSON results file.")
    payload = json.loads(Path(filename).expanduser().read_text(encoding="utf-8"))
    if "bakerydemo" not in payload:
        path = Path(os.environ.get("BENCHMARK_BAKERYDEMO", str(bench_report.BAKERY)))
        if path.is_file():
            payload["bakerydemo"] = json.loads(path.read_text(encoding="utf-8"))
    return payload


@app.route("/")
def index(request):
    return app.render(
        request, "bench.html", bench_report.context(results()), using="rust"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", type=Path, help="Write a self-contained static HTML report"
    )
    parser.add_argument(
        "--bind", default="127.0.0.1:8000", help="Preview server address"
    )
    args = parser.parse_args()
    if args.export:
        from django.test import RequestFactory

        response = index(RequestFactory().get("/"))
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_bytes(response.content)
    else:
        results()  # Fail early for a missing/invalid input file.
        app.manage(["runserver", args.bind, "--noreload"])
