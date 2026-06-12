"""
Seed the existing hardcoded forms as dynamic FormTemplate rows.

This is the migration path from the legacy hardcoded forms (diesel / repeater /
routine-drive reports, incident reports, routine generator inspections, route
patrols) to the new template system. Existing tables/endpoints are untouched;
these templates let consumers move forward against the dynamic system.

Idempotent: upserts by template `key`. Safe to re-run.

NOTE: legacy diesel/repeater payloads use deeply nested and repeating JSON
structures. The template model is flat sections of typed fields, so these seeds
capture the key fields representatively rather than reproducing the legacy JSON
shape verbatim (legacy PDF parity is intentionally out of scope). Nested /
repeating field groups are a future enhancement.

Run with:
    uv run python scripts/seed_form_templates.py
"""

import os
import sys

# Add parent directory to path so we can import app modules.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select

from app.core import app_settings
from app.database import Database
from app.models import (
    FormTemplate,
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
    FieldConstraints,
    FieldOption,
)
from app.utils.enums import FieldType, RoutineCheckStatus, RoutineIssueSeverity


IMAGE_MIME_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # mirrors app/api/v1/file.py


def _opts(*values: str) -> list[FieldOption]:
    return [FieldOption(value=v) for v in values]


def _str(key, label, order, *, required=False, max_length=2000) -> FieldDefinition:
    return FieldDefinition(
        key=key, label=label, type=FieldType.STRING, order=order,
        required=required, constraints=FieldConstraints(max_length=max_length),
    )


def _num(key, label, order, *, required=False, minimum=None, maximum=None) -> FieldDefinition:
    return FieldDefinition(
        key=key, label=label, type=FieldType.NUMBER, order=order,
        required=required, constraints=FieldConstraints(min=minimum, max=maximum),
    )


def _date(key, label, order, *, required=False) -> FieldDefinition:
    return FieldDefinition(key=key, label=label, type=FieldType.DATE, order=order, required=required)


def _bool(key, label, order) -> FieldDefinition:
    return FieldDefinition(key=key, label=label, type=FieldType.BOOLEAN, order=order)


def _enum(key, label, order, options, *, required=False) -> FieldDefinition:
    return FieldDefinition(
        key=key, label=label, type=FieldType.ENUM, order=order,
        required=required, options=options,
    )


def _attachment(key, label, order, *, required=False) -> FieldDefinition:
    return FieldDefinition(
        key=key, label=label, type=FieldType.ATTACHMENT, order=order, required=required,
        constraints=FieldConstraints(
            allowed_mime_types=IMAGE_MIME_TYPES, max_size_bytes=MAX_ATTACHMENT_BYTES
        ),
    )


def _templates() -> list[dict]:
    """Return seed definitions: key, name, description, TemplateStructure."""

    diesel = TemplateStructure(sections=[
        SectionDefinition(title="Fill-up Details", order=0, fields=[
            _num("gen_no", "Generator Number", 0, required=True, minimum=1),
            _num("liters_filled", "Litres Filled", 1, required=True, minimum=0),
            _str("fill_reason", "Fill Reason", 2, max_length=200),
            _num("gen_runtime_hours", "Generator Runtime (hours)", 3, minimum=0),
        ]),
        SectionDefinition(title="Evidence", order=1, fields=[
            _attachment("photo", "Site Photo", 0),
        ]),
    ])

    repeater = TemplateStructure(sections=[
        SectionDefinition(title="Routine Information", order=0, fields=[
            _enum("routineType", "Routine Type", 0, _opts("Weekly", "Monthly", "Quarterly"), required=True),
            _date("dateRoutinePerformed", "Date Routine Performed", 1, required=True),
            _str("nocRoutineTicketReference", "NOC Routine Ticket Reference", 2, max_length=200),
        ]),
        SectionDefinition(title="Power Systems", order=1, fields=[
            _enum("upsA_status", "UPS A Status", 0, _opts("Normal", "Fault", "Bypass")),
            _num("upsA_batteryChargeStatus", "UPS A Battery Charge %", 1, minimum=0, maximum=100),
            _num("upsA_loadPercent", "UPS A Load %", 2, minimum=0, maximum=100),
            _str("upsA_runtime", "UPS A Runtime (h:m)", 3, max_length=20),
            _enum("upsB_status", "UPS B Status", 4, _opts("Normal", "Fault", "Bypass")),
            _num("upsB_batteryChargeStatus", "UPS B Battery Charge %", 5, minimum=0, maximum=100),
            _num("upsB_loadPercent", "UPS B Load %", 6, minimum=0, maximum=100),
            _str("upsB_runtime", "UPS B Runtime (h:m)", 7, max_length=20),
            _num("rectA_outputVoltage", "Rectifier A Output Voltage", 8, minimum=0),
            _num("rectA_loadCurrent", "Rectifier A Load Current", 9, minimum=0),
            _num("rectB_outputVoltage", "Rectifier B Output Voltage", 10, minimum=0),
            _num("rectB_loadCurrent", "Rectifier B Load Current", 11, minimum=0),
        ]),
        SectionDefinition(title="Site Pictures", order=2, fields=[
            _attachment("sitePicture", "Site Picture", 0),
        ]),
    ])

    routine_drive = TemplateStructure(sections=[
        SectionDefinition(title="Checklist", order=0, fields=[
            _str("check_item", "Check Item", 0, required=True, max_length=200),
            _enum("status", "Status", 1,
                  _opts(*[s.value for s in RoutineCheckStatus]), required=True),
            _str("comments", "Comments", 2),
        ]),
        SectionDefinition(title="Issues", order=1, fields=[
            _enum("severity", "Issue Severity", 0,
                  _opts(*[s.value for s in RoutineIssueSeverity])),
            _str("issue_comments", "Issue Comments", 1),
        ]),
    ])

    incident_report = TemplateStructure(sections=[
        SectionDefinition(title="Report", order=0, fields=[
            _str("site_name", "Site Name", 0, required=True, max_length=255),
            _str("technician_name", "Technician Name", 1, required=True, max_length=255),
            _date("report_date", "Report Date", 2),
            _str("introduction", "Introduction", 3),
            _str("problem_statement", "Problem Statement", 4),
            _str("findings", "Findings", 5),
            _str("actions_taken", "Actions Taken", 6),
            _str("root_cause_analysis", "Root Cause Analysis", 7),
            _str("conclusion", "Conclusion", 8),
        ]),
        SectionDefinition(title="Attachments", order=1, fields=[
            _attachment("attachment", "Attachment", 0),
        ]),
    ])

    routine_generator_inspection = TemplateStructure(sections=[
        SectionDefinition(title="Inspection", order=0, fields=[
            _date("inspection_date", "Inspection Date", 0, required=True),
            _enum("oil_level", "Oil Level", 1, _opts("Low", "Normal", "High")),
            _enum("fuel_level", "Fuel Level", 2, _opts("Low", "Normal", "Full")),
            _str("observations", "Observations", 3),
        ]),
        SectionDefinition(title="Evidence", order=1, fields=[
            _attachment("photo", "Photo", 0),
        ]),
    ])

    route_patrol = TemplateStructure(sections=[
        SectionDefinition(title="Patrol", order=0, fields=[
            _str("route_segment", "Route Segment", 0, required=True, max_length=200),
            _date("patrol_date", "Patrol Date", 1, required=True),
            _str("weather_conditions", "Weather Conditions", 2, max_length=100),
            _bool("anomalies_found", "Anomalies Found", 3),
            _str("anomaly_details", "Anomaly Details", 4, max_length=4000),
            _bool("seacom_attested", "SEACOM Attested", 5),
        ]),
        SectionDefinition(title="Evidence", order=1, fields=[
            _attachment("photo", "Geo-tagged Photo", 0),
        ]),
    ])

    return [
        {"key": "diesel", "name": "Generator Diesel Refill",
         "description": "Diesel refill report for site generators.", "structure": diesel},
        {"key": "repeater", "name": "Repeater Site Visit",
         "description": "Monthly repeater site inspection report.", "structure": repeater},
        {"key": "routine-drive", "name": "Routine Drive / Fibre Route Patrol",
         "description": "Weekly routine drive checklist and issues log.", "structure": routine_drive},
        {"key": "incident-report", "name": "Incident Report",
         "description": "Narrative incident report.", "structure": incident_report},
        {"key": "routine-generator-inspection", "name": "Routine Generator Inspection",
         "description": "Routine generator inspection.", "structure": routine_generator_inspection},
        {"key": "route-patrol", "name": "Route Patrol",
         "description": "Weekly fibre route surveillance patrol.", "structure": route_patrol},
    ]


def seed_form_templates() -> None:
    with Database.session() as session:
        for spec in _templates():
            existing = session.exec(
                select(FormTemplate).where(
                    FormTemplate.key == spec["key"],
                    FormTemplate.deleted_at.is_(None),  # type: ignore
                )
            ).first()

            structure_dump = spec["structure"].model_dump()
            if existing:
                existing.name = spec["name"]
                existing.description = spec["description"]
                if existing.structure != structure_dump:
                    existing.structure = structure_dump
                    existing.version += 1
                existing.touch()
                session.add(existing)
                print(f"  Updated template: {spec['key']} (v{existing.version})")
            else:
                template = FormTemplate(
                    key=spec["key"],
                    name=spec["name"],
                    description=spec["description"],
                    is_active=True,
                    version=1,
                    structure=structure_dump,
                )
                session.add(template)
                print(f"  Created template: {spec['key']}")

        session.commit()


def main() -> None:
    print("\nSeeding form templates...")
    print(f"   Database: {app_settings.DB_NAME}")
    print(f"   Host: {app_settings.DB_HOST}:{app_settings.DB_PORT}")
    print()

    Database.connect(app_settings.database_url)
    Database.init()  # ensure form_templates table exists
    seed_form_templates()
    print()
    print("Form template seeding complete.")
    Database.disconnect()


if __name__ == "__main__":
    main()
