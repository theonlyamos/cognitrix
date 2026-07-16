from decimal import Decimal


def test_step_result_accepts_legacy_string_and_round_trips():
    from cognitrix.tasks.results import StepResult, UsageSummary

    result = StepResult.model_validate("legacy result")

    assert result.text == "legacy result"
    assert result.artifacts == []
    assert result.structured_data is None
    assert result.citations == []
    assert result.warnings == []
    assert result.usage == UsageSummary()
    assert StepResult.model_validate(result.model_dump(mode="json")) == result


def test_step_result_keeps_json_looking_historical_strings_literal():
    from cognitrix.tasks.results import StepResult

    historical = '{"text":"not a typed envelope","warnings":["model output"]}'

    result = StepResult.from_stored(historical)

    assert result.text == historical
    assert result.warnings == []


def test_step_result_preserves_typed_metadata():
    from cognitrix.tasks.results import ArtifactRef, CitationRef, StepResult, UsageSummary

    payload = {
        "text": "finished",
        "artifacts": [{"id": "artifact-1", "mime_type": "image/png"}],
        "structured_data": {"count": 2, "items": ["a", "b"]},
        "citations": [{"url": "https://example.test/source", "title": "Source"}],
        "warnings": ["partial source coverage"],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cost_usd": Decimal("0.0012"),
        },
    }

    result = StepResult.model_validate(payload)
    restored = StepResult.model_validate(result.model_dump(mode="json"))

    assert restored.text == "finished"
    assert isinstance(restored.artifacts[0], ArtifactRef)
    assert restored.artifacts[0]["id"] == "artifact-1"
    assert restored.structured_data == payload["structured_data"]
    assert isinstance(restored.citations[0], CitationRef)
    assert restored.citations[0]["url"] == payload["citations"][0]["url"]
    assert restored.warnings == payload["warnings"]
    assert isinstance(restored.usage, UsageSummary)
    assert restored.usage["prompt_tokens"] == 11
    assert Decimal(str(restored.usage["cost_usd"])) == Decimal("0.0012")


def test_task_run_declares_typed_result_and_durable_identity_fields():
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.run import TaskRun, TaskRunStatus, final_result_update

    expected_fields = {
        "requested_by",
        "resume_from_run_id",
        "result_data",
        "queue_job_id",
        "lease_owner",
        "lease_generation",
        "heartbeat_at",
        "lease_expires_at",
        "cancel_requested_at",
        "version",
        "next_event_sequence",
        "event_outbox",
        "budget",
        "usage",
        "error_code",
    }
    assert expected_fields <= set(TaskRun.model_fields)

    final = StepResult(text="typed final text", warnings=["reviewed"])
    run = TaskRun(
        task_id="task-1",
        status=TaskRunStatus.QUEUED,
        requested_by="user-1",
        resume_from_run_id="run-previous",
        queue_job_id="job-1",
        **final_result_update(final),
    )
    restored = TaskRun(**run.model_dump(mode="json"))

    assert restored.status == TaskRunStatus.QUEUED
    assert restored.requested_by == "user-1"
    assert restored.resume_from_run_id == "run-previous"
    assert restored.result == "typed final text"
    assert isinstance(restored.result_data, StepResult)
    assert restored.result_data.text == "typed final text"
    assert restored.result_data.warnings == ["reviewed"]
    assert restored.queue_job_id == "job-1"


def test_final_result_update_keeps_text_and_payload_atomic():
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.run import final_result_update

    result = StepResult(text="answer", structured_data={"ok": True})
    patch = final_result_update(result)

    assert patch["result"] == "answer"
    assert patch["result_data"] == result.model_dump(mode="json")


def test_task_run_null_new_fields_coerce_to_safe_defaults():
    from cognitrix.tasks.run import TaskRun

    run = TaskRun(
        task_id="task-1",
        plan=None,
        event_outbox=None,
        budget=None,
        usage=None,
        version=None,
        next_event_sequence=None,
        lease_generation=None,
    )

    assert run.plan == []
    assert run.event_outbox == []
    assert run.budget == {}
    assert run.usage == {}
    assert run.version == 0
    assert run.next_event_sequence == 0
    assert run.lease_generation == 0
