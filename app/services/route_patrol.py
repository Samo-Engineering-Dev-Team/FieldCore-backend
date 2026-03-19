"""
RoutePatrol service — CRUD for weekly fibre route surveillance records.
"""

from uuid import UUID
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, select

from app.models.route_patrol import (
    RoutePatrol,
    RoutePatrolCreate,
    RoutePatrolUpdate,
    RoutePatrolResponse,
)


def _enrich(patrol: RoutePatrol, session: Session) -> RoutePatrolResponse:
    from app.models import Technician, Site
    tech_name = ""
    site_name = ""

    tech = session.get(Technician, patrol.technician_id)
    if tech and tech.user:
        tech_name = f"{tech.user.name} {tech.user.surname}"

    if patrol.site_id:
        site = session.get(Site, patrol.site_id)
        if site:
            site_name = site.name

    resp = RoutePatrolResponse.model_validate(patrol)
    resp.technician_fullname = tech_name
    resp.site_name           = site_name
    return resp


class _RoutePatrolService:
    def create(self, data: RoutePatrolCreate, session: Session) -> RoutePatrolResponse:
        patrol = RoutePatrol.model_validate(data)
        session.add(patrol)
        session.commit()
        session.refresh(patrol)

        # Auto-mark the technician's routine_drive maintenance schedule as done
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
                MaintenanceSchedule.is_active == True,  # type: ignore noqa: E712
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
        technician_id: UUID | None = None,
        site_id:       UUID | None = None,
        limit:         int = 100,
        offset:        int = 0,
    ) -> list[RoutePatrolResponse]:
        stmt = select(RoutePatrol).where(RoutePatrol.deleted_at.is_(None))
        if technician_id:
            stmt = stmt.where(RoutePatrol.technician_id == technician_id)
        if site_id:
            stmt = stmt.where(RoutePatrol.site_id == site_id)
        stmt = stmt.order_by(RoutePatrol.patrol_date.desc()).offset(offset).limit(limit)  # type: ignore
        return [_enrich(p, session) for p in session.exec(stmt).all()]

    def get(self, patrol_id: UUID, session: Session) -> RoutePatrolResponse:
        p = session.get(RoutePatrol, patrol_id)
        if not p or p.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("route patrol not found")
        return _enrich(p, session)

    def update(self, patrol_id: UUID, data: RoutePatrolUpdate, session: Session) -> RoutePatrolResponse:
        p = session.get(RoutePatrol, patrol_id)
        if not p or p.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("route patrol not found")
        for k, v in data.model_dump(exclude_none=True).items():
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
