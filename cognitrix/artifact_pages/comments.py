"""Comments on artifact pages, and sending them to the agent that made the page.

A comment someone who can edit the page sends to the agent (or that mentions
``@agent``) wakes that agent in the chat the page was made in. The agent reads
the comment, may publish a new version, and replies in the thread with the
Artifact Comments tool. Each page takes at most ``MAX_SENT_PER_HOUR`` such
wake-ups an hour so a busy thread can't run up a bill.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC
from typing import Any

from cognitrix.artifact_pages import store
from cognitrix.artifact_pages.models import ArtifactComment, ArtifactPage

logger = logging.getLogger('cognitrix.log')

MAX_BODY = 4000
MAX_SENT_PER_HOUR = 60
WAIT_SECONDS = 600.0
POLL_SECONDS = 2.0
AGENT_PREFIX = 'agent:'
MENTION = re.compile(r'(^|\s)@(agent|claude)\b', re.I)

_sent: dict[str, list[float]] = {}
_TASKS: set[asyncio.Task] = set()


def mentions_agent(body: str, agent_name: str = '') -> bool:
    if MENTION.search(body or ''):
        return True
    name = (agent_name or '').strip()
    return bool(name) and re.search(rf'(^|\s)@{re.escape(name)}\b', body or '', re.I) is not None


def _rate_ok(page_id: str) -> bool:
    cutoff = time.monotonic() - 3600
    recent = [t for t in _sent.get(page_id, []) if t > cutoff]
    if len(recent) >= MAX_SENT_PER_HOUR:
        _sent[page_id] = recent
        return False
    recent.append(time.monotonic())
    _sent[page_id] = recent
    return True


def _stamp() -> str:
    # To the microsecond, so replies written in the same second keep their order.
    from datetime import datetime

    return datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S.%f')


def _clean_anchor(anchor: Any) -> dict[str, Any]:
    if not isinstance(anchor, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ('x', 'y'):
        try:
            value = float(anchor.get(key))
        except (TypeError, ValueError):
            continue
        out[key] = min(1.0, max(0.0, value))
    text = ' '.join(str(anchor.get('text') or '').split())[:200]
    if text:
        out['text'] = text
    return out


async def thread_rows(page_id: str) -> list[ArtifactComment]:
    rows = await ArtifactComment.find({'page_id': str(page_id)}) or []
    return sorted(rows, key=lambda r: (r.created, str(r.id)))


def threads(rows: list[ArtifactComment]) -> list[dict[str, Any]]:
    roots = [r for r in rows if not r.thread_id]
    replies: dict[str, list[ArtifactComment]] = {}
    for r in rows:
        if r.thread_id:
            replies.setdefault(r.thread_id, []).append(r)

    def one(r: ArtifactComment) -> dict[str, Any]:
        return {
            'id': str(r.id), 'author_id': r.author_id, 'author_name': r.author_name,
            'is_agent': r.author_id.startswith(AGENT_PREFIX), 'body': r.body,
            'created': r.created, 'version': r.version, 'sent_to_agent': r.sent_to_agent,
        }

    return [{
        **one(root),
        'anchor': root.anchor or {},
        'resolved': root.resolved,
        'replies': [one(x) for x in replies.get(str(root.id), [])],
    } for root in roots]


async def add(
    page: ArtifactPage,
    *,
    author_id: str,
    author_name: str,
    body: str,
    thread_id: str = '',
    anchor: Any = None,
    version: int = 0,
    send_to_agent: bool = False,
) -> ArtifactComment:
    text = str(body or '').strip()
    if not text:
        raise store.PageError('empty', 'Write a comment first')
    text = text[:MAX_BODY]
    root: ArtifactComment | None = None
    if thread_id:
        root = await ArtifactComment.get(thread_id)
        if root is None or root.page_id != str(page.id) or root.thread_id:
            raise store.PageError('no_thread', 'That comment thread is gone', 404)
    row = ArtifactComment(
        page_id=str(page.id), thread_id=str(root.id) if root else '', author_id=author_id,
        author_name=author_name, body=text, anchor={} if root else _clean_anchor(anchor),
        version=int(version or page.version), sent_to_agent=bool(send_to_agent), created=_stamp(),
    )
    await row.save()
    if root is not None and root.resolved and not author_id.startswith(AGENT_PREFIX):
        # A new reply reopens the thread.
        await ArtifactComment.update_one({'id': str(root.id)}, {'resolved': False})
    await ArtifactPage.update_one({'id': str(page.id)}, {'updated': page.updated or store.now()})
    return row


async def set_resolved(page: ArtifactPage, thread_id: str, resolved: bool) -> None:
    root = await ArtifactComment.get(thread_id)
    if root is None or root.page_id != str(page.id) or root.thread_id:
        raise store.PageError('no_thread', 'That comment thread is gone', 404)
    await ArtifactComment.update_one({'id': str(root.id)}, {'resolved': bool(resolved)})


async def notify(page: ArtifactPage, comment: ArtifactComment, *, people: dict[str, str]) -> None:
    """Tell the owner (and whoever started the thread) about a new comment."""
    from cognitrix.tasks.automation import create_notification

    targets: set[str] = {page.owner_id}
    if comment.thread_id:
        root = await ArtifactComment.get(comment.thread_id)
        if root is not None and not root.author_id.startswith(AGENT_PREFIX):
            targets.add(root.author_id)
        for reply in await ArtifactComment.find({'thread_id': comment.thread_id}) or []:
            if not reply.author_id.startswith(AGENT_PREFIX):
                targets.add(reply.author_id)
    targets.discard(comment.author_id)
    who = comment.author_name or people.get(comment.author_id, 'Someone')
    link = f'/artifacts/{page.id}?thread={comment.thread_id or comment.id}'
    for user_key in targets:
        if not user_key or user_key.startswith(AGENT_PREFIX):
            continue
        if store.role_of(page, user_key) is None:
            continue
        try:
            await create_notification(user_key, f'{who} commented on {page.title}', comment.body[:300], link=link)
        except Exception:
            logger.debug('Could not record a comment alert', exc_info=True)


def _agent_text(page: ArtifactPage, comment: ArtifactComment, root: ArtifactComment | None) -> str:
    thread = str(root.id) if root else str(comment.id)
    where = ''
    anchor = (root.anchor if root else comment.anchor) or {}
    if anchor.get('text'):
        where = f' about "{anchor["text"]}"'
    earlier = ''
    if root is not None:
        earlier = f'\n\nThe thread started with: "{root.body[:600]}"'
    return (
        f'[Artifact comment] {comment.author_name or "Someone"} commented on your artifact '
        f'"{page.title}" (artifact_id {page.id}, version {comment.version}){where}:\n\n'
        f'{comment.body}{earlier}\n\n'
        f'Thread id: {thread}. If they ask for a change, read the page with Read Artifact, publish the fix '
        f'with Publish Artifact using artifact_id {page.id}, then reply in the thread with Artifact Comments '
        f'(action "reply", thread_id {thread}) saying what you changed. If they ask a question, just reply there. '
        f'Leave the thread open: they resolve it once they have looked.'
    )


async def send_to_agent(page: ArtifactPage, comment: ArtifactComment) -> str:
    """Wake the page's agent for this comment. Returns a short status for the UI."""
    if not page.agent_id:
        return 'This artifact has no agent to send comments to.'
    if not _rate_ok(str(page.id)):
        return f'Not sent: this artifact already sent {MAX_SENT_PER_HOUR} comments to its agent in the last hour.'
    root = await ArtifactComment.get(comment.thread_id) if comment.thread_id else None
    text = _agent_text(page, comment, root)
    task = asyncio.get_running_loop().create_task(_deliver(page, text))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return 'sent'


async def _deliver(page: ArtifactPage, text: str) -> None:
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    owner = page.owner_id
    try:
        agent = await find_visible_agent(owner, agent_id=page.agent_id)
        if agent is None:
            return
        session = None
        if page.session_id:
            session = await Session.get(page.session_id)
            if session is not None and (str(session.user_id or '') != owner
                                        or str(session.agent_id or '') != str(agent.id)):
                session = None
        if session is None:
            session = await agent_chats.create_chat(owner, agent, {
                'kind': 'artifact', 'artifact_id': str(page.id), 'agent_id': str(agent.id), 'depth': 1,
            })
            await ArtifactPage.update_one({'id': str(page.id)}, {'session_id': str(session.id)})
        sid = str(session.id)
        waited = 0.0
        while agent_chats.is_running(owner, sid) and waited < WAIT_SECONDS:
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        if agent_chats.is_running(owner, sid):
            logger.info('Chat %s stayed busy; artifact comment not delivered', sid)
            return
        session = await Session.get(sid) or session
        agent_chats.run_in_background(owner, agent, session, text)
    except Exception:
        logger.exception('Could not send an artifact comment to its agent')
