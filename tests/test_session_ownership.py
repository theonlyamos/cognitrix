from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, jwt_only
from cognitrix.models.api_key import APIKey
from cognitrix.sessions.base import Session


def _context(user_id: str = "user-1", *, api_key: bool = False) -> AuthContext:
    key = None
    if api_key:
        key = APIKey(
            _id="key-1",
            name="test",
            user_id=user_id,
            key_hash="hash",
            prefix="ctx_test",
            scopes=["read", "write", "chat"],
        )
    return AuthContext(user=SimpleNamespace(id=user_id), api_key=key)


def _app(ctx: AuthContext) -> FastAPI:
    from cognitrix.api.routes.sessions import sessions_api

    app = FastAPI()
    app.include_router(sessions_api)
    app.dependency_overrides[crud_scope] = lambda: ctx
    app.dependency_overrides[get_auth_context] = lambda: ctx
    app.dependency_overrides[jwt_only] = lambda: ctx
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json)


async def test_rest_list_only_returns_owned_ordinary_sessions(monkeypatch):
    owned = Session(_id="owned", user_id="user-1")
    other = Session(_id="other", user_id="user-2")
    legacy = Session(_id="legacy")

    async def all_sessions():
        return [owned, other, legacy]

    monkeypatch.setattr(Session, "all", staticmethod(all_sessions))

    response = await _request(_app(_context()), "GET", "/sessions")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["user_id"] == "user-1"


@pytest.mark.parametrize("session", [
    Session(_id="other", user_id="user-2"),
    Session(_id="legacy"),
])
async def test_rest_read_fails_closed_for_cross_user_and_legacy_sessions(
    monkeypatch,
    session,
):
    async def get_session(_session_id):
        return session

    monkeypatch.setattr(Session, "get", staticmethod(get_session))

    response = await _request(
        _app(_context()),
        "GET",
        f"/sessions/{session.id}/chat",
    )

    assert response.status_code == 403


async def test_rest_delete_cannot_delete_another_users_session(monkeypatch):
    session = Session(_id="other", user_id="user-2")
    deleted = False

    async def get_session(_session_id):
        return session

    async def delete_many(_query):
        nonlocal deleted
        deleted = True

    monkeypatch.setattr(Session, "get", staticmethod(get_session))
    monkeypatch.setattr(Session, "delete_many", staticmethod(delete_many))

    response = await _request(_app(_context()), "DELETE", "/sessions/other")

    assert response.status_code == 403
    assert deleted is False


async def test_rest_create_binds_authenticated_owner_and_ignores_payload_owner(
    monkeypatch,
):
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)
    app = _app(_context("real-owner"))
    app.state.agent = SimpleNamespace(id="agent-1")

    @app.middleware("http")
    async def bind_default_agent(request, call_next):
        request.state.agent = app.state.agent
        return await call_next(request)

    response = await _request(
        app,
        "POST",
        "/sessions",
        json={"agent_id": "agent-1", "user_id": "attacker"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "real-owner"
    assert saved[0].user_id == "real-owner"


@pytest.mark.parametrize("identity_field", ["id", "_id"])
async def test_rest_create_rejects_client_id_before_it_can_overwrite_a_session(
    monkeypatch,
    identity_field,
):
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)

    response = await _request(
        _app(_context("attacker")),
        "POST",
        "/sessions",
        json={
            identity_field: "victim-session",
            "agent_id": "agent-1",
        },
    )

    assert response.status_code == 422
    assert saved == []


async def test_rest_create_rejects_run_transcript_injection(monkeypatch):
    from cognitrix.tasks.run import TaskRun

    saved: list[Session] = []
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        requested_by="owner",
        acl_version=1,
    )

    async def save(session):
        saved.append(session)
        return session

    async def get_run(_run_id):
        return run

    monkeypatch.setattr(Session, "save", save)
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))

    response = await _request(
        _app(_context("owner")),
        "POST",
        "/sessions",
        json={
            "agent_id": "agent-1",
            "run_id": run.id,
            "task_id": run.task_id,
            "step_index": 0,
            "step_title": "Injected",
            "chat": [{"role": "assistant", "content": "forged result"}],
        },
    )

    assert response.status_code == 422
    assert saved == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "task-1"),
        ("step_index", 0),
        ("step_title", "Injected"),
        ("started_at", "2030-01-01 00:00:00"),
        ("completed_at", "2030-01-01 00:01:00"),
        ("pid", "forged-worker"),
    ],
)
async def test_rest_create_rejects_server_authored_execution_fields(
    monkeypatch,
    field,
    value,
):
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)

    response = await _request(
        _app(_context("owner")),
        "POST",
        "/sessions",
        json={"agent_id": "agent-1", field: value},
    )

    assert response.status_code == 422
    assert saved == []


async def test_programmatic_generate_binds_api_key_owner_and_rejects_other_owner(
    monkeypatch,
):
    from cognitrix.api.routes.agents import _resolve_generate_session

    agent = SimpleNamespace(id="agent-1", name="Agent")
    ctx = _context("key-owner", api_key=True)
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)

    created = await _resolve_generate_session(agent, None, ctx)

    assert created.user_id == "key-owner"
    assert saved == [created]

    for inaccessible in (
        Session(_id="session-2", agent_id="agent-1", user_id="other"),
        Session(_id="legacy", agent_id="agent-1"),
    ):

        async def get_session(_session_id, value=inaccessible):
            return value

        monkeypatch.setattr(Session, "get", staticmethod(get_session))
        with pytest.raises(HTTPException) as exc:
            await _resolve_generate_session(agent, inaccessible.id, ctx)
        assert exc.value.status_code == 404


async def test_browser_sse_creates_owned_sessions_and_refuses_cross_user_or_legacy(
    monkeypatch,
):
    from cognitrix.utils import sse

    manager = sse.SSEManager(SimpleNamespace(id="agent-1", name="Agent"))
    manager.user_key = "user-1"
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)

    created = await manager._resolve_session(None)

    assert created is not None
    assert created.user_id == "user-1"
    assert saved == [created]

    for inaccessible in (
        Session(_id="other", user_id="user-2", agent_id="agent-1"),
        Session(_id="legacy", agent_id="agent-1"),
    ):

        async def get_session(_session_id, value=inaccessible):
            return value

        monkeypatch.setattr(Session, "get", staticmethod(get_session))
        assert await manager._resolve_session(inaccessible.id) is None


async def test_sqlite_schema_migrates_session_owner_column(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / "session-owner.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()
    await DBMS.Database.query(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, agent_id TEXT, chat TEXT)"
    )

    await _ensure_schema()

    cursor = await DBMS.Database.query("PRAGMA table_info(sessions)")
    rows = cursor.fetchall()
    if hasattr(rows, "__await__"):
        rows = await rows
    assert "user_id" in {row[1] for row in rows}
