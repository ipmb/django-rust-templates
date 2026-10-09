# M6 close-out verification order

Computed from the agreement census (see `.review/m6-recon.md`), sorted by
citation count descending — the reviewer's scheduling rule. The order is
durable: the census canonicalises a shorthand and a full path to one key, so
it is **invariant under naming**, and this list stays valid through every
remaining file without recomputation.

163 anchors are cited from two or more files. The head was verified
against `.django/` @ 6.0.9 on 2026-10-09 (`base.py:816`, `:813`, `:669-670`,
`:773`, `defaultfilters.py:46`, `defaulttags.py:929`); the rest are unverified.

The column is the number of *files* citing the anchor, which is the unit the
ordering uses. Occurrence counts are higher — an anchor cited from two files
can be cited twice in one of them.

One caveat on the key: the census canonicalises by *spelling*, so the same
construct written two ways counts twice. Exactly one of the 163 does —
`html.py:115-127` and `django/utils/html.py:115-127` — so merging on
`(file, start, end)` gives 162, and each of that pair shows one citer fewer
than the construct has. It is recorded rather than fixed because a name-based
key would need the resolver, which is the thing the census avoids.

| citers | anchor | line | the line holds |
|---|---|---|---|
| 14 | `base.py:816` | django/template/base.py | `if getattr(func, "is_safe", False) and isinstance(obj, SafeData):` |
| 7 | `base.py:813` | django/template/base.py | `new_obj = func(obj, autoescape=context.autoescape, *arg_vals)` |
| 6 | `base.py:669-670` | django/template/base.py | `self.tags.update(lib.tags)` |
| 6 | `base.py:773` | django/template/base.py | `filters.append((filter_func, args))` |
| 6 | `defaultfilters.py:46` | django/template/defaultfilters.py | `first = str(first)` |
| 6 | `defaulttags.py:929` | django/template/defaulttags.py | `return TemplateLiteral(self.template_parser.compile_filter(value), value)` |
| 5 | `base.py:583-585` | django/template/base.py | `compiled_result = compile_func(self, token)` |
| 5 | `base.py:771-772` | django/template/base.py | `filter_func = parser.find_filter(filter_name)` |
| 5 | `base.py:822` | django/template/base.py | `def args_check(name, func, provided):` |
| 5 | `defaulttags.py:744` | django/template/defaulttags.py | `_, rest = token.contents.split(None, 1)` |
| 4 | `base.py:198-200` | django/template/base.py | `try:` |
| 4 | `base.py:804-809` | django/template/base.py | `arg_vals = []` |
| 4 | `defaultfilters.py:210` | django/template/defaultfilters.py | `if sign and rounded_d:` |
| 4 | `defaultfilters.py:365-379` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 4 | `defaultfilters.py:38-52` | django/template/defaultfilters.py | `def stringfilter(func):` |
| 4 | `defaultfilters.py:386` | django/template/defaultfilters.py | `return mark_safe(_urlize(value, nofollow=True, autoescape=autoescape))` |
| 4 | `defaultfilters.py:399` | django/template/defaultfilters.py | `_urlize(value, trim_url_limit=int(limit), nofollow=True, autoescape=autoescape)` |
| 4 | `defaultfilters.py:602-608` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 4 | `defaultfilters.py:990-996` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 4 | `defaulttags.py:1065-1075` | django/template/defaulttags.py | `def find_library(parser, name):` |
| 4 | `defaulttags.py:323` | django/template/defaulttags.py | `for condition, nodelist in self.conditions_nodelists:` |
| 4 | `defaulttags.py:326` | django/template/defaulttags.py | `match = condition.eval(context)` |
| 4 | `defaulttags.py:669-671` | django/template/defaulttags.py | `if name not in parser._named_cycle_nodes:` |
| 4 | `defaulttags.py:917-918` | django/template/defaulttags.py | `def eval(self, context):` |
| 4 | `html.py:407-410` | django/utils/html.py | `def trim_url(self, x, *, limit):` |
| 4 | `smartif.py:179-185` | django/template/smartif.py | `def translate_token(self, token):` |
| 3 | `base.py:1202` | django/template/base.py | `kwargs[key] = parser.compile_filter(value)` |
| 3 | `base.py:518-519` | django/template/base.py | `if builtins is None:` |
| 3 | `base.py:619-630` | django/template/base.py | `def error(self, token, e):` |
| 3 | `base.py:651` | django/template/base.py | `command, token = self.command_stack.pop()` |
| 3 | `base.py:676` | django/template/base.py | `return FilterExpression(token, self)` |
| 3 | `base.py:755` | django/template/base.py | `var_obj = Variable(constant).resolve({})` |
| 3 | `base.py:768` | django/template/base.py | `args.append((False, Variable(constant_arg).resolve({})))` |
| 3 | `base.py:771` | django/template/base.py | `filter_func = parser.find_filter(filter_name)` |
| 3 | `base.py:815` | django/template/base.py | `new_obj = func(obj, *arg_vals)` |
| 3 | `base.py:819` | django/template/base.py | `obj = new_obj` |
| 3 | `base.py:822-826` | django/template/base.py | `def args_check(name, func, provided):` |
| 3 | `base.py:972-976` | django/template/base.py | `if isinstance(current, BaseContext) and getattr(` |
| 3 | `defaultfilters.py:168-170` | django/template/defaultfilters.py | `except TypeError:` |
| 3 | `defaultfilters.py:180` | django/template/defaultfilters.py | `m = int(d) - d` |
| 3 | `defaultfilters.py:204` | django/template/defaultfilters.py | `rounded_d = d.quantize(exp, ROUND_HALF_UP, Context(prec=prec))` |
| 3 | `defaultfilters.py:624-630` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 3 | `defaultfilters.py:633-639` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 3 | `defaultfilters.py:642-648` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 3 | `defaultfilters.py:745-754` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 3 | `defaultfilters.py:757-775` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 3 | `defaultfilters.py:854-857` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 3 | `defaultfilters.py:996` | django/template/defaultfilters.py | `return "Error in formatting: %s: %s" % (e.__class__.__name__, e)` |
| 3 | `defaulttags.py:106-112` | django/template/defaulttags.py | `# First time the node is rendered in template` |
| 3 | `defaulttags.py:265` | django/template/defaulttags.py | `self._varlist = varlist` |
| 3 | `defaulttags.py:693-696` | django/template/defaulttags.py | `node = CycleNode(values, name, silent=silent)` |
| 3 | `html.py:103` | django/utils/html.py | `json_str = json.dumps(value, cls=encoder or DjangoJSONEncoder).translate(` |
| 3 | `html.py:319` | django/utils/html.py | `safe_input = isinstance(text, SafeData)` |
| 3 | `html.py:44` | django/utils/html.py | `MAX_STRIP_TAGS_DEPTH = 50` |
| 3 | `library.py:211-212` | django/template/library.py | `nodelist = parser.parse((end_name,))` |
| 3 | `smartif.py:81-83` | django/template/smartif.py | `def nud(self, parser):` |
| 3 | `text.py:205` | django/utils/text.py | `text = unicodedata.normalize("NFC", self._wrapped)` |
| 2 | `bad_tag.py:6-8` | tests/template_tests/templatetags/bad_tag.py | `@register.tag` |
| 2 | `base.py:1005` | django/template/base.py | `current = context.template.engine.string_if_invalid` |
| 2 | `base.py:1011` | django/template/base.py | `current = context.template.engine.string_if_invalid` |
| 2 | `base.py:1023` | django/template/base.py | `if getattr(e, "silent_variable_failure", False):` |
| 2 | `base.py:1024` | django/template/base.py | `current = context.template.engine.string_if_invalid` |
| 2 | `base.py:1054` | django/template/base.py | `if context.template.engine.debug:` |
| 2 | `base.py:1160` | django/template/base.py | `kwarg_re = _lazy_re_compile(r"(?:(\w+)=)?(.+)")` |
| 2 | `base.py:504-505` | django/template/base.py | `# next_token() can operate at the end of the list in constant time.` |
| 2 | `base.py:514` | django/template/base.py | `self.extra_data = {}` |
| 2 | `base.py:522-523` | django/template/base.py | `for builtin in builtins:` |
| 2 | `base.py:549-551` | django/template/base.py | `if not token.contents:` |
| 2 | `base.py:564-569` | django/template/base.py | `if command in parse_until:` |
| 2 | `base.py:573` | django/template/base.py | `self.command_stack.append((command, token))` |
| 2 | `base.py:585` | django/template/base.py | `raise self.error(token, e)` |
| 2 | `base.py:615` | django/template/base.py | `node.token = token` |
| 2 | `base.py:669` | django/template/base.py | `self.tags.update(lib.tags)` |
| 2 | `base.py:705` | django/template/base.py | `(?P<filter_name>\w+)` |
| 2 | `base.py:714` | django/template/base.py | `"var_chars": r"\w\.\+-",` |
| 2 | `base.py:770` | django/template/base.py | `args.append((True, Variable(var_arg)))` |
| 2 | `base.py:775` | django/template/base.py | `if upto != len(token):` |
| 2 | `base.py:783` | django/template/base.py | `self.is_var = isinstance(var_obj, Variable)` |
| 2 | `base.py:788-792` | django/template/base.py | `obj = self.var.resolve(context)` |
| 2 | `base.py:793` | django/template/base.py | `string_if_invalid = context.template.engine.string_if_invalid` |
| 2 | `base.py:800-806` | django/template/base.py | `obj = string_if_invalid` |
| 2 | `base.py:906` | django/template/base.py | `self.literal = mark_safe(unescape_string_literal(var))` |
| 2 | `base.py:926-928` | django/template/base.py | `if self.lookups is not None:` |
| 2 | `base.py:968` | django/template/base.py | `except (TypeError, AttributeError, KeyError, ValueError, IndexError):` |
| 2 | `base.py:977` | django/template/base.py | `except (TypeError, AttributeError):` |
| 2 | `base.py:979-980` | django/template/base.py | `if not isinstance(current, BaseContext) and bit in dir(current):` |
| 2 | `base.py:983-988` | django/template/base.py | `except (` |
| 2 | `base.py:997` | django/template/base.py | `current = context.template.engine.string_if_invalid` |
| 2 | `dateformat.py:332-335` | django/utils/dateformat.py | `def time_format(value, format_string):` |
| 2 | `dateformat.py:42` | django/utils/dateformat.py | `for i, piece in enumerate(re_formatchars.split(str(formatstr))):` |
| 2 | `defaultfilters.py:141` | django/template/defaultfilters.py | `last_char = arg[-1]` |
| 2 | `defaultfilters.py:152-159` | django/template/defaultfilters.py | `try:` |
| 2 | `defaultfilters.py:153` | django/template/defaultfilters.py | `input_val = str(text)` |
| 2 | `defaultfilters.py:158` | django/template/defaultfilters.py | `except (ValueError, InvalidOperation, TypeError):` |
| 2 | `defaultfilters.py:161` | django/template/defaultfilters.py | `p = int(arg)` |
| 2 | `defaultfilters.py:163` | django/template/defaultfilters.py | `return input_val` |
| 2 | `defaultfilters.py:177` | django/template/defaultfilters.py | `return input_val` |
| 2 | `defaultfilters.py:180-182` | django/template/defaultfilters.py | `m = int(d) - d` |
| 2 | `defaultfilters.py:181` | django/template/defaultfilters.py | `except (ValueError, OverflowError, InvalidOperation):` |
| 2 | `defaultfilters.py:184-192` | django/template/defaultfilters.py | `if not m and p <= 0:` |
| 2 | `defaultfilters.py:194` | django/template/defaultfilters.py | `exp = Decimal(1).scaleb(-abs(p))` |
| 2 | `defaultfilters.py:200` | django/template/defaultfilters.py | `prec = max(getcontext().prec, prec)` |
| 2 | `defaultfilters.py:205-212` | django/template/defaultfilters.py | `sign, digits, exponent = rounded_d.as_tuple()` |
| 2 | `defaultfilters.py:223-227` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:230-244` | django/template/defaultfilters.py | `@register.filter(is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:266-274` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:305-313` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:330-341` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:382-386` | django/template/defaultfilters.py | `@register.filter(is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:389-400` | django/template/defaultfilters.py | `@register.filter(is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:410-414` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:431-438` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:445` | django/template/defaultfilters.py | `safe = isinstance(value, SafeData)` |
| 2 | `defaultfilters.py:485-494` | django/template/defaultfilters.py | `@register.filter("linebreaks", is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:497-508` | django/template/defaultfilters.py | `@register.filter(is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:528-532` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:540-575` | django/template/defaultfilters.py | `def _property_resolver(arg):` |
| 2 | `defaultfilters.py:578-587` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 2 | `defaultfilters.py:590-599` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 2 | `defaultfilters.py:611-621` | django/template/defaultfilters.py | `@register.filter(is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:651-666` | django/template/defaultfilters.py | `@register.filter("slice", is_safe=True)` |
| 2 | `defaultfilters.py:669-737` | django/template/defaultfilters.py | `@register.filter(is_safe=True, needs_autoescape=True)` |
| 2 | `defaultfilters.py:840-843` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 2 | `defaultfilters.py:85-91` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:860-891` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 2 | `defaultfilters.py:905-907` | django/template/defaultfilters.py | `try:` |
| 2 | `defaultfilters.py:906` | django/template/defaultfilters.py | `bytes_ = int(bytes_)` |
| 2 | `defaultfilters.py:911-912` | django/template/defaultfilters.py | `def filesize_number_format(value):` |
| 2 | `defaultfilters.py:922` | django/template/defaultfilters.py | `bytes_ = -bytes_  # Allow formatting of negative numbers.` |
| 2 | `defaultfilters.py:924-935` | django/template/defaultfilters.py | `if bytes_ < KB:` |
| 2 | `defaultfilters.py:925` | django/template/defaultfilters.py | `value = ngettext("%(size)d byte", "%(size)d bytes", bytes_) % {"size": bytes_}` |
| 2 | `defaultfilters.py:94-220` | django/template/defaultfilters.py | `@register.filter(is_safe=True)` |
| 2 | `defaultfilters.py:942-981` | django/template/defaultfilters.py | `@register.filter(is_safe=False)` |
| 2 | `defaultfilters.py:95` | django/template/defaultfilters.py | `def floatformat(text, arg=-1):` |
| 2 | `defaultfilters.py:984-987` | django/template/defaultfilters.py | `@register.filter("phone2numeric", is_safe=True)` |
| 2 | `defaulttags.py:1286` | django/template/defaulttags.py | `@register.simple_tag(name="querystring", takes_context=True)` |
| 2 | `defaulttags.py:1441-1443` | django/template/defaulttags.py | `return ResetCycleNode(parser._last_cycle_node)` |
| 2 | `defaulttags.py:1575-1577` | django/template/defaulttags.py | `match = kwarg_re.match(bit)` |
| 2 | `defaulttags.py:1676` | django/template/defaulttags.py | `extra_context = token_kwargs(remaining_bits, parser, support_legacy=True)` |
| 2 | `defaulttags.py:1682-1684` | django/template/defaulttags.py | `raise TemplateSyntaxError(` |
| 2 | `defaulttags.py:262` | django/template/defaulttags.py | `def __init__(self, nodelist_true, nodelist_false, *varlist):` |
| 2 | `defaulttags.py:745` | django/template/defaulttags.py | `filter_expr = parser.compile_filter("var\|%s" % (rest))` |
| 2 | `defaulttags.py:886` | django/template/defaulttags.py | `loopvars = re.split(r" *, *", " ".join(bits[1:in_index]))` |
| 2 | `defaulttags.py:922` | django/template/defaulttags.py | `error_class = TemplateSyntaxError` |
| 2 | `defaulttags.py:993` | django/template/defaulttags.py | `condition = TemplateIfParser(parser, bits).parse()` |
| 2 | `django/utils/html.py:115-127` | django/utils/html.py | `def conditional_escape(text):` |
| 2 | `engine.py:194` | django/template/engine.py | `extra_data = getattr(template, "extra_data", {})` |
| 2 | `engine.py:63` | django/template/engine.py | `self.template_builtins = self.get_template_builtins(self.builtins)` |
| 2 | `formats.py:113` | django/utils/formats.py | `format_type = str(format_type)  # format_type may be lazy.` |
| 2 | `html.py:115-127` | django/utils/html.py | `def conditional_escape(text):` |
| 2 | `html.py:181-197` | django/utils/html.py | `class MLStripper(HTMLParser):` |
| 2 | `html.py:210-229` | django/utils/html.py | `@keep_lazy_text` |
| 2 | `html.py:47` | django/utils/html.py | `long_open_tag_without_closing_re = _lazy_re_compile(r"<[a-zA-Z][^>]{1000,}")` |
| 2 | `html.py:88-92` | django/utils/html.py | `_json_script_escapes = {` |
| 2 | `html.py:95-112` | django/utils/html.py | `def json_script(value, element_id=None, encoder=None):` |
| 2 | `library.py:454` | django/template/library.py | `args.append(parser.compile_filter(bit))` |
| 2 | `safestring.py:64-78` | django/utils/safestring.py | `@keep_lazy(SafeString)` |
| 2 | `smartif.py:54-56` | django/template/smartif.py | `def led(self, left, parser):` |
| 2 | `smartif.py:55-56` | django/template/smartif.py | `self.first = left` |
| 2 | `test_unordered_list.py:143` | tests/template_tests/filter_tests/test_unordered_list.py | `unordered_list(item_generator()),` |
| 2 | `text.py:220` | django/utils/text.py | `if unicodedata.combining(char):` |
| 2 | `timesince.py:138-143` | django/utils/timesince.py | `def timeuntil(d, now=None, time_strings=None, depth=2):` |
| 2 | `timesince.py:67-70` | django/utils/timesince.py | `if not now:` |
