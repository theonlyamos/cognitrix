"""Acceptance coverage for the artifact-backed image editing pipeline."""

from dataclasses import dataclass, field

import pytest

from cognitrix.artifacts import reset_session, set_session
from cognitrix.media.types import MediaNotFoundError, MediaOwnership, ResolvedImage
from cognitrix.providers.gemini_image import ProviderImage
from cognitrix.tools.utils import (
    ArtifactRef,
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)


@dataclass
class RecordingProvider:
    outputs: list[bytes]
    calls: list[dict] = field(default_factory=list)

    async def generate(self, prompt, *, source, aspect_ratio):
        self.calls.append({
            "prompt": prompt,
            "source": source,
            "aspect_ratio": aspect_ratio,
        })
        return ProviderImage(self.outputs.pop(0), "image/png")


@dataclass
class OwnedMemoryMedia:
    images: dict[str, tuple[MediaOwnership, ResolvedImage]] = field(default_factory=dict)
    stores: list[dict] = field(default_factory=list)
    resolves: list[tuple[str, MediaOwnership, str]] = field(default_factory=list)

    def add(self, identifier, data, ownership, *, origin="uploaded"):
        ref = ArtifactRef(
            id=identifier,
            mime_type="image/png",
            filename=f"{identifier}.png",
            width=8,
            height=6,
            origin=origin,
        )
        self.images[identifier] = (
            ownership,
            ResolvedImage(ref, "original", "image/png", data),
        )
        return ref

    async def resolve_image(self, artifact_id, ownership, variant="original"):
        self.resolves.append((str(artifact_id), ownership, variant))
        entry = self.images.get(str(artifact_id))
        if entry is None or entry[0] != ownership:
            raise MediaNotFoundError("Artifact was not found")
        resolved = entry[1]
        return ResolvedImage(
            resolved.ref,
            variant,
            resolved.mime_type,
            resolved.data,
        )

    async def store_generated_image(self, data, metadata, ownership):
        identifier = f"generated-{len(self.stores) + 1}"
        ref = self.add(identifier, data, ownership, origin="generated")
        self.stores.append({
            "data": data,
            "metadata": metadata,
            "ownership": ownership,
            "ref": ref,
        })
        return ref

    async def list_recent_refs(self, session_id, ownership, limit=3):
        return [
            resolved.ref
            for owner, resolved in self.images.values()
            if owner == ownership and owner.session_id == session_id
        ][-limit:]


@pytest.fixture
def bound_turn():
    artifact_token = set_session("session-1", "agent-1", "user-1")
    execution_token = set_execution_context(ToolExecutionContext(user_id="user-1"))
    try:
        yield MediaOwnership("session-1", "user-1", "agent-1")
    finally:
        reset_execution_context(execution_token)
        reset_session(artifact_token)


async def _selected_call(identifier, callback):
    token = set_execution_context(ToolExecutionContext(
        user_id="user-1", selected_image_artifact_id=identifier
    ))
    try:
        return await callback()
    finally:
        reset_execution_context(token)


@pytest.mark.asyncio
async def test_generated_image_edit_uses_stored_parent_bytes_and_records_parent(
    monkeypatch, bound_turn
):
    from cognitrix.tools import image as image_module

    media = OwnedMemoryMedia()
    provider = RecordingProvider([b"parent pixels", b"child pixels"])
    monkeypatch.setattr(image_module, "media_assets", media)
    monkeypatch.setattr(image_module, "image_provider", provider)

    parent = await image_module.generate_image.run("create a lighthouse")
    parent_id = parent.outcome.artifacts[0].id
    child = await _selected_call(
        parent_id,
        lambda: image_module.generate_image.run(
            "make it dusk", source_artifact_id=parent_id
        ),
    )

    assert provider.calls[1]["source"].data == b"parent pixels"
    assert media.stores[1]["metadata"]["source_artifact_id"] == parent_id
    assert child.outcome.artifacts[0].id == "generated-2"


@pytest.mark.asyncio
async def test_uploaded_image_is_current_vision_and_a_valid_edit_source(
    monkeypatch, bound_turn
):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )
    from cognitrix.tools import image as image_module

    media = OwnedMemoryMedia()
    uploaded = media.add("upload-1", b"sanitized upload pixels", bound_turn)
    provider = RecordingProvider([b"edited upload"])
    monkeypatch.setattr("cognitrix.media.context.media_assets", media)
    monkeypatch.setattr(image_module, "media_assets", media)
    monkeypatch.setattr(image_module, "image_provider", provider)
    media_token = set_media_turn_context(MediaTurnContext(
        ownership=bound_turn,
        current_images=[uploaded],
        selected_image=None,
        vision_data_uri_cache={},
    ))
    try:
        _, history = await MediaContextBuilder().enrich(object(), [])
        result = await image_module.generate_image.run(
            "add clouds", source_artifact_id=uploaded.id
        )
    finally:
        reset_media_turn_context(media_token)

    assert history[-1]["artifact"]["id"] == uploaded.id
    assert history[-1]["content"].startswith("data:image/png;base64,")
    assert provider.calls[0]["source"].data == b"sanitized upload pixels"
    assert result.outcome.status == "success"


@pytest.mark.asyncio
async def test_foreign_artifact_selection_has_generic_denial_without_leaks(
    monkeypatch, bound_turn
):
    from cognitrix.tools import image as image_module

    media = OwnedMemoryMedia()
    media.add(
        "foreign-id",
        b"TOP-SECRET-PIXELS",
        MediaOwnership("other-session", "other-user", "other-agent"),
    )
    provider = RecordingProvider([b"must not be used"])
    monkeypatch.setattr(image_module, "media_assets", media)
    monkeypatch.setattr(image_module, "image_provider", provider)

    result = await _selected_call(
        "foreign-id",
        lambda: image_module.generate_image.run(
            "steal it", source_artifact_id="foreign-id"
        ),
    )

    public = f"{result.outcome.error.code} {result.outcome.error.message}"
    assert result.outcome.status == "error"
    assert result.outcome.error.message == "Artifact was not found"
    assert "TOP-SECRET" not in public
    assert "other-session" not in public
    assert "other-user" not in public
    assert "other-agent" not in public
    assert "path" not in public.lower()
    assert provider.calls == []


@pytest.mark.asyncio
async def test_three_turn_chain_uses_each_child_and_later_text_has_no_old_pixels(
    monkeypatch, bound_turn
):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )
    from cognitrix.tools import image as image_module

    media = OwnedMemoryMedia()
    provider = RecordingProvider([b"v1 pixels", b"v2 pixels", b"v3 pixels"])
    monkeypatch.setattr("cognitrix.media.context.media_assets", media)
    monkeypatch.setattr(image_module, "media_assets", media)
    monkeypatch.setattr(image_module, "image_provider", provider)

    first = await image_module.generate_image.run("base")
    first_id = first.outcome.artifacts[0].id
    second = await _selected_call(
        first_id,
        lambda: image_module.generate_image.run("edit one", source_artifact_id=first_id),
    )
    second_id = second.outcome.artifacts[0].id
    third = await _selected_call(
        second_id,
        lambda: image_module.generate_image.run("edit two", source_artifact_id=second_id),
    )

    assert [
        call["source"].ref.id if call["source"] else None for call in provider.calls
    ] == [None, first_id, second_id]
    assert [store["metadata"]["source_artifact_id"] for store in media.stores] == [
        None,
        first_id,
        second_id,
    ]
    assert third.outcome.artifacts[0].id == "generated-3"

    resolve_count = len(media.resolves)
    media_token = set_media_turn_context(MediaTurnContext(
        ownership=bound_turn,
        current_images=[],
        selected_image=None,
        vision_data_uri_cache={},
    ))
    try:
        _, later_history = await MediaContextBuilder().enrich(
            object(), [{"role": "User", "type": "text", "content": "now explain it"}]
        )
    finally:
        reset_media_turn_context(media_token)

    assert len(media.resolves) == resolve_count
    assert not any(
        str(message.get("content", "")).startswith("data:image/")
        for message in later_history
    )
