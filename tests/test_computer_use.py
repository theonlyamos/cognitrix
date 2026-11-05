"""Desktop control, MCP browser tools and WebFetch link handling."""

import asyncio
import base64
import io

import pytest
from PIL import Image

import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM, LLMManager
from cognitrix.tools import misc
from cognitrix.tools.resilient_tool_wrapper import ToolResult


class FakeGui:
    def __init__(self, size=(1920, 1080)):
        self.size = size
        self.clicks = []
        self.typed = []

    def screenshot(self):
        return Image.new('RGB', self.size, 'white')

    def position(self):
        return (960, 540)

    def click(self, x, y):
        self.clicks.append((x, y))

    def write(self, text, interval=0):
        self.typed.append(text)


@pytest.fixture
def gui(monkeypatch):
    fake = FakeGui()
    monkeypatch.setattr(misc, '_pyautogui', lambda: fake)
    monkeypatch.setattr(misc, 'coords_normalized', False)
    misc.pending_screenshots.clear()
    yield fake
    misc.pending_screenshots.clear()


def test_desktop_tools_are_registered():
    from cognitrix.tools.base import ToolManager

    for name in ('take_screenshot', 'mouse_click', 'text_input', 'key_press', 'hot_key', 'mouse_scroll'):
        assert ToolManager.get_by_name(name) is not None, name


async def test_screenshot_is_scaled_and_queued_as_an_image(gui):
    out = (await misc.take_screenshot.run()).content
    assert '1280x720' in out
    assert len(misc.pending_screenshots) == 1
    uri = misc.pending_screenshots[0]
    assert uri.startswith('data:image/png;base64,')
    img = Image.open(io.BytesIO(base64.b64decode(uri.split(',', 1)[1])))
    assert img.size == (1280, 720)


async def test_clicks_scale_back_to_the_screen(gui):
    await misc.take_screenshot.run()
    await misc.mouse_click.run(x=640, y=360)
    assert gui.clicks == [(960, 540)]


async def test_gemini_grid_coordinates_are_converted(gui, monkeypatch):
    monkeypatch.setattr(misc, 'coords_normalized', True)
    out = (await misc.take_screenshot.run()).content
    assert '0-1000' in out
    await misc.mouse_click.run(x=500, y=500)
    assert gui.clicks == [(960, 540)]


def test_only_the_newest_screenshots_are_sent_as_images():
    llm = LLM(provider='openai', base_url='http://x', api_key='k', model='m')
    msgs = [{'role': 'User', 'type': 'text', 'content': 'go'}]
    msgs += [{'role': 'User', 'type': 'screenshot', 'content': f'data:image/png;base64,AA{i}'} for i in range(5)]
    out = LLMManager.format_query(llm, msgs)
    images = [m for m in out if isinstance(m['content'], list)]
    assert len(images) == 3
    assert images[-1]['content'][1]['image_url']['url'].endswith('AA4')
    assert sum(1 for m in out if m['content'] == '[older screenshot removed]') == 2


def test_screenshot_token_estimate_ignores_base64_length():
    from cognitrix.utils.tokens import estimate_tokens

    assert estimate_tokens({'type': 'screenshot', 'content': 'x' * 400_000}) == 1500


def _agent(tools):
    llm = LLM(provider='openai', base_url='http://x', api_key='k', model='m')
    return Agent(name='A', llm=llm, system_prompt='s', tools=tools,
                 tool_permissions={t.name: 'allow' for t in tools})


async def test_desktop_actions_in_one_batch_run_in_order(monkeypatch):
    log = []

    async def fake_run_tool(self, tool, params, **kw):
        log.append(('start', tool.name))
        await asyncio.sleep(0.01)
        log.append(('end', tool.name))
        return ToolResult(success=True, data='ok')

    monkeypatch.setattr(
        'cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool', fake_run_tool
    )
    tools = [Tool(name=n, description='d', parameters={}) for n in ('mouse_click', 'text_input')]
    monkeypatch.setattr('cognitrix.agents.base.ToolManager.get_by_name',
                        staticmethod(lambda name: next(t for t in tools if t.name == name)))
    agent = _agent(tools)
    await agent.call_tools([
        {'name': 'mouse_click', 'arguments': {}, 'tool_call_id': '1'},
        {'name': 'text_input', 'arguments': {}, 'tool_call_id': '2'},
    ], interface='web')
    assert log == [('start', 'mouse_click'), ('end', 'mouse_click'),
                   ('start', 'text_input'), ('end', 'text_input')]


# ── MCP wrappers ────────────────────────────────────────────────────────


class _Text:
    type = 'text'

    def __init__(self, text):
        self.text = text


class _Image:
    type = 'image'
    mime_type = 'image/png'
    data = 'QUJD'
    text = None


class _Client:
    def __init__(self):
        self.calls = []

    async def call_tool(self, key, name, args):
        self.calls.append((key, name, args))
        return [_Text('### Page'), _Image()]


@pytest.fixture
def mcp_client(monkeypatch):
    client = _Client()

    async def get_client():
        return client

    async def no_problem(key):
        return None

    monkeypatch.setattr('cognitrix.mcp.client.get_dynamic_client', get_client)
    monkeypatch.setattr('cognitrix.mcp.registry.prepare_key', no_problem)
    misc.pending_screenshots.clear()
    yield client
    misc.pending_screenshots.clear()


async def test_mcp_tools_accept_their_arguments(mcp_client):
    from cognitrix.mcp.tools import create_mcp_tool_wrapper

    wrapped = create_mcp_tool_wrapper('playwright', {
        'name': 'browser_navigate',
        'input_schema': {'type': 'object', 'properties': {'url': {'type': 'string'}}, 'required': ['url']},
        'annotations': {'readOnlyHint': True},
    }, 'conn-1')
    params = wrapped.validate_parameters({'url': 'https://example.com'})
    out = (await wrapped.run(**params)).content
    assert mcp_client.calls == [('conn-1', 'browser_navigate', {'url': 'https://example.com'})]
    assert out == '### Page\n[Image attached as the next message]'
    assert misc.pending_screenshots == ['data:image/png;base64,QUJD']


async def test_calls_to_one_mcp_server_run_in_order(monkeypatch):
    from cognitrix.mcp.tools import create_mcp_tool_wrapper

    log = []

    async def fake_run_tool(self, tool, params, **kw):
        log.append(('start', params['text']))
        await asyncio.sleep(0.01)
        log.append(('end', params['text']))
        return ToolResult(success=True, data='ok')

    monkeypatch.setattr(
        'cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool', fake_run_tool
    )
    wrapped = create_mcp_tool_wrapper('pw', {
        'name': 'browser_type',
        'input_schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}},
        'annotations': {'readOnlyHint': True},
    }, 'pw')
    agent = _agent([wrapped])
    await agent.call_tools([
        {'name': wrapped.name, 'arguments': {'text': 'a'}, 'tool_call_id': '1'},
        {'name': wrapped.name, 'arguments': {'text': 'b'}, 'tool_call_id': '2'},
    ], interface='web')
    assert log == [('start', 'a'), ('end', 'a'), ('start', 'b'), ('end', 'b')]


# ── WebFetch ────────────────────────────────────────────────────────────


async def test_webfetch_keeps_link_addresses(monkeypatch):
    import requests

    class Resp:
        is_redirect = False
        headers = {}
        text = ('<html><body><nav><a href="/docs">Docs</a></nav>'
                '<p>See the <a href="/release">release notes</a>.</p></body></html>')

        def raise_for_status(self):
            pass

    monkeypatch.setenv('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', '1')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Resp())
    out = (await misc.WebFetch.run(url='http://127.0.0.1:9/start')).content
    assert 'release notes (http://127.0.0.1:9/release)' in out
    assert '- Docs: http://127.0.0.1:9/docs' in out


async def test_webfetch_flags_javascript_pages(monkeypatch):
    import requests

    class Resp:
        is_redirect = False
        headers = {}
        text = '<html><body><div id="root">Loading...</div><script>render()</script></body></html>'

        def raise_for_status(self):
            pass

    monkeypatch.setenv('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', '1')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Resp())
    out = (await misc.WebFetch.run(url='http://127.0.0.1:9/app')).content
    assert 'JavaScript' in out
