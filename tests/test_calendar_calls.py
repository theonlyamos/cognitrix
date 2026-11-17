"""Calls from Google Calendar: found without a model, suggested, and joined
only after the person (or a rule they set) says so."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 11, 16, 9, 0, 0)


def _event(eid, minutes=30, *, series='', link='meet', title='Standup', organiser_self=False, **extra):
    starts = NOW + timedelta(minutes=minutes)
    event = {
        'id': eid, 'summary': title, 'status': 'confirmed',
        'start': {'dateTime': starts.strftime('%Y-%m-%dT%H:%M:%SZ')},
        'end': {'dateTime': (starts + timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%SZ')},
        'organizer': {'email': 'boss@acme.test', 'self': organiser_self},
    }
    if series:
        event['recurringEventId'] = series
    if link == 'meet':
        event['hangoutLink'] = 'https://meet.google.com/abc-defg-hij'
    elif link == 'teams':
        event['description'] = ('Join: &lt;<a href="https://teams.microsoft.com/l/meetup-join/19%3ameeting_x/0?'
                                'context=%7b%7d">Click here</a>&gt;')
    event.update(extra)
    return event


@pytest.fixture
async def cal(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.meetings import calendar_calls
    from cognitrix.models.mcp_server import MCPServer

    db_file = str(tmp_path / 'c.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (*calendar_calls.MODELS, MCPServer):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()
    alerts, joins = [], []

    async def note(user_id, title, message, **kw):
        alerts.append((user_id, title, kw.get('link')))

    async def start_live(user_key, *, url, agent_id='', title='', **kw):
        joins.append((user_key, url, agent_id, title))
        return SimpleNamespace(id=f'm{len(joins)}')

    import cognitrix.api.routes.meetings as routes
    import cognitrix.tasks.automation as automation
    monkeypatch.setattr(automation, 'create_notification', note)
    monkeypatch.setattr(routes, 'start_live', start_live)
    return SimpleNamespace(cc=calendar_calls, alerts=alerts, joins=joins, MCPServer=MCPServer)


def test_only_events_with_a_call_link_count():
    from cognitrix.meetings.calendar_calls import to_call

    call = to_call(_event('e1'))
    assert call['url'] == 'https://meet.google.com/abc-defg-hij' and call['platform'] == 'meet'
    assert call['starts_at'] == '2026-11-16 09:30:00'
    teams = to_call(_event('e2', link='teams'))
    assert teams['platform'] == 'teams' and teams['url'].startswith('https://teams.microsoft.com/l/meetup-join/')
    zoom = to_call(_event('e3', link='', location='https://acme.zoom.us/j/123456789?pwd=x, room 4'))
    assert zoom['platform'] == 'zoom' and zoom['url'].endswith('pwd=x')
    assert to_call(_event('e4', link='')) is None
    assert to_call(_event('e5', status='cancelled')) is None
    assert to_call(_event('e6', attendees=[{'self': True, 'responseStatus': 'declined'}])) is None
    all_day = _event('e7')
    all_day['start'] = {'date': '2026-11-16'}
    assert to_call(all_day) is None
    offset = _event('e8')
    offset['start'] = {'dateTime': '2026-11-16T11:30:00+02:00'}
    assert to_call(offset)['starts_at'] == '2026-11-16 09:30:00'


async def test_rules_decide_what_is_suggested(cal):
    cc = cal.cc
    await cc.save_settings('u1', never_words='1:1 with HR\nlunch', auto_join='mine')
    settings = await cc.settings_for('u1')
    await cc.CalendarCallSettings.update_one({'id': settings.id}, {'always_series': '["weekly"]',
                                                                    'never_series': '["boring"]'})
    settings = await cc.settings_for('u1')
    calls = await cc.sync('u1', settings, [
        _event('a'), _event('b', series='weekly'), _event('c', series='boring'),
        _event('d', title='Team lunch'), _event('e', organiser_self=True), _event('f', link=''),
    ], NOW)
    got = {c.event_id: (c.status, c.decided_by) for c in calls}
    assert got == {'a': ('suggested', ''), 'b': ('approved', 'series'), 'c': ('skipped', 'series'),
                   'd': ('skipped', 'rule'), 'e': ('approved', 'rule')}

    # Moved: reminded again. Gone from the calendar: cancelled.
    row = next(c for c in calls if c.event_id == 'a')
    await cc.CalendarCall.update_one({'id': row.id}, {'alerted': True})
    calls = await cc.sync('u1', settings, [_event('a', minutes=45), _event('b', series='weekly')], NOW)
    got = {c.event_id: c for c in calls}
    assert got['a'].starts_at == '2026-11-16 09:45:00' and not got['a'].alerted
    assert got['e'].status == 'cancelled'


async def test_reminds_once_and_joins_only_approved_calls(cal):
    cc = cal.cc
    settings = await cc.settings_for('u1')
    await cc.sync('u1', settings, [_event('soon', minutes=10), _event('later', minutes=120),
                                   _event('mine', minutes=1, series='s')], NOW)
    assert await cc.act(NOW) == 0
    assert [a[1] for a in cal.alerts] == ['Send an agent to "Standup"?'] * 2  # soon + mine, not later
    assert all(a[2].startswith('/meetings?call=') for a in cal.alerts)
    await cc.act(NOW)
    assert len(cal.alerts) == 2 and cal.joins == []  # suggestions never join by themselves

    mine = next(c for c in await cc.upcoming('u1', NOW) if c.event_id == 'mine')
    await cc.answer('u1', str(mine.id), 'send', now=NOW)
    assert cal.joins == [('u1', 'https://meet.google.com/abc-defg-hij', '', 'Standup')]
    assert (await cc.CalendarCall.get(mine.id)).status == 'joined'
    await cc.act(NOW)
    assert len(cal.joins) == 1  # once

    # Unanswered suggestions lapse after the call starts.
    await cc.act(NOW + timedelta(minutes=25))
    soon = next(c for c in await cc.upcoming('u1', NOW) if c.event_id == 'soon')
    assert soon.status == 'missed'


async def test_series_choice_and_only_the_owner_answers(cal):
    cc = cal.cc
    settings = await cc.settings_for('u1')
    calls = await cc.sync('u1', settings, [_event('x1', minutes=300, series='wk'),
                                           _event('x2', minutes=600, series='wk')], NOW)
    with pytest.raises(cc.CalendarError):
        await cc.answer('someone-else', str(calls[0].id), 'send', now=NOW)
    with pytest.raises(cc.CalendarError):
        await cc.answer('u1', str(calls[0].id), 'join-now-please', now=NOW)
    await cc.answer('u1', str(calls[0].id), 'always', now=NOW)
    assert {c.status for c in await cc.upcoming('u1', NOW)} == {'approved'}
    assert (await cc.settings_for('u1')).always_series == ['wk']
    assert cal.joins == []  # not due yet
    await cc.answer('u1', str(calls[1].id), 'never', now=NOW)
    settings = await cc.settings_for('u1')
    assert settings.never_series == ['wk'] and settings.always_series == []
    assert {c.status for c in await cc.upcoming('u1', NOW)} == {'skipped'}


async def test_poll_reads_connected_calendars(cal, monkeypatch):
    cc = cal.cc
    from cognitrix.mcp import oauth

    await cal.MCPServer(name='Google Calendar', owner_id='u1', catalog_id='google-calendar',
                        auth_type='oauth').save()
    await cal.MCPServer(name='Gmail', owner_id='u2', catalog_id='gmail', auth_type='oauth').save()
    reads = []

    async def token(row):
        return 'tok'

    async def events(tok, now, hours=24):
        reads.append(tok)
        return [_event('p1', minutes=5)]

    monkeypatch.setattr(oauth, 'access_token', token)
    monkeypatch.setattr(cc, 'upcoming_events', events)
    await cc.poll(NOW)
    assert reads == ['tok'] and [c.event_id for c in await cc.upcoming('u1', NOW)] == ['p1']
    assert len(cal.alerts) == 1
    await cc.poll(NOW + timedelta(minutes=1))
    assert reads == ['tok']  # not due again yet
    await cc.save_settings('u1', enabled=False)
    await cc.poll(NOW + timedelta(hours=1))
    assert reads == ['tok']


async def test_upcoming_calls_tool(cal, monkeypatch):
    from cognitrix.tools.meetings import upcoming_calls
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    cc = cal.cc
    monkeypatch.setattr(cc, 'utcnow', lambda: NOW)   # the events are at fixed times
    token = set_execution_context(ToolExecutionContext(user_id='u1', agent_id='a1', session_id='s1'))
    try:
        out = await upcoming_calls.run(parent=SimpleNamespace(id='a1', name='Ada'))
        assert 'not connected' in str(out.content)
        await cal.MCPServer(name='Google Calendar', owner_id='u1', catalog_id='google-calendar',
                            auth_type='oauth').save()
        await cc.sync('u1', await cc.settings_for('u1'), [_event('t1', minutes=60, title='Vendor review')], NOW)
        out = await upcoming_calls.run(parent=SimpleNamespace(id='a1', name='Ada'))
    finally:
        reset_execution_context(token)
    assert '"Vendor review" (Google Meet' in str(out.content) and 'not decided yet' in str(out.content)
