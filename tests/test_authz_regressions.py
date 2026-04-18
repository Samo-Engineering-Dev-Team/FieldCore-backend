from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.api.v1.user import read_user
from app.exceptions.http import ForbiddenException
from app.models.auth import TokenData
from app.services.access_request import _AccessRequestService
from app.services.incident import _IncidentService
from app.services.route_patrol import _RoutePatrolService
from app.services.task import _TaskService
from app.services.technician import _TechnicianService
from app.utils.enums import UserRole


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
