"""Form submission service: snapshot, validation surfacing, scoping."""

from uuid import uuid4

import pytest

from app.exceptions.http import (
    FormValidationException,
    ForbiddenException,
    BadRequestException,
)
from app.models import (
    FormTemplate,
    FormSubmission,
    FormSubmissionCreate,
    TemplateCategory,
)
from app.models.auth import TokenData
from app.models.form_template import (
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
)
from app.services.form_submission import _FormSubmissionService
from app.utils.enums import FieldType, UserRole, LinkTarget


class FakeSession:
    def __init__(self, first=None, results=None, get_obj=None):
        self._first = first
        self._all = results or []
        # get_obj: either a single object or a dict keyed by pk for session.get().
        self._get_obj = get_obj
        self.added = []
        self.committed = False

    def exec(self, statement):
        return self

    def get(self, model, pk):
        if isinstance(self._get_obj, dict):
            return self._get_obj.get(pk)
        return self._get_obj

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


def _category(requires_link=LinkTarget.NONE):
    return TemplateCategory(code="C", name="C", requires_link=requires_link, is_system=False)


def _template(version=2, category=None):
    category = category or _category()
    return FormTemplate(category_id=category.id, key="k1", name="T", version=version,
                        is_active=True, structure=_structure().model_dump())


def test_create_submission_snapshots_structure_and_version():
    category = _category(LinkTarget.NONE)
    template = _template(version=2, category=category)
    service = _FormSubmissionService()
    session = FakeSession(first=template, get_obj=category)
    user = _user(UserRole.TECHNICIAN)

    payload = FormSubmissionCreate(template_id=uuid4(), values={"name": "Ada", "age": "30"})
    resp = service.create_submission(uuid4(), payload, session, user)

    assert resp.template_version == 2
    assert resp.template_snapshot == template.structure
    assert resp.values == {"name": "Ada", "age": 30.0}
    assert resp.submitted_by == user.user_id


def test_create_submission_validation_failure_surfaces():
    category = _category(LinkTarget.NONE)
    template = _template(category=category)
    service = _FormSubmissionService()
    session = FakeSession(first=template, get_obj=category)
    # missing required "name"
    payload = FormSubmissionCreate(template_id=uuid4(), values={"age": 10})
    with pytest.raises(FormValidationException) as exc:
        service.create_submission(uuid4(), payload, session, _user(UserRole.TECHNICIAN))
    assert "name" in exc.value.errors


def test_create_submission_requires_task_link():
    """A category with requires_link=TASK rejects a submission missing task_id."""
    category = _category(LinkTarget.TASK)
    template = _template(category=category)
    service = _FormSubmissionService()
    session = FakeSession(first=template, get_obj=category)
    payload = FormSubmissionCreate(template_id=uuid4(), values={"name": "Ada"})
    with pytest.raises(BadRequestException):
        service.create_submission(uuid4(), payload, session, _user(UserRole.TECHNICIAN))


def test_create_submission_none_category_rejects_link():
    """A NONE-link category rejects a stray task_id/incident_id."""
    category = _category(LinkTarget.NONE)
    template = _template(category=category)
    service = _FormSubmissionService()
    session = FakeSession(first=template, get_obj=category)
    payload = FormSubmissionCreate(
        template_id=uuid4(), task_id=uuid4(), values={"name": "Ada"}
    )
    with pytest.raises(BadRequestException):
        service.create_submission(uuid4(), payload, session, _user(UserRole.TECHNICIAN))


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
