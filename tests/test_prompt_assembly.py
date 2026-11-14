"""What goes around an agent's own prompt: the date line and the skills list."""
import re
from types import SimpleNamespace

import pytest

from cognitrix.common import clock


def test_date_line_uses_the_persons_timezone():
    token = clock.client_timezone.set('Asia/Tokyo')
    try:
        line = clock.date_line()
    finally:
        clock.client_timezone.reset(token)
    assert re.fullmatch(r"Today is \w+, \d{2} \w+ \d{4} \(the person's timezone: Asia/Tokyo, UTC\+09:00\)\.", line)


def test_date_line_ignores_a_made_up_timezone():
    assert clock.clean_timezone('Mars/Olympus') == ''
    assert clock.clean_timezone('x' * 200) == ''
    assert clock.clean_timezone('../../etc/passwd') == ''
    token = clock.client_timezone.set('Mars/Olympus')
    try:
        line = clock.date_line()
    finally:
        clock.client_timezone.reset(token)
    assert line.startswith('Today is ') and 'Mars' not in line and 'UTC' in line


def test_date_line_has_no_time_of_day():
    # A minute in the prompt would defeat the provider's prompt cache.
    assert not re.search(r'\d{1,2}:\d{2}(?!\))', clock.date_line().split('UTC')[0])


@pytest.mark.asyncio
async def test_own_skills_are_listed_before_the_shared_cache_is_warm(monkeypatch):
    """On the server the shared skills cache starts empty and can't be filled
    from inside a running loop; the person's own skills were dropped too."""
    from cognitrix.agents.base import AgentManager
    from cognitrix.skills import manager as skills_manager
    from cognitrix.skills.personal import personal_skills_ctx

    fake = SimpleNamespace(_cache={}, get_skill_summaries=lambda: [])
    monkeypatch.setattr(skills_manager, 'get_skill_manager', lambda: fake)
    token = personal_skills_ctx.set(({'name': 'weekly-report', 'description': 'Write the weekly report'},))
    try:
        text = AgentManager(SimpleNamespace())._format_skills_string()
    finally:
        personal_skills_ctx.reset(token)
    assert '/weekly-report: Write the weekly report' in text


def test_the_timezone_travels_with_a_queued_chat_message():
    """Chat turns run in the event stream's context, not the request's."""
    from cognitrix.api.routes.agents import _request_timezone

    assert _request_timezone() == ''
    token = clock.client_timezone.set('Africa/Accra')
    try:
        assert _request_timezone() == 'Africa/Accra'
    finally:
        clock.client_timezone.reset(token)
