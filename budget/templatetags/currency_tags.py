from django import template

from budget.currency import format_mmk

register = template.Library()


@register.filter
def mmk(value):
    """Format a value as whole MMK."""
    if value is None or value == "":
        return "0 MMK"
    return format_mmk(value)
