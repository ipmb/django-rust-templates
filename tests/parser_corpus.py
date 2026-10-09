"""Template sources for comparing our parse tree against Django's.

Every source here must *parse successfully*: errors are Task 6's corpus.

There were no ``{{ variable }}`` tags anywhere in this file at M2 — variable
compilation is M3's, and back then such a template reached ``_native.parse`` and
was reported as unimplemented. M3's Task 5 supplies the arm, so the header's old
blanket claim is gone and variables are here for the reason Step 1 of the plan's
Task 10 gives: ``{{ … }}`` is the input that sets ``contains_nontext``.

Each entry below the first block pins one behaviour, named in its comment.
"""

TEMPLATES = [
    # --- text, and the things that are not tags ----------------------------
    "",
    "hello",
    "a\nb\n",
    "  leading and trailing  ",
    "line one\nline two\nline three",
    "a{{",  # an unmatched opener is text, not a tag
    "{#",  # as is an unmatched comment opener
    "{# c #}",
    "a{# c #}b",
    "{#}{#}",  # empty comments, adjacent
    "{# {% if %} #}",  # a block tag inside a comment is not a tag
    "{# {{ x }} #}",
    "héllo 🎉",  # contents and positions are counted in characters
    # --- csrf_token and debug: no arguments are read -----------------------
    "{% csrf_token %}",
    "{% csrf_token junk %}",
    "x{% csrf_token %}y",
    "{% debug %}",
    "{% debug junk more %}",
    # --- templatetag: all eight bits, then two in sequence -----------------
    "{% templatetag openblock %}",
    "{% templatetag closeblock %}",
    "{% templatetag openvariable %}",
    "{% templatetag closevariable %}",
    "{% templatetag openbrace %}",
    "{% templatetag closebrace %}",
    "{% templatetag opencomment %}",
    "{% templatetag closecomment %}",
    "{% templatetag openblock %}{% templatetag closeblock %}",
    # --- now: quoted, unquoted, empty, and the `as` form -------------------
    '{% now "jS F Y H:i" %}',
    '{% now "Y" %}',
    '{% now "Y" as year %}',
    '{% now "Y" as year %}x',
    '{% now "a b" %}',  # one quoted phrase, so `split_contents`, not `split`
    "{% now 5 %}",  # unquoted: first and last characters are dropped
    "{% now abc %}",
    '{% now "" %}',
    # --- autoescape: both settings, empty, nested, and around a non-text node
    "{% autoescape on %}x{% endautoescape %}",
    "{% autoescape off %}x{% endautoescape %}",
    "{% autoescape on %}{% endautoescape %}",
    "{% autoescape off %}{% autoescape on %}x{% endautoescape %}{% endautoescape %}",
    "a{% autoescape on %}{% csrf_token %}{% endautoescape %}b",
    "{% autoescape on %}{% comment %}c{% endcomment %}x{% endautoescape %}",
    # --- comment: the body is skipped, never parsed -----------------------
    "{% comment %}{% endcomment %}",
    "{% comment %}anything{% endcomment %}",
    # `{% nope %}` inside a comment must not be reported: `skip_past`, not
    # `parse` (Review Focus item 9).
    "{% comment %}{% if %}{{ x }}{% nope %}{% endcomment %}x",
    "a{% comment %}b{% endcomment %}c",
    # --- spaceless: text, empty, nested, and around a non-text node --------
    "{% spaceless %}  a  {% endspaceless %}",
    "{% spaceless %}{% endspaceless %}",
    "{% spaceless %}{% spaceless %}a{% endspaceless %}{% endspaceless %}",
    "{% spaceless %}{% csrf_token %}{% endspaceless %}",
    # --- verbatim: the body is rendered at parse time, and kept safe -------
    "{% verbatim %}a{% endverbatim %}",
    "{% verbatim %}{% endverbatim %}",
    "{% verbatim %}{{ x }}{% endverbatim %}",
    # Inside verbatim, `{% if %}` is text: a *lexer* behaviour the tree depends
    # on, and one Django's own lexer also has.
    "{% verbatim %}{% if %}x{% endif %}{% endverbatim %}",
    "{% verbatim %}  spaces & {braces}  {% endverbatim %}",
    "{% verbatim %}a{% endverbatim %}{% verbatim %}b{% endverbatim %}",
    # --- combinations, to reach nesting and dispatch together --------------
    "{% spaceless %}{% verbatim %}{{ x }}{% endverbatim %}{% endspaceless %}",
    "{% autoescape off %}{% spaceless %}{% templatetag openbrace %}"
    "{% endspaceless %}{% endautoescape %}",
    "{% comment %}{% spaceless %}{% endcomment %}{% spaceless %}x{% endspaceless %}",
    "text{% csrf_token %}more{% debug %}end",
    # --- variables, and the `contains_nontext` they set ---------------------
    # M3's half of §10.4's ordering gate. `{{ … }}` is what sets
    # `contains_nontext` — measured: `text{% extends %}` is legal and
    # `{{ x }}{% extends %}` is not — so variable-versus-text and
    # variable-versus-tag are the cases that exercise the rule's input.
    # (The `{% extends %}`-specific case is M7's; see the plan's Task 10.)
    "{{ x }}{% csrf_token %}",
    "{{ x }}{% csrf_token %}y",
    "a{{ x }}",
    "{% autoescape on %}{{ x }}{% endautoescape %}{% autoescape on %}b{% endautoescape %}",
    "{% spaceless %}{{ x }}{% endspaceless %}",
    # --- the variable syntax itself, which M3's Task 5 added ----------------
    "{{ a.b.c }}",
    "{{ a.0 }}",
    "{{ a|upper }}",
    "{{ a|default:'z'|upper }}",
    "{{ 'lit' }}",
    "{{ 5 }}",
    "{{ a.0|join:', ' }}",  # a quoted argument containing the filter separator
    "{{ x }}{{ y }}",
]


# --- the randomised pass ---------------------------------------------------
#
# The corpus and the implementation share an author, which is the standing
# weakness of a parity test written this way. The answer is a second, generated
# corpus: it reaches combinations nobody thought to write down, and it is seeded
# so any failure reproduces exactly.

RANDOM_SEED = 20261008
RANDOM_CASES = 400

# No `{`, `}`, `%` or `#`, so no random text accidentally forms a tag. What the
# generator is for is *shape*: nesting depth, sibling order, and which nodelists
# end up holding non-text nodes.
#
# U+001C..U+001F are here because Python's `str.isspace()` accepts them and
# Rust's `char::is_whitespace()` does not, which is the M1 divergence the parity
# suite exists to catch; `é` and 🎉 keep non-ASCII in the text, and the quote
# characters keep quoting in play. All four separators, not one, because a
# boundary fixed for U+001C could still be wrong for U+001D..U+001F.
RANDOM_ALPHABET = "abc \n\t'\"\u001c\u001d\u001e\u001fé🎉"

INLINE_TAGS = [
    "{% csrf_token %}",
    "{% debug %}",
    "{% templatetag openblock %}",
    "{% templatetag closebrace %}",
    '{% now "Y" %}',
    '{% now "jS F Y H:i" as when %}',
]

# The M2 round's own recorded residue was that all 400 generated templates were
# variable-free outside `{% verbatim %}`/`{% comment %}`, so a green randomised
# pass said nothing about M3's code. These are the *shapes* rather than one
# shape: a generator that only ever produced `{{ x }}` would leave dotted
# lookup, indexing and filters — the whole of M3 — unexercised, which is what
# `RandomisedParityTests.test_the_generator_reaches_variables` floors.
VARIABLES = (
    "{{ x }}",
    "{{ a.b.c }}",
    "{{ a.0 }}",
    "{{ a|upper }}",
    "{{ a|default:'z'|upper }}",
    "{{ 'lit' }}",
    "{{ 5 }}",
    "{{ a.0|join:', ' }}",
)

BLOCK_TAGS = [
    ("{% autoescape on %}", "{% endautoescape %}"),
    ("{% autoescape off %}", "{% endautoescape %}"),
    ("{% spaceless %}", "{% endspaceless %}"),
    ("{% verbatim %}", "{% endverbatim %}"),
    ("{% comment %}", "{% endcomment %}"),
]


def random_text(rng):
    return "".join(rng.choice(RANDOM_ALPHABET) for _ in range(rng.randint(0, 8)))


def random_tag_with_argument(rng):
    """A tag whose *argument* is random text.

    Without this, `RANDOM_ALPHABET` only ever reaches text nodes, and the
    separator table that M1 got wrong is exercised on the one path where it does
    not matter. The table bites in `smart_split`, so the argument has to be a tag
    argument for the alphabet to be worth widening.

    `{% autoescape %}` is the M2 tag with exactly one argument, so a
    disagreement here is a parser bug rather than a milestone boundary. Measured
    on the pinned tree: Python's `re` `\\s` and `str.isspace()` are the same 29
    code points, so `x<U+001C>on` splits into two arguments for Django and must
    for us; `%`, `{`, `}` and `#` are absent from the alphabet, so the random
    text cannot terminate the tag early, while `'` and `"` stay in play and make
    quoting — including unbalanced quoting, which `smart_split` handles by
    failing to match the quoted alternative rather than raising — part of the
    input space.
    """
    return "{% autoescape " + random_text(rng) + " %}x{% endautoescape %}"


def random_piece(rng, depth):
    if depth >= 3:
        return random_text(rng)

    roll = rng.random()
    if roll < 0.35:
        return random_text(rng)
    if roll < 0.52:
        return rng.choice(INLINE_TAGS)
    if roll < 0.68:
        return random_tag_with_argument(rng)
    if roll < 0.84:
        return rng.choice(VARIABLES)

    opening, closing = rng.choice(BLOCK_TAGS)
    return opening + random_piece(rng, depth + 1) + closing


def random_template(rng):
    return "".join(random_piece(rng, 0) for _ in range(rng.randint(0, 5)))
