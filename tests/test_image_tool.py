import asyncio
import base64
import io
import json
from types import SimpleNamespace

from PIL import Image


def _png() -> str:
    buf = io.BytesIO()
    Image.new('RGB', (2, 3), 'red').save(buf, format='PNG')
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
        assert captured['payload']['model'] == 'gemini-3.1-flash-lite-image'
        assert captured['payload']['store'] is False
        assert captured['payload']['input'] == 'A red square'
        assert captured['payload']['response_format'] == {
            'type': 'image', 'image_size': '1K', 'aspect_ratio': '1:1', 'delivery': 'inline'
        }
        assert captured['payload']['generation_config'] == {'thinking_level': 'minimal'}
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
