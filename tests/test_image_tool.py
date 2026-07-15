import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image


def _png() -> str:
    buf = io.BytesIO()
    Image.new('RGB', (2, 3), 'red').save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.asyncio
async def test_generate_image_uses_stateless_one_shot_gemini_payload(monkeypatch):
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
    assert captured['payload']['response_format'] == {
        'type': 'image', 'image_size': '1K', 'aspect_ratio': '1:1', 'delivery': 'inline'
    }
    assert captured['payload']['generation_config'] == {'thinking_level': 'minimal'}
    assert out.outcome.artifacts[0].id == 'artifact1'


@pytest.mark.asyncio
async def test_generate_image_rejects_an_unsupported_ratio_before_provider(monkeypatch):
    from cognitrix.tools.image import generate_image
    monkeypatch.setenv('GOOGLE_API_KEY', 'test-key')
    out = await generate_image.run('x', aspect_ratio='square')
    assert out.outcome.error.code == 'invalid_aspect_ratio'
