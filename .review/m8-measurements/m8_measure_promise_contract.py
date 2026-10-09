"""M8, re-measured by the reviewer: the resolve path hands back the *same
object*, on every shape the path has.

The first version of this measurement enumerated Django's forcing sites -- two
of them, both on the render path -- and concluded "nothing forces a `Promise`"
from that enumeration. The claim is true, but it is an exhaustiveness claim
about Django's source, which is this project's worst class of error: a third
site would falsify it.

This measures the **contract** instead. Whatever Django does internally and
wherever else it does it, the resolve path's output is the object it was handed.
Five shapes, one per way a value can be reached -- the ones a forcing site would
have to sit in to matter -- and each asserts **identity**, because
`str(proxy) == proxy` is True and an equality row cannot see a forced value
(`§D4`'s third no-op cause).

Run as `.venv/bin/python -I .review/m8-measurements/m8_measure_promise_contract.py`.
"""

import sys

ROOT = "/Users/pete/projects/lincolnloop/django-rust-templates"
sys.path.insert(0, ROOT + "/.django")
sys.path.insert(0, ROOT + "/.django/tests")
sys.path.insert(0, ROOT)

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(
    USE_I18N=True,
    USE_TZ=False,
    USE_L10N=False,
    DEBUG=False,
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates"}],
)
django.setup()

from django.template.base import Variable  # noqa: E402
from django.template.context import Context  # noqa: E402
from django.utils.functional import Promise, lazy  # noqa: E402

PROXY = lazy(lambda s: s.upper(), str)("lazy & value")

assert isinstance(PROXY, Promise), "the probe is not a Promise"


class Holder:
    attr = PROXY

    def meth(self):
        return PROXY


SHAPES = [
    ("p", {"p": PROXY}),
    ("o.attr", {"o": Holder()}),
    ("d.k", {"d": {"k": PROXY}}),
    ("lst.0", {"lst": [PROXY]}),
    ("o.meth", {"o": Holder()}),
]

failures = 0
for token, mapping in SHAPES:
    got = Variable(token).resolve(Context(mapping))
    same = got is PROXY
    failures += not same
    print(
        f"  {token:8} isinstance Promise: {isinstance(got, Promise)!s:5} "
        f"is-same-object: {same!s:5}  {type(got).__name__}"
    )

print(f"\n{len(SHAPES)} shapes, {failures} forced")
raise SystemExit(1 if failures else 0)
