"""
Hour-meter parsing and formatting.

A generator's meter is captured as `HHMM:SS` — hours, then minutes, joined by a
colon the UI inserts itself — and stored as integer seconds so two readings
subtract exactly. The legacy H/M notation ("2345H45M") is still sitting in the
diesel report payloads and is never rewritten, so the parser has to keep
reading it. The values exercised here are taken from the real fill-up log.
"""

from app.models.generator import Generator
from app.utils.funcs import format_hour_meter, parse_hour_meter


class TestParseHourMeter:
    def test_reads_the_canonical_colon_form(self) -> None:
        assert parse_hour_meter("2345:45") == 2345 * 3600 + 45 * 60
        assert parse_hour_meter("12:30") == 12 * 3600 + 30 * 60
        assert parse_hour_meter("0:00") == 0

    def test_reads_legacy_h_m_notation_still_in_diesel_payloads(self) -> None:
        assert parse_hour_meter("2345H45M") == parse_hour_meter("2345:45")
        assert parse_hour_meter("1877H05M") == 1877 * 3600 + 5 * 60
        assert parse_hour_meter("12H30M") == parse_hour_meter("12:30")
        # Hours with no minutes part.
        assert parse_hour_meter("2345H") == 2345 * 3600

    def test_is_case_and_space_insensitive(self) -> None:
        assert parse_hour_meter("2345h45m") == parse_hour_meter("2345:45")
        assert parse_hour_meter(" 2345H45M ") == parse_hour_meter("2345:45")

    def test_reads_a_bare_number_of_hours(self) -> None:
        # "1234.2" is the one decimal reading the seeded data carries.
        assert parse_hour_meter("1234.2") == round(1234.2 * 3600)
        assert parse_hour_meter(1234.2) == round(1234.2 * 3600)
        assert parse_hour_meter(2345) == 2345 * 3600

    def test_returns_none_rather_than_raising_on_junk(self) -> None:
        # A technician's submission must never be rejected over a meter reading.
        for junk in (None, "", "   ", "abc", "N/A", "--", True, False):
            assert parse_hour_meter(junk) is None

    def test_rejects_impossible_readings(self) -> None:
        assert parse_hour_meter("-5") is None
        assert parse_hour_meter(-5) is None
        # Minutes are minutes, not a free-running counter.
        assert parse_hour_meter("2345:99") is None
        assert parse_hour_meter("2345:60") is None


class TestFormatHourMeter:
    def test_renders_hours_and_zero_padded_minutes(self) -> None:
        assert format_hour_meter(2345 * 3600 + 45 * 60) == "2345:45"
        assert format_hour_meter(1877 * 3600 + 5 * 60) == "1877:05"
        assert format_hour_meter(0) == "0:00"

    def test_drops_stray_seconds_rather_than_rounding_up(self) -> None:
        # A meter reads in minutes; 2345:46 would overstate 2345:45:59.
        assert format_hour_meter(2345 * 3600 + 45 * 60 + 59) == "2345:45"

    def test_returns_none_for_missing_or_negative(self) -> None:
        assert format_hour_meter(None) is None
        assert format_hour_meter(-1) is None

    def test_round_trips_every_accepted_form(self) -> None:
        for reading in ("2345:45", "2345H45M", "1877H05M", "12:30"):
            assert format_hour_meter(parse_hour_meter(reading)) is not None


class TestSecondsSinceLastService:
    def _generator(self, current: int | None, at_service: int | None) -> Generator:
        return Generator(
            name="Gen 1",
            current_run_seconds=current,
            run_seconds_at_service=at_service,
        )

    def test_subtracts_the_two_readings(self) -> None:
        gen = self._generator(2345 * 3600, 2000 * 3600)
        assert gen.seconds_since_last_service == 345 * 3600
        assert format_hour_meter(gen.seconds_since_last_service) == "345:00"

    def test_is_none_when_either_reading_is_missing(self) -> None:
        assert self._generator(None, 2000 * 3600).seconds_since_last_service is None
        assert self._generator(2345 * 3600, None).seconds_since_last_service is None
        assert self._generator(None, None).seconds_since_last_service is None

    def test_never_goes_negative(self) -> None:
        # A current reading below the service reading means the meter was
        # replaced or mis-keyed; a negative value would read as a service that
        # has not happened yet.
        assert self._generator(100 * 3600, 2000 * 3600).seconds_since_last_service == 0
