from uuid import uuid4

import pytest

from app.exceptions.http import BadRequestException
from app.models import AccessRequestCreate, Site, Technician, User
from app.models.auth import TokenData
from app.services.access_request import _AccessRequestService
from app.utils.enums import Region, UserRole
from app.utils.funcs import utcnow


class QueryResult:
    def __init__(self, *, first=None, all=None) -> None:
        self._first = first
        self._all = [] if all is None else all

    def first(self):
        return self._first

    def all(self):
        return self._all


class SequencedSession:
    def __init__(self, *results: QueryResult) -> None:
        self.results = list(results)
        self.added = []
        self.commits = 0
        self.rolled_back = False

    def exec(self, statement):
        return self.results.pop(0)

    def add(self, item) -> None:
        self.added.append(item)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, item) -> None:
        return None

    def rollback(self) -> None:
        self.rolled_back = True


def make_user(role: UserRole, *, user_id=None) -> TokenData:
    return TokenData(
        user_id=user_id or uuid4(),
        role=role,
        name="Test",
        surname="User",
        token_type="access",
    )


def make_payload(site_id) -> AccessRequestCreate:
    now = utcnow()
    return AccessRequestCreate(
        site_id=site_id,
        description="Routine maintenance access",
        start_time=now,
        end_time=now,
        report_type="routine-drive",
    )


def test_technician_can_create_access_request_without_payload_technician_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AccessRequestService()
    site_id = uuid4()
    user_id = uuid4()
    technician_id = uuid4()

    user = User(
        id=user_id,
        name="Tech",
        surname="User",
        email="tech@example.com",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
    )
    technician = Technician(
        id=technician_id,
        phone="0123456789",
        id_no="12345",
        user_id=user_id,
    )
    technician.user = user
    site = Site(id=site_id, name="Test Site", region=Region.GAUTENG)
    session = SequencedSession(
        QueryResult(first=site),
        QueryResult(first=technician),
        QueryResult(all=[]),
    )

    monkeypatch.setattr(
        "app.services.access_request.get_technician_id_for_user",
        lambda user_id, session: technician_id,
    )

    response = service.create_access_request(
        make_payload(site_id),
        session,
        make_user(UserRole.TECHNICIAN, user_id=user_id),
    )

    assert session.added[0].technician_id == technician_id
    assert response.technician_name == "Tech User"
    assert response.site_name == "Test Site"


def test_management_create_access_request_requires_technician_id() -> None:
    service = _AccessRequestService()

    with pytest.raises(BadRequestException, match="technician_id is required"):
        service.create_access_request(
            make_payload(uuid4()),
            SequencedSession(),
            make_user(UserRole.NOC),
        )
