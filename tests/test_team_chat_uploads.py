"""A team chat stores what the person attached with the chat, writes it into
the transcript after their message, and only then counts it as kept."""

from types import SimpleNamespace

import pytest

from cognitrix.api.routes import team_chat
from cognitrix.media import MediaOwnership
from cognitrix.media.documents import DOCUMENT_HISTORY_MARKER
from cognitrix.tools.utils import ArtifactRef


class _Chat:
    def __init__(self, fail_save=False):
        self.chat = [{'role': 'User', 'type': 'text', 'content': 'Look at these'}]
        self.saved = 0
        self.fail_save = fail_save

    def update_history(self, entries):
        self.chat.extend(entries)

    async def save(self):
        if self.fail_save:
            raise RuntimeError('database is away')
        self.saved += 1


@pytest.fixture
def media(monkeypatch):
    calls = SimpleNamespace(bound=[], released=[], adopted=[], fresh=None)
    promoted = SimpleNamespace(
        image_refs=[ArtifactRef(id='img_1', mime_type='image/png', filename='shapes.png', origin='uploaded')],
        document_paths=[{'id': 'd1', 'name': 'plan.txt', 'path': 'uploads/chat-1/f_abc.txt'}],
    )

    async def bind(ownership):
        calls.bound.append(ownership)

    async def promote(staged, ownership):
        return promoted

    async def capabilities(ownership, fresh_document_ids=()):
        calls.fresh = tuple(fresh_document_ids)
        return ('may-read-d1',)

    async def adopted(ids, ownership):
        calls.adopted.append(list(ids))

    monkeypatch.setattr(team_chat, '_bind_chat', bind)
    monkeypatch.setattr('cognitrix.media.staging.promote_staged_attachments', promote)
    monkeypatch.setattr(
        'cognitrix.media.staging.release_promoted_attachment_reservation', calls.released.append,
    )
    monkeypatch.setattr(
        'cognitrix.media.document_capabilities.load_turn_document_capabilities', capabilities,
    )
    monkeypatch.setattr(
        'cognitrix.media.documents.document_assets.mark_documents_adopted', adopted,
    )
    calls.promoted = promoted
    return calls


OWNER = MediaOwnership(session_id='chat-1', user_id='user-1', agent_id='team:team-1')


@pytest.mark.asyncio
async def test_uploads_join_the_transcript_and_every_agent_may_use_them(media):
    session, uploads, events = _Chat(), team_chat._TurnUploads(), []

    async def emit(event):
        events.append(event)

    await team_chat._take_in_uploads(session, object(), OWNER, uploads, emit)

    assert media.bound == [OWNER]
    assert [e['type'] for e in session.chat] == ['text', 'image', 'text']
    assert session.chat[1]['artifact']['id'] == 'img_1'
    assert session.chat[2]['content'].startswith(DOCUMENT_HISTORY_MARKER)
    assert session.saved == 1
    assert uploads.adopted and uploads.capabilities == ('may-read-d1',)
    assert [ref.id for ref in uploads.images] == ['img_1']
    assert media.fresh == ('d1',)
    assert media.released == [media.promoted] and media.adopted == [['d1']]
    assert events == [{
        'type': 'attachments_ingested',
        'artifacts': [uploads.images[0].model_dump()],
        'document_count': 1,
    }]


@pytest.mark.asyncio
async def test_a_failed_save_leaves_the_upload_to_be_rolled_back(media):
    session, uploads, events = _Chat(fail_save=True), team_chat._TurnUploads(), []

    async def emit(event):
        events.append(event)

    with pytest.raises(team_chat._UploadFailed):
        await team_chat._take_in_uploads(session, object(), OWNER, uploads, emit)

    # The transcript is as it was, and the turn's cleanup removes the files.
    assert len(session.chat) == 1
    assert uploads.promoted is media.promoted and not uploads.adopted
    assert media.released == [] and media.adopted == [] and events == []
