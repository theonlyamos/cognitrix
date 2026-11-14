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

from cognitrix.common.encoding import status_print
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

# A reply cut off by the output-token limit is continued this many times
# before the turn ends with what it has.
MAX_CUTOFF_CONTINUES = 3

# Compaction inside a turn: once a long tool loop's own messages pass this
# fraction of the usable window, its older rounds are folded into a progress
# summary. The newest rounds stay whole: they are what the model works from.
TURN_COMPACT_THRESHOLD = 0.6
TURN_KEEP_ROUNDS = 4

_PROGRESS_SUMMARY_PROMPT = (
    'You compact the working memory of an AI agent that is in the middle of a '
    'long task. Write a progress summary the agent can continue from without '
    'its earlier tool results. Keep every fact, number, value, name, file '
    'path, URL and ID it found that may matter for the request, what it has '
    'done (tools called and their outcomes, errors included) and what is left '
    'to do. Be complete about data and brief about everything else. Output '
    'only the summary.'
)

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


def _with_folder_prompt(prompt, folders) -> Any:
    """Add the working-folders block to the system message."""
    if not folders or not isinstance(prompt, list):
        return prompt
    from cognitrix.common.folders import folder_prompt
    block = folder_prompt(folders)
    if not block:
        return prompt
    if prompt and isinstance(prompt[0], dict) and prompt[0].get('role') == 'system' \
            and isinstance(prompt[0].get('content'), str):
        return [{**prompt[0], 'content': prompt[0]['content'] + '\n\n' + block}, *prompt[1:]]
    return [{'role': 'system', 'content': block}, *prompt]


class Session(Model):
    chat: list[dict[str, Any]] = []
    """The chat history of the session"""

    # default_factory, not a plain default: a plain default is evaluated once at
    # import, giving every session created in a process the same timestamp.
    datetime: str = Field(default_factory=lambda: datetime.now().strftime("%a %b %d %Y %H:%M:%S"))
    """When the session was started"""

    agent_id: str | None = None
    """The id of the agent that started the session"""

    folders: list[dict[str, Any]] | None = None
    """Working folders for this chat ({path, access, name, primary}). None until
    the first turn copies the agent's defaults."""

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

    group_chat: bool | None = None
    """True for a team group chat (the user talking with every team member)"""

    incognito: bool | None = None
    """An incognito chat: agents don't read or change memory or search past
    chats in it, it's left out of chat lists and history search, and it's
    deleted when the person leaves it (or after a day)."""

    title: str | None = None
    """A name the person gave this chat. None: the list shows its first message."""

    pinned: bool | None = None
    """Kept at the top of the chat list."""

    archived: bool | None = None
    """Hidden from the chat list until the person looks at archived chats."""

    pending_messages: list[dict[str, Any]] | None = None
    """Messages sent while the last turn ran that it never read. The next
    turn clears them; a page that reopens the chat sends them."""

    started_by: dict[str, Any] | None = None
    """Set when an agent started this chat: {agent_id, agent_name, session_id
    (the chat it was started from), depth, report_back, exchanges}"""

    async def save(self) -> Self:
        """Save, then refresh this chat's row in the chat-list index."""
        result = await super().save()
        from cognitrix.sessions import index as chat_index

        await chat_index.record(self)
        return result

    @classmethod
    async def update_one(cls, conditions: dict[str, Any], data: dict[str, Any]) -> int:
        result = await super().update_one(conditions, data)
        if conditions.get('id'):
            from cognitrix.sessions import index as chat_index

            await chat_index.refresh(str(conditions['id']))
        return result

    @classmethod
    async def delete_many(cls, conditions: dict[str, Any], cascade: bool = False):
        result = await super().delete_many(conditions, cascade)
        if conditions.get('id'):
            from cognitrix.sessions import index as chat_index

            await chat_index.forget(conditions['id'])
        return result

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
        from cognitrix.sessions import index as chat_index

        await chat_index.forget(session_id)
        return True


    @property
    def last_turn_error(self) -> str | None:
        """Why the most recent turn failed, or None if it produced an answer."""
        return self.__dict__.get('_last_turn_error')

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

    async def _maybe_compact_turn(self, agent: 'Agent') -> bool:
        """Fold the older tool rounds of the running turn into a progress summary.

        Runs between tool rounds, so a long task keeps going instead of
        outgrowing the model's window. Nothing is deleted: folded messages
        are marked ``folded`` (the chat UI still shows them, the prompt
        skips them) and a ``progress_summary`` message takes their place in
        the prompt. If the summarizer fails, nothing changes and the prompt
        builder falls back to shortening old results (fit_current_turn).
        """
        from cognitrix.utils.tokens import estimate_tokens

        llm = agent.llm
        budget = max(2000, llm.get_context_window() - llm.max_tokens - 2000)
        live = [(i, m) for i, m in enumerate(self.chat) if not m.get('folded')]
        start = None
        for k in range(len(live) - 1, -1, -1):
            m = live[k][1]
            if str(m.get('role', '')).lower() == 'user' and m.get('type', 'text') in ('text', 'summary'):
                start = k
                break
        if start is None:
            return False
        turn = live[start:]
        if estimate_tokens([m for _, m in turn]) <= budget * TURN_COMPACT_THRESHOLD:
            return False

        earlier_summaries: list[tuple[int, dict[str, Any]]] = []
        rounds: list[list[tuple[int, dict[str, Any]]]] = []
        for i, m in turn[1:]:
            if m.get('type') == 'progress_summary':
                earlier_summaries.append((i, m))
            elif str(m.get('role', '')).lower() == 'assistant' and m.get('tool_calls'):
                rounds.append([(i, m)])
            elif rounds:
                rounds[-1].append((i, m))
        if len(rounds) <= TURN_KEEP_ROUNDS:
            return False
        fold = rounds[:-TURN_KEEP_ROUNDS]

        # The summarizer's own input has to fit the window too. When the
        # folded rounds are bigger than that, they are summarized in pieces,
        # each piece carrying the summary so far forward, so nothing in the
        # middle is skipped.
        cap = int(min(budget * 0.7, 120_000) * 4)

        def clip(text: str, limit: int) -> str:
            if len(text) <= limit:
                return text
            half = limit // 2
            return text[:half] + f"\n[... {len(text) - limit} chars left out ...]\n" + text[-half:]

        lines: list[str] = []
        for rnd in fold:
            for _, m in rnd:
                content = str(m.get('content') or '')
                if m.get('tool_calls'):
                    calls = '; '.join(
                        f"{tc.get('name')}({json.dumps(tc.get('arguments'), default=str)[:300]})"
                        for tc in m['tool_calls'] if isinstance(tc, dict)
                    )
                    lines.append(f"assistant: {content[:2000]} [called {calls}]".replace(':  [', ': ['))
                elif str(m.get('role', '')).lower() == 'tool':
                    lines.append(f"tool result: {clip(content, cap // 2)}")
                elif m.get('type') == 'steer':
                    lines.append(f"the user, mid-task: {content[:2000]}")
                elif content.strip():
                    lines.append(f"{m.get('role', '')}: {content[:2000]}")
        pieces: list[str] = []
        current = ''
        for line in lines:
            if current and len(current) + len(line) + 1 > cap:
                pieces.append(current)
                current = ''
            current = f"{current}\n{line}" if current else line
        if current:
            pieces.append(current)

        request = str(turn[0][1].get('content') or '')[:4000]
        summary = '\n\n'.join(str(m.get('content') or '') for _, m in earlier_summaries)
        for piece in pieces:
            earlier = f"Progress summary so far:\n{summary}\n\n" if summary else ''
            prompt = [
                {'role': 'system', 'content': _PROGRESS_SUMMARY_PROMPT},
                {'role': 'user', 'content': (
                    f"The request:\n{request}\n\n{earlier}"
                    f"Work done since then:\n{piece}\n\n"
                    "Write the updated progress summary, covering all of the above."
                )},
            ]
            resp = await llm(prompt, stream=False)
            text = ''
            if resp and not getattr(resp, 'error', None):
                text = (resp.llm_response or '').strip()
            if not text:
                logger.warning("Turn compaction skipped: summarizer returned no summary")
                return False
            summary = text

        for i, m in [*earlier_summaries, *(item for rnd in fold for item in rnd)]:
            # The person's own mid-task messages stay word for word.
            if m.get('type') == 'steer':
                continue
            self.chat[i] = {**m, 'folded': True}
        insert_at = rounds[-TURN_KEEP_ROUNDS][0][0]
        self.chat.insert(insert_at, {
            'role': 'user',
            'type': 'progress_summary',
            'content': (
                '[Progress so far on this request. Earlier tool calls were summarized '
                'to save room; their full results are no longer visible to you.]\n' + summary
            ),
        })
        logger.info("Compacted the running turn: folded %s tool rounds into a summary", len(fold))
        return True

    async def __call__(self, message: str | dict, agent: 'Agent', *args: Any, **kwargs: Any):
        # The action reviewer reads what this turn was asked to do.
        from cognitrix import usage
        from cognitrix.safety.action_reviewer import _goal_text, turn_goal

        token = turn_goal.set(_goal_text(message))
        # Model calls in this turn count toward this person's usage totals.
        owner = self.user_id or getattr(kwargs.get('tool_context'), 'user_id', None)
        usage_token = usage.set_owner(owner, getattr(agent, 'id', None))
        try:
            return await self._run_turn(message, agent, *args, **kwargs)
        finally:
            usage.reset_owner(usage_token)
            turn_goal.reset(token)

    async def _run_turn(self, message: str|dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws', 'compat'] = 'cli', stream: bool = False, output: Callable = print, wsquery: dict[str, str] | None= None, save_history: bool = True, attachments: dict[str, Any] | None = None, tool_context=None, record_history: bool | None = None, persist_history: bool | None = None, compact_history: bool | None = None):

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
        self.__dict__['_last_turn_error'] = None
        self.__dict__['_empty_nudged'] = False

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

        # Request Folder lets the agent ask (with approval) for more folders.
        # Like Ask User it's added per turn, not stored on the agent.
        if interface in ('web', 'ws', 'task') and getattr(agent, 'can_request_folders', True):
            try:
                if not any(str(getattr(t, 'name', '')).lower() == 'request folder' for t in agent.tools):
                    from cognitrix.tools.base import ToolManager
                    folder_tool = ToolManager.get_by_name('request_folder')
                    if folder_tool:
                        agent = agent.model_copy(update={'tools': [*agent.tools, folder_tool]})
            except Exception:
                logger.debug('Request Folder tool unavailable', exc_info=True)

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

        # Memory (save, update, forget what the agent remembers about the
        # person) and Search Chats / Read Chat (look through past chats) are
        # added per turn for signed-in chats while their switches are on, and
        # never in an incognito chat.
        owner = self.user_id or getattr(tool_context, 'user_id', None)
        if interface in ('web', 'ws') and owner and not self.task_id:
            try:
                from cognitrix.memory.saved import get_settings
                from cognitrix.tools.base import ToolManager

                settings = await get_settings(str(owner))
                incognito = bool(getattr(self, 'incognito', False))
                wanted = {
                    'memory': settings.enabled and not incognito,
                    'search chats': settings.history_enabled and not incognito,
                    'read chat': settings.history_enabled and not incognito,
                }
                tools = [t for t in agent.tools
                         if wanted.get(str(getattr(t, 'name', '')).lower(), True)]
                names = {str(getattr(t, 'name', '')).lower() for t in tools}
                for name, on in wanted.items():
                    if on and name not in names:
                        extra = ToolManager.get_by_name(name.replace(' ', '_'))
                        if extra:
                            tools.append(extra)
                if len(tools) != len(agent.tools) or any(a is not b for a, b in zip(tools, agent.tools, strict=False)):
                    agent = agent.model_copy(update={'tools': tools})
            except Exception:
                logger.debug('Memory tools unavailable', exc_info=True)

        # Artifacts: pages the agent publishes beside the chat (Publish
        # Artifact, Read Artifact, Artifact Comments). Signed-in turns only:
        # a page belongs to a person. Tool-less models don't get them.
        if interface in ('web', 'ws', 'task') and owner and getattr(agent.llm, 'supports_tool_use', True):
            try:
                from cognitrix.tools.artifact_pages import ARTIFACT_TOOL_CATEGORY
                from cognitrix.tools.base import ToolManager
                from cognitrix.tools.hosted_apps import APP_TOOL_CATEGORY

                names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                # Apps (Publish App, App Status) ride along with artifacts.
                extra = [t for category in (ARTIFACT_TOOL_CATEGORY, APP_TOOL_CATEGORY)
                         for t in ToolManager.get_tools_by_category(category)
                         if t.name.lower() not in names]
                if extra:
                    agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
            except Exception:
                logger.debug('Artifact tools unavailable', exc_info=True)

        # Show Card: numbers, a table, a chart and buttons drawn in the chat
        # (cognitrix/cards). Any chat or task run with a tool-capable model.
        if interface in ('web', 'ws', 'task') and getattr(agent.llm, 'supports_tool_use', True):
            try:
                from cognitrix.tools.base import ToolManager

                if not any(str(getattr(t, 'name', '')).lower() == 'show card' for t in agent.tools):
                    card_tool = ToolManager.get_by_name('show_card')
                    if card_tool:
                        agent = agent.model_copy(update={'tools': [*agent.tools, card_tool]})
            except Exception:
                logger.debug('Show Card tool unavailable', exc_info=True)
        # Set Theme lets an agent change the app's colours for the person
        # (cognitrix/tools/theme.py); the chat shows each change with Undo.
        if interface in ('web', 'ws') and owner and not self.task_id and getattr(agent.llm, 'supports_tool_use', True):
            try:
                if not any(str(getattr(t, 'name', '')).lower() == 'set theme' for t in agent.tools):
                    from cognitrix.tools.base import ToolManager
                    theme_tool = ToolManager.get_by_name('set_theme')
                    if theme_tool:
                        agent = agent.model_copy(update={'tools': [*agent.tools, theme_tool]})
            except Exception:
                logger.debug('Set Theme tool unavailable', exc_info=True)

        # Propose Skill lets an agent suggest saving how it did a job; the
        # person reviews the draft before it is used (skills/drafts.py).
        if interface in ('web', 'ws') and owner and not self.task_id and not getattr(self, 'incognito', False):
            try:
                if not any(str(getattr(t, 'name', '')).lower() == 'propose skill' for t in agent.tools):
                    from cognitrix.tools.base import ToolManager
                    draft_tool = ToolManager.get_by_name('propose_skill')
                    if draft_tool:
                        agent = agent.model_copy(update={'tools': [*agent.tools, draft_tool]})
            except Exception:
                logger.debug('Propose Skill tool unavailable', exc_info=True)

        # The person's own skills are listed next to the shared ones in the
        # system prompt for this turn (skills/personal.py).
        if owner:
            try:
                from cognitrix.safety.permissions import guest_run
                from cognitrix.skills import personal

                # Not for a guest: someone else asking in a shared channel.
                personal.personal_skills_ctx.set(
                    [] if guest_run.get() else await personal.summaries(str(owner)))
            except Exception:
                logger.debug('Personal skills unavailable', exc_info=True)

        # Search Knowledge is offered only to agents that were given at least
        # one document collection.
        try:
            if getattr(agent, 'knowledge', None) and not any(
                str(getattr(t, 'name', '')).lower() == 'search knowledge' for t in agent.tools
            ):
                from cognitrix.tools.base import ToolManager
                knowledge_tool = ToolManager.get_by_name('search_knowledge')
                if knowledge_tool:
                    agent = agent.model_copy(update={'tools': [*agent.tools, knowledge_tool]})
        except Exception:
            logger.debug('Search Knowledge tool unavailable', exc_info=True)

        # Bash's background mode and the Coding Agent tool need the two
        # background helpers; agents that have either get them for the turn
        # without changing the saved agent.
        try:
            names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
            if names & {'bash', 'coding agent'}:
                from cognitrix.tools.base import ToolManager
                helpers = [
                    t for t in (ToolManager.get_by_name(n) for n in ('bash_output', 'kill_shell'))
                    if t is not None and t.name.lower() not in names
                ]
                if helpers:
                    agent = agent.model_copy(update={'tools': [*agent.tools, *helpers]})
        except Exception:
            pass

        # Add the new message to the history before building the prompt
        if wsquery is None:
            wsquery = {}
        if record_history:
            entry = agent.process_prompt(message)
            # Regenerate or edit of a turn that keeps its uploads: the message
            # is already in the history (marked by the rewind), so update it
            # in place and answer it again without adding a second copy.
            kept = next(
                (m for m in reversed(self.chat[-12:]) if isinstance(m, dict) and m.get('resume')),
                None,
            ) if isinstance(entry, dict) and not attachments else None
            if kept is not None:
                kept.pop('resume', None)
                kept['content'] = entry.get('content', kept.get('content'))
            else:
                self.update_history(entry)
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

        # Working folders for this turn: the chat's own list, or (first turn)
        # a copy of the agent's defaults. A delegated turn keeps the folders
        # its parent passed down (read only).
        turn_folders = self._turn_folders(agent, interface)
        inherited_folders = tuple(getattr(tool_context, 'folders', ()) or ())

        # Build the context-aware prompt using the manager
        prompt = await agent.get_context_manager().build_prompt(agent, self)
        prompt = _with_folder_prompt(prompt, inherited_folders or tuple(turn_folders))

        # Tools set to "never" for this agent aren't offered to the model
        # (the agent loop refuses them too, see safety/permissions.py).
        from cognitrix.safety.permissions import visible_tools
        formatted_tools = [
            tool.to_dict_format() for tool in visible_tools(agent, agent.tools)
            if not tool.supported_interfaces or interface in tool.supported_interfaces
        ]
        # Only advertise tools to models with native tool-use; otherwise the tool
        # list is embedded in the system prompt (AgentManager.formatted_system_prompt).
        active_tools = formatted_tools if agent.llm.supports_tool_use else None

        tool_rounds = 0
        active_tool_calls: list[dict[str, Any]] = []
        last_denied_sig = None
        stop_turn = False
        wrap_up_requested = False
        cutoff_continues = 0
        try:
            while True:  # The loop now primarily handles tool calls, not initial message processing
                if tool_rounds >= MAX_TOOL_ROUNDS and not wrap_up_requested and agent.llm.supports_tool_use:
                    # One last call without tools, so the user gets a summary
                    # of the work so far instead of a bare "stopped" notice.
                    logger.warning("Reached max tool rounds (%s); asking for a final answer", MAX_TOOL_ROUNDS)
                    wrap_up_requested = True
                    # Tools stay declared: some providers reject a history
                    # with tool calls when no tools are defined. If the
                    # model calls one anyway, the next check stops the turn.
                    prompt = [*prompt, {
                        'role': 'user',
                        'type': 'text',
                        'content': (
                            f"[System: the tool-call limit ({MAX_TOOL_ROUNDS} rounds) for this turn "
                            "has been reached. Do not call any tools. Reply now with what you did, "
                            "what you found, and what is left to do.]"
                        ),
                    }]
                elif tool_rounds >= MAX_TOOL_ROUNDS:
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

                    # A coding agent CLI provider runs in this chat's folder
                    # and resumes its own session (providers/harness.py).
                    from cognitrix.providers import harness as _harness
                    if _harness.is_harness(agent.llm.provider):
                        from cognitrix.tools.utils import ToolExecutionContext as _TEC
                        _ctx = tool_context or _TEC()
                        _harness.turn.set(_harness.TurnInfo(
                            # Per chat and agent: teammates in a group chat share the
                            # chat id and must not resume each other's CLI session.
                            session_id=f'{self.id}:{agent.id}' if persist_history or record_history else None,
                            folders=tuple(inherited_folders or turn_folders or ()),
                            host_process_mode=_ctx.host_process_mode,
                            tools=tuple(formatted_tools),
                            call_tool=self._cli_tool_caller(
                                agent, interface, output, tool_context, turn_folders, inherited_folders,
                                record_history),
                            approve=lambda name, params: agent.manager.approve_cli_action(
                                _harness.HARNESSES[agent.llm.provider.lower()].label, name, params,
                                interface=interface, folders=tuple(inherited_folders or turn_folders or ()),
                            ),
                        ))

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
                                if status == 'completed':
                                    await self._note_written_file(agent, tool_context, name, data)
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
                                ToolExecutionContext,
                                reset_execution_context,
                                set_execution_context,
                            )
                            bound_context = tool_context or ToolExecutionContext()
                            if turn_folders and not bound_context.folders:
                                import dataclasses as _dc
                                bound_context = _dc.replace(
                                    bound_context, folders=tuple(turn_folders),
                                )
                            from cognitrix.common.folders import reset_grant_sink, set_grant_sink
                            grant_token = set_grant_sink(
                                self._folder_grant_sink(turn_folders)
                                if turn_folders and not inherited_folders else None
                            )
                            artifact_token = set_session(
                                str(self.id), str(agent.id), bound_context.user_id
                            )
                            execution_token = set_execution_context(bound_context)
                            from cognitrix.tools import misc as _misc
                            images_token, round_images = _misc.start_image_collection()
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
                                reset_grant_sink(grant_token)
                                reset_execution_context(execution_token)
                                reset_session(artifact_token)
                                _misc.end_image_collection(images_token)
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
                                    # Screenshots from this round go in as images
                                    # right after the tool results.
                                    for image in round_images:
                                        self.update_history({
                                            'role': 'User', 'type': 'screenshot',
                                            'content': image,
                                        })
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

                                # Messages the person sent while this round ran
                                # go in now, after the tool results and before
                                # compaction, so they are the newest thing the
                                # model reads and are never folded away.
                                if record_history:
                                    await self._take_steering(interface, output, wsquery)

                                if record_history:
                                    try:
                                        await self._maybe_compact_turn(agent)
                                    except ExecutionControlError:
                                        raise
                                    except Exception:
                                        logger.warning("Turn compaction failed", exc_info=True)

                                prompt = await agent.get_context_manager().build_prompt(agent, self)
                                # Keep the folder list (including any just granted) on every request.
                                prompt = _with_folder_prompt(prompt, inherited_folders or tuple(turn_folders))
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
                        self.__dict__['_last_turn_error'] = str(response.error)
                        break

                    # An empty reply right after tool results (Gemini 3.1 Pro
                    # does this mid-task) would end the turn silently. Ask once
                    # more before giving up.
                    if (
                        response and not response.tool_calls and not called_tools
                        and tool_rounds > 0 and not (response.llm_response or '').strip()
                        and not self.__dict__.get('_empty_nudged') and record_history
                    ):
                        self.__dict__['_empty_nudged'] = True
                        self.update_history({
                            'role': 'User', 'type': 'text',
                            'content': '[System: your last reply was empty. Continue the task, '
                                       'or give your final answer.]',
                        })
                        prompt = await agent.get_context_manager().build_prompt(agent, self)
                        prompt = _with_folder_prompt(prompt, inherited_folders or tuple(turn_folders))
                        continue

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

                    if (
                        not called_tools
                        and response
                        and getattr(response, 'finish_reason', None) == 'length'
                        and not response.tool_calls
                        and cutoff_continues < MAX_CUTOFF_CONTINUES
                    ):
                        # The reply hit the output-token limit mid-way (seen
                        # with Gemini thinking out loud before its next tool
                        # call). Without this the cut-off text ended the turn
                        # as if it were the answer.
                        cutoff_continues += 1
                        logger.warning("Reply cut off at the output limit; asking the model to continue")
                        prompt = [*prompt, {
                            'role': 'assistant', 'type': 'text', 'content': response.llm_response or '',
                        }, {
                            'role': 'user', 'type': 'text', 'content': (
                                '[System: your last reply was cut off by the output limit. '
                                'Continue from where it stopped. Keep it short, and if you were '
                                'about to use a tool, call it now.]'
                            ),
                        }]
                        continue

                    if (
                        not called_tools
                        and record_history
                        and not wrap_up_requested
                        and await self._take_steering(interface, output, wsquery)
                    ):
                        # A message arrived while the model was writing its
                        # answer: keep the turn going so it gets read now
                        # instead of waiting for another turn.
                        prompt = await agent.get_context_manager().build_prompt(agent, self)
                        prompt = _with_folder_prompt(prompt, inherited_folders or tuple(turn_folders))
                        continue

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
                            status_print(f"\n[dim]Took {duration_str}[/dim]", markup=True)

                        break # Exit loop if no tools were called

                except ExecutionControlError:
                    raise
                except Exception as e:
                    logger.exception(e)
                    # Tell the user instead of ending the turn silently.
                    error_text = f"Error: the turn failed ({type(e).__name__}: {e})"
                    self.__dict__['_last_turn_error'] = error_text
                    try:
                        if interface == 'cli':
                            output(f"\n{error_text}")
                        else:
                            await output({'type': wsquery.get('type'), 'content': error_text, 'action': wsquery.get('action'), 'complete': False})
                    except Exception:
                        logger.exception("Could not report the turn error")
                    break # Exit on error

            if self.last_turn_error and record_history:
                # Close the failed turn in history. Without this the user's
                # message stayed unanswered, and each retry stacked another
                # identical user message in front of the model.
                self.update_history({
                    'role': 'assistant',
                    'name': agent.name,
                    'type': 'text',
                    'content': f"[No answer: this request failed. {self.last_turn_error[:300]}]",
                    'turn_error': True,
                })

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
                status_print(f"\n[dim]Took {duration_str}[/dim]", markup=True)

            if persist_history:
                await self.save()
            if compact_history:
                try:
                    await self._maybe_compact(agent)
                except Exception:
                    logger.exception("History compaction failed; keeping history as-is")

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

    def _steering_run(self):
        """The chat run whose inbox this turn reads, if any."""
        from cognitrix.sessions import runs as chat_runs

        run = chat_runs.current_run.get()
        # A nested turn (Call Agent, a teammate) runs another session in the
        # same context; only the chat the person is writing to reads the inbox.
        if run is None or not run.running or run.session_id != str(self.id):
            return None
        return run

    async def _take_steering(self, interface: str, output: Callable, wsquery: dict[str, str] | None) -> bool:
        """Move messages sent during the turn into history. True if any."""
        run = self._steering_run()
        if run is None or not run.inbox:
            return False
        messages = run.take_inbox()
        for message in messages:
            self.update_history({
                'role': 'User',
                # Its own type: it belongs to the running turn rather than
                # starting a new one (sessions/context.py partition_turns).
                'type': 'steer',
                'content': message.content,
                'steer_id': message.id,
                'source': message.source,
            })
        if interface != 'cli':
            try:
                await output({
                    'type': 'message_delivered',
                    'ids': [m.id for m in messages],
                    'messages': [m.as_dict() for m in messages],
                    'action': (wsquery or {}).get('action'),
                })
            except Exception:
                logger.warning('Could not report delivered messages', exc_info=True)
        return True

    def _cli_tool_caller(self, agent: 'Agent', interface: str, output: Callable, tool_context,
                         turn_folders: list, inherited_folders: tuple, record_history: bool | None = False):
        """Runs one of the agent's tools for a coding agent CLI (providers/harness.py).

        The CLI calls it over MCP while its turn runs. The call goes through
        the same permission and approval checks, and shows the same tool cards,
        as a tool call from any other model. Returns (text, worked?, images).
        """
        import uuid as _uuid

        async def call(name: str, arguments: dict[str, Any]) -> tuple[str, bool, list[str]]:
            from cognitrix.artifacts import reset_session, set_session
            from cognitrix.common.folders import reset_grant_sink, set_grant_sink
            from cognitrix.tools import misc as _misc
            from cognitrix.tools.utils import (
                ToolExecutionContext,
                reset_execution_context,
                set_execution_context,
            )

            tcid = f'cli_{_uuid.uuid4().hex[:16]}'
            shows_cards = interface in ('web', 'ws', 'task')
            if shows_cards:
                await output({'type': 'tool', 'status': 'started', 'tool_name': name,
                              'tool_call_id': tcid, 'params': _tool_preview(arguments)})
            bound = tool_context or ToolExecutionContext()
            if turn_folders and not bound.folders:
                import dataclasses as _dc
                bound = _dc.replace(bound, folders=tuple(turn_folders))
            grant_token = set_grant_sink(
                self._folder_grant_sink(turn_folders) if turn_folders and not inherited_folders else None
            )
            artifact_token = set_session(str(self.id), str(agent.id), bound.user_id)
            execution_token = set_execution_context(bound)
            images_token, images = _misc.start_image_collection()
            item: dict[str, Any] = {}
            tool_call = {'name': name, 'tool_call_id': tcid, 'arguments': arguments}
            try:
                result = await agent.call_tools([tool_call], interface=interface)
                if isinstance(result, dict) and result.get('result'):
                    item = result['result'][0]
                    if record_history:
                        # The call and its result go in together, so calls the
                        # CLI runs side by side stay paired. Reloading the
                        # chat rebuilds the tool cards from these.
                        self.update_history({
                            'role': 'assistant', 'name': agent.name, 'type': 'tool_calls',
                            'content': '', 'tool_calls': [tool_call],
                        })
                        self.update_history(agent.process_prompt(result))
            finally:
                reset_grant_sink(grant_token)
                reset_execution_context(execution_token)
                reset_session(artifact_token)
                _misc.end_image_collection(images_token)
                if shows_cards:
                    outcome = item.get('outcome')
                    worked = (outcome.get('status') == 'success') if isinstance(outcome, dict) \
                        else item.get('success') is True
                    preview = outcome.get('text', item.get('data', '')) if isinstance(outcome, dict) \
                        else item.get('data', 'Stopped')
                    await output({
                        'type': 'tool', 'status': 'completed' if worked else 'error', 'tool_name': name,
                        'tool_call_id': tcid, 'result': _tool_preview(preview), 'outcome': outcome,
                        'artifacts': (outcome or {}).get('artifacts', []),
                    })
            return str(item.get('data', '')), item.get('success') is True, list(images)

        return call

    async def _note_written_file(self, agent: 'Agent', tool_context: Any, name: str | None, data: Any) -> None:
        """List a document the agent wrote on the person's Artifacts page."""
        try:
            from cognitrix.artifact_pages.store import record_written_file, written_path

            path = written_path(name, data)
            owner = self.user_id or getattr(tool_context, 'user_id', None)
            if not path or not owner:
                return
            await record_written_file(
                owner_id=str(owner), path=path, agent_id=str(getattr(agent, 'id', '') or ''),
                session_id='' if self.task_id else str(self.id), task_id=str(self.task_id or ''),
            )
        except Exception:
            logger.debug('Could not list a written file on the Artifacts page', exc_info=True)

    def _turn_folders(self, agent: 'Agent', interface: str) -> list:
        """This chat's working folders, copying the agent's defaults on first use.

        A new chat starts with only its private scratch folder unless the agent
        has its own default folders. CLI and compat turns keep the app's working
        folder (an empty list).
        """
        if interface not in ('web', 'ws', 'task'):
            return []
        from cognitrix.common.folders import (
            folders_to_dicts,
            normalize_folders,
            scratch_folder,
        )
        source = self.folders
        if source is None:
            source = list(getattr(agent, 'folders', None) or [])
        folders = normalize_folders(source, validate=False, scratch=self.scratch_path())
        if not folders:
            folders = [scratch_folder(self.scratch_key())]
        self.folders = folders_to_dicts(folders)
        return folders

    def scratch_key(self) -> str:
        return f"task-{self.task_id}" if self.task_id else f"chat-{self.id or 'new'}"

    def scratch_path(self) -> str:
        """This chat's own scratch folder (the only scratch folder it may list)."""
        from cognitrix.common.folders import scratch_path
        return str(scratch_path(self.scratch_key()))

    def _folder_grant_sink(self, turn_folders: list):
        """Apply an approved Request Folder to this chat (and this turn)."""
        from cognitrix.common.folders import folders_to_dicts, merge_folder

        def sink(folder) -> None:
            updated = merge_folder(turn_folders, folder)
            turn_folders[:] = updated
            self.folders = folders_to_dicts(updated)
        return sink


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
