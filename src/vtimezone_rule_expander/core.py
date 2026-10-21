"""Core expansion logic for VTIMEZONE RRULE blocks.

The iCalendar spec (RFC 5545) lets a VTIMEZONE component describe daylight-saving
rules via STANDARD / DAYLIGHT sub-components whose DTSTART and RRULE encode the
wall-clock moment at which the offset changes.  Expanding those rules into concrete
UTC datetimes is straightforward in principle but fiddly in practice:

* DTSTART is a naive local time that only becomes meaningful once combined with
  the offset the rule applies *from* (the one whose suffix component we are
  leaving).  We convert it to UTC once, then let the recurrence engine operate in
  UTC so that BYDAY-in-FREQ=YEARLY arithmetic is unambiguous.
* BYDAY in a YEARLY RRULE can carry a signed ordinal prefix (e.g. ``-1SU``) that
  selects the nth weekday from the start or end of the month.  We implement that
  for the common cases (1..5 and -1..-5) and raise for anything else.
* We deliberately ignore UNTIL values whose resolution does not match the RRULE
  frequency, per RFC 5545 section 3.3.10.  Real-world feeds occasionally violate
  this; we refuse to guess rather than silently mis-expanding.

Interpretation we picked, stated plainly: we only support RRULEs whose FREQ is
YEARLY and which carry BYMONTH plus optional BYDAY.  That is the shape every
published tzdata-to-ical converter produces, so anything more general would be
speculative surface area we cannot back with tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Name -> (python weekday int, sign multiplier) for the BYDAY weekday code.
# We map SUNDAY first because icalendar uses 2-letter codes, not Python's 0-6.
_WEEKDAY_CODES: Dict[str, int] = {
    "SU": 6,
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
}


def _parse_offset(value: str) -> int:
    """Parse an iCalendar UTC-offset string (e.g. ``+0200``) into seconds.

    A leading ``Z`` would mean the value is already UTC, which is not valid for
    TZOFFSETFROM/TZOFFSETTO per RFC 5545; we reject it rather than treat it as
    zero so that malformed feeds surface loudly.
    """
    text = value.strip()
    if not text or text == "Z":
        raise ValueError("invalid UTC offset: %r" % value)
    sign = 1
    if text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    if len(text) < 4 or not text.isdigit():
        raise ValueError("invalid UTC offset: %r" % value)
    hours = int(text[0:2])
    minutes = int(text[2:4])
    seconds = int(text[4:6]) if len(text) >= 6 else 0
    return sign * (hours * 3600 + minutes * 60 + seconds)


def _parse_ical_datetime(value: str, offset_seconds: int) -> datetime:
    """Parse an iCalendar datetime literal into a naive UTC datetime.

    DTSTART inside a sub-component is wall-clock in the *from* offset, so we add
    the inverse of that offset to land in UTC.  We return naive datetimes
    throughout because the recurrence engine works in a fixed 0-offset frame
    and attaching ``tzinfo=utc`` would buy us nothing while complicating the
    arithmetic.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    dt = datetime.strptime(text, "%Y%m%dT%H%M%S")
    return dt - timedelta(seconds=offset_seconds)


def _parse_byday(code: str) -> Tuple[int, int]:
    """Parse a BYDAY token like ``2SU`` or ``-1SU`` into (ordinal, weekday).

    An absent ordinal means "every matching weekday in the month"; we return
    ordinal 0 to signal that and let the caller iterate.  We only ever produce
    ordinal 0 here when the token is a bare weekday like ``SU``.
    """
    code = code.strip().upper()
    ordinal = 0
    digits = ""
    while code and code[0] in "+-0123456789":
        digits += code[0]
        code = code[1:]
    if digits:
        ordinal = int(digits)
    if code not in _WEEKDAY_CODES:
        raise ValueError("invalid BYDAY weekday: %r" % code)
    return ordinal, _WEEKDAY_CODES[code]


def _nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    """Return the date of the nth weekday in a month.

    Positive ordinals count from the start of the month; negative ordinals count
    from the end.  Ordinal 0 means the first matching weekday (we never call it
    that way for BYDAY, but it's a useful default).
    """
    if ordinal == 0:
        ordinal = 1
    if ordinal > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        day = 1 + offset + (ordinal - 1) * 7
        return date(year, month, day)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    day = last.day - offset + (ordinal + 1) * 7
    return date(year, month, day)


def _apply_byday(base: datetime, byday_list: List[str]) -> List[datetime]:
    """Materialise a list of BYDAY-specified datetimes within base's month.

    ``base`` carries the year/month and time-of-day we are operating in; we
    replace its day based on each BYDAY token.  Tokens without an ordinal
    (bare ``SU``) expand to every matching weekday of the month, per RFC 5545.
    """
    results: List[datetime] = []
    for token in byday_list:
        ordinal, weekday = _parse_byday(token)
        if ordinal == 0:
            # Every matching weekday in the month.
            day = 1
            first = date(base.year, base.month, 1)
            offset = (weekday - first.weekday()) % 7
            day = 1 + offset
            while True:
                try:
                    d = date(base.year, base.month, day)
                except ValueError:
                    break
                if d.month != base.month:
                    break
                results.append(datetime.combine(d, base.time()))
                day += 7
        else:
            d = _nth_weekday(base.year, base.month, weekday, ordinal)
            results.append(datetime.combine(d, base.time()))
    return sorted(set(results))


def _parse_rrule(value: str) -> Dict[str, object]:
    """Parse an RRULE value string into a dict of lower-case keys.

    Values are kept as strings; the caller coerces as needed.  We do NOT lower-
    case the values themselves because weekday codes are case-sensitive in some
    tooling even though the spec says they're upper-case; we normalise at the
    point of use instead.
    """
    parts: Dict[str, object] = {}
    for chunk in value.split(";"):
        if "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        parts[key.strip().upper()] = val.strip()
    if "BYDAY" in parts:
        days = str(parts["BYDAY"])
        parts["BYDAY"] = [d.strip() for d in days.split(",") if d.strip()]
    if "BYMONTH" in parts:
        months = str(parts["BYMONTH"])
        parts["BYMONTH"] = [int(m) for m in months.split(",") if m.strip()]
    return parts


def _until_terminates(rule: Dict[str, object], dt_utc: datetime, interval: int) -> bool:
    """Return True if an UNTIL clause ends the recurrence at or before dt_utc.

    RFC 5545 requires UNTIL to have the same resolution as FREQ; if the user gave
    a DATE-only UNTIL with FREQ=YEARLY we treat it as the end of that day in UTC.
    A DATE-TIME UNTIL with a trailing ``Z`` is UTC by definition; a DATE-TIME
    without ``Z`` is technically ambiguous, and we choose to read it as UTC
    rather than guess the zone, because any other reading would require zone
    information we don't have.
    """
    until = rule.get("UNTIL")
    if until is None:
        return False
    text = str(until)
    if text.endswith("Z"):
        text = text[:-1]
        try:
            until_dt = datetime.strptime(text, "%Y%m%dT%H%M%S")
        except ValueError:
            return False
    elif "T" in text:
        try:
            until_dt = datetime.strptime(text, "%Y%m%dT%H%M%S")
        except ValueError:
            return False
    else:
        try:
            until_dt = datetime.strptime(text, "%Y%m%d")
        except ValueError:
            return False
        until_dt = until_dt.replace(hour=23, minute=59, second=59)
    return dt_utc > until_dt


@dataclass(frozen=True)
class Transition:
    """A single offset transition as a concrete UTC datetime.

    ``to_offset`` is the offset (seconds east of UTC) that is in effect after
    the transition.  ``is_dst`` flags whether that offset is daylight time.
    """

    when_utc: datetime
    to_offset: int
    is_dst: bool


def _build_subcomponent(block: Dict[str, object]) -> Dict[str, object]:
    """Normalise a parsed sub-component into the fields expand_rrule_transitions needs."""
    return {
        "TZOFFSETFROM": _parse_offset(str(block["TZOFFSETFROM"])),
        "TZOFFSETTO": _parse_offset(str(block["TZOFFSETTO"])),
        "DTSTART": str(block["DTSTART"]),
        "RRULE": block.get("RRULE"),
        "TZNAME": block.get("TZNAME"),
    }


def expand_rrule_transitions(
    dtstart_local: str,
    from_offset: int,
    rule: Optional[Dict[str, object]],
    to_offset: int,
    is_dst: bool,
    start_year: int,
    end_year: int,
) -> List[Transition]:
    """Expand a single STANDARD/DAYLIGHT rule into transitions.

    If ``rule`` is None the component fires exactly once, at its DTSTART.  We
    emit a transition only when its UTC moment falls inside [start_year,
    end_year]; callers wanting the historical first occurrence outside the
    window should extend the window.
    """
    base = _parse_ical_datetime(dtstart_local, from_offset)

    if rule is None:
        if start_year <= base.year <= end_year:
            return [Transition(when_utc=base, to_offset=to_offset, is_dst=is_dst)]
        return []

    freq = str(rule.get("FREQ", "")).upper()
    if freq != "YEARLY":
        raise ValueError("unsupported FREQ: %r (only YEARLY is supported)" % rule.get("FREQ"))

    interval = int(rule.get("INTERVAL", 1))
    if interval < 1:
        raise ValueError("INTERVAL must be >= 1, got %d" % interval)

    bymonth = rule.get("BYMONTH")
    byday = rule.get("BYDAY")
    count = rule.get("COUNT")
    count_int = int(count) if count is not None else None

    transitions: List[Transition] = []
    emitted = 0

    # Walk from the rule's DTSTART year forward.  We start at the base year, not
    # start_year, because UNTIL/COUNT are measured from DTSTART; a rule that
    # started in 1970 with COUNT=10 must stop in 1979 even if start_year=2020.
    year = base.year
    while year <= end_year:
        months: List[int]
        if bymonth is not None:
            months = [int(m) for m in bymonth]  # type: ignore[union-attr]
            months.sort()
        else:
            months = [base.month]

        candidates: List[datetime] = []
        for m in months:
            seed = base.replace(year=year, month=m, day=1)
            if byday is not None:
                candidates.extend(_apply_byday(seed, list(byday)))  # type: ignore[arg-type]
            else:
                candidates.append(seed.replace(day=base.day))

        for cand in candidates:
            utc = cand
            if _until_terminates(rule, utc, interval):
                return transitions
            if base <= utc <= datetime(end_year, 12, 31, 23, 59, 59) and utc.year >= start_year:
                transitions.append(Transition(when_utc=utc, to_offset=to_offset, is_dst=is_dst))
                emitted += 1
                if count_int is not None and emitted >= count_int:
                    return transitions

        year += interval

    return transitions


def _split_lines(content: str) -> List[str]:
    """Unfold iCalendar folded lines per RFC 5545 section 3.1.

    A CRLF followed by a space or tab means "continuation".  We handle plain
    LF as well because real feeds love to drop the CR; the spec says CRLF but
    we are not going to crash on a missing carriage return.
    """
    raw = content.replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in raw.split("\n"):
        if not line:
            continue
        if line[0] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_property(line: str) -> Tuple[str, Dict[str, str], str]:
    """Split a property line into (name, params, value).

    ``DTSTART;TZID=America/New_York:20200101T020000`` becomes
    ``("DTSTART", {"TZID": "America/New_York"}, "20200101T020000")``.
    """
    name, _, rest = line.partition(":")
    params: Dict[str, str] = {}
    if ";" in name:
        name, _, param_blob = name.partition(";")
        for chunk in param_blob.split(";"):
            if "=" in chunk:
                k, _, v = chunk.partition("=")
                params[k.strip().upper()] = v.strip()
    return name.strip().upper(), params, rest.strip()


def parse_vtimezone(content: str) -> Dict[str, object]:
    """Parse an iCalendar VTIMEZONE block into a plain dict.

    Returns ``{"TZID": str, "subcomponents": [{...}]}`` where each subcomponent
    has the keys ``TZOFFSETFROM``, ``TZOFFSETTO``, ``DTSTART``, optional
    ``RRULE`` (string), optional ``TZNAME``, and ``TYPE`` ("STANDARD" or
    "DAYLIGHT").

    We intentionally do not validate the surrounding BEGIN:VCALENDAR / END
    markers; a VTIMEZONE block is useful in isolation and feeds frequently
    arrive without a calendar wrapper.
    """
    lines = _split_lines(content)
    result: Dict[str, object] = {"TZID": "", "subcomponents": []}
    i = 0
    in_vtimezone = False
    current_sub: Optional[Dict[str, object]] = None
    current_type: Optional[str] = None

    while i < len(lines):
        line = lines[i]
        name, params, value = _parse_property(line)

        if name == "BEGIN" and value == "VTIMEZONE":
            in_vtimezone = True
            i += 1
            continue
        if name == "END" and value == "VTIMEZONE":
            break
        if not in_vtimezone:
            i += 1
            continue

        if name == "BEGIN" and value in ("STANDARD", "DAYLIGHT"):
            current_sub = {}
            current_type = value
            i += 1
            continue
        if name == "END" and value in ("STANDARD", "DAYLIGHT"):
            if current_sub is not None and current_type is not None:
                current_sub["TYPE"] = current_type
                result["subcomponents"].append(current_sub)
            current_sub = None
            current_type = None
            i += 1
            continue

        if name == "TZID":
            result["TZID"] = value
        elif current_sub is not None:
            # DTSTART can carry a TZID param in real feeds; for sub-component
            # DTSTART the offset is the enclosing TZOFFSETFROM, so we ignore
            # the TZID param and use the raw value.
            if name in ("TZOFFSETFROM", "TZOFFSETTO", "DTSTART", "RRULE", "TZNAME"):
                current_sub[name] = value

        i += 1

    return result


def expand_vtimezone(
    content: str,
    start_year: int,
    end_year: int,
) -> List[Transition]:
    """Parse and expand a VTIMEZONE block into sorted transitions.

    Returns a list of :class:`Transition` sorted by ``when_utc``.  The same
    rule may legitimately produce no transitions inside the window if its
    DTSTART is far in the future; we return an empty list rather than raising.
    """
    parsed = parse_vtimezone(content)
    subs = parsed.get("subcomponents", [])
    if not subs:
        return []

    all_transitions: List[Transition] = []
    for sub in subs:
        sub_dict = _build_subcomponent(sub)
        rule_str = sub_dict.get("RRULE")
        rule = _parse_rrule(str(rule_str)) if rule_str else None
        is_dst = str(sub.get("TYPE", "")).upper() == "DAYLIGHT"
        all_transitions.extend(
            expand_rrule_transitions(
                dtstart_local=str(sub_dict["DTSTART"]),
                from_offset=int(sub_dict["TZOFFSETFROM"]),  # type: ignore[arg-type]
                rule=rule,
                to_offset=int(sub_dict["TZOFFSETTO"]),  # type: ignore[arg-type]
                is_dst=is_dst,
                start_year=start_year,
                end_year=end_year,
            )
        )

    all_transitions.sort(key=lambda t: t.when_utc)
    return all_transitions
