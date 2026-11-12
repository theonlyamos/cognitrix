"""Per-agent and per-tool permission levels.

A person can set, on each agent:

- an agent-wide level: ``auto`` (the default risk policy, see
  ``destructive_ops``), ``ask`` (ask before every action) or ``allow``
  (run every action without asking);
- a level per tool, which wins over the agent-wide one: ``allow`` (always
  allow), ``ask`` (always ask first), ``told`` (run in a conversation the
  person is in, ask first when the agent works on its own), ``handoff``
  (never run: the prepared action goes to the person's Inbox to do
  themselves), ``deny`` (never run) or ``auto`` (follow the agent-wide
  level).

``decide_tool_call`` turns those settings, the tool's own ``approval_mode``
and the risk assessment into one decision for a call: run it, ask a person,
or block it. The agent loop acts on that decision; ``ApprovalGate`` (and any
reviewer plugged into it) only ever sees calls whose action is ``ask``.

Two things no setting changes:

- the full shell (a Bash command that isn't on the allowlist) still needs a
  person to approve that exact call, so "always allow" asks for it;
- ``COGNITRIX_AUTO_APPROVE`` and bypass mode still apply inside the gate.

A run can also be read-only (``read_only_run``): the check-ins of a standing
assistant look at things but may not change anything, so every call that
would otherwise ask (or that only an "always allow" setting lets through) is
blocked instead, whatever the agent's levels say.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

AgentLevel = Literal['auto', 'ask', 'allow']
ToolLevel = Literal['auto', 'allow', 'ask', 'told', 'handoff', 'deny']

AGENT_LEVELS: tuple[str, ...] = ('auto', 'ask', 'allow')
TOOL_LEVELS: tuple[str, ...] = ('auto', 'allow', 'ask', 'told', 'handoff', 'deny')
STORED_TOOL_LEVELS: tuple[str, ...] = tuple(level for level in TOOL_LEVELS if level != 'auto')

# Interfaces where nobody is in the conversation (same set the reviewer uses).
UNATTENDED_INTERFACES = frozenset({'task', 'auto'})

# True for a turn nobody started by hand on an attended interface, e.g. a
# chat one agent started with another (sessions/agent_chats.py).
unattended_turn: ContextVar[bool] = ContextVar('permissions_unattended_turn', default=False)

# Why the current run may only read, or '' when it may act (see module doc).
read_only_run: ContextVar[str] = ContextVar('permissions_read_only_run', default='')

# True while the agent answers someone other than its person (a question from
# a shared chat channel). On top of read-only, only GUEST_TOOLS run: nothing
# that reaches the person's connected apps, memory, chats, tasks or screen.
guest_run: ContextVar[bool] = ContextVar('permissions_guest_run', default=False)


def is_unattended(interface: str | None) -> bool:
    return str(interface or '') in UNATTENDED_INTERFACES or unattended_turn.get()

# Added to the risk's categories when a person's "ask" setting is why the
# call asks, so a reviewer in the gate knows a person wants to answer it.
PERMISSION_ASK_CATEGORY = 'permission_ask'

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def tool_key(name: Any) -> str:
    """How tool names are matched: case, spaces, dashes and underscores don't matter."""
    return re.sub(r'[\s\-_]+', '_', str(name or '').strip()).lower()


def normalize_agent_level(value: Any) -> str:
    level = str(value or 'auto').strip().lower()
    if level not in AGENT_LEVELS:
        raise ValueError(f"Unknown agent permission level '{value}'. Use one of: {', '.join(AGENT_LEVELS)}")
    return level


def normalize_tool_permissions(value: Any) -> dict[str, str]:
    """Clean a ``{tool name: level}`` map. ``auto`` entries are dropped."""
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ValueError('tool_permissions must be an object of tool name to level')
    cleaned: dict[str, str] = {}
    for name, level in value.items():
        name = str(name or '').strip()
        if not name:
            continue
        level = str(level or 'auto').strip().lower()
        if level not in TOOL_LEVELS:
            raise ValueError(
                f"Unknown permission level '{level}' for tool '{name}'. Use one of: {', '.join(TOOL_LEVELS)}"
            )
        if level != 'auto':
            cleaned[name] = level
    return cleaned


def stored_tool_permissions(agent: Any) -> dict[str, str]:
    """The agent's per-tool levels, skipping anything malformed. Never raises."""
    perms = getattr(agent, 'tool_permissions', None) or {}
    if not isinstance(perms, dict):
        return {}
    return {
        str(name): str(level).lower()
        for name, level in perms.items()
        if str(name or '').strip() and str(level or '').lower() in STORED_TOOL_LEVELS
    }


def tool_level(agent: Any, tool_name: str) -> str:
    """The level set for this tool on the agent, else ``auto``."""
    key = tool_key(tool_name)
    for name, level in stored_tool_permissions(agent).items():
        if tool_key(name) == key:
            return level
    return 'auto'


def agent_level(agent: Any) -> str:
    level = str(getattr(agent, 'permission_level', None) or 'auto').lower()
    return level if level in AGENT_LEVELS else 'auto'


def is_denied(agent: Any, tool_name: str) -> bool:
    return tool_level(agent, tool_name) == 'deny'


# Tools an agent setting can switch off, and why.
_SWITCHES = {
    'create_agent': ('can_create_sub_agents', None, "This agent isn't allowed to create sub-agents"),
    'schedule_next_check': ('automation', 'self_checkins', "This agent isn't allowed to schedule its own check-ins"),
}


def switched_off(agent: Any, tool_name: str) -> str | None:
    """Why an agent setting turns this tool off, or None."""
    switch = _SWITCHES.get(tool_key(tool_name))
    if switch is None:
        return None
    field, key, reason = switch
    value = getattr(agent, field, None)
    if key is not None:
        value = value.get(key) if isinstance(value, dict) else None
    return reason if value is False else None


def visible_tools(agent: Any, tools: list) -> list:
    """Drop tools set to "never" (or switched off) so the model isn't offered them."""
    return [
        t for t in tools
        if not is_denied(agent, getattr(t, 'name', '')) and not switched_off(agent, getattr(t, 'name', ''))
    ]


@dataclass
class ToolDecision:
    """What to do with one tool call."""

    action: Literal['run', 'ask', 'block']
    risk: RiskAssessment
    # Which setting decided: 'tool' (per-tool level), 'agent' (agent-wide
    # level) or 'policy' (the default risk policy and the tool's approval_mode).
    source: str = 'policy'
    reason: str = ''
    # True when the block means "the person does this one themselves": the
    # agent loop then leaves the prepared action in their Inbox.
    handoff: bool = False


def needs_person(tools: tuple, args: dict) -> bool:
    """True when the tool itself only runs this call after a person approves it.

    Today that's Bash outside the allowlist (the full shell). ``tools`` is the
    assigned tool and the registered one that actually runs: an agent saved
    before Bash had ``internal_params`` only shows it on the registered one.
    """
    if not any('approved' in (getattr(t, 'internal_params', None) or []) for t in tools if t is not None):
        return False
    command = args.get('command') if isinstance(args, dict) else None
    if not isinstance(command, str) or not command.strip():
        return False
    try:
        from pathlib import Path

        from cognitrix.common import shell as sh
        from cognitrix.config import settings

        if sh.sandbox_shell_enabled() or sh.shell_mode() == 'allowlist':
            return False  # runs without a person, or is refused without running
        plan = sh.plan_call(
            command, Path(settings.tools_root).expanduser().resolve(), args.get('run_in_background'),
        )
        return plan.kind == 'shell'
    except Exception:
        return True


_needs_person = needs_person


def _asking(risk: RiskAssessment, details: str) -> RiskAssessment:
    """The risk shown on the approval card when a setting makes a call ask."""
    level = risk.risk_level if _RISK_ORDER[risk.risk_level] >= 1 else RiskLevel.MEDIUM
    categories = [*risk.categories]
    if PERMISSION_ASK_CATEGORY not in categories:
        categories.append(PERMISSION_ASK_CATEGORY)
    shown = details if risk.risk_level == RiskLevel.LOW else f"{details}. {risk.details}"
    return RiskAssessment(level, categories, shown, risk.confidence)


def _policy_action(tool: Any, risk: RiskAssessment) -> str:
    approval_mode = getattr(tool, 'approval_mode', 'risk_based') or 'risk_based'
    if approval_mode == 'always' or (
        approval_mode == 'risk_based' and risk.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH)
    ):
        return 'ask'
    return 'run'


# What a read-only run (a standing assistant's check-in) may call. Asking
# first is not the same as changing something: Edit, a new file from Write
# and undoable changes in connected apps all run without asking, so the
# check-in boundary is this list and not the approval policy.
READ_ONLY_TOOLS = frozenset({
    'read', 'grep', 'glob', 'search', 'tavily_search', 'webfetch', 'web_fetch',
    'search_knowledge', 'search_chats', 'read_chat', 'check_chat',
    'list_agents', 'list_tasks', 'get_task', 'list_mcp_tools', 'list_credentials',
    'load_skill', 'bash_output', 'list_windows', 'look_at_window', 'take_screenshot',
    # the check-in's own notes, alerts and offers
    'read_notes', 'update_notes', 'schedule_next_check', 'notify_me', 'offer_help',
})


GUEST_TOOLS = frozenset({
    'read', 'grep', 'glob', 'search', 'tavily_search', 'webfetch', 'web_fetch',
    'search_knowledge', 'load_skill',
})


def _only_looks(tool: Any, args: dict, mcp_reads: bool | None) -> bool:
    name = str(getattr(tool, 'name', '') or '').strip().lower().replace(' ', '_')
    args = args if isinstance(args, dict) else {}
    if guest_run.get():
        return name in GUEST_TOOLS
    if name in READ_ONLY_TOOLS:
        return True
    if str(getattr(tool, 'category', '') or '') in ('mcp', 'mcp_dynamic') and name != 'list_mcp_tools':
        return bool(mcp_reads)
    if name == 'http_request':
        return str(args.get('method') or 'GET').strip().upper() in ('GET', 'HEAD')
    if name == 'bash':
        command = args.get('command')
        if not isinstance(command, str) or not command.strip():
            return False
        try:
            from pathlib import Path

            from cognitrix.common import shell as sh
            from cognitrix.config import settings

            plan = sh.plan_call(
                command, Path(settings.tools_root).expanduser().resolve(), args.get('run_in_background'),
            )
            return plan.kind == 'allowlisted' and bool(plan.read_only)
        except Exception:
            return False
    return False


def read_only_block(
    decision: ToolDecision, tool: Any, args: dict, *, runtime_tool: Any = None,
    mcp_reads: bool | None = None,
) -> ToolDecision:
    """In a read-only run, refuse everything that is not known to only look.

    ``mcp_reads`` says whether an action on a connected app only reads
    (``destructive_ops.mcp_only_reads``); unknown counts as a change.
    """
    why = read_only_run.get()
    if not why or decision.action == 'block':
        return decision
    changes = (
        not _only_looks(tool, args, mcp_reads)
        or decision.action == 'ask'
        or _policy_action(tool, decision.risk) == 'ask'
        or needs_person((tool, runtime_tool), args)
    )
    if not changes:
        return decision
    return ToolDecision('block', decision.risk, 'read_only', why)


def decide_tool_call(
    agent: Any, tool: Any, args: dict, risk: RiskAssessment, *, runtime_tool: Any = None,
    interface: str | None = None,
) -> ToolDecision:
    """Decide whether a call runs, asks a person first, or is blocked.

    ``runtime_tool`` is the registered tool that will run, when it differs
    from the agent's assigned copy.
    """
    name = str(getattr(tool, 'name', '') or '')
    off = switched_off(agent, name)
    if off:
        return ToolDecision('block', risk, 'agent', off)
    level = tool_level(agent, name)
    source = 'tool'
    if level == 'auto':
        level = agent_level(agent)
        source = 'agent'

    if level == 'deny':
        return ToolDecision('block', risk, source, f"'{name}' is set to never run for this agent")

    if level == 'handoff':
        return ToolDecision(
            'block', risk, source,
            f"the person does '{name}' themselves. What you prepared was left in their Inbox; "
            'tell them it is waiting there', handoff=True)

    if level == 'told':
        # In a conversation the person is in, their request is the go-ahead.
        level = 'ask' if is_unattended(interface) else 'allow'
        if level == 'ask':
            return ToolDecision(
                'ask', _asking(risk, 'You asked to approve this tool first when the agent works on its own'),
                source)

    if level == 'allow':
        if needs_person((tool, runtime_tool), args):
            return ToolDecision(
                'ask', risk, source,
                'The full shell needs a person to approve each command, even when the tool is always allowed',
            )
        return ToolDecision('run', risk, source, f"'{name}' is always allowed for this agent")

    approval_mode = getattr(tool, 'approval_mode', 'risk_based') or 'risk_based'

    if level == 'ask':
        # The agent-wide "ask" leaves tools that never ask (questions to the
        # person, notes, alerts) alone; a per-tool "ask" is explicit, so it asks.
        if source == 'agent' and approval_mode == 'assigned_only':
            return ToolDecision('run', risk, 'policy')
        where = 'this tool' if source == 'tool' else 'every action of this agent'
        return ToolDecision(
            'ask', _asking(risk, f"You asked to approve {where} first"), source,
        )

    # auto: the default policy.
    return ToolDecision(_policy_action(tool, risk), risk, 'policy')


def apply_target_level(
    decision: ToolDecision, level: str, name: str, risk: RiskAssessment, *, interface: str | None = None,
) -> ToolDecision:
    """Apply the person's setting for the app tool that a Run Mcp Tool call runs.

    Agents reach connected apps through one tool (Run Mcp Tool), so the
    per-tool levels set on the agent's Connections tab are keyed by the app
    tool's own name and applied here, on top of ``decide_tool_call``.
    """
    if level == 'deny':
        return ToolDecision('block', risk, 'tool', f"'{name}' is set to never run for this agent")
    if level == 'handoff':
        return ToolDecision(
            'block', risk, 'tool',
            f"the person does '{name}' themselves. What you prepared was left in their Inbox; "
            'tell them it is waiting there', handoff=True)
    if level == 'told':
        level = 'ask' if is_unattended(interface) else 'allow'
    if level == 'allow':
        return ToolDecision('run', risk, 'tool', f"'{name}' is always allowed for this agent")
    if level == 'ask':
        return ToolDecision('ask', _asking(risk, 'You asked to approve this tool first'), 'tool')
    return decision


def default_behavior(tool: Any) -> str:
    """What the default policy does with a tool, for the agent form.

    ``asks``: asks before every call. ``runs``: runs without asking.
    ``depends``: depends on the call (e.g. overwriting a file, a shell command).
    """
    from cognitrix.safety.destructive_ops import (
        _SHELL_TOOLS,
        _WRITE_TOOLS,
        DestructiveOpDetector,
        _normalise,
    )

    mode = getattr(tool, 'approval_mode', 'risk_based') or 'risk_based'
    if mode == 'always':
        return 'asks'
    if mode == 'assigned_only':
        return 'runs'
    name = str(getattr(tool, 'name', '') or '')
    key = _normalise(name)
    if key in _SHELL_TOOLS or key in _WRITE_TOOLS or getattr(tool, 'category', '') == 'mcp':
        return 'depends'
    risk = DestructiveOpDetector().analyze(name, {})
    return 'runs' if risk.risk_level == RiskLevel.LOW else 'asks'
