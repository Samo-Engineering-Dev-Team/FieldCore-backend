"""Canonical report `data`/`attachments` JSONB shapes, per report type.

These are the target schemas Phase 1-3 migrate mobile/frontend writers and the
PDF renderer (`app/services/pdf.py`) toward. `Report.data`/`Report.attachments`
remain untyped `dict[str, Any]` JSONB columns (see `app/models/report.py`) —
these models are a documentation + validation contract, not a DB migration.

Not yet wired into the write path (`app/api/v1/report.py`) or the PDF renderer.
See `docs/report-schemas.md` for the cross-repo contract and migration plan.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


# ── Per-generator refuel history ─────────────────────────────────────────
#
# Read-side shapes only. A unit's refuels come from two places and neither is
# complete on its own:
#
#   * diesel report JSON — litres, runtime, fill reason, and the Rand recorded
#     in the field. Identifies the unit only by free-text gen_no.
#   * the funds ledger — the Rand actually disbursed, against a real FK.
#
# `finance_dashboard` already fixes the rule: litres always come from the
# report, because the ledger never records what was actually filled; Rand comes
# from the report for pre-cutover fills and from the ledger after. This reuses
# that rule rather than inventing a second one.


class GeneratorRefuelEntry(BaseModel):
    """One refuel against a unit, from either source."""

    source: str = Field(description='"report" or "ledger"')
    fill_date: datetime | None = None
    iso_week: str = Field(default="N/A")
    liters_filled: float = 0.0
    amount: float = 0.0
    fill_reason: str | None = None
    gen_runtime_hours: str | float | None = None
    technician_name: str | None = None
    seacom_ref: str | None = None
    # Exactly one of these is set, matching `source`.
    report_id: str | None = None
    funds_request_id: str | None = None


class GeneratorDieselHistory(BaseModel):
    """Every refuel recorded against one generator, both sources merged."""

    generator_id: str
    generator_name: str
    serial_no: str | None = None
    site_id: str | None = None
    site_name: str = ""
    date_from: datetime | None = None
    date_to: datetime | None = None
    first_fill_date: datetime | None = None
    last_fill_date: datetime | None = None
    entries: list[GeneratorRefuelEntry] = Field(default_factory=list)
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


# ── Hosted Site Routine (Datacenter / POP) ───────────────────────────────
#
# Backs ReportType.DATACENTER and ReportType.POP. Both workbooks are the same
# source template one version apart (see DC_POP_REPORTS_IMPLEMENTATION_PLAN.md
# §1) — one schema, parameterized by report type for title/cover art/default
# routine type, not two.

RoutineCheckStatus = Literal["yes", "no", "n/a"]


class HostedSiteHeader(BaseModel):
    service_provider: str
    routine_type: str
    site_name: str
    technician_name: str
    date_routine_performed: str
    snoc_routine_ticket: str
    site_owner_access_ticket: str | None = None


class SiteCheckItem(BaseModel):
    status: RoutineCheckStatus
    issue: str | None = None


# Ordered, stable keys — independent of the source labels so relabelling a
# question never orphans stored data.
SITE_CHECK_KEYS = (
    "access_safe",
    "perimeter_fence",
    "ac_dbs_locked",
    "gates_doors_locked",
    "room_clean",
    "combustibles",
    "aircon_working",
    "lighting",
    "env_monitoring",
    "fire_monitoring",
    "access_monitoring",
    "aircon_controller",
)


class SiteCheckLabel(BaseModel):
    label: str
    bad_when: RoutineCheckStatus


# Labels transcribed verbatim from the workbook (including its typos) so the
# rendered PDF matches the document the client already signs off. `bad_when`
# drives colour without hardcoding per-question polarity logic elsewhere —
# every item is yes=good except `combustibles`, which is yes=bad.
SITE_CHECK_LABELS: dict[str, SiteCheckLabel] = {
    "access_safe": SiteCheckLabel(
        label="Is the acces to the site/container/room safe?", bad_when="no"
    ),
    "perimeter_fence": SiteCheckLabel(
        label="Is the Perimeter fence condition in order?", bad_when="no"
    ),
    "ac_dbs_locked": SiteCheckLabel(
        label="AC DB's safe and securely locked", bad_when="no"
    ),
    "gates_doors_locked": SiteCheckLabel(
        label="Gates and doors securely locked and locks funtional? ", bad_when="no"
    ),
    "room_clean": SiteCheckLabel(label="Equipment room clean?", bad_when="no"),
    "combustibles": SiteCheckLabel(
        label="Any combustable materials in site/container/room - boxes, etc?",
        bad_when="yes",
    ),
    "aircon_working": SiteCheckLabel(label="Airconditioner/s working ", bad_when="no"),
    "lighting": SiteCheckLabel(
        label="Is the lighting in order and working?", bad_when="no"
    ),
    "env_monitoring": SiteCheckLabel(
        label="Is the Site/room/container Environmental Monitoring system working and monitored",
        bad_when="no",
    ),
    "fire_monitoring": SiteCheckLabel(
        label="Is the Site/room/container fire monitoring system working and monitored",
        bad_when="no",
    ),
    "access_monitoring": SiteCheckLabel(
        label="Is the Site/room/container access monitoring system working and monitored",
        bad_when="no",
    ),
    "aircon_controller": SiteCheckLabel(
        label="Is the Site/room/container fitted with an Aircon controller system?",
        bad_when="no",
    ),
}


class RectifierReadings(BaseModel):
    status: RoutineCheckStatus
    a_output_voltage: str | None = None
    a_load_current: str | None = None
    a_battery_charging_current: str | None = None
    b_output_voltage: str | None = None
    b_load_current: str | None = None
    b_battery_charging_current: str | None = None


class UpsReadings(BaseModel):
    status: RoutineCheckStatus
    a_load_percent: str | None = None
    b_load_percent: str | None = None
    a_battery_capacity_percent: str | None = None
    b_battery_capacity_percent: str | None = None
    a_battery_charge_voltage: str | None = None
    b_battery_charge_voltage: str | None = None


class PowerReadings(BaseModel):
    rectifier: RectifierReadings
    ups: UpsReadings


class CabinetCheckLabel(BaseModel):
    label: str
    bad_when: RoutineCheckStatus


# `damages_observed` and `visual_alarms` are yes=bad; the other three are
# yes=good — same polarity-as-data rule as SITE_CHECK_LABELS.
CABINET_CHECK_LABELS: dict[str, CabinetCheckLabel] = {
    "locked_and_keys": CabinetCheckLabel(
        label="Is the cabinet locked and keys available?", bad_when="no"
    ),
    "damages_observed": CabinetCheckLabel(
        label="Any damages observed when inspecting the cabinet??", bad_when="yes"
    ),
    "clean": CabinetCheckLabel(label="Is the cabinet clean?", bad_when="no"),
    "patching_neat": CabinetCheckLabel(
        label="Is the fibre patching and routing in the cabinet between ODF/Patch Panels and devices neat?",
        bad_when="no",
    ),
    "visual_alarms": CabinetCheckLabel(
        label="Are there any visual alarms on equipment in cabinet? Note",
        bad_when="yes",
    ),
}


class CabinetInspection(BaseModel):
    order: int = Field(description="1-based, authoritative render order")
    location: str
    equipment_hosted: str | None = None
    locked_and_keys: RoutineCheckStatus
    damages_observed: RoutineCheckStatus
    clean: RoutineCheckStatus
    patching_neat: RoutineCheckStatus
    visual_alarms: RoutineCheckStatus
    alarm_note: str | None = None
    pdu_photo: GeoPhoto | None = None
    cabinet_photo: GeoPhoto | None = None
    remarks: str | None = None

    @model_validator(mode="after")
    def _alarm_note_required_when_visual_alarms_yes(self) -> "CabinetInspection":
        if self.visual_alarms == "yes" and not (self.alarm_note or "").strip():
            raise ValueError(
                "alarm_note is required when visual_alarms is 'yes'"
            )
        return self


class ExtraPhotoSection(BaseModel):
    """A trailing, technician-named photo section after the cabinets (e.g. 'SITE-BACK VIEW')."""

    order: int
    label: str
    photos: list[GeoPhoto] = Field(default_factory=list)
    remarks: str | None = None


class HostedSiteSectionMeta(BaseModel):
    key: str
    number: int
    label: str
    hint: str
    required: bool


# One shared, ordered section definition — drives capture order, view/edit
# order, PDF order and progress counting on both clients, so they cannot
# disagree (DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §4.5). This is also the
# order authority for the PDF renderer (Phase 3).
HOSTED_SITE_SECTIONS: list[HostedSiteSectionMeta] = [
    HostedSiteSectionMeta(
        key="details",
        number=1,
        label="Details",
        hint="Service provider, routine type, site, technician, date and ticket references.",
        required=True,
    ),
    HostedSiteSectionMeta(
        key="site_checks",
        number=2,
        label="Site checklist",
        hint="All 12 site checks answered, with an issue description on every finding.",
        required=True,
    ),
    HostedSiteSectionMeta(
        key="power_readings",
        number=3,
        label="Power readings",
        hint="Rectifier and UPS status, with readings recorded whenever a block is checked.",
        required=True,
    ),
    HostedSiteSectionMeta(
        key="cabinets",
        number=4,
        label="Cabinets",
        hint="Every cabinet in the room, inspected one at a time with its own two photos.",
        required=True,
    ),
    HostedSiteSectionMeta(
        key="extra_sections",
        number=5,
        label="Extra sections",
        hint="Optional trailing photo sections, e.g. a site-back view.",
        required=False,
    ),
    HostedSiteSectionMeta(
        key="other_issues",
        number=6,
        label="Other issues",
        hint="Anything else requiring attention or investigation. May be left blank.",
        required=False,
    ),
]


class HostedSiteRoutineData(BaseModel):
    source: Literal["mobile", "web"]
    form_version: str = "hosted-site-routine-1"
    header: HostedSiteHeader
    site_checks: dict[str, SiteCheckItem]
    power_readings: PowerReadings
    cabinets: list[CabinetInspection] = Field(default_factory=list)
    extra_sections: list[ExtraPhotoSection] = Field(default_factory=list)
    other_issues: str | None = None

    @model_validator(mode="after")
    def _cabinet_order_is_1_based_and_contiguous(self) -> "HostedSiteRoutineData":
        orders = [c.order for c in self.cabinets]
        if orders and sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError(
                f"cabinet order must be 1-based, contiguous and unique; got {orders}"
            )
        return self


class HostedSiteAttachments(BaseModel):
    """`label` grammar: `cabinet:<order>:pdu`, `cabinet:<order>:cabinet`, `extra:<order>:<index>`."""

    files: list[AttachmentFile] = Field(default_factory=list)
