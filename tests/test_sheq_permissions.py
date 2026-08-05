from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.exceptions.http import ConflictException, ForbiddenException
from app.models.auth import TokenData
from app.models.sheq_submission import SheqSignatureCreate, SheqSubmissionUpdate
from app.services.sheq_submission import _SheqSubmissionService
from app.utils.enums import SheqChecklistType, SheqStatus, UserRole


def _make_user(role: UserRole) -> TokenData:
    return TokenData(user_id=uuid4(), role=role, name="Test", surname="User", token_type="access")


def _make_submission(technician_id, status=SheqStatus.SUBMITTED, checklist_type=SheqChecklistType.VEHICLE_DAILY):
    now = datetime.now(timezone.utc)
    submission = SimpleNamespace(
        id=uuid4(),
        checklist_type=checklist_type,
        status=status,
        technician_id=technician_id,
        task_id=None,
        site_id=None,
        data={},
        attachments=None,
        signatures=[],
        summary=None,
        performed_on=date(2026, 8, 5),
        created_at=now,
        updated_at=now,
        deleted_at=None,
        submitted_at=now,
        signed_off_at=None,
        supervisor_user_id=None,
        technician=SimpleNamespace(user=SimpleNamespace(name="Test", surname="Technician")),
        touch=MagicMock(),
        sign_off=MagicMock(),
    )
    submission.model_dump = lambda: {
        "id": submission.id,
        "checklist_type": submission.checklist_type,
        "status": submission.status,
        "performed_on": submission.performed_on,
        "technician_id": submission.technician_id,
        "task_id": submission.task_id,
        "site_id": submission.site_id,
        "data": submission.data,
        "attachments": submission.attachments,
        "summary": submission.summary,
        "signatures": submission.signatures,
        "created_at": submission.created_at,
        "updated_at": submission.updated_at,
        "deleted_at": submission.deleted_at,
        "submitted_at": submission.submitted_at,
        "signed_off_at": submission.signed_off_at,
        "supervisor_user_id": submission.supervisor_user_id,
    }
    return submission


# ── read scoping ─────────────────────────────────────────────────────────────


def test_technician_cannot_read_another_technicians_submission():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()
    submission = _make_submission(technician_id=uuid4())

    service._get_technician_by_user = MagicMock(return_value=SimpleNamespace(id=own_technician_id))
    service._get_submission = MagicMock(return_value=submission)

    with pytest.raises(ForbiddenException, match="view their own"):
        service.read_submission(submission.id, session, current_user)


def test_technician_can_read_own_submission():
    service = _SheqSubmissionService()
    session = MagicMock()
    own_technician_id = uuid4()
    current_user = _make_user(UserRole.TECHNICIAN)
    submission = _make_submission(technician_id=own_technician_id)
    submission.technician = None

    service._get_technician_by_user = MagicMock(return_value=SimpleNamespace(id=own_technician_id))
    service._get_submission = MagicMock(return_value=submission)

    response = service.read_submission(submission.id, session, current_user)
    assert response.id == submission.id


def test_sheq_officer_can_read_any_submission():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.SHEQ)
    submission = _make_submission(technician_id=uuid4())
    submission.technician = None

    service._get_submission = MagicMock(return_value=submission)

    response = service.read_submission(submission.id, session, current_user)
    assert response.id == submission.id


def test_partner_cannot_read_sheq_submissions():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)
    submission = _make_submission(technician_id=uuid4())

    service._get_submission = MagicMock(return_value=submission)

    with pytest.raises(ForbiddenException):
        service.read_submission(submission.id, session, current_user)


# ── write scoping ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [UserRole.SHEQ, UserRole.NOC, UserRole.PARTNER])
def test_non_management_non_owner_cannot_update(role):
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(role)
    submission = _make_submission(technician_id=uuid4())

    service._get_submission = MagicMock(return_value=submission)
    if role == UserRole.NOC:
        # NOC has no technician profile of its own here — still not management.
        service._get_technician_by_user = MagicMock(side_effect=Exception("not a technician"))

    with pytest.raises(ForbiddenException):
        service.update_submission(submission.id, SheqSubmissionUpdate(), session, current_user)


def test_manager_can_update_any_technicians_submission():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.MANAGER)
    submission = _make_submission(technician_id=uuid4())
    submission.technician = None

    service._get_submission = MagicMock(return_value=submission)
    service._to_response = MagicMock(return_value=SimpleNamespace(id=submission.id))

    response = service.update_submission(submission.id, SheqSubmissionUpdate(), session, current_user)
    assert response.id == submission.id


# ── immutability after sign-off ──────────────────────────────────────────────


def test_patch_on_signed_off_submission_returns_409():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.MANAGER)
    submission = _make_submission(technician_id=uuid4(), status=SheqStatus.SIGNED_OFF)

    service._get_submission = MagicMock(return_value=submission)

    with pytest.raises(ConflictException):
        service.update_submission(
            submission.id, SheqSubmissionUpdate(data={"x": 1}), session, current_user
        )


def test_delete_on_signed_off_submission_returns_409():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.MANAGER)
    submission = _make_submission(technician_id=uuid4(), status=SheqStatus.SIGNED_OFF)

    service._get_submission = MagicMock(return_value=submission)

    with pytest.raises(ConflictException):
        service.delete_submission(submission.id, session, current_user)


def test_signature_on_signed_off_submission_returns_409():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.MANAGER)
    submission = _make_submission(technician_id=uuid4(), status=SheqStatus.SIGNED_OFF)

    service._get_submission = MagicMock(return_value=submission)
    payload = SheqSignatureCreate(role="supervisor", method="drawn", captured_at=datetime.now(timezone.utc))

    with pytest.raises(ConflictException):
        service.add_signature(submission.id, payload, session, current_user, None, None)


# ── signature authorization ──────────────────────────────────────────────────


def test_only_management_can_post_supervisor_signature():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.TECHNICIAN)
    submission = _make_submission(technician_id=uuid4())

    service._get_submission = MagicMock(return_value=submission)
    payload = SheqSignatureCreate(role="supervisor", method="drawn", captured_at=datetime.now(timezone.utc))

    with pytest.raises(ForbiddenException):
        service.add_signature(submission.id, payload, session, current_user, None, None)


def test_management_can_post_supervisor_signature_and_signs_off():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.MANAGER)
    submission = _make_submission(technician_id=uuid4())
    submission.technician = None

    service._get_submission = MagicMock(return_value=submission)
    payload = SheqSignatureCreate(role="supervisor", method="drawn", captured_at=datetime.now(timezone.utc))

    service.add_signature(submission.id, payload, session, current_user, "10.0.0.1", "ua")
    submission.sign_off.assert_called_once()


def test_technician_can_sign_their_own_submission_as_driver():
    service = _SheqSubmissionService()
    session = MagicMock()
    own_technician_id = uuid4()
    current_user = _make_user(UserRole.TECHNICIAN)
    submission = _make_submission(technician_id=own_technician_id)
    submission.technician = None

    service._get_technician_by_user = MagicMock(return_value=SimpleNamespace(id=own_technician_id))
    service._get_submission = MagicMock(return_value=submission)
    payload = SheqSignatureCreate(role="driver", method="drawn", captured_at=datetime.now(timezone.utc))

    service.add_signature(submission.id, payload, session, current_user, None, None)
    submission.sign_off.assert_not_called()


def test_technician_cannot_sign_another_technicians_submission():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.TECHNICIAN)
    submission = _make_submission(technician_id=uuid4())

    service._get_technician_by_user = MagicMock(return_value=SimpleNamespace(id=uuid4()))
    service._get_submission = MagicMock(return_value=submission)
    payload = SheqSignatureCreate(role="driver", method="drawn", captured_at=datetime.now(timezone.utc))

    with pytest.raises(ForbiddenException):
        service.add_signature(submission.id, payload, session, current_user, None, None)


# ── SHEQ officer read-but-not-write ──────────────────────────────────────────


@pytest.mark.parametrize("method_name,args", [
    ("update_submission", (SheqSubmissionUpdate(),)),
    ("delete_submission", ()),
])
def test_sheq_officer_cannot_mutate_submissions(method_name, args):
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.SHEQ)
    submission = _make_submission(technician_id=uuid4())

    service._get_submission = MagicMock(return_value=submission)
    method = getattr(service, method_name)

    with pytest.raises(ForbiddenException):
        method(submission.id, *args, session, current_user)


def test_sheq_officer_cannot_post_any_signature():
    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.SHEQ)
    submission = _make_submission(technician_id=uuid4())

    service._get_submission = MagicMock(return_value=submission)
    payload = SheqSignatureCreate(role="driver", method="drawn", captured_at=datetime.now(timezone.utc))

    with pytest.raises(ForbiddenException):
        service.add_signature(submission.id, payload, session, current_user, None, None)


def test_sheq_officer_cannot_create_submissions():
    from app.models.sheq_submission import SheqSubmissionCreate

    service = _SheqSubmissionService()
    session = MagicMock()
    current_user = _make_user(UserRole.SHEQ)
    payload = SheqSubmissionCreate(
        checklist_type=SheqChecklistType.VEHICLE_DAILY, performed_on=date(2026, 8, 5), status=SheqStatus.DRAFT
    )

    with pytest.raises(ForbiddenException):
        service.create_submission(payload, session, current_user)
