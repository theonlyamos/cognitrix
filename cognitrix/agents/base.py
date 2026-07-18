import asyncio
import logging
import os
import weakref
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, TypeAlias

from rich import print

from cognitrix.errors import ExecutionControlError
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
from cognitrix.models import Agent, Message, Tool
from cognitrix.models.tool import MCPTool
from cognitrix.providers.base import LLM
from cognitrix.safety.approval_gate import OPERATION_BLOCKED_PREFIX, ApprovalGate, ToolCall
from cognitrix.safety.destructive_ops import DestructiveOpDetector
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

        print(f"\n{self.agent.name} responded: {result}")
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
        return "You have access to the following Tools:\n" + "\n".join([f"{tool.name}: {tool.description}" for tool in self.agent.tools])

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
            if self.agent.is_sub_agent:
                print("=======is sub agent===========")
                print(processed_query)

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
                print(processed_query)
        else:
            prompt['content'] = processed_query

        return prompt

    def _process_query(self, query: str | dict) -> str | dict:
        return extract_json(query) if isinstance(query, str) else query

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
        jobs: list[tuple[int, Tool, dict[str, Any], int, bool]] = []
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
                tool_call = ToolCall(tool_name=assigned_tool.name, params=args)
                risk = self.detector.analyze(assigned_tool.name, args)

                if assigned_tool.approval_mode == 'always' or (
                    assigned_tool.approval_mode == 'risk_based' and risk.risk_level.value in ['medium', 'high']
                ):
                    print(f"\n⚠️  Risk detected: {risk.risk_level.value}")
                    print(f"   Details: {risk.details}")

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
                        results_by_index[i] = _tool_result_entry(
                            tc_id,
                            ToolOutcome.failure(
                                denial_code,
                                f"{OPERATION_BLOCKED_PREFIX}: user denied approval for {tool.name}",
                                denied=True,
                            ),
                        )
                        continue

                    if approval.cached:
                        print("   (Using cached approval)")

                print(f"\nRunning tool '{tool.name.title()}' with parameters: {args}")

                # Add parent reference for sub-agent tools
                if 'sub agent' in tool.name.lower() or tool.name.lower() == 'create sub agent' or tool.category == 'mcp':
                    args['parent'] = self.agent

                # Delegation inherits the caller's interface so a sub-agent's
                # risky tools are gated by the same channel (never a model-
                # supplied value).
                if tool.name.lower() == 'call agent':
                    args['interface'] = interface

                jobs.append(
                    (
                        i,
                        tool,
                        args,
                        assigned_tool.max_attempts,
                        assigned_tool.retryable,
                    )
                )

            # A fixed worker pool avoids creating one asyncio.Task per model-
            # supplied call. All batches share the same execution limiter.
            next_job = 0
            execution_slots = _tool_execution_limiter()

            async def worker() -> None:
                nonlocal next_job
                while next_job < len(jobs):
                    job_index = next_job
                    next_job += 1
                    i, tool, params, max_retries, attempt_recovery = jobs[job_index]
                    tc_id = agent_tool_calls[i].get('tool_call_id')
                    try:
                        async with execution_slots:
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
                    results_by_index[i] = _tool_result_entry(tc_id, outcome)

            worker_count = min(MAX_CONCURRENT_TOOL_CALLS, len(jobs))
            worker_tasks = [
                asyncio.create_task(worker()) for _ in range(worker_count)
            ]
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
        for server in self.agent.mcp_servers:
            await self.import_mcp_tools(server)

    async def import_mcp_tools(self, server: str):
        """Import tools from a single connected MCP server.

        Uses the shared, working client API + wrapper factory (the previous
        bespoke implementation called a no-arg async factory with an argument,
        never awaited it, and used a nonexistent client.run_tool method).
        """
        try:
            from cognitrix.mcp.client import get_dynamic_client
            from cognitrix.mcp.tools import create_mcp_tool_wrapper

            client = await get_dynamic_client()
            if not client.is_connected(server):
                logger.warning("MCP server '%s' is not connected; skipping tool import", server)
                return
            tools_list = await client.list_tools(server)
            for tool_def in (tools_list or []):
                self.add_tool(create_mcp_tool_wrapper(server, tool_def))
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
