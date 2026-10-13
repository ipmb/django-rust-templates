"""Presentation data for the nanodjango benchmark report."""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/ipmb/django-rust-templates"
BAKERY = ROOT / "docs/benchmarks/bakerydemo-2026-10-12.json"
LABELS = {
    "render": "Cached template rendering",
    "parse": "Compile + render",
    "compile": "Template compilation",
}
PAGE_NAMES = {
    "real-world/normal": "Report table",
    "real-world/complex": "Nested invoice",
    "real-world/large": "Large page",
    "django-only/realistic": "Translated report",
}
BAKERY_TEMPLATES = {
    "/": "base/home_page.html",
    "/blog/": "blog/blog_index_page.html",
    "/blog/wild-yeast/": "blog/blog_page.html",
    "/breads/": "breads/breads_index_page.html",
    "/breads/bagel/": "breads/bread_page.html",
    "/locations/": "locations/locations_index_page.html",
    "/locations/hof/": "locations/location_page.html",
    "/recipes/": "recipes/recipe_index_page.html",
    "/recipes/hot-cross-bun/": "recipes/recipe_page.html",
    "/gallery/": "base/gallery_page.html",
    "/about/": "base/standard_page.html",
}


def comparison(django, rust):
    ratio = rust / django
    percent = abs(1 - ratio) * 100
    if percent < 0.05:
        return {"text": "About the same", "class": "same", "percent": 0, "ratio": ratio}
    return {
        "text": f"{percent:.1f}% {'less' if ratio < 1 else 'more'} time",
        "class": "faster" if ratio < 1 else "slower",
        "percent": percent,
        "ratio": ratio,
    }


def charts(rows, unit="µs", shared=False):
    """Linear bars start at zero; page groups share a scale, small pairs do not."""
    maximum = max((value for row in rows for _, value in row["timings"]), default=1)
    for row in rows:
        limit = maximum if shared else max(value for _, value in row["timings"])
        row["bars"] = [
            {
                "engine": {"django": "Django", "rust": "Rust", "jinja2": "Jinja2"}[
                    engine
                ],
                "class": engine,
                "width": f"{value / limit * 100:.3f}",
                "value": f"{value:.2f}",
                "unit": unit,
            }
            for engine, value in row["timings"]
        ]
    return f"{maximum:.2f}"


def context(payload):
    """Prepare presentation data without importing or configuring Django."""
    grouped = {}
    for record in payload["records"]:
        grouped.setdefault((record["mode"], record["key"]), {})[record["engine"]] = (
            record
        )
    modes = []
    for mode in payload["modes"]:
        pages, filters, tags = [], [], []
        for (m, key), engines in grouped.items():
            if m != mode or not {"django", "rust"} <= engines.keys():
                continue
            d, r = engines["django"], engines["rust"]
            family = d["family"]
            if (
                family not in ("real-world", "filters", "tags")
                and key != "django-only/realistic"
            ):
                continue
            name = key.split("/", 1)[-1]
            row = {
                "name": PAGE_NAMES.get(key, name),
                "description": d["summary"],
                "django": f"{d['best_us']:.2f}",
                "rust": f"{r['best_us']:.2f}",
                "jinja": f"{engines['jinja2']['best_us']:.2f}"
                if "jinja2" in engines
                else "—",
                "difference": comparison(d["best_us"], r["best_us"]),
                "note": d.get("note", ""),
                "example": payload.get("workloads", {})
                .get(key, {})
                .get("source", d["summary"]),
                "match": d["sha256"] == r["sha256"],
                "timings": [
                    (engine, engines[engine]["best_us"])
                    for engine in ("django", "rust", "jinja2")
                    if engine in engines
                ],
            }
            source = payload.get("workloads", {}).get(key, {})
            row["url"] = source.get("url", f"{REPO}/blob/main/scripts/bench_corpus.py")
            libraries = source.get("libraries", [])
            documentation = "ref/templates/builtins/"
            if libraries:
                documentation = {
                    "i18n": "topics/i18n/translation/",
                    "l10n": "topics/i18n/formatting/",
                    "tz": "topics/i18n/timezones/",
                    "cache": "topics/cache/",
                    "static": "ref/templates/builtins/",
                }[libraries[0]]
            if family == "filters":
                row["url"] = (
                    f"https://docs.djangoproject.com/en/6.0/{documentation}#{name.replace('_', '-')}"
                )
                filters.append(row)
            elif family == "tags":
                row["url"] = (
                    f"https://docs.djangoproject.com/en/6.0/{documentation}#{name.replace('_', '-')}"
                )
                tags.append(row)
            else:
                pages.append(row)
        chart_max = charts(pages, shared=True)
        charts(filters)
        charts(tags)
        modes.append(
            {
                "key": mode,
                "label": LABELS.get(mode, mode),
                "pages": pages,
                "chart_max": chart_max,
                "filters": sorted(filters, key=lambda r: r["name"]),
                "tags": sorted(tags, key=lambda r: r["name"]),
            }
        )
    render_pages = next((m["pages"] for m in modes if m["key"] == "render"), [])
    ratios = [r["difference"]["ratio"] for r in render_pages]
    summary = (
        comparison(1, math.exp(sum(map(math.log, ratios)) / len(ratios)))
        if ratios
        else None
    )
    bakery = payload.get("bakerydemo")
    bakery_context = None
    if bakery:
        pages = []
        requests = {
            p["url"]: p for p in bakery.get("request_comparison", {}).get("pages", [])
        }
        for page in bakery["pages"]:
            # One hash per page: the recorded run aborts unless both engines agree.
            d, r = page["median_ms"]["django"], page["median_ms"]["candidate"]
            request = requests.get(page["url"])
            pages.append(
                {
                    "name": page["url"],
                    "description": BAKERY_TEMPLATES[page["url"]],
                    "django": f"{d:.3f}",
                    "rust": f"{r:.3f}",
                    "match": True,
                    "timings": [("django", d), ("rust", r)],
                    "difference": comparison(d, r),
                    "request_difference": comparison(
                        request["django_ms"], request["candidate_ms"]
                    )
                    if request
                    else None,
                    "url": f"https://github.com/wagtail/bakerydemo/blob/{bakery['bakerydemo_commit']}/bakerydemo/templates/{BAKERY_TEMPLATES[page['url']]}",
                }
            )
        bakery_context = {
            "pages": pages,
            "chart_max": charts(pages, unit="ms", shared=True),
            "date": bakery["date"],
            "python": bakery["python"],
            "django": bakery["django"],
            "samples": bakery["samples_per_page_per_backend"],
            "summary": comparison(1, bakery["ratios"]["candidate_django"]),
            "request_summary": comparison(
                1, bakery["request_comparison"]["candidate_django_ratio"]
            )
            if bakery.get("request_comparison")
            else None,
            "artifact": bakery["native_sha256"]["candidate"],
            "url": f"{REPO}/blob/main/docs/benchmarks/bakerydemo.md",
        }
    data = base64.b64encode(json.dumps(payload, indent=2).encode()).decode()
    return {
        "modes": modes,
        "summary": summary,
        "page_count": len(render_pages),
        "bakery": bakery_context,
        "meta": payload["meta"],
        "args": payload["args"],
        "jinja": "jinja2" in payload["ran"],
        "download": f"data:application/json;base64,{data}",
        "repo": REPO,
        "native": payload["native"],
        "command": payload.get("command", ""),
        "all_match": all(
            r["sha256"] == grouped[(r["mode"], r["key"])]["django"]["sha256"]
            for r in payload["records"]
            if r["engine"] == "rust"
        ),
        "disagreements": [
            f"{key}: {', '.join(v['differ'])}"
            for key, v in payload.get("verdicts", {}).items()
            if v.get("differ")
        ],
    }
