import django_rust_templates.engine as template

register = template.Library()


@register.simple_tag()
def annotated_tag_function(val: int):
    return val
