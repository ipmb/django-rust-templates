"""
# TRANSCRIBED: django/template/base.py (Django 6.0.9)

This is the Django template system.

How it works:

The Lexer.tokenize() method converts a template string (i.e., a string
containing markup with custom template tags) to tokens, which can be either
plain text (TokenType.TEXT), variables (TokenType.VAR), or block statements
(TokenType.BLOCK).

The Parser() class takes a list of tokens in its constructor, and its parse()
method returns a compiled template -- which is, under the hood, a list of
Node objects.

Each Node is responsible for creating some sort of output -- e.g. simple text
(TextNode), variable values in a given context (VariableNode), results of basic
logic (IfNode), results of looping (ForNode), or anything else. The core Node
types are TextNode, VariableNode, IfNode and ForNode, but plugin modules can
define their own custom node types.

Each Node has a render() method, which takes a Context and returns a string of
the rendered node. For example, the render() method of a Variable Node returns
the variable's value as a string. The render() method of a ForNode returns the
rendered output of whatever was inside the loop, recursively.

The Template class is a convenient wrapper that takes care of template
compilation and rendering.

Usage:

The only thing you should ever use directly in this file is the Template class.
Create a compiled template object with a template_string, then call render()
with a context. In the compilation stage, the TemplateSyntaxError exception
will be raised if the template doesn't have proper syntax.

Sample code:

>>> from django import template
>>> s = '<html>{% if test %}<h1>{{ varvalue }}</h1>{% endif %}</html>'
>>> t = template.Template(s)

(t is now a compiled template, and its render() method can be called multiple
times with multiple contexts)

>>> c = template.Context({'test':True, 'varvalue': 'Hello'})
>>> t.render(c)
'<html><h1>Hello</h1></html>'
>>> c = template.Context({'test':False, 'varvalue': 'Hello'})
>>> t.render(c)
'<html></html>'
"""

import inspect
import logging
import re
import warnings
from enum import Enum

from .context import BaseContext
from django.conf import settings
from django.utils.deprecation import django_file_prefixes
from django.utils.formats import localize
from django.utils.html import conditional_escape
from django.utils.inspect import lazy_annotations, signature
from django.utils.regex_helper import _lazy_re_compile
from django.utils.safestring import SafeData, SafeString, mark_safe
from django.utils.text import get_text_list, smart_split, unescape_string_literal
from django.utils.timezone import template_localtime
from django.utils.translation import gettext_lazy, pgettext_lazy

from .exceptions import TemplateSyntaxError
from django_rust_templates import _native

# template syntax constants
FILTER_SEPARATOR = "|"
FILTER_ARGUMENT_SEPARATOR = ":"
VARIABLE_ATTRIBUTE_SEPARATOR = "."
BLOCK_TAG_START = "{%"
BLOCK_TAG_END = "%}"
VARIABLE_TAG_START = "{{"
VARIABLE_TAG_END = "}}"
COMMENT_TAG_START = "{#"
COMMENT_TAG_END = "#}"
SINGLE_BRACE_START = "{"
SINGLE_BRACE_END = "}"

# what to report as the origin for templates that come from non-loader sources
# (e.g. strings)
UNKNOWN_SOURCE = "<unknown source>"

# Match BLOCK_TAG_*, VARIABLE_TAG_*, and COMMENT_TAG_* tags and capture the
# entire tag, including start/end delimiters. Using re.compile() is faster
# than instantiating SimpleLazyObject with _lazy_re_compile().
tag_re = re.compile(r"({%.*?%}|{{.*?}}|{#.*?#})")

logger = logging.getLogger("django.template")


class TokenType(Enum):
    TEXT = 0
    VAR = 1
    BLOCK = 2
    COMMENT = 3


class VariableDoesNotExist(Exception):
    def __init__(self, msg, params=()):
        self.msg = msg
        self.params = params

    def __str__(self):
        return self.msg % self.params


class Origin:
    def __init__(self, name, template_name=None, loader=None):
        self.name = name
        self.template_name = template_name
        self.loader = loader

    def __str__(self):
        return self.name

    def __repr__(self):
        return "<%s name=%r>" % (self.__class__.__qualname__, self.name)

    def __eq__(self, other):
        return (
            isinstance(other, Origin)
            and self.name == other.name
            and self.loader == other.loader
        )

    @property
    def loader_name(self):
        if self.loader:
            return "%s.%s" % (
                self.loader.__module__,
                self.loader.__class__.__name__,
            )


class Template:
    # The native render plan, or `None` (M19 step 2). A class attribute rather
    # than an instance one because `Template` is built by `__new__` in more than
    # one place — `engine/parser.py::template_for`, and every hand-built fixture
    # in the suite — and a missing attribute would be an `AttributeError` in
    # `_render` rather than a fallback. `None` is "render the nodelist", which is
    # exactly what this class did before the walk existed.
    _native_plan = None

    def __init__(self, template_string, origin=None, name=None, engine=None):
        # If Template is instantiated directly rather than from an Engine and
        # exactly one Django template engine is configured, use that engine.
        # This is required to preserve backwards-compatibility for direct use
        # e.g. Template('...').render(Context({...}))
        if engine is None:
            from .engine import Engine

            engine = Engine.get_default()
        if origin is None:
            origin = Origin(UNKNOWN_SOURCE)
        self.name = name
        self.origin = origin
        self.engine = engine
        self.source = str(template_string)  # May be lazy.
        self.nodelist = self.compile_nodelist()

    def __repr__(self):
        return '<%s template_string="%s...">' % (
            self.__class__.__qualname__,
            self.source[:20].replace("\n", ""),
        )

    def _render(self, context):
        # The native walk (M19 step 2). `_native_plan` is set by
        # `engine/parser.py::template_for` and copied by `compile_nodelist`; it is
        # `None` for every `Template` this package did not build, which is every
        # hand-built fixture in the test suite, and `None` for a template whose
        # plan the Rust side declined. Both cases are the Python path below,
        # unchanged — a declined plan is a lost optimisation, never a different
        # answer. `_native.render_plan` returns the same `SafeString`
        # `NodeList.render` does.
        plan = self._native_plan
        if plan is not None:
            return _native.render_plan(plan, context)
        return self.nodelist.render(context)

    def render(self, context):
        "Display stage -- can be called many times"
        with context.render_context.push_state(self):
            if context.template is None:
                with context.bind_template(self):
                    context.template_name = self.name
                    return self._render(context)
            else:
                return self._render(context)

    def compile_nodelist(self):
        """
        Parse and compile the template source into a nodelist. If debug
        is True and an exception occurs during parsing, the exception is
        annotated with contextual line information where it occurred in the
        template source.
        """
        # Delegated to Rust (M9): `django_rust_templates.render` binds
        # `template-core`'s lexer and parser to Django's node classes and
        # returns a `Template` built by `__new__`, so this method is not
        # re-entered. The docstring above is upstream's verbatim; the delegation
        # is recorded here rather than in it so that the method's *contract*
        # stays a transcription of upstream's.
        #
        # Everything after the parse is upstream's and stays, because it is this
        # class's contract rather than the engine's: `extra_data`, and an
        # `except` arm that both annotates a debug parse and prefixes a
        # `TemplateSyntaxError` with the origin name when the template has one.
        # That last clause never fires for `Engine.from_string` — its origin is
        # `UNKNOWN_SOURCE` — so only a template loaded from a file reaches it,
        # which is exactly how it would go missing unnoticed.
        from django_rust_templates.render import parse_template

        try:
            parsed = parse_template(self.source, self.engine, self.origin)
            self.extra_data = parsed.extra_data
            # The one line of the delegation that upstream has no counterpart
            # for. `parse_template` returns a `Template` already carrying the
            # plan; upstream returns only the nodelist and this constructor
            # discards the object, so the plan has to be copied across or a
            # `Template(source)` — which is every `Engine.from_string`, and the
            # suite's most common construction — would render on the Python path
            # while the identical `render_string` walked natively.
            self._native_plan = parsed._native_plan
            return parsed.nodelist
        except Exception as e:
            if self.engine.debug:
                e.template_debug = self.get_exception_info(e, e.token)
            if (
                isinstance(e, TemplateSyntaxError)
                and self.origin.name != UNKNOWN_SOURCE
                and e.args
            ):
                raw_message = e.args[0]
                e.raw_error_message = raw_message
                e.args = (f"Template: {self.origin.name}, {raw_message}", *e.args[1:])
            raise

    def get_exception_info(self, exception, token):
        """
        Return a dictionary containing contextual line information of where
        the exception occurred in the template. The following information is
        provided:

        message
            The message of the exception raised.

        source_lines
            The lines before, after, and including the line the exception
            occurred on.

        line
            The line number the exception occurred on.

        before, during, after
            The line the exception occurred on split into three parts:
            1. The content before the token that raised the error.
            2. The token that raised the error.
            3. The content after the token that raised the error.

        total
            The number of lines in source_lines.

        top
            The line number where source_lines starts.

        bottom
            The line number where source_lines ends.

        start
            The start position of the token in the template source.

        end
            The end position of the token in the template source.
        """
        start, end = token.position
        context_lines = 10
        line = 0
        upto = 0
        source_lines = []
        before = during = after = ""
        for num, next in enumerate(linebreak_iter(self.source)):
            if start >= upto and end <= next:
                line = num
                before = self.source[upto:start]
                during = self.source[start:end]
                after = self.source[end:next]
            source_lines.append((num, self.source[upto:next]))
            upto = next
        total = len(source_lines)

        top = max(1, line - context_lines)
        bottom = min(total, line + 1 + context_lines)

        # In some rare cases exc_value.args can be empty or an invalid
        # string.
        try:
            message = str(exception.args[0])
        except (IndexError, UnicodeDecodeError):
            message = "(Could not get exception message)"

        return {
            "message": message,
            "source_lines": source_lines[top:bottom],
            "before": before,
            "during": during,
            "after": after,
            "top": top,
            "bottom": bottom,
            "total": total,
            "line": line,
            "name": self.origin.name,
            "start": start,
            "end": end,
        }


class PartialTemplate:
    """
    A lightweight Template lookalike used for template partials.

    Wraps nodelist as a partial, in order to be able to bind context.
    """

    def __init__(self, nodelist, origin, name, source_start=None, source_end=None):
        self.nodelist = nodelist
        self.origin = origin
        self.name = name
        # If available (debug mode), the absolute character offsets in the
        # template.source correspond to the full partial region.
        self._source_start = source_start
        self._source_end = source_end

    def get_exception_info(self, exception, token):
        template = self.origin.loader.get_template(self.origin.template_name)
        return template.get_exception_info(exception, token)

    def find_partial_source(self, full_source):
        if (
            self._source_start is not None
            and self._source_end is not None
            and 0 <= self._source_start <= self._source_end <= len(full_source)
        ):
            return full_source[self._source_start : self._source_end]

        return ""

    @property
    def source(self):
        template = self.origin.loader.get_template(self.origin.template_name)
        if not template.engine.debug:
            warnings.warn(
                "PartialTemplate.source is only available when template "
                "debugging is enabled.",
                RuntimeWarning,
                skip_file_prefixes=django_file_prefixes(),
            )
        return self.find_partial_source(template.source)

    def _render(self, context):
        return self.nodelist.render(context)

    def render(self, context):
        with context.render_context.push_state(self):
            if context.template is None:
                with context.bind_template(self):
                    context.template_name = self.name
                    return self._render(context)
            else:
                return self._render(context)


def linebreak_iter(template_source):
    yield 0
    p = template_source.find("\n")
    while p >= 0:
        yield p + 1
        p = template_source.find("\n", p + 1)
    yield len(template_source) + 1


class Token:
    def __init__(self, token_type, contents, position=None, lineno=None):
        """
        A token representing a string from the template.

        token_type
            A TokenType, either .TEXT, .VAR, .BLOCK, or .COMMENT.

        contents
            The token source string.

        position
            An optional tuple containing the start and end index of the token
            in the template source. This is used for traceback information
            when debug is on.

        lineno
            The line number the token appears on in the template source.
            This is used for traceback information and gettext files.
        """
        self.token_type = token_type
        self.contents = contents
        self.lineno = lineno
        self.position = position

    def __repr__(self):
        token_name = self.token_type.name.capitalize()
        return '<%s token: "%s...">' % (
            token_name,
            self.contents[:20].replace("\n", ""),
        )

    def split_contents(self):
        split = []
        bits = smart_split(self.contents)
        for bit in bits:
            # Handle translation-marked template pieces
            if bit.startswith(('_("', "_('")):
                sentinel = bit[2] + ")"
                trans_bit = [bit]
                while not bit.endswith(sentinel):
                    bit = next(bits)
                    trans_bit.append(bit)
                bit = " ".join(trans_bit)
            split.append(bit)
        return split


class Lexer:
    def __init__(self, template_string):
        self.template_string = template_string
        self.verbatim = False

    def __repr__(self):
        return '<%s template_string="%s...", verbatim=%s>' % (
            self.__class__.__qualname__,
            self.template_string[:20].replace("\n", ""),
            self.verbatim,
        )

    def tokenize(self):
        """
        Return a list of tokens from a given template_string.
        """
        in_tag = False
        lineno = 1
        result = []
        for token_string in tag_re.split(self.template_string):
            if token_string:
                result.append(self.create_token(token_string, None, lineno, in_tag))
                lineno += token_string.count("\n")
            in_tag = not in_tag
        return result

    def create_token(self, token_string, position, lineno, in_tag):
        """
        Convert the given token string into a new Token object and return it.
        If in_tag is True, we are processing something that matched a tag,
        otherwise it should be treated as a literal string.
        """
        if in_tag:
            # The [0:2] and [2:-2] ranges below strip off *_TAG_START and
            # *_TAG_END. The 2's are hard-coded for performance. Using
            # len(BLOCK_TAG_START) would permit BLOCK_TAG_START to be
            # different, but it's not likely that the TAG_START values will
            # change anytime soon.
            token_start = token_string[0:2]
            if token_start == BLOCK_TAG_START:
                content = token_string[2:-2].strip()
                if self.verbatim:
                    # Then a verbatim block is being processed.
                    if content != self.verbatim:
                        return Token(TokenType.TEXT, token_string, position, lineno)
                    # Otherwise, the current verbatim block is ending.
                    self.verbatim = False
                elif content[:9] in ("verbatim", "verbatim "):
                    # Then a verbatim block is starting.
                    self.verbatim = "end%s" % content
                return Token(TokenType.BLOCK, content, position, lineno)
            if not self.verbatim:
                content = token_string[2:-2].strip()
                if token_start == VARIABLE_TAG_START:
                    return Token(TokenType.VAR, content, position, lineno)
                # BLOCK_TAG_START was handled above.
                assert token_start == COMMENT_TAG_START
                return Token(TokenType.COMMENT, content, position, lineno)
        return Token(TokenType.TEXT, token_string, position, lineno)


class DebugLexer(Lexer):
    def _tag_re_split_positions(self):
        last = 0
        for match in tag_re.finditer(self.template_string):
            start, end = match.span()
            yield last, start
            yield start, end
            last = end
        yield last, len(self.template_string)

    # This parallels the use of tag_re.split() in Lexer.tokenize().
    def _tag_re_split(self):
        for position in self._tag_re_split_positions():
            yield self.template_string[slice(*position)], position

    def tokenize(self):
        """
        Split a template string into tokens and annotates each token with its
        start and end position in the source. This is slower than the default
        lexer so only use it when debug is True.
        """
        # For maintainability, it is helpful if the implementation below can
        # continue to closely parallel Lexer.tokenize()'s implementation.
        in_tag = False
        lineno = 1
        result = []
        for token_string, position in self._tag_re_split():
            if token_string:
                result.append(self.create_token(token_string, position, lineno, in_tag))
                lineno += token_string.count("\n")
            in_tag = not in_tag
        return result


class Parser:
    def __init__(self, tokens, libraries=None, builtins=None, origin=None):
        # Reverse the tokens so delete_first_token(), prepend_token(), and
        # next_token() can operate at the end of the list in constant time.
        self.tokens = list(reversed(tokens))
        self.tags = {}
        self.filters = {}
        self.command_stack = []

        # Custom template tags may store additional data on the parser that
        # will be made available on the template instance. Library authors
        # should use a key to namespace any added data. The 'django' namespace
        # is reserved for internal use.
        self.extra_data = {}

        if libraries is None:
            libraries = {}
        if builtins is None:
            builtins = []

        self.libraries = libraries
        for builtin in builtins:
            self.add_library(builtin)
        self.origin = origin

    def __repr__(self):
        return "<%s tokens=%r>" % (self.__class__.__qualname__, self.tokens)

    def parse(self, parse_until=None):
        """
        Iterate through the parser tokens and compiles each one into a node.

        If parse_until is provided, parsing will stop once one of the
        specified tokens has been reached. This is formatted as a list of
        tokens, e.g. ['elif', 'else', 'endif']. If no matching token is
        reached, raise an exception with the unclosed block tag details.
        """
        if parse_until is None:
            parse_until = []
        nodelist = NodeList()
        while self.tokens:
            token = self.next_token()
            # Use the raw values here for TokenType.* for a tiny performance
            # boost.
            token_type = token.token_type.value
            if token_type == 0:  # TokenType.TEXT
                self.extend_nodelist(nodelist, TextNode(token.contents), token)
            elif token_type == 1:  # TokenType.VAR
                if not token.contents:
                    raise self.error(
                        token, "Empty variable tag on line %d" % token.lineno
                    )
                try:
                    filter_expression = self.compile_filter(token.contents)
                except TemplateSyntaxError as e:
                    raise self.error(token, e)
                var_node = VariableNode(filter_expression)
                self.extend_nodelist(nodelist, var_node, token)
            elif token_type == 2:  # TokenType.BLOCK
                try:
                    command = token.contents.split()[0]
                except IndexError:
                    raise self.error(token, "Empty block tag on line %d" % token.lineno)
                if command in parse_until:
                    # A matching token has been reached. Return control to
                    # the caller. Put the token back on the token list so the
                    # caller knows where it terminated.
                    self.prepend_token(token)
                    return nodelist
                # Add the token to the command stack. This is used for error
                # messages if further parsing fails due to an unclosed block
                # tag.
                self.command_stack.append((command, token))
                # Get the tag callback function from the ones registered with
                # the parser.
                try:
                    compile_func = self.tags[command]
                except KeyError:
                    self.invalid_block_tag(token, command, parse_until)
                # Compile the callback into a node object and add it to
                # the node list.
                try:
                    compiled_result = compile_func(self, token)
                except Exception as e:
                    raise self.error(token, e)
                self.extend_nodelist(nodelist, compiled_result, token)
                # Compile success. Remove the token from the command stack.
                self.command_stack.pop()
        if parse_until:
            self.unclosed_block_tag(parse_until)
        return nodelist

    def skip_past(self, endtag):
        while self.tokens:
            token = self.next_token()
            if token.token_type == TokenType.BLOCK and token.contents == endtag:
                return
        self.unclosed_block_tag([endtag])

    def extend_nodelist(self, nodelist, node, token):
        # Check that non-text nodes don't appear before an extends tag.
        if node.must_be_first and nodelist.contains_nontext:
            if self.origin.template_name:
                origin = repr(self.origin.template_name)
            else:
                origin = "the template"
            raise self.error(
                token,
                "{%% %s %%} must be the first tag in %s." % (token.contents, origin),
            )
        if not isinstance(node, TextNode):
            nodelist.contains_nontext = True
        # Set origin and token here since we can't modify the node __init__()
        # method.
        node.token = token
        node.origin = self.origin
        nodelist.append(node)

    def error(self, token, e):
        """
        Return an exception annotated with the originating token. Since the
        parser can be called recursively, check if a token is already set. This
        ensures the innermost token is highlighted if an exception occurs,
        e.g. a compile error within the body of an if statement.
        """
        if not isinstance(e, Exception):
            e = TemplateSyntaxError(e)
        if not hasattr(e, "token"):
            e.token = token
        return e

    def invalid_block_tag(self, token, command, parse_until=None):
        if parse_until:
            raise self.error(
                token,
                "Invalid block tag on line %d: '%s', expected %s. Did you "
                "forget to register or load this tag?"
                % (
                    token.lineno,
                    command,
                    get_text_list(["'%s'" % p for p in parse_until], "or"),
                ),
            )
        raise self.error(
            token,
            "Invalid block tag on line %d: '%s'. Did you forget to register "
            "or load this tag?" % (token.lineno, command),
        )

    def unclosed_block_tag(self, parse_until):
        command, token = self.command_stack.pop()
        msg = "Unclosed tag on line %d: '%s'. Looking for one of: %s." % (
            token.lineno,
            command,
            ", ".join(parse_until),
        )
        raise self.error(token, msg)

    def next_token(self):
        return self.tokens.pop()

    def prepend_token(self, token):
        self.tokens.append(token)

    def delete_first_token(self):
        del self.tokens[-1]

    def add_library(self, lib):
        self.tags.update(lib.tags)
        self.filters.update(lib.filters)

    def compile_filter(self, token):
        """
        Convenient wrapper for FilterExpression
        """
        return FilterExpression(token, self)

    def find_filter(self, filter_name):
        if filter_name in self.filters:
            return self.filters[filter_name]
        else:
            raise TemplateSyntaxError("Invalid filter: '%s'" % filter_name)


# This only matches constant *strings* (things in quotes or marked for
# translation). Numbers are treated as variables for implementation reasons
# (so that they retain their type when passed to filters).
constant_string = r"""
(?:%(i18n_open)s%(strdq)s%(i18n_close)s|
%(i18n_open)s%(strsq)s%(i18n_close)s|
%(strdq)s|
%(strsq)s)
""" % {
    "strdq": r'"[^"\\]*(?:\\.[^"\\]*)*"',  # double-quoted string
    "strsq": r"'[^'\\]*(?:\\.[^'\\]*)*'",  # single-quoted string
    "i18n_open": re.escape("_("),
    "i18n_close": re.escape(")"),
}
constant_string = constant_string.replace("\n", "")

filter_raw_string = r"""
^(?P<constant>%(constant)s)|
^(?P<var>[%(var_chars)s]+)|
 (?:\s*%(filter_sep)s\s*
     (?P<filter_name>\w+)
         (?:%(arg_sep)s
             (?:
              (?P<constant_arg>%(constant)s)|
              (?P<var_arg>[%(var_chars)s]+)
             )
         )?
 )""" % {
    "constant": constant_string,
    "var_chars": r"\w\.\+-",
    "filter_sep": re.escape(FILTER_SEPARATOR),
    "arg_sep": re.escape(FILTER_ARGUMENT_SEPARATOR),
}

filter_re = _lazy_re_compile(filter_raw_string, re.VERBOSE)


class FilterExpression:
    """
    Parse a variable token and its optional filters (all as a single string),
    and return a list of tuples of the filter name and arguments.
    Sample::

        >>> token = 'variable|default:"Default value"|date:"Y-m-d"'
        >>> p = Parser('')
        >>> fe = FilterExpression(token, p)
        >>> len(fe.filters)
        2
        >>> fe.var
        <Variable: 'variable'>
    """

    __slots__ = ("token", "filters", "var", "is_var", "_rust_names")

    def __init__(self, token, parser):
        self.token = token
        matches = filter_re.finditer(token)
        var_obj = None
        filters = []
        upto = 0
        for match in matches:
            start = match.start()
            if upto != start:
                raise TemplateSyntaxError(
                    "Could not parse some characters: "
                    "%s|%s|%s" % (token[:upto], token[upto:start], token[start:])
                )
            if var_obj is None:
                if constant := match["constant"]:
                    try:
                        var_obj = Variable(constant).resolve({})
                    except VariableDoesNotExist:
                        var_obj = None
                elif (var := match["var"]) is None:
                    raise TemplateSyntaxError(
                        "Could not find variable at start of %s." % token
                    )
                else:
                    var_obj = Variable(var)
            else:
                filter_name = match["filter_name"]
                args = []
                if constant_arg := match["constant_arg"]:
                    args.append((False, Variable(constant_arg).resolve({})))
                elif var_arg := match["var_arg"]:
                    args.append((True, Variable(var_arg)))
                filter_func = parser.find_filter(filter_name)
                self.args_check(filter_name, filter_func, args)
                filters.append((filter_func, args))
            upto = match.end()
        if upto != len(token):
            raise TemplateSyntaxError(
                "Could not parse the remainder: '%s' "
                "from '%s'" % (token[upto:], token)
            )

        self.filters = filters
        self.var = var_obj
        self.is_var = isinstance(var_obj, Variable)

    def resolve(self, context, ignore_failures=False):
        if self.is_var:
            try:
                obj = self.var.resolve(context)
            except VariableDoesNotExist:
                if ignore_failures:
                    obj = None
                else:
                    string_if_invalid = context.template.engine.string_if_invalid
                    if string_if_invalid:
                        if "%s" in string_if_invalid:
                            return string_if_invalid % self.var
                        else:
                            return string_if_invalid
                    else:
                        obj = string_if_invalid
        else:
            obj = self.var
        # Rust's name for each handle in `self.filters`, or `None` where the
        # handle is not ours — computed **once per expression** and kept, where
        # M11 asked the question once per filter *application*. It is a property
        # of the handle alone and `self.filters` froze the handles at compile
        # time, so one expression has one answer; asking it per application cost
        # `real-world/normal` 69.8 us of a 620.8 us render, 65 asks at the
        # 1.074 us `host.django_own_filters` measures (M19). `host.django_own_filter_names`
        # carries the argument for the shape, and for what the re-reading gives up.
        #
        # Filled here rather than in `__init__` because of where the handles are
        # *read*: `__init__` is on the parity harness's **compared path**
        # (`check_transcriptions.COMPARED_PATH`), so a declared difference there
        # would make the comparison Rust against Rust, while `resolve` is
        # deliberately not — which is also why the arm below lives here. The
        # `AttributeError` is the unset slot, not an error: `__slots__` is all
        # this class has, and the descriptor materialiser
        # (`engine/parser.py`) builds one through `__new__` without `__init__`.
        #
        # Guarded by `self.filters` because the fill is a *one-off* cost and the
        # row that pays it is the one with no filters to win back: `{{ var }}`
        # pays an `AttributeError`, an import and a call it never uses. Whether
        # that shows depends on the mode — `render` compiles once and hides it,
        # `parse` compiles every iteration and does not: without this guard,
        # `real-world/large` parse went 2900 -> 3114/3469 us, render unmoved.
        if self.filters:
            try:
                names = self._rust_names
            except AttributeError:
                from django_rust_templates.host import django_own_filter_names

                names = self._rust_names = django_own_filter_names(self.filters)
        for func, args in self.filters:
            arg_vals = []
            for lookup, arg in args:
                if not lookup:
                    arg_vals.append(mark_safe(arg))
                else:
                    arg_vals.append(arg.resolve(context))
            # A built-in filter's arm runs in Rust, and it *replaces* the loop
            # body below rather than wrapping it: upstream's four steps —
            # `expects_localtime`, `needs_autoescape`, the call, and
            # `is_safe`/`mark_safe` — are what `_native.filter` does inside, so
            # running them here as well would apply `@stringfilter`'s coercion
            # and `mark_safe` twice. Decided by handle identity, so a
            # `{% load %}`ed filter that merely shares a name keeps this arm.
            # The decision itself was made in the prologue above, once per
            # expression; `names` is its result.
            from django_rust_templates.host import (
                effective_autoescape,
                effective_use_tz,
            )

            if (builtin_name := names.get(func)) is not None:
                # Upstream's *conditionality*, not its order: each attribute is
                # read only when its own flag is set, and the two flags are
                # disjoint — measured at 6.0.9, 2 filters set `expects_localtime`
                # (`date`, `time`) and 7 set `needs_autoescape`, intersection
                # empty, 48 of the 57 read neither — so a `dict` context, which
                # Django's own suite passes (`test_parser.py:207`), sees exactly
                # the one `AttributeError` upstream sees. As call arguments the
                # FFI's order would decide which name `date` blames.
                use_tz = effective_use_tz(context, func)
                autoescape = effective_autoescape(context, func)
                filtered = _native.filter(
                    builtin_name,
                    obj,
                    tuple(arg_vals),
                    autoescape,
                    use_tz,
                )
                # **`_native.CANNOT_HOLD` is not an answer, it is this seam
                # declining to give one** — it says it cannot hold the value (a
                # Python `str` may be a lone surrogate; a Rust `&str` may not),
                # and the honest thing is to let Django's own callable below
                # answer instead of inventing an exception for a value Django
                # renders. An identity test and not `is None`, because `None`
                # **is** a result a filter may give: `{{ x|default:None }}` with
                # a falsy `x` answers it, and reading that as the refusal would
                # run the filter a second time.
                #
                # **`obj` is assigned only on an answer, and that is the whole
                # reason the result is a separate name.** Assigning the sentinel
                # into `obj` and falling through would hand *it* to Django's
                # body, and `{{ x|upper }}` with `x = '\ud800'` then renders
                # `&lt;the filter seam cannot hold this value&gt;` — measured,
                # and it looks like a successful port rather than a leak.
                if filtered is not _native.CANNOT_HOLD:
                    obj = filtered
                    continue
            if getattr(func, "expects_localtime", False):
                obj = template_localtime(obj, context.use_tz)
            if getattr(func, "needs_autoescape", False):
                new_obj = func(obj, autoescape=context.autoescape, *arg_vals)
            else:
                new_obj = func(obj, *arg_vals)
            if getattr(func, "is_safe", False) and isinstance(obj, SafeData):
                obj = mark_safe(new_obj)
            else:
                obj = new_obj
        return obj

    def args_check(name, func, provided):
        provided = list(provided)
        # First argument, filter input, is implied.
        plen = len(provided) + 1
        # Check to see if a decorator is providing the real function.
        func = inspect.unwrap(func)

        with lazy_annotations():
            args, _, _, defaults, _, _, _ = inspect.getfullargspec(func)
        alen = len(args)
        dlen = len(defaults or [])
        # Not enough OR Too many
        if plen < (alen - dlen) or plen > alen:
            raise TemplateSyntaxError(
                "%s requires %d arguments, %d provided" % (name, alen - dlen, plen)
            )

        return True

    args_check = staticmethod(args_check)

    def __str__(self):
        return self.token

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__qualname__, self.token)


class Variable:
    """
    A template variable, resolvable against a given context. The variable may
    be a hard-coded string (if it begins and ends with single or double quote
    marks)::

        >>> c = {'article': {'section':'News'}}
        >>> Variable('article.section').resolve(c)
        'News'
        >>> Variable('article').resolve(c)
        {'section': 'News'}
        >>> class AClass: pass
        >>> c = AClass()
        >>> c.article = AClass()
        >>> c.article.section = 'News'

    (The example assumes VARIABLE_ATTRIBUTE_SEPARATOR is '.')
    """

    __slots__ = ("var", "literal", "lookups", "translate", "message_context")

    def __init__(self, var):
        self.var = var
        self.literal = None
        self.lookups = None
        self.translate = False
        self.message_context = None

        if not isinstance(var, str):
            raise TypeError("Variable must be a string or number, got %s" % type(var))
        try:
            # First try to treat this variable as a number.
            #
            # Note that this could cause an OverflowError here that we're not
            # catching. Since this should only happen at compile time, that's
            # probably OK.

            # Try to interpret values containing a period or an 'e'/'E'
            # (possibly scientific notation) as a float;  otherwise, try int.
            if "." in var or "e" in var.lower():
                self.literal = float(var)
                # "2." is invalid
                if var[-1] == ".":
                    raise ValueError
            else:
                self.literal = int(var)
        except ValueError:
            # A ValueError means that the variable isn't a number.
            if var[0:2] == "_(" and var[-1] == ")":
                # The result of the lookup should be translated at rendering
                # time.
                self.translate = True
                var = var[2:-1]
            # If it's wrapped with quotes (single or double), then
            # we're also dealing with a literal.
            try:
                self.literal = mark_safe(unescape_string_literal(var))
            except ValueError:
                # Otherwise we'll set self.lookups so that resolve() knows
                # we're dealing with a bonafide variable
                if VARIABLE_ATTRIBUTE_SEPARATOR + "_" in var or var[0] == "_":
                    raise TemplateSyntaxError(
                        "Variables and attributes may "
                        "not begin with underscores: '%s'" % var
                    )
                # Disallow characters that are allowed in numbers but not in a
                # variable name.
                for c in ["+", "-"]:
                    if c in var:
                        raise TemplateSyntaxError(
                            "Invalid character ('%s') in variable name: '%s'" % (c, var)
                        )
                self.lookups = tuple(var.split(VARIABLE_ATTRIBUTE_SEPARATOR))

    def resolve(self, context):
        """Resolve this variable against a given context."""
        if self.lookups is not None:
            # We're dealing with a variable that needs to be resolved
            value = self._resolve_lookup(context)
        else:
            # We're dealing with a literal, so it's already been "resolved"
            value = self.literal
        if self.translate:
            is_safe = isinstance(value, SafeData)
            msgid = value.replace("%", "%%")
            msgid = mark_safe(msgid) if is_safe else msgid
            if self.message_context:
                return pgettext_lazy(self.message_context, msgid)
            else:
                return gettext_lazy(msgid)
        return value

    def __repr__(self):
        return "<%s: %r>" % (self.__class__.__name__, self.var)

    def __str__(self):
        return self.var

    def _resolve_lookup(self, context):
        """
        Perform resolution of a real variable (i.e. not a literal) against the
        given context.

        As indicated by the method's name, this method is an implementation
        detail and shouldn't be called by external code. Use Variable.resolve()
        instead.
        """
        # Delegated to Rust (M9). The docstring above is upstream's verbatim and
        # the *contract* is unchanged; the body is `template-core`'s
        # `resolve_lookup`, which walks `base.py:959-992`'s three arms (dictionary,
        # attribute, list-index) and then applies the outer `except Exception`
        # handler at `base.py:1014`.
        #
        # **The handler is Rust's too, including the `logger.debug`, and that is
        # a deviation from the M9 plan** — the plan put the log here, in Python,
        # with `LookupOutcome::Escaped` carrying the failing `bit` across.
        #
        # The first version of this comment said it could not go here because
        # Rust would have to *raise* for this frame to be inside an `except`
        # block. That is false: the handler works here as soon as
        # `_native.resolve` *returns* its outcome — `(value, bit, error)` —
        # because `logging` takes the exception **instance**, which is what makes
        # `exc_info[1]` the original object by identity without any live `except`.
        # The constraint was a property of the signature, not of the seam.
        #
        # It is in Rust because `_native.resolve` is defined as the Rust mirror
        # of `_resolve_lookup`, and five comparison harnesses — which include
        # `silent_variable_failure` rows — compare it against the pinned method.
        # `outcome_to_py` in `crates/template-py/src/lib.rs` records the full
        # trade and the alternative Task 7 inherits. What is observable here is
        # that the record is the same one: same logger, same message, same
        # `exc_info[1]`.
        #
        # **Pass the components, not a path.** `_native.resolve` takes a dotted
        # path, as `Variable.__init__` took it, and re-derives the split — which is
        # 0.25 µs a variable at the depth this corpus uses (98% of its variables
        # are two bits, not one) and 35% of what resolving a variable costs here.
        # `_native.resolve_lookups` skips the classification entirely and takes
        # `self.lookups` as it stands. What that buys is not only the time: a
        # re-parse can *re-classify*, and `".".join(("2", ""))` is `"2."`, which
        # parses back as a number. That is how `{{ 2. }}` came to render `2.0`
        # where Django renders `string_if_invalid` — a drop-in divergence the 1547
        # cannot see, because no template in `template_tests/` writes a trailing-dot
        # numeral. Handing over the components removes the input class rather than
        # ordering the dispatch around it, so the divergence cannot come back
        # through a different numeral. `.review/m19-measurements/lookup_roundtrip.txt`
        # has the population and the render comparison that caught it;
        # `.review/m19-measurements/production_render_path.txt` §3d prices the join.
        #
        # Reached only when `self.lookups is not None` (see `resolve`), so the
        # literal and translate branches of `Variable.resolve` — which stay
        # Python, one frame out — are not duplicated by this call. That is also why
        # `resolve_lookups` has no `translate` arm to refuse here.
        return _native.resolve_lookups(self.lookups, context)


class Node:
    # Set this to True for nodes that must be first in the template (although
    # they can be preceded by text nodes.
    must_be_first = False
    child_nodelists = ("nodelist",)
    token = None

    def render(self, context):
        """
        Return the node rendered as a string.
        """
        pass

    def render_annotated(self, context):
        """
        Render the node. If debug is True and an exception occurs during
        rendering, the exception is annotated with contextual line information
        where it occurred in the template. For internal usage this method is
        preferred over using the render method directly.
        """
        try:
            return self.render(context)
        except Exception as e:
            if context.template.engine.debug:
                # Store the actual node that caused the exception.
                if not hasattr(e, "_culprit_node"):
                    e._culprit_node = self
                if (
                    not hasattr(e, "template_debug")
                    and context.render_context.template.origin == e._culprit_node.origin
                ):
                    e.template_debug = (
                        context.render_context.template.get_exception_info(
                            e,
                            e._culprit_node.token,
                        )
                    )
            raise

    def get_nodes_by_type(self, nodetype):
        """
        Return a list of all nodes (within this node and its nodelist)
        of the given type
        """
        nodes = []
        if isinstance(self, nodetype):
            nodes.append(self)
        for attr in self.child_nodelists:
            nodelist = getattr(self, attr, None)
            if nodelist:
                nodes.extend(nodelist.get_nodes_by_type(nodetype))
        return nodes


class NodeList(list):
    # Set to True the first time a non-TextNode is inserted by
    # extend_nodelist().
    contains_nontext = False

    # The native render plan for this nodelist, or `None` (M19 step 5). Set by
    # `_native.compile_template` on the **body nodelist of every `{% block %}` it
    # could plan**, and `None` everywhere else — read by `_render_with_plan` just
    # below, which is what `BlockNode.render` calls in place of `render`.
    #
    # A class attribute rather than an instance one, for the reason
    # `Template._native_plan` gives: `NodeList` is built by several paths — the
    # materialiser, and every hand-built fixture in the suite — and a missing
    # attribute would be an `AttributeError` at the read rather than a fallback.
    #
    # It goes on the *body* and not on the `BlockNode` because the node that
    # renders a body is frequently not the node the plan was built for:
    # `BlockNode.render` pops an override out of the block context and renders
    # `type(self)(block.name, block.nodelist).nodelist` — the same `NodeList`
    # object — and `{{ block.super }}` re-enters through the base template's own
    # block. The body nodelist is the one object all of those paths share.
    _native_plan = None

    def render(self, context):
        return SafeString("".join([node.render_annotated(context) for node in self]))

    def get_nodes_by_type(self, nodetype):
        "Return a list of all nodes of the given type"
        nodes = []
        for node in self:
            nodes.extend(node.get_nodes_by_type(nodetype))
        return nodes

    def _render_with_plan(self, context):
        """Render this nodelist, in Rust when it carries a plan (M19 step 5).

        `Template._render`'s two lines, one level down, and the whole of this
        slice: the walk delegates a `BlockNode`, Python pops the override out of
        the block context and renders its body here, and on a page with
        inheritance that body is the whole render — measured, **99.0%** of
        `real-world/normal`'s wall time is inside the one delegated `ExtendsNode`
        frame (`.review/m19-measurements/inheritance_share.py`).

        **The caller is `loader_tags.py::BlockNode.render`, and this method is
        *not* named `_render` after `Template`'s.**  `Template._render` is
        Django's name, and Django's reason for it is the bound-context contract
        — `render` binds, `_render` assumes a bound context — which `NodeList`
        has no counterpart for.  What differs here is the *plan*, so the name
        says the plan.  (The call site is upstream's own line rewritten in
        `scripts/gen_impl_transcription.py`'s `TRANSFORMS`: this file is
        hand-written, `loader_tags.py` is generated, and a generated file may
        differ from the pin only by whole-line substitutions the generator
        makes.)

        **Why the arm is not in `render` above.**  `NodeList.render` is on the
        transcription guard's compared path (`(1199, 1200)`), so an arm inside it
        would make the parity harness compare Rust against Rust — the same
        reason `Template._render` took the walk's arm rather than `Template.render`
        taking it, and the same clause of `scripts/check_transcriptions.py`.  The
        *caller* is the other half of the choice: `BlockNode.render` is where a
        block body funnels, and it is not on that path.

        **The fallback is total, so this cannot change an answer.**  Every
        nodelist the descent did not plan reads `None` — the class default, so
        every hand-built `NodeList` in the suite included — and takes `render`,
        which is what the call did before the walk existed.  A body whose nodes
        and descriptors disagree gets no plan at all (`plan_list` returns `None`
        on the first class mismatch), so a block this pass misreads is a lost
        optimisation, never a different string.
        """
        plan = self._native_plan
        if plan is not None:
            return _native.render_plan(plan, context)
        return self.render(context)


class TextNode(Node):
    child_nodelists = ()

    def __init__(self, s):
        self.s = s

    def __repr__(self):
        return "<%s: %r>" % (self.__class__.__name__, self.s[:25])

    def render(self, context):
        return self.s

    def render_annotated(self, context):
        """
        Return the given value.

        The default implementation of this method handles exceptions raised
        during rendering, which is not necessary for text nodes.
        """
        return self.s


def render_value_in_context(value, context):
    """
    Convert any value to a string to become part of a rendered template. This
    means escaping, if required, and conversion to a string. If value is a
    string, it's expected to already be translated.
    """
    # The common case, and §2's largest single line: an exact `str` is a no-op for
    # both calls below (`localize` short-circuits on `str` before reading
    # anything), and `conditional_escape` is `SafeString(html.escape(str))` for
    # anything without `__html__`. **Exact** type, not `isinstance`: a `str`
    # subclass is where `SafeString` and `markupsafe.Markup` live, both carry
    # `__html__`, and escaping one is the bug the predicate exists to decline.
    # Everything it declines — a subclass, or a lone surrogate, where the binding
    # refuses rather than raise — takes the slow path unchanged.
    if context.autoescape and type(value) is str:
        escaped = _native.html_escape(value)
        if escaped is not None:
            return SafeString(escaped)
    # §2's other large line, and the same shape. An exact `int` is a no-op for
    # `localize` whenever grouping is off, because `numberformat.format` has its
    # own fast path -- `if isinstance(number, int) and not use_grouping and not
    # decimal_pos: return mark_safe(number)` -- which returns the *int*, so the
    # only thing that ever makes it a string is the `str()` below, in this
    # function. Upstream still pays `get_language()` and three `get_format()`
    # calls to arrive at that line: 3.34 us against this arm's 0.385, measured
    # on `int_fast_path.py` §2. `use_grouping` is `use_l10n and
    # settings.USE_THOUSAND_SEPARATOR and grouping != 0`, and the two disjuncts
    # below cover it -- the first because `localize` short-circuits on
    # `use_l10n is False` before `number_format` is entered at all, the second
    # because the setting alone makes `use_grouping` false whatever the locale
    # says. A locale that sets `NUMBER_GROUPING` to 0 reaches `mark_safe(number)`
    # upstream even with `USE_THOUSAND_SEPARATOR` on and takes the slow path
    # here: we lose the win, never the answer. Exact type again, and here it
    # also excludes `bool`, which `localize` sends down its own arm.
    if type(value) is int and (
        context.use_l10n is False or not settings.USE_THOUSAND_SEPARATOR
    ):
        text = str(value)
        if context.autoescape:
            escaped = _native.html_escape(text)
            if escaped is not None:
                return SafeString(escaped)
            return conditional_escape(text)
        # **The unescaped answer is not always a plain `str`, and which one it
        # is tracks *why* the arm fired.** Upstream's last statement here is
        # `str(value)`, over whatever `localize` returned: for `use_l10n is
        # False` that is `localize`'s own `str(value)` (plain), but otherwise it
        # is `numberformat.format`'s `mark_safe(number)` — and `str()` is the
        # identity on a `SafeString`, so the answer is a `SafeString`. Measured
        # for all four settings states in `int_fast_path.py` §1; the render
        # output is the same string either way, so only a type comparison sees
        # it, which is why this comment is the whole cost of being exact.
        if context.use_l10n is False:
            return text
        return mark_safe(text)
    value = template_localtime(value, use_tz=context.use_tz)
    value = localize(value, use_l10n=context.use_l10n)
    if context.autoescape:
        if not issubclass(type(value), str):
            value = str(value)
        return conditional_escape(value)
    else:
        return str(value)


class VariableNode(Node):
    child_nodelists = ()

    def __init__(self, filter_expression):
        self.filter_expression = filter_expression

    def __repr__(self):
        return "<Variable Node: %s>" % self.filter_expression

    def render(self, context):
        try:
            output = self.filter_expression.resolve(context)
        except UnicodeDecodeError:
            # Unicode conversion can fail sometimes for reasons out of our
            # control (e.g. exception rendering). In that case, we fail
            # quietly.
            return ""
        return render_value_in_context(output, context)


# Regex for token keyword arguments
kwarg_re = _lazy_re_compile(r"(?:(\w+)=)?(.+)")


def token_kwargs(bits, parser, support_legacy=False):
    """
    Parse token keyword arguments and return a dictionary of the arguments
    retrieved from the ``bits`` token list.

    `bits` is a list containing the remainder of the token (split by spaces)
    that is to be checked for arguments. Valid arguments are removed from this
    list.

    `support_legacy` - if True, the legacy format ``1 as foo`` is accepted.
    Otherwise, only the standard ``foo=1`` format is allowed.

    There is no requirement for all remaining token ``bits`` to be keyword
    arguments, so return the dictionary as soon as an invalid argument format
    is reached.
    """
    if not bits:
        return {}
    match = kwarg_re.match(bits[0])
    kwarg_format = match and match[1]
    if not kwarg_format:
        if not support_legacy:
            return {}
        if len(bits) < 3 or bits[1] != "as":
            return {}

    kwargs = {}
    while bits:
        if kwarg_format:
            match = kwarg_re.match(bits[0])
            if not match or not match[1]:
                return kwargs
            key, value = match.groups()
            del bits[:1]
        else:
            if len(bits) < 3 or bits[1] != "as":
                return kwargs
            key, value = bits[2], bits[0]
            del bits[:3]
        kwargs[key] = parser.compile_filter(value)
        if bits and not kwarg_format:
            if bits[0] != "and":
                return kwargs
            del bits[:1]
    return kwargs


# ---------------------------------------------------------------------------
# `BaseContext.__getitem__`, pinned for the Rust walk's root lookup.
#
# `PyTarget::get_item` in `crates/template-py/src/lib.rs` reimplements that
# method's upward search — the `reversed(self.dicts)` walk, membership before
# subscription — and enters it only while `type(context).__getitem__` *is* the
# function bound below. So this line has to be the function as it stood **before
# any code outside this package could rebind it**, and where it sits is the whole
# of that claim. Capture the same attribute lazily, on the Rust side, and a
# process that rebinds `BaseContext.__getitem__` before its first render pins the
# *replacement*, after which the guard licenses the transcribed body and answers a
# lookup the class no longer performs — silently, and green everywhere.
#
# Reading this name requires importing this module, and **the import order of
# the package this module is in is what makes that unavoidable** — not this
# module's own `from .context import BaseContext` 60 lines above, which is
# *downstream* of the fact. Any route to `BaseContext` executes
# `django_rust_templates/engine/__init__.py` first, because that is how a
# submodule is reached, and its first statement is `from .engine import Engine`
# (`engine/__init__.py:26`) → `from .base import Template` (`engine/engine.py:7`) → this
# module's body, which is where the binding below happens. A project can
# therefore only reach `engine/context.py` after the pin exists. Measured, in a
# fresh interpreter: `import django_rust_templates.engine.context` alone leaves
# `django_rust_templates.engine.base` in `sys.modules` with this name already
# bound, and rebinding the class attribute immediately afterwards makes the walk
# fall back. **The invariant is that first import**, so it is the line to protect
# and `tests/test_loop_read_guard.py` pins it; the pin cannot be late, and it
# cannot be early either — the `BaseContext` it reads is bound 60 lines above.
#
# **At end of file on purpose.** Every `COMPARED_PATH["base.py"]` span in
# `scripts/check_transcriptions.py` is a literal line range over this file, and the
# highest ends at 1407 — `token_kwargs`, this file's last line until this block was
# appended under it. A binding at 1408 moves no span; one above 1407 re-keys all.
BASE_CONTEXT_GETITEM = BaseContext.__getitem__
