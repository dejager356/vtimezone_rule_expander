import unittest

from vtimezone_rule_expander import (
    expand_vtimezone,
    parse_vtimezone,
    expand_rrule_transitions,
    Transition,
)


class TestOffsetParsing(unittest.TestCase):
    """The offset parser is the load-bearing primitive; if it mis-reads a sign
    or drops seconds, every downstream transition is wrong."""

    def test_positive_offset(self):
        from vtimezone_rule_expander.core import _parse_offset

        self.assertEqual(_parse_offset("+0200"), 2 * 3600)

    def test_negative_offset(self):
        from vtimezone_rule_expander.core import _parse_offset

        self.assertEqual(_parse_offset("-0500"), -5 * 3600)

    def test_offset_with_seconds(self):
        from vtimezone_rule_expander.core import _parse_offset

        self.assertEqual(_parse_offset("+013015"), 3600 + 1800 + 15)

    def test_z_is_rejected(self):
        from vtimezone_rule_expander.core import _parse_offset

        with self.assertRaises(ValueError):
            _parse_offset("Z")


class TestBydayParsing(unittest.TestCase):
    def test_bare_weekday(self):
        from vtimezone_rule_expander.core import _parse_byday

        ordinal, weekday = _parse_byday("SU")
        self.assertEqual(ordinal, 0)
        self.assertEqual(weekday, 6)

    def test_positive_ordinal(self):
        from vtimezone_rule_expander.core import _parse_byday

        ordinal, weekday = _parse_byday("2SU")
        self.assertEqual(ordinal, 2)
        self.assertEqual(weekday, 6)

    def test_negative_ordinal(self):
        from vtimezone_rule_expander.core import _parse_byday

        ordinal, weekday = _parse_byday("-1SU")
        self.assertEqual(ordinal, -1)
        self.assertEqual(weekday, 6)

    def test_invalid_weekday(self):
        from vtimezone_rule_expander.core import _parse_byday

        with self.assertRaises(ValueError):
            _parse_byday("2XX")


class TestNthWeekday(unittest.TestCase):
    def test_first_sunday_of_jan_2024(self):
        from vtimezone_rule_expander.core import _nth_weekday
        from datetime import date

        # Jan 7 2024 is the first Sunday.
        self.assertEqual(_nth_weekday(2024, 1, 6, 1), date(2024, 1, 7))

    def test_last_sunday_of_march_2024(self):
        from vtimezone_rule_expander.core import _nth_weekday
        from datetime import date

        # March 31 2024 is the last Sunday (EUS boundary).
        self.assertEqual(_nth_weekday(2024, 3, 6, -1), date(2024, 3, 31))

    def test_second_monday_of_oct_2024(self):
        from vtimezone_rule_expander.core import _nth_weekday
        from datetime import date

        # Oct 14 2024 (Columbus Day).
        self.assertEqual(_nth_weekday(2024, 10, 0, 2), date(2024, 10, 14))


class TestParseVTimezone(unittest.TestCase):
    def test_parses_tzid_and_subcomponents(self):
        content = (
            "BEGIN:VTIMEZONE\n"
            "TZID:America/New_York\n"
            "BEGIN:STANDARD\n"
            "DTSTART:19701101T020000\n"
            "TZOFFSETFROM:-0400\n"
            "TZOFFSETTO:-0500\n"
            "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\n"
            "TZNAME:EST\n"
            "END:STANDARD\n"
            "BEGIN:DAYLIGHT\n"
            "DTSTART:19700308T020000\n"
            "TZOFFSETFROM:-0500\n"
            "TZOFFSETTO:-0400\n"
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\n"
            "TZNAME:EDT\n"
            "END:DAYLIGHT\n"
            "END:VTIMEZONE\n"
        )
        parsed = parse_vtimezone(content)
        self.assertEqual(parsed["TZID"], "America/New_York")
        subs = parsed["subcomponents"]
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0]["TYPE"], "STANDARD")
        self.assertEqual(subs[1]["TYPE"], "DAYLIGHT")
        self.assertEqual(subs[0]["TZOFFSETFROM"], "-0400")
        self.assertEqual(subs[0]["RRULE"], "FREQ=YEARLY;BYMONTH=11;BYDAY=1SU")

    def test_handles_folded_lines(self):
        content = (
            "BEGIN:VTIMEZONE\n"
            "TZID:Europe/Berlin\n"
            "BEGIN:DAYLIGHT\n"
            "DTSTART:19700329T020000\n"
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU;\n"
            " INTERVAL=1\n"
            "TZOFFSETFROM:+0100\n"
            "TZOFFSETTO:+0200\n"
            "END:DAYLIGHT\n"
            "END:VTIMEZONE\n"
        )
        parsed = parse_vtimezone(content)
        rule = parsed["subcomponents"][0]["RRULE"]
        self.assertIn("INTERVAL=1", rule)


class TestExpandRRuleTransitions(unittest.TestCase):
    def test_first_sunday_of_november(self):
        from datetime import datetime

        rule = {"FREQ": "YEARLY", "BYMONTH": [11], "BYDAY": ["1SU"]}
        transitions = expand_rrule_transitions(
            dtstart_local="19701101T020000",
            from_offset=-4 * 3600,
            rule=rule,
            to_offset=-5 * 3600,
            is_dst=False,
            start_year=2024,
            end_year=2024,
        )
        self.assertEqual(len(transitions), 1)
        # Nov 3 2024 is the first Sunday. Wall clock 02:00 EDT (-0400) -> 06:00 UTC.
        self.assertEqual(transitions[0].when_utc, datetime(2024, 11, 3, 6, 0, 0))
        self.assertEqual(transitions[0].to_offset, -5 * 3600)
        self.assertFalse(transitions[0].is_dst)

    def test_second_sunday_of_march(self):
        from datetime import datetime

        rule = {"FREQ": "YEARLY", "BYMONTH": [3], "BYDAY": ["2SU"]}
        transitions = expand_rrule_transitions(
            dtstart_local="19700308T020000",
            from_offset=-5 * 3600,
            rule=rule,
            to_offset=-4 * 3600,
            is_dst=True,
            start_year=2024,
            end_year=2024,
        )
        self.assertEqual(len(transitions), 1)
        # March 10 2024 is the second Sunday. 02:00 EST (-0500) -> 07:00 UTC.
        self.assertEqual(transitions[0].when_utc, datetime(2024, 3, 10, 7, 0, 0))
        self.assertTrue(transitions[0].is_dst)

    def test_last_sunday_of_march(self):
        from datetime import datetime

        rule = {"FREQ": "YEARLY", "BYMONTH": [3], "BYDAY": ["-1SU"]}
        transitions = expand_rrule_transitions(
            dtstart_local="19700329T020000",
            from_offset=3600,
            rule=rule,
            to_offset=2 * 3600,
            is_dst=True,
            start_year=2024,
            end_year=2024,
        )
        # March 31 2024 is the last Sunday. 02:00 CET (+0100) -> 01:00 UTC.
        self.assertEqual(transitions[0].when_utc, datetime(2024, 3, 31, 1, 0, 0))

    def test_no_rule_emits_once_if_in_window(self):
        from datetime import datetime

        transitions = expand_rrule_transitions(
            dtstart_local="20240101T000000",
            from_offset=0,
            rule=None,
            to_offset=3600,
            is_dst=False,
            start_year=2024,
            end_year=2024,
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].when_utc, datetime(2024, 1, 1, 0, 0, 0))

    def test_no_rule_emits_nothing_outside_window(self):
        transitions = expand_rrule_transitions(
            dtstart_local="19900101T000000",
            from_offset=0,
            rule=None,
            to_offset=3600,
            is_dst=False,
            start_year=2024,
            end_year=2024,
        )
        self.assertEqual(transitions, [])

    def test_interval_skips_years(self):
        rule = {"FREQ": "YEARLY", "INTERVAL": 2, "BYMONTH": [3], "BYDAY": ["1SU"]}
        transitions = expand_rrule_transitions(
            dtstart_local="19700301T020000",
            from_offset=0,
            rule=rule,
            to_offset=3600,
            is_dst=True,
            start_year=2020,
            end_year=2024,
        )
        # 1970, 1972, ...; from 2020 we get 2020, 2022, 2024.
        years = [t.when_utc.year for t in transitions]
        self.assertEqual(years, [2020, 2022, 2024])

    def test_count_limits_emissions(self):
        rule = {
            "FREQ": "YEARLY",
            "BYMONTH": [3],
            "BYDAY": ["1SU"],
            "COUNT": 2,
        }
        transitions = expand_rrule_transitions(
            dtstart_local="19700301T020000",
            from_offset=0,
            rule=rule,
            to_offset=3600,
            is_dst=True,
            start_year=1970,
            end_year=2024,
        )
        self.assertEqual(len(transitions), 2)

    def test_until_date_terminates(self):
        rule = {
            "FREQ": "YEARLY",
            "BYMONTH": [3],
            "BYDAY": ["1SU"],
            "UNTIL": "20220101",
        }
        transitions = expand_rrule_transitions(
            dtstart_local="19700301T020000",
            from_offset=0,
            rule=rule,
            to_offset=3600,
            is_dst=True,
            start_year=1970,
            end_year=2024,
        )
        # 1970, 1971, ..., 2021 — 52 transitions. 2022 and beyond are cut by UNTIL.
        self.assertEqual(len(transitions), 52)
        self.assertEqual(transitions[-1].when_utc.year, 2021)

    def test_until_datetime_z_terminates(self):
        rule = {
            "FREQ": "YEARLY",
            "BYMONTH": [3],
            "BYDAY": ["1SU"],
            "UNTIL": "20210301T000000Z",
        }
        transitions = expand_rrule_transitions(
            dtstart_local="19700301T020000",
            from_offset=0,
            rule=rule,
            to_offset=3600,
            is_dst=True,
            start_year=1970,
            end_year=2024,
        )
        # UNTIL is 2021-03-01; the 2021 first-Sunday-of-March is March 7, which is
        # after UNTIL, so the last emission is 2020.
        self.assertEqual(transitions[-1].when_utc.year, 2020)

    def test_unsupported_freq_raises(self):
        rule = {"FREQ": "MONTHLY", "BYMONTH": [3]}
        with self.assertRaises(ValueError):
            expand_rrule_transitions(
                dtstart_local="19700301T020000",
                from_offset=0,
                rule=rule,
                to_offset=3600,
                is_dst=True,
                start_year=2024,
                end_year=2024,
            )


class TestExpandVTimezone(unittest.TestCase):
    """End-to-end tests on the public entry point."""

    US_EASTERN = (
        "BEGIN:VTIMEZONE\n"
        "TZID:America/New_York\n"
        "BEGIN:DAYLIGHT\n"
        "DTSTART:19700308T020000\n"
        "TZOFFSETFROM:-0500\n"
        "TZOFFSETTO:-0400\n"
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\n"
        "TZNAME:EDT\n"
        "END:DAYLIGHT\n"
        "BEGIN:STANDARD\n"
        "DTSTART:19701101T020000\n"
        "TZOFFSETFROM:-0400\n"
        "TZOFFSETTO:-0500\n"
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\n"
        "TZNAME:EST\n"
        "END:STANDARD\n"
        "END:VTIMEZONE\n"
    )

    def test_two_transitions_per_year(self):
        from datetime import datetime

        transitions = expand_vtimezone(self.US_EASTERN, 2024, 2024)
        self.assertEqual(len(transitions), 2)
        # Spring forward: March 10 2024, 02:00 EST -> 07:00 UTC, to -0400.
        self.assertEqual(transitions[0].when_utc, datetime(2024, 3, 10, 7, 0, 0))
        self.assertEqual(transitions[0].to_offset, -4 * 3600)
        self.assertTrue(transitions[0].is_dst)
        # Fall back: Nov 3 2024, 02:00 EDT -> 06:00 UTC, to -0500.
        self.assertEqual(transitions[1].when_utc, datetime(2024, 11, 3, 6, 0, 0))
        self.assertEqual(transitions[1].to_offset, -5 * 3600)
        self.assertFalse(transitions[1].is_dst)

    def test_sorted_by_utc(self):
        transitions = expand_vtimezone(self.US_EASTERN, 2023, 2024)
        utc_values = [t.when_utc for t in transitions]
        self.assertEqual(utc_values, sorted(utc_values))

    def test_empty_when_no_subcomponents(self):
        content = "BEGIN:VTIMEZONE\nTZID:Test\nEND:VTIMEZONE\n"
        self.assertEqual(expand_vtimezone(content, 2024, 2024), [])

    def test_transition_is_frozen(self):
        transitions = expand_vtimezone(self.US_EASTERN, 2024, 2024)
        with self.assertRaises(Exception):
            transitions[0].to_offset = 0  # type: ignore[misc]


class TestTransitionDataclass(unittest.TestCase):
    def test_fields(self):
        from datetime import datetime

        t = Transition(
            when_utc=datetime(2024, 3, 10, 7, 0, 0),
            to_offset=-4 * 3600,
            is_dst=True,
        )
        self.assertEqual(t.when_utc, datetime(2024, 3, 10, 7, 0, 0))
        self.assertEqual(t.to_offset, -14400)
        self.assertTrue(t.is_dst)


if __name__ == "__main__":
    unittest.main()
