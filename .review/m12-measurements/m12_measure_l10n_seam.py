"""Which code compiles `{% load l10n %}{% localize on %}…{% endlocalize %}`?

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
  time, which never consults `NODE_CLASSES`.

So two mutually exclusive signals settle it: a recording `NODE_CLASSES`
observes the first, and replacing that module global with a recorder observes
the second. `parser.tags` holds the registered function object directly, so
replacing the global changes nothing about routing — it only shows which body
ran.

Measured at `ba64a52` (`M12: l10n is our module — the second count is 1`):

    control        : recorder fires
    {% localize on %}  NODE_CLASSES : ['LocalizeNode', 'VariableNode']
                       localize_tag : (not called)
    {% localize %}     NODE_CLASSES : ['LocalizeNode', 'VariableNode']
                       localize_tag : (not called)

The control matters: without it, "(not called)" would be as consistent with a
broken recorder as with Rust having compiled the tag. It calls the *patched*
name, since calling the saved original would bypass the recorder — the first
version of this script did exactly that and printed `RECORDER BROKEN`.

Run with the shim on `PYTHONPATH`, and **not** `-I` (which implies `-s` and
ignores `PYTHONPATH`, so under it this measures stock Django):

    . scripts/env.sh
    DJANGO_RUST_TEMPLATES=1 PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django:$PWD" \\
        .venv/bin/python .review/m12-measurements/m12_measure_l10n_seam.py
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

# --- the transcription signal: a recording module-global ----------------------

import django.templatetags.l10n as l10n  # noqa: E402

print("l10n module    :", l10n.__file__)
real_node = l10n.LocalizeNode
calls = []


def recording_node(*args, **kwargs):
    calls.append(args)
    return real_node(*args, **kwargs)


l10n.LocalizeNode = recording_node

# --- compile ------------------------------------------------------------------

backend = DjangoTemplates(
    {"NAME": "django", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
)
engine = backend.engine

# Positive control on the instrument: silence is not success. If the recorder
# does not fire when the transcription's own last line is executed, then
# "(not called)" below would mean "the probe is broken", not "Rust compiled it".
# The patched name is called on purpose; the saved original would bypass it.
calls.clear()
l10n.LocalizeNode("control", False)
print("control        :", "recorder fires" if calls else "RECORDER BROKEN")

for source in (
    "{% load l10n %}{% localize on %}{{ x }}{% endlocalize %}",
    "{% load l10n %}{% localize %}{{ x }}{% endlocalize %}",
):
    del lookups[:], calls[:]
    template = engine.from_string(source)
    kinds = [type(n).__name__ for n in template.nodelist]
    node = next(n for n in template.nodelist if type(n).__name__ == "LocalizeNode")
    print()
    print("source         :", source)
    print("nodelist       :", kinds)
    print("node module    :", type(node).__module__)
    print("use_l10n       :", getattr(node, "use_l10n", "<none>"))
    print("NODE_CLASSES   :", lookups or "(no lookup)")
    print("localize_tag   :", "CALLED" if calls else "(not called)")
