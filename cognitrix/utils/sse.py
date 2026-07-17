import asyncio
import json
import logging
import time

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from cognitrix.agents import Agent, PromptGenerator
from cognitrix.agents.generators import TaskInstructor
from cognitrix.sessions.access import (
    browser_authorization,
    session_access_allowed,
    visible_sessions,
)
from cognitrix.sessions.base import Session
from cognitrix.tasks.handler import handle_multi_step_task, is_multi_step_task
from cognitrix.tools.utils import ToolExecutionContext

logger = logging.getLogger('cognitrix.log')

# Bound the SSE queues so a slow/stalled EventSource client backpressures the
# producer (via `await queue.put(...)`) instead of buffering chunks unboundedly
# in memory. Both the inbound action queue (POST /agents/chat uses `await put`)
# and the per-turn output queue (_emit uses `await put`) block when full.
_SSE_QUEUE_MAXSIZE = 512
_SSE_RECONNECT_GRACE_SECONDS = 30.0
_QUEUE_TIMEOUT = object()
_CONSUMER_GONE = object()
_TURN_TERMINAL = object()
_NO_REPLAY = object()


class SSEManagerCapacityError(RuntimeError):
    """Raised when no idle SSE manager can be reclaimed safely."""


class SSEManager:
    def __init__(self, agent):
        self.agent = agent
        self.user_key: str | None = None
        self.action_queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
        # Turn output belongs to the browser stream, not to one transient HTTP
        # response. A reconnect can therefore resume draining the same turn.
        self.turn_output_queue: asyncio.Queue | None = None
        # The single terminal event is control state, not data. Keeping it out
        # of the bounded data queue prevents completion from blocking behind a
        # slow/disconnected consumer while preserving bounded streamed output.
        self.turn_terminal_event: asyncio.Event | None = None
        self.turn_terminal: dict | None = None
        self.completed_output_at: float | None = None
        self.active_task: asyncio.Task | None = None
        self.turn_pending = False
        self.stop_requested = False
        # A superseded consumer may already have dequeued one item. Preserve it
        # outside the bounded queue so the new owner receives it first without
        # risking QueueFull or reordering.
        self._replay_queue: asyncio.Queue | None = None
        self._replay_item = _NO_REPLAY
        # Exactly one response generator may consume this manager at a time.
        # Claiming a newer connection wakes and supersedes the older consumer.
        self._consumer_generation = 0
        self._consumer_superseded = asyncio.Event()
        self._consumer_lock = asyncio.Lock()
        self._consumer_claims: set[int] = set()

    def begin_turn(self) -> bool:
        """Reserve this browser stream for one chat turn."""
        if self.turn_pending:
            return False
        self.turn_pending = True
        self.stop_requested = False
        return True

    def stop_current_turn(self) -> bool:
        """Request cancellation for the pending or active chat turn."""
        if not self.turn_pending:
            logger.info("Stop ignored: no pending turn for user=%s", self.user_key)
            return False
        self.stop_requested = True
        logger.info(
            "Stop requested: user=%s active=%s done=%s",
            self.user_key,
            self.active_task is not None,
            self.active_task.done() if self.active_task is not None else None,
        )
        if self.active_task is not None and not self.active_task.done():
            self.active_task.cancel()
        return True

    def finish_turn(self, task: asyncio.Task | None = None) -> None:
        """Release the browser stream after its terminal event is emitted."""
        if task is not None and self.active_task is not task:
            return
        self.active_task = None
        self.turn_pending = False
        self.stop_requested = False

    @property
    def is_idle(self) -> bool:
        """Whether registry pressure may immediately discard this manager."""
        return (
            not self._consumer_claimed
            and not self.turn_pending
            and (self.active_task is None or self.active_task.done())
            and self.turn_output_queue is None
        )

    @property
    def has_expired_output(self) -> bool:
        """Whether undrained completed output has exceeded reconnect grace."""
        return (
            not self._consumer_claimed
            and not self.turn_pending
            and (self.active_task is None or self.active_task.done())
            and self.turn_output_queue is not None
            and self.completed_output_at is not None
            and time.monotonic() - self.completed_output_at
            >= _SSE_RECONNECT_GRACE_SECONDS
        )

    @property
    def _consumer_claimed(self) -> bool:
        return bool(self._consumer_claims)

    def _claim_consumer(self) -> tuple[int, asyncio.Event]:
        self._consumer_superseded.set()
        self._consumer_generation += 1
        self._consumer_superseded = asyncio.Event()
        self._consumer_claims.add(self._consumer_generation)
        return self._consumer_generation, self._consumer_superseded

    def _release_consumer(self, generation: int) -> None:
        self._consumer_claims.discard(generation)

    def _open_turn_output(self) -> tuple[asyncio.Queue, asyncio.Event]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
        terminal_event = asyncio.Event()
        self.turn_output_queue = queue
        self.turn_terminal_event = terminal_event
        self.turn_terminal = None
        self.completed_output_at = None
        return queue, terminal_event

    def _complete_turn_output(
        self,
        task: asyncio.Task,
        queue: asyncio.Queue,
        terminal_event: asyncio.Event,
        terminal: dict,
    ) -> None:
        """Publish one terminal event and release the turn without awaiting."""
        if (
            self.active_task is task
            and self.turn_output_queue is queue
            and self.turn_terminal_event is terminal_event
        ):
            self.turn_terminal = terminal
            self.completed_output_at = time.monotonic()
            terminal_event.set()
        self.finish_turn(task)

    def _take_replay(self, queue: asyncio.Queue):
        if self._replay_queue is not queue:
            return _NO_REPLAY
        item = self._replay_item
        self._replay_queue = None
        self._replay_item = _NO_REPLAY
        return item

    async def _next_queue_item(
        self,
        queue: asyncio.Queue,
        request: Request,
        superseded: asyncio.Event,
        terminal_event: asyncio.Event | None = None,
    ):
        """Wait for one queue item while remaining reconnect/disconnect aware."""
        if superseded.is_set() or await request.is_disconnected():
            return _CONSUMER_GONE

        replay = self._take_replay(queue)
        if replay is not _NO_REPLAY:
            return replay

        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        if terminal_event is not None and terminal_event.is_set():
            return _TURN_TERMINAL

        get_task = asyncio.create_task(queue.get())
        superseded_task = asyncio.create_task(superseded.wait())
        terminal_task = (
            asyncio.create_task(terminal_event.wait())
            if terminal_event is not None else None
        )
        wait_tasks = {get_task, superseded_task}
        if terminal_task is not None:
            wait_tasks.add(terminal_task)
        try:
            done, _ = await asyncio.wait(
                wait_tasks,
                timeout=1.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if superseded_task in done:
                # If an item arrived in the same scheduler tick, preserve it
                # outside the bounded queue for the newer consumer.
                if get_task in done and not get_task.cancelled():
                    self._replay_queue = queue
                    self._replay_item = get_task.result()
                return _CONSUMER_GONE
            if get_task in done:
                return get_task.result()
            if terminal_task is not None and terminal_task in done:
                return _TURN_TERMINAL
            return _QUEUE_TIMEOUT
        finally:
            for task in wait_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*wait_tasks, return_exceptions=True)

    async def _resolve_session(self, session_id: str | None) -> Session | None:
        """Resolve the conversation for a chat action.

        - id given and found: that conversation (agent_id backfilled if empty).
        - id given but gone (deleted/stale): None — the caller must emit an
          error tagged with the requested id and skip the action. Falling back
          to another session would silently persist the turn into the wrong
          conversation.
        - no id: a fresh conversation; the client adopts its id from the
          tagged reply events.
        """
        if not self.user_key:
            return None
        authorization = browser_authorization(self.user_key)
        if session_id:
            session = await Session.get(session_id)
            if session is None or not await session_access_allowed(
                session,
                authorization,
            ):
                return None
            if session is not None and not session.agent_id:
                session.agent_id = self.agent.id
                await session.save()
            return session
        session = Session(agent_id=self.agent.id, user_id=self.user_key)
        await session.save()
        return session

    async def sse_endpoint(self, request: Request):
        async def event_generator(superseded: asyncio.Event):
            while True:
                # A turn may outlive the HTTP response that started it. Drain
                # its manager-owned output before accepting another action.
                turn_queue = self.turn_output_queue
                if turn_queue is not None:
                    terminal_event = self.turn_terminal_event
                    item = await self._next_queue_item(
                        turn_queue, request, superseded, terminal_event
                    )
                    if item is _CONSUMER_GONE:
                        break
                    if item is _QUEUE_TIMEOUT:
                        yield {'event': 'ping', 'data': ''}
                        continue
                    if item is _TURN_TERMINAL:
                        terminal = self.turn_terminal
                        if self.turn_output_queue is turn_queue:
                            self.turn_output_queue = None
                            self.turn_terminal_event = None
                            self.turn_terminal = None
                            self.completed_output_at = None
                        if self._replay_queue is turn_queue:
                            self._replay_queue = None
                            self._replay_item = _NO_REPLAY
                        if terminal is not None:
                            yield {
                                'event': 'message',
                                'data': json.dumps(terminal),
                            }
                        continue
                    yield {'event': 'message', 'data': json.dumps(item)}
                    continue

                if superseded.is_set() or await request.is_disconnected():
                    break

                action = await self._next_queue_item(
                    self.action_queue, request, superseded
                )
                if action is _CONSUMER_GONE:
                    break
                if action is _QUEUE_TIMEOUT:
                    yield {'event': 'ping', 'data': ''}
                    continue

                if action['type'] == 'chat_history':
                    session_id = action['session_id']

                    if action['action'] == 'get':
                        session = await self._resolve_session(session_id)
                        if session is None:
                            yield {'event': 'message', 'data': json.dumps({
                                'type': 'error',
                                'content': 'This conversation is unavailable.',
                                'session_id': session_id,
                            })}
                            continue

                        if not session.agent_id:
                            session.agent_id = self.agent.id
                            await session.save()

                        if session.agent_id == self.agent.id:
                            loaded_agent = self.agent
                        else:
                            loaded_agent: Agent | None = await Agent.get(session.agent_id)

                        if loaded_agent:
                            self.agent = loaded_agent

                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'get'})}

                    elif action['action'] == 'delete':
                        session = await self._resolve_session(session_id)
                        if session is None:
                            yield {'event': 'message', 'data': json.dumps({
                                'type': 'error',
                                'content': 'This conversation is unavailable.',
                                'session_id': session_id,
                            })}
                            continue
                        session.chat = []
                        await session.save()
                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'delete'})}

                elif action['type'] == 'sessions':
                    if action['action'] == 'list':
                        allowed = await visible_sessions(
                            list(await Session.list_sessions()),
                            browser_authorization(self.user_key),
                        )
                        sessions = [sess.json() for sess in allowed]
                        yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'content': sessions, 'action': 'list'})}

                    elif action['action'] == 'get':
                        agent_id = action['agent_id']
                        if agent_id:
                            loaded_agent = await Agent.get(agent_id)
                            if loaded_agent:
                                self.agent = loaded_agent
                                session = await Session.get_by_agent_id(
                                    loaded_agent.id,
                                    self.user_key,
                                )
                                yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'agent_name': self.agent.name, 'content': session.dict(), 'action': 'get'})}

                elif action['type'] == 'generate':
                    default_prompt = action['prompt']
                    prompt = ''
                    name = action.get('name', '')
                    agent = self.agent
                    generate_kind = action.get('action')

                    if generate_kind == 'system_prompt':
                        agent = PromptGenerator(llm=agent.llm)

                        prompt = "Agent Description"
                        if name:
                            prompt += f"""\n\nAgent Name: {name}"""

                        prompt += f"""\n\n{default_prompt}"""

                    elif generate_kind == 'task_instructions':
                        agent = TaskInstructor(llm=agent.llm)

                        prompt = ""
                        if name:
                            prompt += f"""\\nTask Title: {name}"""

                        prompt += f"""\n\nTask Description: {default_prompt}"""

                    async for response in agent.generate(prompt):
                        yield {'event': 'message', 'data': json.dumps({'type': 'generate', 'content': response.current_chunk, 'action': 'system_prompt'})}

                elif action['type'] == 'chat_message':
                    user_prompt = action['content']
                    requested_sid = action.get('session_id')
                    if self.stop_requested:
                        self.finish_turn()
                        yield {'event': 'message', 'data': json.dumps({
                            'type': 'turn_stopped', 'content': '',
                            'session_id': requested_sid,
                        })}
                        continue
                    # Uploaded attachments: images → vision, files → workspace paths.
                    chat_images = action.get('images') or []
                    chat_files = action.get('files') or []
                    chat_attachments = (
                        {'images': chat_images, 'files': chat_files}
                        if (chat_images or chat_files) else None
                    )
                    # Bypass = auto-approve risky tools for this turn (approvals only).
                    chat_bypass = bool(action.get('bypass_permissions'))

                    # Resolve the conversation up front (own try so a DB error
                    # can't propagate out of this generator and kill the SSE
                    # stream). Every reply event is tagged with session_id so
                    # the client can route/drop it against its active thread.
                    try:
                        session = await self._resolve_session(requested_sid)
                    except Exception:
                        logger.exception("Failed to resolve chat session")
                        self.finish_turn()
                        yield {'event': 'message', 'data': json.dumps({'type': 'error', 'content': 'Could not load the conversation. Please try again.', 'session_id': requested_sid})}
                        continue
                    if session is None:
                        # Stale id (deleted elsewhere). Tag with the REQUESTED id
                        # so the client's filter lets it through; never `return`
                        # here — that would end the stream for this user+agent.
                        self.finish_turn()
                        yield {'event': 'message', 'data': json.dumps({'type': 'error', 'content': 'This conversation no longer exists — start a new one.', 'session_id': requested_sid})}
                        continue

                    if self.stop_requested:
                        self.finish_turn()
                        yield {'event': 'message', 'data': json.dumps({
                            'type': 'turn_stopped', 'content': '',
                            'session_id': session.id,
                        })}
                        continue

                    # Check for multi-step tasks
                    if is_multi_step_task(user_prompt):
                        # Bridge through a queue so the run link reaches the
                        # client immediately — the run itself blocks for its
                        # whole duration, and the user can watch it live on
                        # the task page meanwhile.
                        ms_queue, ms_terminal_event = self._open_turn_output()
                        await ms_queue.put({
                            'type': 'status',
                            'content': 'Planning multi-step task...',
                            'session_id': session.id,
                        })

                        async def _notify_task(task_id, _q=ms_queue, _sid=session.id):
                            await _q.put({
                                'type': 'status',
                                'content': f'Task created — watch it run live on the task page (/tasks/{task_id}).',
                                'task_id': task_id,
                                'session_id': _sid,
                            })

                        # ponytail: multi-step forwards file paths only (no vision) in v1.
                        ms_prompt = user_prompt
                        if chat_files:
                            _fpaths = '\n'.join(f.get('path', '') for f in chat_files if f.get('path'))
                            if _fpaths:
                                ms_prompt = f"{user_prompt}\n\n[User uploaded files, readable with your file tools:]\n{_fpaths}"

                        async def _run_multistep(
                            _prompt=ms_prompt,
                            _sess=session,
                            _q=ms_queue,
                            _terminal_event=ms_terminal_event,
                        ):
                            terminal = {
                                'type': 'error',
                                'content': 'Multi-step task failed unexpectedly.',
                                'session_id': _sess.id,
                            }
                            try:
                                result = await handle_multi_step_task(
                                    _prompt,
                                    self.agent,
                                    _sess,
                                    self.agent.llm,
                                    stream=False,
                                    interface='web',
                                    on_task_created=_notify_task,
                                )
                                terminal = {
                                    'type': 'multistep_result',
                                    'content': result,
                                    'session_id': _sess.id,
                                }
                            except asyncio.CancelledError:
                                terminal = {
                                    'type': 'turn_stopped',
                                    'content': '',
                                    'session_id': _sess.id,
                                }
                            except Exception as e:
                                logger.exception("Multi-step chat task failed")
                                terminal = {
                                    'type': 'error',
                                    'content': f'Multi-step task failed: {str(e)}',
                                    'session_id': _sess.id,
                                }
                            finally:
                                if self.stop_requested:
                                    terminal = {
                                        'type': 'turn_stopped',
                                        'content': '',
                                        'session_id': _sess.id,
                                    }
                                self._complete_turn_output(
                                    asyncio.current_task(),
                                    _q,
                                    _terminal_event,
                                    terminal,
                                )

                        ms_task = asyncio.create_task(_run_multistep())
                        self.active_task = ms_task
                        if self.stop_requested:
                            ms_task.cancel()
                    else:
                        # Route through the full session loop so the web path gets
                        # tools + safety gating + history + persistence (previously it
                        # called agent.generate() directly, bypassing all of that).
                        # Bridge the session's callback-based output to this SSE
                        # generator through a queue.
                        out_queue, out_terminal_event = self._open_turn_output()

                        async def _emit(payload, _q=out_queue, _sid=session.id):
                            if isinstance(payload, dict):
                                payload = {**payload, 'session_id': _sid}
                            await _q.put(payload)

                        async def _run(
                            _prompt=user_prompt,
                            _sess=session,
                            _q=out_queue,
                            _terminal_event=out_terminal_event,
                            _att=chat_attachments,
                            _bypass=chat_bypass,
                        ):
                            # Bind the turn's approval context (emit channel, owner,
                            # bypass) so a risky tool can prompt the browser.
                            from cognitrix.safety.approval_gate import web_turn_ctx
                            token = web_turn_ctx.set({
                                'emit': _emit,
                                'session_id': _sess.id,
                                'bypass': _bypass,
                                'user_key': self.user_key,
                            })
                            terminal_type = 'turn_complete'
                            try:
                                await _sess(
                                    _prompt, self.agent,
                                    interface='web', stream=True, output=_emit,
                                    wsquery={'type': 'generate', 'action': 'chat_message'},
                                    attachments=_att,
                                    tool_context=ToolExecutionContext(user_id=self.user_key),
                                )
                            except asyncio.CancelledError:
                                terminal_type = 'turn_stopped'
                                logger.info("Chat turn cancelled for session=%s", _sess.id)
                            except Exception as e:
                                logger.exception("SSE session turn failed")
                                await _q.put({
                                    'type': 'error',
                                    'content': str(e),
                                    'session_id': _sess.id,
                                })
                            finally:
                                try:
                                    web_turn_ctx.reset(token)
                                finally:
                                    if self.stop_requested:
                                        terminal_type = 'turn_stopped'
                                    # Completion is control state and must not
                                    # block behind the bounded streamed data.
                                    self._complete_turn_output(
                                        asyncio.current_task(),
                                        _q,
                                        _terminal_event,
                                        {
                                            'type': terminal_type,
                                            'content': '',
                                            'session_id': _sess.id,
                                        },
                                    )
                                    logger.info(
                                        "Queued %s for session=%s",
                                        terminal_type,
                                        _sess.id,
                                    )

                        run_task = asyncio.create_task(_run())
                        self.active_task = run_task
                        if self.stop_requested:
                            run_task.cancel()

        async def owned_event_generator():
            generation, superseded = self._claim_consumer()
            try:
                async with self._consumer_lock:
                    # Only an actually iterated response claims ownership. A
                    # later iterator supersedes this one before it can dequeue.
                    if generation != self._consumer_generation:
                        return
                    async for event in event_generator(superseded):
                        if superseded.is_set():
                            break
                        yield event
            finally:
                # Covers cancellation while waiting to acquire the lock too.
                self._release_consumer(generation)

        return EventSourceResponse(owned_event_generator())


# Per-browser SSE managers. Sharing one action queue across tabs/reconnects for
# the same user+agent lets a stale stream dequeue a chat action and receive the
# entire private turn output. The browser's stable stream id makes the POST
# rendezvous with the exact GET that submitted it.
_SSE_MANAGERS: dict[tuple[str, str, str], SSEManager] = {}
_MAX_SSE_MANAGERS = 512
_MAX_SSE_MANAGERS_PER_USER = 16


def get_sse_manager(user_id: str, agent_id: str, agent,
                    stream_id: str | None = None, *,
                    create: bool = True) -> SSEManager | None:
    """Look up the SSE manager, optionally creating it for an SSE GET.

    Chat and stop requests use ``create=False`` so a guessed stream id cannot
    allocate state or enqueue work without an established event stream.
    """
    user_key = str(user_id)
    key = (user_key, str(agent_id), str(stream_id or 'default'))
    mgr = _SSE_MANAGERS.get(key)
    if mgr is None:
        if not create:
            return None

        def evict_idle(*, same_user: bool = False) -> bool:
            candidates = [
                (candidate_key, candidate)
                for candidate_key, candidate in list(_SSE_MANAGERS.items())
                if not same_user or candidate_key[0] == user_key
            ]
            # Prefer managers with no buffered output. Completed output is
            # retained long enough for normal EventSource reconnects, then may
            # be reclaimed under pressure so abandoned streams cannot leak.
            for predicate in (
                lambda candidate: candidate.is_idle,
                lambda candidate: candidate.has_expired_output,
            ):
                for candidate_key, candidate in candidates:
                    if predicate(candidate):
                        _SSE_MANAGERS.pop(candidate_key, None)
                        return True
            return False

        user_manager_count = sum(
            1 for candidate_key in _SSE_MANAGERS if candidate_key[0] == user_key
        )
        if user_manager_count >= _MAX_SSE_MANAGERS_PER_USER:
            if not evict_idle(same_user=True):
                raise SSEManagerCapacityError(
                    "Too many active browser streams for this user"
                )
        if len(_SSE_MANAGERS) >= _MAX_SSE_MANAGERS:
            if not evict_idle():
                raise SSEManagerCapacityError(
                    "The server has reached its active browser stream capacity"
                )
        mgr = SSEManager(agent)
        _SSE_MANAGERS[key] = mgr
    else:
        # Refresh the bound agent (it may have been edited/reloaded).
        mgr.agent = agent
    mgr.user_key = user_key
    return mgr
