import asyncio
import json
import logging
import time

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from cognitrix.agents import Agent, PromptGenerator
from cognitrix.agents.generators import TaskInstructor
from cognitrix.artifacts import delete_owned_session_artifacts
from cognitrix.media import MediaOwnership, MediaValidationError, media_assets
from cognitrix.media.document_capabilities import (
    load_turn_document_capabilities,
)
from cognitrix.media.documents import document_assets
from cognitrix.media.staging import (
    AttachmentCleanupError,
    PromotedAttachments,
    cleanup_staged_attachments,
    promote_staged_attachments,
    release_promoted_attachment_reservation,
    rollback_promoted_attachments,
)
from cognitrix.sessions.base import Session
from cognitrix.session_ownership import (
    OwnershipConflict,
    OwnershipNotFound,
    OwnershipState,
    session_ownerships,
)
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


async def _settle_mutation(operation):
    """Join one started database mutation before cancellation escapes."""
    mutation = asyncio.create_task(operation)
    try:
        return await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        mutation.result()
        raise cancelled


async def _settle_background_task(task: asyncio.Task) -> BaseException | None:
    """Join a transferred durability task and return its terminal error."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    if task.cancelled():
        return asyncio.CancelledError()
    try:
        task.result()
    except BaseException as error:
        return error
    return None


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
        self._fallback_tasks: set[asyncio.Task] = set()
        self.last_action_error: BaseException | None = None
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
        user_id = str(self.user_key or '').strip()
        agent_id = str(getattr(self.agent, 'id', '') or '').strip()
        if not user_id or not agent_id:
            raise OwnershipNotFound()
        if session_id:
            binding = await session_ownerships.require_active_owned(
                str(session_id), user_id, agent_id
            )
            session = await Session.get(session_id)
            if (
                session is None
                or str(getattr(session, 'agent_id', '') or '')
                != binding.agent_id
            ):
                raise OwnershipNotFound()
            return session

        session = Session(agent_id=agent_id)
        created_id: str | None = None
        try:
            await _settle_mutation(session.save())
            created_id = str(session.id)
            await session_ownerships.claim_new(created_id, user_id, agent_id)
            return session
        except BaseException:
            if created_id is not None:
                async def compensate() -> None:
                    await Session.delete_many({'id': created_id})
                    await session_ownerships.discard_fresh_claim(
                        created_id, user_id, agent_id
                    )

                await _settle_mutation(compensate())
            raise

    async def _load_owned_session(self, session_id: str):
        user_id = str(self.user_key or '').strip()
        if not user_id:
            raise OwnershipNotFound()
        binding = await session_ownerships.require_active_owned(
            str(session_id), user_id
        )
        session = await Session.get(str(session_id))
        if (
            session is None
            or str(getattr(session, 'agent_id', '') or '') != binding.agent_id
        ):
            raise OwnershipNotFound()
        return binding, session

    async def _clear_owned_history(self, session_id: str):
        user_id = str(self.user_key or '').strip()
        if not user_id:
            raise OwnershipNotFound()
        binding = await session_ownerships.require_owned(str(session_id), user_id)
        if binding.state == OwnershipState.ACTIVE:
            token = await session_ownerships.begin_clear(
                binding.session_id,
                binding.user_id,
                binding.agent_id,
            )
        elif binding.state == OwnershipState.CLEARING:
            token = await session_ownerships.resume_lifecycle(
                binding.session_id,
                binding.user_id,
                binding.agent_id,
                OwnershipState.CLEARING,
            )
        else:
            raise OwnershipConflict('Session is in a different lifecycle state')
        session = await Session.get(binding.session_id)
        if (
            session is None
            or str(getattr(session, 'agent_id', '') or '') != binding.agent_id
        ):
            raise OwnershipNotFound()
        session.chat = []
        await _settle_mutation(session.save())
        await delete_owned_session_artifacts(
            session_id=token.session_id,
            user_id=token.user_id,
            agent_id=token.agent_id,
            generation=token.generation,
        )
        await session_ownerships.finish_clear(token)
        return session

    def _start_chat_action(
        self,
        action: dict,
        output_queue: asyncio.Queue,
        terminal_event: asyncio.Event,
    ) -> asyncio.Task:
        """Claim staging and launch a supervised manager-owned action task."""
        claimed_action = action
        staged = action.get('staged_attachments')
        claim_now = getattr(staged, 'claim_now', None)
        if callable(claim_now):
            try:
                claim_now()
                claimed_action = {
                    **action,
                    '_staging_cleanup_owned': True,
                }
            except BaseException:
                logger.exception('Failed to claim staged chat attachments')
                # A failed claim may mean another consumer already owns this
                # batch. Never let the losing action delete another owner's
                # staging directory.
                claimed_action = {
                    **action,
                    '_staging_claim_failed': True,
                    '_staging_cleanup_owned': False,
                }

        started = {'value': False}
        self._active_task_started = False
        task = asyncio.create_task(
            self._process_chat_action(
                claimed_action,
                output_queue,
                terminal_event,
                _started=started,
            )
        )
        self.active_task = task

        def done(completed: asyncio.Task) -> None:
            if not started['value']:
                fallback = asyncio.create_task(
                    self._finalize_unstarted_chat_action(
                        completed,
                        claimed_action,
                        output_queue,
                        terminal_event,
                    )
                )
                self._fallback_tasks.add(fallback)

                def fallback_done(finished: asyncio.Task) -> None:
                    self._fallback_tasks.discard(finished)
                    try:
                        error = finished.exception()
                    except asyncio.CancelledError as exc:
                        error = exc
                    if error is not None:
                        self.last_action_error = error
                        logger.error('Pre-start attachment cleanup task failed')

                fallback.add_done_callback(fallback_done)
                return
            try:
                error = completed.exception()
            except asyncio.CancelledError as exc:
                error = exc
            if error is not None:
                self.last_action_error = error
                logger.error('Manager-owned chat action failed internally')

        task.add_done_callback(done)
        return task

    async def _finalize_unstarted_chat_action(
        self,
        task: asyncio.Task,
        action: dict,
        output_queue: asyncio.Queue,
        terminal_event: asyncio.Event,
    ) -> None:
        cleanup_error: BaseException | None = None
        staged = action.get('staged_attachments')
        cleanup_owned = action.get('_staging_cleanup_owned', staged is not None)
        if staged is not None and cleanup_owned:
            try:
                await cleanup_staged_attachments(staged)
            except BaseException as exc:
                cleanup_error = exc
                logger.exception('Pre-start staged attachment cleanup failed')
        terminal = {
            'type': 'turn_stopped' if cleanup_error is None else 'error',
            'content': '' if cleanup_error is None else _ATTACHMENT_UNAVAILABLE,
            'session_id': action.get('session_id'),
        }
        self._complete_turn_output(task, output_queue, terminal_event, terminal)
        if cleanup_error is not None:
            raise AttachmentCleanupError([cleanup_error]) from cleanup_error

    async def _process_chat_action(
        self,
        action: dict,
        output_queue: asyncio.Queue,
        terminal_event: asyncio.Event,
        *,
        _started: dict[str, bool] | None = None,
    ) -> None:
        """Own a dequeued chat action independently of one HTTP consumer."""
        # This assignment happens before the coroutine's first await. Stops
        # recorded before it starts therefore do not cancel away its cleanup.
        if _started is not None:
            _started['value'] = True
        self._active_task_started = True
        requested_sid = action.get('session_id')
        staged = action.get('staged_attachments')
        cleanup_owned = action.get('_staging_cleanup_owned', staged is not None)
        selected_id = action.get('edit_source_artifact_id')
        selected_image_index = action.get('edit_source_image_index')
        adopted_document_ids = action.get('document_ids') or ()
        has_media = (
            staged is not None
            or bool(selected_id)
            or selected_image_index is not None
            or bool(adopted_document_ids)
        )
        session = None
        ownership: MediaOwnership | None = None
        promoted: PromotedAttachments | None = None
        adopted = False
        adoption_task: asyncio.Task | None = None
        ingestion_emitted = False
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

        def mark_adopted() -> None:
            nonlocal adopted, adoption_task, ingestion_emitted
            if adopted:
                return
            adopted = True
            if promoted is not None:
                # Session invokes this only after its history save succeeds;
                # durable adoption ends the rollback obligation exactly once.
                release_promoted_attachment_reservation(promoted)
                document_ids = [
                    str(item['id'])
                    for item in promoted.document_paths
                    if item.get('id')
                ]
                if document_ids:
                    if ownership is None:
                        raise RuntimeError('Document adoption authority is missing')
                    adoption_task = asyncio.create_task(
                        document_assets.mark_documents_adopted(
                            document_ids,
                            ownership,
                        )
                    )
            if promoted is not None and not ingestion_emitted:
                output_queue.put_nowait({
                    'type': 'attachments_ingested',
                    'artifacts': [
                        item.model_dump() for item in promoted.image_refs
                    ],
                    'document_count': len(promoted.document_paths),
                    'session_id': session.id,
                })
                ingestion_emitted = True

        try:
            if self.stop_requested:
                raise asyncio.CancelledError
            if action.get('_staging_claim_failed'):
                raise MediaValidationError('Staged attachments are unavailable')

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
            if selected_id and selected_image_index is not None:
                raise MediaValidationError('Select exactly one image edit source')
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
            if selected_image_index is not None:
                if (
                    isinstance(selected_image_index, bool)
                    or not isinstance(selected_image_index, int)
                    or selected_image_index < 0
                    or selected_image_index >= len(image_refs)
                ):
                    raise MediaValidationError('Selected upload image is unavailable')
                selected_ref = image_refs[selected_image_index]
            fresh_document_ids = tuple(
                str(item['id']) for item in documents if item.get('id')
            )
            document_capabilities = await load_turn_document_capabilities(
                ownership,
                fresh_document_ids=fresh_document_ids,
                adopted_document_ids=tuple(adopted_document_ids),
            )
            attachments = None
            if image_refs or documents or selected_ref is not None:
                attachments = {
                    'images': [item.model_dump() for item in image_refs],
                    'files': documents,
                    'image_selection': (
                        selected_ref.model_dump() if selected_ref is not None else None
                    ),
                    '_on_adopted': mark_adopted,
                }
            user_prompt = action['content']
            bypass = bool(action.get('bypass_permissions'))
            # Attachment-bearing prompts use Session so their immutable refs
            # are consumed and persisted instead of being orphaned by a task.
            if (
                is_multi_step_task(user_prompt)
                and attachments is None
                and not document_capabilities
            ):
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
                    await session(
                        user_prompt,
                        self.agent,
                        interface='web',
                        stream=True,
                        output=emit,
                        wsquery={'type': 'generate', 'action': 'chat_message'},
                        attachments=attachments,
                        tool_context=ToolExecutionContext(
                            session_id=str(session.id),
                            user_id=self.user_key,
                            agent_id=str(self.agent.id),
                            document_capabilities=document_capabilities,
                            selected_image_artifact_id=(
                                selected_ref.id if selected_ref is not None else None
                            ),
                        ),
                    )
                    if promoted is not None and (
                        promoted.image_refs or promoted.document_paths
                    ) and not adopted:
                        raise MediaValidationError(
                            'Session did not durably adopt promoted attachments'
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
            cleanup_errors: list[BaseException] = []
            adoption_error: BaseException | None = None
            if adoption_task is not None:
                adoption_error = await _settle_background_task(adoption_task)
                if adoption_error is not None:
                    logger.error(
                        'Document adoption commit failed after Session save',
                        exc_info=(
                            type(adoption_error),
                            adoption_error,
                            adoption_error.__traceback__,
                        ),
                    )
            if promoted is not None and not adopted and ownership is not None:
                try:
                    await rollback_promoted_attachments(promoted, ownership)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    logger.exception('Failed to roll back unscheduled attachments')
            if staged is not None and cleanup_owned:
                try:
                    await cleanup_staged_attachments(staged)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    logger.exception('Failed to clean staged chat attachments')
            if cleanup_errors or adoption_error is not None:
                terminal = {
                    'type': 'error',
                    'content': _ATTACHMENT_UNAVAILABLE,
                    'session_id': terminal.get('session_id'),
                }
            elif self.stop_requested:
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
            if cleanup_errors:
                raise AttachmentCleanupError(cleanup_errors)
            if adoption_error is not None:
                raise adoption_error

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
                        await delete_session_artifacts(str(session.id))
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
                    self._start_chat_action(
                        action, out_queue, out_terminal_event
                    )
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
