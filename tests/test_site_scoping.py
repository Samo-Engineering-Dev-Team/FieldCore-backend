"""Technician site scoping — GET /sites must never leak unassigned sites.

The load-bearing rule under test: an *empty* restriction list means "nothing is
visible", not "no restriction". Treating empty as unrestricted would silently
hand every site to any technician with no assignments, which is the exact bug
this feature exists to prevent.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models.auth import TokenData
from app.services.authorization import site_scope_for_user
from app.services.site import _SiteService
from app.utils.enums import UserRole


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

    def exec(self, statement):
        return self.results.pop(0)


class ExplodingSession:
    """Fails the test if the query layer is reached at all."""

    def exec(self, statement):  # pragma: no cover - reaching this is the failure
        raise AssertionError("session should not be queried")


def _token(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Test",
        surname="User",
        must_change_password=False,
    )


# --- site_scope_for_user -------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.MANAGER,
        UserRole.NOC,
        UserRole.PARTNER,
        UserRole.SHEQ,
    ],
)
def test_non_technician_roles_are_unrestricted(role):
    """None means unrestricted. Regression guard: scoping must not leak to NOC."""
    assert site_scope_for_user(_token(role), ExplodingSession()) is None


def test_technician_is_scoped_to_assigned_sites():
    technician = SimpleNamespace(id=uuid4())
    site_ids = [uuid4(), uuid4()]
    session = SequencedSession(
        QueryResult(first=technician),
        QueryResult(all=site_ids),
        QueryResult(all=[]),  # no coverage this week
    )

    assert site_scope_for_user(_token(UserRole.TECHNICIAN), session) == site_ids


def test_technician_with_no_assignments_is_scoped_to_nothing():
    session = SequencedSession(
        QueryResult(first=SimpleNamespace(id=uuid4())),
        QueryResult(all=[]),
        QueryResult(all=[]),
    )

    # Empty list, NOT None — None would mean unrestricted.
    assert site_scope_for_user(_token(UserRole.TECHNICIAN), session) == []


def test_covered_sites_are_added_to_assigned_sites():
    """Coverage is a management-granted, week-bounded loan of another tech's site."""
    assigned = [uuid4()]
    covered = [uuid4()]
    session = SequencedSession(
        QueryResult(first=SimpleNamespace(id=uuid4())),
        QueryResult(all=assigned),
        QueryResult(all=covered),
    )

    scope = site_scope_for_user(_token(UserRole.TECHNICIAN), session)
    assert scope == [*assigned, *covered]


def test_coverage_only_technician_sees_the_covered_site():
    """No standing assignment, but covering this week — must not be locked out."""
    covered = [uuid4()]
    session = SequencedSession(
        QueryResult(first=SimpleNamespace(id=uuid4())),
        QueryResult(all=[]),
        QueryResult(all=covered),
    )

    assert site_scope_for_user(_token(UserRole.TECHNICIAN), session) == covered


def test_site_covered_and_assigned_is_not_duplicated():
    shared = uuid4()
    session = SequencedSession(
        QueryResult(first=SimpleNamespace(id=uuid4())),
        QueryResult(all=[shared]),
        QueryResult(all=[shared]),
    )

    assert site_scope_for_user(_token(UserRole.TECHNICIAN), session) == [shared]


def test_technician_without_profile_is_scoped_to_nothing():
    """Broken data (technician role, no technician row) must fail closed."""
    session = SequencedSession(QueryResult(first=None))

    assert site_scope_for_user(_token(UserRole.TECHNICIAN), session) == []


# --- service enforcement -------------------------------------------------


def test_read_sites_returns_nothing_for_empty_restriction():
    assert _SiteService().read_sites(ExplodingSession(), restrict_to_site_ids=[]) == []


def test_search_sites_returns_nothing_for_empty_restriction():
    result = _SiteService().search_sites(
        "durban", ExplodingSession(), restrict_to_site_ids=[]
    )
    assert result == []


def test_read_site_rejects_site_outside_restriction():
    service = _SiteService()
    with pytest.raises(ForbiddenException):
        service.read_site(
            uuid4(), ExplodingSession(), restrict_to_site_ids=[uuid4()]
        )


def test_read_site_rejects_every_site_when_restriction_is_empty():
    service = _SiteService()
    with pytest.raises(ForbiddenException):
        service.read_site(uuid4(), ExplodingSession(), restrict_to_site_ids=[])
