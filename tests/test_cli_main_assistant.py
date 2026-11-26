"""The CLI uses the person's main assistant, and runs it like the app does.

- With no --agent, the CLI (and `cognitrix acp`) picks the agent the app marks
  as main, not an agent that happens to be called "Assistant".
- --agent NAME still picks by name; --provider/--model change only that run.
- Turns for an account run through the app's chat path (interface 'web',
  the person as owner): same per-turn tools, approvals and saved chat.
- The sandbox CLI gets the main assistant from the engine.
"""
import asyncio
import base64
import json
import sys

from tests.test_onboarding import _ctx, _user, db  # noqa: F401


async def _main_and_decoy(monkeypatch):
    from cognitrix.agents import Agent
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
    from cognitrix.api.routes import onboarding

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    user = await _user()
    result = await onboarding.create_default_assistant(
        onboarding.AssistantSetup(name='Jarvis', provider='anthropic', model='claude-sonnet-5-5'), _ctx(user))
    # An older CLI-made agent called "Assistant", owned by nobody.
    decoy = await Agent.create_agent(name='Assistant', provider='anthropic', system_prompt=ASSISTANT_SYSTEM_PROMPT)
    main = await Agent.find_one({'id': result['agent']['id']})
    return user, main, decoy


async def test_no_agent_flag_means_the_main_assistant(db, monkeypatch):  # noqa: F811
    from cognitrix.cli import main_assistant as cli_agent

    user, main, decoy = await _main_and_decoy(monkeypatch)
    resolved = await cli_agent.resolve('', '')
    assert resolved.source == 'main' and str(resolved.agent.id) == str(main.id)
    assert resolved.owner == str(user.id)
    # By email too.
    resolved = await cli_agent.resolve('', 'ada@example.com')
    assert str(resolved.agent.id) == str(main.id)
    # --agent NAME still picks by name.
    named = await cli_agent.resolve('Assistant', '')
    assert named.source == 'named' and str(named.agent.id) == str(decoy.id)


async def test_main_assistant_mark_not_the_name(db, monkeypatch):  # noqa: F811
    """Whatever it is called, the one marked main wins; renaming it changes nothing."""
    from cognitrix.cli import main_assistant as cli_agent

    _user_row, main, _decoy = await _main_and_decoy(monkeypatch)
    main.name = 'Friday'
    await main.save()
    resolved = await cli_agent.resolve('', '')
    assert str(resolved.agent.id) == str(main.id) and resolved.agent.name == 'Friday'


async def test_unknown_email_and_no_accounts(db, monkeypatch):  # noqa: F811
    import pytest

    from cognitrix.agents import Agent
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
    from cognitrix.cli import main_assistant as cli_agent

    with pytest.raises(cli_agent.NoSuchUser):
        await cli_agent.resolve('', 'nobody@example.com')
    # No accounts and no agents: the caller creates the local "Assistant".
    assert await cli_agent.resolve('', '') is None
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    local = await Agent.create_agent(name='Assistant', provider='anthropic', system_prompt=ASSISTANT_SYSTEM_PROMPT)
    resolved = await cli_agent.resolve('', '')
    assert resolved.source == 'legacy' and str(resolved.agent.id) == str(local.id)


def test_cli_flags_no_longer_override_the_agent():
    from tests.test_cli_args import _parse

    args = _parse(['-p', 'hi'])
    assert args.agent == '' and args.user == '' and args.provider == '' and args.temperature is None
    assert not args.load_tools
    assert _parse(['--agent', 'Coder', '--user', 'a@b.c']).agent == 'Coder'


async def test_terminal_turn_runs_like_an_app_chat(db, monkeypatch, tmp_path):  # noqa: F811
    from cognitrix.cli import app_turn
    from cognitrix.cli import main_assistant as cli_agent
    from cognitrix.sessions.base import Session
    from cognitrix.utils.llm_response import LLMResponse

    _user_row, main, _decoy = await _main_and_decoy(monkeypatch)
    resolved = await cli_agent.resolve('', '')
    seen = {}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        seen['tools'] = json.dumps(tools, default=str).lower()
        seen['prompt'] = prompt
        response = LLMResponse()
        response.add_chunk('Hello from Jarvis')
        if stream:
            async def gen():
                yield response
            return gen()
        return response

    monkeypatch.setattr('cognitrix.providers.base.LLMManager.generate_response', staticmethod(fake_generate))
    monkeypatch.chdir(tmp_path)
    session = await app_turn.new_chat(resolved.owner, resolved.agent)
    assert session.user_id == resolved.owner
    assert session.folders[0]['path'] == str(tmp_path) and session.folders[0]['primary']

    called = {}
    original = Session.__call__

    async def spy(self, message, agent, *args, **kwargs):
        called.update(kwargs)
        return await original(self, message, agent, *args, **kwargs)

    monkeypatch.setattr(Session, '__call__', spy)
    written = []
    await app_turn.turn(session, resolved.agent, 'hi', stream=True, output=lambda t, end='': written.append(t))

    assert called['interface'] == 'web'
    assert called['tool_context'].user_id == resolved.owner
    assert ''.join(written).strip() == 'Hello from Jarvis'
    # Per-turn tools the app gives a signed-in chat.
    assert 'show_card' in seen['tools'] or 'show card' in seen['tools']
    # The chat is saved in the person's account and found again by --continue.
    again = await app_turn.last_chat(resolved.owner, resolved.agent)
    assert again is not None and str(again.id) == str(session.id)
    assert any(m.get('content') == 'Hello from Jarvis' for m in again.chat if isinstance(m, dict))


async def test_approval_without_a_terminal_is_refused(monkeypatch):
    from cognitrix.cli import app_turn
    from cognitrix.safety import approval_gate

    answers = []
    monkeypatch.setattr(approval_gate, 'resolve_web_approval', lambda *a, **k: answers.append((a, k)) or True)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False, raising=False)
    channel = app_turn.TerminalChannel('owner-1', write=lambda *a, **k: None)
    await channel.emit({'type': 'approval_request', 'request_id': 'r1', 'tool_name': 'Bash', 'params': {}})
    assert answers == [(('r1', False, 'owner-1'), {})]


async def test_question_without_a_terminal_is_skipped_not_cancelled(monkeypatch):
    from cognitrix.cli import app_turn
    from cognitrix.questions import broker

    calls = []

    async def fake_resolve(request_id, owner, action, **kw):
        calls.append((action, kw))
        return True

    monkeypatch.setattr(broker, 'resolve_question', fake_resolve)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False, raising=False)
    channel = app_turn.TerminalChannel('owner-1', write=lambda *a, **k: None)
    await channel.emit({'type': 'question_request', 'request_id': 'q1', 'prompt': 'Which one?',
                        'questions': [{'prompt': 'Which one?', 'options': [{'id': 'a', 'label': 'A'}]}]})
    assert calls and calls[0][0] == 'answer' and calls[0][1]['text'].startswith('[No answer:')


async def test_api_key_alone_overrides_for_the_run(db, monkeypatch, tmp_path):  # noqa: F811
    from argparse import Namespace

    from cognitrix.cli import app_turn, core

    _user_row, main, _decoy = await _main_and_decoy(monkeypatch)
    ran = {}

    async def fake_turn(session, agent, text, *, stream=True, output=None):
        ran.update(agent=agent)

    async def no_config():
        return None

    monkeypatch.setattr(app_turn, 'turn', fake_turn)
    monkeypatch.setattr(core, 'run_configuration', no_config)
    monkeypatch.chdir(tmp_path)
    args = Namespace(agents=False, tasks=False, teams=False, sessions=False, agent='', user='',
                     provider='', model='', api_key='sk-own-key-123456', api_base='http://localhost:9999/v1', temperature=None,
                     max_tokens=0, load_tools=None, prompt='hi', prompt_file='', continue_session=False,
                     clear_history=False, verbose=False, task=False, stream=False, ui='cli')
    await core.start(args)
    llm = ran['agent'].llm
    assert llm.base_url == 'http://localhost:9999/v1' and llm.api_key == 'sk-own-key-123456'
    assert llm.provider == 'anthropic' and llm.model == 'claude-sonnet-5-5'


async def test_cli_overrides_do_not_change_the_saved_agent(db, monkeypatch, tmp_path):  # noqa: F811
    from argparse import Namespace

    from cognitrix.agents import Agent
    from cognitrix.cli import app_turn, core

    _user_row, main, _decoy = await _main_and_decoy(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test-openai-123456')
    ran = {}

    async def fake_turn(session, agent, text, *, stream=True, output=None):
        ran.update(agent=agent, session=session, text=text)

    async def no_config():
        return None

    monkeypatch.setattr(app_turn, 'turn', fake_turn)
    monkeypatch.setattr(core, 'run_configuration', no_config)
    monkeypatch.chdir(tmp_path)
    args = Namespace(agents=False, tasks=False, teams=False, sessions=False, agent='', user='',
                     provider='openai', model='gpt-test', api_key='', api_base='', temperature=None, max_tokens=0,
                     load_tools=None, prompt='hi', prompt_file='', continue_session=False, clear_history=False,
                     verbose=False, task=False, stream=False, ui='cli')
    await core.start(args)
    assert str(ran['agent'].id) == str(main.id)
    assert ran['agent'].llm.provider == 'openai' and ran['agent'].llm.model == 'gpt-test'
    saved = await Agent.find_one({'id': str(main.id)})
    assert saved.llm.provider == 'anthropic' and saved.llm.model == 'claude-sonnet-5-5'


# ---------------------------------------------------------------- the sandbox CLI

def test_profile_round_trip_and_size_cap():
    from cognitrix.cli import main_assistant as cli_agent

    profile = {'id': 'a1', 'name': 'Jarvis', 'description': '', 'system_prompt': 'p' * 300_000,
               'llm': {'provider': 'anthropic', 'model': 'm'}, 'tools': ['Bash']}
    encoded = cli_agent.encode_profile(profile)
    # The whole NAME=value entry fits Linux's per-string limit.
    assert len(cli_agent.PROFILE_ENV) + 1 + len(encoded) + 1 < 128 * 1024
    assert len(base64.b64decode(encoded)) <= cli_agent._PROFILE_MAX_BYTES
    # Multi-byte text too.
    wide = cli_agent.encode_profile({**profile, 'system_prompt': 'é漢' * 200_000})
    assert len(cli_agent.PROFILE_ENV) + 2 + len(wide) < 128 * 1024
    back = cli_agent.decode_profile(encoded)
    assert back['id'] == 'a1' and back['tools'] == [] and back['system_prompt'].startswith('ppp')
    assert cli_agent.decode_profile('not base64!') is None


async def test_sandbox_cli_uses_the_profile_it_was_given(db, monkeypatch):  # noqa: F811
    from cognitrix.cli import main_assistant as cli_agent

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    profile = {'id': 'main-1', 'name': 'Jarvis', 'description': 'Main', 'system_prompt': 'You are Jarvis.',
               'llm': {'provider': 'anthropic', 'model': 'claude-sonnet-5-5'}, 'tools': ['Bash']}
    monkeypatch.setenv(cli_agent.PROFILE_ENV, cli_agent.encode_profile(profile))
    resolved = await cli_agent.resolve('', '')
    assert resolved.source == 'sandbox'
    agent = resolved.agent
    assert str(agent.id) == 'main-1' and agent.system_prompt == 'You are Jarvis.'
    assert agent.llm.model == 'claude-sonnet-5-5'
    # Refreshed, not duplicated, on the next run.
    profile['system_prompt'] = 'You are Jarvis, v2.'
    monkeypatch.setenv(cli_agent.PROFILE_ENV, cli_agent.encode_profile(profile))
    again = await cli_agent.resolve('', '')
    assert str(again.agent.id) == 'main-1' and again.agent.system_prompt == 'You are Jarvis, v2.'


async def test_engine_passes_the_main_assistant_to_the_sandbox_cli(db, monkeypatch):  # noqa: F811
    from cognitrix.cli import main_assistant as cli_agent
    from cognitrix.common import sandbox_work as sw
    from cognitrix.tools.tool import calling_loop

    user, main, _decoy = await _main_and_decoy(monkeypatch)
    sent = []

    class Box:
        def call(self, method, path, body, **kw):
            sent.append(body)
            return {'ok': True}

    monkeypatch.setattr(sw, 'cognitrix_cli_env', lambda: {})
    work = sw.SandboxWork(str(user.id), 'agent-1')
    work._box = Box()
    token = calling_loop.set(asyncio.get_running_loop())
    try:
        await asyncio.to_thread(work.run, 'cognitrix -p hi')
        await asyncio.to_thread(work.run, 'ls')
    finally:
        calling_loop.reset(token)
    profile = cli_agent.decode_profile(sent[0]['env'][cli_agent.PROFILE_ENV])
    assert profile['id'] == str(main.id) and profile['name'] == 'Jarvis'
    assert 'api_key' not in json.dumps(profile)
    assert cli_agent.PROFILE_ENV not in sent[1]['env']


async def test_editor_starts_with_the_main_assistant(db, monkeypatch):  # noqa: F811
    import pytest

    pytest.importorskip('acp')
    from cognitrix.acp import server

    user, main, _decoy = await _main_and_decoy(monkeypatch)
    picked = await server.CognitrixAgent(user_id=str(user.id))._pick_agent()
    assert str(picked.id) == str(main.id)
    # --agent still wins.
    picked = await server.CognitrixAgent(agent_name='assistant', user_id=str(user.id))._pick_agent()
    assert picked.name == 'Assistant'
