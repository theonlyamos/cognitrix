"""The proactive main assistant.

- Interruptions: never in quiet hours or at level Off; a daily limit except for urgent alerts.
- Check-ins skip the model when nothing changed, and slow down after "not useful" answers.
- Follow Up Later comes back to the chat at the set time.
- The morning brief is published once a day, takes feedback and mutes topics.
- New mail is sorted by rules before any model sees it.
- First-run setup turns check-ins on with a starter brief.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_onboarding import _ctx, _user, db  # noqa: F401

NOON = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- interruptions

async def test_quiet_hours_and_daily_limit(db):  # noqa: F811
    from cognitrix.assistant import proactivity as p

    user = await _user()
    uid = str(user.id)
    await p.save_settings(uid, timezone='UTC', quiet_start='21:00', quiet_end='08:00', max_pushes=2)
    assert await p.should_interrupt(uid, now=NOON)
    assert await p.should_interrupt(uid, now=NOON)
    assert not await p.should_interrupt(uid, now=NOON)            # limit reached
    assert await p.should_interrupt(uid, p.URGENT, now=NOON)      # urgent skips the limit
    night = NOON.replace(hour=23)
    assert not await p.should_interrupt(uid, p.URGENT, now=night)  # never in quiet hours
    row = await p.today_row(uid, NOON)
    assert row.pushes == 3 and row.held == 2

    await p.save_settings(uid, level='off')
    assert not await p.should_interrupt(uid, p.URGENT, now=NOON)
    with pytest.raises(ValueError):
        await p.save_settings(uid, quiet_start='25:99')
    with pytest.raises(ValueError):
        await p.save_settings(uid, level='loud')


async def test_quiet_hours_follow_the_persons_time_zone(db):  # noqa: F811
    from cognitrix.assistant import proactivity as p

    user = await _user()
    settings = await p.save_settings(str(user.id), timezone='Asia/Tokyo')
    # 12:00 UTC is 21:00 in Tokyo.
    assert p.in_quiet_hours(settings, NOON)
    assert not p.in_quiet_hours(settings, NOON.replace(hour=3))


async def test_quiet_notifications_are_kept_but_not_pushed(db, monkeypatch):  # noqa: F811
    from cognitrix.assistant import proactivity as p
    from cognitrix.tasks import automation

    user = await _user()
    uid = str(user.id)
    await p.save_settings(uid, max_pushes=0, quiet_start='00:00', quiet_end='00:00')
    pushed = []

    from cognitrix.common import webpush

    monkeypatch.setattr(webpush, 'send_soon', lambda *a, **k: pushed.append(a[1]))
    row = await automation.create_notification(uid, 'Heads up', 'Something small', proactive=True)
    assert row.quiet is True
    urgent = await automation.create_notification(uid, 'Deadline', 'Due in an hour', proactive=True, urgent=True)
    assert urgent.quiet is False
    plain = await automation.create_notification(uid, 'You asked', 'Done', proactive=False)
    assert plain.quiet is False
    assert pushed == ['Deadline', 'You asked']


async def test_feedback_backs_off_and_mutes(db):  # noqa: F811
    from cognitrix.assistant import proactivity as p

    user = await _user()
    uid = str(user.id)
    for i in range(6):
        settings = await p.record_feedback(uid, 'dismissed', f'offer {i}')
    assert settings.backoff == 2
    settings = await p.record_feedback(uid, 'useful', 'the brief')
    assert settings.backoff == 1
    settings = await p.record_feedback(uid, 'not_useful', 'Newsletter from Acme', mute='Newsletter from Acme')
    note = p.feedback_note(settings)
    assert 'Never bring these up' in note and 'Newsletter from Acme' in note


# ---------------------------------------------------------------- check-in gate

async def test_checkin_skips_when_nothing_changed(db, monkeypatch):  # noqa: F811
    from cognitrix.assistant import checkins
    from cognitrix.assistant import proactivity as p
    from cognitrix.tasks import standing

    user = await _user()
    uid = str(user.id)
    agent = SimpleNamespace(id='agent-1', automation={'standing': {'every_minutes': 60}})
    task = SimpleNamespace(id='task-1', owner_id=uid)
    marker = {'value': 'a'}

    async def fake_agent(_task):
        return agent

    async def fake_marker(owner, agent_, settings, now=None):
        return marker['value']

    async def fake_save(agent_, state):
        agent_.automation = {'standing_state': state}

    monkeypatch.setattr(standing, 'standing_agent', fake_agent)
    monkeypatch.setattr(checkins, 'change_marker', fake_marker)
    monkeypatch.setattr(checkins, '_save_state', fake_save)

    assert await checkins.gate_checkin(task, NOON) == (True, '')
    allowed, reason = await checkins.gate_checkin(task, NOON + timedelta(hours=1))
    assert not allowed and 'nothing changed' in reason
    marker['value'] = 'b'
    assert (await checkins.gate_checkin(task, NOON + timedelta(hours=2)))[0]

    await p.save_settings(uid, level='brief')
    allowed, reason = await checkins.gate_checkin(task, NOON + timedelta(hours=3))
    assert not allowed and 'brief' in reason

    await p.save_settings(uid, level='balanced', daily_tokens=1000)
    await p.bump(uid, 'tokens', 5000, now=NOON + timedelta(hours=4))
    marker['value'] = 'c'
    allowed, reason = await checkins.gate_checkin(task, NOON + timedelta(hours=4))
    assert not allowed and 'token limit' in reason
    day = await p.today_row(uid, NOON)
    assert day.runs == 2 and day.skipped == 1


# ---------------------------------------------------------------- follow-ups

async def test_follow_ups_are_delivered_when_due(db, monkeypatch):  # noqa: F811
    from cognitrix.agents import access
    from cognitrix.assistant import followups
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    user = await _user()
    uid = str(user.id)
    start = datetime(2026, 11, 16, 12, 0)
    with pytest.raises(ValueError):
        await followups.schedule(uid, 'a1', 's1', minutes=1, what='too soon', now=start)
    row = await followups.schedule(uid, 'a1', 's1', minutes=30, what='Check whether Sam replied', now=start)
    assert [r.what for r in await followups.pending_for(uid, 's1')] == ['Check whether Sam replied']

    delivered = []

    async def fake_agent(user_id, agent_id=None):
        return SimpleNamespace(id='a1', name='Jarvis')

    async def fake_session(session_id):
        return SimpleNamespace(id=session_id, user_id=uid, agent_id='a1')

    async def fake_deliver(user_id, agent_id, session_id, text, **kwargs):
        delivered.append(text)
        return 'started'

    monkeypatch.setattr(access, 'find_visible_agent', fake_agent)
    monkeypatch.setattr(Session, 'get', staticmethod(fake_session))
    monkeypatch.setattr(agent_chats, 'deliver_report', fake_deliver)

    assert await followups.deliver_due(start + timedelta(minutes=10)) == 0
    assert await followups.deliver_due(start + timedelta(minutes=31)) == 1
    assert 'Check whether Sam replied' in delivered[0] and followups.NOTHING_NEW in delivered[0]
    done = await followups.FollowUp.get(row.id)
    assert done.status == followups.DONE
    assert followups.quiet_reply('nothing new, Sam has not replied')


async def test_busy_follow_up_retries_then_falls_back(db, monkeypatch):  # noqa: F811
    from cognitrix.agents import access
    from cognitrix.assistant import followups
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session
    from cognitrix.tasks import automation

    user = await _user()
    uid = str(user.id)
    start = datetime(2026, 11, 16, 12, 0)
    row = await followups.schedule(uid, 'a1', 's1', minutes=5, what='Check the deploy', now=start)

    async def fake_agent(user_id, agent_id=None):
        return SimpleNamespace(id='a1', name='Jarvis')

    async def fake_session(session_id):
        return SimpleNamespace(id=session_id, user_id=uid, agent_id='a1')

    async def busy(*args, **kwargs):
        return 'busy'

    monkeypatch.setattr(access, 'find_visible_agent', fake_agent)
    monkeypatch.setattr(Session, 'get', staticmethod(fake_session))
    monkeypatch.setattr(agent_chats, 'deliver_report', busy)

    await followups.deliver_due(start + timedelta(minutes=6))
    waiting = await followups.FollowUp.get(row.id)
    assert waiting.status == followups.PENDING and waiting.next_at
    await followups.deliver_due(start + timedelta(hours=7))
    assert (await followups.FollowUp.get(row.id)).status == followups.FAILED
    notes = await automation.AgentNotification.find({'user_id': uid}) or []
    assert any('follow-up' in n.title.lower() for n in notes)


async def test_follow_up_tool_is_chat_only(db):  # noqa: F811
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    user = await _user()
    tool = ToolManager.get_by_name('follow up later')
    token = set_execution_context(ToolExecutionContext(user_id=str(user.id), task_id='t1'))
    try:
        result = await tool.run(what='Check it', minutes=30)
    finally:
        reset_execution_context(token)
    assert 'chat' in str(result.content).lower()


# ---------------------------------------------------------------- brief

async def test_brief_publish_feedback_and_mute(db):  # noqa: F811
    from cognitrix.assistant import brief
    from cognitrix.assistant import proactivity as p

    user = await _user()
    uid = str(user.id)
    agent = SimpleNamespace(id='a1', name='Jarvis')
    items = brief.clean_items([
        {'section': 'today', 'text': '10:00 call with Sam', 'why': 'on your calendar'},
        {'section': 'nonsense', 'text': 'Acme newsletter arrived'},
        {'section': 'reply', 'text': ''},
    ])
    assert [i['section'] for i in items] == ['today', 'other']
    row = await brief.publish(uid, agent, 'run-1', '2026-11-16', 'One call today.', items)
    again = await brief.publish(uid, agent, 'run-2', '2026-11-16', 'One call, updated.', items)
    assert again.id == row.id and again.summary == 'One call, updated.'
    assert (await brief.latest(uid)).run_id == 'run-2'

    row = await brief.give_feedback(again, 1, 'never')
    assert [i['feedback'] for i in brief.public(row)['items']] == [None, 'never']
    assert 'Acme newsletter arrived' in p.muted_topics(await p.get_settings(uid))
    with pytest.raises(ValueError):
        await brief.give_feedback(row, 9, 'useful')


# ---------------------------------------------------------------- mail

@pytest.mark.parametrize('mail, category', [
    ({'from': 'Google Calendar <calendar@google.com>', 'subject': 'Invitation: Sync @ 10am'}, 'invite'),
    ({'from': 'Acme <no-reply@acme.com>', 'subject': 'Your weekly digest'}, 'skip'),
    ({'from': 'Shop <orders@shop.com>', 'subject': 'Thanks', 'snippet': 'Click to unsubscribe'}, 'skip'),
    ({'from': 'Sam Boateng <sam@x.com>', 'subject': 'Lunch'}, 'reply'),
    ({'from': 'Kofi <kofi@y.com>', 'subject': 'Re: the invoice'}, 'reply'),
    ({'from': 'Ama <ama@z.com>', 'subject': 'Quick one', 'snippet': 'Can you send the slides?'}, 'reply'),
    ({'from': 'Ama <ama@z.com>', 'subject': 'FYI', 'snippet': 'Slides attached.'}, 'fyi'),
])
def test_mail_sorting_rules(mail, category):
    from cognitrix.assistant import mail as mail_mod

    assert mail_mod.sort_mail(mail, ['my colleague sam works on billing.'])[0] == category


# ---------------------------------------------------------------- setup and routes

async def test_first_run_turns_check_ins_on(db, monkeypatch):  # noqa: F811
    from cognitrix.agents import Agent
    from cognitrix.api.routes import onboarding, proactive
    from cognitrix.tasks import standing

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    user = await _user()
    result = await onboarding.create_default_assistant(
        onboarding.AssistantSetup(name='Jarvis', provider='anthropic', model='claude-sonnet-5-5'), _ctx(user))
    assert result['proactive'] is True
    agent = await Agent.find_one({'id': result['agent']['id']})
    settings = standing.settings_of(agent)
    assert settings['primary'] and settings['enabled'] and settings['every_minutes'] == 240
    assert 'Publish Brief' not in [t.name if hasattr(t, 'name') else t for t in (agent.tools or [])]

    view = await proactive.get_proactivity(_ctx(user))
    assert view['needs_setup'] is False and view['level'] == 'balanced'
    assert view['main_assistant']['name'] == 'Jarvis'
    changed = await proactive.put_proactivity(proactive.ProactivityBody(max_pushes=5, brief_time='07:30'), _ctx(user))
    assert changed['max_pushes'] == 5 and changed['brief_time'] == '07:30'
    with pytest.raises(HTTPException):
        await proactive.put_proactivity(proactive.ProactivityBody(brief_time='late'), _ctx(user))
    assert (await proactive.latest_brief(_ctx(user))) == {'brief': None}


async def test_setup_can_leave_check_ins_off(db, monkeypatch):  # noqa: F811
    from cognitrix.agents import Agent
    from cognitrix.api.routes import onboarding, proactive
    from cognitrix.tasks import standing

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-123456')
    user = await _user()
    result = await onboarding.create_default_assistant(
        onboarding.AssistantSetup(name='Jarvis', provider='anthropic', model='claude-sonnet-5-5', proactive=False),
        _ctx(user))
    assert result['proactive'] is False
    agent = await Agent.find_one({'id': result['agent']['id']})
    assert not standing.settings_of(agent)['enabled']
    assert (await proactive.get_proactivity(_ctx(user)))['needs_setup'] is True
    with pytest.raises(HTTPException) as exc:
        await proactive.brief_now(_ctx(user))
    assert exc.value.status_code == 409
