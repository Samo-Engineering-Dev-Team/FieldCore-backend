"""
Management Dashboard API Endpoints
Provides access to SLA monitoring views and real-time metrics
"""

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Database
from app.services.auth import ManagerOrAdminUser, NocOrManagerOrAdminUser
from app.models.user import User
import os
import shutil
from datetime import datetime, timezone
from loguru import logger as LOG

try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    psutil = None
    _HAS_PSUTIL = False

router = APIRouter(prefix="/dashboard", tags=["management-dashboard"])


# ============================================================
# Executive SLA Overview
# ============================================================
@router.get("/executive-sla-overview")
def get_executive_sla_overview(
    current_user: ManagerOrAdminUser
) -> dict:
    """Get executive SLA overview metrics - high-level KPIs for C-suite"""
    try:
        with Database.session() as session:
            result = session.execute(
                text("SELECT * FROM v_executive_sla_overview LIMIT 1")
            )
            row = result.fetchone()
            if not row:
                return {
                    "total_items": 0,
                    "within_sla_count": 0,
                    "at_risk_count": 0,
                    "critical_count": 0,
                    "breached_count": 0,
                    "compliance_percentage": 0,
                    "at_risk_percentage": 0,
                    "last_updated": None
                }
            return row._mapping
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Incident SLA Monitoring
# ============================================================
@router.get("/incident-sla-monitoring")
def get_incident_sla_monitoring(
    current_user: ManagerOrAdminUser,
    severity: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500)
) -> dict:
    """Get incident SLA monitoring records with filtering support"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_incident_sla_monitoring WHERE 1=1"
            params = {}

            if severity:
                query += " AND severity = :severity"
                params["severity"] = severity

            if region:
                query += " AND region = :region"
                params["region"] = region

            if status:
                query += " AND sla_status = :status"
                params["status"] = status

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results
            query += f" ORDER BY sla_deadline ASC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/incident-sla-monitoring/{incident_id}")
def get_incident_sla_detail(
    incident_id: str,
    current_user: ManagerOrAdminUser
) -> dict:
    """Get detailed incident SLA record"""
    try:
        with Database.session() as session:
            result = session.execute(
                text("SELECT * FROM v_incident_sla_monitoring WHERE id = :id"),
                {"id": incident_id}
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Incident not found")
            return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Presence: NOC operator online listing (Managers only)
# ============================================================
@router.get("/noc-online")
def get_noc_online(
    current_user: ManagerOrAdminUser,
    cutoff_minutes: int = Query(10, ge=1, le=60)
) -> dict:
    """Return list of active NOC operator sessions (restricted to Manager/Admin)."""
    from app.services.presence import PresenceService
    try:
        data = PresenceService.list_active_noc_operators(cutoff_minutes=cutoff_minutes)
        return {"data": data, "total": len(data)}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Task Performance & Compliance
# ============================================================
@router.get("/task-performance")
def get_task_performance(
    current_user: ManagerOrAdminUser,
    task_type: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500)
) -> dict:
    """Get task performance and compliance records"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_task_performance_compliance WHERE 1=1"
            params = {}

            if task_type:
                query += " AND task_type = :task_type"
                params["task_type"] = task_type

            if region:
                query += " AND region = :region"
                params["region"] = region

            if status:
                query += " AND sla_status = :status"
                params["status"] = status

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results
            query += f" ORDER BY sla_deadline ASC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/task-performance/{task_id}")
def get_task_performance_detail(
    task_id: str,
    current_user: ManagerOrAdminUser
) -> dict:
    """Get detailed task performance record"""
    try:
        with Database.session() as session:
            result = session.execute(
                text("SELECT * FROM v_task_performance_compliance WHERE id = :id"),
                {"id": task_id}
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Task not found")
            return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Site Risk & Reliability
# ============================================================
@router.get("/site-risk-reliability")
def get_site_risk_reliability(
    current_user: ManagerOrAdminUser,
    region: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500)
) -> dict:
    """Get site risk and reliability metrics"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_site_risk_reliability WHERE 1=1"
            params = {}

            if region:
                query += " AND region = :region"
                params["region"] = region

            if risk_level:
                query += " AND risk_level = :risk_level"
                params["risk_level"] = risk_level

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results
            query += f" ORDER BY incident_count DESC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/site-risk-reliability/{site_id}")
def get_site_risk_detail(
    site_id: str,
    current_user: ManagerOrAdminUser
) -> dict:
    """Get detailed site risk and reliability record"""
    try:
        with Database.session() as session:
            result = session.execute(
                text("SELECT * FROM v_site_risk_reliability WHERE site_id = :id"),
                {"id": site_id}
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Site not found")
            return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Technician Performance
# ============================================================
@router.get("/technician-performance")
def get_technician_performance(
    current_user: NocOrManagerOrAdminUser,
    workload_level: Optional[str] = Query(None),
    performance_level: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500)
) -> dict:
    """Get technician performance metrics (aggregate, non-punitive)"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_technician_performance WHERE 1=1"
            params = {}

            if workload_level:
                query += " AND workload_level = :workload_level"
                params["workload_level"] = workload_level

            if performance_level:
                query += " AND performance_level = :performance_level"
                params["performance_level"] = performance_level

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results
            query += f" ORDER BY total_workload DESC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/technician-performance/{technician_id}")
def get_technician_performance_detail(
    technician_id: str,
    current_user: NocOrManagerOrAdminUser
) -> dict:
    """Get detailed technician performance record"""
    try:
        with Database.session() as session:
            result = session.execute(
                text("SELECT * FROM v_technician_performance WHERE technician_id = :id"),
                {"id": technician_id}
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Technician not found")
            return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Access Request SLA Impact
# ============================================================
@router.get("/access-request-sla")
def get_access_request_sla(
    current_user: ManagerOrAdminUser,
    region: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500)
) -> dict:
    """Get access request SLA impact records"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_access_request_sla_impact WHERE 1=1"
            params = {}

            if region:
                query += " AND region = :region"
                params["region"] = region

            if status:
                query += " AND sla_status = :status"
                params["status"] = status

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results
            query += f" ORDER BY sla_deadline ASC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Regional SLA Analytics
# ============================================================
@router.get("/regional-sla-analytics")
def get_regional_sla_analytics(
    current_user: ManagerOrAdminUser
) -> dict:
    """Get regional SLA analytics and performance comparison"""
    try:
        with Database.session() as session:
            result = session.execute(
                text("SELECT * FROM v_regional_sla_analytics ORDER BY overall_sla_compliance DESC NULLS LAST")
            )
            records = [dict(row._mapping) for row in result]
            return {"data": records, "total": len(records)}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# SLA Trend Analysis
# ============================================================
@router.get("/sla-trend-analysis")
def get_sla_trend_analysis(
    current_user: ManagerOrAdminUser,
    metric_type: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(90, ge=1, le=500)
) -> dict:
    """Get historical SLA trend data (90-day window)"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_sla_trend_analysis WHERE 1=1"
            params = {}

            if metric_type:
                query += " AND metric_type = :metric_type"
                params["metric_type"] = metric_type

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results - ordered by date DESC (most recent first)
            query += f" ORDER BY metric_date DESC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# SLA Alerts & Escalation
# ============================================================
@router.get("/sla-alerts")
def get_sla_alerts(
    current_user: ManagerOrAdminUser,
    alert_level: Optional[str] = Query(None),
    item_type: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500)
) -> dict:
    """Get real-time SLA alerts and escalation items"""
    try:
        with Database.session() as session:
            query = "SELECT * FROM v_sla_alerts_escalation WHERE 1=1"
            params = {}

            if alert_level:
                query += " AND alert_level = :alert_level"
                params["alert_level"] = alert_level

            if item_type:
                query += " AND item_type = :item_type"
                params["item_type"] = item_type

            # Get total count
            count_query = f"SELECT COUNT(*) as total FROM ({query}) AS subquery"
            count_result = session.execute(text(count_query), params)
            total = count_result.scalar() or 0

            # Get paginated results - ordered by priority
            query += f" ORDER BY priority_order ASC, sla_deadline ASC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(text(query), params)
            records = [dict(row._mapping) for row in result]

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Management dashboard endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# System Alerts (platform-level, not SLA breach alerts)
# ============================================================
@router.get("/system-alerts")
def get_system_alerts(
    current_user: ManagerOrAdminUser,
) -> dict:
    """
    Generate real-time platform-level alerts:
    - Unusual incident spike
    - Bulk stale technician locations
    - No NOC operators online
    - High SLA breach rate
    - Technician overload
    - Maintenance mode active
    - Repeated failed logins (brute-force indicator)
    """
    alerts = []
    try:
        with Database.session() as session:

            # 1. SLA breach rate
            try:
                sla_row = session.execute(
                    text("SELECT breached_count, total_items, compliance_percentage FROM v_executive_sla_overview LIMIT 1")
                ).fetchone()
                if sla_row:
                    breached = int(sla_row._mapping.get("breached_count") or 0)
                    total = int(sla_row._mapping.get("total_items") or 0)
                    compliance = float(sla_row._mapping.get("compliance_percentage") or 100)
                    if compliance < 80 and total > 0:
                        alerts.append({
                            "id": "sla_breach_high",
                            "severity": "critical",
                            "category": "SLA",
                            "title": "High SLA Breach Rate",
                            "message": f"Overall compliance is at {compliance:.1f}% — {breached} items breached. Immediate attention required.",
                            "action": "Review breached incidents and tasks",
                        })
                    elif compliance < 90 and total > 0:
                        alerts.append({
                            "id": "sla_below_target",
                            "severity": "warning",
                            "category": "SLA",
                            "title": "SLA Below 90% Target",
                            "message": f"Compliance at {compliance:.1f}% is below the contractual 90% target.",
                            "action": "Review at-risk items before they breach",
                        })
            except Exception:
                pass

            # 2. Incident spike — today vs 30-day daily average
            try:
                spike_row = session.execute(text("""
                    SELECT
                        COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) AS today_count,
                        ROUND(COUNT(*) / NULLIF(EXTRACT(DAY FROM (NOW() - MIN(created_at))), 0), 1) AS daily_avg
                    FROM incidents
                    WHERE deleted_at IS NULL
                      AND created_at >= NOW() - INTERVAL '30 days'
                """)).fetchone()
                if spike_row:
                    today = int(spike_row._mapping.get("today_count") or 0)
                    avg = float(spike_row._mapping.get("daily_avg") or 0)
                    if avg > 0 and today > avg * 2.5 and today >= 3:
                        alerts.append({
                            "id": "incident_spike",
                            "severity": "warning",
                            "category": "Operations",
                            "title": "Unusual Incident Spike",
                            "message": f"{today} incidents created today vs daily average of {avg:.1f}. Possible network event or DDoS.",
                            "action": "Investigate root cause — check NOC queue",
                        })
            except Exception:
                pass

            # 3. Bulk stale locations (>40% of technicians)
            try:
                loc_row = session.execute(text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (
                            WHERE last_location_update < NOW() - INTERVAL '24 hours'
                              OR last_location_update IS NULL
                        ) AS stale
                    FROM technicians
                    WHERE deleted_at IS NULL
                """)).fetchone()
                if loc_row:
                    total_tech = int(loc_row._mapping.get("total") or 0)
                    stale_tech = int(loc_row._mapping.get("stale") or 0)
                    if total_tech > 0 and stale_tech / total_tech >= 0.4:
                        alerts.append({
                            "id": "bulk_stale_locations",
                            "severity": "warning",
                            "category": "Tracking",
                            "title": "Bulk Stale GPS Data",
                            "message": f"{stale_tech} of {total_tech} technicians ({round(stale_tech/total_tech*100)}%) have not updated their location in 24h.",
                            "action": "Contact technicians to re-enable location sharing",
                        })
            except Exception:
                pass

            # 4. No NOC operators online in the last 30 minutes
            try:
                from app.services.presence import PresenceService
                noc_online = PresenceService.list_active_noc_operators(cutoff_minutes=30)
                if len(noc_online) == 0:
                    alerts.append({
                        "id": "no_noc_online",
                        "severity": "critical",
                        "category": "Staffing",
                        "title": "No NOC Operators Online",
                        "message": "No NOC operators have been active in the last 30 minutes. Incidents may go unassigned.",
                        "action": "Contact on-call NOC operator immediately",
                    })
            except Exception:
                pass

            # 5. Technician overload (any technician with > 8 active items)
            try:
                overload_row = session.execute(text("""
                    SELECT COUNT(*) AS overloaded
                    FROM (
                        SELECT t.id,
                            COUNT(DISTINCT i.id) FILTER (WHERE LOWER(i.status::text) <> 'resolved') +
                            COUNT(DISTINCT tsk.id) FILTER (WHERE LOWER(tsk.status::text) <> 'completed') AS active
                        FROM technicians t
                        LEFT JOIN incidents i ON i.technician_id = t.id AND i.deleted_at IS NULL
                        LEFT JOIN tasks tsk ON tsk.technician_id = t.id AND tsk.deleted_at IS NULL
                        WHERE t.deleted_at IS NULL
                        GROUP BY t.id
                    ) sub WHERE active > 8
                """)).scalar() or 0
                if int(overload_row) > 0:
                    alerts.append({
                        "id": "technician_overload",
                        "severity": "warning",
                        "category": "Operations",
                        "title": "Technician Overload Detected",
                        "message": f"{int(overload_row)} technician(s) have more than 8 active items. SLA risk is elevated.",
                        "action": "Redistribute workload via Live Tracking → Dispatch",
                    })
            except Exception:
                pass

            # 6. Repeated failed logins in last hour (brute-force indicator)
            try:
                failed_row = session.execute(text("""
                    SELECT
                        email,
                        COUNT(*) AS attempts
                    FROM login_audit
                    WHERE success = false
                      AND created_at >= NOW() - INTERVAL '1 hour'
                    GROUP BY email
                    HAVING COUNT(*) >= 5
                    ORDER BY attempts DESC
                    LIMIT 5
                """)).fetchall()
                if failed_row:
                    targets = ", ".join(r._mapping["email"] for r in failed_row[:3])
                    total_suspicious = len(failed_row)
                    alerts.append({
                        "id": "brute_force_attempts",
                        "severity": "critical",
                        "category": "Security",
                        "title": "Repeated Failed Login Attempts",
                        "message": f"{total_suspicious} account(s) have 5+ failed logins in the last hour: {targets}.",
                        "action": "Review Login Activity — consider disabling affected accounts",
                    })
            except Exception:
                pass

            # 7. Maintenance mode active
            try:
                maint_row = session.execute(text(
                    "SELECT value FROM system_settings WHERE key = 'maintenance_mode' LIMIT 1"
                )).fetchone()
                if maint_row and str(maint_row._mapping.get("value", "")).lower() in ("true", "1", "yes"):
                    alerts.append({
                        "id": "maintenance_mode",
                        "severity": "info",
                        "category": "System",
                        "title": "Maintenance Mode Active",
                        "message": "The system is currently in maintenance mode. Some features may be unavailable to users.",
                        "action": "Disable maintenance mode in Settings when ready",
                    })
            except Exception:
                pass

        severity_order = {"critical": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))
        return {"data": alerts, "total": len(alerts)}

    except Exception as e:
        LOG.exception("System alerts endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Login Audit (admin-only)
# ============================================================
@router.get("/login-audit")
def get_login_audit(
    current_user: ManagerOrAdminUser,
    success: Optional[bool] = Query(None),
    email: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Get login audit records for security monitoring (admin only)."""
    try:
        with Database.session() as session:
            where_clauses = ["1=1"]
            params: dict = {}

            if success is not None:
                where_clauses.append("la.success = :success")
                params["success"] = success

            if email:
                where_clauses.append("la.email ILIKE :email")
                params["email"] = f"%{email}%"

            where_sql = " AND ".join(where_clauses)

            count_result = session.execute(
                text(f"SELECT COUNT(*) FROM login_audit la WHERE {where_sql}"), params
            )
            total = count_result.scalar() or 0

            params["limit"] = limit
            params["offset"] = offset
            result = session.execute(
                text(f"""
                    SELECT
                        la.id,
                        la.email,
                        la.success,
                        la.failure_reason,
                        la.role,
                        la.ip_address,
                        la.user_agent,
                        la.created_at,
                        u.name || ' ' || u.surname AS full_name
                    FROM login_audit la
                    LEFT JOIN users u ON u.id = la.user_id
                    WHERE {where_sql}
                    ORDER BY la.created_at DESC
                    LIMIT :limit OFFSET :offset
                """),
                params,
            )
            records = [dict(row._mapping) for row in result]
            # Convert datetimes to ISO strings for JSON serialisation
            for r in records:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()

            return {"data": records, "total": total}
    except Exception as e:
        LOG.exception("Login audit endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Health Check
# ============================================================
@router.get("/health")
def dashboard_health(
    current_user: ManagerOrAdminUser
) -> dict:
    """Check if dashboard views are healthy and responsive"""
    try:
        with Database.session() as session:
            # Try to query a simple view
            result = session.execute(
                text("SELECT 1 as status")
            )
            result.scalar()
        # Collect system metrics
        root = os.path.abspath(os.sep)
        # Disk
        try:
            du = shutil.disk_usage(root)
            disk_total = du.total
            disk_used = du.used
            disk_percent = round((du.used / du.total) * 100, 1) if du.total > 0 else 0
        except Exception:
            disk_total = disk_used = disk_percent = None

        # Memory and CPU (prefer psutil)
        cpu_percent = None
        mem_total = mem_used = mem_percent = None
        if _HAS_PSUTIL and psutil is not None:
            try:
                cpu_percent = psutil.cpu_percent(interval=0.1)
                vm = psutil.virtual_memory()
                mem_total = vm.total
                mem_used = vm.used
                mem_percent = vm.percent
            except Exception:
                cpu_percent = None
        # Fallbacks: leave as None when not available

        # Presence backend diagnostics for production troubleshooting.
        presence_info = {
            "configured_backend": "unknown",
            "active_backend": "unknown",
            "redis_url_set": False,
            "redis_connected": False,
        }
        try:
            from app.core.settings import app_settings
            from app.services.presence import PresenceService

            configured_backend = app_settings.PRESENCE_BACKEND.lower()
            redis_url_set = bool(app_settings.REDIS_URL)
            redis_connected = bool(PresenceService._use_redis())
            active_backend = "redis" if redis_connected else "db"

            presence_info = {
                "configured_backend": configured_backend,
                "active_backend": active_backend,
                "redis_url_set": redis_url_set,
                "redis_connected": redis_connected,
            }
        except Exception as e:
            LOG.exception("Failed to compute presence health info: {}", e)

        return {
            "status": "healthy",
            "timestamp": Database.get_current_timestamp(),
            "system": {
                "cpu_percent": cpu_percent,
                "memory_total": mem_total,
                "memory_used": mem_used,
                "memory_percent": mem_percent,
                "disk_total": disk_total,
                "disk_used": disk_used,
                "disk_percent": disk_percent,
            },
            "presence": presence_info,
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Dashboard health check failed"
        )


# ============================================================
# NOC Operator Performance Analytics
# ============================================================
@router.get("/noc-performance")
def get_noc_performance(
    current_user: ManagerOrAdminUser,
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """
    Get per-NOC-operator activity metrics:
    incidents assigned, RHS tasks created, and average incident response time.
    """
    try:
        with Database.session() as session:
            result = session.execute(
                text("""
                    SELECT
                        u.id::text AS user_id,
                        u.name || ' ' || u.surname AS noc_name,
                        COUNT(DISTINCT i.id) AS incidents_assigned,
                        COUNT(DISTINCT t.id) AS tasks_created,
                        ROUND(
                            AVG(
                                EXTRACT(EPOCH FROM (i.start_time - i.created_at)) / 60
                            ) FILTER (WHERE i.start_time IS NOT NULL),
                            1
                        ) AS avg_response_mins
                    FROM users u
                    LEFT JOIN incidents i
                        ON i.assigned_by_user_id = u.id
                        AND i.deleted_at IS NULL
                        AND i.created_at >= NOW() - INTERVAL '1 day' * :days
                    LEFT JOIN tasks t
                        ON t.assigned_by_user_id = u.id
                        AND t.deleted_at IS NULL
                        AND t.created_at >= NOW() - INTERVAL '1 day' * :days
                    WHERE u.role = 'NOC'
                      AND u.deleted_at IS NULL
                    GROUP BY u.id, u.name, u.surname
                    ORDER BY incidents_assigned DESC
                """),
                {"days": days},
            )
            records = [dict(row._mapping) for row in result]
            return {"data": records, "total": len(records), "period_days": days}
    except Exception as e:
        LOG.exception("NOC performance endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Technician Workload Analytics
# ============================================================
@router.get("/technician-workload")
def get_technician_workload(
    current_user: ManagerOrAdminUser,
) -> dict:
    """
    Get per-technician workload: active incidents, active tasks, and 30-day totals.
    Useful for identifying overloaded vs under-utilised technicians.
    """
    try:
        with Database.session() as session:
            result = session.execute(
                text("""
                    SELECT
                        t.id::text AS technician_id,
                        u.name || ' ' || u.surname AS technician_name,
                        COUNT(DISTINCT inc.id) FILTER (
                            WHERE LOWER(inc.status::text) <> 'resolved'
                        ) AS active_incidents,
                        COUNT(DISTINCT tsk.id) FILTER (
                            WHERE LOWER(tsk.status::text) <> 'completed'
                        ) AS active_tasks,
                        COUNT(DISTINCT inc.id) FILTER (
                            WHERE inc.created_at >= NOW() - INTERVAL '30 days'
                        ) AS incidents_30d,
                        COUNT(DISTINCT tsk.id) FILTER (
                            WHERE tsk.created_at >= NOW() - INTERVAL '30 days'
                        ) AS tasks_30d
                    FROM technicians t
                    JOIN users u ON u.id = t.user_id AND u.deleted_at IS NULL
                    LEFT JOIN incidents inc
                        ON inc.technician_id = t.id AND inc.deleted_at IS NULL
                    LEFT JOIN tasks tsk
                        ON tsk.technician_id = t.id AND tsk.deleted_at IS NULL
                    WHERE t.deleted_at IS NULL
                    GROUP BY t.id, u.name, u.surname
                    ORDER BY (
                        COUNT(DISTINCT inc.id) FILTER (
                            WHERE LOWER(inc.status::text) <> 'resolved'
                        ) +
                        COUNT(DISTINCT tsk.id) FILTER (
                            WHERE LOWER(tsk.status::text) <> 'completed'
                        )
                    ) DESC
                """),
            )
            records = [dict(row._mapping) for row in result]
            return {"data": records, "total": len(records)}
    except Exception as e:
        LOG.exception("Technician workload endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Incident Time-of-Day & Day-of-Week Analysis
# ============================================================
@router.get("/incident-time-analysis")
def get_incident_time_analysis(
    current_user: ManagerOrAdminUser,
    days: int = Query(90, ge=7, le=365),
) -> dict:
    """
    Get incident distribution by hour of day and day of week.
    Useful for staffing and shift planning decisions.
    """
    try:
        with Database.session() as session:
            hourly_result = session.execute(
                text("""
                    SELECT
                        EXTRACT(HOUR FROM start_time)::int AS hour,
                        COUNT(*) AS count
                    FROM incidents
                    WHERE deleted_at IS NULL
                      AND start_time IS NOT NULL
                      AND start_time >= NOW() - INTERVAL '1 day' * :days
                    GROUP BY hour
                    ORDER BY hour
                """),
                {"days": days},
            )
            hourly = [dict(row._mapping) for row in hourly_result]

            daily_result = session.execute(
                text("""
                    SELECT
                        EXTRACT(DOW FROM start_time)::int AS dow,
                        TO_CHAR(start_time, 'Day') AS day_name,
                        COUNT(*) AS count
                    FROM incidents
                    WHERE deleted_at IS NULL
                      AND start_time IS NOT NULL
                      AND start_time >= NOW() - INTERVAL '1 day' * :days
                    GROUP BY dow, day_name
                    ORDER BY dow
                """),
                {"days": days},
            )
            daily = [dict(row._mapping) for row in daily_result]

            return {"hourly": hourly, "daily": daily, "period_days": days}
    except Exception as e:
        LOG.exception("Incident time analysis endpoint error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================
# Executive Summary PDF Export
# ============================================================
@router.get("/executive-summary-pdf")
def get_executive_summary_pdf(
    current_user: ManagerOrAdminUser,
    month: str = Query(
        default="",
        description="Month in YYYY-MM format. Defaults to current month.",
    ),
) -> StreamingResponse:
    """
    Generate and download an executive management summary PDF with charts.
    Includes KPIs, monthly incident trend, technician workload, and regional SLA compliance.
    """
    try:
        from app.services.pdf import PDFService

        now = datetime.now(timezone.utc)
        if month:
            try:
                parsed = datetime.strptime(month, "%Y-%m")
                month_label = parsed.strftime("%B %Y")
            except ValueError:
                month_label = now.strftime("%B %Y")
        else:
            month_label = now.strftime("%B %Y")

        with Database.session() as session:
            # Overall SLA compliance
            sla_row = session.execute(
                text("SELECT compliance_percentage, total_items FROM v_executive_sla_overview LIMIT 1")
            ).fetchone()
            sla_compliance = float(sla_row._mapping.get("compliance_percentage", 0)) if sla_row else 0.0
            total_items = int(sla_row._mapping.get("total_items", 0)) if sla_row else 0

            # Total incidents and tasks (last 30 days)
            counts = session.execute(
                text("""
                    SELECT
                        (SELECT COUNT(*) FROM incidents
                         WHERE deleted_at IS NULL AND created_at >= NOW() - INTERVAL '30 days') AS total_incidents,
                        (SELECT COUNT(*) FROM tasks
                         WHERE deleted_at IS NULL AND created_at >= NOW() - INTERVAL '30 days') AS total_tasks
                """)
            ).fetchone()
            total_incidents = int(counts._mapping["total_incidents"]) if counts else 0
            total_tasks = int(counts._mapping["total_tasks"]) if counts else 0

            # Monthly incident trend (last 6 months)
            monthly_result = session.execute(
                text("""
                    SELECT
                        TO_CHAR(DATE_TRUNC('month', created_at), 'Mon YY') AS month,
                        COUNT(*) AS count
                    FROM incidents
                    WHERE deleted_at IS NULL
                      AND created_at >= NOW() - INTERVAL '6 months'
                    GROUP BY DATE_TRUNC('month', created_at)
                    ORDER BY DATE_TRUNC('month', created_at)
                """)
            )
            monthly_incidents = [dict(r._mapping) for r in monthly_result]

            # Technician performance (top 8 by total activity, last 30 days)
            tech_result = session.execute(
                text("""
                    SELECT
                        u.name || ' ' || u.surname AS name,
                        COUNT(DISTINCT i.id) AS incidents,
                        COUNT(DISTINCT t.id) AS tasks
                    FROM technicians tech
                    JOIN users u ON u.id = tech.user_id AND u.deleted_at IS NULL
                    LEFT JOIN incidents i ON i.technician_id = tech.id
                        AND i.deleted_at IS NULL AND i.created_at >= NOW() - INTERVAL '30 days'
                    LEFT JOIN tasks t ON t.technician_id = tech.id
                        AND t.deleted_at IS NULL AND t.created_at >= NOW() - INTERVAL '30 days'
                    WHERE tech.deleted_at IS NULL
                    GROUP BY u.name, u.surname
                    ORDER BY (COUNT(DISTINCT i.id) + COUNT(DISTINCT t.id)) DESC
                    LIMIT 8
                """)
            )
            technician_performance = [dict(r._mapping) for r in tech_result]

            # Regional SLA compliance
            regional_result = session.execute(
                text("SELECT region, overall_sla_compliance AS compliance FROM v_regional_sla_analytics ORDER BY overall_sla_compliance DESC NULLS LAST")
            )
            regional_performance = [dict(r._mapping) for r in regional_result]

        pdf_bytes = PDFService().generate_executive_summary_pdf(
            month_label=month_label,
            sla_compliance=sla_compliance,
            total_incidents=total_incidents,
            total_tasks=total_tasks,
            monthly_incidents=monthly_incidents,
            technician_performance=technician_performance,
            regional_performance=regional_performance,
        )

        filename = f"Executive_Summary_{month_label.replace(' ', '_')}.pdf"
        return StreamingResponse(
            pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        LOG.exception("Executive summary PDF error: {}", e)
        raise HTTPException(status_code=500, detail="Internal server error")
