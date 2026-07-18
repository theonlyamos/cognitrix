import types

import pytest

from cognitrix.cli import core


@pytest.mark.asyncio
async def test_cli_clear_history_cleans_artifacts_after_session_save(monkeypatch):
    order = []
    session = types.SimpleNamespace(id='session-1', chat=['message'])

    async def save():
        order.append(('save', list(session.chat)))

    async def cleanup(session_id):
        order.append(('cleanup', session_id))

    session.save = save
    monkeypatch.setattr(core, 'delete_session_artifacts', cleanup, raising=False)

    await core._clear_session_history(session)

    assert order == [('save', []), ('cleanup', 'session-1')]
