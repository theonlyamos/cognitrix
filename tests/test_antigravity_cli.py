"""Google's Antigravity CLI (agy) as an agent harness and a Coding Agent CLI.

The events below follow https://antigravity.google/docs/cli/headless/
(--output-format stream-json: {"event": <type>, <type>: {...}} for init,
step_update and result).
"""
import asyncio
import json
import os
import stat
import sys
import textwrap

import pytest

from cognitrix.common import coding_agents as ca
from cognitrix.common.folders import WRITE, Folder
from cognitrix.common.process_security import HostProcessMode
from cognitrix.providers import harness as h


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'workdir', tmp_path)
    return tmp_path


def _cmd(**kw):
    args = dict(mode='edit', model='', effort='', resume=None, new_session=None, cwd='/w')
    args.update(kw)
    return h.build_command('antigravity-cli', 'agy', **args)


def test_listed_as_a_harness():
    assert h.is_harness('antigravity-cli')
    assert h.HARNESSES['antigravity-cli'].program == 'agy'
    assert 'antigravity-cli' not in h.NO_TOOL_HARNESSES
    assert 'antigravity-cli' in h.ASK_HARNESSES


def test_command_lines_per_mode():
    argv, env = _cmd()
    # The prompt goes on stdin as a stream-json user event, never -p.
    assert argv[:5] == ['agy', '--input-format', 'stream-json', '--output-format', 'stream-json']
    assert '-p' not in argv and '--print' not in argv
    assert argv[argv.index('--mode') + 1] == 'accept-edits'
    assert env == {}
    read_only, _ = _cmd(mode='read_only')
    assert read_only[read_only.index('--mode') + 1] == 'plan'
    full, _ = _cmd(mode='full')
    assert '--dangerously-skip-permissions' in full and '--mode' not in full
    # Nobody to ask: 'ask' falls back to edit.
    asked, _ = _cmd(mode='ask', tools_url='http://127.0.0.1:1/mcp')
    assert asked[asked.index('--mode') + 1] == 'accept-edits'


def test_model_effort_resume_and_apps_folder():
    argv, _ = _cmd(model='gemini-pro-orchestrator', effort='high', resume='conv-1', extra_dirs=['/data/apps'])
    assert argv[argv.index('--model') + 1] == 'gemini-pro-orchestrator'
    assert argv[argv.index('--effort') + 1] == 'high'
    assert argv[argv.index('--conversation') + 1] == 'conv-1'
    assert argv[argv.index('--add-dir') + 1] == '/data/apps'
    plain, _ = _cmd(effort='minimal')
    assert '--effort' not in plain and '--conversation' not in plain and '--add-dir' not in plain
    # A slug that names its own effort doesn't also get --effort.
    slug, _ = _cmd(model='gemini-3.8-flash-medium', effort='high')
    assert '--effort' not in slug


def test_message_goes_on_stdin_as_a_user_event():
    line = h.stdin_message('antigravity-cli', 'Fix "this" & that')
    assert line.endswith('\n')
    assert json.loads(line) == {'event': 'user', 'message': {'content': 'Fix "this" & that'}}
    assert h.stdin_message('claude-code', 'hi') == 'hi'


def _ev(kind, **body):
    return {'event': kind, kind: body}


def _events(*lines):
    out = []
    for line in lines:
        out += h.parse_line('antigravity-cli', json.dumps(line))
    return out


def test_parse_stream_json():
    ev = _events(
        {'event': 'init', 'conversation_id': 'c1',
         'init': {'cwd': '/w', 'tools': ['run_command'], 'permission_mode': 'request-review'}},
        _ev('step_update', conversation_id='c1', step_index=0, state='DONE', step_type='user_input'),
        _ev('step_update', conversation_id='c1', step_index=1, state='ACTIVE', step_type='tool',
            tool_name='run_command'),
        _ev('step_update', conversation_id='c1', step_index=1, state='DONE', step_type='tool', tool_name='run_command',
            tool_info={'name': 'run_command', 'parameters': {'CommandLine': 'ls'}, 'output': 'a.py\r\n'}),
        _ev('step_update', conversation_id='c1', step_index=2, state='ACTIVE', step_type='agent_response',
            text_delta='Fo'),
        _ev('step_update', conversation_id='c1', step_index=2, state='DONE', step_type='agent_response',
            text_delta='ur.\n'),
        _ev('step_update', conversation_id='c1', step_index=3, state='DONE', step_type='agent_response'),
        _ev('step_update', conversation_id='c1', step_index=4, state='DONE', step_type='checkpoint'),
        # The documented example: total = input + output, so output already counts thinking.
        _ev('result', conversation_id='c1', status='SUCCESS', response='Four.\n', num_turns=1,
            usage={'input_tokens': 10415, 'output_tokens': 657, 'thinking_tokens': 616,
                   'cache_read_tokens': 8113, 'total_tokens': 11072}),
        {'type': 'result', 'status': 'SUCCESS', 'response': 'old shape is ignored'},
    )
    assert ev[0].activity == '› run_command: {"CommandLine": "ls"}\n'
    assert [e.text for e in ev[1:4]] == ['Fo', 'ur.\n', '\n\n']
    assert ev[4].session == 'c1'
    assert ev[4].usage == {'prompt_tokens': 10415, 'completion_tokens': 657, 'cached_tokens': 8113}
    assert ev[5].final_text == 'Four.\n'
    assert len(ev) == 6


def test_parse_failures():
    failed_tool = _events(_ev('step_update', state='DONE', step_type='tool',
                              tool_info={'name': 'write_to_file', 'parameters': {'path': 'a.py'},
                                         'error': {'type': 'denied', 'message': 'needs approval'}}))
    assert failed_tool[0].activity == '› write_to_file: a.py (failed: needs approval)\n'
    err = _events(_ev('result', conversation_id='', status='ERROR', response='',
                      error='invalid model selection (--model "x" --effort ""): model x is not recognized'))
    assert err[-1].error.startswith('invalid model selection')
    canceled = _events(_ev('result', status='CANCELED'))
    assert 'CANCELED' in canceled[-1].error


# --- a whole turn through a fake agy --------------------------------------------------

FAKE_AGY = textwrap.dedent('''\
    import json, os, sys
    lines = [json.loads(line) for line in sys.stdin if line.strip()]
    prompt = lines[0]['message']['content'] if lines and lines[0].get('event') == 'user' else None
    with open(os.environ['FAKE_LOG'], 'a') as fh:
        fh.write(json.dumps({'argv': sys.argv[1:], 'stdin': prompt}) + '\\n')
    def out(kind, **body):
        print(json.dumps({'event': kind, kind: body}), flush=True)
    if os.environ.get('FAKE_FAIL'):
        out('result', conversation_id='', status='ERROR', response='', error='authentication required')
        sys.exit(1)
    print(json.dumps({'event': 'init', 'conversation_id': 'conv-9',
                      'init': {'cwd': os.getcwd(), 'tools': [], 'permission_mode': 'request-review'}}), flush=True)
    out('step_update', conversation_id='conv-9', step_index=1, state='DONE', step_type='tool',
        tool_info={'name': 'view_file', 'parameters': {'path': 'a.py'}})
    out('step_update', conversation_id='conv-9', step_index=2, state='ACTIVE', step_type='agent_response',
        text_delta='Four')
    out('step_update', conversation_id='conv-9', step_index=2, state='DONE', step_type='agent_response',
        text_delta='.\\n')
    out('result', conversation_id='conv-9', status='SUCCESS', response='Four.\\n', num_turns=1,
        usage={'input_tokens': 10, 'output_tokens': 2, 'thinking_tokens': 1, 'cache_read_tokens': 0,
               'total_tokens': 12})
''')


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    script = tmp_path / 'fake_agy.py'
    script.write_text(FAKE_AGY)
    if os.name == 'nt':
        launcher = tmp_path / 'agy.cmd'
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        launcher = tmp_path / 'agy'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('COGNITRIX_ANTIGRAVITY_CLI_PATH', str(launcher))
    monkeypatch.setenv('FAKE_LOG', str(log))
    return log


async def _run(llm, messages, folder, chat='chat-1'):
    token = h.turn.set(h.TurnInfo(session_id=chat, folders=(folder,), host_process_mode=HostProcessMode.TRUSTED_LOCAL))
    try:
        chunks, last = [], None
        async for item in h.stream(llm, messages):
            chunks.append(item.current_chunk)
            last = item
        return ''.join(chunks), last
    finally:
        h.turn.reset(token)


def _llm(**kw):
    from cognitrix.providers.base import LLM

    return LLM(provider='antigravity-cli', model='', **kw)


def test_turn_streams_then_resumes_the_conversation(workdir, fake_agy, tmp_path):
    folder = Folder(str(tmp_path), WRITE, 'work', True)
    first = [{'role': 'system', 'content': 'Be brief.'}, {'role': 'user', 'content': 'What is 2+2?'}]
    shown, last = asyncio.run(_run(_llm(), first, folder))
    assert shown == '<think>› view_file: a.py\n\n</think>\nFour.\n'
    assert last.llm_response == 'Four.'
    assert last.usage == {'prompt_tokens': 10, 'completion_tokens': 2, 'cached_tokens': 0}

    second = first + [{'role': 'assistant', 'content': 'Four.'}, {'role': 'user', 'content': 'And 3+3?'}]
    asyncio.run(_run(_llm(), second, folder))
    calls = [json.loads(line) for line in fake_agy.read_text().splitlines()]
    assert '<instructions>\nBe brief.\n</instructions>' in calls[0]['stdin']
    assert '--conversation' not in calls[0]['argv']
    assert calls[1]['stdin'] == 'And 3+3?'
    assert calls[1]['argv'][calls[1]['argv'].index('--conversation') + 1] == 'conv-9'


def test_sign_in_failure_says_how_to_sign_in(workdir, fake_agy, tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_FAIL', '1')
    _, last = asyncio.run(_run(_llm(), [{'role': 'user', 'content': 'Hi'}], Folder(str(tmp_path), WRITE, 'w', True)))
    assert last.error.startswith('Antigravity CLI failed: authentication required')
    assert 'run `agy` once and sign in' in last.error


# --- Coding Agent tool -----------------------------------------------------------------

def test_coding_agent_cli():
    cli = ca.CLIS['antigravity']
    assert cli.program == 'agy'
    assert cli.build('read_only', None) == ['--output-format', 'text', '--mode', 'plan']
    assert cli.build('edit', 'gemini-3.1-pro-low') == ['--output-format', 'text', '--mode', 'accept-edits',
                                                       '--model', 'gemini-3.1-pro-low']
    assert cli.build('full', None) == ['--output-format', 'text', '--dangerously-skip-permissions']
    for name in ('agy', 'Antigravity', 'antigravity cli', 'antigravity-cli'):
        assert ca.normalise_agent(name) == 'antigravity'
    assert ca.PREFERENCE[-1] == 'antigravity'
    assert ca.normalize_agent_limits({'clis': ['agy', 'claude']}) == {'clis': ['antigravity', 'claude']}


def test_tools_reach_agy_through_a_private_workspace_folder(tmp_path):
    folder = tmp_path / (h.AGY_TOOLS_DIR_PREFIX + 'abc')
    argv, env = _cmd(tools_url='http://127.0.0.1:5/mcp/tok', settings_file=str(folder), extra_dirs=['/data/apps'])
    config = json.loads((folder / '.agents' / 'mcp_config.json').read_text())
    assert config == {'mcpServers': {'cognitrix': {'serverUrl': 'http://127.0.0.1:5/mcp/tok'}}}
    dirs = [argv[i + 1] for i, a in enumerate(argv) if a == '--add-dir']
    assert dirs == ['/data/apps', str(folder)]
    assert env == {}
    # Nothing is written into the working folder itself.
    assert not (tmp_path / '.agents').exists()
