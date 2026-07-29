"""
Schema contract for the Datacenter/POP hosted-site routine reports.

Both report types share one `HostedSiteRoutineData` model (the source
workbooks are the same template, one version apart — see
DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §1). These tests pin the two
transcribed fixtures against that model and the invariants a bad write
must not be able to violate: an unknown checklist status, cabinets whose
`order` isn't 1-based/contiguous/unique, a visual alarm with no note, and
an attachment mirror that has drifted from the inline photos it's derived
from.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.report_data import CabinetInspection, HostedSiteRoutineData

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def base_cabinet(**overrides) -> dict:
    cabinet = {
        "order": 1,
        "location": "Row 1",
        "equipment_hosted": None,
        "locked_and_keys": "yes",
        "damages_observed": "no",
        "clean": "yes",
        "patching_neat": "yes",
        "visual_alarms": "no",
        "alarm_note": None,
        "pdu_photo": None,
        "cabinet_photo": None,
        "remarks": None,
    }
    cabinet.update(overrides)
    return cabinet


def base_data(**overrides) -> dict:
    data = {
        "source": "mobile",
        "form_version": "hosted-site-routine-1",
        "header": {
            "service_provider": "SAMO Telecoms",
            "routine_type": "SITE INSPECTION",
            "site_name": "Test Site",
            "technician_name": "Test Tech",
            "date_routine_performed": "2026-07-04",
            "snoc_routine_ticket": "SEACOM-1",
            "site_owner_access_ticket": None,
        },
        "site_checks": {},
        "power_readings": {
            "rectifier": {"status": "n/a"},
            "ups": {"status": "n/a"},
        },
        "cabinets": [base_cabinet()],
        "extra_sections": [],
        "other_issues": None,
    }
    data.update(overrides)
    return data


class TestFixturesValidate:
    def test_dc_fixture_validates(self) -> None:
        data = load_fixture("hosted_site_routine_dc.json")
        model = HostedSiteRoutineData.model_validate(data)
        assert len(model.cabinets) == 6
        assert model.header.site_name == "PE IS DC"

    def test_pop_fixture_validates(self) -> None:
        data = load_fixture("hosted_site_routine_pop.json")
        model = HostedSiteRoutineData.model_validate(data)
        assert len(model.cabinets) == 15
        assert model.header.site_name == "MTN TYGERBERG"

    def test_fixture_attachment_mirror_labels_derivable_from_inline_photos(self) -> None:
        # `attachments.files` is derived from `data`, never authoritative
        # (§4.4) — assert every inline photo yields a well-formed,
        # order-addressable `cabinet:<order>:{pdu,cabinet}` /
        # `extra:<order>:<index>` label, the join key both clients' mirror
        # builders use.
        for name in ("hosted_site_routine_dc.json", "hosted_site_routine_pop.json"):
            data = load_fixture(name)
            model = HostedSiteRoutineData.model_validate(data)
            expected_labels = set()
            for cabinet in model.cabinets:
                if cabinet.pdu_photo:
                    expected_labels.add(f"cabinet:{cabinet.order}:pdu")
                if cabinet.cabinet_photo:
                    expected_labels.add(f"cabinet:{cabinet.order}:cabinet")
            for section in model.extra_sections:
                for i in range(len(section.photos)):
                    expected_labels.add(f"extra:{section.order}:{i}")

            assert expected_labels, f"{name} should have at least one photo label"


class TestUnknownStatusRejected:
    def test_unknown_site_check_status_rejected(self) -> None:
        data = base_data(site_checks={"access_safe": {"status": "maybe"}})
        with pytest.raises(ValidationError):
            HostedSiteRoutineData.model_validate(data)

    def test_unknown_cabinet_check_status_rejected(self) -> None:
        data = base_data(cabinets=[base_cabinet(locked_and_keys="sure")])
        with pytest.raises(ValidationError):
            HostedSiteRoutineData.model_validate(data)

    def test_unknown_power_status_rejected(self) -> None:
        data = base_data(
            power_readings={
                "rectifier": {"status": "broken"},
                "ups": {"status": "n/a"},
            }
        )
        with pytest.raises(ValidationError):
            HostedSiteRoutineData.model_validate(data)


class TestCabinetOrder:
    def test_single_cabinet_order_one_is_valid(self) -> None:
        data = base_data(cabinets=[base_cabinet(order=1)])
        HostedSiteRoutineData.model_validate(data)

    def test_contiguous_1_based_orders_are_valid(self) -> None:
        data = base_data(
            cabinets=[base_cabinet(order=1), base_cabinet(order=2), base_cabinet(order=3)]
        )
        HostedSiteRoutineData.model_validate(data)

    def test_duplicate_order_rejected(self) -> None:
        data = base_data(cabinets=[base_cabinet(order=1), base_cabinet(order=1)])
        with pytest.raises(ValidationError):
            HostedSiteRoutineData.model_validate(data)

    def test_gap_in_order_rejected(self) -> None:
        data = base_data(cabinets=[base_cabinet(order=1), base_cabinet(order=3)])
        with pytest.raises(ValidationError):
            HostedSiteRoutineData.model_validate(data)

    def test_zero_based_order_rejected(self) -> None:
        data = base_data(cabinets=[base_cabinet(order=0)])
        with pytest.raises(ValidationError):
            HostedSiteRoutineData.model_validate(data)

    def test_empty_cabinets_list_is_valid_at_schema_level(self) -> None:
        # Section-completeness ("at least 1 cabinet") is a §4.5 progress
        # rule, not a schema constraint — an in-progress draft with zero
        # cabinets must still parse.
        data = base_data(cabinets=[])
        HostedSiteRoutineData.model_validate(data)


class TestAlarmNoteRequiredWhenVisualAlarmsYes:
    def test_alarm_note_required_when_yes(self) -> None:
        with pytest.raises(ValidationError):
            CabinetInspection.model_validate(
                base_cabinet(visual_alarms="yes", alarm_note=None)
            )

    def test_blank_alarm_note_rejected_when_yes(self) -> None:
        with pytest.raises(ValidationError):
            CabinetInspection.model_validate(
                base_cabinet(visual_alarms="yes", alarm_note="   ")
            )

    def test_alarm_note_present_when_yes_is_valid(self) -> None:
        CabinetInspection.model_validate(
            base_cabinet(visual_alarms="yes", alarm_note="Alarms on Controller Module")
        )

    def test_alarm_note_optional_when_no_or_na(self) -> None:
        CabinetInspection.model_validate(base_cabinet(visual_alarms="no", alarm_note=None))
        CabinetInspection.model_validate(base_cabinet(visual_alarms="n/a", alarm_note=None))
