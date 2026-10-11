"""The implementations the native walk stands in for, captured at import.

``{% include %}`` ends in ``template.render(context)``, and the native walk
answers that call itself rather than making it. That is only the same thing
while the target's ``render`` and ``_render`` still resolve to the two functions
below — the ones ``engine/base.py`` defines and the walk reproduces.

**Read here, once, and never again.** A second read is what turns the check
into a follow: measured, rebinding ``Template.render`` to a wrapper that adds
brackets leaves a slot that rereads the class rendering ``[x]`` where stock
Django renders ``[[x]]``. The wrapper is not an implementation Rust reproduces,
so a target carrying it has to decline the slot and go back to Python.

**The capture point is the import of this module**, which is why
``django_rust_templates/rust.py`` names it: that file is the one door into the
package — ``EngineHandler`` resolves it before any template is loaded — so
nothing can rebind either name ahead of the capture. Reaching the engine
directly, as the test suite does, and rebinding before the first render is the
one shape that gets past this; it is not a shape a project can be in.
"""

import types

from .base import Template

RENDER = Template.render
PLAN = Template._render

# ``types.MethodType`` is CPython's ``method``, the type ``target.render`` has when the lookup
# really did find a function on the type. It cannot be subclassed, so an exact type test is the
# whole question.
METHOD_TYPE = types.MethodType
