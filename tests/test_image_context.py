"""Selective, turn-local image context for model prompts."""

import pytest

from cognitrix.media.types import MediaOwnership, ResolvedImage
from cognitrix.tools.utils import ArtifactRef


def _ref(identifier: str) -> ArtifactRef:
    return ArtifactRef(
        id=identifier, mime_type="image/png", filename=f"{identifier}.png", origin="uploaded"
    )


@pytest.mark.asyncio
async def test_media_context_hydrates_only_current_and_selected_images(monkeypatch):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )

    current, selected = _ref("current"), _ref("selected")
    calls = []

    async def resolve_image(identifier, ownership, variant="original"):
        calls.append((identifier, variant))
        return ResolvedImage(_ref(identifier), variant, "image/png", identifier.encode())

    async def list_recent_refs(session_id, ownership, limit=3):
        return []

    monkeypatch.setattr("cognitrix.media.context.media_assets.resolve_image", resolve_image)
    monkeypatch.setattr("cognitrix.media.context.media_assets.list_recent_refs", list_recent_refs)
    turn = MediaTurnContext(
        ownership=MediaOwnership("session", "user", "agent"),
        current_images=[current],
        selected_image=selected,
        vision_data_uri_cache={},
    )
    token = set_media_turn_context(turn)
    try:
        media, history = await MediaContextBuilder().enrich(
            object(),
            [
                {"role": "User", "type": "text", "content": "[Previously supplied image: old]"},
                {"role": "User", "type": "text", "content": "edit this"},
            ],
        )
    finally:
        reset_media_turn_context(token)

    assert history[0]["content"] == "[Previously supplied image: old]"
    assert calls == [("current", "vision"), ("selected", "vision")]
    assert media is not None
    hydrated = [item["content"] for item in history if item.get("type") == "image"]
    assert "data:image/png;base64,Y3VycmVudA==" in hydrated
    assert "data:image/png;base64,c2VsZWN0ZWQ=" in hydrated


@pytest.mark.asyncio
async def test_media_context_deduplicates_pixels_and_keeps_only_recent_text_refs(monkeypatch):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )

    selected = _ref("same")

    async def resolve_image(identifier, ownership, variant="original"):
        return ResolvedImage(_ref(identifier), variant, "image/png", b"pixels")

    async def list_recent_refs(session_id, ownership, limit=3):
        return [_ref("one"), _ref("two"), _ref("three"), _ref("four")]

    monkeypatch.setattr("cognitrix.media.context.media_assets.resolve_image", resolve_image)
    monkeypatch.setattr("cognitrix.media.context.media_assets.list_recent_refs", list_recent_refs)
    turn = MediaTurnContext(
        ownership=MediaOwnership("session", "user", "agent"),
        current_images=[selected], selected_image=selected, vision_data_uri_cache={},
    )
    token = set_media_turn_context(turn)
    try:
        media, _ = await MediaContextBuilder().enrich(object(), [])
    finally:
        reset_media_turn_context(token)

    hydrated = [item["content"] for item in _ if item.get("type") == "image"]
    assert hydrated.count("data:image/png;base64,cGl4ZWxz") == 1
    assert "one, two, three" in media["content"]
    assert "four" not in media["content"]


@pytest.mark.asyncio
async def test_later_turn_without_selection_contains_no_old_image_pixels(monkeypatch):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )

    async def list_recent_refs(session_id, ownership, limit=3):
        return [_ref("old")]

    monkeypatch.setattr("cognitrix.media.context.media_assets.list_recent_refs", list_recent_refs)
    token = set_media_turn_context(MediaTurnContext(
        ownership=MediaOwnership("session", "user", "agent"),
        current_images=[], selected_image=None, vision_data_uri_cache={},
    ))
    try:
        media, history = await MediaContextBuilder().enrich(object(), [{
            "role": "User", "type": "image", "content": "data:image/png;base64,old-pixels",
            "artifact": {"id": "old"},
        }])
    finally:
        reset_media_turn_context(token)

    assert media is not None
    assert all(item.get("type") != "image" for item in history)
    assert all("data:image" not in item.get("content", "") for item in history)


@pytest.mark.asyncio
async def test_multiple_current_images_require_user_clarification(monkeypatch):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )

    async def resolve_image(identifier, ownership, variant="original"):
        return ResolvedImage(_ref(identifier), variant, "image/png", b"pixels")

    async def list_recent_refs(session_id, ownership, limit=3):
        return []

    monkeypatch.setattr("cognitrix.media.context.media_assets.resolve_image", resolve_image)
    monkeypatch.setattr("cognitrix.media.context.media_assets.list_recent_refs", list_recent_refs)
    token = set_media_turn_context(MediaTurnContext(
        ownership=MediaOwnership("session", "user", "agent"),
        current_images=[_ref("first"), _ref("second")], selected_image=None,
        vision_data_uri_cache={},
    ))
    try:
        media, _ = await MediaContextBuilder().enrich(object(), [])
    finally:
        reset_media_turn_context(token)

    assert media is not None
    assert "ask the user which image to use" in media["content"]
    assert "Selected edit source: none" in media["content"]


@pytest.mark.asyncio
async def test_recent_refs_are_loaded_from_persistence_after_history_compaction(monkeypatch):
    from cognitrix.media.context import (
        MediaContextBuilder,
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )

    calls = []

    async def list_recent_refs(session_id, ownership, limit=3):
        calls.append((session_id, ownership, limit))
        return [_ref("persisted")]

    monkeypatch.setattr("cognitrix.media.context.media_assets.list_recent_refs", list_recent_refs)
    ownership = MediaOwnership("session", "user", "agent")
    token = set_media_turn_context(MediaTurnContext(
        ownership=ownership, current_images=[], selected_image=None, vision_data_uri_cache={},
    ))
    try:
        media, history = await MediaContextBuilder().enrich(object(), [])
    finally:
        reset_media_turn_context(token)

    assert history == []
    assert calls == [("session", ownership, 3)]
    assert "Recent image refs: persisted" in media["content"]
