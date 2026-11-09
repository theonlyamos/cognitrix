"""OpenAI as the image provider when there is no Google key."""
import base64

import httpx
import pytest

from cognitrix.media.types import ResolvedImage
from cognitrix.providers import openai_image
from cognitrix.providers.gemini_image import GeminiImageError
from cognitrix.tools import image as image_tool

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32


def _no_saved_keys(monkeypatch):
    from cognitrix.providers import saved_keys
    monkeypatch.setattr(saved_keys, 'get_saved_key', lambda provider: '')


def test_closest_size_for_each_shape():
    assert openai_image.size_for('1:1') == '1024x1024'
    assert openai_image.size_for('16:9') == '1536x1024'
    assert openai_image.size_for('9:16') == '1024x1536'
    assert openai_image.size_for('nonsense') == '1024x1024'


def test_provider_choice_follows_the_keys(monkeypatch):
    _no_saved_keys(monkeypatch)
    for name in ('GOOGLE_API_KEY', 'OPENAI_API_KEY', 'COGNITRIX_IMAGE_PROVIDER'):
        monkeypatch.delenv(name, raising=False)
    pick = image_tool.ImageProviderChoice._pick
    assert pick() == 'none'
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    assert pick() == 'openai'
    monkeypatch.setenv('GOOGLE_API_KEY', 'g-test')
    assert pick() == 'google'
    monkeypatch.setenv('COGNITRIX_IMAGE_PROVIDER', 'openai')
    assert pick() == 'openai'


async def test_generate_and_edit_requests(monkeypatch):
    _no_saved_keys(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.delenv('COGNITRIX_OPENAI_IMAGE_MODEL', raising=False)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(PNG).decode()}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        openai_image.httpx, 'AsyncClient',
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = openai_image.OpenAIImageProvider()

    image = await provider.generate('a red kite', source=None, aspect_ratio='16:9')
    assert image.data == PNG and image.model == 'gpt-image-2'
    assert seen[0].url.path == '/v1/images/generations'
    assert seen[0].headers['authorization'] == 'Bearer sk-test'
    assert b'"size":"1536x1024"' in seen[0].content.replace(b' ', b'')

    source = ResolvedImage.__new__(ResolvedImage)
    object.__setattr__(source, 'data', PNG)
    object.__setattr__(source, 'mime_type', 'image/png')
    await provider.generate('make it blue', source=source, aspect_ratio='1:1')
    assert seen[1].url.path == '/v1/images/edits'
    assert b'make it blue' in seen[1].content and PNG in seen[1].content


async def test_errors_are_reported_safely(monkeypatch):
    _no_saved_keys(monkeypatch)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(GeminiImageError) as exc_info:
        await openai_image.OpenAIImageProvider().generate('x', source=None, aspect_ratio='1:1')
    assert exc_info.value.code == 'missing_openai_api_key'

    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        openai_image.httpx, 'AsyncClient',
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(400, json={'error': {'message': 'bad prompt'}})),
            **kwargs),
    )
    with pytest.raises(GeminiImageError) as exc_info:
        await openai_image.OpenAIImageProvider().generate('x', source=None, aspect_ratio='1:1')
    assert exc_info.value.code == 'image_provider_error' and 'bad prompt' in str(exc_info.value)
