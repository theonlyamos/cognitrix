"""Turn-local, bounded image context for model prompts."""

from __future__ import annotations

import base64
import contextvars
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from cognitrix.media import MediaOwnership, media_assets, run_media_cpu
from cognitrix.tools.utils import ArtifactRef


@dataclass
class MediaTurnContext:
    ownership: MediaOwnership
    current_images: list[ArtifactRef]
    selected_image: ArtifactRef | None
    vision_data_uri_cache: dict[str, str]
    recent_refs_cache: list[ArtifactRef] | None = None


_media_turn_context: contextvars.ContextVar[MediaTurnContext | None] = (
    contextvars.ContextVar('media_turn_context', default=None)
)


def set_media_turn_context(value: MediaTurnContext):
    return _media_turn_context.set(value)


def reset_media_turn_context(token) -> None:
    _media_turn_context.reset(token)


def current_media_turn_context() -> MediaTurnContext | None:
    return _media_turn_context.get()


def _data_uri(mime_type: str, data: bytes) -> str:
    return f'data:{mime_type};base64,{base64.b64encode(data).decode("ascii")}'


class MediaContextBuilder:
    """Hydrate only current or explicitly selected images for one model turn."""

    async def _vision_uri(self, ref: ArtifactRef, turn: MediaTurnContext) -> str:
        cached = turn.vision_data_uri_cache.get(ref.id)
        if cached is not None:
            return cached
        resolved = await media_assets.resolve_image(ref.id, turn.ownership, 'vision')
        uri = await run_media_cpu(_data_uri, resolved.mime_type, resolved.data)
        turn.vision_data_uri_cache[ref.id] = uri
        return uri

    async def enrich(
        self,
        session: Any,
        recent_history: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        history = deepcopy(recent_history)
        turn = current_media_turn_context()
        if turn is None:
            return None, history

        current_refs = {ref.id: ref for ref in turn.current_images}
        current_ids = set(current_refs)
        selected_marker_index: int | None = None
        if turn.selected_image is not None:
            for index in range(len(history) - 1, -1, -1):
                message = history[index]
                artifact = message.get('artifact')
                artifact_id = artifact.get('id') if isinstance(artifact, dict) else None
                if (
                    message.get('type') == 'image_selection'
                    and artifact_id == turn.selected_image.id
                ):
                    selected_marker_index = index
                    break

        hydrated_ids: set[str] = set()
        enriched: list[dict[str, Any]] = []
        for index, message in enumerate(history):
            artifact = message.get('artifact')
            artifact_id = artifact.get('id') if isinstance(artifact, dict) else None
            if message.get('type') == 'image' and artifact_id in current_ids:
                if artifact_id not in hydrated_ids:
                    message['content'] = await self._vision_uri(
                        current_refs[artifact_id], turn
                    )
                    hydrated_ids.add(artifact_id)
                else:
                    continue
            elif index == selected_marker_index and turn.selected_image is not None:
                if turn.selected_image.id not in hydrated_ids:
                    message['type'] = 'image'
                    message['content'] = await self._vision_uri(turn.selected_image, turn)
                    hydrated_ids.add(turn.selected_image.id)
                else:
                    enriched.append({
                        'role': message.get('role', 'User'),
                        'type': 'text',
                        'content': f'[Previously supplied image: {artifact_id or "unknown"}]',
                    })
                    continue
            elif message.get('type') in {'image', 'image_selection'}:
                enriched.append({
                    'role': message.get('role', 'User'),
                    'type': 'text',
                    'content': f'[Previously supplied image: {artifact_id or "unknown"}]',
                })
                continue
            enriched.append(message)

        fallback_images: list[dict[str, Any]] = []
        for ref in turn.current_images:
            if ref.id in hydrated_ids:
                continue
            fallback_images.append({
                'role': 'User', 'type': 'image',
                'content': await self._vision_uri(ref, turn),
                'artifact': ref.model_dump(),
            })
            hydrated_ids.add(ref.id)

        if turn.selected_image is not None and turn.selected_image.id not in hydrated_ids:
            fallback_images.append({
                'role': 'User',
                'type': 'image',
                'content': await self._vision_uri(turn.selected_image, turn),
                'artifact': turn.selected_image.model_dump(),
            })
            hydrated_ids.add(turn.selected_image.id)

        if fallback_images:
            # A fallback image still belongs to the current user request. Never
            # append it after assistant tool calls/results, where providers would
            # interpret it as a fresh user turn and invoke the image tool again.
            user_request_index = next((
                index
                for index in range(len(enriched) - 1, -1, -1)
                if str(enriched[index].get('role', '')).lower() == 'user'
                and enriched[index].get('type', 'text') in {'text', 'summary'}
            ), None)
            if user_request_index is not None:
                insert_at = user_request_index + 1
                while (
                    insert_at < len(enriched)
                    and str(enriched[insert_at].get('role', '')).lower() == 'user'
                ):
                    insert_at += 1
            else:
                insert_at = next((
                    index
                    for index, message in enumerate(enriched)
                    if str(message.get('role', '')).lower() in {'assistant', 'tool'}
                ), len(enriched))
            enriched[insert_at:insert_at] = fallback_images

        if turn.recent_refs_cache is None:
            turn.recent_refs_cache = await media_assets.list_recent_refs(
                turn.ownership.session_id or '', turn.ownership, limit=3
            )
        recent = turn.recent_refs_cache[:3]
        if not (current_ids or turn.selected_image or recent):
            return None, enriched

        current_text = ', '.join(ref.id for ref in turn.current_images) or 'none'
        selected_text = turn.selected_image.id if turn.selected_image else 'none'
        recent_text = ', '.join(ref.id for ref in recent) or 'none'
        return {
            'role': 'system',
            'type': 'media_context',
            'content': (
                '## Image context\n'
                f'Current image refs: {current_text}\n'
                f'Selected edit source: {selected_text}\n'
                f'Recent image refs: {recent_text}\n'
                'Use the explicitly selected source for edits. Use a current upload when no source is selected. '
                'Never infer a source from an older reference. If multiple current images are present and no source is selected, ask the user which image to use.'
            ),
        }, enriched
