# Frontend Changes — Dynamic Template Categories + API v2

Backend changes for the move from hardcoded report types to dynamic template
categories. This is the list of things the **frontend** needs to adapt to.

Branch: `sheq-intergration`

---

## TL;DR

1. New **API v2** namespace (`/api/v2`). Form template + submission endpoints
   are now canonical under v2. The old v1 ones still work but are **deprecated**.
2. New resource: **Template Categories** (`/api/v2/template-categories`) — the
   dynamic "type ID" for a template (replaces a hardcoded enum).
3. Creating a form template now **requires `category_id`**.
4. Creating a submission may now **require a domain link** (`task_id` or
   `incident_id`) depending on the template's category.
5. New user role **`SHEQ`** exists (access rules TBD).

---

## 1. API versioning

| Resource | New canonical (v2) | Old (v1) status |
|---|---|---|
| Template categories | `/api/v2/template-categories` | n/a (new) |
| Form templates | `/api/v2/form-templates` | `/api/v1/form-templates` — **deprecated**, still works |
| Form submissions | `/api/v2/form-templates/{template_id}/submissions` | `/api/v1/...` — **deprecated**, still works |

- Auth unchanged: same bearer token, all endpoints require an authenticated user.
- File upload is unchanged and still lives at `POST /api/v1/files/upload`.
- **Action:** point the template/submission screens at the v2 paths. The v1
  paths keep working during migration but are marked deprecated in the OpenAPI
  docs and will be removed later.

---

## 2. Template Categories (new resource)

The dynamic "type ID" for a template. Admin/management-managed.

### Object shape (`TemplateCategoryResponse`)
```jsonc
{
  "id": "uuid",
  "code": "TASK",            // immutable machine code, unique
  "name": "Task",            // display name
  "description": "string|null",
  "requires_link": "none",   // one of: "none" | "task" | "incident"
  "is_system": true,         // built-in; cannot be deleted
  "is_active": true,
  "created_at": "ts", "updated_at": "ts", "deleted_at": "ts|null"
}
```

> NOTE on `requires_link`: the API returns/accepts the **lowercase** values
> `none` / `task` / `incident` (enum values). They are stored uppercase in the
> DB but you never see that over the wire.

### Endpoints
| Method | Path | Who | Notes |
|---|---|---|---|
| `POST`   | `/api/v2/template-categories`            | management | create custom category |
| `GET`    | `/api/v2/template-categories`            | any auth   | `?active_only=&offset=&limit=` |
| `GET`    | `/api/v2/template-categories/{id}`       | any auth   | |
| `PATCH`  | `/api/v2/template-categories/{id}`       | management | `code` is immutable (not updatable) |
| `DELETE` | `/api/v2/template-categories/{id}`       | management | 403 if `is_system` (built-in) |

### Create payload (`TemplateCategoryCreate`)
```jsonc
{
  "code": "MY_CATEGORY",      // required, unique
  "name": "My Category",      // required
  "description": "string|null",
  "requires_link": "none",    // optional, default "none"
  "is_active": true           // optional, default true
}
```

### Built-in categories (seeded, `is_system: true`, cannot delete)
| code | requires_link |
|---|---|
| `TASK`          | `task` |
| `SHEQ`          | `none` |
| `INCIDENT`      | `incident` |
| `ROUTINE_DRIVE` | `task` |
| `DIESEL`        | `task` |
| `REPEATER`      | `task` |

> These mappings (esp. which categories require a task vs incident link) are the
> backend's current assumption — flag if any should change.

---

## 3. Form Templates — `category_id` now required

`POST /api/v2/form-templates` (and the deprecated v1 equivalent) now require a
`category_id`.

### Create payload change (`FormTemplateCreate`)
```diff
 {
+  "category_id": "uuid",     // REQUIRED — from /template-categories
   "key": "string",
   "name": "string",
   "description": "string|null",
   "is_active": true,
   "structure": { "sections": [ ... ] }   // unchanged
 }
```
- 404 if `category_id` doesn't exist; 409 if the category is inactive.

### Response change (`FormTemplateResponse`)
- Now includes `category_id` (uuid).

### Update (`PATCH`)
- `category_id` is **not** updatable — a template's category is fixed at create.
  (Other fields unchanged.)

- **Action:** add a category picker to the "create template" flow (populate from
  `GET /api/v2/template-categories?active_only=true`).

---

## 4. Form Submissions — conditional domain link

Whether a submission must be attached to a domain object now depends on the
template's category `requires_link`:

| category.requires_link | submission must include |
|---|---|
| `none`     | neither (sending `task_id`/`incident_id` → **400**) |
| `task`     | `task_id` (missing → **400**, not found → **404**) |
| `incident` | `incident_id` (missing → **400**, not found → **404**) |

### Create payload change (`FormSubmissionCreate`)
```diff
 {
   "template_id": "uuid",
+  "task_id": "uuid|null",       // send when category.requires_link == "task"
+  "incident_id": "uuid|null",   // send when category.requires_link == "incident"
   "values": { ... },            // unchanged
   "attachments": { ... }        // unchanged
 }
```

### Response change (`FormSubmissionResponse`)
- Now includes `task_id` and `incident_id` (either may be null).

- **Action:** before submitting, read the template's category (`template.category_id`
  → category `requires_link`) and collect the matching link id. Easiest is to
  resolve the category once when loading the template.

- Validation behaviour is otherwise unchanged: field-level errors still return
  **422** with a per-field error map; attachment mime/size still enforced.

---

## 5. New user role: `SHEQ`

- `SHEQ` is now a valid `UserRole` (alongside admin, manager, technician, noc).
- Access/permission rules for SHEQ are **not yet defined** — TBD.
- **Action:** add `SHEQ` to any role dropdown/label maps. Don't assume any
  specific screen access yet.

---

## Backend artifacts (reference only)

- Migrations applied to remote DB: `0034` (SHEQ role), `0035` (categories table
  + `linktarget` enum + seed), `0036` (`form_templates.category_id`), `0037`
  (`form_submissions.task_id` / `incident_id`).
- New models: `TemplateCategory`. New service + v2 routers.
- Legacy `reports` / `incident_report` / `routine_inspection` / `route_patrol`
  tables/endpoints are **untouched** and still function.
