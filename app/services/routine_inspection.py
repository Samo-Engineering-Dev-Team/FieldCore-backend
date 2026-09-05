from uuid import UUID
from typing import List, Annotated
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_
from fastapi import Depends

from app.models import (
    InspectionGeneratorSummary,
    Notification,
    RoutineInspection,
    RoutineInspectionCreate,
    RoutineInspectionUpdate,
    RoutineInspectionResponse,
    Task,
    Technician,
    User,
)
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ForbiddenException,
)
from app.services.notification import NotificationTemplates
from app.services.report_support import record_generator_meter_readings
from app.utils.enums import UserRole


class _RoutineInspectionService:
    def inspection_to_response(
        self, inspection: RoutineInspection
    ) -> RoutineInspectionResponse:
        site = inspection.site if hasattr(inspection, "site") else None
        technician = (
            inspection.technician if hasattr(inspection, "technician") else None
        )
        task = inspection.task if hasattr(inspection, "task") else None

        site_name = site.name if site else None
        site_region = site.region if site else None
        site_type = site.site_type if site else None
        site_geofence_radius = site.geofence_radius if site else None
        coords = site.get_coordinates() if site else None
        technician_fullname = (
            f"{technician.user.name} {technician.user.surname}"
            if technician and technician.user
            else None
        )
        seacom_ref = task.seacom_ref if task else None

        return RoutineInspectionResponse(
            **inspection.model_dump(),
            site_name=site_name,
            site_region=site_region,
            site_type=site_type,
            site_geofence_radius=site_geofence_radius,
            site_latitude=coords[0] if coords else None,
            site_longitude=coords[1] if coords else None,
            technician_fullname=technician_fullname,
            seacom_ref=seacom_ref,
            gen1_generator=InspectionGeneratorSummary.from_generator(
                inspection.gen1_generator
            ),
            gen2_generator=InspectionGeneratorSummary.from_generator(
                inspection.gen2_generator
            ),
        )

    def _record_meter_readings(
        self, inspection: RoutineInspection, session: Session
    ) -> None:
        """Delegates to the shared writeback — see report_support."""
        record_generator_meter_readings(inspection, session)

    def create_inspection(
        self, data: RoutineInspectionCreate, session: Session
    ) -> RoutineInspectionResponse:
        inspection: RoutineInspection = RoutineInspection(**data.model_dump())
        try:
            session.add(inspection)
            session.commit()
            session.refresh(inspection)

            # Get task and technician info for notification
            task = session.exec(select(Task).where(Task.id == data.task_id)).first()
            technician = session.exec(
                select(Technician).where(Technician.id == data.technician_id)
            ).first()

            if task and technician:
                # Create notification for NOC operators about new inspection
                # Notify all NOC operators
                noc_users = session.exec(
                    select(User).where(
                        and_(User.role == UserRole.NOC, User.deleted_at.is_(None))
                    )
                ).all()

                # Get site name safely
                site_name = task.site.name if task.site else "Unknown Site"
                technician_name = (
                    technician.user.name if technician.user else "Unknown Technician"
                )

                template = NotificationTemplates.inspection_started(
                    site_name, technician_name
                )
                for noc_user in noc_users:
                    session.add(
                        Notification(
                            user_id=noc_user.id,
                            title=template.title,
                            message=template.message,
                            priority=template.priority,
                        )
                    )
                session.commit()

            return self.inspection_to_response(inspection)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating routine inspection: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error creating routine inspection: {e}"
            )

    def read_inspection(
        self, inspection_id: UUID, session: Session
    ) -> RoutineInspectionResponse:
        inspection = self._get_inspection(inspection_id, session)
        return self.inspection_to_response(inspection)

    def read_inspections(
        self,
        session: Session,
        status: str | None = None,
        technician_id: UUID | None = None,
        site_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[RoutineInspectionResponse]:
        statement = select(RoutineInspection).where(
            RoutineInspection.deleted_at.is_(None)
        )  # type: ignore

        if status is not None:
            statement = statement.where(RoutineInspection.status == status)
        if technician_id is not None:
            statement = statement.where(
                RoutineInspection.technician_id == technician_id
            )
        if site_id is not None:
            statement = statement.where(RoutineInspection.site_id == site_id)

        statement = statement.offset(offset).limit(limit)
        inspections = session.exec(statement).all()
        return [self.inspection_to_response(inspection) for inspection in inspections]

    def update_inspection(
        self, inspection_id: UUID, data: RoutineInspectionUpdate, session: Session
    ) -> RoutineInspectionResponse:
        inspection = self._get_inspection(inspection_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if inspection.status == "completed":
            raise ForbiddenException("Cannot update a completed routine inspection.")

        if not update_data:
            return self.inspection_to_response(inspection)

        for k, v in update_data.items():
            setattr(inspection, k, v)

        inspection.touch()

        try:
            session.commit()
            session.refresh(inspection)
            return self.inspection_to_response(inspection)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating routine inspection: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating routine inspection: {e}"
            )

    def delete_inspection(self, inspection_id: UUID, session: Session) -> None:
        inspection = self._get_inspection(inspection_id, session)
        inspection.soft_delete()
        session.commit()

    def submit_inspection(
        self, inspection_id: UUID, session: Session
    ) -> RoutineInspectionResponse:
        """Submit a routine inspection, marking it as completed"""
        inspection = self._get_inspection(inspection_id, session)

        if inspection.status == "completed":
            raise ForbiddenException(
                "This routine inspection has already been completed."
            )

        inspection.status = "completed"
        inspection.touch()
        self._record_meter_readings(inspection, session)

        try:
            session.commit()
            session.refresh(inspection)

            # Create notification for NOC operators about completion
            task = session.exec(
                select(Task).where(Task.id == inspection.task_id)
            ).first()
            site_name = task.site.name if task and task.site else "Unknown Site"

            noc_users = session.exec(
                select(User).where(
                    and_(User.role == UserRole.NOC, User.deleted_at.is_(None))
                )
            ).all()

            template = NotificationTemplates.inspection_completed(site_name)
            for noc_user in noc_users:
                session.add(
                    Notification(
                        user_id=noc_user.id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
            session.commit()

            return self.inspection_to_response(inspection)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error submitting routine inspection: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error submitting routine inspection: {e}"
            )

    def _get_inspection(
        self, inspection_id: UUID, session: Session
    ) -> RoutineInspection:
        inspection = session.exec(
            select(RoutineInspection).where(
                and_(
                    RoutineInspection.id == inspection_id,
                    RoutineInspection.deleted_at.is_(None),
                )
            )
        ).first()

        if not inspection:
            raise NotFoundException("Routine inspection not found")

        return inspection


RoutineInspectionService = Annotated[
    _RoutineInspectionService, Depends(lambda: _RoutineInspectionService())
]
