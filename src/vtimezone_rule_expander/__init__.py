"""vtimezone_rule_expander
======================

Expands iCalendar VTIMEZONE RRULE blocks into concrete transition datetimes.
"""

from .core import (
    expand_vtimezone,
    parse_vtimezone,
    expand_rrule_transitions,
    Transition,
)

__all__ = [
    "expand_vtimezone",
    "parse_vtimezone",
    "expand_rrule_transitions",
    "Transition",
]
