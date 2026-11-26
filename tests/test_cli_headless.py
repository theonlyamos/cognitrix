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
    from cognitrix.sessions import base as session_base

    monkeypatch.setattr(session_base, 'MAX_TOOL_ROUNDS', 0)
    agent = SimpleNamespace(system_prompt='Base.', tools=[_tool('Read'), _tool('Bash'), _tool('Web Fetch')])
    headless.apply_run_options(agent, Namespace(
        system_prompt='', append_system_prompt='Be brief.', allowed_tools='', disallowed_tools='bash, web_fetch',
        max_turns=3))
    assert [t.name for t in agent.tools] == ['Read']
    assert agent.system_prompt == 'Base.\n\nBe brief.'
    assert session_base.MAX_TOOL_ROUNDS == 3

    agent = SimpleNamespace(system_prompt='Base.', tools=[_tool('Read'), _tool('Bash')])
    headless.apply_run_options(agent, Namespace(system_prompt='New.', allowed_tools='read'))
    assert [t.name for t in agent.tools] == ['Read'] and agent.system_prompt == 'New.'


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
