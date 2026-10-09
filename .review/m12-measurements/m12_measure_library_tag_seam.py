"""Which code compiles a ported library tag — Rust's descriptor, or the transcription?

`m12_measure_tag_routing.py` answers a *registry* question — is the callable
`register.tags` holds ours, and is the node class ours. Both are ours for a
ported library tag either way, so that instrument cannot tell the two routes to
`ours/ours` apart:

* **Rust's descriptor path.** Rust's `tags::l10n::localize` builds a node
  *descriptor* carrying `class_name = "LocalizeNode"`, and the materialiser
  resolves it through the module-global `NODE_CLASSES` lookup at
  `_impl/parser.py:310`.
* **The transcription.** `_impl/templatetags/l10n.py:62` ends
  `return LocalizeNode(nodelist, use_l10n)` — a *module-global* read at call
  time, which never consults `NODE_CLASSES`. `i18n`'s bodies do the same
  (`_impl/templatetags/i18n.py:453` for `TranslateNode`, `:616` for
  `LanguageNode`).

So two mutually exclusive signals settle it: a recording `NODE_CLASSES`
observes the first, and replacing that module global with a recorder observes
the second. `parser.tags` holds the registered function object directly, so
replacing the global changes nothing about routing — it only shows which body
ran.

**Covering three tags in two libraries.** The probe began as `l10n`'s alone and
was renamed when M12 Task 6a gave `i18n` its eight bodies: the question is the
same for every ported library tag, and a per-library copy of the probe would be
the drift this tree keeps writing rows against. The `l10n` case is kept
unchanged as the historical measurement, so the rename is not a rewrite.

Measured at `ba64a52` (`M12: l10n is our module — the second count is 1`), for
`l10n`:

    control        : recorder fires
    {% localize on %}  NODE_CLASSES : ['LocalizeNode', 'VariableNode']
                       localize_tag : (not called)
    {% localize %}     NODE_CLASSES : ['LocalizeNode', 'VariableNode']
                       localize_tag : (not called)

and at this commit (M12 Task 6a), for `i18n`.

**Read that as the *compile function* being dead, and not as the module being
inert.** The recorder observes the tag body — `localize_tag`, `do_translate`,
`language` — and only it. The node class the Rust descriptor path materialises
*is* the transcribed file's class (the reviewer confirmed the identity rather
than the module name for `l10n`: `compiled node class:
<class 'django.templatetags.l10n.LocalizeNode'>, defined in
_impl/templatetags/l10n.py, is l10n.LocalizeNode: True`, and it renders), and
the libraries' filters run through our files. What the transcription contributes
here is the node class and the filters; what it carries and never runs is the
tag body.

That is unreachable on **every** path, not just the measured one: Rust owns the
name, so `has_tag` answers `False` and Rust compiles; and if a third-party
library rebinds the name, `has_tag` answers `True` and *that library's* function
runs — still not ours. So an error in a transcribed tag body is invisible to the
1547, and the transcription guard's value is that it keeps the function honest
against upstream rather than that anything executes it. This is true of every
ported library tag, and it is Task 8's sentence to write.

The control matters: without it, "(not called)" would be as consistent with a
broken recorder as with Rust having compiled the tag. It calls the *patched*
name, since calling the saved original would bypass the recorder — the first
version of this script did exactly that and printed `RECORDER BROKEN`.

Run with the shim on `PYTHONPATH`, and **not** `-I` (which implies `-s` and
ignores `PYTHONPATH`, so under it this measures stock Django):

    . scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django:$PWD" \\
        .venv/bin/python .review/m12-measurements/m12_measure_library_tag_seam.py
"""

import pathlib
import sys

ROOT = pathlib.Path("/Users/pete/projects/lincolnloop/django-rust-templates")
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / ".django"))

from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(USE_I18N=True, USE_TZ=True, INSTALLED_APPS=[])

import django  # noqa: E402

django.setup()

import django.template.defaulttags as dtags  # noqa: E402
import django.template.parser as parser_mod  # noqa: E402
from django.template.backends.django import DjangoTemplates  # noqa: E402

print("shim installed :", "_impl" in (dtags.__file__ or ""))
print("parser module  :", parser_mod.__file__)

# --- the Rust signal: a recording NODE_CLASSES --------------------------------

real_classes = parser_mod.NODE_CLASSES
lookups = []


class Recording(dict):
    def __getitem__(self, key):
        lookups.append(key)
        return real_classes[key]


parser_mod.NODE_CLASSES = Recording(real_classes)

# --- compile ------------------------------------------------------------------

backend = DjangoTemplates(
    {"NAME": "django", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
)
engine = backend.engine


def run(module_name, node_name, sources, control_args):
    """One library's tag bodies: patch the module global, run the control, compile."""
    import importlib

    module = importlib.import_module(module_name)
    print()
    print(f"== {module_name}  ({module.__file__})")

    real_node = getattr(module, node_name)
    calls = []

    def recording_node(*args, **kwargs):
        calls.append(args)
        return real_node(*args, **kwargs)

    setattr(module, node_name, recording_node)

    # Positive control on the instrument: silence is not success. If the recorder
    # does not fire when the transcription's own last line is executed, then
    # "(not called)" below would mean "the probe is broken", not "Rust compiled
    # it". The patched name is called on purpose; the saved original would bypass
    # it. `control_args` is per node because the constructors differ — the
    # `FilterExpression` slots are stood in for by `_filter`.
    calls.clear()
    recording_node(*control_args)
    print("control        :", "recorder fires" if calls else "RECORDER BROKEN")

    for source in sources:
        del lookups[:], calls[:]
        template = engine.from_string(source)
        kinds = [type(n).__name__ for n in template.nodelist]
        node = next(n for n in template.nodelist if type(n).__name__ == node_name)
        print()
        print("source         :", source)
        print("nodelist       :", kinds)
        print("node module    :", type(node).__module__)
        print("NODE_CLASSES   :", lookups or "(no lookup)")
        print(f"{node_name:<14} :", "CALLED" if calls else "(not called)")

    setattr(module, node_name, real_node)


def _filter():
    """A stand-in for a `FilterExpression` in a control call.

    `TranslateNode.__init__` reads `.var` and rewrites it; `LanguageNode.__init__`
    only stores it. A `str` `var` is the literal case `__init__` handles, so this
    is the shape the real constructor sees for `{% translate "x" %}`.
    """
    import types

    return types.SimpleNamespace(var="control")


# The l10n case, unchanged from the pre-rename version.
run(
    "django.templatetags.l10n",
    "LocalizeNode",
    (
        "{% load l10n %}{% localize on %}{{ x }}{% endlocalize %}",
        "{% load l10n %}{% localize %}{{ x }}{% endlocalize %}",
    ),
    ("control", False),
)

# M12 Task 6a's additions. `translate` and `trans` are one body, so the second is
# the same evidence under the other registry name.
run(
    "django.templatetags.i18n",
    "TranslateNode",
    (
        '{% load i18n %}{% translate "Hello" %}',
        '{% load i18n %}{% trans "Hello" noop %}',
    ),
    (_filter(), False, None, None),
)

run(
    "django.templatetags.i18n",
    "LanguageNode",
    ('{% load i18n %}{% language "de" %}{{ x }}{% endlanguage %}',),
    ("control", _filter()),
)
