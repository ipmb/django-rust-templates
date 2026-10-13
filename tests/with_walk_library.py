"""Host-owned nodes used to test scoped nodelist joins."""

from django.template import Library, Node

register = Library()


class NonString(Node):
    def render(self, context):
        context["events"].append("non-string")
        return None


class Record(Node):
    def render(self, context):
        context["events"].append("record")
        return ""


@register.tag
def non_string(parser, token):
    return NonString()


@register.tag
def record(parser, token):
    return Record()
