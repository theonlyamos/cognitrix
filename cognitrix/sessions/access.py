"""Authorization helpers for persisted chat sessions.

Ordinary chat sessions are private to the durable ``Session.user_id`` owner.
Task-run step sessions are different: their immutable enqueue-time TaskRun ACL
is authoritative, so mutable session ownership never overrides it.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

from cognitrix.tasks.run import TaskRun, run_acl_allowed

_RUN_NOT_PROVIDED = object()


def authorization_user_id(authorization: Any) -> str | None:
    """Return the stable authenticated user id, never a credential id."""
    user = getattr(authorization, "user", None)
    value = getattr(user, "id", None)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def browser_authorization(user_id: str | None) -> Any:
    """Build the JWT-equivalent authorization used by a browser SSE/WS stream."""
    normalized = str(user_id).strip() if user_id is not None else ""
    return SimpleNamespace(
        user=SimpleNamespace(id=normalized or None),
        api_key=None,
        agent_allowed=lambda _value: True,
        team_allowed=lambda _value: True,
    )


async def session_access_allowed(
    session: Any,
    authorization: Any,
    *,
    run: TaskRun | None | object = _RUN_NOT_PROVIDED,
) -> bool:
    """Authorize one session using its authoritative ownership source."""
    caller_id = authorization_user_id(authorization)
    if caller_id is None:
        return False
    run_id = getattr(session, "run_id", None)
    if run_id:
        resolved = (
            await TaskRun.get(run_id)
            if run is _RUN_NOT_PROVIDED
            else run
        )
        return bool(resolved is not None and run_acl_allowed(resolved, authorization))

    owner_id = getattr(session, "user_id", None)
    return bool(owner_id and caller_id and str(owner_id) == caller_id)


async def visible_sessions(sessions: list[Any], authorization: Any) -> list[Any]:
    """Filter a list without one TaskRun lookup per session."""
    run_ids = list(dict.fromkeys(
        str(session.run_id)
        for session in sessions
        if getattr(session, "run_id", None)
    ))
    runs = await asyncio.gather(*(TaskRun.get(run_id) for run_id in run_ids))
    run_by_id = dict(zip(run_ids, runs, strict=True))

    visible = []
    for session in sessions:
        run_id = getattr(session, "run_id", None)
        run = run_by_id.get(str(run_id)) if run_id else None
        if await session_access_allowed(session, authorization, run=run):
            visible.append(session)
    return visible
