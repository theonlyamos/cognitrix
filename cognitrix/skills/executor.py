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
    ) -> AsyncGenerator[SkillEvent, None]:
        """Execute a skill, streaming events.

        Args:
            manifest:   Parsed skill manifest
            arguments:  Arguments string (e.g. "src/auth/login.ts")
            session:    Optional session for history tracking

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
            # 1. Resolve argument substitutions
            rendered = self._resolve_arguments(manifest.body, arguments)
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

            # 5. Safety check
            if manifest.safety.requires_approval or manifest.safety.risk_level == RiskLevel.HIGH:
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

    def _resolve_arguments(self, body: str, arguments: str) -> str:
        """Replace $ARGUMENTS, $ARGUMENTS[N], and $N with actual values."""
        arguments = arguments or ""
        args_list = arguments.split() if arguments else []

        result = body.replace("$ARGUMENTS", arguments)

        result = _ARGUMENTS_BRACKET_PATTERN.sub(
            lambda m: args_list[int(m.group(1))] if int(m.group(1)) < len(args_list) else m.group(0),
            result
        )

        def replace_dollar_n(m):
            idx = int(m.group(1))
            return args_list[idx] if idx < len(args_list) else m.group(0)

        return _ARG_NUM_PATTERN.sub(replace_dollar_n, result)

    def _resolve_env_substitutions(
        self, body: str, session: 'Session | None', manifest: SkillManifest | None = None
    ) -> str:
        """Replace ${COGNITRIX_*} environment variables."""
        import os

        replacements = {
            '${COGNITRIX_SESSION_ID}': session.id if session.id else '',
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

    async def _execute_same_context(
        self,
        prompt: str,
        manifest: SkillManifest,
        session: 'Session | None',
    ) -> AsyncGenerator[str, None]:
        """Inject skill prompt into current agent conversation, stream response."""
        # Prepare messages
        messages = [
            {'role': 'system', 'content': self.agent_manager.formatted_system_prompt()},
            {'role': 'user', 'content': prompt},
        ]

        # Filter tools if allowed-tools is specified
        tools = self.agent_manager.agent.tools
        if manifest.allowed_tools is not None:
            allowed_lower = frozenset(t.lower() for t in manifest.allowed_tools)
            tools = [t for t in tools if t.name.lower() in allowed_lower]

        # Stream the LLM response
        response = await self.llm(messages, tools=tools, stream=True)

        if hasattr(response, '__aiter__'):
            async for chunk in response:
                if hasattr(chunk, 'current_chunk'):
                    text = chunk.current_chunk
                elif isinstance(chunk, str):
                    text = chunk
                else:
                    text = str(chunk)
                if text:
                    yield text
        else:
            # Non-streaming fallback
            if hasattr(response, 'llm_response'):
                yield response.llm_response
            else:
                yield str(response)

    async def _execute_forked(
        self,
        prompt: str,
        manifest: SkillManifest,
        session: 'Session | None',
    ) -> AsyncGenerator[str, None]:
        """Create sub-agent with skill prompt as system prompt, stream response."""
        from cognitrix.agents.base import AgentManager
        from cognitrix.models import Agent
        from cognitrix.providers.base import LLM

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
        if manifest.allowed_tools is not None:
            allowed_lower = frozenset(t.lower() for t in manifest.allowed_tools)
            agent.tools = [t for t in agent.tools if t.name.lower() in allowed_lower]

        # Use the specified agent type or default
        if manifest.agent:
            # Try to find the named agent
            try:
                found = Agent.find_one({'name': manifest.agent})
                if found:
                    agent.system_prompt = found.system_prompt + "\n\n" + prompt
                    agent.tools = found.tools
            except Exception:
                pass

        sub_manager = AgentManager(agent)
        messages = [
            {'role': 'system', 'content': sub_manager.formatted_system_prompt()},
            {'role': 'user', 'content': prompt},
        ]

        response = await self.llm(messages, tools=agent.tools, stream=True)

        if hasattr(response, '__aiter__'):
            async for chunk in response:
                if hasattr(chunk, 'current_chunk'):
                    text = chunk.current_chunk
                elif isinstance(chunk, str):
                    text = chunk
                else:
                    text = str(chunk)
                if text:
                    yield text
        else:
            if hasattr(response, 'llm_response'):
                yield response.llm_response
            else:
                yield str(response)
