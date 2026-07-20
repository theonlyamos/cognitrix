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

from cognitrix.errors import ExecutionControlError
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
# 100 fits long multi-file/agentic turns while still bounding runaway loops. Configurable.
MAX_TOOL_ROUNDS = int(os.getenv('COGNITRIX_MAX_TOOL_ROUNDS', '100'))

# Compaction: when the stored history estimate crosses this fraction of the
# model's usable window, fold the oldest turns into a summary message.
COMPACT_THRESHOLD = 0.7
# Never fold the most recent turns — they are the working context.
COMPACT_KEEP_TURNS = 4

# Cap tool param/result previews streamed to the browser. A search tool can
# return hundreds of KB; the chat UI only shows a preview in the collapsible.
_TOOL_PREVIEW_MAX = 4000
_MALFORMED_TOOL_LABEL = 'Malformed tool call'
_STOPPED_TOOL_TEXT = 'Stopped by user.'


def _tool_preview(data: Any) -> str:
    """Stringify and cap for the chat UI.

    Pretty-print genuine dict/list; otherwise use str() — matching how tool
    results are persisted (process_prompt does str(data)). Using json.dumps with
    default=str on a string-like object would double-quote and escape newlines.
    """
    if isinstance(data, str):
        s = data
    elif isinstance(data, dict | list):
        try:
            s = json.dumps(data, default=str, indent=2)
        except Exception:
            s = str(data)
    else:
        s = str(data)
    if len(s) > _TOOL_PREVIEW_MAX:
        return s[:_TOOL_PREVIEW_MAX] + f"\n… (truncated, {len(s)} chars total)"
    return s


def _stopped_tool_messages(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build terminal results for tool calls left unresolved by cancellation."""
    stopped = []
    for tool_call in tool_calls:
        stopped.append({
            'role': 'tool',
            'tool_call_id': tool_call.get('tool_call_id'),
            'content': _STOPPED_TOOL_TEXT,
            'outcome': {
                'status': 'stopped',
                'text': _STOPPED_TOOL_TEXT,
                'artifacts': [],
                'entities': [],
                'warnings': [],
                'error': None,
            },
        })
    return stopped


class Session(Model):
    chat: list[dict[str, Any]] = []
    """The chat history of the session"""

    # default_factory, not a plain default: a plain default is evaluated once at
    # import, giving every session created in a process the same timestamp.
    datetime: str = Field(default_factory=lambda: datetime.now().strftime("%a %b %d %Y %H:%M:%S"))
    """When the session was started"""

    agent_id: str | None = None
    """The id of the agent that started the session"""

    user_id: str | None = None
    """Durable owner of an ordinary web/API chat (None for local/internal sessions)"""

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
    async def get_by_agent_id(cls, agent_id: str, user_id: str | None = None) -> Self:
        """Retrieve a session by agent, optionally scoped to a web/API owner.

        Omitting ``user_id`` preserves local CLI behavior. Web callers must
        always pass it, which deliberately ignores legacy ownerless rows.
        """
        agent_id_str = str(agent_id)
        query = {'agent_id': agent_id_str}
        if user_id is not None:
            query['user_id'] = str(user_id)
        session = await cls.find_one(query)
        if not session:
            session = cls(
                agent_id=agent_id_str,
                user_id=str(user_id) if user_id is not None else None,
            )
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

    async def __call__(self, message: str|dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws', 'compat'] = 'cli', stream: bool = False, output: Callable = print, wsquery: dict[str, str] | None= None, save_history: bool = True, attachments: dict[str, Any] | None = None, tool_context=None, record_history: bool | None = None, persist_history: bool | None = None, compact_history: bool | None = None):

        # ``save_history`` remains the compatibility umbrella.  Task execution
        # records the assistant/tool protocol in memory while independently
        # disabling database writes and chat compaction.
        if record_history is None:
            record_history = save_history
        if persist_history is None:
            persist_history = save_history
        if compact_history is None:
            compact_history = persist_history

        # Add timing for turn duration
        turn_start_time = time.monotonic()

        # Every agent can invoke skills: ensure the load_skill meta-tool is present
        # for this turn. UI/API-created agents only carry their configured tools,
        # so a `/skill` chat message would otherwise have nothing to load it.
        if interface != 'task':
            try:
                if not any(str(getattr(t, 'name', '')).lower() == 'load skill' for t in agent.tools):
                    from cognitrix.tools.base import ToolManager
                    _skill_tool = ToolManager.get_by_name('load_skill')
                    if _skill_tool:
                        agent.tools.append(_skill_tool)
            except Exception:
                pass

        # Ask User is a transport capability, not persisted agent
        # configuration. Attach it lazily to every direct web-chat turn so
        # existing Assistants gain the capability without a database migration.
        if interface == 'web':
            try:
                if not any(str(getattr(t, 'name', '')).lower() == 'ask user' for t in agent.tools):
                    from cognitrix.tools.base import ToolManager
                    question_tool = ToolManager.get_by_name('ask_user')
                    if question_tool:
                        agent = agent.model_copy(update={
                            'tools': [*agent.tools, question_tool],
                        })
            except Exception:
                pass

        # Add the new message to the history before building the prompt
        if wsquery is None:
            wsquery = {}
        if record_history:
            self.update_history(agent.process_prompt(message))
            # User uploads ride alongside the text turn as immutable artifact
            # references and confined tool-root-relative document paths.
            if attachments:
                from pathlib import Path

                from cognitrix.tools.utils import ArtifactRef

                extra: list[dict[str, Any]] = []
                for img in attachments.get('images', []):
                    artifact = ArtifactRef.model_validate(img).model_dump()
                    extra.append({
                        'role': 'User',
                        'type': 'image',
                        'content': f"[Current image artifact: {artifact['id']}]",
                        'artifact': artifact,
                    })
                selected = attachments.get('image_selection')
                if selected:
                    artifact = ArtifactRef.model_validate(selected).model_dump()
                    extra.append({
                        'role': 'User',
                        'type': 'image_selection',
                        'content': f"[Selected source image artifact: {artifact['id']}]",
                        'artifact': artifact,
                    })
                files = attachments.get('files', [])
                if files:
                    safe_paths = []
                    for item in files:
                        value = str(item.get('path') or '')
                        relative = Path(value)
                        if (
                            value
                            and not relative.is_absolute()
                            and not relative.drive
                            and '..' not in relative.parts
                            and len(relative.parts) >= 3
                            and relative.parts[0] == 'uploads'
                        ):
                            safe_paths.append(relative.as_posix())
                    paths = '\n'.join(safe_paths)
                    if paths:
                        extra.append({
                            'role': 'User', 'type': 'text',
                            'content': f'[User uploaded files, readable with your file tools:]\n{paths}',
                        })
                if extra:
                    attachment_history_start = len(self.chat)
                    self.update_history(extra)
                    on_adopted = attachments.get('_on_adopted')
                    if callable(on_adopted):
                        # Media ownership transfers only after these safe refs
                        # are durably recorded. If cancellation arrives during
                        # save, settle it, acknowledge adoption, then propagate.
                        try:
                            save_task = asyncio.create_task(self.save())
                            cancelled: asyncio.CancelledError | None = None
                            try:
                                await asyncio.shield(save_task)
                            except asyncio.CancelledError as exc:
                                cancelled = exc
                                while not save_task.done():
                                    try:
                                        await asyncio.shield(save_task)
                                    except asyncio.CancelledError:
                                        continue
                            save_task.result()
                        except BaseException:
                            # A failed durable adoption must not leave dead
                            # promoted refs in a reusable in-memory Session.
                            del self.chat[attachment_history_start:]
                            raise
                        on_adopted()
                        if cancelled is not None:
                            raise cancelled

        # Build the context-aware prompt using the manager
        prompt = await agent.get_context_manager().build_prompt(agent, self)

        formatted_tools = [
            tool.to_dict_format() for tool in agent.tools
            if not tool.supported_interfaces or interface in tool.supported_interfaces
        ]
        # Only advertise tools to models with native tool-use; otherwise the tool
        # list is embedded in the system prompt (AgentManager.formatted_system_prompt).
        active_tools = formatted_tools if agent.llm.supports_tool_use else None

        tool_rounds = 0
        active_tool_calls: list[dict[str, Any]] = []
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
                            active_tool_calls = response.tool_calls
                            # (interface is threaded into call_tools so approval uses
                            # the right channel — CLI prompts, web/ws deny for now.)
                            # Record the assistant message that ISSUED the tool calls
                            # BEFORE the tool results, so the re-prompt is a valid
                            # OpenAI sequence (assistant.tool_calls -> tool results).
                            # Unconditional (like the tool-results append below): the
                            # loop needs both in history to rebuild the next prompt.
                            if record_history:
                                self.update_history({
                                    'role': 'assistant',
                                    'name': agent.name,
                                    'type': 'tool_calls',
                                    'content': response.llm_response or '',
                                    'tool_calls': response.tool_calls,
                                })
                            # Surface tool activity to browser transports so the chat
                            # UI can show what the agent is running: name + params up
                            # front, then the result on completion. The whole batch
                            # runs concurrently in call_tools (all starts, then all
                            # completions). Previews are stringified/capped and keyed
                            # by tool_call_id so the UI pairs result to the right call.
                            tool_meta = [
                                (t.get('name'), t.get('tool_call_id'), t.get('arguments') or {})
                                for t in response.tool_calls
                            ]

                            async def emit_tool_terminal(
                                name: str | None,
                                tcid: str | None,
                                item: dict[str, Any],
                            ) -> None:
                                data = item.get('data', '')
                                outcome = item.get('outcome')
                                # ``data`` is model-facing; the UI keeps the
                                # compact outcome text it has always displayed.
                                preview = (
                                    outcome.get('text', data)
                                    if isinstance(outcome, dict) else data
                                )
                                status = 'completed' if (
                                    outcome.get('status') == 'success'
                                    if outcome else item.get('success') is True
                                ) else 'error'
                                await output({
                                    'type': 'tool',
                                    'status': status,
                                    'tool_name': name or _MALFORMED_TOOL_LABEL,
                                    'tool_call_id': tcid,
                                    'result': _tool_preview(preview),
                                    'outcome': outcome,
                                    'artifacts': (outcome or {}).get('artifacts', []),
                                })

                            if interface in ('web', 'ws', 'task'):
                                for name, tcid, args in tool_meta:
                                    if name:
                                        await output({'type': 'tool', 'status': 'started', 'tool_name': name,
                                                      'tool_call_id': tcid, 'params': _tool_preview(args)})
                            # Tool artifacts are session-owned.  ContextVars keep
                            # concurrent browser/task turns isolated without
                            # allowing the model to choose a storage namespace.
                            from cognitrix.artifacts import reset_session, set_session
                            from cognitrix.tools.utils import (
                                ToolExecutionContext, reset_execution_context, set_execution_context,
                            )
                            bound_context = tool_context or ToolExecutionContext()
                            artifact_token = set_session(
                                str(self.id), str(agent.id), bound_context.user_id
                            )
                            execution_token = set_execution_context(bound_context)
                            try:
                                result: dict[Any, Any] | str = await agent.call_tools(
                                    response.tool_calls,
                                    interface=interface,
                                )
                            except asyncio.CancelledError as exc:
                                # call_tools records outcomes incrementally. Keep
                                # completed siblings (including artifacts) and
                                # leave only unresolved calls for the outer stop
                                # handler to close with a stopped result.
                                completed_by_index = getattr(
                                    exc, 'completed_results_by_index', None
                                )
                                if isinstance(completed_by_index, dict):
                                    terminal_messages: list[dict[str, Any]] = []
                                    for i, tool_call in enumerate(active_tool_calls):
                                        completed_entry = completed_by_index.get(i)
                                        if completed_entry is None:
                                            terminal_messages.extend(
                                                _stopped_tool_messages([tool_call])
                                            )
                                            continue
                                        processed = agent.process_prompt({
                                            'type': 'tool_calls_result',
                                            'result': [completed_entry],
                                        })
                                        if isinstance(processed, list):
                                            terminal_messages.extend(processed)
                                        else:
                                            terminal_messages.append(processed)
                                    if terminal_messages and record_history:
                                        self.update_history(terminal_messages)
                                    if interface in ('web', 'ws', 'task'):
                                        for i, (name, tcid, _args) in enumerate(tool_meta):
                                            completed_entry = completed_by_index.get(i)
                                            if completed_entry is not None:
                                                await emit_tool_terminal(
                                                    name, tcid, completed_entry
                                                )
                                    active_tool_calls = []
                                else:
                                    # Compatibility fallback for a cancellation
                                    # raised outside AgentManager.call_tools.
                                    completed = getattr(exc, 'completed_result', None)
                                    if (
                                        isinstance(completed, dict)
                                        and completed.get('type') == 'tool_calls_result'
                                        and completed.get('result')
                                    ):
                                        if record_history:
                                            self.update_history(agent.process_prompt(completed))
                                    unresolved = getattr(
                                        exc, 'unresolved_tool_calls', None
                                    )
                                    if unresolved is not None:
                                        active_tool_calls = unresolved
                                raise
                            finally:
                                reset_execution_context(execution_token)
                                reset_session(artifact_token)
                            called_tools = True
                            tool_rounds += 1
                            is_tool_result = (
                                isinstance(result, dict)
                                and result.get('type') == 'tool_calls_result'
                            )
                            if is_tool_result:
                                # Persist protocol results before any awaited UI
                                # emission, so a transport cancellation cannot
                                # relabel completed calls as stopped.
                                if record_history:
                                    self.update_history(agent.process_prompt(result))
                                active_tool_calls = []
                            if interface in ('web', 'ws', 'task'):
                                result_list = result['result'] if is_tool_result else []
                                for i, (name, tcid, _args) in enumerate(tool_meta):
                                    item = result_list[i] if i < len(result_list) else {}
                                    await emit_tool_terminal(name, tcid, item)

                            if is_tool_result:
                                # Deny-loop breaker: if every call in the batch was
                                # denied and the model re-issues the exact same
                                # batch, stop instead of re-prompting round after
                                # round. Structured tool-policy denials supersede
                                # the legacy approval-prefix representation.
                                all_blocked = all(
                                    (
                                        isinstance(r.get('outcome'), dict)
                                        and r['outcome'].get('status') == 'denied'
                                    )
                                    or str(r.get('data', '')).startswith(OPERATION_BLOCKED_PREFIX)
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
                            if interface in ('ws', 'web', 'task'):
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
                        if interface == 'task':
                            from cognitrix.errors import ProviderExecutionError

                            raise ProviderExecutionError('provider request failed')
                        break

                    # Persist the final assistant text only when this response did
                    # NOT issue tool calls (that assistant message was already saved
                    # with its tool_calls above).
                    if response and record_history and not response.tool_calls:
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
                        if record_history:
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

                except ExecutionControlError:
                    raise
                except Exception as e:
                    logger.exception(e)
                    break # Exit on error

            # Calculate turn duration for all exit paths
            turn_duration = time.monotonic() - turn_start_time
            duration_str = format_duration(turn_duration)

            # Store timing in history (only if not already stored in 'not called_tools' block)
            if record_history and called_tools:
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

            if persist_history:
                await self.save()
            if compact_history:
                try:
                    await self._maybe_compact(agent)
                except Exception:
                    logger.exception("History compaction failed; keeping history as-is")

            # Add to agent's memory
            if persist_history and hasattr(agent, 'context_manager'):
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

        except asyncio.CancelledError:
            if record_history:
                stopped_messages = _stopped_tool_messages(active_tool_calls)
                if stopped_messages:
                    self.update_history(stopped_messages)
            if persist_history:
                try:
                    # Preserve the protocol-closing tool results before the
                    # transport reports the stopped turn to the client.
                    await asyncio.shield(self.save())
                except Exception:
                    logger.exception("Failed to persist stopped tool results")
            raise
        except ExecutionControlError:
            raise
        except Exception as e:
            logger.exception(e)

class SessionManager:
    """Manager for Session business logic"""

    def __init__(self, session: Session):
        self.session = session

    # Provide a convenience creator to match the uniform API requested.
    @staticmethod
    async def create(agent_id: str | None = None, team_id: str | None = None, task_id: str | None = None, user_id: str | None = None) -> 'Session':
        session = Session(
            agent_id=agent_id,
            team_id=team_id,
            task_id=task_id,
            user_id=user_id,
        )
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
