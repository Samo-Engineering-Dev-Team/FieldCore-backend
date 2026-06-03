# Dynamic Form Templates — Implementation

Backend-only. Replaces hardcoded forms with reusable, data-defined **form templates**
that people submit **responses** against. Implemented additively: existing
report/incident/routine endpoints are untouched; today's forms are seeded as templates
so consumers can migrate forward.

## Context

Every form/checklist was hardcoded:

- `ReportType` fixed enum `{diesel, repeater, routine-drive}` + free-form `Report.data` JSONB, rendered by per-type PDF builders in `app/services/pdf.py`.
- `IncidentReport` fixed narrative columns; `RoutineCheck` / `RoutineIssue`; `RoutineInspection` (JSONB); `RoutePatrol`.

Adding/changing a form required a code change. Now a form is **data**: an ordered set of
sections of typed fields. Submissions are validated server-side against the template and
**snapshot** the template structure so historical submissions stay valid after edits.

**Decisions:** additive rollout (deprecate old endpoints in a later phase) · dynamic-form
PDF export out of scope · submission stores a version snapshot (not normalized version rows).

## Data model

`app/models/form_template.py`
- `FormTemplate` table `form_templates`: `key`, `name`, `description`, `version`, `is_active`,
  and `structure` (JSONB) — the whole sections+fields tree as one validated blob (matches the
  repo's JSONB style; lets a client create a full template in one request; makes the submission
  snapshot a direct copy).
- Validated sub-schemas describing `structure`: `FieldOption`, `FieldConstraints`,
  `FieldDefinition` (`key`, `label`, `type`, `order`, `required`, `constraints`, `options`),
  `SectionDefinition`, `TemplateStructure`. Validators enforce: field `key` unique across the
  template, unique section/field `order`, enum fields must declare ≥1 option.
- `FormTemplateCreate` / `FormTemplateUpdate` / `FormTemplateResponse`.

`app/models/form_submission.py`
- `FormSubmission` table `form_submissions`: `template_id` (FK), `template_version`,
  `template_snapshot` (JSONB, frozen structure), `values` (JSONB, coerced), `attachments`
  (JSONB refs keyed by field), `submitted_by`.

`app/utils/enums.py` — `FieldType(StrEnum)`: `string`, `number`, `boolean`, `date`,
`attachment`, `enum`.

**Extensible type system:** `FIELD_TYPE_VALIDATORS` registry maps each `FieldType` to a
coerce/validate function. New type = add an enum value + register a validator. No schema/table churn.

## Validation engine

`app/services/form_validation.py` — pure, DB-free, unit-testable.
`validate_submission(structure, values, attachments)`:
- rejects unknown keys (in values and attachments)
- enforces required fields
- coerces/validates each value via its type validator and per-type constraints
  (string lengths, number min/max, enum options, attachment mime/size against `FileService`
  upload metadata, date ISO parse, boolean coercion)
- collects **all** errors, then raises `FormValidationException` (HTTP 422) with a structured
  `{field_key: [messages]}` map (in `app/exceptions/http.py`, rendered alongside FastAPI's
  `RequestValidationError`).

## Services

`app/services/form_template.py` — `_FormTemplateService`: create/read/list/update/delete,
management-only writes (`authorization.require_management`), structural update bumps `version`,
delete is soft. Factory + `FormTemplateService = Annotated[...]` DI alias.

`app/services/form_submission.py` — `_FormSubmissionService`: `create_submission` loads the active
template, validates, snapshots structure + version, sets `submitted_by`. Reads scope technicians
to their own submissions. Factory + `Annotated` alias. Both exported from `app/services/__init__.py`.

## API

`app/api/v1/form_template.py` — `/api/v1/form-templates`: `POST` (full nested structure in one
body), `GET` list (`active_only`, paging), `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`.

`app/api/v1/form_submission.py` — `/api/v1/form-templates/{template_id}/submissions`: `POST`,
`GET` list, `GET /{submission_id}`.

**Attachments** reuse the existing `POST /api/v1/files/upload` (`FileService`, Supabase): client
uploads first, then submits returned refs in `attachments`; per-field mime/size constraints are
enforced by the validation engine. Both routers registered in `app/api/v1/__init__.py` behind
`Depends(get_current_user)`.

## Migration / seed

`scripts/seed_form_templates.py` — idempotent upsert (by `key`) recreating the six existing forms
as templates: `diesel`, `repeater`, `routine-drive`, `incident-report`,
`routine-generator-inspection`, `route-patrol`. No destructive change to existing tables.
Schema itself auto-creates via `SQLModel.metadata.create_all` in `Database.init()` (no Alembic in
this repo). Deprecation/cutover of the old endpoints is a later phase.

> Limitation: legacy diesel/repeater payloads use deeply nested + repeating JSON. The template
> model is flat sections of typed fields, so seeds capture key fields representatively rather than
> reproducing legacy JSON verbatim. Nested / repeating field groups are a future enhancement.

## Tests

`tests/` (pure unit, no live DB — hand-rolled fake session + `TokenData`, per repo convention):
- `test_form_template_validation.py` — structure validators.
- `test_form_submission_validation.py` — per-type happy path + every rejection case + multi-error collection.
- `test_form_attachment_handling.py` — mime/size/required/unknown attachment enforcement.
- `test_form_template_service.py` — CRUD, version bump, management-only auth.
- `test_form_submission_service.py` — snapshot, validation surfacing, technician scoping.

30 new tests pass. (`uv run pytest tests/test_form_*`)

## Verification

1. `uv run pytest` — new tests green; pre-existing CORS/passkey/redis failures are environmental,
   unrelated to this work.
2. Boot the app → `Database.init()` logs `form_templates` / `form_submissions` created.
3. `uv run python scripts/seed_form_templates.py` → seeds 6 templates; re-run is idempotent.
4. Smoke: `POST /api/v1/form-templates` (2-section template w/ enum + attachment) → 201;
   `POST .../{id}/submissions` valid → 201 (response shows `template_version` + snapshot);
   invalid → 422 with per-field errors; attachment via `/files/upload` then referenced → enforced.

## Out of scope
- Frontend.
- Generic template→PDF rendering for dynamic submissions.
- Removing/replacing the existing report/incident/routine models & endpoints (deprecation + cutover is a follow-up phase).
