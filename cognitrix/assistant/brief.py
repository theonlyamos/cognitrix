"""The morning brief: one predictable daily touchpoint from the main assistant.

Once a day, at the time the person picked (in their timezone), the scheduler
starts a run of the main assistant's check-in task with ``trigger_kind``
'brief'. That run reads mail, the calendar, tasks and open loops (read-only,
like any check-in) and calls **Publish Brief** with a few short lines in fixed
sections. The brief shows at the top of the Dashboard and as one Inbox item,
and each line has Useful / Not useful / Don't show this.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

SECTIONS = ('today', 'reply', 'waiting', 'stalled', 'offers', 'other')
SECTION_TITLES = {
    'today': 'Today', 'reply': 'Needs a reply', 'waiting': 'Waiting on others',
    'stalled': 'Stuck work', 'offers': 'I can do these', 'other': 'Also',
}
MAX_ITEMS = 12
ITEM_MAX = 300
WHY_MAX = 200
SUMMARY_MAX = 400
VERDICTS = ('useful', 'not_useful', 'never')


class Brief(Model):
    user_id: str
    agent_id: str = ''
    agent_name: str = ''
    day: str
    """The person's local date it is for."""
    run_id: str = ''
    summary: str = ''
    items: str = '[]'
    """JSON list of {section, text, why, link}."""
    feedback: str = '{}'
    """JSON {item index: verdict}."""

    @field_validator('agent_id', 'agent_name', 'run_id', 'summary', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)

    @field_validator('items', mode='before')
    @classmethod
    def _items(cls, value):
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        return value if isinstance(value, str) and value.strip().startswith('[') else '[]'

    @field_validator('feedback', mode='before')
    @classmethod
    def _feedback(cls, value):
        if isinstance(value, dict):
            return json.dumps(value)
        return value if isinstance(value, str) and value.strip().startswith('{') else '{}'


MODELS = (Brief,)


def items_of(row: Brief) -> list[dict[str, Any]]:
    try:
        value = json.loads(row.items or '[]')
    except ValueError:
        return []
    return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []


def feedback_of(row: Brief) -> dict[str, str]:
    try:
        value = json.loads(row.feedback or '{}')
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def public(row: Brief) -> dict[str, Any]:
    created = getattr(row, 'created_at', None)
    feedback = feedback_of(row)
    return {
        'id': str(row.id), 'agent_id': row.agent_id or None, 'agent_name': row.agent_name or '',
        'day': row.day, 'summary': row.summary,
        'items': [{**item, 'index': i, 'feedback': feedback.get(str(i))} for i, item in enumerate(items_of(row))],
        'sections': SECTION_TITLES,
        'created_at': str(created).replace('T', ' ')[:19] if created else None,
    }


def clean_items(raw: Any) -> list[dict[str, str]]:
    """Read the model's items generously: a list of objects, or a JSON string of one."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError('items must be a list of {section, text, why}') from None
    if not isinstance(raw, list):
        raise ValueError('items must be a list of {section, text, why}')
    out = []
    for entry in raw[:MAX_ITEMS]:
        if isinstance(entry, str):
            entry = {'text': entry}
        if not isinstance(entry, dict):
            continue
        text = ' '.join(str(entry.get('text') or '').split())[:ITEM_MAX]
        if not text:
            continue
        section = str(entry.get('section') or 'other').strip().lower().replace(' ', '_')
        section = {'needs_a_reply': 'reply', 'replies': 'reply', 'meetings': 'today', 'calendar': 'today',
                   'stuck': 'stalled', 'stuck_work': 'stalled', 'offer': 'offers'}.get(section, section)
        if section not in SECTIONS:
            section = 'other'
        link = str(entry.get('link') or '').strip()
        out.append({
            'section': section, 'text': text,
            'why': ' '.join(str(entry.get('why') or '').split())[:WHY_MAX],
            'link': link if link.startswith('/') or link.startswith('https://') else '',
        })
    return out


async def publish(user_id: str, agent: Any, run_id: str, day: str, summary: str, items: list[dict[str, str]]) -> Brief:
    """Store today's brief (replacing one already published today) and tell the person once."""
    from cognitrix.tasks.automation import create_notification

    summary = ' '.join(str(summary or '').split())[:SUMMARY_MAX]
    existing = await Brief.find_one({'user_id': str(user_id), 'day': day})
    fields = {
        'agent_id': str(getattr(agent, 'id', '') or ''), 'agent_name': str(getattr(agent, 'name', '') or ''),
        'run_id': str(run_id or ''), 'summary': summary, 'items': json.dumps(items, ensure_ascii=False),
    }
    if existing is not None:
        await Brief.update_one({'id': existing.id}, fields)
        return await Brief.get(existing.id) or existing
    row = Brief(user_id=str(user_id), day=day, **fields)
    await row.save()
    row = await Brief.find_one({'user_id': str(user_id), 'day': day}) or row
    top = '\n'.join(f"- {i['text']}" for i in items[:3])
    message = (summary + ('\n' + top if top else '')).strip() or 'Nothing needs you today.'
    await create_notification(user_id, 'Your morning brief', message, link='/dashboard', proactive=True)
    return row


async def latest(user_id: str) -> Brief | None:
    try:
        rows = list(await Brief.find({'user_id': str(user_id)}) or [])
    except Exception as exc:
        if 'no such table' in str(exc).lower():
            return None
        raise
    if not rows:
        return None
    return max(rows, key=lambda r: (r.day, str(getattr(r, 'created_at', '') or '')))


async def give_feedback(row: Brief, index: int, verdict: str) -> Brief:
    from cognitrix.assistant.proactivity import record_feedback

    if verdict not in VERDICTS:
        raise ValueError('verdict must be useful, not_useful or never')
    items = items_of(row)
    if not 0 <= index < len(items):
        raise ValueError('No such line in this brief')
    feedback = feedback_of(row)
    feedback[str(index)] = verdict
    await Brief.update_one({'id': row.id}, {'feedback': json.dumps(feedback)})
    text = items[index]['text']
    await record_feedback(
        row.user_id, 'useful' if verdict == 'useful' else 'not_useful', text,
        mute=text[:120] if verdict == 'never' else '')
    return await Brief.get(row.id) or row


def run_instructions(today_local: str) -> str:
    return (
        f"This run is the person's MORNING BRIEF for {today_local}. Look at what the brief and the "
        "notes below point to (calendar, mail, tasks, things coming due) and finish by calling Publish "
        "Brief exactly once. Keep it short: at most 8 lines in total, each one sentence the person can act "
        "on. Sections: today (meetings and deadlines), reply (mail or messages that need an answer, with "
        "who and what about), waiting (things the person is waiting on from others), stalled (stuck or "
        "failing work), offers (work you could take off their hands; also make an Offer Help for each), "
        "other. Give each line a short why. Leave out anything routine, anything already handled, and "
        "anything the person said not to show. If nothing needs them, publish an empty list with a "
        "one-line summary saying so. Do not also send Notify Me for things in the brief."
    )


def local_day(settings: Any, now: datetime | None = None) -> str:
    from cognitrix.assistant.proactivity import local_now

    return local_now(settings, now or datetime.now(UTC)).strftime('%Y-%m-%d')
