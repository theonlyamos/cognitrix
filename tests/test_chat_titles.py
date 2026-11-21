"""Chat titles the model writes (no DB)."""
from types import SimpleNamespace

from cognitrix.sessions import titles


CHAT = [
    {'role': 'user', 'type': 'text', 'content': 'Give me a 5-step plan to launch a weekly newsletter'},
    {'role': 'assistant', 'type': 'tool_calls', 'content': '', 'tool_calls': [{'name': 'Search'}]},
    {'role': 'tool', 'content': 'results'},
    {'role': 'assistant', 'type': 'turn_timing', 'content': 'ignored'},
    {'role': 'assistant', 'type': 'text', 'content': 'Here is the plan.'},
]


def _session(**over):
    base = dict(chat=CHAT, title=None, auto_title=None, incognito=None, group_chat=None,
                task_id=None, run_id=None, public_link_id=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_first_exchange_skips_tool_messages():
    assert titles.first_exchange(CHAT) == ('Give me a 5-step plan to launch a weekly newsletter', 'Here is the plan.')
    assert titles.first_exchange(CHAT[:1]) == ('Give me a 5-step plan to launch a weekly newsletter', '')


def test_clean_makes_one_tidy_line():
    assert titles.clean('"Newsletter launch plan."\n\nHope that helps') == 'Newsletter launch plan'
    assert titles.clean('<think>hmm</think>Title: **Coffee newsletter**') == 'Coffee newsletter'
    assert titles.clean('   ') == ''
    long = titles.clean('word ' * 40)
    assert len(long) <= titles.MAX_TITLE_CHARS + 1 and long.endswith('…')


def test_only_unnamed_ordinary_chats_with_a_reply_get_a_title():
    assert titles.wanted(_session())
    assert not titles.wanted(_session(title='Mine'))
    assert not titles.wanted(_session(auto_title='Already'))
    assert not titles.wanted(_session(incognito=True))
    assert not titles.wanted(_session(task_id='t1'))
    assert not titles.wanted(_session(group_chat=True))
    assert not titles.wanted(_session(chat=CHAT[:1]))
