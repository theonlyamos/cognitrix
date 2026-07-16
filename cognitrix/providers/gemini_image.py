"""Stateless Gemini image-generation transport adapter."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from cognitrix.media.processing import run_media_cpu
from cognitrix.media.types import ResolvedImage

MODEL = 'gemini-3.1-flash-image'
URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
MAX_PROVIDER_RESPONSE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class ProviderImage:
    data: bytes
    reported_mime_type: str | None = None


class GeminiImageError(ValueError):
    """Safe provider-boundary failure carrying the public tool error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _encode_source(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def _image_data(value: dict[str, Any]) -> tuple[str, str | None] | None:
    for key in ('data', 'base64', 'image_data'):
        encoded = value.get(key)
        if not isinstance(encoded, str):
            continue
        candidate = encoded.split(',', 1)[-1]
        mime_type = value.get('mime_type') or value.get('mimeType')
        return candidate, mime_type if isinstance(mime_type, str) else None
    return None


def _decode_image(value: dict[str, Any]) -> ProviderImage | None:
    candidate = _image_data(value)
    if candidate is None:
        return None
    encoded, mime_type = candidate
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    return ProviderImage(data=data, reported_mime_type=mime_type)


def _find_image(value: Any) -> ProviderImage | None:
    """Find an image in a legacy provider response shape."""
    if isinstance(value, dict):
        candidate = _decode_image(value)
        if candidate:
            return candidate
        for child in value.values():
            found = _find_image(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_image(child)
            if found:
                return found
    return None


def _find_response_image(value: Any) -> ProviderImage | None:
    """Select only the final image in the final model-output step."""
    if not isinstance(value, dict) or not isinstance(value.get('steps'), list):
        return _find_image(value)

    last_model_output = next(
        (
            step
            for step in reversed(value['steps'])
            if isinstance(step, dict) and step.get('type') == 'model_output'
        ),
        None,
    )
    if last_model_output is None:
        return None

    content = last_model_output.get('content')
    if not isinstance(content, list):
        return None
    final_image = next(
        (
            block
            for block in reversed(content)
            if isinstance(block, dict) and block.get('type') == 'image'
        ),
        None,
    )
    return _decode_image(final_image) if final_image is not None else None


def _decode_response(response_body: bytes | bytearray) -> ProviderImage | None:
    try:
        response_value = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise GeminiImageError(
            'image_generation_error',
            'Image provider returned an invalid response',
        ) from None
    return _find_response_image(response_value)


class GeminiImageProvider:
    async def generate(
        self,
        prompt: str,
        *,
        source: ResolvedImage | None,
        aspect_ratio: str,
    ) -> ProviderImage:
        api_key = os.getenv('GOOGLE_API_KEY', '')
        if not api_key:
            raise GeminiImageError(
                'missing_google_api_key',
                'GOOGLE_API_KEY is required for image generation',
            )

        input_content: list[dict[str, Any]] = [
            {'type': 'text', 'text': prompt},
        ]
        if source is not None:
            input_content.append({
                'type': 'image',
                'mime_type': source.mime_type,
                'data': await run_media_cpu(_encode_source, source.data),
            })

        payload: dict[str, Any] = {
            'model': MODEL,
            'store': False,
            'input': input_content,
            'response_format': {
                'type': 'image',
                'image_size': '1K',
                'aspect_ratio': aspect_ratio,
            },
        }
        response_body = bytearray()
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                async with client.stream(
                    'POST',
                    URL,
                    headers={'x-goog-api-key': api_key},
                    json=payload,
                ) as response:
                    async for chunk in response.aiter_bytes():
                        response_body.extend(chunk)
                        if len(response_body) > MAX_PROVIDER_RESPONSE_BYTES:
                            raise GeminiImageError(
                                'image_generation_error',
                                'Image provider response exceeds the 15MB limit',
                            )
                    response.raise_for_status()
        except GeminiImageError:
            raise
        except httpx.HTTPStatusError as exc:
            detail = bytes(response_body[:1000]).decode(
                'utf-8', errors='replace'
            ).strip()
            suffix = f': {detail}' if detail else ''
            raise GeminiImageError(
                'image_provider_error',
                f'Image provider request failed: {exc}{suffix}',
            ) from None
        except httpx.HTTPError as exc:
            raise GeminiImageError(
                'image_provider_error',
                f'Image provider request failed: {exc}',
            ) from None

        selected = await run_media_cpu(_decode_response, response_body)
        if selected is None:
            raise GeminiImageError(
                'invalid_provider_output',
                'Image provider returned no image',
            )
        return selected
