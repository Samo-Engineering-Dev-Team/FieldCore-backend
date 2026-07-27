"""Canonical report `data`/`attachments` JSONB shapes, per report type.

These are the target schemas Phase 1-3 migrate mobile/frontend writers and the
PDF renderer (`app/services/pdf.py`) toward. `Report.data`/`Report.attachments`
remain untyped `dict[str, Any]` JSONB columns (see `app/models/report.py`) —
these models are a documentation + validation contract, not a DB migration.

Not yet wired into the write path (`app/api/v1/report.py`) or the PDF renderer.
See `docs/report-schemas.md` for the cross-repo contract and migration plan.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AttachmentFile(BaseModel):
    file_path: str | None = None
    public_url: str | None = None
    signed_url: str | None = None
    url: str | None = None
    original_name: str | None = None
    content_type: str | None = None
    size: int | None = None
    label: str | None = None


class GeoPhoto(AttachmentFile):
    lat: float | None = None
    lon: float | None = None
    address: str | None = None
    altitude: float | None = None
    speed: float | None = None
    captured_at: str | None = None
    index_number: int | None = None


class CheckItem(BaseModel):
    passed: bool
    issue: str | None = None


CheckMap = dict[str, CheckItem]


# ── Repeater Site Visit ──────────────────────────────────────────────────

SITE_OBSERVATION_KEYS = (
    "perimeterFenceGood",
    "siteYardClean",
    "containerExteriorClean",
    "generatorCanopiesClean",
    "gatesAndDoorsSecure",
    "securityCamerasGood",
    "outdoorLightsWorking",
    "areaOutsideClean",
    "accessRoadSafe",
    "accessGateLocked",
)

CONTAINER_INTERIOR_KEYS = (
    "wallsAndFloorClean",
    "lightingWorking",
    "cableGridGood",
    "odfNeat",
    "equipmentCabinetsClean",
    "noUnusualAlarms",
    "cabinetLockedAndKeyed",
    "noCombustibles",
    "noWaterIngressLights",
    "noWaterIngressOutdoor",
    "siteRegisterUpdated",
    "noDamageNeeded",
)


class NearbyConstructionWork(BaseModel):
    passed: bool
    issueDescription: str | None = None


class SafetyObservations(BaseModel):
    basicRiskAssessmentPerformed: bool
    nearbyConstructionWork: NearbyConstructionWork | None = None


class SiteConcerns(BaseModel):
    description: str = ""


class RepeaterReportData(BaseModel):
    routineType: str = ""
    dateRoutinePerformed: str | None = None
    nocRoutineTicketReference: str | None = None
    source: str | None = None
    powerSystems: dict[str, Any] = Field(default_factory=dict)
    gen1: dict[str, Any] = Field(default_factory=dict)
    gen2: dict[str, Any] = Field(default_factory=dict)
    # These five are required (the key must be present, even if its value is
    # empty) rather than defaulted: both mobile and web always send all five
    # on every save, including partial-progress autosaves — so a missing key
    # means a client regressed to the pre-fix abbreviated names (siteObs,
    # container, riskAssessment, env, concerns) rather than a legitimately
    # empty section. That's exactly the drift this schema exists to catch.
    siteObservations: CheckMap
    containerInterior: CheckMap
    safetyObservations: SafetyObservations
    environmentalSystems: dict[str, Any]
    siteConcerns: SiteConcerns


class RepeaterAttachments(BaseModel):
    files: list[AttachmentFile] = Field(default_factory=list)


# ── Diesel / Generator Refill ────────────────────────────────────────────


class DieselFillupEntry(BaseModel):
    gen_no: str | int | None = None
    site_id: str | None = None
    site_name: str | None = None
    amount_used: float = Field(description="Rand amount spent on this fill-up")
    liters_filled: float
    fill_reason: str | None = None
    gen_runtime_hours: str | float | None = None


class DieselReportData(BaseModel):
    diesel_fillups: list[DieselFillupEntry] = Field(default_factory=list)


class DieselAttachments(BaseModel):
    files: list[AttachmentFile] = Field(default_factory=list)


# ── Diesel site history ──────────────────────────────────────────────────
#
# Read-side shapes only. A "history entry" is one element of a report's
# `data.diesel_fillups` array, flattened together with the fields that live on
# the owning report (date, technician, ticket ref). Nothing writes these.


class DieselHistoryEntry(BaseModel):
    """One fill-up, flattened with its owning report's context."""

    report_id: str
    fill_date: datetime | None = Field(
        default=None, description="Report date; the fill-up itself carries no date"
    )
    iso_week: str = Field(default="N/A", description='ISO week label, e.g. "WEEK 30"')
    gen_no: int = Field(description="1 or 2; entries with no usable gen_no land in 1")
    gen_no_inferred: bool = Field(
        default=False, description="True when gen_no was absent/unparseable"
    )
    liters_filled: float = 0.0
    amount_used: float = 0.0
    fill_reason: str | None = None
    gen_runtime_hours: str | float | None = None
    technician_name: str | None = None
    seacom_ref: str | None = None


class DieselGeneratorHistory(BaseModel):
    """One generator's fill-ups for a site, with its own subtotals."""

    gen_no: int
    entries: list[DieselHistoryEntry] = Field(default_factory=list)
    entry_count: int = 0
    total_liters: float = 0.0
    total_amount: float = 0.0
    highest_runtime_minutes: int | None = None


class DieselSiteHistory(BaseModel):
    """Every diesel fill-up recorded against one site, split by generator."""

    site_id: str
    site_name: str
    date_from: datetime | None = None
    date_to: datetime | None = None
    first_fill_date: datetime | None = None
    last_fill_date: datetime | None = None
    generators: list[DieselGeneratorHistory] = Field(default_factory=list)
    entry_count: int = 0
    total_liters: float = 0.0
    total_amount: float = 0.0


# ── Routine Drive / Route Patrol ─────────────────────────────────────────


class ManholeInspection(BaseModel):
    id: str | None = None
    manhole_id: str | None = None
    photos: list[GeoPhoto] = Field(default_factory=list)
    remarks: str | None = None
    lid_locked: str | None = None
    ducts_sealed: str | None = None
    lid_disturbed: str | None = None
    can_be_unlocked: str | None = None
    clean_no_debris: str | None = None
    manhole_exposed: str | None = None
    marker_in_place: str | None = None
    chemical_threats: str | None = None
    corrosion_splice: str | None = None
    slack_management: str | None = None
    coordinates_on_file: str | None = None
    disturbance_erosion: str | None = None
    coordinates_recorded: str | None = None
    water_ingress_rodents: str | None = None


class BridgeCulvertCheck(BaseModel):
    id: str | None = None
    photos: list[GeoPhoto] = Field(default_factory=list)
    location: str | None = None
    coordinates: str | None = None
    mitigation: str | None = None
    flood_damage: str | None = None
    ground_movement: str | None = None
    risk_to_network: str | None = None


class ActivityCheck(BaseModel):
    id: str | None = None
    photos: list[GeoPhoto] = Field(default_factory=list)
    location: str | None = None
    coordinates: str | None = None
    risk_to_network: str | None = None
    mitigation: str | None = None


class RoutePatrolPhotos(BaseModel):
    all_photos: list[GeoPhoto] = Field(default_factory=list)
    noc_ticket: str | None = None
    final_notes: str | None = None
    form_version: str | None = None
    technician_name: str | None = None
    trip_start_photos: list[GeoPhoto] = Field(default_factory=list)
    trip_end_photos: list[GeoPhoto] = Field(default_factory=list)
    bridge_culvert_checks: list[BridgeCulvertCheck] = Field(default_factory=list)
    activity_checks: list[ActivityCheck] = Field(default_factory=list)
    manhole_inspections: list[ManholeInspection] = Field(default_factory=list)


class RoutePatrolReportData(BaseModel):
    source: str | None = None
    patrol_date: str | None = None
    route_segment: str | None = None
    weather_conditions: str | None = None
    anomalies_found: bool = False
    anomaly_details: str | None = None
    photos: RoutePatrolPhotos = Field(default_factory=RoutePatrolPhotos)


class RoutePatrolAttachments(BaseModel):
    files: list[AttachmentFile] = Field(default_factory=list)
