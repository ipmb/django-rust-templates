"""Which functions in an interposed module does the parity harness execute?

The module is an argument (``base.py`` by default), because M10 added nine more
``COMPARED_PATH`` entries and one re-measurement instrument beats ten scripts.
It was hardcoded to ``base.py`` when M9 was the only milestone with a pin;
`tests/test_interposition.py`'s stale-pin failure message names this script for
every entry now, so it has to be able to answer for every entry.

    python -I .review/m9-measurements/m9_measure_parse_path.py [module ...]

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

import importlib
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

from scripts.check_transcriptions import module_of  # noqa: E402

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

for name in sys.argv[1:] or ["base.py"]:
    dotted = module_of(name)
    module = importlib.import_module(dotted)

    # The measurement is meaningless if the module is not ours: `measure` would
    # count entries into Django's functions and the pin would be a pin on the
    # fallback. The same guard M9 had, generalised off `base`.
    if dotted in _shim.unshimmed():
        sys.exit(f"{dotted} is not ours; this measurement is meaningless")

    by_hand = measure(module, TEMPLATES, django_parse)
    by_generator = measure(module, generated, django_parse)

    print(f"=== {name} ({dotted}) ===")
    print(f"hand-written corpus ({len(TEMPLATES)}): {len(by_hand)} function(s)")
    print(f"generated corpus ({len(generated)}): {len(by_generator)} function(s)")
    only_generated = sorted(set(by_generator) - set(by_hand))
    print("reached only by the generator:", only_generated or "none")

    reached = dict(by_hand)
    reached.update(by_generator)

    all_defs = definitions(module)
    ours = {label: span for label, span in all_defs.items() if span is not None}
    print(f"{len(reached)} of {len(ours)} function(s) the module defines were executed")
    print()
    print("ours-side span   function")
    for label, span in sorted(reached.items(), key=lambda kv: kv[1]):
        print(f"  {span[0]:5d}-{span[1]:<5d}    {label}")
    print()
    print(f"for scripts/check_transcriptions.py's COMPARED_PATH['{name}']:")
    print("    [")
    for label, span in sorted(reached.items(), key=lambda kv: kv[1]):
        print(f'        ({span[0]}, {span[1]}, "{label}"),')
    print("    ],")
    print()
