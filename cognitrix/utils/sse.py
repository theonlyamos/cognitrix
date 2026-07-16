import asyncio
import json
import logging
import time

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from cognitrix.agents import Agent, PromptGenerator
from cognitrix.agents.generators import TaskInstructor
from cognitrix.media import MediaOwnership, media_assets
from cognitrix.media.staging import (
    PromotedAttachments,
    promote_staged_attachments,
    rollback_promoted_attachments,
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
_ATTACHMENT_UNAVAILABLE = (
    'Attachments or the selected image are unavailable. Please try again.'
)


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
        self._active_task_started = True
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
        if (
            self.active_task is not None
            and not self.active_task.done()
            and self._active_task_started
        ):
            self.active_task.cancel()
        return True

    def finish_turn(self, task: asyncio.Task | None = None) -> None:
        """Release the browser stream after its terminal event is emitted."""
        if task is not None and self.active_task is not task:
            return
        self.active_task = None
        self._active_task_started = True
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
        if session_id:
            session = await Session.get(session_id)
            if session is not None and not session.agent_id:
                session.agent_id = self.agent.id
                await session.save()
            return session
        session = Session(agent_id=self.agent.id)
        await session.save()
        return session

    async def _process_chat_action(
        self,
        action: dict,
        output_queue: asyncio.Queue,
        terminal_event: asyncio.Event,
    ) -> None:
        """Own a dequeued chat action independently of one HTTP consumer."""
        # This assignment happens before the coroutine's first await. Stops
        # recorded before it starts therefore do not cancel away its cleanup.
        self._active_task_started = True
        requested_sid = action.get('session_id')
        staged = action.get('staged_attachments')
        selected_id = action.get('edit_source_artifact_id')
        has_media = staged is not None or bool(selected_id)
        session = None
        ownership: MediaOwnership | None = None
        promoted: PromotedAttachments | None = None
        handed_off = False
        terminal = {
            'type': 'turn_complete',
            'content': '',
            'session_id': requested_sid,
        }

        async def emit(payload):
            if isinstance(payload, dict):
                payload = {
                    **payload,
                    'session_id': session.id if session is not None else requested_sid,
                }
            await output_queue.put(payload)

        try:
            if self.stop_requested:
                raise asyncio.CancelledError

            try:
                session = await self._resolve_session(requested_sid)
            except Exception:
                logger.exception('Failed to resolve chat session')
                await emit({
                    'type': 'error',
                    'content': (
                        _ATTACHMENT_UNAVAILABLE if has_media else
                        'Could not load the conversation. Please try again.'
                    ),
                })
                return
            if self.stop_requested:
                raise asyncio.CancelledError
            if session is None:
                await emit({
                    'type': 'error',
                    'content': (
                        _ATTACHMENT_UNAVAILABLE if has_media else
                        'This conversation no longer exists â€” start a new one.'
                    ),
                })
                return
            terminal['session_id'] = session.id
            session_agent_id = getattr(session, 'agent_id', None)
            if session_agent_id and str(session_agent_id) != str(self.agent.id):
                logger.warning(
                    'Rejected chat session with wrong agent session=%s', session.id
                )
                await emit({
                    'type': 'error',
                    'content': (
                        _ATTACHMENT_UNAVAILABLE if has_media else
                        'Could not load the conversation. Please try again.'
                    ),
                })
                return

            ownership = MediaOwnership(
                session_id=str(session.id),
                user_id=self.user_key,
                agent_id=str(self.agent.id),
            )
            selected_ref = None
            if selected_id:
                selected_ref = await media_assets.resolve_ref(
                    str(selected_id), ownership
                )
                if self.stop_requested:
                    raise asyncio.CancelledError

            if staged is not None:
                promoted = await promote_staged_attachments(staged, ownership)
                if self.stop_requested:
                    raise asyncio.CancelledError

            image_refs = list(promoted.image_refs) if promoted else []
            documents = list(promoted.document_paths) if promoted else []
            attachments = None
            if image_refs or documents or selected_ref is not None:
                attachments = {
                    'images': [item.model_dump() for item in image_refs],
                    'files': documents,
                    'image_selection': (
                        selected_ref.model_dump() if selected_ref is not None else None
                    ),
                }
            if promoted is not None:
                await emit({
                    'type': 'attachments_ingested',
                    'artifacts': [item.model_dump() for item in image_refs],
                    'document_count': len(documents),
                })
                if self.stop_requested:
                    raise asyncio.CancelledError

            user_prompt = action['content']
            bypass = bool(action.get('bypass_permissions'))
            # Attachment-bearing prompts use Session so their immutable refs
            # are consumed and persisted instead of being orphaned by a task.
            if is_multi_step_task(user_prompt) and attachments is None:
                await emit({
                    'type': 'status',
                    'content': 'Planning multi-step task...',
                })

                async def notify_task(task_id):
                    await emit({
                        'type': 'status',
                        'content': (
                            'Task created â€” watch it run live on the task page '
                            f'(/tasks/{task_id}).'
                        ),
                        'task_id': task_id,
                    })

                handed_off = True
                result = await handle_multi_step_task(
                    user_prompt,
                    self.agent,
                    session,
                    self.agent.llm,
                    stream=False,
                    interface='web',
                    on_task_created=notify_task,
                )
                terminal = {
                    'type': 'multistep_result',
                    'content': result,
                    'session_id': session.id,
                }
            else:
                from cognitrix.safety.approval_gate import web_turn_ctx

                token = web_turn_ctx.set({
                    'emit': emit,
                    'session_id': session.id,
                    'bypass': bypass,
                    'user_key': self.user_key,
                })
                try:
                    handed_off = True
                    await session(
                        user_prompt,
                        self.agent,
                        interface='web',
                        stream=True,
                        output=emit,
                        wsquery={'type': 'generate', 'action': 'chat_message'},
                        attachments=attachments,
                        tool_context=ToolExecutionContext(user_id=self.user_key),
                    )
                finally:
                    web_turn_ctx.reset(token)
        except asyncio.CancelledError:
            terminal = {
                'type': 'turn_stopped',
                'content': '',
                'session_id': session.id if session is not None else requested_sid,
            }
        except Exception as exc:
            logger.exception('SSE chat action failed')
            await emit({
                'type': 'error',
                'content': _ATTACHMENT_UNAVAILABLE if has_media else str(exc),
            })
        finally:
            if promoted is not None and not handed_off and ownership is not None:
                try:
                    await rollback_promoted_attachments(promoted, ownership)
                except BaseException:
                    logger.exception('Failed to roll back unscheduled attachments')
            if staged is not None:
                try:
                    await staged.cleanup()
                except BaseException:
                    logger.exception('Failed to clean staged chat attachments')
            if self.stop_requested:
                terminal = {
                    'type': 'turn_stopped',
                    'content': '',
                    'session_id': terminal.get('session_id'),
                }
            self._complete_turn_output(
                asyncio.current_task(),
                output_queue,
                terminal_event,
                terminal,
            )

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
                        session = await Session.load(session_id)

                        if not session.agent_id:
                            session.agent_id = self.agent.id
                            await session.save()

                        if session.agent_id == self.agent.id:
                            loaded_agent = self.agent
                        else:
                            loaded_agent: Agent | None = Agent.get(session.agent_id)

                        if loaded_agent:
                            self.agent = loaded_agent

                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'get'})}

                    elif action['action'] == 'delete':
                        session = await Session.load(session_id)
                        session.chat = []
                        await session.save()
                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'delete'})}

                elif action['type'] == 'sessions':
                    if action['action'] == 'list':
                        sessions = [sess.json() for sess in await Session.list_sessions()]
                        yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'content': sessions, 'action': 'list'})}

                    elif action['action'] == 'get':
                        agent_id = action['agent_id']
                        if agent_id:
                            loaded_agent = Agent.get(agent_id)
                            if loaded_agent:
                                self.agent = loaded_agent
                                session = await Session.get_by_agent_id(loaded_agent.id)
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
                    out_queue, out_terminal_event = self._open_turn_output()
                    self._active_task_started = False
                    run_task = asyncio.create_task(
                        self._process_chat_action(
                            action, out_queue, out_terminal_event
                        )
                    )
                    self.active_task = run_task
                    continue

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
