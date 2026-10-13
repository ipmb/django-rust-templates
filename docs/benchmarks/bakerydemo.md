# Benchmarking Wagtail Bakerydemo

[Bakerydemo](https://github.com/wagtail/bakerydemo) exercises the template engine
inside a real Wagtail application. Its pages combine inheritance, includes, rich
content, navigation, images, and third-party template tags. This complements the
small filter/tag examples and synthetic full-page templates in the benchmark
report: it measures how much an engine change helps when rendering is part of a
larger application.

## What the benchmark measures

[`scripts/bench_bakerydemo.py`](../../scripts/bench_bakerydemo.py) compares stock
Django with the Rust backend across eleven pages: the home page, blog listings
and articles, bread listings and detail pages, locations, recipes, gallery, and
about page.

Each backend runs in its own persistent process, using Bakerydemo's production
settings and a single configured template backend. This keeps Wagtail's internal
template loads on the engine being measured. Templates and their dependencies
are warmed before timed samples, and backend order alternates between samples.
The same page URLs and demo database are used for both backends.

The harness has two modes:

- **Warm template render** (`--mode render`, the default): make an initial real
  request to obtain the template, context, and request object, then repeatedly
  render that template. Timings include UTF-8 encoding, context processors
  invoked during rendering, and work initiated by template tags or lazy context
  values. They exclude the initial view execution and template loading.
- **Warm request** (`--mode request`): repeatedly request each page through
  Django's test client. Timings include views, middleware, database work, and
  rendering. They exclude network transport and a browser's work.

The harness records individual samples, per-page medians, Python and Django
versions, the imported native extension's path and SHA-256, and output hashes.
After warmup, it validates a render of each page and refuses the comparison if
those output hashes differ between backends.
Its summary is the geometric mean of the per-page Rust/Django median ratios,
giving each page equal weight. A ratio below one means less time; above one
means more time.

These are warm measurements. They do not measure startup, cold template
compilation, or user-perceived page load time. Reusing render contexts and warming
pages can also warm lazy querysets and application caches; the request mode
captures more of the repeated request work.

## Why gains can be smaller than in the other examples

The engine accelerates part of the work on a Bakerydemo page. The small examples
spend a much larger share of their time in template parsing, variable resolution,
filters, and walking ordinary template nodes. Bakerydemo also spends time doing
work that replacing the template engine does not accelerate:

- **Database access.** Navigation, related page lookups, and lazy querysets can
  issue ORM queries during rendering. Request mode also includes queries made
  by views. SQL execution, database latency, and constructing model objects
  remain costs of the application.
- **Custom Python tags.** Wagtail and project-specific tags execute their Python
  callbacks. Their work can include rich-content processing, image rendition
  lookups, navigation building, and assembling inclusion-tag contexts. Rust can
  render eligible templates they invoke, but the callback's own work remains
  in Python.
- **Python object behavior.** Model properties, methods, custom lookup behavior,
  and formatting still need to preserve Django's semantics. Accelerated lookup
  cannot remove the cost of a property or method it calls.
- **Work around the template.** Context processors, response encoding, and, in
  request mode, middleware and view execution contribute to the measured total.

As these costs take a larger share of a page's time, even a substantial reduction
in the engine's own work produces a smaller reduction in the total. Pages differ
in that mix, so a simple table template and a Wagtail page need not show similar
improvements. Profile the application before interpreting a small overall change
as evidence that the engine's ordinary nodes did not get faster.

## Run the benchmark

The Bakerydemo checkout needs its dependencies, demo database, production
settings, and the Rust backend installed in its environment. Use a release build
of the extension when evaluating performance. The harness uses Bakerydemo's
`.venv/bin/python` by default; `--python` selects another interpreter.

```sh
python scripts/bench_bakerydemo.py ../bakerydemo --samples 250 --warmup 10 --output render.json
python scripts/bench_bakerydemo.py ../bakerydemo --mode request --samples 250 --warmup 10 --output request.json
```

To measure a wheel without changing Bakerydemo's installation, extract it into a
temporary directory and pass that directory with `--rust-pythonpath`. Check the
recorded native path and hash to confirm which artifact ran.

Keep the machine, database, application checkout, interpreter, settings, and
sample count consistent between runs. Repeat measurements to see whether a small
change persists, and inspect the per-page samples rather than relying only on the
aggregate ratio.

## Recorded data in the public report

The public report includes separately recorded Bakerydemo measurements alongside
the fresh synthetic benchmarks. Those measurements retain their own date,
interpreter version, application commit, and artifact identity; generating a new
report does not rerun Bakerydemo. The report labels them as a recorded run and
keeps their methodology separate. Historical data lives in dated JSON files in
this directory; this document describes the benchmark rather than a particular
run's results.
