"""A board copied both ways to a Slack channel.

The person links a board (or a project channel) to a Slack channel through
their connected Slack app (Settings, Chat apps). Every post goes to Slack as
a message and every answer as a reply in its thread. In Slack, a message the
linked person writes in that channel becomes a post (a question if it ends
with "?", else a heads-up), and a reply in a post's thread becomes an answer.
Other people's messages are copied only when the app answers others.

The Slack app needs the ``channels:history`` scope and the
``message.channels`` event (``SLACK_MANIFEST`` has them), and must be invited
to the channel.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from cognitrix.board.models import BoardMirror, BoardPost

logger = logging.getLogger('cognitrix.log')

MIRRORS_MAX = 20


def _service():
    from cognitrix.board import service

    return service


def public(row: BoardMirror) -> dict[str, Any]:
    return {'id': str(row.id), 'board': row.board, 'channel_id': row.channel_id, 'slack_channel': row.slack_channel}


async def list_mirrors(user_key: str, board: str = '') -> list[BoardMirror]:
    where: dict[str, Any] = {'user_id': str(user_key)}
    if board:
        where['board'] = board
    return await BoardMirror.find(where) or []


def clean_slack_channel(value: str) -> str:
    """'C0123ABC', '<#C0123ABC|general>' or a channel link -> 'C0123ABC'."""
    text = str(value or '').strip()
    match = re.search(r'\b([CG][A-Z0-9]{6,})\b', text)
    if not match:
        raise _service().BoardError('bad_channel', 'Give the Slack channel id (C0123…): in Slack, open the '
                                                   'channel details; the id is at the bottom.')
    return match.group(1)


async def add_mirror(user_key: str, board: str, slack_channel: str) -> BoardMirror:
    from cognitrix.models.chat_channel import ChatChannel

    service = _service()
    board = service.clean_board(board)
    slack_channel = clean_slack_channel(slack_channel)
    app = await ChatChannel.find_one({'user_id': str(user_key), 'kind': 'slack'})
    if app is None:
        raise service.BoardError('no_slack', 'Connect Slack first in Settings, Chat apps.')
    for row in await BoardMirror.find({'channel_id': str(app.id), 'slack_channel': slack_channel}) or []:
        raise service.BoardError('taken', 'That Slack channel already mirrors '
                                          f'{"this" if row.board == board else "another"} board.')
    if len(await list_mirrors(user_key)) >= MIRRORS_MAX:
        raise service.BoardError('too_many', f'At most {MIRRORS_MAX} mirrors.')
    row = BoardMirror(user_id=str(user_key), board=board, channel_id=str(app.id), slack_channel=slack_channel,
                      created_at=service.now_ms())
    await row.save()
    return row


async def remove_mirror(user_key: str, mirror_id: str) -> None:
    row = await BoardMirror.get(str(mirror_id or ''))
    if row is None or row.user_id != str(user_key):
        raise _service().BoardError('not_found', 'No such mirror.')
    await BoardMirror.delete_many({'id': row.id})


# ---------------------------------------------------------------------------
# Board -> Slack
# ---------------------------------------------------------------------------

def _transport(channel_id: str):
    from cognitrix.channels.service import get_service

    svc = get_service()
    return svc._transports.get(channel_id) if svc is not None else None


def post_text(post: BoardPost) -> str:
    service = _service()
    label = service.KIND_LABELS.get(post.kind, 'Post')
    head = f'*{label}* · {post.author_name or "Someone"}'
    if post.to_name:
        head += f' → {"the owner" if post.to_name == "you" else post.to_name}'
    if post.thread:
        head += f' · _{post.thread_title or post.thread}_'
    tags = ' '.join('#' + t for t in post.tags)
    return f'{head}\n{post.text}' + (f'\n{tags}' if tags else '')


async def push(user_key: str, post_id: str, entry: dict[str, Any] | None = None) -> int:
    """Send a new post (or, with ``entry``, an answer) to each mirror of its
    board. Returns how many Slack messages were sent."""
    post = await BoardPost.get(str(post_id))
    if post is None or post.retracted:
        return 0
    sent = 0
    for row in await BoardMirror.find({'user_id': str(user_key), 'board': post.board}) or []:
        transport = _transport(row.channel_id)
        if transport is None:
            continue
        mid = str(row.id)
        if entry is not None and entry.get('via') == f'mirror:{mid}':
            continue  # the answer came from this Slack channel
        try:
            if entry is None:
                if (post.mirror or {}).get(mid):
                    continue  # the post came from (or already went to) this Slack channel
                ts = await transport.send(row.slack_channel, post_text(post))
                if ts:
                    async with _service()._lock(f'post:{post.id}'):
                        fresh = await BoardPost.get(str(post.id))
                        links = {**((fresh.mirror if fresh else None) or {}), mid: ts}
                        await BoardPost.update_one({'id': post.id}, {'mirror': links})
            else:
                ts = (post.mirror or {}).get(mid)
                if not ts:
                    continue
                verb = '' if entry.get('claim') else ':'
                await transport.send(row.slack_channel, f'*{entry.get("author_name") or "Someone"}*{verb} '
                                                        f'{entry.get("text") or ""}', thread_id=ts)
            sent += 1
        except Exception:
            logger.warning('Could not copy board post %s to Slack', post.id, exc_info=True)
    return sent


# ---------------------------------------------------------------------------
# Slack -> board
# ---------------------------------------------------------------------------

async def incoming(channel: Any, msg: Any) -> bool:
    """A message in a Slack channel a board is mirrored to. True when it was
    taken (posted, answered, or deliberately ignored)."""
    if getattr(channel, 'kind', '') != 'slack' or msg.private:
        return False
    rows = await BoardMirror.find({'channel_id': str(channel.id), 'slack_channel': str(msg.chat_id)}) or []
    if not rows:
        return False
    if msg.mention:
        return True  # Slack sends the same message as a plain message event too; that one is copied
    row = rows[0]
    service = _service()
    from cognitrix.board import channels

    owner = str(row.user_id)
    if channel.peer_id and msg.sender_id == channel.peer_id:
        author = await channels.person(owner)
    elif getattr(channel, 'answer_others', False):
        author = service.Author(kind='person', id=f'slack:{msg.sender_id}',
                                name=(msg.sender_name or f'Slack {msg.sender_id}')[:40])
    else:
        return True
    text = str(msg.text or '').strip()
    if not text:
        return True
    try:
        if msg.thread_id:
            post = next((p for p in await service.posts_on(owner, row.board)
                         if (p.mirror or {}).get(str(row.id)) == msg.thread_id), None)
            if post is None:
                return True
            await service.reply(owner, str(post.id), text, author, via=f'mirror:{row.id}')
            return True
        kind = 'question' if text.rstrip().endswith('?') else 'heads_up'
        match = re.match(r'\s*(finding|question|request|decision|heads[ -_]?up)\s*:\s*(.+)', text,
                         re.IGNORECASE | re.DOTALL)
        if match:
            kind, text = match.group(1), match.group(2)
        # Linked to this Slack message, so it isn't posted back and replies in its thread answer it.
        origin = {str(row.id): msg.message_id} if getattr(msg, 'message_id', '') else {str(row.id): 'slack'}
        await service.create_post(owner, board=row.board, kind=kind, text=text, author=author, mirror=origin)
    except service.BoardError as exc:
        logger.info('Slack message not copied to the board: %s', exc.message)
    return True
