"""Generator asset register.

CRUD over the physical units, plus site assignment. Writes are management-only
(`require_management` — super_admin, admin, manager, noc); reads are open to any
authenticated user, because a technician has to list units to pick the one they
are refuelling.

Site assignment is its own operation rather than a field on the generic update:
it is the one change with a real-world action behind it (a unit was moved), it
needs its own validation, and `site_id=None` unassigning is explicit rather than
indistinguishable from "field omitted".
"""

from datetime import datetime
from io import BytesIO
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.exceptions.http import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    Generator,
    GeneratorCreate,
    GeneratorResponse,
    GeneratorUpdate,
    Site,
)
from app.models.auth import TokenData
from app.models.disbursement import Disbursement
from app.models.funds_request import FundsRequest
from app.models.report_data import GeneratorDieselHistory, GeneratorRefuelEntry
from app.services.authorization import require_management, require_report_read
from app.services.finance_dashboard import legacy_refuel_cutover
from app.services.report_support import (
    assert_site_history_in_scope,
    diesel_reports_for_site,
    flatten_diesel_fillups,
)
from app.utils.enums import FundsRequestType
from app.utils.funcs import format_iso_week, utcnow


class _GeneratorService:
    def _to_response(self, generator: Generator) -> GeneratorResponse:
        return GeneratorResponse.from_generator(generator)

    def _get(self, generator_id: UUID, session: Session) -> Generator:
        generator = session.exec(
            select(Generator).where(
                Generator.id == generator_id,
                Generator.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not generator:
            raise NotFoundException("generator not found")
        return generator

    def _get_site(self, site_id: UUID, session: Session) -> Site:
        site = session.exec(
            select(Site).where(
                Site.id == site_id,
                Site.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if site is None:
            raise NotFoundException("site not found")
        return site

    def _duplicate_serial(self, serial_no: str | None) -> ConflictException:
        # The partial unique index on serial_no is the only realistic cause of an
        # IntegrityError here; name the unit rather than surfacing the raw
        # constraint error.
        return ConflictException(
            f"Serial number {serial_no} is already registered to another generator. "
            "Check the plate, or reactivate the existing unit."
        )

    def read_generators(
        self,
        session: Session,
        site_id: UUID | None = None,
        unassigned: bool = False,
        include_inactive: bool = False,
    ) -> list[GeneratorResponse]:
        """
        List units, newest naming order aside — ordered by name so the grid reads
        alphabetically rather than by insertion.

        `site_id` and `unassigned` are separate filters on purpose: `unassigned`
        cannot be expressed as a site id, and asking for both is a contradiction
        the caller resolves, not this method (site_id wins).
        """
        statement = select(Generator).where(Generator.deleted_at.is_(None))  # type: ignore
        if site_id is not None:
            statement = statement.where(Generator.site_id == site_id)
        elif unassigned:
            statement = statement.where(Generator.site_id.is_(None))  # type: ignore
        if not include_inactive:
            statement = statement.where(Generator.is_active)  # type: ignore[arg-type]
        statement = statement.order_by(Generator.name)  # type: ignore[arg-type]
        return [self._to_response(g) for g in session.exec(statement).all()]

    def read_generator(self, generator_id: UUID, session: Session) -> GeneratorResponse:
        return self._to_response(self._get(generator_id, session))

    def create_generator(
        self, payload: GeneratorCreate, session: Session, current_user: TokenData
    ) -> GeneratorResponse:
        require_management(current_user, "Only management may register a generator")

        # A site is optional — a unit can be registered before it is placed — so
        # it is validated only when one was given.
        if payload.site_id is not None:
            self._get_site(payload.site_id, session)

        generator = Generator(**payload.model_dump())
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except IntegrityError:
            session.rollback()
            raise self._duplicate_serial(payload.serial_no)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating generator: {e}")

    def update_generator(
        self,
        generator_id: UUID,
        payload: GeneratorUpdate,
        session: Session,
        current_user: TokenData,
    ) -> GeneratorResponse:
        require_management(current_user, "Only management may change a generator")
        generator = self._get(generator_id, session)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(generator, field, value)
        generator.touch()
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except IntegrityError:
            session.rollback()
            raise self._duplicate_serial(payload.serial_no)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating generator: {e}")

    def assign_site(
        self,
        generator_id: UUID,
        site_id: UUID | None,
        session: Session,
        current_user: TokenData,
    ) -> GeneratorResponse:
        """Assign a unit to a site, or unassign it when `site_id` is None."""
        require_management(current_user, "Only management may assign a generator to a site")
        generator = self._get(generator_id, session)
        if site_id is not None:
            self._get_site(site_id, session)

        generator.site_id = site_id
        generator.touch()
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error assigning generator: {e}")


    # ── Refuel history ────────────────────────────────────────────────────

    def read_diesel_history(
        self,
        generator_id: UUID,
        session: Session,
        current_user: TokenData | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> GeneratorDieselHistory:
        """
        Every refuel recorded against one unit, from both sources.

        A unit's refuels live in two places and neither is complete alone:

        * **Diesel report JSON** — litres, runtime, fill reason, and the Rand
          recorded in the field. Identifies the unit only by free-text `gen_no`,
          so this leg resolves through `legacy_gen_no` and can only run for a
          unit that has one *and* is placed at a site.
        * **The funds ledger** — the Rand actually disbursed, against a real FK.
          Works regardless of assignment, so an unassigned unit still shows its
          history: the unit moved, its refuels did not vanish.

        The cutover rule is the Finance Dashboard's, reused rather than
        restated: litres always come from the report because the ledger never
        records what was filled; Rand comes from the report before the cutover
        and from the ledger after, so a fill is never counted twice.
        """
        if current_user is not None:
            require_report_read(
                current_user, "You do not have permission to read reports."
            )

        generator = self._get(generator_id, session)

        # Authorization depends only on where the unit is, never on which data
        # source happens to have rows. Tying it to a leg left a unit that is
        # placed at a site but has no legacy_gen_no — every unit registered
        # through the asset register — checked by neither branch, while the
        # ledger leg below still returned its history.
        if current_user is not None:
            if generator.site_id is not None:
                assert_site_history_in_scope(generator.site_id, current_user, session)
            else:
                # An unassigned unit has no site to scope a technician against,
                # so only management may read it.
                require_management(
                    current_user,
                    "That generator is not assigned to a site, so only management "
                    "can read its history.",
                )

        entries: list[GeneratorRefuelEntry] = []
        cutover = legacy_refuel_cutover(session)

        # ── Legacy leg ────────────────────────────────────────────────────
        # Only resolvable for a unit that is placed and carries a legacy number;
        # a unit registered through the asset register has no legacy fills.
        if generator.site_id is not None and generator.legacy_gen_no is not None:
            reports = diesel_reports_for_site(
                session, generator.site_id, date_from, date_to
            )
            for entry in flatten_diesel_fillups(reports):
                if entry.gen_no != generator.legacy_gen_no:
                    continue
                # Post-cutover Rand belongs to the ledger; taking it from the
                # report too would double-count the same refuel.
                pre_cutover = cutover is None or (
                    entry.fill_date is not None and entry.fill_date < cutover
                )
                entries.append(
                    GeneratorRefuelEntry(
                        source="report",
                        fill_date=entry.fill_date,
                        iso_week=entry.iso_week,
                        liters_filled=entry.liters_filled,
                        amount=entry.amount_used if pre_cutover else 0.0,
                        fill_reason=entry.fill_reason,
                        gen_runtime_hours=entry.gen_runtime_hours,
                        technician_name=entry.technician_name,
                        seacom_ref=entry.seacom_ref,
                        report_id=entry.report_id,
                    )
                )

        # ── Ledger leg ────────────────────────────────────────────────────
        ledger_statement = (
            select(FundsRequest, Disbursement)
            .join(Disbursement, Disbursement.funds_request_id == FundsRequest.id)  # type: ignore[arg-type]
            .where(
                FundsRequest.generator_id == generator_id,
                FundsRequest.type == FundsRequestType.GENERATOR_REFUEL,
                FundsRequest.deleted_at.is_(None),  # type: ignore
                Disbursement.deleted_at.is_(None),  # type: ignore
            )
        )
        if date_from is not None:
            ledger_statement = ledger_statement.where(
                Disbursement.approved_at >= date_from  # type: ignore[arg-type]
            )
        if date_to is not None:
            ledger_statement = ledger_statement.where(
                Disbursement.approved_at <= date_to  # type: ignore[arg-type]
            )

        for request, disbursement in session.exec(ledger_statement).all():  # type: ignore[misc]
            released = disbursement.released_at or disbursement.approved_at
            entries.append(
                GeneratorRefuelEntry(
                    source="ledger",
                    fill_date=released,
                    iso_week=format_iso_week(released),
                    # The ledger never records what was actually filled — only
                    # what was requested — so litres stay with the report leg.
                    liters_filled=0.0,
                    amount=float(disbursement.amount_issued or 0),
                    fill_reason=request.description,
                    funds_request_id=str(request.id),
                )
            )

        # Newest first: the question is almost always "when was this last
        # filled", not "when did it start".
        entries.sort(key=lambda e: (e.fill_date is None, e.fill_date), reverse=True)
        fill_dates = sorted(e.fill_date for e in entries if e.fill_date)

        return GeneratorDieselHistory(
            generator_id=str(generator.id),
            generator_name=generator.name,
            serial_no=generator.serial_no,
            site_id=str(generator.site_id) if generator.site_id else None,
            site_name=generator.site.name if generator.site else "",
            date_from=date_from,
            date_to=date_to,
            first_fill_date=fill_dates[0] if fill_dates else None,
            last_fill_date=fill_dates[-1] if fill_dates else None,
            entries=entries,
            entry_count=len(entries),
            total_liters=round(sum(e.liters_filled for e in entries), 2),
            total_amount=round(sum(e.amount for e in entries), 2),
        )


    def export_diesel_history_pdf(
        self,
        generator_id: UUID,
        session: Session,
        current_user: TokenData | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[BytesIO, str]:
        """Render this unit's refuel history as a PDF, with its filename."""
        history = self.read_diesel_history(
            generator_id, session, current_user, date_from, date_to
        )
        try:
            from app.services.pdf import PDFService

            pdf_buffer = PDFService().generate_generator_diesel_history_pdf(history)
            slug = (
                "".join(
                    ch if ch.isalnum() else "_" for ch in history.generator_name.lower()
                ).strip("_")
                or "generator"
            )
            filename = f"refuel_history_{slug}_{utcnow().strftime('%Y%m%d')}.pdf"
            pdf_buffer.seek(0)
            return pdf_buffer, filename
        except (ForbiddenException, NotFoundException):
            raise
        except Exception as e:
            raise InternalServerErrorException(f"Failed to generate PDF: {e}")

    def delete_generator(
        self, generator_id: UUID, session: Session, current_user: TokenData
    ) -> None:
        """Soft delete. Historical refuel records keep pointing at the row so past
        fills stay attributable; prefer is_active=False for a decommissioned unit."""
        require_management(current_user, "Only management may remove a generator")
        generator = self._get(generator_id, session)
        generator.soft_delete()
        session.add(generator)
        session.commit()


def get_generator_service() -> _GeneratorService:
    return _GeneratorService()


GeneratorService = Annotated[_GeneratorService, Depends(get_generator_service)]
