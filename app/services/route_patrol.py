"""
RoutePatrol service — CRUD for weekly fibre route surveillance records.
"""

from uuid import UUID
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, select

from app.exceptions.http import BadRequestException, ForbiddenException
from app.models.route_patrol import (
    RoutePatrol,
    RoutePatrolCreate,
    RoutePatrolUpdate,
    RoutePatrolResponse,
)
from app.models.auth import TokenData
from app.services.authorization import get_technician_id_for_user, is_management
from app.services.field_work import create_completed_field_report
from app.utils.enums import ReportType


def _route_patrol_all_photos(photos: dict) -> list:
    """Collect every photo uploaded for a route patrol submission."""
    collected: list = []
    seen: set[str] = set()

    def add_many(items) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            source = ""
            if isinstance(item, str):
                source = item.strip()
            elif isinstance(item, dict):
                source = str(
                    item.get("signed_url")
                    or item.get("url")
                    or item.get("public_url")
                    or item.get("file_path")
                    or item.get("path")
                    or ""
                ).strip()
            if not source or source in seen:
                continue
            seen.add(source)
            collected.append(item)

    add_many(photos.get("trip_start_photos"))
    add_many(photos.get("trip_end_photos"))
    add_many(photos.get("all_photos"))

    for group_key in (
        "bridge_culvert_checks",
        "activity_checks",
        "manhole_inspections",
    ):
        for entry in photos.get(group_key) or []:
            if isinstance(entry, dict):
                add_many(entry.get("photos"))

    return collected


def _enrich(patrol: RoutePatrol, session: Session) -> RoutePatrolResponse:
    from app.models import Technician, Site

    tech_name = ""
    site_name = ""
    site_region = None
    site_type = None
    site_geofence_radius = None
    coords = None

    tech = session.get(Technician, patrol.technician_id)
    if tech and tech.user:
        tech_name = f"{tech.user.name} {tech.user.surname}"

    if patrol.site_id:
        site = session.get(Site, patrol.site_id)
        if site:
            site_name = site.name
            site_region = site.region
            site_type = site.site_type
            site_geofence_radius = site.geofence_radius
            coords = site.get_coordinates()

    resp = RoutePatrolResponse.model_validate(patrol)
    resp.technician_fullname = tech_name
    resp.site_name = site_name
    resp.site_region = site_region
    resp.site_type = site_type
    resp.site_geofence_radius = site_geofence_radius
    resp.site_latitude = coords[0] if coords else None
    resp.site_longitude = coords[1] if coords else None
    return resp


class _RoutePatrolService:
    def create(
        self,
        data: RoutePatrolCreate,
        session: Session,
        current_user: TokenData,
    ) -> RoutePatrolResponse:
        if not is_management(current_user):
            technician_id = get_technician_id_for_user(current_user.user_id, session)
            if data.technician_id != technician_id:
                raise ForbiddenException(
                    "Technicians can only create patrols for themselves."
                )
            data = data.model_copy(update={"technician_id": technician_id})

        patrol = RoutePatrol.model_validate(data)
        session.add(patrol)

        if data.site_id:
            from app.models import Site, Technician

            technician = session.get(Technician, data.technician_id)
            site = session.get(Site, data.site_id)
            if technician and site:
                photos = dict(data.photos) if isinstance(data.photos, dict) else {}
                all_photos = _route_patrol_all_photos(photos)
                photos["all_photos"] = all_photos
                seacom_ref = str(photos.get("noc_ticket") or "N/A").strip() or "N/A"
                report_data = {
                    "source": "route_patrol",
                    "route_segment": data.route_segment,
                    "patrol_date": data.patrol_date.isoformat(),
                    "weather_conditions": data.weather_conditions,
                    "anomalies_found": data.anomalies_found,
                    "anomaly_details": data.anomaly_details,
                    "photos": photos,
                }
                _, report = create_completed_field_report(
                    session=session,
                    technician=technician,
                    site=site,
                    report_type=ReportType.ROUTINE_DRIVE,
                    seacom_ref=seacom_ref,
                    performed_at=data.patrol_date,
                    data=report_data,
                    attachments={"files": all_photos} if all_photos else None,
                )
                patrol.report_id = report.id

        session.commit()
        session.refresh(patrol)

        # Auto-mark the technician's routine_drive maintenance schedule as done
        if not patrol.report_id:
            self._mark_routine_drive_done(data.technician_id, session)

        return _enrich(patrol, session)

    def _mark_routine_drive_done(self, technician_id: UUID, session: Session) -> None:
        """After a route patrol is submitted, advance the maintenance schedule for this technician."""
        from app.models.maintenance_schedule import MaintenanceSchedule
        from app.utils.funcs import utcnow
        from datetime import timedelta

        sched = session.exec(
            select(MaintenanceSchedule).where(
                MaintenanceSchedule.assigned_technician_id == technician_id,
                MaintenanceSchedule.schedule_type == "routine_drive",
                MaintenanceSchedule.deleted_at.is_(None),  # type: ignore
                MaintenanceSchedule.is_active,  # type: ignore
            )
        ).first()

        if sched:
            now = utcnow()
            sched.last_run_at = now
            sched.next_due_at = now + timedelta(days=7)
            session.add(sched)
            session.commit()

    def list_patrols(
        self,
        session: Session,
        current_user: TokenData,
        technician_id: UUID | None = None,
        site_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RoutePatrolResponse]:
        if not is_management(current_user):
            technician_id = get_technician_id_for_user(current_user.user_id, session)

        stmt = select(RoutePatrol).where(RoutePatrol.deleted_at.is_(None))
        if technician_id:
            stmt = stmt.where(RoutePatrol.technician_id == technician_id)
        if site_id:
            stmt = stmt.where(RoutePatrol.site_id == site_id)
        stmt = stmt.order_by(RoutePatrol.patrol_date.desc()).offset(offset).limit(limit)  # type: ignore
        return [_enrich(p, session) for p in session.exec(stmt).all()]

    def get(
        self, patrol_id: UUID, session: Session, current_user: TokenData
    ) -> RoutePatrolResponse:
        p = session.get(RoutePatrol, patrol_id)
        if not p or p.deleted_at:
            from app.exceptions.http import NotFoundException

            raise NotFoundException("route patrol not found")
        if not is_management(current_user):
            technician_id = get_technician_id_for_user(current_user.user_id, session)
            if p.technician_id != technician_id:
                raise ForbiddenException(
                    "Technicians can only view their own route patrols."
                )
        return _enrich(p, session)

    def update(
        self, patrol_id: UUID, data: RoutePatrolUpdate, session: Session
    ) -> RoutePatrolResponse:
        p = session.get(RoutePatrol, patrol_id)
        if not p or p.deleted_at:
            from app.exceptions.http import NotFoundException

            raise NotFoundException("route patrol not found")
        non_nullable_fields = {"technician_id", "route_segment", "patrol_date"}
        update_data = data.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            if k in non_nullable_fields and v is None:
                raise BadRequestException(f"{k} cannot be null")
            setattr(p, k, v)
        p.touch()
        session.commit()
        session.refresh(p)
        return _enrich(p, session)

    def delete(self, patrol_id: UUID, session: Session) -> None:
        p = session.get(RoutePatrol, patrol_id)
        if not p or p.deleted_at:
            from app.exceptions.http import NotFoundException

            raise NotFoundException("route patrol not found")
        p.soft_delete()
        session.commit()


def get_route_patrol_service() -> "_RoutePatrolService":
    return _RoutePatrolService()


RoutePatrolService = Annotated[_RoutePatrolService, Depends(get_route_patrol_service)]
