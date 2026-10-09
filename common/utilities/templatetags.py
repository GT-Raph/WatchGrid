"""Dashboard template filters and tags, registered as template builtins (no {% load %}).

Django's own ``{% querystring %}`` tag builds pagination and filter links."""

from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def label(value):
    return str(value or "Unknown").replace("_", " ").capitalize()


@register.filter
def percent(value):
    return "No data" if value is None else f"{value:.2f}%"


@register.filter
def duration(value):
    if value is None:
        return "—"
    value = int(value)
    if value >= 86400:
        return f"{value // 86400}d {(value % 86400) // 3600}h"
    if value >= 3600:
        return f"{value // 3600}h {(value % 3600) // 60}m"
    return f"{value // 60}m {value % 60}s"


@register.simple_tag
def icon(name):
    paths = {
        "grid": "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
        "folder": "M3 7V4h6l3 3h9v13H3z",
        "pulse": "M2 12h5l3-8 4 16 3-8h5",
        "alert": "M12 3 2 21h20z M12 9v5 M12 17v1",
        "code": "m8 5-6 7 6 7 M16 5l6 7-6 7 M14 3l-4 18",
        "chart": "M3 3v18h18 M7 17v-5 M12 17V7 M17 17V4",
        "users": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M13 3a4 4 0 0 1 0 8 "
        "M20 21v-2a4 4 0 0 0-3-4 M9 3a4 4 0 1 0 0 8a4 4 0 0 0 0-8",
        "link": "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2",
        "settings": "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6",
        "plus": "M12 5v14 M5 12h14",
        "refresh": "M20 7v5h-5 M4 17v-5h5 M5 8a8 8 0 0 1 13-3l2 3 M4 16l2 3a8 8 0 0 0 13-3",
        "sun": "M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2 "
        "M16 12a4 4 0 1 0-8 0a4 4 0 0 0 8 0",
    }
    return format_html(
        '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true"><path d="{}"/></svg>',
        paths.get(name, paths["grid"]),
    )
