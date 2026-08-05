"""
SheqSubmission model — one completed instance of a SHEQ safety checklist
(SHEQ-CHECKLISTS-PLAN.md §5). A sibling table to `Report`, not an extension of
it: `Report.task_id` is NOT NULL, and a vehicle check or journey management
plan has no task.

Four checklist types (`SheqChecklistType`) share this one table, one API
surface and one signature mechanism; each keeps its own `data` shape (§6) and
its own capture form per client — the same house pattern as
`ReportType.DATACENTER`/`ReportType.POP` sharing `Report`.

`summary` is a denormalised set of compliance counters (§8.4), recomputed by
the service on every mutation so the compliance endpoint reads indexed
columns and a shallow JSONB dict instead of scanning `data` across every row.

`signatures` is a list of signature records (§7.3) — one entry per drawn or
typed mark, each carrying its own audit metadata. Roster signatures on the
Daily Risk Assessment (§7.5) live in this same list, `roster_index` set.
"""

from datetime import date, datetime
from typing import Any, TYPE_CHECKING
from uuid import UUID

from sqlmodel import SQLModel, Field, DateTime, Relationship
from sqlalchemy import Index, text
from sqlalchemy.dialects.postgresql import JSONB

from .base import BaseDB
from app.utils.enums import SheqChecklistType, SheqStatus

if TYPE_CHECKING:
    from .technician import Technician

# ── Shared section/label constants (SHEQ-CHECKLISTS-PLAN.md §6.3/§6.4) ──────
# Verbatim source labels, transcribed 2026-08-05. This is the backend's copy of
# the one shared section definition mirrored across all three repos (§5) — the
# same role `HOSTED_SITE_SECTIONS` plays for Datacenter/POP reports. Used by
# both `sheq_validation.py` (row/item membership) and `pdf.py` (verbatim
# labels). Do not paraphrase a label — the printed PDF must match the source
# document a supervisor is comparing it against.

# Company Vehicle Daily Checklist — pre-trip inspection rows, verbatim.
VEHICLE_PRE_TRIP_LABELS: dict[str, str] = {
    "tyres": "Tyres (pressure, tread, damage)",
    "lights": "Lights (headlights, brake, reverse, indicators)",
    "mirrors_windows": "Mirrors & Windows (clean/intact)",
    "wipers_washer": "Wipers & Washer Fluid",
    "horn": "Horn",
    "brakes": "Brakes (foot & hand)",
    "engine_oil": "Engine Oil Level",
    "coolant": "Coolant Level",
    "fuel_level": "Fuel Level",
    "tools_spare_wheel": "Tools & Spare Wheel",
}

# Daily Risk Assessment checklist matrix — group key -> {item key: label}.
# Two groups both use `barricading_available`; that is not a collision, since
# each group nests its own dict.
DAILY_RISK_MATRIX: dict[str, dict[str, str]] = {
    "site_establishment": {
        "employees_on_site": "Is all employees on site",
        "equipment_on_site": "Is all equipment on site",
        "pre_inspection_for_safety": "Pre Inspection for Safety",
    },
    "hand_tools": {
        "tools_good_condition": "Are all hand tools in good condition?",
        "employees_knowledgeable": "Employees knowledgeable on use?",
    },
    "road_reserve": {
        "early_warning_signs": "Early warning signs up? (speed reduction, workmen ahead, delineators and flagmen)",
        "reflective_vests_worn": "All personnel wearing reflective vest gear?",
        "walkways_clear": "Walkways clear for pedestrians?",
        "rubble_area_barricaded": "Dedicated area for rubble barricaded?",
    },
    "excavation": {
        "barricading_available": "Barricading readily available?",
        "trench_inspected_safe": "Inspection of trench/excavation-safe to work in",
        "soil_heaps_out_of_public_path": "Soil heaps placed out of public part?",  # sic
        "soil_heaps_away_from_trench": "Soil heaps away from trench/excavation?",
        "danger_tape_correct_depth_and_facing": (
            "Danger tape is placed correctly on current depth with writing "
            "facing top side of trench"
        ),
    },
    "cable_duct_laying": {
        "gloves_worn": "All employees wearing hand gloves?",
        "trip_fall_hazards_removed": "Trip/fall hazards removed from path of employees",
        "pull_sequence_understood": "Sequence/direction to pull understood?",
        "barricading_available": "Barricading available?",
        "trench_backfilled": "Trench/excavation backfilled?",
        "pinch_points_identified": "Pinch points identified and communicated?",
    },
    "ppe": {
        "gloves_issued": "Gloves issued?",
        "safety_shoes_issued": "Safety shoes issued?",
        "hearing_protection": "Hearing Protection?",
        "reflective_jacket": "Reflective jacket?",
    },
    "de_coiling": {
        "gloves_worn": "Must wear gloves",
        "decoiling_behind_barricading": "All-de-coiling done behind barricading",
        "bedding_correct": "Ensure bedding is correct",
    },
}

DAILY_RISK_MATRIX_GROUP_TITLES: dict[str, str] = {
    "site_establishment": "Site Establishment",
    "hand_tools": "Working with Hand Tools",
    "road_reserve": "Working Next to/in Road Reserve",
    "excavation": "Excavation Work (Bedding, Padding, Danger Tape)",
    "cable_duct_laying": "Laying of Cable/Ducts, De-coiling",
    "ppe": "Personnel Protective Clothing",
    "de_coiling": "De Coiling",
}

# Technician Master Safety & Operations Checklist — ordered sections, each an
# ordered {row id: label} dict. Row ids match the source's own numbering.
MASTER_SAFETY_SECTIONS: dict[str, dict[str, str]] = {
    "pre_job_safety": {
        "1.1": "Valid work permit obtained",
        "1.2": "Risk assessment (HIRA/JSA) completed",
        "1.3": "Toolbox talk conducted",
        "1.4": "Emergency contacts available",
        "1.5": "Fire extinguisher present & valid",
        "1.6": "Weather conditions assessed",
    },
    "vehicle_safety": {
        "2.1": "Vehicle roadworthy (tyres, brakes, lights)",
        "2.2": "Tyres tread and pressure acceptable",
        "2.3": "Lights, indicators operational",
        "2.4": "No oil/fuel leaks",
        "2.5": "Seatbelts functional",
        "2.6": "Fire extinguisher present",
        "2.7": "First aid kit available",
        "2.8": "Fuel stored in approved containers",
        "2.9": "Fuel containers secured",
        "2.10": "No fuel vapours present",
    },
    "confined_space_pest": {
        "3.1": "Confined space permit issued",
        "3.2": "Gas testing complete (O2, CO, H2S)",
        "3.3": "Ventilation provided",
        "3.4": "Standby/rescue person present",
        "3.5": "Safe access & egress",
        "3.6": "Ant infestation checked",
        "3.7": "Bees present/controlled",
        "3.8": "Wasp nests checked",
        "3.9": "Rodents (rats/mice) inspected",
        "3.10": "Snakes checked",
        "3.11": "Spiders/insects inspected",
        "3.12": "Bird/bat droppings checked",
        "3.13": "Pest control measures implemented",
    },
    "working_at_heights": {
        "4.1": "Fall protection plan in place",
        "4.2": "Harness inspected",
        "4.3": "Anchor points secure",
        "4.4": "Ladders stable",
        "4.5": "Drop zone controlled",
    },
    "microwave_rf": {
        "5.1": "RF exposure assessed",
        "5.2": "Transmitters isolated",
        "5.3": "Safe distance maintained",
        "5.4": "RF monitor used",
        "5.5": "RF signage present",
    },
    "generator_servicing": {
        "6.1": "LOTO applied",
        "6.2": "No fuel leaks",
        "6.3": "Battery isolated",
        "6.4": "Ventilation adequate",
        "6.5": "Fire extinguisher nearby",
    },
    "air_conditioning": {
        "7.1": "Power isolated",
        "7.2": "Refrigerant handled safely",
        "7.3": "No gas leaks",
        "7.4": "Ventilation adequate",
    },
    "fibre_internet": {
        "8.1": "Manhole secured",
        "8.2": "Traffic control in place",
        "8.3": "Splicing area clean",
        "8.4": "Fibre waste disposed safely",
        "8.5": "Confined space compliance followed",
    },
}

MASTER_SAFETY_SECTION_TITLES: dict[str, str] = {
    "pre_job_safety": "Pre-job Safety & Administration",
    "vehicle_safety": "Vehicle Safety & Fuel Transport",
    "confined_space_pest": "Confined Space, Biological & Pest Hazards",
    "working_at_heights": "Working at Heights",
    "microwave_rf": "Microwave & RF Work",
    "generator_servicing": "Generator Servicing",
    "air_conditioning": "Air Conditioning",
    "fibre_internet": "Fibre / Internet Work",
}

# The same 4 photo-evidence slots repeat verbatim under every section.
MASTER_SAFETY_PHOTO_SLOTS: dict[str, str] = {
    "work_area_setup": "Work Area Setup",
    "equipment_condition": "Equipment Condition",
    "hazards_identified": "Hazards Identified",
    "controls_implemented": "Controls Implemented",
}


class BaseSheqSubmission(SQLModel):
    checklist_type: SheqChecklistType = Field(nullable=False, index=True)
    performed_on: date = Field(
        nullable=False,
        description="Calendar date the checklist was performed — DATE, not a "
        "timestamp, so a SAST/UTC boundary never shifts it to the wrong day.",
    )
    technician_id: UUID = Field(foreign_key="technicians.id", nullable=False)
    task_id: UUID | None = Field(default=None, foreign_key="tasks.id", nullable=True)
    site_id: UUID | None = Field(default=None, foreign_key="sites.id", nullable=True)
    data: dict[str, Any] = Field(
        sa_type=JSONB, nullable=False,
        description="Per-checklist-type shape (§6), validated by sheq_validation.py",
    )
    attachments: dict[str, Any] | None = Field(
        default=None, sa_type=JSONB,
        description="{ slot_key: [file_ref, ...] } — mirrors Report.attachments",
    )
    summary: dict[str, Any] | None = Field(
        default=None, sa_type=JSONB,
        description="Denormalised compliance counters, recomputed on every mutation (§8.4)",
    )
    signatures: list[dict[str, Any]] = Field(
        default_factory=list, sa_type=JSONB,
        description="One record per drawn/typed signature, including roster signatures (§7.3)",
    )


class SheqSubmission(BaseDB, BaseSheqSubmission, table=True):
    __tablename__ = "sheq_submissions"  # type: ignore

    status: SheqStatus = Field(default=SheqStatus.DRAFT, nullable=False, index=True)
    submitted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore
    signed_off_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore
    supervisor_user_id: UUID | None = Field(default=None, foreign_key="users.id", nullable=True)

    technician: "Technician" = Relationship(back_populates="sheq_submissions")

    # Partial indexes (deleted_at IS NULL) per SHEQ-CHECKLISTS-PLAN.md §9.3 —
    # declared here so Database.init()'s create_all() and the Alembic
    # migration produce identical schema.
    __table_args__ = (
        Index(
            "ix_sheq_submissions_technician_performed_on",
            "technician_id",
            text("performed_on DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_sheq_submissions_type_status",
            "checklist_type",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_sheq_submissions_task",
            "task_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_sheq_submissions_site",
            "site_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def submit(self) -> None:
        self.status = SheqStatus.SUBMITTED
        self.submitted_at = datetime.now(self.created_at.tzinfo) if self.created_at else None

    def sign_off(self, supervisor_user_id: UUID, signed_off_at: datetime) -> None:
        self.status = SheqStatus.SIGNED_OFF
        self.supervisor_user_id = supervisor_user_id
        self.signed_off_at = signed_off_at


class SheqSubmissionCreate(SQLModel):
    checklist_type: SheqChecklistType
    performed_on: date
    # Auto-filled from JWT for a technician; management may override.
    technician_id: UUID | None = Field(default=None)
    task_id: UUID | None = Field(default=None)
    site_id: UUID | None = Field(default=None)
    data: dict[str, Any] = Field(default_factory=dict)
    attachments: dict[str, Any] | None = Field(default=None)
    status: SheqStatus = Field(
        default=SheqStatus.SUBMITTED,
        description="Both clients POST a complete payload in one shot (mobile "
        "keeps drafts client-side, per the DC/POP precedent) — default to "
        "submitted, which runs full §6 validation. Pass 'draft' explicitly to "
        "save a genuinely partial submission without validation.",
    )


class SheqSubmissionUpdate(SQLModel):
    performed_on: date | None = Field(default=None)
    task_id: UUID | None = Field(default=None)
    site_id: UUID | None = Field(default=None)
    data: dict[str, Any] | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)
    status: SheqStatus | None = Field(
        default=None,
        description="Only draft->submitted is accepted here; signed_off is set "
        "exclusively via POST /{id}/signatures.",
    )


class SheqSignatureCreate(SQLModel):
    """Body for POST /api/v1/sheq-checklists/{id}/signatures (§7.4)."""

    role: str
    roster_index: int | None = Field(default=None)
    method: str
    file_ref: dict[str, Any] | None = Field(default=None, description="Drawn signatures only")
    typed_name: str | None = Field(default=None, description="Typed fallback only")
    captured_at: datetime
    offline_captured: bool = Field(default=False)
    device: dict[str, Any] | None = Field(default=None)


class SheqSubmissionResponse(BaseDB, BaseSheqSubmission):
    status: SheqStatus
    submitted_at: datetime | None
    signed_off_at: datetime | None
    supervisor_user_id: UUID | None
    technician_fullname: str = Field(default="")
    num_attachments: int = Field(default=0, ge=0)
    is_signed_off: bool = Field(default=False)
