"""Team task-list projections must resolve live Task rows."""

from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from cognitrix.common.security import AuthContext, crud_scope
from cognitrix.tasks.base import Task
from cognitrix.teams.base import Team


def _app() -> FastAPI:
    from cognitrix.api.routes.teams import teams_api

    app = FastAPI()
    app.include_router(teams_api)
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)
    app.dependency_overrides[crud_scope] = lambda: ctx
    return app


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()),
        base_url="http://test",
    ) as client:
        return await client.get(path)


async def test_team_tasks_resolve_live_rows_and_filter_deleted_or_missing(
    monkeypatch,
):
    monkeypatch.setenv("CELERY_WORKER_MODE", "1")
    stale_live = Task(
        _id="task-live",
        title="stale title",
        description="stale",
        team_id="team-1",
    )
    stale_deleted = Task(
        _id="task-deleted",
        title="deleted snapshot",
        description="stale",
        team_id="team-1",
    )
    stale_missing = Task(
        _id="task-missing",
        title="missing snapshot",
        description="stale",
        team_id="team-1",
    )
    team = Team(
        _id="team-1",
        name="Team",
        description="Test",
        tasks=[stale_live, stale_deleted, stale_missing],
    )
    live = Task(
        _id=stale_live.id,
        title="authoritative title",
        description="fresh",
        team_id=team.id,
        callback_url="https://example.test/secret",
        callback_key_id="key-secret",
    )
    deleted = Task(
        _id=stale_deleted.id,
        title="deleted",
        description="hidden",
        team_id=team.id,
        deleted_at="2030-01-01 00:00:00",
    )
    rows = {live.id: live, deleted.id: deleted, stale_missing.id: None}

    async def get_team(team_id):
        return team if team_id == team.id else None

    async def get_task(task_id):
        return rows[task_id]

    monkeypatch.setattr(Team, "get", staticmethod(get_team))
    monkeypatch.setattr(Task, "get", staticmethod(get_task))

    response = await _get(f"/teams/{team.id}/tasks")

    assert response.status_code == 200
    assert len(response.json()) == 1
    projected = response.json()[0]
    assert projected.get("id", projected.get("_id")) == live.id
    assert projected["title"] == "authoritative title"
    assert "callback_url" not in projected
    assert "callback_key_id" not in projected
    assert "deleted_at" not in projected


async def test_team_tasks_filter_rows_authoritatively_reassigned_to_another_team(
    monkeypatch,
):
    monkeypatch.setenv("CELERY_WORKER_MODE", "1")
    snapshot = Task(
        _id="task-moved",
        title="old assignment",
        description="stale",
        team_id="team-1",
    )
    team = Team(
        _id="team-1",
        name="Team",
        description="Test",
        tasks=[snapshot],
    )
    moved = Task(
        _id=snapshot.id,
        title="new assignment",
        description="fresh",
        team_id="team-2",
    )

    async def get_team(_team_id):
        return team

    async def get_task(_task_id):
        return moved

    monkeypatch.setattr(Team, "get", staticmethod(get_team))
    monkeypatch.setattr(Task, "get", staticmethod(get_task))

    response = await _get(f"/teams/{team.id}/tasks")

    assert response.status_code == 200
    assert response.json() == []
