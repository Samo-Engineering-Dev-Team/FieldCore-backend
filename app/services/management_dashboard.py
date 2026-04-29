"""
Management Dashboard Service
Provides access to SLA monitoring views and metrics
"""

from typing import List, Dict, Any, Optional
from sqlalchemy import text
from app.database import Database


class ManagementDashboardService:
    @staticmethod
    def get_executive_sla_overview() -> Dict[str, Any]:
        """Get executive SLA overview metrics"""
        with Database.session() as session:
            result = session.execute(text("SELECT * FROM v_executive_sla_overview LIMIT 1"))
            row = result.fetchone()
            if not row:
                return {
                    "total_items": 0,
                    "within_sla_count": 0,
                    "at_risk_count": 0,
                    "critical_count": 0,
                    "breached_count": 0,
                    "compliance_percentage": 0.0,
                    "at_risk_percentage": 0.0,
                    "last_updated": ""
                }
            return dict(row)

    @staticmethod
    def get_incident_sla_monitoring(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get incident SLA monitoring data"""
        filters = filters or {}
        query = "SELECT * FROM v_incident_sla_monitoring"
        conditions = []
        params = {}

        if filters.get("severity"):
            conditions.append("severity = :severity")
            params["severity"] = filters["severity"]
        if filters.get("region"):
            conditions.append("region = :region")
            params["region"] = filters["region"]
        if filters.get("status"):
            conditions.append("sla_status = :status")
            params["status"] = filters["status"]
        if filters.get("incident_status"):
            conditions.append("incident_status = :incident_status")
            params["incident_status"] = filters["incident_status"]
        if filters.get("from_date"):
            conditions.append("created_at >= :from_date")
            params["from_date"] = filters["from_date"]
        if filters.get("to_date"):
            conditions.append("created_at <= :to_date")
            params["to_date"] = filters["to_date"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        query += f" ORDER BY sla_percentage_used DESC LIMIT {limit} OFFSET {offset}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_incident_sla_monitoring"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }

    @staticmethod
    def get_task_performance(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get task performance data"""
        filters = filters or {}
        query = "SELECT * FROM v_task_performance"
        conditions = []
        params = {}

        if filters.get("task_type"):
            conditions.append("task_type = :task_type")
            params["task_type"] = filters["task_type"]
        if filters.get("task_status"):
            conditions.append("task_status = :task_status")
            params["task_status"] = filters["task_status"]
        if filters.get("region"):
            conditions.append("region = :region")
            params["region"] = filters["region"]
        if filters.get("status"):
            conditions.append("sla_status = :status")
            params["status"] = filters["status"]
        if filters.get("from_date"):
            conditions.append("created_at >= :from_date")
            params["from_date"] = filters["from_date"]
        if filters.get("to_date"):
            conditions.append("created_at <= :to_date")
            params["to_date"] = filters["to_date"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        query += f" ORDER BY sla_percentage_used DESC LIMIT {limit} OFFSET {offset}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_task_performance"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }

    @staticmethod
    def get_site_risk_reliability(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get site risk and reliability data"""
        filters = filters or {}
        query = "SELECT * FROM v_site_risk_reliability"
        conditions = []
        params = {}

        if filters.get("region"):
            conditions.append("region = :region")
            params["region"] = filters["region"]
        if filters.get("risk_level"):
            conditions.append("risk_level = :risk_level")
            params["risk_level"] = filters["risk_level"]
        if filters.get("site_status"):
            conditions.append("site_status = :site_status")
            params["site_status"] = filters["site_status"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        query += f" ORDER BY risk_level DESC, sla_compliance_percentage ASC LIMIT {limit} OFFSET {offset}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_site_risk_reliability"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }

    @staticmethod
    def get_technician_performance(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get technician performance data"""
        filters = filters or {}
        query = "SELECT * FROM v_technician_performance"
        conditions = []
        params = {}

        if filters.get("workload_level"):
            conditions.append("workload_level = :workload_level")
            params["workload_level"] = filters["workload_level"]
        if filters.get("performance_level"):
            conditions.append("performance_level = :performance_level")
            params["performance_level"] = filters["performance_level"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        query += f" ORDER BY workload_level DESC, performance_level ASC LIMIT {limit} OFFSET {offset}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_technician_performance"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }

    @staticmethod
    def get_access_request_sla(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get access request SLA data"""
        filters = filters or {}
        query = "SELECT * FROM v_access_request_sla"
        conditions = []
        params = {}

        if filters.get("status"):
            conditions.append("request_status = :status")
            params["status"] = filters["status"]
        if filters.get("from_date"):
            conditions.append("created_at >= :from_date")
            params["from_date"] = filters["from_date"]
        if filters.get("to_date"):
            conditions.append("created_at <= :to_date")
            params["to_date"] = filters["to_date"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        query += f" ORDER BY sla_percentage_used DESC LIMIT {limit} OFFSET {offset}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_access_request_sla"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }

    @staticmethod
    def get_regional_sla_analytics() -> List[Dict[str, Any]]:
        """Get regional SLA analytics"""
        with Database.session() as session:
            result = session.execute(text("SELECT * FROM v_regional_sla_analytics ORDER BY region"))
            rows = result.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_sla_trend_analysis(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get SLA trend analysis data"""
        filters = filters or {}
        query = "SELECT * FROM v_sla_trend_analysis"
        conditions = []
        params = {}

        if filters.get("region"):
            conditions.append("region = :region")
            params["region"] = filters["region"]
        if filters.get("from_date"):
            conditions.append("date >= :from_date")
            params["from_date"] = filters["from_date"]
        if filters.get("to_date"):
            conditions.append("date <= :to_date")
            params["to_date"] = filters["to_date"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        query += f" ORDER BY date DESC LIMIT {limit}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_sla_trend_analysis"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }

    @staticmethod
    def get_sla_alerts(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get SLA alerts"""
        filters = filters or {}
        query = "SELECT * FROM v_sla_alerts_escalation"
        conditions = []
        params = {}

        if filters.get("alert_level"):
            conditions.append("alert_level = :alert_level")
            params["alert_level"] = filters["alert_level"]
        if filters.get("item_type"):
            conditions.append("item_type = :item_type")
            params["item_type"] = filters["item_type"]
        if filters.get("region"):
            conditions.append("region = :region")
            params["region"] = filters["region"]

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        limit = filters.get("limit", 100)
        offset = filters.get("offset", 0)
        query += f" ORDER BY alert_level DESC, created_at DESC LIMIT {limit} OFFSET {offset}"

        with Database.session() as session:
            result = session.execute(text(query), params)
            rows = result.fetchall()
            total_query = "SELECT COUNT(*) FROM v_sla_alerts_escalation"
            if conditions:
                total_query += " WHERE " + " AND ".join(conditions)
            total_result = session.execute(text(total_query), params)
            total = total_result.scalar()

            return {
                "data": [dict(row) for row in rows],
                "total": total
            }