"""
Carrying an inspection's hour-meter reading onto the generator.

The generator inspection is the only moment someone actually reads the meter, so
submitting one is what keeps `Generator.current_run_seconds` from going stale.
The writeback is shared by the repeater report — where inspections are really
captured — and the routine inspection service, so the two cannot drift.

`data` is untyped JSONB, so these guard the shapes that actually arrive and the
rules that stop a submission being lost or a meter being rewound.
"""

from types import SimpleNamespace

from app.models import Generator
from app.services.report_support import (
    record_generator_meter_readings,
    section_hour_meter_reading,
)


class _FakeSession:
    """Collects what would be persisted; the writeback never queries."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _generator(current: int | None = None) -> Generator:
    return Generator(name="Gen 1", current_run_seconds=current)


def _inspection(data: dict, gen1: Generator | None = None) -> SimpleNamespace:
    """
    Stand-in for a loaded inspection.

    A real RoutineInspection cannot be built without a SQLAlchemy session — its
    relationship attributes need instance state — and the writeback only ever
    reads `data`, `gen1_generator` and `gen2_generator`. The FK wiring itself is
    covered by the migration, not here.
    """
    return SimpleNamespace(data=data, gen1_generator=gen1, gen2_generator=None)


class TestSectionReading:
    def test_reads_the_nested_questions_shape_the_forms_write(self) -> None:
        data = {"gen1": {"questions": {"standbyHourMeterAfterTest": "2345:45"}}}
        assert section_hour_meter_reading(data, "gen1") == "2345:45"

    def test_reads_the_flat_shape_older_drafts_write(self) -> None:
        data = {"gen1": {"standbyHourMeterAfterTest": "2345:45"}}
        assert section_hour_meter_reading(data, "gen1") == "2345:45"

    def test_returns_none_for_shapes_that_carry_no_reading(self) -> None:
        assert section_hour_meter_reading({}, "gen1") is None
        assert section_hour_meter_reading({"gen1": {}}, "gen1") is None
        assert section_hour_meter_reading({"gen1": "not a dict"}, "gen1") is None
        assert section_hour_meter_reading({"gen2": {}}, "gen1") is None
        assert section_hour_meter_reading(None, "gen1") is None
        assert section_hour_meter_reading("not a dict", "gen1") is None


class TestRecordMeterReadings:
    def test_writes_the_reading_onto_the_linked_unit(self) -> None:
        generator = _generator()
        inspection = _inspection(
            {"gen1": {"questions": {"standbyHourMeterAfterTest": "2345:45"}}}, generator
        )
        session = _FakeSession()
        record_generator_meter_readings(inspection, session)  # type: ignore[arg-type]

        assert generator.current_run_seconds == 2345 * 3600 + 45 * 60
        assert generator in session.added

    def test_does_nothing_for_a_section_with_no_linked_unit(self) -> None:
        # Every inspection recorded before the asset register existed.
        inspection = _inspection(
            {"gen1": {"questions": {"standbyHourMeterAfterTest": "2345:45"}}}, None
        )
        session = _FakeSession()
        record_generator_meter_readings(inspection, session)  # type: ignore[arg-type]
        assert session.added == []

    def test_skips_an_unparseable_reading_rather_than_raising(self) -> None:
        # The submission is the technician's work; it must not be rejected over
        # a meter value.
        generator = _generator(current=100 * 3600)
        inspection = _inspection(
            {"gen1": {"questions": {"standbyHourMeterAfterTest": "N/A"}}}, generator
        )
        session = _FakeSession()
        record_generator_meter_readings(inspection, session)  # type: ignore[arg-type]

        assert generator.current_run_seconds == 100 * 3600
        assert session.added == []

    def test_never_rewinds_a_meter(self) -> None:
        # Re-submitting an older inspection must not walk the unit backwards.
        generator = _generator(current=5000 * 3600)
        inspection = _inspection(
            {"gen1": {"questions": {"standbyHourMeterAfterTest": "2345:45"}}}, generator
        )
        session = _FakeSession()
        record_generator_meter_readings(inspection, session)  # type: ignore[arg-type]

        assert generator.current_run_seconds == 5000 * 3600
        assert session.added == []

    def test_accepts_the_legacy_h_m_notation(self) -> None:
        generator = _generator()
        inspection = _inspection(
            {"gen1": {"questions": {"standbyHourMeterAfterTest": "2345H45M"}}}, generator
        )
        record_generator_meter_readings(inspection, _FakeSession())  # type: ignore[arg-type]
        assert generator.current_run_seconds == 2345 * 3600 + 45 * 60

    def test_records_each_section_against_its_own_unit(self) -> None:
        gen1, gen2 = _generator(), _generator()
        gen2.name = "Gen 2"
        inspection = _inspection(
            {
                "gen1": {"questions": {"standbyHourMeterAfterTest": "1000:00"}},
                "gen2": {"questions": {"standbyHourMeterAfterTest": "2000:30"}},
            },
            gen1,
        )
        inspection.gen2_generator = gen2
        record_generator_meter_readings(inspection, _FakeSession())  # type: ignore[arg-type]

        assert gen1.current_run_seconds == 1000 * 3600
        assert gen2.current_run_seconds == 2000 * 3600 + 30 * 60
