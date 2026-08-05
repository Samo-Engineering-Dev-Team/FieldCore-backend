"""
SHEQ compliance reporting (SHEQ-CHECKLISTS-PLAN.md §8.4).

The missed-vehicle-check rule's *definition* is a business rule, not a data
question — the plan flags it explicitly as unresolved with Samo (Phase 0).
This implements the plan's stated default (a technician is expected to submit
a `vehicle-daily` checklist on any day they have a task in a non-pending
status) behind a `system_settings` override, so the rule can change without a
deploy once that answer lands.

Aggregation only, no writes. Reads `SheqSubmission.summary` (denormalised at
write time by `sheq_summary.py`) rather than scanning `data` across every row.
"""

from collections import Counter
from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.models import Site, Task, Technician
from app.models.auth import TokenData
from app.models.sheq_submission import (
    MASTER_SAFETY_SECTION_TITLES,
    MASTER_SAFETY_SECTIONS,
    DAILY_RISK_MATRIX,
    SheqSubmission,
)
from app.services.authorization import require_sheq_read
from app.services.system_settings import get_system_settings_service
from app.utils.enums import SheqChecklistType, SheqStatus, TaskStatus
from app.utils.funcs import utcnow

# Checklist types that require a supervisor countersign (§7.4). "Signature
# completeness" below concerns only this — technician/driver/employee marks
# are already enforced blocking at submit time by sheq_validation.py, so they
# can never be missing on a `submitted` row.
_SIGNOFF_REQUIRED_TYPES = (
    SheqChecklistType.VEHICLE_DAILY,
    SheqChecklistType.DAILY_RISK_ASSESSMENT,
)

_DEFAULT_SIGNOFF_SLA_DAYS = 3

# Verbatim label lookup for "top failing items" — flattens the two nested
# label constants into one {dotted key: label} map.
_ITEM_LABELS: dict[str, str] = {}
for _section_key, _rows in MASTER_SAFETY_SECTIONS.items():
    for _row_id, _label in _rows.items():
        _ITEM_LABELS[f"{_section_key}.{_row_id}"] = _label
for _group_key, _items in DAILY_RISK_MATRIX.items():
    for _item_key, _label in _items.items():
        _ITEM_LABELS[f"{_group_key}.{_item_key}"] = _label


class SubmissionVolumeEntry(BaseModel):
    checklist_type: str
    status: str
    count: int


class MissedVehicleCheck(BaseModel):
    technician_id: UUID
    technician_name: str
    date: date


class OverdueSignoff(BaseModel):
    submission_id: UUID
    checklist_type: str
    technician_name: str
    submitted_at: datetime
    days_overdue: int


class FailingItem(BaseModel):
    key: str
    label: str
    count: int


class SectionNAFrequency(BaseModel):
    section_key: str
    section_title: str
    count: int


class SignatureGap(BaseModel):
    submission_id: UUID
    checklist_type: str
    technician_name: str
    submitted_at: datetime | None


class SheqComplianceResponse(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    submission_volume: list[SubmissionVolumeEntry] = Field(default_factory=list)
    missed_vehicle_checks: list[MissedVehicleCheck] = Field(default_factory=list)
    overdue_signoffs: list[OverdueSignoff] = Field(default_factory=list)
    no_go_count: int = 0
    master_checklist_total: int = 0
    no_go_rate: float = 0.0
    top_failing_items: list[FailingItem] = Field(default_factory=list)
    section_na_frequency: list[SectionNAFrequency] = Field(default_factory=list)
    signature_gaps: list[SignatureGap] = Field(default_factory=list)


# ── Pure calculations (unit-testable without a DB) ───────────────────────────


def _submission_volume(rows: list[dict]) -> list[SubmissionVolumeEntry]:
    counts = Counter((r["checklist_type"], r["status"]) for r in rows)
    return [
        SubmissionVolumeEntry(checklist_type=ctype, status=status, count=count)
        for (ctype, status), count in sorted(counts.items())
    ]


def _no_go_stats(rows: list[dict]) -> tuple[int, int, float]:
    master_rows = [r for r in rows if r["checklist_type"] == SheqChecklistType.TECHNICIAN_MASTER_SAFETY]
    total = len(master_rows)
    no_go = sum(
        1 for r in master_rows if (r.get("summary") or {}).get("overall_decision") == "No-Go"
    )
    rate = (no_go / total) if total else 0.0
    return no_go, total, rate


def _top_failing_items(rows: list[dict], limit: int = 10) -> list[FailingItem]:
    counter: Counter[str] = Counter()
    for r in rows:
        for key in (r.get("summary") or {}).get("failed_item_keys", []):
            counter[key] += 1
    return [
        FailingItem(key=key, label=_ITEM_LABELS.get(key, key), count=count)
        for key, count in counter.most_common(limit)
    ]


def _section_na_frequency(rows: list[dict]) -> list[SectionNAFrequency]:
    counter: Counter[str] = Counter()
    for r in rows:
        if r["checklist_type"] != SheqChecklistType.TECHNICIAN_MASTER_SAFETY:
            continue
        for key in (r.get("summary") or {}).get("sections_na", []):
            counter[key] += 1
    return [
        SectionNAFrequency(
            section_key=key, section_title=MASTER_SAFETY_SECTION_TITLES.get(key, key), count=count
        )
        for key, count in counter.most_common()
    ]


def _overdue_signoffs(rows: list[dict], sla_days: int, now: datetime) -> list[OverdueSignoff]:
    overdue: list[OverdueSignoff] = []
    for r in rows:
        if r["checklist_type"] not in _SIGNOFF_REQUIRED_TYPES:
            continue
        if r["status"] != SheqStatus.SUBMITTED:
            continue
        submitted_at = r.get("submitted_at")
        if not submitted_at:
            continue
        days_overdue = (now - submitted_at).days - sla_days
        if days_overdue > 0:
            overdue.append(
                OverdueSignoff(
                    submission_id=r["id"],
                    checklist_type=r["checklist_type"],
                    technician_name=r.get("technician_name", "Unknown"),
                    submitted_at=submitted_at,
                    days_overdue=days_overdue,
                )
            )
    return sorted(overdue, key=lambda o: o.days_overdue, reverse=True)


def _signature_gaps(rows: list[dict]) -> list[SignatureGap]:
    gaps: list[SignatureGap] = []
    for r in rows:
        if r["checklist_type"] not in _SIGNOFF_REQUIRED_TYPES:
            continue
        if r["status"] != SheqStatus.SUBMITTED:
            continue
        roles = (r.get("summary") or {}).get("signature_roles", [])
        if "supervisor" not in roles:
            gaps.append(
                SignatureGap(
                    submission_id=r["id"],
                    checklist_type=r["checklist_type"],
                    technician_name=r.get("technician_name", "Unknown"),
                    submitted_at=r.get("submitted_at"),
                )
            )
    return gaps


def _missed_vehicle_checks(
    technicians: list[dict],
    tasks: list[dict],
    submitted_dates_by_technician: dict[UUID, set[date]],
    date_from: date,
    date_to: date,
) -> list[MissedVehicleCheck]:
    """
    Default rule (SHEQ-CHECKLISTS-PLAN.md §8.4, unconfirmed with the business):
    a technician is expected to submit a `vehicle-daily` checklist on any day
    within range they have at least one task whose status is not PENDING.
    """
    expected_days_by_technician: dict[UUID, set[date]] = {}
    for t in tasks:
        day = t["start_time"].date()
        if day < date_from or day > date_to:
            continue
        expected_days_by_technician.setdefault(t["technician_id"], set()).add(day)

    technician_names = {t["id"]: t["name"] for t in technicians}

    missed: list[MissedVehicleCheck] = []
    for technician_id, expected_days in expected_days_by_technician.items():
        submitted = submitted_dates_by_technician.get(technician_id, set())
        for day in sorted(expected_days - submitted):
            missed.append(
                MissedVehicleCheck(
                    technician_id=technician_id,
                    technician_name=technician_names.get(technician_id, "Unknown"),
                    date=day,
                )
            )
    return missed


class _SheqComplianceService:
    def get_compliance_report(
        self,
        session: Session,
        current_user: TokenData,
        date_from: date | None = None,
        date_to: date | None = None,
        checklist_type: SheqChecklistType | None = None,
        technician_id: UUID | None = None,
        region: str | None = None,
    ) -> SheqComplianceResponse:
        require_sheq_read(current_user, "You do not have permission to view SHEQ compliance data.")

        statement = select(SheqSubmission).where(SheqSubmission.deleted_at.is_(None))  # type: ignore
        if date_from is not None:
            statement = statement.where(SheqSubmission.performed_on >= date_from)
        if date_to is not None:
            statement = statement.where(SheqSubmission.performed_on <= date_to)
        if checklist_type is not None:
            statement = statement.where(SheqSubmission.checklist_type == checklist_type)
        if technician_id is not None:
            statement = statement.where(SheqSubmission.technician_id == technician_id)
        if region is not None:
            statement = statement.join(Site, Site.id == SheqSubmission.site_id).where(
                Site.region == region
            )

        submissions = session.exec(statement).all()

        rows: list[dict] = []
        technician_name_cache: dict[UUID, str] = {}
        for s in submissions:
            if s.technician_id not in technician_name_cache:
                technician = session.get(Technician, s.technician_id)
                name = "Unknown Technician"
                if technician and technician.user:
                    name = f"{technician.user.name} {technician.user.surname}"
                technician_name_cache[s.technician_id] = name
            rows.append(
                {
                    "id": s.id,
                    "checklist_type": s.checklist_type,
                    "status": s.status,
                    "performed_on": s.performed_on,
                    "technician_id": s.technician_id,
                    "technician_name": technician_name_cache[s.technician_id],
                    "submitted_at": s.submitted_at,
                    "summary": s.summary or {},
                }
            )

        no_go_count, master_total, no_go_rate = _no_go_stats(rows)
        sla_days = get_system_settings_service().get_setting(
            "sheq.signoff_sla_days", session, _DEFAULT_SIGNOFF_SLA_DAYS
        )

        response = SheqComplianceResponse(
            date_from=date_from,
            date_to=date_to,
            submission_volume=_submission_volume(rows),
            overdue_signoffs=_overdue_signoffs(rows, sla_days, utcnow()),
            no_go_count=no_go_count,
            master_checklist_total=master_total,
            no_go_rate=no_go_rate,
            top_failing_items=_top_failing_items(rows),
            section_na_frequency=_section_na_frequency(rows),
            signature_gaps=_signature_gaps(rows),
        )

        if checklist_type is None or checklist_type == SheqChecklistType.VEHICLE_DAILY:
            response.missed_vehicle_checks = self._compute_missed_vehicle_checks(
                session, date_from, date_to, technician_id, rows
            )

        return response

    def _compute_missed_vehicle_checks(
        self,
        session: Session,
        date_from: date | None,
        date_to: date | None,
        technician_id: UUID | None,
        rows: list[dict],
    ) -> list[MissedVehicleCheck]:
        if date_from is None or date_to is None:
            # An open-ended range has no bounded "expected days" set to check
            # against — require an explicit window for this metric.
            return []

        technician_statement = select(Technician).where(Technician.deleted_at.is_(None))  # type: ignore
        if technician_id is not None:
            technician_statement = technician_statement.where(Technician.id == technician_id)
        technicians = session.exec(technician_statement).all()
        technician_dicts = []
        for t in technicians:
            name = "Unknown Technician"
            if t.user:
                name = f"{t.user.name} {t.user.surname}"
            technician_dicts.append({"id": t.id, "name": name})

        task_statement = select(Task).where(
            Task.deleted_at.is_(None),  # type: ignore
            Task.status != TaskStatus.PENDING,
            Task.start_time >= datetime.combine(date_from, datetime.min.time()),
            Task.start_time <= datetime.combine(date_to, datetime.max.time()),
        )
        if technician_id is not None:
            task_statement = task_statement.where(Task.technician_id == technician_id)
        tasks = session.exec(task_statement).all()
        task_dicts = [
            {"technician_id": t.technician_id, "start_time": t.start_time} for t in tasks
        ]

        submitted_dates_by_technician: dict[UUID, set[date]] = {}
        for r in rows:
            if r["checklist_type"] != SheqChecklistType.VEHICLE_DAILY:
                continue
            submitted_dates_by_technician.setdefault(r["technician_id"], set()).add(
                r["performed_on"]
            )

        return _missed_vehicle_checks(
            technician_dicts, task_dicts, submitted_dates_by_technician, date_from, date_to
        )


def get_sheq_compliance_service() -> _SheqComplianceService:
    return _SheqComplianceService()


SheqComplianceService = Annotated[_SheqComplianceService, Depends(get_sheq_compliance_service)]
