from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Look up a dict value by variable key in templates.
    Usage: {{ my_dict|get_item:variable_key }}
    """
    return dictionary.get(str(key), '')


@register.simple_tag
def store_target(lookup_dict, period, store_id):
    """Return a pre-built revenue target value for a specific store + period.
    Usage: {% store_target target_lookup type_val store.id %}
    """
    return lookup_dict.get(f'{period}_store_{store_id}', '')
