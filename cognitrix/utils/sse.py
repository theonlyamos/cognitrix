import asyncio
import json
import logging

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from cognitrix.agents import Agent, PromptGenerator
from cognitrix.agents.generators import TaskInstructor
from cognitrix.sessions.base import Session
from cognitrix.tasks.handler import handle_multi_step_task, is_multi_step_task

logger = logging.getLogger('cognitrix.log')

# Bound the SSE queues so a slow/stalled EventSource client backpressures the
# producer (via `await queue.put(...)`) instead of buffering chunks unboundedly
# in memory. Both the inbound action queue (POST /agents/chat uses `await put`)
# and the per-turn output queue (_emit uses `await put`) block when full.
_SSE_QUEUE_MAXSIZE = 512


class SSEManager:
    def __init__(self, agent):
        self.agent = agent
        self.action_queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)

    async def sse_endpoint(self, request: Request):
        async def event_generator():
            while True:
                if await request.is_disconnected():
                    break

                try:
                    action = await asyncio.wait_for(self.action_queue.get(), timeout=1.0)
                except TimeoutError:
                    yield {'event': 'ping', 'data': ''}
                    await asyncio.sleep(0.25)
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
                    user_prompt = action['content']

                    # Check for multi-step tasks
                    if is_multi_step_task(user_prompt):
                        # Send planning status
                        yield {'event': 'message', 'data': json.dumps({'type': 'status', 'content': 'Planning multi-step task...'})}

                        try:
                            session = await Session.get_by_agent_id(self.agent.id)
                            if session is None:
                                yield {'event': 'message', 'data': json.dumps({'type': 'error', 'content': 'No active session found. Please refresh and try again.'})}
                                return

                            result = await handle_multi_step_task(
                                user_prompt,
                                self.agent,
                                session,
                                self.agent.llm,
                                stream=False,
                                interface='web',
                            )
                            yield {'event': 'message', 'data': json.dumps({'type': 'multistep_result', 'content': result})}
                        except Exception as e:
                            yield {'event': 'message', 'data': json.dumps({'type': 'error', 'content': f'Multi-step task failed: {str(e)}'})}
                    else:
                        # Route through the full session loop so the web path gets
                        # tools + safety gating + history + persistence (previously it
                        # called agent.generate() directly, bypassing all of that).
                        # Bridge the session's callback-based output to this SSE
                        # generator through a queue.
                        session = await Session.get_by_agent_id(self.agent.id)
                        out_queue: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)

                        async def _emit(payload, _q=out_queue):
                            await _q.put(payload)

                        async def _run(_prompt=user_prompt, _sess=session, _q=out_queue):
                            try:
                                await _sess(
                                    _prompt, self.agent,
                                    interface='web', stream=True, output=_emit,
                                    wsquery={'type': 'generate', 'action': 'chat_message'},
                                )
                            except Exception as e:
                                logger.exception("SSE session turn failed")
                                await _q.put({'type': 'error', 'content': str(e)})
                            finally:
                                await _q.put(None)

                        run_task = asyncio.create_task(_run())
                        try:
                            while True:
                                item = await out_queue.get()
                                if item is None:
                                    break
                                yield {'event': 'message', 'data': json.dumps(item)}
                            await run_task
                        finally:
                            # If the client disconnected mid-turn the generator is
                            # closed here; cancel the session task so it doesn't keep
                            # running (and executing tools) with no consumer.
                            if not run_task.done():
                                run_task.cancel()

        return EventSourceResponse(event_generator())


# Per-(user, agent) SSE managers. A single shared manager caused concurrent
# clients to share one action queue and one `self.agent`, so one user's messages
# and agent swaps leaked into another's stream. Keying by (user_id, agent_id)
# isolates them. ponytail: unbounded map; add an LRU cap if a long-lived process
# serves many distinct (user, agent) pairs.
_SSE_MANAGERS: dict[tuple[str, str], SSEManager] = {}
_MAX_SSE_MANAGERS = 512


def get_sse_manager(user_id: str, agent_id: str, agent) -> SSEManager:
    """Return the SSE manager for this (user, agent), creating it on first use.

    Both the SSE stream (GET) and the chat POST resolve the same key, so they
    rendezvous on one queue while remaining isolated from other users/agents.
    """
    key = (str(user_id), str(agent_id))
    mgr = _SSE_MANAGERS.get(key)
    if mgr is None:
        # Bound memory: evict the oldest entry past the cap. A returning client
        # simply re-creates its manager on the next request.
        if len(_SSE_MANAGERS) >= _MAX_SSE_MANAGERS:
            _SSE_MANAGERS.pop(next(iter(_SSE_MANAGERS)), None)
        mgr = SSEManager(agent)
        _SSE_MANAGERS[key] = mgr
    else:
        # Refresh the bound agent (it may have been edited/reloaded).
        mgr.agent = agent
    return mgr
