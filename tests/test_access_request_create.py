from uuid import uuid4

import pytest

from app.exceptions.http import BadRequestException
from app.models import AccessRequestCreate
from app.models.auth import TokenData
from app.services.access_request import _AccessRequestService
from app.utils.enums import UserRole
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


def test_access_request_creation_is_deprecated() -> None:
    service = _AccessRequestService()
    site_id = uuid4()

    with pytest.raises(BadRequestException, match="Access requests are deprecated"):
        service.create_access_request(
            make_payload(site_id),
            SequencedSession(),
            make_user(UserRole.TECHNICIAN),
        )


def test_management_create_access_request_requires_technician_id() -> None:
    service = _AccessRequestService()

    with pytest.raises(BadRequestException, match="Access requests are deprecated"):
        service.create_access_request(
            make_payload(uuid4()),
            SequencedSession(),
            make_user(UserRole.NOC),
        )
