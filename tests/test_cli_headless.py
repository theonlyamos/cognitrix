"""Headless CLI runs (cognitrix/cli/headless.py) and the CLI's own commands
(agents, sessions, keys): output formats, exit codes, per-run options."""

import io
import json
import sys
from argparse import Namespace
from types import SimpleNamespace

import pytest

from tests.test_onboarding import db  # noqa: F401


def _parse(argv):
    from cognitrix.cli.args import get_arguments

    sys.argv = ["cognitrix", *argv]
    return get_arguments()


# ---------------------------------------------------------------- arguments

def test_headless_flags_parse():
    a = _parse(["-p", "x", "--output-format", "stream-json", "--max-turns", "5", "--timeout", "30",
                "--effort", "low", "-C", "/tmp", "--allowed-tools", "Read,Edit"])
    assert a.output_format == "stream-json" and a.max_turns == 5 and a.timeout == 30
    assert a.effort == "low" and a.cwd == "/tmp" and a.allowed_tools == "Read,Edit"
    assert _parse(["-p", "x"]).output_format == "text"
    with pytest.raises(SystemExit):
        _parse(["-p", "x", "--output-format", "yaml"])


def test_resume_and_old_session_flag_are_the_same():
    assert _parse(["--resume", "abc"]).resume == "abc"
    assert _parse(["-r", "abc"]).resume == "abc"
    assert _parse(["--session", "abc"]).resume == "abc"


def test_subcommands_parse():
    assert _parse(["tools", "-l"]).list == "all"  # used to be None: nothing printed
    assert _parse(["tools", "-l", "apps"]).list == "apps"  # any category, not only 4
    a = _parse(["agents", "Coder", "--new", "--provider", "anthropic", "--json"])
    assert a.new and a.provider == "anthropic" and a.name == "Coder" and a.json
    assert _parse(["keys", "set", "anthropic", "--stdin"]).stdin is True
    assert _parse(["sessions", "show", "abc", "--json"]).json is True
    assert _parse(["sessions", "--json"]).json is True
    assert _parse(["sessions"]).json is False


# ---------------------------------------------------------------- per-run options

def _tool(name):
    return SimpleNamespace(name=name)


def test_run_options_filter_tools_and_prompt(monkeypatch):
    from cognitrix.cli import headless
    from cognitrix.safety import permissions
    from cognitrix.sessions import base as session_base

    monkeypatch.setattr(session_base, 'MAX_TOOL_ROUNDS', 0)
    token = permissions.run_tool_limits.set(None)
    try:
        agent = SimpleNamespace(system_prompt='Base.', tools=[_tool('Read'), _tool('Bash'), _tool('Web Fetch')],
                                tool_permissions={})
        headless.apply_run_options(agent, Namespace(
            system_prompt='', append_system_prompt='Be brief.', allowed_tools='', disallowed_tools='bash, web_fetch',
            max_turns=3))
        visible = [t.name for t in permissions.visible_tools(agent, agent.tools + [_tool('Run Code')])]
        assert visible == ['Read', 'Run Code']
        assert agent.system_prompt == 'Base.\n\nBe brief.'
        assert session_base.MAX_TOOL_ROUNDS == 3

        # --allowed-tools also hides the tools every chat gets (Run Code ...).
        agent = SimpleNamespace(system_prompt='Base.', tools=[_tool('Read'), _tool('Bash')], tool_permissions={})
        headless.apply_run_options(agent, Namespace(system_prompt='New.', allowed_tools='read'))
        visible = [t.name for t in permissions.visible_tools(agent, agent.tools + [_tool('Run Code')])]
        assert visible == ['Read'] and agent.system_prompt == 'New.'
        assert permissions.is_denied(agent, 'run_code')
        assert agent.tool_permissions == {}  # nothing stored on the agent
    finally:
        permissions.run_tool_limits.reset(token)


def test_read_prompt_from_stdin_and_file(monkeypatch, tmp_path):
    from cognitrix.cli import headless

    monkeypatch.setattr(sys, 'stdin', io.StringIO('from stdin'))
    assert headless.read_prompt(Namespace(prompt='-', prompt_file='')) == 'from stdin'
    f = tmp_path / 'p.txt'
    f.write_text('from file', encoding='utf-8')
    assert headless.read_prompt(Namespace(prompt='ignored', prompt_file=str(f))) == 'from file'
    with pytest.raises(headless.UsageError):
        headless.read_prompt(Namespace(prompt='', prompt_file=str(tmp_path / 'missing.txt')))


# ---------------------------------------------------------------- one-shot runs

class _Session:
    def __init__(self, error=None):
        self.id = 'sess-1'
        self.user_id = None
        self.last_turn_error = error
        self.token_usage = {'last_turn': {'models': {'anthropic|m': {
            'provider': 'anthropic', 'model': 'm', 'calls': 2, 'prompt_tokens': 100, 'completion_tokens': 7}}}}


_AGENT = SimpleNamespace(id='a1', name='Assistant', tools=[_tool('Read')],
                         llm=SimpleNamespace(provider='anthropic', model='m'))


def _args(fmt, **extra):
    return Namespace(output_format=fmt, timeout=0, task=False, stream=True, verbose=False, **extra)


async def _fake_turn(session, agent, text, *, stream, channel):
    await channel.emit({'content': 'Let me look. '})
    await channel.emit({'type': 'tool', 'status': 'started', 'tool_name': 'Read', 'tool_call_id': 't1',
                        'params': '{"file_path": "a.txt"}'})
    await channel.emit({'type': 'tool', 'status': 'completed', 'tool_name': 'Read', 'tool_call_id': 't1',
                        'result': 'hello'})
    await channel.emit({'content': 'The file says hello.'})


async def _run(monkeypatch, capsys, fmt, turn=_fake_turn, session=None, **extra):
    from cognitrix.cli import headless

    monkeypatch.setattr(headless, '_local_turn', turn)
    code = await headless.run_prompt(session or _Session(), _AGENT, 'read a.txt', _args(fmt, **extra))
    return code, capsys.readouterr().out


async def test_stream_json_events(monkeypatch, capsys):
    code, out = await _run(monkeypatch, capsys, 'stream-json')
    events = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert [e['type'] for e in events] == ['system', 'text', 'tool_use', 'tool_result', 'text', 'result']
    assert events[0]['session_id'] == 'sess-1' and events[0]['model'] == 'm'
    assert events[2]['input'] == {'file_path': 'a.txt'}
    result = events[-1]
    assert result['result'] == 'The file says hello.' and result['tool_calls'] == 1
    assert result['usage']['input_tokens'] == 100 and result['usage']['output_tokens'] == 7
    assert result['is_error'] is False and result['exit_code'] == 0


async def test_json_is_one_object(monkeypatch, capsys):
    code, out = await _run(monkeypatch, capsys, 'json')
    result = json.loads(out)
    assert code == 0 and result['type'] == 'result' and result['subtype'] == 'success'
    assert result['full_text'] == 'Let me look. The file says hello.'


async def test_text_prints_only_the_reply(monkeypatch, capsys):
    code, out = await _run(monkeypatch, capsys, 'text')
    assert code == 0 and out.strip() == 'Let me look. The file says hello.'


async def test_failed_turn_exits_1(monkeypatch, capsys):
    code, out = await _run(monkeypatch, capsys, 'json', session=_Session(error='Model said no'))
    result = json.loads(out)
    assert code == 1 and result['is_error'] and result['subtype'] == 'error_during_execution'
    assert result['error'] == 'Model said no'


async def test_timeout_exits_124(monkeypatch, capsys):
    import asyncio

    async def slow(session, agent, text, *, stream, channel):
        await asyncio.sleep(5)

    from cognitrix.cli import headless

    monkeypatch.setattr(headless, '_local_turn', slow)
    args = _args('json')
    args.timeout = 0.05
    code = await headless.run_prompt(_Session(), _AGENT, 'x', args)
    result = json.loads(capsys.readouterr().out)
    assert code == 124 and result['subtype'] == 'error_timeout'


# ---------------------------------------------------------------- resume

async def test_resume_checks_the_owner(db, monkeypatch):  # noqa: F811
    from cognitrix.cli import headless
    from cognitrix.sessions.base import Session

    mine = Session(agent_id='a1', user_id=None)
    await mine.save()
    theirs = Session(agent_id='a1', user_id='u-2')
    await theirs.save()
    agent = SimpleNamespace(id='a1', folders=None)

    session, same = await headless.load_resumed(str(mine.id)[:8], agent, None, explicit_agent=False)
    assert str(session.id) == str(mine.id) and same is agent
    with pytest.raises(headless.UsageError):
        await headless.load_resumed(str(theirs.id), agent, None, explicit_agent=False)
    with pytest.raises(headless.UsageError):
        await headless.load_resumed(str(mine.id), agent, 'u-1', explicit_agent=False)
    with pytest.raises(headless.UsageError):
        await headless.load_resumed('nope', agent, None, explicit_agent=False)


# ---------------------------------------------------------------- agents / keys

async def test_agent_delete_actually_deletes(db, monkeypatch, capsys):  # noqa: F811
    from cognitrix.agents import Agent
    from cognitrix.cli import handlers

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    agent = await Agent.create_agent('Temp', system_prompt='x', provider='anthropic', temperature=None)
    assert await handlers.find_agent('temp') is not None  # any case
    assert await handlers.delete_agent('Temp') is True
    assert await Agent.find_one({'id': str(agent.id)}) is None
    assert await handlers.delete_agent('Temp') is False


def test_keys_set_from_env_and_list(monkeypatch, tmp_path, capsys):
    from cognitrix.cli import handlers_keys
    from cognitrix.config import settings
    from cognitrix.providers import saved_keys

    monkeypatch.setattr(settings, 'workdir', tmp_path)
    monkeypatch.setattr(saved_keys, '_cache', None)
    monkeypatch.setenv('MY_KEY', 'sk-ant-abcdefghijklmnop')
    handlers_keys.manage_keys(Namespace(keys_action='set', provider='anthropic', from_env='MY_KEY', stdin=False))
    assert saved_keys.saved_providers()['anthropic'] == 'sk-ant-abcdefghijklmnop'
    capsys.readouterr()
    handlers_keys.manage_keys(Namespace(keys_action='list', json=True))
    assert json.loads(capsys.readouterr().out)['anthropic'] is True
    handlers_keys.manage_keys(Namespace(keys_action='unset', provider='anthropic'))
    assert 'anthropic' not in saved_keys.saved_providers()
    with pytest.raises(SystemExit) as exc:
        handlers_keys.manage_keys(Namespace(keys_action='set', provider='nope', from_env='', stdin=False))
    assert exc.value.code == 2


async def test_failed_task_run_is_an_error(monkeypatch, capsys):
    from cognitrix.cli import headless
    from cognitrix.tasks import handler

    async def failing(prompt, agent, session, llm, stream):
        return 'Multi-step task failed: planner crashed\n\n[View task run](/tasks/t1)'

    monkeypatch.setattr(handler, 'handle_multi_step_task', failing)
    args = _args('json')
    args.task = True
    code = await headless.run_prompt(_Session(), _AGENT, 'plan it', args)
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['is_error'] and result['error'] == 'Multi-step task failed: planner crashed'


def test_task_run_rejects_per_run_agent_options():
    from cognitrix.cli import headless

    assert headless.task_conflicts(Namespace(system_prompt='', allowed_tools='Read')) == ['--allowed-tools']
    assert headless.task_conflicts(Namespace()) == []


# ---------------------------------------------------------------- MCP servers

def test_mcp_config_formats(tmp_path):
    from cognitrix.cli import handlers_mcp

    claude = tmp_path / '.mcp.json'
    claude.write_text(json.dumps({'mcpServers': {
        'fs': {'command': 'npx', 'args': ['-y', 'server-fs'], 'env': {'A': '1'}},
        'docs': {'type': 'http', 'url': 'https://example.com/mcp', 'headers': {'Authorization': 'Bearer x'}},
        'old': {'type': 'sse', 'url': 'https://example.com/sse', 'disabled': True},
    }}))
    servers = {c.name: c for c in handlers_mcp.read_config_file(str(claude))}
    assert servers['fs'].transport.value == 'stdio' and servers['fs'].args == ['-y', 'server-fs']
    assert servers['docs'].transport.value == 'http' and servers['docs'].headers == {'Authorization': 'Bearer x'}
    assert servers['old'].transport.value == 'sse' and servers['old'].enabled is False
    # Cognitrix's own mcp.json (a list) and an inline JSON string work too.
    own = handlers_mcp.read_config_file(json.dumps({'servers': [
        {'name': 'x', 'transport': 'stdio', 'command': 'python', 'args': ['s.py']}]}))
    assert own[0].name == 'x' and own[0].command == 'python'
    with pytest.raises(ValueError):
        handlers_mcp.read_config_file(json.dumps({'mcpServers': {'bad': {'type': 'http'}}}))  # no url


def test_mcp_add_reads_options_before_the_command():
    from cognitrix.cli import handlers_mcp

    args = _parse(['mcp', 'add', 'fs', '-e', 'A=1', '--agent', 'Coder', '--', 'npx', '-y', 'srv', '--port', '3'])
    command = handlers_mcp.split_add_args(args)
    assert command == ['npx', '-y', 'srv', '--port', '3']
    assert args.env == ['A=1'] and args.agent == 'Coder'
    args = _parse(['mcp', 'add', 'docs', '--url', 'https://e.com/mcp', '-H', 'Authorization=Bearer x'])
    assert handlers_mcp.split_add_args(args) == [] and args.url == 'https://e.com/mcp'
    assert args.header == ['Authorization=Bearer x']


async def test_mcp_config_is_for_one_run_only(monkeypatch):
    from cognitrix.cli import handlers_mcp
    from cognitrix.mcp.server_manager import mcp_server_manager

    saved = []
    monkeypatch.setattr(mcp_server_manager, '_save_configuration', lambda: saved.append(1))
    monkeypatch.setattr(mcp_server_manager, 'servers', {})
    agent = SimpleNamespace(mcp_servers=['mine'], tools=[])
    names = await handlers_mcp.use_for_run(agent, json.dumps({'mcpServers': {'calc': {'command': 'python'}}}))
    assert names == ['calc'] and agent.mcp_servers == ['mine', 'calc']
    assert 'calc' in mcp_server_manager.servers and not saved
    assert {t.name for t in agent.tools} >= {'List Mcp Tools', 'Run Mcp Tool'}


def test_run_mcp_tool_accepts_json_string_arguments():
    import inspect

    from cognitrix.tools import mcp as mcp_tools

    fn = getattr(mcp_tools.run_mcp_tool, 'func', None) or getattr(mcp_tools.run_mcp_tool, 'function', None)
    if fn is not None:
        assert 'str' in str(inspect.signature(fn).parameters['arguments'].annotation)


# ---------------------------------------------------------------- skills, voice, old flags

def test_skill_arguments_the_body_never_uses_are_passed_on():
    from cognitrix.skills.executor import SkillExecutor

    unused = SkillExecutor._unreferenced_args('Write to $(arg file_path).', {'file_path': 'a', 'content': 'yo'})
    assert unused == {'content': 'yo'}
    assert SkillExecutor._unreferenced_args('Do $ARGUMENTS', {'content': 'yo'}) == {}


async def test_voice_falls_back_to_the_next_provider(monkeypatch):
    from cognitrix.cli import voice
    from cognitrix.providers import transcription

    monkeypatch.setattr(transcription, 'server_key', lambda p: {'deepgram': 'k1', 'groq': 'k2'}.get(p, ''))
    monkeypatch.setattr(voice, '_record_until_enter', lambda: b'\x00\x00' * voice.SAMPLE_RATE)
    tried = []

    async def fake_transcribe(audio, *, mime, provider, model, api_key, language=None):
        tried.append(provider)
        if provider == 'deepgram':
            raise transcription.TranscriptionError('out of credit')
        assert audio[:4] == b'RIFF'
        return ' hello there '

    monkeypatch.setattr(transcription, 'transcribe', fake_transcribe)
    assert await voice.listen() == 'hello there'
    assert tried == ['deepgram', 'groq']
    monkeypatch.setattr(transcription, 'server_key', lambda p: '')
    with pytest.raises(voice.VoiceUnavailable):
        await voice.listen()


def test_old_flags_still_parse():
    a = _parse(['--name', 'Coder', '--prompt-template', 'Be terse.', '--audio'])
    assert a.legacy_agent_name == 'Coder' and a.prompt_template == 'Be terse.' and a.audio
    assert _parse(['--mcp-config', 'a.json', '--mcp-config', 'b.json']).mcp_config == ['a.json', 'b.json']
