"""Form template service: CRUD, version bump, management-only auth."""

from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models import (
    FormTemplate,
    FormTemplateCreate,
    FormTemplateUpdate,
    TemplateCategory,
)
from app.models.auth import TokenData
from app.models.form_template import (
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
)
from app.services.form_template import _FormTemplateService
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


def _user(role: UserRole) -> TokenData:
    return TokenData(user_id=uuid4(), role=role, name="T", surname="U", token_type="access")


def _structure():
    return TemplateStructure(sections=[
        SectionDefinition(title="A", order=0, fields=[
            FieldDefinition(key="name", label="Name", type=FieldType.STRING, order=0),
        ]),
    ])


def _category():
    return TemplateCategory(code="TASK", name="Task", is_active=True, is_system=True)


def _create_payload(category_id=None):
    return FormTemplateCreate(
        category_id=category_id or uuid4(), key="k1", name="Template 1", structure=_structure()
    )


def test_create_template_management_allowed():
    service = _FormTemplateService()
    category = _category()
    # create_template looks the category up first (exec().first()).
    session = FakeSession(first=category)
    resp = service.create_template(
        _create_payload(category.id), session, _user(UserRole.MANAGER)
    )
    assert resp.key == "k1"
    assert resp.version == 1
    assert resp.category_id == category.id
    assert session.committed


def test_create_template_technician_forbidden():
    service = _FormTemplateService()
    with pytest.raises(ForbiddenException):
        service.create_template(_create_payload(), FakeSession(), _user(UserRole.TECHNICIAN))


def test_update_structure_bumps_version():
    existing = FormTemplate(category_id=uuid4(), key="k1", name="T", version=1, structure=_structure().model_dump())
    service = _FormTemplateService()
    session = FakeSession(first=existing)

    new_structure = TemplateStructure(sections=[
        SectionDefinition(title="A", order=0, fields=[
            FieldDefinition(key="name", label="Name", type=FieldType.STRING, order=0),
            FieldDefinition(key="extra", label="Extra", type=FieldType.NUMBER, order=1),
        ]),
    ])
    payload = FormTemplateUpdate(structure=new_structure)
    resp = service.update_template(uuid4(), payload, session, _user(UserRole.ADMIN))
    assert resp.version == 2


def test_update_without_structure_keeps_version():
    existing = FormTemplate(category_id=uuid4(), key="k1", name="T", version=3, structure=_structure().model_dump())
    service = _FormTemplateService()
    session = FakeSession(first=existing)
    resp = service.update_template(
        uuid4(), FormTemplateUpdate(name="Renamed"), session, _user(UserRole.ADMIN)
    )
    assert resp.version == 3
    assert resp.name == "Renamed"


def test_delete_template_soft_deletes():
    existing = FormTemplate(category_id=uuid4(), key="k1", name="T", version=1, structure=_structure().model_dump())
    service = _FormTemplateService()
    session = FakeSession(first=existing)
    service.delete_template(uuid4(), session, _user(UserRole.ADMIN))
    assert existing.deleted_at is not None


def test_delete_template_technician_forbidden():
    service = _FormTemplateService()
    with pytest.raises(ForbiddenException):
        service.delete_template(uuid4(), FakeSession(), _user(UserRole.TECHNICIAN))
