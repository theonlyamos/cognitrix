import types

import pytest

import cognitrix.api.routes.agents as agent_routes
from cognitrix.utils.sse import SSEManager, _SSE_MANAGERS


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_stop_endpoint_cancels_the_callers_scoped_browser_turn(monkeypatch):
    agent = types.SimpleNamespace(id="agent-1")
    manager = SSEManager(agent)
    manager.begin_turn()

    async def resolve_agent(_agent_id, _request):
        return agent

    monkeypatch.setattr(agent_routes, "_resolve_agent", resolve_agent)
    monkeypatch.setattr(agent_routes, "get_sse_manager", lambda *args, **kwargs: manager)

    result = await agent_routes.stop_endpoint(
        JsonRequest({"agent_id": agent.id, "stream_id": "browser-a"}),
        user=types.SimpleNamespace(id="user-a"),
    )

    assert result == {"status": "stopping"}
    assert manager.stop_requested is True


@pytest.mark.asyncio
async def test_stop_endpoint_rejects_when_no_turn_is_running(monkeypatch):
    agent = types.SimpleNamespace(id="agent-1")
    manager = SSEManager(agent)

    async def resolve_agent(_agent_id, _request):
        return agent

    monkeypatch.setattr(agent_routes, "_resolve_agent", resolve_agent)
    monkeypatch.setattr(agent_routes, "get_sse_manager", lambda *args, **kwargs: manager)

    with pytest.raises(agent_routes.HTTPException) as exc:
        await agent_routes.stop_endpoint(
            JsonRequest({"agent_id": agent.id, "stream_id": "browser-a"}),
            user=types.SimpleNamespace(id="user-a"),
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["chat", "stop"])
async def test_turn_endpoints_do_not_create_missing_stream_manager(monkeypatch, endpoint):
    _SSE_MANAGERS.clear()
    agent = types.SimpleNamespace(id="agent-1")

    async def resolve_agent(_agent_id, _request):
        return agent

    monkeypatch.setattr(agent_routes, "_resolve_agent", resolve_agent)
    request = JsonRequest({"agent_id": agent.id, "stream_id": "missing-stream"})

    with pytest.raises(agent_routes.HTTPException) as exc:
        if endpoint == "chat":
            await agent_routes.chat_endpoint(
                request, user=types.SimpleNamespace(id="user-a")
            )
        else:
            await agent_routes.stop_endpoint(
                request, user=types.SimpleNamespace(id="user-a")
            )

    assert exc.value.status_code == 409
    assert _SSE_MANAGERS == {}
