"""SkillExecutor — streaming runtime for skill invocations.

Handles:
1. $ARGUMENTS / $N / ${ENV} substitutions
2. !`command` dynamic context injection
3. Safety gate checks (ApprovalGate)
4. Tool filtering (allowed-tools)
5. Same-context or forked (sub-agent) execution
6. Streaming SkillEvents for real-time progress
"""

import asyncio
import fnmatch
import logging
import re
import shlex
from typing import Any, AsyncGenerator, TYPE_CHECKING

from cognitrix.skills.models import (
    SkillEvent,
    SkillEventType,
    SkillManifest,
    RiskLevel,
)

if TYPE_CHECKING:
    from cognitrix.agents.base import AgentManager
    from cognitrix.providers.base import LLM
    from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

# Max size for dynamic context command output
MAX_DYNAMIC_CONTEXT_SIZE = 100_000  # 100KB

# Pre-compiled regex for argument substitution
_ARG_NUM_PATTERN = re.compile(r'\$(\d+)(?!\d)')
_ARGUMENTS_BRACKET_PATTERN = re.compile(r'\$ARGUMENTS\[(\d+)\]')
_ARG_PATTERN = re.compile(r'\$\(arg\s+(\w+)\)')

# Tool alias map for AgentSkills compatibility
# Standard Anthropic skill names -> Cognitrix native tool names
TOOL_ALIASES = {
    'bash': 'Bash',
    'shell': 'Bash',
    'read': 'Read',
    'write': 'Write',
    'edit': 'Edit',
    'grep': 'Grep',
    'glob': 'Glob',
    'webfetch': 'Webfetch',
    'search': 'Search',
}

# Tool name for argument extraction (first param by convention)
TOOL_FIRST_ARG = {
    'Bash': 'command',
    'Read': 'file_path',
    'Write': 'file_path',
    'Edit': 'file_path',
    'Grep': 'pattern',
    'Glob': 'pattern',
    'Webfetch': 'url',
    'Search': 'query',
}

# Allowed shell commands for dynamic context (whitelist)
ALLOWED_COMMANDS = frozenset({
    'ls', 'dir', 'cat', 'type', 'head', 'tail', 'find', 'grep', 'rg',
    'git', 'gitstatus', 'git log', 'git diff', 'git branch',
    'npm', 'pip', 'python', 'python3', 'node', 'uv',
    'pwd', 'cd', 'echo', 'which', 'where', 'file',
    'wc', 'sort', 'uniq', 'awk', 'sed',
})


class SkillExecutionError(Exception):
    """Raised when skill execution fails."""
    pass


class SkillExecutor:
    """Execute a skill by resolving context and injecting into agent.

    The executor follows Anthropic's approach: skills are prompt-based
    instructions. The executor resolves placeholders, applies safety,
    and injects the rendered prompt into the agent's conversation.
    """

    def __init__(self, agent_manager: 'AgentManager', llm: 'LLM'):
        self.agent_manager = agent_manager
        self.llm = llm

    async def execute(
        self,
        manifest: SkillManifest,
        arguments: str = "",
        session: 'Session | None' = None,
        skill_args: dict[str, Any] | None = None,
    ) -> AsyncGenerator[SkillEvent, None]:
        """Execute a skill, streaming events.

        Args:
            manifest:   Parsed skill manifest
            arguments:  Arguments string (e.g. "src/auth/login.ts")
            session:    Optional session for history tracking
            skill_args: Optional dict of structured args from use_skill kwargs

        Yields:
            SkillEvent for each significant moment
        """
        yield SkillEvent(
            type=SkillEventType.SKILL_START,
            skill_name=manifest.name,
            data={
                "description": manifest.description,
                "context": manifest.context or "same",
                "arguments": arguments,
            },
        )

        try:
            # Parse arguments with shlex to preserve quoted strings
            args_list = shlex.split(arguments) if arguments else []
            
            # If structured args provided, merge them into args_list
            if skill_args and manifest.args:
                # Map skill args by name to position
                arg_mapping = {arg.name: idx for idx, arg in enumerate(manifest.args) if arg.name in skill_args}
                for name, idx in arg_mapping.items():
                    if idx < len(args_list):
                        args_list[idx] = str(skill_args[name])
                    else:
                        args_list.append(str(skill_args[name]))
            
            # Auto-build skill_args from positional arguments when not
            # explicitly provided.  This covers CLI slash-command invocations
            # (e.g. /web-research AI agents 2025) and use_skill calls that
            # only pass an `arguments` string.
            if not skill_args and manifest.args and args_list:
                skill_args = {}
                num_defs = len(manifest.args)
                for idx, arg_def in enumerate(manifest.args):
                    if idx < len(args_list):
                        if idx == num_defs - 1 and len(args_list) > num_defs:
                            # Last defined arg absorbs all remaining words
                            # e.g. args=["AI","agents","2025"] → topic="AI agents 2025"
                            skill_args[arg_def.name] = " ".join(args_list[idx:])
                        else:
                            skill_args[arg_def.name] = args_list[idx]
                    elif arg_def.default is not None:
                        skill_args[arg_def.name] = arg_def.default
            
            # Resolve $(arg name) syntax whenever we have skill_args + definitions
            if skill_args and manifest.args:
                rendered = self._resolve_arg_syntax(manifest.body, skill_args, manifest.args)
            else:
                rendered = manifest.body
            
            # Resolve $ARGUMENTS, $N, $ARGUMENTS[N] substitutions
            rendered = self._resolve_arguments(rendered, args_list)
            # 2. Resolve environment substitutions (including ${COGNITRIX_SKILL_DIR})
            rendered = self._resolve_env_substitutions(rendered, session, manifest)

            # 3. Execute dynamic context commands (!`cmd`)
            has_dynamic = '!`' in manifest.body
            rendered = await self._resolve_dynamic_context(rendered)
            yield SkillEvent(
                type=SkillEventType.SKILL_CONTEXT_INJECTED,
                skill_name=manifest.name,
                data={"has_dynamic_context": has_dynamic},
            )

            # 4. Ensure dependencies are installed
            if manifest.dependencies.pip or manifest.dependencies.system:
                await self._ensure_dependencies(manifest)

            # 5. Safety check - require approval for MEDIUM or HIGH risk
            if manifest.safety.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                approved = await self._request_approval(manifest)
                if not approved:
                    yield SkillEvent(
                        type=SkillEventType.SKILL_ERROR,
                        skill_name=manifest.name,
                        data="Execution denied by user",
                    )
                    return

            # 6. Execute
            yield SkillEvent(
                type=SkillEventType.SKILL_PROMPT_SENT,
                skill_name=manifest.name,
                data={
                    "context": manifest.context or "same",
                    "agent": manifest.agent,
                },
            )

            if manifest.context == "fork":
                async for chunk in self._execute_forked(rendered, manifest, session):
                    yield SkillEvent(
                        type=SkillEventType.SKILL_PROGRESS,
                        skill_name=manifest.name,
                        data=chunk,
                    )
            else:
                async for chunk in self._execute_same_context(rendered, manifest, session):
                    yield SkillEvent(
                        type=SkillEventType.SKILL_PROGRESS,
                        skill_name=manifest.name,
                        data=chunk,
                    )

            yield SkillEvent(
                type=SkillEventType.SKILL_COMPLETE,
                skill_name=manifest.name,
            )

        except Exception as e:
            logger.error(f"Skill '{manifest.name}' execution failed: {e}")
            yield SkillEvent(
                type=SkillEventType.SKILL_ERROR,
                skill_name=manifest.name,
                data=str(e),
            )

    # ── Substitution ──

    def _resolve_arg_syntax(
        self, body: str, skill_args: dict[str, Any], args_def: list
    ) -> str:
        """Replace $(arg name) with values from skill_args, using defaults if not provided."""
        # Build a lookup: arg name -> value (or default)
        arg_values = {}
        for arg_def in args_def:
            name = arg_def.name
            if name in skill_args:
                arg_values[name] = skill_args[name]
            elif arg_def.default is not None:
                arg_values[name] = arg_def.default
            elif arg_def.required:
                # Required but not provided - leave placeholder for error
                arg_values[name] = f"$(arg {name})"

        # Replace $(arg name) with the value
        def replace_arg(m):
            name = m.group(1)
            return str(arg_values.get(name, m.group(0)))

        return _ARG_PATTERN.sub(replace_arg, body)

    def _resolve_arguments(self, body: str, args_list: list[str]) -> str:
        """Replace $ARGUMENTS, $ARGUMENTS[N], and $N with actual values.
        
        $N and $ARGUMENTS[N] use 1-based indexing (like shell positional args).
        """
        if not args_list:
            args_list = []
        
        arguments = " ".join(args_list)
        result = body.replace("$ARGUMENTS", arguments)

        # $ARGUMENTS[N] - 1-based to 0-based conversion
        result = _ARGUMENTS_BRACKET_PATTERN.sub(
            lambda m: args_list[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(args_list) else m.group(0),
            result
        )

        def replace_dollar_n(m):
            idx = int(m.group(1))
            # Convert from 1-based to 0-based indexing
            return args_list[idx - 1] if 0 < idx <= len(args_list) else m.group(0)

        return _ARG_NUM_PATTERN.sub(replace_dollar_n, result)

    def _resolve_env_substitutions(
        self, body: str, session: 'Session | None', manifest: SkillManifest | None = None
    ) -> str:
        """Replace ${COGNITRIX_*} environment variables."""
        import os

        replacements = {
            '${COGNITRIX_SESSION_ID}': session.id if (session and session.id) else '',
            '${COGNITRIX_USER}': os.getenv('USER', os.getenv('USERNAME', '')),
            '${COGNITRIX_SKILL_DIR}': manifest.source_path or '' if manifest else '',
        }

        result = body
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        return result

    async def _resolve_dynamic_context(self, body: str) -> str:
        """Execute !`command` blocks and replace with command output.

        Commands run in the user's shell. Output is size-limited.
        """
        pattern = r'!\`([^`]+)\`'
        matches = list(re.finditer(pattern, body))

        if not matches:
            return body

        result = body
        for match in matches:
            cmd = match.group(1)
            try:
                output = await self._run_shell_command(cmd)
                # Size limit
                if len(output) > MAX_DYNAMIC_CONTEXT_SIZE:
                    output = output[:MAX_DYNAMIC_CONTEXT_SIZE] + "\n... (truncated)"
                result = result.replace(match.group(0), output)
            except Exception as e:
                logger.warning(f"Dynamic context command failed: {cmd!r} → {e}")
                result = result.replace(match.group(0), f"(command failed: {e})")

        return result

    async def _run_shell_command(self, cmd: str) -> str:
        """Execute a shell command and return stdout."""
        import sys

        cmd_clean = cmd.strip()
        first_word = cmd_clean.split()[0].lower() if cmd_clean else ''

        if first_word not in ALLOWED_COMMANDS:
            raise PermissionError(f"Command '{first_word}' not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}")

        if sys.platform == 'win32':
            proc = await asyncio.create_subprocess_exec(
                'powershell', '-NoProfile', '-Command', cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return "(command timed out after 30s)"

        return stdout.decode('utf-8', errors='replace').strip()

    # ── Safety ──

    async def _request_approval(self, manifest: SkillManifest) -> bool:
        """Request user approval for high-risk skill execution."""
        from cognitrix.safety.approval_gate import ApprovalGate, ToolCall

        gate = ApprovalGate()
        tool_call = ToolCall(
            tool_name=f"skill:{manifest.name}",
            arguments={"description": manifest.description},
        )
        return await gate.check(tool_call)

    async def _ensure_dependencies(self, manifest: SkillManifest):
        """Check and install missing pip dependencies before execution."""
        if manifest.dependencies.pip:
            missing: list[str] = []
            for package in manifest.dependencies.pip:
                # Normalise: pip package names use hyphens, import names use underscores
                import_name = package.replace('-', '_').lower()
                try:
                    __import__(import_name)
                except ImportError:
                    missing.append(package)

            if missing:
                logger.info(f"Installing missing dependencies for skill '{manifest.name}': {missing}")
                import sys
                cmd = [sys.executable, '-m', 'pip', 'install', '--quiet'] + missing
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    error_msg = stderr.decode('utf-8', errors='replace').strip()
                    raise SkillExecutionError(
                        f"Failed to install dependencies {missing}: {error_msg}"
                    )
                logger.info(f"Installed: {missing}")

        if manifest.dependencies.system:
            # Log a warning for system deps — we can't auto-install those
            import shutil
            missing_sys = [
                pkg for pkg in manifest.dependencies.system
                if not shutil.which(pkg)
            ]
            if missing_sys:
                logger.warning(
                    f"Skill '{manifest.name}' requires system packages not found: {missing_sys}. "
                    f"Install them manually."
                )

    # ── Execution Modes ──

    def _resolve_allowed_tools(
        self, allowed_tools: list[str]
    ) -> tuple[set[str], dict[str, list[str]]]:
        """Resolve tool aliases and extract restrictions.

        Parses strings like 'Bash(git *)' into:
        - allowed_set: {'Bash'} (native tool names)
        - restriction_map: {'Bash': ['git', '*']} (glob patterns)

        Returns:
            Tuple of (allowed_tool_names, restriction_map)
        """
        tool_pattern = re.compile(r'^([^\(]+)(?:\((.*)\))?$')

        allowed_set: set[str] = set()
        restriction_map: dict[str, list[str]] = {}

        for tool_spec in allowed_tools:
            match = tool_pattern.match(tool_spec.strip())
            if not match:
                continue

            base = match.group(1).strip().lower()
            restriction = match.group(2) or ''

            resolved = TOOL_ALIASES.get(base, base.title())
            allowed_set.add(resolved)

            if restriction:
                patterns = restriction.split()
                restriction_map.setdefault(resolved, []).extend(patterns)

        return allowed_set, restriction_map

    def _apply_tool_restrictions(
        self,
        tool_calls: list[dict[str, Any]],
        restriction_map: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Validate tool calls against restrictions, blocking violations.

        Args:
            tool_calls: List of tool calls to validate
            restriction_map: {tool_name: [patterns]} for allowed arguments

        Returns:
            Modified tool_calls with violations replaced by error results
        """
        error_msg = "Error: Skill Constraint Violation. Tool execution blocked by allowed-tools restriction"
        result: list[dict[str, Any]] = []

        for tc in tool_calls:
            tool_name = tc.get('name', '')

            if tool_name not in restriction_map:
                result.append(tc)
                continue

            patterns = restriction_map.get(tool_name, [])
            if not patterns:
                result.append(tc)
                continue

            args = tc.get('arguments', {})
            param_name = TOOL_FIRST_ARG.get(tool_name, next(iter(args), ''))
            first_arg = args.get(param_name, '') if args else ''

            if any(fnmatch.fnmatch(first_arg, p) for p in patterns):
                result.append(tc)
            else:
                result.append({
                    'name': tool_name,
                    'arguments': {},
                    'error': error_msg,
                })

        return result

    async def _execute_same_context(
        self,
        prompt: str,
        manifest: SkillManifest,
        session: 'Session | None',
    ) -> AsyncGenerator[str, None]:
        """Inject skill prompt into current agent conversation, stream response."""
        # Prepare messages
        messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': self.agent_manager.formatted_system_prompt()},
            {'role': 'user', 'content': prompt},
        ]

        # Filter tools if allowed-tools is specified
        tools = self.agent_manager.agent.tools
        allowed_set: set[str] = set()
        restriction_map: dict[str, list[str]] = {}
        if manifest.allowed_tools is not None:
            allowed_set, restriction_map = self._resolve_allowed_tools(manifest.allowed_tools)
            tools = [t for t in tools if t.name in allowed_set]

        formatted_tools = [t.to_dict_format() for t in tools]

        # Tool call loop — keep prompting until LLM gives a final text response
        max_iterations = 15  # safety limit
        for _ in range(max_iterations):
            response = await self.llm(messages, tools=formatted_tools, stream=True)

            last_response = None
            async for chunk in self._stream_response(response):
                if isinstance(chunk, str):
                    yield chunk
                last_response = chunk  # keep reference to last LLMResponse

            # Check for tool calls in the final response object
            tool_calls = self._extract_tool_calls(last_response)
            if not tool_calls:
                break  # No tools — we're done

            # Enforce restrictions if any
            if restriction_map:
                tool_calls = self._apply_tool_restrictions(tool_calls, restriction_map)

            # Execute tools and add results to messages
            logger.info(f"Skill '{manifest.name}' executing tools: {[tc.get('name') for tc in tool_calls]}")
            result = await self.agent_manager.call_tools(tool_calls)

            if isinstance(result, dict) and result.get('type') == 'tool_calls_result':
                # Add assistant message with tool_calls (required by OpenAI API format)
                messages.append({
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': self._format_tool_calls_for_message(tool_calls),
                })
                # Add tool result messages
                tool_results = result.get('result', [])
                if isinstance(tool_results, list):
                    for tr in tool_results:
                        if isinstance(tr, dict):
                            messages.append({
                                'role': 'tool',
                                'tool_call_id': tr.get('tool_call_id'),
                                'content': str(tr.get('data', '')),
                            })
                        else:
                            messages.append({
                                'role': 'tool',
                                'tool_call_id': None,
                                'content': str(tr),
                            })
                else:
                    messages.append({
                        'role': 'tool',
                        'tool_call_id': None,
                        'content': str(tool_results),
                    })
            else:
                # Non-dict result (error string, etc.) — inject as user context
                messages.append({'role': 'user', 'content': f"Tool result: {result}"})

    async def _execute_forked(
        self,
        prompt: str,
        manifest: SkillManifest,
        session: 'Session | None',
    ) -> AsyncGenerator[str, None]:
        """Create sub-agent with skill prompt as system prompt, stream response."""
        from cognitrix.agents.base import AgentManager
        from cognitrix.models import Agent

        # Create an ephemeral agent for this skill
        agent = Agent(
            name=f"skill:{manifest.name}",
            system_prompt=(
                f"You are executing the '{manifest.name}' skill.\n\n"
                f"{prompt}"
            ),
            llm=self.llm,
            tools=self.agent_manager.agent.tools,
        )

        # Filter tools if specified
        allowed_set: set[str] = set()
        restriction_map: dict[str, list[str]] = {}
        if manifest.allowed_tools is not None:
            allowed_set, restriction_map = self._resolve_allowed_tools(manifest.allowed_tools)
            agent.tools = [t for t in agent.tools if t.name in allowed_set]

        # Use the specified agent type or default
        if manifest.agent:
            try:
                found = Agent.find_one({'name': manifest.agent})
                if found:
                    agent.system_prompt = found.system_prompt + "\n\n" + prompt
                    agent.tools = found.tools
            except Exception:
                pass

        sub_manager = AgentManager(agent)
        messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': sub_manager.formatted_system_prompt()},
            {'role': 'user', 'content': prompt},
        ]
        formatted_tools = [t.to_dict_format() for t in agent.tools]

        # Tool call loop
        max_iterations = 15
        for _ in range(max_iterations):
            response = await self.llm(messages, tools=formatted_tools, stream=True)

            last_response = None
            async for chunk in self._stream_response(response):
                if isinstance(chunk, str):
                    yield chunk
                last_response = chunk

            tool_calls = self._extract_tool_calls(last_response)
            if not tool_calls:
                break

            # Enforce restrictions if any
            if restriction_map:
                tool_calls = self._apply_tool_restrictions(tool_calls, restriction_map)

            logger.info(f"Skill '{manifest.name}' (forked) executing tools: {[tc.get('name') for tc in tool_calls]}")
            result = await sub_manager.call_tools(tool_calls)

            if isinstance(result, dict) and result.get('type') == 'tool_calls_result':
                messages.append({
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': self._format_tool_calls_for_message(tool_calls),
                })
                tool_results = result.get('result', [])
                if isinstance(tool_results, list):
                    for tr in tool_results:
                        if isinstance(tr, dict):
                            messages.append({
                                'role': 'tool',
                                'tool_call_id': tr.get('tool_call_id'),
                                'content': str(tr.get('data', '')),
                            })
                        else:
                            messages.append({
                                'role': 'tool',
                                'tool_call_id': None,
                                'content': str(tr),
                            })
                else:
                    messages.append({
                        'role': 'tool',
                        'tool_call_id': None,
                        'content': str(tool_results),
                    })
            else:
                messages.append({'role': 'user', 'content': f"Tool result: {result}"})

    async def _stream_response(self, response) -> AsyncGenerator[str | Any, None]:
        """Shared streaming logic for both execution modes.
        
        Yields text chunks (str) for display, and also yields the raw
        LLMResponse object so the caller can inspect tool_calls afterward.
        """
        last_response = None
        if hasattr(response, '__aiter__'):
            async for chunk in response:
                last_response = chunk
                if hasattr(chunk, 'current_chunk'):
                    text = chunk.current_chunk
                elif isinstance(chunk, str):
                    text = chunk
                else:
                    text = str(chunk)
                if text:
                    yield text
        else:
            last_response = response
            # Non-streaming fallback
            if hasattr(response, 'llm_response'):
                yield response.llm_response
            else:
                yield str(response)

        # Always yield the final response object so the caller can check tool_calls
        if last_response is not None:
            yield last_response

    def _extract_tool_calls(self, response_obj: Any) -> list[dict[str, Any]]:
        """Extract tool_calls from a response object (LLMResponse or similar)."""
        if response_obj is None:
            return []
        tool_calls = getattr(response_obj, 'tool_calls', None)
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            return tool_calls
        return []

    @staticmethod
    def _format_tool_calls_for_message(
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format tool_calls for inclusion in an assistant message (OpenAI API format)."""
        import json as _json
        formatted = []
        for tc in tool_calls:
            formatted.append({
                'id': tc.get('tool_call_id') or 'call_0',
                'type': 'function',
                'function': {
                    'name': tc.get('name', ''),
                    'arguments': _json.dumps(tc.get('arguments', {})),
                },
            })
        return formatted

