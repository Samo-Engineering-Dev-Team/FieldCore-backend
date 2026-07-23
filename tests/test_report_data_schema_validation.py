"""Phase 4 regression guard for docs/report-schemas.md: write-time schema
validation must flag drift (e.g. mobile's pre-fix abbreviated repeater keys,
a diesel fill-up missing amount_used) without ever blocking the write.
"""

from app.services import report_support
from app.utils.enums import ReportType


def _capture_warnings(monkeypatch):
    calls: list[tuple[str, tuple, dict]] = []
    monkeypatch.setattr(
        report_support.LOG,
        "warning",
        lambda msg, *args, **kwargs: calls.append((msg, args, kwargs)),
    )
    return calls


def test_canonical_repeater_data_is_silent(monkeypatch) -> None:
    calls = _capture_warnings(monkeypatch)
    report_support.validate_report_data_schema(
        ReportType.REPEATER,
        {
            "siteObservations": {"perimeterFenceGood": {"passed": True}},
            "containerInterior": {"wallsAndFloorClean": {"passed": True}},
            "safetyObservations": {"basicRiskAssessmentPerformed": True},
            "environmentalSystems": {"airConditioning": {"temperature": "22"}},
            "siteConcerns": {"description": ""},
        },
    )
    assert calls == []


def test_legacy_mobile_repeater_keys_warn(monkeypatch) -> None:
    """The exact abbreviated-key shape the pre-fix mobile app sent (issue #1)."""
    calls = _capture_warnings(monkeypatch)
    report_support.validate_report_data_schema(
        ReportType.REPEATER,
        {
            "siteObs": {"perimeterFenceGood": {"passed": True}},
            "container": {"wallsAndFloorClean": {"passed": True}},
            "riskAssessment": True,
            "env": {"temperature": "22"},
            "concerns": "Loose cable tray",
        },
    )
    assert len(calls) == 1
    missing_fields = {err["loc"][0] for err in calls[0][1][1]}
    assert missing_fields == {
        "siteObservations",
        "containerInterior",
        "safetyObservations",
        "environmentalSystems",
        "siteConcerns",
    }


def test_diesel_fillup_missing_amount_used_warns(monkeypatch) -> None:
    """The exact gap behind issue #2: amount_used absent from a fill-up entry."""
    calls = _capture_warnings(monkeypatch)
    report_support.validate_report_data_schema(
        ReportType.DIESEL,
        {"diesel_fillups": [{"liters_filled": 10, "fill_reason": "Routine"}]},
    )
    assert len(calls) == 1
    assert calls[0][1][1][0]["loc"] == ("diesel_fillups", 0, "amount_used")


def test_canonical_diesel_data_is_silent(monkeypatch) -> None:
    calls = _capture_warnings(monkeypatch)
    report_support.validate_report_data_schema(
        ReportType.DIESEL,
        {"diesel_fillups": [{"liters_filled": 10, "amount_used": 150}]},
    )
    assert calls == []


def test_unknown_report_type_and_non_dict_data_are_ignored(monkeypatch) -> None:
    """No schema registered / malformed data must never raise or warn — the
    validator is a pure regression guard, not a gate."""
    calls = _capture_warnings(monkeypatch)
    report_support.validate_report_data_schema(ReportType.REPEATER, None)
    report_support.validate_report_data_schema(ReportType.REPEATER, "not-a-dict")
    assert calls == []
