"""Board commands: fixed workflows anyone on the board runs by posting ``/name …``.

A command names an agent and a prompt. Posting ``/review PR 12`` (as a
person, an agent, or an outside agent) saves the post as a request, starts a
chat with the command's agent with the prompt and "PR 12", and posts the
agent's result back as the answer, in the same thread.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from cognitrix.board.models import BoardCommand, BoardPost

logger = logging.getLogger('cognitrix.log')

NAME_MAX = 24
PROMPT_MAX = 4000
COMMANDS_MAX = 30

SUGGESTED = {
    'review': ('Review a pull request or a change',
               'Review the change below. Read the diff and the code around it, run the tests if you can, and '
               'list real problems first (bugs, security, missing tests), then smaller notes. Say plainly '
               'whether it is ready to merge. Do not merge it yourself.'),
    'implement': ('Build what is asked, on a branch',
                  'Implement what is asked below. Work on a new branch, keep the change small, add or update '
                  'tests, and run them. Open a pull request if the project has a repository, but do not merge '
                  'or deploy. Report what you changed and how you checked it.'),
    'triage': ('Sort new issues or bug reports',
               'Triage what is described below: say what each item is (bug, feature, question, duplicate), '
               'how urgent it is, and who or which agent should take it. Ask on the board if something is '
               'unclear instead of guessing.'),
}


def _service():
    from cognitrix.board import service

    return service


def clean_name(name: str) -> str:
    value = re.sub(r'[^a-z0-9_-]+', '-', str(name or '').strip().lstrip('/').lower()).strip('-')[:NAME_MAX]
    if not value:
        raise _service().BoardError('bad_name', 'A command needs a short name, like review.')
    return value


def split(text: str) -> tuple[str, str]:
    """('review', 'PR 12') for '/review PR 12'; ('', '') when it isn't a command."""
    match = re.match(r'\s*/([A-Za-z0-9_-]{1,24})(?:\s+(.*))?$', str(text or ''), re.DOTALL)
    if not match:
        return '', ''
    return match.group(1).lower(), (match.group(2) or '').strip()


async def list_commands(user_key: str) -> list[BoardCommand]:
    rows = await BoardCommand.find({'user_id': str(user_key)}) or []
    return sorted(rows, key=lambda r: r.name)


async def find(user_key: str, text: str) -> BoardCommand | None:
    name, _ = split(text)
    if not name:
        return None
    return await BoardCommand.find_one({'user_id': str(user_key), 'name': name})


def public(row: BoardCommand, agents: dict[str, str] | None = None) -> dict[str, Any]:
    return {'id': str(row.id), 'name': row.name, 'agent_id': row.agent_id,
            'agent_name': (agents or {}).get(row.agent_id, ''), 'prompt': row.prompt,
            'description': row.description}


async def save_command(user_key: str, name: str, agent_id: str, prompt: str, description: str = '') -> BoardCommand:
    from cognitrix.agents.access import find_visible_agent

    service = _service()
    name = clean_name(name)
    prompt = str(prompt or '').strip() or SUGGESTED.get(name, ('', ''))[1]
    if not prompt:
        raise service.BoardError('prompt_required', 'Say what the agent should do when the command runs.')
    if len(prompt) > PROMPT_MAX:
        raise service.BoardError('too_long', f'A command prompt holds at most {PROMPT_MAX} characters.')
    agent = await find_visible_agent(user_key, agent_id=str(agent_id or ''))
    if agent is None:
        raise service.BoardError('bad_agent', 'Pick the agent that runs the command.')
    description = ' '.join(str(description or SUGGESTED.get(name, ('',))[0]).split())[:120]
    row = await BoardCommand.find_one({'user_id': str(user_key), 'name': name})
    data = {'agent_id': str(agent.id), 'prompt': prompt, 'description': description}
    if row is None:
        if len(await list_commands(user_key)) >= COMMANDS_MAX:
            raise service.BoardError('too_many', f'At most {COMMANDS_MAX} commands.')
        row = BoardCommand(user_id=str(user_key), name=name, created_at=service.now_ms(), **data)
        await row.save()
        return row
    await BoardCommand.update_one({'id': row.id}, data)
    for key, value in data.items():
        setattr(row, key, value)
    return row


async def delete_command(user_key: str, name: str) -> None:
    await BoardCommand.delete_many({'user_id': str(user_key), 'name': clean_name(name)})


async def run(user_key: str, post: BoardPost, command: BoardCommand, author: Any) -> str:
    """Start the command's agent on a post. Returns 'sent' or why not."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats

    service = _service()
    agent = await find_visible_agent(user_key, agent_id=command.agent_id)
    reason = ''
    if agent is None:
        reason = 'The agent that runs this command no longer exists.'
    elif author.depth >= agent_chats.MAX_DEPTH:
        reason = 'Asked from a chat agents started, too deep to start another.'
    elif agent_chats.running_agent_chats(user_key) >= agent_chats.MAX_RUNNING_PER_USER:
        reason = 'Too many agent chats were running.'
    elif author.kind != 'person' and not await service._take_wake(user_key):
        reason = "Today's limit of chats the board may start is used up."
    if reason:
        await service._mark_wake(post, 'skipped', reason=reason, command=command.name)
        return reason
    _, rest = split(post.text)
    session = await service._open_board_chat(user_key, post, agent, author.depth, f'/{command.name} {rest}')
    where = f' in the thread "{post.thread_title or post.thread}"' if post.thread else ''
    text = (f'{post.author_name} ran /{command.name} on the message board (post {post.id}){where}.\n\n'
            f'{command.prompt}\n\nWhat they asked: {rest or "(nothing more)"}\n\n'
            'When you are done, end with your result in a few short paragraphs: it is posted back on the '
            'board as the answer. If you need something from someone, ask on the board (Board tool) and '
            'say so in your result.')
    await service._mark_wake(post, 'sent', chat_id=str(session.id), command=command.name)
    agent_chats.run_in_background(user_key, agent, session, text,
                                  on_done=_report_back(user_key, post, agent, session))
    return 'sent'


def _report_back(user_key: str, post: BoardPost, agent: Any, session: Any):
    service = _service()
    replies_before = len(post.replies)

    async def done(status: str, reply_text: str) -> None:
        fresh = await BoardPost.get(str(post.id))
        if fresh is None or fresh.retracted:
            return
        by = service.Author(kind='agent', id=str(agent.id), name=str(agent.name), chat_id=str(session.id))
        if any(r.get('author_id') == str(agent.id) for r in fresh.replies[replies_before:]) \
                and fresh.status != 'open':
            return
        if status != 'complete' or not reply_text.strip():
            text = f'/{post.wake.get("command") or "command"} did not finish ({status or "stopped"}).'
        else:
            text = reply_text.strip()[:service.REPLY_MAX]
        try:
            await service.reply(user_key, str(post.id), text, by, close=status == 'complete', trusted=True)
        except service.BoardError:
            logger.debug('Could not post the result of board command on %s', post.id, exc_info=True)

    return done
