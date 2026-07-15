"""Gemini Nano Banana 2 image-generation tool."""

from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

import httpx
from PIL import Image

from cognitrix.artifacts import current_session_id, ref, source_image, store_png
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

MODEL = 'gemini-3.1-flash-image'
URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
ASPECT_RATIOS = {
    '1:1', '1:4', '1:8', '2:3', '3:2', '3:4', '4:1', '4:3',
    '4:5', '5:4', '8:1', '9:16', '16:9', '21:9',
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_PROVIDER_RESPONSE_BYTES = 15 * 1024 * 1024


def _image_data(value: dict[str, Any]) -> str | None:
    for key in ('data', 'base64', 'image_data'):
        if isinstance(value.get(key), str):
            candidate = value[key].split(',', 1)[-1]
            try:
                base64.b64decode(candidate, validate=True)
                return candidate
            except ValueError:
                pass
    return None


def _find_image(value: Any) -> str | None:
    """Find an image in legacy provider response shapes."""
    if isinstance(value, dict):
        candidate = _image_data(value)
        if candidate:
            return candidate
        for child in value.values():
            found = _find_image(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_image(child)
            if found:
                return found
    return None


def _find_response_image(value: Any) -> str | None:
    """Select only the final image from a Gemini Interactions response."""
    if not isinstance(value, dict) or not isinstance(value.get('steps'), list):
        return _find_image(value)

    last_model_output = next((
        step for step in reversed(value['steps'])
        if isinstance(step, dict) and step.get('type') == 'model_output'
    ), None)
    if last_model_output is None:
        return None

    content = last_model_output.get('content')
    if not isinstance(content, list):
        return None
    for block in reversed(content):
        if isinstance(block, dict) and block.get('type') == 'image':
            return _image_data(block)
    return None


def _as_png(encoded: str) -> tuple[bytes, int, int]:
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4:
        raise ValueError('Image provider output exceeds the 10MB limit')
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('Image provider output exceeds the 10MB limit')
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise ValueError('Image provider output exceeds the pixel limit')
        image.load()
        converted = image.convert('RGBA' if image.mode in ('RGBA', 'LA', 'P') else 'RGB')
        output = io.BytesIO()
        converted.save(output, format='PNG', optimize=True)
    png = output.getvalue()
    if len(png) > MAX_IMAGE_BYTES:
        raise ValueError('PNG output exceeds the 10MB limit')
    return png, width, height


@tool(category='media', retryable=False, max_attempts=1,
      approval_mode='assigned_only', supported_interfaces=['web', 'ws', 'cli', 'task', 'tui'])
async def generate_image(prompt: str, source_artifact_id: str | None = None,
                         aspect_ratio: str | None = None) -> ToolOutcome:
    """Generate one 1K PNG image, or edit one image from this conversation.

    Args:
        prompt: The desired image or the edit instructions.
        source_artifact_id: Optional image artifact from this session to edit.
        aspect_ratio: Optional output ratio such as 1:1, 16:9, or 9:16.
    """
    if not prompt.strip():
        return ToolOutcome.failure('invalid_prompt', 'An image prompt is required')
    if aspect_ratio and aspect_ratio not in ASPECT_RATIOS:
        return ToolOutcome.failure('invalid_aspect_ratio', 'Unsupported aspect ratio')
    api_key = os.getenv('GOOGLE_API_KEY', '')
    if not api_key:
        return ToolOutcome.failure('missing_google_api_key', 'GOOGLE_API_KEY is required for image generation')
    prompt_text = prompt.strip()
    # The Interactions API accepts direct content blocks rather than an
    # OpenAI-style {'role': 'user', 'content': ...} message envelope.
    input_content: list[dict[str, Any]] = [
        {'type': 'text', 'text': prompt_text},
    ]
    source_id = None
    response_body = bytearray()
    try:
        if source_artifact_id:
            source, data = await source_image(source_artifact_id, current_session_id())
            source_id = str(source.id)
            input_content = [
                {'type': 'text', 'text': prompt_text},
                {'type': 'image', 'mime_type': source.mime_type,
                 'data': base64.b64encode(data).decode('ascii')},
            ]
        payload: dict[str, Any] = {
            'model': MODEL,
            'store': False,
            'input': input_content,
            'response_format': {
                'type': 'image', 'image_size': '1K',
                'aspect_ratio': aspect_ratio or '1:1',
            },
        }
        async with httpx.AsyncClient(timeout=90) as client:
            async with client.stream(
                'POST', URL, headers={'x-goog-api-key': api_key}, json=payload
            ) as response:
                async for chunk in response.aiter_bytes():
                    response_body.extend(chunk)
                    if len(response_body) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise ValueError('Image provider response exceeds the 15MB limit')
                response.raise_for_status()
        image_data = _find_response_image(json.loads(response_body))
        if not image_data:
            return ToolOutcome.failure('invalid_provider_output', 'Image provider returned no image')
        png, width, height = _as_png(image_data)
        artifact = await store_png(png, prompt=prompt.strip(), source_artifact_id=source_id,
                                   model=MODEL, width=width, height=height)
        return ToolOutcome.success('Image generated.', artifacts=[ref(artifact)])
    except httpx.HTTPStatusError as exc:
        detail = bytes(response_body[:1000]).decode('utf-8', errors='replace').strip()
        suffix = f': {detail}' if detail else ''
        return ToolOutcome.failure('image_provider_error', f'Image provider request failed: {exc}{suffix}')
    except httpx.HTTPError as exc:
        return ToolOutcome.failure('image_provider_error', f'Image provider request failed: {exc}')
    except (ValueError, OSError) as exc:
        return ToolOutcome.failure('image_generation_error', str(exc))
