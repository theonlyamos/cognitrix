"""Cognitrix as an Agent Client Protocol server (cognitrix/acp/server.py).

An editor-side ACP client (the SDK's own) talks to the server over pipes in
the same event loop, with the model faked.
"""

import asyncio
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip('acp')

from acp import PROTOCOL_VERSION, connect_to_agent, run_agent  # noqa: E402
from acp.schema import (  # noqa: E402
    AllowedOutcome,
    RequestPermissionResponse,
    ResourceContentBlock,
    TextContentBlock,
)

import cognitrix.agents  # noqa: E402,F401  (attaches call_tools to Agent)
from cognitrix.acp import server  # noqa: E402
from cognitrix.models.agent import Agent  # noqa: E402
from cognitrix.providers.base import LLM  # noqa: E402
from cognitrix.sessions.base import Session  # noqa: E402
from cognitrix.tools.tool import tool  # noqa: E402
from cognitrix.utils.llm_response import LLMResponse  # noqa: E402

RAN: list[dict] = []


@tool(category='test')
async def look_around(path: str) -> str:
    """Look at a folder."""
    RAN.append({'look': path})
    return f'{path} has 3 files'


class EditorClient:
    """The editor: records updates and answers permission prompts."""

    def __init__(self, answer: str = 'allow_once'):
        self.updates: list = []
        self.asked: list = []
        self.answer = answer

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.asked.append(tool_call)
        return RequestPermissionResponse(outcome=AllowedOutcome(option_id=self.answer))

    def on_connect(self, conn):
        pass


async def _pipe():
    loop = asyncio.get_running_loop()
    r, w = os.pipe()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(r, 'rb', 0))
    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, os.fdopen(w, 'wb', 0))
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    return reader, writer


def _agent(name: str, tools=()) -> Agent:
    return Agent(
        id=f'agent-{name.lower()}',
        name=name,
        llm=LLM(provider='openai', base_url='http://example.invalid', api_key='test', model='test'),
        system_prompt='Test',
        tools=list(tools),
        description=f'{name} agent',
    )


@pytest.fixture
def agents(monkeypatch):
    RAN.clear()
    saved = {}
    team = [_agent('Assistant', [look_around]), _agent('Coder', [look_around])]

    async def fake_agents(self):
        return team

    async def fake_setup(self):
        return None

    async def fake_save(self):
        saved[str(self.id)] = self
        return self

    async def fake_find_one(cls, query):
        return saved.get(query.get('id'))

    monkeypatch.setattr(server.CognitrixAgent, '_agents', fake_agents)
    monkeypatch.setattr(server.CognitrixAgent, '_setup', fake_setup)
    monkeypatch.setattr(Session, 'save', fake_save)
    monkeypatch.setattr(Session, 'find_one', classmethod(fake_find_one))
    return team, saved


async def _connect(client):
    agent_in, editor_out = await _pipe()   # editor -> agent
    editor_in, agent_out = await _pipe()   # agent -> editor
    cog = server.CognitrixAgent()
    serving = asyncio.create_task(run_agent(cog, agent_out, agent_in))
    conn = connect_to_agent(client, editor_out, editor_in)
    return conn, cog, serving


def _fake_model(monkeypatch, steps):
    prompts = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        prompts.append(prompt)
        step = steps[min(len(prompts) - 1, len(steps) - 1)]
        response = LLMResponse()
        if 'tool' in step:
            response.tool_calls = [{'name': step['tool'], 'arguments': step.get('args', {}),
                                    'tool_call_id': f'call-{len(prompts)}'}]
        else:
            response.add_chunk(step['text'])

        if stream:
            async def gen():
                yield response
            return gen()
        return response

    monkeypatch.setattr('cognitrix.providers.base.LLMManager.generate_response', staticmethod(fake_generate))
    return prompts


async def test_editor_chat_streams_text_and_shows_tool_calls(agents, monkeypatch, tmp_path):
    prompts = _fake_model(monkeypatch, [
        {'tool': 'look_around', 'args': {'path': 'src'}},
        {'text': 'There are 3 files.'},
    ])
    client = EditorClient()
    conn, cog, serving = await _connect(client)
    try:
        init = await conn.initialize(protocol_version=PROTOCOL_VERSION)
        assert init.protocol_version == PROTOCOL_VERSION
        assert init.agent_capabilities.load_session is True
        assert init.agent_info.name == 'cognitrix'

        new = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
        assert [m.name for m in new.modes.available_modes] == ['Assistant', 'Coder']
        assert new.modes.current_mode_id == str(agents[0][0].id)

        result = await conn.prompt(session_id=new.session_id, prompt=[
            TextContentBlock(text='What is in src?'),
            ResourceContentBlock(uri=f'file://{tmp_path}/main.py', name='main.py'),
        ])
        assert result.stop_reason == 'end_turn'
    finally:
        serving.cancel()

    kinds = [u.session_update for u in client.updates]
    assert 'tool_call' in kinds and 'tool_call_update' in kinds and 'agent_message_chunk' in kinds
    start = next(u for u in client.updates if u.session_update == 'tool_call')
    assert start.title == 'Look Around: src'
    assert start.status == 'in_progress'
    assert start.locations[0].path == os.path.join(str(tmp_path), 'src')
    done = next(u for u in client.updates if u.session_update == 'tool_call_update')
    assert done.status == 'completed' and 'has 3 files' in done.content[0].content.text
    text = ''.join(u.content.text for u in client.updates if u.session_update == 'agent_message_chunk')
    assert 'There are 3 files.' in text
    assert RAN == [{'look': 'src'}]
    # The @-mentioned file reached the model, and the chat works in the editor's folder.
    user_turn = json.dumps(prompts[0], default=str)
    assert 'main.py' in user_turn
    chat = cog.chats[new.session_id]
    assert chat.session.folders[0]['path'] == os.path.abspath(str(tmp_path))
    assert chat.session.folders[0]['access'] == 'write'


async def test_risky_calls_ask_in_the_editor(agents, monkeypatch, tmp_path):
    from cognitrix.agents.base import AgentManager

    AgentManager.approval_gate.session_cache.clear()
    agents[0][0].tool_permissions = {'Look Around': 'ask'}
    _fake_model(monkeypatch, [{'tool': 'look_around', 'args': {'path': 'secrets'}}, {'text': 'ok'}])
    client = EditorClient(answer='reject_once')
    conn, _cog, serving = await _connect(client)
    try:
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        new = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
        await conn.prompt(session_id=new.session_id, prompt=[TextContentBlock(text='look')])
    finally:
        serving.cancel()
        agents[0][0].tool_permissions = {}

    assert len(client.asked) == 1
    assert client.asked[0].title.startswith('Look Around: secrets')
    assert RAN == []
    failed = [u for u in client.updates if u.session_update == 'tool_call_update']
    assert failed and failed[0].status == 'failed'


async def test_switch_agent_cancel_and_reopen(agents, monkeypatch, tmp_path):
    team, _saved = agents
    release = asyncio.Event()

    async def slow_generate(llm, prompt, stream=False, tools=None, **kwargs):
        await release.wait()
        response = LLMResponse()
        response.add_chunk('late')
        return response

    monkeypatch.setattr('cognitrix.providers.base.LLMManager.generate_response', staticmethod(slow_generate))
    client = EditorClient()
    conn, cog, serving = await _connect(client)
    try:
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        new = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
        await conn.set_session_mode(session_id=new.session_id, mode_id=str(team[1].id))
        assert cog.chats[new.session_id].agent is team[1]

        running = asyncio.create_task(conn.prompt(session_id=new.session_id, prompt=[TextContentBlock(text='hi')]))
        await asyncio.sleep(0.2)
        await conn.cancel(session_id=new.session_id)
        result = await asyncio.wait_for(running, 5)
        assert result.stop_reason == 'cancelled'

        # Reopen: the editor gets the chat's messages back.
        cog.chats[new.session_id].session.chat = [
            {'role': 'User', 'type': 'text', 'content': 'earlier question'},
            {'role': 'assistant', 'type': 'text', 'content': 'earlier answer'},
        ]
        client.updates.clear()
        await conn.load_session(cwd=str(tmp_path), session_id=new.session_id, mcp_servers=[])
        texts = [(u.session_update, u.content.text) for u in client.updates]
        assert texts == [('user_message_chunk', 'earlier question'), ('agent_message_chunk', 'earlier answer')]
    finally:
        release.set()
        serving.cancel()


def test_prompt_text_and_titles():
    blocks = [
        TextContentBlock(text='Fix this'),
        ResourceContentBlock(uri='file:///C:/repo/app.py', name='app.py'),
    ]
    assert server.prompt_text(blocks) == 'Fix this\n\n[File: C:/repo/app.py]'
    assert server.tool_title('bash', {'command': 'pytest -q\nmore'}) == 'Bash: pytest -q'
    assert server.tool_kind('Edit') == 'edit'
    assert server.tool_kind('mystery') == 'other'


def test_stdout_only_carries_protocol_messages(tmp_path):
    """A real `cognitrix acp` process: replies on stdout, everything else on stderr."""
    env = {**os.environ, 'HOME': str(tmp_path), 'USERPROFILE': str(tmp_path)}
    code = (
        "import sys; sys.argv = ['cognitrix', 'acp'];"
        "print('noise before the server starts', flush=True);"
        "import cognitrix.acp.server as s;"
        "orig = s.CognitrixAgent.initialize\n"
        "async def noisy(self, *a, **k):\n"
        "    print('a stray print from a tool')\n"
        "    return await orig(self, *a, **k)\n"
        "s.CognitrixAgent.initialize = noisy\n"
        "from cognitrix.cli.main import main; main()"
    )
    proc = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)

    def send(msg_id, method, params):
        proc.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': msg_id, 'method': method, 'params': params})
                          + '\n').encode())
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        assert proc.stdout.readline().decode().strip() == 'noise before the server starts'
        reply = send(1, 'initialize', {'protocolVersion': PROTOCOL_VERSION, 'clientCapabilities': {}})
        assert reply['id'] == 1 and reply['result']['protocolVersion'] == PROTOCOL_VERSION
        # Setting up the database on the first session prints nothing to stdout.
        reply = send(2, 'session/new', {'cwd': str(tmp_path), 'mcpServers': []})
        assert reply['id'] == 2 and 'No agents yet' in reply['error']['data']['message']
    finally:
        proc.stdin.close()
        out, err = proc.communicate(timeout=60)
    assert out.decode().strip() == ''
    assert 'a stray print from a tool' in err.decode()
