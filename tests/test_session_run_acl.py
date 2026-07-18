from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, jwt_only
from cognitrix.models.api_key import APIKey
from cognitrix.session_ownership import OwnershipNotFound
from cognitrix.sessions.base import Session
from cognitrix.tasks.run import TaskRun


def _context(*, allowed_agents: list[str]) -> AuthContext:
    key = APIKey(
        _id="key-1",
        name="test",
        user_id="user-1",
        key_hash="hash",
        prefix="ctx_test",
        scopes=["read", "write"],
        allowed_agents=allowed_agents,
    )
    return AuthContext(user=SimpleNamespace(id="user-1"), api_key=key)


def _app(ctx: AuthContext) -> FastAPI:
    from cognitrix.api.routes.sessions import sessions_api

    app = FastAPI()
    app.include_router(sessions_api)
    app.dependency_overrides[crud_scope] = lambda: ctx
    app.dependency_overrides[get_auth_context] = lambda: ctx
    app.dependency_overrides[jwt_only] = lambda: ctx
    return app


def _teams_app(ctx: AuthContext) -> FastAPI:
    from cognitrix.api.routes.teams import teams_api

    app = FastAPI()
    app.include_router(teams_api)
    app.dependency_overrides[crud_scope] = lambda: ctx
    app.dependency_overrides[get_auth_context] = lambda: ctx
    return app


def _async_value(value):
    async def get_value(*_args, **_kwargs):
        return value

    return get_value


def _patch_unbound_task_session(monkeypatch, session: Session) -> None:
    from cognitrix.api.routes import sessions as routes

    async def missing_binding(*_args, **_kwargs):
        raise OwnershipNotFound()

    monkeypatch.setattr(routes, "require_active_owned", missing_binding)
    monkeypatch.setattr(
        routes.SessionOwnership,
        "find_one",
        staticmethod(_async_value(None)),
    )
    monkeypatch.setattr(
        Session,
        "find_one",
        staticmethod(_async_value(session)),
    )


async def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path)


async def test_task_run_chat_rejects_key_outside_immutable_acl(monkeypatch):
    session = Session(_id="session-1", run_id="run-1", chat=[{"content": "secret"}])
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-private"],
    )

    _patch_unbound_task_session(monkeypatch, session)
    monkeypatch.setattr(TaskRun, "get", staticmethod(_async_value(run)))

    response = await _request(_app(_context(allowed_agents=["agent-public"])), "GET", "/sessions/session-1/chat")

    assert response.status_code == 403
    assert response.json() == {"detail": "Not allowed to access this task run"}


async def test_full_task_run_session_cannot_bypass_chat_acl(monkeypatch):
    session = Session(_id="session-1", run_id="run-1", chat=[{"content": "secret"}])
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-private"],
    )

    _patch_unbound_task_session(monkeypatch, session)
    monkeypatch.setattr(TaskRun, "get", staticmethod(_async_value(run)))

    response = await _request(_app(_context(allowed_agents=["agent-public"])), "GET", "/sessions/session-1")

    assert response.status_code == 403


async def test_foreign_binding_cannot_fall_through_to_task_run_acl(monkeypatch):
    from cognitrix.api.routes import sessions as routes

    async def foreign_binding(*_args, **_kwargs):
        raise OwnershipNotFound()

    async def unexpected_session_lookup(_query):
        pytest.fail("a foreign durable binding must block Session row access")

    async def unexpected_run_lookup(_run_id):
        pytest.fail("a foreign durable binding must block TaskRun fallback")

    monkeypatch.setattr(routes, "require_active_owned", foreign_binding)
    monkeypatch.setattr(
        routes.SessionOwnership,
        "find_one",
        staticmethod(_async_value(SimpleNamespace(session_id="session-1"))),
    )
    monkeypatch.setattr(Session, "find_one", staticmethod(unexpected_session_lookup))
    monkeypatch.setattr(TaskRun, "get", staticmethod(unexpected_run_lookup))

    response = await _request(
        _app(_context(allowed_agents=["agent-allowed"])),
        "GET",
        "/sessions/session-1/chat",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


async def test_run_session_mapping_requires_run_acl_even_when_empty(monkeypatch):
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-private"],
    )
    find_called = False

    async def find_sessions(_query):
        nonlocal find_called
        find_called = True
        return []

    monkeypatch.setattr(TaskRun, "get", staticmethod(_async_value(run)))
    monkeypatch.setattr(Session, "find", staticmethod(find_sessions))

    response = await _request(_app(_context(allowed_agents=["agent-public"])), "GET", "/sessions/runs/run-1")

    assert response.status_code == 403
    assert find_called is False


async def test_session_list_filters_inaccessible_runs_but_keeps_ordinary_sessions(monkeypatch):
    from cognitrix.api.routes import sessions as routes

    ordinary = Session(
        _id="ordinary",
        agent_id="agent-public",
        chat=[{"content": "ordinary"}],
    )
    protected = Session(_id="protected", run_id="run-1", chat=[{"content": "secret"}])
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-private"],
    )

    monkeypatch.setattr(Session, "all", staticmethod(_async_value([ordinary, protected])))
    monkeypatch.setattr(routes, "_owned_sessions", _async_value([ordinary]))
    monkeypatch.setattr(
        routes.SessionOwnership,
        "find_one",
        staticmethod(_async_value(None)),
    )
    monkeypatch.setattr(TaskRun, "get", staticmethod(_async_value(run)))

    response = await _request(_app(_context(allowed_agents=["agent-public"])), "GET", "/sessions")

    assert response.status_code == 200
    assert response.json() == [ordinary.json()]


async def test_ordinary_session_chat_preserves_existing_crud_behavior(monkeypatch):
    from cognitrix.api.routes import sessions as routes

    session = Session(
        _id="ordinary",
        agent_id="agent-ordinary",
        user_id="user-1",
        chat=[{"role": "User", "type": "text", "content": "hello"}],
    )

    async def unexpected_run_lookup(_run_id):
        pytest.fail("ordinary sessions must not require a TaskRun lookup")

    binding = SimpleNamespace(
        session_id="ordinary",
        user_id="user-1",
        agent_id="agent-ordinary",
    )
    monkeypatch.setattr(routes, "require_active_owned", _async_value(binding))
    monkeypatch.setattr(Session, "get", staticmethod(_async_value(session)))
    monkeypatch.setattr(TaskRun, "get", staticmethod(unexpected_run_lookup))

    response = await _request(_app(_context(allowed_agents=[])), "GET", "/sessions/ordinary/chat")

    assert response.status_code == 200
    assert response.json() == session.chat


async def test_task_run_chat_allows_key_inside_immutable_acl(monkeypatch):
    session = Session(_id="session-1", run_id="run-1", chat=[{"content": "allowed"}])
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-allowed"],
    )

    _patch_unbound_task_session(monkeypatch, session)
    monkeypatch.setattr(TaskRun, "get", staticmethod(_async_value(run)))

    response = await _request(_app(_context(allowed_agents=["agent-allowed"])), "GET", "/sessions/session-1/chat")

    assert response.status_code == 200
    assert response.json() == session.chat


async def test_legacy_team_session_list_filters_task_run_acl(monkeypatch):
    from cognitrix.api.routes import sessions as routes

    ordinary = Session(
        _id="ordinary",
        agent_id="agent-public",
        user_id="user-1",
        team_id="team-1",
        chat=[{"content": "ordinary"}],
    )
    protected = Session(
        _id="protected",
        team_id="team-1",
        run_id="run-1",
        chat=[{"content": "secret"}],
    )
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-private"],
    )

    monkeypatch.setattr(Session, "find", staticmethod(_async_value([ordinary, protected])))
    monkeypatch.setattr(routes, "_owned_sessions", _async_value([ordinary]))
    monkeypatch.setattr(
        routes.SessionOwnership,
        "find_one",
        staticmethod(_async_value(None)),
    )
    monkeypatch.setattr(TaskRun, "get", staticmethod(_async_value(run)))

    response = await _request(
        _teams_app(_context(allowed_agents=["agent-public"])),
        "GET",
        "/teams/team-1/sessions",
    )

    assert response.status_code == 200
    assert response.json() == [{
        "id": ordinary.id,
        "title": "New conversation",
        "datetime": ordinary.datetime,
        "updated_at": ordinary.json().get("updated_at"),
        "started_at": ordinary.started_at,
        "completed_at": ordinary.completed_at,
        "task_id": ordinary.task_id,
        "run_id": ordinary.run_id,
        "step_index": ordinary.step_index,
        "step_title": ordinary.step_title,
        "message_count": 1,
    }]
