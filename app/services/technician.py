from uuid import UUID
from fastapi import Depends
from typing import List, Annotated
from datetime import datetime, timedelta
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from geoalchemy2.functions import ST_DWithin, ST_Distance, ST_SetSRID, ST_MakePoint
from loguru import logger as LOG

from app.models import Technician, TechnicianCreate, TechnicianUpdate, TechnicianResponse, TechnicianLocationUpdate, User, Site
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)
from app.utils.funcs import utcnow


class _TechnicianService:
    def technician_to_response(self, technician: Technician, distance_km: float | None = None) -> TechnicianResponse:
        current_coords = technician.get_current_coordinates()
        home_coords = technician.get_home_base_coordinates()
        return TechnicianResponse(
            id=technician.id,
            created_at=technician.created_at,
            updated_at=technician.updated_at,
            deleted_at=technician.deleted_at,
            phone=technician.phone,
            id_no=technician.id_no,
            user_id=technician.user_id,
            fullname=f"{technician.user.name} {technician.user.surname}",
            is_available=technician.is_available,
            current_latitude=current_coords[0] if current_coords else None,
            current_longitude=current_coords[1] if current_coords else None,
            last_location_update=technician.last_location_update,
            home_latitude=home_coords[0] if home_coords else None,
            home_longitude=home_coords[1] if home_coords else None,
            distance_km=distance_km,
        )

    def create_technician(self, data: TechnicianCreate, session: Session) -> TechnicianResponse:
        # Handle user
        statement = select(User).where(User.id == data.user_id, User.deleted_at.is_(None)) # type: ignore
        user: User | None = session.exec(statement).first()

        if not user:
            raise NotFoundException("user not found, cannot create technician.")
        
        # Extract home location before creating
        tech_data = data.model_dump(exclude={"home_latitude", "home_longitude"})
        technician: Technician = Technician(**tech_data, user=user)
        
        # Set home base if provided
        if data.home_latitude is not None and data.home_longitude is not None:
            technician.set_home_base(data.home_latitude, data.home_longitude)
        
        try:
            session.add(technician)
            session.commit()
            session.refresh(technician)
            return self.technician_to_response(technician)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating technician: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating technician: {e}")

    def read_technician(self, technician_id: UUID, session: Session) -> TechnicianResponse:
        technician = self._get_technician(technician_id, session)
        return self.technician_to_response(technician)

    def read_technicians(
        self,
        session: Session,
        offset: int = 0,
        limit: int = 100,
    ) -> List[TechnicianResponse]:
        statement = select(Technician).where(Technician.deleted_at.is_(None))  # type: ignore
        statement = statement.offset(offset).limit(limit)
        technicians = session.exec(statement).all()
        return [self.technician_to_response(technician) for technician in technicians]

    def update_technician(
        self, technician_id: UUID, data: TechnicianUpdate, session: Session
    ) -> TechnicianResponse:
        technician = self._get_technician(technician_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.technician_to_response(technician)

        # Handle home location update separately
        home_lat = update_data.pop("home_latitude", None)
        home_lon = update_data.pop("home_longitude", None)
        
        if home_lat is not None and home_lon is not None:
            technician.set_home_base(home_lat, home_lon)

        for k, v in update_data.items():
            setattr(technician, k, v)

        technician.touch()

        try:
            session.commit()
            session.refresh(technician)
            return self.technician_to_response(technician)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating technician: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating technician: {e}")

    def delete_technician(self, technician_id: UUID, session: Session) -> None:
        technician = self._get_technician(technician_id, session)
        technician.soft_delete()
        session.commit()

    def read_me(self, user_id: UUID, session: Session) -> TechnicianResponse:
        statement = (
            select(Technician)
            .where(Technician.user_id == user_id)
            .where(Technician.deleted_at.is_(None))  # type: ignore
        )
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician profile not found for current user")
        return self.technician_to_response(technician)

    def _get_technician(self, technician_id: UUID, session: Session) -> Technician:
        statement = (
            select(Technician)
            .where(Technician.id == technician_id)
            .where(Technician.deleted_at.is_(None)) # type: ignore
          )
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician not found")
        return technician

    # ==================== LOCATION TRACKING ====================
    
    def update_location(
        self, 
        technician_id: UUID, 
        data: TechnicianLocationUpdate, 
        session: Session
    ) -> TechnicianResponse:
        """Update technician's current location (called from mobile app)."""
        technician = self._get_technician(technician_id, session)
        technician.update_location(data.latitude, data.longitude)
        
        try:
            session.commit()
            session.refresh(technician)
            return self.technician_to_response(technician)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Error updating location: {e}")

    # ==================== SMART DISPATCH ====================
    
    def find_nearest_technicians(
        self,
        latitude: float,
        longitude: float,
        session: Session,
        limit: int = 5,
        available_only: bool = True,
        max_distance_km: float | None = None,
    ) -> List[TechnicianResponse]:
        """
        Find nearest available technicians to a given location.
        Used for smart incident/task dispatch.
        """
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        
        # Build query
        statement = (
            select(
                Technician,
                # Calculate distance in kilometers using geography cast
                (ST_Distance(Technician.current_location, point, use_spheroid=True) / 1000).label("distance_km")
            )
            .where(Technician.deleted_at.is_(None))
            .where(Technician.current_location.isnot(None))
        )
        
        if available_only:
            statement = statement.where(Technician.is_available == True)
        
        if max_distance_km:
            # Filter by max distance (convert km to meters)
            statement = statement.where(
                ST_DWithin(Technician.current_location, point, max_distance_km * 1000, use_spheroid=True)
            )
        
        statement = statement.order_by("distance_km").limit(limit)
        
        results = session.execute(statement).all()
        return [
            self.technician_to_response(row.Technician, distance_km=round(row.distance_km, 2))
            for row in results
        ]
    
    def find_nearest_to_site(
        self,
        site_id: UUID,
        session: Session,
        limit: int = 5,
        available_only: bool = True,
    ) -> List[TechnicianResponse]:
        """Find nearest technicians to a specific site."""
        # Get site location
        site = session.exec(
            select(Site).where(Site.id == site_id, Site.deleted_at.is_(None))
        ).first()
        
        if not site:
            raise NotFoundException("Site not found")
        
        if site.location is None:
            raise ConflictException("Site does not have a location set")
        
        coords = site.get_coordinates()
        if not coords:
            raise ConflictException("Could not get site coordinates")
        
        return self.find_nearest_technicians(
            latitude=coords[0],
            longitude=coords[1],
            session=session,
            limit=limit,
            available_only=available_only,
        )
    
    def get_technicians_in_region(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        session: Session,
        available_only: bool = False,
    ) -> List[TechnicianResponse]:
        """Get all technicians within a radius of a point."""
        return self.find_nearest_technicians(
            latitude=latitude,
            longitude=longitude,
            session=session,
            limit=100,
            available_only=available_only,
            max_distance_km=radius_km,
        )
    
    def get_stale_locations(
        self,
        session: Session,
        stale_minutes: int = 30,
    ) -> List[TechnicianResponse]:
        """Get technicians with stale location data (for monitoring)."""
        cutoff = utcnow() - timedelta(minutes=stale_minutes)
        
        statement = (
            select(Technician)
            .where(Technician.deleted_at.is_(None))
            .where(Technician.is_available == True)
            .where(
                (Technician.last_location_update.is_(None)) |
                (Technician.last_location_update < cutoff)
            )
        )
        
        technicians = session.exec(statement).all()
        return [self.technician_to_response(t) for t in technicians]

    def escalate_technician_issue(
        self,
        technician_id: UUID,
        reason: str,
        priority: str,
        escalated_by: UUID,
        session: Session
    ) -> dict:
        """Escalate a technician issue to management."""
        from app.services.notification import _NotificationService, NotificationTemplates
        
        # Get technician details
        statement = select(Technician).where(Technician.id == technician_id, Technician.deleted_at.is_(None))
        technician = session.exec(statement).first()
        
        if not technician:
            raise NotFoundException("Technician not found")
        
        # Get management users to notify
        management_statement = select(User).where(
            User.role.in_(["ADMIN", "MANAGER"]),  # Assuming these are management roles
            User.deleted_at.is_(None)
        )
        management_users = session.exec(management_statement).all()
        
        notifications_created = []
        
        # Create notifications for all management users
        notification_service = _NotificationService()
        template = NotificationTemplates.technician_escalation(
            technician_name=f"{technician.user.name} {technician.user.surname}",
            priority=priority,
            reason=reason,
        )
        for manager in management_users:
            try:
                created = notification_service.create_notification_from_template(
                    user_id=manager.id,
                    template=template,
                    session=session,
                )
                if created:
                    notifications_created.append(created.id)
            except Exception as e:
                # Log error but continue with other notifications
                LOG.warning("Failed to create notification for {}: {}", manager.email, e)
        
        # Log the escalation in the database (you might want to create an escalation_log table)
        # For now, we'll just return success info
        
        return {
            "success": True,
            "technician_id": str(technician_id),
            "escalated_by": str(escalated_by),
            "reason": reason,
            "priority": priority,
            "notifications_sent": len(notifications_created),
            "timestamp": utcnow().isoformat()
        }


def get_technician_service() -> _TechnicianService:
    return _TechnicianService()


TechnicianService = Annotated[_TechnicianService, Depends(get_technician_service)]
