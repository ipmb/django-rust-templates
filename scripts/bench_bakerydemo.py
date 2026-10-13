#!/usr/bin/env python3
"""Compare installed backends on real bakerydemo pages, in separate processes.

Usage: python scripts/bench_bakerydemo.py /path/to/bakerydemo
Use --rust-pythonpath to measure an extracted wheel without installing it.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

URLS = (
    "/",
    "/blog/",
    "/blog/wild-yeast/",
    "/breads/",
    "/breads/bagel/",
    "/locations/",
    "/locations/hof/",
    "/recipes/",
    "/recipes/hot-cross-bun/",
    "/gallery/",
    "/about/",
)


def worker(args):
    import django

    django.setup()
    from django.template import engines
    from django.test import Client

    client = Client()
    jobs, outputs = [], []
    for url in URLS:
        response = client.get(url, secure=True, follow=True)
        if response.status_code != 200:
            raise RuntimeError(f"{url}: HTTP {response.status_code}")
        name = response.template_name
        name = name if isinstance(name, str) else name[0]
        template = engines.all()[0].get_template(name)
        context, request = response.context_data, response.wsgi_request
        if args.mode == "render":

            def run(template=template, context=context, request=request):
                return template.render(context, request).encode()
        else:

            def run(url=url):
                result = client.get(url, secure=True, follow=True)
                if result.status_code != 200:
                    raise RuntimeError(f"{url}: HTTP {result.status_code}")
                return result.content

        for _ in range(args.warmup):
            run()
        outputs.append(hashlib.sha256(run()).hexdigest())
        jobs.append(run)
    native = None
    if os.environ["DRT_BENCH_BACKEND"] == "rust":
        from django_rust_templates import _native

        native = _native.__file__
    print(
        json.dumps(
            {
                "hashes": outputs,
                "python": sys.version,
                "django": django.get_version(),
                "native": native,
                "native_sha256": hashlib.sha256(Path(native).read_bytes()).hexdigest()
                if native
                else None,
            }
        ),
        flush=True,
    )
    for line in sys.stdin:
        start = time.perf_counter_ns()
        jobs[int(line)]()
        print(time.perf_counter_ns() - start, flush=True)


def benchmark(args):
    project = args.bakerydemo.resolve()
    interpreter = (args.python or project / ".venv/bin/python").absolute()
    processes, metadata = {}, {}
    with tempfile.TemporaryDirectory(prefix="drt-bakery-") as temporary:
        Path(temporary, "drt_bakery_settings.py").write_text(
            "import os\n"
            "from bakerydemo.settings.production import *\n"
            "assert DEBUG is False\n"
            "TEMPLATES[0]['BACKEND'] = {\n"
            " 'django': 'django.template.backends.django.DjangoTemplates',\n"
            " 'rust': 'django_rust_templates.rust.RustTemplates',\n"
            "}[os.environ['DRT_BENCH_BACKEND']]\n"
            "ALLOWED_HOSTS = [*ALLOWED_HOSTS, 'testserver']\n"
        )
        try:
            for backend in ("django", "rust"):
                paths = [temporary, str(project)]
                if backend == "rust" and args.rust_pythonpath:
                    paths.insert(0, str(args.rust_pythonpath.resolve()))
                env = dict(
                    os.environ,
                    DRT_BENCH_WORKER="1",
                    DRT_BENCH_BACKEND=backend,
                    DJANGO_SETTINGS_MODULE="drt_bakery_settings",
                    PYTHONPATH=os.pathsep.join(paths),
                )
                env.setdefault("DJANGO_SECRET_KEY", "local-benchmark")
                process = subprocess.Popen(
                    [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]],
                    cwd=project,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
                processes[backend] = process
                ready = process.stdout.readline()
                if not ready:
                    raise RuntimeError(
                        f"{backend} worker exited during setup (see stderr)"
                    )
                metadata[backend] = json.loads(ready)
            if metadata["django"]["hashes"] != metadata["rust"]["hashes"]:
                raise RuntimeError("Django and Rust output hashes differ")
            rows = []
            for index, url in enumerate(URLS):
                samples = {backend: [] for backend in processes}
                for sample in range(args.samples):
                    order = (
                        ("django", "rust") if sample % 2 == 0 else ("rust", "django")
                    )
                    for backend in order:
                        process = processes[backend]
                        process.stdin.write(f"{index}\n")
                        process.stdin.flush()
                        samples[backend].append(int(process.stdout.readline()) / 1e6)
                django_ms, rust_ms = (
                    statistics.median(samples[backend]) for backend in processes
                )
                ratio = rust_ms / django_ms
                rows.append(
                    dict(
                        url=url,
                        django_ms=django_ms,
                        rust_ms=rust_ms,
                        ratio=ratio,
                        samples_ms=samples,
                    )
                )
                print(
                    f"{url:26} Django {django_ms:8.3f} ms  Rust {rust_ms:8.3f} ms  {ratio:.4f}x",
                    flush=True,
                )
            ratio = statistics.geometric_mean(row["ratio"] for row in rows)
            print(
                f"Geometric mean Rust/Django: {ratio:.4f}x; {len(URLS)} output hashes match"
            )
            if args.output:
                args.output.write_text(
                    json.dumps(
                        dict(mode=args.mode, metadata=metadata, ratio=ratio, rows=rows),
                        indent=2,
                    )
                    + "\n"
                )
        finally:
            for process in processes.values():
                process.stdin.close()
            for process in processes.values():
                process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bakerydemo", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--rust-pythonpath", type=Path)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--mode", choices=("render", "request"), default="render")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 1 or args.warmup < 0:
        parser.error("samples must be positive and warmup nonnegative")
    if os.environ.get("DRT_BENCH_WORKER"):
        worker(args)
    else:
        benchmark(args)
