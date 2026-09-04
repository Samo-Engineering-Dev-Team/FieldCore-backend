from typing import Any

from loguru import logger as LOG
from pydantic import ValidationError
from sqlalchemy import and_
from sqlmodel import Session, select

from app.models import Notification, User
from app.models.report_data import (
    CabinetInspection,
    DieselReportData,
    HOSTED_SITE_SECTIONS,
    HostedSiteRoutineData,
    RectifierReadings,
    RepeaterReportData,
    RoutePatrolReportData,
    SITE_CHECK_KEYS,
    SITE_CHECK_LABELS,
    UpsReadings,
)
from app.utils.enums import ReportType, UserRole
from app.utils.funcs import utcnow

_REPORT_DATA_SCHEMAS = {
    ReportType.REPEATER: RepeaterReportData,
    ReportType.DIESEL: DieselReportData,
    ReportType.ROUTINE_DRIVE: RoutePatrolReportData,
    ReportType.DATACENTER: HostedSiteRoutineData,
    ReportType.POP: HostedSiteRoutineData,
}


def coerce_diesel_number(value: Any) -> float:
    """
    Best-effort float from a diesel numeric field, returning 0.0 rather than raising.

    Field data is dirty: litres and amounts arrive as `22`, `"22.51"`, `"R21.28"`,
    `"R563,30"` (comma decimal), `""`, and `"N/A"`. A history spanning years will
    hit all of them, so a total must never be able to 500 the request.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return 0.0

    cleaned = value.strip().upper().removeprefix("R").strip()
    if not cleaned or cleaned in {"N/A", "NA", "-"}:
        return 0.0
    # "563,30" is a decimal comma; "1,563.30" is a thousands separator.
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def coerce_reading_number(value: Any) -> float | None:
    """
    Best-effort float from a hosted-site power reading, returning None rather
    than raising or silently coercing to 0.

    Readings arrive mixed: `54.2`, `0.7`, `"0.0A"`, `"5.9A"`, `31`, `" "`.
    Unlike `coerce_diesel_number`, a missing/unparseable reading must not
    collapse to 0 — a real 0V reading and a blank cell mean different things
    on a trend chart.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None

    cleaned = value.strip().upper()
    if not cleaned or cleaned in {"N/A", "NA", "-"}:
        return None
    cleaned = cleaned.rstrip("AV%").strip()
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def coerce_diesel_gen_no(value: Any) -> tuple[int, bool]:
    """
    Resolve a fill-up's generator number to 1 or 2.

    Returns `(gen_no, inferred)`. `inferred` is True when the entry carried no
    usable `gen_no` and was defaulted to generator 1 — a site with one generator
    frequently omits the field entirely.
    """
    if isinstance(value, bool):
        return 1, True
    if isinstance(value, (int, float)):
        return (2, False) if int(value) == 2 else (1, int(value) != 1)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits == "2":
            return 2, False
        if digits == "1":
            return 1, False
    return 1, True


def validate_report_data_schema(report_type: ReportType, data: Any) -> None:
    """Warn (never raise) when `data` drifts from the canonical schema for its
    report type (see docs/report-schemas.md). This is the Phase 4 regression
    guard for the mobile/web/backend key mismatches fixed in
    REPORT_PDF_ISSUES.md — a mismatch here means a report will render with
    blank sections or missing fields in the exported PDF, so it should show
    up in logs immediately rather than being discovered by a technician
    reading a broken PDF weeks later.

    Deliberately non-blocking: a schema edge case must never stop a
    technician's field submission from saving.
    """
    schema = _REPORT_DATA_SCHEMAS.get(report_type)
    if schema is None or not isinstance(data, dict):
        return
    try:
        schema.model_validate(data)
    except ValidationError as e:
        LOG.warning(
            "report_data_schema_drift report_type={} errors={}",
            report_type,
            e.errors(include_url=False, include_context=False),
        )


REQUIRED_HOSTED_SITE_SECTION_COUNT = sum(1 for s in HOSTED_SITE_SECTIONS if s.required)


def is_cabinet_complete(cabinet: CabinetInspection) -> bool:
    """Mirrors `isCabinetComplete` in hosted-site-definitions.ts / hosted-site-routine.ts."""
    if not cabinet.location.strip():
        return False
    if cabinet.visual_alarms == "yes" and not (cabinet.alarm_note or "").strip():
        return False
    return cabinet.pdu_photo is not None and cabinet.cabinet_photo is not None


def _is_power_block_complete(block: RectifierReadings | UpsReadings) -> bool:
    if block.status != "yes":
        return True
    readings = block.model_dump(exclude={"status"})
    return all(isinstance(v, str) and v.strip() for v in readings.values())


def is_hosted_site_section_complete(section_key: str, data: HostedSiteRoutineData) -> bool:
    """Mirrors `isSectionComplete` in hosted-site-definitions.ts / hosted-site-routine.ts.

    Kept in lockstep with those two clients per DC_POP_REPORTS_IMPLEMENTATION_PLAN.md
    §4.5 — a divergence here means the three clients disagree on progress for
    the same report.
    """
    if section_key == "details":
        h = data.header
        return bool(
            h.service_provider.strip()
            and h.routine_type.strip()
            and h.site_name.strip()
            and h.technician_name.strip()
            and h.date_routine_performed.strip()
            and h.snoc_routine_ticket.strip()
        )
    if section_key == "site_checks":
        for key in SITE_CHECK_KEYS:
            item = data.site_checks.get(key)
            if item is None or not item.status:
                return False
            bad_when = SITE_CHECK_LABELS[key].bad_when if key in SITE_CHECK_LABELS else "no"
            if item.status == bad_when and not (item.issue or "").strip():
                return False
        return True
    if section_key == "power_readings":
        pr = data.power_readings
        return _is_power_block_complete(pr.rectifier) and _is_power_block_complete(pr.ups)
    if section_key == "cabinets":
        return len(data.cabinets) > 0 and all(is_cabinet_complete(c) for c in data.cabinets)
    if section_key == "extra_sections":
        return all(bool(s.label.strip()) and len(s.photos) > 0 for s in data.extra_sections)
    if section_key == "other_issues":
        return True
    return False


def completed_hosted_site_section_count(data: HostedSiteRoutineData) -> int:
    return sum(
        1
        for s in HOSTED_SITE_SECTIONS
        if s.required and is_hosted_site_section_complete(s.key, data)
    )


def hosted_site_missing_fields(data: HostedSiteRoutineData) -> list[dict[str, Any]]:
    """Mirrors `missingFields` in hosted-site-definitions.ts / hosted-site-routine.ts."""
    out: list[dict[str, Any]] = []

    if not is_hosted_site_section_complete("details", data):
        h = data.header
        required = [
            ("service_provider", h.service_provider),
            ("routine_type", h.routine_type),
            ("site_name", h.site_name),
            ("technician_name", h.technician_name),
            ("date_routine_performed", h.date_routine_performed),
            ("snoc_routine_ticket", h.snoc_routine_ticket),
        ]
        for field, value in required:
            if not value.strip():
                out.append({"sectionKey": "details", "field": field})

    for key in SITE_CHECK_KEYS:
        item = data.site_checks.get(key)
        if item is None or not item.status:
            out.append({"sectionKey": "site_checks", "field": f"{key}:status"})
            continue
        bad_when = SITE_CHECK_LABELS[key].bad_when if key in SITE_CHECK_LABELS else "no"
        if item.status == bad_when and not (item.issue or "").strip():
            out.append({"sectionKey": "site_checks", "field": f"{key}:issue"})

    pr = data.power_readings
    if not _is_power_block_complete(pr.rectifier):
        out.append({"sectionKey": "power_readings", "field": "rectifier:readings"})
    if not _is_power_block_complete(pr.ups):
        out.append({"sectionKey": "power_readings", "field": "ups:readings"})

    if not data.cabinets:
        out.append({"sectionKey": "cabinets", "field": "cabinets:empty"})
    else:
        for c in data.cabinets:
            if not c.location.strip():
                out.append(
                    {"sectionKey": "cabinets", "cabinetOrder": c.order, "field": "location"}
                )
            if c.visual_alarms == "yes" and not (c.alarm_note or "").strip():
                out.append(
                    {"sectionKey": "cabinets", "cabinetOrder": c.order, "field": "alarm_note"}
                )
            if c.pdu_photo is None:
                out.append(
                    {"sectionKey": "cabinets", "cabinetOrder": c.order, "field": "pdu_photo"}
                )
            if c.cabinet_photo is None:
                out.append(
                    {"sectionKey": "cabinets", "cabinetOrder": c.order, "field": "cabinet_photo"}
                )

    for s in data.extra_sections:
        if not s.label.strip() or not s.photos:
            out.append({"sectionKey": "extra_sections", "field": f"extra:{s.order}"})

    return out


def get_noc_user_ids(session: Session) -> list:
    """Return active NOC user ids for shared report notifications."""
    noc_users = session.exec(
        select(User).where(
            and_(
                User.role == UserRole.NOC,
                User.deleted_at.is_(None),
            )
        )
    ).all()
    return [user.id for user in noc_users]


def create_noc_notifications(session: Session, template: Any) -> None:
    """Send notification template to all active NOC users."""
    for user_id in get_noc_user_ids(session):
        session.add(
            Notification(
                user_id=user_id,
                title=template.title,
                message=template.message,
                priority=template.priority,
            )
        )
    session.commit()


def upload_storage_file(
    *,
    file_content: bytes,
    filename: str,
    content_type: str,
    folder: str,
) -> dict[str, Any]:
    """Upload a file via shared storage service."""
    from app.services.file import FileService

    return FileService().upload_file_sync(
        file_content=file_content,
        filename=filename,
        content_type=content_type,
        folder=folder,
    )


def normalize_attachment_item(item: Any) -> dict[str, Any]:
    """Normalize single attachment object to shared frontend-friendly shape."""
    if isinstance(item, str):
        return {
            "url": item,
            "public_url": item,
            "signed_url": None,
            "file_path": None,
            "path": None,
            "original_name": None,
            "content_type": None,
            "size": None,
        }

    if isinstance(item, dict):
        file_path = item.get("file_path") or item.get("path")
        url = item.get("public_url") or item.get("url") or item.get("signed_url")
        if not url and isinstance(file_path, str):
            from app.services.file import FileService

            url = FileService().get_public_url(file_path)

        normalized = {
            "url": url,
            "public_url": item.get("public_url") or url,
            "signed_url": item.get("signed_url"),
            "file_path": file_path,
            "path": file_path,
            "original_name": item.get("original_name")
            or item.get("name")
            or item.get("filename"),
            "content_type": item.get("content_type") or item.get("mime_type"),
            "size": item.get("size"),
        }
        if item.get("uploaded_at"):
            normalized["uploaded_at"] = item.get("uploaded_at")
        if item.get("label"):
            normalized["label"] = item.get("label")
        return normalized

    return {
        "url": None,
        "public_url": None,
        "signed_url": None,
        "file_path": None,
        "path": None,
        "original_name": None,
        "content_type": None,
        "size": None,
    }


def normalize_attachments(attachments: Any) -> dict[str, Any] | None:
    """Normalize attachments into canonical {'files': [...]} shape."""
    if attachments is None:
        return None

    files: list[dict[str, Any]] = []

    if isinstance(attachments, list):
        files = [normalize_attachment_item(item) for item in attachments]
    elif isinstance(attachments, str):
        files = [normalize_attachment_item(attachments)]
    elif isinstance(attachments, dict):
        if isinstance(attachments.get("files"), list):
            files = [normalize_attachment_item(item) for item in attachments["files"]]
        elif any(
            key in attachments
            for key in ("url", "public_url", "file_path", "path", "filename", "name")
        ):
            files = [normalize_attachment_item(attachments)]
        else:
            for key, value in attachments.items():
                normalized = normalize_attachment_item(value)
                normalized["label"] = key
                files.append(normalized)
    else:
        return None

    cleaned_files = [
        entry for entry in files if entry.get("url") or entry.get("file_path")
    ]
    return {"files": cleaned_files}


def build_storage_attachment(
    *,
    upload_result: dict[str, Any],
    original_name: str,
    content_type: str,
    size: int | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Build shared attachment entry from storage upload result."""
    attachment = normalize_attachment_item(
        {
            "file_path": upload_result.get("file_path"),
            "path": upload_result.get("file_path"),
            "public_url": upload_result.get("public_url"),
            "url": upload_result.get("public_url"),
            "signed_url": upload_result.get("signed_url"),
            "original_name": original_name,
            "content_type": content_type,
            "size": size,
            "uploaded_at": utcnow().isoformat(),
            "label": label,
        }
    )
    return attachment


def append_attachment_entry(
    attachments: dict[str, Any] | None,
    bucket: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Append attachment entry into target bucket while preserving other buckets."""
    payload = dict(attachments or {})
    current_bucket = payload.get(bucket)
    items = list(current_bucket) if isinstance(current_bucket, list) else []
    items.append(entry)
    payload[bucket] = items
    return payload


# ── Generator hour-meter writeback ───────────────────────────────────────


def section_hour_meter_reading(data: Any, section: str) -> Any:
    """
    Pull `standbyHourMeterAfterTest` out of one generator section.

    `data` is untyped JSONB, so nothing guarantees the shape. The web and mobile
    forms nest the answers under `questions`, but drafts written by older
    clients put them flat on the section — read both rather than losing a
    reading to a shape difference.
    """
    if not isinstance(data, dict):
        return None
    block = data.get(section)
    if not isinstance(block, dict):
        return None
    questions = block.get("questions")
    if isinstance(questions, dict) and "standbyHourMeterAfterTest" in questions:
        return questions.get("standbyHourMeterAfterTest")
    return block.get("standbyHourMeterAfterTest")


def record_generator_meter_readings(inspection: Any, session: Session) -> None:
    """
    Carry an inspection's hour-meter readings onto the units it was filled in
    against.

    This is what keeps `Generator.current_run_seconds` from going stale: the
    inspection is the only moment someone actually reads the meter. Shared by
    the repeater report (where inspections are really captured) and the routine
    inspection service, so the two cannot drift apart.

    Takes anything carrying `data`, `gen1_generator` and `gen2_generator` —
    both models expose exactly that.

    Two rules, both deliberate:

    * An unparseable reading is skipped, not raised. The submission is the
      technician's work and must not be rejected over a meter value.
    * A reading is only ever carried forward, never backwards, so re-submitting
      an older report cannot rewind a unit's meter.
    """
    from app.utils.funcs import parse_hour_meter

    for section, generator in (
        ("gen1", getattr(inspection, "gen1_generator", None)),
        ("gen2", getattr(inspection, "gen2_generator", None)),
    ):
        if generator is None:
            continue
        seconds = parse_hour_meter(
            section_hour_meter_reading(getattr(inspection, "data", None), section)
        )
        if seconds is None:
            continue
        if (
            generator.current_run_seconds is not None
            and seconds <= generator.current_run_seconds
        ):
            continue
        generator.current_run_seconds = seconds
        generator.touch()
        session.add(generator)


# ── Diesel fill-up reading ───────────────────────────────────────────────
#
# Shared by the per-site history (ReportService) and the per-generator history
# (GeneratorService). Extracted rather than duplicated: the two views must
# agree on what a fill-up is, and `coerce_diesel_gen_no`'s rule — an entry with
# no usable gen_no lands in generator 1 — has to be applied identically or the
# same fill would appear under different units in the two screens.


def diesel_reports_for_site(
    session: Session,
    site_id: Any,
    date_from: Any = None,
    date_to: Any = None,
) -> list[Any]:
    """Completed diesel reports for one site, oldest first.

    Reports reach a site only through their task, so this joins rather than
    filtering on the report. A site accumulates roughly one visit a week, so
    the row count stays small and the JSONB has to cross the wire either way.
    """
    from sqlalchemy.orm import selectinload
    from sqlmodel import select

    from app.models import Report, Task, Technician
    from app.utils.enums import ReportStatus, ReportType

    conditions = [
        Report.report_type == ReportType.DIESEL,
        Report.status == ReportStatus.COMPLETED,
        Report.deleted_at.is_(None),  # type: ignore[union-attr]
        Task.site_id == site_id,
    ]
    if date_from is not None:
        conditions.append(Report.created_at >= date_from)  # type: ignore[arg-type]
    if date_to is not None:
        conditions.append(Report.created_at <= date_to)  # type: ignore[arg-type]

    statement = (
        select(Report)
        .join(Task, Task.id == Report.task_id)  # type: ignore[arg-type]
        .options(
            selectinload(Report.task).selectinload(Task.site),  # type: ignore[arg-type]
            selectinload(Report.technician).selectinload(Technician.user),  # type: ignore[arg-type]
        )
        .where(*conditions)
        .order_by(Report.created_at)  # type: ignore[arg-type]
    )
    return list(session.exec(statement).all())


def flatten_diesel_fillups(reports: list[Any]) -> list[Any]:
    """Flatten each report's `data.diesel_fillups` into DieselHistoryEntry rows.

    One entry per fill-up, carrying the fields that live on the owning report
    (date, technician, ticket ref) alongside the ones inside the array.
    """
    from app.models.report_data import DieselHistoryEntry
    from app.utils.funcs import format_iso_week

    entries: list[Any] = []
    for report in reports:
        data = report.data if isinstance(report.data, dict) else {}
        fillups = data.get("diesel_fillups")
        if not isinstance(fillups, list):
            continue

        technician_name = None
        if report.technician and report.technician.user:
            technician_name = (
                f"{report.technician.user.name} {report.technician.user.surname}"
            )
        seacom_ref = report.seacom_ref or (
            report.task.seacom_ref if report.task else None
        )

        for raw in fillups:
            if not isinstance(raw, dict):
                continue
            gen_no, inferred = coerce_diesel_gen_no(raw.get("gen_no"))
            entries.append(
                DieselHistoryEntry(
                    report_id=str(report.id),
                    fill_date=report.created_at,
                    iso_week=format_iso_week(report.created_at),
                    gen_no=gen_no,
                    gen_no_inferred=inferred,
                    liters_filled=coerce_diesel_number(raw.get("liters_filled")),
                    amount_used=coerce_diesel_number(raw.get("amount_used")),
                    fill_reason=(
                        str(raw["fill_reason"]) if raw.get("fill_reason") else None
                    ),
                    gen_runtime_hours=raw.get("gen_runtime_hours"),
                    technician_name=technician_name,
                    seacom_ref=seacom_ref,
                )
            )
    return entries


def assert_site_history_in_scope(
    site_id: Any, current_user: Any, session: Session
) -> None:
    """
    Narrow technicians to their assigned sites.

    Per-report export already restricts a technician to their own reports.
    History spans every technician's fill-ups, so the equivalent boundary is
    site assignment. Shared by the per-site and per-generator histories so a
    technician cannot reach through one what the other denies them.
    """
    from app.exceptions.http import ForbiddenException, NotFoundException
    from app.models import Technician, TechnicianSite
    from app.utils.enums import UserRole
    from sqlmodel import select

    if current_user.role != UserRole.TECHNICIAN:
        return

    technician = session.exec(
        select(Technician).where(
            Technician.user_id == current_user.user_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
    ).first()
    if not technician:
        raise NotFoundException("technician profile not found for current user")

    assignment = session.exec(
        select(TechnicianSite).where(
            TechnicianSite.technician_id == technician.id,
            TechnicianSite.site_id == site_id,
        )
    ).first()
    if assignment is None:
        raise ForbiddenException(
            "Technicians can only view diesel history for their assigned sites"
        )
