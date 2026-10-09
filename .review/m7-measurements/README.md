# M7 measurement scripts

The scripts the M7 plan cites as evidence for claims no test carries. They were
written under `/tmp/`, which does not survive, and copied here for that reason:
a recorded divergence whose argument cannot be re-run is one nobody re-examines.

Each is run as

```
.venv/bin/python -I .review/m7-measurements/<name>.py
```

and each inserts its own paths, because `-I` implies `-E`.

| script | the claim it carries | where |
|---|---|---|
| `m7_measureA.py` | the two-clause host predicate, as a six-row matrix — `has_tag` per guard/registry/`loader_tags` combination. **Rows 4 and 5 are measured on the shipping `ShellHost`** (they *are* the shipping predicate); rows 1, 2, 3 and 6 are counterfactuals that turn on the clause Task 4 deleted, so they are modelled and `M7_OWNED_WAS` holds the deleted list verbatim. Re-pointed in Task 7 Step 1: it imported `_M7_OWNED`, so the deletion left it *not running at all* rather than printing a stale figure — and the column it called `defaulttags` had become `django_own_tags()`'s union, which flattened rows 1-3 into row 4 until the pre-widening table was read from `defaulttags.register.tags` directly | plan A2, D3′ |
| `m7_measureB.py` | inbound `must_be_first`: Django's refusal and its message; the `{% block %}` end-tag forms; `render_string`'s signature; **can a `must_be_first` host node ever be *first*?** — no through a `{% load %}` (`contains_nontext` is sticky, `base.py:611-612`), yes as a builtin, with both engines agreeing row for row; and a host tag returning `object()`, where the raising and defaulting spellings of the read first differ | plan A4, Task 5, RF1 |
| `m7_measure_half_fix.py` | Task 4's trap, re-measured: the un-widened `django_own_tags` table, **modelled** by rebinding the module global so the script is re-runnable against the shipping tree. The predicate says `True` for all three names; `{% extends "a" %}{% extends "b" %}` still raises with Django's own text (`do_extends` re-enters `parser.parse()`, `loader_tags.py:307`); **0 of 106 error-corpus rows and 0 of 3 `ORIGIN_ERRORS` rows differ**; and section (4) **runs** the gate file under the rebind rather than restating a count — 8 run, 6 failed, both detectors (the predicate row and the wire row) and nothing else | plan D8/D8′, Task 4 Step 8, Task 6 |
| `m7_measure_origin.py` | `Origin(UNKNOWN_SOURCE).template_name is None`; `construct_relative_path(None, …)` raises rather than parsing on | plan A3, RF2 |
| `m7_measure_nesting.py` | A5's predicate: is any `{% block %}` nested inside a body Django parses? | plan A5 |
| `m7_measure_nest2.py` | the same predicate for `{% extends %}`, and for a library returning an `ExtendsNode` subclass | plan RF3 |
| `m7_measure_pyposix.py` | Task 2's three functions transliterated to Python and diffed against the pinned `posixpath`: 37 table rows, 221,845 generated unary inputs and 800,000 `normpath(join(a, b))` pairs, 0 mismatches | plan Task 2 |
| `m7_measure_gettextlist.py` | which error-corpus row needs `settings` configured and by what reach: 1 of 95 raises `ImproperlyConfigured` (the `{% endblock b %}` row), via `_(", ")` evaluated eagerly at `utils/text.py:301` on a two-element list — **not** via `last_word`'s lazy default, and not only at three elements | `tests/test_parser_errors.py` `setUpClass`, plan Task 1 |
| `m7_measure_seams.py` | Task 7 Step 1's seam inventory, as numbers: the split (`parser.tags` 27 = Rust 25 + host `['load', 'querystring']`, and `registry == the Rust side`), the wire shape per side over six rows, the two names the registry answers for by absence, and the divergence M7 closed (the `{% load %}`ed `block` override). **`_shim.install()` runs last on purpose** — it rewrites the registered callables, so identity reports 0/27 instead of 25/2, which is a property of the measurement rather than of the seam | `.review/m7-seams.md` |
| `m7_measure_child_nodelists.py` | Task 3's walk, in two passes. **Pass 1**: every `Node` subclass, classified by where each `child_nodelists` name resolves — **exactly one is a property, `IfNode`**, and 14 more name an attribute the class never sets, which Django's `if nodelist:` skips. The population is **import-bounded and the script prints both numbers**: 34 with `base`+`defaulttags`+`loader_tags`, **47** once the tag libraries are imported — and `IfNode` is the only property either way. **Pass 2**, the shape pass 1 cannot hold: a class that *stores* a nodelist it does not declare — `SimpleBlockNode` (`self.nodelist`, `child_nodelists = ()` from `SimpleNode`) and `IfNode` from the other side (`conditions_nodelists` stored, `nodelist` declared). Django does not descend into the first | plan Task 3, `desc.rs` |

**A verdict here is dated.** Each is a number only for as long as the tree it
numbered holds still; `.django/` is pinned at 6.0.9 and the figures above are
for that pin.
