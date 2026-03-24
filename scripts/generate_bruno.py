"""
Generates a Bruno API collection for the Seacom App backend.
Run from the repo root:  python scripts/generate_bruno.py
"""

import os, json, textwrap

OUT = os.path.join(os.path.dirname(__file__), "..", "bruno")

# ─── helpers ──────────────────────────────────────────────────────────────────

def write(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content).lstrip())


def bru(folder: str, name: str, method: str, url: str, *,
        body: str | None = None,
        body_type: str = "json",
        params: dict | None = None,
        auth: bool = True,
        seq: int = 1,
        form: bool = False,
        note: str | None = None) -> None:
    """Write a single .bru request file."""

    slug = name.lower().replace(" ", "-").replace("/", "-")
    path = os.path.join(OUT, folder, f"{slug}.bru")

    method_up = method.upper()
    if form:
        body_type_tag = "form-urlencoded"
    elif body_type == "json":
        body_type_tag = "json"
    else:
        body_type_tag = body_type

    body_section = ""
    if form and body:
        body_section = f"\nbody:form-urlencoded {{\n{body}\n}}\n"
    elif body:
        body_section = f"\nbody:json {{\n{body}\n}}\n"

    params_section = ""
    if params:
        lines = "\n".join(
            f"  {'~' if v is None else ''}{k}: {'' if v is None else v}"
            for k, v in params.items()
        )
        params_section = f"\nparams:query {{\n{lines}\n}}\n"

    auth_section = ""
    if auth:
        auth_section = "\nauth:bearer {\n  token: {{token}}\n}\n"

    note_section = ""
    if note:
        note_section = f"\ndocs {{\n  {note}\n}}\n"

    content = f"""\
meta {{
  name: {name}
  type: http
  seq: {seq}
}}

{method_up.lower()} {{
  url: {{{{base_url}}}}{url}
  body: {"none" if not body else body_type_tag}
  auth: {"bearer" if auth else "none"}
}}{params_section}{body_section}{auth_section}{note_section}"""

    write(path, content)
    print(f"  {method_up:6} {url}  ->  {path.replace(OUT,'bruno')}")


# ─── collection root ──────────────────────────────────────────────────────────

write(os.path.join(OUT, "bruno.json"), json.dumps({
    "version": "1",
    "name": "Seacom App API",
    "type": "collection",
    "ignore": ["node_modules", ".git"]
}, indent=2))

# ─── environments ─────────────────────────────────────────────────────────────

write(os.path.join(OUT, "environments", "local.bru"), """\
vars {
  base_url: http://localhost:8000/v1
  token:
  user_id:
  technician_id:
  site_id:
  task_id:
  incident_id:
  report_id:
  incident_report_id:
  notification_id:
  access_request_id:
  client_id:
  schedule_id:
  patrol_id:
  inspection_id:
  routine_check_id:
  routine_issue_id:
  webhook_id:
  setting_key: sla_response_time
}
""")

write(os.path.join(OUT, "environments", "production.bru"), """\
vars {
  base_url: https://your-production-url.com/v1
  token:
  user_id:
  technician_id:
  site_id:
  task_id:
  incident_id:
  report_id:
  incident_report_id:
  notification_id:
  access_request_id:
  client_id:
  schedule_id:
  patrol_id:
  inspection_id:
  routine_check_id:
  routine_issue_id:
  webhook_id:
  setting_key: sla_response_time
}
""")

# ─── 01 auth ──────────────────────────────────────────────────────────────────
F = "01_auth"
write(os.path.join(OUT, F, "login.bru"), """\
meta {
  name: Login
  type: http
  seq: 1
}

post {
  url: {{base_url}}/auth/login
  body: form-urlencoded
  auth: none
}

body:form-urlencoded {
  username: admin@example.com
  password: password123
}

script:post-response {
  if (res.status === 200) {
    bru.setEnvVar("token", res.body.access_token);
  }
}
""")

bru(F, "Get Me", "GET", "/auth/me", seq=2)
bru(F, "Change Password", "POST", "/auth/change-password", seq=3,
    body="""\
  {
    "current_password": "password123",
    "new_password": "newpassword123"
  }""")

# ─── 02 users ─────────────────────────────────────────────────────────────────
F = "02_users"
bru(F, "Create User", "POST", "/users/", seq=1,
    body="""\
  {
    "name": "John",
    "surname": "Doe",
    "email": "john.doe@example.com",
    "password": "password123",
    "role": "noc"
  }""")
bru(F, "List Users", "GET", "/users/", seq=2,
    params={"status": None, "role": None, "offset": "0", "limit": "50"})
bru(F, "Get User", "GET", "/users/{{user_id}}", seq=3)
bru(F, "Update User", "PATCH", "/users/{{user_id}}", seq=4,
    body="""\
  {
    "name": "John",
    "surname": "Doe",
    "email": "john.doe@example.com"
  }""")
bru(F, "Update User Role", "PATCH", "/users/{{user_id}}/role", seq=5,
    body='  {\n    "new_role": "manager"\n  }')
bru(F, "Activate User", "PATCH", "/users/{{user_id}}/status/activate", seq=6)
bru(F, "Deactivate User", "PATCH", "/users/{{user_id}}/status/deactivate", seq=7)
bru(F, "Delete User", "DELETE", "/users/{{user_id}}", seq=8)

# ─── 03 technicians ───────────────────────────────────────────────────────────
F = "03_technicians"
bru(F, "Create Technician", "POST", "/technicians/", seq=1,
    body="""\
  {
    "phone": "0821234567",
    "id_no": "9001015800086",
    "user_id": "{{user_id}}",
    "home_latitude": -26.2041,
    "home_longitude": 28.0473
  }""")
bru(F, "List Technicians", "GET", "/technicians/", seq=2,
    params={"offset": "0", "limit": "50"})
bru(F, "Get My Profile", "GET", "/technicians/me", seq=3)
bru(F, "Get Technician", "GET", "/technicians/{{technician_id}}", seq=4)
bru(F, "Update Technician", "PATCH", "/technicians/{{technician_id}}", seq=5,
    body="""\
  {
    "phone": "0821234567",
    "is_available": true
  }""")
bru(F, "Update Location", "PATCH", "/technicians/{{technician_id}}/location", seq=6,
    body='  {\n    "latitude": -26.2041,\n    "longitude": 28.0473\n  }')
bru(F, "Get Technician Sites", "GET", "/technicians/{{technician_id}}/sites", seq=7)
bru(F, "Update Technician Sites", "PUT", "/technicians/{{technician_id}}/sites", seq=8,
    body='  {\n    "site_ids": ["{{site_id}}"]\n  }')
bru(F, "Nearest Technicians", "GET", "/technicians/dispatch/nearest", seq=9,
    params={"latitude": "-26.2041", "longitude": "28.0473", "limit": "5", "available_only": "true", "max_distance_km": None})
bru(F, "Nearest To Site", "GET", "/technicians/dispatch/nearest-to-site/{{site_id}}", seq=10,
    params={"limit": "5", "available_only": "true"})
bru(F, "In Region", "GET", "/technicians/dispatch/in-region", seq=11,
    params={"latitude": "-26.2041", "longitude": "28.0473", "radius_km": "50", "available_only": "true"})
bru(F, "Stale Locations", "GET", "/technicians/monitoring/stale-locations", seq=12,
    params={"stale_minutes": "30"})
bru(F, "Escalate Technician", "POST", "/technicians/{{technician_id}}/escalate", seq=13,
    params={"reason": "Not responding", "priority": "high"})
bru(F, "Delete Technician", "DELETE", "/technicians/{{technician_id}}", seq=14)

# ─── 04 sites ─────────────────────────────────────────────────────────────────
F = "04_sites"
bru(F, "Create Site", "POST", "/sites/", seq=1,
    body="""\
  {
    "name": "Johannesburg Hub",
    "region": "gauteng",
    "address": "123 Main Street, Johannesburg",
    "latitude": -26.2041,
    "longitude": 28.0473,
    "geofence_radius": 200
  }""")
bru(F, "List Sites", "GET", "/sites/", seq=2,
    params={"region": None, "offset": "0", "limit": "100"})
bru(F, "Get Site", "GET", "/sites/{{site_id}}", seq=3)
bru(F, "Update Site", "PATCH", "/sites/{{site_id}}", seq=4,
    body="""\
  {
    "name": "Updated Site Name",
    "address": "456 New Street"
  }""")
bru(F, "Delete Site", "DELETE", "/sites/{{site_id}}", seq=5)

# ─── 05 tasks ─────────────────────────────────────────────────────────────────
F = "05_tasks"
bru(F, "Create Task", "POST", "/tasks/", seq=1,
    body="""\
  {
    "description": "Routine repeater inspection at JHB Hub",
    "start_time": "2026-03-25T08:00:00Z",
    "end_time": "2026-03-25T12:00:00Z",
    "task_type": "repeater",
    "report_type": "repeater",
    "site_id": "{{site_id}}",
    "technician_id": "{{technician_id}}"
  }""")
bru(F, "List Tasks", "GET", "/tasks/", seq=2,
    params={"technician_id": None, "task_type": None, "status": None, "offset": "0", "limit": "50"})
bru(F, "Get Task", "GET", "/tasks/{{task_id}}", seq=3)
bru(F, "Update Task", "PATCH", "/tasks/{{task_id}}", seq=4,
    body='  {\n    "description": "Updated description"\n  }')
bru(F, "Start Task", "PATCH", "/tasks/{{task_id}}/start", seq=5)
bru(F, "Complete Task", "PATCH", "/tasks/{{task_id}}/complete", seq=6)
bru(F, "Fail Task", "PATCH", "/tasks/{{task_id}}/fail", seq=7)
bru(F, "Hold Task", "PATCH", "/tasks/{{task_id}}/hold", seq=8,
    body='  {\n    "reason": "Waiting for parts"\n  }')
bru(F, "Resume Task", "PATCH", "/tasks/{{task_id}}/resume", seq=9)
bru(F, "Task Feedback", "POST", "/tasks/{{task_id}}/feedback", seq=10,
    body='  {\n    "feedback": "Work completed successfully."\n  }')
bru(F, "Delete Task", "DELETE", "/tasks/{{task_id}}", seq=11)

# ─── 06 incidents ─────────────────────────────────────────────────────────────
F = "06_incidents"
bru(F, "Create Incident", "POST", "/incidents/", seq=1,
    body="""\
  {
    "description": "Fibre cut on main trunk line",
    "severity": "major",
    "site_id": "{{site_id}}",
    "technician_id": "{{technician_id}}",
    "client_id": "{{client_id}}",
    "ref_no": "INC-2026-001",
    "start_time": "2026-03-24T20:00:00Z"
  }""")
bru(F, "List Incidents", "GET", "/incidents/", seq=2,
    params={"technician_id": None, "status": None, "client_id": None, "offset": "0", "limit": "50"})
bru(F, "Penalty Summary", "GET", "/incidents/penalty-summary", seq=3)
bru(F, "Get Incident", "GET", "/incidents/{{incident_id}}", seq=4)
bru(F, "Update Incident", "PATCH", "/incidents/{{incident_id}}", seq=5,
    body='  {\n    "description": "Updated description",\n    "severity": "critical"\n  }')
bru(F, "Start Incident", "PATCH", "/incidents/{{incident_id}}/start", seq=6)
bru(F, "Resolve Incident", "PATCH", "/incidents/{{incident_id}}/resolve", seq=7)
bru(F, "Mark Responded", "POST", "/incidents/{{incident_id}}/respond", seq=8)
bru(F, "Mark Arrived On Site", "POST", "/incidents/{{incident_id}}/arrive", seq=9)
bru(F, "Mark Temp Restored", "POST", "/incidents/{{incident_id}}/temp-restore", seq=10)
bru(F, "Mark Perm Restored", "POST", "/incidents/{{incident_id}}/perm-restore", seq=11)
bru(F, "List Updates", "GET", "/incidents/{{incident_id}}/updates", seq=12)
bru(F, "Create Update", "POST", "/incidents/{{incident_id}}/updates", seq=13,
    body='  {\n    "message": "Technician has arrived on site and is assessing the damage."\n  }')
bru(F, "Due Status", "GET", "/incidents/{{incident_id}}/updates/due-status", seq=14)
bru(F, "SLA Status", "GET", "/incidents/{{incident_id}}/sla-status", seq=15)
bru(F, "Check SLA All", "POST", "/incidents/check-sla", seq=16)
bru(F, "Help Alert", "POST", "/incidents/help-alert", seq=17,
    body='  {\n    "incident_id": "{{incident_id}}",\n    "message": "Need immediate backup"\n  }')
bru(F, "Delete Incident", "DELETE", "/incidents/{{incident_id}}", seq=18)

# ─── 07 reports ───────────────────────────────────────────────────────────────
F = "07_reports"
bru(F, "Create Report", "POST", "/reports/", seq=1,
    body="""\
  {
    "report_type": "repeater",
    "service_provider": "SAMO Telecoms",
    "technician_id": "{{technician_id}}",
    "task_id": "{{task_id}}",
    "data": {
      "site_condition": "good",
      "battery_voltage": 13.2,
      "signal_strength": -65
    }
  }""")
bru(F, "List Reports", "GET", "/reports/", seq=2,
    params={"report_type": None, "status": None, "technician_id": None, "offset": "0", "limit": "50"})
bru(F, "Get Report", "GET", "/reports/{{report_id}}", seq=3)
bru(F, "Update Report", "PATCH", "/reports/{{report_id}}", seq=4,
    body='  {\n    "data": {"site_condition": "fair"}\n  }')
bru(F, "Start Report", "PATCH", "/reports/{{report_id}}/start", seq=5)
bru(F, "Complete Report", "PATCH", "/reports/{{report_id}}/complete", seq=6)
bru(F, "Export Report PDF", "GET", "/reports/{{report_id}}/export/pdf", seq=7)
bru(F, "Delete Report", "DELETE", "/reports/{{report_id}}", seq=8)

# ─── 08 incident-reports ──────────────────────────────────────────────────────
F = "08_incident-reports"
bru(F, "Create Incident Report", "POST", "/incident-reports/", seq=1,
    body="""\
  {
    "incident_id": "{{incident_id}}",
    "site_name": "Johannesburg Hub",
    "technician_name": "John Doe",
    "introduction": "This report documents the fibre cut incident on 24 March 2026.",
    "problem_statement": "Main trunk fibre was cut at the 12km mark.",
    "findings": "Physical damage caused by construction works in the area.",
    "actions_taken": "Spliced and restored the fibre cable.",
    "root_cause_analysis": "Excavation by a third-party contractor without prior notification.",
    "conclusion": "Service restored. Recommend physical markers at cable route."
  }""")
bru(F, "List Incident Reports", "GET", "/incident-reports/", seq=2,
    params={"incident_id": None, "technician_id": None, "offset": "0", "limit": "50"})
bru(F, "Get Incident Report", "GET", "/incident-reports/{{incident_report_id}}", seq=3)
bru(F, "Get By Incident", "GET", "/incident-reports/{{incident_id}}/by-incident", seq=4)
bru(F, "Update Incident Report", "PATCH", "/incident-reports/{{incident_report_id}}", seq=5,
    body='  {\n    "conclusion": "Updated conclusion text."\n  }')
bru(F, "Export Incident Report PDF", "POST", "/incident-reports/{{incident_report_id}}/export-pdf", seq=6)
bru(F, "Delete Incident Report", "DELETE", "/incident-reports/{{incident_report_id}}", seq=7)

# ─── 09 notifications ─────────────────────────────────────────────────────────
F = "09_notifications"
bru(F, "Create Notification", "POST", "/notifications/", seq=1,
    body="""\
  {
    "user_id": "{{user_id}}",
    "title": "New Incident Assigned",
    "message": "A major incident has been assigned to your team.",
    "priority": "high"
  }""")
bru(F, "List Notifications", "GET", "/notifications/", seq=2,
    params={"priority": None, "user_id": None, "read": None, "offset": "0", "limit": "50"})
bru(F, "Unread Count", "GET", "/notifications/unread-count", seq=3,
    params={"user_id": None})
bru(F, "Get Notification", "GET", "/notifications/{{notification_id}}", seq=4)
bru(F, "Mark Read", "PATCH", "/notifications/{{notification_id}}/read", seq=5)
bru(F, "Mark All Read", "PATCH", "/notifications/read-all", seq=6,
    params={"user_id": None})
bru(F, "Delete Notification", "DELETE", "/notifications/{{notification_id}}", seq=7)

# ─── 10 access-requests ───────────────────────────────────────────────────────
F = "10_access-requests"
bru(F, "Create Access Request", "POST", "/access-requests/", seq=1,
    body="""\
  {
    "technician_id": "{{technician_id}}",
    "site_id": "{{site_id}}",
    "description": "Routine repeater inspection",
    "start_time": "2026-03-25T08:00:00Z",
    "end_time": "2026-03-25T12:00:00Z",
    "report_type": "repeater"
  }""")
bru(F, "List Access Requests", "GET", "/access-requests/", seq=2,
    params={"status": None, "technician_id": None, "offset": "0", "limit": "50"})
bru(F, "Get Access Request", "GET", "/access-requests/{{access_request_id}}", seq=3)
bru(F, "Update Access Request", "PATCH", "/access-requests/{{access_request_id}}", seq=4,
    body='  {\n    "description": "Updated description"\n  }')
bru(F, "Approve Access Request", "PATCH", "/access-requests/{{access_request_id}}/approve", seq=5,
    body='  "SEACOM-2026-001"')
bru(F, "Reject Access Request", "PATCH", "/access-requests/{{access_request_id}}/reject", seq=6)
bru(F, "Delete Access Request", "DELETE", "/access-requests/{{access_request_id}}", seq=7)

# ─── 11 clients ───────────────────────────────────────────────────────────────
F = "11_clients"
bru(F, "Create Client", "POST", "/clients/", seq=1,
    body='  {\n    "name": "SEACOM",\n    "is_active": true\n  }')
bru(F, "List Clients", "GET", "/clients/", seq=2,
    params={"active_only": "true", "offset": "0", "limit": "50"})
bru(F, "Search Inactive", "GET", "/clients/search/inactive", seq=3,
    params={"name": "SEACOM"})
bru(F, "Get Client", "GET", "/clients/{{client_id}}", seq=4)
bru(F, "Update Client", "PATCH", "/clients/{{client_id}}", seq=5,
    body='  {\n    "name": "SEACOM Updated",\n    "is_active": true\n  }')
bru(F, "Reactivate Client", "POST", "/clients/{{client_id}}/reactivate", seq=6)
bru(F, "Delete Client", "DELETE", "/clients/{{client_id}}", seq=7)

# ─── 12 files ─────────────────────────────────────────────────────────────────
F = "12_files"
write(os.path.join(OUT, F, "upload-file.bru"), """\
meta {
  name: Upload File
  type: http
  seq: 1
}

post {
  url: {{base_url}}/files/upload
  body: multipart-form
  auth: bearer
}

body:multipart-form {
  file: @file(/path/to/your/file.jpg)
}

params:query {
  ~folder: incident-photos
}

auth:bearer {
  token: {{token}}
}
""")
bru(F, "Get Signed URL", "GET", "/files/signed-url/{{file_path}}", seq=2,
    params={"expires_in": "3600"})
bru(F, "Delete File", "DELETE", "/files/{{file_path}}", seq=3)

# ─── 13 routine-checks ────────────────────────────────────────────────────────
F = "13_routine-checks"
bru(F, "Create Routine Check", "POST", "/routine-checks/", seq=1,
    body="""\
  {
    "report_id": "{{report_id}}",
    "cables_intact": true,
    "power_supply_ok": true,
    "signal_levels_ok": false,
    "notes": "Signal levels below threshold at port 3"
  }""")
bru(F, "List Routine Checks", "GET", "/routine-checks/", seq=2,
    params={"status": None, "offset": "0", "limit": "50"})
bru(F, "Get Routine Check", "GET", "/routine-checks/{{routine_check_id}}", seq=3)
bru(F, "Update Routine Check", "PATCH", "/routine-checks/{{routine_check_id}}", seq=4,
    body='  {\n    "notes": "Updated notes"\n  }')
bru(F, "Delete Routine Check", "DELETE", "/routine-checks/{{routine_check_id}}", seq=5)

# ─── 14 routine-issues ────────────────────────────────────────────────────────
F = "14_routine-issues"
bru(F, "Create Routine Issue", "POST", "/routine-issues/", seq=1,
    body="""\
  {
    "report_id": "{{report_id}}",
    "description": "Corroded connector on port 2",
    "severity": "minor",
    "region": "gauteng"
  }""")
bru(F, "List Routine Issues", "GET", "/routine-issues/", seq=2,
    params={"region": None, "offset": "0", "limit": "50"})
bru(F, "Get Routine Issue", "GET", "/routine-issues/{{routine_issue_id}}", seq=3)
bru(F, "Update Routine Issue", "PATCH", "/routine-issues/{{routine_issue_id}}", seq=4,
    body='  {\n    "description": "Updated issue description"\n  }')
bru(F, "Delete Routine Issue", "DELETE", "/routine-issues/{{routine_issue_id}}", seq=5)

# ─── 15 routine-inspections ───────────────────────────────────────────────────
F = "15_routine-inspections"
bru(F, "Create Routine Inspection", "POST", "/routine-inspections/", seq=1,
    body="""\
  {
    "technician_id": "{{technician_id}}",
    "site_id": "{{site_id}}",
    "task_id": "{{task_id}}",
    "notes": "Quarterly site inspection"
  }""")
bru(F, "List Routine Inspections", "GET", "/routine-inspections/", seq=2,
    params={"status": None, "technician_id": None, "site_id": None, "offset": "0", "limit": "50"})
bru(F, "Get Routine Inspection", "GET", "/routine-inspections/{{inspection_id}}", seq=3)
bru(F, "Update Routine Inspection", "PATCH", "/routine-inspections/{{inspection_id}}", seq=4,
    body='  {\n    "notes": "Updated notes"\n  }')
bru(F, "Submit Inspection", "PATCH", "/routine-inspections/{{inspection_id}}/submit", seq=5)
bru(F, "Delete Routine Inspection", "DELETE", "/routine-inspections/{{inspection_id}}", seq=6)

# ─── 16 route-patrols ─────────────────────────────────────────────────────────
F = "16_route-patrols"
bru(F, "Create Route Patrol", "POST", "/route-patrols/", seq=1,
    body="""\
  {
    "technician_id": "{{technician_id}}",
    "site_id": "{{site_id}}",
    "route_km": 12.5,
    "observations": "No visible damage along the route. Vegetation encroachment at km 4.",
    "patrol_date": "2026-03-24"
  }""")
bru(F, "List Route Patrols", "GET", "/route-patrols/", seq=2,
    params={"technician_id": None, "site_id": None, "offset": "0", "limit": "50"})
bru(F, "Get Route Patrol", "GET", "/route-patrols/{{patrol_id}}", seq=3)
bru(F, "Update Route Patrol", "PATCH", "/route-patrols/{{patrol_id}}", seq=4,
    body='  {\n    "observations": "Updated observations"\n  }')
bru(F, "Delete Route Patrol", "DELETE", "/route-patrols/{{patrol_id}}", seq=5)

# ─── 17 maintenance-schedules ─────────────────────────────────────────────────
F = "17_maintenance-schedules"
bru(F, "Create Schedule", "POST", "/maintenance-schedules/", seq=1,
    body="""\
  {
    "site_id": "{{site_id}}",
    "schedule_type": "repeater_site_visit",
    "frequency": "monthly",
    "assigned_technician_id": "{{technician_id}}",
    "next_due_at": "2026-04-01T08:00:00Z",
    "notes": "Monthly repeater inspection"
  }""")
bru(F, "List Schedules", "GET", "/maintenance-schedules/", seq=2,
    params={"site_id": None, "technician_id": None})
bru(F, "List Due Schedules", "GET", "/maintenance-schedules/due", seq=3,
    params={"technician_id": None})
bru(F, "Get Schedule", "GET", "/maintenance-schedules/{{schedule_id}}", seq=4)
bru(F, "Update Schedule", "PATCH", "/maintenance-schedules/{{schedule_id}}", seq=5,
    body='  {\n    "notes": "Updated notes",\n    "is_active": true\n  }')
bru(F, "Mark Done", "PATCH", "/maintenance-schedules/{{schedule_id}}/mark-done", seq=6)
bru(F, "Check Weekly", "POST", "/maintenance-schedules/check-weekly", seq=7)
bru(F, "Delete Schedule", "DELETE", "/maintenance-schedules/{{schedule_id}}", seq=8)

# ─── 18 sessions ──────────────────────────────────────────────────────────────
F = "18_sessions"
bru(F, "Heartbeat", "POST", "/sessions/heartbeat", seq=1)
bru(F, "Logout", "POST", "/sessions/logout", seq=2)

# ─── 19 system-settings ───────────────────────────────────────────────────────
F = "19_system-settings"
bru(F, "Get All Settings", "GET", "/settings/", seq=1)
bru(F, "List Settings", "GET", "/settings/list", seq=2,
    params={"category": None})
bru(F, "Get Setting", "GET", "/settings/{{setting_key}}", seq=3)
bru(F, "Update Setting", "PATCH", "/settings/{{setting_key}}", seq=4,
    body='  {\n    "value": 3600\n  }')
bru(F, "Bulk Update Settings", "PATCH", "/settings/", seq=5,
    body="""\
  {
    "settings": {
      "sla_response_time": 3600,
      "sla_restoration_time": 14400
    }
  }""")
bru(F, "Get Debug Config", "GET", "/settings/debug", seq=6)
bru(F, "Get Debug Status", "GET", "/settings/debug/status", seq=7)
bru(F, "Toggle Debug", "POST", "/settings/debug/toggle", seq=8)
bru(F, "Test Email", "POST", "/settings/email/test", seq=9)

# ─── 20 dashboard ─────────────────────────────────────────────────────────────
F = "20_dashboard"
bru(F, "Executive SLA Overview", "GET", "/dashboard/executive-sla-overview", seq=1)
bru(F, "Incident SLA Monitoring", "GET", "/dashboard/incident-sla-monitoring", seq=2,
    params={"severity": None, "region": None, "status": None, "offset": "0", "limit": "50"})
bru(F, "Incident SLA Detail", "GET", "/dashboard/incident-sla-monitoring/{{incident_id}}", seq=3)
bru(F, "NOC Online", "GET", "/dashboard/noc-online", seq=4,
    params={"cutoff_minutes": "15"})
bru(F, "Task Performance", "GET", "/dashboard/task-performance", seq=5,
    params={"task_type": None, "region": None, "status": None, "offset": "0", "limit": "50"})
bru(F, "Task Performance Detail", "GET", "/dashboard/task-performance/{{task_id}}", seq=6)
bru(F, "Site Risk Reliability", "GET", "/dashboard/site-risk-reliability", seq=7,
    params={"region": None, "risk_level": None, "offset": "0", "limit": "50"})
bru(F, "Site Risk Detail", "GET", "/dashboard/site-risk-reliability/{{site_id}}", seq=8)
bru(F, "Technician Performance", "GET", "/dashboard/technician-performance", seq=9,
    params={"workload_level": None, "performance_level": None, "offset": "0", "limit": "50"})
bru(F, "Technician Performance Detail", "GET", "/dashboard/technician-performance/{{technician_id}}", seq=10)
bru(F, "Access Request SLA", "GET", "/dashboard/access-request-sla", seq=11,
    params={"region": None, "status": None, "offset": "0", "limit": "50"})
bru(F, "Regional SLA Analytics", "GET", "/dashboard/regional-sla-analytics", seq=12)
bru(F, "SLA Trend Analysis", "GET", "/dashboard/sla-trend-analysis", seq=13,
    params={"metric_type": None, "offset": "0", "limit": "50"})
bru(F, "SLA Alerts", "GET", "/dashboard/sla-alerts", seq=14,
    params={"alert_level": None, "item_type": None, "offset": "0", "limit": "50"})
bru(F, "System Alerts", "GET", "/dashboard/system-alerts", seq=15)
bru(F, "Login Audit", "GET", "/dashboard/login-audit", seq=16,
    params={"success": None, "email": None, "offset": "0", "limit": "50"})
bru(F, "NOC Performance", "GET", "/dashboard/noc-performance", seq=17,
    params={"days": "30"})
bru(F, "Dashboard Health", "GET", "/dashboard/health", seq=18)

# ─── 21 webhooks ──────────────────────────────────────────────────────────────
F = "21_webhooks"
bru(F, "Create Webhook", "POST", "/webhooks/", seq=1,
    body="""\
  {
    "url": "https://your-webhook-endpoint.com/hook",
    "event_type": "incident.created",
    "secret": "your-webhook-secret"
  }""")
bru(F, "List Webhooks", "GET", "/webhooks/", seq=2,
    params={"event_type": None})
bru(F, "Delete Webhook", "DELETE", "/webhooks/{{webhook_id}}", seq=3)

print(f"\nDone. Bruno collection written to: {OUT}")
print("  Open Bruno app → Open Collection → select the 'bruno/' folder")
print("  Or run:  cd bruno && bru run --env local")
