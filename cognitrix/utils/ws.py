"""WebSocket transport with per-connection Session ownership authority."""

import asyncio
import json
import logging
from contextvars import ContextVar

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from cognitrix.agents import Agent, PromptGenerator
from cognitrix.prompts.generator import (
    agent_generator,
    task_details_generator,
    team_details_generator,
)
from cognitrix.session_ownership import (
    LifecycleToken,
    OwnershipConflict,
    OwnershipNotFound,
    OwnershipState,
    begin_clear,
    begin_delete,
    claim_new,
    discard_fresh_claim,
    finish_clear,
    finish_delete,
    owned_session_ids,
    require_active_owned,
    require_owned,
    resume_lifecycle,
)
from cognitrix.sessions.base import Session
from cognitrix.tasks.handler import handle_multi_step_task
from cognitrix.tools.base import Tool
from cognitrix.tools.utils import (
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)

logger = logging.getLogger('cognitrix.log')
_active_websocket: ContextVar[WebSocket | None] = ContextVar(
    'active_websocket', default=None,
)


async def _settle_mutation(operation):
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


async def cleanup_owned_session_resources(
    *,
    session_id: str,
    user_id: str,
    agent_id: str,
    generation: int,
) -> None:
    """Delegate to the shared exact-authority cleanup seam."""
    from cognitrix.api.routes.sessions import cleanup_owned_session_resources as cleanup

    await cleanup(
        session_id=session_id,
        user_id=user_id,
        agent_id=agent_id,
        generation=generation,
    )


async def _settle_compensation(operation) -> None:
    task = asyncio.create_task(operation)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


async def get_or_create_owned_session(
    user_key: str,
    agent_id: str,
):
    """Return one active owned session for this principal+agent, or claim new."""
    user_key = str(user_key or '').strip()
    agent_id = str(agent_id or '').strip()
    if not user_key or not agent_id:
        raise OwnershipNotFound()
    for session_id in await owned_session_ids(user_key, agent_id=agent_id):
        try:
            binding = await require_active_owned(session_id, user_key, agent_id)
        except (OwnershipNotFound, OwnershipConflict):
            continue
        session = await Session.get(session_id)
        if session is not None and str(session.agent_id or '') == binding.agent_id:
            return binding, session

    session = Session(agent_id=agent_id)
    session_id: str | None = None
    try:
        await _settle_mutation(session.save())
        session_id = str(session.id)
        binding = await claim_new(session_id, user_key, agent_id)
    except BaseException:
        session_id = session_id or (
            str(session.id) if session.id is not None else None
        )
        if session_id is not None:
            async def compensate() -> None:
                await Session.delete_many({'id': session_id})
                await discard_fresh_claim(session_id, user_key, agent_id)

            await _settle_compensation(compensate())
        raise
    return binding, session


async def _load_owned_session(
    session_id: str,
    user_key: str,
    *,
    agent_id: str | None = None,
):
    # Ownership is resolved before Session.get to deny foreign/unbound ids
    # without touching their Session row.
    binding = await require_active_owned(session_id, user_key, agent_id)
    session = await Session.get(session_id)
    if session is None or str(session.agent_id or '') != binding.agent_id:
        raise OwnershipNotFound()
    return binding, session


async def _owned_sessions(user_key: str) -> list[Session]:
    sessions = []
    for session_id in await owned_session_ids(user_key):
        try:
            binding = await require_active_owned(session_id, user_key)
        except (OwnershipNotFound, OwnershipConflict):
            continue
        session = await Session.get(session_id)
        if session is not None and str(session.agent_id or '') == binding.agent_id:
            sessions.append(session)
    return sessions


async def _begin_lifecycle(
    session_id: str,
    user_key: str,
    operation: str,
) -> tuple[LifecycleToken, Session | None, bool]:
    binding = await require_owned(session_id, user_key)
    desired = (
        OwnershipState.CLEARING if operation == 'clear'
        else OwnershipState.DELETING
    )
    if binding.state == OwnershipState.ACTIVE:
        session = await Session.get(session_id)
        if session is None or str(session.agent_id or '') != binding.agent_id:
            raise OwnershipNotFound()
        if operation == 'clear':
            token = await begin_clear(
                session_id, binding.user_id, binding.agent_id,
            )
        else:
            token = await begin_delete(
                session_id, binding.user_id, binding.agent_id,
            )
        return token, session, False
    if binding.state != desired:
        raise OwnershipConflict('Session is in a different lifecycle state')
    token = await resume_lifecycle(
        session_id,
        binding.user_id,
        binding.agent_id,
        desired,
    )
    session = await Session.get(session_id)
    if operation == 'clear' and (
        session is None or str(session.agent_id or '') != binding.agent_id
    ):
        raise OwnershipConflict('Clearing session row is unavailable')
    return token, session, True


async def _cleanup_token(token: LifecycleToken) -> None:
    await cleanup_owned_session_resources(
        session_id=token.session_id,
        user_id=token.user_id,
        agent_id=token.agent_id,
        generation=token.generation,
    )


async def _clear_owned_session(session_id: str, user_key: str) -> tuple[LifecycleToken, Session]:
    token, session, _ = await _begin_lifecycle(session_id, user_key, 'clear')
    assert session is not None
    session.chat = []
    await session.save()
    # Any failure from here leaves CLEARING for exact retry/recovery.
    await _cleanup_token(token)
    await finish_clear(token)
    return token, session


async def _delete_owned_session(session_id: str, user_key: str) -> LifecycleToken:
    token, _, resumed = await _begin_lifecycle(session_id, user_key, 'delete')
    # Any failure from here leaves DELETING for exact retry/recovery.
    await _cleanup_token(token)
    deleted = await Session.delete_many({'id': session_id})
    if deleted != 1 and not (resumed and deleted == 0):
        raise OwnershipConflict('Session changed concurrently')
    await finish_delete(token)
    return token


async def _agent_for(agent_id: str, fallback):
    if str(getattr(fallback, 'id', '')) == agent_id:
        return fallback
    return await Agent.get(agent_id)


def _validate_turn_target(query: dict, user_key: str, binding) -> None:
    supplied_session = query.get('session_id')
    supplied_agent = query.get('agent_id')
    supplied_user = query.get('user_id')
    if (
        supplied_session is not None
        and str(supplied_session) != binding.session_id
    ) or (
        supplied_agent is not None
        and str(supplied_agent) != binding.agent_id
    ) or (
        supplied_user is not None
        and str(supplied_user) != user_key
    ):
        raise OwnershipNotFound()


async def _authorize_turn(query: dict, user_key: str, session: Session, web_agent):
    binding = await require_active_owned(
        str(session.id),
        user_key,
        str(web_agent.id),
    )
    _validate_turn_target(query, user_key, binding)
    persisted = await Session.get(binding.session_id)
    if persisted is None or str(persisted.agent_id or '') != binding.agent_id:
        raise OwnershipNotFound()
    context = ToolExecutionContext(
        user_id=user_key,
        session_id=binding.session_id,
        agent_id=binding.agent_id,
    )
    return binding, persisted, context


class WebSocketManager:
    def __init__(self, agent):
        self.agent = agent
        from cognitrix.utils.core import register_websocket_manager
        register_websocket_manager(agent.id, self)

    async def websocket_endpoint(self, websocket: WebSocket, user_key: str):
        """Handle one verified principal; authority never lives on the manager."""
        web_agent = self.agent
        connection_token = _active_websocket.set(websocket)
        await websocket.accept()
        try:
            binding, session = await get_or_create_owned_session(
                user_key,
                str(web_agent.id),
            )
        except Exception:
            logger.exception('Could not initialize owned WebSocket session')
            await websocket.close(code=1011, reason='Session unavailable')
            _active_websocket.reset(connection_token)
            return

        try:
            while True:
                query = json.loads(await websocket.receive_text())
                if not isinstance(query, dict):
                    raise ValueError('Invalid WebSocket action')
                action = str(query.get('action', ''))
                query_type = str(query.get('type', ''))

                try:
                    if query_type == 'chat_history':
                        session_id = str(query.get('session_id') or '')
                        if action == 'get':
                            binding, session = await _load_owned_session(
                                session_id,
                                user_key,
                            )
                            loaded_agent = await _agent_for(binding.agent_id, web_agent)
                            if loaded_agent is None:
                                raise OwnershipNotFound()
                            web_agent = loaded_agent
                            await websocket.send_json({
                                'type': query_type,
                                'content': session.chat,
                                'agent_name': web_agent.name,
                                'action': action,
                            })
                        elif action == 'delete':
                            binding, session = await _load_owned_session(
                                session_id,
                                user_key,
                            )
                            loaded_agent = await _agent_for(binding.agent_id, web_agent)
                            if loaded_agent is None:
                                raise OwnershipNotFound()
                            web_agent = loaded_agent
                            _, session = await _clear_owned_session(session_id, user_key)
                            await websocket.send_json({
                                'type': query_type,
                                'content': session.chat,
                                'agent_name': web_agent.name,
                                'action': action,
                            })

                    elif query_type == 'sessions':
                        if action == 'list':
                            sessions = [item.json() for item in await _owned_sessions(user_key)]
                            await websocket.send_json({
                                'type': query_type,
                                'content': sessions,
                                'action': action,
                            })
                        elif action == 'get':
                            requested_agent = str(query.get('agent_id') or '')
                            requested_session = str(query.get('session_id') or '')
                            if requested_session:
                                binding, session = await _load_owned_session(
                                    requested_session,
                                    user_key,
                                    agent_id=requested_agent,
                                )
                                loaded_agent = await _agent_for(binding.agent_id, web_agent)
                            else:
                                loaded_agent = await _agent_for(requested_agent, web_agent)
                                if loaded_agent is None:
                                    raise OwnershipNotFound()
                                binding, session = await get_or_create_owned_session(
                                    user_key,
                                    str(loaded_agent.id),
                                )
                            if loaded_agent is None:
                                raise OwnershipNotFound()
                            web_agent = loaded_agent
                            await websocket.send_json({
                                'type': query_type,
                                'agent_name': web_agent.name,
                                'content': session.json(),
                                'action': action,
                            })
                        elif action == 'delete':
                            session_id = str(query.get('session_id') or '')
                            deleted_current = session_id == str(session.id)
                            token = await _delete_owned_session(session_id, user_key)
                            if deleted_current:
                                binding, session = await get_or_create_owned_session(
                                    user_key,
                                    token.agent_id,
                                )
                                loaded_agent = await _agent_for(token.agent_id, web_agent)
                                if loaded_agent is not None:
                                    web_agent = loaded_agent
                            sessions = [item.json() for item in await _owned_sessions(user_key)]
                            await websocket.send_json({
                                'type': query_type,
                                'content': sessions,
                                'action': action,
                            })

                    elif query_type == 'generate':
                        binding, session, tool_context = await _authorize_turn(
                            query, user_key, session, web_agent,
                        )
                        default_prompt = str(query.get('prompt') or '')
                        prompt = ''
                        name = str(query.get('name') or '')
                        agent = web_agent
                        if action == 'system_prompt':
                            agent = PromptGenerator(llm=web_agent.llm)
                            prompt = 'Agent Description'
                            if name:
                                prompt += f'\n\nAgent Name: {name}'
                            prompt += f'\n\n{default_prompt}'
                        elif action == 'team_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = team_details_generator
                            available_agents = [item.name for item in await Agent.all()]
                            agent.system_prompt = agent.system_prompt.replace(
                                '{agents}', '\n'.join(available_agents),
                            )
                            prompt = default_prompt
                        elif action == 'agent_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = agent_generator
                            available_tools = [item.name for item in Tool.list_all_tools()]
                            agent.system_prompt = agent.system_prompt.replace(
                                '{available_tools}', '\n'.join(available_tools),
                            )
                            prompt = default_prompt
                        elif action == 'task_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = task_details_generator
                            prompt = default_prompt
                        session.chat = []
                        await session(
                            prompt,
                            agent,
                            interface='web',
                            stream=True,
                            output=websocket.send_json,
                            wsquery=query,
                            save_history=False,
                            tool_context=tool_context,
                        )

                    elif query_type == 'multistep':
                        binding, session, tool_context = await _authorize_turn(
                            query, user_key, session, web_agent,
                        )
                        prompt = str(query.get('prompt') or '')
                        if query_type == 'multistep':
                            await websocket.send_json({
                                'type': 'status',
                                'content': 'Planning multi-step task...',
                            })

                            async def notify_task(task_id):
                                await websocket.send_json({
                                    'type': 'status',
                                    'content': (
                                        'Task created â€” watch it run live on the '
                                        f'task page (/tasks/{task_id}).'
                                    ),
                                    'task_id': task_id,
                                })

                            context_token = set_execution_context(tool_context)
                            try:
                                result = await handle_multi_step_task(
                                    prompt,
                                    web_agent,
                                    session,
                                    web_agent.llm,
                                    stream=False,
                                    interface='ws',
                                    on_task_created=notify_task,
                                )
                            finally:
                                reset_execution_context(context_token)
                            await websocket.send_json({
                                'type': 'multistep_result',
                                'content': result,
                            })
                    else:
                        binding, session, tool_context = await _authorize_turn(
                            query, user_key, session, web_agent,
                        )
                        await session(
                            str(query.get('content') or ''),
                            web_agent,
                            interface='web',
                            stream=True,
                            output=websocket.send_json,
                            wsquery=query,
                            tool_context=tool_context,
                        )
                except OwnershipNotFound:
                    await websocket.send_json({
                        'type': 'error',
                        'content': 'Session not found',
                    })
                except OwnershipConflict as error:
                    await websocket.send_json({
                        'type': 'error',
                        'content': str(error),
                        'status': 409,
                    })
                except Exception as error:
                    logger.exception(error)
                    await websocket.send_json({
                        'type': 'error',
                        'content': 'Request failed',
                    })

        except WebSocketDisconnect:
            logger.warning('Websocket disconnected')
        except Exception as error:
            logger.exception(error)
        finally:
            _active_websocket.reset(connection_token)

    async def send_team_message(self, sender: str, receiver: str, content: str):
        websocket = _active_websocket.get()
        if websocket:
            await websocket.send_json({
                'type': 'team_message',
                'sender': sender,
                'receiver': receiver,
                'content': content,
            })
        else:
            logger.warning('WebSocket is not connected. Unable to send team message.')


class WebSocketManagerProxy:
    """A serializable proxy storing only the active manager lookup id."""

    def __init__(self, task_id: str):
        self.task_id = task_id

    async def send_team_message(self, sender: str, receiver: str, content: str):
        from cognitrix.utils.core import get_websocket_manager
        ws_manager = get_websocket_manager(self.task_id)
        if ws_manager:
            await ws_manager.send_team_message(sender, receiver, content)
