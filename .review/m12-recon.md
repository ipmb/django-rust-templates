# M12 recon — interpose the five `django/templatetags` libraries

Scope, from §10.3's row and §7's census: `i18n`, `l10n`, `static`, `tz`, `cache`.
Everything below is measured on `.django/` @ **6.0.9** with the shim installed,
unless it says otherwise. Instruments: `.review/m12-measurements/
m12_measure_tag_routing.py` and `m12_measure_library_census.py`, both committed
with this doc.

---

## 1. The headline: all 18 tags are Django's, end to end

With the shim installed *today*, compiling `{% load <lib> %}{% <tag> … %}`
through the suite's own engine gives one verdict for every one of the 18
registered tag names:

```
18  theirs/theirs
```

The compile function is `django.templatetags.<lib>`'s, and so is the node class
it builds. Not one tag in these five libraries is on our path — filter or node,
compile or render. The 9 filters are the same: every one reports `ours=False`.

That is the milestone's whole starting position, and it is worth stating in the
shape M11 stated its own: at M11 the *filter bodies* were already Rust's and the
gap was ownership of the registry; here there is no Rust body and no ownership,
so M12 is the one milestone that starts from zero on both sides of the seam.

### The gates today are Django's code

`syntax_tests/i18n/*` (102 rows), `test_cache.py` (18), `test_static.py` (12)
and `test_invalid_string.py` (8) — **140 rows** — run Django's compile functions
and Django's node classes. They are not *inert*: our parser has driven the parse
loop since M9, and `ShellHost.call_tag` (`host.py:308`) is what calls down into
Django's Python compile function, so those rows do exercise our parser's
host-call seam. What they do not exercise is anything of ours in the tag. That
distinction is §10.3's vacuity rule applied honestly, and §7 below records it.

---

## 2. The census, measured, and the three counts it reconciles

`m12_measure_library_census.py`, reading the pinned files with `ast`:

| module | lines | classes | `Node` subclasses | module-level `def`s |
|---|---:|---:|---:|---:|
| `i18n.py` | 616 | 8 | 8 | 12 |
| `l10n.py` | 60 | 1 | 1 | 3 |
| `static.py` | 179 | 2 | 2 | 4 |
| `tz.py` | 200 | 4 | **3** | 6 |
| `cache.py` | 100 | 1 | 1 | 1 |
| **total** | **1155** | **16** | **15** | **26** |

Registry, read from the imported pinned modules rather than from a grep:
**18 tag names** (i18n 10, static 3, tz 3, cache 1, l10n 1) and **9 filters**
(i18n 4, l10n 2, tz 3).

Three counts that have been quoted in this project and are *all* right about
different questions:

* the spec's "**15 more node classes** in `django/templatetags/*.py`" is the
  `Node`-subclass column, and it is 15 — `tz.py`'s fourth class is
  `datetimeobject`, a `datetime` subclass, which is why the class column reads
  16;
* "**1155 lines**" is `splitlines()` over the five files;
* "**26 module defs**" is *not* the registry: 26 is every module-level `def`,
  including the 9 filter bodies and `localize_tag`/`localtime_tag`/
  `timezone_tag`/`get_current_timezone_tag`, which are helper wrappers the
  `register.filter` decorators are applied to. The registry is 18 + 9 = 27
  entries over 26 defs plus `do_cache` — the shape M11 was burned by, one
  namespace over.

---

## 3. How a tag reaches Rust today, and why M12 cannot reuse it by name

The tag path is **not** the filter path, and the difference decides the design.

`ShellHost.has_tag` (`host.py:290`):

```python
func = self.parser.tags.get(command)
if func is None:
    return False
if command not in self._rust_owned:
    return True                       # host compiles it (Django's Python)
return func is not self._django_tags.get(command)
```

with `_rust_owned = set(_native.builtin_tag_names_including_stubs()) - set(
_native.builtin_tag_stub_names())` — **25 names** — and
`_django_tags = {**defaulttags.register.tags, **loader_tags.register.tags}`
(`host.py:93`).

So ownership is decided by **name**, with one escape hatch: if the parser's
callable for a Rust-owned name is no longer Django's own, something rebound it
(a `{% load %}`, or `OPTIONS["builtins"]`) and the host compiles instead. That
escape is what makes the M11 rule hold for tags too — the library wins.

**The trap for M12 is that the escape hatch inverts for a library tag.** For
`trans`, `_django_tags.get("trans")` is `None`, because `django.templatetags.i18n`
is not in that dict. So if M12 only added the 18 names to Rust's tag registry,
`has_tag("trans")` would take the `command in _rust_owned` branch, ask
`func is not None` → **True** → and hand `trans` to the *host*, i.e. to Django's
`do_translate`, which is exactly what happens today. Adding the names alone
changes nothing at all, and it changes nothing *silently* — the suite stays
green.

Widening `django_own_tags()` to the five library modules is therefore a
**precondition**, not a tidy-up: it is what makes the identity comparison mean
"is this callable still the library's own", which is the question that decides
whether Rust compiles. And it must be widened to the five modules whether or not
they are interposed — `django_own_tags()` reads whichever registry is in force,
so after interposition it reads ours.

**Which answers the shape question the filter arm raised.** Filters got away with
one flat table because `django_own_filters()` is `defaultfilters.register.filters`
inverted and *that is the only registry built-in filters live in*. Tags are not
one table: `_django_tags` is a hand-written merge of exactly two modules'
registries, and a library tag is in neither. So `django.templatetags` needs
`django_own_tags()` extended, and the extension cannot be a name list — it has to
be the five modules' own `register.tags`, for the same reason M11 refuses a
name-keyed filter map: `{% load %}` rebinds, and only identity can tell a rebind
from the original.

---

## 4. The namespace is **forced**, and by three lines of the gate

The tempting cheaper shape is to leave `TARGET` alone and intercept one level
down — have our `Engine.get_template_libraries` / `import_library` hand back our
`Library`, so `{% load i18n %}` gets our tags without
`django.templatetags.i18n` ever resolving to our module. It would cost nothing in
the shim and nothing in the meter, and it does not work. Three gate rows bind the
node class **by name, at import time**:

```
.django/tests/template_tests/syntax_tests/test_static.py:5
    from django.templatetags.static import StaticNode
.django/tests/template_tests/syntax_tests/i18n/test_translate.py:7
    from django.templatetags.l10n import LocalizeNode
.django/tests/template_tests/syntax_tests/i18n/test_blocktranslate.py:9
    from django.templatetags.i18n import BlockTranslateNode
```

The assertions downstream of those three are identity and `isinstance`, so
`django.templatetags.static.StaticNode` must **be** our class — and a
library-level interception leaves it Django's, because the name was bound when
the test module was executed and nothing done to a `Library` afterwards can
change it. The module object is what has to move. This is the *reason* the shim
grows a second namespace, and it is a measurement rather than an architectural
preference, which is the difference between a later milestone accepting the
extension and re-litigating it.

Measured call sites of `TARGET` in `_shim.py`:

| site | function | what it does with it |
|---|---|---|
| 75 | module level | the definition, `"django.template"` |
| 219-222 | `_relative_name` | `==` / `startswith` / slice — the whole mapping |
| 246-255 | `_real_search_paths` | `find_spec`, and three error messages |
| 435, 497 | `install` | `importlib.import_module(TARGET)` |
| 518 | `unshimmed` | `f"{TARGET}.{name}"` |

And the measured consequences of the single namespace:

```
_relative_name('django.templatetags.i18n')  ->  None
_served('django.templatetags.i18n')         ->  False
```

**Half the machinery is already prepared for this namespace.** `_CAPTURE_NAMESPACES`
(`_shim.py:282`) is `("django.template", "django.templatetags")`, and the docstring
above `_stale_captures` names the reason in Django's own source: a captured
**enum member** from *"`django/templatetags/i18n.py:5` imports `TokenType`"*.
That is prior art in this tree for exactly the extension M12 needs, and the
capture hazard it addresses becomes live the moment the five modules move
(§6).

### The forward hazard: the keys, not the lookup

Two halves of the machinery react differently to a second namespace, and
knowing which is which before starting is most of the task's risk.

*Namespace-agnostic already, and not by design:*
`check_transcriptions.module_of` derives the dotted name from the **declared
upstream path** (`DECLARED[name][0]` — `django/template/base.py` →
`django.template.base`), so `django/templatetags/i18n.py` becomes
`django.templatetags.i18n` with no change at all. It happens to survive this.

*`django.template`-specific, and it is every key in every table:* `_relative_name`,
and with it the `_impl`-relative posix keys of `SHADOWING`, `DECLARED`,
`COMPARED_PATH` and the anchor manifest. Two decisions follow, and both are
stated rather than left implicit:

1. the five land under **`_impl/templatetags/`**, keyed `templatetags/i18n.py`
   and so on — `loaders/base.py` already taught this tree that a key must carry
   its subdirectory, and M10 Task 6 paid for learning it late;
2. the root → `_impl` subdirectory relationship becomes an **explicit table**
   (`django.template` → flat, `django.templatetags` → `templatetags/`) rather
   than string slicing from `_relative_name`. The asymmetry is ugly and it is
   the right call: re-keying `django.template` under `_impl/template/` for
   symmetry would move every row in four tables and every anchor, and a stated
   wart is cheaper than a tidy re-key that costs four tables of churn.

---

## 5. The meter is an **acceptance criterion**, and M12 is the second amendment to it

Measured:

```
unshimmed() -> 11 names, every one django.template.*
django_modules() -> 26 names, none of them templatetags
```

This is not a discovery. `tests/milestone_ownership.py` already carries the
decision, written when the partition was made at M10:

> §10.3's M12 row owns no meter entry: `i18n`, `l10n`, `static`, `tz` and
> `cache` are `django/templatetags/*`, outside the namespace the meter covers.
> M12 moves tag bodies and retires nothing here.

**The first thing to correct in my own framing: `unshimmed()` is not a progress
display.** §10.1's sentence and §10.3's M14 row both state *`unshimmed()` empty
under strict mode* as the project's end state — it is an acceptance criterion,
with the 1547 as the other one. So adding a namespace does not merely leave a
number unlabelled; it changes **what M14 must be true of**. A second count with
no stated relationship to M14 makes the criterion ambiguous: must both be empty,
only the first, or is the second advisory?

And that sentence **already has one unresolved amendment pending**. §2's goal
question records the measurement that no row reaches zero without interposing
glue §6's vendoring policy refuses, with `autoreload` and `context_processors`
sitting in `UNDECIDED`, one reason each. M12 would make it *two* amendments, from
two milestones, arriving separately. They should be reported together, as one
decision with two parts — because separately they read as two small wordings and
together they are the question *"what exactly is this project claiming when it
says it is done?"*, which is the one question the acceptance criterion exists to
answer and unambiguously the user's to decide.

### The options, with the cost of each

1. **A second, separately-named count over a second named population.**
   `unshimmed()` keeps its meaning exactly — the same 11, still comparable to
   the 25 → 23 → 14 → 11 the spec's table records — and the new number starts at
   5 and ends at 0. Cost: `django_modules()` gains a scope, and
   `tests/test_milestone_ownership.py`'s partition assertion needs either a
   second universe or an explicit statement that M12's names are outside its
   scope.
2. **Generalize the meter and renumber the table.** Same facts, one number — but
   it changes what `unshimmed()` *returns*, so four milestones of recorded
   evidence stop meaning what they meant.
3. **Leave the meter alone; give M12 a direct-entry instrument** of the shape M8
   used when its gate was vacuous — rows that compile each of the 18 tags and
   assert the compile function and the node class are ours, i.e. §1's routing
   table promoted to a test. Cost: it is a row about *ownership*, not about
   behaviour, so it cannot see a wrong body.

**Why 1 is not the two-right-answers trap this project keeps hitting.** That
family — 26/27, 36/55, 32/34, 7/18/31/26, 821/360 — was never two numbers; it was
*one* number whose scope was not stated, and the remedy each time was to name the
granularity, the counting unit and the scope. Two separately-named counts over
two named populations **is** that remedy. The trap here would be the merge:
folding five modules into `unshimmed()` would make 11 incomparable to the 14, 13
and 12 before it with nothing in the number saying so.

**One implementation, two populations.** The recurring defect in this tree is not
a wrong number but *a claim in two places drifting* — D3′, the suite-count
provenance, the vacuity reasons, the M10 row's `origin`. Two implementations of
"which modules in namespace N are still Django's" would drift the first time one
learnt about a subpackage and the other did not, and `loaders/` already showed
that a namespace walker needs teaching. So: **one function with a scope
parameter**, `unshimmed()` preserved as the default scope so that no existing
caller or doc changes meaning.

**Recommendation: 1 + 3** — the second count for the progress claim, the routing
table as a row because a count cannot tell a wrong body from a right one — with
M14 stated to read on the existing count and to *name* the second. This goes to
the user together with §2's pending question, not separately.

---

## 6. Import graph and the capture hazard

Every one of the five captures objects out of the namespace the shim owns:

| module | captures from `django.template*` |
|---|---|
| `i18n.py` | `Library, Node, TemplateSyntaxError, Variable` (`from django.template`) and `TokenType, render_value_in_context` (`from django.template.base`) |
| `l10n.py` | `Library, Node, TemplateSyntaxError` |
| `static.py` | `template` (module handle) |
| `tz.py` | `Library, Node, TemplateSyntaxError` |
| `cache.py` | `Library, Node, TemplateSyntaxError, VariableDoesNotExist` |

Executed *after* install these are ours — measured today: the routing table's
uniform `theirs/theirs` is about *which file defines the class*, not about
capture, and `_stale_captures()` reports nothing for the five modules as they
stand.

The hazard is on the other side of the move. The five modules are executed
lazily, on `{% load %}` — `django.template.engine.Engine.default_builtins`
(`engine.py:14-18`) is `defaulttags`, `loader_tags`, `defaultfilters` and names
none of them — so in the accepted configuration they always run after install
and there is no live stale-capture path. But **our `_impl/templatetags/*.py` will
import from `django.template` too**, and the moment they are `_served` they fall
out of `_stale_captures()`'s sweep (`_shim.py:322-323` skips served modules).
That is correct — a module we serve is not a *capture* — but it means the module
whose captures would go stale is no longer watched, and the watch has to be
re-established for the new name by whatever asserts the five are ours.

The dependencies outside the template engine are the honest scope line, and
they are the same list §6's framework-coupling test exists for. Measured, per
module, the `django.*` names imported from outside `django.templatetags`:

* `i18n` → `django.utils.translation`, `django.utils.safestring`, `django.conf`
* `l10n` → `django.utils.formats`
* `static` → `django.apps`, `django.contrib.staticfiles.storage`, `django.utils.encoding`, `django.utils.html`, `django.conf`
* `tz` → `django.utils.timezone`
* `cache` → `django.core.cache`, `django.core.cache.utils`

Translations catalogs, number formats, staticfiles storage, timezone data and
cache backends are **not** the template engine and M12 does not port them —
exactly as `_impl/defaulttags.py` delegates `{% url %}` to `django.urls` and
`{% now %}` to `django.utils.timezone`. What M12 owns is the **tag's parse-time
body and its node's render body**, not the subsystem it calls.

---

## 7. The gate, read through §10.3's vacuity rule

The rule's question, asked of each row: *is the module whose behaviour this gate
exercises interposed by this milestone?*

| gate | rows | imported by name |
|---|---:|---|
| `syntax_tests/i18n/test_blocktranslate.py` | 47 | `BlockTranslateNode` |
| `syntax_tests/i18n/test_translate.py` | 25 | `TranslateNode` |
| `syntax_tests/i18n/test_underscore_syntax.py` | 14 | — |
| `syntax_tests/i18n/test_get_language_info_list.py` | 4 | — |
| `syntax_tests/i18n/test_get_language_info.py` | 4 | — |
| `syntax_tests/i18n/test_filters.py` | 3 | `language_name`, … |
| `syntax_tests/i18n/test_get_available_languages.py` | 2 | — |
| `syntax_tests/i18n/test_get_current_language.py` | 1 | — |
| `syntax_tests/i18n/test_get_current_language_bidi.py` | 1 | — |
| `syntax_tests/i18n/test_language.py` | 1 | `LocalizeNode`? no — `LanguageNode` |
| `syntax_tests/test_cache.py` | 18 | — |
| `syntax_tests/test_static.py` | 12 | `StaticNode` |
| `syntax_tests/test_invalid_string.py` | 8 | — |
| **total** | **140** | |

`i18n` 102, cache 18, static 12, invalid-string 8.

**For M12 the gate is vacuous until the milestone makes it not.** These rows name
modules M12 *does* interpose, which is the opposite of M7's case — the rule's
obligation is discharged by the interposition itself, and M12 is the milestone
that converts 140 previously-vacuous rows into evidence. That also means the
rows are the acceptance instrument and the plan does not need to invent a
lookalike; what the plan *does* need is the ownership row of §5 option 3,
because 140 green rows cannot distinguish our `BlockTranslateNode` from
Django's, which is the state they have been in all along.

The tests that import a node class **by name** are the sharp edge, and they are
what *forces* the namespace extension rather than merely permitting it — §4 for
the three citations. The corollary for the ownership row: `inspect.getsourcefile`
-style reasoning is unreliable here for the reason M9 recorded
(`sys.modules[cls.__module__]` resolves a class through the *importing* module),
so the row must compare *objects reached through the template's nodelist*, the
way §1's instrument does, and not by reading a module's `__file__` and hoping.

---

## 8. Open questions the plan must answer, not inherit

1. **Scope: tag bodies, or full interposition?**
   `tests/milestone_ownership.py`'s existing note says M12 "moves tag bodies".
   Two readings, and they differ by a lot of work:
   * **(a) Rust compile functions only.** 18 Rust compile functions, 9 Rust
     filter bodies, `django_own_tags()` widened, the 18 names added to Rust's
     registry. Node classes stay in `django.templatetags.*`, so the render
     bodies stay Django's Python and the verdicts land at `ours/theirs`.
   * **(b) transcribe the five modules too** — `_impl/templatetags/`, the
     namespace extension, five new `SHADOWING` entries, declared regions, and
     then (a) on top. Verdicts land at `ours/ours`.
   The project's trajectory is (b): every milestone since M9 has moved the
   module, and the standing finding is that *the Python node tree is the render
   path* — owning the node class is what owning the tag means. And §4's
   measurement settles it rather than leaving it to taste: the three by-name
   imports mean the module object *must* move whether or not (a) is also done,
   so (a) alone is not a smaller version of (b) but a strictly weaker one that
   pays most of the cost. The plan states this and the reason.
2. **The instrument** for a milestone with no meter entry, *and* the M14 wording
   it changes — §5. This is the one open question that is the user's rather than
   the plan's.
3. **The 9 filters.** They are registered on the same libraries and reachable
   the same way (`{% load i18n %}{{ x|language_name }}`). Rust has 57 filter
   bodies; are these 9 in M12 or a later milestone? The routing predicate for
   filters is `django_own_filters()`, which is built from `defaultfilters`
   alone — so the 9 need the same widening `django_own_tags()` needs, and the
   M11 lesson applies unchanged: keyed on the registry object, not on emptiness.
4. **The reviewer's agreement gate** carried over from M11's close-out: sweep
   vs frames per pinned module, with a positive control and an empty-unattributed
   assertion.

---

## 9. Two instrument traps found while measuring this

Both were hit, not imagined, and both are the kind that produce a *confident
wrong answer* rather than an error.

**A bare `Engine()` has no installed libraries.** The built-in libraries are
merged by the backend, not the engine: `Engine.get_template_libraries` loads
only what `settings.LIBRARIES` names, and it is
`DjangoTemplates.get_templatetag_libraries` that calls `get_installed_libraries()`
and folds `django.templatetags.*` in. So `Engine().from_string("{% load i18n %}…")`
raises `TemplateSyntaxError: 'i18n' is not a registered tag library. Must be one
of:` **with an empty list**, and the first version of the routing instrument
reported all 18 tags as parse errors for that reason and no other. A probe that
fails to load the library measures the probe. Fixed by building the engine
through `DjangoTemplates` the way the suite does.

**`{% load %}` returns a node.** `do_load` ends `return LoadNode()`
(`defaulttags.py:1131`), so "the first node in the nodelist that is not
`TextNode`" is the *load* node for every template — and the second version of
the instrument reported `theirs/ours` for all 18 names with every node class
attributed to `django.template.defaulttags`. A verdict that does not vary with
its input is not a measurement of its input; the fix is to skip `LoadNode` as
well, and the tell was the uniformity.

Both are recorded in the instruments' docstrings, because the next person to
measure a library tag will reach for `Engine()` and for `nodelist[0]`.
