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
| `m7_measureA.py` | the two-clause host predicate, as a six-row matrix — `has_tag` per guard/registry/`loader_tags` combination, rows 1 and 6′ measured on the shipping `ShellHost` and the rest modelling Task 4's form | plan A2 |
| `m7_measureB.py` | inbound `must_be_first`: Django's refusal and its message; the `{% block %}` end-tag forms; `render_string`'s signature | plan A4, RF1 |
| `m7_measure_origin.py` | `Origin(UNKNOWN_SOURCE).template_name is None`; `construct_relative_path(None, …)` raises rather than parsing on | plan A3, RF2 |
| `m7_measure_nesting.py` | A5's predicate: is any `{% block %}` nested inside a body Django parses? | plan A5 |
| `m7_measure_nest2.py` | the same predicate for `{% extends %}`, and for a library returning an `ExtendsNode` subclass | plan RF3 |
| `m7_measure_pyposix.py` | Task 2's three functions transliterated to Python and diffed against the pinned `posixpath`: 37 table rows, 221,845 generated unary inputs and 800,000 `normpath(join(a, b))` pairs, 0 mismatches | plan Task 2 |
| `m7_measure_gettextlist.py` | which error-corpus row needs `settings` configured and by what reach: 1 of 95 raises `ImproperlyConfigured` (the `{% endblock b %}` row), via `_(", ")` evaluated eagerly at `utils/text.py:301` on a two-element list — **not** via `last_word`'s lazy default, and not only at three elements | `tests/test_parser_errors.py` `setUpClass`, plan Task 1 |
| `m7_measure_child_nodelists.py` | Task 3's walk, in two passes. **Pass 1**: every `Node` subclass, classified by where each `child_nodelists` name resolves — **exactly one is a property, `IfNode`**, and 14 more name an attribute the class never sets, which Django's `if nodelist:` skips. The population is **import-bounded and the script prints both numbers**: 34 with `base`+`defaulttags`+`loader_tags`, **47** once the tag libraries are imported — and `IfNode` is the only property either way. **Pass 2**, the shape pass 1 cannot hold: a class that *stores* a nodelist it does not declare — `SimpleBlockNode` (`self.nodelist`, `child_nodelists = ()` from `SimpleNode`) and `IfNode` from the other side (`conditions_nodelists` stored, `nodelist` declared). Django does not descend into the first | plan Task 3, `desc.rs` |

**A verdict here is dated.** Each is a number only for as long as the tree it
numbered holds still; `.django/` is pinned at 6.0.9 and the figures above are
for that pin.
