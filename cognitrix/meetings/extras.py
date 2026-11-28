"""What an agent in a call knows going in, and what happens around the call.

- **Settings** (``MeetingSettings``, the Meetings page): what answers in a
  call may draw on by default, words to watch for, words speech-to-text
  should expect, a second pass with the speakers told apart, running notes.
- **Background** (``event_context``, ``previous_context``): the agenda and
  invited people of a calendar call, and what was still open at the last
  meeting of the same series (or with the same title). It helps the agent
  spell names, answer, and write the notes; it is never treated as speech.
- **Look-ups** (``lookup``): when someone asks the agent something in a call
  and the person allowed it (``share='work'``), the agent's memory, its
  knowledge, past meetings and the times the person is busy, found for that
  question. No tools run inside the call.
- **Spoken commands** (``command``): anyone in the call can say "<name>,
  pause notes" / "off the record", "resume", "please leave", or "mark that as
  an action item / a decision".
- **Watch words** (``watch_hits``): the person is alerted when one comes up.
- **Search** (``search``): across all the person's meetings.
- **Report** (``report_to_chat``): the notes go back to the chat that sent the
  agent, whose agent offers follow-ups (tasks, a recap email, memory) and does
  each only with the person's yes.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model
from pydantic import validator

from cognitrix.meetings.notes import SHARE_LEVELS, MeetingNote, action_items, section

logger = logging.getLogger('cognitrix.log')

MAX_WORDS_TEXT = 2000
MAX_CONTEXT = 6000
MAX_AGENDA = 3000
MAX_LOOKUP = 4000
BUSY_DAYS = 7
DEFAULT_TITLE = re.compile(r'^(google meet|microsoft teams|teams|zoom|call|the call) call$', re.I)


class MeetingSettings(Model):
    user_id: str
    share: str = 'call'
    """What answers in calls may draw on by default: 'call' or 'work' (see notes.MeetingNote.share)."""
    watch_words: str = ''
    """Alert me when one of these comes up (one per line or comma)."""
    vocabulary: str = ''
    """Names and words speech-to-text should expect (one per line or comma)."""
    speaker_pass: bool = True
    """After the call, transcribe the whole call again with the speakers told apart, where the provider can."""
    live_notes: bool = True
    """Keep running notes while the call goes on."""

    @validator('share', pre=True)
    def _share(cls, value):
        return value if value in SHARE_LEVELS else 'call'

    @validator('watch_words', 'vocabulary', pre=True)
    def _text(cls, value):
        return value or ''

    @validator('speaker_pass', 'live_notes', pre=True)
    def _flag(cls, value):
        return True if value is None else value not in ('', 0, '0', False, 'false', 'False')


MODELS = (MeetingSettings,)


async def settings_for(user_id: str) -> MeetingSettings:
    rows = await MeetingSettings.find({'user_id': str(user_id)}) or []
    if rows:
        return rows[0]
    row = MeetingSettings(user_id=str(user_id))
    await row.save()
    return row


async def save_settings(user_id: str, **fields: Any) -> MeetingSettings:
    row = await settings_for(user_id)
    changes: dict[str, Any] = {}
    if fields.get('share') is not None:
        if fields['share'] not in SHARE_LEVELS:
            raise ValueError('Answers can use what was said in the call, or also your work.')
        changes['share'] = fields['share']
    for name in ('watch_words', 'vocabulary'):
        if fields.get(name) is not None:
            changes[name] = str(fields[name])[:MAX_WORDS_TEXT]
    for name in ('speaker_pass', 'live_notes'):
        if fields.get(name) is not None:
            changes[name] = bool(fields[name])
    if changes:
        await MeetingSettings.update_one({'id': row.id}, changes)
    return await settings_for(user_id)


def settings_view(row: MeetingSettings) -> dict[str, Any]:
    return {'share': row.share, 'watch_words': row.watch_words, 'vocabulary': row.vocabulary,
            'speaker_pass': row.speaker_pass, 'live_notes': row.live_notes}


def words(text: str | list[str] | None) -> list[str]:
    """A list of words or phrases from 'a, b' / one per line."""
    if isinstance(text, list):
        parts = text
    else:
        parts = re.split(r'[\n,;]', str(text or ''))
    out: list[str] = []
    for part in parts:
        word = ' '.join(str(part).split())[:60]
        if word and word.lower() not in (w.lower() for w in out):
            out.append(word)
    return out[:50]


# ---------------------------------------------------------------- background

def _plain(text: str) -> str:
    """Calendar descriptions are HTML: keep the text and the line breaks."""
    text = re.sub(r'<\s*(br|/p|/div|/li)\s*/?>', '\n', str(text or ''), flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def _person(guest: dict[str, Any]) -> str:
    """A guest's name, or the part of their address before the @ (never the whole address)."""
    name = str(guest.get('displayName') or '').strip()
    if not name:
        name = str(guest.get('email') or '').split('@', 1)[0].replace('.', ' ').strip()
    return name[:80]


def event_context(event: dict[str, Any]) -> tuple[str, list[str]]:
    """(background, names) from a calendar event: its agenda and who is invited."""
    parts: list[str] = []
    agenda = _plain(event.get('description') or '')
    # The call link and dial-in boilerplate are no help to anyone.
    agenda = '\n'.join(line for line in agenda.splitlines()
                       if not re.search(r'https?://|^join (with|by)|^-::|meeting id|passcode|dial', line, re.I))
    if agenda:
        parts.append('Agenda and description from the calendar invite:\n' + agenda[:MAX_AGENDA])
    people = [_person(g) for g in event.get('attendees') or [] if isinstance(g, dict) and not g.get('resource')]
    people = [p for p in people if p]
    if people:
        parts.append('Invited: ' + ', '.join(people[:40]))
    return '\n\n'.join(parts), people[:40]


async def fetch_event(user_id: str, event_id: str) -> dict[str, Any] | None:
    """One event of the person's connected Google Calendar, or None."""
    import httpx

    from cognitrix.mcp import oauth
    from cognitrix.meetings import calendar_calls as cc

    row = await cc.calendar_connection(user_id)
    if row is None or not event_id:
        return None
    try:
        token = await oauth.access_token(row)
        async with httpx.AsyncClient(timeout=cc.HTTP_TIMEOUT,
                                     headers={'Authorization': f'Bearer {token}'}) as client:
            response = await client.get(
                f'{cc.CALENDAR_API}/calendars/primary/events/{event_id}',
                params={'fields': 'summary,description,attendees(displayName,email,self,resource)'})
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        logger.info('Could not read calendar event %s for the meeting background', event_id, exc_info=True)
        return None


async def previous_meeting(user_id: str, *, series_id: str = '', title: str = '',
                           exclude: str = '') -> MeetingNote | None:
    """The person's last finished meeting of the same calendar series, or with
    the same (not generic) title."""
    if not series_id and (not title or DEFAULT_TITLE.match(title.strip())):
        return None
    rows = [r for r in await MeetingNote.find({'user_id': str(user_id)}) or []
            if str(r.id) != str(exclude) and r.status == 'done' and r.notes and not r.test
            and ((series_id and r.series_id == series_id)
                 or (not series_id and r.title.strip().lower() == title.strip().lower()))]
    rows.sort(key=lambda r: str(getattr(r, 'created_at', '') or ''), reverse=True)
    return rows[0] if rows else None


def previous_context(row: MeetingNote | None) -> str:
    if row is None:
        return ''
    when = str(getattr(row, 'created_at', '') or '')[:10]
    parts = []
    for name in ('Decisions', 'Action items', 'Open questions'):
        body = section(row.notes, name)
        if body:
            parts.append(f'{name}:\n{body[:1200]}')
    if not parts:
        return ''
    return f'Last time ("{row.title}", {when}):\n' + '\n'.join(parts)


def join_context(*parts: str) -> str:
    return '\n\n'.join(p.strip() for p in parts if p and p.strip())[:MAX_CONTEXT]


def hints(row: MeetingNote, agent_name: str, owner: str, settings: MeetingSettings | None) -> list[str]:
    """Words speech-to-text should expect: the names in the call, the title, the person's own list."""
    out = [row.bot_name, agent_name, owner]
    invited = re.search(r'^Invited: (.+)$', row.context or '', re.M)
    if invited:
        out += invited.group(1).split(', ')
    out += [w for w in re.findall(r"[A-Z][\w'-]{2,}", row.title or '')]
    if settings is not None:
        out += words(settings.vocabulary)
    return words([w for w in out if w])


# ---------------------------------------------------------------- spoken commands

_PAUSE = re.compile(r"\b(?:pause|stop|hold)\s+(?:the\s+|your\s+)?(?:notes|note[- ]?taking|taking notes|recording|"
                    r"listening|transcri\w+)\b|\boff\s+(?:the\s+)?record\b", re.I)
# A resume is said to the note-taker about the notes ("resume the notes",
# "start taking notes again", "back on the record"), or is the whole request
# ("Ada, resume."), never a word inside another sentence ("continue the
# budget discussion") or a question ("when should we resume taking notes?").
_RESUME = re.compile(r"\b(?:resume|restart|continue|start|keep)\s+(?:taking\s+)?(?:the\s+|your\s+)?(?:notes|"
                     r"note[- ]?taking|recording|listening|transcri\w+)(?:\s+again)?\b|"
                     r"\b(?:go\s+|we're\s+|we\s+are\s+)?back\s+on\s+(?:the\s+)?record\b|"
                     r"\bresume(?:\s+now|\s+please)?$", re.I)
# What may come before a resume in the same clause: a polite way of asking,
# nothing that makes it a question or a remark.
_ASKING = re.compile(r"^(?:(?:ok(?:ay)?|alright|right|so|now|and|then|please|yes|yeah|great|thanks|"
                     r"thank\s+you|you\s+can|we\s+can|can\s+you|could\s+you|would\s+you|will\s+you|"
                     r"go\s+ahead\s+and|let's|let\s+us|time\s+to|i\s+need\s+you\s+to|i'd\s+like\s+you\s+to)\b"
                     r"[\s,]*)*$", re.I)
_NOT = re.compile(r"\b(?:don'?t|do\s+not|never|no\s+need\s+to|not\s+yet|shouldn'?t|won'?t|can'?t|cannot|"
                  r"wouldn'?t|isn'?t|aren'?t|wasn'?t|not)\b[\w\s,']{0,20}$", re.I)
_LEAVE = re.compile(r"\b(?:please\s+leave|leave\s+(?:the|this|our)\s+(?:call|meeting)|you\s+can\s+(?:go|leave)"
                    r"(?:\s+now)?\b|drop\s+off\b|hang\s+up\b)", re.I)
_MARK = re.compile(r"\b(?:mark|note|record|log|add)\s+(?:that|this|it)\s+(?:down\s+)?as\s+(?:an?\s+)?"
                   r"(action(?:\s+item)?|decision|todo|to-do|follow[- ]up)\b", re.I)
_CLAUSE = re.compile(r"[.!?;:,]\s*")


def _strip_names(text: str, names: list[str] | None) -> str:
    """The words without the agent's name ("Ada resume" and "Ada, resume" alike)."""
    out = text
    for name in sorted((n for n in names or [] if n), key=len, reverse=True):
        out = re.sub(rf"(?:\b(?:hey|ok|okay)\s+)?\b{re.escape(name)}\b\s*[,:]?\s*", ' ', out, flags=re.I)
    return re.sub(r'\s+', ' ', out).strip()


def _after_name(text: str) -> str:
    """What follows a leading "Ada," so a bare "Ada, resume." reads as the whole request."""
    return re.sub(r"^\s*(?:hey\s+|ok\s+|okay\s+)?[\w'-]+(?:\s[\w'-]+)?\s*[,:]\s*", '', text, count=1)


def _said(pattern: re.Pattern, text: str) -> re.Match | None:
    """The command, unless it is negated just before ("don't resume the notes yet")."""
    for match in pattern.finditer(text):
        if not _NOT.search(text[:match.start()]):
            return match
    return None


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE.split(text) if c.strip()]


def _asks_to_resume(text: str) -> bool:
    """A clause that is a request to resume: the resume words with at most a
    polite lead-in, and not a question unless it is "can you / could you"."""
    for clause in _clauses(text):
        match = _RESUME.search(clause)
        if match is None:
            continue
        lead = clause[:match.start()].strip()
        if not _ASKING.match(lead):
            continue
        tail = text[text.find(clause) + len(clause):].lstrip()[:1]
        if tail == '?' and not re.match(r'^(?:can|could|would|will)\s+you\b', lead, re.I):
            continue
        return True
    return False


def command(text: str, names: list[str] | None = None) -> tuple[str, str] | None:
    """A command in something said to the agent: ('pause'|'resume'|'leave'|'mark', detail).
    ``names``: what the agent is called in the call, so "Ada resume" (no comma) reads too."""
    text = str(text or '')
    rest = _strip_names(text, names) if names else _after_name(text)
    if _said(_PAUSE, text):
        return 'pause', ''
    if _asks_to_resume(rest):
        return 'resume', ''
    if _said(_LEAVE, text):
        return 'leave', ''
    match = _said(_MARK, text)
    if match:
        kind = match.group(1).lower()
        return 'mark', 'decision' if kind == 'decision' else 'action item'
    return None


def up_to_command(text: str) -> str:
    """The words up to and including the pause itself: whatever followed it
    in the same piece of speech is off the record."""
    match = _said(_PAUSE, text)
    if match is None:
        return text
    return text[:match.end()].strip()


def watch_hits(text: str, watch: list[str]) -> list[str]:
    low = str(text or '').lower()
    return [w for w in watch if w and re.search(rf'(?<!\w){re.escape(w.lower())}(?!\w)', low)]


# ---------------------------------------------------------------- look-ups

async def busy_times(user_id: str, now: datetime | None = None) -> str:
    """When the person is busy in the next week (times only, no titles), or ''."""
    from cognitrix.mcp import oauth
    from cognitrix.meetings import calendar_calls as cc

    row = await cc.calendar_connection(user_id)
    if row is None:
        return ''
    now = now or datetime.now(UTC).replace(tzinfo=None)
    try:
        token = await oauth.access_token(row)
        events = await cc.upcoming_events(token, now, hours=BUSY_DAYS * 24)
    except Exception:
        logger.info('Could not read busy times for a meeting answer', exc_info=True)
        return ''
    slots = []
    for event in events:
        if event.get('status') == 'cancelled':
            continue
        if any(g.get('self') and g.get('responseStatus') == 'declined' for g in event.get('attendees') or []):
            continue
        start = str((event.get('start') or {}).get('dateTime') or '')
        end = str((event.get('end') or {}).get('dateTime') or '')
        if not start:
            continue  # all-day events don't block times
        try:
            a, b = datetime.fromisoformat(start.replace('Z', '+00:00')), datetime.fromisoformat(end.replace('Z', '+00:00'))
        except ValueError:
            continue
        zone = a.strftime('%z')
        zone = f'UTC{zone[:3]}:{zone[3:]}' if zone else 'UTC'
        slots.append(f'{a:%a %d %b} {a:%H:%M}-{b:%H:%M} ({zone})')
    if not slots:
        return f'Calendar: nothing booked in the next {BUSY_DAYS} days.'
    return f'Calendar, busy times in the next {BUSY_DAYS} days (free otherwise during the day):\n' + \
        '\n'.join(f'- {s}' for s in slots[:40])


async def lookup(user_id: str, agent: Any, question: str, *, exclude: str = '') -> str:
    """What the agent's memory, knowledge, past meetings and calendar have on ``question``."""
    parts: list[str] = []
    question = ' '.join(str(question or '').split())[-600:]
    if not question:
        return ''
    try:
        from cognitrix.memory import saved

        found = await saved.search(user_id, agent, question, k=6)
        if found:
            parts.append('From memory:\n' + '\n'.join(f'- {m.content[:300]}' for m in found))
    except Exception:
        logger.debug('Memory look-up for a meeting answer failed', exc_info=True)
    try:
        from cognitrix import knowledge

        collections = [str(c) for c in (getattr(agent, 'knowledge', None) or [])]
        if collections:
            hits = await knowledge.search(user_id, collections, question, 4)
            if hits:
                parts.append('From knowledge:\n' + '\n'.join(
                    f'- ({h.get("collection")}) {str(h.get("text") or "")[:400]}' for h in hits))
    except Exception:
        logger.debug('Knowledge look-up for a meeting answer failed', exc_info=True)
    try:
        hits = await search(user_id, question, 3, exclude=exclude)
        if hits:
            parts.append('From past meetings:\n' + '\n'.join(
                f'- "{h["title"]}" ({h["date"]}): {h["snippet"]}' for h in hits))
    except Exception:
        logger.debug('Meeting look-up for a meeting answer failed', exc_info=True)
    if re.search(r'\b(free|busy|available|availability|calendar|schedule|book|meet|monday|tuesday|wednesday|'
                 r'thursday|friday|saturday|sunday|tomorrow|next week|today|\d{1,2}(:\d\d)?\s?(am|pm))\b',
                 question, re.I):
        busy = await busy_times(user_id)
        if busy:
            parts.append(busy)
    return '\n\n'.join(parts)[:MAX_LOOKUP]


# ---------------------------------------------------------------- search

def _snippet(text: str, terms: list[str], width: int = 220) -> str:
    low = text.lower()
    at = min((low.find(t) for t in terms if t in low), default=-1)
    if at < 0:
        return ' '.join(text[:width].split())
    start = max(0, at - width // 3)
    piece = ' '.join(text[start:start + width].split())
    return ('…' if start else '') + piece + ('…' if start + width < len(text) else '')


async def search(user_id: str, query: str, limit: int = 10, *, exclude: str = '') -> list[dict[str, Any]]:
    """The person's meetings that best match ``query``, with a snippet each."""
    terms = [t for t in re.findall(r'\w+', str(query or '').lower()) if len(t) > 2][:12]
    phrase = ' '.join(str(query or '').lower().split())
    if not terms:
        return []
    scored = []
    for row in await MeetingNote.find({'user_id': str(user_id)}) or []:
        if str(row.id) == str(exclude) or row.test:
            continue
        title, notes = (row.title or '').lower(), (row.notes or '').lower()
        # Both transcripts: the timed one has the agent's own lines, the speaker one may word things better.
        body = '\n'.join(t for t in (row.transcript, row.speaker_transcript) if t).lower()
        score = sum(3 * title.count(t) + 2 * notes.count(t) + min(body.count(t), 10) for t in terms)
        matched = sum(1 for t in terms if t in title or t in notes or t in body)
        if not score or matched < max(1, (len(terms) + 1) // 2):
            continue
        if phrase and (phrase in notes or phrase in body):
            score += 10
        sources = [row.notes, row.transcript, row.speaker_transcript]
        source = next((s for s in sources if s and any(t in s.lower() for t in terms)), '')
        scored.append((score, {
            'id': str(row.id), 'title': row.title, 'date': str(getattr(row, 'created_at', '') or '')[:10],
            'kind': row.kind, 'snippet': _snippet(source or '', terms),
        }))
    scored.sort(key=lambda pair: -pair[0])
    return [hit for _, hit in scored[:limit]]


# ---------------------------------------------------------------- after the call

FOLLOW_UPS = ('Offer the person follow-ups for this call, as one short list they can pick from, and do each '
              'only after they say yes: create tasks for the action items they or their agents own (Create '
              'Task), draft a recap email to the people in the call, save the decisions to memory, or set a '
              'reminder to chase what others owe. Do not start any of them on your own.')


async def report_to_chat(row: MeetingNote, *, notes: str = '', error: str = '') -> bool:
    """Tell the chat that sent the agent how the call went. True if it got there."""
    if not row.session_id:
        return False
    from cognitrix.sessions import agent_chats

    who = row.bot_name or 'The agent'
    if error:
        body = f'{who} could not stay in the call "{row.title}" (meeting {row.id}): {error}'
    else:
        items = action_items(notes)
        listed = '\n'.join(f'- {i["owner"]}: {i["what"]}' for i in items) or '(none)'
        body = (f'{who} is back from the call "{row.title}" (meeting {row.id}). Its notes:\n\n{notes[:12000]}\n\n'
                f'Action items found:\n{listed}\n\n{FOLLOW_UPS}')

    async def quiet(_status: str, _reply: str) -> None:
        return None  # the meeting's own Inbox alert already told the person

    try:
        outcome = await agent_chats.deliver_report(
            row.user_id, row.chat_agent_id or row.agent_id, row.session_id, agent_chats.REPORT_PREFIX + body,
            sender=f'The call "{row.title}"', wait_seconds=120, on_done=quiet)
    except Exception:
        logger.info('Could not report meeting %s to its chat', row.id, exc_info=True)
        return False
    return outcome in ('steered', 'started')


def recent_question(lines: list[str], n: int = 4) -> str:
    """The last few lines, without times, as the question to look things up for."""
    return ' '.join(re.sub(r'^\[[\d:]+\]\s*', '', ln) for ln in lines[-n:])


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def stamp(delta_s: float = 0) -> str:
    return (now_utc() + timedelta(seconds=delta_s)).strftime('%Y-%m-%d %H:%M:%S')


def dumps(value: Any) -> str:
    return json.dumps(value)
