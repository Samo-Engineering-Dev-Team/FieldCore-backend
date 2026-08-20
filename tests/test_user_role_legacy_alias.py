from app.models import UserResponse
from app.models.user import User
from app.utils.enums import UserRole


def test_super_admin_db_role_maps_to_super_admin() -> None:
    role_type = User.__table__.c.role.type

    assert role_type._object_value_for_elem("SUPER_ADMIN") is UserRole.SUPER_ADMIN


def test_super_admin_serializes_as_super_admin() -> None:
    response = UserResponse.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000000",
            "created_at": "2026-04-24T00:00:00Z",
            "updated_at": "2026-04-24T00:00:00Z",
            "deleted_at": None,
            "name": "Admin",
            "surname": "User",
            "email": "admin@example.com",
            "role": UserRole.SUPER_ADMIN,
            "status": "active",
        }
    )

    assert response.model_dump(mode="json")["role"] == "super_admin"
