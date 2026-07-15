import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image


def _png(width: int = 2, height: int = 3, color: str = 'red') -> str:
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def test_generate_image_uses_stateless_one_shot_gemini_payload(monkeypatch):
    async def run():
        from cognitrix.tools.image import generate_image

        monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
        captured = {}

        class Response:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def raise_for_status(self): pass
            async def aiter_bytes(self):
                yield json.dumps({'output': [{'data': _png()}]}).encode()

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def stream(self, method, url, headers, json):
                captured.update(url=url, headers=headers, payload=json)
                return Response()

        monkeypatch.setattr('cognitrix.tools.image.httpx.AsyncClient', lambda **kw: Client())
        artifact = SimpleNamespace(id='artifact1', mime_type='image/png', filename='x.png', width=2, height=3)

        async def store(data, **kw):
            captured['store'] = kw
            return artifact

        monkeypatch.setattr('cognitrix.tools.image.store_png', store)

        out = await generate_image.run('A red square', aspect_ratio='1:1')
        assert out.outcome.status == 'success'
        assert captured['payload']['model'] == 'gemini-3.1-flash-image'
        assert captured['payload']['store'] is False
        assert captured['payload']['input'] == [
            {'type': 'text', 'text': 'A red square'},
        ]
        assert captured['payload']['response_format'] == {
            'type': 'image', 'image_size': '1K', 'aspect_ratio': '1:1'
        }
        assert 'generation_config' not in captured['payload']
        assert out.outcome.artifacts[0].id == 'artifact1'

    asyncio.run(run())


def test_generate_image_rejects_an_unsupported_ratio_before_provider(monkeypatch):
    async def run():
        from cognitrix.tools.image import generate_image
        monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
        out = await generate_image.run('x', aspect_ratio='square')
        assert out.outcome.error.code == 'invalid_aspect_ratio'

    asyncio.run(run())


def test_generate_image_uses_direct_content_blocks_for_source_images(monkeypatch):
    async def run():
        from cognitrix.tools.image import generate_image

        monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
        captured = {}

        class Response:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def raise_for_status(self): pass
            async def aiter_bytes(self):
                yield json.dumps({'output_image': {'data': _png()}}).encode()

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def stream(self, method, url, headers, json):
                captured.update(payload=json)
                return Response()

        monkeypatch.setattr('cognitrix.tools.image.httpx.AsyncClient', lambda **kw: Client())
        source = SimpleNamespace(id='source1', mime_type='image/png')
        monkeypatch.setattr('cognitrix.tools.image.current_session_id', lambda: 'session1')

        async def source_image(artifact_id, session_id):
            assert (artifact_id, session_id) == ('source1', 'session1')
            return source, base64.b64decode(_png())

        monkeypatch.setattr('cognitrix.tools.image.source_image', source_image)
        artifact = SimpleNamespace(id='artifact1', mime_type='image/png', filename='x.png', width=2, height=3)

        async def store(data, **kw):
            captured['store'] = kw
            return artifact

        monkeypatch.setattr('cognitrix.tools.image.store_png', store)

        out = await generate_image.run('Make it blue', source_artifact_id='source1')

        assert out.outcome.status == 'success'
        assert captured['payload']['input'][0] == {'type': 'text', 'text': 'Make it blue'}
        assert captured['payload']['input'][1]['type'] == 'image'
        assert captured['payload']['input'][1]['mime_type'] == 'image/png'
        assert 'role' not in captured['payload']['input'][0]
        assert captured['store']['source_artifact_id'] == 'source1'

    asyncio.run(run())


@pytest.mark.parametrize('aspect_ratio', ['1:4', '1:8', '4:1', '8:1'])
def test_generate_image_accepts_extended_gemini_aspect_ratios(monkeypatch, aspect_ratio):
    async def run():
        from cognitrix.tools.image import generate_image

        monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
        out = await generate_image.run('x', aspect_ratio=aspect_ratio)

        assert out.outcome.error.code == 'missing_google_api_key'

    asyncio.run(run())


def test_generate_image_uses_last_image_from_last_model_output_step(monkeypatch):
    async def run():
        from cognitrix.tools.image import generate_image

        monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
        captured = {}
        response_payload = {
            'steps': [
                {
                    'type': 'thought',
                    'content': [{'type': 'image', 'data': _png(2, 3, 'red')}],
                },
                {
                    'type': 'model_output',
                    'content': [{'type': 'image', 'data': _png(4, 5, 'blue')}],
                },
                {
                    'type': 'model_output',
                    'content': [
                        {'type': 'image', 'data': _png(6, 7, 'green')},
                        {'type': 'text', 'text': 'Final image'},
                        {'type': 'image', 'data': _png(8, 9, 'purple')},
                    ],
                },
            ],
        }

        class Response:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def raise_for_status(self): pass
            async def aiter_bytes(self):
                yield json.dumps(response_payload).encode()

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def stream(self, method, url, headers, json): return Response()

        monkeypatch.setattr('cognitrix.tools.image.httpx.AsyncClient', lambda **kw: Client())
        artifact = SimpleNamespace(
            id='artifact1', mime_type='image/png', filename='x.png', width=8, height=9
        )

        async def store(data, **kw):
            captured['data'] = data
            captured['store'] = kw
            return artifact

        monkeypatch.setattr('cognitrix.tools.image.store_png', store)

        out = await generate_image.run('A final image')

        assert out.outcome.status == 'success'
        assert captured['store']['width'] == 8
        assert captured['store']['height'] == 9
        with Image.open(io.BytesIO(captured['data'])) as image:
            assert image.getpixel((0, 0)) == (128, 0, 128)

    asyncio.run(run())


def test_interactions_image_selection_does_not_fall_back_from_invalid_last_image():
    from cognitrix.tools.image import _find_response_image

    response = {
        'steps': [{
            'type': 'model_output',
            'content': [
                {'type': 'image', 'data': _png()},
                {'type': 'image', 'data': 'not-base64'},
            ],
        }],
    }

    assert _find_response_image(response) is None


def test_generate_image_returns_provider_error_for_streamed_http_error(monkeypatch):
    async def run():
        from cognitrix.tools.image import generate_image

        monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')

        class ErrorStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"error":{"message":"invalid input"}}'

        provider_response = httpx.Response(
            400,
            request=httpx.Request('POST', 'https://example.test/interactions'),
            stream=ErrorStream(),
        )

        class Response:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def raise_for_status(self): provider_response.raise_for_status()
            async def aiter_bytes(self):
                async for chunk in provider_response.aiter_bytes():
                    yield chunk

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def stream(self, method, url, headers, json): return Response()

        monkeypatch.setattr('cognitrix.tools.image.httpx.AsyncClient', lambda **kw: Client())

        out = await generate_image.run('A red square')

        assert out.outcome.status == 'error'
        assert out.outcome.error.code == 'image_provider_error'
        assert 'invalid input' in out.outcome.error.message

    asyncio.run(run())
