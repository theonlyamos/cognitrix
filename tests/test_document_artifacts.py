import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
import uuid

import pytest

from cognitrix.artifacts import DocumentArtifact
from cognitrix.media import MediaOwnership, MediaValidationError
from cognitrix.media import documents
from cognitrix.media.documents import DocumentAssetService
from cognitrix.media.secure_fs import FileIdentity


class Upload:
    def __init__(self, name: str, data: bytes, mime: str = 'text/plain'):
        self.filename = name
        self.content_type = mime
        self._data = data
        self._sent = False

    async def read(self, _size: int = -1):
        if self._sent:
            return b''
        self._sent = True
        return self._data

    async def close(self):
        return None


@pytest.fixture
def document_rows(monkeypatch):
    rows: dict[str, DocumentArtifact] = {}

    async def save(row):
        rows[str(row.id)] = row
        return row

    async def get(document_id):
        return rows.get(str(document_id))

    async def find(query):
        return [
            row for row in rows.values()
            if all(getattr(row, key) == value for key, value in query.items())
        ]

    async def delete_many(query):
        doomed = [
            document_id for document_id, row in rows.items()
            if all(getattr(row, key) == value for key, value in query.items())
        ]
        for document_id in doomed:
            rows.pop(document_id)
        return len(doomed)

    async def update_one(query, values):
        for row in rows.values():
            if all(getattr(row, key) == value for key, value in query.items()):
                for key, value in values.items():
                    setattr(row, key, value)
                return 1
        return 0

    async def all_rows():
        return list(rows.values())

    async def reconciliation_candidates(
        _cls, *, expires_before: str, limit: int
    ):
        cutoff = datetime.fromisoformat(expires_before)
        due = []
        for row in rows.values():
            if row.status not in {'intent', 'pending', 'ready', 'reconciling'}:
                continue
            try:
                expiry = datetime.fromisoformat(str(row.expires_at))
            except (TypeError, ValueError):
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= cutoff:
                due.append(row)
        due.sort(key=lambda row: (str(row.expires_at), str(row.id)))
        return due[:limit]

    class Authority:
        async def require_active_owned(self, *_args):
            return type('Binding', (), {'generation': 0})()

        async def require_owned(self, *_args):
            return type('Binding', (), {'generation': 0})()

        async def reserve_intent(self, *_args, **_kwargs):
            return None

        async def adopt_reservation(self, *_args, **_kwargs):
            return None

        async def commit_reservation(self, *_args, **_kwargs):
            return None

        async def release_reservation(self, *_args, **_kwargs):
            return None

        async def release_document(self, *_args, **_kwargs):
            return None

        async def reconciliation_bindings(self, *, limit):
            return []

    monkeypatch.setattr(DocumentArtifact, 'save', save)
    monkeypatch.setattr(DocumentArtifact, 'get', get)
    monkeypatch.setattr(DocumentArtifact, 'find', find)
    monkeypatch.setattr(DocumentArtifact, 'delete_many', delete_many)
    monkeypatch.setattr(DocumentArtifact, 'update_one', update_one)
    monkeypatch.setattr(DocumentArtifact, 'all', all_rows)
    monkeypatch.setattr(
        DocumentArtifact,
        'reconciliation_candidates',
        classmethod(reconciliation_candidates),
    )
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())
    return rows


def _document(
    ownership: MediaOwnership,
    *,
    size_bytes: int = 4,
    origin: str = 'uploaded',
) -> DocumentArtifact:
    directory = f'{documents.ownership_directory_prefix(ownership)}{uuid.uuid4().hex}'
    return DocumentArtifact(
        id=str(uuid.uuid4()),
        session_id=ownership.session_id,
        user_id=ownership.user_id,
        agent_id=ownership.agent_id,
        storage_key=f'uploads/{directory}/f_{uuid.uuid4().hex}.txt',
        origin=origin,
        mime_type='text/plain',
        filename='notes.txt',
        size_bytes=size_bytes,
        sha256='a' * 64,
        tools_root_identity='v1:1:d:01',
        uploads_identity='v1:1:d:02',
        directory_identity='v1:1:d:03',
        file_identity='v1:1:f:04',
    )


async def _initialize_document_ownership_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.session_ownership import SessionOwnership

    db_file = str(tmp_path / 'document-ownership.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await SessionOwnership.create_table()


def test_document_namespace_binds_session_user_and_agent():
    base = MediaOwnership('session-1', 'user-1', 'agent-1')

    prefixes = {
        documents.ownership_directory_prefix(base),
        documents.ownership_directory_prefix(MediaOwnership('session-2', 'user-1', 'agent-1')),
        documents.ownership_directory_prefix(MediaOwnership('session-1', 'user-2', 'agent-1')),
        documents.ownership_directory_prefix(MediaOwnership('session-1', 'user-1', 'agent-2')),
    }

    assert len(prefixes) == 4
    assert documents.ownership_directory_prefix(base).startswith('d_')
    assert len(documents.ownership_directory_prefix(base)) == 67


def test_document_identity_encoding_round_trips_exact_capability():
    identity = FileIdentity(volume=123, file_id=b'\x00\x01\xfe', is_directory=True)

    encoded = documents.encode_identity(identity)

    assert documents.decode_identity(encoded, directory=True) == identity
    with pytest.raises(MediaValidationError):
        documents.decode_identity(encoded, directory=False)


def test_upload_filename_is_bounded_without_dropping_a_short_extension():
    from cognitrix.media import staging

    filename = staging._safe_filename(f'{"a" * 300}.txt')

    assert len(filename) == 255
    assert filename.endswith('.txt')


@pytest.mark.asyncio
async def test_document_intent_does_not_false_reject_from_process_local_rows(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    for _index in range(25):
        existing = _document(ownership)
        document_rows[str(existing.id)] = existing
    document = _document(ownership)
    document.promotion_token = uuid.uuid4().hex

    await DocumentAssetService().prepare_document_for_storage(document, ownership)

    assert document_rows[str(document.id)].status == 'pending'


@pytest.mark.asyncio
async def test_document_intent_rejects_a_path_from_another_owner(document_rows):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(MediaOwnership('session-2', 'user-1', 'agent-1'))
    document.session_id = ownership.session_id
    document.promotion_token = uuid.uuid4().hex

    with pytest.raises(MediaValidationError, match='ownership namespace'):
        await DocumentAssetService().prepare_document_for_storage(document, ownership)

    assert document_rows == {}


@pytest.mark.asyncio
async def test_document_intent_preserves_generated_origin(document_rows):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(ownership, origin='generated')
    document.promotion_token = uuid.uuid4().hex

    await DocumentAssetService().prepare_document_for_storage(document, ownership)

    assert document_rows[str(document.id)].origin == 'generated'


@pytest.mark.asyncio
async def test_promotion_commits_and_rolls_back_exact_document_row(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.media import staging

    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('notes.txt', b'notes')],
        user_key='user-1',
        stream_id='browser-1',
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')

    promoted = await staging.promote_staged_attachments(staged, ownership)

    assert len(document_rows) == 1
    document = next(iter(document_rows.values()))
    assert promoted.document_paths == [{
        'id': str(document.id),
        'name': 'notes.txt',
        'path': document.storage_key,
        'mime_type': 'text/plain',
        'origin': 'uploaded',
    }]
    assert document.storage_key.split('/')[1].startswith(
        documents.ownership_directory_prefix(ownership)
    )
    target = tools_root / document.storage_key
    assert target.read_bytes() == b'notes'

    await staging.rollback_promoted_attachments(promoted, ownership)

    assert document_rows == {}
    assert not target.exists()


@pytest.mark.asyncio
async def test_promoted_pdf_keeps_safe_extension_and_read_uses_pdf_reader(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.media import staging
    from cognitrix.media.document_capabilities import (
        load_turn_document_capabilities,
    )
    from cognitrix.tools import misc
    from cognitrix.tools.misc import Read
    from cognitrix.tools.utils import (
        ToolExecutionContext,
        reset_execution_context,
        set_execution_context,
    )

    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('report.pdf', b'%PDF-1.4\n%%EOF\n', 'text/plain')],
        user_key='user-1',
        stream_id='browser-pdf',
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    document = next(iter(document_rows.values()))

    assert document.mime_type == 'application/pdf'
    assert document.storage_key.endswith('.pdf')
    opened = []
    monkeypatch.setattr(
        misc,
        '_read_pdf_bytes',
        lambda content, name, page_range=None: (
            opened.append((content, name)) or 'parsed pdf'
        ),
    )
    capabilities = await load_turn_document_capabilities(
        ownership,
        fresh_document_ids=(str(document.id),),
    )
    token = set_execution_context(ToolExecutionContext(
        session_id='session-1', user_id='user-1', agent_id='agent-1',
        document_capabilities=capabilities,
    ))
    try:
        result = await Read.run(file_path=promoted.document_paths[0]['path'])
    finally:
        reset_execution_context(token)

    assert result.content == 'parsed pdf'
    assert opened == [(b'%PDF-1.4\n%%EOF\n', 'report.pdf')]
    await staging.rollback_promoted_attachments(promoted, ownership)


@pytest.mark.asyncio
async def test_declared_pdf_without_pdf_bytes_is_stored_as_sniffed_text(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.media import staging

    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('report.pdf', b'plain text', 'application/pdf')],
        user_key='user-1',
        stream_id='browser-spoofed-pdf',
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')

    promoted = await staging.promote_staged_attachments(staged, ownership)
    document = next(iter(document_rows.values()))

    assert document.mime_type == 'text/plain'
    assert document.storage_key.endswith('.txt')
    await staging.rollback_promoted_attachments(promoted, ownership)


def test_document_model_has_two_phase_promotion_state():
    fields = DocumentArtifact.model_fields

    assert {'status', 'promotion_token', 'generation', 'expires_at'} <= set(fields)
    assert fields['directory_identity'].is_required() is False
    assert fields['file_identity'].is_required() is False


@pytest.mark.asyncio
async def test_cancelled_document_row_save_is_settled_then_fully_rolled_back(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.media import staging

    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('notes.txt', b'notes')],
        user_key='user-1',
        stream_id='browser-1',
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def delayed_save(row):
        save_started.set()
        await release_save.wait()
        document_rows[str(row.id)] = row
        return row

    monkeypatch.setattr(DocumentArtifact, 'save', delayed_save)
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    task = asyncio.create_task(staging.promote_staged_attachments(staged, ownership))
    await asyncio.wait_for(save_started.wait(), timeout=2)
    task.cancel()
    release_save.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert document_rows == {}
    uploads = tools_root / 'uploads'
    assert not uploads.exists() or not list(uploads.rglob('f_*'))


@pytest.mark.asyncio
async def test_session_document_cleanup_deletes_file_before_row(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.media import staging

    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('notes.txt', b'notes')],
        user_key='user-1',
        stream_id='browser-1',
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    document = next(iter(document_rows.values()))
    target = tools_root / document.storage_key
    original_delete_many = DocumentArtifact.delete_many

    async def assert_file_first(query):
        assert not target.exists()
        return await original_delete_many(query)

    monkeypatch.setattr(DocumentArtifact, 'delete_many', assert_file_first)

    await DocumentAssetService().delete_session_documents(ownership)

    assert document_rows == {}
    staging.release_promoted_attachment_reservation(promoted)


@pytest.mark.asyncio
async def test_session_document_cleanup_retains_row_when_exact_file_delete_fails(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.media import staging

    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('notes.txt', b'notes')],
        user_key='user-1',
        stream_id='browser-1',
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    uploads = tools_root / 'uploads'
    original_uploads = tools_root / 'uploads-original'
    uploads.rename(original_uploads)
    uploads.mkdir()

    with pytest.raises(MediaValidationError):
        await DocumentAssetService().delete_session_documents(ownership)

    assert len(document_rows) == 1
    document = next(iter(document_rows.values()))
    assert (original_uploads / document.storage_key.removeprefix('uploads/')).exists()
    uploads.rmdir()
    original_uploads.rename(uploads)
    await DocumentAssetService().delete_session_documents(ownership)
    staging.release_promoted_attachment_reservation(promoted)


@pytest.mark.asyncio
async def test_document_intent_is_durable_before_quota_and_storage_leases(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(ownership)
    document.promotion_token = uuid.uuid4().hex
    events = []
    original_save = DocumentArtifact.save

    async def recording_save(row):
        events.append(('save', row.status))
        return await original_save(row)

    class Authority:
        async def require_active_owned(self, *_args):
            return SimpleNamespace(generation=7)

        async def reserve_intent(self, *_args, **kwargs):
            stored = document_rows[str(document.id)]
            events.append(('reserve', stored.status, kwargs['generation']))

        async def adopt_reservation(self, *_args, **kwargs):
            stored = document_rows[str(document.id)]
            events.append(('adopt', stored.status, kwargs['generation']))

        async def release_reservation(self, *_args, **_kwargs):
            events.append(('release',))

    monkeypatch.setattr(DocumentArtifact, 'save', recording_save)
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())

    await DocumentAssetService().prepare_document_for_storage(document, ownership)

    assert events == [
        ('save', 'intent'),
        ('reserve', 'intent', 7),
        ('adopt', 'pending', 7),
    ]
    assert document_rows[str(document.id)].status == 'pending'


@pytest.mark.asyncio
async def test_expired_pending_document_deletes_file_then_row_then_lease(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(ownership)
    document.status = 'pending'
    document.promotion_token = uuid.uuid4().hex
    document.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    document.directory_identity = None
    document.file_identity = None
    document_rows[str(document.id)] = document
    events = []
    inspected = replace(
        documents._delete_record(document),
        directory_identity=FileIdentity(1, b'\x03', True),
        file_identity=FileIdentity(1, b'\x04', False),
    )
    original_delete_many = DocumentArtifact.delete_many

    async def inspect(_record):
        return inspected

    async def delete_file(_record):
        events.append('file')

    async def delete_many(query):
        events.append('row')
        return await original_delete_many(query)

    class Authority:
        async def release_reservation(self, *_args, **_kwargs):
            events.append('lease')

    monkeypatch.setattr(documents.document_storage, 'inspect_document', inspect)
    monkeypatch.setattr(documents.document_storage, 'delete_document', delete_file)
    monkeypatch.setattr(DocumentArtifact, 'delete_many', delete_many)
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())
    monkeypatch.setattr(
        DocumentAssetService,
        '_session_contains_storage_key',
        staticmethod(lambda _document: asyncio.sleep(0, result=False)),
    )

    recovered = await DocumentAssetService().reconcile_expired()

    assert recovered == 1
    assert events == ['file', 'row', 'lease']
    assert document_rows == {}


@pytest.mark.asyncio
async def test_expired_ready_document_with_exact_history_marker_is_adopted(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(ownership)
    document.status = 'ready'
    document.promotion_token = uuid.uuid4().hex
    document.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    document_rows[str(document.id)] = document
    committed = []

    class Authority:
        async def commit_reservation(self, *_args, **kwargs):
            committed.append((kwargs['generation'], kwargs['promotion_token']))

    from cognitrix.sessions.base import Session

    history = SimpleNamespace(chat=[{
        'role': 'user',
        'type': 'text',
        'content': f'{documents.DOCUMENT_HISTORY_MARKER}\n{document.storage_key}',
    }])
    monkeypatch.setattr(Session, 'get', lambda _session_id: asyncio.sleep(0, result=history))
    monkeypatch.setattr(
        documents.document_storage,
        'inspect_document',
        lambda record: asyncio.sleep(0, result=record),
    )
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())

    recovered = await DocumentAssetService().reconcile_expired()

    assert recovered == 1
    assert committed == [(document.generation, document.promotion_token)]
    assert document_rows[str(document.id)].status == 'adopted'
    assert document_rows[str(document.id)].expires_at is None


@pytest.mark.asyncio
async def test_document_adoption_blocks_clear_until_row_and_quota_are_committed(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.session_ownership import (
        LifecycleLeaseActive,
        adopt_reservation,
        begin_clear,
        claim_new,
        release_document,
        reserve_intent,
        session_ownerships,
    )

    await _initialize_document_ownership_db(tmp_path)
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    binding = await claim_new(
        ownership.session_id, ownership.user_id, ownership.agent_id,
    )
    document = _document(ownership)
    document.status = 'ready'
    document.expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    document.generation = binding.generation
    document.promotion_token = uuid.uuid4().hex
    document_rows[str(document.id)] = document
    await reserve_intent(
        ownership.session_id,
        ownership.user_id,
        ownership.agent_id,
        generation=binding.generation,
        promotion_token=document.promotion_token,
        size_bytes=document.size_bytes,
    )
    await adopt_reservation(
        ownership.session_id,
        ownership.user_id,
        ownership.agent_id,
        generation=binding.generation,
        promotion_token=document.promotion_token,
    )

    original_update = DocumentArtifact.update_one
    row_transition_started = asyncio.Event()
    release_row_transition = asyncio.Event()

    async def delayed_adoption(query, values):
        if values.get('status') == 'adopted':
            row_transition_started.set()
            await release_row_transition.wait()
        return await original_update(query, values)

    monkeypatch.setattr(DocumentArtifact, 'update_one', delayed_adoption)
    monkeypatch.setattr(documents, '_session_authority', lambda: session_ownerships)
    task = asyncio.create_task(
        DocumentAssetService().mark_documents_adopted(
            [str(document.id)], ownership,
        )
    )
    event_wait = asyncio.create_task(row_transition_started.wait())
    done, _ = await asyncio.wait(
        {event_wait, task}, timeout=5, return_when=asyncio.FIRST_COMPLETED,
    )
    if task in done:
        await task
    assert event_wait in done, 'adoption never reached the row transition'
    lifecycle_was_blocked = False
    try:
        await begin_clear(
            ownership.session_id, ownership.user_id, ownership.agent_id,
        )
    except LifecycleLeaseActive:
        lifecycle_was_blocked = True
    finally:
        release_row_transition.set()
        await task

    assert lifecycle_was_blocked
    assert document.status == 'adopted'
    await release_document(
        ownership.session_id,
        ownership.user_id,
        ownership.agent_id,
        promotion_token=document.promotion_token,
        size_bytes=document.size_bytes,
    )


@pytest.mark.asyncio
async def test_reconciliation_commits_adopted_row_left_with_adopting_ledger(
    document_rows, tmp_path, monkeypatch
):
    from cognitrix.session_ownership import (
        adopt_reservation,
        claim_new,
        require_owned,
        reserve_intent,
        session_ownerships,
    )

    await _initialize_document_ownership_db(tmp_path)
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    binding = await claim_new(
        ownership.session_id, ownership.user_id, ownership.agent_id,
    )
    document = _document(ownership)
    document.status = 'adopted'
    document.expires_at = None
    document.generation = binding.generation
    document.promotion_token = uuid.uuid4().hex
    document_rows[str(document.id)] = document
    await reserve_intent(
        ownership.session_id,
        ownership.user_id,
        ownership.agent_id,
        generation=binding.generation,
        promotion_token=document.promotion_token,
        size_bytes=document.size_bytes,
    )
    await adopt_reservation(
        ownership.session_id,
        ownership.user_id,
        ownership.agent_id,
        generation=binding.generation,
        promotion_token=document.promotion_token,
    )
    monkeypatch.setattr(documents, '_session_authority', lambda: session_ownerships)

    recovered = await DocumentAssetService().reconcile_expired()

    stored = await require_owned(
        ownership.session_id, ownership.user_id, ownership.agent_id,
    )
    reservation = next(
        item for item in stored.reservations
        if item['promotion_token'] == document.promotion_token
    )
    assert recovered == 1
    assert reservation['status'] == 'committed'
    assert stored.document_count == 1
    assert stored.document_bytes == document.size_bytes


@pytest.mark.asyncio
async def test_reconciliation_releases_each_no_row_ledger_token_exactly(
    document_rows, monkeypatch
):
    released = []
    binding = SimpleNamespace(
        session_id='session-1',
        user_id='user-1',
        agent_id='agent-1',
        reservations=[
            {
                'promotion_token': 'b' * 32,
                'size_bytes': 11,
                'generation': 2,
                'status': 'committed',
            },
            {
                'promotion_token': 'c' * 32,
                'size_bytes': 12,
                'generation': 2,
                'status': 'adopting',
            },
            {
                'promotion_token': 'd' * 32,
                'size_bytes': 13,
                'generation': 2,
                'status': 'pending',
            },
        ],
    )

    class Authority:
        async def reconciliation_bindings(self, *, limit):
            assert limit == documents.DOCUMENT_RECONCILE_BATCH_SIZE
            return [binding]

        async def release_document(self, *args, **kwargs):
            released.append(('document', *args, kwargs['promotion_token'], kwargs['size_bytes']))

        async def release_reservation(self, *args):
            released.append(('reservation', *args))

    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())

    recovered = await DocumentAssetService().reconcile_expired()

    assert recovered == 3
    assert released == [
        ('document', 'session-1', 'user-1', 'agent-1', 'b' * 32, 11),
        ('reservation', 'session-1', 'user-1', 'agent-1', 'c' * 32),
        ('reservation', 'session-1', 'user-1', 'agent-1', 'd' * 32),
    ]


@pytest.mark.asyncio
async def test_reconciling_row_renews_lease_and_retries_after_worker_crash(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(ownership)
    document.status = 'ready'
    document.promotion_token = uuid.uuid4().hex
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    document.expires_at = (base - timedelta(seconds=1)).isoformat()
    document_rows[str(document.id)] = document
    attempts = 0
    released = []

    async def inspect(_record):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError('worker crashed after claiming reconciliation')
        return None

    class Authority:
        async def release_reservation(self, *args):
            released.append(args[-1])

    monkeypatch.setattr(documents.document_storage, 'inspect_document', inspect)
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())
    monkeypatch.setattr(
        DocumentAssetService,
        '_session_contains_storage_key',
        staticmethod(lambda _document: asyncio.sleep(0, result=False)),
    )

    assert await DocumentAssetService().reconcile_expired(now=base) == 0
    claimed_expiry = datetime.fromisoformat(document.expires_at)
    assert document.status == 'reconciling'
    assert claimed_expiry > base

    assert await DocumentAssetService().reconcile_expired(
        now=base + timedelta(seconds=1)
    ) == 0
    assert attempts == 1

    assert await DocumentAssetService().reconcile_expired(
        now=claimed_expiry + timedelta(seconds=1)
    ) == 1
    assert attempts == 2
    assert document_rows == {}
    assert released == [document.promotion_token]


@pytest.mark.asyncio
async def test_reconciliation_claim_cas_prevents_duplicate_workers(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    document = _document(ownership)
    document.status = 'ready'
    document.promotion_token = uuid.uuid4().hex
    document.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    document_rows[str(document.id)] = document
    inspection_started = asyncio.Event()
    release_inspection = asyncio.Event()
    inspections = 0

    async def inspect(_record):
        nonlocal inspections
        inspections += 1
        inspection_started.set()
        await release_inspection.wait()
        return None

    class Authority:
        async def release_reservation(self, *_args):
            return None

    monkeypatch.setattr(documents.document_storage, 'inspect_document', inspect)
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())
    monkeypatch.setattr(
        DocumentAssetService,
        '_session_contains_storage_key',
        staticmethod(lambda _document: asyncio.sleep(0, result=False)),
    )

    first = asyncio.create_task(DocumentAssetService().reconcile_expired())
    await asyncio.wait_for(inspection_started.wait(), timeout=1)
    second = asyncio.create_task(DocumentAssetService().reconcile_expired())
    await asyncio.sleep(0)
    release_inspection.set()

    assert sorted(await asyncio.gather(first, second)) == [0, 1]
    assert inspections == 1


@pytest.mark.asyncio
async def test_reconciliation_processes_only_one_bounded_batch(
    document_rows, monkeypatch
):
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    for _index in range(3):
        document = _document(ownership)
        document.status = 'ready'
        document.promotion_token = uuid.uuid4().hex
        document.expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        document_rows[str(document.id)] = document

    class Authority:
        async def release_reservation(self, *_args):
            return None

    monkeypatch.setattr(documents, 'DOCUMENT_RECONCILE_BATCH_SIZE', 2)
    monkeypatch.setattr(
        documents.document_storage,
        'inspect_document',
        lambda _record: asyncio.sleep(0, result=None),
    )
    monkeypatch.setattr(documents, '_session_authority', lambda: Authority())
    monkeypatch.setattr(
        DocumentAssetService,
        '_session_contains_storage_key',
        staticmethod(lambda _document: asyncio.sleep(0, result=False)),
    )

    assert await DocumentAssetService().reconcile_expired() == 2
    assert len(document_rows) == 1


@pytest.mark.asyncio
async def test_cancelled_document_storage_join_does_not_replace_cancellation():
    from cognitrix.media import document_storage

    started = threading.Event()
    release = threading.Event()

    def fail_after_cancel():
        started.set()
        release.wait(timeout=2)
        raise OSError('worker failed while caller was cancelling')

    task = asyncio.create_task(document_storage._run_thread_joined(fail_after_cancel))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
