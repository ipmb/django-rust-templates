# django-rust-templates

Django's template engine, reimplemented in Rust.

**Experimental. All code in this repository was written by AI.**

- Django's own `tests/template_tests/` pass. The copy this runs against is generated
  from the pinned checkout by `scripts/gen_django_suite.py`.
- Tagged GitHub prereleases provide wheels; source installs need a Rust
  toolchain — [rustup](https://rustup.rs).
- Supports CPython 3.12, 3.13, and 3.14 with Django 6.0.9 or later in the
  6.0 series. The backend refuses Django outside this range; see `allow_django_mismatch`
  below.

## Install

```console
pip install "git+https://github.com/ipmb/django-rust-templates"
uv add "git+https://github.com/ipmb/django-rust-templates"
```

From a clone, `pip install .` works too.

To install a release wheel, find the correct wheel for your Python and platform from
[GitHub Releases](https://github.com/ipmb/django-rust-templates/releases), then
run `pip install https://github.com/ipmb/django-rust-templates/releases/download/...`.
No packages are published to PyPI yet.

## Use

Point `BACKEND` at the backend class, the way you would any other:

```python
TEMPLATES = [
    {
        "BACKEND": "django_rust_templates.rust.RustTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [...]},
    }
]
```

### `OPTIONS`

Everything Django's own backend accepts, plus:

- `allow_django_mismatch` — the backend refuses to run against a Django it was not
  transcribed from, because its correctness rests on reproducing one version's
  behaviour and every test here was run against that one. Set this to `True` if you
  mean to try it against another Django anyway. Note that `pip` refuses the pairing
  first, from the declared `Django>=6.0.9,<6.1`, so this is for a resolver that has
  been bypassed (`--no-deps`, a vendored wheel, another installer).

### Rendering that does not go through `TEMPLATES`

Naming the backend is not quite the whole of it. Three routes leave this engine
without raising anything, so the backend reports the two it can see as system
checks:

- **Form widgets.** `FORM_RENDERER` defaults to
  `django.forms.renderers.DjangoTemplates`, which builds a private template engine
  of its own; `TEMPLATES` never reaches it, so every widget on every page renders
  on Django's engine whatever backend you configure. To render widgets through
  this one:

  ```python
  FORM_RENDERER = "django.forms.renderers.TemplatesSetting"
  INSTALLED_APPS += ["django.forms"]
  ```

  Reported as `django_rust_templates.W002`. It is a warning rather than advice to
  always change it: the setting is global rather than per-backend, so a project
  running two engines cannot satisfy both.

- **Backend order.** `render_to_string()`, `get_template()` and
  `select_template()` default to `using=None` and resolve to the *first*
  `TEMPLATES` entry. Libraries render their own sub-templates that way, so a
  second entry ahead of this one quietly keeps them on the other engine. Reported
  as `django_rust_templates.W001`.

- **Loaders.** A loader builds the `Template`, so one that builds Django's gives
  you Django's engine. Entries naming Django's own loader classes are substituted
  for this package's transcriptions of them, and a third-party loader that only
  locates templates is adapted automatically; one that overrides `get_template()`
  is refused by name with `ImproperlyConfigured`, because what it builds is its
  own business. Subclass
  `django_rust_templates.engine.loaders.base.Loader` to take that on yourself.

## Development

`scripts/check.sh` is the gate; `--fast` is the loop. Both need the pinned Django
checkout, which `scripts/fetch_django.sh` fetches into the gitignored `.django/`.

GitHub Actions verifies the pinned checkout and regenerated suite, runs Rust
tests, rustfmt, Clippy, Python correctness lint, and tests all three Python
versions against Django 6.0.9 and the latest 6.0 patch release. NumPy is a test
dependency so Django's array-indexing tests run. Python formatting is enforced
for CI helpers; generated transcriptions retain their upstream formatting.

Python 3.12/3.13 use Unicode 15.0/15.1 tables; Python 3.14 uses Unicode 16.
The native parser, numeric literals, and uppercase filters select the matching
tables when the extension loads. Exhaustive parity tests check these tables.

The **Benchmarks** Actions workflow runs on pushes to `main`, pull requests,
manual runs, and through the report publishing workflow. Its job summary
contains compile, render, and parse-and-render timings for Django, this engine,
and Jinja2, plus measurement parameters and output hashes. JSON results, HTML, and the Markdown summary
are saved as workflow artifacts.

The public [benchmark report](https://ipmb.github.io/django-rust-templates/)
is regenerated and published to GitHub Pages after each successful tagged release.
It compares full-page templates, every default and standard-library filter/tag,
and a separately recorded Wagtail Bakerydemo run. The report is rendered by
nanodjango using this package's backend.

To publish without a release, run **Publish benchmark report** from the Actions
tab on `main` (or an existing `v*` tag). This generates fresh measurements and
deploys the HTML and JSON results. It can also be started from the CLI:

```sh
gh workflow run publish-benchmarks.yml --ref main
```

Install its optional dependencies outside the test environment:

```sh
python -m pip install --target .bench-deps -r scripts/bench-report-requirements.txt
.venv/bin/python -I scripts/bench.py --json results.json --html docs/bench.html
```

Preview or export an existing JSON results file without rerunning measurements:

```sh
BENCHMARK_RESULTS=results.json .venv/bin/python -I scripts/bench_site.py
BENCHMARK_RESULTS=results.json .venv/bin/python -I scripts/bench_site.py --export docs/bench.html
```

The preview serves at `127.0.0.1:8000`; `--bind` changes its address. Results are
read on each request. `BENCHMARK_BAKERYDEMO` selects the recorded Bakerydemo JSON
for a file that does not already include it; `bench.py --bakerydemo` selects it
when creating new results. Full timing data remains available in the JSON download.

## Preliminary releases

Run **Release artifacts** manually in Actions to verify release builds without
publishing. A pushed `v*` tag runs CI, builds and tests CPython 3.12–3.14 wheels
for Linux x86_64/aarch64, macOS Intel/Apple Silicon, and Windows x86_64, and
builds and installs the source distribution. After all checks pass, it creates
a GitHub prerelease with those artifacts and publishes a freshly generated
benchmark report to GitHub Pages. Manual release builds on branches do not
publish; use **Publish benchmark report** to update the site independently.
The Bakerydemo section retains its separately recorded measurements; releases
rerun the synthetic corpus. Linux wheels target glibc; musl, free-threaded Python,
and PyPy are outside the initial release matrix.

The tag version must match `workspace.package.version` in `Cargo.toml` (for
example, `v6.0.9-alpha1` for `6.0.9-alpha1`; Python normalizes this to
`6.0.9a1`). Change that version and refresh `Cargo.lock`
before tagging another release. The workflow checks every artifact's version
before publishing. No PyPI credentials or publish step are used.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
