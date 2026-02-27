"""
scheduling_tags.py — custom template filters used across ShiftSync templates.

Filters:
  get_item(d, key)              → d[key], used for dict lookups with a variable key
  shifts_for_day(grid, day)     → sub-dict of shifts for a given date
  for_location(day_slice, pk)   → list of shifts for a specific location pk
"""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary: dict, key):
    """
    Return dictionary[key], supporting variable keys in templates.

    Django templates can't do {{ my_dict[variable] }}, so this filter
    bridges the gap:  {{ my_dict|get_item:variable }}

    Args:
        dictionary: Any dict-like object.
        key:        The key to look up (any hashable type).

    Returns:
        The value at that key, or None if missing.
    """
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def shifts_for_day(grid: dict, day) -> dict:
    """
    Return a sub-dict of grid entries for a specific date.

    Args:
        grid: Dict keyed by (date, location_id) → list[Shift].
        day:  A date object to filter by.

    Returns:
        Dict keyed by location_id → list[Shift] for that day.
    """
    result = {}
    for (d, loc_id), shifts in grid.items():
        if d == day:
            result[loc_id] = shifts
    return result


@register.filter
def for_location(day_slice: dict, location_id: int) -> list:
    """
    Return the list of shifts for a specific location from a day slice.

    Args:
        day_slice:   Dict keyed by location_id → list[Shift].
        location_id: The location PK to look up.

    Returns:
        List of Shift objects, or empty list if none.
    """

    return day_slice.get(int(location_id), [])