from unittest.mock import MagicMock

from app.database.database import Database


def test_apply_schema_fixes_adds_missing_must_change_password_column(monkeypatch) -> None:
    inspector = MagicMock()
    inspector.has_table.return_value = True
    inspector.get_columns.return_value = [{"name": "id"}, {"name": "password_hash"}]

    begin_connection = MagicMock()
    begin_context = MagicMock()
    begin_context.__enter__.return_value = begin_connection
    begin_context.__exit__.return_value = False

    engine = MagicMock()
    engine.begin.return_value = begin_context

    monkeypatch.setattr("app.database.database.inspect", lambda _: inspector)
    Database.connection = engine

    try:
        Database._apply_schema_fixes()
    finally:
        Database.connection = None

    engine.begin.assert_called_once()
    executed_sql = "\n".join(
        str(call.args[0]) for call in begin_connection.execute.call_args_list
    )
    assert "ALTER TABLE users" in executed_sql
    assert "must_change_password" in executed_sql
    assert "credentials_updated_at" in executed_sql
    assert "tenant_id" in executed_sql


def test_apply_schema_fixes_skips_existing_must_change_password_column(monkeypatch) -> None:
    inspector = MagicMock()
    inspector.has_table.return_value = True
    inspector.get_columns.return_value = [
        {"name": "must_change_password"},
        {"name": "credentials_updated_at"},
        {"name": "tenant_id"},
    ]

    engine = MagicMock()

    monkeypatch.setattr("app.database.database.inspect", lambda _: inspector)
    Database.connection = engine

    try:
        Database._apply_schema_fixes()
    finally:
        Database.connection = None

    engine.begin.assert_not_called()
