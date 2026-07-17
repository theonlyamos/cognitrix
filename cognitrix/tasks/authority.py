"""Revalidated, secret-free authority reconstruction for durable workers."""

from cognitrix.errors import ExecutionControlError
from cognitrix.models import User
from cognitrix.models.api_key import APIKey
from cognitrix.tools.utils import ToolExecutionContext


class TaskAuthorityError(ExecutionControlError):
    """The authority that queued a run is no longer valid for execution."""


def _run_allowed(key: APIKey, run) -> bool:
    if run.acl_version != 1:
        return False
    if run.acl_team_id and not key.team_allowed(str(run.acl_team_id)):
        return False
    return all(key.agent_allowed(str(agent_id)) for agent_id in run.acl_agent_ids)


async def reconstruct_tool_context(run, task) -> ToolExecutionContext:
    """Resolve current authority policy from a persisted non-secret reference."""
    run_task_id = getattr(run, "task_id", None)
    task_id = getattr(task, "id", None)
    if (
        run_task_id is None
        or task_id is None
        or str(run_task_id) != str(task_id)
    ):
        raise TaskAuthorityError("durable run task binding is invalid")

    kind = str(getattr(run, "authority_kind", None) or "system")
    authority_id = getattr(run, "authority_id", None)
    task_id = str(task_id)

    if kind == "api_key":
        key = await APIKey.get(authority_id) if authority_id else None
        if (
            key is None
            or key.revoked
            or key.is_expired()
            or str(key.user_id) != str(run.requested_by or "")
            or not key.has_scope("run")
            or not _run_allowed(key, run)
        ):
            raise TaskAuthorityError("durable run authority is no longer valid")
        return ToolExecutionContext(
            user_id=str(key.user_id),
            api_key_id=str(key.id),
            task_id=task_id,
            run_id=str(run.id),
            scopes=frozenset(key.scopes or []),
            allowed_agents=(
                frozenset(key.allowed_agents) if key.allowed_agents else None
            ),
            allowed_teams=(
                frozenset(key.allowed_teams) if key.allowed_teams else None
            ),
        )

    if kind == "jwt":
        user_id = str(authority_id or run.requested_by or "")
        user = await User.get(user_id) if user_id else None
        if user is None or user_id != str(run.requested_by or ""):
            raise TaskAuthorityError("durable run authority is no longer valid")
        return ToolExecutionContext(
            user_id=user_id,
            task_id=task_id,
            run_id=str(run.id),
        )

    if kind not in {"system", "scheduler"}:
        raise TaskAuthorityError("durable run authority kind is invalid")

    # Internal/scheduled work is deliberately least-privilege.  It may execute
    # ordinary assigned capabilities but cannot use management tools whose
    # authorization checks require write/run scopes.
    return ToolExecutionContext(
        user_id=None,
        task_id=task_id,
        run_id=str(run.id),
        scopes=frozenset(),
        allowed_agents=frozenset(str(value) for value in run.acl_agent_ids),
        allowed_teams=(
            frozenset({str(run.acl_team_id)}) if run.acl_team_id else frozenset()
        ),
    )
