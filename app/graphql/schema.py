import strawberry
from typing import List, Optional
from enum import Enum

# Enums
@strawberry.enum
class SLAStatus(Enum):
    WITHIN_SLA = "WITHIN_SLA"
    AT_RISK = "AT_RISK"
    CRITICAL = "CRITICAL"
    BREACHED = "BREACHED"

@strawberry.enum
class AlertLevel(Enum):
    BREACHED = "BREACHED"
    CRITICAL = "CRITICAL"
    AT_RISK = "AT_RISK"

@strawberry.enum
class RiskLevel(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

@strawberry.enum
class PerformanceLevel(Enum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    NEEDS_SUPPORT = "NEEDS_SUPPORT"
    AT_RISK = "AT_RISK"

@strawberry.enum
class WorkloadLevel(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

# Types
@strawberry.type
class ExecutiveSLAOverview:
    total_items: int
    within_sla_count: int
    at_risk_count: int
    critical_count: int
    breached_count: int
    compliance_percentage: float
    at_risk_percentage: float
    last_updated: str

@strawberry.type
class IncidentSLARecord:
    id: str
    seacom_ref: Optional[str]
    description: str
    incident_status: str
    severity: str
    sla_minutes: int
    created_at: str
    start_time: str
    resolved_at: Optional[str]
    sla_deadline: str
    sla_remaining_minutes: int
    sla_percentage_used: float
    sla_status: SLAStatus
    site_id: str
    site_name: str
    region: str
    technician_id: str
    technician_name: str
    technician_email: str

@strawberry.type
class TaskPerformanceRecord:
    id: str
    seacom_ref: Optional[str]
    description: str
    task_type: str
    task_status: str
    sla_minutes: int
    task_category: str
    start_time: str
    scheduled_end_time: str
    completed_at: Optional[str]
    created_at: str
    sla_deadline: str
    sla_remaining_minutes: int
    actual_duration_minutes: Optional[int]
    sla_status: SLAStatus
    site_id: str
    site_name: str
    region: str
    technician_id: str
    technician_name: str
    technician_email: str

@strawberry.type
class SiteRiskReliabilityRecord:
    site_id: str
    site_name: str
    region: str
    incident_count: int
    open_incidents: int
    resolved_incidents: int
    incidents_30_days: int
    incidents_7_days: int
    avg_resolution_time_minutes: Optional[float]
    task_count: int
    pending_tasks: int
    completed_tasks: int
    sla_compliance_percentage: float
    risk_level: RiskLevel
    site_status: str
    last_updated: str

@strawberry.type
class TechnicianPerformanceRecord:
    technician_id: str
    full_name: str
    email: str
    incident_count: int
    task_count: int
    open_incidents: int
    pending_tasks: int
    total_workload: int
    incident_sla_compliance: float
    task_sla_compliance: float
    avg_incident_resolution_minutes: Optional[float]
    avg_task_completion_minutes: Optional[float]
    workload_level: WorkloadLevel
    performance_level: PerformanceLevel
    last_updated: str

@strawberry.type
class NocOperatorStatus:
    technician_id: str
    user_id: str
    fullname: str
    role: str
    is_active: bool
    last_seen: str

@strawberry.type
class AccessRequestSLARecord:
    id: str
    description: str
    request_status: str
    sla_minutes: int
    created_at: str
    start_time: str
    approved_at: Optional[str]
    sla_deadline: str
    sla_remaining_minutes: int
    sla_percentage_used: float
    sla_status: SLAStatus
    requester_id: str
    requester_name: str
    requester_email: str
    approver_id: Optional[str]
    approver_name: Optional[str]

@strawberry.type
class RegionalSLARecord:
    region: str
    total_items: int
    within_sla_count: int
    at_risk_count: int
    critical_count: int
    breached_count: int
    compliance_percentage: float
    incident_count: int
    task_count: int
    access_request_count: int

@strawberry.type
class SLATrendRecord:
    date: str
    region: Optional[str]
    total_items: int
    within_sla_count: int
    at_risk_count: int
    critical_count: int
    breached_count: int
    compliance_percentage: float

@strawberry.type
class SLAAlertRecord:
    id: str
    item_type: str
    item_id: str
    alert_level: AlertLevel
    sla_status: SLAStatus
    description: str
    created_at: str
    site_name: Optional[str]
    technician_name: Optional[str]
    region: Optional[str]

# Input types for filters
@strawberry.input
class IncidentSLAFilters:
    severity: Optional[str] = None
    region: Optional[str] = None
    status: Optional[str] = None
    incident_status: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0

@strawberry.input
class TaskPerformanceFilters:
    task_type: Optional[str] = None
    task_status: Optional[str] = None
    region: Optional[str] = None
    status: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0

@strawberry.input
class SiteRiskFilters:
    region: Optional[str] = None
    risk_level: Optional[RiskLevel] = None
    site_status: Optional[str] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0

@strawberry.input
class TechnicianPerformanceFilters:
    workload_level: Optional[WorkloadLevel] = None
    performance_level: Optional[PerformanceLevel] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0

@strawberry.input
class AccessRequestFilters:
    status: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0

@strawberry.input
class SLATrendFilters:
    region: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    limit: Optional[int] = 100

@strawberry.input
class AlertFilters:
    alert_level: Optional[AlertLevel] = None
    item_type: Optional[str] = None
    region: Optional[str] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0

# Query
@strawberry.type
class Query:
    @strawberry.field
    def executive_sla_overview(self) -> ExecutiveSLAOverview:
        # Import here to avoid circular imports
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_executive_sla_overview()
        return ExecutiveSLAOverview(
            total_items=data["total_items"],
            within_sla_count=data["within_sla_count"],
            at_risk_count=data["at_risk_count"],
            critical_count=data["critical_count"],
            breached_count=data["breached_count"],
            compliance_percentage=data["compliance_percentage"],
            at_risk_percentage=data["at_risk_percentage"],
            last_updated=data["last_updated"]
        )

    @strawberry.field
    def incident_sla_monitoring(self, filters: Optional[IncidentSLAFilters] = None) -> List[IncidentSLARecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_incident_sla_monitoring(filters.__dict__ if filters else {})
        return [
            IncidentSLARecord(
                id=str(row["id"]),
                seacom_ref=row.get("seacom_ref"),
                description=row["description"],
                incident_status=row["incident_status"],
                severity=row["severity"],
                sla_minutes=row["sla_minutes"],
                created_at=row["created_at"],
                start_time=row["start_time"],
                resolved_at=row.get("resolved_at"),
                sla_deadline=row["sla_deadline"],
                sla_remaining_minutes=row["sla_remaining_minutes"],
                sla_percentage_used=row["sla_percentage_used"],
                sla_status=SLAStatus(row["sla_status"]),
                site_id=row["site_id"],
                site_name=row["site_name"],
                region=row["region"],
                technician_id=row["technician_id"],
                technician_name=row["technician_name"],
                technician_email=row["technician_email"]
            ) for row in data["data"]
        ]

    @strawberry.field
    def task_performance(self, filters: Optional[TaskPerformanceFilters] = None) -> List[TaskPerformanceRecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_task_performance(filters.__dict__ if filters else {})
        return [
            TaskPerformanceRecord(
                id=str(row["id"]),
                seacom_ref=row.get("seacom_ref"),
                description=row["description"],
                task_type=row["task_type"],
                task_status=row["task_status"],
                sla_minutes=row["sla_minutes"],
                task_category=row["task_category"],
                start_time=row["start_time"],
                scheduled_end_time=row["scheduled_end_time"],
                completed_at=row.get("completed_at"),
                created_at=row["created_at"],
                sla_deadline=row["sla_deadline"],
                sla_remaining_minutes=row["sla_remaining_minutes"],
                actual_duration_minutes=row.get("actual_duration_minutes"),
                sla_status=SLAStatus(row["sla_status"]),
                site_id=row["site_id"],
                site_name=row["site_name"],
                region=row["region"],
                technician_id=row["technician_id"],
                technician_name=row["technician_name"],
                technician_email=row["technician_email"]
            ) for row in data["data"]
        ]

    @strawberry.field
    def site_risk_reliability(self, filters: Optional[SiteRiskFilters] = None) -> List[SiteRiskReliabilityRecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_site_risk_reliability(filters.__dict__ if filters else {})
        return [
            SiteRiskReliabilityRecord(
                site_id=row["site_id"],
                site_name=row["site_name"],
                region=row["region"],
                incident_count=row["incident_count"],
                open_incidents=row["open_incidents"],
                resolved_incidents=row["resolved_incidents"],
                incidents_30_days=row["incidents_30_days"],
                incidents_7_days=row["incidents_7_days"],
                avg_resolution_time_minutes=row.get("avg_resolution_time_minutes"),
                task_count=row["task_count"],
                pending_tasks=row["pending_tasks"],
                completed_tasks=row["completed_tasks"],
                sla_compliance_percentage=row["sla_compliance_percentage"],
                risk_level=RiskLevel(row["risk_level"]),
                site_status=row["site_status"],
                last_updated=row["last_updated"]
            ) for row in data["data"]
        ]

    @strawberry.field
    def technician_performance(self, filters: Optional[TechnicianPerformanceFilters] = None) -> List[TechnicianPerformanceRecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_technician_performance(filters.__dict__ if filters else {})
        return [
            TechnicianPerformanceRecord(
                technician_id=row["technician_id"],
                full_name=row["full_name"],
                email=row["email"],
                incident_count=row["incident_count"],
                task_count=row["task_count"],
                open_incidents=row["open_incidents"],
                pending_tasks=row["pending_tasks"],
                total_workload=row["total_workload"],
                incident_sla_compliance=row["incident_sla_compliance"],
                task_sla_compliance=row["task_sla_compliance"],
                avg_incident_resolution_minutes=row.get("avg_incident_resolution_minutes"),
                avg_task_completion_minutes=row.get("avg_task_completion_minutes"),
                workload_level=WorkloadLevel(row["workload_level"]),
                performance_level=PerformanceLevel(row["performance_level"]),
                last_updated=row["last_updated"]
            ) for row in data["data"]
        ]

    @strawberry.field
    def noc_online_operators(self, cutoff_minutes: int = 10) -> List[NocOperatorStatus]:
        """List active NOC operators (useful for management panels)."""
        from app.services.presence import PresenceService
        rows = PresenceService.list_active_noc_operators(cutoff_minutes=cutoff_minutes)
        return [NocOperatorStatus(
            technician_id=r.get("user_id") or r.get("technician_id") or "",
            user_id=r.get("user_id"),
            fullname=r.get("fullname"),
            role=r.get("role"),
            is_active=r.get("is_active", False),
            last_seen=r.get("last_seen")
        ) for r in rows]

    @strawberry.field
    def access_request_sla(self, filters: Optional[AccessRequestFilters] = None) -> List[AccessRequestSLARecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_access_request_sla(filters.__dict__ if filters else {})
        return [
            AccessRequestSLARecord(
                id=str(row["id"]),
                description=row["description"],
                request_status=row["request_status"],
                sla_minutes=row["sla_minutes"],
                created_at=row["created_at"],
                start_time=row["start_time"],
                approved_at=row.get("approved_at"),
                sla_deadline=row["sla_deadline"],
                sla_remaining_minutes=row["sla_remaining_minutes"],
                sla_percentage_used=row["sla_percentage_used"],
                sla_status=SLAStatus(row["sla_status"]),
                requester_id=row["requester_id"],
                requester_name=row["requester_name"],
                requester_email=row["requester_email"],
                approver_id=row.get("approver_id"),
                approver_name=row.get("approver_name")
            ) for row in data["data"]
        ]

    @strawberry.field
    def regional_sla_analytics(self) -> List[RegionalSLARecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_regional_sla_analytics()
        return [
            RegionalSLARecord(
                region=row["region"],
                total_items=row["total_items"],
                within_sla_count=row["within_sla_count"],
                at_risk_count=row["at_risk_count"],
                critical_count=row["critical_count"],
                breached_count=row["breached_count"],
                compliance_percentage=row["compliance_percentage"],
                incident_count=row["incident_count"],
                task_count=row["task_count"],
                access_request_count=row["access_request_count"]
            ) for row in data
        ]

    @strawberry.field
    def sla_trend_analysis(self, filters: Optional[SLATrendFilters] = None) -> List[SLATrendRecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_sla_trend_analysis(filters.__dict__ if filters else {})
        return [
            SLATrendRecord(
                date=row["date"],
                region=row.get("region"),
                total_items=row["total_items"],
                within_sla_count=row["within_sla_count"],
                at_risk_count=row["at_risk_count"],
                critical_count=row["critical_count"],
                breached_count=row["breached_count"],
                compliance_percentage=row["compliance_percentage"]
            ) for row in data["data"]
        ]

    @strawberry.field
    def sla_alerts(self, filters: Optional[AlertFilters] = None) -> List[SLAAlertRecord]:
        from app.services.management_dashboard import ManagementDashboardService
        data = ManagementDashboardService.get_sla_alerts(filters.__dict__ if filters else {})
        return [
            SLAAlertRecord(
                id=str(row["id"]),
                item_type=row["item_type"],
                item_id=row["item_id"],
                alert_level=AlertLevel(row["alert_level"]),
                sla_status=SLAStatus(row["sla_status"]),
                description=row["description"],
                created_at=row["created_at"],
                site_name=row.get("site_name"),
                technician_name=row.get("technician_name"),
                region=row.get("region")
            ) for row in data["data"]
        ]

# Schema
schema = strawberry.Schema(query=Query)