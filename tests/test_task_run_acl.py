from types import SimpleNamespace

from cognitrix.tasks.run import TaskRun, run_acl_allowed, same_run_acl


def _authorization(*, teams=(), agents=()):
    return SimpleNamespace(
        team_allowed=lambda value: value in teams,
        agent_allowed=lambda value: value in agents,
    )


def test_historical_run_authorization_uses_immutable_acl_snapshot():
    run = TaskRun(
        task_id="task-1",
        acl_version=1,
        acl_team_id="team-era-a",
        acl_agent_ids=["agent-era-a"],
    )

    assert run_acl_allowed(
        run,
        _authorization(teams={"team-era-a"}, agents={"agent-era-a"}),
    )
    assert not run_acl_allowed(
        run,
        _authorization(teams={"team-era-b"}, agents={"agent-era-b"}),
    )


def test_legacy_acl_and_changed_acl_cannot_seed_resume():
    legacy = TaskRun(task_id="task-1")
    current = TaskRun(
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-current"],
    )
    prior = TaskRun(
        task_id="task-1",
        acl_version=1,
        acl_agent_ids=["agent-prior"],
    )

    assert not same_run_acl(current, legacy)
    assert not same_run_acl(current, prior)


def test_legacy_run_is_visible_only_to_original_jwt_owner():
    legacy = TaskRun(task_id="task-1", requested_by="owner-a")
    owner = SimpleNamespace(user=SimpleNamespace(id="owner-a"), api_key=None)
    other = SimpleNamespace(user=SimpleNamespace(id="owner-b"), api_key=None)
    key = SimpleNamespace(
        user=SimpleNamespace(id="owner-a"),
        api_key=SimpleNamespace(id="key-1"),
    )

    assert run_acl_allowed(legacy, owner)
    assert not run_acl_allowed(legacy, other)
    assert not run_acl_allowed(legacy, key)
    assert not run_acl_allowed(TaskRun(task_id="task-1"), owner)
