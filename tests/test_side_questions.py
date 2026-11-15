"""Side questions: context building and the one-shot answer (no DB)."""
import asyncio
from types import SimpleNamespace

from cognitrix.sessions import side_questions as sq


CHAT = [
    {'role': 'user', 'type': 'text', 'content': 'Fix the login bug'},
    {'role': 'assistant', 'type': 'tool_calls', 'content': 'Looking at auth.py',
     'tool_calls': [{'name': 'Read'}, {'name': 'Grep'}]},
    {'role': 'tool', 'content': 'def login(): ...'},
    {'role': 'assistant', 'type': 'turn_timing', 'content': 'ignored'},
    {'role': 'assistant', 'type': 'text', 'content': 'Found it in auth.py line 40.'},
]

EVENTS = [
    {'type': 'generate', 'content': 'Now running the tests'},
    {'type': 'tool', 'status': 'started', 'tool_name': 'Bash', 'params': {'command': 'pytest'}},
    {'type': 'tool', 'status': 'completed', 'tool_name': 'Bash', 'result': '3 passed'},
    {'type': 'approval_request', 'tool_name': 'Bash'},
]


def test_history_text_names_tools_and_skips_timing():
    text = sq.history_text(CHAT)
    assert 'person: Fix the login bug' in text
    assert 'agent used tools: Read, Grep' in text
    assert 'tool result: def login(): ...' in text
    assert 'ignored' not in text
    assert text.endswith('agent: Found it in auth.py line 40.')


def test_run_text_describes_progress():
    text = sq.run_text(EVENTS)
    assert 'agent (writing): Now running the tests' in text
    assert 'started tool Bash with {"command": "pytest"}' in text
    assert 'tool Bash completed: 3 passed' in text
    assert 'waiting for the person to approve Bash' in text


def test_prompt_has_no_tools_and_mentions_progress_only_while_running():
    idle = sq.build_prompt('what file?', CHAT, None)
    assert idle[0]['role'] == 'system'
    assert 'working on the latest message' not in idle[1]['content']
    assert idle[-1] == {'role': 'user', 'content': 'what file?'}
    running = sq.build_prompt('what file?', CHAT, EVENTS)
    assert 'working on the latest message right now' in running[1]['content']


def test_prompt_keeps_recent_side_questions_as_turns():
    earlier = [sq.SideQuestion(session_id='s', user_id='u', question=f'q{i}', answer=f'a{i}') for i in range(8)]
    prompt = sq.build_prompt('next', [], None, earlier)
    asked = [m['content'] for m in prompt if m['role'] == 'user'][1:]
    assert asked == ['q3', 'q4', 'q5', 'q6', 'q7', 'next']


def test_long_history_keeps_the_newest_part():
    chat = [{'role': 'user', 'content': f'message {i} ' + 'x' * 2000} for i in range(40)]
    prompt = sq.build_prompt('q', chat, None)
    body = prompt[1]['content']
    assert 'message 39' in body and 'message 0 ' not in body


class FakeLLM:
    provider = 'fake'
    model = 'm1'

    def __init__(self, reply='It is in auth.py.', error=None):
        self.reply, self.error, self.prompts = reply, error, []

    async def __call__(self, prompt, stream=False, tools=None, **kw):
        self.prompts.append((prompt, tools))
        return SimpleNamespace(llm_response=self.reply, error=self.error)


def _ask(monkeypatch, llm, events=None):
    async def no_earlier(*a, **k):
        return []

    monkeypatch.setattr(sq, 'list_for', no_earlier)
    monkeypatch.setattr(sq, '_load_llm', lambda agent: llm)
    session = SimpleNamespace(id='s1', chat=CHAT)
    return asyncio.run(sq.ask(session=session, agent=object(), user_id='u1', question=' where? ',
                              run_events=events, save=False))


def test_ask_answers_without_tools(monkeypatch):
    llm = FakeLLM()
    row = _ask(monkeypatch, llm, EVENTS)
    assert row.answer == 'It is in auth.py.' and not row.error
    assert row.question == 'where?' and row.during_run is True
    assert row.model == 'fake/m1'
    assert llm.prompts[0][1] is None


def test_ask_reports_model_errors(monkeypatch):
    row = _ask(monkeypatch, FakeLLM(reply='', error='quota exceeded'))
    assert row.error == 'quota exceeded' and row.answer == ''
    row = _ask(monkeypatch, None)
    assert row.error == 'No model is available to answer.'


def test_harness_agents_fall_back_to_default_provider(monkeypatch):
    from cognitrix.providers import base

    loaded = []
    monkeypatch.setattr(base.LLM, 'load_llm', staticmethod(lambda p: loaded.append(p) or 'default-llm'))
    agent = SimpleNamespace(llm=SimpleNamespace(provider='claude-code'))
    assert sq._load_llm(agent) == 'default-llm'
    normal = SimpleNamespace(provider='anthropic')
    assert sq._load_llm(SimpleNamespace(llm=normal)) is normal
