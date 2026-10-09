"""Which functions in `base.py` does the parity harness actually execute?

`tests/parity_target.py` asserts that the transcription's declared regions miss
"the compared path", so that the parity comparison cannot become Rust against
Rust. The first version of that row computed the path as
`inspect.getsourcelines(target)` for the one target the row names — `Parser.parse`
— and the reviewer's question is whether that is the path or a proxy for it:
`Parser.parse` calls `extend_nodelist`, `compile_filter`, `invalid_block_tag`
and every tag's compile function, so a region declared inside a *helper* is on
the harness's path while a region declared in `parse` is not, and the row's
output cannot tell the two apart.

The instrumentation itself now lives in `tests/compared_path.py`, because the
pin it produces (`scripts/check_transcriptions.py`'s `COMPARED_PATH`) is
re-measured by `tests/test_interposition.py` and both must use one instrument.
This script is the provenance: it prints the measurement, which is a wider
report than the guard needs.

Run it the way the suite is run, because `django.template.base` must be ours:

    . scripts/env.sh
    PYTHONPATH="$PWD/scripts/shimpath:$PWD/.django" \\
        python .review/m9-measurements/m9_measure_parse_path.py
"""

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHECKOUT = os.path.join(ROOT, ".django")

if os.environ.get("DJANGO_RUST_TEMPLATES") != "1":
    sys.exit("DJANGO_RUST_TEMPLATES is not 1; the shim would not be installed")

sys.path.insert(0, os.path.join(ROOT, "scripts", "shimpath"))
sys.path.insert(0, CHECKOUT)
sys.path.insert(0, ROOT)

import django_rust_templates  # noqa: E402

django_rust_templates.install()

from django_rust_templates import _shim  # noqa: E402

if not _shim.installed():
    sys.exit("shim is not installed")

import django.template.base as base  # noqa: E402

if "django.template.base" in _shim.unshimmed():
    sys.exit("django.template.base is not ours; this measurement is meaningless")

from tests.compared_path import definitions, measure  # noqa: E402
from tests.parser_corpus import (  # noqa: E402
    RANDOM_CASES,
    RANDOM_SEED,
    TEMPLATES,
    random_template,
)
from tests.test_parser_parity import django_parse  # noqa: E402

# The same two passes `tests/test_parser_parity.py` makes: the hand-written
# corpus, then the seeded generator. Measured separately, because if the
# hand-written corpus alone reached the same set a re-measurement row could be
# cheap — it does not, so the row pays for both.
_rng = random.Random(RANDOM_SEED)
generated = [random_template(_rng) for _ in range(RANDOM_CASES)]

by_hand = measure(base, TEMPLATES, django_parse)
by_generator = measure(base, generated, django_parse)

print(f"hand-written corpus ({len(TEMPLATES)}): {len(by_hand)} function(s)")
print(f"generated corpus ({len(generated)}): {len(by_generator)} function(s)")
only_generated = sorted(set(by_generator) - set(by_hand))
print("reached only by the generator:", only_generated or "none")
print()

reached = dict(by_hand)
reached.update(by_generator)

all_defs = definitions(base)
ours = {name: span for name, span in all_defs.items() if span is not None}
print(f"{len(reached)} of {len(ours)} function(s) the module defines were executed")
print()
print("ours-side span   function")
for name, span in reached.items():
    print(f"  {span[0]:5d}-{span[1]:<5d}    {name}")
print()
print("for scripts/check_transcriptions.py's COMPARED_PATH['base.py']:")
print("    [")
for name, span in reached.items():
    print(f'        ({span[0]}, {span[1]}, "{name}"),')
print("    ],")
