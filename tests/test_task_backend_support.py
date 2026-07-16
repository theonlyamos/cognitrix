import pytest


@pytest.mark.asyncio
async def test_mongodb_is_rejected_before_a_run_head_is_reserved(monkeypatch):
    from odbms import DBMS

    from cognitrix.tasks.repository import (
        RunRepository,
        UnsupportedDurableTaskBackend,
    )

    monkeypatch.setattr(
        DBMS,
        "Database",
        type("Mongo", (), {"dbms": "mongodb"})(),
    )
    repository = RunRepository()

    async def must_not_initialize_schema():
        raise AssertionError("unsupported backends must fail before persistence")

    monkeypatch.setattr(
        repository,
        "_ensure_indexes",
        must_not_initialize_schema,
    )

    with pytest.raises(
        UnsupportedDurableTaskBackend,
        match="SQLite, PostgreSQL, or MySQL",
    ):
        await repository.create_queued(task_id="task-mongo")
