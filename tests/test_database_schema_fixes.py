from unittest.mock import MagicMock

from app.database.database import Database


def make_inspector(
    table_columns: dict[str, list[str]],
    technician_indexes: list[dict[str, str]] | None = None,
) -> MagicMock:
    inspector = MagicMock()
    known_tables = set(table_columns)
    if technician_indexes is not None:
        known_tables.add("technicians")

    inspector.has_table.side_effect = lambda table: table in known_tables
    inspector.get_columns.side_effect = lambda table: [
        {"name": column} for column in table_columns.get(table, [])
    ]
    inspector.get_indexes.side_effect = (
        lambda table: technician_indexes if table == "technicians" else []
    )
    return inspector


def test_apply_schema_fixes_adds_missing_must_change_password_column(monkeypatch) -> None:
    inspector = make_inspector(
        {
            "users": ["id", "password_hash"],
            "access_requests": ["id", "report_type"],
            "tasks": ["id", "report_type"],
        },
        [
            {"name": "uq_active_technicians_phone"},
            {"name": "uq_active_technicians_id_no"},
        ],
    )

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


def test_apply_schema_fixes_skips_existing_must_change_password_column(monkeypatch) -> None:
    inspector = make_inspector(
        {
            "users": ["must_change_password", "credentials_updated_at"],
            "access_requests": ["report_type"],
            "tasks": ["report_type"],
        },
        [
            {"name": "uq_active_technicians_phone"},
            {"name": "uq_active_technicians_id_no"},
        ],
    )

    engine = MagicMock()

    monkeypatch.setattr("app.database.database.inspect", lambda _: inspector)
    Database.connection = engine

    try:
        Database._apply_schema_fixes()
    finally:
        Database.connection = None

    engine.begin.assert_not_called()


def test_apply_schema_fixes_replaces_legacy_technician_unique_constraints(monkeypatch) -> None:
    inspector = make_inspector(
        {
            "users": ["must_change_password", "credentials_updated_at"],
            "access_requests": ["report_type"],
            "tasks": ["report_type"],
        },
        [
            {"name": "technicians_phone_key"},
            {"name": "technicians_id_no_key"},
        ],
    )

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

    executed_sql = "\n".join(
        str(call.args[0]) for call in begin_connection.execute.call_args_list
    )
    assert "DROP CONSTRAINT IF EXISTS technicians_phone_key" in executed_sql
    assert "DROP CONSTRAINT IF EXISTS technicians_id_no_key" in executed_sql
    assert "uq_active_technicians_phone" in executed_sql
    assert "uq_active_technicians_id_no" in executed_sql


def test_apply_schema_fixes_adds_missing_report_type_columns(monkeypatch) -> None:
    inspector = make_inspector(
        {
            "users": ["must_change_password", "credentials_updated_at"],
            "access_requests": ["id"],
            "tasks": ["id"],
        },
        [
            {"name": "uq_active_technicians_phone"},
            {"name": "uq_active_technicians_id_no"},
        ],
    )

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

    executed_sql = "\n".join(
        str(call.args[0]) for call in begin_connection.execute.call_args_list
    )
    assert "ALTER TABLE access_requests" in executed_sql
    assert "ALTER TABLE tasks" in executed_sql
    assert "report_type" in executed_sql
