"""OpenAI image generation and editing, used when there is no Google key
(or COGNITRIX_IMAGE_PROVIDER=openai). Same contract as the Gemini adapter.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx

from cognitrix.app_settings import get as app_setting
from cognitrix.media.types import ResolvedImage
from cognitrix.providers.gemini_image import (
    MAX_PROVIDER_RESPONSE_BYTES,
    GeminiImageError,
    ProviderImage,
)

DEFAULT_MODEL = 'gpt-image-2.6-flare'
BASE_URL = 'https://api.openai.com/v1'
_EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}


def model_name() -> str:
    return app_setting('COGNITRIX_OPENAI_IMAGE_MODEL', '').strip() or DEFAULT_MODEL


def api_key() -> str:
    from cognitrix.providers.saved_keys import get_saved_key

    return get_saved_key('openai') or ''


def size_for(aspect_ratio: str) -> str:
    """OpenAI offers square, landscape and portrait; pick the closest."""
    try:
        width, height = (float(part) for part in aspect_ratio.split(':', 1))
        ratio = width / height
    except (ValueError, ZeroDivisionError):
        return '1024x1024'
    if ratio > 1.2:
        return '1536x1024'
    if ratio < 0.84:
        return '1024x1536'
    return '1024x1024'


def _image_from(body: Any) -> ProviderImage | None:
    items = body.get('data') if isinstance(body, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    encoded = items[0].get('b64_json')
    if not isinstance(encoded, str):
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    return ProviderImage(data=data, model=model_name()) if data else None


class OpenAIImageProvider:
    async def generate(self, prompt: str, *, source: ResolvedImage | None, aspect_ratio: str) -> ProviderImage:
        key = api_key()
        if not key:
            raise GeminiImageError('missing_openai_api_key', 'Image generation with OpenAI needs an OpenAI key (Settings, Models and keys).')
        headers = {'Authorization': f'Bearer {key}'}
        fields = {'model': model_name(), 'prompt': prompt, 'size': size_for(aspect_ratio), 'n': 1}
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                if source is None:
                    response = await client.post(f'{BASE_URL}/images/generations', headers=headers, json=fields)
                else:
                    extension = _EXTENSIONS.get(source.mime_type, 'png')
                    response = await client.post(
                        f'{BASE_URL}/images/edits',
                        headers=headers,
                        data={name: str(value) for name, value in fields.items()},
                        files={'image': (f'image.{extension}', source.data, source.mime_type)},
                    )
            if len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
                raise GeminiImageError('image_generation_error', 'Image provider response exceeds the 15MB limit')
            response.raise_for_status()
        except GeminiImageError:
            raise
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000].strip()
            raise GeminiImageError(
                'image_provider_error',
                f'Image provider request failed: {exc}{": " + detail if detail else ""}',
            ) from None
        except httpx.HTTPError as exc:
            raise GeminiImageError('image_provider_error', f'Image provider request failed: {exc}') from None
        try:
            image = _image_from(response.json())
        except ValueError:
            raise GeminiImageError('image_generation_error', 'Image provider returned an invalid response') from None
        if image is None:
            raise GeminiImageError('invalid_provider_output', 'Image provider returned no image')
        return image
