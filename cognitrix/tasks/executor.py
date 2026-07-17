"""Task-specific turn execution, separate from persisted chat sessions."""

from collections.abc import Awaitable, Callable
from typing import Any

from cognitrix.artifacts import bound_task_run_artifact
from cognitrix.sessions.base import Session
from cognitrix.tasks.accounting import capture_task_usage
from cognitrix.tasks.events import TaskRunEventEmitter
from cognitrix.tasks.results import (
    ArtifactRef,
    StepResult,
    UsageSummary,
    canonical_artifact_ref,
)
from cognitrix.tasks.runtime import AgentRuntimeSnapshot, instantiate_runtime


class _AnswerChunkFilter:
    """Drop provider reasoning channels while preserving answer deltas.

    The shared streaming transport represents reasoning as ``<think>`` blocks.
    Task output is durable and may be fed to later agents, so it must never
    capture or publish those blocks.  A small carry buffer also handles tags
    split across arbitrary provider chunks.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._reasoning = False
        self._carry = ""

    @staticmethod
    def _possible_prefix(value: str, tag: str) -> int:
        return max(
            (size for size in range(1, min(len(value), len(tag) - 1) + 1)
             if value.endswith(tag[:size])),
            default=0,
        )

    def feed(self, chunk: str) -> str:
        pending = self._carry + str(chunk)
        self._carry = ""
        answer: list[str] = []
        while pending:
            if self._reasoning:
                close_at = pending.find(self._CLOSE)
                if close_at < 0:
                    keep = self._possible_prefix(pending, self._CLOSE)
                    self._carry = pending[-keep:] if keep else ""
                    return "".join(answer)
                pending = pending[close_at + len(self._CLOSE):]
                self._reasoning = False
                continue

            open_at = pending.find(self._OPEN)
            if open_at >= 0:
                answer.append(pending[:open_at])
                pending = pending[open_at + len(self._OPEN):]
                self._reasoning = True
                continue
            keep = self._possible_prefix(pending, self._OPEN)
            if keep:
                answer.append(pending[:-keep])
                self._carry = pending[-keep:]
            else:
                answer.append(pending)
            return "".join(answer)
        return "".join(answer)

    def finish(self) -> str:
        trailing = "" if self._reasoning else self._carry
        self._carry = ""
        return trailing


class TaskStepExecutor:
    """Materialize one fresh runtime and keep its protocol history in memory."""

    def __init__(
        self,
        snapshot: AgentRuntimeSnapshot,
        *,
        tool_resolver: Callable[[str], Any] | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        step_index: int | None = None,
        step_title: str | None = None,
        emitter: TaskRunEventEmitter | None = None,
        cancel_check: Callable[[], Awaitable[None]] | None = None,
    ):
        self.snapshot = snapshot
        self.tool_resolver = tool_resolver
        self.task_id = task_id
        self.run_id = run_id
        self.step_index = step_index
        self.step_title = step_title
        self.emitter = emitter
        self.cancel_check = cancel_check

    def create_attempt(self) -> tuple[Any, Session]:
        agent = instantiate_runtime(self.snapshot, tool_resolver=self.tool_resolver)
        return agent, Session(
            task_id=self.task_id,
            run_id=self.run_id,
            step_index=self.step_index,
            step_title=self.step_title,
            agent_id=self.snapshot.agent_id,
        )

    async def _checkpoint(self) -> None:
        if self.cancel_check is not None:
            await self.cancel_check()

    async def execute(
        self,
        prompt: str,
        *,
        tool_context=None,
        attempt: int = 1,
    ) -> StepResult:
        await self._checkpoint()
        agent, session = self.create_attempt()
        chunks: list[str] = []
        artifacts: list[ArtifactRef] = []
        artifact_ids: set[str] = set()
        answer_filter = _AnswerChunkFilter()
        turn_id = f"{session.id}:{attempt}"

        async def capture(payload=None, *args, **kwargs) -> None:
            await self._checkpoint()
            if not isinstance(payload, dict):
                return
            raw_artifacts = payload.get("artifacts") or []
            if isinstance(raw_artifacts, dict):
                raw_artifacts = [raw_artifacts]
            for item in raw_artifacts:
                if isinstance(item, dict) and item.get("id"):
                    artifact_id = str(item["id"])
                    if artifact_id in artifact_ids:
                        continue
                    artifact_ids.add(artifact_id)
                    if (
                        self.task_id is None
                        or self.run_id is None
                        or tool_context is None
                        or str(getattr(tool_context, "task_id", "")) != str(self.task_id)
                        or str(getattr(tool_context, "run_id", "")) != str(self.run_id)
                    ):
                        continue
                    durable = await bound_task_run_artifact(
                        artifact_id,
                        run_id=self.run_id,
                        user_id=getattr(tool_context, "user_id", None),
                    )
                    if durable is None:
                        continue
                    artifacts.append(canonical_artifact_ref(
                        durable,
                        task_id=self.task_id,
                        run_id=self.run_id,
                    ))
            if payload.get("type") == "tool":
                if self.emitter is not None:
                    await self.emitter.flush_text(
                        session_id=session.id,
                        turn_id=turn_id,
                    )
                    status = str(payload.get("status") or "")
                    kind = "tool_started" if status == "started" else "tool_completed"
                    data = {
                        "turn_id": turn_id,
                        "tool_call_id": payload.get("tool_call_id"),
                        "tool_name": payload.get("tool_name"),
                    }
                    if kind == "tool_started":
                        data["params"] = payload.get("params") or ""
                    else:
                        data["result"] = payload.get("result") or ""
                        data["status"] = "error" if status == "error" else "done"
                    await self.emitter.emit(
                        kind,
                        session_id=session.id,
                        step_index=self.step_index,
                        agent_name=self.snapshot.name,
                        data=data,
                    )
                return

            content = payload.get("content")
            if content:
                text = answer_filter.feed(str(content))
                if not text:
                    return
                chunks.append(text)
                if self.emitter is not None:
                    await self.emitter.text_delta(
                        session_id=session.id,
                        step_index=self.step_index,
                        agent_name=self.snapshot.name,
                        turn_id=turn_id,
                        attempt=attempt,
                        content=text,
                    )

        async with capture_task_usage() as usage:
            try:
                await session(
                    prompt,
                    agent,
                    interface="task",
                    stream=True,
                    output=capture,
                    wsquery={},
                    record_history=True,
                    persist_history=False,
                    compact_history=False,
                    tool_context=tool_context,
                )
            finally:
                if self.emitter is not None:
                    await self.emitter.flush_text(
                        session_id=session.id,
                        turn_id=turn_id,
                    )
        trailing = answer_filter.finish()
        if trailing:
            chunks.append(trailing)
            if self.emitter is not None:
                await self.emitter.text_delta(
                    session_id=session.id,
                    step_index=self.step_index,
                    agent_name=self.snapshot.name,
                    turn_id=turn_id,
                    attempt=attempt,
                    content=trailing,
                )
                await self.emitter.flush_text(
                    session_id=session.id,
                    turn_id=turn_id,
                )
        await self._checkpoint()
        if self.emitter is not None:
            await self.emitter.emit(
                "turn_completed",
                session_id=session.id,
                step_index=self.step_index,
                agent_name=self.snapshot.name,
                data={"turn_id": turn_id, "attempt": attempt},
            )
        return StepResult(
            text="".join(chunks).strip(),
            artifacts=artifacts,
            usage=UsageSummary.model_validate(usage.snapshot()),
        )
