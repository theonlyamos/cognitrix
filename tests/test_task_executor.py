"""Focused contracts for ephemeral task-step execution and live events."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from cognitrix.tasks.executor import TaskStepExecutor
from cognitrix.tasks.runtime import AgentRuntimeSnapshot, LLMRuntimeSnapshot


def _snapshot():
    return AgentRuntimeSnapshot(
        agent_id="agent-1",
        name="Worker",
        system_prompt="Frozen prompt",
        llm=LLMRuntimeSnapshot(provider="openai", model="m"),
    )


@pytest.mark.asyncio
async def test_executor_emits_live_turn_events_and_returns_typed_artifacts(monkeypatch):
    from cognitrix.artifacts import Artifact
    from cognitrix.tasks import executor as module
    from cognitrix.tools.utils import ToolExecutionContext

    session_kwargs = []
    call_kwargs = []

    class FakeSession:
        def __init__(self, **kwargs):
            session_kwargs.append(kwargs)
            self.id = "session-1"

        async def __call__(self, prompt, agent, **kwargs):
            call_kwargs.append(kwargs)
            output = kwargs["output"]
            await output({"content": "first "})
            await output({
                "type": "tool",
                "status": "started",
                "tool_name": "Search",
                "tool_call_id": "call-1",
                "params": "query",
            })
            await output({
                "type": "tool",
                "status": "completed",
                "tool_name": "Search",
                "tool_call_id": "call-1",
                "result": "found",
                "artifacts": [{
                    "id": "artifact-tool",
                    "mime_type": "text/plain",
                    "filename": "research.txt",
                }],
            })
            await output({
                "content": "second",
                "artifacts": [{"id": "artifact-provider", "mime_type": "image/png"}],
            })

    class RecordingEmitter:
        def __init__(self):
            self.events = []

        async def flush_text(self, **kwargs):
            self.events.append(("flush", kwargs))

        async def text_delta(self, **kwargs):
            self.events.append(("text_delta", kwargs))

        async def emit(self, kind, **kwargs):
            self.events.append((kind, kwargs))

    checkpoints = 0

    async def checkpoint():
        nonlocal checkpoints
        checkpoints += 1

    emitter = RecordingEmitter()
    monkeypatch.setattr(module, "Session", FakeSession)
    monkeypatch.setattr(
        module,
        "instantiate_runtime",
        lambda snapshot, tool_resolver=None: SimpleNamespace(name=snapshot.name),
    )
    durable_artifacts = {
        "artifact-tool": Artifact(
            _id="artifact-tool",
            session_id="session-1",
            run_id="run-1",
            user_id="owner-1",
            storage_key="safe/research.txt",
            mime_type="text/plain",
            filename="research.txt",
        ),
        "artifact-provider": Artifact(
            _id="artifact-provider",
            session_id="session-1",
            run_id="run-1",
            user_id="owner-1",
            storage_key="safe/image.png",
            mime_type="image/png",
        ),
    }

    async def get_artifact(artifact_id):
        return durable_artifacts.get(artifact_id)

    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))
    executor = TaskStepExecutor(
        _snapshot(),
        task_id="task-1",
        run_id="run-1",
        step_index=2,
        step_title="Research",
        emitter=emitter,
        cancel_check=checkpoint,
    )

    result = await executor.execute(
        "do it",
        attempt=3,
        tool_context=ToolExecutionContext(
            user_id="owner-1",
            task_id="task-1",
            run_id="run-1",
        ),
    )

    assert result.text == "first second"
    assert [item.id for item in result.artifacts] == [
        "artifact-tool",
        "artifact-provider",
    ]
    assert result.artifacts[0].name == "research.txt"
    assert result.usage.llm_calls == 0
    assert len(session_kwargs) == 1
    attempt_session_id = session_kwargs[0].pop('_id')
    assert str(UUID(attempt_session_id)) == attempt_session_id
    assert session_kwargs == [{
        "task_id": "task-1",
        "run_id": "run-1",
        "step_index": 2,
        "step_title": "Research",
        "agent_id": "agent-1",
    }]
    assert call_kwargs[0]["interface"] == "task"
    assert call_kwargs[0]["record_history"] is True
    assert call_kwargs[0]["persist_history"] is False
    assert call_kwargs[0]["compact_history"] is False
    assert checkpoints == 6
    assert [kind for kind, _ in emitter.events] == [
        "text_delta",
        "flush",
        "tool_started",
        "flush",
        "tool_completed",
        "text_delta",
        "flush",
        "turn_completed",
    ]
    completed = emitter.events[-1][1]
    assert completed["data"] == {
        "turn_id": "session-1:3",
        "attempt": 3,
    }


@pytest.mark.asyncio
async def test_executor_checks_cancellation_before_creating_attempt(monkeypatch):
    from cognitrix.tasks import executor as module

    created = False

    async def cancelled():
        raise RuntimeError("cancel checkpoint")

    def instantiate(*_args, **_kwargs):
        nonlocal created
        created = True
        return SimpleNamespace()

    monkeypatch.setattr(module, "instantiate_runtime", instantiate)
    executor = TaskStepExecutor(_snapshot(), cancel_check=cancelled)

    with pytest.raises(RuntimeError, match="cancel checkpoint"):
        await executor.execute("do it")
    assert created is False


def test_executor_assigns_each_ephemeral_attempt_a_unique_session_id(monkeypatch):
    from cognitrix.tasks import executor as module

    monkeypatch.setattr(
        module,
        "instantiate_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    executor = TaskStepExecutor(_snapshot(), task_id="task-1", run_id="run-1")

    _, first = executor.create_attempt()
    _, second = executor.create_attempt()

    assert isinstance(first.id, str) and first.id
    assert isinstance(second.id, str) and second.id
    assert first.id != second.id


@pytest.mark.asyncio
async def test_executor_exposes_canonical_durable_artifact_identity(monkeypatch):
    from cognitrix.tasks import executor as module
    from cognitrix.artifacts import Artifact
    from cognitrix.tools.utils import ToolExecutionContext

    class FakeSession:
        def __init__(self, **_kwargs):
            self.id = "ephemeral-step-session"

        async def __call__(self, _prompt, _agent, **kwargs):
            await kwargs["output"]({
                "type": "tool",
                "status": "completed",
                "tool_name": "Generate Image",
                "tool_call_id": "call-1",
                "result": "generated",
                "artifacts": [{
                    "id": "image/one",
                    "name": "forged name.exe",
                    "filename": "forged name.exe",
                    "mime_type": "application/x-forged",
                    "uri": "https://provider.invalid/private",
                }],
            })

    durable = Artifact(
        _id="image/one",
        session_id="ephemeral-step-session",
        run_id="run/one",
        user_id="owner-1",
        storage_key="safe/image.png",
        filename="moon owl.png",
        mime_type="image/png",
    )

    async def get_artifact(artifact_id):
        assert artifact_id == "image/one"
        return durable

    monkeypatch.setattr(module, "Session", FakeSession)
    monkeypatch.setattr(
        module,
        "instantiate_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))

    result = await TaskStepExecutor(
        _snapshot(),
        task_id="task/one",
        run_id="run/one",
    ).execute(
        "generate it",
        tool_context=ToolExecutionContext(
            user_id="owner-1",
            task_id="task/one",
            run_id="run/one",
        ),
    )

    assert result.artifacts[0].model_dump() == {
        "id": "image/one",
        "name": "moon owl.png",
        "mime_type": "image/png",
        "uri": "/tasks/task%2Fone/runs/run%2Fone/artifacts/image%2Fone",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("durable_run_id", "durable_user_id"),
    [
        (None, "owner-1"),
        ("other-run", "owner-1"),
        ("run-1", "other-owner"),
    ],
)
async def test_executor_omits_artifacts_without_exact_durable_binding(
    monkeypatch,
    durable_run_id,
    durable_user_id,
):
    from cognitrix.artifacts import Artifact
    from cognitrix.tasks import executor as module
    from cognitrix.tools.utils import ToolExecutionContext

    class FakeSession:
        def __init__(self, **_kwargs):
            self.id = "ephemeral-step-session"

        async def __call__(self, _prompt, _agent, **kwargs):
            await kwargs["output"]({
                "type": "tool",
                "status": "completed",
                "tool_name": "Generate Image",
                "tool_call_id": "call-1",
                "result": "generated",
                "artifacts": [{"id": "artifact-1", "mime_type": "image/png"}],
            })

    durable = Artifact(
        _id="artifact-1",
        session_id="ephemeral-step-session",
        run_id=durable_run_id,
        user_id=durable_user_id,
        storage_key="safe/image.png",
        filename="image.png",
        mime_type="image/png",
    )

    async def get_artifact(_artifact_id):
        return durable

    monkeypatch.setattr(module, "Session", FakeSession)
    monkeypatch.setattr(
        module,
        "instantiate_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))

    result = await TaskStepExecutor(
        _snapshot(),
        task_id="task-1",
        run_id="run-1",
    ).execute(
        "generate it",
        tool_context=ToolExecutionContext(
            user_id="owner-1",
            task_id="task-1",
            run_id="run-1",
        ),
    )

    assert result.artifacts == []


@pytest.mark.asyncio
async def test_executor_omits_caller_supplied_missing_artifact(monkeypatch):
    from cognitrix.artifacts import Artifact
    from cognitrix.tasks import executor as module
    from cognitrix.tools.utils import ToolExecutionContext

    class FakeSession:
        def __init__(self, **_kwargs):
            self.id = "ephemeral-step-session"

        async def __call__(self, _prompt, _agent, **kwargs):
            await kwargs["output"]({
                "artifacts": [{
                    "id": "does-not-exist",
                    "name": "forged.pdf",
                    "mime_type": "application/pdf",
                }],
            })

    async def get_artifact(_artifact_id):
        return None

    monkeypatch.setattr(module, "Session", FakeSession)
    monkeypatch.setattr(
        module,
        "instantiate_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))

    result = await TaskStepExecutor(
        _snapshot(),
        task_id="task-1",
        run_id="run-1",
    ).execute(
        "generate it",
        tool_context=ToolExecutionContext(
            user_id="owner-1",
            task_id="task-1",
            run_id="run-1",
        ),
    )

    assert result.artifacts == []


@pytest.mark.asyncio
async def test_executor_never_persists_or_emits_streamed_reasoning(monkeypatch):
    from cognitrix.tasks import executor as module

    class FakeSession:
        def __init__(self, **_kwargs):
            self.id = "session-reasoning"

        async def __call__(self, _prompt, _agent, **kwargs):
            output = kwargs["output"]
            # Exercise tags split across provider chunks as well as answer text
            # following the closing tag in the same chunk.
            for chunk in ("<thi", "nk>private chain", " of thought</thi", "nk>public ", "answer"):
                await output({"content": chunk})

    class RecordingEmitter:
        def __init__(self):
            self.deltas = []

        async def flush_text(self, **_kwargs):
            pass

        async def text_delta(self, **kwargs):
            self.deltas.append(kwargs["content"])

        async def emit(self, *_args, **_kwargs):
            pass

    emitter = RecordingEmitter()
    monkeypatch.setattr(module, "Session", FakeSession)
    monkeypatch.setattr(
        module,
        "instantiate_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    result = await TaskStepExecutor(_snapshot(), emitter=emitter).execute("do it")

    assert result.text == "public answer"
    assert "private" not in result.text
    assert "private" not in "".join(emitter.deltas)
    assert "<think>" not in "".join(emitter.deltas)
