import pytest
from types import SimpleNamespace


@pytest.mark.asyncio
async def test_ensure_schema_registers_document_artifact_table(monkeypatch):
    from odbms import DBMS

    from cognitrix import config
    from cognitrix.artifacts import DocumentArtifact
    from cognitrix.session_ownership import SessionOwnership

    created = []

    async def create_document_table():
        created.append('documents')

    monkeypatch.setattr(
        DocumentArtifact,
        '_create_table_async',
        staticmethod(create_document_table),
        raising=False,
    )
    monkeypatch.setattr(
        SessionOwnership,
        '_create_table_async',
        staticmethod(lambda: _async(None)),
        raising=False,
    )
    monkeypatch.setattr(DBMS, 'Database', SimpleNamespace(dbms='mongodb'))

    await config._ensure_schema()

    assert created == ['documents']


def test_cli_database_startup_registers_critical_artifact_tables():
    import inspect

    from cognitrix.cli import core

    source = inspect.getsource(core.run_configuration)
    assert 'DocumentArtifact' in source
    assert 'SessionOwnership' in source


@pytest.mark.asyncio
async def test_ensure_schema_fails_startup_when_document_journal_is_unavailable(
    monkeypatch,
):
    from odbms import DBMS

    from cognitrix import config
    from cognitrix.artifacts import DocumentArtifact
    from cognitrix.session_ownership import SessionOwnership

    async def fail_document_table():
        raise RuntimeError('document journal unavailable')

    monkeypatch.setattr(
        SessionOwnership,
        '_create_table_async',
        staticmethod(lambda: _async(None)),
        raising=False,
    )
    monkeypatch.setattr(
        DocumentArtifact,
        '_create_table_async',
        staticmethod(fail_document_table),
        raising=False,
    )
    monkeypatch.setattr(DBMS, 'Database', SimpleNamespace(dbms='mongodb'))

    with pytest.raises(RuntimeError, match='document journal unavailable'):
        await config._ensure_schema()


@pytest.mark.asyncio
async def test_ensure_schema_adds_artifact_metadata_columns_without_destructive_sql(monkeypatch):
    from odbms import DBMS

    from cognitrix import config
    from cognitrix.artifacts import DocumentArtifact
    from cognitrix.session_ownership import SessionOwnership

    statements = []

    class Cursor:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def fetchall(self):
            return self.rows

    table_columns = [
                (0, 'id'),
                (1, 'session_id'),
                (2, 'storage_key'),
                (3, 'mime_type'),
                (4, 'user_id'),
    ]

    async def query(statement):
        statements.append(statement)
        if statement.startswith('PRAGMA table_info('):
            return Cursor(table_columns)
        if statement == 'PRAGMA index_info(ux_task_run_events_run_sequence)':
            return Cursor([(0, 0, 'run_id'), (1, 0, 'sequence')])
        if statement.startswith('PRAGMA index_list('):
            table = statement.removeprefix('PRAGMA index_list(').removesuffix(')')
            rows = [
                (index, name, int(unique))
                for index, (name, index_table, _columns, unique)
                in enumerate(config._TASKRUN_INDEXES)
                if index_table == table
            ]
            return Cursor(rows)
        return Cursor()

    monkeypatch.setattr(DBMS, 'Database', SimpleNamespace(dbms='sqlite', query=None))
    monkeypatch.setattr(DBMS.Database, 'query', query)
    monkeypatch.setattr(
        SessionOwnership,
        '_create_table_async',
        staticmethod(lambda: _async(None)),
        raising=False,
    )
    monkeypatch.setattr(
        DocumentArtifact,
        '_create_table_async',
        staticmethod(lambda: _async(None)),
        raising=False,
    )

    await config._ensure_schema()

    artifact_alters = [
        statement
        for statement in statements
        if statement.startswith('ALTER TABLE artifacts ADD COLUMN')
    ]
    assert artifact_alters == [
        'ALTER TABLE artifacts ADD COLUMN run_id TEXT',
        'ALTER TABLE artifacts ADD COLUMN origin TEXT',
        'ALTER TABLE artifacts ADD COLUMN vision_storage_key TEXT',
        'ALTER TABLE artifacts ADD COLUMN thumbnail_storage_key TEXT',
        'ALTER TABLE artifacts ADD COLUMN created_at TEXT',
    ]
    assert not any(
        destructive in statement.upper()
        for statement in statements
        if 'ARTIFACTS' in statement.upper()
        for destructive in ('DROP TABLE', 'RENAME TABLE', 'DELETE FROM')
    )


@pytest.mark.asyncio
async def test_document_schema_creates_unique_storage_and_recovery_indexes(tmp_path):
    from odbms import DBMS

    from cognitrix.artifacts import DocumentArtifact
    from cognitrix.config import _patch_odbms_sqlite

    database = str(tmp_path / 'document-schema.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=database)
    else:
        DBMS.initialize('sqlite', database=database)
    _patch_odbms_sqlite()
    await DocumentArtifact.create_table()

    table = DocumentArtifact.table_name()
    cursor = await DBMS.Database.query(f'PRAGMA index_list({table})')
    indexes = {row[1]: row for row in cursor.fetchall()}

    assert indexes[f'ux_{table}_storage_key'][2] == 1
    assert f'ix_{table}_owner' in indexes
    assert f'ix_{table}_reconcile' in indexes
    assert f'ix_{table}_promotion_token' in indexes


@pytest.mark.asyncio
async def test_document_schema_creates_mongodb_indexes(monkeypatch):
    from odbms import DBMS, Model

    from cognitrix.artifacts import DocumentArtifact

    created = []

    class Collection:
        async def create_index(self, keys, **kwargs):
            created.append((keys, kwargs))

    class MongoDatabase:
        def __getitem__(self, _table):
            return Collection()

    async def create_base_async(cls):
        created.append(('base', cls))

    def create_base(_cls):
        raise AssertionError('ODBMS sync scheduling wrapper must be bypassed')

    monkeypatch.setattr(
        Model,
        '_create_table_async',
        classmethod(create_base_async),
        raising=False,
    )
    monkeypatch.setattr(Model, 'create_table', classmethod(create_base))
    monkeypatch.setattr(
        DBMS,
        'Database',
        SimpleNamespace(dbms='mongodb', db=MongoDatabase()),
    )

    await DocumentArtifact.create_table()

    table = DocumentArtifact.table_name()
    assert created == [
        ('base', DocumentArtifact),
        ([('storage_key', 1)], {
            'name': f'ux_{table}_storage_key',
            'unique': True,
        }),
        ([('session_id', 1), ('user_id', 1), ('agent_id', 1)], {
            'name': f'ix_{table}_owner',
        }),
        ([('status', 1), ('expires_at', 1)], {
            'name': f'ix_{table}_reconcile',
        }),
        ([('promotion_token', 1)], {
            'name': f'ix_{table}_promotion_token',
        }),
    ]


@pytest.mark.asyncio
async def test_document_schema_creates_mysql_indexes_idempotently(monkeypatch):
    from odbms import DBMS, Model

    from cognitrix.artifacts import DocumentArtifact

    existing = set()
    creates = []

    class Cursor:
        found = False

        async def execute(self, statement, params=()):
            if statement.startswith('SELECT 1 FROM information_schema.statistics'):
                self.found = params[1] in existing
                return
            creates.append(statement)
            index_name = statement.split(' INDEX ', 1)[1].split(' ON ', 1)[0]
            existing.add(index_name)

        async def fetchone(self):
            return (1,) if self.found else None

    class Context:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    class Connection:
        def cursor(self):
            return Context(Cursor())

    class Pool:
        def acquire(self):
            return Context(Connection())

    async def create_base(_cls):
        return None

    monkeypatch.setattr(Model, 'create_table', classmethod(create_base))
    monkeypatch.setattr(
        DBMS,
        'Database',
        SimpleNamespace(dbms='mysql', _pool=Pool()),
    )

    await DocumentArtifact.create_table()
    await DocumentArtifact.create_table()

    table = DocumentArtifact.table_name()
    assert creates == [
        f'CREATE UNIQUE INDEX ux_{table}_storage_key '
        f'ON {table} (storage_key(191))',
        f'CREATE INDEX ix_{table}_owner '
        f'ON {table} (session_id(191), user_id(191), agent_id(191))',
        f'CREATE INDEX ix_{table}_reconcile '
        f'ON {table} (status(32), expires_at(64))',
        f'CREATE INDEX ix_{table}_promotion_token '
        f'ON {table} (promotion_token(64))',
    ]


@pytest.mark.asyncio
async def test_document_schema_rejects_an_unsupported_backend(monkeypatch):
    from odbms import DBMS, Model

    from cognitrix.artifacts import DocumentArtifact

    async def create_base(_cls):
        return None

    monkeypatch.setattr(Model, 'create_table', classmethod(create_base))
    monkeypatch.setattr(
        DBMS,
        'Database',
        SimpleNamespace(dbms='unknown'),
    )

    with pytest.raises(RuntimeError, match='Unsupported document metadata backend'):
        await DocumentArtifact.create_table()


async def _async(value):
    return value
