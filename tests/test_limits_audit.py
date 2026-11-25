"""Limits that used to cut agents off part-way (audit of Nov 2026)."""
import itertools
from types import SimpleNamespace

from cognitrix import app_settings
from cognitrix.agents import base as agents_base
from cognitrix.providers.base import DEFAULT_MAX_TOKENS, LARGE_MAX_TOKENS, output_token_limit
from cognitrix.sessions import agent_chats
from cognitrix.sessions.base import Session
from cognitrix.skills import executor as skills_executor


def _settings(monkeypatch, module, values):
    monkeypatch.setattr(module, 'app_setting', lambda name, default=None: values.get(name, default))


def test_background_replies_have_no_time_limit_by_default(monkeypatch):
    values: dict[str, str] = {}
    monkeypatch.setattr(app_settings, 'get', lambda name, default='': values.get(name, default))
    assert agent_chats.background_turn_timeout() is None
    values['COGNITRIX_CHAT_TURN_TIMEOUT'] = '1800'
    assert agent_chats.background_turn_timeout() == 1800.0
    values['COGNITRIX_CHAT_TURN_TIMEOUT'] = 'soon'
    assert agent_chats.background_turn_timeout() is None


def test_tool_results_keep_32k_and_follow_the_setting(monkeypatch):
    values: dict[str, str] = {}
    _settings(monkeypatch, agents_base, values)
    text = 'x' * 30_000
    assert agents_base._truncate_tool_result(text) == text
    cut = agents_base._truncate_tool_result('x' * 40_000)
    assert cut.startswith('x' * 32_000) and '[truncated 8000 chars' in cut
    values['COGNITRIX_TOOL_RESULT_CHARS'] = '0'
    assert len(agents_base._truncate_tool_result('x' * 400_000)) == 400_000
    values['COGNITRIX_TOOL_RESULT_CHARS'] = '100'
    assert agents_base._truncate_tool_result('x' * 150).startswith('x' * 100 + '\n[truncated 50')


def test_reply_length_grows_only_on_models_that_allow_it():
    def llm(model, max_tokens=DEFAULT_MAX_TOKENS):
        return SimpleNamespace(model=model, max_tokens=max_tokens)

    for model in ('claude-opus-5-5', 'anthropic/claude-sonnet-4.5', 'gpt-5.2', 'openai/gpt-6.2-sol',
                  'o3-mini', 'gemini-2.5-pro', 'google/gemini-4-neon'):
        assert output_token_limit(llm(model)) == LARGE_MAX_TOKENS, model
    for model in ('gpt-4o', 'claude-3-5-haiku-latest', 'llama-3.3-70b', 'gemini-2.0-flash', ''):
        assert output_token_limit(llm(model)) == DEFAULT_MAX_TOKENS, model
    # A limit set on the agent is used as it is.
    assert output_token_limit(llm('claude-opus-5-5', 4000)) == 4000
    assert output_token_limit(llm('gpt-4o', 16_000)) == 16_000


def test_saved_chat_keeps_every_message():
    session = Session()
    session.update_history([{'role': 'user', 'type': 'text', 'content': str(i)} for i in range(1500)])
    session.update_history({'role': 'user', 'type': 'text', 'content': 'last'})
    assert len(session.chat) == 1501
    assert session.chat[0]['content'] == '0'


def test_skills_follow_the_chat_tool_round_setting(monkeypatch):
    monkeypatch.setattr(skills_executor, '_max_tool_rounds', lambda: 0)
    assert list(itertools.islice(skills_executor._tool_rounds(), 40)) == list(range(40))
    monkeypatch.setattr(skills_executor, '_max_tool_rounds', lambda: 3)
    assert list(skills_executor._tool_rounds()) == [0, 1, 2]
