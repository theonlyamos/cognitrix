import base64
import json

import httpx
import pytest

from cognitrix.media.types import ResolvedImage
from cognitrix.tools.utils import ArtifactRef


def _response_image(data: bytes, *, mime_type: str = 'image/png') -> dict:
    return {
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'image',
                'mime_type': mime_type,
                'data': base64.b64encode(data).decode('ascii'),
            }],
        }],
    }


def _source() -> ResolvedImage:
    return ResolvedImage(
        ref=ArtifactRef(
            id='artifact-id-must-stay-local',
            mime_type='image/png',
            filename=r'C:\private\storage-key-must-stay-local.png',
            origin='uploaded',
        ),
        variant='original',
        mime_type='image/png',
        data=b'verified source bytes',
    )


def _install_stream(monkeypatch, provider_module, payload, *, chunks=None, status=200):
    captured = {}
    body_chunks = chunks or [json.dumps(payload).encode()]

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self, chunk_size=None):
            captured['chunk_size'] = chunk_size
            for chunk in body_chunks:
                yield chunk

        def raise_for_status(self):
            if status >= 400:
                response = httpx.Response(
                    status,
                    request=httpx.Request('POST', 'https://provider.invalid/interactions'),
                    content=b'',
                )
                response.raise_for_status()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url, headers, json):
            captured.update(
                method=method,
                url=url,
                headers=headers,
                payload=json,
            )
            return Response()

    monkeypatch.setattr(provider_module.httpx, 'AsyncClient', lambda **kwargs: Client())
    return captured


@pytest.mark.asyncio
async def test_generate_sends_stateless_direct_blocks_and_encodes_source_off_loop(
    monkeypatch,
):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    response_payload = _response_image(
        b'provider image', mime_type='image/webp'
    )
    captured = _install_stream(
        monkeypatch,
        provider_module,
        response_payload,
    )
    cpu_calls = []

    async def run_media_cpu(func, *args, **kwargs):
        cpu_calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(provider_module, 'run_media_cpu', run_media_cpu)

    image = await GeminiImageProvider().generate(
        'Make it blue',
        source=_source(),
        aspect_ratio='16:9',
    )

    assert image.data == b'provider image'
    assert image.reported_mime_type == 'image/webp'
    assert captured['chunk_size'] == 64 * 1024
    assert len(cpu_calls) == 2
    assert cpu_calls[0][1] == (b'verified source bytes',)
    assert len(cpu_calls[1][1]) == 1
    assert isinstance(cpu_calls[1][1][0], (bytes, bytearray))
    assert bytes(cpu_calls[1][1][0]) == json.dumps(response_payload).encode()
    assert captured['payload'] == {
        'model': 'gemini-3.1-flash-image',
        'store': False,
        'input': [
            {'type': 'text', 'text': 'Make it blue'},
            {
                'type': 'image',
                'mime_type': 'image/png',
                'data': base64.b64encode(b'verified source bytes').decode('ascii'),
            },
        ],
        'response_format': {
            'type': 'image',
            'image_size': '1K',
            'aspect_ratio': '16:9',
        },
    }
    serialized = json.dumps(captured['payload'])
    assert 'artifact-id-must-stay-local' not in serialized
    assert 'storage-key-must-stay-local' not in serialized
    assert 'C:\\private' not in serialized


@pytest.mark.asyncio
async def test_generate_without_source_sends_only_the_text_block(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    captured = _install_stream(
        monkeypatch,
        provider_module,
        _response_image(b'generated'),
    )

    await GeminiImageProvider().generate(
        'A red square',
        source=None,
        aspect_ratio='1:1',
    )

    assert captured['payload']['input'] == [
        {'type': 'text', 'text': 'A red square'},
    ]


@pytest.mark.asyncio
async def test_generate_chooses_last_image_from_last_model_output_step(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    payload = {
        'steps': [
            {
                'type': 'model_output',
                'content': [{'type': 'image', 'data': base64.b64encode(b'old').decode()}],
            },
            {
                'type': 'thought',
                'content': [{'type': 'image', 'data': base64.b64encode(b'thought').decode()}],
            },
            {
                'type': 'model_output',
                'content': [
                    {'type': 'image', 'data': base64.b64encode(b'newer').decode()},
                    {'type': 'text', 'text': 'done'},
                    {
                        'type': 'image',
                        'mime_type': 'image/jpeg',
                        'data': base64.b64encode(b'final').decode(),
                    },
                ],
            },
        ],
    }
    _install_stream(monkeypatch, provider_module, payload)

    image = await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert image.data == b'final'
    assert image.reported_mime_type == 'image/jpeg'


@pytest.mark.asyncio
async def test_invalid_final_image_does_not_fall_back(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    payload = {
        'steps': [{
            'type': 'model_output',
            'content': [
                {'type': 'image', 'data': base64.b64encode(b'valid').decode()},
                {'type': 'image', 'data': 'not-base64'},
            ],
        }],
    }
    _install_stream(monkeypatch, provider_module, payload)

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'invalid_provider_output'
    assert str(exc_info.value) == 'Image provider returned no image'


@pytest.mark.asyncio
async def test_empty_final_image_does_not_fall_back(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    payload = {
        'steps': [{
            'type': 'model_output',
            'content': [
                {'type': 'image', 'data': base64.b64encode(b'valid').decode()},
                {'type': 'image', 'data': ''},
            ],
        }],
    }
    _install_stream(monkeypatch, provider_module, payload)

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'invalid_provider_output'
    assert str(exc_info.value) == 'Image provider returned no image'


@pytest.mark.asyncio
async def test_legacy_traversal_skips_empty_image_for_a_later_valid_one(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    payload = {
        'output': [
            {'data': ''},
            {'data': base64.b64encode(b'legacy valid').decode()},
        ],
    }
    _install_stream(monkeypatch, provider_module, payload)

    image = await GeminiImageProvider().generate(
        'prompt', source=None, aspect_ratio='1:1'
    )

    assert image.data == b'legacy valid'


@pytest.mark.asyncio
async def test_streamed_response_is_capped_at_fifteen_mib(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    _install_stream(
        monkeypatch,
        provider_module,
        {},
        chunks=[b'x' * (15 * 1024 * 1024), b'x'],
    )

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'image_generation_error'
    assert str(exc_info.value) == 'Image provider response exceeds the 15MB limit'


@pytest.mark.asyncio
async def test_single_oversized_stream_chunk_is_rejected_before_copy(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    monkeypatch.setattr(provider_module, 'MAX_PROVIDER_RESPONSE_BYTES', 3)

    class OversizedChunk:
        def __len__(self):
            return 4

        def __iter__(self):
            raise AssertionError('oversized chunk must not be copied')

    _install_stream(
        monkeypatch,
        provider_module,
        {},
        chunks=[OversizedChunk()],
    )

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'image_generation_error'
    assert str(exc_info.value) == 'Image provider response exceeds the 15MB limit'


@pytest.mark.asyncio
async def test_http_error_uses_capped_provider_detail(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    secret_tail = 'must-not-leak'
    _install_stream(
        monkeypatch,
        provider_module,
        {},
        chunks=[b'invalid input:' + (b'x' * 1100) + secret_tail.encode()],
        status=400,
    )

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'image_provider_error'
    assert 'invalid input' in str(exc_info.value)
    assert secret_tail not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_json_returns_safe_decode_error(monkeypatch):
    from cognitrix.providers import gemini_image as provider_module
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    _install_stream(monkeypatch, provider_module, {}, chunks=[b'{private invalid body'])

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'image_generation_error'
    assert str(exc_info.value) == 'Image provider returned an invalid response'
    assert 'private invalid body' not in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_api_key_is_a_typed_provider_error(monkeypatch):
    from cognitrix.providers.gemini_image import GeminiImageError, GeminiImageProvider

    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)

    with pytest.raises(GeminiImageError) as exc_info:
        await GeminiImageProvider().generate('prompt', source=None, aspect_ratio='1:1')

    assert exc_info.value.code == 'missing_google_api_key'
    assert str(exc_info.value) == 'GOOGLE_API_KEY is required for image generation'
