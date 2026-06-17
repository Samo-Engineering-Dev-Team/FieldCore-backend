from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.api.v1.user import read_user
from app.exceptions.http import BadRequestException, ForbiddenException
from app.models import Technician, TechnicianCreate, User
from app.models.auth import TokenData
from app.services.access_request import _AccessRequestService
from app.services.incident import _IncidentService
from app.services.route_patrol import _RoutePatrolService
from app.services.task import _TaskService
from app.services.technician import _TechnicianService
from app.services.user import _UserService
from app.utils.enums import UserRole, UserStatus
from app.utils.funcs import utcnow


class CapturingSession:
    def __init__(self) -> None:
        self.statement = None

    def exec(self, statement):
        self.statement = statement
        return self

    def all(self):
        return []

    def first(self):
        return None


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
        self.commits = 0
        self.rolled_back = False

    def exec(self, statement):
        return self.results.pop(0)

    def add(self, item) -> None:
        self.added = item

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


def test_task_read_tasks_scopes_technician_to_own_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _TaskService()
    session = CapturingSession()
    current_user = make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()

    monkeypatch.setattr(
        "app.services.task.get_technician_id_for_user",
        lambda user_id, session: own_technician_id,
    )

    service.read_tasks(session=session, current_user=current_user, technician_id=uuid4())

    compiled = session.statement.compile()

    assert own_technician_id in compiled.params.values()


def test_task_read_task_rejects_other_technician(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _TaskService()
    session = MagicMock()
    current_user = make_user(UserRole.TECHNICIAN)
    task = SimpleNamespace(id=uuid4(), technician_id=uuid4())

    monkeypatch.setattr(
        "app.services.task.get_technician_id_for_user",
        lambda user_id, session: uuid4(),
    )
    service._get_task = MagicMock(return_value=task)  # type: ignore[method-assign]

    with pytest.raises(ForbiddenException, match="view this task"):
        service.read_task(task.id, session, current_user)


def test_create_task_rejects_technician() -> None:
    service = _TaskService()

    with pytest.raises(ForbiddenException, match="create tasks"):
        service.create_task(SimpleNamespace(), MagicMock(), make_user(UserRole.TECHNICIAN))  # type: ignore[arg-type]


def test_incident_read_incidents_scopes_technician_to_own_incidents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IncidentService()
    session = CapturingSession()
    current_user = make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()

    monkeypatch.setattr(
        "app.services.incident.get_technician_id_for_user",
        lambda user_id, session: own_technician_id,
    )

    service.read_incidents(session=session, current_user=current_user, technician_id=uuid4())

    compiled = session.statement.compile()

    assert own_technician_id in compiled.params.values()


def test_access_request_read_access_request_rejects_other_technician(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AccessRequestService()
    session = MagicMock()
    current_user = make_user(UserRole.TECHNICIAN)
    access_request = SimpleNamespace(id=uuid4(), technician_id=uuid4())

    monkeypatch.setattr(
        "app.services.access_request.get_technician_id_for_user",
        lambda user_id, session: uuid4(),
    )
    service._get_access_request = MagicMock(return_value=access_request)  # type: ignore[method-assign]

    with pytest.raises(ForbiddenException, match="view this access request"):
        service.read_access_request(access_request.id, session, current_user)


def test_route_patrol_list_scopes_technician_to_own_patrols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RoutePatrolService()
    session = CapturingSession()
    current_user = make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()

    monkeypatch.setattr(
        "app.services.route_patrol.get_technician_id_for_user",
        lambda user_id, session: own_technician_id,
    )

    service.list_patrols(session=session, current_user=current_user, technician_id=uuid4())

    compiled = session.statement.compile()

    assert own_technician_id in compiled.params.values()


def test_read_user_rejects_non_management_user_accessing_other_user() -> None:
    current_user = make_user(UserRole.TECHNICIAN)
    service = MagicMock()

    with pytest.raises(ForbiddenException, match="view this user"):
        read_user(uuid4(), service, MagicMock(), current_user)


def test_technician_read_rejects_other_technician(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _TechnicianService()
    session = MagicMock()
    current_user = make_user(UserRole.TECHNICIAN)
    technician = SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        "app.services.technician.assert_technician_self_or_roles",
        MagicMock(side_effect=ForbiddenException("You do not have permission to view this technician.")),
    )
    service._get_technician = MagicMock(return_value=technician)  # type: ignore[method-assign]

    with pytest.raises(ForbiddenException, match="view this technician"):
        service.read_technician(uuid4(), session, current_user)


def test_technician_list_excludes_profiles_for_deleted_users() -> None:
    service = _TechnicianService()
    session = CapturingSession()

    service.read_technicians(session=session)

    compiled_sql = str(session.statement.compile())

    assert "JOIN users" in compiled_sql
    assert "users.deleted_at IS NULL" in compiled_sql


def test_create_technician_restores_deleted_matching_profile() -> None:
    service = _TechnicianService()
    active_user = User(
        id=uuid4(),
        name="Ishmael",
        surname="Mamuela",
        email="ishmael@samotelecoms.co.za",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
    )
    old_user = User(
        id=uuid4(),
        name="Ishmael",
        surname="Maumela",
        email="ishmael@samotelecoms.co.za",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
        deleted_at=utcnow(),
    )
    deleted_technician = Technician(
        id=uuid4(),
        phone="073 210 0882",
        id_no="9710025648085",
        user_id=old_user.id,
        deleted_at=utcnow(),
    )
    deleted_technician.user = old_user
    session = SequencedSession(
        QueryResult(first=active_user),
        QueryResult(all=[deleted_technician]),
    )

    response = service.create_technician(
        TechnicianCreate(
            user_id=active_user.id,
            phone="073 210 0882",
            id_no="9710025648085",
        ),
        session,
    )

    assert deleted_technician.user_id == active_user.id
    assert deleted_technician.deleted_at is None
    assert deleted_technician.is_available is True
    assert response.user_id == active_user.id
    assert active_user.status == UserStatus.ACTIVE


def test_create_technician_user_starts_disabled_until_profile_exists() -> None:
    from app.models import UserCreate

    service = _UserService()
    session = MagicMock()

    response = service.create_user(
        UserCreate(
            name="New",
            surname="Tech",
            email="newtech@example.com",
            role=UserRole.TECHNICIAN,
            password="Password1!",
        ),
        session,
    )

    created_user = session.add.call_args.args[0]

    assert created_user.status == UserStatus.DISABLED
    assert response.status == UserStatus.DISABLED


def test_activate_technician_user_requires_active_profile() -> None:
    service = _UserService()
    user = User(
        id=uuid4(),
        name="No",
        surname="Profile",
        email="noprofile@example.com",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
        status=UserStatus.DISABLED,
    )
    session = SequencedSession(QueryResult(first=user), QueryResult(first=None))

    with pytest.raises(BadRequestException, match="active technician profile"):
        service.activate_user(user.id, session)


def test_delete_technician_disables_linked_user() -> None:
    service = _TechnicianService()
    user = User(
        id=uuid4(),
        name="Delete",
        surname="Tech",
        email="deletetech@example.com",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
    )
    technician = Technician(
        id=uuid4(),
        phone="0123456789",
        id_no="1234567890",
        user_id=user.id,
    )
    technician.user = user
    service._get_technician = MagicMock(return_value=technician)  # type: ignore[method-assign]
    session = MagicMock()

    service.delete_technician(technician.id, session)

    assert technician.deleted_at is not None
    assert user.status == UserStatus.DISABLED
    session.commit.assert_called_once()


def test_technician_data_issues_reports_unlinked_users_and_invalid_profiles() -> None:
    service = _TechnicianService()
    unlinked_user = User(
        id=uuid4(),
        name="Missing",
        surname="Profile",
        email="missing@example.com",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
        status=UserStatus.DISABLED,
    )
    deleted_user = User(
        id=uuid4(),
        name="Deleted",
        surname="User",
        email="deleted@example.com",
        role=UserRole.TECHNICIAN,
        password_hash="hash",
        deleted_at=utcnow(),
    )
    invalid_profile = Technician(
        id=uuid4(),
        phone="0987654321",
        id_no="9876543210",
        user_id=deleted_user.id,
    )
    invalid_profile.user = deleted_user
    session = SequencedSession(
        QueryResult(all=[unlinked_user]),
        QueryResult(all=[invalid_profile]),
    )

    issues = service.read_data_issues(session)

    assert issues.total == 2
    assert issues.technician_users_without_profiles[0].user_id == unlinked_user.id
    assert (
        issues.profiles_without_active_technician_users[0].technician_id
        == invalid_profile.id
    )
