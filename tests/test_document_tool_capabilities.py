from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from cognitrix.media import document_capabilities, document_storage
from cognitrix.media.types import MediaAccessError, MediaOwnership
from cognitrix.tools import misc
from cognitrix.tools.misc import Edit, Glob, Grep, Read, Write
from cognitrix.tools.utils import (
    DocumentCapability,
    ToolExecutionContext,
    delegated_execution_context,
    reset_execution_context,
    set_execution_context,
)


def _capability(
    *,
    document_id: str = 'doc-1',
    storage_key: str = (
        'uploads/d_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_'
        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/'
        'f_0123456789abcdef0123456789abcdef.txt'
    ),
    mime_type: str = 'text/plain',
) -> DocumentCapability:
    return DocumentCapability(
        document_id=document_id,
        storage_key=storage_key,
        mime_type=mime_type,
        filename='notes.txt',
        size_bytes=12,
        sha256='a' * 64,
        tools_root_identity='v1:1:d:01',
        uploads_identity='v1:1:d:02',
        directory_identity='v1:1:d:03',
        file_identity='v1:1:f:04',
    )


def _document(document_id: str, *, status: str, owner: str = 'user-1'):
    capability = _capability(document_id=document_id)
    return SimpleNamespace(
        id=document_id,
        session_id='session-1',
        user_id=owner,
        agent_id='agent-1',
        status=status,
        storage_key=capability.storage_key,
        mime_type=capability.mime_type,
        filename=capability.filename,
        size_bytes=capability.size_bytes,
        sha256=capability.sha256,
        tools_root_identity=capability.tools_root_identity,
        uploads_identity=capability.uploads_identity,
        directory_identity=capability.directory_identity,
        file_identity=capability.file_identity,
    )


def test_document_capability_is_immutable_and_delegation_drops_it():
    capability = _capability()
    parent = ToolExecutionContext(
        user_id='user-1',
        session_id='session-1',
        agent_id='agent-1',
        document_capabilities=(capability,),
        selected_image_artifact_id='image-1',
    )

    with pytest.raises(FrozenInstanceError):
        capability.storage_key = 'uploads/changed'  # type: ignore[misc]

    child = delegated_execution_context(parent)
    assert child.document_capabilities == ()
    assert child.session_id is None
    assert child.agent_id is None
    assert child.selected_image_artifact_id is None


@pytest.mark.asyncio
async def test_loader_allows_only_exact_fresh_ready_and_selected_adopted_ids(
    monkeypatch,
):
    documents = {
        'fresh': _document('fresh', status='ready'),
        'selected': _document('selected', status='adopted'),
    }
    requested = []

    async def get(document_id):
        requested.append(document_id)
        return documents.get(document_id)

    monkeypatch.setattr(document_capabilities.DocumentArtifact, 'get', get)

    grants = await document_capabilities.load_turn_document_capabilities(
        MediaOwnership('session-1', 'user-1', 'agent-1'),
        fresh_document_ids=('fresh',),
        adopted_document_ids=('selected',),
    )

    assert requested == ['fresh', 'selected']
    assert tuple(grant.document_id for grant in grants) == ('fresh', 'selected')
    assert grants[0].storage_key == documents['fresh'].storage_key
    assert grants[1].file_identity == documents['selected'].file_identity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('document', 'fresh_ids', 'adopted_ids'),
    [
        (_document('doc', status='pending'), ('doc',), ()),
        (_document('doc', status='ready'), (), ('doc',)),
        (_document('doc', status='adopted', owner='user-2'), (), ('doc',)),
    ],
)
async def test_loader_rejects_wrong_status_or_owner(
    monkeypatch, document, fresh_ids, adopted_ids
):
    async def get(_document_id):
        return document

    monkeypatch.setattr(document_capabilities.DocumentArtifact, 'get', get)

    with pytest.raises(MediaAccessError):
        await document_capabilities.load_turn_document_capabilities(
            MediaOwnership('session-1', 'user-1', 'agent-1'),
            fresh_document_ids=fresh_ids,
            adopted_document_ids=adopted_ids,
        )


@pytest.mark.asyncio
async def test_read_uses_only_exact_grant_and_identity_pinned_storage(monkeypatch):
    grant = _capability()
    records = []

    def read_document(record):
        records.append(record)
        return b'hello world\n'

    monkeypatch.setattr(document_storage, 'read_document_sync', read_document)
    token = set_execution_context(ToolExecutionContext(
        user_id='user-1',
        session_id='session-1',
        agent_id='agent-1',
        document_capabilities=(grant,),
    ))
    try:
        allowed = await Read.run(file_path=grant.storage_key)
        denied = await Read.run(
            file_path='uploads/d_owner/f_ffffffffffffffffffffffffffffffff.txt'
        )
    finally:
        reset_execution_context(token)

    assert 'hello world' in allowed.content
    assert denied.content.startswith('Error')
    assert len(records) == 1
    assert records[0].document_id == grant.document_id
    assert records[0].expected_digest == grant.sha256
    assert records[0].file_identity.file_id == b'\x04'


@pytest.mark.asyncio
async def test_pdf_reads_capability_bytes_without_opening_client_path(monkeypatch):
    grant = _capability(mime_type='application/pdf')
    monkeypatch.setattr(
        document_storage,
        'read_document_sync',
        lambda _record: b'%PDF- exact managed bytes',
    )
    captured = {}

    def read_pdf_bytes(content, display_name, page_range=None):
        captured.update(
            content=content,
            display_name=display_name,
            page_range=page_range,
        )
        return 'managed pdf text'

    monkeypatch.setattr(misc, '_read_pdf_bytes', read_pdf_bytes)
    token = set_execution_context(ToolExecutionContext(
        document_capabilities=(grant,),
    ))
    try:
        result = await Read.run(file_path=grant.storage_key, page_range='1')
    finally:
        reset_execution_context(token)

    assert result.content == 'managed pdf text'
    assert captured == {
        'content': b'%PDF- exact managed bytes',
        'display_name': 'notes.txt',
        'page_range': '1',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Write, Edit])
async def test_write_tools_deny_even_an_exact_managed_grant(tool):
    grant = _capability()
    token = set_execution_context(ToolExecutionContext(
        document_capabilities=(grant,),
    ))
    try:
        if tool is Write:
            result = await tool.run(file_path=grant.storage_key, content='changed')
        else:
            result = await tool.run(
                file_path=grant.storage_key,
                old_string='old',
                new_string='changed',
            )
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert 'read-only' in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Grep, Glob])
async def test_search_prunes_the_entire_managed_upload_tree(
    tmp_path, monkeypatch, tool
):
    monkeypatch.setattr(misc.settings, 'tools_root', tmp_path.resolve())
    (tmp_path / 'ordinary.txt').write_text('needle ordinary')
    uploads = tmp_path / 'uploads' / 'd_owner'
    uploads.mkdir(parents=True)
    (uploads / 'f_secret.txt').write_text('needle managed secret')
    grant = _capability(
        storage_key=(
            'uploads/d_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/f_secret.txt'
        ),
    )
    token = set_execution_context(ToolExecutionContext(
        document_capabilities=(grant,),
    ))
    try:
        if tool is Grep:
            result = await tool.run(pattern='needle', path='.')
        else:
            result = await tool.run(pattern='*.txt', path='.')
    finally:
        reset_execution_context(token)

    assert 'ordinary.txt' in result.content
    assert 'f_secret.txt' not in result.content
    assert 'managed secret' not in result.content
