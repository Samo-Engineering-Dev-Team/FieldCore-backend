"""
Section-completeness rules for the Datacenter/POP hosted-site routine form
(DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §4.5).

`is_hosted_site_section_complete` / `completed_hosted_site_section_count` /
`hosted_site_missing_fields` are the Python twin of
`hosted-site-definitions.ts` (web) and `lib/hosted-site-routine.ts` (mobile).
All three must agree on progress for the same report — these tests pin the
backend half of that contract against the two real fixtures and against
single-field mutations of each rule.
"""

import copy
import json
from pathlib import Path

from app.models.report_data import HostedSiteRoutineData
from app.services.report_support import (
    REQUIRED_HOSTED_SITE_SECTION_COUNT,
    completed_hosted_site_section_count,
    hosted_site_missing_fields,
    is_cabinet_complete,
    is_hosted_site_section_complete,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def parse(data: dict) -> HostedSiteRoutineData:
    return HostedSiteRoutineData.model_validate(data)


class TestFixtureCompleteness:
    def test_dc_fixture_is_4_of_4_with_no_missing_fields(self) -> None:
        model = parse(load("hosted_site_routine_dc.json"))
        assert REQUIRED_HOSTED_SITE_SECTION_COUNT == 4
        assert completed_hosted_site_section_count(model) == 4
        assert hosted_site_missing_fields(model) == []

    def test_pop_fixture_reflects_a_real_source_gap(self) -> None:
        # The POP workbook itself leaves both rectifier battery-charging
        # readings blank even though the rectifier block is checked "yes" —
        # a genuine gap in the signed-off source document, not a fixture
        # bug. The completeness helper must surface it, not paper over it.
        model = parse(load("hosted_site_routine_pop.json"))
        assert completed_hosted_site_section_count(model) == 3
        missing = hosted_site_missing_fields(model)
        assert {"sectionKey": "power_readings", "field": "rectifier:readings"} in missing


class TestDetailsSection:
    def test_blank_header_field_is_flagged(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["header"]["site_name"] = ""
        model = parse(data)
        assert is_hosted_site_section_complete("details", model) is False
        assert {"sectionKey": "details", "field": "site_name"} in hosted_site_missing_fields(
            model
        )

    def test_site_owner_access_ticket_optional(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["header"]["site_owner_access_ticket"] = None
        model = parse(data)
        assert is_hosted_site_section_complete("details", model) is True


class TestSiteChecksSection:
    def test_blank_issue_on_a_bad_answer_is_flagged(self) -> None:
        data = load("hosted_site_routine_pop.json")
        # `combustibles` is yes=bad; flip it to the bad answer with no issue.
        data["site_checks"]["combustibles"] = {"status": "yes", "issue": None}
        model = parse(data)
        assert is_hosted_site_section_complete("site_checks", model) is False
        assert {"sectionKey": "site_checks", "field": "combustibles:issue"} in (
            hosted_site_missing_fields(model)
        )

    def test_bad_answer_with_issue_is_complete(self) -> None:
        data = load("hosted_site_routine_pop.json")
        data["site_checks"]["combustibles"] = {"status": "yes", "issue": "Boxes stacked in room"}
        model = parse(data)
        assert is_hosted_site_section_complete("site_checks", model) is True

    def test_good_polarity_no_answer_needs_no_issue(self) -> None:
        data = load("hosted_site_routine_dc.json")
        # `access_safe` is yes=good; "no" is the bad answer for it.
        data["site_checks"]["access_safe"] = {"status": "yes", "issue": None}
        model = parse(data)
        assert is_hosted_site_section_complete("site_checks", model) is True


class TestPowerReadingsSection:
    def test_blank_reading_while_status_yes_is_incomplete(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["power_readings"]["rectifier"]["a_output_voltage"] = None
        model = parse(data)
        assert is_hosted_site_section_complete("power_readings", model) is False

    def test_same_blank_reading_is_fine_when_status_is_na(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["power_readings"]["rectifier"]["status"] = "n/a"
        data["power_readings"]["rectifier"]["a_output_voltage"] = None
        model = parse(data)
        assert is_hosted_site_section_complete("power_readings", model) is True

    def test_ups_no_status_needs_no_readings(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["power_readings"]["ups"] = {"status": "no"}
        model = parse(data)
        assert is_hosted_site_section_complete("power_readings", model) is True


class TestCabinetsSection:
    def test_removing_a_cabinet_photo_is_flagged_with_its_order(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["cabinets"][2]["cabinet_photo"] = None
        model = parse(data)
        assert is_hosted_site_section_complete("cabinets", model) is False
        assert {
            "sectionKey": "cabinets",
            "cabinetOrder": 3,
            "field": "cabinet_photo",
        } in hosted_site_missing_fields(model)

    def test_empty_cabinets_array_is_incomplete(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["cabinets"] = []
        model = parse(data)
        assert is_hosted_site_section_complete("cabinets", model) is False
        assert {"sectionKey": "cabinets", "field": "cabinets:empty"} in (
            hosted_site_missing_fields(model)
        )

    def test_single_cabinet_helper_matches_section_helper(self) -> None:
        model = parse(load("hosted_site_routine_dc.json"))
        assert all(is_cabinet_complete(c) for c in model.cabinets)


class TestOptionalSectionsDoNotBlockSubmit:
    def test_blank_other_issues_still_yields_4_of_4(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["other_issues"] = None
        model = parse(data)
        assert completed_hosted_site_section_count(model) == 4

    def test_empty_extra_sections_still_yields_4_of_4(self) -> None:
        data = load("hosted_site_routine_dc.json")
        data["extra_sections"] = []
        model = parse(data)
        assert completed_hosted_site_section_count(model) == 4
        assert is_hosted_site_section_complete("extra_sections", model) is True

    def test_extra_section_missing_a_photo_does_not_lower_the_required_count(self) -> None:
        data = copy.deepcopy(load("hosted_site_routine_pop.json"))
        data["extra_sections"][0]["photos"] = []
        model = parse(data)
        # extra_sections is optional — it's excluded from the denominator,
        # so a gap there must not change the required-section count.
        before = completed_hosted_site_section_count(parse(load("hosted_site_routine_pop.json")))
        assert completed_hosted_site_section_count(model) == before
        assert is_hosted_site_section_complete("extra_sections", model) is False
