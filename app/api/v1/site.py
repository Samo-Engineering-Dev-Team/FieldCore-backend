from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import SiteCreate, SiteUpdate, SiteResponse, SiteSearchResult
from app.services import SiteService
from app.database import SessionDep
from app.utils.enums import Region

router = APIRouter(prefix="/sites", tags=["Sites"])


@router.post("/", response_model=SiteResponse, status_code=201)
def create_site(
    payload: SiteCreate, service: SiteService, session: SessionDep
) -> SiteResponse:
    """"""
    return service.create_site(payload, session)


@router.get("/", response_model=List[SiteResponse], status_code=200)
def read_sites(
    service: SiteService,
    session: SessionDep,
    region: Region | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[SiteResponse]:
    """"""
    return service.read_sites(session, region, offset, limit)


@router.get("/search", response_model=List[SiteSearchResult], status_code=200)
def search_sites(
    service: SiteService,
    session: SessionDep,
    q: str = Query(..., min_length=2, max_length=100),
    region: Region | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> List[SiteSearchResult]:
    """"""
    return service.search_sites(q, session, region, offset, limit)


@router.get("/{site_id}", response_model=SiteResponse, status_code=200)
def read_site(site_id: UUID, service: SiteService, session: SessionDep) -> SiteResponse:
    """"""
    return service.read_site(site_id, session)


@router.patch("/{site_id}", response_model=SiteResponse, status_code=200)
def update_site(
    site_id: UUID,
    payload: SiteUpdate,
    service: SiteService,
    session: SessionDep,
) -> SiteResponse:
    """"""
    return service.update_site(site_id, payload, session)


@router.delete("/{site_id}", status_code=204)
def delete_site(site_id: UUID, service: SiteService, session: SessionDep) -> None:
    """"""
    service.delete_site(site_id, session)
