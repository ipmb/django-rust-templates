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

## Preliminary releases

Run **Release artifacts** manually in Actions to verify release builds without
publishing. A pushed `v*` tag runs CI, builds and tests CPython 3.12–3.14 wheels
for Linux x86_64/aarch64, macOS Intel/Apple Silicon, and Windows x86_64, and
builds and installs the source distribution. After all checks pass, it creates
a GitHub prerelease with those artifacts. Linux wheels target glibc; musl,
free-threaded Python, and PyPy are outside the initial release matrix.

The tag version must match `workspace.package.version` in `Cargo.toml` (for
example, `v6.0.9-alpha1` for `6.0.9-alpha1`; Python normalizes this to
`6.0.9a1`). Change that version and refresh `Cargo.lock`
before tagging another release. The workflow checks every artifact's version
before publishing. No PyPI credentials or publish step are used.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
