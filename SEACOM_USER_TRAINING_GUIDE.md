# Seacom User Training Guide

## Audience
- NOC Operators
- Technicians
- Managers
- Administrators

## Training Goals
By the end of onboarding, users should be able to:
- Log in and navigate role-specific dashboards.
- Understand task and incident states.
- Execute their role workflow without SLA breaches caused by process gaps.
- Escalate correctly when ownership or deadlines are at risk.

## 1. First Login and Setup
1. Open the Seacom web app URL.
2. Sign in with assigned credentials.
3. Confirm role and profile details.
4. Configure notifications (in-app and email as required).
5. For field staff, enable location features where policy allows.

## 2. Core Concepts
- `Task`: planned or reactive work item assigned to a technician/team.
- `Incident`: outage/event requiring coordinated response and tracking.
- `SLA`: response/resolution time commitments with escalation thresholds.
- `Escalation`: formal handoff to higher authority when risk is high.

## 3. Role-Based Daily Workflow

### NOC Operator
1. Start shift by reviewing active incidents/tasks and SLA warnings.
2. Confirm technician availability and workload balance.
3. Assign/reassign work as priorities change.
4. Escalate early for blocked, unowned, or time-critical items.
5. Close shift with accurate status updates and handover notes.

### Technician
1. Review assigned tasks and priority order.
2. Acknowledge assignments before travel/work start.
3. Update progress status in real time.
4. Capture required evidence (notes, attachments, completion details).
5. Close tasks/incidents only when acceptance criteria are met.

### Manager
1. Review SLA trend and operational workload.
2. Resolve escalations quickly.
3. Reallocate team capacity where bottlenecks appear.
4. Monitor repeated failure patterns and corrective actions.

### Administrator
1. Maintain user roles and access integrity.
2. Monitor webhook/system settings health.
3. Coordinate maintenance windows and platform-level changes.

## 4. Escalation Rules (Operational)
Escalate when any of the following are true:
- No active owner for a critical item.
- Required responder is unavailable and no backup exists.
- SLA warning enters critical window without mitigation.
- Incident scope expands across services or customer impact grows.

Escalation quality standard:
- Include what happened, current status, blockers, and requested decision.

## 5. Data Quality Rules
- Use clear, factual updates.
- Avoid placeholder text in closure notes.
- Keep status transitions accurate (`assigned`, `in progress`, `blocked`, `resolved`, etc.).
- Attach supporting evidence before marking completion.

## 6. Common Mistakes to Avoid
- Late acknowledgment of assignments.
- Closing work without verification/evidence.
- Escalating too late.
- Leaving stale statuses that mislead downstream teams.

## 7. Onboarding Checklist
- Account created with correct role.
- User completed first login and notification setup.
- User executed one practice workflow in sandbox/staging.
- User passed role-specific checklist sign-off.
