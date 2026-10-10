"""Corpus for the differential harness.

Each case is rendered twice — once by stock Django, once by the Rust-backed
shim — and the two outputs are compared. Cases must therefore be expressible as
JSON-able template source plus a JSON-able context, so the two runs can happen in
separate processes without pickling arbitrary objects.

This corpus is a starter set. As milestones land, cases are added here first:
a template that reproduces a failure is far faster to iterate on than the
corresponding Django test module.

**What this corpus cannot carry, and where those cases went instead.** A case is
JSON-able template source plus a JSON-able context, and the runner renders both
sides through ``Engine.from_string(...).render(...)``. So a case can only reach
what a *render* reaches. That rules out the resolver's object-shaped paths — a
failing ``@property``, a ``BaseContext`` class attribute, an exception carrying
``silent_variable_failure`` — because they need real Python objects, and it rules
out exception *behaviour* under lookup, because a render never lets a
``VariableDoesNotExist`` out (``Variable.resolve`` substitutes
``string_if_invalid`` first). They live in
``tests/test_render_parity.py::ResolverParityTests``, which drives
``_native.resolve`` against ``Variable._resolve_lookup`` and compares the
exception class as well as the message. The plan put them here; the JSON
constraint is what moved them.
"""

CASES = [
    {
        "name": "plain_text",
        "template": "hello",
        "context": {},
    },
    {
        "name": "variable",
        "template": "{{ a }}",
        "context": {"a": "x"},
    },
    {
        "name": "missing_variable_uses_string_if_invalid",
        "template": "[{{ missing }}]",
        "context": {},
    },
    {
        "name": "dotted_lookup",
        "template": "{{ a.b.c }}",
        "context": {"a": {"b": {"c": "deep"}}},
    },
    {
        "name": "dotted_lookup_misses_one_bit_in",
        "template": "[{{ a.nope }}]",
        "context": {"a": {"b": "B"}},
    },
    {
        "name": "index_lookup",
        "template": "{{ items.1 }}",
        "context": {"items": ["zero", "one", "two"]},
    },
    {
        "name": "index_lookup_off_the_end",
        "template": "[{{ items.9 }}]",
        "context": {"items": ["zero"]},
    },
    {
        "name": "index_lookup_finds_a_string_key",
        "template": "{{ m.0 }}",
        "context": {"m": {"0": "zero"}},
    },
    {
        "name": "a_bare_number",
        "template": "{{ n }}",
        "context": {"n": 1234567},
    },
    {
        "name": "filter_on_a_literal",
        "template": "{{ 'ada'|capfirst }}",
        "context": {},
    },
    {
        "name": "filter_argument_is_a_lookup",
        "template": "{{ missing|default:fallback }}",
        "context": {"fallback": "F"},
    },
    {
        "name": "two_filter_arguments_in_a_chain",
        "template": "{{ n|add:1|add:2 }}",
        "context": {"n": 1},
    },
    {
        "name": "a_filter_chain_through_a_missing_variable",
        "template": "{{ nope|upper|default:'D' }}",
        "context": {},
    },
    {
        "name": "autoescape_on",
        "template": "{{ html }}",
        "context": {"html": "<b>&</b>"},
    },
    {
        "name": "safe_filter",
        "template": "{{ html|safe }}",
        "context": {"html": "<b>&</b>"},
    },
    {
        "name": "add_filter",
        "template": "{{ 1|add:2 }}",
        "context": {},
    },
    {
        "name": "add_filter_with_variable",
        "template": "{{ n|add:2 }}",
        "context": {"n": 40},
    },
    {
        "name": "upper_filter",
        "template": "{{ name|upper }}",
        "context": {"name": "bob"},
    },
    {
        "name": "filter_chain",
        "template": "{{ name|lower|capfirst }}",
        "context": {"name": "BOB"},
    },
    {
        "name": "default_filter",
        "template": "{{ missing|default:'fallback' }}",
        "context": {},
    },
    {
        "name": "length_filter",
        "template": "{{ items|length }}",
        "context": {"items": [1, 2, 3, 4]},
    },
    {
        "name": "join_filter",
        "template": "{{ items|join:', ' }}",
        "context": {"items": ["a", "b", "c"]},
    },
    {
        "name": "if_true",
        "template": "{% if a %}yes{% else %}no{% endif %}",
        "context": {"a": True},
    },
    {
        "name": "if_false",
        "template": "{% if a %}yes{% else %}no{% endif %}",
        "context": {"a": False},
    },
    {
        "name": "if_elif",
        "template": "{% if a %}1{% elif b %}2{% else %}3{% endif %}",
        "context": {"a": False, "b": True},
    },
    {
        "name": "if_and_or_not",
        "template": "{% if a and not b or c %}y{% else %}n{% endif %}",
        "context": {"a": True, "b": False, "c": False},
    },
    {
        "name": "if_comparison",
        "template": "{% if n > 3 %}big{% else %}small{% endif %}",
        "context": {"n": 5},
    },
    {
        "name": "for_loop",
        "template": "{% for x in items %}{{ x }},{% endfor %}",
        "context": {"items": [1, 2, 3]},
    },
    {
        "name": "for_empty",
        "template": "{% for x in items %}{{ x }}{% empty %}none{% endfor %}",
        "context": {"items": []},
    },
    {
        "name": "for_counter",
        "template": "{% for x in items %}{{ forloop.counter }}:{{ x }} {% endfor %}",
        "context": {"items": ["a", "b"]},
    },
    {
        "name": "for_reversed",
        "template": "{% for x in items reversed %}{{ x }}{% endfor %}",
        "context": {"items": [1, 2, 3]},
    },
    {
        "name": "nested_for",
        "template": "{% for a in outer %}{% for b in inner %}{{ a }}{{ b }}{% endfor %}{% endfor %}",
        "context": {"outer": ["x", "y"], "inner": [1, 2]},
    },
    # **A body that can write the loop variable.**  The walk rewrites a read of
    # the loop variable to a *frame* read — the value this iteration resolved —
    # so a body that can move that name in the context makes the rewrite stale.
    # `{% cycle … as s %}` is the one built-in that does it (`context.set_upward`
    # in `CycleNode.render`), and the name it writes lands in the layer already
    # holding `s`, i.e. the loop's own.  All four **diverge** on the pre-fix tree
    # — `differential-fix.txt` reads 4 of 52 differing there and 52 identical
    # after the fix — and the nested pair is the shape M19 step 5 slice 3 opened:
    # the first two rows differ on the *slice-2* tree as well (2 of 52), so they
    # are older than the slice that made them visible.
    {
        "name": "cycle_rebinds_the_loop_variable",
        "template": '{% for s in sections %}{% cycle "X" as s silent %}{{ s }}{% endfor %}',
        "context": {"sections": ["old"]},
    },
    {
        "name": "cycle_rebinds_the_loop_variable_and_survives_the_loop",
        "template": '{% for s in sections %}{% cycle "X" as s silent %}{{ s }}{% endfor %}[{{ s }}]',
        "context": {"sections": ["old"]},
    },
    {
        "name": "nested_for_keeps_an_ancestor_rebinding",
        "template": (
            '{% for s in sections %}{% for l in s %}'
            '{% cycle "X" as s silent %}{{ s }}{% endfor %}{{ s }}{% endfor %}'
        ),
        "context": {"sections": ["old"]},
    },
    {
        "name": "cycle_then_nested_for_reads_the_rebound_name",
        "template": (
            '{% for s in sections %}{% cycle "X" as s silent %}'
            "{% for l in s %}{{ l }}{% endfor %}{{ s }}{% endfor %}"
        ),
        "context": {"sections": ["old"]},
    },
    # **A loop's *sequence* is Python, and it runs inside the loop's layer.**
    # The second adversarial round's two BLOCKING findings, both on the fix above
    # and both reproduced here. `ForNode.render` opens `with context.push():`
    # *before* `values = self.sequence.resolve(...)` — entered at
    # `engine/defaulttags.py:198`, resolved at `engine/defaulttags.py:198-199` — so a
    # sequence sees the loop's layer and writes into it; and a
    # sequence is a `FilterExpression`, whose filters receive the arguments the
    # template gave them — including the context, named `ctx` here by
    # `context_ref`. `rewrite_body`'s old body-wide predicate
    # (`may_write_context`) had argued both away: "a filter cannot write the
    # context", and a nested loop's "sequence is read, not written".
    #
    # All seven diverge on the `35ff15c` build (`sha256:b0c073c33ecdfe6c`): the
    # first four by *values*, the last three by a layer count.
    {
        "name": "nested_sequence_filter_rebinds_an_ancestor",
        "template": (
            "{% load ctxwrite %}{% for s in sections %}"
            "{% for x in xs|rebind_up:ctx %}{% endfor %}{{ s }}{% endfor %}"
        ),
        "context": {"sections": ["old"], "xs": []},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "nested_sequence_filter_rebinds_an_ancestor_around_a_body",
        "template": (
            "{% load ctxwrite %}{% for s in sections %}"
            "{% for x in xs|rebind_up:ctx %}{{ x }}{% endfor %}{{ s }}{% endfor %}"
        ),
        "context": {"sections": ["old"], "xs": ["z"]},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "nested_sequence_filter_writes_only_its_own_layer",
        "template": (
            "{% load ctxwrite %}{% for s in sections %}"
            "{% for x in xs|rebind_item:ctx %}{% endfor %}{% firstof s %}{% endfor %}"
        ),
        "context": {"sections": ["old"], "xs": []},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "top_level_sequence_filter_writes_only_its_own_layer",
        "template": "{% load ctxwrite %}{% for x in xs|rebind_item:ctx %}{% endfor %}[{{ s }}]",
        "context": {"s": "old", "xs": []},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "sequence_filter_sees_the_loops_layer",
        "template": "{% load ctxwrite %}{% for x in ab|ctxdepth:ctx %}{{ x }}{% endfor %}",
        "context": {"ab": "12"},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "nested_sequence_filter_sees_both_layers",
        "template": (
            "{% load ctxwrite %}{% for s in sections %}"
            "{% for x in ab|ctxdepth:ctx %}{{ x }}{% endfor %}{% endfor %}"
        ),
        "context": {"sections": ["old"], "ab": "12"},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "empty_loop_sequence_filter_sees_the_loops_layer",
        "template": "{% load ctxwrite %}{% for x in xs|ctxdepth:ctx %}{{ x }}{% endfor %}",
        "context": {"xs": []},
        "libraries": {"ctxwrite": "ctxwrite_library"},
        "context_ref": "ctx",
    },
    {
        "name": "with_tag",
        "template": "{% with y=a %}{{ y }}{% endwith %}",
        "context": {"a": "bound"},
    },
    {
        "name": "comment_tag",
        "template": "a{% comment %}hidden{% endcomment %}b",
        "context": {},
    },
    {
        "name": "verbatim_tag",
        "template": "{% verbatim %}{{ not_rendered }}{% endverbatim %}",
        "context": {},
    },
    {
        "name": "spaceless_tag",
        "template": "{% spaceless %}<p> <a>x</a> </p>{% endspaceless %}",
        "context": {},
    },
    {
        "name": "filter_tag",
        "template": "{% filter upper %}shout{% endfilter %}",
        "context": {},
    },
    {
        "name": "firstof_tag",
        "template": "{% firstof a b c %}",
        "context": {"a": "", "b": "second", "c": "third"},
    },
    {
        "name": "widthratio_tag",
        "template": "{% widthratio this_value max_value 100 %}",
        "context": {"this_value": 175, "max_value": 200},
    },
    {
        "name": "cycle_tag",
        "template": "{% for x in items %}{% cycle 'a' 'b' %}{% endfor %}",
        "context": {"items": [1, 2, 3, 4]},
    },
    {
        "name": "ifchanged_tag",
        "template": "{% for x in items %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}",
        "context": {"items": [1, 1, 2, 2, 3]},
    },
    {
        "name": "lorem_tag",
        "template": "{% lorem 3 w %}",
        "context": {},
    },
    {
        "name": "now_tag",
        "template": "{% now 'Y' %}",
        "context": {},
    },
    {
        "name": "string_literal",
        "template": "{{ 'literal' }}",
        "context": {},
    },
    {
        "name": "integer_literal",
        "template": "{{ 42 }}",
        "context": {},
    },
    {
        "name": "syntax_error",
        "template": "{% if %}",
        "context": {},
    },
    {
        "name": "unknown_tag",
        "template": "{% not_a_tag %}",
        "context": {},
    },
    {
        "name": "unclosed_block",
        "template": "{% if a %}",
        "context": {"a": True},
    },
]
