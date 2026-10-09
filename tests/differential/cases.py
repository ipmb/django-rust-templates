"""Corpus for the differential harness.

Each case is rendered twice — once by stock Django, once by the Rust-backed
shim — and the two outputs are compared. Cases must therefore be expressible as
JSON-able template source plus a JSON-able context, so the two runs can happen in
separate processes without pickling arbitrary objects.

This corpus is a starter set. As milestones land, cases are added here first:
a template that reproduces a failure is far faster to iterate on than the
corresponding Django test module.
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
        "name": "index_lookup",
        "template": "{{ items.1 }}",
        "context": {"items": ["zero", "one", "two"]},
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
