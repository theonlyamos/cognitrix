"""Agents that run on a coding agent CLI signed in on this computer."""
import asyncio
import json
import os
import stat
import sys
import textwrap

import pytest

from cognitrix.common.folders import WRITE, Folder
from cognitrix.common.process_security import HostProcessMode
from cognitrix.providers import harness as h


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'workdir', tmp_path)
    return tmp_path


# --- config ------------------------------------------------------------------

def test_agent_config_needs_no_key_or_endpoint():
    from cognitrix.providers.base import LLM

    llm = LLM(provider='claude-code', model='sonnet', harness_mode='full', api_key='x', base_url='https://e.example')
    assert llm.provider == 'claude-code'
    assert llm.api_key == h.PLACEHOLDER_KEY
    assert llm.base_url == ''
    assert llm.harness_mode == 'full'


def test_modes_fall_back_to_edit():
    assert h.normalize_mode('read-only') == 'read_only'
    assert h.normalize_mode('FULL') == 'full'
    assert h.normalize_mode('anything') == 'edit'
    assert h.normalize_mode(None) == 'edit'


# --- command lines -------------------------------------------------------------

def _cmd(key, **kw):
    args = dict(mode='edit', model='', effort='', resume=None, new_session=None, cwd='/w')
    args.update(kw)
    return h.build_command(key, 'prog', **args)


def test_claude_code_command():
    argv, env = _cmd('claude-code', mode='read_only', model='opus', resume='s1')
    assert argv[:2] == ['prog', '-p']
    assert argv[argv.index('--permission-mode') + 1] == 'plan'
    assert argv[argv.index('--model') + 1] == 'opus'
    assert argv[argv.index('--resume') + 1] == 's1'
    assert env == {}
    full, _ = _cmd('claude-code', mode='full')
    assert full[full.index('--permission-mode') + 1] == 'bypassPermissions'


def test_codex_command_new_and_resumed():
    argv, _ = _cmd('codex', effort='high', model='gpt-6.1-sol')
    assert argv[:3] == ['prog', 'exec', '--json']
    assert argv[argv.index('--sandbox') + 1] == 'workspace-write'
    assert argv[argv.index('-C') + 1] == '/w'
    assert 'model_reasoning_effort="high"' in argv
    assert argv[-1] == '-'
    resumed, _ = _cmd('codex', mode='read_only', resume='th-1')
    assert resumed[:3] == ['prog', 'exec', 'resume']
    assert 'sandbox_mode="read-only"' in resumed
    assert resumed[-2:] == ['th-1', '-']


def test_gemini_and_copilot_pick_session_ids_up_front():
    argv, _ = _cmd('gemini-cli', mode='full', new_session='abc')
    assert argv[argv.index('--approval-mode') + 1] == 'yolo'
    assert argv[argv.index('--session-id') + 1] == 'abc'
    resumed, _ = _cmd('gemini-cli', resume='abc')
    assert resumed[resumed.index('--resume') + 1] == 'abc'

    argv, _ = _cmd('copilot-cli', new_session='cid', effort='low')
    assert argv[argv.index('--session-id') + 1] == 'cid'
    assert argv[argv.index('--allow-tool') + 1] == 'write'
    assert argv[argv.index('--reasoning-effort') + 1] == 'low'
    read_only, _ = _cmd('copilot-cli', mode='read_only', resume='cid')
    assert '--allow-tool' not in read_only and '--allow-all' not in read_only
    assert '--allow-all' in _cmd('copilot-cli', mode='full')[0]


def test_opencode_permissions_go_in_the_environment():
    argv, env = _cmd('opencode', mode='read_only', resume='ses_1')
    assert json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission'] == {'edit': 'deny', 'bash': 'deny'}
    assert argv[argv.index('--session') + 1] == 'ses_1'
    assert argv[argv.index('--dir') + 1] == '/w'


# --- reading output ----------------------------------------------------------------

def _events(key, *lines):
    out = []
    for line in lines:
        out += h.parse_line(key, json.dumps(line) if not isinstance(line, str) else line)
    return out


def test_parse_claude_code():
    ev = _events(
        'claude-code',
        {'type': 'system', 'subtype': 'init', 'session_id': 's1'},
        {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Hi'}}},
        {'type': 'stream_event', 'parent_tool_use_id': 't', 'event': {'type': 'content_block_delta',
                                                                       'delta': {'type': 'text_delta', 'text': 'sub'}}},
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}]}},
        {'type': 'result', 'subtype': 'success', 'session_id': 's1', 'result': 'Hi',
         'usage': {'input_tokens': 3, 'cache_read_input_tokens': 7, 'output_tokens': 2}},
        'not json',
    )
    assert ev[0].session == 's1'
    assert ev[1].text == 'Hi'
    assert ev[2].activity == '› Bash: ls\n'
    assert ev[3].usage == {'prompt_tokens': 10, 'completion_tokens': 2, 'cached_tokens': 7}
    assert ev[4].final_text == 'Hi'
    assert len(ev) == 5

    err = _events('claude-code', {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                                  'result': 'Invalid API key · Please run /login'})
    assert 'login' in err[-1].error


def test_parse_codex():
    ev = _events(
        'codex',
        {'type': 'thread.started', 'thread_id': 'th'},
        {'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'ls -la'}},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Done.'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 5, 'cached_input_tokens': 1, 'output_tokens': 2}},
        {'type': 'turn.failed', 'error': {'message': 'boom'}},
    )
    assert [e.session for e in ev][0] == 'th'
    assert ev[1].activity == '› ls -la\n'
    assert ev[2].text == 'Done.\n\n'
    assert ev[3].usage['cached_tokens'] == 1
    assert ev[4].error == 'boom'


def test_parse_gemini_copilot_opencode():
    g = _events('gemini-cli',
                {'type': 'init', 'session_id': 'g1'},
                {'type': 'message', 'role': 'user', 'content': 'ignored'},
                {'type': 'message', 'role': 'assistant', 'content': 'Yo', 'delta': True},
                {'type': 'result', 'status': 'success', 'stats': {'input_tokens': 4, 'output_tokens': 1}})
    assert [e.session for e in g][0] == 'g1' and g[1].text == 'Yo' and g[2].usage['prompt_tokens'] == 4

    c = _events('copilot-cli',
                {'type': 'assistant.message_delta', 'data': {'deltaContent': 'Hel'}},
                {'type': 'tool.execution_start', 'data': {'toolName': 'view', 'arguments': '{"path": "a.py"}'}},
                {'type': 'session.error', 'data': {'message': 'Not logged in'}})
    assert c[0].text == 'Hel' and c[1].activity == '› view: a.py\n' and c[2].error == 'Not logged in'

    o = _events('opencode',
                {'type': 'text', 'sessionID': 'ses', 'part': {'text': 'Answer'}},
                {'type': 'step_finish', 'sessionID': 'ses', 'part': {'tokens': {'input': 2, 'output': 3, 'cache': {'read': 4}}}})
    assert o[0].session == 'ses' and o[1].text == 'Answer\n\n'
    assert o[3].usage == {'prompt_tokens': 6, 'completion_tokens': 3, 'cached_tokens': 4}


# --- what gets sent ---------------------------------------------------------------

def test_first_turn_carries_instructions_and_history():
    messages = [
        {'role': 'system', 'content': 'Be brief.'},
        {'role': 'user', 'content': 'Hi'},
        {'role': 'assistant', 'content': 'Hello'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'What is 2+2?'}]},
        {'role': 'system', 'content': 'Today is Tuesday.'},
    ]
    instructions, earlier, new, users = h.split_prompt(messages)
    assert instructions == 'Be brief.'
    assert users == 2
    assert new == 'What is 2+2?\n\nToday is Tuesday.'
    text = h.first_message(instructions, earlier, new)
    assert '<instructions>\nBe brief.\n</instructions>' in text
    assert 'User: Hi' in text and 'You: Hello' in text
    assert text.endswith('The new message:\nWhat is 2+2?\n\nToday is Tuesday.')
    assert h.first_message('', [], 'Just this') == 'Just this'


def test_remembered_sessions_match_harness_model_and_folder(workdir):
    h.remember('chat1', 'codex', 'm', '/w', 'th', 1)
    assert h.remembered('chat1', 'codex', 'm', '/w')['session'] == 'th'
    assert h.remembered('chat1', 'codex', 'other', '/w') is None
    assert h.remembered('chat1', 'claude-code', 'm', '/w') is None
    assert h.remembered(None, 'codex', 'm', '/w') is None
    # Group chat teammates on the same CLI keep separate sessions.
    h.remember('chat1:agent-b', 'codex', 'm', '/w', 'th-b', 1)
    assert h.remembered('chat1:agent-a', 'codex', 'm', '/w') is None
    assert h.remembered('chat1', 'codex', 'm', '/w')['session'] == 'th'


# --- running a turn ---------------------------------------------------------------

FAKE_CLAUDE = textwrap.dedent('''\
    import json, os, sys
    prompt = sys.stdin.read()
    with open(os.environ['FAKE_LOG'], 'a') as fh:
        fh.write(json.dumps({'argv': sys.argv[1:], 'stdin': prompt, 'cwd': os.getcwd()}) + '\\n')
    if os.environ.get('FAKE_FAIL'):
        sys.stderr.write('Not logged in. Please run /login\\n')
        sys.exit(1)
    def out(o):
        print(json.dumps(o), flush=True)
    out({'type': 'system', 'subtype': 'init', 'session_id': 'sess-1'})
    out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'a.py'}}]}})
    for word in ['Four', '.']:
        out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': word}}})
    out({'type': 'result', 'subtype': 'success', 'session_id': 'sess-1', 'result': 'Four.',
         'usage': {'input_tokens': 10, 'output_tokens': 2}})
''')


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / 'fake_claude.py'
    script.write_text(FAKE_CLAUDE)
    if os.name == 'nt':
        launcher = tmp_path / 'claude.cmd'
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        launcher = tmp_path / 'claude'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('COGNITRIX_CLAUDE_CODE_PATH', str(launcher))
    monkeypatch.setenv('FAKE_LOG', str(log))
    return log


def _llm(**kw):
    from cognitrix.providers.base import LLM

    return LLM(provider='claude-code', model='sonnet', **kw)


async def _run(llm, messages, folder, chat='chat-1', mode=HostProcessMode.TRUSTED_LOCAL):
    token = h.turn.set(h.TurnInfo(session_id=chat, folders=(folder,), host_process_mode=mode))
    try:
        chunks, last = [], None
        async for item in h.stream(llm, messages):
            chunks.append(item.current_chunk)
            last = item
        return ''.join(chunks), last
    finally:
        h.turn.reset(token)


def test_turn_streams_then_resumes_the_cli_session(workdir, fake_claude, tmp_path):
    folder = Folder(str(tmp_path), WRITE, 'work', True)
    first = [{'role': 'system', 'content': 'Be brief.'}, {'role': 'user', 'content': 'What is 2+2?'}]
    shown, last = asyncio.run(_run(_llm(), first, folder))
    assert shown == '<think>› Read: a.py\n\n</think>\nFour.'
    assert last.llm_response == 'Four.'
    assert last.usage == {'prompt_tokens': 10, 'completion_tokens': 2, 'cached_tokens': 0}

    second = first + [{'role': 'assistant', 'content': 'Four.'}, {'role': 'user', 'content': 'And 3+3?'}]
    asyncio.run(_run(_llm(), second, folder))
    calls = [json.loads(line) for line in fake_claude.read_text().splitlines()]
    assert '<instructions>\nBe brief.\n</instructions>' in calls[0]['stdin']
    assert '--resume' not in calls[0]['argv']
    assert calls[0]['argv'][calls[0]['argv'].index('--permission-mode') + 1] == 'acceptEdits'
    assert os.path.realpath(calls[0]['cwd']) == os.path.realpath(tmp_path)
    # The CLI remembers the chat, so only the new message goes.
    assert calls[1]['stdin'] == 'And 3+3?'
    assert calls[1]['argv'][calls[1]['argv'].index('--resume') + 1] == 'sess-1'


def test_edited_history_starts_a_fresh_cli_session(workdir, fake_claude, tmp_path):
    folder = Folder(str(tmp_path), WRITE, 'work', True)
    h.remember('chat-1', 'claude-code', 'sonnet', str(tmp_path), 'old', 5)
    asyncio.run(_run(_llm(), [{'role': 'user', 'content': 'Hi'}], folder))
    call = json.loads(fake_claude.read_text().splitlines()[0])
    assert '--resume' not in call['argv']


def test_read_only_folder_forces_read_only_mode(workdir, fake_claude, tmp_path):
    folder = Folder(str(tmp_path), 'read', 'docs', True)
    shown, _ = asyncio.run(_run(_llm(harness_mode='full'), [{'role': 'user', 'content': 'Hi'}], folder))
    call = json.loads(fake_claude.read_text().splitlines()[0])
    assert call['argv'][call['argv'].index('--permission-mode') + 1] == 'plan'
    assert "'docs' is read only" in shown


def test_sign_in_failure_says_how_to_sign_in(workdir, fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_FAIL', '1')
    _, last = asyncio.run(_run(_llm(), [{'role': 'user', 'content': 'Hi'}], Folder(str(tmp_path), WRITE, 'w', True)))
    assert last.error.startswith('Claude Code failed: Not logged in')
    assert 'Sign in to Claude Code on this computer first' in last.error


def test_refused_on_a_shared_server(workdir, fake_claude, tmp_path):
    _, last = asyncio.run(_run(_llm(), [{'role': 'user', 'content': 'Hi'}], Folder(str(tmp_path), WRITE, 'w', True),
                               mode=HostProcessMode.DENY))
    assert 'not on a shared server' in last.error
    assert not fake_claude.exists()


def test_missing_cli_says_how_to_install(workdir, tmp_path, monkeypatch):
    monkeypatch.setenv('COGNITRIX_CLAUDE_CODE_PATH', str(tmp_path / 'nope'))
    _, last = asyncio.run(_run(_llm(), [{'role': 'user', 'content': 'Hi'}], Folder(str(tmp_path), WRITE, 'w', True)))
    assert 'not installed here' in last.error and 'npm install -g @anthropic-ai/claude-code' in last.error


# --- lending Cognitrix's tools to the CLI (harness_bridge.py) -------------------------

def _tool_dict(name, **props):
    return {'type': 'function', 'function': {
        'name': name, 'description': f'{name} tool',
        'parameters': {'type': 'object', 'properties': props}}}


def test_lendable_leaves_out_what_the_cli_does_itself():
    from cognitrix.providers.harness_bridge import lendable

    tools = lendable([_tool_dict('Memory'), _tool_dict('Bash'), _tool_dict('Read'), _tool_dict('Coding_Agent'),
                      _tool_dict('Update Task'), _tool_dict('Memory'), {'bad': 1}])
    assert [t['name'] for t in tools] == ['Memory', 'Update_Task']
    assert tools[1]['source_name'] == 'Update Task'


def test_commands_carry_the_tools_server(tmp_path):
    url = 'http://127.0.0.1:1/mcp/tok'
    cfg = str(tmp_path / 'cfg.json')
    claude, _ = _cmd('claude-code', mode='ask', tools_url=url, settings_file=cfg)
    assert claude[claude.index('--mcp-config') + 1] == cfg
    assert json.loads((tmp_path / 'cfg.json').read_text())['mcpServers']['cognitrix']['url'] == url
    assert claude[claude.index('--permission-mode') + 1] == 'default'
    assert claude[claude.index('--permission-prompt-tool') + 1] == 'mcp__cognitrix__approve'
    assert claude[claude.index('--allowedTools') + 1] == 'mcp__cognitrix'
    # Plan mode would refuse the lent tools, so read only is enforced another way.
    read_only, _ = _cmd('claude-code', mode='read_only', tools_url=url, settings_file=cfg)
    assert read_only[read_only.index('--permission-mode') + 1] == 'default'
    assert '--disallowedTools' in read_only and '--permission-prompt-tool' not in read_only
    # Nobody to ask without the server: 'ask' becomes 'edit'.
    alone, _ = _cmd('claude-code', mode='ask')
    assert alone[alone.index('--permission-mode') + 1] == 'acceptEdits'

    codex, _ = _cmd('codex', mode='ask', tools_url=url)
    assert f'mcp_servers.cognitrix.url={url}' in codex
    assert 'mcp_servers.cognitrix.default_tools_approval_mode=approve' in codex
    assert codex[codex.index('--sandbox') + 1] == 'workspace-write'

    copilot, _ = _cmd('copilot-cli', tools_url=url, settings_file=cfg)
    assert copilot[copilot.index('--additional-mcp-config') + 1] == '@' + cfg
    assert json.loads((tmp_path / 'cfg.json').read_text())['mcpServers']['cognitrix']['tools'] == ['*']
    assert copilot[-2:] == ['--allow-tool', 'cognitrix']

    _, env = _cmd('opencode', tools_url=url)
    assert json.loads(env['OPENCODE_CONFIG_CONTENT'])['mcp']['cognitrix']['url'] == url


def test_gemini_gets_the_tools_server_in_a_settings_file(tmp_path):
    path = tmp_path / 'g.json'
    _, env = _cmd('gemini-cli', tools_url='http://127.0.0.1:1/mcp/t', settings_file=str(path))
    assert env['GEMINI_CLI_SYSTEM_DEFAULTS_PATH'] == str(path)
    assert json.loads(path.read_text())['mcpServers']['cognitrix'] == {
        'httpUrl': 'http://127.0.0.1:1/mcp/t', 'trust': True}


async def _rpc(url, method, params=None, id_=1):
    import httpx

    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(
            url, json={'jsonrpc': '2.0', 'id': id_, 'method': method, 'params': params or {}},
            headers={'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'})
    return response


async def test_bridge_serves_tools_and_keeps_the_turn_context():
    import contextvars

    from cognitrix.providers.harness_bridge import Bridge, lendable

    who = contextvars.ContextVar('who', default='nobody')
    asked = []

    async def call(name, args):
        return f'{name} {args} for {who.get()}', args.get('ok', True)

    async def approve(name, params):
        asked.append((name, params))
        return 'rm' not in params.get('command', ''), 'No.'

    who.set('amos')
    bridge = await Bridge(tools=lendable([_tool_dict('Update Task', ok={'type': 'boolean'})]), call_tool=call,
                          approve=approve, context=contextvars.copy_context()).start()
    who.set('someone else')
    try:
        listed = (await _rpc(bridge.url, 'tools/list')).json()['result']['tools']
        assert [t['name'] for t in listed] == ['Update_Task', 'approve']
        ran = (await _rpc(bridge.url, 'tools/call', {'name': 'Update_Task', 'arguments': {}})).json()['result']
        assert ran['content'][0]['text'] == 'Update Task {} for amos' and not ran['isError']
        failed = (await _rpc(bridge.url, 'tools/call', {'name': 'Update_Task', 'arguments': {'ok': False}})).json()
        assert failed['result']['isError']
        missing = (await _rpc(bridge.url, 'tools/call', {'name': 'Bash', 'arguments': {}})).json()['result']
        assert missing['isError']
        yes = (await _rpc(bridge.url, 'tools/call', {'name': 'approve', 'arguments': {
            'tool_name': 'Bash', 'input': {'command': 'ls'}}})).json()['result']
        assert json.loads(yes['content'][0]['text']) == {'behavior': 'allow', 'updatedInput': {'command': 'ls'}}
        no = (await _rpc(bridge.url, 'tools/call', {'name': 'approve', 'arguments': {
            'tool_name': 'Bash', 'input': {'command': 'rm -rf x'}}})).json()['result']
        assert json.loads(no['content'][0]['text']) == {'behavior': 'deny', 'message': 'No.'}
        # Only the address with this turn's token works.
        assert (await _rpc(bridge.url + 'x', 'tools/list')).status_code == 404
        assert (await _rpc(bridge.url.rsplit('/', 1)[0], 'tools/list')).status_code == 404
    finally:
        await bridge.stop()
    import httpx
    with pytest.raises(httpx.HTTPError):
        await _rpc(bridge.url, 'tools/list')


FAKE_TOOL_USER = textwrap.dedent('''\
    import json, sys, urllib.request
    sys.stdin.read()
    argv = sys.argv[1:]
    url = json.load(open(argv[argv.index('--mcp-config') + 1]))['mcpServers']['cognitrix']['url']
    def rpc(method, params):
        req = urllib.request.Request(url, data=json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(), headers={
            'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': '2025-06-18'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return json.loads(opener.open(req, timeout=20).read())['result']
    names = [t['name'] for t in rpc('tools/list', {})['tools']]
    got = rpc('tools/call', {'name': 'Lookup', 'arguments': {'q': 'colour'}})
    said = 'Tools: ' + ','.join(names) + '. Lookup said: ' + got['content'][0]['text']
    if '--permission-prompt-tool' in argv:
        asked = rpc('tools/call', {'name': 'approve', 'arguments': {'tool_name': 'Bash', 'input': {'command': 'make'}}})
        said += ' Asked: ' + json.loads(asked['content'][0]['text'])['behavior']
    def out(o):
        print(json.dumps(o), flush=True)
    out({'type': 'system', 'subtype': 'init', 'session_id': 's'})
    out({'type': 'result', 'subtype': 'success', 'session_id': 's', 'result': said, 'usage': {}})
''')


async def test_a_chat_turn_lends_the_agents_tools_and_asks_first(workdir, tmp_path, monkeypatch):
    from cognitrix.models.agent import Agent
    from cognitrix.models.tool import Tool
    from cognitrix.providers.base import LLM
    from cognitrix.safety.approval_gate import ApprovalResult
    from cognitrix.sessions.base import Session
    from cognitrix.tools.resilient_tool_wrapper import ToolResult
    from cognitrix.tools.utils import ToolExecutionContext

    script = tmp_path / 'fake.py'
    script.write_text(FAKE_TOOL_USER)
    if os.name == 'nt':
        launcher = tmp_path / 'claude.cmd'
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        launcher = tmp_path / 'claude'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv('COGNITRIX_CLAUDE_CODE_PATH', str(launcher))

    lookup = Tool(name='Lookup', description='Look something up', parameters={'q': 'str'})
    shell = Tool(name='Bash', description='Shell', parameters={})
    agent = Agent(name='Pip', system_prompt='Test', tools=[lookup, shell],
                  llm=LLM(provider='claude-code', model='sonnet', harness_mode='ask'))
    session = Session(agent_id=str(agent.id))
    ran, asked, events = [], [], []

    async def fake_run_tool(self, tool, params, **kwargs):
        ran.append((tool.name, dict(params)))
        return ToolResult(success=True, data='teal')

    async def fake_check(tool_call, risk, interface='cli', **kwargs):
        asked.append((tool_call.tool_name, tool_call.params, kwargs.get('person_only')))
        return ApprovalResult(approved=True)

    async def no_save(self):
        return None

    async def sink(event):
        events.append(event)

    monkeypatch.setattr('cognitrix.agents.base.ToolManager.get_by_name', staticmethod(lambda name: None))
    monkeypatch.setattr('cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool', fake_run_tool)
    from cognitrix.agents.base import AgentManager as _Manager
    monkeypatch.setattr(_Manager.approval_gate, 'check_approval', fake_check)
    monkeypatch.setattr(Session, 'save', no_save)

    context = ToolExecutionContext(host_process_mode=HostProcessMode.TRUSTED_LOCAL,
                                   folders=(Folder(str(tmp_path), WRITE, 'work', True),))
    await session('What is my favourite colour?', agent, 'web', True, sink, {'type': 'chat', 'action': 'x'}, True,
                  tool_context=context)

    said = ''.join(str(e.get('content') or '') for e in events if e.get('type') == 'chat')
    assert 'Tools: Lookup,approve.' in said  # Bash stays the CLI's own
    assert 'Lookup said: teal' in said and 'Asked: allow' in said
    assert ran == [('Lookup', {'q': 'colour'})]
    assert asked == [('Claude Code: Bash', {'command': 'make'}, True)]
    cards = [(e['status'], e['tool_name']) for e in events if e.get('type') == 'tool']
    assert cards == [('started', 'Lookup'), ('completed', 'Lookup')]


async def test_file_changes_in_the_working_folder_are_not_asked_about(tmp_path, monkeypatch):
    from cognitrix.agents.base import AgentManager
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.safety.approval_gate import ApprovalResult

    asked = []

    async def fake_check(tool_call, risk, interface='cli', **kwargs):
        asked.append((tool_call.tool_name, risk.risk_level.value, kwargs.get('person_only')))
        return ApprovalResult(approved=False)

    from cognitrix.agents.base import AgentManager as _Manager
    monkeypatch.setattr(_Manager.approval_gate, 'check_approval', fake_check)
    manager = AgentManager(Agent(name='Pip', system_prompt='T', tools=[],
                                 llm=LLM(provider='claude-code', model='sonnet')))
    folders = (Folder(str(tmp_path), WRITE, 'work', True),)
    inside = await manager.approve_cli_action('Claude Code', 'Write', {'file_path': str(tmp_path / 'a.txt')},
                                              'web', folders)
    outside = await manager.approve_cli_action('Claude Code', 'Edit', {'file_path': str(tmp_path.parent / 'b.txt')},
                                               'web', folders)
    secret = await manager.approve_cli_action('Claude Code', 'Bash', {'command': 'cat .env'}, 'web', folders)
    assert inside == (True, '')
    assert outside[0] is False and secret[0] is False
    assert asked == [('Claude Code: Edit', 'medium', False), ('Claude Code: Bash', 'high', True)]


def test_newer_openai_models_use_the_responses_api_for_tools():
    from cognitrix.providers import base
    from cognitrix.providers.base import LLM

    assert base._wants_responses_api(LLM(provider='openai', model='gpt-6.1-sol', api_key='k'))
    assert not base._wants_responses_api(LLM(provider='openai', model='gpt-5.5', api_key='k'))
    # Other OpenAI-compatible servers keep chat completions.
    assert not base._wants_responses_api(
        LLM(provider='openai', model='gpt-6.1-sol', api_key='k', base_url='http://localhost:1234/v1'))
    assert not base._wants_responses_api(LLM(provider='openrouter', model='openai/gpt-6.1-sol', api_key='k'))
