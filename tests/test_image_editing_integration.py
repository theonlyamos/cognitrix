"""End-to-end acceptance coverage for artifact-backed image editing."""

from __future__ import annotations

import asyncio
import base64
import importlib
import io
import json
import types
import uuid
from copy import deepcopy

import httpx
import pytest
from PIL import Image
from starlette.requests import Request

import cognitrix.artifacts as artifact_store
import cognitrix.media.staging as staging
from cognitrix.artifacts import Artifact
from cognitrix.media import MediaOwnership, media_assets
from cognitrix.models import Agent
from cognitrix.providers.base import LLM, LLMManager
from cognitrix.sessions.base import Session
from cognitrix.sessions.context import SlidingWindowContextManager
from cognitrix.tools.base import ToolManager
from cognitrix.utils import sse
from cognitrix.utils.llm_response import LLMResponse


_PUBLIC_ARTIFACT_FIELDS = {
    "id", "mime_type", "filename", "width", "height", "origin",
}


class _JsonRequest:
    headers: dict[str, str] = {}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _SSERequest:
    async def is_disconnected(self):
        return False


def _multipart_request(payload: dict, image: bytes, filename: str = "upload.png"):
    request = httpx.Request(
        "POST",
        "http://test/agents/chat",
        files=[
            ("payload", ("payload.json", json.dumps(payload), "application/json")),
            ("files", (filename, image, "image/png")),
        ],
    )
    body = request.read()
    chunks = [body]

    async def receive():
        return {
            "type": "http.request",
            "body": chunks.pop(0) if chunks else b"",
            "more_body": bool(chunks),
        }

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/agents/chat",
        "raw_path": b"/agents/chat",
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in request.headers.items()],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    return Request(scope, receive), len(body)


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="PNG")
    return output.getvalue()


def _provider_response(data: bytes) -> dict:
    return {
        "steps": [{
            "type": "model_output",
            "content": [{
                "type": "image",
                "mime_type": "image/png",
                "data": base64.b64encode(data).decode("ascii"),
            }],
        }],
    }


def _assert_public_artifacts_are_safe(events, artifacts: list[Artifact]):
    public = json.dumps(events)
    public_refs = []
    for event in events:
        public_refs.extend(event.get("artifacts") or [])
        outcome = event.get("outcome")
        if isinstance(outcome, dict):
            public_refs.extend(outcome.get("artifacts") or [])
    for reference in public_refs:
        assert set(reference) == _PUBLIC_ARTIFACT_FIELDS

    for artifact in artifacts:
        for key in (
            artifact.storage_key,
            artifact.vision_storage_key,
            artifact.thumbnail_storage_key,
        ):
            if key:
                assert key not in public
        for variant in ("original", "vision", "thumbnail"):
            try:
                path = artifact_store.variant_path(artifact, variant)
            except ValueError:
                continue
            assert path.as_posix() not in public
            assert json.dumps(str(path))[1:-1] not in public
            if path.is_file():
                assert base64.b64encode(path.read_bytes()).decode("ascii") not in public


class _ConversationProvider:
    """Script the external conversational model and retain its real prompts."""

    def __init__(self):
        self.next_arguments: dict | None = None
        self.awaiting_tool = False
        self.initial_prompts: list[list[dict]] = []

    def script_turn(self, arguments: dict):
        self.next_arguments = arguments
        self.awaiting_tool = True

    async def generate(self, _llm, prompt, stream=False, tools=None, **_kwargs):
        response = LLMResponse()
        if self.awaiting_tool:
            self.initial_prompts.append(deepcopy(prompt))
            response.tool_calls = [{
                "name": "Generate Image",
                "arguments": dict(self.next_arguments or {}),
                "tool_call_id": f"image-{len(self.initial_prompts)}",
            }]
            self.awaiting_tool = False
        else:
            response.add_chunk("Image ready.")

        if not stream:
            return response

        async def iterator():
            yield response

        return iterator()


@pytest.fixture
def integration_harness(tmp_path, monkeypatch):
    rows: dict[str, Artifact] = {}
    session_rows: dict[str, Session] = {}
    root = tmp_path / "artifact-root"

    # Importing the routes package initializes the worker database. Point that
    # import-time side effect at pytest's isolated directory before importing.
    from cognitrix.config import settings

    monkeypatch.setattr(settings, "db_type", "sqlite")
    monkeypatch.setattr(settings, "db_name", str(tmp_path / "routes.db"))
    agent_routes = importlib.import_module("cognitrix.api.routes.agents")

    async def save_artifact(row):
        if row.id is None:
            object.__setattr__(row, "id", str(uuid.uuid4()))
        rows[str(row.id)] = row
        return row

    async def get_artifact(artifact_id):
        return rows.get(str(artifact_id))

    async def find_artifacts(query):
        return [
            row for row in rows.values()
            if all(getattr(row, key) == value for key, value in query.items())
        ]

    async def delete_artifacts(query):
        doomed = [
            identifier for identifier, row in rows.items()
            if all(getattr(row, key) == value for key, value in query.items())
        ]
        for identifier in doomed:
            rows.pop(identifier)
        return len(doomed)

    monkeypatch.setattr(artifact_store, "_root", lambda: root)
    monkeypatch.setattr(Artifact, "save", save_artifact)
    monkeypatch.setattr(Artifact, "get", get_artifact)
    monkeypatch.setattr(Artifact, "find", find_artifacts)
    monkeypatch.setattr(Artifact, "delete_many", delete_artifacts)
    monkeypatch.setattr(staging.settings, "workdir", tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("DISABLE_VECTOR_STORE", "true")

    async def save_session(_session):
        session_rows[str(_session.id)] = _session.model_copy(deep=True)
        return _session

    async def get_session(session_id):
        stored = session_rows.get(str(session_id))
        return stored.model_copy(deep=True) if stored is not None else None

    monkeypatch.setattr(Session, "save", save_session)
    monkeypatch.setattr(Session, "get", get_session)
    monkeypatch.setattr(sse, "is_multi_step_task", lambda _prompt: False)

    from cognitrix.providers import gemini_image
    from cognitrix.tools import image as image_tool

    provider_outputs: list[bytes] = []
    gemini_payloads: list[dict] = []

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self, chunk_size=None):
            assert chunk_size == 64 * 1024
            yield json.dumps(_provider_response(provider_outputs.pop(0))).encode()

        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            assert method == "POST"
            gemini_payloads.append(deepcopy(json))
            return Response()

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(
        ToolManager,
        "get_by_name",
        staticmethod(lambda name: image_tool.generate_image if name.replace("_", " ").lower() == "generate image" else None),
    )

    conversation = _ConversationProvider()
    monkeypatch.setattr(LLMManager, "generate_response", staticmethod(conversation.generate))

    llm = LLM(provider="openai", base_url="http://provider.invalid", api_key="key", model="model")
    agent = Agent(
        id="agent-1",
        name="Image Agent",
        llm=llm,
        system_prompt="Use Generate Image for every request.",
        tools=[image_tool.generate_image],
    )
    agent.__dict__["_ctx_mgr"] = SlidingWindowContextManager()
    agent.__dict__["_ctx_mgr_config"] = {"agent_id": "agent-1"}
    session = Session(id="session-1", agent_id="agent-1", user_id="user-1")
    session_rows["session-1"] = session.model_copy(deep=True)
    manager = sse.SSEManager(agent)
    manager.user_key = "user-1"

    async def resolve_session(session_id):
        assert session_id == "session-1"
        return await get_session(session_id)

    async def resolve_agent(agent_id, _request):
        assert agent_id == "agent-1"
        return agent

    manager._resolve_session = resolve_session
    monkeypatch.setattr(agent_routes, "_resolve_agent", resolve_agent)
    monkeypatch.setattr(agent_routes, "get_sse_manager", lambda *_args, **_kwargs: manager)

    async def run_turn(message, tool_arguments, *, selected_id=None, upload=None):
        conversation.script_turn(tool_arguments)
        payload = {
            "agent_id": "agent-1",
            "stream_id": "browser-1",
            "session_id": "session-1",
            "message": message,
        }
        if selected_id is not None:
            payload["edit_source_artifact_id"] = selected_id
        request_bytes = None
        if upload is None:
            request = _JsonRequest(payload)
        else:
            payload["edit_source_image_index"] = 0
            request, request_bytes = _multipart_request(payload, upload)

        assert await agent_routes.chat_endpoint(
            request, user=types.SimpleNamespace(id="user-1")
        ) == {"status": "Message sent"}
        response = await manager.sse_endpoint(_SSERequest())

        async def collect():
            events = []
            try:
                async for event in response.body_iterator:
                    data = event.get("data")
                    if not data:
                        continue
                    item = json.loads(data)
                    events.append(item)
                    if item.get("type") in {"turn_complete", "turn_stopped", "multistep_result"}:
                        break
            finally:
                await response.body_iterator.aclose()
            return events

        return await asyncio.wait_for(collect(), timeout=5), request_bytes

    return types.SimpleNamespace(
        rows=rows,
        root=root,
        reload_session=get_session,
        conversation=conversation,
        provider_outputs=provider_outputs,
        gemini_payloads=gemini_payloads,
        run_turn=run_turn,
    )


def _completed_artifact(events):
    event = next(
        item for item in events
        if item.get("type") == "tool" and item.get("status") == "completed"
    )
    return event["artifacts"][0]


@pytest.mark.asyncio
async def test_generated_parent_edit_sends_stored_bytes_and_records_child_lineage(
    integration_harness,
):
    harness = integration_harness
    harness.provider_outputs.extend([_png((255, 0, 0)), _png((0, 0, 255))])

    generated, _ = await harness.run_turn(
        "Generate a red lighthouse", {"prompt": "Generate a red lighthouse"}
    )
    parent = _completed_artifact(generated)
    stored_parent = await media_assets.resolve_image(
        parent["id"], MediaOwnership("session-1", "user-1", "agent-1"), "original"
    )
    edited, _ = await harness.run_turn(
        "Make it blue",
        {"prompt": "Make it blue", "source_artifact_id": parent["id"]},
        selected_id=parent["id"],
    )
    child = _completed_artifact(edited)

    assert base64.b64decode(harness.gemini_payloads[1]["input"][1]["data"]) == stored_parent.data
    assert harness.rows[child["id"]].source_artifact_id == parent["id"]
    assert child["origin"] == "generated"


@pytest.mark.asyncio
async def test_multipart_upload_is_promoted_current_vision_and_generate_image_source(
    integration_harness,
):
    harness = integration_harness
    uploaded_pixels = _png((20, 180, 40))
    harness.provider_outputs.append(_png((180, 20, 40)))

    events, request_bytes = await harness.run_turn(
        "Add a red border", {"prompt": "Add a red border"}, upload=uploaded_pixels
    )
    ingested = next(item for item in events if item.get("type") == "attachments_ingested")
    uploaded = ingested["artifacts"][0]
    child = _completed_artifact(events)
    initial_prompt = json.dumps(harness.conversation.initial_prompts[-1])
    resolved_vision = await media_assets.resolve_image(
        uploaded["id"], MediaOwnership("session-1", "user-1", "agent-1"), "vision"
    )

    assert request_bytes > len(uploaded_pixels)
    assert set(uploaded) == _PUBLIC_ARTIFACT_FIELDS
    assert base64.b64encode(resolved_vision.data).decode() in initial_prompt
    assert base64.b64decode(harness.gemini_payloads[0]["input"][1]["data"]) == (
        await media_assets.resolve_image(
            uploaded["id"], MediaOwnership("session-1", "user-1", "agent-1"), "original"
        )
    ).data
    assert harness.rows[child["id"]].source_artifact_id == uploaded["id"]
    reloaded = await harness.reload_session("session-1")
    assert reloaded is not None
    assert any(
        item.get("type") == "image" and item.get("artifact", {}).get("id") == uploaded["id"]
        for item in reloaded.chat
    )
    _assert_public_artifacts_are_safe(
        events,
        [harness.rows[uploaded["id"]], harness.rows[child["id"]]],
    )
    assert base64.b64encode(uploaded_pixels).decode("ascii") not in json.dumps(events)


@pytest.mark.asyncio
async def test_foreign_user_session_and_agent_ids_have_one_generic_public_denial(
    integration_harness,
):
    harness = integration_harness
    foreign = [
        MediaOwnership("session-1", "other-user", "agent-1"),
        MediaOwnership("other-session", "user-1", "agent-1"),
        MediaOwnership("session-1", "user-1", "other-agent"),
    ]
    identifiers = []
    denial_events = []
    for index, ownership in enumerate(foreign):
        ref = await media_assets.store_generated_image(
            _png((30 + index, 40, 50)), {"filename": f"private-{index}.png"}, ownership
        )
        identifiers.append(ref.id)

    for identifier in identifiers:
        events, _ = await harness.run_turn(
            "Edit the selected image",
            {"prompt": "must never run", "source_artifact_id": identifier},
            selected_id=identifier,
        )
        denial_events.extend(events)
        error = next(item for item in events if item.get("type") == "error")
        public = json.dumps(error)
        assert error["content"] == "Attachments or the selected image are unavailable. Please try again."
        assert identifier not in public
        assert "other-user" not in public
        assert "other-session" not in public
        assert "other-agent" not in public
        assert "private-" not in public

    _assert_public_artifacts_are_safe(
        denial_events,
        [harness.rows[identifier] for identifier in identifiers],
    )

    assert harness.gemini_payloads == []


@pytest.mark.asyncio
async def test_three_edit_turns_chain_children_and_hydrate_only_explicit_parent(
    integration_harness,
):
    harness = integration_harness
    harness.provider_outputs.extend([
        _png((255, 0, 0)),
        _png((0, 255, 0)),
        _png((0, 0, 255)),
        _png((255, 255, 0)),
    ])

    events, _ = await harness.run_turn("Base", {"prompt": "Base"})
    refs = [_completed_artifact(events)]
    for number in range(1, 4):
        parent = refs[-1]
        events, _ = await harness.run_turn(
            f"Edit {number}",
            {"prompt": f"Edit {number}", "source_artifact_id": parent["id"]},
            selected_id=parent["id"],
        )
        refs.append(_completed_artifact(events))

    assert [harness.rows[item["id"]].source_artifact_id for item in refs] == [
        None,
        refs[0]["id"],
        refs[1]["id"],
        refs[2]["id"],
    ]

    ownership = MediaOwnership("session-1", "user-1", "agent-1")
    vision_tokens = []
    for ref in refs:
        vision = await media_assets.resolve_image(ref["id"], ownership, "vision")
        vision_tokens.append(base64.b64encode(vision.data).decode())

    for edit_index, prompt in enumerate(harness.conversation.initial_prompts[1:], start=1):
        serialized = json.dumps(prompt)
        assert vision_tokens[edit_index - 1] in serialized
        assert all(
            token not in serialized
            for token in vision_tokens[: edit_index - 1]
        )
        assert vision_tokens[edit_index] not in serialized

    assert [
        base64.b64decode(payload["input"][1]["data"])
        for payload in harness.gemini_payloads[1:]
    ] == [
        (await media_assets.resolve_image(refs[index]["id"], ownership, "original")).data
        for index in range(3)
    ]
