# M8 measurements

Re-runnable instruments behind `.review/m8-recon.md`. Every figure in that
document comes from one of these, run 2026-10-09 against `.django/` @ 6.0.9.

| script | the claim it measures |
|---|---|
| `m8_measure_baseline.py` | `_native`'s public surface and which of §5.2's six seam names exist on it; `resolve` per value protocol (incl. the `__proxy__` row); `alters_data`/`do_not_call_in_templates` off the resolved object vs off the owning class; the 7 `{{ x }}` and 6 `{{ x.attr }}` render rows |
| `m8_measure_escape.py` | which object `defaultfilters.escape` is vs which one the registry holds; the six stock-vs-ours escape rows **through the registry** |
| `m8_measure_filter_names.py` | the six registered filter names shadowed by a different module attribute, and the one name with no module attribute at all (`slice`) |
| `m8_measure_corpus.py` | the ten M8-relevant `.django` modules: tests run/pass; and whether the `CallArm::Call` stub is reachable through a render at all |

Run each as:

```bash
.venv/bin/python -I .review/m8-measurements/<name>.py
```

Each script inserts its own `sys.path` entries, because `-I` implies `-E` and
the venv does not have Django on the path — `.django/` is added at runtime.

Two harness notes, both learned by getting them wrong first:

- **Configure a `TEMPLATES` backend** in `settings.configure(...)`, or any test
  that calls `Engine.get_default()` errors with `ImproperlyConfigured`. The
  first run of `m8_measure_corpus.py` reported that as `1 error`; it is the
  harness, not the corpus.
- **Reach a filter through `register.filters[name]`, never through
  `defaultfilters.<name>`.** Six names differ, and `escape` is one of them:
  `defaultfilters.escape` is `django.utils.html.escape`, while `|escape` in a
  template is `escape_filter` = `conditional_escape`. This produced a false
  divergence on the first pass of `m8_measure_escape.py`.
