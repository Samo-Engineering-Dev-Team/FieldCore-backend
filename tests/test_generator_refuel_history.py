"""
Merging a generator's refuels from the two places they live.

Litres only ever come from the diesel report — the ledger records what was
requested, never what was actually filled. Rand comes from the report before
the finance cutover and from the ledger after, so one refuel is never counted
twice. This is the Finance Dashboard's rule, reused; these guard that it
stays reused rather than quietly diverging.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.report_data import GeneratorDieselHistory, GeneratorRefuelEntry
from app.services.report_support import flatten_diesel_fillups

CUTOVER = datetime(2026, 6, 1, tzinfo=timezone.utc)
BEFORE = CUTOVER - timedelta(days=30)
AFTER = CUTOVER + timedelta(days=30)


def _report(created_at: datetime, fillups: list[dict], report_id: str = "r1"):
    return SimpleNamespace(
        id=report_id,
        created_at=created_at,
        data={"diesel_fillups": fillups},
        seacom_ref="SEA-1",
        technician=SimpleNamespace(user=SimpleNamespace(name="Ishmael", surname="Maumela")),
        task=SimpleNamespace(seacom_ref="SEA-1"),
    )


class TestFlattenDieselFillups:
    def test_flattens_one_entry_per_fillup_with_report_context(self) -> None:
        reports = [
            _report(
                BEFORE,
                [
                    {"gen_no": 1, "liters_filled": 100, "amount_used": 2400},
                    {"gen_no": 2, "liters_filled": 80, "amount_used": 1900},
                ],
            )
        ]
        entries = flatten_diesel_fillups(reports)

        assert len(entries) == 2
        assert {e.gen_no for e in entries} == {1, 2}
        assert all(e.technician_name == "Ishmael Maumela" for e in entries)
        assert all(e.seacom_ref == "SEA-1" for e in entries)
        assert all(e.fill_date == BEFORE for e in entries)

    def test_applies_the_shared_gen_no_rule(self) -> None:
        # An entry with no usable gen_no lands in generator 1 — the same rule
        # the per-site history has always applied. If these two ever diverged,
        # one fill would show under different units on the two screens.
        entries = flatten_diesel_fillups(
            [_report(BEFORE, [{"liters_filled": 50, "amount_used": 1200}])]
        )
        assert len(entries) == 1
        assert entries[0].gen_no == 1
        assert entries[0].gen_no_inferred is True

    def test_survives_payloads_that_are_not_arrays(self) -> None:
        for junk in (None, {}, "not a list", 42):
            report = SimpleNamespace(
                id="r",
                created_at=BEFORE,
                data={"diesel_fillups": junk},
                seacom_ref=None,
                technician=None,
                task=None,
            )
            assert flatten_diesel_fillups([report]) == []

    def test_skips_non_dict_entries_inside_the_array(self) -> None:
        entries = flatten_diesel_fillups(
            [_report(BEFORE, [{"gen_no": 1, "liters_filled": 10}, "junk", None])]  # type: ignore[list-item]
        )
        assert len(entries) == 1


class TestHistoryTotals:
    """The shape the API returns, independent of how it was assembled."""

    def _history(self, entries: list[GeneratorRefuelEntry]) -> GeneratorDieselHistory:
        fill_dates = sorted(e.fill_date for e in entries if e.fill_date)
        return GeneratorDieselHistory(
            generator_id="g1",
            generator_name="Gen 1",
            entries=entries,
            entry_count=len(entries),
            total_liters=round(sum(e.liters_filled for e in entries), 2),
            total_amount=round(sum(e.amount for e in entries), 2),
            first_fill_date=fill_dates[0] if fill_dates else None,
            last_fill_date=fill_dates[-1] if fill_dates else None,
        )

    def test_litres_come_only_from_report_entries(self) -> None:
        history = self._history(
            [
                GeneratorRefuelEntry(source="report", fill_date=BEFORE, liters_filled=100, amount=2400),
                GeneratorRefuelEntry(source="ledger", fill_date=AFTER, liters_filled=0, amount=3100),
            ]
        )
        assert history.total_liters == 100
        assert history.total_amount == 5500

    def test_a_post_cutover_report_entry_contributes_no_rand(self) -> None:
        # Its money is the ledger's row; counting both would double the spend.
        history = self._history(
            [
                GeneratorRefuelEntry(source="report", fill_date=AFTER, liters_filled=120, amount=0.0),
                GeneratorRefuelEntry(source="ledger", fill_date=AFTER, liters_filled=0, amount=2900),
            ]
        )
        assert history.total_liters == 120
        assert history.total_amount == 2900

    def test_an_empty_history_is_a_valid_answer(self) -> None:
        # A newly registered unit has no refuels; that is not an error.
        history = self._history([])
        assert history.entry_count == 0
        assert history.total_liters == 0
        assert history.first_fill_date is None


class TestGeneratorHistoryPdf:
    """The PDF is a different report from the per-site one — flat, two-source,
    with a Source column — so it has its own renderer and its own guard."""

    def test_renders_a_units_history(self) -> None:
        import base64
        from io import BytesIO

        import pdfplumber

        from app.services.pdf import PDFService

        history = GeneratorDieselHistory(
            generator_id="g1",
            generator_name="East yard Cummins",
            serial_no="SN-4471",
            site_name="Glencairn",
            entries=[
                GeneratorRefuelEntry(
                    source="report",
                    fill_date=BEFORE,
                    iso_week="WEEK 18",
                    liters_filled=120,
                    amount=2400,
                    technician_name="Ishmael Maumela",
                ),
                GeneratorRefuelEntry(
                    source="ledger",
                    fill_date=AFTER,
                    iso_week="WEEK 26",
                    liters_filled=0,
                    amount=3100,
                ),
            ],
            entry_count=2,
            total_liters=120,
            total_amount=5500,
            first_fill_date=BEFORE,
            last_fill_date=AFTER,
        )

        service = PDFService()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
        )
        service._fetch_image_bytes = lambda url: BytesIO(png)  # type: ignore[method-assign]
        service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

        buffer = service.generate_generator_diesel_history_pdf(history)
        with pdfplumber.open(BytesIO(buffer.getvalue())) as pdf:
            text = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

        assert "EAST YARD CUMMINS" in text
        assert "SN-4471" in text
        assert "GLENCAIRN" in text
        # Both sources are named, so a reader can see why a row has litres but
        # no Rand, or the reverse.
        assert "REPORT" in text
        assert "LEDGER" in text

    def test_renders_an_unassigned_unit_with_no_refuels(self) -> None:
        import base64
        from io import BytesIO

        import pdfplumber

        from app.services.pdf import PDFService

        history = GeneratorDieselHistory(
            generator_id="g2", generator_name="Spare unit", entries=[]
        )
        service = PDFService()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
        )
        service._fetch_image_bytes = lambda url: BytesIO(png)  # type: ignore[method-assign]
        service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

        buffer = service.generate_generator_diesel_history_pdf(history)
        with pdfplumber.open(BytesIO(buffer.getvalue())) as pdf:
            text = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

        assert "SPARE UNIT" in text
        assert "UNASSIGNED" in text
        assert "NO REFUELS RECORDED" in text
