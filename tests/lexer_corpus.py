"""Template sources used to compare our lexer against Django's.

The corpus is deliberately adversarial: each entry below the first block exists
to pin a specific behaviour that is easy to get wrong, and the comment says
which. Sources are lexed as-is; they are not required to be valid templates.
"""

CORPUS = [
    # --- the case from Django's own test_base.py ---
    "text\n{% if test %}{{ varvalue }}{% endif %}"
    "{#comment {{not a var}} {%not a block%} #}end text",
    # --- ordinary shapes ---
    "",
    "plain text",
    "{{ x }}",
    "{% if x %}y{% endif %}",
    "{# c #}",
    "a{{ b }}c{% d %}{# e #}f",
    "{{ a }}{{ b }}",
    "{% a %}{% b %}",
    # --- whitespace handling in tag contents ---
    "{{    spaced    }}",
    "{%   if x   %}",
    "{#   note   #}",
    "{{\tx\t}}",
    # --- line numbers ---
    "\n\n{{ x }}",
    "a\nb\nc",
    "{{ a }}\n{{ b }}\n{{ c }}",
    "\n{% if x %}\n{% endif %}\n",
    # --- tags must not span newlines (no DOTALL) ---
    "{%\nif x %}",
    "{{ a\n}}",
    "{# a\n#}",
    "{% if\nx %}y{% endif %}",
    # --- unmatched openers ---
    # `{{% a %}` retries one character later and finds `{% a %}`; the other two
    # have no closer anywhere and end up entirely as text.
    "{{% a %}",
    "{% a }}",
    "{# a %}",
    "{{% a %} b {{ c }}",
    "{{{{ x }}",
    # --- interleaved and nested delimiters ---
    "{# {% #}",
    "{# {{ #}",
    "{{ {% }}",
    "{% {{ %} }}",
    "{#comment {{not a var}} {%not a block%} #}",
    # --- verbatim ---
    "{% verbatim %}{{ x }}{% endverbatim %}",
    "{% verbatim %}{% if x %}{% endverbatim %}",
    "{% verbatim %}{{ x }}",
    "{% verbatim foo %}{{ x }}{% endverbatim foo %}",
    "{% verbatim foo %}{% endverbatim bar %}{% endverbatim foo %}",
    "{% verbatimx %}",
    "{% verbatim %}{% endverbatim %}{{ after }}",
    "{% verbatim %}{% verbatim %}{% endverbatim %}",
    # --- verbatim crossed with line numbers ---
    # Items 4 and 5 of Review Focus are each covered alone above; these cross
    # them, so a lineno bug that only appears while verbatim state is set shows.
    "{% verbatim %}a\nb{% endverbatim %}",
    "{% verbatim %}\n{% if x %}\n{% endverbatim %}",
    "{% verbatim %}\n\n\n{% endverbatim %}",
    "x\n{% verbatim %}\ny\n{% endverbatim %}\nz",
    # --- non-ASCII, where byte and character offsets diverge ---
    "héllo{{ x }}",
    "{{ café }}",
    "日本語{% if 文字 %}中文{% endif %}",
    "emoji 🎉{{ x }}🎉",
    # --- empty and adjacent tags ---
    "{{}}{{}}",
    "{%%}{%%}",
    "{{  }}",
    "{#}{#}",
]
