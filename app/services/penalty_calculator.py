"""
Penalty calculator — computes contractual penalty exposure per Annexure H.

Penalty table (applied when SLA milestone is missed):
  Delay 4–8h  → 10% of quarterly fee
  Delay 8–16h → 15%
  Delay 16–24h→ 20%
  Delay 24h+  → 30%

Contract values:
  Monthly fee:   R900,000
  Quarterly fee: R2,700,000
  Per-fault cap: 10% of quarterly fee per call = R270,000
  Aggregate cap: 20% per quarter = R540,000
  SEACOM termination right: 3+ faults missed in a quarter (Annexure H)
"""

from datetime import datetime
from typing import Any

from app.utils.funcs import utcnow

MONTHLY_FEE_ZAR   = 900_000
QUARTERLY_FEE_ZAR = MONTHLY_FEE_ZAR * 3   # R2,700,000
PER_FAULT_CAP_PCT = 10.0                   # max 10% per call
AGGREGATE_CAP_PCT = 20.0                   # max 20% per quarter
TERMINATION_THRESHOLD = 3                  # SEACOM can terminate after this many breaches/quarter


def calculate_penalty_percentage(delay_hours: float) -> float:
    """Return the penalty percentage based on delay in hours (Annexure H table)."""
    if delay_hours < 4:
        return 0.0
    if delay_hours < 8:
        return 10.0
    if delay_hours < 16:
        return 15.0
    if delay_hours < 24:
        return 20.0
    return 30.0


def get_incident_penalty_exposure(incident: Any) -> dict:
    """
    Calculate penalty exposure for a single incident based on SLA milestone breaches.
    Checks the on-site arrival and temp restore milestones (the two most contractually significant).
    """
    from app.utils.sla_utils import calculate_sla_deadlines

    severity = str(incident.severity) if incident.severity else "minor"
    start    = incident.start_time or incident.created_at
    deadlines = calculate_sla_deadlines(severity, start)
    now = utcnow()

    milestone_results = []
    total_penalty_pct = 0.0

    checks = [
        ("onsite_arrival", deadlines["onsite_deadline"], incident.arrived_on_site_at),
        ("temp_restore",   deadlines["temp_restore_deadline"], incident.temporarily_restored_at),
    ]

    for milestone_name, deadline, actual in checks:
        if deadline is None:
            continue

        actual_time = actual or (now if incident.status != "resolved" else None)
        if actual_time is None:
            continue

        delay_s = (actual_time - deadline).total_seconds()
        delay_h = delay_s / 3600

        if delay_h <= 0:
            milestone_results.append({
                "milestone":       milestone_name,
                "deadline":        deadline.isoformat(),
                "actual_time":     actual.isoformat() if actual else None,
                "delay_hours":     0.0,
                "penalty_pct":     0.0,
                "penalty_rand":    0.0,
                "status":          "met",
            })
            continue

        pct  = min(calculate_penalty_percentage(delay_h), PER_FAULT_CAP_PCT)
        rand = round(QUARTERLY_FEE_ZAR * pct / 100, 2)
        total_penalty_pct += pct

        milestone_results.append({
            "milestone":    milestone_name,
            "deadline":     deadline.isoformat(),
            "actual_time":  actual.isoformat() if actual else now.isoformat(),
            "delay_hours":  round(delay_h, 2),
            "penalty_pct":  pct,
            "penalty_rand": rand,
            "status":       "breached",
        })

    total_penalty_rand = round(QUARTERLY_FEE_ZAR * min(total_penalty_pct, PER_FAULT_CAP_PCT) / 100, 2)

    return {
        "incident_id":        str(incident.id),
        "severity":           severity,
        "milestones":         milestone_results,
        "total_penalty_pct":  round(min(total_penalty_pct, PER_FAULT_CAP_PCT), 2),
        "total_penalty_rand": total_penalty_rand,
    }


def get_quarter_penalty_summary(incidents: list) -> dict:
    """
    Aggregate penalty exposure across all incidents in the current quarter.
    Flags termination risk if 3+ faults have breached SLA.
    """
    if not incidents:
        return {
            "quarterly_fee_rand":    QUARTERLY_FEE_ZAR,
            "total_penalty_rand":    0.0,
            "total_penalty_pct":     0.0,
            "breached_fault_count":  0,
            "termination_risk":      False,
            "incidents":             [],
        }

    results      = [get_incident_penalty_exposure(i) for i in incidents]
    total_rand   = sum(r["total_penalty_rand"] for r in results)
    # Apply quarterly aggregate cap
    capped_rand  = min(total_rand, QUARTERLY_FEE_ZAR * AGGREGATE_CAP_PCT / 100)
    capped_pct   = round(capped_rand / QUARTERLY_FEE_ZAR * 100, 2)
    breached     = sum(1 for r in results if r["total_penalty_pct"] > 0)

    return {
        "quarterly_fee_rand":   QUARTERLY_FEE_ZAR,
        "total_penalty_rand":   round(capped_rand, 2),
        "total_penalty_pct":    capped_pct,
        "breached_fault_count": breached,
        "termination_risk":     breached >= TERMINATION_THRESHOLD,
        "incidents":            results,
    }
