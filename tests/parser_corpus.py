"""Template sources for comparing our parse tree against Django's.

Every source here must *parse successfully*: errors are Task 6's corpus. There
are therefore no ``{{ variable }}`` tags anywhere in this file — variable
compilation is M3's, and at M2 such a template reaches ``_native.parse`` and is
reported as unimplemented.

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
RANDOM_ALPHABET = "abc \n\t'\"é🎉"

INLINE_TAGS = [
    "{% csrf_token %}",
    "{% debug %}",
    "{% templatetag openblock %}",
    "{% templatetag closebrace %}",
    '{% now "Y" %}',
    '{% now "jS F Y H:i" as when %}',
]

BLOCK_TAGS = [
    ("{% autoescape on %}", "{% endautoescape %}"),
    ("{% autoescape off %}", "{% endautoescape %}"),
    ("{% spaceless %}", "{% endspaceless %}"),
    ("{% verbatim %}", "{% endverbatim %}"),
    ("{% comment %}", "{% endcomment %}"),
]


def random_text(rng):
    return "".join(rng.choice(RANDOM_ALPHABET) for _ in range(rng.randint(0, 8)))


def random_piece(rng, depth):
    if depth >= 3:
        return random_text(rng)

    roll = rng.random()
    if roll < 0.45:
        return random_text(rng)
    if roll < 0.7:
        return rng.choice(INLINE_TAGS)

    opening, closing = rng.choice(BLOCK_TAGS)
    return opening + random_piece(rng, depth + 1) + closing


def random_template(rng):
    return "".join(random_piece(rng, 0) for _ in range(rng.randint(0, 5)))
