"""Format bench.py measurements as GitHub Markdown tables."""

import argparse
import json
import os
from pathlib import Path


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(payload, commit="", ref=""):
    lines = ["# Template benchmarks", "", "| Parameter | Value |", "| --- | --- |"]
    metadata = {"commit": commit, "ref": ref, **payload["meta"], **payload["args"]}
    metadata["native"] = payload["native"]
    metadata["command"] = payload["command"]
    for key, value in metadata.items():
        if value != "":
            lines.append(f"| {cell(key)} | {cell(value)} |")
    engines = payload["ran"]
    records = {(r["key"], r["mode"], r["engine"]): r for r in payload["records"]}
    workloads = list(dict.fromkeys(r["key"] for r in payload["records"]))
    for mode in payload["modes"]:
        lines.extend(
            [
                "",
                f"## {cell({'parse': 'Parse and render', 'render': 'Render', 'compile': 'Compile'}[mode])}",
                "",
                "| Workload | "
                + " | ".join(f"{cell(e)} µs/call (best–worst)" for e in engines)
                + " |",
                "| --- | " + " | ".join("---:" for _ in engines) + " |",
            ]
        )
        for workload in workloads:
            timings = []
            for engine in engines:
                record = records.get((workload, mode, engine))
                timings.append(
                    f"{record['best_us']:.3f}–{record['worst_us']:.3f}"
                    if record
                    else "—"
                )
            lines.append(f"| {cell(workload)} | " + " | ".join(timings) + " |")
    lines.extend(
        [
            "",
            "## Empty template",
            "",
            "| Mode | " + " | ".join(f"{cell(e)} µs/call" for e in engines) + " |",
            "| --- | " + " | ".join("---:" for _ in engines) + " |",
        ]
    )
    for mode in payload["modes"]:
        timings = [f"{payload['floor'][mode][engine]:.3f}" for engine in engines]
        lines.append(f"| {cell(mode)} | " + " | ".join(timings) + " |")
    lines.extend(
        [
            "",
            "## Measurements",
            "",
            "| Workload | Mode | Engine | Iterations/sample | Samples | Output bytes | Output SHA-256 |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for record in payload["records"]:
        fields = ("key", "mode", "engine", "iters", "repeats", "bytes", "sha256")
        lines.append("| " + " | ".join(cell(record[field]) for field in fields) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        render(
            json.loads(args.payload.read_text(encoding="utf-8")),
            commit=os.environ.get("BENCHMARK_COMMIT", ""),
            ref=os.environ.get("BENCHMARK_REF", ""),
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
