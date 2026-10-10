# django-rust-templates

Django's template engine, reimplemented in Rust, as an ordinary template backend.

**Experimental. All code in this repository was written by AI.**

- Django's own `tests/template_tests/` passes: `Ran 1547 tests … OK (skipped=6)`.
  The six skips are Django's, not ours. The copy this runs against is generated
  from the pinned checkout by `scripts/gen_django_suite.py`.
- **Slower than stock Django**: 1.3× to 1.8× on the four workloads in
  `scripts/bench.py` — 1.34× pre-compiled render, 1.35× parse-and-render, 1.41× a
  ~200-node template, 1.82× a one-variable one. It renders the same templates
  under both engines and prints a hash of each page, so the comparison is like for
  like. Measured 2026-10-09 against Django 6.0.9, **with the extension built
  `--release`** — `pip install` builds that profile, but `maturin develop` installs
  an unoptimised one that is about three times slower, which is what the harness
  now prints. `scripts/check.sh` builds that profile itself before running the
  suites, so the gate's numbers are about the source and not about whatever
  extension happened to be installed. Correctness has been the priority; the
  number is here rather than only a flattering one.
- No wheel is published, so installing builds the Rust extension from source. You
  need a Rust toolchain — [rustup](https://rustup.rs).
- Requires Python 3.12 or later — only 3.14.4 has actually been run — and Django
  6.0.x. The backend refuses any Django outside 6.0.x; see `allow_django_mismatch`
  below.

## Install

```console
pip install "git+https://github.com/ipmb/django-rust-templates"
uv add "git+https://github.com/ipmb/django-rust-templates"
```

From a clone, `pip install .` works too.

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

`NAME` is optional. `EngineHandler` derives an entry's alias from the
second-to-last segment of the backend path — `django.template.backends.jinja2.
Jinja2` becomes `jinja2`, which is why templates usually go unnamed — and this
path's segment is `rust`, so an unnamed entry is reachable as `engines["rust"]`.
Add `"NAME"` only to choose a different alias.

### `OPTIONS`

Everything Django's own backend accepts, plus:

- `allow_django_mismatch` — the backend refuses to run against a Django it was not
  transcribed from, because its correctness rests on reproducing one version's
  behaviour and every test here was run against that one. Set this to `True` if you
  mean to try it against another Django anyway. Note that `pip` refuses the pairing
  first, from the declared `Django>=6.0,<6.1`, so this is for a resolver that has
  been bypassed (`--no-deps`, a vendored wheel, another installer).

## Development

`scripts/check.sh` is the gate; `--fast` is the loop. Both need the pinned Django
checkout, which `scripts/fetch_django.sh` fetches into the gitignored `.django/`.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
