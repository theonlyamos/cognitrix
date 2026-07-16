"""Provider-neutral media service contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cognitrix.tools.utils import ArtifactRef

ImageVariant = Literal['original', 'vision', 'thumbnail']


class MediaError(ValueError):
    """Base error for media validation, access, and storage failures."""


class MediaAccessError(MediaError):
    """The requested artifact is outside the caller's ownership scope."""


class MediaNotFoundError(MediaError):
    """The requested artifact or retained variant is unavailable."""


class MediaValidationError(MediaError):
    """Image input cannot be safely decoded or normalized."""


class MediaQuotaError(MediaError):
    """Retaining an image would exceed a session limit."""


@dataclass(frozen=True)
class MediaOwnership:
    session_id: str | None
    user_id: str | None
    agent_id: str | None


@dataclass(frozen=True)
class StagedAttachment:
    path: Path
    filename: str
    declared_mime: str
    size_bytes: int


@dataclass(frozen=True)
class ResolvedImage:
    ref: ArtifactRef
    variant: ImageVariant
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class ResolvedMediaFile:
    ref: ArtifactRef
    variant: ImageVariant
    mime_type: str
    filename: str
    path: Path
