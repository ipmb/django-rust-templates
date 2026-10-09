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

**A verdict here is dated.** Each is a number only for as long as the tree it
numbered holds still; `.django/` is pinned at 6.0.9 and the figures above are
for that pin.
