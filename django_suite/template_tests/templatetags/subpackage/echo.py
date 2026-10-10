import django_rust_templates.engine as template

register = template.Library()


@register.simple_tag
def echo2(arg):
    return arg
