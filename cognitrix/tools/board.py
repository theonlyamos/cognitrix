"""Board: the message board agents share (cognitrix/board/service.py).

Every agent of a signed-in person gets it per turn. A swarm worker posts to
its swarm's board by default; everyone else to the board all the person's
agents share.
"""
from __future__ import annotations

from typing import Any

from cognitrix.board import service
from cognitrix.board.service import Author, BoardError
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

BOARD_TOOL_CATEGORY = 'board'
READ_LIMIT = 15


class _Me:
    def __init__(self, user_key: str, author: Author, *, swarm_id: str = '', worker: str = '',
                 lead_swarm: str = '', agent_id: str = '', project_id: str = '', thread: str = '',
                 thread_title: str = ''):
        self.user_key = user_key
        self.author = author
        self.swarm_id = swarm_id
        """Set for a swarm worker: its swarm."""
        self.worker = worker
        self.lead_swarm = lead_swarm
        """Set for a chat that started a swarm: its newest one."""
        self.agent_id = agent_id
        self.project_id = project_id
        """The project of this chat or task: its channel is the default board."""
        self.thread = thread
        """In a task run: ``task:<id>``, its thread on the project channel."""
        self.thread_title = thread_title

    def default_board(self) -> str:
        if self.swarm_id:
            return service.swarm_board(self.swarm_id)
        return service.project_board(self.project_id) if self.project_id else 'agents'


async def _me(ctx: Any, parent: Any) -> _Me:
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    if ctx.restricted:
        raise BoardError('not_allowed', 'API key calls cannot use the message board.')
    if not ctx.user_id:
        raise BoardError('no_owner', 'The message board belongs to a signed-in person.')
    user_key = str(ctx.user_id)
    chat = await Session.get(ctx.session_id) if ctx.session_id else None
    started_by = (getattr(chat, 'started_by', None) or {}) if chat is not None else {}
    agent_id = str(getattr(parent, 'id', '') or ctx.agent_id or '')
    agent_name = str(getattr(parent, 'name', '') or 'Agent')
    depth = agent_chats.depth_of(chat) if chat is not None else 0
    chat_id = str(ctx.session_id or '')
    if started_by.get('swarm_id') and started_by.get('worker'):
        worker = str(started_by['worker'])
        return _Me(user_key, Author(kind='agent', id=agent_id, name=worker, chat_id=chat_id, depth=depth),
                   swarm_id=str(started_by['swarm_id']), worker=worker, agent_id=agent_id)
    lead_swarm = ''
    if chat is not None and not ctx.run_id:
        try:
            from cognitrix.swarms import service as swarms

            latest = await swarms.latest_for_chat(user_key, chat_id)
            lead_swarm = str(latest.id) if latest is not None else ''
        except Exception:
            lead_swarm = ''
    project_id, thread, thread_title = await _project_and_thread(ctx, chat)
    return _Me(user_key, Author(kind='agent', id=agent_id, name=agent_name, chat_id=chat_id, depth=depth),
               lead_swarm=lead_swarm, agent_id=agent_id, project_id=project_id, thread=thread,
               thread_title=thread_title)


async def _project_and_thread(ctx: Any, chat: Any) -> tuple[str, str, str]:
    """(project id, thread, thread title) of the turn: a task's thread on its project channel."""
    project_id = ''
    try:
        from cognitrix.projects.context import get as turn_project

        current = turn_project()
        project_id = current.id if current is not None else ''
    except Exception:
        project_id = ''
    project_id = project_id or str(getattr(chat, 'project_id', '') or '')
    task_id = str(getattr(ctx, 'task_id', '') or getattr(chat, 'task_id', '') or '')
    if not task_id:
        return project_id, '', ''
    try:
        from cognitrix.tasks.base import Task

        task = await Task.get(task_id)
    except Exception:
        task = None
    if task is None:
        return project_id, '', ''
    project_id = project_id or str(getattr(task, 'project_id', '') or '')
    return project_id, f'task:{task.id}', str(task.title or '')


def _board(me: _Me, board: str, *, posting: bool) -> str:
    value = str(board or '').strip().lower()
    if not value:
        return me.default_board()
    if value in ('agents', 'all', 'everyone'):
        return 'agents'
    if value in ('project', 'channel', 'my project', 'project channel'):
        if not me.project_id:
            raise BoardError('no_project', 'This chat is not in a project. Use board "agents".')
        return service.project_board(me.project_id)
    if value in ('swarm', 'my swarm', 'team'):
        swarm_id = me.swarm_id or me.lead_swarm
        if not swarm_id:
            raise BoardError('no_swarm', 'You are not in a swarm. Use board "agents".')
        return service.swarm_board(swarm_id)
    key = service.clean_board(str(board).strip())
    swarm_id = service.swarm_of(key)
    if posting and swarm_id and swarm_id not in (me.swarm_id, me.lead_swarm):
        raise BoardError('not_your_swarm', 'You can only post on the board of a swarm you are part of.')
    return key


def _where(board: str) -> str:
    if service.swarm_of(board):
        return 'the swarm board'
    return 'the project channel' if service.project_of(board) else 'the board'


def _found(rows: list, board: str, what: str) -> str:
    where = _where(board)
    if not rows:
        return f'Nothing {what}on {where}.'
    return f'{len(rows)} post(s) {what}on {where}, newest first:\n' + '\n'.join(
        f'- {service.post_line(p)}' for p in rows)


# Its waits (for replies from other agents) must not hold a tool slot.
@tool(category=BOARD_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'],
      occupies_execution_slot=False)
async def board(action: str, text: str = '', kind: str = '', to: str = '', tags: str = '', post_id: str = '',
                filter: str = '', board: str = '', close: bool = False, kinds: str = '', wake: str = '',
                query: str = '', force: bool = False, thread: str = '', seconds: int = 0, when: str = '',
                *, parent=None) -> ToolOutcome:
    """The message board your agents share (a swarm has its own). Post things other agents need, read what
    they posted, and answer questions. Every post has a kind: "finding" (something you found out that changes
    others' work), "question" (something you can't settle yourself), "request" (work you need someone else to
    do), "decision" (a rule everyone follows from now on; pinned) or "heads_up" (a warning). Questions and
    requests stay open until answered or closed. Post only what others need: no thanks, status or chatter.
    Write @Name in a post or answer to tag an agent or a person (on a swarm board a worker, or @lead): they
    are told and can respond. Before asking, search: it may already be answered. A project has its own
    channel (board "project"), the default in a project chat; in a task its posts go in the task's thread.
    Claim an open request before working on it so nobody does it twice.
    Actions: "read" lists posts (filter: open, pinned, for_me, mine, a kind or a #tag; post_id shows one with
    all its answers); "search" finds posts by words (query); "post" adds one (kind, text, optional to and
    tags); "answer" replies to a post (post_id, text; close=true also marks it done); "edit" corrects your own
    post (post_id, text); "retract" withdraws your own post; "remember" proposes a decision for everyone's
    long-term memory (post_id); "close" marks a question or request done;
    "reopen"; "pin" / "unpin"; "follow" sets which new posts you are told about at the start of a turn
    (kinds, tags, wake), or shows them when given nothing; "claim" takes on an open request or question
    (post_id), "unclaim" lets it go; "wait" waits for an answer to a post (post_id, seconds, at most 300);
    post with seconds > 0 also waits for the answer, to ask someone and use their answer right away;
    "schedule" makes a repeating post (text, to, when like "weekdays 09:00"), "schedules" lists them,
    "unschedule" removes one (post_id = its id); "commands" lists the /commands: a post starting with
    /name (like "/review PR 12") runs that saved workflow and its result is posted back as the answer.

    :param action: read, search, post, answer, edit, retract, remember, close, reopen, pin, unpin, follow, claim, unclaim, wait, schedule, schedules, unschedule or commands.
    :param text: What the post or answer says (at most 2000 characters). @Name tags an agent.
    :param kind: For "post": finding, question, request, decision or heads_up.
    :param to: For "post": who it is for. An agent's name (a question or request starts a chat so it can answer), "you" for the person (goes to their Inbox), on a swarm board a worker's name or "lead". Empty for anyone.
    :param tags: For "post": up to 5 short topic tags, comma separated. For "follow": tags to hear about.
    :param post_id: The post, for answer, close, reopen, pin, unpin, or read to see one post.
    :param filter: For "read": open, unclaimed, pinned, for_me, mine, a kind, or #tag.
    :param board: "agents" (everyone's board), "project" (this project's channel), "swarm" (your swarm's), or a board key. Default: your swarm's board for a swarm worker, the project channel in a project, else "agents".
    :param close: For "answer": also mark the question or request done.
    :param kinds: For "follow": kinds of new posts to hear about, comma separated (questions and requests for you always come).
    :param wake: For "follow": "yes" lets a question or request for you start a chat so you can answer; "no" waits until you next work.
    :param query: For "search": the words to look for. board "*" searches every board.
    :param force: For "post": post a question even though a similar one is already on the board.
    :param thread: For "post", "read" and "schedule": a thread in the channel ("task:<id>" or a few words). Default in a task: its own thread.
    :param seconds: For "wait", or "post" to also wait: how long to wait for an answer (at most 300).
    :param when: For "schedule": when it repeats, like "weekdays 09:00", "daily 18:30" or "mon,thu 9am" (the person's time zone).
    """
    ctx = current_execution_context()
    verb = str(action or '').strip().lower()
    try:
        me = await _me(ctx, parent)
        if verb in ('read', 'list', 'show'):
            key = _board(me, board, posting=False)
            if str(post_id or '').strip():
                post = await service.get_post(me.user_key, post_id)
                lines = [service.post_line(post, text_limit=service.TEXT_MAX).split('\n    ↳')[0]]
                lines += [f'  ↳ {r.get("author_name")}: {r.get("text")}' for r in post.replies]
                return ToolOutcome.success('\n'.join(lines))
            return ToolOutcome.success(await _read(me, key, str(filter or '').strip().lower(),
                                                   thread=service.clean_thread(thread)))
        if verb in ('search', 'find'):
            key = '*' if str(board or '').strip() == '*' else _board(me, board, posting=False)
            words = str(query or text or filter or '').strip()
            rows = await service.search(me.user_key, key, words, limit=READ_LIMIT)
            if not rows:
                return ToolOutcome.success(f'Nothing on the board matches "{words}".')
            return ToolOutcome.success(f'{len(rows)} post(s) match "{words}", best first:\n' + '\n'.join(
                f'- {service.post_line(p)}' for p in rows))
        if verb in ('post', 'add', 'new', 'ask'):
            key = _board(me, board, posting=True)
            in_thread, title = _thread_for(me, key, thread)
            post = await service.create_post(me.user_key, board=key, kind=kind or ('question' if verb == 'ask' else 'finding'),
                                             text=text, tags=tags, to=to, author=me.author,
                                             check_similar=not force, thread=in_thread, thread_title=title)
            if int(seconds or 0) > 0 and post.status == 'open':
                post, new = await service.wait_for(me.user_key, str(post.id), seconds=int(seconds), seen=0)
                return ToolOutcome.success(_posted(post) + '\n' + _waited(post, new), card=_card(post, 'Posted'))
            return ToolOutcome.success(_posted(post), card=_card(post, 'Posted'))
        if verb in ('claim', 'take'):
            post = await service.claim(me.user_key, post_id, me.author)
            return ToolOutcome.success(f'You claimed post {post.id}. Do it, then answer it with what you did '
                                       '(close=true when it is done).', card=_card(post, 'Claimed'))
        if verb in ('unclaim', 'release'):
            post = await service.unclaim(me.user_key, post_id, me.author)
            return ToolOutcome.success(f'Let go of post {post.id}; someone else can take it.')
        if verb in ('wait', 'await'):
            post, new = await service.wait_for(me.user_key, post_id, seconds=int(seconds or 120))
            return ToolOutcome.success(_waited(post, new))
        if verb in ('schedule', 'repeat', 'cron'):
            from cognitrix.board import schedules

            key = _board(me, board, posting=True)
            if service.project_of(key):
                from cognitrix.board import channels

                await channels.check_grant(me.user_key, key, me.author)
            row = await schedules.save_schedule(me.user_key, board=key, text=text, to=to, kind=kind or 'request',
                                                when=when or filter, thread=service.clean_thread(thread),
                                                created_by=me.author.name)
            view = schedules.public(row)
            return ToolOutcome.success(f'Scheduled {row.id}: {view["when"]}, posting "{service._clip(row.text, 80)}"'
                                       + (f' for {row.to_name}' if row.to_name else '') + f'. Next: {view["next_at"]}.')
        if verb in ('schedules', 'crons'):
            from cognitrix.board import schedules

            rows = await schedules.list_schedules(me.user_key)
            if not rows:
                return ToolOutcome.success('No repeating posts.')
            return ToolOutcome.success('Repeating posts:\n' + '\n'.join(
                f'- {r.id}: {schedules.describe(list(r.days), r.at_time)} on {r.board}'
                f'{" for " + r.to_name if r.to_name else ""}: {service._clip(r.text, 100)}{"" if r.enabled else " (off)"}'
                for r in rows))
        if verb in ('unschedule', 'unrepeat'):
            from cognitrix.board import schedules

            await schedules.delete_schedule(me.user_key, post_id)
            return ToolOutcome.success(f'Removed repeating post {post_id}.')
        if verb in ('commands', 'command'):
            from cognitrix.board import commands

            rows = await commands.list_commands(me.user_key)
            if not rows:
                return ToolOutcome.success('No /commands yet. The person adds them on the Board page.')
            return ToolOutcome.success('Post one of these (action "post", text "/name what to do") to run it; '
                                       'its result is posted back as the answer:\n' + '\n'.join(
                                           f'- /{r.name}: {r.description or service._clip(r.prompt, 100)}'
                                           for r in rows))
        if verb in ('answer', 'reply'):
            post = await service.reply(me.user_key, post_id, text, me.author, close=bool(close))
            return ToolOutcome.success(f'Answered post {post.id}' + (' and marked it done.' if close else '.'),
                                       card=_card(post, 'Answered'))
        if verb in ('edit', 'update', 'correct'):
            post = await service.edit_post(me.user_key, post_id, text, me.author)
            return ToolOutcome.success(f'Updated post {post.id}.' + _tagged(post.mentions),
                                       card=_card(post, 'Edited'))
        if verb in ('remember', 'save', 'memorize'):
            post, saved_now = await service.remember(me.user_key, post_id, me.author)
            return ToolOutcome.success(f'Decision {post.id} ' + ('saved to memory.' if saved_now else
                                       'proposed for memory; the person approves it on the Memory page.'))
        if verb in ('retract', 'withdraw', 'unpost'):
            post = await service.retract_post(me.user_key, post_id, me.author)
            return ToolOutcome.success(f'Withdrew post {post.id}. Agents are no longer shown it.')
        if verb in ('close', 'done', 'resolve'):
            post = await service.set_status(me.user_key, post_id, 'done')
            return ToolOutcome.success(f'Marked post {post.id} done.')
        if verb == 'reopen':
            post = await service.set_status(me.user_key, post_id, 'open')
            return ToolOutcome.success(f'Reopened post {post.id}.')
        if verb in ('pin', 'unpin'):
            post = await service.set_pinned(me.user_key, post_id, verb == 'pin')
            return ToolOutcome.success(f'{"Pinned" if verb == "pin" else "Unpinned"} post {post.id}.')
        if verb in ('follow', 'follows', 'subscribe'):
            return ToolOutcome.success(await _follow(me, kinds, tags, wake))
        raise BoardError('bad_action', 'action is read, search, post, answer, edit, retract, remember, close, '
                                       'reopen, pin, unpin, follow, claim, unclaim, wait, schedule, schedules, '
                                       'unschedule or commands.')
    except BoardError as exc:
        return ToolOutcome.failure(exc.code, exc.message)


def _thread_for(me: _Me, key: str, thread: str) -> tuple[str, str]:
    """The thread a post goes in: the one asked for, else this task's own thread on its project channel."""
    if str(thread or '').strip():
        return service.clean_thread(thread), ''
    if me.thread and key == service.project_board(me.project_id):
        return me.thread, me.thread_title
    return '', ''


def _waited(post: Any, new: list) -> str:
    if not new:
        return (f'No answer to post {post.id} yet. Carry on; you will be told when it is answered, or wait '
                'again later (action "wait").')
    lines = [f'{len(new)} new answer(s) to post {post.id} (status: {post.status or "-"}):']
    lines += [f'- {r.get("author_name") or "Someone"}: {r.get("text")}' for r in new]
    return '\n'.join(lines)


def _tagged(mentions: list) -> str:
    names = [m.get('name') for m in mentions or [] if isinstance(m, dict) and m.get('name')]
    return f' Tagged: {", ".join("@" + n for n in names)}; they will be told.' if names else ''


def _card(post: Any, verb: str) -> dict | None:
    """The post as a small card under the tool call in the chat."""
    from cognitrix.cards import CardError, normalize

    label = service.KIND_LABELS.get(post.kind, 'Post')
    where = _where(post.board)
    if verb == 'Answered' and post.replies:
        last = post.replies[-1]
        title = f'Answered a {label.lower()} on {where}'
        body = [f'{post.author_name}: {service._clip(post.text, 300)}', f'{last.get("author_name")}: {last.get("text")}']
    else:
        title = f'{verb} a {label.lower()} on {where}'
        body = [post.text]
    fields = []
    if post.to_name:
        fields.append({'label': 'For', 'value': 'You' if post.to_name == 'you' else post.to_name})
    tagged = [m.get('name') for m in (post.replies[-1].get('mentions') if verb == 'Answered' and post.replies
                                     else post.mentions) or [] if isinstance(m, dict)]
    if tagged:
        fields.append({'label': 'Tagged', 'value': ', '.join('@' + n for n in tagged if n)})
    if post.thread:
        fields.append({'label': 'Thread', 'value': post.thread_title or post.thread})
    if post.claimed_by:
        fields.append({'label': 'Claimed by', 'value': str(post.claimed_by.get('name') or 'Someone')})
    if post.status:
        fields.append({'label': 'Status', 'value': post.status.capitalize(),
                       'tone': 'good' if post.status in ('answered', 'done') else 'neutral'})
    if post.tags:
        fields.append({'label': 'Tags', 'value': ' '.join('#' + t for t in post.tags)})
    spec: dict[str, Any] = {'title': title, 'text': [service._clip(b, 1500) for b in body if b]}
    if fields:
        spec['fields'] = fields
    spec['buttons'] = [{'label': 'Open on the board', 'url': f'/board?board={post.board}&post={post.id}'}]
    try:
        return normalize(spec)
    except CardError:
        return None


def _posted(post: Any) -> str:
    text = f'Posted {service.KIND_LABELS.get(post.kind, post.kind).lower()} {post.id}.'
    if post.to_name == 'you':
        text += ' It is in the person\'s Inbox.'
    elif post.to_name and post.status == 'open':
        text += f' {post.to_name} will get it.'
    if post.status == 'open':
        text += ' You will be told here when it is answered; carry on meanwhile.'
    return text + _tagged(post.mentions)


async def _read(me: _Me, key: str, flt: str, *, thread: str = '') -> str:
    posts = [p for p in await service.posts_on(me.user_key, key) if not p.retracted]
    if thread:
        posts = [p for p in posts if p.thread == thread]
    me_name = me.author.name
    if flt in ('', 'all', 'new', 'latest'):
        rows, what = posts, ''
    elif flt == 'open':
        rows, what = [p for p in posts if p.status == 'open'], 'open '
    elif flt in ('unclaimed', 'free', 'todo'):
        rows, what = [p for p in posts if p.status == 'open' and not p.claimed_by], 'open and unclaimed '
    elif flt == 'pinned':
        rows, what = [p for p in posts if p.pinned], 'pinned '
    elif flt in ('for_me', 'for me', 'to_me', 'mine_to_answer', 'tagged', 'mentions'):
        def me_in(m: Any) -> bool:
            return isinstance(m, dict) and (m.get('name') == me.worker if me.worker else m.get('id') == me.agent_id)

        rows = [p for p in posts if (p.to_name == me.worker if me.worker else p.to_id == me.agent_id)
                or any(me_in(m) for m in p.mentions)
                or any(me_in(m) for r in p.replies for m in r.get('mentions') or [])]
        what = 'for you or tagging you '
    elif flt in ('mine', 'my', 'by_me'):
        rows, what = [p for p in posts if p.author_name == me_name and p.author_id == me.author.id], 'by you '
    elif flt.startswith('#'):
        tag = (service.clean_tags([flt]) or [''])[0]
        rows, what = [p for p in posts if tag in p.tags], f'tagged #{tag} '
    else:
        kind = service.clean_kind(flt)
        rows, what = [p for p in posts if p.kind == kind], f'{service.KIND_LABELS[kind].lower()} '
    return _found(rows[:READ_LIMIT], key, what)


async def _follow(me: _Me, kinds: str, tags: str, wake: str) -> str:
    if me.worker:
        raise BoardError('not_here', 'Swarm workers hear about everything on their swarm board.')
    if not me.agent_id:
        raise BoardError('no_agent', 'Follows belong to a saved agent.')
    flag = str(wake or '').strip().lower()
    wake_value = True if flag in ('yes', 'true', 'on', '1') else False if flag in ('no', 'false', 'off', '0') else None
    if kinds or tags or wake_value is not None:
        row = await service.save_follow(me.user_key, me.agent_id, kinds=kinds or None,
                                        tags=tags if tags else None, wake=wake_value)
        lead = 'Saved. '
    else:
        row = await service.get_follow(me.user_key, me.agent_id)
        lead = ''
    kinds_text = ', '.join(service.KIND_LABELS[k] for k in row.kinds) or 'none'
    tags_text = ', '.join('#' + t for t in row.tags) or 'none'
    return (f'{lead}You hear about new posts of these kinds: {kinds_text}; tags: {tags_text}. '
            f'Questions and requests for you {"start a chat so you can answer" if row.wake else "wait until you next work"}.')
