from pathlib import Path
import tomllib

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_fly_gets_queue_and_concurrency_redis_urls_from_secrets():
    config = tomllib.loads(_text("fly.toml"))

    assert "CELERY_BROKER_URL" not in config["env"]
    assert "TASK_LIMIT_REDIS_URL" not in config["env"]


def test_runtime_image_runs_the_supervised_stack_without_bundled_redis():
    dockerfile = _text("Dockerfile")

    assert "redis-server" not in dockerfile
    assert "redis-tools" not in dockerfile
    assert "supervisor" in dockerfile
    assert "deploy/redis.conf" not in dockerfile
    assert "docker-entrypoint.sh" not in dockerfile
    assert "wait-for-redis.sh" not in dockerfile
    assert (
        'CMD ["/usr/bin/supervisord", "-c", '
        '"/etc/supervisor/cognitrix.conf"]'
    ) in dockerfile


def test_ci_builds_the_runtime_image_before_merge():
    workflow = _text(".github/workflows/test.yml")

    assert "container-build:" in workflow
    assert "docker build --tag cognitrix:test ." in workflow


def test_supervisor_runs_only_web_and_solo_worker():
    supervisor_config = _text("deploy/supervisord.conf")

    assert "[program:redis]" not in supervisor_config
    assert "[program:web]" in supervisor_config
    assert "[program:worker]" in supervisor_config
    assert "--pool=solo" in supervisor_config
    assert "wait-for-redis.sh" not in supervisor_config


@pytest.mark.asyncio
async def test_healthcheck_reports_an_unavailable_task_broker():
    from cognitrix.api.health import task_runtime_health

    with pytest.raises(HTTPException) as exc_info:
        await task_runtime_health(lambda: False)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Task runtime unavailable"
