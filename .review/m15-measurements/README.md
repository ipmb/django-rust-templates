# M15 measurements

Each script here is re-runnable and mapped to the claim it carries. Run them
with the pinned interpreter (`.venv`, CPython 3.14.4) against the pinned tree
(`.django/` @ 6.0.9); the shimmed ones say so in their own docstring.

| script | the claim it carries |
|---|---|
| `m15_measure_filter_flag_carriers.py` | `do_not_call_in_templates` and `alters_data` have **no carrier** among the 111 callables the template layer's registries hold, so M15's sixth control (`tests/test_filter_inventory.py`'s flag-inventory row) cannot be made to fire and stays outside the control set. `is_safe` is the positive control: **present on 57 of the 111 and truthy on only 36**, so the control is `hasattr`-shaped, matching the row's subject. Note the trap the script prints around: 57 is *also* the size of `defaultfilters.register.filters`, and the two sets differ — 57 present = 55 of the builtins plus both of `l10n`'s. |

`m15_measure_filter_flag_carriers.py` runs **both halves** — the pinned tree and
the tree the row actually executes in — because the count is about a registry the
shim replaces. Both give 111 and no carriers; the script prints which tree it
read, so the two transcripts cannot be confused.
