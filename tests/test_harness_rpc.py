"""'Ask first' for Codex (app-server) and the ACP CLIs (Gemini CLI, Copilot CLI,
OpenCode, Antigravity CLI): their permission prompts become Cognitrix cards.

Fake CLIs speak each protocol on stdio: they ask about a command and a file
change, and say what they were told."""

import os
import stat
import sys
import textwrap

import pytest

from cognitrix.common.folders import WRITE, Folder
from cognitrix.common.process_security import HostProcessMode
from cognitrix.providers import harness as h
from cognitrix.providers import harness_rpc as rpc

FAKE_ACP = textwrap.dedent('''\
    import json, sys
    log = open(sys.argv[1], 'a')
    log.write(json.dumps(sys.argv[2:]) + '\\n')
    def send(o):
        o['jsonrpc'] = '2.0'
        sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()
    def read():
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        m = json.loads(line)
        log.write(json.dumps(m) + '\\n'); log.flush()
        return m
    def ask(rid, call):
        send({'id': rid, 'method': 'session/request_permission', 'params': {'sessionId': 'S1', 'toolCall': call,
              'options': [{'optionId': 'always', 'name': 'Always', 'kind': 'allow_always'},
                          {'optionId': 'once', 'name': 'Allow', 'kind': 'allow_once'},
                          {'optionId': 'no', 'name': 'Reject', 'kind': 'reject_once'}]}})
        while True:
            m = read()
            if m.get('id') == rid and 'method' not in m:
                return m['result']['outcome']
    while True:
        m = read()
        method, rid, p = m.get('method'), m.get('id'), m.get('params') or {}
        if method == 'initialize':
            send({'id': rid, 'result': {'protocolVersion': 1, 'agentCapabilities': {
                'loadSession': True, 'mcpCapabilities': {'http': True}}}})
        elif method == 'session/new':
            send({'id': rid, 'result': {'sessionId': 'S1'}})
        elif method == 'session/load':
            send({'method': 'session/update', 'params': {'sessionId': 'S1', 'update': {
                'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'OLD HISTORY'}}}})
            send({'id': rid, 'result': {}})
        elif method == 'session/prompt':
            said = p['prompt'][0]['text']
            send({'method': 'session/update', 'params': {'sessionId': 'S1', 'update': {
                'sessionUpdate': 'agent_thought_chunk', 'content': {'type': 'text', 'text': 'thinking'}}}})
            a = ask(900, {'toolCallId': 'c1', 'title': 'make test', 'kind': 'execute',
                          'rawInput': {'command': 'make test'}})
            b = ask(901, {'toolCallId': 'c2', 'title': 'Edit notes.md', 'kind': 'edit',
                          'locations': [{'path': p.get('cwd_hint') or 'notes.md'}], 'rawInput': {'file_path': 'x/notes.md'}})
            c = ask(902, {'toolCallId': 'c3', 'title': 'cognitrix__Lookup', 'kind': 'other', 'rawInput': {'q': 1}})
            text = f"cmd={a.get('optionId')} edit={b.get('optionId')} own={c.get('optionId')} heard={len(said)}"
            send({'method': 'session/update', 'params': {'sessionId': 'S1', 'update': {
                'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}})
            send({'id': rid, 'result': {'stopReason': 'end_turn', 'usage': {'inputTokens': 7, 'outputTokens': 3}}})
''')


FAKE_CODEX = textwrap.dedent('''\
    import json, sys
    log = open(sys.argv[1], 'a')
    log.write(json.dumps(sys.argv[2:]) + '\\n')
    def send(o):
        assert 'jsonrpc' not in o
        sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()
    def read():
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        m = json.loads(line)
        log.write(json.dumps(m) + '\\n'); log.flush()
        return m
    def ask(rid, method, params):
        send({'id': rid, 'method': method, 'params': params})
        while True:
            m = read()
            if m.get('id') == rid and 'method' not in m:
                return m['result']
    while True:
        m = read()
        method, rid, p = m.get('method'), m.get('id'), m.get('params') or {}
        if 'jsonrpc' in m:
            raise SystemExit('jsonrpc field sent')
        if method == 'initialize':
            send({'id': rid, 'result': {'userAgent': 'codex/1'}})
        elif method == 'thread/start':
            send({'id': rid, 'result': {'thread': {'id': 'T1'}}})
        elif method == 'thread/resume':
            send({'id': rid, 'result': {'thread': {'id': p['threadId']}}})
        elif method == 'turn/start':
            send({'id': rid, 'result': {'turn': {'id': 'U1', 'status': 'inProgress', 'items': []}}})
            base = {'threadId': 'T1', 'turnId': 'U1'}
            send({'method': 'item/started', 'params': {**base, 'item': {'id': 'i1', 'type': 'commandExecution', 'command': 'npm test'}}})
            a = ask(500, 'item/commandExecution/requestApproval', {**base, 'itemId': 'i1', 'command': 'npm test'})
            send({'method': 'item/started', 'params': {**base, 'item': {'id': 'i2', 'type': 'fileChange',
                  'changes': [{'path': sys.argv[2], 'kind': 'update', 'diff': ''}], 'status': 'inProgress'}}})
            b = ask(501, 'item/fileChange/requestApproval', {**base, 'itemId': 'i2'})
            c = ask(502, 'item/permissions/requestApproval', {**base, 'itemId': 'i3', 'cwd': '/', 'permissions': {}})
            send({'method': 'item/completed', 'params': {**base, 'item': {'id': 'r', 'type': 'reasoning', 'summary': ['**Plan**']}}})
            send({'method': 'item/completed', 'params': {**base, 'item': {'id': 'm', 'type': 'agentMessage',
                  'text': f"cmd={a['decision']} edit={b['decision']} perms={json.dumps(c['permissions'])}"}}})
            send({'method': 'thread/tokenUsage/updated', 'params': {**base, 'tokenUsage': {
                  'last': {'inputTokens': 11, 'outputTokens': 4, 'cachedInputTokens': 2}}}})
            send({'method': 'turn/completed', 'params': {'threadId': 'T1', 'turn': {'id': 'U1', 'status': 'completed'}}})
''')


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'workdir', tmp_path)
    return tmp_path


def _launcher(tmp_path, key: str, source: str, *extra: str):
    script = tmp_path / f'{key}.py'
    script.write_text(source)
    log = tmp_path / f'{key}.log'
    args = ' '.join(f'"{a}"' for a in (str(log), *extra))
    if os.name == 'nt':
        launcher = tmp_path / f'{key}.cmd'
        launcher.write_text(f'@"{sys.executable}" "{script}" {args} %*\n')
    else:
        launcher = tmp_path / key
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" {args} "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher, log


async def _turn(tmp_path, key: str, answers: dict[str, bool], messages=('hello',), session_id='chat-1'):
    from cognitrix.providers.base import LLM

    asked: list[tuple[str, dict]] = []

    async def approve(name, params):
        asked.append((name, params))
        return answers.get(name, True), ''

    async def call_tool(name, args):
        return 'ok'

    llm = LLM(provider=key, model='', harness_mode='ask')
    h.turn.set(h.TurnInfo(session_id=session_id, folders=(Folder(str(tmp_path), WRITE, 'work', True),),
                          host_process_mode=HostProcessMode.TRUSTED_LOCAL, tools=(
                              {'type': 'function', 'function': {'name': 'Lookup', 'parameters': {
                                  'type': 'object', 'properties': {}}}},),
                          call_tool=call_tool, approve=approve))
    last = None
    async for item in h.stream(llm, [{'role': 'user', 'content': m} for m in messages]):
        last = item
    return last, asked


@pytest.mark.parametrize('key', sorted(rpc.ACP_KEYS))
@pytest.mark.asyncio
async def test_acp_cli_asks_through_cards(workdir, tmp_path, monkeypatch, key):
    launcher, log = _launcher(tmp_path, key, FAKE_ACP)
    monkeypatch.setenv(f"COGNITRIX_{key.upper().replace('-', '_')}_PATH", str(launcher))

    last, asked = await _turn(tmp_path, key, {'Bash': False})
    assert last.error is None, last.error
    reply = last.llm_response
    # The command was refused, the edit allowed once (never "always"), the
    # lent Cognitrix tool allowed without a card.
    assert 'cmd=no edit=once own=once' in reply
    assert asked == [('Bash', {'command': 'make test'}), ('Write', {'file_path': 'x/notes.md'})]
    assert last.usage['completion_tokens'] == 3
    argv = log.read_text().splitlines()[0]
    assert {'gemini-cli': '--acp', 'copilot-cli': '--acp', 'opencode': 'acp',
            'antigravity-cli': 'acp'}[key] in argv

    # Next turn reopens the same session; its replayed history isn't shown again.
    last, _ = await _turn(tmp_path, key, {}, messages=('hello', 'again'))
    assert 'OLD HISTORY' not in last.llm_response
    sent = [line for line in log.read_text().splitlines() if '"session/load"' in line]
    assert sent, 'the second turn should load the session'


@pytest.mark.asyncio
async def test_codex_asks_through_cards(workdir, tmp_path, monkeypatch):
    target = str(tmp_path / 'a.py')
    launcher, log = _launcher(tmp_path, 'codex', FAKE_CODEX, target)
    monkeypatch.setenv('COGNITRIX_CODEX_PATH', str(launcher))

    last, asked = await _turn(tmp_path, 'codex', {'Bash': True, 'Write': False})
    assert last.error is None, last.error
    assert 'cmd=accept edit=decline perms={}' in last.llm_response
    assert asked == [('Bash', {'command': 'npm test'}), ('Write', {'file_path': target})]
    assert last.usage == {'prompt_tokens': 11, 'completion_tokens': 4, 'cached_tokens': 2}
    lines = log.read_text().splitlines()
    assert 'app-server' in lines[0]
    start = next(line for line in lines if '"thread/start"' in line)
    assert '"approvalPolicy": "on-request"' in start and '"approvalsReviewer": "user"' in start

    last, _ = await _turn(tmp_path, 'codex', {}, messages=('hello', 'again'))
    assert any('"thread/resume"' in line for line in log.read_text().splitlines())


def test_permission_mapping():
    name, shown, own = rpc.acp_permission_call({'toolCall': {'kind': 'execute', 'title': 'ls',
                                                             'rawInput': {'command': ['ls', '-la']}}})
    assert (name, shown, own) == ('Bash', {'command': 'ls -la'}, False)
    # A command never passes as a lent tool, whatever its title says.
    name, _shown, own = rpc.acp_permission_call(
        {'toolCall': {'kind': 'execute', 'title': 'cognitrix rm -rf x', 'rawInput': {'command': 'rm -rf x'}}},
        frozenset({'Lookup'}))
    assert name == 'Bash' and not own
    assert rpc.acp_answer([{'optionId': 'a', 'kind': 'allow_always'}], True) == {
        'outcome': {'outcome': 'selected', 'optionId': 'a'}}
    assert rpc.acp_answer([{'optionId': 'a', 'kind': 'allow_always'}], False) == {'outcome': {'outcome': 'cancelled'}}


def test_every_cli_can_ask_first():
    assert set(h.HARNESSES) <= h.ASK_HARNESSES


@pytest.mark.asyncio
async def test_a_cli_that_dies_mid_turn_ends_the_turn(workdir, tmp_path, monkeypatch):
    dying = FAKE_CODEX.replace("    elif method == 'turn/start':\n", "    elif method == 'turn/start':\n        sys.exit(3)\n")
    assert dying != FAKE_CODEX
    launcher, _log = _launcher(tmp_path, 'codex', dying, 'x')
    monkeypatch.setenv('COGNITRIX_CODEX_PATH', str(launcher))
    last, asked = await _turn(tmp_path, 'codex', {})
    assert last.error and 'Codex failed' in last.error
    assert asked == []
