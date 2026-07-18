"""Gemini image-generation and image-editing tool orchestration."""

from __future__ import annotations

from cognitrix import artifacts as artifact_context
from cognitrix.artifacts import current_session_id
from cognitrix.media.context import current_media_turn_context
from cognitrix.media.service import media_assets
from cognitrix.media.types import MediaError, MediaOwnership
from cognitrix.providers.gemini_image import (
    MODEL,
    GeminiImageError,
    GeminiImageProvider,
)
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

ASPECT_RATIOS = {
    '1:1', '1:4', '1:8', '2:3', '3:2', '3:4', '4:1', '4:3',
    '4:5', '5:4', '8:1', '9:16', '16:9', '21:9',
}

image_provider = GeminiImageProvider()


def _current_media_ownership() -> MediaOwnership:
    """Read the authority bound immediately around the current tool call."""
    execution_context = current_execution_context()
    # Session currently exposes only the session component publicly. Keep the
    # private Artifact ContextVar compatibility boundary centralized here until
    # the artifact context gains a public ownership accessor.
    artifact_user_id = artifact_context._user_id.get()
    return MediaOwnership(
        session_id=current_session_id(),
        user_id=(
            execution_context.user_id
            if execution_context.user_id is not None
            else artifact_user_id
        ),
        agent_id=artifact_context._agent_id.get(),
        run_id=execution_context.run_id,
    )


@tool(category='media', retryable=False, max_attempts=1,
      approval_mode='assigned_only', supported_interfaces=['web', 'ws', 'cli', 'task', 'tui'])
async def generate_image(prompt: str, source_artifact_id: str | None = None,
                         aspect_ratio: str | None = None) -> ToolOutcome:
    """Generate one 1K image, or edit one image from this conversation.

    Args:
        prompt (str): The desired image or the edit instructions.
        source_artifact_id (str | None): Exact artifact ID to edit. Omit it when the UI has selected an image; never use a filename or provider image label.
        aspect_ratio (str | None): Optional output ratio such as 1:1, 16:9, or 9:16.
    """
    prompt_text = prompt.strip()
    if not prompt_text:
        return ToolOutcome.failure(
            'invalid_prompt',
            'An image prompt is required',
        )
    if aspect_ratio and aspect_ratio not in ASPECT_RATIOS:
        return ToolOutcome.failure(
            'invalid_aspect_ratio',
            'Unsupported aspect ratio',
        )

    ownership = _current_media_ownership()
    try:
        source = None
        parent_artifact_id = None
        selected_artifact_id = current_execution_context().selected_image_artifact_id
        media_turn = current_media_turn_context()
        sole_current_artifact_id = (
            str(media_turn.current_images[0].id)
            if (
                media_turn is not None
                and media_turn.selected_image is None
                and len(media_turn.current_images) == 1
            )
            else None
        )
        if (
            source_artifact_id
            and not selected_artifact_id
            and str(source_artifact_id) != sole_current_artifact_id
        ):
            return ToolOutcome.failure(
                'image_selection_required',
                'Select exactly one image before requesting an edit',
                denied=True,
            )
        # The UI-selected artifact is trusted turn authority; model arguments are
        # not. When a selection is bound, always edit it and ignore any stale or
        # invented source ID supplied by the model.
        effective_source_id = selected_artifact_id or source_artifact_id
        if effective_source_id:
            source = await media_assets.resolve_image(
                str(effective_source_id),
                ownership,
                'original',
            )
            parent_artifact_id = source.ref.id

        provider_image = await image_provider.generate(
            prompt_text,
            source=source,
            aspect_ratio=aspect_ratio or '1:1',
        )
        artifact = await media_assets.store_generated_image(
            provider_image.data,
            {
                'prompt': prompt_text,
                'source_artifact_id': parent_artifact_id,
                'model': MODEL,
            },
            ownership,
        )
        return ToolOutcome.success('Image generated.', artifacts=[artifact])
    except GeminiImageError as exc:
        return ToolOutcome.failure(exc.code, str(exc))
    except MediaError as exc:
        return ToolOutcome.failure('image_generation_error', str(exc))
    except OSError:
        return ToolOutcome.failure(
            'image_generation_error',
            'Image generation failed',
        )
