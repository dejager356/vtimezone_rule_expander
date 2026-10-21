# vtimezone_rule_expander

Expands iCalendar VTIMEZONE RRULE blocks into concrete transition datetimes for a given year range. Standard library only, no third-party dependencies.

## Usage

```python
from vtimezone_rule_expander import expand_vtimezone, Transition

vtimezone_block = """BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
DTSTART:19700308T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:EDT
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:19701101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:EST
END:STANDARD
END:VTIMEZONE
"""

transitions = expand_vtimezone(vtimezone_block, 2024, 2024)
# [Transition(when_utc=datetime(2024, 3, 10, 7, 0), to_offset=-14400, is_dst=True),
#  Transition(when_utc=datetime(2024, 11, 3, 6, 0), to_offset=-18000, is_dst=False)]
```

Each `Transition` is a frozen dataclass with `when_utc` (naive datetime in UTC), `to_offset` (seconds east of UTC), and `is_dst` (bool). The list is sorted by `when_utc`.

## Exported names

- `expand_vtimezone(content: str, start_year: int, end_year: int) -> list[Transition]` — parse and expand a VTIMEZONE block.
- `parse_vtimezone(content: str) -> dict` — parse only, returns `{"TZID": str, "subcomponents": [...]}`.
- `expand_rrule_transitions(...)` — expand a single rule; see the docstring for parameters.
- `Transition` — frozen dataclass with `when_utc`, `to_offset`, `is_dst`.

## Why this exists

iCalendar VTIMEZONE components describe daylight-saving rules abstractly: a `DTSTART` wall-clock time plus an `RRULE` that says "every year, on the second Sunday of March, at 02:00 local." Converting that into concrete UTC datetimes requires combining the rule with the *from* offset (the offset being left) to land in UTC, then materialising BYDAY ordinals like `-1SU` (last Sunday) against real calendars.

This library does exactly that, and nothing else. It does not bundle a full tzdata copy, does not compute current local time, and does not handle historical transitions before the rule's DTSTART. The trade-off: you get a small, auditable dependency that works offline, but you must feed it the VTIMEZONE block yourself.

## Interpretation chosen

Only `FREQ=YEARLY` rules with `BYMONTH` and optional `BYDAY` are supported. That is the shape every tzdata-to-iCalendar converter produces. `BYDAY` ordinals 1-5 and -1 to -5 are handled; bare weekday codes (e.g. `SU`) expand to every matching weekday in the month. `UNTIL` as a DATE is treated as end-of-that-day in UTC; `UNTIL` as a DATE-TIME is read as UTC (with or without a trailing `Z`). `COUNT` limits total emissions from the rule's DTSTART forward.

## Awkward edge

DTSTART inside a STANDARD/DAYLIGHT sub-component is a naive local time in the *from* offset, not UTC. A rule that says `DTSTART:19700308T020000` with `TZOFFSETFROM:-0500` means 02:00 EST, which is 07:00 UTC. If you pass a feed whose DTSTART already carries a `Z` suffix, this library strips it and treats the value as wall-clock in the from-offset — which is technically wrong per the spec but matches what broken feeds in the wild actually intend. If your feed is spec-compliant, this never triggers.
