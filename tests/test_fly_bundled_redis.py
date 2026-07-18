from pathlib import Path
import tomllib

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_fly_uses_bundled_redis_for_queue_and_concurrency_leases():
    config = tomllib.loads(_text("fly.toml"))

    assert config["env"]["CELERY_BROKER_URL"] == "redis://127.0.0.1:6379/0"
    assert config["env"]["TASK_LIMIT_REDIS_URL"] == "redis://127.0.0.1:6379/1"


def test_runtime_image_installs_redis_and_runs_the_supervised_stack():
    dockerfile = _text("Dockerfile")
    git_attributes = _text(".gitattributes")

    assert "redis-server" in dockerfile
    assert "redis-tools" in dockerfile
    assert "supervisor" in dockerfile
    assert 'ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]' in dockerfile
    assert (
        'CMD ["/usr/bin/supervisord", "-c", '
        '"/etc/supervisor/cognitrix.conf"]'
    ) in dockerfile
    assert "*.sh text eol=lf" in git_attributes


def test_ci_builds_the_runtime_image_before_merge():
    workflow = _text(".github/workflows/test.yml")

    assert "container-build:" in workflow
    assert "docker build --tag cognitrix:test ." in workflow


def test_redis_is_localhost_only_and_persists_on_the_fly_volume():
    redis_config = _text("deploy/redis.conf")
    entrypoint = _text("deploy/docker-entrypoint.sh")

    assert "bind 127.0.0.1" in redis_config
    assert "protected-mode yes" in redis_config
    assert "appendonly yes" in redis_config
    assert "dir /root/.cognitrix/redis" in redis_config
    assert "install -d -o redis -g redis -m 700 /root/.cognitrix/redis" in entrypoint


def test_supervisor_runs_redis_web_and_solo_worker_and_waits_for_redis():
    supervisor_config = _text("deploy/supervisord.conf")
    wait_script = _text("deploy/wait-for-redis.sh")

    assert "[program:redis]" in supervisor_config
    assert "user=redis" in supervisor_config
    assert "[program:web]" in supervisor_config
    assert "[program:worker]" in supervisor_config
    assert "--pool=solo" in supervisor_config
    assert supervisor_config.count("/app/deploy/wait-for-redis.sh") == 2
    assert "redis-cli -h 127.0.0.1 ping" in wait_script


@pytest.mark.asyncio
async def test_healthcheck_reports_an_unavailable_task_broker():
    from cognitrix.api.health import task_runtime_health

    with pytest.raises(HTTPException) as exc_info:
        await task_runtime_health(lambda: False)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Task runtime unavailable"
