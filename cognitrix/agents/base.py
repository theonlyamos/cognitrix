import asyncio
import json
import logging
import os
import weakref
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, TypeAlias

from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
from cognitrix.common.encoding import status_print
from cognitrix.errors import ExecutionControlError
from cognitrix.models import Agent, Message, Tool
from cognitrix.models.tool import MCPTool
from cognitrix.providers.base import LLM
from cognitrix.safety.approval_gate import OPERATION_BLOCKED_PREFIX, ApprovalGate, ToolCall
from cognitrix.safety.destructive_ops import (
    _RISK_ORDER,
    DestructiveOpDetector,
    RiskAssessment,
    RiskLevel,
    mcp_tool_risk,
)
from cognitrix.safety.permissions import decide_tool_call, visible_tools
from cognitrix.tools.base import ToolManager
from cognitrix.tools.resilient_tool_wrapper import ResilientToolManager
from cognitrix.tools.utils import ToolCallResult, ToolOutcome
from cognitrix.utils import extract_json
from cognitrix.utils.llm_response import LLMResponse

if TYPE_CHECKING:
    from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

AgentList: TypeAlias = list['Agent']

# Cap on a single tool result entering chat history (~2k tokens). Oversized
# results bloat every subsequent prompt; the model can re-run the tool with a
# narrower range if it needs more.
MAX_TOOL_RESULT_CHARS = 8000

# Bound simultaneous executions without limiting how many calls a turn may
# make. The limiter is shared by every batch on the same event loop (the normal
# deployment model is one loop per server process).
_DEFAULT_MAX_CONCURRENT_TOOL_CALLS = 4
_MAX_ALLOWED_CONCURRENT_TOOL_CALLS = 64


def _parse_max_concurrent_tool_calls(raw: str | None) -> int:
    try:
        value = int(raw) if raw else _DEFAULT_MAX_CONCURRENT_TOOL_CALLS
    except (TypeError, ValueError):
        logger.warning(
            "Invalid COGNITRIX_MAX_CONCURRENT_TOOL_CALLS=%r; using %s",
            raw,
            _DEFAULT_MAX_CONCURRENT_TOOL_CALLS,
        )
        return _DEFAULT_MAX_CONCURRENT_TOOL_CALLS
    if not 1 <= value <= _MAX_ALLOWED_CONCURRENT_TOOL_CALLS:
        logger.warning(
            "COGNITRIX_MAX_CONCURRENT_TOOL_CALLS must be between 1 and %s; using %s",
            _MAX_ALLOWED_CONCURRENT_TOOL_CALLS,
            _DEFAULT_MAX_CONCURRENT_TOOL_CALLS,
        )
        return _DEFAULT_MAX_CONCURRENT_TOOL_CALLS
    return value


MAX_CONCURRENT_TOOL_CALLS = _parse_max_concurrent_tool_calls(
    os.getenv('COGNITRIX_MAX_CONCURRENT_TOOL_CALLS')
)

_TOOL_EXECUTION_LIMITERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


_PATH_ARGUMENT_NAMES = ('file_path', 'path', 'filepath', 'filename', 'target_file')
_PATH_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _path_lock_for(params: dict[str, Any]) -> asyncio.Lock | None:
    """One lock per file path, so calls on the same file run one at a time.

    Parallel Edit calls on one file each read the old content and wrote back
    their own version, so all but the last edit were lost while every call
    reported success. Calls on different files still run concurrently.
    """
    raw = next(
        (params[name] for name in _PATH_ARGUMENT_NAMES
         if isinstance(params.get(name), str) and params[name].strip()),
        None,
    )
    if raw is None:
        return None
    # File tools resolve relative paths against the primary working folder.
    try:
        from cognitrix.common.folders import primary_folder
        base = str(primary_folder())
    except Exception:
        base = os.getcwd()
    key = os.path.normcase(os.path.abspath(os.path.join(base, os.path.expanduser(raw.strip()))))
    loop = asyncio.get_running_loop()
    locks = _PATH_LOCKS.get(loop)
    if locks is None:
        locks = weakref.WeakValueDictionary()
        _PATH_LOCKS[loop] = locks
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


def _tool_execution_limiter() -> asyncio.Semaphore:
    """Return the shared limiter for the running loop and current config."""
    loop = asyncio.get_running_loop()
    configured = MAX_CONCURRENT_TOOL_CALLS
    entry = _TOOL_EXECUTION_LIMITERS.get(loop)
    if entry is None or entry[0] != configured:
        entry = (configured, asyncio.Semaphore(configured))
        _TOOL_EXECUTION_LIMITERS[loop] = entry
    return entry[1]


class ToolBatchCancelled(asyncio.CancelledError):
    """Cancellation carrying the completed and still-outstanding batch state."""

    def __init__(
        self,
        completed_result: dict[str, Any],
        unresolved_tool_calls: list[dict[str, Any]],
        completed_results_by_index: dict[int, dict[str, Any]],
    ):
        super().__init__("Tool batch cancelled")
        self.completed_result = completed_result
        self.unresolved_tool_calls = unresolved_tool_calls
        self.completed_results_by_index = completed_results_by_index


def _truncate_tool_result(content: str) -> str:
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content
    dropped = len(content) - MAX_TOOL_RESULT_CHARS
    return (
        content[:MAX_TOOL_RESULT_CHARS]
        + f"\n[truncated {dropped} chars — re-run the tool with a narrower range if you need more]"
    )


def _tool_outcome(value: Any) -> ToolOutcome:
    """Normalize legacy tool values into the stable public result contract."""
    if isinstance(value, ToolCallResult) and value.outcome:
        return value.outcome
    return ToolOutcome.success(str(value))


async def _hide_saved_secrets(outcome: ToolOutcome) -> ToolOutcome:
    """Scrub the person's saved credential values out of a tool result.

    A site, a file or a shell command can echo a token back; the model must
    never see it. Values show up as ``[NAME hidden]`` instead.
    """
    from cognitrix.credentials import redact, redaction_map_for
    from cognitrix.tools.utils import current_execution_context

    owner = current_execution_context().user_id
    if not owner:
        return outcome
    try:
        secrets_map = await redaction_map_for(str(owner))
    except Exception:
        logger.exception("Could not load saved credentials to scrub a tool result")
        return outcome
    if not secrets_map:
        return outcome
    data = outcome.model_dump()
    data['text'] = redact(data['text'], secrets_map)
    data['warnings'] = [redact(w, secrets_map) for w in data.get('warnings') or []]
    if data.get('error'):
        data['error']['message'] = redact(data['error']['message'], secrets_map)
    return ToolOutcome.model_validate(data)


def _tool_result_entry(tool_call_id: str | None, outcome: ToolOutcome) -> dict[str, Any]:
    return {
        'tool_call_id': tool_call_id,
        # `data` remains the compact, provider-compatible model-facing text.
        'data': outcome.model_content(),
        # Preserve the legacy flag for callers that have not migrated to the
        # richer outcome contract yet.
        'success': outcome.status == 'success',
        'outcome': outcome.model_dump(),
    }

class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class AgentManager:
    """Handles the business logic for agents."""

    detector = DestructiveOpDetector()
    approval_gate = ApprovalGate()

    def __init__(self, agent: Agent):
        self.agent = agent

    def add_notification_callback(self, callback: Callable[[Message], None]):
        self.agent.notification_callbacks.append(callback)

    def notify(self, message: Message):
        for callback in self.agent.notification_callbacks:
            callback(message)

    async def receive_message(self, message: Message, session: Optional['Session'] = None): # type: ignore
        self.agent.inbox.append(message)
        self.notify(message)
        await self.process_messages(session)

    async def process_messages(self, session: Optional['Session'] = None): # type: ignore
        # Sort messages by priority (higher priority first)
        self.agent.inbox.sort(key=lambda m: m.priority.value, reverse=True)

        while self.agent.inbox:
            message = self.agent.inbox.pop(0)
            response = await self.process_message(message, session)
            self.agent.response_list.append((message, response))
            message.read = True

    async def process_message(self, message: Message, session: Optional['Session'] = None): # type: ignore
        content = f"{message.sender}: {message.content}"

        new_llm = LLM.load_llm(self.agent.llm.provider)
        if new_llm:
            new_llm.temperature = self.agent.llm.temperature
            self.agent.llm = new_llm

        result: str = ''

        async def generate_response(data: dict[str, Any]):
            nonlocal result
            result += data['content']

        if session:
            await session(message.content, self.agent, 'task', True, generate_response, {'type': 'start_task', 'action': 'process_message'})
        else:
            async for response in self.agent.generate(content):
                result += response.result # type: ignore

        status_print(f"\n{self.agent.name} responded: {result}")
        response = LLMResponse()
        response.add_chunk(result)

        return response

    @property
    def available_tools(self) -> list[str]:
        return [tool.name for tool in self.agent.tools]

    def formatted_system_prompt(self):
        tools_str = self._format_tools_string()
        subagents_str = self._format_subagents_string()
        llms_str = self._format_llms_string()
        skills_str = self._format_skills_string()

        today = (datetime.now()).strftime("%a %b %d %Y")
        prompt = f"Today is {today}.\n\n"
        prompt += self.agent.system_prompt
        prompt = prompt.replace("{name}", self.agent.name)

        if not self.agent.llm.supports_tool_use:
            prompt = prompt.replace("{tools}", tools_str)

        prompt = prompt.replace("{subagents}", subagents_str)
        prompt = prompt.replace("{llms}", llms_str)

        if skills_str:
            prompt += "\n\n" + skills_str

        return prompt

    def _format_tools_string(self) -> str:
        return "You have access to the following Tools:\n" + "\n".join([f"{tool.name}: {tool.description}" for tool in visible_tools(self.agent, self.agent.tools)])

    def _format_subagents_string(self) -> str:
        if not len(self.agent.sub_agents):
            return ''
        subagents_str = "Available Subagents:\n"
        subagents_str += "\n".join([f"-- {agent.name}" for agent in self.agent.sub_agents])
        subagents_str += "\nYou should always use a subagent for a task if there is one specifically created for that task."
        subagents_str += "\nWhen creating a sub agent, it's description should be a comprehensive prompt of the agent's behavior, capabilities and example tasks."
        return subagents_str

    def _format_llms_string(self) -> str:
        return (
            "Provider is configured via env (AI_PROVIDER, PROVIDER_BASE_URL, PROVIDER_API_KEY, PROVIDER_MODEL) "
            "or CLI (--provider, --api-base, --api-key, --model). Choose provider for each subagent."
        )

    def _format_skills_string(self) -> str:
        """Build skill awareness section for system prompt."""
        try:
            import asyncio

            from cognitrix.skills.manager import get_skill_manager
            manager = get_skill_manager()

            # Use cached data if available (pre-warmed at startup)
            if not manager._cache:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        return ''
                    loop.run_until_complete(manager.discover_all())
                except RuntimeError:
                    asyncio.run(manager.discover_all())

            summaries = manager.get_skill_summaries()
            if not summaries:
                return ''
            lines = ["Available Skills — invoke by calling the load_skill tool with the skill name "
                     "(e.g. load_skill(skill_name='<name>')) when a request matches one:"]
            for s in summaries:
                hint = f" {s['argument_hint']}" if s.get('argument_hint') else ""
                invocable = "" if s.get('user_invocable') == 'True' else " [auto-only]"
                lines.append(f"  - /{s['name']}{hint}: {s['description'][:100]}{invocable}")
            return '\n'.join(lines)
        except Exception:
            logger.exception("Failed to build skills section for system prompt")
            return ''

    def process_prompt(self, query: str | dict, role: str = 'User') -> dict:
        processed_query = self._process_query(query)
        prompt: dict[str, Any] = {'role': role, 'type': 'text'}

        if isinstance(processed_query, dict):
            if processed_query.get('type') == 'tool_calls_result':
                results = processed_query.get('result', [])
                if isinstance(results, list):
                    # Build tool result messages with role: tool
                    tool_messages = []
                    for r in results:
                        if isinstance(r, dict):
                            tool_call_id = r.get('tool_call_id')
                            content = str(r.get('data', ''))
                            outcome = r.get('outcome')
                        else:
                            tool_call_id = None
                            content = str(r)
                            outcome = None
                        message = {
                            'role': 'tool',
                            'tool_call_id': tool_call_id,
                            'content': _truncate_tool_result(content)
                        }
                        # Extra structured data is ignored by provider formatters
                        # but keeps session reloads/UI transports artifact-aware.
                        if outcome:
                            message['outcome'] = outcome
                        tool_messages.append(message)
                    # Return list of tool messages instead of single prompt
                    return tool_messages
                else:
                    return [{
                        'role': 'tool',
                        'tool_call_id': None,
                        'content': _truncate_tool_result(str(results))
                    }]
            elif 'result' in processed_query.keys():
                result = processed_query['result']
                if isinstance(result, list):
                    if len(result) >= 2 and result[0] == 'image':
                        prompt['type'] = 'image'
                        prompt['content'] = result[1]
                    elif len(result) >= 3 and result[0] == 'agent':
                        new_agent: Agent = result[1]
                        new_agent.parent_id = self.agent.id
                        self.add_sub_agent(new_agent) # type: ignore

                        prompt['content'] = result[2]
                    elif len(result) >= 3:
                        prompt['content'] = result[2]
                    else:
                        prompt['content'] = '\n\n'.join(str(r) for r in result) if result else ''
                else:
                    prompt['content'] = result
            else:
                prompt['content'] = query if isinstance(query, str) else json.dumps(processed_query)
        else:
            prompt['content'] = processed_query

        return prompt

    def _process_query(self, query: str | dict) -> str | dict:
        if not isinstance(query, str):
            return query
        # Only a message that is itself a structured result is unpacked. Text
        # that merely contains JSON (a pasted payload, a task retry prompt with
        # reviewer feedback) must reach the model unchanged; unpacking it used
        # to drop the whole message and send an empty user turn.
        stripped = query.strip()
        if not (stripped.startswith('{') and stripped.endswith('}')):
            return query
        parsed = extract_json(stripped)
        if isinstance(parsed, dict) and (
            'result' in parsed or parsed.get('type') == 'tool_calls_result'
        ):
            return parsed
        return query

    def add_sub_agent(self, agent: Agent):
        self.agent.sub_agents.append(agent)

    def get_sub_agent_by_name(self, name: str) -> Agent | None:
        return next((agent for agent in self.agent.sub_agents if agent.name.lower() == name.lower()), None)

    def get_tool_by_name(self, name: str) -> Tool | None:
        normalized = name.casefold().replace('_', ' ')
        return next((tool for tool in self.agent.tools
                     if tool.name.casefold().replace('_', ' ') == normalized), None)

    async def call_tools(
        self,
        tool_calls: dict[str, Any] | list[dict[str, Any]],
        interface: str = 'cli',
    ) -> dict[str, Any] | str:
        """Execute tool calls with safety checks and retry logic."""
        agent_tool_calls = tool_calls if isinstance(tool_calls, list) else [tool_calls]
        results_by_index: dict[int, dict[str, Any]] = {}
        jobs: list[tuple[int, Tool, dict[str, Any], int, bool, bool]] = []
        worker_tasks: list[asyncio.Task] = []

        def completed_result() -> dict[str, Any]:
            return {
                'type': 'tool_calls_result',
                'result': [results_by_index[i] for i in sorted(results_by_index)],
            }

        async def cancel_workers() -> None:
            for task in worker_tasks:
                if not task.done():
                    task.cancel()
            if worker_tasks:
                await asyncio.gather(*worker_tasks, return_exceptions=True)

        try:
            if not tool_calls:
                return ''

            resilient_manager = ResilientToolManager(llm=self.agent.llm)

            # Results keyed by original position so every call gets exactly one
            # answer (required by the tool-call protocol) and a missing tool or a
            # denied approval does NOT abort the other calls in the batch.
            for i, t in enumerate(agent_tool_calls):
                tc_id = t.get('tool_call_id')
                name = t.get('name')
                args = t.get('arguments', {}) or {}

                if not name:
                    results_by_index[i] = _tool_result_entry(
                        tc_id, ToolOutcome.failure('malformed_tool_call', 'Error: malformed tool call (no name)')
                    )
                    continue

                if t.get('arguments_error'):
                    results_by_index[i] = _tool_result_entry(
                        tc_id, ToolOutcome.failure('malformed_arguments', str(t['arguments_error']))
                    )
                    continue

                # A tool being registered makes it discoverable to the app, not
                # automatically available to every agent.  Resolve exclusively
                # from the agent's assigned allowlist before doing any safety
                # checks or execution.
                assigned_tool = self.get_tool_by_name(name)
                if not assigned_tool:
                    results_by_index[i] = _tool_result_entry(
                        tc_id,
                        ToolOutcome.failure(
                            'tool_not_assigned', f"Error: Tool '{name}' is not assigned to this agent", denied=True
                        ),
                    )
                    continue

                tool = assigned_tool if isinstance(assigned_tool, MCPTool) else (
                    ToolManager.get_by_name(name) or assigned_tool
                )

                if assigned_tool.supported_interfaces and interface not in assigned_tool.supported_interfaces:
                    results_by_index[i] = _tool_result_entry(
                        tc_id,
                        ToolOutcome.failure(
                            'unsupported_interface',
                            f"Tool '{tool.name}' is not available from the {interface} interface",
                            denied=True,
                        ),
                    )
                    continue

                # Safety check
                # Work on a copy: `args` is the dict stored in the assistant
                # message, and runtime-only values (like the agent object)
                # must never end up in history, where they break JSON
                # serialisation on the next model call.
                internal_params = set(getattr(tool, 'internal_params', None) or [])
                args = {k: v for k, v in dict(args).items() if k not in internal_params}
                tool_call = ToolCall(tool_name=assigned_tool.name, params=args)
                # Set only when a person approved this exact call (not auto
                # approval); Bash needs it before using the full shell.
                person_approved = False
                risk = self.detector.analyze(assigned_tool.name, args)
                mcp_risk = await self._mcp_write_risk(assigned_tool, args)
                if mcp_risk is not None and _RISK_ORDER[mcp_risk.risk_level] > _RISK_ORDER[risk.risk_level]:
                    risk = mcp_risk

                # Per-agent and per-tool permission levels, on top of the
                # default risk policy (see cognitrix/safety/permissions.py).
                decision = decide_tool_call(self.agent, assigned_tool, args, risk, runtime_tool=tool)
                if decision.action == 'block':
                    results_by_index[i] = _tool_result_entry(
                        tc_id,
                        ToolOutcome.failure(
                            'permission_denied',
                            f"{OPERATION_BLOCKED_PREFIX}: {decision.reason}. Don't try it again.",
                            denied=True,
                        ),
                    )
                    continue

                if decision.action == 'ask':
                    risk = decision.risk
                    status_print(f"\n⚠️  Risk detected: {risk.risk_level.value}")
                    status_print(f"   Details: {risk.details}")

                    approval = await self.approval_gate.check_approval(
                        tool_call=tool_call,
                        risk=risk,
                        interface=interface,
                        scope=str(self.agent.id),
                    )

                    if not approval.approved:
                        denial_code = (
                            'approval_required'
                            if approval.error == 'approval_required'
                            else 'approval_denied'
                        )
                        reason = (
                            f"{tool.name} was {approval.error}"
                            if getattr(approval, 'reviewed', False) and approval.error
                            else f"user denied approval for {tool.name}"
                        )
                        results_by_index[i] = _tool_result_entry(
                            tc_id,
                            ToolOutcome.failure(
                                denial_code,
                                f"{OPERATION_BLOCKED_PREFIX}: {reason}",
                                denied=True,
                            ),
                        )
                        continue

                    if approval.cached:
                        status_print("   (Using cached approval)")
                    # A remembered approval was for the same text, which can mean
                    # something else after a `cd`, so the full shell needs a fresh one.
                    person_approved = not (
                        getattr(approval, "auto", False) or getattr(approval, "cached", False)
                    )

                status_print(f"\nRunning tool '{tool.name.title()}' with parameters: {args}")

                # Runtime-filled parameters. Delegation inherits the caller's
                # interface so a sub-agent's risky tools are gated by the same
                # channel (never a model-supplied value).
                if 'parent' in internal_params:
                    args['parent'] = self.agent
                if 'interface' in internal_params:
                    args['interface'] = interface
                if 'approved' in internal_params:
                    args['approved'] = person_approved

                jobs.append(
                    (
                        i,
                        tool,
                        args,
                        assigned_tool.max_attempts,
                        assigned_tool.retryable,
                        tool.occupies_execution_slot,
                    )
                )

            # A fixed worker pool avoids creating one asyncio.Task per model-
            # supplied ordinary call. Interactive wait tools are deliberately
            # detached so parked questions consume neither a worker nor one of
            # the process-wide execution slots.
            slot_jobs = [job for job in jobs if job[5]]
            detached_jobs = [job for job in jobs if not job[5]]
            next_job = 0
            execution_slots = _tool_execution_limiter()

            async def run_job(
                job: tuple[int, Tool, dict[str, Any], int, bool, bool],
                *, constrained: bool,
            ) -> None:
                i, tool, params, max_retries, attempt_recovery, _ = job
                tc_id = agent_tool_calls[i].get('tool_call_id')
                path_lock = _path_lock_for(params)
                lock_held = False
                try:
                    if path_lock is not None:
                        await path_lock.acquire()
                        lock_held = True
                    if constrained:
                        async with execution_slots:
                            result = await resilient_manager.run_tool(
                                tool=tool,
                                params=params,
                                max_retries=max_retries,
                                attempt_recovery=attempt_recovery,
                            )
                    else:
                        result = await resilient_manager.run_tool(
                            tool=tool,
                            params=params,
                            max_retries=max_retries,
                            attempt_recovery=attempt_recovery,
                        )
                    if result.success:
                        outcome = _tool_outcome(result.data)
                    else:
                        outcome = ToolOutcome.failure(
                            'tool_execution_error',
                            f"Error: {result.error} (attempted {result.attempts} times)",
                            retryable=False,
                        )
                except asyncio.CancelledError:
                    raise
                except ExecutionControlError:
                    raise
                except Exception as exc:
                    outcome = ToolOutcome.failure(
                        'tool_execution_error',
                        f"Error: {exc}",
                        retryable=False,
                    )
                finally:
                    if lock_held:
                        path_lock.release()
                results_by_index[i] = _tool_result_entry(tc_id, await _hide_saved_secrets(outcome))

            async def worker() -> None:
                nonlocal next_job
                while next_job < len(slot_jobs):
                    job_index = next_job
                    next_job += 1
                    await run_job(slot_jobs[job_index], constrained=True)

            worker_count = min(MAX_CONCURRENT_TOOL_CALLS, len(slot_jobs))
            worker_tasks = [
                asyncio.create_task(worker()) for _ in range(worker_count)
            ]
            worker_tasks.extend(
                asyncio.create_task(run_job(job, constrained=False))
                for job in detached_jobs
            )
            if worker_tasks:
                await asyncio.gather(*worker_tasks)

            results = [results_by_index[i] for i in range(len(agent_tool_calls))]
            return {
                'type': 'tool_calls_result',
                'result': results
            }

        except asyncio.CancelledError as exc:
            await cancel_workers()
            unresolved = [
                call for i, call in enumerate(agent_tool_calls)
                if i not in results_by_index
            ]
            raise ToolBatchCancelled(
                completed_result(), unresolved, dict(results_by_index)
            ) from exc
        except ExecutionControlError:
            await cancel_workers()
            raise
        except Exception as e:
            logger.exception("Tool execution error")
            # Preserve the assistant/tool protocol even for an unexpected
            # preprocessing/worker failure: every unresolved call gets an
            # explicit error result in its original position.
            for i, call in enumerate(agent_tool_calls):
                if i not in results_by_index:
                    results_by_index[i] = _tool_result_entry(
                        call.get('tool_call_id'),
                        ToolOutcome.failure(
                            'tool_execution_error', f"Error: {e}", retryable=False
                        ),
                    )
            return completed_result()

    async def _mcp_write_risk(self, tool: Any, args: dict) -> Any:
        """Risk for an MCP action that should ask first, else None.

        Read-only tools, and ones whose name or server annotations show an
        undoable change, run without asking. Deletes, outward sends, and
        tools we can't classify ask (see ``mcp_tool_risk``).
        """
        category = getattr(tool, 'category', None)
        if category == 'mcp_dynamic':
            # The wrapper was classified when it was built.
            if getattr(tool, 'approval_mode', None) != 'always':
                return None
            return RiskAssessment(
                risk_level=RiskLevel.MEDIUM,
                categories=['mcp_write'],
                details=f"'{tool.name}' may delete data or send something outside the app",
            )
        name = str(getattr(tool, 'name', '') or '').strip().lower().replace(' ', '_')
        if category != 'mcp' or name != 'run_mcp_tool':
            return None
        server = str(args.get('server') or '')
        tool_name = str(args.get('tool_name') or '')
        annotations = None
        try:
            from cognitrix.mcp.client import get_dynamic_client
            from cognitrix.mcp.registry import agent_servers, matches

            config = next((c for c in await agent_servers(self.agent) if matches(c, server)), None)
            if config is None:
                return None  # the tool itself refuses servers the agent can't use
            client = await get_dynamic_client()
            annotations = (client.tool_annotations.get(config.key) or {}).get(tool_name)
        except Exception:
            logger.exception("Could not check MCP tool annotations")
        risk = mcp_tool_risk(tool_name, annotations)
        if risk.risk_level == RiskLevel.LOW:
            return None
        risk.details = f"{risk.details} (MCP server '{server}')"
        return risk

    def add_tool(self, tool: Tool):
        if tool not in self.agent.tools:
            self.agent.tools.append(tool)

    def add_mcp_server(self, server: str):
        if server not in self.agent.mcp_servers:
            self.agent.mcp_servers.append(server)

    def remove_mcp_server(self, server: str):
        if server in self.agent.mcp_servers:
            self.agent.mcp_servers.remove(server)

    async def generate(self, prompt: str):
        # This assumes the context manager will be used by the session/caller
        # to construct the full prompt before calling the LLM.
        processed_prompt = self.process_prompt(prompt)
        # Handle both single dict and list of dicts (for tool results)
        messages = processed_prompt if isinstance(processed_prompt, list) else [processed_prompt]
        async for response in self.agent.llm(messages):
            yield response

    def call_sub_agent(self, agent_name: str, task_description: str):
        pass

    async def init_mcp_tools(self):
        """Add tool wrappers for every MCP server this agent may use."""
        from cognitrix.mcp.tools import sync_mcp_tools_for_agent

        for tool in await sync_mcp_tools_for_agent(self.agent):
            self.add_tool(tool)

    async def import_mcp_tools(self, server: str):
        """Import tools from one of this agent's MCP servers (id or name)."""
        try:
            from cognitrix.mcp.client import get_dynamic_client
            from cognitrix.mcp.registry import agent_servers, ensure_connected, matches
            from cognitrix.mcp.tools import create_mcp_tool_wrapper

            config = next((c for c in await agent_servers(self.agent) if matches(c, server)), None)
            if config is None:
                logger.warning("MCP server '%s' isn't available to this agent; skipping tool import", server)
                return
            client = await get_dynamic_client()
            if not await ensure_connected(client, config):
                logger.warning("MCP server '%s' is not connected; skipping tool import", server)
                return
            tools_list = await client.list_tools(config.key)
            for tool_def in (tools_list or []):
                self.add_tool(create_mcp_tool_wrapper(config.name, tool_def, config.key))
        except Exception as e:
            logger.error(f"Failed to import tools from MCP server '{server}': {e}")

    @staticmethod
    async def create_agent(name: str, system_prompt: str, provider: str | dict[str, Any] = 'groq',
                           model: str | None = '', temperature: float | None = 0.0,  tools: list[str] = None,
                           mcp_servers: list[str] = None,
                           is_sub_agent: bool = False, parent_id=None,
                           ephemeral: bool = False) -> Agent | None:
        if mcp_servers is None:
            mcp_servers = []
        if tools is None:
            tools = []
        llm = LLM.load_llm(provider)
        if not llm:
            return None

        # Empty model / None temperature = keep the provider's default (from load_llm).
        if model:
            llm.model = model
        if temperature is not None:
            llm.temperature = temperature

        loaded_tools: list[Tool] = []
        if tools:
            if 'all' in tools:
                loaded_tools = ToolManager.list_all_tools()
            else:
                for cat in tools:
                    cat_tools = ToolManager.get_tools_by_category(cat.strip().lower())
                    loaded_tools.extend(cat_tools)

                    tool_by_name = ToolManager.get_by_name(cat.strip().lower())
                    if tool_by_name:
                        loaded_tools.append(tool_by_name)

        agent = Agent(
            name=name,
            llm=llm,
            system_prompt=system_prompt or ASSISTANT_SYSTEM_PROMPT,
            tools=loaded_tools,
            mcp_servers=mcp_servers,
            is_sub_agent=is_sub_agent,
            parent_id=parent_id
        )

        if not ephemeral:
            await agent.save()
        return agent

    @staticmethod
    async def list_agents(parent_id: str | None = None) -> list[Agent]:
        if parent_id:
            return await Agent.find({'parent_id': parent_id})
        return await Agent.find({'is_sub_agent': False})

    @staticmethod
    async def load_agent(agent_name: str) -> Agent | None:
        return await Agent.find_one({'name': agent_name})

    # -------------------------------------------------------------------
    # Public convenience aliases
    # -------------------------------------------------------------------

    @staticmethod
    async def create(*args, **kwargs) -> Agent | None:  # noqa: D401, ANN001
        """Alias for :py:meth:`create_agent` so users can call
        ``assistant = await AgentManager.create(...)`` uniformly.
        """
        return await AgentManager.create_agent(*args, **kwargs)

# ---------------------------------------------------------------------------
# Attach the manager interface to the Agent model to centralise management
# logic while keeping backward-compatibility with existing call-sites that
# invoke `Agent.create_agent(...)`, etc.
# ---------------------------------------------------------------------------

# Instance-level manager
def _agent_manager(self: Agent) -> 'AgentManager':  # type: ignore[name-defined]
    """Return an `AgentManager` bound to this Agent instance."""
    return AgentManager(self)

# Add property dynamically (avoids circular import at class definition time)
Agent.manager = property(_agent_manager)

# Add process_prompt method to Agent model (delegates to manager)
Agent.process_prompt = lambda self, query, role='User': AgentManager(self).process_prompt(query, role)  # type: ignore[attr-defined]


async def _agent_call_tools(self, tool_calls, interface='cli'):
    """Delegate call_tools to AgentManager."""
    return await AgentManager(self).call_tools(tool_calls, interface=interface)


Agent.call_tools = _agent_call_tools  # type: ignore[attr-defined]

# Class-level convenience methods that delegate to AgentManager
Agent.create_agent = staticmethod(AgentManager.create_agent)  # type: ignore[attr-defined]
Agent.list_agents = staticmethod(AgentManager.list_agents)  # type: ignore[attr-defined]
Agent.load_agent = staticmethod(AgentManager.load_agent)  # type: ignore[attr-defined]

# Add formatted_system_prompt to Agent model (delegates to manager)
# This is a regular method that calls the manager's method
def _formatted_system_prompt(self):
    return AgentManager(self).formatted_system_prompt()

Agent.formatted_system_prompt = _formatted_system_prompt
