"""Form submission service: snapshot, validation surfacing, scoping."""

from uuid import uuid4

import pytest

from app.exceptions.http import FormValidationException, ForbiddenException
from app.models import FormTemplate, FormSubmission, FormSubmissionCreate
from app.models.auth import TokenData
from app.models.form_template import (
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
)
from app.services.form_submission import _FormSubmissionService
from app.utils.enums import FieldType, UserRole


class FakeSession:
    def __init__(self, first=None, results=None):
        self._first = first
        self._all = results or []
        self.added = []
        self.committed = False

    def exec(self, statement):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _user(role: UserRole, user_id=None) -> TokenData:
    return TokenData(user_id=user_id or uuid4(), role=role, name="T", surname="U", token_type="access")


def _structure():
    return TemplateStructure(sections=[
        SectionDefinition(title="A", order=0, fields=[
            FieldDefinition(key="name", label="Name", type=FieldType.STRING, order=0, required=True),
            FieldDefinition(key="age", label="Age", type=FieldType.NUMBER, order=1),
        ]),
    ])


def _template(version=2):
    return FormTemplate(key="k1", name="T", version=version, is_active=True,
                        structure=_structure().model_dump())


def test_create_submission_snapshots_structure_and_version():
    template = _template(version=2)
    service = _FormSubmissionService()
    session = FakeSession(first=template)
    user = _user(UserRole.TECHNICIAN)

    payload = FormSubmissionCreate(template_id=uuid4(), values={"name": "Ada", "age": "30"})
    resp = service.create_submission(uuid4(), payload, session, user)

    assert resp.template_version == 2
    assert resp.template_snapshot == template.structure
    assert resp.values == {"name": "Ada", "age": 30.0}
    assert resp.submitted_by == user.user_id


def test_create_submission_validation_failure_surfaces():
    template = _template()
    service = _FormSubmissionService()
    session = FakeSession(first=template)
    # missing required "name"
    payload = FormSubmissionCreate(template_id=uuid4(), values={"age": 10})
    with pytest.raises(FormValidationException) as exc:
        service.create_submission(uuid4(), payload, session, _user(UserRole.TECHNICIAN))
    assert "name" in exc.value.errors


def test_read_submission_technician_scoped_to_own():
    owner_id = uuid4()
    template_id = uuid4()
    submission = FormSubmission(
        template_id=template_id, template_version=1, template_snapshot={},
        values={}, submitted_by=owner_id,
    )
    service = _FormSubmissionService()
    session = FakeSession(first=submission)

    # Different technician cannot access.
    with pytest.raises(ForbiddenException):
        service.read_submission(template_id, uuid4(), session, _user(UserRole.TECHNICIAN))

    # Owner can.
    resp = service.read_submission(
        template_id, uuid4(), session, _user(UserRole.TECHNICIAN, user_id=owner_id)
    )
    assert resp.submitted_by == owner_id


def test_read_submission_management_sees_any():
    submission = FormSubmission(
        template_id=uuid4(), template_version=1, template_snapshot={},
        values={}, submitted_by=uuid4(),
    )
    service = _FormSubmissionService()
    session = FakeSession(first=submission)
    resp = service.read_submission(
        submission.template_id, uuid4(), session, _user(UserRole.NOC)
    )
    assert resp.id == submission.id
