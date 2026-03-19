from uuid import UUID
from fastapi import Depends
from typing import List, Annotated, Tuple
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from geoalchemy2.functions import ST_DWithin, ST_Distance, ST_SetSRID, ST_MakePoint
from shapely.geometry import Point

from app.utils.enums import Region
from app.models import Site, SiteCreate, SiteUpdate, SiteResponse, Task, Incident
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)


class _SiteService:
    def site_to_response(
        self,
        site: Site,
        *,
        num_tasks: int = 0,
        num_incidents: int = 0,
    ) -> SiteResponse:
        coords = site.get_coordinates()
        return SiteResponse(
            id=site.id,
            created_at=site.created_at,
            updated_at=site.updated_at,
            deleted_at=site.deleted_at,
            name=site.name,
            region=site.region,
            address=site.address,
            latitude=coords[0] if coords else None,
            longitude=coords[1] if coords else None,
            geofence_radius=site.geofence_radius,
            num_tasks=num_tasks,
            num_incidents=num_incidents,
            num_reports=0,  # TODO: Add reports relationship if needed
        )

    def _get_related_counts(
        self,
        session: Session,
        site_ids: List[UUID],
    ) -> Tuple[dict[UUID, int], dict[UUID, int]]:
        if not site_ids:
            return {}, {}

        task_rows = session.exec(
            select(Task.site_id, func.count(Task.id))
            .where(Task.deleted_at.is_(None), Task.site_id.in_(site_ids))  # type: ignore
            .group_by(Task.site_id)
        ).all()
        task_counts: dict[UUID, int] = {
            site_id: int(count) for site_id, count in task_rows
        }

        incident_rows = session.exec(
            select(Incident.site_id, func.count(Incident.id))
            .where(Incident.deleted_at.is_(None), Incident.site_id.in_(site_ids))  # type: ignore
            .group_by(Incident.site_id)
        ).all()
        incident_counts: dict[UUID, int] = {
            site_id: int(count) for site_id, count in incident_rows
        }

        return task_counts, incident_counts

    def _build_site_responses(
        self,
        sites: List[Site],
        session: Session,
    ) -> List[SiteResponse]:
        if not sites:
            return []

        site_ids = [site.id for site in sites]
        task_counts, incident_counts = self._get_related_counts(session, site_ids)
        return [
            self.site_to_response(
                site,
                num_tasks=task_counts.get(site.id, 0),
                num_incidents=incident_counts.get(site.id, 0),
            )
            for site in sites
        ]

    def create_site(self, data: SiteCreate, session: Session) -> SiteResponse:
        # Extract lat/lon before creating site
        site_data = data.model_dump(exclude={"latitude", "longitude"})
        site: Site = Site(**site_data)
        
        # Set location if coordinates provided
        if data.latitude is not None and data.longitude is not None:
            site.set_location(data.latitude, data.longitude)
        
        try:
            session.add(site)
            session.commit()
            session.refresh(site)
            return self.site_to_response(site, num_tasks=0, num_incidents=0)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating site: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating site: {e}")

    def read_site(self, site_id: UUID, session: Session) -> SiteResponse:
        site = self._get_site(site_id, session)
        responses = self._build_site_responses([site], session)
        return responses[0]

    def read_sites(
        self,
        session: Session,
        region: Region | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[SiteResponse]:
        statement = select(Site).where(Site.deleted_at.is_(None))  # type: ignore

        if region is not None:
            statement = statement.where(Site.region == region)

        statement = statement.offset(offset).limit(limit)
        sites = session.exec(statement).all()
        return self._build_site_responses(sites, session)

    def update_site(
        self, site_id: UUID, data: SiteUpdate, session: Session
    ) -> SiteResponse:
        site = self._get_site(site_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            responses = self._build_site_responses([site], session)
            return responses[0]

        # Handle location update separately
        lat = update_data.pop("latitude", None)
        lon = update_data.pop("longitude", None)
        
        if lat is not None and lon is not None:
            site.set_location(lat, lon)

        for k, v in update_data.items():
            setattr(site, k, v)

        site.touch()

        try:
            session.commit()
            session.refresh(site)
            responses = self._build_site_responses([site], session)
            return responses[0]
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating site: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating site: {e}")

    def delete_site(self, site_id: UUID, session: Session) -> None:
        site = self._get_site(site_id, session)
        site.soft_delete()
        session.commit()

    def _get_site(self, site_id: UUID, session: Session) -> Site:
        statement = select(Site).where(Site.id == site_id, Site.deleted_at.is_(None))  # type: ignore
        site: Site | None = session.exec(statement).first()
        if not site:
            raise NotFoundException("site not found")
        return site

    def find_nearby_sites(
        self, 
        latitude: float, 
        longitude: float, 
        radius_meters: float,
        session: Session,
        limit: int = 10
    ) -> List[SiteResponse]:
        """Find sites within a given radius of a point."""
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        
        statement = (
            select(Site)
            .where(Site.deleted_at.is_(None))
            .where(Site.location.isnot(None))
            .where(ST_DWithin(Site.location, point, radius_meters, use_spheroid=True))
            .order_by(ST_Distance(Site.location, point))
            .limit(limit)
        )
        
        sites = session.exec(statement).all()
        return self._build_site_responses(sites, session)

    def is_within_geofence(
        self,
        site_id: UUID,
        latitude: float,
        longitude: float,
        session: Session
    ) -> bool:
        """Check if a point is within the site's geofence."""
        site = self._get_site(site_id, session)
        
        if site.location is None:
            return False
        
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        
        result = session.execute(
            select(ST_DWithin(site.location, point, site.geofence_radius, use_spheroid=True))
        ).scalar()
        
        return bool(result)


def get_site_service() -> _SiteService:
    return _SiteService()


SiteService = Annotated[_SiteService, Depends(get_site_service)]
