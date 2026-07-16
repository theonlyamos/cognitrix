import pytest
from types import SimpleNamespace


@pytest.mark.asyncio
async def test_ensure_schema_adds_artifact_metadata_columns_without_destructive_sql(monkeypatch):
    from odbms import DBMS

    from cognitrix import config

    statements = []

    class Cursor:
        def fetchall(self):
            return [
                (0, 'id'),
                (1, 'session_id'),
                (2, 'storage_key'),
                (3, 'mime_type'),
                (4, 'user_id'),
            ]

    async def query(statement):
        statements.append(statement)
        return Cursor()

    monkeypatch.setattr(DBMS, 'Database', SimpleNamespace(dbms='sqlite', query=None))
    monkeypatch.setattr(DBMS.Database, 'query', query)

    await config._ensure_schema()

    artifact_alters = [
        statement
        for statement in statements
        if statement.startswith('ALTER TABLE artifacts ADD COLUMN')
    ]
    assert artifact_alters == [
        'ALTER TABLE artifacts ADD COLUMN origin TEXT',
        'ALTER TABLE artifacts ADD COLUMN vision_storage_key TEXT',
        'ALTER TABLE artifacts ADD COLUMN thumbnail_storage_key TEXT',
        'ALTER TABLE artifacts ADD COLUMN created_at TEXT',
    ]
    assert not any(
        destructive in statement.upper()
        for statement in statements
        for destructive in ('DROP TABLE', 'RENAME TABLE', 'DELETE FROM')
    )
