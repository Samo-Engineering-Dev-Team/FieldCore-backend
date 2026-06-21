from fastapi import APIRouter

from app.database import SessionDep
from app.models import FieldWorkCreate, FieldWorkResponse, ReportResponse
from app.services import CurrentUser, FieldWorkService
from app.utils.enums import ReportType

router = APIRouter(prefix="/field-work", tags=["Field Work"])


@router.post(
    "/repeater-site-visits/start",
    response_model=ReportResponse,
    status_code=201,
)
def start_repeater_site_visit(
    payload: FieldWorkCreate,
    service: FieldWorkService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportResponse:
    return service.start(payload, ReportType.REPEATER, session, current_user)


@router.post(
    "/repeater-site-visits",
    response_model=FieldWorkResponse,
    status_code=201,
)
def submit_repeater_site_visit(
    payload: FieldWorkCreate,
    service: FieldWorkService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FieldWorkResponse:
    return service.submit(payload, ReportType.REPEATER, session, current_user)


@router.post(
    "/generator-refuels/start",
    response_model=ReportResponse,
    status_code=201,
)
def start_generator_refuel(
    payload: FieldWorkCreate,
    service: FieldWorkService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportResponse:
    return service.start(payload, ReportType.DIESEL, session, current_user)


@router.post(
    "/generator-refuels",
    response_model=FieldWorkResponse,
    status_code=201,
)
def submit_generator_refuel(
    payload: FieldWorkCreate,
    service: FieldWorkService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FieldWorkResponse:
    return service.submit(payload, ReportType.DIESEL, session, current_user)
