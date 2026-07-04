import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional, Self

from odbms import Model
from pydantic import Field
from rich import print

from cognitrix.providers.base import LLMResponse
from cognitrix.safety.approval_gate import OPERATION_BLOCKED_PREFIX

# from cognitrix.teams.base import Team

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.teams.base import Team

logger = logging.getLogger('cognitrix.log')


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"

# Hard ceiling on retained chat messages per session. The LLM prompt only uses
# the shaped window, so this just bounds memory/serialization growth
# over very long sessions. ponytail: fixed cap; make configurable if needed.
MAX_CHAT_HISTORY = 1000

# Max tool-call rounds in a single turn before the loop stops (guards against a
# model that never stops calling tools). 10 was too low for build-style steps
# (creating several files is >10 tool calls), which cut steps off mid-work;
# 25 fits real multi-file steps while still bounding runaway loops. Configurable.
MAX_TOOL_ROUNDS = int(os.getenv('COGNITRIX_MAX_TOOL_ROUNDS', '25'))

# Compaction: when the stored history estimate crosses this fraction of the
# model's usable window, fold the oldest turns into a summary message.
COMPACT_THRESHOLD = 0.7
# Never fold the most recent turns — they are the working context.
COMPACT_KEEP_TURNS = 4


class Session(Model):
    chat: list[dict[str, Any]] = []
    """The chat history of the session"""

    # default_factory, not a plain default: a plain default is evaluated once at
    # import, giving every session created in a process the same timestamp.
    datetime: str = Field(default_factory=lambda: datetime.now().strftime("%a %b %d %Y %H:%M:%S"))
    """When the session was started"""

    agent_id: str | None = None
    """The id of the agent that started the session"""

    task_id: str | None = None
    """The id of the task that started the session"""

    team_id: str | None = None
    """The id of the team that started the session"""

    started_at: str | None = None
    """Started date of the task"""

    completed_at: str | None = None
    """Completion date of the task"""

    pid: str | None = None
    """Worker Id of task"""

    run_id: str | None = None
    """The TaskRun this session belongs to (task-run step sessions only)"""

    step_index: int | None = None
    """0-based plan step index within the run (None = synthesis/legacy)"""

    step_title: str | None = None
    """Plan step title, denormalized for display"""

    @classmethod
    async def load(cls, session_id: str) -> Self:
        """Load an existing session or create a new one if it doesn't exist"""
        session = await cls.get(session_id)
        if not session:
            session = cls()
            await session.save()
        return session

    @classmethod
    async def list_sessions(cls) -> list[Self]:
        return await cls.all()

    @classmethod
    async def delete(cls, session_id: str):
        """Delete a session by id.

        odbms exposes delete() as an instance method, so load the row and
        delete the instance. The previous implementation called cls.delete()
        which recursed infinitely.
        """
        session = await cls.get(session_id)
        if not session:
            return False
        await Model.delete(session)
        return True


    def update_history(self, message: dict[str, Any] | list[dict[str, Any]]):
        if isinstance(message, list):
            self.chat.extend(message)
        else:
            self.chat.append(message)
        # Bound growth: drop the oldest messages past the ceiling. The prompt
        # builder trims any orphan tool message left at the new start.
        if len(self.chat) > MAX_CHAT_HISTORY:
            del self.chat[:len(self.chat) - MAX_CHAT_HISTORY]

    @property
    async def agent(self):
        from cognitrix.agents.base import Agent
        return await Agent.get(self.agent_id) if self.agent_id else None

    @property
    async def team(self) -> Optional['Team']:
        from cognitrix.teams.base import Team
        return await Team.get(self.team_id) if self.team_id else None



    @classmethod
    async def get_by_agent_id(cls, agent_id: str) -> Self:
        """Retrieve a session by agent_id"""
        agent_id_str = str(agent_id)
        session = await cls.find_one({'agent_id': agent_id_str})
        if not session:
            session = cls(agent_id=agent_id_str)
            await session.save()
        return session


    @classmethod
    async def get_by_task_id(cls, task_id: str) -> list[Self]:
        """Retrieve a session by task_id"""
        return await cls.find({'task_id': task_id}) # type: ignore

    @classmethod
    async def get_by_team_id(cls, team_id: str) -> Self | None:
        """Retrieve a session by team_id"""
        return await cls.find_one({'team_id': str(team_id)})

    async def _maybe_compact(self, agent: 'Agent'):
        """Fold the oldest turns into a summary once history nears the budget.

        Runs at turn end only. Destroys nothing without a produced summary:
        if the summarizer errors or returns nothing, history is left as-is.
        The summary is stored as a user message (type 'summary') so every
        provider accepts it and the window shaper anchors on it.
        """
        from cognitrix.sessions.context import partition_turns
        from cognitrix.utils.tokens import estimate_tokens

        llm = agent.llm
        budget = max(2000, llm.get_context_window() - llm.max_tokens - 2000)
        if estimate_tokens(self.chat) <= budget * COMPACT_THRESHOLD:
            return

        turns = partition_turns(self.chat)
        if len(turns) <= COMPACT_KEEP_TURNS:
            return
        fold_turns = turns[:-COMPACT_KEEP_TURNS]

        lines = []
        for turn in fold_turns:
            for m in turn:
                if m.get('type') == 'turn_timing':
                    continue
                content = str(m.get('content') or '')[:1000]
                if m.get('tool_calls'):
                    content += ' ' + ', '.join(
                        f"[called {tc.get('name')}]" for tc in m['tool_calls']
                    )
                if content.strip():
                    lines.append(f"{m.get('role', '')}: {content}")
        convo = '\n'.join(lines)[:60000]

        prompt = [
            {'role': 'system', 'content': (
                'You compress conversation history. Write a concise summary that '
                'preserves facts, decisions, names, file paths, numbers, and '
                'unresolved tasks. Output only the summary.'
            )},
            {'role': 'user', 'content': f"Summarize this conversation history:\n\n{convo}"},
        ]
        resp = await llm(prompt, stream=False)
        summary = ''
        if resp and not getattr(resp, 'error', None):
            summary = (resp.llm_response or '').strip()
        if not summary:
            logger.warning("Compaction skipped: summarizer returned no summary")
            return

        head = {
            'role': 'user',
            'type': 'summary',
            'content': f"[Summary of the earlier conversation]\n{summary}",
        }
        self.chat = [head] + [m for turn in turns[-COMPACT_KEEP_TURNS:] for m in turn]
        await self.save()
        logger.info("Compacted session history: folded %s turns into a summary", len(fold_turns))

    async def __call__(self, message: str|dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws'] = 'cli', stream: bool = False, output: Callable = print, wsquery: dict[str, str] | None= None, save_history: bool = True):

        # Add timing for turn duration
        turn_start_time = time.monotonic()

        # Add the new message to the history before building the prompt
        if wsquery is None:
            wsquery = {}
        if save_history:
            self.update_history(agent.process_prompt(message))

        # Build the context-aware prompt using the manager
        prompt = await agent.get_context_manager().build_prompt(agent, self)

        formatted_tools = [tool.to_dict_format() for tool in agent.tools]
        # Only advertise tools to models with native tool-use; otherwise the tool
        # list is embedded in the system prompt (AgentManager.formatted_system_prompt).
        active_tools = formatted_tools if agent.llm.supports_tool_use else None

        tool_rounds = 0
        last_denied_sig = None
        stop_turn = False
        try:
            while True:  # The loop now primarily handles tool calls, not initial message processing
                if tool_rounds >= MAX_TOOL_ROUNDS:
                    logger.warning("Exceeded max tool rounds (%s); stopping turn", MAX_TOOL_ROUNDS)
                    stop_msg = f"[Stopped after {MAX_TOOL_ROUNDS} tool rounds]"
                    if interface == 'cli':
                        output(f"\n{stop_msg}")
                    else:
                        # Surface to non-cli consumers (e.g. the multi-step step
                        # capture) so the step output isn't silently empty.
                        await output({'type': wsquery.get('type'), 'content': stop_msg, 'action': wsquery.get('action'), 'complete': False})
                    break
                try:
                    response: LLMResponse = LLMResponse()
                    called_tools: bool = False

                    # The prompt is now a complete history, not a single message
                    llm_result = await agent.llm(prompt, stream=stream, tools=active_tools)

                    if stream:
                        async_iter = llm_result  # type: ignore[arg-type]
                    else:
                        async def _single_resp(result=llm_result):
                            yield result  # type: ignore[misc]

                        async_iter = _single_resp()

                    async for response in async_iter:  # type: ignore[misc]
                        if stream:
                            if interface == 'cli':
                                output(f"{response.current_chunk}", end="")
                            else:
                                await output({'type': wsquery.get('type'), 'content': response.current_chunk, 'action': wsquery.get('action'), 'complete': False})
                        else:
                            if response.result:
                                if interface == 'cli':
                                    output(f"\n{agent.name}:", response.result)
                                else:
                                    await output({'type': wsquery.get('type'), 'content': response.result, 'action': wsquery.get('action'), 'complete': False})
                            else:
                                if interface == 'cli':
                                    output(f"\n{agent.name}:", response.llm_response)
                                else:
                                    await output({'type': wsquery.get('type'), 'content': response.llm_response, 'action': wsquery.get('action'), 'complete': False})

                        if response.tool_calls and not called_tools:
                            # (interface is threaded into call_tools so approval uses
                            # the right channel — CLI prompts, web/ws deny for now.)
                            # Record the assistant message that ISSUED the tool calls
                            # BEFORE the tool results, so the re-prompt is a valid
                            # OpenAI sequence (assistant.tool_calls -> tool results).
                            # Unconditional (like the tool-results append below): the
                            # loop needs both in history to rebuild the next prompt.
                            self.update_history({
                                'role': 'assistant',
                                'name': agent.name,
                                'type': 'tool_calls',
                                'content': response.llm_response or '',
                                'tool_calls': response.tool_calls,
                            })
                            result: dict[Any, Any] | str = await agent.call_tools(response.tool_calls, interface=interface)
                            called_tools = True
                            tool_rounds += 1

                            if isinstance(result, dict) and result['type'] == 'tool_calls_result':
                                # If a tool call has a result, add it to history and rebuild the prompt
                                self.update_history(agent.process_prompt(result))

                                # Deny-loop breaker: if every call in the batch was
                                # blocked and the model re-issues the exact same
                                # batch, stop instead of re-prompting for approval
                                # round after round.
                                all_blocked = all(
                                    str(r.get('data', '')).startswith(OPERATION_BLOCKED_PREFIX)
                                    for r in result['result']
                                )
                                sig = json.dumps(
                                    [(t.get('name'), t.get('arguments')) for t in response.tool_calls],
                                    sort_keys=True, default=str,
                                )
                                if all_blocked and sig == last_denied_sig:
                                    msg = "Stopped: the requested operation was denied and will not be retried."
                                    if interface == 'cli':
                                        output(f"\n{msg}")
                                    else:
                                        await output({'type': wsquery.get('type'), 'content': msg, 'action': wsquery.get('action'), 'complete': False})
                                    stop_turn = True
                                    break
                                last_denied_sig = sig if all_blocked else None

                                prompt = await agent.get_context_manager().build_prompt(agent, self)
                                continue  # Continue the loop to re-prompt the LLM with the tool result
                            else:
                                if interface == 'cli':
                                    output(result)
                                else:
                                    await output({'type': wsquery.get('type'), 'content': result, 'action': wsquery.get('action'), 'complete': False})

                        if response.artifacts:
                            # Both the WS and SSE transports call the session with
                            # interface='web', so gating on 'ws' alone dropped all
                            # artifacts. Emit to any browser transport (async output).
                            if interface in ('ws', 'web'):
                                await output({'type': wsquery.get('type'), 'content': '', 'action': wsquery.get('action'), 'artifacts': response.artifacts, 'complete': False})

                        # Cooperative yield point (no artificial delay): a fixed
                        # 10ms sleep here added ~seconds per streamed answer.
                        await asyncio.sleep(0)

                    # Deny-loop breaker fired inside the stream loop: end the turn.
                    if stop_turn:
                        break

                    # Provider/transport error: already surfaced to the user above;
                    # don't persist it as a normal answer or re-prompt the tool loop.
                    if response and getattr(response, 'error', None):
                        break

                    # Persist the final assistant text only when this response did
                    # NOT issue tool calls (that assistant message was already saved
                    # with its tool_calls above).
                    if response and save_history and not response.tool_calls:
                        response_dict = {
                            'role': 'assistant',
                            'name': agent.name,
                            'type': 'text',
                            'content': response.llm_response
                        }
                        self.update_history(response_dict)

                    if not called_tools:
                        # Calculate turn duration
                        turn_duration = time.monotonic() - turn_start_time
                        duration_str = format_duration(turn_duration)

                        # Store timing + token usage in history
                        if save_history:
                            usage = (getattr(response, 'usage', None) or {}) if response else {}
                            self.update_history({
                                'role': 'system',
                                'type': 'turn_timing',
                                'content': f"Took {duration_str}",
                                'duration': turn_duration,
                                'prompt_tokens': usage.get('prompt_tokens'),
                                'completion_tokens': usage.get('completion_tokens'),
                            })

                        # Display timing for CLI
                        if interface == 'cli':
                            print(f"\n[dim]Took {duration_str}[/dim]")

                        break # Exit loop if no tools were called

                except Exception as e:
                    logger.exception(e)
                    break # Exit on error

            # Calculate turn duration for all exit paths
            turn_duration = time.monotonic() - turn_start_time
            duration_str = format_duration(turn_duration)

            # Store timing in history (only if not already stored in 'not called_tools' block)
            if save_history and called_tools:
                usage = (getattr(response, 'usage', None) or {}) if response else {}
                self.update_history({
                    'role': 'system',
                    'type': 'turn_timing',
                    'content': f"Took {duration_str}",
                    'duration': turn_duration,
                    'prompt_tokens': usage.get('prompt_tokens'),
                    'completion_tokens': usage.get('completion_tokens'),
                })

            # Display timing for CLI (only if not already displayed)
            if interface == 'cli' and called_tools:
                print(f"\n[dim]Took {duration_str}[/dim]")

            if save_history:
                await self.save()
                try:
                    await self._maybe_compact(agent)
                except Exception:
                    logger.exception("History compaction failed; keeping history as-is")

            # Add to agent's memory
            if save_history and hasattr(agent, 'context_manager'):
                try:
                    await agent.context_manager.add_to_memory({
                        'role': 'user',
                        'type': 'text',
                        'content': message if isinstance(message, str) else str(message)
                    })
                    if response:
                        await agent.context_manager.add_to_memory({
                            'role': 'assistant',
                            'type': 'text',
                            'content': response.llm_response
                        })
                except Exception as e:
                    logger.error(f"Failed to add to memory: {e}")

        except Exception as e:
            logger.exception(e)

class SessionManager:
    """Manager for Session business logic"""

    def __init__(self, session: Session):
        self.session = session

    # Provide a convenience creator to match the uniform API requested.
    @staticmethod
    async def create(agent_id: str | None = None, team_id: str | None = None, task_id: str | None = None) -> 'Session':
        session = Session(agent_id=agent_id, team_id=team_id, task_id=task_id)
        await session.save()
        return session

# Note: the previous SessionManager.__call__ was a near-duplicate of
# Session.__call__ and had no callers; the single turn loop now lives in
# Session.__call__. SessionManager keeps only create() + the manager property.

# ---------------------------------------------------------------------------
# Attach SessionManager helpers to the Session model so that all management
# logic lives in SessionManager while existing call-sites can still use
# Session.load / list_sessions etc.
# ---------------------------------------------------------------------------

def _session_manager(self: Session) -> 'SessionManager':  # type: ignore[name-defined]
    return SessionManager(self)

Session.manager = property(_session_manager)  # type: ignore[attr-defined]

# Delegate class-level helpers
Session.load = staticmethod(Session.load)  # already exists, keep for compatibility
Session.list_sessions = staticmethod(Session.list_sessions)  # type: ignore[attr-defined]
# Note: Session.delete stays a classmethod (delete-by-id); do not re-wrap it.
