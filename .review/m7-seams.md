# M7 — inheritance, and the four seams behind it

Forcing event: **a `{% load %}`ed library that registers `block` silently lost.**
`ShellHost.has_tag` consulted an exception list named `_M7_OWNED` *before* its
identity clause, so a caller's override of a Rust-owned name answered "Rust's"
and Rust's `do_block` compiled the caller's tag. Measured and now closed — that
is the divergence this milestone was built to remove, and the list is gone.

The design decision behind it is **Design B**: Rust owns `do_block`,
`do_extends` and `do_include`, so all three compile on Rust's side and the seam
carries a *compile callback*, not three parsers' worth of state. The registry
went 22 → 25.

Every number below comes from `.review/m7-measurements/m7_measure_seams.py`,
run at close-out; nothing here is restated from a section that measured it
earlier. Derived against `.django/` @ 6.0.9.

## The rule

**A verdict here is dated.** It is a number only for as long as the thing it
numbered holds still. M5's rule, unchanged and re-quoted because it is the only
forward use this document has: *the row expires when the seam, its caller, or the
input that exercises it changes.* This is a one-time audit, not a gate. Nothing
re-runs it. When a nearby line moves, this is a list of places to look again —
which is why each row names a **symbol**, never a line.

## The seam inventory

Five crossings. The direction column is the part worth reading twice: M7 added
one inbound crossing and moved one piece of state from the host's side to
Rust's, and the other three were already there.

| seam | direction | who holds the state | what would notice it breaking |
|---|---|---|---|
| `Host::has_tag` | Rust → host, **once per tag in a parse** | `parser.tags` (Django's, merged) + `django_own_tags()` | `test_the_host_declines_every_name_rust_owns`, and the wire row |
| `Host::call_tag` | Rust → host, once per **host-compiled** tag | the host's `Parser`: `tokens`, `command_stack`, `extra_data`, `libraries` | the 106-row error corpus, five `test_custom.py`/`test_partials.py` unclosed-tag assertions |
| `must_be_first` | host → Rust, **per node** (M7's addition) | `NodeDesc.must_be_first` | `tests/test_inheritance_gate.py::MustBeFirstGateTests` |
| `origin_template_name` | Python → Rust, **per parse** | `Parser::origin_template_name` (raw, not a repr) | `{% extends %}`'s three messages, A3 |
| the include cache | **shared** — Django's `render_context` | `IncludeNode.render`'s key on `self` | the parity row, and only as parity (see below) |

Measured: `parser.tags` holds **27** names; Rust compiles **25**, and the host
compiles exactly **`['load', 'querystring']`**. The registry's 25 and the Rust
side are the same set (`registry == the Rust side: True`), which is the check
that no name is claimed by accident.

**The two names the host keeps are answered by absence, not by a list.**
`has_tag` asks the registry first, so a name Rust does not own cannot reach the
identity clause at all. That is what makes the clause *whole* rather than the
last of three rules — and it is the difference the deletion of `_M7_OWNED` made.

**The wire, read structurally.** Who compiled a tag is visible in the parse
result without asking the predicate: a Rust-compiled tag arrives as a plain class
name, a host-compiled one as `HostNode`. Measured, six rows:

```text
rust  '{% block a %}{% endblock %}'      -> ['BlockNode']
rust  '{% extends "base" %}'              -> ['ExtendsNode']
rust  '{% include "a.html" %}'            -> ['IncludeNode']
host  '{% load custom %}{% counter %}'    -> ['HostNode', 'HostNode']
host  '{% load custom %}'                 -> ['HostNode']
host  '{% querystring a=1 %}'             -> ['HostNode']
```

## Divergences, and which way each went

**Closed: the `{% load %}`ed `block` override.** Measured after the fix, a
library registering `block` makes the host compile it — which is correct, because
the loaded callable is not the one `django_own_tags()` names. Before Task 4 the
same input answered `False` and the override was silently ignored. This is the
forcing event, and it is the one entry in this table that is a *repair* rather
than a record.

**Recorded, not fixed: `parser.__loaded_blocks` forks.** `do_block`'s
duplicate-name check reads and writes one Python attribute, and M7 added the
matching field to **Rust's** `Parser` (`loaded_blocks`) so that every `{% block %}`
Rust compiles now reads and writes its state in one place. The state did not
move; it **forked**. A `{% block %}` inside a *host* tag's body is still compiled
by Django's `do_block` and accumulates in Django's counter, while every
Rust-compiled one accumulates in Rust's — two counters that cannot see each
other. No library tag named `block` is needed for this: the trigger is any
`{% block %}` whose enclosing block-level tag Rust does not own.

The predicate *"does any `{% block %}` sit inside a body Django parses?"* was
run rather than assumed, and it returns **two** across the corpus (1046 string
literals walked and tokenized) and **one** reached in the pinned suite —
`test_blocktranslate.py::test_with_block`, where the host raises first.
(`m7_measure_nesting.py`, `m7_measure_nest2.py`.)

**What the fork costs is one refusal, and the refusal is load-bearing — this is
the part a "one message wide" reading understates.** `ExtendsNode.__init__` keys
`self.blocks` by name over *materialised* nodes, which both parsers produce, and
a dict keyed by name keeps the **last** insertion. Stock Django cannot reach that
pairing: `do_block` refuses the duplicate before the template renders. Under the
fork a duplicate split across the two counters is not refused, so both nodes
reach `self.blocks` and the **later one silently wins**. So the divergence is a
missing refusal whose consequence is a *rendered tree*, not a message — it is
unreachable in the pinned suite for a different reason (the host raises first),
and it stays recorded rather than fixed.

**Closed by threading a value, not by widening the wire: `origin`.** `{% extends %}`
needs the **raw** `Origin.template_name`, not a `repr`, because
`construct_relative_path` compares it against a path and interpolates it with
plain `%s` in three messages — a repr is wrong in all three. The field was
renamed and now holds the raw name, with the repr derived through M5's
`Host::repr` seam exactly where the message needs it.

**Not a divergence, and worth saying because it looks like one: the include
cache is shared, so a row comparing our output with stock's cannot see a defect
in it.** `IncludeNode.render` keys Django's own `render_context`, both engines
read that one dict, and breaking it gives `'000000000'` on **both** sides. The
row is therefore a *parity* instrument. The row beside it is not:
`test_the_loop_body_holds_exactly_one_include_node` asserts the materialised
shape, where a stray node renders `''` and is invisible to any output
comparison. **A parity row that cannot see a class of defect needs a non-parity
row beside it** — this is the fourth appearance of that rule in the project and
the first in M7.

## What M7 changes about the seam, in one line

It removes a *rule* and adds a *field*: `has_tag` is now the identity clause and
nothing else, and `must_be_first` crosses inbound on the descriptor so the
must-be-first refusal can fire for a host-compiled node. Nothing new is
interposed — `unshimmed()` is **25**, M6's figure, and any movement there is a
defect rather than a milestone result.

**A note for whoever runs the next measurement script: `_shim.install()` must
come last.** It rewrites the callables Django registered, so `ShellHost`'s
identity clause reads "not Django's" for every name and the split above reports
0/27 instead of 25/2. That is a property of the measurement, not of the seam —
but it is exactly the kind of thing that makes a re-run disagree with its own
record.

## The re-run, at close-out

**2026-10-09, against `.django/` @ 6.0.9.** All ten scripts in
`.review/m7-measurements/`, each as `.venv/bin/python -I .review/m7-measurements/<name>.py`,
because a figure restated from an earlier session is the thing this milestone
kept being corrected for. Every one ran and reproduced the figure its section
states — the four that this document's claims rest on, quoted as printed:

```text
m7_measure_seams.py            names in parser.tags: 27 / Rust compiles: 25
                               host compiles: ['load', 'querystring']
                               registry == the Rust side: True
                               (and of the two nesting rows) 1 reached in the pinned suite
                               unshimmed() modules: 25
m7_measureA.py                 row 4 block=False extends=False include=False
                               row 5 block=True  extends=False include=False
m7_measure_half_fix.py         tests/test_inheritance_gate: 8 run, 6 failed
m7_measure_nesting.py          scanned 1046 string literal(s), tokenized 1046
                               nested inside a host-owned block-level tag: 2
```

The other six (`B`, `origin`, `nest2`, `pyposix`, `gettextlist`,
`child_nodelists`) ran clean as well: `pyposix` still reports **0 mismatches**
across 221,845 unary inputs and 800,000 join pairs, and `child_nodelists` still
finds `IfNode` the only property and 2 classes storing a nodelist they do not
declare. **The `m7_measure_half_fix.py` row is the one to read twice**: it
*executes* the gate file under the rebind rather than printing a remembered
count, so it is a measurement of the detectors rather than a claim about them.

