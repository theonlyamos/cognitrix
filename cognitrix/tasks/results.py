"""Typed task-step results with transparent legacy decoding."""

import json
from decimal import Decimal
from typing import Any, Self
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _MappingModel(BaseModel):
    """Small compatibility shim for callers that previously handled dicts."""

    model_config = ConfigDict(extra="allow")

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class ArtifactRef(_MappingModel):
    id: str
    name: str | None = None
    mime_type: str | None = None
    uri: str | None = None


def canonical_artifact_ref(
    artifact: Any,
    *,
    task_id: str,
    run_id: str,
) -> ArtifactRef:
    """Build a client-safe reference solely from a loaded Artifact row."""
    artifact_id = str(artifact.id)
    return ArtifactRef(
        id=artifact_id,
        name=artifact.filename or artifact_id,
        mime_type=artifact.mime_type or "application/octet-stream",
        uri=task_artifact_uri(task_id, run_id, artifact_id),
    )


class CitationRef(_MappingModel):
    url: str
    title: str | None = None


class UsageSummary(_MappingModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_attempts: int = 0
    duration_seconds: float = 0.0
    cost_usd: Decimal = Decimal("0")


def task_artifact_uri(task_id: str, run_id: str, artifact_id: str) -> str:
    """Return the authenticated API path for one run-owned artifact."""
    return (
        f"/tasks/{quote(str(task_id), safe='')}/runs/"
        f"{quote(str(run_id), safe='')}/artifacts/"
        f"{quote(str(artifact_id), safe='')}"
    )


class StepResult(BaseModel):
    """Structured output persisted by a task step.

    Historical rows store a bare string. The before-validator deliberately
    accepts that representation so existing runs can resume in place.
    """

    text: str = ""
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    structured_data: dict[str, Any] | None = None
    citations: list[CitationRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageSummary = Field(default_factory=UsageSummary)

    @model_validator(mode="before")
    @classmethod
    def _decode_legacy(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, str):
            # Historical result columns were untyped text. JSON-looking output
            # from an agent is still literal output, never a typed envelope.
            return {"text": value}
        return value

    @classmethod
    def from_stored(cls, value: str | dict[str, Any] | Self | None) -> Self:
        if isinstance(value, cls):
            return value
        return cls.model_validate(value)

    def dependency_text(self, max_chars: int) -> str:
        """Return bounded text plus lightweight artifact/citation references."""
        parts = [self.text]
        if self.artifacts:
            artifact_refs = [
                {
                    key: value
                    for key, value in (
                        ("id", artifact.id),
                        ("name", artifact.name),
                        ("mime_type", artifact.mime_type),
                        ("uri", artifact.uri),
                    )
                    if value is not None
                }
                for artifact in self.artifacts
            ]
            parts.append(
                "Artifacts (reference inputs by id): " + json.dumps(
                    artifact_refs,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            )
        if self.citations:
            parts.append("Sources: " + ", ".join(item.url for item in self.citations))
        return "\n".join(part for part in parts if part)[:max(0, max_chars)]
