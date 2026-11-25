import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional, Self

from odbms import Model
from pydantic import Field

from cognitrix.app_settings import get as app_setting
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

# Max tool-call rounds in a single turn before the loop stops (guards against a
# model that never stops calling tools). 10 was too low for build-style steps
# (creating several files is >10 tool calls), which cut steps off mid-work;
# 100 fits long multi-file/agentic turns while still bounding runaway loops. Configurable.
# 0 (the default) means no limit on tool rounds in one reply.
MAX_TOOL_ROUNDS = int(app_setting('COGNITRIX_MAX_TOOL_ROUNDS', '0') or 0)

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


_CODE_TOOLS = frozenset({'bash', 'read', 'write', 'edit', 'glob', 'grep', 'coding agent', 'run tests'})


def _turn_helper_names(names: set[str]) -> tuple[str, ...]:
    """Tools that come along for a turn with the (lowercase) tools an agent has."""
    helpers: tuple[str, ...] = ()
    if names & {'bash', 'coding agent'}:
        helpers += ('bash_output', 'kill_shell')
    if 'look at window' in names:
        helpers += ('show_screenshot',)
    return helpers


def _code_tool_names(agent: Any) -> set[str]:
    """The shell, file and coding tools the agent has itself."""
    return {str(getattr(t, 'name', '')).lower() for t in getattr(agent, 'tools', None) or []} & _CODE_TOOLS


def _with_folder_prompt(prompt, folders, sandbox: tuple | None = None, session_id: Any = None) -> Any:
    """The turn's additions to the system message: working folders, what
    open connected-app screens told the agent, and the Swarm switch when the
    person sent this message with it on."""
    prompt = _with_folder_block(prompt, folders, sandbox)
    prompt = _with_app_screens(prompt, session_id)
    prompt = _with_computer_note(prompt)
    try:
        from cognitrix.tools.swarm import SWARM_MODE_NOTE, swarm_requested

        if not swarm_requested.get() or not isinstance(prompt, list):
            return prompt
    except Exception:
        return prompt
    if prompt and isinstance(prompt[0], dict) and prompt[0].get('role') == 'system' \
            and isinstance(prompt[0].get('content'), str):
        return [{**prompt[0], 'content': prompt[0]['content'] + '\n\n' + SWARM_MODE_NOTE}, *prompt[1:]]
    return [{'role': 'system', 'content': SWARM_MODE_NOTE}, *prompt]


def _with_app_screens(prompt, session_id: Any) -> Any:
    """Add what connected-app screens beside the chat reported (MCP Apps
    ``ui/update-model-context``) to the system message."""
    if not isinstance(prompt, list) or not session_id:
        return prompt
    try:
        from cognitrix.mcp.app_views import context_note

        block = context_note(str(session_id))
    except Exception:
        return prompt
    if not block:
        return prompt
    if prompt and isinstance(prompt[0], dict) and prompt[0].get('role') == 'system' \
            and isinstance(prompt[0].get('content'), str):
        return [{**prompt[0], 'content': prompt[0]['content'] + '\n\n' + block}, *prompt[1:]]
    return [{'role': 'system', 'content': block}, *prompt]


def _with_folder_block(prompt, folders, sandbox: tuple | None = None) -> Any:
    """Add the working-folders block to the system message. With Run in
    sandbox (``sandbox`` is (agent id, user id)), the block says the shell and
    files are in the sandbox and the folders can be copied in."""
    if not isinstance(prompt, list):
        return prompt
    if not folders and sandbox is None:
        # CLI and API turns work in the app's working folder: its AGENTS.md
        # still applies.
        block = _agents_md_block(())
        return _with_note(prompt, block) if block else prompt
    if isinstance(sandbox, dict) and sandbox.get('device'):
        # A paired PC: its own shared folders, not the chat's.
        from cognitrix.computer.paired.turn import device_prompt
        block = device_prompt(sandbox['device'])
    elif sandbox is not None:
        from cognitrix.common.sandbox_work import sandbox_prompt
        block = sandbox_prompt(folders, *sandbox)
        # The folders are copied into the sandbox: their AGENTS.md comes along.
        block = '\n\n'.join(b for b in (block, _agents_md_block(folders)) if b)
    else:
        from cognitrix.common.folders import folder_prompt
        block = '\n\n'.join(b for b in (folder_prompt(folders), _agents_md_block(folders)) if b)
    if not block:
        return prompt
    if prompt and isinstance(prompt[0], dict) and prompt[0].get('role') == 'system' \
            and isinstance(prompt[0].get('content'), str):
        return [{**prompt[0], 'content': prompt[0]['content'] + '\n\n' + block}, *prompt[1:]]
    return [{'role': 'system', 'content': block}, *prompt]


def _agents_md_block(folders) -> str:
    """AGENTS.md instructions from the turn's working folders (the app's
    working folder when the turn has none). A project's instructions that
    were read from the same file aren't repeated."""
    try:
        from cognitrix.common.agents_md import instructions_block
        from cognitrix.common.folders import legacy_default_folders
        from cognitrix.projects.context import get as _turn_project

        project = _turn_project()
        skip = [project.instructions] if project is not None and project.instructions else []
        return instructions_block(tuple(folders) or legacy_default_folders(), skip_texts=skip)
    except Exception:
        logger.debug('AGENTS.md unavailable', exc_info=True)
        return ''


def _with_computer_note(prompt) -> Any:
    """Tell the agent to do computer and browser work on its own sandboxed
    computer, not the person's, while it has one."""
    if not isinstance(prompt, list):
        return prompt
    try:
        from cognitrix.tools.computer import computer_note

        note = computer_note()
    except Exception:
        return prompt
    return _with_note(prompt, note)


def _with_note(prompt, note: str) -> Any:
    """Append a line to the system message (or add one)."""
    if not note:
        return prompt
    if prompt and isinstance(prompt[0], dict) and prompt[0].get('role') == 'system' \
            and isinstance(prompt[0].get('content'), str):
        return [{**prompt[0], 'content': prompt[0]['content'] + '\n\n' + note}, *prompt[1:]]
    return [{'role': 'system', 'content': note}, *prompt]


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

    loaded_tools: list[str] | None = None
    """Tools Find Tools loaded in this chat (agents with tools on demand,
    tools/code_mode.py). Their schemas are sent on every later step."""

    run_in_sandbox: bool | None = None
    """Run in sandbox: this chat's shell, files and tests work in the person's
    sandboxed computer. None follows the agent's own setting."""

    computer: str | None = None
    """A paired PC's id (cognitrix/computer/paired): this chat's shell and
    file tools run on the person's own PC. None: the usual computer."""

    project_id: str | None = None
    """The project this chat belongs to (cognitrix/projects): its
    instructions, folders, knowledge, memories and computer apply."""

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
    """A name the person gave this chat. None: the list shows ``auto_title``,
    or the first message until there is one."""

    auto_title: str | None = None
    """A short title the model wrote after the first reply (sessions/titles.py)."""

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

    public_link_id: str | None = None
    """Set for a visitor's chat on an agent's public link (cognitrix/public_agents).
    ``user_id`` is still the agent's owner (usage and limits count there), but
    the chat is not the owner's: it has none of their memories, skills or
    tools, and it never shows in their chat list."""

    visitor_key: str | None = None
    """Hash of the visitor token that may read and continue a public-link chat."""

    token_usage: dict[str, Any] | None = None
    """Tokens this chat's turns used, by model, for the chat's side panel:
    {models, last_turn, context}. See cognitrix/usage/chat.py."""

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
        # Nothing is dropped here: a saved chat keeps every message. (It used
        # to keep the last 1,000, so long agent chats lost their beginning for
        # good.) What the model is sent is kept small by compaction instead.

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
        from cognitrix.usage import chat as chat_usage
        with chat_usage.side_call():
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
            from cognitrix.usage import chat as chat_usage
            with chat_usage.side_call():
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
        from cognitrix.memory import guard as memory_guard
        from cognitrix.safety.action_reviewer import _goal_text, turn_goal
        from cognitrix.sessions.resume import ContinueTurn

        # A turn picked up after a restart has no new message: its goal is
        # still the request it was answering (sessions/resume.py).
        token = turn_goal.set(_goal_text(message.goal if isinstance(message, ContinueTurn) else message))
        # Memory saves after reading web or app content need the person's OK.
        guard_token = memory_guard.start_turn()
        # Model calls in this turn count toward this person's usage totals.
        owner = self.user_id or getattr(kwargs.get('tool_context'), 'user_id', None)
        usage_token = usage.set_owner(owner, getattr(agent, 'id', None))
        # ...and toward this chat's own token counts (side panel).
        from cognitrix.usage import chat as chat_usage
        sink_token = usage.set_sink(chat_usage.turn_sink(self, getattr(agent, 'llm', None), usage.current_sink()))
        # A chat or task in a project: its instructions, knowledge, memories
        # and computer apply to everything this turn does (projects/context.py).
        from cognitrix.projects import context as project_context
        try:
            turn_project = await project_context.for_session(self, owner)
        except Exception:
            logger.debug('Could not load the chat project', exc_info=True)
            turn_project = None
        project_token = project_context.set_project(turn_project)
        try:
            return await self._run_turn(message, agent, *args, **kwargs)
        finally:
            project_context.reset(project_token)
            usage.reset_sink(sink_token)
            usage.reset_owner(usage_token)
            memory_guard.end_turn(guard_token)
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
        # Wall clock, for finding the files this turn makes (produced_files).
        turn_wall_start = time.time()
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
                    # Come back to this chat later (cognitrix/assistant/followups.py).
                    'follow up later': not incognito and getattr(agent.llm, 'supports_tool_use', True),
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

        # Live calls (cognitrix/meetings/live.py): Join Meeting and friends,
        # while the agents' sandboxed computer is on (the call runs in its browser).
        if interface in ('web', 'ws', 'task') and owner and getattr(agent.llm, 'supports_tool_use', True):
            try:
                from cognitrix.computer.sandbox.client import sandbox_mode

                if sandbox_mode():
                    from cognitrix.tools.base import ToolManager
                    from cognitrix.tools.meetings import MEETING_TOOL_CATEGORY

                    names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                    extra = [t for t in ToolManager.get_tools_by_category(MEETING_TOOL_CATEGORY)
                             if t.name.lower() not in names]
                    if extra:
                        agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
            except Exception:
                logger.debug('Meeting tools unavailable', exc_info=True)

        # Payments (cognitrix/payments): Request Payment, Pay and Cancel
        # Payment, only when the person turned payments on and allows this agent.
        if interface in ('web', 'ws', 'task') and owner and getattr(agent.llm, 'supports_tool_use', True):
            try:
                from cognitrix.payments.service import agent_may_ask

                if await agent_may_ask(str(owner), str(getattr(agent, 'id', '') or '')):
                    from cognitrix.tools.base import ToolManager
                    from cognitrix.tools.payments import PAYMENT_TOOL_CATEGORY

                    names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                    extra = [t for t in ToolManager.get_tools_by_category(PAYMENT_TOOL_CATEGORY)
                             if t.name.lower() not in names]
                    if extra:
                        agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
            except Exception:
                logger.debug('Payment tools unavailable', exc_info=True)

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

        # Send Email: an agent with its own email address (agent email,
        # cognitrix/agent_email) can write from it. Asks before each send.
        if interface in ('web', 'ws', 'task') and owner and getattr(agent.llm, 'supports_tool_use', True):
            try:
                if not any(str(getattr(t, 'name', '')).lower() == 'send email' for t in agent.tools):
                    from cognitrix.tools.agent_mail import agent_has_address
                    from cognitrix.tools.base import ToolManager

                    if await agent_has_address(str(owner), str(agent.id)):
                        mail_tool = ToolManager.get_by_name('send_email')
                        if mail_tool:
                            agent = agent.model_copy(update={'tools': [*agent.tools, mail_tool]})
            except Exception:
                logger.debug('Send Email tool unavailable', exc_info=True)

        # Swarms (cognitrix/swarms): a chat's agent can start workers with
        # Spawn Workers and steer them with Swarm; a worker's own chat gets
        # Swarm Team instead. Added per turn, not stored on the agent.
        if (interface in ('web', 'ws') and owner and not self.task_id and not self.group_chat
                and not getattr(self, 'incognito', False) and getattr(agent.llm, 'supports_tool_use', True)):
            try:
                from cognitrix.tools.base import ToolManager
                from cognitrix.tools.swarm import SWARM_TOOL_CATEGORY, SWARM_WORKER_TOOL_CATEGORY

                category = None
                if (self.started_by or {}).get('swarm_id'):
                    category = SWARM_WORKER_TOOL_CATEGORY
                elif getattr(agent, 'can_create_sub_agents', True) is not False:
                    from cognitrix.swarms.service import get_settings as swarm_settings

                    if (await swarm_settings(str(owner))).enabled:
                        category = SWARM_TOOL_CATEGORY
                if category:
                    names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                    extra = [t for t in ToolManager.get_tools_by_category(category)
                             if t.name.lower() not in names]
                    if extra:
                        agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
            except Exception:
                logger.debug('Swarm tools unavailable', exc_info=True)

        # The message board (cognitrix/board): the Board tool, and a short
        # note of what's on the board for this agent at the start of the turn.
        try:
            from cognitrix.board.service import board_note_ctx

            board_note_ctx.set('')
        except Exception:
            pass
        if (interface in ('web', 'ws', 'task') and owner and not getattr(self, 'incognito', False)
                and not getattr(self, 'public_link_id', None) and getattr(agent.llm, 'supports_tool_use', True)
                and not getattr(tool_context, 'restricted', False)):
            try:
                from cognitrix.safety.permissions import guest_run

                if not guest_run.get():
                    agent = await self._board_turn(agent, str(owner), tool_context)
            except Exception:
                logger.debug('Message board unavailable', exc_info=True)

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
        # one document collection (their own, or their project's).
        try:
            from cognitrix.projects.context import get as _turn_project
            from cognitrix.projects.context import knowledge_for
            if _turn_project() is not None:
                merged = knowledge_for(agent)
                if merged != list(getattr(agent, 'knowledge', None) or []):
                    agent = agent.model_copy(update={'knowledge': merged})
            if getattr(agent, 'knowledge', None):
                from cognitrix.tools.base import ToolManager

                names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                extra = [tool for tool in (ToolManager.get_by_name(n) for n in ('search_knowledge', 'learn_from_website'))
                         if tool and tool.name.lower() not in names]
                if extra:
                    agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
        except Exception:
            logger.debug('Search Knowledge tool unavailable', exc_info=True)

        # Run in sandbox: the shell and file tools work in the sandboxed
        # computer, so the turn gets them (and the sandbox-only tools) even
        # when the agent doesn't have them; nothing there reaches this machine.
        # A paired PC picked for this chat (the computer chip): the shell and
        # file tools run there instead, so the sandbox sits this turn out.
        turn_device = None
        if self.computer and interface in ('web', 'ws', 'task') and not getattr(tool_context, 'restricted', False) \
                and not getattr(tool_context, 'sandbox', False):
            try:
                from cognitrix.computer.paired.turn import chat_device
                turn_device = await chat_device(self, getattr(tool_context, 'user_id', None) or self.user_id)
            except Exception:
                logger.debug('Could not load the paired PC', exc_info=True)
        elif getattr(tool_context, 'device_read_only', False) and getattr(tool_context, 'device', None):
            # A helper agent of a chat on a paired PC: it looks at that PC.
            try:
                from cognitrix.computer.paired.turn import device_view
                turn_device = await device_view(getattr(tool_context, 'user_id', None) or self.user_id,
                                                tool_context.device, read_only=True)
            except Exception:
                logger.debug('Could not load the paired PC', exc_info=True)
        turn_sandbox = False if turn_device else self._turn_sandbox(agent, interface, tool_context)
        sandbox_for_prompt: Any = (
            (agent.id, getattr(tool_context, 'user_id', None) or self.user_id) if turn_sandbox else None)
        if turn_device:
            try:
                # The AGENTS.md in the PC's first shared folder, read there.
                from cognitrix.common.agents_md import pc_instructions
                turn_device = {**turn_device, 'agents_md': await pc_instructions(turn_device)}
            except Exception:
                logger.debug('Could not read AGENTS.md on the PC', exc_info=True)
            sandbox_for_prompt = {'device': turn_device}
            try:
                from cognitrix.computer.paired.turn import tools_for
                from cognitrix.tools.base import ToolManager
                have = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                extra = [t for t in (ToolManager.get_by_name(n) for n in tools_for(turn_device))
                         if t is not None and t.name.lower() not in have]
                if extra:
                    agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
            except Exception:
                logger.debug('PC tools unavailable', exc_info=True)
        if turn_sandbox:
            try:
                from cognitrix.tools.base import ToolManager
                from cognitrix.tools.sandbox_work import SANDBOX_TOOL_NAMES
                have = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
                # The shell and file tools come along only when the chat or
                # the agent chose the sandbox. When the agent has them itself,
                # it may move to the person's computer if they ask for it.
                chosen = self._sandbox_chosen(agent) and not getattr(tool_context, 'sandbox', False)
                wanted = ('bash', 'read', 'write', 'edit', 'glob', 'grep') if chosen else ()
                gives_shell = any(ToolManager.get_by_name(n) is not None and ToolManager.get_by_name(n).name.lower()
                                  not in have for n in wanted)
                switch = () if gives_shell or getattr(tool_context, 'sandbox', False) else ('switch_computer_for_code',)
                extra = [
                    t for t in (ToolManager.get_by_name(n) for n in (*wanted, *SANDBOX_TOOL_NAMES, *switch))
                    if t is not None and t.name.lower() not in have
                ]
                if extra:
                    agent = agent.model_copy(update={'tools': [*agent.tools, *extra]})
            except Exception:
                logger.debug('sandbox tools unavailable', exc_info=True)

        # Bash's background mode and the Coding Agent tool need the two
        # background helpers, and agents saved before Show Screenshot existed
        # still have the window tools it goes with. They get these for the
        # turn without changing the saved agent.
        try:
            names = {str(getattr(t, 'name', '')).lower() for t in agent.tools}
            wanted_helpers = _turn_helper_names(names)
            if wanted_helpers:
                from cognitrix.tools.base import ToolManager
                helpers = [
                    t for t in (ToolManager.get_by_name(n) for n in wanted_helpers)
                    if t is not None and t.name.lower() not in names
                ]
                if helpers:
                    agent = agent.model_copy(update={'tools': [*agent.tools, *helpers]})
        except Exception:
            pass

        # Code mode / tools on demand (Agent.tool_mode, tools/code_mode.py):
        # Run Code and Find Tools join the turn when the agent's switches
        # are on.
        try:
            from cognitrix.tools import code_mode as _code_mode
            meta_tools = _code_mode.turn_tools(agent)
            if meta_tools:
                agent = agent.model_copy(update={'tools': [*agent.tools, *meta_tools]})
        except Exception:
            logger.debug('Code mode tools unavailable', exc_info=True)

        # Add the new message to the history before building the prompt
        if wsquery is None:
            wsquery = {}
        from cognitrix.sessions.resume import ContinueTurn

        continuing = isinstance(message, ContinueTurn)
        if record_history and not continuing:
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
        try:
            from cognitrix.produced_files.service import turn_key, turn_started
            turn_started(turn_key(self.id, getattr(tool_context, 'task_id', None) or self.task_id), turn_wall_start)
        except Exception:
            logger.debug('Could not note when the turn started', exc_info=True)

        # Artifacts: how and when to deliver work as an artifact, and the ones
        # this chat or task already made (artifact_pages/guidance.py). Only
        # for agents that have the artifact tools this turn.
        artifact_block = ''
        if any(str(getattr(t, 'name', '')).lower() == 'publish artifact' for t in agent.tools):
            try:
                from cognitrix.artifact_pages.guidance import context_block

                turn_task = str(getattr(tool_context, 'task_id', '') or self.task_id or '')
                artifact_block = await context_block(
                    str(owner) if owner else None,
                    session_id='' if turn_task else str(self.id or ''), task_id=turn_task)
            except Exception:
                logger.debug('Artifact guidance unavailable', exc_info=True)

        # Build the context-aware prompt using the manager
        turn_note = ''
        try:
            from cognitrix.projects.context import prompt_note
            turn_note = prompt_note()
        except Exception:
            logger.debug('Project note unavailable', exc_info=True)
        prompt = await agent.get_context_manager().build_prompt(agent, self)
        prompt = _with_note(_with_note(_with_folder_prompt(prompt, inherited_folders or tuple(turn_folders), sandbox_for_prompt, self.id), artifact_block), turn_note)

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

        # Tools on demand: only the core tools and the ones Find Tools loaded
        # (in this chat so far, or this turn) are sent; the rest are named in
        # the system prompt. Loaded tools stay loaded for the chat, so later
        # turns keep the same tool list (and the provider's prompt cache).
        from cognitrix.tools import code_mode as _code_mode
        # Only when Find Tools can actually be offered and run this turn: a
        # guest or public-link turn can't run it, so it gets every allowed tool.
        on_demand = (
            agent.llm.supports_tool_use
            and not _code_mode.restricted_turn()
            and any(_code_mode._key(_code_mode._schema_name(f)) == 'find tools' for f in formatted_tools)
        )
        turn_loaded: set[str] | None = set(self.loaded_tools or []) if on_demand else None
        loaded_token = _code_mode.loaded_this_turn.set(turn_loaded)
        if on_demand:
            turn_note = _code_mode.find_tools_note(agent)
            prompt = _with_note(prompt, turn_note)

        def _sent_tools() -> list[dict[str, Any]] | None:
            if active_tools is None or turn_loaded is None:
                return active_tools
            if set(self.loaded_tools or []) != turn_loaded:
                self.loaded_tools = sorted(turn_loaded)
            return _code_mode.sent_schemas(formatted_tools, turn_loaded)

        if not continuing:
            # The request is saved before any work starts, so a restart can
            # pick the turn up (sessions/resume.py).
            await self._checkpoint(interface, persist_history)
        tool_rounds = 0
        active_tool_calls: list[dict[str, Any]] = []
        last_denied_sig = None
        stop_turn = False
        wrap_up_requested = False
        cutoff_continues = 0
        try:
            while True:  # The loop now primarily handles tool calls, not initial message processing
                if MAX_TOOL_ROUNDS and tool_rounds >= MAX_TOOL_ROUNDS and not wrap_up_requested and agent.llm.supports_tool_use:
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
                elif MAX_TOOL_ROUNDS and tool_rounds >= MAX_TOOL_ROUNDS:
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
                    from cognitrix.providers.base import turn_llm_override as _turn_override
                    # A CLI picked for this chat in the model picker counts too.
                    _picked = _turn_override.get()
                    _turn_provider = str(
                        _picked[1].provider if _picked is not None and _picked[0] == agent.llm.id
                        else agent.llm.provider
                    ).lower()
                    if _harness.is_harness(_turn_provider):
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
                                record_history, turn_sandbox),
                            approve=lambda name, params: agent.manager.approve_cli_action(
                                _harness.HARNESSES[_turn_provider].label, name, params,
                                interface=interface, folders=tuple(inherited_folders or turn_folders or ()),
                            ),
                        ))

                    # The prompt is now a complete history, not a single message
                    llm_result = await agent.llm(prompt, stream=stream, tools=_sent_tools())

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
                                # Saved before the tools run: after a restart
                                # the turn knows these were started, and marks
                                # them interrupted instead of silently
                                # repeating a side effect (sessions/resume.py).
                                await self._checkpoint(interface, persist_history)
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

                            if interface in ('web', 'ws', 'task', 'acp'):
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
                            if turn_sandbox and not bound_context.sandbox:
                                import dataclasses as _dc
                                bound_context = _dc.replace(bound_context, sandbox=True)
                            if turn_device and not bound_context.device:
                                import dataclasses as _dc
                                bound_context = _dc.replace(bound_context, device=turn_device['id'])
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
                                    if interface in ('web', 'ws', 'task', 'acp'):
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
                            if interface in ('web', 'ws', 'task', 'acp'):
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
                                    # Each finished tool round is saved, so a
                                    # restart loses at most the round running.
                                    await self._checkpoint(interface, persist_history)

                                prompt = await agent.get_context_manager().build_prompt(agent, self)
                                # Keep the folder list (including any just granted) on every request.
                                prompt = _with_note(_with_note(_with_folder_prompt(prompt, inherited_folders or tuple(turn_folders), sandbox_for_prompt, self.id), artifact_block), turn_note)
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
                        prompt = _with_note(_with_note(_with_folder_prompt(prompt, inherited_folders or tuple(turn_folders), sandbox_for_prompt, self.id), artifact_block), turn_note)
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
                        prompt = _with_note(_with_note(_with_folder_prompt(prompt, inherited_folders or tuple(turn_folders), sandbox_for_prompt, self.id), artifact_block), turn_note)
                        continue

                    if not called_tools:
                        # Calculate turn duration
                        turn_duration = time.monotonic() - turn_start_time
                        duration_str = format_duration(turn_duration)

                        # Store timing + token usage in history
                        if record_history:
                            usage = (getattr(response, 'usage', None) or {}) if response else {}
                            made = await self._files_made(
                                agent, interface, output, tool_context, turn_folders, inherited_folders,
                                turn_sandbox, turn_device, turn_wall_start, tool_rounds)
                            self.update_history({
                                'role': 'system',
                                'type': 'turn_timing',
                                'content': f"Took {duration_str}",
                                'duration': turn_duration,
                                'at': datetime.now(timezone.utc).isoformat(),  # when the reply finished
                                'prompt_tokens': usage.get('prompt_tokens'),
                                'completion_tokens': usage.get('completion_tokens'),
                                **({'files': made} if made else {}),
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

            if turn_sandbox and not self.last_turn_error and not getattr(tool_context, 'sandbox', False):
                await self._sandbox_review(agent, interface, output, tool_context, turn_folders,
                                           inherited_folders, record_history)

            # Calculate turn duration for all exit paths
            turn_duration = time.monotonic() - turn_start_time
            duration_str = format_duration(turn_duration)

            # Store timing in history (only if not already stored in 'not called_tools' block)
            if record_history and called_tools:
                usage = (getattr(response, 'usage', None) or {}) if response else {}
                made = await self._files_made(
                    agent, interface, output, tool_context, turn_folders, inherited_folders,
                    turn_sandbox, turn_device, turn_wall_start, tool_rounds)
                self.update_history({
                    'role': 'system',
                    'type': 'turn_timing',
                    'content': f"Took {duration_str}",
                    'duration': turn_duration,
                    'at': datetime.now(timezone.utc).isoformat(),  # when the reply finished
                    'prompt_tokens': usage.get('prompt_tokens'),
                    'completion_tokens': usage.get('completion_tokens'),
                    **({'files': made} if made else {}),
                })

            # Display timing for CLI (only if not already displayed)
            if interface == 'cli' and called_tools:
                status_print(f"\n[dim]Took {duration_str}[/dim]", markup=True)

            if persist_history:
                await self.save()
                if interface in ('web', 'ws'):
                    # Name the chat once its first reply is in (one short call, in the background).
                    from cognitrix.sessions import titles as chat_titles
                    chat_titles.schedule(self, agent)
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
        finally:
            _code_mode.loaded_this_turn.reset(loaded_token)

    async def _checkpoint(self, interface: str, persist_history: bool) -> None:
        """Save a chat turn's progress so far (sessions/resume.py)."""
        if not persist_history or interface not in ('web', 'ws'):
            return
        try:
            await asyncio.shield(self.save())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Could not save chat %s mid-turn', self.id, exc_info=True)

    async def _board_turn(self, agent: 'Agent', owner: str, tool_context: Any) -> 'Agent':
        """Give this turn the Board tool and the board note (board/service.py)."""
        from cognitrix.board import service as board
        from cognitrix.tools.base import ToolManager

        if not (await board.get_settings(owner)).enabled:
            return agent
        if not any(str(getattr(t, 'name', '')).lower() == 'board' for t in agent.tools):
            board_tool = ToolManager.get_by_name('board')
            if board_tool:
                agent = agent.model_copy(update={'tools': [*agent.tools, board_tool]})
        started_by = self.started_by or {}
        sid = str(self.id or '')
        notes = []
        if started_by.get('swarm_id') and started_by.get('worker'):
            notes.append(await board.digest(
                owner, str(agent.id), str(agent.name), board=board.swarm_board(str(started_by['swarm_id'])),
                worker=str(started_by['worker']), chat_id=sid))
        else:
            notes.append(await board.digest(owner, str(agent.id), str(agent.name)))
            # The project's channel too, with this task's thread first (board/channels.py).
            try:
                from cognitrix.projects.context import get as _turn_project

                project = _turn_project()
                project_id = project.id if project is not None else str(self.project_id or '')
                if project_id and str(getattr(project, 'user_id', owner) or owner) == str(owner):
                    thread = f'task:{self.task_id}' if self.task_id else ''
                    notes.append(await board.digest(owner, str(agent.id), str(agent.name),
                                                    board=board.project_board(project_id), thread=thread))
            except Exception:
                logger.debug('Project channel note unavailable', exc_info=True)
            if sid and not self.task_id and not getattr(tool_context, 'run_id', None):
                from cognitrix.swarms import service as swarms

                led = await swarms.latest_for_chat(owner, sid)
                if led is not None and led.status == 'running':
                    notes.append(await board.digest(
                        owner, str(agent.id), str(agent.name), board=board.swarm_board(str(led.id)),
                        worker='lead', chat_id=sid))
        board.board_note_ctx.set('\n\n'.join(n for n in notes if n))
        return agent

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
                         turn_folders: list, inherited_folders: tuple, record_history: bool | None = False,
                         turn_sandbox: bool = False):
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
            if turn_sandbox and not bound.sandbox:
                import dataclasses as _dc
                bound = _dc.replace(bound, sandbox=True)
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

    def _turn_sandbox(self, agent: 'Agent', interface: str, tool_context: Any = None) -> bool:
        """Whether this turn's shell and files work in the sandbox: the chat's
        own switch, else the agent's default. A helper turn follows the turn
        that called it. API keys never get it."""
        if getattr(tool_context, 'sandbox', False):
            return True
        if interface not in ('web', 'ws', 'task') or getattr(tool_context, 'restricted', False):
            return False
        from cognitrix.common.sandbox_work import sandbox_choice, take_chat_switch
        switched = take_chat_switch(self.id)
        if switched is not None:
            # Switch Computer For Code in the last turn: the chat keeps it.
            self.run_in_sandbox = switched
        choice = self.run_in_sandbox
        if choice is None:
            # A project set to the shared sandbox: its chats and tasks code there.
            from cognitrix.projects.context import get as _turn_project
            project = _turn_project()
            if project is not None and project.shared_sandbox:
                choice = True
        on, chosen = sandbox_choice(choice, agent)
        # The default (nobody chose) only moves the tools the agent already
        # has; it never hands an agent a shell it wasn't given.
        return on and (chosen or bool(_code_tool_names(agent)))

    async def _files_made(self, agent: 'Agent', interface: str, output: Callable, tool_context: Any,
                          turn_folders: list, inherited_folders: tuple, turn_sandbox: bool,
                          turn_device: Any, since: float, tool_rounds: int) -> list[dict[str, Any]]:
        """The files this turn made (cognitrix/produced_files): what the agent
        shared plus what turned up where it worked, for the shelf under the
        reply. Sent to the chat as a ``files_made`` event; kept on the turn's
        timing entry so a reloaded chat shows them too."""
        if interface not in ('web', 'ws', 'task') or tool_rounds <= 0 or getattr(tool_context, 'restricted', False):
            return []
        owner = self.user_id or getattr(tool_context, 'user_id', None)
        if not owner:
            return []
        try:
            from cognitrix.produced_files.service import files_made
            task_id = str(getattr(tool_context, 'task_id', None) or self.task_id or '')
            sandbox_ws = None
            if turn_sandbox:
                from cognitrix.common import sandbox_work as sw
                if sw.chat_switch(self.id) is not False and sw.sandbox_available():
                    sandbox_ws = sw.SandboxWork(owner, agent.id)
            device_id = str((turn_device or {}).get('id') or '') if isinstance(turn_device, dict) else ''
            if device_id and (turn_device or {}).get('read_only'):
                device_id = ''
            device_ctx = None
            if device_id:
                from cognitrix.computer.paired.turn import _project_folders
                device_ctx = {'session_id': self.id, 'agent_id': agent.id, 'task_id': task_id or None,
                              **_project_folders()}
            folders = list(inherited_folders or turn_folders or ())
            if not folders and not sandbox_ws and not device_id:
                from cognitrix.common.folders import legacy_default_folders
                folders = list(legacy_default_folders())
            made = await files_made(
                owner_id=str(owner), agent_id=str(agent.id or ''), session_id='' if task_id else str(self.id or ''),
                task_id=task_id, run_id=str(getattr(tool_context, 'run_id', None) or ''), since=since,
                folders=folders, sandbox_ws=sandbox_ws, device_id=device_id, device_ctx=device_ctx)
        except Exception:
            logger.debug('Could not list the files this turn made', exc_info=True)
            return []
        if made and interface in ('web', 'ws'):
            try:
                await output({'type': 'files_made', 'files': made})
            except Exception:
                logger.debug('Could not send the files this turn made', exc_info=True)
        return made

    async def _sandbox_review(self, agent: 'Agent', interface: str, output: Callable, tool_context: Any,
                              turn_folders: list, inherited_folders: tuple, record_history: bool | None) -> None:
        """End a sandbox turn with a review card for each folder whose copy in
        the sandbox has changes the person hasn't seen: the changed files, with
        buttons to copy them back or keep them there. Also keeps a Switch
        Computer For Code choice on the chat."""
        from cognitrix.common import sandbox_work as sw
        switched = sw.chat_switch(self.id)
        if switched is not None:
            self.run_in_sandbox = switched
        if switched is False or interface not in ('web', 'ws', 'task'):
            return
        if interface in ('web', 'ws') and not getattr(self, 'team_id', None):
            # An agent chat shows its changes live under the last reply and
            # in the side panel (cognitrix.file_changes), with these buttons.
            return
        folders = [f for f in turn_folders if getattr(f, 'name', '') != 'scratch']
        if not folders or not sw.sandbox_available():
            return
        ws = sw.SandboxWork(getattr(tool_context, 'user_id', None) or self.user_id, agent.id)
        try:
            projects = await asyncio.to_thread(sw.changes_to_review, ws, self.id, folders)
        except Exception:
            logger.debug('Could not check the sandbox for changes to review', exc_info=True)
            return
        if not projects:
            return
        call = self._cli_tool_caller(agent, interface, output, tool_context, turn_folders, inherited_folders,
                                     record_history, True)
        for name in projects[:3]:
            try:
                await call('sandbox_changes', {'project': name, 'show_diff': False, 'review': True})
            except Exception:
                logger.debug('Could not show the sandbox review card', exc_info=True)

    def _sandbox_chosen(self, agent: 'Agent') -> bool:
        from cognitrix.common.sandbox_work import sandbox_choice
        return sandbox_choice(self.run_in_sandbox, agent)[1]

    def _turn_folders(self, agent: 'Agent', interface: str) -> list:
        """This chat's working folders, copying the agent's defaults on first use.

        A new chat starts with only its private scratch folder unless the agent
        has its own default folders. CLI and compat turns keep the app's working
        folder (an empty list).
        """
        if interface not in ('web', 'ws', 'task', 'acp'):
            return []
        from cognitrix.common.folders import (
            folders_to_dicts,
            normalize_folders,
            scratch_folder,
        )
        source = self.folders
        if source is None:
            # A chat in a project starts in the project's folders, not the
            # agent's own defaults: a project is one scope.
            from cognitrix.projects.context import get as _turn_project
            project = _turn_project()
            if project is not None and project.folders:
                source = [dict(f) for f in project.folders]
            else:
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
